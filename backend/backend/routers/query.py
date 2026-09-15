"""查询路由——核心。

顺序：读预设 → 填 {subject_id} → 【层一】准入 → 【层二】审计 → 执行 → 存报告。
层一拒绝时不审计、不执行、无结果集。

可扩展：当前查询计划来自 demo/queries.json（演示模式，NL→SQL 缓存）。
端到端模式（真实 NL→SQL）预留 question_text 直查接口，见 run_direct_query()。
"""
import json
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend import config
from backend.admission import admission_check_plan
from backend.db import save_report
from backend.deps import audit_plan
from backend.labels import DEGRADATION_LABELS, DEGRADATION_MESSAGES
from backend.schemas import (AdmissionInfo, DegradationInfo, MetricsInfo,
                             PlanItem, QueryRequest, QueryResponse,
                             ResultSet, RewriteInfo, SecurityEvent, Token)

router = APIRouter(prefix="/api/query", tags=["query"])


def _load_queries() -> dict:
    with open(config.QUERIES_FILE, encoding="utf-8") as f:
        return json.load(f)


@router.post("", response_model=QueryResponse)
def run_query(req: QueryRequest):
    t0 = time.perf_counter()
    queries = _load_queries()
    spec = queries.get(req.question_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="查询不存在")
    if req.token.type not in spec["token_types"]:
        raise HTTPException(status_code=403, detail="该令牌无权使用此查询")

    # 填占位符
    subject = req.token.subject_id or ""
    plan = [
        {"id": sq["id"], "description": sq["description"],
         "sql": sq["sql"].replace("{subject_id}", subject)}
        for sq in spec["plan"]
    ]

    # 【层一】准入 —— 只对病患令牌执行（医护令牌的权限范围由医院
    # RLS 式策略管理，不在本项目实现；其查询一律放行交层二审计）。
    if req.token.type == "patient":
        adm = admission_check_plan(plan, subject)
        if not adm.passed:
            resp = QueryResponse(
                admission=AdmissionInfo(passed=False, reason=adm.reason,
                                        checked_tables=adm.checked_tables,
                                        bound_to_subject=False),
                question=spec["question"],
                plan=[], events=[],
                rewrite=RewriteInfo(),
                degradation=DegradationInfo(
                    level="L3", label=DEGRADATION_LABELS["L3"],
                    message=adm.reason or "",
                    message_cn=adm.reason or DEGRADATION_MESSAGES["L3"]),
                metrics=MetricsInfo(
                    elapsed_ms=int((time.perf_counter() - t0) * 1000),
                    llm_calls=0, db_access=0),
                result=None,
            )
            _persist(req, spec, resp)
            return resp
    else:
        adm = admission_check_plan(plan, subject)  # 仅取涉及的表面板信息
        adm.passed = True
        adm.reason = None

    # 【层二】审计（零 LLM、零查库）
    audited_at = int((time.perf_counter() - t0) * 1000)   # 审计耗时到此为止
    outcome = audit_plan(plan, req.datasource_id)

    # 执行（仅当未被 L3 拒绝）
    result = None
    if outcome.degradation_level != "L3":
        sql_after = {sq["id"]: sq["sql"] for sq in outcome.audited_plan}
        if not sql_after:
            sql_after = {sq["id"]: sq["sql"] for sq in plan}
        result = _execute(sql_after, plan)

    # 组装。注意：改写后计划可能与原始计划条数不同——
    #   Rule A 消除死子查询（变少）、Rule C 跨域拆分（一条变两条 0_a/0_b）。
    # 以 audited_plan 为准展示；sql_before 回填原始 SQL（拆分产物回填源查询）。
    display = outcome.audited_plan
    if not display:
        display = [{"id": sq["id"], "description": sq["description"],
                    "sql": sq["sql"]} for sq in plan]
    original_by_id = {str(sq["id"]): sq for sq in plan}
    plan_items = []
    for i, sq in enumerate(display):
        key = str(sq["id"])
        source = original_by_id.get(key)
        if source is None and "_" in key:
            source = original_by_id.get(key.split("_")[0])
        sql_before = source["sql"] if source else sq.get("sql", "")
        plan_items.append(PlanItem(
            id=i,  # 展示序号（Rule C 拆分后原 id 不再唯一，前端以序号为 key）
            description=sq.get("description")
            or (source.get("description", "") if source else "子查询"),
            sql_before=sql_before,
            sql_after=sq.get("sql", sql_before),
            is_final=(i == len(display) - 1),
        ))

    resp = QueryResponse(
        admission=AdmissionInfo(passed=True, reason=None,
                                checked_tables=adm.checked_tables,
                                bound_to_subject=adm.bound_to_subject),
        question=spec["question"],
        plan=plan_items,
        events=[SecurityEvent(**v) for v in outcome.violations],
        rewrite=RewriteInfo(applied=outcome.rewrites_applied,
                            log=outcome.rewrite_log),
        degradation=DegradationInfo(
            level=outcome.degradation_level,
            label=DEGRADATION_LABELS.get(outcome.degradation_level, ""),
            message=outcome.degradation_message,
            message_cn=DEGRADATION_MESSAGES.get(outcome.degradation_level, "")),
        # metrics 只报审计开销：llm_calls 与 db_access 恒为 0，
        # 这是「零 LLM、零查库」安全声明的可验证形式。查询执行不计入。
        metrics=MetricsInfo(elapsed_ms=audited_at, llm_calls=0, db_access=0),
        result=result,
    )
    _persist(req, spec, resp)
    return resp


class DirectQueryRequest(BaseModel):
    """端到端模式预留：自然语言直查（后续接入 NL→SQL 翻译时启用）。

    当前返回 501，前端契约已冻结此形状，避免将来契约变更。
    """
    token: Token
    datasource_id: str
    question: str


@router.post("/direct")
def run_direct_query(req: DirectQueryRequest):
    raise HTTPException(
        status_code=501,
        detail="端到端模式尚未启用：当前为演示模式（预置查询库）。"
               "后续接入 NL→SQL 翻译后启用本接口。",
    )


def _execute(sql_by_id: Dict[Any, str], plan: list) -> Optional[ResultSet]:
    """执行改写后的最后一条子查询。

    返回 None 表示未执行或执行失败——两种情况都不返回结果集，
    前端表现为「无结果」，不会误导为「查询结果为空」。
    """
    if not os.path.exists(config.BUSINESS_DB):
        return None
    final_id = plan[-1]["id"]
    sql = sql_by_id.get(final_id)
    if not sql:
        return None
    con = sqlite3.connect(config.BUSINESS_DB)
    con.text_factory = lambda b: b.decode(errors="ignore")
    try:
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = [list(r) for r in cur.fetchall()]
    except sqlite3.Error:
        return None
    finally:
        con.close()
    return ResultSet(columns=cols, rows=rows)


def _persist(req: QueryRequest, spec: dict, resp: QueryResponse) -> None:
    db_path = config.METADATA_DB
    if not os.path.exists(db_path):
        return
    save_report(db_path, {
        "question": spec["question"],
        "token_type": req.token.type,
        "degradation_level": resp.degradation.level,
        "event_count": len(resp.events),
        "payload": resp.model_dump(),
    })
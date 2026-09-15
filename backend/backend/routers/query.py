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

from backend import config, llm_nl2sql, query_cache
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

    # 占位符替换在 _pipeline 里做（两条入口共用）
    plan = [
        {"id": sq["id"], "description": sq["description"], "sql": sq["sql"]}
        for sq in spec["plan"]
    ]

    resp = _pipeline(plan, spec["question"], req.token, req.datasource_id, t0)
    _persist(spec["question"], req.token.type, resp)
    return resp


def _pipeline(plan: list, question: str, token: Token,
              datasource_id: str, t0: float) -> QueryResponse:
    """层一准入 → 层二审计 → 执行 → 组装响应。

    预置查询与自由提问共用这一条管线——**它们只在「计划从哪来」上不同**，
    审计与拦截完全一致。这是自由提问不削弱安全演示的原因。
    """
    subject = token.subject_id or ""

    # 占位符替换放在这里，两条入口共用。
    # 自由提问的计划也可能带 {subject_id}——它的内容来自预置库（自由问法与
    # 常用问题是同一句话，两边应当拿到同一份计划）。不在这里替换，就会把
    # 占位符原样送进 SQL。
    plan = [
        {**sq, "sql": (sq.get("sql") or "").replace("{subject_id}", subject)}
        for sq in plan
    ]

    # 【层一】准入 —— 只对病患令牌执行（医护令牌的权限范围由医院
    # RLS 式策略管理，不在本项目实现；其查询一律放行交层二审计）。
    adm = admission_check_plan(plan, subject)
    if token.type == "patient" and not adm.passed:
        resp = QueryResponse(
            admission=AdmissionInfo(passed=False, reason=adm.reason,
                                    checked_tables=adm.checked_tables,
                                    bound_to_subject=False),
            question=question,
            plan=[], events=[],
            rewrite=RewriteInfo(applied=0, log=[]),
            degradation=DegradationInfo(
                level="L3", label=DEGRADATION_LABELS["L3"],
                message=adm.reason or "",
                message_cn=adm.reason or DEGRADATION_MESSAGES["L3"]),
            metrics=MetricsInfo(
                elapsed_ms=int((time.perf_counter() - t0) * 1000),
                llm_calls=0, db_access=0),
            result=None,
        )
        return resp
    if token.type != "patient":
        adm.passed = True
        adm.reason = None

    # 【层二】审计（零 LLM、零查库）
    audited_at = int((time.perf_counter() - t0) * 1000)   # 审计耗时到此为止
    outcome = audit_plan(plan, datasource_id)

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
    # L3 拒绝时不展示计划。算法层对 L3 一律返回空 audited_plan（FINAL_
    # IMPLEMENTATION_NOTES：Every L3 result returns an empty audited_plan,
    # so rejected SQL cannot be accidentally executed）。回落填回原始计划
    # 会把这条保证作废：被拒的语句又重新出现在响应里，而且那计划本身
    # 可能就解析不了（实测有过只有一行注释的），展示出来只是乱码。
    display = outcome.audited_plan
    if not display and outcome.degradation_level != "L3":
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

    return QueryResponse(
        admission=AdmissionInfo(passed=True, reason=None,
                                checked_tables=adm.checked_tables,
                                bound_to_subject=adm.bound_to_subject),
        question=question,
        plan=plan_items,
        events=[SecurityEvent(**v) for v in outcome.violations],
        rewrite=RewriteInfo(applied=outcome.rewrites_applied,
                            log=outcome.rewrite_log),
        degradation=DegradationInfo(
            level=outcome.degradation_level,
            # 算法层给了更准确的标签就用它（如解析失败的「无法处理」），
            # 否则按等级取默认。两者语义不同，不可混用。
            label=(outcome.degradation_label
                   or DEGRADATION_LABELS.get(outcome.degradation_level, "")),
            message=outcome.degradation_message,
            # 算法层给了更准确的中文就用它（如解析失败），否则按等级取默认
            message_cn=(outcome.degradation_message_cn
                        or DEGRADATION_MESSAGES.get(outcome.degradation_level, ""))),
        # metrics 只报**审计**开销：llm_calls 与 db_access 恒为 0，这是
        # 「零 LLM、零查库」安全声明的可验证形式。翻译环节若调了模型不算
        # 在这里——指标条文案写的是「审计阶段零模型调用」，范围已限定。
        metrics=MetricsInfo(elapsed_ms=audited_at, llm_calls=0, db_access=0),
        result=result,
    )


class DirectQueryRequest(BaseModel):
    """自然语言直查。"""
    token: Token
    datasource_id: str
    question: str


@router.post("/direct", response_model=QueryResponse)
def run_direct_query(req: DirectQueryRequest):
    """自由提问：**缓存优先，未命中调模型翻译**。

    缓存的是**计划**不是答案——命中后照样走层一准入与层二审计，安全演示
    一点不打折。缓存的价值不只是提速（模型翻译约 47 秒），更是保演示质量：
    模型倾向选更安全的写法，审计出来的违规常为 0；打磨过的计划才会触发
    Rule C 拆分与跨域 JOIN 事件。
    """
    t0 = time.perf_counter()
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=422, detail="问题不能为空")

    plan = query_cache.lookup(question, req.token.type)

    if plan is None:
        if not llm_nl2sql.llm_available():
            raise HTTPException(
                status_code=503,
                detail="该问法暂未收录，且未配置模型（缺少 OPENAI_API_KEY），"
                       "无法翻译新问法。请换一个已收录的问法。",
            )
        try:
            plan = llm_nl2sql.decompose(
                question, req.datasource_id,
                token_type=req.token.type,
                subject_id=req.token.subject_id or "",
            )
        except llm_nl2sql.NL2SQLError as exc:
            raise HTTPException(status_code=502,
                                detail=f"翻译失败：{exc}") from exc
        # 只写回成功的翻译；失败不该污染缓存
        query_cache.store(question, plan, req.token.type, source="llm")

    resp = _pipeline(plan, question, req.token, req.datasource_id, t0)
    _persist(question, req.token.type, resp)
    return resp


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


def _persist(question: str, token_type: str, resp: QueryResponse) -> None:
    db_path = config.METADATA_DB
    if not os.path.exists(db_path):
        return
    save_report(db_path, {
        "question": question,
        "token_type": token_type,
        "degradation_level": resp.degradation.level,
        "event_count": len(resp.events),
        "payload": resp.model_dump(),
    })
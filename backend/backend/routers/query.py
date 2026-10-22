"""查询路由——核心。

顺序：读预设 → 填 {subject_id} → 【层一】准入 → 【层二】审计 → 执行 → 存报告。
层一拒绝时不审计、不执行、无结果集。

可扩展：当前查询计划来自 demo/queries.json（演示模式，NL→SQL 缓存）。
端到端模式（真实 NL→SQL）预留 question_text 直查接口，见 run_direct_query()。
"""
import hashlib
import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend import config, llm_nl2sql, query_cache
from backend.admission import admission_check_plan
from backend.db import save_report
from backend.deps import audit_plan
from backend.labels import DEGRADATION_LABELS, DEGRADATION_MESSAGES
from backend.schemas import (AdmissionInfo, AuditDecision, DegradationInfo,
                             MetricsInfo, PlanItem, QueryRequest, QueryResponse,
                             ResultSet, RewriteInfo, SecurityEvent, Token)
from backend.security import verified

router = APIRouter(prefix="/api/query", tags=["query"])


def _load_queries() -> dict:
    with open(config.QUERIES_FILE, encoding="utf-8") as f:
        return json.load(f)


def _normalized_sql(sql: str) -> str:
    """SQL 规范化：解析再序列化，格式差异（空格/大小写/引号）不破坏哈希。"""
    try:
        import sqlglot
        return sqlglot.parse_one(sql, read="sqlite").sql(dialect="sqlite")
    except Exception:
        return " ".join((sql or "").split())


def _policy_or_none(datasource_id: str):
    """取数据源策略；不存在/损坏返回 None（就绪门禁用）。

    演示库走兼容模式，**不需要**加载策略对象（allowed 直接放行）。
    """
    if datasource_id == config.DEMO_DATASOURCE_ID:
        return None
    from backend.deps import list_policy_ids, load_policy
    if datasource_id in list_policy_ids():
        try:
            return load_policy(datasource_id)
        except Exception:  # noqa: BLE001 - 策略损坏按未就绪处理
            return None
    return None


def _sql_sha256(sql: str) -> str:
    """规范化的 SQL SHA-256（十六进制）。"""
    return hashlib.sha256(_normalized_sql(sql).encode("utf-8")).hexdigest()


@router.post("", response_model=QueryResponse)
def run_query(req: QueryRequest):
    verified(req.token)
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

    resp, decision = _pipeline(plan, spec["question"], req.token, req.datasource_id, t0)
    _persist(spec["question"], req.token, resp, decision)
    return resp


def _pipeline(plan: list, question: str, token: Token,
              datasource_id: str, t0: float) -> Tuple[QueryResponse, Optional[AuditDecision]]:
    """层一准入 → 层二审计 → 执行 → 组装响应。

    预置查询与自由提问共用这一条管线——**它们只在「计划从哪来」上不同**，
    审计与拦截完全一致。这是自由提问不削弱安全演示的原因。

    返回 (响应, 审计决策绑定证据)。决策证据用领域类型 AuditDecision
    携带（评审 P2：裸字典会漏字段）；随报告落盘见 _persist。
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

    # 数据源就绪门禁（评审 B6）：演示库恒为兼容模式；严格模式下非演示
    # 数据源若存在未标注表/列（schema 与 SSA 有差距），在查询前直接拒绝
    # ——不等到审计层「临时拦」。这是管理门禁，与层一/层二正交。
    from backend import datasource_registry as dsr
    from backend import readiness
    spec = dsr.get(datasource_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="数据源未注册")
    db_path = dsr.resolve_db_path(spec)
    if not readiness.allowed(datasource_id, _policy_or_none(datasource_id),
                             db_path=db_path):
        raise HTTPException(
            status_code=503,
            detail="该数据源未就绪：存在未标注的表或列，"
                   "请管理员完成安全策略标注后再启用。",
        )

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
        # 层一拒绝：没有审计、没有执行，无从绑定——存 None（NULL）而不是
        # 空对象：空对象会让 ReportDetail 的 decision 校验失败（评审 P1）。
        return resp, None
    if token.type != "patient":
        adm.passed = True
        adm.reason = None

    # 【层二】审计（零 LLM、零查库）
    audited_at = int((time.perf_counter() - t0) * 1000)   # 审计耗时到此为止
    decision_id = uuid.uuid4().hex
    outcome = audit_plan(plan, datasource_id)
    audited_plan = outcome.audited_plan
    # 审计—执行绑定证据（评审 B5）：改写后计划每条子查询的规范化哈希。
    plan_hashes = {str(sq["id"]): _sql_sha256(sq["sql"]) for sq in audited_plan}

    # 执行（仅当审计给出了可执行的计划）。
    # 「空审计计划回退执行原计划」已删除（评审 B5）：空计划默认不执行——
    # 回退等于把未审计的原始 SQL 塞回执行器，中介保证就此断开。
    result = None
    executed_hash = ""
    executed_at = ""
    if audited_plan:
        # 键保持原始 id（int 或 Rule C 的 "0_a"/"0_b"），最终子查询定位与
        # 执行器约定一致（plan[-1]["id"]）。
        sql_after = {sq["id"]: sq["sql"] for sq in audited_plan}
        final_id = plan[-1]["id"]
        exec_sql = sql_after.get(final_id)
        expected = plan_hashes.get(str(final_id))
        # B5 评审 P0：**先验证、后执行**。执行器只会收到最终那条 SQL，
        # 且它的规范化哈希必须等于审计通过的最终子查询——不一致时**根本
        # 不执行**（前版是在执行后才比对并丢弃结果，SQL 实际已跑，评审指出）。
        if exec_sql is not None and expected is not None \
                and _sql_sha256(exec_sql) == expected:
            result = _execute(exec_sql, db_path=db_path)
            executed_hash = _sql_sha256(exec_sql)
            executed_at = datetime.now(timezone.utc).isoformat()
        elif exec_sql is not None:
            print(f"[query] 审计—执行绑定校验失败（decision={decision_id}），"
                  f"拒绝执行", flush=True)

    # 组装。注意：改写后计划可能与原始计划条数不同——
    #   Rule A 消除死子查询（变少）、Rule C 跨域拆分（一条变两条 0_a/0_b）。
    # 以 audited_plan 为准展示；sql_before 回填原始 SQL（拆分产物回填源查询）。
    # 空计划（含 L3 拒绝）不展示任何 SQL：回填原始计划会把被拒的语句重新
    # 摆进响应（评审 B5，与「空计划不执行」同口径）。
    display = outcome.audited_plan
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
    ), AuditDecision(
        decision_id=decision_id,
        plan_hashes=plan_hashes,
        executed_hash=executed_hash,
        executed_at=executed_at,
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
    verified(req.token)
    t0 = time.perf_counter()
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=422, detail="问题不能为空")

    # 必须连主体一起查：缓存里的计划可能把主体写成了字面量，
    # 那种计划只对生成它的那个主体成立（见 query_cache._is_subject_agnostic）。
    plan = query_cache.lookup(question, req.token.type, req.token.subject_id)

    if plan is None:
        if not llm_nl2sql.llm_available():
            # 精确原因（缺哪个配置项）只进服务端日志。**不写进给用户看的
            # 文案里**：`OPENAI_API_KEY` 是开发者才认识的配置名，而这段话
            # 是给医护和病患看的——PRODUCT.md 硬约束③「界面上只用中文业务
            # 语言」，把配置项名摊到界面上，与向医生展示 `clinical_records.
            # impression` 是同一类错误。
            print(f"[nl2sql] 问法未收录且无可用模型（未配置 OPENAI_API_KEY），"
                  f"问题：{question[:50]!r}", flush=True)
            raise HTTPException(
                status_code=503,
                detail="该问法暂未收录，系统也未接入语言模型翻译服务，"
                       "无法翻译新的问法。请从常用问题里选一个。",
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
        query_cache.store(question, plan, req.token.type,
                          req.token.subject_id, source="llm")

    resp, decision = _pipeline(plan, question, req.token, req.datasource_id, t0)
    _persist(question, req.token, resp, decision)
    return resp


def _execute(sql: Optional[str], db_path: Optional[str] = None) -> Optional[ResultSet]:
    """执行**已经过哈希校验**的审计产物 SQL（评审 B5 P0）。

    执行器只接收一条「最终子查询」字符串；调用方在传入前已把它的规范化
    哈希与审计通过的 SQL 比对过——不一致根本不会走到这里。传 None/空串
    或执行失败一律不返回结果集（前端表现为「无结果」，不会误导为
    「查询结果为空」）。执行器不接触原计划或其他 SQL 映射。

    `db_path`：数据源注册表解析出的业务库路径（评审 B6 产品化）；None →
    演示业务库。真实登记源按各自路径执行。
    """
    target = db_path or config.BUSINESS_DB
    if not sql or not os.path.exists(target):
        return None
    con = sqlite3.connect(target)
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


def _persist(question: str, token: Token, resp: QueryResponse,
             decision: Optional[AuditDecision] = None) -> None:
    """落盘一条审计报告。

    存的是**整个令牌身份**而不只是 type：安全报告要按发起人隔离。隔离依据
    是 **account**——按 (token_type, subject_id) 隔离时，管理员（subject_id
    为空）会和"绑定主体留空"的 staff 撞进同一个桶，而建号界面上那个字段是
    选填的，等于默认路径上就串号。
    """
    db_path = config.METADATA_DB
    if not os.path.exists(db_path):
        return
    save_report(db_path, {
        "question": question,
        "token_type": token.type,
        "subject_id": token.subject_id,
        "account": token.account,
        "degradation_level": resp.degradation.level,
        "event_count": len(resp.events),
        "payload": resp.model_dump(),
        # B5：审计—执行绑定证据（领域类型，落库时展平为 dict）
        "decision": decision.model_dump() if decision else None,
    })
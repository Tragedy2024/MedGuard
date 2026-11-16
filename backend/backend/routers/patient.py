"""患者统一入口：一个输入框，服务端确定性分流。

路由规则见 ``backend/patient_router.py``（本地、零 LLM、零查库）。本路由
只负责 HTTP 边界与两条既有管线的接线：

- ``personal_record`` / ``knowledge`` / ``clarify`` / ``unsupported``
  → ``smart_doctor.ask``（本人记录仍走层一/层二）；
- ``general_query`` → ``query_cache.lookup`` → 未命中 ``llm_nl2sql.decompose``
  → ``routers.query.run_audited_plan``。**绝不直接执行 SQL**。

令牌约束：仅病患令牌，且必须绑定 ``subject_id``——没有本人绑定就无从
保证「只读本人」。通用查询对病患令牌同样走层一准入；路由不构成授权。
"""
from fastapi import APIRouter, HTTPException

from backend import llm_nl2sql, query_cache, smart_doctor
from backend.patient_router import ROUTE_GENERAL_QUERY, decide
from backend.routers.query import run_audited_plan
from backend.schemas import PatientAskRequest, PatientAskResponse
from backend.security import verified

router = APIRouter(prefix="/api/patient", tags=["patient"])

# 未命中缓存且没有可用模型时的可读原因。**不写配置项名**：这段话是给
# 病患看的，`OPENAI_API_KEY` 只有开发者认识。精确原因进服务端日志。
_NO_MODEL_ERROR = (
    "该问法暂未收录，系统也未接入语言模型翻译服务，无法完成通用数据查询。"
    "请换一种说法（例如说明具体疾病、症状或想查询的医院业务指标）。"
)


@router.post("/ask", response_model=PatientAskResponse)
def ask(req: PatientAskRequest):
    """患者单输入框问答入口。"""
    verified(req.token)
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=422, detail="问题不能为空")
    if req.token.type != "patient":
        raise HTTPException(
            status_code=403,
            detail="患者统一入口仅面向病患令牌开放（医护请使用查询控制台）。",
        )
    if not req.token.subject_id:
        raise HTTPException(
            status_code=422,
            detail="病患令牌缺少本人绑定（subject_id），无法保证只读取本人数据。",
        )

    context = req.context.model_dump() if req.context else None
    decision = decide(
        question, context=context, token_type=req.token.type,
        subject_id=req.token.subject_id)
    # 路由理由必须可回看：评审/排查时能解释「为什么走这条管线」。
    print(f"[patient] route={decision.route} reason={decision.reason}",
          flush=True)

    if decision.route != ROUTE_GENERAL_QUERY:
        reply = smart_doctor.ask(
            question, req.token.subject_id, context=context,
            token=req.token, datasource_id=req.datasource_id)
        return PatientAskResponse(
            route=decision.route, reason=decision.reason,
            smart_doctor=reply, query=None, error=None)

    # ── 通用查询：缓存优先，未命中再翻译 ──
    # 缓存的是**计划**不是答案：命中后照样走层一准入与层二审计。
    plan = query_cache.lookup(
        question, req.token.type, req.token.subject_id)
    if plan is None:
        if not llm_nl2sql.llm_available():
            print(f"[patient] 通用查询未收录且无可用模型，问题：{question[:50]!r}",
                  flush=True)
            return PatientAskResponse(
                route=decision.route, reason=decision.reason,
                smart_doctor=None, query=None, error=_NO_MODEL_ERROR)
        try:
            plan = llm_nl2sql.decompose(
                question, req.datasource_id,
                token_type=req.token.type,
                subject_id=req.token.subject_id or "",
            )
        except llm_nl2sql.NL2SQLError as exc:
            # 翻译失败**不是**服务端故障：HTTP 200 + 中文原因，避免患者
            # 看到 500 而反复重试。原文已在 llm_nl2sql 内进日志。
            return PatientAskResponse(
                route=decision.route, reason=decision.reason,
                smart_doctor=None, query=None,
                error=f"通用查询翻译失败：{exc}")
        except Exception as exc:  # noqa: BLE001 - 翻译环节任何异常都不得变成 500
            # 未预期的翻译异常也按「这次问题没办成」处理；精确原因只进
            # 服务端日志，界面只给可读中文。
            print(f"[patient] 通用查询翻译异常（{type(exc).__name__}）：{exc}",
                  flush=True)
            return PatientAskResponse(
                route=decision.route, reason=decision.reason,
                smart_doctor=None, query=None,
                error="通用查询翻译失败：翻译服务暂时不可用，请稍后重试，"
                      "或换一种问法。")
        # 只写回成功的翻译；失败不该污染缓存。
        query_cache.store(question, plan, req.token.type,
                          req.token.subject_id, source="llm")

    # 所有数据库路径都必须跨过这个 seam（层一准入 → 层二审计 → 哈希绑定
    # 执行 → 报告落盘）；本路由不持有、也不执行任何 SQL。
    query_response, _audit_decision, _report_id = run_audited_plan(
        plan, question, req.token, req.datasource_id)
    return PatientAskResponse(
        route=decision.route, reason=decision.reason,
        smart_doctor=None, query=query_response, error=None)

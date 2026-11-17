"""患者统一入口：一个输入框，服务端确定性分流。

普通自然提问先做有界的语义理解，再交 ``backend/patient_router.py`` 本地规则选择管线。本路由
先按 ``feature`` 选择智慧医生或查询控制台，再根据查询范围复用既有执行器。
本路由负责 HTTP 边界与执行器的接线，内部 ``route`` 兼容值如下：

- 医学咨询及澄清 → ``smart_doctor.ask``；
- 所有院内查询（含本人检验、用药、费用、就诊及医院业务）→ 原控制台 ``query.run_direct_query``，完整复用
  缓存、NL→SQL、准入、审计与执行绑定。**绝不直接执行 SQL**。

令牌约束：仅病患令牌，且必须绑定 ``subject_id``——没有本人绑定就无从
保证「只读本人」。通用查询对病患令牌同样走层一准入；路由不构成授权。
"""
from fastapi import APIRouter, HTTPException

from backend import smart_doctor, semantic_understanding
from backend.patient_router import decide
from backend.routers import query as query_console
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
    understanding = semantic_understanding.analyze(question, context)
    decision = decide(
        question, context=context, token_type=req.token.type,
        subject_id=req.token.subject_id, understanding=understanding)
    # 路由理由必须可回看：评审/排查时能解释「为什么走这条管线」。
    print(f"[patient] route={decision.route} reason={decision.reason}",
          flush=True)

    response_info = {'feature': decision.feature, 'query_scope': decision.query_scope}
    # 顶层先选功能，查询控制台再按范围使用既有的审计执行器。
    use_original_console = decision.feature == 'query_console'
    if not use_original_console:
        reply = smart_doctor.ask(
            question, req.token.subject_id, context=context,
            token=req.token, datasource_id=req.datasource_id, understanding=understanding)
        return PatientAskResponse(
            route=decision.route, reason=decision.reason,
            **response_info,
            smart_doctor=reply, query=None, error=None,
            routing_choices=semantic_understanding.routing_choices(understanding))

    # 调用原控制台实现，而不是再维护一份只支持若干问法的查询能力。
    try:
        query_response = query_console.run_direct_query(query_console.DirectQueryRequest(
            token=req.token, datasource_id=req.datasource_id, question=question))
    except HTTPException as exc:
        if exc.status_code not in {502, 503}:
            raise
        message = _NO_MODEL_ERROR if exc.status_code == 503 and '模型' in str(exc.detail) else str(exc.detail)
        return PatientAskResponse(route=decision.route, reason=decision.reason,
                                  **response_info, error=message)
    except Exception as exc:
        print(f'[patient] 通用查询异常（{type(exc).__name__}）：{exc}', flush=True)
        return PatientAskResponse(route=decision.route, reason=decision.reason,
                                  **response_info, error='通用查询翻译失败：查询服务暂时不可用，请稍后重试。')
    return PatientAskResponse(
        route=decision.route, reason=decision.reason,
        **response_info,
        smart_doctor=None, query=query_response, error=None)

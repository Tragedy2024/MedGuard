"""患者单输入框的**本地确定性路由**。

患者只有一个输入框，问题可能是「我上次的血糖是多少」（本人记录）、
「糖尿病挂什么科」（医学知识）、「统计各科室接诊量」（医院业务统计），
也可能信息不足（「我不舒服」）。本模块只做一件事：在**服务端**把问题
分到一条路由上，返回可解释的理由。

约束（与 CONTRACT 一致）：

- **零 LLM、零 Jev、零查库**：只用 `smart_doctor.parse_intent` 的关键词/
  别名匹配与 `strategy.choose` 的规则。模型不参与数据访问权限的判定。
- 路由结果只决定「接下来走哪条既有管线」：
  `personal_record` / `knowledge` / `clarify` / `unsupported` 交给智慧医生
  `smart_doctor.ask`；`general_query` 交给通用查询管线
  （`query_cache` → `llm_nl2sql` → `routers.query.run_audited_plan`）。
- 病患令牌的通用查询**照样**要过层一准入与层二审计；路由不是授权。

优先级（显式写死，便于审计）：

1. 本人记录：`strategy.choose` 返回 `personal_sql`，且令牌是病患；
2. 医院业务统计：强业务信号（费用/账单/接诊/门诊量/排班/床位/统计/
   排名/名单/哪些医生/多少(人|位|个患者)…）。但「糖尿病挂什么科」这类
   **挂科类知识问题**即使偶然带业务词仍归知识；
3. 澄清：`strategy` 为 `clarify`（缺实体/指代不明）。必须排在知识之前，
   否则「它正常吗」（无 context）会被检验意图误判成知识；
4. 知识：`parse_intent` 为 disease/symptom/lab/medication，或取数策略为
   structured_lookup/hybrid/subtype_lookup；
5. 其余为 unsupported。
"""
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

from backend import dataset_kb, smart_doctor
from backend.knowledge import strategy
from backend.knowledge.strategy import DEPARTMENT_QUESTION_SIGNALS

ROUTE_PERSONAL_RECORD = "personal_record"
ROUTE_GENERAL_QUERY = "general_query"
ROUTE_KNOWLEDGE = "knowledge"
ROUTE_CLARIFY = "clarify"
ROUTE_UNSUPPORTED = "unsupported"

_KNOWLEDGE_INTENTS = frozenset({
    smart_doctor.INTENT_DISEASE,
    smart_doctor.INTENT_SYMPTOM,
    smart_doctor.INTENT_LAB,
    smart_doctor.INTENT_MEDICATION,
})
_KNOWLEDGE_STRATEGIES = frozenset({
    "structured_lookup", "hybrid", "subtype_lookup",
})

# 强医院业务信号。词表来自 CONTRACT §2：只有这些词才足以说明「问题要查
# 医院运营/患者总体数据」，而不是医学知识或本人记录。
_GENERAL_QUERY_SIGNAL = re.compile(
    r"费用|账单|接诊|门诊量|排班|床位|统计|排名|名单|"
    r"哪些医生|哪个医生|多少(?:人|位|个患者)|得这个病的有多少人"
)

_INTENT_LABELS = {
    smart_doctor.INTENT_LAB: "检验/检查指标",
    smart_doctor.INTENT_MEDICATION: "用药",
    smart_doctor.INTENT_SYMPTOM: "症状",
    smart_doctor.INTENT_DISEASE: "疾病",
    smart_doctor.INTENT_FALLBACK: "咨询",
}


@dataclass(frozen=True)
class RouteDecision:
    """一次路由决定（纯数据，不含任何数据访问）。"""
    route: str
    reason: str
    intent: str
    entity: Optional[str] = None


def _general_signal(question: str) -> Optional[str]:
    match = _GENERAL_QUERY_SIGNAL.search(question)
    return match.group(0) if match else None


def _is_department_knowledge(question: str, intent: str) -> bool:
    """挂科类问题（「糖尿病挂什么科」）属于知识，不属于业务统计。

    CONTRACT 点名要求这一条。即使问题里偶然出现「哪个医生」这类业务词，
    只要核心是「某病/某症状该挂哪个科」，仍应由知识库回答。
    """
    return (intent in _KNOWLEDGE_INTENTS
            and any(signal in question for signal in DEPARTMENT_QUESTION_SIGNALS))


def decide(question: str, context: Optional[Dict[str, Any]] = None,
           token_type: str = "patient",
           subject_id: Optional[str] = None) -> RouteDecision:
    """本地确定性地选择一条路由。

    Args:
        question: 患者原话。
        context: 上一轮识别结果（intent/entity/personal），用于解析
            「它正常吗」这类指代式追问。
        token_type: ``patient`` / ``staff``。本人记录路由只对病患令牌成立；
            该参数不改变通用查询仍需层一/层二审计这一事实。
        subject_id: 令牌绑定的主体。仅用于理由文案；是否必需由 HTTP 层校验。

    Returns:
        ``RouteDecision``。不调用模型、不访问数据库、不写任何状态。
    """
    q = (question or "").strip()
    if not q:
        return RouteDecision(
            ROUTE_CLARIFY, "问题为空，请描述您想咨询或查询的内容。",
            smart_doctor.INTENT_FALLBACK, None)

    raw_intent, raw_entity = smart_doctor.parse_intent(q)
    # 与 smart_doctor.ask 共用同一套指代解析，避免「路由说知识、ask 却
    # 按本人记录取数」的两套语义。该函数是纯规则、无 IO。
    intent, entity = smart_doctor._resolve_with_context(
        raw_intent, raw_entity, context, question=q)
    # 保留 ask() 的挂科追问分支：上一轮问疾病、这一轮只说「挂什么科」时，
    # 仍沿用疾病实体，避免路由显示「澄清」而 ask 实际返回科室卡。
    if (raw_intent == smart_doctor.INTENT_SYMPTOM and raw_entity is None
            and context
            and context.get("intent") == smart_doctor.INTENT_DISEASE
            and context.get("entity")
            and smart_doctor._DEPARTMENT_FOLLOWUP.fullmatch(q)):
        intent, entity = smart_doctor.INTENT_DISEASE, context["entity"]

    kb = smart_doctor.load_knowledge()
    # 与 ask() 保持一致：只有「上一轮是本人检验/用药 + context.personal」
    # 的短追问，才把策略提升为 personal_sql。
    personal_followup = False
    if raw_entity is None and entity and intent in {
            smart_doctor.INTENT_LAB, smart_doctor.INTENT_MEDICATION}:
        canonical = kb.get(
            "lab_tests" if intent == smart_doctor.INTENT_LAB else "medications"
        ) or {}
        if entity not in canonical:
            entity = None
        elif context and context.get("personal") is True:
            personal_followup = True

    plan = strategy.choose(
        q, intent, entity, kb,
        disease_names=dataset_kb.disease_names(),
        personal_followup=personal_followup)

    # 1) 本人记录。非病患令牌不得走这条（HTTP 层已拒绝 staff，这里再兜一层，
    #    让 decide 单独被调用时语义也正确）。
    if plan.strategy == "personal_sql":
        if token_type == "patient":
            subject_note = f"（绑定主体 {subject_id}）" if subject_id else ""
            entity_note = f"，实体「{entity}」" if entity else ""
            return RouteDecision(
                ROUTE_PERSONAL_RECORD,
                f"问题指向本人记录{entity_note}{subject_note}；"
                f"策略 personal_sql：只读取本人数据并走层一/层二审计。",
                intent, entity)
        # 医护令牌问「我的…」不构成病患本人数据授权，按知识/其他路由处理。

    # 2) 医院业务统计。强信号优先于泛化的知识检索（例如
    #    「得这个病的有多少人」会被 parse_intent 误当疾病咨询）。
    signal = _general_signal(q)
    if signal and not _is_department_knowledge(q, intent):
        return RouteDecision(
            ROUTE_GENERAL_QUERY,
            f"命中医院业务统计信号「{signal}」；这不是医学知识或本人记录，"
            f"需要跨患者/科室聚合，交由通用查询管线（层一准入 + 层二审计）。",
            intent, entity)

    # 3) 澄清必须早于知识：缺实体/指代不明时，先问清楚再决定。
    if plan.strategy == "clarify":
        reason = plan.reason.rstrip("。")
        return RouteDecision(
            ROUTE_CLARIFY,
            f"信息不足（{reason}）；请补充具体症状、疾病名称、检验项或比较对象。",
            intent, entity)

    # 4) 医学知识。
    if intent in _KNOWLEDGE_INTENTS:
        entity_note = f"（实体：{entity}）" if entity else ""
        return RouteDecision(
            ROUTE_KNOWLEDGE,
            f"识别为{_INTENT_LABELS.get(intent, '医学')}问题{entity_note}；"
            f"不涉及患者个人数据，走医院审核知识库。",
            intent, entity)
    if plan.strategy in _KNOWLEDGE_STRATEGIES:
        return RouteDecision(
            ROUTE_KNOWLEDGE,
            f"取数策略为 {plan.strategy}（{plan.reason}）；"
            f"按受元数据约束的知识检索处理，不直接访问患者数据。",
            intent, entity)

    # 5) 规则之外。
    return RouteDecision(
        ROUTE_UNSUPPORTED,
        f"问题不属于医学知识、本人记录或医院业务统计范围"
        f"（策略 {plan.strategy}）。",
        intent, entity)

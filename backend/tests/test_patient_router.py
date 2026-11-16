"""患者单输入框路由决策测试。

只测 **本地确定性路由**：不联网、不调模型、不查库。路由规则见
`.scratch/patient-unified-entry/CONTRACT.md` §2 与 backend/patient_router.py。
"""
import pytest

from backend import jev_client, smart_doctor
from backend.knowledge.strategy import RetrievalPlan
from backend.patient_router import (ROUTE_CLARIFY, ROUTE_GENERAL_QUERY,
                                    ROUTE_KNOWLEDGE, ROUTE_PERSONAL_RECORD,
                                    ROUTE_UNSUPPORTED, decide)


@pytest.mark.parametrize("question", [
    "我上次的血糖是多少",
    "我的血脂和血糖结果正常吗",
    "医生给我开的药怎么吃",
])
def test_personal_record_questions_route_to_personal(question):
    decision = decide(question)
    assert decision.route == ROUTE_PERSONAL_RECORD
    assert decision.reason.strip(), "路由理由必须可解释"
    assert "本人" in decision.reason


@pytest.mark.parametrize("question", [
    "糖尿病有哪几种",
    "糖尿病挂什么科",
    "血糖高怎么办",
    "糖尿病和高血压有什么区别",
])
def test_knowledge_questions_route_to_knowledge(question):
    decision = decide(question)
    assert decision.route == ROUTE_KNOWLEDGE
    assert decision.intent in {"disease", "symptom", "lab", "medication"}
    assert decision.reason.strip()


def test_diabetes_department_question_is_knowledge_not_general():
    """CONTRACT 点名：挂科问题是知识，不是医院业务统计。"""
    decision = decide("糖尿病挂什么科")
    assert decision.route == ROUTE_KNOWLEDGE
    assert decision.intent == "disease"
    assert decision.entity == "糖尿病"


@pytest.mark.parametrize(("question", "signal"), [
    ("心内科有哪些医生", "哪些医生"),
    ("统计各科室接诊量", "统计"),
    ("得这个病的有多少人", "得这个病的有多少人"),
    ("糖尿病患者产生了多少费用", "费用"),
])
def test_general_business_questions_route_to_general_query(question, signal):
    decision = decide(question)
    assert decision.route == ROUTE_GENERAL_QUERY
    # 理由要能说明命中了哪个业务信号，而不是一句「走了通用查询」
    assert signal in decision.reason


@pytest.mark.parametrize("question", ["我不舒服", "它正常吗"])
def test_missing_entity_without_context_asks_for_clarification(question):
    decision = decide(question)
    assert decision.route == ROUTE_CLARIFY
    assert decision.reason.strip()


def test_context_turns_pronoun_followup_into_personal_record():
    """「它正常吗」带上一轮本人检验上下文 → 查本人记录；无 context → 澄清。"""
    decision = decide(
        "它正常吗",
        context={"intent": "lab", "entity": "血糖", "personal": True})
    assert decision.route == ROUTE_PERSONAL_RECORD
    assert decision.intent == "lab"
    assert decision.entity == "血糖"


def test_department_followup_reuses_previous_disease_context():
    """上一轮是疾病、这一轮只问「挂什么科」→ 知识，而不是澄清。"""
    decision = decide(
        "挂什么科", context={"intent": "disease", "entity": "糖尿病"})
    assert decision.route == ROUTE_KNOWLEDGE
    assert decision.intent == "disease"
    assert decision.entity == "糖尿病"


def test_staff_token_never_routes_as_personal_record():
    """路由本身也知道：非病患令牌不能走本人记录（HTTP 层另有 403）。"""
    decision = decide("我上次的血糖是多少", token_type="staff",
                      subject_id="S001")
    assert decision.route != ROUTE_PERSONAL_RECORD


def test_empty_question_is_clarification():
    assert decide("   ").route == ROUTE_CLARIFY


def test_unexpected_strategy_falls_back_to_unsupported(monkeypatch):
    """规则之外要落到 unsupported，而不是悄悄猜一条数据管线。"""
    monkeypatch.setattr(
        "backend.patient_router.strategy.choose",
        lambda *a, **k: RetrievalPlan("unexpected", "测试用未知策略"))
    decision = decide("今天天气怎么样")
    assert decision.route == ROUTE_UNSUPPORTED
    assert "unexpected" in decision.reason


def test_decide_is_offline_and_read_only(monkeypatch):
    """decide 不得调 ask / 模型 / Jev / 本人取数（CONTRACT §2）。"""

    def boom(*_args, **_kwargs):
        pytest.fail("路由决策不应触发模型、Jev、问答或数据访问")

    monkeypatch.setattr(smart_doctor, "ask", boom)
    monkeypatch.setattr(smart_doctor, "_call_llm", boom)
    monkeypatch.setattr(smart_doctor, "fetch_personal_data", boom)
    monkeypatch.setattr(jev_client, "classify", boom)

    for question in ["我上次的血糖是多少", "糖尿病挂什么科",
                     "心内科有哪些医生", "我不舒服"]:
        decide(question)

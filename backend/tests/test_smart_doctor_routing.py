from threading import Event

import httpx
import pytest

from backend import config, jev_client, smart_doctor as sd


@pytest.fixture
def public_only(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("公开问法或澄清不应查询本人记录")

    monkeypatch.setattr(sd, "fetch_personal_data", unexpected)
    monkeypatch.setattr(config, "LLM_API_KEY", "")


@pytest.mark.parametrize(("question", "intent", "term"), [
    ("二甲双胍是干什么用的", "medication", "二甲双胍"),
    ("我最近在了解二甲双胍是干什么用的", "medication", "二甲双胍"),
    ("做血常规能看到些什么", "lab", "血常规"),
])
def test_public_knowledge_never_reads_records(public_only, question, intent, term):
    result = sd.ask(question, "P001")
    assert result["intent"] == intent
    assert result["retrieval_plan"]["strategy"] == "structured_lookup"
    assert result["data"] is None
    assert term in [item["ref_title"] for item in result["interpretation"]["items"]]


def test_unrelated_question_does_not_inherit_personal_intent(public_only):
    result = sd.ask("怎么办理出院手续", "P001",
                    context={"intent": "lab", "entity": "血糖", "personal": True})
    assert result["intent"] == "fallback"
    assert result["data"] is None


def test_vague_symptom_asks_for_details(public_only, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("模糊表述应先追问")

    monkeypatch.setattr(sd, "_apply_ai_fallback", unexpected)
    result = sd.ask("人怎么老是蔫蔫的", "P001")
    assert result["intent"] == "symptom"
    assert result["retrieval_plan"]["strategy"] == "clarify"
    assert "没力气" in result["interpretation"]["text"]


def test_colloquial_symptom_uses_existing_triage_data(public_only):
    result = sd.ask("这几天一直睡不踏实", "P001")
    assert result["intent"] == "symptom"
    assert result["retrieval_plan"]["strategy"] == "structured_lookup"
    assert result["interpretation"]["items"]


def test_public_context_does_not_turn_into_personal_followup(public_only):
    result = sd.ask("它正常吗", "P001", context={"intent": "lab", "entity": "血糖"})
    assert result["retrieval_plan"]["strategy"] == "structured_lookup"
    assert result["data"] is None


def test_third_party_request_never_reads_own_records(public_only):
    result = sd.ask("帮我看看我妈妈的血常规结果", "P001")
    assert result["data"] is None


@pytest.fixture
def approved_jev(monkeypatch):
    question = "甲状腺结节是咋回事"
    monkeypatch.setattr(config, "JEV_ENABLED", True)
    monkeypatch.setattr(config, "JEV_API_KEY", "placeholder")
    monkeypatch.setattr(config, "JEV_APPROVED_QUESTIONS", frozenset({question}))
    return question


def test_jev_only_sends_exact_approved_public_question(public_only, approved_jev, monkeypatch):
    def response(url, *, headers, json):
        assert url == "https://api.typesafe.ai/v1/systemone"
        assert json["state"] == approved_jev
        assert set(json) == {"model", "state", "questions"}
        assert set(json["questions"]["route"]["criteria"]) == {
            "disease", "symptom", "clarify", "out_of_scope",
        }
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "answers": {"route": {"type": "choice", "choice": "disease",
                                  "probabilities": {"disease": 0.99}}}
        })

    monkeypatch.setattr(jev_client._HTTP, "post", response)
    assert not jev_client.is_approved(approved_jev + "，我的病历号是示例")
    result = sd.ask(approved_jev, "P001")
    assert result["intent"] == "disease"
    assert result["entity"] == "甲状腺结节"
    assert result["retrieval_plan"]["strategy"] == "structured_lookup"
    assert "Jev" in result["retrieval_plan"]["reason"]
    assert result["interpretation"]["items"]


@pytest.mark.parametrize(("choice", "probability", "status"), [
    ("lab", 1.0, 200), ("medication", 1.0, 200),
    ("disease", 0.60, 200), ("disease", 1.0, 401),
])
def test_jev_rejects_invalid_or_low_score_results(
        public_only, approved_jev, monkeypatch, choice, probability, status):
    monkeypatch.setattr(jev_client._HTTP, "post", lambda url, **_: httpx.Response(
        status, request=httpx.Request("POST", url), json={
            "answers": {"route": {"type": "choice", "choice": choice,
                                  "probabilities": {choice: probability}}}
        }))
    result = sd.ask(approved_jev, "P001")
    assert result["intent"] == "fallback"
    assert result["data"] is None


def test_rules_take_priority_even_if_jev_question_is_approved(public_only, approved_jev, monkeypatch):
    question = "二甲双胍是干什么用的"
    monkeypatch.setattr(config, "JEV_APPROVED_QUESTIONS", frozenset({question}))

    def unexpected(*args, **kwargs):
        pytest.fail("规则已经命中，无需调用 Jev")

    monkeypatch.setattr(jev_client, "classify", unexpected)
    assert sd.ask(question, "P001")["retrieval_plan"]["strategy"] == "structured_lookup"


def test_jev_timeout_falls_back_without_using_late_answer(public_only, approved_jev, monkeypatch):
    release = Event()
    entered = Event()

    def slow_request(question):
        entered.set()
        assert release.wait(timeout=3)
        return "disease"

    monkeypatch.setattr(jev_client, "_request_choice", slow_request)
    monkeypatch.setattr(jev_client, "_TIMEOUT_SECONDS", 0.05)
    try:
        result = sd.ask(approved_jev, "P001")
        assert entered.is_set()
        assert result["intent"] == "fallback"
        assert result["data"] is None
    finally:
        release.set()

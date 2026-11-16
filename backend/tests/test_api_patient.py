"""患者统一入口 `/api/patient/ask` 的 API 测试。

与现有路由测试一致：令牌必须签名（裸字典会 401），演示数据由
``load_demo`` 载入；测试进程由 conftest 强制 ``MEDGUARD_TESTING=1``，
因此永远不会真的联网或调用模型（除非用例显式 monkeypatch）。
"""
import pytest
from fastapi.testclient import TestClient

from backend import config, llm_nl2sql, query_cache
from backend.main import app
from tests.helpers import load_demo, sign


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDGUARD_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "METADATA_DB", str(tmp_path / "medguard.db"))
    monkeypatch.setattr(config, "BUSINESS_DB",
                        str(tmp_path / "regional_health.db"))
    from backend.db import init_db
    init_db(str(tmp_path / "medguard.db"))
    c = TestClient(app)
    load_demo(c)
    return c


def _ask(client, token, question, **extra):
    return client.post("/api/patient/ask", json={
        "token": token,
        "datasource_id": "regional_health",
        "question": question,
        **extra,
    })


# ── 本人记录 ───────────────────────────────────────────────────

def test_personal_question_goes_through_smart_doctor(client):
    r = _ask(client, sign("patient", "P001"), "我上次的血糖是多少")
    assert r.status_code == 200
    body = r.json()
    assert body["route"] == "personal_record"
    assert body["reason"]
    assert body["query"] is None
    assert body["error"] is None
    sd = body["smart_doctor"]
    assert sd is not None
    assert sd["intent"] == "lab"
    # 三块结构中的院内数据确实来自本人记录（经层一/层二）
    assert sd["admission"]["passed"] is True
    assert sd["data"]["source"] == "医院主库"
    assert sd["data"]["rows"]


def test_context_followup_stays_on_personal_record(client):
    """「它正常吗」带上一轮本人检验上下文 → 查本人记录，而不是澄清。"""
    r = _ask(
        client, sign("patient", "P001"), "它正常吗",
        context={"intent": "lab", "entity": "血糖", "personal": True})
    assert r.status_code == 200
    body = r.json()
    assert body["route"] == "personal_record"
    assert body["smart_doctor"]["intent"] == "lab"


# ── 知识问答 ───────────────────────────────────────────────────

def test_knowledge_question_returns_smart_doctor(client):
    r = _ask(client, sign("patient", "P001"), "糖尿病挂什么科")
    assert r.status_code == 200
    body = r.json()
    assert body["route"] == "knowledge"
    assert body["query"] is None
    assert body["error"] is None
    assert body["smart_doctor"]["intent"] == "disease"


# ── 通用查询：缓存命中（不走模型）──────────────────────────────

def test_general_question_uses_cached_plan_without_llm(client, monkeypatch):
    """预设缓存里已有「心内科有哪些医生」的患者计划。

    命中缓存必须完全离线：把 decompose 换成「一调就炸」，证明没碰模型，
    同时仍然走层一/层二并拿到结果。
    """
    def boom(*_args, **_kwargs):
        pytest.fail("命中缓存时不应调用模型翻译")

    monkeypatch.setattr(llm_nl2sql, "decompose", boom)
    r = _ask(client, sign("patient", "P001"), "心内科有哪些医生")
    assert r.status_code == 200
    body = r.json()
    assert body["route"] == "general_query"
    assert body["reason"]
    assert body["smart_doctor"] is None
    assert body["error"] is None
    assert body["query"]["admission"]["passed"] is True
    assert body["query"]["result"] is not None


# ── 通用查询：层一拒绝（HTTP 仍是 200）─────────────────────────

def test_general_query_is_rejected_by_layer1_for_patient(client):
    """预设计划统计全院患者数，未绑定本人 → 层一拒绝，不执行。"""
    r = _ask(client, sign("patient", "P001"), "得这个病的有多少人")
    assert r.status_code == 200
    body = r.json()
    assert body["route"] == "general_query"
    q = body["query"]
    assert q is not None
    assert q["admission"]["passed"] is False
    assert q["result"] is None
    assert q["plan"] == []
    assert q["degradation"]["level"] == "L3"


def test_cost_question_is_general_and_rejected_by_layer1(
        client, isolated_cache):
    """「糖尿病患者产生了多少费用」必须走通用查询，且患者令牌被层一拒绝。

    这里显式往隔离缓存里放一份跨患者费用计划——演示缓存的该条目只对
    staff 开放，患者令牌本就不该命中。
    """
    question = "糖尿病患者产生了多少费用"
    query_cache.store(question, [{
        "id": 0,
        "description": "统计全院费用",
        "sql": "SELECT SUM(amount) FROM billing",
    }], "patient", "P001")

    r = _ask(client, sign("patient", "P001"), question)
    assert r.status_code == 200
    body = r.json()
    assert body["route"] == "general_query"
    assert body["query"]["admission"]["passed"] is False
    assert body["query"]["admission"]["reason"] == "该查询涉及其他患者信息，无法提供。"
    assert body["query"]["result"] is None


# ── 通用查询：缓存未命中 → 翻译 → 统一审计管线 ────────────────

def test_general_cache_miss_translates_then_audits(client, isolated_cache,
                                                   monkeypatch):
    seen = {}

    def fake_decompose(question, datasource_id, token_type="staff",
                       subject_id=""):
        seen.update(question=question, datasource_id=datasource_id,
                    token_type=token_type, subject_id=subject_id)
        return [{
            "id": 0,
            "description": "心内科医生名单",
            "sql": "SELECT name FROM staff WHERE department = '心内科'",
        }]

    monkeypatch.setattr(llm_nl2sql, "llm_available", lambda: True)
    monkeypatch.setattr(llm_nl2sql, "decompose", fake_decompose)

    r = _ask(client, sign("patient", "P001"), "心内科有哪些医生")
    assert r.status_code == 200
    body = r.json()
    assert body["route"] == "general_query"
    assert body["query"]["admission"]["passed"] is True
    assert body["query"]["result"] is not None
    assert seen == {
        "question": "心内科有哪些医生",
        "datasource_id": "regional_health",
        "token_type": "patient",
        "subject_id": "P001",
    }

    # 成功的翻译写回缓存：第二次不应再调模型。
    monkeypatch.setattr(llm_nl2sql, "decompose",
                        lambda *a, **k: pytest.fail("第二次应命中缓存"))
    r2 = _ask(client, sign("patient", "P001"), "心内科有哪些医生")
    assert r2.status_code == 200
    assert r2.json()["query"]["result"] is not None


# ── 通用查询：翻译失败/无模型 → HTTP 200 + 中文 error ─────────

def test_translation_failure_returns_200_with_chinese_error(
        client, isolated_cache, monkeypatch):
    monkeypatch.setattr(llm_nl2sql, "llm_available", lambda: True)

    def fail(*_args, **_kwargs):
        raise llm_nl2sql.NL2SQLError("模型调用失败：连接超时")

    monkeypatch.setattr(llm_nl2sql, "decompose", fail)

    r = _ask(client, sign("patient", "P001"), "统计各科室接诊量")
    assert r.status_code == 200, "翻译失败不应是 500"
    body = r.json()
    assert body["route"] == "general_query"
    assert body["query"] is None
    assert body["smart_doctor"] is None
    assert body["error"]
    assert "翻译失败" in body["error"]
    assert "连接超时" in body["error"]


def test_unexpected_translation_exception_still_returns_200(
        client, isolated_cache, monkeypatch):
    """翻译环节的任何异常都不能变成 500；未知原因只进日志。"""
    monkeypatch.setattr(llm_nl2sql, "llm_available", lambda: True)

    def explode(*_args, **_kwargs):
        raise RuntimeError("upstream exploded")

    monkeypatch.setattr(llm_nl2sql, "decompose", explode)

    r = _ask(client, sign("patient", "P001"), "统计各科室接诊量")
    assert r.status_code == 200
    body = r.json()
    assert body["route"] == "general_query"
    assert body["query"] is None
    assert "翻译失败" in body["error"]
    assert "exploded" not in body["error"]


def test_no_model_returns_200_with_actionable_error(client, isolated_cache,
                                                    monkeypatch):
    monkeypatch.setattr(llm_nl2sql, "llm_available", lambda: False)

    r = _ask(client, sign("patient", "P001"), "统计各科室接诊量")
    assert r.status_code == 200
    body = r.json()
    assert body["route"] == "general_query"
    assert body["query"] is None
    assert "暂未收录" in body["error"]
    # 给病患看的文案不能出现开发者配置项名
    assert "OPENAI_API_KEY" not in body["error"]


# ── 澄清 ───────────────────────────────────────────────────────

@pytest.mark.parametrize("question", ["我不舒服", "它正常吗"])
def test_incomplete_question_returns_clarification(client, question):
    r = _ask(client, sign("patient", "P001"), question)
    assert r.status_code == 200
    body = r.json()
    assert body["route"] == "clarify"
    assert body["query"] is None
    assert body["error"] is None
    assert body["smart_doctor"] is not None


# ── 令牌约束 ───────────────────────────────────────────────────

def test_staff_token_is_forbidden(client):
    r = _ask(client, sign("staff"), "我的血糖结果正常吗")
    assert r.status_code == 403


def test_patient_token_without_subject_is_rejected(client):
    r = _ask(client, sign("patient"), "我的血糖结果正常吗")
    assert r.status_code == 422


def test_empty_question_is_rejected(client):
    r = _ask(client, sign("patient", "P001"), "   ")
    assert r.status_code == 422


def test_forged_token_is_rejected(client):
    r = _ask(client, {"type": "patient", "subject_id": "P002"},
             "我上次的血糖是多少")
    assert r.status_code == 401

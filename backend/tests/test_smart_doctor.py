"""智慧医生测试——意图识别、三块结构、医盾管线复用、令牌约束。

不依赖模型、不联网：知识库是 YAML，取数走本地演示库（fixture 建库）。
"""
import pytest

from backend import smart_doctor
from backend.smart_doctor import (INTENT_DISEASE, INTENT_FALLBACK, INTENT_LAB,
                                  INTENT_MEDICATION, INTENT_SYMPTOM,
                                  interpret_lab, parse_intent)


@pytest.fixture(scope="module")
def demo_db(tmp_path_factory):
    """建一个独立演示库并指向 config（P001 血糖锚点 = 6.3）。"""
    from backend import config
    db = tmp_path_factory.mktemp("data") / "regional_health.db"
    from demo.seed import build_database
    build_database(str(db))
    old = config.BUSINESS_DB
    config.BUSINESS_DB = str(db)
    yield
    config.BUSINESS_DB = old


# ── 意图识别 ───────────────────────────────────────────────────

def test_intent_lab_with_test_name():
    intent, entity = parse_intent("我的血糖结果正常吗？接下来怎么办？")
    assert intent == INTENT_LAB
    assert entity == "血糖"


def test_intent_lab_generic():
    intent, entity = parse_intent("帮我看懂检查报告")
    assert intent == INTENT_LAB
    assert entity is None


def test_intent_medication_by_drug_name():
    intent, entity = parse_intent("二甲双胍应该怎么服用")
    assert intent == INTENT_MEDICATION
    assert entity == "二甲双胍"


def test_intent_medication_generic():
    assert parse_intent("医生给我开的药怎么吃")[0] == INTENT_MEDICATION


def test_intent_symptom_with_keyword():
    intent, entity = parse_intent("我不舒服，胸痛，不知道挂什么科")
    assert intent == INTENT_SYMPTOM
    assert entity == "胸痛"


def test_intent_symptom_wins_over_disease_when_asking_dept():
    """「感冒了该挂什么科」必须归 symptom（挂科信号优先于疾病词）。"""
    intent, entity = parse_intent("感冒了该挂什么科")
    assert intent == INTENT_SYMPTOM
    assert entity == "感冒"


def test_intent_disease():
    intent, entity = parse_intent("我想了解糖尿病")
    assert intent == INTENT_DISEASE
    assert entity in ("糖尿病", "2型糖尿病")


def test_intent_fallback():
    assert parse_intent("今天天气怎么样")[0] == INTENT_FALLBACK


# ── 数值分档 ───────────────────────────────────────────────────

def test_interpret_lab_normal():
    hit = interpret_lab("血糖", "5.2")
    assert hit["label"] == "正常"


def test_interpret_lab_elevated():
    hit = interpret_lab("血糖", "6.3")
    assert "偏高" in hit["label"]


def test_interpret_lab_low():
    hit = interpret_lab("血糖", "3.2")
    assert hit["label"] == "偏低"


def test_interpret_unknown_test_returns_none():
    assert interpret_lab("不存在的检验项", "1") is None


# ── ask：三块结构 ──────────────────────────────────────────────

def test_ask_lab_returns_three_blocks(demo_db):
    r = smart_doctor.ask("我的血糖结果正常吗？接下来怎么办？", "P001")
    assert r["intent"] == INTENT_LAB
    assert r["admission"]["passed"] is True
    # 块一：院内数据（来源：医院主库）
    assert r["data"]["source"] == "医院主库"
    assert r["data"]["columns"] == ["test_name", "result_value", "unit", "visit_date"]
    assert len(r["data"]["rows"]) >= 1
    assert "patient_id" not in r["data"]["sql_after"] or \
        "P001" in r["data"]["sql_after"]
    # 块二：通俗解读（来源：医院审核知识库）
    assert r["interpretation"]["source"] == "医院审核知识库"
    assert r["interpretation"]["items"][0]["test_name"] == "血糖"
    assert r["interpretation"]["items"][0]["label"]  # 分档文案非空
    # 块三：下一步建议
    assert r["advice"]["source"] == "医院审核知识库"
    assert isinstance(r["advice"]["actions"], list)


def test_ask_medication_returns_personal_data(demo_db):
    r = smart_doctor.ask("医生给我开的药怎么吃", "P001")
    assert r["intent"] == INTENT_MEDICATION
    assert r["admission"]["passed"] is True
    assert len(r["data"]["rows"]) >= 1
    item = r["interpretation"]["items"][0]
    assert item["drug_name"]
    assert item["purpose"] or item["usage"]  # 知识库说明存在


def test_ask_symptom_no_personal_data_required(demo_db):
    r = smart_doctor.ask("我不舒服，胸痛，不知道挂什么科", "P001")
    assert r["intent"] == INTENT_SYMPTOM
    assert r["data"] is None  # 不涉及院内数据
    assert "心血管内科" in r["advice"]["text"]


def test_ask_disease_knowledge_only(demo_db):
    r = smart_doctor.ask("我想了解糖尿病", "P001")
    assert r["intent"] == INTENT_DISEASE
    assert r["data"] is None
    assert "饮食" in r["advice"]["text"]


def test_ask_fallback_guides_user(demo_db):
    r = smart_doctor.ask("今天天气怎么样", "P001")
    assert r["intent"] == INTENT_FALLBACK
    assert "换个问法" in r["interpretation"]["title"]


# ── 层一：只读本人 ─────────────────────────────────────────────

def test_patient_cannot_read_others_data_via_layer1(demo_db, monkeypatch):
    """关键安全用例：篡改计划把绑定换成其他患者 → 层一必须拒绝。

    直接调 fetch_personal_data 用别人 ID 的计划（模拟计划被人动手脚），
    断言返回的 admission 拒绝且没有执行。
    """
    evil_plan = [
        {"id": 0, "description": "读取他人检验结果（恶意构造）",
         "sql": "SELECT test_name, result_value FROM clinical_records "
                "WHERE patient_id = 'P002' AND record_type = 'lab'"},
    ]
    adm, deg, cols, rows, _ = smart_doctor.fetch_personal_data(evil_plan, "P001")
    assert adm["passed"] is False
    assert adm["reason"] == "该查询涉及其他患者信息，无法提供。"
    assert cols is None
    assert rows == []


def test_own_data_binding_passes_layer1(demo_db):
    plan = [
        {"id": 0, "description": "读取本人检验结果",
         "sql": "SELECT test_name, result_value FROM clinical_records "
                "WHERE patient_id = 'P001' AND record_type = 'lab'"},
    ]
    adm, deg, cols, rows, _ = smart_doctor.fetch_personal_data(plan, "P001")
    assert adm["passed"] is True
    assert cols == ["test_name", "result_value"]
    assert len(rows) >= 1


# ── 路由级 ─────────────────────────────────────────────────────

def _client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend import config
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "METADATA_DB", str(tmp_path / "medguard.db"))
    monkeypatch.setattr(config, "BUSINESS_DB", str(tmp_path / "regional_health.db"))
    from backend.db import init_db
    init_db(str(tmp_path / "medguard.db"))
    from backend.main import app
    c = TestClient(app)
    c.post("/api/datasources/demo")
    return c


def test_api_smart_doctor_ask(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.post("/api/smart-doctor/ask", json={
        "token": {"type": "patient", "subject_id": "P001"},
        "datasource_id": "regional_health",
        "question": "我的血糖结果正常吗？接下来怎么办？"})
    assert r.status_code == 200
    body = r.json()
    assert body["intent"] == "lab"
    assert body["interpretation"]["source"] == "医院审核知识库"
    assert body["data"]["source"] == "医院主库"
    assert len(body["data"]["rows"]) >= 1


def test_api_smart_doctor_rejects_staff_token(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.post("/api/smart-doctor/ask", json={
        "token": {"type": "staff", "subject_id": None},
        "datasource_id": "regional_health",
        "question": "我的血糖结果正常吗"})
    assert r.status_code == 403


def test_api_smart_doctor_requires_subject(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.post("/api/smart-doctor/ask", json={
        "token": {"type": "patient", "subject_id": None},
        "datasource_id": "regional_health",
        "question": "我的血糖结果正常吗"})
    assert r.status_code == 422


def test_api_smart_doctor_empty_question(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.post("/api/smart-doctor/ask", json={
        "token": {"type": "patient", "subject_id": "P001"},
        "datasource_id": "regional_health",
        "question": "   "})
    assert r.status_code == 422

# ── AI 兜底（知识库未覆盖的问法）──────────────────────────────

def test_ai_fallback_uses_llm_when_configured(demo_db, monkeypatch):
    """配置了 Key 且模型正常返回 → 回答来自 AI，来源标注「AI 智能导诊」。"""
    import json as _json
    from backend import config as _config
    monkeypatch.setattr(_config, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(
        smart_doctor, "_call_llm",
        lambda prompt: _json.dumps({
            "title": "拉肚子咨询",
            "text": "腹泻常见于肠道感染或饮食不当。",
            "advice": "症状轻可先补充水分与清淡饮食；持续超两天请挂消化内科。",
            "actions": ["补充水分", "清淡饮食", "持续不缓解就医"],
            "urgent": False,
        }, ensure_ascii=False),
    )
    r = smart_doctor.ask("我最近拉肚子，肚子疼怎么办", "P001")
    assert r["interpretation"]["source"] == "AI 智能导诊"
    assert "肠道感染" in r["interpretation"]["text"]
    assert r["advice"]["actions"] == ["补充水分", "清淡饮食", "持续不缓解就医"]
    assert r["advice"]["urgent"] is False


def test_ai_fallback_marks_urgent(demo_db, monkeypatch):
    from backend import config as _config
    monkeypatch.setattr(_config, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(
        smart_doctor, "_call_llm",
        lambda prompt: '{"title": "胸痛", "text": "剧烈胸痛需警惕心脏问题。", '
                       '"advice": "请立即就医。", "actions": ["立即急诊"], "urgent": true}',
    )
    r = smart_doctor.ask("我胸口突然很疼", "P001")
    assert r["interpretation"]["source"] == "AI 智能导诊"
    assert r["advice"]["urgent"] is True


def test_ai_fallback_without_key_keeps_guide(demo_db, monkeypatch):
    """未配置 Key → 不调模型，回退确定性的引导文案。"""
    from backend import config as _config
    monkeypatch.setattr(_config, "LLM_API_KEY", "")
    r = smart_doctor.ask("我最近拉肚子，肚子疼怎么办", "P001")
    assert r["interpretation"]["source"] == "医院审核知识库"
    assert "换个问法" in r["interpretation"]["title"]


def test_ai_call_failure_falls_back_to_guide(demo_db, monkeypatch):
    """模型调用抛异常 → 回退引导，绝不 500。"""
    from backend import config as _config
    monkeypatch.setattr(_config, "LLM_API_KEY", "sk-test")

    def boom(_prompt):
        raise RuntimeError("network down")

    monkeypatch.setattr(smart_doctor, "_call_llm", boom)
    r = smart_doctor.ask("我最近拉肚子，肚子疼怎么办", "P001")
    assert r["interpretation"]["source"] == "医院审核知识库"


def test_ai_dirty_output_kept_as_text(demo_db, monkeypatch):
    """模型输出不是 JSON 但非空 → 把原样文本作为解读，不丢回答。"""
    from backend import config as _config
    monkeypatch.setattr(_config, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(smart_doctor, "_call_llm",
                        lambda p: "根据您的情况，建议先观察，症状加重请就医。")
    r = smart_doctor.ask("我膝盖疼怎么办", "P001")
    assert r["interpretation"]["source"] == "AI 智能导诊"
    assert "建议先观察" in r["interpretation"]["text"]


def test_ai_empty_output_falls_back_to_guide(demo_db, monkeypatch):
    """模型返回空字符串 → 视为失败，回退引导。"""
    from backend import config as _config
    monkeypatch.setattr(_config, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(smart_doctor, "_call_llm", lambda p: "")
    r = smart_doctor.ask("我膝盖疼怎么办", "P001")
    assert r["interpretation"]["source"] == "医院审核知识库"


def test_parse_llm_json_robustness():
    """JSON 提取：干净 / 带围栏 / 前后废话 / 无 JSON。"""
    assert smart_doctor._parse_llm_json('{"a": 1}') == {"a": 1}
    assert smart_doctor._parse_llm_json('```json\n{"a": {"b": 2}}\n```') == {"a": {"b": 2}}
    assert smart_doctor._parse_llm_json('前言 {"a": 1} 后语') == {"a": 1}
    assert smart_doctor._parse_llm_json('no json here') is None
    assert smart_doctor._parse_llm_json('{"a": [1, 2], "b": {"c": "x"}}') == {
        "a": [1, 2], "b": {"c": "x"}}

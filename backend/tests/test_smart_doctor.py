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


def test_ask_fallback_guides_user(demo_db, monkeypatch):
    """未识别的问法 → 确定性的引导文案，不依赖模型。

    必须显式清空 Key：这是全文件唯一走到 _apply_ai_fallback 却没打桩的
    用例。配好 .env 且装了 openai 的机器上它会真的调模型，title 由模型
    生成，断言随之失败——本机此前"通过"只是因为环境里没装 openai，
    _call_llm 抛 ModuleNotFoundError 被吞掉后瞬间回落。
    """
    from backend import config as _config
    monkeypatch.setattr(_config, "LLM_API_KEY", "")
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


# ── 回归：分档切点（闭区间重叠曾把切点让给上一档）────────────────

def test_lab_range_boundaries_go_to_the_higher_band():
    """3.9 归「正常」、7.0 归「显著偏高」。

    interpret_lab 是「闭区间 lo<=v<=hi + 首个命中即止」，相邻档位若在同一
    切点上都满足，切点会被上一档吃掉：3.9 判成「偏低」、7.0 判成「空腹
    血糖受损」——而 7.0 恰是空腹血糖的糖尿病诊断切点。
    """
    assert interpret_lab("血糖", "3.89")["label"] == "偏低"
    assert interpret_lab("血糖", "3.9")["label"] == "正常"
    assert interpret_lab("血糖", "6.99")["label"] == "偏高（空腹血糖受损）"
    assert interpret_lab("血糖", "7.0")["label"] == "显著偏高"


def test_lab_range_has_no_uncovered_normal_window():
    """血常规 3.5–10 原先没有任何档位，正常值落到「已出结果」的泛化兜底。"""
    assert interpret_lab("血常规", "3.5")["label"] == "白细胞偏低"
    assert interpret_lab("血常规", "5.0")["label"] == "正常"
    assert interpret_lab("血常规", "10")["label"] == "正常"
    assert interpret_lab("血常规", "10.1")["label"] == "白细胞偏高"


# ── 分诊等级（acuity）─────────────────────────────────────────

def test_every_kb_entry_declares_acuity():
    """知识库必须显式写出 acuity；代码里的默认值只是漏写兜底，不是设计。"""
    kb = smart_doctor.load_knowledge()
    for name, entry in (kb.get("lab_tests") or {}).items():
        for r in entry.get("ranges") or []:
            assert r.get("acuity") in (1, 2, 3, 4), f"{name} 的分档缺 acuity: {r}"
    for sym in kb.get("symptoms") or []:
        assert sym.get("acuity") in (1, 2, 3, 4), f"症状条目缺 acuity: {sym}"


def test_urgent_is_driven_by_acuity_not_by_urgent_text(demo_db):
    """原先只要条目写了 urgent_text 就弹急诊横幅，与本次数值无关。

    血糖 6.3 是锚点值（偏高但非急症），不该弹；但 urgent_text 属于
    safety-net 提示，必须无条件展示。
    """
    r = smart_doctor.ask("我的血糖结果正常吗", "P001")
    assert r["advice"]["urgent"] is False
    assert "请立即就医" in r["advice"]["text"]


def test_urgent_not_raised_when_no_record_found(demo_db):
    """查无记录时不该弹急诊横幅（原先会）。"""
    r = smart_doctor.ask("我的血糖结果正常吗", "P002")   # P002 无血糖记录
    assert r["interpretation"]["items"] == []
    assert r["interpretation"]["title"] == "未找到检验记录"
    assert r["advice"]["urgent"] is False


def test_urgent_raised_for_red_flag_symptoms(demo_db):
    """胸痛/便血是红旗信号（acuity 2）→ 弹横幅；普通症状不弹。"""
    assert smart_doctor.ask(
        "我不舒服，胸痛，不知道挂什么科", "P001")["advice"]["urgent"] is True
    assert smart_doctor.ask("我最近便血", "P001")["advice"]["urgent"] is True
    assert smart_doctor.ask("我头晕", "P001")["advice"]["urgent"] is False


# ── 回归：意图路由 ────────────────────────────────────────────

def test_intent_longest_key_wins_for_lab_substring(demo_db):
    """「高血脂」曾被子串「血脂」（lab_tests 的键）抢走 → 「未找到检验记录」。"""
    assert parse_intent("我想了解高血脂") == (INTENT_DISEASE, "高血脂")
    assert parse_intent("高血脂怎么办") == (INTENT_DISEASE, "高血脂")
    r = smart_doctor.ask("我想了解高血脂", "P001")
    assert r["intent"] == INTENT_DISEASE
    assert "高血脂" in r["interpretation"]["title"]


def test_ambiguous_symptom_routes_to_triage_with_guide(demo_db):
    """疾病关键词同时是症状词时 → 症状导诊，并给出转向疾病科普的引导。"""
    for q, dept, disease in [("我胃痛", "消化内科", "胃炎"),
                             ("我胃疼", "消化内科", "胃炎"),
                             ("我咳嗽", "呼吸内科", "支气管炎"),
                             ("我感冒了", "呼吸内科", "上呼吸道感染")]:
        r = smart_doctor.ask(q, "P001")
        assert r["intent"] == INTENT_SYMPTOM, q
        assert dept in r["advice"]["text"], q
        assert f"我想了解{disease}" in r["advice"]["text"], q
        # 疾病科普本身仍然可达，没有被歧义规则误伤
        assert smart_doctor.ask(
            f"我想了解{disease}", "P001")["intent"] == INTENT_DISEASE


def test_lab_query_covers_all_mentioned_tests(demo_db):
    """「我的血脂和血糖结果正常吗」原先只查血糖（YAML 顺序在前）。"""
    r = smart_doctor.ask("我的血脂和血糖结果正常吗", "P001")
    assert r["intent"] == INTENT_LAB
    assert "IN (" in r["data"]["sql_after"]
    assert "血糖" in r["data"]["sql_after"]
    assert "血脂" in r["data"]["sql_after"]


# ── 回归：AI 兜底的输出形态与 YAML 空值 ────────────────────────

def test_ai_actions_string_is_split_not_iterated(demo_db, monkeypatch):
    """模型把 actions 输出成字符串时，不能逐字符展开成单字条目。"""
    from backend import config as _config
    monkeypatch.setattr(_config, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(
        smart_doctor, "_call_llm",
        lambda p: '{"title":"t","text":"x","advice":"y",'
                  '"actions":"补充水分、清淡饮食","urgent":false}')
    r = smart_doctor.ask("我膝盖疼怎么办", "P001")
    assert r["advice"]["actions"] == ["补充水分", "清淡饮食"]


def test_ai_urgent_string_false_is_not_truthy(demo_db, monkeypatch):
    """bool("false") 是 True——模型把布尔输出成字符串时会误报急诊。"""
    from backend import config as _config
    monkeypatch.setattr(_config, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(
        smart_doctor, "_call_llm",
        lambda p: '{"title":"t","text":"x","advice":"y",'
                  '"actions":[],"urgent":"false"}')
    assert smart_doctor.ask("我膝盖疼怎么办", "P001")["advice"]["urgent"] is False


def test_ai_unparsable_output_escalates(demo_db, monkeypatch):
    """解析不了 = 判断不了紧急度 → 不静默当成"不急"。

    Schmitt-Thompson 与 ESI 的一致口径是 when in doubt, escalate；横幅文案
    本身是「请留意重症信号」，不是诊断结论。
    """
    from backend import config as _config
    monkeypatch.setattr(_config, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(smart_doctor, "_call_llm", lambda p: "这不是 JSON")
    r = smart_doctor.ask("我膝盖疼怎么办", "P001")
    assert r["advice"]["urgent"] is True


def test_parse_llm_json_skips_braces_inside_strings():
    """数括号时要跳过字符串字面量，否则合法 JSON 会被判失败。"""
    assert smart_doctor._parse_llm_json('{"t":"a}","u":1}') == {"t": "a}", "u": 1}
    assert smart_doctor._parse_llm_json('{"t":"血糖 {空腹","u":true}') == {
        "t": "血糖 {空腹", "u": True}


def test_kb_null_values_do_not_500(monkeypatch):
    """YAML 写了键但值为 null（"先留空待补"）不能把接口打成 500。"""
    kb = smart_doctor.load_knowledge()
    patched = {
        "lab_tests": kb.get("lab_tests") or {},
        "medications": kb.get("medications") or {},
        "symptoms": list(kb.get("symptoms") or []),
        "diseases": dict(kb.get("diseases") or {}),
    }
    patched["diseases"]["测试病"] = {"what": None, "advice": None,
                                     "keywords": ["测试病"]}
    monkeypatch.setattr(smart_doctor, "_KB", patched)
    r = smart_doctor.ask("我想了解测试病", "P001")
    assert r["interpretation"]["text"] == ""
    assert r["advice"]["text"] == ""
    assert r["advice"]["actions"] == []


def test_load_knowledge_failure_is_not_cached(monkeypatch):
    """一次瞬时读取失败不该把空知识库永久缓存下来。"""
    from backend import config as _config
    monkeypatch.setattr(smart_doctor, "_KB", None)
    real_dir = _config.DEMO_DIR
    monkeypatch.setattr(_config, "DEMO_DIR", "Z:/no/such/dir")
    assert smart_doctor.load_knowledge() == {}
    assert smart_doctor._KB is None          # 关键：没有被缓存
    monkeypatch.setattr(_config, "DEMO_DIR", real_dir)
    assert smart_doctor.load_knowledge().get("lab_tests")

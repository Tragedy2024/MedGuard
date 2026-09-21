"""查询路由测试。"""
import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDGUARD_DATA_DIR", str(tmp_path))
    from backend import config
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "METADATA_DB", str(tmp_path / "medguard.db"))
    monkeypatch.setattr(config, "BUSINESS_DB", str(tmp_path / "regional_health.db"))
    from backend.db import init_db
    init_db(str(tmp_path / "medguard.db"))
    c = TestClient(app)
    load_demo(c)
    return c


# 端点一律验签（见 tests/helpers.py），裸字典令牌会 401。
from tests.helpers import load_demo, scope, sign

STAFF = sign("staff")
PATIENT = sign("patient", "P001")


def test_patient_own_lab_is_allowed(client):
    r = client.post("/api/query", json={
        "token": PATIENT, "datasource_id": "regional_health",
        "question_id": "patient_my_lab"})
    assert r.status_code == 200
    body = r.json()
    assert body["admission"]["passed"] is True
    assert body["result"] is not None


def test_patient_querying_others_is_denied_before_execution(client):
    """层一拒绝：不审计、不执行、无结果。"""
    r = client.post("/api/query", json={
        "token": PATIENT, "datasource_id": "regional_health",
        "question_id": "patient_others_count"})
    body = r.json()
    assert body["admission"]["passed"] is False
    assert body["admission"]["reason"] == "该查询涉及其他患者信息，无法提供。"
    assert body["result"] is None
    assert body["plan"] == []
    assert body["events"] == []


def test_patient_can_query_doctors(client):
    """不涉及患者表的查询放行。"""
    r = client.post("/api/query", json={
        "token": PATIENT, "datasource_id": "regional_health",
        "question_id": "patient_doctors"})
    body = r.json()
    assert body["admission"]["passed"] is True
    assert body["result"] is not None


def test_patient_token_cannot_use_doctor_query(client):
    """令牌类型与查询不匹配时拒绝。"""
    r = client.post("/api/query", json={
        "token": PATIENT, "datasource_id": "regional_health",
        "question_id": "doctor_dept_visits"})
    assert r.status_code == 403


def test_staff_query_returns_events_when_intermediate_exposes(client):
    r = client.post("/api/query", json={
        "token": STAFF, "datasource_id": "regional_health",
        "question_id": "doctor_diabetes_cost"})
    body = r.json()
    assert body["admission"]["passed"] is True
    assert len(body["events"]) >= 1
    ev = body["events"][0]
    assert ev["type_label"] and ev["severity_label"]
    assert ev["column"]


def test_blocked_column_query_degrades(client):
    r = client.post("/api/query", json={
        "token": STAFF, "datasource_id": "regional_health",
        "question_id": "doctor_export_roster"})
    body = r.json()
    assert len(body["events"]) >= 1
    assert body["degradation"]["level"] in ("L2", "L3")


def test_metrics_count_audit_only_not_execution(client):
    """metrics 报的是审计开销，不是查询执行开销。

    查询确实执行了（result 非空），但 db_access 仍为 0——因为
    审计阶段零查库。零 LLM、零查库是安全声明，必须可验证。
    """
    body = client.post("/api/query", json={
        "token": STAFF, "datasource_id": "regional_health",
        "question_id": "doctor_dept_visits"}).json()
    assert body["result"] is not None, "查询应已执行"
    m = body["metrics"]
    assert m["llm_calls"] == 0
    assert m["db_access"] == 0
    assert m["elapsed_ms"] >= 0


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    """把问答缓存指向临时文件。

    必须隔离：QUERY_CACHE_FILE 默认指向 demo/query_cache.json，那是演示要
    预热的真文件——测试往里写会污染演示内容。
    """
    from backend import config
    path = str(tmp_path / "query_cache.json")
    monkeypatch.setattr(config, "QUERY_CACHE_FILE", path)
    return path


def test_direct_rejects_empty_question(client, isolated_cache):
    r = client.post("/api/query/direct", json={
        "token": STAFF, "datasource_id": "regional_health", "question": "   "})
    assert r.status_code == 422


def test_direct_cache_hit_never_calls_llm(client, isolated_cache, monkeypatch):
    """命中缓存必须完全离线。

    这是演示能稳的关键：预热过的问法要毫秒级返回，且不依赖网络。
    这里把 decompose 换成"一调就炸"，用来证明它根本没被碰。
    """
    from backend import query_cache, llm_nl2sql

    def _boom(*_a, **_k):
        raise AssertionError("命中缓存时不应调用模型")

    monkeypatch.setattr(llm_nl2sql, "decompose", _boom)

    query_cache.store(
        "我上次的血糖是多少",
        [{"id": 0, "description": "最终：我的血糖结果",
          "sql": "SELECT test_name, result_value, unit FROM clinical_records "
                 "WHERE patient_id = 'P001' AND record_type = 'lab' "
                 "AND test_name = '血糖'"}],
        "patient", "P001",
    )

    r = client.post("/api/query/direct", json={
        "token": PATIENT, "datasource_id": "regional_health",
        "question": "我上次的血糖是多少"})
    assert r.status_code == 200
    body = r.json()
    assert body["admission"]["passed"] is True
    # 指标条只报审计开销——翻译走缓存，审计仍零 LLM
    assert body["metrics"]["llm_calls"] == 0
    assert body["metrics"]["db_access"] == 0


def test_direct_cache_hit_still_runs_layer1(client, isolated_cache):
    """命中缓存**不等于放行**——层一准入照常拦截。

    这条是「缓存的是计划不是答案」的可验证形式：缓存若绕过审计，
    安全演示就没有意义了。
    """
    from backend import query_cache

    query_cache.store(
        "得这个病的有多少人",
        [{"id": 0, "description": "统计某诊断的患者数",
          "sql": "SELECT COUNT(*) FROM patients WHERE patient_id IN "
                 "(SELECT patient_id FROM clinical_records "
                 "WHERE diagnosis_name = '2型糖尿病')"}],
        "patient", "P001",
    )

    r = client.post("/api/query/direct", json={
        "token": PATIENT, "datasource_id": "regional_health",
        "question": "得这个病的有多少人"})
    assert r.status_code == 200
    body = r.json()
    assert body["admission"]["passed"] is False
    assert body["plan"] == []
    assert body["result"] is None
    assert body["degradation"]["level"] == "L3"


def test_direct_rejects_plan_with_parameter_placeholder(
        client, isolated_cache, monkeypatch):
    """模型偶尔写出 :patient_id 这类参数占位符——层一必须拦住。

    实测真实出现过：模型不知道本站用字面量绑定本人，写了 sqlite 命名参数。
    那种 SQL 既执行不了，更要命的是**等于没有绑定本人**。层一只认字面量
    （admission.py 里 isinstance(val, exp.Literal) 那一行），故会被拒。
    这是可执行的安全回归测试，不是理论担忧。
    """
    from backend import llm_nl2sql
    monkeypatch.setattr(llm_nl2sql, "llm_available", lambda: True)
    monkeypatch.setattr(llm_nl2sql, "decompose", lambda *_a, **_k: [{
        "id": 0, "description": "参数化查询",
        "sql": "SELECT result_value FROM clinical_records "
               "WHERE patient_id = :patient_id"}])

    r = client.post("/api/query/direct", json={
        "token": PATIENT, "datasource_id": "regional_health",
        "question": "我上次的血糖是多少"})
    assert r.status_code == 200
    body = r.json()
    assert body["admission"]["passed"] is False
    assert body["result"] is None
    assert body["degradation"]["level"] == "L3"


def test_direct_accepts_plan_with_literal_binding(
        client, isolated_cache, monkeypatch):
    """用字面量绑定本人则应放行——否则上面那条测试可能只是"永远拒绝"。"""
    from backend import llm_nl2sql
    monkeypatch.setattr(llm_nl2sql, "llm_available", lambda: True)
    monkeypatch.setattr(llm_nl2sql, "decompose", lambda *_a, **_k: [{
        "id": 0, "description": "本人血糖",
        "sql": "SELECT result_value FROM clinical_records "
               "WHERE patient_id = 'P001' AND test_name = '血糖'"}])

    r = client.post("/api/query/direct", json={
        "token": PATIENT, "datasource_id": "regional_health",
        "question": "我上次的血糖是多少"})
    body = r.json()
    assert body["admission"]["passed"] is True
    assert body["admission"]["bound_to_subject"] is True


def test_binding_hint_gives_identity_for_both_token_types():
    """身份约束必须带上主体编号——否则模型写不出「我」是谁。

    实测两次踩同一个坑：
      - 病患：模型写 patient_id = :patient_id，层一拒（兜底生效）
      - 医护：模型写 doctor_id = :doctor_id，SQL 解析不了，整条查询失败
    根因相同：系统没告诉模型「我是谁」。
    """
    from backend.llm_nl2sql import _binding_hint

    p = _binding_hint("patient", "P001")
    assert "P001" in p and "patient_id" in p

    s = _binding_hint("staff", "S001")
    assert "S001" in s and "doctor_id" in s

    assert _binding_hint("staff", "") == ""
    assert _binding_hint("patient", "") == ""
    assert _binding_hint("admin", "X") == ""


def test_staff_identity_reaches_decompose(client, isolated_cache, monkeypatch):
    """医护令牌的工号要一路传到 decompose，不能在中途丢掉。"""
    from backend import llm_nl2sql
    seen = {}

    def fake(question, datasource_id, token_type="staff", subject_id=""):
        seen["token_type"] = token_type
        seen["subject_id"] = subject_id
        return [{"id": 0, "description": "计数",
                 "sql": "SELECT COUNT(DISTINCT patient_id) FROM visits "
                        "WHERE doctor_id = 'S001'"}]

    monkeypatch.setattr(llm_nl2sql, "llm_available", lambda: True)
    monkeypatch.setattr(llm_nl2sql, "decompose", fake)

    r = client.post("/api/query/direct", json={
        "token": sign("staff", "S001"),
        "datasource_id": "regional_health", "question": "我治疗了多少患者"})
    assert r.status_code == 200
    assert seen == {"token_type": "staff", "subject_id": "S001"}
    assert r.json()["admission"]["passed"] is True


def test_unparseable_plan_degrades_to_L3_not_500(
        client, isolated_cache, monkeypatch):
    """模型偶尔生成解析不了的"SQL"——实测出现过只有一行注释的。

    真实案例：问「我治疗了哪些患者」，模型给出
        sql = "-- 过滤出当前医生，直接使用 :doctor_id"
    sqlglot 解析不出任何表达式，算法层抛 AuditParseError。算法自己的语义是
    「解析失败 → L3 + 空计划」，所以产品层要把异常转成那个形状。

    这里钉住三件事：不是 500、是 L3、**计划为空**（空计划保证被拒的 SQL
    不会被下游误执行）。
    """
    from backend import llm_nl2sql
    monkeypatch.setattr(llm_nl2sql, "llm_available", lambda: True)
    monkeypatch.setattr(llm_nl2sql, "decompose", lambda *_a, **_k: [{
        "id": 0, "description": "只有注释的子查询",
        "sql": "-- 过滤出当前医生，直接使用 :doctor_id"}])

    r = client.post("/api/query/direct", json={
        "token": STAFF, "datasource_id": "regional_health",
        "question": "我治疗了哪些患者"})
    assert r.status_code == 200, "不该是 500"
    body = r.json()
    assert body["degradation"]["level"] == "L3"
    assert body["plan"] == []
    assert body["result"] is None
    # 文案要说清是「处理不了」，不能复用 L3 默认那句「涉及其他患者信息」——
    # 那会让用户以为自己触发了隐私规则而反复换问法试探。
    assert "无法处理" in body["degradation"]["message_cn"]
    assert "其他患者" not in body["degradation"]["message_cn"]


def test_direct_uncached_without_key_gives_actionable_503(
        client, isolated_cache, monkeypatch):
    """未收录且无模型 → 明确说明原因，而不是 500 或假装成功。"""
    from backend import llm_nl2sql
    monkeypatch.setattr(llm_nl2sql, "llm_available", lambda: False)

    r = client.post("/api/query/direct", json={
        "token": STAFF, "datasource_id": "regional_health",
        "question": "一个绝对没有收录过的问法"})
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert "暂未收录" in detail
    # **不给非开发者看配置项名**：这段话是给医护和病患看的，
    # `OPENAI_API_KEY` 只有开发者认识（PRODUCT.md 硬约束③：界面上只用
    # 中文业务语言）。精确原因进服务端日志。
    assert "OPENAI_API_KEY" not in detail


def test_unknown_question_id_404(client):
    r = client.post("/api/query", json={
        "token": STAFF, "datasource_id": "regional_health",
        "question_id": "no_such_question"})
    assert r.status_code == 404
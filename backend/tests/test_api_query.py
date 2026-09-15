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
    c.post("/api/datasources/demo")
    return c


STAFF = {"type": "staff", "subject_id": None}
PATIENT = {"type": "patient", "subject_id": "P001"}


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


def test_direct_endpoint_is_reserved_not_implemented(client):
    """端到端模式预留接口：契约已定义，功能待接线。"""
    r = client.post("/api/query/direct", json={
        "token": PATIENT, "datasource_id": "regional_health",
        "question": "我上次的血糖是多少"})
    assert r.status_code == 501


def test_unknown_question_id_404(client):
    r = client.post("/api/query", json={
        "token": STAFF, "datasource_id": "regional_health",
        "question_id": "no_such_question"})
    assert r.status_code == 404
"""报告路由测试。"""
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


def test_detection_metrics_match_paper_repo(client):
    """效能数字必须来自论文仓库实测，不得杜撰。"""
    r = client.get("/api/metrics/detection")
    assert r.status_code == 200
    m = r.json()
    assert m["precision"]["value"] == 1.0
    assert m["recall"]["value"] == 1.0
    assert "rq3" in m["source"]


def test_reports_list_after_query(client):
    client.post("/api/query", json={
        "token": {"type": "staff", "subject_id": None},
        "datasource_id": "regional_health",
        "question_id": "doctor_dept_visits"})
    r = client.get("/api/reports")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) >= 1
    assert rows[0]["question"] == "统计各科室接诊量"


def test_report_detail_has_full_payload(client):
    client.post("/api/query", json={
        "token": {"type": "staff", "subject_id": None},
        "datasource_id": "regional_health",
        "question_id": "doctor_dept_visits"})
    rid = client.get("/api/reports").json()[0]["id"]
    r = client.get(f"/api/reports/{rid}")
    assert r.status_code == 200
    assert "admission" in r.json()["payload"]


def test_export_returns_attachment(client):
    client.post("/api/query", json={
        "token": {"type": "staff", "subject_id": None},
        "datasource_id": "regional_health",
        "question_id": "doctor_dept_visits"})
    rid = client.get("/api/reports").json()[0]["id"]
    r = client.get(f"/api/reports/{rid}/export")
    assert r.status_code == 200
    assert "attachment" in r.headers.get("content-disposition", "")


def test_reports_empty_without_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDGUARD_DATA_DIR", str(tmp_path))
    from backend import config
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "METADATA_DB", str(tmp_path / "medguard.db"))
    monkeypatch.setattr(config, "BUSINESS_DB", str(tmp_path / "regional_health.db"))
    r = TestClient(app).get("/api/reports")
    assert r.status_code == 200
    assert r.json() == []
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


def test_report_timestamps_are_marked_utc(client):
    """created_at 必须带明确的时区标记。

    SQLite 的 `datetime('now')` 是 UTC 且**无标记**，一串 `2026-09-15 06:56:27`
    是歧义的——前端只能猜，猜错就是 8 小时偏差（实测界面显示 06:56，实际 14:56）。
    这里钉住"标记必须存在"：展示成北京时间是前端的事，但数据的时区必须无歧义。
    """
    client.post("/api/query", json={
        "token": {"type": "staff", "subject_id": None},
        "datasource_id": "regional_health",
        "question_id": "doctor_dept_visits"})
    rows = client.get("/api/reports").json()
    assert rows, "应有记录"
    ts = rows[0]["created_at"]
    assert "T" in ts and ts.endswith("Z"), f"时间戳未标 UTC：{ts}"


def test_report_detail_timestamp_also_marked_utc(client):
    """详情走的是另一个查询，别只顾列表那条。"""
    client.post("/api/query", json={
        "token": {"type": "staff", "subject_id": None},
        "datasource_id": "regional_health",
        "question_id": "doctor_dept_visits"})
    rid = client.get("/api/reports").json()[0]["id"]
    ts = client.get(f"/api/reports/{rid}").json()["created_at"]
    assert "T" in ts and ts.endswith("Z"), f"详情时间戳未标 UTC：{ts}"


def test_reports_empty_without_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDGUARD_DATA_DIR", str(tmp_path))
    from backend import config
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "METADATA_DB", str(tmp_path / "medguard.db"))
    monkeypatch.setattr(config, "BUSINESS_DB", str(tmp_path / "regional_health.db"))
    r = TestClient(app).get("/api/reports")
    assert r.status_code == 200
    assert r.json() == []
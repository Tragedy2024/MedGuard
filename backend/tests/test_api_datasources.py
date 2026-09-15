"""数据源路由测试。"""
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
    return TestClient(app)


def test_list_datasources_empty_initially(client):
    r = client.get("/api/datasources")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert r.json() == []


def test_create_demo_datasource(client):
    r = client.post("/api/datasources/demo")
    assert r.status_code == 200
    assert r.json()["id"] == "regional_health"


def test_schema_lists_five_tables(client):
    client.post("/api/datasources/demo")
    r = client.get("/api/datasources/regional_health/schema")
    assert r.status_code == 200
    names = {t["name"] for t in r.json()["tables"]}
    assert {"patients", "staff", "visits", "clinical_records", "billing"} <= names


def test_schema_before_demo_is_404(client):
    r = client.get("/api/datasources/regional_health/schema")
    assert r.status_code == 404


def test_list_datasources_after_demo(client):
    client.post("/api/datasources/demo")
    r = client.get("/api/datasources")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["table_count"] == 5
    assert data[0]["policy_ready"] is True
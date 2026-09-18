"""数据源路由测试。"""
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from tests.helpers import load_demo, sign


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDGUARD_DATA_DIR", str(tmp_path))
    from backend import config
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "METADATA_DB", str(tmp_path / "medguard.db"))
    monkeypatch.setattr(config, "BUSINESS_DB", str(tmp_path / "regional_health.db"))
    from backend.db import init_db
    init_db(str(tmp_path / "medguard.db"))
    return TestClient(app)


def test_list_datasources_empty_initially(client):
    r = client.get("/api/datasources")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert r.json() == []


def test_create_demo_datasource_requires_admin(client):
    """载入演示数据会**先删库再重建**，所以必须管理员——不能给匿名调用方
    一个数据销毁按钮。"""
    r = client.post("/api/datasources/demo")
    assert r.status_code == 422, "缺少令牌应当被拒"

    pat = client.post("/api/auth/login",
                      json={"account": "patient", "password": "medguard"}).json()["token"]
    assert client.post("/api/datasources/demo",
                       json={"token": pat}).status_code == 403

    load_demo(client)  # 管理员应当成功（load_demo 内部断言 200）


def test_create_demo_datasource(client):
    load_demo(client)
    r = client.get("/api/datasources")
    assert r.json()[0]["id"] == "regional_health"


def test_schema_lists_five_tables(client):
    load_demo(client)
    r = client.get("/api/datasources/regional_health/schema")
    assert r.status_code == 200
    names = {t["name"] for t in r.json()["tables"]}
    assert {"patients", "staff", "visits", "clinical_records", "billing"} <= names


def test_schema_before_demo_is_404(client):
    r = client.get("/api/datasources/regional_health/schema")
    assert r.status_code == 404


def test_list_datasources_after_demo(client):
    load_demo(client)
    r = client.get("/api/datasources")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["table_count"] == 5
    assert data[0]["policy_ready"] is True
"""安全策略路由测试。"""
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
    client = TestClient(app)
    client.post("/api/datasources/demo")
    return client


def test_get_policy_returns_labels(client):
    r = client.get("/api/policies/regional_health")
    assert r.status_code == 200
    data = r.json()
    assert data["column_labels"]["patients"]["id_card"] == "blocked"
    assert data["column_labels"]["patients"]["gender"] == "free"


def test_get_policy_returns_cross_domain_rules(client):
    r = client.get("/api/policies/regional_health")
    rules = r.json()["cross_domain_rules"]
    assert len(rules) >= 1
    assert set(rules[0]["table_pair"]) == {"clinical_records", "billing"}


def test_get_policy_returns_column_reasons(client):
    """逐列标注理由是产品层字段——算法层不读，但界面要展示。"""
    r = client.get("/api/policies/regional_health")
    reasons = r.json()["column_reasons"]
    assert reasons["patients"]["id_card"]
    assert reasons["billing"]["amount"]


def test_all_demo_columns_are_labeled(client):
    """演示库的每一列都必须有 ECL 标注。

    这不是洁癖：SSALabels.get() 对未标注的列返回 FREE（fail-open），
    漏标一列 = 医盾静默放行该列。此测试守住演示库不出现这种缺口。
    """
    r = client.get("/api/policies/regional_health")
    status = r.json()["review_status"]
    assert status, "review_status 为空——演示库未载入"
    unlabeled = [
        f"{t}.{c}"
        for t, cols in status.items()
        for c, s in cols.items() if s == "unlabeled"
    ]
    assert unlabeled == [], f"以下列未标注（将被静默放行）：{unlabeled}"


def test_put_policy_updates_label(client):
    r = client.put("/api/policies/regional_health", json={
        "column_labels": {"patients": {"gender": "controlled"}},
    })
    assert r.status_code == 200
    assert r.json()["saved"] is True
    # 回读确认
    after = client.get("/api/policies/regional_health").json()
    assert after["column_labels"]["patients"]["gender"] == "controlled"

    # 复原，避免影响其他测试
    client.put("/api/policies/regional_health", json={
        "column_labels": {"patients": {"gender": "free"}},
    })


def test_policy_validate_ok(client):
    r = client.get("/api/policies/regional_health/validate")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["cross_domain_rules"] >= 1


def test_unknown_policy_is_404(client):
    r = client.get("/api/policies/no_such_datasource")
    assert r.status_code == 404
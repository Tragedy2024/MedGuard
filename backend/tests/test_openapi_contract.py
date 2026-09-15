"""契约完整性测试。

**为什么需要这个文件**：后端 15 个端点里曾有 13 个没声明 `response_model`，
于是 openapi.json 只覆盖了 2 个端点——前端 Task 10 用 openapi-typescript
生成的契约类型因此大面积缺失（`Policy` / `ReportSummary` /
`DetectionMetrics` / `SchemaTable` 全都取不到）。

教训是：**模型存在 ≠ 契约存在，接线才算。** `ReportSummary` 当时明明
已经定义在 schemas.py 里，只是没挂到路由上，就等同于不存在。

本文件把「每个端点都必须声明响应模型」变成一条会失败的测试，
防止新增端点时再次漏掉。
"""
import pytest
from fastapi.testclient import TestClient

from backend.main import app

# 合法例外：200 响应不是普通 JSON 对象，无法用 response_model 表达。
_NO_MODEL_OK = {
    # 原始文件下载（Content-Disposition: attachment），由 Response 直接构造
    ("GET", "/api/reports/{report_id}/export"),
    # 端到端模式预留接口，恒返回 501，没有成功响应可建模
    ("POST", "/api/query/direct"),
}


def _api_operations():
    """枚举 openapi 中所有 /api 端点。"""
    spec = app.openapi()
    for path, ops in spec["paths"].items():
        if not path.startswith("/api"):
            continue
        for method, op in ops.items():
            if method in ("get", "post", "put", "delete", "patch"):
                yield method.upper(), path, op


def _response_schema_names(op):
    """取 200 响应引用的 schema 名（同时支持对象与数组）。"""
    content = (op.get("responses", {}).get("200", {})
               .get("content", {}).get("application/json", {}))
    schema = content.get("schema", {})
    if "$ref" in schema:
        return {schema["$ref"].rsplit("/", 1)[-1]}
    if "$ref" in schema.get("items", {}):
        return {schema["items"]["$ref"].rsplit("/", 1)[-1]}
    return set()


def test_every_api_endpoint_declares_response_model():
    """每个有 200 响应的 /api 端点都必须声明响应模型。"""
    missing = []
    for method, path, op in _api_operations():
        if "200" not in op.get("responses", {}):
            continue
        if (method, path) in _NO_MODEL_OK:
            continue
        if not _response_schema_names(op):
            missing.append(f"{method} {path}")

    assert not missing, (
        "以下端点未声明 response_model，openapi.json 不含其类型，"
        "前端无法生成契约类型：\n  " + "\n  ".join(missing)
    )


# 前端 src/api/ 需要、且必须由 openapi 提供的类型
_FRONTEND_REQUIRED = {
    # 查询链路
    "Token", "AdmissionInfo", "PlanItem", "SecurityEvent", "RewriteInfo",
    "DegradationInfo", "MetricsInfo", "ResultSet", "QueryResponse",
    # 数据源
    "DatasourceInfo", "DatasourceSchema", "SchemaTable", "SchemaColumn",
    "DemoDatasourceResult",
    # 策略
    "Policy", "CrossDomainRule", "PolicyList", "PolicyUpdateResult",
    "PolicyValidation",
    # 报告与效能
    "ReportSummary", "ReportDetail", "DetectionMetrics", "MetricValue",
    # 健康检查
    "HealthInfo",
}


def test_openapi_declares_all_frontend_required_schemas():
    """openapi 必须包含前端要用的每一个 schema 名。"""
    schemas = set(app.openapi().get("components", {}).get("schemas", {}))
    missing = _FRONTEND_REQUIRED - schemas
    assert not missing, f"openapi 缺少前端需要的 schema：{sorted(missing)}"


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


def test_policy_response_keeps_all_fields(client):
    """response_model 会把响应裁剪到模型字段——漏一个字段就是静默丢数据。

    `column_reasons` 与 `review_status` 由路由计算得出而非 YAML 直读，
    最容易在建模型时漏掉，故单独锚定。
    """
    r = client.get("/api/policies/regional_health")
    assert r.status_code == 200
    body = r.json()
    for key in ("datasource_id", "column_labels", "column_reasons",
                "cross_domain_rules", "review_status"):
        assert key in body, f"Policy 响应丢了字段：{key}"
    assert body["column_reasons"], "column_reasons 不应为空（YAML 中有标注理由）"
    assert body["review_status"], "review_status 不应为空"
    assert body["cross_domain_rules"], "cross_domain_rules 不应为空"


def test_report_detail_keeps_payload(client):
    """ReportDetail 继承 ReportSummary，payload 是新增字段，不能被裁掉。"""
    client.post("/api/query", json={
        "token": {"type": "staff", "subject_id": None},
        "datasource_id": "regional_health",
        "question_id": "doctor_dept_visits"})
    rid = client.get("/api/reports").json()[0]["id"]
    body = client.get(f"/api/reports/{rid}").json()
    for key in ("id", "question", "token_type", "created_at",
                "degradation_level", "event_count", "payload"):
        assert key in body, f"ReportDetail 响应丢了字段：{key}"
    assert "admission" in body["payload"]

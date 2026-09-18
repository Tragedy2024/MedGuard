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
    load_demo(c)
    return c


# 报告按令牌身份隔离，读接口必须带上发起人的身份（且必须签名有效）。
# 演示里管理员的令牌是 (staff, None)——没有绑定工号。
from tests.helpers import load_demo, scope, sign

_STAFF = scope(sign("staff"))


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
        "token": sign("staff"),
        "datasource_id": "regional_health",
        "question_id": "doctor_dept_visits"})
    r = client.get("/api/reports", params=_STAFF)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) >= 1
    assert rows[0]["question"] == "统计各科室接诊量"


def test_report_detail_has_full_payload(client):
    client.post("/api/query", json={
        "token": sign("staff"),
        "datasource_id": "regional_health",
        "question_id": "doctor_dept_visits"})
    rid = client.get("/api/reports", params=_STAFF).json()[0]["id"]
    r = client.get(f"/api/reports/{rid}", params=_STAFF)
    assert r.status_code == 200
    assert "admission" in r.json()["payload"]


def test_export_returns_attachment(client):
    client.post("/api/query", json={
        "token": sign("staff"),
        "datasource_id": "regional_health",
        "question_id": "doctor_dept_visits"})
    rid = client.get("/api/reports", params=_STAFF).json()[0]["id"]
    r = client.get(f"/api/reports/{rid}/export", params=_STAFF)
    assert r.status_code == 200
    assert "attachment" in r.headers.get("content-disposition", "")


def test_report_timestamps_are_marked_utc(client):
    """created_at 必须带明确的时区标记。

    SQLite 的 `datetime('now')` 是 UTC 且**无标记**，一串 `2026-09-15 06:56:27`
    是歧义的——前端只能猜，猜错就是 8 小时偏差（实测界面显示 06:56，实际 14:56）。
    这里钉住"标记必须存在"：展示成北京时间是前端的事，但数据的时区必须无歧义。
    """
    client.post("/api/query", json={
        "token": sign("staff"),
        "datasource_id": "regional_health",
        "question_id": "doctor_dept_visits"})
    rows = client.get("/api/reports", params=_STAFF).json()
    assert rows, "应有记录"
    ts = rows[0]["created_at"]
    assert "T" in ts and ts.endswith("Z"), f"时间戳未标 UTC：{ts}"


def test_report_detail_timestamp_also_marked_utc(client):
    """详情走的是另一个查询，别只顾列表那条。"""
    client.post("/api/query", json={
        "token": sign("staff"),
        "datasource_id": "regional_health",
        "question_id": "doctor_dept_visits"})
    rid = client.get("/api/reports", params=_STAFF).json()[0]["id"]
    ts = client.get(f"/api/reports/{rid}", params=_STAFF).json()["created_at"]
    assert "T" in ts and ts.endswith("Z"), f"详情时间戳未标 UTC：{ts}"


def test_reports_empty_without_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDGUARD_DATA_DIR", str(tmp_path))
    from backend import config
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "METADATA_DB", str(tmp_path / "medguard.db"))
    monkeypatch.setattr(config, "BUSINESS_DB", str(tmp_path / "regional_health.db"))
    r = TestClient(app).get("/api/reports", params=_STAFF)
    assert r.status_code == 200
    assert r.json() == []

# ── 报告必须按令牌身份隔离 ────────────────────────────────────

def _run(c, token_type, subject_id, question_id, account=None):
    """发起一次查询。**必须给账号**——报告现在按账号隔离，不给账号的令牌
    会全落进同一个 NULL 桶，隔离断言就没意义了。"""
    return c.post("/api/query", json={
        "token": sign(token_type, subject_id, account),
        "datasource_id": "regional_health",
        "question_id": question_id})


def test_reports_are_scoped_to_the_requesting_account(client):
    """患者不该看到医生发起的查询。

    报告库曾经只存 token_type（角色）、读取时又是无 WHERE 的全表倒序，
    于是全平台共享一份；后来又按 (token_type, subject_id) 隔离，但管理员
    的 subject_id 正好为空，会与"绑定主体留空"的 staff 撞桶。
    现在按**账号**隔离。
    """
    _run(client, "staff", "S001", "doctor_dept_visits", account="doctor")
    _run(client, "patient", "P001", "patient_my_lab", account="patient")

    staff = client.get("/api/reports", params=scope(
        sign("staff", "S001", "doctor"))).json()
    patient = client.get("/api/reports", params=scope(
        sign("patient", "P001", "patient"))).json()

    assert [r["question"] for r in staff] == ["统计各科室接诊量"]
    assert [r["question"] for r in patient] == ["我上次的血糖是多少"]


def test_staff_without_subject_does_not_see_the_admin_ledger(client):
    """回归：subject_id 留空的 staff 曾与管理员共用同一个桶。

    建号界面把「绑定主体」标成**选填**——所以那是默认路径，不是边角情况。
    实测当时新建的 nurse9 一登录就看到了管理员那条记录，详情与导出也都能读。
    """
    _run(client, "staff", None, "doctor_dept_visits", account="admin")
    _run(client, "staff", None, "doctor_dept_visits", account="nurse")

    admin_rows = client.get("/api/reports", params=scope(
        sign("staff", None, "admin"))).json()
    nurse_rows = client.get("/api/reports", params=scope(
        sign("staff", None, "nurse"))).json()

    # 两者 subject_id 都为空，但账号不同 → 必须各自只见自己那一条
    assert len(admin_rows) == 1 and len(nurse_rows) == 1
    assert admin_rows[0]["id"] != nurse_rows[0]["id"]


def test_report_detail_is_scoped_even_with_a_valid_id(client):
    """知道别人的报告 id 也读不到内容。"""
    _run(client, "staff", "S001", "doctor_dept_visits", account="doctor")
    rid = client.get("/api/reports", params=scope(
        sign("staff", "S001", "doctor"))).json()[0]["id"]

    own = client.get(f"/api/reports/{rid}", params=scope(
        sign("staff", "S001", "doctor")))
    other = client.get(f"/api/reports/{rid}", params=scope(
        sign("patient", "P001", "patient")))
    export = client.get(f"/api/reports/{rid}/export", params=scope(
        sign("patient", "P001", "patient")))

    assert own.status_code == 200
    assert other.status_code == 404
    assert export.status_code == 404

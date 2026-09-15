"""演示数据校验——每个预设查询必须按预期触发。

任何一个失败都是阻塞性缺陷：录视频时会「什么都没发生」。
"""
import pytest
from fastapi.testclient import TestClient

from backend.main import app

STAFF = {"type": "staff", "subject_id": None}
PATIENT = {"type": "patient", "subject_id": "P001"}

# 预期：question_id -> (令牌, 是否放行, 最少事件数, 期望的降级等级集合)
EXPECTED = [
    ("doctor_dept_visits",     STAFF,   True,  0, {"L0"}),
    ("doctor_diabetes_cost",   STAFF,   True,  1, {"L0", "L1", "L2"}),   # 跨域
    ("doctor_diagnosis_stats", STAFF,   True,  1, {"L0", "L1", "L2"}),   # 派生
    ("doctor_export_roster",   STAFF,   True,  1, {"L2", "L3"}),         # blocked
    ("patient_my_lab",         PATIENT, True,  0, {"L0"}),
    ("patient_my_medication",  PATIENT, True,  0, {"L0"}),
    ("patient_my_imaging",     PATIENT, True,  0, {"L0"}),
    ("patient_doctors",        PATIENT, True,  0, {"L0"}),
    ("patient_others_count",   PATIENT, False, 0, {"L3"}),               # 层一拒绝
]


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


@pytest.mark.parametrize("qid,token,passed,min_events,levels", EXPECTED,
                         ids=[e[0] for e in EXPECTED])
def test_demo_query_behaves_as_expected(client, qid, token, passed,
                                        min_events, levels):
    r = client.post("/api/query", json={
        "token": token, "datasource_id": "regional_health", "question_id": qid})
    assert r.status_code == 200, f"{qid}: HTTP {r.status_code}"
    body = r.json()
    assert body["admission"]["passed"] is passed, \
        f"{qid}: 准入判定不符（期望 {passed}）"
    assert len(body["events"]) >= min_events, \
        f"{qid}: 事件数 {len(body['events'])} < 期望 {min_events}"
    assert body["degradation"]["level"] in levels, \
        f"{qid}: 降级 {body['degradation']['level']} 不在 {levels}"


def test_all_patient_own_queries_return_data(client):
    """病患查本人数据必须真的有结果——空结果说明 seed 缺锚点数据。"""
    for qid in ("patient_my_lab", "patient_my_medication", "patient_my_imaging"):
        body = client.post("/api/query", json={
            "token": PATIENT, "datasource_id": "regional_health",
            "question_id": qid}).json()
        assert body["result"] is not None, f"{qid}: 无结果集"
        assert len(body["result"]["rows"]) > 0, \
            f"{qid}: 结果为空（seed 缺数据）"


def test_staff_queries_return_data_or_rewritten_answer(client):
    """医护查询（除 blocked 导出外）应返回可执行结果。"""
    for qid in ("doctor_dept_visits", "doctor_diabetes_cost",
                "doctor_diagnosis_stats"):
        body = client.post("/api/query", json={
            "token": STAFF, "datasource_id": "regional_health",
            "question_id": qid}).json()
        assert body["result"] is not None, f"{qid}: 无结果集"


def test_cross_domain_case_actually_splits_join(client):
    """跨域演示的核心：clinical_records↔billing 个人级 JOIN 被拦截。

    断言改写后计划中的子查询不再含 JOIN（Rule C 拆分为聚合查询）。
    """
    body = client.post("/api/query", json={
        "token": STAFF, "datasource_id": "regional_health",
        "question_id": "doctor_diabetes_cost"}).json()
    join_before = [sq for sq in body["plan"]
                   if "join" in sq["sql_before"].lower()]
    join_after = [sq for sq in body["plan"]
                  if "join" in sq["sql_after"].lower()]
    assert join_before, "预设查询本身应含跨域 JOIN（否则没演示什么）"
    assert join_after == [], \
        "跨域个人级 JOIN 未被改写：演示时「什么都没发生」"


def test_blocked_case_never_leaks_id_card(client):
    """blocked 演示的核心：id_card 绝不进入改写后计划与结果。

    sql_before 允许展示原始违规 SQL（diff 面板的「改写前」），
    但执行用的 sql_after 与结果集必须无 blocked 列。
    """
    body = client.post("/api/query", json={
        "token": STAFF, "datasource_id": "regional_health",
        "question_id": "doctor_export_roster"}).json()
    assert any("id_card" in sq["sql_before"].lower() for sq in body["plan"]), \
        "预设查询本身应含 blocked 列（否则没演示什么）"
    after_sql = " ".join(sq["sql_after"] for sq in body["plan"])
    assert "id_card" not in after_sql.lower(), "blocked 列泄漏进输出计划"
    if body["result"]:
        assert "id_card" not in body["result"]["columns"], \
            "blocked 列出现在结果集"
    # 该查询必须确实被标记为降级（L2/L3），而非静默放行
    assert body["degradation"]["level"] in ("L2", "L3")
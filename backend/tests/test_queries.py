"""预设查询库测试。"""
import json
import os

from backend import config


def _load():
    with open(config.QUERIES_FILE, encoding="utf-8") as f:
        return json.load(f)


def test_all_expected_queries_present():
    q = _load()
    assert set(q) == {
        "doctor_dept_visits", "doctor_diabetes_cost", "doctor_diagnosis_stats",
        "doctor_export_roster", "patient_my_lab", "patient_my_medication",
        "patient_my_imaging", "patient_doctors", "patient_others_count",
    }


def test_doctor_queries_tagged_staff():
    q = _load()
    for key in ("doctor_dept_visits", "doctor_diabetes_cost",
                "doctor_diagnosis_stats", "doctor_export_roster"):
        assert "staff" in q[key]["token_types"]


def test_patient_queries_tagged_patient():
    q = _load()
    for key in ("patient_my_lab", "patient_my_medication", "patient_my_imaging",
                "patient_doctors", "patient_others_count"):
        assert "patient" in q[key]["token_types"]


def test_every_plan_has_required_fields():
    q = _load()
    for key, item in q.items():
        assert item["plan"], f"{key} 计划为空"
        for sq in item["plan"]:
            assert {"id", "description", "sql"} <= set(sq), f"{key} 子查询字段不全"


def test_patient_own_queries_use_subject_placeholder():
    """病患查本人数据的查询必须带 {subject_id} 占位符，否则层一会拒绝。"""
    q = _load()
    for key in ("patient_my_lab", "patient_my_medication", "patient_my_imaging"):
        sqls = " ".join(sq["sql"] for sq in q[key]["plan"])
        assert "{subject_id}" in sqls, f"{key} 缺少 {subject_id} 占位符"


def test_final_subquery_is_executable_sqlite():
    """最终子查询必须是合法 SQLite（执行阶段直接运行，非法=演示扑空）。"""
    import sqlite3
    import tempfile

    from demo.seed import build_database

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    build_database(path)
    con = sqlite3.connect(path)
    q = _load()
    try:
        for key, item in q.items():
            final = item["plan"][-1]["sql"].replace("{subject_id}", "P001")
            cur = con.execute(final)
            cur.fetchall()
    except sqlite3.Error as exc:  # noqa: BLE001
        raise AssertionError(f"{key} 最终 SQL 无法执行: {exc}") from exc
    finally:
        con.close()
        os.unlink(path)
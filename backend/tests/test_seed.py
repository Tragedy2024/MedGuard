"""演示库建库测试。"""
import os
import sqlite3
import tempfile

from demo.seed import (ANCHOR_LAB_TEST, ANCHOR_PATIENT, CARDIOLOGY_DEPT,
                       DIABETES_DIAGNOSIS, build_database)


def _make_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    build_database(path)
    return path


def _connect(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def test_all_five_tables_created():
    path = _make_db()
    con = _connect(path)
    names = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    os.unlink(path)
    assert {"patients", "staff", "visits", "clinical_records", "billing"} <= names


def test_patient_ids_are_obviously_fake():
    """仿真数据必须一眼假。"""
    path = _make_db()
    con = _connect(path)
    ids = [r[0] for r in con.execute("SELECT patient_id FROM patients")]
    con.close()
    os.unlink(path)
    assert ids, "patients 表不应为空"
    assert all(i.startswith("P") for i in ids)


def test_names_are_obviously_fake():
    """姓名/手机号/身份证号必须一眼假。"""
    path = _make_db()
    con = _connect(path)
    names = [r[0] for r in con.execute("SELECT name FROM patients")]
    phones = [r[0] for r in con.execute("SELECT phone FROM patients")]
    id_cards = [r[0] for r in con.execute("SELECT id_card FROM patients")]
    con.close()
    os.unlink(path)
    assert all(n.startswith("患者") for n in names)
    assert all(p.startswith("TEST-PHONE-") for p in phones)
    assert all(i.startswith("TEST-") for i in id_cards)


def test_clinical_records_cover_four_types():
    """clinical_records 必须含诊断/用药/检验/影像四类记录。"""
    path = _make_db()
    con = _connect(path)
    types = {r[0] for r in con.execute(
        "SELECT DISTINCT record_type FROM clinical_records")}
    con.close()
    os.unlink(path)
    assert types == {"diagnosis", "medication", "lab", "imaging"}


def test_patient_id_present_on_all_clinical_tables():
    """层一准入依赖 patient_id 可直接出现在查询中（反规范化设计）。"""
    path = _make_db()
    con = _connect(path)
    for tbl in ("visits", "clinical_records", "billing"):
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({tbl})")}
        assert "patient_id" in cols, f"{tbl} 缺少 patient_id"
    con.close()
    os.unlink(path)


def test_anchor_patient_has_lab_medication_imaging():
    """P001 锚点：血糖 lab + medication + imaging 记录必须存在。"""
    path = _make_db()
    con = _connect(path)
    rows = con.execute(
        "SELECT record_type, test_name FROM clinical_records "
        "WHERE patient_id = ?", (ANCHOR_PATIENT,)).fetchall()
    con.close()
    os.unlink(path)
    by_type = {r["record_type"]: r["test_name"] for r in rows}
    assert "medication" in by_type
    assert "imaging" in by_type
    assert by_type.get("lab") == ANCHOR_LAB_TEST


def test_diabetes_anchors_exist():
    """至少 3 位患者诊断为 2 型糖尿病。"""
    path = _make_db()
    con = _connect(path)
    n = con.execute(
        "SELECT COUNT(DISTINCT patient_id) FROM clinical_records "
        "WHERE diagnosis_name = ?", (DIABETES_DIAGNOSIS,)).fetchone()[0]
    con.close()
    os.unlink(path)
    assert n >= 3


def test_cardiology_staff_exist():
    path = _make_db()
    con = _connect(path)
    n = con.execute(
        "SELECT COUNT(*) FROM staff WHERE department = ?",
        (CARDIOLOGY_DEPT,)).fetchone()[0]
    con.close()
    os.unlink(path)
    assert n >= 1


def test_billing_has_records():
    path = _make_db()
    con = _connect(path)
    n = con.execute("SELECT COUNT(*) FROM billing").fetchone()[0]
    con.close()
    os.unlink(path)
    assert n >= 20
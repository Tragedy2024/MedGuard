"""平台元数据库测试。"""
import os
import tempfile

from backend.db import get_report, init_db, list_reports, save_report


def _db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_db(path)
    return path


def test_init_creates_table():
    path = _db()
    assert os.path.exists(path)
    assert list_reports(path, account="doctor", limit=10) == []
    os.unlink(path)


def test_save_and_get():
    path = _db()
    rid = save_report(path, {
        "question": "统计各科室接诊量",
        "token_type": "staff",
        "account": "doctor",
        "degradation_level": "L0",
        "event_count": 2,
        "payload": {"admission": {"passed": True}},
    })
    assert rid > 0
    rec = get_report(path, rid, account="doctor")
    assert rec["question"] == "统计各科室接诊量"
    assert rec["payload"]["admission"]["passed"] is True
    os.unlink(path)


def test_list_returns_newest_first():
    path = _db()
    for q in ("问题一", "问题二", "问题三"):
        save_report(path, {"question": q, "token_type": "staff",
                           "account": "doctor",
                           "degradation_level": "L0", "event_count": 0,
                           "payload": {}})
    rows = list_reports(path, account="doctor", limit=10)
    assert len(rows) == 3
    assert rows[0]["question"] == "问题三"
    os.unlink(path)


def test_get_missing_returns_none():
    path = _db()
    assert get_report(path, 999, account="doctor") is None
    os.unlink(path)


# ── 按账号隔离 ────────────────────────────────────────────────

def _seed(path):
    """同一个库里放三条不同账号发起的报告。

    注意「管理员」与「医生」的 subject_id 分别是 None 和 S001，而
    **账号**不同——这正是关键：按 (token_type, subject_id) 隔离时，
    管理员会和"绑定主体留空"的 staff 撞进同一个桶。
    """
    save_report(path, {"question": "管理员查的", "token_type": "staff",
                       "subject_id": None, "account": "admin",
                       "degradation_level": "L0",
                       "event_count": 0, "payload": {}})
    save_report(path, {"question": "医生查的", "token_type": "staff",
                       "subject_id": "S001", "account": "doctor",
                       "degradation_level": "L0",
                       "event_count": 0, "payload": {}})
    save_report(path, {"question": "患者查的", "token_type": "patient",
                       "subject_id": "P001", "account": "patient",
                       "degradation_level": "L0",
                       "event_count": 0, "payload": {}})


def _q(path, account):
    return [r["question"] for r in list_reports(path, account=account)]


def test_list_reports_only_returns_the_accounts_own():
    """按账号隔离：管理员与工号不同的医护必须各自独立。"""
    path = _db()
    _seed(path)
    assert _q(path, "admin") == ["管理员查的"]
    assert _q(path, "doctor") == ["医生查的"]
    assert _q(path, "patient") == ["患者查的"]
    assert _q(path, "someone-else") == []
    os.unlink(path)


def test_staff_without_subject_does_not_share_the_admin_bucket():
    """回归：一个 subject_id 为空的 staff 账号曾与管理员共用同一个桶。

    建号界面上「绑定主体」是**选填**的，所以那是默认路径而不是边角情况。
    按账号隔离后，两者互不可见——即便 subject_id 都是空。
    """
    path = _db()
    _seed(path)
    save_report(path, {"question": "留空主体的护士查的", "token_type": "staff",
                       "subject_id": None, "account": "nurse",
                       "degradation_level": "L0",
                       "event_count": 0, "payload": {}})
    assert _q(path, "nurse") == ["留空主体的护士查的"]
    assert _q(path, "admin") == ["管理员查的"]
    os.unlink(path)


def test_get_report_rejects_another_accounts_record():
    """拿到别人的 id 也读不到。"""
    path = _db()
    _seed(path)
    rid = list_reports(path, account="doctor")[0]["id"]
    assert get_report(path, rid, account="doctor")
    assert get_report(path, rid, account="patient") is None
    assert get_report(path, rid, account="admin") is None
    os.unlink(path)


def test_init_db_migrates_an_existing_table(tmp_path):
    """老库（没有 subject_id 列）要能被原地补列，而不是只能删库重建。"""
    import sqlite3
    path = str(tmp_path / "old.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE reports ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT, question TEXT NOT NULL,"
                " token_type TEXT NOT NULL, degradation_level TEXT NOT NULL,"
                " event_count INTEGER NOT NULL DEFAULT 0,"
                " created_at TEXT NOT NULL DEFAULT (datetime('now')),"
                " payload TEXT NOT NULL)")
    con.commit()
    con.close()
    init_db(path)
    cols = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(reports)")}
    assert {"subject_id", "account"} <= cols
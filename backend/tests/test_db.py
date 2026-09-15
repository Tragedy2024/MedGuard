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
    assert list_reports(path, 10) == []
    os.unlink(path)


def test_save_and_get():
    path = _db()
    rid = save_report(path, {
        "question": "统计各科室接诊量",
        "token_type": "staff",
        "degradation_level": "L0",
        "event_count": 2,
        "payload": {"admission": {"passed": True}},
    })
    assert rid > 0
    rec = get_report(path, rid)
    assert rec["question"] == "统计各科室接诊量"
    assert rec["payload"]["admission"]["passed"] is True
    os.unlink(path)


def test_list_returns_newest_first():
    path = _db()
    for q in ("问题一", "问题二", "问题三"):
        save_report(path, {"question": q, "token_type": "staff",
                           "degradation_level": "L0", "event_count": 0,
                           "payload": {}})
    rows = list_reports(path, 10)
    assert len(rows) == 3
    assert rows[0]["question"] == "问题三"
    os.unlink(path)


def test_get_missing_returns_none():
    path = _db()
    assert get_report(path, 999) is None
    os.unlink(path)
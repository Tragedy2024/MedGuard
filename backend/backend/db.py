"""平台元数据库（medguard.db）。

与被审计的演示业务库分离——本库存平台自身状态（查询历史报告）。
"""
import json
import sqlite3
from typing import Any, Dict, List, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    question          TEXT NOT NULL,
    token_type        TEXT NOT NULL,
    degradation_level TEXT NOT NULL,
    event_count       INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    payload           TEXT NOT NULL
)
"""


def _connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def init_db(db_path: str) -> None:
    con = _connect(db_path)
    con.execute(_SCHEMA)
    con.commit()
    con.close()


def save_report(db_path: str, record: Dict[str, Any]) -> int:
    con = _connect(db_path)
    cur = con.execute(
        "INSERT INTO reports (question, token_type, degradation_level,"
        " event_count, payload) VALUES (?, ?, ?, ?, ?)",
        (record["question"], record["token_type"],
         record["degradation_level"],
         record.get("event_count", 0),
         json.dumps(record.get("payload", {}), ensure_ascii=False)))
    con.commit()
    rid = cur.lastrowid
    con.close()
    return rid


def list_reports(db_path: str, limit: int = 20) -> List[Dict[str, Any]]:
    con = _connect(db_path)
    rows = con.execute(
        "SELECT id, question, token_type, degradation_level, event_count,"
        " created_at FROM reports ORDER BY id DESC LIMIT ?",
        (limit,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_report(db_path: str, report_id: int) -> Optional[Dict[str, Any]]:
    con = _connect(db_path)
    row = con.execute("SELECT * FROM reports WHERE id = ?",
                      (report_id,)).fetchone()
    con.close()
    if row is None:
        return None
    rec = dict(row)
    rec["payload"] = json.loads(rec["payload"])
    return rec
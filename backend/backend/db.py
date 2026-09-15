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


def _iso_utc(created_at: str) -> str:
    """把 SQLite 的时间戳补成明确的 ISO-8601 UTC（`...T...Z`）。

    `datetime('now')` 返回的是 **UTC**，格式 `YYYY-MM-DD HH:MM:SS`，**没有
    时区标记**。这样一串字符是歧义的：前端只能猜，猜错就是 8 小时偏差
    （实测界面上显示 06:56，实际是 14:56）。

    所以在这里补上 T 与 Z，让"这是 UTC"成为数据的一部分而不是约定。
    展示成北京时间是前端的事——**存 UTC、显示本地**，反过来做会让这个
    字段将来谁读都得先考古。
    """
    if not created_at:
        return created_at
    s = created_at.strip()
    if "T" in s or s.endswith("Z"):
        return s            # 已经是 ISO 形状，不动
    return s.replace(" ", "T") + "Z"


def list_reports(db_path: str, limit: int = 20) -> List[Dict[str, Any]]:
    con = _connect(db_path)
    rows = con.execute(
        "SELECT id, question, token_type, degradation_level, event_count,"
        " created_at FROM reports ORDER BY id DESC LIMIT ?",
        (limit,)).fetchall()
    con.close()
    out = []
    for r in rows:
        d = dict(r)
        d["created_at"] = _iso_utc(d.get("created_at", ""))
        out.append(d)
    return out


def get_report(db_path: str, report_id: int) -> Optional[Dict[str, Any]]:
    con = _connect(db_path)
    row = con.execute("SELECT * FROM reports WHERE id = ?",
                      (report_id,)).fetchone()
    con.close()
    if row is None:
        return None
    rec = dict(row)
    rec["created_at"] = _iso_utc(rec.get("created_at", ""))
    rec["payload"] = json.loads(rec["payload"])
    return rec
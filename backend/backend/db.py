"""平台元数据库（medguard.db）。

与被审计的演示业务库分离——本库存平台自身状态（查询历史报告）。
"""
import json
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

_USERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    account       TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL,
    subject_id    TEXT,
    display_name  TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

# 演示账号。口令在登录页是明示的（页面上直接写着），所以这里不算泄露；
# 真实部署时这三个应当由管理员建号后删掉。
_DEMO_PASSWORD = "medguard"
_DEMO_USERS = [
    ("admin", "admin", None, "信息科管理员"),
    ("doctor", "staff", "S001", "医生001 · 心内科"),
    ("patient", "patient", "P001", "患者001"),
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    question          TEXT NOT NULL,
    token_type        TEXT NOT NULL,
    subject_id        TEXT,
    account           TEXT,
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
    # 老库补列：CREATE TABLE IF NOT EXISTS 对**已存在**的表不会加字段，
    # 而 subject_id 是本次修复新增的（报告要按令牌身份隔离）。
    cols = {r[1] for r in con.execute("PRAGMA table_info(reports)")}
    if "subject_id" not in cols:
        con.execute("ALTER TABLE reports ADD COLUMN subject_id TEXT")
    # account 是"报告按谁隔离"的最终依据（subject_id 会撞管理员口径，
    # 见 _scope 的说明）。老库里的历史行走不到，本来就归不了属。
    if "account" not in cols:
        con.execute("ALTER TABLE reports ADD COLUMN account TEXT")
    con.execute(_USERS_SCHEMA)
    con.commit()
    con.close()
    seed_demo_users(db_path)


# ── 账号 ──────────────────────────────────────────────────────

def _insert_user(db_path: str, *, account: str, password_hash: str,
                 role: str, subject_id: Optional[str],
                 display_name: str) -> bool:
    con = _connect(db_path)
    try:
        con.execute(
            "INSERT INTO users (account, password_hash, role, subject_id,"
            " display_name) VALUES (?, ?, ?, ?, ?)",
            (account, password_hash, role, subject_id, display_name))
        con.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        con.close()


def seed_demo_users(db_path: str) -> None:
    """**首次初始化**时种下三个演示账号（表非空则什么都不做）。

    原先的写法是"缺哪个补哪个"，于是运维按文档删掉演示账号之后，下一次
    重启又会把它们原样建回来——口令还是印在登录页上的那个，没有日志、
    没有开关。演示账号是"开箱可用"的便利，不该变成删不掉的后门。

    三个账号口令相同，所以**只派生一次哈希**：PBKDF2 20 万轮每次约 70ms，
    而每个测试的 fixture 都会 init_db——不省这一步，整套测试要多跑好几秒。
    共用同一个盐意味着攻破一个等于拿到三个，但它们本来就是同一个口令，
    没有额外损失。
    """
    from backend import credentials   # 延迟导入：依赖 config，避免环
    con = _connect(db_path)
    try:
        if con.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
            return
    finally:
        con.close()
    shared_hash = credentials.hash_password(_DEMO_PASSWORD)
    for account, role, subject_id, display in _DEMO_USERS:
        _insert_user(db_path, account=account, password_hash=shared_hash,
                     role=role, subject_id=subject_id, display_name=display)


def create_user(db_path: str, *, account: str, password: str, role: str,
                subject_id: Optional[str], display_name: str) -> bool:
    """建号。账号已存在返回 False（不覆盖，避免静默改掉别人的口令）。"""
    from backend import credentials
    return _insert_user(db_path, account=account,
                        password_hash=credentials.hash_password(password),
                        role=role, subject_id=subject_id,
                        display_name=display_name)


def get_user(db_path: str, account: str) -> Optional[Dict[str, Any]]:
    con = _connect(db_path)
    row = con.execute("SELECT * FROM users WHERE account = ?",
                      (account,)).fetchone()
    con.close()
    return dict(row) if row else None


def list_users(db_path: str) -> List[Dict[str, Any]]:
    con = _connect(db_path)
    rows = con.execute(
        "SELECT account, role, subject_id, display_name, created_at"
        " FROM users ORDER BY account").fetchall()
    con.close()
    return [dict(r) for r in rows]


def save_report(db_path: str, record: Dict[str, Any]) -> int:
    con = _connect(db_path)
    cur = con.execute(
        "INSERT INTO reports (question, token_type, subject_id, account,"
        " degradation_level, event_count, payload)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (record["question"], record["token_type"], record.get("subject_id"),
         record.get("account"),
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


def _scope(account: Optional[str]) -> Tuple[str, list]:
    """把令牌身份翻译成 WHERE 子句——**按账号隔离**。

    一开始是按 `(token_type, subject_id)` 隔离的，那个设计有个洞：管理员
    令牌的 subject_id 是 None，于是"绑定主体留空"的新 staff 账号会和管理员
    落进**同一个桶**，看到管理员的整本台账（实测复现过）。而"绑定主体"
    在建号界面是**选填**的，所以这是默认路径，不是边角情况。

    账号是唯一的，不会重合；它在令牌的签名载荷里，客户端改不了。

    account 为空（没有账号的令牌）时退回 IS NULL——不能用 `account = ?`
    绑定 None，那样永远匹配不上任何一行。
    """
    if not account:
        return "account IS NULL", []
    return "account = ?", [account]


def list_reports(db_path: str, *, account: Optional[str],
                 limit: int = 20) -> List[Dict[str, Any]]:
    """列出**该账号发起的**报告。

    account 是强制关键字参数，刻意不给默认值：这个库原先就是一个无过滤的
    全表倒序，任何"忘了传身份"的调用都会重演"患者看到医生报告"的串号问题。
    宁可让调用方编译期就报错，也不要静默地 fail-open。
    """
    where, params = _scope(account)
    con = _connect(db_path)
    rows = con.execute(
        "SELECT id, question, token_type, degradation_level, event_count,"
        f" created_at FROM reports WHERE {where} ORDER BY id DESC LIMIT ?",
        (*params, limit)).fetchall()
    con.close()
    out = []
    for r in rows:
        d = dict(r)
        d["created_at"] = _iso_utc(d.get("created_at", ""))
        out.append(d)
    return out


def get_report(db_path: str, report_id: int, *,
               account: Optional[str]) -> Optional[Dict[str, Any]]:
    """按 id 取报告，但**仍受账号约束**。

    知道别人的 id 也读不到：越权一律返回 None，由路由转成 404——不暴露
    "这条记录存在"这个事实本身。
    """
    where, params = _scope(account)
    con = _connect(db_path)
    row = con.execute(
        f"SELECT * FROM reports WHERE id = ? AND {where}",
        (report_id, *params)).fetchone()
    con.close()
    if row is None:
        return None
    rec = dict(row)
    rec["created_at"] = _iso_utc(rec.get("created_at", ""))
    rec["payload"] = json.loads(rec["payload"])
    return rec
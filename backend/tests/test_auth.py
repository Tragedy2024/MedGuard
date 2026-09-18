"""账号与令牌测试。

这一层修的是**根本问题**：在此之前登录完全发生在前端（`auth.tsx` 硬编码
三个账号、口令明文写在源码里），令牌由浏览器现场拼出来，后端既不参与登录
也验证不了令牌。于是"身份"只是客户端的自述——手搓一个
`{"type":"patient","subject_id":"P002"}` 就能读别人的安全报告。
"""
import time

import pytest
from fastapi.testclient import TestClient

from backend import credentials
from backend.main import app
from tests.helpers import load_demo, scope, sign


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDGUARD_DATA_DIR", str(tmp_path))
    from backend import config
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "METADATA_DB", str(tmp_path / "medguard.db"))
    monkeypatch.setattr(config, "BUSINESS_DB", str(tmp_path / "regional_health.db"))
    from backend.db import init_db
    init_db(str(tmp_path / "medguard.db"))
    # **不要**在这里 reset 签名密钥：其它测试文件在 import 时就签好了模块级
    # 令牌常量（tests/helpers.sign），换掉密钥会让它们全部验签失败，症状是
    # 一堆莫名其妙的 401。密钥在进程内只读一次，全程稳定即可。
    c = TestClient(app)
    load_demo(c)
    # 注意：business 库要建，否则查询会走到「取数失败」分支
    return c


def _login(c, account, password="medguard"):
    return c.post("/api/auth/login",
                  json={"account": account, "password": password})


# ── 口令哈希 ──────────────────────────────────────────────────

def test_password_hash_is_salted_and_verifiable():
    h1 = credentials.hash_password("medguard")
    h2 = credentials.hash_password("medguard")
    assert h1 != h2, "同一口令两次哈希必须不同（盐随每次生成）"
    assert credentials.verify_password("medguard", h1)
    assert not credentials.verify_password("medguar", h1)


@pytest.mark.parametrize("stored", ["", "garbage", "pbkdf2_sha256$1$zz$zz",
                                    "bcrypt$x$y$z", None])
def test_malformed_hash_never_raises(stored):
    """损坏的哈希串只返回 False——不能因为数据脏就把 500 抛给用户。"""
    assert credentials.verify_password("x", stored) is False


# ── 登录 ──────────────────────────────────────────────────────

def test_login_returns_a_signed_token(client):
    r = _login(client, "doctor")
    assert r.status_code == 200
    body = r.json()
    assert body["user"] == {"account": "doctor", "role": "staff",
                            "subject_id": "S001",
                            "display_name": "医生001 · 心内科"}
    tok = body["token"]
    assert tok["type"] == "staff" and tok["account"] == "doctor"
    assert tok["exp"] > time.time()
    assert len(tok["sig"]) == 64
    # 签发的令牌必须能过验签
    credentials.verify_token(tok["type"], tok["subject_id"], tok["account"],
                             tok["exp"], tok["sig"])


def test_login_response_never_leaks_the_password_hash(client):
    r = _login(client, "patient")
    assert "password" not in r.text and "hash" not in r.text


def test_wrong_password_and_unknown_account_are_indistinguishable(client):
    """分开报错等于送人一个账号枚举接口。"""
    a = _login(client, "doctor", "wrong")
    b = _login(client, "no-such-user", "wrong")
    assert a.status_code == b.status_code == 401
    assert a.json()["detail"] == b.json()["detail"]


# ── 验签：伪造的令牌一律拒绝 ──────────────────────────────────

@pytest.mark.parametrize("endpoint,payload", [
    ("/api/query", {"datasource_id": "regional_health",
                    "question_id": "patient_my_lab"}),
    ("/api/query/direct", {"datasource_id": "regional_health",
                           "question": "我上次的血糖是多少"}),
    ("/api/smart-doctor/ask", {"datasource_id": "regional_health",
                               "question": "我的血糖结果正常吗"}),
])
def test_unsigned_token_is_rejected_everywhere(client, endpoint, payload):
    """裸字典令牌（改动之前客户端就是这么发的）必须全线 401。"""
    r = client.post(endpoint, json={"token": {"type": "patient",
                                              "subject_id": "P001"}, **payload})
    assert r.status_code == 401, f"{endpoint} 竟然接受了未签名令牌"


def test_unsigned_token_is_rejected_on_reports(client):
    r = client.get("/api/reports", params={"token_type": "patient",
                                           "subject_id": "P001"})
    assert r.status_code == 401


@pytest.mark.parametrize("field,value", [
    ("subject_id", "P002"),      # 换成别人的主体
    ("type", "staff"),           # 换成别人的角色
    ("account", "admin"),        # 冒充管理员
    ("exp", 99999999999),        # 延长有效期
    ("sig", "f" * 64),           # 直接伪造签名
])
def test_tampered_token_fields_are_rejected(client, field, value):
    tok = dict(sign("patient", "P001"))
    tok[field] = value
    r = client.post("/api/query", json={
        "token": tok, "datasource_id": "regional_health",
        "question_id": "patient_my_lab"})
    assert r.status_code == 401, f"篡改 {field} 竟然通过了"


def test_expired_token_is_rejected(client):
    exp, sig = credentials.sign_token("patient", "P001", "patient", ttl=-1)
    r = client.post("/api/query", json={
        "token": {"type": "patient", "subject_id": "P001",
                  "account": "patient", "exp": exp, "sig": sig},
        "datasource_id": "regional_health", "question_id": "patient_my_lab"})
    assert r.status_code == 401


# ── 建号：只有管理员能做 ──────────────────────────────────────

def _register(c, token, **over):
    body = {"token": token, "account": "nurse", "password": "pw123456",
            "role": "staff", "subject_id": "S002", "display_name": "护士002"}
    body.update(over)
    return c.post("/api/auth/register", json=body)


def test_admin_can_create_and_the_new_account_can_log_in(client):
    adm = _login(client, "admin").json()["token"]
    r = _register(client, adm)
    assert r.status_code == 201
    assert r.json()["account"] == "nurse"
    assert _login(client, "nurse", "pw123456").status_code == 200


def test_non_admin_cannot_create_accounts(client):
    """患者与医护都不该能给自己或别人开号——否则谁都能注册成医生。"""
    for account in ("patient", "doctor"):
        tok = _login(client, account).json()["token"]
        r = _register(client, tok)
        assert r.status_code == 403, f"{account} 竟然能建号"


def test_unsigned_token_cannot_create_accounts(client):
    r = _register(client, {"type": "staff", "subject_id": None})
    assert r.status_code == 401


def test_duplicate_account_is_rejected(client):
    adm = _login(client, "admin").json()["token"]
    assert _register(client, adm).status_code == 201
    # 口令要够长，否则会先撞上长度校验（422）而不是重复账号（409）
    assert _register(client, adm, password="otherpass").status_code == 409


@pytest.mark.parametrize("over,why", [
    ({"account": "  "}, "空账号"),
    ({"password": "123"}, "口令太短"),
    ({"role": "patient", "subject_id": None}, "病患没绑主体"),
    ({"role": "patient", "subject_id": "  "}, "病患绑了空白主体"),
    ({"role": "admin", "subject_id": "P001"}, "管理员不该绑主体"),
])
def test_register_validation(client, over, why):
    adm = _login(client, "admin").json()["token"]
    assert _register(client, adm, **over).status_code == 422, why


# ── 账号列表 ──────────────────────────────────────────────────

def test_user_list_is_admin_only_and_carries_no_secrets(client):
    adm = _login(client, "admin").json()["token"]
    r = client.get("/api/auth/users", params=scope(adm))
    assert r.status_code == 200
    accounts = [u["account"] for u in r.json()]
    assert set(accounts) == {"admin", "doctor", "patient"}
    assert "password_hash" not in r.text

    pat = _login(client, "patient").json()["token"]
    assert client.get("/api/auth/users", params=scope(pat)).status_code == 403
    assert client.get("/api/auth/users",
                      params={"token_type": "patient",
                              "subject_id": "P001"}).status_code == 401


# ── 报告隔离在签名令牌下的端到端表现 ──────────────────────────

def test_reports_stay_isolated_with_signed_tokens(client):
    doc = _login(client, "doctor").json()["token"]
    pat = _login(client, "patient").json()["token"]
    client.post("/api/query", json={"token": doc,
                                    "datasource_id": "regional_health",
                                    "question_id": "doctor_dept_visits"})
    client.post("/api/query", json={"token": pat,
                                    "datasource_id": "regional_health",
                                    "question_id": "patient_my_lab"})

    staff_rows = client.get("/api/reports", params=scope(doc)).json()
    patient_rows = client.get("/api/reports", params=scope(pat)).json()
    assert [r["question"] for r in staff_rows] == ["统计各科室接诊量"]
    assert [r["question"] for r in patient_rows] == ["我上次的血糖是多少"]

    rid = staff_rows[0]["id"]
    assert client.get(f"/api/reports/{rid}",
                      params=scope(pat)).status_code == 404


# ── 初始化与降级 ──────────────────────────────────────────────

def test_deleted_demo_accounts_are_not_resurrected(client):
    """删掉的演示账号不该在下次启动时被建回来。

    原先的 seed 是"缺哪个补哪个"，于是运维按文档删掉三个演示账号之后，
    下一次重启又把它们原样建回来——口令还是印在登录页上的那个，没有日志、
    没有开关。那把"开箱可用"变成了删不掉的后门。
    """
    import sqlite3

    from backend import config
    from backend.db import get_user, init_db
    con = sqlite3.connect(config.METADATA_DB)
    con.execute("DELETE FROM users WHERE account = 'admin'")
    con.commit()
    con.close()

    init_db(config.METADATA_DB)
    assert get_user(config.METADATA_DB, "admin") is None
    # 其余账号不受影响，也不会被重种
    assert get_user(config.METADATA_DB, "doctor") is not None


def test_login_says_something_useful_when_metadata_db_is_missing(client, tmp_path, monkeypatch):
    """元数据库不可用时给一句人话，而不是 500。

    `_bootstrap_demo_data` 刻意吞掉所有异常（"初始化失败不该导致服务起不来"），
    所以数据目录不可写时服务照常启动、只是没有 users 表——此时
    `no such table: users` 会直接从登录接口冒出去。
    """
    from backend import config
    monkeypatch.setattr(config, "METADATA_DB",
                        str(tmp_path / "nowhere" / "medguard.db"))
    r = client.post("/api/auth/login",
                    json={"account": "admin", "password": "medguard"})
    assert r.status_code == 503
    assert "元数据库" in r.json()["detail"]


def test_login_latency_does_not_reveal_whether_an_account_exists(client):
    """两种失败不只是同一句话，耗时也要相当。

    直接 `if user is None` 短路的话，"账号不存在"会比"口令错误"快一个数量级
    （PBKDF2 20 万轮的差距），等于把账号枚举从错误信息挪到了计时上。
    而演示口令就印在登录页上，枚举出账号就能直接撞库。
    """
    import time

    def median_ms(account):
        ts = []
        for _ in range(5):
            t0 = time.perf_counter()
            client.post("/api/auth/login",
                        json={"account": account, "password": "definitely-wrong"})
            ts.append((time.perf_counter() - t0) * 1000)
        return sorted(ts)[len(ts) // 2]

    existing, missing = median_ms("doctor"), median_ms("no-such-account-at-all")
    # 只要求同一量级，不要求精确相等（CI 上抖动很大）
    assert max(existing, missing) <= min(existing, missing) * 3, (
        f"存在={existing:.1f}ms 不存在={missing:.1f}ms —— 差太多就是账号枚举信道")


def test_register_surfaces_a_lost_race_as_409(client, monkeypatch):
    """并发建号时不能返回 201。

    上面查过一次"不存在"，但两个管理员同时提交时两条请求都可能查到 None，
    INSERT 时一条撞主键。丢掉 create_user 的返回值的话，调用方会拿到
    201——而且响应体是**已存在那条**的角色与绑定，它会以为参数生效了。
    """
    from backend import routers
    adm = _login(client, "admin").json()["token"]
    assert _register(client, adm).status_code == 201

    # 假装第二个请求"没查到已存在"（正是竞态里发生的事）
    monkeypatch.setattr(routers.auth, "_get_user", lambda account: None)
    r = _register(client, adm, password="otherpass")
    assert r.status_code == 409, "竞态下不该报成功"

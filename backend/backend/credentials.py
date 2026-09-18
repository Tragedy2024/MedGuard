"""账号与令牌——后端签发 + HMAC 签名。

在此之前，登录完全发生在前端（`auth.tsx` 里硬编码三个账号、口令明文写在源码
里），令牌由浏览器现场拼出来。后端既不参与登录、也验证不了令牌，于是"身份"
只是客户端的**自述**：任何人手搓一个 `{"type": "patient", "subject_id": "P002"}`
就能读别人的安全报告。

现在：账号落在平台元数据库里，口令用 PBKDF2 哈希，登录由后端签发带签名的
令牌，**每个收令牌的端点都验签**。签名密钥不进代码——从环境变量读，没有就
生成一份持久化到数据目录（这样 `--reload` 重启不会把所有人踢下线）。

注意本模块**不碰算法层**（那是只读依赖），也不做任何 SQL 审计——它只管
"你是谁"，"你能查什么"仍然由层一准入负责。
"""
import hashlib
import hmac
import os
import secrets
import threading
import time
from typing import Optional, Tuple

from backend import config

# 令牌有效期：8 小时。演示足够长，又不至于让一个泄露的令牌永久可用。
TOKEN_TTL_SEC = 8 * 3600

# PBKDF2 轮数。20 万次在现代机器上约 60–80ms，登录/注册各付一次；
# 测试里会跑几十次，仍在可接受范围。
_PBKDF2_ROUNDS = 200_000
_PBKDF2_ALGO = "sha256"

_SECRET_FILE = ".token_secret"
_SECRET_ENV = "MEDGUARD_TOKEN_SECRET"
_SECRET_BYTES = 32

_secret_lock = threading.Lock()
_cached_secret: Optional[bytes] = None


class TokenError(Exception):
    """令牌缺失、过期或签名不符。路由层统一转成 401。"""


# ── 口令哈希 ──────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """返回 `pbkdf2_sha256$<轮数>$<盐hex>$<摘要hex>`。

    盐随每次调用新生成，所以同一个口令两次哈希结果不同——比较时必须走
    verify_password，不能比字符串。
    """
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        _PBKDF2_ALGO, password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_{_PBKDF2_ALGO}${_PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """校验口令。任何格式异常一律返回 False，不抛异常、不泄露细节。"""
    try:
        algo, rounds_s, salt_hex, digest_hex = stored.split("$")
        if not algo.startswith("pbkdf2_"):
            return False
        digest = hashlib.pbkdf2_hmac(
            algo.split("_", 1)[1], password.encode("utf-8"),
            bytes.fromhex(salt_hex), int(rounds_s))
        # 定长比较，避免按字节提前返回泄露信息。
        # **比字节不比字符串**：`hmac.compare_digest` 对含非 ASCII 的 str
        # 会抛 TypeError 而不是返回 False（见 _equals_hex 的说明）。
        return _equals_hex(digest.hex(), digest_hex)
    except (ValueError, AttributeError, TypeError):
        return False


# ── 签名密钥 ──────────────────────────────────────────────────

def _secret() -> bytes:
    """取签名密钥：环境变量优先，否则在数据目录放一份随机密钥。

    刻意**不写死默认值**——写死的密钥等于没有签名，谁读了源码都能伪造。
    持久化到磁盘是因为 `--reload` 会频繁重启进程，内存里的随机密钥会让
    每次热重载都把在场所有人踢下线。
    """
    global _cached_secret
    with _secret_lock:
        if _cached_secret is not None:
            return _cached_secret

        env = os.environ.get(_SECRET_ENV)
        if env:
            _cached_secret = env.encode("utf-8")
            return _cached_secret

        path = os.path.join(config.DATA_DIR, _SECRET_FILE)
        try:
            if os.path.exists(path):
                with open(path, "rb") as f:
                    data = f.read()
                # **绝不能 strip()**：密钥是 32 个任意字节，首尾本来就可能落在
                # 空白区间（实测概率 4.6%）。strip 会吃掉它们，于是"读回来的
                # 密钥"和"写下去的密钥"差一两个字节 → 重启后所有在途令牌验签
                # 失败、全员被登出 —— 恰恰是持久化要防的那件事。
                # 长度不对就当没有（文件被手改过/截断过），重新生成。
                if len(data) == _SECRET_BYTES:
                    _cached_secret = data
                    return _cached_secret
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            data = secrets.token_bytes(_SECRET_BYTES)
            # 原子落地：先写临时文件再 os.replace。直接写目标路径的话，
            # 两个进程同时首启会互相截断对方的文件，读出半截密钥。
            tmp_path = path + f".{os.getpid()}"
            with open(tmp_path, "wb") as f:
                f.write(data)
            os.replace(tmp_path, path)
            _cached_secret = data
            return _cached_secret
        except OSError:
            # 数据目录不可写（只读挂载等）→ 退化成进程内密钥。
            # 令牌过一次热重载就失效，但**绝不**退化成"没有签名"。
            _cached_secret = secrets.token_bytes(32)
            return _cached_secret


# ── 令牌签名 ──────────────────────────────────────────────────

def _equals_hex(expected: str, actual: Optional[str]) -> bool:
    """定长比较两个十六进制摘要。

    **必须 encode 成字节再比**：`hmac.compare_digest` 只接受「两个 str 且都是
    ASCII」或「两个 bytes-like」。给它一个含非 ASCII 的 str 会抛
    `TypeError: comparing strings with non-ASCII characters is not supported`
    —— 而 `sig` 直接来自请求参数，任何人都能发 `sig=中文`。那会让 401 边界
    变成 500：未认证的人可以刷错误页，前端还会把它显示成"服务器错误"而不是
    真正的认证失败。编码成字节后，任何输入都只会得到 False。
    """
    return hmac.compare_digest(expected.encode("ascii"),
                               (actual or "").encode("utf-8"))


def _payload(token_type: str, subject_id: Optional[str],
             account: Optional[str], exp: int) -> bytes:
    """签名的原文。分隔符用 `\\x1f`（单元分隔符）——它不可能出现在账号或
    主体编号里，用 `|` 之类的话 `a|b` 与 `a` + `|b` 会撞出同一个签名。

    **账号必须进签名**：令牌只带 staff/patient 两类角色（设计文档 §1.2），
    "是不是管理员"只能回到账号表里查；如果账号不在签名载荷里，就没法安全
    地把它附在令牌上——任何人都能改成别人的账号。
    """
    body = f"{token_type}\x1f{subject_id or ''}\x1f{account or ''}\x1f{exp}"
    return body.encode("utf-8")


def sign_token(token_type: str, subject_id: Optional[str],
               account: Optional[str] = None,
               ttl: int = TOKEN_TTL_SEC) -> Tuple[int, str]:
    """签发一枚令牌，返回 (到期时间戳, 签名)。"""
    exp = int(time.time()) + ttl
    sig = hmac.new(_secret(), _payload(token_type, subject_id, account, exp),
                   hashlib.sha256).hexdigest()
    return exp, sig


def verify_token(token_type: str, subject_id: Optional[str],
                 account: Optional[str], exp: Optional[int],
                 sig: Optional[str]) -> None:
    """验签 + 查过期。不通过抛 TokenError。

    先比签名再看过期——顺序反了的话，一个被篡改的令牌会收到"已过期"这种
    带有信息量的错误，等于告诉攻击者"你构造的载荷被解析了"。
    """
    if not sig or exp is None:
        raise TokenError("令牌缺少签名或有效期")
    expected = hmac.new(
        _secret(), _payload(token_type, subject_id, account, int(exp)),
        hashlib.sha256).hexdigest()
    if not _equals_hex(expected, sig):
        raise TokenError("令牌签名不符")
    if int(exp) < int(time.time()):
        raise TokenError("令牌已过期")


def token_type_for_role(role: str) -> str:
    """账号角色 → 令牌类型。

    平台只有 staff/patient 两类令牌（设计文档 §1.2）：管理员的「管理」能力
    体现在页面准入与建号权限上，不体现在数据权限上，所以他持医护令牌。
    """
    return "patient" if role == "patient" else "staff"


def reset_secret_cache() -> None:
    """清空密钥缓存。仅测试使用——改完环境变量要让下一次调用重新读。"""
    global _cached_secret
    with _secret_lock:
        _cached_secret = None

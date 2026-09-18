"""登录与建号。

**登录由后端签发令牌**——在此之前登录完全发生在前端（`auth.tsx` 里硬编码
三个账号、口令明文写在源码里），令牌是浏览器现场拼的，后端既验证不了也
拦不住。于是"身份"只是客户端的自述。

**开号只有管理员能做**：医院里给人开账号是信息科的事，患者和医生不该能
自己把自己注册成医生。这也是为什么没有自助注册接口。
"""
import os
import secrets
import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from backend import config, credentials
from backend.db import create_user, get_user, list_users
from backend.schemas import (LoginRequest, LoginResponse, RegisterRequest,
                             Token, UserInfo)
from backend.security import require_admin, token_from_query

router = APIRouter(prefix="/api/auth", tags=["auth"])

# 口令下限。演示环境不追求复杂度规则，但空口令/一位数口令必须挡住——
# 这是唯一一处能让"安全的平台"输在起跑线上的地方。
_MIN_PASSWORD = 6


def _user_info(user: dict) -> UserInfo:
    return UserInfo(account=user["account"], role=user["role"],
                    subject_id=user["subject_id"],
                    display_name=user["display_name"])


def _issue(user: dict) -> Token:
    token_type = credentials.token_type_for_role(user["role"])
    exp, sig = credentials.sign_token(token_type, user["subject_id"], user["account"])
    return Token(type=token_type, subject_id=user["subject_id"],
                 account=user["account"], exp=exp, sig=sig)


# 账号不存在时拿来"陪跑"的哈希。只为让两条失败路径耗时相当，见 login。
_DUMMY_HASH = credentials.hash_password(secrets.token_urlsafe(32))

_INVALID = "账号或密码不正确。"


def _get_user(account: str):
    """读账号，元数据库还没就绪时给一句人话而不是 500。

    `_bootstrap_demo_data` 刻意吞掉所有异常（"初始化失败不该导致服务起不来"），
    所以数据目录不可写时服务照常启动、只是没有 users 表——此时
    `sqlite3.OperationalError: no such table: users` 会直接从登录接口冒出去。
    """
    if not os.path.exists(config.METADATA_DB):
        raise HTTPException(status_code=503,
                            detail="平台元数据库尚未初始化，请先载入演示数据。")
    try:
        return get_user(config.METADATA_DB, account)
    except sqlite3.OperationalError as exc:
        raise HTTPException(status_code=503,
                            detail="平台元数据库不可用，请检查数据目录权限。") from exc


@router.post("/login", response_model=LoginResponse)
def login(req: LoginRequest):
    """登录。两种失败返回**同一句话**，且**耗时相当**。

    错误信息合并是常识；耗时对齐同样必要——`user is None` 直接短路的话，
    "账号不存在"会比"口令错误"快一个数量级（实测 3.3ms vs 38ms，PBKDF2
    20 万轮的差距），等于把账号枚举从错误信息挪到了计时上。而演示口令
    就印在登录页上，枚举出账号就能直接撞库。

    所以账号不存在时也跑一次校验，只是拿一个永远对不上的哈希。
    """
    user = _get_user(req.account.strip())
    stored = user["password_hash"] if user else _DUMMY_HASH
    ok = credentials.verify_password(req.password, stored)
    if user is None or not ok:
        raise HTTPException(status_code=401, detail=_INVALID)
    return LoginResponse(user=_user_info(user), token=_issue(user))


@router.post("/register", response_model=UserInfo, status_code=201)
def register(req: RegisterRequest):
    """建号。只有管理员令牌能调。"""
    require_admin(req.token)

    account = req.account.strip()
    if not account:
        raise HTTPException(status_code=422, detail="账号不能为空")
    if len(req.password) < _MIN_PASSWORD:
        raise HTTPException(
            status_code=422, detail=f"口令至少 {_MIN_PASSWORD} 位")
    if _get_user(account) is not None:
        raise HTTPException(status_code=409, detail="该账号已存在")

    subject_id = (req.subject_id or "").strip() or None
    if req.role == "patient" and not subject_id:
        raise HTTPException(
            status_code=422,
            detail="病患账号必须绑定主体（subject_id，如 P001），否则无从"
                   "保证只读取本人数据。",
        )
    if req.role == "admin" and subject_id:
        raise HTTPException(
            status_code=422,
            detail="管理员不绑定主体——其管理能力体现在页面准入与建号权限，"
                   "不体现在数据权限上。",
        )

    display_name = req.display_name.strip() or account
    # **必须看返回值**：上面查过一次"不存在"，但两个管理员同时提交（或
    # 一次双击）时两条请求都可能查到 None，INSERT 时一条撞主键返回 False。
    # 丢掉它的话，调用方会拿到 201 —— 而且响应体是**已存在那条**的角色与
    # 绑定，它会以为自己的参数生效了。
    if not create_user(config.METADATA_DB, account=account,
                       password=req.password, role=req.role,
                       subject_id=subject_id, display_name=display_name):
        raise HTTPException(status_code=409, detail="该账号已存在")
    return _user_info(_get_user(account))


@router.get("/users", response_model=list[UserInfo])
def users(token: Token = Depends(token_from_query)):
    """账号列表（管理员）。用于用户管理页，只返回公开字段。

    GET 带不了请求体，所以令牌走查询参数（`token_from_query` 会验签）。
    """
    require_admin(token)
    return [_user_info(u) for u in list_users(config.METADATA_DB)]

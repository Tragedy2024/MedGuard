"""HTTP 层的身份强制。

`auth.py` 只负责密码学原语（哈希、签名、验签），不认识 FastAPI；
把"验不过就 401"这件事放在这里，两者分开后，auth 可以被测试直接调用，
不必构造 HTTP 请求。

**为什么是 401 而不是 403**：403 的意思是"你的身份我知道了，但这个资源
不归你"。令牌签名不符时我们连"你是谁"都不知道，属于未通过认证，是 401。
"""
from typing import Any, Dict, Literal, Optional

from fastapi import HTTPException

from backend import credentials
from backend.credentials import TokenError
from backend.schemas import Token


def token_from_query(
    token_type: Literal["staff", "patient"],
    subject_id: Optional[str] = None,
    account: Optional[str] = None,
    exp: Optional[int] = None,
    sig: Optional[str] = None,
) -> Token:
    """从查询参数拼出令牌并验签——给 GET 端点用。

    GET 带不了请求体，令牌只能拆开传。这五个字段就是 `Token` 的全部字段，
    一个都不能少：少了 `sig` 就退回"客户端自述身份"，而伪造一个
    `token_type=patient&subject_id=P002` 恰好能读到别人的报告。

    用法：`token: Token = Depends(token_from_query)`。
    """
    return verified(Token(type=token_type, subject_id=subject_id or None,
                          account=account or None, exp=exp, sig=sig))


def verified(token: Token) -> Token:
    """验签 + 查过期，通过返回原令牌，否则抛 401。

    每个收令牌的端点都必须先过这一关：query / query.direct /
    smart-doctor / reports 全都在内。漏掉一个就等于那一个端点仍可被伪造
    令牌访问——而伪造的令牌恰好能读到别人的安全报告。
    """
    try:
        credentials.verify_token(token.type, token.subject_id, token.account,
                          token.exp, token.sig)
    except TokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return token


def require_admin(token: Token) -> Dict[str, Any]:
    """验签之后再要求管理员角色，返回**库里的**账号记录。

    注意角色是从账号表里查出来的，不是令牌自称的——令牌只带 staff/patient
    （设计文档 §1.2 的既有约束），"是不是管理员"必须回库核对，否则任何
    医护令牌都能自称管理员来建号。账号在签名载荷里，所以用户改不了它。
    """
    import os
    import sqlite3

    from backend import config
    from backend.db import get_user
    verified(token)
    if not os.path.exists(config.METADATA_DB):
        raise HTTPException(status_code=503,
                            detail="平台元数据库尚未初始化，请先载入演示数据。")
    try:
        user = get_user(config.METADATA_DB, token.account or "")
    except sqlite3.OperationalError as exc:
        raise HTTPException(status_code=503,
                            detail="平台元数据库不可用，请检查数据目录权限。") from exc
    if user is None or user["role"] != "admin":
        raise HTTPException(
            status_code=403,
            detail="只有管理员（信息科）可以创建账号。",
        )
    return user

"""测试用的令牌构造。

端点在 2026-09-18 之后**一律验签**：`{"type": "patient", "subject_id": "P001"}`
这种裸字典会直接 401。测试里凡是构造令牌的地方都要走这里，否则失败信息是
401，看着像权限问题，实际只是忘了签名。
"""
from typing import Any, Dict, Optional

from backend import credentials


def sign(token_type: str = "staff", subject_id: Optional[str] = None,
         account: Optional[str] = None) -> Dict[str, Any]:
    """造一枚**已签名**的令牌。"""
    exp, sig = credentials.sign_token(token_type, subject_id, account)
    return {"type": token_type, "subject_id": subject_id,
            "account": account, "exp": exp, "sig": sig}


def scope(token: Dict[str, Any]) -> Dict[str, Any]:
    """把令牌摊成 GET 端点的查询参数（`/api/reports*`、`/api/auth/users`）。

    注意 `type` → `token_type` 的改名：`type` 是 Python 内置名，FastAPI 的
    查询参数不能叫这个。
    """
    return {"token_type": token["type"],
            "subject_id": token["subject_id"] or "",
            "account": token["account"] or "",
            "exp": token["exp"],
            "sig": token["sig"]}


def staff(subject_id: Optional[str] = None,
          account: Optional[str] = "doctor") -> Dict[str, Any]:
    return sign("staff", subject_id, account)


def patient(subject_id: str = "P001",
            account: Optional[str] = "patient") -> Dict[str, Any]:
    return sign("patient", subject_id, account)


def admin() -> Dict[str, Any]:
    """管理员账号。角色在库里查，令牌本身只有 staff/patient 两类。"""
    return sign("staff", None, "admin")


def load_demo(c) -> None:
    """以管理员身份载入演示数据。

    `/api/datasources/demo` 现在要求管理员令牌——它会**先删库再重建**，
    裸调等于给任何人一个数据销毁按钮。所以测试不能像以前那样直接 POST。

    前提：fixture 必须已经调过 `init_db`（三个演示账号由它幂等种下），
    否则连登录这一步都过不去。
    """
    r = c.post("/api/auth/login",
               json={"account": "admin", "password": "medguard"})
    assert r.status_code == 200, (
        f"管理员登录失败（fixture 是不是漏了 init_db？）：{r.status_code} {r.text[:120]}")
    r = c.post("/api/datasources/demo", json={"token": r.json()["token"]})
    assert r.status_code == 200, f"载入演示数据失败：{r.status_code} {r.text[:200]}"

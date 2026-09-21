"""Vercel FastAPI 探测的入口垫片。

真实应用在包内 backend/backend/main.py，不在探测的默认位置（service root
的 main.py）。本文件用**文件路径**加载真实应用并导出 app：
- 探测步骤 import main 时拿到的是真实应用；
- 无论本文件以 `main` 还是 `backend.main` 身份被导入，都不会与包内
  backend/main.py 撞名（避免自引用循环导入）。

本地开发不受影响：uvicorn backend.main:app 从 backend/ 目录解析到
包内真实模块，不会经过本文件。
"""
import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "medguard_backend_app", Path(__file__).parent / "backend" / "main.py"
)
assert _SPEC is not None and _SPEC.loader is not None, \
    "找不到包内应用 backend/backend/main.py"
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)

app = _MOD.app

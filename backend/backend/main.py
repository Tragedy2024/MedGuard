"""FastAPI 应用装配。

后续加路由：在 routers/ 新建模块并在下方 include_router 注册即可。
首次启动会自动初始化演示库与平台元数据库（已存在则跳过，绝不覆盖）。
"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend import config
from backend.routers import (auth, datasources, policies, query, reports,
                             smart_doctor)
from backend.schemas import HealthInfo


def _bootstrap_demo_data() -> None:
    """首次启动初始化：元数据库建表（幂等）+ 演示业务库 seed（仅当缺失）。

    绝不重建已存在的业务库——用户对策略/数据的任何调整都不会被覆盖。
    """
    try:
        from backend import config
        from backend.db import init_db

        os.makedirs(config.DATA_DIR, exist_ok=True)
        init_db(config.METADATA_DB)
        if not os.path.exists(config.BUSINESS_DB):
            from demo.seed import build_database
            build_database(config.BUSINESS_DB)
            print(f"[bootstrap] 演示库已生成: {config.BUSINESS_DB}")
    except Exception as exc:  # noqa: BLE001 - 初始化失败不应导致服务起不来
        print(f"[bootstrap] 演示数据初始化跳过（可手动调用 POST /api/datasources/demo）: {exc}")


def _prewarm_knowledge() -> None:
    """预热知识检索。

    jieba 首次切词要加载词典（约 1 秒）。放在启动时做，是为了不让
    **第一个提问的患者**吃这一下——那 1 秒恰好落在演示时最不该卡的地方。

    失败不影响服务启动：检索只是 AI 兜底路径的增强，它不可用时
    问答照常（退回无上下文的纯生成）。
    """
    try:
        from backend import smart_doctor
        from backend.knowledge import retrieval

        kb = smart_doctor.load_knowledge()
        if kb:
            n = len(retrieval.get_index(kb).docs)
            # flush=True：stdout 接管道时是块缓冲，不刷就看不到——
            # 而这条恰恰是"启动完成"的信号。
            print(f"[prewarm] 知识检索已就绪（{n} 条）", flush=True)
    except Exception as exc:  # noqa: BLE001 - 预热失败不该拖垮启动
        print(f"[prewarm] 知识检索预热跳过：{exc}", flush=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _bootstrap_demo_data()
    _prewarm_knowledge()
    yield


app = FastAPI(
    title="医患信息数据服务云平台",
    version="1.0.0",
    description=(
        "让不会写 SQL 的医护人员和病患，用大白话查到权威准确的医院数据；"
        "保证查询的中间过程不泄露患者信息。安全引擎：医盾（零 LLM 审计）。"
    ),
    lifespan=lifespan,
)

# CORS。
#
# **同源托管时（见文件末尾）浏览器根本不产生跨域预检**，这项只在前后端
# 分开部署时才用得上——所以默认值只留 Vite 开发服务器的地址，生产/局域网
# 场景不必配。确实要跨域时用逗号分隔的白名单覆盖：
#     MEDGUARD_CORS_ORIGINS="https://a.example.com,https://b.example.com"
_origins = [o.strip() for o in os.environ.get(
    "MEDGUARD_CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(datasources.router)
app.include_router(policies.router)
app.include_router(query.router)
app.include_router(reports.router)
app.include_router(smart_doctor.router)


@app.get("/api/health", response_model=HealthInfo)
def health():
    """健康检查：算法层可用性 + 各路由挂载状态。"""
    from backend.deps import algo_available, list_policy_ids
    return {
        "status": "ok",
        "algo_engine": "available" if algo_available() else "unavailable",
        "policies": list_policy_ids(),
        # 从 OpenAPI schema 取，而不是遍历 `app.routes`：新版 FastAPI 的
        # include_router 挂上去的是没有 `.path` 的 `_IncludedRouter` 包装体，
        # 遍历只会列出 /api/health 自己——六个路由组全被过滤掉，这条"路由
        # 挂载状态"的自检等于永远报"一切正常"。
        "routes": sorted(p for p in app.openapi()["paths"]
                         if p.startswith("/api")),
    }


# ── 前端静态托管（可选）───────────────────────────────────────
#
# `frontend/dist` 存在就由后端一并托管：一个进程、一个端口，而且**同源**
# ——浏览器不产生跨域预检，局域网里换 IP 访问、前面加反代都不会撞 CORS。
# 演示现场因此少一个会翻车的环节。
#
# 必须注册在**所有 API 路由之后**：FastAPI 按注册顺序匹配，这个 catch-all
# 放前面会把 /api/* 全吃掉。
_DIST = config.FRONTEND_DIST
if os.path.isdir(_DIST):
    _ASSETS = os.path.join(_DIST, "assets")
    if os.path.isdir(_ASSETS):
        app.mount("/assets", StaticFiles(directory=_ASSETS), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        """非 /api 的路径回落到 index.html。

        React Router 的地址（`/users`、`/reports/3`）在服务端**没有对应
        文件**，直接敲地址或按 F5 就会 404——这是演示时最容易踩的一脚。
        字体、照片、favicon 这些真实存在的文件仍原样返回。
        """
        if full_path.startswith("api/"):
            # 未匹配的 /api/* 应当是 404，不能回一份 HTML——那会让前端拿到
            # 一坨 HTML 去 JSON.parse，报出跟真实原因毫无关系的错。
            raise HTTPException(status_code=404, detail="接口不存在")
        if full_path:
            target = os.path.normpath(os.path.join(_DIST, full_path))
            # 目录穿越防护：normalize 之后必须仍在 dist 之内
            if (target.startswith(os.path.normpath(_DIST) + os.sep)
                    and os.path.isfile(target)):
                return FileResponse(target)
        return FileResponse(os.path.join(_DIST, "index.html"))
else:
    print(f"[static] 未找到前端构建产物，只提供 API：{_DIST}")
    print("[static] 构建前端后重启即可单端口演示："
          "cd frontend && npm run build")
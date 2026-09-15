"""FastAPI 应用装配。

后续加路由：在 routers/ 新建模块并在下方 include_router 注册即可。
首次启动会自动初始化演示库与平台元数据库（已存在则跳过，绝不覆盖）。
"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.routers import datasources, policies, query, reports
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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _bootstrap_demo_data()
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(datasources.router)
app.include_router(policies.router)
app.include_router(query.router)
app.include_router(reports.router)


@app.get("/api/health", response_model=HealthInfo)
def health():
    """健康检查：算法层可用性 + 各路由挂载状态。"""
    from backend.deps import algo_available, list_policy_ids
    return {
        "status": "ok",
        "algo_engine": "available" if algo_available() else "unavailable",
        "policies": list_policy_ids(),
        "routes": sorted({r.path for r in app.routes
                          if getattr(r, "path", "").startswith("/api")}),
    }
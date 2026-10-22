"""数据源路由。

可扩展：当前仅演示库（regional_health）。接入真实数据源时，
在 _discover_datasources() 中追加枚举逻辑即可，契约不变。
"""
import os
import sqlite3

from fastapi import APIRouter, HTTPException

from backend import config
from backend.schemas import (DatasourceInfo, DatasourceSchema,
                             DemoDatasourceResult, DemoLoadRequest)
from backend.security import require_admin

router = APIRouter(prefix="/api/datasources", tags=["datasources"])


def _tables(db_path: str) -> dict:
    """返回 {表名: PRAGMA table_info 行列表}。"""
    con = sqlite3.connect(db_path)
    try:
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {n: list(con.execute(f"PRAGMA table_info({n})")) for n in names}
    finally:
        con.close()


def _discover_datasources() -> list:
    """枚举**注册表内**的数据源（评审 B6 产品化）。

    演示库恒在册；真实库经 MEDGUARD_DATASOURCES 环境变量登记后出现在这里。
    每个源按自己的 db_path 扫描 schema 与就绪门禁（未注册的库不会出现）。
    """
    from backend import datasource_registry as dsr
    from backend import readiness
    from backend.deps import load_policy
    policy_ids = list_policy_ids()
    out = []
    for spec in dsr.specs():
        db_path = dsr.resolve_db_path(spec)
        if not os.path.exists(db_path):
            continue
        tables = _tables(db_path)
        ssa = None
        if spec.id in policy_ids:
            try:
                ssa = load_policy(spec.id)
            except Exception:  # noqa: BLE001 - 策略损坏按未就绪呈现，不让枚举崩
                ssa = None
        report = readiness.scan_readiness(spec.id, ssa, db_path=db_path)
        out.append({
            "id": spec.id,
            "name": spec.name,
            "table_count": len(tables),
            "column_count": sum(len(cols) for cols in tables.values()),
            "policy_ready": spec.id in policy_ids,
            # 就绪门禁（评审 B6）：演示库兼容模式；真实库严格模式下未标注即拒绝。
            "ready": report.ready,
            "unlabeled_columns": report.unlabeled_columns[:20],
            "strict": bool(config.STRICT_DATASOURCES),
        })
    return out


def list_policy_ids():
    from backend.deps import list_policy_ids as _lpi
    return _lpi()


@router.get("", response_model=list[DatasourceInfo])
def list_datasources():
    return _discover_datasources()


@router.post("/demo", response_model=DemoDatasourceResult)
def create_demo(body: DemoLoadRequest):
    """一键载入演示数据（建 5 表 + 灌虚构数据 + 初始化平台元数据库）。

    **必须管理员**：它会**先删掉再重建**业务库（`build_database` 里有
    `os.remove`），所有既有数据没了。管理员在页面上点一下没问题，
    但匿名或局域网里的任何人都能触发就不是"演示便利"而是数据销毁了。
    """
    require_admin(body.token)
    from demo.seed import build_database
    from backend.db import init_db
    os.makedirs(config.DATA_DIR, exist_ok=True)
    build_database(config.BUSINESS_DB)
    init_db(config.METADATA_DB)  # 幂等：报告表已存在时无操作
    return {"id": config.DEMO_DATASOURCE_ID, "created": True}


@router.get("/{datasource_id}/schema", response_model=DatasourceSchema)
def get_schema(datasource_id: str):
    if datasource_id != config.DEMO_DATASOURCE_ID:
        raise HTTPException(status_code=404, detail="数据源不存在")
    if not os.path.exists(config.BUSINESS_DB):
        raise HTTPException(status_code=404, detail="数据源不存在，请先载入演示数据")
    con = sqlite3.connect(config.BUSINESS_DB)
    try:
        tables = []
        for name in _tables(config.BUSINESS_DB):
            cols = [{"name": r[1], "type": r[2], "pk": bool(r[5])}
                    for r in con.execute(f"PRAGMA table_info({name})")]
            tables.append({"name": name, "columns": cols})
    finally:
        con.close()
    return {"tables": tables}
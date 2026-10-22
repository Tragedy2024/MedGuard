"""数据源注册表（评审 B6 产品化，2026-10）。

`datasource_id → 描述（名称 / 数据库路径）` 的权威映射。演示库恒在册、
路径动态取 `config.BUSINESS_DB`；真实医院库用环境变量 `MEDGUARD_DATASOURCES`
（JSON 数组）登记：

    MEDGUARD_DATASOURCES='[{"id":"real_hospital","name":"某院HIS",
                            "db_path":"D:/data/his.db"}]'

登记后即出现在 `/api/datasources`，就绪门禁（readiness）按各自 db_path 扫描
该源的 schema 与 SSA 差距，严格模式下未就绪禁止查询。执行器同样按
spec.db_path 落库（未登记的数据源查询入口直接 404）。
"""
import json
import os
from dataclasses import dataclass
from typing import List, Optional

from backend import config


@dataclass(frozen=True)
class DatasourceSpec:
    """一个数据源的登记信息。"""
    id: str
    name: str
    db_path: Optional[str] = None   # None → 演示库（config.BUSINESS_DB）


def _env_specs() -> List[DatasourceSpec]:
    """从 MEDGUARD_DATASOURCES（JSON 数组）解析真实数据源；坏形状整批忽略。"""
    raw = os.environ.get("MEDGUARD_DATASOURCES", "")
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    out = []
    if not isinstance(data, list):
        return []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        sid = entry.get("id")
        db_path = entry.get("db_path")
        if isinstance(sid, str) and sid.strip() \
                and isinstance(db_path, str) and db_path.strip():
            out.append(DatasourceSpec(
                id=sid.strip(),
                name=str(entry.get("name") or sid).strip(),
                db_path=db_path.strip(),
            ))
    return out


def specs() -> List[DatasourceSpec]:
    """全部在册数据源：演示库恒在第一项，环境登记源去重追加。"""
    out = [DatasourceSpec(id=config.DEMO_DATASOURCE_ID,
                          name=config.DEMO_DATASOURCE_NAME)]
    seen = {out[0].id}
    for spec in _env_specs():
        if spec.id not in seen:
            seen.add(spec.id)
            out.append(spec)
    return out


def get(datasource_id: str) -> Optional[DatasourceSpec]:
    """按 id 查登记；未注册返回 None。"""
    for spec in specs():
        if spec.id == datasource_id:
            return spec
    return None


def resolve_db_path(spec: Optional[DatasourceSpec]) -> Optional[str]:
    """spec 对应的业务库路径；演示库（db_path=None）→ config.BUSINESS_DB。

    None 表示 spec 不存在（应先行 404，不应落到解析）。
    """
    if spec is None:
        return None
    return spec.db_path if spec.db_path is not None else config.BUSINESS_DB
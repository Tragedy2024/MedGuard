"""数据源就绪门禁（评审 B6，2026-10）。

接入真实医院数据源时，未标注的安全策略是静默 fail-open 的最大来源——
算法层对未知列一律 FREE 放行（默认语义）。本模块在**启用前**扫描真实
schema 与 SSA 标注的差距，把"未标注"变成可观测、可拦截的状态：

- 演示库（regional_health）始终为**兼容模式**：不因策略未标全而拦演示；
- 非演示数据源：严格模式（MEDGUARD_STRICT_DATASOURCES=1，生产默认建议
  开启）下，存在未标注表/列即"未就绪"，查询入口直接拒绝——
  不是等查询发生后才在审计里临时拦。

与层一/层二正交：这是"数据源能不能启用"的管理门禁，不是查询级别的
安全判定。
"""
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from backend import config


@dataclass
class ReadinessReport:
    """一次 schema-vs-SSA 扫描的结果。"""
    ready: bool
    unlabeled_tables: List[str] = field(default_factory=list)
    unlabeled_columns: List[str] = field(default_factory=list)
    missing_ssa: bool = False


def scan_readiness(datasource_id: str, ssa,
                  db_path: Optional[str] = None) -> ReadinessReport:
    """扫业务库真实 schema，对照 SSA 标注，列出未标注（乃至未建策略）的表/列。

    `ssa` 为空（策略不存在）时视为**整库未就绪**——连策略都没有的库，
    严格模式下不该被启用；演示库有兼容豁免（见 allowed）。

    `db_path`（评审 P2）：扫哪个库由调用方显式指定——多数据源接入后，
    每个源传自己的连接路径；默认演示业务库。`datasource_id` 用于多源
    扩展（报告归属），当前单源实现下扫描目标以 db_path 为准。
    """
    path = db_path or config.BUSINESS_DB
    report = ReadinessReport(ready=False, missing_ssa=(ssa is None))
    if ssa is None:
        return report

    if not os.path.exists(path):
        return report                       # 库还没载入，无从扫描
    con = sqlite3.connect(path)
    try:
        for (table,) in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"):
            cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
            labeled = ssa.columns_for_table(table)
            missing_table = labeled is None or not labeled
            if missing_table or not cols:
                if not cols:
                    continue                # 空表没有实际列，不产生门禁
                report.unlabeled_tables.append(table)
                report.unlabeled_columns.extend(
                    f"{table}.{c}" for c in sorted(cols))
                continue
            for col in sorted(cols):
                if col.lower() not in {k.lower() for k in labeled}:
                    report.unlabeled_columns.append(f"{table}.{col}")
    finally:
        con.close()

    report.ready = not (report.unlabeled_tables or report.unlabeled_columns)
    return report


def allowed(datasource_id: str, ssa, db_path: Optional[str] = None) -> bool:
    """该数据源当前是否允许启用/查询。

    演示库恒放行（兼容模式）；非演示库在严格模式开启时要求扫描就绪，
    严格模式关闭时维持旧的宽松行为（策略可事后补标）。db_path 语义
    同 scan_readiness。
    """
    if datasource_id == config.DEMO_DATASOURCE_ID:
        return True
    if not config.STRICT_DATASOURCES:
        return True
    return scan_readiness(datasource_id, ssa, db_path=db_path).ready
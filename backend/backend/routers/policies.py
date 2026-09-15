"""安全策略路由。

策略以 YAML 形式存放在 demo/ssa/（每数据源一份），本路由读写该文件。
可扩展：接入真实数据源时只需让 POLICIES_DIR 指向对应策略目录。
"""
import os
import sqlite3
from typing import Any, Dict, List, Optional

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend import config
from backend.deps import list_policy_ids, load_policy

router = APIRouter(prefix="/api/policies", tags=["policies"])


class PolicyUpdate(BaseModel):
    column_labels: Optional[Dict[str, Dict[str, str]]] = None
    cross_domain_rules: Optional[List[Dict[str, Any]]] = None


def _policy_path(datasource_id: str) -> str:
    return os.path.join(config.SSA_DIR, f"{datasource_id}.yaml")


def _read_policy_yaml(datasource_id: str) -> dict:
    p = _policy_path(datasource_id)
    if not os.path.exists(p):
        raise HTTPException(status_code=404, detail="策略不存在")
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _review_status(labels: Dict[str, Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    """对比库中实际列与策略标注，标出未标注的列。

    **为什么这个字段是安全相关的**：SSALabels.get() 对未标注的列返回
    FREE（fail-open）。因此"库里有一列、策略里没有它"意味着医盾会
    **静默放行**该列——这是策略腐烂的具体形态，不是理论担忧。
    """
    if not os.path.exists(config.BUSINESS_DB):
        return {}
    con = sqlite3.connect(config.BUSINESS_DB)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'")]
        status: Dict[str, Dict[str, str]] = {}
        for t in tables:
            cols = [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
            known = {k.lower() for k in labels.get(t, {})}
            status[t] = {
                c: ("labeled" if c.lower() in known else "unlabeled")
                for c in cols
            }
        return status
    finally:
        con.close()


@router.get("")
def list_policies():
    """所有已定案策略的 id 列表（供数据源页展示）。"""
    return {"datasource_ids": list_policy_ids()}


@router.get("/{datasource_id}")
def get_policy(datasource_id: str):
    data = _read_policy_yaml(datasource_id)

    labels = data.get("column_labels", {}) or {}
    return {
        "datasource_id": datasource_id,
        "column_labels": labels,
        "column_reasons": data.get("column_reasons", {}) or {},
        "cross_domain_rules": data.get("cross_domain_rules", []) or [],
        "review_status": _review_status(labels),
    }


@router.put("/{datasource_id}")
def update_policy(datasource_id: str, body: PolicyUpdate):
    """更新策略。只改 YAML 中明确提交的键，其余保留。"""
    data = _read_policy_yaml(datasource_id)

    if body.column_labels:
        labels = data.setdefault("column_labels", {})
        for table, cols in body.column_labels.items():
            labels.setdefault(table, {}).update(cols)
    if body.cross_domain_rules is not None:
        data["cross_domain_rules"] = body.cross_domain_rules

    with open(_policy_path(datasource_id), "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    return {"saved": True, "datasource_id": datasource_id}


@router.get("/{datasource_id}/validate")
def validate_policy(datasource_id: str):
    """校验策略可被算法层正确加载（健康检查/接入新数据源时用）。"""
    try:
        ssa = load_policy(datasource_id)
        return {
            "ok": True,
            "datasource_id": datasource_id,
            "tables": len(ssa.column_labels),
            "columns": sum(len(c) for c in ssa.column_labels.values()),
            "cross_domain_rules": len(ssa.cross_domain_rules),
        }
    except Exception as exc:  # noqa: BLE001 - 校验失败返回可读信息
        return {"ok": False, "datasource_id": datasource_id, "error": str(exc)}
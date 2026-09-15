"""演示库 SSA 策略测试。

策略是手动定案的安全资产：任何一列漏标都会 fail-open（静默放行），
因此这里逐列断言关键标签 + 未标注列为空。
"""
import os

from backend import config
from backend.deps import load_policy


def _labels():
    return load_policy(config.DEMO_DATASOURCE_ID)


def test_policy_loads():
    ssa = _labels()
    assert ssa.db_id == "regional_health"
    assert len(ssa.column_labels) == 5


def test_blocked_columns():
    ssa = _labels()
    assert ssa.get("patients.id_card") == "blocked"
    assert ssa.get("clinical_records.icd_code") == "blocked"
    assert ssa.get("clinical_records.diagnosis_name") == "blocked"


def test_controlled_columns():
    ssa = _labels()
    assert ssa.get("patients.name") == "controlled"
    assert ssa.get("patients.phone") == "controlled"
    assert ssa.get("staff.phone") == "controlled"
    assert ssa.get("billing.amount") == "controlled"
    assert ssa.get("clinical_records.impression") == "controlled"


def test_free_columns():
    ssa = _labels()
    assert ssa.get("patients.gender") == "free"
    assert ssa.get("staff.department") == "free"
    assert ssa.get("clinical_records.drug_name") == "free"
    assert ssa.get("clinical_records.test_name") == "free"


def test_cross_domain_rule_exists():
    """跨域规则必须手写——论文仓库 31 个库的跨域规则实际生效数为 0。"""
    ssa = _labels()
    assert len(ssa.cross_domain_rules) >= 1
    rule = ssa.cross_domain_rules[0]
    assert set(rule.table_pair) == {"clinical_records", "billing"}
    assert rule.join_key == "visit_id"
    assert rule.forbid_personal_level is True


def test_every_demo_column_is_labeled():
    """演示库的每一列都必须有 ECL 标注（防 fail-open 缺口）。

    对照演示库真实列与 YAML 标注。漏标一列 = 医盾静默放行该列。
    """
    import sqlite3
    import tempfile

    from demo.seed import build_database

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    build_database(path)
    con = sqlite3.connect(path)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'")]
        ssa = _labels()
        unlabeled = []
        for t in tables:
            cols = [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
            known = {k.lower() for k in ssa.column_labels.get(t, {})}
            for c in cols:
                if c.lower() not in known:
                    unlabeled.append(f"{t}.{c}")
    finally:
        con.close()
        os.unlink(path)

    assert unlabeled == [], f"以下列未标注（将被静默放行）：{unlabeled}"
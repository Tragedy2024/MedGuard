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


def _yaml_data() -> dict:
    """直接读策略 YAML——别名是产品层字段，不进 SSA 对象。"""
    import yaml

    path = os.path.join(config.SSA_DIR, f"{config.DEMO_DATASOURCE_ID}.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def test_policy_yaml_loads_with_product_layer_fields():
    """YAML 含 table_aliases / column_aliases 时，算法层仍能正常加载。

    规范 §5.2：load_ssa 只读 column_labels 与 cross_domain_rules，未知键
    静默忽略——本测试是「加产品层字段零算法改动」这条承诺的可验证形式。
    列数若变化，说明别名被误当成标注读进去了。
    """
    ssa = _labels()
    total = sum(len(cols) for cols in ssa.column_labels.values())
    assert ssa.db_id == "regional_health"
    assert total == 46, f"预期 46 列标注，实际 {total}（别名是否被误读为标注？）"


def test_every_demo_column_has_alias():
    """演示库的每一张表、每一列都必须有中文别名。

    与 ECL 标注的失败后果不同，但同样硬：本产品的用户是医护与病患，
    不是数据库管理员。界面上直接出现 `frequency` / `clinical_records`
    这类物理名，产品就退回成「开发者工具」——正是会议记录 §1.1 已否决
    的形态。漏别名的后果是产品定位崩塌，所以同样用测试拦住，而不是
    靠前端回落到物理名悄悄兜底。
    """
    import sqlite3
    import tempfile

    from demo.seed import build_database

    data = _yaml_data()
    table_aliases = data.get("table_aliases") or {}
    column_aliases = data.get("column_aliases") or {}

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    build_database(path)
    con = sqlite3.connect(path)
    missing = []
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'")]
        for t in tables:
            if not table_aliases.get(t):
                missing.append(f"表 {t}")
            for c in (r[1] for r in con.execute(f"PRAGMA table_info({t})")):
                if not (column_aliases.get(t) or {}).get(c):
                    missing.append(f"{t}.{c}")
    finally:
        con.close()
        os.unlink(path)

    assert missing == [], f"以下表/列缺少中文别名（界面会显示物理名）：{missing}"
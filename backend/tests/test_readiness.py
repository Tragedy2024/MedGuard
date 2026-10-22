"""数据源就绪门禁测试（评审 B6，2026-10）。

门禁语义：接入真实数据源时，schema 与 SSA 标注的差距必须在**启用前**
可见、可拦（未标注列对算法层是 fail-open）。演示库恒为兼容模式。
"""
import sqlite3

from backend import config
from backend.deps import load_policy
from backend.readiness import allowed, scan_readiness


def _make_db(path, tables: dict) -> None:
    con = sqlite3.connect(str(path))
    for t, cols in tables.items():
        col_sql = ", ".join(f"{c} TEXT" for c in cols)
        con.execute(f"CREATE TABLE {t} ({col_sql})")
    con.commit()
    con.close()


def _ssa(table_prefix: str):
    """一个只标注了 dev_table 的策略对象（未标注表 extra_table 等）。"""
    from ssa.loader import SSALabels
    return SSALabels(db_id=table_prefix, column_labels={
        "dev_table": {"name": "controlled", "amount": "controlled"},
    })


def test_demo_database_is_ready(tmp_path, monkeypatch):
    """演示库 5 张表全部标注 → 扫描就绪（兼容模式的数据基础）。"""
    from backend.deps import load_policy
    ssa = load_policy("regional_health")
    report = scan_readiness("regional_health", ssa)
    assert report.ready is True
    assert report.unlabeled_tables == []
    assert report.unlabeled_columns == []


def test_unlabeled_table_and_column_reported(tmp_path, monkeypatch):
    """真实库多了未标注的表/列 → 明确列出，ready=False。"""
    monkeypatch.setattr(config, "BUSINESS_DB", str(tmp_path / "real.db"))
    _make_db(tmp_path / "real.db", {
        "dev_table": ["name", "amount"],
        "extra_table": ["note", "code"],
    })
    report = scan_readiness("real_hospital", _ssa("real_hospital"))
    assert report.ready is False
    assert "extra_table" in report.unlabeled_tables
    assert "extra_table.note" in report.unlabeled_columns
    assert "extra_table.code" in report.unlabeled_columns


def test_unlabeled_column_in_known_table(tmp_path, monkeypatch):
    """已标注的表里新增了未标注列 → 也要报出（fail-open 缺口）。"""
    monkeypatch.setattr(config, "BUSINESS_DB", str(tmp_path / "real2.db"))
    _make_db(tmp_path / "real2.db", {"dev_table": ["name", "amount", "memo"]})
    report = scan_readiness("real_hospital", _ssa("real_hospital"))
    assert report.ready is False
    assert "dev_table.memo" in report.unlabeled_columns


def test_missing_ssa_is_not_ready(tmp_path, monkeypatch):
    """连策略都没有的库 → 未就绪（严格模式下一律不可启用）。"""
    report = scan_readiness("brand_new", None)
    assert report.ready is False
    assert report.missing_ssa is True


def test_allowed_matrix(tmp_path, monkeypatch):
    """门禁组合：演示恒放行 / 严格下非演示须就绪 / 非严格维持宽松。"""
    from backend import config as _c

    monkeypatch.setattr(config, "BUSINESS_DB", str(tmp_path / "m.db"))
    _make_db(tmp_path / "m.db", {"dev_table": ["name", "amount"],
                                 "extra_table": ["note"]})

    # 演示库：严格模式开启也一样放行（兼容模式）
    monkeypatch.setattr(_c, "STRICT_DATASOURCES", True)
    assert allowed(_c.DEMO_DATASOURCE_ID, None) is True
    # 非演示 + 严格 + 未标注 → 拒绝
    assert allowed("real_hospital", _ssa("real_hospital")) is False
    # 非演示 + 严格 + 已就绪（只标了 dev_table，且库里只有它）→ 放行
    monkeypatch.setattr(config, "BUSINESS_DB", str(tmp_path / "m2.db"))
    _make_db(tmp_path / "m2.db", {"dev_table": ["name", "amount"]})
    assert allowed("real_hospital", _ssa("real_hospital")) is True
    # 非演示 + 非严格 → 维持宽松（策略可事后补标）
    monkeypatch.setattr(_c, "STRICT_DATASOURCES", False)
    monkeypatch.setattr(config, "BUSINESS_DB", str(tmp_path / "m.db"))
    assert allowed("real_hospital", _ssa("real_hospital")) is True

# ── 注册表（评审 B6 产品化）───────────────────────────────────

def test_registry_parses_env_sources(monkeypatch):
    """MEDGUARD_DATASOURCES 登记的真实源进入注册表，演示库恒在首。"""
    from backend import datasource_registry as dsr
    monkeypatch.setenv(
        "MEDGUARD_DATASOURCES",
        '[{"id": "real_hospital", "name": "某院HIS", "db_path": "D:/data/his.db"}]')
    specs = dsr.specs()
    assert specs[0].id == config.DEMO_DATASOURCE_ID
    real = dsr.get("real_hospital")
    assert real is not None and real.db_path == "D:/data/his.db"
    assert real.name == "某院HIS"
    assert dsr.get("ghost") is None


def test_registry_ignores_bad_env(monkeypatch):
    """坏形状的环境变量整批忽略，只剩演示库。"""
    from backend import datasource_registry as dsr
    monkeypatch.setenv("MEDGUARD_DATASOURCES", "not-json")
    assert [s.id for s in dsr.specs()] == [config.DEMO_DATASOURCE_ID]


def test_resolve_db_path_demo_follows_business(monkeypatch):
    """演示库的 db_path = config.BUSINESS_DB（None 哨兵，动态解析）。"""
    from backend import datasource_registry as dsr
    monkeypatch.setattr(config, "BUSINESS_DB", "Z:/demo.db")
    assert dsr.resolve_db_path(dsr.get(config.DEMO_DATASOURCE_ID)) == "Z:/demo.db"
    assert dsr.resolve_db_path(dsr.get("real_hospital")) is None


def test_readiness_scans_registered_db_path(monkeypatch, tmp_path):
    """扫描器按登记源的 db_path 扫（评审 P2：不再是恒扫演示库）。"""
    from backend import datasource_registry as dsr
    from backend.readiness import scan_readiness
    other = tmp_path / "his.db"
    _make_db(other, {"appt": ["id", "patient_id"]})
    monkeypatch.setenv(
        "MEDGUARD_DATASOURCES",
        __import__("json").dumps(
            [{"id": "real_hospital", "name": "HIS", "db_path": str(other)}]))
    spec = dsr.get("real_hospital")
    report = scan_readiness(spec.id, None, db_path=dsr.resolve_db_path(spec))
    assert report.ready is False and report.missing_ssa is True

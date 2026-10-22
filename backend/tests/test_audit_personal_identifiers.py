"""A3 回归：医疗个人标识列必须参与跨域个人级 JOIN 判定（2026-10 评审）。

背景（本文件为什么存在）：算法层的个人属性启发式（ssa/ecl.py）是英文命名
清单（name / email / customer_id …），不含 patient_id / id_card 等医疗标识。
复现过的静默漏报：

    SELECT c.patient_id, b.amount
    FROM clinical_records c JOIN billing b ON c.visit_id = b.visit_id

is_personal_attribute("clinical_records.patient_id") = False → 跨域个人级
JOIN 判定为 False → SSA 的 forbid_personal_level 规则形同虚设，结果 L0 放行。

修复（见 VENDOR-PATCHES.md 第 3 节）：SSA 显式声明 personal_identifier_columns，
判定改为「启发式命中 **或** 显式声明」——纯增量，未声明时行为与上游一致。
"""
from backend.deps import audit_plan, load_policy

# 评审给出的复现 SQL：跨域规则命中表对（clinical_records↔billing,
# join_key=visit_id, forbid_personal_level=true），但 SELECT 里用的是
# 医疗主键 patient_id 而非英文可猜的名字。
_PERSONAL_JOIN_SQL = (
    "SELECT c.patient_id, b.amount "
    "FROM clinical_records c JOIN billing b ON c.visit_id = b.visit_id"
)


def test_demo_policy_declares_medical_identifiers():
    """演示策略必须显式声明患者侧标识列（而不是靠英文名猜测）。"""
    ssa = load_policy("regional_health")
    assert "patients.patient_id" in ssa.personal_identifier_columns
    assert "patients.id_card" in ssa.personal_identifier_columns


def test_declared_identifier_matching():
    """声明语义（评审 P2）：全限定声明只精确匹配，不跨表误报。"""
    ssa = load_policy("regional_health")
    # 全限定：精确命中
    assert ssa.is_personal_identifier("clinical_records.patient_id")
    assert ssa.is_personal_identifier("billing.patient_id")
    # 全限定声明**不**全局匹配：别的表的同名列不算个人标识（评审 P2：
    # 跨表误报只产生不必要的 L2 降级，不产生泄露——但不应发生）
    assert not ssa.is_personal_identifier("unrelated.patient_id")
    # 声明的是全限定名 → 裸列名也不命中（裸列名只有裸声明才全局匹配）
    assert not ssa.is_personal_identifier("patient_id")
    # 不区分大小写 / 反引号
    assert ssa.is_personal_identifier("`patients`.`id_card`")
    # 系统内部键与普通列不得判为个人标识
    assert not ssa.is_personal_identifier("visits.visit_id")
    assert not ssa.is_personal_identifier("clinical_records.record_id")
    assert not ssa.is_personal_identifier("billing.amount")


def test_bare_declaration_matches_any_table():
    """裸列名声明（无表名）才是全局语义：任何表的同名列都算。"""
    from ssa.loader import SSALabels
    ssa = SSALabels(db_id="t", personal_identifier_columns={"id_card"})
    assert ssa.is_personal_identifier("patients.id_card")
    assert ssa.is_personal_identifier("unrelated.id_card")
    assert not ssa.is_personal_identifier("patients.patient_id")


def test_medical_personal_join_is_caught():
    """回归核心：patient_id 的个人级跨域 JOIN 必须被检出并降级。"""
    out = audit_plan(
        [{"id": 0, "description": "取患者编号与费用", "sql": _PERSONAL_JOIN_SQL}],
        "regional_health",
    )
    types = {v["type"] for v in out.violations}
    assert "cross_domain_personal_join" in types
    # 个人级关联不能给逐行答案：降级为聚合替代（Rule C 拆分）
    assert out.degradation_level == "L2"
    # Rule C 把一条 JOIN 拆成两条独立聚合查询
    assert len(out.audited_plan) >= 2
    # 拆分后的查询不得再含 JOIN
    for sq in out.audited_plan:
        assert " JOIN " not in sq["sql"].upper().replace(" JOIN\n", " JOIN ")


def test_aggregated_cross_domain_stays_allowed():
    """聚合形态的跨域查询不应误报（allow_aggregate_level 语义保留）。"""
    out = audit_plan(
        [{"id": 0, "description": "统计平均费用",
          "sql": ("SELECT COUNT(*) AS n, AVG(b.amount) AS avg_amt "
                  "FROM clinical_records c "
                  "JOIN billing b ON c.visit_id = b.visit_id")}],
        "regional_health",
    )
    assert "cross_domain_personal_join" not in {v["type"] for v in out.violations}


def test_unrelated_join_not_flagged():
    """不涉及个人标识的跨表查询不受新声明影响。"""
    out = audit_plan(
        [{"id": 0, "description": "看各科室接诊量",
          "sql": ("SELECT v.department, COUNT(*) AS cnt "
                  "FROM visits v JOIN staff s ON v.doctor_id = s.staff_id "
                  "GROUP BY v.department")}],
        "regional_health",
    )
    assert "cross_domain_personal_join" not in {v["type"] for v in out.violations}
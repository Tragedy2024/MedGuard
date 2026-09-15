"""算法层适配测试。"""
from backend import config
from backend.deps import algo_available, audit_plan, load_policy


def test_algo_engine_available():
    """算法层（医盾引擎）必须可导入——这是后端的地基。"""
    assert algo_available() is True


def test_load_policy_returns_labels():
    ssa = load_policy("regional_health")
    assert ssa.db_id == "regional_health"
    assert ssa.get("patients.id_card") == "blocked"


def test_audit_plan_clean_passes():
    """最后一条子查询是最终答案，controlled 列合法。"""
    plan = [
        {"id": 0, "description": "聚合", "sql": "SELECT COUNT(*) FROM patients"},
    ]
    out = audit_plan(plan, "regional_health")
    assert out.degradation_level == "L0"
    assert out.violations == []


def test_audit_plan_detects_intermediate_exposure():
    """中间子查询 SELECT 受控列应被检出。

    注意：最后一个子查询的 controlled 列永不违规（is_final 豁免），
    因此必须构造「中间子查询 + 最终子查询」两段式计划。
    """
    plan = [
        {"id": 0, "description": "中间：取患者姓名",
         "sql": "SELECT p.name, p.phone FROM patients p"},
        {"id": 1, "description": "最终：计数",
         "sql": "SELECT COUNT(*) FROM patients"},
    ]
    out = audit_plan(plan, "regional_health")
    assert len(out.violations) >= 1
    types = {v["type"] for v in out.violations}
    assert "column_unnecessary_exposure" in types
    # 违规详情必须带这些字段（前端事件卡片依赖）
    v = out.violations[0]
    assert {"type", "type_label", "column", "severity",
            "severity_label", "detail"} <= set(v)
    assert out.rewrites_applied >= 1


def test_audit_plan_blocked_column_triggers_degradation():
    """blocked 列出现在 SELECT（含最终子查询）必须降级。"""
    plan = [
        {"id": 0, "description": "最终：导出身份证",
         "sql": "SELECT id_card FROM patients"},
    ]
    out = audit_plan(plan, "regional_health")
    assert len(out.violations) >= 1
    assert out.degradation_level in ("L2", "L3")


def test_audit_plan_cross_domain_triggers_rule_c():
    """临床↔费用个人级 JOIN 必须触发跨域违规并进入 Rule C 拆分路径。"""
    plan = [
        {"id": 0, "description": "中间：跨域 JOIN",
         "sql": "SELECT c.patient_id, c.diagnosis_name, b.amount "
                "FROM clinical_records c JOIN billing b "
                "ON c.visit_id = b.visit_id "
                "WHERE c.record_type = 'diagnosis'"},
        {"id": 1, "description": "最终：汇总费用",
         "sql": "SELECT SUM(amount) FROM billing"},
    ]
    out = audit_plan(plan, "regional_health")
    types = {v["type"] for v in out.violations}
    assert "cross_domain_personal_join" in types
    assert out.degradation_level in ("L0", "L1", "L2")
    # Rule C 拆分后最近的子查询应无 JOIN
    for sq in out.audited_plan:
        assert " JOIN " not in sq["sql"].upper() or sq["id"] == 1


def test_final_controlled_column_not_avg_wrapped():
    """最终答案的 controlled 列不得被 AVG 误伤（算法层 is_final 豁免的适配）。

    单查询计划 = 最终答案：审计器豁免 controlled 裸列，但算法层 Rule A
    的 AVG 包裹没有该豁免，产品层必须还原，否则「导出患者信息」会变成
    SELECT patient_id, AVG(name)。
    """
    plan = [
        {"id": 0, "description": "最终：导出患者信息",
         "sql": "SELECT patient_id, name, id_card FROM patients"},
    ]
    out = audit_plan(plan, "regional_health")
    assert len(out.audited_plan) == 1
    final_sql = out.audited_plan[0]["sql"].lower()
    assert "id_card" not in final_sql, "blocked 列必须被移除"
    assert "avg(name)" not in final_sql, "controlled 列被 AVG 误伤"
    assert "name" in final_sql


def test_true_aggregate_final_is_not_restored():
    """用户本来就要平均值（原始 SQL 即 AVG）时不得被还原逻辑破坏。"""
    plan = [
        {"id": 0, "description": "最终：平均费用",
         "sql": "SELECT AVG(amount) FROM billing"},
    ]
    out = audit_plan(plan, "regional_health")
    assert out.degradation_level == "L0"
    assert "avg(amount)" in out.audited_plan[0]["sql"].lower()


def test_no_ssa_policy_is_fail_open_l0():
    """不存在的策略（如尚未接入的真实数据源）按无 SSA 处理，不崩溃。"""
    out = audit_plan(
        [{"id": 0, "sql": "SELECT 1"}], "not_a_real_datasource")
    assert out.degradation_level == "L0"
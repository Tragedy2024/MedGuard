"""层一准入判定测试。"""
import pytest

from backend.admission import admission_check, admission_check_plan


def test_bound_to_self_is_allowed():
    # 注意：演示库用 clinical_records 承载检验记录（原计划文档笔误 lab_tests）
    r = admission_check(
        "SELECT * FROM clinical_records WHERE patient_id = 'P001'", "P001")
    assert r.passed is True
    assert r.bound_to_subject is True
    assert r.reason is None


def test_patient_table_without_binding_is_denied():
    r = admission_check("SELECT COUNT(*) FROM patients", "P001")
    assert r.passed is False
    assert r.reason == "该查询涉及其他患者信息，无法提供。"


def test_bound_to_other_patient_is_denied():
    """关键用例：绑定到别的患者也必须拒绝。"""
    r = admission_check("SELECT * FROM patients WHERE patient_id = 'P002'", "P001")
    assert r.passed is False
    assert r.bound_to_subject is False


def test_non_patient_table_is_allowed():
    r = admission_check(
        "SELECT name, title FROM staff WHERE department = '心内科'", "P001")
    assert r.passed is True
    assert r.checked_tables == ["staff"]


def test_clinical_records_without_binding_is_denied():
    r = admission_check("SELECT * FROM clinical_records", "P001")
    assert r.passed is False


def test_malformed_sql_is_denied_fail_closed():
    """解析失败必须拒绝（fail-closed），不能放行。"""
    r = admission_check("SELECT FROM WHERE ((", "P001")
    assert r.passed is False
    assert r.reason == "该查询涉及其他患者信息，无法提供。"


def test_plan_any_subquery_denied_rejects_all():
    """计划中任一条子查询越权 → 整体拒绝。"""
    plan = [
        {"id": 0, "sql": "SELECT test_name FROM clinical_records "
                         "WHERE patient_id = 'P001'"},
        {"id": 1, "sql": "SELECT COUNT(*) FROM patients"},
    ]
    r = admission_check_plan(plan, "P001")
    assert r.passed is False
    assert r.reason == "该查询涉及其他患者信息，无法提供。"


def test_plan_all_bound_passes():
    plan = [
        {"id": 0, "sql": "SELECT drug_name FROM clinical_records "
                         "WHERE patient_id = 'P001'"},
        {"id": 1, "sql": "SELECT COUNT(*) FROM clinical_records "
                         "WHERE patient_id = 'P001'"},
    ]
    r = admission_check_plan(plan, "P001")
    assert r.passed is True
    assert r.bound_to_subject is True


def test_plan_non_patient_tables_passes_without_binding():
    plan = [{"id": 0, "sql": "SELECT department FROM staff"}]
    r = admission_check_plan(plan, "P001")
    assert r.passed is True
    assert r.bound_to_subject is False
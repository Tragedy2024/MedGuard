"""层一：准入判定（纯函数，无 IO、无 LLM、无数据库访问）。

规则（全称）：病患令牌的查询若引用了患者数据表，必须绑定到令牌自身的
patient_id，否则拒绝。不判断聚合结果能否反推个体——那不可判定（需知
结果基数，而基数要查库才知道）。

设计要点：
- 表集合从 backend.config 读取（可扩展：接入真实数据源时按数据源配置）。
- 解析失败一律 fail-closed（拒绝），保证"解析不了就不放行"。
"""
import sqlglot
import sqlglot.expressions as exp

from backend import config

# 统一拒答文案（个人数据与统计数据不作区分，避免经错误信息差异反推数据存在性）
DENY_REASON = "该查询涉及其他患者信息，无法提供。"


class AdmissionResult:
    """一条（或一个计划的）准入判定结果。

    passed:      是否放行
    reason:      拒绝原因（放行时为 None）
    checked_tables: 本次判定实际检查过的表名（去重、排序）
    bound_to_subject: 是否检测到 patient_id = <令牌绑定值> 的绑定
    """

    __slots__ = ("passed", "reason", "checked_tables", "bound_to_subject")

    def __init__(self, passed, reason=None, checked_tables=None,
                 bound_to_subject=False):
        self.passed = passed
        self.reason = reason
        self.checked_tables = list(checked_tables or [])
        self.bound_to_subject = bool(bound_to_subject)

    def to_dict(self):
        return {
            "passed": self.passed,
            "reason": self.reason,
            "checked_tables": self.checked_tables,
            "bound_to_subject": self.bound_to_subject,
        }


def admission_check(sql: str, subject_id: str,
                    patient_tables=None) -> AdmissionResult:
    """判定病患令牌能否发起此查询。

    Args:
        sql: 单条子查询的 SQL
        subject_id: 令牌绑定的患者 ID
        patient_tables: 患者数据表集合（默认取 config.PATIENT_TABLES，
            测试或接入新数据源时可传入覆盖）

    Returns:
        AdmissionResult。解析失败时 fail-closed（拒绝）。
    """
    if patient_tables is None:
        patient_tables = config.PATIENT_TABLES

    try:
        tree = sqlglot.parse_one(sql, read="sqlite")
    except Exception:
        # fail-closed：解析不了就不放行
        return AdmissionResult(passed=False, reason=DENY_REASON)

    tables = sorted({t.name.lower() for t in tree.find_all(exp.Table)})
    touches = [t for t in tables if t in patient_tables]

    if not touches:
        return AdmissionResult(
            passed=True, checked_tables=tables, bound_to_subject=False,
        )

    for eq in tree.find_all(exp.EQ):
        for col, val in ((eq.this, eq.expression), (eq.expression, eq.this)):
            if isinstance(col, exp.Column) and col.name.lower() == "patient_id":
                if isinstance(val, exp.Literal) and val.this == subject_id:
                    return AdmissionResult(
                        passed=True,
                        checked_tables=tables,
                        bound_to_subject=True,
                    )

    return AdmissionResult(
        passed=False, reason=DENY_REASON, checked_tables=tables,
    )


def admission_check_plan(plan: list, subject_id: str,
                         patient_tables=None) -> AdmissionResult:
    """对整个查询计划判定：任一条子查询不通过则整体拒绝。

    Args:
        plan: [{id, description, sql}, ...]
        subject_id: 令牌绑定的患者 ID
    """
    checked = set()
    bound = False
    for sq in plan:
        r = admission_check(sq.get("sql", ""), subject_id,
                            patient_tables=patient_tables)
        checked.update(r.checked_tables)
        bound = bound or r.bound_to_subject
        if not r.passed:
            return AdmissionResult(
                passed=False, reason=r.reason,
                checked_tables=sorted(checked), bound_to_subject=False,
            )
    return AdmissionResult(
        passed=True, checked_tables=sorted(checked),
        bound_to_subject=bound,
    )
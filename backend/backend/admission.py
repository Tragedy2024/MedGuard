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


def _read_tree(sql):
    try:
        statements = sqlglot.parse(sql, read="sqlite")
        if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.Union, exp.Intersect, exp.Except)):
            return None
        tree = statements[0]
        if tree.find(exp.Into):
            return None
        if any(isinstance(f, exp.Anonymous) and f.name.lower() in {"load_extension", "writefile", "readfile"}
               for f in tree.find_all(exp.Func)):
            return None
        return tree
    except Exception:
        return None


def is_read_only_query(sql):
    """只允许一个查询语句；执行器另用只读连接作第二层约束。"""
    return _read_tree(sql) is not None


def _guarantees_self(node, alias, subject, unqualified_allowed):
    if isinstance(node, (exp.Where, exp.Paren)):
        return _guarantees_self(node.this, alias, subject, unqualified_allowed)
    if isinstance(node, exp.And):
        return (_guarantees_self(node.this, alias, subject, unqualified_allowed)
                or _guarantees_self(node.expression, alias, subject, unqualified_allowed))
    if isinstance(node, exp.Or):
        return (_guarantees_self(node.this, alias, subject, unqualified_allowed)
                and _guarantees_self(node.expression, alias, subject, unqualified_allowed))
    if isinstance(node, exp.EQ):
        for col, val in ((node.this, node.expression), (node.expression, node.this)):
            if (isinstance(col, exp.Column) and col.name.lower() == "patient_id"
                    and (col.table.lower() == alias or (not col.table and unqualified_allowed))
                    and isinstance(val, exp.Literal) and val.is_string and val.this == subject):
                return True
    # 不从 NOT、EXISTS、SELECT投影或嵌套子查询中借用绑定。
    return False


def _join_equalities(node):
    if isinstance(node, exp.Paren):
        return _join_equalities(node.this)
    if isinstance(node, exp.And):
        return _join_equalities(node.this) + _join_equalities(node.expression)
    return [node] if isinstance(node, exp.EQ) else []


def _scope_bound(select, subject, patient_tables):
    local = [t for t in select.find_all(exp.Table)
             if t.find_ancestor(exp.Select) is select]
    physical = {t.alias_or_name.lower(): t.name.lower() for t in local
                if t.name.lower() in patient_tables}
    if not physical:
        return True
    where = select.args.get("where")
    bound = {alias for alias in physical
             if _guarantees_self(where, alias, subject, len(local) == 1)}
    edges = []
    predicates = [where.this] if where else []
    predicates.extend(j.args.get("on") for j in select.args.get("joins", []))
    for predicate in predicates:
        for eq in _join_equalities(predicate):
            a, b = eq.this, eq.expression
            if not isinstance(a, exp.Column) or not isinstance(b, exp.Column):
                continue
            aa, bb = a.table.lower(), b.table.lower()
            if aa not in physical or bb not in physical or aa == bb:
                continue
            # patient_id等值连接可传递主体绑定；visit_id连接仅认当前演示模式
            # 中的已知归属关系，不向任意表或其他ID列外推。
            same_patient = a.name.lower() == b.name.lower() == "patient_id"
            same_visit = (a.name.lower() == b.name.lower() == "visit_id"
                          and set((physical[aa], physical[bb])) in (
                              {"clinical_records", "visits"}, {"billing", "visits"}))
            if same_patient or same_visit:
                edges.append((aa, bb))
    changed = True
    while changed:
        old = len(bound)
        for a, b in edges:
            if a in bound or b in bound:
                bound.update((a, b))
        changed = len(bound) != old
    return set(physical) <= bound


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

    patient_tables = {name.lower() for name in patient_tables}
    tree = _read_tree(sql)
    if tree is None:
        return AdmissionResult(passed=False, reason=DENY_REASON)
    tables = sorted({t.name.lower() for t in tree.find_all(exp.Table)})
    touches = bool(set(tables) & patient_tables)
    if not touches:
        return AdmissionResult(passed=True, checked_tables=tables)
    passed = bool(subject_id) and all(
        _scope_bound(select, subject_id, patient_tables)
        for select in tree.find_all(exp.Select))
    return AdmissionResult(passed=passed, reason=None if passed else DENY_REASON,
                           checked_tables=tables, bound_to_subject=passed)


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
"""算法层适配——本项目访问 NL2SQL（医盾引擎）的唯一入口。

约束（团队规范）：
- 后端其他模块不得直接 import security_auditor / ssa.loader / auditor，
  一律经由本模块。
- 算法层一行不改：只 import、只调用，靠 sys.path 指向论文仓库的 src
  （优先已安装的包，其次相对路径探测，仓库挪位置无需改代码）。

封装了算法层的三个已知坑（设计文档 §5.1，实测确认）：
1. `run_security_auditor_pipeline` 的 audit_report 有三种形状，有违规时
   只有整数计数（violations_before/violations_after），没有 violations 数组
2. 违规详情必须另行调用 `SecurityAuditor.audit_all()` 获取
3. rewrite_log 是字符串数组

对外接口保持稳定（load_policy / audit_plan），路由只依赖本模块。
"""
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Set

from backend import config
from backend.labels import (PARSE_FAILED_LABEL, PARSE_FAILED_MESSAGE,
                            SEVERITY_LABELS, VIOLATION_LABELS)


def _ensure_algo_on_path() -> None:
    """把算法层 src 放进 sys.path（幂等）。

    顺序：已安装的包 > 环境变量指定的源码目录 > 相对探测目录。
    如果都没有，导入时抛 ImportError，由调用方转成可读错误。
    """
    for mod in ("security_auditor", "ssa", "auditor"):
        if mod in sys.modules:
            return
    if config.ALGO_SRC_DIR and config.ALGO_SRC_DIR not in sys.path:
        sys.path.insert(0, config.ALGO_SRC_DIR)


_ensure_algo_on_path()

try:
    from ssa.loader import load_ssa, SSALabels  # noqa: E402
    from auditor.base import AuditParseError, SecurityAuditor  # noqa: E402
    from security_auditor import run_security_auditor_pipeline  # noqa: E402
    ALGO_AVAILABLE = True
except ImportError as exc:  # pragma: no cover - 环境缺失时给可读错误
    ALGO_AVAILABLE = False
    _ALGO_IMPORT_ERROR = exc

    class AuditParseError(Exception):  # noqa: D101 - 环境缺失时的占位
        pass

    def _unavailable(*args, **kwargs):  # 占位：调用时抛清晰错误
        raise RuntimeError(
            "算法层（NL2SQL 医盾引擎）不可用，请检查：\n"
            "  1) 已执行 pip install -e <NL2SQL 仓库根目录>\n"
            "  2) 或设置环境变量 MEDGUARD_NL2SQL_SRC=<NL2SQL>/src\n"
            f"原始错误：{exc}"
        )

    load_ssa = _unavailable
    SecurityAuditor = _unavailable
    run_security_auditor_pipeline = _unavailable


@dataclass
class AuditOutcome:
    """审计管线结果（已统一为稳定形状，屏蔽算法层三种 audit_report）。"""
    violations: List[Dict[str, Any]] = field(default_factory=list)
    audited_plan: List[Dict[str, Any]] = field(default_factory=list)
    rewrite_log: List[str] = field(default_factory=list)
    rewrites_applied: int = 0
    degradation_level: str = "L0"
    degradation_message: str = ""
    """产品层中文说明。留空则由路由按等级取默认文案；非默认情形
    （如解析失败）在此给出更准确的措辞，避免复用会误导人的文案。"""
    degradation_message_cn: str = ""
    """同上的标签覆盖。解析失败**不是策略拒绝**，用 L3 的默认标签
    「已拒绝」会让用户以为被规则挡住了，从而反复换问法试探。"""
    degradation_label: str = ""


def _restore_final_projection(audited_sql: str, original_sql: str,
                              ssa) -> str:
    """还原最终子查询被 Rule A 误伤的 AVG 包裹（算法层不改的适配）。

    背景（实测）：审计器对最终子查询的 controlled 列有 is_final 豁免
    （最终答案就是给用户看的），但算法层 Rule A 的 AVG 包裹没有同样
    的豁免——单查询计划（如「导出患者信息」）会把最终答案里的
    ``name`` 改写成 ``AVG(name)``，破坏答案语义。

    本函数只做一件事：把改写后的最终 SQL 中，**原始 SQL 里以非聚合
    形式出现**的 controlled 列的 AVG() 包裹还原为裸列。安全依据：
    - 审计器对最终查询的 controlled 裸列本就合法（豁免）——还原不引入违规；
    - blocked 列永不被还原（任何位置都非法，保持算法层的移除/降级语义）；
    - 原始 SQL 本身是 AVG(x)（用户问的就是平均值）时不会还原——它
      在原始中就是聚合形式。
    """
    try:
        import sqlglot
        import sqlglot.expressions as exp
        audited_tree = sqlglot.parse_one(audited_sql, read="sqlite")
        original_tree = sqlglot.parse_one(original_sql, read="sqlite")
    except Exception:
        return audited_sql

    # 收集原始 SQL 中以非聚合形式出现的列名（裸名）
    naked_in_original: Set[str] = set()
    for sel in original_tree.find_all(exp.Select):
        for proj in sel.expressions:
            for col in proj.find_all(exp.Column):
                node = col.parent
                in_agg = False
                while node is not None:
                    if isinstance(node, exp.AggFunc):
                        in_agg = True
                        break
                    if node is proj:
                        break
                    node = node.parent
                if not in_agg:
                    naked_in_original.add(col.name.strip("`"))

    if not naked_in_original:
        return audited_sql

    def _is_controlled_bare(name: str) -> bool:
        for cols in ssa.column_labels.values():
            if cols.get(name) == "controlled":
                return True
        return False

    changed = False
    for avg in list(audited_tree.find_all(exp.Avg)):
        inner = avg.this
        if isinstance(inner, exp.Column):
            name = inner.name.strip("`")
            if name in naked_in_original and _is_controlled_bare(name):
                avg.replace(inner.copy())
                changed = True
        elif isinstance(inner, exp.Expression):
            # 复杂表达式（如 AVG(CASE ...)）：原始中整体非聚合出现才还原
            if inner.sql() in naked_in_original:
                avg.replace(inner.copy())
                changed = True

    if not changed:
        return audited_sql
    return audited_tree.sql(dialect="sqlite")


def _restore_final_plan(plan: List[Dict[str, Any]],
                        audited: List[Dict[str, Any]], ssa) -> List[Dict[str, Any]]:
    """对改写后计划的「最终子查询」做 AVG 误伤还原（见 _restore_final_projection）。"""
    if not audited:
        return audited
    original = {str(sq.get("id")): sq.get("sql", "") for sq in plan}
    final_entry = audited[-1]
    key = str(final_entry.get("id"))
    original_sql = original.get(key)
    if original_sql is None and "_" in key:
        original_sql = original.get(key.split("_")[0], "")
    if not original_sql:
        return audited
    restored = _restore_final_projection(
        final_entry.get("sql", ""), original_sql, ssa)
    if restored != final_entry.get("sql"):
        final_entry = dict(final_entry)
        final_entry["sql"] = restored
        audited = audited[:-1] + [final_entry]
    return audited


def algo_available() -> bool:
    """算法层是否可用（健康检查用）。"""
    return ALGO_AVAILABLE


def load_policy(datasource_id: str) -> SSALabels:
    """加载数据源的安全策略（SSA）。"""
    return load_ssa(datasource_id, config.SSA_DIR)


def list_policy_ids() -> List[str]:
    """列出策略目录下所有已定案的数据源策略（.yaml 文件，不含 _ 前缀）。"""
    out = []
    if os.path.isdir(config.SSA_DIR):
        for name in sorted(os.listdir(config.SSA_DIR)):
            if name.endswith(".yaml") and not name.startswith("_"):
                out.append(name[:-5])
    return out


def audit_plan(plan: List[Dict[str, Any]], datasource_id: str) -> AuditOutcome:
    """跑完整审计管线，返回违规详情 + 改写 + 降级。

    内部调用算法层两次（都是零 LLM、零查库的纯 AST 计算）：
      1. SecurityAuditor.audit_all()  → 违规详情（管线返回里没有）
      2. run_security_auditor_pipeline() → 改写后的计划与降级等级

    策略不存在时按算法层默认行为处理（无 SSA → 全放行，L0）。
    """
    try:
        ssa = load_policy(datasource_id)
    except FileNotFoundError:
        # 与算法层语义一致：没有 SSA 就无法审计，按「无 SSA → 放行」处理
        return AuditOutcome(
            audited_plan=plan,
            degradation_level="L0",
            degradation_message="No SSA available for this datasource",
        )

    # 算法层遇到无法解析的子查询时**抛 AuditParseError**，而它自己的语义是
    # 「解析失败 → L3 + 空计划」（FINAL_IMPLEMENTATION_NOTES：Parse failures
    # yield L3；Every L3 result returns an empty audited_plan）。所以这里把
    # 异常转成它本应返回的那个形状——空计划是关键，它保证被拒的 SQL 不会
    # 被下游误执行。
    #
    # 实测触发场景：模型生成的子查询只有一行注释
    # （如 `-- 过滤出当前医生，直接使用 :doctor_id`），sqlglot 解析不出表达式。
    # 不接住的话用户看到的是 500，而不是一条可读的拒绝。
    try:
        # ① 违规详情（事件卡片用）
        auditor = SecurityAuditor(ssa)
        audit_results = auditor.audit_all(
            [{"id": sq["id"], "sql": sq["sql"]} for sq in plan]
        )

        violations = []
        for ar in audit_results:
            for v in ar.violations:
                violations.append({
                    "sub_query_id": (
                        v.sub_query_id if v.sub_query_id is not None
                        else str(v.sub_query_index)
                    ),
                    "type": v.type.value,
                    "type_label": VIOLATION_LABELS.get(v.type.value, v.type.value),
                    "column": v.column,
                    "severity": v.severity.value,
                    "severity_label": SEVERITY_LABELS.get(
                        v.severity.value, v.severity.value),
                    "detail": v.detail,
                })

        # ② 改写与降级
        out = run_security_auditor_pipeline(
            decomposition_plan=plan,
            db_id=datasource_id,
            ssa_dir=config.SSA_DIR,
        )
        report = out.get("audit_report", {}) or {}

        # ③ 适配：最终子查询的 AVG 误伤还原（算法层不改，语义见函数注释）
        audited_plan = _restore_final_plan(plan, out.get("audited_plan", []), ssa)
    except AuditParseError as exc:
        return AuditOutcome(
            audited_plan=[],
            degradation_level="L3",
            degradation_message=f"cannot parse sub-query: {exc}",
            degradation_message_cn=PARSE_FAILED_MESSAGE,
            degradation_label=PARSE_FAILED_LABEL,
        )

    return AuditOutcome(
        violations=violations,
        audited_plan=audited_plan,
        rewrite_log=report.get("rewrite_log", []) or [],
        rewrites_applied=report.get("rewrites_applied", 0) or 0,
        degradation_level=out.get("degradation_level", "L0"),
        degradation_message=out.get("degradation_message", "") or "",
    )
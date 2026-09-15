"""
Security Auditor — core of the paper. Three audit dimensions, zero LLM calls.

Architecture:
    SecurityAuditor(audit_subquery)
        ├── _audit_column_level()     → column_unnecessary_exposure,
        │                                column_needs_aggregation,
        │                                blocked_column_in_select
        ├── _audit_cross_domain()     → cross_domain_personal_join
        └── _audit_derived_info_flow() → blocked_column_in_derived,
                                          controlled_column_in_derived

All analysis via sqlglot AST — no database access, no LLM calls.
"""
from dataclasses import dataclass, field
from typing import List, Dict, Set, Optional, Tuple
from enum import Enum

import sqlglot
import sqlglot.expressions as exp

from ssa.loader import SSALabels
from ssa.ecl import ECLLevel, AUDITABLE_LEVELS, MUST_DEGRADE_LEVELS, is_personal_attribute


# ============================================================
# Data types
# ============================================================

class ViolationSeverity(str, Enum):
    REWRITABLE = "rewritable"       # Can be reduced by Rule A/B/D
    DEGRADABLE = "degradable"       # Can fix via Rule C (cross-domain split, L1/L2)
    MUST_DEGRADE = "must_degrade"   # Cannot fix, must reject/degrade (L2/L3)


class ViolationType(str, Enum):
    COLUMN_UNNECESSARY_EXPOSURE = "column_unnecessary_exposure"
    COLUMN_NEEDS_AGGREGATION = "column_needs_aggregation"
    BLOCKED_COLUMN_IN_SELECT = "blocked_column_in_select"
    CROSS_DOMAIN_PERSONAL_JOIN = "cross_domain_personal_join"
    BLOCKED_COLUMN_IN_DERIVED = "blocked_column_in_derived"
    CONTROLLED_COLUMN_IN_DERIVED = "controlled_column_in_derived"


class AuditParseError(ValueError):
    """Raised when a sub-query cannot be audited safely."""

    def __init__(self, sub_query_index: int, sql: str, cause: Exception):
        super().__init__(f"Cannot parse sub-query q{sub_query_index}: {cause}")
        self.sub_query_index = sub_query_index
        self.sql = sql


@dataclass
class Violation:
    """One security violation found by the auditor."""
    type: ViolationType
    sub_query_index: int          # Which sub-query has the violation
    column: str                   # The offending column (fully-qualified)
    severity: ViolationSeverity
    detail: str                   # Human-readable explanation
    # Extra context (varies by violation type)
    tables: Optional[Tuple[str, str]] = None
    derived_expression: Optional[str] = None
    # Stable plan identity.  Positional indices may change when a dead
    # sub-query is eliminated; this id must survive initial audit, rewrite,
    # and re-audit so policy decisions remain attached to the same query.
    sub_query_id: Optional[str] = None


@dataclass
class AuditResult:
    """Complete audit result for one sub-query."""
    sub_query_index: int
    violations: List[Violation] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return len(self.violations) == 0

    @property
    def has_blocking(self) -> bool:
        return any(v.severity == ViolationSeverity.MUST_DEGRADE for v in self.violations)


def deduplicate_violations(violations: List[Violation]) -> List[Violation]:
    """Stable de-duplication across initial and post-rewrite audits.

    Shared by the rewrite engine, the LangGraph orchestration node, and
    the experiment pipelines so the three call sites cannot drift apart.
    """
    unique = []
    seen = set()
    for violation in violations:
        key = (
            violation.type,
            violation.sub_query_id
            if violation.sub_query_id is not None
            else violation.sub_query_index,
            violation.column,
            violation.derived_expression,
            violation.tables,
        )
        if key not in seen:
            seen.add(key)
            unique.append(violation)
    return unique


def collect_leaf_conditions(node, out: List):
    """Collect movable top-level conjuncts of a WHERE tree.

    Only AND is split.  OR/NOT expressions are kept intact so callers never
    turn ``a OR b`` into ``a AND b`` while moving predicates.  Bare Column
    nodes are excluded because sqlglot makes ``Column`` a ``Condition``
    subclass; treating one as a standalone predicate produces invalid
    rewrites such as ``WHERE status = 'active' AND status``.

    Shared by the rewrite engine (Rule B pushdown) and the degradation
    engine (Rule C split).
    """
    if isinstance(node, exp.And):
        collect_leaf_conditions(node.this, out)
        collect_leaf_conditions(node.expression, out)
    elif isinstance(node, exp.Condition) and not isinstance(node, exp.Column):
        out.append(node)


# ============================================================
# Main Auditor
# ============================================================

class SecurityAuditor:
    """
    Three-dimension security auditor for NL2SQL decomposition plans.

    Usage:
        auditor = SecurityAuditor(ssa_labels)
        results = auditor.audit_all(decomposition_plan, downstream_map)
        # decomposition_plan: list of {id, description, sql, tables, columns, ...}
        # downstream_map: dict[int, Set[str]] — for each sub-query, which columns
        #                 its downstream consumers actually need
    """

    def __init__(self, ssa: SSALabels):
        self.ssa = ssa

    # ----------------------------------------------------------
    # Public API
    # ----------------------------------------------------------

    def audit_all(
        self,
        sub_queries: List[dict],
        downstream_map: Optional[Dict[int, Set[str]]] = None
    ) -> List[AuditResult]:
        """
        Run all three audit dimensions on every sub-query.

        Args:
            sub_queries: list of {id, description, sql, tables, columns, all_columns}
            downstream_map: optional {qi_id: set of columns needed by downstream}

        Returns:
            List of AuditResult, one per sub-query
        """
        results = []
        n = len(sub_queries)
        for i, sq in enumerate(sub_queries):
            sql = sq.get('sql', '')
            sq_id = sq.get('id', i)
            is_final = (i == n - 1)

            # Compute downstream-needed columns for this sub-query
            N = set()
            if downstream_map and sq_id in downstream_map:
                N = downstream_map[sq_id]
            else:
                # Fallback: if no downstream_map, compute from downstream SQLs
                N = self._compute_downstream_needs(i, sub_queries)

            violations = []
            violations.extend(self._audit_column_level(i, sql, N, is_final))
            violations.extend(self._audit_cross_domain(i, sql))
            violations.extend(self._audit_derived_info_flow(i, sql, is_final))

            stable_id = str(sq_id)
            for violation in violations:
                violation.sub_query_id = stable_id

            results.append(AuditResult(
                sub_query_index=i,
                violations=violations,
            ))

        return results

    def audit_single(
        self,
        sub_query_index: int,
        sql: str,
        downstream_needs: Optional[Set[str]] = None,
        is_final: bool = False,
        sub_query_id: Optional[str] = None,
    ) -> AuditResult:
        """Audit a single sub-query.

        Args:
            is_final: whether this sub-query is the final answer of the
                plan.  Per the threat model, only intermediate results are
                protected — controlled columns requested by the end user
                are legitimate in the final sub-query.  Blocked columns
                remain forbidden everywhere.
        """
        N = downstream_needs or set()
        violations = []
        violations.extend(self._audit_column_level(
            sub_query_index, sql, N, is_final
        ))
        violations.extend(self._audit_cross_domain(sub_query_index, sql))
        violations.extend(self._audit_derived_info_flow(
            sub_query_index, sql, is_final
        ))
        stable_id = str(sub_query_id) if sub_query_id is not None else None
        for violation in violations:
            violation.sub_query_id = stable_id
        return AuditResult(sub_query_index=sub_query_index, violations=violations)

    # ==========================================================
    # Dimension 1: Column-Level Audit
    # ==========================================================
    #
    # Core logic:
    #   For each column c in the SELECT list of sub-query qi:
    #     1. Look up ECL(c) from SSA
    #     2. If free      → skip (no restriction)
    #     3. If controlled → check (with two exemptions):
    #        a. FINAL sub-query exemption: the final sub-query IS the
    #           end-user answer — controlled columns requested by the user
    #           are legitimate there (the threat model protects intermediate
    #           results only).  Blocked columns are NOT exempt.
    #        b. Aggregate exemption: aggregate form is the sanctioned
    #           exposure for controlled columns — safe regardless of
    #           downstream need (avoids a fixpoint conflict with Rule B
    #           pushdown in the single-pass pipeline).
    #        Otherwise:
    #          - not needed downstream  → unnecessary exposure
    #          - needed but unaggregated → needs aggregation
    #     4. If blocked   → violation in any SELECT (aggregated or not,
    #        final or not).  blocked columns CAN appear in WHERE/JOIN —
    #        filtering is safe.
    #
    # N(q_i) computation (below) marks every column of the FINAL sub-query
    # as "needed"; that rule only feeds the unnecessary-exposure check of
    # INTERMEDIATE sub-queries — the final-answer exemption above is the
    # security definition for the final sub-query itself.
    # ==========================================================

    def _audit_column_level(
        self, qi_index: int, sql: str, downstream_needs: Set[str],
        is_final: bool = False,
    ) -> List[Violation]:
        """Audit SELECT columns of a sub-query against ECL labels."""
        violations = []

        try:
            tree = sqlglot.parse_one(sql, read='sqlite')
        except Exception as exc:
            # A zero-violation result would be fail-open.  Let the orchestration
            # layer route an unauditable query to L3 instead.
            raise AuditParseError(qi_index, sql, exc) from exc

        # Get all columns that appear in SELECT (with their aggregate context)
        select_cols = self._extract_select_columns(tree)

        for col_info in select_cols:
            col_name = col_info['name']        # e.g., "employees.salary"
            is_aggregated = col_info['is_aggregated']
            output_name = col_info.get('output_name', '')
            col_ecl = self.ssa.get(col_name)   # free | controlled | blocked

            if col_ecl == ECLLevel.FREE:
                continue

            elif col_ecl == ECLLevel.CONTROLLED:
                if is_final:
                    # The final sub-query IS the end-user answer: controlled
                    # columns requested by the user are legitimate there.
                    continue
                if is_aggregated:
                    # Aggregate form is the sanctioned exposure for controlled
                    # columns — safe regardless of downstream need.  (Requiring
                    # downstream need here would make the single-pass pipeline
                    # flag its own Rule A AVG() outputs after Rule B pushdown
                    # removes the downstream reference.)
                    continue
                # Check 1: Is this column needed downstream?
                if not (
                    self._is_downstream_needed(col_name, downstream_needs)
                    or self._is_downstream_needed(output_name, downstream_needs)
                ):
                    violations.append(Violation(
                        type=ViolationType.COLUMN_UNNECESSARY_EXPOSURE,
                        sub_query_index=qi_index,
                        column=col_name,
                        severity=ViolationSeverity.REWRITABLE,
                        detail=f"{col_name} (ECL=controlled) is SELECTed but not consumed by any downstream sub-query"
                    ))

                # Check 2: needed but not aggregated → needs wrapping
                else:
                    violations.append(Violation(
                        type=ViolationType.COLUMN_NEEDS_AGGREGATION,
                        sub_query_index=qi_index,
                        column=col_name,
                        severity=ViolationSeverity.REWRITABLE,
                        detail=f"{col_name} (ECL=controlled) should be wrapped in aggregate function"
                    ))

            elif col_ecl == ECLLevel.BLOCKED:
                # Blocked columns can NEVER appear in SELECT (even aggregated)
                violations.append(Violation(
                    type=ViolationType.BLOCKED_COLUMN_IN_SELECT,
                    sub_query_index=qi_index,
                    column=col_name,
                    severity=ViolationSeverity.MUST_DEGRADE,
                    detail=f"{col_name} (ECL=blocked) cannot appear in any SELECT list"
                ))

        return violations

    def _extract_select_columns(self, tree) -> List[dict]:
        """
        Extract all columns from the SELECT clause of a SQL AST.

        For each column reference, determines whether it's inside an aggregate
        function by checking if any AggFunc ancestor contains this column.

        Strategy:
          1. Find all AggFuncs in SELECT → collect columns inside them
          2. These are "aggregated"
          3. All other columns in SELECT are "non-aggregated"
        """
        results = []
        aliases = self._table_aliases(tree)

        # Aggregation is a property of a column *occurrence*, not its name.
        # In ``SELECT salary, AVG(salary)`` only the second occurrence is safe.
        for sel in tree.find_all(exp.Select):
            for col_expr in sel.expressions:
                output_name = self._projection_output_name(col_expr)
                if isinstance(col_expr, exp.Star):
                    # Physical-table stars expand to every column so that
                    # controlled/blocked columns are audited conservatively.
                    # Stars over derived tables expand to nothing here; their
                    # exposure is audited at the inner SELECT instead (every
                    # nested SELECT in the tree is audited independently).
                    for table in self._tables_for_select(sel):
                        for column in self.ssa.columns_for_table(table):
                            results.append({
                                'name': f"{table}.{column}",
                                'is_aggregated': False,
                                'output_name': column,
                            })
                    continue

                if (
                    col_expr.is_star and isinstance(col_expr, exp.Column)
                ):
                    table_ref = col_expr.table.strip('`') if col_expr.table else ''
                    table = aliases.get(table_ref, table_ref)
                    for column in self.ssa.columns_for_table(table):
                        results.append({
                            'name': f"{table}.{column}",
                            'is_aggregated': False,
                            'output_name': column,
                        })
                    continue

                base_cols = list(col_expr.find_all(exp.Column))

                for bc in base_cols:
                    col_name = self._qualify_column(bc, aliases)
                    is_agg = self._is_within_aggregate(bc, col_expr)
                    if col_name:
                        results.append({
                            'name': col_name,
                            'is_aggregated': is_agg,
                            'output_name': output_name,
                        })

        return results

    @staticmethod
    def _projection_output_name(projection: exp.Expression) -> str:
        """Return a projection alias or its plain output column name."""
        if isinstance(projection, exp.Alias) and projection.alias:
            alias = projection.alias
            return (
                alias.strip('`') if isinstance(alias, str)
                else str(alias).strip('`')
            )
        body = projection.this if isinstance(projection, exp.Alias) else projection
        if isinstance(body, exp.Column) and body.name:
            return body.name.strip('`')
        return ''

    @staticmethod
    def _tables_for_select(sel: exp.Select) -> List[str]:
        """Physical tables directly referenced by a SELECT scope."""
        tables = []
        from_expr = sel.args.get('from_') or sel.args.get('from')
        if from_expr:
            tables.extend(table.name for table in from_expr.find_all(exp.Table))
            if isinstance(from_expr.this, exp.Table):
                tables.append(from_expr.this.name)
        for join in sel.args.get('joins') or []:
            if isinstance(join.this, exp.Table):
                tables.append(join.this.name)
        return list(dict.fromkeys(table for table in tables if table))

    @staticmethod
    def _is_within_aggregate(column: exp.Column, projection: exp.Expression) -> bool:
        """Return whether this exact column occurrence is under an aggregate."""
        node = column.parent
        while node is not None:
            if isinstance(node, exp.AggFunc):
                return True
            if node is projection:
                break
            node = node.parent
        return False

    @staticmethod
    def _table_aliases(tree) -> Dict[str, str]:
        """Map SQL aliases to physical table names."""
        aliases = {}
        for table in tree.find_all(exp.Table):
            alias = table.alias
            if alias:
                aliases[str(alias).strip('`')] = table.name
        return aliases

    def _qualify_column(
        self, col: exp.Column, aliases: Optional[Dict[str, str]] = None
    ) -> str:
        """
        Produce a fully-qualified column name.

        If the column has a table alias, use it.
        Otherwise, return the bare column name and let SSA.get() search all tables.
        """
        name = col.name
        table = col.table  # May be None if no table prefix in SQL

        # Strip backticks
        name = name.strip('`') if name else ''
        table = table.strip('`') if table else ''
        if table and aliases:
            table = aliases.get(table, table)

        if table:
            return f"{table}.{name}"
        return name

    # ==========================================================
    # Dimension 2: Cross-Domain Audit
    # ==========================================================
    #
    # Core logic:
    #   For each JOIN in sub-query qi:
    #     1. Extract (table_a, table_b, join_key)
    #     2. Look up cross_domain_rules in SSA
    #     3. If rule.forbid_personal_level AND this is a personal-level JOIN:
    #        → violation: cross_domain_personal_join
    #
    # "Personal-level JOIN" = the JOIN key includes a personal identifier column
    #   AND the SELECT list has non-aggregated columns from BOTH sides
    # ==========================================================

    def _audit_cross_domain(
        self, qi_index: int, sql: str
    ) -> List[Violation]:
        """Audit JOIN operations against cross-domain rules.

        For ``A JOIN B JOIN C`` every table pair joined so far is checked:
        A–B and B–C are both audited (not just pairs against the first FROM
        table), because a cross-domain rule may exist between any two tables
        in the join chain.
        """
        violations = []

        try:
            tree = sqlglot.parse_one(sql, read='sqlite')
        except Exception as exc:
            raise AuditParseError(qi_index, sql, exc) from exc

        for sel in tree.find_all(exp.Select):
            # Tables accumulated in this SELECT scope's join chain.
            accumulated = []
            from_expr = sel.args.get('from_') or sel.args.get('from')
            if from_expr:
                for table_node in from_expr.find_all(exp.Table):
                    if table_node.name and table_node.name not in accumulated:
                        accumulated.append(table_node.name)

            for join_node in sel.args.get('joins') or []:
                right_table = self._get_join_table(join_node)
                if not right_table:
                    continue
                join_key_cols = self._extract_join_key_columns(join_node)

                for left_table in accumulated:
                    for join_col in join_key_cols:
                        rule = self.ssa.get_cross_domain_rule(
                            left_table, right_table, join_col
                        )
                        if not rule or not rule.forbid_personal_level:
                            continue
                        if self._is_personal_level_join(
                            tree, left_table, right_table
                        ):
                            violations.append(Violation(
                                type=ViolationType.CROSS_DOMAIN_PERSONAL_JOIN,
                                sub_query_index=qi_index,
                                column=join_col,
                                severity=ViolationSeverity.DEGRADABLE,
                                detail=(
                                    f"Personal-level JOIN between {left_table} and {right_table} "
                                    f"via {join_col} violates cross-domain rule: {rule.reason}"
                                ),
                                tables=(left_table, right_table),
                            ))

                if right_table not in accumulated:
                    accumulated.append(right_table)

        return violations

    def _get_join_table(self, join_node: exp.Join) -> Optional[str]:
        """Extract the table name from a JOIN node's right side."""
        # join_node.this is the table expression
        table_expr = join_node.this
        if isinstance(table_expr, exp.Table):
            return table_expr.name
        # Could be a subquery — skip
        if isinstance(table_expr, exp.Subquery):
            return None
        # Try to find any Table inside
        tables = list(table_expr.find_all(exp.Table))
        if tables:
            return tables[0].name
        return None

    def _extract_join_key_columns(self, join_node: exp.Join) -> List[str]:
        """Extract column names from the JOIN ON condition."""
        cols = []
        # join_node.args.get('on') gives the ON condition expression
        on_expr = join_node.args.get('on')
        if on_expr:
            for col in on_expr.find_all(exp.Column):
                cols.append(col.name)
        return list(dict.fromkeys(cols))

    def _is_personal_level_join(
        self, tree, left_table: str, right_table: str
    ) -> bool:
        """
        A JOIN is "personal-level" if:
        1. The SELECT list includes non-aggregated columns from BOTH sides
        2. AND at least one of those columns is a personal identifier
        """
        left_cols = set()
        right_cols = set()

        alias_to_table = self._table_aliases(tree)

        for sel in tree.find_all(exp.Select):
            for col_expr in sel.expressions:
                if isinstance(col_expr, exp.Star):
                    left_cols.update(
                        f"{left_table}.{column}"
                        for column in self.ssa.columns_for_table(left_table)
                    )
                    right_cols.update(
                        f"{right_table}.{column}"
                        for column in self.ssa.columns_for_table(right_table)
                    )
                    continue
                if (
                    col_expr.is_star and isinstance(col_expr, exp.Column)
                ):
                    table_ref = col_expr.table.strip('`') if col_expr.table else ''
                    resolved_tbl = alias_to_table.get(table_ref, table_ref)
                    if resolved_tbl in (left_table, right_table):
                        target = (
                            left_cols if resolved_tbl == left_table else right_cols
                        )
                        target.update(
                            f"{resolved_tbl}.{column}"
                            for column in self.ssa.columns_for_table(resolved_tbl)
                        )
                    continue
                for bc in col_expr.find_all(exp.Column):
                    name = self._qualify_column(bc, alias_to_table)
                    if self._is_within_aggregate(bc, col_expr):
                        continue  # Aggregated → safe, skip
                    # Non-aggregated — try to guess which table it belongs to
                    if bc.table:
                        tbl = bc.table.strip('`')
                        resolved_tbl = alias_to_table.get(tbl, tbl)
                        if resolved_tbl == left_table:
                            left_cols.add(name)
                        elif resolved_tbl == right_table:
                            right_cols.add(name)

        # Both sides contribute non-aggregated columns
        has_both = bool(left_cols) and bool(right_cols)

        # At least one is a personal identifier
        has_personal = any(
            is_personal_attribute(c) for c in (left_cols | right_cols)
        )

        return has_both and has_personal

    # ==========================================================
    # Dimension 3: Derived Information Flow Audit
    # ==========================================================
    #
    # Core logic:
    #   For each non-trivial SELECT expression in sub-query qi:
    #     1. Check if it's a plain column reference → skip (handled by dim 1)
    #     2. For CASE WHEN / window functions / arithmetic / string functions:
    #        Recursively trace all source columns referenced in the expression
    #     3. For each source column:
    #        a. If ECL=blocked → violation: blocked in derived
    #        b. If ECL=controlled + expression not aggregated → violation
    #
    # This is the dimension that NO EXISTING METHOD can handle.
    # Maris's field-level masking cannot detect that a CASE WHEN on salary
    # implicitly exposes salary information.
    # ==========================================================

    def _audit_derived_info_flow(
        self, qi_index: int, sql: str, is_final: bool = False
    ) -> List[Violation]:
        """Audit derived/computed expressions for restricted column leaks."""
        violations = []

        try:
            tree = sqlglot.parse_one(sql, read='sqlite')
        except Exception as exc:
            raise AuditParseError(qi_index, sql, exc) from exc

        aliases = self._table_aliases(tree)

        for sel in tree.find_all(exp.Select):
            for col_expr in sel.expressions:
                expression_body = (
                    col_expr.this if isinstance(col_expr, exp.Alias) else col_expr
                )
                if isinstance(expression_body, (exp.Column, exp.AggFunc)):
                    continue
                if col_expr.is_star:
                    continue

                source_cols = self._trace_column_references(col_expr, aliases)
                if not source_cols:
                    continue

                expr_text = col_expr.sql() if hasattr(col_expr, 'sql') else str(col_expr)

                for src_col in source_cols:
                    col_ecl = self.ssa.get(src_col)

                    if col_ecl == ECLLevel.BLOCKED:
                        violations.append(Violation(
                            type=ViolationType.BLOCKED_COLUMN_IN_DERIVED,
                            sub_query_index=qi_index,
                            column=src_col,
                            severity=ViolationSeverity.MUST_DEGRADE,
                            detail=(
                                f"Derived expression '{expr_text[:60]}...' "
                                f"references blocked column {src_col}"
                            ),
                            derived_expression=expr_text,
                        ))

                    elif col_ecl == ECLLevel.CONTROLLED:
                        if is_final:
                            # Final answer — controlled columns requested by
                            # the end user are legitimate (see audit_single).
                            continue
                        # Check if this specific column reference is inside an agg func
                        # Find the column in this expression and check
                        is_agg = self._is_column_aggregated_in_expr(
                            src_col, col_expr, aliases
                        )
                        if not is_agg:
                            violations.append(Violation(
                                type=ViolationType.CONTROLLED_COLUMN_IN_DERIVED,
                                sub_query_index=qi_index,
                                column=src_col,
                                severity=ViolationSeverity.REWRITABLE,
                                detail=(
                                    f"Derived expression '{expr_text[:60]}...' "
                                    f"exposes controlled column {src_col} in non-aggregated form"
                                ),
                                derived_expression=expr_text,
                            ))

        return violations

    def _is_column_aggregated_in_expr(
        self, col_name: str, expr_node,
        aliases: Optional[Dict[str, str]] = None,
    ) -> bool:
        """
        Check if a column with the given name appears inside an aggregate
        function within an expression tree.
        """
        matches = [
            column for column in expr_node.find_all(exp.Column)
            if self._qualify_column(column, aliases) == col_name
        ]
        return bool(matches) and all(
            self._is_within_aggregate(column, expr_node) for column in matches
        )

    def _trace_column_references(
        self, expr_node, aliases: Optional[Dict[str, str]] = None
    ) -> Set[str]:
        """
        Recursively trace all base column references in an expression tree.

        Walks through CASE/WHEN/THEN/ELSE clauses, window functions,
        binary operations, function calls, etc.

        Example:
          CASE WHEN salary > 50000 THEN 'high' ELSE 'low' END
          → {"employees.salary"}

          RANK() OVER (ORDER BY SUM(amount) DESC)
          → {"sales.amount"}

          salary * 1.1 + bonus
          → {"employees.salary", "employees.bonus"}
        """
        if isinstance(expr_node, exp.Column):
            return {self._qualify_column(expr_node, aliases)}

        refs = set()
        if not isinstance(expr_node, exp.Expression):
            return refs

        # Traverse every AST argument.  This covers CASE branches, CAST.this,
        # the first COALESCE argument, window ORDER/PARTITION clauses, aliases,
        # arithmetic, and future sqlglot expression types without a whitelist.
        for value in expr_node.args.values():
            children = value if isinstance(value, (list, tuple)) else [value]
            for child in children:
                if isinstance(child, exp.Expression):
                    refs.update(self._trace_column_references(child, aliases))
        return refs

    # ==========================================================
    # Downstream needs computation
    # ==========================================================
    #
    # N(q_i) = union of all columns that downstream sub-queries consume
    #          from q_i's output.
    #
    # In MAC-SQL's decomposition, sub-queries form a chain:
    #   q_0 → q_1 → ... → q_{n-1}
    # where q_{n-1} is the final answer.
    #
    # q_j may embed q_i's result via a subquery reference or may
    # reference columns from intermediate results through shared table names.
    # We extract column references from all downstream SQLs.
    # ==========================================================

    def _compute_downstream_needs(
        self, qi_index: int, sub_queries: List[dict]
    ) -> Set[str]:
        """
        Compute which columns downstream sub-queries need from q_i.

        For the LAST sub-query (final output): ALL its SELECT columns are
        "needed" — they constitute the query's answer.

        For other sub-queries: columns referenced by downstream SQLs.
        """
        needs = set()

        # Last sub-query: all SELECT columns are needed (final output)
        if qi_index == len(sub_queries) - 1:
            sql = sub_queries[qi_index].get('sql', '')
            try:
                tree = sqlglot.parse_one(sql, read='sqlite')
                aliases = self._table_aliases(tree)
                for col_info in self._extract_select_columns(tree):
                    needs.add(col_info['name'])
                    if col_info.get('output_name'):
                        needs.add(col_info['output_name'])
            except Exception as exc:
                raise AuditParseError(qi_index, sql, exc) from exc
            return needs

        # Other sub-queries: columns referenced by downstream
        for j in range(qi_index + 1, len(sub_queries)):
            sql = sub_queries[j].get('sql', '')
            try:
                tree = sqlglot.parse_one(sql, read='sqlite')
                aliases = self._table_aliases(tree)
                for col in tree.find_all(exp.Column):
                    name = self._qualify_column(col, aliases)
                    if name:
                        needs.add(name)
            except Exception as exc:
                raise AuditParseError(j, sql, exc) from exc
        return needs

    def _is_downstream_needed(self, col_name: str, downstream_needs: Set[str]) -> bool:
        """
        Check if a column (or any of its aliases/partial matches) appears
        in downstream column references.

        Matching is by BARE column name in both directions: downstream may
        reference a producer's output through the producer's plan identity
        (``q0.salary``) rather than the physical qualifier.  Bare-name
        matching is deliberately permissive — keeping a column that is
        actually unused is fail-safe, whereas removing a column the plan
        still references breaks the plan.
        """
        if not downstream_needs:
            return False  # No downstream → nothing is needed

        # Exact match
        if col_name in downstream_needs:
            return True

        bare = col_name.split('.')[-1]

        # "employees.salary" might appear as just "salary" downstream
        if bare in downstream_needs:
            return True

        # "employees.salary" might appear as "q0.salary" downstream
        if any(need.split('.')[-1] == bare for need in downstream_needs):
            return True

        return False


# ============================================================
# Smoke test
# ============================================================

if __name__ == "__main__":
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config import SSA_DIR
    from ssa.loader import load_ssa

    # Load SSA for financial database
    ssa = load_ssa("financial", SSA_DIR)

    auditor = SecurityAuditor(ssa)

    # Test 1: Safe query — only free columns in SELECT
    safe_sql = """
        SELECT district_id, A2, A3
        FROM district
        WHERE A3 = 'Prague'
    """
    result = auditor.audit_single(0, safe_sql)
    print(f"Test 1 (safe): {len(result.violations)} violations (expect 0)")
    for v in result.violations:
        print(f"  [{v.type.value}] {v.detail}")

    # Test 2: Unsafe — controlled column in SELECT, not needed downstream
    unsafe_sql = """
        SELECT district_id, A11, A2
        FROM district
        WHERE A3 = 'Prague'
    """
    downstream_needs = {"district_id", "A2", "A3"}  # A11 not needed!
    result = auditor.audit_single(1, unsafe_sql, downstream_needs)
    print(f"\nTest 2 (unnecessary controlled): {len(result.violations)} violations (expect >=1)")
    for v in result.violations:
        print(f"  [{v.type.value}] {v.severity.value} {v.detail}")

    # Test 3: Unsafe — blocked column in SELECT (thrombosis_prediction)
    # Uses columns without spaces for reliable sqlglot parsing
    ssa_throm = load_ssa("thrombosis_prediction", SSA_DIR)
    auditor2 = SecurityAuditor(ssa_throm)
    unsafe_medical = """
        SELECT ID, GOT, ANA
        FROM Examination
    """
    # GOT and ANA are both blocked in Examination table
    result = auditor2.audit_single(2, unsafe_medical)
    print(f"\nTest 3 (blocked medical): {len(result.violations)} violations (expect >=2)")
    for v in result.violations:
        print(f"  [{v.type.value}] {v.severity.value} {v.detail}")

    # Test 4: Derived info flow — CASE WHEN on controlled column
    unsafe_derived = """
        SELECT client_id,
               CASE WHEN amount > 50000 THEN 'high' ELSE 'low' END AS loan_level
        FROM loan
    """
    result = auditor.audit_single(3, unsafe_derived)
    print(f"\nTest 4 (derived CASE WHEN): {len(result.violations)} violations (expect >=1)")
    for v in result.violations:
        print(f"  [{v.type.value}] {v.severity.value} {v.detail}")

    # Test 5: Aggregated controlled column, needed downstream → should be safe
    safe_aggregated = """
        SELECT district_id, AVG(A11) AS avg_salary
        FROM district
        GROUP BY district_id
    """
    # Both columns are needed by downstream
    downstream_needs_5 = {"district_id", "avg_salary", "A11"}
    result = auditor.audit_single(4, safe_aggregated, downstream_needs_5)
    print(f"\nTest 5 (aggregated controlled, needed): {len(result.violations)} violations (expect 0)")
    for v in result.violations:
        print(f"  [{v.type.value}] {v.detail}")

    # Test 6: Real MAC-SQL sub-query chain
    print(f"\nTest 6 (MAC-SQL real output):")
    sub_queries = [
        {"id": 0, "sql": "SELECT district_id FROM district WHERE A3 = 'Prague'"},
        {"id": 1, "sql": """
            SELECT COUNT(DISTINCT T1.account_id)
            FROM account T1
            INNER JOIN district T2 ON T1.district_id = T2.district_id
            INNER JOIN loan T3 ON T1.account_id = T3.account_id
            WHERE T2.A3 = 'Prague'
        """},
    ]
    results = auditor.audit_all(sub_queries)
    for r in results:
        print(f"  Sub-query {r.sub_query_index}: {len(r.violations)} violations")
        for v in r.violations:
            print(f"    [{v.type.value}] {v.severity.value} {v.column}")

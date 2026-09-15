"""
Degradation Engine — determines L0-L3 level + generates user messages.
Handles Rule C (cross-domain split) when a cross-domain personal JOIN
cannot be semantically rewritten.
"""
from dataclasses import dataclass, field
from copy import deepcopy
from typing import List, Optional, Dict
from enum import Enum

import sqlglot
import sqlglot.expressions as exp

from auditor.base import (
    Violation, ViolationType, ViolationSeverity, collect_leaf_conditions,
)
from ssa.ecl import ECLLevel
from ssa.loader import SSALabels


class DegradationLevel(Enum):
    L0 = "L0"  # No degradation, safe
    L1 = "L1"  # Approximate — aggregate alternative
    L2 = "L2"  # Intent change — related but different answer
    L3 = "L3"  # Unanswerable — blocked by policy


@dataclass
class DegradationResult:
    level: DegradationLevel
    message: str
    modified_sub_queries: List[dict] = field(default_factory=list)
    # modified_sub_queries: new decomposition plan after Rule C split
    degradation_log: List[str] = field(default_factory=list)


class DegradationEngine:
    """
    Determine degradation level and generate transparent user feedback.

    Decision logic:
      L0: no violations
      L1: all violations are rewritable/degradable AND aggregate alternative exists
      L2: some violations are must_degrade BUT partial answer possible
      L3: must_degrade AND no partial answer possible
    """

    def __init__(self, ssa: Optional[SSALabels] = None):
        self.ssa = ssa

    def determine(
        self,
        violations: List[Violation],
        sub_query_index: int,
        sql: str,
        all_sub_queries: Optional[List[dict]] = None,
    ) -> DegradationResult:
        """
        Determine degradation level from audit violations.

        Args:
            violations: Violations found by the auditor
            sub_query_index: Which sub-query
            sql: The sub-query's SQL
            all_sub_queries: All sub-queries (for Rule C splitting)

        Returns:
            DegradationResult with level and user message
        """
        if not violations:
            return DegradationResult(
                level=DegradationLevel.L0,
                message="",
            )

        # Classify violations
        has_must_degrade = any(
            v.severity == ViolationSeverity.MUST_DEGRADE for v in violations
        )
        all_rewritable_or_degradable = all(
            v.severity in (ViolationSeverity.REWRITABLE, ViolationSeverity.DEGRADABLE)
            for v in violations
        )

        if all_rewritable_or_degradable and not has_must_degrade:
            # Check if aggregate alternative exists
            if self._has_aggregate_alternative(violations, sql):
                return self._build_L1(violations)
            else:
                return self._build_L2(violations)

        if has_must_degrade:
            # Check if partial answer is still possible
            if self._can_partial_answer(violations, sql):
                return self._build_L2(violations)
            else:
                return self._build_L3(violations)

        # Default
        return self._build_L1(violations)

    def _has_aggregate_alternative(
        self, violations: List[Violation], sql: str
    ) -> bool:
        """
        Check if all violations can be resolved by aggregation.
        True for column_unnecessary_exposure, column_needs_aggregation,
        controlled_column_in_derived — these can be fixed via AVG() wrapping.
        """
        aggregatable_types = {
            ViolationType.COLUMN_UNNECESSARY_EXPOSURE,
            ViolationType.COLUMN_NEEDS_AGGREGATION,
            ViolationType.CONTROLLED_COLUMN_IN_DERIVED,
        }
        return bool(violations) and all(
            v.type in aggregatable_types for v in violations
        )

    def determine_plan(
        self,
        violations: List[Violation],
        sub_queries: List[dict],
        original_sub_queries: Optional[List[dict]] = None,
        apply_rule_c: bool = True,
    ) -> DegradationResult:
        """Determine a plan-level level using each violation's actual SQL.

        Groups violations by sub-query, keeps the highest level, and applies
        Rule C to cross-domain joins.  Sub-query elimination shifts
        positional indices between the initial audit (original indices) and
        the post-rewrite audit (current indices), so every violation is
        FIRST remapped to the CURRENT plan position via the plan-level id:
          - violations found by both audits deduplicate to one entry;
          - Rule C splits and SQL lookups target the surviving sub-query
            instead of whatever now occupies the stale position;
          - violations of eliminated sub-queries keep a virtual index past
            the plan end (SQL from the source plan, Rule C skipped).
        """
        if not violations:
            return DegradationResult(
                level=DegradationLevel.L0,
                message="",
                modified_sub_queries=deepcopy(sub_queries),
            )

        source_plan = original_sub_queries or sub_queries
        source_by_id = {
            str(sq.get('id', i)): sq for i, sq in enumerate(source_plan)
        }
        id_to_pos: Dict[str, int] = {}
        for j, sq in enumerate(sub_queries):
            id_to_pos.setdefault(str(sq.get('id', j)), j)

        grouped: Dict[int, List[Violation]] = {}
        group_ids: Dict[int, Optional[str]] = {}
        virtual_by_id: Dict[str, int] = {}
        seen = set()
        for violation in violations:
            stable_id = violation.sub_query_id
            if stable_id is None:
                # Compatibility fallback for direct callers that construct
                # Violation objects manually.
                if 0 <= violation.sub_query_index < len(source_plan):
                    stable_id = str(source_plan[violation.sub_query_index].get(
                        'id', violation.sub_query_index
                    ))
                elif 0 <= violation.sub_query_index < len(sub_queries):
                    stable_id = str(sub_queries[violation.sub_query_index].get(
                        'id', violation.sub_query_index
                    ))

            new_index = id_to_pos.get(stable_id) if stable_id is not None else None
            if new_index is None:
                # Sub-query eliminated by the rewrite phase: retain the
                # policy violation under one stable virtual position.
                virtual_key = stable_id or f"index:{violation.sub_query_index}"
                if virtual_key not in virtual_by_id:
                    virtual_by_id[virtual_key] = len(sub_queries) + len(virtual_by_id)
                new_index = virtual_by_id[virtual_key]
            key = (
                violation.type, stable_id or new_index, violation.column,
                violation.derived_expression, violation.tables,
            )
            if key in seen:
                continue
            seen.add(key)
            grouped.setdefault(new_index, []).append(violation)
            group_ids[new_index] = stable_id

        rank = {
            DegradationLevel.L0: 0,
            DegradationLevel.L1: 1,
            DegradationLevel.L2: 2,
            DegradationLevel.L3: 3,
        }
        decisions = []
        combined_log = []
        for index, group in sorted(grouped.items()):
            if index < len(sub_queries):
                sql = sub_queries[index].get('sql', '')
            else:
                source = source_by_id.get(group_ids.get(index) or '', {})
                sql = source.get('sql', '')
            decision = self.determine(group, index, sql, source_plan)
            decisions.append(decision)
            combined_log.extend(decision.degradation_log)

        combined = max(decisions, key=lambda item: rank[item.level])
        modified_plan = deepcopy(sub_queries)
        rule_c_failed = False

        if apply_rule_c and combined.level != DegradationLevel.L3:
            # Replace from the end so list indices remain stable.
            for index in sorted(grouped, reverse=True):
                if index >= len(modified_plan):
                    # Eliminated sub-query — nothing left to split.
                    continue
                cross = next((
                    violation for violation in grouped[index]
                    if violation.type == ViolationType.CROSS_DOMAIN_PERSONAL_JOIN
                    and violation.tables
                ), None)
                if cross is None:
                    continue
                query_id = modified_plan[index].get('id', index)
                replacements = self.apply_rule_C(
                    query_id,
                    modified_plan[index].get('sql', ''),
                    cross.tables[0],
                    cross.tables[1],
                )
                if replacements:
                    modified_plan[index:index + 1] = replacements
                    combined_log.append(
                        f"Rule C: split q{query_id} into {len(replacements)} aggregate queries"
                    )
                else:
                    rule_c_failed = True
                    combined_log.append(
                        f"Rule C failed for q{query_id}; escalating to L3"
                    )

        if rule_c_failed:
            combined = DegradationResult(
                level=DegradationLevel.L3,
                message=(
                    "This query cannot be answered because a safe "
                    "cross-domain aggregate alternative could not be built."
                ),
            )
            modified_plan = []

        return DegradationResult(
            level=combined.level,
            message=combined.message,
            modified_sub_queries=modified_plan,
            degradation_log=combined_log,
        )

    def _can_partial_answer(
        self, violations: List[Violation], sql: str
    ) -> bool:
        """
        Check if dropping the violating columns still leaves a meaningful query.
        If SELECT has other columns besides the blocked ones, partial answer is possible.
        """
        try:
            tree = sqlglot.parse_one(sql, read='sqlite')
            for sel in tree.find_all(exp.Select):
                non_violating = 0
                for col_expr in sel.expressions:
                    if col_expr.is_star:
                        return False  # Can't partially answer SELECT *
                    base_cols = list(col_expr.find_all(exp.Column))
                    violating_names = {v.column.split('.')[-1] for v in violations}
                    col_names = {
                        c.name.strip('`') if c.name else '' for c in base_cols
                    }
                    if not (col_names & violating_names):
                        non_violating += 1
                if non_violating > 0:
                    return True
        except Exception:
            pass
        return False

    def _build_L1(self, violations: List[Violation]) -> DegradationResult:
        """Build L1: approximate — aggregate alternative.

        The message must not claim the columns "have been aggregated":
        the rewrite may not have been applied (e.g. B1 audit-only, or a
        rewrite that could not fix the violation), so we state that the
        individual-level answer is blocked and an aggregate-level
        alternative exists.
        """
        cols = sorted(set(v.column for v in violations))
        return DegradationResult(
            level=DegradationLevel.L1,
            message=(
                f"Due to privacy policy constraints, this query cannot be "
                f"answered at the individual level. An aggregate-level "
                f"alternative exists; the following restricted columns are "
                f"involved: {', '.join(cols)}. Cross-domain personal-level "
                f"associations have been prevented."
            ),
            degradation_log=[f"L1 degradation: aggregate alternative for {cols}"],
        )

    def _build_L2(self, violations: List[Violation]) -> DegradationResult:
        """Build L2: intent change — related but different answer."""
        return DegradationResult(
            level=DegradationLevel.L2,
            message=(
                f"Cannot compute exact result due to privacy policy constraints. "
                f"The following output represents a related but different query. "
                f"Contact the data administrator for full access authorization."
            ),
            degradation_log=["L2 degradation: intent changed"],
        )

    def _build_L3(self, violations: List[Violation]) -> DegradationResult:
        """Build L3: unanswerable."""
        blocked_cols = sorted(set(
            v.column for v in violations
            if v.severity == ViolationSeverity.MUST_DEGRADE
        ))
        return DegradationResult(
            level=DegradationLevel.L3,
            message=(
                f"This query cannot be answered due to privacy policy restrictions. "
                f"Blocked columns: {', '.join(blocked_cols)}."
            ),
            degradation_log=[f"L3 degradation: query rejected"],
        )

    # ==========================================================
    # Rule C: Cross-Domain Split
    # ==========================================================

    def apply_rule_C(
        self,
        sub_query_index: int,
        sql: str,
        left_table: str,
        right_table: str,
    ) -> List[dict]:
        """
        Split a sub-query containing a cross-domain personal JOIN
        into two independent aggregate sub-queries.

        Preserves per-table query structure:
          - WHERE conditions filtering each table independently
          - GROUP BY clauses on each table's own columns
          - Aggregations on each table's own columns (COUNT/AVG/SUM)

        Original: SELECT A.name, B.cnt
                  FROM orders A JOIN returns B ON A.cid = B.cid
                  WHERE A.date > '2024-01-01' AND B.status = 'returned'

        After:    q_a: SELECT COUNT(*) AS aggregate_count
                       FROM orders
                       WHERE date > '2024-01-01'
                  q_b: SELECT COUNT(*) AS aggregate_count
                       FROM returns
                       WHERE status = 'returned'

        Returns:
            List of replacement sub-query dicts (replaces the original q_i)
        """
        try:
            tree = sqlglot.parse_one(sql, read='sqlite')
        except Exception:
            return []

        # Resolve physical tables and their aliases.  Split queries retain the
        # original alias so pushed WHERE/GROUP BY expressions remain valid.
        table_aliases = {}
        for table_expr in tree.find_all(exp.Table):
            if table_expr.alias:
                table_aliases[table_expr.name] = str(table_expr.alias).strip('`')
        left_alias = table_aliases.get(left_table, '')
        right_alias = table_aliases.get(right_table, '')
        left_refs = {left_table, left_alias} - {''}
        right_refs = {right_table, right_alias} - {''}

        # Extract WHERE conditions belonging to each table
        left_conditions = []
        right_conditions = []
        left_select_parts = []
        right_select_parts = []
        left_group_by = []
        right_group_by = []

        # --- Extract WHERE conditions per table ---
        for sel in tree.find_all(exp.Select):
            where = sel.args.get('where')
            if where:
                # Top-level leaf conditions only: bare Column nodes are
                # exp.Condition subclasses in sqlglot and must not be
                # treated as standalone conditions (they previously
                # produced WHERE clauses like "status = 'active' AND status").
                leaves = []
                collect_leaf_conditions(
                    where.this if isinstance(where, exp.Where) else where,
                    leaves,
                )
                for cond in leaves:
                    sides = self._expression_sides(
                        cond, left_table, right_table, left_refs, right_refs
                    )
                    # Only conditions that reference a single side can be
                    # preserved in the split queries.  A condition touching
                    # both sides (e.g. ``A.x = B.y`` in WHERE) cannot be
                    # expressed in either single-table aggregate query and
                    # is dropped — consistent with the degradation semantics.
                    if sides == {'left'}:
                        left_conditions.append(cond)
                    if sides == {'right'}:
                        right_conditions.append(cond)

            # --- Extract SELECT expressions per table ---
            for col_expr in sel.expressions:
                if col_expr.is_star:
                    continue
                cols_in_expr = {
                    c.table.strip('`') for c in col_expr.find_all(exp.Column)
                    if c.table
                }
                if not cols_in_expr:
                    continue
                if cols_in_expr & left_refs:
                    left_select_parts.append(col_expr)
                if cols_in_expr & right_refs:
                    right_select_parts.append(col_expr)

            # --- Extract GROUP BY per table ---
            group = sel.args.get('group')
            if group:
                for gb_expr in group.expressions if hasattr(group, 'expressions') else []:
                    sides = self._expression_sides(
                        gb_expr, left_table, right_table,
                        left_refs, right_refs,
                    )
                    if sides == {'left'}:
                        if self._group_key_is_free(gb_expr, left_table, left_refs):
                            left_group_by.append(gb_expr)
                    if sides == {'right'}:
                        if self._group_key_is_free(gb_expr, right_table, right_refs):
                            right_group_by.append(gb_expr)

        # --- Build replacement sub-queries ---
        replacements = []

        for side, table, alias, conditions, select_parts, group_by_cols, other_table in [
            ('a', left_table, left_alias, left_conditions, left_select_parts,
             left_group_by, right_table),
            ('b', right_table, right_alias, right_conditions, right_select_parts,
             right_group_by, left_table),
        ]:
            # Build the split SQL preserving WHERE + GROUP BY from this table
            from_clause = f"FROM {table}" + (f" AS {alias}" if alias else "")
            parts = ["SELECT COUNT(*) AS aggregate_count", from_clause]

            if conditions:
                where_str = " AND ".join(
                    c.sql(dialect='sqlite') for c in conditions
                )
                parts.append(f"WHERE {where_str}")

            if group_by_cols:
                gb_str = ", ".join(
                    g.sql(dialect='sqlite') for g in group_by_cols
                )
                parts.append(f"GROUP BY {gb_str}")
                # Add COUNT per group
                parts[0] = (
                    f"SELECT {gb_str}, COUNT(*) AS aggregate_count"
                )

            side_sql = "\n  ".join(
                p if p.startswith('FROM') or p.startswith('WHERE') or p.startswith('GROUP')
                else p
                for p in parts
            )
            # Reassemble cleanly
            select_clause = parts[0]
            rest = "\n".join(parts[1:])
            side_sql = f"{select_clause}\n{rest}"

            replacements.append({
                'id': f"{sub_query_index}_{side}",
                'description': (
                    f"Aggregate query for {table} "
                    f"(cross-domain split: personal JOIN with {other_table} prevented)"
                ),
                'sql': side_sql,
                'degradation_note': (
                    f"Cross-domain split: personal JOIN between "
                    f"{left_table} and {right_table} prevented. "
                    f"Showing aggregate results for {table}."
                ),
            })

        return replacements

    def _expression_sides(
        self, expression: exp.Expression,
        left_table: str, right_table: str,
        left_refs: set, right_refs: set,
    ) -> set:
        """Resolve qualified and unqualified columns to Rule C table sides."""
        sides = set()
        left_columns = {
            name.lower() for name in (
                self.ssa.columns_for_table(left_table) if self.ssa else {}
            )
        }
        right_columns = {
            name.lower() for name in (
                self.ssa.columns_for_table(right_table) if self.ssa else {}
            )
        }
        for column in expression.find_all(exp.Column):
            qualifier = column.table.strip('`') if column.table else ''
            if qualifier:
                if qualifier in left_refs:
                    sides.add('left')
                elif qualifier in right_refs:
                    sides.add('right')
                else:
                    sides.add('unknown')
                continue
            name = column.name.strip('`').lower() if column.name else ''
            in_left = name in left_columns
            in_right = name in right_columns
            if in_left:
                sides.add('left')
            if in_right:
                sides.add('right')
            if not in_left and not in_right:
                sides.add('unknown')
        return sides

    def _group_key_is_free(
        self, expression: exp.Expression, table: str, table_refs: set
    ) -> bool:
        """Keep Rule C grouping only when every group key is ECL-free."""
        if self.ssa is None:
            return False
        matched = False
        for column in expression.find_all(exp.Column):
            qualifier = column.table.strip('`') if column.table else ''
            if qualifier and qualifier not in table_refs:
                continue
            matched = True
            name = column.name.strip('`') if column.name else ''
            if self.ssa.get(f"{table}.{name}") != ECLLevel.FREE:
                return False
        return matched


# ============================================================
# Smoke test
# ============================================================

if __name__ == "__main__":
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    from config import SSA_DIR
    from auditor.base import SecurityAuditor, Violation, ViolationType, ViolationSeverity
    from ssa.loader import load_ssa

    ssa = load_ssa("financial", SSA_DIR)
    auditor = SecurityAuditor(ssa)

    # Test degradation level determination
    deg = DegradationEngine()

    # Test L0
    result = deg.determine([], 0, "SELECT district_id FROM district")
    print(f"Test L0 (no violations): level={result.level.value}")
    assert result.level == DegradationLevel.L0

    # Test L1: unnecessary controlled column
    violations = [
        Violation(
            type=ViolationType.COLUMN_UNNECESSARY_EXPOSURE,
            sub_query_index=0,
            column="A11",
            severity=ViolationSeverity.REWRITABLE,
            detail="A11 (ECL=controlled) SELECTed but not consumed downstream"
        )
    ]
    result = deg.determine(violations, 0, "SELECT A11, district_id FROM district")
    print(f"Test L1 (unnecessary controlled): level={result.level.value}")
    print(f"  Message: {result.message[:100]}...")
    assert result.level == DegradationLevel.L1

    # Test L3: blocked column
    ssa_throm = load_ssa("thrombosis_prediction", SSA_DIR)
    violations_blocked = [
        Violation(
            type=ViolationType.BLOCKED_COLUMN_IN_SELECT,
            sub_query_index=0,
            column="GOT",
            severity=ViolationSeverity.MUST_DEGRADE,
            detail="GOT (ECL=blocked) cannot appear in any SELECT list"
        )
    ]
    result = deg.determine(violations_blocked, 0, "SELECT ID, GOT FROM Examination")
    print(f"Test L2/L3 (blocked): level={result.level.value}")
    print(f"  Message: {result.message[:100]}...")
    # GOT is blocked, ID is not → partial answer possible → L2
    assert result.level in (DegradationLevel.L2, DegradationLevel.L3)

    # Test Rule C
    replacements = deg.apply_rule_C(0, "SELECT a.name, b.cnt FROM A a JOIN B b ON a.id=b.id", "A", "B")
    print(f"\nRule C (cross-domain split): {len(replacements)} replacement sub-queries")
    for r in replacements:
        print(f"  {r['id']}: {r['sql']}")

    print("\nAll degradation tests passed.")

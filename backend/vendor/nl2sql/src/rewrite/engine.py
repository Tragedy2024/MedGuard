"""
Rewrite Engine — single-pass pipeline: D → A → B → final audit.

Rules A/B/D reduce information exposure through projection rewrites
(AVG wrapping, public-proxy substitution, removal, predicate pushdown).
They are security-preserving but NOT semantic-equivalent in general —
retained aggregate alternatives are surfaced as L1 and proxy substitutions
as L2.  Rule C (cross-domain split) is handled by the degradation engine and
is explicitly a non-equivalent degradation.

All transformations via sqlglot AST manipulation.
"""
from dataclasses import dataclass, field
from typing import List, Dict, Set, Optional, Tuple
from enum import Enum

import sqlglot
import sqlglot.expressions as exp

from auditor.base import (
    SecurityAuditor, Violation, ViolationType, ViolationSeverity, AuditResult,
    deduplicate_violations, collect_leaf_conditions,
)
from ssa.loader import SSALabels
from ssa.ecl import ECLLevel, is_personal_attribute


class DegradationLevel(Enum):
    L0 = "L0"  # No degradation, safe
    L1 = "L1"  # Approximate — aggregate alternative
    L2 = "L2"  # Intent change — related but different answer
    L3 = "L3"  # Unanswerable — blocked by policy


@dataclass
class RewriteResult:
    """Result of the rewrite pipeline."""
    original_sql: str
    rewritten_sql: str
    rewrite_log: List[str] = field(default_factory=list)
    remaining_violations: List[Violation] = field(default_factory=list)
    policy_violations: List[Violation] = field(default_factory=list)
    degradation_level: DegradationLevel = DegradationLevel.L0
    degradation_message: str = ""
    # Rewrites that remain in the returned plan and can change answer
    # semantics.  Temporary AVG wrappers removed by Rule B are subtracted.
    aggregate_rewrites: int = 0
    intent_changed: bool = False
    # Dead sub-query elimination: every projection is a restricted column
    # not consumed downstream, so the whole sub-query can be dropped from
    # the plan (emitting an empty SELECT would be invalid SQL).
    eliminate: bool = False

    @property
    def passed(self) -> bool:
        return self.degradation_level == DegradationLevel.L0


class RewriteEngine:
    """
    Single-pass rewrite pipeline: Rule D → Rule A → Rule B → final audit.

    Usage:
        engine = RewriteEngine(auditor, ssa)
        result = engine.rewrite(sql, sub_query_index, sub_queries)
    """

    def __init__(self, auditor: SecurityAuditor, ssa: SSALabels):
        self.auditor = auditor
        self.ssa = ssa

    def rewrite(
        self,
        sql: str,
        sub_query_index: int,
        all_sub_queries: List[dict],
        downstream_needs: Optional[Set[str]] = None,
    ) -> RewriteResult:
        """
        Apply rewrite pipeline to one sub-query's SQL.

        Args:
            sql: The SQL to rewrite
            sub_query_index: Which sub-query this is
            all_sub_queries: All sub-queries in the decomposition (for Rule B pushdown)
            downstream_needs: Columns needed by downstream (auto-computed if None)

        Returns:
            RewriteResult with rewritten SQL and degradation decision
        """
        if downstream_needs is None:
            downstream_needs = self.auditor._compute_downstream_needs(
                sub_query_index, all_sub_queries
            )

        result = RewriteResult(original_sql=sql, rewritten_sql=sql)
        is_final = sub_query_index == len(all_sub_queries) - 1
        sub_query_id = all_sub_queries[sub_query_index].get(
            'id', sub_query_index
        )
        initial_audit = self.auditor.audit_single(
            sub_query_index, sql, downstream_needs, is_final, sub_query_id
        )

        # --- Rule D: Derived expression rewrite ---
        result = self._rule_D(result, sub_query_index, all_sub_queries)

        # --- Rule A: Column-level rewrite ---
        result = self._rule_A(result, downstream_needs, is_final)

        if result.eliminate:
            # Dead sub-query: the caller removes it from the plan.  Skip
            # Rule B — pushing downstream conditions into a sub-query that
            # is about to be deleted would strip filters from live
            # downstream queries.
            result.remaining_violations = []
            # Elimination removes the exposure, but MUST_DEGRADE / DEGRADABLE
            # findings describe the original request (e.g. a blocked column
            # was requested) — they must not be erased by the deletion.
            result.policy_violations = deduplicate_violations([
                v for v in initial_audit.violations
                if v.severity in (
                    ViolationSeverity.MUST_DEGRADE,
                    ViolationSeverity.DEGRADABLE,
                )
            ])
            if result.policy_violations:
                has_must = any(
                    v.severity == ViolationSeverity.MUST_DEGRADE
                    for v in result.policy_violations
                )
                result.degradation_level = (
                    DegradationLevel.L2 if has_must else DegradationLevel.L1
                )
            else:
                result.degradation_level = DegradationLevel.L0
            return result

        # --- Rule B: Predicate pushdown ---
        result = self._rule_B(result, sub_query_index, all_sub_queries)

        # --- Final audit ---
        final_audit = self.auditor.audit_single(
            sub_query_index, result.rewritten_sql, downstream_needs,
            is_final, sub_query_id
        )
        result.remaining_violations = final_audit.violations

        # MUST_DEGRADE and DEGRADABLE findings describe the original request,
        # not merely the current AST.  Removing a blocked projection or
        # splitting a personal-level JOIN must therefore never erase the
        # degradation requirement during re-audit.
        result.policy_violations = deduplicate_violations(
            [
                v for v in initial_audit.violations
                if v.severity in (
                    ViolationSeverity.MUST_DEGRADE,
                    ViolationSeverity.DEGRADABLE,
                )
            ] + result.remaining_violations
        )

        if not result.policy_violations:
            if result.intent_changed:
                result.degradation_level = DegradationLevel.L2
            elif result.aggregate_rewrites > 0:
                result.degradation_level = DegradationLevel.L1
            else:
                result.degradation_level = DegradationLevel.L0
        else:
            # Check severity: MUST_DEGRADE → L2 or L3
            has_must_degrade = any(
                v.severity == ViolationSeverity.MUST_DEGRADE
                for v in result.policy_violations
            )
            if has_must_degrade:
                result.degradation_level = DegradationLevel.L2
            else:
                result.degradation_level = DegradationLevel.L1

        return result

    # ==========================================================
    # Rule D: Derived Expression Rewrite
    # ==========================================================

    def _rule_D(
        self, result: RewriteResult,
        sub_query_index: int = 0,
        all_sub_queries: List[dict] = None,
    ) -> RewriteResult:
        """Rewrite derived expressions (CASE WHEN, window functions) referencing controlled/blocked columns.

        Three strategies (in priority order):
          1. Wrap in AVG() aggregate → aggregate-safe alternative
          2. Replace controlled column with public proxy from same table
          3. Remove expression if its output is not referenced by any downstream query
        """
        if all_sub_queries is None:
            all_sub_queries = []
        try:
            tree = sqlglot.parse_one(result.rewritten_sql, read='sqlite')
        except Exception:
            return result

        modified = False
        aliases = self.auditor._table_aliases(tree)

        for sel in tree.find_all(exp.Select):
            current_exprs = list(sel.expressions)
            new_exprs = []

            for col_expr in current_exprs:
                expression_body = (
                    col_expr.this if isinstance(col_expr, exp.Alias) else col_expr
                )
                if isinstance(
                    expression_body, (exp.Column, exp.Star, exp.AggFunc)
                ):
                    new_exprs.append(col_expr)
                    continue

                source_cols = self._trace_columns(col_expr, aliases)
                blocked = {
                    c for c in source_cols
                    if self.ssa.get(c) == ECLLevel.BLOCKED
                }
                controlled = {
                    c for c in source_cols
                    if self.ssa.get(c) == ECLLevel.CONTROLLED
                }

                if not controlled and not blocked:
                    new_exprs.append(col_expr)
                    continue

                expr_text = col_expr.sql()

                # Blocked sources are never made safe by AVG/proxy substitution.
                # Keep the expression for Rule A/degradation and preserve the
                # MUST_DEGRADE decision from the initial audit.
                if blocked:
                    new_exprs.append(col_expr)
                    result.rewrite_log.append(
                        f"Rule D: Blocked source(s) {sorted(blocked)} require degradation"
                    )
                    continue

                # Strategy 1: Wrap in aggregate
                if self._can_aggregate(col_expr):
                    new_expr = self._wrap_avg(col_expr)
                    new_exprs.append(new_expr)
                    modified = True
                    result.aggregate_rewrites += 1
                    result.rewrite_log.append(
                        f"Rule D: Wrapped '{expr_text[:50]}...' in AVG()"
                    )
                    continue

                # Strategy 2: Try public proxy replacement
                proxy_found = False
                for col in controlled:
                    proxy = self._find_public_proxy(col)
                    if proxy:
                        new_expr = self._replace_column_in_expr(
                            col_expr, col, proxy, aliases
                        )
                        if new_expr:
                            new_exprs.append(new_expr)
                            modified = True
                            proxy_found = True
                            result.intent_changed = True
                            result.rewrite_log.append(
                                f"Rule D: Replaced {col} with public proxy {proxy}"
                            )
                            break
                if proxy_found:
                    continue

                # Strategy 3: Remove if not needed downstream
                alias_name = _alias_of(col_expr)
                if alias_name is None:
                    # Without an alias we cannot verify downstream usage —
                    # keep the expression and let degradation decide.
                    new_exprs.append(col_expr)
                    result.rewrite_log.append(
                        f"Rule D: Kept un-aliased '{expr_text[:50]}...' "
                        f"(cannot verify downstream usage)"
                    )
                    continue
                downstream_refs = _find_downstream_refs(
                    col_expr, sub_query_index, all_sub_queries
                )
                if (
                    sub_query_index < len(all_sub_queries) - 1
                    and not downstream_refs
                ):
                    # Expression output not consumed by any downstream query — safe to remove
                    result.rewrite_log.append(
                        f"Rule D: Removed '{expr_text[:60]}...' (not needed downstream)"
                    )
                    modified = True
                    continue

                # Cannot rewrite — keep as-is, degradation will handle
                new_exprs.append(col_expr)
                result.rewrite_log.append(
                    f"Rule D: Cannot rewrite '{expr_text[:50]}...' (controlled: {controlled}), needs degradation"
                )

            if not new_exprs:
                new_exprs = current_exprs
                result.rewrite_log.append(
                    "Rule D: Kept the sole derived projection; removal would empty SELECT"
                )
            sel.args['expressions'] = new_exprs

        if modified:
            result.rewritten_sql = tree.sql(dialect='sqlite')

        return result

    def _trace_columns(
        self, expr_node, aliases: Optional[Dict[str, str]] = None
    ) -> Set[str]:
        """Trace all base column references in an expression (same logic as auditor)."""
        refs = set()
        for col in expr_node.find_all(exp.Column):
            name = col.name.strip('`') if col.name else ''
            table = col.table.strip('`') if col.table else ''
            if table and aliases:
                table = aliases.get(table, table)
            if table:
                refs.add(f"{table}.{name}")
            else:
                refs.add(name)
        return refs

    def _can_aggregate(self, expr_node) -> bool:
        """Check if an expression can be meaningfully aggregated."""
        if isinstance(expr_node, exp.Alias):
            expr_node = expr_node.this
        # Numeric expressions can be AVG'd
        if isinstance(expr_node, (exp.Binary, exp.Unary)):
            return True
        # Numeric function results can be AVG'd
        if isinstance(expr_node, exp.Func):
            return True
        # CASE WHEN with numeric results
        if isinstance(expr_node, exp.Case):
            return True
        return False

    @staticmethod
    def _wrap_avg(col_expr: exp.Expression) -> exp.Expression:
        """Wrap a projection in AVG while keeping an alias outside AVG()."""
        if isinstance(col_expr, exp.Alias):
            alias_val = (
                col_expr.alias if isinstance(col_expr.alias, str)
                else str(col_expr.alias)
            )
            return exp.Alias(
                this=exp.Avg(this=col_expr.this.copy()), alias=alias_val
            )
        return exp.Avg(this=col_expr.copy())

    def _find_public_proxy(self, col_name: str) -> Optional[str]:
        """
        Find a public (free) proxy column in the same table that conveys
        similar aggregate-level information.

        Strategy: look up SSA column_labels for the same table and return
        the first free column. Prefer columns whose name suggests aggregate
        semantics (e.g., containing 'avg', 'total', 'count', 'level').

        Example: salary (controlled) → dept_level (free in same table)
        """
        if '.' not in col_name:
            return None

        table, col = col_name.rsplit('.', 1)
        table = table.strip('`')
        col = col.strip('`')

        # Get all columns for this table from SSA
        table_labels = self.ssa.columns_for_table(table)
        if not table_labels:
            return None

        # Find free columns in the same table
        free_cols = [
            c for c, label in table_labels.items()
            if label == 'free' and c != col
        ]

        if not free_cols:
            return None

        # Prefer columns with aggregate-like names
        agg_keywords = ['avg', 'total', 'count', 'level', 'group', 'type', 'category']
        for kw in agg_keywords:
            for fc in free_cols:
                if kw in fc.lower():
                    return f"{table}.{fc}"

        # Fallback: return any free column
        return f"{table}.{free_cols[0]}"

    def _replace_column_in_expr(
        self, expr_node, old_col: str, new_col: str,
        aliases: Optional[Dict[str, str]] = None,
    ) -> Optional[exp.Expression]:
        """
        Replace all references to old_col with new_col in an expression tree.

        Uses sqlglot AST traversal: finds all Column nodes matching old_col
        and replaces them with new_col references.
        """
        old_table, old_name = (old_col.rsplit('.', 1) if '.' in old_col
                               else ('', old_col))
        new_table, new_name = (new_col.rsplit('.', 1) if '.' in new_col
                               else ('', new_col))

        try:
            new_node = expr_node.copy()
            replaced = False
            for col_node in new_node.find_all(exp.Column):
                cname = col_node.name.strip('`') if col_node.name else ''
                ctable = col_node.table.strip('`') if col_node.table else ''
                resolved_table = aliases.get(ctable, ctable) if aliases else ctable
                if cname == old_name.strip('`') and (
                    not old_table or resolved_table == old_table or not ctable
                ):
                    col_node.args['this'] = exp.Identifier(this=new_name.strip('`'))
                    if new_table and not ctable:
                        col_node.args['table'] = exp.Identifier(this=new_table.strip('`'))
                    replaced = True
            return new_node if replaced else None
        except Exception:
            return None
        return None

    # ==========================================================
    # Rule A: Column-Level Rewrite
    # ==========================================================

    def _rule_A(
        self, result: RewriteResult, downstream_needs: Set[str],
        is_final: bool = False,
    ) -> RewriteResult:
        """Remove or aggregate controlled/blocked columns from SELECT."""
        try:
            tree = sqlglot.parse_one(result.rewritten_sql, read='sqlite')
        except Exception:
            return result

        modified = False
        aliases = self.auditor._table_aliases(tree)

        for sel in tree.find_all(exp.Select):
            # Get current expressions
            current_exprs = list(sel.expressions)

            # Build new expressions list
            new_exprs = []
            for col_expr in current_exprs:
                if isinstance(col_expr, exp.Star) or (
                    col_expr.is_star and isinstance(col_expr, exp.Column)
                ):
                    # Star expansion: the auditor audits every column of a
                    # ``SELECT *`` (all non-aggregated), so the rewrite must
                    # fix them individually — expand the star into an explicit
                    # projection with restricted columns removed or wrapped.
                    if isinstance(col_expr, exp.Star):
                        star_tables = self.auditor._tables_for_select(sel)
                    else:
                        table_ref = col_expr.table.strip('`') if col_expr.table else ''
                        resolved = aliases.get(table_ref, table_ref)
                        star_tables = [resolved] if resolved else []
                    star_columns_seen = 0
                    for table in star_tables:
                        for column in self.ssa.columns_for_table(table):
                            col_name = f"{table}.{column}"
                            star_columns_seen += 1
                            col_ref = _column_ref(table, column)
                            ecl = self.ssa.get(col_name)
                            if ecl == ECLLevel.FREE:
                                new_exprs.append(col_ref)
                            elif ecl == ECLLevel.CONTROLLED:
                                if not self._is_needed(col_name, downstream_needs):
                                    result.rewrite_log.append(
                                        f"Rule A: Removing {col_name} (ECL=controlled, not needed downstream, star expansion)"
                                    )
                                    continue
                                new_exprs.append(self._wrap_avg(col_ref))
                                result.aggregate_rewrites += 1
                                result.rewrite_log.append(
                                    f"Rule A: Wrapping {col_name} (ECL=controlled) in AVG() (star expansion)"
                                )
                            elif ecl == ECLLevel.BLOCKED:
                                result.rewrite_log.append(
                                    f"Rule A: Removing {col_name} (ECL=blocked, star expansion)"
                                )
                                continue
                    if not star_columns_seen:
                        # Unresolvable star (e.g. t.* over an unknown alias) —
                        # keep it as-is rather than silently dropping columns.
                        new_exprs.append(col_expr)
                        continue
                    # The star is always replaced by explicit columns (or by
                    # nothing when every column is restricted) — the
                    # projection changed either way.
                    modified = True
                    continue

                base_cols = list(col_expr.find_all(exp.Column))
                if not base_cols:
                    new_exprs.append(col_expr)
                    continue

                should_remove = False
                should_aggregate = False

                for bc in base_cols:
                    col_name = self._qualify(bc, aliases)
                    ecl = self.ssa.get(col_name)

                    if ecl == ECLLevel.FREE:
                        continue
                    elif ecl == ECLLevel.CONTROLLED:
                        is_needed = self._is_needed(
                            col_name,
                            downstream_needs,
                            _projection_output_name(col_expr),
                        )
                        is_agg = self.auditor._is_within_aggregate(bc, col_expr)
                        if not is_needed:
                            should_remove = True
                            result.rewrite_log.append(
                                f"Rule A: Removing {col_name} (ECL=controlled, not needed downstream)"
                            )
                        elif not is_agg:
                            should_aggregate = True
                            result.rewrite_log.append(
                                f"Rule A: Wrapping {col_name} (ECL=controlled) in AVG()"
                            )
                    elif ecl == ECLLevel.BLOCKED:
                        should_remove = True
                        result.rewrite_log.append(
                            f"Rule A: Removing {col_name} (ECL=blocked, forbidden in SELECT)"
                        )

                if should_remove:
                    modified = True
                    continue  # Skip this expression

                if should_aggregate:
                    modified = True
                    new_exprs.append(self._wrap_avg(col_expr))
                    result.aggregate_rewrites += 1
                else:
                    new_exprs.append(col_expr)

            # Ensure we didn't empty the SELECT
            if not new_exprs:
                if is_final:
                    # The final answer itself would become empty — degrade
                    # instead of emitting an invalid empty SELECT.
                    result.rewrite_log.append(
                        "Rule A: SELECT was entirely sensitive/unnecessary, marking for L2 degradation"
                    )
                    result.degradation_level = DegradationLevel.L2
                else:
                    # Dead sub-query elimination: every projection is a
                    # restricted column not consumed downstream.  The
                    # orchestration layer drops this sub-query from the plan
                    # instead of emitting an invalid empty SELECT.
                    result.eliminate = True
                    result.rewrite_log.append(
                        "Rule A: sub-query eliminated (all projections restricted and unconsumed downstream)"
                    )
                # Do not serialize an invalid empty SELECT.
                new_exprs = current_exprs

            # Add LIMIT for Top-K / ORDER BY patterns on restricted columns
            if self._has_rank_or_order_by_restricted(
                sel, current_exprs, aliases
            ):
                if self._limit_needs_cap(sel.args.get('limit')):
                    sel.args['limit'] = exp.Limit(expression=exp.Literal.number(100))
                    result.rewrite_log.append(
                        "Rule A: Added/capped LIMIT 100 for restricted ranking pattern"
                    )
                    modified = True
                    result.intent_changed = True

            # Direct assignment to args (avoids sqlglot mutation issues)
            sel.args['expressions'] = new_exprs

        if modified:
            result.rewritten_sql = tree.sql(dialect='sqlite')

        return result

    def _qualify(
        self, col: exp.Column,
        aliases: Optional[Dict[str, str]] = None,
    ) -> str:
        """Produce fully-qualified column name."""
        name = col.name.strip('`') if col.name else ''
        table = col.table.strip('`') if col.table else ''
        if table and aliases:
            table = aliases.get(table, table)
        if table:
            return f"{table}.{name}"
        return name

    def _has_rank_or_order_by_restricted(
        self, sel, original_exprs,
        aliases: Optional[Dict[str, str]] = None,
    ) -> bool:
        """
        Check if this SELECT has ORDER BY / RANK / ROW_NUMBER on a restricted column.
        Top-K patterns expose individual-level ranking — add LIMIT to minimize exposure.
        """
        # Check ORDER BY clauses
        order = sel.args.get('order')
        if order:
            for col in order.find_all(exp.Column):
                col_name = self._qualify(col, aliases)
                if self.ssa.get(col_name) in (ECLLevel.CONTROLLED, ECLLevel.BLOCKED):
                    return True

        # Check for window functions (RANK, ROW_NUMBER, NTILE)
        for expr in original_exprs:
            for window in expr.find_all(exp.Window):
                # RANK() OVER (ORDER BY salary DESC) exposes salary ranking
                for col in window.find_all(exp.Column):
                    col_name = self._qualify(col, aliases)
                    if self.ssa.get(col_name) in (ECLLevel.CONTROLLED, ECLLevel.BLOCKED):
                        return True

        return False

    @staticmethod
    def _limit_needs_cap(limit: Optional[exp.Expression]) -> bool:
        """Return whether a missing/nonliteral/large LIMIT must become 100."""
        if limit is None:
            return True
        expression = limit.args.get('expression')
        if not isinstance(expression, exp.Literal) or not expression.is_number:
            return True
        try:
            return int(expression.this) > 100
        except (TypeError, ValueError):
            return True

    def apply_restricted_order_limit(self, sql: str) -> Tuple[str, List[str]]:
        """Apply the restricted ORDER/RANK data-minimization cap standalone.

        This path is required even when the three audit dimensions report no
        projection violation (for example, a controlled column used only in
        ORDER BY).  The caller must surface the change as L2 because LIMIT can
        alter the answer set.
        """
        try:
            tree = sqlglot.parse_one(sql, read='sqlite')
        except Exception:
            return sql, []
        aliases = self.auditor._table_aliases(tree)
        logs = []
        for sel in tree.find_all(exp.Select):
            expressions = list(sel.expressions)
            if not self._has_rank_or_order_by_restricted(
                sel, expressions, aliases
            ):
                continue
            if self._limit_needs_cap(sel.args.get('limit')):
                sel.args['limit'] = exp.Limit(
                    expression=exp.Literal.number(100)
                )
                logs.append(
                    "Rule A: Added/capped LIMIT 100 for restricted ranking pattern"
                )
        return (
            tree.sql(dialect='sqlite') if logs else sql,
            logs,
        )

    def _is_needed(
        self, col_name: str, downstream_needs: Set[str],
        output_name: Optional[str] = None,
    ) -> bool:
        """Check if a column appears in downstream needs.

        Bare-name matching in both directions: downstream may reference a
        producer's output through the producer's plan identity
        (``q0.salary``) rather than the physical qualifier.  Permissive on
        purpose — keeping an actually-unused column is fail-safe.
        """
        if not downstream_needs:
            return False
        if col_name in downstream_needs:
            return True
        bare = col_name.split('.')[-1]
        if bare in downstream_needs:
            return True
        if any(need.split('.')[-1] == bare for need in downstream_needs):
            return True
        if output_name:
            output_bare = output_name.split('.')[-1]
            if output_bare in downstream_needs:
                return True
            if any(
                need.split('.')[-1] == output_bare
                for need in downstream_needs
            ):
                return True
        return False

    # ==========================================================
    # Rule B: Predicate Pushdown
    # ==========================================================

    def _rule_B(
        self,
        result: RewriteResult,
        sub_query_index: int,
        all_sub_queries: List[dict],
    ) -> RewriteResult:
        """
        Push downstream WHERE conditions that reference this sub-query's
        controlled columns INTO this sub-query's SQL.

        Example:
          q0: SELECT name, salary FROM employees       (salary is controlled)
          q1: SELECT name FROM q0 WHERE salary > 50000  (references salary from q0)

          After pushdown:
          q0: SELECT name, salary FROM employees WHERE salary > 50000
          q1: SELECT name FROM q0                        (no more salary in WHERE)
        """
        try:
            tree = sqlglot.parse_one(result.rewritten_sql, read='sqlite')
        except Exception:
            return result

        # Rule B is valid only for outputs that were plain columns in the
        # original producer query.  Pushing a predicate through an aggregate
        # or derived alias is not generally semantics-preserving.
        try:
            original_tree = sqlglot.parse_one(result.original_sql, read='sqlite')
        except Exception:
            return result

        controlled_outputs = set()
        output_bindings = {}
        controlled_output_names = set()
        original_aliases = self.auditor._table_aliases(original_tree)
        original_selects = list(original_tree.find_all(exp.Select))
        if not original_selects:
            return result
        for projection in original_selects[0].expressions:
            body = projection.this if isinstance(projection, exp.Alias) else projection
            if not isinstance(body, exp.Column) or body.is_star:
                continue
            qualified = self._qualify(body, original_aliases)
            output_name = _projection_output_name(projection)
            if not output_name:
                continue
            physical_qualifier = body.table.strip('`') if body.table else ''
            output_bindings[output_name] = (
                physical_qualifier,
                body.name.strip('`') if body.name else output_name,
            )
            if self.ssa.get(qualified) == ECLLevel.CONTROLLED:
                controlled_outputs.add(output_name)
                controlled_output_names.add(output_name)

        if not controlled_outputs:
            return result

        aliases = self.auditor._table_aliases(tree)
        # Plan-identity qualifiers that downstream uses to reference THIS
        # sub-query's OUTPUT (e.g. ``FROM step2 WHERE step2.salary > 100``).
        # Physical table/alias qualifiers do NOT count: a downstream query
        # re-reading the physical table (e.g. ``L.PT`` with
        # ``Laboratory AS L``) is not consuming this producer's materialised
        # output.  Pushing such a filter and neutralising it downstream would
        # silently drop the predicate from the final answer.
        producer_id = all_sub_queries[sub_query_index].get('id')
        producer_qualifiers = set()
        if producer_id is not None:
            pid = str(producer_id)
            producer_qualifiers = {pid, f"q{pid}", f"step{pid}"} - {''}
        modified = False

        # Check each downstream sub-query
        for j in range(sub_query_index + 1, len(all_sub_queries)):
            downstream_sql = all_sub_queries[j].get('sql', '')
            try:
                ds_tree = sqlglot.parse_one(downstream_sql, read='sqlite')
            except Exception:
                continue

            # Only the outermost SELECT in the downstream query
            ds_sel = ds_tree
            if isinstance(ds_tree, exp.Select) or hasattr(ds_tree, 'find'):
                outermost = list(ds_tree.find_all(exp.Select))
                if not outermost:
                    continue
                ds_sel = outermost[0]  # find_all starts at the root SELECT

            where = ds_sel.args.get('where')
            if not where:
                continue

            # Does the downstream query actually read this producer's output?
            ds_tables = {
                t.name.strip('`')
                for t in ds_sel.find_all(exp.Table) if t.name
            }
            downstream_reads_producer = bool(ds_tables & producer_qualifiers)

            # Find LEAF conditions that reference our controlled columns
            # (skip AND/OR compound nodes — only check individual conditions)
            conditions_to_push = []
            pushed_texts = set()
            where_condition = where.this if isinstance(where, exp.Where) else where
            # Collect only top-level leaf conditions of the WHERE tree.
            # (find_all(exp.Condition) would also yield bare Column nodes —
            # exp.Column subclasses exp.Condition in sqlglot — and nested
            # conditions inside NOT, producing duplicated/meaningless pushes
            # like "WHERE salary > 5000 AND salary".)
            leaf_conditions = []
            collect_leaf_conditions(where_condition, leaf_conditions)
            for cond in leaf_conditions:
                # AND nodes are split by collect_leaf_conditions; OR/NOT are
                # intentionally kept whole to preserve Boolean semantics.
                if isinstance(cond, exp.And):
                    continue
                # Skip conditions embedding subqueries — pushing them would
                # drag downstream-only tables into this sub-query.
                if any(isinstance(node, exp.Subquery)
                       for node in cond.find_all(exp.Subquery)):
                    continue
                cond_nodes = list(cond.find_all(exp.Column))
                cond_cols = {
                    c.name.strip('`') if c.name else '' for c in cond_nodes
                }
                if not _column_sets_overlap(cond_cols, controlled_outputs):
                    continue
                # Every referenced column must be an output of this producer;
                # otherwise moving the condition would drag downstream-local
                # state into the producer query.
                if any(name not in output_bindings for name in cond_cols):
                    result.rewrite_log.append(
                        f"Rule B: Skipped condition '{cond.sql()[:60]}...' "
                        f"(contains non-producer output columns)"
                    )
                    continue
                # Only push when every producer-output column reference in the
                # condition resolves to THIS producer's output: either
                # qualified with the producer's plan identity, or unqualified
                # AND the downstream query actually reads the producer's step
                # table.  A physical-table qualifier means the downstream
                # re-reads the physical table independently — pushing the
                # filter would drop it from the downstream answer.
                foreign = False
                for col in cond.find_all(exp.Column):
                    bare = col.name.strip('`') if col.name else ''
                    if bare not in output_bindings:
                        continue
                    qualifier = col.table.strip('`') if col.table else ''
                    if qualifier:
                        if qualifier not in producer_qualifiers:
                            foreign = True
                            break
                    elif not downstream_reads_producer:
                        foreign = True
                        break
                if foreign:
                    result.rewrite_log.append(
                        f"Rule B: Skipped condition '{cond.sql()[:60]}...' "
                        f"(not consumed via this producer's output)"
                    )
                    continue
                cond_key = cond.sql()
                if cond_key in pushed_texts:
                    continue  # same condition already scheduled from this downstream query
                pushed_texts.add(cond_key)
                conditions_to_push.append(cond)

            pushed_from_j = False
            for cond in conditions_to_push:
                # Add condition to OUR outermost SELECT's WHERE clause
                our_outermost = list(tree.find_all(exp.Select))
                if not our_outermost:
                    continue
                our_sel = our_outermost[0]
                existing_where = our_sel.args.get('where')
                pushed_condition = _retarget_condition(
                    cond, output_bindings, producer_qualifiers
                )
                if existing_where:
                    existing_condition = (
                        existing_where.this
                        if isinstance(existing_where, exp.Where)
                        else existing_where
                    )
                    our_sel.args['where'] = exp.Where(this=exp.And(
                        this=existing_condition.copy(),
                        expression=pushed_condition,
                    ))
                else:
                    our_sel.args['where'] = exp.Where(this=pushed_condition)

                # Remove the pushed condition from the downstream query
                _remove_condition_from_where(where, cond)

                result.rewrite_log.append(
                    f"Rule B: Pushed condition '{cond.sql()[:60]}...' "
                    f"from downstream q{j} into q{sub_query_index}"
                )
                modified = True
                pushed_from_j = True

            # H1 FIX: Persist modified downstream SQL back to the plan
            if pushed_from_j:
                all_sub_queries[j]['sql'] = ds_tree.sql(dialect='sqlite')

        # Rule A runs before Rule B.  Once Rule B has moved the only
        # downstream use of a controlled output into the producer WHERE, the
        # aggregate-wrapped projection inserted by Rule A is dead.  Remove it
        # so queries such as SELECT id, AVG(salary) do not collapse row-level
        # results in SQLite due to a missing GROUP BY.
        if modified:
            referenced_names = set()
            for j in range(sub_query_index + 1, len(all_sub_queries)):
                try:
                    downstream_tree = sqlglot.parse_one(
                        all_sub_queries[j].get('sql', ''), read='sqlite'
                    )
                except Exception:
                    continue
                referenced_names.update(
                    column.name.strip('`')
                    for column in downstream_tree.find_all(exp.Column)
                    if column.name
                )

            producer_selects = list(tree.find_all(exp.Select))
            if producer_selects:
                producer_select = producer_selects[0]
                kept = []
                for projection in producer_select.expressions:
                    output_name = _projection_output_name(projection)
                    source_columns = list(projection.find_all(exp.Column))
                    has_controlled_source = any(
                        self.ssa.get(self._qualify(column, aliases))
                        == ECLLevel.CONTROLLED
                        for column in source_columns
                    )
                    if (
                        has_controlled_source
                        and output_name in controlled_output_names
                        and output_name not in referenced_names
                    ):
                        if (
                            any(isinstance(node, exp.AggFunc)
                                for node in projection.walk())
                            and result.aggregate_rewrites > 0
                        ):
                            result.aggregate_rewrites -= 1
                        result.rewrite_log.append(
                            f"Rule B: Removed dead controlled projection "
                            f"'{projection.sql()[:60]}...' after pushdown"
                        )
                        continue
                    kept.append(projection)
                if kept and len(kept) != len(producer_select.expressions):
                    producer_select.args['expressions'] = kept

        if modified:
            result.rewritten_sql = tree.sql(dialect='sqlite')

        return result


def _find_downstream_refs(
    col_expr, sub_query_index: int, all_sub_queries: List[dict]
) -> List[str]:
    """
    Check if an expression's output (alias or computed column name) is referenced
    in any downstream sub-query's SQL text.

    Returns list of downstream sub-query indices that reference this expression.
    """
    refs = []
    alias_name = None
    expr_name = None

    # Extract alias from expression
    if isinstance(col_expr, exp.Alias) and col_expr.alias:
        alias_name = col_expr.alias if isinstance(col_expr.alias, str) else (
            col_expr.alias.this if hasattr(col_expr.alias, 'this') else str(col_expr.alias)
        )
    # Also try to get a recognizable name from the expression
    if hasattr(col_expr, 'sql'):
        expr_name = col_expr.sql()

    search_terms = []
    if alias_name:
        search_terms.append(str(alias_name).strip('`').lower())
    # For expressions like "CASE WHEN ... END", check if downstream uses the alias

    for j in range(sub_query_index + 1, len(all_sub_queries or [])):
        ds_sql = all_sub_queries[j].get('sql', '').lower()
        for term in search_terms:
            if term in ds_sql:
                refs.append(j)
                break

    return refs


def _column_sets_overlap(left: Set[str], right: Set[str]) -> bool:
    """Compare qualified and unqualified column references by bare name."""
    left_names = {name.rsplit('.', 1)[-1] for name in left}
    right_names = {name.rsplit('.', 1)[-1] for name in right}
    return bool(left_names & right_names)


def _alias_of(col_expr) -> Optional[str]:
    """Return the alias of a projection, or None if it has none."""
    if isinstance(col_expr, exp.Alias) and col_expr.alias:
        alias = col_expr.alias
        return alias if isinstance(alias, str) else str(alias)
    return None


def _column_ref(table: str, column: str) -> exp.Column:
    """Build a qualified column reference for star expansion."""
    return exp.Column(
        this=exp.Identifier(this=column),
        table=exp.Identifier(this=table),
    )


def _remove_condition_from_where(where_node, cond_to_remove):
    """Remove a specific condition from a WHERE clause, handling AND compounds."""
    root = where_node.this if isinstance(where_node, exp.Where) else where_node
    replacement = _replace_condition(root, cond_to_remove)
    if isinstance(where_node, exp.Where):
        where_node.args['this'] = replacement
    return replacement


def _replace_condition(node, target, neutral: bool = True):
    """Replace one matching WHERE leaf with the correct neutral element.

    Children of an AND clause neutralize to TRUE; children of an OR
    clause neutralize to FALSE.  (Using TRUE inside an OR would collapse
    the whole clause to TRUE and silently widen the downstream filter.)
    """
    if node.sql() == target.sql():
        return exp.Boolean(this=neutral)
    if isinstance(node, (exp.And, exp.Or)):
        copied = node.copy()
        child_neutral = isinstance(node, exp.And)
        copied.set('this', _replace_condition(node.this, target, child_neutral))
        copied.set(
            'expression',
            _replace_condition(node.expression, target, child_neutral),
        )
        return copied
    return node.copy()


def _retarget_condition(
    condition, output_bindings: Dict[str, Tuple[str, str]],
    producer_qualifiers: Optional[Set[str]] = None,
):
    """Retarget downstream output references to the producer's qualifier.

    Unqualified column references and references qualified with the
    producer's plan identity (e.g. ``q0.salary``) are retargeted to the
    producer's physical qualifier.  Other qualified references
    (e.g. ``t2.salary``) are downstream-local and are left untouched —
    callers must filter such conditions before pushing.
    """
    producer_qualifiers = producer_qualifiers or set()
    copied = condition.copy()
    for column in copied.find_all(exp.Column):
        bare = column.name.strip('`') if column.name else ''
        if bare not in output_bindings:
            continue
        qualifier = column.table.strip('`') if column.table else ''
        if qualifier and qualifier not in producer_qualifiers:
            continue
        target_qualifier, target_name = output_bindings[bare]
        column.args['this'] = exp.Identifier(this=target_name)
        if target_qualifier:
            column.args['table'] = exp.Identifier(this=target_qualifier)
        else:
            column.args.pop('table', None)
    return copied


def _projection_output_name(projection: exp.Expression) -> Optional[str]:
    """Return the column name exposed by a projection when it is stable."""
    if isinstance(projection, exp.Alias) and projection.alias:
        alias = projection.alias
        return (
            alias.strip('`') if isinstance(alias, str)
            else str(alias).strip('`')
        )
    columns = list(projection.find_all(exp.Column))
    if len(columns) == 1 and columns[0].name:
        return columns[0].name.strip('`')
    return None

"""
Post-hoc Column Filter baseline (B3 in experiments).

Key comparison against our Security Auditor Agent:
  Ours:   Rewrites SQL BEFORE execution (proactive) — doesn't query unnecessary columns
  This:   Masks columns AFTER execution (reactive) — queries everything, then hides

The comparison reveals the core value of our approach:
  - When a column is used downstream (Y-type violation): Ours preserves functionality,
    Post-hoc filter breaks it by masking needed data
  - When a column is not needed downstream (X-type violation): Both work,
    but Ours never exposes the data to begin with

Uses the same SSA labels as our auditor — shared security input, different mechanism.

Implementation per v1-implementation-report §8:
  P1: Filter immediately after SQL execution (remove blocked, mask controlled)
  P2: Filter before GroupChat broadcast (per-recipient need-to-know)
"""
from typing import List, Dict, Set, Optional, Tuple
from dataclasses import dataclass, field
import sqlglot
import sqlglot.expressions as exp

from ssa.loader import SSALabels
from ssa.ecl import ECLLevel


@dataclass
class FilterResult:
    """Result of post-hoc filtering on one sub-query result set."""
    original_rows: int = 0
    filtered_rows: int = 0
    removed_columns: List[str] = field(default_factory=list)
    masked_columns: List[str] = field(default_factory=list)
    # Columns that were masked but needed downstream → functionality broken
    broken_functionality: List[str] = field(default_factory=list)


class PostHocColumnFilter:
    """
    Filter intermediate query results AFTER SQL execution.

    P1: Execute → filter columns → pass filtered result downstream
    P2: Before GroupChat broadcast → per-recipient masking

    This is the "reactive" approach: query everything, then hide sensitive data.
    """

    def __init__(self, ssa: SSALabels):
        self.ssa = ssa

    def filter_intermediate_result(
        self,
        sub_query_index: int,
        sql: str,
        result_set: List[dict],
        downstream_needs: Set[str],
    ) -> Tuple[List[dict], FilterResult]:
        """
        P1: Filter a sub-query's result set before passing to downstream.

        Rules:
          - ECL=blocked columns: REMOVE from result (never expose)
          - ECL=controlled columns not needed downstream: REMOVE from result
          - ECL=controlled columns needed downstream: KEEP (but track for audit)

        Args:
            sub_query_index: Which sub-query
            sql: The SQL that produced this result set
            result_set: The executed result rows [{col: val, ...}, ...]
            downstream_needs: Set of column names needed by downstream sub-queries

        Returns:
            (filtered_result_set, FilterResult with audit info)
        """
        if not result_set:
            return result_set, FilterResult(original_rows=0)

        # Get all columns from ALL rows (not just first)
        all_columns = set()
        for row in result_set:
            all_columns.update(row.keys())
        all_columns = list(all_columns)

        removed_columns = []
        masked_columns = []
        broken_functionality = []

        for col in all_columns:
            ecl = self.ssa.get(col)

            if ecl == ECLLevel.BLOCKED:
                # Blocked: always remove
                removed_columns.append(col)
                if col in downstream_needs:
                    broken_functionality.append(col)

            elif ecl == ECLLevel.CONTROLLED:
                if col not in downstream_needs:
                    # Not needed downstream → remove entirely
                    removed_columns.append(col)
                else:
                    # Needed downstream but controlled → keep, flag for masking
                    # (In P2, this would be masked from unauthorized recipients)
                    pass

            # free columns: keep as-is

        # Remove columns from result set
        filtered = []
        for row in result_set:
            filtered_row = {
                col: val for col, val in row.items()
                if col not in removed_columns
            }
            filtered.append(filtered_row)

        fr = FilterResult(
            original_rows=len(result_set),
            filtered_rows=len(filtered),
            removed_columns=removed_columns,
            masked_columns=masked_columns,
            broken_functionality=broken_functionality,
        )

        return filtered, fr

    def filter_before_broadcast(
        self,
        message_content: str,
        recipient_needs: Dict[str, Set[str]],  # recipient → columns they need
    ) -> Dict[str, str]:
        """
        P2: Filter message content before GroupChat broadcast.

        For each recipient, mask columns they don't need to know.
        Returns per-recipient filtered message contents.

        Args:
            message_content: The raw message text (may contain column values)
            recipient_needs: For each recipient agent, which columns they're authorized to see

        Returns:
            {recipient: filtered_message_content}
        """
        filtered_messages = {}
        for recipient, allowed_cols in recipient_needs.items():
            filtered = message_content
            for col_name in self.ssa.restricted_columns:
                bare = col_name.split('.')[-1] if '.' in col_name else col_name
                fqn = col_name
                # Check both bare and fully-qualified forms against allowed set
                if bare not in allowed_cols and fqn not in allowed_cols:
                    # Mask value occurrences: "col_name = value" → "col_name = [REDACTED]"
                    import re
                    filtered = re.sub(
                        rf'({bare}\s*[=:]\s*)\S+',
                        r'\1[REDACTED]',
                        filtered
                    )
            filtered_messages[recipient] = filtered

        return filtered_messages

    def compute_filter_effectiveness(
        self,
        sub_queries: List[dict],
        result_set: List[List[dict]],
        downstream_map: Dict[int, Set[str]],
    ) -> dict:
        """
        Compute overall filter effectiveness across all sub-queries.

        Returns metrics:
          - total_columns_removed
          - total_functionality_broken (Y-type violations created by filtering)
          - column_exposure_before_filtering (raw query result exposure)
          - column_exposure_after_filtering
        """
        total_removed = 0
        total_broken = 0
        total_exposed_before = 0
        total_exposed_after = 0

        for i, sq in enumerate(sub_queries):
            sql = sq.get('sql', '')

            # Count exposed restricted columns before filtering
            exposed_before = self._count_exposed_columns(sql)
            total_exposed_before += exposed_before

            # Apply filter
            downstream_needs = downstream_map.get(i, set())
            results = result_set[i] if i < len(result_set) else []
            _, fr = self.filter_intermediate_result(
                i, sql, results, downstream_needs
            )

            total_removed += len(fr.removed_columns)
            total_broken += len(fr.broken_functionality)

            # After filtering: restricted columns still exposed (those needed downstream)
            exposed_after = max(0, exposed_before - len(fr.removed_columns))
            total_exposed_after += exposed_after

        return {
            "total_columns_removed": total_removed,
            "total_functionality_broken": total_broken,
            "column_exposure_before": total_exposed_before,
            "column_exposure_after": total_exposed_after,
            "exposure_reduction_pct": (
                (total_exposed_before - total_exposed_after) / max(total_exposed_before, 1) * 100
            ),
        }

    def _count_exposed_columns(self, sql: str) -> int:
        """Count how many controlled/blocked columns appear in SELECT.
        Returns -1 if SQL cannot be parsed (distinguishable from genuinely 0 exposed columns)."""
        try:
            tree = sqlglot.parse_one(sql, read='sqlite')
        except Exception:
            return -1  # Sentinel: parse failure, not zero exposure

        count = 0
        for sel in tree.find_all(exp.Select):
            for col_expr in sel.expressions:
                for col in col_expr.find_all(exp.Column):
                    name = col.name.strip('`') if col.name else ''
                    table = col.table.strip('`') if col.table else ''
                    qualified = f"{table}.{name}" if table else name
                    if self.ssa.get(qualified) in (ECLLevel.CONTROLLED, ECLLevel.BLOCKED):
                        count += 1

        return count


# ============================================================
# Smoke test
# ============================================================

if __name__ == "__main__":
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config import SSA_DIR
    from ssa.loader import load_ssa

    ssa = load_ssa("financial", SSA_DIR)
    phf = PostHocColumnFilter(ssa)

    # Test: filter intermediate result
    sql = "SELECT district_id, A11, A2, A3 FROM district WHERE A3 = 'Prague'"
    result_set = [
        {"district_id": 1, "A11": 12541, "A2": "Praha 1", "A3": "Prague"},
        {"district_id": 2, "A11": 11277, "A2": "Praha 2", "A3": "Prague"},
    ]
    downstream_needs = {"district_id", "A2", "A3"}  # A11 is NOT needed

    filtered, fr = phf.filter_intermediate_result(0, sql, result_set, downstream_needs)

    print("Original result columns:", list(result_set[0].keys()))
    print("Filtered result columns:", list(filtered[0].keys()))
    print(f"Removed columns: {fr.removed_columns}")
    print(f"Broken functionality: {fr.broken_functionality}")

    # A11 should be removed (controlled, not needed downstream)
    assert "A11" in fr.removed_columns, "A11 should be removed!"
    assert "A11" not in filtered[0], "A11 should not be in filtered result!"
    assert len(fr.broken_functionality) == 0, "No functionality broken (A11 not needed)"

    # Test: blocked column should break functionality if needed downstream
    ssa2 = load_ssa("thrombosis_prediction", SSA_DIR)
    phf2 = PostHocColumnFilter(ssa2)
    result_set2 = [{"ID": 1, "GOT": 42, "ANA": 15}]
    downstream_needs2 = {"ID", "GOT"}  # GOT IS needed downstream!

    filtered2, fr2 = phf2.filter_intermediate_result(0, "", result_set2, downstream_needs2)
    print(f"\nMedical test:")
    print(f"  Removed: {fr2.removed_columns}")
    print(f"  Broken: {fr2.broken_functionality}")
    assert "GOT" in fr2.removed_columns, "GOT should be removed (blocked)!"
    assert "GOT" in fr2.broken_functionality, "GOT needed downstream → functionality broken!"

    print("\nAll Post-hoc Column Filter tests passed.")

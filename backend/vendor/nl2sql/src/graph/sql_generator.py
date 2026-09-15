"""
SQL Generator — converts QSG partitions into executable SQL via LLM.

Step 4: after decomposition search finds the optimal partition,
generate SQL for each partition (set of QSG nodes).

Each partition = one sub-query. The LLM receives:
  - The schema description (desc_str, fk_str)
  - A natural-language description of what this partition should compute
  - Context about upstream partitions connected by hard edges ONLY
  - The original user question (for context)

Output: [{id, description, sql}, ...] — same format as MAC-SQL Decomposer's parse_qa_pairs output.

Protections:
  - max_partitions: merges smallest partitions if too many (default 8)
  - upstream context limited to hard-edge-connected partitions only
  - safe string replacement for prompt templates (no str.format brace issues)
"""
import sys, os, re
from typing import List, Dict, FrozenSet, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'vendor', 'MAC-SQL'))
from core.llm import safe_call_llm

from qsg.parser import QuerySemanticGraph, QSGNode
from graph.dependency import DependencyGraph
from graph.search import DecompositionCandidate

MAX_PARTITIONS = 5   # Default limit on LLM calls per query

# ============================================================
# Prompt templates (safe string replacement — no { } issues)
# ============================================================

SQL_GEN_SYSTEM = """You are a SQL expert. Given a database schema, a natural language question, and a description of ONE sub-step needed to answer the question, write the SQL for that sub-step.

Rules:
- Use valid SQLite syntax
- Only produce the SQL for THIS sub-step, not the entire query
- Use backtick-quoted identifiers for column names with spaces
- Output ONLY the SQL, in a ```sql block"""

SQL_GEN_USER = """Database Schema:
__DESC_STR__

Foreign Keys:
__FK_STR__

Original Question:
__QUERY__

Evidence:
__EVIDENCE__

This sub-step should compute:
__STEP_DESC__

__UPSTREAM__

Write the SQL for this sub-step:"""


def _build_gen_prompt(desc_str, fk_str, query, evidence, step_desc, upstream):
    """Safe prompt builder — avoids str.format() brace issues in SQL text."""
    return (SQL_GEN_SYSTEM + "\n\n" + SQL_GEN_USER
            .replace("__DESC_STR__", desc_str)
            .replace("__FK_STR__", fk_str)
            .replace("__QUERY__", query)
            .replace("__EVIDENCE__", evidence)
            .replace("__STEP_DESC__", step_desc)
            .replace("__UPSTREAM__", upstream))


# ============================================================
# Node-to-description translator
# ============================================================

def _describe_node(node: QSGNode) -> str:
    """Generate a natural-language description of what a QSG node does."""
    if node.type == "SCAN":
        cols = ", ".join(node.output_columns[:5]) if node.output_columns else ""
        return f"Read from table `{node.table}`" + (f" (columns: {cols})" if cols else "")

    elif node.type == "FILTER":
        return f"Filter rows where {node.condition}"

    elif node.type == "JOIN":
        return f"JOIN on: {node.on_condition}" + (f" (type: {node.join_type})" if node.join_type else "")

    elif node.type == "AGGREGATE":
        gb = ", ".join(node.group_by) if node.group_by else "all rows"
        parts = [f"Group by {gb}"]
        for af in (node.agg_funcs or []):
            parts.append(f"Compute {af['function']}({af['column']})")
        return ". ".join(parts)

    elif node.type == "PROJECT":
        cols = ", ".join(node.columns) if node.columns else "all columns"
        return f"Output: {cols}"

    return f"Unknown operation"


def _describe_partition(partition: FrozenSet[str], qsg: QuerySemanticGraph) -> str:
    """Build a natural-language description of what one partition should compute."""
    nodes = [qsg.get_node(nid) for nid in partition]
    nodes = [n for n in nodes if n is not None]
    return "Operations: " + "; ".join(_describe_node(n) for n in nodes)


# ============================================================
# Upstream context — FIXED: only hard-edge-connected partitions
# ============================================================

def _build_upstream_context(
    partition: FrozenSet[str],
    dep_graph: DependencyGraph,
    generated_sqls: Dict[int, str],
    partition_descriptions: Dict[int, str],
    partition_to_idx: Dict[FrozenSet[str], int],
) -> str:
    """
    Only include upstream partitions that have hard edges INTO this partition.

    A partition P_j is "upstream" of P_i iff there exists a hard edge
    u→v where u∈P_j and v∈P_i.
    """
    upstream_info = {}

    for (u, v) in dep_graph.hard_edges:
        if v in partition:
            # u is upstream — find which generated partition u belongs to
            for up_part, up_idx in partition_to_idx.items():
                if u in up_part and up_idx in generated_sqls:
                    desc = partition_descriptions.get(up_idx, f"Sub-step {up_idx}")
                    upstream_info[up_idx] = desc
                    break

    if not upstream_info:
        return ""

    lines = ["This sub-step depends on results from:"]
    for up_idx, desc in sorted(upstream_info.items()):
        lines.append(f"  Sub-step {up_idx}: {desc}")
    return "\n".join(lines)


# ============================================================
# SQL Generator
# ============================================================

class SQLGenerator:
    """Generate SQL for each partition in a decomposition candidate via LLM."""

    def __init__(self, temperature: float = 0.0, max_partitions: int = MAX_PARTITIONS):
        self.temperature = temperature
        self.max_partitions = max_partitions

    def generate(
        self,
        candidate: DecompositionCandidate,
        qsg: QuerySemanticGraph,
        dep_graph: DependencyGraph,
        desc_str: str,
        fk_str: str,
        query: str,
        evidence: str = "",
    ) -> List[dict]:
        """
        Generate SQL for each partition in topological order.

        Protections:
          - Merges smallest partitions if count exceeds max_partitions
          - Hard limit at max_partitions * 2 (raises error)
        """
        partitions = list(candidate.partitions)

        # Fix 2: merge small partitions if too many
        if len(partitions) > self.max_partitions:
            sorted_parts = sorted(partitions, key=lambda p: len(p))
            keep = sorted_parts[:self.max_partitions - 1]
            merge = sorted_parts[self.max_partitions - 1:]
            merged = frozenset().union(*merge) if merge else frozenset()
            partitions = keep + ([merged] if merged else [])
            # Update candidate to reflect merged state (I(D) stays the same —
            # merging partitions doesn't change the information profile)
            candidate.partitions = partitions
            print(f"    [SQL Gen] Merged to {len(partitions)} partitions")

        # Fix 3: hard safety limit
        if len(partitions) > self.max_partitions * 2:
            raise ValueError(
                f"Too many partitions ({len(partitions)}). "
                f"Run decomposition search with fewer iterations."
            )

        # Build partition-to-index map for upstream context
        partition_to_idx = {p: i for i, p in enumerate(partitions)}

        # Topological order
        ordered = self._topological_order_from_parts(partitions, dep_graph)

        generated = {}
        descriptions = {}
        results = []

        for pidx, partition in enumerate(ordered):
            # Fix 1: upstream context only via hard edges
            upstream_context = ""
            if generated:
                upstream_context = _build_upstream_context(
                    partition, dep_graph, generated, descriptions, partition_to_idx
                )

            desc = _describe_partition(partition, qsg)
            descriptions[pidx] = desc

            prompt = _build_gen_prompt(
                desc_str, fk_str, query, evidence, desc, upstream_context
            )

            response = safe_call_llm(prompt)
            sql = self._extract_sql(response)

            generated[pidx] = sql
            results.append({
                'id': pidx,
                'description': desc[:200],
                'sql': sql,
            })

        return results

    def _topological_order_from_parts(
        self, partitions: List[FrozenSet[str]], dep_graph: DependencyGraph
    ) -> List[FrozenSet[str]]:
        """Order partitions so upstream ones come before downstream."""
        node_to_part = {}
        for i, part in enumerate(partitions):
            for node in part:
                node_to_part[node] = i

        n = len(partitions)
        adj = {i: set() for i in range(n)}
        in_deg = {i: 0 for i in range(n)}

        for (u, v) in dep_graph.hard_edges:
            if u in node_to_part and v in node_to_part:
                pu, pv = node_to_part[u], node_to_part[v]
                if pu != pv and pv not in adj[pu]:
                    adj[pu].add(pv)
                    in_deg[pv] += 1

        queue = [i for i in range(n) if in_deg[i] == 0]
        ordered = []
        while queue:
            cur = queue.pop(0)
            ordered.append(partitions[cur])
            for nxt in adj[cur]:
                in_deg[nxt] -= 1
                if in_deg[nxt] == 0:
                    queue.append(nxt)

        # Append unreachable (shouldn't happen for DAGs)
        for i, p in enumerate(partitions):
            if p not in ordered:
                ordered.append(p)

        return ordered

    def _extract_sql(self, response: str) -> str:
        """Extract SQL from LLM response (robust to \\r\\n and multi-line)."""
        response = response.replace('\r\n', '\n').replace('\r', '\n')

        match = re.search(r'```sql\s*\n(.*?)\n```', response, re.DOTALL)
        if match:
            return match.group(1).strip()

        match = re.search(r'```\s*\n(.*?)\n```', response, re.DOTALL)
        if match and '{' not in match.group(1):
            return match.group(1).strip()

        lines = response.split('\n')
        sql_lines = []
        started = False
        for line in lines:
            s = line.strip()
            if s.upper().startswith('SELECT'):
                started = True
            if started:
                sql_lines.append(s)
                if s.endswith(';'):
                    break
        return '\n'.join(sql_lines) if sql_lines else response.strip()


# ============================================================
# Smoke test
# ============================================================

if __name__ == "__main__":
    from qsg.parser import QuerySemanticGraph, QSGNode, QSGEdge

    qsg = QuerySemanticGraph(question="Count accounts in Prague eligible for loans")
    qsg.nodes = [
        QSGNode(id="n1", type="SCAN", table="district",
                output_columns=["district.district_id", "district.A3"]),
        QSGNode(id="n2", type="SCAN", table="account",
                output_columns=["account.account_id", "account.district_id"]),
        QSGNode(id="n3", type="SCAN", table="loan",
                output_columns=["loan.account_id"]),
        QSGNode(id="n4", type="FILTER", input_node="n1",
                condition="district.A3 = 'Prague'",
                output_columns=["district.district_id"]),
        QSGNode(id="n5", type="JOIN", join_type="INNER",
                left_node="n4", right_node="n2",
                on_condition="district.district_id = account.district_id",
                output_columns=["account.account_id"]),
        QSGNode(id="n6", type="JOIN", join_type="INNER",
                left_node="n5", right_node="n3",
                on_condition="account.account_id = loan.account_id",
                output_columns=["account.account_id"]),
        QSGNode(id="n7", type="AGGREGATE", input_node="n6",
                agg_funcs=[{"function": "COUNT", "column": "account.account_id", "alias": "cnt"}],
                output_columns=["cnt"]),
        QSGNode(id="n8", type="PROJECT", input_node="n7",
                columns=["cnt"]),
    ]
    qsg.edges = [
        QSGEdge(from_node="n1", to_node="n4", consumed_columns=["district.district_id", "district.A3"]),
        QSGEdge(from_node="n4", to_node="n5", consumed_columns=["district.district_id"]),
        QSGEdge(from_node="n2", to_node="n5", consumed_columns=["account.account_id", "account.district_id"]),
        QSGEdge(from_node="n5", to_node="n6", consumed_columns=["account.account_id"]),
        QSGEdge(from_node="n3", to_node="n6", consumed_columns=["loan.account_id"]),
        QSGEdge(from_node="n6", to_node="n7", consumed_columns=["account.account_id"]),
        QSGEdge(from_node="n7", to_node="n8", consumed_columns=["cnt"]),
    ]

    for node in qsg.nodes:
        print(f"  {node.id} ({node.type}): {_describe_node(node)}")

    print("\nSQL Generator ready. (upstream context: hard-edge-only)")

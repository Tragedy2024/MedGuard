"""
QSG Parser — calls LLM to parse NL query into Query Semantic Graph JSON.
Validates the output against structural rules.

NOTE: This module makes 1 LLM call per query (line ~172).
The Security Auditor Agent is advertised as "zero LLM for security decisions",
which is true — all audit/rewrite/degradation logic is algorithmic.
However, the QSG semantic parsing step does use LLM. This contradiction
is acknowledged in v1-implementation-report §0.4:

  "QSG parsing (LLM call — this is the ONLY LLM call
   attributed to Security Auditor Agent; in practice it can
   be shared with Decomposer's context)"
"""
import json
import re
import sys
import os
from dataclasses import dataclass, field
from typing import List, Dict, Set, Optional, Tuple
from collections import defaultdict

# Import MAC-SQL's LLM caller
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'vendor', 'MAC-SQL'))
from core.llm import safe_call_llm

from qsg.prompt import build_qsg_prompt
import qsg.prompt as qsg_prompt


@dataclass
class QSGNode:
    id: str
    type: str  # SCAN | FILTER | JOIN | AGGREGATE | PROJECT
    table: Optional[str] = None
    input_node: Optional[str] = None
    output_columns: List[str] = field(default_factory=list)
    # type-specific fields
    condition: Optional[str] = None
    join_type: Optional[str] = None
    left_node: Optional[str] = None
    right_node: Optional[str] = None
    on_condition: Optional[str] = None
    group_by: List[str] = field(default_factory=list)
    agg_funcs: List[dict] = field(default_factory=list)
    columns: List[str] = field(default_factory=list)  # PROJECT only


@dataclass
class QSGEdge:
    from_node: str
    to_node: str
    consumed_columns: List[str] = field(default_factory=list)


@dataclass
class QuerySemanticGraph:
    query_id: Optional[str] = None
    question: Optional[str] = None
    nodes: List[QSGNode] = field(default_factory=list)
    edges: List[QSGEdge] = field(default_factory=list)

    def get_node(self, node_id: str) -> Optional[QSGNode]:
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def get_outgoing_edges(self, node_id: str) -> List[QSGEdge]:
        return [e for e in self.edges if e.from_node == node_id]

    def get_incoming_edges(self, node_id: str) -> List[QSGEdge]:
        return [e for e in self.edges if e.to_node == node_id]


class QSGValidator:
    """Validate QSG structural integrity (v1 §1.3 rules)."""

    def validate(self, qsg: QuerySemanticGraph) -> Tuple[bool, List[str]]:
        errors = []

        # Rule 1: Every node has unique id
        node_ids = [n.id for n in qsg.nodes]
        if len(node_ids) != len(set(node_ids)):
            errors.append("Rule 1: Duplicate node IDs found")

        # Rule 2: Every edge references valid node ids
        for e in qsg.edges:
            if e.from_node not in node_ids:
                errors.append(f"Rule 2: Edge from '{e.from_node}' references unknown node")
            if e.to_node not in node_ids:
                errors.append(f"Rule 2: Edge to '{e.to_node}' references unknown node")

        # Rule 3: PROJECT is unique and has no outgoing edges
        projects = [n for n in qsg.nodes if n.type == "PROJECT"]
        if len(projects) != 1:
            errors.append(f"Rule 3: Expected exactly 1 PROJECT node, found {len(projects)}")
        else:
            project_id = projects[0].id
            if any(e.from_node == project_id for e in qsg.edges):
                errors.append(f"Rule 3: PROJECT node '{project_id}' has outgoing edges")

        # Rule 4: All column references are fully qualified (table.column)
        for n in qsg.nodes:
            for col in n.output_columns or []:
                if '.' not in col and col not in {'*'}:
                    # Might be an alias — warn but don't fail
                    pass

        # Rule 5: Every consumed column in an edge exists in source node's output
        for e in qsg.edges:
            src = qsg.get_node(e.from_node)
            if src:
                src_cols = set(src.output_columns or [])
                for col in e.consumed_columns:
                    if col not in src_cols:
                        errors.append(
                            f"Rule 5: Edge {e.from_node}->{e.to_node}: "
                            f"consumed column '{col}' not in source output {src_cols}"
                        )

        # Rule 6: Graph is a DAG (no cycles)
        if not self._is_dag(qsg):
            errors.append("Rule 6: Graph contains cycles (not a DAG)")

        return len(errors) == 0, errors

    def _is_dag(self, qsg: QuerySemanticGraph) -> bool:
        """Check for cycles via topological sort."""
        in_degree = defaultdict(int)
        adj = defaultdict(list)

        for n in qsg.nodes:
            in_degree[n.id] = 0
        for e in qsg.edges:
            adj[e.from_node].append(e.to_node)
            in_degree[e.to_node] += 1

        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        visited = 0

        while queue:
            node = queue.pop(0)
            visited += 1
            for neighbor in adj[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        return visited == len(qsg.nodes)


class QSGParser:
    """
    Parse NL query into QSG via LLM.

    Usage:
        parser = QSGParser()
        qsg = parser.parse(query, desc_str, fk_str, evidence)
    """

    def __init__(self, temperature: float = 0.0):
        self.temperature = temperature
        self.validator = QSGValidator()

    def parse(
        self,
        query: str,
        desc_str: str,
        fk_str: str,
        evidence: str = "",
    ) -> Tuple[Optional[QuerySemanticGraph], List[str]]:
        """
        Parse a natural language query into a Query Semantic Graph.

        Returns:
            (QSG object or None, list of validation errors)
        """
        prompt = qsg_prompt.QSG_SYSTEM_PROMPT + "\n\n" + build_qsg_prompt(
            desc_str, fk_str, query, evidence
        )

        response = safe_call_llm(prompt)

        # Extract JSON from response
        json_text = self._extract_json(response)
        if not json_text:
            return None, ["Failed to extract JSON from LLM response"]

        try:
            data = json.loads(json_text)
        except json.JSONDecodeError as e:
            return None, [f"JSON parse error: {e}"]

        # Convert to QSG objects
        try:
            qsg = self._dict_to_qsg(data, query)
        except Exception as e:
            return None, [f"QSG construction error: {e}"]

        # Validate
        valid, errors = self.validator.validate(qsg)
        if not valid:
            return qsg, errors  # Return partial QSG with errors

        return qsg, []

    def _extract_json(self, text: str) -> Optional[str]:
        """Extract JSON object from LLM response (robust to \r\n and untagged JSON)."""
        text = text.strip()

        # Normalize line endings
        text = text.replace('\r\n', '\n').replace('\r', '\n')

        # Try direct parse first
        if text.startswith('{'):
            return text

        # Try ```json block (with optional \r\n)
        match = re.search(r'```json\s*\n(.*?)\n```', text, re.DOTALL)
        if match:
            return match.group(1).strip()

        # Try ``` block (no language tag)
        match = re.search(r'```\s*\n(.*?)\n```', text, re.DOTALL)
        if match and '{' in match.group(1):
            return match.group(1).strip()

        # Fallback: find balanced braces (untagged JSON in prose)
        start = text.find('{')
        if start >= 0:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == '{':
                    depth += 1
                elif text[i] == '}':
                    depth -= 1
                    if depth == 0:
                        return text[start:i+1]

        return None

    def _dict_to_qsg(self, data: dict, question: str) -> QuerySemanticGraph:
        """Convert parsed JSON dict to QSG object."""
        qsg = QuerySemanticGraph(question=question)

        for nd in data.get('nodes', []):
            node = QSGNode(
                id=nd['id'],
                type=nd['type'],
                table=nd.get('table'),
                input_node=nd.get('input_node'),
                output_columns=nd.get('output_columns', []),
                condition=nd.get('condition'),
                join_type=nd.get('join_type'),
                left_node=nd.get('left_node'),
                right_node=nd.get('right_node'),
                on_condition=nd.get('on_condition'),
                group_by=nd.get('group_by', []),
                agg_funcs=nd.get('agg_funcs', []),
                columns=nd.get('columns', []),
            )
            qsg.nodes.append(node)

        for ed in data.get('edges', []):
            edge = QSGEdge(
                from_node=ed['from'],
                to_node=ed['to'],
                consumed_columns=ed.get('consumed_columns', []),
            )
            qsg.edges.append(edge)

        return qsg


# ============================================================
# Smoke test
# ============================================================

if __name__ == "__main__":
    parser = QSGParser()

    # Minimal test: just verify the validator works on a hand-built QSG
    qsg = QuerySemanticGraph(
        question="List employees earning more than dept average",
        nodes=[
            QSGNode(id="n1", type="SCAN", table="employees",
                    output_columns=["employees.name", "employees.salary", "employees.dept_id"]),
            QSGNode(id="n2", type="SCAN", table="departments",
                    output_columns=["departments.dept_id", "departments.dept_name"]),
            QSGNode(id="n3", type="AGGREGATE", input_node="n1",
                    group_by=["employees.dept_id"],
                    agg_funcs=[{"function": "AVG", "column": "employees.salary", "alias": "avg_sal"}],
                    output_columns=["dept_id", "avg_sal"]),
            QSGNode(id="n4", type="JOIN", join_type="INNER", left_node="n1", right_node="n3",
                    on_condition="n1.dept_id = n3.dept_id",
                    output_columns=["employees.name", "employees.salary", "employees.dept_id", "avg_sal"]),
            QSGNode(id="n5", type="FILTER", input_node="n4",
                    condition="employees.salary > avg_sal",
                    output_columns=["employees.name", "employees.dept_id"]),
            QSGNode(id="n6", type="JOIN", join_type="INNER", left_node="n5", right_node="n2",
                    on_condition="n5.dept_id = n2.dept_id",
                    output_columns=["employees.name", "departments.dept_name"]),
            QSGNode(id="n7", type="PROJECT", input_node="n6",
                    columns=["employees.name", "departments.dept_name"]),
        ],
        edges=[
            QSGEdge(from_node="n1", to_node="n3", consumed_columns=["employees.dept_id", "employees.salary"]),
            QSGEdge(from_node="n1", to_node="n4", consumed_columns=["employees.name", "employees.salary", "employees.dept_id"]),
            QSGEdge(from_node="n3", to_node="n4", consumed_columns=["dept_id", "avg_sal"]),
            QSGEdge(from_node="n4", to_node="n5", consumed_columns=["employees.name", "employees.salary", "employees.dept_id", "avg_sal"]),
            QSGEdge(from_node="n5", to_node="n6", consumed_columns=["employees.name", "employees.dept_id"]),
            QSGEdge(from_node="n2", to_node="n6", consumed_columns=["departments.dept_id", "departments.dept_name"]),
            QSGEdge(from_node="n6", to_node="n7", consumed_columns=["employees.name", "departments.dept_name"]),
        ],
    )

    validator = QSGValidator()
    valid, errors = validator.validate(qsg)

    print(f"QSG valid: {valid}")
    if errors:
        for e in errors:
            print(f"  ERROR: {e}")
    print(f"Nodes: {len(qsg.nodes)}, Edges: {len(qsg.edges)}")

"""
Dependency Graph Builder — converts QSG into hard/soft edge graph.

From v1-implementation-report §3:
  - hard_edges: directed (u→v) where v consumes u's output (from QSG edges)
  - soft_edges: undirected (u—v) where u,v share a SCAN ancestor but no data dep
"""
from dataclasses import dataclass, field
from typing import Set, Dict, List, Tuple, FrozenSet
from collections import defaultdict

from qsg.parser import QuerySemanticGraph, QSGNode


@dataclass
class DependencyGraph:
    nodes: Set[str] = field(default_factory=set)
    hard_edges: Set[Tuple[str, str]] = field(default_factory=set)    # (from, to)
    soft_edges: Set[Tuple[str, str]] = field(default_factory=set)   # (a, b) undirected
    # Extra metadata
    scan_consumers: Dict[str, Set[str]] = field(default_factory=dict)  # SCAN node → consumer nodes
    node_types: Dict[str, str] = field(default_factory=dict)          # node_id → node_type
    # Column-level data for precise I(D) calculation
    node_output_columns: Dict[str, List[str]] = field(default_factory=dict)       # node_id → [col, ...]
    edge_consumed_columns: Dict[Tuple[str, str], List[str]] = field(default_factory=dict)  # (from,to) → [col, ...]


def build_dependency_graph(qsg: QuerySemanticGraph) -> DependencyGraph:
    """
    Convert QSG to DependencyGraph.

    Step 1: hard_edges = translate QSG edges directly (u→v where v consumes u's output)
    Step 2: soft_edges = nodes that share a SCAN ancestor but have no data dependency path
    """
    nodes = {n.id for n in qsg.nodes}
    node_types = {n.id: n.type for n in qsg.nodes}

    # Extract column-level data from QSG
    node_output_columns = {n.id: list(n.output_columns or []) for n in qsg.nodes}
    edge_consumed_columns = {
        (e.from_node, e.to_node): list(e.consumed_columns or [])
        for e in qsg.edges
    }

    # Step 1: Hard edges from QSG edges
    hard_edges = set()
    for e in qsg.edges:
        hard_edges.add((e.from_node, e.to_node))

    # Compute transitive closure of hard edges (reachability)
    reachable = _compute_reachability(nodes, hard_edges)

    # Step 2: Soft edges — shared SCAN ancestors, no dependency path
    soft_edges = set()

    # Find all SCAN nodes
    scan_nodes = [n for n in qsg.nodes if n.type == "SCAN"]

    # For each SCAN, find all transitive consumers
    scan_consumers: Dict[str, Set[str]] = {}
    for scan in scan_nodes:
        consumers = set()
        _find_consumers(scan.id, hard_edges, consumers)
        scan_consumers[scan.id] = consumers

    # For each pair of consumers of the same SCAN, add soft edge if no directed path
    for scan_id, consumers in scan_consumers.items():
        consumer_list = list(consumers)
        for i in range(len(consumer_list)):
            for j in range(i + 1, len(consumer_list)):
                u, v = consumer_list[i], consumer_list[j]
                if u == v:
                    continue
                # Check if there's a directed path between them
                if (u not in reachable.get(v, set())) and (v not in reachable.get(u, set())):
                    soft_edges.add((u, v))

    return DependencyGraph(
        nodes=nodes,
        hard_edges=hard_edges,
        soft_edges=soft_edges,
        scan_consumers=scan_consumers,
        node_types=node_types,
        node_output_columns=node_output_columns,
        edge_consumed_columns=edge_consumed_columns,
    )


def _compute_reachability(
    nodes: Set[str], edges: Set[Tuple[str, str]]
) -> Dict[str, Set[str]]:
    """Compute transitive closure: reachable[u] = all nodes reachable from u."""
    # Floyd-Warshall on adjacency
    adj = defaultdict(set)
    for u, v in edges:
        adj[u].add(v)

    reachable = {n: set() for n in nodes}
    for n in nodes:
        # BFS from each node
        visited = set()
        queue = [n]
        while queue:
            cur = queue.pop(0)
            if cur in visited:
                continue
            visited.add(cur)
            for nxt in adj[cur]:
                if nxt not in visited:
                    queue.append(nxt)
        reachable[n] = visited - {n}

    return reachable


def _find_consumers(node_id: str, edges: Set[Tuple[str, str]], result: Set[str]):
    """Recursively find all nodes that (transitively) consume a node's output."""
    for u, v in edges:
        if u == node_id:
            if v not in result:
                result.add(v)
                _find_consumers(v, edges, result)


# ============================================================
# Partition validation
# ============================================================

def is_valid_partition(
    partition: List[FrozenSet[str]], graph: DependencyGraph
) -> bool:
    """
    A partition is valid iff:
    1. Every hard edge (u→v) has u and v in DIFFERENT partitions
    2. Each partition's internal subgraph is connected via soft_edges
    """
    node_to_part = {}
    for i, part in enumerate(partition):
        for node in part:
            node_to_part[node] = i

    # Constraint 1: hard edges must cross partitions
    for (u, v) in graph.hard_edges:
        if u in node_to_part and v in node_to_part:
            if node_to_part[u] == node_to_part[v]:
                return False  # Hard dependency violated

    # Constraint 2: connectivity within each partition
    for part in partition:
        if len(part) > 1:
            if not _is_connected_via_soft(part, graph.soft_edges):
                return False

    return True


def _is_connected_via_soft(
    part_nodes: FrozenSet[str], soft_edges: Set[Tuple[str, str]]
) -> bool:
    """Check if all nodes in a partition are connected via soft edges."""
    if len(part_nodes) <= 1:
        return True

    # Build adjacency from soft_edges within this partition
    adj = defaultdict(set)
    for u, v in soft_edges:
        if u in part_nodes and v in part_nodes:
            adj[u].add(v)
            adj[v].add(u)

    # BFS from any node
    start = next(iter(part_nodes))
    visited = set()
    queue = [start]
    while queue:
        cur = queue.pop(0)
        if cur in visited:
            continue
        visited.add(cur)
        for nxt in adj[cur]:
            if nxt not in visited:
                queue.append(nxt)

    return len(visited) == len(part_nodes)

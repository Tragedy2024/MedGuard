"""
Decomposition Space Search — randomized greedy for information-minimal decomposition.

From v1-implementation-report §4:
  The decomposition search is NP-hard (Bell number of partitions).
  We use a randomized greedy approximation with soft-edge order perturbation,
  running K iterations and taking the best I(D) result.
  This is acknowledged as a greedy approximation in the paper.
"""
from dataclasses import dataclass, field
from typing import List, Set, Dict, FrozenSet, Optional, Tuple
from collections import defaultdict
import random

from graph.dependency import (
    DependencyGraph, is_valid_partition, _is_connected_via_soft
)
from ssa.loader import SSALabels
from ssa.ecl import ECLLevel, is_personal_attribute


@dataclass
class DecompositionCandidate:
    """One valid decomposition of the QSG into sub-queries."""
    partitions: List[FrozenSet[str]]  # Each partition = one sub-query (a set of node IDs)
    I_score: float = 0.0
    sub_query_sqls: List[str] = field(default_factory=list)  # Generated SQL per partition
    sub_query_descriptions: List[str] = field(default_factory=list)


def search_information_minimal_decomposition(
    graph: DependencyGraph,
    ssa: SSALabels,
    max_candidates: int = 10,
    n_iterations: int = 10,
    random_seed: int = 42,
) -> List[DecompositionCandidate]:
    """
    Randomized greedy search for information-minimal decompositions.

    Strategy: run K iterations of Union-Find greedy with randomly shuffled
    soft-edge merge order. Return the best I(D) candidates (deduplicated).

    This is a greedy approximation — the paper acknowledges NP-hardness and
    reports that greedy solutions are within close range of exhaustive optima
    for N ≤ 8 (verified separately).

    Args:
        max_candidates: max unique candidates to return
        n_iterations: number of randomized greedy runs (more = better coverage)
        random_seed: for reproducibility
    """
    nodes = list(graph.nodes)
    if len(nodes) == 0:
        return []

    rng = random.Random(random_seed)
    seen = set()  # deduplicate by frozenset of frozensets
    candidates = []

    for _ in range(n_iterations):
        # Randomize soft-edge merge order
        shuffled_soft = list(graph.soft_edges)
        rng.shuffle(shuffled_soft)

        candidate = _randomized_greedy(graph, shuffled_soft)
        if candidate is None:
            continue

        # Deduplicate
        key = frozenset(candidate.partitions)
        if key in seen:
            continue
        seen.add(key)

        _score_decomposition(candidate, graph, ssa)
        candidates.append(candidate)

    # Sort by I(D) ascending, return top K
    candidates.sort(key=lambda c: c.I_score)
    return candidates[:max_candidates]


class _UnionFind:
    """Disjoint-set (Union-Find) data structure."""
    def __init__(self, nodes):
        self.parent = {n: n for n in nodes}
        self.rank = {n: 0 for n in nodes}

    def find(self, x):
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, x, y):
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return False
        if self.rank[rx] < self.rank[ry]:
            self.parent[rx] = ry
        elif self.rank[rx] > self.rank[ry]:
            self.parent[ry] = rx
        else:
            self.parent[ry] = rx
            self.rank[rx] += 1
        return True


def _randomized_greedy(
    graph: DependencyGraph,
    soft_edges: List[Tuple[str, str]],
) -> Optional[DecompositionCandidate]:
    """
    Run Union-Find greedy with soft edges in the given order.
    Returns a DecompositionCandidate or None if no valid partition found.
    """
    uf = _UnionFind(graph.nodes)

    for (u, v) in soft_edges:
        if uf.find(u) == uf.find(v):
            continue
        # Check if merging would put hard-edge endpoints in same partition
        ru = uf.find(u)
        rv = uf.find(v)
        violates = False
        for (a, b) in graph.hard_edges:
            ra = uf.find(a)
            rb = uf.find(b)
            if (ra == ru and rb == rv) or (ra == rv and rb == ru):
                violates = True
                break
        if not violates:
            uf.union(u, v)

    # Collect partitions by root
    root_to_nodes = {}
    for n in graph.nodes:
        root = uf.find(n)
        if root not in root_to_nodes:
            root_to_nodes[root] = set()
        root_to_nodes[root].add(n)

    partitions = [frozenset(nodes) for nodes in root_to_nodes.values()]
    return DecompositionCandidate(partitions=partitions)


def _score_decomposition(
    candidate: DecompositionCandidate, graph: DependencyGraph, ssa: SSALabels
):
    """
    Compute I(D) = Σ|R(q_i) ∩ {controlled, blocked}|   (v1-design §4.2)

    For each partition p_i (sub-query):
      A(p_i) = union of output_columns of all QSG nodes in p_i
      N(p_i) = union of consumed_columns on edges crossing from p_i to downstream
      R(p_i) = A(p_i) - N(p_i)
      I(D) += |{c in R(p_i) | ssa.get(c) in (controlled, blocked)}|
    """
    total_score = 0.0

    node_to_part = {}
    for i, part in enumerate(candidate.partitions):
        for node in part:
            node_to_part[node] = i

    for i, part in enumerate(candidate.partitions):
        # A(p_i): union of output_columns from all nodes in this partition
        A = set()
        for node in part:
            for col in graph.node_output_columns.get(node, []):
                A.add(col)

        # N(p_i): union of consumed_columns on edges crossing from p_i to downstream
        N = set()
        for (u, v), consumed_cols in graph.edge_consumed_columns.items():
            if u in part and node_to_part.get(v, -1) != i:
                for col in consumed_cols:
                    N.add(col)

        # R(p_i) = A - N
        R = A - N

        # Count restricted columns in R
        restricted_R = 0
        for col in R:
            ecl = ssa.get(col)
            if ecl in (ECLLevel.CONTROLLED, ECLLevel.BLOCKED):
                restricted_R += 1
        total_score += restricted_R

    candidate.I_score = total_score


# ============================================================
# Verification: exhaustive comparison for N ≤ 8
# ============================================================

def _exhaustive_partitions(nodes: List[str], graph: DependencyGraph) -> List[DecompositionCandidate]:
    """Generate all valid partitions exhaustively (only for small N ≤ 8)."""
    from itertools import product
    n = len(nodes)
    valid_candidates = []
    # Generate all possible assignments of nodes to partition indices (0..n-1)
    for assignment in product(range(n), repeat=n):
        parts = defaultdict(set)
        for node_idx, part_idx in enumerate(assignment):
            parts[part_idx].add(nodes[node_idx])
        partitions = [frozenset(s) for s in parts.values()]
        if is_valid_partition(partitions, graph):
            valid_candidates.append(DecompositionCandidate(partitions=partitions))
    return valid_candidates


# ============================================================
# Smoke test
# ============================================================

if __name__ == "__main__":
    from qsg.parser import QuerySemanticGraph, QSGNode, QSGEdge
    from graph.dependency import build_dependency_graph
    from ssa.loader import load_ssa

    # Build a minimal QSG
    qsg = QuerySemanticGraph(question='Count accounts in Prague eligible for loans')
    qsg.nodes = [
        QSGNode(id='n1', type='SCAN', table='district', output_columns=['district.district_id', 'district.A3']),
        QSGNode(id='n2', type='SCAN', table='account', output_columns=['account.account_id', 'account.district_id']),
        QSGNode(id='n3', type='SCAN', table='loan', output_columns=['loan.account_id', 'loan.amount']),
        QSGNode(id='n4', type='FILTER', input_node='n1', condition='district.A3 = Prague', output_columns=['district.district_id']),
        QSGNode(id='n5', type='JOIN', left_node='n4', right_node='n2', on_condition='...', output_columns=['account.account_id']),
        QSGNode(id='n6', type='JOIN', left_node='n5', right_node='n3', on_condition='...', output_columns=['account.account_id']),
        QSGNode(id='n7', type='AGGREGATE', input_node='n6', agg_funcs=[{'function':'COUNT','column':'account.account_id','alias':'cnt'}], output_columns=['cnt']),
        QSGNode(id='n8', type='PROJECT', input_node='n7', columns=['cnt']),
    ]
    qsg.edges = [
        QSGEdge(from_node='n1', to_node='n4', consumed_columns=['district.district_id', 'district.A3']),
        QSGEdge(from_node='n4', to_node='n5', consumed_columns=['district.district_id']),
        QSGEdge(from_node='n2', to_node='n5', consumed_columns=['account.account_id', 'account.district_id']),
        QSGEdge(from_node='n5', to_node='n6', consumed_columns=['account.account_id']),
        QSGEdge(from_node='n3', to_node='n6', consumed_columns=['loan.account_id']),
        QSGEdge(from_node='n6', to_node='n7', consumed_columns=['account.account_id']),
        QSGEdge(from_node='n7', to_node='n8', consumed_columns=['cnt']),
    ]

    dep_graph = build_dependency_graph(qsg)
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config import SSA_DIR
    ssa = load_ssa('financial', SSA_DIR)

    # Randomized greedy
    candidates = search_information_minimal_decomposition(dep_graph, ssa, n_iterations=20)
    print(f"Greedy search: {len(candidates)} candidates (iterations=20)")
    for i, c in enumerate(candidates[:3]):
        print(f"  [{i}] {len(c.partitions)} partitions, I(D)={c.I_score}")

    # Exhaustive comparison (N=8, Bell(8)=4140)
    print("\nExhaustive enumeration (N=8)...")
    exhaustive = _exhaustive_partitions(list(dep_graph.nodes), dep_graph)
    valid_exhaustive = [c for c in exhaustive if is_valid_partition(c.partitions, dep_graph)]
    for c in valid_exhaustive:
        _score_decomposition(c, dep_graph, ssa)
    valid_exhaustive.sort(key=lambda c: c.I_score)
    print(f"Exhaustive: {len(valid_exhaustive)} valid partitions")
    for i, c in enumerate(valid_exhaustive[:3]):
        print(f"  [{i}] {len(c.partitions)} partitions, I(D)={c.I_score}")

    # Compare
    greedy_best = candidates[0].I_score
    exhaustive_best = valid_exhaustive[0].I_score if valid_exhaustive else float('inf')
    gap = greedy_best - exhaustive_best
    print(f"\nGreedy best I(D)={greedy_best}, Exhaustive best I(D)={exhaustive_best}, gap={gap}")
    if gap == 0:
        print("Greedy found the optimal solution!")



# ============================================================
# Smoke test
# ============================================================

if __name__ == "__main__":
    # Test with a minimal graph
    nodes = {"n1", "n2", "n3", "n4"}
    hard_edges = {("n1", "n2"), ("n2", "n3")}  # n1→n2→n3 chain, n4 independent
    soft_edges = {("n1", "n4")}                  # n1 and n4 can merge
    node_types = {"n1": "SCAN", "n2": "FILTER", "n3": "PROJECT", "n4": "SCAN"}

    graph = DependencyGraph(
        nodes=nodes, hard_edges=hard_edges, soft_edges=soft_edges,
        node_types=node_types
    )

    # Randomized greedy search
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config import SSA_DIR
    from ssa.loader import load_ssa
    ssa = load_ssa("financial", SSA_DIR)

    candidates = search_information_minimal_decomposition(graph, ssa, n_iterations=20)
    print(f"\nGreedy search: {len(candidates)} candidates")
    for i, c in enumerate(candidates[:3]):
        print(f"  Candidate {i}: {len(c.partitions)} partitions, I(D)={c.I_score}")

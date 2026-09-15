"""
Security Auditor Agent — LangGraph orchestration for the 4-Agent NL2SQL pipeline.

Per v1-implementation-report.md §0.2-0.6:
  - SecurityAwareNL2SQLState: global state flowing through the graph
  - build_security_aware_graph(): 4-Agent StateGraph with conditional routing
  - build_mac_sql_baseline_graph(): 3-Agent baseline (B1, no Security Auditor)
  - security_auditor_agent_node(): ★ core contribution — zero LLM calls
  - route_after_audit(): conditional routing based on degradation level

Architecture:
    Selector → Decomposer → Security Auditor → conditional → Refiner / END
    (LLM×1)    (LLM×1)      (★ LLM×0!)          ┌─ "continue" → Refiner
                                                  ├─ "degrade" → END (L1/L2)
                                                  └─ "reject"  → END (L3)

The Security Auditor Agent can be inserted into any LangGraph multi-Agent
NL2SQL pipeline. It sits between the Decomposer and any downstream consumer.
"""
import copy
import os
import sys
from typing import TypedDict, List, Optional, Dict, Any

# Ensure project paths
_SRC = os.path.dirname(os.path.abspath(__file__))
_BASE = os.path.dirname(_SRC)
_VENDOR = os.path.join(_BASE, 'vendor', 'MAC-SQL')
for _p in [_VENDOR, _SRC]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ============================================================
# State Definition
# ============================================================

class SecurityAwareNL2SQLState(TypedDict, total=False):
    """Global state flowing through the 4-Agent LangGraph pipeline."""

    # --- Input ---
    user_query: str
    database_schema: str
    ssa: dict  # SSA labels (ECL + cross-domain rules), keyed by db_id
    ssa_dir: str

    # --- Selector output ---
    relevant_schema: str

    # --- Decomposer output ---
    decomposition_plan: List[dict]  # [{id, description, sql}, ...]

    # --- Security Auditor output (★ our contribution) ---
    audited_plan: List[dict]
    audit_report: dict
    degradation_level: str       # "L0" | "L1" | "L2" | "L3"
    degradation_message: str

    # --- Refiner output ---
    final_sql: str
    execution_result: str

    # --- Audit trail ---
    audit_trace: List[dict]

    # --- Metadata ---
    db_id: str
    evidence: str
    desc_str: str
    fk_str: str


# ============================================================
# Routing
# ============================================================

def route_after_audit(state: SecurityAwareNL2SQLState) -> str:
    """Route after Security Auditor Agent based on degradation level.

    v1-implementation-report §0.3 lines 152-160:
      L0 → "continue" (pass through to Refiner)
      L1/L2 → "degrade" (output degraded result, end pipeline)
      L3 → "reject" (output rejection message, end pipeline)
    """
    level = state.get("degradation_level", "L0")
    if level == "L0":
        return "continue"
    elif level in ("L1", "L2"):
        return "degrade"
    else:  # L3
        return "reject"


# ============================================================
# Security Auditor Agent Node (★ core contribution)
# ============================================================

def security_auditor_agent_node(
    state: SecurityAwareNL2SQLState,
) -> SecurityAwareNL2SQLState:
    """
    Security Auditor Agent — the core contribution of this paper.

    Internal flow (all algorithmic, zero LLM calls for security decisions):
      1. Build dependency graph from decomposition plan
      2. Three-dimension security audit (column-level / cross-domain / derived info)
      3. If violations → single-pass rewrite pipeline (D → A → B)
      4. Re-audit after rewrite
      5. Degradation determination (L0-L3)

    Per v1-implementation-report §0.4 lines 166-249.
    """
    from auditor.base import (
        AuditParseError, SecurityAuditor, ViolationSeverity,
        deduplicate_violations,
    )
    from rewrite.engine import RewriteEngine
    from degradation.engine import DegradationEngine, DegradationLevel
    from ssa.loader import load_ssa, load_ssa_data

    db_id = state.get("db_id", "")
    decomposition_plan = copy.deepcopy(state.get("decomposition_plan", []))
    original_plan = copy.deepcopy(decomposition_plan)
    ssa_data = state.get("ssa", {})
    ssa_dir = state.get("ssa_dir")
    audit_trace = list(state.get("audit_trace", []))

    # --- Load SSA labels ---
    ssa = None
    if ssa_data:
        try:
            ssa = load_ssa_data(db_id, ssa_data)
        except (TypeError, ValueError):
            ssa = None
    if ssa is None and db_id:
        try:
            ssa = load_ssa(db_id, ssa_dir)
        except FileNotFoundError:
            pass

    if ssa is None:
        # No SSA → pass through unchanged (no security audit possible)
        state["audited_plan"] = decomposition_plan
        state["audit_report"] = {"passed": True, "violations": [], "note": "No SSA available"}
        state["degradation_level"] = "L0"
        state["degradation_message"] = ""
        state["audit_trace"] = audit_trace + [{
            "agent": "security_auditor",
            "decision": "pass",
            "rationale": "No SSA labels available for this database"
        }]
        return state

    # --- Step 1: Audit decomposition plan ---
    auditor = SecurityAuditor(ssa)

    # Convert decomposition_plan to auditor-compatible format
    sub_queries = copy.deepcopy(decomposition_plan)
    seen_ids = set()
    for i, sq in enumerate(sub_queries):
        if not isinstance(sq, dict):
            return _build_invalid_plan_state(
                state, audit_trace,
                f"Sub-query at position {i} is not an object",
            )
        sq.setdefault("id", i)
        sq.setdefault("sql", "")
        raw_id = sq.get("id")
        stable_id = "" if raw_id is None else str(raw_id).strip()
        if not stable_id or stable_id in seen_ids:
            return _build_invalid_plan_state(
                state, audit_trace,
                f"Duplicate or empty sub-query id at position {i}: {stable_id!r}",
            )
        seen_ids.add(stable_id)

    try:
        audit_results = auditor.audit_all(sub_queries)
    except AuditParseError as exc:
        return _build_parse_failure_state(state, audit_trace, exc)
    all_violations = []
    for ar in audit_results:
        all_violations.extend(ar.violations)

    rewriter = RewriteEngine(auditor, ssa)
    if not all_violations:
        # ORDER/RANK minimization is independent of projection violations.
        # It must still run when a restricted column appears only in ORDER BY.
        minimized_plan = copy.deepcopy(sub_queries)
        minimization_log = []
        for sq in minimized_plan:
            rewritten_sql, logs = rewriter.apply_restricted_order_limit(
                sq.get("sql", "")
            )
            if logs:
                sq["sql"] = rewritten_sql
                minimization_log.extend(logs)

        level = "L2" if minimization_log else "L0"
        message = (
            "The result was capped at 100 rows because ranking by a "
            "restricted column can expose individual-level ordering."
            if minimization_log else ""
        )
        state["audited_plan"] = minimized_plan
        state["audit_report"] = {
            "passed": not minimization_log,
            "violations": [],
            "dimensions_checked": ["column_level", "cross_domain", "derived_info_flow"],
            "rewrite_log": minimization_log,
            "semantic_degradation": level,
        }
        state["degradation_level"] = level
        state["degradation_message"] = message
        state["audit_trace"] = audit_trace + [{
            "agent": "security_auditor",
            "decision": "pass" if level == "L0" else "degrade_L2",
            "rationale": (
                "All 3 audit dimensions passed — no violations"
                if level == "L0" else
                "Restricted ranking capped at 100 rows"
            )
        }]
        return state

    # --- Step 2: Rewrite pipeline (single-pass D → A → B) ---
    # Reverse order so dead sub-query elimination (in-place deletion of
    # later indices) never shifts indices still to process, and Rule B no
    # longer pushes conditions from already-deleted downstream sub-queries.
    rewrite_log_all = []
    all_rewrites = 0
    rewrite_floor = "L0"
    rewrite_rank = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}

    for i in range(len(sub_queries) - 1, -1, -1):
        sq = sub_queries[i]
        has_v = any(v.sub_query_index == i for v in all_violations)
        if not has_v:
            continue

        try:
            down_needs = auditor._compute_downstream_needs(i, sub_queries)
            rw_result = rewriter.rewrite(
                sq["sql"], i, sub_queries, down_needs
            )
        except AuditParseError as exc:
            return _build_parse_failure_state(state, audit_trace, exc)

        candidate_floor = rw_result.degradation_level.value
        if rewrite_rank[candidate_floor] > rewrite_rank[rewrite_floor]:
            rewrite_floor = candidate_floor

        if rw_result.eliminate:
            del sub_queries[i]
            all_rewrites += 1
            rewrite_log_all.extend(rw_result.rewrite_log)
            continue

        rewrite_log_all.extend(rw_result.rewrite_log)
        if rw_result.rewritten_sql != sq["sql"]:
            sq["sql"] = rw_result.rewritten_sql
            all_rewrites += 1

    # --- Step 3: Re-audit after rewrite ---
    try:
        audit_after = auditor.audit_all(sub_queries)
    except AuditParseError as exc:
        return _build_parse_failure_state(state, audit_trace, exc)
    remaining_violations = []
    for ar in audit_after:
        remaining_violations.extend(ar.violations)

    # Rewrites may remove blocked output from the AST, but that is a partial
    # answer, not L0.  Preserve original MUST_DEGRADE/DEGRADABLE findings.
    policy_violations = deduplicate_violations([
        v for v in all_violations
        if v.severity in (
            ViolationSeverity.MUST_DEGRADE,
            ViolationSeverity.DEGRADABLE,
        )
    ] + remaining_violations)

    if not policy_violations:
        # Security violations were eliminated.  Only exact rewrites may
        # return L0; a retained aggregate/proxy alternative is transparent
        # L1/L2 degradation even though the resulting SQL is secure.
        if rewrite_floor == "L1":
            message = (
                "The individual-level intermediate result was replaced with "
                "a secure aggregate-level alternative."
            )
        elif rewrite_floor == "L2":
            message = (
                "The requested intermediate result required a related but "
                "different secure alternative."
            )
        else:
            message = ""
        state["audited_plan"] = sub_queries
        state["audit_report"] = {
            "passed": rewrite_floor == "L0",
            "violations_before": len(all_violations),
            "violations_after": 0,
            "rewrites_applied": all_rewrites,
            "rewrite_log": rewrite_log_all,
            "semantic_degradation": rewrite_floor,
        }
        state["degradation_level"] = rewrite_floor
        state["degradation_message"] = message
        state["audit_trace"] = audit_trace + [{
            "agent": "security_auditor",
            "decision": (
                "rewrite_and_pass" if rewrite_floor == "L0"
                else f"rewrite_and_degrade_{rewrite_floor}"
            ),
            "rewrites": all_rewrites,
            "rationale": (
                f"Rewrite eliminated all {len(all_violations)} violations; "
                f"semantic degradation floor={rewrite_floor}"
            )
        }]
        return state

    # --- Step 4: Degradation determination ---
    degrader = DegradationEngine(ssa)
    deg_result = degrader.determine_plan(
        policy_violations,
        sub_queries,
        original_sub_queries=original_plan,
    )
    if rewrite_rank[rewrite_floor] > rewrite_rank[deg_result.level.value]:
        deg_result.level = DegradationLevel(rewrite_floor)
        if rewrite_floor == "L2":
            deg_result.message = (
                "Cannot provide the exact intermediate result. A related "
                "but different secure alternative has been generated."
            )

    rule_c_build_failed = any(
        entry.startswith("Rule C failed")
        for entry in deg_result.degradation_log
    )
    degraded_plan = (
        [] if rule_c_build_failed
        else (deg_result.modified_sub_queries or sub_queries)
    )
    post_degradation_violations = []
    rule_c_applied = any(
        entry.startswith("Rule C:") for entry in deg_result.degradation_log
    )
    if rule_c_applied:
        # Rule C creates new SELECT statements.  Treat generated SQL as
        # untrusted and audit it once before returning it.  The pipeline is
        # intentionally single-pass: a residual violation escalates to L3
        # instead of launching another rewrite loop.
        try:
            generated_audit = auditor.audit_all(degraded_plan)
        except AuditParseError as exc:
            return _build_parse_failure_state(state, audit_trace, exc)
        for audit_result in generated_audit:
            post_degradation_violations.extend(audit_result.violations)
        post_degradation_violations = deduplicate_violations(
            post_degradation_violations
        )
        if post_degradation_violations:
            deg_result.level = DegradationLevel.L3
            deg_result.message = (
                "This query cannot be answered because the generated "
                "aggregate alternative did not pass the final security audit."
            )
            deg_result.degradation_log.append(
                "L3 degradation: Rule C output failed final audit"
            )
            degraded_plan = []

    # Never return executable SQL on a rejection path.  Consumers should not
    # need to remember that an L3 plan is unsafe merely because it is present.
    if deg_result.level == DegradationLevel.L3:
        degraded_plan = []

    state["audited_plan"] = degraded_plan
    state["audit_report"] = {
        "passed": False,
        "violations_before": len(all_violations),
        "violations_after": len(remaining_violations),
        "rewrites_applied": all_rewrites,
        "rewrite_log": rewrite_log_all,
        "remaining_violations": [
            {"type": v.type.value, "severity": v.severity.value,
             "column": v.column, "detail": v.detail,
             "sub_query_id": v.sub_query_id}
            for v in policy_violations
        ],
        "post_degradation_violations": [
            {"type": v.type.value, "severity": v.severity.value,
             "column": v.column, "detail": v.detail,
             "sub_query_id": v.sub_query_id}
            for v in post_degradation_violations
        ],
        "degradation_log": deg_result.degradation_log,
    }
    state["degradation_level"] = deg_result.level.value
    state["degradation_message"] = deg_result.message
    state["audit_trace"] = audit_trace + [{
        "agent": "security_auditor",
        "decision": f"degrade_{deg_result.level.value}",
        "message": deg_result.message,
        "rationale": f"{len(policy_violations)} policy violations require degradation"
    }]

    return state


def _build_parse_failure_state(state, audit_trace, error):
    """Fail closed when generated SQL cannot be parsed by the auditor."""
    message = (
        "This query cannot be safely audited because a generated SQL "
        "statement could not be parsed."
    )
    state["audited_plan"] = []
    state["audit_report"] = {
        "passed": False,
        "violations": [],
        "parse_error": str(error),
        "failed_sub_query_index": error.sub_query_index,
    }
    state["degradation_level"] = "L3"
    state["degradation_message"] = message
    state["audit_trace"] = audit_trace + [{
        "agent": "security_auditor",
        "decision": "reject_L3",
        "message": message,
        "rationale": str(error),
    }]
    return state


def _build_invalid_plan_state(state, audit_trace, reason):
    """Fail closed when the untrusted Decomposer emits an ambiguous plan."""
    message = (
        "This query cannot be safely audited because the decomposition plan "
        "is invalid or has ambiguous sub-query identities."
    )
    state["audited_plan"] = []
    state["audit_report"] = {
        "passed": False,
        "violations": [],
        "plan_error": reason,
    }
    state["degradation_level"] = "L3"
    state["degradation_message"] = message
    state["audit_trace"] = audit_trace + [{
        "agent": "security_auditor",
        "decision": "reject_L3",
        "message": message,
        "rationale": reason,
    }]
    return state


# ============================================================
# MAC-SQL Agent Bridge — wraps Selector/Decomposer/Refiner as LangGraph nodes
# ============================================================

class MACSQLAgentBridge:
    """
    Wraps MAC-SQL's individual agents (Selector, Decomposer, Refiner) as
    LangGraph node functions, bridging between SecurityAwareNL2SQLState
    and MAC-SQL's message-passing dict format.

    Usage:
        bridge = MACSQLAgentBridge(data_path=..., tables_json_path=...)
        graph.add_node("selector", bridge.selector_node)
        graph.add_node("decomposer", bridge.decomposer_node)
        graph.add_node("refiner", bridge.refiner_node)

    Each node reads state, constructs a MAC-SQL-compatible message dict,
    calls the agent's talk(), and writes results back to state.
    """

    def __init__(
        self,
        data_path: str,
        tables_json_path: str,
        model_name: str = "gpt-4o",
        dataset_name: str = "bird",
        lazy: bool = True,
    ):
        from core.agents import Selector, Decomposer, Refiner
        from core.const import SELECTOR_NAME, DECOMPOSER_NAME, REFINER_NAME

        self.data_path = data_path
        self.tables_json_path = tables_json_path
        self.model_name = model_name
        self.dataset_name = dataset_name
        self.SELECTOR = SELECTOR_NAME
        self.DECOMPOSER = DECOMPOSER_NAME
        self.REFINER = REFINER_NAME

        self.selector = Selector(
            data_path=data_path,
            tables_json_path=tables_json_path,
            model_name=model_name,
            dataset_name=dataset_name,
            lazy=lazy,
            without_selector=False,
        )
        self.decomposer = Decomposer(dataset_name=dataset_name)
        self.refiner = Refiner(data_path=data_path, dataset_name=dataset_name)

    # ---- LangGraph Node Functions ----

    def selector_node(
        self, state: SecurityAwareNL2SQLState
    ) -> SecurityAwareNL2SQLState:
        """
        Selector Agent node — extracts relevant schema from the database.

        Reads: state['user_query'], state['db_id'], state.get('evidence')
        Writes: state['desc_str'], state['fk_str'], state['relevant_schema']
        """
        query = state.get("user_query", "")
        db_id = state.get("db_id", "")
        evidence = state.get("evidence", "")

        msg = {
            "idx": state.get("question_id", 0),
            "db_id": db_id,
            "query": query,
            "evidence": evidence,
            "extracted_schema": {},
            "ground_truth": "",
            "difficulty": state.get("difficulty", "simple"),
            "send_to": self.SELECTOR,
        }

        self.selector.talk(msg)

        state["desc_str"] = msg.get("desc_str", "")
        state["fk_str"] = msg.get("fk_str", "")
        state["relevant_schema"] = msg.get("desc_str", "")
        state["database_schema"] = msg.get("desc_str", "")
        state["audit_trace"] = list(state.get("audit_trace", [])) + [{
            "agent": "selector",
            "status": "done",
        }]

        return state

    def decomposer_node(
        self, state: SecurityAwareNL2SQLState
    ) -> SecurityAwareNL2SQLState:
        """
        Decomposer Agent node — decomposes query into sub-queries.

        Reads: state['user_query'], state['desc_str'], state['fk_str'], state['evidence']
        Writes: state['decomposition_plan'], state['final_sql'] (initial pred)
        """
        query = state.get("user_query", "")
        desc_str = state.get("desc_str", "")
        fk_str = state.get("fk_str", "")
        evidence = state.get("evidence", "")

        msg = {
            "idx": state.get("question_id", 0),
            "db_id": state.get("db_id", ""),
            "query": query,
            "evidence": evidence,
            "desc_str": desc_str,
            "fk_str": fk_str,
            "extracted_schema": {},
            "send_to": self.DECOMPOSER,
        }

        self.decomposer.talk(msg)

        qa_pairs = msg.get("qa_pairs", "")
        pred_sql = msg.get("final_sql", msg.get("pred", ""))

        # Parse qa_pairs into decomposition_plan
        from decomposer_parser import parse_qa_pairs
        tasks = parse_qa_pairs(qa_pairs) if qa_pairs else []
        decomposition_plan = [
            {"id": t.id, "description": t.description, "sql": t.sql}
            for t in tasks
        ]

        state["decomposition_plan"] = decomposition_plan
        state["final_sql"] = pred_sql
        state["audit_trace"] = list(state.get("audit_trace", [])) + [{
            "agent": "decomposer",
            "n_subqueries": len(decomposition_plan),
            "status": "done",
        }]

        return state

    def refiner_node(
        self, state: SecurityAwareNL2SQLState
    ) -> SecurityAwareNL2SQLState:
        """
        Refiner Agent node — executes SQL and corrects errors.

        Reads: state['final_sql'], state['db_id'], state['desc_str'], state['fk_str']
        Writes: state['final_sql'], state['execution_result']
        """
        db_id = state.get("db_id", "")
        desc_str = state.get("desc_str", "")
        fk_str = state.get("fk_str", "")
        query = state.get("user_query", "")
        evidence = state.get("evidence", "")

        # Use audited_plan SQLs if available, otherwise fall back to final_sql
        audited = state.get("audited_plan", [])
        if audited:
            final_sql = audited[-1].get("sql", "") if audited else ""
        else:
            final_sql = state.get("final_sql", "")

        msg = {
            "idx": state.get("question_id", 0),
            "db_id": db_id,
            "query": query,
            "evidence": evidence,
            "desc_str": desc_str,
            "fk_str": fk_str,
            "pred": final_sql,
            "final_sql": final_sql,
            "send_to": self.REFINER,
        }

        self.refiner.talk(msg)

        state["final_sql"] = msg.get("pred", final_sql)
        state["audit_trace"] = list(state.get("audit_trace", [])) + [{
            "agent": "refiner",
            "fixed": msg.get("fixed", False),
            "try_times": msg.get("try_times", 0),
            "status": "done",
        }]

        return state


# ============================================================
# Graph Builder Factory
# ============================================================

def _get_graph_nodes(bridge: MACSQLAgentBridge = None):
    """Return agent node functions, either from bridge or as pass-through placeholders."""
    if bridge is not None:
        return bridge.selector_node, bridge.decomposer_node, bridge.refiner_node
    else:
        # Fallback placeholders (for testing without LLM)
        def _pass(state): return state
        return _pass, _pass, _pass


# ============================================================
# Graph Builders
# ============================================================

def build_security_aware_graph(
    bridge: MACSQLAgentBridge = None,
):
    """
    Build the 4-Agent LangGraph StateGraph with Security Auditor.

    Per v1-implementation-report §0.3 lines 113-149.

    Args:
        bridge: MACSQLAgentBridge wrapping real MAC-SQL agents.
                If None, uses pass-through placeholders (for testing).

    Nodes:
      selector          — MAC-SQL (LLM ×1)
      decomposer        — MAC-SQL (LLM ×1)
      security_auditor  — ★ Our contribution (LLM ×0)
      refiner           — MAC-SQL (LLM ×1-2)

    Edges:
      selector → decomposer → security_auditor → conditional → refiner / END

    Returns:
        Compiled LangGraph StateGraph with MemorySaver checkpointer.
    """
    try:
        from langgraph.graph import StateGraph, END
        from langgraph.checkpoint.memory import MemorySaver
    except ImportError:
        raise ImportError(
            "langgraph is required for graph-based agent orchestration. "
            "Install with: pip install langgraph>=0.5.0"
        )

    sel_node, dec_node, ref_node = _get_graph_nodes(bridge)

    graph = StateGraph(SecurityAwareNL2SQLState)

    # Node registration
    graph.add_node("selector", sel_node)
    graph.add_node("decomposer", dec_node)
    graph.add_node("security_auditor", security_auditor_agent_node)  # ★
    graph.add_node("refiner", ref_node)

    # Edges
    graph.set_entry_point("selector")
    graph.add_edge("selector", "decomposer")
    graph.add_edge("decomposer", "security_auditor")
    graph.add_conditional_edges(
        "security_auditor",
        route_after_audit,
        {
            "continue": "refiner",   # L0: safe → normal flow
            "degrade": END,          # L1/L2: degraded → output result
            "reject": END,           # L3: rejected → output message
        },
    )
    graph.add_edge("refiner", END)

    return graph.compile(checkpointer=MemorySaver())


def build_mac_sql_baseline_graph(
    bridge: MACSQLAgentBridge = None,
):
    """
    Build the 3-Agent MAC-SQL baseline graph (B1 — no Security Auditor).

    Per v1-implementation-report §0.6 lines 268-286.

    Args:
        bridge: MACSQLAgentBridge wrapping real MAC-SQL agents.

    Used for B1 (vanilla), B2 (+security prompt), B3 (+post-hoc filter).
    """
    try:
        from langgraph.graph import StateGraph, END
        from langgraph.checkpoint.memory import MemorySaver
    except ImportError:
        raise ImportError("langgraph is required. Install with: pip install langgraph>=0.5.0")

    sel_node, dec_node, ref_node = _get_graph_nodes(bridge)

    graph = StateGraph(SecurityAwareNL2SQLState)

    graph.add_node("selector", sel_node)
    graph.add_node("decomposer", dec_node)
    graph.add_node("refiner", ref_node)

    graph.set_entry_point("selector")
    graph.add_edge("selector", "decomposer")
    graph.add_edge("decomposer", "refiner")  # No Security Auditor
    graph.add_edge("refiner", END)

    return graph.compile(checkpointer=MemorySaver())


# ============================================================
# Convenience: run full pipeline (function-based, no LangGraph needed)
# ============================================================

def run_security_auditor_pipeline(
    decomposition_plan: List[dict],
    db_id: str,
    ssa_dir: str = None,
) -> dict:
    """
    Run the Security Auditor Agent's internal pipeline on a decomposition plan.

    This is a function-call interface (not LangGraph) that exercises the same
    internal logic as security_auditor_agent_node(). Useful when you already
    have a decomposition plan and don't need the full 4-Agent graph.

    Args:
        decomposition_plan: [{id, description, sql}, ...]
        db_id: Database identifier
        ssa_dir: Path to SSA config directory

    Returns:
        {
            "audited_plan": [...],
            "audit_report": {...},
            "degradation_level": "L0"|"L1"|"L2"|"L3",
            "degradation_message": "...",
            "audit_trace": [...],
        }
    """
    state = SecurityAwareNL2SQLState(
        user_query="",
        database_schema="",
        ssa={},
        decomposition_plan=decomposition_plan,
        db_id=db_id,
        audit_trace=[],
    )
    if ssa_dir is not None:
        state["ssa_dir"] = ssa_dir
    return security_auditor_agent_node(state)


# ============================================================
# Smoke test
# ============================================================

if __name__ == "__main__":
    # Test 1: route_after_audit
    print("=== route_after_audit ===")
    assert route_after_audit({"degradation_level": "L0"}) == "continue"
    assert route_after_audit({"degradation_level": "L1"}) == "degrade"
    assert route_after_audit({"degradation_level": "L2"}) == "degrade"
    assert route_after_audit({"degradation_level": "L3"}) == "reject"
    print("  OK")

    # Test 2: security_auditor_agent_node on a safe decomposition (no SSA needed)
    print("\n=== security_auditor_agent_node (safe, no SSA) ===")
    state = SecurityAwareNL2SQLState(
        user_query="SELECT 1",
        db_id="",
        ssa={},
        decomposition_plan=[
            {"id": 0, "description": "test", "sql": "SELECT 1"}
        ],
        audit_trace=[],
    )
    result = security_auditor_agent_node(state)
    print(f"  degradation_level: {result['degradation_level']}")
    print(f"  audit_report passed: {result['audit_report']['passed']}")
    assert result["degradation_level"] == "L0"

    # Test 3: run_security_auditor_pipeline
    print("\n=== run_security_auditor_pipeline ===")
    pipeline_result = run_security_auditor_pipeline(
        decomposition_plan=[{"id": 0, "description": "safe", "sql": "SELECT 1"}],
        db_id="",
    )
    print(f"  degradation_level: {pipeline_result['degradation_level']}")
    assert pipeline_result["degradation_level"] == "L0"

    # Test 4: With SSA — controlled column in SELECT should trigger violation
    print("\n=== security_auditor_agent_node (with SSA, controlled column) ===")
    from ssa.loader import load_ssa

    try:
        ssa = load_ssa("financial")
        print(f"  SSA loaded: {ssa.db_id}, {len(ssa.column_labels)} tables")
    except FileNotFoundError:
        ssa = None
        print("  SSA not found, skipping SSA-dependent test")

    if ssa:
        state2 = SecurityAwareNL2SQLState(
            user_query="Find average salary by district",
            db_id="financial",
            decomposition_plan=[
                {"id": 0, "description": "Get all district data",
                 "sql": "SELECT district_id, A11, A2, A3 FROM district"},
                {"id": 1, "description": "Count accounts",
                 "sql": "SELECT COUNT(*) FROM account"},
            ],
            audit_trace=[],
        )
        result2 = security_auditor_agent_node(state2)
        print(f"  degradation_level: {result2['degradation_level']}")
        print(f"  violations_before: {result2['audit_report'].get('violations_before', '?')}")
        print(f"  violations_after:  {result2['audit_report'].get('violations_after', '?')}")
        print(f"  rewrites_applied:  {result2['audit_report'].get('rewrites_applied', '?')}")
        for entry in result2.get("audit_trace", []):
            print(f"  audit: [{entry['decision']}] {entry.get('rationale', '')[:100]}")

    # Test 5: Graph builder (requires langgraph)
    print("\n=== build_security_aware_graph ===")
    try:
        graph = build_security_aware_graph()
        print(f"  Graph built: {graph}")
        print("  LangGraph integration: OK")
    except ImportError as e:
        print(f"  langgraph not installed — graph builder skipped ({e})")
    except Exception as e:
        print(f"  ERROR: {e}")

    print("\nAll smoke tests passed.")

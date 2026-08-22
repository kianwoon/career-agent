"""LangGraph supervisor graph definition."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agent.nodes import (
    AgentState,
    check_human,
    deduplicate,
    extract,
    match_rank,
    normalize,
    plan_search,
    run_search,
    understand,
)


def build_graph():
    """Build the Phase 1 supervisor graph."""
    builder = StateGraph(AgentState)

    builder.add_node("understand", understand)
    builder.add_node("plan_search", plan_search)
    builder.add_node("run_search", run_search)
    builder.add_node("extract", extract)
    builder.add_node("normalize", normalize)
    builder.add_node("deduplicate", deduplicate)
    builder.add_node("match_rank", match_rank)
    builder.add_node("check_human", check_human)

    builder.add_edge(START, "understand")
    builder.add_edge("understand", "plan_search")
    builder.add_edge("plan_search", "run_search")
    builder.add_edge("run_search", "extract")
    builder.add_edge("extract", "normalize")
    builder.add_edge("normalize", "deduplicate")
    builder.add_edge("deduplicate", "match_rank")
    builder.add_edge("match_rank", "check_human")
    builder.add_edge("check_human", END)

    return builder.compile()


# Compile once at import time.
supervisor_graph = build_graph()

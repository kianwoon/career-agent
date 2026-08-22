"""Tests for the LangGraph supervisor pipeline."""

import asyncio

from app.agent.graph import supervisor_graph
from app.agent.nodes import AgentState
from app.models.schemas import SearchType, TaskStatus


def test_supervisor_pipeline_runs():
    async def run():
        initial: AgentState = {
            "task_id": "t1",
            "type": SearchType.jobs,
            "query": "AI leadership",
            "location": "Singapore",
            "status": TaskStatus.pending,
            "profile": {
                "headline": "AI leader",
                "summary": "AI platform leadership",
                "skills": ["ai", "ml", "leadership"],
                "preferences": {"location": "Singapore"},
            },
        }
        return await supervisor_graph.ainvoke(initial)

    result = asyncio.run(run())
    assert result["status"] == TaskStatus.completed
    # The graph runs the real LinkedIn adapter when the Brave CDP session is
    # available; otherwise it returns seed results. Either way, results exist.
    assert len(result["results"]) >= 1
    steps = [e.step for e in result["timeline"]]
    assert steps == ["UNDERSTAND", "PLAN SEARCH", "RUN SEARCH", "EXTRACT", "NORMALIZE", "DEDUPLICATE", "MATCH / RANK"]

"""Tests for the LangGraph supervisor pipeline."""

import asyncio
from unittest.mock import AsyncMock, patch

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
        # Mock the LinkedIn adapter so the pipeline runs deterministically in
        # CI without a live browser/CDP session.
        fake_search = AsyncMock(
            return_value={
                "raw_results": [
                    {
                        "id": "seed-1",
                        "title": "Head of AI Platform",
                        "company": "Test Corp",
                        "location": "Singapore",
                        "source": "linkedin",
                        "source_url": "https://example.com/jobs/1",
                        "description": "We need AI and ML platform leadership.",
                    }
                ],
                "needs_human": False,
                "human_reason": None,
            }
        )
        with patch("app.services.linkedin.search_linkedin_jobs", fake_search):
            return await supervisor_graph.ainvoke(initial)

    result = asyncio.run(run())
    assert result["status"] == TaskStatus.completed
    assert len(result["results"]) >= 1
    steps = [e.step for e in result["timeline"]]
    assert steps == ["UNDERSTAND", "PLAN SEARCH", "RUN SEARCH", "EXTRACT", "NORMALIZE", "DEDUPLICATE", "MATCH / RANK"]

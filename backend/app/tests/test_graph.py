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
        # Mock all adapters so the pipeline runs deterministically in CI
        # without a live browser/CDP session or a network call.
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
        fake_mcf = AsyncMock(
            return_value={
                "raw_results": [
                    {
                        "id": "MCF-2026-0000001",
                        "title": "AI Engineer",
                        "company": "Test Co SG",
                        "location": "Singapore",
                        "source": "mycareersfuture",
                        "source_url": "https://www.mycareersfuture.gov.sg/job/ai-engineer-1",
                        "description": "AI and ML engineering on the Singapore platform.",
                    }
                ],
                "needs_human": False,
                "human_reason": None,
            }
        )
        fake_fj = AsyncMock(
            return_value={
                "raw_results": [
                    {
                        "id": "fj-12345",
                        "title": "Software Engineer",
                        "company": "Test Pte Ltd",
                        "location": "Singapore",
                        "source": "fastjobs",
                        "source_url": "https://www.fastjobs.sg/singapore-job-ad/12345/software-engineer/test-pte/",
                        "description": "Building software on the Singapore team.",
                    }
                ],
                "needs_human": False,
                "human_reason": None,
            }
        )
        with (
            patch("app.services.linkedin.search_linkedin_jobs", fake_search),
            patch("app.services.mycareersfuture.search_mycareersfuture_jobs", fake_mcf),
            patch("app.services.fastjobs.search_fastjobs_jobs", fake_fj),
        ):
            return await supervisor_graph.ainvoke(initial)

    # The graph persists via app.db, whose pooled asyncpg connections may be
    # bound to a different (already-closed) event loop from an earlier test
    # module. Run with a NullPool engine so each asyncio.run gets fresh
    # connections and no pooled Future crosses loops.
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app import db

    engine = create_async_engine(db.settings.database_url, poolclass=NullPool)
    test_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    with patch.object(db, "engine", engine), patch.object(db, "async_session", test_session):
        result = asyncio.run(run())
    asyncio.run(engine.dispose())
    assert result["status"] == TaskStatus.completed
    # The low-relevance FastJobs row ("Software Engineer" vs an AI-leadership
    # profile) legitimately scores below the min-score quality gate and is
    # dropped; the two on-topic rows from LinkedIn and MyCareersFuture survive.
    assert len(result["results"]) >= 2
    sources = {r.source for r in result["results"]}
    assert "linkedin" in sources
    assert "mycareersfuture" in sources
    steps = [e.step for e in result["timeline"]]
    assert steps == ["UNDERSTAND", "PLAN SEARCH", "RUN SEARCH", "EXTRACT", "NORMALIZE", "DEDUPLICATE", "MATCH / RANK"]

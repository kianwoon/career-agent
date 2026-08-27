"""API routes for the Career Agent service."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from app.agent.graph import supervisor_graph
from app.agent.nodes import AgentState
from app.api.security import require_api_key
from app.db import get_db
from app.models.orm import SearchTask
from app.models.schemas import (
    ApprovalDecision,
    ApprovalRequest,
    BrowserSessionView,
    BrowserTakeoverRequest,
    CandidateSearchRequest,
    JobSearchRequest,
    MatchResult,
    SearchHistoryItem,
    SearchHistoryResponse,
    SearchTaskResult,
    SearchType,
    TaskStatus,
    TaskStatusResponse,
)
from app.services.browser import BrowserError, browser_service

router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(require_api_key)],
)


# ---------------------------------------------------------------------------
# Search tasks
# ---------------------------------------------------------------------------


@router.post("/search/jobs", status_code=201)
async def start_job_search(
    req: JobSearchRequest,
    db: AsyncSession = Depends(get_db),
) -> TaskStatusResponse:
    """Create and start a job search task."""
    return await _start_task(db, SearchType.jobs, query=req.query, location=req.location, source_ids=req.sources)


@router.post("/search/candidates", status_code=201)
async def start_candidate_search(
    req: CandidateSearchRequest,
    db: AsyncSession = Depends(get_db),
) -> TaskStatusResponse:
    """Create and start a candidate search task."""
    return await _start_task(db, SearchType.candidates, query=req.query, location=req.location, source_ids=req.sources)


async def _default_user_id(db: AsyncSession) -> str | None:
    """Resolve the demo user id (single-user Phase 1)."""
    from app.models.orm import User

    user = (
        await db.execute(select(User).limit(1))
    ).scalar_one_or_none()
    return user.id if user else None


async def _start_task(
    db: AsyncSession,
    task_type: SearchType,
    query: str,
    location: str | None = None,
    source_ids: list[str] | None = None,
) -> TaskStatusResponse:
    task = SearchTask(
        id=str(uuid.uuid4()),
        type=task_type.value,
        query=query,
        status=TaskStatus.pending.value,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    # Return immediately; run the agent in the background so the caller does
    # not block. Poll GET /tasks/{id} for completion.
    asyncio.create_task(_run_task(task.id, task_type, query, location, source_ids))
    return TaskStatusResponse(
        task_id=task.id,
        type=task_type,
        status=TaskStatus.pending,
        workflow_state=task.workflow_state,
        created_at=task.created_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
        error=task.error,
    )


async def _run_task(
    task_id: str,
    task_type: SearchType,
    query: str,
    location: str | None,
    source_ids: list[str] | None = None,
) -> None:
    """Execute the LangGraph pipeline for a task in the background."""
    from app.db import async_session as _session_factory

    async with _session_factory() as db:
        task = await db.get(SearchTask, task_id)
        if task is None:
            return
        task.status = TaskStatus.running.value
        task.started_at = datetime.utcnow()
        await db.commit()

        # Seed the profile so the matching engine has something to score against.
        profile = {
            "headline": "AI Power User",
            "summary": "Applying frontier models to real-world problems; technology leadership",
            "skills": ["ai", "ml", "leadership", "platform", "engineering", "product"],
            "preferences": {"location": location or "Singapore"},
        }

        initial: AgentState = {
            "task_id": task.id,
            "type": task_type,
            "query": query,
            "location": location,
            "status": TaskStatus.running,
            "profile": profile,
            "source_ids": source_ids,
        }

        try:
            result_state = await supervisor_graph.ainvoke(initial)
            task.status = result_state.get("status", TaskStatus.completed).value
            task.workflow_state = "complete"
            if result_state.get("error"):
                task.error = result_state["error"]
            # Persist per-source issues (expired logins etc.) as JSON in the
            # error column so GET /tasks/{id}/results can surface them.
            source_issues = result_state.get("source_issues") or []
            if source_issues:
                import json as _json

                task.error = _json.dumps({"source_issues": source_issues})
            task.completed_at = datetime.utcnow()

            # Persist ranked results (jobs or candidates) + match evaluations.
            from app.models.orm import Candidate, Job, MatchEvaluation

            user_id = await _default_user_id(db)
            for r in result_state.get("results", []):
                if task_type == SearchType.jobs:
                    entity = (
                        await db.execute(
                            select(Job).where(Job.source_url == (r.source_url or ""))
                        )
                    ).scalar_one_or_none()
                    if entity is None:
                        entity = Job(
                            user_id=user_id,
                            title=r.title,
                            company=r.subtitle or r.title,
                            location=r.location,
                            description=r.match_reason or "",
                            source=r.source,
                            source_url=r.source_url or "",
                            posted_at=None,
                            employment_type=None,
                            salary_text=None,
                        )
                        db.add(entity)
                        await db.flush()
                else:
                    entity = (
                        await db.execute(
                            select(Candidate).where(Candidate.source_url == (r.source_url or ""))
                        )
                    ).scalar_one_or_none()
                    if entity is None:
                        entity = Candidate(
                            user_id=user_id,
                            name=r.title,
                            headline=r.subtitle,
                            location=r.location,
                            summary=getattr(r, "summary", "") or r.match_reason or "",
                            skills=getattr(r, "skills", []) or [],
                            experience=getattr(r, "experience", "") or "",
                            education=getattr(r, "education", "") or "",
                            certifications=getattr(r, "certifications", "") or "",
                            source=r.source,
                            source_url=r.source_url or "",
                        )
                        db.add(entity)
                        await db.flush()
                    else:
                        # Enrich an existing row if it was created before
                        # profile enrichment existed.
                        enriched_summary = getattr(r, "summary", "") or ""
                        enriched_skills = getattr(r, "skills", []) or []
                        enriched_experience = getattr(r, "experience", "") or ""
                        if enriched_summary and len(enriched_summary) > len(entity.summary or ""):
                            entity.summary = enriched_summary
                        if enriched_skills:
                            entity.skills = enriched_skills
                        if enriched_experience and len(enriched_experience) > len(entity.experience or ""):
                            entity.experience = enriched_experience
                        if getattr(r, "education", ""):
                            entity.education = r.education
                        if getattr(r, "certifications", ""):
                            entity.certifications = r.certifications
                db.add(
                    MatchEvaluation(
                        task_id=task.id,
                        entity_type="job" if task_type == SearchType.jobs else "candidate",
                        entity_id=entity.id,
                        score=r.match_score,
                        reason=r.match_reason or "",
                    )
                )
            await db.commit()
        except Exception as exc:
            task.status = TaskStatus.failed.value
            task.error = str(exc)
            await db.commit()


@router.get("/search/history", response_model=SearchHistoryResponse)
async def search_history(db: AsyncSession = Depends(get_db)) -> SearchHistoryResponse:
    """List past search tasks (most recent first), with result counts.

    Lets users browse previous successful searches and their results.
    """
    from app.models.orm import MatchEvaluation

    tasks = (
        (
            await db.execute(
                select(SearchTask).order_by(SearchTask.created_at.desc()).limit(50)
            )
        )
        .scalars()
        .all()
    )

    items: list[SearchHistoryItem] = []
    for t in tasks:
        count = (
            await db.execute(
                select(func.count())
                .select_from(MatchEvaluation)
                .where(MatchEvaluation.task_id == t.id)
            )
        ).scalar_one()
        items.append(
            SearchHistoryItem(
                task_id=t.id,
                type=SearchType(t.type),
                query=t.query,
                status=TaskStatus(t.status),
                result_count=count or 0,
                created_at=t.created_at,
                completed_at=t.completed_at,
            )
        )
    return SearchHistoryResponse(items=items)


@router.get("/tasks/{task_id}", response_model=TaskStatusResponse)
async def get_task(task_id: str, db: AsyncSession = Depends(get_db)) -> TaskStatusResponse:
    task = await db.get(SearchTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskStatusResponse(
        task_id=task.id,
        type=SearchType(task.type),
        status=TaskStatus(task.status),
        workflow_state=task.workflow_state,
        created_at=task.created_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
        error=task.error,
    )


@router.get("/tasks/{task_id}/results", response_model=SearchTaskResult)
async def get_task_results(
    task_id: str, db: AsyncSession = Depends(get_db)
) -> SearchTaskResult:
    task = await db.get(SearchTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    # Load match evaluations for this task, joined with the underlying
    # entity (job or candidate).
    from app.models.orm import Candidate, Job, MatchEvaluation

    evals = (
        (
            await db.execute(
                select(MatchEvaluation)
                .where(MatchEvaluation.task_id == task_id)
                .order_by(MatchEvaluation.score.desc())
            )
        )
        .scalars()
        .all()
    )

    # Determine entity type from the first eval.
    entity_type = evals[0].entity_type if evals else "job"

    results: list[MatchResult] = []
    for ev in evals:
        if entity_type == "candidate":
            entity = await db.get(Candidate, ev.entity_id)
        else:
            entity = await db.get(Job, ev.entity_id)
        if entity is None:
            continue
        if entity_type == "candidate":
            from app.models.schemas import CandidateMatchResult
            from app.services.credibility import assess_credibility

            cred = assess_credibility({
                "name": entity.name,
                "headline": entity.headline or "",
                "skills": entity.skills or [],
                "experience": entity.experience or "",
            })

            results.append(
                CandidateMatchResult(
                    id=entity.id,
                    title=entity.name,
                    subtitle=entity.headline,
                    location=entity.location,
                    source=entity.source,
                    source_url=entity.source_url,
                    match_score=ev.score,
                    match_reason=ev.reason,
                    evidence=[],
                    gaps=[],
                    summary=entity.summary,
                    skills=entity.skills or [],
                    experience=entity.experience,
                    education=entity.education,
                    certifications=entity.certifications,
                    credibility=cred.to_dict(),
                )
            )
        else:
            results.append(
                MatchResult(
                    id=entity.id,
                    title=entity.title,
                    subtitle=entity.company,
                    location=entity.location,
                    source=entity.source,
                    source_url=entity.source_url,
                    match_score=ev.score,
                    match_reason=ev.reason,
                    evidence=[],
                    gaps=[],
                )
            )

    # Surface per-source failures (e.g. expired login) so the UI can prompt
    # a re-login instead of silently missing results.
    source_issues: list[str] = []
    if task.error:
        import json as _json

        try:
            payload = _json.loads(task.error)
            if isinstance(payload, dict):
                source_issues = payload.get("source_issues", [])
            else:
                source_issues = []
        except (ValueError, TypeError):
            source_issues = []

    return SearchTaskResult(
        task_id=task.id,
        status=TaskStatus(task.status),
        results=results,
        summary=f"{len(results)} ranked results",
        source_issues=source_issues,
    )


# ---------------------------------------------------------------------------
# Browser sessions
# ---------------------------------------------------------------------------


@router.post("/browser/sessions", status_code=201)
async def create_browser_session(db: AsyncSession = Depends(get_db)) -> BrowserSessionView:
    from app.models.orm import BrowserSession

    session_id = str(uuid.uuid4())
    # Persist a DB row so capture/replay can find it by id.
    user_id = await _default_user_id(db)
    db.add(
        BrowserSession(
            id=session_id,
            user_id=user_id,
            profile_name="default",
            status="idle",
        )
    )
    await db.commit()
    return BrowserSessionView(session_id=session_id, status="idle")


@router.post("/browser/{session_id}/takeover", response_model=BrowserSessionView)
async def browser_takeover(
    session_id: str, req: BrowserTakeoverRequest
) -> BrowserSessionView:
    try:
        await browser_service.get_session(session_id)
    except BrowserError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if req.action == "status":
        return BrowserSessionView(session_id=session_id, status="running")
    if req.action == "return":
        return BrowserSessionView(session_id=session_id, status="agent")
    # start: mark that a human is taking over
    return BrowserSessionView(session_id=session_id, status="human", needs_human=True)


@router.get("/browser/{session_id}/observe", response_model=BrowserSessionView)
async def browser_observe(session_id: str) -> BrowserSessionView:
    try:
        session = await browser_service.get_session(session_id)
        result = await session.observe()
    except BrowserError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return BrowserSessionView(
        session_id=session_id,
        status="running",
        url=result.url,
        title=result.title,
    )


@router.post("/browser/{session_id}/capture", response_model=BrowserSessionView)
async def browser_capture(session_id: str, db: AsyncSession = Depends(get_db)) -> BrowserSessionView:
    """Capture the live signed-in browser's session state (cookies + localStorage).

    Connects to the authenticated Brave instance via CDP, reads storage_state
    for LinkedIn, encrypts it, and stores on the session row.
    """
    from app.models.orm import BrowserSession
    from app.services.session import capture_from_cdp

    session = await db.get(BrowserSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Browser session not found")

    try:
        await capture_from_cdp(session)
        await db.commit()
    except Exception as exc:
        logger.warning("Capture failed for %s: %s", session_id, exc)
        raise HTTPException(status_code=500, detail=f"Capture failed: {exc}") from exc

    return BrowserSessionView(
        session_id=session.id,
        status="captured",
        url="https://www.linkedin.com/",
        title="Captured session",
    )


@router.post("/browser/{session_id}/replay", response_model=BrowserSessionView)
async def browser_replay(session_id: str, db: AsyncSession = Depends(get_db)) -> BrowserSessionView:
    """Replay a captured session in a fresh headless Chromium and verify login."""
    from app.models.orm import BrowserSession
    from app.services.session import replay_session

    session = await db.get(BrowserSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Browser session not found")
    if not session.session_state:
        raise HTTPException(status_code=409, detail="Session has no captured state — capture first")

    try:
        result = await replay_session(session)
    except Exception as exc:
        logger.warning("Replay failed for %s: %s", session_id, exc)
        raise HTTPException(status_code=500, detail=f"Replay failed: {exc}") from exc

    if result == "logged_in":
        return BrowserSessionView(
            session_id=session.id,
            status="ready",
            url="https://www.linkedin.com/feed/",
            title="Replay OK — logged in",
        )
    return BrowserSessionView(
        session_id=session.id,
        status="needs_human",
        needs_human=True,
        reason="Session replay did not stay logged in (cookies expired?) — re-capture or sign in manually",
    )


@router.post("/browser/{session_id}/refresh", response_model=BrowserSessionView)
async def browser_refresh(session_id: str, db: AsyncSession = Depends(get_db)) -> BrowserSessionView:
    """Re-capture the session from the live signed-in browser (CDP).

    Refreshes near-expiry cookies. Requires the signed-in browser to be
    running on the CDP port. Returns needs_human if CDP is unavailable.
    """
    from app.models.orm import BrowserSession
    from app.services.session import refresh_from_cdp

    session = await db.get(BrowserSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Browser session not found")

    try:
        refreshed = await refresh_from_cdp(session)
        await db.commit()
    except Exception as exc:
        logger.warning("Refresh failed for %s: %s", session_id, exc)
        raise HTTPException(status_code=500, detail=f"Refresh failed: {exc}") from exc

    if refreshed:
        return BrowserSessionView(
            session_id=session.id,
            status="captured",
            url="https://www.linkedin.com/",
            title="Session refreshed",
        )
    return BrowserSessionView(
        session_id=session.id,
        status="needs_human",
        needs_human=True,
        reason="Could not refresh via CDP (browser not running?) — sign in manually and re-capture",
    )


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------


@router.post("/approvals/{approval_id}")
async def decide_approval(approval_id: str, req: ApprovalRequest) -> dict:
    """Record an approval decision.

    Phase 1 stores no pending approvals yet; this endpoint validates the
    decision shape and returns a stub. The policy engine (Milestone 5) will
    wire this to real pending actions.
    """
    if req.decision not in (ApprovalDecision.approve, ApprovalDecision.reject):
        raise HTTPException(status_code=422, detail="Invalid decision")
    return {
        "approval_id": approval_id,
        "decision": req.decision.value,
        "status": "processed",
        "note": req.note,
    }


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "career-agent-api", "time": datetime.utcnow().isoformat()}

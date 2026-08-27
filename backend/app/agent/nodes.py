"""LangGraph workflow nodes for the Career Agent supervisor.

Phase 1 implements a deterministic StateGraph that mirrors the design-spec
workflow:

    REQUEST -> UNDERSTAND -> PLAN SEARCH -> RUN SEARCH -> EXTRACT
        -> NORMALIZE -> DEDUPLICATE -> MATCH/RANK -> RETURN RESULTS

Each node writes structured state; a checkpoint (Redis-backed in production)
allows durable task state and resume.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, TypedDict

from app.models.schemas import ActivityEvent, MatchResult, SearchType, TaskStatus

logger = logging.getLogger(__name__)


class AgentState(TypedDict, total=False):
    task_id: str
    type: SearchType
    query: str
    location: str | None
    status: TaskStatus
    profile: dict[str, Any]
    raw_results: list[dict[str, Any]]
    normalized: list[dict[str, Any]]
    results: list[MatchResult]
    timeline: list[ActivityEvent]
    error: str | None
    needs_human: bool
    human_reason: str | None
    source_ids: list[str] | None


def _log(state: AgentState, step: str, detail: str | None = None, url: str | None = None) -> list[ActivityEvent]:
    """Append an activity event to the timeline (best-effort state copy)."""
    events = list(state.get("timeline", []))
    events.append(ActivityEvent(step=step, detail=detail, url=url))
    return events


def _extract_skills(query: str) -> list[str]:
    """Best-effort keyword extraction from a candidate search query.

    e.g. "Java, Kafka, payments, microservices, banking" -> those tokens.
    Phase 1: split on common separators; the LLM layer can do this properly.
    """
    import re

    if not query:
        return []
    # Split on commas, 'and', 'with', etc.
    parts = re.split(r"[,;]|\band\b|\bwith\b|\bplus\b", query)
    skills = [p.strip().lower() for p in parts if p.strip()]
    # Remove filler words that aren't skills.
    filler = {"experience", "background", "candidate", "candidates", "looking", "need", "needed", "required", "must", "have", "strong", "exposure", "in"}
    return [s for s in skills if s not in filler][:20]


def understand(state: AgentState) -> AgentState:
    """INTERPRET USER INTENT: classify the request and normalize parameters."""
    task_type = state.get("type")
    type_label = task_type.value if isinstance(task_type, SearchType) else "unknown"
    return {
        **state,
        "status": TaskStatus.running,
        "timeline": _log(state, "UNDERSTAND", f"Parsed request type={type_label}"),
    }


def plan_search(state: AgentState) -> AgentState:
    """PLAN SEARCH STRATEGY: decide sources and query terms.

    For job searches we query multiple sources in parallel (LinkedIn +
    MyCareersFuture + FastJobs). The ``sources`` list is recorded in the
    timeline for observability but is not consumed by subsequent nodes (they
    see only the merged ``raw_results``).
    """
    sources = ["linkedin", "mycareersfuture", "fastjobs"]
    source_label = "+".join(sources)
    return {
        **state,
        "timeline": _log(state, "PLAN SEARCH", f"Sources={source_label}, query={state.get('query')}"),
    }


async def _safe_search(
    fn: Any,
    query: str,
    location: str | None,
) -> dict[str, Any]:
    """Call a search adapter and normalize failures into the adapter contract.

    Adapters may raise; ``run_search`` treats an exception the same as a
    blocked source (empty results + human_reason) so one failing source never
    takes down the other.
    """
    try:
        result = await fn(query, location)
        return result or {}
    except Exception as exc:
        return {
            "raw_results": [],
            "needs_human": True,
            "human_reason": f"{getattr(fn, '__name__', 'adapter')} failed: {exc}",
        }


async def _search_custom_sources(
    state: AgentState,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Run templatized flows for all selected/enabled custom sources.

    Returns (raw_results, ok_labels, failed_labels).
    """
    from sqlalchemy import select

    from app.db import async_session
    from app.models.orm import Source, SourceFlow
    from app.services.source_flows import execute_flow

    source_ids = state.get("source_ids")
    async with async_session() as db:
        stmt = select(Source).where(Source.enabled == True)
        if source_ids:
            stmt = stmt.where(Source.id.in_(source_ids))
        sources = (await db.execute(stmt)).scalars().all()
        flows = (
            await db.execute(select(SourceFlow).where(SourceFlow.status == "active"))
        ).scalars().all()
    flows_by_source: dict[str, SourceFlow] = {f.source_id: f for f in flows}

    query = state.get("query", "")
    raw: list[dict[str, Any]] = []
    ok: list[str] = []
    failed: list[str] = []

    async def _run_one(source: Source) -> None:
        flow = flows_by_source.get(source.id)
        flow_type = "find_jobs" if state.get("type") == SearchType.jobs else "find_candidates"
        if flow is None or flow.flow_type != flow_type:
            failed.append(f"{source.name}: no {flow_type} flow")
            return
        result = await execute_flow(
            base_url=source.base_url,
            steps=flow.steps,
            query=query,
            storage_state_encrypted=source.session_state,
            card_selectors=flow.steps[-1] if flow.steps and flow.steps[-1].get("card") else None,
        )
        # Stash card_selectors on the flow so executor can find them.
        results = result.get("results", [])
        if result.get("needs_human") or not results:
            failed.append(f"{source.name}: {result.get('human_reason', 'no results')}")
            return
        for r in results:
            r.setdefault("source", source.name)
            r.setdefault("title", "")
            raw.append(r)
        ok.append(f"{source.name}: {len(results)}")

    await asyncio.gather(*(_run_one(s) for s in sources))
    return raw, ok, failed


async def run_search(state: AgentState) -> AgentState:
    """RUN SEARCH: invoke all job-search adapters and merge the results.

    Job searches hit LinkedIn (authenticated browser), MyCareersFuture
    (public API), and FastJobs (browser) in parallel. Candidate searches
    delegate to the LinkedIn People adapter only.
    """
    query = state.get("query", "")
    location = state.get("location")
    task_type = state.get("type")

    # -------------------------------------------------------
    # Custom user-registered sources (templatized flows)
    # -------------------------------------------------------
    custom_raw, custom_ok, custom_failed = await _search_custom_sources(state)

    # -------------------------------------------------------
    # Jobs -> run LinkedIn + MyCareersFuture + FastJobs in parallel
    # -------------------------------------------------------
    if task_type == SearchType.jobs:
        from app.services.fastjobs import search_fastjobs_jobs
        from app.services.linkedin import search_linkedin_jobs
        from app.services.mycareersfuture import search_mycareersfuture_jobs

        li_result, mcf_result, fj_result = await asyncio.gather(
            _safe_search(search_linkedin_jobs, query, location),
            _safe_search(search_mycareersfuture_jobs, query, location),
            _safe_search(search_fastjobs_jobs, query, location),
        )

        raw: list[dict[str, Any]] = []
        timeline_events: list[str] = []

        li_raw = li_result.get("raw_results", [])
        li_ok = not li_result.get("needs_human", False) and li_raw
        if li_ok:
            raw.extend(li_raw)
            timeline_events.append(f"LinkedIn: {len(li_raw)} jobs")
        else:
            timeline_events.append(f"LinkedIn blocked: {li_result.get('human_reason', 'unknown')}")

        mcf_raw = mcf_result.get("raw_results", [])
        mcf_ok = not mcf_result.get("needs_human", False) and mcf_raw
        if mcf_ok:
            raw.extend(mcf_raw)
            timeline_events.append(f"MyCareersFuture: {len(mcf_raw)} jobs")
        else:
            timeline_events.append(f"MyCareersFuture: {mcf_result.get('human_reason', 'no results')}")

        fj_raw = fj_result.get("raw_results", [])
        fj_ok = not fj_result.get("needs_human", False) and fj_raw
        if fj_ok:
            raw.extend(fj_raw)
            timeline_events.append(f"FastJobs: {len(fj_raw)} jobs")
        else:
            timeline_events.append(f"FastJobs: {fj_result.get('human_reason', 'no results')}")

        # Only flag a human bottleneck if ALL sources are blocked/failed.
        all_blocked = not li_ok and not mcf_ok and not fj_ok and not custom_raw
        if all_blocked:
            reasons = []
            if li_result.get("human_reason"):
                reasons.append(li_result["human_reason"])
            if mcf_result.get("human_reason"):
                reasons.append(mcf_result["human_reason"])
            if fj_result.get("human_reason"):
                reasons.append(fj_result["human_reason"])
            reasons.extend(custom_failed)
            return {
                **state,
                "raw_results": [],
                "needs_human": True,
                "human_reason": "; ".join(reasons) if reasons else "All job sources failed",
                "timeline": _log(state, "RUN SEARCH", " | ".join(timeline_events)),
            }

        raw.extend(custom_raw)
        timeline_events.extend(f"Custom {label}" for label in custom_ok)
        timeline_events.extend(custom_failed)

        return {
            **state,
            "raw_results": raw,
            "needs_human": False,
            "human_reason": None,
            "timeline": _log(state, "RUN SEARCH", " | ".join(timeline_events)),
        }

    # -------------------------------------------------------
    # Candidates -> LinkedIn People adapter (authenticated Brave session).
    # -------------------------------------------------------
    if task_type == SearchType.candidates:
        from app.services.linkedin_people import search_linkedin_people

        try:
            result = await search_linkedin_people(query)
            raw = result.get("raw_results", [])
            needs_human = result.get("needs_human", False)
            human_reason = result.get("human_reason")
            detail = (
                f"LinkedIn people search found {len(raw)} candidates"
                if not needs_human
                else f"LinkedIn blocked: {human_reason}"
            )
            if custom_raw:
                raw = raw + custom_raw
                needs_human = False
                human_reason = None
                detail += f" | Custom: {' | '.join(custom_ok)}"
            elif custom_failed:
                detail += f" | Custom: {' | '.join(custom_failed)}"
            return {
                **state,
                "raw_results": raw,
                "needs_human": needs_human,
                "human_reason": human_reason,
                "timeline": _log(state, "RUN SEARCH", detail),
            }
        except Exception as exc:
            return {
                **state,
                "raw_results": [],
                "needs_human": True,
                "human_reason": f"LinkedIn people search failed: {exc}",
                "timeline": _log(state, "RUN SEARCH", f"LinkedIn people search error: {exc}"),
            }

    # Fallback: seed results for candidate search / unknown sources.
    raw: list[dict[str, Any]] = [
        {
            "id": f"seed-{i}",
            "title": f"Sample {query or 'Role'} - #{i}",
            "company": f"Company {i}",
            "location": location or "Singapore",
            "source": "seed",
            "source_url": f"https://example.com/jobs/{i}",
            "description": f"Responsible for {query or 'delivery'} at a fast-growing team.",
        }
        for i in range(1, 4)
    ]
    return {
        **state,
        "raw_results": raw,
        "timeline": _log(state, "RUN SEARCH", f"Seed search found {len(raw)} raw results"),
    }


def extract(state: AgentState) -> AgentState:
    """EXTRACT: pull structured fields from each result."""
    return {
        **state,
        "timeline": _log(state, "EXTRACT", "Extracted structured fields"),
    }


def normalize(state: AgentState) -> AgentState:
    """NORMALIZE: map raw results to the canonical job/candidate schema."""
    return {
        **state,
        "normalized": state.get("raw_results", []),
        "timeline": _log(state, "NORMALIZE", f"Normalized {len(state.get('raw_results', []))} records"),
    }


def deduplicate(state: AgentState) -> AgentState:
    """DEDUPLICATE: drop duplicates on (source, source_url)."""
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for item in state.get("normalized", []):
        key = (str(item.get("source", "")), str(item.get("source_url", "")))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return {
        **state,
        "normalized": unique,
        "timeline": _log(state, "DEDUPLICATE", f"{len(unique)} unique after dedup"),
    }


async def match_rank(state: AgentState) -> AgentState:
    """MATCH/RANK: score each record against the stored profile.

    Deterministic scoring first, then optional LLM reranking when enabled.
    """
    from app.services.matching import score_candidate, score_job

    profile = state.get("profile", {})
    scored: list[MatchResult] = []
    for item in state.get("normalized", []):
        if state.get("type") == SearchType.candidates:
            # For candidate search, the reference is the search query (the
            # candidate criteria), not the searcher's career profile.
            job_ref = {
                "description": state.get("query", ""),
                "location": state.get("location"),
                "required_skills": _extract_skills(state.get("query", "")),
            }
            base = score_candidate(item, job_ref)
            # Carry enriched profile fields through to the API/persistence.
            # (MatchResult base now includes these fields, so just update.)
            base.summary = item.get("summary", "")
            base.skills = item.get("skills", [])
            base.experience = item.get("experience", "")
            base.education = item.get("education", "")
            base.certifications = item.get("certifications", "")
            base.credibility = getattr(base, "credibility", None)
            # Attach credibility to the raw item for the LLM reranker.
            if base.credibility:
                item["_credibility"] = base.credibility
            scored.append(base)
        else:
            scored.append(score_job(item, profile))
    scored.sort(key=lambda r: r.match_score, reverse=True)

    # Optional LLM reranking (flag-gated; no-op when disabled).
    from app.services.llm import llm_service

    if llm_service.enabled:
        try:
            if state.get("type") == SearchType.jobs:
                scored = await llm_service.rerank_jobs(
                    profile, state.get("normalized", []), scored
                )
            elif state.get("type") == SearchType.candidates:
                scored = await llm_service.rerank_candidates(
                    state.get("query", ""), state.get("normalized", []), scored
                )
        except Exception as exc:
            logger.warning("LLM rerank failed, keeping deterministic order: %s", exc)

    return {
        **state,
        "results": scored,
        "status": TaskStatus.completed,
        "timeline": _log(state, "MATCH / RANK", f"Ranked {len(scored)} results"),
    }


def check_human(state: AgentState) -> AgentState:
    """HUMAN GATE: pause if a blocker was flagged (MFA/CAPTCHA/low confidence)."""
    if state.get("needs_human"):
        return {
            **state,
            "status": TaskStatus.paused,
            "timeline": _log(state, "HUMAN TAKEOVER", state.get("human_reason")),
        }
    return state

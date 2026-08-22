"""LangGraph workflow nodes for the Career Agent supervisor.

Phase 1 implements a deterministic StateGraph that mirrors the design-spec
workflow:

    REQUEST -> UNDERSTAND -> PLAN SEARCH -> RUN SEARCH -> EXTRACT
        -> NORMALIZE -> DEDUPLICATE -> MATCH/RANK -> RETURN RESULTS

Each node writes structured state; a checkpoint (Redis-backed in production)
allows durable task state and resume.
"""

from __future__ import annotations

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
    """PLAN SEARCH STRATEGY: decide sources and query terms."""
    source = "linkedin" if "linkedin" in (state.get("query") or "").lower() else "jobs_site"
    return {
        **state,
        "timeline": _log(state, "PLAN SEARCH", f"Chose source={source}, query={state.get('query')}"),
    }


async def run_search(state: AgentState) -> AgentState:
    """RUN SEARCH: invoke the browser/search adapter for the source."""
    query = state.get("query", "")
    location = state.get("location")
    task_type = state.get("type")

    # Jobs -> LinkedIn adapter (authenticated Brave session).
    if task_type == SearchType.jobs:
        from app.services.linkedin import search_linkedin_jobs

        try:
            result = await search_linkedin_jobs(query, location)
            raw = result.get("raw_results", [])
            needs_human = result.get("needs_human", False)
            human_reason = result.get("human_reason")
            detail = (
                f"LinkedIn search found {len(raw)} jobs"
                if not needs_human
                else f"LinkedIn blocked: {human_reason}"
            )
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
                "human_reason": f"LinkedIn search failed: {exc}",
                "timeline": _log(state, "RUN SEARCH", f"LinkedIn search error: {exc}"),
            }

    # Candidates -> LinkedIn People adapter (authenticated Brave session).
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

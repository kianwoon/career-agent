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

from sqlalchemy import func, select

from app.models.schemas import ActivityEvent, MatchResult, SearchType, TaskStatus

logger = logging.getLogger(__name__)

# Platform -> candidate search adapter. Add new platforms here as their
# adapters become available; unsupported entries are rejected at RUN SEARCH.
_CANDIDATE_PLATFORM_ADAPTERS: dict[str, Any] = {}


def _candidate_adapters() -> dict[str, Any]:
    """Lazy adapter registry (imports happen on first use, not at module load)."""
    if not _CANDIDATE_PLATFORM_ADAPTERS:
        from app.services.linkedin_people import search_linkedin_people

        _CANDIDATE_PLATFORM_ADAPTERS["linkedin"] = search_linkedin_people
    return _CANDIDATE_PLATFORM_ADAPTERS


async def _search_candidates_via_flow(
    source_name: str,
    queries: list[str],
    excludes: list[str] | None = None,
    location: str | None = None,
) -> dict[str, Any]:
    """Run a candidate search on a custom source via its recorded flow.

    Lets any source with an active `find_candidates` flow act as a
    sourcing platform. Resolves the source by name (case-insensitive),
    builds the boolean keyword string from the plan, and executes the
    flow through the browser-extension agent (falling back to Playwright)
    — the same machinery `_search_custom_sources` uses.
    """

    from app.db import async_session
    from app.models.orm import Source, SourceFlow
    from app.services.source_flows import (
        build_boolean_keywords,
        execute_flow,
        filter_excluded_results,
    )

    async with async_session() as db:
        source = (
            await db.execute(
                select(Source).where(func.lower(Source.name) == source_name.lower())
            )
        ).scalar_one_or_none()
        if source is None:
            return {"raw_results": [], "needs_human": True, "human_reason": f"Unknown source: {source_name}"}
        flow = (
            await db.execute(
                select(SourceFlow).where(
                    SourceFlow.source_id == source.id,
                    SourceFlow.flow_type == "find_candidates",
                    SourceFlow.status == "active",
                )
            )
        ).scalars().first()

    if flow is None:
        return {
            "raw_results": [],
            "needs_human": True,
            "human_reason": f"{source.name}: no active find_candidates flow — record one from the Sources panel",
        }

    flow_query = build_boolean_keywords(queries, excludes) or " ".join(queries)

    # Prefer the browser-extension agent (real browser, never blocked);
    # fall back to server-side Playwright.
    from app.services.agent_relay import agent_registry

    results: list[dict[str, Any]] | None = None
    if agent_registry.connected:
        try:
            data = await agent_registry.dispatch(
                "run_flow",
                {
                    "baseUrl": source.base_url,
                    "query": flow_query,
                    "steps": flow.steps,
                },
                timeout_s=180,
            )
            if isinstance(data, dict) and data.get("needs_human"):
                return {
                    "raw_results": [],
                    "needs_human": True,
                    "human_reason": f"{source.name}: {data.get('error') or 'site showing a login page'}",
                }
            results = (data.get("results") if isinstance(data, dict) else data) or []
        except Exception as exc:
            logger.warning("Agent run_flow failed for %s: %s — falling back to Playwright", source.name, exc)
            results = None

    if results is None:
        result = await execute_flow(
            base_url=source.base_url,
            steps=flow.steps,
            query=flow_query,
            storage_state_encrypted=source.session_state,
            card_selectors=flow.steps[-1] if flow.steps and flow.steps[-1].get("card") else None,
        )
        if result.get("needs_human") or not result.get("results"):
            reason = result.get("human_reason", "no results")
            return {
                "raw_results": [],
                "needs_human": bool(result.get("needs_human")),
                "human_reason": f"{source.name}: {reason}",
            }
        results = result["results"]

    results = filter_excluded_results(results or [], excludes or None)
    for r in results:
        r.setdefault("source", source.name)
        r.setdefault("title", "")
    return {
        "raw_results": results,
        "needs_human": False,
        "human_reason": None,
        "plan_detail": f"{source.name} flow: {len(results)} results",
    }


async def _flow_platforms() -> set[str]:
    """Names of enabled sources that have an active find_candidates flow.

    These are valid candidate-search platforms in addition to the
    built-in adapter registry.
    """

    from app.db import async_session
    from app.models.orm import Source, SourceFlow

    async with async_session() as db:
        rows = (
            await db.execute(
                select(Source.name)
                .join(SourceFlow, SourceFlow.source_id == Source.id)
                .where(
                    Source.enabled.is_(True),
                    SourceFlow.flow_type == "find_candidates",
                    SourceFlow.status == "active",
                )
            )
        ).scalars().all()
    return {r.lower() for r in rows}


class AgentState(TypedDict, total=False):
    task_id: str
    type: SearchType
    query: str
    location: str | None
    status: TaskStatus
    profile: dict[str, Any]
    # Sourcing plan from the external system (queries, exclude, platform,
    # salary, employment_type). Present when POST /search/candidates was
    # called with the structured panel fields rather than a plain query.
    plan: dict[str, Any]
    raw_results: list[dict[str, Any]]
    normalized: list[dict[str, Any]]
    results: list[MatchResult]
    timeline: list[ActivityEvent]
    error: str | None
    needs_human: bool
    human_reason: str | None
    # Sourcing-plan execution detail: queries run, relaxed variants, filters.
    plan_detail: str | None
    source_ids: list[str] | None
    source_issues: list[dict[str, str]]


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


async def _noop_search(reason: str) -> dict[str, Any]:
    """Placeholder result for a disabled built-in source (skipped, not an error)."""
    return {"raw_results": [], "needs_human": False, "human_reason": reason}


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
) -> tuple[list[dict[str, Any]], list[str], list[str], list[dict[str, str]]]:
    """Run templatized flows for all selected/enabled custom sources.

    Returns (raw_results, ok_labels, failed_labels).
    """

    from app.db import async_session
    from app.models.orm import Source, SourceFlow
    from app.services.source_flows import execute_flow

    source_ids = state.get("source_ids")
    async with async_session() as db:
        stmt = select(Source).where(Source.enabled.is_(True))
        if source_ids:
            stmt = stmt.where(Source.id.in_(source_ids))
        sources = (await db.execute(stmt)).scalars().all()
        flows = (
            await db.execute(select(SourceFlow).where(SourceFlow.status == "active"))
        ).scalars().all()
    flows_by_source: dict[str, SourceFlow] = {f.source_id: f for f in flows}

    query = state.get("query", "")
    # Sourcing-plan aware keyword mapping: custom-source search boxes
    # (seek's 'Keywords in CV / Profile' etc.) accept boolean syntax, so
    # merge plan queries[] + exclude[] into ONE string — e.g.
    # '"software engineer" OR developer NOT ("recruiter" OR "talent acquisition")'.
    # Without a plan this degrades to the plain query (unchanged behavior).
    from app.services.source_flows import build_boolean_keywords, filter_excluded_results

    plan = state.get("plan") or {}
    plan_queries = [q.strip() for q in (plan.get("queries") or []) if q and q.strip()] or (
        [query.strip()] if query.strip() else []
    )
    plan_excludes = [e.strip() for e in (plan.get("exclude") or []) if e and e.strip()]
    flow_query = build_boolean_keywords(plan_queries, plan_excludes) or query

    raw: list[dict[str, Any]] = []
    ok: list[str] = []
    failed: list[str] = []
    issues: list[dict[str, str]] = []

    async def _run_one(source: Source) -> None:
        flow = flows_by_source.get(source.id)
        flow_type = "find_jobs" if state.get("type") == SearchType.jobs else "find_candidates"
        if flow is None or flow.flow_type != flow_type:
            failed.append(f"{source.name}: no {flow_type} flow")
            issues.append({"source": source.name, "reason": f"no {flow_type} flow recorded"})
            return

        # Prefer the browser-extension agent (runs in the user's real browser
        # — sites never block it). Fall back to server-side Playwright.
        from app.services.agent_relay import agent_registry

        results: list[dict[str, Any]] | None = None
        reason: str | None = None
        if agent_registry.connected:
            try:
                data = await agent_registry.dispatch(
                    "run_flow",
                    {
                        "baseUrl": source.base_url,
                        "query": flow_query,
                        "steps": flow.steps,
                    },
                    timeout_s=180,
                )
                results = data.get("results") if isinstance(data, dict) else data
                if results is None:
                    results = []
                # Extension hit a login wall mid-flow — treat as session
                # expired so the search pauses for human re-login.
                if isinstance(data, dict) and data.get("needs_human"):
                    wall_reason = data.get("error") or "site showing a login page"
                    issues.append({"source": source.name, "reason": f"session expired: {wall_reason}"})
                    failed.append(f"{source.name}: session expired ({wall_reason})")
                    return
                results = filter_excluded_results(results, plan_excludes or None)
                for r in results:
                    r.setdefault("source", source.name)
                    r.setdefault("title", "")
                    raw.append(r)
                ok.append(f"{source.name}: {len(results)} (agent)")
                return
            except Exception as exc:
                logger.warning("Agent run_flow failed for %s: %s — falling back to Playwright", source.name, exc)
                reason = str(exc)
                results = None

        result = await execute_flow(
            base_url=source.base_url,
            steps=flow.steps,
            query=flow_query,
            storage_state_encrypted=source.session_state,
            card_selectors=flow.steps[-1] if flow.steps and flow.steps[-1].get("card") else None,
        )
        results = result.get("results", [])
        if result.get("needs_human") or not results:
            _reason = result.get("human_reason", "no results")
            if reason:
                _reason = f"{_reason} (agent: {reason})"
            failed.append(f"{source.name}: {_reason}")
            issues.append({"source": source.name, "reason": _reason})
            # If the session expired, flag the flow so setup shows "Re-login".
            if "expired" in _reason.lower() or "login" in _reason.lower():
                async with async_session() as db2:
                    db_flow = await db2.get(SourceFlow, flow.id)
                    if db_flow:
                        db_flow.status = "broken"
                        await db2.commit()
            return
        results = filter_excluded_results(results, plan_excludes or None)
        for r in results:
            r.setdefault("source", source.name)
            r.setdefault("title", "")
            raw.append(r)
        ok.append(f"{source.name}: {len(results)}")

    await asyncio.gather(*(_run_one(s) for s in sources))
    return raw, ok, failed, issues


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
    custom_raw, custom_ok, custom_failed, custom_issues = await _search_custom_sources(state)
    # Stash structured per-source issues so the API can surface them.
    state = {**state, "source_issues": custom_issues}
    # A source whose session expired is recorded as a per-source issue —
    # the user can re-login from Sources; it must NOT hard-pause the whole
    # task, because built-in sources (LinkedIn etc.) still deliver results
    # independently. The expired source is simply skipped this round.

    # -------------------------------------------------------
    # Jobs -> run enabled built-in adapters in parallel
    # (LinkedIn + MyCareersFuture + FastJobs; each is a seeded Source row,
    # so the user can disable any of them from the Sources UI)
    # -------------------------------------------------------
    if task_type == SearchType.jobs:
        from sqlalchemy import select

        from app.db import async_session
        from app.models.orm import Source

        async with async_session() as db:
            disabled_domains = set(
                (
                    await db.execute(
                        select(Source.domain).where(Source.enabled.is_(False))
                    )
                ).scalars().all()
            )

        from app.services.fastjobs import search_fastjobs_jobs
        from app.services.linkedin import search_linkedin_jobs
        from app.services.mycareersfuture import search_mycareersfuture_jobs

        # A built-in runs unless its Source row explicitly disables it.
        # Missing row = enabled (seeding may not have run yet, e.g. fresh
        # test DB), so an empty sources table never silently kills search.
        li_on = "linkedin.com" not in disabled_domains
        mcf_on = "mycareersfuture.gov.sg" not in disabled_domains
        fj_on = "fastjobs.io" not in disabled_domains

        li_result, mcf_result, fj_result = await asyncio.gather(
            _safe_search(search_linkedin_jobs, query, location) if li_on else _noop_search("disabled"),
            _safe_search(search_mycareersfuture_jobs, query, location) if mcf_on else _noop_search("disabled"),
            _safe_search(search_fastjobs_jobs, query, location) if fj_on else _noop_search("disabled"),
        )

        raw: list[dict[str, Any]] = []
        timeline_events: list[str] = []

        li_raw = li_result.get("raw_results", [])
        li_ok = not li_result.get("needs_human", False) and li_raw
        if li_ok:
            raw.extend(li_raw)
            timeline_events.append(f"LinkedIn: {len(li_raw)} jobs")
        elif not li_on:
            timeline_events.append("LinkedIn: disabled")
        else:
            timeline_events.append(f"LinkedIn blocked: {li_result.get('human_reason', 'unknown')}")

        mcf_raw = mcf_result.get("raw_results", [])
        mcf_ok = not mcf_result.get("needs_human", False) and mcf_raw
        if mcf_ok:
            raw.extend(mcf_raw)
            timeline_events.append(f"MyCareersFuture: {len(mcf_raw)} jobs")
        elif not mcf_on:
            timeline_events.append("MyCareersFuture: disabled")
        else:
            timeline_events.append(f"MyCareersFuture: {mcf_result.get('human_reason', 'no results')}")

        fj_raw = fj_result.get("raw_results", [])
        fj_ok = not fj_result.get("needs_human", False) and fj_raw
        if fj_ok:
            raw.extend(fj_raw)
            timeline_events.append(f"FastJobs: {len(fj_raw)} jobs")
        elif not fj_on:
            timeline_events.append("FastJobs: disabled")
        else:
            timeline_events.append(f"FastJobs: {fj_result.get('human_reason', 'no results')}")

        # Only flag a human bottleneck if ALL *enabled* sources are blocked/failed.
        all_blocked = (
            not (li_ok or not li_on)
            and not (mcf_ok or not mcf_on)
            and not (fj_ok or not fj_on)
            and not custom_raw
        )
        if all_blocked:
            reasons = []
            if li_on and li_result.get("human_reason"):
                reasons.append(li_result["human_reason"])
            if mcf_on and mcf_result.get("human_reason"):
                reasons.append(mcf_result["human_reason"])
            if fj_on and fj_result.get("human_reason"):
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
    # Candidates -> per-platform adapters (authenticated Brave session).
    # Accepts a structured sourcing plan (plan['platforms'] or legacy
    # plan['platform'], plan['queries'], plan['exclude'], location
    # post-filter) or falls back to the legacy single query. Results from
    # every platform are merged; dedup happens downstream.
    # -------------------------------------------------------
    if task_type == SearchType.candidates:
        plan = state.get("plan") or {}
        queries = list(plan.get("queries") or []) or ([query] if query else [])
        excludes = list(plan.get("exclude") or [])
        platforms = [str(p).lower() for p in (plan.get("platforms") or [])]
        if not platforms:
            legacy = plan.get("platform")
            platforms = [str(legacy).lower()] if legacy else ["linkedin"]

        raw: list[dict[str, Any]] = []
        needs_human = False
        human_reason: str | None = None
        plan_details: list[str] = []
        # Valid platforms = built-in adapters + any enabled source with an
        # active find_candidates flow (Option B: sources become platforms).
        flow_platforms = await _flow_platforms()

        def _resolve(p: str) -> Any:
            return _candidate_adapters().get(p) or (
                _search_candidates_via_flow if p in flow_platforms else None
            )

        unsupported = [p for p in platforms if _resolve(p) is None]
        if unsupported:
            supported = sorted(set(_candidate_adapters()) | flow_platforms)
            human_reason = (
                f"Unsupported platform(s): {', '.join(unsupported)}; "
                f"supported: {supported}"
            )
            needs_human = True
        try:
            for platform in platforms:
                adapter = _resolve(platform)
                if adapter is None:
                    continue
                if not queries:
                    plan_details.append(f"{platform}: no queries")
                    continue
                # Flow-based adapters are generic — one function serving every
                # custom source — so they take the platform name to resolve
                # which source's flow to run. Built-in adapters are
                # platform-specific and don't.
                if adapter is _search_candidates_via_flow:
                    result = await adapter(
                        source_name=platform,
                        queries=queries,
                        excludes=excludes or None,
                        location=location,
                    )
                else:
                    result = await adapter(
                        queries=queries,
                        excludes=excludes or None,
                        location=location,
                    )
                p_raw = result.get("raw_results", [])
                raw.extend(p_raw)
                if result.get("needs_human"):
                    needs_human = True
                    if result.get("human_reason"):
                        human_reason = result["human_reason"]
                plan_details.append(
                    result.get("plan_detail") or f"{platform}: {len(p_raw)} results"
                )
            plan_detail = " | ".join(plan_details) if plan_details else "No queries"
            detail = (
                f"Plan search ({', '.join(platforms)}) — {plan_detail}; {len(raw)} candidates"
                if not needs_human
                else f"Blocked: {human_reason}"
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
                "plan_detail": plan_detail,
                "timeline": _log(state, "RUN SEARCH", detail),
            }
        except Exception as exc:
            return {
                **state,
                "raw_results": [],
                "needs_human": True,
                "human_reason": f"Candidate search failed: {exc}",
                "timeline": _log(state, "RUN SEARCH", f"Candidate search error: {exc}"),
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
    # Sourcing-plan context (salary, employment type) becomes part of the
    # ranking reference — they are not searchable on LinkedIn people search
    # but still describe the role we are matching candidates against.
    plan = state.get("plan") or {}
    plan_context = " ".join(
        str(plan.get(k)) for k in ("salary", "employment_type") if plan.get(k)
    )
    scored: list[MatchResult] = []
    for item in state.get("normalized", []):
        if state.get("type") == SearchType.candidates:
            # For candidate search, the reference is the search query (the
            # candidate criteria), not the searcher's career profile.
            criteria_text = state.get("query", "") + (f" {plan_context}" if plan_context else "")
            job_ref = {
                "description": criteria_text,
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

    # Cap at the top 10 after ranking — multi-platform merges can exceed
    # the actionable shortlist size.
    MAX_TOP_RESULTS = 10
    scored = scored[:MAX_TOP_RESULTS]

    return {
        **state,
        "results": scored,
        "status": TaskStatus.completed,
        "timeline": _log(
            state,
            "MATCH / RANK",
            f"Ranked {len(state.get('normalized', []))} results; returning top {len(scored)}",
        ),
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

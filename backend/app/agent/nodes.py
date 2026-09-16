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
import os
import re
from time import monotonic
from typing import Any, TypedDict

from sqlalchemy import func, select

from app.models.schemas import ActivityEvent, MatchResult, SearchType, TaskStatus

logger = logging.getLogger(__name__)

# Platform -> candidate search adapter. Add new platforms here as their
# adapters become available; unsupported entries are rejected at RUN SEARCH.
_CANDIDATE_PLATFORM_ADAPTERS: dict[str, Any] = {}

# Global wall-clock budget for the candidates branch of run_search. The task
# watchdog is TASK_HARD_TIMEOUT_S = 720s; downstream stages (match/rank,
# persist) need headroom, so the sequential pipeline (flow dispatch legs x
# queries + per-platform loops + LinkedIn multi-pass) must finish within this
# budget. On exhaustion the search returns results gathered so far as a
# PARTIAL result — the task completes; it never watchdog-fails.
SEARCH_BUDGET_S = 480.0
# Minimum remaining budget below which starting another dispatch leg/platform
# is pointless (a leg needs real time to navigate + extract).
_SEARCH_MIN_REMAINING_S = 15.0
# Per-dispatch timeout cap (was a flat 240s per leg).
_SEARCH_LEG_TIMEOUT_CAP_S = 240.0


def _normalize_platform(s: str) -> str:
    """Lowercase and drop non-alphanumerics (``JobStreet - Candidate`` ->
    ``jobstreetcandidate``)."""
    return "".join(ch for ch in s.lower() if ch.isalnum())


def resolve_candidate_platform(requested: str, known: set[str]) -> str | None:
    """Resolve a requested platform name to a canonical name in ``known``.

    Resolution order — exact match always wins so unrelated sites are never
    silently conflated:

    1. exact case-insensitive, whitespace-trimmed match;
    2. normalized match ignoring non-alphanumerics
       (``JobStreet - Candidate`` == ``jobstreet - candidate``, but
       ``jobstreet`` != ``jobstreet - candidate``);
    3. singular/plural tolerance on the normalized form (trailing ``s``
       add/strip), so ``FastJobs`` resolves to a known ``fastjob`` (and
       ``fastjob`` to a known ``fastjobs``) — only when unambiguous.

    Returns the canonical name from ``known``, or ``None`` if nothing matches.
    """
    req = (requested or "").strip().lower()
    if not req:
        return None
    # 1) exact (case-insensitive) match wins.
    if req in known:
        return req
    norm = _normalize_platform(req)
    if not norm:
        return None
    norm_known = {_normalize_platform(k): k for k in known if _normalize_platform(k)}
    # 2) normalized match.
    if norm in norm_known:
        return norm_known[norm]
    # 3) singular/plural tolerance — trailing "s" only, and only when a
    # single candidate matches (never guess between two).
    candidates: set[str] = set()
    if norm.endswith("s") and norm[:-1] in norm_known:
        candidates.add(norm_known[norm[:-1]])
    if norm + "s" in norm_known:
        candidates.add(norm_known[norm + "s"])
    if len(candidates) == 1:
        return candidates.pop()
    return None


def _candidate_adapters() -> dict[str, Any]:
    """Lazy adapter registry (imports happen on first use, not at module load)."""
    if not _CANDIDATE_PLATFORM_ADAPTERS:
        from app.services.linkedin_people import search_linkedin_people

        _CANDIDATE_PLATFORM_ADAPTERS["linkedin"] = search_linkedin_people
    return _CANDIDATE_PLATFORM_ADAPTERS


# A real person name is short; anything longer is a textContent blob from a
# drifted card selector (block elements concatenate without separators:
# "Tang Yee HennSenior QC Technician (Deputy Shift Lead) at ...").
_NAME_DISPLAY_MAX = 120


def _normalize_flow_candidate(
    r: dict[str, Any], source_name: str, idx: int, base_url: str | None = None
) -> dict[str, Any] | None:
    """Map one raw flow-extracted row to the canonical candidate schema.

    Extension/Playwright extraction returns {title, company, location,
    summary, url, raw_text}. score_candidate() reads name/headline/
    source_url — without this mapping every row renders as "Unknown".

    SEEK (and similar) candidate cards often have NO links and their name
    lives in the card text, so:
      - name     = title, else first non-empty line of raw_text
      - headline = the role line ("Python Developer at X, May 2025 - ...")
      - source_url = the card's href when present, else the source's
        base_url (an openable platform page — candidates have no public
        deep links there; the user opens it and searches the name).
    """
    raw_text = str(r.get("raw_text") or "")
    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
    raw_name = str(r.get("title") or "").strip() or (lines[0] if lines else "")
    # Extractors can fall back to a 1-2 char node (avatar initial badge,
    # truncated text). If the title is that short but raw_text has a real
    # line, prefer the first substantial line as the name.
    if len(raw_name) < 3:
        longer = next((ln for ln in lines if len(ln) >= 3), "")
        if longer:
            raw_name = longer
    # SEEK cards glue role text to the name with no separator when the
    # recorded card selector drifts ("...HennSenior QC Technician…" /
    # "...ChowQC Manager II…" / "...TayQA Supervisor…"). Split NAME from
    # ROLE with a name-aware matcher instead of the first camel bump:
    #   - "Xxx ...Yyy<ROLE>" where <ROLE> starts with a known job-title
    #     keyword (QC/QA/Technician/Manager/Supervisor/Engineer/…) glued to
    #     the surname. The name is the short head before it.
    #   - Fallback: first inner camel split (…HennSenior…), name = head.
    name = raw_name
    tail = ""
    # Seniority-prefix roles ("Senior QC Technician…") have the cleanest
    # anchor: split right at the seniority word glued to the surname.
    m = re.search(
        r"^(?P<name>.{2,60}?)"
        r"(?P<role>(?:Senior|Sr\.|Junior|Jr\.|Lead|Chief|Head|Deputy|Assistant|Associate)\s+.+)$",
        raw_name,
        flags=re.IGNORECASE | re.DOTALL,
    )
    glue = m and re.search(r"(?<=[A-Za-z…)-])(?=[A-Z])", raw_name[: m.start("role") + 1])
    if m and glue:
        name, tail = raw_name[: m.start("role")].strip(), raw_name[m.start("role"):].strip()
    else:
        # Bare roles with no seniority prefix ("…ChowQC Manager II…",
        # "…TayQA Supervisor…", "…GUNASEKARANLaboratory Technician…"):
        # find a known role keyword glued to the surname. Case-insensitive
        # — all-caps surnames glue to a capitalized role word.
        m2 = re.search(
            r"(?<=[A-Za-z)])(?=(?:QC|QA|Quality|Laboratory|Lab|Production|Process|Validation|"
            r"Technician|Technologist|Manager|Supervisor|Engineer|Chemist|Specialist|Analyst|"
            r"Director|Executive|Officer|Coordinator|Assistant)\b)",
            raw_name,
            flags=re.IGNORECASE,
        )
        if m2 and len(raw_name) > 20:
            name, tail = raw_name[: m2.start()].strip(), raw_name[m2.start():].strip()
        else:
            m3 = re.search(r"(?<=[a-z])(?=[A-Z][a-z])", raw_name)
            if m3 and (len(raw_name) > _NAME_DISPLAY_MAX or not str(r.get("title") or "").strip()):
                name, tail = raw_name[: m3.start()], raw_name[m3.start():]
    # A name with digits / @ / 'monthly' / 'pool' / SGD is card-body junk,
    # not a person — drop the row instead of ranking it. (Role words like
    # "QC Manager" are fine — only UI chrome triggers the drop.)
    if re.search(r"[\d@]|monthly|add to pool|^updated|send (job|message)|access profile|\bSGD\b", name, re.IGNORECASE):
        return None
    # Page-chrome junk guard: when a recorded card selector drifts (seek
    # rotates obfuscated classes), extraction can return the whole app root
    # and the "name" becomes the page's first text line ("Skip to
    # content"). Drop obvious chrome instead of ranking it as a candidate.
    chrome_markers = (
        "skip to content",
        "sign in",
        "log in",
        "accept all",
        "privacy policy",
        "toggle menu",
        "your saved searches",
        "saved searches will appear",
        "filter by",
        "matching candidates",
        "hide names",
        "sorted by",
        "previous",
        "next",
    )
    lowered = name.lower()
    if (
        lowered.rstrip("…") in chrome_markers
        or any(m in lowered for m in chrome_markers[6:])
        or (not str(r.get("title") or "").strip() and len(raw_text) > 400)
    ):
        return None
    headline = (
        str(r.get("summary") or "").strip()
        or str(r.get("company") or "").strip()
        or (tail.strip()[:500] if tail else "")
        or (lines[1] if len(lines) > 1 else None)
    )
    source_url = str(r.get("url") or "").strip()
    # Search/listing wrapper hrefs (SEEK /talentsearch/keyword?...searchQuery=)
    # are not candidate deep links — treat them as absent so the fallback
    # below synthesizes the openable per-candidate profile search link.
    if re.search(r"/keyword\b|searchQuery=|searchId=", source_url):
        source_url = ""
    if not source_url and base_url:
        # SEEK talent search: cards carry no hrefs, and the bare base_url is
        # a blank results page. The openable deep link is a profile SEARCH
        # for the candidate's name (validated live by the user):
        #   /talentsearch/search/profiles?...&uncoupledFreeText=<name>
        if "seek.com" in base_url:
            from urllib.parse import quote
            source_url = (
                "https://sg.employer.seek.com/talentsearch/search/profiles"
                "?locationList=24553&nation=24553&pageNumber=1"
                "&salaryNation=24553&salaryType=MONTHLY&searchId=112aa"
                "&searchType=new_search&sortBy=relevance"
                f"&uncoupledFreeText={quote(name or '')}"
                "&willingToRelocate=false"
            )
        else:
            # Generic platform landing page — the user opens it and
            # searches the candidate name there.
            source_url = base_url
    # Belt + suspenders: DB name is varchar(255). The split above handles
    # concatenated blobs, but a legitimately long single-line title (no
    # camel split point) must still never kill the whole task's commit.
    name = name.strip()[:255] or f"{source_name} candidate {idx + 1}"
    headline = (headline or "").strip()[:500] or None
    location = str(r.get("location") or "").strip()[:255] or None
    return {
        "id": r.get("id") or f"flow-{abs(hash((name, headline, idx)))}",
        "name": name,
        "headline": headline,
        "location": location,
        "summary": str(r.get("summary") or "")[:500],
        "skills": [],
        "experience": raw_text[:800],
        "source": source_name,
        "source_url": source_url or None,
    }


async def _search_candidates_via_flow(
    source_name: str,
    queries: list[str],
    excludes: list[str] | None = None,
    location: str | None = None,
    deadline: float | None = None,
    use_agent: bool = True,
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
        KEYWORD_LIMIT_FLOW,
        build_boolean_keywords_async,
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

    flow_queries: list[str] = []
    for q in queries:
        fq = await build_boolean_keywords_async([q], excludes, limit=KEYWORD_LIMIT_FLOW)
        flow_queries.append(fq or q)

    # Prefer the browser-extension agent (real browser, never blocked);
    # fall back to server-side Playwright. Each plan query runs as its OWN
    # navigation (parity with the LinkedIn adapter) so wide OR-merged
    # booleans can't starve any intent; results merge across legs.
    from app.services.agent_relay import agent_registry

    results: list[dict[str, Any]] = []
    if use_agent and agent_registry.connected:
        leg_counts: list[str] = []
        needs_human_leg: str | None = None
        for q in flow_queries:
            # Global budget: cap each leg's timeout to the remaining budget
            # and stop before starting a leg that cannot finish in time.
            if deadline is not None:
                remaining = deadline - monotonic()
                if remaining < _SEARCH_MIN_REMAINING_S:
                    leg_counts.append("skipped: time budget exhausted")
                    continue
                leg_timeout = min(_SEARCH_LEG_TIMEOUT_CAP_S, remaining)
            else:
                leg_timeout = _SEARCH_LEG_TIMEOUT_CAP_S
            try:
                data = await agent_registry.dispatch(
                    "run_flow",
                    {
                        "baseUrl": source.base_url,
                        "query": q,
                        "steps": flow.steps,
                    },
                    timeout_s=leg_timeout,
                )
            except Exception as exc:
                # Do NOT fall back to server-side Playwright for flow
                # sources: the stored cookie blob is stale by definition
                # (the extension browser is the live session) and seek's
                # rotated markup makes the fallback useless. Per-leg soft
                # miss: record and continue with remaining legs.
                logger.warning("Agent run_flow failed for %s: %s", source.name, exc)
                leg_counts.append(f"{q[:8]}…: dispatch failed")
                continue
            if isinstance(data, dict) and data.get("needs_human"):
                needs_human_leg = f"{source.name}: {data.get('error') or 'site showing a login page'}"
                leg_counts.append(f"{q[:8]}…: needs human")
                continue
            leg_results = (data.get("results") if isinstance(data, dict) else data) or []
            results.extend(leg_results)
            leg_counts.append(f"{q[:8]}…: {len(leg_results)}")
        detail = "; ".join(leg_counts)
        if needs_human_leg and not results:
            return {
                "raw_results": [],
                "needs_human": True,
                "human_reason": needs_human_leg,
            }
        detail = f"{source.name} - candidate: {detail}"
        plan_detail = detail if len(detail) <= 200 else detail[:197] + "…"
        if not results and all(c.endswith(("failed", "human")) for c in leg_counts) and leg_counts:
            return {"raw_results": [], "needs_human": False, "human_reason": None, "plan_detail": plan_detail}
        agent_results: list[dict[str, Any]] | None = results or None
    else:
        agent_results = None
        plan_detail = None

    if agent_results is None:
        flow_query = " ".join(flow_queries)
        result = await execute_flow(
            base_url=source.base_url,
            steps=flow.steps,
            query=flow_query,
            storage_state_encrypted=source.session_state,
            card_selectors=flow.steps[-1] if flow.steps and flow.steps[-1].get("card") else None,
            source_id=source.id,
        )
        if result.get("needs_human") or not result.get("results"):
            reason = result.get("human_reason", "no results")
            # Genuine session expiry — flag the flow so setup shows "Re-login"
            # (parity with _run_one's broken-flow handling).
            if result.get("needs_human") and (
                "expired" in reason.lower() or "login" in reason.lower()
            ):
                async with async_session() as db2:
                    db_flow = await db2.get(SourceFlow, flow.id)
                    if db_flow:
                        db_flow.status = "broken"
                        await db2.commit()
            return {
                "raw_results": [],
                "needs_human": bool(result.get("needs_human")),
                "human_reason": f"{source.name}: {reason}",
            }
        results = result["results"]

    results = filter_excluded_results(agent_results or [], excludes or None)
    results = [
        normalized
        for i, r in enumerate(results)
        if (normalized := _normalize_flow_candidate(r, source.name, i, source.base_url))
    ]
    final_detail = plan_detail if plan_detail else f"{source.name} flow: {len(results)} results"
    return {
        "raw_results": results,
        "needs_human": False,
        "human_reason": None,
        "plan_detail": final_detail,
    }


_CACHE_TTL_S = 30.0
_flow_platforms_cache: tuple[float, set[str]] | None = None
_candidate_source_platforms_cache: tuple[float, set[str]] | None = None


async def _flow_platforms() -> set[str]:
    """Names of enabled sources that have an active find_candidates flow.

    These are valid candidate-search platforms in addition to the
    built-in adapter registry. Cached briefly so the synchronous POST
    /search/candidates path never pays repeated DB round-trips per call.
    """

    global _flow_platforms_cache
    import time

    if _flow_platforms_cache and time.monotonic() - _flow_platforms_cache[0] < _CACHE_TTL_S:
        return _flow_platforms_cache[1]

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
    platforms = {r.lower() for r in rows}
    _flow_platforms_cache = (time.monotonic(), platforms)
    return platforms


async def _candidate_source_platforms() -> set[str]:
    """Names of enabled sources with a find_candidates flow of ANY status.

    Unlike _flow_platforms (active only), this includes sources whose flow
    is broken/paused — they remain valid candidate-search platforms so the
    attempt happens and the failure is surfaced as a per-source issue
    ("no active find_candidates flow") instead of being silently dropped.
    Cached briefly; derived from the same rows as the full lookup so no
    extra query is needed when both are called back-to-back by the POST
    path.
    """

    global _candidate_source_platforms_cache
    import time

    if _candidate_source_platforms_cache and (
        time.monotonic() - _candidate_source_platforms_cache[0] < _CACHE_TTL_S
    ):
        return _candidate_source_platforms_cache[1]

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
                )
            )
        ).scalars().all()
    platforms = {r.lower() for r in rows}
    _candidate_source_platforms_cache = (time.monotonic(), platforms)
    return platforms


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
    # User intent: may searches use the connected browser-extension agent?
    # Defaults True (backward-compatible). False forces server-side adapters.
    use_agent: bool


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
    **kwargs: Any,
) -> dict[str, Any]:
    """Call a search adapter and normalize failures into the adapter contract.

    Adapters may raise; ``run_search`` treats an exception the same as a
    blocked source (empty results + human_reason) so one failing source never
    takes down the other.
    """
    try:
        result = await fn(query, location, **kwargs)
        return result or {}
    except Exception as exc:
        return {
            "raw_results": [],
            "needs_human": True,
            "human_reason": f"{getattr(fn, '__name__', 'adapter')} failed: {exc}",
        }


async def _persist_agent_finding(source: Any, reason: str | None) -> None:
    """Best-effort write-back of an extension-reported wall onto the Source profile.

    Maps Cloudflare/login keywords in the extension's error text to a finding
    and folds it into source.profile so later method choices see the wall.
    Never raises — persistence is advisory only.
    """
    if not reason:
        return
    from app.services.site_profiles import apply_finding

    low = reason.lower()
    if "cloudflare" in low:
        finding = "cloudflare"
    elif any(m in low for m in ("singpass", "corppass", "sso", "openid")):
        finding = "sso"
    elif "login" in low or "sign in" in low or "sign-in" in low:
        finding = "login_wall"
    else:
        return
    try:
        from app.db import async_session
        from app.models.orm import Source

        async with async_session() as db:
            row = await db.get(Source, getattr(source, "id", None))
            if row is not None:
                row.profile = apply_finding(row.profile, finding)
                await db.commit()
    except Exception as exc:
        logger.warning("Could not persist %s finding for source %s: %s", finding, getattr(source, "name", "?"), exc)


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
    from app.services.source_flows import build_boolean_keywords_async, filter_excluded_results

    plan = state.get("plan") or {}
    plan_queries = [q.strip() for q in (plan.get("queries") or []) if q and q.strip()] or (
        [query.strip()] if query.strip() else []
    )
    plan_excludes = [e.strip() for e in (plan.get("exclude") or []) if e and e.strip()]
    flow_query = await build_boolean_keywords_async(plan_queries, plan_excludes) or query

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
        if state.get("use_agent", True) and agent_registry.connected:
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
                    await _persist_agent_finding(source, wall_reason)
                    issues.append({"source": source.name, "reason": f"session expired: {wall_reason}"})
                    failed.append(f"{source.name}: session expired ({wall_reason})")
                    return
                results = filter_excluded_results(results, plan_excludes or None)
                results = [
                    normalized
                    for i, r in enumerate(results)
                    if (
                        normalized := _normalize_flow_candidate(
                            r, source.name, i, source.base_url
                        )
                    )
                ]
                raw.extend(results)
                ok.append(f"{source.name}: {len(results)} (agent)")
                return
            except Exception as exc:
                logger.warning("Agent run_flow failed for %s: %s — falling back to Playwright", source.name, exc)
                reason = str(exc)
                results = None

        # Cloudflare-protected sites (FastJobs) serve a JS challenge that
        # headless server-side Playwright cannot pass — attempting execute_flow
        # just burns ~45s and returns a bogus "0 results". The extension runs
        # in the user's real browser, so when IT was unavailable/errored
        # there's no viable fallback: report the missing extension instead.
        # The per-source profile (probed/learned) overrides the global registry
        # so a site that turned out to be CF-protected is caught here too.
        from app.services.site_profiles import profile_for_source

        _prof = profile_for_source(
            source.domain or source.base_url, getattr(source, "profile", None)
        )
        if _prof and _prof.cloudflare_protected:
            failed.append(
                f"{source.name}: Cloudflare-protected site requires the browser "
                "extension (agent not connected)"
            )
            issues.append(
                {
                    "source": source.name,
                    "reason": "Cloudflare-protected site requires the browser extension",
                }
            )
            return

        result = await execute_flow(
            base_url=source.base_url,
            steps=flow.steps,
            query=flow_query,
            storage_state_encrypted=source.session_state,
            card_selectors=flow.steps[-1] if flow.steps and flow.steps[-1].get("card") else None,
            source_id=source.id,
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
        results = [
            normalized
            for i, r in enumerate(results)
            if (
                normalized := _normalize_flow_candidate(
                    r, source.name, i, source.base_url
                )
            )
        ]
        raw.extend(results)
        ok.append(f"{source.name}: {len(results)}")

    await asyncio.gather(*(_run_one(s) for s in sources))
    return raw, ok, failed, issues


async def _no_browser_session_available(use_agent: bool = True) -> bool:
    """Preflight: would any candidate adapter have a browser to work with?

    Fast-fail check for candidate searches: when there is no connected
    extension agent, no reachable Brave CDP, and no stored browser session,
    every adapter can only end in "No authenticated browser session" — so
    the caller can complete the task in seconds instead of grinding through
    a multi-minute pipeline whose per-source timeouts sum up.

    `use_agent=False` (user paused the browser agent) means a connected
    extension does NOT count as an available browser — the search will run
    server-side, so the CDP/session checks alone decide.
    """
    import httpx

    from app.services.agent_relay import agent_registry
    from app.services.linkedin import BRAVE_CDP_URL

    if use_agent and agent_registry.connected:
        return False
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.get(f"{BRAVE_CDP_URL}/json/version")
        return False  # CDP reachable — a browser session exists
    except Exception:
        pass
    try:
        from sqlalchemy import select

        from app.db import async_session
        from app.models.orm import BrowserSession

        async with async_session() as db:
            row = (
                await db.execute(
                    select(BrowserSession.id)
                    .where(BrowserSession.session_state.isnot(None))
                    .limit(1)
                )
            ).scalar_one_or_none()
        if row is not None:
            return False  # stored session exists — let the adapter try it
    except Exception:
        pass
    return True


async def run_search(state: AgentState) -> AgentState:
    """RUN SEARCH: invoke all job-search adapters and merge the results.

    Job searches hit LinkedIn (authenticated browser), MyCareersFuture
    (public API), and FastJobs (browser) in parallel. Candidate searches
    delegate to the LinkedIn People adapter only.
    """
    query = state.get("query", "")
    location = state.get("location")
    task_type = state.get("type")
    use_agent = state.get("use_agent", True)

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
            _safe_search(search_linkedin_jobs, query, location, use_agent=use_agent) if li_on else _noop_search("disabled"),
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
        # Preflight fail-fast: with no extension agent, no reachable CDP and
        # no stored session, every adapter can only report "no authenticated
        # browser session". Complete immediately with an actionable message
        # instead of burning the multi-minute pipeline (TASK_HARD_TIMEOUT_S
        # remains the backstop for genuinely slow-but-working runs).
        if await _no_browser_session_available(use_agent):
            return {
                **state,
                "raw_results": [],
                "needs_human": False,
                "human_reason": None,
                "source_issues": [
                    {
                        "source": "preflight",
                        "reason": (
                            "No browser session available: connect Brave with "
                            "--remote-debugging-port=9222 (CDP), start the "
                            "browser extension, or capture a session from the "
                            "Sources panel, then retry."
                        ),
                    }
                ],
                "plan_detail": "Preflight: no browser session (no CDP, no extension, no stored session) — search not attempted",
                "timeline": _log(
                    state,
                    "RUN SEARCH",
                    "Preflight: no browser session available — skipped candidate search (fast-fail)",
                ),
            }
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
        # Per-platform fatal errors (e.g. BrowserError "extension went
        # offline mid-search" raised by an adapter). Recorded so the run can
        # still return partial results from earlier legs instead of dropping
        # everything via the outer except.
        offline_issues: list[dict[str, str]] = []
        # Global time budget: the task watchdog kills the whole task at
        # TASK_HARD_TIMEOUT_S (720s); match/rank + persist still need to run
        # after this node. Give the sequential candidate pipeline
        # (flow legs x queries + per-platform multi-pass) a hard deadline so
        # worst-case wall time stays provably under the watchdog. On
        # exhaustion the search returns partial results instead of failing.
        budget_s = float(os.getenv("SEARCH_BUDGET_S", "") or SEARCH_BUDGET_S)
        deadline = monotonic() + budget_s
        budget_exhausted = False
        # Valid platforms = built-in adapters + any enabled source with a
        # find_candidates flow (active OR broken — Option B: sources become
        # platforms; broken flows are attempted and reported, not dropped).
        flow_platforms = await _flow_platforms()
        all_source_platforms = flow_platforms | await _candidate_source_platforms()
        # Canonicalize requested platform names against the known set so
        # display/builtin variants (e.g. "FastJobs") resolve to their source
        # ("fastjob"). Exact matches win; alias resolution is fallback only.
        # Unresolvable names are kept verbatim (they become the 422 list).
        resolved: list[str] = []
        for _p in platforms:
            _canon = resolve_candidate_platform(_p, all_source_platforms | set(_candidate_adapters()))
            resolved.append(_canon if _canon is not None else _p)
        platforms = resolved

        def _resolve(p: str) -> Any:
            return _candidate_adapters().get(p) or (
                _search_candidates_via_flow if p in all_source_platforms else None
            )

        unsupported = [p for p in platforms if _resolve(p) is None]
        if unsupported:
            supported = sorted(set(_candidate_adapters()) | all_source_platforms)
            human_reason = (
                f"Unsupported platform(s): {', '.join(unsupported)}; "
                f"supported: {supported}"
            )
            needs_human = True
        try:
            for platform in platforms:
                remaining = deadline - monotonic()
                if remaining < _SEARCH_MIN_REMAINING_S:
                    budget_exhausted = True
                    plan_details.append(f"{platform}: skipped — time budget exhausted")
                    continue
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
                # A platform adapter that raises (e.g. BrowserError
                # "extension went offline mid-search") must not discard the
                # results earlier platforms produced — mirror the budget and
                # "busy" handling: record the issue, keep partial results,
                # continue with the remaining platforms.
                from app.services.browser import BrowserError

                try:
                    if adapter is _search_candidates_via_flow:
                        result = await adapter(
                            source_name=platform,
                            queries=queries,
                            excludes=excludes or None,
                            location=location,
                            deadline=deadline,
                            use_agent=use_agent,
                        )
                    else:
                        result = await adapter(
                            queries=queries,
                            excludes=excludes or None,
                            location=location,
                            deadline=deadline,
                            use_agent=use_agent,
                        )
                except BrowserError as exc:
                    offline_issues.append({"source": platform, "reason": str(exc)})
                    plan_details.append(
                        f"{platform}: OFFLINE — {exc} (results so far kept)"
                    )
                    logger.warning(
                        "Candidate platform %s failed with BrowserError: %s",
                        platform,
                        exc,
                    )
                    continue
                p_raw = result.get("raw_results", [])
                raw.extend(p_raw)
                if result.get("needs_human"):
                    needs_human = True
                    if result.get("human_reason"):
                        human_reason = result["human_reason"]
                # A blocked platform must not nuke rows other platforms
                # produced: pause only when NOTHING was found anywhere. The
                # block reason is preserved in plan_detail, not discarded.
                if needs_human:
                    # Preserve the block reason (e.g. a broken-flow source's
                    # "no active find_candidates flow") in plan_detail either
                    # way: if other platforms produced rows the pause is
                    # suppressed; otherwise needs_human stays set and the
                    # reason is in human_reason — never silently dropped.
                    if human_reason:
                        plan_details.append(f"BLOCKED: {human_reason}")
                    if raw:
                        needs_human = False
                        human_reason = None
                plan_details.append(
                    result.get("plan_detail") or f"{platform}: {len(p_raw)} results"
                )
            plan_detail = " | ".join(plan_details) if plan_details else "No queries"
            detail = (
                f"Plan search ({', '.join(platforms)}) — {plan_detail}; {len(raw)} candidates"
                if not needs_human
                else f"Blocked: {human_reason}"
            )
            # Budget exhaustion is NOT a failure: return partial results and
            # surface the truncation so the user knows more was planned.
            source_issues = list(state.get("source_issues") or []) + offline_issues
            if offline_issues:
                # Offline errors are transient (extension sleep/websocket
                # blip) — treat like budget exhaustion: partial results
                # complete, message tells the user to reconnect + re-run.
                if raw:
                    needs_human = False
                    human_reason = None
                else:
                    needs_human = True
                    human_reason = (
                        "Extension agent went offline mid-search before any "
                        "results were collected — re-open the app so the "
                        "agent reconnects, then re-run"
                    )
                offline_note = offline_issues[0]["reason"]
                plan_detail = (
                    f"{plan_detail} | PARTIAL: {offline_note}"
                    if plan_detail
                    else f"PARTIAL: {offline_note}"
                )
            if budget_exhausted:
                partial_note = "time budget exhausted, returning partial results"
                source_issues = source_issues + [{"source": "budget", "reason": partial_note}]
                plan_detail = (
                    f"{plan_detail} | PARTIAL: {partial_note}"
                    if plan_detail
                    else f"PARTIAL: {partial_note}"
                )
                detail += " | budget exhausted — partial results"
                logger.warning(
                    "Candidate search budget (%.0fs) exhausted after %d results",
                    budget_s,
                    len(raw),
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
                "source_issues": source_issues,
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


def _clean(v: Any) -> Any:
    """Strip whitespace on strings; leave other types alone."""
    return v.strip() if isinstance(v, str) else v


def normalize(state: AgentState) -> AgentState:
    """NORMALIZE: map raw results to the canonical job/candidate schema.

    Drops junk rows (no title, and no url AND no company) so downstream
    matching never scores empty shells.
    """
    cleaned: list[dict[str, Any]] = []
    dropped = 0
    for raw in state.get("raw_results", []):
        item = {
            k: _clean(v)
            for k, v in (raw.items() if isinstance(raw, dict) else [])
        }
        title = str(item.get("title") or "").strip()
        name = str(item.get("name") or "").strip()
        url = str(item.get("source_url") or item.get("url") or "").strip()
        company = str(item.get("company") or "").strip()
        if (
            (not title or title.lower() == "unknown")
            and (not name or name.lower() == "unknown")
            and not url
            and not company
        ):
            dropped += 1
            continue
        if not title and name:
            title = name  # candidates carry name, not title
            item["title"] = title
        item["title"] = title
        item["source_url"] = url
        if not item.get("company"):
            item["company"] = company
        cleaned.append(item)
    msg = f"Normalized {len(cleaned)} records"
    if dropped:
        msg += f"; dropped {dropped} junk record(s)"
    return {
        **state,
        "normalized": cleaned,
        "timeline": _log(state, "NORMALIZE", msg),
    }


def _norm_text(s: Any) -> str:
    """Normalize text for fuzzy dedup: lower, strip punctuation, collapse spaces."""
    t = re.sub(r"[^\w\s]", " ", str(s or "").lower())
    return " ".join(t.split())


def deduplicate(state: AgentState) -> AgentState:
    """DEDUPLICATE: drop duplicates on (source, name), preferring deep links,
    then a cross-source fuzzy pass on the type-aware identity:
    (name-or-title, company-or-headline) — jobs key on (title, company),
    candidates on (name, headline).

    Flow extractions may share one URL per platform (the landing/search
    page used as the openable link when a site exposes no per-candidate
    hrefs), so the NAME is part of the identity — two different
    candidates on the same platform page must both survive. Conversely
    SEEK emits the same person twice (a /profile/<id> deep link AND a
    generic /search/profiles?...&uncoupledFreeText=<name> row) — collapse
    those to one, keeping the deep link.
    """
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for item in state.get("normalized", []):
        url = str(item.get("source_url", "") or "")
        name = str(item.get("name") or item.get("title") or "")
        key = (str(item.get("source", "")), name.strip().lower())
        prev = best.get(key)
        if prev is None:
            best[key] = item
        elif "uncoupledFreeText=" in str(prev.get("source_url", "") or "") and "uncoupledFreeText=" not in url:
            best[key] = item  # prefer the deep link over the search page

    # Cross-source fuzzy pass: same (title, company) on different platforms
    # is the same job. Prefer the row with a description, then a url.
    def _richer(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
        for field in ("description", "source_url"):
            if str(a.get(field) or "").strip() and not str(b.get(field) or "").strip():
                return a
            if str(b.get(field) or "").strip() and not str(a.get(field) or "").strip():
                return b
        return a

    fuzzy: dict[tuple[str, str], dict[str, Any]] = {}
    for item in best.values():
        # Type-aware identity: candidates (with `name`) key on
        # (name, headline); jobs (no `name`) fall back to (title, company).
        key = (
            _norm_text(item.get("name") or item.get("title")),
            _norm_text(
                item.get("company") or item.get("headline") or item.get("subtitle")
            ),
        )
        prev = fuzzy.get(key)
        fuzzy[key] = _richer(prev, item) if prev is not None else item

    unique = list(fuzzy.values())
    # Preserve first-seen order.
    order = {id(v): i for i, v in enumerate(state.get("normalized", []))}
    unique.sort(key=lambda v: order.get(id(v), 0))
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

    # Quality gate: drop jobs scoring below the minimum threshold.
    MIN_JOB_SCORE = 25.0
    note = None
    is_jobs = state.get("type") == SearchType.jobs
    if is_jobs:
        below = [r for r in scored if r.match_score < MIN_JOB_SCORE]
        if below and len(below) == len(scored):
            # Everything is below the bar: return top-3 with a quality note
            # rather than an empty result set.
            note = (
                f"All {len(scored)} result(s) scored below the quality "
                f"threshold ({MIN_JOB_SCORE:.0f}); returning best {min(3, len(scored))}"
            )
            scored = scored[:3]
        else:
            note = None
            scored = [r for r in scored if r.match_score >= MIN_JOB_SCORE]

    msg = f"Ranked {len(state.get('normalized', []))} results; returning top {len(scored)}"
    if note:
        msg += f" — {note}"
    return {
        **state,
        "results": scored,
        "status": TaskStatus.completed,
        "timeline": _log(state, "MATCH / RANK", msg),
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

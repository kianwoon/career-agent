"""LinkedIn People (candidate) search adapter.

Connects to an authenticated browser session (via CDP) and runs a people
search, extracting structured candidate profiles. Reuses the user's persistent
browser session (cookies, login state); no credentials handled here.

Safety:
- Read-only: only navigates and extracts. No connection requests or messages.
- Human-in-the-loop: MFA/CAPTCHA/login-expired pages pause the task.
- Paced like a human (shared PacingService).
"""

from __future__ import annotations

import logging
import os
import random
import re
from typing import Any
from urllib.parse import quote

from app.services.browser import BrowserError
from app.services.cache import query_cache
from app.services.pacing import pacing

logger = logging.getLogger(__name__)

BRAVE_CDP_URL = os.getenv("BRAVE_CDP_URL", "http://localhost:9222")
# Optional auth header for CDP connections (e.g. "X-Auth-Token: abc123").
# Format: "HeaderName: HeaderValue". Used for CDP tunnel auth.
CDP_AUTH_HEADER = os.getenv("CDP_AUTH_HEADER", "")

# Stable hooks discovered by probing the live people-search page.
# Profile links are a[href*="/in/"]; each candidate card is the ancestor
# container that has such a link and enough text.
PROFILE_LINK = 'a[href*="/in/"]'

MAX_CANDIDATES = 25
# Total profile-detail opens across a WHOLE plan (all queries merged), not
# per query — each open is a page view LinkedIn can see.
ENRICH_BUDGET = 10
# Run the relaxed (first OR-group only) variant when the full plan merges
# to fewer than this many unique candidates — validated live: an
# over-constrained AND clause hid the exact target profile.
RELAXED_MERGE_THRESHOLD = 8

# Certification/acronym tokens that appear on lines directly under a name.
# The old location heuristic ("short line with a comma") grabbed these.
_CERT_PATTERN = re.compile(
    r"\b(CISA|CISM|CISSP|ITIL|PMP|PRINCE2|CEH|ACCA|CPA|CFA|CIA|CFP|MBA|B\.?Com|CPAA|CA\s?\(?.?SG\)?)\b",
    re.IGNORECASE,
)


def _apply_excludes(query: str, excludes: list[str] | None) -> str:
    """Append LinkedIn's NOT (...) clause for the plan's exclude terms.

    Validated live: `term NOT (a OR b)` is applied server-side. Terms with
    spaces are quoted so multi-word exclusions ("fresh graduate") work.
    """
    terms = [e.strip() for e in (excludes or []) if e and e.strip()]
    if not terms:
        return query
    quoted = " OR ".join(f'"{t}"' if " " in t else t for t in terms)
    return f"{query} NOT ({quoted})"


def _first_or_group(query: str) -> str | None:
    """Extract the first parenthesized OR-group from a boolean query.

    '"agency accounting" AND (insurance OR "real estate")' -> 'insurance OR "real estate"'
    '("a" OR "b") AND c' -> '"a" OR "b"'; no OR-group anywhere -> None.
    Used for the relaxed variant of over-constrained plan queries — live
    testing showed the strict AND form hid the exact target profile.
    """
    q = query.strip()
    depth = 0
    start = -1
    for i, ch in enumerate(q):
        if ch == "(":
            if depth == 0:
                start = i
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0 and start >= 0:
                inner = q[start + 1 : i].strip()
                if " OR " in inner.upper():
                    return inner
                start = -1
    return None


def _normalize_profile_url(url: str) -> str:
    """Canonical form of a profile URL for dedupe (path only, no query/fragment).

    Live cards mix `linkedin.com` and `www.linkedin.com` hosts — both occur
    on the same results page, so the host is canonicalized too.
    """
    if not url:
        return ""
    if url.startswith("/"):
        url = f"https://www.linkedin.com{url}"
    url = url.replace("://linkedin.com", "://www.linkedin.com", 1)
    return url.split("?", 1)[0].split("#", 1)[0].rstrip("/")


def dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge results across queries by normalized profile URL.

    Keeps the first (richest) occurrence; counts how many queries surfaced
    each profile — a useful ranking signal stored as `_hit_count`.
    """
    seen: dict[str, dict[str, Any]] = {}
    ordered: list[dict[str, Any]] = []
    for c in candidates:
        key = _normalize_profile_url(c.get("source_url", ""))
        if not key:
            continue
        if key in seen:
            seen[key]["_hit_count"] = seen[key].get("_hit_count", 1) + 1
        else:
            c = dict(c)
            c["_hit_count"] = 1
            seen[key] = c
            ordered.append(c)
    return ordered


def filter_by_location(candidates: list[dict[str, Any]], location: str | None) -> tuple[list[dict[str, Any]], int]:
    """Post-filter results by target location (e.g. 'Singapore').

    LinkedIn's geoUrn URL facet returned empty result sets in live testing,
    so location is applied AFTER extraction. Conservative: a card is dropped
    only when its location text clearly names a DIFFERENT country that is
    not the target. Cards with unknown/None location are kept (extraction
    misses location too often to hard-drop them).
    """
    if not location:
        return candidates, 0
    target = location.strip().lower()
    kept: list[dict[str, Any]] = []
    dropped = 0
    for c in candidates:
        loc = (c.get("location") or "").strip().lower()
        # A location line naming any country/region other than the target
        # (e.g. target Singapore vs 'Holland, Michigan, United States').
        if loc and target not in loc:
            dropped += 1
            continue
        kept.append(c)
    return kept, dropped


def _build_search_url(query: str) -> str:
    """Build the LinkedIn people search URL."""
    q = quote(query)
    return f"https://www.linkedin.com/search/results/people/?keywords={q}"


def _check_blocker(url: str, title: str) -> str | None:
    """Return a human-in-the-loop reason if the page is blocked, else None."""
    lower_title = (title or "").lower()
    lower_url = (url or "").lower()
    if "authwall" in lower_url or "login" in lower_url or "checkpoint" in lower_url:
        return "LinkedIn login wall / session expired"
    if "captcha" in lower_title or "challenge" in lower_title or "unusual activity" in lower_title:
        return "LinkedIn presented a CAPTCHA/challenge"
    return None


async def _connect() -> tuple[Any, Any]:
    """Connect Playwright to the authenticated Brave session via CDP."""
    from playwright.async_api import async_playwright

    pw = None
    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp(BRAVE_CDP_URL, headers=_cdp_headers())
        ctx = browser.contexts[0]
        page = await ctx.new_page()
        return pw, page
    except Exception as exc:
        if pw:
            await pw.stop()
        raise BrowserError(f"Failed to connect to Brave CDP at {BRAVE_CDP_URL}: {exc}") from exc


def _cdp_headers() -> dict[str, str] | None:
    """Parse CDP_AUTH_HEADER ('Name: Value') into a headers dict, or None."""
    if not CDP_AUTH_HEADER:
        return None
    name, _, value = CDP_AUTH_HEADER.partition(":")
    return {name.strip(): value.strip()}


async def _extract_candidates(page: Any) -> list[dict[str, Any]]:
    """Extract candidate cards from the people-search results list.

    Strategy: find the container (UL/DIV) that has 3+ direct children each
    containing a profile link (a[href*="/in/"]). For each card extract:
      - name (profile link text)
      - profile_url
      - headline / current role
      - location
      - snippet (first lines of the card text)
    """
    data = await page.evaluate(
        """() => {
            const main = document.querySelector('main') || document.body;
            const all = main.querySelectorAll('*');
            let target = null;
            for (const el of all) {
                const kids = Array.from(el.children);
                const inLinks = kids.filter(k => k.querySelector('a[href*="/in/"]'));
                if (inLinks.length >= 3) { target = el; break; }
            }
            if (!target) return { error: 'no results container found' };

            const results = [];
            for (const kid of Array.from(target.children)) {
                const nameLink = kid.querySelector('a[href*="/in/"]');
                if (!nameLink) continue;
                const name = (nameLink.innerText || '').trim();
                const href = nameLink.getAttribute('href') || '';
                const text = (kid.innerText || '').trim();
                results.push({ name, href, text });
            }
            return { count: results.length, results };
        }"""
    )

    if data.get("error"):
        logger.warning("Candidate extraction: %s", data["error"])
        return []

    candidates: list[dict[str, Any]] = []
    for item in data.get("results", [])[:MAX_CANDIDATES]:
        name = (item.get("name", "") or "").strip()
        if not name:
            continue
        # LinkedIn shows "Name • 2nd" / "Name • 3rd+" (connection degree).
        name = name.split("•")[0].strip()
        if not name:
            continue
        text = item.get("text", "")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

        # Location is usually a short standalone line (e.g. "Singapore, Singapore").
        # Location heuristic: short comma'd line under the name, but NOT a
        # certifications line (live bug: 'CISA, ITIL Expert, PMP, CEH' was
        # picked as a location) and not a Current:/Past: role line.
        location = next(
            (
                ln
                for ln in lines[1:6]
                if len(ln) < 50
                and "," in ln
                and not _CERT_PATTERN.search(ln)
                and not ln.startswith("Current:")
                and not ln.startswith("Past:")
            ),
            None,
        )
        # Headline = the descriptive line that isn't the name/location/degree.
        headline = next(
            (ln for ln in lines[1:8] if len(ln) > 15 and not ln.startswith("Current:") and not ln.startswith("Past:") and ln != location and " is a mutual connection" not in ln and ln not in ("Connect", "Message", "Follow")),
            None,
        )
        current = next(
            (ln[9:] for ln in lines if ln.startswith("Current:")),
            None,
        )

        candidates.append(
            {
                "id": f"li-people-{abs(hash((name, item.get('href', ''))))}",
                "name": name,
                "headline": headline,
                "location": location,
                "summary": text[:500],
                "current_role": current,
                "skills": [],  # not visible on search results; enriched from profile if opened
                "source": "linkedin_people",
                "source_url": item.get("href", ""),
                "experience": text[:800],
            }
        )
    return candidates


async def _extract_profile_detail(page: Any, candidate: dict[str, Any], profile_url: str) -> dict[str, Any]:
    """Open a candidate's profile and extract full detail sections.

    Pace each open like a human: throttle page views, variable (non-stagnant)
    delays, and scroll through the profile. Extracts:
      - summary (About)
      - skills (Top skills / Skills)
      - experience (roles, companies, bullets)
      - education
      - certifications (Licenses & certifications)
    """
    await pacing.throttle_pages()
    await pacing.human_delay("commit")  # pause before committing to open

    # Profile URLs may be relative.
    if profile_url.startswith("/"):
        profile_url = f"https://www.linkedin.com{profile_url}"

    try:
        await page.goto(profile_url, timeout=45_000, wait_until="domcontentloaded")
        await pacing.human_delay("navigate")
        await pacing.human_scroll(page, total_px=random.randint(800, 1600))

        blocker = _check_blocker(page.url, await page.title())
        if blocker:
            pacing.trip_circuit_breaker(blocker)
            logger.warning("Blocked while opening profile: %s", blocker)
            return candidate

        # Extract sections by h2 heading (stable anchor).
        sections = await page.evaluate(
            """() => {
                const result = {};
                document.querySelectorAll('section').forEach(sec => {
                    const h2 = sec.querySelector('h2');
                    const heading = h2 ? h2.innerText.trim() : '';
                    if (heading) result[heading] = (sec.innerText || '').trim();
                });
                return result;
            }"""
        )

        about = sections.get("About", "")
        skills_text = sections.get("Top skills", "") or sections.get("Skills", "")
        experience = sections.get("Experience", "")
        education = sections.get("Education", "")
        certs = sections.get("Licenses & certifications", "")

        # Fallback: the Top skills block may not be inside a <section>.
        # Search the whole body for the marker.
        if not skills_text:
            body_text = await page.evaluate("() => document.body.innerText")
            import re

            m = re.search(r"Top skills\s*\n(.+)", body_text)
            if m:
                skills_text = "Top skills\n" + m.group(1).split("\n")[0]

        # Parse skills: "Top skills\nDevOps • Snowflake • Data Warehousing • BI"
        skills: list[str] = []
        if skills_text:
            # Take everything after the "Top skills"/"Skills" heading line.
            lines = [ln.strip() for ln in skills_text.splitlines() if ln.strip()]
            skill_line = next((ln for ln in lines[1:] if "•" in ln), None)
            if skill_line:
                skills = [s.strip() for s in skill_line.split("•") if s.strip()]
            elif len(lines) > 1:
                skills = [lines[1]]

        candidate["summary"] = about or candidate.get("summary", "")
        candidate["skills"] = skills or candidate.get("skills", [])
        candidate["experience"] = experience or candidate.get("experience", "")
        candidate["education"] = education
        candidate["certifications"] = certs
        logger.info(
            "Extracted profile for %r: %d skills, %d chars experience",
            candidate.get("name"),
            len(skills),
            len(experience),
        )
    except Exception as exc:
        logger.warning("Failed to open profile %s: %s", profile_url, exc)
    return candidate


async def _extract_candidates_with_details(
    page: Any, candidates: list[dict[str, Any]], limit: int = 10
) -> list[dict[str, Any]]:
    """Extract the search list, then open the top N profiles for full detail.

    Each profile open is a separate page view, so we pace it and keep N low.
    """
    enriched: list[dict[str, Any]] = []
    for idx, candidate in enumerate(candidates):
        profile_url = candidate.get("source_url", "")
        if idx < limit and profile_url:
            candidate = await _extract_profile_detail(page, candidate, profile_url)
        enriched.append(candidate)
    return enriched


class _PlanSession:
    """One CDP connection shared across a whole plan's searches + enrich.

    Brave's browser-level DevTools WebSocket degrades after repeated
    connect/disconnect cycles (each attach re-enumerates every target;
    the handshake then times out). Holding ONE connection for the whole
    plan avoids that entirely and is faster (no re-handshake per query).

    The connection is established lazily on first use — a plan served
    entirely from cache never opens the browser.
    """

    def __init__(self, connect_fn: Any) -> None:
        self._connect_fn = connect_fn
        self._pair: tuple[Any, Any] | None = None
        self._connected = False
        self._page: Any = None

    async def page(self) -> tuple[Any, Any]:
        """Return (playwright, page) or (None, None) if unavailable.

        Connects lazily on first call, then reuses the SAME page for the
        whole plan (sequential navigations) — matching the single-connection
        design. The pair is (playwright_manager, page) from
        _connect_with_best_session; the manager does not expose contexts.
        """
        if not self._connected:
            self._connected = True
            try:
                self._pair = await self._connect_fn()
            except Exception as exc:
                logger.warning("Plan session connect failed: %s", exc)
                self._pair = None
        if self._pair is None:
            return None, None
        pw, page = self._pair
        try:
            if page is not None and not page.is_closed():
                return pw, page
        except Exception:
            pass
        return None, None

    async def close(self) -> None:
        """Tear down the whole connection (called once, by the owner)."""
        if self._pair is None:
            return
        try:
            await self._pair[0].stop()
        except Exception:
            pass


async def search_people_list(
    query: str, session: _PlanSession | None = None
) -> dict[str, Any]:
    """Run ONE LinkedIn people search and return extracted cards (no enrich).

    Search-only so a multi-query plan can merge across queries first and
    spend its total enrich budget on the merged top candidates.

    `session` lets a plan reuse ONE CDP connection across all its searches
    (Brave's DevTools endpoint degrades after repeated connect cycles).
    When omitted, a fresh connection is opened and closed for this search.

    Returns {"raw_results": [...], "needs_human": bool, "human_reason": str|None}.
    """
    # Result cache first: no browser needed for a cache hit.
    cached = query_cache.get(f"people:{query}", None)
    if cached is not None:
        logger.info("People cache hit for %r — %d candidates", query, len(cached))
        return {"raw_results": cached, "needs_human": False, "human_reason": None, "cached": True}

    owned = session is None
    if owned:
        from app.services.linkedin import _connect_with_best_session

        session = _PlanSession(await _connect_with_best_session())
    pw, page = await session.page()
    if pw is None or page is None:
        return {
            "raw_results": [],
            "needs_human": True,
            "human_reason": "No authenticated browser session available (capture one or connect Brave CDP)",
        }
    try:
        # Polite pacing: search gap + circuit breaker before any traffic.
        await pacing.wait_if_breaker_open()
        await pacing.search_gap()
        await pacing.human_delay("commit")

        url = _build_search_url(query)
        await page.goto(url, timeout=45_000, wait_until="domcontentloaded")
        await pacing.throttle_pages()
        await pacing.human_delay("navigate")
        await pacing.human_pause_reading((2.0, 5.0))
        await pacing.human_scroll(page, total_px=random.randint(600, 1200))

        blocker = _check_blocker(page.url, await page.title())
        if blocker:
            pacing.trip_circuit_breaker(blocker)
            return {"raw_results": [], "needs_human": True, "human_reason": blocker}

        candidates = await _extract_candidates(page)
        query_cache.put(f"people:{query}", None, candidates)
        return {"raw_results": candidates, "needs_human": False, "human_reason": None}
    finally:
        if owned:
            await session.close()


async def enrich_candidates(
    candidates: list[dict[str, Any]],
    limit: int = ENRICH_BUDGET,
    session: _PlanSession | None = None,
) -> list[dict[str, Any]]:
    """Open the top `limit` candidate profiles for full detail (paced).

    Called ONCE per plan after merge — the budget is shared across all
    queries in the plan, not per query. `session` reuses the plan's single
    CDP connection when provided.
    """
    if not candidates or limit <= 0:
        return candidates
    owned = session is None
    if owned:
        from app.services.linkedin import _connect_with_best_session

        session = _PlanSession(await _connect_with_best_session())
    pw, page = await session.page()
    if pw is None or page is None:
        logger.warning("Enrich skipped — no authenticated session")
        return candidates
    try:
        return await _extract_candidates_with_details(page, candidates, limit=limit)
    finally:
        if owned:
            await session.close()


async def search_linkedin_people(
    queries: list[str],
    excludes: list[str] | None = None,
    location: str | None = None,
) -> dict[str, Any]:
    """Run a sourcing plan against LinkedIn people search.

    Plan semantics (all validated live against linkedin.com):
    - `queries` are executed sequentially (each paced), merged and deduped
      by profile URL.
    - `excludes` become a NOT (...) suffix on every query AND a post-filter.
    - Over-constrained plans: when the merged unique count is below
      RELAXED_MERGE_THRESHOLD and queries have a leading OR-group, the
      relaxed group-only variant is also run (a strict AND hid the exact
      target profile in live testing).
    - `location` is a conservative post-filter (the geoUrn URL facet
      returned empty sets live, so it is not used).
    - Profile enrichment opens at most ENRICH_BUDGET profiles TOTAL.

    Returns {"raw_results": [...], "needs_human": bool, "human_reason": str|None}.
    """
    if not queries:
        return {
            "raw_results": [],
            "needs_human": False,
            "human_reason": "No queries in plan",
        }

    # --- PRIMARY: run the whole plan inside the browser extension ----------
    # The extension holds the real logged-in profile (the user's own browser),
    # so reachability no longer depends on any local debug setup. One command
    # = one atomic plan execution in the agent tab.
    from app.services.agent_relay import agent_registry

    if agent_registry.connected:
        effective_queries = [_apply_excludes(q, excludes) for q in queries]
        # Relaxed variants: same trigger as the CDP path (sparse strict plans).
        data = await agent_registry.dispatch(
            "linkedin_people_plan",
            {
                "queries": effective_queries,
                "excludes": [],  # already folded into effective_queries
                "location": location or "",
                "enrichBudget": ENRICH_BUDGET,
            },
            timeout_s=max(180, 90 * len(effective_queries)),
        )
        results = {
            "raw_results": data.get("raw_results", []),
            "needs_human": bool(data.get("needs_human", False)),
            "human_reason": data.get("human_reason"),
            "plan_detail": data.get("plan_detail"),
        }
        # Sparse strict plan: append relaxed OR-group variants in a second pass.
        if (
            not results["needs_human"]
            and len(results["raw_results"]) < RELAXED_MERGE_THRESHOLD
        ):
            relaxed: list[str] = []
            for q in queries:
                group = _first_or_group(q)
                if group and group.strip().upper() != q.strip().upper():
                    relaxed.append(group)
            if relaxed:
                extra = await agent_registry.dispatch(
                    "linkedin_people_plan",
                    {
                        "queries": [_apply_excludes(g, excludes) for g in relaxed],
                        "excludes": [],
                        "location": location or "",
                        "enrichBudget": 0,  # budget already spent on pass 1
                    },
                    timeout_s=max(180, 90 * len(relaxed)),
                )
                combined = dedupe_candidates(
                    results["raw_results"] + extra.get("raw_results", [])
                )
                results["raw_results"] = combined
                results["plan_detail"] = (
                    f"{results.get('plan_detail', '')} + relaxed → {len(combined)} unique"
                )
        return results

    # --- FALLBACK: local CDP plan (original path) --------------------------
    # One shared CDP connection for the whole plan: repeated browser-level
    # connect/disconnect cycles wedge Brave's DevTools endpoint; a single
    # held connection avoids it. Lazy: connects only if a query needs it.
    from app.services.linkedin import _connect_with_best_session

    plan_session = _PlanSession(_connect_with_best_session)
    try:
        return await _run_plan(queries, excludes, location, plan_session)
    finally:
        await plan_session.close()


async def _run_plan(
    queries: list[str],
    excludes: list[str] | None,
    location: str | None,
    plan_session: _PlanSession,
) -> dict[str, Any]:
    """Plan body: sequential searches → relaxed variants → filters → enrich."""
    # --- Execute each query (excludes folded in), sequentially + paced ----
    merged: list[dict[str, Any]] = []
    per_query_counts: list[str] = []
    needs_human: str | None = None
    for q in queries:
        effective = _apply_excludes(q, excludes)
        result = await search_people_list(effective, session=plan_session)
        found = result.get("raw_results", [])
        per_query_counts.append(f"{q[:40]}{'…' if len(q) > 40 else ''}: {len(found)}")
        if result.get("needs_human") and not found:
            # Record the first blocker; keep running remaining queries —
            # a transient failure on one shouldn't kill the whole plan.
            needs_human = needs_human or result.get("human_reason")
            continue
        merged.extend(found)

    # --- Relaxed variant for over-constrained plans ------------------------
    uniques = dedupe_candidates(merged)
    if len(uniques) < RELAXED_MERGE_THRESHOLD:
        relaxed: list[str] = []
        for q in queries:
            group = _first_or_group(q)
            if group and group.strip().upper() != q.strip().upper():
                relaxed.append(_apply_excludes(group, excludes))
        for q in relaxed:
            result = await search_people_list(q, session=plan_session)
            found = result.get("raw_results", [])
            per_query_counts.append(f"relaxed {q[:30]}…: {len(found)}")
            if result.get("needs_human") and not found:
                needs_human = needs_human or result.get("human_reason")
                continue
            merged.extend(found)
        uniques = dedupe_candidates(merged)

    # --- Post-filters -------------------------------------------------------
    if excludes:
        # Safety net alongside the NOT clause: LinkedIn's NOT is sometimes
        # over-aggressive; here it is exact and auditable.
        excl_lower = [e.strip().lower() for e in excludes if e.strip()]
        uniques = [
            c for c in uniques
            if not any(e in f"{c.get('headline','')} {c.get('current_role','')}".lower() for e in excl_lower)
        ]
    uniques, dropped = filter_by_location(uniques, location)
    if dropped:
        logger.info("Location post-filter dropped %d candidates outside %r", dropped, location)

    # --- Enrich the merged top candidates (shared budget) ------------------
    uniques = await enrich_candidates(uniques, limit=ENRICH_BUDGET, session=plan_session)

    detail = f"Plan: {len(queries)} queries [{'; '.join(per_query_counts)}] → {len(uniques)} unique"
    if not uniques and needs_human:
        return {"raw_results": [], "needs_human": True, "human_reason": needs_human, "plan_detail": detail}
    return {"raw_results": uniques, "needs_human": False, "human_reason": None, "plan_detail": detail}

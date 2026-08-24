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
        location = next(
            (ln for ln in lines[1:6] if len(ln) < 50 and "," in ln and not ln.startswith("Current:") and not ln.startswith("Past:")),
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


async def search_linkedin_people(query: str) -> dict[str, Any]:
    """Run a LinkedIn people search through an authenticated browser session.

    Uses the captured/replayed stored session (fresh Chromium) when available;
    otherwise falls back to the Brave CDP connection.

    Returns:
        {
          "raw_results": [...],
          "needs_human": bool,
          "human_reason": str | None,
        }
    """
    from app.services.linkedin import _connect_with_best_session

    pw, page = await _connect_with_best_session()
    if pw is None or page is None:
        return {
            "raw_results": [],
            "needs_human": True,
            "human_reason": "No authenticated browser session available (capture one or connect Brave CDP)",
        }
    try:
        # Result cache: avoid re-hitting LinkedIn for the same query.
        cached = query_cache.get(f"people:{query}", None)
        if cached is not None:
            logger.info("People cache hit for %r — %d candidates", query, len(cached))
            return {"raw_results": cached, "needs_human": False, "human_reason": None, "cached": True}

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
        # Enrich the top N candidates by opening their profiles (paced).
        candidates = await _extract_candidates_with_details(page, candidates)
        query_cache.put(f"people:{query}", None, candidates)
        return {"raw_results": candidates, "needs_human": False, "human_reason": None}
    finally:
        try:
            await page.close()
        except Exception:
            pass
        try:
            await pw.stop()
        except Exception:
            pass

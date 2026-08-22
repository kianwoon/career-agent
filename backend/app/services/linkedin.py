"""LinkedIn job search adapter.

Connects to an authenticated browser session (via CDP) and runs a job search,
extracting structured job data. The adapter reuses the user's persistent
browser session (cookies, login state) so no credentials are handled here.

Safety:
- Read-only: only navigates and extracts. No messages, connections, or applies.
- Human-in-the-loop: MFA/CAPTCHA/login-expired pages pause the task.
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

# Where the authenticated browser (e.g. Brave) exposes CDP.
BRAVE_CDP_URL = os.getenv("BRAVE_CDP_URL", "http://localhost:9222")

# Selectors discovered by probing the live LinkedIn jobs page.
JOB_CARD = "li.scaffold-layout__list-item"
JOB_TITLE_LINK = ".job-card-container__link"
JOB_COMPANY = ".artdeco-entity-lockup__subtitle"
JOB_METADATA = ".job-card-container__metadata-wrapper"  # location, salary, type
JOB_FOOTER = ".job-card-list__footer-wrapper"  # posted date, easy apply, connections

# Job detail pane selectors (probed live).
JOB_DESCRIPTION = ".jobs-description-content__text, .jobs-box__html-content"
JOB_TOP_CARD = ".jobs-unified-top-card__content--two-pane, .jobs-unified-top-card"

MAX_JOBS = 25
# How many jobs to open for full detail extraction per search (keep it low to
# stay within reasonable usage; each open is a page view).
MAX_DETAIL_EXTRACTS = 5


def _build_search_url(query: str, location: str | None = None) -> str:
    """Build the LinkedIn jobs search URL."""
    q = quote(query)
    loc = quote(location) if location else ""
    return f"https://www.linkedin.com/jobs/search?keywords={q}&location={loc}"


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
    """Connect Playwright to the authenticated Brave session via CDP.

    Returns (playwright, page).
    """
    from playwright.async_api import async_playwright

    pw = None
    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp(BRAVE_CDP_URL)
        ctx = browser.contexts[0]
        page = await ctx.new_page()
        return pw, page
    except Exception as exc:
        if pw:
            await pw.stop()
        raise BrowserError(f"Failed to connect to Brave CDP at {BRAVE_CDP_URL}: {exc}") from exc


async def _connect_with_session(
    session_state_blob: str | None = None,
) -> tuple[Any, Any] | None:
    """Try to connect via a stored session first (fresh Chromium, no Brave).

    Falls back to CDP if no stored session is available.
    Returns (playwright, page) or None if both methods fail.
    """
    from app.services.session import connect_with_stored_session

    if session_state_blob:
        try:
            result = await connect_with_stored_session(
                session_state_blob,
                "https://www.linkedin.com/jobs/",
            )
            if result:
                return result
            logger.info("Stored session expired, falling back to CDP")
        except Exception as exc:
            logger.warning("Stored session connect failed: %s", exc)

    return None


async def _connect_with_best_session() -> tuple[Any, Any] | None:
    """Connect using the most reliable available session.

    Priority:
      1. CDP (persistent in-container Chromium / local Brave) — the SAME
         browser+profile that holds the login. This is the primary path on
         Koyeb and locally.
      2. Stored (encrypted) session replayed in a fresh Chromium — fallback
         when no CDP browser is reachable.
    """
    # 1. Prefer CDP: the persistent browser is the one that's actually logged in.
    try:
        return await _connect()
    except BrowserError:
        logger.info("CDP browser unavailable, falling back to stored session")

    # 2. Fallback: replay the stored (encrypted) session in a fresh Chromium.
    row = None
    try:
        from sqlalchemy import select

        from app.db import async_session
        from app.models.orm import BrowserSession

        async with async_session() as db:
            row = (
                await db.execute(
                    select(BrowserSession)
                    .where(BrowserSession.session_state.isnot(None))
                    .order_by(BrowserSession.captured_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
    except Exception as exc:
        logger.warning("Could not load stored session: %s", exc)

    if row is not None and row.session_state:
        result = await _connect_with_session(row.session_state)
        if result:
            return result
        logger.info("Stored session %s expired or invalid", row.id)

    return None, None


async def _extract_jobs(page: Any) -> list[dict[str, Any]]:
    """Extract structured jobs from the LinkedIn job cards list."""
    jobs: list[dict[str, Any]] = []
    cards = await page.locator(JOB_CARD).all()
    for card in cards[:MAX_JOBS]:
        try:
            title_el = card.locator(JOB_TITLE_LINK).first
            title = (await title_el.inner_text()).strip() if await title_el.count() else ""
            href = (await title_el.get_attribute("href")) if await title_el.count() else None

            company_el = card.locator(JOB_COMPANY).first
            company = (await company_el.inner_text()).strip() if await company_el.count() else ""

            meta_el = card.locator(JOB_METADATA).first
            metadata = (await meta_el.inner_text()).strip() if await meta_el.count() else ""

            footer_el = card.locator(JOB_FOOTER).first
            footer = (await footer_el.inner_text()).strip() if await footer_el.count() else ""

            if not title:
                continue

            # Parse location from metadata (usually first line).
            lines = [ln.strip() for ln in metadata.splitlines() if ln.strip()]
            location = lines[0] if lines else None
            salary = next((ln for ln in lines if "SGD" in ln or "$" in ln or "K" in ln), None)

            jobs.append(
                {
                    "id": f"li-{abs(hash((title, company, href)))}",
                    "title": title,
                    "company": company,
                    "location": location,
                    "salary_text": salary,
                    "description": "",  # filled when detail page is opened
                    "source": "linkedin",
                    "source_url": href or "",
                    "posted_at": footer.splitlines()[0] if footer else None,
                    "metadata_footer": footer,
                }
            )
        except Exception as exc:
            logger.warning("Skipping a job card: %s", exc)
    return jobs


async def _extract_job_detail(page: Any, job: dict[str, Any], href: str) -> dict[str, Any]:
    """Open a job's detail page and extract the full description.

    Pace each open like a human: throttle page views, variable (non-stagnant)
    delays, and scroll through the description with reading pauses.
    """
    await pacing.throttle_pages()
    await pacing.human_delay("commit")  # pause before committing to open

    # LinkedIn hrefs are relative; make absolute.
    if href.startswith("/"):
        href = f"https://www.linkedin.com{href}"

    try:
        await page.goto(href, timeout=45_000, wait_until="domcontentloaded")
        # Variable scan pause after load (human reads before acting).
        await pacing.human_delay("navigate")
        await pacing.human_scroll(page, total_px=random.randint(700, 1400))

        blocker = _check_blocker(page.url, await page.title())
        if blocker:
            pacing.trip_circuit_breaker(blocker)
            logger.warning("Blocked while opening job detail: %s", blocker)
            return job

        desc_el = page.locator(JOB_DESCRIPTION).first
        description = (await desc_el.inner_text()).strip() if await desc_el.count() else ""

        # Fallback: standalone job pages use obfuscated class names; find the
        # element whose text starts with the stable "About the job" marker.
        if not description:
            description = await page.evaluate(
                """() => {
                    const els = document.querySelectorAll('*');
                    for (const el of els) {
                        const t = (el.innerText || '').trim();
                        if (t.startsWith('About the job') && t.length > 200) {
                            return t;
                        }
                    }
                    return '';
                }"""
            )

        # Best-effort posted date / insights from the top card.
        top_el = page.locator(JOB_TOP_CARD).first
        top_text = (await top_el.inner_text()).strip() if await top_el.count() else ""

        job["description"] = description[:20_000]
        if not job.get("posted_at") and top_text:
            # e.g. "Head of Robotics, AI\nSingapore · Reposted 3 weeks ago · Over 100 applicants"
            lines = [ln.strip() for ln in top_text.splitlines() if ln.strip()]
            job["posted_at"] = next(
                (ln for ln in lines if "ago" in ln or "day" in ln or "week" in ln or "month" in ln),
                None,
            )
        logger.info("Extracted detail for %r (%d chars)", job.get("title"), len(description))
    except Exception as exc:
        logger.warning("Failed to open detail for %s: %s", href, exc)
    return job


async def _extract_jobs_with_details(
    page: Any, jobs: list[dict[str, Any]], limit: int = MAX_DETAIL_EXTRACTS
) -> list[dict[str, Any]]:
    """Extract the search list, then open the top N jobs for full details.

    Each detail open is a separate page view, so we pace it and keep N low
    (default 5). The remaining jobs keep their list-level data.
    """
    enriched: list[dict[str, Any]] = []
    for idx, job in enumerate(jobs):
        href = job.get("source_url", "")
        if idx < limit and href.startswith("/jobs/view/"):
            job = await _extract_job_detail(page, job, href)
        enriched.append(job)
    return enriched


async def search_linkedin_jobs(query: str, location: str | None = None) -> dict[str, Any]:
    """Run a LinkedIn job search through an authenticated browser session.

    Uses the captured/replayed session (fresh Chromium + stored cookies) when
    available; otherwise falls back to the Brave CDP connection.

    Returns:
        {
          "raw_results": [...],
          "needs_human": bool,
          "human_reason": str | None,
        }
    """
    # Prefer a stored (encrypted, replayed) session — no Brave needed.
    pw, page = await _connect_with_best_session()
    if pw is None or page is None:
        return {
            "raw_results": [],
            "needs_human": True,
            "human_reason": "No authenticated browser session available (capture one or connect Brave CDP)",
        }
    try:
        # Result cache: avoid re-hitting LinkedIn for the same query.
        cached = query_cache.get(query, location)
        if cached is not None:
            logger.info("Cache hit for %r (location=%r) — %d jobs", query, location, len(cached))
            return {"raw_results": cached, "needs_human": False, "human_reason": None, "cached": True}

        # Polite pacing: search gap + circuit breaker before any traffic.
        await pacing.wait_if_breaker_open()
        await pacing.search_gap()
        await pacing.human_delay("commit")  # pause before starting search

        url = _build_search_url(query, location)
        await page.goto(url, timeout=45_000, wait_until="domcontentloaded")
        await pacing.throttle_pages()

        # Variable scan pause after results load (human reads before scrolling).
        await pacing.human_delay("navigate")
        await pacing.human_pause_reading((2.0, 5.0))
        await pacing.human_scroll(page, total_px=random.randint(600, 1200))

        blocker = _check_blocker(page.url, await page.title())
        if blocker:
            pacing.trip_circuit_breaker(blocker)
            return {"raw_results": [], "needs_human": True, "human_reason": blocker}

        jobs = await _extract_jobs(page)
        # Open the top N jobs for full descriptions (paced per page view).
        jobs = await _extract_jobs_with_details(page, jobs)
        query_cache.put(query, location, jobs)
        return {"raw_results": jobs, "needs_human": False, "human_reason": None}
    finally:
        try:
            await page.close()
        except Exception:
            pass
        try:
            await pw.stop()
        except Exception:
            pass

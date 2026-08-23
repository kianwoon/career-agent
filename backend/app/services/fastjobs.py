"""FastJobs.sg job search adapter.

FastJobs is a Cloudflare-protected, server-side-rendered (SSR) job board.
Unlike MyCareersFuture there is no public JSON API — plain HTTP clients get a
403 Cloudflare challenge. A real browser (or a CDP-connected persistent
browser) passes the challenge and receives the SSR HTML.

This adapter therefore reuses the same connection strategy as the LinkedIn
adapter:
  1. Stored (encrypted, replayed) browser session in a fresh Chromium.
  2. CDP connection to a persistent authenticated browser (e.g. Brave).

It is read-only: it navigates listing pages, extracts job cards from the SSR
HTML, then opens the top N job-detail pages for full descriptions. All
navigation is paced via the shared :mod:`app.services.pacing` service.

Listing pages (SSR HTML, Cloudflare-passable):
    https://www.fastjobs.sg/singapore-jobs/all-categories-jobs/page-{N}/
    ?keyword=<query>

Job detail page (SSR HTML):
    https://www.fastjobs.sg/singapore-job-ad/{jobId}/{slug}/{company}/

Job card DOM (`.joblink`):
    .job-card__title          -> title
    .job-card__coy .coyinfo-title -> company
    .job-salary .salmin       -> salary (e.g. "S$ 2800 - 4000 / per month")
    .joblocation-info         -> location(s)
    .job-tag li               -> employment type, schedule, etc.
    data-job-id / href        -> id + detail URL

Job detail DOM (`#jobad`):
    h1 / .job-card__title     -> title
    .job-salary               -> salary
    .joblocation-info         -> location
    #jobad .job-description   -> description (largest text block)
"""

from __future__ import annotations

import logging
import random
import re
from typing import Any
from urllib.parse import quote

from app.services.browser import BrowserError
from app.services.cache import query_cache
from app.services.pacing import pacing

logger = logging.getLogger(__name__)

SOURCE = "fastjobs"
BASE_URL = "https://www.fastjobs.sg"
LISTING_URL = f"{BASE_URL}/singapore-jobs/all-categories-jobs"
# How many results to keep and how many detail pages to open per search.
MAX_JOBS = 25
MAX_DETAILS = 5

# --- Selectors (probed on the live site) -----------------------------------
JOB_CARD = "a.joblink[href*='/singapore-job-ad/']"
JOB_TITLE = ".job-card__title"
JOB_COMPANY = ".coyinfo-title"
JOB_SALARY = ".job-salary .salmin"
JOB_LOCATION = ".joblocation-info"
JOB_TAGS = ".job-tag li"
JOB_DETAIL_CONTAINER = "#jobad"
JOB_DETAIL_TITLE = "h1"
JOB_DETAIL_DESC = ".job-description, #jobad .job-description, .job-detail-description"


def _check_blocker(url: str, title: str) -> str | None:
    """Return a human-in-the-loop reason if the page is blocked, else None."""
    lower_title = (title or "").lower()
    lower_url = (url or "").lower()
    if "just a moment" in lower_title or "cloudflare" in lower_url or "challenge" in lower_title:
        return "FastJobs presented a Cloudflare challenge"
    if "captcha" in lower_title or "unusual activity" in lower_title:
        return "FastJobs presented a CAPTCHA/challenge"
    return None


async def _connect() -> tuple[Any, Any]:
    """Connect Playwright to a browser that can pass Cloudflare.

    Priority:
      1. Stored (encrypted, replayed) session in a fresh headless Chromium.
      2. CDP connection to a persistent browser (e.g. Brave).

    Returns (playwright, page), raises BrowserError if both fail.
    """
    from playwright.async_api import async_playwright

    # 1. Stored session first (no Brave needed, works headless).
    try:
        from app.services.session import connect_with_stored_session

        row = await _latest_session_row()
        if row is not None and row.session_state:
            result = await connect_with_stored_session(row.session_state, f"{BASE_URL}/")
            if result:
                logger.info("FastJobs: connected via stored session")
                return result
            logger.info("FastJobs: stored session expired/invalid")
    except Exception as exc:
        logger.warning("FastJobs stored-session connect failed: %s", exc)

    # 2. Fall back to CDP (persistent browser that already passed Cloudflare).
    import os

    from playwright.async_api import async_playwright

    cdp_url = os.getenv("BRAVE_CDP_URL", "http://localhost:9222")
    auth_header = os.getenv("CDP_AUTH_HEADER", "")
    headers = None
    if auth_header:
        name, _, value = auth_header.partition(":")
        headers = {name.strip(): value.strip()}

    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.connect_over_cdp(cdp_url, headers=headers)
        ctx = browser.contexts[0]
        page = await ctx.new_page()
        logger.info("FastJobs: connected via CDP at %s", cdp_url)
        return pw, page
    except Exception as exc:
        try:
            await pw.stop()
        except Exception:
            pass
        raise BrowserError(f"Failed to connect for FastJobs at {cdp_url}: {exc}") from exc


async def _latest_session_row() -> Any | None:
    """Return the most recent stored browser session row, or None."""
    from sqlalchemy import select

    from app.db import async_session
    from app.models.orm import BrowserSession

    try:
        async with async_session() as db:
            return (
                await db.execute(
                    select(BrowserSession)
                    .where(BrowserSession.session_state.isnot(None))
                    .order_by(BrowserSession.captured_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
    except Exception as exc:
        logger.warning("Could not load stored session: %s", exc)
        return None


def _build_listing_url(query: str, page: int = 1) -> str:
    """Build a listing page URL for a query + page (1-based)."""
    base = f"{LISTING_URL}/page-{page}/" if page > 1 else f"{LISTING_URL}/"
    return f"{base}?keyword={quote(query)}"


async def _extract_jobs(page: Any) -> list[dict[str, Any]]:
    """Extract structured jobs from the FastJobs listing cards."""
    jobs: list[dict[str, Any]] = []
    cards = page.locator(JOB_CARD)
    count = await cards.count()
    for i in range(min(count, MAX_JOBS)):
        try:
            card = cards.nth(i)
            title_el = card.locator(JOB_TITLE).first
            title = (await title_el.inner_text()).strip() if await title_el.count() else ""

            coy_el = card.locator(JOB_COMPANY).first
            company = (await coy_el.inner_text()).strip() if await coy_el.count() else ""

            sal_el = card.locator(JOB_SALARY).first
            salary = (await sal_el.inner_text()).strip() if await sal_el.count() else ""

            loc_el = card.locator(JOB_LOCATION).first
            location = (await loc_el.inner_text()).strip() if await loc_el.count() else ""

            tags_el = card.locator(JOB_TAGS)
            tag_count = await tags_el.count()
            tags: list[str] = []
            for j in range(tag_count):
                try:
                    t = (await tags_el.nth(j).inner_text()).strip()
                    if t:
                        tags.append(t)
                except Exception:
                    continue

            href = await card.get_attribute("href")
            job_id = await card.get_attribute("data-job-id")

            if not title:
                continue

            # Parse salary to a clean text (e.g. "S$2800 - 4000 / per month").
            salary_text = _clean_salary(salary)

            jobs.append(
                {
                    "id": f"fj-{job_id or abs(hash((title, company, href)))}",
                    "title": title,
                    "company": company,
                    "location": location or None,
                    "salary_text": salary_text,
                    "description": "",  # filled when detail page is opened
                    "source": SOURCE,
                    "source_url": href or "",
                    "employment_type": next((t for t in tags if _looks_employment_type(t)), None),
                    "schedule": next((t for t in tags if _looks_schedule(t)), None),
                    "metadata_tags": tags,
                }
            )
        except Exception as exc:
            logger.warning("Skipping a FastJobs card: %s", exc)
    return jobs


def _clean_salary(text: str | None) -> str | None:
    """Normalize the raw salary text (e.g. 'S$ 2800 - 4000 / per month')."""
    text = re.sub(r"\s+", " ", (text or "")).strip()
    return text or None


def _looks_employment_type(text: str) -> bool:
    lowered = text.lower()
    return any(k in lowered for k in ("full time", "part time", "contract", "permanent", "temporary", "internship", "freelance"))


def _looks_schedule(text: str) -> bool:
    lowered = text.lower()
    # Use word boundaries to avoid matching "month" for "mon" or "hourly" for "hour".
    return bool(re.search(r"\b(day week|shift|rotating|5\.5|mon(?:day)?(?:-fri)?|tue|weekend|hour)\b", lowered))


async def _extract_job_detail(page: Any, job: dict[str, Any]) -> dict[str, Any]:
    """Open a job's detail page and extract the full description."""
    href = job.get("source_url", "")
    if not href or not href.startswith(f"{BASE_URL}/singapore-job-ad/"):
        return job

    # Strip query params (e.g. ?source=search) — they break the detail page.
    href = href.split("?", 1)[0]

    await pacing.throttle_pages()
    await pacing.human_delay("commit")

    try:
        await page.goto(href, timeout=45_000, wait_until="domcontentloaded")
        await pacing.human_delay("navigate")
        await pacing.human_scroll(page, total_px=random.randint(600, 1400))

        blocker = _check_blocker(page.url, await page.title())
        if blocker:
            pacing.trip_circuit_breaker(blocker)
            logger.warning("FastJobs blocked on detail: %s", blocker)
            return job

        # Description: look for the largest meaningful text block inside #jobad.
        description = await page.evaluate(
            """() => {
                const root = document.querySelector('#jobad') || document.body;
                const els = root.querySelectorAll('div, section, article');
                let best = '';
                for (const el of els) {
                    const t = (el.innerText || '').trim();
                    if (t.length > best.length && t.length > 200 &&
                        !t.includes('DOWNLOAD MOBILE APP') &&
                        !t.includes('What\u2019s your preferred work location')) {
                        best = t;
                    }
                }
                return best;
            }"""
        )
        # Fallback: the whole #jobad text minus boilerplate.
        if not description:
            description = await page.evaluate(
                """() => {
                    const root = document.querySelector('#jobad');
                    return root ? root.innerText.trim() : '';
                }"""
            )

        job["description"] = description[:20_000]
        logger.info("FastJobs detail for %r (%d chars)", job.get("title"), len(description))
    except Exception as exc:
        logger.warning("Failed to open FastJobs detail for %s: %s", href, exc)
    return job


async def _extract_jobs_with_details(
    page: Any, jobs: list[dict[str, Any]], limit: int = MAX_DETAILS
) -> list[dict[str, Any]]:
    """Extract the listing, then open the top N jobs for full details."""
    enriched: list[dict[str, Any]] = []
    for idx, job in enumerate(jobs):
        if idx < limit:
            job = await _extract_job_detail(page, job)
        enriched.append(job)
    return enriched


async def search_fastjobs_jobs(query: str, location: str | None = None) -> dict[str, Any]:
    """Run a FastJobs job search through a Cloudflare-capable browser.

    Args:
        query: free-text job search terms.
        location: currently unused (FastJobs listing is nationwide); kept for
            interface parity with the other adapters.

    Returns:
        Dict with keys ``raw_results``, ``needs_human``, ``human_reason`` —
        the same contract as the LinkedIn/MyCareersFuture adapters.
    """
    # Result cache: avoid re-hitting FastJobs for the same query.
    cached = query_cache.get(query, location)
    if cached is not None:
        logger.info("FastJobs cache hit for %r — %d jobs", query, len(cached))
        return {"raw_results": cached, "needs_human": False, "human_reason": None, "cached": True}

    try:
        pw, page = await _connect()
    except BrowserError as exc:
        return {
            "raw_results": [],
            "needs_human": True,
            "human_reason": str(exc),
        }
    if pw is None or page is None:
        return {
            "raw_results": [],
            "needs_human": True,
            "human_reason": "No browser session available for FastJobs (capture one or connect Brave CDP)",
        }

    try:
        await pacing.wait_if_breaker_open()
        await pacing.search_gap()
        await pacing.human_delay("commit")

        url = _build_listing_url(query)
        await page.goto(url, timeout=45_000, wait_until="domcontentloaded")
        await pacing.throttle_pages()
        await pacing.human_delay("navigate")
        await pacing.human_pause_reading((2.0, 5.0))
        await pacing.human_scroll(page, total_px=random.randint(600, 1200))

        blocker = _check_blocker(page.url, await page.title())
        if blocker:
            pacing.trip_circuit_breaker(blocker)
            return {"raw_results": [], "needs_human": True, "human_reason": blocker}

        jobs = await _extract_jobs(page)
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

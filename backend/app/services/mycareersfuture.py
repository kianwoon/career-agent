"""MyCareersFuture (MCF) job search adapter.

Queries the public MyCareersFuture.gov.sg JSON API directly over HTTPS.

Unlike the LinkedIn adapter (which needs an authenticated CDP browser
session), MCF exposes a public job-search API that accepts a JSON POST and
requires no login. This adapter therefore needs no browser, no session state,
and no pacing service for auth protection — we simply stay a polite client.

Endpoints (verified August 2026):
    POST https://api.mycareersfuture.gov.sg/v2/search?limit=20&page=N
        body: {"sessionId":"","search":"<query>","postingCompany":[],
               "sortBy":["new_posting_date"]}
        -> {results: [...], total, countWithoutFilters, _links}
    GET  https://api.mycareersfuture.gov.sg/v2/jobs/{uuid}
        -> full job detail incl. HTML description, skills, salary, employer.

The search response already carries rich fields (salary, skills, employer,
location) for every result, so detail pages are only fetched for the top
``MAX_DETAILS`` jobs to get the full description — mirroring the LinkedIn
adapter's pacing behaviour.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://api.mycareersfuture.gov.sg"
SEARCH_ENDPOINT = f"{API_BASE}/v2/search"
JOB_DETAIL_ENDPOINT = f"{API_BASE}/v2/jobs/{{uuid}}"
SOURCE = "mycareersfuture"

# How many results to fetch from a single search call.
PAGE_SIZE = 20
# How many jobs to open for full detail extraction per search (page views).
MAX_JOBS = 25
MAX_DETAILS = 8
# Polite client: timeout and a browser-like UA.
REQUEST_TIMEOUT = 20.0
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

_STRIP_TAGS_PATTERN = None  # lazily compiled; see _strip_html


def _strip_html(text: str | None) -> str:
    """Strip HTML tags and decode entities, returning plain text."""
    if not text:
        return ""
    import html
    import re

    global _STRIP_TAGS_PATTERN
    if _STRIP_TAGS_PATTERN is None:
        _STRIP_TAGS_PATTERN = re.compile(r"<[^>]+>")
    stripped = _STRIP_TAGS_PATTERN.sub(" ", text)
    stripped = html.unescape(stripped)
    return re.sub(r"\s+", " ", stripped).strip()


def _format_salary(salary: dict[str, Any] | None) -> str | None:
    """Format the MCF salary object into a human-readable string."""
    if not salary:
        return None
    minimum = salary.get("minimum")
    maximum = salary.get("maximum")
    salary_type = ((salary.get("type") or {}).get("salaryType")) or ""
    if not minimum and not maximum:
        return None
    if minimum and maximum:
        text = f"SGD {minimum:,.0f} - {maximum:,.0f}"
    else:
        value = minimum or maximum
        text = f"SGD {value:,.0f}"
    if salary_type:
        text = f"{text} {salary_type.lower()}"
    return text


def _format_location(address: dict[str, Any] | None) -> str:
    """Build a human-readable location from the MCF address object."""
    if not address:
        return ""
    parts = []
    building = address.get("building")
    if building:
        parts.append(str(building))
    postal = address.get("postalCode")
    if postal:
        parts.append(f"Singapore {postal}")
    districts = address.get("districts") or []
    if districts:
        region = districts[0].get("region")
        if region:
            parts.append(str(region))
    return ", ".join(parts)


def _format_employment_types(types: list[dict[str, Any]] | None) -> str | None:
    """Join employment types (e.g. ['Contract', 'Full Time'])."""
    if not types:
        return None
    names = [str(t.get("employmentType")) for t in types if t.get("employmentType")]
    return ", ".join(names) if names else None


def _extract_skills(skills: list[dict[str, Any]] | None) -> list[str]:
    """Pull skill names from the MCF skills array."""
    if not skills:
        return []
    return [str(s.get("skill")) for s in skills if s.get("skill")]


def _build_search_url(page: int = 0) -> str:
    """Build the search URL for a given 0-based page."""
    return f"{SEARCH_ENDPOINT}?limit={PAGE_SIZE}&page={page}"


async def _client() -> httpx.AsyncClient:
    """Return a configured HTTP client."""
    return httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )


async def _search_jobs(query: str, page: int = 0) -> dict[str, Any]:
    """Call the MCF search API and return the raw JSON payload."""
    payload = {
        "sessionId": "",
        "search": query,
        "postingCompany": [],
        "sortBy": ["new_posting_date"],
    }
    async with await _client() as client:
        resp = await client.post(_build_search_url(page), json=payload)
        resp.raise_for_status()
        return resp.json()


async def _fetch_job_detail(uuid: str) -> dict[str, Any] | None:
    """Fetch the full detail for one job by uuid, or None on failure."""
    try:
        async with await _client() as client:
            resp = await client.get(JOB_DETAIL_ENDPOINT.format(uuid=uuid))
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:  # BLE001: detail fetch is best-effort
        logger.warning("MCF detail fetch failed for %s: %s", uuid, exc)
        return None


def _to_raw_job(item: dict[str, Any], detail: dict[str, Any] | None = None) -> dict[str, Any]:
    """Map one MCF search result (+ optional detail) to the canonical raw job dict."""
    metadata = item.get("metadata") or {}
    address = item.get("address") or {}
    company = item.get("postedCompany") or {}
    uuid = item.get("uuid") or metadata.get("jobPostId") or ""

    raw = {
        "id": metadata.get("jobPostId") or uuid,
        "title": item.get("title", "Untitled"),
        "company": company.get("name"),
        "location": _format_location(address) or None,
        "source": SOURCE,
        "source_url": metadata.get("jobDetailsUrl") or (
            f"https://www.mycareersfuture.gov.sg/job/{uuid}" if uuid else None
        ),
        "description": "",
        "salary_text": _format_salary(item.get("salary")),
        "posted_at": metadata.get("newPostingDate"),
        "employment_type": _format_employment_types(item.get("employmentTypes")),
        "skills": _extract_skills(item.get("skills")),
        "category": (
            (item.get("categories") or [{}])[0].get("category")
            if item.get("categories")
            else None
        ),
        "position_level": (
            (item.get("positionLevels") or [{}])[0].get("position")
            if item.get("positionLevels")
            else None
        ),
    }

    if detail:
        raw["description"] = _strip_html(detail.get("description"))
        raw["minimum_years_experience"] = detail.get("minimumYearsExperience")
        raw["number_of_vacancies"] = detail.get("numberOfVacancies")
        raw["other_requirements"] = _strip_html(detail.get("otherRequirements"))
        raw["working_hours"] = detail.get("workingHours")
        raw["expiry_date"] = (detail.get("metadata") or {}).get("expiryDate")
        raw["salary_text"] = _format_salary(detail.get("salary")) or raw.get("salary_text")
    return raw


async def search_mycareersfuture_jobs(
    query: str,
    location: str | None = None,
    max_jobs: int = MAX_JOBS,
    max_details: int = MAX_DETAILS,
) -> dict[str, Any]:
    """Run a job search against MyCareersFuture and return raw job dicts.

    Args:
        query: free-text job search terms.
        location: currently unused by the MCF search API (the site filters on
            the client side); kept for interface parity with the LinkedIn
            adapter.
        max_jobs: max results to return (list-card data).
        max_details: max jobs to open for full description extraction.

    Returns:
        Dict with keys ``raw_results``, ``needs_human``, ``human_reason`` —
        the same contract as the LinkedIn adapter.
    """
    try:
        data = await _search_jobs(query, page=0)
        items = data.get("results") or []
        logger.info("MCF search %r -> %d results (total=%s)", query, len(items), data.get("total"))
    except Exception as exc:
        logger.warning("MCF search failed for %r: %s", query, exc)
        return {
            "raw_results": [],
            "needs_human": False,  # API failure is transient, not a human blocker
            "human_reason": f"MyCareersFuture search failed: {exc}",
        }

    raw: list[dict[str, Any]] = []
    for item in items[:max_jobs]:
        raw.append(_to_raw_job(item))

    # Fetch full descriptions for the top N jobs (paced; each is a page view).
    for i, item in enumerate(items[:max_details]):
        uuid = item.get("uuid")
        if not uuid:
            continue
        detail = await _fetch_job_detail(uuid)
        if detail is None:
            continue
        raw[i] = _to_raw_job(item, detail)

    return {
        "raw_results": raw,
        "needs_human": False,
        "human_reason": None,
    }

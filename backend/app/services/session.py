"""Browser session capture & replay service.

Captures a signed-in user's session state (cookies + localStorage) from an
authenticated browser via CDP, encrypts it, stores it in the DB. Replay
decrypts the state and launches a fresh Chromium with the cookies applied —
no dependency on the original browser profile.

Security (spec §7 + §15):
- Cookies are sensitive credentials. Always encrypted at rest.
- Never log raw cookies or decrypted state.
- Session rows are scoped by user_id.
- Replay verifies the login actually works (no silent failure).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

from app.models.orm import BrowserSession
from app.services.encryption import decrypt_session_state, encrypt_session_state
from app.services.proxy import proxy_config
from app.services.site_profiles import profile_for_source

logger = logging.getLogger(__name__)

BRAVE_CDP_URL = os.getenv("BRAVE_CDP_URL", "http://localhost:9222")  # same default as adapters
# Optional auth header for CDP connections (e.g. "X-Auth-Token: abc123").
# Format: "HeaderName: HeaderValue". Used for CDP tunnel auth.
CDP_AUTH_HEADER = os.getenv("CDP_AUTH_HEADER", "")

# Which session-state we keep per domain (Playwright storage_state format).
STORAGE_STATE_DOMAINS = ["linkedin.com", "www.linkedin.com"]


def _cdp_headers() -> dict[str, str] | None:
    """Parse CDP_AUTH_HEADER ('Name: Value') into a headers dict, or None."""
    if not CDP_AUTH_HEADER:
        return None
    name, _, value = CDP_AUTH_HEADER.partition(":")
    return {name.strip(): value.strip()}


async def capture_from_cdp(session: BrowserSession) -> BrowserSession:
    """Capture cookies + localStorage from the live signed-in browser (CDP).

    Connects to the running Brave instance on the debug port, reads the
    storage_state for the target domains, encrypts it, and stores on the
    session row (not yet committed by this function).
    """
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.connect_over_cdp(BRAVE_CDP_URL, headers=_cdp_headers())
        ctx = browser.contexts[0]
        # Ensure a page exists on the target domain so cookies/localStorage load.
        page = await ctx.new_page()
        try:
            await page.goto(
                "https://www.linkedin.com/",
                timeout=30_000,
                wait_until="domcontentloaded",
            )
            await asyncio.sleep(2)
        except Exception as exc:
            logger.warning("Pre-capture navigation to linkedin failed: %s", exc)

        state = await ctx.storage_state()
        # Filter to the domains we care about (drop unrelated sites).
        filtered = _filter_state(state, STORAGE_STATE_DOMAINS)

        # Compute earliest cookie expiry for lifecycle metadata.
        expires = _earliest_expiry(filtered)

        session.session_state = encrypt_session_state(json.dumps(filtered))
        session.captured_at = datetime.now(UTC)
        session.expires_at = expires
        session.domains = STORAGE_STATE_DOMAINS
        session.session_label = "linkedin-signed-in"
        session.status = "captured"

        await page.close()
        n_cookies = len(filtered.get("cookies", []))
        logger.info("Captured %d cookies for session %s", n_cookies, session.id)
        return session
    finally:
        try:
            await pw.stop()
        except Exception:
            pass


async def replay_session(session: BrowserSession) -> str:
    """Replay a captured session in a fresh Chromium.

    Decrypts the stored storage_state, launches a fresh headless Chromium,
    applies the cookies, navigates to LinkedIn, and verifies login.

    Returns "logged_in" or "logged_out" (does not raise for logged-out —
    the caller decides whether to request human takeover).
    """
    from playwright.async_api import async_playwright

    if not session.session_state:
        raise ValueError("Session has no captured state — capture first")

    state = json.loads(decrypt_session_state(session.session_state))

    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.launch(headless=True, proxy=proxy_config())
        ctx = await browser.new_context()
        await ctx.add_cookies(state.get("cookies", []))
        page = await ctx.new_page()
        await page.goto(
            "https://www.linkedin.com/feed/",
            timeout=45_000,
            wait_until="domcontentloaded",
        )
        await asyncio.sleep(3)

        url = page.url
        title = await page.title()
        logged_in = "/login" not in url and "authwall" not in url and "Sign in" not in title

        await ctx.close()
        await browser.close()
        logger.info("Replay for session %s -> %s", session.id, "logged_in" if logged_in else "logged_out")
        return "logged_in" if logged_in else "logged_out"
    finally:
        try:
            await pw.stop()
        except Exception:
            pass


def _filter_state(state: dict, domains: list[str]) -> dict:
    """Keep only cookies/localStorage for the given domains."""
    cookies = [
        c for c in state.get("cookies", [])
        if any(c.get("domain", "").endswith(d.lstrip(".")) for d in domains)
    ]
    origins = [
        o for o in state.get("origins", [])
        if any(d in o.get("origin", "") for d in domains)
    ]
    return {"cookies": cookies, "origins": origins}


# How far ahead of the earliest cookie expiry we consider a session "near
# expiry" and in need of refresh.
REFRESH_LEAD_DAYS = 7


def session_needs_refresh(session: BrowserSession, lead_days: int = REFRESH_LEAD_DAYS) -> bool:
    """True if the session has no expiry or is within lead_days of expiring."""
    if not session.session_state:
        return True  # nothing captured -> needs capture
    if session.expires_at is None:
        return True  # no expiry info (e.g. all session cookies) -> be safe
    # Naive vs aware: expires_at is stored tz-aware; compare with now.
    now = datetime.now(UTC)
    exp = session.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=UTC)
    return (exp - now).total_seconds() < lead_days * 86400


async def refresh_from_cdp(session: BrowserSession) -> bool:
    """Re-capture the session from the live signed-in browser (CDP).

    Returns True if a fresh capture happened, False if CDP is unavailable
    (caller should keep the old session and/or request human takeover).
    """
    try:
        await capture_from_cdp(session)
        logger.info("Refreshed session %s via CDP (expires %s)", session.id, session.expires_at)
        return True
    except Exception as exc:
        logger.warning("Refresh via CDP failed for %s: %s", session.id, exc)
        return False


def _earliest_expiry(state: dict) -> datetime | None:
    """Earliest cookie expiry among the captured cookies (or None)."""
    expiries = []
    for c in state.get("cookies", []):
        exp = c.get("expires")
        if isinstance(exp, (int, float)) and exp > 0:
            expiries.append(exp)
    if not expiries:
        return None
    earliest = min(expiries)
    try:
        return datetime.fromtimestamp(earliest, tz=UTC)
    except (ValueError, OSError):
        return None


# ---------------------------------------------------------------------------
# Extension cookie capture helpers (shared by the /agent_session endpoints
# and the pre-search self-heal path).
# ---------------------------------------------------------------------------

# Cloudflare clearance/challenge cookies are bound to the issuing host + client
# and MUST NOT be replayed by a different browser (server-side Playwright): the
# CF edge rejects a mismatched clearance and can flag the session. Sites whose
# profile is cloudflare_protected have these stripped before storage.
_CF_COOKIE_PREFIXES = ("__cf", "cf_clearance", "cf_bm")

# A stored source session is considered STALE (and worth re-capturing via the
# extension relay) when it is missing, older than this age, or within this
# lead of its earliest cookie expiry.
SESSION_MAX_AGE_H = 24.0
SESSION_EXPIRY_LEAD_H = 12.0


def _is_cloudflare_cookie(name: str) -> bool:
    """True for a Cloudflare clearance/challenge cookie name."""
    n = (name or "").lower()
    return n.startswith(_CF_COOKIE_PREFIXES)


def relevant_cookie_domains(source: Any) -> list[str]:
    """Domains whose cookies authorize a source's recorded flows.

    The source domain plus — for FastJobs, which authenticates every regional
    TLD against its shared employer portal — the employer and apex hosts.
    Never raises.
    """
    domain = (getattr(source, "domain", None) or (source if isinstance(source, str) else "") or "")
    domain = domain.strip().lower()
    domains: list[str] = []
    if domain:
        domains.append(domain)
    if "fastjobs." in domain:
        domains.extend(["employer.fastjobs.sg", "fastjobs.sg"])
    return domains


def _cookie_domain_matches(cookie_domain: str, domains: list[str]) -> bool:
    """True if a cookie's (possibly leading-dot) domain falls under any target."""
    host = (cookie_domain or "").lstrip(".").lower()
    if not host:
        return False
    for d in domains:
        target = d.lstrip(".").lower()
        if host == target or host.endswith("." + target):
            return True
    return False


def _strip_cloudflare(cookies: list[dict]) -> list[dict]:
    """Drop Cloudflare cookies, but never return an empty list if input wasn't."""
    kept = [c for c in cookies if not _is_cloudflare_cookie(c.get("name", ""))]
    if not kept and cookies:
        logger.warning(
            "All %d cookies were Cloudflare-owned; keeping them (nothing else to store)",
            len(cookies),
        )
        return list(cookies)
    return kept


def prepare_source_cookies(
    cookies: list[dict], source: Any
) -> tuple[list[dict], datetime | None]:
    """Filter extension cookies to a source's hosts and compute earliest expiry.

    Returns (cookies, expires_at). Cookies are filtered to the source's relevant
    domains (see `relevant_cookie_domains`) and, when the source's profile is
    cloudflare_protected, CF clearance cookies are stripped. Tolerant by design:
    if filtering would drop ALL cookies from a non-empty input we keep the
    unfiltered list (log a warning) rather than store nothing. `expires_at` is
    the earliest cookie expiry, or None for an all-session-cookie state.
    """
    if not cookies:
        return [], None
    relevant = relevant_cookie_domains(source)
    filtered = [
        c for c in cookies if _cookie_domain_matches(c.get("domain", ""), relevant)
    ]
    if not filtered:
        logger.warning(
            "Cookie domain filter matched none of %s for source %s — keeping all %d cookies",
            relevant,
            getattr(source, "name", "?"),
            len(cookies),
        )
        filtered = list(cookies)

    prof = profile_for_source(
        getattr(source, "domain", "") or getattr(source, "base_url", ""),
        getattr(source, "profile", None),
    )
    if prof and prof.cloudflare_protected:
        filtered = _strip_cloudflare(filtered)

    return filtered, _earliest_expiry({"cookies": filtered})


def session_is_stale(source: Any) -> bool:
    """True when a source's stored session should be re-captured before use.

    Stale when: no session_state, captured more than SESSION_MAX_AGE_H ago, or
    within SESSION_EXPIRY_LEAD_H of its earliest cookie expiry (or already past).
    """
    if not getattr(source, "session_state", None):
        return True
    now = datetime.now(UTC)
    captured = getattr(source, "captured_at", None)
    if captured is not None:
        c = captured if captured.tzinfo else captured.replace(tzinfo=UTC)
        if (now - c).total_seconds() > SESSION_MAX_AGE_H * 3600:
            return True
    exp = getattr(source, "expires_at", None)
    if exp is not None:
        e = exp if exp.tzinfo else exp.replace(tzinfo=UTC)
        if (e - now).total_seconds() < SESSION_EXPIRY_LEAD_H * 3600:
            return True
    return False


async def self_heal_source_session(
    source: Any, db: Any, entry_url: str | None = None
) -> bool:
    """Best-effort re-capture of a stale source session via the extension relay.

    Runs BEFORE a recorded flow. If the session is stale and the source is not
    anonymous, ask the extension for its live cookies for `entry_url` (falling
    back to the source base_url), filter them, and store the blob
    (session_state/captured_at/expires_at) on the row. Returns True if a fresh
    capture was stored.

    Never raises and NEVER breaks a passing flow: a missing extension
    (RuntimeError), empty capture, or DB error just logs and returns False so the
    existing downstream login-wall/Cloudflare handling still applies.
    """
    if not session_is_stale(source):
        return False
    prof = profile_for_source(
        getattr(source, "domain", "") or getattr(source, "base_url", ""),
        getattr(source, "profile", None),
    )
    if prof and prof.auth_model == "anonymous":
        return False
    url = entry_url or getattr(source, "base_url", None)
    if not url:
        return False
    from app.services.agent_relay import agent_registry

    try:
        cookies = await agent_registry.dispatch("get_cookies", {"url": url}, timeout_s=20)
    except RuntimeError as exc:
        logger.warning(
            "Session self-heal: extension unavailable for %s: %s",
            getattr(source, "name", "?"),
            exc,
        )
        return False
    except Exception as exc:  # never let heal break the flow
        logger.warning(
            "Session self-heal dispatch failed for %s: %s",
            getattr(source, "name", "?"),
            exc,
        )
        return False
    if not cookies:
        logger.info(
            "Session self-heal: no cookies returned for %s — proceeding with stored session",
            getattr(source, "name", "?"),
        )
        return False
    try:
        filtered, expires_at = prepare_source_cookies(cookies, source)
        source.session_state = encrypt_session_state(
            json.dumps({"cookies": filtered, "origins": []})
        )
        source.captured_at = datetime.now(UTC)
        source.expires_at = expires_at
        await db.commit()
    except Exception as exc:  # a DB hiccup must not abort the search
        logger.warning(
            "Session self-heal store failed for %s: %s", getattr(source, "name", "?"), exc
        )
        return False
    logger.info(
        "Session self-heal re-captured %d cookies for %s (expires %s)",
        len(filtered),
        getattr(source, "name", "?"),
        expires_at,
    )
    return True


async def heal_source_session(source_id: str, entry_url: str | None = None) -> bool:
    """Look a source up and self-heal its session in its own DB session.

    Thin convenience wrapper for flow entry points that don't already hold a
    live Source row/session. Best-effort: never raises, returns False when no
    heal was needed or possible.
    """
    from sqlalchemy import select

    from app.db import async_session
    from app.models.orm import Source

    try:
        async with async_session() as db:
            source = (
                await db.execute(select(Source).where(Source.id == source_id))
            ).scalar_one_or_none()
            if source is None:
                return False
            return await self_heal_source_session(source, db, entry_url)
    except Exception as exc:  # must never break the search
        logger.warning("Session self-heal wrapper failed for %s: %s", source_id, exc)
        return False


async def connect_with_stored_session(
    session_state_blob: str | None,
    target_url: str,
) -> tuple[Any, Any] | None:
    """Launch a fresh Chromium with the stored session applied.

    Returns (playwright, page) if a session blob exists AND the cookies keep
    us logged in at target_url. Returns None if there's no stored session or
    the session is no longer valid (caller falls back to CDP or human).

    This lets the adapters run WITHOUT the original Brave instance — the
    whole point of capture/replay.
    """
    if not session_state_blob:
        return None
    from playwright.async_api import async_playwright

    state = json.loads(decrypt_session_state(session_state_blob))
    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.launch(headless=True, proxy=proxy_config())
        ctx = await browser.new_context()
        await ctx.add_cookies(state.get("cookies", []))
        page = await ctx.new_page()
        await page.goto(target_url, timeout=45_000, wait_until="domcontentloaded")
        await asyncio.sleep(3)
        # Verify login (page did not redirect to a login/authwall).
        url = page.url
        logged_in = "/login" not in url and "authwall" not in url
        if not logged_in:
            await pw.stop()
            return None
        return pw, page
    except Exception as exc:
        logger.warning("Stored-session connect failed: %s", exc)
        try:
            await pw.stop()
        except Exception:
            pass
        return None

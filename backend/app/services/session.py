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
from datetime import UTC, datetime
from typing import Any

from app.models.orm import BrowserSession
from app.services.encryption import decrypt_session_state, encrypt_session_state

logger = logging.getLogger(__name__)

BRAVE_CDP_URL = "http://localhost:9222"  # same default as adapters

# Which session-state we keep per domain (Playwright storage_state format).
STORAGE_STATE_DOMAINS = ["linkedin.com", "www.linkedin.com"]


async def capture_from_cdp(session: BrowserSession) -> BrowserSession:
    """Capture cookies + localStorage from the live signed-in browser (CDP).

    Connects to the running Brave instance on the debug port, reads the
    storage_state for the target domains, encrypts it, and stores on the
    session row (not yet committed by this function).
    """
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.connect_over_cdp(BRAVE_CDP_URL)
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
        browser = await pw.chromium.launch(headless=True)
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
        browser = await pw.chromium.launch(headless=True)
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

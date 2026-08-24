#!/usr/bin/env python3
"""One-time LinkedIn login in the container's persistent browser.

Logs into LinkedIn THROUGH the residential proxy and persists the session in
the container's persistent Chromium profile (/data/browser-profile). After this
runs once, the persistent browser is logged in and all searches work.

Credentials come from env (LINKEDIN_EMAIL / LINKEDIN_PASSWORD) — set as Koyeb
secrets, never in code. If they're missing, this script does nothing.

Usage (in container):
    python login_linkedin.py

Env:
    LINKEDIN_EMAIL      LinkedIn login email
    LINKEDIN_PASSWORD   LinkedIn login password
    PROXY_URL           e.g. http://127.0.0.1:1080
    PROXY_USERNAME      proxy username
    PROXY_PASSWORD      proxy password
    BRAVE_CDP_URL       in-container CDP (http://127.0.0.1:9222)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("login-linkedin")

# Paths
PROFILE_DIR = os.getenv("BROWSER_PROFILE_DIR", "/data/browser-profile")
CDP_URL = os.getenv("BRAVE_CDP_URL", "http://127.0.0.1:9222")
PROXY_URL = os.getenv("PROXY_URL", "")
PROXY_USER = os.getenv("PROXY_USERNAME", "")
PROXY_PASS = os.getenv("PROXY_PASSWORD", "")
EMAIL = os.getenv("LINKEDIN_EMAIL", "")
PASSWORD = os.getenv("LINKEDIN_PASSWORD", "")

# Marker file: exists after a successful login (so we don't re-login each boot).
MARKER = os.path.join(PROFILE_DIR, ".linkedin-logged-in")


def _proxy_cfg() -> dict | None:
    if not PROXY_URL:
        return None
    cfg = {"server": PROXY_URL}
    if PROXY_USER:
        cfg["username"] = PROXY_USER
    if PROXY_PASS:
        cfg["password"] = PROXY_PASS
    return cfg


async def _is_logged_in(page) -> bool:
    try:
        await page.goto(
            "https://www.linkedin.com/feed/",
            timeout=30_000,
            wait_until="domcontentloaded",
        )
        await asyncio.sleep(2)
        url = page.url
        title = await page.title()
        ok = "/login" not in url and "/uas/login" not in url and "authwall" not in url and "Sign in" not in title
        logger.info("Login check: url=%s title=%s -> %s", url, title[:40], ok)
        return ok
    except Exception as exc:
        logger.warning("Login check error: %s", exc)
        return False


async def _do_login() -> bool:
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    try:
        # Use a FRESH persistent-context approach: connect to the in-container
        # CDP browser (its profile is /data/browser-profile), which persists.
        browser = await pw.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0]
        page = await ctx.new_page()
        try:
            # Already logged in?
            if await _is_logged_in(page):
                logger.info("Already logged in — nothing to do")
                open(MARKER, "w").close()
                return True

            # Go to login page
            await page.goto(
                "https://www.linkedin.com/login",
                timeout=30_000,
                wait_until="domcontentloaded",
            )
            await asyncio.sleep(2)

            # Fill email
            email_sel = page.locator("#username")
            if await email_sel.count() == 0:
                email_sel = page.locator('input[name="session_key"]')
            if await email_sel.count() == 0:
                logger.warning("Login form not found (maybe layout changed or blocked)")
                return False
            await email_sel.fill(EMAIL)
            logger.info("Filled email")

            # Fill password
            pass_sel = page.locator("#password")
            if await pass_sel.count() == 0:
                pass_sel = page.locator('input[name="session_password"]')
            if await pass_sel.count() == 0:
                logger.warning("Password field not found")
                return False
            await pass_sel.fill(PASSWORD)
            logger.info("Filled password")

            # Submit
            submit = page.locator('button[type="submit"]').first
            if await submit.count() == 0:
                submit = page.locator('button:has-text("Sign in")').first
            await submit.click(timeout=10_000)
            logger.info("Clicked sign in")

            # Wait for login to complete
            for _ in range(15):
                await asyncio.sleep(2)
                url = page.url
                if "/login" not in url and "authwall" not in url and "/feed" in url:
                    logger.info("Login successful! url=%s", url)
                    open(MARKER, "w").close()
                    return True
                if "checkpoint" in url:
                    logger.warning("LinkedIn checkpoint/challenge — manual action needed")
                    return False
            logger.warning("Login did not complete within timeout. url=%s", page.url)
            return False
        finally:
            try:
                await page.close()
            except Exception:
                pass
    finally:
        try:
            await pw.stop()
        except Exception:
            pass


async def main() -> int:
    if not EMAIL or not PASSWORD:
        logger.warning("LINKEDIN_EMAIL/LINKEDIN_PASSWORD not set — skipping auto-login")
        return 0
    if os.path.exists(MARKER):
        logger.info("Already logged in (marker exists) — skipping")
        return 0
    # Wait a bit for cloudflared access tcp + CDP to be ready (entrypoint starts them).
    logger.info("Waiting for proxy + CDP to be ready...")
    for _ in range(20):
        if os.path.exists(MARKER):
            logger.info("Marker appeared (logged in elsewhere) — done")
            return 0
        ok = await _try_connect()
        if ok:
            break
        await asyncio.sleep(3)
    ok = await _do_login()
    logger.info("Auto-login result: %s", "SUCCESS" if ok else "FAILED")
    return 0 if ok else 1


async def _try_connect() -> bool:
    try:
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        try:
            await pw.chromium.connect_over_cdp(CDP_URL)
            return True
        finally:
            await pw.stop()
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

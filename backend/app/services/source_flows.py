"""Pluggable source support: guided wizard recorder, templatizer, and executor.

Design (guided wizard approach):
1. User registers a source (name + base URL) and logs in inside a wizard
   browser session. The captured storage_state is encrypted and saved on the
   Source row (shared across users — one operator session per site).
2. User demonstrates a "find jobs" / "find candidates" flow. The recorder
   captures each interaction as a raw event (action + selector + value).
3. The templatizer converts raw events into parameterized steps: literal
   search text becomes `{"param": "query"}`, pagination clicks become
   repeatable steps.
4. The executor replays templatized steps for any query, extracting
   structured results via the recorded card selectors.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import async_playwright

from app.services.encryption import decrypt_session_state
from app.services.proxy import proxy_config as _proxy_config

logger = logging.getLogger(__name__)

FLOW_TYPES = ("find_jobs", "find_candidates")


# ---------------------------------------------------------------------------
# Wizard session (live browser the user drives; we record events)
# ---------------------------------------------------------------------------


class WizardSession:
    """A HEADLESS browser running inside the container; the user drives it
    remotely through the web UI (screenshots + typed credentials).

    Cross-platform by design: nothing runs on the end user's machine —
    no tunnel, no local browser, works identically on Windows/macOS/Linux.

    The UI polls GET /wizard/screenshot for a live view and posts actions:
    - credentials (username/password + submit)
    - mfa code
    - click (by coordinates from the screenshot)
    """

    def __init__(self, source_id: str, flow_type: str, domain: str | None = None) -> None:
        self.source_id = source_id
        self.flow_type = flow_type
        self.domain = domain
        self.pw = None
        self.browser = None
        self.context = None
        self.page = None
        self.events: list[dict[str, Any]] = []
        self.last_activity = asyncio.get_event_loop().time()
        self.logged_in = False

    async def start(self, start_url: str, storage_state: dict | None = None) -> None:
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(
            headless=True, proxy=_proxy_config(),
            args=["--disable-blink-features=AutomationControlled"],
        )
        self.context = await self.browser.new_context(
            storage_state=storage_state,
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        self.page = await self.context.new_page()
        await self.page.goto(start_url, timeout=45_000, wait_until="domcontentloaded")
        await self._wait_until_rendered()
        self.touch()

    async def _wait_until_rendered(self, timeout_s: float = 20.0) -> None:
        """Wait until the site's JS app has actually rendered content.

        Many sites (React/Angular SPAs) serve a near-empty shell with a
        'Loading…' placeholder; domcontentloaded fires long before real
        content. Poll the body text until it's substantial (or timeout).
        """
        deadline = asyncio.get_event_loop().time() + timeout_s
        while asyncio.get_event_loop().time() < deadline:
            try:
                info = await self.page.evaluate(
                    """() => ({
                      textLen: (document.body?.innerText || '').length,
                      loading: /\\bloading\\b/i.test(
                        (document.body?.innerText || '').slice(0, 400)
                      ),
                      nodes: document.body?.querySelectorAll('*').length || 0,
                    })"""
                )
                # Consider it rendered when there's real text and enough DOM,
                # and it isn't showing a bare "Loading…" placeholder.
                if info.get("textLen", 0) > 200 and info.get("nodes", 0) > 150 and not info.get("loading"):
                    return
            except Exception:
                pass  # navigation in flight
            await asyncio.sleep(0.5)
        logger.info("_wait_until_rendered timed out after %ss — continuing", timeout_s)

    def touch(self) -> None:
        self.last_activity = asyncio.get_event_loop().time()

    async def age_s(self) -> float:
        return asyncio.get_event_loop().time() - self.last_activity

    # ---- UI-driven interaction ------------------------------------------

    async def screenshot(
        self,
        clip: dict[str, float] | None = None,
        scale: float | None = None,
    ) -> bytes | None:
        """Live PNG of the wizard page (optionally clipped + upscaled).

        If the page still shows a bare loading placeholder, wait briefly for
        the app to render so the preview isn't a blank frame.

        scale=2 uses the device scale factor for a sharper, phone-scannable
        shot (used for QR crops).
        """
        if self.page is None:
            return None
        try:
            # Don't capture a "Loading…" shell — give the app a moment.
            try:
                bare = await self.page.evaluate(
                    """() => {
                      const t = (document.body?.innerText || '').trim();
                      return t.length < 50 && /loading/i.test(t);
                    }"""
                )
                if bare:
                    await asyncio.wait_for(self._wait_until_rendered(10.0), timeout=12.0)
            except Exception:
                pass
            self.touch()
            return await self.page.screenshot(
                type="png",
                full_page=False,
                clip=clip,
                scale="device" if scale == 2 else "css",
            )
        except Exception as exc:
            logger.debug("screenshot failed: %s", exc)
            return None

    async def locate_qr_region(self) -> tuple[float, float, float, float] | None:
        """Find a QR code on the page and return (x, y, width, height) in
        CSS pixels, padded for scannability. None if nothing QR-like exists.

        Detection heuristics: <img>/<canvas>/div whose class/id/alt hints at
        QR, or a roughly square image near a 'scan' label.
        """
        if self.page is None:
            return None
        try:
            region = await self.page.evaluate(
                """() => {
                  const hint = /(qr|qrcode|qr-code|scanme|scan-me|barcode)/i;
                  const cands = [];
                  for (const el of document.querySelectorAll('img, canvas, div, iframe')) {
                    const r = el.getBoundingClientRect();
                    if (r.width < 80 || r.height < 80) continue;
                    const aspect = r.width / r.height;
                    if (aspect < 0.7 || aspect > 1.4) continue;  // QRs are square-ish
                    const label = [
                      el.id, el.className && String(el.className),
                      el.getAttribute('alt'), el.getAttribute('aria-label'),
                      el.getAttribute('title'),
                    ].filter(Boolean).join(' ');
                    const nearText = (el.closest('div,section')?.innerText || '').slice(0, 300);
                    const hinted = hint.test(label) || /scan/i.test(nearText);
                    if (hinted) cands.push(r);
                  }
                  if (!cands.length) return null;
                  // Largest hinted square wins.
                  cands.sort((a, b) => b.width * b.height - a.width * a.height);
                  const r = cands[0];
                  const pad = Math.max(30, r.width * 0.15);
                  return {
                    x: Math.max(0, r.x - pad),
                    y: Math.max(0, r.y - pad),
                    width: r.width + pad * 2,
                    height: r.height + pad * 2,
                  };
                }"""
            )
            if not region:
                return None
            self.touch()
            return (
                float(region["x"]),
                float(region["y"]),
                float(region["width"]),
                float(region["height"]),
            )
        except Exception as exc:
            logger.debug("locate_qr_region failed: %s", exc)
            return None

    async def status(self) -> dict[str, Any]:
        if self.page is None:
            return {"url": "", "title": "", "logged_in": self.logged_in}
        try:
            url = self.page.url
            title = await self.page.title()
            # Auto-detect completed login: the page is on the source domain
            # (or a post-login dashboard) and no longer login-shaped. Covers
            # QR-code flows where the phone does the auth.
            if not self.logged_in and url:
                low = url.lower()
                on_domain = not self.domain or self.domain in low
                login_shaped = any(
                    p in low for p in ("login", "signin", "sign-in", "log-in", "auth", "sso")
                )
                if on_domain and not login_shaped:
                    # Extra confirmation: page has meaningful content.
                    try:
                        text_len = await self.page.evaluate(
                            "() => (document.body?.innerText || '').length"
                        )
                    except Exception:
                        text_len = 0
                    if text_len > 100:
                        self.logged_in = True
                        logger.info("Wizard login auto-detected as complete (url=%s)", url[:80])
            return {"url": url, "title": title, "logged_in": self.logged_in}
        except Exception:
            return {"url": "", "title": "", "logged_in": self.logged_in}

    async def fill_credentials(self, username: str, password: str, submit: bool = True) -> dict[str, Any]:
        """Type credentials into the best-matching fields on the current page."""
        assert self.page, "wizard not started"

        async def _find(selectors: list[str]) -> str | None:
            for sel in selectors:
                try:
                    el = self.page.locator(sel).first
                    if await el.count() > 0 and await el.is_visible():
                        return sel
                except Exception as exc:
                    logger.debug("selector probe failed for %s: %s", sel, exc)
            return None

        user_sel = await _find([
            "input[type='email']",
            "input[type='text'][name*='user' i]",
            "input[type='text'][id*='user' i]",
            "input[type='text'][name*='email' i]",
            "input[type='text'][id*='email' i]",
            "input[type='text']:not([name*='search' i])",
            "input[type='tel']",
        ])
        pass_sel = await _find(["input[type='password']"])
        if not user_sel or not pass_sel:
            return {
                "ok": False,
                "reason": "No username/password fields visible on the page — "
                          "navigate to the login form first (or use click to get there).",
            }
        await self.page.fill(user_sel, username, timeout=5_000)
        await self.page.fill(pass_sel, password, timeout=5_000)
        self.touch()
        if not submit:
            return {"ok": True, "submitted": False}
        # Click a plausible submit button, else press Enter in the password box.
        submit_sel = await _find([
            "button[type='submit']",
            "input[type='submit']",
            "button:has-text('Sign in')",
            "button:has-text('Log in')",
            "button:has-text('Login')",
        ])
        if submit_sel:
            await self.page.click(submit_sel, timeout=5_000)
        else:
            await self.page.press(pass_sel, "Enter", timeout=5_000)
        await asyncio.sleep(3)
        self.touch()
        st = await self.status()
        return {"ok": True, "submitted": True, **st}

    async def submit_mfa(self, code: str) -> dict[str, Any]:
        """Submit an OTP/MFA code into the first visible short text input."""
        assert self.page, "wizard not started"
        sel = None
        for candidate in (
            "input[autocomplete='one-time-code']",
            "input[name*='otp' i]",
            "input[id*='otp' i]",
            "input[name*='code' i]",
            "input[maxlength='6']",
            "input[maxlength='4']",
        ):
            try:
                el = self.page.locator(candidate).first
                if await el.count() > 0 and await el.is_visible():
                    sel = candidate
                    break
            except Exception as exc:
                logger.debug("mfa probe failed for %s: %s", candidate, exc)
                continue
        if not sel:
            return {"ok": False, "reason": "No MFA/OTP input found on the page"}
        await self.page.fill(sel, code, timeout=5_000)
        submit_sel = None
        for candidate in ("button[type='submit']", "button:has-text('Verify')", "button:has-text('Submit')"):
            try:
                el = self.page.locator(candidate).first
                if await el.count() > 0 and await el.is_visible():
                    submit_sel = candidate
                    break
            except Exception as exc:
                logger.debug("mfa submit probe failed: %s", exc)
                continue
        if submit_sel:
            await self.page.click(submit_sel, timeout=5_000)
        else:
            await self.page.press(sel, "Enter", timeout=5_000)
        await asyncio.sleep(3)
        self.touch()
        st = await self.status()
        return {"ok": True, **st}

    async def click_at(self, x: int, y: int) -> dict[str, Any]:
        """Click the page at screenshot coordinates (scaled to viewport)."""
        assert self.page, "wizard not started"
        vp = self.page.viewport_size or {"width": 1280, "height": 900}
        await self.page.mouse.click(x, y)
        await asyncio.sleep(1.5)
        self.touch()
        return {"ok": True, "x": x, "y": y, "viewport": vp}

    async def mark_logged_in(self) -> None:
        self.logged_in = True
        self.touch()

    async def capture_state(self) -> dict[str, Any]:
        """Grab cookies + final URL + page title snapshot for saving."""
        assert self.context and self.page
        state = await self.context.storage_state()
        if self.domain:
            state["cookies"] = [
                c for c in state.get("cookies", [])
                if self.domain in (c.get("domain") or "")
            ]
            state.setdefault("origins", [])
        return {"storage_state": state, "url": self.page.url, "title": await self.page.title()}

    async def close(self) -> None:
        try:
            if self.context:
                await self.context.close()
        except Exception:
            pass
        try:
            if self.browser:
                await self.browser.close()
        except Exception:
            pass
        try:
            if self.pw:
                await self.pw.stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Templatizer: raw events -> parameterized steps
# ---------------------------------------------------------------------------

# Filler verbs whose literal value should become the query parameter.
_FILL_ACTIONS = ("fill",)


def templatize(
    events: list[dict[str, Any]], query_hint: str | None = None
) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
    """Convert raw wizard events into (steps, card_selectors).

    mark_card events are pulled out of the stream and become card_selectors:
    {"card": "<selector>", "fields": {"title": "...", "company": "..."}}.
    Field selectors default to the card itself (innerText as title); they can
    be refined later via the flow-edit API.
    """
    """Convert raw wizard events into parameterized flow steps.

    Rules:
    - The first 'fill' whose value resembles the demonstrated query (or the
      first fill into a text/search input) becomes {"param": "query"}.
    - 'submit' events collapse into a click on the submit control.
    - A click on common pagination controls (text next/›/more/more/2nd page)
      becomes a repeatable pagination step executed at the end.
    - Navigation is implicit: the executor starts at source.base_url.
    """
    if not events:
        return [], None

    steps: list[dict[str, Any]] = []
    card_selector: str | None = None
    pagination_step: dict[str, Any] | None = None
    query_assigned = False
    hint = (query_hint or "").strip().lower()

    def _looks_like_pagination(ev: dict[str, Any]) -> bool:
        text = (ev.get("text") or "").strip().lower()
        sel = (ev.get("selector") or "").lower()
        keywords = ("next", "»", "›", ">", "more", "load")
        return any(k in text for k in keywords) or "next" in sel or "pagination" in sel

    for ev in events:
        action = ev.get("action")
        if action == "fill":
            value = ev.get("value") or ""
            is_query_param = not query_assigned and bool(hint) and value.strip().lower() == hint
            steps.append(
                {
                    "action": "fill",
                    "selector": ev["selector"],
                    **({"param": "query"} if is_query_param else {"value": value}),
                }
            )
            if is_query_param:
                query_assigned = True
        elif action == "mark_card":
            if card_selector is None:
                card_selector = ev.get("selector")
        elif action == "submit":
            steps.append({"action": "press", "selector": ev["selector"], "key": "Enter"})
        elif action == "click":
            if _looks_like_pagination(ev):
                pagination_step = {
                    "action": "click",
                    "selector": ev["selector"],
                    "repeat": "paginate",
                }
                continue  # pagination runs after extraction, not inline
            steps.append({"action": "click", "selector": ev["selector"]})

    if pagination_step:
        steps.append(pagination_step)

    # Fallback: if nothing was bound to the query param, use the last fill.
    if not query_assigned:
        for step in reversed(steps):
            if step.get("action") == "fill":
                step.pop("value", None)
                step["param"] = "query"
                break

    card_selectors = (
        {"card": card_selector, "fields": {"title": "", "url": ""}}
        if card_selector
        else None
    )
    return steps, card_selectors


# ---------------------------------------------------------------------------
# Executor: run a templatized flow for a query
# ---------------------------------------------------------------------------

MAX_PAGES = 5
PAGE_WAIT_S = 2.5

# Common login-page signals. If the flow lands on one of these, the saved
# session has expired and a human must re-login via the wizard.
_LOGIN_URL_PATTERNS = ("login", "signin", "sign-in", "log-in", "auth", "sso")
_LOGIN_TEXT_PATTERNS = ("sign in", "log in", "login to", "sign up")


async def _looks_logged_out(page: Any, base_domain: str) -> str | None:
    """Return a human_reason if the page looks like a login wall, else None."""
    try:
        url = page.url.lower()
        title = (await page.title()).lower()
    except Exception:
        return None
    if base_domain not in url:
        # Redirected off the source site — often an SSO/login redirect.
        return f"Redirected away from {base_domain} to {url[:120]} — session likely expired"
    if any(p in url for p in _LOGIN_URL_PATTERNS):
        return f"Session expired: {base_domain} redirected to a login page"
    if any(p in title for p in _LOGIN_TEXT_PATTERNS):
        return f"Session expired: page title is '{title[:80]}'"
    # Body-text check: a "Log in" BUTTON in the header is normal even for
    # signed-in users (MCF shows one always). Only treat it as a login wall
    # when the page looks like an actual login FORM: a visible password
    # field, or a short page dominated by login wording.
    try:
        probe = await page.evaluate(
            """() => {
              const t = (document.body?.innerText || '').trim();
              const pw = document.querySelector("input[type='password']");
              const pwVisible = !!(pw && (pw.offsetWidth || pw.offsetHeight));
              return { len: t.length, pwVisible,
                       head: t.slice(0, 400).toLowerCase() };
            }"""
        )
        if probe.get("pwVisible"):
            return f"Session expired: {base_domain} is showing a login form"
        if probe.get("len", 0) < 400 and any(
            p in probe.get("head", "") for p in _LOGIN_TEXT_PATTERNS
        ):
            return f"Session expired: {base_domain} is showing a sign-in prompt"
    except Exception as exc:
        logger.debug("login-probe failed: %s", exc)
    return None


async def execute_flow(
    base_url: str,
    steps: list[dict[str, Any]],
    query: str,
    storage_state_encrypted: str | None = None,
    card_selectors: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Replay a templatized flow and extract results.

    Returns {"results": [...], "needs_human": bool, "human_reason": str|None}.
    If the site bounces us to a login page (expired cookies), the reason
    says so explicitly so the UI can prompt a re-login.
    """
    state = None
    if storage_state_encrypted:
        try:
            state = json.loads(decrypt_session_state(storage_state_encrypted))
        except Exception as exc:
            logger.warning("Could not decrypt source session state: %s", exc)

    base_domain = domain_of(base_url)
    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.launch(headless=True, proxy=_proxy_config())
        ctx = await browser.new_context(storage_state=state)
        page = await ctx.new_page()

        # Step 0: implicit navigation to the source.
        try:
            await page.goto(base_url, timeout=45_000, wait_until="domcontentloaded")
        except Exception as exc:
            return {
                "results": [],
                "needs_human": True,
                "human_reason": f"Could not reach {base_url}: {exc}",
            }

        # Session check right after landing.
        logged_out = await _looks_logged_out(page, base_domain)
        if logged_out:
            return {"results": [], "needs_human": True, "human_reason": logged_out}

        error: str | None = None
        for step in steps:
            action = step.get("action")
            try:
                if action == "fill":
                    value = query if step.get("param") == "query" else step.get("value", "")
                    await page.fill(step["selector"], value, timeout=10_000)
                elif action == "click":
                    await page.click(step["selector"], timeout=10_000)
                    await asyncio.sleep(PAGE_WAIT_S)
                elif action == "press":
                    await page.press(step["selector"], step.get("key", "Enter"), timeout=10_000)
                    await asyncio.sleep(PAGE_WAIT_S)
            except Exception as exc:
                error = f"Step failed ({action} {step.get('selector')}): {exc}"
                break

        results: list[dict[str, Any]] = []
        pag = next((s for s in steps if s.get("repeat") == "paginate"), None)
        seen_urls: set[str] = set()
        for page_num in range(MAX_PAGES):
            # Mid-flow session check: sites can bounce to login after search.
            bounced = await _looks_logged_out(page, base_domain)
            if bounced:
                return {"results": [], "needs_human": True, "human_reason": bounced}
            page_results = await _extract_page(page, card_selectors)
            new = [r for r in page_results if r.get("url") and r["url"] not in seen_urls]
            if not new:
                break
            seen_urls.update(r["url"] for r in new)
            results.extend(new)
            if pag is None:
                break
            try:
                await page.click(pag["selector"], timeout=8_000)
                await asyncio.sleep(PAGE_WAIT_S)
            except Exception:
                break  # no more pages
        if not results:
            reason = error or (
                f"No results extracted from {base_url} — if the site requires "
                "login, re-run the Login step in source setup; otherwise the "
                "recorded flow may need re-recording"
            )
            return {"results": [], "needs_human": True, "human_reason": reason}
        return {"results": results, "needs_human": False, "human_reason": None}
    finally:
        try:
            await pw.stop()
        except Exception:
            pass


async def _extract_page(page: Any, card_selectors: dict[str, str] | None) -> list[dict[str, Any]]:
    card = (card_selectors or {}).get("card")
    if not card:
        return []
    fields = (card_selectors or {}).get("fields", {})
    try:
        return await page.evaluate(
            """([card, fields]) => {
              return Array.from(document.querySelectorAll(card)).map(el => {
                const get = (sel) => sel ? (el.querySelector(sel)?.innerText || '').trim() : '';
                const out = {};
                for (const [key, sel] of Object.entries(fields)) out[key] = get(sel);
                const link = el.matches('a') ? el : el.querySelector('a');
                out.url = link ? link.href : '';
                // Fallbacks when field selectors are missing/empty (LLM only
                // discovered the card, not inner fields): derive a title from
                // the first heading or the card's leading text.
                if (!out.title) {
                  const h = el.querySelector('h1,h2,h3,h4,[class*="title" i],[class*="job" i] ');
                  out.title = (h?.innerText || el.innerText || '').trim().split('\\n')[0].slice(0, 200);
                }
                out.title = out.title || '';
                return out;
              });
            }""",
            [card, fields],
        )
    except Exception as exc:
        logger.warning("Card extraction failed: %s", exc)
        return []


def domain_of(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    return netloc.removeprefix("www.")


# ---------------------------------------------------------------------------
# LLM auto-record: discover a site's search flow without a human recording
# ---------------------------------------------------------------------------

# Runs in the page: score candidate "result card" elements. Card-like =
# reasonably sized, contains links, and is a REPEATED sibling of the same tag
# (>=2 — strict >=3 missed LinkedIn's job cards whose parent mixes tags).
_CARD_DISCOVERY_JS = """() => {
  const scored = [];
  for (const el of document.querySelectorAll('article, li, div, section')) {
    const links = el.querySelectorAll('a');
    if (links.length === 0) continue;
    const textLen = (el.innerText || '').length;
    if (textLen < 40) continue;  // too empty to be a listing card
    const rect = el.getBoundingClientRect();
    if (rect.width < 150 || rect.height < 40) continue;
    const siblings = el.parentElement
      ? Array.from(el.parentElement.children).filter(
          c => c.tagName === el.tagName
        ).length
      : 1;
    if (siblings >= 2) {
      scored.push({
        tag: el.tagName.toLowerCase(),
        cls: (el.className && typeof el.className === 'string') ? el.className.slice(0, 120) : '',
        id: el.id || '',
        siblings,
        textLen,
        links: links.length,
      });
    }
  }
  // Prefer the most content-rich repeated container
  scored.sort((a, b) => b.textLen - a.textLen);
  return scored.slice(0, 15);
}"""


async def discover_flow(
    base_url: str,
    query: str,
    flow_type: str,
    session: WizardSession | None = None,
) -> dict[str, Any]:
    """Automatically discover a site's search flow using the LLM.

    Drives a headless browser: loads the site, asks the LLM to identify the
    search input from the DOM, fills it, submits, then asks the LLM to
    identify the result-card structure. Returns templatized steps.

    Raises RuntimeError with a user-friendly message on failure.
    """
    import re

    from app.services.llm import LLMService

    llm = LLMService()
    if not llm.enabled:
        raise RuntimeError("LLM is not enabled (LLM_ENABLED/LLM_API_KEY) — auto-record unavailable")

    own_browser = session is None
    if own_browser:
        session = WizardSession("discover", flow_type)
        await session.start(base_url)
    page = session.page
    assert page is not None

    try:
        # --- Step 1: identify the search input -----------------------------
        inputs = await page.evaluate(
            """() => Array.from(document.querySelectorAll('input, textarea')).map((el, i) => ({
              index: i,
              tag: el.tagName.toLowerCase(),
              type: el.type || '',
              id: el.id || '',
              name: el.name || '',
              placeholder: el.placeholder || '',
              ariaLabel: el.getAttribute('aria-label') || '',
              visible: !!(el.offsetWidth || el.offsetHeight),
            }))"""
        )
        # Renumber so the LLM's array position == the "index" it should reply
        # with. (Filtering by visibility keeps original DOM indices, which do
        # NOT line up with the list the LLM actually sees — LinkedIn has hidden
        # inputs that shifted every position and broke the lookup.)
        visible_inputs = [
            {**i, "index": pos} for pos, i in enumerate(inputs) if i.get("visible")
        ]

        raw_json = await llm.chat(
            "You are a web-automation expert. Given a list of form inputs from a "
            "job/candidate listing website, pick the one that is the MAIN SEARCH BOX "
            f"for searching {'jobs' if flow_type == 'find_jobs' else 'candidates'}. "
            "Reply with ONLY a JSON object: {\"index\": <number>}. No other text.",
            json.dumps(visible_inputs, indent=1),
        )
        if not raw_json:
            raise RuntimeError("LLM did not respond")
        m = re.search(r"\{[^}]*\}", raw_json, re.DOTALL)
        if not m:
            raise RuntimeError(f"Could not parse LLM response: {raw_json[:120]}")
        idx = json.loads(m.group(0)).get("index")
        chosen = next((i for i in visible_inputs if i["index"] == idx), None)
        if not chosen:
            # Fallback: first visible text/search input (skip email/password/
            # hidden-ish boxes) so one bad LLM answer doesn't kill recording.
            candidates_inputs = [
                i for i in visible_inputs
                if i.get("type") in ("", "text", "search")
            ]
            chosen = candidates_inputs[0] if candidates_inputs else None
        if not chosen:
            raise RuntimeError(
                "No usable search box found on the page — the site may not "
                "have loaded, or requires login first."
            )

        sel = (
            f"#{chosen['id']}" if chosen["id"]
            else f"{chosen['tag']}[name=\"{chosen['name']}\"]" if chosen["name"]
            else chosen["tag"]
        )

        # --- Step 2: fill + submit ----------------------------------------
        await page.fill(sel, query, timeout=10_000)
        await page.keyboard.press("Enter")
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=15_000)
        except Exception:
            pass
        cards = []
        for _ in range(10):  # up to ~30s
            cards = await page.evaluate(_CARD_DISCOVERY_JS)
            if cards:
                break
            await asyncio.sleep(3)

        # --- Step 3: identify result cards ---------------------------------
        candidates = cards
        if not candidates:
            # Include page state to make remote failures diagnosable.
            page_state = await page.evaluate(
                """() => ({
                  url: location.href.slice(0, 150),
                  title: document.title.slice(0, 80),
                  bodyChars: (document.body?.innerText || '').length,
                  loginHint: /sign in|log in|authwall/i.test(
                    document.body?.innerText?.slice(0, 3000) || ''
                  ),
                })"""
            )
            raise RuntimeError(
                "Could not find repeated result cards on the results page — "
                f"the site may not have loaded results, or requires login. "
                f"Page: {page_state.get('url')} | title='{page_state.get('title')}' | "
                f"textChars={page_state.get('bodyChars')} | "
                f"loginWall={page_state.get('loginHint')}"
            )

        raw_json = await llm.chat(
            "You are a web-automation expert. Below are DOM element summaries from a "
            f"{'job' if flow_type == 'find_jobs' else 'candidate'} search results page. "
            "Pick the element that represents ONE search-result card (the repeated "
            "item containing title/company/link). Reply with ONLY: "
            "{\"index\": <number>, \"css_selector\": \"<css selector matching one card>\"}. "
            "Build the css_selector from the tag + class (use a class, or an id if "
            "unique). No other text.",
            json.dumps(candidates, indent=1),
        )
        if not raw_json:
            raise RuntimeError("LLM did not respond for card selection")
        m = re.search(r"\{.*\}", raw_json, re.DOTALL)
        if not m:
            raise RuntimeError(f"Could not parse LLM card response: {raw_json[:120]}")
        parsed = json.loads(m.group(0))
        card = parsed.get("css_selector") or (
            f"{candidates[0]['tag']}.{candidates[0]['cls'].split()[0]}"
            if candidates[0].get("cls")
            else candidates[0]["tag"]
        )
        # Generalize indexed ids: #job-card-2 / div#job-card-2 are one card of
        # many (job-card-0..N). Convert to a prefix attribute selector so all
        # sibling cards match.
        m = re.fullmatch(r"(\w*)#([a-zA-Z_-]+)-\d+", card)
        if m:
            tag, idbase = m.groups()
            card = f"{tag}[id^='{idbase}-']" if tag else f"[id^='{idbase}-']"
            logger.info("generalized card id selector to: %s", card)
        # sanity: selector must match something
        try:
            count = await page.locator(card).count()
        except Exception:
            count = 0
        if not count:
            # fall back to tag + first class of top-scored element
            top = candidates[0]
            card = f"{top['tag']}.{top['cls'].split()[0]}" if top.get("cls") else top["tag"]
            count = await page.locator(card).count()
        if not count:
            raise RuntimeError("LLM-selected card selector matched nothing on the page")

        # --- Step 4: discover inner field selectors (title/company/etc.) ---
        # Grab one real card's inner structure for the LLM to map fields.
        card_fields: dict[str, str] = {}
        try:
            sample = await page.evaluate(
                """(card) => {
                  const el = document.querySelector(card);
                  if (!el) return null;
                  const describe = (root) => Array.from(root.querySelectorAll('*')).slice(0, 40).map((n, i) => ({
                    i,
                    tag: n.tagName.toLowerCase(),
                    cls: (n.className && typeof n.className === 'string') ? n.className.slice(0, 80) : '',
                    text: (n.children.length === 0 ? (n.innerText || '').trim().slice(0, 120) : ''),
                  }));
                  const link = el.matches('a') ? el : el.querySelector('a');
                  return { desc: describe(el), href: link ? link.href : '' };
                }""",
                card,
            )
        except Exception:
            sample = None

        if sample and sample.get("desc"):
            fields_for_llm = [
                "title" if flow_type == "find_jobs" else "name",
                "company",
                "location",
                "salary",
            ]
            raw_json = await llm.chat(
                "You are a web-automation expert. Below is the DOM structure of ONE "
                f"{'job' if flow_type == 'find_jobs' else 'candidate'} result card. "
                "For each field I need, give a CSS selector (relative to the card element) "
                "that extracts its text. Use tag+class of the deepest matching node. "
                "If a field isn't present use null. Reply with ONLY JSON: "
                '{"selectors": {"title": "...", "company": "...", "location": "...", "salary": null}}. '
                "Fields I need: " + ", ".join(fields_for_llm) + ". No other text.",
                json.dumps(sample["desc"], indent=1),
            )
            if raw_json:
                m = re.search(r"\{.*\}", raw_json, re.DOTALL)
                if m:
                    try:
                        parsed_fields = json.loads(m.group(0)).get("selectors", {})
                        for key, field_sel in parsed_fields.items():
                            if field_sel and isinstance(field_sel, str):
                                # sanity: selector must match inside the card
                                try:
                                    ok = await page.locator(f"{card} {field_sel}").first.count()
                                except Exception:
                                    ok = 0
                                if ok:
                                    card_fields[key] = field_sel
                    except Exception as exc:
                        logger.debug("field-selector parse failed: %s", exc)
        logger.info("discover_flow field selectors: %s", card_fields or "none")

        steps = [
            {"action": "fill", "selector": sel, "param": "query"},
            {"action": "press", "selector": sel, "key": "Enter"},
        ]
        fields = {"title": "", "url": ""}
        fields.update(card_fields)
        return {
            "steps": steps,
            "card": card,
            "fields": fields,
            "raw": [{"discovered": True, "url": page.url}],
        }
    finally:
        if own_browser and session:
            await session.close()

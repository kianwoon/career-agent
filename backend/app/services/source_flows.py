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
    """A headed browser the user drives manually; events are recorded.

    Recording strategy: inject an init script that listens to capture-phase
    click/change/submit events and records them into window.__cbEvents;
    the backend polls that buffer. Password fields are never recorded.
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
        self._cdp = False

    _INIT_SCRIPT = """
    () => {
      window.__cbEvents = [];
      const sel = (el) => {
        if (el.id) return '#' + CSS.escape(el.id);
        if (el.name) return `${el.tagName.toLowerCase()}[name="${el.name}"]`;
        const parts = [];
        let node = el;
        while (node && node !== document.body && parts.length < 4) {
          let part = node.tagName.toLowerCase();
          if (node.id) { parts.unshift('#' + CSS.escape(node.id)); break; }
          const parent = node.parentElement;
          if (parent) {
            const same = Array.from(parent.children).filter(c => c.tagName === node.tagName);
            if (same.length > 1) part += `:nth-of-type(${same.indexOf(node) + 1})`;
          }
          parts.unshift(part);
          node = parent;
        }
        return parts.join(' > ');
      };
      document.addEventListener('click', (e) => {
        if (e.altKey) {
          const card = e.target.closest('[class]');
          window.__cbEvents.push({action: 'mark_card', selector: sel(card), url: location.href});
          e.preventDefault(); e.stopPropagation();
          return;
        }
        const el = e.target.closest('a,button,[role=button],input[type=submit]') || e.target;
        window.__cbEvents.push({action: 'click', selector: sel(el), text: (el.innerText || el.value || '').slice(0, 80), url: location.href});
      }, true);
      document.addEventListener('change', (e) => {
        const el = e.target;
        if (el.type === 'password') return;
        window.__cbEvents.push({action: 'fill', selector: sel(el), value: String(el.value || '').slice(0, 200), url: location.href});
      }, true);
      document.addEventListener('submit', (e) => {
        window.__cbEvents.push({action: 'submit', selector: sel(e.target), url: location.href});
      }, true);
    }
    """

    async def start(self, start_url: str, storage_state: dict | None = None) -> None:
        from app.services.session import BRAVE_CDP_URL, _cdp_headers

        self.pw = await async_playwright().start()
        self._cdp = False
        try:
            # Preferred: drive the user's visible browser via CDP (works
            # in a headless container — the browser runs on the user's Mac).
            browser = await self.pw.chromium.connect_over_cdp(
                BRAVE_CDP_URL, headers=_cdp_headers()
            )
            self._cdp = True
        except Exception as exc:
            logger.warning("CDP connect failed (%s); falling back to headed launch", exc)
            browser = await self.pw.chromium.launch(
                headless=False, proxy=_proxy_config()
            )
        self.browser = browser
        # CDP: reuse the default context (real profile — already logged in).
        self.context = (
            browser.contexts[0]
            if self._cdp and browser.contexts
            else await browser.new_context(storage_state=storage_state)
        )
        await self.context.add_init_script(self._INIT_SCRIPT)
        self.page = await self.context.new_page()
        await self.page.goto(start_url, timeout=45_000, wait_until="domcontentloaded")

    async def drain_events(self) -> list[dict[str, Any]]:
        if self.page is None:
            return []
        try:
            fresh = await self.page.evaluate("() => window.__cbEvents || []")
            await self.page.evaluate("() => { window.__cbEvents = []; }")
        except Exception as exc:  # page navigating — retry next poll
            logger.debug("drain_events failed: %s", exc)
            return []
        if fresh:
            self.events.extend(fresh)
        return fresh

    async def capture_state(self) -> dict[str, Any]:
        """Grab cookies + final URL + page title snapshot for verification.

        On CDP the default context holds the user's whole profile, so filter
        cookies to the wizard's source domain.
        """
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
        # Close only our tab; never the user's browser.
        try:
            if self.page:
                await self.page.close()
        except Exception:
            pass
        if not self._cdp:
            try:
                if self.context:
                    await self.context.close()
            except Exception:
                pass
        try:
            if self.browser:
                await self.browser.close()  # CDP: disconnects only
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
    try:
        body = (await page.evaluate("() => document.body?.innerText?.slice(0, 2000) || ''")).lower()
        if any(p in body[:600] for p in _LOGIN_TEXT_PATTERNS):
            return f"Session expired: {base_domain} is showing a sign-in prompt"
    except Exception:
        pass
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

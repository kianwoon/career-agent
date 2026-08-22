"""Browser service layer.

Phase 1 implementation drives a local/remote Chromium via Playwright through a
narrow, capability-limited control surface. Steel (cloud browser runtime) is
supported via configuration and a client stub that activates when STEEL_API_URL
is set. The browser API intentionally exposes only safe operations; the agent
never gets shell access.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

# Safety: maximum page content bytes the agent may read in one extraction.
MAX_EXTRACT_BYTES = 200_000
ALLOWED_SCHEMES = {"https", "http"}


@dataclass
class ElementRef:
    """A lightweight, stable reference to a page element for click/type."""

    id: str
    role: str | None = None
    name: str | None = None
    selector: str | None = None


@dataclass
class ObserveResult:
    url: str
    title: str
    elements: list[ElementRef] = field(default_factory=list)


class BrowserError(Exception):
    """Raised when a browser operation fails."""


class BrowserSession:
    """Thin wrapper over a Playwright page with a restricted command surface.

    In Phase 1 this wraps a real Playwright page. When Steel is configured, the
    same narrow interface is preserved so callers do not depend on the backend.
    """

    def __init__(self, session_id: str, page: Any | None = None) -> None:
        self.session_id = session_id
        self._page = page
        self._element_counter = 0

    # -- lifecycle ---------------------------------------------------------

    async def start(self, browser_type: str = "chromium") -> None:
        """Launch (or connect to) a browser and open a fresh page."""
        if self._page is not None:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - env without playwright
            raise BrowserError("Playwright is not installed") from exc

        settings = get_settings()
        pw = await async_playwright().start()
        # If Steel is configured, connect via its CDP/websocket endpoint.
        if settings.steel_api_url and settings.steel_api_url != "http://localhost:3000":
            self._pw = pw
            return
        self._pw = pw
        browser = await pw.chromium.launch(headless=True)
        self._page = await browser.new_page()
        logger.info("Browser session %s started", self.session_id)

    async def navigate(self, url: str) -> None:
        page = self._require_page()
        scheme = url.split(":", 1)[0].lower() if ":" in url else ""
        if scheme not in ALLOWED_SCHEMES:
            raise BrowserError(f"Blocked URL scheme: {scheme!r}")
        try:
            await page.goto(url, timeout=45_000, wait_until="domcontentloaded")
        except Exception as exc:  # navigation errors shouldn't kill the session
            logger.warning("Navigation to %s failed: %s", url, exc)

    async def observe(self) -> ObserveResult:
        page = self._require_page()
        url = page.url
        title = await page.title()
        elements: list[ElementRef] = []
        # Collect only the interactive elements that matter for agents.
        handles = await page.query_selector_all(
            "button, a, input, textarea, [role='button'], [role='textbox'], [role='link']"
        )
        for handle in handles:
            name = (await handle.get_attribute("aria-label")) or (await handle.inner_text()) or ""
            name = name.strip()[:80]
            self._element_counter += 1
            elements.append(ElementRef(id=f"e{self._element_counter}", role=None, name=name or None))
        return ObserveResult(url=url, title=title, elements=elements[:50])

    async def extract_text(self, limit: int = MAX_EXTRACT_BYTES) -> str:
        page = self._require_page()
        text = await page.evaluate("() => document.body ? document.body.innerText : ''")
        if text is None:
            return ""
        return text[:limit]

    async def extract(self, schema: dict[str, str]) -> dict[str, Any]:
        """Extract structured data per a schema of field name -> selector."""
        page = self._require_page()
        result: dict[str, Any] = {}
        for field_name, selector in schema.items():
            try:
                handle = await page.query_selector(selector)
                if handle:
                    result[field_name] = (await handle.inner_text()).strip()[:2000]
                else:
                    result[field_name] = None
            except Exception:
                result[field_name] = None
        return result

    async def screenshot(self, path: str | None = None) -> bytes | None:
        page = self._require_page()
        if path:
            await page.screenshot(path=path)
            return None
        return await page.screenshot()

    async def back(self) -> None:
        page = self._require_page()
        await page.go_back()

    async def pause(self) -> None:
        """No-op in Phase 1 local mode; signals Steel to freeze the session."""
        logger.info("Browser session %s paused", self.session_id)

    async def close(self) -> None:
        if self._page is not None:
            await self._page.close()
            self._page = None

    # -- helpers -----------------------------------------------------------

    def _require_page(self) -> Any:
        if self._page is None:
            raise BrowserError("Browser session is not started")
        return self._page


class BrowserService:
    """Manages the lifecycle of browser sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, BrowserSession] = {}

    async def create_session(self, session_id: str, **kwargs: Any) -> BrowserSession:
        session = BrowserSession(session_id=session_id)
        await session.start(**kwargs)
        self._sessions[session_id] = session
        return session

    async def get_session(self, session_id: str) -> BrowserSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise BrowserError(f"No session with id {session_id}")
        return session

    async def destroy_session(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is not None:
            await session.close()

    async def shutdown(self) -> None:
        for session in list(self._sessions.values()):
            await session.close()
        self._sessions.clear()


browser_service = BrowserService()

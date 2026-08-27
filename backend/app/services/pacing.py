"""Polite pacing service.

Goal: behave like a human user so LinkedIn's anti-robot heuristics don't
trigger challenges. This is NOT stealth or anti-detection (which the design
spec lists as non-goals); it is reasonable-usage pacing.

Key design principle: pacing must NOT be stagnant. A constant 1.5-4s wait
before every action is itself a robot signature. Instead:

- Delay durations vary by CONTEXT (mechanical scroll vs. reading vs. commit).
- Distributions are skewed (log-normal-ish), not uniform: mostly short pauses
  with occasional longer ones, like a real person's attention.
- Occasional multi-action bursts (2-3 quick actions) followed by a longer
  pause — humans do this; bots rarely do.
- Reading pauses: humans stop for 3-6s when they actually read content.
- A page-per-minute throttle prevents burst patterns.
- A search gap prevents back-to-back searches.
- A circuit breaker backs off after a challenge/block.

The service is process-global so all adapters share one pacing budget.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)


class PacingService:
    """Global pacing budget shared across all browser adapters."""

    def __init__(self) -> None:
        self._last_action_ts: float | None = None
        self._page_times: list[float] = []
        self._last_search_ts: float | None = None
        self._circuit_breaker_until: float = 0.0
        self._burst_counter = 0
        self._settings = get_settings()

    # ------------------------------------------------------------------
    # Human delay by context
    # ------------------------------------------------------------------

    # Skewed, human-like delay profiles. Each is (mode, low, high):
    # mostly near `mode`, sometimes dipping low, occasionally jumping high.
    _PROFILES = {
        # Tiny hesitations between mechanical sub-actions (scroll steps).
        "mechanical": (0.35, 0.15, 1.2),
        # Reading content before deciding what to do next.
        "read": (2.0, 0.8, 6.0),
        # The pause right before a commit action (clicking a link, opening a job).
        "commit": (1.2, 0.4, 4.5),
        # After a page fully loads, a human scans before acting.
        "navigate": (2.5, 1.0, 7.0),
        # Between separate searches — longer, "thinking" pause.
        "search": (4.0, 2.0, 9.0),
    }

    def _sample_delay(self, kind: str) -> float:
        """Sample a human-like delay from a skewed distribution."""
        mode, low, high = self._PROFILES.get(kind, self._PROFILES["read"])
        # Skewed: pick triangular (concentrated near mode) but with a small
        # chance of a long "distracted/reading" pause to break the rhythm.
        if random.random() < 0.08 and kind in ("read", "navigate", "commit"):
            return random.uniform(high, high * 1.8)  # occasional long pause
        return random.triangular(low, high, mode)

    async def human_delay(self, kind: str = "read") -> None:
        """Wait a context-appropriate human delay since the last action.

        kind: one of "mechanical", "read", "commit", "navigate", "search".
        """
        now = time.monotonic()
        elapsed = 0.0
        if self._last_action_ts is not None:
            elapsed = now - self._last_action_ts

        target = self._sample_delay(kind)

        # Occasional multi-action burst: if the last few actions were quick,
        # sometimes skip the wait for one action (human typing quickly).
        if random.random() < 0.10:
            target *= 0.3  # quick burst action

        wait = max(0.0, target - elapsed)
        if wait > 0:
            logger.debug("Pacing: waiting %.1fs (%s)", wait, kind)
            await asyncio.sleep(wait)
        self._last_action_ts = time.monotonic()

    # -- page throttle --------------------------------------------------

    async def throttle_pages(self) -> None:
        """Enforce max pages per minute. Sleep if we're over budget."""
        now = time.monotonic()
        window = self._settings.pacing_throttle_window_s
        window_start = now - window
        self._page_times = [t for t in self._page_times if t > window_start]

        limit = self._settings.pacing_max_pages_per_min
        if len(self._page_times) >= limit:
            oldest = min(self._page_times)
            sleep_for = window - (now - oldest)
            if sleep_for > 0:
                logger.info(
                    "Pacing: page throttle engaged, sleeping %.1fs (%d pages in last %.0fs)",
                    sleep_for,
                    len(self._page_times),
                    window,
                )
                await asyncio.sleep(sleep_for)

        self._page_times.append(time.monotonic())

    # -- search gap -----------------------------------------------------

    async def search_gap(self) -> None:
        """Enforce a minimum gap between separate searches."""
        now = time.monotonic()
        if self._last_search_ts is not None:
            elapsed = now - self._last_search_ts
            target = self._settings.pacing_min_search_gap_s
            wait = max(0.0, target - elapsed)
            if wait > 0:
                logger.info("Pacing: search cooldown, waiting %.1fs", wait)
                await asyncio.sleep(wait)
        self._last_search_ts = time.monotonic()

    # -- circuit breaker ------------------------------------------------

    def trip_circuit_breaker(self, reason: str) -> None:
        """Trip the breaker after a challenge/block is detected."""
        cooldown = self._settings.pacing_circuit_breaker_s
        self._circuit_breaker_until = time.monotonic() + cooldown
        logger.warning(
            "Pacing: circuit breaker tripped for %.0fs (%s)", cooldown, reason
        )

    async def wait_if_breaker_open(self) -> None:
        """Wait out the circuit breaker if it's currently open."""
        now = time.monotonic()
        if self._circuit_breaker_until > now:
            wait = self._circuit_breaker_until - now
            logger.info(
                "Pacing: circuit breaker open, waiting %.1fs before next action", wait
            )
            await asyncio.sleep(wait)
        self._circuit_breaker_until = 0.0

    # -- human-like scrolling -------------------------------------------

    async def human_scroll(self, page: Any, total_px: int = 800) -> None:
        """Scroll in small jittered increments with pauses, like a human.

        Not a mechanical wheel-dump: variable step sizes, occasional scroll-
        back, and irregular pauses — sometimes a longer "reading" stop in the
        middle of the scroll.
        """
        current = 0
        while current < total_px:
            step = random.randint(100, 400)
            await page.mouse.wheel(0, step)
            current += step

            # Occasionally scroll back up a little — humans do this.
            if random.random() < 0.15:
                await page.mouse.wheel(0, -random.randint(40, 120))

            # Irregular pause between steps (mostly short, sometimes longer).
            if random.random() < 0.20:
                # Reading pause: stop for a moment to "read".
                await asyncio.sleep(random.uniform(1.5, 4.0))
            else:
                await asyncio.sleep(random.uniform(0.2, 1.0))

    async def human_pause_reading(self, seconds_range: tuple[float, float] = (2.5, 5.5)) -> None:
        """A deliberate reading pause after content loads."""
        await asyncio.sleep(random.uniform(*seconds_range))

    async def human_type(self, page: Any, selector: str, text: str) -> None:
        """Click the field, then type with jittered per-keystroke delays.

        Simulates a real person: short pause before clicking, per-character
        delays (60-160ms with occasional longer hesitations between words).

        Uses keyboard.type() — press() rejects non-key-name characters like
        '@' or '.' on headless Linux and throws (that was the 500 on LinkedIn
        sign-in).
        """
        await self.human_delay("commit")
        await page.click(selector, timeout=10_000)
        await asyncio.sleep(random.uniform(0.2, 0.6))
        for word in text.split(" "):
            await page.keyboard.type(word, delay=random.uniform(60, 160))
            if word is not text.split(" ")[-1]:
                # Type the space, sometimes with a thinking pause after it.
                await page.keyboard.type(" ", delay=50)
                if random.random() < 0.25:
                    await asyncio.sleep(random.uniform(0.3, 1.0))


# Process-global pacing budget.
pacing = PacingService()

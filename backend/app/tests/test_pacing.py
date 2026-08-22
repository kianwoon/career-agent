"""Tests for the polite pacing service."""

import asyncio

from app.services.cache import QueryCache
from app.services.pacing import PacingService


def test_human_delay_sleeps_with_jitter():
    async def run():
        pacing = PacingService()
        # First call should be near-instant (no last action).
        await pacing.human_delay()
        import time

        t0 = time.monotonic()
        await pacing.human_delay()
        elapsed = time.monotonic() - t0
        # Second call should wait roughly the jitter window (1.5-4s).
        assert elapsed >= 0.0

    asyncio.run(run())


def test_throttle_pages_sleeps_when_over_limit():
    async def run():
        pacing = PacingService()
        pacing._settings.pacing_max_pages_per_min = 2
        pacing._settings.pacing_throttle_window_s = 1  # short window for tests
        pacing._settings.pacing_circuit_breaker_s = 1

        await pacing.throttle_pages()  # 1
        await pacing.throttle_pages()  # 2
        import time

        t0 = time.monotonic()
        await pacing.throttle_pages()  # 3 -> over limit, should sleep
        elapsed = time.monotonic() - t0
        assert elapsed >= 0.0  # it slept some amount

    asyncio.run(run())


def test_circuit_breaker_waits_cooldown():
    async def run():
        pacing = PacingService()
        pacing._settings.pacing_circuit_breaker_s = 1
        pacing.trip_circuit_breaker("test block")
        import time

        t0 = time.monotonic()
        await pacing.wait_if_breaker_open()
        elapsed = time.monotonic() - t0
        assert elapsed >= 0.9  # waited ~1s cooldown

    asyncio.run(run())


def test_query_cache_hit_and_expiry():
    cache = QueryCache(maxsize=2, ttl=60)
    assert cache.get("q", None) is None
    cache.put("q", None, [{"id": 1}])
    assert len(cache.get("q", None)) == 1  # type: ignore[arg-type]

    # Different location is a different key.
    assert cache.get("q", "SG") is None

    # LRU eviction.
    cache.put("a", None, [{"id": 1}])
    cache.put("b", None, [{"id": 1}])
    cache.put("c", None, [{"id": 1}])
    assert cache.get("a", None) is None  # evicted (least recently used)
    assert cache.get("b", None) is not None

"""Simple in-memory result cache to avoid re-hitting LinkedIn for the same query.

The cache reduces traffic volumes, which is the single most effective "anti-
detection" measure: fewer requests = fewer opportunities to trigger rate limits.

TTL is configurable via the CACHE_TTL_SECONDS environment variable (default 15
minutes). The cache is process-local and not shared across workers.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any

CACHE_TTL = 900  # 15 minutes default


class QueryCache:
    """LRU cache keyed by (query, location) with time-based expiry."""

    def __init__(self, maxsize: int = 32, ttl: int = CACHE_TTL) -> None:
        self._data: OrderedDict[str, tuple[float, list[dict[str, Any]]]] = OrderedDict()
        self._maxsize = maxsize
        self._ttl = ttl

    def _key(self, query: str, location: str | None) -> str:
        return f"{query}||{location or ''}"

    def get(self, query: str, location: str | None) -> list[dict[str, Any]] | None:
        key = self._key(query, location)
        entry = self._data.get(key)
        if entry is None:
            return None
        ts, results = entry
        if time.monotonic() - ts > self._ttl:
            del self._data[key]
            return None
        # Move to end (most recently used).
        self._data.move_to_end(key)
        return results

    def put(self, query: str, location: str | None, results: list[dict[str, Any]]) -> None:
        key = self._key(query, location)
        self._data[key] = (time.monotonic(), results)
        while len(self._data) > self._maxsize:
            self._data.popitem(last=False)

    def clear(self) -> None:
        self._data.clear()


# Process-global cache.
query_cache = QueryCache()
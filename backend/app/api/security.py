"""API authentication and per-key rate limiting.

API-key auth via the `X-API-Key` header. Keys are configured in the
environment (`API_KEYS="key1,key2:60"` — the optional `:N` suffix sets a
per-key requests-per-minute limit; otherwise the global default applies).

Rate limiting uses a per-key sliding-window counter held in memory. This is
fine for single-process deployments; for multi-worker, back it with Redis.

Design:
- `require_api_key` dependency: 401 if missing/invalid key, 429 if over limit.
- Public paths (health, docs) bypass auth.
- When `API_KEYS` is empty (dev), auth is disabled entirely.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import Header, HTTPException, Request, status

from app.config import get_settings

logger = logging.getLogger(__name__)


class APIKeyStore:
    """Parsed API keys with per-key rate limits."""

    def __init__(self, raw: str, default_limit: int) -> None:
        self._keys: dict[str, int] = {}
        self.default_limit = default_limit
        for item in (raw or "").split(","):
            item = item.strip()
            if not item:
                continue
            if ":" in item:
                key, _, limit = item.partition(":")
                try:
                    self._keys[key.strip()] = int(limit.strip())
                except ValueError:
                    logger.warning("Bad rate limit for key %s, using default", key.strip())
                    self._keys[key.strip()] = default_limit
            else:
                self._keys[item] = default_limit
        self.enabled = bool(self._keys)

    def limit_for(self, key: str) -> int:
        return self._keys.get(key, self.default_limit)

    def is_valid(self, key: str) -> bool:
        return key in self._keys

    def __len__(self) -> int:
        return len(self._keys)


class SlidingWindowLimiter:
    """Per-key sliding-window rate limiter (in-memory)."""

    def __init__(self) -> None:
        # key -> list of request timestamps (within the window).
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str, limit_per_min: int, window_s: float = 60.0) -> tuple[bool, int]:
        """Check if the key is within limit. Returns (allowed, retry_after_s)."""
        now = time.monotonic()
        hits = [t for t in self._hits.get(key, []) if now - t < window_s]
        if len(hits) >= limit_per_min:
            self._hits[key] = hits
            oldest = min(hits) if hits else now
            retry_after = max(1, int(window_s - (now - oldest)))
            return False, retry_after
        hits.append(now)
        self._hits[key] = hits
        return True, 0


# Process-global instances (refreshed lazily from settings).
_key_store: Optional[APIKeyStore] = None
_limiter = SlidingWindowLimiter()


def _get_key_store() -> APIKeyStore:
    global _key_store
    s = get_settings()
    if _key_store is None or _key_store.default_limit != s.api_rate_limit_per_min:
        _key_store = APIKeyStore(s.api_keys, s.api_rate_limit_per_min)
    return _key_store


async def require_api_key(
    request: Request,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> str:
    """FastAPI dependency enforcing API-key auth + rate limiting.

    Returns the validated key. Raises 401/429 on failure.
    """
    store = _get_key_store()

    # Auth disabled in dev when no keys configured.
    if not store.enabled:
        return "dev"

    # Public paths bypass auth (health, docs).
    for public in get_settings().api_public_paths:
        if request.url.path == public or request.url.path.startswith(public + "/"):
            return "public"

    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    if not store.is_valid(x_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    allowed, retry_after = _limiter.allow(x_api_key, store.limit_for(x_api_key))
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )
    return x_api_key

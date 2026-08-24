"""Proxy configuration for browser automation.

When `PROXY_URL` is set, Playwright browsers are launched with a proxy so all
browser traffic egresses through a remote IP (e.g. a residential proxy service
or a SOCKS5 tunnel to a home machine). When unset, browsers connect directly.

Env vars:
  PROXY_URL       e.g. "socks5://host:1080" or "http://host:8080"
  PROXY_USERNAME  optional
  PROXY_PASSWORD  optional

The proxy is applied to:
  - stored-session replay (fresh Chromium)
  - browser_service sessions (headless Chromium)
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def proxy_config() -> dict[str, Any] | None:
    """Return a Playwright `proxy` dict, or None if no proxy is configured.

    Playwright accepts: {"server": ..., "username": ..., "password": ...}
    """
    url = os.getenv("PROXY_URL", "").strip()
    if not url:
        return None
    cfg: dict[str, Any] = {"server": url}
    user = os.getenv("PROXY_USERNAME", "").strip()
    password = os.getenv("PROXY_PASSWORD", "").strip()
    if user:
        cfg["username"] = user
    if password:
        cfg["password"] = password
    logger.info("Browser proxy configured: %s (auth=%s)", url, bool(user))
    return cfg


def proxy_env() -> dict[str, str]:
    """Return the subset of proxy env vars (for subprocess/child launches)."""
    out = {}
    for key in ("PROXY_URL", "PROXY_USERNAME", "PROXY_PASSWORD"):
        val = os.getenv(key, "")
        if val:
            out[key] = val
    return out

"""Best-effort anonymous probe of an unknown site's auth/anti-bot shape.

When a user registers a source we have no built-in profile for, the settings
that drive later decisions (auth model, Cloudflare protection, login-wall URL
patterns) used to be unknown until a flow ran and failed. This module fetches
the landing page ONCE with a browser UA and infers a SiteProfile-shaped dict,
so the very first search already knows what it is dealing with.

It is deliberately polite: a single GET, no login-form credential attempts,
never POSTs anywhere. It never raises — a network failure degrades to the safe
"assume a login is required" default so callers can always store *something*.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

import httpx

logger = logging.getLogger(__name__)

# A real desktop browser UA so sites serve their normal HTML (not a bot block).
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Hosts/ids in a redirect chain or href that betray an SSO hand-off.
_SSO_MARKERS = ("singpass", "corppass", "openid", "sso")
# Distinctive provider names that also count when seen in the body text.
_SSO_BODY_MARKERS = ("singpass", "corppass")

# Anchors whose href contains one of these are treated as the login route.
_LOGIN_HREF = re.compile(r"""href=["']([^"']*(?:login|signin|sign-in)[^"']*)["']""", re.IGNORECASE)

_BODY_HEAD_LIMIT = 2000


async def probe_site(url: str) -> dict:
    """Fetch `url` once and return a SiteProfile-shaped dict describing it.

    Keys mirror SiteProfile field names (auth_model, cloudflare_protected,
    login_url_patterns, session_expired_markers, candidate_entry_url, notes)
    plus a "job_api" best-effort hint and "probed"/"probed_at" markers. Never
    raises; on any failure returns the safe portal default.
    """
    probed_at = datetime.now(UTC).isoformat()
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=10.0,
            headers={"User-Agent": _BROWSER_UA, "Accept": "text/html,*/*"},
        ) as client:
            resp = await client.get(url)
        return _profile_from_response(url, resp, probed_at)
    except Exception as exc:  # network/DNS/TLS — degrade safely, never raise
        logger.info("probe_site failed for %s: %s", url, exc)
        return {
            "auth_model": "portal",
            "cloudflare_protected": False,
            "login_url_patterns": [],
            "session_expired_markers": [],
            "candidate_entry_url": None,
            "job_api": None,
            "notes": f"probe failed: {exc}",
            "probed": True,
            "probed_at": probed_at,
        }


def _profile_from_response(url: str, resp: httpx.Response, probed_at: str) -> dict:
    """Infer a profile dict from a successful HTTP response."""
    headers_lc = {k.lower(): v for k, v in resp.headers.items()}
    body = (resp.text or "")[:_BODY_HEAD_LIMIT].lower()
    status = resp.status_code

    cloudflare = _detect_cloudflare(status, headers_lc, body)

    # Redirect chain (history + final) surfaces SSO hand-offs even when the
    # landing page itself looks anonymous.
    chain = [str(r.url).lower() for r in resp.history] + [str(resp.url).lower()]
    login_hrefs = [m.lower() for m in _LOGIN_HREF.findall(body)]
    has_password = 'type="password"' in body or "type='password'" in body
    login_exists = has_password or bool(login_hrefs)

    auth_model = _infer_auth_model(chain, login_hrefs, login_exists, body)

    login_patterns = _login_url_patterns(login_hrefs)
    job_api = _detect_job_api(headers_lc, body)

    notes = ""
    if cloudflare:
        notes = "probe detected a Cloudflare challenge on the landing page."
    elif auth_model == "anonymous":
        notes = "probe found no login wall on the landing page."

    return {
        "auth_model": auth_model,
        "cloudflare_protected": cloudflare,
        "login_url_patterns": login_patterns,
        "session_expired_markers": [],
        "candidate_entry_url": None,
        "job_api": job_api,
        "notes": notes,
        "probed": True,
        "probed_at": probed_at,
    }


def _detect_cloudflare(status: int, headers_lc: dict[str, str], body: str) -> bool:
    """True when a 403 carries a Cloudflare challenge signature."""
    if status != 403:
        return False
    challenge_text = any(
        m in body
        for m in ("just a moment", "checking your browser", "cf-challenge", "attention required")
    )
    cf_mitigated = "cf-mitigated" in headers_lc
    cf_server_challenge = "cloudflare" in headers_lc.get("server", "") and challenge_text
    return challenge_text or cf_mitigated or cf_server_challenge


def _infer_auth_model(
    chain: list[str], login_hrefs: list[str], login_exists: bool, body: str
) -> str:
    """anonymous | portal | sso — SSO hand-off wins over a plain login form.

    An SSO marker in the redirect chain or a login anchor's href means the site
    authenticates via SSO. In the body only the distinctive provider names
    (singpass/corppass/openid) count — a bare "sso" substring would false-positive
    on unrelated page text.
    """
    joined = " ".join(chain) + " " + " ".join(login_hrefs)
    if any(marker in joined for marker in _SSO_MARKERS) or any(
        marker in body for marker in _SSO_BODY_MARKERS
    ):
        return "sso"
    if login_exists:
        return "portal"
    return "anonymous"


def _login_url_patterns(login_hrefs: list[str]) -> list[str]:
    """Extract de-duplicated path substrings (e.g. "/site/login") from anchors."""
    patterns: list[str] = []
    for href in login_hrefs:
        path = href
        if "://" in path:
            path = "/" + path.split("://", 1)[1].split("/", 1)[-1] if "/" in path.split("://", 1)[1] else ""
        if not path.startswith("/"):
            path = "/" + path.lstrip("/")
        # Keep only the path portion for a stable substring match.
        path = path.split("?", 1)[0].split("#", 1)[0]
        if path and path not in patterns:
            patterns.append(path)
        if len(patterns) >= 10:
            break
    return patterns


def _detect_job_api(headers_lc: dict[str, str], body: str) -> str | None:
    """Best-effort hint that the site exposes a JSON API we could call directly."""
    if "application/json" in headers_lc.get("content-type", ""):
        return "public-json"
    if "/api/" in body and ("json" in body or "fetch(" in body):
        return "public-json"
    return None

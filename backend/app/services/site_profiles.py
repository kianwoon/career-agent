"""Per-site capability profiles — one registry for how each job site behaves.

Sites differ in three ways that used to be scattered as ad-hoc
`if "fastjobs.sg" in domain` branches across the session guard, login URL
resolution, block detection, and the Playwright fallback:

- **auth_model** — how a session is established and how it expires.
- **cloudflare_protected** — whether a headless server browser can even load
  the site (FastJobs presents a CF challenge; the extension in the user's real
  browser passes it, Playwright usually does not).
- **candidate_entry_url** — talent-search landing template for find_candidates
  (MCF/FastJobs split public vs. employer hosts).

This module is intentionally dependency-free (stdlib + logging only) so it can
be imported from every layer without import cycles.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SiteProfile:
    """Capability profile for one site (or family of subdomains).

    auth_model:
      - "anonymous": no login needed (public job search).
      - "portal": a cookie session on a single host (FastJobs, LinkedIn).
      - "sso": short-window Singpass/Corppass-style SSO that the employer
        portal exchanges for its own session (MCF employer, Corppass).
    """

    auth_model: str
    cloudflare_protected: bool = False
    # Extra URL substrings that indicate a login wall (checked against the
    # lowercased page URL, in addition to the global defaults).
    login_url_patterns: tuple[str, ...] = ()
    # Lowercased body-text phrases that indicate an inactivity/session expiry.
    session_expired_markers: tuple[str, ...] = ()
    # Talent-search landing URL template for find_candidates recordings.
    candidate_entry_url: str | None = None
    notes: str = ""


# Suffix → profile. Matching prefers the longest suffix so more specific hosts
# (employer.seek.com) win over their parent (seek.com).
_PROFILES: dict[str, SiteProfile] = {
    "linkedin.com": SiteProfile(
        auth_model="portal",
        cloudflare_protected=False,
        login_url_patterns=("login", "authwall"),
        session_expired_markers=("authwall",),
        notes="LinkedIn bounces anonymous/logged-out page loads to /login or /authwall.",
    ),
    "mycareersfuture.gov.sg": SiteProfile(
        auth_model="sso",
        cloudflare_protected=False,
        # MCF's employer app redirects to Singpass/Corppass or an in-app
        # session-timeout route when the exchange window lapses.
        login_url_patterns=("sign-in", "session-timeout"),
        session_expired_markers=("you were logged out after", "session-timeout"),
        candidate_entry_url="https://employer.mycareersfuture.gov.sg/talent-search",
        notes="Public site (www) is separate from the employer talent-search host.",
    ),
    "fastjobs.sg": SiteProfile(
        auth_model="portal",
        cloudflare_protected=True,
        # FastJobs renders its expiry as an in-place modal on the talent route,
        # so the "login wall" is the same page (site/login or a session-expiring
        # banner over the results).
        login_url_patterns=("site/login", "session expiring"),
        session_expired_markers=("session has expired due to inactivity", "session expiring"),
        candidate_entry_url="https://employer.fastjobs.sg/p/talent/search/?coyid=22091",
        notes="Cloudflare-protected; requires the extension's real browser session.",
    ),
    "employer.seek.com": SiteProfile(
        auth_model="portal",
        cloudflare_protected=False,
        login_url_patterns=("sign-in", "login"),
        notes="SEEK's employer talent-search host.",
    ),
    "seek.com": SiteProfile(
        auth_model="portal",
        cloudflare_protected=False,
        login_url_patterns=("sign-in", "login"),
        notes="SEEK portals (public + employer).",
    ),
    "jobstreet": SiteProfile(
        auth_model="portal",
        cloudflare_protected=False,
        login_url_patterns=("login",),
        notes="JobStreet (SEEK family) employer portal.",
    ),
}


def profile_for_source(domain_or_url: str, stored: dict | None) -> SiteProfile | None:
    """Resolve a site profile, letting a per-source override win field-wise.

    The built-in registry entry (if any) is the seed; any fields present in the
    source's stored profile (probed at registration or learned reactively)
    override it. Returns None only when BOTH are absent, so an unknown site with
    a stored profile still resolves. Partial stored dicts are tolerated — the
    SiteProfile dataclass fills the rest from its defaults.
    """
    builtin = profile_for(domain_or_url)
    if builtin is None and not stored:
        return None
    seed = _profile_to_dict(builtin) if builtin is not None else {}
    merged = merge_profile(seed, stored)
    return _profile_from_dict(merged)


def merge_profile(base: dict | None, override: dict | None) -> dict:
    """Merge two profile dicts, override winning key-wise. None-safe.

    A None on either side yields the other; neither present yields {}. Only
    keys with a non-None override value are replaced, so an override that omits
    a field (partial probe dict) never blanks the base value.
    """
    merged = dict(base or {})
    for key, value in (override or {}).items():
        if value is not None:
            merged[key] = value
    return merged


def apply_finding(stored: dict | None, finding: str, pattern: str | None = None) -> dict:
    """Fold a reactive finding into a stored profile dict.

    `finding` is one of "cloudflare", "sso", "login_wall":
      - cloudflare  → cloudflare_protected=True
      - sso         → auth_model="sso"
      - login_wall  → auth_model defaults to "portal" if unset (an existing
        "sso" is kept), and the matched `pattern` (a URL substring, if given)
        is appended to login_url_patterns (dedup, capped at 10).
    Returns a NEW dict; never mutates the input.
    """
    result = dict(stored or {})
    if finding == "cloudflare":
        result["cloudflare_protected"] = True
    elif finding == "sso":
        result["auth_model"] = "sso"
    elif finding == "login_wall":
        if not result.get("auth_model"):
            result["auth_model"] = "portal"
        if pattern:
            patterns = list(result.get("login_url_patterns") or [])
            if pattern not in patterns and len(patterns) < 10:
                patterns.append(pattern)
            result["login_url_patterns"] = patterns
    return result


def _profile_to_dict(profile: SiteProfile) -> dict:
    """Serialize a SiteProfile to a plain dict (tuples → lists for JSON)."""
    return {
        "auth_model": profile.auth_model,
        "cloudflare_protected": profile.cloudflare_protected,
        "login_url_patterns": list(profile.login_url_patterns),
        "session_expired_markers": list(profile.session_expired_markers),
        "candidate_entry_url": profile.candidate_entry_url,
        "notes": profile.notes,
    }


def _profile_from_dict(data: dict) -> SiteProfile:
    """Build a SiteProfile from a possibly-partial dict, tolerating missing keys."""
    return SiteProfile(
        auth_model=data.get("auth_model") or "portal",
        cloudflare_protected=bool(data.get("cloudflare_protected", False)),
        login_url_patterns=tuple(data.get("login_url_patterns") or ()),
        session_expired_markers=tuple(data.get("session_expired_markers") or ()),
        candidate_entry_url=data.get("candidate_entry_url"),
        notes=data.get("notes") or "",
    )


def _host_of(domain_or_url: str) -> str:
    """Return a bare lowercase host for a domain or full URL.

    Accepts "employer.fastjobs.sg", "https://employer.fastjobs.sg/p/..." and
    "fastjobs.sg/path" alike; strips a port and any userinfo. Never raises.
    """
    value = (domain_or_url or "").strip().lower()
    if "://" in value:
        try:
            return urlparse(value).hostname or ""
        except Exception:
            return ""
    # No scheme: could be "host/path" — keep only the authority part.
    return value.split("/", 1)[0].split("@")[-1].split(":", 1)[0]


def profile_for(domain_or_url: str) -> SiteProfile | None:
    """Return the profile whose suffix matches the host, longest suffix first.

    Matches "fastjobs.sg" and "employer.fastjobs.sg" to the fastjobs profile,
    but NOT "notfastjobs.sg" (suffix must fall on a dot boundary). Returns None
    for unknown sites or empty input — callers treat None as "no profile".
    """
    host = _host_of(domain_or_url)
    if not host:
        return None
    best_suffix: str | None = None
    for suffix in _PROFILES:
        if (host == suffix or host.endswith("." + suffix)) and (
            best_suffix is None or len(suffix) > len(best_suffix)
        ):
            best_suffix = suffix
    if best_suffix is None:
        return None
    return _PROFILES[best_suffix]

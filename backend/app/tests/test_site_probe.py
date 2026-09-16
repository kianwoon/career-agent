"""Tests for the site prober and per-source profile merge/apply helpers."""

import httpx

from app.services.site_probe import probe_site
from app.services.site_profiles import (
    apply_finding,
    merge_profile,
    profile_for_source,
)


def _client_factory(handler):
    """Build a probe-compatible AsyncClient whose transport is the given handler.

    Captures the real httpx.AsyncClient BEFORE the monkeypatch replaces it, so
    the factory does not recurse into itself.
    """
    real_client = httpx.AsyncClient

    def factory(**kwargs):
        kwargs.pop("transport", None)
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    return factory


# --- merge_profile ---------------------------------------------------------


def test_merge_profile_override_wins():
    assert merge_profile({"a": 1, "b": 2}, {"b": 3}) == {"a": 1, "b": 3}


def test_merge_profile_none_safe():
    assert merge_profile(None, {"b": 1}) == {"b": 1}
    assert merge_profile({"a": 1}, None) == {"a": 1}
    assert merge_profile(None, None) == {}


def test_merge_profile_partial_override_keeps_base():
    """An override that omits a key must not blank the base value."""
    out = merge_profile({"cloudflare_protected": True, "auth_model": "portal"}, {"auth_model": "sso"})
    assert out["cloudflare_protected"] is True
    assert out["auth_model"] == "sso"


def test_merge_profile_none_value_not_written():
    out = merge_profile({"auth_model": "sso"}, {"auth_model": None})
    assert out["auth_model"] == "sso"


# --- apply_finding ---------------------------------------------------------


def test_apply_finding_cloudflare():
    out = apply_finding(None, "cloudflare")
    assert out["cloudflare_protected"] is True


def test_apply_finding_sso():
    assert apply_finding(None, "sso")["auth_model"] == "sso"


def test_apply_finding_login_wall_sets_portal_and_pattern():
    out = apply_finding(None, "login_wall", pattern="/site/login")
    assert out["auth_model"] == "portal"
    assert "/site/login" in out["login_url_patterns"]


def test_apply_finding_login_wall_keeps_existing_sso():
    out = apply_finding({"auth_model": "sso"}, "login_wall", pattern="/x")
    assert out["auth_model"] == "sso"


def test_apply_finding_login_wall_dedups_and_caps():
    existing = {"login_url_patterns": [f"/p{i}" for i in range(10)]}
    out = apply_finding(existing, "login_wall", pattern="/p0")
    assert len(out["login_url_patterns"]) == 10  # dedup + cap
    out2 = apply_finding(existing, "login_wall", pattern="/new")
    assert len(out2["login_url_patterns"]) == 10  # cap holds


def test_apply_finding_does_not_mutate_input():
    original = {"auth_model": "portal"}
    apply_finding(original, "cloudflare")
    assert original == {"auth_model": "portal"}


# --- profile_for_source precedence ----------------------------------------


def test_profile_for_source_registry_only():
    prof = profile_for_source("fastjobs.sg", None)
    assert prof is not None
    assert prof.cloudflare_protected is True


def test_profile_for_source_stored_overrides_field_wise():
    """A stored dict overrides the registry field-wise; other fields inherit."""
    prof = profile_for_source("fastjobs.sg", {"cloudflare_protected": False})
    assert prof is not None
    assert prof.cloudflare_protected is False  # stored wins
    assert prof.candidate_entry_url is not None  # registry value inherited


def test_profile_for_source_both_absent_none():
    assert profile_for_source("unknown.example", None) is None


def test_profile_for_source_stored_only():
    prof = profile_for_source("unknown.example", {"auth_model": "sso"})
    assert prof is not None
    assert prof.auth_model == "sso"


# --- probe_site (mocked httpx) --------------------------------------------


async def test_probe_detects_cloudflare_403(monkeypatch):
    def handler(request):
        return httpx.Response(
            403,
            headers={"server": "cloudflare", "cf-mitigated": "challenge"},
            text="Just a moment... checking your browser",
        )

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory(handler))
    out = await probe_site("https://cf.example.com/")
    assert out["cloudflare_protected"] is True
    assert out["probed"] is True


async def test_probe_detects_portal_password_input(monkeypatch):
    def handler(request):
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text='<html><body><form><input type="password" name="pw"></form></body></html>',
        )

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory(handler))
    out = await probe_site("https://portal.example.com/")
    assert out["auth_model"] == "portal"
    assert out["cloudflare_protected"] is False


async def test_probe_detects_sso_redirect(monkeypatch):
    def handler(request):
        # The landing page hands off to Singpass via a login anchor — the
        # prober treats the SSO marker (in chain or hrefs) as auth_model="sso".
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text='<html><a href="https://www.singpass.gov.sg/auth">Login with Singpass</a></html>',
        )

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory(handler))
    out = await probe_site("https://gov.example.com/")
    assert out["auth_model"] == "sso"


async def test_probe_detects_anonymous(monkeypatch):
    def handler(request):
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html>public job listings</html>",
        )

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory(handler))
    out = await probe_site("https://anon.example.com/")
    assert out["auth_model"] == "anonymous"


async def test_probe_network_error_safe_default(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("no dns")

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory(handler))
    out = await probe_site("https://down.example.com/")
    assert out["auth_model"] == "portal"  # safe default: assume login required
    assert out["notes"].startswith("probe failed:")
    assert out["probed"] is True

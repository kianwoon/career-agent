"""Tests for the central per-site capability registry."""

from app.services.site_profiles import profile_for


def test_exact_domain_match():
    prof = profile_for("fastjobs.sg")
    assert prof is not None
    assert prof.cloudflare_protected is True
    assert prof.auth_model == "portal"


def test_subdomain_match():
    prof = profile_for("employer.fastjobs.sg")
    assert prof is not None
    assert prof.candidate_entry_url.startswith("https://employer.fastjobs.sg/")

def test_full_url_input():
    prof = profile_for("https://employer.fastjobs.sg/p/talent/search/?coyid=22091")
    assert prof is not None
    assert prof.auth_model == "portal"


def test_unknown_returns_none():
    assert profile_for("example.com") is None
    assert profile_for("") is None
    assert profile_for("notfastjobs.sg") is None  # suffix must be on a dot boundary


def test_longest_suffix_precedence():
    """employer.seek.com must win over the parent seek.com profile."""
    from app.services.site_profiles import _PROFILES

    sub = profile_for("employer.seek.com")
    parent = profile_for("www.seek.com")
    assert sub is not None and parent is not None
    assert sub is _PROFILES["employer.seek.com"]
    assert parent is _PROFILES["seek.com"]


def test_fastjobs_field_values():
    prof = profile_for("fastjobs.sg")
    assert "site/login" in prof.login_url_patterns
    assert "session expiring" in prof.login_url_patterns
    assert "session has expired due to inactivity" in prof.session_expired_markers
    assert "coyid=22091" in prof.candidate_entry_url


def test_fastjobs_io_full_parity():
    """Regional TLD must resolve the same employer-portal capabilities."""
    sg = profile_for("fastjobs.sg")
    io = profile_for("fastjobs.io")
    assert io is not None
    assert io.auth_model == "portal"
    assert io.cloudflare_protected is True
    assert io.candidate_entry_url == sg.candidate_entry_url
    assert io.candidate_entry_url.startswith("https://employer.fastjobs.sg/")
    assert io.login_url_patterns == sg.login_url_patterns
    assert io.session_expired_markers == sg.session_expired_markers


def test_mcf_field_values():
    prof = profile_for("www.mycareersfuture.gov.sg")
    assert prof.auth_model == "sso"
    assert "sign-in" in prof.login_url_patterns
    assert "session-timeout" in prof.login_url_patterns
    assert "you were logged out after" in prof.session_expired_markers
    assert prof.candidate_entry_url == "https://employer.mycareersfuture.gov.sg/talent-search"


def test_linkedin_field_values():
    prof = profile_for("linkedin.com")
    assert prof.auth_model == "portal"
    assert prof.cloudflare_protected is False
    assert "login" in prof.login_url_patterns
    assert "authwall" in prof.login_url_patterns
    assert "authwall" in prof.session_expired_markers

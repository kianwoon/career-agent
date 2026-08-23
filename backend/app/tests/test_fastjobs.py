"""Unit tests for the FastJobs adapter's pure helpers.

The browser navigation/extraction is network + Cloudflare dependent, so only
the deterministic pure helpers are unit-tested.
"""

from app.services.fastjobs import (
    _build_listing_url,
    _check_blocker,
    _clean_salary,
    _looks_employment_type,
    _looks_schedule,
)


def test_build_listing_url_page_1():
    url = _build_listing_url("software engineer")
    assert url.startswith("https://www.fastjobs.sg/singapore-jobs/all-categories-jobs/")
    assert "keyword=software%20engineer" in url


def test_build_listing_url_page_n():
    url = _build_listing_url("software engineer", page=3)
    assert "/all-categories-jobs/page-3/" in url
    assert "keyword=software%20engineer" in url


def test_clean_salary_range():
    assert _clean_salary("S$ 2800 - 4000 / per month") == "S$ 2800 - 4000 / per month"
    assert _clean_salary("S$ 2800 - 4000\n/per month") == "S$ 2800 - 4000 /per month"
    assert _clean_salary(None) is None
    assert _clean_salary("") is None

def test_looks_employment_type():
    assert _looks_employment_type("Full Time")
    assert _looks_employment_type("Part Time")
    assert _looks_employment_type("Contract")
    assert not _looks_employment_type("5.5 Day Week")
    assert not _looks_employment_type("Central Region")


def test_looks_schedule():
    assert _looks_schedule("5.5 Day Week")
    assert _looks_schedule("Rotating Shift")
    assert _looks_schedule("Mon to Fri")
    assert not _looks_schedule("Full Time")
    assert not _looks_schedule("S$ 2800 - 4000 / per month")


def test_check_blocker_detects_cloudflare():
    reason = _check_blocker(
        "https://www.fastjobs.sg/jobs/searchjobs",
        "Just a moment...",
    )
    assert reason is not None
    assert "Cloudflare" in reason


def test_check_blocker_none_for_normal_page():
    assert _check_blocker("https://www.fastjobs.sg/jobs/", "Jobs in Singapore - Aug 2026 | FastJobs") is None

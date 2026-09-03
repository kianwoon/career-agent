"""Tests for the guided-wizard templatizer and domain parsing."""

from app.services.source_flows import (
    build_boolean_keywords,
    domain_of,
    filter_excluded_results,
    templatize,
)


def test_domain_of_strips_www():
    assert domain_of("https://www.fastjob.com/jobs") == "fastjob.com"
    assert domain_of("http://FastJob.com") == "fastjob.com"


def test_boolean_keywords_single_query_no_excludes():
    assert build_boolean_keywords(["software engineer"], []) == '"software engineer"'


def test_boolean_keywords_multi_query_or():
    out = build_boolean_keywords(["software engineer", "developer"], [])
    assert out == '"software engineer" OR developer'


def test_boolean_keywords_with_excludes():
    out = build_boolean_keywords(
        ["software engineer"], ["recruiter", "talent acquisition"]
    )
    assert out == '"software engineer" NOT (recruiter OR "talent acquisition")'


def test_boolean_keywords_cap_not_terms():
    # 5 excludes -> only first 4 in the NOT clause (boolean-engine quirk:
    # 5+ silently returns zero results).
    out = build_boolean_keywords(["dev"], ["a", "b", "c", "d", "e"])
    assert 'NOT (a OR b OR c OR d)' in out
    assert ' e' not in out


def test_boolean_keywords_preserves_existing_syntax():
    q = 'digital AND sales NOT (hr OR "people ops")'
    assert build_boolean_keywords([q], []) == q


def test_boolean_keywords_empty():
    assert build_boolean_keywords([], []) == ""


def test_filter_excluded_results_drops_matches():
    results = [
        {"title": "Senior Python Dev", "company": "Acme"},
        {"title": "Tech Recruiter", "company": "HireCo"},
    ]
    kept = filter_excluded_results(results, ["recruiter"])
    assert [r["title"] for r in kept] == ["Senior Python Dev"]


def test_filter_excluded_results_no_excludes_noop():
    results = [{"title": "Dev"}]
    assert filter_excluded_results(results, None) is results


def test_templatize_empty_events():
    steps, card = templatize([])
    assert steps == []
    assert card is None


def test_templatize_binds_query_param():
    events = [
        {"action": "fill", "selector": "#search-box", "value": "python developer"},
        {"action": "submit", "selector": "form#search"},
        {"action": "click", "selector": "button.filter", "text": "Full-time"},
    ]
    steps, _card = templatize(events, query_hint="python developer")
    fills = [s for s in steps if s["action"] == "fill"]
    assert len(fills) == 1
    assert fills[0]["param"] == "query"
    assert fills[0]["selector"] == "#search-box"
    # submit becomes a press step
    assert any(s["action"] == "press" and s["key"] == "Enter" for s in steps)
    # filter click preserved as a plain click
    assert {"action": "click", "selector": "button.filter"} in steps


def test_templatize_extracts_pagination():
    events = [
        {"action": "fill", "selector": "#q", "value": "devops"},
        {"action": "click", "selector": "a.next-page", "text": "Next ›"},
    ]
    steps, _ = templatize(events, query_hint="devops")
    pag = [s for s in steps if s.get("repeat") == "paginate"]
    assert len(pag) == 1
    assert pag[0]["selector"] == "a.next-page"
    # pagination step must come last (runs after extraction)
    assert steps[-1] is pag[0]


def test_templatize_mark_card_becomes_card_selectors():
    events = [
        {"action": "fill", "selector": "#q", "value": "qa"},
        {"action": "mark_card", "selector": "div.job-card"},
    ]
    steps, card = templatize(events, query_hint="qa")
    assert card is not None
    assert card["card"] == "div.job-card"
    assert "fields" in card
    # mark_card events must not leak into steps
    assert all(s["action"] != "mark_card" for s in steps)


def test_templatize_only_first_fill_becomes_query():
    events = [
        {"action": "fill", "selector": "#location", "value": "Singapore"},
        {"action": "fill", "selector": "#q", "value": "python"},
    ]
    steps, _ = templatize(events, query_hint="python")
    fills = [s for s in steps if s["action"] == "fill"]
    assert fills[0].get("param") is None
    assert fills[0]["value"] == "Singapore"
    assert fills[1].get("param") == "query"


def test_sanitize_storage_state_normalizes_chrome_samesite():
    """Chrome cookie sameSite values must be mapped to Playwright's
    Strict|Lax|None — a bad value used to crash new_context() and pause
    the whole candidate search with a misleading 'session expired'."""
    from app.services.encryption import encrypt_session_state
    from app.services.source_flows import _sanitize_storage_state

    bad = {
        "cookies": [
            {"name": "a", "sameSite": "no_restriction"},
            {"name": "b", "sameSite": "lax"},
            {"name": "c", "sameSite": "strict"},
            {"name": "d", "sameSite": "unspecified"},
            {"name": "e"},  # missing sameSite
        ],
        "origins": [],
    }
    blob = encrypt_session_state(__import__("json").dumps(bad))
    out = _sanitize_storage_state(blob)
    assert [c["sameSite"] for c in out["cookies"]] == ["None", "Lax", "Strict", "Lax", "Lax"]
    assert _sanitize_storage_state(None) is None


def test_record_stop_rejects_empty_clicks():
    """Sanity on the model: query_hint optional."""
    from app.api.routes.sources import AgentRecordRequest

    req = AgentRecordRequest(flow_type="find_candidates")
    assert req.query_hint is None

"""Tests for the guided-wizard templatizer and domain parsing."""

from app.services.source_flows import domain_of, templatize


def test_domain_of_strips_www():
    assert domain_of("https://www.fastjob.com/jobs") == "fastjob.com"
    assert domain_of("http://FastJob.com") == "fastjob.com"


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

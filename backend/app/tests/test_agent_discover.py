"""Tests for _agent_discover SEEK record path (URL-param search, no DOM fill)."""

import pytest

from app.api.routes.sources import _agent_discover


class _FakeRegistry:
    """Records dispatch calls; returns canned find_result_card payloads."""

    def __init__(
        self,
        card_found: bool = True,
        needs_human: bool = False,
        page_state: dict | None = None,
        extract_rows: list | None = None,
    ):
        self.calls: list[tuple[str, dict]] = []
        self.card_found = card_found
        self.needs_human = needs_human
        self.page_state = page_state or {}
        self.extract_rows = extract_rows

    async def dispatch(self, cmd: str, params: dict, timeout_s: int = 30):
        self.calls.append((cmd, params))
        if cmd == "run_flow" and self.needs_human:
            return {"needs_human": True, "error": "login page detected"}
        if cmd == "page_state":
            return self.page_state
        if cmd == "find_result_card":
            if self.card_found:
                return {"found": True, "card": "div[data-card]"}
            return {"found": False, "card": None}
        if cmd == "extract":
            if self.extract_rows is not None:
                return self.extract_rows
            card = params.get("card", "")
            if card == "[data-testid*='card']":
                return [
                    {"raw_text": "x" * 100},
                    {"raw_text": "y" * 90},
                    {"raw_text": "z" * 85},
                ]
            return []
        return {}


class _FakeSource:
    domain = "seek.com"
    name = "jobstreet - candidate"
    base_url = "https://sg.employer.seek.com/talentsearch"


class _FakeReq:
    query_hint = "Tang Yee Henn"
    flow_type = "find_candidates"


async def test_seek_discover_searches_via_url_param(monkeypatch):
    """Regression: '#uncoupledFreeText' DOM fill fails when the input is not
    rendered — discovery must search via the URL param instead."""
    fake = _FakeRegistry()
    monkeypatch.setattr(
        "app.services.agent_relay.agent_registry", fake, raising=False
    )

    steps = await _agent_discover(_FakeSource(), _FakeReq())

    run_flow = next(p for c, p in fake.calls if c == "run_flow")
    actions = [s["action"] for s in run_flow["steps"]]
    assert "fill" not in actions, "SEEK discovery must not depend on DOM fill"
    assert any(
        s["action"] == "navigate" and "uncoupledFreeText={query}" in s["url"]
        for s in run_flow["steps"]
    )
    # Replay steps must also be URL-param based and carry the detected card.
    assert any(
        s.get("action") == "navigate" and "uncoupledFreeText={query}" in s["url"]
        for s in steps
    )
    assert steps[-1] == {"card": "div[data-card]", "fields": {"title": "a"}}
    assert run_flow["query"] == "Tang Yee Henn"
    # First try found the card — no extra wait-only run_flow, no extract probe.
    assert not any(c == "extract" for c, _ in fake.calls)


async def test_seek_discover_falls_back_to_extract_probe(monkeypatch):
    """find_result_card misses twice → probe extract selectors; first with
    3+ text-rich rows wins as the replay card."""
    fake = _FakeRegistry(card_found=False)
    monkeypatch.setattr(
        "app.services.agent_relay.agent_registry", fake, raising=False
    )

    steps = await _agent_discover(_FakeSource(), _FakeReq())

    assert any(c == "extract" for c, _ in fake.calls)
    assert steps[-1] == {
        "card": "[data-testid*='card']",
        "fields": {"title": "a"},
    }
    # Extra wait-only run_flow happened before the retry.
    waits = [
        p
        for c, p in fake.calls
        if c == "run_flow" and [s["action"] for s in p["steps"]] == ["wait"]
    ]
    assert waits, "expected a wait-only retry run_flow"


async def test_run_flow_wall_short_circuits(monkeypatch):
    """run_flow reports needs_human → 502 mentions re-login; no card detection."""
    fake = _FakeRegistry(needs_human=True)
    monkeypatch.setattr(
        "app.services.agent_relay.agent_registry", fake, raising=False
    )

    with pytest.raises(Exception) as ei:
        await _agent_discover(_FakeSource(), _FakeReq())

    assert "re-login" in str(ei.value)
    assert not any(c == "find_result_card" for c, _ in fake.calls)


async def test_no_cards_reports_page_state(monkeypatch):
    """All probes miss → 502 message includes page title/bodyChars from
    page_state so the failure is diagnosable."""
    fake = _FakeRegistry(
        card_found=False,
        extract_rows=[],
        page_state={
            "url": "https://sg.employer.seek.com/...",
            "title": "Talent Search",
            "bodyChars": 4200,
            "bodyHead": "Search profiles",
            "loginHint": False,
            "counts": {"[data-testid*='card']": 0},
        },
    )
    monkeypatch.setattr(
        "app.services.agent_relay.agent_registry", fake, raising=False
    )

    with pytest.raises(Exception) as ei:
        await _agent_discover(_FakeSource(), _FakeReq())

    msg = str(ei.value)
    assert "Talent Search" in msg
    assert "4200" in msg
    assert any(c == "page_state" for c, _ in fake.calls)

"""Tests for the MCF find_candidates employer talent-search path."""

import pytest

from app.api.routes.sources import (
    MCF_TALENT_SEARCH_URL,
    _agent_discover,
    _is_mcf_candidates,
    _session_capture_urls,
)
from app.services.source_flows import is_root_selector


class _FakeRegistry:
    """Records dispatch calls; returns canned payloads."""

    def __init__(self, card_found: bool = True, needs_human: bool = False):
        self.calls: list[tuple[str, dict]] = []
        self.card_found = card_found
        self.needs_human = needs_human

    async def dispatch(self, cmd: str, params: dict, timeout_s: int = 30):
        self.calls.append((cmd, params))
        if cmd == "run_flow" and self.needs_human:
            return {"needs_human": True, "error": "login page detected"}
        if cmd == "find_result_card":
            return {"found": self.card_found, "card": "div.talent-card" if self.card_found else None}
        if cmd == "extract":
            card = params.get("card", "")
            if card == "[data-testid*='talent']":
                return [
                    {"raw_text": "x" * 100},
                    {"raw_text": "y" * 90},
                    {"raw_text": "z" * 85},
                ]
            return []
        return {}


class _FakeSource:
    domain = "mycareersfuture.gov.sg"
    name = "MyCareersFuture - candidates"
    base_url = "https://www.mycareersfuture.gov.sg/"


class _FakeReq:
    query_hint = "software engineer"
    flow_type = "find_candidates"


def test_is_mcf_candidates_matches_domain_and_type():
    assert _is_mcf_candidates(_FakeSource(), "find_candidates") is True
    # find_jobs uses the public adapter — never the employer flow.
    assert _is_mcf_candidates(_FakeSource(), "find_jobs") is False


def test_session_capture_urls_cover_employer_host():
    urls = _session_capture_urls(_FakeSource())
    assert "https://www.mycareersfuture.gov.sg/" in urls
    assert MCF_TALENT_SEARCH_URL in urls


async def test_mcf_discover_targets_employer_talent_search(monkeypatch):
    fake = _FakeRegistry()
    monkeypatch.setattr("app.services.agent_relay.agent_registry", fake, raising=False)

    steps = await _agent_discover(_FakeSource(), _FakeReq())

    run_flow = next(p for c, p in fake.calls if c == "run_flow")
    assert run_flow["baseUrl"] == MCF_TALENT_SEARCH_URL
    # Landing + search-input fill/press on the employer host.
    assert run_flow["steps"][0] == {"action": "navigate", "url": MCF_TALENT_SEARCH_URL}
    assert any(
        s["action"] == "fill" and s["selector"] == "#talent-search-input"
        for s in run_flow["steps"]
    )
    # Replay steps lead with navigate to the employer URL and carry the card.
    assert steps[0] == {"action": "navigate", "url": MCF_TALENT_SEARCH_URL}
    assert steps[-1] == {"card": "div.talent-card", "fields": {"title": "a"}}


async def test_mcf_discover_rejects_root_card_then_probes(monkeypatch):
    class _RootRegistry(_FakeRegistry):
        async def dispatch(self, cmd, params, timeout_s=30):
            self.calls.append((cmd, params))
            if cmd == "find_result_card":
                return {"found": True, "card": "div#root"}
            if cmd == "extract":
                card = params.get("card", "")
                if card == "[data-testid*='talent']":
                    return [
                        {"raw_text": "x" * 100},
                        {"raw_text": "y" * 90},
                        {"raw_text": "z" * 85},
                    ]
                return []
            return {}

    fake = _RootRegistry()
    monkeypatch.setattr("app.services.agent_relay.agent_registry", fake, raising=False)

    steps = await _agent_discover(_FakeSource(), _FakeReq())

    assert steps[-1]["card"] == "[data-testid*='talent']"
    assert not is_root_selector(steps[-1]["card"])


async def test_mcf_discover_wall_raises(monkeypatch):
    fake = _FakeRegistry(needs_human=True)
    monkeypatch.setattr("app.services.agent_relay.agent_registry", fake, raising=False)

    with pytest.raises(Exception) as ei:
        await _agent_discover(_FakeSource(), _FakeReq())
    assert "re-login" in str(ei.value)


def test_is_root_selector_rejects_page_roots():
    for bad in ("html", "body", "#root", "div#root", "div[id=root]", "body > div", "HTML"):
        assert is_root_selector(bad) is True, bad
    for good in ("div.talent-card", "table tbody tr", "[data-testid*='card']", "article.result"):
        assert is_root_selector(good) is False, good

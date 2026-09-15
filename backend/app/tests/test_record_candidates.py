"""Tests for user-driven candidate click-recording (Record candidates redesign).

Covers: per-source entry-URL resolution (MCF / FastJobs / other), the session
guard on record start, the fill->param:query conversion, and the from-scratch
stop path that creates a find_candidates flow without a pre-existing card step.
"""

import os

import pytest
from fastapi.testclient import TestClient

from app.api.routes.sources import (
    FASTJOBS_TALENT_SEARCH_URL,
    MCF_TALENT_SEARCH_URL,
    _candidate_entry_url,
)
from app.config import get_settings
from app.main import app


class _FakeSource:
    def __init__(self, domain: str, base_url: str):
        self.domain = domain
        self.base_url = base_url


def test_candidate_entry_url_mcf():
    src = _FakeSource("mycareersfuture.gov.sg", "https://www.mycareersfuture.gov.sg/")
    assert _candidate_entry_url(src) == MCF_TALENT_SEARCH_URL


def test_candidate_entry_url_fastjobs():
    src = _FakeSource("fastjobs.sg", "https://www.fastjobs.sg/")
    assert _candidate_entry_url(src) == FASTJOBS_TALENT_SEARCH_URL
    assert "employer.fastjobs.sg" in FASTJOBS_TALENT_SEARCH_URL
    assert "coyid=22091" in FASTJOBS_TALENT_SEARCH_URL


def test_candidate_entry_url_other_source_falls_back_to_base():
    src = _FakeSource("jobstreet.com.sg", "https://www.jobstreet.com.sg/")
    assert _candidate_entry_url(src) == src.base_url


# --- route-level tests (live DB, mocked extension) -------------------------


@pytest.fixture(scope="module")
def client():
    saved = {k: os.environ.get(k) for k in ("API_KEYS", "API_RATE_LIMIT_PER_MIN")}
    os.environ["API_KEYS"] = "test-record-key:1000"
    os.environ["API_RATE_LIMIT_PER_MIN"] = "1000"
    from app.api import security

    get_settings.cache_clear()
    security._key_store = None
    with TestClient(app) as c:
        yield c
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    security._key_store = None
    get_settings.cache_clear()


def _headers():
    return {"X-API-Key": "test-record-key"}


class _FakeRegistry:
    """Canned extension: records dispatch calls, returns recorded events."""

    def __init__(self, events=None, card_found=True):
        self.calls = []
        self.events = events if events is not None else [
            {"action": "fill", "selector": "#talent-search-input", "ts": 1},
            {"action": "press", "key": "Enter", "selector": "#talent-search-input", "ts": 2},
            {"action": "click", "selector": "div.candidate-card", "text": "Jane", "ts": 3},
        ]
        self.card_found = card_found

    async def dispatch(self, cmd, params, timeout_s=30):
        self.calls.append((cmd, params))
        if cmd == "stop_record":
            return {"ok": True, "events": self.events, "count": len(self.events)}
        if cmd == "start_record":
            return {"ok": True, "recording": True}
        if cmd == "find_result_card":
            return {"found": self.card_found, "card": "div.talent-card" if self.card_found else None}
        if cmd == "extract":
            return []
        return {}


def _make_source(client, domain: str) -> str:
    # Clean a leftover from a previous run.
    for row in client.get("/api/v1/sources", headers=_headers()).json():
        if row["domain"] == domain:
            client.delete(f"/api/v1/sources/{row['id']}", headers=_headers())
    r = client.post(
        "/api/v1/sources",
        json={"name": f"rec-{domain}", "base_url": f"https://{domain}/"},
        headers=_headers(),
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_record_start_rejects_candidates_without_session(client, monkeypatch):
    fake = _FakeRegistry()
    monkeypatch.setattr("app.services.agent_relay.agent_registry", fake, raising=False)
    sid = _make_source(client, "rec-nosess.example")
    try:
        r = client.post(
            f"/api/v1/sources/{sid}/agent_record/start",
            json={"flow_type": "find_candidates"},
            headers=_headers(),
        )
        assert r.status_code == 422, r.text
        assert "sign in" in r.json()["error"]["message"].lower()
        # No extension dispatch happened.
        assert not any(c == "start_record" for c, _ in fake.calls)
    finally:
        client.delete(f"/api/v1/sources/{sid}", headers=_headers())


def test_record_start_uses_entry_url_for_candidates(client, monkeypatch):
    fake = _FakeRegistry()
    monkeypatch.setattr("app.services.agent_relay.agent_registry", fake, raising=False)
    sid = _make_source(client, "mycareersfuture.gov.sg")
    try:
        # Give it a session so the guard passes.
        client.put(
            f"/api/v1/sources/{sid}/agent_session",
            json={"cookies": [{"name": "a", "value": "b", "domain": ".mycareersfuture.gov.sg"}]},
            headers=_headers(),
        )
        r = client.post(
            f"/api/v1/sources/{sid}/agent_record/start",
            json={"flow_type": "find_candidates"},
            headers=_headers(),
        )
        assert r.status_code == 200, r.text
        start = next(p for c, p in fake.calls if c == "start_record")
        assert start["baseUrl"] == MCF_TALENT_SEARCH_URL
    finally:
        client.delete(f"/api/v1/sources/{sid}", headers=_headers())


def test_record_stop_creates_candidate_flow_from_scratch(client, monkeypatch):
    """No existing flow + recorded fill/press/click + detected card => flow."""
    fake = _FakeRegistry()
    monkeypatch.setattr("app.services.agent_relay.agent_registry", fake, raising=False)
    sid = _make_source(client, "mycareersfuture.gov.sg")
    try:
        r = client.post(
            f"/api/v1/sources/{sid}/agent_record/stop",
            json={"flow_type": "find_candidates"},
            headers=_headers(),
        )
        assert r.status_code == 200, r.text
        flow = r.json()
        assert flow["flow_type"] == "find_candidates"
        steps = flow["steps"]
        # Navigate prefix uses the candidate entry URL.
        assert steps[0] == {"action": "navigate", "url": MCF_TALENT_SEARCH_URL}
        # Recorded fill became param:query with NO literal text.
        fill = next(s for s in steps if s.get("action") == "fill")
        assert fill == {"action": "fill", "selector": "#talent-search-input", "param": "query"}
        assert "value" not in fill
        # Press Enter preserved.
        assert any(
            s.get("action") == "press" and s.get("key") == "Enter" for s in steps
        )
        # Card step appended from find_result_card.
        assert steps[-1]["card"] == "div.talent-card"
    finally:
        client.delete(f"/api/v1/sources/{sid}", headers=_headers())


def test_record_stop_candidates_no_card_gets_clear_error(client, monkeypatch):
    fake = _FakeRegistry(card_found=False)
    monkeypatch.setattr("app.services.agent_relay.agent_registry", fake, raising=False)
    sid = _make_source(client, "mycareersfuture.gov.sg")
    try:
        r = client.post(
            f"/api/v1/sources/{sid}/agent_record/stop",
            json={"flow_type": "find_candidates"},
            headers=_headers(),
        )
        assert r.status_code == 502, r.text
        assert "click a candidate card" in r.json()["error"]["message"].lower()
    finally:
        client.delete(f"/api/v1/sources/{sid}", headers=_headers())


def test_record_stop_never_stores_typed_text(client, monkeypatch):
    """A fill carrying user text in the raw event must not leak it into steps."""
    fake = _FakeRegistry(events=[
        {"action": "fill", "selector": "input.q", "text": "secret query", "ts": 1},
    ])
    monkeypatch.setattr("app.services.agent_relay.agent_registry", fake, raising=False)
    sid = _make_source(client, "rec-leak.example")
    try:
        r = client.post(
            f"/api/v1/sources/{sid}/agent_record/stop",
            json={"flow_type": "find_candidates"},
            headers=_headers(),
        )
        assert r.status_code == 200, r.text
        blob = str(r.json()["steps"])
        assert "secret query" not in blob
    finally:
        client.delete(f"/api/v1/sources/{sid}", headers=_headers())

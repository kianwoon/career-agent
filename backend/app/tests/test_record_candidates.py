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
    _session_capture_urls,
)
from app.config import get_settings
from app.main import app


class _FakeSource:
    def __init__(self, domain: str, base_url: str):
        self.domain = domain
        self.base_url = base_url
        self.name = f"rec-{domain}"


def test_candidate_entry_url_mcf():
    src = _FakeSource("mycareersfuture.gov.sg", "https://www.mycareersfuture.gov.sg/")
    assert _candidate_entry_url(src) == MCF_TALENT_SEARCH_URL


def test_candidate_entry_url_fastjobs():
    src = _FakeSource("fastjobs.sg", "https://www.fastjobs.sg/")
    assert _candidate_entry_url(src) == FASTJOBS_TALENT_SEARCH_URL
    assert "employer.fastjobs.sg" in FASTJOBS_TALENT_SEARCH_URL
    assert "coyid=22091" in FASTJOBS_TALENT_SEARCH_URL


def test_candidate_entry_url_fastjobs_io():
    """Regional TLD resolves the employer talent-search entry, not base_url."""
    src = _FakeSource("fastjobs.io", "https://www.fastjobs.io/")
    assert _candidate_entry_url(src) == FASTJOBS_TALENT_SEARCH_URL
    assert "employer.fastjobs.sg" in _candidate_entry_url(src)


def test_session_capture_urls_fastjobs_io():
    """.io captures both its base host and the employer host."""
    src = _FakeSource("fastjobs.io", "https://www.fastjobs.io/")
    urls = _session_capture_urls(src)
    assert src.base_url in urls
    assert FASTJOBS_TALENT_SEARCH_URL in urls


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

    def __init__(self, events=None, card_found=True, page_state=None):
        self.calls = []
        self.events = events if events is not None else [
            {"action": "fill", "selector": "#talent-search-input", "ts": 1},
            {"action": "press", "key": "Enter", "selector": "#talent-search-input", "ts": 2},
            {"action": "click", "selector": "div.candidate-card", "text": "Jane", "ts": 3},
        ]
        self.card_found = card_found
        self.page_state = page_state if page_state is not None else {}

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
        if cmd == "page_state":
            return self.page_state
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


# --- FastJobs candidate branch (coyid capture + login wall) -----------------


def _fastjobs_source():
    return _FakeSource(
        "fastjobs.sg", "https://www.fastjobs.sg/"
    )


async def test_fastjobs_branch_uses_resolved_url(monkeypatch):
    """A page_state URL with a different coyid must replace the default."""
    from app.api.routes import sources as sources_mod

    resolved = "https://employer.fastjobs.sg/p/talent/search/?coyid=99999"
    fake = _FakeRegistry(page_state={"url": resolved, "counts": {}, "bodyChars": 5000})
    monkeypatch.setattr("app.services.agent_relay.agent_registry", fake, raising=False)

    steps = await sources_mod._agent_discover_fastjobs(_fastjobs_source())
    assert steps[0]["action"] == "navigate"
    assert steps[0]["url"] == resolved
    # find_result_card returned a card → final step present.
    assert steps[-1]["card"] == "div.talent-card"


async def test_fastjobs_branch_login_wall_raises_502(monkeypatch):
    from fastapi import HTTPException

    from app.api.routes import sources as sources_mod

    fake = _FakeRegistry(
        page_state={"url": "https://employer.fastjobs.sg/site/login/", "counts": {"input[type='password']": 1}}
    )
    monkeypatch.setattr("app.services.agent_relay.agent_registry", fake, raising=False)

    try:
        await sources_mod._agent_discover_fastjobs(_fastjobs_source())
        raise AssertionError("expected HTTPException 502")
    except HTTPException as exc:
        assert exc.status_code == 502
        assert "session expired" in exc.detail.lower()


def test_is_fastjobs_candidates_gate():
    from app.api.routes.sources import _is_fastjobs_candidates

    assert _is_fastjobs_candidates(_FakeSource("fastjobs.sg", "x"), "find_candidates")
    assert not _is_fastjobs_candidates(_FakeSource("fastjobs.sg", "x"), "find_jobs")
    assert not _is_fastjobs_candidates(_FakeSource("other.com", "x"), "find_candidates")


# --- backend-side nav-noise filtering (mirror of extension pruneNavNoise) ----


def test_is_nav_noise_click_backend_rule():
    from app.api.routes.sources import _is_nav_noise_click

    # navbar selector + non-search text → noise (the FastJobs "Talent search\n NEW" case unless it matches search/talent)
    assert _is_nav_noise_click({"action": "click", "selector": "ul.navbar-nav > li > a", "text": "Chats"})
    assert _is_nav_noise_click({"action": "click", "selector": "div.navbar-container a", "text": "Home"})
    # navbar but search/talent entry → kept
    assert not _is_nav_noise_click({"action": "click", "selector": "div.navbar-nav a", "text": "Talent search\n NEW"})
    assert not _is_nav_noise_click({"action": "click", "selector": "div.navbar-nav a", "text": "Search"})
    # not navbar → kept
    assert not _is_nav_noise_click({"action": "click", "selector": "div.candidate-card", "text": "Jane"})
    # non-click steps never filtered
    assert not _is_nav_noise_click({"action": "fill", "selector": "div.navbar-nav input"})


def test_record_stop_drops_navbar_clicks(client, monkeypatch):
    """A recorded navbar click is filtered out of the persisted steps."""
    fake = _FakeRegistry(events=[
        {"action": "click", "selector": "div.navbar-container a", "text": "Chats", "ts": 1},
        {"action": "click", "selector": "div.navbar-nav a", "text": "Talent search\n NEW", "ts": 2},
        {"action": "fill", "selector": "#talent-search-input", "ts": 3},
        {"action": "press", "key": "Enter", "selector": "#talent-search-input", "ts": 4},
    ])
    monkeypatch.setattr("app.services.agent_relay.agent_registry", fake, raising=False)
    sid = _make_source(client, "rec-nav.example")
    try:
        r = client.post(
            f"/api/v1/sources/{sid}/agent_record/stop",
            json={"flow_type": "find_candidates"},
            headers=_headers(),
        )
        assert r.status_code == 200, r.text
        blob = str(r.json()["steps"])
        # The incidental navbar click is gone; the search/talent nav click stays.
        assert "navbar-container" not in blob
        assert "Chats" not in blob
        assert "Talent search" in blob
    finally:
        client.delete(f"/api/v1/sources/{sid}", headers=_headers())


def test_record_stop_refuses_stale_card_without_card_click(client, monkeypatch):
    """Existing candidate flow, stored card rotted, auto-detect failed, and the
    recording contains a candidate-ish click → must NOT silently keep the junk
    suffix; it must raise a diagnosable 502 and leave the flow untouched."""
    sid = _make_source(client, "rec-stale.example")
    try:
        # 1) First stop (card_found=True) creates the flow with a real card step.
        ok = _FakeRegistry()
        monkeypatch.setattr("app.services.agent_relay.agent_registry", ok, raising=False)
        first = client.post(
            f"/api/v1/sources/{sid}/agent_record/stop",
            json={"flow_type": "find_candidates"},
            headers=_headers(),
        )
        assert first.status_code == 200, first.text
        original_steps = first.json()["steps"]
        assert original_steps[-1]["card"] == "div.talent-card"

        # 2) Re-record: stored card rots (extract→[]), auto-detect fails
        #    (card_found=False), but the user DID click a candidate-ish card.
        bad = _FakeRegistry(
            card_found=False,
            events=[
                {"action": "fill", "selector": "#talent-search-input", "ts": 1},
                {"action": "press", "key": "Enter", "selector": "#talent-search-input", "ts": 2},
                {"action": "click", "selector": "div.candidate-card", "text": "Ahmad Rizal", "ts": 3},
            ],
        )
        monkeypatch.setattr("app.services.agent_relay.agent_registry", bad, raising=False)
        second = client.post(
            f"/api/v1/sources/{sid}/agent_record/stop",
            json={"flow_type": "find_candidates"},
            headers=_headers(),
        )
        assert second.status_code == 502, second.text
        assert "not updated" in second.json()["error"]["message"].lower()

        # 3) The stored flow is unchanged — the junk suffix was not persisted.
        flows = client.get(f"/api/v1/sources/{sid}/flows", headers=_headers()).json()
        cand = next(f for f in flows if f["flow_type"] == "find_candidates")
        assert cand["steps"] == original_steps
    finally:
        client.delete(f"/api/v1/sources/{sid}", headers=_headers())



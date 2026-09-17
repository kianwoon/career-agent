"""Tests for pluggable source CRUD (no browser needed)."""

import os

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


@pytest.fixture(scope="module")
def client():
    # Other test modules (test_security) install a tiny rate limit; give this
    # module a fresh key store with a generous limit so CRUD tests don't 429.
    os.environ["API_KEYS"] = "test-src-key:1000"
    os.environ["API_RATE_LIMIT_PER_MIN"] = "1000"
    from app.api import security

    get_settings.cache_clear()
    security._key_store = None
    with TestClient(app) as c:
        yield c


def _headers():
    return {"X-API-Key": "test-src-key"}


def test_source_crud_roundtrip(client):
    # Clean up any leftover row from a previous run (via the API to stay on
    # the TestClient's event loop).
    listing = client.get("/api/v1/sources", headers=_headers()).json()
    for row in listing:
        if row["domain"] == "testboard.example":
            client.delete(f"/api/v1/sources/{row['id']}", headers=_headers())

    # Create
    r = client.post(
        "/api/v1/sources",
        json={"name": "TestBoard", "base_url": "https://www.testboard.example/jobs"},
        headers=_headers(),
    )
    assert r.status_code == 201, r.text
    src = r.json()
    assert src["domain"] == "testboard.example"
    assert src["has_session"] is False

    # Duplicate domain rejected
    r2 = client.post(
        "/api/v1/sources",
        json={"name": "Dup", "base_url": "testboard.example"},
        headers=_headers(),
    )
    assert r2.status_code == 409

    # List contains it
    r3 = client.get("/api/v1/sources", headers=_headers())
    assert r3.status_code == 200
    assert any(s["id"] == src["id"] for s in r3.json())

    # Delete
    r4 = client.delete(f"/api/v1/sources/{src['id']}", headers=_headers())
    assert r4.status_code == 204
    r5 = client.get("/api/v1/sources", headers=_headers())
    assert not any(s["id"] == src["id"] for s in r5.json())


def test_wizard_start_unknown_source_404(client):
    r = client.post(
        "/api/v1/sources/nonexistent/wizard/start",
        json={"mode": "login"},
        headers=_headers(),
    )
    assert r.status_code == 404


def test_create_source_rejects_dotless_url(client):
    """A bare word URL ("JobStreet") would navigate login to https://jobstreet/
    — reject it at creation so the swapped-fields typo can't store garbage."""
    r = client.post(
        "/api/v1/sources",
        json={"name": "JobStreet", "base_url": "JobStreet"},
        headers=_headers(),
    )
    assert r.status_code == 400
    assert "not a valid site URL" in r.json()["error"]["message"]


def test_create_source_probes_and_exposes_profile(client, monkeypatch):
    """Registration runs the site probe once and stores the result on the row;
    SourceView exposes it (default None)."""
    fake = {
        "auth_model": "portal",
        "cloudflare_protected": False,
        "login_url_patterns": ["/site/login"],
        "probed": True,
    }

    async def fake_probe(_url):
        return fake

    monkeypatch.setattr("app.api.routes.sources.probe_site", fake_probe)

    # Clean up any leftover row from a previous run.
    for row in client.get("/api/v1/sources", headers=_headers()).json():
        if row["domain"] == "probed.example":
            client.delete(f"/api/v1/sources/{row['id']}", headers=_headers())

    r = client.post(
        "/api/v1/sources",
        json={"name": "ProbedBoard", "base_url": "https://probed.example/jobs"},
        headers=_headers(),
    )
    assert r.status_code == 201, r.text
    src = r.json()
    assert src["profile"] == fake

    # Also exposed via the list endpoint.
    listing = client.get("/api/v1/sources", headers=_headers()).json()
    row = next(s for s in listing if s["id"] == src["id"])
    assert row["profile"] == fake

    client.delete(f"/api/v1/sources/{src['id']}", headers=_headers())


def test_create_source_probe_failure_still_registers(client, monkeypatch):
    """A raising probe must never fail registration; profile stores None."""

    async def boom(_url):
        raise RuntimeError("network down")

    monkeypatch.setattr("app.api.routes.sources.probe_site", boom)

    for row in client.get("/api/v1/sources", headers=_headers()).json():
        if row["domain"] == "probe-fail.example":
            client.delete(f"/api/v1/sources/{row['id']}", headers=_headers())

    r = client.post(
        "/api/v1/sources",
        json={"name": "ProbeFail", "base_url": "https://probe-fail.example/"},
        headers=_headers(),
    )
    assert r.status_code == 201, r.text
    assert r.json()["profile"] is None
    client.delete(f"/api/v1/sources/{r.json()['id']}", headers=_headers())


def test_source_credentials_save_forget_and_never_leak(client):
    """POST /credentials stores only an encrypted blob and the API echoes back
    booleans — never the password or the ciphertext. DELETE forgets them."""
    for row in client.get("/api/v1/sources", headers=_headers()).json():
        if row["domain"] == "creds.example":
            client.delete(f"/api/v1/sources/{row['id']}", headers=_headers())

    src = client.post(
        "/api/v1/sources",
        json={"name": "CredsBoard", "base_url": "https://creds.example/jobs"},
        headers=_headers(),
    ).json()
    assert src["has_credentials"] is False

    r = client.post(
        f"/api/v1/sources/{src['id']}/credentials",
        json={"username": "ops@creds.example", "password": "s3cret-pw"},
        headers=_headers(),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["has_credentials"] is True
    # The secret must not appear anywhere in the serialized response.
    assert "s3cret-pw" not in r.text
    assert "ops@creds.example" not in r.text

    # Re-fetch: still only the boolean.
    row = next(
        s
        for s in client.get("/api/v1/sources", headers=_headers()).json()
        if s["id"] == src["id"]
    )
    assert row["has_credentials"] is True
    assert row["needs_relogin"] is True  # no session captured yet

    # Forget.
    r2 = client.delete(f"/api/v1/sources/{src['id']}/credentials", headers=_headers())
    assert r2.status_code == 200, r2.text
    assert r2.json()["has_credentials"] is False

    client.delete(f"/api/v1/sources/{src['id']}", headers=_headers())


class _FakeWizard:
    """Stand-in for WizardSession so wizard_start runs without a browser."""

    instances: list["_FakeWizard"] = []
    capture_cookies: list[dict] = [{"name": "sid", "value": "x"}]

    def __init__(self, source_id, flow_type, domain=None):
        self.source_id = source_id
        self.flow_type = flow_type
        self.domain = domain
        self.page = object()
        self.context = object()
        self.started = False
        self.closed = False
        _FakeWizard.instances.append(self)

    async def age_s(self):
        return 0.0

    async def start(self, start_url, storage_state=None):
        self.started = True

    async def close(self):
        self.closed = True

    async def fill_credentials(self, username, password, submit=True):
        return {"ok": True}

    async def capture_state(self):
        cookies = getattr(_FakeWizard, "capture_cookies", [{"name": "sid", "value": "x"}])
        return {"storage_state": {"cookies": cookies, "origins": []}, "url": "https://s/", "title": "t"}

    async def status(self):
        return {
            "url": "https://s/",
            "title": "t",
            "logged_in": False,
            "autofill_status": getattr(self, "autofill_status", None),
            "autofill_blocker": getattr(self, "autofill_blocker", None),
        }


def test_wizard_start_login_autofills_saved_credentials(client, monkeypatch):
    """Re-login with saved creds calls the backend auto-fill path, and neither
    the username nor the password appears in the response body."""
    import app.api.routes.sources as routes

    for row in client.get("/api/v1/sources", headers=_headers()).json():
        if row["domain"] == "wizautofill.example":
            client.delete(f"/api/v1/sources/{row['id']}", headers=_headers())

    src = client.post(
        "/api/v1/sources",
        json={"name": "WizAutofill", "base_url": "https://wizautofill.example/"},
        headers=_headers(),
    ).json()
    client.post(
        f"/api/v1/sources/{src['id']}/credentials",
        json={"username": "ops@wizautofill.example", "password": "s3cret-pw"},
        headers=_headers(),
    )

    calls = []

    async def fake_autofill(page, username, password, timeout_s=10.0):
        calls.append((username, password))
        return "filled", None

    _FakeWizard.instances = []
    monkeypatch.setattr(routes, "WizardSession", _FakeWizard)
    monkeypatch.setattr(routes, "autofill_wizard_login", fake_autofill)
    monkeypatch.setattr(routes, "_AUTO_SAVE_SETTLE_S", 0)

    r = client.post(
        f"/api/v1/sources/{src['id']}/wizard/start",
        json={"mode": "login"},
        headers=_headers(),
    )
    assert r.status_code == 201, r.text
    # The saved pair reached the fill path exactly once (backend-side only).
    assert calls == [("ops@wizautofill.example", "s3cret-pw")]
    # Response carries no credential material.
    assert "s3cret-pw" not in r.text
    assert "ops@wizautofill.example" not in r.text
    assert set(r.json().keys()) == {"wizard_id", "mode", "start_url"}

    client.delete(f"/api/v1/sources/{src['id']}", headers=_headers())


def test_wizard_start_login_autosaves_after_submit(client, monkeypatch):
    """A no-blocker 'submitted' auto-fill captures + persists the session."""
    import app.api.routes.sources as routes

    for row in client.get("/api/v1/sources", headers=_headers()).json():
        if row["domain"] == "wizsave.example":
            client.delete(f"/api/v1/sources/{row['id']}", headers=_headers())

    src = client.post(
        "/api/v1/sources",
        json={"name": "WizSave", "base_url": "https://wizsave.example/"},
        headers=_headers(),
    ).json()
    client.post(
        f"/api/v1/sources/{src['id']}/credentials",
        json={"username": "ops@wizsave.example", "password": "s3cret-pw"},
        headers=_headers(),
    )

    async def fake_autofill(page, username, password, timeout_s=10.0):
        return "submitted", None

    _FakeWizard.instances = []
    _FakeWizard.capture_cookies = [{"name": "sid", "value": "abc", "domain": "wizsave.example"}]
    monkeypatch.setattr(routes, "WizardSession", _FakeWizard)
    monkeypatch.setattr(routes, "autofill_wizard_login", fake_autofill)
    monkeypatch.setattr(routes, "_AUTO_SAVE_SETTLE_S", 0)

    r = client.post(
        f"/api/v1/sources/{src['id']}/wizard/start",
        json={"mode": "login"},
        headers=_headers(),
    )
    assert r.status_code == 201, r.text

    stored = client.get(f"/api/v1/sources/{src['id']}/wizard/status", headers=_headers())
    # wizard status carries the derived autofill outcome (never creds/cookies)
    assert stored.status_code == 200, stored.text
    assert stored.json()["autofill_status"] == "submitted-saved"
    assert "sid" not in r.text  # cookie values never in the response

    client.delete(f"/api/v1/sources/{src['id']}", headers=_headers())
    _FakeWizard.capture_cookies = [{"name": "sid", "value": "x"}]


def test_wizard_start_login_no_creds_skips_autofill(client, monkeypatch):
    """No saved credentials => auto-fill is never attempted (empty form)."""
    import app.api.routes.sources as routes

    for row in client.get("/api/v1/sources", headers=_headers()).json():
        if row["domain"] == "wiznocreds.example":
            client.delete(f"/api/v1/sources/{row['id']}", headers=_headers())

    src = client.post(
        "/api/v1/sources",
        json={"name": "WizNoCreds", "base_url": "https://wiznocreds.example/"},
        headers=_headers(),
    ).json()

    called = []

    async def fake_autofill(page, username, password, timeout_s=10.0):
        called.append(True)
        return "filled", None

    monkeypatch.setattr(routes, "WizardSession", _FakeWizard)
    monkeypatch.setattr(routes, "autofill_wizard_login", fake_autofill)

    r = client.post(
        f"/api/v1/sources/{src['id']}/wizard/start",
        json={"mode": "login"},
        headers=_headers(),
    )
    assert r.status_code == 201, r.text
    assert called == []

    client.delete(f"/api/v1/sources/{src['id']}", headers=_headers())


def test_agent_login_autofills_saved_credentials(client, monkeypatch):
    """Re-login via the browser agent (the path the UI actually hits) relays an
    autofill_login with the decrypted pair AFTER navigating, and the response
    never carries credential material."""
    import app.services.agent_relay as relay

    for row in client.get("/api/v1/sources", headers=_headers()).json():
        if row["domain"] == "agentfill.example":
            client.delete(f"/api/v1/sources/{row['id']}", headers=_headers())

    src = client.post(
        "/api/v1/sources",
        json={"name": "AgentFill", "base_url": "https://agentfill.example/"},
        headers=_headers(),
    ).json()
    client.post(
        f"/api/v1/sources/{src['id']}/credentials",
        json={"username": "ops@agentfill.example", "password": "s3cret-pw"},
        headers=_headers(),
    )

    calls = []

    class _FakeRegistry:
        async def dispatch(self, action, params, timeout_s=180):
            calls.append((action, params))
            if action == "autofill_login":
                return {"ok": True, "filled": True}
            return {"ok": True}

    monkeypatch.setattr(relay, "agent_registry", _FakeRegistry())

    r = client.post(f"/api/v1/sources/{src['id']}/agent_login", headers=_headers())
    assert r.status_code == 200, r.text
    # Navigate first, then the credential-bearing autofill relay command.
    assert [c[0] for c in calls if c[0] in ("navigate", "autofill_login")] == [
        "navigate",
        "autofill_login",
    ]
    fill = next(p for a, p in calls if a == "autofill_login")
    assert fill == {"username": "ops@agentfill.example", "password": "s3cret-pw"}
    # Secrets never leave the backend through the response.
    assert "s3cret-pw" not in r.text
    assert "ops@agentfill.example" not in r.text
    assert r.json()["autofill"] == "filled"

    client.delete(f"/api/v1/sources/{src['id']}", headers=_headers())


def test_agent_login_surfaces_submitted_status(client, monkeypatch):
    """A no-blocker auto-submit triggers an automatic get_cookies relay capture
    and reports 'submitted-saved'; the response never carries credential/cookie
    material."""
    import app.api.routes.sources as routes
    import app.services.agent_relay as relay

    for row in client.get("/api/v1/sources", headers=_headers()).json():
        if row["domain"] == "agentsubmit.example":
            client.delete(f"/api/v1/sources/{row['id']}", headers=_headers())

    src = client.post(
        "/api/v1/sources",
        json={"name": "AgentSubmit", "base_url": "https://agentsubmit.example/"},
        headers=_headers(),
    ).json()
    client.post(
        f"/api/v1/sources/{src['id']}/credentials",
        json={"username": "ops@agentsubmit.example", "password": "s3cret-pw"},
        headers=_headers(),
    )

    calls = []

    class _FakeRegistry:
        async def dispatch(self, action, params, timeout_s=180):
            calls.append(action)
            if action == "autofill_login":
                return {"ok": True, "filled": True, "submitted": True, "blocked": False}
            if action == "get_cookies":
                return [{"name": "sid", "value": "cookieval", "domain": "agentsubmit.example"}]
            return {"ok": True}

    monkeypatch.setattr(relay, "agent_registry", _FakeRegistry())
    monkeypatch.setattr(routes, "_AUTO_SAVE_SETTLE_S", 0)

    r = client.post(f"/api/v1/sources/{src['id']}/agent_login", headers=_headers())
    assert r.status_code == 200, r.text
    assert r.json()["autofill"] == "submitted-saved"
    assert "get_cookies" in calls  # auto-capture actually dispatched
    assert "s3cret-pw" not in r.text
    assert "ops@agentsubmit.example" not in r.text
    assert "cookieval" not in r.text
    stored = next(
        row for row in client.get("/api/v1/sources", headers=_headers()).json()
        if row["id"] == src["id"]
    )
    assert stored["has_session"] is True

    client.delete(f"/api/v1/sources/{src['id']}", headers=_headers())


def test_agent_login_save_failure_reports_unsaved(client, monkeypatch):
    """A get_cookies failure after auto-submit degrades to 'submitted-unsaved'
    (manual capture path stays intact); no exception leaks."""
    import app.api.routes.sources as routes
    import app.services.agent_relay as relay

    for row in client.get("/api/v1/sources", headers=_headers()).json():
        if row["domain"] == "agentsavefail.example":
            client.delete(f"/api/v1/sources/{row['id']}", headers=_headers())

    src = client.post(
        "/api/v1/sources",
        json={"name": "AgentSaveFail", "base_url": "https://agentsavefail.example/"},
        headers=_headers(),
    ).json()
    client.post(
        f"/api/v1/sources/{src['id']}/credentials",
        json={"username": "u", "password": "p"},
        headers=_headers(),
    )

    class _FakeRegistry:
        async def dispatch(self, action, params, timeout_s=180):
            if action == "autofill_login":
                return {"ok": True, "filled": True, "submitted": True, "blocked": False}
            if action == "get_cookies":
                raise RuntimeError("extension gone")
            return {"ok": True}

    monkeypatch.setattr(relay, "agent_registry", _FakeRegistry())
    monkeypatch.setattr(routes, "_AUTO_SAVE_SETTLE_S", 0)

    r = client.post(f"/api/v1/sources/{src['id']}/agent_login", headers=_headers())
    assert r.status_code == 200, r.text
    assert r.json()["autofill"] == "submitted-unsaved"

    client.delete(f"/api/v1/sources/{src['id']}", headers=_headers())


def test_agent_login_blocker_fills_without_saving(client, monkeypatch):
    """A CAPTCHA/MFA blocker => fill only, never submit, never auto-save."""
    import app.api.routes.sources as routes
    import app.services.agent_relay as relay

    for row in client.get("/api/v1/sources", headers=_headers()).json():
        if row["domain"] == "agentblocked.example":
            client.delete(f"/api/v1/sources/{row['id']}", headers=_headers())

    src = client.post(
        "/api/v1/sources",
        json={"name": "AgentBlocked", "base_url": "https://agentblocked.example/"},
        headers=_headers(),
    ).json()
    client.post(
        f"/api/v1/sources/{src['id']}/credentials",
        json={"username": "u", "password": "p"},
        headers=_headers(),
    )

    calls = []

    class _FakeRegistry:
        async def dispatch(self, action, params, timeout_s=180):
            calls.append(action)
            if action == "autofill_login":
                return {"ok": True, "filled": True, "submitted": False, "blocked": True}
            return {"ok": True}

    monkeypatch.setattr(relay, "agent_registry", _FakeRegistry())
    monkeypatch.setattr(routes, "_AUTO_SAVE_SETTLE_S", 0)

    r = client.post(f"/api/v1/sources/{src['id']}/agent_login", headers=_headers())
    assert r.status_code == 200, r.text
    assert r.json()["autofill"] == "filled-blocker-present"
    assert "get_cookies" not in calls  # no auto-save on a blocker

    client.delete(f"/api/v1/sources/{src['id']}", headers=_headers())


def test_agent_login_no_credentials_skips_autofill(client, monkeypatch):
    """No saved creds => no autofill relay command is dispatched."""
    import app.services.agent_relay as relay

    for row in client.get("/api/v1/sources", headers=_headers()).json():
        if row["domain"] == "agentnofill.example":
            client.delete(f"/api/v1/sources/{row['id']}", headers=_headers())

    src = client.post(
        "/api/v1/sources",
        json={"name": "AgentNoFill", "base_url": "https://agentnofill.example/"},
        headers=_headers(),
    ).json()

    calls = []

    class _FakeRegistry:
        async def dispatch(self, action, params, timeout_s=180):
            calls.append(action)
            return {"ok": True}

    monkeypatch.setattr(relay, "agent_registry", _FakeRegistry())

    r = client.post(f"/api/v1/sources/{src['id']}/agent_login", headers=_headers())
    assert r.status_code == 200, r.text
    assert "autofill_login" not in calls
    assert r.json()["autofill"] == "skipped-no-credentials"

    client.delete(f"/api/v1/sources/{src['id']}", headers=_headers())


def test_clear_session_keeps_credentials(client):
    """DELETE /session wipes the session but preserves saved credentials so the
    next search can auto re-login."""
    for row in client.get("/api/v1/sources", headers=_headers()).json():
        if row["domain"] == "keepcreds.example":
            client.delete(f"/api/v1/sources/{row['id']}", headers=_headers())

    src = client.post(
        "/api/v1/sources",
        json={"name": "KeepCreds", "base_url": "https://keepcreds.example/"},
        headers=_headers(),
    ).json()
    client.post(
        f"/api/v1/sources/{src['id']}/credentials",
        json={"username": "u", "password": "p"},
        headers=_headers(),
    )
    r = client.delete(f"/api/v1/sources/{src['id']}/session", headers=_headers())
    assert r.status_code == 200, r.text
    assert r.json()["has_session"] is False
    assert r.json()["has_credentials"] is True

    client.delete(f"/api/v1/sources/{src['id']}", headers=_headers())

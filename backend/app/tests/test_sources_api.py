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

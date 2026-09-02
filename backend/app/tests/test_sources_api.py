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

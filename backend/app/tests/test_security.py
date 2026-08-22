"""Tests for API security: auth + rate limiting + error envelope."""

import importlib
import os

import pytest
from fastapi.testclient import TestClient

# Set test env vars and force-reload modules so the settings take effect
# regardless of previous test imports.
os.environ["API_KEYS"] = "test-key-1:5,test-key-2:3"
os.environ["API_RATE_LIMIT_PER_MIN"] = "10"

from app.api import security as _security_mod

importlib.reload(_security_mod)

# Force settings to re-read env (clear the lru_cache populated by earlier
# test modules that imported app.config with the live .env).
from app.config import get_settings

get_settings.cache_clear()
from app.main import app


@pytest.fixture(scope="module")
def client():
    # Reset the key store so it picks up the env config.
    from app.api import security

    security._key_store = None
    with TestClient(app) as c:
        yield c


def test_health_is_public(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200


def test_missing_key_rejected(client):
    r = client.post("/api/v1/browser/sessions")
    assert r.status_code == 401
    body = r.json()
    assert body["error"]["code"] == "unauthorized"


def test_invalid_key_rejected(client):
    r = client.post(
        "/api/v1/browser/sessions", headers={"X-API-Key": "wrong-key"}
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_valid_key_accepted(client):
    r = client.post(
        "/api/v1/browser/sessions", headers={"X-API-Key": "test-key-1"}
    )
    assert r.status_code == 201


def test_validation_error_envelope(client):
    r = client.post(
        "/api/v1/search/jobs",
        headers={"X-API-Key": "test-key-1"},
        json={},  # missing required 'query'
    )
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["details"]


def test_rate_limit_429(client):
    """test-key-2 has a limit of 3/min -> 4th request is 429."""
    for _ in range(3):
        r = client.post(
            "/api/v1/browser/sessions", headers={"X-API-Key": "test-key-2"}
        )
        assert r.status_code == 201
    r = client.post(
        "/api/v1/browser/sessions", headers={"X-API-Key": "test-key-2"}
    )
    assert r.status_code == 429
    body = r.json()
    assert body["error"]["code"] == "rate_limited"
    assert "Retry-After" in r.headers

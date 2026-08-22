"""Tests for session capture/replay: encryption round-trip + state filtering."""

import json

from app.services.encryption import decrypt_session_state, encrypt_session_state
from app.services.session import _earliest_expiry, _filter_state


def test_encrypt_decrypt_round_trip():
    state = json.dumps({
        "cookies": [{"name": "li_at", "value": "secret", "domain": ".linkedin.com", "expires": 9999999999}],
        "origins": [],
    })
    blob = encrypt_session_state(state)
    assert blob != state  # encrypted, not plaintext
    assert "li_at" not in blob  # no plaintext cookie names
    assert "secret" not in blob
    decrypted = decrypt_session_state(blob)
    assert json.loads(decrypted) == json.loads(state)


def test_encrypted_blob_changes_each_time():
    """AES-GCM uses a random nonce, so two encryptions differ."""
    state = json.dumps({"cookies": [{"name": "a", "value": "b"}]})
    b1 = encrypt_session_state(state)
    b2 = encrypt_session_state(state)
    assert b1 != b2
    # Both still decrypt correctly.
    assert decrypt_session_state(b1) == state
    assert decrypt_session_state(b2) == state


def test_filter_state_keeps_target_domain_only():
    state = {
        "cookies": [
            {"name": "li_at", "domain": ".linkedin.com"},
            {"name": "session", "domain": ".google.com"},
            {"name": "other", "domain": "www.linkedin.com"},
        ],
        "origins": [
            {"origin": "https://www.linkedin.com"},
            {"origin": "https://www.google.com"},
        ],
    }
    filtered = _filter_state(state, ["linkedin.com", "www.linkedin.com"])
    names = [c["name"] for c in filtered["cookies"]]
    assert "li_at" in names
    assert "other" in names
    assert "session" not in names  # google cookie dropped
    origins = [o["origin"] for o in filtered["origins"]]
    assert "https://www.linkedin.com" in origins
    assert "https://www.google.com" not in origins


def test_earliest_expiry():
    state = {
        "cookies": [
            {"name": "a", "expires": 1_800_000_000},
            {"name": "b", "expires": 1_700_000_000},  # earliest
            {"name": "session", "expires": -1},  # session cookie, ignored
        ]
    }
    exp = _earliest_expiry(state)
    assert exp is not None
    assert int(exp.timestamp()) == 1_700_000_000


def test_earliest_expiry_none_when_all_session():
    state = {"cookies": [{"name": "a", "expires": -1}, {"name": "b", "expires": 0}]}
    assert _earliest_expiry(state) is None

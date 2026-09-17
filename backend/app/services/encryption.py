"""Encrpytion service for browser session state (cookies + localStorage).

Uses AES-256-GCM to encrypt/decrypt Playwright storage_state blobs before
persisting to the database. The key is from `SESSION_ENCRYPTION_KEY` env var.

Design (spec §7 + §15):
- Encrypt at rest (AES-GCM with random nonce per entry).
- Key is env-only, never in code or repo.
- Decryption key is ephemeral (process memory) — not logged or cached to disk.
- Never log raw cookies or decrypted state.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import get_settings

logger = logging.getLogger(__name__)

# 32 bytes = AES-256 (GCM uses 256-bit keys).
KEY_BYTES = 32
NONCE_BYTES = 12  # GCM standard nonce length


def _derive_key() -> bytes:
    """Get the 32-byte AES key from config or derive a dev key."""
    raw = get_settings().session_encryption_key
    if raw:
        try:
            return bytes.fromhex(raw)
        except ValueError as exc:
            raise ValueError(
                "SESSION_ENCRYPTION_KEY must be 64 hex chars (32 bytes)"
            ) from exc
    # Dev fallback: deterministic key (WARNING: not secure, only for local dev).
    logger.warning(
        "SESSION_ENCRYPTION_KEY not set — using derived dev key. "
        "DO NOT use in production."
    )
    # Derive from hostname + app name so it's stable within a dev session.
    raw = os.uname().nodename + "::career-agent-dev"
    return hashlib.sha256(raw.encode()).digest()


# Cached key (process lifetime).
_cached_key: bytes | None = None


def _get_key() -> bytes:
    global _cached_key
    if _cached_key is None:
        _cached_key = _derive_key()
    return _cached_key


def encrypt_session_state(state_json: str) -> str:
    """Encrypt a JSON string (Playwright storage_state) into a base64 blob.

    Format: base64(nonce || ciphertext || tag)
    Returns a string safe for DB storage.
    """
    key = _get_key()
    nonce = secrets.token_bytes(NONCE_BYTES)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, state_json.encode("utf-8"), None)
    # Concatenate nonce + ciphertext (GCM tag is appended by the library).
    blob = base64.b64encode(nonce + ciphertext).decode("ascii")
    return blob


def decrypt_session_state(blob: str) -> str:
    """Decrypt a base64 blob back into the original JSON string.

    Returns the raw Playwright storage_state JSON (cookies + localStorage).
    """
    key = _get_key()
    data = base64.b64decode(blob)
    nonce = data[:NONCE_BYTES]
    ciphertext = data[NONCE_BYTES:]
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return plaintext.decode("utf-8")


# ---------------------------------------------------------------------------
# Source login credentials (auto re-login)
# ---------------------------------------------------------------------------
# Same AES-256-GCM envelope as session state (base64(nonce || ciphertext ||
# tag)), so operators get one key + one threat model for both secrets. The
# plaintext is a JSON object {"username": ..., "password": ...} — secrets are
# NEVER logged and NEVER echoed back through the API (SourceView exposes only
# `has_credentials`).

def encrypt_credentials(username: str, password: str) -> str:
    """Encrypt a source login credential pair into a base64 blob.

    Format: base64(nonce || ciphertext || tag). Never log the return value.
    """
    payload = json.dumps({"username": username, "password": password})
    return encrypt_session_state(payload)


def decrypt_credentials(blob: str) -> tuple[str, str]:
    """Decrypt a credentials blob into (username, password).

    Raises TypeError when the blob is not a well-formed credential pair so the
    caller can fall back to the manual re-login banner instead of crashing.
    """
    raw = decrypt_session_state(blob)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Stored credentials blob is not valid JSON") from exc
    username = data.get("username")
    password = data.get("password")
    if not isinstance(username, str) or not isinstance(password, str):
        raise TypeError("Stored credentials blob is missing username/password")
    return username, password
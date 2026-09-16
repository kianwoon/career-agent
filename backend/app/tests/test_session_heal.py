"""Tests for extension cookie capture filtering and pre-search session self-heal.

Covers: relevant-domain filtering (FastJobs keeps the employer host), Cloudflare
cookie stripping for CF-protected profiles, the never-empty fallback, earliest
expiry computation, session_is_stale boundaries, and best-effort self-heal
(cookies stored when the relay answers; flow proceeds unchanged when it fails).
"""

from datetime import UTC, datetime, timedelta

from app.services.session import (
    prepare_source_cookies,
    self_heal_source_session,
    session_is_stale,
)


class _FakeSource:
    def __init__(self, domain, base_url=None, profile=None, session_state=None,
                 captured_at=None, expires_at=None, name="src"):
        self.domain = domain
        self.base_url = base_url or f"https://{domain}/"
        self.profile = profile
        self.session_state = session_state
        self.captured_at = captured_at
        self.expires_at = expires_at
        self.name = name


# --- cookie filtering -------------------------------------------------------


def test_prepare_fastjobs_keeps_employer_drops_cloudflare():
    """fastjobs.io keeps employer.fastjobs.sg cookies and drops CF cookies
    (profile is cloudflare_protected), and computes the earliest expiry."""
    src = _FakeSource("fastjobs.io", profile=None)  # builtin .io profile is CF
    cookies = [
        {"name": "auth", "domain": ".fastjobs.io", "expires": 1_800_000_000},
        {"name": "employer_sess", "domain": "employer.fastjobs.sg", "expires": 1_700_000_000},
        {"name": "cf_clearance", "domain": ".fastjobs.io", "expires": 1_900_000_000},
        {"name": "__cf_bm", "domain": ".fastjobs.sg", "expires": 1_900_000_000},
        {"name": "unrelated", "domain": ".example.com", "expires": 1_800_000_000},
    ]
    filtered, expires_at = prepare_source_cookies(cookies, src)
    names = {c["name"] for c in filtered}
    assert "employer_sess" in names
    assert "auth" in names
    assert "cf_clearance" not in names
    assert "__cf_bm" not in names
    assert "unrelated" not in names
    assert int(expires_at.timestamp()) == 1_700_000_000


def test_prepare_filter_fallback_keeps_unfiltered_when_empty():
    """A filter that would drop a NON-empty list keeps the unfiltered list."""
    src = _FakeSource("fastjobs.sg", profile=None)
    cookies = [
        {"name": "x", "domain": ".totally-unrelated.example", "expires": 1_700_000_000},
    ]
    filtered, expires_at = prepare_source_cookies(cookies, src)
    assert [c["name"] for c in filtered] == ["x"]
    assert int(expires_at.timestamp()) == 1_700_000_000


def test_prepare_non_cloudflare_profile_keeps_cf_cookies():
    """A non-CF profile must NOT strip cf_clearance."""
    src = _FakeSource("mcf.example", profile={"cloudflare_protected": False})
    cookies = [{"name": "cf_clearance", "domain": ".mcf.example", "expires": 1_700_000_000}]
    filtered, _ = prepare_source_cookies(cookies, src)
    assert any(c["name"] == "cf_clearance" for c in filtered)


def test_prepare_all_session_cookies_expiry_none():
    src = _FakeSource("fastjobs.sg", profile=None)
    cookies = [{"name": "a", "domain": ".fastjobs.sg", "expires": -1}]
    _, expires_at = prepare_source_cookies(cookies, src)
    assert expires_at is None


# --- session_is_stale -------------------------------------------------------


def test_session_is_stale_none_state():
    assert session_is_stale(_FakeSource("x.example")) is True


def test_session_is_stale_fresh_future_expiry():
    src = _FakeSource(
        "x.example",
        session_state="blob",
        captured_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    assert session_is_stale(src) is False


def test_session_is_stale_old_capture():
    src = _FakeSource(
        "x.example",
        session_state="blob",
        captured_at=datetime.now(UTC) - timedelta(hours=25),
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    assert session_is_stale(src) is True


def test_session_is_stale_expiry_within_lead():
    src = _FakeSource(
        "x.example",
        session_state="blob",
        captured_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=6),
    )
    assert session_is_stale(src) is True


# --- self-heal (best-effort) ------------------------------------------------


class _FakeDb:
    def __init__(self):
        self.committed = False

    async def commit(self):
        self.committed = True


class _FakeRegistry:
    def __init__(self, cookies=None, raise_exc=None):
        self.cookies = cookies
        self.raise_exc = raise_exc
        self.calls = []

    async def dispatch(self, action, params, timeout_s=20):
        self.calls.append((action, params))
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.cookies


async def test_self_heal_stores_cookies(monkeypatch):
    """A stale source + relay returning cookies updates session_state + proceeds."""
    from app.services import session as sess

    stale = datetime.now(UTC) - timedelta(hours=30)
    src = _FakeSource(
        "fastjobs.io",
        profile=None,
        session_state="old-blob",
        captured_at=stale,
        expires_at=None,
    )
    fake = _FakeRegistry(cookies=[{"name": "a", "domain": ".fastjobs.io", "expires": 1_900_000_000}])
    monkeypatch.setattr("app.services.agent_relay.agent_registry", fake, raising=False)
    db = _FakeDb()
    healed = await self_heal_source_session(src, db, "https://employer.fastjobs.sg/p/talent/search/")
    assert healed is True
    assert src.session_state != "old-blob"
    assert db.committed is True
    assert any(c == "get_cookies" for c, _ in fake.calls)
    # Sanity: encryption round-trips and holds the cookie.
    state = __import__("json").loads(sess.decrypt_session_state(src.session_state))
    assert any(c["name"] == "a" for c in state["cookies"])


async def test_self_heal_relay_unavailable_proceeds(monkeypatch):
    """RuntimeError from the relay is swallowed; session unchanged."""
    src = _FakeSource(
        "fastjobs.io",
        profile=None,
        session_state="old-blob",
        captured_at=datetime.now(UTC) - timedelta(hours=30),
        expires_at=None,
    )
    fake = _FakeRegistry(raise_exc=RuntimeError("no agent"))
    monkeypatch.setattr("app.services.agent_relay.agent_registry", fake, raising=False)
    db = _FakeDb()
    healed = await self_heal_source_session(src, db, "https://employer.fastjobs.sg/")
    assert healed is False
    assert src.session_state == "old-blob"
    assert db.committed is False


async def test_self_heal_empty_cookies_proceeds(monkeypatch):
    """An empty capture leaves the stored session untouched (no false flip)."""
    src = _FakeSource(
        "fastjobs.io",
        profile=None,
        session_state="old-blob",
        captured_at=datetime.now(UTC) - timedelta(hours=30),
        expires_at=None,
    )
    fake = _FakeRegistry(cookies=[])
    monkeypatch.setattr("app.services.agent_relay.agent_registry", fake, raising=False)
    db = _FakeDb()
    healed = await self_heal_source_session(src, db, "https://employer.fastjobs.sg/")
    assert healed is False
    assert src.session_state == "old-blob"
    assert db.committed is False


async def test_self_heal_skips_fresh_session(monkeypatch):
    """A fresh session is never re-captured (no relay call)."""
    src = _FakeSource(
        "fastjobs.io",
        profile=None,
        session_state="blob",
        captured_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=10),
    )
    fake = _FakeRegistry(cookies=[{"name": "a", "domain": ".fastjobs.io"}])
    monkeypatch.setattr("app.services.agent_relay.agent_registry", fake, raising=False)
    db = _FakeDb()
    healed = await self_heal_source_session(src, db, "https://employer.fastjobs.sg/")
    assert healed is False
    assert fake.calls == []


async def test_self_heal_skips_anonymous(monkeypatch):
    """Anonymous sources need no session — no relay call."""
    src = _FakeSource(
        "public.example",
        profile={"auth_model": "anonymous"},
        session_state=None,
    )
    fake = _FakeRegistry(cookies=[{"name": "a", "domain": ".public.example"}])
    monkeypatch.setattr("app.services.agent_relay.agent_registry", fake, raising=False)
    db = _FakeDb()
    healed = await self_heal_source_session(src, db, "https://public.example/")
    assert healed is False
    assert fake.calls == []

"""Tests for LLM rerank JSON salvage and LinkedIn agent-busy skip."""

from __future__ import annotations

import json

import pytest

from app.services import linkedin as li
from app.services import linkedin_people as lp
from app.services.llm import LLMService


@pytest.fixture
def svc():
    return LLMService.__new__(LLMService)


def _record(i: int) -> dict:
    return {"id": f"cand-{i}", "score": 0.5 + i / 10, "reason": f"reason {i}"}


def test_valid_array_unchanged(svc):
    arr = [_record(i) for i in range(3)]
    assert svc._parse_rerank_json(json.dumps(arr)) == arr


def test_truncated_mid_record_salvages(svc):
    # Live failure shape: array cut mid-"score" field (char ~3749).
    records = [_record(i) for i in range(50)]
    text = json.dumps(records)
    truncated = text[:3749]
    assert truncated.rstrip().endswith(('"', ":", ",", "-", "0", "1")) or ":" in truncated[-20:]
    out = svc._parse_rerank_json(truncated)
    ids = [r["id"] for r in out]
    assert ids == [f"cand-{i}" for i in range(len(ids))]
    assert 40 <= len(out) <= 50


def test_markdown_fenced_truncated_salvages(svc):
    records = [_record(i) for i in range(10)]
    body = json.dumps(records)[:180]
    assert svc._parse_rerank_json(f"```json\n{body}\n```") == [_record(i) for i in range(3)]


def test_pure_garbage_raises(svc):
    with pytest.raises(ValueError):
        svc._parse_rerank_json("total nonsense no json here")


# ---------------------------------------------------------------------------
# LinkedIn agent-busy path
# ---------------------------------------------------------------------------


class _FakeRegistry:
    connected = True

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def dispatch(self, action, params, timeout_s=180):
        raise self._exc


@pytest.mark.asyncio
async def test_busy_dispatch_skips_leg(monkeypatch):
    import app.services.agent_relay as relay

    monkeypatch.setattr(relay, "agent_registry", _FakeRegistry(RuntimeError("Agent busy — dispatch lock not released within 60s")))
    result = await lp.search_linkedin_people(queries=["test"], excludes=[], location="Singapore")
    assert result["needs_human"] is False
    assert "busy" in (result.get("plan_detail") or "")


@pytest.mark.asyncio
async def test_offline_dispatch_raises_browser_error(monkeypatch):
    import app.services.agent_relay as relay

    monkeypatch.setattr(relay, "agent_registry", _FakeRegistry(RuntimeError("websocket dropped")))

    async def fake_connect():
        return None, None

    monkeypatch.setattr(li, "_connect_with_best_session", fake_connect)
    with pytest.raises(lp.BrowserError):
        await lp.search_linkedin_people(queries=["test"], excludes=[], location="Singapore")

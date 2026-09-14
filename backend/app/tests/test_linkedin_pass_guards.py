"""Regression tests for widen-pass budget guards in `search_linkedin_people`.

Production failure (Koyeb): a late widen pass (relaxed OR-group / broad) was
dispatched with only ~33s of the global budget left and raised
`RuntimeError("Agent command 'linkedin_people_plan' timed out after 33s ...")`,
which propagated to run_search and PAUSED the task with a useless reason.

Fix pinned here:
- a widen pass is SKIPPED when its computed timeout < MIN_PASS_TIMEOUT_S
  (the extension paces 6-11s BEFORE EACH query — a sub-90s budget is a
  guaranteed timeout);
- a widen dispatch that still fails is SWALLOWED (logged, degraded to the
  partial results already collected) and never sets needs_human.

The dispatch hook is patched on `app.services.agent_relay.agent_registry`
(the function imports that name at call time).
"""

from __future__ import annotations

from time import monotonic
from typing import Any

import pytest

from app.services import linkedin_people as lp

# A plan query with a leading OR-group so the relaxed/broad variants exist,
# mirroring the live over-constrained plans that triggered the pause.
PLAN_QUERY = '"agency accounting" AND (insurance OR "real estate")'


class FakeRegistry:
    """Minimal stand-in for AgentRegistry: connected + local dispatch."""

    def __init__(self, handler) -> None:
        self.connected = True
        self.calls: list[tuple[str, dict[str, Any], float]] = []
        self._handler = handler

    async def dispatch(self, action, params, timeout_s=180):
        self.calls.append((action, params, timeout_s))
        result = self._handler(action, params)
        if isinstance(result, Exception):
            raise result
        return result

    def plan_calls(self) -> int:
        return sum(1 for a, _, _ in self.calls if a == "linkedin_people_plan")


def _row(url: str, headline: str) -> dict[str, Any]:
    """Card-level row that is already "enriched" (education present).

    The enrichment marker keys make the final top-up (`linkedin_people_enrich`)
    a no-op, so tests assert only on the plan dispatches under test.
    """
    return {
        "id": url,
        "name": headline.split()[0],
        "headline": headline,
        "source": "linkedin_people",
        "source_url": url,
        "education": [],
        "certifications": None,
        "_hit_count": 1,
    }


def _install(monkeypatch, handler) -> FakeRegistry:
    fake = FakeRegistry(handler)
    import app.services.agent_relay as relay

    monkeypatch.setattr(relay, "agent_registry", fake)
    monkeypatch.setattr(lp, "agent_registry", fake, raising=False)
    return fake


@pytest.mark.asyncio
async def test_relaxed_pass_skipped_when_budget_cannot_finish_it(monkeypatch):
    """Remaining budget < MIN_PASS_TIMEOUT_S → relaxed pass is not dispatched."""
    primary = _row("https://www.linkedin.com/in/primary/", "agency accounting")

    def handler(action, params):
        return {
            "raw_results": [primary],
            "needs_human": False,
            "human_reason": None,
            "plan_detail": "primary",
        }

    fake = _install(monkeypatch, handler)
    # ~80s remain after pass 1: enough to avoid _budget_gone() (15s) but far
    # below the 6-11s-per-query pacing floor the extension needs.
    result = await lp.search_linkedin_people(
        queries=[PLAN_QUERY], excludes=[], location="", deadline=monotonic() + 80
    )

    assert fake.plan_calls() == 1  # only the primary pass ran
    assert result["needs_human"] is False
    assert result["human_reason"] is None
    assert result["raw_results"] == [primary]
    assert "relaxed skipped" in (result["plan_detail"] or "")


@pytest.mark.asyncio
async def test_relaxed_timeout_is_swallowed_not_fatal(monkeypatch):
    """A relaxed dispatch that RAISES must degrade, not pause the task."""
    primary = _row("https://www.linkedin.com/in/primary/", "agency accounting")

    def handler(action, params):
        if " AND " in params["queries"][0].upper():
            return {
                "raw_results": [primary],
                "needs_human": False,
                "human_reason": None,
                "plan_detail": "primary",
            }
        raise RuntimeError(
            "Agent command 'linkedin_people_plan' timed out after 33s "
            "(is the browser open?)"
        )

    fake = _install(monkeypatch, handler)
    result = await lp.search_linkedin_people(
        queries=[PLAN_QUERY], excludes=[], location="", deadline=monotonic() + 1000
    )

    assert fake.plan_calls() == 2  # primary + relaxed (broad never reached)
    assert result["needs_human"] is False
    assert result["human_reason"] is None
    assert result["raw_results"] == [primary]
    assert "relaxed failed" in (result["plan_detail"] or "")


@pytest.mark.asyncio
async def test_broad_timeout_is_swallowed_not_fatal(monkeypatch):
    """A broad (third) dispatch that RAISES must degrade, not pause the task.

    Note: the broad pass only runs when the merged set is still empty, so the
    primary and relaxed passes must both return zero rows here — preserving
    partial results in this path means returning an empty (not paused) result.
    """
    seen = {"n": 0}

    def handler(action, params):
        seen["n"] += 1
        if seen["n"] == 1:  # primary pass (strict AND)
            return {
                "raw_results": [],
                "needs_human": False,
                "human_reason": None,
                "plan_detail": "primary",
            }
        if seen["n"] == 2:  # relaxed pass
            return {
                "raw_results": [],
                "needs_human": False,
                "human_reason": None,
                "plan_detail": "relaxed",
            }
        raise RuntimeError(
            "Agent command 'linkedin_people_plan' timed out after 33s "
            "(is the browser open?)"
        )

    fake = _install(monkeypatch, handler)
    result = await lp.search_linkedin_people(
        queries=[PLAN_QUERY], excludes=[], location="", deadline=monotonic() + 1000
    )

    assert fake.plan_calls() == 3  # primary + relaxed + broad
    assert result["needs_human"] is False
    assert result["human_reason"] is None
    assert result["raw_results"] == []
    assert "broad failed" in (result["plan_detail"] or "")


@pytest.mark.asyncio
async def test_normal_path_still_widens(monkeypatch):
    """With budget to spare, a successful relaxed pass gates + merges rows."""
    relaxed_row = _row(
        "https://www.linkedin.com/in/relaxed/",
        "agency accounting analyst, insurance desk",
    )

    def handler(action, params):
        qs = params["queries"]
        if " AND " in qs[0].upper():
            return {
                "raw_results": [],
                "needs_human": False,
                "human_reason": None,
                "plan_detail": "primary",
            }
        return {
            "raw_results": [relaxed_row],
            "needs_human": False,
            "human_reason": None,
            "plan_detail": "relaxed",
        }

    fake = _install(monkeypatch, handler)
    result = await lp.search_linkedin_people(
        queries=[PLAN_QUERY], excludes=[], location="", deadline=monotonic() + 1000
    )

    assert fake.plan_calls() == 2  # primary + relaxed (combined non-empty → no broad)
    assert result["needs_human"] is False
    assert [r["source_url"] for r in result["raw_results"]] == [
        "https://www.linkedin.com/in/relaxed/"
    ]
    assert "+ relaxed" in (result["plan_detail"] or "")
    assert "failed" not in (result["plan_detail"] or "")
    assert "skipped" not in (result["plan_detail"] or "")

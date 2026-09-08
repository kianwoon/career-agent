"""Extension-transport tests: LinkedIn people + jobs via agent_registry dispatch.

The backend must dispatch `linkedin_people_plan` / `linkedin_jobs_search`
commands to the extension queue and consume the returned rows. CDP remains
only as fallback when no extension is connected.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.services import linkedin as li
from app.services import linkedin_people as lp
from app.services.agent_relay import AgentRegistry

# ---------------------------------------------------------------------------
# Test double: an AgentRegistry whose commands are answered locally
# ---------------------------------------------------------------------------


class FakeExtension(AgentRegistry):
    """Simulates the extension: resolves dispatched commands immediately."""

    def __init__(self, handler) -> None:
        super().__init__()
        self._handler = handler
        self.actions: list[tuple[str, dict[str, Any]]] = []
        import time as _time

        # Mark the fake as "connected" so services take the extension path.
        self.last_poll_ts = _time.time()

    async def dispatch(self, action, params, timeout_s=180):
        self.actions.append((action, params))
        result = self._handler(action, params)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.mark.asyncio
async def test_people_plan_via_extension(monkeypatch):
    """search_linkedin_people dispatches one plan command and returns rows."""
    rows = [
        {
            "id": "li-people-ext-1",
            "name": "Sher Rien L.",
            "headline": "Manager, Agency Accounting",
            "location": "Singapore",
            "summary": "x",
            "skills": [],
            "source": "linkedin_people",
            "source_url": "https://www.linkedin.com/in/sher-rien/",
            "experience": "x",
            "_hit_count": 1,
        },
        {
            "id": "li-people-ext-2",
            "name": "Overseas Person",
            "location": "Holland, Michigan, United States",
            "source": "linkedin_people",
            "source_url": "https://www.linkedin.com/in/overseas/",
            "_hit_count": 1,
        },
    ]

    async def fake_connect():
        return None, None

    monkeypatch.setattr(li, "_connect_with_best_session", fake_connect)
    fake = FakeExtension(lambda a, p: {"raw_results": rows, "needs_human": False, "human_reason": None, "plan_detail": "plan ran"})
    monkeypatch.setattr(lp, "agent_registry", fake, raising=False)
    import app.services.agent_relay as relay

    monkeypatch.setattr(relay, "agent_registry", fake)

    result = await lp.search_linkedin_people(
        queries=['"agency accounting" Singapore'],
        excludes=["intern"],
        location="Singapore",
    )

    assert result["needs_human"] is False
    assert len(result["raw_results"]) == 2
    # Excludes are folded into the dispatched queries server-side.
    action, params = fake.actions[0]
    assert action == "linkedin_people_plan"
    assert "NOT" in params["queries"][0]
    assert params["location"] == "Singapore"


@pytest.mark.asyncio
async def test_jobs_via_extension(monkeypatch):
    """search_linkedin_jobs dispatches linkedin_jobs_search and maps rows."""

    def handler(action, params):
        assert action == "linkedin_jobs_search"
        assert params["query"] == "senior accountant"
        assert params["maxJobs"] == li.MAX_JOBS
        return {
            "raw_results": [
                {
                    "id": "li-ext-1",
                    "title": "Senior Accountant",
                    "company": "ACME",
                    "location": "Singapore",
                    "salary_text": "SGD 5,000",
                    "description": "About the job ...",
                    "source": "linkedin",
                    "source_url": "https://www.linkedin.com/jobs/view/1/",
                    "posted_at": "2 days ago",
                    "metadata_footer": "2 days ago",
                }
            ],
            "needs_human": False,
            "human_reason": None,
        }

    async def fake_connect():
        return None, None

    monkeypatch.setattr(li, "_connect_with_best_session", fake_connect)
    li.query_cache.clear()
    fake = FakeExtension(handler)
    import app.services.agent_relay as relay

    monkeypatch.setattr(relay, "agent_registry", fake)

    result = await li.search_linkedin_jobs("senior accountant", "Singapore")
    assert result["needs_human"] is False
    assert result["raw_results"][0]["title"] == "Senior Accountant"
    assert result["raw_results"][0]["source"] == "linkedin"


@pytest.mark.asyncio
async def test_people_plan_relaxed_second_pass(monkeypatch):
    """Sparse strict results trigger a relaxed OR-group second dispatch."""

    def handler(action, params):
        qs = params["queries"]
        if any(" AND " in q.upper() for q in qs):
            return {"raw_results": [], "needs_human": False, "human_reason": None, "plan_detail": "strict"}
        return {
            "raw_results": [
                {
                    "id": f"r{i}",
                    "name": f"Person {i}",
                    "location": "Singapore",
                    "source": "linkedin_people",
                    "source_url": f"https://www.linkedin.com/in/p{i}/",
                    # Relaxed rows must pass the relevance gate: match both
                    # plan OR-groups ('agency accounting' + 'insurance').
                    "headline": f"agency accounting specialist {i}, insurance desk",
                    "_hit_count": 1,
                }
                for i in range(5)
            ],
            "needs_human": False,
            "human_reason": None,
            "plan_detail": "relaxed",
        }

    async def fake_connect():
        return None, None

    monkeypatch.setattr(li, "_connect_with_best_session", fake_connect)
    fake = FakeExtension(handler)
    import app.services.agent_relay as relay

    monkeypatch.setattr(relay, "agent_registry", fake)

    result = await lp.search_linkedin_people(
        queries=['"agency accounting" AND (insurance OR "real estate")'],
        excludes=[],
        location="Singapore",
    )
    # Three dispatches: strict pass, relaxed pass, then the enrichment
    # top-up (the fake relaxed rows carry no education/certifications).
    assert len(fake.actions) == 3
    assert fake.actions[1][0] == "linkedin_people_plan"
    # Relaxed pass gets the OR-group text, NOT the strict AND query.
    assert " AND " not in fake.actions[1][1]["queries"][0].upper()
    assert "insurance" in fake.actions[1][1]["queries"][0]
    # Pass 1 returned zero rows → relaxed pass MUST carry the full enrich
    # budget (unriched card-text rows make 2nd-round assessment impossible).
    assert fake.actions[1][1]["enrichBudget"] == lp.ENRICH_BUDGET
    assert len(result["raw_results"]) == 5
    assert "relaxed" in (result.get("plan_detail") or "")


@pytest.mark.asyncio
async def test_unenriched_rows_get_enrichment_topup(monkeypatch):
    """Merged rows lacking profile sections trigger a linkedin_people_enrich
    top-up so 2nd-round assessment has real About/skills/experience data."""

    def handler(action, params):
        if action == "linkedin_people_enrich":
            out = []
            for c in params["candidates"]:
                c = {**c, "education": "NUS", "certifications": "CPA"}
                out.append(c)
            return {"candidates": out}
        qs = params["queries"]
        if any(" AND " in q.upper() for q in qs):
            return {"raw_results": [], "needs_human": False, "human_reason": None, "plan_detail": "strict"}
        return {
            "raw_results": [
                {
                    "id": f"r{i}",
                    "name": f"Person {i}",
                    "location": "Singapore",
                    "source": "linkedin_people",
                    "source_url": f"https://www.linkedin.com/in/p{i}/",
                    # Relaxed rows must pass the relevance gate.
                    "headline": f"agency accounting specialist {i}, insurance desk",
                    "summary": "card text only",
                    "_hit_count": 1,
                }
                for i in range(3)
            ],
            "needs_human": False,
            "human_reason": None,
            "plan_detail": "relaxed",
        }

    async def fake_connect():
        return None, None

    monkeypatch.setattr(li, "_connect_with_best_session", fake_connect)
    fake = FakeExtension(handler)
    import app.services.agent_relay as relay

    monkeypatch.setattr(relay, "agent_registry", fake)

    result = await lp.search_linkedin_people(
        queries=['"agency accounting" AND (insurance OR "real estate")'],
        excludes=[],
        location="Singapore",
    )
    actions = [a for a, _ in fake.actions]
    assert "linkedin_people_enrich" in actions
    assert all(r.get("education") == "NUS" for r in result["raw_results"])
    assert "enrichment top-up" in (result.get("plan_detail") or "")


@pytest.mark.asyncio
async def test_extension_blocker_surfaces_needs_human(monkeypatch):
    """A login wall reported by the extension becomes needs_human upstream."""

    async def fake_connect():
        return None, None

    monkeypatch.setattr(li, "_connect_with_best_session", fake_connect)
    fake = FakeExtension(
        lambda a, p: {"raw_results": [], "needs_human": True, "human_reason": "LinkedIn login wall / session expired"}
    )
    import app.services.agent_relay as relay

    monkeypatch.setattr(relay, "agent_registry", fake)

    result = await lp.search_linkedin_people(queries=["test"], excludes=[], location="Singapore")
    assert result["needs_human"] is True
    assert "login wall" in (result.get("human_reason") or "")


@pytest.mark.asyncio
async def test_fallback_to_cdp_when_no_extension(monkeypatch):
    """With no extension connected, the original CDP path still runs."""

    async def fake_run_plan(queries, excludes, location, session):
        return {"raw_results": [{"name": "cdp"}], "needs_human": False, "human_reason": None}

    monkeypatch.setattr(lp, "_run_plan", fake_run_plan)

    async def fake_connect():
        return None, None

    monkeypatch.setattr(li, "_connect_with_best_session", fake_connect)

    import app.services.agent_relay as relay

    # Not connected: last_poll_ts stays None.
    assert relay.agent_registry.connected is False

    result = await lp.search_linkedin_people(queries=["q"], excludes=[], location=None)
    assert result["raw_results"] == [{"name": "cdp"}]


# -- orphan purge vs long-running claimed commands ----------------------------

def _registry_with(cmd):
    """Fresh AgentRegistry with one command pre-staged in pending."""
    import app.services.agent_relay as relay

    reg = relay.AgentRegistry()
    reg.last_poll_ts = None
    reg.pending = [cmd]
    return reg


@pytest.mark.asyncio
async def test_long_claimed_command_not_purged_before_timeout():
    """A claimed 450s command still pending at age 300s must NOT be purged
    (the extension is still executing it — purging it made postResult hit an
    unknown id and killed task 3ce3caba)."""
    import app.services.agent_relay as relay

    cmd = relay.Command(id="cmd-long", action="linkedin_people_plan", params={}, timeout_s=450.0)
    cmd.claimed = True
    cmd.enqueued_at -= 300.0
    reg = _registry_with(cmd)

    assert reg.poll() is None  # claimed, so nothing to hand out...
    assert any(c.id == "cmd-long" for c in reg.pending)  # ...but NOT purged

    # After 460s (> its 450s timeout) it IS purged.
    cmd.enqueued_at -= 160.0
    assert reg.poll() is None
    assert not any(c.id == "cmd-long" for c in reg.pending)


@pytest.mark.asyncio
async def test_default_command_purged_after_180s():
    """Default 180s commands keep the existing purge behavior."""
    import app.services.agent_relay as relay

    cmd = relay.Command(id="cmd-short", action="open_tab", params={})
    cmd.claimed = True
    cmd.enqueued_at -= 179.0
    reg = _registry_with(cmd)
    assert reg.poll() is None
    assert any(c.id == "cmd-short" for c in reg.pending)

    cmd.enqueued_at -= 2.0
    reg.poll()
    assert not any(c.id == "cmd-short" for c in reg.pending)


@pytest.mark.asyncio
async def test_dispatch_records_per_command_timeout():
    """dispatch() stamps the caller's timeout_s onto the queued Command."""
    import app.services.agent_relay as relay

    reg = relay.AgentRegistry()

    async def fake_wait(self, timeout_s=20.0):
        return None

    real_wait = relay.AgentRegistry.wait_for_agent
    relay.AgentRegistry.wait_for_agent = fake_wait
    try:
        task = asyncio.create_task(reg.dispatch("linkedin_people_plan", {}, timeout_s=450.0))
        await asyncio.sleep(0.01)
        assert len(reg.pending) == 1
        assert reg.pending[0].timeout_s == 450.0
        reg.resolve(reg.pending[0].id, ok=True, data={"ok": True})
        assert await task == {"ok": True}
    finally:
        relay.AgentRegistry.wait_for_agent = real_wait

"""Tests for candidate search helpers and scoring."""

import pytest

from app.agent.nodes import _extract_skills, _normalize_flow_candidate
from app.services.matching import score_candidate


def test_extract_skills_from_query():
    skills = _extract_skills("Java, Kafka, payments, microservices, banking experience")
    assert "java" in skills
    assert "kafka" in skills
    assert "payments" in skills
    assert "experience" not in skills  # filler removed


def test_score_candidate_against_job_reference():
    candidate = {
        "id": "c1",
        "name": "Jane Doe",
        "headline": "Senior Java Engineer",
        "location": "Singapore",
        "skills": ["java", "kafka", "payments", "microservices"],
        "source": "linkedin_people",
        "source_url": "https://linkedin.com/in/jane",
        "experience": "Built payments platforms with Java and Kafka at a bank.",
    }
    job_ref = {
        "description": "Java, Kafka, payments, microservices, banking",
        "location": "Singapore",
        "required_skills": ["java", "kafka", "payments", "microservices"],
    }
    result = score_candidate(candidate, job_ref)
    assert result.match_score > 50
    assert result.title == "Jane Doe"
    assert any(e.field == "mandatory_skills" for e in result.evidence)
    assert result.gaps == []  # all required skills matched


def test_normalize_flow_candidate_splits_glued_name_blob():
    """SEEK cards without links return the whole card as one camelCase-glued
    blob ('Tang Yee HennSenior QC Technician …'). The name must be split
    out — never stored as a 255-char+ blob that kills the DB insert."""
    blob = (
        "Tang Yee HennSenior QC Technician (Deputy Shift Lead) at "
        "Thermo Fisher Scientific Aug 2022 - Present (4 years 2 months)"
        "Research Assistant at Singapore Institute of Manufacturing "
        "Technology (A*STAR) Sep 2020 - Apr 2021 (8 months)"
    )
    out = _normalize_flow_candidate(
        {"title": "", "raw_text": blob},
        "jobstreet - candidate",
        0,
        "https://sg.employer.seek.com/talentsearch/search/profiles",
    )
    assert out is not None
    assert out["name"] == "Tang Yee Henn"
    assert len(out["name"]) <= 255
    assert "Senior QC Technician" in (out["headline"] or "")
    assert "uncoupledFreeText=Tang%20Yee%20Henn" in (out["source_url"] or "")


def test_normalize_flow_candidate_clamps_long_fields():
    out = _normalize_flow_candidate(
        {"title": "x" * 900, "location": "y" * 900, "raw_text": ""},
        "jobstreet - candidate",
        0,
    )
    assert out is not None
    assert len(out["name"]) <= 255
    assert len(out["location"] or "") <= 255


def test_normalize_flow_candidate_splits_glued_role_variants():
    # Loose title text (title field holds the whole card, no camel glue
    # point after the surname) must still split name from role.
    for blob, name in [
        ("Kwong-Meng ChowQC Manager II at Bio-Rad Laboratories Apr 2021 - Feb 2025 (3 years 11 months)", "Kwong-Meng Chow"),
        ("Ho Kiat, Thomas TayQA Supervisor at AbbVie Operations Singapore Jan 2017 - Present (9 years 9 months)", "Ho Kiat, Thomas Tay"),
        ("THINESHWARAN GUNASEKARANLaboratory Technician at Thermo Fisher Scientific Mar 2022 - Apr 2025 (3 years 2 months)", "THINESHWARAN GUNASEKARAN"),
    ]:
        out = _normalize_flow_candidate({"title": blob, "raw_text": blob}, "jobstreet - candidate", 0)
        assert out is not None, blob
        assert out["name"] == name, f"{blob} -> {out['name']}"


def test_normalize_flow_candidate_drops_ui_junk_names():
    junk = "SingaporeSGD 15,000+ monthlyAdd to poolUpdated 11 months agoSend jobSend messageAccess profile"
    assert _normalize_flow_candidate({"title": junk, "raw_text": junk}, "jobstreet - candidate", 0) is None


def test_deduplicate_collapses_seek_search_and_profile_rows():
    from app.agent.nodes import deduplicate

    state = {
        "normalized": [
            {"name": "Tang Yee Henn", "source": "jobstreet - candidate",
             "source_url": "https://sg.employer.seek.com/talentsearch/profile/504751178?x=1"},
            {"name": "Tang Yee Henn", "source": "jobstreet - candidate",
             "source_url": "https://sg.employer.seek.com/talentsearch/search/profiles?uncoupledFreeText=Tang%20Yee%20Henn"},
        ],
        "timeline": [],
    }
    out = deduplicate(state)
    assert len(out["normalized"]) == 1
    assert "/profile/" in out["normalized"][0]["source_url"]


def _cand(n: int) -> dict:
    return {
        "name": f"Candidate {n}",
        "headline": f"Engineer {n}",
        "source": "linkedin_people",
        "source_url": f"https://linkedin.com/in/c{n}",
    }


def test_deduplicate_keeps_distinct_candidates():
    """Regression: candidate rows have no title/company keys, so the fuzzy
    pass keyed everything on ("", "") and collapsed all candidates to one."""
    from app.agent.nodes import deduplicate

    state = {"normalized": [_cand(i) for i in range(5)], "timeline": []}
    out = deduplicate(state)
    assert len(out["normalized"]) == 5
    assert {c["name"] for c in out["normalized"]} == {f"Candidate {i}" for i in range(5)}


def test_deduplicate_collapses_exact_name_dupes_preferring_deep_link():
    from app.agent.nodes import deduplicate

    state = {
        "normalized": [
            _cand(1),
            {**_cand(1),
             "source_url": "https://x.com/search/profiles?uncoupledFreeText=Candidate%201"},
        ],
        "timeline": [],
    }
    out = deduplicate(state)
    assert len(out["normalized"]) == 1
    assert out["normalized"][0]["source_url"] == "https://linkedin.com/in/c1"


def test_normalize_accepts_name_and_sets_title():
    from app.agent.nodes import normalize

    state = {
        "raw_results": [
            {"name": "Jane Doe", "headline": "Java Dev", "source": "linkedin_people"},
            {"name": "", "headline": "", "source": "linkedin_people"},  # junk
        ],
        "timeline": [],
    }
    out = normalize(state)
    assert len(out["normalized"]) == 1
    assert out["normalized"][0]["title"] == "Jane Doe"
    assert out["normalized"][0].get("source_url") == ""


def test_normalize_flow_candidate_rejects_wrapper_source_url():
    """A search/listing wrapper href (SEEK /talentsearch/keyword?...searchQuery=)
    must not pass through as source_url — it becomes the synthesized
    profile-search deep link instead."""
    wrapper = (
        "https://sg.employer.seek.com/talentsearch/keyword?pageNumber=1"
        "&salaryType=MONTHLY&searchQuery=M&searchId=112aa"
    )
    out = _normalize_flow_candidate(
        {"title": "Mak Choy Yin", "url": wrapper, "raw_text": "Mak Choy Yin"},
        "seek - candidate",
        0,
        "https://sg.employer.seek.com/talentsearch/keyword",
    )
    assert out is not None
    su = out["source_url"] or ""
    assert "/keyword" not in su and "searchQuery=" not in su
    assert "uncoupledFreeText=" in su and "Mak%20Choy%20Yin" in su


def test_normalize_flow_candidate_recovers_name_from_short_title():
    """A 1-char title (avatar initial badge) with a real first line in
    raw_text must yield the real line as the name."""
    out = _normalize_flow_candidate(
        {"title": "M", "raw_text": "Mak Choy Yin\nRecruitment Consultant at SEEK"},
        "seek - candidate",
        0,
    )
    assert out is not None
    assert out["name"] == "Mak Choy Yin"


# ---------------------------------------------------------------------------
# _search_candidates_via_flow: per-query navigations (LinkedIn parity)
# ---------------------------------------------------------------------------

class _MultiQueryFakeRegistry:
    """Records run_flow dispatches; returns canned results per query."""

    def __init__(self, rows_per_query: dict[str, list[dict]]):
        self.calls: list[tuple[str, dict]] = []
        self.rows_per_query = rows_per_query

    @property
    def connected(self):
        return True

    async def dispatch(self, cmd, params, timeout_s=30):
        self.calls.append((cmd, params))
        assert cmd == "run_flow"
        for key, rows in self.rows_per_query.items():
            if params["query"].startswith(key):
                return {"results": rows}
        return {"results": []}


class _FakeFlowSource:
    name = "jobstreet - candidate"
    base_url = "https://sg.employer.seek.com/talentsearch"
    session_state = None
    id = 1


class _FakeFlow:
    id = 7
    steps = [{"action": "fill", "selector": "#kw", "param": "query"}]
    status = "active"
    flow_type = "find_candidates"


async def test_flow_search_runs_per_query_legs(monkeypatch):
    """Each plan query gets its OWN run_flow dispatch (parity with LinkedIn's
    per-query navigations); results merge across legs and plan_detail shows
    per-query counts."""
    import app.agent.nodes as nodes_mod

    queries = ["python developer", "data engineer", "devops"]
    registry = _MultiQueryFakeRegistry({
        f'"{q}" NOT (recruiter)' if ' ' in q else f'{q} NOT (recruiter)': [{"title": f"cand-{q[:4]}"}] for q in queries
    })

    async def fake_db():
        raise AssertionError("real DB should not be touched in this test")

    async def fake_get_source_and_flow():
        return _FakeFlowSource(), _FakeFlow()

    class _FakeCtx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def execute(self, *a, **k):
            raise AssertionError("DB not expected")

    async def fake_async_session():
        return _FakeCtx()

    # Stub the two DB lookups by faking async_session + select results.
    async def fake_resolve():
        return _FakeFlowSource(), _FakeFlow()

    async def fake_build(qs, excludes, limit=500):
        from app.services.source_flows import build_boolean_keywords
        return build_boolean_keywords(qs, excludes, truncate=False) or (qs[0] if qs else "")

    monkeypatch.setattr(nodes_mod, "_resolve_flow_source_and_flow", fake_resolve, raising=False)

    # Instead of stubbing internals not extracted, patch db + registry via
    # the modules _search_candidates_via_flow imports lazily.
    import app.db as db_mod
    import app.services.agent_relay as relay_mod

    async def patched_async_session():
        return _FakeDbCtx()

    class _FakeDbCtx:
        pass

    # Simpler: craft fake db returning scalars for the two queries.
    class _FakeResult:
        def scalar_one_or_none(self):
            return _FakeFlowSource()

        def scalars(self):
            return _FakeScalars()

    class _FakeScalars:
        def first(self):
            return _FakeFlow()

    class _FakeDb:
        async def execute(self, *a, **k):
            return _FakeResult()

        async def get(self, *a, **k):
            return None

    class _FakeSessionCtx:
        async def __aenter__(self):
            return _FakeDb()

        async def __aexit__(self, *a):
            return False

    def fake_session():
        return _FakeSessionCtx()

    monkeypatch.setattr(db_mod, "async_session", fake_session)
    monkeypatch.setattr(relay_mod, "agent_registry", registry)

    result = await nodes_mod._search_candidates_via_flow(
        "jobstreet - candidate", queries, excludes=["recruiter"]
    )

    run_flow_calls = [p for c, p in registry.calls if c == "run_flow"]
    assert len(run_flow_calls) == 3
    sent_queries = [p["query"] for p in run_flow_calls]
    assert len(set(sent_queries)) == 3  # one distinct query per leg
    for q in queries:
        assert any(q in sq for sq in sent_queries)
    assert all("NOT (recruiter)" in sq for sq in sent_queries)  # excludes attached per leg

    names = [r["name"] for r in result["raw_results"]]
    assert len(names) == 3
    assert result["needs_human"] is False
    assert "candidate:" in result["plan_detail"]
    for q in queries:
        assert q[:7] in result["plan_detail"]
    assert ": 1" in result["plan_detail"]


async def test_budget_exhaustion_returns_partial_results(monkeypatch):
    """When the candidate-search time budget is already expired, run_search
    completes normally with partial (empty) results and a budget source_issue
    — it never raises and never relies on the watchdog to bail out."""
    import pytest

    pytest.importorskip("sqlalchemy")
    import app.agent.nodes as nodes_mod

    monkeypatch.setattr(nodes_mod, "SEARCH_BUDGET_S", 0.0)

    async def fake_no_browser(*_a, **_k):
        return False

    async def fake_flow_platforms():
        return set()

    async def fake_candidate_sources():
        return set()

    monkeypatch.setattr(nodes_mod, "_no_browser_session_available", fake_no_browser)
    monkeypatch.setattr(nodes_mod, "_flow_platforms", fake_flow_platforms)
    monkeypatch.setattr(nodes_mod, "_candidate_source_platforms", fake_candidate_sources)
    async def fake_custom_sources(state):
        return [], [], [], []

    monkeypatch.setattr(nodes_mod, "_search_custom_sources", fake_custom_sources)

    async def fail_adapter(*a, **k):
        raise AssertionError("adapter must not run once the budget is exhausted")

    monkeypatch.setitem(
        nodes_mod._candidate_adapters(), "linkedin", fail_adapter
    )

    state = {
        "type": nodes_mod.SearchType.candidates,
        "query": "python developer",
        "plan": {"platforms": ["linkedin", "linkedin"], "queries": ["python developer"]},
    }
    result = await nodes_mod.run_search(state)

    assert result["needs_human"] is False
    assert result["raw_results"] == []
    budget_issues = [i for i in result.get("source_issues", []) if i.get("source") == "budget"]
    assert len(budget_issues) == 1
    assert "time budget exhausted" in budget_issues[0]["reason"]
    assert "PARTIAL" in result["plan_detail"]


async def test_midloop_browsererror_keeps_partial_results(monkeypatch):
    """A platform raising BrowserError mid-loop (extension agent offline)
    must not discard results earlier platforms already produced — the run
    completes with the partial results + PARTIAL note, not a pause."""
    pytest.importorskip("sqlalchemy")
    import app.agent.nodes as nodes_mod
    from app.services.browser import BrowserError

    async def fake_no_browser(*_a, **_k):
        return False

    async def fake_flow_platforms():
        return set()

    async def fake_candidate_sources():
        return set()

    monkeypatch.setattr(nodes_mod, "_no_browser_session_available", fake_no_browser)
    monkeypatch.setattr(nodes_mod, "_flow_platforms", fake_flow_platforms)
    monkeypatch.setattr(nodes_mod, "_candidate_source_platforms", fake_candidate_sources)

    async def fake_custom_sources(state):
        return [], [], [], []

    monkeypatch.setattr(nodes_mod, "_search_custom_sources", fake_custom_sources)

    async def good_adapter(*a, **k):
        return {
            "raw_results": [{"name": "Kept Candidate", "source": "seek"}],
            "needs_human": False,
        }

    async def offline_adapter(*a, **k):
        raise BrowserError(
            "Extension agent went offline mid-search — re-open the app "
            "so the agent reconnects, then re-run"
        )

    monkeypatch.setitem(nodes_mod._candidate_adapters(), "seek", good_adapter)
    monkeypatch.setitem(nodes_mod._candidate_adapters(), "linkedin", offline_adapter)

    state = {
        "type": nodes_mod.SearchType.candidates,
        "query": "python developer",
        "plan": {"platforms": ["seek", "linkedin"], "queries": ["python developer"]},
    }
    result = await nodes_mod.run_search(state)

    assert [r["name"] for r in result["raw_results"]] == ["Kept Candidate"]
    assert result["needs_human"] is False
    assert result["human_reason"] is None
    assert "PARTIAL" in result["plan_detail"]
    issues = result.get("source_issues", [])
    assert any(i.get("source") == "linkedin" for i in issues)


async def test_offline_only_still_pauses_with_actionable_message(monkeypatch):
    """When the only platform goes offline and nothing was collected, the
    run still pauses with an actionable message (no partials to keep)."""
    pytest.importorskip("sqlalchemy")
    import app.agent.nodes as nodes_mod
    from app.services.browser import BrowserError

    async def fake_no_browser(*_a, **_k):
        return False

    async def fake_flow_platforms():
        return set()

    async def fake_candidate_sources():
        return set()

    monkeypatch.setattr(nodes_mod, "_no_browser_session_available", fake_no_browser)
    monkeypatch.setattr(nodes_mod, "_flow_platforms", fake_flow_platforms)
    monkeypatch.setattr(nodes_mod, "_candidate_source_platforms", fake_candidate_sources)

    async def fake_custom_sources(state):
        return [], [], [], []

    monkeypatch.setattr(nodes_mod, "_search_custom_sources", fake_custom_sources)

    async def offline_adapter(*a, **k):
        raise BrowserError("Extension agent went offline mid-search")

    monkeypatch.setitem(nodes_mod._candidate_adapters(), "linkedin", offline_adapter)

    state = {
        "type": nodes_mod.SearchType.candidates,
        "query": "python developer",
        "plan": {"platforms": ["linkedin"], "queries": ["python developer"]},
    }
    result = await nodes_mod.run_search(state)

    assert result["raw_results"] == []
    assert result["needs_human"] is True
    assert "re-open the app" in result["human_reason"]
    assert result["source_issues"][0]["source"] == "linkedin"


async def test_cloudflare_source_skips_execute_flow_when_agent_down(monkeypatch):
    """A Cloudflare-protected source (FastJobs) must NOT fall back to headless
    Playwright when the extension agent is unavailable — that fallback cannot
    pass the CF challenge and just wastes ~45s. The failure names the missing
    extension instead."""
    pytest.importorskip("sqlalchemy")
    import app.agent.nodes as nodes_mod

    async def fail_execute_flow(**kwargs):
        raise AssertionError("execute_flow must not run for a CF-protected source")

    # execute_flow is imported lazily inside _search_custom_sources, so patch
    # it at its definition site.
    monkeypatch.setattr("app.services.source_flows.execute_flow", fail_execute_flow)

    class _CFSource:
        id = 1
        name = "fastjobs - candidate"
        domain = "fastjobs.sg"
        base_url = "https://employer.fastjobs.sg/"
        session_state = None
        enabled = True

    class _Flow:
        id = 7
        source_id = 1
        steps = [{"action": "navigate", "url": "https://employer.fastjobs.sg/"}]
        status = "active"
        flow_type = "find_candidates"

    class _FakeScalars:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class _FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return _FakeScalars(self._rows)

    class _FakeDb:
        def __init__(self):
            self._calls = 0

        async def execute(self, *a, **k):
            # First call → sources, second → active flows.
            self._calls += 1
            return _FakeResult([_CFSource()] if self._calls == 1 else [_Flow()])

        async def get(self, *a, **k):
            return None

    class _FakeCtx:
        async def __aenter__(self):
            return _FakeDb()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr("app.db.async_session", lambda: _FakeCtx())

    # Agent reports disconnected → run_flow is never attempted.
    class _OfflineRegistry:
        connected = False

        async def dispatch(self, *a, **k):
            raise AssertionError("dispatch must not happen when disconnected")

    monkeypatch.setattr("app.services.agent_relay.agent_registry", _OfflineRegistry())

    raw, ok, failed, issues = await nodes_mod._search_custom_sources(
        {"type": nodes_mod.SearchType.candidates, "query": "engineer"}
    )
    assert raw == []
    assert any("Cloudflare-protected" in f for f in failed)
    assert any("browser extension" in i["reason"] for i in issues)


async def test_stored_profile_cloudflare_fail_fasts_without_registry_entry(monkeypatch):
    """A source with NO built-in registry entry but a STORED profile marking it
    Cloudflare-protected must still fail-fast — proves the per-source profile
    (not just the global registry) drives the method-choice decision."""
    pytest.importorskip("sqlalchemy")
    import app.agent.nodes as nodes_mod

    async def fail_execute_flow(**kwargs):
        raise AssertionError("execute_flow must not run for a stored-CF source")

    monkeypatch.setattr("app.services.source_flows.execute_flow", fail_execute_flow)

    class _StoredCFSource:
        id = 2
        name = "unknownboard"
        domain = "unknownboard.example"  # no registry entry
        base_url = "https://unknownboard.example/"
        session_state = None
        enabled = True
        profile = {"auth_model": "portal", "cloudflare_protected": True}

    class _Flow:
        id = 8
        source_id = 2
        steps = [{"action": "navigate", "url": "https://unknownboard.example/"}]
        status = "active"
        flow_type = "find_candidates"

    class _FakeScalars:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class _FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return _FakeScalars(self._rows)

    class _FakeDb:
        def __init__(self):
            self._calls = 0

        async def execute(self, *a, **k):
            self._calls += 1
            return _FakeResult([_StoredCFSource()] if self._calls == 1 else [_Flow()])

        async def get(self, *a, **k):
            return None

    class _FakeCtx:
        async def __aenter__(self):
            return _FakeDb()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr("app.db.async_session", lambda: _FakeCtx())

    class _OfflineRegistry:
        connected = False

        async def dispatch(self, *a, **k):
            raise AssertionError("dispatch must not happen when disconnected")

    monkeypatch.setattr("app.services.agent_relay.agent_registry", _OfflineRegistry())

    raw, ok, failed, issues = await nodes_mod._search_custom_sources(
        {"type": nodes_mod.SearchType.candidates, "query": "engineer"}
    )
    assert raw == []
    assert any("Cloudflare-protected" in f for f in failed)


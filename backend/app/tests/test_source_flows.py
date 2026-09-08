"""Tests for the guided-wizard templatizer and domain parsing."""

from app.services.source_flows import (
    KEYWORD_LIMIT,
    SEEK_KEYWORD_LIMIT,
    build_boolean_keywords,
    build_boolean_keywords_async,
    compact_boolean_query,
    domain_of,
    filter_excluded_results,
    templatize,
)


def test_keyword_limit_alias_backwards_compat():
    """SEEK_KEYWORD_LIMIT survives as an alias of the generic platform-wide
    KEYWORD_LIMIT (the 500-char cap applies to LinkedIn too, not just SEEK)."""
    from app.services import source_flows

    assert KEYWORD_LIMIT == 500
    assert source_flows.SEEK_KEYWORD_LIMIT is source_flows.KEYWORD_LIMIT
    assert SEEK_KEYWORD_LIMIT == KEYWORD_LIMIT


def test_domain_of_strips_www():
    assert domain_of("https://www.fastjob.com/jobs") == "fastjob.com"
    assert domain_of("http://FastJob.com") == "fastjob.com"


def test_boolean_keywords_single_query_no_excludes():
    assert build_boolean_keywords(["software engineer"], []) == '"software engineer"'


def test_boolean_keywords_multi_query_or():
    out = build_boolean_keywords(["software engineer", "developer"], [])
    assert out == '"software engineer" OR developer'


def test_boolean_keywords_with_excludes():
    out = build_boolean_keywords(
        ["software engineer"], ["recruiter", "talent acquisition"]
    )
    assert out == '"software engineer" NOT (recruiter OR "talent acquisition")'


def test_boolean_keywords_cap_not_terms():
    # NOT clause widened to 12 terms (flow platforms run one query per leg;
    # filter_excluded_results still enforces the full list post-hoc).
    out = build_boolean_keywords(["dev"], ["a", "b", "c", "d", "e"])
    assert 'NOT (a OR b OR c OR d OR e)' in out


def test_boolean_keywords_preserves_existing_syntax():
    q = 'digital AND sales NOT (hr OR "people ops")'
    assert build_boolean_keywords([q], []) == q


def test_boolean_keywords_empty():
    assert build_boolean_keywords([], []) == ""


def test_boolean_keywords_truncates_over_seek_limit():
    long_q = " OR ".join([f'"skill number {i} engineer"' for i in range(40)])
    out = build_boolean_keywords([long_q], [])
    assert len(out) <= SEEK_KEYWORD_LIMIT


async def test_compact_boolean_query_llm_preserves_syntax(monkeypatch):
    async def fake_chat(self, system, user):
        return '"QC technician" OR "QC inspector" OR microarray'

    monkeypatch.setattr("app.services.llm.LLMService.enabled", property(lambda self: True))
    monkeypatch.setattr("app.services.llm.LLMService.chat", fake_chat)
    long_q = " OR ".join([f'"term number {i} technician"' for i in range(40)])
    out = await compact_boolean_query(long_q)
    assert out == '"QC technician" OR "QC inspector" OR microarray'


async def test_compact_boolean_query_falls_back_when_llm_disabled(monkeypatch):
    monkeypatch.setattr("app.services.llm.LLMService.enabled", property(lambda self: False))
    long_q = "x" * (SEEK_KEYWORD_LIMIT + 100)
    out = await compact_boolean_query(long_q)
    assert len(out) <= SEEK_KEYWORD_LIMIT


async def test_build_boolean_keywords_async_uses_llm(monkeypatch):
    from app.services import source_flows

    async def fake_compact(keywords, limit=SEEK_KEYWORD_LIMIT):
        return "compacted"

    monkeypatch.setattr(source_flows, "compact_boolean_query", fake_compact)
    out = await build_boolean_keywords_async(["skill one", "skill two"], ["x" * 600])
    assert out == "compacted"


async def test_build_boolean_keywords_async_short_query_no_llm(monkeypatch):
    from app.services import source_flows

    async def fail_compact(keywords, limit=SEEK_KEYWORD_LIMIT):
        raise AssertionError("LLM should not be called for short queries")

    monkeypatch.setattr(source_flows, "compact_boolean_query", fail_compact)
    out = await build_boolean_keywords_async(["dev"], [])
    assert out == "dev"


def test_filter_excluded_results_drops_matches():
    results = [
        {"title": "Senior Python Dev", "company": "Acme"},
        {"title": "Tech Recruiter", "company": "HireCo"},
    ]
    kept = filter_excluded_results(results, ["recruiter"])
    assert [r["title"] for r in kept] == ["Senior Python Dev"]


def test_filter_excluded_results_no_excludes_noop():
    results = [{"title": "Dev"}]
    assert filter_excluded_results(results, None) is results


def test_templatize_empty_events():
    steps, card = templatize([])
    assert steps == []
    assert card is None


def test_templatize_binds_query_param():
    events = [
        {"action": "fill", "selector": "#search-box", "value": "python developer"},
        {"action": "submit", "selector": "form#search"},
        {"action": "click", "selector": "button.filter", "text": "Full-time"},
    ]
    steps, _card = templatize(events, query_hint="python developer")
    fills = [s for s in steps if s["action"] == "fill"]
    assert len(fills) == 1
    assert fills[0]["param"] == "query"
    assert fills[0]["selector"] == "#search-box"
    # submit becomes a press step
    assert any(s["action"] == "press" and s["key"] == "Enter" for s in steps)
    # filter click preserved as a plain click
    assert {"action": "click", "selector": "button.filter"} in steps


def test_templatize_extracts_pagination():
    events = [
        {"action": "fill", "selector": "#q", "value": "devops"},
        {"action": "click", "selector": "a.next-page", "text": "Next ›"},
    ]
    steps, _ = templatize(events, query_hint="devops")
    pag = [s for s in steps if s.get("repeat") == "paginate"]
    assert len(pag) == 1
    assert pag[0]["selector"] == "a.next-page"
    # pagination step must come last (runs after extraction)
    assert steps[-1] is pag[0]


def test_templatize_mark_card_becomes_card_selectors():
    events = [
        {"action": "fill", "selector": "#q", "value": "qa"},
        {"action": "mark_card", "selector": "div.job-card"},
    ]
    steps, card = templatize(events, query_hint="qa")
    assert card is not None
    assert card["card"] == "div.job-card"
    assert "fields" in card
    # mark_card events must not leak into steps
    assert all(s["action"] != "mark_card" for s in steps)


def test_templatize_only_first_fill_becomes_query():
    events = [
        {"action": "fill", "selector": "#location", "value": "Singapore"},
        {"action": "fill", "selector": "#q", "value": "python"},
    ]
    steps, _ = templatize(events, query_hint="python")
    fills = [s for s in steps if s["action"] == "fill"]
    assert fills[0].get("param") is None
    assert fills[0]["value"] == "Singapore"
    assert fills[1].get("param") == "query"


def test_sanitize_storage_state_normalizes_chrome_samesite():
    """Chrome cookie sameSite values must be mapped to Playwright's
    Strict|Lax|None — a bad value used to crash new_context() and pause
    the whole candidate search with a misleading 'session expired'."""
    from app.services.encryption import encrypt_session_state
    from app.services.source_flows import _sanitize_storage_state

    bad = {
        "cookies": [
            {"name": "a", "sameSite": "no_restriction"},
            {"name": "b", "sameSite": "lax"},
            {"name": "c", "sameSite": "strict"},
            {"name": "d", "sameSite": "unspecified"},
            {"name": "e"},  # missing sameSite
        ],
        "origins": [],
    }
    blob = encrypt_session_state(__import__("json").dumps(bad))
    out = _sanitize_storage_state(blob)
    assert [c["sameSite"] for c in out["cookies"]] == ["None", "Lax", "Strict", "Lax", "Lax"]
    assert _sanitize_storage_state(None) is None


def test_record_stop_rejects_empty_clicks():
    """Sanity on the model: query_hint optional."""
    from app.api.routes.sources import AgentRecordRequest

    req = AgentRecordRequest(flow_type="find_candidates")
    assert req.query_hint is None


def test_max_not_terms_widened_to_12():
    """Flow platforms run one query per navigation leg, so the NOT clause can
    carry up to 12 exclude terms (post-filter still enforces the full list)."""
    from app.services.source_flows import MAX_NOT_TERMS

    assert MAX_NOT_TERMS == 12
    excludes = [f"x{i}" for i in range(12)]
    out = build_boolean_keywords(["dev"], excludes)
    for t in excludes:
        assert t in out


async def test_build_boolean_keywords_async_limit_900_no_compaction(monkeypatch):
    from app.services import source_flows

    async def fail_compact(keywords, limit=KEYWORD_LIMIT):
        raise AssertionError("LLM compaction must not run under limit=900")

    monkeypatch.setattr(source_flows, "compact_boolean_query", fail_compact)
    queries = [" OR ".join([f'"long skill phrase {i} engineer"' for i in range(20)])]
    assert len(" OR ".join(queries)) > KEYWORD_LIMIT
    assert len(" OR ".join(queries)) <= source_flows.KEYWORD_LIMIT_FLOW
    out = await build_boolean_keywords_async(queries, [], limit=source_flows.KEYWORD_LIMIT_FLOW)
    assert len(out) <= source_flows.KEYWORD_LIMIT_FLOW
    assert len(out) > KEYWORD_LIMIT  # kept more than the 500-char cap allows


async def test_build_boolean_keywords_async_default_limit_compacts(monkeypatch):
    from app.services import source_flows

    calls: list[int] = []

    async def fake_compact(keywords, limit=KEYWORD_LIMIT):
        calls.append(limit)
        return keywords[:limit]

    monkeypatch.setattr(source_flows, "compact_boolean_query", fake_compact)
    queries = [" OR ".join([f'"long skill phrase {i} engineer"' for i in range(30)])]
    out = await build_boolean_keywords_async(queries, [])
    assert calls == [KEYWORD_LIMIT]
    assert len(out) <= KEYWORD_LIMIT

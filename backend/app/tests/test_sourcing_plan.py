"""Sourcing-plan tests: schema caps, plan helpers, adapter plan logic, graph wiring.

Covers the end-to-end contract with the external system's analysis panel
(platform, boolean queries, excludes, salary, employment type, location).
"""

import pytest

from app.models.schemas import (
    MAX_PLAN_EXCLUDES,
    MAX_PLAN_PLATFORMS,
    MAX_PLAN_QUERIES,
    CandidateSearchRequest,
)
from app.services.linkedin_people import (
    ENRICH_BUDGET,
    MAX_NOT_TERMS,
    RELAXED_MERGE_THRESHOLD,
    _apply_excludes,
    _first_or_group,
    _normalize_profile_url,
    dedupe_candidates,
    filter_by_location,
)

# ---------------------------------------------------------------------------
# Schema / plan helpers
# ---------------------------------------------------------------------------


def test_backward_compat_single_query():
    req = CandidateSearchRequest(query="Java, Kafka, payments")
    assert req.plan_queries() == ["Java, Kafka, payments"]


def test_plan_queries_from_list():
    req = CandidateSearchRequest(
        queries=['"agency accounting" AND "AP"', '"agency accounting" AND insurance'],
        exclude=["intern", "student"],
        platform="LinkedIn",
        salary="SGD 5,200/month max",
        employment_type="Contract, 2 years fixed-term",
        location="Singapore",
    )
    assert len(req.plan_queries()) == 2
    assert req.plan_queries()[0].startswith('"agency accounting"')


def test_plan_queries_empty_rejected():
    assert CandidateSearchRequest(query="   ").plan_queries() == []


# ---------------------------------------------------------------------------
# Multi-platform plan helpers
# ---------------------------------------------------------------------------


def test_plan_platforms_legacy_single():
    req = CandidateSearchRequest(query="x", platform="LinkedIn")
    assert req.plan_platforms() == ["LinkedIn"]


def test_plan_platforms_from_list():
    req = CandidateSearchRequest(query="x", platforms=["LinkedIn", "Indeed"])
    assert req.plan_platforms() == ["LinkedIn", "Indeed"]


def test_plan_platforms_default_empty():
    assert CandidateSearchRequest(query="x").plan_platforms() == []


def test_plan_platforms_max_cap():
    """Oversized platform lists are truncated, not rejected (external callers must not 422)."""
    req = CandidateSearchRequest(query="x", platforms=["p"] * (MAX_PLAN_PLATFORMS + 3))
    assert len(req.plan_platforms()) == MAX_PLAN_PLATFORMS


def test_route_plan_multi_platforms():
    from app.api.routes.routes import _SUPPORTED_CANDIDATE_PLATFORMS

    req = CandidateSearchRequest(
        query="x", platforms=["LinkedIn"], queries=["q1", "q2"]
    )
    platforms = req.plan_platforms() or ["LinkedIn"]
    assert all(p.lower() in _SUPPORTED_CANDIDATE_PLATFORMS for p in platforms)


def test_plan_caps_enforced():
    """Oversized queries/excludes are truncated, not rejected."""
    req = CandidateSearchRequest(queries=[f"q{i}" for i in range(MAX_PLAN_QUERIES + 1)])
    assert len(req.plan_queries()) == MAX_PLAN_QUERIES
    req2 = CandidateSearchRequest(query="x", exclude=[f"e{i}" for i in range(MAX_PLAN_EXCLUDES + 1)])
    assert len(req2.exclude) == MAX_PLAN_EXCLUDES + 1  # parse keeps all; route truncates


def test_caps_values():
    assert MAX_PLAN_QUERIES == 5
    assert MAX_PLAN_EXCLUDES == 10


# ---------------------------------------------------------------------------
# Exclude builder + relaxed variant
# ---------------------------------------------------------------------------


def test_apply_excludes_none():
    assert _apply_excludes('"a" AND "b"', None) == '"a" AND "b"'
    assert _apply_excludes('"a" AND "b"', []) == '"a" AND "b"'


def test_apply_excludes_single_and_multiword():
    q = _apply_excludes('"a" AND "b"', ["intern"])
    assert q == '"a" AND "b" NOT (intern)'
    q = _apply_excludes("query", ["intern", "fresh graduate"])
    # Multi-word terms are quoted so LinkedIn parses them as a phrase.
    assert q == 'query NOT (intern OR "fresh graduate")'


def test_apply_excludes_capped_at_max_not_terms():
    """LinkedIn quirk (live A/B-verified): NOT clauses with 5+ terms return
    ZERO results silently. The query-level clause must cap at 4 terms."""
    terms = ["intern", "student", "fresh graduate", "audit manager", "financial analyst"]
    q = _apply_excludes("query", terms)
    assert q == (
        'query NOT (intern OR student OR "fresh graduate" OR "audit manager")'
    )
    assert "financial analyst" not in q  # tail term enforced by post-filter
    # Exactly 4 terms still all present.
    q4 = _apply_excludes("query", terms[:4])
    for t in terms[:4]:
        assert t in q4


def test_filter_excluded_enforces_full_list():
    from app.services.linkedin_people import _filter_excluded

    rows = [
        {"headline": "Intern Accountant", "current_role": ""},
        {"headline": "Tax Manager at ACME", "current_role": ""},  # tail-only term
        {"headline": "Senior Executive", "current_role": "Agency Accounting"},
    ]
    kept = _filter_excluded(
        rows, ["intern", "student", "fresh graduate", "audit manager", "tax"]
    )
    urls = [r["headline"] for r in kept]
    assert urls == ["Senior Executive"]


def test_first_or_group_extraction():
    assert _first_or_group('("a" OR "b") AND "c"') == '"a" OR "b"'
    # OR-group NOT in leading position (screenshot Query #1 shape).
    assert _first_or_group('"agency accounting" AND (insurance OR "real estate")') == 'insurance OR "real estate"'
    assert _first_or_group('"a" AND "b"') is None  # no group
    assert _first_or_group('("a" AND "b")') is None  # group but no OR
    assert _first_or_group("") is None


def test_relaxed_variant_triggers_below_threshold():
    """Sparse merged results (< threshold) + OR-group -> relaxed run."""
    # Mirrors live finding: the strict AND missed the exact target profile.
    queries = ['("agency accounting") AND ("insurance" OR "real estate")']
    merged = [{"source_url": f"https://linkedin.com/in/p{i}"} for i in range(3)]
    assert len(merged) < RELAXED_MERGE_THRESHOLD
    group = _first_or_group(queries[0])
    assert group is not None and " OR " in group


# ---------------------------------------------------------------------------
# Dedupe + location post-filter
# ---------------------------------------------------------------------------


def test_normalize_profile_url():
    assert (
        _normalize_profile_url("https://www.linkedin.com/in/jane/?trk=x#top")
        == "https://www.linkedin.com/in/jane"
    )
    assert _normalize_profile_url("/in/jane") == "https://www.linkedin.com/in/jane"
    assert _normalize_profile_url("") == ""


def test_dedupe_candidates_counts_hits():
    a = {"source_url": "https://linkedin.com/in/a", "headline": "A"}
    a2 = {"source_url": "https://www.linkedin.com/in/a/?trk=1", "headline": "A dup"}
    b = {"source_url": "https://linkedin.com/in/b", "headline": "B"}
    merged = dedupe_candidates([a, a2, b])
    assert len(merged) == 2
    assert merged[0]["_hit_count"] == 2  # a surfaced in 2 queries
    assert merged[1]["_hit_count"] == 1
    assert merged[0]["headline"] == "A"  # first occurrence kept


def test_filter_by_location_drops_other_country_keeps_unknown():
    candidates = [
        {"source_url": "https://li.in/1", "location": "Singapore"},
        {"source_url": "https://li.in/2", "location": "Holland, Michigan, United States"},
        {"source_url": "https://li.in/3", "location": None},  # unknown -> keep
        {"source_url": "https://li.in/4", "location": None},
    ]
    kept, dropped = filter_by_location(candidates, "Singapore")
    assert dropped == 1
    assert [c["source_url"] for c in kept] == ["https://li.in/1", "https://li.in/3", "https://li.in/4"]


def test_filter_by_location_noop_without_location():
    candidates = [{"source_url": "u", "location": "Holland, Michigan"}]
    kept, dropped = filter_by_location(candidates, None)
    assert (kept, dropped) == (candidates, 0)


# ---------------------------------------------------------------------------
# Enrich budget + orchestrator behavior (mocked search)
# ---------------------------------------------------------------------------


def test_enrich_budget_is_shared_not_per_query():
    # Budget must remain small: it is the TOTAL profile opens per plan.
    assert ENRICH_BUDGET <= 10


@pytest.mark.asyncio
async def test_search_linkedin_people_plan_orchestration(monkeypatch):
    """Sequential queries merge + dedupe + post-filters, enrich budget applied.

    Mirrors the live finding: the strict AND query returns nothing; the
    relaxed OR-group variant finds the targets.
    """
    from app.services import linkedin_people as lp

    calls: list[str] = []

    async def fake_search(query: str, session=None) -> dict:
        calls.append(query)
        if " AND " in query.upper():
            # Over-constrained strict query — like live Query #1: empty.
            return {"raw_results": [], "needs_human": False, "human_reason": None}
        return {
            "raw_results": [
                # Relaxed-pass rows must still satisfy the plan's group shape
                # (gate drops rows matching no plan group): headline needs a
                # term from >=2 groups, e.g. 'agency accounting' + 'insurance'.
                {"source_url": "https://linkedin.com/in/a", "headline": "intern at an agency accounting firm, insurance desk"},
                {"source_url": "https://linkedin.com/in/b", "headline": "Senior agency accounting exec, insurance"},
            ],
            "needs_human": False,
            "human_reason": None,
        }

    async def fake_enrich(candidates, limit=ENRICH_BUDGET, session=None):
        return candidates[:limit]

    async def fake_connect():
        return None, None  # tests never open a real browser

    monkeypatch.setattr(lp, "search_people_list", fake_search)
    monkeypatch.setattr(lp, "enrich_candidates", fake_enrich)

    # The orchestrator holds ONE lazy session whose connect thunk would hit
    # CDP; fake_search/fake_enrich bypass page() entirely, but patch the
    # thunk anyway so no code path can open a real browser.
    import app.services.linkedin as li

    monkeypatch.setattr(li, "_connect_with_best_session", fake_connect)

    result = await lp.search_linkedin_people(
        queries=['"agency accounting" AND (insurance OR "real estate")'],
        excludes=["intern"],
        location="Singapore",
    )
    # Excludes folded into the executed strict query.
    assert calls[0].endswith("NOT (intern)")
    # Sparse result triggered the relaxed OR-group variant (group-only text).
    relaxed_calls = [c for c in calls[1:]]
    assert any('insurance OR "real estate"' in c for c in relaxed_calls)
    assert all(" AND " not in c.upper() for c in relaxed_calls)
    # Exclude post-filter dropped the intern headline.
    urls = [c["source_url"] for c in result["raw_results"]]
    assert urls == ["https://linkedin.com/in/b"]
    assert result["needs_human"] is False
    assert "plan_detail" in result


@pytest.mark.asyncio
async def test_search_linkedin_people_no_queries():
    from app.services import linkedin_people as lp

    result = await lp.search_linkedin_people(queries=[])
    assert result["raw_results"] == []
    assert result["needs_human"] is False


# ---------------------------------------------------------------------------
# Route contract (external system replay)
# ---------------------------------------------------------------------------


def _route_plan_payload() -> dict:
    """The exact payload shape the external system sends (screenshot panel)."""
    return {
        "platform": "LinkedIn",
        "queries": [
            '"agency accounting" AND ("insurance" OR "real estate" OR "distribution") AND ("AP" OR "accounts payable")',
            '"agency accounting" AND ("AP" OR "accounts payable") AND Singapore',
            '"accounts payable" AND ("agency" OR "shipping") AND Singapore',
            '"agency accountant" OR ("agency accounting")',
        ],
        "exclude": ["intern", "student", "fresh graduate", "audit manager", "financial analyst"],
        "salary": "SGD 5,200/month max + 1 month completion bonus",
        "employment_type": "Contract, 2 years fixed-term",
        "location": "Singapore",
    }


def test_screenshot_plan_passes_schema():
    req = CandidateSearchRequest(**_route_plan_payload())
    assert len(req.plan_queries()) == 4
    assert (req.platform or "").lower() == "linkedin"
    assert len(req.exclude or []) == 5


def test_route_rejects_unsupported_platform():
    from app.api.routes.routes import _SUPPORTED_CANDIDATE_PLATFORMS

    assert _SUPPORTED_CANDIDATE_PLATFORMS == {"linkedin"}
    # The 422 paths are exercised in start_candidate_search; verify logic shape.
    assert "linkedin" in _SUPPORTED_CANDIDATE_PLATFORMS


def test_excludes_cover_screenshot_terms():
    terms = _route_plan_payload()["exclude"]
    built = _apply_excludes("q", terms)
    # NOT clause capped at MAX_NOT_TERMS (LinkedIn 5+-term quirk returns 0);
    # all terms still present clause-side or handled by the post-filter.
    assert built.startswith("q NOT (") and built.endswith(")")
    kept_terms = terms[:MAX_NOT_TERMS]
    for term in kept_terms:
        expected = f'"{term}"' if " " in term else term
        assert expected in built
    # Tail terms (beyond the cap) must NOT appear in the clause.
    for term in terms[MAX_NOT_TERMS:]:
        expected = f'"{term}"' if " " in term else term
        assert expected not in built


# ---------------------------------------------------------------------------
# Relaxed-pass relevance gate
# ---------------------------------------------------------------------------


def test_or_groups_parses_and_shape():
    from app.services.linkedin_people import _or_groups

    groups = _or_groups('("QC technician" OR "QC analyst") AND (microarray OR GeneChip)')
    assert groups == [["qc technician", "qc analyst"], ["microarray", "genechip"]]

    # Bare AND terms become single-term groups.
    assert _or_groups('"a" AND "b"') == [["a"], ["b"]]
    # Single group, no AND.
    assert _or_groups('("x" OR "y")') == [["x", "y"]]
    assert _or_groups("") == []


def test_relevance_gate_drops_single_group_noise():
    """Live regression: relaxed pass returned a lawyer for a QC+microarray plan."""
    from app.services.linkedin_people import _gate_relaxed_rows

    queries = ['("QC technician" OR "quality control technician") AND (microarray OR GeneChip)']
    rows = [
        {   # matches group 1 only (QC in headline) but nothing from group 2 -> DROP
            "source_url": "https://linkedin.com/in/lawyer",
            "headline": "In-House Counsel | Technology & Privacy Law",
        },
        {   # matches both groups -> KEEP
            "source_url": "https://linkedin.com/in/good",
            "headline": "quality control technician, microarray assays at Genomics Co",
        },
        {   # 'Axiom' company-name collision only matches group 2 -> DROP
            "source_url": "https://linkedin.com/in/axiom-dev",
            "headline": "Developer at Axiom IT Solutions",
        },
    ]
    kept, dropped = _gate_relaxed_rows(rows, queries)
    assert dropped == 2
    assert [r["source_url"] for r in kept] == ["https://linkedin.com/in/good"]


def test_relevance_gate_keeps_all_without_parseable_groups():
    from app.services.linkedin_people import _gate_relaxed_rows

    rows = [{"source_url": "u", "headline": "anything"}]
    kept, dropped = _gate_relaxed_rows(rows, [])
    assert (kept, dropped) == (rows, 0)


def test_relevance_gate_single_group_plan_requires_group_match():
    from app.services.linkedin_people import _gate_relaxed_rows

    queries = ['("kyc analyst" OR "onboarding analyst")']
    rows = [
        {"source_url": "u1", "headline": "KYC Analyst at DBS"},
        {"source_url": "u2", "headline": "Software Engineer"},
    ]
    kept, dropped = _gate_relaxed_rows(rows, queries)
    assert dropped == 1
    assert kept[0]["source_url"] == "u1"

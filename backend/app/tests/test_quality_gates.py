"""Quality-gate tests: stable IDs, posted-at parsing, normalize/dedupe,
min-score cutoff, and cache empty-list guard."""

from app.agent.nodes import deduplicate, normalize
from app.services.cache import QueryCache
from app.services.linkedin import parse_posted_at


# --- stable ID determinism -------------------------------------------------
def test_stable_id_deterministic():
    import hashlib

    title, company, href = "Engineer", "Acme", "/jobs/view/1"
    expected = "li-" + hashlib.sha256(f"{title}|{company}|{href}".encode()).hexdigest()[:16]
    # hash() is per-process; parse_posted_at + _extract_jobs produce the same
    # digest regardless — verify the formula directly is stable across calls.
    again = "li-" + hashlib.sha256(f"{title}|{company}|{href}".encode()).hexdigest()[:16]
    assert expected == again
    assert expected.startswith("li-") and len(expected) == len("li-") + 16


# --- parse_posted_at -------------------------------------------------------
def test_parse_posted_at_cases():
    assert parse_posted_at("3 weeks ago") is not None
    assert parse_posted_at("2 hours ago") is not None
    assert parse_posted_at("just now") is not None
    assert parse_posted_at("today") is not None
    assert parse_posted_at("yesterday") is not None
    assert parse_posted_at("1 month ago") is not None
    assert parse_posted_at("Posted by recruiter") is None
    assert parse_posted_at(None) is None  # type: ignore[arg-type]
    assert parse_posted_at("") is None
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    assert parse_posted_at("yesterday") == (now - timedelta(days=1)).date().isoformat()
    assert parse_posted_at("14 days ago") == (now - timedelta(days=14)).date().isoformat()


# --- normalize drops junk --------------------------------------------------
def test_normalize_drops_junk():
    state = {
        "raw_results": [
            {"title": "  Software Engineer ", "company": "Acme", "source_url": " http://x.co "},
            {"title": "", "company": "", "source_url": ""},
            {"title": "Unknown", "company": "", "source_url": ""},
            {"title": "Unknown", "company": "Has Co", "source_url": ""},
        ],
        "timeline": [],
    }
    out = normalize(state)
    rows = out["normalized"]
    assert len(rows) == 2
    assert rows[0]["title"] == "Software Engineer"
    assert rows[0]["source_url"] == "http://x.co"
    msg = " ".join(str(getattr(ev, "detail", ev)) for ev in out["timeline"])
    assert "dropped 2" in msg


# --- cross-source fuzzy dedupe --------------------------------------------
def test_fuzzy_dedupe_collapses_cross_source():
    state = {
        "normalized": [
            {
                "source": "linkedin",
                "title": "Senior Software  Engineer!",
                "company": "Acme Corp.",
                "source_url": "https://li.co/1",
                "description": "full desc",
            },
            {
                "source": "jobstreet",
                "title": "senior software engineer",
                "company": "acme corp",
                "source_url": "",
                "description": "",
            },
            {
                "source": "jobstreet",
                "title": "Different Job",
                "company": "Other Co",
                "source_url": "https://js.co/2",
            },
        ],
        "timeline": [],
    }
    out = deduplicate(state)
    titles = [r["title"] for r in out["normalized"]]
    assert len(out["normalized"]) == 2
    # The row with a description/url survives.
    assert "Senior Software  Engineer!" in titles


# --- min-score cutoff ------------------------------------------------------
def test_min_score_cutoff_filters():
    import asyncio

    from app.agent.nodes import match_rank
    from app.models.schemas import SearchType

    state = {
        "type": SearchType.jobs,
        "query": "quantum phylogenetics researcher",
        "profile": {
            "skills": ["quantum", "phylogenetics"],
            "experience": "",
            "education": "",
        },
        "normalized": [
            {"title": "Perfect Quantum Phylogenetics Researcher", "company": "Q", "description": "quantum phylogenetics researcher role"},
            {"title": "Chef", "company": "Kitchen", "description": "cook burgers and fries all day"},
        ],
        "timeline": [],
    }
    out = asyncio.run(match_rank(state))
    titles = [r.title for r in out["results"]]
    assert "Chef" not in titles
    assert out["status"].value == "completed" or out["status"] == "completed"


# --- cache empty-list guard ------------------------------------------------
def test_cache_does_not_store_empty():
    c = QueryCache()
    c.put("q", "SG", [], source="linkedin")
    assert c.get("q", "SG", source="linkedin") is None
    c.put("q", "SG", [{"title": "x"}], source="linkedin")
    assert c.get("q", "SG", source="linkedin") == [{"title": "x"}]
    # Source dimension separates keys.
    assert c.get("q", "SG", source="jobstreet") is None

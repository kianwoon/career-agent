"""Regression tests for the relaxed-pass relevance gate.

The relaxed pass in `search_linkedin_people` deliberately dispatches only the
FIRST OR-group of each plan query, so gating the returned rows against the
FULL plan's AND-shape drops genuinely relevant people (the search never asked
for the second group's vocabulary). These tests pin the fixed behaviour.
"""

from app.services.linkedin_people import (
    _gate_relaxed_rows,
    _matches_plan_groups,
    _or_groups,
)

PLAN = '("QC tech" OR "QC analyst") AND (microarray OR GeneChip)'
# The relaxed pass searches only group 1 -> its parsed groups are:
SEARCHED_GROUPS = [g for q in ['"QC tech" OR "QC analyst"'] for g in _or_groups(q)]


def _rows():
    return [
        {  # matches only the searched group (QC analyst) -> KEEP under fix
            "source_url": "https://linkedin.com/in/jane",
            "headline": "QC Analyst at Illumina",
            "location": "Singapore",
        },
        {  # matches only group 2's vocabulary -> KEEP (matches searched set)
            "source_url": "https://linkedin.com/in/john",
            "headline": "Microarray Scientist at A*STAR",
        },
        {  # matches only the searched group (QC Tech) -> KEEP under fix
            "source_url": "https://linkedin.com/in/amy",
            "headline": "Senior QC Tech, genomics lab",
        },
    ]


def test_regression_relaxed_rows_kept_when_gating_searched_group():
    """Regression: rows matching the searched group must not be dropped.

    The relaxed pass searched only group 1 ("QC tech" OR "QC analyst"), so
    Jane (QC Analyst) and Amy (QC Tech) are relevant; John matches only the
    never-searched group 2 and is legitimately irrelevant to THIS search.
    """
    rows = _rows()
    kept, dropped = _gate_relaxed_rows(rows, [PLAN], gate_groups=SEARCHED_GROUPS)
    kept_urls = [r["source_url"] for r in kept]
    assert "https://linkedin.com/in/jane" in kept_urls
    assert "https://linkedin.com/in/amy" in kept_urls
    assert dropped == 1
    assert kept_urls == [
        "https://linkedin.com/in/jane",
        "https://linkedin.com/in/amy",
    ]


def test_regression_before_fix_all_dropped():
    """Documents the pre-fix full-plan gating that zeroed every relevant row."""
    rows = _rows()
    kept, dropped = _gate_relaxed_rows(rows, [PLAN])  # gate_groups=None == old
    assert kept == []
    assert dropped == 3


def test_skills_field_is_matchable():
    """A row whose only signal is a skills list must now be matchable."""
    row = {"source_url": "x", "skills": ["microarray"]}
    assert _matches_plan_groups(row, [["microarray", "genechip"]]) is True


def test_list_valued_hay_does_not_crash_and_matches():
    """List values (skills) are joined, not str()'d into bracket noise."""
    row = {
        "source_url": "x",
        "skills": ["microarray", "genechip"],
        "headline": "QC Analyst",
    }
    searched = [g for q in ['"QC tech" OR "QC analyst"'] for g in _or_groups(q)]
    kept, dropped = _gate_relaxed_rows([row], [PLAN], gate_groups=searched)
    assert kept == [row]
    assert dropped == 0


def test_irrelevant_row_still_dropped():
    """A row matching no group at all is still dropped."""
    row = {"source_url": "x", "headline": "In-House Counsel | Privacy Law"}
    kept, dropped = _gate_relaxed_rows([row], [PLAN], gate_groups=SEARCHED_GROUPS)
    assert kept == []
    assert dropped == 1

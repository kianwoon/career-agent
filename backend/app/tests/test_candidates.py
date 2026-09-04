"""Tests for candidate search helpers and scoring."""

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

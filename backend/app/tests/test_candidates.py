"""Tests for candidate search helpers and scoring."""

from app.agent.nodes import _extract_skills
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

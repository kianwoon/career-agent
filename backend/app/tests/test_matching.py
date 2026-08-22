"""Unit tests for the matching engine."""

from app.services.matching import score_candidate, score_job

PROFILE = {
    "headline": "AI Power User",
    "summary": "Applying frontier models to real-world problems; technology leadership",
    "skills": ["ai", "ml", "leadership", "platform", "engineering", "product"],
    "preferences": {"location": "Singapore"},
}


def test_score_job_returns_match_result():
    job = {
        "id": "j1",
        "title": "Head of AI Platform Engineering",
        "company": "Test Corp",
        "location": "Singapore",
        "source": "test",
        "source_url": "https://example.com/jobs/1",
        "description": "We need AI and ML platform leadership for our engineering team.",
    }
    result = score_job(job, PROFILE)
    assert result.match_score > 0
    assert result.title == "Head of AI Platform Engineering"
    assert any(e.field == "capability" for e in result.evidence)


def test_score_candidate_returns_match_result():
    candidate = {
        "id": "c1",
        "name": "Jane Doe",
        "headline": "Senior Platform Engineer",
        "location": "Singapore",
        "skills": ["java", "kafka", "payments", "microservices"],
        "source": "test",
        "source_url": "https://example.com/c/1",
        "experience": "Built payments platforms with Java and Kafka.",
    }
    job = {
        "title": "Payments Engineer",
        "location": "Singapore",
        "description": "Kafka and Java experience required",
        "required_skills": ["java", "kafka", "payments"],
    }
    result = score_candidate(candidate, job)
    assert result.match_score > 50
    assert any(e.field == "mandatory_skills" for e in result.evidence)

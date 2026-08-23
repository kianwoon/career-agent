"""Unit tests for the MyCareersFuture adapter.

The search/detail HTTP calls are network-dependent, so the pure mapping
functions are tested against fixtures that mirror the live MCF API shapes.
"""

from app.services.mycareersfuture import (
    _extract_skills,
    _format_location,
    _format_salary,
    _strip_html,
    _to_raw_job,
)

SAMPLE_ITEM = {
    "uuid": "c4bc487b2266d866a59db2bc77a7c815",
    "title": "Backend/ Full Stack Engineer",
    "metadata": {
        "jobPostId": "MCF-2026-1462969",
        "newPostingDate": "2026-08-23",
        "jobDetailsUrl": "https://www.mycareersfuture.gov.sg/job/information-technology/backend-full-stack-engineer-intuit-recruitment-c4bc487b2266d866a59db2bc77a7c815",
    },
    "address": {
        "building": "20 COLLYER QUAY",
        "postalCode": "049319",
        "districts": [{"region": "Central", "location": "D01 Marina"}],
    },
    "salary": {"minimum": 5000, "maximum": 7600, "type": {"salaryType": "Monthly"}},
    "postedCompany": {"name": "INTUIT RECRUITMENT PTE. LTD."},
    "skills": [{"skill": "Node.js", "isKeySkill": True}, {"skill": "Terraform"}],
    "employmentTypes": [{"employmentType": "Contract"}, {"employmentType": "Full Time"}],
    "categories": [{"category": "Information Technology"}],
    "positionLevels": [{"position": "Senior Executive"}],
}

SAMPLE_DETAIL = {
    "uuid": "c4bc487b2266d866a59db2bc77a7c815",
    "title": "Backend/ Full Stack Engineer",
    "description": "<p>Build <strong>APIs</strong> with Node.js.</p><ul><li>Ship features</li></ul>",
    "minimumYearsExperience": 1,
    "numberOfVacancies": 2,
    "otherRequirements": "<p>PDPA consent required.</p>",
    "workingHours": "Monday to Friday",
    "salary": {"minimum": 5500, "maximum": 8000, "type": {"salaryType": "Monthly"}},
    "metadata": {"expiryDate": "2026-09-22"},
}


def test_strip_html():
    assert _strip_html("<p>Hello <strong>world</strong></p>") == "Hello world"
    assert _strip_html(None) == ""
    assert _strip_html("<ul><li>One</li><li>Two</li></ul>") == "One Two"


def test_format_salary_range():
    salary = {"minimum": 5000, "maximum": 7600, "type": {"salaryType": "Monthly"}}
    assert _format_salary(salary) == "SGD 5,000 - 7,600 monthly"


def test_format_salary_single_value():
    salary = {"minimum": 8000, "maximum": None, "type": {"salaryType": "Monthly"}}
    assert _format_salary(salary) == "SGD 8,000 monthly"


def test_format_salary_none():
    assert _format_salary(None) is None


def test_format_location():
    addr = {"building": "20 COLLYER QUAY", "postalCode": "049319", "districts": [{"region": "Central"}]}
    assert _format_location(addr) == "20 COLLYER QUAY, Singapore 049319, Central"


def test_format_location_minimal():
    assert _format_location({}) == ""


def test_extract_skills():
    skills = [{"skill": "Node.js"}, {"skill": "Terraform"}, {"skill": None}]
    assert _extract_skills(skills) == ["Node.js", "Terraform"]


def test_to_raw_job_maps_search_item():
    raw = _to_raw_job(SAMPLE_ITEM)
    assert raw["id"] == "MCF-2026-1462969"
    assert raw["title"] == "Backend/ Full Stack Engineer"
    assert raw["company"] == "INTUIT RECRUITMENT PTE. LTD."
    assert raw["source"] == "mycareersfuture"
    assert "mycareersfuture.gov.sg" in raw["source_url"]
    assert raw["salary_text"] == "SGD 5,000 - 7,600 monthly"
    assert raw["posted_at"] == "2026-08-23"
    assert raw["employment_type"] == "Contract, Full Time"
    assert raw["skills"] == ["Node.js", "Terraform"]
    assert raw["category"] == "Information Technology"
    assert raw["position_level"] == "Senior Executive"
    assert "COLLYER QUAY" in raw["location"]
    # No detail yet -> description empty
    assert raw["description"] == ""


def test_to_raw_job_enriches_with_detail():
    raw = _to_raw_job(SAMPLE_ITEM, SAMPLE_DETAIL)
    assert "Build" in raw["description"]
    assert "PDPA" in raw["other_requirements"]
    assert raw["minimum_years_experience"] == 1
    assert raw["number_of_vacancies"] == 2
    assert raw["working_hours"] == "Monday to Friday"
    assert raw["expiry_date"] == "2026-09-22"
    # Detail salary overrides search salary
    assert raw["salary_text"] == "SGD 5,500 - 8,000 monthly"

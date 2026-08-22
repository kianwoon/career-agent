"""Tests for the credibility / signal-validation module."""

from app.services.credibility import assess_credibility, parse_roles, _duration_to_months

AVP_EXPERIENCE = """Experience
Assistant Vice President
OCBC Bank · Full-time
Apr 2024 - Present · 2 yrs 5 mos
Singapore · On-site
- Architecting Java/J2EE backend solutions for banking products.
Senior Java Software Engineer
NCS Group · Full-time
Feb 2021 - Nov 2023 · 2 yrs 10 mos
Singapore · On-site
- Developed Java microservices with Spring Boot.
Senior Software Engineer
Kelly Services · Full-time
Jul 2019 - Feb 2021 · 1 yr 8 mos
Singapore · On-site
- Built REST APIs in Java."""

REAL_LEAD_EXPERIENCE = """Experience
Head of Engineering
TechCorp · Full-time
Jan 2018 - Present · 8 yrs
Singapore · On-site
- Led 40-engineer org building microservices platforms on AWS.
- Architected Kafka event streaming for payments.
Senior Engineer
OldCo · Full-time
Mar 2013 - Dec 2017 · 4 yrs 9 mos
- Built Java services."""


def test_duration_to_months():
    assert _duration_to_months("Apr 2024 - Present · 2 yrs 5 mos") == 29
    assert _duration_to_months("Jul 2019 - Feb 2021 · 1 yr 8 mos") == 20
    assert _duration_to_months("no duration here") == 0


def test_parse_roles_extracts_title_company_duration():
    roles = parse_roles(AVP_EXPERIENCE)
    assert len(roles) >= 2
    first = roles[0]
    assert "Assistant Vice President" in first.title
    assert "OCBC" in first.company
    assert first.months == 29
    assert "Architecting Java" in first.bullets


def test_avp_title_inflation_detected():
    rep = assess_credibility({
        "name": "Amol",
        "headline": "Assistant Vice President at OCBC",
        "skills": ["Java", "Spring Boot", "Microservices"],
        "experience": AVP_EXPERIENCE,
    })
    assert rep.title_inflation > 0.2
    assert any("AVP" in f or "IC-level" in f or "Assistant Vice President" in f for f in rep.flags)


def test_real_leadership_has_low_inflation():
    rep = assess_credibility({
        "name": "Carol",
        "headline": "Head of Engineering",
        "skills": ["Java", "Kafka", "AWS"],
        "experience": REAL_LEAD_EXPERIENCE,
    })
    assert rep.title_inflation == 0.0
    assert rep.score > 60
    assert rep.tenure_depth > 0.7


def test_short_tenure_grand_title_flagged():
    rep = assess_credibility({
        "name": "Dave",
        "headline": "Chief Technology Officer",
        "skills": ["Python"],
        "experience": """Experience
Chief Technology Officer
StartupX · Full-time
Jan 2024 - Present · 6 mos
- Built a small website in Python.""",
    })
    assert rep.title_inflation > 0.4
    assert any("only 6m" in f or "6m" in f for f in rep.flags)

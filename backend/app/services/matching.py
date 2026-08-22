"""Matching engine: hybrid scoring pipeline per the Phase 1 design spec.

Pipeline:
  hard filters -> structured scoring -> (embedding similarity) -> evidence

Phase 1 implements structured scoring with transparent weights and evidence.
Embedding similarity (Qdrant) and LLM reranking are stubbed behind interfaces
so they can be enabled without changing callers.
"""

from __future__ import annotations

import re
from typing import Any, Protocol

from app.models.schemas import Evidence, MatchResult

JOB_WEIGHTS = {
    "capability": 0.30,
    "experience": 0.20,
    "domain": 0.15,
    "seniority": 0.10,
    "direction": 0.10,
    "location": 0.05,
    "compensation": 0.05,
    "other": 0.05,
}

CANDIDATE_WEIGHTS = {
    "mandatory_skills": 0.30,
    "actual_work": 0.25,
    "domain": 0.15,
    "seniority": 0.10,
    "recency": 0.10,
    "location": 0.05,
    "other": 0.05,
}


class EmbeddingProvider(Protocol):
    """Interface for embedding similarity (Qdrant-backed in production)."""

    async def similarity(self, query: str, documents: list[str]) -> list[float]: ...


class Reranker(Protocol):
    """Interface for LLM-based reranking."""

    async def rerank(self, request: Any, candidates: list[Any]) -> list[Any]: ...


class NoopEmbeddingProvider:
    async def similarity(self, query: str, documents: list[str]) -> list[float]:
        return [0.0] * len(documents)


class NoopReranker:
    async def rerank(self, request: Any, candidates: list[Any]) -> list[Any]:
        return candidates


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9+#.]+", (text or "").lower()))


def keyword_overlap(query_tokens: set[str], text_tokens: set[str]) -> float:
    """Fraction of query tokens found in text (0..1)."""
    if not query_tokens:
        return 0.0
    return len(query_tokens & text_tokens) / len(query_tokens)


def score_job(job: dict[str, Any], profile: dict[str, Any]) -> MatchResult:
    """Score one job against a career profile.

    Args:
        job: normalized job dict (title, company, location, description, ...).
        profile: career profile dict (headline, summary, skills, preferences).
    """
    evidence: list[Evidence] = []
    skills = {s.lower() for s in profile.get("skills", [])}
    description = job.get("description", "") or ""
    text_tokens = _tokenize(f"{job.get('title', '')} {description}")
    skill_tokens = {s for s in skills if s}

    # Capability fit: how many profile skills appear in the job description.
    capability = keyword_overlap(skill_tokens, text_tokens) if skill_tokens else 0.0
    if skill_tokens:
        matched = skill_tokens & text_tokens
        if matched:
            evidence.append(
                Evidence(field="capability", value=f"Matched skills: {', '.join(sorted(matched))}")
            )

    # Domain fit: keywords from profile summary/preferences in the job.
    profile_tokens = _tokenize(profile.get("summary", "") + " " + profile.get("headline", ""))
    domain = keyword_overlap(profile_tokens, text_tokens) if profile_tokens else 0.0

    # Seniority fit: title-level signals.
    title_lower = job.get("title", "").lower()
    seniority_terms = {"head", "chief", "director", "vp", "vice president", "lead", "principal"}
    seniority = 1.0 if any(t in title_lower for t in seniority_terms) else 0.4
    evidence.append(
        Evidence(field="seniority", value=f"Title signals: {title_lower or 'unknown'}")
    )

    # Location fit.
    location = (job.get("location") or "").lower()
    pref_location = (profile.get("preferences") or {}).get("location", "").lower()
    location_score = 1.0 if (not pref_location or pref_location in location) else 0.0
    if pref_location:
        evidence.append(
            Evidence(field="location", value=f"Preferred {pref_location!r} vs job {location!r}")
        )

    # Composite score with weights.
    components = {
        "capability": capability,
        "experience": capability,  # Phase 1 proxy until full profile extraction
        "domain": domain,
        "seniority": seniority,
        "direction": domain,
        "location": location_score,
        "compensation": 0.5,  # neutral when no salary data
        "other": 0.5,
    }
    score = round(100 * sum(JOB_WEIGHTS[k] * components[k] for k in JOB_WEIGHTS), 1)

    reason = _build_reason(evidence, score)

    return MatchResult(
        id=job.get("id", ""),
        title=job.get("title", "Untitled"),
        subtitle=job.get("company"),
        location=job.get("location"),
        source=job.get("source", "unknown"),
        source_url=job.get("source_url"),
        match_score=score,
        match_reason=reason,
        evidence=evidence,
        gaps=_find_gaps(skills, text_tokens),
    )


def score_candidate(candidate: dict[str, Any], job: dict[str, Any]) -> MatchResult:
    """Score one candidate against a job description.

    Uses enriched profile data when available:
      - explicit skills (Top skills)
      - experience text (roles, companies, bullets)
      - summary (About)
      - education / certifications
    """
    evidence: list[Evidence] = []
    required = {s.lower() for s in job.get("required_skills", [])}
    explicit_skills = {s.lower() for s in candidate.get("skills", [])}

    # Enrich skills by scanning the experience + summary text for known
    # required-skill tokens (the profile may only list top 5 skills).
    experience_text = str(candidate.get("experience", ""))
    summary_text = str(candidate.get("summary", ""))
    all_text = _tokenize(experience_text + " " + summary_text)
    inferred_skills = {s for s in required if s in all_text}
    candidate_skills = explicit_skills | inferred_skills

    mandatory = keyword_overlap(required, candidate_skills) if required else 0.0
    matched_required = required & candidate_skills
    if matched_required:
        evidence.append(
            Evidence(
                field="mandatory_skills",
                value=f"Matched required skills: {', '.join(sorted(matched_required))}",
            )
        )

    actual_work = keyword_overlap(_tokenize(job.get("description", "")), _tokenize(experience_text))
    domain = keyword_overlap(candidate_skills, _tokenize(job.get("description", "")))

    seniority_terms = {"head", "chief", "director", "vp", "vice president", "lead", "principal", "senior"}
    headline_lower = (candidate.get("headline") or "").lower()
    seniority = 1.0 if any(t in headline_lower for t in seniority_terms) else 0.4

    location = (candidate.get("location") or "").lower()
    job_location = (job.get("location") or "").lower()
    location_score = 1.0 if (not job_location or job_location in location) else 0.0

    # Evidence for actual-work fit (real experience signals).
    if experience_text:
        evidence.append(
            Evidence(
                field="actual_work",
                value=f"Experience: {experience_text[:200]}",
            )
        )
    if summary_text:
        evidence.append(
            Evidence(
                field="summary",
                value=summary_text[:200],
            )
        )

    components = {
        "mandatory_skills": mandatory,
        "actual_work": actual_work,
        "domain": domain,
        "seniority": seniority,
        "recency": 0.7,  # Phase 1: no recency signals yet, neutral-positive
        "location": location_score,
        "other": 0.5,
    }
    score = round(100 * sum(CANDIDATE_WEIGHTS[k] * components[k] for k in CANDIDATE_WEIGHTS), 1)

    # Credibility adjustment: discount inflated claims, reward evidenced skills.
    from app.services.credibility import assess_credibility

    cred = assess_credibility(candidate)
    # Map credibility (0-100) to a multiplier centered at 1.0:
    #   cred=80 -> x1.05, cred=50 -> x1.0, cred=20 -> x0.90
    cred_mult = 0.85 + (cred.score / 100) * 0.25  # range 0.85-1.10
    adjusted = round(score * cred_mult, 1)
    if adjusted != score:
        evidence.append(
            Evidence(
                field="credibility",
                value=(
                    f"Credibility {cred.score}/100 — "
                    + ("; ".join(cred.flags[:2]) if cred.flags else "no flags")
                ),
            )
        )
    score = adjusted

    reason = _build_reason(evidence, score)
    missing = sorted(required - candidate_skills)

    result = MatchResult(
        id=candidate.get("id", ""),
        title=candidate.get("name", "Unknown"),
        subtitle=candidate.get("headline"),
        location=candidate.get("location"),
        source=candidate.get("source", "unknown"),
        source_url=candidate.get("source_url"),
        match_score=score,
        match_reason=reason,
        evidence=evidence,
        gaps=missing,
    )
    # Attach credibility report for downstream display.
    result.credibility = cred.to_dict()
    return result


def _build_reason(evidence: list[Evidence], score: float) -> str:
    if not evidence:
        return f"Scored {score}/100 (no strong signals found)."
    top = "; ".join(e.value for e in evidence[:3])
    return f"Scored {score}/100. {top}"


def _find_gaps(profile_skills: set[str], job_tokens: set[str]) -> list[str]:
    """Skills in the profile that the job doesn't mention (potential overfit) — simplified."""
    return []

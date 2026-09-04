"""Pydantic models / schemas for the Career Agent API."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SearchType(str, Enum):
    jobs = "jobs"
    candidates = "candidates"


class TaskStatus(str, Enum):
    pending = "pending"
    running = "running"
    paused = "paused"
    waiting_approval = "waiting_approval"
    completed = "completed"
    failed = "failed"


class ApprovalDecision(str, Enum):
    approve = "approve"
    reject = "reject"


class MatchRole(str, Enum):
    """Which side of the match we are scoring."""

    job = "job"
    candidate = "candidate"


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


class JobSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Natural-language job search request")
    location: str | None = Field(default=None, description="Location filter, e.g. Singapore")
    sources: list[str] | None = Field(
        default=None,
        description="Source IDs to include; None/empty = all enabled sources (built-in + custom)",
    )


# Hard caps on sourcing-plan size — each query is a separate paced page
# load on LinkedIn, so more than a handful is a rate-limit/ban risk.
MAX_PLAN_QUERIES = 5
MAX_PLAN_EXCLUDES = 10
MAX_PLAN_PLATFORMS = 5


class CandidateSearchRequest(BaseModel):
    """A candidate sourcing plan.

    Mirrors the external system's analysis panel: platform, boolean queries,
    exclude terms, salary/location/employment-type context. The simple
    single-string `query` remains accepted for backward compatibility and is
    treated as a one-query plan.
    """

    query: str | None = Field(
        default=None,
        description="Simple candidate criteria; treated as a one-query plan when `queries` is absent",
    )
    queries: list[str] | None = Field(
        default=None,
        description=(
            "Boolean search queries run in sequence and merged. More than "
            f"{MAX_PLAN_QUERIES} is accepted but silently truncated to the first "
            f"{MAX_PLAN_QUERIES} (each query is a paced page load — ban risk)"
        ),
    )
    exclude: list[str] | None = Field(
        default=None,
        description=(
            "Terms to exclude via NOT (...) and post-filter. More than "
            f"{MAX_PLAN_EXCLUDES} is accepted but silently truncated to the first "
            f"{MAX_PLAN_EXCLUDES} (boolean engines return zero results with too "
            "many NOT terms)"
        ),
    )
    platform: str | None = Field(
        default=None,
        description="Legacy single platform, e.g. 'LinkedIn'. Prefer `platforms`",
    )
    platforms: list[str] | None = Field(
        default=None,
        description=(
            "Search platforms to run in sequence and merge, e.g. ['LinkedIn']. "
            f"More than {MAX_PLAN_PLATFORMS} is accepted but silently truncated to "
            f"the first {MAX_PLAN_PLATFORMS}. Unknown platforms are rejected"
        ),
    )
    salary: str | None = Field(
        default=None,
        description="Salary context from the plan (not searchable on people search; used as ranking criteria)",
    )
    employment_type: str | None = Field(
        default=None,
        description="Employment type context from the plan (ranking criteria only)",
    )
    location: str | None = Field(default=None, description="Location filter, e.g. Singapore")
    sources: list[str] | None = Field(
        default=None,
        description="Source IDs to include; None/empty = all enabled sources (built-in + custom)",
    )

    def plan_queries(self) -> list[str]:
        """Normalized query list (queries[] or the single query), capped at MAX_PLAN_QUERIES.

        Oversized inputs are truncated, not rejected — external callers may
        send large lists and must not get a 422 for it.
        """
        if self.queries:
            cleaned = [q.strip() for q in self.queries if q and q.strip()]
            return cleaned[:MAX_PLAN_QUERIES]
        return [self.query.strip()] if self.query and self.query.strip() else []

    def plan_platforms(self) -> list[str]:
        """Normalized platform list (platforms[] or the legacy single platform), capped at MAX_PLAN_PLATFORMS."""
        raw = self.platforms or ([self.platform] if self.platform else [])
        return [p.strip() for p in raw if p and p.strip()][:MAX_PLAN_PLATFORMS]


class TaskStatusResponse(BaseModel):
    task_id: str
    type: SearchType
    status: TaskStatus
    workflow_state: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None


class ApprovalRequest(BaseModel):
    decision: ApprovalDecision
    note: str | None = None


class BrowserTakeoverRequest(BaseModel):
    session_id: str
    action: str = Field(default="start", pattern="^(start|return|status)$")


# ---------------------------------------------------------------------------
# Results / entities
# ---------------------------------------------------------------------------


class Evidence(BaseModel):
    """A single piece of traceable evidence backing a match score."""

    field: str
    value: str
    source_url: str | None = None
    source_text: str | None = None


class MatchResult(BaseModel):
    """A ranked job or candidate with score and supporting evidence."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    subtitle: str | None = None  # company (jobs) or headline (candidates)
    location: str | None = None
    source: str
    # Display-friendly platform label derived from `source`
    # (e.g. "LinkedIn", "JobStreet") — for UI badges.
    source_platform: str | None = None
    source_url: str | None = None
    match_score: float = Field(..., ge=0.0, le=100.0)
    match_reason: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    recommended_action: str | None = None
    status: str = "new"
    discovered_at: datetime = Field(default_factory=datetime.utcnow)
    # Candidate enrichment fields (populated for candidate results so they
    # survive response serialization even when typed as MatchResult).
    summary: str | None = None
    skills: list[str] = Field(default_factory=list)
    experience: str | None = None
    education: str | None = None
    certifications: str | None = None
    # Credibility assessment (candidate mode).
    credibility: dict[str, Any] | None = None


class JobMatchResult(MatchResult):
    company: str | None = None
    posted_at: str | None = None
    employment_type: str | None = None
    salary_text: str | None = None
    description: str | None = None


class CandidateMatchResult(MatchResult):
    """Candidate result — enrichment fields are on the base MatchResult so
    they survive serialization via SearchTaskResult."""



class SearchTaskResult(BaseModel):
    task_id: str
    status: TaskStatus
    results: list[MatchResult] = Field(default_factory=list)
    summary: str | None = None
    # Sourcing-plan execution detail (queries run, relaxed variants, filters).
    plan_detail: str | None = None
    # Populated when sources failed (e.g. expired login) — "Source: reason".
    source_issues: list[str] = Field(default_factory=list)


class ActivityEvent(BaseModel):
    """One entry in the agent activity timeline."""

    timestamp: datetime = Field(default_factory=datetime.utcnow)
    step: str
    detail: str | None = None
    url: str | None = None
    duration_ms: int | None = None


class SearchHistoryItem(BaseModel):
    """One past search task, for the history browser."""

    task_id: str
    type: SearchType
    query: str
    status: TaskStatus
    result_count: int = 0
    created_at: datetime
    completed_at: datetime | None = None


class SearchHistoryResponse(BaseModel):
    items: list[SearchHistoryItem] = Field(default_factory=list)


class BrowserSessionView(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    session_id: str
    status: str = "idle"
    url: str | None = None
    title: str | None = None
    needs_human: bool = False
    reason: str | None = None


# ---------------------------------------------------------------------------
# Pluggable sources
# ---------------------------------------------------------------------------


class SourceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    base_url: str = Field(..., min_length=1, max_length=1000)


class SourceView(BaseModel):
    id: str
    name: str
    base_url: str
    domain: str
    enabled: bool
    has_session: bool = False
    flows: dict[str, str] = Field(default_factory=dict, description="flow_type -> status")
    created_at: datetime


class WizardStartRequest(BaseModel):
    """Start a guided wizard step. `mode`: login | record."""
    mode: str = Field(..., pattern="^(login|record)$")
    flow_type: str | None = Field(default=None, pattern="^(find_jobs|find_candidates)$")


class WizardStartResponse(BaseModel):
    wizard_id: str
    mode: str
    start_url: str


class WizardEvent(BaseModel):
    action: str
    selector: str
    text: str | None = None
    value: str | None = None
    url: str | None = None


class WizardPollResponse(BaseModel):
    events: list[WizardEvent] = Field(default_factory=list)
    total_events: int = 0


class WizardCompleteRequest(BaseModel):
    query_hint: str | None = Field(default=None, description="The search text demonstrated, to bind as the query parameter")


class WizardCompleteResponse(BaseModel):
    flow_id: str | None = None
    steps: list[dict[str, Any]] = Field(default_factory=list)
    card_selectors: dict[str, Any] | None = None


class SourceFlowView(BaseModel):
    id: str
    source_id: str
    flow_type: str
    steps: list[dict[str, Any]]
    status: str
    created_at: datetime
    # Set when re-record verification could not confirm the extract step
    # (e.g. login wall during the probe) — tells the caller the flow is
    # saved but UNVERIFIED.
    note: str | None = None


class SourceFlowUpdate(BaseModel):
    steps: list[dict[str, Any]] | None = None
    status: str | None = Field(default=None, pattern="^(active|broken|draft)$")

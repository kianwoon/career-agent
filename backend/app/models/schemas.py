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


class CandidateSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Candidate criteria, e.g. 'Java, Kafka, payments, microservices, banking'")
    location: str | None = Field(default=None, description="Location filter, e.g. Singapore")


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

    pass


class SearchTaskResult(BaseModel):
    task_id: str
    status: TaskStatus
    results: list[MatchResult] = Field(default_factory=list)
    summary: str | None = None


class ActivityEvent(BaseModel):
    """One entry in the agent activity timeline."""

    timestamp: datetime = Field(default_factory=datetime.utcnow)
    step: str
    detail: str | None = None
    url: str | None = None
    duration_ms: int | None = None


class BrowserSessionView(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    session_id: str
    status: str = "idle"
    url: str | None = None
    title: str | None = None
    needs_human: bool = False
    reason: str | None = None

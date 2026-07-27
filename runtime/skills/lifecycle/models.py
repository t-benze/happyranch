"""THR-055 lifecycle data models — immutable package-version records, assignments,
and provenance/audit metadata.

All models are Pydantic v2. The lifecycle is append-only: every state-changing
action creates a new immutable record.

Lifecycle states (from PRD §Product Model):
  proposed -> draft -> validated -> in_review -> approved -> published
  -> assigned_not_yet_effective -> effective

  rolled_back / retired are terminal re-assignment states.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


def utcnow() -> datetime:
    """Injectable clock for deterministic tests."""
    return datetime.now(timezone.utc)


# ── Lifecycle state enum ──────────────────────────────────────────────────

class LifecycleStatus(str, Enum):
    """Canonical lifecycle states for a skill package version."""
    PROPOSED = "proposed"
    DRAFT = "draft"
    VALIDATION_FAILED = "validation_failed"
    VALIDATED = "validated"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    PUBLISHED = "published"
    RETIRED = "retired"
    ROLLED_BACK = "rolled_back"


# ── Package version record ────────────────────────────────────────────────

class PackageVersion(BaseModel):
    """An immutable record of a skill package at a specific version + content hash.

    Created once and never mutated. Byte changes fork a new version.
    """

    id: int | None = None  # DB primary key, set on insert
    skill_id: str  # e.g. "hr:my-skill"
    slug: str  # e.g. "my-skill"
    name: str
    version: str  # semantic version
    content_hash: str  # SHA-256 hex of the package content
    policy_class: str = "standard_operational"
    description: str = ""
    skill_md: str = ""  # Full SKILL.md body
    status: LifecycleStatus = LifecycleStatus.PROPOSED
    created_at: datetime = Field(default_factory=utcnow)
    created_by: str = ""  # agent name or "founder"

    # Proposal provenance (agent-authored proposals)
    proposal_task_id: str | None = None
    proposal_session_id: str | None = None
    proposer_agent: str | None = None

    # Review provenance
    reviewer: str | None = None
    review_decision: str | None = None  # "approved" | "rejected"
    review_rationale: str | None = None
    reviewed_at: datetime | None = None

    # Publication provenance
    publisher: str | None = None
    published_at: datetime | None = None
    publication_decision_id: int | None = None  # FK to lifecycle event

    @staticmethod
    def compute_content_hash(skill_md: str, references: dict[str, str] | None = None, assets: dict[str, str] | None = None) -> str:
        """Compute a deterministic SHA-256 hash of the package content."""
        h = hashlib.sha256()
        h.update(skill_md.encode("utf-8"))
        if references:
            for k in sorted(references):
                h.update(k.encode("utf-8"))
                h.update(references[k].encode("utf-8"))
        if assets:
            for k in sorted(assets):
                h.update(k.encode("utf-8"))
                h.update(assets[k].encode("utf-8"))
        return h.hexdigest()

    @field_validator("version")
    @classmethod
    def version_must_be_semver(cls, v: str) -> str:
        parts = v.split(".")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            raise ValueError(f"version must be semver (MAJOR.MINOR.PATCH), got {v!r}")
        return v


class AssignmentRecord(BaseModel):
    """A version-pinned skill assignment to a named agent.

    Assignments are immutable: re-assignment creates a new record.
    """

    id: int | None = None
    skill_id: str
    agent_name: str
    package_version_id: int  # FK to PackageVersion
    version: str  # denormalized for fast lookup
    content_hash: str  # denormalized
    assigned_by: str = ""  # actor who assigned
    assigned_at: datetime = Field(default_factory=utcnow)
    active: bool = True  # False on rollback/unassign

    # Rollback provenance
    rolled_back_by: str | None = None
    rolled_back_at: datetime | None = None
    rollback_reason: str | None = None
    rollback_target_version_id: int | None = None  # FK to replacement PackageVersion


class LifecycleEvent(BaseModel):
    """An append-only lifecycle transition event.

    Every state-changing action records one event row.
    """

    id: int | None = None
    skill_id: str
    package_version_id: int | None = None  # FK to PackageVersion
    event_type: str  # "proposed", "drafted", "validated", "submitted_for_review",
    # "approved", "rejected", "published", "assigned", "unassigned",
    # "rolled_back", "retired", "materialized", "materialization_failed"
    actor: str = ""  # agent name or "founder"
    actor_role: str = ""  # "agent", "founder", "publisher", "reviewer"
    previous_status: str | None = None
    new_status: str | None = None
    content_hash: str | None = None
    metadata: dict | None = None  # Extra provenance data
    created_at: datetime = Field(default_factory=utcnow)

    # Proposal/task linkage
    task_id: str | None = None
    session_id: str | None = None


class MaterializationRecord(BaseModel):
    """Records a materialization attempt at session spawn.

    Stored alongside or replacing skill_validation_events.materialization records.
    """

    id: int | None = None
    skill_id: str
    agent_name: str
    package_version_id: int
    version: str
    content_hash: str
    success: bool
    error_message: str | None = None
    session_context: str | None = None  # "task", "thread", "wake", "dream"
    created_at: datetime = Field(default_factory=utcnow)


# ── API request/response models ───────────────────────────────────────────

class ProposalRequest(BaseModel):
    """Agent-submitted skill proposal — bound to task/session."""
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=512)
    version: str = "0.1.0"
    policy_class: str = "standard_operational"
    skill_md: str = Field(min_length=1)
    purpose: str = ""  # Why this skill is needed
    target_agent_suggestion: str = ""  # Informational only
    references: dict[str, str] | None = None
    assets: dict[str, str] | None = None

    # These are validated server-side — body claims ignored
    task_id: str | None = None
    session_id: str | None = None
    proposer_agent: str | None = None


class ClaimProposalRequest(BaseModel):
    """Human sponsor claims an agent proposal, making it a draft."""
    proposal_version_id: int


class SubmitForReviewRequest(BaseModel):
    """Human sponsor submits a validated version for review."""
    version_id: int
    intended_audience: str = ""
    review_notes: str = ""


class ReviewDecisionRequest(BaseModel):
    """Reviewer approves or rejects a submitted version."""
    version_id: int
    decision: str  # "approved" | "rejected"
    rationale: str = ""


class PublishRequest(BaseModel):
    """Publisher admits an approved version to the catalog."""
    approval_event_id: int  # Must match the approval lifecycle event


class AssignRequest(BaseModel):
    """Eligibility admin assigns a published version to a named agent."""
    agent_name: str
    version_id: int


class RollbackRequest(BaseModel):
    """Emergency rollback: unassign all current assignments for a skill."""
    reason: str = ""
    # Optional: point to a prior version
    target_version_id: int | None = None


class LifecycleStatusResponse(BaseModel):
    """Response for the lifecycle status endpoint."""
    skill_id: str
    slug: str
    current_status: LifecycleStatus
    current_version: str
    current_version_id: int | None = None
    published_version_id: int | None = None
    published_version: str | None = None
    assignments: list[AssignmentRecord] = []
    events: list[LifecycleEvent] = []
    proposal_task_id: str | None = None
    proposer_agent: str | None = None

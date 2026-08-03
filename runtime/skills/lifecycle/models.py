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
    """Canonical lifecycle states for a skill package version.

    Decision lifecycle (package-level, terminal at published or rejected):
        proposed → draft → validated → in_review → approved → published
    Terminal reject: in_review → rejected (immutable, blocks all later mutations)

    Assignment/materialization is a separate projection — never sets package status.
    Historical ROLLED_BACK and RETIRED are assignment-level terminal states only;
    new flows do NOT generate them for package status. Legacy rows retain them.
    """
    PROPOSED = "proposed"
    DRAFT = "draft"
    VALIDATION_FAILED = "validation_failed"
    VALIDATED = "validated"
    IN_REVIEW = "in_review"
    REJECTED = "rejected"  # Terminal — blocks all subsequent mutations
    APPROVED = "approved"
    PUBLISHED = "published"
    RETIRED = "retired"
    ROLLED_BACK = "rolled_back"
    LEGACY_QUARANTINED = "legacy_quarantined"  # Pre-lifecycle data, read-only


# ── Package version record ────────────────────────────────────────────────

class PackageVersion(BaseModel):
    """An immutable record of a skill package at a specific version + content hash.

    Created once and never mutated. Byte changes fork a new version.

    Content retention: the primary content lives in the org artifact store
    (``<org_root>/artifacts/skill-lifecycle/<slug>/<version>/``). The
    ``content_artifact_key`` stores the relative path; ``skill_md`` is a
    transient cache populated on read, not the durable store. This keeps
    the ledger tables lean while content lives under the task-artifact
    retention policy.
    """

    id: int | None = None  # DB primary key, set on insert
    skill_id: str  # e.g. "hr:my-skill"
    slug: str  # e.g. "my-skill"
    name: str
    version: str  # semantic version
    content_hash: str  # SHA-256 hex of the package content
    policy_class: str = "standard_operational"
    description: str = ""
    skill_md: str = ""  # Transient cache of SKILL.md body (loaded on demand)
    content_artifact_key: str | None = None  # Relative path in org artifact store
    status: LifecycleStatus = LifecycleStatus.PROPOSED
    created_at: datetime = Field(default_factory=utcnow)
    created_by: str = ""  # agent name or "founder" (immutable proposer identity)
    claimed_by: str | None = None  # Optional separate founder claimant
    claimed_at: datetime | None = None  # When founder claimed this proposal

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
    def compute_content_hash(skill_md: str) -> str:
        """Compute a deterministic SHA-256 hash of the canonical SKILL.md bytes.

        This hash matches exactly the bytes stored in the artifact store under
        the content-addressed key. References and assets are stored as independent
        artifacts with their own hashes recorded in the lifecycle event metadata.
        """
        return hashlib.sha256(skill_md.encode("utf-8")).hexdigest()

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
    version_id: int  # The PackageVersion to publish
    approval_event_id: int  # Must match the approval lifecycle event


class AssignRequest(BaseModel):
    """Eligibility admin assigns a published version to a named agent."""
    skill_id: str  # e.g. "hr:my-skill"
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


# ── THR-055 Founder-only proposal review API models ──────────────────────

class ProposalQueueRequest(BaseModel):
    """Pagination/filter params for the founder-only proposal queue."""
    status: str | None = None  # Filter by status
    page: int = 1
    page_size: int = 20


class ProposalQueueItem(BaseModel):
    """A single row in the founder-only proposal queue."""
    version_id: int
    skill_id: str
    slug: str
    name: str
    version: str
    content_hash: str
    proposer_agent: str
    claimed_by: str | None = None
    proposal_task_id: str | None = None
    proposal_session_id: str | None = None
    status: LifecycleStatus
    latest_validator_version: str | None = None
    latest_validator_key: str | None = None
    permitted_next_action: str | None = None  # e.g. "claim", "validate", "review", "publish", "assign"
    assigned_agent_count: int = 0
    assigned_agents: list[str] = []
    created_at: str = ""


class ProposalQueueResponse(BaseModel):
    """Paginated/filterable proposal queue."""
    items: list[ProposalQueueItem]
    page: int
    page_size: int
    total: int


class ProposalDetailResponse(BaseModel):
    """Founder-only full proposal detail by version id."""
    version_id: int
    skill_id: str
    slug: str
    name: str
    version: str
    description: str
    content_hash: str
    content_artifact_key: str | None = None
    policy_class: str
    status: LifecycleStatus
    # Immutable author
    proposer_agent: str | None = None
    proposal_task_id: str | None = None
    proposal_session_id: str | None = None
    # Optional separate claimant
    claimed_by: str | None = None
    claimed_at: str | None = None
    # Review provenance
    reviewer: str | None = None
    review_decision: str | None = None
    review_rationale: str | None = None
    reviewed_at: str | None = None
    # Publication provenance
    publisher: str | None = None
    published_at: str | None = None
    # Full append-only events
    events: list[dict] = []
    # Assignment projection
    assignments: list[dict] = []
    # Materialization attempts
    materializations: list[dict] = []
    # Concurrency marker for state-changing operations
    last_event_id: int | None = None
    created_at: str = ""


class ClaimProposalV2Request(BaseModel):
    """Founder claims a proposal (v2 — preserves immutable author)."""
    expected_event_id: int  # Concurrency marker from detail


class ValidateProposalRequest(BaseModel):
    """Founder validates a proposal with reproducible validation."""
    validator_version: str  # e.g. "THR-055/1.0.0"
    expected_event_id: int  # Concurrency marker from detail


class ReviewProposalRequest(BaseModel):
    """Founder review decision on a proposal."""
    decision: str  # "approved" | "rejected"
    rationale: str = ""
    expected_event_id: int  # Concurrency marker from detail


class PublishProposalRequest(BaseModel):
    """Founder publishes an approved proposal."""
    approval_event_id: int
    expected_event_id: int  # Concurrency marker from detail


class AssignProposalRequest(BaseModel):
    """Founder assigns a published proposal to an agent."""
    agent_name: str
    expected_event_id: int  # Concurrency marker from detail


class SubmitReviewProposalRequest(BaseModel):
    """Founder submit-review from VALIDATED to IN_REVIEW with concurrency."""
    expected_event_id: int  # Concurrency marker from detail
    intended_audience: str = ""
    review_notes: str = ""


class RollbackProposalRequest(BaseModel):
    """Founder rollback (assignment-level only, never mutates package status)."""
    reason: str = ""
    expected_event_id: int  # Concurrency marker from detail

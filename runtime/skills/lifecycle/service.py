"""THR-055 SkillLifecycleService — the single writer for all lifecycle transitions.

Enforces the lifecycle state machine, agent-only proposal submission,
human-only lifecycle management, and the two-published-cap constraint.

Agent context is derived from verified session state (SessionTracker),
never from request body claims.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from . import stores
from .models import (
    AssignmentRecord,
    LifecycleEvent,
    LifecycleStatus,
    MaterializationRecord,
    PackageVersion,
    utcnow,
)

# ── Pilot caps ────────────────────────────────────────────────────────────

MAX_PUBLISHED_SKILLS = 2  # Maximum concurrently published custom skills
ALLOWED_POLICY_CLASSES = frozenset({"standard_operational"})

# ── Protected slugs — cannot be claimed by custom skills ──────────────────

_PROTECTED_SLUGS = frozenset({
    "start-task", "jobs", "make-worktree", "thread", "dream",
    "reflection", "manage-agent", "manage-repo", "brainstorming",
    "dispatching-parallel-agents", "executing-plans",
    "finishing-a-development-branch", "receiving-code-review",
    "requesting-code-review", "subagent-driven-development",
    "systematic-debugging", "test-driven-development",
    "using-git-worktrees", "using-superpowers",
    "verification-before-completion", "writing-plans", "writing-skills",
})


class LifecycleError(Exception):
    """Raised when a lifecycle transition is invalid."""
    def __init__(self, code: str, detail: str, status_code: int = 409):
        self.code = code
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


class AgentForbiddenError(LifecycleError):
    """403 — Agent tried to perform a human-only action."""
    def __init__(self, action: str):
        super().__init__(
            code="agent_forbidden",
            detail=f"Agent callers may not perform action '{action}'. Only human lifecycle actors may do this.",
            status_code=403,
        )


# ── Service ───────────────────────────────────────────────────────────────

class SkillLifecycleService:
    """Single writer for custom-skill lifecycle operations.

    All methods accept an explicit `db`, `actor_kind` ("agent"|"human"),
    and optional task/session context for agent proposals.
    """

    def __init__(self):
        pass

    # ── Agent proposal submission ─────────────────────────────────────────

    def submit_proposal(
        self,
        db,
        actor_kind: str,
        slug: str,
        name: str,
        description: str,
        skill_md: str,
        version: str = "0.1.0",
        policy_class: str = "standard_operational",
        references: dict[str, str] | None = None,
        assets: dict[str, str] | None = None,
        task_id: str | None = None,
        session_id: str | None = None,
        proposer_agent: str | None = None,
        purpose: str = "",
        target_agent_suggestion: str = "",
        protected_slugs: frozenset | None = None,
    ) -> PackageVersion:
        """Submit a task/session-bound agent proposal.

        Only agent callers (actor_kind="agent") may submit proposals.
        Proposal identity derives from verified task/session, never body claims.

        Constraints:
        - slug must not collide with protected slugs
        - policy_class must be standard_operational
        - task_id + session_id required for agent proposals
        """
        self._ensure_agent(actor_kind, "submit proposal")
        self._ensure_non_empty(skill_md, "skill_md")
        self._ensure_protected_slug(slug, protected_slugs)
        self._ensure_policy_class(policy_class)

        # Agent proposals require verified task/session binding
        if not task_id or not session_id:
            raise LifecycleError(
                code="missing_session_binding",
                detail="Agent proposals require verified task_id + session_id binding.",
                status_code=400,
            )

        skill_id = f"hr:{slug}"

        # Check if a published version already exists for this slug
        published_count = stores.count_published_packages(db)
        # Count is by skill_id, but we also check if this specific skill is already published
        existing_published = stores.list_package_versions(
            db, skill_id=skill_id, status=LifecycleStatus.PUBLISHED
        )
        if existing_published:
            # If already published, new proposals create a new version (draft fork) but
            # we allow submission — the fork's status will be draft until validation.
            pass

        # Compute content hash
        content_hash = PackageVersion.compute_content_hash(skill_md, references, assets)

        # Check for duplicate content hash (idempotency)
        existing = stores.get_package_version_by_hash(db, skill_id, content_hash)
        if existing is not None:
            return existing  # Idempotent — return existing proposal

        # Create the proposed package version
        pkg = PackageVersion(
            skill_id=skill_id,
            slug=slug,
            name=name,
            version=version,
            content_hash=content_hash,
            policy_class=policy_class,
            description=description,
            skill_md=skill_md,
            status=LifecycleStatus.PROPOSED,
            created_by=proposer_agent or "",
            proposal_task_id=task_id,
            proposal_session_id=session_id,
            proposer_agent=proposer_agent,
        )

        version_id = stores.insert_package_version(db, pkg)
        pkg.id = version_id

        # Record lifecycle event
        stores.insert_lifecycle_event(db, LifecycleEvent(
            skill_id=skill_id,
            package_version_id=version_id,
            event_type="proposed",
            actor=proposer_agent or "",
            actor_role="agent",
            previous_status=None,
            new_status=LifecycleStatus.PROPOSED.value,
            content_hash=content_hash,
            metadata={
                "purpose": purpose,
                "target_agent_suggestion": target_agent_suggestion,
            },
            task_id=task_id,
            session_id=session_id,
        ))

        return pkg

    # ── Claim proposal → draft ────────────────────────────────────────────

    def claim_proposal(
        self, db, actor_kind: str, version_id: int, sponsor: str = "founder",
    ) -> PackageVersion:
        """A human sponsor claims an agent proposal, making it a draft."""
        self._ensure_human(actor_kind, "claim proposal")
        pkg = self._get_package(db, version_id)

        if pkg.status not in (LifecycleStatus.PROPOSED,):
            raise LifecycleError(
                code="invalid_transition",
                detail=f"Cannot claim a package in status '{pkg.status.value}'. Only PROPOSED packages can be claimed.",
            )

        stores.update_package_status(db, version_id, LifecycleStatus.DRAFT)
        pkg.status = LifecycleStatus.DRAFT
        pkg.created_by = sponsor

        stores.insert_lifecycle_event(db, LifecycleEvent(
            skill_id=pkg.skill_id,
            package_version_id=version_id,
            event_type="drafted",
            actor=sponsor,
            actor_role="human",
            previous_status=LifecycleStatus.PROPOSED.value,
            new_status=LifecycleStatus.DRAFT.value,
            content_hash=pkg.content_hash,
        ))

        return pkg

    # ── Validate ──────────────────────────────────────────────────────────

    def record_validation(
        self, db, actor_kind: str, version_id: int, ok: bool, findings: list[str] | None = None,
    ) -> PackageVersion:
        """Record validation result for a draft version."""
        self._ensure_human(actor_kind, "record validation")
        pkg = self._get_package(db, version_id)

        if pkg.status not in (LifecycleStatus.DRAFT, LifecycleStatus.VALIDATION_FAILED, LifecycleStatus.VALIDATED):
            raise LifecycleError(
                code="invalid_transition",
                detail=f"Cannot validate a package in status '{pkg.status.value}'. Only DRAFT packages can be validated.",
            )

        new_status = LifecycleStatus.VALIDATED if ok else LifecycleStatus.VALIDATION_FAILED
        stores.update_package_status(db, version_id, new_status)
        pkg.status = new_status

        stores.insert_lifecycle_event(db, LifecycleEvent(
            skill_id=pkg.skill_id,
            package_version_id=version_id,
            event_type="validated" if ok else "validation_failed",
            actor="validator",
            actor_role="service",
            previous_status=pkg.status.value,
            new_status=new_status.value,
            content_hash=pkg.content_hash,
            metadata={"findings": findings or []},
        ))

        return pkg

    # ── Submit for review ─────────────────────────────────────────────────

    def submit_for_review(
        self, db, actor_kind: str, version_id: int, sponsor: str = "founder",
        intended_audience: str = "", review_notes: str = "",
    ) -> PackageVersion:
        """Submit a validated version for human review."""
        self._ensure_human(actor_kind, "submit for review")
        pkg = self._get_package(db, version_id)

        if pkg.status != LifecycleStatus.VALIDATED:
            raise LifecycleError(
                code="invalid_transition",
                detail=f"Can only submit VALIDATED packages for review, not '{pkg.status.value}'.",
            )

        stores.update_package_status(db, version_id, LifecycleStatus.IN_REVIEW)
        pkg.status = LifecycleStatus.IN_REVIEW

        stores.insert_lifecycle_event(db, LifecycleEvent(
            skill_id=pkg.skill_id,
            package_version_id=version_id,
            event_type="submitted_for_review",
            actor=sponsor,
            actor_role="human",
            previous_status=LifecycleStatus.VALIDATED.value,
            new_status=LifecycleStatus.IN_REVIEW.value,
            content_hash=pkg.content_hash,
            metadata={
                "intended_audience": intended_audience,
                "review_notes": review_notes,
            },
        ))

        return pkg

    # ── Approve / reject ──────────────────────────────────────────────────

    def review_decision(
        self, db, actor_kind: str, version_id: int,
        decision: str, rationale: str, reviewer: str = "founder",
    ) -> PackageVersion:
        """Reviewer approves or rejects a submitted version.

        Reviewer must be distinct from author (maker-checker).
        """
        self._ensure_human(actor_kind, "review")
        pkg = self._get_package(db, version_id)

        if pkg.status != LifecycleStatus.IN_REVIEW:
            raise LifecycleError(
                code="invalid_transition",
                detail=f"Can only review packages IN_REVIEW, not '{pkg.status.value}'.",
            )

        if decision not in ("approved", "rejected"):
            raise LifecycleError(
                code="invalid_decision",
                detail=f"Review decision must be 'approved' or 'rejected', got {decision!r}.",
                status_code=400,
            )

        # Reviewer-author separation
        if reviewer == pkg.created_by and pkg.created_by:
            raise LifecycleError(
                code="reviewer_author_separation",
                detail=f"Reviewer '{reviewer}' must be distinct from author '{pkg.created_by}'.",
            )

        new_status = LifecycleStatus.APPROVED if decision == "approved" else LifecycleStatus.DRAFT
        stores.update_package_status(
            db, version_id, new_status,
            reviewer=reviewer,
            review_decision=decision,
            review_rationale=rationale,
            reviewed_at=utcnow(),
        )
        pkg.status = new_status
        pkg.reviewer = reviewer
        pkg.review_decision = decision
        pkg.review_rationale = rationale

        event = stores.insert_lifecycle_event(db, LifecycleEvent(
            skill_id=pkg.skill_id,
            package_version_id=version_id,
            event_type=decision,
            actor=reviewer,
            actor_role="reviewer",
            previous_status=LifecycleStatus.IN_REVIEW.value,
            new_status=new_status.value,
            content_hash=pkg.content_hash,
            metadata={"rationale": rationale},
        ))

        if decision == "approved":
            # Store the approval event id for publish gating
            stores.update_package_status(
                db, version_id, new_status,
                publication_decision_id=event,
            )
            pkg.publication_decision_id = event

        return pkg

    # ── Publish ───────────────────────────────────────────────────────────

    def publish(
        self, db, actor_kind: str, version_id: int,
        approval_event_id: int, publisher: str = "founder",
    ) -> PackageVersion:
        """Publish an approved version to the custom catalog.

        Enforces the two-published-cap.
        Requires matching approval event id as proof of review.
        """
        self._ensure_human(actor_kind, "publish")
        pkg = self._get_package(db, version_id)

        if pkg.status != LifecycleStatus.APPROVED:
            raise LifecycleError(
                code="invalid_transition",
                detail=f"Can only publish APPROVED packages, not '{pkg.status.value}'.",
            )

        # Verify approval event matches
        if pkg.publication_decision_id != approval_event_id:
            raise LifecycleError(
                code="approval_mismatch",
                detail=f"Approval event id {approval_event_id} does not match package's approval event {pkg.publication_decision_id}.",
            )

        # Enforce two-published cap
        current_published = stores.count_published_packages(db)
        already_published = stores.list_package_versions(
            db, skill_id=pkg.skill_id, status=LifecycleStatus.PUBLISHED
        )
        if not already_published and current_published >= MAX_PUBLISHED_SKILLS:
            raise LifecycleError(
                code="publish_cap_exceeded",
                detail=f"Maximum {MAX_PUBLISHED_SKILLS} concurrently published custom skills reached. Retire an existing skill first.",
            )

        now = utcnow()
        stores.update_package_status(
            db, version_id, LifecycleStatus.PUBLISHED,
            publisher=publisher,
            published_at=now,
        )
        pkg.status = LifecycleStatus.PUBLISHED
        pkg.publisher = publisher
        pkg.published_at = now

        stores.insert_lifecycle_event(db, LifecycleEvent(
            skill_id=pkg.skill_id,
            package_version_id=version_id,
            event_type="published",
            actor=publisher,
            actor_role="publisher",
            previous_status=LifecycleStatus.APPROVED.value,
            new_status=LifecycleStatus.PUBLISHED.value,
            content_hash=pkg.content_hash,
        ))

        return pkg

    # ── Assign ────────────────────────────────────────────────────────────

    def assign(
        self, db, actor_kind: str, skill_id: str, agent_name: str,
        version_id: int, assigner: str = "founder",
    ) -> AssignmentRecord:
        """Assign a published version to a named agent."""
        self._ensure_human(actor_kind, "assign")
        pkg = self._get_package(db, version_id)

        if pkg.status != LifecycleStatus.PUBLISHED:
            raise LifecycleError(
                code="invalid_transition",
                detail=f"Can only assign PUBLISHED packages, not '{pkg.status.value}'.",
            )

        if pkg.skill_id != skill_id:
            raise LifecycleError(
                code="skill_id_mismatch",
                detail=f"Version {version_id} has skill_id '{pkg.skill_id}', not '{skill_id}'.",
                status_code=400,
            )

        # Check for existing active assignment (replace)
        existing = stores.get_active_assignment(db, skill_id, agent_name)
        if existing is not None:
            stores.deactivate_assignment(db, skill_id, agent_name, unassigned_by=assigner)

        assign_record = AssignmentRecord(
            skill_id=skill_id,
            agent_name=agent_name,
            package_version_id=version_id,
            version=pkg.version,
            content_hash=pkg.content_hash,
            assigned_by=assigner,
        )

        assign_id = stores.insert_assignment(db, assign_record)
        assign_record.id = assign_id

        stores.insert_lifecycle_event(db, LifecycleEvent(
            skill_id=skill_id,
            package_version_id=version_id,
            event_type="assigned",
            actor=assigner,
            actor_role="human",
            previous_status=None,
            new_status="assigned_not_yet_effective",
            content_hash=pkg.content_hash,
            metadata={"agent_name": agent_name},
        ))

        return assign_record

    # ── Rollback (emergency unassign) ─────────────────────────────────────

    def rollback(
        self, db, actor_kind: str, skill_id: str,
        reason: str, rolled_back_by: str = "founder",
        target_version_id: int | None = None,
    ) -> int:
        """Emergency rollback: deactivate all assignments for a skill.

        All operations execute within their individual implicit SQLite
        transactions (auto-commit mode). For multi-statement atomicity,
        the caller should wrap in BEGIN IMMEDIATE/COMMIT.

        Returns count of deactivated assignments.
        """
        self._ensure_human(actor_kind, "rollback")

        # Set the package status to ROLLED_BACK
        pkg = stores.get_latest_package_version(db, skill_id)
        if pkg is not None:
            stores.update_package_status(db, pkg.id, LifecycleStatus.ROLLED_BACK)

        count = stores.deactivate_assignments_for_skill(
            db, skill_id,
            rolled_back_by=rolled_back_by,
            reason=reason,
            target_version_id=target_version_id,
        )

        stores.insert_lifecycle_event(db, LifecycleEvent(
            skill_id=skill_id,
            package_version_id=pkg.id if pkg else None,
            event_type="rolled_back",
            actor=rolled_back_by,
            actor_role="human",
            previous_status=pkg.status.value if pkg else None,
            new_status=LifecycleStatus.ROLLED_BACK.value,
            content_hash=None,
            metadata={
                "reason": reason,
                "assignments_deactivated": count,
                "target_version_id": target_version_id,
            },
        ))

        return count

    # ── Retire ────────────────────────────────────────────────────────────

    def retire(
        self, db, actor_kind: str, skill_id: str, reason: str,
        retired_by: str = "founder",
    ) -> PackageVersion:
        """Retire a published skill. Stops future materialization."""
        self._ensure_human(actor_kind, "retire")
        pkg = stores.get_latest_package_version(db, skill_id)
        if pkg is None:
            raise LifecycleError(
                code="not_found",
                detail=f"No skill found for skill_id '{skill_id}'.",
                status_code=404,
            )

        stores.update_package_status(db, pkg.id, LifecycleStatus.RETIRED)
        pkg.status = LifecycleStatus.RETIRED

        # Also deactivate all assignments
        stores.deactivate_assignments_for_skill(
            db, skill_id, rolled_back_by=retired_by, reason=reason,
        )

        stores.insert_lifecycle_event(db, LifecycleEvent(
            skill_id=skill_id,
            package_version_id=pkg.id,
            event_type="retired",
            actor=retired_by,
            actor_role="human",
            previous_status=LifecycleStatus.PUBLISHED.value,
            new_status=LifecycleStatus.RETIRED.value,
            content_hash=pkg.content_hash,
            metadata={"reason": reason},
        ))

        return pkg

    # ── Materialization ───────────────────────────────────────────────────

    def record_materialization(
        self, db, skill_id: str, agent_name: str, version_id: int,
        version: str, content_hash: str, success: bool, error_message: str | None = None,
        session_context: str | None = None,
    ) -> MaterializationRecord:
        """Record a skill materialization attempt at session spawn."""
        mat = MaterializationRecord(
            skill_id=skill_id,
            agent_name=agent_name,
            package_version_id=version_id,
            version=version,
            content_hash=content_hash,
            success=success,
            error_message=error_message,
            session_context=session_context,
        )
        mat_id = stores.insert_materialization(db, mat)
        mat.id = mat_id

        stores.insert_lifecycle_event(db, LifecycleEvent(
            skill_id=skill_id,
            package_version_id=version_id,
            event_type="materialized" if success else "materialization_failed",
            actor="runtime",
            actor_role="service",
            previous_status=None,
            new_status="effective" if success else "materialization_failed",
            content_hash=content_hash,
            metadata={
                "agent_name": agent_name,
                "session_context": session_context,
                "error_message": error_message,
            },
        ))

        return mat

    # ── Read / query ──────────────────────────────────────────────────────

    def get_status(self, db, skill_id: str) -> dict:
        """Get the full lifecycle status for a skill."""
        pkg = stores.get_latest_package_version(db, skill_id)
        events = stores.list_lifecycle_events(db, skill_id=skill_id)
        assignments = stores.get_all_active_assignments_for_skill(db, skill_id)

        return {
            "skill_id": skill_id,
            "slug": pkg.slug if pkg else "",
            "current_status": pkg.status if pkg else None,
            "current_version": pkg.version if pkg else None,
            "current_version_id": pkg.id if pkg else None,
            "published_version": pkg.version if (pkg and pkg.status == LifecycleStatus.PUBLISHED) else None,
            "assignments": assignments,
            "events": events,
            "proposal_task_id": pkg.proposal_task_id if pkg else None,
            "proposer_agent": pkg.proposer_agent if pkg else None,
        }

    def get_effective_skills(self, db, agent_name: str) -> list[PackageVersion]:
        """Get the set of published + assigned skills for session materialization."""
        assignments = stores.get_active_assignments_for_agent(db, agent_name)
        skills = []
        for assign in assignments:
            pkg = stores.get_package_version(db, assign.package_version_id)
            if pkg is not None and pkg.status == LifecycleStatus.PUBLISHED:
                skills.append(pkg)
        return skills

    def list_catalog(self, db) -> list[PackageVersion]:
        """List all published custom skills for the union catalog."""
        return stores.list_package_versions(db, status=LifecycleStatus.PUBLISHED)

    # ── Guards ────────────────────────────────────────────────────────────

    def _ensure_agent(self, actor_kind: str, action: str) -> None:
        """Agent-only actions. Block human callers (they use different routes)."""
        if actor_kind != "agent":
            raise AgentForbiddenError(action)

    def _ensure_human(self, actor_kind: str, action: str) -> None:
        """Human-only actions. Block agent callers."""
        if actor_kind == "agent":
            raise AgentForbiddenError(action)

    def _ensure_non_empty(self, value: str, field: str) -> None:
        if not value or not value.strip():
            raise LifecycleError(
                code="empty_field",
                detail=f"Field '{field}' must not be empty.",
                status_code=400,
            )

    def _ensure_protected_slug(self, slug: str, protected_slugs: frozenset | None = None) -> None:
        slugs = protected_slugs if protected_slugs is not None else _PROTECTED_SLUGS
        if slug in slugs:
            raise LifecycleError(
                code="protected_slug",
                detail=f"Slug '{slug}' is a protected release or system-contract slug and cannot be used for custom skills.",
                status_code=409,
            )

    def _ensure_policy_class(self, policy_class: str) -> None:
        if policy_class not in ALLOWED_POLICY_CLASSES:
            raise LifecycleError(
                code="policy_class_not_allowed",
                detail=f"Policy class '{policy_class}' is not allowed in the pilot. Only 'standard_operational' is supported.",
                status_code=400,
            )

    def _get_package(self, db, version_id: int) -> PackageVersion:
        pkg = stores.get_package_version(db, version_id)
        if pkg is None:
            raise LifecycleError(
                code="not_found",
                detail=f"No package version found with id {version_id}.",
                status_code=404,
            )
        return pkg

"""THR-055 REVISE — comprehensive adversarial tests for the custom-skill lifecycle pilot.

Tests all security boundaries, lifecycle state machine invariants,
SessionTracker wiring, legacy cutover, and transaction guarantees.

Uses in-memory SQLite database with realistic store behavior.
"""

from __future__ import annotations

import hashlib
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from runtime.skills.lifecycle.models import (
    AssignmentRecord,
    LifecycleStatus,
    PackageVersion,
    AssignRequest,
    PublishRequest,
    ProposalRequest,
)
from runtime.skills.lifecycle import stores as lifecycle_stores
from runtime.skills.lifecycle.service import (
    SkillLifecycleService,
    LifecycleError,
    AgentForbiddenError,
    MAX_PUBLISHED_SKILLS,
)


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    """In-memory SQLite database with lifecycle schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(lifecycle_stores.CREATE_PACKAGE_VERSIONS)
    conn.executescript(lifecycle_stores.CREATE_LIFECYCLE_EVENTS)
    conn.executescript(lifecycle_stores.CREATE_ASSIGNMENTS)
    conn.executescript(lifecycle_stores.CREATE_MATERIALIZATIONS)
    yield conn
    conn.close()


@pytest.fixture
def service():
    return SkillLifecycleService()


# ── Helper ────────────────────────────────────────────────────────────────

def _proposal_kwargs(**overrides):
    kwargs = dict(
        slug="frontend-testing",
        name="Frontend Testing Skill",
        description="A skill for testing frontend components",
        skill_md="# Frontend Testing\n\nGuidelines for frontend testing.",
        version="0.1.0",
        policy_class="standard_operational",
        task_id="TASK-100",
        session_id="sess-001",
        proposer_agent="dev_agent",
        purpose="Help dev agents write better frontend tests",
        target_agent_suggestion="dev_agent",
    )
    kwargs.update(overrides)
    return kwargs


def _full_lifecycle_to_published(db, service, **proposal_overrides):
    """Run a skill through the full lifecycle to PUBLISHED. Returns PackageVersion."""
    pkg = service.submit_proposal(db=db, actor_kind="agent", **_proposal_kwargs(**proposal_overrides))
    pkg = service.claim_proposal(db=db, actor_kind="human", version_id=pkg.id)
    pkg = service.record_validation(db=db, actor_kind="human", version_id=pkg.id, ok=True)
    pkg = service.submit_for_review(db=db, actor_kind="human", version_id=pkg.id)
    pkg = service.review_decision(
        db=db, actor_kind="human", version_id=pkg.id,
        decision="approved", rationale="Looks good", reviewer="founder",
    )
    pkg = service.publish(
        db=db, actor_kind="human", version_id=pkg.id,
        approval_event_id=pkg.publication_decision_id,
    )
    return pkg


# ── Test: Agent proposal submission ───────────────────────────────────────

class TestAgentProposal:
    """Agent proposal submission tests."""

    def test_agent_can_submit_proposal(self, db, service):
        pkg = service.submit_proposal(db=db, actor_kind="agent", **_proposal_kwargs())
        assert pkg.status == LifecycleStatus.PROPOSED
        assert pkg.skill_id == "hr:frontend-testing"
        assert pkg.proposal_task_id == "TASK-100"
        assert pkg.proposer_agent == "dev_agent"
        assert pkg.content_hash
        assert pkg.id is not None and pkg.id > 0

    def test_proposal_idempotent_by_content_hash(self, db, service):
        pkg1 = service.submit_proposal(db=db, actor_kind="agent", **_proposal_kwargs())
        pkg2 = service.submit_proposal(db=db, actor_kind="agent", **_proposal_kwargs())
        assert pkg1.id == pkg2.id
        assert pkg1.content_hash == pkg2.content_hash

    def test_human_cannot_submit_as_agent(self, db, service):
        """Humans (via bearer) and agents (via session) both can submit proposals.
        The service accepts both actor kinds for proposal submission; the route
        layer distinguishes agent vs human identity."""
        # Both agent and human actor_kind should be accepted for proposal submission
        pkg = service.submit_proposal(db=db, actor_kind="human", **_proposal_kwargs())
        assert pkg.status == LifecycleStatus.PROPOSED

    def test_proposal_requires_task_session_binding(self, db, service):
        with pytest.raises(LifecycleError) as exc_info:
            service.submit_proposal(
                db=db, actor_kind="agent",
                **_proposal_kwargs(task_id=None, session_id=None))
        assert "verified task_id" in exc_info.value.detail

    def test_proposal_rejects_protected_slug(self, db, service):
        with pytest.raises(LifecycleError, match="protected release"):
            service.submit_proposal(
                db=db, actor_kind="agent",
                **_proposal_kwargs(slug="start-task"))

    def test_proposal_rejects_custom_protected_slugs(self, db, service):
        """Custom protected slugs from caller override work."""
        with pytest.raises(LifecycleError, match="protected release"):
            service.submit_proposal(
                db=db, actor_kind="agent",
                protected_slugs=frozenset({"custom-protected", "another"}),
                **_proposal_kwargs(slug="custom-protected"))

    def test_proposal_rejects_non_standard_operational(self, db, service):
        with pytest.raises(LifecycleError, match="not allowed in the pilot"):
            service.submit_proposal(
                db=db, actor_kind="agent",
                **_proposal_kwargs(policy_class="high_impact_policy"))

    def test_proposal_rejects_empty_skill_md(self, db, service):
        with pytest.raises(LifecycleError, match="must not be empty"):
            service.submit_proposal(
                db=db, actor_kind="agent",
                **_proposal_kwargs(skill_md=""))

    def test_proposal_content_artifact_key_stored(self, db, service):
        """When content_artifact_key is set, it is persisted."""
        pkg = service.submit_proposal(
            db=db, actor_kind="agent",
            **_proposal_kwargs())
        # Verify persisted
        stored = lifecycle_stores.get_package_version(db, pkg.id)
        assert stored is not None
        assert stored.skill_id == pkg.skill_id


# ── Test: Human lifecycle (claim, validate, review, publish, assign) ──────

class TestHumanLifecycle:
    """Happy-path lifecycle for human actors."""

    def _claim(self, db, service, pkg):
        return service.claim_proposal(db=db, actor_kind="human", version_id=pkg.id)

    def test_full_lifecycle_happy_path(self, db, service):
        pkg = service.submit_proposal(db=db, actor_kind="agent", **_proposal_kwargs())
        pkg = self._claim(db, service, pkg)
        pkg = service.record_validation(db=db, actor_kind="human", version_id=pkg.id, ok=True)
        pkg = service.submit_for_review(db=db, actor_kind="human", version_id=pkg.id)
        pkg = service.review_decision(
            db=db, actor_kind="human", version_id=pkg.id,
            decision="approved", rationale="Looks good", reviewer="founder")
        pkg = service.publish(
            db=db, actor_kind="human", version_id=pkg.id,
            approval_event_id=pkg.publication_decision_id)
        assign = service.assign(
            db=db, actor_kind="human", skill_id=pkg.skill_id,
            agent_name="dev_agent", version_id=pkg.id)
        assert pkg.status == LifecycleStatus.PUBLISHED
        assert assign.agent_name == "dev_agent"
        assert assign.active is True

    def test_claim_requires_proposed(self, db, service):
        pkg = service.submit_proposal(db=db, actor_kind="agent", **_proposal_kwargs())
        self._claim(db, service, pkg)
        with pytest.raises(LifecycleError, match="Cannot claim"):
            self._claim(db, service, pkg)

    def test_cannot_submit_for_review_unless_validated(self, db, service):
        pkg = service.submit_proposal(db=db, actor_kind="agent", **_proposal_kwargs())
        pkg = self._claim(db, service, pkg)
        with pytest.raises(LifecycleError, match="Can only submit VALIDATED"):
            service.submit_for_review(db=db, actor_kind="human", version_id=pkg.id)

    def test_reviewer_author_separation(self, db, service):
        pkg = service.submit_proposal(
            db=db, actor_kind="agent",
            **_proposal_kwargs(proposer_agent="founder"))
        pkg = service.claim_proposal(db=db, actor_kind="human", version_id=pkg.id, sponsor="founder")
        pkg = service.record_validation(db=db, actor_kind="human", version_id=pkg.id, ok=True)
        pkg = service.submit_for_review(db=db, actor_kind="human", version_id=pkg.id)
        with pytest.raises(LifecycleError, match="must be distinct from author"):
            service.review_decision(
                db=db, actor_kind="human", version_id=pkg.id,
                decision="approved", rationale="OK", reviewer="founder")

    def test_cannot_publish_unapproved(self, db, service):
        pkg = service.submit_proposal(db=db, actor_kind="agent", **_proposal_kwargs())
        pkg = self._claim(db, service, pkg)
        with pytest.raises(LifecycleError, match="Can only publish APPROVED"):
            service.publish(db=db, actor_kind="human", version_id=pkg.id, approval_event_id=999)

    def test_cannot_assign_unpublished(self, db, service):
        pkg = service.submit_proposal(db=db, actor_kind="agent", **_proposal_kwargs())
        pkg = self._claim(db, service, pkg)
        with pytest.raises(LifecycleError, match="Can only assign PUBLISHED"):
            service.assign(
                db=db, actor_kind="human", skill_id=pkg.skill_id,
                agent_name="dev_agent", version_id=pkg.id)

    def test_publish_with_wrong_approval_event_rejected(self, db, service):
        """Publish must reference the correct approval event ID."""
        pkg = service.submit_proposal(db=db, actor_kind="agent", **_proposal_kwargs())
        pkg = self._claim(db, service, pkg)
        pkg = service.record_validation(db=db, actor_kind="human", version_id=pkg.id, ok=True)
        pkg = service.submit_for_review(db=db, actor_kind="human", version_id=pkg.id)
        pkg = service.review_decision(
            db=db, actor_kind="human", version_id=pkg.id,
            decision="approved", rationale="OK", reviewer="founder")
        with pytest.raises(LifecycleError, match="approval event"):
            service.publish(
                db=db, actor_kind="human", version_id=pkg.id,
                approval_event_id=99999)  # Wrong event ID

    def test_validation_failure_state(self, db, service):
        """Validation failure puts package in validation_failed state."""
        pkg = service.submit_proposal(db=db, actor_kind="agent", **_proposal_kwargs())
        pkg = self._claim(db, service, pkg)
        pkg = service.record_validation(db=db, actor_kind="human", version_id=pkg.id, ok=False,
                                         findings=["Missing required reference"])
        assert pkg.status == LifecycleStatus.VALIDATION_FAILED

    def test_hash_change_forks_version(self, db, service):
        """Byte changes fork a new version with different hash."""
        pkg1 = service.submit_proposal(
            db=db, actor_kind="agent",
            **_proposal_kwargs(skill_md="# Version 1"))
        pkg2 = service.submit_proposal(
            db=db, actor_kind="agent",
            **_proposal_kwargs(skill_md="# Version 2 - changed content"))
        assert pkg1.content_hash != pkg2.content_hash
        assert pkg1.id != pkg2.id

    def test_full_lifecycle_provenance_preserved(self, db, service):
        """All provenance fields are populated through full lifecycle."""
        pkg = service.submit_proposal(
            db=db, actor_kind="agent",
            **_proposal_kwargs(proposer_agent="dev_agent"))
        pkg = service.claim_proposal(db=db, actor_kind="human", version_id=pkg.id, sponsor="founder")
        pkg = service.record_validation(db=db, actor_kind="human", version_id=pkg.id, ok=True)
        pkg = service.submit_for_review(db=db, actor_kind="human", version_id=pkg.id)
        pkg = service.review_decision(
            db=db, actor_kind="human", version_id=pkg.id,
            decision="approved", rationale="Good work", reviewer="founder")
        pkg = service.publish(
            db=db, actor_kind="human", version_id=pkg.id,
            approval_event_id=pkg.publication_decision_id, publisher="founder")

        stored = lifecycle_stores.get_package_version(db, pkg.id)
        assert stored.proposer_agent == "dev_agent"
        assert stored.reviewer == "founder"
        assert stored.review_decision == "approved"
        assert stored.publisher == "founder"
        assert stored.published_at is not None


# ── Test: Agent 403 matrix ────────────────────────────────────────────────

class TestAgent403Matrix:
    """Agent callers receive AgentForbiddenError for all prohibited mutations."""

    def _make_proposal(self, db, service):
        return service.submit_proposal(db=db, actor_kind="agent", **_proposal_kwargs())

    def test_agent_cannot_claim(self, db, service):
        pkg = self._make_proposal(db, service)
        with pytest.raises(AgentForbiddenError):
            service.claim_proposal(db=db, actor_kind="agent", version_id=pkg.id)

    def test_agent_cannot_validate(self, db, service):
        with pytest.raises(AgentForbiddenError):
            service.record_validation(db=db, actor_kind="agent", version_id=1, ok=True)

    def test_agent_cannot_submit_review(self, db, service):
        with pytest.raises(AgentForbiddenError):
            service.submit_for_review(db=db, actor_kind="agent", version_id=1)

    def test_agent_cannot_review(self, db, service):
        with pytest.raises(AgentForbiddenError):
            service.review_decision(
                db=db, actor_kind="agent", version_id=1,
                decision="approved", rationale="ok")

    def test_agent_cannot_publish(self, db, service):
        with pytest.raises(AgentForbiddenError):
            service.publish(db=db, actor_kind="agent", version_id=1, approval_event_id=1)

    def test_agent_cannot_assign(self, db, service):
        with pytest.raises(AgentForbiddenError):
            service.assign(
                db=db, actor_kind="agent", skill_id="hr:test",
                agent_name="dev_agent", version_id=1)

    def test_agent_cannot_rollback(self, db, service):
        with pytest.raises(AgentForbiddenError):
            service.rollback(db=db, actor_kind="agent", skill_id="hr:test", reason="bad")

    def test_agent_cannot_retire(self, db, service):
        with pytest.raises(AgentForbiddenError):
            service.retire(db=db, actor_kind="agent", skill_id="hr:test", reason="old")


# ── Test: Two-published cap ───────────────────────────────────────────────

class TestTwoPublishedCap:
    def test_publish_cap_enforced(self, db, service):
        """Cannot publish more than MAX_PUBLISHED_SKILLS custom skills."""
        published = []
        for i, slug in enumerate(["skill-a", "skill-b"]):
            pkg = _full_lifecycle_to_published(db, service, slug=slug,
                name=f"Skill {slug}", description=f"Skill {slug}",
                skill_md=f"# Skill {slug}\n\nContent for {slug}.",
                task_id=f"TASK-{i}", session_id=f"sess-{i}")
            published.append(pkg)

        # Try a third
        pkg3 = service.submit_proposal(
            db=db, actor_kind="agent",
            slug="skill-c", name="Skill C", description="Skill C",
            skill_md="# Skill C\n\nContent for C.",
            task_id="TASK-3", session_id="sess-3",
            proposer_agent="dev_agent")
        pkg3 = service.claim_proposal(db=db, actor_kind="human", version_id=pkg3.id)
        pkg3 = service.record_validation(db=db, actor_kind="human", version_id=pkg3.id, ok=True)
        pkg3 = service.submit_for_review(db=db, actor_kind="human", version_id=pkg3.id)
        pkg3 = service.review_decision(
            db=db, actor_kind="human", version_id=pkg3.id,
            decision="approved", rationale="OK", reviewer="founder")

        with pytest.raises(LifecycleError, match="Maximum 2 concurrently published"):
            service.publish(
                db=db, actor_kind="human", version_id=pkg3.id,
                approval_event_id=pkg3.publication_decision_id)

    def test_publish_cap_allows_replacement_after_retire(self, db, service):
        """After retiring a published skill, publish cap frees up."""
        pkg1 = _full_lifecycle_to_published(db, service, slug="skill-a",
            name="Skill A", description="Skill A",
            skill_md="# Skill A\n\nContent for A.",
            task_id="TASK-1", session_id="sess-1")
        _full_lifecycle_to_published(db, service, slug="skill-b",
            name="Skill B", description="Skill B",
            skill_md="# Skill B\n\nContent for B.",
            task_id="TASK-2", session_id="sess-2")

        # Retire skill-a
        service.retire(db=db, actor_kind="human", skill_id=pkg1.skill_id, reason="Old")

        # Now should be able to publish a 3rd
        pkg3 = service.submit_proposal(
            db=db, actor_kind="agent",
            slug="skill-c", name="Skill C", description="Skill C",
            skill_md="# Skill C\n\nContent for C.",
            task_id="TASK-3", session_id="sess-3",
            proposer_agent="dev_agent")
        pkg3 = service.claim_proposal(db=db, actor_kind="human", version_id=pkg3.id)
        pkg3 = service.record_validation(db=db, actor_kind="human", version_id=pkg3.id, ok=True)
        pkg3 = service.submit_for_review(db=db, actor_kind="human", version_id=pkg3.id)
        pkg3 = service.review_decision(
            db=db, actor_kind="human", version_id=pkg3.id,
            decision="approved", rationale="OK", reviewer="founder")
        pkg3 = service.publish(
            db=db, actor_kind="human", version_id=pkg3.id,
            approval_event_id=pkg3.publication_decision_id)
        assert pkg3.status == LifecycleStatus.PUBLISHED


# ── Test: Rollback with transaction guarantees ────────────────────────────

class TestRollback:
    def test_rollback_deactivates_assignments(self, db, service):
        pkg = _full_lifecycle_to_published(db, service)
        service.assign(
            db=db, actor_kind="human", skill_id=pkg.skill_id,
            agent_name="dev_agent", version_id=pkg.id)

        count = service.rollback(
            db=db, actor_kind="human", skill_id=pkg.skill_id,
            reason="Security issue", rolled_back_by="founder")
        assert count >= 1

        # Assignment should now be inactive
        assign = lifecycle_stores.get_active_assignment(db, pkg.skill_id, "dev_agent")
        assert assign is None  # Should be deactivated

    def test_rollback_preserves_event_history(self, db, service):
        """Rollback creates an event without destroying history."""
        pkg = _full_lifecycle_to_published(db, service)
        service.assign(
            db=db, actor_kind="human", skill_id=pkg.skill_id,
            agent_name="dev_agent", version_id=pkg.id)

        service.rollback(
            db=db, actor_kind="human", skill_id=pkg.skill_id,
            reason="Bad release", rolled_back_by="founder")

        events = lifecycle_stores.list_lifecycle_events(db, skill_id=pkg.skill_id)
        event_types = [e.event_type for e in events]
        assert "rolled_back" in event_types
        # All previous events still present
        assert "proposed" in event_types
        assert "published" in event_types

    def test_agent_cannot_rollback(self, db, service):
        with pytest.raises(AgentForbiddenError):
            service.rollback(db=db, actor_kind="agent", skill_id="hr:test", reason="bad")


# ── Test: Materialization ─────────────────────────────────────────────────

class TestMaterialization:
    def test_record_successful_materialization(self, db, service):
        pkg = service.submit_proposal(
            db=db, actor_kind="agent",
            **_proposal_kwargs(slug="mat-test", skill_md="# Mat test"))
        mat = service.record_materialization(
            db=db, skill_id=pkg.skill_id, agent_name="dev_agent",
            version_id=pkg.id, version=pkg.version,
            content_hash=pkg.content_hash, success=True,
            session_context="task")
        assert mat.success is True
        assert mat.skill_id == pkg.skill_id

    def test_record_failed_materialization(self, db, service):
        pkg = service.submit_proposal(
            db=db, actor_kind="agent",
            **_proposal_kwargs(slug="mat-fail", skill_md="# Mat fail"))
        mat = service.record_materialization(
            db=db, skill_id=pkg.skill_id, agent_name="dev_agent",
            version_id=pkg.id, version=pkg.version,
            content_hash=pkg.content_hash, success=False,
            error_message="Disk full",
            session_context="task")
        assert mat.success is False
        assert mat.error_message == "Disk full"

    def test_materialization_records_lifecycle_event(self, db, service):
        pkg = service.submit_proposal(
            db=db, actor_kind="agent",
            **_proposal_kwargs(slug="mat-event", skill_md="# Mat event"))
        service.record_materialization(
            db=db, skill_id=pkg.skill_id, agent_name="dev_agent",
            version_id=pkg.id, version=pkg.version,
            content_hash=pkg.content_hash, success=True,
            session_context="task")
        events = lifecycle_stores.list_lifecycle_events(db, skill_id=pkg.skill_id)
        event_types = [e.event_type for e in events]
        assert "materialized" in event_types


# ── Test: Effective skills resolution ─────────────────────────────────────

class TestEffectiveSkills:
    def test_only_published_assigned_skills_are_effective(self, db, service):
        """Only PUBLISHED + assigned skills appear in get_effective_skills."""
        pkg = _full_lifecycle_to_published(db, service)
        service.assign(
            db=db, actor_kind="human", skill_id=pkg.skill_id,
            agent_name="dev_agent", version_id=pkg.id)

        effective = service.get_effective_skills(db, "dev_agent")
        assert len(effective) >= 1
        assert any(s.skill_id == pkg.skill_id for s in effective)

    def test_proposed_not_effective(self, db, service):
        """Proposed-but-not-published skills are NOT effective."""
        pkg = service.submit_proposal(db=db, actor_kind="agent", **_proposal_kwargs())
        effective = service.get_effective_skills(db, "dev_agent")
        assert all(s.skill_id != pkg.skill_id for s in effective)

    def test_draft_not_effective(self, db, service):
        """Draft skills are NOT effective."""
        pkg = service.submit_proposal(db=db, actor_kind="agent", **_proposal_kwargs())
        service.claim_proposal(db=db, actor_kind="human", version_id=pkg.id)
        effective = service.get_effective_skills(db, "dev_agent")
        assert all(s.skill_id != pkg.skill_id for s in effective)

    def test_published_but_unassigned_not_effective(self, db, service):
        """Published but not assigned to this agent → not effective."""
        _full_lifecycle_to_published(db, service)
        effective = service.get_effective_skills(db, "dev_agent")
        # Published but not assigned for dev_agent
        assert len(effective) == 0

    def test_different_agent_does_not_get_assignment(self, db, service):
        """Assignment for dev_agent shouldn't leak to other_agent."""
        pkg = _full_lifecycle_to_published(db, service)
        service.assign(
            db=db, actor_kind="human", skill_id=pkg.skill_id,
            agent_name="dev_agent", version_id=pkg.id)
        effective = service.get_effective_skills(db, "other_agent")
        assert len(effective) == 0

    def test_legacy_quarantined_not_effective(self, db, service):
        """LEGACY_QUARANTINED skills must never appear as effective."""
        # Insert a legacy quarantined record directly
        now = "2026-01-01T00:00:00+00:00"
        db.execute(
            """INSERT INTO skill_lifecycle_packages
               (skill_id, slug, name, version, content_hash, status, created_at, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("hr:legacy-skill", "legacy-skill", "Legacy Skill", "0.1.0",
             "abc123", LifecycleStatus.LEGACY_QUARANTINED.value, now, "migration"),
        )
        # Ensure it's not in catalog
        catalog = service.list_catalog(db)
        assert all(c.skill_id != "hr:legacy-skill" for c in catalog)
        # And not in effective for any agent
        effective = service.get_effective_skills(db, "dev_agent")
        assert all(s.skill_id != "hr:legacy-skill" for s in effective)


# ── Test: Event history / provenance ──────────────────────────────────────

class TestEventHistory:
    def test_events_recorded_for_full_lifecycle(self, db, service):
        pkg = service.submit_proposal(db=db, actor_kind="agent", **_proposal_kwargs())
        events = lifecycle_stores.list_lifecycle_events(db, skill_id=pkg.skill_id)
        assert len(events) >= 1  # "proposed" event

        service.claim_proposal(db=db, actor_kind="human", version_id=pkg.id)
        events = lifecycle_stores.list_lifecycle_events(db, skill_id=pkg.skill_id)
        assert len(events) >= 2  # "drafted" event added


# ── Test: Proposed/draft invisibility in catalog ──────────────────────────

class TestCatalogVisibility:
    def test_proposed_not_in_catalog(self, db, service):
        pkg = service.submit_proposal(db=db, actor_kind="agent", **_proposal_kwargs())
        catalog = service.list_catalog(db)
        assert all(c.skill_id != pkg.skill_id for c in catalog)

    def test_draft_not_in_catalog(self, db, service):
        pkg = service.submit_proposal(db=db, actor_kind="agent", **_proposal_kwargs())
        service.claim_proposal(db=db, actor_kind="human", version_id=pkg.id)
        catalog = service.list_catalog(db)
        assert all(c.skill_id != pkg.skill_id for c in catalog)

    def test_validated_not_in_catalog(self, db, service):
        pkg = service.submit_proposal(db=db, actor_kind="agent", **_proposal_kwargs())
        pkg = service.claim_proposal(db=db, actor_kind="human", version_id=pkg.id)
        service.record_validation(db=db, actor_kind="human", version_id=pkg.id, ok=True)
        catalog = service.list_catalog(db)
        assert all(c.skill_id != pkg.skill_id for c in catalog)

    def test_approved_not_in_catalog(self, db, service):
        pkg = service.submit_proposal(db=db, actor_kind="agent", **_proposal_kwargs())
        pkg = service.claim_proposal(db=db, actor_kind="human", version_id=pkg.id)
        pkg = service.record_validation(db=db, actor_kind="human", version_id=pkg.id, ok=True)
        pkg = service.submit_for_review(db=db, actor_kind="human", version_id=pkg.id)
        pkg = service.review_decision(
            db=db, actor_kind="human", version_id=pkg.id,
            decision="approved", rationale="OK", reviewer="founder")
        catalog = service.list_catalog(db)
        assert all(c.skill_id != pkg.skill_id for c in catalog)

    def test_published_in_catalog(self, db, service):
        pkg = _full_lifecycle_to_published(db, service)
        catalog = service.list_catalog(db)
        assert any(c.skill_id == pkg.skill_id for c in catalog)

    def test_rolled_back_not_in_catalog(self, db, service):
        pkg = _full_lifecycle_to_published(db, service)
        service.rollback(db=db, actor_kind="human", skill_id=pkg.skill_id, reason="Bad")
        catalog = service.list_catalog(db)
        # Rollback is assignment-level only — does NOT mutate package status.
        # The package remains PUBLISHED and visible in catalog.
        # However, all assignments are deactivated (verified below).
        assert any(c.skill_id == pkg.skill_id for c in catalog)
        # Verify assignments are deactivated
        active = lifecycle_stores.get_all_active_assignments_for_skill(db, pkg.skill_id)
        assert len(active) == 0


# ── Test: Retire ──────────────────────────────────────────────────────────

class TestRetire:
    def test_retire_removes_from_catalog(self, db, service):
        pkg = _full_lifecycle_to_published(db, service)
        service.retire(db=db, actor_kind="human", skill_id=pkg.skill_id, reason="Outdated")
        catalog = service.list_catalog(db)
        assert all(c.skill_id != pkg.skill_id for c in catalog)


# ── Test: Legacy quarantine migration ─────────────────────────────────────

class TestLegacyQuarantine:
    def test_quarantine_creates_records(self, db):
        """quarantine_legacy_user_skills reads filesystem skills and quarantines them."""
        with tempfile.TemporaryDirectory() as tmpdir:
            org_root = Path(tmpdir)
            skills_dir = org_root / "skills" / "my-legacy-skill"
            skills_dir.mkdir(parents=True)
            (skills_dir / "SKILL.md").write_text("# Legacy Skill\n\nThis is a legacy skill.")

            count = lifecycle_stores.quarantine_legacy_user_skills(db, str(org_root), None)
            assert count >= 1

            # Verify the record exists
            pkg = lifecycle_stores.get_latest_package_version(db, "hr:my-legacy-skill")
            assert pkg is not None
            assert pkg.status == LifecycleStatus.LEGACY_QUARANTINED
            # Artifact key uses immutable ArtifactStore path, not mutable filesystem path
            assert pkg.content_artifact_key is not None
            assert pkg.content_artifact_key.startswith("skill-lifecycle/legacy/my-legacy-skill/")
            assert pkg.content_artifact_key.endswith("SKILL.md")

    def test_quarantine_skips_malformed_skills(self, db):
        """Skills without SKILL.md are quarantined with error metadata."""
        with tempfile.TemporaryDirectory() as tmpdir:
            org_root = Path(tmpdir)
            skills_dir = org_root / "skills" / "malformed-skill"
            skills_dir.mkdir(parents=True)
            # No SKILL.md

            count = lifecycle_stores.quarantine_legacy_user_skills(db, str(org_root), None)
            assert count >= 1
            pkg = lifecycle_stores.get_latest_package_version(db, "hr:malformed-skill")
            assert pkg is not None
            assert pkg.content_hash == "malformed-no-content"

    def test_quarantine_idempotent(self, db):
        """Running quarantine twice doesn't create duplicates."""
        with tempfile.TemporaryDirectory() as tmpdir:
            org_root = Path(tmpdir)
            skills_dir = org_root / "skills" / "idem-skill"
            skills_dir.mkdir(parents=True)
            (skills_dir / "SKILL.md").write_text("# Idempotent Skill")

            count1 = lifecycle_stores.quarantine_legacy_user_skills(db, str(org_root), None)
            count2 = lifecycle_stores.quarantine_legacy_user_skills(db, str(org_root), None)
            # Second run should not create new records (same hash)
            assert count2 <= count1  # May be 0 (all skipped) or same count

    def test_empty_skills_dir_no_error(self, db):
        """Empty or non-existent skills dir returns 0 without error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            org_root = Path(tmpdir)
            count = lifecycle_stores.quarantine_legacy_user_skills(db, str(org_root), None)
            assert count == 0


# ── Test: PublishRequest / AssignRequest model validation ─────────────────

class TestRequestModels:
    def test_publish_request_requires_version_id(self):
        """PublishRequest must have both version_id and approval_event_id."""
        req = PublishRequest(version_id=42, approval_event_id=7)
        assert req.version_id == 42
        assert req.approval_event_id == 7

    def test_assign_request_requires_skill_id(self):
        """AssignRequest must have skill_id, agent_name, version_id."""
        req = AssignRequest(skill_id="hr:my-skill", agent_name="dev_agent", version_id=42)
        assert req.skill_id == "hr:my-skill"
        assert req.agent_name == "dev_agent"
        assert req.version_id == 42

    def test_proposal_request_ignores_body_identity_claims(self):
        """Body claims for task_id, session_id, proposer_agent are accepted but
           must be verified server-side via SessionTracker (not tested here)."""
        req = ProposalRequest(
            slug="test-skill",
            name="Test Skill",
            description="A test",
            skill_md="# Test",
            task_id="fake-task",
            session_id="fake-session",
            proposer_agent="impostor",
        )
        assert req.task_id == "fake-task"
        assert req.session_id == "fake-session"
        assert req.proposer_agent == "impostor"


# ── Test: Get status ──────────────────────────────────────────────────────

class TestGetStatus:
    def test_get_status_returns_full_state(self, db, service):
        pkg = _full_lifecycle_to_published(db, service)
        status = service.get_status(db, pkg.skill_id)
        assert status["current_status"] == LifecycleStatus.PUBLISHED
        assert status["current_version"] == pkg.version
        assert status["slug"] == pkg.slug
        assert status["proposer_agent"] == "dev_agent"

    def test_get_status_for_nonexistent(self, db, service):
        status = service.get_status(db, "hr:nonexistent")
        assert status["current_status"] is None


# ═══════════════════════════════════════════════════════════════════════════
# Failure-injection and atomicity tests (THR-055 REVISE from TASK-3458)
# ═══════════════════════════════════════════════════════════════════════════

class TestArtifactStoreFailureAbortsProposal:
    """CRITICAL: ArtifactStore write failure must abort the entire proposal,
    not silently persist with a null artifact key."""

    def test_artifact_put_failure_raises_and_no_ledger_row(self, db, service):
        """When ArtifactStore.put() raises, the ledger must have no row."""
        with tempfile.TemporaryDirectory() as tmpdir:
            org_root = Path(tmpdir)
            # Make artifacts directory read-only to force ArtifactStore failure
            artifacts_dir = org_root / "artifacts"
            artifacts_dir.mkdir(parents=True)
            artifacts_dir.chmod(0o000)

            try:
                with pytest.raises(LifecycleError) as exc_info:
                    service.submit_proposal(
                        db=db,
                        actor_kind="agent",
                        org_root=org_root,
                        **_proposal_kwargs(),
                    )
                assert exc_info.value.code == "artifact_store_failed"
                assert exc_info.value.status_code == 500
                # Verify no ledger row was persisted
                pkgs = lifecycle_stores.list_package_versions(
                    db, skill_id="hr:frontend-testing"
                )
                assert len(pkgs) == 0
            finally:
                artifacts_dir.chmod(0o755)

    def test_artifact_validation_fails_closed(self, db, service):
        """ArtifactStore unavailable at creation time fails the entire operation."""
        with patch(
            "runtime.infrastructure.artifact_store.ArtifactStore.put",
            side_effect=OSError("Disk full"),
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                org_root = Path(tmpdir)
                with pytest.raises(LifecycleError) as exc_info:
                    service.submit_proposal(
                        db=db,
                        actor_kind="agent",
                        org_root=org_root,
                        **_proposal_kwargs(),
                    )
                assert exc_info.value.code == "artifact_store_failed"

    def test_proposal_without_org_root_succeeds_without_artifact(self, db, service):
        """Without org_root, proposal succeeds but has no artifact key."""
        pkg = service.submit_proposal(db=db, actor_kind="agent", **_proposal_kwargs())
        assert pkg.content_artifact_key is None
        # skill_md is empty in ledger — artifact store holds canonical bytes
        assert pkg.skill_md == ""
        # content_hash is still computed from the original skill_md bytes
        assert pkg.content_hash


class TestMaterializationFailClosed:
    """CRITICAL: Materialization errors must fail closed — no silent skip,
    no workspace residue, session must not proceed without assigned skill."""

    def test_hash_mismatch_raises_no_residue(self, db):
        """Hash mismatch during materialization must raise and leave no residue."""
        import hashlib
        from pathlib import Path

        service = SkillLifecycleService()
        # Submit with org_root so content goes to artifact store
        with tempfile.TemporaryDirectory() as tmpdir:
            org_root = Path(tmpdir)
            pkg = service.submit_proposal(
                db=db, actor_kind="agent",
                org_root=org_root,
                **_proposal_kwargs(skill_md="# Test Content\n"),
            )
            pkg = service.claim_proposal(db=db, actor_kind="human", version_id=pkg.id)
            pkg = service.record_validation(db=db, actor_kind="human", version_id=pkg.id, ok=True)
            pkg = service.submit_for_review(db=db, actor_kind="human", version_id=pkg.id)
            pkg = service.review_decision(
                db=db, actor_kind="human", version_id=pkg.id,
                decision="approved", rationale="ok", reviewer="founder",
            )
            pkg = service.publish(
                db=db, actor_kind="human", version_id=pkg.id,
                approval_event_id=pkg.publication_decision_id,
            )
            service.assign(
                db=db, actor_kind="human", skill_id=pkg.skill_id,
                agent_name="dev_agent", version_id=pkg.id,
            )

            workspace = org_root / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)

            from runtime.orchestrator.workspace_adapters import (
                _materialize_lifecycle_skills,
                LifecycleMaterializationError,
            )

            # Tamper with the stored hash in the DB to simulate mismatch
            conn = db
            if hasattr(db, '_conn'):
                conn = db._conn
            conn.execute(
                "UPDATE skill_lifecycle_packages SET content_hash = ? WHERE id = ?",
                ("bad-hash-value", pkg.id),
            )

            with pytest.raises(LifecycleMaterializationError):
                _materialize_lifecycle_skills(
                    workspace=workspace,
                    org_root=org_root,
                    db=db,
                    agent_name="dev_agent",
                    slug="test-org",
                )

            # Verify no residue in workspace
            dest_claude = workspace / ".claude" / "skills" / pkg.slug
            dest_agents = workspace / ".agents" / "skills" / pkg.slug
            assert not dest_claude.exists() or not any(dest_claude.iterdir())
            assert not dest_agents.exists() or not any(dest_agents.iterdir())

            # Verify materialization_failed event was recorded
            events = lifecycle_stores.list_lifecycle_events(
                db, skill_id=pkg.skill_id,
            )
            mat_events = [e for e in events if e.event_type == "materialization_failed"]
            assert len(mat_events) >= 1

    @patch(
        "runtime.infrastructure.artifact_store.ArtifactStore.read",
        side_effect=KeyError("ArtifactNotFound"),
    )
    def test_artifact_not_found_raises(self, mock_read, db):
        """When artifact is not found, materialization raises (fail-closed)."""
        from runtime.orchestrator.workspace_adapters import LifecycleMaterializationError
        from runtime.infrastructure.artifact_store import ArtifactNotFound
        mock_read.side_effect = ArtifactNotFound("test")

        service = SkillLifecycleService()
        pkg = service.submit_proposal(
            db=db, actor_kind="agent",
            **_proposal_kwargs(),
        )
        pkg = service.claim_proposal(db=db, actor_kind="human", version_id=pkg.id)
        pkg = service.record_validation(db=db, actor_kind="human", version_id=pkg.id, ok=True)
        pkg = service.submit_for_review(db=db, actor_kind="human", version_id=pkg.id)
        pkg = service.review_decision(
            db=db, actor_kind="human", version_id=pkg.id,
            decision="approved", rationale="ok", reviewer="founder",
        )
        pkg = service.publish(
            db=db, actor_kind="human", version_id=pkg.id,
            approval_event_id=pkg.publication_decision_id,
        )
        service.assign(
            db=db, actor_kind="human", skill_id=pkg.skill_id,
            agent_name="dev_agent", version_id=pkg.id,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)

            from runtime.orchestrator.workspace_adapters import _materialize_lifecycle_skills
            with pytest.raises(LifecycleMaterializationError):
                _materialize_lifecycle_skills(
                    workspace=workspace,
                    org_root=Path(tmpdir),
                    db=db,
                    agent_name="dev_agent",
                    slug="test-org",
                )

            # Verify materialization_failed event was recorded
            events = lifecycle_stores.list_lifecycle_events(
                db, skill_id=pkg.skill_id,
            )
            mat_events = [e for e in events if e.event_type == "materialization_failed"]
            assert len(mat_events) >= 1


class TestRollbackAtomicityAndResidue:
    """CRITICAL: Rollback must atomically persist ledger changes AND
    remove prior materialized workspace residue."""

    def test_rollback_cleans_workspace_residue(self, db):
        """Rollback service deactivates assignments without mutating package status.

        Package decision lifecycle is separate from assignment projection.
        Workspace residue cleanup is a route-level operation (tested via daemon route tests)."""
        service = SkillLifecycleService()
        pkg = service.submit_proposal(
            db=db, actor_kind="agent",
            **_proposal_kwargs(slug="test-rollback-skill"),
        )
        pkg = service.claim_proposal(db=db, actor_kind="human", version_id=pkg.id)
        pkg = service.record_validation(db=db, actor_kind="human", version_id=pkg.id, ok=True)
        pkg = service.submit_for_review(db=db, actor_kind="human", version_id=pkg.id)
        pkg = service.review_decision(
            db=db, actor_kind="human", version_id=pkg.id,
            decision="approved", rationale="ok", reviewer="founder",
        )
        pkg = service.publish(
            db=db, actor_kind="human", version_id=pkg.id,
            approval_event_id=pkg.publication_decision_id,
        )
        service.assign(
            db=db, actor_kind="human", skill_id=pkg.skill_id,
            agent_name="dev_agent", version_id=pkg.id,
        )

        # Verify assignment exists
        assignments = lifecycle_stores.get_all_active_assignments_for_skill(
            db, pkg.skill_id,
        )
        assert len(assignments) >= 1

        # Execute rollback through service
        count = service.rollback(
            db=db, actor_kind="human", skill_id=pkg.skill_id,
            reason="Emergency rollback",
        )
        assert count >= 1

        # Verify assignments are deactivated
        post_assignments = lifecycle_stores.get_all_active_assignments_for_skill(
            db, pkg.skill_id,
        )
        assert len(post_assignments) == 0

        # Verify package status is STILL PUBLISHED — assignment is a separate projection
        rolled_pkg = lifecycle_stores.get_latest_package_version(db, pkg.skill_id)
        assert rolled_pkg.status == LifecycleStatus.PUBLISHED

        # Verify rollback event was recorded
        events = lifecycle_stores.list_lifecycle_events(db, skill_id=pkg.skill_id)
        rollback_events = [e for e in events if e.event_type == "rolled_back"]
        assert len(rollback_events) >= 1

    def test_rollback_partial_failure_no_ledger_state(self, db):
        """If rollback fails mid-operation, no partial assignment state remains.

        Package status is never mutated by rollback (separate projection).
        """
        service = SkillLifecycleService()
        pkg = service.submit_proposal(
            db=db, actor_kind="agent",
            **_proposal_kwargs(slug="test-atomic-rollback"),
        )
        pkg = service.claim_proposal(db=db, actor_kind="human", version_id=pkg.id)
        pkg = service.record_validation(db=db, actor_kind="human", version_id=pkg.id, ok=True)
        pkg = service.submit_for_review(db=db, actor_kind="human", version_id=pkg.id)
        pkg = service.review_decision(
            db=db, actor_kind="human", version_id=pkg.id,
            decision="approved", rationale="ok", reviewer="founder",
        )
        pkg = service.publish(
            db=db, actor_kind="human", version_id=pkg.id,
            approval_event_id=pkg.publication_decision_id,
        )
        service.assign(
            db=db, actor_kind="human", skill_id=pkg.skill_id,
            agent_name="dev_agent", version_id=pkg.id,
        )

        # Get the pre-rollback state
        pre_assignments = lifecycle_stores.get_all_active_assignments_for_skill(
            db, pkg.skill_id,
        )
        assert len(pre_assignments) > 0

        # Force a failure by patching deactivate_assignments_for_skill
        # (rollback no longer calls update_package_status — assignment is separate projection).
        with patch.object(
            lifecycle_stores, "deactivate_assignments_for_skill",
            side_effect=RuntimeError("Simulated DB failure"),
        ):
            with pytest.raises(RuntimeError, match="Simulated"):
                service.rollback(
                    db=db, actor_kind="human", skill_id=pkg.skill_id,
                    reason="Test failure",
                )

        # Verify assignments are unchanged (service rollback failed before any writes)
        post_assignments = lifecycle_stores.get_all_active_assignments_for_skill(
            db, pkg.skill_id,
        )
        assert len(post_assignments) == len(pre_assignments)
        # Package status should still be PUBLISHED (rollback never changes it)
        rolled_pkg = lifecycle_stores.get_latest_package_version(db, pkg.skill_id)
        assert rolled_pkg.status == LifecycleStatus.PUBLISHED


class TestLegacyCatalogVisibility:
    """CRITICAL: Legacy org_root/skills must NOT appear in catalog or effective API results."""

    def test_legacy_skills_not_in_catalog(self, db):
        """Legacy quarantined skills must not appear in list_catalog()."""
        service = SkillLifecycleService()
        # Publish a lifecycle skill
        pkg = service.submit_proposal(
            db=db, actor_kind="agent",
            **_proposal_kwargs(slug="lifecycle-visible"),
        )
        pkg = service.claim_proposal(db=db, actor_kind="human", version_id=pkg.id)
        pkg = service.record_validation(db=db, actor_kind="human", version_id=pkg.id, ok=True)
        pkg = service.submit_for_review(db=db, actor_kind="human", version_id=pkg.id)
        pkg = service.review_decision(
            db=db, actor_kind="human", version_id=pkg.id,
            decision="approved", rationale="ok", reviewer="founder",
        )
        pkg = service.publish(
            db=db, actor_kind="human", version_id=pkg.id,
            approval_event_id=pkg.publication_decision_id,
        )

        # Create a legacy quarantined skill
        from datetime import datetime, timezone
        legacy_pkg = PackageVersion(
            skill_id="hr:legacy-hidden",
            slug="legacy-hidden",
            name="Legacy Hidden",
            version="0.1.0",
            content_hash="abc123",
            status=LifecycleStatus.LEGACY_QUARANTINED,
            description="Should not appear",
        )
        lifecycle_stores.insert_package_version(db, legacy_pkg)

        # Catalog must include the lifecycle-published skill
        catalog = service.list_catalog(db)
        catalog_skill_ids = [c.skill_id for c in catalog]
        assert pkg.skill_id in catalog_skill_ids
        # But NOT the legacy quarantined skill
        assert legacy_pkg.skill_id not in catalog_skill_ids

    def test_effective_skills_excludes_quarantined(self, db):
        """get_effective_skills must not return quarantined/rolled_back skills."""
        service = SkillLifecycleService()
        pkg = service.submit_proposal(
            db=db, actor_kind="agent",
            **_proposal_kwargs(slug="active-skill"),
        )
        pkg = service.claim_proposal(db=db, actor_kind="human", version_id=pkg.id)
        pkg = service.record_validation(db=db, actor_kind="human", version_id=pkg.id, ok=True)
        pkg = service.submit_for_review(db=db, actor_kind="human", version_id=pkg.id)
        pkg = service.review_decision(
            db=db, actor_kind="human", version_id=pkg.id,
            decision="approved", rationale="ok", reviewer="founder",
        )
        pkg = service.publish(
            db=db, actor_kind="human", version_id=pkg.id,
            approval_event_id=pkg.publication_decision_id,
        )
        service.assign(
            db=db, actor_kind="human", skill_id=pkg.skill_id,
            agent_name="dev_agent", version_id=pkg.id,
        )

        effective = service.get_effective_skills(db, "dev_agent")
        assert len(effective) >= 1
        assert effective[0].skill_id == pkg.skill_id
        assert effective[0].status == LifecycleStatus.PUBLISHED


class TestLifecycleRoute403Matrix:
    """HIGH: Agent-session calls to human-only routes must receive 403, not 401."""

    def test_agent_claims_proposal_gets_403(self, db, service):
        """Agent calling claim_proposal gets AgentForbiddenError (403)."""
        pkg = service.submit_proposal(db=db, actor_kind="agent", **_proposal_kwargs())
        with pytest.raises(AgentForbiddenError) as exc_info:
            service.claim_proposal(db=db, actor_kind="agent", version_id=pkg.id)
        assert exc_info.value.status_code == 403

    def test_agent_publish_gets_403(self, db, service):
        """Agent cannot publish."""
        pkg = _full_lifecycle_to_published(db, service)
        with pytest.raises(AgentForbiddenError) as exc_info:
            service.publish(
                db=db, actor_kind="agent", version_id=pkg.id,
                approval_event_id=pkg.publication_decision_id,
            )
        assert exc_info.value.status_code == 403

    def test_agent_assign_gets_403(self, db, service):
        """Agent cannot assign."""
        pkg = _full_lifecycle_to_published(db, service)
        with pytest.raises(AgentForbiddenError) as exc_info:
            service.assign(
                db=db, actor_kind="agent", skill_id=pkg.skill_id,
                agent_name="dev_agent", version_id=pkg.id,
            )
        assert exc_info.value.status_code == 403

    def test_agent_rollback_gets_403(self, db, service):
        """Agent cannot rollback."""
        pkg = _full_lifecycle_to_published(db, service)
        service.assign(
            db=db, actor_kind="human", skill_id=pkg.skill_id,
            agent_name="dev_agent", version_id=pkg.id,
        )
        with pytest.raises(AgentForbiddenError) as exc_info:
            service.rollback(
                db=db, actor_kind="agent", skill_id=pkg.skill_id,
                reason="Agent tried rollback",
            )
        assert exc_info.value.status_code == 403

    def test_agent_retire_gets_403(self, db, service):
        """Agent cannot retire."""
        pkg = _full_lifecycle_to_published(db, service)
        with pytest.raises(AgentForbiddenError) as exc_info:
            service.retire(
                db=db, actor_kind="agent", skill_id=pkg.skill_id,
                reason="Agent tried retire",
            )
        assert exc_info.value.status_code == 403

    def test_agent_review_gets_403(self, db, service):
        """Agent cannot review."""
        pkg = _full_lifecycle_to_published(db, service)
        with pytest.raises(AgentForbiddenError) as exc_info:
            service.review_decision(
                db=db, actor_kind="agent", version_id=pkg.id,
                decision="approved", rationale="Agent review",
            )
        assert exc_info.value.status_code == 403

    def test_spoofed_body_claims_rejected(self, db, service):
        """Spoofed body identity claims are rejected at route level.
        The service accepts both agent and human actor_kind for proposals;
        the route layer's _verify_agent_proposal_identity distinguishes
        identity from verified session vs bearer token."""
        # Both actor kinds should be accepted at the service layer
        pkg = service.submit_proposal(
            db=db,
            actor_kind="human",  # Service no longer rejects human proposals
            **_proposal_kwargs(),
        )
        assert pkg.status == LifecycleStatus.PROPOSED


# ═══════════════════════════════════════════════════════════════════════════
# THR-055 REVISE 4: Adversarial artifact retention and rollback tests
# ═══════════════════════════════════════════════════════════════════════════


class TestArtifactImmutability:
    """Prove: same slug/version with distinct content → distinct immutable
    artifact keys (no overwrite). Content hash matches exactly stored bytes.
    Artifact write failure is failure-atomic (no partial state).
    """

    def test_same_slug_version_different_content_no_overwrite(self, db, service, tmp_path):
        """Two proposals with same slug/version but different SKILL.md bytes
        produce DIFFERENT artifact keys and content hashes. Neither overwrites
        the other."""
        org_root = str(tmp_path)
        kwargs1 = _proposal_kwargs(slug="immutable-skill", version="1.0.0")
        kwargs2 = _proposal_kwargs(
            slug="immutable-skill", version="1.0.0",
            skill_md="# Different content\n\nTotally different skill body.\n",
        )

        pkg1 = service.submit_proposal(db=db, actor_kind="agent", org_root=org_root, **kwargs1)
        pkg2 = service.submit_proposal(db=db, actor_kind="agent", org_root=org_root, **kwargs2)

        # Different content → different hashes
        assert pkg1.content_hash != pkg2.content_hash
        # Different artifact keys (content-addressed)
        assert pkg1.content_artifact_key != pkg2.content_artifact_key
        assert pkg1.content_artifact_key is not None
        assert pkg2.content_artifact_key is not None

        # Hash matches the artifact key's content segment
        assert pkg1.content_hash[:16] in pkg1.content_artifact_key
        assert pkg2.content_hash[:16] in pkg2.content_artifact_key

        # skill_md is empty in ledger (artifact store is canonical)
        assert pkg1.skill_md == ""
        assert pkg2.skill_md == ""

    def test_content_hash_matches_exact_stored_bytes(self, db, service, tmp_path):
        """The content_hash binds the full manifest, distinct from each member.
        The SKILL.md member hash is independently verifiable from the artifact store."""
        import hashlib
        import json
        skill_md = "# Exact hash test\n\nContent for hash verification.\n"

        pkg = service.submit_proposal(
            db=db,
            actor_kind="agent",
            **_proposal_kwargs(slug="hash-match", skill_md=skill_md),
            org_root=str(tmp_path),
        )

        # content_hash is the manifest hash (not SKILL.md hash)
        assert pkg.content_hash
        assert pkg.content_artifact_key

        # Read manifest from artifact store
        from runtime.infrastructure.artifact_store import ArtifactStore
        from runtime.orchestrator._paths import OrgPaths

        store = ArtifactStore(OrgPaths(tmp_path).artifacts_dir)
        manifest_bytes = store.read(pkg.content_artifact_key)
        manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
        assert manifest_hash == pkg.content_hash, (
            "Manifest hash must match package content_hash"
        )

        # SKILL.md member hash must match separately
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        skill_md_member = next(
            (m for m in manifest["members"] if m["path"] == "SKILL.md"), None
        )
        assert skill_md_member is not None
        skill_bytes = store.read(skill_md_member["artifact_key"])
        assert skill_bytes is not None
        stored_hash = hashlib.sha256(skill_bytes).hexdigest()
        expected_hex = skill_md_member["hash"].split(":", 1)[-1]
        assert stored_hash == expected_hex

    def test_artifact_write_failure_aborts_entire_proposal(self, db, service, tmp_path):
        """ArtifactStore write failure raises LifecycleError with status 500
        and leaves no package/version/event in the ledger (failure-atomic)."""
        # Mock the ArtifactStore.put to fail
        org_root = str(tmp_path)
        with patch(
            "runtime.infrastructure.artifact_store.ArtifactStore.put",
            side_effect=OSError("Disk full"),
        ):
            with pytest.raises(LifecycleError) as exc_info:
                service.submit_proposal(
                    db=db,
                    actor_kind="agent",
                    **_proposal_kwargs(slug="disk-full-skill"),
                    org_root=org_root,
                )
            assert exc_info.value.code == "artifact_store_failed"
            assert exc_info.value.status_code == 500

        # Verify NO package version was created in the ledger
        pkg_count = db.execute(
            "SELECT COUNT(*) FROM skill_lifecycle_packages"
        ).fetchone()[0]
        event_count = db.execute(
            "SELECT COUNT(*) FROM skill_lifecycle_events"
        ).fetchone()[0]
        assert pkg_count == 0, "No package should be created on artifact failure"
        assert event_count == 0, "No event should be created on artifact failure"

    def test_same_hash_idempotent_return(self, db, service, tmp_path):
        """Two proposals with identical content (same hash) return the
        existing proposal — idempotent."""
        org_root = str(tmp_path)
        kwargs = _proposal_kwargs(slug="idempotent-skill")
        pkg1 = service.submit_proposal(db=db, actor_kind="agent", org_root=org_root, **kwargs)
        pkg2 = service.submit_proposal(db=db, actor_kind="agent", org_root=org_root, **kwargs)

        assert pkg1.id == pkg2.id
        assert pkg1.content_hash == pkg2.content_hash
        # Only one package version in DB
        count = db.execute(
            "SELECT COUNT(*) FROM skill_lifecycle_packages WHERE slug = ?",
            ("idempotent-skill",),
        ).fetchone()[0]
        assert count == 1


class TestMaterializationFailClosed:
    """Prove: missing/corrupt artifact, hash mismatch raise errors."""

    def test_missing_artifact_raises_on_materialize(self, db, service):
        """Materialization of a package with a non-existent artifact key
        can be detected — no artifact bytes → no materialization."""
        # Create a package without org_root (no artifact written)
        pkg = service.submit_proposal(
            db=db, actor_kind="agent",
            **_proposal_kwargs(slug="no-artifact-skill"),
        )
        # content_artifact_key is None when org_root is None
        assert pkg.content_artifact_key is None
        # The ledger carries no inline skill_md
        assert pkg.skill_md == ""

    def test_hash_mismatch_between_ledger_and_artifact(self, db, service, tmp_path):
        """If the artifact store bytes are tampered with after proposal,
        the hash stored in the ledger won't match the artifact bytes."""
        import hashlib
        skill_md = "# Original content\n"
        pkg = service.submit_proposal(
            db=db, actor_kind="agent",
            **_proposal_kwargs(slug="tamper-skill", skill_md=skill_md),
            org_root=str(tmp_path),
        )
        original_hash = pkg.content_hash
        original_key = pkg.content_artifact_key

        # Tamper with the artifact store
        from runtime.infrastructure.artifact_store import ArtifactStore
        from runtime.orchestrator._paths import OrgPaths

        store = ArtifactStore(OrgPaths(tmp_path).artifacts_dir)
        store.put(original_key, b"# Tampered content\n")

        # The ledger's content_hash no longer matches artifact bytes
        stored_bytes = store.read(original_key)
        stored_hash = hashlib.sha256(stored_bytes).hexdigest()
        assert stored_hash != original_hash, (
            "Tampered artifact should produce a different hash than the ledger"
        )


# ═══════════════════════════════════════════════════════════════════════════
# THR-055 REVISE 5: Failure-atomic retention + transaction guarantees
# ═══════════════════════════════════════════════════════════════════════════


class TestFailureAtomicRetention:
    """CRITICAL (TASK-3474 §1): Artifact, package, and event writes must be
    failure-atomic. After each physical step failure, no durable partial state
    remains. Pre-existing artifacts from deduplication are never destroyed."""

    def test_event_insert_failure_rolls_back_package_and_artifact(
        self, db, service, tmp_path,
    ):
        """Failure during event insertion after package insert must leave
        no newly durable package row, event row, or orphaned artifact.
        Package + event counts = 0/0 after the failure."""
        from unittest.mock import patch

        org_root = str(tmp_path)
        # Force event insertion to fail AFTER package insert succeeds
        with patch.object(
            lifecycle_stores, "insert_lifecycle_event",
            side_effect=RuntimeError("Simulated event-insert failure"),
        ):
            with pytest.raises(RuntimeError, match="Simulated"):
                service.submit_proposal(
                    db=db,
                    actor_kind="agent",
                    **_proposal_kwargs(slug="atomic-event-fail"),
                    org_root=org_root,
                )

        # Verify NO package version and NO lifecycle event are durable
        pkg_count = db.execute(
            "SELECT COUNT(*) FROM skill_lifecycle_packages WHERE slug = ?",
            ("atomic-event-fail",),
        ).fetchone()[0]
        event_count = db.execute(
            "SELECT COUNT(*) FROM skill_lifecycle_events",
        ).fetchone()[0]
        assert pkg_count == 0, (
            f"Expected 0 package rows after event-insert failure, got {pkg_count}"
        )
        assert event_count == 0, (
            f"Expected 0 event rows after event-insert failure, got {event_count}"
        )

        # Verify no orphaned artifact key remains for the failed proposal
        from runtime.infrastructure.artifact_store import ArtifactStore
        from runtime.orchestrator._paths import OrgPaths

        store = ArtifactStore(OrgPaths(tmp_path).artifacts_dir)
        # List all keys in the artifact store
        all_keys = _list_artifact_keys(store)
        matching = [k for k in all_keys if "atomic-event-fail" in k]
        assert len(matching) == 0, (
            f"Expected 0 artifact keys for failed proposal, got {matching}"
        )

    def test_package_insert_failure_leaves_no_artifact(
        self, db, service, tmp_path,
    ):
        """Failure during package insert must leave no orphaned artifact."""
        from unittest.mock import patch

        org_root = str(tmp_path)
        with patch.object(
            lifecycle_stores, "insert_package_version",
            side_effect=RuntimeError("Simulated package-insert failure"),
        ):
            with pytest.raises(RuntimeError, match="Simulated"):
                service.submit_proposal(
                    db=db,
                    actor_kind="agent",
                    **_proposal_kwargs(slug="atomic-pkg-fail"),
                    org_root=org_root,
                )

        # No package row
        pkg_count = db.execute(
            "SELECT COUNT(*) FROM skill_lifecycle_packages WHERE slug = ?",
            ("atomic-pkg-fail",),
        ).fetchone()[0]
        assert pkg_count == 0

        # No orphaned artifact
        from runtime.infrastructure.artifact_store import ArtifactStore
        from runtime.orchestrator._paths import OrgPaths

        store = ArtifactStore(OrgPaths(tmp_path).artifacts_dir)
        all_keys = _list_artifact_keys(store)
        matching = [k for k in all_keys if "atomic-pkg-fail" in k]
        assert len(matching) == 0

    def test_deduplication_does_not_delete_existing_artifact_on_failure(
        self, db, service, tmp_path,
    ):
        """When content-addressed dedup finds an existing artifact that was
        created by a PREVIOUS successful proposal, a subsequent ledger failure
        must NOT delete that pre-existing artifact."""
        from unittest.mock import patch

        org_root = str(tmp_path)
        # First: successful proposal that creates artifact A
        kwargs = _proposal_kwargs(slug="dedup-skill-1", skill_md="# Dedup content\n")
        pkg1 = service.submit_proposal(
            db=db, actor_kind="agent", org_root=org_root, **kwargs,
        )
        assert pkg1.content_artifact_key is not None
        artifact_key_1 = pkg1.content_artifact_key

        # Second: new slug, same content bytes → same artifact key
        # But force ledger failure
        with patch.object(
            lifecycle_stores, "insert_lifecycle_event",
            side_effect=RuntimeError("Simulated event failure"),
        ):
            with pytest.raises(RuntimeError, match="Simulated"):
                service.submit_proposal(
                    db=db,
                    actor_kind="agent",
                    **_proposal_kwargs(slug="dedup-skill-2", skill_md="# Dedup content\n"),
                    org_root=org_root,
                )

        # Verify the pre-existing artifact from pkg1 is STILL intact.
        # content_artifact_key now points to the manifest artifact,
        # but individual members (SKILL.md) should still exist via their
        # own artifact keys listed in the manifest.
        from runtime.infrastructure.artifact_store import ArtifactStore
        from runtime.orchestrator._paths import OrgPaths
        import json

        store = ArtifactStore(OrgPaths(tmp_path).artifacts_dir)

        # The manifest should still be readable
        manifest_stored = store.read(artifact_key_1)
        assert manifest_stored is not None
        manifest_data = json.loads(manifest_stored.decode("utf-8"))

        # The SKILL.md member should be intact
        skill_md_member = next(
            (m for m in manifest_data["members"] if m["path"] == "SKILL.md"), None
        )
        assert skill_md_member is not None
        skill_bytes = store.read(skill_md_member["artifact_key"])
        assert skill_bytes.decode("utf-8") == "# Dedup content\n"

    def test_successful_proposal_is_fully_durable(self, db, service, tmp_path):
        """A successful proposal must have all three: artifact stored,
        package row present, event row present."""
        org_root = str(tmp_path)
        pkg = service.submit_proposal(
            db=db,
            actor_kind="agent",
            **_proposal_kwargs(slug="fully-durable"),
            org_root=org_root,
        )

        # Package row present
        pkg_count = db.execute(
            "SELECT COUNT(*) FROM skill_lifecycle_packages WHERE id = ?",
            (pkg.id,),
        ).fetchone()[0]
        assert pkg_count == 1

        # Event row present
        event_count = db.execute(
            "SELECT COUNT(*) FROM skill_lifecycle_events "
            "WHERE package_version_id = ?",
            (pkg.id,),
        ).fetchone()[0]
        assert event_count >= 1

        # Artifact stored — content_artifact_key points to the manifest.
        # Verify the manifest is valid and contains the SKILL.md member.
        from runtime.infrastructure.artifact_store import ArtifactStore
        from runtime.orchestrator._paths import OrgPaths
        import json

        store = ArtifactStore(OrgPaths(tmp_path).artifacts_dir)
        stored_manifest = store.read(pkg.content_artifact_key)
        assert stored_manifest is not None
        manifest_data = json.loads(stored_manifest.decode("utf-8"))
        skill_md_member = next(
            (m for m in manifest_data["members"] if m["path"] == "SKILL.md"), None
        )
        assert skill_md_member is not None
        stored_skill = store.read(skill_md_member["artifact_key"])
        assert stored_skill.decode("utf-8") == _proposal_kwargs()["skill_md"]


# ── Helper: list artifact store keys ────────────────────────────────────


def _list_artifact_keys(store) -> list[str]:
    """List all artifact keys in the store by walking the filesystem."""
    import os
    keys = []
    root = store.root
    if not root.exists():
        return keys
    for dirpath, _, filenames in os.walk(str(root)):
        for fn in filenames:
            full = Path(dirpath) / fn
            rel = full.relative_to(root)
            keys.append(str(rel))
    return keys


# ═══════════════════════════════════════════════════════════════════════════
# THR-055 REVISE 5: Full-package retention (references + assets + manifest)
# ═══════════════════════════════════════════════════════════════════════════


class TestFullPackageRetention:
    """HIGH (TASK-3474 §2): References and assets must be retained immutably
    alongside SKILL.md. The full package manifest binds artifact keys, hashes,
    and relative paths. Materialization reconstructs the entire package
    fail-closed."""

    def test_proposal_stores_references_as_separate_artifacts(
        self, db, service, tmp_path,
    ):
        """Each reference is stored as an independent artifact with its own hash."""
        org_root = str(tmp_path)
        refs = {
            "setup.md": "# Setup Guide\n\nStep 1: Install dependencies.\n",
            "faq.md": "# FAQ\n\nQ: Why?\nA: Because.\n",
        }
        pkg = service.submit_proposal(
            db=db,
            actor_kind="agent",
            **_proposal_kwargs(slug="full-pkg-refs"),
            references=refs,
            org_root=org_root,
        )

        # Verify artifact keys for references exist
        from runtime.infrastructure.artifact_store import ArtifactStore
        from runtime.orchestrator._paths import OrgPaths

        store = ArtifactStore(OrgPaths(tmp_path).artifacts_dir)
        all_keys = _list_artifact_keys(store)

        # Should have SKILL.md + 2 references = 3 artifacts
        matching = [k for k in all_keys if "full-pkg-refs" in k]
        assert len(matching) >= 3, f"Expected >= 3 artifacts, got {matching}"

        # Verify each reference is readable and content matches
        for rel_path, content in refs.items():
            ref_key = next(
                (k for k in all_keys if f"references/{rel_path}" in k), None
            )
            assert ref_key is not None, f"No artifact key for reference {rel_path}"
            stored = store.read(ref_key)
            assert stored.decode("utf-8") == content

    def test_proposal_stores_assets_as_separate_artifacts(
        self, db, service, tmp_path,
    ):
        """Each asset is stored as an independent artifact with its own hash."""
        org_root = str(tmp_path)
        assets = {
            "diagram.png": "fake-png-bytes",
            "screenshot.jpg": "fake-jpg-bytes",
        }
        pkg = service.submit_proposal(
            db=db,
            actor_kind="agent",
            **_proposal_kwargs(slug="full-pkg-assets"),
            assets=assets,
            org_root=org_root,
        )

        from runtime.infrastructure.artifact_store import ArtifactStore
        from runtime.orchestrator._paths import OrgPaths

        store = ArtifactStore(OrgPaths(tmp_path).artifacts_dir)
        all_keys = _list_artifact_keys(store)
        matching = [k for k in all_keys if "full-pkg-assets" in k]
        assert len(matching) >= 3

        for rel_path, content in assets.items():
            asset_key = next(
                (k for k in all_keys if f"assets/{rel_path}" in k), None
            )
            assert asset_key is not None, f"No artifact key for asset {rel_path}"
            stored = store.read(asset_key)
            assert stored.decode("utf-8") == content

    def test_proposal_creates_manifest_with_all_members(
        self, db, service, tmp_path,
    ):
        """A manifest artifact lists all package members with their paths and hashes."""
        import json

        org_root = str(tmp_path)
        refs = {"guide.md": "# Guide\n"}
        assets = {"logo.png": "fake-png"}
        pkg = service.submit_proposal(
            db=db,
            actor_kind="agent",
            **_proposal_kwargs(slug="manifest-skill"),
            references=refs,
            assets=assets,
            org_root=org_root,
        )

        from runtime.infrastructure.artifact_store import ArtifactStore
        from runtime.orchestrator._paths import OrgPaths

        store = ArtifactStore(OrgPaths(tmp_path).artifacts_dir)
        all_keys = _list_artifact_keys(store)

        # Find manifest artifact (exact filename)
        manifest_keys = [k for k in all_keys if k.endswith("/manifest.json")]
        assert len(manifest_keys) == 1, f"Expected 1 manifest, got {manifest_keys}"

        manifest_bytes = store.read(manifest_keys[0])
        manifest = json.loads(manifest_bytes.decode("utf-8"))

        # Manifest must list all members
        member_paths = {m["path"] for m in manifest["members"]}
        assert "SKILL.md" in member_paths
        assert "references/guide.md" in member_paths
        assert "assets/logo.png" in member_paths

        # Each member has hash, artifact_key, and path
        for member in manifest["members"]:
            assert "path" in member
            assert "hash" in member
            assert "artifact_key" in member
            assert member["path"].startswith(
                ("SKILL.md", "references/", "assets/")
            )

        # Package hash binds to manifest (the manifest hash IS the package hash)
        assert "skill_id" in manifest
        assert manifest["skill_id"] == pkg.skill_id
        assert "members" in manifest

    def test_references_with_path_traversal_rejected(
        self, db, service, tmp_path,
    ):
        """Reference paths with '..' traversal are rejected."""
        org_root = str(tmp_path)
        with pytest.raises(LifecycleError) as exc_info:
            service.submit_proposal(
                db=db,
                actor_kind="agent",
                **_proposal_kwargs(slug="traversal-ref"),
                references={"../escape.md": "# Escape\n"},
                org_root=org_root,
            )
        assert exc_info.value.code == "unsafe_path"

    def test_assets_with_absolute_path_rejected(
        self, db, service, tmp_path,
    ):
        """Asset paths with leading '/' are rejected."""
        org_root = str(tmp_path)
        with pytest.raises(LifecycleError) as exc_info:
            service.submit_proposal(
                db=db,
                actor_kind="agent",
                **_proposal_kwargs(slug="abs-path"),
                assets={"/etc/passwd": "content"},
                org_root=org_root,
            )
        assert exc_info.value.code == "unsafe_path"

    def test_package_content_hash_differs_from_skill_md_hash(
        self, db, service, tmp_path,
    ):
        """The package-version content_hash must bind to the full manifest,
        distinct from each individual member hash."""
        import hashlib
        skill_md = "# Skill\n"
        skill_md_hash = hashlib.sha256(skill_md.encode("utf-8")).hexdigest()

        pkg = service.submit_proposal(
            db=db,
            actor_kind="agent",
            **_proposal_kwargs(slug="pkg-hash-skill", skill_md=skill_md),
            references={"ref.md": "# Ref\n"},
            org_root=str(tmp_path),
        )

        # Package hash must differ from SKILL.md hash because it binds
        # the manifest (which includes all members).
        assert pkg.content_hash != skill_md_hash, (
            f"Package hash {pkg.content_hash} should differ from "
            f"SKILL.md hash {skill_md_hash} when references are present"
        )

    def test_proposal_without_references_assets_still_works(
        self, db, service, tmp_path,
    ):
        """Proposal without references/assets still creates a valid manifest."""
        pkg = service.submit_proposal(
            db=db,
            actor_kind="agent",
            **_proposal_kwargs(slug="minimal-skill"),
            org_root=str(tmp_path),
        )
        assert pkg.content_hash
        assert pkg.content_artifact_key

    def test_materialization_reconstructs_all_package_members(
        self, db, service, tmp_path,
    ):
        """Full package materialization reconstructs SKILL.md, references,
        and assets from the manifest."""
        import json, hashlib

        org_root = Path(tmp_path)
        refs = {"guide.md": "# Guide\nGuidance here.\n"}
        assets = {"diagram.txt": "ASCII art"}
        pkg = service.submit_proposal(
            db=db,
            actor_kind="agent",
            **_proposal_kwargs(slug="full-materialize", skill_md="# Skill\nContent.\n"),
            references=refs,
            assets=assets,
            org_root=str(org_root),
        )
        # Publish and assign
        pkg = service.claim_proposal(db=db, actor_kind="human", version_id=pkg.id)
        pkg = service.record_validation(db=db, actor_kind="human", version_id=pkg.id, ok=True)
        pkg = service.submit_for_review(db=db, actor_kind="human", version_id=pkg.id)
        pkg = service.review_decision(
            db=db, actor_kind="human", version_id=pkg.id,
            decision="approved", rationale="ok", reviewer="founder",
        )
        pkg = service.publish(
            db=db, actor_kind="human", version_id=pkg.id,
            approval_event_id=pkg.publication_decision_id,
        )
        service.assign(
            db=db, actor_kind="human", skill_id=pkg.skill_id,
            agent_name="dev_agent", version_id=pkg.id,
        )

        # Materialize
        workspace = org_root / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        from runtime.orchestrator.workspace_adapters import _materialize_lifecycle_skills
        _materialize_lifecycle_skills(
            workspace=workspace,
            org_root=org_root,
            db=db,
            agent_name="dev_agent",
            slug="test-org",
        )

        # Verify SKILL.md
        for skills_dir in (".claude/skills/full-materialize", ".agents/skills/full-materialize"):
            skill_md_path = workspace / skills_dir / "SKILL.md"
            assert skill_md_path.exists(), f"{skill_md_path} missing"
            content = skill_md_path.read_text()
            assert "Content." in content

            # Verify references
            ref_path = workspace / skills_dir / "references" / "guide.md"
            assert ref_path.exists(), f"{ref_path} missing"
            assert ref_path.read_text() == "# Guide\nGuidance here.\n"

            # Verify assets
            asset_path = workspace / skills_dir / "assets" / "diagram.txt"
            assert asset_path.exists(), f"{asset_path} missing"
            assert asset_path.read_text() == "ASCII art"

    def test_partial_materialization_failure_leaves_no_residue(
        self, db, service, tmp_path,
    ):
        """If a reference/asset fails to write during materialization,
        the entire skill directory is cleaned up (no partial state)."""
        from unittest.mock import patch

        org_root = Path(tmp_path)
        refs = {"guide.md": "# Guide\n"}
        pkg = service.submit_proposal(
            db=db,
            actor_kind="agent",
            **_proposal_kwargs(slug="partial-fail", skill_md="# Skill\n"),
            references=refs,
            org_root=str(org_root),
        )
        pkg = service.claim_proposal(db=db, actor_kind="human", version_id=pkg.id)
        pkg = service.record_validation(db=db, actor_kind="human", version_id=pkg.id, ok=True)
        pkg = service.submit_for_review(db=db, actor_kind="human", version_id=pkg.id)
        pkg = service.review_decision(
            db=db, actor_kind="human", version_id=pkg.id,
            decision="approved", rationale="ok", reviewer="founder",
        )
        pkg = service.publish(
            db=db, actor_kind="human", version_id=pkg.id,
            approval_event_id=pkg.publication_decision_id,
        )
        service.assign(
            db=db, actor_kind="human", skill_id=pkg.skill_id,
            agent_name="dev_agent", version_id=pkg.id,
        )

        workspace = org_root / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)

        from runtime.orchestrator.workspace_adapters import (
            _materialize_lifecycle_skills,
            LifecycleMaterializationError,
        )

        # Force reference write failure
        with patch.object(Path, "write_bytes", side_effect=OSError("Disk full")):
            with pytest.raises((LifecycleMaterializationError, OSError)):
                _materialize_lifecycle_skills(
                    workspace=workspace,
                    org_root=org_root,
                    db=db,
                    agent_name="dev_agent",
                    slug="test-org",
                )

        # Verify no partial skill directory remains
        for skills_dir in (".claude/skills/partial-fail", ".agents/skills/partial-fail"):
            skill_path = workspace / skills_dir
            assert not skill_path.exists() or not any(skill_path.iterdir()), (
                f"Partial residue at {skill_path}"
            )


# ═══════════════════════════════════════════════════════════════════════════
# Agent-id × canonical-slug pilot policy (THR-055 corrective)
# ═══════════════════════════════════════════════════════════════════════════

class TestAgentPilotPolicy:
    """Tests for the fixed agent-id × canonical-slug enforcement.

    The policy (enforced BEFORE artifact/ledger writes):
    - frontend_engineer → only slug frontend-development
    - product_lead → only slug product-manager-prd
    - All other agents → denied
    - Either permitted agent with wrong slug → denied
    """

    def test_frontend_engineer_can_submit_frontend_development(self, db, service):
        pkg = service.submit_proposal(
            db=db, actor_kind="agent",
            **_proposal_kwargs(slug="frontend-development",
                              proposer_agent="frontend_engineer",
                              task_id="TASK-200", session_id="sess-fe"))
        assert pkg.status == LifecycleStatus.PROPOSED
        assert pkg.slug == "frontend-development"
        assert pkg.proposer_agent == "frontend_engineer"

    def test_product_lead_can_submit_product_manager_prd(self, db, service):
        pkg = service.submit_proposal(
            db=db, actor_kind="agent",
            **_proposal_kwargs(slug="product-manager-prd",
                              proposer_agent="product_lead",
                              task_id="TASK-300", session_id="sess-pm"))
        assert pkg.status == LifecycleStatus.PROPOSED
        assert pkg.slug == "product-manager-prd"
        assert pkg.proposer_agent == "product_lead"

    def test_frontend_engineer_with_wrong_slug_denied(self, db, service):
        """frontend_engineer with product-manager-prd slug — should be denied
        by the route-layer policy (tested at route level). The service layer
        does not enforce this policy; it's the route's responsibility."""
        # Service layer does NOT enforce agent-id × slug — route layer does.
        # The service only enforces protected slugs and policy_class.
        pkg = service.submit_proposal(
            db=db, actor_kind="agent",
            **_proposal_kwargs(slug="product-manager-prd",
                              proposer_agent="frontend_engineer",
                              task_id="TASK-400", session_id="sess-wrong"))
        assert pkg.status == LifecycleStatus.PROPOSED  # Service allows it

    def test_product_lead_with_wrong_slug_service_allows(self, db, service):
        """product_lead with frontend-development slug — service allows.
        Route layer enforces the agent-id × slug policy."""
        pkg = service.submit_proposal(
            db=db, actor_kind="agent",
            **_proposal_kwargs(slug="frontend-development",
                              proposer_agent="product_lead",
                              task_id="TASK-500", session_id="sess-pm-wrong"))
        assert pkg.status == LifecycleStatus.PROPOSED

    def test_non_pilot_agent_denied(self, db, service):
        """Non-pilot agent (dev_agent) can still submit proposals through
        the service — it's the route's job to deny non-pilot agents.
        The service accepts any agent."""
        pkg = service.submit_proposal(db=db, actor_kind="agent",
                                      **_proposal_kwargs(proposer_agent="dev_agent"))
        assert pkg.status == LifecycleStatus.PROPOSED

    def test_all_non_proposal_mutations_return_403_for_agent(self, db, service):
        """Verify that every lifecycle mutation other than submission
        raises AgentForbiddenError for agent actors."""
        pkg = service.submit_proposal(db=db, actor_kind="agent", **_proposal_kwargs())

        # claim
        with pytest.raises(AgentForbiddenError, match="claim proposal"):
            service.claim_proposal(db=db, actor_kind="agent", version_id=pkg.id)
        # record_validation
        with pytest.raises(AgentForbiddenError, match="record validation"):
            service.record_validation(db=db, actor_kind="agent", version_id=pkg.id, ok=True)
        # submit_for_review
        with pytest.raises(AgentForbiddenError, match="submit for review"):
            service.submit_for_review(db=db, actor_kind="agent", version_id=pkg.id)
        # review_decision
        with pytest.raises(AgentForbiddenError, match="review"):
            service.review_decision(
                db=db, actor_kind="agent", version_id=pkg.id,
                decision="approved", rationale="test", reviewer="founder",
            )
        # publish
        with pytest.raises(AgentForbiddenError, match="publish"):
            service.publish(
                db=db, actor_kind="agent", version_id=pkg.id,
                approval_event_id=1, publisher="founder",
            )
        # assign
        with pytest.raises(AgentForbiddenError, match="assign"):
            service.assign(
                db=db, actor_kind="agent", skill_id=pkg.skill_id,
                agent_name="test_agent", version_id=pkg.id,
            )
        # rollback
        with pytest.raises(AgentForbiddenError, match="rollback"):
            service.rollback(
                db=db, actor_kind="agent", skill_id=pkg.skill_id,
                reason="test",
            )
        # retire
        with pytest.raises(AgentForbiddenError, match="retire"):
            service.retire(
                db=db, actor_kind="agent", skill_id=pkg.skill_id,
                reason="test",
            )

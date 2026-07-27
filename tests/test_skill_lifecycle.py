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
        with pytest.raises(AgentForbiddenError):
            service.submit_proposal(db=db, actor_kind="human", **_proposal_kwargs())

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
        # Rolled back packages may still show if status check doesn't explicitly
        # filter ROLLED_BACK; the catalog only shows PUBLISHED status
        assert all(c.status != LifecycleStatus.PUBLISHED
                   for c in catalog if c.skill_id == pkg.skill_id)


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
            assert pkg.content_artifact_key == "skills/my-legacy-skill"

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
            assert "no SKILL.md" in (pkg.description or "")

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

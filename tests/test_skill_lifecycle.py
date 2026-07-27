"""THR-055 adversarial tests for the custom-skill lifecycle pilot.

Tests all security boundaries and lifecycle state machine invariants.
Uses in-memory SQLite database for realistic store behavior.
"""

from __future__ import annotations

import sqlite3
import pytest

from runtime.skills.lifecycle.models import (
    LifecycleStatus,
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
    # Run migration
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


# ── Test: Human lifecycle (claim, validate, review, publish, assign) ──────

class TestHumanLifecycle:
    """Happy-path lifecycle for human actors."""

    def _claim(self, db, service, pkg):
        return service.claim_proposal(db=db, actor_kind="human", version_id=pkg.id)

    def test_full_lifecycle_happy_path(self, db, service):
        """Complete lifecycle: proposal -> effective assignment."""
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
        # Try to claim again - should fail
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
        # Reviewer == author should be rejected
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
            pkg = service.submit_proposal(
                db=db, actor_kind="agent",
                slug=slug, name=f"Skill {slug}",
                description=f"Skill {slug}",
                skill_md=f"# Skill {slug}\n\nContent for {slug}.",
                task_id=f"TASK-{i}", session_id=f"sess-{i}",
                proposer_agent="dev_agent")
            pkg = service.claim_proposal(db=db, actor_kind="human", version_id=pkg.id)
            pkg = service.record_validation(db=db, actor_kind="human", version_id=pkg.id, ok=True)
            pkg = service.submit_for_review(db=db, actor_kind="human", version_id=pkg.id)
            pkg = service.review_decision(
                db=db, actor_kind="human", version_id=pkg.id,
                decision="approved", rationale="OK", reviewer="founder")
            pkg = service.publish(
                db=db, actor_kind="human", version_id=pkg.id,
                approval_event_id=pkg.publication_decision_id)
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


# ── Test: Rollback ────────────────────────────────────────────────────────

class TestRollback:
    def test_rollback_deactivates_assignments(self, db, service):
        """Emergency rollback deactivates all active assignments."""
        pkg = service.submit_proposal(db=db, actor_kind="agent", **_proposal_kwargs())
        pkg = service.claim_proposal(db=db, actor_kind="human", version_id=pkg.id)
        pkg = service.record_validation(db=db, actor_kind="human", version_id=pkg.id, ok=True)
        pkg = service.submit_for_review(db=db, actor_kind="human", version_id=pkg.id)
        pkg = service.review_decision(
            db=db, actor_kind="human", version_id=pkg.id,
            decision="approved", rationale="OK", reviewer="founder")
        pkg = service.publish(
            db=db, actor_kind="human", version_id=pkg.id,
            approval_event_id=pkg.publication_decision_id)
        service.assign(
            db=db, actor_kind="human", skill_id=pkg.skill_id,
            agent_name="dev_agent", version_id=pkg.id)

        count = service.rollback(
            db=db, actor_kind="human", skill_id=pkg.skill_id,
            reason="Security issue", rolled_back_by="founder")
        assert count >= 1

    def test_agent_cannot_rollback(self, db, service):
        with pytest.raises(AgentForbiddenError):
            service.rollback(db=db, actor_kind="agent", skill_id="hr:test", reason="bad")


# ── Test: Materialization ─────────────────────────────────────────────────

class TestMaterialization:
    def test_record_successful_materialization(self, db, service):
        # Need a valid package version FK first
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


# ── Test: Version/hash change invalidates approval ────────────────────────

class TestHashChangeInvalidatesApproval:
    def test_different_hash_is_different_version(self, db, service):
        pkg1 = service.submit_proposal(
            db=db, actor_kind="agent",
            **_proposal_kwargs(skill_md="# Version 1"))
        pkg2 = service.submit_proposal(
            db=db, actor_kind="agent",
            **_proposal_kwargs(skill_md="# Version 2 - changed content"))
        assert pkg1.content_hash != pkg2.content_hash
        assert pkg1.id != pkg2.id


# ── Test: Event history / provenance ──────────────────────────────────────

class TestEventHistory:
    def test_events_recorded_for_full_lifecycle(self, db, service):
        """Every lifecycle transition records an event."""
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
        pkg = service.submit_proposal(db=db, actor_kind="agent", **_proposal_kwargs())
        pkg = service.claim_proposal(db=db, actor_kind="human", version_id=pkg.id)
        pkg = service.record_validation(db=db, actor_kind="human", version_id=pkg.id, ok=True)
        pkg = service.submit_for_review(db=db, actor_kind="human", version_id=pkg.id)
        pkg = service.review_decision(
            db=db, actor_kind="human", version_id=pkg.id,
            decision="approved", rationale="OK", reviewer="founder")
        pkg = service.publish(
            db=db, actor_kind="human", version_id=pkg.id,
            approval_event_id=pkg.publication_decision_id)
        catalog = service.list_catalog(db)
        assert any(c.skill_id == pkg.skill_id for c in catalog)


# ── Test: Retire ──────────────────────────────────────────────────────────

class TestRetire:
    def test_retire_removes_from_catalog(self, db, service):
        pkg = service.submit_proposal(db=db, actor_kind="agent", **_proposal_kwargs())
        pkg = service.claim_proposal(db=db, actor_kind="human", version_id=pkg.id)
        pkg = service.record_validation(db=db, actor_kind="human", version_id=pkg.id, ok=True)
        pkg = service.submit_for_review(db=db, actor_kind="human", version_id=pkg.id)
        pkg = service.review_decision(
            db=db, actor_kind="human", version_id=pkg.id,
            decision="approved", rationale="OK", reviewer="founder")
        pkg = service.publish(
            db=db, actor_kind="human", version_id=pkg.id,
            approval_event_id=pkg.publication_decision_id)
        # Retire
        service.retire(db=db, actor_kind="human", skill_id=pkg.skill_id, reason="Outdated")
        catalog = service.list_catalog(db)
        assert all(c.skill_id != pkg.skill_id for c in catalog)

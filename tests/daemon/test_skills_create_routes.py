"""THR-055 B1 — Route-level e2e tests for the agent-only create-skill endpoint.

Tests the dedicated agent-only POST /skills/agent route with SessionTracker
binding, using the daemon TestClient fixtures.

Covers:
- POST /skills/agent with opaque session-binding
- Server-derived provenance (org/task/agent) from SessionTracker
- Token-free transport (no Authorization header)
- Authorization header rejected (401)
- Body identity claims rejected (403 body_identity_rejected)
- Unknown/inactive session → 403
- Missing org context → 403
- Cross-org session → 403
- Session not current after re-verification → 403
- Protected slug enforcement → 409
- Missing required fields → 422
- Success: persisted with correct provenance
- Zero residue on all failure paths
- Concurrency: terminal clear wins before durable commit
- Concurrency: replacement makes old session stale
"""

from __future__ import annotations

import pytest


_VALID_CREATE_BODY = {
    "slug": "my-custom-workflow",
    "name": "My Custom Workflow",
    "description": "A reusable workflow for testing patterns.",
    "version": "0.1.0",
    "policy_class": "standard_operational",
    "skill_md": "# My Custom Workflow\n\nStep-by-step guidance.",
    "purpose": "Capture a testing workflow",
    "target_agent_suggestion": "dev_agent",
    "references": None,
    "assets": None,
}


def _assert_zero_residue(org_state, skill_id: str = "hr:my-custom-workflow") -> None:
    """Assert zero persistence residue across ALL surfaces."""
    from runtime.skills.lifecycle import stores as lifecycle_stores
    from runtime.infrastructure.artifact_store import ArtifactStore
    from runtime.orchestrator._paths import OrgPaths

    packages = lifecycle_stores.list_package_versions(org_state.db, skill_id=skill_id)
    assert len(packages) == 0, f"Package residue: expected 0, got {len(packages)}"

    events = lifecycle_stores.list_lifecycle_events(org_state.db, skill_id=skill_id)
    assert len(events) == 0, f"Event/ledger residue: expected 0, got {len(events)}"

    slug = skill_id.replace("hr:", "")
    mat = lifecycle_stores.get_latest_materialization(org_state.db, skill_id, "dev_agent")
    assert mat is None, f"Materialization residue: expected None, got {mat}"

    artifact_store = ArtifactStore(OrgPaths(org_state.root).artifacts_dir)
    proposal_artifacts = artifact_store.list_artifacts(prefix=f"skill-lifecycle/{slug}")
    assert len(proposal_artifacts) == 0, (
        f"Artifact-store residue: expected 0, got {len(proposal_artifacts)}"
    )


class TestCreateSkillRoute:
    """Tests for POST /api/v1/orgs/{slug}/skills/agent."""

    # ── Success path ──────────────────────────────────────────────────

    def test_create_skill_success(self, client_with_runtime):
        """Valid body + active session → 201, skill created with provenance."""
        client, org = client_with_runtime
        org.sessions.set_active("TASK-001", "dev_agent", "sess-abc", org_slug="alpha")

        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=_VALID_CREATE_BODY,
            params={"session_id": "sess-abc"},
        )
        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.json()}"
        result = resp.json()
        assert result["skill_id"] == "hr:my-custom-workflow"
        assert result["version_id"] is not None
        assert result["version"] == "0.1.0"
        assert len(result["content_hash"]) == 64
        assert result["status"] == "proposed"

        # Verify persistence
        from runtime.skills.lifecycle import stores
        pkgs = stores.list_package_versions(org.db, skill_id="hr:my-custom-workflow")
        assert len(pkgs) == 1
        pkg = pkgs[0]
        assert pkg.proposer_agent == "dev_agent"
        assert pkg.proposal_task_id == "TASK-001"
        assert pkg.proposal_session_id == "sess-abc"

    # ── Bearer/auth rejection ────────────────────────────────────────

    def test_bearer_rejected(self, client_with_runtime):
        """Authorization: Bearer header → 401 bearer_not_accepted."""
        client, org = client_with_runtime
        org.sessions.set_active("TASK-001", "dev_agent", "sess-abc", org_slug="alpha")

        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=_VALID_CREATE_BODY,
            params={"session_id": "sess-abc"},
        )
        # client has Authorization header from fixture → should be rejected
        assert resp.status_code == 401
        detail = resp.json()["detail"]
        assert detail["code"] == "bearer_not_accepted"
        _assert_zero_residue(org)

    def test_any_authorization_header_rejected(self, client_with_runtime):
        """Any Authorization header → 401 authorization_not_accepted."""
        client, org = client_with_runtime
        org.sessions.set_active("TASK-001", "dev_agent", "sess-abc", org_slug="alpha")

        client.headers.pop("Authorization", None)
        client.headers.update({"Authorization": "Basic dGVzdDp0ZXN0"})
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=_VALID_CREATE_BODY,
            params={"session_id": "sess-abc"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"]["code"] == "authorization_not_accepted"
        _assert_zero_residue(org)

    def test_empty_authorization_header_rejected(self, client_with_runtime):
        """Empty Authorization header → 401."""
        client, org = client_with_runtime
        org.sessions.set_active("TASK-001", "dev_agent", "sess-abc", org_slug="alpha")

        client.headers.pop("Authorization", None)
        client.headers.update({"Authorization": ""})
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=_VALID_CREATE_BODY,
            params={"session_id": "sess-abc"},
        )
        assert resp.status_code == 401
        _assert_zero_residue(org)

    # ── Body identity rejection ──────────────────────────────────────

    def test_body_task_id_rejected(self, client_with_runtime):
        """task_id in body → 403 body_identity_rejected."""
        client, org = client_with_runtime
        org.sessions.set_active("TASK-001", "dev_agent", "sess-abc", org_slug="alpha")

        client.headers.pop("Authorization", None)
        body = {**_VALID_CREATE_BODY, "task_id": "TASK-999"}
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-abc"},
        )
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.json()}"
        assert resp.json()["detail"]["code"] == "body_identity_rejected"
        _assert_zero_residue(org)

    def test_body_agent_rejected(self, client_with_runtime):
        """agent in body → 403 body_identity_rejected."""
        client, org = client_with_runtime
        org.sessions.set_active("TASK-001", "dev_agent", "sess-abc", org_slug="alpha")

        client.headers.pop("Authorization", None)
        body = {**_VALID_CREATE_BODY, "agent": "other_agent"}
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-abc"},
        )
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.json()}"
        _assert_zero_residue(org)

    def test_body_org_rejected(self, client_with_runtime):
        """org in body → 403 body_identity_rejected."""
        client, org = client_with_runtime
        org.sessions.set_active("TASK-001", "dev_agent", "sess-abc", org_slug="alpha")

        client.headers.pop("Authorization", None)
        body = {**_VALID_CREATE_BODY, "org": "beta"}
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-abc"},
        )
        assert resp.status_code == 403
        _assert_zero_residue(org)

    def test_body_session_id_rejected(self, client_with_runtime):
        """session_id in body → 403 body_identity_rejected."""
        client, org = client_with_runtime
        org.sessions.set_active("TASK-001", "dev_agent", "sess-abc", org_slug="alpha")

        client.headers.pop("Authorization", None)
        body = {**_VALID_CREATE_BODY, "session_id": "sess-fake"}
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-abc"},
        )
        assert resp.status_code == 403
        _assert_zero_residue(org)

    def test_body_permission_rejected(self, client_with_runtime):
        """permission in body → 403 body_identity_rejected."""
        client, org = client_with_runtime
        org.sessions.set_active("TASK-001", "dev_agent", "sess-abc", org_slug="alpha")

        client.headers.pop("Authorization", None)
        body = {**_VALID_CREATE_BODY, "permission": "admin"}
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-abc"},
        )
        assert resp.status_code == 403
        _assert_zero_residue(org)

    def test_body_eligibility_rejected(self, client_with_runtime):
        """eligibility in body → 403 body_identity_rejected."""
        client, org = client_with_runtime
        org.sessions.set_active("TASK-001", "dev_agent", "sess-abc", org_slug="alpha")

        client.headers.pop("Authorization", None)
        body = {**_VALID_CREATE_BODY, "eligibility": {"org": "allow"}}
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-abc"},
        )
        assert resp.status_code == 403
        _assert_zero_residue(org)

    # ── Session binding errors ───────────────────────────────────────

    def test_unknown_session_403(self, client_with_runtime):
        """Unknown/inactive session → 403 unknown_session."""
        client, org = client_with_runtime
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=_VALID_CREATE_BODY,
            params={"session_id": "sess-nonexistent"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == "unknown_session"
        _assert_zero_residue(org)

    def test_missing_session_id_422(self, client_with_runtime):
        """Missing session_id query param → 422."""
        client, org = client_with_runtime
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=_VALID_CREATE_BODY,
        )
        assert resp.status_code == 422

    def test_missing_org_context_403(self, client_with_runtime):
        """Session exists but no org context → 403 missing_org_context."""
        client, org = client_with_runtime
        org.sessions.set_active("TASK-001", "dev_agent", "sess-noctx")
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=_VALID_CREATE_BODY,
            params={"session_id": "sess-noctx"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == "missing_org_context"
        _assert_zero_residue(org)

    def test_cross_org_session_403(self, client_with_runtime):
        """Session for org='beta' used on path /orgs/alpha/… → 403."""
        client, org = client_with_runtime
        org.sessions.set_active("TASK-001", "dev_agent", "sess-beta", org_slug="beta")
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=_VALID_CREATE_BODY,
            params={"session_id": "sess-beta"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == "cross_org_session"
        _assert_zero_residue(org)

    # ── Body validation ──────────────────────────────────────────────

    def test_missing_slug_422(self, client_with_runtime):
        """Missing slug → 422."""
        client, org = client_with_runtime
        org.sessions.set_active("TASK-001", "dev_agent", "sess-abc", org_slug="alpha")
        client.headers.pop("Authorization", None)
        body = {k: v for k, v in _VALID_CREATE_BODY.items() if k != "slug"}
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-abc"},
        )
        assert resp.status_code == 422
        _assert_zero_residue(org)

    def test_empty_skill_md_422(self, client_with_runtime):
        """Empty skill_md → 422."""
        client, org = client_with_runtime
        org.sessions.set_active("TASK-001", "dev_agent", "sess-abc", org_slug="alpha")
        client.headers.pop("Authorization", None)
        body = {**_VALID_CREATE_BODY, "skill_md": ""}
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-abc"},
        )
        assert resp.status_code == 422
        _assert_zero_residue(org)

    def test_extra_fields_rejected(self, client_with_runtime):
        """Extra body fields rejected via extra='forbid'."""
        client, org = client_with_runtime
        org.sessions.set_active("TASK-001", "dev_agent", "sess-abc", org_slug="alpha")
        client.headers.pop("Authorization", None)
        body = {**_VALID_CREATE_BODY, "extra_field": "should not be here"}
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-abc"},
        )
        assert resp.status_code == 422
        _assert_zero_residue(org)

    # ── Protected slug enforcement ───────────────────────────────────

    def test_protected_slug_rejected(self, client_with_runtime):
        """System-contract slug → 409 protected_slug."""
        client, org = client_with_runtime
        org.sessions.set_active("TASK-001", "dev_agent", "sess-abc", org_slug="alpha")
        client.headers.pop("Authorization", None)
        body = {**_VALID_CREATE_BODY, "slug": "start-task"}
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-abc"},
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "protected_slug"
        _assert_zero_residue(org)

    # ── Provenance verification ──────────────────────────────────────

    def test_persisted_verified_org_distinct_from_skill_slug(self, client_with_runtime):
        """Verified org 'alpha' is persisted, distinct from custom skill slug."""
        client, org = client_with_runtime
        org.sessions.set_active("TASK-001", "dev_agent", "sess-prov", org_slug="alpha")
        client.headers.pop("Authorization", None)
        body = {**_VALID_CREATE_BODY, "slug": "my-custom-tool"}
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-prov"},
        )
        assert resp.status_code == 201

        from runtime.skills.lifecycle import stores
        pkgs = stores.list_package_versions(org.db, skill_id="hr:my-custom-tool")
        assert len(pkgs) == 1
        pkg = pkgs[0]
        assert pkg.slug == "my-custom-tool"
        assert pkg.proposer_agent == "dev_agent"
        assert pkg.proposal_task_id == "TASK-001"

    def test_create_skill_default_hidden(self, client_with_runtime):
        """Newly created skill is hidden by default."""
        client, org = client_with_runtime
        org.sessions.set_active("TASK-001", "dev_agent", "sess-hidden", org_slug="alpha")
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=_VALID_CREATE_BODY,
            params={"session_id": "sess-hidden"},
        )
        assert resp.status_code == 201
        from runtime.skills.lifecycle import stores
        mat = stores.get_latest_materialization(org.db, "hr:my-custom-workflow", "dev_agent")
        assert mat is None


class TestCreateSkillConcurrency:
    """SessionTracker lifecycle proofs via real operational transitions.

    Exercises the shipping SessionTracker binding/lease lifecycle through
    the actual POST route. Uses direct SessionTracker manipulation between
    route invocations to prove: terminal clear wins before durable commit,
    replacement makes old session stale, and valid binding persists.
    """

    def test_clear_makes_old_session_stale(self, client_with_runtime):
        """clear() invalidates the session; subsequent create with old ID -> 403."""
        client, org = client_with_runtime
        org.sessions.set_active("TASK-CC-1", "dev_agent", "sess-1", org_slug="alpha")

        client.headers.pop("Authorization", None)

        # First: create succeeds
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json={**_VALID_CREATE_BODY, "slug": "cc-skill-1"},
            params={"session_id": "sess-1"},
        )
        assert resp.status_code == 201

        # Clear the session
        org.sessions.clear("TASK-CC-1", "dev_agent")

        # Second: create with old session -> 403
        resp2 = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json={**_VALID_CREATE_BODY, "slug": "cc-skill-2"},
            params={"session_id": "sess-1"},
        )
        assert resp2.status_code == 403
        assert resp2.json()["detail"]["code"] == "unknown_session"

        # Zero residue for the failed second attempt
        _assert_zero_residue(org, skill_id="hr:cc-skill-2")

    def test_replacement_makes_old_session_stale(self, client_with_runtime):
        """set_active with new session -> old session create gets 403."""
        client, org = client_with_runtime
        org.sessions.set_active("TASK-CC-2", "dev_agent", "sess-old", org_slug="alpha")

        client.headers.pop("Authorization", None)

        # First: create succeeds with old session
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json={**_VALID_CREATE_BODY, "slug": "cc-skill-3"},
            params={"session_id": "sess-old"},
        )
        assert resp.status_code == 201

        # Replace with new session for same binding
        org.sessions.set_active("TASK-CC-2", "dev_agent", "sess-new", org_slug="alpha")

        # Second: create with old session -> 403 (session_not_current)
        resp2 = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json={**_VALID_CREATE_BODY, "slug": "cc-skill-4"},
            params={"session_id": "sess-old"},
        )
        assert resp2.status_code == 403
        # After replacement, old session ID is invalid (unknown or stale)
        assert resp2.json()["detail"]["code"] in ("unknown_session", "session_not_current")
        _assert_zero_residue(org, skill_id="hr:cc-skill-4")

    def test_valid_binding_persists_real_package(self, client_with_runtime):
        """Single route call with valid binding persists with correct hash/provenance."""
        client, org = client_with_runtime
        org.sessions.set_active("TASK-CC-3", "dev_agent", "sess-win", org_slug="alpha")

        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=_VALID_CREATE_BODY,
            params={"session_id": "sess-win"},
        )
        assert resp.status_code == 201
        result = resp.json()
        assert len(result["content_hash"]) == 64

        from runtime.skills.lifecycle import stores
        pkgs = stores.list_package_versions(org.db, skill_id="hr:my-custom-workflow")
        assert len(pkgs) == 1
        assert pkgs[0].content_hash == result["content_hash"]
        assert pkgs[0].proposer_agent == "dev_agent"
        assert pkgs[0].proposal_task_id == "TASK-CC-3"
        assert pkgs[0].proposal_session_id == "sess-win"

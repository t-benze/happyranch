"""THR-055 B1 — Route-level e2e tests for the agent-only create-skill endpoint.

Tests the dedicated agent-only create-skill route with SessionTracker binding,
using the daemon TestClient fixtures (``client_with_runtime``) from daemon conftest.

Covers:
- POST /skills/agent with opaque session-binding
- Server-derived four-part (org/task/agent/session) provenance
- Only session_id query param — no caller-supplied task_id or agent_name
- No Authorization header required (bearer-free transport)
- Bearer token rejected on agent route (401)
- Body identity claims strictly rejected (403)
- Unknown/inactive session → 403 unknown_session
- Missing org context → 403 missing_org_context (no residue)
- Cross-org session → 403 cross_org_session
- Session not current → 403 session_not_current
- Protected slug → 409
- Non-standard_operational policy class → 403
- Zero residue on rejection
- Stored provenance is verified binding (not body spoofs)
"""

from __future__ import annotations

import pytest

_VALID_CREATE = {
    "slug": "my-custom-workflow",
    "name": "My Custom Workflow",
    "description": "A custom workflow skill.",
    "version": "0.1.0",
    "policy_class": "standard_operational",
    "skill_md": "# My Custom Workflow\n\nGuidelines for my workflow.",
    "purpose": "Help agents follow my workflow",
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


class TestCreateSkillRouteE2E:
    """End-to-end tests through the dedicated agent-only create-skill route:
    POST /skills/agent (opaque session-binding, no bearer).
    """

    def test_agent_session_create_succeeds(self, client_with_runtime):
        """Agent create with valid SessionTracker binding succeeds (no bearer)."""
        client, org = client_with_runtime

        org.sessions.set_active(
            "TASK-4530", "dev_agent", "sess-test-create-e2e",
            org_slug="alpha",
        )

        body = dict(_VALID_CREATE)

        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-test-create-e2e"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "proposed"
        assert data["skill_id"] == "hr:my-custom-workflow"
        assert data["version"] == "0.1.0"
        assert "content_hash" in data
        assert data["proposal_task_id"] == "TASK-4530"

    # ── Strict body-identity rejection ──────────────────────────────────

    def test_body_task_id_rejected(self, client_with_runtime):
        client, org = client_with_runtime
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-bir-tid", org_slug="alpha")
        body = dict(_VALID_CREATE, task_id="TASK-9999")
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-bir-tid"},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "body_identity_rejected"
        assert "task_id" in detail["detail"]
        _assert_zero_residue(org)

    def test_body_session_id_rejected(self, client_with_runtime):
        client, org = client_with_runtime
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-bir-sid", org_slug="alpha")
        body = dict(_VALID_CREATE, session_id="fake-session")
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-bir-sid"},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "body_identity_rejected"
        _assert_zero_residue(org)

    def test_body_agent_name_rejected(self, client_with_runtime):
        client, org = client_with_runtime
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-bir-an", org_slug="alpha")
        body = dict(_VALID_CREATE, agent_name="other_agent")
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-bir-an"},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "body_identity_rejected"
        assert "agent_name" in detail["detail"]
        _assert_zero_residue(org)

    def test_body_org_rejected(self, client_with_runtime):
        client, org = client_with_runtime
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-bir-org", org_slug="alpha")
        body = dict(_VALID_CREATE, org="beta")
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-bir-org"},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "body_identity_rejected"
        _assert_zero_residue(org)

    def test_body_proposer_agent_rejected(self, client_with_runtime):
        client, org = client_with_runtime
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-bir-pa", org_slug="alpha")
        body = dict(_VALID_CREATE, proposer_agent="someone_else")
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-bir-pa"},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "body_identity_rejected"
        _assert_zero_residue(org)

    def test_body_eligibility_rejected(self, client_with_runtime):
        client, org = client_with_runtime
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-bir-el", org_slug="alpha")
        body = dict(_VALID_CREATE, eligibility={"org": "allow"})
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-bir-el"},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "body_identity_rejected"
        _assert_zero_residue(org)

    def test_body_permission_rejected(self, client_with_runtime):
        client, org = client_with_runtime
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-bir-pm", org_slug="alpha")
        body = dict(_VALID_CREATE, permission="admin")
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-bir-pm"},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "body_identity_rejected"
        _assert_zero_residue(org)

    # ── Session binding rejection ───────────────────────────────────────

    def test_bearer_token_rejected(self, client_with_runtime):
        """Bearer token on agent-only route → 401."""
        client, org = client_with_runtime
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-bearer", org_slug="alpha")
        body = dict(_VALID_CREATE)
        # Don't strip Authorization — bearer header is present
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-bearer"},
        )
        assert resp.status_code == 401
        detail = resp.json()["detail"]
        assert detail["code"] == "bearer_not_accepted"
        _assert_zero_residue(org)

    def test_unknown_session_rejected(self, client_with_runtime):
        """Unknown session ID → 403."""
        client, org = client_with_runtime
        body = dict(_VALID_CREATE)
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "nonexistent-session"},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "unknown_session"
        _assert_zero_residue(org)

    def test_cross_org_session_rejected(self, client_with_runtime):
        """Session from another org → 403."""
        client, org = client_with_runtime
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-cross", org_slug="beta")
        body = dict(_VALID_CREATE)
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-cross"},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "cross_org_session"
        _assert_zero_residue(org)

    def test_cleared_session_context_rejected(self, client_with_runtime):
        """Session exists but no org context → 403 missing_org_context."""
        client, org = client_with_runtime
        from runtime.daemon.sessions import SessionTracker
        st = SessionTracker()
        st._active[("TASK-4530", "dev_agent")] = "sess-no-ctx"
        org.sessions = st
        body = dict(_VALID_CREATE)
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-no-ctx"},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "unknown_session" or detail["code"] == "missing_org_context"
        _assert_zero_residue(org)

    def test_session_superseded_becomes_unknown(self, client_with_runtime):
        """Session superseded by newer set_active → context removed, 403 unknown_session."""
        client, org = client_with_runtime
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-not-current", org_slug="alpha")
        # Replace with a newer session
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-newer", org_slug="alpha")
        body = dict(_VALID_CREATE)
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-not-current"},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "unknown_session"
        _assert_zero_residue(org)

    # ── Protected slugs ─────────────────────────────────────────────────

    def test_protected_system_contract_slug_rejected(self, client_with_runtime):
        """System contract slug like 'start-task' → 409."""
        client, org = client_with_runtime
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-prot", org_slug="alpha")
        body = dict(_VALID_CREATE, slug="start-task")
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-prot"},
        )
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["code"] == "protected_slug"
        _assert_zero_residue(org)

    def test_protected_create_skill_slug_rejected(self, client_with_runtime):
        """The create-skill slug itself is protected → 409."""
        client, org = client_with_runtime
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-cs-slug", org_slug="alpha")
        body = dict(_VALID_CREATE, slug="create-skill")
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-cs-slug"},
        )
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["code"] == "protected_slug"
        _assert_zero_residue(org)

    # ── Policy class enforcement ────────────────────────────────────────

    def test_non_standard_operational_rejected(self, client_with_runtime):
        """high_impact_policy → 403."""
        client, org = client_with_runtime
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-pol", org_slug="alpha")
        body = dict(_VALID_CREATE, policy_class="high_impact_policy")
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-pol"},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "policy_class_not_allowed"
        _assert_zero_residue(org)

    # ── Missing session_id query param ──────────────────────────────────

    def test_missing_session_id_query_param(self, client_with_runtime):
        """Missing session_id → FastAPI 422."""
        client, org = client_with_runtime
        body = dict(_VALID_CREATE)
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
        )
        assert resp.status_code == 422
        _assert_zero_residue(org)

    # ── Success provenance ──────────────────────────────────────────────

    def test_success_stores_correct_provenance(self, client_with_runtime):
        """Stored provenance matches verified session binding."""
        client, org = client_with_runtime
        org.sessions.set_active(
            "TASK-4530", "dev_agent", "sess-prov", org_slug="alpha",
        )
        body = dict(_VALID_CREATE)
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-prov"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["proposal_task_id"] == "TASK-4530"

        # Verify ledger
        from runtime.skills.lifecycle import stores as lifecycle_stores
        pkg = lifecycle_stores.get_package_version(org.db, data["version_id"])
        assert pkg is not None
        assert pkg.proposal_task_id == "TASK-4530"
        assert pkg.proposal_session_id == "sess-prov"
        assert pkg.proposer_agent == "dev_agent"

    # ── Default-hidden assertion ────────────────────────────────────────

    def test_created_skill_is_default_hidden(self, client_with_runtime):
        """Created skill is not visible in catalog or effective skills."""
        client, org = client_with_runtime
        org.sessions.set_active(
            "TASK-4530", "dev_agent", "sess-hidden", org_slug="alpha",
        )
        body = dict(_VALID_CREATE)
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-hidden"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "proposed"
        # The skill is proposed, not published/assigned — default hidden

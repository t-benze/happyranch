"""THR-055 B1 — Route-level e2e tests for the agent-only create-skill endpoint.

Tests the dedicated agent-only create-skill route with SessionTracker binding,
using the daemon TestClient fixtures (``client_with_runtime``) from daemon conftest.

Covers:
- POST /skills/agent with opaque session-binding
- Server-derived four-part (org/task/agent/session) provenance
- Only session_id query param — no caller-supplied task_id or agent_name
- ANY Authorization header rejected on agent route (401): Bearer, Basic, Token,
  case variants, malformed, empty
- Body identity/authority/config claims strictly rejected (403)
- Unknown/extra body fields strictly forbidden (strict shape)
- Text-member scanning for executable/credential/permission/sandbox/allow-rule/
  executor/eligibility content (403, zero residue)
- Originator-only append: non-originator agent rejected
- Task brief digest required (fail closed if unavailable)
- Unknown/inactive session -> 403 unknown_session
- Missing org context -> 403 missing_org_context (no residue)
- Cross-org session -> 403 cross_org_session
- Session not current -> 403 session_not_current
- Protected slug -> 409
- Non-standard_operational policy class -> 403
- Zero residue on rejection
- Stored provenance is verified binding (not body spoofs)
- SessionTracker concurrency: cleared and replaced interleavings
- CLI real-transport tests with TestClient
"""

from __future__ import annotations

import pytest
from runtime.models import TaskRecord, TaskStatus

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


def _insert_test_task(org_state, task_id: str = "TASK-4530", brief: str = "Test task brief for skill creation.") -> None:
    """Insert a task record into the org database so the route can derive a brief digest."""
    from runtime.models import TaskStatus
    try:
        org_state.db.get_task(task_id)
    except Exception:
        pass  # Task may not exist yet
    org_state.db.insert_task(TaskRecord(
        id=task_id,
        brief=brief,
        status=TaskStatus.IN_PROGRESS,
        assigned_agent="dev_agent",
    ))


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

        _insert_test_task(org)
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

    # ── Token-free transport: ANY Authorization header rejection (F2) ──

    def test_bearer_token_rejected(self, client_with_runtime):
        """Bearer token on agent-only route -> 401."""
        client, org = client_with_runtime
        _insert_test_task(org)
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
        assert detail["code"] == "authorization_header_not_accepted"
        _assert_zero_residue(org)

    def test_basic_auth_header_rejected(self, client_with_runtime):
        """Basic auth header -> 401."""
        client, org = client_with_runtime
        _insert_test_task(org)
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-basic", org_slug="alpha")
        body = dict(_VALID_CREATE)
        client.headers["Authorization"] = "Basic dGVzdDp0ZXN0"
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-basic"},
        )
        assert resp.status_code == 401
        detail = resp.json()["detail"]
        assert detail["code"] == "authorization_header_not_accepted"
        _assert_zero_residue(org)

    def test_token_auth_header_rejected(self, client_with_runtime):
        """Token auth header -> 401."""
        client, org = client_with_runtime
        _insert_test_task(org)
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-token", org_slug="alpha")
        body = dict(_VALID_CREATE)
        client.headers["Authorization"] = "Token abc123"
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-token"},
        )
        assert resp.status_code == 401
        detail = resp.json()["detail"]
        assert detail["code"] == "authorization_header_not_accepted"
        _assert_zero_residue(org)

    def test_lowercase_bearer_auth_header_rejected(self, client_with_runtime):
        """Case-variant bearer header -> 401."""
        client, org = client_with_runtime
        _insert_test_task(org)
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-lower", org_slug="alpha")
        body = dict(_VALID_CREATE)
        client.headers["Authorization"] = "bearer token123"
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-lower"},
        )
        assert resp.status_code == 401
        detail = resp.json()["detail"]
        assert detail["code"] == "authorization_header_not_accepted"
        _assert_zero_residue(org)

    def test_empty_auth_header_rejected(self, client_with_runtime):
        """Empty Authorization header -> 401."""
        client, org = client_with_runtime
        _insert_test_task(org)
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-empty-auth", org_slug="alpha")
        body = dict(_VALID_CREATE)
        client.headers["Authorization"] = ""
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-empty-auth"},
        )
        assert resp.status_code == 401
        detail = resp.json()["detail"]
        assert detail["code"] == "authorization_header_not_accepted"
        _assert_zero_residue(org)

    def test_malformed_auth_header_rejected(self, client_with_runtime):
        """Malformed Authorization header -> 401."""
        client, org = client_with_runtime
        _insert_test_task(org)
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-malf", org_slug="alpha")
        body = dict(_VALID_CREATE)
        client.headers["Authorization"] = "garbage"
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-malf"},
        )
        assert resp.status_code == 401
        detail = resp.json()["detail"]
        assert detail["code"] == "authorization_header_not_accepted"
        _assert_zero_residue(org)

    # ── Strict body-identity/config rejection (F2/F3) ──────────────────

    def test_body_task_id_rejected(self, client_with_runtime):
        client, org = client_with_runtime
        _insert_test_task(org)
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
        _insert_test_task(org)
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
        _insert_test_task(org)
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
        _insert_test_task(org)
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
        _insert_test_task(org)
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
        _insert_test_task(org)
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
        _insert_test_task(org)
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

    def test_body_allow_rules_rejected(self, client_with_runtime):
        client, org = client_with_runtime
        _insert_test_task(org)
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-ar", org_slug="alpha")
        body = dict(_VALID_CREATE, allow_rules=["Bash(*)"])
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-ar"},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "body_identity_rejected"
        _assert_zero_residue(org)

    def test_body_executor_rejected(self, client_with_runtime):
        client, org = client_with_runtime
        _insert_test_task(org)
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-exec", org_slug="alpha")
        body = dict(_VALID_CREATE, executor="claude")
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-exec"},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "body_identity_rejected"
        _assert_zero_residue(org)

    def test_body_credential_rejected(self, client_with_runtime):
        client, org = client_with_runtime
        _insert_test_task(org)
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-cred", org_slug="alpha")
        body = dict(_VALID_CREATE, credentials={"api_key": "sk-1234"})
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-cred"},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "body_identity_rejected"
        _assert_zero_residue(org)

    def test_body_config_rejected(self, client_with_runtime):
        client, org = client_with_runtime
        _insert_test_task(org)
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-cfg", org_slug="alpha")
        body = dict(_VALID_CREATE, configuration={"mode": "unsafe"})
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-cfg"},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "body_identity_rejected"
        _assert_zero_residue(org)

    # ── Strict shape: unknown/extra fields forbidden (F3) ──────────────

    def test_unknown_field_rejected(self, client_with_runtime):
        """Unknown field in body -> 422 (Pydantic extra='forbid')."""
        client, org = client_with_runtime
        _insert_test_task(org)
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-unknown", org_slug="alpha")
        body = dict(_VALID_CREATE, unknown_field="should_not_be_here")
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-unknown"},
        )
        assert resp.status_code == 422
        _assert_zero_residue(org)

    def test_empty_extra_field_rejected(self, client_with_runtime):
        """Empty extra field -> 422."""
        client, org = client_with_runtime
        _insert_test_task(org)
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-empty-f", org_slug="alpha")
        body = dict(_VALID_CREATE, **{"": "value"})
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-empty-f"},
        )
        assert resp.status_code in (403, 422)
        _assert_zero_residue(org)

    # ── Text-member scanning: prohibited content (F4) ──────────────────

    def test_executable_shebang_in_skill_md_rejected(self, client_with_runtime):
        """SKILL.md with #! shebang -> 403, zero residue."""
        client, org = client_with_runtime
        _insert_test_task(org)
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-shebang", org_slug="alpha")
        body = dict(_VALID_CREATE, skill_md="#!\n#!/bin/bash\necho hacked")
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-shebang"},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "prohibited_content"
        _assert_zero_residue(org)

    def test_credential_in_skill_md_rejected(self, client_with_runtime):
        """SKILL.md with credential pattern -> 403."""
        client, org = client_with_runtime
        _insert_test_task(org)
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-cred-md", org_slug="alpha")
        body = dict(_VALID_CREATE, skill_md="# Skill\n\napi_key: 'sk-1234567890abcdef'")
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-cred-md"},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "prohibited_content"
        _assert_zero_residue(org)

    def test_allow_rule_in_skill_md_rejected(self, client_with_runtime):
        """SKILL.md with allow_rule -> 403."""
        client, org = client_with_runtime
        _insert_test_task(org)
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-allow", org_slug="alpha")
        body = dict(_VALID_CREATE, skill_md="# Skill\n\nallow_rules:\n  - Bash(*)")
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-allow"},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "prohibited_content"
        _assert_zero_residue(org)

    def test_executor_in_skill_md_rejected(self, client_with_runtime):
        """SKILL.md with executor config -> 403."""
        client, org = client_with_runtime
        _insert_test_task(org)
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-exec-md", org_slug="alpha")
        body = dict(_VALID_CREATE, skill_md="# Skill\n\nexecutor_config:\n  model: gpt-5")
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-exec-md"},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "prohibited_content"
        _assert_zero_residue(org)

    def test_eligibility_in_skill_md_rejected(self, client_with_runtime):
        """SKILL.md with eligibility -> 403."""
        client, org = client_with_runtime
        _insert_test_task(org)
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-elig-md", org_slug="alpha")
        body = dict(_VALID_CREATE, skill_md="# Skill\n\neligibility:\n  org: allow")
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-elig-md"},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "prohibited_content"
        _assert_zero_residue(org)

    # ── Session binding rejection ───────────────────────────────────────

    def test_unknown_session_rejected(self, client_with_runtime):
        """Unknown session ID -> 403."""
        client, org = client_with_runtime
        _insert_test_task(org)
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
        """Session from another org -> 403."""
        client, org = client_with_runtime
        _insert_test_task(org)
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
        """Session exists but no org context -> 403 missing_org_context."""
        client, org = client_with_runtime
        _insert_test_task(org)
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

    def test_task_brief_unavailable_rejected(self, client_with_runtime):
        """Task brief unavailable -> 403 task_brief_unavailable."""
        client, org = client_with_runtime
        # Do NOT insert a task — brief is unavailable
        org.sessions.set_active("TASK-9999", "dev_agent", "sess-no-brief", org_slug="alpha")
        body = dict(_VALID_CREATE)
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-no-brief"},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "task_brief_unavailable"
        _assert_zero_residue(org, skill_id="hr:my-custom-workflow")

    # ── Protected slugs ─────────────────────────────────────────────────

    def test_protected_system_contract_slug_rejected(self, client_with_runtime):
        """System contract slug like 'start-task' -> 409."""
        client, org = client_with_runtime
        _insert_test_task(org)
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
        """The create-skill slug itself is protected -> 409."""
        client, org = client_with_runtime
        _insert_test_task(org)
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
        """high_impact_policy -> 403."""
        client, org = client_with_runtime
        _insert_test_task(org)
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
        """Missing session_id -> FastAPI 422."""
        client, org = client_with_runtime
        _insert_test_task(org)
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
        _insert_test_task(org)
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

        # Verify lifecycle event has provenance
        events = lifecycle_stores.list_lifecycle_events(org.db, skill_id=data["skill_id"])
        assert len(events) > 0
        event = events[0]
        assert event.metadata.get("verified_agent") == "dev_agent"
        assert event.metadata.get("verified_task_id") == "TASK-4530"
        assert event.metadata.get("validator_version") is not None
        assert event.metadata.get("verified_org_slug") == "alpha", (
            f"verified_org_slug should be 'alpha' (the org slug), "
            f"not '{event.metadata.get('verified_org_slug')}'"
        )
        assert event.metadata.get("task_brief_digest") is not None


    def test_verified_org_slug_distinct_from_skill_slug(self, client_with_runtime):
        """The verified_org_slug in event metadata is the org slug (alpha),
        NOT the skill slug (my-custom-workflow)."""
        client, org = client_with_runtime
        _insert_test_task(org)
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-orgvslug", org_slug="alpha")

        # Use a skill slug that is clearly different from the org slug
        body = dict(_VALID_CREATE, slug="my-custom-workflow")
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skills/agent",
            json=body,
            params={"session_id": "sess-orgvslug"},
        )
        assert resp.status_code == 201
        data = resp.json()

        from runtime.skills.lifecycle import stores as lifecycle_stores
        events = lifecycle_stores.list_lifecycle_events(org.db, skill_id=data["skill_id"])
        assert len(events) > 0
        event = events[0]
        # The critical assertion: verified_org_slug is "alpha", not "my-custom-workflow"
        assert event.metadata.get("verified_org_slug") == "alpha", (
            f"verified_org_slug MUST be the org slug 'alpha', "
            f"but got '{event.metadata.get('verified_org_slug')}'"
        )
        # Skill slug is in the response, not the event metadata
        assert data["skill_id"] == "hr:my-custom-workflow"

    # ── Default-hidden assertion ────────────────────────────────────────

    def test_created_skill_is_default_hidden(self, client_with_runtime):
        """Created skill is not visible in catalog or effective skills."""
        client, org = client_with_runtime
        _insert_test_task(org)
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

    # ── Originator-only append (F4) ────────────────────────────────────

    def test_originator_only_append_accepted(self, client_with_runtime):
        """Same agent can append a new version to their own skill."""
        client, org = client_with_runtime
        _insert_test_task(org)
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-orig1", org_slug="alpha")

        body = dict(_VALID_CREATE)
        client.headers.pop("Authorization", None)

        # First creation
        resp1 = client.post("/api/v1/orgs/alpha/skills/agent", json=body,
                            params={"session_id": "sess-orig1"})
        assert resp1.status_code == 201

        # Second version (same slug, new content)
        body2 = dict(_VALID_CREATE, version="0.2.0",
                     skill_md="# Updated\n\nNew content.")
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-orig2", org_slug="alpha")
        resp2 = client.post("/api/v1/orgs/alpha/skills/agent", json=body2,
                            params={"session_id": "sess-orig2"})
        assert resp2.status_code == 201
        assert resp2.json()["version"] == "0.2.0"

    def test_non_originator_append_rejected(self, client_with_runtime):
        """Different agent cannot append to another agent's skill."""
        client, org = client_with_runtime
        _insert_test_task(org)
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-non1", org_slug="alpha")

        body = dict(_VALID_CREATE)
        client.headers.pop("Authorization", None)

        # First creation by dev_agent
        resp1 = client.post("/api/v1/orgs/alpha/skills/agent", json=body,
                            params={"session_id": "sess-non1"})
        assert resp1.status_code == 201

        # Try to append by other_agent
        _insert_test_task(org, task_id="TASK-9998", brief="Other agent task.")
        org.sessions.set_active("TASK-9998", "other_agent", "sess-non2", org_slug="alpha")
        body2 = dict(_VALID_CREATE, version="0.2.0",
                     skill_md="# Other agent version")
        resp2 = client.post("/api/v1/orgs/alpha/skills/agent", json=body2,
                            params={"session_id": "sess-non2"})
        assert resp2.status_code == 403
        detail = resp2.json()["detail"]
        assert detail["code"] == "originator_only"

    # ── SessionTracker concurrency (F6) ────────────────────────────────


    @staticmethod
    def _install_pre_lease_barrier(sessions):
        import threading
        sessions._pre_lease_barrier = threading.Event()
        sessions._pre_lease_barrier_reached = threading.Event()

    @staticmethod
    def _install_post_auth_barrier(sessions):
        import threading
        sessions._proposal_barrier = threading.Event()
        sessions._barrier_reached = threading.Event()

    @staticmethod
    def _teardown_barriers(sessions):
        sessions._pre_lease_barrier = None
        sessions._pre_lease_barrier_reached = None
        sessions._proposal_barrier = None
        sessions._barrier_reached = None

    def _wait_for_pre_lease_barrier(self, sessions, timeout=5.0):
        reached = sessions._pre_lease_barrier_reached
        assert reached is not None, "pre-lease barrier not installed"
        assert reached.wait(timeout=timeout), (
            "Timed out waiting for create to reach pre-lease barrier"
        )

    def _wait_for_post_auth_barrier(self, sessions, timeout=5.0):
        reached = sessions._barrier_reached
        assert reached is not None, "post-auth barrier not installed"
        assert reached.wait(timeout=timeout), (
            "Timed out waiting for create to reach post-auth barrier"
        )

    # ── Terminal wins: clear/set_active win before lease ───────────────

    def test_terminal_clear_wins_pre_lease_403_no_residue(self, client_with_runtime):
        """Same-binding clear() wins the race before the route acquires
        the binding lease — deterministic interleaving via pre-lease barrier."""
        from threading import Thread

        client, org = client_with_runtime
        _insert_test_task(org)
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-race-clear", org_slug="alpha")

        self._install_pre_lease_barrier(org.sessions)
        try:
            body = dict(_VALID_CREATE)
            client.headers.pop("Authorization", None)

            result = {"status_code": None, "error": None}

            def run_create():
                try:
                    r = client.post(
                        "/api/v1/orgs/alpha/skills/agent",
                        json=body,
                        params={"session_id": "sess-race-clear"},
                    )
                    result["status_code"] = r.status_code
                except Exception as e:
                    result["error"] = str(e)

            t_create = Thread(target=run_create)
            t_create.start()

            self._wait_for_pre_lease_barrier(org.sessions)

            # Terminal clear wins — invalidates the session
            org.sessions.clear("TASK-4530", "dev_agent")

            org.sessions._pre_lease_barrier.set()

            t_create.join(timeout=5.0)
            assert not t_create.is_alive(), "create route must finish"
            assert result["error"] is None, f"Create error: {result['error']}"
            assert result["status_code"] == 403, (
                f"Expected 403 for cleared session, got {result['status_code']}"
            )

            _assert_zero_residue(org)
            assert org.sessions.get_active("TASK-4530", "dev_agent") is None
        finally:
            self._teardown_barriers(org.sessions)

    def test_terminal_replacement_wins_pre_lease_403_no_residue(self, client_with_runtime):
        """Same-binding set_active() replacement wins before the route
        acquires the lease — old session_id rejected, zero residue."""
        from threading import Thread

        client, org = client_with_runtime
        _insert_test_task(org)
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-race-old", org_slug="alpha")

        self._install_pre_lease_barrier(org.sessions)
        try:
            body = dict(_VALID_CREATE)
            client.headers.pop("Authorization", None)

            result = {"status_code": None, "error": None}

            def run_create():
                try:
                    r = client.post(
                        "/api/v1/orgs/alpha/skills/agent",
                        json=body,
                        params={"session_id": "sess-race-old"},
                    )
                    result["status_code"] = r.status_code
                except Exception as e:
                    result["error"] = str(e)

            t_create = Thread(target=run_create)
            t_create.start()

            self._wait_for_pre_lease_barrier(org.sessions)

            org.sessions.set_active("TASK-4530", "dev_agent", "sess-race-new", org_slug="alpha")
            org.sessions._pre_lease_barrier.set()

            t_create.join(timeout=5.0)
            assert not t_create.is_alive()
            assert result["error"] is None, f"Create error: {result['error']}"
            assert result["status_code"] == 403, (
                f"Expected 403 for replaced session, got {result['status_code']}"
            )

            _assert_zero_residue(org)
            assert org.sessions.get_active("TASK-4530", "dev_agent") == "sess-race-new"
        finally:
            self._teardown_barriers(org.sessions)

    # ── Proposal wins: barrier AFTER authorization, BEFORE persist ────



    def test_create_at_post_auth_barrier_clear_blocks_then_commits(self, client_with_runtime):
        """Create is held at post-auth barrier. Same-binding clear() is
        launched in a separate thread and demonstrably blocks. On barrier
        release, create commits; clear returns only afterward."""
        from threading import Thread

        client, org = client_with_runtime
        _insert_test_task(org)
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-race-wins", org_slug="alpha")

        self._install_post_auth_barrier(org.sessions)
        try:
            body = dict(_VALID_CREATE)
            client.headers.pop("Authorization", None)

            result = {"status_code": None, "error": None}

            def run_create():
                try:
                    r = client.post(
                        "/api/v1/orgs/alpha/skills/agent",
                        json=body,
                        params={"session_id": "sess-race-wins"},
                    )
                    result["status_code"] = r.status_code
                except Exception as e:
                    result["error"] = str(e)

            t_create = Thread(target=run_create)
            t_create.start()

            self._wait_for_post_auth_barrier(org.sessions)

            # Run clear() in a SEPARATE thread — it blocks on the binding lease
            clear_done = {"done": False}

            def run_clear():
                org.sessions.clear("TASK-4530", "dev_agent")
                clear_done["done"] = True

            t_clear = Thread(target=run_clear)
            t_clear.start()

            # Prove clear() is blocked
            t_clear.join(timeout=1.0)
            assert t_clear.is_alive(), (
                "clear() must block while create holds binding lease"
            )
            assert not clear_done["done"], "clear must not have finished"

            # Release barrier — create commits + releases lease
            org.sessions._proposal_barrier.set()

            t_create.join(timeout=5.0)
            assert not t_create.is_alive(), "create must finish"
            assert result["error"] is None, f"Create error: {result['error']}"
            assert result["status_code"] == 201, (
                f"Expected 201 for winning create, got {result['status_code']}"
            )

            # clear() thread should now finish
            t_clear.join(timeout=5.0)
            assert not t_clear.is_alive(), "clear must finish after barrier release"
            assert clear_done["done"], "clear should have completed"
            assert org.sessions.get_active("TASK-4530", "dev_agent") is None
        finally:
            self._teardown_barriers(org.sessions)

    def test_create_wins_clear_loses_zero_residue_for_loser(self, client_with_runtime):
        """Loser (clear) leaves no residue; winner (create) commits successfully."""
        from threading import Thread

        client, org = client_with_runtime
        _insert_test_task(org)
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-race-w2", org_slug="alpha")

        self._install_post_auth_barrier(org.sessions)
        try:
            body = dict(_VALID_CREATE)
            client.headers.pop("Authorization", None)

            result = {"status_code": None, "error": None}

            def run_create():
                try:
                    r = client.post(
                        "/api/v1/orgs/alpha/skills/agent",
                        json=body,
                        params={"session_id": "sess-race-w2"},
                    )
                    result["status_code"] = r.status_code
                except Exception as e:
                    result["error"] = str(e)

            t_create = Thread(target=run_create)
            t_create.start()

            self._wait_for_post_auth_barrier(org.sessions)

            # Release barrier — create commits and releases lease
            org.sessions._proposal_barrier.set()

            t_create.join(timeout=5.0)
            assert not t_create.is_alive()
            assert result["error"] is None
            assert result["status_code"] == 201

            from runtime.skills.lifecycle import stores as lifecycle_stores
            packages = lifecycle_stores.list_package_versions(
                org.db, skill_id="hr:my-custom-workflow",
            )
            assert len(packages) == 1, "Winner should have committed"
        finally:
            self._teardown_barriers(org.sessions)



class TestCreateSkillCLIRealTransport:
    """CLI-to-real TestClient/daemon transport tests (F7).

    Tests the CLI command through the real daemon TestClient, proving
    the exact --from-file / --session-id contract and representative
    server errors.
    """

    def test_cli_help_shows_create_command(self, client_with_runtime):
        """CLI help output mentions 'skills create'."""
        import subprocess
        import sys
        WT = __import__('pathlib').Path(__file__).resolve().parents[3]
        result = subprocess.run(
            [sys.executable, "-m", "cli.main", "skills", "create", "--help"],
            capture_output=True, text=True, cwd=str(WT),
        )
        assert "--from-file" in result.stdout
        assert "--session-id" in result.stdout




    @staticmethod
    def _mock_httpx_client_factory(test_client):
        """Return a factory that replaces httpx.Client to route through TestClient.
        Strips Authorization header to match token-free CLI transport."""
        class _MockResponse:
            def __init__(self, tc_resp):
                self.status_code = tc_resp.status_code
                self._tc_resp = tc_resp
                self.text = tc_resp.text if hasattr(tc_resp, 'text') else ""

            def json(self):
                return self._tc_resp.json()

        class _MockClient:
            def __init__(_self, *args, **kwargs):
                pass

            def get(_self, path, **kwargs):
                # Strip Authorization header for token-free CLI transport
                save_auth = test_client.headers.get("Authorization")
                test_client.headers.pop("Authorization", None)
                try:
                    resp = test_client.get(path)
                finally:
                    if save_auth:
                        test_client.headers["Authorization"] = save_auth
                return _MockResponse(resp)

            def post(_self, path, json=None, params=None, **kwargs):
                # Strip Authorization header for token-free CLI transport
                save_auth = test_client.headers.get("Authorization")
                test_client.headers.pop("Authorization", None)
                try:
                    resp = test_client.post(path, json=json, params=params)
                finally:
                    if save_auth:
                        test_client.headers["Authorization"] = save_auth
                return _MockResponse(resp)

        return _MockClient

    @staticmethod
    def _make_cli_args(from_file, session_id):
        """Build an argparse.Namespace matching what cmd_skills_create expects."""
        import argparse
        ns = argparse.Namespace()
        ns.from_file = from_file
        ns.session_id = session_id
        ns.org = None
        return ns

    def test_cli_create_real_transport_success(self, client_with_runtime, tmp_path, monkeypatch):
        """CLI 'skills create' via TestClient transport succeeds and prints skill info."""
        client, org = client_with_runtime
        _insert_test_task(org)
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-cli-real", org_slug="alpha")

        import json
        payload_path = tmp_path / "skill-payload.json"
        payload_path.write_text(json.dumps(_VALID_CREATE), encoding="utf-8")

        # Mock port_file + resolve_org_slug + httpx.Client
        port_file_path = tmp_path / "daemon.port"
        port_file_path.write_text("19999")
        monkeypatch.setattr("cli.client.client.port_file", lambda: port_file_path)
        monkeypatch.setattr("cli._shared.resolve_org_slug", lambda args_org, available: "alpha")
        mock_client_cls = self._mock_httpx_client_factory(client)
        monkeypatch.setattr("httpx.Client", mock_client_cls)

        from cli.commands.skills import cmd_skills_create
        import io
        import sys as _sys
        args = self._make_cli_args(str(payload_path), "sess-cli-real")

        # Capture stdout
        save_stdout = _sys.stdout
        try:
            _sys.stdout = io.StringIO()
            cmd_skills_create(args)
            output = _sys.stdout.getvalue()
        finally:
            _sys.stdout = save_stdout

        assert "Skill created successfully." in output
        assert "skill_id:" in output
        assert "hr:my-custom-workflow" in output
        assert "version:" in output
        assert "content_hash:" in output

    def test_cli_create_malformed_input_rejected(self, client_with_runtime, tmp_path, monkeypatch):
        """CLI with malformed local JSON -> exits 1 with error message."""
        client, org = client_with_runtime
        _insert_test_task(org)
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-cli-mal", org_slug="alpha")

        payload_path = tmp_path / "bad-payload.json"
        payload_path.write_text("not json", encoding="utf-8")

        port_file_path = tmp_path / "daemon.port"
        port_file_path.write_text("19999")
        monkeypatch.setattr("cli.client.client.port_file", lambda: port_file_path)
        monkeypatch.setattr("cli._shared.resolve_org_slug", lambda args_org, available: "alpha")
        mock_client_cls = self._mock_httpx_client_factory(client)
        monkeypatch.setattr("httpx.Client", mock_client_cls)

        from cli.commands.skills import cmd_skills_create
        args = self._make_cli_args(str(payload_path), "sess-cli-mal")

        with pytest.raises(SystemExit) as exc_info:
            cmd_skills_create(args)
        assert exc_info.value.code == 1
        _assert_zero_residue(org)

    def test_cli_missing_session_id_rejected(self, client_with_runtime, tmp_path, monkeypatch):
        """CLI missing --session-id -> exits 1 with error."""
        client, org = client_with_runtime
        _insert_test_task(org)
        org.sessions.set_active("TASK-4530", "dev_agent", "sess-cli-ms", org_slug="alpha")

        import json
        payload_path = tmp_path / "skill-payload.json"
        payload_path.write_text(json.dumps(_VALID_CREATE), encoding="utf-8")

        port_file_path = tmp_path / "daemon.port"
        port_file_path.write_text("19999")
        monkeypatch.setattr("cli.client.client.port_file", lambda: port_file_path)
        monkeypatch.setattr("cli._shared.resolve_org_slug", lambda args_org, available: "alpha")
        mock_client_cls = self._mock_httpx_client_factory(client)
        monkeypatch.setattr("httpx.Client", mock_client_cls)

        from cli.commands.skills import cmd_skills_create
        args = self._make_cli_args(str(payload_path), "")  # empty session_id


        with pytest.raises(SystemExit) as exc_info:
            cmd_skills_create(args)
        assert exc_info.value.code == 1
        _assert_zero_residue(org)

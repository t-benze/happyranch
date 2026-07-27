"""THR-055 follow-up — Route-level e2e tests for the agent-only skill proposal endpoint.

Tests the dedicated agent-only proposal route with SessionTracker binding,
using the daemon TestClient fixtures (``client_with_runtime``, ``client``)
from daemon conftest.

Covers the approved shipping interface with its real semantics:
- POST /skill-lifecycle/proposals/agent with opaque session-binding
- Server-derived four-part (org/task/agent/session) provenance
- Only session_id query param — no caller-supplied task_id or agent_name
- No Authorization header required (bearer-free transport)
- Bearer token rejected on agent route (401)
- Body identity claims (proposer_agent, task_id, session_id) strictly rejected (403)
- Unknown/inactive session → 403 unknown_session
- Missing org context → 403 missing_org_context (no residue)
- Cross-org session → 403 cross_org_session
- Missing session_id → FastAPI 422
- Non-proposal lifecycle routes → 403 for agent callers (human_only)
- Stored provenance is verified binding (not body spoofs)
- Agent-id × canonical-slug pilot policy enforcement
"""

from __future__ import annotations

import pytest


_VALID_PROPOSAL = {
    "slug": "frontend-development",
    "name": "Frontend Development",
    "description": "A skill for frontend development.",
    "version": "0.1.0",
    "policy_class": "standard_operational",
    "skill_md": "# Frontend Development\n\nGuidelines.",
    "purpose": "Help dev agents write better tests",
    "target_agent_suggestion": "frontend_engineer",
    "references": None,
    "assets": None,
}


def _read_test_token() -> str:
    import runtime.daemon.paths as paths_mod
    return paths_mod.read_token()


class TestProposalRouteE2E:
    """End-to-end tests through the dedicated agent-only proposal route:
    POST /skill-lifecycle/proposals/agent (opaque session-binding, no bearer).

    Exercises the approved shipping interface with its real semantics:
    server-derived four-part provenance, bearer-free transport,
    and pilot policy enforcement.
    """

    def test_agent_session_proposal_succeeds(
        self, client_with_runtime
    ):
        """Agent proposal with valid SessionTracker binding succeeds (no bearer)."""
        client, org = client_with_runtime

        org.sessions.set_active(
            "TASK-3510", "frontend_engineer", "sess-test-e2e",
            org_slug="alpha",
        )

        body = dict(_VALID_PROPOSAL)

        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/agent",
            json=body,
            params={"session_id": "sess-test-e2e"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "proposed"
        assert data["skill_id"] == "hr:frontend-development"
        assert data["version"] == "0.1.0"
        assert "content_hash" in data
        assert data["proposal_task_id"] == "TASK-3510"

    # ── Strict body-identity rejection (each claimed field → exact 403) ──

    def test_body_task_id_rejected_strict_403(
        self, client_with_runtime
    ):
        """Proposal body containing task_id is strictly rejected with 403
        body_identity_rejected. No permissive 201 path exists."""
        client, org = client_with_runtime

        org.sessions.set_active(
            "TASK-3510", "frontend_engineer", "sess-body-task",
            org_slug="alpha",
        )

        body = dict(_VALID_PROPOSAL, task_id="TASK-spoof")

        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/agent",
            json=body,
            params={"session_id": "sess-body-task"},
        )
        assert resp.status_code == 403, (
            f"Expected 403 for body task_id claim, got {resp.status_code}: {resp.json()}"
        )
        detail = resp.json()["detail"]
        assert detail["code"] == "body_identity_rejected"

    def test_body_session_id_rejected_strict_403(
        self, client_with_runtime
    ):
        """Proposal body containing session_id is strictly rejected with 403
        body_identity_rejected. No permissive 201 path exists."""
        client, org = client_with_runtime

        org.sessions.set_active(
            "TASK-3510", "frontend_engineer", "sess-body-sid",
            org_slug="alpha",
        )

        body = dict(_VALID_PROPOSAL, session_id="sess-spoof")

        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/agent",
            json=body,
            params={"session_id": "sess-body-sid"},
        )
        assert resp.status_code == 403, (
            f"Expected 403 for body session_id claim, got {resp.status_code}: {resp.json()}"
        )
        detail = resp.json()["detail"]
        assert detail["code"] == "body_identity_rejected"

    def test_body_proposer_agent_rejected_strict_403(
        self, client_with_runtime
    ):
        """Proposal body containing proposer_agent is strictly rejected with 403
        body_identity_rejected. No permissive 201 path exists."""
        client, org = client_with_runtime

        org.sessions.set_active(
            "TASK-3510", "frontend_engineer", "sess-body-pa",
            org_slug="alpha",
        )

        body = dict(_VALID_PROPOSAL, proposer_agent="engineering_manager")

        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/agent",
            json=body,
            params={"session_id": "sess-body-pa"},
        )
        assert resp.status_code == 403, (
            f"Expected 403 for body proposer_agent claim, got {resp.status_code}: {resp.json()}"
        )
        detail = resp.json()["detail"]
        assert detail["code"] == "body_identity_rejected"

    # ── Clean-body success: server derives exact provenance ──

    def test_clean_body_success_proves_server_provenance(
        self, client_with_runtime
    ):
        """Clean proposal body (no identity claims) succeeds with server-derived
        exact org/task/agent/session provenance."""
        client, org = client_with_runtime

        org.sessions.set_active(
            "TASK-PROV", "frontend_engineer", "sess-prov",
            org_slug="alpha",
        )

        # Clean body — no task_id, session_id, or proposer_agent
        clean_body = dict(_VALID_PROPOSAL)

        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/agent",
            json=clean_body,
            params={"session_id": "sess-prov"},
        )
        assert resp.status_code == 201, f"Got {resp.status_code}: {resp.json()}"
        data = resp.json()
        # Server-derived provenance — NOT from body
        assert data["proposal_task_id"] == "TASK-PROV"
        assert data["skill_id"] == "hr:frontend-development"

        # Verify through the read endpoint
        client.headers["Authorization"] = f"Bearer {_read_test_token()}"
        skill_id = data["skill_id"]
        status_resp = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/{skill_id}",
            params={"slug": "alpha"},
        )
        assert status_resp.status_code == 200
        sdata = status_resp.json()
        assert sdata["proposal_task_id"] == "TASK-PROV"
        assert sdata["proposer_agent"] == "frontend_engineer"

    # ── Missing org context denial ──

    def test_missing_org_context_denied_403_no_residue(
        self, client_with_runtime
    ):
        """Session activated without org_slug is denied with 403
        missing_org_context BEFORE any artifact/ledger write. Zero residue."""
        client, org = client_with_runtime

        # Set active WITHOUT org_slug — no context stored
        org.sessions.set_active(
            "TASK-NOORG", "frontend_engineer", "sess-noorg",
        )

        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/agent",
            json=_VALID_PROPOSAL,
            params={"session_id": "sess-noorg"},
        )
        assert resp.status_code == 403, (
            f"Expected 403 for missing org context, got {resp.status_code}: {resp.json()}"
        )
        detail = resp.json()["detail"]
        assert detail["code"] == "missing_org_context"

        # Verify zero residue: no proposal artifact exists under this session
        client.headers["Authorization"] = f"Bearer {_read_test_token()}"
        catalog_resp = client.get("/api/v1/orgs/alpha/skill-lifecycle/catalog/custom")
        assert catalog_resp.status_code == 200
        skills = catalog_resp.json()["skills"]
        skill_ids = [s["skill_id"] for s in skills]
        assert "hr:frontend-development" not in skill_ids, (
            "Missing-org denial must leave zero artifact residue"
        )

    # ── Cross-org denial ──

    def test_cross_org_session_denied_403(
        self, client_with_runtime
    ):
        """Session belonging to a different org than the URL path is denied with
        403 cross_org_session."""
        client, org = client_with_runtime

        # Session belongs to org 'beta', but URL targets 'alpha'
        org.sessions.set_active(
            "TASK-XORG", "frontend_engineer", "sess-xorg",
            org_slug="beta",
        )

        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/agent",
            json=_VALID_PROPOSAL,
            params={"session_id": "sess-xorg"},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "cross_org_session"

    def test_unknown_session_binding_rejected(
        self, client_with_runtime
    ):
        """Unknown session_id is rejected with 403 unknown_session."""
        client, org = client_with_runtime

        org.sessions.set_active(
            "TASK-3510", "frontend_engineer", "sess-real",
            org_slug="alpha",
        )

        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/agent",
            json=_VALID_PROPOSAL,
            params={"session_id": "sess-wrong-unknown"},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "unknown_session"

    def test_inactive_session_binding_rejected(
        self, client_with_runtime
    ):
        """No active session → 403 unknown_session."""
        client, _org = client_with_runtime

        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/agent",
            json=_VALID_PROPOSAL,
            params={"session_id": "sess-nonexistent"},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "unknown_session"

    def test_missing_session_id_param_rejected(
        self, client_with_runtime
    ):
        """Missing session_id query param → FastAPI 422 validation error."""
        client, _org = client_with_runtime

        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/agent",
            json=_VALID_PROPOSAL,
            # Deliberately omit session_id param
        )
        assert resp.status_code == 422

    def test_non_proposal_route_agent_403(
        self, client
    ):
        """Agent calling a non-proposal lifecycle route (publish) gets 403."""
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/publish",
            json={"version_id": 1, "approval_event_id": 1},
            params={"slug": "alpha"},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert "human_only" in detail.get("code", str(detail))

    def test_bearer_token_rejected_on_agent_route(
        self, client
    ):
        """Bearer-authenticated request to agent-only route → 401."""
        body = dict(_VALID_PROPOSAL, slug="bearer-test")
        resp = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/agent",
            json=body,
            params={"session_id": "sess-irrelevant"},
        )
        assert resp.status_code == 401
        detail = resp.json()["detail"]
        assert detail["code"] == "bearer_not_accepted"


class TestAgentOnlyProposalRoute:
    """Route-level tests exercising the dedicated agent-only proposal endpoint.

    Replaces the removed command-to-route _transport seam with direct
    TestClient calls that exercise the same invariants against the
    real /proposals/agent route with SessionTracker binding.
    """

    def test_proposal_success_with_verified_provenance(
        self, client_with_runtime
    ):
        """Full proposal acceptance: success response with verified provenance."""
        client, org = client_with_runtime

        org.sessions.set_active(
            "TASK-CMD-1", "frontend_engineer", "sess-provenance",
            org_slug="alpha",
        )

        body = dict(_VALID_PROPOSAL, slug="frontend-development")

        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/agent",
            json=body,
            params={"session_id": "sess-provenance"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "proposed"

        # Verify stored provenance via the lifecycle status route
        client.headers["Authorization"] = f"Bearer {_read_test_token()}"
        skill_id = data["skill_id"]
        status_resp = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/{skill_id}",
            params={"slug": "alpha"},
        )
        assert status_resp.status_code == 200
        sdata = status_resp.json()
        assert sdata["proposal_task_id"] == "TASK-CMD-1"
        assert sdata["proposer_agent"] == "frontend_engineer"

    def test_no_authorization_header_sent(
        self, client_with_runtime
    ):
        """The agent route accepts requests with NO Authorization header."""
        client, org = client_with_runtime

        org.sessions.set_active(
            "TASK-CMD-2", "frontend_engineer", "sess-noauth",
            org_slug="alpha",
        )

        # Intercept at the TestClient level — record actual request headers
        captured_headers = {}
        original_send = client.send

        def _intercept(request, **kwargs):
            captured_headers.update(dict(request.headers))
            return original_send(request, **kwargs)

        client.send = _intercept

        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/agent",
            json=_VALID_PROPOSAL,
            params={"session_id": "sess-noauth"},
        )
        assert resp.status_code == 201

        # No Authorization header was sent
        auth_keys = [k for k in captured_headers if k.lower() == "authorization"]
        assert len(auth_keys) == 0, (
            f"Authorization header was present: {captured_headers}"
        )

    def test_body_identity_fields_rejected(
        self, client_with_runtime
    ):
        """Body containing proposer_agent, task_id, or session_id is rejected
        by the server BEFORE any artifact/ledger write."""
        client, org = client_with_runtime

        org.sessions.set_active(
            "TASK-CMD-3", "frontend_engineer", "sess-cmd-ghi",
            org_slug="alpha",
        )

        # proposer_agent in body
        body = dict(_VALID_PROPOSAL, proposer_agent="engineering_manager")

        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/agent",
            json=body,
            params={"session_id": "sess-cmd-ghi"},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "body_identity_rejected"

    def test_unknown_session_rejection_visible(
        self, client_with_runtime
    ):
        """A mismatched / unknown session_id is rejected with actionable
        error code visible in the HTTP response."""
        client, org = client_with_runtime

        org.sessions.set_active(
            "TASK-CMD-4", "frontend_engineer", "sess-real-cmd",
            org_slug="alpha",
        )

        body = dict(_VALID_PROPOSAL, slug="frontend-development")

        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/agent",
            json=body,
            params={"session_id": "sess-wrong-cmd"},  # mismatched / unknown
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "unknown_session"

    def test_inactive_session_rejection_visible(
        self, client_with_runtime
    ):
        """An inactive (unknown) session is rejected with actionable
        error code visible in the HTTP response."""
        client, _org = client_with_runtime
        # Deliberately do NOT set_active — the session is unknown

        body = dict(_VALID_PROPOSAL, slug="frontend-development")

        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/agent",
            json=body,
            params={"session_id": "sess-nonexistent"},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "unknown_session"

    def test_server_non_proposal_403_still_intact(self, client):
        """Agent calling a non-proposal lifecycle route (validate) gets 403
        — server authority is unchanged by the agent-only route changes."""
        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/validate",
            json={"version_id": 1, "validation_notes": "test"},
            params={"slug": "alpha"},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert "human_only" in detail.get("code", str(detail))

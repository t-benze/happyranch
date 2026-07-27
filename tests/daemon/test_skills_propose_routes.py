"""THR-055 follow-up — Route-level e2e tests for skill proposal endpoint.

Tests the real proposal route with SessionTracker binding, using the daemon
TestClient fixtures (``client_with_runtime``, ``client``) from daemon conftest.

Covers:
- Unauthenticated agent proposal succeeds with valid SessionTracker binding
- Stored provenance is verified task/session/agent (not body spoofs)
- Mismatched/inactive session binding → 403
- Missing binding params → 403
- Non-proposal lifecycle routes → 403 for agent callers (human_only)
- Bearer-authenticated proposal succeeds (founder path)
"""

from __future__ import annotations


_VALID_PROPOSAL = {
    "slug": "frontend-testing",
    "name": "Frontend Testing",
    "description": "A skill for frontend testing.",
    "version": "0.1.0",
    "policy_class": "standard_operational",
    "skill_md": "# Frontend Testing\n\nGuidelines.",
    "purpose": "Help dev agents write better tests",
    "target_agent_suggestion": "dev_agent",
    "references": None,
    "assets": None,
}


def _read_test_token() -> str:
    import runtime.daemon.paths as paths_mod
    return paths_mod.read_token()


class TestProposalRouteE2E:
    """End-to-end tests through the real proposal route with TestClient."""

    def test_unauthenticated_session_proposal_succeeds(
        self, client_with_runtime
    ):
        """Agent proposal without bearer token succeeds with valid SessionTracker binding."""
        client, org = client_with_runtime

        org.sessions.set_active("TASK-3510", "dev_agent", "sess-test-e2e")

        body = dict(_VALID_PROPOSAL)

        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals",
            json=body,
            params={
                "slug": "alpha",
                "task_id": "TASK-3510",
                "session_id": "sess-test-e2e",
                "agent_name": "dev_agent",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "proposed"
        assert data["skill_id"] == "hr:frontend-testing"
        assert data["version"] == "0.1.0"
        assert "content_hash" in data
        assert data["proposal_task_id"] == "TASK-3510"

    def test_proposal_stored_provenance_is_verified_binding(
        self, client_with_runtime
    ):
        """Stored provenance is the verified task/session/agent, not body spoofs."""
        client, org = client_with_runtime

        org.sessions.set_active("TASK-3510", "dev_agent", "sess-real")

        spoof_body = dict(
            _VALID_PROPOSAL,
            task_id="TASK-spoof",
            session_id="sess-spoof",
            proposer_agent="engineering_manager",
        )

        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals",
            json=spoof_body,
            params={
                "slug": "alpha",
                "task_id": "TASK-3510",
                "session_id": "sess-real",
                "agent_name": "dev_agent",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["proposal_task_id"] == "TASK-3510"

        skill_id = data["skill_id"]
        client.headers["Authorization"] = f"Bearer {_read_test_token()}"
        status_resp = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/{skill_id}",
            params={"slug": "alpha"},
        )
        assert status_resp.status_code == 200
        sdata = status_resp.json()
        assert sdata["proposal_task_id"] == "TASK-3510"
        assert sdata["proposer_agent"] == "dev_agent"

    def test_mismatched_session_binding_rejected(
        self, client_with_runtime
    ):
        """Mismatched session_id is rejected with 403 session_mismatch."""
        client, org = client_with_runtime

        org.sessions.set_active("TASK-3510", "dev_agent", "sess-real")

        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals",
            json=_VALID_PROPOSAL,
            params={
                "slug": "alpha",
                "task_id": "TASK-3510",
                "session_id": "sess-wrong-mismatch",
                "agent_name": "dev_agent",
            },
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "session_mismatch"

    def test_inactive_session_binding_rejected(
        self, client_with_runtime
    ):
        """No active session for agent/task → 403 unknown_session."""
        client, org = client_with_runtime

        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals",
            json=_VALID_PROPOSAL,
            params={
                "slug": "alpha",
                "task_id": "TASK-nonexistent",
                "session_id": "sess-none",
                "agent_name": "dev_agent",
            },
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "unknown_session"

    def test_missing_binding_params_rejected(
        self, client_with_runtime
    ):
        """Missing task_id/session_id/agent_name without bearer → 403."""
        client, _ = client_with_runtime

        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals",
            json=_VALID_PROPOSAL,
            params={"slug": "alpha"},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "agent_identity_required"

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

    def test_bearer_proposal_succeeds(
        self, client
    ):
        """Bearer-authenticated (founder) proposal succeeds."""
        body = dict(
            _VALID_PROPOSAL,
            slug="bearer-test",
            name="Bearer Test",
        )
        resp = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals",
            json=body,
            params={"slug": "alpha"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "proposed"

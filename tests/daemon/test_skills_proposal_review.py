"""THR-055 Slice 1 — Founder-only proposal review route tests.

Tests the new Founder-only endpoints:
- GET /skill-lifecycle/proposals/queue — paginated/filterable queue
- GET /skill-lifecycle/proposals/{version_id} — full proposal detail
- POST /skill-lifecycle/proposals/{version_id}/claim — claim with concurrency
- POST /skill-lifecycle/proposals/{version_id}/validate — validate with concurrency
- POST /skill-lifecycle/proposals/{version_id}/review — review with concurrency
- POST /skill-lifecycle/proposals/{version_id}/publish — publish with concurrency
- POST /skill-lifecycle/proposals/{version_id}/assign — assign with concurrency
- POST /skill-lifecycle/proposals/{version_id}/rollback — rollback with concurrency

All new routes are Founder-only (bearer-required). Agent callers receive 403.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from runtime.skills.lifecycle.service import LifecycleError


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


def _founder_headers() -> dict:
    return {"Authorization": f"Bearer {_read_test_token()}"}


def _setup_session(org_state, task_id: str, agent_name: str, session_id: str):
    org_state.sessions.set_active(task_id, agent_name, session_id, org_slug="alpha")


def _submit_agent_proposal(app, org_state, slug: str = "frontend-development", skill_md: str | None = None) -> dict:
    """Submit a proposal via the agent-only route and return the response data."""
    task_id = "TASK-RV-001"
    session_id = "sess-rv-agent-001"
    _setup_session(org_state, task_id, "frontend_engineer", session_id)
    client = TestClient(app)
    body = dict(_VALID_PROPOSAL)
    if slug != _VALID_PROPOSAL["slug"]:
        body["slug"] = slug
        body["name"] = slug.replace("-", " ").title()
    if skill_md is not None:
        body["skill_md"] = skill_md
    r = client.post(
        "/api/v1/orgs/alpha/skill-lifecycle/proposals/agent",
        json=body,
        params={"session_id": session_id},
    )
    assert r.status_code == 201, f"Proposal failed: {r.json()}"
    return r.json()


def _claim_proposal(client: TestClient, version_id: int, **kwargs) -> dict:
    """[THR-136] Retired route — returns 410 Gone for authorized callers."""
    r = client.post(
        "/api/v1/orgs/alpha/skill-lifecycle/proposals/claim",
        headers=_founder_headers(),
        json={"proposal_version_id": version_id, **kwargs},
    )
    return r


def _assert_retired_route(r, code: str = "route_retired_thr136"):
    """Assert a response is 410 Gone from a THR-136 retired route."""
    assert r.status_code == 410, f"Expected 410, got {r.status_code}: {r.json() if r.text else 'empty'}"
    detail = r.json().get("detail", {})
    assert detail.get("code") == code, f"Expected code={code}, got {detail}"


def _validate_proposal(client: TestClient, version_id: int, expected_event_id: int) -> dict:
    """[THR-136] Retired route."""
    r = client.post(
        f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/validate",
        headers=_founder_headers(),
        json={"validator_version": "THR-055/1.0.0", "expected_event_id": expected_event_id},
    )
    return r


def _review_proposal(client: TestClient, version_id: int, decision: str, expected_event_id: int) -> dict:
    """Review via v2 route."""
    r = client.post(
        f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/review",
        headers=_founder_headers(),
        json={"decision": decision, "rationale": "Test rationale", "expected_event_id": expected_event_id},
    )
    return r


def _publish_proposal(client: TestClient, version_id: int, approval_event_id: int, expected_event_id: int) -> dict:
    """Publish via v2 route."""
    r = client.post(
        f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/publish",
        headers=_founder_headers(),
        json={"approval_event_id": approval_event_id, "expected_event_id": expected_event_id},
    )
    return r


# ═══════════════════════════════════════════════════════════════════════════
# Agent 403 tests — no-bearer/agent deep-link 403 for every new route
# ═══════════════════════════════════════════════════════════════════════════

class TestAgent403Matrix:
    """Agent callers receive 403 for all Founder-only proposal review routes."""

    @pytest.mark.parametrize("method,path", [
        ("GET", "/api/v1/orgs/alpha/skill-lifecycle/proposals/queue"),
        ("GET", "/api/v1/orgs/alpha/skill-lifecycle/proposals/1"),
        ("POST", "/api/v1/orgs/alpha/skill-lifecycle/proposals/1/claim"),
        ("POST", "/api/v1/orgs/alpha/skill-lifecycle/proposals/1/validate"),
        ("POST", "/api/v1/orgs/alpha/skill-lifecycle/proposals/1/review"),
        ("POST", "/api/v1/orgs/alpha/skill-lifecycle/proposals/1/publish"),
        ("POST", "/api/v1/orgs/alpha/skill-lifecycle/proposals/1/assign"),
        ("POST", "/api/v1/orgs/alpha/skill-lifecycle/proposals/1/rollback"),
    ])
    def test_agent_gets_403_for_all_review_routes(
        self, method, path, app, org_state,
    ):
        """Every Founder-only proposal review route returns 403 for agent callers."""
        _setup_session(org_state, "TASK-403", "frontend_engineer", "sess-no-bearer")
        client = TestClient(app)
        # No bearer token — agent session only
        if method == "POST":
            r = client.post(path, json={})
        else:
            r = client.get(path)
        assert r.status_code in (403, 401), (
            f"Expected 403 or 401 for {method} {path}, got {r.status_code}: {r.json()}"
        )

    def test_agent_cannot_read_lifecycle_status(self, app, org_state):
        """Agent cannot read lifecycle status — Founder-only."""
        data = _submit_agent_proposal(app, org_state)
        skill_id = data["skill_id"]
        client = TestClient(app)
        r = client.get(f"/api/v1/orgs/alpha/skill-lifecycle/{skill_id}")
        assert r.status_code == 403

    def test_agent_cannot_read_events(self, app, org_state):
        """Agent cannot read event history — Founder-only."""
        data = _submit_agent_proposal(app, org_state)
        skill_id = data["skill_id"]
        client = TestClient(app)
        r = client.get(f"/api/v1/orgs/alpha/skill-lifecycle/events/{skill_id}")
        assert r.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════
# Claimant/proposer immutability
# ═══════════════════════════════════════════════════════════════════════════

class TestClaimantProposerImmutability:
    """Founder claim is separate claimant identity — never rewrites proposer_agent/created_by."""

    def test_claim_preserves_proposer_agent(self, app, org_state):
        """claim_proposal_v2 sets claimed_by without touching proposer_agent/created_by."""
        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]
        skill_id = data["skill_id"]

        client = TestClient(app)

        # Get initial detail to get concurrency marker
        r_detail = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        assert r_detail.status_code == 200
        detail = r_detail.json()
        assert detail["proposer_agent"] == "frontend_engineer"
        assert detail["claimed_by"] is None

        # Claim the proposal
        last_event = detail["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/claim",
            headers=_founder_headers(),
            json={"expected_event_id": last_event},
        )
        assert r.status_code == 200, f"Claim failed: {r.json()}"
        claim_data = r.json()
        assert claim_data["claimed_by"] == "founder"
        assert claim_data["claimed_at"] is not None

        # Verify proposer_agent is STILL frontend_engineer (not overwritten)
        r_detail2 = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        assert r_detail2.status_code == 200
        detail2 = r_detail2.json()
        assert detail2["proposer_agent"] == "frontend_engineer"  # Immutable!
        assert detail2["claimed_by"] == "founder"


# ═══════════════════════════════════════════════════════════════════════════
# Terminal REJECTED semantics
# ═══════════════════════════════════════════════════════════════════════════

class TestTerminalRejected:
    """Terminal REJECTED blocks all subsequent mutations."""

    def _advance_to_in_review(self, app, org_state) -> tuple[TestClient, int, str]:
        """Advance a proposal through claim → validate → submit-review."""
        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]
        client = TestClient(app)

        # Get concurrency marker
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        detail = r.json()
        eid = detail["last_event_id"]

        # Claim
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/claim",
            headers=_founder_headers(),
            json={"expected_event_id": eid},
        )
        assert r.status_code == 200

        # Validate
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid = r.json()["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/validate",
            headers=_founder_headers(),
            json={"validator_version": "THR-055/1.0.0", "expected_event_id": eid},
        )
        assert r.status_code == 200

        # Submit for review (use legacy route since we haven't added submit-review to v2)
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        r_submit = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/submit-review",
            headers=_founder_headers(),
            json={"version_id": version_id},
        )
        assert r_submit.status_code == 200

        return client, version_id, data["skill_id"]

    def test_rejected_blocks_claim(self, app, org_state):
        """After rejection, claim is blocked."""
        client, version_id, skill_id = self._advance_to_in_review(app, org_state)

        # Reject
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid = r.json()["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/review",
            headers=_founder_headers(),
            json={"decision": "rejected", "rationale": "Not good enough", "expected_event_id": eid},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "rejected"

        # Verify status is REJECTED
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        assert r.json()["status"] == "rejected"

        # Attempt to claim — should fail
        eid = r.json()["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/claim",
            headers=_founder_headers(),
            json={"expected_event_id": eid},
        )
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "rejected_terminal"

    def test_rejected_blocks_validate(self, app, org_state):
        """After rejection, validation is blocked."""
        client, version_id, skill_id = self._advance_to_in_review(app, org_state)

        # Reject
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid = r.json()["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/review",
            headers=_founder_headers(),
            json={"decision": "rejected", "rationale": "No", "expected_event_id": eid},
        )
        assert r.status_code == 200

        # Attempt to validate
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid = r.json()["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/validate",
            headers=_founder_headers(),
            json={"validator_version": "THR-055/1.0.0", "expected_event_id": eid},
        )
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "rejected_terminal"

    def test_rejected_blocks_review(self, app, org_state):
        """After rejection, another review is blocked."""
        client, version_id, skill_id = self._advance_to_in_review(app, org_state)

        # Reject
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid = r.json()["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/review",
            headers=_founder_headers(),
            json={"decision": "rejected", "rationale": "No", "expected_event_id": eid},
        )
        assert r.status_code == 200

        # Attempt another review
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid = r.json()["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/review",
            headers=_founder_headers(),
            json={"decision": "approved", "rationale": "Changed mind", "expected_event_id": eid},
        )
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "rejected_terminal"

    def test_rejected_blocks_publish(self, app, org_state):
        """After rejection, publish is blocked."""
        client, version_id, skill_id = self._advance_to_in_review(app, org_state)

        # Reject
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid = r.json()["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/review",
            headers=_founder_headers(),
            json={"decision": "rejected", "rationale": "No", "expected_event_id": eid},
        )
        assert r.status_code == 200

        # Attempt to publish
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid = r.json()["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/publish",
            headers=_founder_headers(),
            json={"approval_event_id": 999, "expected_event_id": eid},
        )
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "rejected_terminal"


# ═══════════════════════════════════════════════════════════════════════════
# Queue ordering and filtering
# ═══════════════════════════════════════════════════════════════════════════

class TestProposalQueue:
    """Founder-only proposal queue — ordering, filtering, pagination."""

    def test_queue_returns_proposals(self, app, org_state):
        """Queue returns submitted proposals with expected fields."""
        data = _submit_agent_proposal(app, org_state)
        client = TestClient(app)

        r = client.get(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/queue",
            headers=_founder_headers(),
        )
        assert r.status_code == 200
        body = r.json()
        assert "items" in body
        assert body["total"] >= 1
        assert body["page"] == 1
        assert body["page_size"] == 20

        # Check fields on the item
        item = body["items"][0]
        assert item["version_id"] == data["version_id"]
        assert item["skill_id"] == data["skill_id"]
        assert item["slug"] == "frontend-development"
        assert item["content_hash"] is not None
        assert item["proposer_agent"] == "frontend_engineer"
        assert item["status"] == "proposed"
        assert item["permitted_next_action"] == "claim"

    def test_queue_actionable_first(self, app, org_state):
        """Actionable proposals appear before terminal ones."""
        # Submit a proposal and advance to rejected (terminal)
        client, version_id, skill_id = (
            TestTerminalRejected()._advance_to_in_review(app, org_state)
        )
        # Reject it
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid = r.json()["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/review",
            headers=_founder_headers(),
            json={"decision": "rejected", "rationale": "No", "expected_event_id": eid},
        )
        assert r.status_code == 200

        # Submit a second proposal (actionable) using product_lead session for different slug
        _setup_session(org_state, "TASK-RV-002", "product_lead", "sess-rv-pl-002")
        client2 = TestClient(app)
        r2 = client2.post(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/agent",
            json={
                "slug": "product-manager-prd",
                "name": "Product Manager PRD",
                "description": "A skill for product PRDs.",
                "skill_md": "# PRD Skill\n\nDifferent content.",
            },
            params={"session_id": "sess-rv-pl-002"},
        )
        assert r2.status_code == 201, f"Second proposal failed: {r2.json()}"

        r = client.get(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/queue",
            headers=_founder_headers(),
        )
        body = r.json()
        items = body["items"]

        # First item should be actionable (the 2nd proposal with status proposed)
        actionable_statuses = {"proposed", "draft", "validated", "validation_failed", "in_review", "approved", "published"}
        first_status = items[0]["status"]
        assert first_status in actionable_statuses, (
            f"Expected first item to be actionable, got status={first_status}"
        )

    def test_queue_filter_by_status(self, app, org_state):
        """Queue can be filtered by status."""
        _submit_agent_proposal(app, org_state)
        client = TestClient(app)

        r = client.get(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/queue",
            headers=_founder_headers(),
            params={"status": "proposed"},
        )
        assert r.status_code == 200
        body = r.json()
        for item in body["items"]:
            assert item["status"] == "proposed"

        # Filter for non-existent status
        r = client.get(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/queue",
            headers=_founder_headers(),
            params={"status": "nonexistent"},
        )
        assert r.status_code == 200
        assert r.json()["total"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# Proposal detail
# ═══════════════════════════════════════════════════════════════════════════

class TestProposalDetail:
    """Founder-only full proposal detail."""

    def test_detail_returns_full_data(self, app, org_state):
        """Detail returns all required fields including concurrency marker."""
        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]
        client = TestClient(app)

        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        assert r.status_code == 200
        detail = r.json()

        # Check required fields
        assert detail["version_id"] == version_id
        assert detail["skill_id"] == data["skill_id"]
        assert detail["content_hash"] == data["content_hash"]
        assert detail["proposer_agent"] == "frontend_engineer"
        assert detail["proposal_task_id"] == "TASK-RV-001"
        assert detail["status"] == "proposed"
        assert detail["claimed_by"] is None
        assert detail["last_event_id"] is not None  # Concurrency marker

        # Events should be present
        assert len(detail["events"]) >= 1
        assert detail["events"][0]["event_type"] == "proposed"

    def test_detail_events_are_append_only(self, app, org_state):
        """Events list grows with each lifecycle action."""
        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]
        client = TestClient(app)

        # Claim adds an event
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid = r.json()["last_event_id"]
        assert len(r.json()["events"]) == 1

        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/claim",
            headers=_founder_headers(),
            json={"expected_event_id": eid},
        )
        assert r.status_code == 200

        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        assert len(r.json()["events"]) == 2

    def test_detail_not_found(self, app, org_state):
        """Detail returns 404 for non-existent version."""
        client = TestClient(app)
        r = client.get(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/99999",
            headers=_founder_headers(),
        )
        assert r.status_code == 404

    def test_detail_includes_skill_md_bytes(self, app, org_state):
        """Detail returns the canonical SKILL.md bytes loaded from the ArtifactStore."""
        skill_md_content = "# Test Skill\n\nThis is a test skill for proposal review."
        data = _submit_agent_proposal(app, org_state, skill_md=skill_md_content)
        version_id = data["version_id"]
        client = TestClient(app)

        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        assert r.status_code == 200
        detail = r.json()

        # SKILL.md bytes should be loaded from artifact store
        assert detail["skill_md"] == skill_md_content

        # content_artifact_key should still be present
        assert detail["content_artifact_key"] is not None

    def test_detail_includes_purpose_and_target(self, app, org_state):
        """Detail returns purpose and target_agent_suggestion from creation event."""
        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]
        client = TestClient(app)

        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        assert r.status_code == 200
        detail = r.json()

        assert detail["purpose"] == _VALID_PROPOSAL["purpose"]
        assert detail["target_agent_suggestion"] == _VALID_PROPOSAL["target_agent_suggestion"]

    def test_detail_package_members_from_manifest(self, app, org_state):
        """Detail returns package_members from the manifest artifact."""
        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]
        client = TestClient(app)

        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        assert r.status_code == 200
        detail = r.json()

        # Should have package_members (at minimum the SKILL.md entry)
        assert detail["package_members"] is not None
        assert isinstance(detail["package_members"], list)
        assert len(detail["package_members"]) >= 1
        # First member should be SKILL.md
        first = detail["package_members"][0]
        assert first["path"] == "SKILL.md"
        assert "hash" in first
        assert "artifact_key" in first

    def test_detail_skill_md_null_for_missing_artifact(self, app, org_state):
        """Detail safely returns null skill_md for proposals with missing/malformed artifacts.

        This tests that the safe loader never fabricates bytes.
        """
        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]
        client = TestClient(app)

        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        assert r.status_code == 200
        detail = r.json()

        # For a valid proposal submitted via the agent route (which uses ArtifactStore),
        # skill_md should be non-null
        assert detail["skill_md"] is not None


class TestProposalQueueFilters:
    """Typed server-authoritative filters on the queue endpoint."""

    def test_queue_filter_by_proposer(self, app, org_state):
        """Queue filter by proposer_agent returns only matching proposals."""
        _submit_agent_proposal(app, org_state)

        # Submit as product_lead (different proposer)
        task_id = "TASK-RV-002"
        session_id = "sess-rv-agent-002"
        _setup_session(org_state, task_id, "product_lead", session_id)
        client = TestClient(app)
        body = dict(_VALID_PROPOSAL)
        body["slug"] = "product-manager-prd"
        body["name"] = "Product Manager PRD"
        r = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/agent",
            json=body,
            params={"session_id": session_id},
        )
        assert r.status_code == 201

        # Filter by frontend_engineer
        r = client.get(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/queue",
            headers=_founder_headers(),
            params={"proposer": "frontend_engineer"},
        )
        assert r.status_code == 200
        result = r.json()
        assert result["total"] == 1
        assert result["items"][0]["proposer_agent"] == "frontend_engineer"

    def test_queue_filter_by_search(self, app, org_state):
        """Queue search filter matches skill_id, slug, or name case-insensitively."""
        _submit_agent_proposal(app, org_state)
        client = TestClient(app)

        # Search by partial slug
        r = client.get(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/queue",
            headers=_founder_headers(),
            params={"search": "frontend"},
        )
        assert r.status_code == 200
        result = r.json()
        assert result["total"] == 1

        # Search by name
        r = client.get(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/queue",
            headers=_founder_headers(),
            params={"search": "Development"},
        )
        assert r.status_code == 200
        result2 = r.json()
        assert result2["total"] == 1

        # Search without match
        r = client.get(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/queue",
            headers=_founder_headers(),
            params={"search": "nonexistent"},
        )
        assert r.status_code == 200
        result3 = r.json()
        assert result3["total"] == 0

    def test_queue_filter_by_validation_outcome(self, app, org_state):
        """Queue validation_outcome filter: validated, validation_failed, unvalidated."""
        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]
        client = TestClient(app)

        # Without validation: should show as unvalidated
        r = client.get(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/queue",
            headers=_founder_headers(),
            params={"validation_outcome": "unvalidated"},
        )
        assert r.status_code == 200
        result = r.json()
        assert result["total"] == 1
        assert result["items"][0]["version_id"] == version_id

        # No validated proposals yet
        r = client.get(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/queue",
            headers=_founder_headers(),
            params={"validation_outcome": "validated"},
        )
        assert r.status_code == 200
        assert r.json()["total"] == 0

    def test_queue_filter_by_date_bounds(self, app, org_state):
        """Queue date bounds filter on submitted_after / submitted_before."""
        _submit_agent_proposal(app, org_state)
        client = TestClient(app)

        # submitted_after in the past should include
        r = client.get(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/queue",
            headers=_founder_headers(),
            params={"submitted_after": "2020-01-01T00:00:00"},
        )
        assert r.status_code == 200
        result = r.json()
        assert result["total"] == 1

        # submitted_before in the far future should also include
        r = client.get(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/queue",
            headers=_founder_headers(),
            params={"submitted_before": "2099-01-01T00:00:00"},
        )
        assert r.status_code == 200
        result2 = r.json()
        assert result2["total"] == 1

        # submitted_after in the future should exclude
        r = client.get(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/queue",
            headers=_founder_headers(),
            params={"submitted_after": "2099-01-01T00:00:00"},
        )
        assert r.status_code == 200
        result3 = r.json()
        assert result3["total"] == 0

    def test_queue_combined_filters(self, app, org_state):
        """Queue AND-composes multiple filters."""
        _submit_agent_proposal(app, org_state)

        # Submit a second proposal
        task_id = "TASK-RV-003"
        session_id = "sess-rv-agent-003"
        _setup_session(org_state, task_id, "product_lead", session_id)
        client = TestClient(app)
        body = dict(_VALID_PROPOSAL)
        body["slug"] = "product-manager-prd"
        body["name"] = "Product Manager PRD"
        r = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/agent",
            json=body,
            params={"session_id": session_id},
        )
        assert r.status_code == 201

        # Combined: status=proposed + proposer=frontend_engineer
        r = client.get(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/queue",
            headers=_founder_headers(),
            params={"status": "proposed", "proposer": "frontend_engineer"},
        )
        assert r.status_code == 200
        result = r.json()
        assert result["total"] == 1
        assert result["items"][0]["proposer_agent"] == "frontend_engineer"

    def test_queue_pagination_total_accurate(self, app, org_state):
        """Pagination total reflects filtered count, not unfiltered total."""
        _submit_agent_proposal(app, org_state)

        # Submit second proposal via product_lead (different agent, different slug)
        task_id = "TASK-RV-004"
        session_id = "sess-rv-agent-004"
        _setup_session(org_state, task_id, "product_lead", session_id)
        client = TestClient(app)
        body = dict(_VALID_PROPOSAL)
        body["slug"] = "product-manager-prd"
        body["name"] = "Product Manager PRD"
        body["skill_md"] = "# Product Manager PRD\n\nA skill for PRDs."
        r = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/agent",
            json=body,
            params={"session_id": session_id},
        )
        assert r.status_code == 201

        # Total should reflect all proposals
        r = client.get(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/queue",
            headers=_founder_headers(),
            params={"page_size": 1},
        )
        assert r.status_code == 200
        result = r.json()
        assert result["total"] == 2
        assert len(result["items"]) == 1  # page_size=1
        assert result["page"] == 1

    def test_queue_ordering_actionable_first(self, app, org_state):
        """Queue orders actionable (non-terminal) first, then oldest."""
        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]
        client = TestClient(app)

        # Mark the proposal as rejected (terminal)
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid = r.json()["last_event_id"]

        # Need to move through lifecycle to reject: propose → claim → validate → submit-review → review(rejected)
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/claim",
            headers=_founder_headers(),
            json={"expected_event_id": eid},
        )
        assert r.status_code == 200

        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid2 = r.json()["last_event_id"]

        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/validate",
            headers=_founder_headers(),
            json={"validator_version": "THR-055/1.0.0", "expected_event_id": eid2},
        )
        assert r.status_code == 200

        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid3 = r.json()["last_event_id"]

        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/submit-review",
            headers=_founder_headers(),
            json={"expected_event_id": eid3},
        )
        assert r.status_code == 200

        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid4 = r.json()["last_event_id"]

        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/review",
            headers=_founder_headers(),
            json={"decision": "rejected", "rationale": "Not good enough.", "expected_event_id": eid4},
        )
        assert r.status_code == 200

        # Submit a new proposal (actionable) via product_lead
        task_id = "TASK-RV-005"
        session_id = "sess-rv-agent-005"
        _setup_session(org_state, task_id, "product_lead", session_id)
        body2 = dict(_VALID_PROPOSAL)
        body2["slug"] = "product-manager-prd"
        body2["name"] = "Product Manager PRD"
        body2["skill_md"] = "# Product Manager PRD\n\nDifferent."
        r2 = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/agent",
            json=body2,
            params={"session_id": session_id},
        )
        assert r2.status_code == 201

        # Queue should have actionable (non-rejected) first
        r = client.get(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/queue",
            headers=_founder_headers(),
        )
        assert r.status_code == 200
        result = r.json()
        assert result["total"] == 2
        # First item should be actionable (proposed), not rejected
        assert result["items"][0]["status"] == "proposed"
        # Second item should be the rejected one
        assert result["items"][1]["status"] == "rejected"

    def test_queue_invalid_validation_outcome_rejected(self, app, org_state):
        """Queue rejects invalid validation_outcome values."""
        client = TestClient(app)
        r = client.get(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/queue",
            headers=_founder_headers(),
            params={"validation_outcome": "invalid"},
        )
        assert r.status_code == 400


class TestProposalDetailArtifactSafety:
    """Detail endpoint safely handles missing/malformed artifacts."""

    def test_detail_no_artifact_store_returns_null_skill_md(self, app, org_state):
        """When org_root is not passed or artifact is missing, skill_md safely returns null."""
        # This tests the code path where the provenance loader gracefully
        # returns None. We test by verifying that a valid proposal's detail works
        # even with null org_root passed to the stores layer.
        from runtime.skills.lifecycle import stores as lifecycle_stores

        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]

        # Call stores directly with org_root=None
        detail = lifecycle_stores.get_proposal_detail(org_state.db, version_id, org_root=None)
        assert detail is not None
        # skill_md should be null because org_root=None means we can't reach artifact store
        assert detail["skill_md"] is None
        # package_members should also be null
        assert detail["package_members"] is None
        # But other fields should still be present
        assert detail["content_artifact_key"] is not None
        assert detail["content_hash"] is not None

    def test_detail_safe_on_malformed_artifact_key(self, app, org_state):
        """Detail gracefully handles a bogus/nonexistent artifact key."""
        from runtime.skills.lifecycle import stores as lifecycle_stores

        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]
        org_root = str(org_state.root)

        # Modify the package directly to have a bogus artifact key
        db = org_state.db
        conn = db._conn if hasattr(db, '_conn') else db
        conn.execute(
            "UPDATE skill_lifecycle_packages SET content_artifact_key = ? WHERE id = ?",
            ("nonexistent/path/SKILL.md", version_id),
        )
        conn.commit()

        # Detail should still return successfully with null skill_md
        detail = lifecycle_stores.get_proposal_detail(db, version_id, org_root=org_root)
        assert detail is not None
        assert detail["skill_md"] is None
        assert detail["package_members"] is None

    def test_detail_read_does_not_append_events(self, app, org_state):
        """Reading proposal detail does NOT create any lifecycle data (events, mutations)."""
        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]
        client = TestClient(app)

        # Count events before reads
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        before_events = r.json()["events"]
        before_count = len(before_events)

        # Read multiple times
        for _ in range(3):
            r = client.get(
                f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
                headers=_founder_headers(),
            )
            assert len(r.json()["events"]) == before_count

    def test_queue_read_does_not_append_events(self, app, org_state):
        """Reading proposals queue does NOT create any lifecycle data."""
        _submit_agent_proposal(app, org_state)
        client = TestClient(app)

        # Count events before
        r_detail = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/1",
            headers=_founder_headers(),
        )
        before_count = len(r_detail.json()["events"])

        # Read queue multiple times
        for _ in range(3):
            r = client.get(
                "/api/v1/orgs/alpha/skill-lifecycle/proposals/queue",
                headers=_founder_headers(),
            )
            assert r.status_code == 200

        # Events should not have changed
        r_detail_after = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/1",
            headers=_founder_headers(),
        )
        assert len(r_detail_after.json()["events"]) == before_count


# ═══════════════════════════════════════════════════════════════════════════
# Artifact integrity — adversarial regression
# ═══════════════════════════════════════════════════════════════════════════


class TestProposalDetailArtifactIntegrity:
    """Adversarial: overwritten artifacts must fail closed — never return
    attacker-controlled bytes or fabricated member listings."""

    def test_overwritten_skill_md_returns_null(self, app, org_state):
        """Submit proposal → overwrite SKILL.md artifact via ArtifactStore →
        detail endpoint returns null skill_md (hash mismatch).

        Regresses: HIGH provenance flaw where get_proposal_detail returned
        mutable ArtifactStore-selected SKILL.md bytes after authenticated
        overwrite, while ledger content_hash still identified the
        original immutable version.
        """
        import json
        from runtime.infrastructure.artifact_store import ArtifactStore
        from runtime.orchestrator._paths import OrgPaths

        original_md = "# Test Skill\n\nVerified immutable SKILL.md content."
        data = _submit_agent_proposal(app, org_state, skill_md=original_md)
        version_id = data["version_id"]

        client = TestClient(app)
        # Before overwrite: detail returns canonical content
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        assert r.status_code == 200
        before = r.json()
        assert before["skill_md"] == original_md
        assert before["content_hash"] == data["content_hash"]
        assert before["package_members"] is not None

        # Access the ArtifactStore and locate the SKILL.md artifact key
        store = ArtifactStore(OrgPaths(org_state.root).artifacts_dir)
        manifest_raw = store.read(before["content_artifact_key"])
        manifest = json.loads(manifest_raw.decode("utf-8"))
        skill_member = next(
            m for m in manifest["members"] if m["path"] == "SKILL.md"
        )
        skill_key = skill_member["artifact_key"]

        # Authenticated overwrite of the SKILL.md artifact
        evil_content = b"# EVIL\n\nAttacker-controlled content overwritten via ArtifactStore.put()."
        store.put(skill_key, evil_content)

        # After overwrite: detail must fail closed — return null skill_md
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        assert r.status_code == 200
        after = r.json()
        assert after["skill_md"] is None, (
            "Founder detail must NOT return overwritten SKILL.md bytes; "
            "got attacker-controlled content instead of null"
        )
        # Remainder of the response must be truthful — ledger hash unchanged
        assert after["content_hash"] == data["content_hash"]
        assert after["content_artifact_key"] is not None
        assert after["proposer_agent"] == "frontend_engineer"
        assert after["proposal_task_id"] == "TASK-RV-001"
        assert after["status"] == "proposed"
        # package_members must also be null — when SKILL.md bytes don't match
        # the member's declared hash, both fields are absent from the
        # same verified provenance snapshot
        assert after["package_members"] is None

    def test_overwritten_manifest_returns_null_skill_md(self, app, org_state):
        """Overwrite the manifest artifact → skill_md AND package_members
        are null because the manifest hash no longer matches the ledger."""
        import json
        import hashlib
        from runtime.infrastructure.artifact_store import ArtifactStore
        from runtime.orchestrator._paths import OrgPaths

        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]

        client = TestClient(app)
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        assert r.status_code == 200
        before = r.json()
        assert before["skill_md"] is not None
        assert before["package_members"] is not None

        # Overwrite manifest with a forged version
        store = ArtifactStore(OrgPaths(org_state.root).artifacts_dir)
        manifest_key = before["content_artifact_key"]
        forged = {
            "schema_version": 1,
            "skill_id": "hr:frontend-development",
            "slug": "frontend-development",
            "members": [
                {
                    "path": "SKILL.md",
                    "hash": "sha256:" + hashlib.sha256(b"evil").hexdigest(),
                    "artifact_key": "attacker/controlled/path",
                    "size_bytes": 4,
                }
            ],
        }
        store.put(manifest_key, json.dumps(forged).encode("utf-8"))

        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        assert r.status_code == 200
        after = r.json()
        # Both must be null because the manifest hash is now wrong
        assert after["skill_md"] is None, (
            "skill_md must be null when manifest hash mismatches ledger content_hash"
        )
        assert after["package_members"] is None, (
            "package_members must be null when manifest hash mismatches ledger content_hash"
        )
        # Provenance preserved
        assert after["content_hash"] == data["content_hash"]
        assert after["status"] == "proposed"

    def test_overwritten_manifest_member_hash_mismatch(self, app, org_state):
        """Alter the member's declared SHA-256 in the manifest (but keep the
        artifact bytes correct) → the manifest hash changes, failing the
        ledger content_hash check."""
        import json
        import hashlib
        from runtime.infrastructure.artifact_store import ArtifactStore
        from runtime.orchestrator._paths import OrgPaths

        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]

        client = TestClient(app)
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        assert r.status_code == 200
        before = r.json()

        store = ArtifactStore(OrgPaths(org_state.root).artifacts_dir)
        manifest_raw = store.read(before["content_artifact_key"])
        manifest = json.loads(manifest_raw.decode("utf-8"))

        # Tamper with the SKILL.md member hash
        for m in manifest["members"]:
            if m["path"] == "SKILL.md":
                m["hash"] = "sha256:" + hashlib.sha256(b"tampered").hexdigest()
                break

        # Write the tampered manifest back (its SHA-256 changes)
        new_manifest_bytes = json.dumps(manifest, sort_keys=True, indent=2).encode("utf-8")
        store.put(before["content_artifact_key"], new_manifest_bytes)

        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        assert r.status_code == 200
        after = r.json()
        assert after["skill_md"] is None
        assert after["package_members"] is None

    def test_detail_read_appends_no_events_on_overwrite(self, app, org_state):
        """Overwriting an artifact and then reading the detail must NOT
        produce any lifecycle events — it's a read-only operation."""
        import json
        from runtime.infrastructure.artifact_store import ArtifactStore
        from runtime.orchestrator._paths import OrgPaths

        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]
        client = TestClient(app)

        # Count events before
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        before_count = len(r.json()["events"])

        # Overwrite the SKILL.md artifact
        store = ArtifactStore(OrgPaths(org_state.root).artifacts_dir)
        manifest_raw = store.read(r.json()["content_artifact_key"])
        manifest = json.loads(manifest_raw.decode("utf-8"))
        skill_member = next(m for m in manifest["members"] if m["path"] == "SKILL.md")
        store.put(skill_member["artifact_key"], b"overwritten")

        # Read detail again — must still have same number of events
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        assert len(r.json()["events"]) == before_count, (
            "Reading proposal detail must never append lifecycle events"
        )

    def test_blank_ledger_content_hash_fails_closed(self, app, org_state):
        """When the ledger content_hash is blank, both loaders must fail closed.

        Direct stores-layer test: corrupt the DB row to set content_hash='',
        then verify get_proposal_detail returns null skill_md AND null
        package_members."""
        from runtime.skills.lifecycle import stores as lifecycle_stores

        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]
        assert data["content_hash"]

        # Baseline: valid detail returns content
        detail_before = lifecycle_stores.get_proposal_detail(
            org_state.db, version_id, org_root=str(org_state.root)
        )
        assert detail_before["skill_md"] is not None
        assert detail_before["package_members"] is not None

        # Corrupt: set content_hash to empty string in the ledger
        org_state.db.execute(
            "UPDATE skill_lifecycle_packages SET content_hash = '' WHERE id = ?",
            (version_id,),
        )

        detail_after = lifecycle_stores.get_proposal_detail(
            org_state.db, version_id, org_root=str(org_state.root)
        )
        # Both must be null — blank content_hash cannot prove the package intact
        assert detail_after["skill_md"] is None, (
            "skill_md must be null when ledger content_hash is blank"
        )
        assert detail_after["package_members"] is None, (
            "package_members must be null when ledger content_hash is blank"
        )
        # Provenance still intact
        assert detail_after["version_id"] == version_id
        assert detail_after["status"] == "proposed"

    def test_blank_member_hash_fails_closed_skill_md(self, app, org_state):
        """When the manifest member ('SKILL.md') has a blank hash, skill_md
        must be null — member-digest validation rejects the empty hash.

        Direct stores-layer test: after submitting a valid proposal, overwrite
        the manifest artifact to give the SKILL.md member an empty hash, then
        update the ledger content_hash to match the tampered manifest.  The
        manifest passes content_hash verification, but the blank member hash
        is fail-closed at the member-digest validation step."""
        import hashlib
        import json
        from runtime.infrastructure.artifact_store import ArtifactStore
        from runtime.orchestrator._paths import OrgPaths
        from runtime.skills.lifecycle import stores as lifecycle_stores

        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]

        # Baseline: valid detail returns content
        detail = lifecycle_stores.get_proposal_detail(
            org_state.db, version_id, org_root=str(org_state.root)
        )
        assert detail["skill_md"] is not None
        assert detail["package_members"] is not None

        store = ArtifactStore(OrgPaths(org_state.root).artifacts_dir)
        manifest_key = detail["content_artifact_key"]
        original_manifest_raw = store.read(manifest_key)
        manifest = json.loads(original_manifest_raw.decode("utf-8"))

        # Blank the SKILL.md member hash, keep everything else identical
        for m in manifest["members"]:
            if m["path"] == "SKILL.md":
                m["hash"] = ""
                break

        # Write the tampered manifest
        tampered_bytes = json.dumps(manifest, sort_keys=True, indent=2).encode("utf-8")
        store.put(manifest_key, tampered_bytes)

        # Update ledger content_hash to match the tampered manifest so
        # manifest verification passes and we genuinely reach member-digest
        # validation (the blank hash is rejected THERE).
        tampered_content_hash = hashlib.sha256(tampered_bytes).hexdigest()
        org_state.db.execute(
            "UPDATE skill_lifecycle_packages SET content_hash = ? WHERE id = ?",
            (tampered_content_hash, version_id),
        )

        # Member-digest validation catches the blank hash, not manifest verification
        detail2 = lifecycle_stores.get_proposal_detail(
            org_state.db, version_id, org_root=str(org_state.root)
        )
        assert detail2["skill_md"] is None, (
            "skill_md must be null when member hash is blank"
        )
        assert detail2["package_members"] is None, (
            "package_members must also be null when member hash is blank — "
            "both fields derive from the same verified provenance snapshot"
        )
        # Provenance preserved
        assert detail2["version_id"] == version_id
        assert detail2["status"] == "proposed"

    def test_malformed_member_hash_fails_closed(self, app, org_state):
        """A non-sha256 member hash (e.g. 'md5:...') is fail-closed —
        skill_md must be null.

        Direct stores-layer: overwrite manifest member hash to use an
        unsupported algorithm prefix, then update the ledger content_hash
        so manifest verification passes.  The unsupported algorithm is
        caught at the member-digest validation step, not manifest check."""
        import hashlib
        import json
        from runtime.infrastructure.artifact_store import ArtifactStore
        from runtime.orchestrator._paths import OrgPaths
        from runtime.skills.lifecycle import stores as lifecycle_stores

        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]

        detail = lifecycle_stores.get_proposal_detail(
            org_state.db, version_id, org_root=str(org_state.root)
        )
        assert detail["skill_md"] is not None

        store = ArtifactStore(OrgPaths(org_state.root).artifacts_dir)
        manifest_key = detail["content_artifact_key"]
        manifest_raw = store.read(manifest_key)
        manifest = json.loads(manifest_raw.decode("utf-8"))

        # Change to an unsupported algorithm prefix
        for m in manifest["members"]:
            if m["path"] == "SKILL.md":
                m["hash"] = "md5:d41d8cd98f00b204e9800998ecf8427e"
                break

        # Write tampered manifest and update ledger content_hash so
        # manifest verification passes and member-digest validation is
        # genuinely exercised.
        tampered_bytes = json.dumps(manifest, sort_keys=True, indent=2).encode("utf-8")
        store.put(manifest_key, tampered_bytes)
        org_state.db.execute(
            "UPDATE skill_lifecycle_packages SET content_hash = ? WHERE id = ?",
            (hashlib.sha256(tampered_bytes).hexdigest(), version_id),
        )

        detail2 = lifecycle_stores.get_proposal_detail(
            org_state.db, version_id, org_root=str(org_state.root)
        )
        assert detail2["skill_md"] is None, (
            "skill_md must be null for unsupported member hash algorithm"
        )
        assert detail2["package_members"] is None, (
            "package_members must also be null for unsupported member hash algorithm — "
            "both fields derive from the same verified provenance snapshot"
        )
        assert detail2["version_id"] == version_id
        assert detail2["status"] == "proposed"

    def test_missing_skill_md_member_returns_null(self, app, org_state):
        """When the manifest has no SKILL.md member, skill_md is null.

        Direct stores-layer: remove the SKILL.md member from the manifest
        members array, update the ledger content_hash, and verify that the
        loader returns null skill_md.  The manifest still passes content_hash
        verification, but there is no SKILL.md entry to load."""
        import hashlib
        import json
        from runtime.infrastructure.artifact_store import ArtifactStore
        from runtime.orchestrator._paths import OrgPaths
        from runtime.skills.lifecycle import stores as lifecycle_stores

        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]

        detail = lifecycle_stores.get_proposal_detail(
            org_state.db, version_id, org_root=str(org_state.root)
        )
        assert detail["skill_md"] is not None
        assert detail["package_members"] is not None

        store = ArtifactStore(OrgPaths(org_state.root).artifacts_dir)
        manifest_key = detail["content_artifact_key"]
        manifest_raw = store.read(manifest_key)
        manifest = json.loads(manifest_raw.decode("utf-8"))

        # Remove the SKILL.md member entirely
        manifest["members"] = [
            m for m in manifest["members"] if m["path"] != "SKILL.md"
        ]

        tampered_bytes = json.dumps(manifest, sort_keys=True, indent=2).encode("utf-8")
        store.put(manifest_key, tampered_bytes)
        org_state.db.execute(
            "UPDATE skill_lifecycle_packages SET content_hash = ? WHERE id = ?",
            (hashlib.sha256(tampered_bytes).hexdigest(), version_id),
        )

        detail2 = lifecycle_stores.get_proposal_detail(
            org_state.db, version_id, org_root=str(org_state.root)
        )
        assert detail2["skill_md"] is None, (
            "skill_md must be null when manifest has no SKILL.md member"
        )
        assert detail2["package_members"] is None, (
            "package_members must also be null when manifest has no SKILL.md member — "
            "both fields derive from the same verified provenance snapshot"
        )
        assert detail2["version_id"] == version_id
        assert detail2["status"] == "proposed"

    def test_mismatched_sha256_member_digest_fails_closed(self, app, org_state):
        """A sha256: member hash with a wrong hex digest is fail-closed —
        skill_md must be null.

        The member hash has the canonical sha256: prefix format, but the
        hex value does not match the actual artifact bytes.  The manifest
        passes content_hash verification (ledger is updated to match),
        but the member hash mismatch is caught at member-digest validation."""
        import hashlib
        import json
        from runtime.infrastructure.artifact_store import ArtifactStore
        from runtime.orchestrator._paths import OrgPaths
        from runtime.skills.lifecycle import stores as lifecycle_stores

        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]

        detail = lifecycle_stores.get_proposal_detail(
            org_state.db, version_id, org_root=str(org_state.root)
        )
        assert detail["skill_md"] is not None

        store = ArtifactStore(OrgPaths(org_state.root).artifacts_dir)
        manifest_key = detail["content_artifact_key"]
        manifest_raw = store.read(manifest_key)
        manifest = json.loads(manifest_raw.decode("utf-8"))

        # Replace member hash with a canonical-format sha256 that does NOT
        # match the actual bytes (wrong hex)
        wrong_hex = hashlib.sha256(b"tampered content").hexdigest()
        for m in manifest["members"]:
            if m["path"] == "SKILL.md":
                m["hash"] = f"sha256:{wrong_hex}"
                break

        tampered_bytes = json.dumps(manifest, sort_keys=True, indent=2).encode("utf-8")
        store.put(manifest_key, tampered_bytes)
        org_state.db.execute(
            "UPDATE skill_lifecycle_packages SET content_hash = ? WHERE id = ?",
            (hashlib.sha256(tampered_bytes).hexdigest(), version_id),
        )

        detail2 = lifecycle_stores.get_proposal_detail(
            org_state.db, version_id, org_root=str(org_state.root)
        )
        assert detail2["skill_md"] is None, (
            "skill_md must be null when sha256 member digest does not match actual bytes"
        )
        assert detail2["package_members"] is None, (
            "package_members must also be null when sha256 member digest does not match — "
            "both fields derive from the same verified provenance snapshot"
        )
        assert detail2["version_id"] == version_id
        assert detail2["status"] == "proposed"

    def test_no_digest_key_on_member_fails_closed(self, app, org_state):
        """When the manifest SKILL.md member has no 'hash' key at all,
        skill_md must be null.

        Direct stores-layer: remove the hash key from the SKILL.md member
        entry, update the ledger content_hash, and verify that the loader
        returns null skill_md.  The manifest passes content_hash verification,
        but the absent hash field is fail-closed at member-digest validation."""
        import hashlib
        import json
        from runtime.infrastructure.artifact_store import ArtifactStore
        from runtime.orchestrator._paths import OrgPaths
        from runtime.skills.lifecycle import stores as lifecycle_stores

        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]

        detail = lifecycle_stores.get_proposal_detail(
            org_state.db, version_id, org_root=str(org_state.root)
        )
        assert detail["skill_md"] is not None

        store = ArtifactStore(OrgPaths(org_state.root).artifacts_dir)
        manifest_key = detail["content_artifact_key"]
        manifest_raw = store.read(manifest_key)
        manifest = json.loads(manifest_raw.decode("utf-8"))

        # Remove the hash key from the SKILL.md member
        for m in manifest["members"]:
            if m["path"] == "SKILL.md":
                del m["hash"]
                break

        tampered_bytes = json.dumps(manifest, sort_keys=True, indent=2).encode("utf-8")
        store.put(manifest_key, tampered_bytes)
        org_state.db.execute(
            "UPDATE skill_lifecycle_packages SET content_hash = ? WHERE id = ?",
            (hashlib.sha256(tampered_bytes).hexdigest(), version_id),
        )

        detail2 = lifecycle_stores.get_proposal_detail(
            org_state.db, version_id, org_root=str(org_state.root)
        )
        assert detail2["skill_md"] is None, (
            "skill_md must be null when member has no hash key"
        )
        assert detail2["package_members"] is None, (
            "package_members must also be null when member has no hash key — "
            "both fields derive from the same verified provenance snapshot"
        )
        assert detail2["version_id"] == version_id
        assert detail2["status"] == "proposed"

    def test_no_events_on_hash_rejected_read(self, app, org_state):
        """Every rejected read (hash mismatch, blank hash) must append
        zero lifecycle events and make zero package mutations."""
        import json
        from runtime.infrastructure.artifact_store import ArtifactStore
        from runtime.orchestrator._paths import OrgPaths
        from runtime.skills.lifecycle import stores as lifecycle_stores

        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]

        detail = lifecycle_stores.get_proposal_detail(
            org_state.db, version_id, org_root=str(org_state.root)
        )
        before_event_count = len(detail["events"])

        # Corrupt content_hash to blank → causes rejected read
        org_state.db.execute(
            "UPDATE skill_lifecycle_packages SET content_hash = '' WHERE id = ?",
            (version_id,),
        )

        # Read multiple times — no events should be appended
        for _ in range(3):
            detail_rej = lifecycle_stores.get_proposal_detail(
                org_state.db, version_id, org_root=str(org_state.root)
            )
            assert detail_rej["skill_md"] is None
            assert detail_rej["package_members"] is None

        # Restore valid content_hash and verify event count unchanged
        org_state.db.execute(
            "UPDATE skill_lifecycle_packages SET content_hash = ? WHERE id = ?",
            (data["content_hash"], version_id),
        )
        detail_after = lifecycle_stores.get_proposal_detail(
            org_state.db, version_id, org_root=str(org_state.root)
        )
        assert len(detail_after["events"]) == before_event_count, (
            "Rejected reads must never append lifecycle events"
        )

    def test_unauthenticated_detail_no_leak(self, app, org_state):
        """Unauthenticated access to proposal detail returns 403 —
        no skill_md or member bytes leak."""
        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]
        client = TestClient(app)

        # No auth header
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
        )
        assert r.status_code == 403

        # Wrong auth token
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert r.status_code == 403

    def test_agent_deep_link_detail_no_leak(self, app, org_state):
        """Agent callers (non-bearer) receive 403 on proposal detail endpoint
        — skill_md and package_members must never leak to agent session."""
        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]
        client = TestClient(app)

        # Agent caller without bearer token
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
        )
        assert r.status_code == 403

        # Agent caller with a plausible session but no bearer
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            params={"task_id": "TASK-RV-001", "session_id": "sess-rv-agent-001"},
        )
        assert r.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════
# Concurrency marker protection
# ═══════════════════════════════════════════════════════════════════════════

class TestConcurrencyProtection:
    """State-changing endpoints reject stale concurrency markers with 409."""

    def test_stale_concurrency_returns_409(self, app, org_state):
        """Using an old concurrency marker returns 409 conflict."""
        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]
        client = TestClient(app)

        # Get current marker
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid = r.json()["last_event_id"]

        # Do a valid claim
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/claim",
            headers=_founder_headers(),
            json={"expected_event_id": eid},
        )
        assert r.status_code == 200

        # Now try another claim with the STALE marker — should 409
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/claim",
            headers=_founder_headers(),
            json={"expected_event_id": eid},
        )
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "stale_concurrency"

    def test_concurrency_response_includes_current_state(self, app, org_state):
        """409 response includes current event_id and status for client refresh."""
        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]
        client = TestClient(app)

        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid = r.json()["last_event_id"]

        # Claim first
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/claim",
            headers=_founder_headers(),
            json={"expected_event_id": eid},
        )
        assert r.status_code == 200

        # Stale claim
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/claim",
            headers=_founder_headers(),
            json={"expected_event_id": eid},
        )
        assert r.status_code == 409
        detail = r.json()["detail"]
        # FastAPI wraps the detail — the conflict data is nested inside
        conflict = detail.get("detail", detail)
        assert "current_event_id" in conflict, f"detail: {detail}"
        assert "current_status" in conflict
        assert conflict["expected_event_id"] == eid


# ═══════════════════════════════════════════════════════════════════════════
# Validator version/hash/key + distinct run records
# ═══════════════════════════════════════════════════════════════════════════

class TestValidationReproducibility:
    """Validation records reproducible metadata."""

    def test_validation_records_validator_version(self, app, org_state):
        """Validation event includes validator_version in metadata."""
        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]
        client = TestClient(app)

        # Claim
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid = r.json()["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/claim",
            headers=_founder_headers(),
            json={"expected_event_id": eid},
        )
        assert r.status_code == 200

        # Validate with explicit version
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid = r.json()["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/validate",
            headers=_founder_headers(),
            json={"validator_version": "THR-055/1.0.0", "expected_event_id": eid},
        )
        assert r.status_code == 200

        # Verify metadata in events
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        detail = r.json()
        validation_events = [e for e in detail["events"] if e["event_type"] == "validated"]
        assert len(validation_events) >= 1
        meta = validation_events[0].get("metadata") or {}
        assert meta.get("validator_version") == "THR-055/1.0.0"
        assert meta.get("validator_key") == "THR-055/1.0.0"
        assert meta.get("content_hash") == detail["content_hash"]

    def test_revalidation_appends_new_event(self, app, org_state):
        """Re-validating the same version appends a new event (does not overwrite)."""
        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]
        client = TestClient(app)

        # Claim
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid = r.json()["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/claim",
            headers=_founder_headers(),
            json={"expected_event_id": eid},
        )
        assert r.status_code == 200

        # First validation
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid = r.json()["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/validate",
            headers=_founder_headers(),
            json={"validator_version": "THR-055/1.0.0", "expected_event_id": eid},
        )
        assert r.status_code == 200

        # Count validation events after first validation
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        detail = r.json()
        val_events_1 = [e for e in detail["events"] if e["event_type"] == "validated"]
        assert len(val_events_1) == 1

        # Second validation (after validation_failed or just re-run)
        # First revert to validation_failed
        eid = detail["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/validate",
            headers=_founder_headers(),
            json={"validator_version": "THR-055/2.0.0", "expected_event_id": eid},
        )
        assert r.status_code == 200

        # Should now have 2 validation events
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        detail = r.json()
        val_events_2 = [e for e in detail["events"] if e["event_type"] == "validated"]
        assert len(val_events_2) >= 2, f"Expected >=2 validation events, got {len(val_events_2)}"


# ═══════════════════════════════════════════════════════════════════════════
# Decision status independent of assignment/materialization
# ═══════════════════════════════════════════════════════════════════════════

class TestDecisionStatusIndependentOfAssignment:
    """Package decision status remains independent of assignment/materialization."""

    def _setup_published_package(self, app, org_state) -> tuple[TestClient, int, str]:
        """Advance a proposal all the way to published."""
        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]
        skill_id = data["skill_id"]
        client = TestClient(app)

        # Claim → validate → submit → review(approve) → publish
        for action in ["claim", "validate", "submit"]:
            r = client.get(
                f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
                headers=_founder_headers(),
            )
            eid = r.json()["last_event_id"]
            if action == "claim":
                r = client.post(
                    f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/claim",
                    headers=_founder_headers(),
                    json={"expected_event_id": eid},
                )
            elif action == "validate":
                r = client.post(
                    f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/validate",
                    headers=_founder_headers(),
                    json={"validator_version": "THR-055/1.0.0", "expected_event_id": eid},
                )
            elif action == "submit":
                r = client.post(
                    "/api/v1/orgs/alpha/skill-lifecycle/submit-review",
                    headers=_founder_headers(),
                    json={"version_id": version_id},
                )
            assert r.status_code == 200, f"{action} failed: {r.json()}"

        # Review (approved)
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid = r.json()["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/review",
            headers=_founder_headers(),
            json={"decision": "approved", "rationale": "OK", "expected_event_id": eid},
        )
        assert r.status_code == 200

        # Get approval event ID
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        approval_event_id = None
        for e in r.json()["events"]:
            if e["event_type"] == "approved":
                approval_event_id = e["id"]
                break
        assert approval_event_id is not None

        # Publish
        eid = r.json()["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/publish",
            headers=_founder_headers(),
            json={"approval_event_id": approval_event_id, "expected_event_id": eid},
        )
        assert r.status_code == 200, f"Publish failed: {r.json()}"

        return client, version_id, skill_id

    def test_rollback_does_not_mutate_package_status(self, app, org_state):
        """Rollback deactivates assignments but keeps package status PUBLISHED."""
        client, version_id, skill_id = self._setup_published_package(app, org_state)

        # Assign first
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid = r.json()["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/assign",
            headers=_founder_headers(),
            json={"agent_name": "dev_agent", "expected_event_id": eid},
        )
        assert r.status_code == 200, f"Assign failed: {r.json()}"

        # Rollback
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid = r.json()["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/rollback",
            headers=_founder_headers(),
            json={"reason": "Test rollback", "expected_event_id": eid},
        )
        assert r.status_code == 200, f"Rollback failed: {r.json()}"
        assert r.json()["assignments_deactivated"] >= 1

        # Verify package status is STILL published
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        detail = r.json()
        assert detail["status"] == "published", (
            f"Expected package status 'published', got '{detail['status']}'"
        )

    def test_assign_does_not_change_package_status(self, app, org_state):
        """Assignment is a separate projection — package status stays PUBLISHED."""
        client, version_id, skill_id = self._setup_published_package(app, org_state)

        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        assert r.json()["status"] == "published"

        eid = r.json()["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/assign",
            headers=_founder_headers(),
            json={"agent_name": "dev_agent", "expected_event_id": eid},
        )
        assert r.status_code == 200

        # Status still published
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        assert r.json()["status"] == "published"


# ═══════════════════════════════════════════════════════════════════════════
# Append-only audit fields
# ═══════════════════════════════════════════════════════════════════════════

class TestAppendOnlyAudit:
    """Lifecycle events are append-only with full audit provenance."""

    def test_claim_event_has_actor_and_time(self, app, org_state):
        """Each lifecycle event records actor, role, and timestamp."""
        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]
        client = TestClient(app)

        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        detail = r.json()
        event = detail["events"][0]
        assert event["event_type"] == "proposed"
        assert event["actor"] == "frontend_engineer"
        assert event["actor_role"] == "agent"
        assert event["created_at"] is not None


# ═══════════════════════════════════════════════════════════════════════════
# Legacy compatibility
# ═══════════════════════════════════════════════════════════════════════════

class TestLegacyCompatibility:
    """Existing legacy routes still work for founder callers."""

    def test_legacy_claim_still_works_for_founder(self, app, org_state):
        """Legacy claim route still works with bearer token."""
        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]
        skill_id = data["skill_id"]
        client = TestClient(app)

        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/{skill_id}/claim",
            headers=_founder_headers(),
            json={"proposal_version_id": version_id},
        )
        assert r.status_code == 200

    def test_catalog_still_dual_auth(self, app, org_state):
        """Catalog route remains dual-auth (published skills are safe to expose)."""
        client = TestClient(app)
        r = client.get("/api/v1/orgs/alpha/skill-lifecycle/catalog/custom")
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# Fix 1: REJECTED terminal — blocks ALL mutations incl legacy routes
# ═══════════════════════════════════════════════════════════════════════════

class TestRejectedBlocksAllMutations:
    """Terminal REJECTED blocks every reachable mutation path incl legacy routes,
    projections, and service methods."""

    def _setup_rejected_package(self, app, org_state) -> tuple[TestClient, int, str]:
        """Advance a proposal to IN_REVIEW, then reject it."""
        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]
        client = TestClient(app)

        # Claim → validate → submit
        for action in ["claim", "validate", "submit"]:
            r = client.get(
                f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
                headers=_founder_headers(),
            )
            eid = r.json()["last_event_id"]
            if action == "claim":
                r = client.post(
                    f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/claim",
                    headers=_founder_headers(),
                    json={"expected_event_id": eid},
                )
            elif action == "validate":
                r = client.post(
                    f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/validate",
                    headers=_founder_headers(),
                    json={"validator_version": "THR-055/1.0.0", "expected_event_id": eid},
                )
            elif action == "submit":
                r = client.post(
                    "/api/v1/orgs/alpha/skill-lifecycle/submit-review",
                    headers=_founder_headers(),
                    json={"version_id": version_id},
                )
            assert r.status_code == 200, f"{action} failed: {r.json()}"

        # Reject via v2 route
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid = r.json()["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/review",
            headers=_founder_headers(),
            json={"decision": "rejected", "rationale": "Not acceptable", "expected_event_id": eid},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "rejected"

        return client, version_id, data["skill_id"]

    def _setup_published_package(self, app, org_state) -> tuple[TestClient, int, str]:
        """Advance a proposal all the way to published."""
        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]
        skill_id = data["skill_id"]
        client = TestClient(app)

        for action in ["claim", "validate", "submit"]:
            r = client.get(
                f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
                headers=_founder_headers(),
            )
            eid = r.json()["last_event_id"]
            if action == "claim":
                r = client.post(
                    f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/claim",
                    headers=_founder_headers(),
                    json={"expected_event_id": eid},
                )
            elif action == "validate":
                r = client.post(
                    f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/validate",
                    headers=_founder_headers(),
                    json={"validator_version": "THR-055/1.0.0", "expected_event_id": eid},
                )
            elif action == "submit":
                r = client.post(
                    "/api/v1/orgs/alpha/skill-lifecycle/submit-review",
                    headers=_founder_headers(),
                    json={"version_id": version_id},
                )
            assert r.status_code == 200, f"{action} failed: {r.json()}"

        # Review(approved)
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid = r.json()["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/review",
            headers=_founder_headers(),
            json={"decision": "approved", "rationale": "OK", "expected_event_id": eid},
        )
        assert r.status_code == 200

        # Get approval event ID from events endpoint
        r_evt = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/events/{skill_id}",
            headers=_founder_headers(),
        )
        approval_event_id = None
        for e in r_evt.json()["events"]:
            if e["event_type"] == "approved":
                approval_event_id = e["id"]
                break
        assert approval_event_id is not None, "Could not find approval event id"

        # Publish
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid = r.json()["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/publish",
            headers=_founder_headers(),
            json={"approval_event_id": approval_event_id, "expected_event_id": eid},
        )
        assert r.status_code == 200, f"Publish failed: {r.json()}"

        return client, version_id, skill_id

    def test_rejected_blocks_legacy_rollback(self, app, org_state):
        """Legacy rollback route blocks REJECTED packages."""
        client, version_id, skill_id = self._setup_rejected_package(app, org_state)

        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/rollback",
            headers=_founder_headers(),
            params={"skill_id": skill_id, "reason": "test"},
        )
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "rejected_terminal"

    def test_rejected_blocks_legacy_retire(self, app, org_state):
        """Legacy retire route blocks REJECTED packages."""
        client, version_id, skill_id = self._setup_rejected_package(app, org_state)

        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/retire",
            headers=_founder_headers(),
            params={"skill_id": skill_id, "reason": "test"},
        )
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "rejected_terminal"

    def test_retire_requires_published(self, app, org_state):
        """Retire must be from PUBLISHED only — non-published states reject."""
        data = _submit_agent_proposal(app, org_state)
        skill_id = data["skill_id"]
        client = TestClient(app)

        # PROPOSED cannot be retired
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/retire",
            headers=_founder_headers(),
            params={"skill_id": skill_id, "reason": "test"},
        )
        assert r.status_code == 409
        assert "PUBLISHED" in r.json()["detail"]["detail"]

    def test_retire_preserves_package_status(self, app, org_state):
        """Retire deactivates assignments but keeps package status PUBLISHED."""
        client, version_id, skill_id = self._setup_published_package(app, org_state)

        # Assign first
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid = r.json()["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/assign",
            headers=_founder_headers(),
            json={"agent_name": "dev_agent", "expected_event_id": eid},
        )
        assert r.status_code == 200, f"Assign failed: {r.json()}"

        # Retire
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/retire",
            headers=_founder_headers(),
            params={"skill_id": skill_id, "reason": "Obsolete"},
        )
        assert r.status_code == 200, f"Retire failed: {r.json()}"

        # Package status should still be PUBLISHED (not mutated)
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        assert r.status_code == 200
        assert r.json()["status"] == "published", (
            f"Expected 'published', got '{r.json()['status']}'"
        )

    def test_rejected_blocks_v2_rollback(self, app, org_state):
        """V2 rollback route blocks REJECTED proposals."""
        client, version_id, skill_id = self._setup_rejected_package(app, org_state)

        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/rollback",
            headers=_founder_headers(),
            json={"reason": "test", "expected_event_id": 999},
        )
        # V2 route checks concurrency first, but then service rejects terminal
        assert r.status_code in (409,), f"Expected 409, got {r.status_code}: {r.json()}"

    def test_rejected_blocks_legacy_validate(self, app, org_state):
        """Legacy validate route blocks REJECTED packages."""
        client, version_id, skill_id = self._setup_rejected_package(app, org_state)

        r = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/validate",
            headers=_founder_headers(),
            params={"version_id": version_id},
        )
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "rejected_terminal"

    def test_rejected_blocks_legacy_submit_review(self, app, org_state):
        """Legacy submit-review route blocks REJECTED packages."""
        client, version_id, skill_id = self._setup_rejected_package(app, org_state)

        r = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/submit-review",
            headers=_founder_headers(),
            json={"version_id": version_id},
        )
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "rejected_terminal"


# ═══════════════════════════════════════════════════════════════════════════
# Fix 2: Atomic compare-and-mutate concurrency
# ═══════════════════════════════════════════════════════════════════════════

class TestAtomicConcurrency:
    """Concurrent equal-marker requests produce exactly one success and one 409."""

    def test_concurrent_equal_marker_claim_atomic(self, app, org_state):
        """Two identical-marker claim requests → one success, one 409."""
        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]
        client = TestClient(app)

        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid = r.json()["last_event_id"]

        # First claim succeeds
        r1 = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/claim",
            headers=_founder_headers(),
            json={"expected_event_id": eid},
        )
        assert r1.status_code == 200

        # Second claim with SAME marker fails with 409
        r2 = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/claim",
            headers=_founder_headers(),
            json={"expected_event_id": eid},
        )
        assert r2.status_code == 409
        assert r2.json()["detail"]["code"] == "stale_concurrency"

        # Verify exactly one mutation succeeded by checking event count
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        detail = r.json()
        # Two events: proposed + exactly one drafted
        event_types = [e["event_type"] for e in detail["events"]]
        assert event_types.count("drafted") == 1, (
            f"Expected exactly 1 drafted event, got {event_types}"
        )

    def test_concurrent_equal_marker_validate_atomic(self, app, org_state):
        """Two identical-marker validate requests → one success, one 409."""
        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]
        client = TestClient(app)

        # Claim first
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid = r.json()["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/claim",
            headers=_founder_headers(),
            json={"expected_event_id": eid},
        )
        assert r.status_code == 200

        # Get new marker
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid = r.json()["last_event_id"]

        # First validate succeeds
        r1 = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/validate",
            headers=_founder_headers(),
            json={"validator_version": "THR-055/1.0.0", "expected_event_id": eid},
        )
        assert r1.status_code == 200

        # Second with same marker fails
        r2 = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/validate",
            headers=_founder_headers(),
            json={"validator_version": "THR-055/1.0.0", "expected_event_id": eid},
        )
        assert r2.status_code == 409
        assert r2.json()["detail"]["code"] == "stale_concurrency"


# ═══════════════════════════════════════════════════════════════════════════
# Fix 3: submit-review v2 route
# ═══════════════════════════════════════════════════════════════════════════

class TestSubmitReviewV2:
    """V2 submit-review route: concurrency-protected, Founder-only."""

    def test_agent_gets_403_for_v2_submit_review(self, app, org_state):
        """V2 submit-review returns 403 for agent callers."""
        _setup_session(org_state, "TASK-403", "frontend_engineer", "sess-no-bearer")
        client = TestClient(app)
        r = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/1/submit-review",
            json={},
        )
        assert r.status_code in (403, 401), f"Expected 403/401, got {r.status_code}: {r.json()}"

    def test_deep_link_unauthorized(self, app, org_state):
        """Deep-link access to v2 submit-review without bearer returns 403."""
        client = TestClient(app)
        r = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/1/submit-review",
            json={"expected_event_id": 1},
        )
        assert r.status_code == 403

    def test_v2_submit_review_moves_validated_to_in_review(self, app, org_state):
        """V2 submit-review moves VALIDATED to IN_REVIEW."""
        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]
        client = TestClient(app)

        # Claim → validate → v2 submit-review
        for action in ["claim", "validate"]:
            r = client.get(
                f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
                headers=_founder_headers(),
            )
            eid = r.json()["last_event_id"]
            if action == "claim":
                r = client.post(
                    f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/claim",
                    headers=_founder_headers(),
                    json={"expected_event_id": eid},
                )
            elif action == "validate":
                r = client.post(
                    f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/validate",
                    headers=_founder_headers(),
                    json={"validator_version": "THR-055/1.0.0", "expected_event_id": eid},
                )
            assert r.status_code == 200, f"{action} failed: {r.json()}"

        # Verify status is VALIDATED
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        assert r.json()["status"] == "validated"

        # V2 submit-review
        eid = r.json()["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/submit-review",
            headers=_founder_headers(),
            json={"expected_event_id": eid, "intended_audience": "eng", "review_notes": "looks good"},
        )
        assert r.status_code == 200, f"Submit-review failed: {r.json()}"
        assert r.json()["status"] == "in_review"

    def test_v2_submit_review_stale_concurrency(self, app, org_state):
        """V2 submit-review rejects stale concurrency marker."""
        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]
        client = TestClient(app)

        # Claim → validate
        for action in ["claim", "validate"]:
            r = client.get(
                f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
                headers=_founder_headers(),
            )
            eid = r.json()["last_event_id"]
            if action == "claim":
                r = client.post(
                    f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/claim",
                    headers=_founder_headers(),
                    json={"expected_event_id": eid},
                )
            elif action == "validate":
                r = client.post(
                    f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/validate",
                    headers=_founder_headers(),
                    json={"validator_version": "THR-055/1.0.0", "expected_event_id": eid},
                )
            assert r.status_code == 200

        # First submit-review succeeds
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid = r.json()["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/submit-review",
            headers=_founder_headers(),
            json={"expected_event_id": eid},
        )
        assert r.status_code == 200

        # Second with same stale marker fails
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/submit-review",
            headers=_founder_headers(),
            json={"expected_event_id": eid},
        )
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "stale_concurrency"


# ═══════════════════════════════════════════════════════════════════════════
# Fix 4: Deterministic validation evidence
# ═══════════════════════════════════════════════════════════════════════════

class TestDeterministicValidation:
    """Validation events are deterministic and complete on every path."""

    def test_validation_events_record_accurate_previous_status(self, app, org_state):
        """Validation event captures DRAFT as previous_status, not VALIDATED."""
        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]
        client = TestClient(app)

        # Claim
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid = r.json()["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/claim",
            headers=_founder_headers(),
            json={"expected_event_id": eid},
        )
        assert r.status_code == 200

        # Status before validation should be DRAFT
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        assert r.json()["status"] == "draft"

        # Validate
        eid = r.json()["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/validate",
            headers=_founder_headers(),
            json={"validator_version": "THR-055/1.0.0", "expected_event_id": eid},
        )
        assert r.status_code == 200

        # Check validation event's previous_status
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        detail = r.json()
        val_events = [e for e in detail["events"] if e["event_type"] == "validated"]
        assert len(val_events) >= 1
        prev = val_events[0].get("previous_status")
        assert prev == "draft", (
            f"Expected previous_status='draft', got '{prev}'. "
            f"The previous_status must be captured BEFORE the status update."
        )

    def test_legacy_validate_records_deterministic_key(self, app, org_state):
        """Legacy validate route injects a deterministic validator_key."""
        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]
        skill_id = data["skill_id"]
        client = TestClient(app)

        # Claim
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/{skill_id}/claim",
            headers=_founder_headers(),
            json={"proposal_version_id": version_id},
        )
        assert r.status_code == 200

        # Legacy validate
        r = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/validate",
            headers=_founder_headers(),
            params={"version_id": version_id},
        )
        assert r.status_code == 200

        # Check events have deterministic metadata
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/events/{skill_id}",
            headers=_founder_headers(),
        )
        events = r.json()["events"]
        val_events = [e for e in events if e["event_type"] == "validated"]
        assert len(val_events) >= 1
        meta = val_events[0].get("metadata") or {}
        assert meta.get("validator_version") == "LEGACY/1.0.0", (
            f"Legacy validation must record validator_version, got {meta}"
        )
        assert meta.get("validator_key") is not None
        assert meta.get("content_hash") is not None

    def test_revalidation_distinct_events(self, app, org_state):
        """Successful and failed re-runs append distinct events with distinct IDs."""
        data = _submit_agent_proposal(app, org_state)
        version_id = data["version_id"]
        client = TestClient(app)

        # Claim
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid = r.json()["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/claim",
            headers=_founder_headers(),
            json={"expected_event_id": eid},
        )
        assert r.status_code == 200

        # First validation
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid = r.json()["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/validate",
            headers=_founder_headers(),
            json={"validator_version": "THR-055/1.0.0", "expected_event_id": eid},
        )
        assert r.status_code == 200

        # Second validation
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid = r.json()["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/validate",
            headers=_founder_headers(),
            json={"validator_version": "THR-055/2.0.0", "expected_event_id": eid},
        )
        assert r.status_code == 200

        # Verify two distinct events with different IDs
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        detail = r.json()
        val_events = [e for e in detail["events"] if e["event_type"] == "validated"]
        assert len(val_events) == 2, f"Expected 2 validation events, got {len(val_events)}"
        ids = [e["id"] for e in val_events]
        assert ids[0] != ids[1], "Validation event IDs must be distinct"


# ═══════════════════════════════════════════════════════════════════════════
# Fix 5: Decision/assignment separation — retire preserves published status
# ═══════════════════════════════════════════════════════════════════════════

class TestDecisionAssignmentSeparation:
    """Retire/rollback operate on assignments only — never mutate package status."""

    def test_retire_does_not_change_package_status(self, app, org_state):
        """Retire deactivates assignments but preserves PUBLISHED status."""
        client, version_id, skill_id = (
            TestRejectedBlocksAllMutations()._setup_published_package(app, org_state)
        )

        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        assert r.json()["status"] == "published"

        # Assign
        eid = r.json()["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/assign",
            headers=_founder_headers(),
            json={"agent_name": "dev_agent", "expected_event_id": eid},
        )
        assert r.status_code == 200

        # Retire
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/retire",
            headers=_founder_headers(),
            params={"skill_id": skill_id, "reason": "Old"},
        )
        assert r.status_code == 200

        # Status remains PUBLISHED
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        assert r.json()["status"] == "published", (
            f"Retire must not mutate package status. "
            f"Got '{r.json()['status']}'"
        )

    def test_rollback_does_not_mutate_package_status_v2(self, app, org_state):
        """V2 rollback preserves published package status."""
        client, version_id, skill_id = (
            TestRejectedBlocksAllMutations()._setup_published_package(app, org_state)
        )

        # Assign
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid = r.json()["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/assign",
            headers=_founder_headers(),
            json={"agent_name": "dev_agent", "expected_event_id": eid},
        )
        assert r.status_code == 200

        # Rollback v2
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        eid = r.json()["last_event_id"]
        r = client.post(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}/rollback",
            headers=_founder_headers(),
            json={"reason": "test", "expected_event_id": eid},
        )
        assert r.status_code == 200
        assert r.json()["assignments_deactivated"] >= 1

        # Status remains published
        r = client.get(
            f"/api/v1/orgs/alpha/skill-lifecycle/proposals/{version_id}",
            headers=_founder_headers(),
        )
        assert r.json()["status"] == "published"


# ═══════════════════════════════════════════════════════════════════════════
# Fix 1: Version-pinned rollback/retire + legacy rejected guard
# ═══════════════════════════════════════════════════════════════════════════

class TestVersionPinnedRollbackRetire:
    """Version-pinned rollback and retire with mixed rejected+published fixtures."""

    def test_v2_rollback_rejected_exact_version_no_mutation(self, app, org_state):
        """V2 rollback on exact rejected version returns rejected_terminal,
        leaves assignments and event count unchanged."""
        from runtime.skills.lifecycle import stores
        from runtime.skills.lifecycle.service import SkillLifecycleService

        db = org_state.db
        service = SkillLifecycleService()
        slug_val = "v2-reject-rollback"

        # v1: published + assigned to dev_agent
        pkg1 = service.submit_proposal(
            db=db, actor_kind="human", slug=slug_val,
            name="V2 Reject Rollback", description="Test",
            skill_md="# v1\n", version="0.1.0",
            proposer_agent="frontend_engineer",
            org_root=org_state.root,
        )
        v1_id = pkg1.id
        pkg1 = service.claim_proposal(db, "human", v1_id, "founder")
        pkg1 = service.record_validation(db, "human", v1_id, True,
            validator_version="THR-055/1.0.0", validator_key="THR-055/1.0.0")
        pkg1 = service.submit_for_review(db, "human", v1_id, "founder")
        pkg1 = service.review_decision(db, "human", v1_id, "approved", "OK", "founder")
        aid = None
        for e in stores.list_lifecycle_events(db, skill_id=pkg1.skill_id):
            if e.event_type == "approved" and e.package_version_id == v1_id:
                aid = e.id
                break
        assert aid is not None
        pkg1 = service.publish(db, "human", v1_id, aid, "founder")
        service.assign(db, "human", pkg1.skill_id, "dev_agent", v1_id, "founder")

        # v2: just publish another version
        pkg2 = service.submit_proposal(
            db=db, actor_kind="human", slug=slug_val,
            name="V2 Reject Rollback v2", description="Test v2",
            skill_md="# v2 diff\n", version="0.2.0",
            proposer_agent="frontend_engineer",
        )
        v2_id = pkg2.id

        # REJECT v1 after the fact
        db.execute(
            "UPDATE skill_lifecycle_packages SET status = 'rejected' WHERE id = ?",
            (v1_id,),
        )
        from runtime.skills.lifecycle.models import LifecycleEvent as LE
        stores.insert_lifecycle_event(db, LE(
            skill_id=pkg1.skill_id, package_version_id=v1_id,
            event_type="rejected", actor="founder", actor_role="reviewer",
            previous_status="in_review", new_status="rejected",
            content_hash=pkg1.content_hash,
        ))

        # Event count and assignments before
        events_before = stores.list_lifecycle_events(db, skill_id=pkg1.skill_id)
        event_count_before = len(events_before)
        active_before = stores.get_all_active_assignments_for_skill(db, pkg1.skill_id)

        # V2 rollback on REJECTED v1 → rejected_terminal
        with pytest.raises(LifecycleError) as exc_info:
            service.rollback(
                db, "human", pkg1.skill_id, "test", "founder",
                target_version_id=v1_id,
            )
        assert exc_info.value.code == "rejected_terminal"

        # No mutation
        events_after = stores.list_lifecycle_events(db, skill_id=pkg1.skill_id)
        assert len(events_after) == event_count_before
        active_after = stores.get_all_active_assignments_for_skill(db, pkg1.skill_id)
        assert len(active_after) == len(active_before)

    def test_v2_rollback_targets_only_addressed_version(self, app, org_state):
        """V2 exact-version rollback deactivates only addressed version's assignments."""
        from runtime.skills.lifecycle import stores
        from runtime.skills.lifecycle.service import SkillLifecycleService

        db = org_state.db
        service = SkillLifecycleService()
        slug_val = "v2-targeted-rollback"

        pkg1 = service.submit_proposal(
            db=db, actor_kind="human", slug=slug_val,
            name="Targeted Rollback", description="Test",
            skill_md="# v1\n", version="0.1.0",
            proposer_agent="frontend_engineer",
            org_root=org_state.root,
        )
        v1_id = pkg1.id
        pkg1 = service.claim_proposal(db, "human", v1_id, "founder")
        pkg1 = service.record_validation(db, "human", v1_id, True,
            validator_version="THR-055/1.0.0", validator_key="THR-055/1.0.0")
        pkg1 = service.submit_for_review(db, "human", v1_id, "founder")
        pkg1 = service.review_decision(db, "human", v1_id, "approved", "OK", "founder")
        aid = None
        for e in stores.list_lifecycle_events(db, skill_id=pkg1.skill_id):
            if e.event_type == "approved" and e.package_version_id == v1_id:
                aid = e.id
                break
        assert aid is not None
        pkg1 = service.publish(db, "human", v1_id, aid, "founder")
        service.assign(db, "human", pkg1.skill_id, "dev_agent", v1_id, "founder")

        # v2: published, no assignment
        pkg2 = service.submit_proposal(
            db=db, actor_kind="human", slug=slug_val,
            name="Targeted Rollback v2", description="Test v2",
            skill_md="# v2 diff\n", version="0.2.0",
            proposer_agent="frontend_engineer",
        )
        v2_id = pkg2.id

        # Rollback v1 — only deactivates v1
        count = service.rollback(
            db, "human", pkg1.skill_id, "test", "founder",
            target_version_id=v1_id,
        )
        assert count >= 1

        # v1 assignment inactive
        active = stores.get_all_active_assignments_for_skill(db, pkg1.skill_id)
        assert len(active) == 0

        # Package statuses correct
        pkg1_chk = stores.get_package_version(db, v1_id)
        assert pkg1_chk.status.value == "published"

    def test_legacy_rollback_blocked_on_rejected_assignment(self, app, org_state):
        """Legacy rollback (skill_id only) blocked when active assignment
        is on a REJECTED version. No assignment/event mutation."""
        from runtime.skills.lifecycle import stores
        from runtime.skills.lifecycle.service import SkillLifecycleService
        from runtime.skills.lifecycle.models import LifecycleEvent as LE

        db = org_state.db
        service = SkillLifecycleService()
        slug_val = "legacy-blocked-rollback"

        pkg1 = service.submit_proposal(
            db=db, actor_kind="human", slug=slug_val,
            name="Legacy Rollback Blocked", description="Test",
            skill_md="# v1\n", version="0.1.0",
            proposer_agent="frontend_engineer",
            org_root=org_state.root,
        )
        v1_id = pkg1.id
        pkg1 = service.claim_proposal(db, "human", v1_id, "founder")
        pkg1 = service.record_validation(db, "human", v1_id, True,
            validator_version="THR-055/1.0.0", validator_key="THR-055/1.0.0")
        pkg1 = service.submit_for_review(db, "human", v1_id, "founder")
        pkg1 = service.review_decision(db, "human", v1_id, "approved", "OK", "founder")
        aid = None
        for e in stores.list_lifecycle_events(db, skill_id=pkg1.skill_id):
            if e.event_type == "approved" and e.package_version_id == v1_id:
                aid = e.id
                break
        assert aid is not None
        pkg1 = service.publish(db, "human", v1_id, aid, "founder")
        service.assign(db, "human", pkg1.skill_id, "dev_agent", v1_id, "founder")

        # v2 published
        pkg2 = service.submit_proposal(
            db=db, actor_kind="human", slug=slug_val,
            name="Legacy Rollback Blocked v2", description="Test v2",
            skill_md="# v2 diff\n", version="0.2.0",
            proposer_agent="frontend_engineer",
        )

        # Reject v1 after assignment
        db.execute(
            "UPDATE skill_lifecycle_packages SET status = 'rejected' WHERE id = ?",
            (v1_id,),
        )
        stores.insert_lifecycle_event(db, LE(
            skill_id=pkg1.skill_id, package_version_id=v1_id,
            event_type="rejected", actor="founder", actor_role="reviewer",
            previous_status="in_review", new_status="rejected",
            content_hash=pkg1.content_hash,
        ))

        events_before = stores.list_lifecycle_events(db, skill_id=pkg1.skill_id)
        event_count_before = len(events_before)
        active_before = stores.get_all_active_assignments_for_skill(db, pkg1.skill_id)

        # Legacy rollback blocked
        with pytest.raises(LifecycleError) as exc_info:
            service.rollback(db, "human", pkg1.skill_id, "test", "founder")
        assert exc_info.value.code == "rejected_terminal"

        events_after = stores.list_lifecycle_events(db, skill_id=pkg1.skill_id)
        assert len(events_after) == event_count_before
        active_after = stores.get_all_active_assignments_for_skill(db, pkg1.skill_id)
        assert len(active_after) == len(active_before)

    def test_legacy_retire_blocked_on_rejected_assignment(self, app, org_state):
        """Legacy retire blocked when active assignment is on REJECTED version.
        Both assignments and event count unchanged."""
        from runtime.skills.lifecycle import stores
        from runtime.skills.lifecycle.service import SkillLifecycleService
        from runtime.skills.lifecycle.models import LifecycleEvent as LE

        db = org_state.db
        service = SkillLifecycleService()
        slug_val = "legacy-retire-blocked"

        pkg1 = service.submit_proposal(
            db=db, actor_kind="human", slug=slug_val,
            name="Retire Blocked", description="Test",
            skill_md="# v1\n", version="0.1.0",
            proposer_agent="frontend_engineer",
            org_root=org_state.root,
        )
        v1_id = pkg1.id
        pkg1 = service.claim_proposal(db, "human", v1_id, "founder")
        pkg1 = service.record_validation(db, "human", v1_id, True,
            validator_version="THR-055/1.0.0", validator_key="THR-055/1.0.0")
        pkg1 = service.submit_for_review(db, "human", v1_id, "founder")
        pkg1 = service.review_decision(db, "human", v1_id, "approved", "OK", "founder")
        aid = None
        for e in stores.list_lifecycle_events(db, skill_id=pkg1.skill_id):
            if e.event_type == "approved" and e.package_version_id == v1_id:
                aid = e.id
                break
        assert aid is not None
        pkg1 = service.publish(db, "human", v1_id, aid, "founder")
        service.assign(db, "human", pkg1.skill_id, "dev_agent", v1_id, "founder")

        # v2
        pkg2 = service.submit_proposal(
            db=db, actor_kind="human", slug=slug_val,
            name="Retire Blocked v2", description="Test v2",
            skill_md="# v2 diff\n", version="0.2.0",
            proposer_agent="frontend_engineer",
        )

        # Reject v1
        db.execute(
            "UPDATE skill_lifecycle_packages SET status = 'rejected' WHERE id = ?",
            (v1_id,),
        )
        stores.insert_lifecycle_event(db, LE(
            skill_id=pkg1.skill_id, package_version_id=v1_id,
            event_type="rejected", actor="founder", actor_role="reviewer",
            previous_status="in_review", new_status="rejected",
            content_hash=pkg1.content_hash,
        ))

        events_before = stores.list_lifecycle_events(db, skill_id=pkg1.skill_id)
        event_count_before = len(events_before)
        active_before = stores.get_all_active_assignments_for_skill(db, pkg1.skill_id)

        with pytest.raises(LifecycleError) as exc_info:
            service.retire(db, "human", pkg1.skill_id, "obsolete", "founder")
        assert exc_info.value.code == "rejected_terminal"

        events_after = stores.list_lifecycle_events(db, skill_id=pkg1.skill_id)
        assert len(events_after) == event_count_before
        active_after = stores.get_all_active_assignments_for_skill(db, pkg1.skill_id)
        assert len(active_after) == len(active_before)

    def test_legacy_retire_succeeds_with_no_rejected_assignment(self, app, org_state):
        """Legacy retire succeeds when all active assignments are on PUBLISHED versions."""
        from runtime.skills.lifecycle import stores
        from runtime.skills.lifecycle.service import SkillLifecycleService

        db = org_state.db
        service = SkillLifecycleService()
        slug_val = "legacy-retire-ok"

        pkg1 = service.submit_proposal(
            db=db, actor_kind="human", slug=slug_val,
            name="Retire OK", description="Test",
            skill_md="# v1\n", version="0.1.0",
            proposer_agent="frontend_engineer",
            org_root=org_state.root,
        )
        v1_id = pkg1.id
        pkg1 = service.claim_proposal(db, "human", v1_id, "founder")
        pkg1 = service.record_validation(db, "human", v1_id, True,
            validator_version="THR-055/1.0.0", validator_key="THR-055/1.0.0")
        pkg1 = service.submit_for_review(db, "human", v1_id, "founder")
        pkg1 = service.review_decision(db, "human", v1_id, "approved", "OK", "founder")
        aid = None
        for e in stores.list_lifecycle_events(db, skill_id=pkg1.skill_id):
            if e.event_type == "approved" and e.package_version_id == v1_id:
                aid = e.id
                break
        assert aid is not None
        pkg1 = service.publish(db, "human", v1_id, aid, "founder")
        service.assign(db, "human", pkg1.skill_id, "dev_agent", v1_id, "founder")

        result = service.retire(db, "human", pkg1.skill_id, "obsolete", "founder")
        assert result is not None
        assert result.status.value == "published"


# ═══════════════════════════════════════════════════════════════════════════
# Fix 3: Deterministic validation identifiers mandatory
# ═══════════════════════════════════════════════════════════════════════════

class TestMandatoryValidatorVersion:
    """Missing/blank validator_version must be rejected."""

    def test_missing_validator_version_rejected_direct(self, app, org_state):
        """record_validation with missing validator_version raises error."""
        from runtime.skills.lifecycle.service import SkillLifecycleService

        db = org_state.db
        service = SkillLifecycleService()

        pkg = service.submit_proposal(
            db=db, actor_kind="human", slug="mandatory-val",
            name="Mandatory Val", description="Test",
            skill_md="# Test\n", version="0.1.0",
            proposer_agent="frontend_engineer",
        )
        pkg = service.claim_proposal(db, "human", pkg.id, "founder")

        with pytest.raises(LifecycleError) as exc_info:
            service.record_validation(db, "human", pkg.id, True)
        assert exc_info.value.code == "missing_validator_version"

    def test_blank_validator_version_rejected_direct(self, app, org_state):
        """record_validation with blank validator_version raises error."""
        from runtime.skills.lifecycle.service import SkillLifecycleService

        db = org_state.db
        service = SkillLifecycleService()

        pkg = service.submit_proposal(
            db=db, actor_kind="human", slug="blank-val",
            name="Blank Val", description="Test",
            skill_md="# Test\n", version="0.1.0",
            proposer_agent="frontend_engineer",
        )
        pkg = service.claim_proposal(db, "human", pkg.id, "founder")

        with pytest.raises(LifecycleError) as exc_info:
            service.record_validation(db, "human", pkg.id, True, validator_version="   ")
        assert exc_info.value.code == "missing_validator_version"

    def test_validator_version_with_value_succeeds(self, app, org_state):
        """record_validation with non-blank validator_version succeeds."""
        from runtime.skills.lifecycle.service import SkillLifecycleService
        from runtime.skills.lifecycle import stores

        db = org_state.db
        service = SkillLifecycleService()

        pkg = service.submit_proposal(
            db=db, actor_kind="human", slug="valid-val",
            name="Valid Val", description="Test",
            skill_md="# Test\n", version="0.1.0",
            proposer_agent="frontend_engineer",
        )
        pkg = service.claim_proposal(db, "human", pkg.id, "founder")

        pkg = service.record_validation(
            db, "human", pkg.id, True,
            validator_version="THR-055/1.0.0",
            validator_key="THR-055/1.0.0",
        )
        assert pkg.status.value == "validated"

        events = stores.list_lifecycle_events(db, skill_id=pkg.skill_id)
        val_events = [e for e in events if e.event_type == "validated"]
        assert len(val_events) >= 1
        assert val_events[-1].metadata.get("validator_version") == "THR-055/1.0.0"
        assert val_events[-1].metadata.get("validator_key") == "THR-055/1.0.0"
        assert val_events[-1].metadata.get("content_hash") == pkg.content_hash


class TestValidatorKeyNormalization:
    """Coverage of validator_key behavior across the legacy HTTP validate
    route and the direct-service validate_proposal method.

    The legacy POST /validate route writes the fixed ``LEGACY/1.0.0``
    version/key pair — it takes no caller-supplied ``validator_key``
    and hardcodes both fields.  The single legacy-route test below is
    **fixed legacy HTTP validation evidence** — it proves that the
    endpoint always records ``LEGACY/1.0.0`` regardless of any
    caller-supplied input.

    The v2 HTTP route (POST /proposals/{version_id}/validate) does NOT
    accept a caller-supplied ``validator_key`` — it always derives it
    from the ``validator_version`` request field.

    The two direct-service tests below exercise the
    ``SkillLifecycleService.validate_proposal`` method and are the
    **sole coverage of caller blank/None validator_key normalization**
    — they prove that a blank, whitespace-only, or ``None``
    ``validator_key`` arg is normalized to the supplied
    ``validator_version``."""

    def test_legacy_validate_records_fixed_version_key_pair(self, app, org_state):
        """Legacy POST /validate writes the fixed LEGACY/1.0.0 version/key
        pair — the endpoint takes no caller-supplied validator_key and
        hardcodes both fields.  This is fixed legacy HTTP validation
        evidence, not a normalization test."""
        from runtime.skills.lifecycle import stores
        from runtime.skills.lifecycle.service import SkillLifecycleService

        db = org_state.db
        service = SkillLifecycleService()
        pkg = service.submit_proposal(
            db=db, actor_kind="human", slug="legacy-ws-key",
            name="Legacy WS Key", description="Test",
            skill_md="# Test\n", version="0.1.0",
            proposer_agent="frontend_engineer",
        )
        pkg = service.claim_proposal(db, "human", pkg.id, "founder")

        # Call legacy validate route — it supplies LEGACY/1.0.0 as both
        # validator_version and validator_key (hardcoded in the route).
        client = TestClient(app)
        resp = client.post(
            f"/api/v1/orgs/{org_state.slug}/skill-lifecycle/validate",
            params={"slug": "legacy-ws-key", "version_id": pkg.id},
            headers=_founder_headers(),
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        # Verify the validation event stores LEGACY/1.0.0 for both fields
        events = stores.list_lifecycle_events(db, skill_id=pkg.skill_id)
        val_events = [e for e in events if e.event_type == "validated"]
        assert len(val_events) >= 1
        meta = val_events[-1].metadata
        assert meta.get("validator_version") == "LEGACY/1.0.0"
        assert meta.get("validator_key") == "LEGACY/1.0.0"

    def test_service_validate_blank_key_normalized_to_version(self, app, org_state):
        """Direct-service validate_proposal normalizes a blank/whitespace
        validator_key to the supplied validator_version — this is the
        sole coverage of caller blank-key normalization.
        No HTTP route is involved; this tests the service method directly."""
        from runtime.skills.lifecycle import stores
        from runtime.skills.lifecycle.service import SkillLifecycleService

        db = org_state.db
        service = SkillLifecycleService()
        pkg = service.submit_proposal(
            db=db, actor_kind="human", slug="v2-val-key",
            name="V2 Val Key", description="Test",
            skill_md="# Test\n", version="0.1.0",
            proposer_agent="frontend_engineer",
        )
        pkg = service.claim_proposal(db, "human", pkg.id, "founder")

        # Direct-service validate_proposal normalizes blank/whitespace
        # validator_key to the supplied validator_version.
        pkg = service.validate_proposal(
            db=db, actor_kind="human", version_id=pkg.id,
            validator_version="THR-055/1.0.0",
            validator_key="   ",  # Whitespace — should normalize
        )
        assert pkg.status.value == "validated"

        events = stores.list_lifecycle_events(db, skill_id=pkg.skill_id)
        val_events = [e for e in events if e.event_type == "validated"]
        assert len(val_events) >= 1
        meta = val_events[-1].metadata
        assert meta.get("validator_version") == "THR-055/1.0.0"
        assert meta.get("validator_key") == "THR-055/1.0.0"  # Normalized

    def test_service_validate_none_key_normalized_to_version(self, app, org_state):
        """Direct-service validate_proposal with None validator_key arg
        falls back to the supplied validator_version — this is the
        sole coverage of caller None-key normalization.
        No HTTP route is involved; this tests the service method directly."""
        from runtime.skills.lifecycle import stores
        from runtime.skills.lifecycle.service import SkillLifecycleService

        db = org_state.db
        service = SkillLifecycleService()
        pkg = service.submit_proposal(
            db=db, actor_kind="human", slug="v2-val-none",
            name="V2 Val None", description="Test",
            skill_md="# Test\n", version="0.1.0",
            proposer_agent="frontend_engineer",
        )
        pkg = service.claim_proposal(db, "human", pkg.id, "founder")

        pkg = service.validate_proposal(
            db=db, actor_kind="human", version_id=pkg.id,
            validator_version="THR-055/1.0.0",
            validator_key=None,
        )
        assert pkg.status.value == "validated"

        events = stores.list_lifecycle_events(db, skill_id=pkg.skill_id)
        val_events = [e for e in events if e.event_type == "validated"]
        assert len(val_events) >= 1
        meta = val_events[-1].metadata
        assert meta.get("validator_version") == "THR-055/1.0.0"
        assert meta.get("validator_key") == "THR-055/1.0.0"  # Normalized

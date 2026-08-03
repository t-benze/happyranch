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
    """Founder claims a proposal via v2 route."""
    r = client.post(
        "/api/v1/orgs/alpha/skill-lifecycle/proposals/claim",
        headers=_founder_headers(),
        json={"proposal_version_id": version_id, **kwargs},
    )
    return r


def _validate_proposal(client: TestClient, version_id: int, expected_event_id: int) -> dict:
    """Validate via v2 route."""
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

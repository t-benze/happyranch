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


def _assert_zero_residue(org_state, skill_id: str = "hr:frontend-development") -> None:
    """Assert zero persistence residue across ALL surfaces:
    - skill_lifecycle_packages (package version inventory)
    - skill_lifecycle_events (lifecycle event ledger)
    - skill_lifecycle_materializations (operational records)
    - ArtifactStore (correct org-scoped prefix)
    """
    from runtime.skills.lifecycle import stores as lifecycle_stores
    from runtime.infrastructure.artifact_store import ArtifactStore
    from runtime.orchestrator._paths import OrgPaths

    # 1. Zero package versions
    packages = lifecycle_stores.list_package_versions(org_state.db, skill_id=skill_id)
    assert len(packages) == 0, (
        f"Package residue: expected 0, got {len(packages)}"
    )

    # 2. Zero lifecycle events
    events = lifecycle_stores.list_lifecycle_events(org_state.db, skill_id=skill_id)
    assert len(events) == 0, (
        f"Event/ledger residue: expected 0, got {len(events)}"
    )

    # 3. Zero operational materializations
    slug = skill_id.replace("hr:", "")
    mat = lifecycle_stores.get_latest_materialization(org_state.db, skill_id, "frontend_engineer")
    assert mat is None, (
        f"Materialization residue: expected None, got {mat}"
    )

    # 4. Zero proposal artifacts in ArtifactStore (org-scoped prefix)
    artifact_store = ArtifactStore(OrgPaths(org_state.root).artifacts_dir)
    proposal_artifacts = artifact_store.list_artifacts(
        prefix=f"skill-lifecycle/{slug}",
    )
    assert len(proposal_artifacts) == 0, (
        f"Artifact-store residue: expected 0, got {len(proposal_artifacts)}"
    )


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
        # Verify zero residue across ALL persistence surfaces
        _assert_zero_residue(org)

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
        # Verify zero residue across ALL persistence surfaces
        _assert_zero_residue(org)

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
        # Verify zero residue across ALL persistence surfaces
        _assert_zero_residue(org)

    # ── Empty-string identity rejection (closes truthiness-bypass gap) ──

    @pytest.mark.parametrize("key,value", [
        ("task_id", ""),
        ("session_id", ""),
        ("proposer_agent", ""),
    ])
    def test_body_identity_empty_string_rejected_403(
        self, client_with_runtime, key, value
    ):
        """Empty-string identity claims are strictly rejected with 403
        body_identity_rejected — the old truthiness check is closed."""
        client, org = client_with_runtime

        org.sessions.set_active(
            "TASK-3510", "frontend_engineer", "sess-body-empty",
            org_slug="alpha",
        )

        body = dict(_VALID_PROPOSAL, **{key: value})

        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/agent",
            json=body,
            params={"session_id": "sess-body-empty"},
        )
        assert resp.status_code == 403, (
            f"Expected 403 for empty {key} claim, got {resp.status_code}: {resp.json()}"
        )
        detail = resp.json()["detail"]
        assert detail["code"] == "body_identity_rejected"
        assert key in detail.get("detail", "")
        # Verify zero residue — identity rejection before any persistence
        _assert_zero_residue(org)

    # ── Extra-key identity rejection (keys Pydantic would silently drop) ──

    @pytest.mark.parametrize("key,value", [
        ("org", "evil-org"),
        ("org_slug", "evil-org"),
        ("agent", "evil-agent"),
        ("agent_name", "evil-agent"),
        ("actor", "evil-actor"),
        ("eligibility", "true"),
        ("permission", "admin"),
        ("permissions", ["admin"]),
    ])
    def test_body_extra_identity_key_rejected_403(
        self, client_with_runtime, key, value
    ):
        """Every prohibited identity/authority key (including those Pydantic
        would silently drop) is rejected with exact 403 body_identity_rejected.
        This closes the model-silently-ignores gap."""
        client, org = client_with_runtime

        org.sessions.set_active(
            "TASK-3510", "frontend_engineer", "sess-body-extra",
            org_slug="alpha",
        )

        body = dict(_VALID_PROPOSAL, **{key: value})

        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/agent",
            json=body,
            params={"session_id": "sess-body-extra"},
        )
        assert resp.status_code == 403, (
            f"Expected 403 for prohibited {key} claim, got {resp.status_code}: {resp.json()}"
        )
        detail = resp.json()["detail"]
        assert detail["code"] == "body_identity_rejected"
        assert key in detail.get("detail", "")
        # Verify zero residue — identity rejection before any persistence
        _assert_zero_residue(org)

    # ── Empty-value extra-key rejection (presence boundary, not truthiness) ──

    @pytest.mark.parametrize("key,value", [
        ("org", ""),
        ("org_slug", ""),
        ("agent", ""),
        ("agent_name", ""),
        ("actor", ""),
        ("eligibility", ""),
        ("permission", ""),
        ("permissions", []),
    ])
    def test_body_extra_identity_key_empty_value_rejected_403(
        self, client_with_runtime, key, value
    ):
        """Every prohibited identity/authority key with an empty/falsey value
        is rejected with exact 403 body_identity_rejected. The boundary is
        key *presence* — not truthiness — so empty strings and empty lists
        are just as prohibited as non-empty values."""
        client, org = client_with_runtime

        org.sessions.set_active(
            "TASK-3510", "frontend_engineer", "sess-body-empty-extra",
            org_slug="alpha",
        )

        body = dict(_VALID_PROPOSAL, **{key: value})

        client.headers.pop("Authorization", None)
        resp = client.post(
            "/api/v1/orgs/alpha/skill-lifecycle/proposals/agent",
            json=body,
            params={"session_id": "sess-body-empty-extra"},
        )
        assert resp.status_code == 403, (
            f"Expected 403 for empty {key} claim, got {resp.status_code}: {resp.json()}"
        )
        detail = resp.json()["detail"]
        assert detail["code"] == "body_identity_rejected"
        assert key in detail.get("detail", "")
        # Verify zero residue across ALL persistence surfaces
        _assert_zero_residue(org)

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

        # ── Prove exact four-part provenance directly from stored record ──
        from runtime.skills.lifecycle import stores as lifecycle_stores

        # 1. Exactly one immutable proposed package version
        packages = lifecycle_stores.list_package_versions(org.db, skill_id=skill_id)
        assert len(packages) == 1, (
            f"Expected exactly 1 proposed package, got {len(packages)}"
        )
        pkg = packages[0]
        assert pkg.status.value == "proposed"
        assert pkg.proposal_task_id == "TASK-PROV"
        assert pkg.proposer_agent == "frontend_engineer"
        assert pkg.proposal_session_id == "sess-prov", (
            f"Stored proposal_session_id mismatch: expected sess-prov, got {pkg.proposal_session_id}"
        )

        # 2. Exactly one lifecycle event (proposed)
        events = lifecycle_stores.list_lifecycle_events(org.db, skill_id=skill_id)
        assert len(events) == 1, (
            f"Expected exactly 1 lifecycle event, got {len(events)}"
        )
        event = events[0]
        assert event.event_type == "proposed"
        assert event.actor == "frontend_engineer"

        # 3. Zero materialization before founder publication
        mat = lifecycle_stores.get_latest_materialization(
            org.db, skill_id, "frontend_engineer"
        )
        assert mat is None, f"Unexpected materialization before publication: {mat}"

        # 4. Artifact store has proposal artifacts (org-scoped prefix)
        from runtime.infrastructure.artifact_store import ArtifactStore
        from runtime.orchestrator._paths import OrgPaths
        artifact_store = ArtifactStore(OrgPaths(org.root).artifacts_dir)
        proposal_artifacts = artifact_store.list_artifacts(
            prefix="skill-lifecycle/frontend-development",
        )
        assert len(proposal_artifacts) > 0, (
            "Expected proposal artifacts in org-scoped artifact store"
        )

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

        # Verify zero residue across ALL persistence surfaces:
        # package versions, lifecycle events, materializations, and
        # ArtifactStore (not just catalog invisibility which would miss
        # proposed-but-not-published packages).
        _assert_zero_residue(org)

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
        # Verify zero residue — cross-org denial must happen BEFORE persistence
        _assert_zero_residue(org)

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

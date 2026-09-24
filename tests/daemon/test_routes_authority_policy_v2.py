"""THR-229 checkpoint B2b1 — shipping HTTP control API at the real router seam.

Drives the actual authenticated ``runtime/daemon/routes/authority_policy.py``
routes over disposable org DBs (TestClient, no lifespan):

  * strict raw-byte decode before JSON/Pydantic normalization (duplicate
    member, invalid UTF-8, BOM, NaN/Infinity, non-object root, wrong scalar
    type, unknown field, blank/oversized text, missing required CAS base);
  * empty-then-paired bootstrap and active-v1-then-first-v2 selection with the
    exact accepted starter bytes/digests and complete release/activation/
    selector/history/control receipts;
  * exact replay returning the ORIGINAL receipt after a later selection and
    across a database reopen, and altered replay conflicting without residue;
  * stale/absent CAS base refusal with no policy-write residue;
  * v2 -> v1 rollback through the shipping legacy route with byte-preserved
    historical legacy activation bytes;
  * authenticated/eligible-only surfaces and stable v2 history pagination.

This is HTTP-seam proof for B2b1. It does not claim startup initialization,
launch/prompt/session binding, completion consumer, recovery/queue or the
two-text editor (B2b2 + C/D debt).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pytest

from runtime.infrastructure.database import Database
from runtime.models import (
    AuthorityPolicyActivation,
    AuthorityPolicyRelease,
)
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.agent_def import AgentDef, render_agent_text
from runtime.orchestrator.authority_policy import (
    CONTINUE_ROUTINE_PHRASE,
    ENGINEERING_PRE_ESCALATION_POLICY,
)
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore

TEAM = "engineering"
POLICY_ID = "engineering-dual-text"
TITLE = "Engineering escalation policy"
WHAT_NOT = (
    "Continue implementation, debugging, review corrections, testing, CI waits, "
    "evidence collection and worker reassignment within approved scope. Failed "
    "reviews, retries, incomplete worker results and recoverable execution "
    "failures alone do not require founder escalation. Continue to enforce the "
    "required review, QA and merge gates."
)
WHAT_TO = (
    "Escalate when the next action requires a product or external-contract "
    "change, significant architecture change, or substantial development effort "
    "beyond the approved scope. Also escalate decisions explicitly reserved for "
    "the founder that lack applicable authorization. Existing approval carries "
    "through ordinary implementation and recovery within its scope."
)
EMPTY_SELECTOR_ID = "APS-8cf17c75b0d19dd9c39f5a310902f501faae2022d926026f1922b61241a9e344"
APPROVED_RELEASE_DIGEST = "975aa38509c4ccd466b751716962f02f01ee6db128eafb4c68691d7af18297fb"
APPROVED_ACTIVATION_DIGEST = "3158fe32a06c5d57cf80c9f46f9368fdd8e28df47b04025eecbf646561a7da32"
APPROVED_SELECTOR_ID = "APS-4b7c7be62a05ec768eecd1a21bb1cbf6aac34b4b99af5ed5fe604abd2c550d47"

BASE = "/api/v1/orgs/alpha/agents/engineering_manager/team-escalation-policy"


def _seed_agent(org, name="engineering_manager", *, team="engineering", role="manager"):
    agent = AgentDef(
        name=name, team=team, role=role, executor="claude", allow_rules=tuple(),
        repos={}, enrolled_by=None, enrolled_at_task=None,
        enrolled_at=datetime.now(timezone.utc), system_prompt="prompt", description="desc",
    )
    paths = OrgPaths(root=org.root)
    paths.agents_dir.mkdir(parents=True, exist_ok=True)
    (paths.agents_dir / f"{name}.md").write_text(render_agent_text(agent))


def _legacy_release_and_activation(org):
    clauses = '[{"action":"escalate_to_founder","category":"protected","condition":"stop","id":"esc-protected"}]'
    release = AuthorityPolicyRelease(
        team=TEAM, policy_id="engineering/pre-escalation-authority",
        version=1, title="Policy", normative_text="text", clauses_json=clauses,
        continuation_phrase="routine same-root follow-through of the already-completed slice",
        actor_kind="shared_local_operator_credential",
    )
    store = AuthorityPolicyStore(org.db)
    release = store.create_release(release)
    activation = AuthorityPolicyActivation.create(
        id="APA-1", team=TEAM, epoch=1, release_id=release.id,
        action="bootstrap", actor_kind="shared_local_operator_credential",
        request_id="REQ-1", request_digest=hashlib.sha256(b"request").hexdigest(),
    )
    return release, store.activate(activation)


def _pair_body(*, base, expected, action="bootstrap", create_id="req-create-0001",
               activation_id="req-activate-0001", title=TITLE, policy_id=POLICY_ID):
    return {
        "team": TEAM, "policy_id": policy_id, "title": title,
        "create_request_id": create_id, "activation_request_id": activation_id,
        "based_on_selector_id": base, "expected_selector_id": expected,
        "action": action, "what_to_escalate": WHAT_TO,
        "what_not_to_escalate": WHAT_NOT,
        "acknowledge_shared_credential_attribution": True,
    }


def _count(org, table):
    return org.db._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _post_raw(client, url, payload: bytes):
    return client.post(url, content=payload, headers={"content-type": "application/json"})


def test_empty_paired_bootstrap_is_atomic_exact_and_readback(client_with_runtime):
    client, org = client_with_runtime
    _seed_agent(org)

    projection = client.get(BASE).json()
    assert projection["bootstrap_required"] is True
    assert projection["family"] == "empty"
    assert projection["selector_id"] == EMPTY_SELECTOR_ID
    assert projection["selector_epoch"] == 0
    assert "active" not in projection

    response = client.post(
        f"{BASE}/v2/releases",
        json=_pair_body(base=None, expected=None),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["family"] == "v2" and body["contract_version"] == "v2"
    receipt = body["receipt"]
    assert body["control"] == "v2_create_activate"
    assert receipt["policy_digest"] == APPROVED_RELEASE_DIGEST
    assert receipt["release_id"] == f"APV2-{APPROVED_RELEASE_DIGEST}"
    assert receipt["activation_digest"] == APPROVED_ACTIVATION_DIGEST
    assert receipt["selector_epoch"] == 1
    assert receipt["previous_selector_id"] == EMPTY_SELECTOR_ID
    assert receipt["action"] == "bootstrap"
    assert receipt["selector_id"] == APPROVED_SELECTOR_ID

    # ONE immutable release + activation + selector + both control audits.
    assert _count(org, "authority_policy_v2_releases") == 1
    assert _count(org, "authority_policy_v2_activations") == 1
    assert _count(org, "authority_policy_active_selector") == 1
    assert _count(org, "authority_policy_active_selector_history") == 2  # empty + v2
    kinds = [row[0] for row in org.db._conn.execute(
        "SELECT kind FROM authority_policy_v2_control_audit ORDER BY id"
    ).fetchall()]
    assert kinds == ["selector_initialized_empty", "release_created", "activation_selected"]
    # No legacy rows were created or rewritten by the v2 path.
    assert _count(org, "authority_policy_releases") == 0
    assert _count(org, "authority_policy_activations") == 0

    readback = client.get(BASE).json()
    assert readback["family"] == "v2"
    assert readback["contract_version"] == "v2"
    assert readback["selector_id"] == receipt["selector_id"]
    assert readback["selector_epoch"] == 1
    assert readback["active"]["release"]["what_to_escalate"] == WHAT_TO
    assert readback["active"]["release"]["what_not_to_escalate"] == WHAT_NOT
    assert readback["active"]["release"]["digest"] == APPROVED_RELEASE_DIGEST

    history = client.get(f"{BASE}/v2/history").json()
    assert len(history["items"]) == 1
    assert history["items"][0]["release_id"] == receipt["release_id"]
    assert history["items"][0]["what_to_escalate"] == WHAT_TO
    assert history["items"][0]["activation"]["selector_epoch"] == 1


def test_active_v1_then_first_v2_selection_keeps_v1_rows(client_with_runtime):
    client, org = client_with_runtime
    _seed_agent(org)
    release, activation = _legacy_release_and_activation(org)

    legacy_projection = client.get(BASE).json()
    assert legacy_projection["family"] == "legacy_v1"
    assert legacy_projection["contract_version"] == "v1"
    assert legacy_projection["selector_epoch"] == 1
    base_selector = legacy_projection["selector_id"]

    response = client.post(
        f"{BASE}/v2/releases",
        json=_pair_body(base=base_selector, expected=base_selector, action="activate"),
    )
    assert response.status_code == 201
    receipt = response.json()["receipt"]
    assert receipt["action"] == "activate"
    assert receipt["selector_epoch"] == 2
    assert receipt["previous_selector_id"] == base_selector

    # Historical legacy release/activation bytes are unchanged.
    assert (_count(org, "authority_policy_releases"),
            _count(org, "authority_policy_activations")) == (1, 1)
    persisted = AuthorityPolicyStore(org.db).get_activation(activation.id)
    assert persisted.epoch == 1
    assert persisted.activation_digest == activation.activation_digest
    assert AuthorityPolicyStore(org.db).get_release(release.id).policy_digest == release.policy_digest

    readback = client.get(BASE).json()
    assert readback["family"] == "v2"
    assert readback["selector_epoch"] == 2
    assert readback["active"]["family"] == "v2"
    assert readback["active"]["release"]["digest"] == APPROVED_RELEASE_DIGEST
    assert readback["active"]["release"].get("normative_text") is None
    # The v1 history stream stays intact and separately authoritative.
    legacy_history = client.get(f"{BASE}/history").json()
    assert legacy_history["items"][0]["family"] == "legacy_v1"
    assert legacy_history["items"][0]["release_id"] == release.id


def test_v2_activation_route_rolls_back_and_replays_after_reopen(client_with_runtime, tmp_path):
    client, org = client_with_runtime
    _seed_agent(org)

    first = client.post(f"{BASE}/v2/releases", json=_pair_body(base=None, expected=None))
    assert first.status_code == 201
    receipt1 = first.json()["receipt"]

    second = client.post(f"{BASE}/v2/releases", json=_pair_body(
        base=receipt1["selector_id"], expected=receipt1["selector_id"],
        action="activate", create_id="req-create-0002", activation_id="req-activate-0002",
        title="Second dual text",
    ))
    assert second.status_code == 201
    receipt2 = second.json()["receipt"]
    assert receipt2["release_version"] == 2

    rollback = client.post(f"{BASE}/v2/activations", json={
        "team": TEAM, "release_id": receipt1["release_id"],
        "request_id": "req-rollback-0001",
        "expected_selector_id": receipt2["selector_id"],
        "action": "reactivate_rollback",
        "acknowledge_shared_credential_attribution": True,
    })
    assert rollback.status_code == 200
    rolled = rollback.json()["receipt"]
    assert rolled["kind"] == "v2_activate"
    assert rolled["action"] == "reactivate_rollback"
    assert rolled["release_id"] == receipt1["release_id"]
    assert rolled["selector_epoch"] == 3
    assert rolled["previous_selector_id"] == receipt2["selector_id"]

    # Exact replay of the FIRST paired request returns the ORIGINAL receipt,
    # after a later selection, without reapplying anything.
    replay = client.post(f"{BASE}/v2/releases", json=_pair_body(base=None, expected=None))
    assert replay.status_code == 201
    assert replay.json()["receipt"] == receipt1
    assert client.get(BASE).json()["selector_id"] == rolled["selector_id"]
    assert _count(org, "authority_policy_v2_releases") == 2
    assert _count(org, "authority_policy_v2_activations") == 3

    # Reopen the same database file on a fresh connection: the original receipt
    # authenticates byte-for-byte.
    reopened = AuthorityPolicyStore(Database(org.db.path))
    data = _pair_body(base=None, expected=None)
    data.pop("acknowledge_shared_credential_attribution")
    stored = reopened.create_and_activate_v2(data)
    assert stored.release_id == receipt1["release_id"]
    assert stored.activation_id == receipt1["activation_id"]
    assert stored.selector_id == receipt1["selector_id"]


def test_altered_replay_conflicts_without_state_change(client_with_runtime):
    client, org = client_with_runtime
    _seed_agent(org)
    assert client.post(f"{BASE}/v2/releases",
                       json=_pair_body(base=None, expected=None)).status_code == 201

    altered = _pair_body(base=None, expected=None, title="Mutated retry")
    conflict = client.post(f"{BASE}/v2/releases", json=altered)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] in ("control_conflict", "idempotency_conflict")
    assert _count(org, "authority_policy_v2_releases") == 1
    assert _count(org, "authority_policy_v2_activations") == 1
    assert _count(org, "authority_policy_active_selector_history") == 2

    # A reused ACTIVATION request id on a fresh pair is also a conflict.
    reused_activation = _pair_body(
        base=None, expected=APPROVED_SELECTOR_ID, action="activate",
        create_id="req-create-0009", activation_id="req-activate-0001",
    )
    assert client.post(f"{BASE}/v2/releases", json=reused_activation).status_code == 409


def test_stale_or_absent_selector_base_refuses_without_residue(client_with_runtime):
    client, org = client_with_runtime
    _seed_agent(org)
    assert client.post(f"{BASE}/v2/releases",
                       json=_pair_body(base=None, expected=None)).status_code == 201
    history_before = _count(org, "authority_policy_active_selector_history")

    # explicit null against a non-empty selector is a selector conflict.
    stale_null = client.post(f"{BASE}/v2/releases", json=_pair_body(
        base=None, expected=None, action="activate",
        create_id="req-create-0003", activation_id="req-activate-0003",
    ))
    assert stale_null.status_code == 409
    assert stale_null.json()["detail"]["code"] == "selector_conflict"
    # A stale explicit selector id also conflicts.
    stale_id = client.post(f"{BASE}/v2/releases", json=_pair_body(
        base=EMPTY_SELECTOR_ID, expected=EMPTY_SELECTOR_ID, action="activate",
        create_id="req-create-0004", activation_id="req-activate-0004",
    ))
    assert stale_id.status_code == 409
    assert stale_id.json()["detail"]["code"] == "selector_conflict"

    assert _count(org, "authority_policy_v2_releases") == 1
    assert _count(org, "authority_policy_v2_activations") == 1
    assert _count(org, "authority_policy_active_selector_history") == history_before


@pytest.mark.parametrize("raw", [
    b'{"team":"engineering","team":"engineering"}',                     # duplicate member
    b'{"team":"\xff"}',                                                  # invalid UTF-8
    b'\xef\xbb\xbf{"team":"engineering"}',                               # BOM
    b'{"team":"engineering","policy_id":NaN}',                           # NaN constant
    b'{"team":"engineering","policy_id":Infinity}',                      # Infinity
    b"[]",                                                               # non-object root
    b'"engineering"',                                                    # non-object scalar
    json.dumps({**_pair_body(base=None, expected=None), "policy_id": 123}).encode(),
    json.dumps({**_pair_body(base=None, expected=None), "title": ""}).encode(),
    json.dumps({**_pair_body(base=None, expected=None), "title": "x" * 201}).encode(),
    json.dumps({**_pair_body(base=None, expected=None), "action": "bogus"}).encode(),
    json.dumps({**_pair_body(base=None, expected=None), "extra": 1}).encode(),
])
def test_strict_raw_transport_rejects_malformed_before_any_write(client_with_runtime, raw):
    client, org = client_with_runtime
    _seed_agent(org)
    response = _post_raw(client, f"{BASE}/v2/releases", raw)
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "invalid_policy_request"}}
    assert _count(org, "authority_policy_v2_releases") == 0
    assert _count(org, "authority_policy_v2_activations") == 0
    assert _count(org, "authority_policy_active_selector") == 0


@pytest.mark.parametrize("drop", ["expected_selector_id", "based_on_selector_id"])
def test_missing_required_cas_members_are_invalid(client_with_runtime, drop):
    client, org = client_with_runtime
    _seed_agent(org)
    body = _pair_body(base=None, expected=None)
    body.pop(drop)
    response = client.post(f"{BASE}/v2/releases", json=body)
    assert response.status_code == 422
    assert _count(org, "authority_policy_v2_releases") == 0


def test_v2_to_v1_rollback_preserves_historical_legacy_activation(client_with_runtime):
    client, org = client_with_runtime
    _seed_agent(org)
    legacy_release, legacy_activation = _legacy_release_and_activation(org)
    legacy_selector = client.get(BASE).json()["selector_id"]

    v2 = client.post(f"{BASE}/v2/releases", json=_pair_body(
        base=legacy_selector, expected=legacy_selector, action="activate",
    ))
    assert v2.status_code == 201
    v2_receipt = v2.json()["receipt"]

    rollback = client.post(f"{BASE}/activations", json={
        "release_id": legacy_release.id, "expected_previous_epoch": 9,
        "expected_selector_id": v2_receipt["selector_id"],
        "request_id": "REQ-v2-to-v1", "action": "reactivate_rollback",
        "acknowledge_shared_credential_attribution": True,
    })
    assert rollback.status_code == 200
    body = rollback.json()
    assert body["family"] == "legacy_v1"
    assert body["control"] == "legacy_reactivate_rollback"
    assert body["selector_epoch"] == 3
    assert body["activation"]["id"] == legacy_activation.id
    assert body["activation"]["epoch"] == 1

    # The original legacy activation was neither appended nor resealed.
    assert _count(org, "authority_policy_activations") == 1
    persisted = AuthorityPolicyStore(org.db).get_activation(legacy_activation.id)
    assert persisted.activation_digest == legacy_activation.activation_digest
    assert persisted.request_id == legacy_activation.request_id
    selector = AuthorityPolicyStore(org.db).get_authority_selector(TEAM)
    assert selector.family == "legacy_v1"
    assert selector.legacy_activation_id == legacy_activation.id
    assert selector.selector_epoch == 3

    # Readback projects the selected legacy family, not the abandoned v2 release.
    readback = client.get(BASE).json()
    assert readback["family"] == "legacy_v1"
    assert readback["contract_version"] == "v1"
    assert readback["selector_epoch"] == 3
    assert readback["active"]["activation_id"] == legacy_activation.id


def test_legacy_route_requires_an_observed_selector_base(client_with_runtime):
    client, org = client_with_runtime
    _seed_agent(org)
    release, _ = _legacy_release_and_activation(org)

    absent = client.post(f"{BASE}/activations", json={
        "release_id": release.id, "expected_previous_epoch": 1,
        "request_id": "REQ-absent", "action": "activate",
        "acknowledge_shared_credential_attribution": True,
    })
    assert absent.status_code == 422

    stale = client.post(f"{BASE}/activations", json={
        "release_id": release.id, "expected_previous_epoch": 1,
        "expected_selector_id": EMPTY_SELECTOR_ID,
        "request_id": "REQ-stale", "action": "activate",
        "acknowledge_shared_credential_attribution": True,
    })
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "selector_conflict"
    # No new selection was written on either refusal.
    assert _count(org, "authority_policy_activations") == 1
    selector = AuthorityPolicyStore(org.db).get_authority_selector(TEAM)
    assert selector.family == "legacy_v1" and selector.selector_epoch == 1


def test_v2_and_legacy_surfaces_keep_auth_and_eligibility_fail_closed(client_with_runtime):
    client, org = client_with_runtime
    _seed_agent(org)
    _seed_agent(org, "dev_agent", role="worker")

    ineligible = client.post(
        "/api/v1/orgs/alpha/agents/dev_agent/team-escalation-policy/v2/releases",
        json=_pair_body(base=None, expected=None),
    )
    assert ineligible.status_code == 404
    assert ineligible.json() == {"detail": {"code": "policy_surface_not_available"}}

    client.headers["Authorization"] = "Bearer wrong"
    unauthenticated = client.post(f"{BASE}/v2/releases",
                                  json=_pair_body(base=None, expected=None))
    assert unauthenticated.status_code == 401
    assert _count(org, "authority_policy_v2_releases") == 0
    assert _count(org, "authority_policy_active_selector") == 0


def test_v2_history_cursor_keeps_initial_snapshot(client_with_runtime):
    client, org = client_with_runtime
    _seed_agent(org)
    first = client.post(f"{BASE}/v2/releases", json=_pair_body(base=None, expected=None))
    selector = first.json()["receipt"]["selector_id"]
    second = client.post(f"{BASE}/v2/releases", json=_pair_body(
        base=selector, expected=selector, action="activate",
        create_id="req-create-0002", activation_id="req-activate-0002", title="Second",
    ))
    assert second.status_code == 201

    page1 = client.get(f"{BASE}/v2/history?limit=1").json()
    assert page1["items"][0]["release_id"] == second.json()["receipt"]["release_id"]
    cursor = page1["next_cursor"]
    assert cursor is not None

    third = client.post(f"{BASE}/v2/releases", json=_pair_body(
        base=second.json()["receipt"]["selector_id"],
        expected=second.json()["receipt"]["selector_id"], action="activate",
        create_id="req-create-0003", activation_id="req-activate-0003", title="Third",
    ))
    assert third.status_code == 201

    page2 = client.get(f"{BASE}/v2/history?limit=1&cursor={cursor}").json()
    assert page2["items"][0]["release_id"] == first.json()["receipt"]["release_id"]
    assert page2["next_cursor"] is None
    # A cursor from the v1 stream is not accepted by the v2 stream.
    legacy_cursor = client.get(f"{BASE}/history?limit=1").json()["next_cursor"]
    if legacy_cursor is not None:
        assert client.get(f"{BASE}/v2/history?cursor={legacy_cursor}").status_code == 422


def _shipping_legacy_release_body():
    policy = ENGINEERING_PRE_ESCALATION_POLICY
    return {
        "based_on_release_id": None,
        "title": "Edited authority policy",
        "normative_text": "Bounded normative policy text.",
        "clauses": [
            {"id": clause.id, "category": clause.category,
             "condition": clause.condition, "action": clause.action}
            for clause in policy.clauses
        ],
        "continuation_phrase": CONTINUE_ROUTINE_PHRASE,
        "request_id": "REQ-create-legacy-empty",
    }


def test_empty_to_first_legacy_selection_is_selector_epoch_one(client_with_runtime):
    """A legacy family bootstrap away from the genuinely-empty selector is valid.

    The new legacy selector is at selector epoch 1 with the empty initializer as
    its predecessor; this is a shipping-route path the recovered patch could not
    reach before the selector value model accepted the empty predecessor.
    """
    client, org = client_with_runtime
    _seed_agent(org)
    created = client.post(f"{BASE}/releases",
                          json=_shipping_legacy_release_body()).json()["release"]
    assert client.get(BASE).json()["family"] == "empty"

    activated = client.post(f"{BASE}/activations", json={
        "release_id": created["id"], "expected_previous_epoch": 0,
        "expected_selector_id": None, "request_id": "REQ-empty-v1",
        "action": "activate",
        "acknowledge_shared_credential_attribution": True,
    })
    assert activated.status_code == 200
    body = activated.json()
    assert body["family"] == "legacy_v1"
    assert body["control"] == "legacy_activate"
    assert body["selector_epoch"] == 1
    assert body["previous_selector_id"] == EMPTY_SELECTOR_ID
    assert body["activation"]["action"] == "bootstrap"

    selector = AuthorityPolicyStore(org.db).get_authority_selector(TEAM)
    assert selector.family == "legacy_v1"
    assert selector.selector_epoch == 1
    assert selector.previous_selector_id == EMPTY_SELECTOR_ID
    readback = client.get(BASE).json()
    assert readback["family"] == "legacy_v1"
    assert readback["active"]["release"]["id"] == created["id"]

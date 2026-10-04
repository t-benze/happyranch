from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

BASE = "/api/v1/orgs/alpha/workflows/cutover"


def test_scoped_founder_request_and_historical_replay(client_with_runtime) -> None:
    client, org = client_with_runtime
    initial = client.get(BASE)
    assert initial.status_code == 200
    assert initial.json()["generation"] == 1
    response = client.post(BASE + "/requests", json={
        "action": "enable", "operation_key": "enable", "expected_generation": 1,
    })
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "enabled"
    disabled = client.post(BASE + "/requests", json={
        "action": "disable", "operation_key": "disable", "expected_generation": 4,
    })
    assert disabled.status_code == 200 and disabled.json()["state"] == "drained"
    replay = client.post(BASE + "/requests", json={
        "action": "enable", "operation_key": "enable", "expected_generation": 1,
    })
    assert replay.json()["request_event_id"] == response.json()["request_event_id"]
    assert replay.json()["replayed"] and replay.json()["state"] == "drained"
    preflight = client.get(BASE + "/downgrade-preflight")
    assert preflight.status_code == 200 and not preflight.json()["eligible"]
    assert org.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


@pytest.mark.parametrize("body,status,code", [
    ({"actor": "founder", "expected_generation": True}, 403, "body_identity_rejected"),
    ({"verified": True}, 403, "body_identity_rejected"),
    ({"action": "enable", "operation_key": "ok", "expected_generation": True}, 422, "cutover_invalid_request"),
    ({"action": "enable", "operation_key": "é", "expected_generation": 1}, 422, "cutover_invalid_request"),
    ({"action": "enable", "operation_key": "ok", "expected_generation": 1, "unknown": 1}, 422, "cutover_invalid_request"),
    ({"action": "enable", "operation_key": "ok", "expected_generation": 2}, 409, "cutover_generation_stale"),
    ({"action": "disable", "operation_key": "ok", "expected_generation": 1}, 409, "cutover_transition_not_allowed"),
])
def test_identity_strict_input_and_domain_precedence_preserve_database(
    client_with_runtime, body: dict, status: int, code: str,
) -> None:
    client, org = client_with_runtime
    before = tuple(org.db._conn.iterdump())
    response = client.post(BASE + "/requests", json=body)
    assert response.status_code == status, response.text
    assert response.json()["detail"] == {"code": code}
    assert tuple(org.db._conn.iterdump()) == before


def test_bearer_and_actual_org_precede_body_and_storage(client_with_runtime) -> None:
    client, org = client_with_runtime
    no_bearer = TestClient(client.app)
    before = tuple(org.db._conn.iterdump())
    assert no_bearer.post(BASE + "/requests", json={"actor": "forged"}).status_code == 403
    assert no_bearer.get(BASE).status_code == 403
    assert client.post(BASE.replace("alpha", "foreign") + "/requests", json={"actor": "forged"}).status_code == 404
    assert client.post(BASE + "/requests?session_id=pretend", json={}).status_code == 403
    assert tuple(org.db._conn.iterdump()) == before


def test_auth_org_and_safe_syntax_precedence_on_actual_wire(client_with_runtime) -> None:
    client, org = client_with_runtime
    before = tuple(org.db._conn.iterdump())
    unauthenticated = TestClient(client.app)
    assert unauthenticated.post(BASE + "/requests", content=b"{bad").status_code == 403
    assert client.post(BASE.replace("alpha", "foreign") + "/requests", content=b"{bad").status_code == 404
    response = client.post(BASE + "/requests", content=b"{bad")
    assert response.status_code == 422 and response.json()["detail"] == {"code": "cutover_invalid_request"}
    assert tuple(org.db._conn.iterdump()) == before


def test_pending_reconciliation_corruption_and_replay_error_categories(client_with_runtime) -> None:
    client, org = client_with_runtime
    org.db.execute("INSERT INTO workflow_recovery_claims VALUES ('owner','workflow_task','workflow_recovery','token','effect','claimed','now')")
    org.db._conn.commit()
    body = {"action": "enable", "operation_key": "enable", "expected_generation": 1}
    result = client.post(BASE + "/requests", json=body)
    assert result.status_code == 200
    assert result.json()["state"] == "enable_requested" and result.json()["reconciliation_required"]
    before = tuple(org.db._conn.iterdump())
    conflict = client.post(BASE + "/requests", json={**body, "expected_generation": 2})
    assert conflict.status_code == 409 and conflict.json()["detail"] == {"code": "cutover_operation_conflict"}
    assert tuple(org.db._conn.iterdump()) == before
    org.db.execute("UPDATE workflow_cutover_events SET event_digest='forged' WHERE event_seq=2")
    org.db._conn.commit()
    before = tuple(org.db._conn.iterdump())
    response = client.post(BASE + "/requests", json=body)
    assert response.status_code == 500 and response.json()["detail"] == {"code": "cutover_storage_corrupt"}
    assert client.get(BASE).status_code == 500
    assert tuple(org.db._conn.iterdump()) == before


def test_actual_second_org_does_not_disclose_or_replay_first_org_key(client_with_runtime) -> None:
    import asyncio
    from tests.daemon.test_org_state import _seed_org

    client, org = client_with_runtime
    state = client.app.state.daemon
    _seed_org(state.runtime.orgs_dir / "beta")
    beta = asyncio.run(state.add_org("beta"))
    try:
        body = {"action": "enable", "operation_key": "same-key", "expected_generation": 1}
        alpha = client.post(BASE + "/requests", json=body)
        assert alpha.status_code == 200
        result = client.get(BASE.replace("alpha", "beta"))
        assert result.json()["org_slug"] == "beta" and result.json()["generation"] == 1
        request = client.post(BASE.replace("alpha", "beta") + "/requests", json=body)
        assert request.status_code == 200 and not request.json()["replayed"]
        assert request.json()["org_slug"] == "beta"
        assert request.json()["events"][1]["event_digest"] != alpha.json()["events"][1]["event_digest"]
        assert client.get(BASE).json()["generation"] == 4
    finally:
        beta.close()


@pytest.mark.parametrize("field", ["actor_id", "org_slug", "owner_id", "proof_bytes", "verification", "receipt_id"])
def test_server_provenance_claims_precede_invalid_generation(client_with_runtime, field: str) -> None:
    client, org = client_with_runtime
    before = tuple(org.db._conn.iterdump())
    response = client.post(BASE + "/requests", json={field: "forged", "expected_generation": True})
    assert response.status_code == 403 and response.json()["detail"] == {"code": "body_identity_rejected"}
    assert tuple(org.db._conn.iterdump()) == before

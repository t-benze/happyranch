from __future__ import annotations

import base64
import copy

import pytest
from fastapi.testclient import TestClient

from runtime.models import TaskRecord, TaskStatus
from runtime.orchestrator.teams import TeamManager
from tests.workflows.test_template_store import VALID_DEFINITION


BASE = "/api/v1/orgs/alpha/workflows/templates"
_TEMPLATE_TABLES = (
    "workflow_template_drafts",
    "workflow_template_versions",
    "workflow_template_identities",
    "workflow_template_identity_versions",
    "workflow_template_publish_operations",
)
_PROTECTED_TABLES = (
    *_TEMPLATE_TABLES,
    "workflow_cutover_state",
    "workflow_cutover_events",
    "workflow_activations",
    "workflow_activation_operations",
    "workflow_instances",
    "workflow_dispatch_operations",
    "workflow_dispatch_outbox",
    "workflow_authority_pointers",
    "tasks",
    "audit_log",
)


def _active_session(org, agent: str) -> str:
    task = TaskRecord(
        id=org.db.next_task_id(),
        assigned_agent=agent,
        team=org.teams.team_for_agent(agent) or "engineering",
        brief="publish template",
        status=TaskStatus.IN_PROGRESS,
    )
    org.db.insert_task(task)
    session_id = f"sess-{agent}"
    org.sessions.set_active(task.id, agent, session_id, org_slug="alpha")
    return session_id


def _body(**updates) -> dict:
    body = {
        "operation_key": "op-1",
        "template_name": "product-design",
        "expected_current_version": 0,
        "definition": copy.deepcopy(VALID_DEFINITION),
    }
    body.update(updates)
    return body


def _counts(org) -> tuple[int, ...]:
    return tuple(
        org.db._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in _TEMPLATE_TABLES
    )


def _rows(org) -> dict[str, tuple[tuple, ...]]:
    return {
        table: tuple(map(tuple, org.db._conn.execute(f"SELECT * FROM {table}")))
        for table in _PROTECTED_TABLES
    }


def _agent_client(client: TestClient) -> TestClient:
    return TestClient(client.app)


def test_verified_current_manager_publish_list_show_and_founder_publish(
    client_with_runtime,
) -> None:
    founder_client, org = client_with_runtime
    session_id = _active_session(org, "engineering_head")
    agent_client = _agent_client(founder_client)

    published = agent_client.post(
        f"{BASE}/publish", params={"session_id": session_id}, json=_body(),
    )
    assert published.status_code == 201, published.text
    item = published.json()
    assert item["namespace"] == "org/alpha/team/engineering"
    assert item["version"] == 1
    assert item["publisher"]["principal_id"] == "engineering_head"
    assert base64.b64decode(item["definition_bytes_base64"]) == item["definition_json"].encode()

    listed = founder_client.get(BASE, params={"team_slug": "engineering"})
    assert listed.status_code == 200
    assert listed.json() == {"templates": [item]}
    shown = founder_client.get(f"{BASE}/engineering/product-design/1")
    assert shown.status_code == 200
    assert shown.json() == item

    founder_body = _body(
        operation_key="founder-op", template_name="founder-design",
        team_slug="engineering",
    )
    founder = founder_client.post(f"{BASE}/publish", json=founder_body)
    assert founder.status_code == 201, founder.text
    assert founder.json()["publisher"] == {
        "principal_id": "founder",
        "principal_kind": "human",
        "proof_kind": "founder_bearer",
    }


@pytest.mark.parametrize(
    ("agent", "body_updates", "expected_code"),
    [
        ("dev_agent", {}, "manager_required"),
        ("content_manager", {"team_slug": "engineering"}, "namespace_claim_rejected"),
    ],
)
def test_non_manager_and_other_team_manager_refuse_with_zero_residue(
    client_with_runtime, agent: str, body_updates: dict, expected_code: str,
) -> None:
    founder_client, org = client_with_runtime
    session_id = _active_session(org, agent)
    response = _agent_client(founder_client).post(
        f"{BASE}/publish",
        params={"session_id": session_id},
        json=_body(**body_updates),
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == expected_code
    assert _counts(org) == (0, 0, 0, 0, 0)


def test_removed_or_replaced_manager_is_rechecked_at_commit(client_with_runtime) -> None:
    founder_client, org = client_with_runtime
    session_id = _active_session(org, "engineering_head")
    original = org.teams.manager_for_team("engineering")
    org.teams._teams["engineering"] = TeamManager(
        name="replacement_head", team="engineering", workers=original.workers,
    )

    response = _agent_client(founder_client).post(
        f"{BASE}/publish", params={"session_id": session_id}, json=_body(),
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "manager_required"
    assert _counts(org) == (0, 0, 0, 0, 0)


@pytest.mark.parametrize("field", ["publisher", "principal", "namespace", "org_slug", "task_id"])
def test_forged_identity_fields_refuse_with_zero_residue(client_with_runtime, field: str) -> None:
    founder_client, org = client_with_runtime
    session_id = _active_session(org, "engineering_head")
    response = _agent_client(founder_client).post(
        f"{BASE}/publish",
        params={"session_id": session_id},
        json=_body(**{field: "forged"}),
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "body_identity_rejected"
    assert _counts(org) == (0, 0, 0, 0, 0)


def test_route_error_codes_are_stable_and_refusals_have_zero_residue(
    client_with_runtime,
) -> None:
    founder_client, org = client_with_runtime
    session_id = _active_session(org, "engineering_head")
    agent_client = _agent_client(founder_client)
    first = agent_client.post(
        f"{BASE}/publish", params={"session_id": session_id}, json=_body(),
    )
    assert first.status_code == 201
    before = _counts(org)

    stale = agent_client.post(
        f"{BASE}/publish", params={"session_id": session_id},
        json=_body(operation_key="op-stale"),
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "template_version_cas_stale"
    assert _counts(org) == before

    changed = _body()
    changed["definition"]["description"] = "Changed but valid definition"
    conflict = agent_client.post(
        f"{BASE}/publish", params={"session_id": session_id}, json=changed,
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "template_publish_operation_conflict"
    assert _counts(org) == before


def test_publish_never_mutates_cutover_activation_task_outbox_or_authority_rows(
    client_with_runtime,
) -> None:
    founder_client, org = client_with_runtime
    session_id = _active_session(org, "engineering_head")
    conn = org.db._conn
    protected = (
        "workflow_cutover_state", "workflow_cutover_events", "workflow_activations",
        "workflow_activation_operations", "workflow_instances",
        "workflow_dispatch_operations", "workflow_dispatch_outbox",
        "workflow_authority_pointers", "tasks", "audit_log",
    )
    before = {table: tuple(map(tuple, conn.execute(f"SELECT * FROM {table}"))) for table in protected}

    response = _agent_client(founder_client).post(
        f"{BASE}/publish", params={"session_id": session_id}, json=_body(),
    )
    assert response.status_code == 201, response.text
    after = {table: tuple(map(tuple, conn.execute(f"SELECT * FROM {table}"))) for table in protected}
    assert after == before


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"operation_key": "op"},
        _body(expected_current_version=True),
        _body(unexpected="value"),
    ],
)
def test_malformed_requests_use_stable_invalid_request_code(
    client_with_runtime, body: dict,
) -> None:
    founder_client, org = client_with_runtime
    response = founder_client.post(f"{BASE}/publish", json=body)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_request"
    assert _counts(org) == (0, 0, 0, 0, 0)


@pytest.mark.parametrize(
    "schema_version",
    [True, False, 1.0, "1"],
    ids=["boolean-true", "boolean-false", "float", "numeric-string"],
)
def test_schema_version_requires_exact_integer_one_without_residue(
    client_with_runtime, schema_version: object,
) -> None:
    founder_client, org = client_with_runtime
    session_id = _active_session(org, "engineering_head")
    body = _body()
    body["definition"]["schema_version"] = schema_version
    before_counts = _counts(org)
    before_rows = _rows(org)

    response = _agent_client(founder_client).post(
        f"{BASE}/publish", params={"session_id": session_id}, json=body,
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_template_definition"
    assert before_counts == (0, 0, 0, 0, 0)
    assert _counts(org) == before_counts
    assert _rows(org) == before_rows

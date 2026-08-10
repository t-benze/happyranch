"""Route contract tests for THR-055 B2 custom-skill APIs."""
from __future__ import annotations

import pytest

from runtime.models import TaskRecord


BASE = "/api/v1/orgs/alpha/custom-skills"
_FORBIDDEN_IDENTITY = (
    "task_id", "session_id", "proposer_agent", "agent", "agent_name", "org",
    "org_slug", "actor", "eligibility", "permission", "permissions",
)


def _body(slug: str = "test-skill", skill_md: str = "# Test\n\nOne") -> dict:
    return {"slug": slug, "name": "Test skill", "description": "test", "skill_md": skill_md}


def _custom_counts(org) -> dict[str, int]:
    conn = getattr(org.db, "_conn", org.db)
    tables = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'custom_skill_%'"
    )]
    return {table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tables}


def _create(client, slug: str = "test-skill", skill_md: str = "# Test\n\nOne") -> dict:
    response = client.post(BASE, json=_body(slug, skill_md))
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize("key", _FORBIDDEN_IDENTITY)
def test_agent_create_rejects_every_identity_claim_without_custom_rows(client_with_runtime, key):
    client, org = client_with_runtime
    org.sessions.set_active("TASK-IDENTITY", "frontend_engineer", "sess-identity", org_slug="alpha")
    before = _custom_counts(org)
    client.headers.pop("Authorization", None)
    response = client.post(
        f"{BASE}/agent-create", params={"session_id": "sess-identity"},
        json={**_body("frontend-development"), key: [] if key == "permissions" else "spoof"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "body_identity_rejected"
    assert _custom_counts(org) == before


def test_agent_create_rejects_another_agents_originated_skill_without_mutation(client_with_runtime):
    client, org = client_with_runtime
    org.db.insert_task(TaskRecord(id="TASK-A", brief="a"))
    org.db.insert_task(TaskRecord(id="TASK-B", brief="b"))
    org.sessions.set_active("TASK-A", "product_lead", "sess-a", org_slug="alpha")
    org.sessions.set_active("TASK-B", "frontend_engineer", "sess-b", org_slug="alpha")
    client.headers.pop("Authorization", None)
    created = client.post(
        f"{BASE}/agent-create", params={"session_id": "sess-a"},
        json=_body("product-manager-prd"),
    )
    assert created.status_code == 201, created.text
    before = _custom_counts(org)
    response = client.post(
        f"{BASE}/agent-create", params={"session_id": "sess-b"},
        json=_body("product-manager-prd", "# Attempt"),
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "not_origin_owner"
    assert _custom_counts(org) == before


@pytest.mark.parametrize("method,path,payload", [
    ("post", "", _body()),
    ("patch", "/{skill_id}", {"name": "Renamed"}),
    ("post", "/{skill_id}/versions", {"skill_md": "# Version two"}),
    ("post", "/{skill_id}/retire", {"reason": "test"}),
    ("post", "/{skill_id}/restore", None),
    ("put", "/{skill_id}/eligibility", [{"scope_type": "org", "scope_target": None, "effect": "allow"}]),
])
def test_founder_mutations_require_valid_bearer_without_custom_mutation(
    client_with_runtime, method, path, payload,
):
    client, org = client_with_runtime
    created = _create(client)
    before = _custom_counts(org)
    client.headers.clear()
    url = BASE + path.format(skill_id=created["skill_id"])
    response = getattr(client, method)(url, json=payload, headers={"If-Match": str(created["version_id"])})
    assert response.status_code == 401
    assert _custom_counts(org) == before
    response = getattr(client, method)(url, json=payload, headers={"Authorization": "Bearer wrong", "If-Match": str(created["version_id"])})
    assert response.status_code == 401
    assert _custom_counts(org) == before


def test_eligibility_rejections_are_atomic(client_with_runtime):
    client, org = client_with_runtime
    created = _create(client)
    skill_id, revision = created["skill_id"], created["version_id"]
    rules = [{"scope_type": "org", "scope_target": None, "effect": "allow"}]
    before = _custom_counts(org)
    stale = client.put(f"{BASE}/{skill_id}/eligibility", json=rules, headers={"If-Match": "stale"})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale_revision"
    assert _custom_counts(org) == before
    unknown = client.put(f"{BASE}/{skill_id}/eligibility", json=[{"scope_type": "agent", "scope_target": "nobody", "effect": "allow"}], headers={"If-Match": str(revision)})
    assert unknown.status_code == 422 and unknown.json()["detail"]["code"] == "unknown_target"
    assert _custom_counts(org) == before


@pytest.mark.parametrize("state", ["retired", "invalid"])
def test_ineligible_skill_cannot_write_rules(client_with_runtime, state):
    client, org = client_with_runtime
    created = _create(client)
    skill_id = created["skill_id"]
    if state == "retired":
        assert client.post(f"{BASE}/{skill_id}/retire", json={}).status_code == 200
        revision = created["version_id"]
    else:
        invalid = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": "not markdown"})
        assert invalid.status_code == 201
        revision = invalid.json()["version_id"]
    before = _custom_counts(org)
    response = client.put(f"{BASE}/{skill_id}/eligibility", json=[], headers={"If-Match": str(revision)})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "version_not_eligible"
    assert _custom_counts(org) == before


def test_version_diff_returns_metadata_and_unified_content_diff(client_with_runtime):
    client, _org = client_with_runtime
    first = _create(client, skill_md="# Test\n\nOld line")
    second = client.post(f"{BASE}/{first['skill_id']}/versions", json={"skill_md": "# Test\n\nNew line"})
    assert second.status_code == 201
    response = client.get(f"{BASE}/{first['skill_id']}/versions/{first['version_id']}/diff/{second.json()['version_id']}")
    assert response.status_code == 200
    data = response.json()
    assert data["a"]["content_hash"] == first["content_hash"]
    assert data["b"]["author_kind"] == "human"
    assert "-Old line" in data["diff"] and "+New line" in data["diff"]
    other = _create(client, slug="other-skill")
    foreign = client.get(f"{BASE}/{first['skill_id']}/versions/{first['version_id']}/diff/{other['version_id']}")
    assert foreign.status_code == 404


def test_custom_skill_flow_never_writes_lifecycle_tables(client_with_runtime):
    client, org = client_with_runtime
    conn = getattr(org.db, "_conn", org.db)
    lifecycle_tables = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'skill_lifecycle_%'"
    )]
    created = _create(client)
    skill_id = created["skill_id"]
    version = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": "# Test\n\nTwo"}).json()
    assert client.post(f"{BASE}/{skill_id}/retire", json={}).status_code == 200
    assert client.post(f"{BASE}/{skill_id}/restore").status_code == 200
    rules = [{"scope_type": "org", "scope_target": None, "effect": "allow"}]
    assert client.post(f"{BASE}/{skill_id}/eligibility/preview", json=rules).status_code == 200
    assert client.put(f"{BASE}/{skill_id}/eligibility", json=rules, headers={"If-Match": str(version["version_id"])}).status_code == 200
    assert all(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0 for table in lifecycle_tables)

"""Route contract tests for THR-055 B2 custom-skill APIs."""
from __future__ import annotations

import hashlib

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


def _add_agent(org, agent: str = "dev_agent") -> None:
    agents_dir = org.root / "org" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / f"{agent}.md").write_text(
        "---\n"
        f"name: {agent}\n"
        "team: engineering\n"
        "role: worker\n"
        "executor: claude\n"
        "---\n\n"
        "You are a test agent.\n"
    )


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


def test_agent_create_is_available_to_every_verified_agent(client_with_runtime):
    client, org = client_with_runtime
    org.db.insert_task(TaskRecord(id="TASK-NON-PILOT", brief="create a custom skill"))
    org.sessions.set_active("TASK-NON-PILOT", "dev_agent", "sess-non-pilot", org_slug="alpha")
    client.headers.pop("Authorization", None)
    response = client.post(
        f"{BASE}/agent-create", params={"session_id": "sess-non-pilot"},
        json=_body("frontend-development"),
    )
    assert response.status_code == 201, response.text
    assert response.json()["provenance"]["agent_name"] == "dev_agent"


def test_skills_agent_returns_only_b2_custom_skill_mapping(client_with_runtime):
    client, org = client_with_runtime
    org.db.insert_task(TaskRecord(id="TASK-B2", brief="create a custom skill"))
    org.sessions.set_active("TASK-B2", "dev_agent", "sess-b2", org_slug="alpha")
    client.headers.pop("Authorization", None)
    response = client.post(
        "/api/v1/orgs/alpha/skills/agent",
        params={"session_id": "sess-b2"},
        json=_body("b2-agent-skill"),
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert set(payload) == {"skill", "version", "hidden_reason", "provenance"}
    assert payload["skill"]["origin_kind"] == "agent"
    assert payload["version"]["source_task_id"] == "TASK-B2"
    assert payload["provenance"] == {
        "verified_org": "alpha",
        "task_id": "TASK-B2",
        "agent_name": "dev_agent",
        "session_id": "sess-b2",
        "task_brief_digest": payload["version"]["task_brief_digest"],
    }


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
    preview = client.post(f"{BASE}/{skill_id}/eligibility/preview", json=rules)
    assert preview.status_code == 200 and preview.json()["revision"] == revision
    advanced = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": "# Test\n\nTwo"})
    assert advanced.status_code == 201
    conn = getattr(org.db, "_conn", org.db)
    before = (
        conn.execute("SELECT count(*) FROM custom_skill_eligibility_rules").fetchone()[0],
        conn.execute("SELECT count(*) FROM custom_skill_eligibility_events").fetchone()[0],
    )
    stale = client.put(f"{BASE}/{skill_id}/eligibility", json=rules, headers={"If-Match": str(revision)})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale_revision"
    assert (
        conn.execute("SELECT count(*) FROM custom_skill_eligibility_rules").fetchone()[0],
        conn.execute("SELECT count(*) FROM custom_skill_eligibility_events").fetchone()[0],
    ) == before
    unknown = client.put(f"{BASE}/{skill_id}/eligibility", json=[{"scope_type": "agent", "scope_target": "nobody", "effect": "allow"}], headers={"If-Match": str(advanced.json()["version_id"])})
    assert unknown.status_code == 422 and unknown.json()["detail"]["code"] == "unknown_target"
    assert (
        conn.execute("SELECT count(*) FROM custom_skill_eligibility_rules").fetchone()[0],
        conn.execute("SELECT count(*) FROM custom_skill_eligibility_events").fetchone()[0],
    ) == before


def test_custom_create_reuses_package_validator_for_protected_and_normal_slugs(client_with_runtime):
    client, org = client_with_runtime
    protected = client.post(BASE, json=_body("start-task"))
    assert protected.status_code == 409
    assert protected.json()["detail"]["code"] == "protected_slug"
    assert _custom_counts(org) == {table: 0 for table in _custom_counts(org)}
    assert _create(client, slug="normal-custom-skill")["validation_state"] == "valid"


def test_catalog_and_detail_project_missing_eligibility_as_hidden(client_with_runtime):
    client, _org = client_with_runtime
    created = _create(client)
    skill_id, revision = created["skill_id"], created["version_id"]

    catalog = client.get(f"{BASE}/catalog")
    assert catalog.status_code == 200
    listed = next(skill for skill in catalog.json()["skills"] if skill["id"] == skill_id)
    assert listed["hidden_reason"] == "no_eligibility_policy"
    assert client.get(f"{BASE}/{skill_id}").json()["hidden_reason"] == "no_eligibility_policy"

    rules = [{"scope_type": "org", "scope_target": None, "effect": "allow"}]
    assert client.put(f"{BASE}/{skill_id}/eligibility", json=rules, headers={"If-Match": str(revision)}).status_code == 200
    catalog = client.get(f"{BASE}/catalog")
    listed = next(skill for skill in catalog.json()["skills"] if skill["id"] == skill_id)
    assert listed["hidden_reason"] is None
    assert client.get(f"{BASE}/{skill_id}").json()["hidden_reason"] is None


def test_b2_recover_deletes_only_corrupt_version_with_audit(
    client_with_runtime, monkeypatch,
):
    """The retained operator route repairs a refused B2 canonical package."""
    from runtime.infrastructure.artifact_store import ArtifactStore
    from runtime.orchestrator._paths import OrgPaths
    from runtime.skills.canonical_store import CanonicalSkillStore, _make_writable_for_removal

    client, org = client_with_runtime
    created = _create(client, slug="recoverable-b2", skill_md="# Recover\n\nOriginal")
    content_hash = created["content_hash"]
    version = str(created["version_id"])
    monkeypatch.setenv("HAPPYRANCH_CANONICAL_STORE_ROOT", str(org.root / "canonical-store"))
    conn = getattr(org.db, "_conn", org.db)
    record = conn.execute(
        "SELECT * FROM custom_skill_versions WHERE id = ?", (created["version_id"],)
    ).fetchone()
    artifact = ArtifactStore(OrgPaths(org.root).artifacts_dir).read(record["content_artifact_key"])
    source = org.root / "recovery-source"
    source.mkdir()
    (source / "SKILL.md").write_bytes(artifact)
    expected_tree_hash = hashlib.sha256(b"SKILL.md\x00" + artifact + b"\x00").hexdigest()
    store = CanonicalSkillStore()
    package = store.build_from_source("recoverable-b2", version, content_hash, source, verify_source_hash=expected_tree_hash)
    _make_writable_for_removal(package)
    (package / "SKILL.md").write_text("# Recover\n\nTampered")

    with pytest.raises(Exception, match="skills recover"):
        store.build_from_source("recoverable-b2", version, content_hash, source, verify_source_hash=expected_tree_hash)
    response = client.post(
        "/api/v1/orgs/alpha/skills/recover",
        json={"slug": "recoverable-b2", "version": version, "content_hash": content_hash},
    )
    assert response.status_code == 200, response.text
    assert response.json()["skill_id"] == created["skill_id"]
    assert response.json()["artifact_key"] == record["content_artifact_key"]
    assert not package.exists()
    audit = conn.execute(
        "SELECT source, ok, version, reason_codes FROM skill_validation_events WHERE skill_id = ? ORDER BY id DESC LIMIT 1",
        (created["skill_id"],),
    ).fetchone()
    assert dict(audit) == {"source": "operator_recovery", "ok": 1, "version": version, "reason_codes": '["operator_recovery"]'}

    foreign = client.post(
        "/api/v1/orgs/alpha/skills/recover",
        json={"slug": "recoverable-b2", "version": version, "content_hash": "0" * 64},
    )
    assert foreign.status_code == 400


def test_effective_custom_skill_distinguishes_next_session_from_materialized(client_with_runtime):
    client, org = client_with_runtime
    _add_agent(org)
    created = _create(client)
    skill_id, version_id = created["skill_id"], created["version_id"]
    rules = [{"scope_type": "org", "scope_target": None, "effect": "allow"}]
    assert client.put(f"{BASE}/{skill_id}/eligibility", json=rules, headers={"If-Match": str(version_id)}).status_code == 200

    response = client.get("/api/v1/orgs/alpha/agents/dev_agent/skills/effective")
    assert response.status_code == 200
    projected = next(skill for skill in response.json()["skills"] if skill["skill_id"] == skill_id)
    assert projected["materialized_at"] is None
    assert projected["materialized_session_id"] is None
    assert projected["materialization_state"] == "visible_next_session"

    conn = getattr(org.db, "_conn", org.db)
    conn.execute(
        """INSERT INTO custom_skill_materializations
           (skill_id,agent_name,task_id,session_context,session_id,version_id,content_hash,success,created_at)
           VALUES (?,?,NULL,'dream',?,?,?,1,?)""",
        (skill_id, "dev_agent", "sess-materialized", version_id, created["content_hash"], "2026-08-11T14:00:00+00:00"),
    )
    conn.commit()
    response = client.get("/api/v1/orgs/alpha/agents/dev_agent/skills/effective")
    projected = next(skill for skill in response.json()["skills"] if skill["skill_id"] == skill_id)
    assert projected["materialized_at"] == "2026-08-11T14:00:00+00:00"
    assert projected["materialized_session_id"] == "sess-materialized"
    assert projected["materialization_state"] == "materialized"


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

"""Route contract tests for THR-055 B2 custom-skill APIs."""
from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from runtime.models import TaskRecord


BASE = "/api/v1/orgs/alpha/custom-skills"
_FORBIDDEN_IDENTITY = (
    "task_id", "session_id", "proposer_agent", "agent", "agent_name", "org",
    "org_slug", "actor", "eligibility", "permission", "permissions",
)

# Supported authoring contract (founder-approved, THR-169): YAML
# frontmatter first, then a Markdown heading.
_FM_BODY = "---\nname: Test skill\ndescription: test\n---\n\n# Test\n\nOne\n"


def _body(slug: str = "test-skill", skill_md: str = _FM_BODY) -> dict:
    return {"slug": slug, "name": "Test skill", "description": "test", "skill_md": skill_md}


def _custom_counts(org, conn=None) -> dict[str, int]:
    conn = conn or getattr(org.db, "_conn", org.db)
    tables = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'custom_skill_%'"
    )]
    return {table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tables}


def _artifact_keys(org) -> set[str]:
    from runtime.infrastructure.artifact_store import ArtifactStore
    from runtime.orchestrator._paths import OrgPaths
    store = ArtifactStore(OrgPaths(org.root).artifacts_dir)
    return {info.name for info in store.list_artifacts()}


def _empty_artifact_dirs(org) -> list[str]:
    """Directories under the custom-skills artifact tree left completely
    empty — compensation must remove the artifact file AND any now-empty
    parent directories (digest dir, slug dir)."""
    from runtime.orchestrator._paths import OrgPaths
    root = OrgPaths(org.root).artifacts_dir / "custom-skills"
    if not root.exists():
        return []
    return sorted(
        str(path.relative_to(root))
        for path in root.rglob("*") if path.is_dir() and not any(path.iterdir())
    )


def _residue_snapshot(org, skill_id: str, conn=None) -> dict:
    """Snapshot every zero-residue dimension for one skill: table counts
    (version/event/eligibility/materialization rows), current pointer,
    parent/current lineage, artifacts, and newly-created empty directories."""
    conn = conn or getattr(org.db, "_conn", org.db)
    row = conn.execute("SELECT * FROM custom_skills WHERE id=?", (skill_id,)).fetchone()
    current = row["current_version_id"] if row else None
    parent = None
    if current is not None:
        parent_row = conn.execute(
            "SELECT parent_version_id FROM custom_skill_versions WHERE id=?", (current,)
        ).fetchone()
        parent = parent_row["parent_version_id"] if parent_row else None
    return {
        "counts": _custom_counts(org, conn),
        "current_version_id": current,
        "current_parent_version_id": parent,
        "artifacts": _artifact_keys(org),
        "empty_dirs": _empty_artifact_dirs(org),
    }


def _create(client, slug: str = "test-skill", skill_md: str = _FM_BODY) -> dict:
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
        json=_body("product-manager-prd", "---\nname: Attempt\n---\n\n# Attempt\n"),
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
    ("post", "/{skill_id}/versions", {"skill_md": "---\nname: Test skill\n---\n\n# Version two\n"}),
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
    advanced = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": "---\nname: Test skill\n---\n\n# Test\n\nTwo\n"})
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
    created = _create(client, slug="recoverable-b2", skill_md="---\nname: Recover\n---\n\n# Recover\n\nOriginal\n")
    current = client.post(
        f"{BASE}/{created['skill_id']}/versions",
        json={"skill_md": "---\nname: Recover\n---\n\n# Recover\n\nCurrent\n"},
    )
    assert current.status_code == 201, current.text
    content_hash = current.json()["content_hash"]
    version = str(current.json()["version_id"])
    monkeypatch.setenv("HAPPYRANCH_CANONICAL_STORE_ROOT", str(org.root / "canonical-store"))
    conn = getattr(org.db, "_conn", org.db)
    record = conn.execute(
        "SELECT * FROM custom_skill_versions WHERE id = ?", (current.json()["version_id"],)
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


def test_b2_recover_refuses_corrupt_historical_version_after_current_advances(
    client_with_runtime, monkeypatch,
):
    """Recovery cannot delete an otherwise valid historical B2 package."""
    from runtime.infrastructure.artifact_store import ArtifactStore
    from runtime.orchestrator._paths import OrgPaths
    from runtime.skills.canonical_store import CanonicalSkillStore, _make_writable_for_removal

    client, org = client_with_runtime
    created = _create(client, slug="stale-recoverable-b2", skill_md="---\nname: Recover\n---\n\n# Recover\n\nVersion A\n")
    current = client.post(
        f"{BASE}/{created['skill_id']}/versions",
        json={"skill_md": "---\nname: Recover\n---\n\n# Recover\n\nVersion B\n"},
    )
    assert current.status_code == 201, current.text
    monkeypatch.setenv("HAPPYRANCH_CANONICAL_STORE_ROOT", str(org.root / "canonical-store"))
    conn = getattr(org.db, "_conn", org.db)
    historical = conn.execute(
        "SELECT * FROM custom_skill_versions WHERE id = ?", (created["version_id"],)
    ).fetchone()
    artifact = ArtifactStore(OrgPaths(org.root).artifacts_dir).read(historical["content_artifact_key"])
    source = org.root / "stale-recovery-source"
    source.mkdir()
    (source / "SKILL.md").write_bytes(artifact)
    expected_tree_hash = hashlib.sha256(b"SKILL.md\x00" + artifact + b"\x00").hexdigest()
    package = CanonicalSkillStore().build_from_source(
        "stale-recoverable-b2",
        str(created["version_id"]),
        created["content_hash"],
        source,
        verify_source_hash=expected_tree_hash,
    )
    _make_writable_for_removal(package)
    (package / "SKILL.md").write_text("# Recover\n\nTampered")

    response = client.post(
        "/api/v1/orgs/alpha/skills/recover",
        json={
            "slug": "stale-recoverable-b2",
            "version": str(created["version_id"]),
            "content_hash": created["content_hash"],
        },
    )
    assert response.status_code == 409, response.text
    assert "current" in response.json()["detail"]
    assert package.exists()
    audit = conn.execute(
        "SELECT source, ok, version, reason_codes FROM skill_validation_events WHERE skill_id = ? ORDER BY id DESC LIMIT 1",
        (created["skill_id"],),
    ).fetchone()
    assert dict(audit) == {
        "source": "operator_recovery",
        "ok": 0,
        "version": None,
        "reason_codes": '["stale_current_version"]',
    }


def test_b2_recover_refuses_missing_and_ineligible_current_provenance(client_with_runtime):
    client, org = client_with_runtime
    created = _create(client, slug="ineligible-recoverable-b2")
    conn = getattr(org.db, "_conn", org.db)

    missing = client.post(
        "/api/v1/orgs/alpha/skills/recover",
        json={"slug": "ineligible-recoverable-b2", "version": "99999", "content_hash": created["content_hash"]},
    )
    assert missing.status_code == 404
    missing_audit = conn.execute(
        "SELECT skill_id, reason_codes FROM skill_validation_events WHERE slug = ? ORDER BY id DESC LIMIT 1",
        ("ineligible-recoverable-b2",),
    ).fetchone()
    assert dict(missing_audit) == {
        "skill_id": "custom:ineligible-recoverable-b2",
        "reason_codes": '["b2_provenance_not_found"]',
    }

    # Invalid bodies are now rejected atomically by POST /versions. Simulate
    # the legacy invalid current version (pre-cutover rows persisted before
    # rejection existed) to exercise the recover route's ineligibility gate.
    from runtime.skills.custom import service as custom_service
    conn.execute(
        """INSERT INTO custom_skill_versions
           (skill_id,parent_version_id,content_hash,content_artifact_key,skill_md_cache,
            validation_state,validator_version,validation_findings,created_at,
            author_kind,author_identity)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (created["skill_id"], created["version_id"], "0" * 64,
         "custom-skills/ineligible-recoverable-b2/legacy/SKILL.md", "not markdown",
         "invalid", "THR-055/1.0.0", '["SKILL.md must start with a heading"]',
         custom_service.now(), "human", "founder"),
    )
    version_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "UPDATE custom_skills SET current_version_id=? WHERE id=?",
        (version_id, created["skill_id"]),
    )
    conn.commit()
    refused = client.post(
        "/api/v1/orgs/alpha/skills/recover",
        json={
            "slug": "ineligible-recoverable-b2",
            "version": str(version_id),
            "content_hash": "0" * 64,
        },
    )
    assert refused.status_code == 409
    audit = conn.execute(
        "SELECT source, ok, version, reason_codes FROM skill_validation_events WHERE skill_id = ? ORDER BY id DESC LIMIT 1",
        (created["skill_id"],),
    ).fetchone()
    assert dict(audit) == {
        "source": "operator_recovery",
        "ok": 0,
        "version": None,
        "reason_codes": '["ineligible_current_version"]',
    }


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
        # Legacy invalid current version: pre-cutover rows persisted before
        # invalid rejection existed (POST /versions now rejects atomically).
        from runtime.skills.custom import service as custom_service
        conn = getattr(org.db, "_conn", org.db)
        conn.execute(
            """INSERT INTO custom_skill_versions
               (skill_id,parent_version_id,content_hash,content_artifact_key,skill_md_cache,
                validation_state,validator_version,validation_findings,created_at,
                author_kind,author_identity)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (skill_id, created["version_id"], "0" * 64,
             "custom-skills/invalid-legacy/SKILL.md", "not markdown",
             "invalid", "THR-055/1.0.0", '["SKILL.md must start with a heading"]',
             custom_service.now(), "human", "founder"),
        )
        revision = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "UPDATE custom_skills SET current_version_id=? WHERE id=?",
            (revision, skill_id),
        )
        conn.commit()
    before = _custom_counts(org)
    response = client.put(f"{BASE}/{skill_id}/eligibility", json=[], headers={"If-Match": str(revision)})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "version_not_eligible"
    assert _custom_counts(org) == before


def test_version_diff_returns_metadata_and_unified_content_diff(client_with_runtime):
    client, _org = client_with_runtime
    first = _create(client, skill_md="---\nname: Test\n---\n\n# Test\n\nOld line\n")
    second = client.post(f"{BASE}/{first['skill_id']}/versions", json={"skill_md": "---\nname: Test\n---\n\n# Test\n\nNew line\n"})
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
    version = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": "---\nname: Test skill\n---\n\n# Test\n\nTwo\n"}).json()
    assert client.post(f"{BASE}/{skill_id}/retire", json={}).status_code == 200
    assert client.post(f"{BASE}/{skill_id}/restore").status_code == 200
    rules = [{"scope_type": "org", "scope_target": None, "effect": "allow"}]
    assert client.post(f"{BASE}/{skill_id}/eligibility/preview", json=rules).status_code == 200
    assert client.put(f"{BASE}/{skill_id}/eligibility", json=rules, headers={"If-Match": str(version["version_id"])}).status_code == 200
    assert all(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0 for table in lifecycle_tables)


# ═══════════════════════════════════════════════════════════════════════════
# THR-169 frontmatter-first authoring contract + atomic version writes
# ═══════════════════════════════════════════════════════════════════════════

_INVALID_BODIES = [
    ("---\nname: [unclosed\n---\n# Test\n\nOne\n", "skill_md_malformed_frontmatter"),
    ("---\nname: x\n# no closing fence\n", "skill_md_unclosed_frontmatter"),
    ("---\n- a\n- b\n---\n# Test\n\nOne\n", "skill_md_frontmatter_not_mapping"),
    ("---\njust a string\n---\n# Test\n\nOne\n", "skill_md_frontmatter_not_mapping"),
    ("---\nname: x\n---\nplain text without a heading\n", "skill_md_no_heading"),
    ("---\nname: x\n---\n\n", "skill_md_no_heading"),
    ("plain text without frontmatter", "skill_md_no_frontmatter"),
    # malformed heading-LIKE candidates: hash-prefixed but NOT ATX headings
    # (1-6 hashes followed by whitespace/EOL) — invalid evidence, same rules
    ("#not-a-heading\n", "skill_md_no_frontmatter"),
    ("####### Too many hashes\n", "skill_md_no_frontmatter"),
    # the post-frontmatter body heading uses the identical ATX boundary
    ("---\nname: x\n---\n#not-a-heading\n", "skill_md_no_heading"),
    ("---\nname: x\n---\n####### Seven hashes\n", "skill_md_no_heading"),
]

# THR-210 PR 2: heading-first SKILL.md bodies with a column-zero Markdown
# heading are now ACCEPTED for new authoring (same grammar the frontmatter
# path requires for its body heading).
_VALID_HEADING_FIRST_BODIES = [
    "# Heading-first body\n\nBody text.\n",
    "## Heading-first level two\n\nBody text.\n",
    "# Heading without trailing newline",
    "#\n",                                         # ATX boundary: 1 hash + EOL
    "###### Level-six heading\n\nBody text.\n",   # ATX boundary: 6 hashes + space
    "######\n",                                    # ATX boundary: 6 hashes + EOL
    "#\tTab-separated heading\n\nBody text.\n",   # ATX: whitespace after hashes
]


@pytest.mark.parametrize("skill_md,code", _INVALID_BODIES)
def test_add_version_appends_invalid_bodies_as_evidence_retaining_current(client_with_runtime, skill_md, code):
    """THR-210 PR 1 (A): an invalid successor is appended as immutable
    validation/provenance evidence — exactly one version row with
    deterministic findings, its content-addressed artifact, and the
    version_saved + validated events — but NEVER displaces the existing
    valid current_version_id. Eligibility/materialization stay bound to the
    retained valid current version."""
    from runtime.skills.custom import service as custom_service
    client, org = client_with_runtime
    created = _create(client)
    skill_id, prior_revision = created["skill_id"], created["version_id"]
    prior_keys = _artifact_keys(org)
    response = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": skill_md})
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["validation_state"] == "invalid"
    assert payload["current_version_id"] == prior_revision
    conn = getattr(org.db, "_conn", org.db)
    row = conn.execute(
        "SELECT * FROM custom_skill_versions WHERE id=?", (payload["version_id"],)
    ).fetchone()
    expected = custom_service.validate_package(
        org, slug="test-skill", name="Test skill", skill_md=skill_md
    )
    assert row["validation_state"] == "invalid"
    assert row["parent_version_id"] == prior_revision
    assert json.loads(row["validation_findings"]) == expected["errors"]
    assert row["skill_md_cache"] == skill_md
    # current pointer unchanged: prior valid revision retained
    assert conn.execute(
        "SELECT current_version_id FROM custom_skills WHERE id=?", (skill_id,)
    ).fetchone()["current_version_id"] == prior_revision
    # events: created+validated (original) then version_saved+validated (evidence)
    events = [r["event_type"] for r in conn.execute(
        "SELECT event_type FROM custom_skill_events WHERE skill_id=? ORDER BY id", (skill_id,)
    )]
    assert events == ["created", "validated", "version_saved", "validated"]
    # content-addressed artifact is durable provenance (never a dangling key)
    digest = hashlib.sha256(skill_md.encode()).hexdigest()
    assert f"custom-skills/test-skill/{digest}/SKILL.md" in _artifact_keys(org)
    assert prior_keys <= _artifact_keys(org)
    # detail still resolves the retained VALID current version
    detail = client.get(f"{BASE}/{skill_id}").json()
    assert detail["validation_state"] == "valid"
    assert detail["version_id"] == prior_revision
    # history exposes the invalid evidence
    versions = client.get(f"{BASE}/{skill_id}/versions").json()["versions"]
    assert versions[0]["validation_state"] == "invalid"
    assert versions[0]["id"] == payload["version_id"]


def test_add_version_rejects_empty_skill_md_without_residue(client_with_runtime):
    client, org = client_with_runtime
    created = _create(client)
    skill_id = created["skill_id"]
    before = _residue_snapshot(org, skill_id)
    response = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": ""})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_request"
    assert _residue_snapshot(org, skill_id) == before


def test_add_version_accepts_frontmatter_first_successor(client_with_runtime):
    client, org = client_with_runtime
    created = _create(client)
    skill_id = created["skill_id"]
    successor = "---\nname: Test skill\ndescription: test\n---\n\n# Test\n\nTwo\n"
    response = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": successor})
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["validation_state"] == "valid"
    conn = getattr(org.db, "_conn", org.db)
    row = conn.execute(
        "SELECT current_version_id FROM custom_skills WHERE id=?", (skill_id,)
    ).fetchone()
    assert row["current_version_id"] == payload["version_id"]
    events = [
        r["event_type"]
        for r in conn.execute(
            "SELECT event_type FROM custom_skill_events WHERE skill_id=? ORDER BY id",
            (skill_id,),
        )
    ]
    assert events == ["created", "validated", "version_saved", "validated"]
    stored = conn.execute(
        "SELECT skill_md_cache FROM custom_skill_versions WHERE id=?",
        (payload["version_id"],),
    ).fetchone()
    assert stored["skill_md_cache"] == successor


@pytest.mark.parametrize("successor", _VALID_HEADING_FIRST_BODIES)
def test_add_version_accepts_heading_first_successor_advancing_current(
    client_with_runtime, successor,
):
    """THR-210 PR 2: a heading-first successor (H1/H2, column-zero heading)
    is now VALID for new authoring — it advances current_version_id
    normally (A/D), appends the usual version_saved+validated events, stores
    its content-addressed artifact, and detail resolves it as valid."""
    client, org = client_with_runtime
    created = _create(client)
    skill_id, prior_revision = created["skill_id"], created["version_id"]
    response = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": successor})
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["validation_state"] == "valid"
    conn = getattr(org.db, "_conn", org.db)
    # pointer advanced to the new valid version
    assert payload["current_version_id"] == payload["version_id"]
    assert conn.execute(
        "SELECT current_version_id FROM custom_skills WHERE id=?", (skill_id,)
    ).fetchone()["current_version_id"] == payload["version_id"]
    events = [
        r["event_type"]
        for r in conn.execute(
            "SELECT event_type FROM custom_skill_events WHERE skill_id=? ORDER BY id",
            (skill_id,),
        )
    ]
    assert events == ["created", "validated", "version_saved", "validated"]
    # content-addressed artifact stored; detail resolves the new valid version
    digest = hashlib.sha256(successor.encode()).hexdigest()
    assert f"custom-skills/test-skill/{digest}/SKILL.md" in _artifact_keys(org)
    detail = client.get(f"{BASE}/{skill_id}").json()
    assert detail["validation_state"] == "valid"
    assert detail["version_id"] == payload["version_id"]


def test_heading_first_duplicate_content_conflicts_atomically(client_with_runtime):
    """PR 3 is NOT implemented: a byte-identical heading-first body replay
    still conflicts with the append-only UNIQUE (skill_id, content_hash)
    invariant as HTTP 409 `duplicate_content` with zero residue — the
    duplicate-replay contract is unchanged (no version_content_exists rename,
    no artifact rewrite, no pointer change)."""
    client, org = client_with_runtime
    created = _create(client)
    skill_id = created["skill_id"]
    heading_first = "# Heading-first body\n\nBody text.\n"
    first = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": heading_first})
    assert first.status_code == 201 and first.json()["validation_state"] == "valid"
    before = _residue_snapshot(org, skill_id)
    response = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": heading_first})
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "duplicate_content"
    assert _residue_snapshot(org, skill_id) == before


def test_heading_first_initial_creation_is_valid_and_materializable(
    client_with_runtime, monkeypatch,
):
    """THR-210 PR 2: a heading-first body on INITIAL creation is a valid
    first version (B over PR 2), becomes the current pointer, is eligible,
    and materializes through the canonical store — it is no longer treated
    as legacy-only evidence."""
    from runtime.skills.canonical_store import CanonicalSkillStore
    from runtime.orchestrator.workspace_adapters import _build_custom_skill_canonical_specs

    client, org = client_with_runtime
    _add_agent(org)
    heading_first = "# Heading-first create\n\nBody text.\n"
    created = _create(client, slug="heading-create", skill_md=heading_first)
    assert created["validation_state"] == "valid"
    skill_id = created["skill_id"]
    conn = getattr(org.db, "_conn", org.db)
    assert conn.execute(
        "SELECT current_version_id FROM custom_skills WHERE id=?", (skill_id,)
    ).fetchone()["current_version_id"] == created["version_id"]
    rules = [{"scope_type": "org", "scope_target": None, "effect": "allow"}]
    assert client.put(
        f"{BASE}/{skill_id}/eligibility", json=rules,
        headers={"If-Match": str(created["version_id"])},
    ).status_code == 200
    monkeypatch.setenv("HAPPYRANCH_CANONICAL_STORE_ROOT", str(org.root / "canonical-store"))
    specs = _build_custom_skill_canonical_specs(
        store=CanonicalSkillStore(), org_root=org.root, db=org.db, slug="alpha",
        agent_name="dev_agent", team="engineering", task_id="TASK-HF",
        session_id="sess-hf", session_context="task",
    )
    spec = next(s for s in specs if s["slug"] == "heading-create")
    assert spec["version"] == str(created["version_id"])
    assert spec["content_hash"] == created["content_hash"]


def test_agent_create_accepts_heading_first_body_with_provenance(client_with_runtime):
    """Agent path under PR 2: heading-first body creates a VALID first
    version with verified task/session provenance and advances the pointer."""
    client, org = client_with_runtime
    org.db.insert_task(TaskRecord(id="TASK-HF2", brief="create a custom skill"))
    org.sessions.set_active("TASK-HF2", "dev_agent", "sess-hf2", org_slug="alpha")
    client.headers.pop("Authorization", None)
    heading_first = "# Agent heading-first\n\nBody.\n"
    response = client.post(
        f"{BASE}/agent-create", params={"session_id": "sess-hf2"},
        json={"slug": "agent-heading", "name": "Agent Heading", "skill_md": heading_first},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["version"]["validation_state"] == "valid"
    assert payload["version"]["source_task_id"] == "TASK-HF2"
    assert payload["version"]["source_session_id"] == "sess-hf2"
    conn = getattr(org.db, "_conn", org.db)
    assert conn.execute(
        "SELECT current_version_id FROM custom_skills WHERE id=?",
        (payload["skill"]["id"],),
    ).fetchone()["current_version_id"] == payload["version"]["id"]


def test_malformed_heading_like_successor_never_eligible_or_materializable(
    client_with_runtime, monkeypatch,
):
    """THR-210 PR 2 (reviewer lock): a hash-prefixed body that is NOT an ATX
    heading (e.g. '#not-a-heading', no whitespace/EOL after the hashes) is
    classified invalid evidence under PR 1 rules — it never displaces the
    existing valid current pointer, and eligibility/materialization stay
    bound to the retained valid version, exactly like any other invalid
    successor."""
    from runtime.skills.canonical_store import CanonicalSkillStore
    from runtime.orchestrator.workspace_adapters import _build_custom_skill_canonical_specs

    client, org = client_with_runtime
    _add_agent(org)
    created = _create(
        client, slug="heading-like-b2",
        skill_md="---\nname: M\n---\n\n# M\n\nOne\n",
    )
    skill_id, v1 = created["skill_id"], created["version_id"]
    rules = [{"scope_type": "org", "scope_target": None, "effect": "allow"}]
    assert client.put(
        f"{BASE}/{skill_id}/eligibility", json=rules, headers={"If-Match": str(v1)}
    ).status_code == 200
    invalid = client.post(
        f"{BASE}/{skill_id}/versions", json={"skill_md": "#not-a-heading\n"}
    )
    assert invalid.status_code == 201 and invalid.json()["validation_state"] == "invalid"
    # pointer retained; eligibility still writes against the retained valid revision
    conn = getattr(org.db, "_conn", org.db)
    assert conn.execute(
        "SELECT current_version_id FROM custom_skills WHERE id=?", (skill_id,)
    ).fetchone()["current_version_id"] == v1
    assert client.put(
        f"{BASE}/{skill_id}/eligibility", json=rules, headers={"If-Match": str(v1)}
    ).status_code == 200
    # materialization resolves the RETAINED valid v1, never the malformed evidence
    monkeypatch.setenv("HAPPYRANCH_CANONICAL_STORE_ROOT", str(org.root / "canonical-store"))
    specs = _build_custom_skill_canonical_specs(
        store=CanonicalSkillStore(), org_root=org.root, db=org.db, slug="alpha",
        agent_name="dev_agent", team="engineering", task_id="TASK-HLM",
        session_id="sess-hlm", session_context="task",
    )
    spec = next(s for s in specs if s["slug"] == "heading-like-b2")
    assert spec["version"] == str(v1)
    explain = client.get(f"{BASE}/{skill_id}/eligibility/explain", params={"agent": "dev_agent"})
    assert explain.json()["visible"] is True


def test_malformed_heading_like_initial_candidate_is_invalid_evidence(client_with_runtime):
    """THR-210 PR 2 (reviewer lock): on INITIAL creation a malformed
    heading-like body ('#not-a-heading') is an invalid FIRST version —
    persisted as immutable evidence (current pointer = the invalid first
    version, per PR 1's B-shape), never eligible or materializable — and a
    later TRUE ATX heading-first successor validates and advances the
    pointer normally."""
    client, org = client_with_runtime
    malformed = "#not-a-heading\n\nBody text.\n"
    response = client.post(
        BASE,
        json={"slug": "bad-heading-like", "name": "Bad", "skill_md": malformed},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["validation_state"] == "invalid"
    skill_id = payload["skill_id"]
    conn = getattr(org.db, "_conn", org.db)
    assert conn.execute(
        "SELECT current_version_id FROM custom_skills WHERE id=?", (skill_id,)
    ).fetchone()["current_version_id"] == payload["version_id"]
    # inspectable evidence, never eligible
    assert client.get(f"{BASE}/{skill_id}").json()["validation_state"] == "invalid"
    rules = [{"scope_type": "org", "scope_target": None, "effect": "allow"}]
    assert client.put(
        f"{BASE}/{skill_id}/eligibility", json=rules,
        headers={"If-Match": str(payload["version_id"])},
    ).status_code == 422
    _add_agent(org)
    explain = client.get(f"{BASE}/{skill_id}/eligibility/explain", params={"agent": "dev_agent"})
    assert explain.json()["visible"] is False
    assert explain.json()["hidden_reason"] == "current_version_invalid"
    digest = hashlib.sha256(malformed.encode()).hexdigest()
    assert f"custom-skills/bad-heading-like/{digest}/SKILL.md" in _artifact_keys(org)
    # a true ATX heading-first successor is valid and advances the pointer
    valid_md = "# Now a real heading\n\nBody.\n"
    advanced = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": valid_md})
    assert advanced.status_code == 201, advanced.text
    assert advanced.json()["validation_state"] == "valid"
    assert conn.execute(
        "SELECT current_version_id FROM custom_skills WHERE id=?", (skill_id,)
    ).fetchone()["current_version_id"] == advanced.json()["version_id"]


def test_add_version_duplicate_content_conflicts_atomically(client_with_runtime):
    """A byte-identical body (TASK-5741 failure mode) conflicts with zero
    durable residue — the append-only (skill_id, content_hash) uniqueness
    invariant is preserved, never relaxed."""
    client, org = client_with_runtime
    created = _create(client)
    skill_id = created["skill_id"]
    before = _residue_snapshot(org, skill_id)
    response = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": _FM_BODY})
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "duplicate_content"
    assert _residue_snapshot(org, skill_id) == before


def test_create_with_invalid_first_version_creates_skill_with_evidence(client_with_runtime):
    """THR-210 PR 1 (B): initial creation with an invalid candidate persists
    the skill with the invalid FIRST version as the current pointer — the
    nullable-pointer schema has no prior pointer to preserve, and a NULL
    pointer is unreadable by every JOIN-based list/detail consumer and
    uneditable through the version route. The invalid candidate is
    inspectable evidence (catalog/detail/history + artifact) but never
    eligible or materializable; a later valid successor advances the
    pointer normally."""
    client, org = client_with_runtime
    invalid_md = "---\nname: Bad\n---\nno heading\n"
    response = client.post(
        BASE,
        json={"slug": "bad-create", "name": "Bad", "skill_md": invalid_md},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["validation_state"] == "invalid"
    skill_id = payload["skill_id"]
    conn = getattr(org.db, "_conn", org.db)
    assert conn.execute(
        "SELECT current_version_id FROM custom_skills WHERE id=?", (skill_id,)
    ).fetchone()["current_version_id"] == payload["version_id"]
    # catalog + detail read it as inspectable invalid evidence
    listed = next(
        s for s in client.get(f"{BASE}/catalog").json()["skills"] if s["id"] == skill_id
    )
    assert listed["validation_state"] == "invalid"
    detail = client.get(f"{BASE}/{skill_id}").json()
    assert detail["validation_state"] == "invalid"
    assert detail["version_id"] == payload["version_id"]
    # version history exposes the invalid first version
    versions = client.get(f"{BASE}/{skill_id}/versions").json()["versions"]
    assert len(versions) == 1 and versions[0]["validation_state"] == "invalid"
    # never eligible: rules PUT refused; explain hidden as current_version_invalid
    rules = [{"scope_type": "org", "scope_target": None, "effect": "allow"}]
    assert client.put(
        f"{BASE}/{skill_id}/eligibility", json=rules,
        headers={"If-Match": str(payload["version_id"])},
    ).status_code == 422
    _add_agent(org)
    explain = client.get(f"{BASE}/{skill_id}/eligibility/explain", params={"agent": "dev_agent"})
    assert explain.json()["visible"] is False
    assert explain.json()["hidden_reason"] == "current_version_invalid"
    # content-addressed artifact evidence exists
    digest = hashlib.sha256(invalid_md.encode()).hexdigest()
    assert f"custom-skills/bad-create/{digest}/SKILL.md" in _artifact_keys(org)
    # a later VALID successor advances the pointer (D over the B shape)
    valid_md = "---\nname: Bad\n---\n\n# Bad\n\nNow valid\n"
    advanced = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": valid_md})
    assert advanced.status_code == 201, advanced.text
    assert advanced.json()["validation_state"] == "valid"
    assert conn.execute(
        "SELECT current_version_id FROM custom_skills WHERE id=?", (skill_id,)
    ).fetchone()["current_version_id"] == advanced.json()["version_id"]


def test_agent_create_with_invalid_first_version_creates_skill_with_evidence(client_with_runtime):
    """Agent path (B): an invalid first version persists with verified
    task/session provenance and darkens the skill (current_version_invalid) —
    evidence, never eligible or materializable."""
    client, org = client_with_runtime
    org.db.insert_task(TaskRecord(id="TASK-INV", brief="create a custom skill"))
    org.sessions.set_active("TASK-INV", "dev_agent", "sess-inv", org_slug="alpha")
    client.headers.pop("Authorization", None)
    response = client.post(
        f"{BASE}/agent-create",
        params={"session_id": "sess-inv"},
        json={"slug": "bad-agent", "name": "Bad", "skill_md": "---\nname: Bad\n---\nno heading\n"},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["version"]["validation_state"] == "invalid"
    assert payload["version"]["source_task_id"] == "TASK-INV"
    assert payload["version"]["source_session_id"] == "sess-inv"
    assert payload["provenance"]["task_brief_digest"] == payload["version"]["task_brief_digest"]
    conn = getattr(org.db, "_conn", org.db)
    assert conn.execute(
        "SELECT current_version_id FROM custom_skills WHERE id=?", (payload["skill"]["id"],)
    ).fetchone()["current_version_id"] == payload["version"]["id"]


def test_agent_update_appends_invalid_body_as_evidence_retaining_current(client_with_runtime):
    """Agent updating its own originated skill (A): invalid body appended as
    evidence with task/session provenance; current pointer stays at the prior
    valid version."""
    client, org = client_with_runtime
    org.db.insert_task(TaskRecord(id="TASK-OWN", brief="create a custom skill"))
    org.sessions.set_active("TASK-OWN", "dev_agent", "sess-own", org_slug="alpha")
    client.headers.pop("Authorization", None)
    created = client.post(
        f"{BASE}/agent-create", params={"session_id": "sess-own"}, json=_body("owned-skill")
    )
    assert created.status_code == 201, created.text
    skill_id = created.json()["skill"]["id"]
    prior_revision = created.json()["skill"]["version_id"]
    response = client.post(
        f"{BASE}/agent-create",
        params={"session_id": "sess-own"},
        json=_body("owned-skill", "---\nname: x\n---\nno heading\n"),
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["version"]["validation_state"] == "invalid"
    assert payload["version"]["source_task_id"] == "TASK-OWN"
    assert payload["version"]["source_session_id"] == "sess-own"
    conn = getattr(org.db, "_conn", org.db)
    assert conn.execute(
        "SELECT current_version_id FROM custom_skills WHERE id=?", (skill_id,)
    ).fetchone()["current_version_id"] == prior_revision


# ═══════════════════════════════════════════════════════════════════════════
# THR-210 PR 1: invalid candidates are immutable validation/provenance evidence
#
# State matrix locked in tests before production edits:
#   (A) valid current + invalid successor  -> append evidence, RETAIN pointer
#   (B) no prior version + invalid first   -> first version becomes current
#        (NULL pointer is unreadable by JOIN consumers and uneditable),
#        skill darkens as current_version_invalid until a valid successor
#   (C) legacy records incl. current->invalid read without healing/rewriting
#   (D) later valid successor advances current_version_id normally
#   (E) any persistence failure -> full rollback + artifact compensation,
#        zero partial residue
# ═══════════════════════════════════════════════════════════════════════════

def test_invalid_duplicate_content_conflicts_atomically(client_with_runtime):
    """A byte-identical INVALID body still conflicts with the append-only
    UNIQUE (skill_id, content_hash) invariant as 409 duplicate_content with
    zero additional residue — the same evidence is never appended twice."""
    client, org = client_with_runtime
    created = _create(client)
    skill_id = created["skill_id"]
    invalid_md = "---\nname: x\n---\nno heading\n"
    first = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": invalid_md})
    assert first.status_code == 201 and first.json()["validation_state"] == "invalid"
    before = _residue_snapshot(org, skill_id)
    response = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": invalid_md})
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "duplicate_content"
    assert _residue_snapshot(org, skill_id) == before


def test_invalid_append_then_valid_successor_advances_current_with_lineage(client_with_runtime):
    """THR-210 PR 1 (D): after an invalid evidence append, a later valid
    successor advances current_version_id normally. Parent/provenance
    continuity: every version's parent is the current version it was authored
    against — the invalid evidence never displaces the pointer, so the
    advancing line's parent is the retained valid current, not the evidence."""
    client, org = client_with_runtime
    created = _create(client, slug="lineage-skill")
    skill_id, v1 = created["skill_id"], created["version_id"]
    invalid_md = "---\nname: x\n---\nno heading\n"
    invalid = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": invalid_md})
    assert invalid.status_code == 201, invalid.text
    v2 = invalid.json()["version_id"]
    valid_md = "---\nname: Lineage\n---\n\n# Lineage\n\nNow valid\n"
    valid = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": valid_md})
    assert valid.status_code == 201, valid.text
    assert valid.json()["validation_state"] == "valid"
    v3 = valid.json()["version_id"]
    conn = getattr(org.db, "_conn", org.db)
    assert conn.execute(
        "SELECT current_version_id FROM custom_skills WHERE id=?", (skill_id,)
    ).fetchone()["current_version_id"] == v3
    parents = {
        r["id"]: r["parent_version_id"]
        for r in conn.execute(
            "SELECT id, parent_version_id FROM custom_skill_versions WHERE skill_id=?",
            (skill_id,),
        )
    }
    assert parents[v1] is None
    assert parents[v2] == v1   # authored against the current (v1) guidance
    assert parents[v3] == v1   # v2 never displaced the pointer
    assert conn.execute(
        "SELECT count(*) FROM custom_skill_versions WHERE skill_id=?", (skill_id,)
    ).fetchone()[0] == 3


def test_legacy_invalid_current_and_history_read_without_silent_healing(client_with_runtime):
    """THR-210 PR 1 (C): pre-existing records — including a current pointer
    that already references an invalid version alongside a valid/invalid
    history — continue to read/resolve with no silent healing and no
    destructive rewriting. Appends follow the same pointer contract: an
    invalid successor keeps the existing pointer; a valid one advances."""
    from runtime.skills.custom import service as custom_service
    client, org = client_with_runtime
    created = _create(client, slug="legacy-history")
    skill_id, v1 = created["skill_id"], created["version_id"]
    conn = getattr(org.db, "_conn", org.db)
    conn.execute(
        """INSERT INTO custom_skill_versions
           (skill_id,parent_version_id,content_hash,content_artifact_key,skill_md_cache,
            validation_state,validator_version,validation_findings,created_at,
            author_kind,author_identity)
           VALUES (?,?,?,?,?,?,?,?,?,?,?) """,
        (skill_id, v1, "0" * 64, "custom-skills/legacy-history/legacy/SKILL.md",
         "not markdown", "invalid", "THR-055/1.0.0",
         '["SKILL.md must start with a heading"]', custom_service.now(),
         "human", "founder"),
    )
    v_legacy = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "UPDATE custom_skills SET current_version_id=? WHERE id=?", (v_legacy, skill_id)
    )
    conn.commit()
    # (C) reads without healing: detail/catalog show the legacy invalid current
    assert client.get(f"{BASE}/{skill_id}").json()["validation_state"] == "invalid"
    rows_before = [
        dict(r) for r in conn.execute(
            "SELECT id, validation_state, content_hash, skill_md_cache "
            "FROM custom_skill_versions WHERE skill_id=? ORDER BY id", (skill_id,)
        )
    ]
    # another invalid successor keeps the EXISTING (invalid) pointer
    second_invalid = client.post(
        f"{BASE}/{skill_id}/versions", json={"skill_md": "---\nname: x\n---\nno heading\n"}
    )
    assert second_invalid.status_code == 201, second_invalid.text
    assert second_invalid.json()["validation_state"] == "invalid"
    assert second_invalid.json()["current_version_id"] == v_legacy
    # no silent healing: every pre-existing row is byte-identical after the append
    rows_after = [
        dict(r) for r in conn.execute(
            "SELECT id, validation_state, content_hash, skill_md_cache "
            "FROM custom_skill_versions WHERE skill_id=? ORDER BY id", (skill_id,)
        )
    ]
    assert rows_before == rows_after[: len(rows_before)]
    # valid successor advances from the legacy invalid current (D over C)
    valid_md = "---\nname: Legacy\n---\n\n# Legacy\n\nHealed by valid successor\n"
    advanced = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": valid_md})
    assert advanced.status_code == 201, advanced.text
    assert advanced.json()["validation_state"] == "valid"
    assert conn.execute(
        "SELECT current_version_id FROM custom_skills WHERE id=?", (skill_id,)
    ).fetchone()["current_version_id"] == advanced.json()["version_id"]


def test_invalid_successor_keeps_prior_valid_eligible_and_materializable(client_with_runtime, monkeypatch):
    """THR-210 PR 1 (A): after an invalid evidence append, eligibility writes
    and canonical materialization stay bound to the retained VALID current
    version — the invalid candidate never becomes eligible or materializable."""
    from runtime.skills.canonical_store import CanonicalSkillStore
    from runtime.orchestrator.workspace_adapters import _build_custom_skill_canonical_specs

    client, org = client_with_runtime
    _add_agent(org)
    created = _create(
        client, slug="materializable-b2",
        skill_md="---\nname: M\n---\n\n# M\n\nOne\n",
    )
    skill_id, v1 = created["skill_id"], created["version_id"]
    rules = [{"scope_type": "org", "scope_target": None, "effect": "allow"}]
    assert client.put(
        f"{BASE}/{skill_id}/eligibility", json=rules, headers={"If-Match": str(v1)}
    ).status_code == 200
    invalid = client.post(
        f"{BASE}/{skill_id}/versions", json={"skill_md": "---\nname: x\n---\nno heading\n"}
    )
    assert invalid.status_code == 201 and invalid.json()["validation_state"] == "invalid"
    # eligibility still writes against the retained valid revision
    assert client.put(
        f"{BASE}/{skill_id}/eligibility", json=rules, headers={"If-Match": str(v1)}
    ).status_code == 200
    # materialization resolves the RETAINED valid v1, never the invalid evidence
    monkeypatch.setenv("HAPPYRANCH_CANONICAL_STORE_ROOT", str(org.root / "canonical-store"))
    specs = _build_custom_skill_canonical_specs(
        store=CanonicalSkillStore(), org_root=org.root, db=org.db, slug="alpha",
        agent_name="dev_agent", team="engineering", task_id="TASK-MAT",
        session_id="sess-mat", session_context="task",
    )
    spec = next(s for s in specs if s["slug"] == "materializable-b2")
    assert spec["version"] == str(v1)
    # explain still resolves visible through the valid current
    explain = client.get(f"{BASE}/{skill_id}/eligibility/explain", params={"agent": "dev_agent"})
    assert explain.json()["visible"] is True


# ═══════════════════════════════════════════════════════════════════════════
# Post-validation persistence fault injection (TASK-5781)
#
# _persist_validated_version is the shared persistence helper for ALL three
# authoring surfaces (human create, agent create/update, human POST /versions).
# Each stage below is injected at the shared stage the routes actually reach;
# the assertions drive the PUBLIC HTTP routes (never the helper directly) and
# verify the correct failure mapping: only the version INSERT's UNIQUE
# (skill_id, content_hash) violation is duplicate_content (409); every later
# failure (artifact write, current-pointer update, either event append) must
# map to a NON-duplicate error (500), compensate the artifact this request
# wrote, and leave zero durable residue in every dimension.
# ═══════════════════════════════════════════════════════════════════════════

def _fault_client(client):
    """A second TestClient over the same app that surfaces 500 responses
    instead of re-raising the server exception."""
    from fastapi.testclient import TestClient
    fault = TestClient(client.app, raise_server_exceptions=False)
    fault.headers.update(client.headers)
    return fault


def _install_persistence_fault(monkeypatch, org, stage: str) -> None:
    """Route-level fault injection for one post-validation persistence stage.

    Injection points are the shared helpers all authoring routes reach:
    service.create_version (version INSERT), _write_artifact (artifact write),
    the BEGIN IMMEDIATE connection's current-pointer UPDATE, and
    service.append_event (first / validated event append).
    """
    from runtime.daemon.routes import custom_skills as routes
    from runtime.infrastructure.artifact_store import ArtifactStore
    from runtime.skills.custom import service

    if stage == "version-insert":
        def _insert_fails(*args, **kwargs):
            raise sqlite3.IntegrityError(
                "UNIQUE constraint failed: custom_skill_versions.skill_id, "
                "custom_skill_versions.content_hash"
            )
        monkeypatch.setattr(service, "create_version", _insert_fails)
    elif stage == "artifact-write":
        real_put = ArtifactStore.put
        def _put_then_crash(self, name, content):
            real_put(self, name, content)
            raise OSError("injected crash after artifact write")
        monkeypatch.setattr(ArtifactStore, "put", _put_then_crash)
    elif stage == "current-pointer":
        real_conn = org.db._conn
        class _PointerFailureProxy:
            """Delegating connection proxy that fails ONLY the current-pointer
            UPDATE (sqlite3.Connection is an immutable C type, so the instance
            execute cannot be monkeypatched directly)."""
            def __getattr__(self, name):
                return getattr(real_conn, name)
            def execute(self, sql, *args, **kwargs):
                if "SET current_version_id" in str(sql):
                    raise sqlite3.IntegrityError("injected current-pointer update failure")
                return real_conn.execute(sql, *args, **kwargs)
        monkeypatch.setattr(org.db, "_conn", _PointerFailureProxy())
    elif stage in ("first-event", "validated-event"):
        real_append = service.append_event
        state = {"n": 0}
        # The first append carries event="created" on create surfaces and
        # event="version_saved" on update / POST /versions surfaces; the
        # second append is always event="validated".
        target = 1 if stage == "first-event" else 2
        def _append_fails(*args, **kwargs):
            state["n"] += 1
            if state["n"] == target:
                raise sqlite3.IntegrityError("injected event append failure")
            return real_append(*args, **kwargs)
        monkeypatch.setattr(service, "append_event", _append_fails)
    elif stage == "commit":
        # The FINAL commit boundary (TASK-5803): the persistence helper
        # returns after writing the artifact, and the route then commits.
        # A commit failure must still compensate the artifact this request
        # wrote — compensation stays armed through successful commit.
        real_conn = org.db._conn
        class _CommitFailureProxy:
            """Delegating connection proxy that fails the route's final
            conn.commit() exactly once — but only after a BEGIN IMMEDIATE
            transaction has written custom_skills rows in this request, so
            reads, fixture commits, and teardown commits pass untouched.
            Disarms after the single injected failure."""
            def __init__(self):
                self._begun = False
                self._custom_write = False
                self._failed = False
            def __getattr__(self, name):
                return getattr(real_conn, name)
            def execute(self, sql, *args, **kwargs):
                text = str(sql).strip().upper()
                if text.startswith("BEGIN"):
                    self._begun = True
                if text.startswith(("INSERT", "UPDATE")) and "CUSTOM_SKILL" in text:
                    self._custom_write = True
                return real_conn.execute(sql, *args, **kwargs)
            def commit(self):
                if not self._failed and self._begun and self._custom_write:
                    self._failed = True
                    raise sqlite3.OperationalError("injected commit failure")
                self._begun = False
                self._custom_write = False
                return real_conn.commit()
        monkeypatch.setattr(org.db, "_conn", _CommitFailureProxy())
    else:
        raise AssertionError(f"unknown stage: {stage}")


@pytest.mark.parametrize("stage", [
    "version-insert", "artifact-write", "current-pointer",
    "first-event", "validated-event", "commit",
])
@pytest.mark.parametrize("surface", [
    "human-create", "agent-create", "agent-update", "human-version",
])
def test_authoring_persistence_fault_leaves_zero_residue_and_no_false_409(
    client_with_runtime, monkeypatch, surface, stage,
):
    """Every post-validation persistence stage, driven through every public
    authoring route: the version INSERT's IntegrityError is the ONLY stage
    mapped to 409 duplicate_content; all later failures return a non-duplicate
    error (500) and compensate the artifact + empty dirs this request created,
    with zero durable residue in version rows, events, current_version_id,
    parent/current lineage, artifacts, empty directories, materialization,
    and any temporary parent. The `commit` stage (TASK-5803) fails the final
    conn.commit() — compensation must stay armed through successful commit
    so a commit failure rolls back the DB AND removes the request-written
    artifact, not just the DB rows."""
    client, org = client_with_runtime
    real_conn = getattr(org.db, "_conn", org.db)
    body_two = "---\nname: Test skill\ndescription: test\n---\n\n# Test\n\nTwo\n"

    if surface == "human-create":
        skill_id = None
        _install_persistence_fault(monkeypatch, org, stage)
        fault = _fault_client(client)
        before = _residue_snapshot(org, None, conn=real_conn)
        response = fault.post(BASE, json=_body(f"fault-{stage}"))
    elif surface == "agent-create":
        skill_id = None
        org.db.insert_task(TaskRecord(id="TASK-FI", brief="create a custom skill"))
        org.sessions.set_active("TASK-FI", "dev_agent", "sess-fi", org_slug="alpha")
        _install_persistence_fault(monkeypatch, org, stage)
        fault = _fault_client(client)
        fault.headers.pop("Authorization", None)
        before = _residue_snapshot(org, None, conn=real_conn)
        response = fault.post(
            f"{BASE}/agent-create", params={"session_id": "sess-fi"},
            json=_body(f"fault-{stage}"),
        )
    elif surface == "agent-update":
        org.db.insert_task(TaskRecord(id="TASK-OWN2", brief="create a custom skill"))
        org.sessions.set_active("TASK-OWN2", "dev_agent", "sess-own2", org_slug="alpha")
        client.headers.pop("Authorization", None)
        created = client.post(
            f"{BASE}/agent-create", params={"session_id": "sess-own2"},
            json=_body("owned-fault"),
        )
        assert created.status_code == 201, created.text
        skill_id = created.json()["skill"]["id"]
        _install_persistence_fault(monkeypatch, org, stage)
        fault = _fault_client(client)
        fault.headers.pop("Authorization", None)
        before = _residue_snapshot(org, skill_id, conn=real_conn)
        response = fault.post(
            f"{BASE}/agent-create", params={"session_id": "sess-own2"},
            json=_body("owned-fault", body_two),
        )
    elif surface == "human-version":
        created = _create(client, slug=f"fault-{stage}")
        skill_id = created["skill_id"]
        _install_persistence_fault(monkeypatch, org, stage)
        fault = _fault_client(client)
        before = _residue_snapshot(org, skill_id, conn=real_conn)
        response = fault.post(
            f"{BASE}/{skill_id}/versions", json={"skill_md": body_two}
        )
    else:
        raise AssertionError(surface)

    if stage == "version-insert":
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "duplicate_content"
    else:
        # A post-INSERT integrity failure or artifact-write crash must NOT be
        # reported as duplicate_content, and must leave zero durable residue.
        assert response.status_code == 500, response.text
        assert "duplicate_content" not in response.text
    assert _residue_snapshot(org, skill_id, conn=real_conn) == before


@pytest.mark.parametrize("stage", [
    "version-insert", "artifact-write", "current-pointer",
    "first-event", "validated-event", "commit",
])
@pytest.mark.parametrize("surface", [
    "human-create", "agent-create", "agent-update", "human-version",
])
def test_invalid_candidate_persistence_fault_leaves_zero_residue(
    client_with_runtime, monkeypatch, surface, stage,
):
    """THR-210 PR 1 (E): an INVALID candidate flows through the SAME shared
    persistence helper as valid ones, so every post-validation fault still
    maps correctly (only the version INSERT's IntegrityError is 409
    duplicate_content; everything later is 500) and rolls back with full
    artifact compensation — zero partial version/event/pointer/artifact
    residue in every dimension."""
    client, org = client_with_runtime
    real_conn = getattr(org.db, "_conn", org.db)
    invalid_md = "---\nname: x\n---\nno heading\n"

    if surface == "human-create":
        skill_id = None
        _install_persistence_fault(monkeypatch, org, stage)
        fault = _fault_client(client)
        before = _residue_snapshot(org, None, conn=real_conn)
        response = fault.post(BASE, json=_body(f"fault-invalid-{stage}", invalid_md))
    elif surface == "agent-create":
        skill_id = None
        org.db.insert_task(TaskRecord(id="TASK-FII", brief="create a custom skill"))
        org.sessions.set_active("TASK-FII", "dev_agent", "sess-fii", org_slug="alpha")
        _install_persistence_fault(monkeypatch, org, stage)
        fault = _fault_client(client)
        fault.headers.pop("Authorization", None)
        before = _residue_snapshot(org, None, conn=real_conn)
        response = fault.post(
            f"{BASE}/agent-create", params={"session_id": "sess-fii"},
            json=_body(f"fault-invalid-{stage}", invalid_md),
        )
    elif surface == "agent-update":
        org.db.insert_task(TaskRecord(id="TASK-OWN3", brief="create a custom skill"))
        org.sessions.set_active("TASK-OWN3", "dev_agent", "sess-own3", org_slug="alpha")
        client.headers.pop("Authorization", None)
        created = client.post(
            f"{BASE}/agent-create", params={"session_id": "sess-own3"},
            json=_body("owned-fault-inv"),
        )
        assert created.status_code == 201, created.text
        skill_id = created.json()["skill"]["id"]
        _install_persistence_fault(monkeypatch, org, stage)
        fault = _fault_client(client)
        fault.headers.pop("Authorization", None)
        before = _residue_snapshot(org, skill_id, conn=real_conn)
        response = fault.post(
            f"{BASE}/agent-create", params={"session_id": "sess-own3"},
            json=_body("owned-fault-inv", invalid_md),
        )
    elif surface == "human-version":
        created = _create(client, slug=f"fault-invalid-{stage}")
        skill_id = created["skill_id"]
        _install_persistence_fault(monkeypatch, org, stage)
        fault = _fault_client(client)
        before = _residue_snapshot(org, skill_id, conn=real_conn)
        response = fault.post(
            f"{BASE}/{skill_id}/versions", json={"skill_md": invalid_md}
        )
    else:
        raise AssertionError(surface)

    if stage == "version-insert":
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "duplicate_content"
    else:
        assert response.status_code == 500, response.text
        assert "duplicate_content" not in response.text
    assert _residue_snapshot(org, skill_id, conn=real_conn) == before


def test_legacy_heading_first_valid_version_stays_resolvable_and_materializable(
    client_with_runtime, monkeypatch,
):
    """The approved migration boundary: heading-first versions validated under
    the legacy contract and stored valid remain resolvable by the resolver and
    materializable through the canonical store — the seams read stored
    validation_state and never re-validate against the new contract."""
    from runtime.infrastructure.artifact_store import ArtifactStore
    from runtime.orchestrator._paths import OrgPaths
    from runtime.skills.canonical_store import CanonicalSkillStore
    from runtime.skills.custom import service as custom_service
    from runtime.orchestrator.workspace_adapters import _build_custom_skill_canonical_specs

    client, org = client_with_runtime
    _add_agent(org, "dev_agent")
    conn = getattr(org.db, "_conn", org.db)
    skill_id = "custom:legacy"
    content = "# Heading-first legacy body\n\nStill valid under the old contract.\n"
    artifact_key = ArtifactStore(OrgPaths(org.root).artifacts_dir).put(
        "custom-skills/legacy/legacy/SKILL.md", content.encode(),
    ).name
    conn.execute(
        "INSERT INTO custom_skills (id,org_slug,slug,name,origin_kind,created_at,created_by) "
        "VALUES (?,?,?,?,?,?,?)",
        (skill_id, "alpha", "legacy", "Legacy", "human", custom_service.now(), "founder"),
    )
    conn.execute(
        """INSERT INTO custom_skill_versions
           (skill_id,content_hash,content_artifact_key,skill_md_cache,validation_state,
            validator_version,validation_findings,created_at,author_kind,author_identity)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (skill_id, hashlib.sha256(content.encode()).hexdigest(), artifact_key, content,
         "valid", "THR-055/1.0.0", "[]", custom_service.now(), "human", "founder"),
    )
    version_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "UPDATE custom_skills SET current_version_id=? WHERE id=?",
        (version_id, skill_id),
    )
    conn.execute(
        "INSERT INTO custom_skill_eligibility_rules "
        "(skill_id,scope_type,scope_target,effect,created_at,created_by) "
        "VALUES (?,?,?,?,?,?)",
        (skill_id, "org", None, "allow", custom_service.now(), "founder"),
    )
    conn.commit()

    # Resolver: legacy valid heading-first version is visible.
    response = client.get(f"{BASE}/{skill_id}/eligibility/explain", params={"agent": "dev_agent"})
    assert response.status_code == 200
    assert response.json()["visible"] is True

    # Canonical materialization: the legacy body builds a package.
    monkeypatch.setenv("HAPPYRANCH_CANONICAL_STORE_ROOT", str(org.root / "canonical-store"))
    specs = _build_custom_skill_canonical_specs(
        store=CanonicalSkillStore(),
        org_root=org.root,
        db=org.db,
        slug="alpha",
        agent_name="dev_agent",
        team="engineering",
        task_id="TASK-LEGACY",
        session_id="sess-legacy",
        session_context="task",
    )
    assert any(spec["slug"] == "legacy" for spec in specs)


def test_pr1_era_heading_first_invalid_evidence_is_not_rewritten_or_healed(
    client_with_runtime,
):
    """THR-210 PR 2 compatibility (C): a PR-1-era heading-first candidate was
    persisted as immutable INVALID evidence (findings carry the old
    `skill_md_no_frontmatter` message, before heading-first was accepted).
    Under PR 2 that stored row must remain byte-identical and read as invalid
    — no silent healing, no rewrite — while NEW heading-first bodies validate
    as valid. Legacy evidence is never retrofitted to the new grammar."""
    from runtime.skills.custom import service as custom_service
    client, org = client_with_runtime
    created = _create(client, slug="pr1-heading-evidence")
    skill_id, v1 = created["skill_id"], created["version_id"]
    conn = getattr(org.db, "_conn", org.db)
    pr1_heading_first = "# Heading-first body\n\nBody text.\n"
    conn.execute(
        """INSERT INTO custom_skill_versions
           (skill_id,parent_version_id,content_hash,content_artifact_key,skill_md_cache,
            validation_state,validator_version,validation_findings,created_at,
            author_kind,author_identity)
           VALUES (?,?,?,?,?,?,?,?,?,?,?) """,
        (skill_id, v1, hashlib.sha256(pr1_heading_first.encode()).hexdigest(),
         "custom-skills/pr1-heading-evidence/pr1/SKILL.md", pr1_heading_first,
         "invalid", "THR-055/1.0.0",
         '["SKILL.md must start with a YAML frontmatter fence"]',
         custom_service.now(), "human", "founder"),
    )
    v_pr1 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "UPDATE custom_skills SET current_version_id=? WHERE id=?", (v_pr1, skill_id)
    )
    conn.commit()
    # reads the PR-1-era evidence as invalid, without healing
    assert client.get(f"{BASE}/{skill_id}").json()["validation_state"] == "invalid"
    rows_before = [
        dict(r) for r in conn.execute(
            "SELECT id, validation_state, content_hash, skill_md_cache, "
            "validation_findings, validator_version FROM custom_skill_versions "
            "WHERE skill_id=? ORDER BY id", (skill_id,)
        )
    ]
    # a NEW heading-first successor is valid and advances the pointer
    successor = "# New heading-first\n\nAccepted under PR 2.\n"
    advanced = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": successor})
    assert advanced.status_code == 201, advanced.text
    assert advanced.json()["validation_state"] == "valid"
    assert advanced.json()["current_version_id"] == advanced.json()["version_id"]
    # the PR-1-era evidence row is byte-identical after the append
    rows_after = [
        dict(r) for r in conn.execute(
            "SELECT id, validation_state, content_hash, skill_md_cache, "
            "validation_findings, validator_version FROM custom_skill_versions "
            "WHERE skill_id=? ORDER BY id", (skill_id,)
        )
    ]
    assert rows_before == rows_after[: len(rows_before)]
    assert rows_after[len(rows_before)]["validation_state"] == "valid"

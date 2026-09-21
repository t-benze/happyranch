"""Route contract tests for THR-055 B2 custom-skill APIs."""
from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from runtime.models import TaskRecord
# Accepted TASK-8588 §4.3 literal matrix, reused verbatim from the authorized
# validator-level file; the route cases below pair every row with an
# INDEPENDENTLY specified expected finding list (never computed by calling the
# production validator).
from tests.daemon.test_routes_skills import _LITERAL_CONTRACT_ROWS
from runtime.skills.skill_md import (
    ADMISSION_FIELD_NOT_ALLOWED,
    FRONTMATTER_DUPLICATE_KEY,
    FRONTMATTER_INVALID_COMPATIBILITY,
    FRONTMATTER_INVALID_DESCRIPTION,
    FRONTMATTER_INVALID_LICENSE,
    FRONTMATTER_INVALID_METADATA,
    FRONTMATTER_INVALID_NAME,
    FRONTMATTER_MISSING_DESCRIPTION,
    FRONTMATTER_MISSING_NAME,
    FRONTMATTER_NAME_SLUG_MISMATCH,
    SKILL_MD_MALFORMED_FRONTMATTER,
    SKILL_MD_NO_FRONTMATTER,
    SKILL_MD_UNCLOSED_FRONTMATTER,
)


BASE = "/api/v1/orgs/alpha/custom-skills"
_FORBIDDEN_IDENTITY = (
    "task_id", "session_id", "proposer_agent", "agent", "agent_name", "org",
    "org_slug", "actor", "eligibility", "permission", "permissions",
)

# Supported authoring contract (THR-262 / seq27): YAML frontmatter is
# required; the body heading is retired and the body may be empty. The
# frontmatter `name` must equal the logical slug and `description` must be a
# non-empty string (the catalog description is projected from it).
def _fm_body(slug: str, description: str = "test") -> str:
    return f"---\nname: {slug}\ndescription: {description}\n---\n"


_FM_BODY = _fm_body("test-skill")


def _body(slug: str = "test-skill", skill_md: str | None = None) -> dict:
    """Build a create/append body.

    With no explicit ``skill_md`` the request supplies an explicit
    description equal to the derived frontmatter description. With an explicit
    ``skill_md`` the request omits the optional description entirely (THR-262
    omitted-key derivation) so a caller-supplied body cannot diverge from it.
    """
    if skill_md is None:
        return {"slug": slug, "name": "Test skill", "description": "test", "skill_md": _fm_body(slug)}
    return {"slug": slug, "name": "Test skill", "skill_md": skill_md}


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


def _artifact_bytes_state(org) -> dict:
    """Per-artifact bytes + file metadata for the custom-skills artifact tree.

    Proves zero artifact REWRITE on a duplicate replay: if the request never
    invokes the write seam, every artifact keeps byte-for-byte identical
    content and unchanged stat metadata (size, mtime).
    """
    from runtime.infrastructure.artifact_store import ArtifactStore
    from runtime.orchestrator._paths import OrgPaths
    store = ArtifactStore(OrgPaths(org.root).artifacts_dir)
    state = {}
    for info in store.list_artifacts():
        if not info.name.startswith("custom-skills/"):
            continue
        path = store.path_for(info.name)
        stat = path.stat()
        state[info.name] = {
            "bytes": path.read_bytes(),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    return state


def _no_write_artifact_seam(monkeypatch) -> list:
    """Instrumented no-write seam: replace the route's artifact writer with a
    recorder. THR-210 PR 3: a byte-identical replay must never attempt an
    artifact write — the recorder stays empty after the replayed request.
    """
    from runtime.daemon.routes import custom_skills as routes
    calls = []
    def _recorder(org, key, content):
        calls.append((key, content))
    monkeypatch.setattr(routes, "_write_artifact", _recorder)
    return calls


def _residue_snapshot(org, skill_id: str, conn=None) -> dict:
    """Snapshot every zero-residue dimension for one skill: complete skill row
    and projected description, version/event/eligibility/materialization rows,
    current pointer, parent/current lineage, artifacts, and empty dirs."""
    conn = conn or getattr(org.db, "_conn", org.db)
    row = conn.execute("SELECT * FROM custom_skills WHERE id=?", (skill_id,)).fetchone()
    current = row["current_version_id"] if row else None
    parent = None
    if current is not None:
        parent_row = conn.execute(
            "SELECT parent_version_id FROM custom_skill_versions WHERE id=?", (current,)
        ).fetchone()
        parent = parent_row["parent_version_id"] if parent_row else None

    def _rows(sql: str) -> list[dict]:
        if skill_id is None:
            return []
        return [dict(r) for r in conn.execute(sql, (skill_id,))]

    return {
        "counts": _custom_counts(org, conn),
        "skill_row": dict(row) if row else None,
        "description": row["description"] if row else None,
        "current_version_id": current,
        "current_parent_version_id": parent,
        "versions": _rows("SELECT * FROM custom_skill_versions WHERE skill_id=? ORDER BY id"),
        "events": _rows("SELECT * FROM custom_skill_events WHERE skill_id=? ORDER BY id"),
        "eligibility": _rows(
            "SELECT * FROM custom_skill_eligibility_rules WHERE skill_id=? ORDER BY id"
        ),
        "materializations": _rows(
            "SELECT * FROM custom_skill_materializations WHERE skill_id=? ORDER BY id"
        ),
        "artifacts": _artifact_keys(org),
        "empty_dirs": _empty_artifact_dirs(org),
    }


def _full_custom_state(org) -> dict:
    """Snapshot EVERY custom-skill durable row plus the actual artifact bytes and
    file metadata. Unlike ``_custom_counts`` this is content-level; unlike
    ``_residue_snapshot`` it is not scoped to a single skill id."""
    conn = getattr(org.db, "_conn", org.db)
    tables = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'custom_skill%'"
    )]
    return {
        "rows": {
            table: [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in tables
        },
        "artifacts": _artifact_bytes_state(org),
        "empty_dirs": _empty_artifact_dirs(org),
    }


def _create(client, slug: str = "test-skill", skill_md: str | None = None) -> dict:
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


@pytest.mark.parametrize("path", [f"{BASE}/agent-create", "/api/v1/orgs/alpha/skills/agent"])
def test_recovery_session_cannot_create_custom_skill_on_either_mount(client_with_runtime, path):
    """Both agent creation mounts delegate to the same recovery-purpose gate."""
    client, org = client_with_runtime
    task_id, session_id = "TASK-RECOVERY-SKILL", "sess-recovery-skill"
    org.db.insert_task(TaskRecord(id=task_id, brief="recover", assigned_agent="dev_agent"))
    org.sessions.register_recovery_session(task_id, "dev_agent", session_id, org_slug="alpha")
    before = _custom_counts(org)
    client.headers.pop("Authorization", None)
    response = client.post(path, params={"session_id": session_id}, json=_body("recovery-skill"))
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "recovery_purpose_forbidden"
    assert _custom_counts(org) == before


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
    advanced = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": _fm_body("test-skill", "two")})
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


def test_catalog_excludes_purged_tombstones_by_default_and_lists_only_removed(client_with_runtime):
    client, _org = client_with_runtime
    active = _create(client, slug="still-active")
    removed = _create(client, slug="removed-reservation")
    removed_id = removed["skill_id"]
    assert client.post(f"{BASE}/{removed_id}/retire", json={"reason": "done"}).status_code == 200
    purge = client.post(f"{BASE}/{removed_id}/purge", json={"typed_slug": "removed-reservation"})
    assert purge.status_code == 200

    default = client.get(f"{BASE}/catalog")
    assert default.status_code == 200
    assert [skill["id"] for skill in default.json()["skills"]] == [active["skill_id"]]

    removed_only = client.get(f"{BASE}/catalog", params={"view": "removed"})
    assert removed_only.status_code == 200
    assert [skill["id"] for skill in removed_only.json()["skills"]] == [removed_id]
    tombstone = removed_only.json()["skills"][0]
    assert tombstone["state"] == "permanently_removed"
    assert tombstone["hidden_reason"] == "purged"
    assert tombstone["purge_id"] == purge.json()["purge_id"]
    assert tombstone["purged_at"] == purge.json()["purged_at"]

    recreate = client.post(BASE, json=_body("removed-reservation"))
    assert recreate.status_code == 409
    assert recreate.json()["detail"]["code"] == "slug_permanently_reserved"


def test_catalog_rejects_unknown_view_and_removed_view_can_be_empty(client_with_runtime):
    client, _org = client_with_runtime
    _create(client, slug="ordinary-only")

    empty = client.get(f"{BASE}/catalog", params={"view": "removed"})
    assert empty.status_code == 200
    assert empty.json() == {"skills": []}

    unknown = client.get(f"{BASE}/catalog", params={"view": "everything"})
    assert unknown.status_code == 422


def test_purge_requires_retirement_and_exact_slug_then_is_stably_idempotent(client_with_runtime):
    client, org = client_with_runtime
    created = _create(client, slug="logical-only")
    skill_id = created["skill_id"]
    assert client.post(f"{BASE}/{skill_id}/purge", json={"typed_slug": "logical-only"}).json()["detail"]["code"] == "skill_not_retired"
    assert client.post(f"{BASE}/{skill_id}/retire", json={"reason": "done"}).status_code == 200
    mismatch = client.post(f"{BASE}/{skill_id}/purge", json={"typed_slug": "wrong"})
    assert mismatch.status_code == 422
    assert mismatch.json()["detail"]["code"] == "typed_slug_mismatch"

    conn = getattr(org.db, "_conn", org.db)
    retained_before = {
        table: conn.execute(f"SELECT count(*) FROM {table} WHERE skill_id=?", (skill_id,)).fetchone()[0]
        for table in (
            "custom_skill_versions", "custom_skill_events",
            "custom_skill_eligibility_rules", "custom_skill_materializations",
        )
    }
    first = client.post(f"{BASE}/{skill_id}/purge", json={"typed_slug": "logical-only"})
    assert first.status_code == 200
    assert first.json()["state"] == "permanently_removed"
    assert first.json()["physical_erasure"] == 0
    replay = client.post(f"{BASE}/{skill_id}/purge", json={"typed_slug": "logical-only"})
    assert replay.status_code == 200
    assert replay.json()["purge_id"] == first.json()["purge_id"]
    assert replay.json()["already_purged"] is True
    assert {
        table: conn.execute(f"SELECT count(*) FROM {table} WHERE skill_id=?", (skill_id,)).fetchone()[0]
        for table in retained_before
    } == retained_before
    detail = client.get(f"{BASE}/{skill_id}").json()
    assert detail["state"] == "permanently_removed"
    assert "skill_md_cache" not in detail
    assert client.post(f"{BASE}/{skill_id}/restore").status_code == 410
    assert client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": _FM_BODY + "two"}).status_code == 410


def test_purge_policy_withdrawal_is_old_resolver_compatibility_latch(client_with_runtime):
    """Downgrade plus explicit old restore still resolves fail-closed."""
    from runtime.skills.custom import service
    from runtime.skills.eligibility import (
        EligibilityRecipient, SkillEligibilityState, resolve_custom_skill_eligibility,
    )

    client, org = client_with_runtime
    created = _create(client, slug="old-restore-latch")
    skill_id = created["skill_id"]
    response = client.put(
        f"{BASE}/{skill_id}/eligibility",
        headers={"If-Match": str(created["version_id"])},
        json=[{"scope_type": "org", "scope_target": None, "effect": "allow"}],
    )
    assert response.status_code == 200, response.text
    conn = getattr(org.db, "_conn", org.db)
    historical_count = conn.execute(
        "SELECT count(*) FROM custom_skill_eligibility_rules WHERE skill_id=?", (skill_id,)
    ).fetchone()[0]

    client.post(f"{BASE}/{skill_id}/retire", json={})
    assert client.post(
        f"{BASE}/{skill_id}/purge", json={"typed_slug": "old-restore-latch"}
    ).status_code == 200
    purged = conn.execute("SELECT * FROM custom_skills WHERE id=?", (skill_id,)).fetchone()
    assert purged["retired_at"] is not None
    assert len(service.current_rules(conn, skill_id)) == 0
    assert conn.execute(
        "SELECT count(*) FROM custom_skill_eligibility_rules WHERE skill_id=?", (skill_id,)
    ).fetchone()[0] == historical_count

    # Exact preserved-old restore behavior: it clears retirement only and is
    # unaware of purged_at. Its existing resolver then sees the withdrawn
    # current policy while all historical rule rows remain retained.
    conn.execute(
        "UPDATE custom_skills SET retired_at=NULL,retired_by=NULL,retired_reason=NULL WHERE id=?",
        (skill_id,),
    )
    result = resolve_custom_skill_eligibility(
        SkillEligibilityState(False, "valid"),
        service.current_rules(conn, skill_id),
        EligibilityRecipient("dev_agent", ("engineering",)),
    )
    assert result.visible is False
    assert result.reason == "no_eligibility_policy"


def test_purge_refuses_when_foreign_keys_are_disabled(client_with_runtime):
    client, org = client_with_runtime
    created = _create(client, slug="fk-off")
    client.post(f"{BASE}/{created['skill_id']}/retire", json={})
    conn = getattr(org.db, "_conn", org.db)
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        response = client.post(f"{BASE}/{created['skill_id']}/purge", json={"typed_slug": "fk-off"})
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "schema_contract_unsupported"
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def test_purge_refuses_a_partially_migrated_schema(client_with_runtime):
    client, org = client_with_runtime
    created = _create(client, slug="partial-schema")
    client.post(f"{BASE}/{created['skill_id']}/retire", json={})
    conn = getattr(org.db, "_conn", org.db)
    conn.execute("DROP TABLE custom_skill_purge_events")

    response = client.post(
        f"{BASE}/{created['skill_id']}/purge", json={"typed_slug": "partial-schema"}
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "schema_contract_unsupported"


def test_purge_rolls_back_policy_withdrawal_and_tombstone_on_failure(
    client_with_runtime, monkeypatch,
):
    from runtime.skills.custom import service

    client, org = client_with_runtime
    created = _create(client, slug="rollback-purge")
    skill_id = created["skill_id"]
    assert client.put(
        f"{BASE}/{skill_id}/eligibility",
        headers={"If-Match": str(created["version_id"])},
        json=[{"scope_type": "org", "scope_target": None, "effect": "allow"}],
    ).status_code == 200
    client.post(f"{BASE}/{skill_id}/retire", json={})
    conn = getattr(org.db, "_conn", org.db)
    original = service.replace_rules

    def fail_after_withdrawal(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("fault after policy withdrawal")

    monkeypatch.setattr(service, "replace_rules", fail_after_withdrawal)
    with pytest.raises(RuntimeError, match="fault after policy withdrawal"):
        client.post(f"{BASE}/{skill_id}/purge", json={"typed_slug": "rollback-purge"})

    row = conn.execute("SELECT purged_at,purge_id FROM custom_skills WHERE id=?", (skill_id,)).fetchone()
    assert tuple(row) == (None, None)
    assert service.purge_tombstone(conn, skill_id) is None
    assert len(service.current_rules(conn, skill_id)) == 1


def test_set_executor_purge_after_selection_excludes_skill_from_both_roots(
    client_with_runtime, monkeypatch, tmp_path,
):
    """The real executor-switch route publishes only the authoritative set."""
    import threading
    from contextlib import contextmanager

    from runtime.daemon.routes import agents as agent_routes
    from runtime.orchestrator import workspace_adapters as wa
    from runtime.skills.custom import service

    client, org = client_with_runtime
    _add_agent(org)
    racing = _create(client, slug="purged-during-build")
    unaffected = _create(client, slug="unaffected-during-build")
    other_org = _create(client, slug="other-org-during-build")
    allow = [{"scope_type": "org", "scope_target": None, "effect": "allow"}]
    for created in (racing, unaffected, other_org):
        assert client.put(
            f"{BASE}/{created['skill_id']}/eligibility", json=allow,
            headers={"If-Match": str(created["version_id"])},
        ).status_code == 200
    conn = getattr(org.db, "_conn", org.db)
    conn.execute(
        "UPDATE custom_skills SET org_slug='beta' WHERE id=?",
        (other_org["skill_id"],),
    )
    conn.commit()

    monkeypatch.setenv("HAPPYRANCH_CANONICAL_STORE_ROOT", str(tmp_path / "canonical"))
    selected = threading.Event()
    release = threading.Event()
    original_barrier = service.canonical_publication_barrier

    @contextmanager
    def paused_barrier(org_slug):
        if threading.get_ident() == outcome.get("publisher_ident"):
            selected.set()
            assert release.wait(2)
        with original_barrier(org_slug):
            yield

    monkeypatch.setattr(service, "canonical_publication_barrier", paused_barrier)
    outcome = {}
    workspace = org.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    original_materialize = wa._materialize_unified_canonical

    def capture_specs(*args, **kwargs):
        outcome["publisher_ident"] = threading.get_ident()
        specs = original_materialize(*args, **kwargs)
        outcome["specs"] = specs
        return specs

    monkeypatch.setattr(wa, "_materialize_unified_canonical", capture_specs)
    monkeypatch.setattr(
        agent_routes.ContextBuilder, "ensure_workspace_ready",
        lambda self, *args, **kwargs: None,
    )

    def publish():
        outcome["response"] = client.put(
            "/api/v1/orgs/alpha/agents/dev_agent/executor",
            json={"executor": "codex"},
        )

    worker = threading.Thread(target=publish, name="canonical-publisher")
    worker.start()
    assert selected.wait(2)
    assert client.post(f"{BASE}/{racing['skill_id']}/retire", json={}).status_code == 200
    purged = client.post(
        f"{BASE}/{racing['skill_id']}/purge",
        json={"typed_slug": "purged-during-build"},
    )
    assert purged.status_code == 200
    release.set()
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert outcome["response"].status_code == 200, outcome["response"].text

    slugs = {spec["slug"] for spec in outcome["specs"]}
    assert "purged-during-build" not in slugs
    assert "unaffected-during-build" in slugs
    beta_workspace = tmp_path / "beta-workspace"
    beta_workspace.mkdir()
    beta_specs = original_materialize(
        beta_workspace, org.settings, slug="beta", context="task", provider="codex",
        agent_name="dev_agent", team="engineering", task_id="TASK-BETA",
        session_id="sess-beta", org_root=org.root, db=org.db,
        skills_root=org.settings.project_root / "runtime" / "skills",
    )
    assert "other-org-during-build" in {spec["slug"] for spec in beta_specs}
    for root in (".claude/skills", ".agents/skills"):
        assert not (workspace / root / "purged-during-build").exists()
        assert (workspace / root / "unaffected-during-build").exists()
        assert (beta_workspace / root / "other-org-during-build").exists()


def test_canonical_publication_barrier_is_org_scoped_and_exception_safe():
    import threading

    from runtime.skills.custom import service

    alpha_entered = threading.Event()
    release_alpha = threading.Event()
    same_org_entered = threading.Event()
    other_org_entered = threading.Event()

    def hold_alpha():
        with service.canonical_publication_barrier("alpha"):
            alpha_entered.set()
            assert release_alpha.wait(2)

    def enter(org_slug, entered):
        with service.canonical_publication_barrier(org_slug):
            entered.set()

    holder = threading.Thread(target=hold_alpha)
    same_org = threading.Thread(target=enter, args=("alpha", same_org_entered))
    other_org = threading.Thread(target=enter, args=("beta", other_org_entered))
    holder.start()
    assert alpha_entered.wait(2)
    same_org.start()
    other_org.start()
    assert other_org_entered.wait(2)
    assert not same_org_entered.wait(0.05)
    release_alpha.set()
    for worker in (holder, same_org, other_org):
        worker.join(timeout=2)
        assert not worker.is_alive()
    assert same_org_entered.is_set()

    with pytest.raises(RuntimeError, match="publication fault"):
        with service.canonical_publication_barrier("alpha"):
            raise RuntimeError("publication fault")
    with service.canonical_publication_barrier("alpha"):
        pass


def test_b2_recover_deletes_only_corrupt_version_with_audit(
    client_with_runtime, monkeypatch,
):
    """The retained operator route repairs a refused B2 canonical package."""
    from runtime.infrastructure.artifact_store import ArtifactStore
    from runtime.orchestrator._paths import OrgPaths
    from runtime.skills.canonical_store import CanonicalSkillStore, _make_writable_for_removal

    client, org = client_with_runtime
    created = _create(client, slug="recoverable-b2", skill_md=_fm_body("recoverable-b2", "original"))
    current = client.post(
        f"{BASE}/{created['skill_id']}/versions",
        json={"skill_md": _fm_body("recoverable-b2", "current")},
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
    created = _create(client, slug="stale-recoverable-b2", skill_md=_fm_body("stale-recoverable-b2", "version a"))
    current = client.post(
        f"{BASE}/{created['skill_id']}/versions",
        json={"skill_md": _fm_body("stale-recoverable-b2", "version b")},
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
    version = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": _fm_body("test-skill", "two")}).json()
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
    ("---\nname: x\n---\nplain text without a heading\n", "frontmatter_name_slug_mismatch"),
    ("---\nname: x\n---\n\n", "frontmatter_name_slug_mismatch"),
    ("plain text without frontmatter", "skill_md_no_frontmatter"),
    # malformed heading-LIKE candidates: hash-prefixed but NOT ATX headings
    # (1-6 hashes followed by whitespace/EOL) — invalid evidence, same rules
    ("#not-a-heading\n", "skill_md_no_frontmatter"),
    ("####### Too many hashes\n", "skill_md_no_frontmatter"),
    # THR-262 retires the body-heading grammar for new writes: heading-first
    # documents (and post-frontmatter body headings) are no longer accepted.
    ("---\nname: x\n---\n#not-a-heading\n", "frontmatter_name_slug_mismatch"),
    ("---\nname: x\n---\n####### Seven hashes\n", "frontmatter_name_slug_mismatch"),
    ("# Heading-first body\n\nBody text.\n", "skill_md_no_frontmatter"),
    ("## Heading-first level two\n\nBody text.\n", "skill_md_no_frontmatter"),
    ("# Heading without trailing newline", "skill_md_no_frontmatter"),
    ("#\n", "skill_md_no_frontmatter"),
    ("###### Level-six heading\n\nBody text.\n", "skill_md_no_frontmatter"),
    ("######\n", "skill_md_no_frontmatter"),
    ("#\tTab-separated heading\n\nBody text.\n", "skill_md_no_frontmatter"),
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
    successor = _fm_body("test-skill", "updated")
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


@pytest.mark.parametrize("successor", [
    "# Heading-first body\n\nBody text.\n",
    "## Heading-first level two\n\nBody text.\n",
    "# Heading without trailing newline",
    "#\n",
    "###### Level-six heading\n\nBody text.\n",
    "######\n",
    "#\tTab-separated heading\n\nBody text.\n",
])
def test_add_version_appends_heading_first_successor_as_invalid_evidence(
    client_with_runtime, successor,
):
    """THR-262 retires the heading-first grammar for new writes. A heading-first
    successor is appended as immutable invalid evidence (201) with its
    content-addressed artifact and the usual events, but NEVER displaces the
    retained valid current pointer. The old THR-210 PR-2 acceptance is
    superseded by the accepted seq27 field policy; this is the same
    invalid-evidence retention contract as any other invalid successor."""
    client, org = client_with_runtime
    created = _create(client)
    skill_id, prior_revision = created["skill_id"], created["version_id"]
    response = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": successor})
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["validation_state"] == "invalid"
    assert payload["current_version_id"] == prior_revision
    conn = getattr(org.db, "_conn", org.db)
    assert conn.execute(
        "SELECT current_version_id FROM custom_skills WHERE id=?", (skill_id,)
    ).fetchone()["current_version_id"] == prior_revision
    events = [
        r["event_type"]
        for r in conn.execute(
            "SELECT event_type FROM custom_skill_events WHERE skill_id=? ORDER BY id",
            (skill_id,),
        )
    ]
    assert events == ["created", "validated", "version_saved", "validated"]
    digest = hashlib.sha256(successor.encode()).hexdigest()
    assert f"custom-skills/test-skill/{digest}/SKILL.md" in _artifact_keys(org)
    detail = client.get(f"{BASE}/{skill_id}").json()
    assert detail["validation_state"] == "valid"
    assert detail["version_id"] == prior_revision


def test_heading_first_replay_conflicts_as_version_content_exists(client_with_runtime, monkeypatch):
    """THR-262 replay precedence: a byte-identical heading-first (invalid
    evidence) body replay conflicts with the append-only UNIQUE
    (skill_id, content_hash) invariant as HTTP 409 `version_content_exists` —
    zero artifact rewrite (instrumented no-write seam stays empty; artifact
    bytes/metadata unchanged), zero new version row, zero new event row, zero
    current_version_id change. Replay 409 precedes any divergence handling."""
    client, org = client_with_runtime
    created = _create(client)
    skill_id = created["skill_id"]
    heading_first = "# Heading-first body\n\nBody text.\n"
    first = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": heading_first})
    assert first.status_code == 201 and first.json()["validation_state"] == "invalid"
    before = _residue_snapshot(org, skill_id)
    before_bytes = _artifact_bytes_state(org)
    write_calls = _no_write_artifact_seam(monkeypatch)
    response = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": heading_first})
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "version_content_exists"
    assert write_calls == []
    assert _residue_snapshot(org, skill_id) == before
    assert _artifact_bytes_state(org) == before_bytes


def test_frontmatter_initial_creation_is_valid_and_materializable(
    client_with_runtime, monkeypatch,
):
    """THR-262 C1: a frontmatter-first body on INITIAL creation is a valid
    first version, becomes the current pointer, is eligible, and materializes
    through the canonical store."""
    from runtime.skills.canonical_store import CanonicalSkillStore
    from runtime.orchestrator.workspace_adapters import _build_custom_skill_canonical_specs

    client, org = client_with_runtime
    _add_agent(org)
    created = _create(client, slug="heading-create")
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


def test_agent_create_accepts_frontmatter_body_with_provenance(client_with_runtime):
    """Agent path (THR-262 C1): a frontmatter-first body creates a VALID first
    version with verified task/session provenance and advances the pointer."""
    client, org = client_with_runtime
    org.db.insert_task(TaskRecord(id="TASK-HF2", brief="create a custom skill"))
    org.sessions.set_active("TASK-HF2", "dev_agent", "sess-hf2", org_slug="alpha")
    client.headers.pop("Authorization", None)
    agent_body = _fm_body("agent-heading", "agent created")
    response = client.post(
        f"{BASE}/agent-create", params={"session_id": "sess-hf2"},
        json={"slug": "agent-heading", "name": "Agent Heading", "skill_md": agent_body},
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
    created = _create(client, slug="heading-like-b2")
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
    # a conforming frontmatter successor is valid and advances the pointer
    valid_md = _fm_body("bad-heading-like", "now valid")
    advanced = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": valid_md})
    assert advanced.status_code == 201, advanced.text
    assert advanced.json()["validation_state"] == "valid"
    assert conn.execute(
        "SELECT current_version_id FROM custom_skills WHERE id=?", (skill_id,)
    ).fetchone()["current_version_id"] == advanced.json()["version_id"]


def test_add_version_replay_conflicts_as_version_content_exists(client_with_runtime, monkeypatch):
    """THR-210 PR 3 (human add-version surface): a byte-identical body replay
    (TASK-5741 failure mode) conflicts as HTTP 409 `version_content_exists`
    with zero durable residue — no artifact rewrite, no new version/event row,
    no pointer change; the append-only (skill_id, content_hash) uniqueness
    invariant is preserved, never relaxed."""
    client, org = client_with_runtime
    created = _create(client)
    skill_id = created["skill_id"]
    before = _residue_snapshot(org, skill_id)
    before_bytes = _artifact_bytes_state(org)
    write_calls = _no_write_artifact_seam(monkeypatch)
    response = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": _FM_BODY})
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "version_content_exists"
    assert write_calls == []
    assert _residue_snapshot(org, skill_id) == before
    assert _artifact_bytes_state(org) == before_bytes


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
    valid_md = _fm_body("bad-create", "now valid")
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

def test_invalid_replay_conflicts_as_version_content_exists(client_with_runtime, monkeypatch):
    """THR-210 PR 3: a byte-identical INVALID body replay still conflicts
    with the append-only UNIQUE (skill_id, content_hash) invariant as HTTP
    409 `version_content_exists` with zero additional residue — the same
    evidence is never appended twice, and the invalid evidence artifact is
    never rewritten."""
    client, org = client_with_runtime
    created = _create(client)
    skill_id = created["skill_id"]
    invalid_md = "---\nname: x\n---\nno heading\n"
    first = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": invalid_md})
    assert first.status_code == 201 and first.json()["validation_state"] == "invalid"
    before = _residue_snapshot(org, skill_id)
    before_bytes = _artifact_bytes_state(org)
    write_calls = _no_write_artifact_seam(monkeypatch)
    response = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": invalid_md})
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "version_content_exists"
    assert write_calls == []
    assert _residue_snapshot(org, skill_id) == before
    assert _artifact_bytes_state(org) == before_bytes


def test_agent_create_replay_conflicts_as_version_content_exists(client_with_runtime, monkeypatch):
    """THR-210 PR 3 (agent create/update surface): an agent re-submitting an
    identical create body for its own already-created skill conflicts as HTTP
    409 `version_content_exists` — zero residue: no new version/event row, no
    artifact write, no current_version_id change."""
    client, org = client_with_runtime
    org.db.insert_task(TaskRecord(id="TASK-R1", brief="create a custom skill"))
    org.sessions.set_active("TASK-R1", "dev_agent", "sess-r1", org_slug="alpha")
    client.headers.pop("Authorization", None)
    first = client.post(
        f"{BASE}/agent-create", params={"session_id": "sess-r1"}, json=_body("replay-agent")
    )
    assert first.status_code == 201, first.text
    skill_id = first.json()["skill"]["id"]
    before = _residue_snapshot(org, skill_id)
    before_bytes = _artifact_bytes_state(org)
    write_calls = _no_write_artifact_seam(monkeypatch)
    response = client.post(
        f"{BASE}/agent-create", params={"session_id": "sess-r1"}, json=_body("replay-agent")
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "version_content_exists"
    assert write_calls == []
    assert _residue_snapshot(org, skill_id) == before
    assert _artifact_bytes_state(org) == before_bytes


def test_agent_update_replay_conflicts_as_version_content_exists(client_with_runtime, monkeypatch):
    """THR-210 PR 3 (agent update surface): after an agent advances its own
    skill to body B, re-submitting the identical body B conflicts as HTTP 409
    `version_content_exists` — the pointer stays at B's version, and no new
    version/event row or artifact write appears."""
    client, org = client_with_runtime
    org.db.insert_task(TaskRecord(id="TASK-R2", brief="create a custom skill"))
    org.sessions.set_active("TASK-R2", "dev_agent", "sess-r2", org_slug="alpha")
    client.headers.pop("Authorization", None)
    first = client.post(
        f"{BASE}/agent-create", params={"session_id": "sess-r2"}, json=_body("replay-update")
    )
    assert first.status_code == 201, first.text
    skill_id = first.json()["skill"]["id"]
    body_b = _fm_body("replay-update", "two")
    updated = client.post(
        f"{BASE}/agent-create", params={"session_id": "sess-r2"},
        json=_body("replay-update", body_b),
    )
    assert updated.status_code == 201, updated.text
    v2 = updated.json()["version"]["id"]
    conn = getattr(org.db, "_conn", org.db)
    assert conn.execute(
        "SELECT current_version_id FROM custom_skills WHERE id=?", (skill_id,)
    ).fetchone()["current_version_id"] == v2
    before = _residue_snapshot(org, skill_id)
    before_bytes = _artifact_bytes_state(org)
    write_calls = _no_write_artifact_seam(monkeypatch)
    response = client.post(
        f"{BASE}/agent-create", params={"session_id": "sess-r2"},
        json=_body("replay-update", body_b),
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "version_content_exists"
    assert write_calls == []
    assert _residue_snapshot(org, skill_id) == before
    assert _artifact_bytes_state(org) == before_bytes
    assert conn.execute(
        "SELECT current_version_id FROM custom_skills WHERE id=?", (skill_id,)
    ).fetchone()["current_version_id"] == v2


def test_replay_of_noncurrent_historical_version_conflicts_without_residue(client_with_runtime, monkeypatch):
    """THR-210 PR 3: the uniqueness conflict applies against ANY stored
    version of the skill, not just the current one. Replaying a body identical
    to a NON-current historical version still returns HTTP 409
    `version_content_exists` with zero residue — the current pointer and every
    version/event/artifact stay exactly as they were."""
    client, org = client_with_runtime
    created = _create(client)
    skill_id = created["skill_id"]
    successor = _fm_body("test-skill", "two")
    advanced = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": successor})
    assert advanced.status_code == 201 and advanced.json()["validation_state"] == "valid"
    v2 = advanced.json()["version_id"]
    before = _residue_snapshot(org, skill_id)
    before_bytes = _artifact_bytes_state(org)
    write_calls = _no_write_artifact_seam(monkeypatch)
    # _FM_BODY is now the NON-current historical v1
    response = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": _FM_BODY})
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "version_content_exists"
    assert write_calls == []
    assert _residue_snapshot(org, skill_id) == before
    assert _artifact_bytes_state(org) == before_bytes
    conn = getattr(org.db, "_conn", org.db)
    assert conn.execute(
        "SELECT current_version_id FROM custom_skills WHERE id=?", (skill_id,)
    ).fetchone()["current_version_id"] == v2


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
    valid_md = _fm_body("lineage-skill", "now valid")
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
    valid_md = _fm_body("legacy-history", "healed by valid successor")
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
    created = _create(client, slug="materializable-b2")
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
# (skill_id, content_hash) violation is version_content_exists (409); every later
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
    mapped to 409 version_content_exists; all later failures return a
    non-duplicate error (500) and compensate the artifact + empty dirs this
    request created, with zero durable residue in version rows, events,
    current_version_id, parent/current lineage, artifacts, empty directories,
    materialization, and any temporary parent. The `commit` stage (TASK-5803)
    fails the final conn.commit() — compensation must stay armed through
    successful commit so a commit failure rolls back the DB AND removes the
    request-written artifact, not just the DB rows."""
    client, org = client_with_runtime
    real_conn = getattr(org.db, "_conn", org.db)
    from runtime.skills.skill_md import skill_md_contract_violations

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
        # R5a: a conforming slug-matched successor with a DIFFERENT description,
        # proven valid before fault injection, so the route actually executes
        # valid description projection before the injected failure.
        successor = _fm_body("owned-fault", "owned-fault-two")
        assert skill_md_contract_violations(successor, expected_slug="owned-fault") == []
        assert "owned-fault-two" != created.json()["skill"]["description"]
        _install_persistence_fault(monkeypatch, org, stage)
        fault = _fault_client(client)
        fault.headers.pop("Authorization", None)
        before = _residue_snapshot(org, skill_id, conn=real_conn)
        response = fault.post(
            f"{BASE}/agent-create", params={"session_id": "sess-own2"},
            json=_body("owned-fault", successor),
        )
    elif surface == "human-version":
        created = _create(client, slug=f"fault-{stage}")
        skill_id = created["skill_id"]
        successor = _fm_body(f"fault-{stage}", "fault-two")
        assert skill_md_contract_violations(
            successor, expected_slug=f"fault-{stage}"
        ) == []
        assert _catalog_description(client, skill_id) != "fault-two"
        _install_persistence_fault(monkeypatch, org, stage)
        fault = _fault_client(client)
        before = _residue_snapshot(org, skill_id, conn=real_conn)
        response = fault.post(
            f"{BASE}/{skill_id}/versions", json={"skill_md": successor}
        )
    else:
        raise AssertionError(surface)

    if stage == "version-insert":
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "version_content_exists"
    else:
        # A post-INSERT integrity failure or artifact-write crash must NOT be
        # reported as version_content_exists, and must leave zero durable residue.
        assert response.status_code == 500, response.text
        assert "version_content_exists" not in response.text
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
    version_content_exists; everything later is 500) and rolls back with full
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
        assert response.json()["detail"]["code"] == "version_content_exists"
    else:
        assert response.status_code == 500, response.text
        assert "version_content_exists" not in response.text
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
    # a NEW heading-first successor is now invalid evidence too (THR-262
    # retires the heading grammar), retaining the PR-1-era invalid current.
    heading_successor = "# New heading-first\n\nRetired under THR-262.\n"
    appended = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": heading_successor})
    assert appended.status_code == 201, appended.text
    assert appended.json()["validation_state"] == "invalid"
    assert appended.json()["current_version_id"] == v_pr1
    # a conforming frontmatter successor advances the pointer
    successor = _fm_body("pr1-heading-evidence", "conforming successor")
    advanced = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": successor})
    assert advanced.status_code == 201, advanced.text
    assert advanced.json()["validation_state"] == "valid"
    assert advanced.json()["current_version_id"] == advanced.json()["version_id"]
    # the PR-1-era evidence row is byte-identical after the appends
    rows_after = [
        dict(r) for r in conn.execute(
            "SELECT id, validation_state, content_hash, skill_md_cache, "
            "validation_findings, validator_version FROM custom_skill_versions "
            "WHERE skill_id=? ORDER BY id", (skill_id,)
        )
    ]
    assert rows_before == rows_after[: len(rows_before)]
    assert [r["validation_state"] for r in rows_after[len(rows_before):]] == [
        "invalid",
        "valid",
    ]


# ═══════════════════════════════════════════════════════════════════════════
# THR-262 / seq27 description projection, replay precedence and PATCH
# ═══════════════════════════════════════════════════════════════════════════

def _catalog_description(client, skill_id):
    return client.get(f"{BASE}/{skill_id}").json()["description"]


def test_omitted_and_json_null_request_description_derive(client_with_runtime):
    """C4: an omitted (or JSON-null) request description derives from the
    validated frontmatter description; the catalog record is projected."""
    client, _org = client_with_runtime
    omitted = client.post(
        BASE, json={"slug": "derive-omitted", "name": "Omitted",
                    "skill_md": _fm_body("derive-omitted", "omitted-desc")},
    )
    assert omitted.status_code == 201, omitted.text
    assert omitted.json()["validation_state"] == "valid"
    assert _catalog_description(client, omitted.json()["skill_id"]) == "omitted-desc"

    nulled = client.post(
        BASE, json={"slug": "derive-null", "name": "Nulled", "description": None,
                    "skill_md": _fm_body("derive-null", "null-desc")},
    )
    assert nulled.status_code == 201, nulled.text
    assert nulled.json()["validation_state"] == "valid"
    assert _catalog_description(client, nulled.json()["skill_id"]) == "null-desc"


def test_matching_explicit_description_is_accepted(client_with_runtime):
    """C4: an explicitly supplied description equal to the validated
    frontmatter description is accepted."""
    client, _org = client_with_runtime
    response = client.post(
        BASE, json={"slug": "match-desc", "name": "Match", "description": "same",
                    "skill_md": _fm_body("match-desc", "same")},
    )
    assert response.status_code == 201, response.text
    assert _catalog_description(client, response.json()["skill_id"]) == "same"


@pytest.mark.parametrize("supplied", ["", "   ", "different"])
def test_divergent_explicit_description_is_422_with_zero_residue(
    client_with_runtime, supplied,
):
    """C4: an explicit API string (including ''/whitespace) that differs from
    the validated frontmatter description is a pre-persistence 422 with no
    artifact/version/event/metadata residue."""
    client, org = client_with_runtime
    before = _residue_snapshot(org, None)
    response = client.post(
        BASE, json={"slug": "divergent-desc", "name": "Divergent",
                    "description": supplied,
                    "skill_md": _fm_body("divergent-desc", "authoritative")},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "divergent_description"
    assert _residue_snapshot(org, None) == before


def test_valid_append_advances_pointer_and_projects_description_together(
    client_with_runtime,
):
    """C4: a valid append updates the catalog description in the same
    transaction that advances current_version_id; the display label is
    independent of the slug/projection."""
    client, org = client_with_runtime
    created = _create(client, slug="projection-skill")
    skill_id = created["skill_id"]
    conn = getattr(org.db, "_conn", org.db)
    assert _catalog_description(client, skill_id) == "test"
    response = client.post(
        f"{BASE}/{skill_id}/versions",
        json={"skill_md": _fm_body("projection-skill", "projected-two")},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["validation_state"] == "valid"
    row = conn.execute(
        "SELECT current_version_id, description, name FROM custom_skills WHERE id=?",
        (skill_id,),
    ).fetchone()
    assert row["current_version_id"] == payload["version_id"]
    assert row["description"] == "projected-two"
    assert row["name"] == "Test skill"


def test_invalid_append_never_projects_description_or_advances_pointer(
    client_with_runtime,
):
    """C5: an invalid append retains the prior valid pointer and leaves the
    projected catalog description untouched."""
    client, org = client_with_runtime
    created = _create(client, slug="no-project-invalid")
    skill_id, v1 = created["skill_id"], created["version_id"]
    conn = getattr(org.db, "_conn", org.db)
    invalid = client.post(
        f"{BASE}/{skill_id}/versions",
        json={"skill_md": "---\nname: no-project-invalid\n---\n"},
    )
    assert invalid.status_code == 201, invalid.text
    assert invalid.json()["validation_state"] == "invalid"
    row = conn.execute(
        "SELECT current_version_id, description FROM custom_skills WHERE id=?",
        (skill_id,),
    ).fetchone()
    assert row["current_version_id"] == v1
    assert row["description"] == "test"


def test_replay_409_precedes_divergent_description(client_with_runtime, monkeypatch):
    """C4/C5: an exact byte-identical replay with a divergent request
    description is 409 version_content_exists, not 422 divergent_description."""
    client, org = client_with_runtime
    created = _create(client, slug="replay-divergent")
    skill_id = created["skill_id"]
    body = _fm_body("replay-divergent", "replayed")
    first = client.post(
        f"{BASE}/{skill_id}/versions", json={"skill_md": body, "description": "replayed"}
    )
    assert first.status_code == 201, first.text
    before = _residue_snapshot(org, skill_id)
    write_calls = _no_write_artifact_seam(monkeypatch)
    replay = client.post(
        f"{BASE}/{skill_id}/versions", json={"skill_md": body, "description": "divergent"}
    )
    assert replay.status_code == 409, replay.text
    assert replay.json()["detail"]["code"] == "version_content_exists"
    assert write_calls == []
    assert _residue_snapshot(org, skill_id) == before


def test_first_invalid_creation_uses_request_fallback_and_stays_dark(
    client_with_runtime,
):
    """C5: an invalid FIRST creation keeps the request-metadata description
    fallback, becomes the current (dark) pointer, and projects nothing."""
    client, org = client_with_runtime
    response = client.post(
        BASE,
        json={"slug": "first-invalid-desc", "name": "First Invalid",
              "description": "requested fallback",
              "skill_md": "#not-a-heading\n"},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["validation_state"] == "invalid"
    conn = getattr(org.db, "_conn", org.db)
    row = conn.execute(
        "SELECT current_version_id, description FROM custom_skills WHERE id=?",
        (payload["skill_id"],),
    ).fetchone()
    assert row["current_version_id"] == payload["version_id"]
    assert row["description"] == "requested fallback"


def test_new_rows_record_thr262_validator_version(client_with_runtime):
    """C5/D5: new rows record the THR-262/1.0.0 validator marker."""
    client, org = client_with_runtime
    created = _create(client, slug="marker-skill")
    conn = getattr(org.db, "_conn", org.db)
    row = conn.execute(
        "SELECT validator_version FROM custom_skill_versions WHERE id=?",
        (created["version_id"],),
    ).fetchone()
    assert row["validator_version"] == "THR-262/1.0.0"


def test_patch_description_valid_current_equal_accepted_differing_422(
    client_with_runtime,
):
    """C6: on a valid current version with a frontmatter description, PATCH
    accepts the equal value and rejects a different one with
    description_divergence. The display name stays independently editable."""
    client, _org = client_with_runtime
    created = _create(client, slug="patch-valid", skill_md=_fm_body("patch-valid", "fm-desc"))
    skill_id = created["skill_id"]
    equal = client.patch(f"{BASE}/{skill_id}", json={"description": "fm-desc"})
    assert equal.status_code == 200, equal.text
    assert equal.json()["description"] == "fm-desc"
    differing = client.patch(f"{BASE}/{skill_id}", json={"description": "other"})
    assert differing.status_code == 422, differing.text
    assert differing.json()["detail"]["code"] == "description_divergence"
    renamed = client.patch(f"{BASE}/{skill_id}", json={"name": "Renamed Label"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Renamed Label"
    assert renamed.json()["description"] == "fm-desc"


def test_patch_description_invalid_current_accepts_only_unchanged_stored(
    client_with_runtime,
):
    """C6: a current version that is invalid (or legacy heading-first with no
    frontmatter description) accepts only the unchanged stored description;
    any differing value is 422 description_requires_valid_version."""
    client, _org = client_with_runtime
    created = client.post(
        BASE,
        json={"slug": "patch-invalid", "name": "Patch Invalid",
              "description": "stored", "skill_md": "#not-a-heading\n"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["validation_state"] == "invalid"
    skill_id = created.json()["skill_id"]
    same = client.patch(f"{BASE}/{skill_id}", json={"description": "stored"})
    assert same.status_code == 200, same.text
    changed = client.patch(f"{BASE}/{skill_id}", json={"description": "changed"})
    assert changed.status_code == 422, changed.text
    assert changed.json()["detail"]["code"] == "description_requires_valid_version"


def test_patch_description_legacy_valid_heading_first_uses_stored_value(
    client_with_runtime,
):
    """C6: a valid legacy heading-first current version has no frontmatter
    description, so PATCH accepts only the unchanged stored catalog value and
    never parses the legacy body as a valid source."""
    from runtime.infrastructure.artifact_store import ArtifactStore
    from runtime.orchestrator._paths import OrgPaths
    from runtime.skills.custom import service as custom_service

    client, org = client_with_runtime
    conn = getattr(org.db, "_conn", org.db)
    skill_id = "custom:legacy-patch"
    content = "# Legacy heading-first\n\nNo frontmatter here.\n"
    artifact_key = ArtifactStore(OrgPaths(org.root).artifacts_dir).put(
        "custom-skills/legacy-patch/legacy/SKILL.md", content.encode(),
    ).name
    conn.execute(
        "INSERT INTO custom_skills (id,org_slug,slug,name,description,origin_kind,created_at,created_by) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (skill_id, "alpha", "legacy-patch", "Legacy Patch", "legacy stored", "human", custom_service.now(), "founder"),
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
    conn.execute("UPDATE custom_skills SET current_version_id=? WHERE id=?", (version_id, skill_id))
    conn.commit()

    same = client.patch(f"{BASE}/{skill_id}", json={"description": "legacy stored"})
    assert same.status_code == 200, same.text
    differing = client.patch(f"{BASE}/{skill_id}", json={"description": "Legacy heading-first"})
    assert differing.status_code == 422, differing.text
    assert differing.json()["detail"]["code"] == "description_requires_valid_version"


def test_agent_same_owner_append_projects_description(client_with_runtime):
    """C4: the real agent route (initial create and same-owner append) derives
    and projects the frontmatter description on a valid append."""
    client, org = client_with_runtime
    org.db.insert_task(TaskRecord(id="TASK-APPEND", brief="append a custom skill"))
    org.sessions.set_active("TASK-APPEND", "dev_agent", "sess-append", org_slug="alpha")
    client.headers.pop("Authorization", None)
    first = client.post(
        f"{BASE}/agent-create", params={"session_id": "sess-append"},
        json={"slug": "agent-append", "name": "Agent Append",
              "skill_md": _fm_body("agent-append", "agent-one")},
    )
    assert first.status_code == 201, first.text
    skill_id = first.json()["skill"]["id"]
    assert first.json()["skill"]["description"] == "agent-one"
    second = client.post(
        f"{BASE}/agent-create", params={"session_id": "sess-append"},
        json={"slug": "agent-append", "name": "Agent Append",
              "skill_md": _fm_body("agent-append", "agent-two")},
    )
    assert second.status_code == 201, second.text
    assert second.json()["version"]["validation_state"] == "valid"
    assert second.json()["skill"]["description"] == "agent-two"
    assert second.json()["skill"]["current_version_id"] == second.json()["version"]["id"]


# ═══════════════════════════════════════════════════════════════════════════
# TASK-8543 R4/R5: PATCH invalid-current parsing, transactional pointer
# re-read, replay/description matrix, Unicode/dual-root route proof
# ═══════════════════════════════════════════════════════════════════════════

def test_patch_invalid_current_never_parses_frontmatter(client_with_runtime, monkeypatch):
    """R4/C6: on an invalid current version the route branches on stored
    validity BEFORE parsing. The unchanged stored description succeeds with no
    parser call; a supplied value equal to the parseable invalid frontmatter
    description but different from the stored value is 422
    description_requires_valid_version, again with no parser call."""
    from runtime.daemon.routes import custom_skills as routes

    client, org = client_with_runtime
    invalid_md = (
        "---\nname: patch-invalid-parse\ndescription: parseable\nhooks: null\n---\n"
    )
    created = client.post(
        BASE,
        json={"slug": "patch-invalid-parse", "name": "Patch Invalid Parse",
              "description": "stored-different", "skill_md": invalid_md},
    )
    assert created.status_code == 201, created.text
    assert created.json()["validation_state"] == "invalid"
    skill_id = created.json()["skill_id"]

    calls: list = []

    def _spy(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError(
            "parse_skill_frontmatter must not run for an invalid current version"
        )

    monkeypatch.setattr(routes, "parse_skill_frontmatter", _spy)

    same = client.patch(f"{BASE}/{skill_id}", json={"description": "stored-different"})
    assert same.status_code == 200, same.text
    assert same.json()["description"] == "stored-different"
    before = _residue_snapshot(org, skill_id)
    other = client.patch(f"{BASE}/{skill_id}", json={"description": "parseable"})
    assert other.status_code == 422, other.text
    assert other.json()["detail"]["code"] == "description_requires_valid_version"
    assert calls == []
    assert _residue_snapshot(org, skill_id) == before


def test_patch_reads_current_pointer_inside_transaction(client_with_runtime, monkeypatch):
    """R5b/C6: deterministically advance the current pointer immediately before
    BEGIN acquisition. The route's transactional re-read must observe the new
    pointer and let it control the accepted 200/422 outcomes (no stale read, no
    invented 409, no residue on the rejected patch)."""
    import sqlite3 as _sqlite3

    from runtime.skills.custom import service

    client, org = client_with_runtime
    created = _create(client, slug="patch-reread", skill_md=_fm_body("patch-reread", "alpha"))
    skill_id, v1 = created["skill_id"], created["version_id"]
    real_conn = getattr(org.db, "_conn", org.db)

    # A second immutable VALID version with a different frontmatter
    # description, inserted directly with the pointer still on v1.
    v2_md = _fm_body("patch-reread", "beta")
    version2, _hash, _state = service.create_version(
        real_conn, skill_id=skill_id, skill_md=v2_md, actor_kind="human",
        actor="founder", artifact_key="custom-skills/patch-reread/v2/SKILL.md",
        validation={"ok": True, "errors": []}, parent_id=v1,
    )
    real_conn.commit()
    assert service.current(real_conn, skill_id)["version_id"] == v1

    state = {"advanced": False}

    class _AdvanceBeforeBeginProxy:
        def __getattr__(self, name):
            return getattr(real_conn, name)

        def execute(self, sql, *args, **kwargs):
            if not state["advanced"] and str(sql).strip().upper().startswith("BEGIN"):
                state["advanced"] = True
                real_conn.execute(
                    "UPDATE custom_skills SET current_version_id=? WHERE id=?",
                    (version2, skill_id),
                )
                real_conn.commit()
            return real_conn.execute(sql, *args, **kwargs)

    monkeypatch.setattr(org.db, "_conn", _AdvanceBeforeBeginProxy())

    accepted = client.patch(f"{BASE}/{skill_id}", json={"description": "beta"})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["description"] == "beta"
    before_reject = _residue_snapshot(org, skill_id, conn=real_conn)
    rejected = client.patch(f"{BASE}/{skill_id}", json={"description": "alpha"})
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["detail"]["code"] == "description_divergence"
    assert _residue_snapshot(org, skill_id, conn=real_conn) == before_reject
    row = real_conn.execute(
        "SELECT description, current_version_id FROM custom_skills WHERE id=?", (skill_id,)
    ).fetchone()
    assert row["description"] == "beta"
    assert row["current_version_id"] == version2


_REPLAY_MATRIX_BODIES = {
    "valid": "---\nname: replay-matrix\ndescription: candidate\n---\n",
    "invalid": "---\nname: replay-matrix\ndescription: candidate\nhooks: null\n---\n",
    "historical": "# Heading-first legacy\n\nbody\n",
}


@pytest.mark.parametrize("surface", ["human-append", "agent-same-owner-append"])
@pytest.mark.parametrize("kind", sorted(_REPLAY_MATRIX_BODIES))
@pytest.mark.parametrize("supplied", ["", "divergent"])
def test_replay_description_matrix_conflicts_before_artifacts(
    client_with_runtime, monkeypatch, surface, kind, supplied,
):
    """R5c/C4-C5: valid, invalid and historical bytes replayed with an explicit
    blank or divergent description are 409 version_content_exists BEFORE any
    artifact write, leaving rows/description/events unchanged."""
    client, org = client_with_runtime
    body = _REPLAY_MATRIX_BODIES[kind]

    if surface == "agent-same-owner-append":
        org.db.insert_task(TaskRecord(id="TASK-REPLAY", brief="append a custom skill"))
        org.sessions.set_active("TASK-REPLAY", "dev_agent", "sess-replay", org_slug="alpha")
        client.headers.pop("Authorization", None)
        first = client.post(
            f"{BASE}/agent-create", params={"session_id": "sess-replay"},
            json={"slug": "replay-matrix", "name": "Test skill", "skill_md": body},
        )
        assert first.status_code == 201, first.text
        skill_id = first.json()["skill"]["id"]
        before = _residue_snapshot(org, skill_id)
        write_calls = _no_write_artifact_seam(monkeypatch)
        replay = client.post(
            f"{BASE}/agent-create", params={"session_id": "sess-replay"},
            json={"slug": "replay-matrix", "name": "Test skill", "skill_md": body,
                  "description": supplied},
        )
    else:
        created = _create(client, slug="replay-matrix")
        skill_id = created["skill_id"]
        first = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": body})
        assert first.status_code == 201, first.text
        before = _residue_snapshot(org, skill_id)
        write_calls = _no_write_artifact_seam(monkeypatch)
        replay = client.post(
            f"{BASE}/{skill_id}/versions", json={"skill_md": body, "description": supplied}
        )

    assert replay.status_code == 409, replay.text
    assert replay.json()["detail"]["code"] == "version_content_exists"
    assert write_calls == []
    assert _residue_snapshot(org, skill_id) == before


#: Non-conforming logical identities (seq43 Option A §4.3.1 rows A5-A13, plus
#: the THR262seq48 concrete values ``wf-٣`` / ``wf-²`` / ``ｗf-1`` within
#: already-accepted non-ASCII categories).
_NONCONFORMING_LOGICAL_IDENTITIES = [
    "a" * 65,                 # A5 over-length
    "a" * 63 + "-b",          # A5 over-length, otherwise grammar-conforming
    "-a", "a-", "a--b",       # A6 hyphen boundaries / consecutive hyphen
    "My-Workflow",            # A7 uppercase ASCII (no lowercasing)
    "café-workflow",          # A8 precomposed accent
    "cafe\u0301-workflow",    # A9 decomposed accent (no NFC/NFKC)
    "my-workflow\n", "my-workflow ",  # A9b full-string (no `$` loophole)
    "库存盘点",                # A10 CJK
    "ａｂｃ", "１２３", "ｗf-1",   # A11 fullwidth letters/digits
    "١٢٣", "wf-٣",            # A12 Arabic-Indic digits
    "а-b",                    # A13 Cyrillic lookalike
    "wf-²",                   # THR262seq48 superscript
]

_SAFE_ASCII_BODY = "---\nname: my-workflow\ndescription: d\n---\n"


def test_unicode_request_identity_rejected_before_materialization_no_residue(
    client_with_runtime, monkeypatch, tmp_path,
):
    """seq43 Option A / C1b A8: the named café-workflow request identity is
    refused by the route-level logical-slug gate with 422 ``invalid_slug``
    BEFORE ``service.validate_package``, dry materialization, ``_artifact_key``
    construction or any durable write — zero residue anywhere.

    This REPLACES the historical diagnostic expectation (HTTP 500 /
    ``InvalidArtifactName`` from the unchanged read-only ArtifactStore name
    guard). That prior failure receipt is retained as historical evidence in
    ``output/TASK-8597/repair-evidence.md``; it is never asserted as a passing
    assertion here, and the case is neither deleted, skipped nor suppressed.
    """
    from runtime.orchestrator._paths import OrgPaths
    from runtime.daemon.routes import custom_skills as routes
    from runtime.skills.custom import service
    from runtime.skills.skill_md import is_valid_logical_slug, parse_skill_frontmatter

    client, org = client_with_runtime
    _add_agent(org)
    monkeypatch.setenv("HAPPYRANCH_CANONICAL_STORE_ROOT", str(tmp_path / "canonical"))
    skill_md = "---\nname: café-workflow\ndescription: unicode café route\n---\n"

    # The one shared literal ASCII full-string predicate refuses the identity.
    assert is_valid_logical_slug("café-workflow") is False
    # The document parses; the name is exactly the non-conforming identity.
    assert parse_skill_frontmatter(skill_md)["name"] == "café-workflow"

    # Instrument every downstream seam the refusal must never reach.
    validator_calls: list = []
    key_calls: list = []
    monkeypatch.setattr(
        service, "validate_package",
        lambda *a, **k: validator_calls.append((a, k)) or {
            "ok": True, "reason_codes": [], "errors": [],
        },
    )
    monkeypatch.setattr(
        routes, "_artifact_key",
        lambda slug, content: key_calls.append((slug, content)) or "unused",
    )
    write_calls = _no_write_artifact_seam(monkeypatch)

    before = _residue_snapshot(org, None)
    response = client.post(
        BASE, json={"slug": "café-workflow", "name": "café-workflow", "skill_md": skill_md}
    )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_slug"
    assert "ASCII" in detail["detail"]
    assert "HappyRanch admission" in detail["detail"]

    # No validate/dry/key/artifact write was reached, and no residue remains.
    assert validator_calls == [] and key_calls == [] and write_calls == []
    assert _residue_snapshot(org, None) == before
    assert not any("café" in artifact for artifact in _artifact_keys(org))
    assert not (
        OrgPaths(org.root).artifacts_dir / "custom-skills" / "café-workflow"
    ).exists()


@pytest.mark.parametrize("path", [
    f"{BASE}/agent-create",
    "/api/v1/orgs/alpha/skills/agent",
])
@pytest.mark.parametrize("bad_slug", _NONCONFORMING_LOGICAL_IDENTITIES)
def test_agent_endpoints_reject_invalid_logical_slug_before_validation(
    client_with_runtime, monkeypatch, path, bad_slug,
):
    """A5-A13 across BOTH agent create/append endpoints: a malformed logical
    identity is 422 ``invalid_slug`` before validation, with no
    validate/dry/key/write call and no residue."""
    from runtime.daemon.routes import custom_skills as routes
    from runtime.skills.custom import service

    client, org = client_with_runtime
    org.db.insert_task(TaskRecord(id="TASK-SLUG", brief="author a skill"))
    org.sessions.set_active("TASK-SLUG", "dev_agent", "sess-slug", org_slug="alpha")
    client.headers.pop("Authorization", None)

    validator_calls: list = []
    key_calls: list = []
    monkeypatch.setattr(
        service, "validate_package",
        lambda *a, **k: validator_calls.append((a, k)) or {
            "ok": True, "reason_codes": [], "errors": [],
        },
    )
    monkeypatch.setattr(
        routes, "_artifact_key", lambda *a, **k: key_calls.append(a) or "unused"
    )
    write_calls = _no_write_artifact_seam(monkeypatch)

    before = _residue_snapshot(org, None)
    response = client.post(
        path, params={"session_id": "sess-slug"},
        json={"slug": bad_slug, "name": "Display label", "skill_md": _SAFE_ASCII_BODY},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "invalid_slug"
    assert validator_calls == [] and key_calls == [] and write_calls == []
    assert _residue_snapshot(org, None) == before


@pytest.mark.parametrize("bad_slug", _NONCONFORMING_LOGICAL_IDENTITIES)
def test_human_create_rejects_invalid_logical_slug_before_validation(
    client_with_runtime, monkeypatch, bad_slug,
):
    """A5-A13 on the human create route: the same gate, same no-residue 422."""
    from runtime.daemon.routes import custom_skills as routes
    from runtime.skills.custom import service

    client, org = client_with_runtime
    validator_calls: list = []
    key_calls: list = []
    monkeypatch.setattr(
        service, "validate_package",
        lambda *a, **k: validator_calls.append((a, k)) or {
            "ok": True, "reason_codes": [], "errors": [],
        },
    )
    monkeypatch.setattr(
        routes, "_artifact_key", lambda *a, **k: key_calls.append(a) or "unused"
    )
    write_calls = _no_write_artifact_seam(monkeypatch)

    before = _residue_snapshot(org, None)
    response = client.post(
        BASE, json={"slug": bad_slug, "name": "Display label", "skill_md": _SAFE_ASCII_BODY}
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "invalid_slug"
    assert validator_calls == [] and key_calls == [] and write_calls == []
    assert _residue_snapshot(org, None) == before


def test_stored_non_ascii_slug_append_rejected_without_rewriting_history(
    client_with_runtime,
):
    """A17: a synthetic historical row whose stored slug is non-ASCII keeps all
    reads; a NEW append under it is 422 ``invalid_slug`` with zero residue, and
    the stored row/version bytes are never rewritten or revalidated."""
    from runtime.infrastructure.artifact_store import ArtifactStore
    from runtime.orchestrator._paths import OrgPaths
    from runtime.skills.custom import service as custom_service

    client, org = client_with_runtime
    conn = getattr(org.db, "_conn", org.db)
    skill_id = "custom:synthetic-unicode"
    content = "---\nname: café-workflow\ndescription: stored\n---\n"
    artifact_key = ArtifactStore(OrgPaths(org.root).artifacts_dir).put(
        "custom-skills/cafe-stored/stored/SKILL.md", content.encode(),
    ).name
    conn.execute(
        "INSERT INTO custom_skills "
        "(id,org_slug,slug,name,description,origin_kind,created_at,created_by) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (skill_id, "alpha", "café-workflow", "Stored Unicode", "stored",
         "human", custom_service.now(), "founder"),
    )
    conn.execute(
        """INSERT INTO custom_skill_versions
           (skill_id,content_hash,content_artifact_key,skill_md_cache,validation_state,
            validator_version,validation_findings,created_at,author_kind,author_identity)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (skill_id, hashlib.sha256(content.encode()).hexdigest(), artifact_key, content,
         "invalid", "THR-262/1.0.0", '["stored"]', custom_service.now(), "human", "founder"),
    )
    version_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("UPDATE custom_skills SET current_version_id=? WHERE id=?", (version_id, skill_id))
    conn.commit()

    # Historical reads are preserved unchanged.
    detail = client.get(f"{BASE}/{skill_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["slug"] == "café-workflow"
    assert client.get(f"{BASE}/{skill_id}/versions").status_code == 200

    stored_row_before = dict(conn.execute(
        "SELECT * FROM custom_skills WHERE id=?", (skill_id,)).fetchone())
    versions_before = [dict(r) for r in conn.execute(
        "SELECT * FROM custom_skill_versions WHERE skill_id=? ORDER BY id", (skill_id,))]
    before = _residue_snapshot(org, skill_id)

    append = client.post(
        f"{BASE}/{skill_id}/versions",
        json={"skill_md": "---\nname: café-workflow\ndescription: next\n---\n"},
    )
    assert append.status_code == 422, append.text
    assert append.json()["detail"]["code"] == "invalid_slug"

    assert _residue_snapshot(org, skill_id) == before
    assert dict(conn.execute(
        "SELECT * FROM custom_skills WHERE id=?", (skill_id,)).fetchone()) == stored_row_before
    assert [dict(r) for r in conn.execute(
        "SELECT * FROM custom_skill_versions WHERE skill_id=? ORDER BY id", (skill_id,))
    ] == versions_before


@pytest.mark.parametrize("doc_name,expected_message", [
    # A14 / document row 17b: admitted ASCII identity + Unicode document name.
    ("café-workflow", "ASCII lower-case"),
    # A15: admitted ASCII identity + ASCII name that differs from the slug.
    ("other-workflow", "must equal the logical slug"),
    # A5b: admitted ASCII identity + 65-char document name (over the bound).
    ("a" * 65, "ASCII lower-case"),
])
def test_admitted_ascii_identity_with_invalid_document_name_is_201_evidence(
    client_with_runtime, doc_name, expected_message,
):
    """A5b/A14/A15: an ASCII-conforming request identity whose frontmatter
    ``name`` is invalid or mismatching stays on the 201 immutable
    invalid-evidence path (dark first version) — never a request-identity 4xx
    and never a zero-residue claim."""
    client, org = client_with_runtime
    response = client.post(
        BASE, json={
            "slug": "my-workflow", "name": "Display label",
            "skill_md": f"---\nname: {doc_name}\ndescription: d\n---\n",
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["validation_state"] == "invalid"
    conn = getattr(org.db, "_conn", org.db)
    findings = json.loads(conn.execute(
        "SELECT validation_findings FROM custom_skill_versions WHERE id=?",
        (payload["version_id"],),
    ).fetchone()[0])
    assert len(findings) == 1
    assert expected_message in findings[0]
    # Initial invalid creation is the current (dark) pointer.
    assert payload["current_version_id"] == payload["version_id"]


def test_excluded_key_first_invalid_and_append_preserve_dual_root_identity(
    client_with_runtime, monkeypatch, tmp_path,
):
    """R5d/C3-C5: an allowlist-invalid first creation stays dark in BOTH
    discovery roots while its bytes/state are retained; an allowlist-invalid
    append to a valid skill retains the valid pointer/description/eligibility
    and both roots keep the valid target identity."""
    from runtime.orchestrator import workspace_adapters as wa

    client, org = client_with_runtime
    _add_agent(org)
    monkeypatch.setenv("HAPPYRANCH_CANONICAL_STORE_ROOT", str(tmp_path / "canonical"))
    workspace = org.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    allow = [{"scope_type": "org", "scope_target": None, "effect": "allow"}]
    conn = getattr(org.db, "_conn", org.db)

    dark_md = "---\nname: dark-excluded\ndescription: d\nhooks: null\n---\n"
    dark = client.post(
        BASE,
        json={"slug": "dark-excluded", "name": "Dark", "description": "fallback",
              "skill_md": dark_md},
    )
    assert dark.status_code == 201, dark.text
    assert dark.json()["validation_state"] == "invalid"
    dark_row = conn.execute(
        "SELECT skill_md_cache, validation_state, validation_findings "
        "FROM custom_skill_versions WHERE id=?",
        (dark.json()["version_id"],),
    ).fetchone()
    assert dark_row["skill_md_cache"] == dark_md
    assert dark_row["validation_state"] == "invalid"
    # Admission-invalid by presence: the retained finding names the excluded key.
    assert "'hooks' is not permitted" in dark_row["validation_findings"]

    valid_md = "---\nname: retain-valid\ndescription: valid target\n---\n"
    created = client.post(
        BASE, json={"slug": "retain-valid", "name": "Retain", "skill_md": valid_md}
    )
    assert created.status_code == 201, created.text
    assert created.json()["validation_state"] == "valid"
    valid_id, valid_version = created.json()["skill_id"], created.json()["version_id"]
    saved = client.put(
        f"{BASE}/{valid_id}/eligibility", json=allow,
        headers={"If-Match": str(valid_version)},
    )
    assert saved.status_code == 200, saved.text

    append_md = "---\nname: retain-valid\ndescription: bogus\nhooks: null\n---\n"
    appended = client.post(f"{BASE}/{valid_id}/versions", json={"skill_md": append_md})
    assert appended.status_code == 201, appended.text
    assert appended.json()["validation_state"] == "invalid"
    row = conn.execute(
        "SELECT current_version_id, description FROM custom_skills WHERE id=?", (valid_id,)
    ).fetchone()
    assert row["current_version_id"] == valid_version
    assert row["description"] == "valid target"
    appended_row = conn.execute(
        "SELECT skill_md_cache, validation_state FROM custom_skill_versions WHERE id=?",
        (appended.json()["version_id"],),
    ).fetchone()
    assert appended_row["skill_md_cache"] == append_md
    assert appended_row["validation_state"] == "invalid"

    specs = wa._materialize_unified_canonical(
        workspace, org.settings, slug="alpha", context="task", provider="codex",
        agent_name="dev_agent", team="engineering", task_id="TASK-DARK",
        session_id="sess-dark", org_root=org.root, db=org.db,
        skills_root=org.settings.project_root / "runtime" / "skills",
    )
    slugs = {spec["slug"] for spec in specs}
    assert "retain-valid" in slugs
    assert "dark-excluded" not in slugs
    for root in (".claude/skills", ".agents/skills"):
        assert not (workspace / root / "dark-excluded").exists(), root
        link = workspace / root / "retain-valid"
        assert link.exists(), root
        assert (link / "SKILL.md").read_text(encoding="utf-8") == valid_md


def test_route_rejects_present_merge_key_and_null_duplicate(client_with_runtime):
    """R2/R3 at the real route: a present YAML ``<<`` merge declaration is
    admission-invalid by presence (never silently normalized to valid), and a
    repeated ``null`` key is the sole duplicate finding."""
    client, org = client_with_runtime
    conn = getattr(org.db, "_conn", org.db)

    merge_md = "---\nname: merge-route\ndescription: d\n<<: {}\n---\n"
    merge = client.post(
        BASE, json={"slug": "merge-route", "name": "Merge Route", "skill_md": merge_md}
    )
    assert merge.status_code == 201, merge.text
    assert merge.json()["validation_state"] == "invalid"
    merge_row = conn.execute(
        "SELECT validation_findings FROM custom_skill_versions WHERE id=?",
        (merge.json()["version_id"],),
    ).fetchone()
    assert "<<" in merge_row["validation_findings"]

    null_md = "---\nnull: a\nnull: b\n---\n"
    nulled = client.post(
        BASE, json={"slug": "null-dup", "name": "Null Dup", "skill_md": null_md}
    )
    assert nulled.status_code == 201, nulled.text
    assert nulled.json()["validation_state"] == "invalid"
    null_row = conn.execute(
        "SELECT validation_findings FROM custom_skill_versions WHERE id=?",
        (nulled.json()["version_id"],),
    ).fetchone()
    assert "repeats the top-level key" in null_row["validation_findings"]


# ═══════════════════════════════════════════════════════════════════════════
# TASK-8617 — repair of the two TASK-8613 findings under the accepted Option A
# design. Finding 1 duplicate identity (original root order before merge
# flattening) and the literal-matrix/route completion; A1-A4 admitted ASCII
# boundaries; A5b/A14/A15 invalid-document 201 evidence on human and both agent
# mounts; A17 frozen non-ASCII history; A18 route ordering; R5c/A16/C4-C6
# frozen legacy replay. All cases reuse the existing helpers and fixtures; no
# second validator or parallel framework is introduced.
# ═══════════════════════════════════════════════════════════════════════════

_AGENT_CREATE_PATHS = [f"{BASE}/agent-create", "/api/v1/orgs/alpha/skills/agent"]


def _canonical_both_roots(org, monkeypatch, tmp_path, *, task_id="TASK-ROOTS"):
    """Materialize the unified canonical set into a disposable workspace and
    return ``(specs, workspace)`` so BOTH provider roots can be inspected."""
    from runtime.orchestrator import workspace_adapters as wa

    monkeypatch.setenv("HAPPYRANCH_CANONICAL_STORE_ROOT", str(tmp_path / "canonical"))
    workspace = org.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True, exist_ok=True)
    specs = wa._materialize_unified_canonical(
        workspace, org.settings, slug="alpha", context="task", provider="codex",
        agent_name="dev_agent", team="engineering", task_id=task_id,
        session_id="sess-roots", org_root=org.root, db=org.db,
        skills_root=org.settings.project_root / "runtime" / "skills",
    )
    return specs, workspace


def _grant_org_allow(client, skill_id, version_id):
    response = client.put(
        f"{BASE}/{skill_id}/eligibility",
        json=[{"scope_type": "org", "scope_target": None, "effect": "allow"}],
        headers={"If-Match": str(version_id)},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _activate_agent(org, *, task_id="TASK-T8617", agent="dev_agent", session="sess-t8617"):
    org.db.insert_task(TaskRecord(id=task_id, brief="author a custom skill"))
    org.sessions.set_active(task_id, agent, session, org_slug="alpha")
    return task_id, session


# ── Finding 1: duplicate identity at the real human-create route ─────────

def test_finding1_duplicate_merge_and_override_route_findings(client_with_runtime):
    """Finding 1 route proof: two original top-level ``<<: {}`` declarations
    persist exactly ONE ``frontmatter_duplicate_key`` naming ``<<`` and parse to
    no mapping; one ``<<: {name: inherited}`` beside one explicit ``name``
    persists excluded-merge admission naming ``<<`` (never a fabricated
    duplicate name). Both remain 201 immutable invalid evidence."""
    from runtime.skills.skill_md import parse_skill_frontmatter

    client, org = client_with_runtime
    conn = getattr(org.db, "_conn", org.db)

    duplicate_md = "---\nname: duplicate-merge\ndescription: d\n<<: {}\n<<: {}\n---\n"
    duplicate = client.post(
        BASE,
        json={"slug": "duplicate-merge", "name": "Duplicate Merge", "skill_md": duplicate_md},
    )
    assert duplicate.status_code == 201, duplicate.text
    assert duplicate.json()["validation_state"] == "invalid"
    dup_row = conn.execute(
        "SELECT skill_md_cache, validation_findings FROM custom_skill_versions WHERE id=?",
        (duplicate.json()["version_id"],),
    ).fetchone()
    assert dup_row["skill_md_cache"] == duplicate_md
    assert json.loads(dup_row["validation_findings"]) == [
        "SKILL.md frontmatter repeats the top-level key '<<'"
    ]
    # A structural duplicate produces no parsed mapping channel.
    assert parse_skill_frontmatter(duplicate_md) is None

    override_md = (
        "---\nname: merge-override\ndescription: d\n<<: {name: inherited}\n---\n"
    )
    override = client.post(
        BASE,
        json={"slug": "merge-override", "name": "Merge Override", "skill_md": override_md},
    )
    assert override.status_code == 201, override.text
    assert override.json()["validation_state"] == "invalid"
    override_findings = json.loads(conn.execute(
        "SELECT validation_findings FROM custom_skill_versions WHERE id=?",
        (override.json()["version_id"],),
    ).fetchone()["validation_findings"])
    assert len(override_findings) == 1
    assert "'<<' is not permitted" in override_findings[0]
    assert "duplicate" not in override_findings[0].lower()
    # The original root declares ``name`` once: the parsed channel is the
    # explicit document value, never a fabricated duplicate name.
    assert parse_skill_frontmatter(override_md) == {
        "name": "merge-override", "description": "d",
    }


# ── Finding 2: literal matrix through the real route + nonmutation ────────
#
# The accepted 46-row matrix is reused verbatim from the authorized
# validator-level file. Each row here is paired with an INDEPENDENTLY specified
# expected persisted finding list (literal reason_code + key pairs) plus local
# literal message templates, so the route assertion never calls the production
# validator to compute its own expected answer. Row index 17 (the uppercase
# request slug ``My-Workflow``) is a request-IDENTITY refusal: 422 invalid_slug
# with no residue, classified separately from every conforming-identity row
# whose invalid document is 201 immutable evidence.

#: Local, independently specified persisted-finding message templates.
_ROUTE_FINDING_MESSAGE: dict[str, str] = {
    ADMISSION_FIELD_NOT_ALLOWED: (
        "HappyRanch accepts only name, description, license, compatibility and "
        "metadata in SKILL.md frontmatter; '{key}' is not permitted. Request "
        "tool permission through the jobs / manage-agent workflow."
    ),
    FRONTMATTER_DUPLICATE_KEY: "SKILL.md frontmatter repeats the top-level key '{key}'",
    FRONTMATTER_MISSING_NAME: "SKILL.md frontmatter is missing the required 'name' field",
    FRONTMATTER_INVALID_NAME: (
        "SKILL.md frontmatter 'name' must be 1-64 ASCII lower-case letters, "
        "ASCII digits or single hyphens (a-z, 0-9, '-'), with no leading, "
        "trailing or consecutive hyphen. This is a HappyRanch admission rule; "
        "the Agent Skills standard permits Unicode names."
    ),
    FRONTMATTER_NAME_SLUG_MISMATCH: (
        "SKILL.md frontmatter 'name' must equal the logical slug '{slug}'"
    ),
    FRONTMATTER_MISSING_DESCRIPTION: (
        "SKILL.md frontmatter is missing the required 'description' field"
    ),
    FRONTMATTER_INVALID_DESCRIPTION: (
        "SKILL.md frontmatter 'description' must be a non-empty string of at "
        "most 1024 characters"
    ),
    FRONTMATTER_INVALID_LICENSE: "SKILL.md frontmatter 'license' must be a string",
    FRONTMATTER_INVALID_COMPATIBILITY: (
        "SKILL.md frontmatter 'compatibility' must be a string of 1-500 characters"
    ),
    FRONTMATTER_INVALID_METADATA: (
        "SKILL.md frontmatter 'metadata' must be a mapping of string keys to "
        "string values"
    ),
    SKILL_MD_NO_FRONTMATTER: (
        "SKILL.md must start with a YAML frontmatter '---' fence at column zero"
    ),
    SKILL_MD_UNCLOSED_FRONTMATTER: (
        "SKILL.md YAML frontmatter is missing its closing fence"
    ),
    SKILL_MD_MALFORMED_FRONTMATTER: "SKILL.md YAML frontmatter is malformed",
}

#: One entry per `_LITERAL_CONTRACT_ROWS` row, in the same order:
#: ("document", [(reason_code, key_or_None), ...]) or ("identity", None).
_ROUTE_ROW_EXPECTATIONS: list[tuple[str, object]] = [
    ("document", []),  # 0 baseline valid
    ("document", [(FRONTMATTER_MISSING_DESCRIPTION, None)]),  # 1
    ("document", [(FRONTMATTER_MISSING_NAME, None)]),  # 2
    ("document", [(SKILL_MD_NO_FRONTMATTER, None)]),  # 3
    ("document", [(SKILL_MD_UNCLOSED_FRONTMATTER, None)]),  # 4
    ("document", [(SKILL_MD_MALFORMED_FRONTMATTER, None)]),  # 5
    ("document", [(SKILL_MD_MALFORMED_FRONTMATTER, None)]),  # 6
    ("document", [(ADMISSION_FIELD_NOT_ALLOWED, "hooks")]),  # 7
    ("document", [(ADMISSION_FIELD_NOT_ALLOWED, "allowed-tools")]),  # 8
    ("document", [  # 9 four disallowed keys in document order
        (ADMISSION_FIELD_NOT_ALLOWED, "allowed-tools"),
        (ADMISSION_FIELD_NOT_ALLOWED, "hooks"),
        (ADMISSION_FIELD_NOT_ALLOWED, "vendor-x"),
        (ADMISSION_FIELD_NOT_ALLOWED, "future-field"),
    ]),
    ("document", [(ADMISSION_FIELD_NOT_ALLOWED, "vendor-x")]),  # 10
    ("document", [(FRONTMATTER_DUPLICATE_KEY, "name")]),  # 11
    ("document", [(FRONTMATTER_DUPLICATE_KEY, "hooks")]),  # 12
    ("document", []),  # 13 quoted digit-only identity, valid
    ("document", [(FRONTMATTER_INVALID_NAME, None)]),  # 14 unquoted int name
    ("document", [(FRONTMATTER_INVALID_NAME, None)]),  # 15 bool name
    ("document", [(FRONTMATTER_INVALID_NAME, None)]),  # 16 non-ASCII name
    ("identity", None),  # 17 uppercase request slug -> 422 invalid_slug
    ("document", [(FRONTMATTER_INVALID_NAME, None)]),  # 18 leading hyphen
    ("document", [(FRONTMATTER_INVALID_NAME, None)]),  # 19 trailing hyphen
    ("document", [(FRONTMATTER_INVALID_NAME, None)]),  # 20 double hyphen
    ("document", [(FRONTMATTER_NAME_SLUG_MISMATCH, None)]),  # 21 mismatch
    ("document", [(FRONTMATTER_INVALID_DESCRIPTION, None)]),  # 22 null
    ("document", [(FRONTMATTER_INVALID_DESCRIPTION, None)]),  # 23 int
    ("document", [(FRONTMATTER_INVALID_DESCRIPTION, None)]),  # 24 empty
    ("document", [(FRONTMATTER_INVALID_DESCRIPTION, None)]),  # 25 whitespace
    ("document", [(FRONTMATTER_INVALID_DESCRIPTION, None)]),  # 26 over length
    ("document", [(FRONTMATTER_INVALID_LICENSE, None)]),  # 27
    ("document", [(FRONTMATTER_INVALID_COMPATIBILITY, None)]),  # 28
    ("document", [(FRONTMATTER_INVALID_COMPATIBILITY, None)]),  # 29
    ("document", [(FRONTMATTER_INVALID_COMPATIBILITY, None)]),  # 30
    ("document", [(FRONTMATTER_INVALID_METADATA, None)]),  # 31
    ("document", [(FRONTMATTER_INVALID_METADATA, None)]),  # 32
    ("document", [(FRONTMATTER_INVALID_METADATA, None)]),  # 33
    ("document", [(FRONTMATTER_INVALID_METADATA, None)]),  # 34
    ("document", [(FRONTMATTER_INVALID_METADATA, None)]),  # 35
    ("document", [(FRONTMATTER_INVALID_METADATA, None)]),  # 36
    ("document", []),  # 37 valid string metadata
    ("document", [(ADMISSION_FIELD_NOT_ALLOWED, "allowed-tools")]),  # 38
    ("document", [(ADMISSION_FIELD_NOT_ALLOWED, "hooks")]),  # 39
    ("document", []),  # 40 prose mention
    ("document", []),  # 41 metadata prose mention
    ("document", [  # 42 admission precedes missing required
        (ADMISSION_FIELD_NOT_ALLOWED, "allowed-tools"),
        (FRONTMATTER_MISSING_DESCRIPTION, None),
    ]),
    ("document", [  # 43 admission precedes optional-field finding
        (ADMISSION_FIELD_NOT_ALLOWED, "allowed-tools"),
        (FRONTMATTER_INVALID_LICENSE, None),
    ]),
    ("document", [(FRONTMATTER_DUPLICATE_KEY, "<<")]),  # 44 duplicate merges
    ("document", [(ADMISSION_FIELD_NOT_ALLOWED, "<<")]),  # 45 merge override
]

_ROUTE_ROW_IDS = [f"row{i:02d}" for i in range(len(_LITERAL_CONTRACT_ROWS))]


def _route_expected_messages(pairs, expected_slug) -> list[str]:
    return [
        _ROUTE_FINDING_MESSAGE[code].format(key=key, slug=expected_slug)
        for code, key in pairs
    ]


@pytest.mark.parametrize("row_index", range(len(_LITERAL_CONTRACT_ROWS)), ids=_ROUTE_ROW_IDS)
def test_finding2_literal_matrix_route_outcomes_and_permission_nonmutation(
    client_with_runtime, row_index,
):
    """Finding 2 route proof: the FULL accepted literal matrix through the real
    human-create persistence route. Document/admission findings persist as 201
    immutable invalid evidence with the EXACT ordered finding list (asserted
    against locally specified messages, never against the production
    validator); a valid row is 201 valid; the uppercase request slug is a 422
    request identity refusal with no residue, classified separately from a
    conforming identity with an invalid document. Authoring leaves every
    generated permission/settings surface byte-identical (C3)."""
    assert len(_ROUTE_ROW_EXPECTATIONS) == len(_LITERAL_CONTRACT_ROWS)
    skill_md, expected_slug, expected_codes = _LITERAL_CONTRACT_ROWS[row_index]
    kind, expected_pairs = _ROUTE_ROW_EXPECTATIONS[row_index]
    # The two independent specifications (accepted literal reason codes and the
    # locally specified finding pairs) must agree on order and identity. The
    # identity-refusal row has no document finding list.
    if expected_pairs is not None:
        assert [code for code, _ in expected_pairs] == expected_codes

    client, org = client_with_runtime
    conn = getattr(org.db, "_conn", org.db)

    workspace = org.root / "workspaces" / "dev_agent"
    (workspace / ".claude").mkdir(parents=True, exist_ok=True)
    permission_surfaces = {
        workspace / ".claude" / "settings.json": b'{"permissions": {"allow": ["happyranch"]}}\n',
        workspace / "opencode.json": b'{"permission": {"bash": "ask"}}\n',
    }
    for path, payload in permission_surfaces.items():
        path.write_bytes(payload)
    before_permissions = {path: path.read_bytes() for path in permission_surfaces}
    before_counts = _custom_counts(org)
    before_artifacts = _artifact_bytes_state(org)

    response = client.post(
        BASE, json={"slug": expected_slug, "name": "Display label", "skill_md": skill_md}
    )

    if kind == "identity":
        assert response.status_code == 422, (row_index, response.text)
        assert response.json()["detail"]["code"] == "invalid_slug"
        assert _custom_counts(org) == before_counts
        assert _artifact_bytes_state(org) == before_artifacts
        assert {p: p.read_bytes() for p in permission_surfaces} == before_permissions
        return

    assert response.status_code == 201, (row_index, response.text)
    expected_state = "invalid" if expected_codes else "valid"
    assert response.json()["validation_state"] == expected_state
    persisted = json.loads(conn.execute(
        "SELECT validation_findings FROM custom_skill_versions WHERE id=?",
        (response.json()["version_id"],),
    ).fetchone()["validation_findings"])
    assert persisted == _route_expected_messages(expected_pairs, expected_slug)
    # Four identical admission codes must still prove allowed-tools, hooks,
    # vendor-x and future-field order: each persisted message names its key.
    if expected_codes == [ADMISSION_FIELD_NOT_ALLOWED] * 4:
        assert [message.split("'")[1] for message in persisted] == [
            "allowed-tools", "hooks", "vendor-x", "future-field",
        ]
    assert {p: p.read_bytes() for p in permission_surfaces} == before_permissions


@pytest.mark.parametrize("bad_slug", [
    "My-Workflow",       # uppercase
    "café-workflow",     # non-ASCII
    "a" * 65,            # over length
    "a--b",              # consecutive hyphen
    "-a",                # leading hyphen
    "a-",                # trailing hyphen
    "a_b",               # underscore
])
def test_finding2_nonconforming_request_identity_is_422_no_residue(
    client_with_runtime, bad_slug,
):
    """A non-conforming logical request identity is a 422 ``invalid_slug``
    BEFORE validation/dry-materialization/artifact-key/durable writes, with no
    durable or artifact residue and byte-identical permission surfaces."""
    client, org = client_with_runtime
    workspace = org.root / "workspaces" / "dev_agent"
    (workspace / ".claude").mkdir(parents=True, exist_ok=True)
    surfaces = {
        workspace / ".claude" / "settings.json": b'{"permissions": {"allow": ["happyranch"]}}\n',
        workspace / "opencode.json": b'{"permission": {"bash": "ask"}}\n',
    }
    for path, payload in surfaces.items():
        path.write_bytes(payload)
    before_counts = _custom_counts(org)
    before_artifacts = _artifact_bytes_state(org)

    refused = client.post(
        BASE, json={"slug": bad_slug, "name": "Display", "skill_md": _SAFE_ASCII_BODY}
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == "invalid_slug"
    assert _custom_counts(org) == before_counts
    assert _artifact_bytes_state(org) == before_artifacts
    assert {p: p.read_bytes() for p in surfaces} == {
        workspace / ".claude" / "settings.json": b'{"permissions": {"allow": ["happyranch"]}}\n',
        workspace / "opencode.json": b'{"permission": {"bash": "ask"}}\n',
    }


# ── Finding 3: A1-A4 admitted ASCII identities + Unicode display ──────────

def test_a1_a2_a4_admitted_ascii_boundaries_and_quoted_digit_identities(
    client_with_runtime, monkeypatch, tmp_path,
):
    """A1/A2/A4: the real human-create route admits length-1/64 and quoted
    digit-only/mixed ASCII identities, persisting valid versions under the
    UNCHANGED ASCII artifact-key formula (under the 200-char store bound). Every
    authorized version materializes in BOTH provider roots as a symlink whose
    resolved target is the canonical package identity (version + content hash),
    with the exact SKILL.md bytes."""
    from runtime.skills.canonical_store import CanonicalSkillStore

    client, org = client_with_runtime
    _add_agent(org)
    conn = getattr(org.db, "_conn", org.db)
    cases = [
        ("a", "a"),
        ("a-1", "a-1"),
        ("123", '"123"'),
        ("inventory-count", "inventory-count"),
        ("a" * 64, "a" * 64),
        ("a" * 62 + "-b", "a" * 62 + "-b"),
    ]
    created = []
    for slug, yaml_name in cases:
        skill_md = f"---\nname: {yaml_name}\ndescription: d\n---\n"
        response = client.post(
            BASE, json={"slug": slug, "name": f"Display {slug}", "skill_md": skill_md}
        )
        assert response.status_code == 201, (slug, response.text)
        assert response.json()["validation_state"] == "valid"
        version_id = response.json()["version_id"]
        digest = hashlib.sha256(skill_md.encode()).hexdigest()
        row = conn.execute(
            "SELECT content_artifact_key, content_hash FROM custom_skill_versions WHERE id=?",
            (version_id,),
        ).fetchone()
        assert row["content_artifact_key"] == f"custom-skills/{slug}/{digest}/SKILL.md"
        assert row["content_hash"] == digest
        assert len(row["content_artifact_key"]) <= 200
        _grant_org_allow(client, response.json()["skill_id"], version_id)
        created.append((slug, version_id, digest, skill_md))

    specs, workspace = _canonical_both_roots(org, monkeypatch, tmp_path, task_id="TASK-A1A4")
    by_slug = {spec["slug"]: spec for spec in specs}
    store = CanonicalSkillStore()
    for slug, version_id, digest, skill_md in created:
        spec = by_slug[slug]
        assert spec["version"] == str(version_id)
        assert spec["content_hash"] == digest
        target = store.canonical_path(slug, str(version_id), digest)
        for root in (".claude/skills", ".agents/skills"):
            link = workspace / root / slug
            assert link.is_symlink(), (root, slug)
            assert link.resolve() == target.resolve(), (root, slug)
            assert (link / "SKILL.md").read_bytes() == skill_md.encode("utf-8")


def test_a3_unicode_display_and_description_retained_both_roots(
    client_with_runtime, monkeypatch, tmp_path,
):
    """A3: an ASCII logical identity admits a Unicode catalog display name and
    Unicode frontmatter description. Both are retained EXACTLY, the artifact key
    keeps the ASCII logical slug, and the authorized skill materializes in both
    provider roots with the exact target identity."""
    client, org = client_with_runtime
    _add_agent(org)
    display = "库存盘点"
    unicode_description = "盘点库存流程 café"
    skill_md = f"---\nname: my-workflow\ndescription: {unicode_description}\n---\n"
    response = client.post(
        BASE, json={"slug": "my-workflow", "name": display, "skill_md": skill_md}
    )
    assert response.status_code == 201, response.text
    assert response.json()["validation_state"] == "valid"
    skill_id, version = response.json()["skill_id"], response.json()["version_id"]

    detail = client.get(f"{BASE}/{skill_id}").json()
    assert detail["name"] == display
    assert detail["description"] == unicode_description
    stored = getattr(org.db, "_conn", org.db).execute(
        "SELECT content_artifact_key, skill_md_cache FROM custom_skill_versions WHERE id=?",
        (version,),
    ).fetchone()
    assert stored["content_artifact_key"] == (
        f"custom-skills/my-workflow/{hashlib.sha256(skill_md.encode()).hexdigest()}/SKILL.md"
    )
    assert stored["skill_md_cache"] == skill_md

    _grant_org_allow(client, skill_id, version)
    specs, workspace = _canonical_both_roots(org, monkeypatch, tmp_path, task_id="TASK-A3")
    from runtime.skills.canonical_store import CanonicalSkillStore

    spec = next(s for s in specs if s["slug"] == "my-workflow")
    assert spec["version"] == str(version)
    assert spec["content_hash"] == hashlib.sha256(skill_md.encode()).hexdigest()
    target = CanonicalSkillStore().canonical_path(
        "my-workflow", str(version), spec["content_hash"]
    )
    for root in (".claude/skills", ".agents/skills"):
        link = workspace / root / "my-workflow"
        assert link.is_symlink(), root
        assert link.resolve() == target.resolve(), root
        assert (link / "SKILL.md").read_bytes() == skill_md.encode("utf-8")


# ── Finding 4: A5b/A14/A15 invalid-document 201 evidence ─────────────────

_INVALID_DOCUMENT_NAME_CASES = [
    # A14: admitted ASCII identity + Unicode document name.
    ("café-workflow", FRONTMATTER_INVALID_NAME),
    # A15: admitted ASCII identity + ASCII name that differs from the slug.
    ("other-workflow", FRONTMATTER_NAME_SLUG_MISMATCH),
    # A5b: admitted ASCII identity + 65-char document name (over the bound).
    ("a" * 65, FRONTMATTER_INVALID_NAME),
]


@pytest.mark.parametrize("doc_name,expected_code", _INVALID_DOCUMENT_NAME_CASES)
@pytest.mark.parametrize("surface", ["human-create", "agent-create-a", "agent-create-b"])
def test_a5b_a14_a15_invalid_document_name_201_evidence_create_seams(
    client_with_runtime, doc_name, expected_code, surface,
):
    """A5b/A14/A15 on every create seam (human + both agent mounts): a valid
    ASCII identity with an invalid/mismatching/over-length document name is 201
    immutable invalid evidence, never a request-identity 4xx and never a
    zero-residue claim. The ACTUAL stored artifact bytes at the returned key are
    read and hash-compared (the cached DB column is additional evidence, not a
    substitute); the exact ordered finding, validator marker and lineage are
    asserted and the first-invalid version is the dark current pointer."""
    from runtime.infrastructure.artifact_store import ArtifactStore
    from runtime.orchestrator._paths import OrgPaths

    client, org = client_with_runtime
    conn = getattr(org.db, "_conn", org.db)
    skill_md = f"---\nname: {doc_name}\ndescription: d\n---\n"

    if surface == "human-create":
        response = client.post(
            BASE, json={"slug": "my-workflow", "name": "Display label", "skill_md": skill_md}
        )
    else:
        path = _AGENT_CREATE_PATHS[0 if surface == "agent-create-a" else 1]
        _activate_agent(org, task_id="TASK-INV", session="sess-inv")
        client.headers.pop("Authorization", None)
        response = client.post(
            path, params={"session_id": "sess-inv"},
            json={"slug": "my-workflow", "name": "Display label", "skill_md": skill_md},
        )
    assert response.status_code == 201, response.text
    payload = response.json()
    if surface == "human-create":
        skill_id, version_id = payload["skill_id"], payload["version_id"]
        assert payload["validation_state"] == "invalid"
    else:
        skill_id, version_id = payload["skill"]["id"], payload["version"]["id"]
        assert payload["version"]["validation_state"] == "invalid"

    expected_message = _ROUTE_FINDING_MESSAGE[expected_code].format(
        key=None, slug="my-workflow",
    )
    digest = hashlib.sha256(skill_md.encode()).hexdigest()
    row = conn.execute(
        "SELECT skill_md_cache, content_hash, content_artifact_key, validation_findings, "
        "validation_state, validator_version FROM custom_skill_versions WHERE id=?",
        (version_id,),
    ).fetchone()
    assert row["skill_md_cache"] == skill_md
    assert row["content_hash"] == digest
    assert row["content_artifact_key"] == f"custom-skills/my-workflow/{digest}/SKILL.md"
    assert json.loads(row["validation_findings"]) == [expected_message]
    assert row["validation_state"] == "invalid"
    assert row["validator_version"] == "THR-262/1.0.0"
    current = conn.execute(
        "SELECT current_version_id FROM custom_skills WHERE id=?", (skill_id,)
    ).fetchone()["current_version_id"]
    assert current == version_id
    # Lineage: a first version has no parent.
    assert conn.execute(
        "SELECT parent_version_id FROM custom_skill_versions WHERE id=?", (version_id,)
    ).fetchone()["parent_version_id"] is None

    # The ACTUAL immutable artifact bytes at the persisted key.
    store = ArtifactStore(OrgPaths(org.root).artifacts_dir)
    artifact_path = store.path_for(row["content_artifact_key"])
    assert artifact_path.is_file(), row["content_artifact_key"]
    artifact_bytes = artifact_path.read_bytes()
    assert artifact_bytes == skill_md.encode("utf-8")
    assert hashlib.sha256(artifact_bytes).hexdigest() == digest


@pytest.mark.parametrize("doc_name,expected_code", _INVALID_DOCUMENT_NAME_CASES)
@pytest.mark.parametrize("surface", ["human-append", "agent-append-a", "agent-append-b"])
def test_a5b_a14_a15_invalid_document_name_201_evidence_append_seams(
    client_with_runtime, doc_name, expected_code, surface,
):
    """A5b/A14/A15 on every append seam (human + both agent mounts): an invalid
    document name appended to an eligible VALID version retains the COMPLETE
    prior pointer/description/eligibility rows and the valid predecessor's
    actual artifact bytes, and appends immutable invalid evidence whose actual
    artifact bytes/hash/key and ordered finding are read and compared."""
    from runtime.infrastructure.artifact_store import ArtifactStore
    from runtime.orchestrator._paths import OrgPaths

    client, org = client_with_runtime
    conn = getattr(org.db, "_conn", org.db)
    _add_agent(org)
    token = client.headers.get("Authorization")
    valid_md = "---\nname: retain-valid\ndescription: valid target\n---\n"
    invalid_md = f"---\nname: {doc_name}\ndescription: d\n---\n"

    if surface == "human-append":
        created = client.post(
            BASE, json={"slug": "retain-valid", "name": "Retain", "skill_md": valid_md}
        )
    else:
        path = _AGENT_CREATE_PATHS[0 if surface == "agent-append-a" else 1]
        _activate_agent(org, task_id="TASK-APP", session="sess-app")
        client.headers.pop("Authorization", None)
        created = client.post(
            path, params={"session_id": "sess-app"},
            json={"slug": "retain-valid", "name": "Retain", "skill_md": valid_md},
        )
    assert created.status_code == 201, created.text
    if surface == "human-append":
        skill_id, valid_version = created.json()["skill_id"], created.json()["version_id"]
    else:
        skill_id = created.json()["skill"]["id"]
        valid_version = created.json()["version"]["id"]

    client.headers["Authorization"] = token
    _grant_org_allow(client, skill_id, valid_version)

    # Snapshot the COMPLETE durable state and the predecessor's actual artifact
    # bytes immediately before the invalid successor request.
    store = ArtifactStore(OrgPaths(org.root).artifacts_dir)
    valid_digest = hashlib.sha256(valid_md.encode()).hexdigest()
    valid_key = f"custom-skills/retain-valid/{valid_digest}/SKILL.md"
    before_pointer = dict(conn.execute(
        "SELECT current_version_id, description FROM custom_skills WHERE id=?", (skill_id,)
    ).fetchone())
    before_valid_row = dict(conn.execute(
        "SELECT * FROM custom_skill_versions WHERE id=?", (valid_version,)
    ).fetchone())
    before_eligibility = [dict(r) for r in conn.execute(
        "SELECT * FROM custom_skill_eligibility_rules WHERE skill_id=? ORDER BY id", (skill_id,)
    )]
    assert before_pointer["current_version_id"] == valid_version
    assert store.path_for(valid_key).read_bytes() == valid_md.encode("utf-8")

    if surface == "human-append":
        appended = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": invalid_md})
    else:
        path = _AGENT_CREATE_PATHS[0 if surface == "agent-append-a" else 1]
        client.headers.pop("Authorization", None)
        appended = client.post(
            path, params={"session_id": "sess-app"},
            json={"slug": "retain-valid", "name": "Retain", "skill_md": invalid_md},
        )
        client.headers["Authorization"] = token
    assert appended.status_code == 201, appended.text
    if surface == "human-append":
        version_id = appended.json()["version_id"]
        assert appended.json()["validation_state"] == "invalid"
    else:
        version_id = appended.json()["version"]["id"]
        assert appended.json()["version"]["validation_state"] == "invalid"

    # Prior VALID pointer/description and the COMPLETE eligibility rows retained
    # (a single active count is insufficient).
    assert dict(conn.execute(
        "SELECT current_version_id, description FROM custom_skills WHERE id=?", (skill_id,)
    ).fetchone()) == before_pointer
    after_eligibility = [dict(r) for r in conn.execute(
        "SELECT * FROM custom_skill_eligibility_rules WHERE skill_id=? ORDER BY id", (skill_id,)
    )]
    assert after_eligibility == before_eligibility
    assert sum(1 for r in after_eligibility if r["superseded_at"] is None) == 1
    # The valid predecessor row AND its actual artifact bytes are retained.
    assert dict(conn.execute(
        "SELECT * FROM custom_skill_versions WHERE id=?", (valid_version,)
    ).fetchone()) == before_valid_row
    assert store.path_for(valid_key).read_bytes() == valid_md.encode("utf-8")

    digest = hashlib.sha256(invalid_md.encode()).hexdigest()
    invalid_row = conn.execute(
        "SELECT skill_md_cache, content_hash, content_artifact_key, validation_findings, "
        "validation_state, validator_version, parent_version_id "
        "FROM custom_skill_versions WHERE id=?", (version_id,)
    ).fetchone()
    assert invalid_row["skill_md_cache"] == invalid_md
    assert invalid_row["content_hash"] == digest
    assert invalid_row["content_artifact_key"] == (
        f"custom-skills/retain-valid/{digest}/SKILL.md"
    )
    assert invalid_row["validation_state"] == "invalid"
    assert invalid_row["validator_version"] == "THR-262/1.0.0"
    assert invalid_row["parent_version_id"] == valid_version
    expected_message = _ROUTE_FINDING_MESSAGE[expected_code].format(
        key=None, slug="retain-valid",
    )
    assert json.loads(invalid_row["validation_findings"]) == [expected_message]

    # The ACTUAL immutable artifact bytes at the invalid successor's key.
    invalid_path = store.path_for(invalid_row["content_artifact_key"])
    assert invalid_path.is_file(), invalid_row["content_artifact_key"]
    invalid_bytes = invalid_path.read_bytes()
    assert invalid_bytes == invalid_md.encode("utf-8")
    assert hashlib.sha256(invalid_bytes).hexdigest() == digest


@pytest.mark.parametrize("doc_name,_expected_code", _INVALID_DOCUMENT_NAME_CASES)
@pytest.mark.parametrize("surface", ["human-create", "agent-create-a", "agent-create-b"])
def test_a5b_a14_a15_first_invalid_dark_both_roots_all_seams(
    client_with_runtime, monkeypatch, tmp_path, doc_name, _expected_code, surface,
):
    """A5b/A14/A15: an invalid-document-name FIRST creation stays dark and
    leaves NO link — not even a broken symlink — in EITHER provider root, on the
    human create and both agent create mounts."""
    client, org = client_with_runtime
    _add_agent(org)
    slug = "dark-doc-name"
    dark_md = f"---\nname: {doc_name}\ndescription: d\n---\n"

    if surface == "human-create":
        dark = client.post(BASE, json={"slug": slug, "name": "Dark", "skill_md": dark_md})
    else:
        path = _AGENT_CREATE_PATHS[0 if surface == "agent-create-a" else 1]
        _activate_agent(org, task_id="TASK-DARK", session="sess-dark")
        client.headers.pop("Authorization", None)
        dark = client.post(
            path, params={"session_id": "sess-dark"},
            json={"slug": slug, "name": "Dark", "skill_md": dark_md},
        )
    assert dark.status_code == 201, dark.text
    if surface == "human-create":
        assert dark.json()["validation_state"] == "invalid"
    else:
        assert dark.json()["version"]["validation_state"] == "invalid"

    specs, workspace = _canonical_both_roots(org, monkeypatch, tmp_path, task_id="TASK-DARK")
    assert slug not in {spec["slug"] for spec in specs}
    for root in (".claude/skills", ".agents/skills"):
        link = workspace / root / slug
        # Link-aware absence: a broken symlink is still a link and must fail.
        assert not link.is_symlink(), (root, "broken link present")
        assert not link.exists(), root


@pytest.mark.parametrize("doc_name,_expected_code", _INVALID_DOCUMENT_NAME_CASES)
def test_a5b_a14_a15_invalid_successor_retains_both_roots_and_eligibility(
    client_with_runtime, monkeypatch, tmp_path, doc_name, _expected_code,
):
    """A5b/A14/A15: after granting eligibility and materializing the VALID
    predecessor in BOTH roots, an invalid-document-name successor retains the
    prior pointer, the COMPLETE eligibility rows, and BOTH roots' resolved
    target identity (same canonical package version/hash/bytes)."""
    from runtime.skills.canonical_store import CanonicalSkillStore

    client, org = client_with_runtime
    _add_agent(org)
    conn = getattr(org.db, "_conn", org.db)
    slug = "retain-valid"
    valid_md = "---\nname: retain-valid\ndescription: valid target\n---\n"
    created = client.post(
        BASE, json={"slug": slug, "name": "Retain", "skill_md": valid_md}
    )
    assert created.status_code == 201, created.text
    skill_id, valid_version = created.json()["skill_id"], created.json()["version_id"]
    _grant_org_allow(client, skill_id, valid_version)
    before_eligibility = [dict(r) for r in conn.execute(
        "SELECT * FROM custom_skill_eligibility_rules WHERE skill_id=? ORDER BY id", (skill_id,)
    )]

    specs, workspace = _canonical_both_roots(org, monkeypatch, tmp_path, task_id="TASK-A5B")
    spec = next(s for s in specs if s["slug"] == slug)
    valid_digest = hashlib.sha256(valid_md.encode()).hexdigest()
    assert spec["version"] == str(valid_version)
    assert spec["content_hash"] == valid_digest
    target = CanonicalSkillStore().canonical_path(slug, str(valid_version), valid_digest)
    before_targets = {}
    for root in (".claude/skills", ".agents/skills"):
        link = workspace / root / slug
        assert link.is_symlink(), root
        assert link.resolve() == target.resolve(), root
        assert (link / "SKILL.md").read_bytes() == valid_md.encode("utf-8")
        before_targets[root] = str(link.resolve())

    invalid_md = f"---\nname: {doc_name}\ndescription: d\n---\n"
    appended = client.post(f"{BASE}/{skill_id}/versions", json={"skill_md": invalid_md})
    assert appended.status_code == 201 and appended.json()["validation_state"] == "invalid"
    assert appended.json()["current_version_id"] == valid_version

    # Prior pointer and complete eligibility rows retained after the successor.
    assert dict(conn.execute(
        "SELECT current_version_id, description FROM custom_skills WHERE id=?", (skill_id,)
    ).fetchone())["current_version_id"] == valid_version
    assert [dict(r) for r in conn.execute(
        "SELECT * FROM custom_skill_eligibility_rules WHERE skill_id=? ORDER BY id", (skill_id,)
    )] == before_eligibility

    specs_after, workspace_after = _canonical_both_roots(
        org, monkeypatch, tmp_path, task_id="TASK-A5B2"
    )
    spec_after = next(s for s in specs_after if s["slug"] == slug)
    assert spec_after["version"] == str(valid_version)
    assert spec_after["content_hash"] == valid_digest
    for root in (".claude/skills", ".agents/skills"):
        link = workspace_after / root / slug
        assert link.is_symlink(), root
        assert str(link.resolve()) == before_targets[root], root
        assert link.resolve() == target.resolve(), root
        assert (link / "SKILL.md").read_bytes() == valid_md.encode("utf-8")


# ── Finding 5: A17 frozen non-ASCII historical row ───────────────────────

def test_a17_frozen_non_ascii_history_reads_and_append_refusal(
    client_with_runtime, monkeypatch, tmp_path,
):
    """A17: a synthetic frozen non-ASCII stored row keeps READ-ONLY history
    (detail/versions/diff/resolve/materialization/recovery) with frozen
    bytes/hash/key/marker/state/findings/provenance and no revalidation. A NEW
    append under it is 422 ``invalid_slug`` with a complete no-residue snapshot
    and never reaches the validator/dry/key/artifact-write seams."""
    from runtime.daemon.routes import custom_skills as routes
    from runtime.daemon.routes import skills as skills_routes
    from runtime.infrastructure.artifact_store import ArtifactStore
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.workspace_adapters import _build_custom_skill_canonical_specs
    from runtime.skills.canonical_store import CanonicalSkillStore
    from runtime.skills.custom import service as custom_service

    client, org = client_with_runtime
    _add_agent(org)
    conn = getattr(org.db, "_conn", org.db)
    skill_id = "custom:synthetic-unicode"
    slug = "café-workflow"
    first_md = "---\nname: café-workflow\ndescription: stored\n---\n"
    second_md = "# Frozen legacy heading-first\n\nbody\n"
    store = ArtifactStore(OrgPaths(org.root).artifacts_dir)
    key_one = store.put(
        "custom-skills/cafe-stored/stored/SKILL.md", first_md.encode()
    ).name
    key_two = store.put(
        "custom-skills/cafe-stored/legacy/SKILL.md", second_md.encode()
    ).name
    conn.execute(
        "INSERT INTO custom_skills "
        "(id,org_slug,slug,name,description,origin_kind,created_at,created_by) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (skill_id, "alpha", slug, "Stored Unicode", "stored", "human",
         custom_service.now(), "founder"),
    )
    conn.execute(
        """INSERT INTO custom_skill_versions
           (skill_id,parent_version_id,content_hash,content_artifact_key,skill_md_cache,
            validation_state,validator_version,validation_findings,created_at,
            author_kind,author_identity,source_task_id,source_session_id,task_brief_digest)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (skill_id, None, hashlib.sha256(first_md.encode()).hexdigest(), key_one, first_md,
         "invalid", "THR-055/1.0.0",
         '["SKILL.md frontmatter repeats the top-level key \'<<\'"]',
         custom_service.now(), "human", "founder", "TASK-HIST", "sess-hist", "hist-digest"),
    )
    version_one = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO custom_skill_versions
           (skill_id,parent_version_id,content_hash,content_artifact_key,skill_md_cache,
            validation_state,validator_version,validation_findings,created_at,
            author_kind,author_identity)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (skill_id, version_one, hashlib.sha256(second_md.encode()).hexdigest(), key_two,
         second_md, "valid", "THR-055/1.0.0", "[]", custom_service.now(), "human", "founder"),
    )
    version_two = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "UPDATE custom_skills SET current_version_id=? WHERE id=?", (version_one, skill_id)
    )
    conn.commit()

    frozen_skill = dict(conn.execute(
        "SELECT * FROM custom_skills WHERE id=?", (skill_id,)).fetchone())
    frozen_versions = [dict(r) for r in conn.execute(
        "SELECT * FROM custom_skill_versions WHERE skill_id=? ORDER BY id", (skill_id,))]

    # Snapshot the ACTUAL bytes/hash/key of BOTH seeded artifacts BEFORE any
    # historical read, recovery or append, and install the fail-if-called
    # package-validation spy BEFORE those reads (a read-only historical read and
    # the recovery refusal must never revalidate).
    before_artifacts = _artifact_bytes_state(org)
    seeded_artifacts = {}
    for seeded_key, seeded_md in ((key_one, first_md), (key_two, second_md)):
        seeded_bytes = store.path_for(seeded_key).read_bytes()
        assert seeded_bytes == seeded_md.encode("utf-8")
        seeded_artifacts[seeded_key] = (
            seeded_bytes, hashlib.sha256(seeded_bytes).hexdigest(),
        )

    validator_calls: list = []

    def _fail_if_validated(*args, **kwargs):
        validator_calls.append((args, kwargs))
        raise AssertionError(
            "package validator must not run during frozen historical reads/recovery"
        )

    monkeypatch.setattr(skills_routes, "_validate_skill_package", _fail_if_validated)
    dry_calls: list = []
    key_calls: list = []
    monkeypatch.setattr(
        skills_routes, "_dry_materialize", lambda *a, **k: dry_calls.append((a, k))
    )
    monkeypatch.setattr(
        routes, "_artifact_key", lambda *a, **k: key_calls.append(a) or "unused"
    )
    write_calls = _no_write_artifact_seam(monkeypatch)

    # Read-only history is preserved unchanged.
    detail = client.get(f"{BASE}/{skill_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["slug"] == slug
    assert detail.json()["validation_state"] == "invalid"
    assert client.get(f"{BASE}/{skill_id}/versions").status_code == 200
    diff = client.get(f"{BASE}/{skill_id}/versions/{version_one}/diff/{version_two}")
    assert diff.status_code == 200, diff.text
    assert diff.json()["a"]["content_hash"] == frozen_versions[0]["content_hash"]
    explain = client.get(f"{BASE}/{skill_id}/eligibility/explain", params={"agent": "dev_agent"})
    assert explain.status_code == 200, explain.text
    assert explain.json()["visible"] is False
    # Materialization is not promised for an INVALID stored row: it is excluded.
    monkeypatch.setenv("HAPPYRANCH_CANONICAL_STORE_ROOT", str(tmp_path / "canonical"))
    specs = _build_custom_skill_canonical_specs(
        store=CanonicalSkillStore(), org_root=org.root, db=org.db, slug="alpha",
        agent_name="dev_agent", team="engineering", task_id="TASK-A17",
        session_id="sess-a17", session_context="task",
    )
    assert slug not in {spec["slug"] for spec in specs}
    # Recovery refuses the ineligible (invalid) current with a bounded audit.
    recover = client.post(
        "/api/v1/orgs/alpha/skills/recover",
        json={"slug": slug, "version": str(version_one),
              "content_hash": frozen_versions[0]["content_hash"]},
    )
    assert recover.status_code == 409
    assert conn.execute(
        "SELECT reason_codes FROM skill_validation_events WHERE skill_id=? "
        "ORDER BY id DESC LIMIT 1", (skill_id,)
    ).fetchone()["reason_codes"] == '["ineligible_current_version"]'

    # The read/recovery sequence performed no validation, no dry run, no key
    # construction and no artifact write, and every actual frozen artifact byte
    # is unchanged.
    assert validator_calls == [] and dry_calls == [] and key_calls == [] and write_calls == []
    assert _artifact_bytes_state(org) == before_artifacts
    for seeded_key, (seeded_bytes, seeded_digest) in seeded_artifacts.items():
        assert store.path_for(seeded_key).read_bytes() == seeded_bytes
        assert hashlib.sha256(
            store.path_for(seeded_key).read_bytes()
        ).hexdigest() == seeded_digest

    # Frozen bytes/hash/key/marker/state/findings/provenance never rewritten.
    assert dict(conn.execute(
        "SELECT * FROM custom_skills WHERE id=?", (skill_id,)).fetchone()) == frozen_skill
    assert [dict(r) for r in conn.execute(
        "SELECT * FROM custom_skill_versions WHERE skill_id=? ORDER BY id", (skill_id,))
    ] == frozen_versions

    # A NEW append under the stored non-ASCII slug: 422, no residue, and no
    # downstream validator/dry/key/artifact-write call. The same spies installed
    # before the historical reads remain armed.
    before = _residue_snapshot(org, skill_id)
    append = client.post(
        f"{BASE}/{skill_id}/versions",
        json={"skill_md": "---\nname: café-workflow\ndescription: next\n---\n"},
    )
    assert append.status_code == 422, append.text
    assert append.json()["detail"]["code"] == "invalid_slug"
    assert validator_calls == [] and dry_calls == [] and key_calls == [] and write_calls == []
    assert _residue_snapshot(org, skill_id) == before
    assert _artifact_bytes_state(org) == before_artifacts
    for seeded_key, (seeded_bytes, seeded_digest) in seeded_artifacts.items():
        assert store.path_for(seeded_key).read_bytes() == seeded_bytes
        assert hashlib.sha256(
            store.path_for(seeded_key).read_bytes()
        ).hexdigest() == seeded_digest
    assert dict(conn.execute(
        "SELECT * FROM custom_skills WHERE id=?", (skill_id,)).fetchone()) == frozen_skill
    assert [dict(r) for r in conn.execute(
        "SELECT * FROM custom_skill_versions WHERE skill_id=? ORDER BY id", (skill_id,))
    ] == frozen_versions


# ── Finding 6: A18 route ordering / 409 reachability ─────────────────────

def test_a18_admitted_ascii_existing_human_and_protected_slug_409s(
    client_with_runtime,
):
    """A18: an admitted ASCII identity still reaches the existing-human 409
    ``slug_exists`` and the protected-slug 409, with the complete per-skill and
    full-durable state unchanged."""
    client, org = client_with_runtime
    first = client.post(
        BASE, json={"slug": "existing-human", "name": "First", "skill_md": _fm_body("existing-human")}
    )
    assert first.status_code == 201, first.text
    skill_id = first.json()["skill_id"]
    before_skill = _residue_snapshot(org, skill_id)
    before = _full_custom_state(org)

    again = client.post(
        BASE,
        json={"slug": "existing-human", "name": "Again",
              "skill_md": _fm_body("existing-human", "other")},
    )
    assert again.status_code == 409, again.text
    assert again.json()["detail"]["code"] == "slug_exists"
    assert _residue_snapshot(org, skill_id) == before_skill
    assert _full_custom_state(org) == before

    protected = client.post(
        BASE, json={"slug": "start-task", "name": "Start", "skill_md": _fm_body("start-task")}
    )
    assert protected.status_code == 409, protected.text
    assert protected.json()["detail"]["code"] == "protected_slug"
    assert _full_custom_state(org) == before


@pytest.mark.parametrize("case,expected_code,expected_status", [
    ("missing-metadata", "invalid_request", 422),
    ("unknown-session", "unknown_session", 403),
    ("cross-org", "cross_org_session", 403),
    ("recovery", "recovery_purpose_forbidden", 403),
])
def test_a18_invalid_identity_keeps_earlier_refusals(
    client_with_runtime, case, expected_code, expected_status,
):
    """A18: a non-conforming logical identity combined with an earlier
    auth/session/lease/metadata refusal keeps that earlier result — the identity
    gate is not moved ahead of the existing checks — with no residue."""
    client, org = client_with_runtime
    bad_slug = "café-workflow"
    before = _full_custom_state(org)
    client.headers.pop("Authorization", None)

    if case == "missing-metadata":
        _activate_agent(org, task_id="TASK-A18", session="sess-a18")
        response = client.post(
            _AGENT_CREATE_PATHS[0], params={"session_id": "sess-a18"},
            json={"slug": bad_slug, "name": "Display"},
        )
    elif case == "unknown-session":
        response = client.post(
            _AGENT_CREATE_PATHS[0], params={"session_id": "sess-missing"},
            json={"slug": bad_slug, "name": "Display", "skill_md": _SAFE_ASCII_BODY},
        )
    elif case == "cross-org":
        org.db.insert_task(TaskRecord(id="TASK-A18X", brief="author"))
        org.sessions.set_active("TASK-A18X", "dev_agent", "sess-cross", org_slug="beta")
        response = client.post(
            _AGENT_CREATE_PATHS[0], params={"session_id": "sess-cross"},
            json={"slug": bad_slug, "name": "Display", "skill_md": _SAFE_ASCII_BODY},
        )
    else:
        org.db.insert_task(TaskRecord(id="TASK-A18R", brief="recover"))
        org.sessions.register_recovery_session(
            "TASK-A18R", "dev_agent", "sess-recovery", org_slug="alpha"
        )
        response = client.post(
            _AGENT_CREATE_PATHS[0], params={"session_id": "sess-recovery"},
            json={"slug": bad_slug, "name": "Display", "skill_md": _SAFE_ASCII_BODY},
        )

    assert response.status_code == expected_status, response.text
    assert response.json()["detail"]["code"] == expected_code
    assert response.json()["detail"]["code"] != "invalid_slug"
    assert _full_custom_state(org) == before


@pytest.mark.parametrize("mount", [0, 1])
@pytest.mark.parametrize("case,expected_code,expected_status", [
    ("bearer-rejected", "bearer_not_accepted", 401),
    ("body-identity-rejected", "body_identity_rejected", 403),
    ("session-not-current", "session_not_current", 403),
])
def test_a18_invalid_identity_competing_earlier_refusals_both_mounts(
    client_with_runtime, monkeypatch, mount, case, expected_code, expected_status,
):
    """A18: a non-conforming identity combined with an EARLIER bearer, forbidden
    body-identity or stale/non-current-session refusal keeps that earlier result
    on BOTH agent mounts — the identity gate stays after the existing checks —
    with the complete durable/artifact state unchanged."""
    client, org = client_with_runtime
    bad_slug = "café-workflow"
    path = _AGENT_CREATE_PATHS[mount]

    if case == "bearer-rejected":
        # The bearer check is the very first route check: keep the default
        # Authorization header present and supply an otherwise valid body.
        _activate_agent(org, task_id="TASK-A18B", session="sess-a18b")
        body = {"slug": bad_slug, "name": "Display", "skill_md": _SAFE_ASCII_BODY}
        params = {"session_id": "sess-a18b"}
    elif case == "body-identity-rejected":
        _activate_agent(org, task_id="TASK-A18I", session="sess-a18i")
        client.headers.pop("Authorization", None)
        body = {
            "slug": bad_slug, "name": "Display", "skill_md": _SAFE_ASCII_BODY,
            "task_id": "spoofed",
        }
        params = {"session_id": "sess-a18i"}
    else:
        _activate_agent(org, task_id="TASK-A18S", session="sess-stale")
        client.headers.pop("Authorization", None)
        # The stale generation still resolves its context, but the tracker's
        # active generation is a different session.
        monkeypatch.setattr(org.sessions, "get_active", lambda task_id, agent: "sess-current")
        body = {"slug": bad_slug, "name": "Display", "skill_md": _SAFE_ASCII_BODY}
        params = {"session_id": "sess-stale"}

    before = _full_custom_state(org)
    response = client.post(path, params=params, json=body)

    assert response.status_code == expected_status, response.text
    assert response.json()["detail"]["code"] == expected_code
    assert response.json()["detail"]["code"] != "invalid_slug"
    assert _full_custom_state(org) == before


def test_a18_human_missing_metadata_precedes_invalid_identity(
    client_with_runtime,
):
    """A18: the human create route's required-metadata check precedes the
    logical-identity gate: a non-conforming slug with a missing ``name`` is 422
    ``invalid_request`` (never ``invalid_slug``) with no residue."""
    client, org = client_with_runtime
    before = _full_custom_state(org)
    response = client.post(BASE, json={"slug": "café-workflow", "skill_md": _SAFE_ASCII_BODY})
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "invalid_request"
    assert response.json()["detail"]["code"] != "invalid_slug"
    assert _full_custom_state(org) == before


def test_a18_append_lookup_and_mutability_refusals_precede_invalid_identity(
    client_with_runtime,
):
    """A18: on ``add_version`` the current-row lookup and ``_mutable`` check
    precede the stored-slug identity gate. A missing skill is 404, a missing
    ``skill_md`` on a synthetic non-ASCII row is 422 ``invalid_request``, and a
    purged row is 410 ``skill_purged`` — none is ``invalid_slug`` and none
    leaves residue."""
    from runtime.infrastructure.artifact_store import ArtifactStore
    from runtime.orchestrator._paths import OrgPaths
    from runtime.skills.custom import service as custom_service

    client, org = client_with_runtime
    conn = getattr(org.db, "_conn", org.db)
    before = _full_custom_state(org)

    missing = client.post(
        f"{BASE}/custom:brand-new/versions",
        json={"skill_md": "---\nname: café-workflow\ndescription: d\n---\n"},
    )
    assert missing.status_code == 404
    assert _full_custom_state(org) == before

    def _seed(skill_id: str, *, purged: bool, stored_slug: str) -> None:
        content = "---\nname: café-workflow\ndescription: stored\n---\n"
        key = ArtifactStore(OrgPaths(org.root).artifacts_dir).put(
            f"custom-skills/ordering-stored/{skill_id.replace(':', '-')}/SKILL.md",
            content.encode(),
        ).name
        conn.execute(
            "INSERT INTO custom_skills "
            "(id,org_slug,slug,name,description,origin_kind,created_at,created_by,"
            "purged_at,purge_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (skill_id, "alpha", stored_slug, "Stored", "stored", "human",
             custom_service.now(), "founder",
             custom_service.now() if purged else None, "purge:x" if purged else None),
        )
        conn.execute(
            """INSERT INTO custom_skill_versions
               (skill_id,content_hash,content_artifact_key,skill_md_cache,validation_state,
                validator_version,validation_findings,created_at,author_kind,author_identity)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (skill_id, hashlib.sha256(content.encode()).hexdigest(), key, content,
             "invalid", "THR-055/1.0.0", '["stored"]', custom_service.now(),
             "human", "founder"),
        )
        version_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "UPDATE custom_skills SET current_version_id=? WHERE id=?", (version_id, skill_id)
        )
        conn.commit()

    _seed("custom:ordering-unicode", purged=False, stored_slug="café-ordering-unicode")
    present_before = _residue_snapshot(org, "custom:ordering-unicode")
    present_full_before = _full_custom_state(org)
    no_body = client.post(f"{BASE}/custom:ordering-unicode/versions", json={})
    assert no_body.status_code == 422, no_body.text
    assert no_body.json()["detail"]["code"] == "invalid_request"
    assert _residue_snapshot(org, "custom:ordering-unicode") == present_before
    assert _full_custom_state(org) == present_full_before

    _seed("custom:ordering-purged", purged=True, stored_slug="café-ordering-purged")
    purged_before = _residue_snapshot(org, "custom:ordering-purged")
    purged_full_before = _full_custom_state(org)
    purged = client.post(
        f"{BASE}/custom:ordering-purged/versions",
        json={"skill_md": "---\nname: café-workflow\ndescription: d\n---\n"},
    )
    assert purged.status_code == 410, purged.text
    assert purged.json()["detail"]["code"] == "skill_purged"
    assert _residue_snapshot(org, "custom:ordering-purged") == purged_before
    assert _full_custom_state(org) == purged_full_before


# ── Finding 7: R5c/A16/C4-C6 frozen legacy replay ────────────────────────

_LEGACY_REPLAY_BODY = "# Heading-first legacy\n\nbody\n"


def _seed_frozen_legacy_version(
    org, skill_id, *, content, parent_id=None, state="valid", findings="[]",
    validator_version="THR-055/1.0.0",
):
    """Seed a FROZEN historical version directly in the disposable fixture with
    old marker/findings/state/provenance. The old bytes are never submitted
    through the new validator to fabricate history."""
    from runtime.infrastructure.artifact_store import ArtifactStore
    from runtime.orchestrator._paths import OrgPaths
    from runtime.skills.custom import service as custom_service

    conn = getattr(org.db, "_conn", org.db)
    digest = hashlib.sha256(content.encode()).hexdigest()
    key = ArtifactStore(OrgPaths(org.root).artifacts_dir).put(
        f"custom-skills/legacy-replay/{digest}/SKILL.md", content.encode()
    ).name
    conn.execute(
        """INSERT INTO custom_skill_versions
           (skill_id,parent_version_id,content_hash,content_artifact_key,skill_md_cache,
            validation_state,validator_version,validation_findings,created_at,
            author_kind,author_identity,source_task_id,source_session_id,task_brief_digest)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (skill_id, parent_id, digest, key, content, state, validator_version, findings,
         custom_service.now(), "human", "founder", "TASK-LEGACY", "sess-legacy", "legacy-brief"),
    )
    version_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    return version_id, digest


@pytest.mark.parametrize("supplied", ["", "divergent"])
@pytest.mark.parametrize("is_current", [True, False])
@pytest.mark.parametrize("legacy_state", ["valid", "invalid"])
@pytest.mark.parametrize("surface", ["human-append", "agent-same-owner-append"])
def test_r5c_frozen_legacy_replay_409_before_artifacts(
    client_with_runtime, monkeypatch, surface, legacy_state, is_current, supplied,
):
    """R5c/A16/C4-C6: a FROZEN legacy version (old marker/findings/state/
    provenance seeded directly) replayed byte-identically as current or
    noncurrent is 409 ``version_content_exists`` before any artifact write,
    including with an explicit blank or divergent request description. The
    complete version/catalog/eligibility/artifact snapshot is unchanged."""
    client, org = client_with_runtime
    _add_agent(org)
    conn = getattr(org.db, "_conn", org.db)
    token = client.headers.get("Authorization")
    valid_md = "---\nname: legacy-replay\ndescription: current valid\n---\n"

    if surface == "human-append":
        created = client.post(
            BASE, json={"slug": "legacy-replay", "name": "Legacy Replay", "skill_md": valid_md}
        )
    else:
        _activate_agent(org, task_id="TASK-LEG", session="sess-leg")
        client.headers.pop("Authorization", None)
        created = client.post(
            _AGENT_CREATE_PATHS[0], params={"session_id": "sess-leg"},
            json={"slug": "legacy-replay", "name": "Legacy Replay", "skill_md": valid_md},
        )
        client.headers["Authorization"] = token
    assert created.status_code == 201, created.text
    if surface == "human-append":
        skill_id, valid_version = created.json()["skill_id"], created.json()["version_id"]
    else:
        skill_id = created.json()["skill"]["id"]
        valid_version = created.json()["version"]["id"]

    client.headers["Authorization"] = token
    _grant_org_allow(client, skill_id, valid_version)

    legacy_findings = (
        '["SKILL.md must start with a YAML frontmatter fence"]'
        if legacy_state == "invalid" else "[]"
    )
    legacy_version, legacy_digest = _seed_frozen_legacy_version(
        org, skill_id, content=_LEGACY_REPLAY_BODY, parent_id=valid_version,
        state=legacy_state, findings=legacy_findings,
    )
    if is_current:
        conn.execute(
            "UPDATE custom_skills SET current_version_id=? WHERE id=?",
            (legacy_version, skill_id),
        )
        conn.commit()

    before = _residue_snapshot(org, skill_id)
    before_artifacts = _artifact_bytes_state(org)
    write_calls = _no_write_artifact_seam(monkeypatch)

    request_body = {"skill_md": _LEGACY_REPLAY_BODY, "description": supplied}
    if surface == "human-append":
        replay = client.post(f"{BASE}/{skill_id}/versions", json=request_body)
    else:
        client.headers.pop("Authorization", None)
        replay = client.post(
            _AGENT_CREATE_PATHS[0], params={"session_id": "sess-leg"},
            json={"slug": "legacy-replay", "name": "Legacy Replay",
                  "skill_md": _LEGACY_REPLAY_BODY, "description": supplied},
        )
        client.headers["Authorization"] = token

    assert replay.status_code == 409, replay.text
    assert replay.json()["detail"]["code"] == "version_content_exists"
    assert write_calls == []
    assert _residue_snapshot(org, skill_id) == before
    assert _artifact_bytes_state(org) == before_artifacts
    frozen = conn.execute(
        "SELECT content_hash, skill_md_cache, validation_state, validator_version, "
        "validation_findings, source_task_id, source_session_id, task_brief_digest "
        "FROM custom_skill_versions WHERE id=?", (legacy_version,)
    ).fetchone()
    assert frozen["content_hash"] == legacy_digest
    assert frozen["skill_md_cache"] == _LEGACY_REPLAY_BODY
    assert frozen["validation_state"] == legacy_state
    assert frozen["validator_version"] == "THR-055/1.0.0"
    assert frozen["validation_findings"] == legacy_findings
    assert frozen["source_task_id"] == "TASK-LEGACY"
    assert frozen["source_session_id"] == "sess-legacy"
    assert frozen["task_brief_digest"] == "legacy-brief"

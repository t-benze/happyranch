from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.daemon.org_state import OrgState
from runtime.infrastructure.database import Database
from runtime.workflows.templates import (
    WorkflowTemplateError,
    WorkflowTemplatePrincipal,
    WorkflowTemplateStore,
)


VALID_DEFINITION = {
    "kind": "product-design",
    "schema_version": 1,
    "description": "Immutable PRD authoring and current-revision review",
    "author": {
        "role": "product-lead",
        "kind": "agent",
        "artifact": "immutable-prd-revision",
    },
    "reviewers": [
        {"role": "founder", "kind": "human"},
        {"role": "implementer", "kind": "agent"},
        {"role": "tester", "kind": "agent"},
    ],
    "approval": {
        "mode": "all",
        "revision": "current",
        "required_roles": ["founder", "implementer", "tester"],
    },
    "request_changes": {"action": "return-to-author"},
}


def _org(tmp_path: Path) -> OrgState:
    root = tmp_path / "org"
    (root / "org" / "agents").mkdir(parents=True)
    (root / "org" / "teams.yaml").write_text("teams: {}\n")
    for name in ("workspaces", "kb", "threads", "artifacts"):
        (root / name).mkdir()
    return OrgState.load(slug="alpha", root=root, settings=Settings())


def _principal(*, team: str = "engineering", callback=lambda: None):
    return WorkflowTemplatePrincipal.agent(
        org_slug="alpha",
        agent_name="engineering-head",
        team_slug=team,
        task_id="TASK-1",
        session_id="sess-1",
        revalidate=callback,
    )


def _publish(store: WorkflowTemplateStore, *, key: str = "op-1", expected: int = 0,
             definition=VALID_DEFINITION, name: str = "product-design",
             principal=None):
    return store.publish_version(
        org_slug="alpha",
        principal=principal or _principal(),
        operation_key=key,
        namespace="org/alpha/team/engineering",
        template_name=name,
        definition=definition,
        expected_current_version=expected,
    )


def _counts(org: OrgState) -> dict[str, int]:
    return {
        table: org.db._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in (
            "workflow_template_drafts",
            "workflow_template_versions",
            "workflow_template_identities",
            "workflow_template_identity_versions",
            "workflow_template_publish_operations",
        )
    }


def test_canonical_digest_and_immutable_read_vector(tmp_path: Path) -> None:
    org = _org(tmp_path)
    result = _publish(WorkflowTemplateStore(org.db))

    assert result.version == 1
    assert result.definition_bytes == (
        b'{"approval":{"mode":"all","required_roles":["founder","implementer",'
        b'"tester"],"revision":"current"},"author":{"artifact":"immutable-prd-'
        b'revision","kind":"agent","role":"product-lead"},"description":"Immutable '
        b'PRD authoring and current-revision review","kind":"product-design",'
        b'"request_changes":{"action":"return-to-author"},"reviewers":[{"kind":'
        b'"human","role":"founder"},{"kind":"agent","role":"implementer"},{'
        b'"kind":"agent","role":"tester"}],"schema_version":1}'
    )
    assert result.definition_digest == "0d815497899d1e6f43022ec8c88574a36bbf295a05fbf00f411c8a8e324c7f57"
    assert result.publisher == {
        "principal_id": "engineering-head",
        "principal_kind": "agent",
        "proof_kind": "task_session",
        "session_id": "sess-1",
        "task_id": "TASK-1",
    }
    assert WorkflowTemplateStore(org.db).get(
        org_slug="alpha",
        namespace="org/alpha/team/engineering",
        template_name="product-design",
        version=1,
    ) == result
    assert WorkflowTemplateStore(org.db).list(
        org_slug="alpha", namespace="org/alpha/team/engineering"
    ) == (result,)


def test_identical_replay_is_read_only_and_changed_payload_conflicts(tmp_path: Path) -> None:
    org = _org(tmp_path)
    store = WorkflowTemplateStore(org.db)
    first = _publish(store)
    before = _counts(org)

    assert _publish(store) == first
    assert _counts(org) == before

    changed = copy.deepcopy(VALID_DEFINITION)
    changed["description"] = "A changed but still valid product-design description"
    with pytest.raises(WorkflowTemplateError, match="template_publish_operation_conflict") as exc:
        _publish(store, definition=changed)
    assert exc.value.code == "template_publish_operation_conflict"
    assert _counts(org) == before


def test_stale_version_and_duplicate_content_leave_zero_residue(tmp_path: Path) -> None:
    org = _org(tmp_path)
    store = WorkflowTemplateStore(org.db)
    _publish(store)
    before = _counts(org)

    with pytest.raises(WorkflowTemplateError) as stale:
        _publish(store, key="op-stale", expected=0)
    assert stale.value.code == "template_version_cas_stale"
    assert _counts(org) == before

    with pytest.raises(WorkflowTemplateError) as duplicate:
        _publish(store, key="op-duplicate", expected=1)
    assert duplicate.value.code == "template_content_already_published"
    assert _counts(org) == before


def test_two_writers_from_one_predecessor_create_one_gap_free_version(tmp_path: Path) -> None:
    org = _org(tmp_path)
    first_store = WorkflowTemplateStore(org.db)
    _publish(first_store)
    second_db = Database(org.db.path)
    stores = (first_store, WorkflowTemplateStore(second_db))
    definitions = []
    for label in ("a", "b"):
        item = copy.deepcopy(VALID_DEFINITION)
        item["description"] = f"Valid product-design template variant {label}"
        definitions.append(item)

    def attempt(index: int):
        try:
            return _publish(
                stores[index], key=f"op-{index + 2}", expected=1,
                definition=definitions[index],
            ).version
        except WorkflowTemplateError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, (0, 1)))
    second_db.close()

    assert sorted(str(value) for value in outcomes) == ["2", "template_version_cas_stale"]
    rows = org.db._conn.execute(
        "SELECT version FROM workflow_template_identity_versions ORDER BY version"
    ).fetchall()
    assert [row[0] for row in rows] == [1, 2]
    assert _counts(org) == {
        "workflow_template_drafts": 2,
        "workflow_template_versions": 2,
        "workflow_template_identities": 1,
        "workflow_template_identity_versions": 2,
        "workflow_template_publish_operations": 2,
    }


@pytest.mark.parametrize(
    ("org_slug", "team", "name"),
    [
        ("../alpha", "engineering", "product-design"),
        ("Alpha", "engineering", "product-design"),
        ("álpha", "engineering", "product-design"),
        ("alpha", "../engineering", "product-design"),
        ("alpha", "Engineering", "product-design"),
        ("alpha", "engineering", "../product-design"),
        ("alpha", "engineering", "Product-Design"),
        ("alpha", "engineering", "产品"),
        ("alpha", "engineering", "a" * 64),
    ],
)
def test_identifier_grammar_fails_closed_without_residue(
    tmp_path: Path, org_slug: str, team: str, name: str,
) -> None:
    org = _org(tmp_path)
    store = WorkflowTemplateStore(org.db)
    principal = WorkflowTemplatePrincipal.agent(
        org_slug=org_slug, agent_name="manager", team_slug=team,
        task_id="TASK-1", session_id="sess-1", revalidate=lambda: None,
    )
    with pytest.raises(WorkflowTemplateError) as exc:
        store.publish_version(
            org_slug=org_slug,
            principal=principal,
            operation_key="op",
            namespace=f"org/{org_slug}/team/{team}",
            template_name=name,
            definition=VALID_DEFINITION,
            expected_current_version=0,
        )
    assert exc.value.code in {"invalid_template_namespace", "invalid_template_name"}
    assert _counts(org) == {table: 0 for table in _counts(org)}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update({"unknown": True}),
        lambda value: value.update({"kind": "coding"}),
        lambda value: value["reviewers"].pop(),
        lambda value: value["approval"].update({"revision": "any"}),
        lambda value: value["request_changes"].update({"action": "continue"}),
    ],
)
def test_definition_is_closed_product_design_data(tmp_path: Path, mutate) -> None:
    org = _org(tmp_path)
    definition = copy.deepcopy(VALID_DEFINITION)
    mutate(definition)
    with pytest.raises(WorkflowTemplateError) as exc:
        _publish(WorkflowTemplateStore(org.db), definition=definition)
    assert exc.value.code == "invalid_template_definition"
    assert _counts(org) == {table: 0 for table in _counts(org)}


@pytest.mark.parametrize(
    "state",
    [
        "installed_legacy_only", "enable_requested", "compatibility_verified",
        "enabled", "disable_requested", "draining", "drained",
    ],
)
def test_publish_is_inert_and_allowed_in_every_cutover_state(
    tmp_path: Path, state: str,
) -> None:
    org = _org(tmp_path)
    conn = org.db._conn
    conn.execute(
        "UPDATE workflow_cutover_state SET state=?, generation=9, operation_key='held', "
        "disable_reason='unchanged' WHERE singleton=1",
        (state,),
    )
    conn.commit()
    protected = (
        "workflow_cutover_state", "workflow_cutover_events", "workflow_activations",
        "workflow_activation_operations", "workflow_instances",
        "workflow_dispatch_operations", "workflow_dispatch_outbox",
        "workflow_authority_pointers", "tasks", "audit_log",
    )
    before = {table: tuple(map(tuple, conn.execute(f"SELECT * FROM {table}"))) for table in protected}

    _publish(WorkflowTemplateStore(org.db))

    after = {table: tuple(map(tuple, conn.execute(f"SELECT * FROM {table}"))) for table in protected}
    assert after == before


def test_revalidation_runs_inside_transaction_and_refusal_rolls_back(tmp_path: Path) -> None:
    org = _org(tmp_path)
    observed: list[bool] = []

    def refuse() -> None:
        observed.append(org.db._conn.in_transaction)
        raise WorkflowTemplateError("manager_authority_lost")

    with pytest.raises(WorkflowTemplateError) as exc:
        _publish(WorkflowTemplateStore(org.db), principal=_principal(callback=refuse))
    assert exc.value.code == "manager_authority_lost"
    assert observed == [True]
    assert _counts(org) == {table: 0 for table in _counts(org)}

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
        for table in _TEMPLATE_TABLES
    }


def _rows(org: OrgState) -> dict[str, tuple[tuple, ...]]:
    return {
        table: tuple(map(tuple, org.db._conn.execute(f"SELECT * FROM {table}")))
        for table in _PROTECTED_TABLES
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
    "schema_version",
    [True, False, 1.0, "1"],
    ids=["boolean-true", "boolean-false", "float", "numeric-string"],
)
def test_schema_version_requires_exact_integer_one_without_residue(
    tmp_path: Path, schema_version: object,
) -> None:
    org = _org(tmp_path)
    definition = copy.deepcopy(VALID_DEFINITION)
    definition["schema_version"] = schema_version
    before_counts = _counts(org)
    before_rows = _rows(org)

    with pytest.raises(WorkflowTemplateError) as exc:
        _publish(WorkflowTemplateStore(org.db), definition=definition)

    assert exc.value.code == "invalid_template_definition"
    assert before_counts == {table: 0 for table in _TEMPLATE_TABLES}
    assert _counts(org) == before_counts
    assert _rows(org) == before_rows


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


# Literal accepted B02/F1 vectors; expected contracts are independent of the compiler.
GENERIC_VECTORS = (('product',
  {'approval': {'mode': 'all',
                'required_roles': ['founder', 'implementer', 'tester'],
                'revision': 'current'},
   'author': {'kind': 'agent', 'role': 'product-lead'},
   'description': 'Prepare and independently review a product requirements document.',
   'kind': 'document-review',
   'outcomes': ['approved', 'changes_requested'],
   'output': {'description': 'Product requirements document',
              'primitive': 'immutable-document-revision'},
   'request_changes': {'action': 'return-to-author',
                       'invalidate': 'all-prior-receipts',
                       'revision': 'new'},
   'reviewers': [{'kind': 'human', 'role': 'founder'},
                 {'kind': 'agent', 'role': 'implementer'},
                 {'kind': 'agent', 'role': 'tester'}],
   'schema_version': 2,
   'submission': {'timing': 'while-active-or-completed'}},
  {'approval': {'mode': 'all',
                'required_roles': ['founder', 'implementer', 'tester'],
                'revision': 'current'},
   'author_role': 'product-lead',
   'format': 'workflow-document-contract@2',
   'outcomes': ['approved', 'changes_requested'],
   'output': {'description': 'Product requirements document',
              'primitive': 'immutable-document-revision'},
   'request_changes': {'action': 'return-to-author',
                       'invalidate': 'all-prior-receipts',
                       'revision': 'new'},
   'reviewer_roles': ['founder', 'implementer', 'tester'],
   'role_kinds': {'founder': 'human',
                  'implementer': 'agent',
                  'product-lead': 'agent',
                  'tester': 'agent'},
   'submission': {'timing': 'while-active-or-completed'}},
  'd89f905c2d943b83390894f5e6729c60264a3db2ba8df24b88b808a17722be0b',
  '8bd2766225e2fa21ff0f1bfcbf3aa962deb2fab3b9f4ea9b54141e7de94add09'),
 ('proposal',
  {'approval': {'mode': 'all', 'required_roles': ['sponsor'], 'revision': 'current'},
   'author': {'kind': 'agent', 'role': 'proposal-writer'},
   'description': 'Prepare a bounded written proposal for one human review.',
   'kind': 'document-review',
   'outcomes': ['approved', 'changes_requested'],
   'output': {'description': 'Written proposal', 'primitive': 'immutable-document-revision'},
   'request_changes': {'action': 'return-to-author',
                       'invalidate': 'all-prior-receipts',
                       'revision': 'new'},
   'reviewers': [{'kind': 'human', 'role': 'sponsor'}],
   'schema_version': 2,
   'submission': {'timing': 'on-completion'}},
  {'approval': {'mode': 'all', 'required_roles': ['sponsor'], 'revision': 'current'},
   'author_role': 'proposal-writer',
   'format': 'workflow-document-contract@2',
   'outcomes': ['approved', 'changes_requested'],
   'output': {'description': 'Written proposal', 'primitive': 'immutable-document-revision'},
   'request_changes': {'action': 'return-to-author',
                       'invalidate': 'all-prior-receipts',
                       'revision': 'new'},
   'reviewer_roles': ['sponsor'],
   'role_kinds': {'proposal-writer': 'agent', 'sponsor': 'human'},
   'submission': {'timing': 'on-completion'}},
  '39b50119bce6e2b1197c405db6a1b8a41c77278f704e2059d907b0dee62663cf',
  '2cf9ecc23d90d353a93c90df84057b78cc0a0dd24e8bb4b96ce9be2a9b8077ee'),
 ('A',
  {'approval': {'mode': 'all', 'required_roles': ['sponsor'], 'revision': 'current'},
   'author': {'kind': 'agent', 'role': 'proposal-writer'},
   'description': 'Prepare a bounded written proposal for one human review.',
   'kind': 'document-review',
   'outcomes': ['approved'],
   'output': {'description': 'Written proposal', 'primitive': 'immutable-document-revision'},
   'request_changes': None,
   'reviewers': [{'kind': 'human', 'role': 'sponsor'}],
   'schema_version': 2,
   'submission': {'timing': 'on-completion'}},
  {'approval': {'mode': 'all', 'required_roles': ['sponsor'], 'revision': 'current'},
   'author_role': 'proposal-writer',
   'format': 'workflow-document-contract@2',
   'outcomes': ['approved'],
   'output': {'description': 'Written proposal', 'primitive': 'immutable-document-revision'},
   'request_changes': None,
   'reviewer_roles': ['sponsor'],
   'role_kinds': {'proposal-writer': 'agent', 'sponsor': 'human'},
   'submission': {'timing': 'on-completion'}},
  'c9e84dc59d2d7a933c9cc389b0c79b90f00afe2e4f219bbef9bc03a3591985b1',
  '08139df31135b67e39ce8ab51ddbfb559050788ea171610ccf68e99d23f73e19'),
 ('Z',
  {'approval': {'mode': 'all', 'required_roles': ['sponsor'], 'revision': 'current'},
   'author': {'kind': 'agent', 'role': 'proposal-writer'},
   'description': 'Prepare a bounded written proposal for one agent review.',
   'kind': 'document-review',
   'outcomes': ['approved', 'changes_requested'],
   'output': {'description': 'Written proposal', 'primitive': 'immutable-document-revision'},
   'request_changes': {'action': 'return-to-author',
                       'invalidate': 'all-prior-receipts',
                       'revision': 'new'},
   'reviewers': [{'kind': 'agent', 'role': 'sponsor'}],
   'schema_version': 2,
   'submission': {'timing': 'on-completion'}},
  {'approval': {'mode': 'all', 'required_roles': ['sponsor'], 'revision': 'current'},
   'author_role': 'proposal-writer',
   'format': 'workflow-document-contract@2',
   'outcomes': ['approved', 'changes_requested'],
   'output': {'description': 'Written proposal', 'primitive': 'immutable-document-revision'},
   'request_changes': {'action': 'return-to-author',
                       'invalidate': 'all-prior-receipts',
                       'revision': 'new'},
   'reviewer_roles': ['sponsor'],
   'role_kinds': {'proposal-writer': 'agent', 'sponsor': 'agent'},
   'submission': {'timing': 'on-completion'}},
  '6e525c69be0fdd20a12ea20f875ebc0c1f8e2bf02bcff26c595f864cef03c770',
  '09ea1a7abb939909ea860e99e96fbe5f48ddeb6c44a307b9525382758c0f764d'))


@pytest.fixture
def offline_template_db(tmp_path: Path):
    """Real fresh org SQLite storage only; no daemon, roster or platform probe."""
    from runtime.infrastructure.workflow_schema import initialize_complete_org_schema
    db = Database(tmp_path / "offline-org.db")
    initialize_complete_org_schema(db, expected_org_slug="alpha")
    try:
        yield db
    finally:
        db.close()


@pytest.mark.parametrize("name,definition,contract,raw_sha,contract_sha", GENERIC_VECTORS,
                         ids=["product", "proposal", "A", "Z"])
def test_generic_publication_pins_and_canonical_rows(
    offline_template_db: Database, name: str, definition: dict, contract: dict,
    raw_sha: str, contract_sha: str,
) -> None:
    import hashlib
    import json
    from runtime.infrastructure.workflow_schema import validate_workflow_schema

    db = offline_template_db
    store = WorkflowTemplateStore(db)
    protected = ("tasks", "workflow_instances", "workflow_activations", "workflow_draft_dispatch_intents")
    before = {table: tuple(map(tuple, db._conn.execute(f"SELECT * FROM {table}"))) for table in protected}
    try:
        result = _publish(store, definition=definition)
    except WorkflowTemplateError as exc:
        pytest.fail(f"real publication expected immutable version, observed {exc.code}")
    expected_bytes = json.dumps(definition, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    assert result.definition_bytes == expected_bytes
    assert result.definition_digest == raw_sha == hashlib.sha256(expected_bytes).hexdigest()
    assert (result.compiler_pin, result.validator_pin, result.source_pin) == (
        "workflow-compiler@2", "workflow-validator@2", "operator-input@2",
    )
    for table in ("workflow_template_drafts", "workflow_template_versions"):
        rows = db._conn.execute(f"SELECT definition_bytes,definition_digest,compiler_pin,validator_pin,source_pin FROM {table}").fetchall()
        assert [tuple(row) for row in rows] == [(expected_bytes, raw_sha, "workflow-compiler@2", "workflow-validator@2", "operator-input@2")]
    assert store.get(org_slug="alpha", namespace=result.namespace, template_name=result.template_name, version=result.version) == result
    assert store.list(org_slug="alpha", namespace=result.namespace) == (result,)
    assert _publish(store, definition=definition) == result
    assert validate_workflow_schema(db._conn, expected_org_slug="alpha") == "E"
    assert {table: tuple(map(tuple, db._conn.execute(f"SELECT * FROM {table}"))) for table in protected} == before


@pytest.mark.parametrize("mutation", [
    "unknown-root", "principal", "author-human", "empty-reviewers", "too-many-reviewers",
    "duplicate-author", "duplicate-reviewer", "two-humans", "bad-role", "long-role",
    "bad-reviewer-kind", "missing-required", "extra-required", "duplicate-required",
    "bad-approval-mode", "bad-approval-revision", "empty-description", "long-description",
    "empty-output-description", "long-output-description", "code-output", "output-identity",
    "unknown-outcome", "no-approval", "duplicate-outcome", "empty-outcomes", "null-dual-return",
    "nonnull-approval-only-return", "bad-return-action", "bad-return-revision", "bad-invalidation",
    "unknown-timing", "extra-submission", "schema-bool", "schema-float", "schema-string", "bad-kind",
])
def test_generic_closed_definition_refusal_has_no_rows(offline_template_db: Database, mutation: str) -> None:
    definition = copy.deepcopy(GENERIC_VECTORS[1][1])
    if mutation == "unknown-root":
        definition["command"] = "ship code"
    elif mutation == "principal":
        definition["author"]["principal"] = "dev_agent"
    elif mutation == "author-human":
        definition["author"]["kind"] = "human"
    elif mutation == "empty-reviewers":
        definition["reviewers"] = []
    elif mutation == "too-many-reviewers":
        definition["reviewers"] = [{"role": f"reviewer-{i}", "kind": "agent"} for i in range(4)]
    elif mutation == "duplicate-author":
        definition["reviewers"][0]["role"] = "proposal-writer"
    elif mutation == "duplicate-reviewer":
        definition["reviewers"] *= 2
    elif mutation == "two-humans":
        definition["reviewers"].append({"role": "second-human", "kind": "human"})
        definition["approval"]["required_roles"].append("second-human")
    elif mutation in {"bad-role", "long-role"}:
        definition["author"]["role"] = "../author" if mutation == "bad-role" else "a" * 64
    elif mutation == "bad-reviewer-kind":
        definition["reviewers"][0]["kind"] = "service"
    elif mutation in {"missing-required", "extra-required", "duplicate-required"}:
        definition["approval"]["required_roles"] = {"missing-required": [], "extra-required": ["sponsor", "phantom"], "duplicate-required": ["sponsor", "sponsor"]}[mutation]
    elif mutation in {"bad-approval-mode", "bad-approval-revision"}:
        definition["approval"]["mode" if mutation.endswith("mode") else "revision"] = "any"
    elif mutation in {"empty-description", "long-description"}:
        definition["description"] = " " if mutation.startswith("empty") else "a" * 2001
    elif mutation in {"empty-output-description", "long-output-description"}:
        definition["output"]["description"] = " " if mutation.startswith("empty") else "a" * 2001
    elif mutation == "code-output":
        definition["output"]["primitive"] = "code-delivery"
    elif mutation == "output-identity":
        definition["output"]["task_id"] = "TASK-1"
    elif mutation in {"unknown-outcome", "no-approval", "duplicate-outcome", "empty-outcomes"}:
        definition["outcomes"] = {"unknown-outcome": ["approved", "timeout"], "no-approval": ["changes_requested"], "duplicate-outcome": ["approved", "approved"], "empty-outcomes": []}[mutation]
    elif mutation == "null-dual-return":
        definition["request_changes"] = None
    elif mutation == "nonnull-approval-only-return":
        definition["outcomes"] = ["approved"]
    elif mutation.startswith("bad-return") or mutation == "bad-invalidation":
        key = {"bad-return-action": "action", "bad-return-revision": "revision", "bad-invalidation": "invalidate"}[mutation]
        definition["request_changes"][key] = "continue"
    elif mutation == "unknown-timing":
        definition["submission"]["timing"] = "always-active"
    elif mutation == "extra-submission":
        definition["submission"]["executor"] = "codex"
    elif mutation.startswith("schema-"):
        definition["schema_version"] = {"schema-bool": True, "schema-float": 2.0, "schema-string": "2"}[mutation]
    elif mutation == "bad-kind":
        definition["kind"] = "coding"
    db = offline_template_db
    tables = (*_TEMPLATE_TABLES, "tasks", "workflow_instances", "workflow_draft_dispatch_intents")
    before = {table: tuple(map(tuple, db._conn.execute(f"SELECT * FROM {table}"))) for table in tables}
    with pytest.raises(WorkflowTemplateError) as exc:
        _publish(WorkflowTemplateStore(db), definition=definition)
    assert exc.value.code == "invalid_template_definition"
    assert {table: tuple(map(tuple, db._conn.execute(f"SELECT * FROM {table}"))) for table in tables} == before


def test_generic_retained_versions_dispatch_without_current_pointer(offline_template_db: Database) -> None:
    from runtime.infrastructure.workflow_schema import validate_workflow_schema
    db = offline_template_db
    store = WorkflowTemplateStore(db)
    published = []
    for version, row in enumerate(GENERIC_VECTORS[1:], 1):
        published.append(_publish(store, definition=row[1], name="written-proposal",
                                  key=f"policy-{version}", expected=version - 1))
    legacy = _publish(store, definition=VALID_DEFINITION, name="written-proposal", key="legacy-four", expected=3)
    assert [item.version for item in published] == [1, 2, 3]
    assert legacy.version == 4 and legacy.compiler_pin == "workflow-compiler@1"
    assert db._conn.execute("SELECT current_version FROM workflow_template_identities").fetchone()[0] == 4
    assert store.get(org_slug="alpha", namespace=legacy.namespace, template_name="written-proposal", version=2) == published[1]
    assert store.list(org_slug="alpha", namespace=legacy.namespace) == (*published, legacy)
    assert validate_workflow_schema(db._conn, expected_org_slug="alpha") == "E"


@pytest.mark.parametrize("table", ["workflow_template_drafts", "workflow_template_versions", "both"])
@pytest.mark.parametrize("pin", ["compiler_pin", "validator_pin", "source_pin"])
@pytest.mark.parametrize("replacement", ["unknown@9", "legacy-family"])
def test_generic_all_history_pin_corruption_refuses(
    offline_template_db: Database, table: str, pin: str, replacement: str,
) -> None:
    from runtime.infrastructure.workflow_schema import validate_workflow_schema
    db = offline_template_db
    store = WorkflowTemplateStore(db)
    old = _publish(store, definition=GENERIC_VECTORS[2][1], name="written-proposal")
    _publish(store, definition=GENERIC_VECTORS[3][1], name="written-proposal", key="next", expected=1)
    before = tuple(map(tuple, db._conn.execute("SELECT * FROM workflow_template_publish_operations")))
    affected = ("workflow_template_drafts", "workflow_template_versions") if table == "both" else (table,)
    value = replacement if replacement != "legacy-family" else {"compiler_pin": "workflow-compiler@1", "validator_pin": "workflow-validator@1", "source_pin": "operator-input@1"}[pin]
    for target in affected:
        db._conn.execute(f"UPDATE {target} SET {pin}=? WHERE definition_digest=?", (value, old.definition_digest))
    db._conn.commit()
    with pytest.raises(ValueError):
        validate_workflow_schema(db._conn, expected_org_slug="alpha")
    if table != "workflow_template_drafts":
        with pytest.raises(WorkflowTemplateError) as exc:
            store.get(org_slug="alpha", namespace=old.namespace, template_name="written-proposal", version=old.version)
        assert exc.value.code == "template_storage_corrupt"
    assert tuple(map(tuple, db._conn.execute("SELECT * FROM workflow_template_publish_operations"))) == before

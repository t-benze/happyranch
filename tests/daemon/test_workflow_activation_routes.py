from __future__ import annotations

import sqlite3
import copy

import pytest
from fastapi.testclient import TestClient

from runtime.config import Settings
from runtime.daemon import paths
from runtime.daemon.app import create_app
from runtime.daemon.state import DaemonState
from runtime.infrastructure.workflow_schema import validate_workflow_schema
from runtime.runtime import RuntimeDir
from tests.workflows.test_template_store import VALID_DEFINITION


BASE = "/api/v1/orgs/alpha/workflows/activations"


@pytest.fixture
def activation_org(tmp_path, monkeypatch):
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / "daemon"))
    runtime = RuntimeDir.init(tmp_path / "runtime")
    state = DaemonState.from_runtime(runtime, Settings())
    client = TestClient(create_app(state))
    client.headers.update({"Authorization": f"Bearer {paths.ensure_token()}"})
    response = client.post("/api/v1/orgs", json={"slug": "alpha"})
    assert response.status_code == 200, response.text
    org = state.orgs["alpha"]
    try:
        for name, team, role in (
            ("product_lead", "product", "manager"),
            ("engineering_manager", "engineering", "manager"),
            ("code_reviewer", "engineering", "worker"),
            ("dev_agent", "engineering", "worker"),
            ("qa_engineer", "engineering", "worker"),
        ):
            response = client.post("/api/v1/orgs/alpha/agents", json={
                "name": name, "role": role, "executor": "claude",
                **({"new_team": team} if role == "manager" else {"team": team}),
                "description": "isolated S2 acceptance", "system_prompt": "bounded document work",
            })
            assert response.status_code == 200, response.text
        readiness = org.workflow_authority.verify_admission_ready()
        response = client.post("/api/v1/orgs/alpha/workflows/templates/publish", json={
            "operation_key": "publish-v1", "team_slug": "product",
            "template_name": "product-design", "expected_current_version": 0,
            "definition": VALID_DEFINITION,
        })
        assert response.status_code == 201, response.text
        template = response.json()
        response = client.post("/api/v1/orgs/alpha/workflows/cutover/requests", json={
            "operation_key": "enable", "action": "enable", "expected_generation": 1,
        })
        assert response.status_code == 200 and response.json()["state"] == "enabled", response.text
        body = {
            "operation_key": "activation-1", "instance_id": "design-one",
            "expected_activation_revision": 0,
            "template": {"identity_id": template["identity_id"], "version": 1,
                         "definition_digest": template["definition_digest"]},
            "authority": {"namespace": readiness.namespace, "generation": readiness.generation,
                          "snapshot_digest": readiness.snapshot_digest},
            "scope": {"brief": "Draft the bounded initial product requirements document."},
            "bindings": {
                "product-lead": {"kind": "agent", "principal": "product_lead", "team": "product"},
                "founder": {"kind": "human", "principal": "founder", "team": None},
                "implementer": {"kind": "agent", "principal": "dev_agent", "team": "engineering"},
                "tester": {"kind": "agent", "principal": "qa_engineer", "team": "engineering"},
            },
            "eligible_replacements": {"product-lead": [], "founder": [], "implementer": [], "tester": []},
            "allowed_actions": ["draft-document", "submit-immutable-document", "collect-review",
                                "approve-planning-input", "return-to-author"],
            "inputs": [],
        }
        yield client, org, state, body
    finally:
        org.close()



@pytest.fixture
def generic_activation_org(activation_org):
    """Derived real publisher/roster venue; original fixture defaults stay intact."""
    from tests.workflows.test_template_store import GENERIC_VECTORS
    client, org, state, legacy = activation_org
    ddl = tuple(org.db.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY name"))
    cases = {}
    product_bindings = {
        "product-lead": {"kind": "agent", "principal": "product_lead", "team": "product"},
        "founder": {"kind": "human", "principal": "founder", "team": None},
        "implementer": {"kind": "agent", "principal": "dev_agent", "team": "engineering"},
        "tester": {"kind": "agent", "principal": "qa_engineer", "team": "engineering"},
    }
    for name, definition, contract, raw_sha, contract_sha in GENERIC_VECTORS:
        product = name == "product"
        response = client.post("/api/v1/orgs/alpha/workflows/templates/publish", json={
            "operation_key": f"generic-publish-{name}", "team_slug": "product" if product else "engineering",
            "template_name": "document-product" if product else "written-proposal",
            "expected_current_version": 0 if name in {"product", "proposal"} else 1 if name == "A" else 2,
            "definition": copy.deepcopy(definition),
        })
        assert response.status_code == 201, response.text
        published = response.json()
        assert published["definition_digest"] == raw_sha
        assert (published["compiler_pin"], published["validator_pin"], published["source_pin"]) == (
            "workflow-compiler@2", "workflow-validator@2", "operator-input@2")
        bindings = copy.deepcopy(product_bindings) if product else {
            "proposal-writer": {"kind": "agent", "principal": "dev_agent", "team": "engineering"},
            "sponsor": {"kind": "agent" if name == "Z" else "human",
                        "principal": "qa_engineer" if name == "Z" else "founder",
                        "team": "engineering" if name == "Z" else None},
        }
        request = {
            "format": "workflow-activation-request@2", "operation_key": f"generic-admit-{name}",
            "instance_id": f"generic-{name.lower()}", "expected_activation_revision": 0,
            "template": {key: published[key] for key in ("identity_id", "version", "definition_digest")},
            "authority": copy.deepcopy(legacy["authority"]),
            "scope": {"brief": "Draft a bounded product requirements document." if product else "Draft a bounded written proposal."},
            "bindings": bindings, "eligible_replacements": {role: [] for role in bindings},
            "allowed_actions": ["draft-document", "submit-immutable-document", "collect-review", "approve-planning-input"],
            "inputs": [],
        }
        if name != "A":
            request["allowed_actions"].append("return-to-author")
        cases[name] = (request, published, copy.deepcopy(contract), contract_sha)
    assert tuple(org.db.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY name")) == ddl
    assert cases["A"][1]["identity_id"] == cases["Z"][1]["identity_id"]
    assert cases["A"][1]["version"] == 2 and cases["Z"][1]["version"] == 3
    return client, org, state, cases

@pytest.mark.parametrize("format_row", ["legacy", "product", "proposal", "A", "Z"])
def test_initial_activation_commits_authentic_root_and_admitted_lane(activation_org, request, format_row):
    client, org, state, body = activation_org
    expected_author, expected_team = "product_lead", "product"
    if format_row != "legacy":
        client, org, state, cases = request.getfixturevalue("generic_activation_org")
        body, published, contract, contract_sha = cases[format_row]
        if format_row != "product":
            expected_author, expected_team = "dev_agent", "engineering"
    original_tasks = {task.id for task in org.db.list_tasks(limit=1000)}
    response = client.post(BASE, json=body)
    assert response.status_code == 201, response.text
    receipt = response.json()
    assert receipt["execution_started"] is False
    if format_row != "legacy":
        import hashlib, json
        assert receipt["format"] == "workflow-activation-receipt@2"
        assert receipt["bindings"] == body["bindings"]
        assert receipt["template"] == {key: published[key] for key in (
            "identity_id", "version_id", "version", "definition_digest", "compiler_pin", "validator_pin", "source_pin")}
        stored = org.db.execute("SELECT context_bytes FROM workflow_contexts WHERE id=(SELECT context_id FROM workflow_draft_dispatch_intents WHERE id=?)", (receipt["intent_id"],)).fetchone()[0]
        context = json.loads(stored)
        assert context["format"] == "workflow-initial-draft-context@2"
        assert context["document_contract"] == contract
        assert hashlib.sha256(json.dumps(context["document_contract"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest() == contract_sha
        assert context["template"] == json.loads(published["definition_json"])
        assert context["authorization"]["format"] == "workflow-authorization@2"
        assert context["binding"]["format"] == "workflow-binding@2"
        assert context["authorization"]["root_task_id"] == receipt["root_task_id"]
        assert context["authorization"]["original_request"] == body
    else:
        assert "format" not in receipt
    with sqlite3.connect(f"file:{org.root / 'happyranch.db'}?mode=ro", uri=True) as reader:
        reader.row_factory = sqlite3.Row
        rows = reader.execute(
            "SELECT t.*,i.id AS instance_id,d.id AS intent_id,d.state AS intent_state,"
            "d.session_id,d.final_result_id,d.host_execution_id,d.host_launch_started "
            "FROM workflow_instances i JOIN tasks t ON t.id=i.root_task_id "
            "JOIN workflow_draft_dispatch_intents d ON d.task_id=t.id "
            "JOIN workflow_activations a ON a.id=d.activation_id"
        ).fetchall()
        assert len(rows) == 1
        task = dict(rows[0])
        assert task["id"] not in original_tasks
        assert task["id"] == receipt["root_task_id"]
        assert task["intent_id"] == receipt["intent_id"]
        assert task["assigned_agent"] == expected_author and task["team"] == expected_team
        assert task["parent_task_id"] is None and task["revisit_of_task_id"] is None
        assert task["status"] == "pending" and task["intent_state"] == "queued"
        assert task["session_id"] is None and task["current_session_id"] is None
        assert task["final_result_id"] is None and task["host_execution_id"] is None
        assert task["host_launch_started"] == 0
        assert reader.execute("SELECT event_kind FROM workflow_draft_dispatch_events").fetchall()[0][0] == "admitted"
        for table in ("workflow_submissions", "workflow_rounds", "workflow_review_requests",
                      "workflow_dispatch_operations", "workflow_dispatch_outbox", "task_results"):
            assert reader.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        validate_workflow_schema(reader, expected_org_slug="alpha")


def _snapshot(org):
    with sqlite3.connect(f"file:{org.root / 'happyranch.db'}?mode=ro", uri=True) as reader:
        names = [row[0] for row in reader.execute("SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name")]
        return {name: tuple(reader.execute(f'SELECT * FROM "{name}" ORDER BY rowid')) for name in names}


def test_historical_activation_replay_survives_disable_and_authority_change(activation_org):
    client, org, state, body = activation_org
    response = client.post(BASE, json=body)
    assert response.status_code == 201, response.text
    first = response.json()
    response = client.post("/api/v1/orgs/alpha/workflows/cutover/requests", json={
        "action": "disable", "operation_key": "disable", "expected_generation": 4,
    })
    assert response.status_code == 200, response.text
    response = client.post("/api/v1/orgs/alpha/agents", json={
        "name": "later_worker", "role": "worker", "executor": "claude", "team": "engineering",
        "description": "later canonical authority", "system_prompt": "bounded document work",
    })
    assert response.status_code == 200, response.text
    before = _snapshot(org)
    replay = client.post(BASE, json=body)
    assert replay.status_code == 200, replay.text
    second = replay.json()
    for key in ("activation_id", "instance_id", "root_task_id", "intent_id", "template", "authority", "created_at",
                "original_request_digest", "bindings", "context_digest"):
        assert second[key] == first[key]
    assert second["replayed"] is True and second["execution_started"] is False
    assert "workflow_new_runs_disabled" in second["current_eligibility"]["blockers"]
    assert "workflow_activation_authority_stale" in second["current_eligibility"]["blockers"]
    assert _snapshot(org) == before
    shown = client.get(f"{BASE}/{first['activation_id']}")
    assert shown.status_code == 200 and shown.json()["root_task_id"] == first["root_task_id"]
    assert client.get(BASE).json()[0]["activation_id"] == first["activation_id"]
    assert _snapshot(org) == before


def test_operation_conflict_and_instance_cas_create_no_losing_rows(activation_org):
    client, org, state, body = activation_org
    assert client.post(BASE, json=body).status_code == 201
    before = _snapshot(org)
    changed = copy.deepcopy(body)
    changed["scope"]["brief"] = "Different scope"
    response = client.post(BASE, json=changed)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "workflow_activation_operation_conflict"
    changed["operation_key"] = "different-key"
    response = client.post(BASE, json=changed)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "workflow_activation_cas_stale"
    assert _snapshot(org) == before


@pytest.mark.parametrize("field,value,expected", [
    ("expected_activation_revision", True, 422),
    ("expected_activation_revision", 1, 422),
    ("actor", "founder", 403),
    ("task_id", "TASK-1", 403),
    ("session_id", "sess-forged", 403),
    ("result_id", 1, 403),
    ("unknown", "private-source-value", 422),
    ("instance_id", "TASK-1", 422),
    ("allowed_actions", ["execute-code"], 422),
    ("principal", "private-source-value", 403),
    ("principal_id", "private-source-value", 403),
    ("provenance", {"proof_kind": "private-source-value"}, 403),
    ("task", {"id": "TASK-1"}, 403),
    ("org_slug", "foreign", 403),
], ids=["boolean-revision", "noninitial-revision", "actor", "task", "session", "result", "unknown",
        "task-as-instance", "unsupported-action", "principal", "principal-id", "provenance",
        "task-record", "org-claim"])
def test_closed_activation_wire_refuses_without_rows_or_private_echo(activation_org, field, value, expected):
    client, org, state, body = activation_org
    before = _snapshot(org)
    changed = copy.deepcopy(body)
    changed[field] = value
    response = client.post(BASE, json=changed)
    assert response.status_code == expected, response.text
    assert "private-source-value" not in response.text
    assert _snapshot(org) == before


@pytest.mark.parametrize("role,value", [
    ("product-lead", {"kind": "agent", "principal": "absent", "team": "product"}),
    ("implementer", {"kind": "agent", "principal": "product_lead", "team": "product"}),
    ("tester", {"kind": "agent", "principal": "qa_engineer", "team": "foreign"}),
    ("founder", {"kind": "agent", "principal": "founder", "team": None}),
], ids=["missing-author", "author-as-reviewer", "foreign-team", "forged-founder"])
def test_activation_author_binding_requires_current_distinct_same_org_principals(activation_org, role, value):
    client, org, state, body = activation_org
    before = _snapshot(org)
    changed = copy.deepcopy(body)
    changed["bindings"][role] = value
    response = client.post(BASE, json=changed)
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "role_binding_not_authorized"
    assert _snapshot(org) == before


def test_failed_event_write_rolls_back_real_task_and_every_domain_row(activation_org, monkeypatch):
    client, org, state, body = activation_org
    from runtime.workflows import activation
    before = _snapshot(org)
    original = activation.append_event_uncommitted

    def interrupt(conn, **kwargs):
        original(conn, **kwargs)
        raise RuntimeError("interruption after real event insertion")

    monkeypatch.setattr(activation, "append_event_uncommitted", interrupt)
    with pytest.raises(RuntimeError, match="interruption after real event insertion"):
        client.post(BASE, json=body)
    assert _snapshot(org) == before
    assert org.db.next_task_id() == "TASK-001"


def test_corrupt_historical_request_refuses_with_safe_code_and_no_repair(activation_org):
    client, org, state, body = activation_org
    response = client.post(BASE, json=body)
    assert response.status_code == 201, response.text
    receipt = response.json()
    # Adverse stored evidence is deliberately corrupted, never used to seed a
    # successful activation or manufacture a host/session/result identity.
    org.db.execute("UPDATE workflow_draft_dispatch_intents SET request_bytes=? WHERE id=?",
                   (b'{"private":"sensitive-source-bytes"}', receipt["intent_id"]))
    org.db._conn.commit()
    before = _snapshot(org)
    for response in (client.post(BASE, json=body), client.get(f"{BASE}/{receipt['activation_id']}")):
        assert response.status_code == 500, response.text
        assert response.json()["detail"]["code"] == "workflow_activation_storage_corrupt"
        assert "sensitive-source-bytes" not in response.text
        assert _snapshot(org) == before


def test_cold_org_reopen_and_new_template_preserve_original_activation(activation_org):
    from runtime.daemon.org_state import OrgState
    client, org, state, body = activation_org
    response = client.post(BASE, json=body)
    assert response.status_code == 201, response.text
    first = response.json()
    definition = copy.deepcopy(VALID_DEFINITION)
    definition["description"] += " Second immutable version."
    response = client.post("/api/v1/orgs/alpha/workflows/templates/publish", json={
        "operation_key": "publish-v2", "team_slug": "product", "template_name": "product-design",
        "expected_current_version": 1, "definition": definition,
    })
    assert response.status_code == 201 and response.json()["version"] == 2, response.text
    org.close()
    reopened = OrgState.load(slug="alpha", root=org.root, settings=org.settings)
    try:
        with state.profile_coordinator.dynamic_org_attachment(reopened):
            state.orgs["alpha"] = reopened
        before = _snapshot(reopened)
        response = client.post(BASE, json=body)
        assert response.status_code == 200, response.text
        for key in ("activation_id", "root_task_id", "intent_id", "context_digest", "template"):
            assert response.json()[key] == first[key]
        assert response.json()["template"]["version"] == 1
        assert _snapshot(reopened) == before
    finally:
        reopened.close()


def test_activation_reads_session_capacity_before_sqlite_writer(activation_org, monkeypatch):
    client, org, state, body = activation_org
    original = org.sessions.iter_active
    observations = []

    def observe_capacity():
        observations.append(org.db._conn.in_transaction)
        assert not org.db._conn.in_transaction, 'tracker read inverts callback binding-lease -> DB order'
        return original()

    monkeypatch.setattr(org.sessions, 'iter_active', observe_capacity)
    response = client.post(BASE, json=body)
    assert response.status_code == 201, response.text
    assert observations == [False, False]  # admission capture and separate receipt projection
    assert org.db.get_task(response.json()['root_task_id']).assigned_agent == 'product_lead'
    validate_workflow_schema(org.db._conn, expected_org_slug='alpha')


@pytest.mark.parametrize('record', [
    'template', 'authority', 'scope', 'bindings', 'bindings.product-lead',
    'eligible_replacements', 'eligible_replacements.implementer.0', 'inputs.0',
], ids=['template', 'authority', 'scope', 'role-map', 'role-binding',
        'replacement-map', 'replacement-binding', 'input-pin'])
def test_nested_activation_records_are_closed_before_discovery(activation_org, monkeypatch, record):
    from runtime.models import TaskRecord
    from runtime.infrastructure.task_attachment_store import TaskAttachmentStore
    from runtime.orchestrator._paths import OrgPaths
    import hashlib

    client, org, state, original = activation_org
    body = copy.deepcopy(original)
    source_id = org.db.next_task_id()
    org.db.insert_task(TaskRecord(id=source_id, assigned_agent='dev_agent', brief='input record owner'))
    content = b'canonical closed-record input'
    TaskAttachmentStore(OrgPaths(org.root).task_attachments_dir).put('source.txt', content)
    org.db.insert_task_attachment(task_id=source_id, ordinal=0, storage_key='source.txt', display_name='source.txt',
        size_bytes=len(content), content_type='text/plain', uploaded_by='founder')
    body['eligible_replacements']['implementer'] = [
        dict(kind='agent', principal='code_reviewer', team='engineering')]
    body['inputs'] = [dict(kind='task-attachment', task_id=source_id, storage_key='source.txt',
                          sha256=hashlib.sha256(content).hexdigest(), recipients=['product-lead'])]
    selected = body
    for member in record.split('.'):
        selected = selected[int(member)] if isinstance(selected, list) else selected[member]
    selected['private-unknown'] = 'private-source-value'
    discovered = []
    # Observe the actual post-parser boundary; no success is supplied here.
    original_capture = org.workflow_authority.capture_admission
    def capture():
        discovered.append(True)
        return original_capture()
    monkeypatch.setattr(org.workflow_authority, 'capture_admission', capture)
    before = _snapshot(org)
    response = client.post(BASE, json=body)
    assert response.status_code == 422, response.text
    assert response.json()['detail'] == {'code': 'workflow_activation_invalid_request'}
    assert discovered == [], 'unknown nested field reached authority discovery'
    assert 'private-source-value' not in response.text
    assert _snapshot(org) == before


@pytest.mark.parametrize('path,value', [
    ('template.version', True), ('template.version', '1'), ('template.version', 'latest'),
    ('authority.generation', True), ('authority.generation', '3'),
    ('expected_activation_revision', False), ('expected_activation_revision', '0'),
    ('scope.brief', '  '), ('allowed_actions', []),
    ('allowed_actions', ['draft-document', 'draft-document']),
    ('allowed_actions', ['collect-review']),
    ('template.definition_digest', 'A' * 64),
    ('bindings.product-lead.principal', 'org/foreign/product_lead'),
    ('inputs', [dict(kind='host-path', path='/private-source-value')]),
    ('inputs', [dict(kind='url', url='https://private-source-value.invalid/')]),
    ('inputs', [dict(kind='mutable-reference', reference='latest')]),
], ids=['boolean-version', 'string-version', 'latest-version', 'boolean-generation',
        'string-generation', 'boolean-zero-revision', 'string-zero-revision', 'blank-scope',
        'no-actions', 'duplicate-action', 'missing-draft-action', 'noncanonical-digest',
        'foreign-principal', 'host-path', 'url', 'mutable-only'])
def test_nested_activation_values_refuse_aliases_and_coercions_without_effects(
    activation_org, monkeypatch, path, value,
):
    client, org, state, original = activation_org
    body = copy.deepcopy(original)
    selected = body
    members = path.split('.')
    for member in members[:-1]:
        selected = selected[member]
    selected[members[-1]] = value
    discovered = []
    original_capture = org.workflow_authority.capture_admission
    def capture():
        discovered.append(True)
        return original_capture()
    monkeypatch.setattr(org.workflow_authority, 'capture_admission', capture)
    before = _snapshot(org)
    response = client.post(BASE, json=body)
    assert response.status_code == 422, response.text
    assert response.json()['detail'] == {'code': 'workflow_activation_invalid_request'}
    assert discovered == [], 'malformed request reached authority discovery'
    assert 'private-source-value' not in response.text
    assert _snapshot(org) == before


@pytest.mark.parametrize('credential', ['missing', 'invalid', 'session'])
def test_activation_http_human_boundary_precedes_corrupt_receipt_reads(activation_org, credential):
    client, org, state, body = activation_org
    admitted = client.post(BASE, json=body)
    assert admitted.status_code == 201, admitted.text
    receipt = admitted.json()
    org.db.execute('UPDATE workflow_draft_dispatch_intents SET request_bytes=? WHERE id=?',
                   (b'private-source-value', receipt['intent_id']))
    org.db._conn.commit()
    before = _snapshot(org)
    headers = {} if credential == 'missing' else {'Authorization': 'Bearer wrong-bearer'}
    foreign = TestClient(client.app, headers=headers)
    try:
        params = {'session_id': 'sess-forged'} if credential == 'session' else {}
        responses = [foreign.post(BASE, json=body, params=params),
                     foreign.get(BASE, params=params),
                     foreign.get(f"{BASE}/{receipt['activation_id']}", params=params)]
    finally:
        foreign.close()
    for response in responses:
        assert response.status_code == 403, response.text
        assert response.json()['detail']['code'] == 'human_only'
        assert 'private-source-value' not in response.text
    assert _snapshot(org) == before


def test_foreign_org_receipt_read_ignores_original_corruption_and_own_readiness(activation_org):
    client, org, state, body = activation_org
    admitted = client.post(BASE, json=body)
    assert admitted.status_code == 201, admitted.text
    receipt = admitted.json()
    response = client.post('/api/v1/orgs', json={'slug': 'beta'})
    assert response.status_code == 200, response.text
    beta = state.orgs['beta']
    try:
        org.db.execute('UPDATE workflow_draft_dispatch_intents SET request_bytes=? WHERE id=?',
                       (b'private-source-value', receipt['intent_id']))
        org.db._conn.commit()
        before, beta_before = _snapshot(org), _snapshot(beta)
        base = '/api/v1/orgs/beta/workflows/activations'
        response = client.get(f"{base}/{receipt['activation_id']}")
        assert response.status_code == 404, response.text
        assert response.json()['detail'] == {'code': 'workflow_activation_not_found'}
        assert receipt['root_task_id'] not in response.text
        assert 'private-source-value' not in response.text
        listed = client.get(base)
        assert listed.status_code == 200 and listed.json() == [], listed.text
        assert _snapshot(org) == before and _snapshot(beta) == beta_before
    finally:
        beta.close()


@pytest.mark.parametrize("row", ["proposal", "A", "Z"])
@pytest.mark.parametrize("mutation", ["missing-binding", "extra-binding", "missing-replacement", "extra-replacement",
    "wrong-kind", "human-replacement", "unknown-human", "duplicate-principal", "foreign-team", "wrong-family"])
def test_generic_slot_set_and_kind_refusals_leave_zero_admission_residue(generic_activation_org, row, mutation):
    client, org, state, cases = generic_activation_org
    body = copy.deepcopy(cases[row][0])
    if mutation == "missing-binding":
        body["bindings"].pop("sponsor")
    elif mutation == "extra-binding":
        body["bindings"]["phantom"] = {"kind": "agent", "principal": "code_reviewer", "team": "engineering"}
    elif mutation == "missing-replacement":
        body["eligible_replacements"].pop("sponsor")
    elif mutation == "extra-replacement":
        body["eligible_replacements"]["phantom"] = []
    elif mutation == "wrong-kind":
        body["bindings"]["sponsor"]["kind"] = "human" if row == "Z" else "agent"
    elif mutation == "human-replacement":
        # Z has no human slot: attempt an actual wrong-kind agent replacement.
        body["eligible_replacements"]["sponsor"] = [{"kind": "human", "principal": "founder", "team": None}]
    elif mutation == "unknown-human":
        body["bindings"]["sponsor"]["principal"] = "PRIVATE-person"
    elif mutation == "duplicate-principal":
        body["bindings"]["sponsor"] = copy.deepcopy(body["bindings"]["proposal-writer"])
    elif mutation == "foreign-team":
        body["bindings"]["proposal-writer"]["team"] = "foreign"
    else:
        body.pop("format")
    before = _snapshot(org)
    queue_before = state.queue._queue.qsize()
    response = client.post(BASE, json=body)
    structural = mutation in {"missing-binding", "missing-replacement", "wrong-family"}
    assert response.status_code == (422 if structural else 403), response.text
    assert response.json()["detail"]["code"] == ("workflow_activation_invalid_request" if structural else "role_binding_not_authorized")
    assert _snapshot(org) == before
    assert state.queue._queue.qsize() == queue_before
    assert "PRIVATE" not in response.text


def test_generic_approval_only_return_refusal_and_zero_human_valid_admission(generic_activation_org):
    client, org, state, cases = generic_activation_org
    body = copy.deepcopy(cases["A"][0])
    body["allowed_actions"].append("return-to-author")
    before = _snapshot(org)
    queue_before = state.queue._queue.qsize()
    refused = client.post(BASE, json=body)
    assert refused.status_code == 403 and refused.json()["detail"]["code"] == "role_binding_not_authorized"
    assert _snapshot(org) == before and state.queue._queue.qsize() == queue_before
    for row in ("A", "Z"):
        admitted = client.post(BASE, json=cases[row][0])
        assert admitted.status_code == 201, admitted.text
        receipt = admitted.json()
        assert receipt["bindings"] == cases[row][0]["bindings"]
        assert ("return-to-author" in receipt["allowed_actions"]) is (row == "Z")
        assert [value["principal"] for value in receipt["bindings"].values() if value["kind"] == "human"] == ([] if row == "Z" else ["founder"])
        task = org.db.get_task(receipt["root_task_id"])
        assert task.assigned_agent == "dev_agent" and task.team == "engineering"
        assert not org.db.execute("SELECT 1 FROM task_results").fetchone()

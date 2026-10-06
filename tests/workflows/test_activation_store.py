"""Complete canonical admission closure over genuine published org records."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import sqlite3
import asyncio
import threading
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from runtime.infrastructure.workflow_schema import validate_workflow_schema
from tests.daemon.test_workflow_activation_routes import BASE, _snapshot, activation_org


@pytest.mark.parametrize('boundary', [
    'before-begin', 'tasks', 'workflow_authorization_revisions', 'workflow_active_authorizations',
    'workflow_binding_snapshots', 'workflow_contexts', 'workflow_instances', 'workflow_activations',
    'workflow_active_activations', 'workflow_activation_operations', 'workflow_draft_dispatch_intents',
    'workflow_draft_dispatch_events', 'before-commit', 'after-commit',
])
def test_process_loss_at_actual_admission_boundary_retains_atomic_graph_and_original_replay(
    activation_org, tmp_path, boundary,
):
    from runtime.daemon.org_state import OrgState
    from runtime.workflows.templates import WorkflowTemplatePrincipal

    client, org, state, body = activation_org
    before = _snapshot(org)
    request_path = tmp_path / 'activation-request.json'
    request_path.write_text(json.dumps(body))
    # This disposable process uses production OrgState and activation. The
    # connection proxy only exits at an observed real SQLite boundary; it
    # supplies no successful row/receipt/authority or alternate store.
    source = '''
import asyncio,json,os,sys
from pathlib import Path
from runtime.config import Settings
from runtime.daemon.org_state import OrgState
from runtime.workflows.templates import WorkflowTemplatePrincipal
from runtime.workflows.profile_coordinator import ProfileCoordinator
from runtime.runtime import daemon_home
root, request_path, boundary = sys.argv[1:]
org = OrgState.load(root=Path(root), slug='alpha', settings=Settings())
coordinator=ProfileCoordinator(daemon_home=daemon_home(),orgs={'alpha':org})
with coordinator.dynamic_org_attachment(org): pass
real = org.db._conn
armed = False
class CrashBoundary:
    def __getattr__(self,name): return getattr(real,name)
    @property
    def isolation_level(self): return real.isolation_level
    @isolation_level.setter
    def isolation_level(self,value): real.isolation_level=value
    def execute(self,sql,*args):
        if armed and boundary == 'before-begin' and sql == 'BEGIN IMMEDIATE': os._exit(91)
        result=real.execute(sql,*args)
        if armed and sql.startswith('INSERT INTO '+boundary+' '): os._exit(91)
        return result
    def commit(self):
        domain=armed and real.in_transaction and real.execute('SELECT COUNT(*) FROM workflow_instances').fetchone()[0]
        if domain and boundary == 'before-commit': os._exit(91)
        real.commit()
        if domain and boundary == 'after-commit': os._exit(91)
org.db._conn=CrashBoundary()
principal=WorkflowTemplatePrincipal.founder(org_slug='alpha',team_slug='',revalidate=lambda:None)
armed=True
asyncio.run(org.workflow_activations.activate(principal=principal,request=json.loads(Path(request_path).read_text())))
raise AssertionError('requested admission crash boundary was not reached')
'''
    result = subprocess.run([sys.executable, '-c', source, str(org.root), str(request_path), boundary],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 91, (boundary, result.stdout, result.stderr)
    crashed = _snapshot(org)
    # A dead publisher's separate durable lease is honest recovery evidence,
    # distinct from the atomic task/domain graph. Cold load reclaims it using
    # its existing owner-PID contract; never edit or erase it in this test.
    if boundary != 'after-commit':
        assert {k: v for k, v in crashed.items() if k != 'workflow_publication_leases'} == {
            k: v for k, v in before.items() if k != 'workflow_publication_leases'}, 'process loss left task/domain residue at '+boundary
    else:
        assert len(crashed['tasks']) == 1 and len(crashed['workflow_draft_dispatch_intents']) == 1
        assert len(crashed['workflow_draft_dispatch_events']) == 1
        assert crashed['task_results'] == ()
    reopened = OrgState.load(root=org.root, slug=org.slug, settings=org.settings)
    try:
        with state.profile_coordinator.dynamic_org_attachment(reopened):
            pass
        principal = WorkflowTemplatePrincipal.founder(org_slug='alpha', team_slug='', revalidate=lambda: None)
        replay_before = _snapshot(reopened)
        receipt = asyncio.run(reopened.workflow_activations.activate(principal=principal, request=body))
        assert receipt['root_task_id'] == 'TASK-001' and receipt['execution_started'] is False
        assert receipt['replayed'] is (boundary == 'after-commit')
        if boundary == 'after-commit':
            assert _snapshot(reopened) == replay_before
            original = dict(reopened.db.execute('SELECT * FROM workflow_draft_dispatch_intents').fetchone())
            assert receipt['intent_id'] == original['id'] and receipt['root_task_id'] == original['task_id']
            assert original['session_id'] is None and original['final_result_id'] is None
        assert reopened.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 1
        assert reopened.db.execute('SELECT COUNT(*) FROM workflow_draft_dispatch_events').fetchone()[0] == 1
        validate_workflow_schema(reopened.db._conn, expected_org_slug='alpha')
        assert state.queue._queue.qsize() == 0
    finally:
        reopened.close()

def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf8")


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _identity(kind, *parts):
    return f"{kind}:{_sha(_bytes(list(parts)))}"


def _rewrite_canonical_document(org, receipt, target, field, value):
    """Adverse storage control: rehash/rekey the complete queued graph.

    The unchanged generic E validator must accept these coherent byte/digest
    relationships. Only the shipping activation owner can detect the semantic
    contradiction. This helper never constructs successful admission evidence.
    """
    conn = org.db._conn
    intent = dict(conn.execute("SELECT * FROM workflow_draft_dispatch_intents WHERE id=?",
                               (receipt["intent_id"],)).fetchone())
    binding = dict(conn.execute("SELECT * FROM workflow_binding_snapshots WHERE id=?",
                                (intent["binding_snapshot_id"],)).fetchone())
    auth = dict(conn.execute("SELECT * FROM workflow_authorization_revisions WHERE id=?",
                             (binding["authorization_revision_id"],)).fetchone())
    context = dict(conn.execute("SELECT * FROM workflow_contexts WHERE id=?",
                                (intent["context_id"],)).fetchone())
    documents = {"authorization": json.loads(auth["authority_bytes"]),
                 "binding": json.loads(binding["binding_bytes"]),
                 "context": json.loads(context["context_bytes"])}
    cursor = documents[target]
    parts = field.split(".")
    for part in parts[:-1]:
        cursor = cursor[part]
    cursor[parts[-1]] = value
    instance = receipt["instance_id"]
    auth_bytes = _bytes(documents["authorization"])
    auth_id = _identity("workflow-authorization", instance, 1, _sha(auth_bytes))
    documents["binding"]["authorization_revision_id"] = auth_id
    binding_bytes = _bytes(documents["binding"])
    binding_id = _identity("workflow-binding", instance, _sha(binding_bytes))
    if "authorization" in documents["context"]:
        documents["context"].update(authorization=documents["authorization"],
                                    authorization_revision_id=auth_id,
                                    binding=documents["binding"], binding_snapshot_id=binding_id)
    context_bytes = _bytes(documents["context"])
    context_id = _identity("workflow-context", instance, _sha(context_bytes))
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("PRAGMA defer_foreign_keys=ON")
        conn.execute("UPDATE workflow_authorization_revisions SET id=?,authority_bytes=?,authority_digest=? WHERE id=?",
                     (auth_id, auth_bytes, _sha(auth_bytes), auth["id"]))
        conn.execute("UPDATE workflow_active_authorizations SET authorization_revision_id=? WHERE authorization_revision_id=?",
                     (auth_id, auth["id"]))
        conn.execute("UPDATE workflow_binding_snapshots SET id=?,authorization_revision_id=?,binding_bytes=?,binding_digest=? WHERE id=?",
                     (binding_id, auth_id, binding_bytes, _sha(binding_bytes), binding["id"]))
        conn.execute("UPDATE workflow_contexts SET id=?,binding_snapshot_id=?,context_bytes=?,context_digest=? WHERE id=?",
                     (context_id, binding_id, context_bytes, _sha(context_bytes), context["id"]))
        conn.execute("UPDATE workflow_instances SET binding_snapshot_id=?,context_id=? WHERE id=?",
                     (binding_id, context_id, instance))
        conn.execute("UPDATE workflow_draft_dispatch_intents SET binding_snapshot_id=?,context_id=? WHERE id=?",
                     (binding_id, context_id, intent["id"]))
        event = conn.execute("SELECT * FROM workflow_draft_dispatch_events WHERE intent_id=?", (intent["id"],)).fetchone()
        payload = json.loads(event["event_bytes"])
        payload["intent"].update(binding_snapshot_id=binding_id, context_id=context_id)
        event_bytes = _bytes(payload)
        conn.execute("UPDATE workflow_draft_dispatch_events SET event_bytes=?,event_digest=? WHERE id=?",
                     (event_bytes, _sha(event_bytes), event["id"]))
        validate_workflow_schema(conn, expected_org_slug=org.slug)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


@pytest.mark.parametrize(("target", "field", "value"), [
    ("authorization", "format", "foreign-format"),
    ("authorization", "template.identity_id", "foreign-template"),
    ("authorization", "template.definition_digest", "0" * 64),
    ("authorization", "template.compiler_pin", "foreign-compiler"),
    ("authorization", "template.validator_pin", "foreign-validator"),
    ("authorization", "template.source_pin", "foreign-source"),
    ("authorization", "activation_revision", 2),
    ("authorization", "extra", "private-corrupt-member"),
    ("binding", "format", "foreign-format"),
    ("binding", "authority.namespace", "org/foreign"),
    ("binding", "activation_revision", 2),
    ("binding", "extra", "private-corrupt-member"),
    ("context", "format", "foreign-format"),
    ("context", "org_slug", "foreign"),
    ("context", "template.description", "private-corrupt-member"),
    ("context", "authority_snapshot.org_slug", "foreign"),
    ("context", "inputs", [{"private": "private-corrupt-member"}]),
    ("context", "extra", "private-corrupt-member"),
], ids=lambda item: str(item)[:60])
@pytest.mark.parametrize('activation_org', ['E', 'G'], indirect=True, ids=['existing-E', 'fresh-G'])
def test_historical_semantic_corruption_refuses_all_receipt_seams_without_repair(
    activation_org, target, field, value,
):
    client, org, state, body = activation_org
    admitted = client.post(BASE, json=body)
    assert admitted.status_code == 201, admitted.text
    receipt = admitted.json()
    _rewrite_canonical_document(org, receipt, target, field, value)
    before = _snapshot(org)
    for response in (client.post(BASE, json=body), client.get(BASE),
                     client.get(f"{BASE}/{receipt['activation_id']}")):
        assert response.status_code == 500, response.text
        assert response.json()["detail"] == {"code": "workflow_activation_storage_corrupt"}
        assert "private-corrupt-member" not in response.text
        assert _snapshot(org) == before


def test_admitted_context_freezes_server_identities_and_full_binding(activation_org):
    client, org, state, body = activation_org
    response = client.post(BASE, json=body)
    assert response.status_code == 201, response.text
    receipt = response.json()
    with org.db._lock:
        row = org.db._conn.execute("SELECT c.*,b.binding_bytes,b.authorization_revision_id,a.authority_bytes "
            "FROM workflow_contexts c JOIN workflow_binding_snapshots b ON b.id=c.binding_snapshot_id "
            "JOIN workflow_authorization_revisions a ON a.id=b.authorization_revision_id").fetchone()
        stored = json.loads(row["context_bytes"])
        grant = json.loads(row["authority_bytes"])
        bound = json.loads(row["binding_bytes"])
    assert stored.get("authorization") == grant
    assert stored.get("authorization_revision_id") == row["authorization_revision_id"]
    assert stored.get("binding") == bound
    assert stored.get("binding_snapshot_id") == row["binding_snapshot_id"]
    assert stored.get("intent_id") == receipt["intent_id"]
    assert stored.get("attempt_sequence") == stored.get("assignment_generation") == 1
    assert stored["authorization"]["root_task_id"] == receipt["root_task_id"]
    assert stored["authorization"]["actor"] == receipt["activated_by"]
    assert stored["authorization"]["created_at"] == receipt["created_at"]
    assert stored["authorization"]["template"] == receipt["template"]
    assert row["context_bytes"] == _bytes(stored)
    assert _sha(row["context_bytes"]) == receipt["context_digest"]


def _expected_context(body, content, snapshot, template, root_task_id, timestamp):
    """Independent wire/persistence oracle; no activation compiler is called."""
    instance = _identity("workflow-instance", "alpha", body["instance_id"])
    request_digest = _sha(_bytes(body))
    activation = _identity("workflow-activation", instance, 1, request_digest)
    pin = {key: template[key] for key in ("version", "definition_digest", "compiler_pin",
                                         "validator_pin", "source_pin")}
    pin.update(identity_id=body["template"]["identity_id"], version_id=template["id"])
    grant = dict(format="workflow-authorization@1", namespace=f"org/alpha/workflow-instance/{instance}",
        instance_id=instance, activation_id=activation, activation_revision=1, root_task_id=root_task_id,
        original_request=body, request_digest=request_digest,
        actor=dict(principal_kind="human", principal_id="founder", proof_kind="founder_bearer"),
        created_at=timestamp, template=pin, authority=body["authority"])
    grant_id = _identity("workflow-authorization", instance, 1, _sha(_bytes(grant)))
    bound = dict(format="workflow-binding@1", instance_id=instance, activation_id=activation,
        activation_revision=1, template_version_id=template["id"], authorization_revision_id=grant_id,
        bindings=body["bindings"], eligible_replacements=body["eligible_replacements"],
        scope=body["scope"], authority=body["authority"])
    binding_id = _identity("workflow-binding", instance, _sha(_bytes(bound)))
    intent_id = _sha(_bytes(dict(org_slug="alpha", instance_id=instance, activation_id=activation,
        activation_revision=1, attempt_sequence=1, predecessor_intent_id=None,
        admission_principal="human:founder", operation_key=body["operation_key"], request_digest=request_digest)))
    return dict(format="workflow-initial-draft-context@1", org_slug="alpha", request=body,
        authority_snapshot=snapshot, template=json.loads(template["definition_bytes"]),
        inputs=[dict(pin=body["inputs"][0], bytes_base64=base64.b64encode(content).decode("ascii"))],
        authorization=grant, authorization_revision_id=grant_id, binding=bound,
        binding_snapshot_id=binding_id, intent_id=intent_id, attempt_sequence=1, assignment_generation=1)


@pytest.mark.parametrize("delta", [-1, 0, 1], ids=["budget-minus-one", "exact-budget", "budget-plus-one"])
def test_total_canonical_context_budget_includes_actual_server_envelope(activation_org, monkeypatch, delta):
    from runtime.models import TaskRecord
    from runtime.infrastructure.task_attachment_store import TaskAttachmentStore
    from runtime.orchestrator._paths import OrgPaths
    from runtime.workflows import activation

    client, org, state, original = activation_org
    instant = datetime(2026, 10, 5, 12, 34, 56, 123456, tzinfo=timezone.utc)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant

    monkeypatch.setattr(activation, "datetime", FrozenDatetime)
    body = copy.deepcopy(original)
    source_id = org.db.next_task_id()
    org.db.insert_task(TaskRecord(id=source_id, assigned_agent="product_lead", team="product", brief="Source document"))
    root_id = org.db.next_task_id()
    body["scope"]["brief"] = "x"
    body["inputs"] = [dict(kind="task-attachment", task_id=source_id, storage_key="bounded-source.txt",
                           sha256=_sha(b""), recipients=["product-lead"])]
    snapshot = json.loads(org.workflow_authority.verify_admission_ready().snapshot_bytes)
    template = dict(org.db._conn.execute("SELECT * FROM workflow_template_versions WHERE version=1").fetchone())
    expected = _expected_context(body, b"", snapshot, template, root_id, instant.isoformat())
    target = 1024 * 1024 + delta
    remaining = target - len(_bytes(expected))
    padding = next(n for n in range(4) if (remaining - 3 * n) % 4 == 0)
    content = b"x" * ((remaining - 3 * padding) // 4 * 3)
    body["scope"]["brief"] += "x" * padding
    body["inputs"][0]["sha256"] = _sha(content)
    expected = _expected_context(body, content, snapshot, template, root_id, instant.isoformat())
    assert len(_bytes(expected)) == target
    store = TaskAttachmentStore(OrgPaths(org.root).task_attachments_dir)
    store.put("bounded-source.txt", content)
    org.db.insert_task_attachment(task_id=source_id, ordinal=0, storage_key="bounded-source.txt",
        display_name="bounded-source.txt", size_bytes=len(content), content_type="text/plain", uploaded_by="founder")
    before = _snapshot(org)
    response = client.post(BASE, json=body)
    if delta > 0:
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "workflow_activation_context_too_large"
        assert response.json()["detail"]["owner"] == "founder"
        assert _snapshot(org) == before
        assert org.db.next_task_id() == root_id
    else:
        assert response.status_code == 201, response.text
        stored = org.db._conn.execute("SELECT context_bytes FROM workflow_contexts").fetchone()[0]
        assert stored == _bytes(expected)
        assert len(stored) == target
        assert response.json()["root_task_id"] == root_id
        assert response.json()["context_digest"] == _sha(stored)
        # Historical replay neither re-reads mutable source files nor re-authorizes
        # current visibility: the authenticated frozen bytes own its receipt.
        store.put("bounded-source.txt", b"replaced source after admission")
        frozen_rows = _snapshot(org)
        replay = client.post(BASE, json=body)
        assert replay.status_code == 200, replay.text
        assert replay.json()["context_digest"] == response.json()["context_digest"]
        assert _snapshot(org) == frozen_rows


@pytest.mark.parametrize("ownership", ["ordinary", "ordinary-open-transaction", "uncommitted-commit", "uncommitted-rollback"])
def test_extracted_insert_preserves_all_twenty_fields_and_transaction_owner(activation_org, ownership):
    from runtime.models import TaskRecord
    client, org, state, body = activation_org
    stamp = datetime(2026, 10, 5, 12, 34, 56, tzinfo=timezone.utc)
    values = dict(id="supplied-id", status="in_progress", assigned_agent="product_lead", team="product",
        brief="exact supplied brief", revision_count=4, created_at=stamp, updated_at=stamp,
        completed_at=stamp, parent_task_id="parent-id", revisit_of_task_id="revisit-id",
        dispatched_from_thread_id="thread-id", block_kind="delegated", note="exact note",
        orchestration_step_count=7, session_timeout_seconds=123, task_type="subtask",
        active_fanout='{"retained":"opaque-existing-shape"}', current_session_id="existing-session",
        zombie_flagged_at=stamp)
    expected = {key: value.isoformat() if isinstance(value, datetime) else value for key, value in values.items()}
    task = TaskRecord(**values)
    conn = org.db._conn
    before_audit = tuple(conn.execute("SELECT * FROM audit_log"))
    with sqlite3.connect(f"file:{org.root / 'happyranch.db'}?mode=ro", uri=True) as reader:
        reader.row_factory = sqlite3.Row
        with org.db._lock:
            if ownership != "ordinary":
                conn.execute("BEGIN IMMEDIATE")
            if ownership.startswith("ordinary"):
                org.db.insert_task(task)
                assert not conn.in_transaction
            else:
                org.db._insert_task_uncommitted(task)
                assert conn.in_transaction
                assert reader.execute("SELECT * FROM tasks WHERE id=?", (task.id,)).fetchone() is None
                if ownership.endswith("rollback"):
                    conn.rollback()
                    assert reader.execute("SELECT * FROM tasks WHERE id=?", (task.id,)).fetchone() is None
                    assert tuple(conn.execute("SELECT * FROM audit_log")) == before_audit
                    return
                conn.commit()
        stored = dict(reader.execute("SELECT * FROM tasks WHERE id=?", (task.id,)).fetchone())
        assert {key: stored[key] for key in expected} == expected
        assert tuple(conn.execute("SELECT * FROM audit_log")) == before_audit
        # The unchanged supplied-ID wrapper propagates the INSERT error, does
        # not silently replace a row, and does not roll back its caller's work.
        with org.db._lock:
            conn.execute("BEGIN IMMEDIATE")
            with pytest.raises(sqlite3.IntegrityError):
                org.db.insert_task(task)
            assert conn.in_transaction
            assert dict(reader.execute("SELECT * FROM tasks WHERE id=?", (task.id,)).fetchone()) == stored
            conn.rollback()


def test_activation_allocates_with_existing_max_glob_semantics_and_gaps(activation_org):
    from runtime.models import TaskRecord
    client, org, state, body = activation_org
    for task_id in ("TASK-001", "TASK-007", "TASK-090tail", "TASK-X999", "foreign-999"):
        org.db.insert_task(TaskRecord(id=task_id, assigned_agent="dev_agent", brief=f"Ordinary {task_id}"))
    before = {task.id: task for task in org.db.list_tasks(limit=1000)}
    next_before_admission = org.db.next_task_id()
    response = client.post(BASE, json=body)
    assert response.status_code == 201, response.text
    assert response.json()["root_task_id"] == "TASK-091"
    assert next_before_admission == "TASK-091"
    for task_id, task in before.items():
        assert org.db.get_task(task_id) == task
    assert org.db.next_task_id() == "TASK-092"
    assert org.db._conn.execute("SELECT COUNT(*) FROM workflow_instances").fetchone()[0] == 1


def test_independent_activation_writers_contend_on_explicit_instance_and_atomic_root(activation_org, monkeypatch):
    from runtime.daemon.org_state import OrgState
    from runtime.workflows.activation import WorkflowActivationError
    from runtime.workflows.templates import WorkflowTemplatePrincipal

    client, first, state, body = activation_org
    second = OrgState.load(slug=first.slug, root=first.root, settings=first.settings)
    captured = threading.Barrier(2)
    inserted = threading.Event()
    release = threading.Event()
    committed = threading.Event()
    outcomes = {}
    errors = []
    threads = []
    original_insert = first.db._insert_task_uncommitted

    def paused_insert(task):
        original_insert(task)
        assert first.db._conn.in_transaction
        inserted.set()
        assert release.wait(5), "independent reader did not release first writer"

    monkeypatch.setattr(first.db, "_insert_task_uncommitted", paused_insert)
    original_second_writer = second.workflow_authority.admission_writer

    @contextmanager
    def second_after_first_commit(capture):
        assert committed.wait(5), "first writer did not finish"
        with original_second_writer(capture) as conn:
            yield conn

    monkeypatch.setattr(second.workflow_authority, "admission_writer", second_after_first_commit)

    def run(name, org, request):
        principal = WorkflowTemplatePrincipal.founder(org_slug=org.slug, team_slug="", revalidate=lambda: None)
        try:
            outcomes[name] = asyncio.run(org.workflow_activations.activate(principal=principal, request=request))
        except WorkflowActivationError as exc:
            outcomes[name] = exc.code
        except BaseException as exc:
            errors.append(exc)
        finally:
            if name == "first":
                committed.set()

    try:
        with state.profile_coordinator.dynamic_org_attachment(second):
            pass
        for org in (first, second):
            original_capture = org.workflow_authority.capture_admission

            def capture_together(original=original_capture):
                result = original()
                captured.wait(timeout=5)
                return result

            monkeypatch.setattr(org.workflow_authority, "capture_admission", capture_together)
        alternative = copy.deepcopy(body)
        alternative["operation_key"] = "another-key-same-explicit-instance"
        for name, org, request in (("first", first, body), ("second", second, alternative)):
            thread = threading.Thread(target=run, args=(name, org, request))
            threads.append(thread)
            thread.start()
        assert inserted.wait(5), errors
        with sqlite3.connect(f"file:{first.root / 'happyranch.db'}?mode=ro", uri=True) as reader:
            assert reader.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
            assert reader.execute("SELECT COUNT(*) FROM workflow_instances").fetchone()[0] == 0
            release.set()
            for thread in threads:
                thread.join(timeout=5)
                assert not thread.is_alive(), "activation contender leaked"
            assert not errors, errors
            assert outcomes["second"] == "workflow_activation_cas_stale"
            receipt = outcomes["first"]
            assert receipt["root_task_id"] == "TASK-001"
            assert reader.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
            assert reader.execute("SELECT COUNT(*) FROM workflow_instances").fetchone()[0] == 1
            assert reader.execute("SELECT COUNT(*) FROM workflow_activation_operations").fetchone()[0] == 1
            assert reader.execute("SELECT COUNT(*) FROM workflow_draft_dispatch_events").fetchone()[0] == 1
            assert first.db.next_task_id() == second.db.next_task_id() == "TASK-002"
            validate_workflow_schema(reader, expected_org_slug="alpha")
    finally:
        release.set()
        committed.set()
        captured.abort()
        for thread in threads:
            thread.join(timeout=5)
        assert all(not thread.is_alive() for thread in threads)
        second.close()


@pytest.mark.parametrize("recipient", ["product-lead", "implementer", "tester"])
def test_thread_private_source_requires_existing_recipient_visibility(activation_org, recipient):
    from runtime.models import ThreadRecord
    client, org, state, original = activation_org
    thread_id = org.db.next_thread_id()
    org.db.insert_thread(ThreadRecord(id=thread_id, subject="Private source"))
    upload = client.post(f"/api/v1/orgs/alpha/threads/{thread_id}/attachments",
        files={"file": ("private.txt", b"private workflow source", "text/plain")}, params={"agent": "founder"})
    assert upload.status_code == 200, upload.text
    body = copy.deepcopy(original)
    body["inputs"] = [dict(kind="thread-attachment", thread_id=thread_id,
        attachment_id=upload.json()["attachment_id"], sha256=_sha(b"private workflow source"), recipients=[recipient])]
    before = _snapshot(org)
    refusal = client.post(BASE, json=body)
    assert refusal.status_code == 422, refusal.text
    assert refusal.json()["detail"]["code"] == "workflow_activation_input_unavailable"
    assert refusal.json()["detail"]["owner"] == "founder"
    assert "private workflow source" not in refusal.text
    assert _snapshot(org) == before
    assert org.db.add_thread_participant(thread_id, body["bindings"][recipient]["principal"], added_by="founder")
    admitted = client.post(BASE, json=body)
    assert admitted.status_code == 201, admitted.text
    context = json.loads(org.db._conn.execute("SELECT context_bytes FROM workflow_contexts").fetchone()[0])
    assert context["inputs"] == [dict(pin=body["inputs"][0], bytes_base64=base64.b64encode(b"private workflow source").decode())]
    task = org.db.get_task(admitted.json()["root_task_id"])
    assert ("Authorized immutable input data" in task.brief) == (recipient == "product-lead")
    assert "private workflow source" not in admitted.text


@pytest.mark.parametrize("broken", ["missing-task", "missing-attachment", "digest-drift", "traversal-key"])
def test_task_input_refusal_never_creates_domain_rows_or_echoes_private_bytes(activation_org, broken):
    from runtime.models import TaskRecord
    from runtime.infrastructure.task_attachment_store import TaskAttachmentStore
    from runtime.orchestrator._paths import OrgPaths
    client, org, state, original = activation_org
    task_id = org.db.next_task_id()
    org.db.insert_task(TaskRecord(id=task_id, assigned_agent="dev_agent", brief="source"))
    store = TaskAttachmentStore(OrgPaths(org.root).task_attachments_dir)
    content = b"private corruption source"
    store.put("input.txt", content)
    org.db.insert_task_attachment(task_id=task_id, ordinal=0, storage_key="input.txt", display_name="input.txt",
        size_bytes=len(content), content_type="text/plain", uploaded_by="founder")
    body = copy.deepcopy(original)
    body["inputs"] = [dict(kind="task-attachment", task_id=task_id, storage_key="input.txt",
                           sha256=_sha(content), recipients=["product-lead"])]
    pin = body["inputs"][0]
    if broken == "missing-task":
        pin["task_id"] = "TASK-foreign"
    elif broken == "missing-attachment":
        pin["storage_key"] = "missing.txt"
    elif broken == "digest-drift":
        pin["sha256"] = "0" * 64
    else:
        pin["storage_key"] = "../input.txt"
    before = _snapshot(org)
    refusal = client.post(BASE, json=body)
    assert refusal.status_code == 422, refusal.text
    assert refusal.json()["detail"]["code"] == "workflow_activation_input_unavailable"
    assert refusal.json()["detail"]["owner"] == "founder"
    assert "private corruption source" not in refusal.text
    assert _snapshot(org) == before


def test_activation_commit_failure_rolls_back_before_releasing_publication_lease(activation_org, monkeypatch):
    client, org, state, body = activation_org
    connection = org.db._conn
    before = _snapshot(org)
    failures = []

    class CommitFault:
        """External SQLite failure control; every SQL call uses the real DB."""
        def __getattr__(self, name):
            return getattr(connection, name)

        def commit(self):
            count = connection.execute("SELECT COUNT(*) FROM workflow_instances").fetchone()[0]
            if connection.in_transaction and count and not failures:
                failures.append("writer-commit")
                raise sqlite3.OperationalError("controlled commit failure")
            connection.commit()

    monkeypatch.setattr(org.db, "_conn", CommitFault())
    try:
        with pytest.raises(sqlite3.OperationalError):
            client.post(BASE, json=body)
        assert failures == ["writer-commit"]
        assert not connection.in_transaction, "failed commit left the actual writer transaction open"
        assert _snapshot(org) == before
        assert org.db.next_task_id() == "TASK-001"
    finally:
        connection.rollback()


@pytest.fixture
def activation_profile(activation_org):
    from runtime.orchestrator.runtime_executor_store import save_runtime_profile, remove_runtime_profile
    from tests.workflows.test_profile_coordinator import _registered_profile

    client, org, state, body = activation_org
    name = 's2-selected-profile'
    save_runtime_profile(name, {'workspace_adapter_id': 'pi',
                               'command_adapter_id': f'custom-adapter:{name}-adapter'})
    try:
        with _registered_profile(name):
            yield client, org, state, body, name
    finally:
        remove_runtime_profile(name)


def _select_activation_profile(client, org, body, executor):
    response = client.put('/api/v1/orgs/alpha/agents/dev_agent/executor', json={'executor': executor})
    assert response.status_code == 200, response.text
    ready = org.workflow_authority.verify_admission_ready()
    body['authority'] = dict(namespace=ready.namespace, generation=ready.generation,
                             snapshot_digest=ready.snapshot_digest)


@pytest.mark.parametrize('selection', ['empty-to-selected', 'selected-to-empty'])
def test_supported_target_change_after_capture_refuses_stale_admission_without_new_lease(
    activation_profile, monkeypatch, selection,
):
    from runtime.workflows.templates import WorkflowTemplatePrincipal
    client, org, state, body, name = activation_profile
    if selection == 'selected-to-empty':
        _select_activation_profile(client, org, body, name)
    captured = threading.Event()
    release = threading.Event()
    outcomes = []
    captures = []
    acquired = []
    original_capture = org.workflow_authority.capture_admission
    original_read = state.profile_coordinator.profile_read

    def pause_capture():
        value = original_capture()
        captures.append(value.profile_names)
        captured.set()
        assert release.wait(5), 'target-change barrier timed out'
        return value

    @contextmanager
    def observe_lease(profile):
        acquired.append(profile)
        with original_read(profile):
            yield

    monkeypatch.setattr(org.workflow_authority, 'capture_admission', pause_capture)
    monkeypatch.setattr(state.profile_coordinator, 'profile_read', observe_lease)
    principal = WorkflowTemplatePrincipal.founder(org_slug=org.slug, team_slug='', revalidate=lambda: None)

    def admit():
        try:
            outcomes.append(asyncio.run(org.workflow_activations.activate(principal=principal, request=copy.deepcopy(body))))
        except BaseException as exc:
            outcomes.append(exc)

    worker = threading.Thread(target=admit)
    worker.start()
    try:
        assert captured.wait(5)
        _select_activation_profile(client, org, body, name if selection == 'empty-to-selected' else 'claude')
        after_writer = _snapshot(org)
        acquired.clear()
        release.set()
        worker.join(5)
        assert not worker.is_alive()
        assert len(outcomes) == 1 and getattr(outcomes[0], 'code', None) == 'workflow_activation_authority_stale'
        assert acquired == list(captures[0]), 'new target acquired beneath stale org ownership'
        assert _snapshot(org) == after_writer
        assert not org.db.execute('SELECT 1 FROM workflow_instances').fetchone()
        assert not org.db.execute('SELECT 1 FROM workflow_publication_leases').fetchone()
    finally:
        release.set()
        worker.join(5)
        assert not worker.is_alive()
    monkeypatch.setattr(org.workflow_authority, 'capture_admission', original_capture)
    admitted = client.post(BASE, json=body)
    assert admitted.status_code == 201, admitted.text
    assert admitted.json()['authority'] == body['authority']
    validate_workflow_schema(org.db._conn, expected_org_slug=org.slug)


@pytest.mark.parametrize('identity', ['foreign-org', 'agent', 'other-human', 'wrong-proof', 'revoked'])
def test_service_receipt_principal_is_checked_before_any_stored_or_current_read(
    activation_org, monkeypatch, identity,
):
    from dataclasses import replace
    from runtime.workflows.activation import WorkflowActivationError
    from runtime.workflows.templates import WorkflowTemplatePrincipal

    client, org, state, body = activation_org
    response = client.post(BASE, json=body)
    assert response.status_code == 201, response.text
    receipt = response.json()
    org.db.execute('UPDATE workflow_draft_dispatch_intents SET request_bytes=? WHERE id=?',
                   (b'private-source-value', receipt['intent_id']))
    org.db._conn.commit()
    before = _snapshot(org)
    principal = WorkflowTemplatePrincipal.founder(org_slug='alpha', team_slug='', revalidate=lambda: None)
    if identity == 'foreign-org':
        principal = replace(principal, org_slug='beta')
    elif identity == 'agent':
        principal = WorkflowTemplatePrincipal.agent(org_slug='alpha', agent_name='product_lead',
            team_slug='product', task_id=receipt['root_task_id'], session_id='sess-forged', revalidate=lambda: None)
    elif identity == 'other-human':
        principal = replace(principal, principal_id='another-human')
    elif identity == 'wrong-proof':
        principal = replace(principal, proof_kind='task_session')
    else:
        def revoked():
            raise WorkflowActivationError('role_binding_not_authorized')
        principal = replace(principal, _revalidate=revoked)
    queries = []
    org.db._conn.set_trace_callback(queries.append)
    try:
        for read in ('activate', 'get', 'list'):
            with pytest.raises(WorkflowActivationError) as refusal:
                if read == 'activate':
                    asyncio.run(org.workflow_activations.activate(principal=principal, request=body))
                elif read == 'get':
                    org.workflow_activations.get(principal=principal, activation_id=receipt['activation_id'])
                else:
                    org.workflow_activations.list(principal=principal)
            assert refusal.value.code == 'role_binding_not_authorized'
            assert queries == [], 'unauthorized principal reached stored receipt before refusal'
    finally:
        org.db._conn.set_trace_callback(None)
    assert _snapshot(org) == before


@pytest.mark.parametrize('source_change', ['remove-bytes', 'replace-bytes', 'remove-record'])
def test_original_input_snapshot_replays_after_mutable_source_disappears(
    activation_org, monkeypatch, source_change,
):
    from runtime.models import TaskRecord
    from runtime.infrastructure.task_attachment_store import TaskAttachmentStore
    from runtime.orchestrator._paths import OrgPaths
    from runtime.daemon.org_state import OrgState

    client, org, state, original = activation_org
    source_id = org.db.next_task_id()
    org.db.insert_task(TaskRecord(id=source_id, assigned_agent='dev_agent', brief='source owner'))
    store = TaskAttachmentStore(OrgPaths(org.root).task_attachments_dir)
    content = b'private immutable initial draft input'
    store.put('pinned-source.txt', content)
    org.db.insert_task_attachment(task_id=source_id, ordinal=0, storage_key='pinned-source.txt',
        display_name='pinned-source.txt', size_bytes=len(content), content_type='text/plain', uploaded_by='founder')
    body = copy.deepcopy(original)
    body['inputs'] = [dict(kind='task-attachment', task_id=source_id, storage_key='pinned-source.txt',
                          sha256=_sha(content), recipients=['product-lead'])]
    response = client.post(BASE, json=body)
    assert response.status_code == 201, response.text
    receipt = response.json()
    if source_change == 'remove-bytes':
        store.delete('pinned-source.txt')
    elif source_change == 'replace-bytes':
        store.put('pinned-source.txt', b'changed mutable bytes')
    else:
        org.db.execute('DELETE FROM task_attachments WHERE task_id=? AND storage_key=?',
                       (source_id, 'pinned-source.txt'))
        org.db._conn.commit()
    org.close()
    reopened = OrgState.load(slug='alpha', root=org.root, settings=org.settings)
    try:
        with state.profile_coordinator.dynamic_org_attachment(reopened):
            state.orgs['alpha'] = reopened
        # Observe the actual source owner, never supply substitute input bytes.
        # The retained context is checked independently below.
        source_reads = []
        original_inputs = reopened.workflow_activations._inputs
        def observe_inputs(*args, **kwargs):
            source_reads.append(True)
            return original_inputs(*args, **kwargs)
        def unexpected(*args, **kwargs):
            raise AssertionError('historical replay resolved new authority')
        monkeypatch.setattr(reopened.workflow_activations, '_inputs', observe_inputs)
        monkeypatch.setattr(reopened.workflow_authority, 'capture_admission', unexpected)
        before = _snapshot(reopened)
        for response in (client.post(BASE, json=body), client.get(BASE),
                         client.get(f"{BASE}/{receipt['activation_id']}")):
            assert response.status_code == 200, response.text
            observed = response.json()[0] if isinstance(response.json(), list) else response.json()
            for key in ('activation_id', 'root_task_id', 'intent_id', 'context_digest', 'created_at', 'original_request_digest'):
                assert observed[key] == receipt[key]
            assert 'private immutable initial draft input' not in response.text
        context = json.loads(reopened.db.execute('SELECT context_bytes FROM workflow_contexts').fetchone()[0])
        assert context['inputs'] == [dict(pin=body['inputs'][0], bytes_base64=base64.b64encode(content).decode())]
        assert _snapshot(reopened) == before
        assert source_reads == [], 'historical receipt reread mutable source bytes'
        assert len(reopened.db.list_tasks()) == 2
        validate_workflow_schema(reopened.db._conn, expected_org_slug='alpha')
    finally:
        reopened.close()


@pytest.mark.parametrize('first_owner', ['profile-writer', 'activation'])
def test_real_profile_operation_and_activation_serialize_both_orders(activation_profile, monkeypatch, first_owner):
    from runtime.workflows.profile_coordinator import ProfileCoordinator, ProfileCoordinatorError
    from runtime.workflows.templates import WorkflowTemplatePrincipal
    client, org, state, body, name = activation_profile
    _select_activation_profile(client, org, body, name)
    coordinator = state.profile_coordinator
    principal = WorkflowTemplatePrincipal.founder(org_slug=org.slug, team_slug='', revalidate=lambda: None)
    entered = threading.Event()
    release = threading.Event()
    outcomes = []
    original_insert = org.db._insert_task_uncommitted
    original_capture = org.workflow_authority.capture_admission
    capture_done = threading.Event()
    capture_release = threading.Event()
    admission_errors = []

    def captured_before_profile():
        capture = original_capture()
        capture_done.set()
        assert capture_release.wait(5), 'profile-first capture barrier timed out'
        return capture

    def captured_admission():
        try:
            admission_errors.append(asyncio.run(org.workflow_activations.activate(principal=principal, request=body)))
        except BaseException as exc:
            admission_errors.append(exc)

    admission_worker = None
    if first_owner == 'profile-writer':
        monkeypatch.setattr(org.workflow_authority, 'capture_admission', captured_before_profile)
        admission_worker = threading.Thread(target=captured_admission)
        admission_worker.start()
        assert capture_done.wait(5)

    def held_insert(task):
        original_insert(task)
        entered.set()
        assert release.wait(5), 'activation writer barrier timed out'

    def hold_first():
        try:
            if first_owner == 'profile-writer':
                with coordinator.operation([name], operation_kind='rebind', publisher='s2-race-profile-first'):
                    entered.set()
                    assert release.wait(5), 'profile writer barrier timed out'
            else:
                outcomes.append(asyncio.run(org.workflow_activations.activate(principal=principal, request=body)))
        except BaseException as exc:
            outcomes.append(exc)

    if first_owner == 'activation':
        monkeypatch.setattr(org.db, '_insert_task_uncommitted', held_insert)
    worker = threading.Thread(target=hold_first)
    worker.start()
    try:
        assert entered.wait(5)
        # Independently opened coordinator uses the same real flock; it cannot
        # acquire the selected profile beneath either owner's reservation.
        contender = ProfileCoordinator(daemon_home=coordinator._daemon_home, orgs={'alpha': org})
        with pytest.raises(ProfileCoordinatorError, match='profile_coordinator_busy'):
            with contender.profile_read(name):
                pytest.fail('selected profile lease escaped first owner')
        with sqlite3.connect(f"file:{org.root / 'happyranch.db'}?mode=ro", uri=True) as reader:
            assert reader.execute('SELECT COUNT(*) FROM workflow_instances').fetchone()[0] == 0
            assert reader.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0
        if first_owner == 'profile-writer':
            capture_release.set()
            admission_worker.join(5)
            assert not admission_worker.is_alive()
            assert len(admission_errors) == 1
            assert getattr(admission_errors[0], 'code', None) == 'profile_coordinator_busy', admission_errors
        else:
            with pytest.raises(ProfileCoordinatorError, match='profile_coordinator_busy'):
                with coordinator.operation([name], operation_kind='rebind', publisher='s2-race-loser'):
                    pytest.fail('profile operation entered while activation owned selected lease')
        release.set()
        worker.join(5)
        assert not worker.is_alive()
        assert not [value for value in outcomes if isinstance(value, BaseException)], outcomes
    finally:
        capture_release.set()
        release.set()
        worker.join(5)
        assert not worker.is_alive()
        if admission_worker is not None:
            admission_worker.join(5)
            assert not admission_worker.is_alive()
    if first_owner == 'profile-writer':
        monkeypatch.setattr(org.workflow_authority, 'capture_admission', original_capture)
        ready = org.workflow_authority.verify_admission_ready()
        body['authority'] = dict(namespace=ready.namespace, generation=ready.generation, snapshot_digest=ready.snapshot_digest)
        admitted = client.post(BASE, json=body)
        assert admitted.status_code == 201, admitted.text
        original = admitted.json()
    else:
        original = outcomes[0]
        with coordinator.operation([name], operation_kind='rebind', publisher='s2-race-after-admission'):
            pass
    before_replay = _snapshot(org)
    replay = client.post(BASE, json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json()['root_task_id'] == original['root_task_id']
    assert replay.json()['authority'] == original['authority']
    assert _snapshot(org) == before_replay
    assert len(org.db.list_tasks(limit=100)) == 1
    validate_workflow_schema(org.db._conn, expected_org_slug=org.slug)


def test_activation_discovery_and_notification_are_outside_durable_ownership(activation_profile, monkeypatch):
    from pathlib import Path
    from runtime.workflows.profile_coordinator import ProfileCoordinator
    client, org, state, body, name = activation_profile
    _select_activation_profile(client, org, body, name)
    observations = []
    real_read = Path.read_bytes
    real_inputs = org.workflow_activations._inputs
    real_capture = org.workflow_authority.capture_canonical_snapshot
    real_digest = state.profile_coordinator.profile_digest
    real_enqueue = state.queue.enqueue
    contender = ProfileCoordinator(daemon_home=state.profile_coordinator._daemon_home, orgs={'alpha': org})

    def outside(label):
        assert not org.db._conn.in_transaction, f'{label} ran inside SQLite transaction'
        assert not org.db.execute('SELECT 1 FROM workflow_publication_leases').fetchone(), f'{label} held org publication lease'
        with contender.profile_read(name):
            observations.append(label)

    def read_bytes(path, *args, **kwargs):
        outside('file-bytes')
        return real_read(path, *args, **kwargs)

    def capture():
        outside('canonical-discovery')
        return real_capture()

    def global_digest(profile):
        outside('effective-profile')
        return real_digest(profile)

    def inputs(*args):
        outside('input-resolution')
        return real_inputs(*args)

    def notify(*args, **kwargs):
        outside('notification')
        with sqlite3.connect(f"file:{org.root / 'happyranch.db'}?mode=ro", uri=True) as reader:
            assert reader.execute('SELECT COUNT(*) FROM workflow_instances').fetchone()[0] == 1
            assert reader.execute('SELECT COUNT(*) FROM workflow_draft_dispatch_events').fetchone()[0] == 1
        return real_enqueue(*args, **kwargs)

    monkeypatch.setattr(Path, 'read_bytes', read_bytes)
    monkeypatch.setattr(org.workflow_authority, 'capture_canonical_snapshot', capture)
    monkeypatch.setattr(state.profile_coordinator, 'profile_digest', global_digest)
    monkeypatch.setattr(org.workflow_activations, '_inputs', inputs)
    monkeypatch.setattr(state.queue, 'enqueue', notify)
    response = client.post(BASE, json=body)
    assert response.status_code == 201, response.text
    assert {'file-bytes', 'canonical-discovery', 'effective-profile', 'input-resolution', 'notification'} <= set(observations)
    assert observations[-1] == 'notification'
    validate_workflow_schema(org.db._conn, expected_org_slug=org.slug)


@pytest.mark.parametrize('first_owner', ['disable', 'activation'])
def test_actual_disable_and_admission_writer_serialize_without_partial_graph(activation_org, monkeypatch, first_owner):
    from runtime.workflows.cutover import WorkflowCutoverStore
    from runtime.workflows.templates import WorkflowTemplatePrincipal
    client, org, state, body = activation_org
    store = WorkflowCutoverStore(org.db, org_slug=org.slug)
    principal = WorkflowTemplatePrincipal.founder(org_slug=org.slug, team_slug='', revalidate=lambda: None)
    entered = threading.Event()
    release = threading.Event()
    second_entered = threading.Event()
    observations = {}
    errors = []
    original_insert = org.db._insert_task_uncommitted
    original_advance = store._advance
    original_transaction = store._transaction
    original_capture = org.workflow_authority.capture_admission

    def hold_insert(task):
        original_insert(task)
        entered.set()
        assert release.wait(5), 'activation/disable barrier timed out'

    def hold_disable(conn, marker, events, **kwargs):
        original_advance(conn, marker, events, **kwargs)
        if marker['generation'] == 4:
            entered.set()
            assert release.wait(5), 'disable/activation barrier timed out'

    @contextmanager
    def observe_disable_transaction(**kwargs):
        second_entered.set()
        with original_transaction(**kwargs) as conn:
            yield conn

    def observe_capture():
        second_entered.set()
        return original_capture()

    def admit():
        try:
            observations['activation'] = asyncio.run(org.workflow_activations.activate(principal=principal, request=body))
        except BaseException as exc:
            observations['activation'] = exc

    def disable():
        try:
            observations['disable'] = store.request(action='disable', operation_key='writer-disable', expected_generation=4)
        except BaseException as exc:
            errors.append(exc)

    if first_owner == 'activation':
        monkeypatch.setattr(org.db, '_insert_task_uncommitted', hold_insert)
        monkeypatch.setattr(store, '_transaction', observe_disable_transaction)
        first, second = threading.Thread(target=admit), threading.Thread(target=disable)
    else:
        monkeypatch.setattr(store, '_advance', hold_disable)
        monkeypatch.setattr(org.workflow_authority, 'capture_admission', observe_capture)
        first, second = threading.Thread(target=disable), threading.Thread(target=admit)
    real_lock = org.db._lock

    class ObservedLock:
        def acquire(self, *args, **kwargs):
            if threading.current_thread() is second:
                second_entered.set()
            return real_lock.acquire(*args, **kwargs)

        def release(self):
            return real_lock.release()

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, *args):
            self.release()

        def __getattr__(self, name):
            return getattr(real_lock, name)

    monkeypatch.setattr(org.db, '_lock', ObservedLock())
    first.start()
    try:
        assert entered.wait(5)
        second.start()
        assert second_entered.wait(5)
        with sqlite3.connect(f"file:{org.root / 'happyranch.db'}?mode=ro", uri=True) as reader:
            assert reader.execute('SELECT state FROM workflow_cutover_state').fetchone()[0] == 'enabled'
            for table in ('tasks', 'workflow_instances', 'workflow_draft_dispatch_intents', 'workflow_draft_dispatch_events'):
                assert reader.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
        release.set()
        first.join(5)
        second.join(5)
        assert not first.is_alive() and not second.is_alive()
        assert errors == [], errors
    finally:
        release.set()
        first.join(5)
        if second.ident is not None:
            second.join(5)
        assert not first.is_alive() and not second.is_alive()
    assert observations['disable']['state'] in {'disable_requested', 'draining', 'drained'}
    if first_owner == 'disable':
        assert getattr(observations['activation'], 'code', None) == 'workflow_new_runs_disabled'
        assert not org.db.execute('SELECT 1 FROM tasks').fetchone()
        assert not org.db.execute('SELECT 1 FROM workflow_instances').fetchone()
    else:
        receipt = observations['activation']
        assert isinstance(receipt, dict), receipt
        task = org.db.get_task(receipt['root_task_id'])
        assert task.status.value == 'pending' and task.cancelled_at is None
        before = _snapshot(org)
        replay = client.post(BASE, json=body)
        assert replay.status_code == 200, replay.text
        assert replay.json()['root_task_id'] == receipt['root_task_id']
        assert 'workflow_new_runs_disabled' in replay.json()['current_eligibility']['blockers']
        assert _snapshot(org) == before
    assert not org.db.execute('SELECT 1 FROM workflow_publication_leases').fetchone()


@pytest.mark.parametrize('first_owner', ['canonical-writer', 'activation'])
def test_real_canonical_org_writer_and_activation_serialize_both_orders(
    activation_org, monkeypatch, first_owner,
):
    import httpx
    from contextvars import ContextVar
    from runtime.orchestrator import prompt_loader
    from runtime.orchestrator._paths import OrgPaths
    from runtime.workflows.templates import WorkflowTemplatePrincipal
    from runtime.workflows.activation import WorkflowActivationError
    from runtime.workflows.authority import WorkflowAuthorityError

    client, org, state, body = activation_org
    original = org.workflow_authority._async_writer_lock
    principal = WorkflowTemplatePrincipal.founder(org_slug='alpha', team_slug='', revalidate=lambda: None)
    old_generation = body['authority']['generation']

    async def schedule():
        first_entered, second_entered, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        owner = ContextVar('s2_observed_writer')
        order = []
        tasks = []

        class ObservedAsyncGate:
            async def __aenter__(self):
                name = owner.get()
                order.append(('request', name))
                if name != first_owner:
                    second_entered.set()
                await original.acquire()
                order.append(('owned', name))
                if name == first_owner and not first_entered.is_set():
                    first_entered.set()
                    try:
                        await asyncio.wait_for(release.wait(), 5)
                    except BaseException:
                        original.release()
                        raise
                return self

            async def __aexit__(self, *args):
                original.release()

            def locked(self):
                return original.locked()

        monkeypatch.setattr(org.workflow_authority, '_async_writer_lock', ObservedAsyncGate())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app),
                                     base_url='http://testserver', headers=client.headers) as transport:
            async def writer():
                owner.set('canonical-writer')
                return await transport.post('/api/v1/orgs/alpha/agents', json={
                    'name': 'later_writer', 'role': 'worker', 'executor': 'claude', 'team': 'engineering',
                    'description': 'real canonical writer contender', 'system_prompt': 'bounded document work'})

            async def activation():
                owner.set('activation')
                try:
                    return await org.workflow_activations.activate(principal=principal, request=copy.deepcopy(body))
                except (WorkflowActivationError, WorkflowAuthorityError) as exc:
                    return exc

            first = writer if first_owner == 'canonical-writer' else activation
            second = activation if first_owner == 'canonical-writer' else writer
            try:
                tasks.append(asyncio.create_task(first(), name=first_owner))
                await asyncio.wait_for(first_entered.wait(), 5)
                other = 'activation' if first_owner == 'canonical-writer' else 'canonical-writer'
                tasks.append(asyncio.create_task(second(), name=other))
                await asyncio.wait_for(second_entered.wait(), 5)
                assert not org.db._conn.in_transaction
                with sqlite3.connect(f"file:{org.root / 'happyranch.db'}?mode=ro", uri=True) as reader:
                    for table in ('tasks', 'workflow_instances', 'workflow_draft_dispatch_intents',
                                  'workflow_draft_dispatch_events', 'workflow_activation_operations'):
                        assert reader.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
                    assert reader.execute('SELECT COUNT(*) FROM workflow_publication_leases').fetchone()[0] == 0
                release.set()
                results = await asyncio.wait_for(asyncio.gather(*tasks), 10)
                writer_result = results[0 if first_owner == 'canonical-writer' else 1]
                admitted = results[1 if first_owner == 'canonical-writer' else 0]
                assert writer_result.status_code == 200, writer_result.text
                assert prompt_loader.load_agent(OrgPaths(org.root), 'later_writer') is not None
                assert [name for action, name in order if action == 'owned'][:2] == [first_owner, other]
                ready = org.workflow_authority.verify_admission_ready()
                assert ready.generation > old_generation
                if first_owner == 'canonical-writer':
                    assert getattr(admitted, 'code', None) == 'workflow_activation_authority_stale', admitted
                    for table in ('tasks', 'workflow_instances', 'workflow_draft_dispatch_intents',
                                  'workflow_draft_dispatch_events', 'workflow_activation_operations'):
                        assert not org.db.execute(f'SELECT 1 FROM {table}').fetchone()
                else:
                    assert isinstance(admitted, dict), admitted
                    before = _snapshot(org)
                    owner.set('replay')
                    replay = await transport.post(BASE, json=body)
                    assert replay.status_code == 200, replay.text
                    for key in ('activation_id', 'root_task_id', 'intent_id', 'authority', 'created_at'):
                        assert replay.json()[key] == admitted[key]
                    assert replay.json()['current_eligibility'] == {
                        'eligible': False, 'blockers': ['workflow_activation_authority_stale']}
                    assert _snapshot(org) == before
                    assert len(org.db.list_tasks()) == 1
                assert not org.db.execute('SELECT 1 FROM workflow_publication_leases').fetchone()
                validate_workflow_schema(org.db._conn, expected_org_slug='alpha')
            finally:
                release.set()
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(schedule())


@pytest.mark.parametrize('consumer', ['admission', 'claim'])
@pytest.mark.parametrize('closure', [
    'cache', 'pointer-fence', 'journal-state', 'journal-bytes', 'journal-digest',
    'dependency-generation', 'dependency-state', 'store-generation', 'store-digest',
    'store-state', 'registry-generation', 'operation', 'captured-global-digest',
], ids=lambda value: value)
def test_admission_and_claim_revalidate_complete_captured_authority_profile_closure(
    activation_profile, monkeypatch, closure, consumer,
):
    from dataclasses import replace
    from runtime.workflows.templates import WorkflowTemplatePrincipal
    from runtime.workflows.authority import WorkflowAuthorityError
    from runtime.workflows.profile_coordinator import ProfileCoordinatorError

    client, org, state, body, name = activation_profile
    _select_activation_profile(client, org, body, name)
    # Create the complete real operation history through its shipping owner.
    with state.profile_coordinator.operation([name], operation_kind='rebind', publisher='s2-closure-baseline'):
        pass
    ready = org.workflow_authority.verify_admission_ready()
    body['authority'] = dict(namespace=ready.namespace, generation=ready.generation, snapshot_digest=ready.snapshot_digest)
    receipt = None
    if consumer == 'claim':
        response = client.post(BASE, json=body)
        assert response.status_code == 201, response.text
        receipt = response.json()
    original_capture = org.workflow_authority.capture_admission
    observations = []

    def corrupt_after_actual_capture():
        capture = original_capture()
        assert not org.db._conn.in_transaction
        assert not org.db.execute('SELECT 1 FROM workflow_publication_leases').fetchone()
        conn = org.db._conn
        journal_id = capture.pointer[1]
        if closure == 'cache':
            org.workflow_authority._cache[ready.namespace] = (ready.generation, '0' * 64)
        elif closure == 'pointer-fence':
            conn.execute('UPDATE workflow_authority_pointers SET profile_fence=profile_fence+1 WHERE namespace=?', (ready.namespace,))
        elif closure == 'journal-state':
            conn.execute("UPDATE workflow_publication_journals SET state='prepared' WHERE id=?", (journal_id,))
        elif closure == 'journal-bytes':
            conn.execute('UPDATE workflow_publication_journals SET snapshot_bytes=? WHERE id=?', (b'private-corrupt-snapshot', journal_id))
        elif closure == 'journal-digest':
            conn.execute('UPDATE workflow_publication_journals SET snapshot_digest=? WHERE id=?', ('0' * 64, journal_id))
        elif closure == 'dependency-generation':
            conn.execute('UPDATE workflow_profile_dependencies SET bound_generation=bound_generation+1 WHERE profile_name=?', (name,))
        elif closure == 'dependency-state':
            conn.execute("UPDATE workflow_profile_dependencies SET state='unbound' WHERE profile_name=?", (name,))
        elif closure == 'store-generation':
            conn.execute('UPDATE workflow_profile_store SET generation=generation+1 WHERE profile_name=?', (name,))
        elif closure == 'store-digest':
            conn.execute('UPDATE workflow_profile_store SET profile_digest=? WHERE profile_name=?', ('0' * 64, name))
        elif closure == 'store-state':
            conn.execute("UPDATE workflow_profile_store SET state='removed' WHERE profile_name=?", (name,))
        elif closure == 'registry-generation':
            conn.execute('UPDATE workflow_profile_registry SET published_generation=published_generation+1 WHERE profile_name=?', (name,))
        elif closure == 'operation':
            conn.execute("UPDATE workflow_profile_operations SET state='captured' WHERE id=(SELECT id FROM workflow_profile_operations WHERE profile_name=? ORDER BY rowid DESC LIMIT 1)", (name,))
        else:
            capture = replace(capture, profile_digests=((name, '0' * 64),))
        conn.commit()
        observations.append(_snapshot(org))
        return capture

    monkeypatch.setattr(org.workflow_authority, 'capture_admission', corrupt_after_actual_capture)
    principal = WorkflowTemplatePrincipal.founder(org_slug='alpha', team_slug='', revalidate=lambda: None)
    with pytest.raises((WorkflowAuthorityError, ProfileCoordinatorError)) as refusal:
        if consumer == 'admission':
            asyncio.run(org.workflow_activations.activate(principal=principal, request=body))
        else:
            asyncio.run(org.workflow_drafts.claim(receipt['root_task_id']))
    expected = ('authority_pointer_not_ready' if closure == 'cache' else
                'authority_pointer_journal_invalid' if closure in {'journal-bytes', 'journal-digest'} else
                'profile_operation_in_progress:captured' if closure == 'operation' else
                'workflow_activation_authority_stale')
    assert refusal.value.code == expected, refusal.value
    assert len(observations) == 1
    assert _snapshot(org) == observations[0], 'captured closure refusal left partial admission or claim rows'
    assert not org.db.execute('SELECT 1 FROM workflow_publication_leases').fetchone()
    if consumer == 'admission':
        assert not org.db.execute('SELECT 1 FROM tasks').fetchone()
        assert not org.db.execute('SELECT 1 FROM workflow_instances').fetchone()
    else:
        task = org.db.get_task(receipt['root_task_id'])
        assert task.status.value == 'pending' and task.current_session_id is None
        assert [row[0] for row in org.db.execute('SELECT event_kind FROM workflow_draft_dispatch_events')] == ['admitted']


@pytest.mark.parametrize('consumer', ['admission', 'receipt', 'claim'])
def test_running_work_hour_occupies_author_without_task_tracker_binding(activation_org, consumer):
    from runtime.models import WorkHourRecord, WorkHourMode, WorkHourStatus
    from runtime.workflows.draft_dispatch import DraftOwnershipError

    client, org, state, body = activation_org
    receipt = None
    if consumer != 'admission':
        response = client.post(BASE, json=body)
        assert response.status_code == 201, response.text
        receipt = response.json()
    hour_id = org.db.work_hours.next_id()
    org.db.work_hours.insert(WorkHourRecord(id=hour_id, agent_name='product_lead',
        local_date='2026-10-06', slot='04:30', mode=WorkHourMode.CONTINUOUS,
        scheduled_for=datetime(2026, 10, 5, 20, 30, tzinfo=timezone.utc)))
    org.db.work_hours.update(hour_id, status=WorkHourStatus.RUNNING,
                            started_at=datetime.now(timezone.utc))
    assert tuple(org.sessions.iter_active()) == ()
    before = _snapshot(org)
    if consumer == 'admission':
        response = client.post(BASE, json=body)
        assert response.status_code == 409, 'running work-hour admitted a second author task'
        assert response.json()['detail']['code'] == 'workflow_activation_author_pending'
        assert not org.db.execute('SELECT 1 FROM workflow_instances').fetchone()
    elif consumer == 'receipt':
        response = client.get(f"{BASE}/{receipt['activation_id']}")
        assert response.status_code == 200, response.text
        shown = response.json()
        assert shown['current_eligibility'] == {
            'eligible': False, 'blockers': ['workflow_activation_author_pending']}, shown
        assert shown['bindings'] == receipt['bindings']
        assert shown['pending'] and not shown['execution_started']
    else:
        with pytest.raises(DraftOwnershipError, match='workflow_activation_author_pending'):
            asyncio.run(org.workflow_drafts.claim(receipt['root_task_id']))
        assert org.db.get_task(receipt['root_task_id']).status.value == 'pending'
    assert _snapshot(org) == before, 'work-hour pending changed the admitted lane or authority'
    assert org.db.work_hours.get(hour_id).status == WorkHourStatus.RUNNING
    org.db.work_hours.update(hour_id, status=WorkHourStatus.COMPLETED,
                            ended_at=datetime.now(timezone.utc))
    if consumer == 'admission':
        response = client.post(BASE, json=body)
        assert response.status_code == 201, response.text
        receipt = response.json()
    else:
        assert client.get(f"{BASE}/{receipt['activation_id']}").json()['current_eligibility']['eligible']
    assert org.db.get_task(receipt['root_task_id']).assigned_agent == 'product_lead'
    validate_workflow_schema(org.db._conn, expected_org_slug='alpha')


@pytest.mark.parametrize('hour_agent,status', [
    ('dev_agent', 'running'), ('product_lead', 'pending'),
    ('product_lead', 'failed'), ('product_lead', 'timeout'),
])
def test_unrelated_or_inactive_work_hour_does_not_substitute_author(activation_org, hour_agent, status):
    from runtime.models import WorkHourRecord, WorkHourMode, WorkHourStatus
    client, org, state, body = activation_org
    hour_id = org.db.work_hours.next_id()
    org.db.work_hours.insert(WorkHourRecord(id=hour_id, agent_name=hour_agent,
        local_date='2026-10-06', slot='04:30', mode=WorkHourMode.CONTINUOUS,
        scheduled_for=datetime(2026, 10, 5, 20, 30, tzinfo=timezone.utc)))
    org.db.work_hours.update(hour_id, status=WorkHourStatus(status))
    before_hour = org.db.work_hours.get(hour_id)
    response = client.post(BASE, json=body)
    assert response.status_code == 201, response.text
    assert response.json()['current_eligibility'] == {'eligible': True, 'blockers': []}
    assert response.json()['bindings'] == body['bindings']
    assert org.db.work_hours.get(hour_id) == before_hour
    assert org.db.get_task(response.json()['root_task_id']).assigned_agent == 'product_lead'


@pytest.mark.parametrize('candidate', ['same-selected', 'other-selected', 'duplicate', 'unknown', 'wrong-team', 'human'])
def test_replacement_candidates_cannot_bypass_role_or_independence(activation_org, candidate):
    client, org, state, original = activation_org
    body = copy.deepcopy(original)
    replacement = {'kind': 'agent', 'principal': 'code_reviewer', 'team': 'engineering'}
    if candidate == 'same-selected':
        replacement = body['bindings']['implementer']
    elif candidate == 'other-selected':
        replacement = body['bindings']['tester']
    elif candidate == 'unknown':
        replacement['principal'] = 'unavailable_candidate'
    elif candidate == 'wrong-team':
        replacement['team'] = 'product'
    elif candidate == 'human':
        replacement = {'kind': 'human', 'principal': 'founder', 'team': None}
    body['eligible_replacements']['implementer'] = [replacement] * (2 if candidate == 'duplicate' else 1)
    before = _snapshot(org)
    response = client.post(BASE, json=body)
    assert response.status_code == 403, response.text
    assert response.json()['detail']['code'] == 'role_binding_not_authorized'
    assert _snapshot(org) == before


def test_eligible_replacements_are_frozen_inspectable_and_never_dispatched(activation_org):
    client, org, state, body = activation_org
    body['eligible_replacements']['product-lead'] = [
        {'kind': 'agent', 'principal': 'engineering_manager', 'team': 'engineering'}]
    body['eligible_replacements']['implementer'] = [
        {'kind': 'agent', 'principal': 'code_reviewer', 'team': 'engineering'}]
    response = client.post(BASE, json=body)
    assert response.status_code == 201, response.text
    receipt = response.json()
    before = _snapshot(org)
    for observed in (client.get(BASE).json()[0], client.get(f"{BASE}/{receipt['activation_id']}").json(),
                     client.post(BASE, json=body).json()):
        assert observed['eligible_replacements'] == body['eligible_replacements']
        assert observed['bindings'] == body['bindings']
        assert observed['root_task_id'] == receipt['root_task_id']
    assert _snapshot(org) == before
    assert len(org.db.list_tasks()) == 1
    assert org.db.get_task(receipt['root_task_id']).assigned_agent == 'product_lead'


@pytest.mark.parametrize('lifecycle', ['pending', 'terminated'])
@pytest.mark.parametrize('binding_kind', ['selected', 'replacement'])
def test_canonical_nonactive_agent_cannot_be_bound_or_selected_as_replacement(
    activation_org, lifecycle, binding_kind,
):
    from tests.workflows.test_profile_coordinator import _manager_request
    from runtime.orchestrator import prompt_loader
    from runtime.orchestrator._paths import OrgPaths

    client, org, state, body = activation_org
    if lifecycle == 'pending':
        response = _manager_request(client, org, 'enroll', 'candidate', executor='claude',
                                    description='pending candidate', system_prompt='bounded document work')
        assert response.status_code == 200 and response.json()['status'] == 'pending', response.text
        name = 'candidate'
        assert prompt_loader.load_pending_agent(OrgPaths(org.root), name) is not None
    else:
        name = 'candidate'
        created = client.post('/api/v1/orgs/alpha/agents', json={
            'name': name, 'role': 'worker', 'executor': 'claude', 'team': 'engineering',
            'description': 'terminated candidate', 'system_prompt': 'bounded document work'})
        assert created.status_code == 200, created.text
        response = _manager_request(client, org, 'terminate', name)
        assert response.status_code == 200, response.text
        assert prompt_loader.is_terminated(OrgPaths(org.root), name)
    ready = org.workflow_authority.verify_admission_ready()
    body['authority'] = dict(namespace=ready.namespace, generation=ready.generation,
                             snapshot_digest=ready.snapshot_digest)
    candidate = {'kind': 'agent', 'principal': name, 'team': 'engineering'}
    if binding_kind == 'selected':
        body['bindings']['implementer'] = candidate
    else:
        body['eligible_replacements']['implementer'] = [candidate]
    before = _snapshot(org)
    response = client.post(BASE, json=body)
    assert response.status_code == 403, response.text
    assert response.json()['detail']['code'] == 'role_binding_not_authorized'
    assert _snapshot(org) == before
    validate_workflow_schema(org.db._conn, expected_org_slug=org.slug)

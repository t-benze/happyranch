"""Inert U3b admission and U3c lifecycle, with no runtime caller attached.

The caller exclusively owns WorkflowAuthorityCoordinator.admission_writer(capture).
All discovery precedes that writer. These synchronous functions perform SQL and
pure validation only; they neither commit nor launch, notify or settle anything.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass

from runtime.infrastructure.database import Database
from runtime.infrastructure.db.tasks import RetryClaim
from runtime.infrastructure.workflow_task_ownership import adopt_sequential_source_uncommitted, selected_owner
from runtime.models import TaskRecord, TaskStatus
from runtime.workflows.authority import AuthorityAdmissionCapture, WorkflowAuthorityCoordinator, validate_authority_snapshot
from runtime.workflows.sequential_contracts import Limits, NextEdge, StartCommand, TerminalEdge
from runtime.workflows.sequential_definition import CompiledDefinition, parse_start, validate_start
from runtime.workflows.coding_authority_history import _bounded_rows
from runtime.workflows.sequential_history import _ReadBudget, _bytes, _native_rows, _parse, _require, _result, _sql_budget
from runtime.workflows.sequential_run_validation import _pins, _selected_layout, validate_selected_run_uncommitted


JSON_OUTPUT_LIMIT = 49152
DOCUMENT_OUTPUT_LIMIT = 1048576
EVIDENCE_CONTENT_LIMIT = 16384


class SequentialRefusal(ValueError):
    """Categorical refusal; callers propagate it out of their writer to roll back."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def check_output_size(contract: str, content: bytes) -> None:
    """Decision 1=B: inspect raw length before decoding, hashing or writing."""
    _check_output_length(contract, len(content))


def _check_output_length(contract: str, size: int) -> None:
    if size > (DOCUMENT_OUTPUT_LIMIT if contract == 'immutable-document@1' else JSON_OUTPUT_LIMIT):
        raise SequentialRefusal('sequential_output_limit_exceeded')


def check_evidence_size(raw: bytes) -> None:
    """Runtime-built evidence writer contract: raw length before any work."""
    if len(raw) > EVIDENCE_CONTENT_LIMIT:
        raise SequentialRefusal('sequential_evidence_limit_exceeded')


@dataclass(frozen=True)
class AdmissionIds:
    run_id: str
    carrier_task_id: str
    visit_id: str
    visit_task_id: str
    event_id: str
    outbox_id: str


@dataclass(frozen=True)
class _Admission:
    ids: AdmissionIds
    command: StartCommand
    start_bytes: bytes
    original_limits: Limits
    source: RetryClaim
    compiled: CompiledDefinition
    settings_id: str
    carrier_task: TaskRecord
    visit_task: TaskRecord
    budget: _ReadBudget


def _identity(kind: str, *parts: str) -> str:
    return 'seq-' + kind + '-' + hashlib.sha256(_bytes(parts)).hexdigest()


def _bound_agents(compiled: CompiledDefinition, command: StartCommand,
                  capture: AuthorityAdmissionCapture) -> dict[str, dict]:
    snapshot = json.loads(capture.ready.snapshot_bytes)
    validate_authority_snapshot(snapshot)
    agents = {agent['name']: agent for agent in snapshot['agents']}
    bindings = {item.role: item.principal for item in command.bindings}
    selected = {}
    for role, requirement in compiled.definition.roles:
        agent = agents.get(bindings[role])
        _require(agent is not None and agent['status'] == 'active' and bool(agent['executor']), 'role_unavailable')
        if requirement.eligibility == 'registered-team-manager':
            valid = agent['role'] == 'manager' and any(
                team['name'] == agent['team'] and team['manager'] == dict(kind='agent', principal=agent['name'])
                for team in snapshot['teams'])
        else:
            valid = agent['role'] == 'worker' and any(
                team['name'] == agent['team'] and agent['name'] in team['workers'] for team in snapshot['teams'])
        _require(valid, 'role_ineligible')
        selected[role] = agent
    return selected


def _prepare_admission(conn: sqlite3.Connection, db: Database, *, org_slug: str,
                       compiled: CompiledDefinition, settings_row: dict, start: bytes,
                       source: RetryClaim, carrier_task: TaskRecord, visit_task: TaskRecord,
                       original_limits: Limits, authority: WorkflowAuthorityCoordinator,
                       capture: AuthorityAdmissionCapture, command: StartCommand, budget: _ReadBudget) -> _Admission:
    # The entry point has already parsed exact canonical bytes before SQL;
    # it never normalizes an incoming command, including on replay.
    _require(conn is db._conn and conn.in_transaction and conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1,
             'admission_requires_owned_writer')
    _require(authority._db is db and authority._org_slug == org_slug
             and getattr(db, '_task_ownership_org', None) == org_slug, 'admission_owner_conflict')
    authority._validate_admission_uncommitted(conn, capture)
    _require(type(source.result_row_id) is int and source.result_row_id > 0
             and source.assigned_agent is not None and source.current_session_id is not None,
             'result_identity_invalid')
    with _sql_budget(conn, budget):
        metadata = _selected_layout(conn, budget)
        stored, setting, snapshot = _pins(conn, metadata, org_slug=org_slug,
            definition_id=settings_row['definition_id'], settings_id=settings_row['id'], budget=budget)
        _require(setting == settings_row and stored == compiled, 'admission_pin_conflict')
        validate_start(command, stored, original_limits=original_limits)
        _require(snapshot.admission == 'Allowed' and command.settings.generation == setting['generation']
                 and command.settings.snapshot_digest == setting['snapshot_digest']
                 and command.bindings == snapshot.bindings
                 and all(getattr(command.lower_limits, key) <= getattr(snapshot.limits, key)
                         for key in Limits.model_fields), 'admission_pin_conflict')
        bound = _bound_agents(stored, command, capture)
        _result(conn, source.result_row_id, source.task_id, source.assigned_agent, source.current_session_id, budget)
        node = next(node for node in stored.definition.nodes if node.id == stored.definition.entry_node)
        _require(carrier_task.id != visit_task.id and source.task_id not in (carrier_task.id, visit_task.id),
                 'native_identity_conflict')
        _require(carrier_task.parent_task_id == source.task_id
                 and carrier_task.assigned_agent == source.assigned_agent and carrier_task.team == source.team
                 and visit_task.parent_task_id == carrier_task.id
                 and visit_task.assigned_agent == bound[node.role]['name']
                 and visit_task.team == bound[node.role]['team'], 'native_task_conflict')
        for task in (carrier_task, visit_task):
            _require(task.status == TaskStatus.PENDING and task.current_session_id is None
                     and task.active_chain is None and task.active_fanout is None and task.block_kind is None
                     and task.blocked_on_job_ids is None and task.cancelled_at is None and task.completed_at is None
                     and task.executor_pid is None and task.revisit_of_task_id is None
                     and task.dispatched_from_thread_id is None and task.zombie_flagged_at is None,
                     'native_task_not_pristine')
        run_id = _identity('run', org_slug, source.assigned_agent, command.operation_key)
        ids = AdmissionIds(run_id, carrier_task.id, _identity('visit', run_id, '1'), visit_task.id,
                           _identity('event', run_id, '1'), _identity('outbox', run_id, '1'))
        budget.check()
        return _Admission(ids, command, start, original_limits, source, stored, setting['id'],
                          carrier_task, visit_task, budget)


def _replay(conn: sqlite3.Connection, *, org_slug: str, command: StartCommand, start: bytes,
            source: RetryClaim, original_limits: Limits, budget: _ReadBudget) -> AdmissionIds | None:
    # The immutable operation identity is looked up first. Prospective new task
    # IDs supplied by a redelivery never replace the original admission IDs.
    columns = ('id', 'start_bytes', 'source_task_id', 'source_principal', 'source_session_id',
               'source_result_id', 'carrier_task_id', 'original_limits_bytes', 'state', 'event_count')
    rows = list(_bounded_rows(conn, 'workflow_seq_runs', columns,
        (('text',), ('blob',), ('text',), ('text',), ('text',), ('integer',), ('text',), ('blob',),
         ('text',), ('integer',)), budget,
        where='org_slug=? AND source_principal=? AND start_operation_key=?',
        args=(org_slug, source.assigned_agent, command.operation_key), maximum=1))
    if not rows:
        return None
    row = rows[0][:len(columns)]
    budget.reserve(512 + 2 * sum(len(value.encode('utf8')) if type(value) is str else len(value)
                                for value in row if type(value) in (str, bytes)))
    _require(row[1:6] == (start, source.task_id, source.assigned_agent, source.current_session_id, source.result_row_id)
             and row[7] == _bytes(original_limits.model_dump()), 'start_operation_conflict')
    _require(row[8:] == ('active', 1), 'admission_replay_scope_unavailable')
    visits = list(_bounded_rows(conn, 'workflow_seq_visits', ('id', 'task_id'), (('text',), ('text',)), budget,
        where='run_id=? AND ordinal=?', args=(row[0], 1), maximum=1))
    events = list(_bounded_rows(conn, 'workflow_seq_events', ('id',), (('text',),), budget,
        where='run_id=? AND sequence=?', args=(row[0], 1), maximum=1))
    _require(len(visits) == len(events) == 1, 'admission_replay_incomplete')
    outbox = list(_bounded_rows(conn, 'workflow_seq_outbox', ('id',), (('text',),), budget,
        where='event_id=? AND kind=?', args=(events[0][0], 'visit_queued'), maximum=1))
    _require(len(outbox) == 1, 'admission_replay_incomplete')
    return AdmissionIds(row[0], row[6], visits[0][0], visits[0][1], events[0][0], outbox[0][0])


def admit_uncommitted(conn: sqlite3.Connection, db: Database, *, org_slug: str,
                       compiled: CompiledDefinition, settings_row: dict, start: bytes,
                       source: RetryClaim, carrier_task: TaskRecord, visit_task: TaskRecord,
                       original_limits: Limits, authority: WorkflowAuthorityCoordinator,
                       capture: AuthorityAdmissionCapture) -> AdmissionIds:
    """Admit and first-activate atomically in the caller's admission_writer.

    Supplied TaskRecords are prospective native rows, never authority assertions.
    Exact admission replay returns the original IDs; conflict or later lifecycle
    state refuses. Later-state replay/transition scopes belong to U3c.
    """
    budget = _ReadBudget()
    command = _parse(start, parse_start, budget)
    _require(conn is db._conn and conn.in_transaction and authority._db is db
             and authority._org_slug == org_slug and getattr(db, '_task_ownership_org', None) == org_slug,
             'admission_requires_owned_writer')
    authority._validate_admission_uncommitted(conn, capture)
    with _sql_budget(conn, budget):
        replay = _replay(conn, org_slug=org_slug, command=command, start=start, source=source,
                         original_limits=original_limits, budget=budget)
    if replay is not None:
        validate_selected_run_uncommitted(conn, org_slug=org_slug, run_id=replay.run_id, scope='admission')
        return replay
    admission = _prepare_admission(conn, db, org_slug=org_slug, compiled=compiled, settings_row=settings_row,
        start=start, source=source, carrier_task=carrier_task, visit_task=visit_task,
        original_limits=original_limits, authority=authority, capture=capture, command=command, budget=budget)
    _require(db._retry_claim_matches(source)
             and selected_owner(conn, source.task_id, org_slug) == ('legacy', None, None), 'source_not_legacy')
    for task in (carrier_task, visit_task):
        _require(not _native_rows(conn, 'tasks', ('id',), (('text',),), task.id, admission.budget),
                 'native_identity_conflict')
    # Preserve native move checks before entering a half-built cyclic graph.
    db._check_team_move_task_uncommitted(source.task_id)
    db._check_team_move_task_uncommitted(carrier_task)
    db._check_team_move_task_uncommitted(visit_task)
    admission.budget.check()
    conn.execute('PRAGMA defer_foreign_keys=ON')
    ids = admission.ids
    conn.execute('INSERT INTO workflow_seq_runs '
        '(id,org_slug,definition_id,settings_id,source_task_id,source_principal,source_session_id,source_result_id,'
        'carrier_task_id,start_operation_key,start_bytes,start_digest,original_limits_bytes,active_visit_id,state,'
        'epoch,content_returns,visit_count,event_count,cas,last_event_id) VALUES '
        '(?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,\'active\',1,0,1,1,0,?)',
        (ids.run_id, org_slug, settings_row['definition_id'], admission.settings_id, source.task_id,
         source.assigned_agent, source.current_session_id, source.result_row_id, carrier_task.id,
         admission.command.operation_key, start, hashlib.sha256(start).hexdigest(),
         _bytes(original_limits.model_dump()), ids.event_id))
    db._insert_task_row_uncommitted(carrier_task, ownership=('seq_carrier', ids.run_id))
    adopt_sequential_source_uncommitted(db, source.task_id, ids.run_id)
    return activate_uncommitted(conn, db, org_slug=org_slug, admission=admission)


def activate_uncommitted(conn: sqlite3.Connection, db: Database, *, org_slug: str,
                          admission: _Admission) -> AdmissionIds:
    """Complete only the first activation prepared by this admission writer.

    A provisional run is never a supported commit boundary. The caller must
    propagate every exception out of admission_writer to roll back the graph.
    """
    _require(conn is db._conn and conn.in_transaction, 'activation_requires_owned_writer')
    ids, command, task = admission.ids, admission.command, admission.visit_task
    provisional = conn.execute('SELECT active_visit_id,last_event_id,state,event_count FROM workflow_seq_runs '
        'WHERE id=? AND org_slug=?', (ids.run_id, org_slug)).fetchone()
    _require(provisional is not None and tuple(provisional) == (None, ids.event_id, 'active', 1),
             'first_activation_conflict')
    selection = _bytes(dict(outputs=[]))
    node = admission.compiled.definition.entry_node
    conn.execute('INSERT INTO workflow_seq_visits '
        '(id,run_id,node_id,ordinal,epoch,predecessor_event_id,task_id,principal,input_selection_bytes,'
        'input_selection_digest,state,selected) VALUES (?,?,?,1,1,NULL,?,?,?,?,\'queued\',1)',
        (ids.visit_id, ids.run_id, node, task.id, task.assigned_agent, selection, hashlib.sha256(selection).hexdigest()))
    db._insert_task_row_uncommitted(task, ownership=('seq_visit', ids.visit_id))
    event = _bytes(dict(format='workflow-sequential-storage-event@1', kind='admitted', run_id=ids.run_id,
                        sequence=1, operation_key=command.operation_key, prior_digest='0' * 64))
    conn.execute('INSERT INTO workflow_seq_events '
        '(id,run_id,sequence,operation_key,kind,prior_digest,event_bytes,event_digest) VALUES (?,?,1,?,\'admitted\',?,?,?)',
        (ids.event_id, ids.run_id, command.operation_key, '0' * 64, event, hashlib.sha256(event).hexdigest()))
    notification = _bytes(dict(format='workflow-sequential-notification-storage@1', notification_id=ids.outbox_id,
        event_id=ids.event_id, run_id=ids.run_id, visit_id=ids.visit_id, task_id=task.id, kind='visit_queued'))
    conn.execute('INSERT INTO workflow_seq_outbox '
        '(id,run_id,event_id,kind,visit_id,task_id,notification_bytes,notification_digest) VALUES (?,?,?,\'visit_queued\',?,?,?,?)',
        (ids.outbox_id, ids.run_id, ids.event_id, ids.visit_id, task.id, notification, hashlib.sha256(notification).hexdigest()))
    changed = conn.execute('UPDATE workflow_seq_runs SET active_visit_id=?,cas=0,event_count=1,last_event_id=?,'
        'epoch=1,content_returns=0,visit_count=1 WHERE id=? AND active_visit_id IS NULL',
        (ids.visit_id, ids.event_id, ids.run_id))
    _require(changed.rowcount == 1, 'first_activation_conflict')
    validate_selected_run_uncommitted(conn, org_slug=org_slug, run_id=ids.run_id, scope='admission')
    return ids


@dataclass(frozen=True)
class AcceptedIds:
    run_id: str
    visit_id: str
    result_id: int
    output_id: str | None


@dataclass(frozen=True)
class TransitionIds:
    run_id: str
    event_id: str
    visit_id: str | None
    visit_task_id: str | None
    outbox_id: str | None


def _engine_rows(conn: sqlite3.Connection, table: str, budget: _ReadBudget,
                 where: str, args: tuple, *, omit: tuple[str, ...] = ()) -> list[dict]:
    # Bounded native metadata, then pre-fetch typeof/length gating. The complete
    # independently constructed schema comparison remains the final validator.
    from runtime.infrastructure.workflow_schema import _structure_cells
    from runtime.workflows.sequential_run_validation import _selected_rows

    metadata = tuple(_structure_cells(conn, 'SELECT * FROM pragma_table_xinfo(?)',
        ('cid', 'name', 'type', 'notnull', 'dflt_value', 'pk', 'hidden'), ('cid',), budget, (table,)))
    columns = tuple(row[1] for row in metadata if row[1] not in omit)
    return list(_selected_rows(conn, table, metadata, budget, columns=columns, where=where, args=args))


def _engine_context(conn: sqlite3.Connection, db: Database, *, org_slug: str, run_id: str,
                    compiled: CompiledDefinition, authority: WorkflowAuthorityCoordinator,
                    capture: AuthorityAdmissionCapture, budget: _ReadBudget) -> dict:
    _require(conn is db._conn and conn.in_transaction and authority._db is db
             and authority._org_slug == org_slug and getattr(db, '_task_ownership_org', None) == org_slug
             and conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1, 'admission_requires_owned_writer')
    authority._validate_admission_uncommitted(conn, capture)
    rows = _engine_rows(conn, 'workflow_seq_runs', budget, 'id=?', (run_id,))
    _require(len(rows) == 1 and rows[0]['org_slug'] == org_slug, 'run_missing')
    run = rows[0]
    pins = _engine_rows(conn, 'workflow_seq_definitions', budget, 'id=?', (run['definition_id'],))
    _require(len(pins) == 1 and pins[0]['definition_bytes'] == compiled.raw
             and pins[0]['definition_digest'] == compiled.pin.definition_digest
             and pins[0]['source_digest'] == compiled.pin.source_digest, 'run_pin_invalid')
    return run


def _active_visit(conn: sqlite3.Connection, run: dict, visit_id: str, budget: _ReadBudget) -> dict:
    rows = _engine_rows(conn, 'workflow_seq_visits', budget, 'id=?', (visit_id,))
    _require(len(rows) == 1 and rows[0]['run_id'] == run['id'], 'visit_missing')
    return rows[0]


def bind_session_uncommitted(conn: sqlite3.Connection, db: Database, *, org_slug: str, run_id: str,
                             visit_id: str, session_id: str, compiled: CompiledDefinition,
                             authority: WorkflowAuthorityCoordinator, capture: AuthorityAdmissionCapture) -> str:
    """Bind only the session already owned by the native task; launch stays U3d."""
    budget = _ReadBudget()
    with _sql_budget(conn, budget):
        run = _engine_context(conn, db, org_slug=org_slug, run_id=run_id, compiled=compiled,
                              authority=authority, capture=capture, budget=budget)
        visit = _active_visit(conn, run, visit_id, budget)
        _require(type(session_id) is str and bool(session_id) and '\x00' not in session_id, 'identity_invalid')
        _require(run['state'] == 'active' and run['active_visit_id'] == visit_id, 'session_binding_conflict')
        if (visit['state'], visit['session_id']) != ('running', session_id):
            _require(visit['state'] == 'queued' and visit['session_id'] is None and visit['result_id'] is None,
                     'session_binding_conflict')
            native = _native_rows(conn, 'tasks', ('current_session_id',), (('text', 'null'),), visit['task_id'], budget)
            _require(native == [(session_id,)], 'native_session_conflict')
            changed = conn.execute("UPDATE workflow_seq_visits SET session_id=?,state='running' "
                "WHERE id=? AND state='queued' AND session_id IS NULL", (session_id, visit_id))
            _require(changed.rowcount == 1, 'session_binding_conflict')
    validate_selected_run_uncommitted(conn, org_slug=org_slug, run_id=run_id, scope='session')
    return visit_id


def accept_result_uncommitted(conn: sqlite3.Connection, db: Database, *, org_slug: str, run_id: str,
                              visit_id: str, task_id: str, principal: str, session_id: str, result_id: int,
                              envelope_bytes: bytes, verified_checks: tuple[str, ...], content: bytes,
                              contract: str, compiled: CompiledDefinition, recipients: tuple[str, ...],
                              authority: WorkflowAuthorityCoordinator,
                              capture: AuthorityAdmissionCapture) -> AcceptedIds:
    """Accept explicit content bytes supplied by the future trusted adapter.

    No summary carrier, native callback or settlement proof is invented here.
    Negative/return envelopes have no outputs and require empty content/recipients.
    """
    from runtime.workflows.sequential_definition import parse_result
    from runtime.workflows.sequential_reducer import AuthenticatedResult

    check_output_size(contract, content)
    budget = _ReadBudget()
    with _sql_budget(conn, budget):
        run = _engine_context(conn, db, org_slug=org_slug, run_id=run_id, compiled=compiled,
                              authority=authority, capture=capture, budget=budget)
        visit = _active_visit(conn, run, visit_id, budget)
        node = next(node for node in compiled.definition.nodes if node.id == visit['node_id'])
        _require(contract == node.output_contract.type, 'output_contract_conflict')
        check_output_size(node.output_contract.type, content)
        _result(conn, result_id, task_id, principal, session_id, budget)
        envelope = _parse(envelope_bytes, parse_result, budget)
        AuthenticatedResult(task_id=task_id, session_id=session_id, result_id=result_id,
                            envelope=envelope, verified_checks=verified_checks)
        _require((task_id, principal, session_id) == (visit['task_id'], visit['principal'], visit['session_id']),
                 'result_identity_conflict')
        checks = _bytes(dict(checks=list(verified_checks)))
        digest = hashlib.sha256(envelope_bytes).hexdigest()
        output_id = _identity('output', visit_id, node.output_contract.slot) if envelope.outputs else None
        ids = AcceptedIds(run_id, visit_id, result_id, output_id)
        if visit['result_id'] is not None:
            if (visit['result_id'], visit['result_bytes'], visit['result_digest'], visit['verified_checks_bytes']) != (
                    result_id, envelope_bytes, digest, checks):
                raise SequentialRefusal('sequential_result_conflict')
            stored = _engine_rows(conn, 'workflow_seq_outputs', budget, 'visit_id=?', (visit_id,))
            if ((not envelope.outputs and (content or recipients))
                    or len(stored) != len(envelope.outputs)
                    or (stored and (stored[0]['content_bytes'] != content or
                                    stored[0]['recipients_bytes'] != _bytes(dict(roles=list(recipients)))))):
                raise SequentialRefusal('sequential_result_conflict')
        else:
            _require(run['state'] == 'active' and run['active_visit_id'] == visit_id
                     and visit['state'] == 'running', 'accept_visit_conflict')
            _require(len(envelope.outputs) <= 1, 'output_contract_conflict')
            if not envelope.outputs:
                _require(not content and not recipients, 'unexpected_output_content')
            conn.execute('UPDATE workflow_seq_visits SET result_id=?,result_bytes=?,result_digest=?,verified_checks_bytes=? '
                         'WHERE id=? AND result_id IS NULL', (result_id, envelope_bytes, digest, checks, visit_id))
            if envelope.outputs:
                ref = envelope.outputs[0]
                conn.execute('INSERT INTO workflow_seq_outputs '
                    '(id,run_id,visit_id,slot,epoch,contract,task_id,session_id,result_id,content_bytes,content_digest,recipients_bytes) '
                    'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', (output_id, run_id, visit_id, ref.slot, ref.epoch, ref.contract,
                    task_id, session_id, result_id, content, hashlib.sha256(content).hexdigest(),
                    _bytes(dict(roles=list(recipients)))))
    validate_selected_run_uncommitted(conn, org_slug=org_slug, run_id=run_id, scope='accept', visit_id=visit_id)
    return ids


def _stored_event(conn: sqlite3.Connection, run_id: str, operation_key: str,
                   budget: _ReadBudget) -> dict | None:
    rows = _engine_rows(conn, 'workflow_seq_events', budget, 'run_id=? AND operation_key=?', (run_id, operation_key))
    return rows[0] if rows else None


def _move_run(conn: sqlite3.Connection, run: dict, event_id: str, *, state: str,
               active_visit_id: str | None, epoch: int, content_returns: int, visits: int) -> None:
    changed = conn.execute('UPDATE workflow_seq_runs SET state=?,active_visit_id=?,epoch=?,content_returns=?,'
        'visit_count=?,event_count=event_count+1,cas=cas+1,last_event_id=? WHERE id=? AND cas=? AND last_event_id=?',
        (state, active_visit_id, epoch, content_returns, visits, event_id, run['id'], run['cas'], run['last_event_id']))
    _require(changed.rowcount == 1, 'transition_cas_conflict')


def _event(conn: sqlite3.Connection, run: dict, operation_key: str, kind: str, prior: str,
            *, raw: bytes | None = None, source_visit: str | None = None, source_result: int | None = None) -> str:
    sequence = run['event_count'] + 1
    event_id = _identity('event', run['id'], str(sequence))
    if raw is None:
        raw = _bytes(dict(format='workflow-sequential-storage-event@1', kind=kind, run_id=run['id'],
            sequence=sequence, operation_key=operation_key, prior_digest=prior))
    conn.execute('INSERT INTO workflow_seq_events '
        '(id,run_id,sequence,operation_key,kind,prior_digest,event_bytes,event_digest,source_visit_id,source_result_id) '
        'VALUES (?,?,?,?,?,?,?,?,?,?)', (event_id, run['id'], sequence, operation_key, kind, prior, raw,
        hashlib.sha256(raw).hexdigest(), source_visit, source_result))
    return event_id


def _outbox(conn: sqlite3.Connection, run: dict, event_id: str, kind: str,
              visit_id: str | None, task_id: str) -> str:
    outbox_id = _identity('outbox', event_id, kind)
    raw = _bytes(dict(format='workflow-sequential-notification-storage@1', notification_id=outbox_id,
        event_id=event_id, run_id=run['id'], visit_id=visit_id, task_id=task_id, kind=kind))
    conn.execute('INSERT INTO workflow_seq_outbox '
        '(id,run_id,event_id,kind,visit_id,task_id,notification_bytes,notification_digest) VALUES (?,?,?,?,?,?,?,?)',
        (outbox_id, run['id'], event_id, kind, visit_id, task_id, raw, hashlib.sha256(raw).hexdigest()))
    return outbox_id


def transition_uncommitted(conn: sqlite3.Connection, db: Database, *, org_slug: str, run_id: str,
                            visit_id: str, result_id: int, operation_key: str, expected_cas: int,
                            successor_task: TaskRecord | None, compiled: CompiledDefinition,
                            authority: WorkflowAuthorityCoordinator,
                            capture: AuthorityAdmissionCapture) -> TransitionIds:
    from runtime.workflows.sequential_contracts import TransitionRecord
    from runtime.workflows.sequential_definition import parse_start, parse_transition
    from runtime.workflows.sequential_reducer import ActiveVisit, RunState, reduce_result
    from runtime.workflows.sequential_run_validation import _accepted, _metadata_outputs, _prior_state
    from runtime.infrastructure.workflow_schema import _structure_cells

    budget = _ReadBudget()
    with _sql_budget(conn, budget):
        run = _engine_context(conn, db, org_slug=org_slug, run_id=run_id, compiled=compiled,
                              authority=authority, capture=capture, budget=budget)
        replay = _stored_event(conn, run_id, operation_key, budget)
        if replay is not None:
            if replay['kind'] != 'transition':
                raise SequentialRefusal('sequential_operation_conflict')
            record = _parse(replay['event_bytes'], parse_transition, budget)
            if ((record.source_visit, record.source_result, record.prior_cas) != (visit_id, result_id, expected_cas)
                    or (successor_task is not None and
                        (record.successor is None or successor_task.id != record.successor.task_id))):
                raise SequentialRefusal('sequential_operation_conflict')
            successor = record.successor
            outbox = _engine_rows(conn, 'workflow_seq_outbox', budget, 'event_id=?', (replay['id'],))
            ids = TransitionIds(run_id, replay['id'], successor.visit_id if successor else None,
                                successor.task_id if successor else None, outbox[0]['id'] if outbox else None)
        else:
            _require(run['state'] == 'active' and run['active_visit_id'] == visit_id and run['cas'] == expected_cas,
                     'transition_cas_conflict')
            visits = _engine_rows(conn, 'workflow_seq_visits', budget, 'run_id=?', (run_id,),
                                  omit=('result_bytes', 'verified_checks_bytes'))
            events = _engine_rows(conn, 'workflow_seq_events', budget, 'run_id=?', (run_id,))
            visits.sort(key=lambda row: row['ordinal'])
            events.sort(key=lambda row: row['sequence'])
            source = next(row for row in visits if row['id'] == visit_id)
            _require(source['result_id'] == result_id, 'result_identity_conflict')
            # Metadata only. The full output payload was decoded in accept's
            # separate scope; no prior result envelopes are acquired here.
            metadata = {}
            for table in ('workflow_seq_visits', 'workflow_seq_outputs'):
                metadata[table] = tuple(_structure_cells(conn, 'SELECT * FROM pragma_table_xinfo(?)',
                    ('cid', 'name', 'type', 'notnull', 'dflt_value', 'pk', 'hidden'), ('cid',), budget, (table,)))
            start = _parse(run['start_bytes'], parse_start, budget)
            original = Limits.model_validate(json.loads(run['original_limits_bytes']))
            outputs = _metadata_outputs(conn, metadata, visits, budget)
            counters, selections = _prior_state(run, compiled, start, original, visits, events, outputs, budget)
            current = ActiveVisit(node=source['node_id'], visit_id=visit_id, task_id=source['task_id'],
                session_id=source['session_id'], epoch=source['epoch'], input_selection_digest=source['input_selection_digest'])
            accepted = _accepted(conn, metadata, run, source, compiled, budget, content=False)
            decision = reduce_result(compiled, RunState(run_id=run_id, definition_digest=compiled.pin.definition_digest,
                original_limits=original, lower_limits=start.lower_limits, counters=counters, active_visit=current,
                selections=selections, findings=(), receipts=()), accepted)
            successor = None
            visit_next = None
            event_id = _identity('event', run_id, str(run['event_count'] + 1))
            if isinstance(decision.edge, NextEdge):
                _require(successor_task is not None and decision.activation is not None, 'successor_required')
                node = next(node for node in compiled.definition.nodes if node.id == decision.edge.node)
                bound = _bound_agents(compiled, start, capture)
                task = successor_task
                _require(task.parent_task_id == run['carrier_task_id'] and task.assigned_agent == bound[node.role]['name']
                         and task.team == bound[node.role]['team'] and task.status == TaskStatus.PENDING
                         and task.current_session_id is None and task.active_chain is None and task.active_fanout is None
                         and task.block_kind is None and task.blocked_on_job_ids is None and task.cancelled_at is None
                         and task.completed_at is None and task.executor_pid is None and task.revisit_of_task_id is None
                         and task.dispatched_from_thread_id is None and task.zombie_flagged_at is None,
                         'native_task_not_pristine')
                _require(not _native_rows(conn, 'tasks', ('id',), (('text',),), task.id, budget), 'native_identity_conflict')
                db._check_team_move_task_uncommitted(task)
                visit_next = _identity('visit', run_id, str(decision.after.visits))
                successor = dict(node=node.id, visit_id=visit_next, task_id=task.id)
            raw = _bytes(dict(format='workflow-sequential-transition@1', run_id=run_id,
                definition_digest=compiled.pin.definition_digest, source_visit=visit_id, source_result=result_id,
                result_digest=decision.result_digest, output_digest=accepted.envelope.outputs[0].digest if accepted.envelope.outputs else None,
                node=source['node_id'], outcome=decision.outcome, prior_cas=expected_cas,
                prior_event_digest=events[-1]['event_digest'], before=decision.before.model_dump(),
                after=decision.after.model_dump(), invalidated=[ref.model_dump() for ref in decision.invalidated],
                edge=decision.edge.model_dump(), successor=successor))
            TransitionRecord.model_validate(json.loads(raw))
            budget.check()
            conn.execute('PRAGMA defer_foreign_keys=ON')
            state = ('active' if successor else 'completed' if isinstance(decision.edge, TerminalEdge) else
                     'failed' if decision.edge.action == 'fail' else 'held')
            conn.execute('UPDATE workflow_seq_visits SET state=?,selected=? WHERE id=? AND selected=1',
                ('completed' if successor else state, int(state == 'held'), visit_id))
            event_id = _event(conn, run, operation_key, 'transition', events[-1]['event_digest'], raw=raw,
                              source_visit=visit_id, source_result=result_id)
            outbox_id = None
            if successor is not None:
                selection = _bytes(dict(outputs=[ref.model_dump() for ref in decision.activation.inputs]))
                conn.execute('INSERT INTO workflow_seq_visits '
                    '(id,run_id,node_id,ordinal,epoch,predecessor_event_id,task_id,principal,input_selection_bytes,'
                    'input_selection_digest,state,selected) VALUES (?,?,?,?,?,?,?,?,?,?,\'queued\',1)',
                    (visit_next, run_id, successor['node'], decision.after.visits, decision.after.epoch, event_id,
                    successor_task.id, successor_task.assigned_agent, selection, hashlib.sha256(selection).hexdigest()))
                db._insert_task_row_uncommitted(successor_task, ownership=('seq_visit', visit_next))
                outbox_id = _outbox(conn, run, event_id, 'visit_queued', visit_next, successor_task.id)
            _move_run(conn, run, event_id, state=state, active_visit_id=visit_next if successor else visit_id if state == 'held' else None,
                epoch=decision.after.epoch, content_returns=decision.after.content_returns, visits=decision.after.visits)
            ids = TransitionIds(run_id, event_id, visit_next, successor_task.id if successor else None, outbox_id)
    validate_selected_run_uncommitted(conn, org_slug=org_slug, run_id=run_id, scope='transition', event_id=ids.event_id)
    return ids


def _close_event_uncommitted(conn: sqlite3.Connection, db: Database, *, org_slug: str, run_id: str,
                             operation_key: str, expected_cas: int, compiled: CompiledDefinition,
                             authority: WorkflowAuthorityCoordinator, capture: AuthorityAdmissionCapture,
                             kind: str, completed_at: str | None = None) -> TransitionIds:
    budget = _ReadBudget()
    with _sql_budget(conn, budget):
        run = _engine_context(conn, db, org_slug=org_slug, run_id=run_id, compiled=compiled,
                              authority=authority, capture=capture, budget=budget)
        replay = _stored_event(conn, run_id, operation_key, budget)
        if replay is not None:
            if replay['kind'] != kind or replay['sequence'] != expected_cas + 2:
                raise SequentialRefusal('sequential_operation_conflict')
            outbox = _engine_rows(conn, 'workflow_seq_outbox', budget, 'event_id=?', (replay['id'],))
            ids = TransitionIds(run_id, replay['id'], None, None, outbox[0]['id'] if outbox else None)
        else:
            _require(run['cas'] == expected_cas, 'transition_cas_conflict')
            if kind == 'cancelled':
                _require(run['state'] in ('active', 'held') and run['active_visit_id'] is not None, 'cancellation_conflict')
            else:
                _require(run['state'] == 'completed' and run['active_visit_id'] is None
                         and type(completed_at) is str and bool(completed_at), 'return_conflict')
            prior = _engine_rows(conn, 'workflow_seq_events', budget, 'id=?', (run['last_event_id'],))[0]
            event_id = _event(conn, run, operation_key, kind, prior['event_digest'])
            if kind == 'cancelled':
                conn.execute("UPDATE workflow_seq_visits SET state='cancelled',selected=0 WHERE id=? AND selected=1",
                             (run['active_visit_id'],))
            else:
                # No publication/notification. U3e owns caller orchestration.
                for task in (run['source_task_id'], run['carrier_task_id']):
                    conn.execute("UPDATE tasks SET status='completed',completed_at=?,updated_at=? WHERE id=?",
                                 (completed_at, completed_at, task))
            _move_run(conn, run, event_id, state='cancelled' if kind == 'cancelled' else 'completed',
                active_visit_id=None, epoch=run['epoch'], content_returns=run['content_returns'], visits=run['visit_count'])
            outbox_id = _outbox(conn, run, event_id, 'run_return', None, run['source_task_id']) if kind == 'returned' else None
            ids = TransitionIds(run_id, event_id, None, None, outbox_id)
    validate_selected_run_uncommitted(conn, org_slug=org_slug, run_id=run_id,
                                      scope='cancel' if kind == 'cancelled' else 'return')
    return ids


def return_uncommitted(conn: sqlite3.Connection, db: Database, *, org_slug: str, run_id: str,
                        operation_key: str, expected_cas: int, completed_at: str,
                        compiled: CompiledDefinition, authority: WorkflowAuthorityCoordinator,
                        capture: AuthorityAdmissionCapture) -> TransitionIds:
    return _close_event_uncommitted(conn, db, org_slug=org_slug, run_id=run_id, operation_key=operation_key,
        expected_cas=expected_cas, completed_at=completed_at, compiled=compiled, authority=authority,
        capture=capture, kind='returned')


def cancel_fence_uncommitted(conn: sqlite3.Connection, db: Database, *, org_slug: str, run_id: str,
                              operation_key: str, expected_cas: int, compiled: CompiledDefinition,
                              authority: WorkflowAuthorityCoordinator,
                              capture: AuthorityAdmissionCapture) -> TransitionIds:
    return _close_event_uncommitted(conn, db, org_slug=org_slug, run_id=run_id, operation_key=operation_key,
        expected_cas=expected_cas, compiled=compiled, authority=authority, capture=capture, kind='cancelled')

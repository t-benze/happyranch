"""Inert U3b admission and first activation, with no runtime caller attached.

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
from runtime.workflows.sequential_contracts import Limits, StartCommand
from runtime.workflows.sequential_definition import CompiledDefinition, parse_start, validate_start
from runtime.workflows.coding_authority_history import _bounded_rows
from runtime.workflows.sequential_history import _ReadBudget, _bytes, _native_rows, _parse, _require, _result, _sql_budget
from runtime.workflows.sequential_run_validation import _pins, _selected_layout, validate_selected_run_uncommitted


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

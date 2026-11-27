"""Generic member controls. Discovery precedes the caller-owned authority writer.

No native signal, queue publication, source continuation or public admission is
performed here. Refusals must escape the writer and roll back its transaction.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from pydantic import ValidationError

from runtime.infrastructure.db.tasks import RetryClaim
from runtime.models import TaskRecord, TaskStatus
from runtime.workflows.authority import generic_assignment_authority
from runtime.workflows.sequential_assignments import (
    _roles, resolve_node_assignment, selection_agent,
)
from runtime.workflows.sequential_contracts import GenericControl, GenericCounters, GenericNodeCounters, GenericStepOverride
from runtime.workflows.sequential_definition import SequentialContractError, _json, generic_limits, parse_generic_start
from runtime.workflows.sequential_engine import SequentialRefusal, _engine_context, _engine_rows, _identity
from runtime.workflows.sequential_history import _ReadBudget, _bytes, _decode, _hashed, _require, _result, _sql_budget
from runtime.workflows.sequential_reducer import reduce_generic_control, require_generic_event_capacity


def parse_control(raw: bytes) -> GenericControl:
    value = _json(raw, 49152)
    try:
        command = GenericControl.model_validate(value)
    except ValidationError:
        raise SequentialContractError('invalid_control') from None
    _require(raw == _bytes(value), 'control_not_canonical')
    return command


@dataclass(frozen=True)
class ControlCapture:
    authority: object
    command_bytes: bytes
    prior_cas: int
    assignment: dict | None
    authority_snapshot: dict


def _validate_control_assignment(command: GenericControl, *, compiled: object, previous: dict,
                                  value: dict, authority: dict, run: dict,
                                  history: list[dict]) -> None:
    """Pure reconstruction of presence/carry and historical contributor rules."""
    from runtime.workflows.authority import validate_generic_assignment_authority
    validate_generic_assignment_authority(authority, org_slug=run['org_slug'])
    node = next(node for node in compiled.definition.nodes if node.id == previous['node'])
    target = command.target_agent_id if command.action == 'reassign' else previous['principal']
    resolved = json.loads(run['assignment_snapshot_bytes'])
    roles = {item['role']: item['principal'] for item in resolved['roles']}
    roles[node.role] = target
    start = json.loads(run['start_bytes'])
    start['role_overrides'] = [dict(role=role, principal=principal) for role, principal in roles.items()]
    _roles(compiled, parse_generic_start(_bytes(start)), (), authority, resolved['team'])
    requirements = dict(compiled.definition.roles)
    for row in history:
        prior_node = next(item for item in compiled.definition.nodes if item.id == row['node_id'])
        independent = (prior_node.role in requirements[node.role].independent_of
                       or node.role in requirements[prior_node.role].independent_of)
        _require(not independent or row['principal'] != target, 'assignment_contributor_conflict')
    agent = next(item for item in authority['agents'] if item['name'] == target)
    edits = command.model_dump(include={'model', 'reasoning_effort'}, exclude_unset=True)
    requested, origins = dict(previous['requested']), dict(previous['origins'])
    requested.update(edits)
    origins.update({key: 'clear' if edit is None else 'run' for key, edit in edits.items()})
    selection = dict(previous['selection'])
    selection.update({key + '_override': edit for key, edit in edits.items()})
    selection['override_source'] = 'manager' if any(selection[key + '_override'] is not None
                                                   for key in ('model', 'reasoning_effort')) else None
    effective = {key: selection[key + '_override'] if selection[key + '_override'] is not None else agent.get(key)
                 for key in ('model', 'reasoning_effort')}
    _require(value.keys() == previous.keys() and (value['node'], value['role'], value['principal']) ==
        (node.id, node.role, target) and value['requested'] == requested and value['origins'] == origins
        and value['selection'] == selection and value['effective'] == effective
        and value['executor'].keys() == {'name', 'kind', 'workspace_adapter_id', 'command_adapter_id'}
        and value['executor']['name'] == agent['executor'], 'control_assignment_conflict')


def result_counters(run: dict, compiled: object, visits: list[dict], events: list[dict],
                    controls: list[dict], node_rows: list[dict]) -> GenericCounters:
    """Result progress never uses the persisted metadata sequence counter."""
    revisions = {row['node_id']: row['revisions'] for row in node_rows}
    counts = {node.id: GenericNodeCounters(
        attempts=sum(visit['node_id'] == node.id for visit in visits),
        revisions=revisions.get(node.id, 0),
        recoveries=sum(control['new_visit_id'] is not None and any(
            visit['id'] == control['new_visit_id'] and visit['node_id'] == node.id for visit in visits)
            for control in controls)) for node in compiled.definition.nodes}
    return GenericCounters(visits=run['visit_count'], events=sum(event['kind'] == 'transition' for event in events),
                           recoveries=run['recovery_count'], nodes=counts)


def prepare_control(org: object, *, command_bytes: bytes, compiled: object) -> ControlCapture:
    """Resolve only the replacement occurrence, outside SQL ownership/leases."""
    command = parse_control(command_bytes)
    capture = org.workflow_authority.capture_admission()
    authority = generic_assignment_authority(capture, org_slug=org.slug)
    with org.db.coherent_read_view() as conn:
        budget = _ReadBudget()
        rows = _engine_rows(conn, 'workflow_seq_runs', budget, 'id=?', (command.run_id,))
        _require(len(rows) == 1 and rows[0]['engine_version'] == 2 and rows[0]['org_slug'] == org.slug, 'run_missing')
        run = rows[0]
        # Replays need no executor discovery and must survive later lifecycle.
        replay = _engine_rows(conn, 'workflow_seq_controls', budget, 'run_id=? AND operation_key=?',
                              (command.run_id, command.operation_key))
        assignment = None
        if command.action != 'cancel' and not replay and run['state'] == 'held':
            held = _engine_rows(conn, 'workflow_seq_events', budget, 'run_id=? AND id=?',
                                (run['id'], run['held_event_id']))
            _require(len(held) == 1, 'control_assignment_conflict')
            visits = _engine_rows(conn, 'workflow_seq_visits', budget, 'run_id=? AND id=? AND node_id=?',
                                  (run['id'], held[0]['source_visit_id'], run['held_node_id']))
            _require(len(visits) == 1, 'control_assignment_conflict')
            assignments = _engine_rows(conn, 'workflow_seq_assignments', budget, 'run_id=? AND id=?',
                                       (run['id'], visits[0]['assignment_id']))
            _require(len(assignments) == 1, 'control_assignment_conflict')
            previous = assignments[0]
            assignment = json.loads(_hashed(previous['snapshot_bytes'], previous['snapshot_digest']))
        start = parse_generic_start(run['start_bytes'])
        resolved = json.loads(run['assignment_snapshot_bytes'])
        history = _engine_rows(conn, 'workflow_seq_assignments', budget, 'run_id=?', (run['id'],))
    if assignment is not None:
        node = next(node for node in compiled.definition.nodes if node.id == assignment['node'])
        target = command.target_agent_id if command.action == 'reassign' else assignment['principal']
        overrides = {row['role']: row['principal'] for row in resolved['roles']}
        overrides[node.role] = target
        roles = tuple(dict(role=role, principal=principal) for role, principal in overrides.items())
        value = json.loads(run['start_bytes'])
        value['role_overrides'] = list(roles)
        prospective = parse_generic_start(_bytes(value))
        _roles(compiled, prospective, (), authority, resolved['team'])
        requirements = dict(compiled.definition.roles)
        for prior in history:
            prior_node = next(item for item in compiled.definition.nodes if item.id == prior['node_id'])
            independent = (prior_node.role in requirements[node.role].independent_of
                           or node.role in requirements[prior_node.role].independent_of)
            _require(not independent or prior['principal'] != target, 'assignment_contributor_conflict')
        agent = next(item for item in authority['agents'] if item['name'] == target)
        edits = command.model_dump(include={'model', 'reasoning_effort'}, exclude_unset=True)
        assignment = resolve_node_assignment(selection_agent(agent), node=node.id, role=node.role,
            previous={**assignment, 'principal': target}, edit=GenericStepOverride(node=node.id, **edits))
    return ControlCapture(capture, command_bytes, run['cas'], assignment, authority)


def _append_event(conn: sqlite3.Connection, run: dict, *, kind: str, operation_key: str,
                  payload: dict, budget: _ReadBudget, resulting_state: str, cancel: bool = False,
                  source_visit: str | None = None, source_result: int | None = None) -> str:
    require_generic_event_capacity(run['event_count'], resulting_state=resulting_state, cancel=cancel)
    prior = _engine_rows(conn, 'workflow_seq_events', budget, 'id=?', (run['last_event_id'],))
    _require(len(prior) == 1, 'event_missing')
    sequence = run['event_count'] + 1
    key = _identity('event', run['id'], str(sequence))
    value = dict(format='workflow-sequential-storage-event@2', kind=kind, run_id=run['id'], sequence=sequence,
                 operation_key=operation_key, prior_digest=prior[0]['event_digest'], **payload)
    raw = _bytes(value)
    _require(len(raw) <= 49152, 'control_event_bounds')
    conn.execute('INSERT INTO workflow_seq_events '
        '(id,run_id,engine_version,sequence,operation_key,kind,prior_digest,event_bytes,event_digest,source_visit_id,source_result_id) '
        'VALUES (?,?,2,?,?,?,?,?,?,?,?)', (key, run['id'], sequence, operation_key, kind,
        prior[0]['event_digest'], raw, hashlib.sha256(raw).hexdigest(), source_visit, source_result))
    return key


def _move(conn: sqlite3.Connection, run: dict, event_id: str, **changes: object) -> None:
    values = dict(changes, event_count=run['event_count'] + 1, cas=run['cas'] + 1, last_event_id=event_id)
    changed = conn.execute('UPDATE workflow_seq_runs SET ' + ','.join(key + '=?' for key in values)
        + ' WHERE id=? AND cas=?', (*values.values(), run['id'], run['cas']))
    _require(changed.rowcount == 1, 'control_cas_conflict')


def _rows(conn: sqlite3.Connection, run: dict, budget: _ReadBudget) -> dict[str, list[dict]]:
    return {table: _engine_rows(conn, table, budget, 'run_id=?', (run['id'],)) for table in (
        'workflow_seq_visits', 'workflow_seq_events', 'workflow_seq_controls', 'workflow_seq_node_counters')}


def _finish_member_carrier(conn: sqlite3.Connection, run: dict) -> None:
    """Only a fully drained member cancellation closes its structural carrier."""
    if conn.execute('SELECT 1 FROM workflow_seq_source_memberships WHERE run_id=?', (run['id'],)).fetchone():
        stamp = datetime.now(timezone.utc).isoformat()
        conn.execute("UPDATE tasks SET status='cancelled',cancelled_at=?,updated_at=? "
                     "WHERE id=? AND status='pending'", (stamp, stamp, run['carrier_task_id']))


def apply_control_uncommitted(conn: sqlite3.Connection, db: object, *, org_slug: str, compiled: object,
                              actor: RetryClaim, capture: ControlCapture, authority: object,
                              replacement_task: TaskRecord | None = None) -> bytes:
    """Return the immutable original receipt bytes, including after later transitions."""
    from runtime.workflows.sequential_business_hold import generic_closed_visit
    command = parse_control(capture.command_bytes)
    budget = _ReadBudget()
    with _sql_budget(conn, budget):
        run = _engine_context(conn, db, org_slug=org_slug, run_id=command.run_id, compiled=compiled,
                              authority=authority, capture=capture.authority, budget=budget)
        _require(run['engine_version'] == 2, 'control_version_conflict')
        _require((actor.task_id, actor.assigned_agent) == (run['source_task_id'], run['source_principal']),
                 'control_actor_conflict')
        _result(conn, actor.result_row_id, actor.task_id, actor.assigned_agent, actor.current_session_id, budget)
        _require(conn.execute('SELECT status FROM task_results WHERE id=?', (actor.result_row_id,)).fetchone()[0] == 'completed',
                 'control_actor_conflict')
        replay = _engine_rows(conn, 'workflow_seq_controls', budget, 'run_id=? AND operation_key=?',
                              (run['id'], command.operation_key))
        if replay:
            row = replay[0]
            _require((row['request_bytes'], row['actor_task_id'], row['actor_principal'], row['actor_session_id'], row['actor_result_id'])
                == (capture.command_bytes, actor.task_id, actor.assigned_agent, actor.current_session_id, actor.result_row_id),
                'control_operation_conflict')
            return _hashed(row['receipt_bytes'], row['receipt_digest'])
        _require(db._retry_claim_matches(actor), 'control_actor_conflict')
        data = _rows(conn, run, budget)
        if run['held_event_id'] is not None:
            held = next(row for row in data['workflow_seq_events'] if row['id'] == run['held_event_id'])
            run = dict(run, hold_cause=json.loads(_hashed(held['event_bytes'], held['event_digest'])).get('cause'))
        counts = result_counters(run, compiled, data['workflow_seq_visits'], data['workflow_seq_events'],
                                 data['workflow_seq_controls'], data['workflow_seq_node_counters'])
        start = parse_generic_start(run['start_bytes'])
        from runtime.workflows.sequential_contracts import GenericLimits
        original = GenericLimits.model_validate_json(run['original_limits_bytes'])
        limits = generic_limits(compiled, original, start.lower_limits)
        disposition = reduce_generic_control(compiled, command, run=run, counters=counts, limits=limits)
        _require(run['cas'] == capture.prior_cas, 'control_cas_conflict')
        visit = next((row for row in data['workflow_seq_visits'] if row['id'] == run['active_visit_id']), None)
        immediate = disposition == 'cancel' and (visit is None or visit['session_id'] is None)
        # Defensive for invalid histories: a launched cancel needs fence + finish.
        # Valid non-cancel writers leave Running <=4094, so both always fit.
        resulting_state = 'finished' if immediate else 'running'
        require_generic_event_capacity(run['event_count'], resulting_state=resulting_state,
                                       cancel=disposition == 'cancel')
        payload = dict(command_digest=hashlib.sha256(capture.command_bytes).hexdigest(),
            actor=dict(task_id=actor.task_id, principal=actor.assigned_agent, session_id=actor.current_session_id,
                       result_id=actor.result_row_id), before_counters=counts.model_dump(mode='json'),
            replaced_visit_id=None, new_visit_id=None, assignment=None, supplement=None)
        new_visit = None
        if disposition == 'replace':
            previous = max((row for row in data['workflow_seq_visits'] if row['node_id'] == run['held_node_id']),
                           key=lambda row: row['ordinal'])
            generic_closed_visit(conn, run=run, visit=previous, budget=budget)
            from runtime.workflows.sequential_run_validation import generic_selection
            node = next(item for item in compiled.definition.nodes if item.id == previous['node_id'])
            current_slots = {item.source[8:] for item in node.inputs if item.source.startswith('current.')}
            retained = tuple(ref for ref in generic_selection(previous, budget) if ref.slot in current_slots)
            if retained:
                # Correction edges retire selections even when their target's
                # allowance is exhausted. Never admit an unrenderable recovery.
                latest = max((event for event in data['workflow_seq_events'] if event['kind'] == 'transition'),
                             key=lambda event: event['sequence'], default=None)
                current = {} if latest is None else {ref['slot']: ref for ref in _decode(
                    _hashed(latest['event_bytes'], latest['event_digest']), budget)['decision']['selections']}
                if any(current.get(ref.slot) != ref.model_dump(mode='json') for ref in retained):
                    raise SequentialRefusal('control_input_retired')
            _require(capture.assignment is not None and capture.authority_snapshot == generic_assignment_authority(
                capture.authority, org_slug=org_slug), 'control_assignment_conflict')
            assignment = capture.assignment
            previous_assignment = _engine_rows(conn, 'workflow_seq_assignments', budget,
                'id=?', (previous['assignment_id'],))[0]
            _validate_control_assignment(command, compiled=compiled,
                previous=json.loads(_hashed(previous_assignment['snapshot_bytes'], previous_assignment['snapshot_digest'])),
                value=assignment, authority=capture.authority_snapshot, run=run,
                history=_engine_rows(conn, 'workflow_seq_assignments', budget, 'run_id=?', (run['id'],)))
            task = replacement_task
            _require(task is not None and task.id != previous['task_id']
                and task.parent_task_id == run['carrier_task_id'] and task.assigned_agent == assignment['principal']
                and task.team == json.loads(run['assignment_snapshot_bytes'])['team']
                and task.task_type == 'subtask' and task.status == TaskStatus.PENDING
                and task.current_session_id is None and task.completed_at is None and task.cancelled_at is None
                and task.executor_pid is None and task.active_chain is None and task.active_fanout is None
                and task.block_kind is None and task.blocked_on_job_ids is None and task.revisit_of_task_id is None
                and task.dispatched_from_thread_id is None and task.zombie_flagged_at is None, 'native_task_not_pristine')
            _require(not _engine_rows(conn, 'tasks', budget, 'id=?', (task.id,)), 'native_identity_conflict')
            new_visit = _identity('visit', run['id'], str(run['visit_count'] + 1))
            prior_controls = data['workflow_seq_controls']
            prior_supplement = None
            for prior_control in sorted(prior_controls, key=lambda item: item['expected_cas']):
                prior_command = parse_control(prior_control['request_bytes'])
                prior_receipt = json.loads(prior_control['receipt_bytes'])
                prior_visit = next((item for item in data['workflow_seq_visits']
                                    if item['id'] == prior_receipt['new_visit_id']), None)
                if prior_visit is not None and prior_visit['node_id'] == previous['node_id'] and prior_command.supplement is not None:
                    prior_supplement = prior_command.supplement
            supplement = command.supplement if command.supplement is not None else prior_supplement
            original_visit = min((item for item in data['workflow_seq_visits'] if item['node_id'] == previous['node_id']),
                                 key=lambda item: item['ordinal'])
            from runtime.workflows.sequential_briefs import recovery_context
            readonly, _, _ = recovery_context(conn, run, previous, budget, held_event_id=run['held_event_id'])
            brief = _bytes(dict(format='workflow-sequential-recovery-brief@2', original_request=start.request,
                node=node.id, role=node.role, responsibilities=list(node.responsibilities),
                original_step_brief=db.get_task(original_visit['task_id']).brief,
                selected_input_digest=previous['input_selection_digest'], supplement=supplement,
                reason=command.reason, effect_disposition='unverified',
                read_only_reconciliation=readonly)).decode('utf8')
            replacement_task = task.model_copy(update={'brief': brief})
            payload.update(replaced_visit_id=previous['id'], new_visit_id=new_visit,
                assignment=dict(value=assignment, authority=capture.authority_snapshot), supplement=supplement,
                original_brief_digest=hashlib.sha256(db.get_task(previous['task_id']).brief.encode('utf8')).hexdigest(),
                new_brief_digest=hashlib.sha256(brief.encode('utf8')).hexdigest())
        kind = 'cancellation_finished' if immediate else 'control'
        conn.execute('PRAGMA defer_foreign_keys=ON')
        event_id = _append_event(conn, run, kind=kind, operation_key=command.operation_key,
                                  payload=payload, budget=budget, resulting_state=resulting_state,
                                  cancel=disposition == 'cancel')
        if disposition == 'replace':
            assignment_rows = _engine_rows(conn, 'workflow_seq_assignments', budget,
                'run_id=? AND node_id=?', (run['id'], run['held_node_id']))
            predecessor = previous_assignment
            generation = max(row['generation'] for row in assignment_rows) + 1
            assignment_id = _identity('assignment', run['id'], run['held_node_id'], str(generation))
            raw = _bytes(capture.assignment)
            conn.execute('INSERT INTO workflow_seq_assignments VALUES (?,?,?,?,?,?,?,?)',
                (assignment_id, run['id'], run['held_node_id'], generation, capture.assignment['principal'],
                 raw, hashlib.sha256(raw).hexdigest(), predecessor['id']))
            _move(conn, run, event_id, state='running', progress='queued', active_visit_id=new_visit,
                held_node_id=None, held_event_id=None, visit_count=run['visit_count'] + 1,
                recovery_count=run['recovery_count'] + 1, assignment_generation=run['assignment_generation'] + 1)
            conn.execute('INSERT INTO workflow_seq_visits '
                '(id,run_id,engine_version,node_id,ordinal,epoch,predecessor_event_id,task_id,principal,'
                'input_selection_bytes,input_selection_digest,state,selected,assignment_id,assignment_generation) '
                "VALUES (?,?,2,?,?,NULL,?,?,?,?,?,'queued',1,?,?)", (new_visit, run['id'], previous['node_id'],
                run['visit_count'] + 1, event_id, replacement_task.id, capture.assignment['principal'],
                previous['input_selection_bytes'], previous['input_selection_digest'], assignment_id, generation))
            db._insert_task_row_uncommitted(replacement_task.model_copy(update=capture.assignment['selection']),
                                            ownership=('seq_visit', new_visit))
            node_count = dict(counts.nodes)[previous['node_id']]
            conn.execute('INSERT INTO workflow_seq_node_counters VALUES (?,?,?,?) '
                'ON CONFLICT(run_id,node_id) DO UPDATE SET visits=excluded.visits,revisions=excluded.revisions',
                (run['id'], previous['node_id'], node_count.attempts + 1, node_count.revisions))
            from runtime.workflows.sequential_engine import _outbox
            _outbox(conn, run, event_id, 'visit_queued', new_visit, replacement_task.id)
        elif immediate:
            if visit is not None:
                generic_closed_visit(conn, run=run, visit=visit, budget=budget, cancellation=True)
                conn.execute("UPDATE workflow_seq_visits SET state='cancelled',selected=0 WHERE id=?", (visit['id'],))
                stamp = datetime.now(timezone.utc).isoformat()
                conn.execute("UPDATE tasks SET status='cancelled',cancelled_at=?,updated_at=? WHERE id=? AND status='pending'",
                             (stamp, stamp, visit['task_id']))
            _move(conn, run, event_id, state='finished', terminal_outcome='cancelled', progress=None,
                  active_visit_id=None, held_node_id=None, held_event_id=None)
            _finish_member_carrier(conn, run)
        else:
            _move(conn, run, event_id, progress='cancelling')
        receipt = _bytes(dict(format='workflow-sequential-control-receipt@2', run_id=run['id'],
            event_id=event_id, new_visit_id=new_visit,
            new_task_id=None if new_visit is None else replacement_task.id))
        conn.execute('INSERT INTO workflow_seq_controls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (_identity('control', run['id'], command.operation_key), run['id'], command.operation_key,
             actor.task_id, actor.assigned_agent, actor.current_session_id, actor.result_row_id,
             command.expected_cas, command.expected_held_event_id, command.action, capture.command_bytes,
             hashlib.sha256(capture.command_bytes).hexdigest(), event_id, new_visit,
             receipt, hashlib.sha256(receipt).hexdigest()))
    validate_control_write(conn, org_slug=org_slug, deadline=budget.deadline,
                           reserve_cancellation=command.action != 'cancel')
    return receipt


def validate_control_write(conn: sqlite3.Connection, *, org_slug: str, deadline: float,
                           reserve_cancellation: bool = False,
                           run_ids: frozenset[str] | None = None,
                           source_ids: frozenset[str] = frozenset(),
                           definition_ids: frozenset[str] = frozenset(),
                           settings_ids: frozenset[str] = frozenset()) -> None:
    """Validate resulting history on the original deadline before commit.

    Admission supplies explicit roots. Other control/hold/dispatch callers keep
    complete-history validation until their own bounded wiring groups.
    """
    from runtime.workflows.sequential_history import read_sequential_history, _reserve_generic_cancellation
    if run_ids is None:
        read_sequential_history(conn, expected_org_slug=org_slug, deadline=deadline,
                                reserve_cancellation=reserve_cancellation)
        return
    from runtime.workflows.sequential_task_history import read_write_history, read_cancellation_headroom
    selected = read_write_history(conn, expected_org_slug=org_slug, deadline=deadline,
        run_ids=run_ids, source_ids=source_ids, definition_ids=definition_ids, settings_ids=settings_ids)
    if reserve_cancellation:
        headroom = read_cancellation_headroom(conn, expected_org_slug=org_slug,
            metadata=selected.metadata, budget=selected.budget)
        _reserve_generic_cancellation(headroom, selected.budget)


def native_cancel_fence_uncommitted(conn: sqlite3.Connection, db: object, *, org_slug: str,
        run_id: str, compiled: object, authority: object, capture: object,
        origin: str, deadline: float, native_pending: bool = False) -> str | None:
    """Fence native source loss/Founder stop without a result-owned control.

    The mixed owner authenticates the complete membership and native preparation
    in this SAME writer. A prepared owner is outstanding even before SID binding.
    """
    from runtime.workflows.sequential_business_hold import generic_closed_visit
    from runtime.workflows.sequential_initiator import wake_uncommitted
    budget = _ReadBudget(deadline=deadline)
    with _sql_budget(conn, budget):
        run = _engine_context(conn, db, org_slug=org_slug, run_id=run_id, compiled=compiled,
            authority=authority, capture=capture, budget=budget)
        _require(run['engine_version'] == 2 and origin in ('founder_cancel', 'source_terminal') and type(native_pending) is bool,
                 'native_cancel_origin_invalid')
        source = _engine_rows(conn, 'tasks', budget, 'id=?', (run['source_task_id'],))
        _require(len(source) == 1 and (origin != 'source_terminal' or
            source[0]['status'] in ('failed', 'cancelled', 'superseded')), 'native_cancel_source_conflict')
        if run['state'] == 'finished' or run['progress'] == 'cancelling':
            return None  # An existing fence/terminal history has no fresh effect.
        _require(run['state'] in ('running', 'held'), 'native_cancel_state_conflict')
        visits = _engine_rows(conn, 'workflow_seq_visits', budget, 'run_id=?', (run_id,))
        visit = next((row for row in visits if row['id'] == run['active_visit_id']), None)
        immediate = run['state'] == 'held' or (visit is not None and visit['session_id'] is None and not native_pending)
        if immediate and visit is not None:
            generic_closed_visit(conn, run=run, visit=visit, budget=budget, cancellation=True)
            conn.execute("UPDATE workflow_seq_visits SET state='cancelled',selected=0 WHERE id=?", (visit['id'],))
        selected_visit = visit or max(visits, key=lambda row: row['ordinal'])
        event_id = _append_event(conn, run,
            kind='cancellation_finished' if immediate else 'cancelled',
            operation_key='native-cancel-' + str(run['cas']),
            payload=dict(origin=dict(kind=origin, source_task_id=run['source_task_id'], source_status=source[0]['status']),
                before_cas=run['cas'], visit_id=selected_visit['id'], held_event_id=run['held_event_id']),
            budget=budget, resulting_state='finished' if immediate else 'running', cancel=True)
        if immediate:
            _move(conn, run, event_id, state='finished', terminal_outcome='cancelled', progress=None,
                active_visit_id=None, held_node_id=None, held_event_id=None)
            _finish_member_carrier(conn, run)
            event = _engine_rows(conn, 'workflow_seq_events', budget, 'id=?', (event_id,))[0]
            wake_uncommitted(conn, run, event, compiled, {row['id']: row for row in visits}, budget)
        else:
            _require(run['state'] == 'running' and visit is not None, 'native_cancel_state_conflict')
            _move(conn, run, event_id, progress='cancelling')
    validate_control_write(conn, org_slug=org_slug, deadline=budget.deadline)
    return event_id


def finish_cancellation_uncommitted(conn: sqlite3.Connection, db: object, *, org_slug: str,
                                     run_id: str, expected_cas: int, compiled: object,
                                     authority: object, capture: object) -> str:
    from runtime.workflows.sequential_business_hold import generic_closed_visit
    budget = _ReadBudget()
    with _sql_budget(conn, budget):
        run = _engine_context(conn, db, org_slug=org_slug, run_id=run_id, compiled=compiled,
                              authority=authority, capture=capture, budget=budget)
        _require(run['engine_version'] == 2 and run['state'] == 'running'
                 and run['progress'] == 'cancelling' and run['cas'] == expected_cas, 'cancellation_conflict')
        visit = _engine_rows(conn, 'workflow_seq_visits', budget, 'id=?', (run['active_visit_id'],))[0]
        proof = generic_closed_visit(conn, run=run, visit=visit, budget=budget, cancellation=True)
        event = _append_event(conn, run, kind='cancellation_finished', operation_key='cancel-finished-' + str(expected_cas),
            payload=dict(visit_id=visit['id'], closure=proof), budget=budget, resulting_state='finished', cancel=True)
        conn.execute("UPDATE workflow_seq_visits SET state='cancelled',selected=0 WHERE id=?", (visit['id'],))
        _move(conn, run, event, state='finished', terminal_outcome='cancelled', progress=None,
              active_visit_id=None, held_node_id=None, held_event_id=None)
        _finish_member_carrier(conn, run)
    validate_control_write(conn, org_slug=org_slug, deadline=budget.deadline)
    return event

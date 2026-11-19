"""Closed, non-authorizing post-settlement refusal observations.

The event prefix remains authoritative. A hold overlays its last active visit;
only cancellation may follow it. Reading this family issues no capability.
"""
from __future__ import annotations

import hashlib
import sqlite3
from enum import StrEnum

from runtime.workflows.sequential_definition import CompiledDefinition, parse_transition
from runtime.workflows.coding_authority_history import _bounded_rows
from runtime.workflows.sequential_history import _ReadBudget, _bytes, _decode, _native_rows, _parse, _require


class BusinessHoldCause(StrEnum):
    OUTPUT_ACCESS = 'sequential_output_access_unavailable'
    CAPABILITY = 'sequential_capability_unavailable'
    MALFORMED_OUTPUT = 'sequential_carrier_invalid'
    CARRIER_LIMIT = 'sequential_carrier_limit_exceeded'
    OUTPUT_LIMIT = 'sequential_output_limit_exceeded'
    CODE_CHANGE = 'sequential_code_change_invalid'
    SUBJECT_CHANGED = 'sequential_repository_subject_changed'
    SUBJECT_UNVERIFIED = 'sequential_repository_subject_unverified'
    AUTHORITY_DENIED = 'sequential_authority_denied'
    AUTHORITY_UNVERIFIED = 'sequential_authority_unverified'


def hold_id(run_id: str, visit_id: str, result_id: int) -> str:
    return hashlib.sha256(_bytes(dict(format='workflow-sequential-business-hold-id@1',
        run_id=run_id, visit_id=visit_id, result_id=result_id))).hexdigest()


def read_hold_rows(conn: sqlite3.Connection, run_id: str, budget: _ReadBudget) -> list[dict]:
    """Selected acquisition: at most one observation, raw 16 KiB gate first."""
    columns = ('id', 'run_id', 'visit_id', 'kind', 'task_id', 'principal', 'session_id',
               'result_id', 'evidence_bytes', 'evidence_digest')
    kinds = (('text',), ('text',), ('text', 'null'), ('text',), ('text', 'null'),
             ('text', 'null'), ('text', 'null'), ('integer', 'null'), ('blob',), ('text',))
    rows = []
    for item in _bounded_rows(conn, 'workflow_seq_evidence', ('id',), (('text',),), budget,
                             where='run_id=? AND kind=?', args=(run_id, 'observation'), maximum=1):
        key = item[0]
        budget.reserve(512)
        size = conn.execute('SELECT typeof(evidence_bytes),length(CAST(evidence_bytes AS BLOB)) '
                            'FROM workflow_seq_evidence WHERE id=?', (key,)).fetchone()
        _require(size is not None and size[0] == 'blob' and type(size[1]) is int
                 and 0 < size[1] <= 16384, 'business_hold_bytes_invalid')
        selected = _native_rows(conn, 'workflow_seq_evidence', columns, kinds, key, budget)
        _require(len(selected) == 1, 'business_hold_missing')
        rows.append(dict(zip(columns, selected[0], strict=True)))
    return rows


def _proof(conn: sqlite3.Connection, run_id: str, visit_id: str, kind: str,
           budget: _ReadBudget) -> tuple[dict, str]:
    key = hashlib.sha256(_bytes(dict(format='workflow-sequential-evidence-id@1',
        run_id=run_id, visit_id=visit_id, kind=kind))).hexdigest()
    budget.step()
    size = conn.execute('SELECT typeof(evidence_bytes),length(CAST(evidence_bytes AS BLOB)) '
                        'FROM workflow_seq_evidence WHERE id=?', (key,)).fetchone()
    _require(size is not None and size[0] == 'blob' and type(size[1]) is int
             and 0 < size[1] <= 16384, 'business_hold_proof_invalid')
    rows = _native_rows(conn, 'workflow_seq_evidence',
        ('run_id', 'visit_id', 'kind', 'task_id', 'principal', 'session_id', 'result_id',
         'evidence_bytes', 'evidence_digest'),
        (('text',), ('text',), ('text',), ('null',), ('null',), ('null',), ('null',),
         ('blob',), ('text',)), key, budget)
    _require(len(rows) == 1 and rows[0][:7] == (run_id, visit_id, kind, None, None, None, None),
             'business_hold_proof_conflict')
    raw, digest = rows[0][7:]
    _require(hashlib.sha256(raw).hexdigest() == digest, 'business_hold_proof_digest')
    return _decode(raw, budget), digest


def hold_body(conn: sqlite3.Connection, *, run: dict, visit: dict, compiled: CompiledDefinition,
              result_id: int, cause: BusinessHoldCause, event: dict, budget: _ReadBudget) -> dict:
    """Reconstruct exclusively from original native callback and committed proofs."""
    _require(type(result_id) is int and result_id > 0 and visit['session_id'] is not None,
             'business_hold_identity_invalid')
    # The carrier can exceed the generic reader's 1 MiB cell limit because of
    # base64. Preflight actual bytes and reserve its acquisition/UTF8 hash copies;
    # do not decode business content or reconstruct an output to record refusal.
    budget.step()
    size = conn.execute('SELECT typeof(output_summary),length(CAST(output_summary AS BLOB)) '
                        'FROM task_results WHERE id=?', (result_id,)).fetchone()
    _require(size is not None and size[0] == 'text' and type(size[1]) is int
             and 0 < size[1] <= 1414488, 'business_hold_callback_invalid')
    budget.reserve(8 * size[1] + 512)
    native = _native_rows(conn, 'task_results', ('task_id', 'agent', 'session_id', 'status'),
        (('text',), ('text',), ('text',), ('text',)), result_id, budget)
    _require(native == [(visit['task_id'], visit['principal'], visit['session_id'], 'completed')],
             'business_hold_callback_conflict')
    summary = conn.execute('SELECT output_summary FROM task_results WHERE id=?', (result_id,)).fetchone()[0]
    identity = dict(task_id=visit['task_id'], principal=visit['principal'], session_id=visit['session_id'],
        result_id=result_id, status='completed', carrier_digest=hashlib.sha256(summary.encode('utf8')).hexdigest())
    terminal, terminal_digest = _proof(conn, run['id'], visit['id'], 'terminal', budget)
    outer, outer_digest = _proof(conn, run['id'], visit['id'], 'outer', budget)
    _require(terminal.keys() == {'format', 'invocation_id', 'task_id', 'principal', 'session_id',
             'classification', 'request', 'handle_digest', 'receipt', 'callback'}
             and terminal['format'] == 'workflow-sequential-terminal@1'
             and type(terminal['invocation_id']) is str and bool(terminal['invocation_id'])
             and terminal['classification'] == 'success'
             and terminal['callback'] == identity
             and type(terminal['handle_digest']) is str and len(terminal['handle_digest']) == 64
             and all(c in '0123456789abcdef' for c in terminal['handle_digest'])
             and type(terminal['request']) is dict
             and terminal['request'].keys() == {'org', 'logical_id', 'agent_id', 'retry_attempt'}
             and terminal['request']['org'] == run['org_slug']
             and terminal['request']['agent_id'] == visit['principal']
             and type(terminal['request']['logical_id']) is str and bool(terminal['request']['logical_id'])
             and type(terminal['request']['retry_attempt']) is int
             and terminal['request']['retry_attempt'] == 0, 'business_hold_terminal_invalid')
    receipt = terminal['receipt']
    _require(type(receipt) is dict and receipt.keys() == {'backend', 'terminal_reason', 'cleanup_status',
             'quiescent', 'survivors'} and receipt['backend'] in ('linux-systemd-cgroup-v2', 'macos-process-group')
             and receipt['terminal_reason'] == 'success' and receipt['cleanup_status'] in ('clean', 'term', 'kill')
             and receipt['quiescent'] is True and receipt['survivors'] == [], 'business_hold_terminal_invalid')
    common = dict(task_id=visit['task_id'], principal=visit['principal'], session_id=visit['session_id'])
    _require(all(terminal[key] == value for key, value in common.items())
             and outer == dict(format='workflow-sequential-outer@1', invocation_id=terminal['invocation_id'],
                 **common, callback=identity, positive=True, tracker_clear=True, native_phase='result_processing',
                 close_removed=True, release_removed=True, release_owner=terminal['invocation_id'],
                 execution_unknown=False)
             and outer.get('positive') is True and outer.get('tracker_clear') is True
             and outer.get('close_removed') is True and outer.get('release_removed') is True
             and outer.get('execution_unknown') is False, 'business_hold_outer_invalid')
    task = _native_rows(conn, 'tasks', ('assigned_agent', 'current_session_id', 'completed_at'),
        (('text',), ('text',), ('text',)), visit['task_id'], budget)
    _require(len(task) == 1 and task[0][:2] == (visit['principal'], visit['session_id'])
             and bool(task[0][2]), 'business_hold_native_conflict')
    node = next(node for node in compiled.definition.nodes if node.id == visit['node_id'])
    budget.reserve(16384)
    return dict(format='workflow-sequential-business-hold@1', run_id=run['id'], visit_id=visit['id'],
        node=node.id, role=node.role, principal=visit['principal'], task_id=visit['task_id'],
        session_id=visit['session_id'], result_id=result_id, epoch=visit['epoch'],
        definition_digest=compiled.pin.definition_digest, start_digest=run['start_digest'],
        input_selection_digest=visit['input_selection_digest'], callback=identity,
        terminal_digest=terminal_digest, outer_digest=outer_digest,
        cas=event['sequence'] - 1, event_count=event['sequence'], last_event_id=event['id'],
        last_event_digest=event['event_digest'], cause=cause.value, next_owner='accountable_manager',
        action='no_automatic_retry', limitation='no_output_or_successor; lifecycle_resume_unavailable')


def validate_business_holds(conn: sqlite3.Connection, *, rows: list[dict], run: dict,
                            visits: dict[str, dict], events: list[dict], compiled: CompiledDefinition,
                            budget: _ReadBudget) -> dict | None:
    """Authenticate an observation against its active prefix, never just state."""
    _require(len(rows) <= 1, 'business_hold_conflict')  # Resume is not implemented.
    if not rows:
        return None
    row = rows[0]
    raw = row['evidence_bytes']
    _require(type(raw) is bytes and 0 < len(raw) <= 16384
             and hashlib.sha256(raw).hexdigest() == row['evidence_digest'], 'business_hold_digest')
    value = _decode(raw, budget)
    if value.get('format') == 'workflow-sequential-repository-hold@1':
        from runtime.workflows.sequential_repository_subject import validate_repository_hold
        return validate_repository_hold(conn, row=row, value=value, run=run, visits=visits,
                                        events=events, compiled=compiled, budget=budget)
    visit = visits.get(row['visit_id'])
    _require(visit is not None and row['run_id'] == run['id'] == visit['run_id']
             and row['kind'] == 'observation'
             and (row['task_id'], row['principal'], row['session_id']) ==
                 (visit['task_id'], visit['principal'], visit['session_id'])
             and type(row['result_id']) is int and row['result_id'] > 0
             and row['id'] == hold_id(run['id'], visit['id'], row['result_id']), 'business_hold_owner_conflict')
    _require(type(value.get('cause')) is str and value['cause'] in BusinessHoldCause
             and type(value.get('event_count')) is int and type(value.get('cas')) is int
             and type(value.get('result_id')) is int and type(value.get('epoch')) is int,
             'business_hold_cause_invalid')
    budget.work(2 * len(events))
    budget.reserve(512 + 128 * len(events))
    event = next((event for event in events if event['id'] == value.get('last_event_id')), None)
    _require(event is not None and event['kind'] in ('admitted', 'transition'), 'business_hold_anchor_invalid')
    # This must be the event that selected this visit, not any active-looking
    # earlier prefix. The ordinary readers independently authenticate the chain.
    _require((visit['ordinal'] == 1 and event['kind'] == 'admitted'
              and visit['predecessor_event_id'] is None)
             or (event['kind'] == 'transition' and visit['predecessor_event_id'] == event['id']),
             'business_hold_prefix_conflict')
    if event['kind'] == 'transition':
        transition = _parse(event['event_bytes'], parse_transition, budget)
        successor = transition.successor
        _require(successor is not None and
                 (successor.visit_id, successor.task_id, successor.node) ==
                 (visit['id'], visit['task_id'], visit['node_id']), 'business_hold_prefix_conflict')
    suffix = [item for item in events if item['sequence'] > event['sequence']]
    _require(not suffix or len(suffix) == 1 and suffix[0]['kind'] == 'cancelled', 'business_hold_suffix_conflict')
    _require(visit['result_id'] is None and (visit['state'], visit['selected']) ==
             (('cancelled', 0) if suffix else ('held', 1))
             and run['state'] == ('cancelled' if suffix else 'held'), 'business_hold_state_conflict')
    _require(conn.execute('SELECT 1 FROM workflow_seq_outputs WHERE visit_id=? LIMIT 1',
                         (visit['id'],)).fetchone() is None, 'business_hold_output_conflict')
    expected = hold_body(conn, run=run, visit=visit, compiled=compiled, result_id=row['result_id'],
        cause=BusinessHoldCause(value['cause']), event=event, budget=budget)
    budget.reserve(16384)
    _require(raw == _bytes(expected), 'business_hold_payload_conflict')
    return value


def generic_callback_bytes(conn: sqlite3.Connection, result_id: int,
                           budget: _ReadBudget) -> tuple[bytes, str] | None:
    """Acquire a bounded native payload once within the caller's snapshot.

    Oversize rejection observes only size, never acquires unbounded text.
    Callers separately authenticate native ownership, status and closure.
    """
    budget.step()
    size = conn.execute('SELECT typeof(output_summary),length(CAST(output_summary AS BLOB)) '
                        'FROM task_results WHERE id=?', (result_id,)).fetchone()
    _require(size is not None and size[0] == 'text' and type(size[1]) is int
             and size[1] >= 0, 'hold_callback_bounds')
    if size[1] > 1414488:
        return None
    cached = budget.generic_callbacks.get(result_id)
    if cached is None:
        budget.reserve(8 * size[1] + 512)
        summary = conn.execute('SELECT output_summary FROM task_results WHERE id=?', (result_id,)).fetchone()[0]
        raw = summary.encode('utf8')
        cached = (raw, hashlib.sha256(raw).hexdigest())
        budget.generic_callbacks[result_id] = cached
    _require(len(cached[0]) == size[1], 'hold_callback_conflict')
    return cached


def _generic_callback_digest(conn: sqlite3.Connection, result_id: int, budget: _ReadBudget) -> str | None:
    """Authenticate cancellation bytes without acquiring business content.

    Charge every raw byte and its UTF8 validation copies, but no full-content
    parser/model reservation. The stream and any reused payload belong only to
    this snapshot; oversize callbacks retain the existing size-only refusal.
    """
    import codecs
    budget.step()
    size = conn.execute('SELECT typeof(output_summary),length(CAST(output_summary AS BLOB)) '
                        'FROM task_results WHERE id=?', (result_id,)).fetchone()
    _require(size is not None and size[0] == 'text' and type(size[1]) is int
             and size[1] >= 0, 'hold_callback_bounds')
    if size[1] > 1414488:
        return None
    cached = budget.generic_callbacks.get(result_id)
    if cached is not None:
        _require(len(cached[0]) == size[1], 'hold_callback_conflict')
        return cached[1]
    # One raw-byte copy plus up to four bytes per decoded Unicode character.
    # No business content is decoded or retained, and all work shares the
    # original byte/SQL/traversal/deadline allowances.
    budget.reserve(5 * size[1] + 512)
    digest = hashlib.sha256()
    decoder = codecs.getincrementaldecoder('utf8')()
    for offset in range(0, size[1], 65536):
        budget.step()
        length = min(65536, size[1] - offset)
        raw = conn.execute('SELECT substr(CAST(output_summary AS BLOB),?,?) FROM task_results WHERE id=?',
                           (offset + 1, length, result_id)).fetchone()[0]
        _require(type(raw) is bytes and len(raw) == length, 'hold_callback_conflict')
        decoder.decode(raw, final=offset + length == size[1])
        digest.update(raw)
    budget.check()
    return digest.hexdigest()


def generic_closed_visit(conn: sqlite3.Connection, *, run: dict, visit: dict, budget: _ReadBudget,
                         cancellation: bool = False) -> dict:
    """Original native terminal AND positive outer proof, or pristine no-launch.

    This observation never manufactures a callback or settles native ownership.
    """
    native = _native_rows(conn, 'tasks', ('assigned_agent', 'current_session_id', 'status',
        'completed_at', 'executor_pid'), (('text',), ('text', 'null'), ('text',),
        ('text', 'null'), ('integer', 'null')), visit['task_id'], budget)
    _require(len(native) == 1 and native[0][:2] == (visit['principal'], visit['session_id']), 'hold_native_conflict')
    if visit['session_id'] is None:
        _require(visit['state'] in ('queued', 'held', 'cancelled')
                 and native[0][2] in ('pending', 'cancelled') and native[0][3:] == (None, None), 'hold_unsettled')
        _require(conn.execute('SELECT 1 FROM task_results WHERE task_id=? LIMIT 1', (visit['task_id'],)).fetchone() is None,
                 'hold_native_conflict')
        return dict(kind='never_launched', result_id=None)
    terminal, terminal_digest = _proof(conn, run['id'], visit['id'], 'terminal', budget)
    outer, outer_digest = _proof(conn, run['id'], visit['id'], 'outer', budget)
    common = dict(task_id=visit['task_id'], principal=visit['principal'], session_id=visit['session_id'])
    _require(terminal.keys() == {'format', 'invocation_id', 'task_id', 'principal', 'session_id',
        'classification', 'request', 'handle_digest', 'receipt', 'callback'}
        and terminal['format'] == 'workflow-sequential-terminal@1'
        and type(terminal['invocation_id']) is str and bool(terminal['invocation_id'])
        and all(terminal[key] == value for key, value in common.items())
        and type(terminal['handle_digest']) is str and len(terminal['handle_digest']) == 64
        and all(char in '0123456789abcdef' for char in terminal['handle_digest'])
        and type(terminal['request']) is dict
        and terminal['request'].keys() == {'org', 'logical_id', 'agent_id', 'retry_attempt'}
        and terminal['request']['org'] == run['org_slug']
        and terminal['request']['agent_id'] == visit['principal']
        and type(terminal['request']['logical_id']) is str and bool(terminal['request']['logical_id'])
        and type(terminal['request']['retry_attempt']) is int and terminal['request']['retry_attempt'] == 0,
        'hold_terminal_invalid')
    receipt = terminal['receipt']
    _require(type(receipt) is dict and receipt.keys() == {'backend', 'terminal_reason', 'cleanup_status', 'quiescent', 'survivors'}
        and receipt['backend'] in ('linux-systemd-cgroup-v2', 'macos-process-group')
        and receipt['cleanup_status'] in ('clean', 'term', 'kill')
        and receipt['quiescent'] is True and receipt['survivors'] == [], 'hold_unsettled')
    _require(outer == dict(format='workflow-sequential-outer@1', invocation_id=terminal['invocation_id'],
        **common, callback=terminal['callback'], positive=True, tracker_clear=True, native_phase='result_processing',
        close_removed=True, release_removed=True, release_owner=terminal['invocation_id'], execution_unknown=False)
        and outer.get('positive') is True and outer.get('tracker_clear') is True
        and outer.get('close_removed') is True and outer.get('release_removed') is True
        and outer.get('execution_unknown') is False
        and native[0][2] in ('completed', 'failed', 'cancelled') and bool(native[0][3]), 'hold_unsettled')
    callback = terminal['callback']
    if callback is None:
        _require(conn.execute('SELECT 1 FROM task_results WHERE task_id=? AND session_id=? LIMIT 1',
            (visit['task_id'], visit['session_id'])).fetchone() is None, 'hold_callback_conflict')
    else:
        _require(type(callback) is dict and callback.keys() == {'task_id', 'principal', 'session_id', 'result_id', 'status', 'carrier_digest'}
            and all(callback[key] == value for key, value in common.items()), 'hold_callback_conflict')
        from runtime.workflows.sequential_history import _result
        _result(conn, callback['result_id'], visit['task_id'], visit['principal'], visit['session_id'], budget)
        status = conn.execute('SELECT status FROM task_results WHERE id=?', (callback['result_id'],)).fetchone()[0]
        _require(status == callback['status'], 'hold_callback_conflict')
        if cancellation:
            digest = _generic_callback_digest(conn, callback['result_id'], budget)
        else:
            payload = generic_callback_bytes(conn, callback['result_id'], budget)
            digest = None if payload is None else payload[1]
        if digest is not None:
            _require(digest == callback['carrier_digest'], 'hold_callback_conflict')
    return dict(kind='settled', terminal_digest=terminal_digest, outer_digest=outer_digest,
                result_id=None if callback is None else callback['result_id'])


def generic_hold_cause(conn: sqlite3.Connection, *, run: dict, visit: dict, compiled: object,
                       budget: _ReadBudget, unavailable: bool = False) -> tuple[str, dict]:
    from runtime.platform.session_backend import TerminalReason
    from runtime.workflows.sequential_definition import SequentialContractError, parse_generic_callback
    proof = generic_closed_visit(conn, run=run, visit=visit, budget=budget)
    if unavailable:
        _require(proof['kind'] == 'never_launched', 'hold_unavailable_conflict')
        return 'assignee_unavailable', proof
    _require(proof['kind'] == 'settled', 'hold_cause_unavailable')
    if proof['result_id'] is None:
        terminal, _ = _proof(conn, run['id'], visit['id'], 'terminal', budget)
        reason = terminal['receipt']['terminal_reason']
        _require(reason in tuple(item.value for item in TerminalReason if item != TerminalReason.SUCCESS)
                 + ('failed', 'error'), 'hold_cause_unavailable')
        return ('task_timed_out_without_result' if reason == TerminalReason.TIMEOUT.value else 'task_failed_without_result'), proof
    terminal, _ = _proof(conn, run['id'], visit['id'], 'terminal', budget)
    reason = terminal['receipt']['terminal_reason']
    _require(reason in tuple(item.value for item in TerminalReason) + ('failed', 'error'), 'hold_cause_unavailable')
    if reason == TerminalReason.TIMEOUT.value:
        return 'task_timed_out_after_result', proof
    if reason != TerminalReason.SUCCESS.value:
        return 'task_failed_after_result', proof
    payload = generic_callback_bytes(conn, proof['result_id'], budget)
    if payload is None:
        return 'result_too_large', proof
    if not payload[0]:
        return 'result_bad_shape', proof
    node = next(item for item in compiled.definition.nodes if item.id == visit['node_id'])
    try:
        callback = parse_generic_callback(payload[0], contract=node.output_contract.type)
    except SequentialContractError as exc:
        return ('result_too_large' if str(exc) in ('encoded_bounds', 'generic_output_bounds', 'callback_metadata_bounds')
                else 'result_bad_shape'), proof
    edge = dict(node.outcomes).get(callback.outcome)
    if edge is None:
        return 'unknown_outcome', proof
    if getattr(edge, 'consume_counter', None) is not None:
        from runtime.workflows.sequential_contracts import GenericLimits
        from runtime.workflows.sequential_definition import generic_limits, parse_generic_start
        limits = generic_limits(compiled, GenericLimits.model_validate_json(run['original_limits_bytes']),
                                parse_generic_start(run['start_bytes']).lower_limits)
        count = conn.execute('SELECT revisions FROM workflow_seq_node_counters WHERE run_id=? AND node_id=?',
                             (run['id'], edge.node)).fetchone()
        if (0 if count is None else count[0]) >= dict(limits.node_revisions)[edge.node]:
            return 'revision_limit_exhausted', proof
    _require(False, 'hold_cause_unavailable')


def hold_generic_uncommitted(conn: sqlite3.Connection, db: object, *, org_slug: str, run_id: str,
                              visit_id: str, expected_cas: int, compiled: object,
                              authority: object, capture: object, unavailable: bool = False) -> str:
    """Persist Held without a synthetic result or successor. Consumer is unmounted."""
    from runtime.workflows.authority import generic_assignment_authority
    from runtime.workflows.sequential_assignments import recheck_launch_assignment, selection_agent
    from runtime.workflows.sequential_control import _append_event, _move, validate_control_write
    from runtime.workflows.sequential_engine import _engine_context, _engine_rows
    from runtime.workflows.sequential_history import _sql_budget
    budget = _ReadBudget()
    with _sql_budget(conn, budget):
        run = _engine_context(conn, db, org_slug=org_slug, run_id=run_id, compiled=compiled,
            authority=authority, capture=capture, budget=budget)
        _require(run['engine_version'] == 2 and run['state'] == 'running'
            and run['progress'] != 'cancelling' and run['cas'] == expected_cas
            and run['active_visit_id'] == visit_id, 'hold_cas_conflict')
        visit = _engine_rows(conn, 'workflow_seq_visits', budget, 'id=?', (visit_id,))[0]
        _require(visit['selected'] == 1, 'hold_visit_conflict')
        if unavailable:
            assignment = _engine_rows(conn, 'workflow_seq_assignments', budget, 'id=?', (visit['assignment_id'],))[0]
            value = _decode(assignment['snapshot_bytes'], budget)
            current = generic_assignment_authority(capture, org_slug=org_slug)
            target = next((item for item in current['agents'] if item['name'] == visit['principal']), None)
            node = next(item for item in compiled.definition.nodes if item.id == visit['node_id'])
            requirement = dict(compiled.definition.roles)[node.role]
            team = next((item for item in current['teams'] if target is not None and item['name'] == target['team']), None)
            eligible = target is not None and team is not None and target['team'] == _decode(run['assignment_snapshot_bytes'], budget)['team']
            eligible = eligible and (target['role'] == 'manager' and team['manager'] == dict(kind='agent', principal=target['name'])
                if requirement.eligibility == 'registered-team-manager'
                else target['role'] == 'worker' and target['name'] in team['workers'])
            _require(not eligible or target['status'] != 'active' or not target['executor']
                or recheck_launch_assignment(value, selection_agent(target)) is not None, 'assignee_available')
        cause, proof = generic_hold_cause(conn, run=run, visit=visit, compiled=compiled,
                                         budget=budget, unavailable=unavailable)
        event_id = _append_event(conn, run, kind='held', operation_key='hold-' + visit_id,
            payload=dict(visit_id=visit_id, node=visit['node_id'], cause=cause, closure=proof), budget=budget,
            resulting_state='held',
            source_visit=visit_id, source_result=proof['result_id'])
        conn.execute("UPDATE workflow_seq_visits SET state='held',selected=0 WHERE id=? AND selected=1", (visit_id,))
        _move(conn, run, event_id, state='held', progress=None, active_visit_id=None,
              held_node_id=visit['node_id'], held_event_id=event_id)
    validate_control_write(conn, org_slug=org_slug, deadline=budget.deadline,
                           reserve_cancellation=True)
    return event_id

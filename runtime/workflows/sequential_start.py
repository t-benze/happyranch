"""Result-owned sequential admission; source settlement and publication are separate."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Literal

from runtime.infrastructure.db.tasks import RetryClaim
from runtime.infrastructure.task_pause_controls import PauseControlError
from runtime.infrastructure.workflow_task_ownership import selected_owner
from runtime.models import CompletionReport, NextStep, TaskRecord, TaskStatus
from runtime.orchestrator import prompt_loader
from runtime.orchestrator.org_config import load_org_config
from runtime.orchestrator.task_pause import current_invocation
from runtime.workflows.authority import AuthorityAdmissionCapture, WorkflowAuthorityError
from runtime.workflows.coding_authority_history import _bounded_rows, _decoded
from runtime.workflows.sequential_contracts import Closed, Limits, StartCommand
from runtime.workflows.sequential_definition import CompiledDefinition, parse_start, validate_start
from runtime.workflows.sequential_engine import (
    AdmissionIds, SequentialRefusal, _bound_agents, _engine_rows, _identity, admit_uncommitted,
)
from runtime.workflows.sequential_history import StorageSettings, _ReadBudget, _bytes, _parse, _sql_budget
from runtime.workflows.sequential_run_validation import _pins, _selected_layout
from runtime.workflows.sequential_venue import check_venue_start


class SequentialStartDecision(Closed):
    action: Literal['workflow_start']
    workflow: StartCommand



def sequential_start_decision_schema() -> dict:
    """Closed advertised adapter schema without local definition references."""
    schema = SequentialStartDecision.model_json_schema()
    definitions = schema.pop('$defs', {})

    def inline(value: Any) -> Any:
        if isinstance(value, dict):
            if '$ref' in value:
                return inline(definitions[value['$ref'].rsplit('/', 1)[-1]])
            return {key: inline(item) for key, item in value.items()
                    if not (key == 'mapping' and 'propertyName' in value)}
        if isinstance(value, list):
            return [inline(item) for item in value]
        return value

    return inline(schema)


def _check(value: bool, code: str = 'sequential_start_unavailable') -> None:
    if not value:
        raise SequentialRefusal(code)


def _selection(conn: sqlite3.Connection, *, org_slug: str, command: StartCommand,
               budget: _ReadBudget) -> tuple[CompiledDefinition, dict, StorageSettings]:
    """Reuse the complete selected layout and independently validated pin readers."""
    metadata = _selected_layout(conn, budget)
    definitions = list(_bounded_rows(conn, 'workflow_seq_definitions', ('id',), (('text',),), budget,
        where='identity=? AND version=?', args=(command.definition.identity, command.definition.version), maximum=1))
    settings = list(_bounded_rows(conn, 'workflow_seq_settings', ('id',), (('text',),), budget,
        where='org_slug=? AND definition_identity=? AND generation=?',
        args=(org_slug, command.definition.identity, command.settings.generation), maximum=1))
    _check(len(definitions) == len(settings) == 1)
    compiled, setting, snapshot = _pins(conn, metadata, org_slug=org_slug,
        definition_id=definitions[0][0], settings_id=settings[0][0], budget=budget)
    _check(command.definition == compiled.pin and snapshot.admission == 'Allowed'
           and command.settings.snapshot_digest == setting['snapshot_digest']
           and command.bindings == snapshot.bindings
           and all(getattr(command.lower_limits, key) <= getattr(snapshot.limits, key)
                   for key in Limits.model_fields))
    # D1: the pinned snapshot, never the request or a freshly invented allowance.
    validate_start(command, compiled, original_limits=snapshot.limits)
    budget.check()
    return compiled, setting, snapshot


def precheck_start(org: Any, workflow: dict, *, source_task_id: str) -> None:
    """Fresh SQL-free venue observation, then one coherent read-only pin scope."""
    check_venue_start(org_slug=org.slug, org_root=org.root, db_path=org.db.path)
    budget = _ReadBudget()
    command = _parse(_bytes(workflow), parse_start, budget)
    with org.db.coherent_read_view() as conn, _sql_budget(conn, budget):
        _selection(conn, org_slug=org.slug, command=command, budget=budget)
        _ordinary_source(conn, org, source_task_id, budget)


def precheck_completion(org: Any, workflow: dict, *, task_id: str, principal: str,
                        session_id: str, status: str, waiting_on_job_ids: list[str] | None) -> None:
    """Check the original ordinary callback binding before persisting its result.

    The caller holds the existing session binding lease. Request identities are
    comparisons only; the tracker supplies the source used by the start reader.
    The committed-result consumer still owns INTEGER/native admission proof.
    """
    context = org.sessions.get_context_by_session(session_id)
    _check(context == (org.slug, task_id, principal)
           and org.sessions.get_active(task_id, principal) == session_id
           and not org.sessions.is_recovery_session(task_id, principal, session_id)
           and status == 'completed' and not waiting_on_job_ids)
    _, source_task_id, _ = context
    precheck_start(org, workflow, source_task_id=source_task_id)


def is_start_result(prior: dict) -> bool:
    """Recognize the saved native decision, never a supplied replay selector.

    Native decision_json is not the canonical command encoding. The literal
    prefilter only avoids decoding unrelated decisions; the parsed discriminator
    determines whether the original sequential completion owns exact replay.
    """
    raw = prior.get('decision_json')
    if type(raw) is not str or 'workflow-sequential-start@1' not in raw:
        return False
    budget = _ReadBudget()
    budget.reserve(48 * len(raw) + 512)
    value = _decoded(json.loads(raw), budget)
    budget.check()
    return (type(value) is dict and value.get('action') == 'workflow_start'
            and type(value.get('workflow')) is dict
            and value['workflow'].get('format') == 'workflow-sequential-start@1')


def _callback(conn: sqlite3.Connection, org: Any, claim: RetryClaim, decision: NextStep,
              report: CompletionReport, budget: _ReadBudget) -> StartCommand:
    _check(type(claim.result_row_id) is int and claim.result_row_id > 0
           and bool(claim.assigned_agent) and bool(claim.current_session_id)
           and (report.task_id, report.agent) == (claim.task_id, claim.assigned_agent))
    # The selected sequential reader intentionally supports INTEGER/TEXT/BLOB.
    # Native estimated_cost is REAL and is outside callback authority material.
    # The existing native report-binding API below still authenticates the full
    # established completion projection; do not change the frozen reader.
    rows = _engine_rows(conn, 'task_results', budget, 'id=?', (claim.result_row_id,),
                        omit=('estimated_cost',))
    _check(len(rows) == 1)
    row = rows[0]
    _check((row['task_id'], row['agent'], row['session_id'], row['status'], row['waiting_on_job_ids']) ==
           (claim.task_id, claim.assigned_agent, claim.current_session_id, 'completed', None))
    _check(org.db.authority_policy_v2_decision_result_report_binds(
        root_task_id=claim.task_id, spending_result_id=claim.result_row_id, report=report))
    # The reserved native self-evaluation carrier is consumed by its own owner.
    saved = json.loads(row['decision_json'])
    saved.pop('_manager_self_evaluation', None)
    parsed = SequentialStartDecision.model_validate(saved)
    _check(parsed == SequentialStartDecision.model_validate(decision.model_dump(mode='json')))
    return _parse(_bytes(parsed.workflow.model_dump(mode='json')), parse_start, budget)


def _policy(org: Any, claim: RetryClaim) -> int:
    """File-backed manager and native configuration discovery precedes the writer."""
    actor = prompt_loader.load_agent(org.orchestrator._paths, claim.assigned_agent)
    _check(actor is not None and actor.role == 'manager' and bool(actor.executor) and actor.team == claim.team)
    _check(org.teams.executable_manager_for_team(claim.team) == claim.assigned_agent)
    return load_org_config(org.orchestrator._paths).max_revise_rounds


def _ordinary_source(conn: sqlite3.Connection, org: Any, task_id: str,
                     budget: _ReadBudget) -> tuple[dict, dict, set[str]]:
    seen: set[str] = set()
    key = task_id
    source = None
    while key is not None:
        budget.step()
        _check(key not in seen and len(seen) < 20)
        seen.add(key)
        rows = _engine_rows(conn, 'tasks', budget, 'id=?', (key,))
        _check(len(rows) == 1)
        row = rows[0]
        _check(selected_owner(conn, key, org.slug) == ('legacy', None, None)
               and row['cancelled_at'] is None
               and row['status'] not in ('completed', 'failed', 'cancelled', 'superseded')
               and row['dispatched_from_thread_id'] is None)
        if source is None:
            source = row
            _check(row['task_type'] == 'task' and row['status'] == 'in_progress'
                   and row['active_chain'] is None and row['active_fanout'] is None
                   and row['blocked_on_job_ids'] is None and row['block_kind'] is None
                   and row['zombie_flagged_at'] is None and row['completed_at'] is None
                   and org.teams.executable_manager_for_team(row['team']) == row['assigned_agent'])
        _check(row['team'] == source['team'])
        key = row['parent_task_id']
    return source, row, seen


def _source(conn: sqlite3.Connection, org: Any, claim: RetryClaim, invocation: Any,
            capture: AuthorityAdmissionCapture, revise_limit: int, budget: _ReadBudget) -> None:
    _check(not org._runner_admission_closed and org.orchestrator._db is org.db
           and org.db._retry_claim_matches(claim))
    store = org.db._task_pause_store
    _check(invocation is not None and invocation.orch is org.orchestrator
           and store._live.get(invocation.identity) is invocation
           and invocation.owner in ('ordinary', 'v2') and invocation.admitted
           and invocation.attempt_committed and invocation.unsettled is False
           and invocation.recovery_deadline is None
           and (invocation.task.id, invocation.agent, invocation.session_id) ==
               (claim.task_id, claim.assigned_agent, claim.current_session_id))
    _check(org.db.get_active_authority_continue_envelope(claim.task_id) is None,
           'sequential_start_native_continuation_unavailable')
    _check(org.teams.executable_manager_for_team(claim.team) == claim.assigned_agent
           and (revise_limit == 0 or claim.revision < revise_limit))
    snapshot = json.loads(capture.ready.snapshot_bytes)
    managers = [a for a in snapshot['agents'] if a['name'] == claim.assigned_agent]
    _check(len(managers) == 1 and managers[0]['role'] == 'manager'
           and managers[0]['status'] == 'active' and bool(managers[0]['executor'])
           and managers[0]['team'] == claim.team)
    _check(any(t['name'] == claim.team and t['manager'] == dict(kind='agent', principal=claim.assigned_agent)
               for t in snapshot['teams']))
    source, ancestor, seen = _ordinary_source(conn, org, claim.task_id, budget)
    root, pause = store.held_uncommitted(claim.task_id)
    _check(pause is not None and not pause['held'] and root['id'] == ancestor['id'])
    entry = invocation._entry(pause)
    _check(entry['phase'] == 'result_processing' and entry['generation'] == pause['generation']
           and not entry['context'].get('execution_unknown')
           and entry['fingerprint']['count'] + 1 == claim.step
           and entry['fingerprint']['brief_digest'] == hashlib.sha256(source['brief'].encode('utf8')).hexdigest())
    from runtime.workflows.sequential_dispatch import _native_metadata

    terminal = getattr(invocation, 'native_terminal_observation', None)
    _check(type(terminal) is dict)
    try:
        _native_metadata(invocation.task.id, invocation.agent, invocation.session_id, terminal)
    except SequentialRefusal:
        raise SequentialRefusal('sequential_start_unavailable') from None
    _check(terminal.get('terminal_reason') == 'success' and terminal.get('cleanup_error') is None)
    for child in _engine_rows(conn, 'tasks', budget, 'parent_task_id=?', (claim.task_id,)):
        _check(child['status'] in ('completed', 'failed', 'cancelled', 'superseded'))


def _claim_action(org: Any, claim: RetryClaim) -> Any:
    """Only native uncommitted APIs; never run the ordinary self-committing gate."""
    db = org.db
    context = db.authority_policy_v2_completion_dispatch_context(
        root_task_id=claim.task_id, result_row_id=claim.result_row_id)
    if context.kind in ('no_v2', 'later'):
        return None
    if context.kind == 'reserved':
        spent = db._spend_v2_continuation_envelope_uncommitted(
            root_task_id=claim.task_id, manager_agent=context.manager_agent,
            manager_session_id=context.manager_session_id, result_id=context.causal_result_id,
            generation_id=context.generation_id, next_session_id=context.next_session_id,
            spending_result_id=claim.result_row_id, now_dt=datetime.now(timezone.utc))
        _check(spent.status == 'spent')
    else:
        _check(context.kind == 'receipt' and context.decision_state == 'ready')
    claimed = db._claim_v2_decision_dispatch_uncommitted(
        root_task_id=claim.task_id, manager_agent=context.manager_agent, result_id=context.causal_result_id)
    _check(claimed.status == 'claimed')
    return context


def start_in_writer(conn: sqlite3.Connection, *, org: Any, claim: RetryClaim, decision: NextStep,
                    report: CompletionReport, invocation: Any, capture: AuthorityAdmissionCapture,
                    revise_limit: int) -> AdmissionIds:
    """Atomic native ownership + unchanged U3b graph + accepted source parking."""
    _check(conn is org.db._conn and conn.in_transaction)
    budget = _ReadBudget()
    with _sql_budget(conn, budget):
        command = _callback(conn, org, claim, decision, report, budget)
        compiled, setting, snapshot = _selection(conn, org_slug=org.slug, command=command, budget=budget)
        run_id = _identity('run', org.slug, claim.assigned_agent, command.operation_key)
        existing = _engine_rows(conn, 'workflow_seq_runs', budget, 'id=?', (run_id,))
        bound = _bound_agents(compiled, command, capture)
        node = next(n for n in compiled.definition.nodes if n.id == compiled.definition.entry_node)
        carrier = TaskRecord(id='TASK-SEQ-' + hashlib.sha256(_bytes([run_id, 'carrier'])).hexdigest(),
            parent_task_id=claim.task_id, assigned_agent=claim.assigned_agent, team=claim.team,
            task_type='subtask', brief='Sequential structural carrier; no host execution.')
        visit = TaskRecord(id='TASK-SEQ-' + hashlib.sha256(_bytes([run_id, 'visit', 1])).hexdigest(),
            parent_task_id=carrier.id, assigned_agent=bound[node.role]['name'], team=bound[node.role]['team'],
            task_type='subtask', brief='Sequential node ' + node.id + '; follow only the bound node brief and domain supplement.')
        action = None
        if not existing:
            _source(conn, org, claim, invocation, capture, revise_limit, budget)
            action = _claim_action(org, claim)
        else:
            # U3b authenticates the original quartet/command/limits and refuses
            # later-state replay. Never spend or park the source a second time.
            _check(existing[0]['settings_id'] == setting['id'])
        budget.check()
    ids = admit_uncommitted(conn, org.db, org_slug=org.slug, compiled=compiled, settings_row=setting,
        start=_bytes(command.model_dump(mode='json')), source=claim, carrier_task=carrier, visit_task=visit,
        original_limits=snapshot.limits, authority=org.workflow_authority, capture=capture)
    if not existing:
        now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        changed = conn.execute("UPDATE tasks SET block_kind='delegated',note=?,updated_at=? WHERE id=? "
            "AND status='in_progress' AND block_kind IS NULL AND current_session_id=? AND assigned_agent=?",
            ('sequential-owned:' + ids.run_id, now, claim.task_id, claim.current_session_id, claim.assigned_agent))
        _check(changed.rowcount == 1)
        if action is not None:
            ack = org.db._acknowledge_v2_decision_dispatch_uncommitted(
                root_task_id=claim.task_id, manager_agent=action.manager_agent, result_id=action.causal_result_id)
            _check(ack.status == 'applied')
    org.workflow_authority._validate_admission_uncommitted(conn, capture)
    return ids


async def _consume(dispatcher: Any, claim: RetryClaim, decision: NextStep,
                   report: CompletionReport, invocation: Any) -> AdmissionIds:
    org = dispatcher.org
    capture = org.workflow_authority.capture_admission()
    revise_limit = _policy(org, claim)
    async with org.workflow_authority._async_writer_lock:
        async with org.teams_lock:
            async with org.db_lock:
                # Re-read file-backed policy after lock waits, BEFORE the writer.
                _check(_policy(org, claim) == revise_limit)
                with org.workflow_authority.admission_writer(capture) as conn:
                    return start_in_writer(conn, org=org, claim=claim, decision=decision, report=report,
                        invocation=invocation, capture=capture, revise_limit=revise_limit)


def consume_start(dispatcher: Any, claim: RetryClaim, decision: NextStep,
                  report: CompletionReport) -> AdmissionIds:
    org = dispatcher.org
    _check(dispatcher is org.workflow_sequential_dispatch
           and getattr(org.orchestrator, '_workflow_sequential_dispatch', None) is dispatcher)
    # Authenticate the actual committed callback before any fresh observation,
    # capture, native result-action spending or graph allocation.
    try:
        budget = _ReadBudget()
        with org.db.coherent_read_view() as conn, _sql_budget(conn, budget):
            _callback(conn, org, claim, decision, report, budget)
        precheck_start(org, decision.workflow, source_task_id=claim.task_id)
        return dispatcher._on_loop(_consume(dispatcher, claim, decision, report, current_invocation()))
    except SequentialRefusal:
        raise
    except (ValueError, OSError, sqlite3.Error, WorkflowAuthorityError, PauseControlError):
        raise SequentialRefusal('sequential_start_unavailable') from None


def source_callback_replay(org: Any, run_id: str, prior: dict) -> bool:
    """Read-only acknowledgement of the original source quartet and saved command."""
    budget = _ReadBudget()
    with org.db.coherent_read_view() as conn, _sql_budget(conn, budget):
        runs = _engine_rows(conn, 'workflow_seq_runs', budget, 'id=?', (run_id,))
        _check(len(runs) == 1)
        run = runs[0]
        _check(run['org_slug'] == org.slug
               and (run['source_task_id'], run['source_principal'], run['source_session_id'], run['source_result_id']) ==
                   (prior['task_id'], prior['agent'], prior['session_id'], prior['id'])
               and hashlib.sha256(run['start_bytes']).hexdigest() == run['start_digest'])
        command = _parse(run['start_bytes'], parse_start, budget)
        saved = json.loads(prior['decision_json'])
        saved.pop('_manager_self_evaluation', None)
        _check(SequentialStartDecision.model_validate(saved).workflow == command
               and command.operation_key == run['start_operation_key']
               and run_id == _identity('run', org.slug, prior['agent'], command.operation_key))
        budget.check()
        return True


def _source_outer_positive(dispatcher: Any, invocation: Any, *, failed: bool, cleanup: Any,
                           native_entry: dict | None, close_disposition: dict | None,
                           release_disposition: dict | None) -> bool:
    """Read the original native observations; absence never proves removal."""
    from runtime.workflows.sequential_dispatch import _native_metadata

    org = dispatcher.org
    terminal = getattr(invocation, 'native_terminal_observation', None)
    if not (failed is False and cleanup is not None and cleanup.kind == 'safe'
            and not cleanup.processes and invocation.unsettled is False
            and type(terminal) is dict and type(native_entry) is dict
            and type(close_disposition) is dict and type(release_disposition) is dict):
        return False
    # Bound native terminal encoding BEFORE projecting/copying any of its data.
    _native_metadata(invocation.task.id, invocation.agent, invocation.session_id, terminal)
    request, receipt = terminal.get('request'), terminal.get('receipt')
    context = native_entry.get('context')
    if not (type(request) is dict and type(receipt) is dict and type(context) is dict
            and terminal.get('terminal_reason') == 'success' and terminal.get('cleanup_error') is None
            and receipt.get('terminal_reason') == 'success'
            and receipt.get('backend') in ('linux-systemd-cgroup-v2', 'macos-process-group')
            and receipt.get('quiescent') is True and receipt.get('survivors') == []
            and receipt.get('cleanup_status') in ('clean', 'term', 'kill')
            and native_entry.get('phase') == 'result_processing'
            and context.get('execution_unknown', False) is False
            and close_disposition.get('removed') is True
            and close_disposition.get('invocation') == native_entry
            and release_disposition.get('removed') is True
            and release_disposition.get('owner_live') is False
            and release_disposition.get('cleanup_failed') is False
            and type(release_disposition.get('possible_launch')) is bool
            and (release_disposition['possible_launch'] is False or release_disposition.get('quiescent') is True)):
        return False
    _check(invocation.owner in ('ordinary', 'v2') and invocation.orch is org.orchestrator
           and invocation.store is org.db._task_pause_store
           and invocation.admitted is True and invocation.attempt_committed is True
           and invocation.recovery_deadline is None
           and (native_entry.get('id'), native_entry.get('org'), native_entry.get('task_id'),
                native_entry.get('agent'), native_entry.get('session_id'), native_entry.get('owner')) ==
               (invocation.identity, org.slug, invocation.task.id, invocation.agent, invocation.session_id, invocation.owner)
           and (request.get('org'), request.get('invocation_kind'), request.get('logical_id'), request.get('agent_id')) ==
               (org.slug, 'task', invocation.task.id, invocation.agent)
           and type(terminal.get('attempt')) is int and terminal['attempt'] >= 0
           and type(request.get('retry_attempt')) is int and request['retry_attempt'] == terminal['attempt']
           and (release_disposition.get('owner_id'), release_disposition.get('task_id'),
                release_disposition.get('agent'), release_disposition.get('session_id')) ==
               (invocation.identity, invocation.task.id, invocation.agent, invocation.session_id))
    return org.sessions.get_active(invocation.task.id, invocation.agent) is None


def _source_outer_selected(conn: sqlite3.Connection, dispatcher: Any, invocation: Any,
                           run_id: str, budget: _ReadBudget) -> tuple[dict, dict, dict] | None:
    """PK selection under the evidence writer, including the immutable callback."""
    from runtime.workflows.sequential_dispatch import _cancelled

    org = dispatcher.org
    kind, owner, _ = selected_owner(conn, invocation.task.id, org.slug)
    _check(kind == 'seq_source_waiting' and owner == run_id)
    runs = _engine_rows(conn, 'workflow_seq_runs', budget, 'id=?', (run_id,))
    _check(len(runs) == 1)
    run = runs[0]
    _check(run['org_slug'] == org.slug
           and (run['source_task_id'], run['source_principal'], run['source_session_id']) ==
               (invocation.task.id, invocation.agent, invocation.session_id))
    if run['state'] == 'cancelled':
        return None
    visits = _engine_rows(conn, 'workflow_seq_visits', budget, 'id=?', (run['active_visit_id'],))
    _check(len(visits) == 1)
    visit = visits[0]
    if _cancelled(conn, run, visit):
        return None
    _check(run['state'] == 'active' and visit['run_id'] == run_id and visit['selected'] == 1)
    sources = _engine_rows(conn, 'tasks', budget, 'id=?', (invocation.task.id,))
    _check(len(sources) == 1)
    source = sources[0]
    _check((source['assigned_agent'], source['current_session_id'], source['status'], source['block_kind'], source['note']) ==
           (invocation.agent, invocation.session_id, 'in_progress', 'delegated', 'sequential-owned:' + run_id))
    # Ordinary manager callbacks use admission's bounded native reader, not
    # the narrower sequential-visit carrier metadata limit.
    _check(type(run['source_result_id']) is int and run['source_result_id'] > 0)
    results = _engine_rows(conn, 'task_results', budget, 'id=?', (run['source_result_id'],),
                           omit=('estimated_cost',))
    _check(len(results) == 1)
    result = results[0]
    _check(type(result['output_summary']) is str and type(result['decision_json']) is str
           and (result['task_id'], result['agent'], result['session_id'], result['status'], result['waiting_on_job_ids']) ==
           (invocation.task.id, invocation.agent, invocation.session_id, 'completed', None))
    command = _parse(run['start_bytes'], parse_start, budget)
    _check(hashlib.sha256(run['start_bytes']).hexdigest() == run['start_digest'])
    saved = json.loads(result['decision_json'])
    saved.pop('_manager_self_evaluation', None)
    _check(SequentialStartDecision.model_validate(saved).workflow == command
           and command.operation_key == run['start_operation_key']
           and run_id == _identity('run', org.slug, invocation.agent, command.operation_key))
    budget.check()
    return run, visit, result


def source_outer_settled(dispatcher: Any, invocation: Any, *, failed: bool, cleanup: Any,
                         native_entry: dict | None, close_disposition: dict | None,
                         release_disposition: dict | None) -> None:
    """Original source settlement COMMIT precedes the first queue publication."""
    from runtime.workflows.sequential_dispatch import _callback_identity, _evidence, _evidence_id, _native_metadata
    from runtime.workflows.sequential_run_validation import validate_selected_run_uncommitted

    org = dispatcher.org
    _check(dispatcher is org.workflow_sequential_dispatch
           and getattr(org.orchestrator, '_workflow_sequential_dispatch', None) is dispatcher
           and invocation.orch is org.orchestrator)
    with dispatcher._reader() as conn:
        kind, run_id, _ = selected_owner(conn, invocation.task.id, org.slug)
        if kind != 'seq_source_waiting':
            return  # Ordinary non-sequential sources remain excluded.
    if not _source_outer_positive(dispatcher, invocation, failed=failed, cleanup=cleanup,
            native_entry=native_entry, close_disposition=close_disposition, release_disposition=release_disposition):
        return
    budget = _ReadBudget()
    with dispatcher._writer() as conn:
        if not _source_outer_positive(dispatcher, invocation, failed=failed, cleanup=cleanup,
                native_entry=native_entry, close_disposition=close_disposition, release_disposition=release_disposition):
            return
        with _sql_budget(conn, budget):
            selected = _source_outer_selected(conn, dispatcher, invocation, run_id, budget)
            if selected is None:
                return
            run, visit, result = selected
            body = dict(format='workflow-sequential-outer@1', invocation_id=invocation.identity,
                task_id=invocation.task.id, principal=invocation.agent, session_id=invocation.session_id,
                callback=_callback_identity(result), positive=True, tracker_clear=True,
                native_phase=native_entry['phase'], close_removed=True, release_removed=True,
                release_owner=release_disposition['owner_id'], execution_unknown=False)
            _native_metadata(invocation.task.id, invocation.agent, invocation.session_id, dict(body=body))
            prior = _evidence(conn, run_id, None, 'outer')
            if prior is None:
                _check(visit['ordinal'] == 1 and visit['state'] == 'queued'
                       and visit['session_id'] is None and visit['result_id'] is None)
            _evidence(conn, run_id, None, 'outer', body)  # Conflicts raise inside rollback owner.
            budget.check()
        validate_selected_run_uncommitted(conn, org_slug=org.slug, run_id=run_id, scope='evidence',
            evidence_ids=(_evidence_id(run_id, None, 'outer'),))
    if prior is None:
        dispatcher.notify_queued(visit['task_id'], org.orchestrator._queue)

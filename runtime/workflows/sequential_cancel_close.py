"""Explicit human business close; never repairs native ownership or journals."""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, ExitStack
import hashlib
import json
import re
import sqlite3
import time
from typing import Annotated, Any, Callable, Literal
import uuid

from pydantic import Field, StrictInt, StrictStr

from runtime.infrastructure.task_pause_controls import MAX_JOURNAL_BYTES, decode_journal
from runtime.workflows.sequential_contracts import Closed
from runtime.workflows.sequential_control import validate_control_write
from runtime.workflows.sequential_definition import _json, compile_generic_definition
from runtime.workflows.sequential_engine import _engine_rows
from runtime.workflows.sequential_history import _ReadBudget, _bytes, _sql_budget
from runtime.workflows.sequential_initiator import finish_cancel_and_wake_uncommitted

ACTION = 'sequential_never_launched_cancel_close'
KEY = r'[A-Za-z0-9._:-]{1,128}'
RUN = r'seq-run-[0-9a-f]{64}'


class CancelCloseError(ValueError):
    def __init__(self, code: str, status: int = 409) -> None:
        self.code, self.status = code, status
        super().__init__(code)


def require(ok: bool, code: str, status: int = 409) -> None:
    if not ok:
        raise CancelCloseError(code, status)


class CancelCloseCommand(Closed):
    operation_key: Annotated[StrictStr, Field(pattern='^' + KEY + '$')]
    run_id: Annotated[StrictStr, Field(pattern='^' + RUN + '$')]
    expected_cas: Annotated[StrictInt, Field(ge=0)]


class CancelCloseReceipt(Closed):
    version: Literal[1]
    code: Literal['cancel_closed']
    committed: Literal[True]
    run_id: StrictStr
    visit_id: StrictStr
    task_id: StrictStr
    operation_key: StrictStr
    before_cas: StrictInt
    after_cas: StrictInt
    event_id: StrictStr
    state: Literal['finished']
    terminal_outcome: Literal['cancelled']
    journal_sha256: Annotated[StrictStr, Field(pattern='^[0-9a-f]{64}$')]
    native_cleanup: Literal['unresolved_unchanged']


def _object(raw: bytes) -> dict:
    require(type(raw) is bytes and 0 < len(raw) <= 16384, 'invalid_request', 422)
    def unique(pairs: list) -> dict:
        result = {}
        for key, value in pairs:
            require(key not in result, 'invalid_request', 422)
            result[key] = value
        return result
    value = json.loads(raw.decode('utf8'), object_pairs_hook=unique)
    require(type(value) is dict, 'invalid_request', 422)
    return value


def parse_command(raw: bytes) -> CancelCloseCommand:
    try:
        command = CancelCloseCommand.model_validate(_object(raw))
        require(re.fullmatch(KEY, command.operation_key) is not None
            and re.fullmatch(RUN, command.run_id) is not None, 'invalid_request', 422)
        return command
    except (ValueError, TypeError, RecursionError):
        raise CancelCloseError('invalid_request', 422) from None


def _exists(conn: sqlite3.Connection, sql: str, args: tuple, budget: _ReadBudget) -> bool:
    budget.step()
    return conn.execute('SELECT EXISTS(' + sql + ')', args).fetchone()[0] == 1


def _receipt(conn: sqlite3.Connection, run: dict, key: str, command: CancelCloseCommand | None,
             budget: _ReadBudget) -> dict | None:
    # Retained task/visit identities survive clearing the active cursor.
    rows = conn.execute('SELECT a.id,a.task_id,a.agent,typeof(a.payload),length(CAST(a.payload AS BLOB)) '
        'FROM audit_log a JOIN workflow_seq_visits v ON v.task_id=a.task_id '
        'WHERE v.run_id=? AND a.action=? ORDER BY a.id LIMIT 129', (run['id'], ACTION)).fetchall()
    require(len(rows) <= 128, 'evidence_unavailable', 503)
    found, keys = None, set()
    for audit_id, task_id, agent, kind, size in rows:
        require(agent == 'founder' and kind == 'text' and type(size) is int and size <= 16384, 'evidence_unavailable', 503)
        budget.reserve(48 * size + 512)
        saved = _json(conn.execute('SELECT CAST(payload AS BLOB) FROM audit_log WHERE id=?',
            (audit_id,)).fetchone()[0], 16384)
        require(saved.keys() == {'command', 'response'}, 'evidence_unavailable', 503)
        require(type(saved.get('response')) is dict and type(saved['response'].get('version')) is int
            and saved['response'].get('committed') is True, 'evidence_unavailable', 503)
        try:
            prior = parse_command(_bytes(saved['command']))
            receipt = CancelCloseReceipt.model_validate(saved['response'], strict=True).model_dump()
        except ValueError:
            raise CancelCloseError('evidence_unavailable', 503) from None
        require(prior.run_id == run['id'] and prior.operation_key not in keys
            and receipt['run_id'] == run['id'] and receipt['task_id'] == task_id
            and receipt['operation_key'] == prior.operation_key
            and receipt['before_cas'] == prior.expected_cas and receipt['after_cas'] == prior.expected_cas + 1
            and (run['state'], run['terminal_outcome'], run['cas'], run['last_event_id']) ==
                ('finished', 'cancelled', receipt['after_cas'], receipt['event_id']), 'evidence_unavailable', 503)
        links = conn.execute('SELECT 1 FROM workflow_seq_visits v JOIN workflow_seq_events e ON e.run_id=v.run_id '
            "WHERE v.id=? AND v.run_id=? AND v.task_id=? AND v.state='cancelled' AND v.selected=0 "
            "AND e.id=? AND e.kind='cancellation_finished'", (receipt['visit_id'], run['id'], task_id,
                receipt['event_id'])).fetchone()
        require(links is not None, 'evidence_unavailable', 503)
        keys.add(prior.operation_key)
        if prior.operation_key == key:
            require(command is None or prior == command, 'operation_key_conflict')
            found = receipt
    require(len(keys) <= 1, 'evidence_unavailable', 503)
    return found


def _journal(conn: sqlite3.Connection, org_slug: str, task: dict, budget: _ReadBudget) -> str:
    root, seen = task, set()
    while root['parent_task_id'] is not None:
        budget.step()
        require(root['id'] not in seen and len(seen) < 1024, 'history_conflict')
        seen.add(root['id'])
        parents = _engine_rows(conn, 'tasks', budget, 'id=?', (root['parent_task_id'],))
        require(len(parents) == 1, 'history_conflict')
        root = parents[0]
    size = conn.execute('SELECT typeof(admission_journal_json),length(CAST(admission_journal_json AS BLOB)) '
        'FROM task_pause_controls WHERE root_task_id=?', (root['id'],)).fetchone()
    require(size is not None and size[0] == 'text' and size[1] <= MAX_JOURNAL_BYTES, 'evidence_unavailable', 503)
    budget.reserve(48 * size[1] + 512)
    raw, generation, held, version = conn.execute('SELECT admission_journal_json,generation,held,schema_version FROM task_pause_controls '
        'WHERE root_task_id=?', (root['id'],)).fetchone()
    require(version == 1 and type(generation) is int and generation >= 0
        and held == generation % 2, 'evidence_unavailable', 503)
    journal = decode_journal(raw)
    for entry in journal['entries']:
        budget.step()
        require(entry['org'] == org_slug and entry['root_task_id'] == root['id']
            and entry['generation'] <= generation, 'evidence_unavailable', 503)
        if entry['task_id'] != task['id']:
            continue
        context = entry['context']
        require(entry['phase'] not in ('possible_launch', 'running', 'result_processing', 'action_started')
            and entry['started_at'] is None and entry['captured_generation'] is None
            and not {'recovery_return', 'job_submission'}.intersection(context), 'journal_launch_evidence')
        for audit_id in context.get('audit_ids', []):
            budget.step()
            audit = conn.execute('SELECT task_id,action FROM audit_log WHERE id=?', (audit_id,)).fetchone()
            require(audit is not None and audit[0] == task['id'], 'evidence_unavailable', 503)
            require(not (audit[1] in ('session_start', 'authority_policy_session_binding',
                'authority_policy_selector_session_binding', 'task_native_launch_boundary')
                or audit[1].startswith('repository_preparation')), 'journal_launch_evidence')
    return hashlib.sha256(raw.encode()).hexdigest()


def _proof(conn: sqlite3.Connection, org_slug: str, run: dict, command: CancelCloseCommand,
           budget: _ReadBudget) -> tuple[dict, str]:
    require(run['engine_version'] == 2, 'unsupported_engine', 422)
    require(run['state'] != 'finished', 'already_closed')
    require((run['state'], run['progress'], run['terminal_outcome']) ==
        ('running', 'cancelling', None), 'run_not_cancelling')
    require(run['cas'] == command.expected_cas, 'stale_state')
    visits = _engine_rows(conn, 'workflow_seq_visits', budget, 'run_id=? AND id=?',
        (run['id'], run['active_visit_id']))
    require(len(visits) == 1 and (visits[0]['selected'], visits[0]['state'], visits[0]['session_id']) ==
        (1, 'queued', None), 'visit_not_never_bound')
    visit = visits[0]
    tasks = _engine_rows(conn, 'tasks', budget, 'id=?', (visit['task_id'],))
    require(len(tasks) == 1 and tasks[0]['assigned_agent'] == visit['principal'], 'history_conflict')
    task, tid = tasks[0], visit['task_id']
    require(task['status'] == 'cancelled' and task['cancelled_at'] is not None
        and task['completed_at'] is None, 'visit_task_not_cancelled')
    require(not _exists(conn, "SELECT 1 FROM audit_log WHERE task_id=? AND action='session_start'", (tid,), budget),
        'session_start_present')
    require(task['current_session_id'] is None and task['executor_pid'] is None
        and task['orchestration_step_count'] == 0
        and not _exists(conn, 'SELECT 1 FROM authority_policy_v2_session_bindings WHERE root_task_id=?', (tid,), budget)
        and not _exists(conn, "SELECT 1 FROM audit_log WHERE task_id=? AND action IN "
            "('authority_policy_session_binding','authority_policy_selector_session_binding')", (tid,), budget),
        'executor_admission_present')
    for query, args, code in (
        ('SELECT 1 FROM jobs WHERE task_id=?', (tid,), 'job_present'),
        ("SELECT 1 FROM audit_log WHERE task_id=? AND substr(action,1,22)='repository_preparation'",
            (tid,), 'preparation_audit_present'),
        ("SELECT 1 FROM audit_log WHERE task_id=? AND action='task_native_launch_boundary'", (tid,), 'launch_boundary_present'),
        ('SELECT 1 FROM workflow_seq_evidence WHERE visit_id=? OR task_id=?', (visit['id'], tid), 'workflow_evidence_present'),
        ('SELECT 1 FROM task_results WHERE task_id=?', (tid,), 'callback_result_present'),
    ):
        require(not _exists(conn, query, args, budget), code)
    require(all(visit[k] is None for k in ('result_id', 'result_bytes', 'result_digest',
        'verified_checks_bytes', 'generic_checks_bytes')), 'callback_result_present')
    require(not _exists(conn, 'SELECT 1 FROM workflow_seq_outputs WHERE visit_id=? OR task_id=?',
        (visit['id'], tid), budget), 'workflow_output_present')
    require(not _exists(conn, 'SELECT 1 FROM workflow_seq_events WHERE source_visit_id=? OR source_result_id IN '
        '(SELECT id FROM task_results WHERE task_id=?)', (visit['id'], tid), budget), 'workflow_result_event_present')
    return visit, _journal(conn, org_slug, task, budget)


def close_uncommitted(org: Any, conn: sqlite3.Connection, capture: Any, *, run_id: str,
                      key: str, command: CancelCloseCommand | None, budget: _ReadBudget) -> dict:
    """Caller owns the authority writer/read snapshot; no native settlement."""
    runs = _engine_rows(conn, 'workflow_seq_runs', budget, 'id=? AND org_slug=?', (run_id, org.slug))
    require(len(runs) == 1, 'run_not_found', 404)
    run = runs[0]
    replay = _receipt(conn, run, key, command, budget)
    if replay is not None:
        validate_control_write(conn, org_slug=org.slug, deadline=budget.deadline,
            run_ids=frozenset((run_id,)), source_ids=frozenset((run['source_task_id'],)))
        return replay
    require(command is not None, 'operation_not_found', 404)
    visit, digest = _proof(conn, org.slug, run, command, budget)
    validate_control_write(conn, org_slug=org.slug, deadline=budget.deadline,
        run_ids=frozenset((run_id,)), source_ids=frozenset((run['source_task_id'],)))
    pins = _engine_rows(conn, 'workflow_seq_definitions', budget, 'id=?', (run['definition_id'],))
    require(len(pins) == 1, 'history_conflict')
    compiled = compile_generic_definition(pins[0]['definition_bytes'], source_digest=pins[0]['source_digest'])
    event_id = finish_cancel_and_wake_uncommitted(conn, org.db, org_slug=org.slug, run=run,
        compiled=compiled, authority=org.workflow_authority, capture=capture)
    receipt = CancelCloseReceipt(version=1, code='cancel_closed', committed=True, run_id=run_id,
        visit_id=visit['id'], task_id=visit['task_id'], operation_key=key, before_cas=run['cas'],
        after_cas=run['cas'] + 1, event_id=event_id, state='finished', terminal_outcome='cancelled',
        journal_sha256=digest, native_cleanup='unresolved_unchanged').model_dump()
    org.db.insert_audit_log_uncommitted(visit['task_id'], 'founder', ACTION,
        {'command': command.model_dump(), 'response': receipt}, canonical_payload=True)
    validate_control_write(conn, org_slug=org.slug, deadline=budget.deadline,
        run_ids=frozenset((run_id,)), source_ids=frozenset((run['source_task_id'],)))
    budget.check()
    return receipt


async def operation(org: Any, *, run_id: str, key: str, command: CancelCloseCommand | None,
                    revalidate: Callable[[], None]) -> dict:
    """Same authority components/order; immediate synchronous lock refusal."""
    require(re.fullmatch(RUN, run_id) is not None and re.fullmatch(KEY, key) is not None, 'invalid_request', 422)
    deadline = time.monotonic() + 5.0
    authority = org.workflow_authority
    capture = authority.capture_admission()
    budget = _ReadBudget(deadline=deadline)
    async with AsyncExitStack() as async_stack:
        for lock in (authority._async_writer_lock, org.teams_lock, org.db_lock):
            try:
                await asyncio.wait_for(lock.acquire(), timeout=max(0.001, deadline - time.monotonic()))
            except TimeoutError:
                raise CancelCloseError('control_unavailable', 503) from None
            async_stack.callback(lock.release)
        with ExitStack() as fences:
            for name in capture.profile_names:
                fences.enter_context(authority._profile_coordinator.profile_read(name))
                authority._profile_coordinator._assert_no_active_operation(name)
            for lock in (authority._publisher_lock, org.db._lock):
                require(time.monotonic() < deadline and lock.acquire(blocking=False), 'control_unavailable', 503)
                fences.callback(lock.release)
            require(not org.db._conn.in_transaction, 'control_unavailable', 503)
            revalidate()
            if command is None:
                with org.db.coherent_read_view() as conn, _sql_budget(conn, budget):
                    return close_uncommitted(org, conn, capture, run_id=run_id, key=key, command=None, budget=budget)
            owner = 'workflow-admission:' + uuid.uuid4().hex
            authority._acquire_lease(owner)
            try:
                with authority._admission_transaction() as conn, _sql_budget(conn, budget):
                    authority._validate_admission_uncommitted(conn, capture)
                    revalidate()
                    return close_uncommitted(org, conn, capture, run_id=run_id, key=key, command=command, budget=budget)
            finally:
                authority._release_lease(owner)

"""Sequential native consumer and addressed document/Coding composition.

Native summaries retain the carrier. Only a post-outer current-authority writer
may supply explicit bytes to U3c. Claims never issue domain/access witnesses.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import re
import sqlite3
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, get_args

from runtime.infrastructure.database import completion_result_payload_matches
from runtime.infrastructure.workflow_task_ownership import selected_owner
from runtime.models import TaskRecord
from runtime.platform.session_backend import CleanupStatus, TerminalReason
from runtime.workflows.authority import WorkflowAuthorityError
from runtime.workflows.sequential_contracts import Check, NextEdge, SETTLEMENT_CHECK, TerminalEdge
from runtime.workflows.sequential_definition import CompiledDefinition, _json, compile_definition, parse_result, parse_start, parse_transition
from runtime.workflows.sequential_engine import (
    SequentialRefusal, _bound_agents, _engine_rows, accept_result_uncommitted, check_evidence_size,
    check_output_size, return_uncommitted, transition_uncommitted,
)
from runtime.workflows.sequential_document_access import (
    authorize_document_input, build_document_witness, document_binding_context,
    document_only, document_supplement,
)
from runtime.workflows.sequential_history import _ReadBudget, _bytes, _selection, _sql_budget
from runtime.workflows.sequential_run_validation import validate_selected_run_uncommitted
from runtime.workflows.sequential_business_hold import BusinessHoldCause, hold_body, hold_id
from runtime.workflows.sequential_coding_output import (
    authorize_coding_input, build_coding_witness, coding_assessment_supplement, coding_binding_context, coding_graph, coding_supplement,
)

CARRIER_LIMIT = 1414488
CARRIER_METADATA_LIMIT = 16384
_SUCCESS = ('produced', 'APPROVE', 'PASS', 'merged')
_TOKEN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}', re.ASCII)
_DIGEST = re.compile(r'[0-9a-f]{64}', re.ASCII)
_RESULT_COLUMNS = ('id', 'task_id', 'agent', 'session_id', 'status', 'output_summary',
                   'decision_json', 'confidence_score', 'risks_flagged', 'output_dir',
                   'waiting_on_job_ids', 'verdict', 'local_ci')


def _check(value: bool, code: str = 'sequential_carrier_identity_conflict') -> None:
    if not value:
        raise SequentialRefusal(code)


def _unique(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        _check(key not in result, 'sequential_carrier_invalid')
        result[key] = value
    return result


def _no_number(value: str) -> None:
    raise SequentialRefusal('sequential_carrier_invalid')


def _carrier(summary: str, *, contract: str, checks: tuple[str, ...],
             status: str, verdict: str | None) -> tuple[dict, bytes]:
    """Transport decode -> imported ACTUAL-byte gate -> domain decode/hash.

    Base64 characters are not JSON nodes. Never feed this wrapper to a retained
    full-reader decoder or to parse_result's independent 64KiB envelope parser.
    """
    invalid = 'sequential_carrier_invalid'
    try:
        _check(type(summary) is str, invalid)
        _check(len(summary) <= CARRIER_LIMIT, 'sequential_carrier_limit_exceeded')
        raw = summary.encode('utf8')
        _check(len(raw) <= CARRIER_LIMIT, 'sequential_carrier_limit_exceeded')
        depth = 0
        quoted = escaped = False
        containers = 0
        for byte in raw:
            if quoted:
                if escaped:
                    escaped = False
                elif byte == 92:
                    escaped = True
                elif byte == 34:
                    quoted = False
            elif byte == 34:
                quoted = True
            elif byte in (91, 123):
                depth += 1
                containers += 1
                _check(depth <= 4 and containers <= 11, invalid)
            elif byte in (93, 125):
                depth -= 1
                _check(depth >= 0, invalid)
        value = json.loads(raw.decode('utf8'), object_pairs_hook=_unique,
                           parse_int=_no_number, parse_float=_no_number, parse_constant=_no_number)
        _check(type(value) is dict and value.keys() == {
            'format', 'outcome', 'content_base64', 'findings', 'capability_claims'}, invalid)
        _check(value['format'] == 'workflow-sequential-callback@1'
               and type(value['outcome']) is str and _TOKEN.fullmatch(value['outcome']) is not None
               and type(value['content_base64']) is str, invalid)
        findings, claims = value['findings'], value['capability_claims']
        _check(type(findings) is list and len(findings) <= 32
               and type(claims) is list and len(claims) <= 8, invalid)
        for text in findings:
            _check(type(text) is str and bool(text.strip()) and '\x00' not in text
                   and len(text.encode('utf8')) <= CARRIER_METADATA_LIMIT, invalid)
        declared = []
        for claim in claims:
            _check(type(claim) is dict and claim.keys() == {'check', 'evidence_digest'}, invalid)
            _check(type(claim['check']) is str and claim['check'] in get_args(Check)
                   and type(claim['evidence_digest']) is str
                   and _DIGEST.fullmatch(claim['evidence_digest']) is not None, invalid)
            declared.append(claim['check'])
        _check(len(set(declared)) == len(declared)
               and declared == [check for check in checks if check in declared], invalid)
        metadata = _bytes({**value, 'content_base64': ''})
        _check(len(metadata) <= CARRIER_METADATA_LIMIT, 'sequential_carrier_limit_exceeded')
        _check(_bytes(value) == raw, invalid)
        content = base64.b64decode(value['content_base64'], validate=True)
        _check(base64.b64encode(content).decode('ascii') == value['content_base64'], invalid)
        check_output_size(contract, content)  # Must precede any domain decoding/hash/INSERT.
        success = value['outcome'] in _SUCCESS
        _check(status in ('completed', 'failed', 'blocked')
               and (status == 'completed' or not success)
               and (verdict is None or verdict == value['outcome']), invalid)
        _check(bool(content) if success else not content and bool(findings) and not claims, invalid)
        if success:
            content.decode('utf8', errors='strict') if contract == 'immutable-document@1' else _json(content, 49152)
        return value, content
    except SequentialRefusal:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, binascii.Error):
        raise SequentialRefusal(invalid) from None


def _native_metadata(task_id: str, agent: str, session_id: str, payload: dict) -> int:
    # Bound encoding work before constructing the aggregate canonical buffer.
    pending = [task_id, agent, session_id, {key: value for key, value in payload.items() if key != 'output_summary'}]
    minimum = 0
    while pending:
        value = pending.pop()
        minimum += 1
        if type(value) is str:
            _check(len(value) <= CARRIER_METADATA_LIMIT, 'sequential_carrier_limit_exceeded')
            try:
                minimum += len(value.encode('utf8')) + 2
            except UnicodeError:
                raise SequentialRefusal('sequential_carrier_invalid') from None
        elif type(value) is dict:
            _check(len(value) <= CARRIER_METADATA_LIMIT, 'sequential_carrier_limit_exceeded')
            pending.extend(value.keys())
            pending.extend(value.values())
        elif type(value) in (list, tuple):
            _check(len(value) <= CARRIER_METADATA_LIMIT, 'sequential_carrier_limit_exceeded')
            pending.extend(value)
        _check(minimum <= CARRIER_METADATA_LIMIT, 'sequential_carrier_limit_exceeded')
    try:
        persisted = {key: value for key, value in payload.items() if key != 'output_summary'}
        for key in ('risks_flagged', 'waiting_on_job_ids'):
            if persisted.get(key) is not None:
                persisted[key] = json.dumps(persisted[key])  # Exact native cell serialization.
        size = len(_bytes(dict(task_id=task_id, agent=agent, session_id=session_id, **persisted)))
    except (ValueError, TypeError, UnicodeError):
        raise SequentialRefusal('sequential_carrier_invalid') from None
    _check(size <= CARRIER_METADATA_LIMIT, 'sequential_carrier_limit_exceeded')
    return size


def _native_result(conn: sqlite3.Connection, result_id: int) -> dict:
    """Only the original PK, with scalar type/raw byte preflight before exposure."""
    _check(type(result_id) is int and result_id > 0)
    scalar = ','.join(f'typeof({column}),length(CAST({column} AS BLOB))' for column in _RESULT_COLUMNS)
    sizes = conn.execute('SELECT ' + scalar + ' FROM task_results WHERE id=?', (result_id,)).fetchone()
    _check(sizes is not None)
    for column, kind, size in zip(_RESULT_COLUMNS, sizes[::2], sizes[1::2], strict=True):
        allowed = ('integer',) if column in ('id', 'confidence_score') else ('text', 'null')
        limit = CARRIER_LIMIT if column == 'output_summary' else CARRIER_METADATA_LIMIT
        _check(kind in allowed and (kind == 'null' or type(size) is int and size <= limit))
    row = conn.execute('SELECT ' + ','.join(_RESULT_COLUMNS) + ' FROM task_results WHERE id=?', (result_id,)).fetchone()
    _check(row is not None)
    result = dict(zip(_RESULT_COLUMNS, row, strict=True))
    _check(type(result['output_summary']) is str)
    return result


def _native_task(conn: sqlite3.Connection, task_id: str) -> dict:
    columns = ('assigned_agent', 'current_session_id', 'status', 'cancelled_at', 'completed_at')
    scalar = ','.join(f'typeof({column}),length(CAST({column} AS BLOB))' for column in columns)
    sizes = conn.execute('SELECT ' + scalar + ' FROM tasks WHERE id=?', (task_id,)).fetchone()
    _check(sizes is not None and all(kind in ('text', 'null') and
        (kind == 'null' or type(size) is int and size <= CARRIER_METADATA_LIMIT)
        for kind, size in zip(sizes[::2], sizes[1::2], strict=True)))
    row = conn.execute('SELECT ' + ','.join(columns) + ' FROM tasks WHERE id=?', (task_id,)).fetchone()
    _check(row is not None)
    return dict(zip(columns, row, strict=True))


def _cancelled(conn: sqlite3.Connection, run: dict, visit: dict) -> bool:
    return run['state'] == 'cancelled' or any(
        task['cancelled_at'] is not None or task['status'] == 'cancelled'
        for task in (_native_task(conn, key) for key in
            (run['source_task_id'], run['carrier_task_id'], visit['task_id'])))


def _discover_result(conn: sqlite3.Connection, visit: dict) -> dict | None:
    # idx_cleanup_results_task_agent_id already owns this bounded discovery.
    ids = conn.execute('SELECT id FROM task_results WHERE task_id=? AND agent=? ORDER BY id DESC LIMIT 2',
                       (visit['task_id'], visit['principal'])).fetchall()
    _check(len(ids) <= 1)
    if not ids:
        return None
    result = _native_result(conn, ids[0][0])
    _check((result['task_id'], result['agent'], result['session_id']) ==
           (visit['task_id'], visit['principal'], visit['session_id']))
    return result


def _callback_identity(result: dict | None) -> dict | None:
    if result is None:
        return None
    return dict(task_id=result['task_id'], principal=result['agent'], session_id=result['session_id'],
                result_id=result['id'], status=result['status'],
                carrier_digest=hashlib.sha256(result['output_summary'].encode('utf8')).hexdigest())


def _evidence_id(run_id: str, visit_id: str | None, kind: str) -> str:
    return hashlib.sha256(_bytes(dict(format='workflow-sequential-evidence-id@1',
        run_id=run_id, visit_id=visit_id, kind=kind))).hexdigest()


def _evidence(conn: sqlite3.Connection, run_id: str, visit_id: str | None, kind: str,
              body: dict | None = None) -> dict | None:
    key = _evidence_id(run_id, visit_id, kind)
    size = conn.execute('SELECT typeof(evidence_bytes),length(CAST(evidence_bytes AS BLOB)) '
                        'FROM workflow_seq_evidence WHERE id=?', (key,)).fetchone()
    if size is not None:
        _check(size[0] == 'blob' and type(size[1]) is int and size[1] <= 16384)
        row = conn.execute('SELECT run_id,visit_id,kind,task_id,principal,session_id,result_id,evidence_bytes,evidence_digest '
                           'FROM workflow_seq_evidence WHERE id=?', (key,)).fetchone()
        _check(tuple(row[:7]) == (run_id, visit_id, kind, None, None, None, None))
        raw = row[7]
        check_evidence_size(raw)
        _check(hashlib.sha256(raw).hexdigest() == row[8])
        value = _json(raw, 16384)
        _check(body is None or raw == _bytes(body))
        return value
    if body is None:
        return None
    raw = _bytes(body)
    check_evidence_size(raw)
    conn.execute('INSERT INTO workflow_seq_evidence(id,run_id,visit_id,kind,evidence_bytes,evidence_digest) '
                 'VALUES(?,?,?,?,?,?)', (key, run_id, visit_id, kind, raw, hashlib.sha256(raw).hexdigest()))
    return body


class SequentialDispatcher:
    """Native ports; Coding declaration provenance does not prove remote effects."""

    def __init__(self, org: Any) -> None:
        self.org = org
        self._queue_live: dict[str, dict] = {}

    @contextmanager
    def _writer(self) -> Iterator[sqlite3.Connection]:
        with self.org.db._lock:
            conn = self.org.db._conn
            _check(not conn.in_transaction)
            conn.execute('BEGIN IMMEDIATE')
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    @contextmanager
    def _reader(self) -> Iterator[sqlite3.Connection]:
        with self.org.db._lock:
            conn = self.org.db._conn
            own = not conn.in_transaction
            if own:
                conn.execute('BEGIN')
            try:
                yield conn
            finally:
                if own:
                    conn.rollback()  # Read-only snapshot; never commits business data.

    def _on_loop(self, coroutine: Any) -> Any:
        loop = self.org.orchestrator._main_loop
        if loop is None or not loop.is_running():
            coroutine.close()
            raise SequentialRefusal('sequential_host_unavailable')
        return asyncio.run_coroutine_threadsafe(coroutine, loop).result()

    def _entry(self, conn: sqlite3.Connection, task_id: str, *, budget: _ReadBudget | None = None) -> tuple[dict, dict, CompiledDefinition]:
        kind, visit_id, _ = selected_owner(conn, task_id, self.org.slug)
        _check(kind == 'seq_visit')
        budget = _ReadBudget() if budget is None else budget
        with _sql_budget(conn, budget):
            visits = _engine_rows(conn, 'workflow_seq_visits', budget, 'id=?', (visit_id,))
            _check(len(visits) == 1)
            visit = visits[0]
            runs = _engine_rows(conn, 'workflow_seq_runs', budget, 'id=?', (visit['run_id'],))
            _check(len(runs) == 1 and runs[0]['org_slug'] == self.org.slug and visit['task_id'] == task_id)
            run = runs[0]
            native = _native_task(conn, task_id)
            _check((native['assigned_agent'], native['current_session_id']) == (visit['principal'], visit['session_id']))
            definitions = _engine_rows(conn, 'workflow_seq_definitions', budget, 'id=?', (run['definition_id'],))
            _check(len(definitions) == 1)
            definition = definitions[0]
            compiled = compile_definition(definition['definition_bytes'], source_digest=definition['source_digest'])
            _check(compiled.pin.definition_digest == definition['definition_digest'])
        return run, visit, compiled

    def _invocation(self, task_id: str, session_id: str) -> Any:
        from runtime.orchestrator.task_pause import current_invocation
        invocation = current_invocation()
        _check(invocation is not None and invocation.owner == 'sequential' and invocation.workflow_dispatcher is self
               and invocation.task.id == task_id and invocation.session_id == session_id
               and invocation is invocation.store._live.get(invocation.identity))
        return invocation

    @asynccontextmanager
    async def callback_admission(self, task_id: str) -> AsyncIterator[Any]:
        with self._reader() as conn:
            _, visit, _ = self._entry(conn, task_id)
            prior = _discover_result(conn, visit)
        if prior is not None:
            async with self.org.db_lock:
                yield None
            return
        capture = self.org.workflow_authority.capture_admission()
        async with self.org.workflow_authority._async_writer_lock:
            async with self.org.teams_lock:
                async with self.org.db_lock:
                    with self.org.workflow_authority._admission_ownership(capture):
                        yield capture  # The route must not await inside this ownership.

    def queue_admission_allowed(self, task_id: str) -> bool:
        try:
            with self._reader() as conn:
                run, visit, _ = self._entry(conn, task_id)
                task = self.org.db.get_task(task_id)
                outer = _evidence(conn, run['id'], None, 'outer')
                return (run['state'] == 'active' and run['active_visit_id'] == visit['id']
                        and visit['state'] == 'queued' and visit['selected'] == 1
                        and visit['session_id'] is None and visit['result_id'] is None
                        and task is not None and task.status.value == 'pending'
                        and task.orchestration_step_count == 0 and task.block_kind is None
                        and task.cancelled_at is None and task.current_session_id is None
                        and not _cancelled(conn, run, visit)
                        and outer is not None and outer.get('positive') is True)
        except (SequentialRefusal, ValueError, TypeError, sqlite3.DatabaseError):
            return False

    def _repository_held(self, conn: sqlite3.Connection, run: dict, visit: dict) -> bool:
        """Authenticate only the pre-result repository hold, without releasing it."""
        from runtime.workflows.sequential_repository_subject import repository_hold_id
        if (run['state'] != 'held' or run['active_visit_id'] != visit['id']
                or visit['state'] != 'held' or visit['selected'] != 1 or visit['result_id'] is not None):
            return False
        row = conn.execute('SELECT id FROM workflow_seq_evidence WHERE id=? AND run_id=? AND visit_id=? '
            "AND kind='observation'", (repository_hold_id(run['id'], visit['id']), run['id'], visit['id'])).fetchone()
        if row is None:
            return False
        validate_selected_run_uncommitted(conn, org_slug=self.org.slug, run_id=run['id'], scope='evidence')
        return True

    def callback_uncommitted(self, *, task_id: str, agent: str, session_id: str,
                             payload: dict, capture: Any = None, budget: Any = None) -> bool:
        conn = self.org.db._conn
        _check(conn.in_transaction)
        run, visit, compiled = self._entry(conn, task_id)
        prior = _discover_result(conn, visit)
        if prior is not None:
            return ((prior['task_id'], prior['agent'], prior['session_id']) == (task_id, agent, session_id)
                    and completion_result_payload_matches(prior, **payload))
        task = _native_task(conn, task_id)
        if ((run['state'], visit['state']) not in (('active', 'running'), ('held', 'held'))
                or run['active_visit_id'] != visit['id'] or visit['selected'] != 1
                or (visit['principal'], visit['session_id']) != (agent, session_id)
                or _cancelled(conn, run, visit) or task['status'] != 'in_progress'
                or (task['assigned_agent'], task['current_session_id']) != (agent, session_id)
                or payload['decision_json'] is not None or payload['waiting_on_job_ids'] is not None
                or payload['status'] not in ('completed', 'failed', 'blocked')):
            return False
        store = getattr(self.org.db, '_task_pause_store', None)
        live = [] if store is None else [inv for inv in store._live.values()
            if inv.owner == 'sequential' and inv.workflow_dispatcher is self and inv.task.id == task_id
            and inv.agent == agent and inv.session_id == session_id]
        if len(live) != 1 or capture is None:
            return False
        self.org.workflow_authority._validate_admission_uncommitted(conn, capture)
        node = next(node for node in compiled.definition.nodes if node.id == visit['node_id'])
        metadata = _native_metadata(task_id, agent, session_id, payload)
        value, content = _carrier(payload['output_summary'], contract=node.output_contract.type,
            checks=node.completion_checks, status=payload['status'], verdict=payload['verdict'])
        if run['state'] == 'held' and (value['outcome'] != 'BLOCK' or not self._repository_held(conn, run, visit)):
            return False
        del value, content
        needed = len(payload['output_summary'].encode('utf8')) + metadata + 4096
        _check(needed <= conn.getlimit(sqlite3.SQLITE_LIMIT_LENGTH), 'sequential_carrier_storage_unavailable')
        self.org.db._insert_task_result(task_id=task_id, agent=agent, session_id=session_id, **payload)
        result_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        result = _native_result(conn, result_id)
        _check((result['task_id'], result['agent'], result['session_id']) == (task_id, agent, session_id)
               and completion_result_payload_matches(result, **payload))
        return True  # Native COMMIT belongs to Database; NO seq accepted fields here.

    def callback_replay(self, *, task_id: str, agent: str, session_id: str, payload: dict) -> bool:
        with self._reader() as conn:
            _, visit, _ = self._entry(conn, task_id)
            result = _discover_result(conn, visit)
            return bool(result is not None and (result['task_id'], result['agent'], result['session_id']) ==
                        (task_id, agent, session_id) and completion_result_payload_matches(result, **payload))

    def terminal(self, task_id: str, agent: str, session_id: str, outcome: Any, *, expected_request: Any = None) -> None:
        invocation = self._invocation(task_id, session_id)
        _check(invocation.agent == agent)
        request = getattr(invocation, 'coding_request', None)
        running = getattr(invocation, 'coding_running', None)
        # D6: same original request/attempt gate and line-parallel safe predicate.
        _check(request is not None and outcome is not None and outcome.request is expected_request
               and expected_request is request and outcome.attempt == 0)
        receipt = None if outcome.receipt is None else json.loads(_bytes(asdict(outcome.receipt)))
        _check(receipt is None or receipt['terminal_reason'] == outcome.terminal_reason.value)
        host = dict(handle_id=None if running is None else running.token,
                    backend=None if running is None else running.backend)
        safe = (receipt is not None and host['handle_id'] is not None and receipt['backend'] == host['backend']
                and type(receipt['backend']) is str
                and receipt['backend'] in ('linux-systemd-cgroup-v2', 'macos-process-group')
                and type(receipt['terminal_reason']) is str
                and receipt['terminal_reason'] in tuple(reason.value for reason in TerminalReason)
                and receipt['quiescent'] is True and type(receipt['cleanup_status']) is str
                and receipt['cleanup_status'] in (CleanupStatus.CLEAN.value, CleanupStatus.TERM.value, CleanupStatus.KILL.value)
                and receipt['survivors'] == [] and outcome.cleanup_error is None)
        if not safe:
            return  # D9: native owner retains uncertainty; never fabricate a held result.
        _check(running.request_id == request.logical_id and request.org == self.org.slug
               and request.agent_id == agent and request.retry_attempt == 0)
        with self._writer() as conn:
            run, visit, _ = self._entry(conn, task_id)
            if _cancelled(conn, run, visit):
                return
            _check((visit['principal'], visit['session_id']) == (agent, session_id))
            result = _discover_result(conn, visit)
            classification = ('success' if receipt['terminal_reason'] == 'success' and result is not None and result['status'] == 'completed'
                else 'failed' if result is not None and result['status'] == 'failed'
                else 'blocked' if result is not None and result['status'] == 'blocked' else 'unknown')
            body = dict(format='workflow-sequential-terminal@1', invocation_id=invocation.identity,
                task_id=task_id, principal=agent, session_id=session_id, classification=classification,
                request=dict(org=request.org, logical_id=request.logical_id, agent_id=request.agent_id,
                             retry_attempt=request.retry_attempt),
                handle_digest=hashlib.sha256(_bytes(dict(token=running.token, request_id=running.request_id,
                                                        backend=running.backend))).hexdigest(),
                receipt={key: receipt[key] for key in ('backend', 'terminal_reason', 'cleanup_status',
                    'quiescent', 'survivors')}, callback=_callback_identity(result))
            existing = _evidence(conn, run['id'], visit['id'], 'terminal')
            if existing is not None:
                _check(existing == body)
                return
            repository_blocked = classification == 'blocked' and self._repository_held(conn, run, visit)
            if classification in ('success', 'failed') or repository_blocked:
                # Native tasks have no terminal blocked status. Retain the actual
                # blocked callback/classification while closing this held task as failed.
                now = datetime.now(timezone.utc).isoformat()
                conn.execute('UPDATE tasks SET status=?,completed_at=?,updated_at=?,block_kind=NULL,final_output_summary=?,final_output_dir=? WHERE id=?',
                    ('completed' if classification == 'success' else 'failed', now, now,
                     result['output_summary'], result['output_dir'], task_id))
            _evidence(conn, run['id'], visit['id'], 'terminal', body)
            validate_selected_run_uncommitted(conn, org_slug=self.org.slug, run_id=run['id'], scope='evidence',
                evidence_ids=(_evidence_id(run['id'], visit['id'], 'terminal'),))

    def outer_settled(self, invocation: Any, *, failed: bool, cleanup: Any, native_entry: dict | None,
                      close_disposition: dict | None, release_disposition: dict | None) -> None:
        if invocation.owner in ('ordinary', 'v2'):
            from runtime.workflows.sequential_start import source_outer_settled
            source_outer_settled(self, invocation, failed=failed, cleanup=cleanup, native_entry=native_entry,
                close_disposition=close_disposition, release_disposition=release_disposition)
            return
        if invocation.owner != 'sequential' or invocation.workflow_dispatcher is not self:
            return
        # Original close/release arguments belong to TaskInvocation.settle's finally.
        safe = (not failed and cleanup is not None and cleanup.kind == 'safe'
                and not cleanup.processes and not invocation.unsettled
                and native_entry is not None and not native_entry['context'].get('execution_unknown')
                and native_entry['phase'] == 'result_processing'
                and close_disposition is not None and close_disposition['removed'] is True
                and release_disposition is not None and release_disposition['removed'] is True
                and release_disposition['owner_id'] == invocation.identity
                and not release_disposition['owner_live'] and not release_disposition['cleanup_failed']
                and (not release_disposition['possible_launch'] or release_disposition['quiescent']))
        tracker_clear = self.org.sessions.get_active(invocation.task.id, invocation.agent) is None
        safe = safe and tracker_clear
        if not safe:
            return
        _check(native_entry['id'] == invocation.identity and native_entry['task_id'] == invocation.task.id
               and native_entry['agent'] == invocation.agent and native_entry['session_id'] == invocation.session_id
               and native_entry['owner'] == invocation.owner)
        result_id = None
        with self._writer() as conn:
            run, visit, _ = self._entry(conn, invocation.task.id)
            if _cancelled(conn, run, visit):
                return
            terminal = _evidence(conn, run['id'], visit['id'], 'terminal')
            if terminal is None:
                return
            _check(terminal['invocation_id'] == invocation.identity
                   and (terminal['task_id'], terminal['principal'], terminal['session_id']) ==
                       (invocation.task.id, invocation.agent, invocation.session_id))
            body = dict(format='workflow-sequential-outer@1', invocation_id=invocation.identity,
                task_id=invocation.task.id, principal=invocation.agent, session_id=invocation.session_id,
                callback=terminal['callback'], positive=True, tracker_clear=tracker_clear,
                native_phase=native_entry['phase'], close_removed=True, release_removed=True,
                release_owner=release_disposition['owner_id'], execution_unknown=False)
            _evidence(conn, run['id'], visit['id'], 'outer', body)
            validate_selected_run_uncommitted(conn, org_slug=self.org.slug, run_id=run['id'], scope='evidence',
                evidence_ids=(_evidence_id(run['id'], visit['id'], 'outer'),))
            if terminal['classification'] == 'success' and terminal['callback'] is not None:
                result_id = terminal['callback']['result_id']
                if run['state'] == visit['state'] == 'held' and visit['result_id'] is None:
                    if self._repository_held(conn, run, visit):
                        return  # Original negative callback drains; no sequential acceptance.
                    # The selected reader authenticated the exact original hold
                    # and proofs. Redelivery needs neither fresh access nor retry.
                    row = conn.execute('SELECT id FROM workflow_seq_evidence WHERE id=?',
                        (hold_id(run['id'], visit['id'], result_id),)).fetchone()
                    _check(row is not None)
                    return
        # The positive outer COMMIT is complete BEFORE capturing fresh authority.
        if result_id is not None:
            self._advance(run['id'], visit_id=visit['id'], result_id=result_id,
                          expected_run=run, expected_visit=visit)

    def _record_business_hold(self, expected_run: dict, expected_visit: dict, result_id: int,
                              cause: BusinessHoldCause) -> dict | None:
        """One independent non-authorizing transaction after advance rollback."""
        with self._writer() as conn:
            budget = _ReadBudget()
            run, visit, compiled = self._entry(conn, expected_visit['task_id'], budget=budget)
            validate_selected_run_uncommitted(conn, org_slug=self.org.slug, run_id=run['id'], scope='evidence')
            with _sql_budget(conn, budget):
                _check((run['id'], visit['id']) == (expected_run['id'], expected_visit['id']))
                if _cancelled(conn, run, visit):
                    return None
                # Validate before deciding that a competing committed transition
                # won; a changed/corrupt CAS is not a successful race disposition.
                anchor = ('cas', 'event_count', 'last_event_id', 'active_visit_id', 'epoch',
                          'content_returns', 'visit_count', 'definition_id', 'settings_id', 'start_digest')
                if any(run[key] != expected_run[key] for key in anchor):
                    _check(run['cas'] > expected_run['cas'] and run['last_event_id'] != expected_run['last_event_id'])
                    return None
                _check(all(visit[key] == expected_visit[key] for key in
                    ('id', 'run_id', 'node_id', 'epoch', 'task_id', 'principal', 'session_id', 'input_selection_digest')))
                result = _discover_result(conn, visit)
                _check(result is not None and result['id'] == result_id)
                key = hold_id(run['id'], visit['id'], result_id)
                if run['state'] == visit['state'] == 'held' and visit['result_id'] is None:
                    row = conn.execute('SELECT evidence_bytes FROM workflow_seq_evidence WHERE id=?', (key,)).fetchone()
                    _check(row is not None)
                    value = _json(row[0], 16384)
                    _check(value['cause'] == cause.value)
                    return value
                _check(run['state'] == 'active' and run['active_visit_id'] == visit['id']
                       and visit['state'] == 'running' and visit['selected'] == 1 and visit['result_id'] is None
                       and _native_task(conn, visit['task_id'])['status'] == 'completed')
                events = _engine_rows(conn, 'workflow_seq_events', budget, 'id=?', (run['last_event_id'],))
                _check(len(events) == 1)
                body = hold_body(conn, run=run, visit=visit, compiled=compiled, result_id=result_id,
                    cause=cause, event=events[0], budget=budget)
                raw = _bytes(body)
                check_evidence_size(raw)
                inserted = conn.execute('INSERT INTO workflow_seq_evidence '
                    '(id,run_id,visit_id,kind,task_id,principal,session_id,result_id,evidence_bytes,evidence_digest) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?)', (key, run['id'], visit['id'], 'observation', visit['task_id'],
                    visit['principal'], visit['session_id'], result_id, raw, hashlib.sha256(raw).hexdigest()))
                _check(inserted.rowcount == 1)
                changed_visit = conn.execute("UPDATE workflow_seq_visits SET state='held' WHERE id=? AND run_id=? "
                    "AND task_id=? AND principal=? AND session_id=? AND state='running' AND selected=1 "
                    'AND result_id IS NULL AND input_selection_digest=?', (visit['id'], run['id'], visit['task_id'],
                    visit['principal'], visit['session_id'], visit['input_selection_digest']))
                changed_run = conn.execute("UPDATE workflow_seq_runs SET state='held' WHERE id=? AND state='active' "
                    'AND active_visit_id=? AND cas=? AND event_count=? AND last_event_id=?',
                    (run['id'], visit['id'], run['cas'], run['event_count'], run['last_event_id']))
                _check(changed_visit.rowcount == changed_run.rowcount == 1)
            validate_selected_run_uncommitted(conn, org_slug=self.org.slug, run_id=run['id'], scope='evidence',
                evidence_ids=(key,))
        return body  # No durable Held claim before successful COMMIT.

    def _authority_unverified(self, task_id: str) -> None:
        """Classify only Coding failures; preserve other domains' error behavior."""
        with self._reader() as conn:
            _, _, compiled = self._entry(conn, task_id)
            if coding_graph(compiled):
                raise SequentialRefusal(BusinessHoldCause.AUTHORITY_UNVERIFIED.value) from None

    def _capture_authority(self, task_id: str) -> Any:
        from runtime.workflows.profile_coordinator import ProfileCoordinatorError
        try:
            return self.org.workflow_authority.capture_admission()
        except (WorkflowAuthorityError, ProfileCoordinatorError, OSError, ValueError):
            self._authority_unverified(task_id)
            raise

    def _compose(self, conn: sqlite3.Connection, run: dict, visit: dict, compiled: CompiledDefinition,
                 result: dict, *, bound: dict[str, dict], budget: _ReadBudget,
                 carrier_digest: str, terminal_digest: str, outer_digest: str
                 ) -> tuple[bytes, bytes, tuple[str, ...], tuple[str, ...]]:
        node = next(node for node in compiled.definition.nodes if node.id == visit['node_id'])
        value, content = _carrier(result['output_summary'], contract=node.output_contract.type,
            checks=node.completion_checks, status=result['status'], verdict=result['verdict'])
        if node.output_contract.type in ('independent-code-review@1', 'code-qa@1'):
            _check(result['verdict'] == value['outcome'], 'sequential_carrier_invalid')
        outputs = []
        recipients: tuple[str, ...] = ()
        receipts: tuple[dict[str, str], ...] = ()
        checks = (SETTLEMENT_CHECK,)
        if value['outcome'] in _SUCCESS:
            _check(not value['capability_claims'], 'sequential_capability_unavailable')
            digest = hashlib.sha256(content).hexdigest()
            if document_only(compiled, budget):
                witness = build_document_witness(org_slug=self.org.slug, run=run, visit=visit, node=node,
                    compiled=compiled, bound=bound, result=result, outcome=value['outcome'], content_digest=digest,
                    carrier_digest=carrier_digest, terminal_digest=terminal_digest, outer_digest=outer_digest,
                    budget=budget)
            else:
                _check(node.output_contract.type in ('code-change@1', 'independent-code-review@1', 'code-qa@1'),
                       'sequential_output_access_unavailable')
                witness = build_coding_witness(conn, org_slug=self.org.slug, run=run, visit=visit, node=node,
                    compiled=compiled, bound=bound, result=result, outcome=value['outcome'], content=content,
                    content_digest=digest, carrier_digest=carrier_digest, terminal_digest=terminal_digest,
                    outer_digest=outer_digest, budget=budget)
            recipients, receipts, checks = witness.recipients, witness.capability_receipts, witness.verified_checks
            outputs = [dict(slot=node.output_contract.slot, contract=node.output_contract.type, epoch=visit['epoch'],
                producer_visit=visit['id'], task_id=visit['task_id'], session_id=visit['session_id'],
                result_id=result['id'], digest=digest)]
        _check(not value['capability_claims'], 'sequential_capability_unavailable')
        envelope = _bytes(dict(format='workflow-sequential-result@1', run_id=run['id'], visit_id=visit['id'],
            definition_digest=compiled.pin.definition_digest, input_selection_digest=visit['input_selection_digest'],
            outcome=value['outcome'], outputs=outputs,
            findings=[dict(source_visit=visit['id'], source_result=result['id'], text=text) for text in value['findings']],
            capability_receipts=receipts))
        parse_result(envelope)
        budget.check()
        return envelope, content, recipients, checks

    def _advance(self, run_id: str, *, visit_id: str, result_id: int,
                 expected_run: dict, expected_visit: dict) -> Any:
        async def advance() -> Any:
            from runtime.workflows.profile_coordinator import ProfileCoordinatorError
            try:
                capture = self._capture_authority(expected_visit['task_id'])
            except SequentialRefusal as refusal:
                if refusal.code != BusinessHoldCause.AUTHORITY_UNVERIFIED.value:
                    raise
                async with self.org.db_lock:
                    return self._record_business_hold(expected_run, expected_visit, result_id,
                                                      BusinessHoldCause.AUTHORITY_UNVERIFIED)
            async with self.org.workflow_authority._async_writer_lock:
                async with self.org.teams_lock:
                    async with self.org.db_lock:
                        try:
                            with self.org.workflow_authority.admission_writer(capture) as conn:
                                budget = _ReadBudget()
                                visits = _engine_rows(conn, 'workflow_seq_visits', budget, 'id=?', (visit_id,))
                                _check(len(visits) == 1 and visits[0]['run_id'] == run_id)
                                run, visit, compiled = self._entry(conn, visits[0]['task_id'], budget=budget)
                                with _sql_budget(conn, budget):
                                    _check((run['id'], visit['id']) == (run_id, visit_id))
                                    task = _native_task(conn, visit['task_id'])
                                    if _cancelled(conn, run, visit):
                                        return None
                                    terminal = _evidence(conn, run_id, visit_id, 'terminal')
                                    outer = _evidence(conn, run_id, visit_id, 'outer')
                                    _check(terminal is not None and outer is not None and outer['positive'] is True
                                           and terminal['classification'] == 'success'
                                           and terminal['invocation_id'] == outer['invocation_id'])
                                    result = _native_result(conn, result_id)
                                    identity = _callback_identity(result)
                                    _check(terminal['callback'] == outer['callback'] == identity
                                           and result['status'] == 'completed'
                                           and (result['task_id'], result['agent'], result['session_id']) ==
                                               (visit['task_id'], visit['principal'], visit['session_id']))
                                    source = hashlib.sha256(_bytes(dict(format='workflow-sequential-advance-id@1',
                                        run_id=run_id, visit_id=visit_id, result_id=result_id))).hexdigest()
                                    transition_key, return_key = 'seq-advance-transition-' + source, 'seq-advance-return-' + source
                                    events = [_engine_rows(conn, 'workflow_seq_events', budget, 'run_id=? AND operation_key=?',
                                        (run_id, key)) for key in (transition_key, return_key)]
                                    transition, returned = (rows[0] if rows else None for rows in events)
                                    c = run['cas']
                                    terminal_edge = False
                                    if transition is None:
                                        _check(returned is None and run['state'] == 'active'
                                               and run['active_visit_id'] == visit_id and visit['state'] == 'running')
                                    else:
                                        _check(transition['kind'] == 'transition'
                                               and hashlib.sha256(transition['event_bytes']).hexdigest() == transition['event_digest'])
                                        record = parse_transition(transition['event_bytes'])
                                        _check((record.source_visit, record.source_result) == (visit_id, result_id))
                                        c = record.prior_cas
                                        terminal_edge = isinstance(record.edge, TerminalEdge)
                                        _check((returned is not None) == terminal_edge)
                                        if returned is not None:
                                            expected = dict(format='workflow-sequential-storage-event@1', kind='returned',
                                                run_id=run_id, sequence=c + 3, operation_key=return_key,
                                                prior_digest=transition['event_digest'])
                                            _check(returned['kind'] == 'returned' and returned['sequence'] == (c + 1) + 2
                                                   and returned['event_bytes'] == _bytes(expected)
                                                   and hashlib.sha256(returned['event_bytes']).hexdigest() == returned['event_digest'])
                                    is_document = document_only(compiled, budget)
                                    is_code = next(node.output_contract.type for node in compiled.definition.nodes
                                                   if node.id == visit['node_id']) in ('code-change@1', 'independent-code-review@1', 'code-qa@1')
                                    if is_document or is_code:
                                        context = document_binding_context if is_document else coding_binding_context
                                        start, bound = context(run, visit, compiled, capture, budget)
                                        # Original proofs are preflighted at 16 KiB by _evidence.
                                        # Prepay canonical copies only on supported output paths;
                                        # reserve() also charges a step on this same allowance.
                                        budget.reserve(48 * 16384 + 512)
                                        terminal_digest = hashlib.sha256(_bytes(terminal)).hexdigest()
                                        budget.reserve(48 * 16384 + 512)
                                        outer_digest = hashlib.sha256(_bytes(outer)).hexdigest()
                                    else:
                                        start, bound = None, None
                                        terminal_digest = outer_digest = None
                                    envelope, content, recipients, checks = self._compose(conn, run, visit, compiled, result,
                                        bound=bound, budget=budget, carrier_digest=identity['carrier_digest'],
                                        terminal_digest=terminal_digest, outer_digest=outer_digest)
                                    contract = next(node.output_contract.type for node in compiled.definition.nodes if node.id == visit['node_id'])
                                    # Parser/base64/snapshot temporaries and native summary die before acceptance.
                                    del result, terminal, outer, identity
                                    budget.check()
                                common = dict(org_slug=self.org.slug, run_id=run_id, compiled=compiled,
                                              authority=self.org.workflow_authority, capture=capture)
                                successor = None
                                if transition is None:
                                    node = next(node for node in compiled.definition.nodes if node.id == visit['node_id'])
                                    edge = dict(node.outcomes).get(parse_result(envelope).outcome)
                                    if isinstance(edge, NextEdge):
                                        target = next(node for node in compiled.definition.nodes if node.id == edge.node)
                                        bound = (bound if bound is not None else _bound_agents(compiled, parse_start(run['start_bytes']), capture))[target.role]
                                        successor = TaskRecord(id='TASK-SEQ-' + source, parent_task_id=run['carrier_task_id'],
                                            assigned_agent=bound['name'], team=bound['team'], task_type='subtask',
                                            brief='Sequential node ' + target.id + '; follow only the bound node brief and domain supplement.')
                                accept_result_uncommitted(conn, self.org.db, **common, visit_id=visit_id,
                                    task_id=visit['task_id'], principal=visit['principal'], session_id=visit['session_id'],
                                    result_id=result_id, envelope_bytes=envelope, verified_checks=checks,
                                    content=content, contract=contract, recipients=recipients)
                                del content, envelope
                                moved = transition_uncommitted(conn, self.org.db, **common, visit_id=visit_id,
                                    result_id=result_id, operation_key=transition_key, expected_cas=c, successor_task=successor)
                                if transition is None:
                                    row = conn.execute('SELECT event_bytes FROM workflow_seq_events WHERE id=?', (moved.event_id,)).fetchone()
                                    terminal_edge = isinstance(parse_transition(row[0]).edge, TerminalEdge)
                                if terminal_edge:
                                    return_uncommitted(conn, self.org.db, **common, operation_key=return_key,
                                        expected_cas=c + 1, completed_at=task['completed_at'])
                                    if returned is None:
                                        # U3c owns lifecycle closure. Project only the final
                                        # native task's retained caller fields in this writer.
                                        conn.execute('UPDATE tasks SET block_kind=NULL,note=?,'
                                            'final_output_summary=(SELECT final_output_summary FROM tasks WHERE id=?),'
                                            'final_output_dir=(SELECT final_output_dir FROM tasks WHERE id=?) '
                                            'WHERE id IN (?,?)',
                                            ('Sequential run ' + run_id + ' returned.', visit['task_id'], visit['task_id'],
                                             run['source_task_id'], run['carrier_task_id']))
                        except (SequentialRefusal, WorkflowAuthorityError, ProfileCoordinatorError) as refusal:
                            if isinstance(refusal, (WorkflowAuthorityError, ProfileCoordinatorError)):
                                try:
                                    self._authority_unverified(expected_visit['task_id'])
                                except SequentialRefusal as classified:
                                    refusal = classified
                                else:
                                    raise
                            if refusal.code not in BusinessHoldCause:
                                raise
                            # Admission ownership has exited and its refused
                            # transaction rolled back. This writer grants no access.
                            return self._record_business_hold(expected_run, expected_visit, result_id,
                                                              BusinessHoldCause(refusal.code))
                        # Sole sequential COMMIT precedes all queue/caller effects.
                        if terminal_edge:
                            from runtime.workflows.sequential_return import notify_returned
                            notify_returned(self, run_id)
                        if moved.visit_task_id is not None:
                            self.notify_queued(moved.visit_task_id, self.org.orchestrator._queue)
                        return moved
        return self._on_loop(advance())

    def heartbeat(self, task_id: str, identity: Any = None) -> None:
        pass

    def queue_started(self, task_id: str) -> dict:
        from runtime.orchestrator.task_pause import TaskInvocation, release_live
        _check(self.queue_admission_allowed(task_id), 'sequential_queue_unavailable')
        with self._reader() as conn:
            _, visit, _ = self._entry(conn, task_id)
            task = self.org.db.get_task(task_id)
        invocation = TaskInvocation(self.org.orchestrator, task, task.assigned_agent,
            self.org.orchestrator._build_session_id(), owner='sequential')
        invocation.workflow_dispatcher = self
        invocation.draft_request_key = hashlib.sha256(_bytes(dict(
            visit_id=visit['id'], invocation_id=invocation.identity))).hexdigest()
        owner = dict(invocation=invocation, binding=None)
        with self.org.db._lock:
            self._queue_live.setdefault(task_id, {})[id(owner)] = owner
        try:
            invocation.prepare()
        except BaseException as exc:
            owner['invocation'] = None
            self.queue_finished(task_id, owner)
            if invocation.move_reservation is not None:
                try:
                    release_live(invocation, failed=True)
                except BaseException as cleanup_error:
                    exc.add_note(f'sequential queue preparation settlement failed: {type(cleanup_error).__name__}')
            raise
        return owner

    def queue_finished(self, task_id: str, owner: dict) -> None:
        from runtime.orchestrator.task_pause import release_live
        with self.org.db._lock:
            owners = self._queue_live.get(task_id, {})
            _check(owners.get(id(owner)) is owner)
            owners.pop(id(owner))
            if not owners:
                self._queue_live.pop(task_id, None)
        invocation = owner['invocation']
        if invocation is not None:
            with self.org.db._lock:
                live = invocation.store._live.get(invocation.identity) is invocation
            if live:
                release_live(invocation, failed=True)

    def bind_queue_invocation(self, invocation: Any) -> None:
        from copy import deepcopy
        with self.org.db._lock:
            owners = self._queue_live.get(invocation.task.id, {})
            candidates = [owner for owner in owners.values() if owner['invocation'] is invocation]
            if not candidates and len(owners) == 1:
                candidates = [next(iter(owners.values()))]
            if len(candidates) != 1:
                return
            _check(invocation.owner == 'sequential' and invocation.workflow_dispatcher is self)
            owner = candidates[0]
            row = invocation.store.row_uncommitted(invocation.store.root_uncommitted(invocation.task.id)['id'])
            native = deepcopy(invocation._entry(row))
            binding = dict(owner_id=native['id'], queue_generation=native['generation'],
                           claim_identity=native['id'], native=native)
            _check(owner['binding'] is None or owner['binding'] == binding)
            owner['binding'] = binding

    def notify_queued(self, task_id: str, queue: Any) -> None:
        if self.queue_admission_allowed(task_id):
            queue.enqueue_if_absent(self.org.slug, task_id)

    def dispatch(self, task_id: str) -> None:
        from runtime.orchestrator.task_pause import _invocation, release_live, fingerprint
        _check(self.queue_admission_allowed(task_id), 'sequential_queue_unavailable')
        with self._reader() as conn:
            self._entry(conn, task_id)
            task = self.org.db.get_task(task_id)
            owners = [owner for owner in self._queue_live.get(task_id, {}).values()
                      if owner['invocation'] is not None and owner['binding'] is not None]
            _check(len(owners) == 1)
            invocation = owners[0]['invocation']
            _check(invocation is invocation.store._live.get(invocation.identity)
                   and invocation.task.id == task_id and fingerprint(invocation.task) == fingerprint(task))
        token = _invocation.set(invocation)
        failed = False
        try:
            self.org.orchestrator._run_agent(task_id, task.assigned_agent, task.brief)
        except BaseException:
            failed = True
            raise
        finally:
            _invocation.reset(token)
            release_live(invocation, failed=failed)

    async def _commit_pause_launch(self, invocation: Any, ctx: Any) -> bool:
        _check(self.queue_admission_allowed(invocation.task.id), 'sequential_queue_unavailable')
        capture = self.org.workflow_authority.capture_admission()
        async with self.org.workflow_authority._async_writer_lock:
            async with self.org.teams_lock:
                async with self.org.db_lock:
                    with self.org.workflow_authority._admission_ownership(capture):
                        invocation.sequential_capture = capture
                        return invocation._commit_local(ctx)

    def _claim_launch_uncommitted(self, invocation: Any) -> None:
        conn = self.org.db._conn
        _check(conn.in_transaction and invocation.owner == 'sequential'
               and invocation.workflow_dispatcher is self and not invocation.admitted)
        _check(self.queue_admission_allowed(invocation.task.id), 'sequential_queue_unavailable')
        run, visit, compiled = self._entry(conn, invocation.task.id)
        self.org.workflow_authority._validate_admission_uncommitted(conn, invocation.sequential_capture)
        start = parse_start(run['start_bytes'])
        agents = _bound_agents(compiled, start, invocation.sequential_capture)
        role = next(node.role for node in compiled.definition.nodes if node.id == visit['node_id'])
        _check(agents[role]['name'] == invocation.agent == visit['principal']
               and agents[role]['team'] == invocation.task.team)
        invocation.sequential_before = (run, visit, compiled)
        before = dict(conn.execute('SELECT * FROM tasks WHERE id=?', (invocation.task.id,)).fetchone())
        self.org.db.claim_coding_step_uncommitted(task_id=invocation.task.id, before=before)

    def _session_bound_uncommitted(self, invocation: Any) -> None:
        from runtime.workflows.sequential_engine import bind_session_uncommitted
        run, visit, compiled = invocation.sequential_before
        bind_session_uncommitted(self.org.db._conn, self.org.db, org_slug=self.org.slug,
            run_id=run['id'], visit_id=visit['id'], session_id=invocation.session_id,
            compiled=compiled, authority=self.org.workflow_authority, capture=invocation.sequential_capture)

    def reserve_launch(self, task_id: str, agent: str, session_id: str) -> None:
        invocation = self._invocation(task_id, session_id)
        _check(invocation.agent == agent and invocation.admitted, 'sequential_native_recovery_unavailable')
        with self._reader() as conn:
            run, visit, _ = self._entry(conn, task_id)
            _check(run['state'] == 'active' and run['active_visit_id'] == visit['id']
                   and visit['state'] == 'running' and visit['selected'] == 1
                   and visit['session_id'] == session_id and not _cancelled(conn, run, visit))

    def host_request_key(self, task_id: str, agent: str, session_id: str) -> str:
        invocation = self._invocation(task_id, session_id)
        _check(invocation.agent == agent)
        return invocation.draft_request_key

    def observed_handle(self, task_id: str, agent: str, session_id: str, running: Any) -> None:
        invocation = self._invocation(task_id, session_id)
        self.reserve_launch(task_id, agent, session_id)
        request = getattr(invocation, 'coding_request', None)
        _check(request is not None and request.org == self.org.slug and request.agent_id == agent
               and request.logical_id == invocation.draft_request_key == running.request_id
               and request.retry_attempt == 0)
        original = getattr(invocation, 'coding_running', None)
        _check(original is None or original is running)
        invocation.coding_running = running

    def record_pid(self, *, task_id: str, agent: str, session_id: str, pid: int) -> bool:
        try:
            invocation = self._invocation(task_id, session_id)
        except SequentialRefusal:
            return False
        running = getattr(invocation, 'coding_running', None)
        if type(pid) is not int or pid <= 0 or invocation.agent != agent or running is None:
            return False
        with self._writer() as conn:
            run, visit, _ = self._entry(conn, task_id)
            if (run['state'] != 'active' or run['active_visit_id'] != visit['id']
                    or visit['state'] != 'running' or visit['selected'] != 1
                    or (visit['principal'], visit['session_id']) != (agent, session_id)
                    or _cancelled(conn, run, visit)):
                return False
            changed = conn.execute('UPDATE tasks SET executor_pid=? WHERE id=? AND assigned_agent=? '
                "AND current_session_id=? AND status='in_progress' AND cancelled_at IS NULL",
                (pid, task_id, agent, session_id))
            _check(changed.rowcount == 1)
            validate_selected_run_uncommitted(conn, org_slug=self.org.slug, run_id=run['id'], scope='session')
        return True

    def node_instruction(self, task_id: str, agent: str, sid: str) -> str:
        from runtime.workflows.profile_coordinator import ProfileCoordinatorError
        from runtime.workflows.sequential_briefs import BriefInputs, RecipientInput, render_node_brief
        from runtime.workflows.sequential_contracts import Counters, Finding, Limits
        from runtime.workflows.sequential_definition import parse_result
        from runtime.workflows.sequential_engine import _identity
        from runtime.workflows.sequential_history import _WIRE_TOKENS
        from runtime.workflows.sequential_reducer import ActiveVisit, RunState
        invocation = self._invocation(task_id, sid)
        _check(invocation.agent == agent)
        budget = _ReadBudget()
        capture = self._capture_authority(task_id)
        # Three capture decodes plus the owned admission validator's decode.
        # Prepay the latter before entry; retain this original allowance and
        # deadline through capture, validation, document/non-document rendering.
        # Token walks charge work without another decode or snapshot/tree copy.
        for _ in range(4):
            budget.reserve(48 * len(capture.ready.snapshot_bytes) + 512)
            for _ in _WIRE_TOKENS.finditer(capture.ready.snapshot_bytes):
                budget.step()
        # Capture and final validation each rebuild/traverse the profile mirror.
        # Include removed mirror rows, which need not appear in the snapshot,
        # and the per-profile digest-map reconstructions in both validators.
        for _ in range(2):
            for row in capture.profile_mirror:
                budget.candidate()
                budget.reserve(512 + 128 * len(row))
                for value in row:
                    budget.reserve(4 * len(value) + 512 if type(value) is str else 512)
        budget.work(2 * len(capture.profile_names) * (len(capture.profile_digests) + 1))

        async def render() -> str:
            async with self.org.workflow_authority._async_writer_lock:
                async with self.org.teams_lock:
                    async with self.org.db_lock:
                        with self.org.workflow_authority.admission_writer(capture) as conn:
                            with _sql_budget(conn, budget):
                                _check(invocation is invocation.store._live.get(invocation.identity))
                                run, visit, compiled = self._entry(conn, task_id, budget=budget)
                                _check(run['state'] == 'active' and run['active_visit_id'] == visit['id']
                                       and visit['selected'] == 1 and visit['state'] in ('queued', 'running')
                                       and visit['session_id'] in (None, sid) and visit['principal'] == agent
                                       and not _cancelled(conn, run, visit))
                                is_document = document_only(compiled, budget)
                                if is_document:
                                    start, bound = document_binding_context(run, visit, compiled, capture, budget)
                                    _check(bound[next(node.role for node in compiled.definition.nodes if node.id == visit['node_id'])]['name'] == agent,
                                           'sequential_output_access_unavailable')
                                else:
                                    budget.work(len(compiled.definition.nodes))
                                    if coding_graph(compiled):
                                        start, bound = coding_binding_context(run, visit, compiled, capture, budget)
                                    else:
                                        start, bound = parse_start(run['start_bytes']), None
                                node = next(node for node in compiled.definition.nodes if node.id == visit['node_id'])
                                refs = _selection(visit, budget)
                                if is_document or bound is not None:
                                    _check(all(not item.required or not item.source.startswith('current.')
                                               or any(ref.slot == item.source[8:] for ref in refs) for item in node.inputs))
                                values = [RecipientInput(source='run.' + item.name, recipients=(node.role,), value=item.value)
                                          for item in start.inputs if any(declared.source == 'run.' + item.name for declared in node.inputs)]
                                for ref in refs:
                                    if not any(declared.source == 'current.' + ref.slot for declared in node.inputs):
                                        _check(not is_document)
                                        continue
                                    rows = _engine_rows(conn, 'workflow_seq_outputs', budget, 'id=?',
                                                        (_identity('output', ref.producer_visit, ref.slot),))
                                    _check(len(rows) == 1)
                                    output = rows[0]
                                    recipients = _json(output['recipients_bytes'], 16384)
                                    if is_document:
                                        authorize_document_input(compiled=compiled, node=node, ref=ref, output=output,
                                            stored_recipients=recipients, bound=bound, principal=agent, budget=budget)
                                    elif bound is not None:
                                        authorize_coding_input(conn, run=run, visit=visit, compiled=compiled, node=node, ref=ref,
                                            output=output, stored_recipients=recipients, bound=bound,
                                            principal=agent, budget=budget)
                                    _check(output['run_id'] == run['id'] and output['visit_id'] == ref.producer_visit
                                           and (output['slot'], output['contract'], output['epoch'], output['task_id'],
                                                output['session_id'], output['result_id'], output['content_digest']) ==
                                               (ref.slot, ref.contract, ref.epoch, ref.task_id, ref.session_id, ref.result_id, ref.digest)
                                           and node.role in recipients['roles']
                                           and hashlib.sha256(output['content_bytes']).hexdigest() == ref.digest)
                                    values.append(RecipientInput(source='current.' + ref.slot, recipients=recipients['roles'],
                                        value=output['content_bytes'].decode('utf8', errors='strict'), reference=ref))
                                findings = []
                                if any(declared.source == 'returned_findings' for declared in node.inputs):
                                    for ordinal in range(1, visit['ordinal']):
                                        rows = _engine_rows(conn, 'workflow_seq_visits', budget, 'id=?',
                                                            (_identity('visit', run['id'], str(ordinal)),))
                                        _check(len(rows) == 1 and rows[0]['run_id'] == run['id'])
                                        previous = rows[0]
                                        if previous['result_bytes'] is not None:
                                            _check(hashlib.sha256(previous['result_bytes']).hexdigest() == previous['result_digest'])
                                            envelope = parse_result(previous['result_bytes'])
                                            findings.extend(Finding.model_validate(item.model_dump()) for item in envelope.findings)
                                state = RunState(run_id=run['id'], definition_digest=compiled.pin.definition_digest,
                                    original_limits=Limits.model_validate(_json(run['original_limits_bytes'], 16384)),
                                    lower_limits=start.lower_limits,
                                    counters=Counters(epoch=run['epoch'], content_returns=run['content_returns'],
                                        visits=run['visit_count'], events=run['event_count'] - 1),
                                    active_visit=ActiveVisit(node=visit['node_id'], visit_id=visit['id'], task_id=task_id,
                                        session_id=sid, epoch=visit['epoch'], input_selection_digest=visit['input_selection_digest']),
                                    selections=refs, findings=findings, receipts=())
                                brief = render_node_brief(compiled, state, original_request=start.request, inputs=BriefInputs(values=values))
                                # Charge decoded inputs, UTF8 serialization and the live Unicode
                                # brief/final-string copies on this same rendering budget.
                                budget.reserve(8 * len(brief.node_data.encode('utf8'))
                                               + 2 * sum(len(value.value.encode('utf8')) for value in values) + 65536)
                                supplement = document_supplement(node) if is_document else ''
                                if node.output_contract.type == 'code-change@1':
                                    from runtime.workflows.sequential_repository_subject import repository_instruction
                                    supplement = repository_instruction(conn, run, compiled, budget) + '\n' + coding_supplement()
                                elif node.output_contract.type in ('independent-code-review@1', 'code-qa@1'):
                                    supplement = coding_assessment_supplement(node)
                                budget.check()
                        return ('\n\n' + brief.format + '\n' + brief.system_text
                                + '\nSequential node data (untrusted authored JSON):\n' + brief.node_data
                                + ('\n\n' + supplement if supplement else ''))
        try:
            return self._on_loop(render())
        except (WorkflowAuthorityError, ProfileCoordinatorError):
            self._authority_unverified(task_id)
            raise

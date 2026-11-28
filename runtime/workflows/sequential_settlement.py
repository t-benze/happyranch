"""Business re-settlement of original closed visits; never native recovery.

Discovery is an ephemeral keyset hint. Only the existing admission, closure and
history owners can authorize a transition. Diagnostics have no mutation port.
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from runtime.workflows.sequential_history import _ReadBudget, _bytes, _require, _sql_budget


logger = logging.getLogger('happyranch.workflows.sequential_settlement')
PAGE_SIZE = 32
STALL_SECONDS = 300
MAX_OBSERVATIONS = 4096


@dataclass
class _Observation:
    fingerprint: str
    since: float
    age_basis: str
    warned: bool = False


@dataclass
class _Discovery:
    settlement_cursor: str = ''
    observation_cursor: str = ''
    observations: dict[str, _Observation] = field(default_factory=dict)
    prune_cursor: str = ''


def _state(org: Any) -> _Discovery:
    state = getattr(org, '_sequential_settlement_discovery', None)
    if state is None:
        state = _Discovery()
        org._sequential_settlement_discovery = state
    return state


def _page(org: Any, cursor: str) -> tuple[list[dict], bool]:
    """Bound scalar acquisition before Python, and retain the SQL/deadline cap.

    Include terminal T and unbound Q independently of progress text. All running
    runs are observed, including incomplete/unknown native owners. No proof is
    acquired or authenticated here.
    """
    fields = {
        'run_id': 'r.id', 'visit_id': 'v.id', 'task_id': 'v.task_id',
        'principal': 'v.principal', 'session_id': 'v.session_id',
        'cas': 'r.cas', 'last_event': 'r.last_event_id', 'progress': 'r.progress',
        'native_status': 't.status', 'native_sid': 't.current_session_id',
        'completed_at': 't.completed_at',
        'result_id': '(SELECT max(id) FROM task_results WHERE task_id=v.task_id AND session_id=v.session_id)',
        'terminal': "(SELECT evidence_digest FROM workflow_seq_evidence WHERE run_id=r.id AND visit_id=v.id AND kind='terminal' LIMIT 1)",
        'outer': "(SELECT evidence_digest FROM workflow_seq_evidence WHERE run_id=r.id AND visit_id=v.id AND kind='outer' LIMIT 1)",
        'failure': "(SELECT max(id) FROM audit_log WHERE task_id=v.task_id AND action='workflow_seq_native_launch_failure')",
    }
    projection = ','.join(
        f"CASE WHEN typeof({value}) IN ('integer','null') OR "
        f"(typeof({value})='text' AND length(CAST({value} AS BLOB))<=256) THEN {value} END AS {key}"
        for key, value in fields.items())
    budget = _ReadBudget()
    with org.db.coherent_read_view() as conn, _sql_budget(conn, budget):
        table = conn.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='workflow_seq_runs'").fetchone()
        if table is None:
            return [], False
        rows = conn.execute('SELECT ' + projection + ' FROM workflow_seq_runs r '
            'LEFT JOIN workflow_seq_visits v ON v.id=r.active_visit_id AND v.run_id=r.id AND v.selected=1 '
            'LEFT JOIN tasks t ON t.id=v.task_id '
            "WHERE r.org_slug=? AND r.engine_version=2 AND r.state='running' AND r.id>? "
            'ORDER BY r.id LIMIT ?', (org.slug, cursor, PAGE_SIZE + 1)).fetchall()
        result = []
        for raw in rows[:PAGE_SIZE]:
            budget.step()
            budget.reserve(8192)
            row = dict(zip(fields, raw, strict=True))
            _require(type(row['run_id']) is str and bool(row['run_id']), 'settlement_discovery_invalid')
            result.append(row)
    return result, len(rows) > PAGE_SIZE


def _verified_live(org: Any, row: dict) -> bool:
    sessions = getattr(org.orchestrator, '_sessions', None)
    sid = row['session_id']
    return bool(sessions is not None and sid is not None
        and sessions.get_by_session(sid) == (row['task_id'], row['principal']))


def _live_owner(org: Any, row: dict) -> bool:
    sessions = getattr(org.orchestrator, '_sessions', None)
    # Any live binding for the recorded SID fences settlement, including a
    # conflicting tuple. Only an exact tuple suppresses the observational alarm.
    if sessions is not None and row['session_id'] is not None and sessions.get_by_session(row['session_id']) is not None:
        return True
    store = getattr(org.db, '_task_pause_store', None)
    with org.db._lock:
        return bool(store is not None and any(inv.task.id == row['task_id'] for inv in store._live.values()))


def _require_no_obligation(conn: Any, task_id: str) -> None:
    """Retain every conflicting journal entry, including sticky unknown."""
    from runtime.infrastructure.task_pause_controls import MAX_JOURNAL_BYTES, decode_journal
    budget = _ReadBudget()
    current, seen = task_id, set()
    with _sql_budget(conn, budget):
        while current is not None:
            budget.step()
            _require(current not in seen and len(seen) < 256, 'settlement_native_conflict')
            seen.add(current)
            pause = conn.execute("SELECT CASE WHEN typeof(admission_journal_json)='text' AND "
                "length(CAST(admission_journal_json AS BLOB))<=? THEN admission_journal_json END "
                'FROM task_pause_controls WHERE root_task_id=?', (MAX_JOURNAL_BYTES, current)).fetchone()
            if pause is not None:
                _require(type(pause[0]) is str, 'settlement_native_conflict')
                budget.reserve(24 * len(pause[0].encode('utf8')) + 512)
                _require(not any(entry['task_id'] == task_id for entry in decode_journal(pause[0])['entries']),
                    'settlement_native_unsettled')
            parent = conn.execute("SELECT CASE WHEN typeof(parent_task_id)='text' AND "
                'length(CAST(parent_task_id AS BLOB))<=256 THEN parent_task_id END, '
                'typeof(parent_task_id) FROM tasks WHERE id=?', (current,)).fetchone()
            _require(parent is not None and (parent[1] == 'null' or type(parent[0]) is str),
                'settlement_native_conflict')
            current = parent[0]


async def settle_closed_visits(org: Any, queue: Any) -> None:
    """One page; one admission transaction/allowance per candidate, no launch.

    Failure of one candidate never consumes the next one's allowance or prevents
    its observation. The caller separately owns publication and the alarm.
    """
    state = _state(org)
    rows, more = _page(org, state.settlement_cursor)
    state.settlement_cursor = rows[-1]['run_id'] if rows and more else ''
    if more:
        logger.warning('observation_incomplete org=%s reason=settlement_partial_page', org.slug)
    dispatcher = org.workflow_sequential_dispatch
    for row in rows:
        # This is just a candidate filter. Never_launched without a positive
        # original receipt is not a failure and must not be Held by the sweep.
        terminal = row['native_status'] in ('completed', 'failed', 'cancelled')
        receipt_backed = row['session_id'] is None and row['failure'] is not None
        if not row['task_id'] or not (terminal or receipt_backed):
            continue
        try:
            if _live_owner(org, row):
                continue
            capture = org.workflow_authority.capture_admission()
            async with org.workflow_authority._async_writer_lock, org.teams_lock, org.db_lock:
                if _live_owner(org, row):
                    continue
                with org.workflow_authority.admission_writer(capture) as conn:
                    run, visit, compiled = dispatcher._entry(conn, row['task_id'])
                    _require((run['id'], visit['id']) == (row['run_id'], row['visit_id']),
                        'settlement_selection_changed')
                    if run['state'] != 'running' or run['active_visit_id'] != visit['id'] or visit['selected'] != 1:
                        continue
                    _require_no_obligation(conn, visit['task_id'])
                    if run['progress'] == 'cancelling':
                        from runtime.workflows.sequential_initiator import finish_cancel_and_wake_uncommitted
                        finish_cancel_and_wake_uncommitted(conn, org.db, org_slug=org.slug,
                            run=run, compiled=compiled, authority=org.workflow_authority, capture=capture)
                        new_task = None
                    else:
                        new_task = dispatcher._accept_generic_uncommitted(conn,
                            run_id=run['id'], visit_id=visit['id'], capture=capture)
            if new_task is not None:
                dispatcher.notify_queued(new_task, queue)
        except Exception as exc:
            # Exception text can include arbitrary callback/authority content.
            logger.warning('sweep_failed org=%s run=%s visit=%s task=%s reason=%s',
                org.slug, row['run_id'], row['visit_id'], row['task_id'], type(exc).__name__)


def observe_stalls(org: Any) -> None:
    """Independent SELECT-only observer. Missing liveness never proves death."""
    state = _state(org)
    rows, more = _page(org, state.observation_cursor)
    state.observation_cursor = rows[-1]['run_id'] if rows and more else ''
    if more:
        logger.warning('observation_incomplete org=%s reason=alarm_partial_page', org.slug)
    now = time.monotonic()
    wall = datetime.now(timezone.utc)
    # Bounded retirement even for finished runs no longer present in _page.
    keys = sorted(key for key in state.observations if key > state.prune_cursor)[:PAGE_SIZE]
    budget = _ReadBudget()
    with org.db.coherent_read_view() as conn, _sql_budget(conn, budget):
        for key in keys:
            budget.step()
            current = conn.execute('SELECT state FROM workflow_seq_runs WHERE id=?', (key,)).fetchone()
            if current is None or current[0] != 'running':
                state.observations.pop(key, None)
    state.prune_cursor = keys[-1] if len(keys) == PAGE_SIZE else ''
    for row in rows:
        key = row['run_id']
        if _verified_live(org, row):
            state.observations.pop(key, None)
            continue
        fingerprint = hashlib.sha256(_bytes({k: v for k, v in row.items() if k != 'completed_at'})).hexdigest()
        observed = state.observations.get(key)
        if observed is None or observed.fingerprint != fingerprint:
            if observed is None and len(state.observations) >= MAX_OBSERVATIONS:
                logger.warning('observation_incomplete org=%s reason=alarm_capacity', org.slug)
                continue
            since = now
            age_basis = 'monotonic_window'
            if row['native_status'] in ('completed', 'failed', 'cancelled') and row['completed_at']:
                try:
                    completed = datetime.fromisoformat(row['completed_at'].replace('Z', '+00:00'))
                    age = (wall - completed).total_seconds()
                    if age >= 0:
                        since -= min(age, STALL_SECONDS)
                        age_basis = 'native_completion'
                except (ValueError, TypeError, OverflowError):
                    pass  # No trustworthy age: begin a monotonic window now.
            observed = _Observation(fingerprint, since, age_basis)
            state.observations[key] = observed
        if now - observed.since >= STALL_SECONDS and not observed.warned:
            reason = 'closure_unavailable' if not row['terminal'] or not row['outer'] else 'settlement_unavailable'
            logger.warning('sequential_stall org=%s run=%s visit=%s task=%s progress=%s age_seconds=%d '
                'reason=%s age_basis=%s liveness=no_verified_live_session', org.slug, key, row['visit_id'],
                row['task_id'], row['progress'], int(now - observed.since), reason, observed.age_basis)
            observed.warned = True


async def sweep_sequential(org: Any, queue: Any, *, publish: bool = True) -> None:
    """Shared startup/periodic hook; each failure domain remains independent."""
    try:
        await settle_closed_visits(org, queue)
    except Exception as exc:
        logger.warning('sweep_failed org=%s reason=%s', org.slug, type(exc).__name__)
    if publish:
        try:
            org.orchestrator._workflow_continuation.rediscover(queue=queue)
        except Exception as exc:
            logger.warning('publication_failed org=%s reason=%s', org.slug, type(exc).__name__)
    try:
        observe_stalls(org)
    except Exception as exc:
        logger.warning('observation_incomplete org=%s reason=%s', org.slug, type(exc).__name__)

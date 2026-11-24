"""Positive original pre-preparation refusal receipts; never cleanup evidence.

Only queue_started's renderer exception path writes these rows, before creating
TaskInvocation. No receipt can discharge a Pause or repository obligation.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone

from runtime.workflows.sequential_history import _ReadBudget, _bytes, _require

_ACTION = 'workflow_sequential_preparation_free_refusal'
_LIMIT = 3


def _history(conn: sqlite3.Connection, run: dict, visit: dict, budget: _ReadBudget) -> list[dict]:
    budget.step()
    rows = conn.execute("SELECT CASE WHEN typeof(payload)='text' AND length(CAST(payload AS BLOB))<=2048 "
        "THEN payload END FROM audit_log WHERE task_id=? AND action=? ORDER BY id DESC LIMIT 3",
        (visit['task_id'], _ACTION)).fetchall()
    history = []
    for row in rows:
        _require(type(row[0]) is str, 'launch_refusal_receipt_invalid')
        budget.reserve(48 * len(row[0]) + 512)
        value = json.loads(row[0])
        _require(type(value) is dict and value.keys() == {'run_id', 'visit_id', 'task_id', 'principal',
            'session_id', 'count', 'boundary'} and value['run_id'] == run['id'] and value['visit_id'] == visit['id']
            and value['task_id'] == visit['task_id'] and value['principal'] == visit['principal']
            and type(value['session_id']) is str and 0 < len(value['session_id']) <= 256
            and type(value['count']) is int and 1 <= value['count'] <= _LIMIT
            and value['boundary'] == 'node_instruction_before_prepare', 'launch_refusal_receipt_invalid')
        history.append(value)
    _require(not history or [row['count'] for row in history] == list(range(history[0]['count'], 0, -1)),
        'launch_refusal_receipt_invalid')
    _require(len({row['session_id'] for row in history}) == len(history), 'launch_refusal_receipt_invalid')
    return history


def record_refusal(conn: sqlite3.Connection, run: dict, visit: dict, sid: str) -> int:
    """The actual original preflight owner calls once before any preparation."""
    _require(conn.in_transaction and run['engine_version'] == 2 and run['state'] == 'running'
        and run['progress'] == 'queued' and run['active_visit_id'] == visit['id']
        and visit['state'] == 'queued' and visit['session_id'] is None, 'launch_refusal_owner_conflict')
    history = _history(conn, run, visit, _ReadBudget())
    _require(not history or history[0]['count'] < _LIMIT, 'launch_refusal_limit')
    count = len(history) + 1
    payload = dict(run_id=run['id'], visit_id=visit['id'], task_id=visit['task_id'], principal=visit['principal'],
        session_id=sid, count=count, boundary='node_instruction_before_prepare')
    conn.execute('INSERT INTO audit_log(task_id,agent,action,payload,timestamp) VALUES (?,?,?,?,?)',
        (visit['task_id'], visit['principal'], _ACTION, _bytes(payload).decode('utf8'),
         datetime.now(timezone.utc).isoformat()))
    return count


def refusal_proof(conn: sqlite3.Connection, run: dict, visit: dict, budget: _ReadBudget) -> dict:
    """Authenticate positive cap receipts for business Held, never native close."""
    history = _history(conn, run, visit, budget)
    _require(len(history) == _LIMIT and history[0]['count'] == _LIMIT, 'launch_refusal_limit')
    return dict(kind='preparation_free_refusals', result_id=None,
        receipts_digest=hashlib.sha256(_bytes(history)).hexdigest())

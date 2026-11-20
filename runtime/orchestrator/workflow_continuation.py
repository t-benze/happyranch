"""Workflow dependency observations and ordinary initiator continuations.

The native callback writer owns result admission. This bridge never commits a
callback, invents a result identity, or treats an inbox wake as task completion.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any

from runtime.workflows.sequential_history import _bytes, _require


_control_capture: ContextVar[Any] = ContextVar('workflow_continuation_capture', default=None)


def decision_schemas() -> list[dict]:
    """Closed served alternatives for the same internal NextStep grammar."""
    from runtime.workflows.sequential_contracts import GenericControl, GenericStartCommand

    def inline_model(model: Any) -> dict:
        schema = model.model_json_schema()
        definitions = schema.pop('$defs', {})
        def inline(value: Any) -> Any:
            if isinstance(value, dict):
                if '$ref' in value:
                    return inline(definitions[value['$ref'].rsplit('/', 1)[-1]])
                return {key: inline(item) for key, item in value.items()}
            if isinstance(value, list):
                return [inline(item) for item in value]
            return value
        return inline(schema)

    control = inline_model(GenericControl)
    control['allOf'] = [
        {'if': {'properties': {'action': {'const': 'cancel'}}},
         'then': {'not': {'anyOf': [{'required': [key]} for key in ('supplement', 'target_agent_id', 'model', 'reasoning_effort')]}}},
        {'if': {'properties': {'action': {'const': 'retry'}}}, 'then': {'not': {'required': ['supplement']}}},
        {'if': {'properties': {'action': {'const': 'rebrief'}}},
         'then': {'required': ['supplement'], 'properties': {'supplement': {'type': 'string', 'minLength': 1, 'maxLength': 16384}}}},
        {'if': {'properties': {'action': {'const': 'reassign'}}},
         'then': {'required': ['target_agent_id'], 'properties': {'target_agent_id': {'type': 'string', 'minLength': 1}}},
         'else': {'not': {'required': ['target_agent_id']}}},
    ]
    ids = dict(type='array', maxItems=32, uniqueItems=True,
        items=dict(type='string', minLength=1, pattern='^[^\u0000]+$'))
    return [dict(type='object', additionalProperties=False, required=['action', key],
        properties=dict(action=dict(const=action), consume_notifications=ids, **{key: payload}))
        for action, key, payload in (
            ('workflow_control', 'workflow', control),
            ('workflow_wait', 'run_ids', dict(ids, maxItems=4096)),
            ('workflow_start', 'workflows', dict(type='array', minItems=1, maxItems=8,
                items=inline_model(GenericStartCommand))))]


def observation(db: Any, task_id: str) -> dict | None:
    """One coherent, read-only observation; absence alone retains legacy rules."""
    from runtime.workflows.sequential_initiator import observe_dependencies
    with db.coherent_read_view() as conn:
        return observe_dependencies(conn, db, task_id)


def ready(orch: Any, task: Any) -> bool:
    """Inbox readiness admits its own initiator, never an enclosing parent."""
    import sqlite3
    try:
        value = observation(orch._db, task.id)
        return bool(value and value['pending'] and task.task_type == 'task'
            and task.status == 'in_progress' and task.block_kind == 'delegated'
            and task.cancelled_at is None)
    except (ValueError, TypeError, sqlite3.Error):
        return False


def fanout_join_ready(orch: Any, task: Any) -> bool:
    from runtime.orchestrator.fanout import FanoutState
    from runtime.orchestrator.task_terminal_readers import _child_landed_terminal_report
    fanout = FanoutState.deserialize(task.active_fanout)
    for task_id in fanout.children_ids:
        child = orch._db.get_task(task_id)
        if child is None or (child.status not in {'completed', 'failed', 'cancelled', 'superseded'}
                and _child_landed_terminal_report(orch, child) is None):
            return False
    return True


def ordinary_children(orch: Any, task: Any) -> list | None:
    """Workflow carriers never enter the ordinary chain/fanout barrier."""
    import sqlite3
    try:
        with orch._db.coherent_read_view():
            value = observation(orch._db, task.id)
            ids = value['ordinary_task_ids'] if value is not None else orch._db.get_children(task.id)
            return [orch._db.get_task(task_id) for task_id in ids]
    except (ValueError, TypeError, sqlite3.Error):
        return None


class WorkflowContinuation:
    def __init__(self, org: Any) -> None:
        self.org = org
        self.db = org.db

    @asynccontextmanager
    async def callback_admission(self, task_id: str, decision: dict | None,
                                 *, status: str = 'completed') -> AsyncIterator[None]:
        """File-backed control discovery happens before the native write fence."""
        capture = None
        if status == 'completed' and decision and decision.get('action') == 'workflow_control':
            from runtime.workflows.sequential_control import prepare_control
            from runtime.workflows.sequential_definition import compile_generic_definition
            command = decision['workflow']
            with self.db.coherent_read_view() as conn:
                row = conn.execute('SELECT d.definition_bytes,d.source_digest FROM workflow_seq_runs r '
                    'JOIN workflow_seq_definitions d ON d.id=r.definition_id '
                    'WHERE r.id=? AND r.source_task_id=? AND r.engine_version=2',
                    (command['run_id'], task_id)).fetchone()
                _require(row is not None, 'workflow_run_not_owned')
                compiled = compile_generic_definition(row[0], source_digest=row[1])
            from runtime.workflows.authority import WorkflowAuthorityError
            try:
                capture = (compiled, prepare_control(self.org, command_bytes=_bytes(command), compiled=compiled))
            except WorkflowAuthorityError:
                raise ValueError('workflow_seq_workflow_authority_unavailable') from None
        token = _control_capture.set(capture)
        try:
            yield
        finally:
            _control_capture.reset(token)

    def admitted_uncommitted(self, invocation: Any) -> None:
        """The single snapshot call site, after a genuinely new SID is bound."""
        from runtime.workflows.sequential_initiator import snapshot_delivery
        snapshot_delivery(self.db._conn, invocation.task.id, invocation.agent, invocation.session_id)

    def callback_uncommitted(self, *, task_id: str, agent: str, session_id: str,
                             result_id: int) -> bool:
        """Result + effects + consumption share Database's BEGIN/COMMIT."""
        from runtime.infrastructure.db.tasks import RetryClaim
        from runtime.models import TaskRecord
        from runtime.orchestrator.orchestrator import completion_report_from_result_row
        from runtime.workflows.sequential_initiator import consume_decision_uncommitted, observe_dependencies, delivered_page
        conn = self.db._conn
        _require(conn.in_transaction and type(result_id) is int, 'workflow_result_identity_invalid')
        row = conn.execute('SELECT * FROM task_results WHERE id=? AND task_id=? '
            'AND agent=? AND session_id=?', (result_id, task_id, agent, session_id)).fetchone()
        _require(row is not None, 'workflow_result_identity_invalid')
        if row['status'] != 'completed':
            return True  # Native blocked/jobs/failed causes and branches survive.
        report = completion_report_from_result_row(task_id, dict(row), fallback_agent=agent)
        parsed = self.org.orchestrator._parse_next_step(report)
        if parsed.action not in ('workflow_start', 'workflow_control', 'workflow_wait'):
            # Ordinary decisions never acknowledge the stored delivery page.
            # Done and new dispatch still recheck dependencies under this fence.
            if parsed.action in ('done', 'delegate', 'fanout', 'parallel'):
                state = observe_dependencies(conn, self.db, task_id)
                _require(state is not None, 'workflow_dependencies_unavailable')
                if parsed.action == 'done':
                    _require(not state['unfinished_runs'] and not state['ordinary_outstanding'],
                             'workflow_done_outstanding')
                else:
                    _require(not state['ordinary_join_outstanding'],
                        'workflow_dispatch_join_outstanding')
            return True
        _require(type(row['decision_json']) is str, 'workflow_decision_required')
        value = json.loads(row['decision_json'])

        def apply() -> dict:
            state = observe_dependencies(conn, self.db, task_id)
            _require(state is not None, 'workflow_dependencies_unavailable')
            if parsed.action == 'workflow_wait':
                _require(set(parsed.run_ids) <= set(state['run_ids']), 'workflow_run_not_owned')
                page = delivered_page(conn, task_id, agent, session_id)
                consumed = value.get('consume_notifications', [item['notification_id'] for item in page['notifications']])
                _require(consumed or state['unfinished_runs'] or state['ordinary_outstanding'],
                         'workflow_wait_without_dependency')
            elif parsed.action == 'workflow_control':
                from runtime.workflows.sequential_control import apply_control_uncommitted
                _require(parsed.workflow['run_id'] in state['run_ids'], 'workflow_run_not_owned')
                prepared = _control_capture.get()
                _require(prepared is not None, 'workflow_control_capture_missing')
                compiled, capture = prepared
                task = self.db.get_task(task_id)
                replacement = None
                if capture.assignment is not None:
                    run = conn.execute('SELECT carrier_task_id FROM workflow_seq_runs WHERE id=?',
                        (parsed.workflow['run_id'],)).fetchone()
                    replacement = TaskRecord(id=self.db.next_task_id(), parent_task_id=run[0],
                        assigned_agent=capture.assignment['principal'], team=task.team, brief='', task_type='subtask')
                receipt = apply_control_uncommitted(conn, self.db, org_slug=self.org.slug, compiled=compiled,
                    actor=RetryClaim.from_task(task, result_row_id=result_id), capture=capture,
                    authority=self.org.workflow_authority, replacement_task=replacement)
                effects = json.loads(receipt)
                conn.execute("UPDATE tasks SET block_kind='delegated' WHERE id=?", (task_id,))
                return effects
            elif parsed.action == 'workflow_start':
                # Public @2 start remains unmounted until its later owner.
                raise ValueError('workflow_start_v2_unavailable')
            if parsed.action == 'workflow_wait':
                conn.execute("UPDATE tasks SET block_kind='delegated' WHERE id=?", (task_id,))
            return {'action': parsed.action}

        consume_decision_uncommitted(conn, initiator_task_id=task_id, principal=agent,
            session_id=session_id, result_id=result_id, decision=_bytes(value), apply=apply)
        return True

    def prompt_context(self, task: Any) -> str:
        from runtime.workflows.sequential_initiator import delivered_page
        with self.db.coherent_read_view() as conn:
            task = self.db.get_task(task.id)
            state = observation(self.db, task.id)
            if state is None:
                return ''
            page = delivered_page(conn, task.id, task.assigned_agent, task.current_session_id)
            return '\nWorkflow events (stored delivery for this session):\n' + json.dumps(
                dict(page=page, unfinished_runs=state['unfinished_runs'],
                     runs=state['runs'], ordinary_outstanding=state['ordinary_outstanding']), ensure_ascii=False, sort_keys=True)

    def admitted_result(self, task: Any, result_id: int, report: Any) -> bool:
        """Authenticate completed workflow decisions against their receipt."""
        with self.db.coherent_read_view() as conn:
            receipt = conn.execute('SELECT result_id FROM workflow_seq_consumption_receipts '
                'WHERE result_id=? AND initiator_task_id=? AND principal=? AND session_id=?',
                (result_id, task.id, task.assigned_agent, task.current_session_id)).fetchone()
            if receipt is None or not self.db.authority_policy_v2_decision_result_report_binds(
                    root_task_id=task.id, spending_result_id=result_id, report=report):
                return False
            observation(self.db, task.id)
        return True

    def rediscover(self, *, queue: Any = None) -> None:
        from runtime.workflows.sequential_history import _ReadBudget, _sql_budget
        from runtime.workflows.sequential_initiator import _rows
        selected = queue or getattr(self.org.orchestrator, '_queue', None)
        budget = _ReadBudget()
        with self.db.coherent_read_view() as conn:
            if conn.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='workflow_seq_source_wait_sets'").fetchone() is None:
                return
            with _sql_budget(conn, budget):
                ids = [row['source_task_id'] for row in _rows(conn, 'workflow_seq_source_wait_sets', budget,
                    "source_task_id IN (SELECT id FROM tasks WHERE status='in_progress' AND block_kind='delegated')", ())]
                visits = _rows(conn, 'workflow_seq_outbox', budget,
                    "kind='visit_queued' AND task_id IN (SELECT id FROM tasks WHERE status='pending')", ())
        if selected is not None:
            for row in visits:
                self.org.workflow_sequential_dispatch.notify_queued(row['task_id'], selected)
        for task_id in ids:
            self.wake(task_id, queue=queue)

    def publish_result(self, result_id: int, task_id: str) -> None:
        """Postcommit publication uses only the exact recorded effect receipt."""
        with self.db.coherent_read_view() as conn:
            row = conn.execute('SELECT receipt_bytes FROM workflow_seq_consumption_receipts '
                'WHERE result_id=? AND initiator_task_id=?', (result_id, task_id)).fetchone()
            _require(row is not None, 'initiator_receipt_missing')
            effects = json.loads(row[0])['effects']
        selected = getattr(self.org.orchestrator, '_queue', None)
        if selected is not None and effects.get('new_task_id') is not None:
            self.org.workflow_sequential_dispatch.notify_queued(effects['new_task_id'], selected)
        self.wake(task_id)

    def settled(self, task_id: str) -> None:
        """Retry publication after the actual native close/release boundary."""
        if not self.member_event(task_id):
            self.wake(task_id)

    def member_event(self, task_id: str, *, queue: Any = None) -> bool:
        """Postcommit native visit/carrier tail, without ordinary chain effects."""
        from runtime.infrastructure.workflow_task_ownership import selected_owner
        with self.db.coherent_read_view() as conn:
            owner = selected_owner(conn, task_id, self.org.slug)
            if owner[0] not in ('seq_visit', 'seq_carrier'):
                return False
            if owner[0] == 'seq_visit':
                row = conn.execute('SELECT r.source_task_id FROM workflow_seq_runs r '
                    'JOIN workflow_seq_source_memberships m ON m.run_id=r.id '
                    'JOIN workflow_seq_visits v ON v.run_id=r.id WHERE v.id=? AND v.task_id=?',
                    (owner[1], task_id)).fetchone()
            else:
                row = conn.execute('SELECT r.source_task_id FROM workflow_seq_runs r '
                    'JOIN workflow_seq_source_memberships m ON m.run_id=r.id WHERE r.carrier_task_id=?',
                    (task_id,)).fetchone()
            if row is None:
                return False
            source_id = row[0]
        return self.wake(source_id, queue=queue)

    def wake(self, task_id: str, *, queue: Any = None) -> bool:
        task = self.db.get_task(task_id)
        if task is None or not ready(self.org.orchestrator, task):
            return False
        selected = queue or getattr(self.org.orchestrator, '_queue', None)
        if selected is not None:
            selected.enqueue_if_absent(self.org.slug, task_id)
        return True

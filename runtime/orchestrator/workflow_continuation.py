"""Workflow dependency observations and ordinary initiator continuations.

The native callback writer owns result admission. This bridge never commits a
callback, invents a result identity, or treats an inbox wake as task completion.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import ExitStack, asynccontextmanager, contextmanager
from contextvars import ContextVar
from typing import Any

from runtime.workflows.sequential_history import _bytes, _require


_control_capture: ContextVar[Any] = ContextVar('workflow_continuation_capture', default=None)
_start_capture: ContextVar[Any] = ContextVar('workflow_start_capture', default=None)


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
        value = observe_dependencies(conn, db, task_id)
        task = db.get_task(task_id) if value is not None else None
    if value is not None:
        value['disposition'] = 'initiator_terminal' if task.status in ('completed', 'failed', 'cancelled', 'superseded') else 'pending'
        value['unavailable_reason'] = None
        if value['disposition'] == 'pending':
            continuation = getattr(db, '_workflow_continuation', None)
            if continuation is not None:
                from runtime.orchestrator.prompt_loader import load_agent
                try:
                    agent = load_agent(continuation.org.orchestrator._paths, task.assigned_agent)
                    available = (agent is not None and agent.role == 'manager' and agent.team == task.team
                        and continuation.org.teams.executable_manager_for_team(task.team) == task.assigned_agent)
                except (OSError, ValueError, TypeError, KeyError):
                    available = False
                if not available:
                    value.update(disposition='unavailable', unavailable_reason='initiator_agent_unavailable')
    return value


def ready(orch: Any, task: Any) -> bool:
    """Inbox readiness admits its own initiator, never an enclosing parent."""
    import sqlite3
    try:
        value = observation(orch._db, task.id)
        return bool(value and value['pending'] and value['disposition'] == 'pending' and task.task_type == 'task'
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
            if value is not None and value['disposition'] == 'unavailable':
                return None
            ids = value['ordinary_task_ids'] if value is not None else orch._db.get_children(task.id)
            return [orch._db.get_task(task_id) for task_id in ids]
    except (ValueError, TypeError, sqlite3.Error):
        return None


class WorkflowContinuation:
    def __init__(self, org: Any) -> None:
        self.org = org
        self.db = org.db
        # OrgState attaches separate callback and orchestrator bridge objects.
        # Their ephemeral original-owner handoff belongs to the shared DB.
        with self.db._lock:
            if not hasattr(self.db, '_workflow_cancel_publications'):
                self.db._workflow_cancel_publications = {}
            self._cancel_publications = self.db._workflow_cancel_publications

    @asynccontextmanager
    async def callback_admission(self, task_id: str, decision: dict | None,
                                 *, status: str = 'completed') -> AsyncIterator[None]:
        """File-backed control discovery happens before the native write fence."""
        capture, starts = None, None
        prior = self.db.get_latest_task_result(task_id, self.db.get_task(task_id).assigned_agent,
            self.db.get_task(task_id).current_session_id)
        if decision and decision.get('action') == 'workflow_start' and 'workflows' in decision and prior is None:
            from runtime.infrastructure.db.tasks import RetryClaim
            from runtime.workflows.sequential_start import prepare_generic_batch, _policy, _require_runtime
            from runtime.workflows.sequential_release import generic_start_catalog
            task = self.db.get_task(task_id)
            _require(status == 'completed', 'workflow_start_completed_required')
            from runtime.workflows.sequential_start_discovery import read_start_source_v2
            from runtime.workflows.sequential_history import _ReadBudget, _sql_budget
            budget = _ReadBudget()
            with self.db.coherent_read_view() as conn, _sql_budget(conn, budget):
                read_start_source_v2(conn, self.org, task_id=task_id, principal=task.assigned_agent,
                    session_id=task.current_session_id, budget=budget)
            _require_runtime(self.org)
            for command in decision['workflows']:
                pin = command['definition']
                compiled, = generic_start_catalog(pin['identity'] + '@' + str(pin['version']))
                _require(compiled.pin.model_dump(mode='json') == pin, 'workflow_start_pin_conflict')
            source = RetryClaim.from_task(task)
            starts = (prepare_generic_batch(self.org, decision=_bytes(decision), source=source),
                _policy(self.org, source))
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
        # Originals are attached inside the callback transaction and rolled
        # back on refusal. Only its successful COMMIT publishes this handoff.
        with ExitStack() as captures:
            cancellations = {}
            token = _control_capture.set((capture, captures, cancellations))
            start_token = _start_capture.set(starts)
            try:
                yield
                with self.db._lock:
                    for result_id, handoff in cancellations.items():
                        committed = self.db._conn.execute('SELECT 1 FROM workflow_seq_consumption_receipts WHERE result_id=?', (result_id,)).fetchone()
                        if committed is not None:
                            handoff['owners'].committed = True
                            self._cancel_publications[result_id] = handoff
                # A result consumer can run after native COMMIT but before
                # this context resumes. Complete its pending Stop here too;
                # the shared locked pop lets only one publisher take it.
                for result_id in cancellations:
                    self._publish_cancellation(result_id)
            finally:
                _control_capture.reset(token)
                _start_capture.reset(start_token)

    @contextmanager
    def writer_ownership(self, task_id: str):
        """Profile/publisher leases surround only the synchronous native writer."""
        starts, control = _start_capture.get(), _control_capture.get()
        authority = starts[0][0].authority if starts is not None else (
            control[0][1].authority if control is not None and control[0] is not None else None)
        with ExitStack() as stack:
            if starts is not None:
                from runtime.infrastructure.db.tasks import RetryClaim
                from runtime.workflows.sequential_start import _policy
                # The route has finished its async lock waits. Refresh file
                # policy before publication leases or the native transaction.
                source = RetryClaim.from_task(self.db.get_task(task_id))
                revise_limit = _policy(self.org, source)
                _require(revise_limit == 0 or source.revision < revise_limit,
                    'workflow_start_revisions_exhausted')
                token = _start_capture.set((starts[0], revise_limit))
                stack.callback(_start_capture.reset, token)
            if authority is not None:
                stack.enter_context(self.org.workflow_authority._admission_ownership(authority))
            yield

    def admitted_uncommitted(self, invocation: Any) -> None:
        """The single snapshot call site, after a genuinely new SID is bound."""
        from runtime.workflows.sequential_initiator import snapshot_delivery
        snapshot_delivery(self.db._conn, invocation.task.id, invocation.agent, invocation.session_id)

    def preflight_start_uncommitted(self, task_id: str) -> None:
        """Reject impossible reservations before the native result/attempt write.

        The actual batch writer still validates the complete resulting history.
        Both checks run under the same native writer and current authority lease.
        """
        from runtime.workflows.sequential_history import (
            _ReadBudget, _sql_budget, _reserve_generic_cancellation, read_sequential_history,
        )
        from runtime.workflows.sequential_engine import _engine_rows, _identity
        from runtime.workflows.sequential_start_discovery import read_start_source_v2
        from runtime.workflows.sequential_start import _generic_selection, _selected_layout
        from runtime.workflows.sequential_assignments import parse_assignment_start
        starts = _start_capture.get()
        _require(starts is not None, 'workflow_start_capture_missing')
        captures, revise_limit = starts
        conn, task, budget = self.db._conn, self.db.get_task(task_id), _ReadBudget()
        _require(conn.in_transaction and not self.org._runner_admission_closed, 'workflow_start_admission_closed')
        _require(revise_limit == 0 or task.revision_count < revise_limit, 'workflow_start_revisions_exhausted')
        with _sql_budget(conn, budget):
            read_start_source_v2(conn, self.org, task_id=task_id, principal=task.assigned_agent,
                session_id=task.current_session_id, budget=budget)
            read_sequential_history(conn, expected_org_slug=self.org.slug, deadline=budget.deadline)
            runs = _engine_rows(conn, 'workflow_seq_runs', budget, '1', ())
            visits = _engine_rows(conn, 'workflow_seq_visits', budget, '1', ())
            metadata = _selected_layout(conn, budget)
            for capture in captures:
                self.org.workflow_authority._validate_admission_uncommitted(conn, capture.authority)
                command = parse_assignment_start(capture.start_bytes)
                compiled, _, _ = _generic_selection(conn, org_slug=self.org.slug, command=command, budget=budget, metadata=metadata)
                run_id = _identity('run', self.org.slug, task.assigned_agent, task_id, command.operation_key)
                # Every fresh member starts with the definition's entry assignment.
                resolved = json.loads(capture.snapshot_bytes)
                principal = next(node['principal'] for node in resolved['nodes'] if node['node'] == compiled.definition.entry_node)
                visit_id = _identity('visit', run_id, '1')
                runs.append(dict(engine_version=2, state='running', id=run_id, source_task_id=task_id,
                    source_principal=task.assigned_agent, source_session_id=task.current_session_id,
                    carrier_task_id='TASK-SEQ-' + _identity('carrier', run_id), active_visit_id=visit_id))
                visits.append(dict(id=visit_id, task_id='TASK-SEQ-' + _identity('first-task', run_id),
                    principal=principal, session_id=None))
            _reserve_generic_cancellation(dict(workflow_seq_runs=runs, workflow_seq_visits=visits), budget)

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
        start_effects = None
        from runtime.workflows.sequential_start import _claim_action
        action = _claim_action(self.org, RetryClaim.from_task(self.db.get_task(task_id), result_row_id=result_id))
        if parsed.action == 'workflow_start':
            from runtime.workflows.sequential_start import admit_batch_uncommitted
            from runtime.workflows.sequential_start_discovery import read_start_source_v2
            from runtime.workflows.sequential_contracts import GenericLimits
            from runtime.workflows.sequential_history import _ReadBudget, _sql_budget
            from runtime.workflows.sequential_initiator import snapshot_delivery
            starts = _start_capture.get()
            _require(starts is not None, 'workflow_start_capture_missing')
            captures, revise_limit = starts
            task = self.db.get_task(task_id)
            _require(revise_limit == 0 or task.revision_count < revise_limit, 'workflow_start_revisions_exhausted')
            _require(not self.org._runner_admission_closed, 'workflow_start_admission_closed')
            budget = _ReadBudget()
            with _sql_budget(conn, budget):
                read_start_source_v2(conn, self.org, task_id=task_id, principal=agent,
                    session_id=session_id, budget=budget, result_id=result_id)
            # Engine model ceilings, per member; never a cross-run pool or step cap.
            remaining = tuple(GenericLimits(max_visits=64, max_events=4096, max_recoveries=64)
                for _ in captures)
            first = conn.execute('SELECT 1 FROM workflow_seq_source_wait_sets WHERE source_task_id=?',
                (task_id,)).fetchone() is None
            admissions = admit_batch_uncommitted(conn, self.db, org_slug=self.org.slug,
                source=RetryClaim.from_task(task, result_row_id=result_id), decision=_bytes(parsed.model_dump(mode='json')),
                captures=captures, authority=self.org.workflow_authority, remaining=remaining)
            if first:
                # Adoption and its actual empty delivered page belong to this first callback.
                page = snapshot_delivery(conn, task_id, agent, session_id)
                _require(not page['notifications'], 'workflow_first_delivery_conflict')
            start_effects = dict(action='workflow_start', run_ids=[item.run_id for item in admissions])

        def apply() -> dict:
            result = apply_effects()
            if action is not None:
                ack = self.db._acknowledge_v2_decision_dispatch_uncommitted(root_task_id=task_id,
                    manager_agent=action.manager_agent, result_id=action.causal_result_id)
                _require(ack.status == 'applied', 'workflow_action_ack_conflict')
            return result

        def apply_effects() -> dict:
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
                context = _control_capture.get()
                _require(context is not None and context[0] is not None, 'workflow_control_capture_missing')
                prepared, captures, cancellations = context
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
                if parsed.workflow['action'] == 'cancel':
                    from runtime.workflows.sequential_cancel_owners import capture_member_cancel
                    cancellations[result_id] = capture_member_cancel(self.org, conn, parsed.workflow['run_id'], captures)
                conn.execute("UPDATE tasks SET block_kind='delegated' WHERE id=?", (task_id,))
                return effects
            elif parsed.action == 'workflow_start':
                return start_effects
            if parsed.action == 'workflow_wait':
                conn.execute("UPDATE tasks SET block_kind='delegated' WHERE id=?", (task_id,))
            return {'action': parsed.action}

        consume_decision_uncommitted(conn, initiator_task_id=task_id, principal=agent,
            session_id=session_id, result_id=result_id, decision=_bytes(value), apply=apply)
        return True

    def prompt_context(self, task: Any) -> str:
        from types import SimpleNamespace
        from runtime.workflows.sequential_initiator import delivered_page, _rows
        from runtime.workflows.sequential_history import _ReadBudget, _sql_budget, _parse, _generic_limits_value
        from runtime.workflows.sequential_definition import compile_generic_definition, parse_generic_start, generic_limits
        from runtime.workflows.sequential_contracts import GenericLimits
        from runtime.workflows.sequential_control import result_counters
        from runtime.workflows.sequential_reducer import reduce_generic_control
        with self.db.coherent_read_view() as conn:
            task = self.db.get_task(task.id)
            state = observation(self.db, task.id)
            if state is None:
                return ''
            page = delivered_page(conn, task.id, task.assigned_agent, task.current_session_id)
            budget = _ReadBudget()
            with _sql_budget(conn, budget):
                for view in state['runs']:
                    run = _rows(conn, 'workflow_seq_runs', budget, 'id=?', (view['run_id'],))[0]
                    definition = _rows(conn, 'workflow_seq_definitions', budget, 'id=?', (run['definition_id'],))[0]
                    compiled = _parse(definition['definition_bytes'], compile_generic_definition, budget,
                        source_digest=definition['source_digest'])
                    history = {table: _rows(conn, 'workflow_seq_' + table, budget, 'run_id=?', (run['id'],))
                        for table in ('visits', 'events', 'controls', 'node_counters')}
                    counters = result_counters(run, compiled, history['visits'], history['events'],
                        history['controls'], history['node_counters'])
                    start = _parse(run['start_bytes'], parse_generic_start, budget)
                    original = GenericLimits.model_validate_json(run['original_limits_bytes'])
                    limits = generic_limits(compiled, original, start.lower_limits)
                    held = next((event for event in history['events'] if event['id'] == run['held_event_id']), None)
                    cause = None if held is None else json.loads(held['event_bytes']).get('cause')
                    run = dict(run, hold_cause=cause)
                    controls = {}
                    for action in ('retry', 'rebrief', 'reassign', 'cancel'):
                        command = dict(format='workflow-sequential-control@2', run_id=run['id'],
                            operation_key='<stable-key>', expected_cas=run['cas'],
                            expected_held_event_id=run['held_event_id'], action=action, reason='<reason>')
                        if action == 'rebrief':
                            command['supplement'] = '<same-goal supplement>'
                        if action == 'reassign':
                            command['target_agent_id'] = '<eligible permanent agent ID>'
                        try:
                            reduce_generic_control(compiled, SimpleNamespace(**command), run=run,
                                counters=counters, limits=limits)
                            controls[action] = dict(command=command, revalidate='current authority, assignment and positive closure required')
                        except ValueError as exc:
                            controls[action] = dict(refusal=str(exc))
                    view.update(cause=cause, stopped_node_id=run['held_node_id'],
                        counters=counters.model_dump(mode='json'), limits=_generic_limits_value(limits), controls=controls)
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

    def rediscover(self, *, queue: Any = None, diagnostics: bool = True) -> None:
        """Retry committed publication in bounded, independently fair pages."""
        import logging
        from runtime.workflows.sequential_history import _ReadBudget, _sql_budget
        selected = queue or getattr(self.org.orchestrator, '_queue', None)
        budget = _ReadBudget()
        with self.db.coherent_read_view() as conn:
            if conn.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='workflow_seq_source_wait_sets'").fetchone() is None:
                return
            with _sql_budget(conn, budget):
                ids = conn.execute('SELECT CASE WHEN typeof(source_task_id)=\'text\' AND '
                    'length(CAST(source_task_id AS BLOB))<=256 THEN source_task_id END '
                    'FROM workflow_seq_source_wait_sets WHERE source_task_id>? ORDER BY source_task_id LIMIT 33',
                    (getattr(self, '_source_cursor', ''),)).fetchall()
                visits = conn.execute("SELECT CASE WHEN typeof(task_id)='text' AND "
                    'length(CAST(task_id AS BLOB))<=256 THEN task_id END FROM workflow_seq_outbox '
                    "WHERE kind='visit_queued' AND task_id>? AND task_id IN "
                    "(SELECT id FROM tasks WHERE status='pending') ORDER BY task_id LIMIT 33",
                    (getattr(self, '_visit_cursor', ''),)).fetchall()
                _require(all(type(row[0]) is str and bool(row[0]) for row in [*ids, *visits]),
                    'publication_discovery_invalid')
                budget.reserve(66 * 512)
        self._source_cursor = ids[31][0] if len(ids) > 32 else ''
        self._visit_cursor = visits[31][0] if len(visits) > 32 else ''
        log = logging.getLogger('happyranch.workflows.sequential_settlement')
        if len(ids) > 32 or len(visits) > 32:
            log.warning('observation_incomplete org=%s reason=publication_partial_page', self.org.slug)
        if selected is not None:
            for (task_id,) in visits[:32]:
                try:
                    self.org.workflow_sequential_dispatch.notify_queued(task_id, selected)
                except Exception as exc:
                    log.warning('publication_failed org=%s task=%s reason=%s', self.org.slug, task_id, type(exc).__name__)
        for (task_id,) in ids[:32]:
            try:
                # Diagnostic certification is independent of wake eligibility.
                # Startup defers it to the normal periodic sweep; wake always
                # rechecks its own source fencing, drains and readiness below.
                if diagnostics:
                    value = observation(self.db, task_id)
                    if value and value['pending'] and value['disposition'] in ('initiator_terminal', 'unavailable'):
                        log.warning('initiator_unavailable org=%s task=%s reason=%s', self.org.slug, task_id, value['disposition'])
                self.wake(task_id, queue=queue)
            except Exception as exc:
                log.warning('publication_failed org=%s task=%s reason=%s', self.org.slug, task_id, type(exc).__name__)

    def _publish_cancellation(self, result_id: int) -> None:
        """Take one committed member handoff, then Stop outside the DB lock."""
        with self.db._lock:
            cancellation = self._cancel_publications.pop(result_id, None)
        if cancellation is not None:
            failed = False
            try:
                for _, _, control in cancellation['controls']:
                    control()
            except BaseException:
                failed = True
                raise
            finally:
                cancellation['owners'].seal(failed=failed)

    def publish_result(self, result_id: int, task_id: str) -> None:
        """Postcommit publication uses only the exact recorded effect receipt."""
        with self.db.coherent_read_view() as conn:
            row = conn.execute('SELECT receipt_bytes FROM workflow_seq_consumption_receipts '
                'WHERE result_id=? AND initiator_task_id=?', (result_id, task_id)).fetchone()
            _require(row is not None, 'initiator_receipt_missing')
            effects = json.loads(row[0])['effects']
        self._publish_cancellation(result_id)
        selected = getattr(self.org.orchestrator, '_queue', None)
        # Start entries are released exclusively by their ORIGINAL source settlement.
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
        return bool(self.wake(source_id, queue=queue))

    def wake(self, task_id: str, *, queue: Any = None) -> bool | None:
        """Return delivery, refusal, or None when no publication is needed."""
        from runtime.workflows.sequential_mixed_cancel import fence_terminal_source
        from runtime.workflows.sequential_cancellation_tail import drain_source_cancellations
        terminal = fence_terminal_source(self.org, task_id)
        drain_source_cancellations(self.org, task_id)
        if terminal:
            return None
        task = self.db.get_task(task_id)
        if task is None or not ready(self.org.orchestrator, task):
            return None
        selected = queue or getattr(self.org.orchestrator, '_queue', None)
        if selected is not None:
            return selected.enqueue_if_absent(self.org.slug, task_id)
        return False

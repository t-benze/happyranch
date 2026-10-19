"""Production owner for the disabled first coding development invocation.

Queue and launch admission remain unavailable until the whole coding engine is
present. This owner composes native writers and their SELECT-only readers; it
has no artifact, receipt, successor, control or legacy completion consumer.
"""
from __future__ import annotations

import asyncio
import base64
import json
import uuid
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Iterator

from runtime.workflows.coding_bootstrap import encode_row, identity
from runtime.workflows.coding_saved_records import _canonical, _check, _hash
from runtime.workflows.coding_start import CodingStartError, queued_storage, require_ready
from runtime.workflows.coding_start_history import rows, same
from runtime.workflows.coding_authority_history import _Budget


def stamp() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def _native_value(value: Any) -> Any:
    """Bounded dataclass observations, never exception prose or opaque controls."""
    from enum import Enum
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _native_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_native_value(v) for v in value]
    _check(value is None or type(value) in (str, bool, int, float))
    return value


def terminal_observation(outcome: Any) -> dict | None:
    if outcome is None:
        return None
    request = outcome.request
    return dict(request=dict(org=request.org, invocation_kind=request.invocation_kind,
            logical_id=request.logical_id, executor_profile=request.executor_profile,
            enqueued_at=request.enqueued_at, retry_attempt=request.retry_attempt, agent_id=request.agent_id),
        terminal_reason=outcome.terminal_reason.value, attempt=outcome.attempt,
        receipt=None if outcome.receipt is None else _native_value(asdict(outcome.receipt)),
        cleanup_error=outcome.cleanup_error)


class CodingDispatcher:
    def __init__(self, org: Any) -> None:
        self.org, self.db = org, org.db
        self._queue_live: dict[str, dict] = {}
        self._queue_generation = 0
        self.producer = 'coding-dispatcher:' + org.slug

    def _on_loop(self, coroutine: Any) -> Any:
        loop = self.org.orchestrator._main_loop
        if loop is None or not loop.is_running():
            coroutine.close()
            raise CodingStartError('coding_host_unavailable')
        return asyncio.run_coroutine_threadsafe(coroutine, loop).result()

    def _entry(self, conn: Any, task_id: str) -> dict:
        saved = queued_storage(conn, self.org.slug)
        owner = saved.owners.get(task_id)
        _check(owner is not None)
        return saved.runs[owner[1]]

    @contextmanager
    def _writer(self) -> Iterator[Any]:
        with self.db._lock:
            conn = self.db._conn
            _check(not conn.in_transaction)
            conn.execute('BEGIN IMMEDIATE')
            try:
                yield conn
                from runtime.infrastructure.workflow_schema import validate_workflow_schema
                validate_workflow_schema(conn, expected_org_slug=self.org.slug)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def queue_admission_allowed(self, task_id: str) -> bool:
        # This check precedes generation, SID, Pause preparation, counter and
        # host effects. It has no caller/config readiness Boolean.
        try:
            require_ready()
        except CodingStartError:
            return False
        with self.db._lock:
            entry = self._entry(self.db._conn, task_id)
            return task_id == entry['attempt']['task_id'] and entry['attempt']['state'] == 'queued'

    def queue_started(self, task_id: str) -> dict:
        # Called only by the actual native queue worker after its own admission.
        # Every ordinary worker gets a lifetime too: an original source can
        # become coding-owned during that SAME worker's result-action tail.
        with self.db._lock:
            self._queue_generation += 1
            owner = dict(owner_id=uuid.uuid4().hex, queue_generation=self._queue_generation,
                         claim_identity=uuid.uuid4().hex)
            self._queue_live.setdefault(task_id, {})[owner['owner_id']] = owner
            return owner

    def queue_finished(self, task_id: str, owner: dict) -> None:
        with self.db._lock:
            owners = self._queue_live.get(task_id, {})
            _check(owners.get(owner['owner_id']) is owner)
            owners.pop(owner['owner_id'])
            if not owners:
                self._queue_live.pop(task_id, None)

    def bind_queue_invocation(self, invocation: Any) -> None:
        # This is only an observation of the real queue owner. Ordinary duplicate
        # deliveries retain their original native claim semantics and are not
        # made ineligible merely because attribution is unavailable.
        with self.db._lock:
            owners = self._queue_live.get(invocation.task.id, {})
            if len(owners) == 1:
                owner = next(iter(owners.values()))
                owner['claim_identity'] = invocation.identity

    def heartbeat(self, task_id: str, owner: dict | None) -> None:
        with self.db._lock:
            owners = self._queue_live.get(task_id, {})
            _check(owner is not None and owners.get(owner['owner_id']) is owner)
            with self._writer() as conn:
                entry = self._entry(conn, task_id)
                kind = ('source' if task_id == entry['source']['task_id'] else 'attempt'
                        if task_id == entry['attempt']['task_id'] else None)
                _check(kind is not None)
                expected_claim = (entry['source']['result_claim']['owner_invocation_id'] if kind == 'source'
                    else entry['native']['claim']['invocation_id'] if entry['native']['claim'] is not None else None)
                _check(expected_claim is None or owner['claim_identity'] == expected_claim)
                anchor, digest, previous = entry['native']['anchors'][task_id]
                before = entry['tasks'][task_id]
                now = stamp()
                after = {**before, 'updated_at': now, 'last_heartbeat': now}
                payload = dict(format='workflow-coding-task-heartbeat@1', instance_id=entry['run']['instance_id'],
                    task_id=task_id, queue_owner=dict(kind=kind, **owner), session_id=before['current_session_id'],
                    previous_audit_id=previous, anchor_event_id=anchor, anchor_task_digest=digest,
                    before_record=encode_row(before), after_record=encode_row(after), observed_at=now)
                self.db.record_coding_heartbeat_uncommitted(task_id=task_id, before=before, after=after, payload=payload)

    def notify_queued(self, task_id: str, queue: Any) -> None:
        if self.queue_admission_allowed(task_id):
            queue.enqueue_if_absent(self.org.slug, task_id)

    def dispatch(self, task_id: str) -> None:
        require_ready()  # Direct owned routing cannot bypass native queue denial.
        from runtime.orchestrator.task_pause import TaskInvocation, _invocation, release_live
        with self.db._lock:
            entry = self._entry(self.db._conn, task_id)
            _check(entry['attempt']['task_id'] == task_id and entry['attempt']['state'] == 'queued')
            task = self.db.get_task(task_id)
        invocation = TaskInvocation(self.org.orchestrator, task, task.assigned_agent,
                                    self.org.orchestrator._build_session_id(), owner='coding')
        invocation.workflow_dispatcher = self
        invocation.coding_claim_token = uuid.uuid4().hex
        invocation.draft_request_key = identity('host', entry['attempt']['id'], invocation.identity)
        invocation.prepare()
        token = _invocation.set(invocation)
        failed = False
        try:
            # The actual native publisher supplies workspace, session_start,
            # prompt rebuild, AdmissionRequest, handle and terminal observations.
            self.org.orchestrator._run_agent(task_id, task.assigned_agent, task.brief)
        except BaseException:
            failed = True
            raise
        finally:
            _invocation.reset(token)
            release_live(invocation, failed=failed)

    def _policies(self, entry: dict) -> dict:
        # File-backed executable roles are resolved outside synchronous owners.
        from runtime.orchestrator import prompt_loader
        from runtime.orchestrator.org_config import load_org_config
        policies = {}
        for role, binding in entry['roles'].items():
            policy = prompt_loader.load_agent(self.org.orchestrator._paths, binding['principal'])
            _check(policy is not None and policy.executor and policy.team == binding['team'])
            _check((role == 'agent_manager') == (policy.role == 'manager'))
            policies[role] = policy
        _check(load_org_config(self.org.orchestrator._paths).max_revise_rounds ==
               entry['source']['budgets']['max_revise_rounds'])
        return policies

    def _eligible(self, conn: Any, entry: dict, invocation: Any, capture: Any, sessions: tuple) -> None:
        from runtime.workflows._coding_role_records import configuration
        from runtime.workflows.coding_start_records import effective_mapping
        from runtime.workflows.activation import author_capacity_blocked
        from runtime.workflows.coding_mapping_history import read_mapping_storage
        _check(not self.org._runner_admission_closed and invocation.orch is self.org.orchestrator
               and invocation.owner == 'coding' and invocation.workflow_dispatcher is self
               and invocation is invocation.store._live.get(invocation.identity))
        self.org.workflow_authority._validate_admission_uncommitted(conn, capture)
        mappings = read_mapping_storage(conn, expected_org_slug=self.org.slug)
        pinned = next(m for m in mappings.mappings if m.mapping_id == entry['command']['mapping']['id'])
        mapping = json.loads(pinned.mapping_bytes)['mapping']
        current = json.loads(capture.ready.snapshot_bytes)
        configuration(mapping, current)
        configuration(effective_mapping(mapping, entry['command']), current)
        _check(self.org.teams.teams_for_manager(entry['source']['principal']) ==
               (entry['roles']['agent_manager']['team'],))
        for binding in entry['roles'].values():
            _check(binding['team'] == entry['roles']['agent_manager']['team'])
        _check(not entry['native'].get('ownership_unknown')
               and entry['native']['source_settled'] and entry['run']['state'] == 'development'
               and entry['attempt']['task_id'] == invocation.task.id
               and entry['attempt']['principal'] == invocation.agent)
        for task in entry['tasks'].values():
            self.db._check_team_move_task_uncommitted(task['id'])
            _check(task['cancelled_at'] is None and task['zombie_flagged_at'] is None)
        self.db._check_team_move_workflow_uncommitted(binding_bytes=entry['binding']['binding_bytes'])
        invocation.store.held_uncommitted(invocation.task.id)
        if author_capacity_blocked(conn, author=invocation.agent, task_id=invocation.task.id, active_sessions=sessions):
            raise CodingStartError('coding_capacity_unavailable')
        _check(entry['attempt']['revision'] == 1 and entry['attempt']['attempt_sequence'] == 1
               and entry['source']['budgets']['coding_attempt_limit'] >= 1
               and entry['run']['cas_version'] + 2 <= entry['source']['budgets']['coding_event_limit'])

    async def _commit_pause_launch(self, invocation: Any, ctx: Any) -> bool:
        require_ready()  # Alternate native TaskInvocation.commit remains closed.
        with self.db._lock:
            entry = self._entry(self.db._conn, invocation.task.id)
        self._policies(entry)
        capture = self.org.workflow_authority.capture_admission()
        async with self.org.workflow_authority._async_writer_lock:
            self._policies(entry)
            async with self.org.teams_lock:
                self._policies(entry)
                async with self.org.db_lock:
                    self._policies(entry)
                    sessions = tuple(self.org.sessions.iter_active())
                    with self.org.workflow_authority._admission_ownership(capture):
                        invocation.coding_capture, invocation.coding_sessions = capture, sessions
                        return invocation._commit_local(ctx)

    def _claim_launch_uncommitted(self, invocation: Any) -> None:
        require_ready()
        conn = self.db._conn
        entry = self._entry(conn, invocation.task.id)
        self._eligible(conn, entry, invocation, invocation.coding_capture, invocation.coding_sessions)
        _check(entry['attempt']['state'] == 'queued' and not invocation.admitted)
        invocation.coding_before = entry
        row = invocation._entry(invocation.store.row_uncommitted(entry['source']['root_ancestor_id']))
        invocation.coding_claim = dict(token=invocation.coding_claim_token,
            owner=self.producer + ':' + invocation.identity, invocation_id=invocation.identity,
            pause_entry_id=invocation.identity, pause_generation=row['generation'],
            task_fingerprint=deepcopy(row['fingerprint']), claimed_at=stamp())
        self.db.claim_coding_step_uncommitted(task_id=invocation.task.id,
                                             before=entry['tasks'][invocation.task.id])

    def _projection(self, entry: dict) -> dict:
        return dict(run=deepcopy(entry['run']), attempts=[dict(id=entry['attempt']['id'], record=encode_row(entry['attempt']))],
            instances=_json_start(entry)['after']['instances'],
            tasks=[dict(id=k, record=encode_row(v)) for k, v in sorted(entry['tasks'].items())],
            rounds=[], requests=[], assignments=[])

    def _append(self, conn: Any, entry: dict, kind: str, facts: dict, *, invocation: Any,
                run_changes: dict | None = None, attempt_changes: dict | None = None,
                native_rows: list | None = None, event_time: str | None = None) -> None:
        from runtime.workflows.coding_native_history import KINDS
        _check(conn.in_transaction and kind in KINDS)
        previous = entry['native']['history'][-1]
        _check(previous['event_seq'] < entry['source']['budgets']['coding_event_limit'])
        source_tail = invocation.task.id == entry['source']['task_id']
        claim = None if source_tail else getattr(invocation, 'coding_claim', entry['native']['claim'])
        producer = invocation.identity if source_tail else claim['owner']
        row = invocation.store.row_uncommitted(entry['source']['root_ancestor_id'])
        matches = [] if row is None else [r for r in row['journal']['entries'] if r['id'] == invocation.identity]
        native = deepcopy(matches[0]) if len(matches) == 1 else deepcopy(getattr(invocation, 'coding_settled_entry', None))
        with self.db._lock:
            candidates = [owner for owner in self._queue_live.get(invocation.task.id, {}).values()
                          if owner['claim_identity'] == invocation.identity]
            queue_owner = deepcopy(candidates[0]) if len(candidates) == 1 else None
        observation = dict(facts=facts, invocation=native, queue_owner=queue_owner)
        command = dict(format='workflow-coding-internal-command@1', kind=kind,
            source_event_ids=[previous['id']], source_event_digests=[previous['event_digest']],
            subject=None, producer_identity=producer, observation=observation)
        instance = entry['run']['instance_id']
        key = 'coding-internal:' + _hash(_canonical([instance, kind, producer, [previous['id']], observation]))
        principal = 'service:' + self.producer
        event_id = identity('event', self.org.slug, instance, principal, key)
        now = event_time or stamp()
        before = self._projection(entry)
        after = deepcopy(before)
        after['run'].update(cas_version=entry['run']['cas_version'] + 1, last_event_id=event_id, **(run_changes or {}))
        attempt = {**entry['attempt'], **(attempt_changes or {})}
        after['attempts'] = [dict(id=attempt['id'], record=encode_row(attempt))]
        after['tasks'] = [dict(id=k, record=encode_row(dict(conn.execute('SELECT * FROM tasks WHERE id=?', (k,)).fetchone())))
                          for k in sorted(entry['tasks'])]
        operation = dict(org_slug=self.org.slug, principal=principal, key=key, request_digest=_hash(_canonical(command)))
        body = dict(format='workflow-coding-event@1', id=event_id, instance_id=instance,
            event_seq=previous['event_seq'] + 1, event_kind=kind, attempt_id=attempt['id'], operation=operation,
            previous_digest=previous['event_digest'], before=before, after=after,
            evidence=dict(kind=kind, command=command, origin=dict(kind='service', principal=invocation.agent,
                task_id=invocation.task.id, session_id=invocation.session_id, result_id=None, claim=claim),
                authority=entry['authority'], rows=native_rows or [], facts=facts,
                native_task_observations=entry['native'].get('pending_observations', [])), created_at=now)
        raw = _canonical(body)
        _check(len(raw) <= 16777216)
        conn.execute('UPDATE workflow_coding_attempts SET session_id=?,result_id=?,state=? WHERE id=?',
            (attempt['session_id'], attempt['result_id'], attempt['state'], attempt['id']))
        # FK points at the newly inserted event only after that row exists.
        conn.execute('INSERT INTO workflow_coding_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (event_id, instance, body['event_seq'], attempt['id'], self.org.slug, principal, key,
             operation['request_digest'], kind, previous['event_digest'], raw, _hash(raw), now))
        conn.execute('UPDATE workflow_coding_runs SET cas_version=?,state=?,last_event_id=? WHERE instance_id=?',
            (after['run']['cas_version'], after['run']['state'], event_id, instance))

    def _session_bound_uncommitted(self, invocation: Any) -> None:
        conn = self.db._conn
        entry = invocation.coding_before
        audits = list(rows(conn, 'audit_log', _Budget(), where="task_id=? AND action='session_start'", args=(invocation.task.id,)))
        audits = [r for r in audits if json.loads(r['payload']).get('session_id') == invocation.session_id]
        _check(len(audits) == 1)
        now = stamp()
        conn.execute('UPDATE tasks SET updated_at=? WHERE id=?', (now, invocation.task.id))
        self._append(conn, entry, 'session_bound', dict(claim=invocation.coding_claim,
            session_start_audit_id=audits[0]['id'], session_purpose='worker_execution', session_id=invocation.session_id),
            invocation=invocation, attempt_changes=dict(session_id=invocation.session_id, state='claimed'),
            native_rows=[dict(table='audit_log', id=audits[0]['id'], record=encode_row(audits[0]))], event_time=now)
        entry = self._entry(conn, invocation.task.id)
        host = self._host(invocation)
        self._append(conn, entry, 'launch_reserved', dict(host=host, retry=invocation._entry(
            invocation.store.row_uncommitted(entry['source']['root_ancestor_id']))['retry']), invocation=invocation)

    def _host(self, invocation: Any) -> dict:
        request = invocation.coding_request
        claim = invocation.coding_claim
        raw = _canonical(dict(org=request.org, invocation_kind=request.invocation_kind,
            logical_id=request.logical_id, executor_profile=request.executor_profile,
            enqueued_at=request.enqueued_at, retry_attempt=request.retry_attempt, agent_id=request.agent_id))
        _check(request.org == self.org.slug and request.agent_id == invocation.agent
               and request.logical_id == invocation.draft_request_key and request.retry_attempt == 0)
        return dict(logical_request_key=request.logical_id, execution_key=request.logical_id,
            invocation_id=invocation.identity, host_attempt=request.retry_attempt, session_id=invocation.session_id,
            claim_token=claim['token'], claim_owner=claim['owner'], launch_started=True,
            request_bytes_base64=base64.b64encode(raw).decode('ascii'), request_digest=_hash(raw),
            handle_id=None, backend=None, terminal_receipt=None, cleanup=None)

    def _invocation(self, task_id: str, session_id: str) -> Any:
        from runtime.orchestrator.task_pause import current_invocation
        invocation = current_invocation()
        _check(invocation is not None and invocation.owner == 'coding' and invocation.workflow_dispatcher is self
               and invocation.task.id == task_id and invocation.session_id == session_id
               and invocation is invocation.store._live.get(invocation.identity))
        return invocation

    def observed_handle(self, task_id: str, agent: str, session_id: str, running: Any) -> None:
        invocation = self._invocation(task_id, session_id)
        _check(invocation.agent == agent and running.request_id == invocation.coding_request.logical_id)
        with self._writer() as conn:
            entry = self._entry(conn, task_id)
            host = {**entry['native']['host'], 'handle_id': _hash(_canonical(dict(
                token=running.token, request_id=running.request_id, backend=running.backend))), 'backend': running.backend}
            if entry['native']['host']['handle_id'] is not None:
                _check(same(host, entry['native']['host']))
                return
            self._append(conn, entry, 'launch_observed', dict(host=host), invocation=invocation,
                         attempt_changes=dict(state='running'))
        invocation.coding_running = running

    def record_pid(self, *, task_id: str, agent: str, session_id: str, pid: int) -> bool:
        invocation = self._invocation(task_id, session_id)
        _check(type(pid) is int and pid > 0 and invocation.agent == agent)
        with self._writer() as conn:
            entry = self._entry(conn, task_id)
            _check(entry['native']['host']['handle_id'] is not None)
            before = entry['tasks'][task_id]
            _check(before['current_session_id'] == session_id and before['assigned_agent'] == agent
                   and before['status'] == 'in_progress' and before['cancelled_at'] is None)
            if before['executor_pid'] == pid:
                return True
            conn.execute('UPDATE tasks SET executor_pid=? WHERE id=? AND assigned_agent=? AND current_session_id=? AND status=? AND cancelled_at IS NULL',
                         (pid, task_id, agent, session_id, 'in_progress'))
            raw = _canonical(dict(task_id=task_id, agent=agent, session_id=session_id, pid=pid))
            self._append(conn, entry, 'host_reconciled', dict(host=entry['native']['host'],
                source_owner_event_ids=[entry['native']['history'][-1]['id']], observation_bytes_base64=base64.b64encode(raw).decode('ascii'),
                observation_digest=_hash(raw), recovery_owner=entry['native']['claim']['owner']), invocation=invocation)
        return True

    def callback_uncommitted(self, *, task_id: str, agent: str, session_id: str, payload: dict) -> bool:
        from runtime.infrastructure.database import completion_result_payload_matches
        conn = self.db._conn
        entry = self._entry(conn, task_id)
        attempt = entry['attempt']
        result = entry['native']['callback']
        if result is not None:
            return ((result['task_id'], result['agent'], result['session_id']) == (task_id, agent, session_id)
                    and completion_result_payload_matches(result, **payload))
        if (attempt['principal'] != agent or attempt['session_id'] != session_id
                or attempt['state'] not in ('claimed', 'running') or payload['decision_json'] is not None
                or payload['waiting_on_job_ids'] is not None or payload['status'] not in ('completed', 'failed', 'blocked')):
            return False
        invocations = [i for i in self.db._task_pause_store._live.values()
                       if i.identity == entry['native']['claim']['invocation_id'] and i.owner == 'coding']
        if len(invocations) != 1:
            return False
        invocation = invocations[0]
        self._eligible(conn, entry, invocation, invocation.coding_capture, invocation.coding_sessions)
        self.db._insert_task_result(task_id=task_id, agent=agent, session_id=session_id, **payload)
        result_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        result = dict(conn.execute('SELECT * FROM task_results WHERE id=?', (result_id,)).fetchone())
        self._append(conn, entry, 'callback_recorded', dict(accepted=dict(id=result_id, record=encode_row(result)),
            attempt_id=attempt['id'], session_id=session_id, callback_digest=_hash(_canonical(encode_row(result))), historical_only=False),
            invocation=invocation, attempt_changes=dict(result_id=result_id),
            native_rows=[dict(table='task_results', id=result_id, record=encode_row(result))])
        return True

    def callback_replay(self, *, task_id: str, agent: str, session_id: str, payload: dict) -> bool:
        from runtime.infrastructure.database import completion_result_payload_matches
        with self.db._lock:
            entry = self._entry(self.db._conn, task_id)
            result = entry['native']['callback']
            return bool(result is not None and (result['task_id'], result['agent'], result['session_id']) ==
                        (task_id, agent, session_id) and completion_result_payload_matches(result, **payload))

    def terminal(self, task_id: str, agent: str, session_id: str, outcome: Any, *, expected_request: Any = None) -> None:
        invocation = self._invocation(task_id, session_id)
        from runtime.orchestrator.run_step import _capture_root_worktree_transition, _RootWorktreeTransitions, _root_worktree_batch
        transitions = _RootWorktreeTransitions()
        failed = True
        try:
            with self._writer() as conn, _capture_root_worktree_transition(
                    self.org.orchestrator, task_id, 'coding_dispatch.terminal', transitions):
                entry = self._entry(conn, task_id)
                host = entry['native']['host']
                _check(host is not None and outcome is not None and outcome.request is expected_request
                       and expected_request is invocation.coding_request and outcome.attempt == 0)
                receipt = None if outcome.receipt is None else _native_value(asdict(outcome.receipt))
                safe = (receipt is not None and host['handle_id'] is not None and receipt['backend'] == host['backend']
                        and receipt['backend'] != 'passthrough' and receipt['quiescent'] is True
                        and receipt['cleanup_status'] != 'incomplete' and not receipt['survivors'] and outcome.cleanup_error is None)
                result = entry['native']['callback']
                classification = ('success' if safe and receipt['terminal_reason'] == 'success' and result is not None and result['status'] == 'completed'
                    else 'failed' if safe and result is not None and result['status'] == 'failed'
                    else 'blocked' if safe and result is not None and result['status'] == 'blocked' else 'unknown')
                host = {**host, 'terminal_receipt': receipt, 'cleanup': dict(error=outcome.cleanup_error)}
                now = stamp()
                if classification in ('success', 'failed'):
                    conn.execute('UPDATE tasks SET status=?,completed_at=?,updated_at=?,block_kind=NULL,final_output_summary=?,final_output_dir=? WHERE id=?',
                        ('completed' if classification == 'success' else 'failed', now, now, result['output_summary'], result['output_dir'], task_id))
                self._append(conn, entry, 'terminal_recorded', dict(host=host, accepted_result_id=entry['attempt']['result_id'],
                    terminal_class=classification, positive_no_launch=None, outer_cleanup_pending=True), invocation=invocation,
                    run_changes={} if classification == 'success' else dict(state='held'),
                    attempt_changes={} if classification == 'success' else dict(state={
                        'failed': 'failed', 'blocked': 'blocked', 'unknown': 'uncertain'}[classification]), event_time=now)

            failed = False
        finally:
            if transitions:
                _root_worktree_batch(self.org.orchestrator, transitions, teardown_incomplete=failed)

    def outer_settled(self, invocation: Any, *, failed: bool, cleanup: Any, native_entry: dict | None) -> None:
        # Called by the native settle_live finally owner after tracker, task tail,
        # root worktree batch and native close/release. No absence is a witness.
        with self.db._lock:
            if self.db._conn.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='workflow_coding_runs'").fetchone() is None:
                return
            if self.db._conn.execute('SELECT 1 FROM workflow_coding_runs LIMIT 1').fetchone() is None:
                return
        with self._writer() as conn:
            saved = queued_storage(conn, self.org.slug)
            ownership = saved.owners.get(invocation.task.id)
            if ownership is None:
                return
            entry = saved.runs[ownership[1]]
            if ownership[0] == 'coding_carrier':
                raise CodingStartError('coding_structural_launch_refused')
            source = ownership[0] == 'coding_source_waiting'
            safe = (not failed and cleanup is not None and cleanup.kind == 'safe'
                    and not cleanup.processes and not invocation.unsettled
                    and native_entry is not None and not native_entry['context'].get('execution_unknown'))
            tracker_clear = self.org.sessions.get_active(invocation.task.id, invocation.agent) is None
            safe = safe and tracker_clear
            disposition = dict(kind='safe' if safe else 'unknown', reason='native_outer_settlement',
                processes=[] if cleanup is None else list(cleanup.processes), invocation=native_entry,
                terminal=getattr(invocation, 'native_terminal_observation', None), tracker_clear=tracker_clear,
                reconciliation_complete=not failed, task_tail_complete=cleanup is not None,
                root_worktree_batch_complete=cleanup is not None and cleanup.kind == 'safe', settle_live_complete=not failed)
            raw = _canonical(disposition)
            now = stamp()
            facts = dict(host_identity=None if source else entry['native']['host']['logical_request_key'],
                claim_token=None if source else entry['native']['claim']['token'], pause_entry_id=invocation.identity,
                disposition_bytes_base64=base64.b64encode(raw).decode('ascii'), disposition_digest=_hash(raw),
                settled=safe, execution_unknown=not safe, settlement_error=None if safe else 'coding_outer_settlement_unknown')
            # Preserve the authentic final entry even when close removed it.
            invocation.coding_settled_entry = native_entry
            self._append(conn, entry, 'cleanup_settled', facts, invocation=invocation,
                run_changes={} if safe else dict(state='held'),
                attempt_changes=dict(state='completed') if not source and safe and entry['native']['terminal'] == 'success' else {},
                event_time=now)

    def development_instruction(self, task_id: str, agent: str, session_id: str) -> str:
        with self.db._lock:
            entry = self._entry(self.db._conn, task_id)
            attempt = entry['attempt']
            _check(task_id == attempt['task_id'] and agent == attempt['principal']
                   and attempt['state'] in ('queued', 'claimed', 'running')
                   and attempt['session_id'] in (None, session_id))
            supplement = dict(format='workflow-coding-development@1', instance_id=entry['run']['instance_id'],
                attempt_id=attempt['id'], task_id=task_id, session_id=session_id, role='implementer',
                revision=attempt['revision'], assignment_generation=attempt['assignment_generation'],
                scope_digest=attempt['scope_digest'], release=entry['command']['release'],
                mapping=entry['command']['mapping'], work=entry['command']['work'], head=None,
                reading='Use your existing authorized repository tools. A reference grants no access or execution authority.',
                output=dict(format='workflow-code-change@1', repository_id=entry['command']['work']['repository_id'],
                    object_format=entry['command']['work']['object_format'], base=entry['command']['work']['base'],
                    head=None, pull_request=None),
                output_obligation='Return only the full commit genuinely produced by this task, bound to this fixed base and repository. PR navigation is not approval identity.',
                final_callback=dict(task_id=task_id, agent=agent, session_id=session_id,
                    command='happyranch report-completion --org ' + self.org.slug + ' --from-file ABS',
                    obligation='Report your own authentic result as the final action. No staging/advancement command is available.'))
        return '\n\n' + _canonical(supplement).decode('utf8')


def _json_start(entry: dict) -> dict:
    return json.loads(entry['event']['event_bytes'])

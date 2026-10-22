"""Native owner for disabled development, review, QA and final manager.

Queue and launch admission remain unavailable until the whole coding engine is
present. Original native writers compose complete readers and the coding
ordered edges through QA; finalization, controls and legacy effects remain absent.
"""
from __future__ import annotations

import asyncio
import base64
import json
import uuid
from contextlib import asynccontextmanager, contextmanager
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Iterator

from runtime.workflows.coding_bootstrap import encode_row, identity
from runtime.workflows.coding_saved_records import _canonical, _check, _hash
from runtime.workflows.coding_start import CodingStartError, queued_storage, require_ready
from runtime.workflows._coding_settings_execution import _read_json
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
        self.producer = 'coding-dispatcher:' + org.slug

    def _on_loop(self, coroutine: Any) -> Any:
        loop = self.org.orchestrator._main_loop
        if loop is None or not loop.is_running():
            coroutine.close()
            raise CodingStartError('coding_host_unavailable')
        return asyncio.run_coroutine_threadsafe(coroutine, loop).result()

    def _entry(self, conn: Any, task_id: str, *, budget: _Budget | None = None) -> dict:
        _check(budget is None or type(budget) is _Budget)
        budget = _Budget() if budget is None else budget
        saved = queued_storage(conn, self.org.slug, budget=budget)
        return self._selected_entry(saved, task_id, budget)

    def _selected_entry(self, saved: Any, task_id: str, budget: _Budget) -> dict:
        owner = saved.owners.get(task_id)
        _check(owner is not None)
        entry = saved.runs[owner[1]]
        if owner[0] == 'coding_attempt':
            selected = next(a for a in entry['attempts'].values() if a['task_id'] == task_id)
            if selected['id'] != entry['attempt']['id']:
                entry = {**entry, 'attempt': selected, 'native': {**entry['native'],
                    **entry['native']['stages'][selected['id']]}}
        return {**entry, '_budget': budget}

    @contextmanager
    def _writer(self) -> Iterator[tuple[Any, _Budget]]:
        with self.db._lock:
            conn = self.db._conn
            _check(not conn.in_transaction)
            conn.execute('BEGIN IMMEDIATE')
            budget = _Budget()
            try:
                yield conn, budget
                from runtime.infrastructure.workflow_schema import _read_workflow_schema
                _read_workflow_schema(conn, expected_org_slug=self.org.slug, budget=budget)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def require_task_ready(self, task_id: str) -> None:
        require_ready()
        with self.db._lock:
            self._entry(self.db._conn, task_id)

    def queue_admission_allowed(self, task_id: str) -> bool:
        # This check precedes generation, SID, Pause preparation, counter and
        # host effects. It has no caller/config readiness Boolean.
        try:
            require_ready()
        except CodingStartError:
            return False
        with self.db._lock:
            entry = self._entry(self.db._conn, task_id)
            return (task_id == entry['attempt']['task_id'] and entry['attempt']['state'] == 'queued'
                    and entry['run']['current_attempt_id'] == entry['attempt']['id']
                    and entry['run']['state'] == entry['attempt']['stage']
                    and entry['native']['source_settled'] and not entry['native'].get('ownership_unknown')
                    and entry['native']['claim'] is None and entry['native']['host'] is None)

    def queue_started(self, task_id: str) -> dict:
        # The queue calls this before scheduling its first heartbeat. A coding
        # worker owns the actual native preparation, not a generated stand-in.
        from runtime.workflows.recovery import classify_task
        from runtime.orchestrator.task_pause import TaskInvocation
        with self.db._lock:
            coding = classify_task(self.db, task_id, org_slug=self.org.slug).kind == 'coding_attempt'
        if coding:
            require_ready()  # Before creating a SID or preparing any owner.
            with self.db._lock:
                entry = self._entry(self.db._conn, task_id)
                task = self.db.get_task(task_id)
            invocation = TaskInvocation(self.org.orchestrator, task, task.assigned_agent,
                self.org.orchestrator._build_session_id(), owner='coding')
            invocation.workflow_dispatcher = self
            invocation.coding_claim_token = uuid.uuid4().hex
            invocation.draft_request_key = identity('host', entry['attempt']['id'], invocation.identity)
            owner = dict(invocation=invocation, binding=None)
        else:
            owner = dict(invocation=None, binding=None)
        with self.db._lock:
            self._queue_live.setdefault(task_id, {})[id(owner)] = owner
        if coding:
            try:
                invocation.prepare()
            except BaseException as exc:
                owner['invocation'] = None
                self.queue_finished(task_id, owner)
                if invocation.move_reservation is not None:
                    # A failed prepare may already own a move reservation even
                    # when the journal COMMIT or live publication failed.
                    from runtime.orchestrator.task_pause import release_live
                    try:
                        release_live(invocation, failed=True)
                    except BaseException as cleanup_error:
                        exc.add_note(f'coding queue preparation settlement failed: {type(cleanup_error).__name__}')
                raise
        return owner

    def queue_finished(self, task_id: str, owner: dict) -> None:
        with self.db._lock:
            owners = self._queue_live.get(task_id, {})
            _check(owners.get(id(owner)) is owner)
            owners.pop(id(owner))
            if not owners:
                self._queue_live.pop(task_id, None)
        invocation = owner['invocation']
        if invocation is not None:
            # The route may have refused before dispatch adopted preparation.
            # Its real queue owner still owes native close/release.
            from runtime.orchestrator.task_pause import release_live
            with self.db._lock:
                live = invocation.store._live.get(invocation.identity) is invocation
            if live:
                release_live(invocation, failed=True)

    def bind_queue_invocation(self, invocation: Any) -> None:
        with self.db._lock:
            owners = self._queue_live.get(invocation.task.id, {})
            candidates = [o for o in owners.values() if o['invocation'] is invocation]
            if not candidates and len(owners) == 1:
                candidates = [next(iter(owners.values()))]
            if len(candidates) != 1:
                return  # Ordinary duplicate delivery keeps its native semantics.
            owner = candidates[0]
            row = invocation.store.row_uncommitted(invocation.store.root_uncommitted(invocation.task.id)['id'])
            native = deepcopy(invocation._entry(row))
            binding = dict(owner_id=native['id'], queue_generation=native['generation'],
                           claim_identity=native['id'], native=native)
            # A persisted heartbeat's claim can never be silently replaced.
            _check(owner['binding'] is None or same(owner['binding'], binding))
            owner['binding'] = binding
            # queue_finished removes the live queue slot before native close.
            # Keep its actual original preparation for uncertainty capture only.
            if invocation.owner == 'coding':
                original = getattr(invocation, 'coding_queue_binding', None)
                _check(original is None or same(original, binding))
                invocation.coding_queue_binding = deepcopy(binding)

    def heartbeat(self, task_id: str, owner: dict | None) -> None:
        with self.db._lock:
            owners = self._queue_live.get(task_id, {})
            _check(owner is not None and owners.get(id(owner)) is owner)
            with self._writer() as (conn, budget):
                entry = self._entry(conn, task_id, budget=budget)
                kind = ('source' if task_id == entry['source']['task_id'] else 'attempt'
                        if task_id == entry['attempt']['task_id'] else None)
                _check(kind is not None)
                expected_claim = (entry['source']['result_claim']['owner_invocation_id'] if kind == 'source'
                    else entry['native']['claim']['invocation_id'] if entry['native']['claim'] is not None else None)
                binding = owner['binding']
                _check(binding is not None and (expected_claim is None or binding['claim_identity'] == expected_claim))
                anchor, digest, previous = entry['native']['anchors'][task_id]
                before = entry['tasks'][task_id]
                now = stamp()
                after = {**before, 'updated_at': now, 'last_heartbeat': now}
                payload = dict(format='workflow-coding-task-heartbeat@2', instance_id=entry['run']['instance_id'],
                    task_id=task_id, queue_owner=dict(kind=kind, **deepcopy(binding)), session_id=before['current_session_id'],
                    previous_audit_id=previous, anchor_event_id=anchor, anchor_task_digest=digest,
                    before_record=encode_row(before), after_record=encode_row(after), observed_at=now)
                self.db.record_coding_heartbeat_uncommitted(task_id=task_id, before=before, after=after, payload=payload)

    def notify_queued(self, task_id: str, queue: Any) -> None:
        if self.queue_admission_allowed(task_id):
            queue.enqueue_if_absent(self.org.slug, task_id)

    def dispatch(self, task_id: str) -> None:
        require_ready()  # Direct owned routing cannot bypass native queue denial.
        from runtime.orchestrator.task_pause import _invocation, release_live, fingerprint
        with self.db._lock:
            entry = self._entry(self.db._conn, task_id)
            _check(entry['attempt']['task_id'] == task_id and entry['attempt']['state'] == 'queued')
            task = self.db.get_task(task_id)
        with self.db._lock:
            owners = [o for o in self._queue_live.get(task_id, {}).values()
                      if o['invocation'] is not None and o['binding'] is not None]
            _check(len(owners) == 1)
            invocation = owners[0]['invocation']
            _check(invocation is invocation.store._live.get(invocation.identity)
                   and invocation.task.id == task_id and fingerprint(invocation.task) == fingerprint(task))
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

    def _eligible(self, conn: Any, entry: dict, invocation: Any, capture: Any, sessions: tuple,
                  *, callback: bool = False) -> dict:
        from runtime.workflows._coding_settings_execution import _current_configuration, _settings_family
        from runtime.workflows.activation import author_capacity_blocked
        from runtime.workflows.coding_bootstrap import effective_bindings
        _check(not self.org._runner_admission_closed and invocation.orch is self.org.orchestrator
               and invocation.owner == 'coding' and invocation.workflow_dispatcher is self
               and invocation is invocation.store._live.get(invocation.identity))
        self.org.workflow_authority._validate_admission_uncommitted(conn, capture)
        # _entry reconstructed the complete mapping/settings/native closure on
        # this connection before any write. Current authority is independent.
        current = _read_json(capture.ready.snapshot_bytes, entry['_budget'])
        _current_configuration(entry['mapping'], current, entry['command'], entry['_budget'])
        _check(self.org.teams.teams_for_manager(entry['source']['principal']) ==
               (entry['roles']['agent_manager']['team'],))
        if not _settings_family(entry['command']):
            for binding in entry['roles'].values():
                _check(binding['team'] == entry['roles']['agent_manager']['team'])
        _check(not entry['native'].get('ownership_unknown')
               and entry['native']['source_settled'] and entry['run']['state'] == entry['attempt']['stage']
               and entry['run']['current_attempt_id'] == entry['attempt']['id']
               and entry['attempt']['task_id'] == invocation.task.id
               and entry['attempt']['principal'] == invocation.agent)
        for task in entry['tasks'].values():
            self.db._check_team_move_task_uncommitted(task['id'])
            _check(task['cancelled_at'] is None and task['zombie_flagged_at'] is None)
        self.db._check_team_move_workflow_uncommitted(binding_bytes=effective_bindings(entry['run']['instance_id'], entry['roles']))
        if not callback:
            invocation.store.held_uncommitted(invocation.task.id)
        if not callback and author_capacity_blocked(conn, author=invocation.agent, task_id=invocation.task.id, active_sessions=sessions):
            raise CodingStartError('coding_capacity_unavailable')
        from runtime.workflows.coding_revision import independent
        independent(entry['attempts'], entry['roles'])
        _check(1 <= entry['attempt']['revision'] <= min(3, entry['source']['budgets']['coding_revision_limit']) and entry['attempt']['attempt_sequence'] == 1
               and len(entry['attempts']) <= entry['source']['budgets']['coding_attempt_limit']
               and entry['run']['cas_version'] + (1 if callback else 2) <= entry['source']['budgets']['coding_event_limit'])
        return self._admission_observation(conn, entry, invocation, capture, sessions, callback=callback)

    def _admission_observation(self, conn: Any, entry: dict, invocation: Any, capture: Any,
                               sessions: tuple, *, callback: bool) -> dict:
        from runtime.workflows._coding_import_support import _capture
        from runtime.workflows.coding_bootstrap import effective_bindings
        from runtime.workflows._coding_settings_execution import _native_format, _settings_family
        _check(len(sessions) <= 1024)
        budget = entry['_budget']
        root = invocation.store.root_uncommitted(invocation.task.id)
        pause = invocation.store.row_uncommitted(root['id'])
        native = deepcopy(invocation._entry(pause))
        # Bound the complete causal move closure, including retained revisits.
        pending, closure = [invocation.task.id], {}
        while pending:
            key = pending.pop()
            if key in closure:
                continue
            _check(len(closure) < 512)
            found = list(rows(conn, 'tasks', budget, where='id=?', args=(key,), maximum=1))
            _check(len(found) == 1)
            task = found[0]
            closure[key] = task
            pending.extend(r['id'] for r in rows(conn, 'tasks', budget,
                where='parent_task_id=? OR revisit_of_task_id=?', args=(key, key), maximum=512))
            pending.extend(task[k] for k in ('parent_task_id', 'revisit_of_task_id') if task[k] is not None)
        bindings = []
        for row in rows(conn, 'workflow_instances', budget):
            linked = row['root_task_id'] in closure or any(r['task_id'] in closure for r in
                rows(conn, 'workflow_instance_tasks', budget, where='instance_id=?', args=(row['id'],)))
            if linked:
                bound = list(rows(conn, 'workflow_binding_snapshots', budget, where='id=?',
                    args=(row['binding_snapshot_id'],), maximum=1))
                _check(len(bound) == 1)
                bindings.append(dict(instance_id=row['id'], binding=encode_row(bound[0])))
        current, seen = closure[invocation.task.id], set()
        while True:
            _check(current['id'] not in seen and len(seen) < 23
                   and current['team'] == (entry['roles'][{'development': 'implementer',
                       'code_review': 'code_reviewer', 'qa': 'qa_engineer',
                       'manager_closeout': 'agent_manager'}[entry['attempt']['stage']]]['team']
                       if _settings_family(entry['command']) and current['id'] == invocation.task.id
                       else entry['roles']['agent_manager']['team'])
                   and current['cancelled_at'] is None and current['zombie_flagged_at'] is None
                   and current['status'] not in ('completed', 'failed', 'cancelled', 'superseded'))
            seen.add(current['id'])
            if current['parent_task_id'] is None:
                _check(current['id'] == entry['source']['root_ancestor_id'])
                break
            current = closure[current['parent_task_id']]
        capacity = None if callback else dict(sessions=[list(v) for v in sessions],
            tasks=[encode_row(r) for r in rows(conn, 'tasks', budget,
                where="assigned_agent=? AND status='in_progress' AND block_kind IS NULL AND cancelled_at IS NULL AND current_session_id IS NOT NULL AND id<>?",
                args=(invocation.agent, invocation.task.id))],
            work_hours=[encode_row(r) for r in rows(conn, 'work_hours', budget,
                where="agent_name=? AND status='running'", args=(invocation.agent,))])
        if callback:
            claim = entry['native']['claim']
            task = entry['tasks'][invocation.task.id]
            _check(invocation.admitted and invocation.attempt_committed
                   and claim is not None and claim['invocation_id'] == native['id'] == invocation.identity
                   and native['owner'] == 'coding' and native['fingerprint'] == claim['task_fingerprint']
                   and native['generation'] == claim['pause_generation']
                   and native['session_id'] == task['current_session_id'] == invocation.session_id
                   and task['status'] == 'in_progress' and task['block_kind'] is None
                   and task['orchestration_step_count'] == native['fingerprint']['count'] + 1
                   and native['phase'] in ('possible_launch', 'running', 'result_processing')
                   and not native['context'].get('execution_unknown'))
        # Frozen config was checked outside each awaited boundary by _policies.
        # Do not read files while owning this synchronous native writer.
        result = dict(format=_native_format(entry['command'], entry['attempt']['stage']),
            phase='callback' if callback else 'launch',
            authority=_capture(capture, identity('native-admission', entry['attempt']['id'],
                invocation.identity, entry['run']['cas_version']), _canonical(entry['command'])),
            manager_teams=list(self.org.teams.teams_for_manager(entry['source']['principal'])),
            tasks=[encode_row(closure[k]) for k in sorted(closure)], bindings=bindings,
            pause=encode_row({k: v for k, v in pause.items() if k != 'journal'}), invocation=native,
            move=None if (move := invocation.orch._team_moves.active_uncommitted()) is None else encode_row(move),
            capacity=capacity, max_revise_rounds=entry['source']['budgets']['max_revise_rounds'],
            effective_bindings_base64=base64.b64encode(effective_bindings(entry['run']['instance_id'], entry['roles'])).decode('ascii'))
        if _settings_family(entry['command']) and entry['attempt']['stage'] == 'development':
            result.update(settings=entry['command']['settings'], mapping=entry['command']['mapping'])
        pin = result['authority']['pin']
        result['record'] = dict(id=result['authority']['admission_record_id'], namespace=pin['namespace'],
            generation=pin['generation'], request_digest=_hash(_canonical(entry['command'])),
            admitted_by='service:' + self.producer)
        if entry['attempt']['revision'] > 1:
            result.update(format='workflow-coding-revision-native-admission@1',
                attempt_id=entry['attempt']['id'], revision=entry['attempt']['revision'])
        _check(len(_canonical(result)) <= 4194304)
        _check(conn.in_transaction)
        conn.execute('INSERT INTO workflow_admission_records VALUES (?,?,?,?,?)', tuple(result['record'][k]
            for k in ('id', 'namespace', 'generation', 'request_digest', 'admitted_by')))
        return result

    @asynccontextmanager
    async def callback_admission(self, task_id: str):
        with self.db._lock:
            entry = self._entry(self.db._conn, task_id)
        if entry['native']['callback'] is not None:
            # Still-tracked exact transport retries are read-only too. The
            # synchronized DB writer compares all bytes before returning.
            async with self.org.db_lock:
                yield None
            return
        require_ready()
        capture = self.org.workflow_authority.capture_admission()
        self._policies(entry)
        async with self.org.workflow_authority._async_writer_lock:
            self._policies(entry)
            async with self.org.teams_lock:
                self._policies(entry)
                async with self.org.db_lock:
                    self._policies(entry)
                    with self.org.workflow_authority._admission_ownership(capture):
                        # No awaits are permitted in the route's yielded block.
                        yield capture

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
        invocation.coding_launch_admission = self._eligible(conn, entry, invocation, invocation.coding_capture, invocation.coding_sessions)
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
        return dict(run=deepcopy(entry['run']), attempts=[dict(id=k, record=encode_row(a)) for k, a in sorted(entry['attempts'].items())],
            instances=_json_start(entry)['after']['instances'],
            tasks=[dict(id=k, record=encode_row(v)) for k, v in sorted(entry['tasks'].items())],
            rounds=[], requests=[], assignments=[])

    def _final_workspace(self, invocation: Any, entry: dict) -> dict:
        """Capture only existing native OrgPaths metadata; no filesystem or launcher effect."""
        from runtime.workflows.coding_start_records import CodingFinalWorkspace
        paths = invocation.orch._paths
        _check(invocation.orch is self.org.orchestrator and paths.root == self.org.root
               and invocation.owner == 'coding' and invocation.workflow_dispatcher is self
               and invocation.task.id == entry['attempt']['task_id']
               and invocation.agent == entry['attempt']['principal'])
        return CodingFinalWorkspace.model_validate(dict(format='workflow-coding-final-workspace@1',
            org_slug=self.org.slug, org_root=str(paths.root),
            workspace_root=str(paths.workspaces_dir / invocation.agent), principal=invocation.agent,
            task_id=invocation.task.id, attempt_id=entry['attempt']['id'],
            invocation_id=invocation.identity, session_id=invocation.session_id)).model_dump()

    def _append(self, conn: Any, entry: dict, kind: str, facts: dict, *, invocation: Any,
                run_changes: dict | None = None, attempt_changes: dict | None = None,
                native_rows: list | None = None, event_time: str | None = None) -> None:
        from runtime.workflows.coding_native_history import KINDS
        _check(conn.in_transaction and kind in KINDS)
        previous = entry['native']['history'][-1]
        _check(previous['event_seq'] < entry['source']['budgets']['coding_event_limit'])
        source_tail = invocation.task.id == entry['source']['task_id']
        repaired = (entry['attempt']['stage'] == 'manager_closeout' and
                    json.loads(entry['attempt']['scope_bytes'])['format'] in ('workflow-coding-final-scope@2', 'workflow-coding-final-scope@3'))
        uncertainty = ((repaired or entry['attempt']['revision'] > 1) and not source_tail and kind == 'held' and facts.get('reason') in
                       ('coding_native_preparation_unsettled', 'coding_native_close_unavailable'))
        prebinding = uncertainty and facts['reason'] == 'coding_native_preparation_unsettled'
        claim = None if source_tail else getattr(invocation, 'coding_claim', entry['native']['claim'])
        if prebinding:
            # A rolled-back session admission may leave transient Python facts;
            # those are not a committed coding claim or workspace.
            _check(entry['native']['claim'] is entry['native']['host'] is entry['native'].get('workspace') is None)
            claim = None
        producer = (invocation.identity if source_tail else claim['owner'] if claim is not None
                    else self.producer + ':' + invocation.identity)
        row = invocation.store.row_uncommitted(entry['source']['root_ancestor_id'])
        matches = [] if row is None else [r for r in row['journal']['entries'] if r['id'] == invocation.identity]
        native = deepcopy(matches[0]) if len(matches) == 1 else deepcopy(getattr(invocation, 'coding_settled_entry', None))
        with self.db._lock:
            candidates = [owner for owner in self._queue_live.get(invocation.task.id, {}).values()
                          if owner['binding'] is not None and owner['binding']['claim_identity'] == invocation.identity]
            queue_owner = deepcopy(candidates[0]['binding']) if len(candidates) == 1 else None
        if uncertainty:
            _check(invocation.owner == 'coding' and invocation.workflow_dispatcher is self
                   and invocation.orch is self.org.orchestrator
                   and invocation.task.id == entry['attempt']['task_id']
                   and invocation.agent == entry['attempt']['principal'])
            if queue_owner is None:
                queue_owner = deepcopy(getattr(invocation, 'coding_queue_binding', None))
            if prebinding:
                _check(queue_owner is not None and queue_owner['claim_identity'] == invocation.identity
                       and queue_owner['native']['session_id'] == invocation.session_id)
            else:
                # A retained journal entry is not the unavailable current close.
                native = None
        observation = dict(facts=facts, invocation=native, queue_owner=queue_owner,
            admission=(invocation.coding_launch_admission if kind == 'session_bound' else
                       invocation.coding_callback_admission if kind == 'callback_recorded' else None))
        if repaired:
            workspace = self._final_workspace(invocation, entry) if kind == 'session_bound' else entry['native'].get('workspace')
            _check(prebinding or workspace is not None)
            observation['workspace'] = workspace
        manager_version = '3' if uncertainty else '2' if repaired else '1'
        command = dict(format=(('workflow-coding-revision-native-command@2' if uncertainty else 'workflow-coding-revision-native-command@1') if entry['attempt']['revision'] > 1 else {'development': 'workflow-coding-internal-command@2',
            'code_review': 'workflow-coding-native-review-command@1', 'qa': 'workflow-coding-native-qa-command@1', 'manager_closeout': 'workflow-coding-native-manager-command@' + manager_version}[entry['attempt']['stage']]), kind=kind,
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
        after['attempts'] = [dict(id=k, record=encode_row(attempt if k == attempt['id'] else a))
                             for k, a in sorted(entry['attempts'].items())]
        after['tasks'] = [dict(id=k, record=encode_row(dict(conn.execute('SELECT * FROM tasks WHERE id=?', (k,)).fetchone())))
                          for k in sorted(entry['tasks'])]
        operation = dict(org_slug=self.org.slug, principal=principal, key=key, request_digest=_hash(_canonical(command)))
        body = dict(format=(('workflow-coding-revision-native-event@2' if uncertainty else 'workflow-coding-revision-native-event@1') if entry['attempt']['revision'] > 1 else {'development': 'workflow-coding-event@2',
            'code_review': 'workflow-coding-native-review-event@1', 'qa': 'workflow-coding-native-qa-event@1', 'manager_closeout': 'workflow-coding-native-manager-event@' + manager_version}[entry['attempt']['stage']]),
            id=event_id, instance_id=instance,
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
        audits = list(rows(conn, 'audit_log', entry['_budget'], where="task_id=? AND action='session_start'", args=(invocation.task.id,)))
        audits = [r for r in audits if json.loads(r['payload']).get('session_id') == invocation.session_id]
        _check(len(audits) == 1)
        now = stamp()
        conn.execute('UPDATE tasks SET updated_at=? WHERE id=?', (now, invocation.task.id))
        self._append(conn, entry, 'session_bound', dict(claim=invocation.coding_claim,
            session_start_audit_id=audits[0]['id'], session_purpose='worker_execution', session_id=invocation.session_id),
            invocation=invocation, attempt_changes=dict(session_id=invocation.session_id, state='claimed'),
            native_rows=[dict(table='audit_log', id=audits[0]['id'], record=encode_row(audits[0]))], event_time=now)
        # Both events belong to this SAME native admission transaction. Do
        # not expose a separately committed session without its reservation.
        entry = deepcopy(entry)
        entry['run'] = dict(conn.execute('SELECT * FROM workflow_coding_runs WHERE instance_id=?', (entry['run']['instance_id'],)).fetchone())
        entry['attempt'] = dict(conn.execute('SELECT * FROM workflow_coding_attempts WHERE id=?', (entry['attempt']['id'],)).fetchone())
        entry['tasks'] = {k: dict(conn.execute('SELECT * FROM tasks WHERE id=?', (k,)).fetchone()) for k in entry['tasks']}
        entry['attempts'][entry['attempt']['id']] = entry['attempt']
        event = dict(conn.execute('SELECT * FROM workflow_coding_events WHERE id=?', (entry['run']['last_event_id'],)).fetchone())
        entry['native'].update(claim=invocation.coding_claim, history=[*entry['native']['history'], event], pending_observations=[])
        if entry['attempt']['stage'] == 'manager_closeout' and json.loads(entry['attempt']['scope_bytes'])['format'] in ('workflow-coding-final-scope@2', 'workflow-coding-final-scope@3'):
            entry['native']['workspace'] = self._final_workspace(invocation, entry)
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
        with self._writer() as (conn, budget):
            entry = self._entry(conn, task_id, budget=budget)
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
        with self._writer() as (conn, budget):
            entry = self._entry(conn, task_id, budget=budget)
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

    def callback_uncommitted(self, *, task_id: str, agent: str, session_id: str, payload: dict, capture: Any) -> bool:
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
        require_ready()
        invocation.coding_callback_admission = self._eligible(conn, entry, invocation, capture, (), callback=True)
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
            with self._writer() as (conn, budget), _capture_root_worktree_transition(
                    self.org.orchestrator, task_id, 'coding_dispatch.terminal', transitions):
                entry = self._entry(conn, task_id, budget=budget)
                host = entry['native']['host']
                _check(host is not None and outcome is not None and outcome.request is expected_request
                       and expected_request is invocation.coding_request and outcome.attempt == 0)
                receipt = None if outcome.receipt is None else _native_value(asdict(outcome.receipt))
                _check(receipt is None or receipt['terminal_reason'] == outcome.terminal_reason.value)
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

    def outer_settled(self, invocation: Any, *, failed: bool, cleanup: Any, native_entry: dict | None,
                      close_disposition: dict | None, release_disposition: dict | None) -> None:
        # Called by the native settle_live finally owner after tracker, task tail,
        # root worktree batch and native close/release. No absence is a witness.
        with self.db._lock:
            if self.db._conn.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='workflow_coding_runs'").fetchone() is None:
                return
            if self.db._conn.execute('SELECT 1 FROM workflow_coding_runs LIMIT 1').fetchone() is None:
                return
        with self._writer() as (conn, budget):
            saved = queued_storage(conn, self.org.slug, budget=budget)
            ownership = saved.owners.get(invocation.task.id)
            if ownership is None:
                return
            entry = self._selected_entry(saved, invocation.task.id, budget)
            if ownership[0] == 'coding_carrier':
                raise CodingStartError('coding_structural_launch_refused')
            source = ownership[0] == 'coding_source_waiting'
            if not source and (entry['native']['claim'] is None or entry['native']['host'] is None):
                # Unclosed preparation is not affirmative no-launch proof.
                # Hold this exact native owner; never invent a host/result or
                # consume a retry to recover an unrecorded reservation.
                producer = self.producer + ':' + invocation.identity
                invocation.coding_settled_entry = native_entry
                self._append(conn, entry, 'held', dict(reason='coding_native_preparation_unsettled',
                    owner_event_ids=[entry['native']['history'][-1]['id']], resume_state=entry['attempt']['stage'],
                    required_owner=producer, required_action='original_owner_reconciliation'),
                    invocation=invocation, run_changes=dict(state='held'))
                return
            if native_entry is None:
                invocation.coding_settled_entry = None
                self._append(conn, entry, 'held', dict(reason='coding_native_close_unavailable',
                    owner_event_ids=[entry['native']['history'][-1]['id']], resume_state=entry['attempt']['stage'],
                    required_owner=invocation.identity if source else entry['native']['claim']['owner'],
                    required_action='original_owner_reconciliation'), invocation=invocation,
                    run_changes=dict(state='held'))
                return
            _check(native_entry['id'] == invocation.identity and native_entry['task_id'] == invocation.task.id
                   and native_entry['agent'] == invocation.agent and native_entry['session_id'] == invocation.session_id
                   and native_entry['owner'] == invocation.owner)
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
            disposition = dict(kind='safe' if safe else 'unknown', reason='native_outer_settlement',
                processes=[] if cleanup is None else list(cleanup.processes), invocation=native_entry,
                terminal=getattr(invocation, 'native_terminal_observation', None), tracker_clear=tracker_clear,
                close=close_disposition, release=release_disposition,
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
            changes = {} if safe else dict(state='held')
            if not source and safe and entry['native']['terminal'] == 'success':
                from runtime.workflows.coding_revision import fulfilled_revision
                if fulfilled_revision(entry['attempt'], entry['native']['callback'], entry['command']['work'], budget):
                    changes['current_revision'] = entry['attempt']['revision']
            self._append(conn, entry, 'cleanup_settled', facts, invocation=invocation,
                run_changes=changes,
                attempt_changes=(dict(state='completed') if not source and safe and entry['native']['terminal'] == 'success'
                                 else dict(state='uncertain') if not source and not safe else {}),
                event_time=now)
            if not source and safe and entry['native']['terminal'] == 'success':
                entry = self._entry(conn, invocation.task.id, budget=budget)
                from runtime.workflows.coding_progression import _settled, record_final_in_writer, record_qa_in_writer, record_review_in_writer
                try:
                    usable = _settled(entry, budget=entry['_budget'])
                except (ValueError, TypeError, UnicodeError):
                    usable = None
                if usable is None:
                    self._append(conn, entry, 'held', dict(reason='coding_stage_output_unusable',
                        owner_event_ids=[entry['native']['history'][-1]['id']], resume_state=entry['attempt']['stage'],
                        required_owner=entry['native']['claim']['owner'], required_action='original_result_inspection'),
                        invocation=invocation, run_changes=dict(state='held'))
                elif entry['attempt']['stage'] == 'code_review':
                    record_review_in_writer(self, conn, entry)
                elif entry['attempt']['stage'] == 'qa':
                    record_qa_in_writer(self, conn, entry)
                elif entry['attempt']['stage'] == 'manager_closeout':
                    record_final_in_writer(self, conn, entry)
        # Native close/move release was recorded and committed before any new
        # current-authority allocation, with no await or enqueue in that writer.
        from runtime.workflows.coding_progression import reconcile_run
        reconcile_run(self, entry['run']['instance_id'], self.org.orchestrator._queue)

    def _selected_final_owner(self, conn: Any, principal: Any, instance: str) -> Any:
        """Own original running coding leaf BEFORE any private history decoding.

        Do not reuse or weaken the ordinary/v2 source-manager native predicate.
        This domain read creates no owner, session, scope, job or permission.
        """
        principal.revalidate()
        org = self.org
        _check(principal.org_slug == org.slug and principal.principal_kind == 'agent'
               and principal.proof_kind == 'task_session' and principal.principal_id != 'founder'
               and org.teams.teams_for_manager(principal.principal_id) == (principal.team_slug,)
               and org.sessions.get_context_by_session(principal.session_id) ==
                   (org.slug, principal.task_id, principal.principal_id)
               and org.sessions.get_active(principal.task_id, principal.principal_id) == principal.session_id
               and not org.sessions.is_recovery_session(principal.task_id, principal.principal_id, principal.session_id)
               and not org._runner_admission_closed and org.orchestrator._db is self.db)
        row = conn.execute('SELECT a.id,a.state,a.principal,a.session_id,t.team,t.status,t.task_type,'
            't.current_session_id,t.block_kind,t.cancelled_at,t.zombie_flagged_at,t.active_chain,t.active_fanout,t.blocked_on_job_ids '
            'FROM workflow_coding_attempts a JOIN workflow_coding_runs r ON r.instance_id=a.instance_id '
            'JOIN tasks t ON t.id=a.task_id WHERE a.instance_id=? AND a.task_id=? '
            "AND a.stage='manager_closeout' AND r.state='manager_closeout' AND r.current_attempt_id=a.id",
            (instance, principal.task_id)).fetchall()
        _check(len(row) == 1)
        a = row[0]
        _check(a[1] == 'running' and a[2] == principal.principal_id and a[3] == principal.session_id
               and a[4] == principal.team_slug and a[5] == 'in_progress' and a[6] == 'subtask'
               and a[7] == principal.session_id and all(v is None for v in a[8:]))
        _check(conn.execute('SELECT 1 FROM task_results WHERE task_id=? LIMIT 1', (principal.task_id,)).fetchone() is None)
        store = self.db._task_pause_store
        root, pause = store.held_uncommitted(principal.task_id)
        owners = [v for v in store._live.values() if (v.task.id, v.agent, v.session_id) ==
                  (principal.task_id, principal.principal_id, principal.session_id)]
        _check(len(owners) == 1 and pause is not None and not pause['held'])
        owner = owners[0]
        _check(owner.owner == 'coding' and owner.workflow_dispatcher is self and owner.orch is org.orchestrator
               and owner.admitted and owner.attempt_committed and owner.unsettled)
        entries = [v for v in pause['journal']['entries'] if v['id'] == owner.identity]
        _check(len(entries) == 1)
        native = entries[0]
        _check(native['owner'] == 'coding' and native['org'] == org.slug and native['root_task_id'] == root['id']
               and (native['task_id'], native['agent'], native['session_id']) ==
                   (principal.task_id, principal.principal_id, principal.session_id)
               and native['phase'] == 'running' and native['generation'] == pause['generation']
               and not native['context'].get('execution_unknown') and not native['context'].get('producer_settled'))
        self.db._check_team_move_task_uncommitted(principal.task_id)
        return owner

    async def selected_final(self, *, principal: Any, instance: str) -> dict:
        """Selected metadata only; no GitHub/file I/O in ownership/read snapshots."""
        from runtime.orchestrator import prompt_loader
        from runtime.workflows._coding_settings_execution import _current_configuration
        from runtime.workflows.coding_bootstrap import effective_bindings
        from runtime.workflows.coding_progression import final_selection
        org = self.org
        principal.revalidate()
        policy = prompt_loader.load_agent(org.orchestrator._paths, principal.principal_id)
        _check(policy is not None and policy.role == 'manager' and policy.executor and policy.team == principal.team_slug)
        support = org.workflow_coding_start.support
        references = support._schema_references()
        capture = org.workflow_authority.capture_admission()
        async with org.workflow_authority._async_writer_lock:
            principal.revalidate()
            async with org.teams_lock:
                principal.revalidate()
                async with org.db_lock:
                    with org.sessions._get_binding_lease(principal.task_id, principal.principal_id):
                        with support._read(capture) as (conn, current):
                            self._selected_final_owner(conn, principal, instance)
                            _check(current is not None)
                            org.workflow_authority._validate_admission_uncommitted(conn, current)
                            inventory = support._storage(conn, references)
                            entry = inventory.queued.runs[instance]
                            owner = self._selected_final_owner(conn, principal, instance)
                            _check(entry['attempt']['task_id'] == principal.task_id
                                   and entry['attempt']['principal'] == principal.principal_id
                                   and entry['roles']['agent_manager'] ==
                                       dict(principal=principal.principal_id, team=principal.team_slug)
                                   and not entry['native'].get('ownership_unknown')
                                   and entry['native']['claim'] is not None and entry['native']['host'] is not None
                                   and entry['native']['claim']['invocation_id'] == owner.identity
                                   and entry['native']['host']['invocation_id'] == owner.identity
                                   and entry['native']['host']['handle_id'] is not None
                                   and entry['native']['terminal'] is None and entry['native']['callback'] is None)
                            snapshot = _read_json(current.ready.snapshot_bytes, inventory.budget)
                            _current_configuration(entry['mapping'], snapshot, entry['command'], inventory.budget)
                            for task in entry['tasks']:
                                self.db._check_team_move_task_uncommitted(task)
                            self.db._check_team_move_workflow_uncommitted(binding_bytes=effective_bindings(instance, entry['roles']))
                            _check(entry['native'].get('workspace') == self._final_workspace(owner, entry))
                            result = final_selection(entry, budget=inventory.budget)
                            _check(result['format'] in ('workflow-coding-final-selection@2', 'workflow-coding-final-selection@3'))
                            _check(len(_canonical(result)) <= 262144)
                            # Current live authorization is not an external-effect lease.
                            org.workflow_authority._validate_admission_uncommitted(conn, current)
                            owner = self._selected_final_owner(conn, principal, instance)
                            _check(result['workspace'] == self._final_workspace(owner, entry))
                            return result

    def final_instruction(self, entry: dict, task_id: str, agent: str, session_id: str) -> str:
        from runtime.workflows.coding_bootstrap import final_scope
        from runtime.workflows.coding_progression import settled_upstreams
        from runtime.workflows.coding_start_records import selected_repository
        scope = json.loads(entry['attempt']['scope_bytes'])
        pins, _ = settled_upstreams(entry, budget=entry['_budget'])
        if entry['attempt']['revision'] > 1:
            from runtime.workflows.coding_revision import revision_scope
            expected_scope = revision_scope(org=self.org.slug, instance=entry['run']['instance_id'],
                command=entry['command'], roles=entry['roles'], attempt=entry['attempt'], attempts=entry['attempts'],
                subject=scope['subject'], returns=[], upstream=pins,
                repository_reference=selected_repository(entry['mapping'], entry['command']['work']))
        else:
            qa = next(a for a in entry['attempts'].values() if a['stage'] == 'qa')
            expected_scope = final_scope(org=self.org.slug, instance=entry['run']['instance_id'],
                command=entry['command'], roles=entry['roles'], qa=qa, subject=scope['subject'], task_id=task_id, upstream=pins,
                repository_reference=selected_repository(entry['mapping'], entry['command']['work']))
        _check(scope == expected_scope)
        workspace = entry['native'].get('workspace')
        _check(workspace is not None and (workspace['task_id'], workspace['principal'], workspace['session_id']) ==
               (task_id, agent, session_id) and workspace['invocation_id'] == entry['native']['claim']['invocation_id'])
        command = dict(format='workflow-coding-finalize-command@2' if entry['attempt']['revision'] > 1 else 'workflow-coding-finalize-command@1', operation_key=identity('finalize', entry['attempt']['id']), repository_path='ABSOLUTE_EXISTING_AUTHORIZED_WORKTREE',
            instance_id=entry['run']['instance_id'], attempt_id=entry['attempt']['id'], revision=entry['attempt']['revision'],
            task_id=task_id, session_id=session_id, principal=agent, subject=scope['subject'], upstream_results=pins)
        supplement = dict(format='workflow-coding-final-manager@3' if entry['attempt']['revision'] > 1 else 'workflow-coding-final-manager@2', role='agent_manager', scope=scope,
            task_id=task_id, session_id=session_id, attempt_id=entry['attempt']['id'], command=command,
            workspace=entry['native']['workspace'], repository_reference=scope['repository_reference'],
            tool='happyranch workflows coding finalize --org ' + self.org.slug + ' --session-id CURRENT --from-file ABS --json',
            obligation='Use only existing authorized tools in your actual workspace. A descriptor grants no access. '
                'The LOCAL command selects authentic own upstream results, observes both literal checks once, persists intent, '
                'revalidates current ownership and calls the expected-head non-admin guard once. Keep original file/key and receipts; '
                'never replay an incomplete intent or remerge after transport loss. Pending checks remain held, no job waits/decisions. '
                'Preserve its canonical final-output@2 JSON STRING in your own summary, null verdict, no decision/jobs. '
                'Save receipts before report-completion through this genuine task/agent/SID as FINAL action. '
                'Callback alone cannot complete; successful host/quiescence and positive native outer closure are required.')
        if entry['attempt']['revision'] > 1:
            supplement['obligation'] = supplement['obligation'].replace('final-output@2', 'final-output@3')
        if entry['command']['format'] == 'workflow-coding-start@2':
            supplement['settings'] = entry['command']['settings']
        return '\n\n' + _canonical(supplement).decode('utf8')


    def development_instruction(self, task_id: str, agent: str, session_id: str) -> str:
        with self.db._lock:
            entry = self._entry(self.db._conn, task_id)
            attempt = entry['attempt']
            _check(task_id == attempt['task_id'] and agent == attempt['principal']
                   and entry['run']['current_attempt_id'] == attempt['id']
                   and attempt['state'] in ('queued', 'claimed', 'running')
                   and attempt['session_id'] in (None, session_id))
            from runtime.workflows.coding_bootstrap import qa_scope, review_scope
            if attempt['stage'] == 'manager_closeout':
                return self.final_instruction(entry, task_id, agent, session_id)
            if attempt['revision'] > 1:
                return self.revision_instruction(entry, task_id, agent, session_id)
            development = attempt['stage'] == 'development'
            qa = attempt['stage'] == 'qa'
            scope = json.loads(attempt['scope_bytes'])
            supplement = dict(format={'development': 'workflow-coding-development@1',
                'code_review': 'workflow-coding-review@1', 'qa': 'workflow-coding-qa@1'}[attempt['stage']],
                instance_id=entry['run']['instance_id'], attempt_id=attempt['id'], task_id=task_id,
                session_id=session_id, role='implementer' if development else 'qa_engineer' if qa else 'code_reviewer',
                revision=attempt['revision'], assignment_generation=attempt['assignment_generation'],
                scope_digest=attempt['scope_digest'], release=entry['command']['release'], mapping=entry['command']['mapping'],
                work=entry['command']['work'], head=None if development else scope['subject']['head'],
                reading='Use existing authorized repository tools. A reference grants no access or execution authority.',
                output=dict(format='workflow-coding-stage-output@1', instance_id=entry['run']['instance_id'],
                    attempt_id=attempt['id'], revision=1, stage=attempt['stage'],
                    subject=(dict(format='workflow-code-change@1', kind='code-change',
                        repository=dict(id=entry['command']['work']['repository_id']),
                        object_format=entry['command']['work']['object_format'], head=None,
                        comparison=dict(base=entry['command']['work']['base']), navigation=dict(branch=None, pr_url=None))
                        if development else scope['subject']),
                    findings='Your own nonempty findings, at most 16384 UTF8 bytes.',
                    outcome='produced' if development else 'PASS|REVISE|BLOCK' if qa else 'APPROVE|REQUEST_CHANGES|BLOCK'),
                output_obligation=('Introduce only the full commit genuinely produced by this assigned task.' if development else
                    'Independently inspect the exact pinned produced and approved subject; use BLOCK for unavailable access or proof. '
                    'Write your own QA findings; never copy another principal findings.' if qa else
                    'Independently inspect the exact pinned subject; use BLOCK for unavailable access. Never copy another actor approval.'),
                final_callback=dict(task_id=task_id, agent=agent, session_id=session_id,
                    command='happyranch report-completion --org ' + self.org.slug + ' --from-file ABS',
                    obligation=('summary is the canonical UTF8 JSON STRING of output, never an object; completed status with null verdict for development or exact outcome verdict for review. Failed/blocked callbacks retain actual causes without inventing output. Callback is FINAL. No staging command.'
                        if not qa else 'summary is the canonical UTF8 JSON STRING of output; completed status and verdict=PASS|REVISE|BLOCK matching outcome. '
                        'Use only this real task/agent/SID and own findings. Failed/blocked callbacks retain actual causes. Callback is FINAL. No staging or coding decision/job wait.')))
            if entry['command']['format'] == 'workflow-coding-start@2':
                supplement.update(settings=entry['command']['settings'], scope=scope)
            if not development:
                developer = next(a for a in entry['attempts'].values() if a['stage'] == 'development')
                if qa:
                    reviewer = next(a for a in entry['attempts'].values() if a['stage'] == 'code_review')
                    _check(scope == qa_scope(org=self.org.slug, instance=entry['run']['instance_id'],
                        command=entry['command'], roles=entry['roles'], developer=developer,
                        reviewer=reviewer, subject=scope['subject'], task_id=task_id))
                    supplement['reviewer'] = scope['reviewer']
                    supplement['team'] = scope['team']
                    supplement['scope'] = scope
                else:
                    _check(scope == review_scope(org=self.org.slug, instance=entry['run']['instance_id'],
                        command=entry['command'], roles=entry['roles'], developer=developer, subject=scope['subject']))
                supplement['developer'] = scope['developer']
                supplement['subject'] = scope['subject']
        return '\n\n' + _canonical(supplement).decode('utf8')

    def revision_instruction(self, entry: dict, task_id: str, agent: str, session_id: str) -> str:
        from runtime.workflows.coding_revision import ROLES, returned_findings, revision_scope
        from runtime.workflows.coding_revision_history import upstream_pins
        from runtime.workflows.coding_start_records import selected_repository
        a, budget = entry['attempt'], entry['_budget']
        scope = _read_json(a['scope_bytes'], budget, 1048576)
        prior = {k: v for k, v in entry['attempts'].items() if k != a['id']}
        development = a['stage'] == 'development'
        subject = scope['previous_subject'] if development else scope['subject']
        returns = returned_findings(prior, entry['native']['stages'], entry['run']['instance_id'], entry['command']['work'], budget) if development else []
        upstream = None if development else upstream_pins(prior, entry['native']['stages'], a['revision'],
            entry['run']['instance_id'], entry['command']['work'], subject, budget,
            through='development' if a['stage'] == 'code_review' else 'code_review')
        _check(scope == revision_scope(org=self.org.slug, instance=entry['run']['instance_id'], command=entry['command'],
            roles=entry['roles'], attempt=a, attempts=prior, subject=subject, returns=returns, upstream=upstream,
            repository_reference=selected_repository(entry['mapping'], entry['command']['work'])))
        output_subject = deepcopy(subject)
        if development:
            output_subject['head'] = None
        supplement = dict(format={'development': 'workflow-coding-development@2', 'code_review': 'workflow-coding-review@2',
            'qa': 'workflow-coding-qa@2'}[a['stage']], instance_id=entry['run']['instance_id'], attempt_id=a['id'],
            task_id=task_id, session_id=session_id, role=ROLES[a['stage']], revision=a['revision'],
            attempt_sequence=a['attempt_sequence'], assignment_generation=a['assignment_generation'],
            scope_digest=a['scope_digest'], scope=scope, release=entry['command']['release'], mapping=entry['command']['mapping'],
            work=entry['command']['work'], head=None if development else subject['head'],
            output=dict(format='workflow-coding-stage-output@2', instance_id=entry['run']['instance_id'], attempt_id=a['id'],
                revision=a['revision'], stage=a['stage'], subject=output_subject, findings='Your own nonblank findings, at most16384 UTF8 bytes.',
                outcome='produced' if development else 'APPROVE|REQUEST_CHANGES|BLOCK' if a['stage'] == 'code_review' else 'PASS|REVISE|BLOCK'),
            obligation=('Address ALL original returned_findings with a genuinely changed head on the SAME admitted base/paths. '
                'Previous approvals do not apply.' if development else 'Independently assess this EXACT revision and subject using existing authorized tools. '
                'Write your own findings; never copy or relabel previous approvals. Unavailable access/proof requires BLOCK.'),
            final_callback=dict(task_id=task_id, agent=agent, session_id=session_id,
                command='happyranch report-completion --org ' + self.org.slug + ' --from-file ABS',
                obligation='Summary is canonical UTF8 JSON STRING of output, completed status; null verdict for development, exact outcome for review/QA. '
                    'Only this genuine task/SID/agent. Callback is FINAL. No staging, decision, or job wait. '
                    'Failed/blocked outcomes retain their true cause. References grant no access or execution authority.'))
        if entry['command']['format'] == 'workflow-coding-start@2':
            supplement['settings'] = entry['command']['settings']
        return '\n\n' + _canonical(supplement).decode('utf8')


def _json_start(entry: dict) -> dict:
    return json.loads(entry['event']['event_bytes'])

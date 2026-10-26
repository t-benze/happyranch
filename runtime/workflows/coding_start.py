"""Genuine result-owned K3a bootstrap. No pre-callback allocation or launcher."""
from __future__ import annotations

import sqlite3
from typing import Any
from dataclasses import asdict
from datetime import datetime, timezone

from runtime.models import BlockKind, CompletionReport, NextStep, TaskRecord, TaskStatus
from runtime.infrastructure.db.tasks import RetryClaim
from runtime.workflows.authority import AuthorityAdmissionCapture
from runtime.orchestrator import prompt_loader
from runtime.orchestrator.org_config import load_org_config
from runtime.orchestrator.task_pause import current_invocation
from runtime.workflows._coding_import_support import CodingImportSupport, _capture
from runtime.workflows._coding_release_history import read_release_import
from runtime.workflows._coding_settings_execution import _configuration, _read_json, _resolve_start
from runtime.workflows.activation import author_capacity_blocked
from runtime.workflows.coding_authority_history import _Budget
from runtime.workflows.coding_bootstrap import effective_bindings, encode_row, identity, initial_scope, journal_rows, pinned_rows, task_brief
from runtime.workflows.coding_saved_records import _canonical, _check, _hash
from runtime.workflows.coding_source_history import _session_start
from runtime.workflows.coding_start_history import claim_for, rows, same
from runtime.workflows.coding_start_records import CodingRunReceipt, effective_mapping, parse_decision, parse_native_decision


class CodingStartError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def readiness() -> dict:
    # Source capability, not org admission, deployment or scratch acceptance.
    return dict(enabled=True, reason='coding_engine_available')


def require_ready(org: Any) -> None:
    """Require the real mounted native runtime, never a caller readiness flag."""
    orch = org.orchestrator
    dispatcher = getattr(org, 'workflow_coding_dispatch', None)
    if (dispatcher is None or getattr(dispatcher, 'db', None) is not org.db
            or getattr(orch, '_workflow_coding_dispatch', None) is not dispatcher
            or getattr(orch, '_workflow_coding_start', None) is not org.workflow_coding_start
            or orch._db is not org.db or getattr(orch, '_host_supervisor', None) is None
            or getattr(orch, '_queue', None) is None or getattr(orch, '_main_loop', None) is None
            or not orch._main_loop.is_running()):
        raise CodingStartError('coding_downstream_unavailable')


def require_start_ready(conn: sqlite3.Connection, org: Any, command: dict) -> None:
    # Fresh starts alone require current explicit settings. Admitted callbacks
    # and continuations keep their original pins when new admission is disabled.
    require_ready(org)
    from runtime.workflows._coding_settings_execution import _settings_family
    from runtime.infrastructure.workflow_schema import _read_workflow_schema
    owned = not conn.in_transaction
    if owned:
        conn.execute('BEGIN')
    try:
        _check(_settings_family(command))
        budget = _Budget()
        layout, inventory = _read_workflow_schema(conn, expected_org_slug=org.slug, budget=budget)
        _check(inventory is not None)
        _resolve_start(command, inventory.mappings, inventory.imported, budget, fresh=True)
    except (ValueError, TypeError, KeyError, sqlite3.Error, RecursionError, OverflowError):
        raise CodingStartError('coding_start_settings_unavailable') from None
    finally:
        if owned:
            conn.rollback()


class CodingStartStore:
    def __init__(self, org: Any) -> None:
        self.org, self.db = org, org.db
        self.support = CodingImportSupport(org)

    def _native(self, conn: sqlite3.Connection, claim: RetryClaim, invocation: Any, *,
                budget: _Budget | None = None) -> tuple:
        _check(not self.org._runner_admission_closed and self.org.orchestrator._db is self.db)
        _check(type(claim.result_row_id) is int and claim.result_row_id > 0
               and self.db._retry_claim_matches(claim))
        _check(invocation is not None and invocation.orch is self.org.orchestrator
               and invocation is self.db._task_pause_store._live.get(invocation.identity)
               and invocation.owner in ('ordinary', 'v2') and invocation.admitted
               and invocation.attempt_committed and invocation.unsettled
               and (invocation.task.id, invocation.agent, invocation.session_id) ==
                   (claim.task_id, claim.assigned_agent, claim.current_session_id))
        if self.db.get_active_authority_continue_envelope(claim.task_id) is not None:
            # The historical v1 consumer owns a self-committing spend, so it
            # cannot be borrowed by this atomic graph writer. Preserve its
            # owner; an unavailable native composition never becomes a bypass.
            raise CodingStartError('coding_start_native_continuation_unavailable')
        policy = self.db.authority_policy_v2_completion_dispatch_context(
            root_task_id=claim.task_id, result_row_id=claim.result_row_id)
        _check(policy.kind in ('no_v2', 'later', 'reserved')
               or (policy.kind == 'receipt' and policy.decision_state == 'ready'))
        _check(budget is None or type(budget) is _Budget)
        budget = _Budget() if budget is None else budget
        selected = list(rows(conn, 'tasks', budget, where='id=?', args=(claim.task_id,), maximum=1))
        _check(len(selected) == 1)
        source = selected[0]
        _check(source['zombie_flagged_at'] is None and source['completed_at'] is None)
        _check(source['task_type'] == 'task' and claim.active_chain is None and claim.active_fanout is None
               and self.org.teams.teams_for_manager(claim.assigned_agent) == (claim.team,))
        ancestors, seen = [], {claim.task_id}
        current = source
        while current['parent_task_id'] is not None:
            _check(len(ancestors) < 19 and current['parent_task_id'] not in seen)
            selected = list(rows(conn, 'tasks', budget, where='id=?', args=(current['parent_task_id'],), maximum=1))
            _check(len(selected) == 1)
            current = selected[0]
            _check(current['team'] == claim.team and current['cancelled_at'] is None
                   and current['status'] not in ('completed', 'failed', 'cancelled', 'superseded'))
            seen.add(current['id'])
            ancestors.append(current)
        root, pause = self.db._task_pause_store.held_uncommitted(claim.task_id)
        _check(pause is not None and not pause['held'] and root['id'] == current['id'])
        entry = invocation._entry(pause)
        _check(entry['phase'] == 'running' and entry['generation'] == pause['generation']
               and not entry['context'].get('execution_unknown')
               and entry['fingerprint']['count'] + 1 == claim.step
               and entry['fingerprint']['brief_digest'] == _hash(source['brief'].encode('utf8')))
        for row in [source, *ancestors]:
            self.db._check_team_move_task_uncommitted(row['id'])
        result_rows = list(rows(conn, 'task_results', budget,
            where='task_id=? AND session_id=?', args=(claim.task_id, claim.current_session_id)))
        _check(len(result_rows) == 1)
        result = result_rows[0]
        _check(type(result['id']) is int and result['id'] == claim.result_row_id
               and result['agent'] == claim.assigned_agent and result['status'] == 'completed'
               and result['waiting_on_job_ids'] is None)
        decision = parse_native_decision(result['decision_json'].encode('utf8'))
        start = _session_start(conn, dict(task_id=claim.task_id, session_id=claim.current_session_id,
                                        principal=claim.assigned_agent), budget)
        start_row = {c.name: c.value.decode('utf8') if c.storage_class == 'text' else c.value for c in start.columns}
        _check(claim_for(source, result['id']) == asdict(claim))
        native = dict(ancestors=[encode_row(r) for r in ancestors], session_start=start_row,
                      invocation=dict(id=invocation.identity, owner=invocation.owner, entry=entry))
        return source, result, decision, native

    async def get(self, *, principal: Any, instance_id: str) -> dict:
        # Reuse current-manager native session transport and lock ownership.
        # Select the source owner before private run/context decoding.
        from runtime.workflows.coding_roles import CodingRoleError
        roles = self.org.workflow_coding_roles
        async with roles._access(principal) as (references, capture, _):
            budget = _Budget()
            with roles._read(principal, capture, references, budget=budget) as (conn, current):
                selected = conn.execute('SELECT owner_principal FROM workflow_instances WHERE id=?',
                                        (instance_id,)).fetchone()
                if selected is None or selected[0] != principal.operation_principal:
                    raise CodingRoleError('coding_role_not_found')
                inventory = self.support._storage(conn, references, budget=budget)
                entry = inventory.queued.runs.get(instance_id)
                if entry is None or entry['source']['principal'] != principal.principal_id:
                    raise CodingRoleError('coding_role_not_found')
                from runtime.workflows._coding_settings_execution import _settings_family
                if not _settings_family(entry['command']):
                    roles._current(conn, principal, current, entry['mapping'], budget=budget)
                effective = effective_mapping(entry['mapping'], entry['command'])
                roles._current(conn, principal, current, effective,
                    command=entry['command'], budget=inventory.budget)
                self.db._check_team_move_workflow_uncommitted(binding_bytes=effective_bindings(instance_id, entry['roles']))
                return self.project(instance_id, entry)

    async def get_final(self, *, principal: Any, instance_id: str) -> dict:
        return await self.org.workflow_coding_dispatch.selected_final(principal=principal, instance=instance_id)

    def guard_cancellation(self, task_id: str, *, cascade: bool, rationale: str,
                           actor: str, transitions: Any) -> dict | None:
        # This is an owned writer, never permission for the legacy mutation
        # below it. Reconstruct closure after the route's actual lock wait.
        from runtime.workflows.coding_cancellation import cancel_locked
        return cancel_locked(self.org, task_id, cascade=cascade, rationale=rationale,
                             actor=actor, transitions=transitions)

    def guard_native_mutation(self, task_id: str) -> None:
        # K3a has no standalone native mutation bridge. Preserve complete
        # original observations; do not silently accept new PID/clock/status/
        # counter/session/cancel drift or record a fabricated heartbeat owner.
        conn = self.db._conn
        if conn.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='workflow_coding_runs'").fetchone() is None:
            return
        if not conn.execute('SELECT 1 FROM workflow_coding_runs LIMIT 1').fetchone():
            return
        if task_id in queued_storage(conn, self.org.slug).owners:
            raise CodingStartError('coding_queued_mutation_unavailable')

    def consume(self, claim: RetryClaim, decision: NextStep, report: CompletionReport) -> str:
        with self.db._lock:
            require_start_ready(self.db._conn, self.org, decision.workflow)
        # BEFORE v2 action spending, result claim, or allocation.
        owner = current_invocation()
        coroutine = self._start(claim, decision, report, owner)
        return self.org.workflow_drafts._on_loop(coroutine)

    def _policy(self, claim: RetryClaim, revise_limit: int) -> None:
        # Native file-backed discovery stays outside SQLite intervals.
        actor = prompt_loader.load_agent(self.org.orchestrator._paths, claim.assigned_agent)
        _check(actor is not None and actor.role == 'manager' and bool(actor.executor) and actor.team == claim.team)
        _check(load_org_config(self.org.orchestrator._paths).max_revise_rounds == revise_limit)

    async def _start(self, claim: RetryClaim, decision: NextStep, report: CompletionReport, invocation: Any) -> str:
        references = self.support._schema_references()
        authority = self.org.workflow_authority
        # Discovery/file I/O occurs before the synchronous bootstrap interval.
        capture = self.support._discover()
        if capture is None:
            raise CodingStartError('coding_start_authority_unavailable')
        revise_limit = load_org_config(self.org.orchestrator._paths).max_revise_rounds
        self._policy(claim, revise_limit)
        checkpoints = []

        def checkpoint() -> None:
            with self.db._lock:
                task, _, _, native = self._native(self.db._conn, claim, invocation)
                checkpoints.append(dict(source=encode_row(task), ancestors=native['ancestors']))

        checkpoint()
        async with authority._async_writer_lock:
            self._policy(claim, revise_limit)
            checkpoint()
            async with self.org.teams_lock:
                self._policy(claim, revise_limit)
                checkpoint()
                async with self.org.db_lock:
                    self._policy(claim, revise_limit)
                    sessions = tuple(self.org.sessions.iter_active())
                    with authority.admission_writer(capture) as conn:
                        return self.start_in_writer(conn, claim=claim, decision=decision, report=report, invocation=invocation,
                            capture=capture, references=references, revise_limit=revise_limit, active_sessions=sessions,
                            checkpoints=checkpoints)

    def start_in_writer(self, conn: sqlite3.Connection, *, claim: RetryClaim, decision: NextStep,
                        report: CompletionReport, invocation: Any, capture: AuthorityAdmissionCapture,
                        references: tuple[str, ...], revise_limit: int, active_sessions: tuple, checkpoints: list) -> str:
        """Single uncommitted bootstrap; caller owns native admission transaction.

        No self-committing task/delegate helper, I/O, await or queue publication.
        Any refusal/error unwinds admission_writer and preserves the callback.
        """
        _check(conn is self.db._conn and conn.in_transaction)
        from runtime.infrastructure.workflow_schema import _read_workflow_schema
        inventory = self.support._storage(conn, references)
        command = decision.workflow
        parsed = parse_decision(_canonical(dict(action='workflow_start', workflow=command)))
        _check(self.db.authority_policy_v2_decision_result_report_binds(
            root_task_id=claim.task_id, spending_result_id=claim.result_row_id, report=report))
        # Frozen replay precedes fresh native claim/current capacity checks.
        # Complete schema/reference admission reconstructed these histories on
        # this connection/org/snapshot. Consume locally before any write; the
        # post-write validator below must reconstruct the changed state anew.
        mappings, imported, saved = inventory.mappings, inventory.imported, inventory.queued
        for instance, entry in saved.runs.items():
            old = entry['command']
            if (entry['source']['principal'], old['operation_key']) == (claim.assigned_agent, command['operation_key']):
                if (entry['source']['result']['id'] != claim.result_row_id
                        or entry['source']['task_id'] != claim.task_id
                        or entry['source']['session_id'] != claim.current_session_id
                        or old != command):
                    raise CodingStartError('coding_start_operation_conflict')
                return instance  # No admission/task/counter/event/pointer writes.
        source_before, callback, actual, native = self._native(conn, claim, invocation,
                                                             budget=inventory.budget)
        checkpoints = [*checkpoints, dict(source=encode_row(source_before), ancestors=native['ancestors'])]
        native['checkpoints'] = checkpoints
        _check(same(actual, parsed) and imported is not None and mappings is not None)
        _check(self.db.authority_policy_v2_decision_result_report_binds(
            root_task_id=claim.task_id, spending_result_id=claim.result_row_id, report=report))
        entries = [e for e in mappings.mappings if e.mapping_id == command['mapping']['id']]
        _check(len(entries) == 1)
        selected = entries[0]
        _check(command['mapping'] == dict(id=selected.mapping_id, generation=selected.generation, digest=selected.mapping_digest))
        record = _read_json(selected.mapping_bytes, inventory.budget)
        _resolve_start(command, mappings, imported, inventory.budget, fresh=True)
        mapping = record['mapping']
        effective = effective_mapping(mapping, command)
        roles = effective['roles']
        _check(roles['agent_manager'] == dict(principal=claim.assigned_agent, team=claim.team))
        snapshot = _read_json(capture.ready.snapshot_bytes, inventory.budget)
        _configuration(mapping, snapshot, command, inventory.budget)
        _configuration(effective, snapshot, command, inventory.budget)
        self.org.workflow_authority._validate_admission_uncommitted(conn, capture)
        for selector in roles.values():
            if selector['principal'] != claim.assigned_agent and author_capacity_blocked(conn,
                    author=selector['principal'], task_id=None, active_sessions=active_sessions):
                raise CodingStartError('coding_start_capacity_unavailable')
        for child in rows(conn, 'tasks', inventory.budget, where='parent_task_id=?', args=(claim.task_id,)):
            _check(child['status'] in ('completed', 'failed', 'cancelled', 'superseded'))
        _check(revise_limit == 0 or claim.revision < revise_limit)
        action_claim = self._claim_result_action_uncommitted(claim)
        now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        digest = _hash(_canonical(command))
        instance = identity('root', self.org.slug, claim.task_id, callback['id'], digest)
        attempt_id = identity('attempt', instance, 1)
        event_id = identity('event', self.org.slug, instance, 'agent:' + claim.assigned_agent, command['operation_key'])
        budgets = dict(source_step=claim.step, source_revision=claim.revision,
            session_timeout_seconds=source_before['session_timeout_seconds'], max_revise_rounds=revise_limit,
            coding_attempt_limit=64, coding_event_limit=4096, coding_revision_limit=min(3, revise_limit - claim.revision if revise_limit else 3),
            coding_same_principal_retry_limit=1)
        source = dict(task_id=claim.task_id, session_id=claim.current_session_id, principal=claim.assigned_agent,
            result=dict(id=callback['id'], record=encode_row(callback)),
            result_claim=dict(native_claim=asdict(claim), owner_invocation_id=invocation.identity,
                              action_fingerprint=_hash(callback['decision_json'].encode('utf8'))),
            parent_task_id=source_before['parent_task_id'],
            root_ancestor_id=native['ancestors'][-1]['id'] if native['ancestors'] else claim.task_id, budgets=budgets)
        template = dict(identity_id=imported.identity_id, version_id=imported.version_id, **command['release'])
        authority = _capture(capture, identity('admission', instance), _canonical(command))
        carrier_id = self.db.next_task_id()
        stamp = datetime.fromisoformat(now.replace('Z', '+00:00'))
        carrier = TaskRecord(id=carrier_id, task_type='subtask', parent_task_id=claim.task_id,
            assigned_agent=roles['agent_manager']['principal'], team=roles['agent_manager']['team'],
            brief='Structural coding carrier ' + instance, status=TaskStatus.IN_PROGRESS,
            block_kind=BlockKind.DELEGATED, created_at=stamp, updated_at=stamp,
            session_timeout_seconds=source_before['session_timeout_seconds'])
        self.db._insert_task_uncommitted(carrier)
        leaf_id = self.db.next_task_id()
        leaf = TaskRecord(id=leaf_id, task_type='subtask', parent_task_id=carrier_id,
            assigned_agent=roles['implementer']['principal'], team=roles['implementer']['team'],
            brief=task_brief(command['work']), created_at=stamp, updated_at=stamp,
            session_timeout_seconds=source_before['session_timeout_seconds'])
        self.db._check_team_move_task_uncommitted(leaf)
        pins, context, _binding = pinned_rows(org=self.org.slug, command=command, source=source,
            native_source=native, authority=authority, mapping=mapping, roles=roles,
            template=template, carrier_id=carrier_id, instance_id=instance, created_at=now)
        # Native task serializer uses ISO UTC; journal captures those exact bytes.
        conn.execute('UPDATE tasks SET created_at=?,updated_at=? WHERE id=?', (now, now, carrier_id))
        self.db._check_team_move_workflow_uncommitted(binding_bytes=effective_bindings(instance, roles))
        for table, values in pins.items():
            for row in values:
                names = ','.join(row)
                marks = ','.join('?' for _ in row)
                conn.execute(f'INSERT INTO {table} ({names}) VALUES ({marks})', tuple(row.values()))
        conn.execute('INSERT INTO workflow_coding_runs VALUES (?,?,1,0,?,NULL,NULL)',
                     (instance, selected.mapping_id, 'development'))
        self.db._insert_task_row_uncommitted(leaf)
        conn.execute('UPDATE tasks SET created_at=?,updated_at=? WHERE id=?', (now, now, leaf_id))
        scope = _canonical(initial_scope(org=self.org.slug, instance=instance, attempt=attempt_id,
                                        command=command, roles=roles, context=context))
        conn.execute('INSERT INTO workflow_coding_attempts VALUES (?,?,1,?,1,1,NULL,?,?,NULL,NULL,NULL,NULL,?,?,?)',
                     (attempt_id, instance, 'development', leaf_id, roles['implementer']['principal'], 'queued', scope, _hash(scope)))
        conn.execute('UPDATE tasks SET block_kind=?,note=?,updated_at=? WHERE id=?',
                     ('delegated', 'coding-owned:' + instance, now, claim.task_id))
        conn.execute('UPDATE workflow_coding_runs SET current_attempt_id=? WHERE instance_id=?', (attempt_id, instance))
        budget = inventory.budget
        run = next(rows(conn, 'workflow_coding_runs', budget, where='instance_id=?', args=(instance,)))
        run['last_event_id'] = event_id
        attempt = next(rows(conn, 'workflow_coding_attempts', budget, where='id=?', args=(attempt_id,)))
        tasks = [next(rows(conn, 'tasks', budget, where='id=?', args=(key,)))
                 for key in sorted([claim.task_id, carrier_id, leaf_id])]
        before = dict(run=None, attempts=[dict(id=attempt_id, record=None)],
            instances=[dict(id=instance, record=None)], tasks=[dict(id=r['id'], record=
                encode_row(source_before) if r['id'] == claim.task_id else None) for r in tasks],
            rounds=[], requests=[], assignments=[])
        after = dict(run=run, attempts=[dict(id=attempt_id, record=encode_row(attempt))],
            instances=[dict(id=instance, record=encode_row(pins['workflow_instances'][0]))],
            tasks=[dict(id=r['id'], record=encode_row(r)) for r in tasks], rounds=[], requests=[], assignments=[])
        payload = dict(format='workflow-coding-event@1', id=event_id, instance_id=instance,
            event_seq=1, event_kind='run_started', attempt_id=attempt_id,
            operation=dict(org_slug=self.org.slug, principal='agent:' + claim.assigned_agent,
                           key=command['operation_key'], request_digest=digest), previous_digest=None,
            before=before, after=after, created_at=now,
            evidence=dict(kind='run_started', command=command,
                origin=dict(kind='manager_result', principal=claim.assigned_agent, task_id=claim.task_id,
                            session_id=claim.current_session_id, result_id=callback['id'], claim=None),
                authority=authority, native_task_observations=[],
                rows=journal_rows(pins),
                facts=dict(source=source, carrier_task_id=carrier_id, release=command['release'],
                    mapping_id=selected.mapping_id, mapping_digest=selected.mapping_digest,
                    context_id=identity('context', instance), binding_snapshot_id=identity('binding', instance),
                    authorization_id=identity('authorization', instance), activation_id=identity('activation', instance),
                    first_attempt_id=attempt_id)))
        raw = _canonical(payload)
        _check(len(raw) <= 16777216 and len(scope) <= 1048576)
        conn.execute('INSERT INTO workflow_coding_events VALUES (?,?,1,?,?,?,?,?,?,NULL,?,?,?)',
            (event_id, instance, attempt_id, self.org.slug, 'agent:' + claim.assigned_agent,
             command['operation_key'], digest, 'run_started', raw, _hash(raw), now))
        conn.execute('UPDATE workflow_coding_runs SET last_event_id=? WHERE instance_id=?', (event_id, instance))
        if action_claim is not None:
            ack = self.db._acknowledge_v2_decision_dispatch_uncommitted(
                root_task_id=claim.task_id, manager_agent=action_claim.manager_agent,
                result_id=action_claim.causal_result_id)
            _check(ack.status == 'applied')
        self.org.workflow_authority._validate_admission_uncommitted(conn, capture)
        _, target = _read_workflow_schema(conn, expected_org_slug=self.org.slug, budget=inventory.budget)
        _check(target is not None)
        return instance

    def _claim_result_action_uncommitted(self, claim: RetryClaim) -> Any:
        # Reuse the native owner's existing uncommitted spend/claim/ack seams.
        # Their exact evidence/CAS/limits stay unchanged; all writes now share
        # this bootstrap transaction instead of a prior self-committing call.
        context = self.db.authority_policy_v2_completion_dispatch_context(
            root_task_id=claim.task_id, result_row_id=claim.result_row_id)
        if context.kind in ('no_v2', 'later'):
            return None
        if context.kind == 'reserved':
            outcome = self.db._spend_v2_continuation_envelope_uncommitted(
                root_task_id=claim.task_id, manager_agent=context.manager_agent,
                manager_session_id=context.manager_session_id, result_id=context.causal_result_id,
                generation_id=context.generation_id, next_session_id=context.next_session_id,
                spending_result_id=claim.result_row_id, now_dt=datetime.now(timezone.utc))
            _check(outcome.status == 'spent')
        else:
            _check(context.kind == 'receipt' and context.decision_state == 'ready')
        outcome = self.db._claim_v2_decision_dispatch_uncommitted(
            root_task_id=claim.task_id, manager_agent=context.manager_agent, result_id=context.causal_result_id)
        _check(outcome.status == 'claimed')
        return context

    @staticmethod
    def project(instance: str, entry: dict) -> dict:
        attempt = entry['attempt']
        final = entry['native'].get('final_output')
        closeout = (None if final is None else dict(result_id=attempt['result_id'], task_id=attempt['task_id'],
            subject=final['subject'], outcome=final['outcome']))
        revised = attempt['revision'] > 1
        return CodingRunReceipt.model_validate(dict(format='workflow-coding-run@8' if entry['native'].get('returned') else 'workflow-coding-run@7' if entry['native'].get('cancellation') is not None else 'workflow-coding-run@6' if attempt['attempt_sequence'] > 1 else 'workflow-coding-run@5' if revised else {1: 'workflow-coding-run@1', 2: 'workflow-coding-run@2',
            3: 'workflow-coding-run@3', 4: 'workflow-coding-run@4'}[len(entry['attempts'])], instance_id=instance,
            release=entry['command']['release'], mapping=entry['command']['mapping'], effective_roles=entry['roles'],
            cas_version=entry['run']['cas_version'], current_revision=entry['run']['current_revision'], target_revision=attempt['revision'], state='cancelled' if entry['native'].get('cancellation') is not None else 'held' if entry['native'].get('ownership_unknown') else 'agent_manager' if entry['run']['state'] == 'manager_closeout' else entry['run']['state'],
            current_attempt=dict(id=attempt['id'], task_id=attempt['task_id'], stage='agent_manager' if attempt['stage'] == 'manager_closeout' else attempt['stage'], state='uncertain' if entry['native'].get('ownership_unknown') else attempt['state'], principal=attempt['principal']),
            history=[dict(id=e['id'], kind=e['event_kind'], event_seq=e['event_seq']) for e in entry['native']['history']], closeout=closeout,
            readiness=readiness())).model_dump()


def queued_storage(conn: sqlite3.Connection, org: str, *, budget: _Budget | None = None) -> Any:
    """Compose one complete coding snapshot and cumulative caller allowance.

    Only internally reconstructed inventories are reused within this call.
    Caller-owned transactions are never ended; no schema recursion/cache.
    """
    from runtime.infrastructure.workflow_schema import _read_workflow_schema
    if budget is not None and type(budget) is not _Budget:
        raise CodingStartError('coding_start_history_invalid')
    # Central reconstruction calls read_queued directly, never this composer.
    # Cold/recovery/native callers therefore receive the same structural,
    # shared-dependency and complete history validation without recursion.
    _, inventory = _read_workflow_schema(conn, expected_org_slug=org, budget=budget)
    _check(inventory is not None)
    return inventory.queued

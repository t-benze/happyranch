"""One real pending manager-control owner after positive development failure.

No control execution, selection, retry policy or new capability is admitted.
"""
from __future__ import annotations

import base64
import json
from copy import deepcopy
from datetime import datetime
from typing import Any

from runtime.models import TaskRecord
from runtime.workflows.coding_authority_history import _Budget
from runtime.workflows.coding_bootstrap import effective_bindings, encode_row, identity
from runtime.workflows.coding_control_history import RESTORE, control_brief, positive_failure
from runtime.workflows.coding_saved_records import _canonical, _check, _hash
from runtime.workflows.coding_start import queued_storage
from runtime.workflows.coding_start_history import rows


class CodingControlQueue:
    def __init__(self, dispatcher: Any) -> None:
        self.dispatcher, self.org, self.db = dispatcher, dispatcher.org, dispatcher.db

    def hold_uncommitted(self, conn: Any, entry: dict) -> None:
        state = entry['native']
        witnesses = positive_failure(state, entry['attempt'])
        if witnesses is None or state.get('safe_failure_held') is not None or state.get('control') is not None:
            return
        self._append(conn, entry, 'held', dict(reason='stage_safe_failure', owner_event_ids=witnesses,
            resume_state='development', required_owner=entry['source']['principal'], required_action=RESTORE))

    def _current(self, conn: Any, entry: dict, capture: Any) -> dict:
        from runtime.workflows._coding_role_records import configuration
        from runtime.workflows.coding_mapping_history import read_mapping_storage
        from runtime.workflows.coding_start_records import effective_mapping
        _check(not self.org._runner_admission_closed and self.org.orchestrator._db is self.db)
        self.org.workflow_authority._validate_admission_uncommitted(conn, capture)
        selected = [m for m in read_mapping_storage(conn, expected_org_slug=self.org.slug).mappings
                    if m.mapping_id == entry['command']['mapping']['id']]
        _check(len(selected) == 1)
        mapping = json.loads(selected[0].mapping_bytes)['mapping']
        snapshot = json.loads(capture.ready.snapshot_bytes)
        configuration(mapping, snapshot)
        configuration(effective_mapping(mapping, entry['command']), snapshot)
        _check(self.org.teams.teams_for_manager(entry['source']['principal']) ==
               (entry['roles']['agent_manager']['team'],))
        _check(positive_failure(entry['native'], entry['attempt']) is not None
               and entry['run']['state'] == 'held')
        for task in entry['tasks'].values():
            self.db._check_team_move_task_uncommitted(task['id'])
            _check(task['cancelled_at'] is None and task['zombie_flagged_at'] is None)
        self.db._check_team_move_workflow_uncommitted(binding_bytes=effective_bindings(entry['run']['instance_id'], entry['roles']))
        root, pause = self.db._task_pause_store.held_uncommitted(entry['source']['task_id'])
        _check(root['id'] == entry['source']['root_ancestor_id'] and pause is not None)
        _check(not any(native['task_id'] in entry['tasks'] for native in pause['journal']['entries'])
               and not any(native.task.id in entry['tasks'] for native in self.db._task_pause_store._live.values())
               and not any(owner.task_id in entry['tasks'] for owner in self.org.orchestrator._team_moves._reservations.values()))
        return mapping

    def _admission(self, conn: Any, entry: dict, capture: Any, sessions: tuple) -> dict:
        from runtime.workflows._coding_import_support import _capture
        from runtime.workflows.activation import author_capacity_blocked
        _check(len(sessions) <= 1024)
        manager = entry['roles']['agent_manager']['principal']
        _check(not author_capacity_blocked(conn, author=manager, task_id=None, active_sessions=sessions))
        budget = _Budget()
        tasks = dict(entry['tasks'])
        task = tasks[entry['source']['task_id']]
        seen = set()
        while True:
            _check(task['id'] not in seen and len(seen) < 20 and task['status'] == 'in_progress'
                   and task['cancelled_at'] is None and task['zombie_flagged_at'] is None
                   and task['team'] == entry['roles']['agent_manager']['team'])
            seen.add(task['id'])
            self.db._check_team_move_task_uncommitted(task['id'])
            if task['parent_task_id'] is None:
                break
            found = list(rows(conn, 'tasks', budget, where='id=?', args=(task['parent_task_id'],), maximum=1))
            _check(len(found) == 1)
            task = found[0]
            tasks[task['id']] = task
        _check(task['id'] == entry['source']['root_ancestor_id'])
        limits, source = entry['source']['budgets'], entry['tasks'][entry['source']['task_id']]
        _check(source['orchestration_step_count'] == limits['source_step']
               and source['revision_count'] == limits['source_revision']
               and (not limits['max_revise_rounds'] or limits['source_revision'] < limits['max_revise_rounds'])
               and limits['coding_attempt_limit'] >= 2
               and entry['run']['cas_version'] < limits['coding_event_limit'])
        current = _capture(capture, identity('control-admission', entry['run']['instance_id'],
            entry['run']['last_event_id']), _canonical(entry['command']))
        pin = current['pin']
        record = dict(id=current['admission_record_id'], namespace=pin['namespace'], generation=pin['generation'],
            request_digest=_hash(_canonical(entry['command'])), admitted_by='service:' + self.dispatcher.producer)
        pause = next(rows(conn, 'task_pause_controls', budget, where='root_task_id=?',
            args=(entry['source']['root_ancestor_id'],), maximum=1))
        value = dict(format='workflow-coding-control-admission@1', authority=current, record=record,
            manager_teams=list(self.org.teams.teams_for_manager(entry['source']['principal'])),
            tasks=[encode_row(r) for _, r in sorted(tasks.items())], pause=encode_row(pause),
            capacity=dict(sessions=[list(s) for s in sessions], tasks=[encode_row(r) for r in rows(conn, 'tasks', budget,
                where="assigned_agent=? AND status='in_progress' AND block_kind IS NULL AND cancelled_at IS NULL AND current_session_id IS NOT NULL",
                args=(manager,))], work_hours=[encode_row(r) for r in rows(conn, 'work_hours', budget,
                where="agent_name=? AND status='running'", args=(manager,))]),
            max_revise_rounds=limits['max_revise_rounds'],
            effective_bindings_base64=base64.b64encode(effective_bindings(entry['run']['instance_id'], entry['roles'])).decode('ascii'))
        _check(len(_canonical(value)) <= 4194304)
        conn.execute('INSERT INTO workflow_admission_records VALUES (?,?,?,?,?)', tuple(record[k]
            for k in ('id', 'namespace', 'generation', 'request_digest', 'admitted_by')))
        return value

    async def reconcile(self, task_id: str, queue: Any = None) -> dict | None:
        """No awaited work/lease acquisition under a synchronous native owner."""
        with self.db._lock:
            entry = self.dispatcher._entry(self.db._conn, task_id)
        if positive_failure(entry['native'], entry['attempt']) is None:
            return None
        # Safe-failure hold is durable even when current authority is unavailable.
        with self.dispatcher._writer() as conn:
            self.hold_uncommitted(conn, self.dispatcher._entry(conn, task_id))
        capture = self.org.workflow_authority.capture_admission()
        authority = self.org.workflow_authority

        def checkpoint() -> None:
            self.dispatcher._policies(entry)  # File-backed current policy outside DB ownership.
            with self.db._lock:
                self._current(self.db._conn, self.dispatcher._entry(self.db._conn, task_id), capture)

        checkpoint()
        async with authority._async_writer_lock:
            checkpoint()
            async with self.org.teams_lock:
                checkpoint()
                async with self.org.db_lock:
                    checkpoint()
                    sessions = tuple(self.org.sessions.iter_active())
                    # Acquire publication/profile leases before Pause/move/DB gates.
                    with authority._admission_ownership(capture):
                        with self.db._task_pause_store.gate(entry['source']['root_ancestor_id']):
                            with self.org.orchestrator._team_moves.gate:
                                with self.dispatcher._writer() as conn:
                                    current = self.dispatcher._entry(conn, task_id)
                                    mapping = self._current(conn, current, capture)
                                    control = current['native'].get('control')
                                    if control is None:
                                        control = self._allocate(conn, current, mapping, capture, sessions)
                    if queue is not None and control is not None:
                        # Notification only; ordinary queue admission still refuses
                        # before prepare/SID/heartbeat. Durable identity owns replay.
                        queue.enqueue_if_absent(self.org.slug, control['control_task_id'], metadata=dict(
                            owner='coding', control_id=control['control_id'],
                            predecessor_attempt_id=control['predecessor_attempt_id'],
                            notification_event_id=control['notification_event_id'],
                            notification_digest=control['notification_digest'],
                            notification_command=control['notification_command'],
                            original_enqueued_at=entry['event']['created_at']))
                    return control

    def _allocate(self, conn: Any, entry: dict, mapping: dict, capture: Any, sessions: tuple) -> dict:
        from runtime.workflows.coding_dispatch import stamp
        state, attempt = entry['native'], entry['attempt']
        held = state.get('safe_failure_held')
        _check(held == entry['run']['last_event_id'] and state.get('control') is None)
        admission = self._admission(conn, entry, capture, sessions)
        instance, binding = entry['run']['instance_id'], entry['roles']['agent_manager']
        control_id = 'coding-control:' + _hash(_canonical([instance, attempt['id'], held]))
        task_id, now = self.db.next_task_id(), stamp()
        carrier = json.loads(entry['event']['event_bytes'])['evidence']['facts']['carrier_task_id']
        task = TaskRecord(id=task_id, task_type='subtask', parent_task_id=carrier,
            assigned_agent=binding['principal'], team=binding['team'], brief=control_brief(instance, attempt['id']),
            session_timeout_seconds=entry['source']['budgets']['session_timeout_seconds'],
            created_at=datetime.fromisoformat(now.replace('Z', '+00:00')),
            updated_at=datetime.fromisoformat(now.replace('Z', '+00:00')))
        self.db._check_team_move_task_uncommitted(task)
        self.db._check_team_move_workflow_uncommitted(binding_bytes=effective_bindings(instance, entry['roles']))
        self.db._insert_task_uncommitted(task)
        conn.execute('UPDATE tasks SET created_at=?,updated_at=? WHERE id=?', (now, now, task_id))
        native_task = next(rows(conn, 'tasks', _Budget(), where='id=?', args=(task_id,), maximum=1))
        scope = dict(format='workflow-coding-control-scope@1', control_id=control_id, instance_id=instance,
            control_task_id=task_id, predecessor_attempt_id=attempt['id'], predecessor_scope_digest=attempt['scope_digest'],
            held_event_id=held, manager_binding=dict(**binding, task_id=task_id), release=entry['command']['release'],
            mapping_digest=entry['command']['mapping']['digest'], authority=admission['authority'],
            positive_witness_event_ids=positive_failure(state, attempt), eligible_targets=mapping['eligible_replacements']['implementer'],
            original_limits=entry['source']['budgets'], business_purpose='coding-replacement-selection@1')
        raw = _canonical(scope)
        _check(len(raw) <= 1048576)
        control = dict(control_id=control_id, control_task_id=task_id, predecessor_attempt_id=attempt['id'],
            notification_event_id=None, manager_principal=binding['principal'], manager_team=binding['team'],
            business_purpose='coding-replacement-selection@1', original_limits=entry['source']['budgets'])
        self._append(conn, entry, 'manager_control_queued', dict(control=control,
            positive_witness_event_ids=scope['positive_witness_event_ids'], task_record=encode_row(native_task),
            scope_bytes_base64=base64.b64encode(raw).decode('ascii'), scope_digest=_hash(raw)),
            admission=admission, task_id=task_id, now=now, producer=control_id)
        # Complete independent closure sees all new native/J/admission rows
        # before the enclosing _writer commits or publishes a notification.
        return queued_storage(conn, self.org.slug).runs[instance]['native']['control']

    def _append(self, conn: Any, entry: dict, kind: str, facts: dict, *, admission: dict | None = None,
                task_id: str | None = None, now: str | None = None, producer: str | None = None) -> None:
        from runtime.workflows.coding_dispatch import stamp
        previous = entry['native']['history'][-1]
        _check(conn.in_transaction and entry['run']['cas_version'] < entry['source']['budgets']['coding_event_limit'])
        instance, principal = entry['run']['instance_id'], 'service:' + self.dispatcher.producer
        command = dict(format='workflow-coding-internal-command@3', kind=kind, source_event_ids=[previous['id']],
            source_event_digests=[previous['event_digest']], subject=None,
            producer_identity=producer or self.dispatcher.producer,
            observation=None if admission is None else dict(admission=admission))
        key = 'coding-internal:' + _hash(_canonical([instance, kind, command['producer_identity'],
            command['source_event_ids'], command['observation']]))
        event_id = identity('event', self.org.slug, instance, principal, key)
        before = self.dispatcher._projection(entry)
        after = deepcopy(before)
        after['run'].update(cas_version=entry['run']['cas_version'] + 1, last_event_id=event_id, state='held')
        if task_id is not None:
            facts['control']['notification_event_id'] = event_id
            before['tasks'].append(dict(id=task_id, record=None))
            after['tasks'].append(dict(id=task_id, record=facts['task_record']))
            for value in (before, after):
                value['tasks'].sort(key=lambda row: row['id'])
        body = dict(format='workflow-coding-event@3', id=event_id, instance_id=instance,
            event_seq=previous['event_seq'] + 1, event_kind=kind, attempt_id=entry['attempt']['id'],
            operation=dict(org_slug=self.org.slug, principal=principal, key=key, request_digest=_hash(_canonical(command))),
            previous_digest=previous['event_digest'], before=before, after=after, created_at=now or stamp(),
            evidence=dict(kind=kind, command=command, origin=dict(kind='service', principal=principal, task_id=None,
                session_id=None, result_id=None, claim=None), authority=entry['authority'] if admission is None else admission['authority'],
                rows=[] if admission is None else [dict(table='workflow_admission_records',
                    id=admission['record']['id'], record=encode_row(admission['record']))], facts=facts,
                native_task_observations=entry['native']['pending_observations']))
        raw = _canonical(body)
        _check(len(raw) <= 16777216)
        conn.execute('INSERT INTO workflow_coding_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (event_id, instance, body['event_seq'], entry['attempt']['id'], self.org.slug, principal, key,
             body['operation']['request_digest'], kind, previous['event_digest'], raw, _hash(raw), body['created_at']))
        changed = conn.execute('UPDATE workflow_coding_runs SET cas_version=?,state=?,last_event_id=? '
            'WHERE instance_id=? AND cas_version=? AND last_event_id=? AND current_attempt_id=?',
            (after['run']['cas_version'], 'held', event_id, instance, entry['run']['cas_version'],
             previous['id'], entry['attempt']['id']))
        _check(changed.rowcount == 1)

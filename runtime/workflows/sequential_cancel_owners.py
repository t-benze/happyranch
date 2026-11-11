"""One mixed stop's original native owners and one original root-tail batch.

This in-memory handoff is neither a receipt nor recovery authority. Lost originals
stay unsettled. Only the original settle_live finally supplies observations.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from runtime.infrastructure.task_pause_controls import decode_journal
from runtime.workflows.coding_authority_history import _Budget
from runtime.workflows.sequential_cancel import _check
from runtime.workflows.sequential_engine import _engine_rows
from runtime.workflows.sequential_history import _ReadBudget


@dataclass
class CancelOwners:
    org: Any
    originals: dict[str, tuple[str, Any]]
    unknown: bool
    observations: dict[str, bool] = field(default_factory=dict)
    transitions: Any = None
    committed: bool = False
    sealed: bool = False
    consumed: bool = False

    @classmethod
    def capture(cls, org: Any, conn: sqlite3.Connection, tasks: dict, history: Any,
                budget: _ReadBudget, *, saved: Any, retained_cancelled: bool) -> CancelOwners:
        """Capture actual objects and journals; absence never creates an object."""
        from runtime.orchestrator.task_pause import TaskInvocation

        store = getattr(org.db, '_task_pause_store', None)
        bindings = {key: (kind, owner, instance) for key, kind, owner, instance in history.owners}
        kinds = {key: binding[0] for key, binding in bindings.items()}
        originals, journals = {}, {}
        roots = history.root_task_ids
        if store is not None:
            for root in roots:
                for pause in _engine_rows(conn, 'task_pause_controls', budget, 'root_task_id=?', (root,)):
                    budget.reserve(24 * len(pause['admission_journal_json'].encode('utf8')) + 512)
                    for entry in decode_journal(pause['admission_journal_json'])['entries']:
                        budget.step()
                        if entry['task_id'] in tasks:
                            _check(entry['id'] not in journals and len(journals) < 512)
                            journals[entry['id']] = entry
            for identity, invocation in store._live.items():
                budget.step()
                if invocation.task.id in tasks:
                    _check(isinstance(invocation, TaskInvocation) and invocation.identity == identity
                           and invocation.orch is org.orchestrator and invocation.store is store
                           and len(originals) < 512)
                    originals[identity] = (kinds[invocation.task.id], invocation)
        unknown = any(identity not in originals for identity in journals)
        bound_tasks = {inv.task.id for _, inv in originals.values()}
        for identity, (_, invocation) in originals.items():
            entry = journals.get(identity)
            unknown |= (entry is None or (entry['task_id'], entry['agent'], entry['session_id'], entry['owner']) !=
                        (invocation.task.id, invocation.agent, invocation.session_id, invocation.owner))
        for key, task in tasks.items():
            budget.step()
            # Structural parked rows have no executor. Running, prepared and
            # tracked originals require a genuine original invocation instead.
            controls = tuple(org.sessions.iter_task_cancel_controls(key))
            pids = tuple(org.sessions.iter_task_pids(key))
            required = bool(controls or pids) or (task['status'] == 'in_progress'
                        and task['block_kind'] is None and kinds[key] != 'seq_carrier')
            unknown |= required and key not in bound_tasks
            if kinds[key] in ('seq_source_waiting', 'seq_visit') and key not in bound_tasks:
                kind, owner, run = bindings[key]
                # The full oracle already authenticates these original positive
                # outers. An original queued/unbound engine visit has no native
                # invocation; any captured preparation/journal still controls.
                # Otherwise a parked/terminal task is not settlement.
                queued = False
                if (kind == 'seq_visit' and task['status'] == 'pending'
                        and task['current_session_id'] is None and task['orchestration_step_count'] == 0):
                    visits = _engine_rows(conn, 'workflow_seq_visits', budget, 'id=?', (owner,))
                    queued = (len(visits) == 1 and visits[0]['state'] == 'queued'
                              and visits[0]['session_id'] is None)
                if not queued:
                    evidence = _engine_rows(conn, 'workflow_seq_evidence', budget,
                        "run_id=? AND visit_id IS ? AND kind='outer'",
                        (run, owner if kind == 'seq_visit' else None))
                    unknown |= not evidence
            for agent, _ in (*controls, *pids):
                unknown |= not any(inv.task.id == key and inv.agent == agent for _, inv in originals.values())
        # Retained cancellation has no durable positive sequential-cancellation
        # outer receipt. Without its original owners it cannot gain clearance.
        unknown |= retained_cancelled and not originals
        if saved is not None:
            for key in tasks:
                budget.step()
                owner = saved.owners.get(key)
                if owner is None:
                    continue
                entry = saved.runs[owner[1]]
                native = entry['native']
                if key == entry['source']['task_id']:
                    unknown |= not native['source_settled'] and key not in bound_tasks
                else:
                    for attempt in entry['attempts'].values():
                        if attempt['task_id'] == key:
                            state = native['stages'].get(attempt['id'])
                            if state is not None:
                                owned = state['claim'] is not None or state.get('queue_owner') is not None or state['host'] is not None
                                unknown |= owned and not (state['cleanup_recorded'] and state['cleanup']) and key not in bound_tasks
        budget.reserve(1024 * (len(originals) + len(journals)))
        return cls(org, originals, bool(unknown))

    @property
    def incomplete(self) -> bool:
        return self.unknown or len(self.observations) != len(self.originals) or not all(self.observations.values())

    def attach(self, transitions: Any, captures: Any) -> None:
        """Publish only to the captured originals; rollback removes this handoff."""
        self.transitions = transitions
        captures.callback(self._rollback)
        for _, invocation in self.originals.values():
            owners = getattr(invocation, '_sequential_cancel_owners', ())
            _check(len(owners) < 16)
            invocation._sequential_cancel_owners = (*owners, self)

    def _rollback(self) -> None:
        if not self.committed:
            for _, invocation in self.originals.values():
                invocation._sequential_cancel_owners = tuple(owner for owner in
                    getattr(invocation, '_sequential_cancel_owners', ()) if owner is not self)

    def observe(self, invocation: Any, *, failed: bool, cleanup: Any, native_entry: dict | None,
                close_disposition: dict | None, release_disposition: dict | None) -> None:
        """Compose proof owners after their original native/family finally work."""
        db = self.org.db
        with db._lock:
            original = self.originals.get(invocation.identity)
            _check(original is not None and original[1] is invocation)
            _check(invocation.identity not in self.observations)
            kind = original[0]
            positive = False
            if kind in ('seq_source_waiting', 'seq_visit'):
                from runtime.workflows.sequential_cancellation_tail import cancelled_owner_disposition_uncommitted
                budget, retained = _ReadBudget(), _Budget()
                with db.coherent_read_view() as conn:
                    positive = cancelled_owner_disposition_uncommitted(conn, self.org, invocation,
                        failed=failed, cleanup=cleanup, native_entry=native_entry,
                        close_disposition=close_disposition, release_disposition=release_disposition,
                        budget=budget, retained_budget=retained).settled
            elif kind.startswith('coding_'):
                # Read the existing positive family reconstruction after its
                # actual outer_settled COMMIT, bound to this exact invocation.
                dispatcher = getattr(invocation.orch, '_workflow_coding_dispatch', None)
                if (failed is False and dispatcher is not None and native_entry is not None
                        and getattr(invocation, 'coding_settled_entry', None) is native_entry):
                    from runtime.workflows.sequential_legacy_cancel import legacy_cancellation_scope
                    with db.coherent_read_view() as conn, legacy_cancellation_scope(
                            conn, org_slug=self.org.slug, task_id=invocation.task.id):
                        entry = dispatcher._entry(conn, invocation.task.id)
                        native = entry['native']
                        if kind == 'coding_source_waiting':
                            positive = native['source_settled'] is True
                        elif kind == 'coding_attempt':
                            state = native['stages'][next(a['id'] for a in entry['attempts'].values()
                                if a['task_id'] == invocation.task.id)]
                            positive = state['cleanup_recorded'] is True and state['cleanup'] is True
            # Ordinary/document families expose no existing positive family
            # cancellation outer owner. The manager's Option B fallback keeps
            # these captured invocations UNSETTLED rather than inventing one.
            self.observations[invocation.identity] = positive
        self._drain()

    def seal(self, *, failed: bool) -> None:
        with self.org.db._lock:
            _check(self.committed and not self.sealed)
            self.unknown |= failed
            self.sealed = True
        self._drain()

    def _drain(self) -> None:
        with self.org.db._lock:
            if self.consumed or not self.sealed:
                return
            if not self.unknown and all(self.observations.values()) and len(self.observations) < len(self.originals):
                return  # The original invocation, not a retry, retains delivery.
            self.consumed = True
            incomplete = self.incomplete
        from runtime.orchestrator.run_step import _root_worktree_batch
        _root_worktree_batch(self.org.orchestrator, self.transitions, teardown_incomplete=incomplete)


def observe_cancel_owners(invocation: Any, **observations: Any) -> None:
    for owner in getattr(invocation, '_sequential_cancel_owners', ()):
        try:
            owner.observe(invocation, **observations)
        except BaseException:
            # Preserve original native errors and retained ownership. No failed
            # reader can promote any owner, and there is no cleanup retry.
            with owner.org.db._lock:
                owner.unknown = True
                owner.observations[invocation.identity] = False
            owner._drain()
            raise

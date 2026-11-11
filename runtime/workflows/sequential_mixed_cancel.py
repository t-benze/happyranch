"""Mixed business stop in one writer; native settlement keeps original owners."""
from __future__ import annotations

import hashlib
import sqlite3
from contextlib import ExitStack
from datetime import datetime, timezone
from typing import Any

from runtime.models import BlockKind, TaskStatus
from runtime.workflows.coding_authority_history import _Budget
from runtime.workflows.coding_source_history import _TASK_TYPES
from runtime.workflows.sequential_cancel import _check, _closure, _controls, _KINDS, _TERMINAL
from runtime.workflows.sequential_definition import compile_definition
from runtime.workflows.sequential_engine import _engine_rows, cancel_fence_uncommitted
from runtime.workflows.sequential_history import _ReadBudget, _bytes, _sql_budget, read_sequential_history
from runtime.workflows.sequential_mixed_history import MixedSequentialHistory, MixedTaskHistory, _rows


def has_sequential_descendant(org: Any, task_id: str) -> bool:
    """Select the existing mixed owner before any ordinary-family mutation."""
    from runtime.infrastructure.workflow_task_ownership import selected_owner

    budget = _ReadBudget()
    with org.db.coherent_read_view() as conn, _sql_budget(conn, budget):
        return any(selected_owner(conn, key, org.slug)[0] in _KINDS
                   for key in _closure(conn, task_id, budget))


def _tasks(history: MixedTaskHistory, budget: _ReadBudget) -> dict[str, dict]:
    budget.reserve(512 * len(history.tasks) + 128 * len(history.task_columns) * len(history.tasks))
    return {row[0]: dict(zip(history.task_columns, row, strict=True)) for row in history.tasks}


def _affected(history: MixedTaskHistory, task_id: str, cascade: bool,
              budget: _ReadBudget) -> tuple[dict[str, dict], set[str]]:
    """Ordinary live walk plus whole sequential families; terminal paths retained.

    A point stop cannot sever a family dependency, even through terminal rows.
    Live dispatch members retain their existing inner cancellation writers.
    """
    tasks = _tasks(history, budget)
    owners = {key: (kind, owner, instance) for key, kind, owner, instance in history.owners}
    children: dict[str | None, list[str]] = {}
    families: dict[tuple[bool, str], set[str]] = {}
    dependencies: set[str] = set()
    for key, task in tasks.items():
        budget.step()
        children.setdefault(task['parent_task_id'], []).append(key)
        if owners[key][2] is not None:
            families.setdefault((owners[key][0] in _KINDS, owners[key][2]), set()).add(key)
            parent = task['parent_task_id']
            while parent is not None and parent not in dependencies:
                budget.step()
                dependencies.add(parent)
                parent = tasks[parent]['parent_task_id']
    _check(cascade or owners[task_id][0] in _KINDS or task_id not in dependencies)
    pending, selected, runs, retaining, expanded = [task_id], {}, set(), set(), set()
    while pending:
        budget.step()
        key = pending.pop()
        if key in selected:
            continue
        task = tasks[key]
        _check(key not in retaining or task['status'] in _TERMINAL)
        selected[key] = task
        kind, _, instance = owners[key]
        if instance is not None:
            family = (kind in _KINDS, instance)
            if family not in expanded:
                _check(key not in retaining or all(tasks[member]['status'] in _TERMINAL
                                                  for member in families[family]))
                expanded.add(family)
                pending.extend(families[family])
            if kind in _KINDS:
                runs.add(instance)
        _check(task['status'] in _TERMINAL or kind in ('legacy', 'draft', 'f5')
               or kind.startswith('coding_') or kind in _KINDS)
        if cascade:
            if task['status'] not in _TERMINAL:
                pending.extend(children.get(key, ()))
            elif key in dependencies:
                # Follow only known sequential dependency paths below a terminal
                # ordinary row. Never expand its unrelated ordinary subtrees.
                for child in children.get(key, ()):
                    budget.step()
                    if child in dependencies or owners[child][2] is not None:
                        _check(tasks[child]['status'] in _TERMINAL)
                        retaining.add(child)
                        pending.append(child)
    if not cascade:
        for key, task in tasks.items():
            budget.step()
            if key in selected or task['status'] in _TERMINAL:
                continue
            parent = task['parent_task_id']
            while parent is not None and parent not in selected:
                budget.step()
                parent = tasks[parent]['parent_task_id']
            _check(parent is None)  # No live foreign child left behind by a point stop.
    return selected, runs


def _unchanged(conn: sqlite3.Connection, history: MixedTaskHistory, budget: _ReadBudget) -> None:
    """Fresh exact native comparison after native control discovery, before DML."""
    for original in history.tasks:
        budget.step()
        _check(_rows(conn, 'tasks', history.task_columns, _TASK_TYPES, budget,
                     where='id=?', args=(original[0],), maximum=1) == [original])


async def cancel_mixed(org: Any, task_id: str, *, cascade: bool, actor: str,
                       rationale: str, transitions: Any) -> dict:
    """Fence a mixed closure; return controls only after COMMIT/outside locks."""
    from runtime.orchestrator.run_step import _capture_root_worktree_transition

    _check(type(cascade) is bool and type(actor) is str and bool(actor)
           and type(rationale) is str)
    budget, retained = _ReadBudget(), _Budget()
    budget.reserve(24 * (len(actor.encode('utf8')) + len(rationale.encode('utf8'))) + 512)
    authority = org.workflow_authority
    _check(authority is not None)
    capture = authority.capture_admission()
    async with authority._async_writer_lock:
        async with org.teams_lock:
            async with org.db_lock:
                with ExitStack() as captures:
                    with authority.admission_writer(capture) as conn:
                        before = read_sequential_history(conn, expected_org_slug=org.slug,
                            retained_budget=retained, deadline=budget.deadline, task_id=task_id)
                        _check(isinstance(before, MixedSequentialHistory) and before.task_history is not None)
                        history = before.task_history
                        with _sql_budget(conn, budget):
                            selected, runs = _affected(history, task_id, cascade, budget)
                            _check(bool(runs))
                            # Preserve the old Coding reverse-dependency point
                            # guard too; the original reader owns these facts.
                            saved = before.retained[1].queued if before.retained and before.retained[1] else None
                            _check(cascade or saved is None or task_id in saved.owners
                                   or task_id not in saved.cancellation_dependencies)
                            live = {key: row for key, row in selected.items() if row['status'] not in _TERMINAL}
                            _check(task_id in live)
                            selected_kind = next(kind for key, kind, _, _ in history.owners if key == task_id)
                            _check(cascade or selected_kind not in ('seq_source_waiting', 'seq_carrier')
                                   or set(live) == {task_id})
                            fresh, retained_cancelled = [], False
                            for run_id in sorted(runs):
                                found = _engine_rows(conn, 'workflow_seq_runs', budget, 'id=?', (run_id,))
                                _check(len(found) == 1)
                                run = found[0]
                                members = {key for key, kind, _, instance in history.owners
                                           if kind in _KINDS and instance == run_id}
                                if run['state'] in ('completed', 'cancelled'):
                                    _check(all(selected[key]['status'] in _TERMINAL for key in members))
                                    retained_cancelled |= run['state'] == 'cancelled'
                                    continue
                                _check(run['state'] in ('active', 'held') and bool(members & live.keys()))
                                pins = _engine_rows(conn, 'workflow_seq_definitions', budget,
                                                    'id=?', (run['definition_id'],))
                                _check(len(pins) == 1)
                                budget.reserve(24 * len(pins[0]['definition_bytes']) + 512)
                                compiled = compile_definition(pins[0]['definition_bytes'],
                                                              source_digest=pins[0]['source_digest'])
                                key = 'seq-cancel-' + hashlib.sha256(_bytes(
                                    [run_id, task_id, cascade, actor, rationale])).hexdigest()
                                fresh.append((run, compiled, key))
                            controls = []
                            all_tasks = _tasks(history, budget)
                            by_root: dict[str, dict[str, dict]] = {}
                            for key, task in selected.items():
                                root = key
                                while all_tasks[root]['parent_task_id'] is not None:
                                    budget.step()
                                    root = all_tasks[root]['parent_task_id']
                                by_root.setdefault(root, {})[key] = task
                            for root, native_tasks in by_root.items():
                                native, uncertain = _controls(org, conn, root, native_tasks, budget)
                                controls.extend(item for item in native if item[0] in live)
                            # Original Coding closure is independently reconstructed.
                            # It owns its exact original native subset; detached
                            # foreign members retain the mixed overlay owner.
                            coding_selection, instances = {}, set()
                            if saved is not None:
                                from runtime.workflows.coding_cancellation import affected
                                coding_selection, instances = affected(conn, task_id, cascade, saved, retained)
                                if instances:
                                    _check(coding_selection.keys() <= selected.keys())
                            from runtime.workflows.sequential_legacy_cancel import legacy_cancellation_scope
                            documents = []
                            with legacy_cancellation_scope(conn, org_slug=org.slug, task_id=task_id,
                                    cancelled=False, retained_budget=retained, deadline=budget.deadline):
                                for key, kind, _, _ in history.owners:
                                    if key in live and kind in ('draft', 'f5'):
                                        dispatcher = org.workflow_drafts if kind == 'draft' else org.workflow_reviews
                                        _check(dispatcher is not None and dispatcher.org is org and dispatcher.db is org.db)
                                        # Resolve original controls/intent before any DML.
                                        intent = dispatcher._intent(conn, key)
                                        documents.append((key, dispatcher, intent))
                            with _sql_budget(conn, budget):
                                from runtime.workflows.sequential_cancel_owners import CancelOwners
                                owners = CancelOwners.capture(org, conn, selected, history, budget, saved=saved,
                                    retained_cancelled=retained_cancelled)
                                _unchanged(conn, history, budget)
                        for run, compiled, key in fresh:
                            cancel_fence_uncommitted(conn, org.db, org_slug=org.slug, run_id=run['id'],
                                operation_key=key, expected_cas=run['cas'], compiled=compiled,
                                authority=authority, capture=capture)
                        # Every family uses its original event/command bytes. Old
                        # Coding, when present, owns the sole native overlays too.
                        for key, dispatcher, intent in documents:
                            dispatcher._cancel_uncommitted(conn, key, native_overlay=False, intent=intent)
                        note = f'cancelled by {actor}: {rationale}' if rationale else f'cancelled by {actor}'
                        if instances:
                            from runtime.workflows.coding_cancellation import write_uncommitted
                            now, _ = write_uncommitted(org, task_id, cascade=cascade, rationale=rationale,
                                actor=actor, transitions=transitions, captures=captures, saved=saved,
                                selected=coding_selection, instances=instances, budget=retained)
                        else:
                            now = datetime.now(timezone.utc).isoformat()
                        for key in sorted(live.keys() - (coding_selection.keys() if instances else set())):
                            captures.enter_context(_capture_root_worktree_transition(
                                org.orchestrator, key, 'cancel_task', transitions))
                            conn.execute("UPDATE tasks SET status='cancelled',block_kind=NULL,note=?,"
                                'cancelled_at=?,completed_at=?,updated_at=? WHERE id=?', (note, now, now, now, key))
                            org.db.insert_audit_log_uncommitted(key, actor, 'task_cancelled',
                                                              dict(rationale=rationale, cascade=cascade))
                        after = read_sequential_history(conn, expected_org_slug=org.slug,
                            retained_budget=retained, deadline=budget.deadline, task_id=task_id)
                        _check(isinstance(after, MixedSequentialHistory) and after.task_history is not None)
                        target = after.task_history
                        with _sql_budget(conn, budget):
                            expected = _tasks(history, budget)
                            for key in live:
                                expected[key].update(status='cancelled', block_kind=None, note=note,
                                                     cancelled_at=now, completed_at=now, updated_at=now)
                            _check(target.tasks == tuple(tuple(expected[key][column] for column in history.task_columns)
                                for key in sorted(expected)) and target.owners == history.owners
                                and target.results == history.results and target.root_task_ids == history.root_task_ids)
                            owners.attach(transitions, captures)
                    owners.committed = True
                return dict(tasks=sorted(live), prior={key: TaskStatus(row['status']) for key, row in live.items()},
                    block_kinds={key: BlockKind(row['block_kind']) if row['block_kind'] else None
                                 for key, row in live.items()}, controls=controls, teardown_incomplete=owners.incomplete, owners=owners,
                    sequential_tasks=sorted(key for key, kind, _, _ in history.owners if key in live and kind in _KINDS))

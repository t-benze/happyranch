"""Inert U6a mixed native/ownership closure; no cancellation or callback writer.

The complete sequential oracle calls this only on explicit selection, after all
retained families validate in the SAME snapshot. Returned native values are
immutable observations, never current authority, settlement or effect clearance.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TypeAlias

from runtime.infrastructure.workflow_schema import _structure_cells
from runtime.infrastructure.workflow_task_ownership import _binding_owner
from runtime.models import BlockKind, TaskStatus
from runtime.workflows.coding_authority_history import _bounded_rows
from runtime.workflows.coding_source_history import _TASK_COLUMNS, _TASK_TYPES
from runtime.workflows.sequential_history import (
    SequentialHistory, _ReadBudget, _require, read_sequential_history,
)

Cell: TypeAlias = str | bytes | int | float | None
_RESULT_COLUMNS = ('id', 'task_id', 'agent', 'session_id', 'status')
_RESULT_TYPES = (('integer',), ('text',), ('text',), ('text',), ('text',))
_SEQUENTIAL = ('seq_source_waiting', 'seq_carrier', 'seq_visit')


@dataclass(frozen=True)
class MixedTaskHistory:
    """Exact native rows and result identities, without decoding callback text.

    Owner entries are (task, kind, owner, instance). Document and old Coding
    IDs retain their original namespace; a sequential run with the same text
    ID cannot join that family. A result lookup binds an original INTEGER
    quartet only; its payload/native terminal/outer validation stays with U5.
    """

    selected_task_id: str
    root_task_ids: tuple[str, ...]
    task_columns: tuple[str, ...]
    tasks: tuple[tuple[Cell, ...], ...]
    owners: tuple[tuple[str, str, str | None, str | None], ...]
    result_columns: tuple[str, ...]
    results: tuple[tuple[Cell, ...], ...]

    def result(self, result_id: int, *, task_id: str, principal: str,
               session_id: str) -> tuple[Cell, ...]:
        _require(type(result_id) is int and result_id > 0, 'mixed_result_identity_invalid')
        found = [row for row in self.results if row[0] == result_id]
        _require(len(found) == 1 and found[0][:4] == (result_id, task_id, principal, session_id),
                 'mixed_result_identity_conflict')
        return found[0]


@dataclass(frozen=True)
class MixedSequentialHistory(SequentialHistory):
    task_history: MixedTaskHistory | None = None


def _rows(conn: sqlite3.Connection, table: str, columns: tuple[str, ...],
          types: tuple[tuple[str, ...], ...], budget: _ReadBudget, *,
          where: str, args: tuple, maximum: int = 8192) -> list[tuple]:
    result = []
    for row in _bounded_rows(conn, table, columns, types, budget,
                             where=where, args=args, maximum=maximum):
        values = row[:len(columns)]
        budget.reserve(512 + 128 * len(columns) + 24 * sum(
            len(value.encode('utf8')) if type(value) is str else len(value)
            for value in values if type(value) in (str, bytes)))
        result.append(values)
    return result


def _identity(value: object) -> None:
    _require(type(value) is str and bool(value) and '\x00' not in value,
             'mixed_task_identity_invalid')


def close_mixed_history_uncommitted(conn: sqlite3.Connection, *, org_slug: str,
                                    task_id: str, budget: _ReadBudget) -> MixedTaskHistory:
    """Close parent/child and workflow-member edges, INCLUDING terminal paths.

    Internal composition only: the caller has just validated the complete
    combined layout/data. Use its original C allowance and SQL progress hook;
    do not create a budget, transaction, cache or independently trusted subset.
    This is a history inventory, never a proposed live cancellation write set.
    """
    _require(conn.in_transaction, 'mixed_history_requires_snapshot')
    _identity(task_id)
    tasks: dict[str, tuple] = {}
    owners: dict[str, tuple] = {}
    results: list[tuple] = []
    families: set[tuple[bool, str]] = set()
    pending, queued = [task_id], {task_id}
    while pending:
        budget.step()
        key = pending.pop()
        queued.remove(key)
        _require(len(tasks) < 512, 'mixed_task_limit_exceeded')
        found = _rows(conn, 'tasks', _TASK_COLUMNS, _TASK_TYPES, budget,
                      where='id=?', args=(key,), maximum=1)
        _require(len(found) == 1, 'mixed_task_missing')
        row = found[0]
        task = dict(zip(_TASK_COLUMNS, row, strict=True))
        _identity(task['id'])
        TaskStatus(task['status'])
        if task['block_kind'] is not None:
            BlockKind(task['block_kind'])
        tasks[key] = row
        marks = _rows(conn, 'workflow_task_ownership',
            ('org_slug', 'version', 'kind', 'owner_id'),
            (('text',), ('integer',), ('text',), ('text', 'null')), budget,
            where='task_id=?', args=(key,), maximum=1)
        _require(len(marks) == 1 and marks[0][0] == org_slug, 'mixed_owner_missing')
        # Stable integer ordinals let the existing pre-fetch cell gate bound
        # this view (which has no rowid). A fifth binding cannot be hidden.
        bindings = _structure_cells(conn,
            'SELECT row_number() OVER (ORDER BY relation,owner_id,instance_id) AS ordinal,'
            'relation,owner_id,instance_id FROM workflow_task_owner_bindings WHERE task_id=? LIMIT 5',
            ('relation', 'owner_id', 'instance_id'), ('ordinal',), budget, (key,))
        _require(len(bindings) <= 3, 'mixed_owner_conflict')
        for binding in bindings:
            for value in binding:
                _identity(value)
        kind, owner, instance = _binding_owner(key, bindings)
        _require(marks[0][1:] == (2 if kind in _SEQUENTIAL else 1, kind, owner),
                 'mixed_owner_conflict')
        owners[key] = (key, kind, owner, instance)
        adjacent = []
        if task['parent_task_id'] is not None:
            _identity(task['parent_task_id'])
            adjacent.append(task['parent_task_id'])
        adjacent.extend(child[0] for child in _rows(conn, 'tasks', ('id',), (('text',),),
            budget, where='parent_task_id=?', args=(key,), maximum=512))
        if instance is not None:
            family = (kind in _SEQUENTIAL, instance)
            if family not in families:
                families.add(family)
                predicate = 'IN' if family[0] else 'NOT IN'
                adjacent.extend(member[0] for member in _rows(conn, 'tasks', ('id',), (('text',),),
                    budget, where='id IN (SELECT task_id FROM workflow_task_owner_bindings '
                        f'WHERE instance_id=? AND relation {predicate} (?,?,?))',
                    args=(instance, *_SEQUENTIAL), maximum=512))
        for adjacent_id in adjacent:
            budget.step()
            _identity(adjacent_id)
            if adjacent_id not in tasks and adjacent_id not in queued:
                pending.append(adjacent_id)
                queued.add(adjacent_id)
                _require(len(tasks) + len(pending) <= 512, 'mixed_task_limit_exceeded')
        callbacks = _rows(conn, 'task_results', _RESULT_COLUMNS, _RESULT_TYPES, budget,
                          where='task_id=?', args=(key,))
        for callback in callbacks:
            _require(type(callback[0]) is int and callback[0] > 0 and callback[1] == key,
                     'mixed_result_identity_invalid')
            _identity(callback[2])
            _identity(callback[3])
        results.extend(callbacks)
    # A visited set prevents repeated work, but must not hide a parent cycle.
    parents = {key: row[_TASK_COLUMNS.index('parent_task_id')] for key, row in tasks.items()}
    settled: set[str] = set()
    roots = set()
    for key in sorted(tasks):
        path = set()
        while key is not None and key not in settled:
            budget.step()
            _require(key in tasks and key not in path, 'mixed_ancestry_conflict')
            path.add(key)
            parent = parents[key]
            if parent is None:
                roots.add(key)
            key = parent
        settled.update(path)
    budget.work(3 * len(tasks) * max(1, len(tasks).bit_length())
                + len(results) * max(1, len(results).bit_length()))
    budget.reserve(128 * (len(tasks) + len(results)))
    history = MixedTaskHistory(task_id, tuple(sorted(roots)), _TASK_COLUMNS,
        tuple(tasks[key] for key in sorted(tasks)), tuple(owners[key] for key in sorted(owners)),
        _RESULT_COLUMNS, tuple(sorted(results)))
    budget.check()
    return history


def read_selected_mixed_history_uncommitted(conn: sqlite3.Connection, *, org_slug: str,
                                           task_id: str, deadline: float | None = None) -> MixedTaskHistory:
    """Inert selected read through the full oracle; no incremental trust path.

    Caller owns its connection/lock/read transaction. Complete D2 A/B/C/L caps
    apply once, with the same original deadline. This is intentionally a full
    compatibility read, not the engine's narrower pre-COMMIT lifecycle validator.
    """
    _require(conn.in_transaction, 'mixed_history_requires_snapshot')
    history = read_sequential_history(conn, expected_org_slug=org_slug,
                                      task_id=task_id, deadline=deadline)
    _require(isinstance(history, MixedSequentialHistory) and history.task_history is not None,
             'mixed_history_unavailable')
    return history.task_history

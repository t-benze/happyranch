"""Unattached companion ownership@2 reference/readers; no adoption writer.

Historical table/view literals and their v1 producers are unchanged. Installing
this companion requires the future explicit U2b transaction; new source adoption
belongs exclusively to the future admitted U3 engine writer.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from runtime.infrastructure.workflow_task_ownership import (
    _binding_owner, _selected_sequential_owner, binding_view_ddl, selected_owner,
)

from runtime.infrastructure.workflow_schema import _candidate_reserve
from runtime.workflows.coding_authority_history import _Budget, _bounded_rows


SEQUENTIAL_KINDS = ("seq_source_waiting", "seq_carrier", "seq_visit")

SEQUENTIAL_OWNERSHIP_DDL = """\
CREATE TABLE workflow_task_ownership (
  task_id TEXT NOT NULL PRIMARY KEY REFERENCES tasks(id),
  org_slug TEXT NOT NULL CHECK(typeof(org_slug)='text' AND length(org_slug)>0),
  version INTEGER NOT NULL CHECK(typeof(version)='integer' AND version IN (1,2)),
  kind TEXT NOT NULL CHECK(
    (version=1 AND kind IN ('legacy','draft','f5','document','coding_source_waiting','coding_carrier','coding_attempt')) OR
    (version=2 AND kind IN ('seq_source_waiting','seq_carrier','seq_visit'))),
  owner_id TEXT,
  CHECK((kind='legacy' AND owner_id IS NULL) OR
        (kind!='legacy' AND typeof(owner_id)='text' AND length(owner_id)>0))
);
"""


def sequential_binding_view_ddl(layout: str, *, coding_extension: bool) -> str:
    """Keep every original relation literal, then append three new relations."""
    original = binding_view_ddl(layout, coding_extension=coding_extension)
    return original.removesuffix(';') + " UNION ALL " + " UNION ALL ".join((
        "SELECT source_task_id,'seq_source_waiting',id,id FROM workflow_seq_runs",
        "SELECT carrier_task_id,'seq_carrier',id,id FROM workflow_seq_runs",
        "SELECT task_id,'seq_visit',id,run_id FROM workflow_seq_visits",
    )) + ';'


def _binding_rows(conn: sqlite3.Connection, budget: _Budget) -> Iterator[tuple]:
    """The retained finite reader's gated projection, adapted to a rowid-less view.

    Preflight scalar metadata, then stream payload once. A sentinel preserves
    invalid/oversize rows as refusals. No unbounded inventory or per-task rescan.
    """
    names = ('task_id', 'relation', 'owner_id', 'instance_id')
    if not conn.in_transaction:
        raise ValueError('workflow_seq_ownership_requires_snapshot')
    # First observe only bounded scalar metadata, reserving the COMPLETE view's
    # payload/copies before any payload fetch in the same snapshot. The second
    # traversal is global, never repeated for each task/run.
    scalar = ','.join(f'typeof({name}),length(CAST({name} AS BLOB))' for name in names)
    cursor = conn.cursor()
    cursor.row_factory = None
    count = total = 0
    try:
        cursor.execute('SELECT ' + scalar + ' FROM workflow_task_owner_bindings LIMIT ?',
                       (8193 - budget.accepted_rows,))
        for row in cursor:
            budget.step()
            budget.candidate()
            kinds, sizes = row[::2], row[1::2]
            if any(kind != 'text' for kind in kinds) or any(
                    type(size) is not int or not 0 < size <= 1048576 for size in sizes):
                raise ValueError('workflow_seq_ownership_bounds')
            size = sum(sizes)
            _candidate_reserve(budget, 512 + 25 * size)
            total += size
            count += 1
        valid = ' AND '.join(f"typeof({name})='text' AND length(CAST({name} AS BLOB))<=1048576"
                             for name in names)
        query = ('WITH gated AS (SELECT task_id,relation,owner_id,instance_id,('
            + valid + ') AS valid FROM workflow_task_owner_bindings) SELECT valid,'
            + ','.join(f'CASE WHEN valid THEN {name} ELSE NULL END' for name in names)
            + ' FROM gated LIMIT ?')
        cursor.execute(query, (count + 1,))
        fetched = acquired = 0
        for row in cursor:
            budget.step()
            budget.candidate()
            if row[0] != 1 or any(type(value) is not str for value in row[1:]):
                raise ValueError('workflow_seq_ownership_storage_invalid')
            fetched += 1
            acquired += sum(len(value.encode('utf8')) for value in row[1:])
            if fetched > count or acquired > total:
                raise ValueError('workflow_seq_ownership_inventory_changed')
            yield tuple(row[1:])
        if fetched != count or acquired != total:
            raise ValueError('workflow_seq_ownership_inventory_changed')
        budget.step()
    finally:
        cursor.close()


def validate_sequential_ownership(conn: sqlite3.Connection, *, org_slug: str,
                                  budget: _Budget, runs: dict[str, dict],
                                  visits: dict[str, dict]) -> None:
    """Complete v1/v2 inventory on the enclosing history's original allowance."""
    selected: dict[str, list[tuple]] = {}
    for task, kind, owner, instance in _binding_rows(conn, budget):
        if any(not value or '\x00' in value for value in (task, kind, owner, instance)):
            raise ValueError('workflow_seq_ownership_invalid_binding')
        selected.setdefault(task, []).append((kind, owner, instance))
    expected = {}
    for task, bindings in selected.items():
        budget.step()
        expected[task] = _binding_owner(task, bindings)
    tasks = {}
    for row in _bounded_rows(conn, 'tasks',
            ('id', 'parent_task_id', 'assigned_agent', 'current_session_id'),
            (('text',), ('text', 'null'), ('text', 'null'), ('text', 'null')), budget):
        _candidate_reserve(budget, 512 + 24 * sum(len(value.encode('utf8')) for value in row[:4]
                                               if type(value) is str))
        tasks[row[0]] = row[:4]
    actual = set()
    for row in _bounded_rows(conn, 'workflow_task_ownership',
            ('task_id', 'org_slug', 'version', 'kind', 'owner_id'),
            (('text',), ('text',), ('integer',), ('text',), ('text', 'null')), budget):
        _candidate_reserve(budget, 512 + 24 * sum(len(value.encode('utf8')) for value in row[:5]
                                               if type(value) is str))
        task, org, version, kind, owner = row[:5]
        required = 2 if kind in SEQUENTIAL_KINDS else 1
        binding = expected.get(task, ('legacy', None, None))
        if (task in actual or org != org_slug or version != required
                or (kind, owner) != binding[:2]):
            raise ValueError('workflow_seq_ownership_invalid')
        actual.add(task)
        if required == 2:
            native, run = tasks.get(task), runs.get(binding[2])
            if native is None or run is None:
                raise ValueError('workflow_seq_ownership_reference_missing')
            if kind == 'seq_source_waiting':
                valid = task == run['source_task_id'] and native[2] == run['source_principal']
            elif kind == 'seq_carrier':
                valid = (task == run['carrier_task_id'] and native[1] == run['source_task_id']
                         and native[3] is None)
            else:
                visit = visits.get(owner)
                valid = (visit is not None and visit['task_id'] == task and visit['run_id'] == run['id']
                         and native[1] == run['carrier_task_id'] and native[2] == visit['principal'])
            if not valid:
                raise ValueError('workflow_seq_ownership_native_conflict')
    if actual != tasks.keys() or expected.keys() - tasks.keys():
        raise ValueError('workflow_seq_ownership_incomplete')
    budget.step()


def read_sequential_owner(conn: sqlite3.Connection, *, task_id: str,
                          org_slug: str) -> tuple[str, str | None, str | None]:
    """Selected scalar consistency only; never allocation/execution authority.

    The caller owns a validated complete layout and a read snapshot. Ordinary
    discrimination uses the installed reader and holds new marks for the future
    engine. Old marks use their existing selected reader without relabeling.
    """
    if not conn.in_transaction:
        raise ValueError('workflow_seq_ownership_requires_snapshot')
    mark = conn.execute('SELECT org_slug,version,kind,owner_id FROM workflow_task_ownership '
                        'WHERE task_id=?', (task_id,)).fetchone()
    if mark is None or mark[0] != org_slug or type(mark[1]) is not int:
        raise ValueError('workflow_seq_ownership_missing')
    if mark[1] == 1:
        return selected_owner(conn, task_id, org_slug)
    if mark[1] != 2 or mark[2] not in SEQUENTIAL_KINDS:
        raise ValueError('workflow_seq_ownership_unsupported')
    return _selected_sequential_owner(conn, task_id, org_slug)

"""Unattached companion ownership@2 reference/readers; no adoption writer.

Historical table/view literals and their v1 producers are unchanged. Installing
this companion requires the future explicit U2b transaction; new source adoption
belongs exclusively to the future admitted U3 engine writer.
"""
from __future__ import annotations

import sqlite3

from runtime.infrastructure.workflow_task_ownership import (
    _binding_owner, _selected_sequential_owner, binding_view_ddl, selected_owner,
)

from runtime.infrastructure.workflow_schema import _candidate_reserve
from runtime.workflows.coding_authority_history import _Budget, _VALUE_LIMIT, _bounded_rows, _check


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


def validate_sequential_ownership(conn: sqlite3.Connection, *, org_slug: str,
                                  budget: _Budget, runs: dict[str, dict],
                                  visits: dict[str, dict]) -> None:
    """Validate run-bound ownership using authenticated relations and keyed rows."""
    if not conn.in_transaction:
        raise ValueError('workflow_seq_ownership_requires_snapshot')
    selected: dict[str, list[tuple]] = {}
    bindings = []
    for run in sorted(runs.values(), key=lambda row: row['id']):
        bindings.extend(((run['source_task_id'], 'seq_source_waiting', run['id'], run['id']),
                         (run['carrier_task_id'], 'seq_carrier', run['id'], run['id'])))
    for visit in sorted(visits.values(), key=lambda row: row['id']):
        bindings.append((visit['task_id'], 'seq_visit', visit['id'], visit['run_id']))
    for task, kind, owner, instance in bindings:
        budget.step()
        budget.candidate()
        values = (task, kind, owner, instance)
        if any(type(value) is not str or not 0 < len(value.encode('utf8')) <= 1048576
               for value in values):
            raise ValueError('workflow_seq_ownership_bounds')
        _candidate_reserve(budget, 512 + 25 * sum(len(value.encode('utf8')) for value in values))
        if any(not value or '\x00' in value for value in values):
            raise ValueError('workflow_seq_ownership_invalid_binding')
        selected.setdefault(task, []).append((kind, owner, instance))
    expected = {}
    for task, relations in selected.items():
        budget.step()
        expected[task] = _binding_owner(task, relations)
    for task in sorted(expected):
        native = None
        for row in _bounded_rows(conn, 'tasks',
                ('id', 'parent_task_id', 'assigned_agent', 'current_session_id'),
                (('text',), ('text', 'null'), ('text', 'null'), ('text', 'null')), budget,
                where='id=?', args=(task,), maximum=1):
            _candidate_reserve(budget, 512 + 24 * sum(len(value.encode('utf8')) for value in row[:4]
                                                   if type(value) is str))
            native = row[:4]
        mark = None
        for row in _bounded_rows(conn, 'workflow_task_ownership',
                ('task_id', 'org_slug', 'version', 'kind', 'owner_id'),
                (('text',), ('text',), ('integer',), ('text',), ('text', 'null')), budget,
                where='task_id=?', args=(task,), maximum=1):
            _candidate_reserve(budget, 512 + 24 * sum(len(value.encode('utf8')) for value in row[:5]
                                                   if type(value) is str))
            mark = row[:5]
        if mark is None:
            raise ValueError('workflow_seq_ownership_incomplete')
        _, org, version, kind, owner = mark
        required = 2 if kind in SEQUENTIAL_KINDS else 1
        binding = expected[task]
        if org != org_slug or version != required or (kind, owner) != binding[:2]:
            raise ValueError('workflow_seq_ownership_invalid')
        if required == 2:
            run = runs.get(binding[2])
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
    budget.step()


def _gate(columns: tuple[str, ...], allowed: tuple[tuple[str, ...], ...]) -> str:
    """The retained per-cell gate, without the removed acquisition's resource term."""
    gates = []
    for column, choices in zip(columns, allowed, strict=True):
        name = '"' + column.replace('"', '""') + '"'
        kinds = ','.join("'" + kind + "'" for kind in choices)
        gates.append(f"(typeof({name}) IN ({kinds}) AND (typeof({name}) NOT IN ('text','blob') "
                     f"OR length(CAST({name} AS BLOB))<={_VALUE_LIMIT}))")
    return ' AND '.join(gates)


def _candidates(columns: tuple[str, ...]) -> str:
    """Only rowid-table text needing Python decoding; ASCII payload stays in SQL."""
    terms = []
    for column in columns:
        name = '"' + column.replace('"', '""') + '"'
        terms.append(f"(typeof({name})='text' AND (instr(CAST({name} AS BLOB),X'00')>0 "
                     f"OR {name} GLOB '*[^ -~]*'))")
    return ' OR '.join(terms)


def validate_sequential_ownership_guards(conn: sqlite3.Connection, *, budget: _Budget) -> None:
    """Preserve non-run type/size/NUL/decode guards under the org-wide SQL meter.

    Binding text is decoded only by the unchanged retained legacy inventory.
    These predicate statements acquire no rows unless a defect is present.
    """
    if not conn.in_transaction:
        raise ValueError('workflow_seq_ownership_requires_snapshot')
    names = ('task_id', 'relation', 'owner_id', 'instance_id')
    valid = ' AND '.join(f"typeof({name})='text' AND length(CAST({name} AS BLOB)) "
                         'BETWEEN 1 AND 1048576' for name in names)
    budget.step()
    if conn.execute('SELECT 1 FROM workflow_task_owner_bindings WHERE NOT ('
                    + valid + ') LIMIT 1').fetchone() is not None:
        raise ValueError('workflow_seq_ownership_bounds')
    budget.step()
    if conn.execute('SELECT 1 FROM workflow_task_owner_bindings WHERE '
                    + ' OR '.join(f"instr(CAST({name} AS BLOB),X'00')>0" for name in names)
                    + ' LIMIT 1').fetchone() is not None:
        raise ValueError('workflow_seq_ownership_invalid_binding')
    for table, columns, allowed in (
        ('tasks', ('id', 'parent_task_id', 'assigned_agent', 'current_session_id'),
         (('text',), ('text', 'null'), ('text', 'null'), ('text', 'null'))),
        ('workflow_task_ownership', ('task_id', 'org_slug', 'version', 'kind', 'owner_id'),
         (('text',), ('text',), ('integer',), ('text',), ('text', 'null'))),
    ):
        try:
            budget.step()
            _check(conn.execute('SELECT 1 FROM ' + table + ' WHERE NOT ('
                                + _gate(columns, allowed) + ') LIMIT 1').fetchone() is None)
            budget.step()
            text_columns = tuple(name for name, choices in zip(columns, allowed, strict=True)
                                 if 'text' in choices)
            for _ in _bounded_rows(conn, table, columns, allowed, budget,
                                   where=_candidates(text_columns)):
                # _bounded_rows charges candidate rows, native bytes and decode work.
                pass
        except (sqlite3.Error, UnicodeError, OverflowError):
            _check(False)


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

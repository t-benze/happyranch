"""Unattached companion ownership@2 reference/readers; no adoption writer.

Historical table/view literals and their v1 producers are unchanged. Installing
this companion requires the future explicit U2b transaction; new source adoption
belongs exclusively to the future admitted U3 engine writer.
"""
from __future__ import annotations

import sqlite3

from runtime.infrastructure.workflow_task_ownership import (
    _binding_owner, _inventory, binding_view_ddl, selected_owner,
)


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


def validate_sequential_ownership(conn: sqlite3.Connection, *, org_slug: str) -> None:
    """Complete mark inventory, with independent v1/v2 relation selection."""
    expected = _inventory(conn)
    tasks = {row[0] for row in conn.execute('SELECT id FROM tasks')}
    actual = set()
    for task, org, version, kind, owner in conn.execute('SELECT * FROM workflow_task_ownership'):
        required = 2 if kind in SEQUENTIAL_KINDS else 1
        if (task in actual or org != org_slug or type(version) is not int or version != required
                or (kind, owner) != expected.get(task, ('legacy', None, None))[:2]):
            raise ValueError('workflow_seq_ownership_invalid')
        actual.add(task)
        if required == 2:
            read_sequential_owner(conn, task_id=task, org_slug=org_slug)
    if actual != tasks or expected.keys() - tasks:
        raise ValueError('workflow_seq_ownership_incomplete')


def read_sequential_owner(conn: sqlite3.Connection, *, task_id: str,
                          org_slug: str) -> tuple[str, str | None, str | None]:
    """Selected scalar consistency only; never allocation/execution authority.

    The caller owns a validated complete layout and a read snapshot. Ordinary
    discrimination must not call this unmounted reader until runtime integration
    is reviewed. Old marks use their existing selected reader without relabeling.
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
    bindings = [tuple(row) for row in conn.execute(
        'SELECT relation,owner_id,instance_id FROM workflow_task_owner_bindings WHERE task_id=?', (task_id,))]
    kind, owner, run_id = _binding_owner(task_id, bindings)
    if (kind, owner) != tuple(mark[2:]):
        raise ValueError('workflow_seq_ownership_relation_conflict')
    run = conn.execute('SELECT source_task_id,carrier_task_id,state,source_principal '
                       'FROM workflow_seq_runs WHERE id=? AND org_slug=?', (run_id, org_slug)).fetchone()
    task = conn.execute('SELECT parent_task_id,assigned_agent,current_session_id FROM tasks WHERE id=?',
                        (task_id,)).fetchone()
    if run is None or task is None:
        raise ValueError('workflow_seq_ownership_reference_missing')
    if kind == 'seq_source_waiting':
        valid = task_id == run[0] and task[1] == run[3]
        state = run[2]
    elif kind == 'seq_carrier':
        valid = task_id == run[1] and task[0] == run[0] and task[2] is None
        state = run[2]
    else:
        visit = conn.execute('SELECT principal,state FROM workflow_seq_visits '
                             'WHERE id=? AND run_id=? AND task_id=?', (owner, run_id, task_id)).fetchone()
        valid = visit is not None and task[0] == run[1] and task[1] == visit[0]
        state = None if visit is None else visit[1]
    if not valid:
        raise ValueError('workflow_seq_ownership_native_conflict')
    return kind, owner, state

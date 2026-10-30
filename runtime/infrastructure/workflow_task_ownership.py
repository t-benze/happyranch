"""Org-owned creation-time routing marks, separate from frozen native task rows.

The table is created and populated in one compatibility transaction. Its absence
or a missing row never means ordinary. The literal view exposes only scalar
bindings so selected-task consistency needs no schema or history reconstruction.
Neither a mark nor this view establishes execution authority.
"""
from __future__ import annotations

import sqlite3
from typing import Any


OWNERSHIP_DDL = """\
CREATE TABLE workflow_task_ownership (
  task_id TEXT NOT NULL PRIMARY KEY REFERENCES tasks(id),
  org_slug TEXT NOT NULL CHECK(typeof(org_slug)='text' AND length(org_slug)>0),
  version INTEGER NOT NULL CHECK(version=1),
  kind TEXT NOT NULL CHECK(kind IN ('legacy','draft','f5','document','coding_source_waiting','coding_carrier','coding_attempt')),
  owner_id TEXT,
  CHECK((kind='legacy' AND owner_id IS NULL) OR
        (kind!='legacy' AND typeof(owner_id)='text' AND length(owner_id)>0))
);
"""


def binding_view_ddl(layout: str, *, coding_extension: bool) -> str:
    """Independent literal relations for each supported complete schema variant."""
    branches = [
        "SELECT root_task_id AS task_id,'root' AS relation,id AS owner_id,id AS instance_id FROM workflow_instances",
        "SELECT task_id,'link',instance_id,instance_id FROM workflow_instance_tasks",
        "SELECT task_id,'f5',request_id,instance_id FROM workflow_request_task_bridges",
    ]
    if layout != 'F':
        branches.append("SELECT task_id,'draft',id,instance_id FROM workflow_draft_dispatch_intents")
    if coding_extension or layout == 'J':
        branches.extend((
            "SELECT a.task_id,'coding_attempt',a.id,a.instance_id FROM workflow_coding_attempts a",
            "SELECT i.root_task_id,'coding_carrier',r.instance_id,r.instance_id "
            "FROM workflow_coding_runs r JOIN workflow_instances i ON i.id=r.instance_id",
            "SELECT t.parent_task_id,'coding_source_waiting',r.instance_id,r.instance_id "
            "FROM tasks t JOIN workflow_instances i ON i.root_task_id=t.id "
            "JOIN workflow_coding_runs r ON r.instance_id=i.id",
        ))
    return 'CREATE VIEW workflow_task_owner_bindings AS ' + ' UNION ALL '.join(branches) + ';'


def ownership_storage_present(conn: sqlite3.Connection) -> bool:
    """Boundary-only observation; complete schema comparison checks both objects."""
    return conn.execute("SELECT 1 FROM sqlite_schema WHERE name IN "
                        "('workflow_task_ownership','workflow_task_owner_bindings') LIMIT 1").fetchone() is not None


def refresh_binding_view(conn: sqlite3.Connection, layout: str, *, coding_extension: bool) -> None:
    """Only a validated compatibility/migration writer may change this view."""
    if ownership_storage_present(conn):
        conn.execute('DROP VIEW workflow_task_owner_bindings')
        conn.execute(binding_view_ddl(layout, coding_extension=coding_extension))


def _binding_owner(task_id: str, bindings: list[tuple]) -> tuple[str, str | None, str | None]:
    direct = [(kind, owner, instance) for kind, owner, instance in bindings if kind not in ('root', 'link')]
    if direct:
        if len(direct) != 1:
            raise ValueError('workflow_task_ownership_dual:' + task_id)
        kind, owner, instance = direct[0]
        if any(ref != instance for _, _, ref in bindings):
            raise ValueError('workflow_task_ownership_binding_conflict:' + task_id)
        return kind, owner, instance
    instances = {instance for _, _, instance in bindings}
    if len(instances) > 1:
        raise ValueError('workflow_task_ownership_dual:' + task_id)
    if instances:
        instance = next(iter(instances))
        return 'document', instance, instance
    return 'legacy', None, None


def _inventory(conn: sqlite3.Connection) -> dict[str, tuple[str, str | None, str | None]]:
    selected: dict[str, list[tuple]] = {}
    for task, kind, owner, instance in conn.execute('SELECT * FROM workflow_task_owner_bindings'):
        if any(type(value) is not str or not value for value in (task, kind, owner, instance)):
            raise ValueError('workflow_task_ownership_invalid_binding')
        selected.setdefault(task, []).append((kind, owner, instance))
    return {task: _binding_owner(task, bindings) for task, bindings in selected.items()}


def validate_ownership_data(conn: sqlite3.Connection, *, org_slug: str | None) -> None:
    """Complete boundary reader; never entered by routine discrimination."""
    expected = _inventory(conn)
    tasks = {row[0] for row in conn.execute('SELECT id FROM tasks')}
    if expected.keys() - tasks:
        raise ValueError('workflow_task_ownership_task_missing:' + sorted(expected.keys() - tasks)[0])
    actual = {}
    for task, org, version, kind, owner in conn.execute('SELECT * FROM workflow_task_ownership'):
        if (task in actual or type(version) is not int or version != 1
                or type(org) is not str or not org or (org_slug is not None and org != org_slug)
                or (kind, owner) != expected.get(task, ('legacy', None, None))[:2]):
            raise ValueError('workflow_task_ownership_invalid:' + str(task))
        actual[task] = org
    if actual.keys() != tasks or len(set(actual.values())) > 1:
        raise ValueError('workflow_task_ownership_incomplete')


def install_task_ownership(db: Any, *, org_slug: str) -> None:
    """Once-only controlled org initialization, before producers attach.

    Full existing readers authenticate all layouts/bindings, including draft
    intents and coding source/carrier/old attempts. Empty ownership is positive
    evidence from that inventory, not absence of an optional table or an audit.
    A known rollback leaves storage absent. Existing storage is validated, never
    repaired or backfilled again. Publication follows successful COMMIT only.
    """
    from runtime.infrastructure.workflow_schema import _read_workflow_schema
    with db.workflow_schema_transaction() as conn:
        layout, coding = _read_workflow_schema(conn, expected_org_slug=org_slug)
        if not ownership_storage_present(conn):
            conn.execute(OWNERSHIP_DDL)
            conn.execute(binding_view_ddl(layout, coding_extension=coding is not None))
            expected = _inventory(conn)
            if coding is not None:
                observed = {task: (kind, instance) for task, (kind, _, instance) in expected.items()
                            if kind.startswith('coding_')}
                if observed != coding.queued.owners:
                    raise ValueError('workflow_task_ownership_coding_inventory_conflict')
            # Every task receives an explicit record, including ordinary tasks.
            conn.execute("INSERT INTO workflow_task_ownership "
                         "SELECT id,?,1,'legacy',NULL FROM tasks", (org_slug,))
            for task, (kind, owner, _) in expected.items():
                changed = conn.execute('UPDATE workflow_task_ownership SET kind=?,owner_id=? WHERE task_id=?',
                                       (kind, owner, task))
                if changed.rowcount != 1:
                    raise ValueError('workflow_task_ownership_task_missing:' + task)
        _read_workflow_schema(conn, expected_org_slug=org_slug)
    db._task_ownership_org = org_slug


def insert_owner_uncommitted(db: Any, task_id: str, *, ownership: tuple[str, str] | None = None) -> None:
    """Private daemon producer input; never a TaskRecord/API/model field."""
    conn = db._conn
    if not conn.in_transaction:
        raise ValueError('workflow_task_ownership_requires_transaction')
    org = getattr(db, '_task_ownership_org', None)
    if org is None:
        # Generic Database/runtime-audit construction remains workflow-free.
        # A detached org DB may not silently insert an unmarked ordinary row.
        if ownership is not None or ownership_storage_present(conn):
            raise ValueError('workflow_task_ownership_org_unattached')
        return
    kind, owner = ('legacy', None) if ownership is None else ownership
    if kind == 'legacy' and ownership is not None:
        raise ValueError('workflow_task_ownership_invalid_producer')
    conn.execute('INSERT INTO workflow_task_ownership VALUES (?,?,1,?,?)', (task_id, org, kind, owner))


def adopt_coding_source_uncommitted(db: Any, task_id: str, instance_id: str) -> None:
    """The admitted start writer alone changes an existing ordinary source."""
    if not db._conn.in_transaction:
        raise ValueError('workflow_task_ownership_requires_transaction')
    changed = db._conn.execute("UPDATE workflow_task_ownership SET kind='coding_source_waiting',owner_id=? "
        "WHERE task_id=? AND org_slug=? AND version=1 AND kind='legacy' AND owner_id IS NULL",
        (instance_id, task_id, getattr(db, '_task_ownership_org', None)))
    if changed.rowcount != 1:
        raise ValueError('workflow_task_ownership_source_conflict:' + task_id)


def selected_owner(conn: sqlite3.Connection, task_id: str, org_slug: str | None) -> tuple[str, str | None, str | None]:
    """Indexed selected mark plus scalar consistency, inside the caller snapshot."""
    row = conn.execute('SELECT o.org_slug,o.version,o.kind,o.owner_id,t.assigned_agent,t.parent_task_id,'
        't.status,t.current_session_id FROM workflow_task_ownership o JOIN tasks t ON t.id=o.task_id '
        'WHERE o.task_id=?', (task_id,)).fetchone()
    if row is None or org_slug is None or row[0] != org_slug or type(row[1]) is not int or row[1] != 1:
        raise ValueError('workflow_task_ownership_missing_or_unsupported')
    org, _, kind, owner, agent, parent, status, session = row
    bindings = [tuple(r) for r in conn.execute('SELECT relation,owner_id,instance_id '
                                             'FROM workflow_task_owner_bindings WHERE task_id=? LIMIT 4', (task_id,))]
    expected_kind, expected_owner, instance = _binding_owner(task_id, bindings)
    if (kind, owner) != (expected_kind, expected_owner):
        raise ValueError('workflow_task_ownership_binding_conflict:' + task_id)
    if kind == 'legacy':
        return kind, None, None
    if type(owner) is not str or not owner:
        raise ValueError('workflow_task_ownership_invalid_reference')
    if kind == 'draft':
        intent = conn.execute('SELECT assigned_principal,state,admission_kind,admission_principal,operation_key,'
            'request_digest,activation_id FROM workflow_draft_dispatch_intents WHERE id=? AND task_id=?', (owner, task_id)).fetchone()
        if intent is None or intent[0] != agent or (intent[1] == 'queued' and (status != 'pending' or session is not None)):
            raise ValueError('workflow_task_ownership_draft_conflict')
        if intent[2] == 'revision':
            operation = conn.execute('SELECT 1 FROM workflow_document_revision_operations WHERE draft_intent_id=? '
                'AND org_slug=? AND principal=? AND operation_key=? AND request_digest=?',
                (owner, org, intent[3], intent[4], intent[5])).fetchone()
        else:
            operation = conn.execute('SELECT 1 FROM workflow_activation_operations WHERE activation_id=? '
                'AND org_slug=? AND principal=? AND operation_key=? AND request_digest=?',
                (intent[6], org, intent[3], intent[4], intent[5])).fetchone()
        if operation is None:
            raise ValueError('workflow_task_ownership_draft_operation_missing')
        return kind, owner, intent[1]
    if kind == 'f5':
        bridge = conn.execute('SELECT b.state FROM workflow_request_task_bridges b '
            'JOIN workflow_dispatch_operations o ON o.id=b.operation_id AND o.instance_id=b.instance_id AND o.request_id=b.request_id '
            'JOIN workflow_dispatch_outbox x ON x.operation_id=o.id AND x.request_id=o.request_id '
            'JOIN workflow_review_requests q ON q.id=b.request_id AND q.principal=b.assigned_principal '
            'AND q.assignment_generation=b.assignment_generation '
            'JOIN workflow_rounds r ON r.id=q.round_id AND r.id=o.round_id AND r.instance_id=b.instance_id '
            'WHERE b.request_id=? AND b.task_id=? AND b.assigned_principal=? AND o.org_slug=?',
            (owner, task_id, agent, org)).fetchall()
        if len(bridge) != 1:
            raise ValueError('workflow_task_ownership_review_conflict')
        return kind, None, bridge[0][0]
    if kind.startswith('coding_'):
        run = conn.execute('SELECT i.root_task_id,t.parent_task_id,a.state FROM workflow_coding_runs r '
            'JOIN workflow_instances i ON i.id=r.instance_id JOIN tasks t ON t.id=i.root_task_id '
            'JOIN workflow_coding_mappings m ON m.id=r.mapping_id AND m.org_slug=? '
            'JOIN workflow_coding_attempts a ON a.id=r.current_attempt_id AND a.instance_id=r.instance_id '
            'WHERE r.instance_id=?', (org, instance)).fetchone()
        if run is None:
            raise ValueError('workflow_task_ownership_run_missing')
        if kind == 'coding_attempt':
            attempt = conn.execute('SELECT principal FROM workflow_coding_attempts '
                                  'WHERE id=? AND task_id=? AND instance_id=?', (owner, task_id, instance)).fetchone()
            if attempt is None or attempt[0] != agent or parent != run[0]:
                raise ValueError('workflow_task_ownership_attempt_conflict')
        elif (kind == 'coding_carrier' and task_id != run[0]) or (kind == 'coding_source_waiting' and task_id != run[1]):
            raise ValueError('workflow_task_ownership_source_conflict')
        return kind, instance, run[2]
    # Historical document-only roots/links remain owned and recovery-held.
    if kind == 'document':
        return 'reconciliation_required', owner, None
    raise ValueError('workflow_task_ownership_unsupported')

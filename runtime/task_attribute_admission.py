"""Creation-domain composition shared by explicit task producers (PR3/PR4).

Caller holds the org async lock, move gate and DB RLock, owns BEGIN IMMEDIATE,
validates the raw per-task map before allocating a task, inserts it, then calls
write_initial_attributes_uncommitted with that same raw map and trusted actor.
Both phases read current definitions; no cached match is admission authority.
Caller rolls back the whole operation on any failure, commits once after other
bindings, then enqueues. This adapter never awaits, commits, enqueues, derives
parent/default values or decides which producers support admission. {} records
positive empty admission; legacy/internal producers must explicitly stay out.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from runtime.infrastructure.database import Database


def validate_initial_attributes(db: Database, values: Mapping[str, Any]) -> list[dict]:
    """Read-only whole-set validation; authoritative only under caller's writer.

    Archived current keys are read only to return PR1's archived_key refusal.
    AttributeValidationError preserves every key/reason; storage errors propagate.
    """
    return db.validate_task_attribute_values(values)


def write_initial_attributes_uncommitted(db: Database, task_id: str, values: Mapping[str, Any], *,
                                         actor_id: str, actor_kind: str) -> None:
    """Compose PR1 admission with PR2 success-audit vocabulary in caller's writer.

    Never ends the caller transaction. The creator must roll back on exceptions,
    including audit failure; the PR1 savepoint alone does not remove its task.
    """
    db._attribute_owned_writer()
    matched = validate_initial_attributes(db, values)
    db._write_task_attributes_initial_uncommitted(task_id, values, actor_id=actor_id, actor_kind=actor_kind)
    for item in matched:
        db.insert_audit_log_uncommitted(task_id, actor_id, "task_attribute_set", {
            "definition_id": item["definition_id"], "definition_version": item["definition_version"],
            "revision": 1, "actor_id": actor_id, "actor_kind": actor_kind,
            "old": {"present": False}, "new": {"present": True, "value": item["value"]},
        })

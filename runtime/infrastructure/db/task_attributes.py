"""Org-local attribute storage. Transport authorization belongs to callers."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from typing import Any
from uuid import uuid4

from runtime.infrastructure.db._shared import _synchronized
from runtime.task_attributes import AttributeValidationError, validate_definition, validate_values, value_errors


class AttributeStoreError(ValueError):
    def __init__(self, code: str, *, definition_id: str | None = None, conflicts: list | None = None) -> None:
        self.code = code
        self.definition_id = definition_id
        self.conflicts = conflicts
        super().__init__(code)


class AttributeConflictError(AttributeStoreError):
    """Stale caller version/revision; no part of the attempted write persists."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _presence(present: bool, value_json: str | None) -> dict:
    return {"present": True, "value": json.loads(value_json)} if present else {"present": False}


def _instant(value: str | None) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(timezone.utc) if parsed.utcoffset() is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _actor(actor_id: str, actor_kind: str, note: str | None = None) -> None:
    if not isinstance(actor_id, str) or not actor_id.strip():
        raise AttributeValidationError({"actor_id": ["required"]})
    if not isinstance(actor_kind, str) or not actor_kind.strip():
        raise AttributeValidationError({"actor_kind": ["required"]})
    if note is not None and not isinstance(note, str):
        raise AttributeValidationError({"note": ["expected_string"]})


def _version(value: int, field: str, minimum: int = 1) -> None:
    if type(value) is not int or value < minimum:
        raise AttributeValidationError({field: ["invalid_version"]})


class TaskAttributesMixin:
    @_synchronized
    def root_attribute_definitions(self, keys: list[str], *, for_export: bool = False) -> list[dict]:
        """Current key resolution; reservations are never aliases.

        Export reads a bounded metadata-only union, including filtered archives.
        The request owner holds coherent_read_view across selection and values.
        """
        if not keys and not for_export:
            return []
        selected = "v.key IN (" + ",".join("?" for _ in keys) + ")" if keys else "0"
        where = f"(d.archived_at IS NULL OR {selected})" if for_export else selected
        rows = self._conn.execute(
            "SELECT v.key,v.definition_id,v.version,v.type,v.name,d.archived_at "
            "FROM task_attribute_definitions d JOIN task_attribute_definition_versions v "
            "ON v.definition_id=d.definition_id AND v.version=d.current_version "
            f"WHERE {where} ORDER BY v.key LIMIT 257", tuple(keys),
        ).fetchall()
        return [{**{k: row[k] for k in ("key", "definition_id", "version", "type", "name")},
                 "archived": row["archived_at"] is not None} for row in rows]

    @_synchronized
    def root_attribute_values(self, task_ids: list[str], definitions: list[dict]) -> dict[str, dict]:
        """Batch current presence only; no initial estimates or history reads."""
        result = {task_id: {d["key"]: {"present": False} for d in definitions} for task_id in task_ids}
        if not task_ids or not definitions:
            return result
        by_id = {d["definition_id"]: d["key"] for d in definitions}
        for offset in range(0, len(task_ids), 200):
            chunk = task_ids[offset:offset + 200]
            rows = self._conn.execute(
                "SELECT task_id,definition_id,present,value_json FROM task_attribute_values "
                f"WHERE task_id IN ({','.join('?' for _ in chunk)}) "
                f"AND definition_id IN ({','.join('?' for _ in by_id)})",
                (*chunk, *by_id),
            )
            for row in rows:
                result[row["task_id"]][by_id[row["definition_id"]]] = _presence(row["present"], row["value_json"])
        return result

    @contextmanager
    def _attribute_writer(self):
        # Never commit/rollback a transaction owned by another domain caller.
        if self._conn.in_transaction:
            raise AttributeStoreError("caller_transaction_active")
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            raise

    @contextmanager
    def _attribute_reader(self):
        owned = not self._conn.in_transaction
        if owned:
            self._conn.execute("BEGIN")
        try:
            yield
        finally:
            if owned:
                self._conn.rollback()  # read snapshot only; caller work is untouched

    def _attribute_definition(self, definition_id: str, version: int | None = None) -> dict:
        row = self._conn.execute(
            "SELECT v.*, d.created_at, d.first_used_at FROM task_attribute_definitions d "
            "JOIN task_attribute_definition_versions v ON v.definition_id=d.definition_id "
            "AND v.version=COALESCE(?,d.current_version) WHERE d.definition_id=?",
            (version, definition_id),
        ).fetchone()
        if row is None:
            raise AttributeStoreError("definition_not_found")
        result = dict(row)
        result["constraints"] = json.loads(result.pop("constraints_json"))
        return result

    def _attribute_definitions(self, include_archived: bool = True) -> list[dict]:
        rows = self._conn.execute(
            "SELECT v.*, d.created_at, d.first_used_at FROM task_attribute_definitions d "
            "JOIN task_attribute_definition_versions v ON v.definition_id=d.definition_id "
            "AND v.version=d.current_version "
            + ("" if include_archived else "WHERE d.archived_at IS NULL ")
            + "ORDER BY v.key, d.definition_id",
        ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["constraints"] = json.loads(item.pop("constraints_json"))
            results.append(item)
        return results

    def _attribute_insert_version(self, definition: dict, at: str) -> None:
        self._conn.execute(
            "INSERT INTO task_attribute_definition_versions "
            "(definition_id,version,key,type,name,description,constraints_json,recorded_at,archived_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (definition["definition_id"], definition["version"], definition["key"], definition["type"],
             definition["name"], definition["description"], _json(definition["constraints"]), at,
             definition.get("archived_at")),
        )

    def _attribute_reserve_key(self, key: str, definition_id: str, at: str) -> None:
        owner = self._conn.execute(
            "SELECT definition_id FROM task_attribute_key_owners WHERE key=?", (key,),
        ).fetchone()
        if owner is not None:
            if owner[0] != definition_id:
                raise AttributeValidationError({"key": ["key_reserved"]})
        else:
            self._conn.execute("INSERT INTO task_attribute_key_owners VALUES (?,?,?)", (key, definition_id, at))

    @_synchronized
    def create_task_attribute_definition(self, *, key: str, type: str, name: str,
                                         description: str | None = None, constraints: dict | None = None) -> dict:
        with self._attribute_writer():
            return self._create_task_attribute_definition_uncommitted(
                key=key, type=type, name=name, description=description, constraints=constraints)

    def _attribute_owned_writer(self) -> None:
        if not self._conn.in_transaction:
            raise AttributeStoreError("owned_transaction_required")

    def _audit_task_attribute_definition_uncommitted(self, operation: str, before: dict | None, after: dict) -> None:
        self._attribute_owned_writer()
        self.insert_audit_log_uncommitted("config:task_attributes", "founder", "org_config_write", {
            "section": "task_attributes", "tiers": ["org"], "operation": operation,
            "definition_id": after["definition_id"],
            "before": None if before is None else {**before, "archived": before["archived_at"] is not None},
            "after": {**after, "archived": after["archived_at"] is not None}, "actor_kind": "founder",
        })

    def _write_task_attribute_changes_uncommitted(self, task_id: str, changes: list[dict], *,
                                                actor_id: str, actor_kind: str) -> None:
        """Caller owns authorization, lock and BEGIN; audit shares the batch.

        Compare every field before scalar validation and before any effect.
        Public PR1 single-field/admission APIs retain their existing contracts.
        """
        self._attribute_owned_writer()
        _actor(actor_id, actor_kind)
        self._attribute_task(task_id)
        if not changes:
            raise AttributeValidationError({"changes": ["required"]})
        seen, definitions, currents, conflicts = set(), [], [], []
        for i, change in enumerate(changes):
            definition_id = change["definition_id"]
            if definition_id in seen:
                raise AttributeValidationError({f"changes.{i}.definition_id": ["duplicate_definition"]})
            seen.add(definition_id)
            _version(change["expected_version"], "expected_version")
            _version(change["expected_revision"], "expected_revision", 0)
            _actor(actor_id, actor_kind, change.get("note"))
            definitions.append(self._attribute_definition(definition_id))
            currents.append(self._attribute_current(task_id, definition_id))
        for change, definition, current in zip(changes, definitions, currents):
            if definition["version"] != change["expected_version"]:
                raise AttributeConflictError("definition_version_conflict", definition_id=definition["definition_id"])
            if definition["archived_at"] is not None:
                raise AttributeStoreError("definition_archived", definition_id=definition["definition_id"])
            if current["revision"] != change["expected_revision"]:
                conflicts.append({"definition_id": definition["definition_id"],
                    "expected_revision": change["expected_revision"], "current_revision": current["revision"]})
        if conflicts:
            raise AttributeConflictError("value_revision_conflict", conflicts=conflicts)
        errors = {}
        for i, (change, definition) in enumerate(zip(changes, definitions)):
            if change["op"] not in ("set", "clear"):
                errors[f"changes.{i}.op"] = ["invalid_operation"]
            elif change["op"] == "set":
                failures = value_errors(definition, change.get("value"))
                if failures:
                    errors[f"changes.{i}.value"] = failures
        if errors:
            raise AttributeValidationError(errors)
        for change, current in zip(changes, currents):
            present = change["op"] == "set"
            new = self._attribute_write_value(task_id, change["definition_id"], change.get("value"),
                present=present, expected_revision=change["expected_revision"], expected_version=change["expected_version"],
                actor_id=actor_id, actor_kind=actor_kind, note=change.get("note"), source="edit" if present else "clear", at=_now())
            payload = {"definition_id": change["definition_id"], "definition_version": change["expected_version"],
                "revision": new["revision"], "actor_id": actor_id, "actor_kind": actor_kind,
                "old": {key: current[key] for key in ("present", "value") if key in current},
                "new": {key: new[key] for key in ("present", "value") if key in new}}
            if change.get("note") is not None:
                payload["note"] = change["note"]
            self.insert_audit_log_uncommitted(task_id, actor_id,
                "task_attribute_set" if present else "task_attribute_cleared", payload)

    @_synchronized
    def get_task_attributes(self, task_id: str) -> list[dict]:
        """Active fields and retained archived task evidence in one snapshot."""
        with self._attribute_reader():
            self._attribute_task(task_id)
            result = []
            for definition in self._attribute_definitions():
                item = self.get_task_attribute(task_id, definition["definition_id"])
                if (definition["archived_at"] is None or item["current"]["revision"]
                        or item["initial"]["state"] == "set" or item["history"]["events"]):
                    result.append(item)
            return result

    @_synchronized
    def _attribute_key_owner(self, key: str) -> dict | None:
        row = self._conn.execute("SELECT definition_id FROM task_attribute_key_owners WHERE key=?", (key,)).fetchone()
        return None if row is None else self._attribute_definition(row[0])

    def _create_task_attribute_definition_uncommitted(self, **fields) -> dict:
        self._attribute_owned_writer()
        fields = validate_definition(**fields)
        definition_id, at = uuid4().hex, _now()
        self._conn.execute(
            "INSERT INTO task_attribute_definitions (definition_id,current_version,created_at) VALUES (?,1,?)",
            (definition_id, at),
        )
        self._attribute_reserve_key(fields["key"], definition_id, at)
        self._attribute_insert_version({**fields, "definition_id": definition_id, "version": 1}, at)
        return self._attribute_definition(definition_id)

    @_synchronized
    def get_task_attribute_definition(self, definition_id: str, *, version: int | None = None) -> dict:
        return self._attribute_definition(definition_id, version)

    @_synchronized
    def list_task_attribute_definitions(self, *, include_archived: bool = False) -> list[dict]:
        return self._attribute_definitions(include_archived)

    def _attribute_editable(self, definition_id: str, expected_version: int) -> dict:
        current = self._attribute_definition(definition_id)
        if current["version"] != expected_version:
            raise AttributeConflictError("definition_version_conflict")
        if current["archived_at"] is not None:
            raise AttributeStoreError("definition_archived")
        return current

    @_synchronized
    def update_task_attribute_definition(self, definition_id: str, *, expected_version: int, **changes) -> dict:
        with self._attribute_writer():
            return self._update_task_attribute_definition_uncommitted(definition_id, expected_version=expected_version, **changes)

    def _update_task_attribute_definition_uncommitted(self, definition_id: str, *, expected_version: int, **changes) -> dict:
        self._attribute_owned_writer()
        _version(expected_version, "expected_version")
        unknown = changes.keys() - {"key", "type", "name", "description", "constraints"}
        if unknown:
            raise AttributeValidationError({str(key): ["unsupported_field"] for key in unknown})
        current = self._attribute_editable(definition_id, expected_version)
        fields = validate_definition(**{key: changes.get(key, current[key])
                                       for key in ("key", "type", "name", "description", "constraints")})
        structural = any(fields[key] != current[key] for key in ("key", "type", "constraints"))
        if structural and current["first_used_at"] is not None:
            raise AttributeStoreError("definition_structure_frozen")
        at = _now()
        self._attribute_reserve_key(fields["key"], definition_id, at)
        self._attribute_insert_version({**current, **fields, "version": expected_version + 1}, at)
        self._conn.execute("UPDATE task_attribute_definitions SET current_version=? WHERE definition_id=?",
                           (expected_version + 1, definition_id))
        return self._attribute_definition(definition_id)

    @_synchronized
    def archive_task_attribute_definition(self, definition_id: str, *, expected_version: int) -> dict:
        with self._attribute_writer():
            return self._archive_task_attribute_definition_uncommitted(definition_id, expected_version=expected_version)

    def _archive_task_attribute_definition_uncommitted(self, definition_id: str, *, expected_version: int) -> dict:
        self._attribute_owned_writer()
        _version(expected_version, "expected_version")
        current = self._attribute_editable(definition_id, expected_version)
        at = _now()
        self._attribute_insert_version({**current, "version": expected_version + 1, "archived_at": at}, at)
        self._conn.execute(
            "UPDATE task_attribute_definitions SET current_version=?,archived_at=? WHERE definition_id=?",
            (expected_version + 1, at, definition_id),
        )
        return self._attribute_definition(definition_id)

    @_synchronized
    def validate_task_attribute_values(self, values) -> list[dict]:
        return validate_values(values, self._attribute_definitions())

    def _attribute_task(self, task_id: str):
        row = self._conn.execute("SELECT created_at FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise AttributeStoreError("task_not_found")
        return row

    def _attribute_current(self, task_id: str, definition_id: str) -> dict:
        row = self._conn.execute("SELECT * FROM task_attribute_values WHERE task_id=? AND definition_id=?",
                                 (task_id, definition_id)).fetchone()
        if row is None:
            return {"present": False, "revision": 0}
        return {**_presence(row["present"], row["value_json"]), "revision": row["revision"],
                "definition_version": row["definition_version"], "updated_at": row["updated_at"]}

    def _attribute_write_value(self, task_id: str, definition_id: str, value: Any, *, present: bool,
                               expected_revision: int, expected_version: int, actor_id: str,
                               actor_kind: str, note: str | None, source: str, at: str) -> dict:
        # All authoritative reads/comparisons occur after the caller's BEGIN IMMEDIATE.
        self._attribute_task(task_id)
        definition = self._attribute_editable(definition_id, expected_version)
        current = self._attribute_current(task_id, definition_id)
        if current["revision"] != expected_revision:
            raise AttributeConflictError("value_revision_conflict")
        if present:
            failures = value_errors(definition, value)
            if failures:
                raise AttributeValidationError({definition["key"]: failures})
        revision = current["revision"] + 1
        value_json = _json(value) if present else None
        self._conn.execute(
            "INSERT INTO task_attribute_values "
            "(task_id,definition_id,definition_version,revision,present,value_json,updated_at) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(task_id,definition_id) DO UPDATE SET definition_version=excluded.definition_version, "
            "revision=excluded.revision,present=excluded.present,value_json=excluded.value_json,updated_at=excluded.updated_at",
            (task_id, definition_id, expected_version, revision, int(present), value_json, at),
        )
        if present:
            self._conn.execute(
                "UPDATE task_attribute_definitions SET first_used_at=COALESCE(first_used_at,?) WHERE definition_id=?",
                (at, definition_id),
            )
        self._conn.execute(
            "INSERT INTO task_attribute_value_events "
            "(task_id,definition_id,definition_version,revision,old_present,new_present,old_value_json,new_value_json,"
            "note,actor_id,actor_kind,at,source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, definition_id, expected_version, revision, int(current["present"]), int(present),
             _json(current["value"]) if current["present"] else None, value_json, note, actor_id, actor_kind, at, source),
        )
        return self._attribute_current(task_id, definition_id)

    @_synchronized
    def set_task_attribute(self, task_id: str, definition_id: str, value: Any, *, expected_revision: int,
                           expected_version: int, actor_id: str, actor_kind: str, note: str | None = None) -> dict:
        _version(expected_revision, "expected_revision", 0)
        _version(expected_version, "expected_version")
        _actor(actor_id, actor_kind, note)
        with self._attribute_writer():
            return self._attribute_write_value(task_id, definition_id, value, present=True,
                expected_revision=expected_revision, expected_version=expected_version,
                actor_id=actor_id, actor_kind=actor_kind, note=note, source="edit", at=_now())

    @_synchronized
    def clear_task_attribute(self, task_id: str, definition_id: str, *, expected_revision: int,
                             expected_version: int, actor_id: str, actor_kind: str, note: str | None = None) -> dict:
        _version(expected_revision, "expected_revision", 0)
        _version(expected_version, "expected_version")
        _actor(actor_id, actor_kind, note)
        with self._attribute_writer():
            return self._attribute_write_value(task_id, definition_id, None, present=False,
                expected_revision=expected_revision, expected_version=expected_version,
                actor_id=actor_id, actor_kind=actor_kind, note=note, source="clear", at=_now())

    def _write_task_attributes_initial_uncommitted(self, task_id: str, values, *, actor_id: str, actor_kind: str) -> None:
        """Caller must hold db._lock and an open writer transaction, as persist_root_task does.

        Never commits or rolls back caller work. A savepoint removes this helper's
        own partial writes on failure while retaining the caller's task/sentinel.
        This primitive records admission, including a positively known empty set.
        """
        if not self._conn.in_transaction:
            raise AttributeStoreError("owned_transaction_required")
        _actor(actor_id, actor_kind)
        self._attribute_task(task_id)
        if self._conn.execute("SELECT 1 FROM task_attribute_admissions WHERE task_id=?", (task_id,)).fetchone() or \
                self._conn.execute("SELECT 1 FROM task_attribute_value_events WHERE task_id=?", (task_id,)).fetchone():
            raise AttributeStoreError("admission_already_recorded_or_late")
        matched = validate_values(values, self._attribute_definitions())
        at = _now()
        self._conn.execute("SAVEPOINT task_attribute_initial")
        try:
            self._conn.execute("INSERT INTO task_attribute_admissions VALUES (?,?,?,?)",
                               (task_id, actor_id, actor_kind, at))
            for item in matched:
                self._conn.execute("INSERT INTO task_attribute_initial_values VALUES (?,?,?,?)",
                    (task_id, item["definition_id"], item["definition_version"], _json(item["value"])))
                self._attribute_write_value(task_id, item["definition_id"], item["value"], present=True,
                    expected_revision=0, expected_version=item["definition_version"], actor_id=actor_id,
                    actor_kind=actor_kind, note=None, source="admission", at=at)
            self._conn.execute("RELEASE task_attribute_initial")
        except BaseException:
            self._conn.execute("ROLLBACK TO task_attribute_initial")
            self._conn.execute("RELEASE task_attribute_initial")
            raise

    def _attribute_history(self, task_id: str, definition_id: str | None = None) -> dict:
        admission = self._conn.execute("SELECT 1 FROM task_attribute_admissions WHERE task_id=?", (task_id,)).fetchone()
        rows = self._conn.execute(
            "SELECT * FROM task_attribute_value_events WHERE task_id=? "
            + ("AND definition_id=? " if definition_id is not None else "") + "ORDER BY event_id",
            (task_id, definition_id) if definition_id is not None else (task_id,),
        ).fetchall()
        events = []
        for row in rows:
            event = {key: row[key] for key in ("event_id", "definition_id", "definition_version", "revision",
                                              "actor_id", "actor_kind", "at", "source")}
            event["old"] = _presence(row["old_present"], row["old_value_json"])
            event["new"] = _presence(row["new_present"], row["new_value_json"])
            event["definition"] = self._attribute_definition(row["definition_id"], row["definition_version"])
            if row["note"] is not None:
                event["note"] = row["note"]
            events.append(event)
        state = "complete" if admission else ("incomplete" if events else "unavailable")
        return {"history_state": state, "events": events}

    @_synchronized
    def get_task_attribute_history(self, task_id: str, *, definition_id: str | None = None) -> dict:
        with self._attribute_reader():
            self._attribute_task(task_id)
            return self._attribute_history(task_id, definition_id)

    @_synchronized
    def get_task_attribute(self, task_id: str, definition_id: str) -> dict:
        with self._attribute_reader():
            task = self._attribute_task(task_id)
            definition = self._attribute_definition(definition_id)
            admission = self._conn.execute("SELECT * FROM task_attribute_admissions WHERE task_id=?", (task_id,)).fetchone()
            initial = self._conn.execute(
                "SELECT * FROM task_attribute_initial_values WHERE task_id=? AND definition_id=?",
                (task_id, definition_id),
            ).fetchone()
            task_at, definition_at = _instant(task["created_at"]), _instant(definition["created_at"])
            if initial and admission and definition_at is not None:
                admitted_at = _instant(admission["admitted_at"])
                if admitted_at is not None and definition_at > admitted_at:
                    raise AttributeStoreError("contradictory_definition_admission_timing")
            later = task_at is not None and definition_at is not None and definition_at > task_at
            before = task_at is not None and definition_at is not None and definition_at < task_at
            if initial:
                provenance = "supplied_at_admission"
                original = {"state": "set", "value": json.loads(initial["value_json"]),
                            "definition_version": initial["definition_version"],
                            "definition": self._attribute_definition(definition_id, initial["definition_version"])}
            else:
                provenance = "definition_added_later" if later else (
                    "not_supplied_before_dispatch" if admission and before else "timing_unknown")
                original = {"state": "unset" if admission and (before or later) else "unknown"}
            admitted = {"state": "unavailable"} if admission is None else {
                "state": "recorded", "actor_id": admission["actor_id"],
                "actor_kind": admission["actor_kind"], "at": admission["admitted_at"]}
            return {"definition": definition, "current": self._attribute_current(task_id, definition_id),
                    "initial": original, "admission": admitted, "provenance": provenance,
                    "history": self._attribute_history(task_id, definition_id)}

"""Accepted PR1 C01-C18 cases in the standalone SQLite/unittest venue.

No daemon, provider, retired unit-suite import, live org or production test seam.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest

from runtime.infrastructure.database import Database
from runtime.infrastructure.db.task_attributes import AttributeConflictError, AttributeStoreError
from runtime.models import TaskRecord, TaskStatus, ThreadRecord
from runtime.task_attributes import AttributeValidationError, validate_definition, validate_values


ACTOR = {"actor_id": "actor-founder", "actor_kind": "founder"}
TABLES = ["task_attribute_" + suffix for suffix in (
    "definitions", "definition_versions", "key_owners", "admissions", "initial_values", "values", "value_events")]
IMMUTABLE = [TABLES[i] for i in (1, 2, 3, 4, 6)]


def definition(kind="numeric", **changes):
    return validate_definition(**{"key": "effort", "type": kind, "name": " Effort ", **changes})


class ValidationTests(unittest.TestCase):
    def test_c02_definition_boundaries(self):
        for key in ("a", "a0_b", "a" * 64):
            with self.subTest(key=key):
                self.assertEqual(definition(key=key)["key"], key)
        for key in ("", "A", "aB", "1a", "a-b", "a b", "a" * 65, "雪", None):
            with self.subTest(key=key):
                with self.assertRaises(AttributeValidationError) as error:
                    definition(key=key)
                self.assertEqual(error.exception.errors, {"key": ["invalid_key"]})
        for name in ("x", "x" * 80, " x "):
            self.assertEqual(definition(name=name)["name"], name.strip())
        for name in ("", "  ", "x" * 81, None):
            with self.subTest(name=name):
                with self.assertRaises(AttributeValidationError) as error:
                    definition(name=name)
                self.assertIn("name", error.exception.errors)
        for description in (None, "", "雪" * 4000):
            self.assertEqual(definition(description=description)["description"], description)
        for description in ("雪" * 4001, 7):
            with self.assertRaises(AttributeValidationError):
                definition(description=description)

    def test_c04_constraint_boundaries(self):
        for rules in ({}, {"min": 1, "max": 1}, {"min": 1.2, "max": 2, "integer_only": True},
                      {"integer_only": False}, {"min": -(2**80), "max": 2**80}):
            self.assertEqual(definition(constraints=rules)["constraints"], rules)
        bad = [{"min": 2, "max": 1}, {"min": 1.2, "max": 1.8, "integer_only": True},
               {"integer_only": 1}, {"min_length": 0}]
        bad += [{field: value} for field in ("min", "max") for value in (True, "1", None, float("nan"), float("inf"), -float("inf"))]
        bad += [{field: value} for field, value in (("regex", "x"), ("enum", []), ("required", True), ("default", 4))]
        for rules in bad:
            with self.subTest(rules=rules), self.assertRaises(AttributeValidationError):
                definition(constraints=rules)
        for kind in ("number", "array", "object", None, True, []):
            with self.subTest(kind=kind), self.assertRaises(AttributeValidationError):
                definition(kind)
        for kind, rules in (("string", {"min": 0}), ("string", {"integer_only": True}),
                            ("boolean", {"min_length": 0}), ("boolean", {"max": 1})):
            with self.subTest(kind=kind, rules=rules), self.assertRaises(AttributeValidationError):
                definition(kind, constraints=rules)
        for rules in ([], "x", 1):
            with self.assertRaises(AttributeValidationError):
                definition(constraints=rules)

    def check_values(self, kind, rules, good, bad):
        d = {**definition(kind, constraints=rules), "definition_id": "D", "version": 1}
        for value in good:
            with self.subTest(kind=kind, accepted=repr(value)):
                self.assertEqual(validate_values({"effort": value}, [d])[0]["value"], value)
        for value in bad:
            with self.subTest(kind=kind, rejected=repr(value)):
                with self.assertRaises(AttributeValidationError) as error:
                    validate_values({"effort": value}, [d])
                self.assertIn("effort", error.exception.errors)

    def test_c05_numeric_values(self):
        self.check_values("numeric", {"min": 1, "max": 10, "integer_only": True},
                          [4, 6, 4.0], ["4", True, None, 0, 11, 4.5, float("nan"), float("inf"), -float("inf")])
        self.check_values("numeric", {}, [0, -1, 0.125, -0.5, 2**80+1, -(2**80+1)], [True, False, "4", None])

    def test_c06_string_values_and_bounds(self):
        self.check_values("string", {}, ["", " ", "雪🙂e\u0301", "雪"*4000], [None, False, 0, "雪"*4001])
        self.check_values("string", {"min_length": 1, "max_length": 2}, ["x", "🙂雪"], ["", "xyz"])
        for field in ("min_length", "max_length"):
            for value in (-1, 1.5, True, "1", None, 4001):
                with self.subTest(field=field, value=value), self.assertRaises(AttributeValidationError):
                    definition("string", constraints={field: value})
        with self.assertRaises(AttributeValidationError):
            definition("string", constraints={"min_length": 2, "max_length": 1})
        self.assertEqual(definition("string", constraints={"min_length": 0, "max_length": 4000})["constraints"],
                         {"min_length": 0, "max_length": 4000})

    def test_c07_boolean_values(self):
        self.check_values("boolean", {}, [True, False], [0, 1, "true", "false", None])


class BeginGate:
    """Forward the actual connection, pausing just before its first writer BEGIN."""
    def __init__(self, connection):
        self.connection = connection
        self.arrived = threading.Event()
        self.release = threading.Event()
        self.hits = 0
        self.was_in_transaction = None
        self.deadline = time.monotonic() + 30

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, sql, parameters=()):
        if sql == "BEGIN IMMEDIATE" and self.hits == 0:
            self.hits += 1
            self.was_in_transaction = self.connection.in_transaction
            self.arrived.set()
            if not self.release.wait(max(0, self.deadline - time.monotonic())):
                raise AssertionError("writer gate deadline")
        return self.connection.execute(sql, parameters)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.path = Path(self.scratch.name) / "org.db"
        self.db = Database(self.path)
        self.handles = [self.db]
        self.task("TASK-1")

    def tearDown(self):
        for db in reversed(self.handles):
            db.close()
        self.scratch.cleanup()

    def reopen(self):
        self.db.close()
        self.db = Database(self.path)
        self.handles.append(self.db)
        return self.db

    def observer(self):
        db = Database(self.path)
        self.handles.append(db)
        return db

    def task(self, task_id, *, at="2026-11-01T08:00:00+00:00", status=TaskStatus.PENDING):
        self.db.insert_task(TaskRecord(id=task_id, brief="Synthetic attribute case", status=status,
                                      created_at=datetime.fromisoformat(at)))

    def create(self, key="effort", kind="numeric", **fields):
        return self.db.create_task_attribute_definition(key=key, type=kind, name=key, **fields)

    def set_value(self, d, value, revision=0, *, db=None, task="TASK-1", **fields):
        return (db or self.db).set_task_attribute(task, d["definition_id"], value,
            expected_revision=revision, expected_version=d["version"], **{**ACTOR, **fields})

    def clear(self, d, revision, *, db=None):
        return (db or self.db).clear_task_attribute("TASK-1", d["definition_id"],
            expected_revision=revision, expected_version=d["version"], **ACTOR)

    def state(self, db=None):
        db = db or self.db
        return {name: [tuple(r) for r in db.execute(f"SELECT * FROM {name} ORDER BY rowid")]
                for name in TABLES}

    def admit(self, values, task="TASK-1"):
        with self.db._lock:
            self.db._conn.execute("BEGIN IMMEDIATE")
            try:
                self.db._write_task_attributes_initial_uncommitted(task, values, **ACTOR)
                self.db._conn.commit()
            except BaseException:
                self.db._conn.rollback()
                raise

    def projection(self, d, task="TASK-1"):
        return self.db.get_task_attribute(task, d["definition_id"])

    def test_c01_org_isolation(self):
        a = self.create(description="A rubric")
        other = Database(Path(self.scratch.name) / "other.db")
        self.handles.append(other)
        other.insert_task(TaskRecord(id="TASK-1", brief="Other org"))
        b = other.create_task_attribute_definition(key="effort", type="numeric", name="Other", description="B rubric")
        other.create_task_attribute_definition(key="foreign_only", type="boolean", name="Foreign")
        self.set_value(a, 4)
        self.set_value(b, 6, db=other)
        self.db.update_task_attribute_definition(a["definition_id"], expected_version=1, description="A2")
        self.db.archive_task_attribute_definition(a["definition_id"], expected_version=2)
        self.assertEqual(other.get_task_attribute("TASK-1", b["definition_id"])["current"]["value"], 6)
        self.assertIs(type(other.get_task_attribute("TASK-1", b["definition_id"])["current"]["value"]), int)
        self.assertEqual(other.get_task_attribute_definition(b["definition_id"])["description"], "B rubric")
        self.assertIsNone(other.get_task_attribute_definition(b["definition_id"])["archived_at"])
        with self.assertRaises(AttributeValidationError) as error:
            self.db.validate_task_attribute_values({"foreign_only": True})
        self.assertEqual(error.exception.errors, {"foreign_only": ["unknown_key"]})

    def test_c02_invalid_definitions_leave_no_residue(self):
        before = self.state()
        for fields in ({"key": "BAD"}, {"name": " "}, {"description": "x"*4001},
                       {"constraints": {"min": True}}, {"type": "object"}):
            with self.subTest(fields=fields), self.assertRaises(AttributeValidationError):
                self.db.create_task_attribute_definition(**{"key": "a", "type": "numeric", "name": "a", **fields})
            self.assertEqual(self.state(), before)

    def test_c03_c09_historical_key_and_full_versions(self):
        d = self.create("a", description="original", constraints={"min": 0})
        old = self.db.get_task_attribute_definition(d["definition_id"], version=1)
        updated = self.db.update_task_attribute_definition(d["definition_id"], expected_version=1,
            key="b", type="string", constraints={"max_length": 5}, name=" New ", description="reworded")
        self.assertEqual(updated["definition_id"], d["definition_id"])
        self.assertEqual((updated["key"], updated["type"], updated["name"], updated["version"]), ("b", "string", "New", 2))
        f = self.create("c")
        archived = self.create("archived")
        self.db.archive_task_attribute_definition(archived["definition_id"], expected_version=1)
        for attempt in range(2):
            for key in ("a", "b", "c", "archived"):
                for operation in ("create", "rename"):
                    before = self.state()
                    with self.subTest(attempt=attempt, key=key, operation=operation), self.assertRaises(AttributeValidationError):
                        if operation == "create":
                            self.create(key)
                        else:
                            # Use a different owner, including active/archived/retired targets.
                            self.db.update_task_attribute_definition(f["definition_id"] if key != "c" else d["definition_id"],
                                expected_version=1 if key != "c" else 2, key=key)
                    self.assertEqual(self.state(), before)
            self.assertEqual(self.db.get_task_attribute_definition(d["definition_id"], version=1), old)
            self.assertEqual(self.db.execute("SELECT definition_id FROM task_attribute_key_owners WHERE key='a'").fetchone()[0], d["definition_id"])
            self.reopen()
        # Duplicate display names are legal.
        self.db.create_task_attribute_definition(key="duplicate_name", type="boolean", name="New")

    def test_c07_typed_roundtrips(self):
        cases = [("numeric", v) for v in (0, 0.125, -0.5, 2**80+1, -(2**80+1))]
        cases += [("boolean", True), ("boolean", False)]
        cases += [("string", v) for v in ("", "  ", "雪🙂e\u0301")]
        records = []
        for i, (kind, value) in enumerate(cases):
            d = self.create(f"field_{i}", kind)
            self.set_value(d, value)
            records.append((d, value))
        self.reopen()
        for d, value in records:
            with self.subTest(value=repr(value)):
                current = self.projection(d)["current"]
                self.assertTrue(current["present"])
                self.assertEqual(current["value"], value)
                self.assertIs(type(current["value"]), type(value))
                raw = self.db.execute("SELECT value_json FROM task_attribute_values WHERE definition_id=?", (d["definition_id"],)).fetchone()[0]
                self.assertEqual(json.loads(raw), value)
                self.assertIs(type(json.loads(raw)), type(value))

    def test_c08_whole_set_readonly(self):
        effort = self.create(constraints={"min": 1, "max": 10, "integer_only": True})
        complexity = self.create("complexity", constraints={"min": 1, "max": 10, "integer_only": True})
        archived = self.create("archived")
        self.db.archive_task_attribute_definition(archived["definition_id"], expected_version=1)
        existing = self.create("existing", "boolean")
        self.set_value(existing, False)
        before = self.state()
        self.assertEqual(self.db.validate_task_attribute_values({}), [])
        self.assertEqual(self.db.validate_task_attribute_values({"effort": 4, "complexity": 6}), [
            {"key": "effort", "definition_id": effort["definition_id"], "definition_version": 1, "value": 4},
            {"key": "complexity", "definition_id": complexity["definition_id"], "definition_version": 1, "value": 6}])
        self.assertEqual(self.state(), before)
        with self.assertRaises(AttributeValidationError) as error:
            self.db.validate_task_attribute_values([("effort", True), ("effort", 4), ("complexity", None),
                                                    ("unknown", 4), ("archived", 4)])
        self.assertEqual(error.exception.errors, {"effort": ["expected_finite_number", "duplicate_key"],
            "complexity": ["expected_finite_number"], "unknown": ["unknown_key"], "archived": ["archived_key"]})
        self.assertEqual(self.state(), before)
        self.assertIsNone(self.db.get_task_attribute_definition(effort["definition_id"])["first_used_at"])

    def test_c10_freeze_after_clear(self):
        d = self.create()
        self.set_value(d, 4)
        self.clear(d, 1)
        self.reopen()
        before = self.state()
        for fields in ({"key": "new_key"}, {"type": "string"}, {"constraints": {"max": 9}}):
            with self.subTest(fields=fields), self.assertRaisesRegex(AttributeStoreError, "definition_structure_frozen"):
                self.db.update_task_attribute_definition(d["definition_id"], expected_version=1, **fields)
            self.assertEqual(self.state(), before)
        newer = self.db.update_task_attribute_definition(d["definition_id"], expected_version=1, name="Reworded", description="new rubric")
        self.assertEqual(newer["version"], 2)
        self.assertIsNotNone(newer["first_used_at"])
        self.assertEqual(self.db.get_task_attribute_definition(d["definition_id"], version=1)["name"], "effort")

    def test_c11_archive_blocks_writes_and_retains_reads(self):
        used = self.create(description="rubric", constraints={"min": 0})
        unused = self.create("unused")
        self.admit({"effort": 4})
        for d in (used, unused):
            archived = self.db.archive_task_attribute_definition(d["definition_id"], expected_version=1)
            self.assertEqual(archived["version"], 2)
            before = self.state()
            operations = [lambda: self.set_value(archived, 5, 1 if d == used else 0),
                          lambda: self.clear(archived, 1 if d == used else 0),
                          lambda: self.db.update_task_attribute_definition(d["definition_id"], expected_version=2, name="new"),
                          lambda: self.db.archive_task_attribute_definition(d["definition_id"], expected_version=2)]
            for operation in operations:
                with self.assertRaisesRegex(AttributeStoreError, "definition_archived"):
                    operation()
                self.assertEqual(self.state(), before)
        self.assertEqual(self.db.list_task_attribute_definitions(), [])
        self.assertEqual(len(self.db.list_task_attribute_definitions(include_archived=True)), 2)
        projection = self.projection(used)
        self.assertEqual(projection["current"]["value"], 4)
        self.assertEqual(projection["initial"]["value"], 4)
        self.assertEqual(projection["history"]["events"][0]["definition"]["description"], "rubric")

    def test_c12_stale_and_aba(self):
        d = self.create()
        self.set_value(d, 4)
        self.clear(d, 1)
        tombstone = self.projection(d)["current"]
        self.assertEqual((tombstone["present"], tombstone["revision"]), (False, 2))
        self.set_value(d, 6, 2)
        self.assertEqual(self.projection(d)["current"]["revision"], 3)
        before = self.state()
        for revision in (0, 1, 2):
            for clear in (False, True):
                with self.subTest(revision=revision, clear=clear), self.assertRaisesRegex(AttributeConflictError, "value_revision_conflict"):
                    self.clear(d, revision) if clear else self.set_value(d, 5, revision)
                self.assertEqual(self.state(), before)

    def run_schedule(self, loser, winner):
        competitor = self.observer()
        gate = BeginGate(self.db._conn)
        original = self.db._conn
        self.db._conn = gate
        result = []
        def run():
            try:
                loser()
                result.append("accepted")
            except BaseException as error:
                result.append(error)
        worker = threading.Thread(target=run)
        worker.start()
        try:
            self.assertTrue(gate.arrived.wait(10), "writer did not reach actual BEGIN")
            self.assertFalse(gate.was_in_transaction)
            winner(competitor)
            winning_state = self.state(competitor)
            gate.release.set()
            worker.join(max(0, gate.deadline - time.monotonic()))
            self.assertFalse(worker.is_alive(), "writer exceeded bounded deadline")
            self.assertEqual(gate.hits, 1)
            self.assertEqual(len(result), 1)
            self.assertIsInstance(result[0], AttributeStoreError, result)
            self.assertIn(result[0].code, {"value_revision_conflict", "definition_version_conflict", "definition_archived", "definition_structure_frozen"})
            self.assertEqual(self.state(competitor), winning_state)
        finally:
            gate.release.set()
            worker.join(max(0, gate.deadline - time.monotonic()))
            self.db._conn = original
        self.assertFalse(worker.is_alive())
        self.reopen()
        self.assertEqual(self.state(), winning_state)

    def test_c12a_set_writer_boundary(self):
        d = self.create()
        self.admit({"effort": 4})
        self.db.validate_task_attribute_values({"effort": 5})  # early check passes
        self.run_schedule(lambda: self.set_value(d, 5, 1), lambda other: self.set_value(d, 6, 1, db=other))
        self.assertEqual(self.projection(d)["current"]["value"], 6)
        self.assertEqual(self.projection(d)["initial"]["value"], 4)
        self.assertEqual(len(self.projection(d)["history"]["events"]), 2)

    def test_c12a_clear_writer_boundary(self):
        d = self.create()
        self.admit({"effort": 4})
        self.assertEqual(self.projection(d)["current"]["revision"], 1)
        self.run_schedule(lambda: self.clear(d, 1), lambda other: self.set_value(d, 6, 1, db=other))
        self.assertEqual(self.projection(d)["current"]["value"], 6)

    def test_c12b_first_assignment_writer_boundary(self):
        d = self.create()
        self.db.validate_task_attribute_values({"effort": 4})
        self.assertEqual(self.projection(d)["current"], {"present": False, "revision": 0})
        self.run_schedule(lambda: self.set_value(d, 4), lambda other: self.set_value(d, 6, db=other))
        self.assertEqual(self.projection(d)["current"]["revision"], 1)
        self.assertEqual(self.projection(d)["history"]["events"][0]["source"], "edit")
        self.assertEqual(self.state()["task_attribute_initial_values"], [])

    def test_c13a_archive_writer_boundary(self):
        d = self.create()
        self.db.validate_task_attribute_values({"effort": 4})
        self.run_schedule(lambda: self.set_value(d, 4), lambda other: other.archive_task_attribute_definition(d["definition_id"], expected_version=1))
        self.assertIsNone(self.db.get_task_attribute_definition(d["definition_id"])["first_used_at"])

    def test_c13b_constraints_writer_boundary(self):
        d = self.create(constraints={"min": 1, "max": 10})
        self.db.validate_task_attribute_values({"effort": 4})
        self.run_schedule(lambda: self.set_value(d, 4), lambda other: other.update_task_attribute_definition(
            d["definition_id"], expected_version=1, constraints={"min": 1, "max": 9}))
        self.assertEqual(self.db.validate_task_attribute_values({"effort": 4})[0]["definition_version"], 2)
        self.assertEqual(self.state()["task_attribute_values"], [])

    def test_c13c_first_use_inverse_writer_boundary(self):
        d = self.create(constraints={"min": 1, "max": 10})
        self.assertIsNone(self.db.get_task_attribute_definition(d["definition_id"])["first_used_at"])
        definition(constraints={"min": 1, "max": 9})
        self.run_schedule(lambda: self.db.update_task_attribute_definition(d["definition_id"], expected_version=1,
            constraints={"min": 1, "max": 9}), lambda other: self.set_value(d, 4, db=other))
        self.assertEqual(self.db.get_task_attribute_definition(d["definition_id"])["version"], 1)

    def test_c14_caller_transaction_ownership(self):
        observer = self.observer()
        self.db.execute("CREATE TABLE case_sentinel (id TEXT PRIMARY KEY)")
        self.db.commit()
        for name, values, commit, invalid in (
            ("commit", {"effort": 4, "complexity": 6}, True, False),
            ("empty", {}, True, False), ("rollback", {"effort": 4}, False, False),
            ("invalid", {"effort": None, "complexity": 11}, False, True)):
            keys = {key: key + "_" + name for key in ("effort", "complexity")}
            definitions = [self.create(key, constraints={"min": 1, "max": 10}) for key in keys.values()]
            values = {keys[key]: value for key, value in values.items()}
            with self.subTest(name=name):
                with self.db._lock:
                    before = self.state()
                    tid = "TASK-" + name
                    self.db._conn.execute("BEGIN IMMEDIATE")
                    try:
                        self.db._conn.execute("INSERT INTO case_sentinel VALUES (?)", (name,))
                        self.db._insert_task_uncommitted(TaskRecord(id=tid, brief="Caller-owned task"))
                        if invalid:
                            with self.assertRaises(AttributeValidationError) as error:
                                self.db._write_task_attributes_initial_uncommitted(tid, values, **ACTOR)
                            self.assertEqual(set(error.exception.errors), set(keys.values()))
                            self.assertEqual(self.state(), before)
                        else:
                            self.db._write_task_attributes_initial_uncommitted(tid, values, **ACTOR)
                        self.assertTrue(self.db._conn.in_transaction, "helper ended caller transaction")
                        self.assertIsNotNone(self.db._conn.execute("SELECT 1 FROM case_sentinel WHERE id=?", (name,)).fetchone())
                        self.assertIsNotNone(self.db._conn.execute("SELECT 1 FROM tasks WHERE id=?", (tid,)).fetchone())
                        self.assertIsNone(observer.execute("SELECT 1 FROM case_sentinel WHERE id=?", (name,)).fetchone())
                        self.assertIsNone(observer.execute("SELECT 1 FROM tasks WHERE id=?", (tid,)).fetchone())
                        self.assertEqual(self.state(observer), before)
                        if commit:
                            self.db._conn.commit()
                        else:
                            self.db._conn.rollback()
                    finally:
                        if self.db._conn.in_transaction:
                            self.db._conn.rollback()
                    self.assertEqual(observer.execute("SELECT COUNT(*) FROM tasks WHERE id=?", (tid,)).fetchone()[0], int(commit))
                    self.assertEqual(observer.execute("SELECT COUNT(*) FROM case_sentinel WHERE id=?", (name,)).fetchone()[0], int(commit))
                    if not commit:
                        self.assertEqual(self.state(observer), before)
                    else:
                        self.assertEqual(observer.execute("SELECT COUNT(*) FROM task_attribute_admissions WHERE task_id=?", (tid,)).fetchone()[0], 1)
                        self.assertEqual(observer.execute("SELECT COUNT(*) FROM task_attribute_initial_values WHERE task_id=?", (tid,)).fetchone()[0], len(values))
                    for d in definitions:
                        used = observer.get_task_attribute_definition(d["definition_id"])["first_used_at"]
                        self.assertEqual(used is not None, commit and d["key"] in values)
                    expected = self.state(observer)
                self.reopen()
                self.assertEqual(self.state(), expected)
        before = self.state()
        with self.db._lock, self.assertRaisesRegex(AttributeStoreError, "owned_transaction_required"):
            self.db._write_task_attributes_initial_uncommitted("TASK-1", {keys["effort"]: 4}, **ACTOR)
        self.assertFalse(self.db._conn.in_transaction)
        self.assertEqual(self.state(), before)
        self.reopen()
        self.assertEqual(self.state(), before)

    def test_c15a_initial_and_ordered_presence_history(self):
        d = self.create(description="original rubric")
        self.admit({"effort": 4})
        initial = self.projection(d)["initial"]
        before = self.state()
        with self.assertRaisesRegex(AttributeStoreError, "admission_already_recorded_or_late"):
            self.admit({"effort": 6})
        self.assertEqual(self.state(), before)
        with self.assertRaises(AttributeValidationError):
            self.set_value(d, None, 1)
        self.assertEqual(self.state(), before)
        self.set_value(d, 5, 1, note="adjusted", actor_id="actor-agent", actor_kind="agent")
        self.clear(d, 2)
        self.reopen()
        projection = self.projection(d)
        self.assertEqual(projection["initial"], initial)
        events = projection["history"]["events"]
        self.assertEqual([e["source"] for e in events], ["admission", "edit", "clear"])
        self.assertEqual([e["old"] for e in events], [{"present": False}, {"present": True, "value": 4}, {"present": True, "value": 5}])
        self.assertEqual([e["new"] for e in events], [{"present": True, "value": 4}, {"present": True, "value": 5}, {"present": False}])
        self.assertEqual([e["revision"] for e in events], [1, 2, 3])
        self.assertEqual([e["actor_kind"] for e in events], ["founder", "agent", "founder"])
        self.assertEqual([e["actor_id"] for e in events], ["actor-founder", "actor-agent", "actor-founder"])
        self.assertEqual([e["definition_version"] for e in events], [1, 1, 1])
        self.assertNotIn("note", events[0])
        self.assertEqual(events[1]["note"], "adjusted")
        for event in events:
            self.assertEqual(datetime.fromisoformat(event["at"]).utcoffset(), timezone.utc.utcoffset(None))
        self.assertEqual([e["event_id"] for e in events], sorted(e["event_id"] for e in events))
        # PR1 storage has no blanket terminal-task edit restriction.
        self.db.execute("UPDATE tasks SET status='completed' WHERE id='TASK-1'")
        self.db.commit()
        self.set_value(d, 7, 3)

    def test_c15b_later_change_event_failure(self):
        self.event_failure_case("later-change")

    def test_c15b_clear_event_failure(self):
        self.event_failure_case("clear")

    def test_c15b_failed_first_event_failure(self):
        self.event_failure_case("failed-first")

    def event_failure_case(self, mode):
        d = self.create("field_" + mode.replace("-", "_"))
        if mode != "failed-first":
            self.admit({d["key"]: 4})
        revision = 0 if mode == "failed-first" else 1
        before = self.state()
        observed = []
        def probe():
            row = self.db._conn.execute("SELECT present,value_json,revision FROM task_attribute_values WHERE definition_id=?", (d["definition_id"],)).fetchone()
            observed.append(tuple(row) if row else None)
            return 1
        self.db._conn.create_function("case_probe", 0, probe)
        self.db.execute("CREATE TEMP TRIGGER case_event_failure BEFORE INSERT ON task_attribute_value_events BEGIN SELECT case_probe(); SELECT RAISE(ABORT,'case_event_failure'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "case_event_failure"):
            self.clear(d, revision) if mode == "clear" else self.set_value(d, 4 if mode == "failed-first" else 5, revision)
        self.assertEqual(observed, [(0 if mode == "clear" else 1, None if mode == "clear" else ("4" if mode == "failed-first" else "5"), revision+1)])
        self.assertFalse(self.db._conn.in_transaction, "public writer left failed transaction open")
        self.assertEqual(self.state(self.observer()), before)
        self.reopen()  # destroys TEMP fault only after observing explicit rollback
        self.assertEqual(self.state(), before)
        if mode == "failed-first":
            self.assertIsNone(self.db.get_task_attribute_definition(d["definition_id"])["first_used_at"])
        result = self.clear(d, revision) if mode == "clear" else self.set_value(d, 4 if mode == "failed-first" else 5, revision)
        self.assertEqual(result["revision"], revision+1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM task_attribute_value_events WHERE definition_id=?", (d["definition_id"],)).fetchone()[0], revision+1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM task_attribute_admissions").fetchone()[0], 0 if mode == "failed-first" else 1)


    def test_c15c_immutable_tables_update_and_delete(self):
        d = self.create("a")
        self.db.update_task_attribute_definition(d["definition_id"], expected_version=1, key="b")
        other = self.create("other")
        self.admit({"b": 4})
        self.task("TASK-empty")
        self.admit({}, "TASK-empty")
        attacks = [
            ("task_attribute_definition_versions", "description='changed'", "definition_id='" + d["definition_id"] + "' AND version=1"),
            ("task_attribute_key_owners", "definition_id='" + other["definition_id"] + "'", "key='a'"),
            ("task_attribute_admissions", "actor_id='changed'", "task_id='TASK-empty'"),
            ("task_attribute_initial_values", "value_json='5'", "task_id='TASK-1'"),
            ("task_attribute_value_events", "note='changed'", "task_id='TASK-1'")]
        for table, change, where in attacks:
            for operation in ("UPDATE", "DELETE"):
                before = self.state()
                sql = f"UPDATE {table} SET {change} WHERE {where}" if operation == "UPDATE" else f"DELETE FROM {table} WHERE {where}"
                with self.subTest(table=table, operation=operation), self.assertRaisesRegex(sqlite3.IntegrityError, "immutable task attribute record"):
                    self.db.execute(sql)
                self.db._conn.rollback()
                self.assertEqual(self.state(), before)
                self.reopen()
                self.assertEqual(self.state(), before)

    def test_c16_literal_provenance(self):
        d = self.create()
        # Synthetic timestamps are fixture inputs; no inference from a generic audit row.
        self.db.execute("UPDATE task_attribute_definitions SET created_at=?", ("2026-11-01T07:00:00+00:00",))
        self.db.commit()
        self.task("TASK-supplied")
        self.admit({"effort": 4}, "TASK-supplied")
        p = self.projection(d, "TASK-supplied")
        self.assertEqual((p["provenance"], p["initial"]["state"], p["admission"]["state"], p["history"]["history_state"]),
                         ("supplied_at_admission", "set", "recorded", "complete"))
        for tid, task_time, def_time, admitted, provenance, initial in (
            ("omitted", "2026-11-01T08:00:00Z", "2026-11-01T07:00:00Z", True, "not_supplied_before_dispatch", "unset"),
            ("recorded_later", "2026-11-01T08:00:00Z", "2026-11-01T09:00:00Z", True, "definition_added_later", "unset"),
            ("legacy_later", "2026-11-01T08:00:00Z", "2026-11-01T09:00:00Z", False, "definition_added_later", "unknown"),
            ("legacy_before", "2026-11-01T08:00:00Z", "2026-11-01T07:00:00Z", False, "timing_unknown", "unknown"),
            ("recorded_equal", "2026-11-01T08:00:00Z", "2026-11-01T16:00:00+08:00", True, "timing_unknown", "unknown"),
            ("equal", "2026-11-01T08:00:00Z", "2026-11-01T16:00:00+08:00", False, "timing_unknown", "unknown"),
            ("offset_later", "2026-11-01T08:00:00Z", "2026-11-01T16:00:01+08:00", False, "definition_added_later", "unknown"),
            ("utc_later", "2026-11-01T08:00:00Z", "2026-11-01T08:00:01Z", False, "definition_added_later", "unknown"),
            ("malformed", "2026-11-01T08:00:00Z", "bad-time", False, "timing_unknown", "unknown"),
            ("naive", "2026-11-01T08:00:00Z", "2026-11-01T09:00:00", False, "timing_unknown", "unknown"),
            ("missing", "2026-11-01T08:00:00Z", "", False, "timing_unknown", "unknown"),
            ("bad_task", "2026-11-01T08:00:00Z", "2026-11-01T09:00:00Z", False, "timing_unknown", "unknown")):
            with self.subTest(tid=tid):
                self.task("TASK-" + tid, at=task_time)
                if tid == "bad_task":
                    self.db.execute("UPDATE tasks SET created_at='bad-time' WHERE id=?", ("TASK-"+tid,))
                self.db.execute("UPDATE task_attribute_definitions SET created_at=?", (def_time,))
                self.db.commit()
                if admitted:
                    self.admit({}, "TASK-" + tid)
                p = self.projection(d, "TASK-" + tid)
                self.assertEqual((p["provenance"], p["initial"]), (provenance, {"state": initial}))
                self.assertEqual(p["admission"]["state"], "recorded" if admitted else "unavailable")
                self.assertEqual(p["history"], {"history_state": "complete" if admitted else "unavailable", "events": []})
                self.set_value(d, 4, task="TASK-" + tid)
                self.reopen()
                p = self.projection(d, "TASK-" + tid)
                self.assertEqual(p["provenance"], provenance)
                self.assertEqual(p["initial"], {"state": initial})
                self.assertEqual(p["history"]["history_state"], "complete" if admitted else "incomplete")
                self.assertEqual(p["history"]["events"][0]["source"], "edit")

        # A real definition created after an already-recorded empty admission is normal.
        self.task("TASK-before-definition")
        self.admit({}, "TASK-before-definition")
        late = self.create("after_admission")
        projection = self.projection(late, "TASK-before-definition")
        self.assertEqual(projection["provenance"], "definition_added_later")
        self.assertEqual(projection["initial"], {"state": "unset"})
        self.assertEqual(projection["history"], {"history_state": "complete", "events": []})
        self.task("TASK-contradictory")
        self.admit({"effort": 4}, "TASK-contradictory")
        self.db.execute("UPDATE task_attribute_definitions SET created_at='2999-01-01T00:00:00Z' WHERE definition_id=?", (d["definition_id"],))
        self.db.commit()
        with self.assertRaisesRegex(AttributeStoreError, "contradictory_definition_admission_timing"):
            self.projection(d, "TASK-contradictory")

    def test_c17_additive_install_manifest_and_retention(self):
        self.task("TASK-902", status=TaskStatus.COMPLETED)
        self.db.insert_thread(ThreadRecord(id="THR-901", subject="Retained thread"))
        audit = [
            (4101, "TASK-901", "actor-agent", "task_created", '{"case":"retained_task","value":0}', "2026-11-01T08:00:00+00:00"),
            (4102, "THR-901", "actor-founder", "thread_message", '{"case":"retained_thread","value":false}', "2026-11-01T08:00:00+00:00"),
            (4103, "artifact:qa-fixture.txt", "actor-agent", "artifact_put", '{"name":"qa-fixture.txt","size":7}', "2026-11-01T08:00:00+00:00"),
            (4104, "config:working_hours", "actor-founder", "org_config_write", '{"section":"working_hours","enabled":false}', "2026-11-01T08:00:00+00:00")]
        self.db._conn.executemany("INSERT INTO audit_log VALUES (?,?,?,?,?,?)", audit)
        self.db.commit()
        for table in reversed(TABLES):
            self.db.execute(f"DROP TABLE {table}")
        self.db.commit()
        def old_layout(db):
            return [tuple(r) for r in db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'task_attribute_%' AND name NOT LIKE 'idx_task_attribute_%' AND tbl_name NOT LIKE 'task_attribute_%' ORDER BY type,name")]
        def retained(db):
            return {table: [tuple(r) for r in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                    for table in ("tasks", "threads", "audit_log")}
        old_schema, old_rows = old_layout(self.db), retained(self.db)
        old_columns = {r[0]: [tuple(c) for c in self.db.execute(f"PRAGMA table_info({r[0]})")]
                       for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        # Literal independent manifest: name/type/NOT NULL/PK ordinal.
        manifest = {
            "definitions": "definition_id:T:1:1 current_version:I:1:0 created_at:T:1:0 archived_at:T:0:0 first_used_at:T:0:0",
            "definition_versions": "definition_id:T:1:1 version:I:1:2 key:T:1:0 type:T:1:0 name:T:1:0 constraints_json:T:1:0 recorded_at:T:1:0 description:T:0:0 archived_at:T:0:0",
            "key_owners": "key:T:1:1 definition_id:T:1:0 reserved_at:T:1:0",
            "admissions": "task_id:T:1:1 actor_id:T:1:0 actor_kind:T:1:0 admitted_at:T:1:0",
            "initial_values": "task_id:T:1:1 definition_id:T:1:2 definition_version:I:1:0 value_json:T:1:0",
            "values": "task_id:T:1:1 definition_id:T:1:2 definition_version:I:1:0 revision:I:1:0 present:I:1:0 value_json:T:0:0 updated_at:T:1:0",
            "value_events": "event_id:I:0:1 task_id:T:1:0 definition_id:T:1:0 definition_version:I:1:0 revision:I:1:0 old_present:I:1:0 new_present:I:1:0 old_value_json:T:0:0 new_value_json:T:0:0 note:T:0:0 actor_id:T:1:0 actor_kind:T:1:0 at:T:1:0 source:T:1:0"}
        foreign = {
            "definitions": {("definition_id", "task_attribute_definition_versions", "definition_id"), ("current_version", "task_attribute_definition_versions", "version")},
            "definition_versions": {("definition_id", "task_attribute_definitions", "definition_id")},
            "key_owners": {("definition_id", "task_attribute_definitions", "definition_id")},
            "admissions": {("task_id", "tasks", "id")},
            "initial_values": {("task_id", "task_attribute_admissions", "task_id"), ("definition_id", "task_attribute_definition_versions", "definition_id"), ("definition_version", "task_attribute_definition_versions", "version")},
            "values": {("task_id", "tasks", "id"), ("definition_id", "task_attribute_definition_versions", "definition_id"), ("definition_version", "task_attribute_definition_versions", "version")},
            "value_events": {("task_id", "tasks", "id"), ("definition_id", "task_attribute_definition_versions", "definition_id"), ("definition_version", "task_attribute_definition_versions", "version")}}
        indexes = {"idx_task_attribute_definitions_active": ("archived_at", "definition_id"),
                   "idx_task_attribute_versions_key": ("key", "definition_id", "version"),
                   "idx_task_attribute_keys_owner": ("definition_id", "key"),
                   "idx_task_attribute_values_definition": ("definition_id", "task_id"),
                   "idx_task_attribute_events_task": ("task_id", "event_id")}
        def bundle_layout(db):
            return [tuple(r) for r in db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE tbl_name LIKE 'task_attribute_%' ORDER BY type,name")]
        def check_manifest(db):
            for suffix, fields in manifest.items():
                table = "task_attribute_" + suffix
                expected = [(n, {"T": "TEXT", "I": "INTEGER"}[t], int(nn), int(pk))
                            for n, t, nn, pk in (field.split(":") for field in fields.split())]
                columns = [(r["name"], r["type"], r["notnull"], r["pk"]) for r in db.execute(f"PRAGMA table_info({table})")]
                self.assertEqual(columns, expected, table)
                fks = list(db.execute(f"PRAGMA foreign_key_list({table})"))
                self.assertEqual({(r["from"], r["table"], r["to"]) for r in fks}, foreign[suffix], table)
                self.assertTrue(all(r["on_delete"] == "NO ACTION" for r in fks))
                # Composite version reference is one FK group, never two unrelated references.
                if suffix in ("definitions", "initial_values", "values", "value_events"):
                    groups = {}
                    for r in fks:
                        groups.setdefault(r["id"], []).append((r["from"], r["to"]))
                    self.assertIn([("definition_id", "definition_id"),
                                   ("current_version" if suffix == "definitions" else "definition_version", "version")], list(groups.values()))
                unique = {tuple(r["name"] for r in db.execute(f"PRAGMA index_info({idx['name']})"))
                          for idx in db.execute(f"PRAGMA index_list({table})") if idx["unique"]}
                pk = tuple(r[0] for r in sorted(expected, key=lambda r: r[3]) if r[3])
                if suffix != "value_events":
                    self.assertIn(pk, unique, table)
                else:
                    self.assertIn(("task_id", "definition_id", "revision"), unique)
            for name, columns in indexes.items():
                self.assertEqual(tuple(r["name"] for r in db.execute(f"PRAGMA index_info({name})")), columns)
            triggers = {r[0]: r[1] for r in db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name LIKE 'task_attribute_%'")}
            self.assertEqual(set(triggers), {table + "_immutable_" + op for table in IMMUTABLE for op in ("update", "delete")})
            for table in IMMUTABLE:
                for op in ("update", "delete"):
                    sql = triggers[table + "_immutable_" + op].upper()
                    self.assertIn("BEFORE " + op.upper(), sql)
                    self.assertIn("RAISE(ABORT", sql)
            ddl = {r[0]: r[1] for r in db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name LIKE 'task_attribute_%'")}
            self.assertIn("DEFERRABLE INITIALLY DEFERRED", ddl[TABLES[0]])
            for suffix, checks in {"definitions": ("current_version >= 1",), "definition_versions": ("version >= 1", "'numeric', 'string', 'boolean'"),
                                   "values": ("revision >= 1", "present IN (0, 1)", "present = 0 AND value_json IS NULL", "json_valid(value_json)"),
                                   "initial_values": ("json_valid(value_json)", "'integer', 'real', 'text', 'true', 'false'"),
                                   "value_events": ("revision >= 1", "old_present IN (0, 1)", "new_present IN (0, 1)", "old_value_json IS NULL", "new_value_json IS NULL", "'admission', 'edit', 'clear'")}.items():
                for check in checks:
                    self.assertIn(check, ddl["task_attribute_"+suffix])
            self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(list(db.execute("PRAGMA foreign_key_check")), [])
        self.reopen()  # production installation on a synthetic pre-feature DB
        check_manifest(self.db)
        self.assertEqual(self.state(), {table: [] for table in TABLES})
        self.assertEqual(old_layout(self.db), old_schema)
        self.assertEqual(retained(self.db), old_rows)
        for table, columns in old_columns.items():
            self.assertEqual([tuple(r) for r in self.db.execute(f"PRAGMA table_info({table})")], columns)
        d = self.create()
        self.set_value(d, 4)
        written = self.state()
        self.reopen()  # idempotent rerun retains a real post-upgrade write
        check_manifest(self.db)
        self.assertEqual(self.state(), written)
        self.assertEqual(retained(self.db), old_rows)
        self.assertEqual(old_layout(self.db), old_schema)
        fresh = Database(Path(self.scratch.name) / "fresh.db")
        self.handles.append(fresh)
        check_manifest(fresh)
        self.assertEqual(bundle_layout(fresh), bundle_layout(self.db))
        self.assertEqual(self.projection(d)["initial"], {"state": "unknown"})
        self.assertEqual(self.projection(d)["history"]["history_state"], "incomplete")
        self.assertEqual([tuple(r) for r in self.db.execute("SELECT * FROM audit_log ORDER BY id")], audit)
        for row in audit:
            logs = self.db.get_audit_logs(row[1])
            self.assertEqual([r["id"] for r in logs], [row[0]])
        self.assertEqual(len(self.projection(d)["history"]["events"]), 1)

    def test_c18_history_suffix_fixed_shape_and_read_failure(self):
        d = self.create(description="old meaning")
        self.db.execute("UPDATE task_attribute_definitions SET created_at='2026-11-01T07:00:00Z'")
        self.db.commit()
        self.assertEqual(self.db.get_task_attribute_history("TASK-1"), {"history_state": "unavailable", "events": []})
        self.task("TASK-empty")
        self.admit({}, "TASK-empty")
        self.assertEqual(self.db.get_task_attribute_history("TASK-empty"), {"history_state": "complete", "events": []})
        for i, value in enumerate((4, 5, None)):
            self.clear(d, i) if value is None else self.set_value(d, value, i)
            self.reopen()
            p = json.loads(json.dumps(self.projection(d)))
            self.assertEqual(p["history"]["history_state"], "incomplete")
            self.assertEqual(len(p["history"]["events"]), i+1)
            self.assertEqual(p["initial"], {"state": "unknown"})
            self.assertEqual(p["admission"], {"state": "unavailable"})
            self.assertEqual(p["provenance"], "timing_unknown")
        suffix = self.projection(d)["history"]
        self.assertEqual([e["source"] for e in suffix["events"]], ["edit", "edit", "clear"])
        self.assertEqual([e["old"] for e in suffix["events"]], [{"present": False}, {"present": True, "value": 4}, {"present": True, "value": 5}])
        self.assertEqual([e["new"] for e in suffix["events"]], [{"present": True, "value": 4}, {"present": True, "value": 5}, {"present": False}])
        self.db.update_task_attribute_definition(d["definition_id"], expected_version=1, name="Renamed", description="new meaning")
        self.db.archive_task_attribute_definition(d["definition_id"], expected_version=2)
        self.assertEqual(self.projection(d)["history"], suffix)
        def deny_read(action, table, column, database, source):
            return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and table == "task_attribute_value_events" else sqlite3.SQLITE_OK
        self.db._conn.set_authorizer(deny_read)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                self.db.get_task_attribute_history("TASK-1")
        finally:
            self.db._conn.set_authorizer(None)
        self.assertEqual(self.projection(d)["history"], suffix)


if __name__ == "__main__":
    unittest.main()

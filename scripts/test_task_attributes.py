"""Accepted PR1 C01-C18 and PR2 P01-P28 in the standalone unittest venue.

Real SQLite and shipping ASGI app; no lifespan, listeners, providers, retired
unit-suite import, live org or production test seam.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import AsyncExitStack
import asyncio
from unittest.mock import patch

from scripts.test_agent_retirement import fixture as route_fixture

from runtime.infrastructure.database import Database
from runtime.infrastructure.db.task_attributes import AttributeConflictError, AttributeStoreError
from runtime.models import TaskRecord, TaskStatus, ThreadRecord
from runtime.orchestrator.teams import TeamsRegistry
from runtime.task_attributes import AttributeValidationError, validate_definition, validate_values


ACTOR = {"actor_id": "actor-founder", "actor_kind": "founder"}
TABLES = ["task_attribute_" + suffix for suffix in (
    "definitions", "definition_versions", "key_owners", "admissions", "initial_values", "values", "value_events")]
IMMUTABLE = [TABLES[i] for i in (1, 2, 3, 4, 6)]


def definition(kind="numeric", **changes):
    return validate_definition(**{"key": "effort", "type": kind, "name": " Effort ", **changes})


class RouteTests(unittest.IsolatedAsyncioTestCase):
    """Accepted PR2 P cases: shipping ASGI app, real isolated org and SQLite."""

    async def asyncSetUp(self):
        self.stack = AsyncExitStack()
        self.tmp = tempfile.TemporaryDirectory()
        self.org, self.client = await self.stack.enter_async_context(
            route_fixture(Path(self.tmp.name) / "fixture", workers=("worker", "sibling")))
        self.base = "/api/v1/orgs/alpha"
        self.defs = self.base + "/task-attributes/definitions"
        self.values = self.base + "/tasks/TASK-target/attributes"
        self.org.db.insert_task(TaskRecord(id="TASK-parent", assigned_agent="keeper", team="engineering", brief="parent"))
        self.org.db.insert_task(TaskRecord(id="TASK-target", assigned_agent="worker", team="retiring",
                                           parent_task_id="TASK-parent", brief="target"))
        for agent, team in (("worker", "retiring"), ("sibling", "retiring"), ("manager", "retiring"), ("keeper", "engineering")):
            sid = "synthetic-" + agent
            self.org.db.insert_task(TaskRecord(id="TASK-caller-" + agent, assigned_agent=agent, team=team,
                brief="caller", status=TaskStatus.IN_PROGRESS, current_session_id=sid))
            self.org.sessions.set_active("TASK-caller-" + agent, agent, sid, org_slug="alpha")

    async def asyncTearDown(self):
        await self.stack.aclose()
        self.tmp.cleanup()

    async def create(self, key="effort", **fields):
        response = await self.client.post(self.defs, json={"expected_version": 0, "key": key,
            "type": "numeric", "name": key.title(), "description": "Synthetic rubric", **fields})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["definition"]

    def change(self, definition, value=4, revision=0, **fields):
        return {"definition_id": definition["definition_id"], "expected_version": definition["version"],
                "expected_revision": revision, "op": "set", "value": value, **fields}

    async def save(self, changes, agent=None):
        return await self.client.put(self.values, json={"changes": changes},
            params={} if agent is None else {"session_id": "synthetic-" + agent})

    async def test_p01_p03_definition_routes(self):
        d = await self.create(constraints={"min": 1, "max": 10})
        self.assertEqual((d["description"], d["version"], d["archived"]), ("Synthetic rubric", 1, False))
        response = await self.client.get(self.defs)
        self.assertEqual(response.json()["definitions"], [d])
        response = await self.client.patch(self.defs + "/" + d["definition_id"],
            json={"expected_version": 1, "name": "New name", "description": None})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["definition"]["description"], None)
        old = await self.client.get(self.defs + "/" + d["definition_id"], params={"version": 1})
        self.assertEqual(old.json()["definition"], d)

    def domain(self):
        path = self.org.db.execute("PRAGMA database_list").fetchone()[2]
        observer = sqlite3.connect(path)
        try:
            return {table: observer.execute(f"SELECT * FROM {table}").fetchall()
                    for table in TABLES + ["audit_log"]}
        finally:
            observer.close()

    def audit(self, scope="TASK-target"):
        return self.org.db.get_audit_logs(scope)

    async def test_p02_p08_human_controls_and_actor_claims(self):
        d = await self.create()
        url = self.defs + "/" + d["definition_id"]
        before = self.domain()
        operations = (("post", self.defs, {"expected_version": 0, "key": "new", "name": "New", "type": "boolean"}),
                      ("patch", url, {"expected_version": 1, "description": "changed"}),
                      ("post", url + "/archive", {"expected_version": 1}))
        for method, path, body in operations:
            for sid in ("synthetic-worker", "unknown", ""):
                response = await getattr(self.client, method)(path, params={"session_id": sid}, json=body)
                self.assertEqual(response.status_code, 403, response.text)
                self.assertEqual(response.json()["detail"]["code"], "human_only")
            for field in ("actor_id", "agent", "session_id", "principal_id", "org_slug"):
                response = await getattr(self.client, method)(path, json={**body, field: "founder"})
                self.assertEqual(response.status_code, 403, response.text)
            for bearer in ("", "Bearer invalid"):
                response = await getattr(self.client, method)(path, json=body, headers={"Authorization": bearer})
                self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self.domain(), before)
        for field in ("actor_id", "actor_kind", "role", "team", "assigned_agent", "org"):
            response = await self.save([self.change(d, **{field: "founder"})], "worker")
            self.assertEqual(response.status_code, 422, response.text)
        response = await self.client.put(self.values, params={"session_id": "synthetic-worker"},
            json={"changes": [self.change(d)]}, headers={"X-HappyRanch-Caller": "founder"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.audit()[0]["agent"], "worker")
        self.assertEqual(response.json()["attributes"][0]["history"]["events"][0]["actor_kind"], "agent")

    async def test_p03_p04_p05_discovery_and_tenant_scope(self):
        d = await self.create(description="雪" * 4000)
        response = await self.client.get(self.defs, params={"session_id": "synthetic-worker"},
                                         headers={"Authorization": ""})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["definitions"][0]["description"], "雪" * 4000)
        self.assertFalse(response.json()["editability"]["can_create"])
        for path in (self.values, self.values + "/history", self.base + "/tasks/TASK-target"):
            response = await self.client.get(path, params={"session_id": "synthetic-worker"}, headers={"Authorization": ""})
            self.assertEqual(response.status_code, 401, response.text)
        response = await self.client.put(self.values, json={"changes": [self.change(d)]},
            params={"session_id": "synthetic-worker"}, headers={"Authorization": ""})
        self.assertEqual(response.status_code, 401)
        # A second mounted real org deliberately repeats task/agent/key IDs.
        from runtime.daemon.org_state import OrgState
        import shutil
        beta_root = self.org.root.parent / "beta"
        shutil.copytree(self.org.root / "org", beta_root / "org")
        beta = OrgState.load(slug="beta", root=beta_root, settings=self.org.settings)
        state = self.client._transport.app.state.daemon
        state.orgs["beta"] = beta
        beta.db.insert_task(TaskRecord(id="TASK-target", assigned_agent="worker", team="retiring", brief="beta"))
        foreign = beta.db.create_task_attribute_definition(key="effort", type="string", name="Beta", description="SYNTHETIC-BETA")
        beta.db.set_task_attribute("TASK-target", foreign["definition_id"], "SYNTHETIC-BETA-VALUE",
            expected_version=1, expected_revision=0, actor_id="founder", actor_kind="founder")
        beta_before = beta.db.get_task_attribute("TASK-target", foreign["definition_id"])
        response = await self.client.get("/api/v1/orgs/beta/task-attributes/definitions",
                                         params={"session_id": "synthetic-worker"})
        self.assertEqual(response.status_code, 403, response.text)
        errors = []
        for identity in (foreign["definition_id"], "unknown"):
            response = await self.client.get(self.defs + "/" + identity)
            self.assertEqual(response.status_code, 404, response.text)
            errors.append(response.json())
            response = await self.save([self.change({**d, "definition_id": identity})])
            self.assertEqual(response.json(), {"detail": {"code": "not_found"}})
        self.assertEqual(errors[0], errors[1])
        response = await self.client.get("/api/v1/orgs/gamma/task-attributes/definitions",
                                         params={"session_id": "synthetic-worker"})
        self.assertEqual(response.json(), {"detail": {"code": "not_found"}})
        self.assertEqual(beta.db.get_task_attribute("TASK-target", foreign["definition_id"]), beta_before)

    async def test_p06_p24_owner_managers_founder_audit(self):
        d = await self.create()
        for revision, agent in enumerate(("worker", "manager", "keeper", None)):
            with self.subTest(actor=agent):
                response = await self.save([self.change(d, value=revision, revision=revision, note="recorded")], agent)
                self.assertEqual(response.status_code, 200, response.text)
                item = response.json()["attributes"][0]
                self.assertEqual(item["current"]["revision"], revision + 1)
                event = item["history"]["events"][-1]
                self.assertEqual((event["actor_id"], event["actor_kind"]),
                    (agent or "founder", "agent" if agent else "founder"))
                row = self.audit()[-1]
                self.assertEqual((row["task_id"], row["agent"], row["action"]),
                                 ("TASK-target", agent or "founder", "task_attribute_set"))
                self.assertEqual(row["payload"], {"definition_id": d["definition_id"], "definition_version": 1,
                    "revision": revision + 1, "actor_id": agent or "founder", "actor_kind": "agent" if agent else "founder",
                    "old": {"present": False} if revision == 0 else {"present": True, "value": revision - 1},
                    "new": {"present": True, "value": revision}, "note": "recorded"})
                self.assertIsNotNone(datetime.fromisoformat(row["timestamp"]).utcoffset())
        clear = self.change(d, revision=4, op="clear")
        del clear["value"]
        for revision in (4, 5):
            response = await self.save([{**clear, "expected_revision": revision}])
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["attributes"][0]["current"]["revision"], revision + 1)
            self.assertEqual(self.audit()[-1]["action"], "task_attribute_cleared")
            self.assertEqual(self.audit()[-1]["payload"]["new"], {"present": False})

    async def assert_refusal(self, d, sid, expected):
        before = self.domain()
        response = await self.client.get(self.values, params={"session_id": sid})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["editability"], {"can_edit": False, "refusal_reason": {"code": expected}})
        response = await self.client.put(self.values, params={"session_id": sid}, json={"changes": [self.change(d)]})
        self.assertEqual((response.status_code, response.json()["detail"]["code"]), (403, expected), response.text)
        self.assertEqual(self.domain(), before)

    async def test_p07_p11_current_authority_refusal_parity(self):
        d = await self.create()
        await self.assert_refusal(d, "synthetic-sibling", "not_task_owner_or_owning_manager")
        await self.assert_refusal(d, "unknown", "authority_unknown")
        self.org.sessions.set_active("TASK-caller-worker", "worker", "new-worker", org_slug="alpha")
        await self.assert_refusal(d, "synthetic-worker", "authority_unknown")
        self.org.sessions.register_recovery_session("TASK-caller-worker", "worker", "synthetic-worker", org_slug="alpha")
        await self.assert_refusal(d, "synthetic-worker", "recovery_purpose_forbidden")
        # A generic manager loses its grant when the immediate parent is another owner.
        self.org.db.execute("UPDATE tasks SET assigned_agent='sibling' WHERE id='TASK-parent'")
        self.org.db.commit()
        await self.assert_refusal(d, "synthetic-keeper", "not_task_owner_or_owning_manager")
        # Direct owner remains provable when an optional parent link is broken.
        self.org.db.execute("UPDATE tasks SET parent_task_id='TASK-missing' WHERE id='TASK-target'")
        self.org.db.commit()
        await self.assert_refusal(d, "synthetic-keeper", "authority_unknown")
        self.org.sessions.set_active("TASK-caller-worker", "worker", "synthetic-worker", org_slug="alpha")
        response = await self.save([self.change(d)], "worker")
        self.assertEqual(response.status_code, 200, response.text)
        # Ambiguous manager registration never grants; an unknown team is not
        # repaired or guessed. This is a synthetic already-published layout.
        teams = self.org.teams
        self.org.teams = TeamsRegistry.from_bytes((self.org.root / "org/teams.yaml").read_bytes(), root=self.org.root)
        try:
            self.org.teams.add_team("ambiguous", manager="keeper")
            # Canonical membership is ambiguous, so no native actor/team proof
            # can be established; this is unknown authority, not a known peer.
            await self.assert_refusal(d, "synthetic-keeper", "authority_unknown")
        finally:
            teams.save()
            self.org.teams = teams
        canonical = self.org.root / "org/teams.yaml"
        original = canonical.read_bytes()
        try:
            canonical.write_text("teams: [")
            await self.assert_refusal(d, "synthetic-keeper", "authority_unknown")
        finally:
            canonical.write_bytes(original)
        self.org.db.execute("UPDATE tasks SET assigned_agent='sibling' WHERE id='TASK-target'")
        self.org.db.commit()
        await self.assert_refusal(d, "synthetic-worker", "not_task_owner_or_owning_manager")
        response = await self.save([self.change(d, revision=1)], "sibling")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.audit()[-1]["agent"], "sibling")

    async def test_p09_session_revocation_at_admission(self):
        d = await self.create()
        lock = self.org.workflow_authority._async_writer_lock
        arrived = asyncio.Event()

        class ObservedLock:
            async def __aenter__(self):
                arrived.set()
                await lock.acquire()

            async def __aexit__(self, *args):
                lock.release()

        await lock.acquire()
        self.org.workflow_authority._async_writer_lock = ObservedLock()
        try:
            pending = asyncio.create_task(self.save([self.change(d)], "worker"))
            await asyncio.wait_for(arrived.wait(), 5)
            self.org.sessions.set_active("TASK-caller-worker", "worker", "new-worker", org_slug="alpha")
        finally:
            lock.release()
        response = await asyncio.wait_for(pending, 5)
        self.org.workflow_authority._async_writer_lock = lock
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self.audit(), [])
        self.assertEqual(self.org.db.get_task_attribute("TASK-target", d["definition_id"])["current"]["revision"], 0)
        # Native retirement refuses the active credential-bearing manager. Do
        # not fabricate a successful unsupported retirement across that owner.
        response = await self.client.post(self.base + "/agents/manager/retire")
        self.assertEqual(response.status_code, 409, response.text)

    async def interleave(self, operation, competitor, *, snapshot=False):
        connection = self.org.db._conn
        observer = Database(Path(connection.execute("PRAGMA database_list").fetchone()[2]))

        class SnapshotGate(BeginGate):
            def execute(gate, sql, parameters=()):
                cursor = gate.connection.execute(sql, parameters)
                if gate.hits == 0 and "COALESCE(?,d.current_version)" in sql:
                    gate.hits += 1
                    gate.arrived.set()
                    if not gate.release.wait(5):
                        raise AssertionError("snapshot gate deadline")
                return cursor

        gate = SnapshotGate(connection) if snapshot else BeginGate(connection)
        self.org.db._conn = gate
        pending = asyncio.create_task(asyncio.to_thread(lambda: asyncio.run(operation())))
        try:
            self.assertTrue(await asyncio.to_thread(gate.arrived.wait, 5), "route reached actual SQL boundary")
            competitor(observer)
        finally:
            gate.release.set()
            try:
                response = await asyncio.wait_for(pending, 10)
            finally:
                self.org.db._conn = connection
                observer.close()
        self.assertEqual(gate.hits, 1)
        return response

    async def test_p14_p15_p16_p18_real_writer_interleavings(self):
        # Each losing route pauses immediately before its actual BEGIN, after
        # all transport/preflight work. Competitors commit through real PR1 APIs.
        for scenario in ("freeze", "revision", "definition", "archive"):
            with self.subTest(scenario=scenario):
                d = await self.create("race_" + scenario, constraints={"min": 1, "max": 10})
                x = await self.create("first_" + scenario)
                before_audit = len(self.audit())
                if scenario == "freeze":
                    operation = lambda: self.client.patch(self.defs + "/" + d["definition_id"],
                        json={"expected_version": 1, "constraints": {"min": 1, "max": 9}})
                    competitor = lambda db: db.set_task_attribute("TASK-target", d["definition_id"], 4,
                        expected_version=1, expected_revision=0, actor_id="worker", actor_kind="agent")
                    expected = "definition_structure_frozen"
                elif scenario == "revision":
                    operation = lambda: self.save([self.change(x), self.change(d, value=5)])
                    competitor = lambda db: db.set_task_attribute("TASK-target", d["definition_id"], 4,
                        expected_version=1, expected_revision=0, actor_id="worker", actor_kind="agent")
                    expected = "value_revision_conflict"
                elif scenario == "definition":
                    operation = lambda: self.save([self.change(x), self.change(d)])
                    competitor = lambda db: db.update_task_attribute_definition(d["definition_id"],
                        expected_version=1, constraints={"min": 1, "max": 9})
                    expected = "definition_version_conflict"
                else:
                    operation = lambda: self.save([self.change(x), self.change(d)])
                    competitor = lambda db: db.archive_task_attribute_definition(d["definition_id"], expected_version=1)
                    expected = "definition_version_conflict"
                response = await self.interleave(operation, competitor)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["detail"]["code"], expected)
                self.assertEqual(self.org.db.get_task_attribute("TASK-target", x["definition_id"])["current"]["revision"], 0)
                self.assertIsNone(self.org.db.get_task_attribute_definition(x["definition_id"])["first_used_at"])
                self.assertEqual(len(self.audit()), before_audit)
        # Opposite ordering: a committed save precedes archive, and then blocks.
        d = await self.create("save_wins")
        response = await self.save([self.change(d)])
        self.assertEqual(response.status_code, 200, response.text)
        response = await self.client.post(self.defs + "/" + d["definition_id"] + "/archive", json={"expected_version": 1})
        archived = response.json()["definition"]
        response = await self.save([self.change(archived, revision=1)])
        self.assertEqual(response.json()["detail"]["code"], "definition_archived")

    async def test_p25_coherent_sqlite_read_snapshot(self):
        d = await self.create()
        await self.save([self.change(d)])

        def competitor(db):
            db.set_task_attribute("TASK-target", d["definition_id"], 6, expected_version=1,
                expected_revision=1, actor_id="worker", actor_kind="agent")
            db.update_task_attribute_definition(d["definition_id"], expected_version=1, description="new rubric")

        response = await self.interleave(lambda: self.client.get(self.values), competitor, snapshot=True)
        self.assertEqual(response.status_code, 200, response.text)
        item = response.json()["attributes"][0]
        self.assertEqual((item["current"]["value"], item["current"]["revision"], len(item["history"]["events"])), (4, 1, 1))
        self.assertEqual(item["definition"]["description"], "Synthetic rubric")
        response = await self.client.get(self.values)
        item = response.json()["attributes"][0]
        self.assertEqual((item["current"]["value"], item["current"]["revision"], len(item["history"]["events"])), (6, 2, 2))
        self.assertEqual(item["definition"]["description"], "new rubric")

    async def test_p22_full_retained_history(self):
        d = await self.create()
        db = self.org.db
        # Real writes, batched only by the production uncommitted transaction
        # primitive. No mocked history, result or actor projection.
        with db._lock, db._attribute_writer():
            for i in range(2048):
                db._write_task_attribute_changes_uncommitted("TASK-target", [self.change(d, value=i % 2, revision=i)],
                    actor_id="founder" if i % 2 else "worker", actor_kind="founder" if i % 2 else "agent")
            for i in range(200):
                db._create_task_attribute_definition_uncommitted(key=f"fixture_{i}", type="boolean", name=f"Fixture {i}")
        started = time.monotonic()
        response = await self.client.get(self.values + "/history", params={"definition_id": d["definition_id"]})
        elapsed = time.monotonic() - started
        self.assertEqual(response.status_code, 200, response.text[:500])
        events = response.json()["events"]
        self.assertEqual(len(events), 2048)
        self.assertEqual([e["revision"] for e in events], list(range(1, 2049)))
        self.assertEqual([e["event_id"] for e in events], sorted(e["event_id"] for e in events))
        self.assertEqual(response.json()["history_state"], "incomplete")
        self.assertEqual(events[-1]["actor_kind"], "founder")
        print(f"P22 full history: {len(events)} events, 200 additional definitions, {len(response.content)} bytes, {elapsed:.3f}s")

    async def test_p10_finished_targets_live_caller(self):
        d = await self.create()
        for revision, status in enumerate(("completed", "failed", "cancelled", "superseded")):
            self.org.db.execute("UPDATE tasks SET status=? WHERE id='TASK-target'", (status,))
            self.org.db.commit()
            response = await self.client.get(self.values, params={"session_id": "synthetic-worker"})
            self.assertTrue(response.json()["editability"]["can_edit"], response.text)
            response = await self.save([self.change(d, revision=revision)], "worker")
            self.assertEqual(response.status_code, 200, response.text)
        self.org.db.insert_task(TaskRecord(id="TASK-paused-root", assigned_agent="worker", team="retiring",
                                           brief="paused metadata target", status=TaskStatus.IN_PROGRESS))
        response = await self.client.post(self.base + "/tasks/TASK-paused-root/pause", json={"expected_generation": 0})
        self.assertEqual(response.status_code, 200, response.text)
        response = await self.client.put(self.base + "/tasks/TASK-paused-root/attributes",
            params={"session_id": "synthetic-worker"}, json={"changes": [self.change(d)]})
        self.assertEqual(response.status_code, 200, response.text)
        self.org.db.execute("UPDATE tasks SET status='completed' WHERE id='TASK-caller-worker'")
        self.org.db.commit()
        await self.assert_refusal(d, "synthetic-worker", "task_not_active")

    async def test_p12_transport_validation(self):
        d = await self.create(constraints={"min": 1, "max": 10, "integer_only": True})
        before = self.domain()
        for rules in ({"required": True}, {"default": 4}, {"min": "1"}, {"integer_only": 1}):
            response = await self.client.post(self.defs, json={"expected_version": 0, "key": "invalid",
                "type": "numeric", "name": "Invalid", "constraints": rules})
            self.assertEqual(response.status_code, 422, response.text)
        for value in ("4", True, None, 0, 11, 4.5, [], {}):
            response = await self.save([self.change(d, value=value)])
            self.assertEqual(response.status_code, 422, response.text)
        for field in ("expected_version", "expected_revision"):
            for value in (True, "1", 1.5):
                response = await self.save([self.change(d, **{field: value})])
                self.assertEqual(response.status_code, 422, response.text)
        response = await self.save([self.change(d), self.change(d)])
        self.assertEqual(response.status_code, 422)
        response = await self.save([])
        self.assertEqual(response.status_code, 422)
        valid = json.dumps([self.change(d)])
        for body in ('{"changes":' + valid + ',"changes":' + valid + '}', '{"changes":NaN}', '{',
                     '{"changes":[{"op":"clear","value":null}]}'):
            response = await self.client.put(self.values, content=body, headers={"Content-Type": "application/json"})
            self.assertEqual(response.status_code, 422, response.text)
        response = await self.client.get(self.values + "?session_id=synthetic-worker&session_id=synthetic-sibling")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.domain(), before)
        for revision, value in enumerate((4, 4.0)):
            response = await self.save([self.change(d, value=value, revision=revision)])
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIs(type(response.json()["attributes"][0]["current"]["value"]), type(value))

    async def test_p13_p14_definition_conflicts_freeze_and_audit(self):
        d = await self.create(constraints={"min": 1, "max": 10})
        url = self.defs + "/" + d["definition_id"]
        response = await self.client.patch(url, json={"expected_version": 1, "key": "effort_new", "constraints": {"min": 1, "max": 9}})
        self.assertEqual(response.status_code, 200, response.text)
        current = response.json()["definition"]
        for path, method, body in ((url, "patch", {"expected_version": 1, "constraints": {"max": 8}}),
                                  (url + "/archive", "post", {"expected_version": 1}),
                                  (self.defs, "post", {"expected_version": 0, "key": "effort", "name": "Effort", "type": "numeric"})):
            response = await getattr(self.client, method)(path, json=body)
            self.assertEqual(response.status_code, 409, response.text)
            self.assertIn("current", response.json()["detail"], response.text)
            self.assertEqual(response.json()["detail"]["current"]["definition"], current)
        response = await self.save([self.change(current)])
        self.assertEqual(response.status_code, 200, response.text)
        for changes in ({"key": "new_key"}, {"type": "string", "constraints": {}}, {"constraints": {"max": 8}}):
            response = await self.client.patch(url, json={"expected_version": 2, **changes})
            self.assertEqual((response.status_code, response.json()["detail"]["code"]), (409, "definition_structure_frozen"))
            state = response.json()["detail"]["current"]
            self.assertEqual(state["definition"]["version"], 2)
            self.assertIsNotNone(state["definition"]["first_used_at"])
            self.assertFalse(state["editability"]["can_edit_structure"])
        response = await self.client.patch(url, json={"expected_version": 2, "description": "Reworded"})
        self.assertEqual(response.status_code, 200, response.text)

    async def test_p15_p16_p17_revision_archive_recovery(self):
        d = await self.create()
        response = await self.save([self.change(d)])
        self.assertEqual(response.status_code, 200, response.text)
        response = await self.save([self.change(d, value=6)])
        self.assertEqual((response.status_code, response.json()["detail"]["code"]), (409, "value_revision_conflict"))
        self.assertIn("conflicts", response.json()["detail"], response.text)
        self.assertEqual(response.json()["detail"]["conflicts"], [{"definition_id": d["definition_id"],
            "expected_revision": 0, "current_revision": 1}])
        self.assertEqual(response.json()["detail"]["current"]["attributes"][0]["current"]["value"], 4)
        response = await self.client.patch(self.defs + "/" + d["definition_id"], json={"expected_version": 1, "description": "v2"})
        self.assertEqual(response.status_code, 200)
        response = await self.save([self.change(d, revision=1)])
        self.assertEqual(response.json()["detail"]["code"], "definition_version_conflict")
        response = await self.client.post(self.defs + "/" + d["definition_id"] + "/archive", json={"expected_version": 2})
        self.assertEqual(response.status_code, 200, response.text)
        archived = response.json()["definition"]
        for version in (1, 3):
            for op in ("set", "clear"):
                change = self.change(archived, revision=1, op=op, expected_version=version)
                if op == "clear":
                    del change["value"]
                response = await self.save([change])
                self.assertEqual(response.status_code, 409, response.text)
                field = response.json()["detail"]["current"]["attributes"][0]
                self.assertEqual(field["definition"], archived)
                self.assertEqual(field["editability"], {"can_edit": False, "refusal_reason": {"code": "definition_archived"}})
        response = await self.client.get(self.defs + "/" + d["definition_id"], params={"version": 1})
        self.assertFalse(response.json()["editability"]["can_edit_metadata"])
        response = await self.client.get(self.defs)
        self.assertEqual(response.json()["definitions"], [])
        response = await self.client.get(self.defs, params={"include_archived": "true", "session_id": "synthetic-worker"})
        self.assertEqual(response.json()["definitions"], [archived])
        # An unused archived field is also concrete in a conflict envelope.
        unused = await self.create("unused")
        await self.client.post(self.defs + "/" + unused["definition_id"] + "/archive", json={"expected_version": 1})
        response = await self.save([self.change(unused)])
        self.assertTrue(any(a["definition"]["definition_id"] == unused["definition_id"] and a["definition"]["archived"]
                            for a in response.json()["detail"]["current"]["attributes"]))

    async def test_p19_atomic_validation_all_fields(self):
        x, y, z = await self.create("x"), await self.create("y"), await self.create("z")
        before = self.domain()
        for changes in ([self.change(x), self.change(y, value="invalid")],
                        [self.change(y, value="invalid"), self.change(x)],
                        [self.change(x), self.change(y, expected_revision=9)],
                        [self.change(x), self.change({**y, "definition_id": "unknown"})]):
            response = await self.save(changes)
            self.assertIn(response.status_code, (404, 409, 422), response.text)
            self.assertEqual(self.domain(), before)
        response = await self.save([self.change(x, value="bad"), self.change(y, value=True)])
        self.assertEqual(set(response.json()["detail"]["errors"]), {"changes.0.value", "changes.1.value"})
        clear = self.change(y, op="clear")
        del clear["value"]
        response = await self.save([self.change(x), clear])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.org.db.get_task_attribute("TASK-target", z["definition_id"])["current"], {"present": False, "revision": 0})
        self.assertEqual(len(self.audit()), 2)
        self.assertEqual(self.org.db.execute("SELECT count(*) FROM task_attribute_admissions").fetchone()[0], 0)

    async def test_p20_real_event_and_audit_fault_rollback(self):
        x, y = await self.create("x"), await self.create("y")
        for table, condition in (("task_attribute_value_events", f"NEW.definition_id='{y['definition_id']}'"),
                                 ("audit_log", "NEW.action='task_attribute_set' AND json_extract(NEW.payload,'$.definition_id')='" + y["definition_id"] + "'")):
            before = self.domain()
            self.org.db.execute(f"CREATE TEMP TRIGGER attribute_fault BEFORE INSERT ON {table} WHEN {condition} BEGIN SELECT RAISE(ABORT,'synthetic fault'); END")
            try:
                response = await self.save([self.change(x), self.change(y)])
                self.assertEqual(response.status_code, 503, response.text)
                self.assertEqual(response.json(), {"detail": {"code": "attribute_storage_unavailable"}})
                self.assertEqual(self.domain(), before)
            finally:
                self.org.db.execute("DROP TRIGGER attribute_fault")
        for operation in ("create", "update", "archive"):
            before = self.domain()
            self.org.db.execute("CREATE TEMP TRIGGER attribute_fault BEFORE INSERT ON audit_log WHEN NEW.action='org_config_write' BEGIN SELECT RAISE(ABORT,'synthetic fault'); END")
            try:
                if operation == "create":
                    response = await self.client.post(self.defs, json={"expected_version": 0, "key": "fault", "name": "Fault", "type": "boolean"})
                elif operation == "update":
                    response = await self.client.patch(self.defs + "/" + x["definition_id"], json={"expected_version": 1, "name": "New"})
                else:
                    response = await self.client.post(self.defs + "/" + x["definition_id"] + "/archive", json={"expected_version": 1})
                self.assertEqual(response.status_code, 503, response.text)
                self.assertEqual(self.domain(), before)
            finally:
                self.org.db.execute("DROP TRIGGER attribute_fault")
        response = await self.save([self.change(x), self.change(y)])
        self.assertEqual(response.status_code, 200, response.text)
        # Later set and explicit clear also roll back on a real audit fault.
        for op in ("set", "clear"):
            before = self.domain()
            change = self.change(x, revision=1, op=op)
            if op == "clear":
                del change["value"]
            self.org.db.execute("CREATE TEMP TRIGGER attribute_fault BEFORE INSERT ON audit_log WHEN NEW.action LIKE 'task_attribute_%' BEGIN SELECT RAISE(ABORT,'synthetic fault'); END")
            try:
                response = await self.save([change])
                self.assertEqual(response.status_code, 503, response.text)
                self.assertEqual(self.domain(), before)
            finally:
                self.org.db.execute("DROP TRIGGER attribute_fault")

    async def test_p21_p22_provenance_initial_history_and_scalars(self):
        d = await self.create()
        same_offset = datetime.fromisoformat(d["created_at"]).astimezone(timezone(timedelta(hours=8))).isoformat()
        # Reliable UTC-offset ordering, absence of admission, and complete-empty
        # admission are literal fixtures; only the PR1 admission owner writes it.
        for suffix, task_at, admitted, provenance, initial in (
            ("legacy", "2100-01-01T00:00:00+00:00", False, "timing_unknown", "unknown"),
            ("later", "2000-01-01T00:00:00+00:00", False, "definition_added_later", "unknown"),
            ("omitted", "2100-01-01T00:00:00+00:00", True, "not_supplied_before_dispatch", "unset"),
            ("emptylater", "2000-01-01T00:00:00+00:00", True, "definition_added_later", "unset"),
            ("naive", "2000-01-01T00:00:00", False, "timing_unknown", "unknown"),
            ("malformed", "invalid", False, "timing_unknown", "unknown"),
            ("same", d["created_at"], True, "timing_unknown", "unknown"),
            ("offset", same_offset, False, "timing_unknown", "unknown")):
            task_id = "TASK-" + suffix
            self.org.db.insert_task(TaskRecord(id=task_id, assigned_agent="worker", team="retiring", brief=suffix))
            self.org.db.execute("UPDATE tasks SET created_at=? WHERE id=?", (task_at, task_id))
            self.org.db.commit()
            if admitted:
                with self.org.db._lock, self.org.db._attribute_writer():
                    self.org.db._write_task_attributes_initial_uncommitted(task_id, {}, actor_id="founder", actor_kind="founder")
            response = await self.client.get(self.base + f"/tasks/{task_id}/attributes")
            self.assertEqual(response.status_code, 200, suffix + ": " + response.text)
            item = response.json()["attributes"][0]
            self.assertEqual((item["provenance"], item["initial"]["state"], item["history"]["history_state"]),
                             (provenance, initial, "complete" if admitted else "unavailable"))
            if not admitted:
                path = self.base + f"/tasks/{task_id}/attributes"
                response = await self.client.put(path, json={"changes": [self.change(d)]})
                self.assertEqual(response.status_code, 200, response.text)
                clear = self.change(d, revision=1, op="clear")
                del clear["value"]
                response = await self.client.put(path, json={"changes": [clear]})
                field = response.json()["attributes"][0]
                self.assertEqual((field["provenance"], field["initial"]["state"], field["history"]["history_state"]),
                                 (provenance, "unknown", "incomplete"))
                self.assertEqual([e["source"] for e in field["history"]["events"]], ["edit", "clear"])
        with self.org.db._lock, self.org.db._attribute_writer():
            self.org.db._write_task_attributes_initial_uncommitted("TASK-target", {"effort": 4}, actor_id="worker", actor_kind="agent")
        await self.save([self.change(d, value=5, revision=1)], "worker")
        clear = self.change(d, revision=2, op="clear")
        del clear["value"]
        response = await self.save([clear])
        item = response.json()["attributes"][0]
        self.assertEqual((item["initial"]["state"], item["initial"]["value"], item["initial"]["definition_version"]), ("set", 4, 1))
        self.assertEqual(item["history"]["history_state"], "complete")
        self.assertEqual([e["source"] for e in item["history"]["events"]], ["admission", "edit", "clear"])
        self.assertEqual(item["history"]["events"][-1]["actor"], {"canonical_id": "founder", "kind": "founder", "addressable_name": "founder"})
        ready = self.org.naming_readiness
        self.org.naming_readiness = "unavailable"
        try:
            response = await self.client.get(self.values + "/history")
            self.assertEqual(response.json()["events"][-1]["actor"],
                {"canonical_id": "founder", "kind": "founder", "addressable_name": None})
        finally:
            self.org.naming_readiness = ready
        for key, kind, value in (("zero", "numeric", 0), ("boolean", "boolean", False), ("empty", "string", "")):
            definition = await self.create(key, type=kind)
            response = await self.save([self.change(definition, value=value)])
            item = next(a for a in response.json()["attributes"] if a["definition"]["key"] == key)
            self.assertIs(type(item["current"]["value"]), type(value))
            self.assertEqual(item["current"]["value"], value)
        response = await self.client.patch(self.defs + "/" + d["definition_id"], json={"expected_version": 1, "description": "new rubric"})
        self.assertEqual(response.status_code, 200, response.text)
        response = await self.client.post(self.defs + "/" + d["definition_id"] + "/archive", json={"expected_version": 2})
        self.assertEqual(response.status_code, 200, response.text)
        response = await self.client.get(self.values)
        field = next(a for a in response.json()["attributes"] if a["definition"]["definition_id"] == d["definition_id"])
        self.assertEqual(field["initial"]["definition"]["description"], "Synthetic rubric")
        self.assertEqual(field["definition"]["description"], "new rubric")
        self.assertTrue(field["definition"]["archived"])

    async def test_p23_definition_audit_scope_shape(self):
        for scope in ("TASK-other", "THR-old", "artifact:old", "config:other"):
            self.org.db.insert_audit_log(scope, "old", "retained", {"literal": scope})
        retained = [tuple(r) for r in self.org.db.execute("SELECT * FROM audit_log").fetchall()]
        d = await self.create()
        response = await self.client.patch(self.defs + "/" + d["definition_id"], json={"expected_version": 1, "description": "v2"})
        changed = response.json()["definition"]
        response = await self.client.post(self.defs + "/" + d["definition_id"] + "/archive", json={"expected_version": 2})
        archived = response.json()["definition"]
        rows = self.audit("config:task_attributes")
        self.assertEqual(len(rows), 3)
        for row, operation, before, after in zip(rows, ("create", "update", "archive"), (None, d, changed), (d, changed, archived)):
            self.assertEqual((row["agent"], row["action"]), ("founder", "org_config_write"))
            self.assertEqual(row["payload"], {"section": "task_attributes", "tiers": ["org"], "operation": operation,
                "definition_id": d["definition_id"], "before": before, "after": after, "actor_kind": "founder"})
        self.assertEqual([tuple(r) for r in self.org.db.execute("SELECT * FROM audit_log ORDER BY id LIMIT ?", (len(retained),)).fetchall()], retained)

    async def test_p26_full_openapi_models_and_classification(self):
        schema = self.client._transport.app.openapi()
        operations = (("get", "/task-attributes/definitions"), ("post", "/task-attributes/definitions"),
            ("get", "/task-attributes/definitions/{definition_id}"), ("patch", "/task-attributes/definitions/{definition_id}"),
            ("post", "/task-attributes/definitions/{definition_id}/archive"), ("get", "/tasks/{task_id}/attributes"),
            ("put", "/tasks/{task_id}/attributes"), ("get", "/tasks/{task_id}/attributes/history"))
        classification = json.loads((Path(__file__).resolve().parents[1] / "tests/contract/route-classification.json").read_text())
        for method, path in operations:
            full = "/api/v1/orgs/{slug}" + path
            self.assertIn(method, schema["paths"][full])
            self.assertEqual(classification["included"].count(method.upper() + " " + full), 1)
            op = schema["paths"][full][method]
            self.assertIn("503", op["responses"])
            self.assertIn("schema", op["responses"]["201" if method == "post" and path.endswith("definitions") else "200"]["content"]["application/json"])
        save = schema["paths"]["/api/v1/orgs/{slug}/tasks/{task_id}/attributes"]["put"]["requestBody"]["content"]["application/json"]["schema"]
        self.assertFalse(save["additionalProperties"])
        change = save["properties"]["changes"]["items"]
        self.assertEqual(change["discriminator"]["propertyName"], "op")
        self.assertEqual(len(change["oneOf"]), 2)
        self.assertNotIn("#/$defs/", json.dumps(save))
        models = schema["components"]["schemas"]
        self.assertIn("current", models["ErrorDetail"]["properties"])
        self.assertIn("editability", models["TaskAttributesRead"]["properties"])
        self.assertIn("actor_kind", models["Event"]["properties"])

    async def test_p27_p28_read_failures_lost_response_and_cutline(self):
        response = await self.client.get(self.values)
        self.assertEqual(response.json()["attributes"], [])
        before = [tuple(r) for r in self.org.db.execute("SELECT * FROM tasks ORDER BY id").fetchall()]
        results = [tuple(r) for r in self.org.db.execute("SELECT * FROM task_results").fetchall()]
        queue = self.client._transport.app.state.daemon.queue
        queued = queue._queue.qsize()
        d = await self.create()
        # A real SQL read failure, not a mocked empty projection.
        self.org.db.execute("ALTER TABLE task_attribute_values RENAME TO synthetic_missing_values")
        try:
            response = await self.client.get(self.values)
            self.assertEqual(response.status_code, 503, response.text)
        finally:
            self.org.db.execute("ALTER TABLE synthetic_missing_values RENAME TO task_attribute_values")
        transport = self.client._transport
        original = transport.handle_async_request

        async def lost(request):
            result = await original(request)
            if request.method == "PUT" and result.status_code == 200:
                raise OSError("synthetic response lost after COMMIT")
            return result
        with patch.object(transport, "handle_async_request", lost):
            with self.assertRaisesRegex(OSError, "after COMMIT"):
                await self.save([self.change(d)])
        response = await self.client.get(self.values)
        self.assertEqual(response.json()["attributes"][0]["current"]["revision"], 1)
        response = await self.save([self.change(d)])
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(len(self.audit()), 1)
        self.assertEqual([tuple(r) for r in self.org.db.execute("SELECT * FROM tasks ORDER BY id").fetchall()], before)
        self.assertEqual([tuple(r) for r in self.org.db.execute("SELECT * FROM task_results").fetchall()], results)
        self.assertEqual(queue._queue.qsize(), queued)


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

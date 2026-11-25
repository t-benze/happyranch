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
from contextlib import AsyncExitStack, closing
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

    async def test_no_store_through_shipping_app(self):
        import httpx
        from runtime.daemon.app import create_app

        dist = Path(self.tmp.name) / "web-dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html>Synthetic SPA</html>")
        state = self.client._transport.app.state.daemon
        for mode, web_dist in (("placeholder", None), ("spa", dist)):
            with patch("runtime.daemon.routes.web_static._resolve_dist_dir", return_value=web_dist):
                app = create_app(state)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                    base_url="http://synthetic.test", headers=self.client.headers) as client:
                async def success(method, path, status=200, **kwargs):
                    response = await client.request(method, path, **kwargs)
                    self.assertEqual(response.status_code, status, response.text)
                    self.assertEqual(response.headers.get("cache-control"), "no-store", (mode, method, path))
                    return response.json()

                command = {"expected_version": 0, "key": "cache_" + mode, "type": "numeric", "name": "Cache"}
                d = (await success("POST", self.defs, 201, json=command))["definition"]
                url = self.defs + "/" + d["definition_id"]
                await success("GET", self.defs)
                current = await success("PUT", self.values, json={"changes": [self.change(d)]})
                definition_read = await success("GET", url)
                self.assertTrue(definition_read["definition"]["first_used_at"])
                await success("GET", self.values)
                history = await success("GET", self.values + "/history",
                                        params={"definition_id": d["definition_id"]})
                self.assertEqual(len(history["events"]), 1)
                self.assertEqual(history["events"][0]["new"], {"present": True, "value": 4})

                cases = (
                    ("missing bearer", "GET", self.values, 401, {"headers": {"Authorization": ""}},
                     {"detail": "missing bearer token"}),
                    ("human control", "POST", self.defs, 403,
                     {"params": {"session_id": "synthetic-worker"}, "json": command},
                     {"detail": {"code": "human_only"}}),
                    ("missing definition", "GET", self.defs + "/missing", 404, {},
                     {"detail": {"code": "not_found"}}),
                    ("stale definition", "PATCH", url, 409,
                     {"json": {"expected_version": 2, "name": "Stale"}},
                     {"detail": {"code": "definition_version_conflict", "current": definition_read}}),
                    ("stale value with history", "PUT", self.values, 409,
                     {"json": {"changes": [self.change(d)]}},
                     {"detail": {"code": "value_revision_conflict", "current": current, "conflicts": [
                         {"definition_id": d["definition_id"], "expected_revision": 0, "current_revision": 1}]}}),
                    ("invalid body", "PUT", self.values, 422, {"json": {"changes": []}},
                     {"detail": {"code": "attribute_validation_failed", "errors": {"changes": ["too_short"]}}}),
                    ("invalid query", "GET", url, 422, {"params": {"version": "invalid"}},
                     {"detail": {"code": "attribute_validation_failed", "errors": {"query.version": ["int_parsing"]}}}),
                )
                before = self.domain()
                for name, method, path, status, kwargs, body in cases:
                    with self.subTest(mode=mode, case=name, status=status):
                        response = await client.request(method, path, **kwargs)
                        self.assertEqual(response.status_code, status, response.text)
                        self.assertEqual(response.json(), body)
                        self.assertEqual(response.headers.get("cache-control"), "no-store")
                self.assertEqual(self.domain(), before)
                await success("PATCH", url, json={"expected_version": 1, "name": "Renamed"})
                await success("POST", url + "/archive", json={"expected_version": 2})

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

class AdmissionTests(unittest.TestCase):
    """H01/H02: shared adapter retains the real caller's transaction."""
    setUp = StorageTests.setUp
    tearDown = StorageTests.tearDown
    task = StorageTests.task
    create = StorageTests.create
    observer = StorageTests.observer

    def test_h01_h02_owned_writer_visibility_rollback_and_own_inputs(self):
        from runtime.task_attribute_admission import validate_initial_attributes, write_initial_attributes_uncommitted as write
        d = self.create(constraints={"min": 1, "max": 10})
        observer = self.observer()
        with self.assertRaisesRegex(AttributeStoreError, "owned_transaction_required"):
            write(self.db, "TASK-1", {}, **ACTOR)
        for commit, child_value in ((False, {}), (True, {})):
            with self.subTest(commit=commit, child=child_value):
                self.db._conn.execute("BEGIN IMMEDIATE")
                for task_id, values in (("TASK-A", {"effort": 4}), ("TASK-B", child_value)):
                    validate_initial_attributes(self.db, values)
                    self.db._insert_task_uncommitted(TaskRecord(id=task_id, brief="Synthetic shared admission", parent_task_id="TASK-A" if task_id == "TASK-B" else "TASK-1"))
                    write(self.db, task_id, values, **ACTOR)
                    self.assertTrue(self.db._conn.in_transaction)
                    self.assertIsNone(observer.execute("SELECT id FROM tasks WHERE id=?", (task_id,)).fetchone())
                with self.assertRaises(AttributeValidationError):
                    write(self.db, "TASK-1", {"effort": 11}, **ACTOR)
                self.assertTrue(self.db._conn.in_transaction)
                self.assertIsNotNone(self.db.execute("SELECT id FROM tasks WHERE id='TASK-A'").fetchone())
                if commit:
                    self.db._conn.commit()
                    projection = observer.get_task_attribute("TASK-B", d["definition_id"])
                    self.assertEqual(projection["current"]["present"], bool(child_value))
                    self.assertEqual(len(observer.get_task_attribute_history("TASK-B")["events"]), len(child_value))
                else:
                    self.db._conn.rollback()
                    self.assertIsNone(observer.execute("SELECT id FROM tasks WHERE id='TASK-A'").fetchone())
                    self.assertEqual(observer.execute("SELECT COUNT(*) FROM task_attribute_admissions").fetchone()[0], 0)
        # Explicit own input on another child never reuses A's value.
        self.db._conn.execute("BEGIN IMMEDIATE")
        self.db._insert_task_uncommitted(TaskRecord(id="TASK-C", brief="Synthetic child own values", parent_task_id="TASK-A"))
        write(self.db, "TASK-C", {"effort": 6}, **ACTOR)
        self.db._conn.commit()
        self.assertEqual(observer.get_task_attribute("TASK-C", d["definition_id"])["current"]["value"], 6)


class CreationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = RouteTests.asyncSetUp
    asyncTearDown = RouteTests.asyncTearDown
    create = RouteTests.create
    change = RouteTests.change
    interleave = RouteTests.interleave

    def inventory(self):
        path = self.org.db.execute("PRAGMA database_list").fetchone()[2]
        # Open/close twice: neither a writer-local read nor a retained snapshot
        # can mask a committed orphan after a refusal.
        tables = TABLES + ["tasks", "workflow_task_ownership", "task_attachments", "audit_log"]
        with closing(sqlite3.connect(path)) as conn:
            first = {name: conn.execute(f"SELECT * FROM {name} ORDER BY rowid").fetchall() for name in tables}
        with closing(sqlite3.connect(path)) as conn:
            self.assertEqual(first, {name: conn.execute(f"SELECT * FROM {name} ORDER BY rowid").fetchall() for name in tables})
        return first

    async def submit(self, attributes=None, *, omitted=False, **fields):
        return await self.client.post(self.base + "/tasks", json={"brief": "Synthetic PR3", "owner": "worker",
            **({} if omitted else {"attributes": attributes}), **fields})

    def manifest(self, task_id, values):
        path = self.org.db.execute("PRAGMA database_list").fetchone()[2]
        with closing(sqlite3.connect(path)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tasks WHERE id=?", (task_id,)).fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM workflow_task_ownership WHERE task_id=?", (task_id,)).fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT actor_id,actor_kind FROM task_attribute_admissions WHERE task_id=?", (task_id,)).fetchall(), [("founder", "founder")])
            stored = conn.execute("SELECT v.key,i.value_json FROM task_attribute_initial_values i JOIN task_attribute_definition_versions v ON v.definition_id=i.definition_id AND v.version=i.definition_version WHERE i.task_id=?", (task_id,)).fetchall()
            self.assertEqual({key: json.loads(value) for key, value in stored}, values)
            for key, raw in stored:
                self.assertIs(type(json.loads(raw)), type(values[key]))
            for table in ("task_attribute_initial_values", "task_attribute_values", "task_attribute_value_events"):
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE task_id=?", (task_id,)).fetchone()[0], len(values))
            audits = conn.execute("SELECT payload FROM audit_log WHERE task_id=? AND action='task_attribute_set' ORDER BY id", (task_id,)).fetchall()
            self.assertEqual(len(audits), len(values))
            for payload, value in zip(audits, values.values()):
                p = json.loads(payload[0])
                self.assertEqual(set(p), {"definition_id", "definition_version", "revision", "actor_id", "actor_kind", "old", "new"})
                self.assertEqual((p["revision"], p["actor_id"], p["actor_kind"], p["old"], p["new"]),
                    (1, "founder", "founder", {"present": False}, {"present": True, "value": value}))

    async def test_r01_r06_r11_r12_admission_types_omission_and_queue(self):
        effort = await self.create(constraints={"min": 1, "max": 10, "integer_only": True})
        await self.create("label", type="string")
        await self.create("ready", type="boolean")
        await self.create("free")
        queue = self.client._transport.app.state.daemon.queue
        from runtime.daemon.runner import enqueue_task
        seen = []
        def observe(state, slug, task_id):
            self.manifest(task_id, expected)
            seen.append(task_id)
            enqueue_task(state, slug, task_id)
        for values in ({"effort": 1, "label": "  é🙂=x  ", "ready": False, "free": 9007199254740993},
                       {"effort": 10, "label": "", "ready": True, "free": 10**100},
                       {"effort": 4.0, "free": 0}, {}, None):
            expected = values or {}
            with self.subTest(values=values), patch("runtime.daemon.routes.tasks.enqueue_task", observe):
                before = queue._queue.qsize()
                response = await self.submit(values, omitted=values is None)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual((response.json()["assigned_agent"], response.json()["team"]), ("worker", "retiring"))
                task_id = response.json()["task_id"]
                self.assertEqual(queue._queue.qsize(), before + 1)
                self.assertEqual(seen[-1], task_id)
                read = (await self.client.get(self.base + f"/tasks/{task_id}/attributes")).json()
                field = next(a for a in read["attributes"] if a["definition"]["key"] == "effort")
                if "effort" not in expected:
                    self.assertEqual(field["provenance"], "not_supplied_before_dispatch")
                    self.assertEqual(field["initial"], {"state": "unset"})
                    self.assertEqual(field["history"], {"history_state": "complete", "events": []})
        self.assertIsNotNone(self.org.db.get_task_attribute_definition(effort["definition_id"])["first_used_at"])

    async def test_r02_r03_r04_r05_keyed_refusals_before_insert(self):
        await self.create(constraints={"min": 1, "max": 10, "integer_only": True})
        await self.create("ratio", constraints={"min": -2.5, "max": 2.5})
        await self.create("label", type="string")
        await self.create("short", type="string", constraints={"min_length": 1, "max_length": 3})
        await self.create("ready", type="boolean")
        archived = await self.create("archived")
        self.org.db.archive_task_attribute_definition(archived["definition_id"], expected_version=1)
        renamed = await self.create("old")
        self.org.db.update_task_attribute_definition(renamed["definition_id"], expected_version=1, key="new")
        foreign_db = Database(Path(self.tmp.name) / "foreign.db")
        try:
            foreign_db.create_task_attribute_definition(key="foreign", type="numeric", name="Private other org rubric")
        finally:
            foreign_db.close()
        used = await self.create("archived_used")
        admitted = await self.submit({"archived_used": 4})
        self.assertEqual(admitted.status_code, 200, admitted.text)
        self.org.db.archive_task_attribute_definition(used["definition_id"], expected_version=1)
        cases = [("archived_used", 4, ["archived_key"])]
        for value in ("4", "4.0", "", True, False, None, [], {}):
            cases.append(("effort", value, ["expected_finite_number"]))
        for value, errors in ((0, ["below_min"]), (-1, ["below_min"]), (11, ["above_max"]),
                (4.5, ["expected_integer"]), (0.5, ["expected_integer", "below_min"]), (10.5, ["expected_integer", "above_max"])):
            cases.append(("effort", value, errors))
        cases += [("ratio", -2.5001, ["below_min"]), ("ratio", 2.5001, ["above_max"]),
                  ("short", "", ["below_min_length"]), ("short", "设计🙂x", ["above_max_length"]),
                  ("label", "🙂" * 4001, ["above_max_length"])]
        cases += [("label", value, ["expected_string"]) for value in (4, 4.0, True, False, None, [], {})]
        cases += [("ready", value, ["expected_boolean"]) for value in (0, 1, -1, 1.0, "true", "false", "yes", "1", "", None, [], {})]
        cases += [(key, 4, [reason]) for key, reason in (("foreign", "unknown_key"), ("old", "unknown_key"), ("archived", "archived_key"))]
        queue = self.client._transport.app.state.daemon.queue
        for key, value, errors in cases:
            with self.subTest(key=key, value=repr(value)[:40], errors=errors):
                before, queued, sql = self.inventory(), queue._queue.qsize(), []
                self.org.db._conn.set_trace_callback(sql.append)
                try:
                    response = await self.submit({"label": "valid", key: value})
                finally:
                    self.org.db._conn.set_trace_callback(None)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["detail"]["errors"][key], errors)
                self.assertFalse(any(s.lstrip().upper().startswith("INSERT INTO TASKS ") for s in sql), sql)
                self.assertEqual(self.inventory(), before)
                self.assertEqual(queue._queue.qsize(), queued)
        response = await self.submit({"effort": 0, "short": "long", "missing": 1})
        self.assertEqual(set(response.json()["detail"]["errors"]), {"effort", "short", "missing"})
        for value in (None, [], 4, "map"):
            with self.subTest(shape=value):
                before = self.inventory()
                response = await self.submit(value)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["detail"]["errors"], {"attributes": ["expected_object"]})
                self.assertEqual(self.inventory(), before)
        for token in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(raw_nonfinite=token):
                before = self.inventory()
                response = await self.client.post(self.base + "/tasks", content=(
                    '{"brief":"Synthetic nonfinite","owner":"worker","attributes":{"effort":' + token + '}}'),
                    headers={"Content-Type": "application/json"})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["detail"]["errors"]["effort"], ["expected_finite_number"])
                self.assertEqual(self.inventory(), before)
        for values in ({"short": "设"}, {"short": "设计🙂"}, {"label": "🙂" * 4000}, {"ratio": -2.5}, {"ratio": 2.5}, {"ratio": 1.25}):
            response = await self.submit(values)
            self.assertEqual(response.status_code, 200, response.text)
            self.manifest(response.json()["task_id"], values)

    async def test_r12_owner_selection_controls_preserved(self):
        await self.create()
        response = await self.submit({"effort": 4}, owner=None, model="synthetic-model")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((response.json()["assigned_agent"], response.json()["team"]), ("keeper", "engineering"))
        task_id = response.json()["task_id"]
        self.manifest(task_id, {"effort": 4})
        task = self.org.db.get_task(task_id)
        self.assertEqual((task.model_override, task.override_source), ("synthetic-model", "founder"))
        self.assertTrue(any(a["action"] == "task_created" for a in self.org.db.get_audit_logs(task_id)))
        for fields, code in (({"owner": "missing"}, "unknown_owner"), ({"team": "engineering"}, "owner_team_mismatch"),
                             ({"reasoning_effort": "invalid"}, "invalid_task_selection")):
            before = self.inventory()
            response = await self.submit({"effort": 4}, **fields)
            self.assertGreaterEqual(response.status_code, 400, response.text)
            self.assertEqual(response.json()["detail"]["code"], code)
            self.assertEqual(self.inventory(), before)

    async def test_r06_zero_definitions_r07_later_and_legacy_r11_originals(self):
        response = await self.submit({}, owner="keeper", team="engineering")
        self.assertEqual(response.status_code, 200, response.text)
        task_id = response.json()["task_id"]
        self.manifest(task_id, {})
        d = await self.create()
        url = self.base + f"/tasks/{task_id}/attributes"
        read = (await self.client.get(url)).json()["attributes"][0]
        self.assertEqual((read["provenance"], read["initial"]), ("definition_added_later", {"state": "unset"}))
        for revision, operation in ((0, {"op": "set", "value": 6}), (1, {"op": "clear"}), (2, {"op": "clear"})):
            response = await self.client.put(url, json={"changes": [{**self.change(d, revision=revision), **operation}] if operation["op"] == "set" else [
                {key: value for key, value in {**self.change(d, revision=revision), **operation}.items() if key != "value"}]})
            self.assertEqual(response.status_code, 200, response.text)
        field = response.json()["attributes"][0]
        self.assertEqual(field["initial"], {"state": "unset"})
        self.assertEqual([e["source"] for e in field["history"]["events"]], ["edit", "clear", "clear"])
        legacy = (await self.client.get(self.values)).json()["attributes"][0]
        self.assertEqual(legacy["initial"], {"state": "unknown"})
        self.assertEqual(legacy["history"]["history_state"], "unavailable")

    async def attachment(self):
        from runtime.daemon.routes.tasks import _task_attachment_store
        store = _task_attachment_store(self.org)
        # The real store's upload API, no invented link/claim fixture.
        upload = await self.client.post(self.base + "/tasks/attachments", files={"file": ("synthetic.txt", b"synthetic attachment", "text/plain")})
        self.assertEqual(upload.status_code, 200, upload.text)
        ref = {"storage_key": upload.json()["storage_key"], "display_name": "synthetic.txt"}
        self.assertTrue(store.path_for(ref["storage_key"]).is_file())
        return ref

    async def test_r08_attachments_joint_admission_and_claim_refusal(self):
        await self.create()
        for attrs in ({"effort": 4}, {}):
            ref = await self.attachment()
            response = await self.submit(attrs, attachments=[ref])
            self.assertEqual(response.status_code, 200, response.text)
            task_id = response.json()["task_id"]
            self.manifest(task_id, attrs)
            self.assertEqual(self.org.db.get_task_attachment_by_storage_key(ref["storage_key"]).task_id, task_id)
            before = self.inventory()
            response = await self.submit(attrs, attachments=[ref])
            self.assertEqual(response.json()["detail"]["code"], "attachment_already_claimed")
            self.assertEqual(self.inventory(), before)
        ref = await self.attachment()
        before = self.inventory()
        response = await self.submit({"missing": 4}, attachments=[ref])
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.inventory(), before)
        self.assertIsNone(self.org.db.get_task_attachment_by_storage_key(ref["storage_key"]))
        response = await self.submit({}, attachments=[{"storage_key": "missing", "display_name": "x.txt"}])
        self.assertEqual(response.json()["detail"]["code"], "task_attachment_not_found")
        self.assertEqual(self.inventory(), before)

    async def test_r09_r08_real_sql_faults_revert_all_effects_and_recover(self):
        queue = self.client._transport.app.state.daemon.queue
        for attachment in (False, True):
            for target in ("admissions", "initial_values", "values", "value_events", "audit_log"):
                with self.subTest(attachment=attachment, target=target):
                    prefix = ("attached_" if attachment else "plain_") + target
                    first_key, second_key = prefix + "_first", prefix + "_second"
                    await self.create(first_key)
                    second = await self.create(second_key)
                    values = {first_key: 4, second_key: 6}
                    ref = await self.attachment() if attachment else None
                    table = target if target == "audit_log" else "task_attribute_" + target
                    where = f"NEW.action='task_attribute_set' AND json_extract(NEW.payload,'$.definition_id')='{second['definition_id']}'" if target == "audit_log" else "1" if target == "admissions" else f"NEW.definition_id='{second['definition_id']}'"
                    before, queued = self.inventory(), queue._queue.qsize()
                    self.org.db.execute(f"CREATE TEMP TRIGGER pr3_fault BEFORE INSERT ON {table} WHEN {where} BEGIN SELECT RAISE(ABORT, 'synthetic PR3 fault'); END")
                    try:
                        response = await self.submit(values, **({"attachments": [ref]} if ref else {}))
                        self.assertEqual(response.status_code, 503, response.text)
                        self.assertEqual(response.json()["detail"]["code"], "attribute_storage_unavailable")
                        self.assertEqual(self.inventory(), before)
                        self.assertEqual(queue._queue.qsize(), queued)
                    finally:
                        self.org.db.execute("DROP TRIGGER pr3_fault")
                    response = await self.submit(values, **({"attachments": [ref]} if ref else {}))
                    self.assertEqual(response.status_code, 200, response.text)
                    self.manifest(response.json()["task_id"], values)

    async def test_h03_bind_failure_preexisting_writer_internal_provenance(self):
        from runtime.daemon.routes.tasks import SubmitTask, persist_root_task, resolve_task_owner
        body = SubmitTask(owner="worker", brief="Synthetic internal")
        await self.create()
        before = self.inventory()
        resolve_task_owner(body, self.org)
        self.assertEqual(self.inventory(), before)
        def bind(task):
            self.org.db.insert_audit_log_uncommitted(task.id, "worker", "synthetic_binding", {})
            raise RuntimeError("synthetic bind failed")
        with self.assertRaisesRegex(RuntimeError, "bind failed"):
            persist_root_task(self.org, body, bind=bind, admit_attributes=True)
        self.assertEqual(self.inventory(), before)
        self.org.db._conn.execute("BEGIN IMMEDIATE")
        try:
            with self.assertRaisesRegex(RuntimeError, "own transaction"):
                persist_root_task(self.org, body)
            self.assertTrue(self.org.db._conn.in_transaction)
        finally:
            self.org.db._conn.rollback()
        task = persist_root_task(self.org, body, bind=lambda task: self.org.db.insert_audit_log_uncommitted(task.id, "worker", "synthetic_binding", {}))
        self.assertEqual(self.org.db.execute("SELECT COUNT(*) FROM task_attribute_admissions WHERE task_id=?", (task.id,)).fetchone()[0], 0)
        self.assertEqual(self.org.db.get_audit_logs(task.id)[0]["action"], "synthetic_binding")

    async def test_r09_precommit_failure_and_r08_concurrent_claim(self):
        from runtime.daemon.routes.tasks import persist_root_task, SubmitTask
        await self.create()
        connection = self.org.db._conn
        class CommitFault:
            def __getattr__(self, name):
                return getattr(connection, name)
            def commit(self):
                raise sqlite3.OperationalError("synthetic precommit fault")
        for attached in (False, True):
            ref = await self.attachment() if attached else None
            before = self.inventory()
            self.org.db._conn = CommitFault()
            try:
                response = await self.submit({"effort": 4}, **({"attachments": [ref]} if ref else {}))
                self.assertEqual(response.status_code, 503, response.text)
            finally:
                self.org.db._conn = connection
            self.assertEqual(self.inventory(), before)
        ref = await self.attachment()
        expected = {}
        def claim(db):
            from runtime.infrastructure.workflow_task_ownership import install_task_ownership
            install_task_ownership(db, org_slug="alpha")
            db.insert_task_with_attachments(TaskRecord(id="TASK-competing-claim", brief="Synthetic competing claim"),
                [{"ordinal": 0, "storage_key": ref["storage_key"], "display_name": "synthetic.txt", "size_bytes": 20,
                  "content_type": "text/plain"}], uploaded_by="founder")
            for table in TABLES + ["tasks", "workflow_task_ownership", "task_attachments", "audit_log"]:
                expected[table] = [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        response = await self.interleave(lambda: self.submit({"effort": 4}, attachments=[ref]), claim)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "attachment_already_claimed")
        self.assertEqual(self.inventory(), expected)
        # Direct bind remains under the same root transaction even after attrs.
        with self.assertRaisesRegex(RuntimeError, "late bind"):
            persist_root_task(self.org, SubmitTask(owner="worker", brief="Synthetic late bind", attributes={"effort": 4}),
                admit_attributes=True, bind=lambda task: (_ for _ in ()).throw(RuntimeError("late bind")))
        self.assertEqual(self.inventory(), expected)

    async def test_r10_current_definitions_at_writer_boundary(self):
        for operation, changes, reason in (("tighten", {"constraints": {"max": 3}}, "above_max"),
                ("type", {"type": "boolean"}, "expected_boolean"), ("rename", {"key": "renamed"}, "unknown_key"),
                ("archive", None, "archived_key"), ("metadata", {"description": "current metadata"}, None)):
            with self.subTest(operation=operation):
                d = await self.create("race_" + operation)
                expected = {}
                queued = self.client._transport.app.state.daemon.queue._queue.qsize()
                def winner(db):
                    if changes is None:
                        db.archive_task_attribute_definition(d["definition_id"], expected_version=1)
                    else:
                        db.update_task_attribute_definition(d["definition_id"], expected_version=1, **changes)
                    for table in TABLES + ["tasks", "workflow_task_ownership", "task_attachments", "audit_log"]:
                        expected[table] = [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                response = await self.interleave(lambda: self.submit({d["key"]: 4}), winner)
                self.assertEqual(response.status_code, 422 if reason else 200, response.text)
                if reason:
                    self.assertEqual(response.json()["detail"]["errors"][d["key"]], [reason])
                    self.assertEqual(self.inventory(), expected)
                    self.assertEqual(self.client._transport.app.state.daemon.queue._queue.qsize(), queued)
                else:
                    task_id = response.json()["task_id"]
                    self.manifest(task_id, {d["key"]: 4})
                    field = self.org.db.get_task_attribute(task_id, d["definition_id"])
                    self.assertEqual(field["initial"]["definition"]["version"], 2)
                    with self.assertRaisesRegex(AttributeStoreError, "definition_structure_frozen"):
                        self.org.db.update_task_attribute_definition(d["definition_id"], expected_version=2, constraints={"max": 3})
        before = self.inventory()
        self.org.db._conn.set_authorizer(lambda action, name, *_: sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and name == "task_attribute_definitions" else sqlite3.SQLITE_OK)
        try:
            response = await self.submit({})
            self.assertEqual(response.status_code, 503, response.text)
        finally:
            self.org.db._conn.set_authorizer(None)
        self.assertEqual(self.inventory(), before)

    async def test_a01_a03_served_create_schema_and_inventory(self):
        schema = (await self.client.get("/openapi.json")).json()
        operation = schema["paths"]["/api/v1/orgs/{slug}/tasks"]["post"]
        self.assertIn("503", operation["responses"])
        error_schema = operation["responses"]["422"]["content"]["application/json"]["schema"]
        self.assertIn({"$ref": "#/components/schemas/AttributeErrorResponse"}, error_schema["anyOf"])
        ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
        body = schema["components"]["schemas"][ref]
        self.assertIn("attributes", body["properties"])
        self.assertEqual(body["properties"]["attributes"]["type"], "object")
        self.assertIn("description", body["properties"]["attributes"])
        self.assertNotIn("attributes", body["required"])
        self.assertIn("brief", body["required"])
        self.assertTrue({"owner", "team", "model", "reasoning_effort", "attachments"} <= body["properties"].keys())
        classification = json.loads((Path(__file__).resolve().parents[1] / "tests/contract/route-classification.json").read_text())
        self.assertEqual(classification["included"].count("POST /api/v1/orgs/{slug}/tasks"), 1)
        self.assertFalse(set(classification["included"]) & set(classification["excluded"]))
        self.assertEqual(len(classification["included"]), len(set(classification["included"])))


class AttributeCliTests(unittest.IsolatedAsyncioTestCase):
    """The shipping parser/handlers bridged only at HTTP transport to real ASGI."""
    asyncSetUp = RouteTests.asyncSetUp
    asyncTearDown = RouteTests.asyncTearDown
    create = RouteTests.create
    change = RouteTests.change
    submit = CreationTests.submit
    manifest = CreationTests.manifest
    inventory = CreationTests.inventory
    attachment = CreationTests.attachment

    async def invoke(self, *arguments, after=None, startup_error=None):
        from cli.main import build_parser
        from cli.client.client import OpcClient
        from contextlib import redirect_stdout, redirect_stderr
        from io import StringIO
        from types import SimpleNamespace
        loop = asyncio.get_running_loop()
        calls = []
        async def send(method, path, kwargs):
            response = await self.client.request(method, path, **kwargs)
            calls.append((method, path, kwargs, response))
            if after:
                await after(method, path, kwargs, response)
            return response
        def request(method, path, **kwargs):
            return asyncio.run_coroutine_threadsafe(send(method, path, kwargs), loop).result(15)
        bridge = SimpleNamespace(get=lambda path, **kw: request("GET", path, **kw),
            post=lambda path, **kw: request("POST", path, **kw), request=request)
        def invoke():
            stdout, stderr, code = StringIO(), StringIO(), 0
            with patch.object(OpcClient, "from_env", return_value=bridge, side_effect=startup_error), redirect_stdout(stdout), redirect_stderr(stderr):
                try:
                    args = build_parser().parse_args(list(arguments) + ["--org", "alpha"])
                    args.func(args)
                except SystemExit as exc:
                    code = exc.code
            return code, stdout.getvalue(), stderr.getvalue(), calls
        return await asyncio.to_thread(invoke)

    async def test_c01_c02_discovery_and_typed_presence_rendering(self):
        code, out, _, _ = await self.invoke("attributes", "list", "--json")
        self.assertEqual((code, json.loads(out)["definitions"]), (0, []))
        await self.create("free", description=None)
        await self.create("ready", type="boolean")
        await self.create("label", type="string")
        archived = await self.create("archived")
        self.org.db.archive_task_attribute_definition(archived["definition_id"], expected_version=1)
        before = self.inventory()
        for flags in ([], ["--json"]):
            code, out, _, _ = await self.invoke("attributes", "list", *flags)
            self.assertEqual(code, 0, out)
            items = json.loads(out)["definitions"]
            self.assertEqual({d["key"] for d in items}, {"free", "ready", "label"})
            self.assertTrue(all({"type", "constraints", "description", "definition_id", "version"} <= d.keys() for d in items))
            code, out, _, _ = await self.invoke("attributes", "show", "free", *flags)
            self.assertEqual(code, 0, out)
            self.assertIsNone(json.loads(out)["definition"]["description"])
        code, out, _, _ = await self.invoke("attributes", "list", "--include-archived")
        self.assertEqual(len(json.loads(out)["definitions"]), 4)
        code, out, _, _ = await self.invoke("attributes", "show", "missing")
        self.assertNotEqual(code, 0)
        self.assertIn("unknown", out)
        self.assertEqual(self.inventory(), before)
        response = await self.submit({"free": 0, "ready": False, "label": ""})
        task_id = response.json()["task_id"]
        code, out, _, _ = await self.invoke("attributes", "get", task_id, "--json", "--session-id", "synthetic-sibling")
        self.assertEqual(code, 0, out)
        read = json.loads(out)
        self.assertFalse(read["editability"]["can_edit"])
        for field in read["attributes"]:
            self.assertIn("value", field["current"])
        self.assertEqual({a["definition"]["key"]: a["current"]["value"] for a in read["attributes"]}, {"free": 0, "ready": False, "label": ""})
        self.assertTrue(all(a["initial"]["state"] == "set" for a in read["attributes"]))

    async def test_c03_c04_run_set_clear_history_and_exact_scalar_parsing(self):
        await self.create("free")
        await self.create("ready", type="boolean")
        await self.create("label", type="string")
        for numeric, text in (("9007199254740993", "  设计🙂=x  "), ("4.0", ""), ("1e2", "null"), ("0", "false")):
            with self.subTest(numeric=numeric, text=text):
                extra = []
                if numeric == "9007199254740993":
                    ref = await self.attachment()
                    brief_file = Path(self.tmp.name) / "brief.txt"
                    brief_file.write_text("Synthetic CLI")
                    extra = ["--brief-file", str(brief_file), "--model", "synthetic-model", "--attach", ref["storage_key"] + ":synthetic.txt"]
                else:
                    extra = ["--brief", "Synthetic CLI"]
                code, out, _, calls = await self.invoke("run", "--owner", "worker", *extra,
                    "--attr", "free=" + numeric, "--attr", "ready=false", "--attr", "label=" + text)
                self.assertEqual(code, 0, out)
                request = next(c for c in calls if c[0] == "POST" and c[1].endswith("/tasks"))
                task_id = request[3].json()["task_id"]
                self.assertEqual(request[2]["json"]["brief"], "Synthetic CLI")
                if numeric == "9007199254740993":
                    self.assertEqual(request[2]["json"]["model"], "synthetic-model")
                    self.assertEqual(self.org.db.get_task_attachment_by_storage_key(ref["storage_key"]).task_id, task_id)
                expected = {"free": json.loads(numeric), "ready": False, "label": text}
                self.manifest(task_id, expected)
                code, out, _, _ = await self.invoke("attributes", "set", task_id, "free", "6", "--note", "updated", "--json")
                self.assertEqual(code, 0, out)
                code, out, _, calls = await self.invoke("attributes", "clear", task_id, "free", "--json")
                self.assertEqual(code, 0, out)
                change = next(c for c in calls if c[0] == "PUT")[2]["json"]["changes"][0]
                self.assertNotIn("value", change)
                self.assertEqual(change["expected_revision"], 2)
                code, out, _, _ = await self.invoke("attributes", "history", task_id, "--key", "free", "--json")
                self.assertEqual(code, 0, out)
                events = json.loads(out)["events"]
                self.assertEqual([e["source"] for e in events], ["admission", "edit", "clear"])
                self.assertEqual(events[1]["note"], "updated")
                self.assertEqual([e["revision"] for e in events], [1, 2, 3])
                code, out, _, _ = await self.invoke("attributes", "get", task_id)
                field = next(a for a in json.loads(out)["attributes"] if a["definition"]["key"] == "free")
                self.assertEqual(field["initial"]["value"], expected["free"])
                self.assertFalse(field["current"]["present"])
        code, out, _, calls = await self.invoke("run", "--owner", "worker", "--brief", "No scores")
        self.assertEqual(code, 0, out)
        self.assertFalse(any("/task-attributes/definitions" in c[1] for c in calls))
        request = next(c for c in calls if c[0] == "POST" and c[1].endswith("/tasks"))
        self.manifest(request[3].json()["task_id"], {})
        for _ in range(2):
            code, out, _, _ = await self.invoke("attributes", "clear", "TASK-target", "free")
            self.assertEqual(code, 0, out)
            self.assertEqual(json.loads(out)["attributes"][0]["initial"], {"state": "unknown"})

    async def test_c05_c06_lexical_constraints_and_duplicate_refusals(self):
        await self.create(constraints={"min": 1, "max": 10, "integer_only": True})
        await self.create("ready", type="boolean")
        await self.create("label", type="string")
        tokens = [["effort=" + s] for s in ("abc", "", "null", "true", "[]", "{}", "NaN", "Infinity", "1e999", "0x4", "01", "0", "11", "4.5")]
        tokens += [["ready=" + s] for s in ("1", "0", "yes", "no", "True", "False", "", "null")]
        tokens += [[s] for s in ("effort", "=4", "Effort=4", "a-b=4", " effort=4", "missing=4")]
        tokens += [["effort=4", "effort=6"], ["effort=4", "effort=4"]]
        for items in tokens:
            with self.subTest(tokens=items):
                before = self.inventory()
                arguments = [arg for token in items for arg in ("--attr", token)]
                code, out, _, calls = await self.invoke("run", "--brief", "Invalid", "--owner", "worker", *arguments)
                self.assertNotEqual(code, 0, out)
                self.assertIn("effort" if items[0].startswith("effort") else "errors", out)
                self.assertEqual(self.inventory(), before)
                if items[0] not in ("effort=0", "effort=11", "effort=4.5"):
                    self.assertFalse(any(c[0] in ("POST", "PUT") and c[1].endswith(("/tasks", "/attributes")) for c in calls))

    async def test_c07_real_cas_archive_conflicts_and_uncertain_transport(self):
        import httpx
        d = await self.create()
        fired = False
        async def competitor(method, path, kwargs, response):
            nonlocal fired
            if method == "GET" and path == self.values and not fired:
                fired = True
                result = await self.client.put(self.values, json={"changes": [self.change(d, 8)]})
                self.assertEqual(result.status_code, 200, result.text)
        code, out, _, calls = await self.invoke("attributes", "set", "TASK-target", "effort", "4", after=competitor)
        self.assertNotEqual(code, 0)
        self.assertIn("value_revision_conflict", out)
        self.assertEqual(sum(c[0] == "PUT" for c in calls), 1)
        for action in ("version", "archive"):
            d = await self.create("race_" + action)
            fired = False
            async def change_definition(method, path, kwargs, response):
                nonlocal fired
                if method == "GET" and path == self.values and not fired:
                    fired = True
                    if action == "archive":
                        self.org.db.archive_task_attribute_definition(d["definition_id"], expected_version=1)
                    else:
                        self.org.db.update_task_attribute_definition(d["definition_id"], expected_version=1, description="new")
            code, out, _, calls = await self.invoke("attributes", "set", "TASK-target", d["key"], "4", after=change_definition)
            self.assertNotEqual(code, 0)
            self.assertIn("definition_version_conflict", out)
            self.assertEqual(sum(c[0] == "PUT" for c in calls), 1)
        async def lose(method, path, kwargs, response):
            if method in ("POST", "PUT") and path.endswith(("/tasks", "/attributes")) and response.is_success:
                raise httpx.ReadError("synthetic lost reply after COMMIT")
        for arguments, method in ((("attributes", "set", "TASK-target", "effort", "6"), "PUT"),
                                  (("run", "--owner", "worker", "--brief", "Lost create", "--attr", "effort=4"), "POST")):
            code, out, _, calls = await self.invoke(*arguments, after=lose)
            self.assertNotEqual(code, 0)
            self.assertIn('"committed": null', out)
            self.assertIn("readback_required", out)
            self.assertEqual(sum(c[0] == method and c[1].endswith(("/tasks", "/attributes")) for c in calls), 1)
            if method == "POST":
                self.manifest(next(c[3].json()["task_id"] for c in calls if c[0] == "POST" and c[1].endswith("/tasks")), {"effort": 4})
            else:
                self.assertEqual(self.org.db.get_task_attribute("TASK-target", next(a["definition_id"] for a in self.org.db.list_task_attribute_definitions() if a["key"] == "effort"))["current"]["value"], 6)

    async def test_c08_session_forwarding_denials_and_unavailable_service(self):
        from cli.client.client import DaemonNotRunning
        await self.create()
        for sid, allowed in (("synthetic-worker", True), ("synthetic-manager", True), ("synthetic-sibling", False), ("stale", False)):
            code, out, _, calls = await self.invoke("attributes", "set", "TASK-target", "effort", "4", "--session-id", sid)
            self.assertEqual(code == 0, allowed, out)
            self.assertTrue(all(c[2].get("params", {}).get("session_id") == sid for c in calls if "/attributes" in c[1] or "/task-attributes" in c[1]))
            if not allowed:
                self.assertTrue("not_task_owner_or_owning_manager" in out or "authority_unknown" in out)
        self.org.db._conn.set_authorizer(lambda action, name, *_: sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and name == "task_attribute_definitions" else sqlite3.SQLITE_OK)
        try:
            code, out, _, _ = await self.invoke("attributes", "list")
            self.assertNotEqual(code, 0)
            self.assertIn("attribute_storage_unavailable", out)
        finally:
            self.org.db._conn.set_authorizer(None)
        code, out, _, calls = await self.invoke("attributes", "list", startup_error=DaemonNotRunning("synthetic unavailable"))
        self.assertNotEqual(code, 0)
        self.assertIn("transport_unavailable", out)
        self.assertEqual(calls, [])


class RootListExportTests(unittest.IsolatedAsyncioTestCase):
    """PR5 accepted L/E/A/S cases: real store and shipping HTTP boundary."""
    create = RouteTests.create
    inventory = CreationTests.inventory
    asyncTearDown = RouteTests.asyncTearDown

    async def asyncSetUp(self):
        self.stack, self.tmp = AsyncExitStack(), tempfile.TemporaryDirectory()
        self.org, self.client = await self.stack.enter_async_context(
            route_fixture(Path(self.tmp.name) / "fixture", workers=("worker", "sibling")))
        self.base = "/api/v1/orgs/alpha"
        self.defs = self.base + "/task-attributes/definitions"
        self.roots = self.base + "/tasks/roots"

    def task(self, task_id, attributes=None, **fields):
        self.org.db.insert_task(TaskRecord(**{"id": task_id, "assigned_agent": "worker",
            "brief": task_id, "created_at": datetime(2026, 11, 25, tzinfo=timezone.utc), **fields}))
        if attributes:
            with self.org.db._lock, self.org.db._attribute_writer():
                self.org.db._write_task_attributes_initial_uncommitted(task_id, attributes, **ACTOR)

    async def read(self, pairs=(), *, format=None, **params):
        response = await self.client.get(self.roots + ("/export" if format else ""),
            params=[*pairs, *params.items(), *([("format", format)] if format else [])])
        self.assertEqual(response.status_code, 200, response.text[:800])
        return response

    def ids(self, response):
        if response.headers["content-type"].startswith("text/csv"):
            import csv, io
            return [row["task_id"] for row in csv.DictReader(io.StringIO(response.text, newline=""))]
        return [row["task_id"] for row in response.json()["tasks"]]

    async def refused(self, pairs, code, reason=None):
        for suffix in ("", "/export"):
            response = await self.client.get(self.roots + suffix, params=pairs)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(response.json()["detail"]["code"], code)
            if reason:
                self.assertIn(reason, [r for reasons in response.json()["detail"]["errors"].values() for r in reasons])

    async def test_l01_min(self):
        await self.create()
        for value in (2, 3, 4):
            self.task("TASK-" + str(value), {"effort": value})
        self.task("TASK-absent")
        response = await self.read([("attr_min", "effort=3")])
        self.assertEqual(self.ids(response), ["TASK-4", "TASK-3"])
        self.assertEqual([t["attribute_values"]["effort"] for t in response.json()["tasks"]],
                         [{"present": True, "value": 4}, {"present": True, "value": 3}])

    async def test_l02_max(self):
        await self.create()
        for value in (4, 5, 6):
            self.task("TASK-" + str(value), {"effort": value})
        self.task("TASK-absent")
        self.assertEqual(self.ids(await self.read([("attr_max", "effort=5")])), ["TASK-5", "TASK-4"])

    async def test_l03_equal_bounds(self):
        await self.create()
        for i, value in enumerate((3.9, 4, 4.0, 4.1)):
            self.task(f"TASK-{i}", {"effort": value})
        response = await self.read([("attr_min", "effort=4"), ("attr_max", "effort=4")])
        self.assertEqual(self.ids(response), ["TASK-2", "TASK-1"])
        self.assertEqual([type(t["attribute_values"]["effort"]["value"]) for t in response.json()["tasks"]], [float, int])

    async def test_l04_native_numeric_precision(self):
        await self.create(constraints={"integer_only": True})
        values = (-5, 0, 3, 4, 100, 2**53, 2**53+1, 2**64, 2**64+1)
        for i, value in enumerate(values):
            self.task(f"TASK-{i:02}", {"effort": value})
        for bound, expected in (("3.5", list(range(8, 2, -1))), ("1e+2", list(range(8, 3, -1))),
                                (str(2**53+1), [8, 7, 6]), (str(2**64+1), [8]), ("-5", list(range(8, -1, -1)))):
            with self.subTest(bound=bound):
                self.assertEqual(self.ids(await self.read([("attr_min", "effort="+bound)])),
                                 [f"TASK-{i:02}" for i in expected])
        self.assertEqual(self.ids(await self.read([("attr_max", "effort=0")])), ["TASK-01", "TASK-00"])
        response = await self.client.get(self.roots + "?attr_min=effort%3D1e%2B2")
        self.assertEqual(self.ids(response), [f"TASK-{i:02}" for i in range(8, 3, -1)])
        await self.refused([("attr_min", "effort=1e 2")], "attribute_filter_invalid")

    async def test_l05_inverted(self):
        await self.create()
        self.task("TASK-3", {"effort": 3})
        before = self.inventory()
        await self.refused([("attr_min", "effort=7"), ("attr_max", "effort=3")],
                           "attribute_filter_inverted_range", "min_exceeds_max")
        self.assertEqual(self.ids(await self.read([("attr_min", "effort=3"), ("attr_max", "effort=3")])), ["TASK-3"])
        self.assertEqual(before, self.inventory())

    async def test_l06_number_grammar(self):
        await self.create()
        for token in ("NaN", "Infinity", "1e999", "true", "null", '"4"', "+1", "01", " 1", "1 ", "", "1"*129):
            with self.subTest(token=token):
                await self.refused([("attr_min", "effort="+token)], "attribute_filter_invalid",
                    "number_too_long" if len(token) > 128 else "expected_finite_json_number")
        for key, kind in (("label", "string"), ("ready", "boolean")):
            await self.create(key, type=kind)
            await self.refused([("attr_min", key+"=7"), ("attr_max", key+"=3")], "attribute_filter_not_numeric")
        for token in ("-0", "1e-2", "1E+2"):
            await self.read([("attr_min", "effort="+token)])
        await self.refused([("attr_min", "effort")], "attribute_filter_invalid", "expected_key_equals_number")

    async def test_l07_repeated_and_conflicting(self):
        for key in ("effort", "risk"):
            await self.create(key)
        self.task("TASK-match", {"effort": 4, "risk": 5})
        self.task("TASK-other", {"effort": 4, "risk": 1})
        pairs = [("attr_min", "effort=3"), ("attr_min", "risk=3"), ("attr_set", "effort"), ("attr_set", "risk")]
        self.assertEqual(self.ids(await self.read(pairs)), ["TASK-match"])
        for tail in (("attr_min", "effort=3"), ("attr_min", "effort=5"), ("attr_not_set", "effort")):
            await self.refused(pairs+[tail], "attribute_filter_invalid",
                "conflicting_predicates" if tail[0] == "attr_not_set" else "duplicate_predicate")
        await self.refused([("attr_not_set", "effort"), ("attr_set", "effort")], "attribute_filter_invalid")
        await self.refused([("attr_set", f"key_{i}") for i in range(17)], "attribute_filter_invalid", "too_many_keys")
        await self.refused([("attr_set", "effort")]*65, "attribute_filter_invalid", "too_many_predicates")

    async def test_l08_key_grammar(self):
        await self.create("risk_score")
        self.task("TASK-safe", {"risk_score": 4})
        response = await self.client.get(self.roots+"?attr_min=risk%5Fscore%3D4")
        self.assertEqual(self.ids(response), ["TASK-safe"])
        for key in ("a.b", "a b", "a:b", "a-b", "a[b]", "雪", "x' OR 1=1--", "risk%5Fscore", "A", ""):
            with self.subTest(key=key):
                await self.refused([("attr_set", key)], "attribute_filter_invalid", "invalid_key")

    async def test_l09_set_false_like(self):
        for key, kind, values in (("effort", "numeric", (0, -5)), ("ready", "boolean", (False, True)), ("label", "string", ("", "x"))):
            await self.create(key, type=kind)
            for i, value in enumerate(values):
                self.task(f"TASK-{key}-{i}", {key: value})
            response = await self.read([("attr_set", key)])
            self.assertEqual(self.ids(response), [f"TASK-{key}-1", f"TASK-{key}-0"])
            for row in response.json()["tasks"]:
                self.assertTrue(row["attribute_values"][key]["present"])
                self.assertIs(type(row["attribute_values"][key]["value"]), type(values[0]))

    async def test_l10_not_set(self):
        self.task("TASK-legacy")
        d = await self.create()
        for tid, values in (("TASK-never", None), ("TASK-clear", {"effort": 4}), ("TASK-zero", {"effort": 0})):
            self.task(tid, values)
        self.org.db.clear_task_attribute("TASK-clear", d["definition_id"], expected_version=1, expected_revision=1, **ACTOR)
        self.assertEqual(self.ids(await self.read([("attr_not_set", "effort")])), ["TASK-never", "TASK-legacy", "TASK-clear"])
        for key, kind, value in (("ready", "boolean", False), ("label", "string", "")):
            await self.create(key, type=kind)
            self.task("TASK-"+key, {key: value})
            self.assertNotIn("TASK-"+key, self.ids(await self.read([("attr_not_set", key)])))

    async def test_l11_and_existing_filters(self):
        await self.create()
        await self.create("risk")
        cases = [
            ("match", {"effort": 4, "risk": 2}, {}),
            ("effort", {"effort": 1, "risk": 2}, {}),
            ("risk", {"effort": 4, "risk": 9}, {}),
            ("agent", {"effort": 4, "risk": 2}, {"assigned_agent": "sibling"}),
            ("status", {"effort": 4, "risk": 2}, {"status": "pending"}),
            ("block", {"effort": 4, "risk": 2}, {"block_kind": "blocked_on_job"}),
            ("parent", None, {}),
            ("child", {"effort": 4, "risk": 2}, {"parent_task_id": "TASK-parent"}),
        ]
        for suffix, values, fields in cases:
            self.task("TASK-"+suffix, values, **{"status": "in_progress", "block_kind": "delegated", **fields})
        response = await self.read([("attr_min", "effort=3"), ("attr_max", "risk=3")],
            assigned_agent="worker", status="in_progress", block_kind="delegated", limit=1)
        self.assertEqual(self.ids(response), ["TASK-match"])

    async def test_l12_filtered_cursor(self):
        await self.create()
        for i in range(6):
            self.task(f"TASK-{i}", {"effort": i % 2})
        pairs = [("attr_min", "effort=1")]
        first = await self.read(pairs, limit=2)
        self.assertEqual(self.ids(first), ["TASK-5", "TASK-3"])
        self.assertEqual(first.json()["next_cursor"], "TASK-3")
        self.assertEqual(self.ids(await self.read(pairs, limit=2, before="TASK-3")), ["TASK-1"])
        self.assertEqual(self.ids(await self.read(pairs, before="TASK-4")), ["TASK-3", "TASK-1"])
        self.assertEqual(self.ids(await self.read(pairs, before="missing")), [])
        await self.refused([("attr_set", "unknown"), ("before", "missing")], "attribute_filter_unknown_key")

    async def test_l13_existing_roots_projection(self):
        await self.create()
        self.task("TASK-root", {"effort": 4})
        self.task("TASK-failed", parent_task_id="TASK-root", status="failed")
        self.task("TASK-retry", parent_task_id="TASK-root", revisit_of_task_id="TASK-failed", status="completed")
        self.task("TASK-root-retry", revisit_of_task_id="TASK-root", status="completed")
        response = await self.read([("attr_set", "effort")], status="pending")
        row = response.json()["tasks"][0]
        self.assertEqual(row["task_id"], "TASK-root")
        self.assertEqual(row["severity_rollup"], "pending")
        self.assertEqual(row["direct_revisits"], ["TASK-root-retry"])
        self.assertTrue({"pause", "brief", "effective_model", "effective_reasoning_effort",
                         "originating_thread_id", "originating_task_id"} <= row.keys())

    async def test_l14_unknown_current_key(self):
        await self.refused([("attr_set", "missing")], "attribute_filter_unknown_key")
        d = await self.create("old")
        self.org.db.update_task_attribute_definition(d["definition_id"], expected_version=1, key="new")
        await self.refused([("attr_set", "old")], "attribute_filter_unknown_key")
        await self.read([("attr_set", "new")])
        # Another real org DB owns this key; alpha never discovers it.
        with closing(Database(Path(self.tmp.name) / "beta.db")) as beta:
            beta.create_task_attribute_definition(key="foreign", type="numeric", name="Foreign")
            await self.refused([("attr_set", "foreign")], "attribute_filter_unknown_key")

    async def test_l15_archived(self):
        d = await self.create()
        self.task("TASK-set", {"effort": 4})
        self.task("TASK-unset")
        self.org.db.archive_task_attribute_definition(d["definition_id"], expected_version=1)
        before = self.inventory()
        for pairs, expected in (([("attr_min", "effort=3")], ["TASK-set"]),
                                ([("attr_set", "effort")], ["TASK-set"]),
                                ([("attr_not_set", "effort")], ["TASK-unset"])):
            for format in (None, "json", "csv"):
                response = await self.read(pairs, format=format)
                self.assertEqual(self.ids(response), expected)
                if format == "json":
                    self.assertTrue(response.json()["attribute_definitions"][0]["archived"])
        self.assertNotIn("attribute:effort", (await self.read(format="json")).json()["columns"])
        self.assertEqual(before, self.inventory())

    async def test_l16_current_projection(self):
        effort = await self.create()
        await self.create("ready", type="boolean")
        label = await self.create("label", type="string")
        await self.create("unfiltered")
        self.task("TASK-current", {"effort": 4, "ready": False, "label": "", "unfiltered": 8})
        self.org.db.set_task_attribute("TASK-current", effort["definition_id"], 6, expected_version=1, expected_revision=1, **ACTOR)
        self.org.db.clear_task_attribute("TASK-current", label["definition_id"], expected_version=1, expected_revision=1, **ACTOR)
        row = (await self.read([("attr_min", "effort=3"), ("attr_max", "effort=7"),
                               ("attr_set", "ready"), ("attr_not_set", "label")])).json()["tasks"][0]
        self.assertEqual(row["attribute_values"], {"effort": {"present": True, "value": 6},
            "ready": {"present": True, "value": False}, "label": {"present": False}})
        self.assertEqual((await self.read()).json()["tasks"][0]["attribute_values"], {})


    async def test_e01_list_export_parity(self):
        effort = await self.create()
        await self.create("ready", type="boolean")
        for i in range(5):
            self.task(f"TASK-{i}", {"effort": i, "ready": i % 2 == 0},
                status="in_progress" if i != 1 else "pending", block_kind="delegated",
                assigned_agent="sibling" if i == 3 else "worker")
        self.org.db.archive_task_attribute_definition(effort["definition_id"], expected_version=1)
        cases = [((), {}, ["TASK-4", "TASK-3", "TASK-2", "TASK-1", "TASK-0"]),
                 ([("attr_min", "effort=2")], {"assigned_agent": "worker"}, ["TASK-4", "TASK-2"]),
                 ([("attr_set", "ready")], {"status": "pending"}, ["TASK-1"]),
                 ([("attr_not_set", "effort")], {}, []),
                 ([("attr_min", "effort=1"), ("attr_max", "effort=3")],
                  {"status": "in_progress", "block_kind": "delegated", "before": "TASK-4", "limit": 1}, ["TASK-3"])]
        for pairs, params, expected in cases:
            cursors = []
            for format in (None, "json", "csv"):
                response = await self.read(pairs, format=format, **params)
                self.assertEqual(self.ids(response), expected)
                cursors.append(response.headers.get("X-HappyRanch-Next-Cursor") if format == "csv"
                               else response.json()["next_cursor"])
            self.assertEqual(cursors, [cursors[0]]*3)

    async def test_e02_columns(self):
        self.task("TASK-empty")
        empty = (await self.read(format="json")).json()
        self.assertEqual(empty["columns"], ["task_id", "brief_excerpt", "status", "assigned_agent", "created_at"])
        self.assertEqual(empty["attribute_definitions"], [])
        await self.create("zeta", type="string", name="雪")
        await self.create("alpha")
        a = await self.create("archived")
        self.org.db.archive_task_attribute_definition(a["definition_id"], expected_version=1)
        for pairs, keys in (([], ["alpha", "zeta"]), ([("attr_not_set", "archived")], ["alpha", "archived", "zeta"])):
            data = (await self.read(pairs, format="json")).json()
            self.assertEqual(data["columns"][5:], ["attribute:"+key for key in keys])
            self.assertEqual([d["key"] for d in data["attribute_definitions"]], keys)
            self.assertEqual(data["tasks"][0]["attribute_values"], {key: {"present": False} for key in keys})
            self.assertEqual(data["attribute_definitions"][-1]["name"], "雪")
        data = (await self.read(format="json", status="failed")).json()
        self.assertEqual(data["tasks"], [])
        self.assertEqual(data["columns"][5:], ["attribute:alpha", "attribute:zeta"])

    async def test_e03_compact_fields(self):
        brief = "雪\r\n\0" + "😀"*210
        self.task("TASK-long", brief=brief, assigned_agent=None)
        row = (await self.read(format="json")).json()["tasks"][0]
        self.assertEqual(row, {"task_id": "TASK-long", "brief_excerpt": brief[:200], "status": "pending",
            "assigned_agent": None, "created_at": "2026-11-25T00:00:00+00:00", "attribute_values": {}})
        self.assertEqual((await self.read()).json()["tasks"][0]["brief"], brief)

    async def test_e04_one_snapshot(self):
        # Same bounded connection-wrapper venue as P25, with two new read gaps.
        for i, (format, boundary) in enumerate((f, b) for f in (None, "json", "csv") for b in ("definitions", "roots")):
            with self.subTest(format=format, boundary=boundary):
                d = await self.create(f"effort_{i}", name="Old")
                label = await self.create(f"label_{i}", type="string")
                tid = f"TASK-snapshot-{i}"
                self.task(tid, {d["key"]: 4}, brief="old")
                connection = self.org.db._conn
                observer = Database(Path(connection.execute("PRAGMA database_list").fetchone()[2]))
                class SnapshotGate(BeginGate):
                    def execute(gate, sql, parameters=()):
                        cursor = gate.connection.execute(sql, parameters)
                        marker = "FROM task_attribute_definitions d" if boundary == "definitions" else "FROM tasks t WHERE"
                        if gate.hits == 0 and marker in sql:
                            gate.hits += 1
                            gate.arrived.set()
                            if not gate.release.wait(5):
                                raise AssertionError("snapshot gate deadline")
                        return cursor
                gate = SnapshotGate(connection)
                self.org.db._conn = gate
                pairs = [("attr_min", d["key"]+"=3")]
                pending = asyncio.create_task(asyncio.to_thread(lambda: asyncio.run(self.read(pairs, format=format))))
                try:
                    self.assertTrue(await asyncio.to_thread(gate.arrived.wait, 5))
                    observer.set_task_attribute(tid, d["definition_id"], 2, expected_version=1, expected_revision=1, **ACTOR)
                    observer.update_task_attribute_definition(d["definition_id"], expected_version=1, name="New")
                    observer.archive_task_attribute_definition(label["definition_id"], expected_version=1)
                    observer.execute("UPDATE tasks SET brief='new',status='failed' WHERE id=?", (tid,))
                    observer.commit()
                    self.assertFalse(observer._conn.in_transaction, "writer COMMIT precedes reader release")
                finally:
                    gate.release.set()
                    try:
                        response = await asyncio.wait_for(pending, 10)
                    finally:
                        self.org.db._conn = connection
                        observer.close()
                self.assertEqual(self.ids(response), [tid])
                if format == "csv":
                    import csv, io
                    row = next(csv.DictReader(io.StringIO(response.text, newline="")))
                    self.assertEqual((row["brief_excerpt"], row["status"], row["attribute:"+d["key"]]), ("old", "pending", "4"))
                    self.assertIn("attribute:"+label["key"], row)
                else:
                    data = response.json()
                    row = data["tasks"][0]
                    self.assertEqual((row["brief_excerpt" if format else "brief"], row["status"]), ("old", "pending"))
                    self.assertEqual(row["attribute_values"][d["key"]]["value"], 4)
                    if format:
                        metadata = {v["key"]: v for v in data["attribute_definitions"]}
                        self.assertEqual((metadata[d["key"]]["name"], metadata[d["key"]]["version"]), ("Old", 1))
                        self.assertIn(label["key"], metadata)
                after = await self.read(pairs, format=format)
                self.assertEqual(self.ids(after), [])
                if format == "json":
                    metadata = {v["key"]: v for v in after.json()["attribute_definitions"]}
                    self.assertEqual(metadata[d["key"]]["name"], "New")
                    self.assertNotIn(label["key"], metadata)

    async def test_e05_page_cap(self):
        for i in range(1001):
            self.task(f"TASK-{i:04}")
        self.assertEqual(len(self.ids(await self.read(format="json"))), 20)
        for format in ("json", "csv"):
            response = await self.read(format=format, limit=1000)
            self.assertEqual(self.ids(response), [f"TASK-{i:04}" for i in range(1000, 0, -1)])
            self.assertEqual(response.headers["X-HappyRanch-Next-Cursor"], "TASK-0001")
            last = await self.read(format=format, limit=1, before="TASK-0001")
            self.assertEqual(self.ids(last), ["TASK-0000"])
            self.assertEqual(last.headers["X-HappyRanch-Page-Full"], "true")
            self.assertEqual(self.ids(await self.read(format=format, before="TASK-0000")), [])
            self.assertEqual(self.ids(await self.read(format=format, before="missing")), [])
        self.assertEqual(len(self.ids(await self.read(limit=1001))), 1001)
        self.assertEqual(self.ids(await self.read(limit=0)), [])
        self.assertEqual(len(self.ids(await self.read(limit=-1))), 1001)

    async def test_e06_export_validation(self):
        for params in ([("limit", v)] for v in ("0", "-1", "1001", "1.5", "abc")):
            response = await self.client.get(self.roots+"/export", params=params)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(response.json()["detail"]["errors"], {"limit": ["integer_1_1000"]})
        for pairs in ([("limit", "1"), ("limit", "1")], [("format", "json"), ("format", "csv")], [("format", "xml")]):
            response = await self.client.get(self.roots+"/export", params=pairs)
            self.assertEqual((response.status_code, response.json()["detail"]["code"]), (422, "task_export_invalid"))
            self.assertNotIn("content-disposition", response.headers)
        for limit in (1, 1000):
            for format in ("json", "csv"):
                await self.read(format=format, limit=limit)

    async def test_e07_export_size(self):
        db = self.org.db
        with db._lock, db._attribute_writer():
            definitions = [db._create_task_attribute_definition_uncommitted(key=f"cap_{i:03}", type="string", name=str(i)) for i in range(257)]
        for format in ("json", "csv"):
            response = await self.client.get(self.roots+"/export", params={"format": format})
            self.assertEqual(response.status_code, 413, response.text)
            self.assertIn("too_many_attribute_columns", response.text)
            self.assertNotIn("content-disposition", response.headers)
        db.archive_task_attribute_definition(definitions[-1]["definition_id"], expected_version=1)
        response = await self.read(format="json")
        self.assertEqual(len(response.json()["columns"]), 261)
        response = await self.client.get(self.roots+"/export", params={"attr_not_set": "cap_256"})
        self.assertEqual(response.status_code, 413)
        # 200 valid 4000-code-point scalars per row: encoded bytes govern.
        for d in definitions[200:256]:
            db.archive_task_attribute_definition(d["definition_id"], expected_version=1)
        definitions = definitions[:200]
        for i in range(4):
            self.task(f"TASK-size-{i}", {d["key"]: ("雪"*4000 if i < 3 else "") for d in definitions})
        below = await self.read(format="json", limit=4)
        cap = 8*1024*1024
        remaining = cap - len(below.content)
        self.assertGreater(remaining, 0)
        changes, values = [], {}
        for d in definitions:
            n = min(12000, remaining)
            value = "雪"*(n//3) + ("é" if n % 3 == 2 else "x" if n % 3 else "")
            self.assertLessEqual(len(value), 4000)
            values[d["key"]] = value
            changes.append({"definition_id": d["definition_id"], "expected_version": 1,
                            "expected_revision": 1, "op": "set", "value": value})
            remaining -= n
        self.assertEqual(remaining, 0)
        with db._lock, db._attribute_writer():
            db._write_task_attribute_changes_uncommitted("TASK-size-3", changes, **ACTOR)
        exact = await self.read(format="json", limit=4)
        self.assertEqual(len(exact.content), cap)
        spare = next(d for d in definitions if not values[d["key"]])
        db.set_task_attribute("TASK-size-3", spare["definition_id"], "x", expected_version=1, expected_revision=2, **ACTOR)
        response = await self.client.get(self.roots+"/export", params={"limit": 4})
        self.assertEqual(response.status_code, 413, response.text[:500])
        self.assertIn("encoded_body_too_large", response.text)
        self.assertNotIn("content-disposition", response.headers)
        # Fill the final row to exceed CSV's cap too.
        with db._lock, db._attribute_writer():
            changes = [{**c, "expected_revision": 3 if c["definition_id"] == spare["definition_id"] else 2, "value": "雪"*4000} for c in changes]
            db._write_task_attribute_changes_uncommitted("TASK-size-3", changes, **ACTOR)
        response = await self.client.get(self.roots+"/export", params={"format": "csv", "limit": 4})
        self.assertEqual(response.status_code, 413, response.text[:500])
        self.assertLessEqual(len((await self.read(format="csv", limit=3)).content), cap)

    async def test_e08_csv_formula_safety(self):
        import csv, io
        await self.create("text", type="string")
        await self.create("number")
        await self.create("boolean", type="boolean")
        controls = ("=1", "+1", "-1", "@x", "\tcell", "\rcell", "", " space", "'quoted", "safe")
        for i, value in enumerate(controls):
            self.task(f"TASK-{i:02}", {"text": value, "number": -5 if i == 0 else 0, "boolean": False}, brief=value)
        response = await self.read(format="csv")
        rows = list(csv.DictReader(io.StringIO(response.text, newline="")))
        for row in rows:
            i = int(row["task_id"].split("-")[-1])
            expected = "'" + controls[i] if i < 6 else controls[i]
            self.assertEqual((row["brief_excerpt"], row["attribute:text"]), (expected, expected))
            self.assertEqual(row["attribute:number"], "'-5" if i == 0 else "0")
            self.assertEqual(row["attribute:boolean"], "false")

    async def test_e09_csv_roundtrip(self):
        import csv, io
        await self.create("text", type="string")
        value = '=雪,"quoted"\r\nnext\n😀'
        self.task("TASK-rfc", {"text": value}, brief=value)
        response = await self.read(format="csv")
        raw = response.content
        self.assertEqual(response.headers["content-type"], "text/csv; charset=utf-8")
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertTrue(raw.endswith(b"\r\n"))
        self.assertIn(b'""quoted""', raw)
        rows = list(csv.reader(io.StringIO(raw.decode("utf-8"), newline="")))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1], ["TASK-rfc", "'"+value, "pending", "worker", "2026-11-25T00:00:00+00:00", "'"+value])
        self.assertEqual(raw.count(b"\r\n"), 4)  # header, two embedded CRLFs, record

    async def test_e10_json_native(self):
        for key, kind in (("text", "string"), ("number", "numeric"), ("ready", "boolean"), ("empty", "string"), ("absent", "string")):
            await self.create(key, type=kind)
        value = "=雪\t\r\n"
        self.task("TASK-json", {"text": value, "number": 2**64+1, "ready": False, "empty": ""}, brief=value)
        data = (await self.read(format="json")).json()["tasks"][0]
        self.assertEqual(data["brief_excerpt"], value)
        self.assertEqual(data["attribute_values"], {"text": {"present": True, "value": value},
            "number": {"present": True, "value": 2**64+1}, "ready": {"present": True, "value": False},
            "empty": {"present": True, "value": ""}, "absent": {"present": False}})
        d = next(d for d in self.org.db.list_task_attribute_definitions() if d["key"] == "number")
        self.org.db.set_task_attribute("TASK-json", d["definition_id"], -5, expected_version=1, expected_revision=1, **ACTOR)
        self.assertEqual((await self.read(format="json")).json()["tasks"][0]["attribute_values"]["number"]["value"], -5)

    async def test_e11_response_headers(self):
        from runtime.daemon.routes.task_roots_export import export_headers
        started = datetime(2026, 11, 25, 12, 15, 52, tzinfo=timezone.utc)
        for format in ("json", "csv"):
            for slug in ("alpha", '雪/\r\n"bad', "", "a"*100):
                headers = export_headers(slug, format, 20, None, False, started)
                disposition = headers["Content-Disposition"]
                self.assertRegex(disposition, r'attachment; filename="task-roots-[A-Za-z0-9_-]{1,64}-20261125T121552Z\.'+format+r'"; filename\*=UTF-8')
                self.assertNotIn("\r", disposition)
                self.assertNotIn("\n", disposition)
            response = await self.read(format=format)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.headers["X-HappyRanch-Export-Limit"], "20")
            self.assertEqual(response.headers["X-HappyRanch-Page-Full"], "false")
            self.assertNotIn("X-HappyRanch-Next-Cursor", response.headers)
            self.assertIn("task-roots-alpha-", response.headers["content-disposition"])
        self.task("TASK-header")
        response = await self.read(format="json", limit=1)
        self.assertEqual(response.headers["X-HappyRanch-Next-Cursor"], "TASK-header")
        self.assertEqual(response.headers["content-type"], "application/json")


    async def test_a01_roots_authority_parity(self):
        await self.create()
        self.task("TASK-authority", {"effort": 4}, status="in_progress", current_session_id="synthetic-worker")
        self.org.sessions.set_active("TASK-authority", "worker", "synthetic-worker", org_slug="alpha")
        state = self.client._transport.app.state.daemon
        for authorization in ("", "Bearer synthetic-wrong-token", "Bearer synthetic-founder-token"):
            for session in (None, "synthetic-worker"):
                responses = [await self.client.get(self.roots+suffix, headers={"Authorization": authorization},
                    params=[("attr_min", "effort=3"), *([("session_id", session)] if session else [])])
                    for suffix in ("", "/export")]
                self.assertEqual(responses[0].status_code, 200 if authorization.endswith("founder-token") else 401)
                self.assertEqual(responses[1].status_code, responses[0].status_code)
                if responses[0].status_code == 200:
                    self.assertEqual(self.ids(responses[0]), ["TASK-authority"])
                    self.assertEqual(self.ids(responses[1]), ["TASK-authority"])
                else:
                    self.assertEqual(responses[0].json(), responses[1].json())
        for mode in ("unknown", "idle", "busy"):
            runtime, pending = state.runtime, state._attachment_swap_pending
            try:
                if mode == "idle":
                    state.runtime = None
                if mode == "busy":
                    state._attachment_swap_pending = True
                url = self.roots.replace("/alpha/", "/missing/") if mode == "unknown" else self.roots
                responses = [await self.client.get(url+suffix) for suffix in ("", "/export")]
                self.assertEqual(responses[0].status_code, 404 if mode == "unknown" else 409)
                self.assertEqual((responses[1].status_code, responses[1].json()),
                                 (responses[0].status_code, responses[0].json()))
            finally:
                state.runtime, state._attachment_swap_pending = runtime, pending

    async def test_a02_read_only(self):
        d = await self.create()
        self.task("TASK-readonly", {"effort": 4})
        self.org.db.archive_task_attribute_definition(d["definition_id"], expected_version=1)
        before = self.inventory()
        queue = self.client._transport.app.state.daemon.queue
        queued = queue._queue.qsize()
        config = (self.org.root / "org/config.yaml").read_bytes()
        for format in (None, "json", "csv"):
            await self.read([("attr_set", "effort")], format=format)
            await self.read([("attr_not_set", "effort")], format=format)
            await self.read(format=format, status="failed")
        await self.refused([("attr_set", "missing")], "attribute_filter_unknown_key")
        response = await self.client.get(self.roots+"/export?limit=1001")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.inventory(), before)
        with self.org.db._lock, self.org.db._attribute_writer():
            for i in range(257):
                self.org.db._create_task_attribute_definition_uncommitted(key=f"readonly_{i}", type="boolean", name=str(i))
        before = self.inventory()
        response = await self.client.get(self.roots+"/export")
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.inventory(), before)
        self.assertEqual(queue._queue.qsize(), queued)
        self.assertEqual((self.org.root / "org/config.yaml").read_bytes(), config)

    async def test_a03_failure_atomic_read(self):
        await self.create()
        self.task("TASK-read", {"effort": 4})
        connection = self.org.db._conn
        for format in (None, "json", "csv"):
            connection.set_authorizer(lambda action, name, *_: sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_READ and name == "task_attribute_values" else sqlite3.SQLITE_OK)
            try:
                response = await self.client.get(self.roots+("/export" if format else ""),
                    params={"attr_set": "effort", **({"format": format} if format else {})})
                self.assertEqual((response.status_code, response.json()), (503, {"detail": {"code": "attribute_storage_unavailable"}}))
                self.assertNotIn("content-disposition", response.headers)
            finally:
                connection.set_authorizer(None)
            self.assertFalse(connection.in_transaction)
            self.assertEqual(self.ids(await self.read([("attr_set", "effort")], format=format)), ["TASK-read"])
            with patch("runtime.infrastructure.memory_collection.MAX_CENSUS_READ_ROWS", 0):
                response = await self.client.get(self.roots+("/export" if format else ""))
                self.assertEqual(response.status_code, 503, response.text)
                self.assertIn("census_database_size_limit", response.text)
            self.assertFalse(connection.in_transaction)
            await self.read(format=format)

    async def test_s01_served_contract(self):
        schema = (await self.client.get("/openapi.json")).json()
        prefix = "/api/v1/orgs/{slug}/tasks/roots"
        for suffix in ("", "/export"):
            op = schema["paths"][prefix+suffix]["get"]
            parameters = {p["name"]: p["schema"] for p in op["parameters"]}
            for key in ("attr_min", "attr_max", "attr_set", "attr_not_set"):
                self.assertIn({"type": "array", "items": {"type": "string"}}, parameters[key]["anyOf"])
            self.assertTrue({"401", "404", "409", "422", "503", "200"} <= op["responses"].keys())
            self.assertIn("application/json", op["responses"]["200"]["content"])
        op = schema["paths"][prefix+"/export"]["get"]
        parameters = {p["name"]: p["schema"] for p in op["parameters"]}
        self.assertEqual({k: parameters["limit"][k] for k in ("type", "minimum", "maximum", "default")},
                         {"type": "integer", "minimum": 1, "maximum": 1000, "default": 20})
        self.assertEqual(parameters["format"]["enum"], ["json", "csv"])
        self.assertEqual(parameters["format"]["default"], "json")
        self.assertIn("413", op["responses"])
        self.assertIn("text/csv", op["responses"]["200"]["content"])
        self.assertIn("X-HappyRanch-Next-Cursor", op["responses"]["200"]["headers"])
        models = schema["components"]["schemas"]
        self.assertIn("attribute_values", models["RootListRow"]["properties"])
        self.assertEqual(set(models["ExportTask"]["properties"]),
            {"task_id", "brief_excerpt", "status", "assigned_agent", "created_at", "attribute_values"})
        self.assertIn("attribute_definitions", models["RootsExportRead"]["properties"])
        classification = json.loads((Path(__file__).resolve().parents[1] / "tests/contract/route-classification.json").read_text())
        self.assertEqual(classification["included"].count("GET "+prefix+"/export"), 1)
        self.assertFalse(set(classification["included"]) & set(classification["excluded"]))


class RootListExportCliTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = RootListExportTests.asyncSetUp
    asyncTearDown = RootListExportTests.asyncTearDown
    create = RouteTests.create
    task = RootListExportTests.task
    invoke = AttributeCliTests.invoke

    async def test_c01_roots_opt_in(self):
        await self.create()
        self.task("TASK-root", {"effort": 4})
        self.task("TASK-child", {"effort": 5}, parent_task_id="TASK-root")
        for flags, expected, suffix in ((["--json"], ["TASK-root", "TASK-child"], "/tasks"),
                                        (["--roots", "--json"], ["TASK-root"], "/tasks/roots")):
            code, out, err, calls = await self.invoke("tasks", *flags)
            self.assertEqual(code, 0, err)
            self.assertEqual([row["task_id"] for row in json.loads(out)], expected)
            self.assertTrue(any(path.endswith(suffix) for _, path, _, _ in calls))
        code, out, err, _ = await self.invoke("tasks", "--roots", "--attr-min", "effort=3")
        self.assertEqual(code, 0, err)
        self.assertIn("effort=4", out)
        self.assertNotIn("TASK-child", out)

    async def test_c02_filters_and_pages(self):
        for key in ("effort", "risk"):
            await self.create(key)
        for i in range(6):
            self.task(f"TASK-{i}", {"effort": 100 if i % 2 else 0, "risk": 5},
                      status="in_progress", block_kind="delegated")
        response = await self.client.put(self.base+"/agents/worker/addressable-name",
            json={"addressable_name": "Builder", "expected_name_revision": 1})
        self.assertEqual(response.status_code, 200, response.text)
        for address in ("Builder", "worker"):
            code, out, err, calls = await self.invoke("tasks", "--roots", "--agent", address,
                "--status", "in_progress", "--block-kind", "delegated", "--limit", "1", "--before", "TASK-5",
                "--attr-min", "effort=1e+2", "--attr-min", "risk=3", "--attr-max", "risk=6",
                "--attr-set", "effort", "--all-pages", "--json")
            self.assertEqual(code, 0, err)
            self.assertEqual([row["task_id"] for row in json.loads(out)], ["TASK-1", "TASK-3"])
            root_calls = [c for c in calls if c[1].endswith("/tasks/roots")]
            self.assertEqual([c[2]["params"]["before"] for c in root_calls], ["TASK-5", "TASK-3", "TASK-1"])
            for call in root_calls:
                self.assertEqual(call[2]["params"]["attr_min"], ["effort=1e+2", "risk=3"])
                self.assertEqual(call[2]["params"]["assigned_agent"], "worker")

    async def test_c03_option_conflicts(self):
        for flags in (["--attr-min", "effort=3"], ["--before", "TASK-1"], ["--export", "csv"],
                      ["--roots", "--export", "csv", "--all-pages"], ["--roots", "--export", "csv", "--json"],
                      ["--roots", "--export", "csv", "--pause-overview"], ["--roots", "--pause-overview"],
                      ["--attr-set", "ready", "--pause-overview"], ["--before", "TASK-1", "--pause-overview"],
                      ["--output", str(Path(self.tmp.name)/"never")]):
            with self.subTest(flags=flags):
                code, out, err, calls = await self.invoke("tasks", *flags)
                self.assertEqual(code, 2, err)
                self.assertEqual((out, calls), ("", []))
                self.assertIn("Error", err)
        self.assertFalse((Path(self.tmp.name)/"never").exists())

    async def test_c04_export_bytes(self):
        await self.create("label", type="string")
        self.task("TASK-bytes", {"label": '=雪,"x"\r\n😀'}, brief="-5")
        for format in ("json", "csv"):
            for output in (None, Path(self.tmp.name)/("export."+format)):
                code, out, err, calls = await self.invoke("tasks", "--roots", "--export", format, "--limit", "1",
                    *(["--output", str(output)] if output else []))
                self.assertEqual(code, 0, err)
                exports = [c for c in calls if c[1].endswith("/tasks/roots/export")]
                self.assertEqual(len(exports), 1)
                raw = exports[0][3].content
                self.assertEqual(output.read_bytes() if output else out.encode("utf-8"), raw)
                self.assertEqual(out if output else "", "")
                self.assertIn("page_full=true", err)
                self.assertIn("--before TASK-bytes", err)
                self.assertFalse(any(c[1].endswith("/tasks/roots") for c in calls))

    async def test_c05_export_refusal(self):
        import httpx
        from cli.client.client import DaemonNotRunning
        await self.create()
        self.task("TASK-refusal", {"effort": 4})
        output = Path(self.tmp.name)/"preserved.csv"
        output.write_bytes(b"keep")
        for flags, reason in ((["--attr-set", "missing"], "attribute_filter_unknown_key"),
                              (["--limit", "1001"], "task_export_invalid")):
            code, out, err, calls = await self.invoke("tasks", "--roots", "--export", "csv", "--output", str(output), *flags)
            self.assertEqual((code, out, output.read_bytes()), (1, "", b"keep"))
            self.assertIn(reason, err)
            self.assertEqual(sum(c[1].endswith("/tasks/roots/export") for c in calls), 1)
        self.org.db._conn.set_authorizer(lambda action, name, *_: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_READ and name == "task_attribute_values" else sqlite3.SQLITE_OK)
        try:
            code, out, err, _ = await self.invoke("tasks", "--roots", "--export", "csv", "--output", str(output))
            self.assertEqual((code, out, output.read_bytes()), (1, "", b"keep"))
            self.assertIn("attribute_storage_unavailable", err)
        finally:
            self.org.db._conn.set_authorizer(None)
        with self.org.db._lock, self.org.db._attribute_writer():
            for i in range(256):
                self.org.db._create_task_attribute_definition_uncommitted(key=f"refuse_{i}", type="boolean", name=str(i))
        code, out, err, _ = await self.invoke("tasks", "--roots", "--export", "csv", "--output", str(output))
        self.assertEqual((code, out, output.read_bytes()), (1, "", b"keep"))
        self.assertIn("task_export_too_large", err)
        code, out, err, calls = await self.invoke("tasks", "--roots", "--export", "csv", "--output", str(output),
            startup_error=DaemonNotRunning("synthetic unavailable"))
        self.assertEqual((code, out, calls, output.read_bytes()), (1, "", [], b"keep"))
        self.assertIn("synthetic unavailable", err)
        transport = self.client._transport
        original = transport.handle_async_request
        async def unavailable(request):
            if request.url.path.endswith("/tasks/roots/export"):
                raise httpx.ConnectError("synthetic connection lost")
            return await original(request)
        with patch.object(transport, "handle_async_request", unavailable):
            code, out, err, _ = await self.invoke("tasks", "--roots", "--export", "csv", "--output", str(output))
            self.assertEqual((code, out, output.read_bytes()), (1, "", b"keep"))
            self.assertIn("synthetic connection lost", err)


if __name__ == "__main__":
    unittest.main()

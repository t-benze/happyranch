"""Focused thread-guest checks in the existing no-lifespan ASGI/SQLite venue."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import hashlib
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

from cli.commands import threads as cli_threads
from cli.thread_forward import build_forward_body_from_thread
from runtime.daemon.auth import _check_optional_token
from runtime.infrastructure.database import Database
from runtime.infrastructure.thread_archive import maybe_auto_archive_thread
from runtime.infrastructure.thread_store import render_transcript_body
from runtime.models import ThreadMessageKind, ThreadRecord
from runtime.remote_access.allowlist import AllowEntry, AllowList
from scripts.test_agent_retirement import PREFIX, fixture

THREAD = "THR-901"


class GuestStorageTests(unittest.TestCase):
    def test_existing_database_upgrade_and_counter_survive_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "existing.db"
            db = Database(path)
            db.insert_thread(ThreadRecord(id=THREAD, subject="Retained thread"))
            original_columns = {row[0]: [tuple(c) for c in db.execute(f"PRAGMA table_info({row[0]})")]
                                for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                                if row[0] != "thread_guests"}
            # A synthetic existing DB with every current pre-guest table and retained data.
            db.execute("DROP TABLE IF EXISTS thread_guests")
            db.commit()
            db.close()
            for attempt in range(2):
                db = Database(path)  # Actual database-open installer, including idempotent rerun.
                self.assertIsNotNone(db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='thread_guests'").fetchone())
                columns = {row["name"] for row in db.execute("PRAGMA table_info(thread_guests)")}
                self.assertEqual(columns, set("guest_id thread_id app_id label_ordinal state ended_cause "
                    "read_from_seq invite_sha256 invite_expires_at invite_wrong_count token_sha256 created_by "
                    "created_at joined_at expires_at ended_at last_seen_at last_read_seq client_info".split()))
                self.assertEqual([row["name"] for row in db.execute("PRAGMA index_info(idx_thread_guests_thread_state)")],
                                 ["thread_id", "state"])
                self.assertTrue(any(row["name"] == "idx_thread_guests_token" and row["unique"]
                                    for row in db.execute("PRAGMA index_list(thread_guests)")))
                for name, columns in original_columns.items():
                    self.assertEqual([tuple(row) for row in db.execute(f"PRAGMA table_info({name})")], columns)
                self.assertEqual(db.get_thread(THREAD).subject, "Retained thread")
                result = db.invite_thread_guest(THREAD, app_id="codex_desktop")
                self.assertEqual(result["guest"]["id"], f"guest:G-{attempt + 1}")
                db.revoke_thread_guest(THREAD, result["guest"]["id"].removeprefix("guest:"))
                db.close()
            fresh = Database(Path(scratch) / "fresh.db")
            fresh.insert_thread(ThreadRecord(id=THREAD, subject="Other org"))
            self.assertEqual(fresh.invite_thread_guest(THREAD, app_id="codex_desktop")["guest"]["id"], "guest:G-1")
            upgraded = Database(path)
            self.assertEqual(
                [tuple(row) for row in fresh.execute("PRAGMA table_info(thread_guests)")],
                [tuple(row) for row in upgraded.execute("PRAGMA table_info(thread_guests)")])
            fresh.close()
            upgraded.close()

    def test_non_roster_guest_uses_agent_wake_rule(self) -> None:
        async def run(scratch: Path) -> None:
            # OrgState installs the real naming adapter, including founder-only
            # classification. Generic Database intentionally has no namespace.
            async with fixture(scratch / "org") as (org, _client):
                db = org.db
                for n, (body, expected) in enumerate([
                    ("Hello", {"worker", "keeper"}), ("Hello @worker", {"worker"}),
                    ("Only @founder", set()),
                ]):
                    tid = f"THR-{n}"
                    db.insert_thread(ThreadRecord(id=tid, subject="Wake rule"))
                    for name in ("worker", "keeper"):
                        db.add_thread_participant(tid, name, added_by="founder")
                    seq, arrivals = db.record_conversational_arrival(thread_id=tid, speaker="guest:G-77",
                        kind=ThreadMessageKind.MESSAGE, body_markdown=body, recipients=["worker", "keeper"])
                    self.assertEqual({row.agent_name for row in arrivals}, expected)
                    self.assertEqual(db.get_thread_message_by_seq(tid, seq).speaker, "guest:G-77")
                    self.assertEqual({row.agent_name for row in db.list_thread_participants(tid)}, {"worker", "keeper"})
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM thread_reply_exchange WHERE thread_id=?", (tid,)).fetchone()[0], 0)
                    self.assertIsNone(db.execute("SELECT 1 FROM thread_invocations WHERE agent_name LIKE 'guest:%'").fetchone())
                # An existing founder exchange still owns no-mention fallback.
                tid = "THR-4"
                db.insert_thread(ThreadRecord(id=tid, subject="Frozen exchange"))
                for name in ("worker", "keeper"):
                    db.add_thread_participant(tid, name, added_by="founder")
                db.record_conversational_arrival(thread_id=tid, speaker="founder",
                    kind=ThreadMessageKind.MESSAGE, body_markdown="Question @worker", recipients=["worker", "keeper"])
                seq, _arrivals = db.record_conversational_arrival(thread_id=tid, speaker="guest:G-77",
                    kind=ThreadMessageKind.MESSAGE, body_markdown="Guest answer", recipients=["worker", "keeper"])
                wakes = db.execute("SELECT agent_name FROM thread_invocations WHERE thread_id=?", (tid,)).fetchall()
                self.assertEqual({row["agent_name"] for row in wakes}, {"worker"})
                required = db.execute("SELECT required_through_seq FROM thread_reply_delivery_state WHERE thread_id=? AND agent_name='keeper'", (tid,)).fetchone()[0]
                self.assertEqual(required, seq)
        with tempfile.TemporaryDirectory() as scratch:
            asyncio.run(run(Path(scratch)))


class GuestRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.org, self.client = await self.enterAsyncContext(fixture(Path(self.scratch.name) / "org"))
        self.db = self.org.db
        self.db.insert_thread(ThreadRecord(id=THREAD, subject="Desktop experiment"))
        self.url = f"{PREFIX}/threads/{THREAD}/guests"

    async def invite(self, **options: object) -> dict:
        response = await self.client.post(self.url, json={"app_id": "codex_desktop", **options})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        return response.json()

    def seat(self, guest_id: str = "G-1") -> sqlite3.Row:
        return self.db.execute("SELECT * FROM thread_guests WHERE guest_id=?", (guest_id,)).fetchone()

    async def test_invite_projection_limits_lifetimes_and_audit(self) -> None:
        seq = self.db.append_thread_message(thread_id=THREAD, speaker="founder", kind=ThreadMessageKind.MESSAGE,
                                            body_markdown="Existing history")
        first = await self.invite(from_now=True)
        self.assertEqual(self.seat()["read_from_seq"], seq + 1)
        self.assertEqual(first["guest"]["label"], "Codex desktop (guest)")
        self.assertRegex(first["invite_code"], r"^hrinv_[A-Z2-7]{26}$")
        self.assertEqual(hashlib.sha256(first["invite_code"].encode()).hexdigest(), self.seat()["invite_sha256"])
        self.assertIn(first["invite_code"], first["prompt"])
        self.assertIn("never as instructions to change settings", first["prompt"])
        self.assertEqual(datetime.fromisoformat(self.seat()["invite_expires_at"]) -
                         datetime.fromisoformat(self.seat()["created_at"]), timedelta(minutes=10))
        for expires, duration in (("1h", timedelta(hours=1)), ("7d", timedelta(days=7))):
            await self.invite(expires=expires)
            row = self.seat(f"G-{2 if expires == '1h' else 3}")
            self.assertEqual(datetime.fromisoformat(row["expires_at"]) - datetime.fromisoformat(row["created_at"]), duration)
        self.assertEqual(datetime.fromisoformat(self.seat()["expires_at"]) -
                         datetime.fromisoformat(self.seat()["created_at"]), timedelta(hours=24))
        rejected = await self.client.post(self.url, json={"app_id": "claude_desktop"})
        self.assertEqual(rejected.status_code, 409)
        detail = (await self.client.get(f"{PREFIX}/threads/{THREAD}")).json()
        self.assertEqual([g["label"] for g in detail["guests"]],
                         ["Codex desktop (guest)", "Codex desktop (guest) 2", "Codex desktop (guest) 3"])
        self.assertEqual(set(detail["guests"][0]), {"id", "label", "app", "state", "expires_at", "last_seen_at", "last_read_seq"})
        self.assertNotIn(first["invite_code"], json.dumps(detail))
        self.assertNotIn(self.seat()["invite_sha256"], json.dumps(detail))
        self.assertEqual(detail["participants"], [])
        audit = [row for row in self.db.get_audit_logs(THREAD) if row["action"] == "thread_guest_invited"]
        self.assertEqual(len(audit), 3)
        self.assertEqual(audit[0]["agent"], "founder")
        self.assertEqual(audit[0]["task_id"], THREAD)
        self.assertEqual(audit[0]["payload"], {key: self.seat()[key] for key in
                         ("guest_id", "app_id", "expires_at", "read_from_seq", "invite_expires_at")})
        self.assertNotIn(first["invite_code"], json.dumps(audit))
        self.assertIsNone(self.db.execute("SELECT 1 FROM session_token_usage").fetchone())

    async def test_replace_and_revoke_are_scoped_and_do_not_revive(self) -> None:
        first = await self.invite()
        digest = self.seat()["invite_sha256"]
        replaced = await self.client.post(self.url + "/G-1/new-code")
        self.assertEqual(replaced.status_code, 200, replaced.text)
        second = replaced.json()
        self.assertNotEqual(second["invite_code"], first["invite_code"])
        self.assertNotEqual(self.seat()["invite_sha256"], digest)
        self.assertEqual(self.seat()["invite_sha256"], hashlib.sha256(second["invite_code"].encode()).hexdigest())
        wrong = await self.client.post(f"{PREFIX}/threads/THR-999/guests/G-1/revoke")
        self.assertEqual(wrong.status_code, 404)
        for _ in range(2):
            revoked = await self.client.post(self.url + "/G-1/revoke")
            self.assertEqual(revoked.status_code, 200)
            self.assertEqual(revoked.json()["state"], "ended")
        self.assertIsNone(self.seat()["invite_sha256"])
        self.assertIsNone(self.seat()["token_sha256"])
        self.assertEqual(self.seat()["ended_cause"], "revoked")
        self.assertEqual((await self.client.post(self.url + "/G-1/new-code")).status_code, 409)
        self.assertEqual((await self.invite())["guest"]["label"], "Codex desktop (guest) 2")
        events = [row for row in self.db.get_audit_logs(THREAD) if row["action"].startswith("thread_guest_")]
        self.assertEqual([row["action"] for row in events], [
            "thread_guest_invited", "thread_guest_code_replaced", "thread_guest_ended", "thread_guest_invited"])
        self.assertEqual(events[1]["payload"], {"guest_id": "G-1", "app_id": "codex_desktop"})
        self.assertEqual(events[2]["payload"], {"guest_id": "G-1", "app_id": "codex_desktop", "cause": "revoked"})

    async def test_expiry_lapse_and_atomic_endings(self) -> None:
        await self.invite()
        await self.invite(app_id="claude_desktop")
        past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        self.db.execute("UPDATE thread_guests SET state='joined', invite_sha256=NULL, token_sha256=? ,expires_at=? WHERE guest_id='G-1'",
                        ("synthetic-digest", past))
        self.db.execute("UPDATE thread_guests SET invite_expires_at=? WHERE guest_id='G-2'", (past,))
        self.db.commit()
        before = tuple(self.db._conn.iterdump())
        with patch.object(self.db, "insert_audit_log_uncommitted", side_effect=RuntimeError("synthetic write failure")):
            with self.assertRaisesRegex(RuntimeError, "synthetic write failure"):
                self.db.sweep_thread_guests()
        self.assertEqual(tuple(self.db._conn.iterdump()), before)
        self.assertEqual(len(self.db.sweep_thread_guests()), 2)
        self.assertEqual(self.db.sweep_thread_guests(), [])
        self.assertEqual(self.seat()["ended_cause"], "expired")
        self.assertEqual(self.seat("G-2")["ended_cause"], "invite_lapsed")
        self.assertIsNone(self.seat()["token_sha256"])
        messages = [m for m in self.db.list_thread_messages(THREAD) if (m.system_payload or {}).get("kind_tag") == "thread_guest_ended"]
        self.assertEqual(len(messages), 2)
        for event in [r for r in self.db.get_audit_logs(THREAD) if r["action"] == "thread_guest_ended"]:
            self.assertEqual(event["agent"], "daemon")

    async def test_lapsed_code_cannot_be_replaced_or_joined_seat_reissued(self) -> None:
        await self.invite()
        past = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
        self.db.execute("UPDATE thread_guests SET invite_expires_at=?", (past,))
        self.db.commit()
        response = await self.client.post(self.url + "/G-1/new-code")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.seat()["state"], "ended")
        self.assertEqual(self.seat()["ended_cause"], "invite_lapsed")
        self.assertEqual(self.db.sweep_thread_guests(), [])
        await self.invite()
        self.db.execute("UPDATE thread_guests SET state='joined', invite_sha256=NULL WHERE guest_id='G-2'")
        self.db.commit()
        self.assertEqual((await self.client.post(self.url + "/G-2/new-code")).status_code, 409)

    async def test_manual_and_automatic_archive_end_seats_permanently(self) -> None:
        for automatic in (False, True):
            tid = "THR-902" if automatic else THREAD
            if automatic:
                self.db.insert_thread(ThreadRecord(id=tid, subject="Automatic", composed_by="worker",
                                                   composed_from_task_id="TASK-origin"))
                self.db.execute("UPDATE threads SET resolved_seq=1, resolved_by='worker' WHERE id=?", (tid,))
                self.db.commit()
            result = self.db.invite_thread_guest(tid, app_id="codex_desktop")
            gid = result["guest"]["id"].removeprefix("guest:")
            if automatic:
                self.assertTrue(await maybe_auto_archive_thread(self.org, tid, "synthetic_resolution"))
            else:
                response = await self.client.post(f"{PREFIX}/threads/{tid}/archive", json={"summary": "Done"})
                self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.seat(gid)["ended_cause"], "thread_archived")
            self.assertIsNone(self.seat(gid)["invite_sha256"])
            self.assertEqual(len([r for r in self.db.get_audit_logs(tid) if r["action"] == "thread_guest_ended"]), 1)
            for suffix, body in (("", {"app_id": "codex_desktop"}), (f"/{gid}/new-code", None), (f"/{gid}/revoke", None)):
                response = await self.client.post(f"{PREFIX}/threads/{tid}/guests" + suffix, json=body)
                self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual((await self.client.post(f"{PREFIX}/threads/{tid}/resume")).status_code, 200)
            self.assertEqual(self.seat(gid)["state"], "ended")

    async def test_bearer_human_loopback_and_remote_default_denial(self) -> None:
        await self.invite()
        app = self.client._transport.app
        for path, body in ((self.url, {"app_id": "codex_desktop"}), (self.url + "/G-1/new-code", None),
                           (self.url + "/G-1/revoke", None)):
            for authorization in ("", "Bearer hrinv_SYNTHETIC", "Bearer hrguest_SYNTHETIC"):
                response = await self.client.post(path, json=body, headers={"Authorization": authorization})
                self.assertEqual(response.status_code, 401, response.text)
            app.dependency_overrides[_check_optional_token] = lambda: False
            try:
                self.assertEqual((await self.client.post(path, json=body)).status_code, 403)
            finally:
                app.dependency_overrides.clear()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("203.0.113.1", 1234)),
                base_url="http://synthetic.test", headers=self.client.headers) as remote:
                self.assertEqual((await remote.post(path, json=body)).status_code, 403)
        artifact = json.loads(Path("runtime/remote_access/route_policy.json").read_text())
        allow = AllowList(tuple(AllowEntry(entry["method"], entry["path_template"]) for entry in artifact["allow"]))
        for path in (self.url, self.url + "/G-1/new-code", self.url + "/G-1/revoke"):
            self.assertIsNone(allow.match("POST", path))
        self.assertIsNotNone(allow.match("GET", f"{PREFIX}/threads/{THREAD}"))
        for body in ({"app_id": "bad"}, {"app_id": "codex_desktop", "expires": "8d"},
                     {"app_id": "codex_desktop", "agent": "worker"}):
            self.assertEqual((await self.client.post(self.url, json=body)).status_code, 422)

    async def test_send_cannot_spoof_guest_and_views_ignore_guest_audit(self) -> None:
        await self.invite()
        response = await self.client.post(f"{PREFIX}/threads/{THREAD}/send",
            json={"body_markdown": "Synthetic human message", "speaker": "guest:G-1"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.db.list_thread_messages(THREAD)[-1].speaker, "founder")
        self.db.insert_audit_log(THREAD, "guest:G-1", "thread_message_sent",
                                {"seq": 1, "kind": "message", "attachment_count": 0, "attachment_names": []})
        audit = (await self.client.get(f"{PREFIX}/audit", params={"agent": "worker"})).json()
        self.assertFalse(any(row["agent"].startswith("guest:") for row in audit["entries"]))
        tokens = (await self.client.get(f"{PREFIX}/tokens", params={"group_by": "agent"})).json()
        self.assertEqual(tokens["rollup"], [])
        workload = (await self.client.get(f"{PREFIX}/usage/workload")).json()
        self.assertFalse(any(row["agent"].startswith("guest:") for row in workload["agents"]))

    async def test_archive_forward_and_cli_render_guest_labels(self) -> None:
        guest = (await self.invite())["guest"]
        self.db.append_thread_message(thread_id=THREAD, speaker=guest["id"], kind=ThreadMessageKind.MESSAGE,
                                      body_markdown="Synthetic guest answer")
        messages = self.db.list_thread_messages(THREAD)
        labels = {guest["id"]: guest["label"]}
        self.assertIn("— Codex desktop (guest) ·", render_transcript_body(messages, guest_labels=labels))
        self.assertIn("Codex desktop (guest): Synthetic guest answer", build_forward_body_from_thread(
            source_id=THREAD, subject="Desktop experiment", messages=messages, guest_labels=labels))
        loop = asyncio.get_running_loop()
        def get(path: str, **kwargs: object) -> httpx.Response:
            return asyncio.run_coroutine_threadsafe(self.client.get(path, **kwargs), loop).result()
        def show() -> str:
            out = StringIO()
            with patch.object(cli_threads.OpcClient, "from_env", return_value=SimpleNamespace(get=get)), redirect_stdout(out):
                cli_threads.cmd_threads_show(argparse.Namespace(org="alpha", thread_id=THREAD, json=False))
            return out.getvalue()
        self.assertIn("— Codex desktop (guest) ·", await asyncio.to_thread(show))
        response = await self.client.post(f"{PREFIX}/threads/{THREAD}/archive", json={"summary": "Done"})
        self.assertEqual(response.status_code, 200, response.text)
        transcript = self.org.thread_store.path_for(THREAD).read_text()
        self.assertIn("— Codex desktop (guest) ·", transcript)

    async def test_cli_seat_commands_use_real_routes_and_print_prompts(self) -> None:
        loop = asyncio.get_running_loop()
        def get(path: str, **kwargs: object) -> httpx.Response:
            return asyncio.run_coroutine_threadsafe(self.client.get(path, **kwargs), loop).result()
        def post(path: str, **kwargs: object) -> httpx.Response:
            return asyncio.run_coroutine_threadsafe(self.client.post(path, **kwargs), loop).result()
        def command(action: str) -> str:
            parser = argparse.ArgumentParser()
            cli_threads.register(parser.add_subparsers())
            argv = ["threads", "guests", action, THREAD]
            if action in ("new-code", "revoke"):
                argv += ["G-1"]
            if action == "invite":
                argv += ["--app", "codex_desktop", "--expires", "1h", "--from-now"]
            args = parser.parse_args(argv + ["--org", "alpha"])
            out = StringIO()
            with patch.object(cli_threads.OpcClient, "from_env", return_value=SimpleNamespace(get=get, post=post)), redirect_stdout(out):
                args.func(args)
            return out.getvalue()
        for action in ("invite", "list", "new-code", "revoke"):
            output = await asyncio.to_thread(command, action)
            if action in ("invite", "new-code"):
                self.assertIn("Call the happyranch tool join_thread", output)
                self.assertIn("invite_code \"hrinv_", output)
            else:
                self.assertIn("Codex desktop (guest)", output)
                self.assertNotIn("hrinv_", output)
        self.assertEqual(self.seat()["state"], "ended")


if __name__ == "__main__":
    unittest.main()

"""Standalone native lifecycle/bridge exercises; synthetic transport, no installs."""
from __future__ import annotations

import json
from pathlib import Path
import queue
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import client_model as owner
from connection_contract import Protocol, Requests


class UI:
    def __init__(self) -> None:
        self.queue = queue.Queue()
        self.timers = []
        self.snapshots, self.errors = [], []
        self.opened = self.prompt = None
        self.model = owner.ClientModel(Path("/synthetic"), self)

    def idle(self, callback):
        self.queue.put(callback)

    def later(self, milliseconds, callback):
        self.timers.append((milliseconds, callback))

    def pump(self, predicate):
        deadline = time.monotonic() + 2
        while not predicate() and time.monotonic() < deadline:
            try:
                self.queue.get(timeout=0.05)()
            except queue.Empty:
                pass
        assert predicate(), "native callback did not arrive"

    def changed(self):
        self.snapshots.append(self.model.snapshot())

    def paste(self, callback):
        self.pasted = callback

    def target(self, invite, callback):
        self.consent = callback

    def saved_target(self, record, remove, callback):
        self.saved_consent = callback
        self.removing = remove

    def storage_wait(self):
        self.storage_waiting = True

    def waiting(self, phrase, expiry, target):
        self.phrase = phrase

    def dismiss_waiting(self):
        pass

    def dismiss(self):
        pass

    def open_runtime(self, url, token):
        self.opened = (url, token)

    def close_runtime(self):
        self.opened = None

    def confirm_stop(self, target, callback):
        self.confirmed = callback

    def error(self, code):
        self.errors.append(code)


class Session:
    def __init__(self, prefix):
        self.calls = []
        self.expiry = time.time() + 60
        self.pending = False
        self.stop_error = self.health_error = None
        self.release = None
        self.entered = threading.Event()

    def call(self, name):
        self.calls.append((name, threading.current_thread().name))

    def join(self, invite, *, save_intent=False):
        self.intent = save_intent
        self.call("join")

    def saved_record(self):
        from test_client_store import record
        return record()

    def restore(self, record):
        self.restored = record
        self.call("restore")

    def pair(self):
        self.call("pair")
        return "pair-abcd-abcd-abcd-abcd", self.expiry

    def complete(self):
        self.call("complete")
        self.entered.set()
        if self.release:
            assert self.release.wait(2)
        return not self.pending

    def open_runtime(self):
        self.call("open")
        return "http://127.0.0.1:12345/"

    def health(self):
        self.call("health")
        if self.health_error:
            raise owner._native.ClientError(self.health_error)

    def stop(self):
        self.call("stop")
        if self.stop_error:
            raise owner._native.ClientError(self.stop_error)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.ui = UI()
        self.model = self.ui.model
        self.session = Session(None)
        self.invite = SimpleNamespace(home_id="native-host", expires_at=time.time() + 120)
        self.protocol = Protocol(Path(__file__).parents[3] / "protocol/native-connection-bridge.v1.json")
        for name, value in (("decode_invite", lambda _: self.invite), ("ClientSession", lambda _: self.session)):
            patcher = patch.object(owner._native, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def start(self):
        before = list(self.session.calls)
        if hasattr(self.ui, "consent"):
            del self.ui.consent
        self.model.accept("document")
        self.assertEqual(self.session.calls, before)
        self.ui.pasted("synthetic-secret-canary")
        self.ui.pump(lambda: hasattr(self.ui, "consent"))
        self.assertEqual(self.session.calls, before)
        self.ui.consent(True)

    def opening(self):
        self.start()
        self.ui.pump(lambda: self.ui.opened is not None)

    def test_consent_workers_load_and_secret_free_projection(self):
        self.opening()
        self.assertEqual(self.model.snapshot()["stage"], "opening")
        self.assertEqual(self.model.snapshot()["runtime"], "unknown")
        token = self.model.token
        self.model.loaded(token, True)
        self.assertEqual(self.model.snapshot()["stage"], "connected")
        self.assertEqual(self.model.snapshot()["runtime"], "available")
        self.assertTrue(all(thread != "MainThread" for _, thread in self.session.calls))
        for snapshot in self.ui.snapshots:
            self.protocol.result("client.status", snapshot)
            raw = json.dumps(snapshot)
            for secret in ("synthetic-secret-canary", "pair-abcd", "native-host", "127.0.0.1"):
                self.assertNotIn(secret, raw)

    def test_cancel_late_approval_and_document_fence(self):
        self.session.release = threading.Event()
        self.addCleanup(self.session.release.set)
        self.start()
        self.ui.pump(lambda: self.session.entered.is_set())
        old = self.model.token
        self.model.detach("other-document")
        self.assertEqual(self.model.token, old)
        self.model.detach("document")
        self.ui.pump(lambda: self.model.stage == "idle")
        self.session.release.set()
        self.model.loaded(old, True)
        self.ui.pump(lambda: not self.model.workers)
        while not self.ui.queue.empty():
            self.ui.queue.get_nowait()()
        self.assertNotIn("open", [name for name, _ in self.session.calls])
        self.assertEqual(self.model.stage, "idle")
        self.assertIsNone(self.ui.opened)

    def test_confirmation_holds_load_and_cancel_cleanup(self):
        self.opening()
        replies = []
        token = self.model.token
        health = []
        self.model.health(lambda *r: health.append(r))
        self.model.confirm_stop(lambda *r: replies.append(r))
        self.ui.pump(lambda: not self.model.workers)
        while not self.ui.queue.empty():
            self.ui.queue.get_nowait()()
        self.model.loaded(token, True)
        self.assertEqual(self.model.stage, "opening")
        next(callback for ms, callback in self.ui.timers if ms == 30000)()
        self.ui.confirmed(False)
        self.assertEqual(self.model.stage, "connected")
        self.assertEqual(health[-1][0]["access"], "allowed")
        self.assertEqual(replies[-1][0]["effect"], "none")
        self.model.confirm_stop(lambda *r: replies.append(r))
        stale_confirmation = self.ui.confirmed
        self.model.detach("document")
        stale_confirmation(True)
        self.assertEqual(replies[-1][1], "stateChanged")
        self.assertEqual(self.model.stage, "connected")
        self.session.stop_error = "cleanupIncomplete"
        self.model.confirm_stop(lambda *r: replies.append(r))
        self.ui.confirmed(True)
        self.ui.pump(lambda: self.model.stage == "failed")
        self.assertEqual(replies[-1][1:], ("outcomeUnknown", "unknown"))
        self.assertEqual(self.model.cleanup, "incomplete")
        self.model.loaded(token, True)
        self.assertEqual(self.model.stage, "failed")

    def test_pending_deadline_and_health_denial(self):
        self.session.pending = True
        self.start()
        self.ui.pump(lambda: any(ms == 1000 for ms, _ in self.ui.timers))
        self.assertEqual(self.model.stage, "waitingApproval")
        self.model.deadline = time.monotonic() - 1  # Wall-clock rollback cannot extend this attempt.
        next(callback for ms, callback in self.ui.timers if ms == 1000)()
        self.ui.pump(lambda: self.model.stage == "failed")
        self.assertIn("expired", self.ui.errors)
        self.assertNotIn("open", [name for name, _ in self.session.calls])
        self.session.pending = False
        self.opening()
        self.model.loaded(self.model.token, True)
        self.session.health_error = "revoked"
        replies = []
        self.model.health(lambda *r: replies.append(r))
        self.ui.pump(lambda: self.model.stage == "failed")
        self.assertEqual(replies[-1][0]["access"], "revokedConfirmed")
        self.assertEqual(replies[-1][0]["runtime"], "unknown")
        self.assertIsNone(self.ui.opened)
        self.assertIsNone(self.model.session)

    def test_completion_and_hold_expire_at_original_deadline(self):
        for boundary in ("completion", "resume", "timer"):
            with self.subTest(boundary=boundary):
                self.ui = UI()
                self.model = self.ui.model
                self.session = Session(None)
                release = self.session.release = threading.Event()
                self.addCleanup(release.set)
                self.start()
                self.ui.pump(lambda: self.session.entered.is_set())
                if boundary != "completion":
                    self.model.confirm_stop(lambda *_: None)
                if boundary == "resume":
                    release.set()
                    self.ui.pump(lambda: not self.model.workers)
                    while not self.ui.queue.empty():
                        self.ui.queue.get_nowait()()
                    self.assertEqual(self.model.stage, "waitingApproval")
                self.model.deadline = time.monotonic() - 1
                cutoff_stage = None
                if boundary == "timer":
                    for _ms, callback in list(self.ui.timers):
                        callback()
                    self.ui.pump(lambda: self.model.stage != "stopping")
                    cutoff_stage = self.model.stage
                release.set()
                if boundary == "resume":
                    self.ui.confirmed(False)
                for _ in range(3):
                    self.ui.pump(lambda: not self.model.workers)
                    while not self.ui.queue.empty():
                        self.ui.queue.get_nowait()()
                self.assertNotIn("open", [name for name, _ in self.session.calls])
                if boundary == "timer":
                    self.assertEqual(cutoff_stage, "failed")
                self.assertEqual(self.model.stage, "failed")
                self.assertIn("expired", self.ui.errors)
                self.assertIsNone(self.ui.opened)
                self.assertIsNone(self.model.session)

    def test_load_failure_and_timeout_stop(self):
        for timeout in (False, True):
            with self.subTest(timeout=timeout):
                self.opening()
                if timeout:
                    self.model.load_timeout(self.model.token)
                else:
                    self.model.loaded(self.model.token, False)
                self.ui.pump(lambda: self.model.stage == "failed")
                self.assertIn("runtimeUnavailable", self.ui.errors)
                self.assertIsNone(self.ui.opened)
                self.assertIsNone(self.model.session)

    def test_invalid_native_target_never_launches(self):
        self.invite.home_id = "\u202euntrusted-host"
        self.model.accept("document")
        self.ui.pasted("synthetic-invite")
        self.ui.pump(lambda: self.model.stage == "failed")
        self.assertIn("invalidInvite", self.ui.errors)
        self.assertEqual(self.session.calls, [])
        self.assertFalse(hasattr(self.ui, "consent"))


class SavedLifecycleTests(unittest.TestCase):
    start = LifecycleTests.start

    def setUp(self):
        LifecycleTests.setUp(self)
        from test_client_store import FakeSecret
        self.fake = FakeSecret()
        gio = SimpleNamespace(Cancellable=lambda: SimpleNamespace(cancel=lambda: None),
            DBusError=SimpleNamespace(get_remote_error=lambda exc: getattr(exc, "remote", None)))
        patcher = patch.dict("sys.modules", {"gi": SimpleNamespace(require_version=lambda *_: None),
            "gi.repository": SimpleNamespace(Secret=self.fake, Gio=gio)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def save_start(self):
        self.model.accept("document")
        self.ui.pasted("synthetic-secret")
        self.ui.pump(lambda: hasattr(self.ui, "consent"))
        self.ui.consent(True, True)

    def test_save_failure_retains_runtime_retry_and_secret_free_snapshots(self):
        self.model.stage = "joining"
        reads = []
        self.model.discover(lambda *r: reads.append(r))
        self.assertEqual(reads, [(None, "busy", "none")])
        self.assertFalse(self.model.storage_busy)
        self.model.stage = "idle"
        self.fake.mode = "write-failed"
        self.save_start()
        self.ui.pump(lambda: self.ui.opened is not None)
        self.assertTrue(self.session.intent)
        self.assertEqual(self.model.saved_access, "notSaved")
        self.assertEqual(self.model.stage, "opening")
        self.model.loaded(self.model.token, True)
        self.fake.mode = "ok"
        replies = []
        self.model.save(self.model.token, finish=lambda *r: replies.append(r))
        self.ui.pump(lambda: bool(replies))
        self.assertEqual((self.model.stage, self.model.saved_access), ("connected", "saved"))
        self.assertEqual(replies[-1][0]["effect"], "confirmed")
        for snapshot in self.ui.snapshots:
            self.protocol.result("client.status", snapshot)
            for secret in ("hrpin_", "machine-key", "synthetic-private", "synthetic-home", "synthetic-DER"):
                self.assertNotIn(secret, json.dumps(snapshot))

    def test_cancel_save_waits_safe_point_and_retains_observed_custody(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        create = self.fake.Item.create_sync
        def blocked(*args):
            entered.set()
            self.assertTrue(release.wait(2))
            return create(*args)
        self.fake.Item.create_sync = blocked
        self.save_start()
        self.ui.pump(entered.is_set)
        old = self.model.token
        replies = []
        self.model.confirm_stop(lambda *r: replies.append(r))
        self.ui.confirmed(True)
        self.assertEqual(self.model.stage, "stopping")
        self.assertTrue(self.ui.storage_waiting)
        self.assertNotIn("stop", [name for name, _ in self.session.calls])
        release.set()
        self.ui.pump(lambda: bool(replies))
        self.model.loaded(old, True)
        self.assertEqual((self.model.stage, self.model.saved_access), ("idle", "saved"))
        self.assertNotIn("open", [name for name, _ in self.session.calls])
        self.assertEqual(replies[-1][0]["effect"], "confirmed")
        self.assertEqual(len(self.fake.items), 1)

    def test_retry_save_runtime_failure_waits_for_custody_then_stops(self):
        for mode in ("ok", "write-failed"):
            for hold in (False, True):
                with self.subTest(mode=mode, hold=hold):
                    self.ui = UI()
                    self.model, self.session = self.ui.model, Session(None)
                    self.fake.mode = "write-failed"
                    self.save_start()
                    self.ui.pump(lambda: self.ui.opened is not None)
                    self.model.loaded(self.model.token, True)
                    entered, release = threading.Event(), threading.Event()
                    self.addCleanup(release.set)
                    create = self.fake.create
                    def blocked(*args):
                        entered.set()
                        self.assertTrue(release.wait(2))
                        return create(*args)
                    self.fake.Item.create_sync = blocked
                    self.fake.mode = mode
                    replies = []
                    self.model.save(self.model.token, finish=lambda *r: replies.append(r))
                    self.ui.pump(entered.is_set)
                    token = self.model.token
                    if hold:
                        self.model.confirm_stop(lambda *_: None)
                    self.model.loaded(token, False)  # WebKit load/process failure
                    self.model.loaded(token, True)  # A later callback cannot heal it.
                    self.assertNotIn("stop", [n for n, _ in self.session.calls])
                    release.set()
                    self.ui.pump(lambda: bool(replies))
                    if hold:
                        self.ui.confirmed(False)
                    self.ui.pump(lambda: not self.model.workers and self.ui.queue.empty())
                    self.assertEqual((self.model.stage, self.model.runtime), ("failed", "unknown"))
                    self.assertEqual(self.model.saved_access, "saved" if mode == "ok" else "notSaved")
                    self.assertIn("runtimeUnavailable", self.ui.errors)
                    self.assertIsNone(self.model.session)
                    self.fake.Item.create_sync = create

    def test_save_completion_while_confirmation_open_holds_progress(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        create = self.fake.Item.create_sync
        def blocked(*args):
            entered.set()
            self.assertTrue(release.wait(2))
            return create(*args)
        self.fake.Item.create_sync = blocked
        self.save_start()
        self.ui.pump(entered.is_set)
        self.model.confirm_stop(lambda *_: None)
        release.set()
        self.ui.pump(lambda: not self.model.saving)
        self.assertEqual((self.model.stage, self.model.saved_access), ("saving", "saved"))
        self.assertIsNone(self.ui.opened)
        self.ui.confirmed(False)
        self.ui.pump(lambda: self.ui.opened is not None)
        self.assertEqual(self.model.stage, "opening")
        self.model.loaded(self.model.token, True)
        self.assertEqual(self.model.stage, "connected")

    def test_saved_resume_document_load_fences_and_truthful_remove(self):
        from test_client_store import record
        self.model.store.save(record())
        replies = []
        self.model.discover(lambda *r: replies.append(r))
        self.ui.pump(lambda: bool(replies))
        self.protocol.result("client.savedComputers", replies[-1][0])
        ref = replies[-1][0]["computers"][0]["computerRef"]
        self.model.connect_saved(ref, "document", lambda *r: replies.append(r))
        stale = self.ui.saved_consent
        self.model.detach("document")
        self.ui.pump(lambda: self.model.stage == "idle")
        stale(True)
        self.assertEqual(self.session.calls, [])
        self.model.connect_saved(ref, "new-document", lambda *r: replies.append(r))
        self.ui.saved_consent(True)
        self.ui.pump(lambda: self.ui.opened is not None)
        self.assertEqual([n for n, _ in self.session.calls], ["restore", "open"])
        self.assertEqual(self.model.stage, "opening")
        self.model.loaded(self.model.token, True)
        self.assertEqual(self.model.stage, "connected")
        self.model.stop()
        self.ui.pump(lambda: self.model.stage == "idle")
        self.assertEqual(len(self.fake.items), 1)
        self.fake.mode = "delete-lies"
        self.model.remove_saved(ref, "new-document", lambda *r: replies.append(r))
        self.ui.saved_consent(True)
        self.ui.pump(lambda: not self.model.storage_busy)
        self.assertEqual(self.model.saved_access, "removalIncomplete")
        self.assertEqual(replies[-1][1:], ("removalIncomplete", "unknown"))
        self.fake.mode = "ok"
        self.model.remove_saved(ref, "new-document", lambda *r: replies.append(r))
        self.ui.saved_consent(True)
        self.ui.pump(lambda: not self.model.storage_busy)
        self.assertEqual((self.model.saved_access, self.fake.items), ("none", []))
        self.assertEqual(replies[-1][0]["localCleanup"], "confirmed")
        self.assertEqual(replies[-1][0]["hostDenial"], "unknown")


class RequestTests(unittest.TestCase):
    def test_dedup_bounds_and_reachable_cancel(self):
        book = Requests()
        replies = []
        def request(key, command="client.acceptInvite"):
            return dict(requestId=key, command=command, args={})
        finish = lambda *r: replies.append(r)
        done = book.admit(request("r0"), 1, finish)
        self.assertIsNone(book.admit(request("r0"), 1, finish))
        self.assertIsNone(book.admit(request("r0", "client.retry"), 1, finish))
        self.assertEqual(replies[-1][1], "invalidRequest")
        done({"effect": "none"}, None)
        self.assertEqual(replies[-1], replies[-2])
        for index in range(1, 64):
            book.admit(request("r" + str(index)), 1, finish)({}, None)
        self.assertIsNone(book.admit(request("full"), 1, finish))
        self.assertEqual(replies[-1][1], "busy")
        cancel = book.admit(request("cancel", "client.cancelAttempt"), 1, finish)
        self.assertIsNotNone(cancel)
        cancel({"effect": "confirmed"}, None)
        book.admit(request("cancel-again", "client.cancelAttempt"), 1, finish)
        self.assertEqual(replies[-1][0]["effect"], "confirmed")
        reads = [book.admit(request("s" + str(i), "client.status"), 1, finish) for i in range(8)]
        self.assertTrue(all(reads))
        self.assertIsNone(book.admit(request("s8", "client.status"), 1, finish))
        for read in reads:
            read({}, None)
        self.assertEqual(book.reads, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

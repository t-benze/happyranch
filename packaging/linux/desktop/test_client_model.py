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
        self.opened = None
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

    def join(self, invite):
        self.call("join")

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

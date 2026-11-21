"""Native compatibility boundary over real local HTTP and a pinned Unix peer."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import socket
import socketserver
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import uuid

from gi.repository import GLib
from app_scheme_webkit import LocalDaemon, RemoteDaemon, Transfer
import test_remote_runtime as peers


class CompatibilityTests(peers.PeerFixture):
    app = peers.SchemeTests.app
    forward = peers.SchemeTests.forward

    def setUp(self) -> None:
        super().setUp()
        directory = tempfile.TemporaryDirectory(prefix="hr-gate-", dir="/tmp")
        self.addCleanup(directory.cleanup)
        peer = self.server.RequestHandlerClass

        class Helper(peer):
            def handle(self) -> None:
                self.connection.sendall(b"\0\0\0\0")
                super().handle()

        helper = socketserver.ThreadingUnixStreamServer(str(Path(directory.name) / "dial"), Helper)
        os.chmod(helper.server_address, 0o600)
        acceptor = threading.Thread(target=helper.serve_forever, kwargs={"poll_interval": 0.01})
        acceptor.start()

        def cleanup() -> None:
            helper.shutdown()
            helper.server_close()
            acceptor.join(1)
            # The stand-in process belongs to this test, never session.stop.
            self.session._process, self.session._directory = None, None

        self.addCleanup(cleanup)
        self.session._directory = Path(directory.name)
        self.session._node = "synthetic-node"
        self.session._process = SimpleNamespace(pid=os.getpid(), poll=lambda: None)
        self.session.dial = peers.client.ClientSession.dial.__get__(self.session)

    def targets(self) -> tuple:
        return LocalDaemon(self.server.server_port), RemoteDaemon(self.session, ready=True)

    def assert_error(self, result: dict, code: str, side: str | None = None, status: int = 503) -> None:
        detail = {"code": code}
        if side is not None:
            detail["side"] = side
        self.assertEqual(result["status"], status)
        self.assertEqual(json.loads(result["body"]), {"detail": detail})

    def test_health_matrix_caches_and_blocks_all_api_including_bootstrap(self) -> None:
        cases = [(b'{"api_contract":{"current":1,"min_client":1}}', 200, None),
                 (b'{"api_contract":{"current":3,"min_client":0}}', 200, None),
                 (b'{}', 200, "host"), (b'null', 200, "host"),
                 (b'{"api_contract":[]}', 200, "host"),
                 (b'{"api_contract":{"current":true,"min_client":1}}', 200, "host"),
                 (b'{"api_contract":{"current":1,"min_client":false}}', 200, "host"),
                 (b'{"api_contract":{"current":1.0,"min_client":1}}', 200, "host"),
                 (b'{"api_contract":{"current":1,"min_client":"1"}}', 200, "host"),
                 (b'{"api_contract":{"current":1}}', 200, "host"),
                 (b'{"api_contract":{"current":2,"min_client":2}}', 200, "app"),
                 (b'{"api_contract":{"current":0,"min_client":1}}', 200, "host"),
                 (b'not-json synthetic-health-private', 200, "host"),
                 (b'\xff', 200, "host"), (b'{}', 500, "host"),
                 (b'{}', 302, "host"), (b' ' * 65537, 200, "host")]
        logs = io.StringIO()
        with redirect_stdout(logs), redirect_stderr(logs):
            for body, status, side in cases:
                self.health_body, self.health_status = body, status
                for daemon in self.targets():
                    with self.subTest(body=body[:80], status=status, remote=isinstance(daemon, RemoteDaemon)):
                        self.observed.clear()
                        app = self.app(daemon)
                        for _ in range(2):
                            result = self.forward(app)
                            if side:
                                self.assert_error(result, "incompatible", side)
                                self.assert_error(self.forward(app, path="/api/v1/auth/bootstrap"), "incompatible", side)
                            else:
                                self.assertEqual((result["status"], result["body"]), (200, b"native-peer-ok"))
                        health = [entry for entry in self.observed if entry[0] == "/api/v1/health"]
                        self.assertEqual(len(health), 1, "health result must be cached on the target")
                        if side:
                            self.assertEqual([entry[0] for entry in self.observed], ["/api/v1/health"])
                        headers = {key.lower(): value for key, value in health[0][1].items()}
                        self.assertNotIn("authorization", headers, "health precedes local bearer bootstrap")
                        self.assertEqual(headers.get("x-happyranch-device-credential"),
                                         "synthetic-device-credential" if isinstance(daemon, RemoteDaemon) else None)
                        self.assertNotIn(b"synthetic-health-private", result["body"])
                        self.assertFalse(self.session._streams)
        for secret in ("synthetic-health-private", "synthetic-device-credential", "synthetic-local-bearer"):
            self.assertNotIn(secret, logs.getvalue())

    def test_reset_reselection_navigation_and_remote_reconnect_rerun_gate(self) -> None:
        for daemon in self.targets():
            with self.subTest(remote=isinstance(daemon, RemoteDaemon)):
                self.observed.clear()
                self.health_body = b'{}'
                app = self.app(daemon)
                self.assert_error(self.forward(app), "incompatible", "host")
                self.health_body = b'{"api_contract":{"current":1,"min_client":1}}'
                self.assert_error(self.forward(app), "incompatible", "host")
                app.select_target(daemon, owner_saves=())
                self.assertEqual(self.forward(app)["status"], 200)
                self.health_body = b'{"api_contract":{"current":2,"min_client":2}}'
                self.assertEqual(self.forward(app)["status"], 200)
                if isinstance(daemon, RemoteDaemon):
                    daemon.ready = False
                    self.assert_error(self.forward(app), "target_starting")
                    daemon.ready = True
                else:
                    app.W = SimpleNamespace(LoadEvent=SimpleNamespace(STARTED="started"))
                    app.load_changed(None, "started")
                self.assert_error(self.forward(app), "incompatible", "app")
                self.assertEqual(sum(path == "/api/v1/health" for path, *_ in self.observed), 3)
                fresh = RemoteDaemon(self.session, ready=True) if isinstance(daemon, RemoteDaemon) else LocalDaemon(self.server.server_port)
                self.assert_error(self.forward(self.app(fresh)), "incompatible", "app")
                self.assertEqual(sum(path == "/api/v1/health" for path, *_ in self.observed), 4)

    def test_unreachable_is_not_incompatible_and_retry_can_recover(self) -> None:
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            local = LocalDaemon(reserved.getsockname()[1])
            self.assert_error(self.forward(self.app(local)), "target_unreachable")
        directory = self.session._directory
        self.session._directory = directory / "absent"
        daemon = RemoteDaemon(self.session, ready=True)
        self.assert_error(self.forward(self.app(daemon)), "target_unreachable")
        self.session._directory = directory
        self.assertEqual(self.forward(self.app(daemon))["status"], 200)
        self.assertEqual([path for path, *_ in self.observed], ["/api/v1/health", "/api/v1/probe"])
        self.assertFalse(self.session._streams)

    def test_truncated_health_body_is_incompatible(self) -> None:
        self.health_mode = "short"
        for daemon in self.targets():
            with self.subTest(remote=isinstance(daemon, RemoteDaemon)):
                self.observed.clear()
                self.assert_error(self.forward(self.app(daemon)), "incompatible", "host")
                self.assertEqual([path for path, *_ in self.observed], ["/api/v1/health"])
                self.assertFalse(self.session._streams)

    def test_bootstrap_handle_is_gated_and_returns_only_dummy_authority(self) -> None:
        for valid in (True, False):
            self.health_body = b'{"api_contract":{"current":1,"min_client":1}}' if valid else b'{}'
            for daemon in (*self.targets(), None, RemoteDaemon(self.session)):
                with self.subTest(valid=valid, target=type(daemon).__name__):
                    self.observed.clear()
                    app = self.app(daemon)
                    app.view = object()
                    result = {}
                    app.reply = lambda _request, status, body, *_: result.update(status=status, body=body)

                    def finish(_request: object, status: int, source: object, *_args: object) -> None:
                        try:
                            body = source.read_bytes(4096, None).get_data()
                        finally:
                            source.close(None)
                        result.update(status=status, body=body)

                    app.finish = finish
                    key = str(uuid.uuid4())
                    headers = SimpleNamespace(foreach=lambda callback, _data: callback("X-HappyRanch-Request-ID", key))
                    request = SimpleNamespace(get_web_view=lambda: app.view,
                                              get_uri=lambda: "happyranch-app://app/api/v1/auth/bootstrap",
                                              get_http_method=lambda: "GET", get_http_headers=lambda: headers)
                    app.handle(request)
                    deadline = time.monotonic() + 1
                    context = GLib.MainContext.default()
                    while not result and time.monotonic() < deadline:
                        while context.pending():
                            context.iteration(False)
                        time.sleep(0.005)
                    self.assertTrue(result, "bootstrap did not settle")
                    if daemon is None:
                        self.assert_error(result, "no_target")
                        self.assertFalse(self.observed)
                    elif isinstance(daemon, RemoteDaemon) and not daemon.ready:
                        self.assert_error(result, "target_starting")
                        self.assertFalse(self.observed)
                    elif not valid:
                        self.assert_error(result, "incompatible", "host")
                        self.assertEqual([path for path, *_ in self.observed], ["/api/v1/health"])
                    else:
                        self.assertEqual((result["status"], json.loads(result["body"])),
                                         (200, {"token": "hr_native_session", "user": "founder", "role": "admin"}))
                        expected = ["/api/v1/health"]
                        if isinstance(daemon, LocalDaemon):
                            expected.append("/api/v1/auth/bootstrap")
                        self.assertEqual([path for path, *_ in self.observed], expected)
                    for secret in (b"synthetic-device-credential", b"synthetic-local-bearer"):
                        self.assertNotIn(secret, result["body"])
                    self.assertFalse(self.session._streams)

    def test_health_header_and_body_deadlines_close_reachable_peer(self) -> None:
        for mode in ("slow_headers", "slow_body"):
            self.health_mode = mode
            for daemon in self.targets():
                with self.subTest(mode=mode, remote=isinstance(daemon, RemoteDaemon)):
                    self.observed.clear()
                    began = time.monotonic()
                    self.assert_error(self.forward(self.app(daemon)), "incompatible", "host")
                    self.assertLess(time.monotonic() - began, 6)
                    self.assertEqual([entry[0] for entry in self.observed], ["/api/v1/health"])
                    self.assertFalse(self.session._streams)

    def test_cancel_and_selection_during_health_do_not_cache_or_bootstrap(self) -> None:
        for code, status in (("cancelled", 499), ("target_changed", 409)):
            for daemon in self.targets():
                with self.subTest(code=code, remote=isinstance(daemon, RemoteDaemon)):
                    self.health_mode = "slow_body"
                    self.health_entered.clear()
                    self.observed.clear()
                    app = self.app(daemon)
                    transfer = Transfer(daemon)
                    app.transfers["pending"] = transfer
                    result = {}
                    app.reply = lambda _request, status, body, *_: result.update(status=status, body=body)
                    worker = threading.Thread(target=app.forward, args=(object(), transfer, "GET", "/api/v1/probe", {}, None))
                    worker.start()
                    try:
                        self.assertTrue(self.health_entered.wait(1))
                        # Let response headers reach the reader; cancellation must
                        # interrupt the withheld body, not merely win before I/O.
                        time.sleep(0.05)
                        self.assertTrue(worker.is_alive())
                        if code == "target_changed":
                            app.select_target(daemon, owner_saves=())
                        else:
                            transfer.cancel()
                        worker.join(1)
                        self.assertFalse(worker.is_alive())
                        context = GLib.MainContext.default()
                        while context.pending():
                            context.iteration(False)
                        self.assert_error(result, code, status=status)
                        self.assertEqual([entry[0] for entry in self.observed], ["/api/v1/health"])
                        self.assertFalse(self.session._streams)
                    finally:
                        transfer.cancel()
                        worker.join(7)
                    self.health_mode = "normal"
                    self.assertEqual(self.forward(app)["status"], 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)

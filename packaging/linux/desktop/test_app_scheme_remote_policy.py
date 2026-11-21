"""S8 app routing boundary: real stand-in HTTP peers and GIO, no display."""
from __future__ import annotations

from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import re
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from gi.repository import Gio, GLib
import app_scheme_webkit as native
import test_remote_runtime as peers


POLICY = json.loads((Path(__file__).parents[3] /
    "protocol/remote-client-config-policy.v1.json").read_text())
CONFIG = [(entry["method"], re.sub(r"\{[^}]+\}", "synthetic", entry["path_template"]))
          for entry in POLICY["entries"]]
DENIED = {"detail": {"code": "remote_read_only"}}


class RemotePolicyTests(peers.PeerFixture):
    app = peers.SchemeTests.app

    def setUp(self) -> None:
        super().setUp()
        handler = self.server.RequestHandlerClass
        handler.do_PUT = handler.do_PATCH = handler.do_DELETE = handler.do_POST
        self.received = []
        for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            original = getattr(handler, "do_" + method)

            def record(peer: object, original: object = original) -> None:
                self.received.append((peer.command, peer.path))
                original(peer)

            setattr(handler, "do_" + method, record)
        self.local_server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.local_thread = threading.Thread(target=self.local_server.serve_forever,
                                             kwargs={"poll_interval": 0.01})
        self.local_thread.start()
        self.addCleanup(self.cleanup_local)

    def cleanup_local(self) -> None:
        self.local_server.shutdown()
        self.local_server.server_close()
        self.local_thread.join(2)

    def request(self, app: object, method: str, path: str, *,
                generation: str | None = None, handle: bool = False) -> dict:
        result = {}
        app.view = object()
        body = b"" if method in {"GET", "HEAD"} else b'{"synthetic":"draft"}'
        app.reply = lambda _request, status, data, *_: result.update(status=status, body=data)

        def finish(_request: object, status: int, source: object, *_args: object) -> None:
            data = bytearray()
            try:
                while chunk := source.read_bytes(4096, None).get_data():
                    data.extend(chunk)
            finally:
                source.close(None)
            result.update(status=status, body=bytes(data))

        app.finish = finish
        reads = []

        def get_body() -> object:
            reads.append("get_body")
            return Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(body))

        if handle:
            headers = {"x-happyranch-request-id": str(uuid.uuid4()),
                       "x-happyranch-body-bytes": "1"}
            if generation is not None:
                headers["x-happyranch-target"] = generation
            request = SimpleNamespace(get_web_view=lambda: app.view,
                get_uri=lambda: native.ORIGIN + path, get_http_method=lambda: method,
                get_http_headers=lambda: SimpleNamespace(
                    foreach=lambda callback, _: [callback(k, v) for k, v in headers.items()]),
                get_http_body=get_body)
            app.handle(request)
        else:
            transfer = native.Transfer(app.daemon)
            transfer.generation = generation
            transfer.cancellable = Gio.Cancellable()
            app.transfers["probe"] = transfer
            stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(body))

            def read(*args: object) -> object:
                reads.append("read_body")
                return stream.read_bytes(*args)

            app.forward(object(), transfer, method, path, {},
                        SimpleNamespace(read_bytes=read, close=stream.close))
            self.assertTrue(transfer.finished)
        context = GLib.MainContext.default()
        deadline = time.monotonic() + 3
        while not result and time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            if not result:
                time.sleep(0.001)
        self.assertIn("status", result, "native response must complete")
        result["reads"] = reads
        return result

    def test_all_config_denied_before_peer_io_and_body_read(self) -> None:
        self.assertEqual(len(CONFIG), 21)
        for ready in (True, False):
            self.remote.ready = ready
            app = self.app(self.remote)
            for method, path in CONFIG:
                with self.subTest(ready=ready, method=method, path=path):
                    self.observed.clear()
                    result = self.request(app, method, path + "?probe=1",
                                          generation=None if ready else "stale-generation")
                    self.assertEqual(result["status"], 403)
                    self.assertEqual(json.loads(result["body"]), DENIED)
                    self.assertEqual(result["reads"], [])
                    self.assertEqual(self.observed, [], "not even health may reach the peer")
                    self.assertEqual(self.dials, [])

    def test_handle_denial_is_generation_independent_and_decodes_templates(self) -> None:
        app = self.app(self.remote)
        for generation in (None, "stale-generation", app.generation):
            for method, path in CONFIG:
                # Parameters match one decoded segment; query/trailing slash do not bypass policy.
                path = path.replace("synthetic", "%E6%B5%8B%E8%AF%95") + "/?probe=1"
                with self.subTest(generation=generation, method=method, path=path):
                    self.observed.clear()
                    result = self.request(app, method, path, generation=generation, handle=True)
                    self.assertEqual(result["status"], 403)
                    self.assertEqual(json.loads(result["body"]), DENIED)
                    self.assertEqual(result["reads"], [])
                    self.assertEqual(self.observed, [])
                    self.assertEqual(self.dials, [])

    def test_local_config_and_unmatched_remote_operations_forward_unchanged(self) -> None:
        operations = [("POST", "/api/v1/executor-binaries/validate"),
            ("POST", "/api/v1/orgs/synthetic/tasks"),
            ("POST", "/api/v1/orgs/synthetic/threads"),
            ("POST", "/api/v1/orgs/synthetic/jobs"),
            ("POST", "/api/v1/orgs/synthetic/agents/synthetic/approve"),
            ("POST", "/api/v1/orgs/synthetic/kb"),
            ("POST", "/api/v1/orgs/synthetic/schedules"),
            ("POST", "/api/v1/orgs/synthetic/schedules/synthetic/run"),
            ("POST", "/api/v1/orgs/synthetic/artifacts"),
            ("PUT", "/api/v1/orgs/synthetic/settings/org/extra"),
            ("PUT", "/api/v1/orgs/synthetic/extra/settings/org"),
            ("GET", "/api/v1/orgs/synthetic/settings/org"),
            ("GET", "/api/v1/orgs/synthetic/schedules/synthetic")]
        for daemon, cases in ((native.LocalDaemon(self.local_server.server_port), CONFIG),
                              (self.remote, operations)):
            app = self.app(daemon)
            # A candidate remote does not turn a committed local target read-only.
            app.candidate = {"kind": "remote"}
            for method, path in cases:
                with self.subTest(remote=daemon is self.remote, method=method, path=path):
                    self.observed.clear()
                    self.received.clear()
                    result = self.request(app, method, path + "?probe=1")
                    self.assertEqual(result["status"], 200)
                    self.assertEqual(self.received.count((method, path + "?probe=1")), 1)
                    received = [entry for entry in self.observed if entry[0] == path + "?probe=1"]
                    self.assertEqual(len(received), 1)
                    if method != "GET":
                        self.assertEqual(received[0][2], b'{"synthetic":"draft"}')
                        self.assertEqual(result["body"], received[0][2])

    def test_installed_policy_is_authoritative_and_missing_or_invalid_refuses(self) -> None:
        # Relocate module lookup, as installed scripts run with system Python -I.
        with tempfile.TemporaryDirectory() as directory:
            desktop = Path(directory) / "prefix/share/happyranch/desktop"
            desktop.mkdir(parents=True)
            policy = desktop / "remote-client-config-policy.v1.json"
            for contents, status in ((json.dumps(POLICY), 403), (None, 500), ("{}", 500)):
                with self.subTest(contents="missing" if contents is None else "present", status=status):
                    if contents is None:
                        policy.unlink()
                    else:
                        policy.write_text(contents)
                    with patch.object(native, "__file__", str(desktop / "app_scheme_webkit.py")):
                        # Fresh installation-owned cache for each isolated app process.
                        if hasattr(native, "remote_config_policy"):
                            native.remote_config_policy.cache_clear()
                        app = self.app(self.remote)
                        result = self.request(app, "PUT", "/api/v1/orgs/synthetic/settings/org",
                                              generation=app.generation, handle=True)
                    self.assertEqual(result["status"], status)
                    self.assertEqual(self.observed, [])
                    self.assertEqual(self.dials, [])
        if hasattr(native, "remote_config_policy"):
            native.remote_config_policy.cache_clear()


if __name__ == "__main__":
    unittest.main()

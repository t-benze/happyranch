"""Listener-free native transport: real HTTP stand-in peer, synthetic authority."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
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
from typing import Callable
import unittest
import uuid
from unittest.mock import patch

from gi.repository import Gio, GLib
from app_scheme_webkit import AppScheme, LocalDaemon, RemoteDaemon, SchemeError, Transfer
import app_scheme_webkit

transport = app_scheme_webkit._transport
runtime = transport._runtime
spec = importlib.util.spec_from_file_location("client_session", Path(__file__).with_name("client_session.py"))
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


class PeerFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.observed = []
        self.next_event = threading.Event()
        self.peer_closed = threading.Event()
        self.health_body = b'{"api_contract":{"current":1,"min_client":1}}'
        self.health_status = 200
        self.health_mode = "normal"
        self.health_entered = threading.Event()
        fixture = self

        class Peer(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: object) -> None:
                pass

            def do_GET(self) -> None:
                fixture.observed.append((self.path, dict(self.headers), b""))
                if self.path == "/api/v1/health":
                    fixture.health_entered.set()
                    if fixture.health_mode != "slow_headers":
                        self.send_response(fixture.health_status)
                        self.send_header("Content-Length", str(len(fixture.health_body) + (10 if fixture.health_mode == "short" else 0)))
                        self.end_headers()
                    if fixture.health_mode.startswith("slow_"):
                        self.connection.settimeout(7)
                        try:
                            self.connection.recv(1)
                        except OSError:
                            pass
                        self.close_connection = True
                    else:
                        try:
                            self.wfile.write(fixture.health_body)
                        except OSError:
                            pass
                    self.close_connection = True
                elif self.path == "/api/v1/auth/bootstrap":
                    self.reply(b'{"token":"synthetic-local-bearer"}')
                elif self.path == "/api/v1/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/api/v1/must-not-follow")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    self.close_connection = True
                elif self.path == "/api/v1/disappear":
                    self.close_connection = True
                elif self.path == "/api/v1/header-stall":
                    self.connection.settimeout(12)
                    try:
                        if self.connection.recv(1) == b"":
                            fixture.peer_closed.set()
                    except OSError:
                        pass
                    self.close_connection = True
                elif self.path in ("/api/v1/stream", "/api/v1/cancel"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    self.wfile.write(b"d\r\ndata: first\n\n\r\n")
                    self.wfile.flush()
                    if self.path.endswith("cancel"):
                        self.connection.settimeout(3)
                        try:
                            if self.connection.recv(1) == b"":
                                fixture.peer_closed.set()
                        except OSError:
                            fixture.peer_closed.set()
                    elif fixture.next_event.wait(3):
                        self.wfile.write(b"c\r\ndata: next\n\n\r\n0\r\n\r\n")
                        self.wfile.flush()
                    self.close_connection = True
                elif self.path == "/api/v1/short":
                    self.send_response(200)
                    self.send_header("Content-Length", "4096")
                    self.end_headers()
                    self.wfile.write(b"short")
                    self.wfile.flush()
                    self.close_connection = True
                elif self.path == "/api/v1/bad-chunk":
                    self.send_response(200)
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    self.wfile.write(b"3\r\nabc\r\ninvalid\r\n")
                    self.wfile.flush()
                    self.close_connection = True
                else:
                    self.reply(b"native-peer-ok")

            def do_POST(self) -> None:
                data = self.rfile.read(int(self.headers["Content-Length"]))
                fixture.observed.append((self.path, dict(self.headers), data))
                if self.path == "/api/v1/header-stall":
                    self.connection.settimeout(3)
                    try:
                        self.connection.recv(1)
                    except OSError:
                        pass
                    self.close_connection = True
                else:
                    self.reply(data)

            def reply(self, data: bytes) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("X-HappyRanch-Device-Credential", "synthetic-response-authority")
                self.send_header("Set-Cookie", "synthetic-response-cookie")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)
                self.wfile.flush()
                self.close_connection = True

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Peer)
        self.server.daemon_threads = False
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.thread.start()
        self.addCleanup(self.cleanup_peer)
        self.session = client.ClientSession(Path("/synthetic-unused-prefix"))
        self.session._bearer = "synthetic-device-credential"
        self.dials, self.releases = [], []
        self.release = threading.Event()
        release = self.session.release

        def dial(deadline: float, acquire: Callable[[socket.socket], bool] | None = None) -> socket.socket:
            # Replace only the network venue, never the production adapter.
            stream = socket.create_connection(self.server.server_address, timeout=max(0.01, deadline - time.monotonic()))
            with self.session._lock:
                self.session._streams.add(stream)
            self.dials.append(stream)
            if acquire is not None and not acquire(stream):
                self.session.release(stream)
                raise runtime.ClientError("cancelled")
            return stream

        def released(stream: socket.socket) -> None:
            self.releases.append(stream)
            release(stream)
            self.release.set()

        self.session.dial, self.session.release = dial, released
        self.addCleanup(self.session.stop)
        self.remote = RemoteDaemon(self.session, ready=True)

    def cleanup_peer(self) -> None:
        self.next_event.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.assertFalse(self.thread.is_alive())

    def call(self) -> object:
        connection = self.remote.connection()
        self.addCleanup(connection.close)
        return connection

    def read_all(self, response: object) -> bytes:
        chunks = []
        with response:
            while chunk := response.read1(65536):
                chunks.append(chunk)
        return b"".join(chunks)


class AdapterTests(PeerFixture):
    def test_native_authority_and_reconstructed_framing(self) -> None:
        self.remote.bootstrap()
        self.observed.clear()
        self.dials.clear()
        self.releases.clear()
        headers = {"Authorization": "page-bearer", "Cookie": "page-cookie", "Host": "page-host",
                   "Proxy-Authorization": "page-proxy", "Forwarded": "page-forwarded", "Via": "page-via",
                   "X-Real-IP": "page-ip", "X-HappyRanch-Device-Credential": "page-device",
                   "X-HappyRanch-Target": "page-generation", "X-Tailscale-User": "page-user",
                   "Tailscale-User-Login": "page-login", "X-Tailnet-User": "page-tailnet",
                   "X-Forwarded-For": "page-forwarded", "Proxy-Identity": "page-identity",
                   "Connection": "keep-alive, x-nominated, content-length", "X-Nominated": "page-nominated",
                   "Content-Length": "999", "Transfer-Encoding": "chunked", "Upgrade": "websocket",
                   "Expect": "100-continue", "TE": "trailers", "Trailer": "X-Authority",
                   "Content-Type": "application/octet-stream", "X-Application": "retained"}
        logs = io.StringIO()
        with redirect_stdout(logs), redirect_stderr(logs):
            response = self.remote.send(self.call(), "POST", "/api/v1/echo?raw=%ff", headers, b"\0\xff\x80")
            self.assertEqual(self.read_all(response), b"\0\xff\x80")
        path, observed, body = self.observed[-1]
        observed = {k.lower(): v for k, v in observed.items()}
        self.assertEqual(path, "/api/v1/echo?raw=%ff")
        self.assertEqual(body, b"\0\xff\x80")
        self.assertEqual(observed, {"host": "home:443", "connection": "close", "content-length": "3",
                                   "content-type": "application/octet-stream", "x-application": "retained",
                                   "accept-encoding": "identity", "x-happyranch-device-credential": "synthetic-device-credential"})
        for secret in ("synthetic-device-credential", "synthetic-local-bearer", "page-bearer"):
            self.assertNotIn(secret, logs.getvalue())
        self.assertEqual(len(self.releases), 1)
        self.assertFalse(self.session._streams)

    def test_binary_multipart_and_large_stream_body(self) -> None:
        data = bytes(range(256)) * (10 * 1024 * 1024 // 256)
        multipart = (b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="binary.bin"\r\n'
                     b'Content-Type: application/octet-stream\r\n\r\n' + data + b'\r\n--boundary--\r\n')
        for body in (b"\x00\x80\xfe\xff", multipart):
            with self.subTest(bytes=len(body)):
                response = self.call().send("POST", "/api/v1/echo", {"Content-Type": "multipart/form-data; boundary=boundary"}, io.BytesIO(body))
                self.assertEqual(self.read_all(response), body)
                self.assertEqual(self.observed[-1][2], body)

    def test_body_limits_refuse_before_dial(self) -> None:
        oversized = b"x" * (64 * 1024 * 1024 + 1)
        for body in (oversized, io.BytesIO(oversized)):
            with self.subTest(stream=not isinstance(body, bytes)):
                with self.assertRaisesRegex(runtime.ClientError, "bodyTooLarge"):
                    self.call().send("POST", "/api/v1/echo", {}, body)
        self.assertFalse(self.dials)
        self.assertFalse(self.observed)

    def test_stream_delivers_before_peer_completes(self) -> None:
        with self.call().send("GET", "/api/v1/stream", {}, b"") as response:
            began = time.monotonic()
            self.assertEqual(response.read1(), b"data: first\n\n")
            self.assertLess(time.monotonic() - began, 1)
            self.assertFalse(self.next_event.is_set())
            self.next_event.set()
            self.assertEqual(response.read1(), b"data: next\n\n")
            self.assertEqual(response.read1(), b"")

    def test_cancel_idle_stream_releases_dial_once(self) -> None:
        call = self.call()
        response = call.send("GET", "/api/v1/cancel", {}, b"")
        self.assertEqual(response.read1(), b"data: first\n\n")
        errors = []
        def read() -> None:
            try:
                response.read1()
            except (OSError, runtime.ClientError) as error:
                errors.append(type(error).__name__)
        worker = threading.Thread(target=read)
        worker.start()
        transfer = Transfer()
        transfer.connection, transfer.socket = call, response.socket
        transfer.cancel()
        worker.join(2)
        response.close()
        self.assertFalse(worker.is_alive())
        self.assertTrue(errors)
        self.assertTrue(self.release.wait(1))
        self.assertTrue(self.peer_closed.wait(2))
        self.assertFalse(self.session._streams)
        self.assertEqual(len(self.releases), 1)

    def test_request_header_deadline_releases_dial(self) -> None:
        began = time.monotonic()
        with self.assertRaises((OSError, runtime.ClientError)):
            self.call().send("GET", "/api/v1/header-stall", {}, b"")
        elapsed = time.monotonic() - began
        self.assertGreaterEqual(elapsed, 9)
        self.assertLess(elapsed, 12)
        self.assertTrue(self.peer_closed.wait(1))
        self.assertFalse(self.session._streams)
        self.assertEqual(len(self.releases), 1)

    def test_short_and_invalid_chunked_bodies_raise(self) -> None:
        for path in ("short", "bad-chunk"):
            with self.subTest(path=path):
                response = self.call().send("GET", "/api/v1/" + path, {}, b"")
                with self.assertRaisesRegex(runtime.ClientError, "protocol"):
                    self.read_all(response)
        self.assertFalse(self.session._streams)


class SchemeTests(PeerFixture):
    def app(self, daemon: object) -> object:
        app = AppScheme.__new__(AppScheme)
        app.daemon, app.closed = daemon, False
        app.transfers, app.cancelled = {}, {}
        app.initialize_state()
        app.Gio, app.GLib = Gio, GLib
        return app

    def forward(self, app: object, transfer: object | None = None, path: str = "/api/v1/probe") -> dict:
        result = {}
        transfer = transfer or Transfer(app.daemon)
        app.transfers["test"] = transfer
        app.reply = lambda _request, status, data, *_args: result.update(status=status, body=data)
        def finish(_request: object, status: int, source: object, length: int, headers: dict) -> None:
            data = bytearray()
            try:
                while chunk := source.read_bytes(65536, None).get_data():
                    data.extend(chunk)
            finally:
                source.close(None)
            result.update(status=status, body=bytes(data), headers=headers)
        app.finish = finish
        # Small finite response: producer completes before dispatching GLib delivery.
        app.forward(object(), transfer, "GET", path, {}, None)
        context = GLib.MainContext.default()
        while context.pending():
            context.iteration(False)
        self.assertTrue(transfer.finished)
        return result

    def test_switch_after_upstream_send_before_headers_is_cancelled(self) -> None:
        for daemon in (self.remote, LocalDaemon(self.server.server_port)):
            with self.subTest(remote=daemon is self.remote):
                app = self.app(daemon)
                transfer = Transfer(daemon)
                app.transfers["test"] = transfer
                result = {}
                app.reply = lambda _request, status, data, *_args: result.update(status=status, body=data)
                worker = threading.Thread(target=app.forward,
                    args=(object(), transfer, "GET", "/api/v1/header-stall", {}, None))
                worker.start()
                until = time.monotonic() + 2
                while not any(path == "/api/v1/header-stall" for path, *_ in self.observed) and time.monotonic() < until:
                    time.sleep(0.005)
                self.assertTrue(any(path == "/api/v1/header-stall" for path, *_ in self.observed))
                app.select_target(None, owner_saves=())
                worker.join(2)
                self.assertFalse(worker.is_alive())
                context = GLib.MainContext.default()
                while context.pending():
                    context.iteration(False)
                self.assertEqual(result.get("status"), 499)
                self.assertEqual(json.loads(result["body"]), {"detail": {"code": "cancelled"}})
                self.observed.clear()

    def test_post_forward_failures_are_ordinary_and_mutations_do_not_retry(self) -> None:
        for daemon in (self.remote, LocalDaemon(self.server.server_port)):
            app = self.app(daemon)
            for path, code in (("redirect", "upstream_redirect"), ("disappear", "incomplete_body")):
                result = self.forward(app, path="/api/v1/" + path)
                self.assertEqual((result["status"], json.loads(result["body"])), (502, {"detail": {"code": code}}))
            transfer = Transfer(daemon)
            app.transfers["mutation"] = transfer
            result = {}
            app.reply = lambda _request, status, data, *_args: result.update(status=status, body=data)
            worker = threading.Thread(target=app.forward,
                args=(object(), transfer, "POST", "/api/v1/header-stall", {}, None))
            worker.start()
            until = time.monotonic() + 2
            while not any(path == "/api/v1/header-stall" for path, *_ in self.observed) and time.monotonic() < until:
                time.sleep(0.005)
            self.assertTrue(any(path == "/api/v1/header-stall" for path, *_ in self.observed))
            self.assertEqual(app.select_target(None, owner_saves=()), "non_get_pending")
            self.assertFalse(transfer.stop.is_set())
            app.invalidate_document()  # Unexpected navigation/process loss still cancels the mutation.
            worker.join(2)
            self.assertFalse(worker.is_alive())
            context = GLib.MainContext.default()
            while context.pending():
                context.iteration(False)
            self.assertEqual((result.get("status"), json.loads(result["body"])), (499, {"detail": {"code": "cancelled"}}))
            self.assertEqual(sum(path == "/api/v1/header-stall" for path, *_ in self.observed), 1)
            self.assertNotIn("/api/v1/must-not-follow", [path for path, *_ in self.observed])
            self.observed.clear()

    def test_shared_no_target_starting_unreachable_errors(self) -> None:
        unreachable = RemoteDaemon(self.session, ready=True)
        # An unavailable stand-in pinned endpoint, with valid native authority.
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            address = reserved.getsockname()
        attempts = []
        def dial(deadline: float, acquire: Callable[[socket.socket], bool] | None = None) -> socket.socket:
            attempts.append(deadline)
            return socket.create_connection(address, timeout=1)
        self.session.dial = dial
        for daemon, code in ((None, "no_target"), (RemoteDaemon(self.session), "target_starting"),
                             (unreachable, "target_unreachable")):
            with self.subTest(code=code):
                result = self.forward(self.app(daemon))
                self.assertEqual(result["status"], 503)
                self.assertEqual(json.loads(result["body"]), {"detail": {"code": code}})
        self.assertFalse(self.dials)
        self.assertEqual(len(attempts), 1)

    def test_remote_and_local_routing_keep_authority_off_page(self) -> None:
        logs = io.StringIO()
        with redirect_stdout(logs), redirect_stderr(logs):
            for daemon in (self.remote, LocalDaemon(self.server.server_port)):
                with self.subTest(remote=daemon is self.remote):
                    result = self.forward(self.app(daemon))
                    self.assertEqual(result["status"], 200)
                    self.assertEqual(result["body"], b"native-peer-ok")
                    self.assertEqual(result["headers"], {"content-type": "application/octet-stream", "content-length": "14"})
                    page = result["body"] + json.dumps(result["headers"]).encode()
                    for secret in (b"synthetic-device-credential", b"synthetic-local-bearer", b"synthetic-response-authority", b"synthetic-response-cookie"):
                        self.assertNotIn(secret, page)
        for secret in ("synthetic-device-credential", "synthetic-local-bearer"):
            self.assertNotIn(secret, logs.getvalue())
        self.assertEqual(self.observed[0][1].get("x-happyranch-device-credential"), "synthetic-device-credential")
        self.assertEqual(self.observed[-1][1].get("Authorization"), "Bearer synthetic-local-bearer")

    def test_native_selection_cancels_bound_request_with_target_changed(self) -> None:
        app = self.app(self.remote)
        transfer = Transfer(self.remote)
        transfer.generation = app.generation
        app.transfers["old"] = transfer
        app.select_target(None, owner_saves=())
        result = self.forward(app, transfer)
        self.assertEqual(result["status"], 409)
        self.assertEqual(json.loads(result["body"]), {"detail": {"code": "target_changed"}})
        self.assertFalse(self.dials)


class V2Tests(PeerFixture):
    app = SchemeTests.app
    forward = SchemeTests.forward

    def request(self, app: object, generation: str | None, method: str = "GET", body: bool = False) -> object:
        values = {"X-HappyRanch-Request-ID": str(uuid.uuid4())}
        if generation is not None:
            values["X-HappyRanch-Target"] = generation
        if body:
            values["X-HappyRanch-Body-Bytes"] = "1"
        headers = SimpleNamespace(foreach=lambda callback, _data: [callback(k, v) for k, v in values.items()])
        return SimpleNamespace(get_web_view=lambda: app.view, get_uri=lambda: transport.ORIGIN + "/api/v1/probe",
                               get_http_method=lambda: method, get_http_headers=lambda: headers)

    def test_generation_admission_and_native_pressure_shapes(self) -> None:
        app = self.app(self.remote)
        app.view = object()
        replies = []
        app.reply = lambda _request, status, data, *_args: replies.append((status, json.loads(data)))
        for generation in (None, "stale", "", "non-ascii-你"):
            app.handle(self.request(app, generation))
            self.assertEqual(replies.pop(), (409, {"detail": {"code": "target_changed"}}))
        self.assertFalse(app.transfers)
        self.assertFalse(self.observed)
        self.assertFalse(self.dials)
        for count, body in ((32, False), (2, True)):
            app.transfers = {str(i): Transfer(self.remote) for i in range(count)}
            for transfer in app.transfers.values():
                transfer.has_body = body
            app.handle(self.request(app, app.generation, "POST" if body else "GET", body))
            self.assertEqual(replies.pop(), (429, {"detail": {"code": "too_many_requests"}}))
        self.assertFalse(self.observed)
        self.assertNotEqual(app.status()["state"], "busy")

    def test_current_generation_forwards_and_bootstrap_is_exempt(self) -> None:
        for daemon in (self.remote, LocalDaemon(self.server.server_port)):
            app = self.app(daemon)
            app.view = object()
            replies = []
            def finish(_request: object, status: int, source: object, *_args: object) -> None:
                data = bytearray()
                try:
                    while chunk := source.read_bytes(65536, None).get_data():
                        data.extend(chunk)
                finally:
                    source.close(None)
                replies.append((status, bytes(data)))
            app.finish = finish
            for bootstrap in (False, True):
                request = self.request(app, None if bootstrap else app.generation)
                if bootstrap:
                    request.get_uri = lambda: transport.ORIGIN + "/api/v1/auth/bootstrap"
                app.handle(request)
                until = time.monotonic() + 2
                context = GLib.MainContext.default()
                while not replies and time.monotonic() < until:
                    while context.pending():
                        context.iteration(False)
                    time.sleep(0.005)
                self.assertEqual(replies.pop(), (200, b'{"token":"hr_native_session","user":"founder","role":"admin"}'
                                                if bootstrap else b"native-peer-ok"))
            self.assertTrue(all("x-happyranch-target" not in {k.lower() for k in headers}
                                for _, headers, *_ in self.observed))
            self.observed.clear()

    def test_commit_gate_and_swap_cancel_event_order(self) -> None:
        app = self.app(self.remote)
        original = app.generation
        transfer = Transfer(self.remote)
        transfer.generation = original
        app.transfers["old"] = transfer
        events = []
        app.emit_state = lambda: events.append((app.daemon, app.generation, transfer.stop.is_set()))
        for owners, expected in ((None, "owner_save_unknown"), (("unknown",), "owner_save_unknown"),
                                 (("pending",), "owner_save_pending")):
            self.assertEqual(app.select_target(None, owner_saves=owners), expected)
            self.assertIs(app.daemon, self.remote)
            self.assertEqual(app.generation, original)
            self.assertFalse(transfer.stop.is_set())
        for method in ("HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            transfer.method = method
            self.assertEqual(app.select_target(None, owner_saves=()), "non_get_pending")
            self.assertEqual(events, [])
        transfer.method = "GET"
        cancel = transfer.cancel
        def cancelled(code: str) -> None:
            self.assertIsNone(app.daemon, "target must swap before cancellation")
            self.assertNotEqual(app.generation, original)
            self.assertEqual(events, [])
            cancel(code)
        transfer.cancel = cancelled
        self.assertIsNone(app.select_target(None, owner_saves=()))
        self.assertEqual(events, [(None, app.generation, True)])
        self.assertEqual((transfer.interrupted().status, transfer.interrupted().code), (409, "target_changed"))
        generations = {app.generation, original}
        for _ in range(32):
            app.select_target(None, owner_saves=())
            self.assertTrue(app.generation.isascii())
            self.assertTrue(1 <= len(app.generation) <= 32)
            self.assertNotIn(app.generation, generations)
            generations.add(app.generation)
        fresh = self.app(None)
        self.assertNotIn(fresh.generation, generations)
        reload_generations = []
        for index in range(2):
            spec = importlib.util.spec_from_file_location("scheme_reload_" + str(index), Path(app_scheme_webkit.__file__))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            reload_generations.append(module.new_generation())
        self.assertNotEqual(*reload_generations, "loader recreation must not reset process generation")

    def test_status_and_event_share_closed_projection(self) -> None:
        local = LocalDaemon(self.server.server_port)
        app = self.app(local)
        local.bootstrap()
        expected = {"schema": 2, "generation": app.generation,
                    "target": {"kind": "local", "id": "local", "name": "This computer"},
                    "state": "ready", "incompatible": None,
                    "local": {"supported": True, "mode": "service", "running": "running",
                              "prompt_base_url": f"http://127.0.0.1:{local.port}/api/v1"},
                    "candidate": None, "access_unsaved": False,
                    "locale": {"current": "en", "persisted": False}}
        app.locale = {"current": "en", "persisted": False}
        self.assertEqual(app.status(), expected)
        app.subscribed, app.bridge_token = True, "native-probed-token"
        delivered = []
        app.view = SimpleNamespace(call_async_javascript_function=lambda *args: delivered.append(args[2].unpack()))
        app.emit_state()
        self.assertEqual(json.loads(delivered[0]["raw"]), expected)
        app.local["running"] = "stopped"
        self.assertEqual(app.status()["state"], "unreachable")
        app.select_target(self.remote, owner_saves=())
        self.assertEqual(app.status()["target"], {"kind": "remote", "id": "", "name": ""})
        self.assertIsNone(app.status()["local"]["prompt_base_url"])
        self.assertNotIn("synthetic-device-credential", json.dumps(app.status()))
        app.select_target(None, owner_saves=())
        self.assertEqual((app.status()["target"], app.status()["state"]), (None, "no_target"))

    def test_busy_health_is_transient_and_metadata_is_projected(self) -> None:
        for daemon in (self.remote, LocalDaemon(self.server.server_port)):
            app = self.app(daemon)
            self.health_body = b'{"api_contract":{"current":1,"min_client":1},"update_busy":{"busy":true}}'
            result = self.forward(app)
            self.assertEqual(result["status"], 503)
            self.assertEqual(json.loads(result["body"]), {"detail": {"code": "busy"}})
            self.assertEqual(app.status()["state"], "busy")
            self.health_body = b'{"api_contract":{"current":1,"min_client":1},"update_busy":{"busy":false}}'
            self.assertEqual(self.forward(app)["status"], 200)
            self.assertEqual(app.status()["state"], "ready")
            app.select_target(daemon, owner_saves=())
            self.health_body = b'{"api_contract":{"current":2,"min_client":2}}'
            self.assertEqual(self.forward(app)["status"], 503)
            self.assertEqual(app.status()["incompatible"], {"side": "app", "host_contract": {"current": 2,"min_client": 2},
                                                            "client_contract": 1, "min_daemon": 1})
            app.select_target(daemon, owner_saves=())
            self.health_body = b'{}'
            self.assertEqual(self.forward(app)["status"], 503)
            self.assertEqual(app.status()["incompatible"], {"side": "host", "host_contract": None,
                                                            "client_contract": 1, "min_daemon": 1})

    def test_native_status_progresses_without_page_api_requests(self) -> None:
        def refresh() -> None:
            events.clear()
            app.refresh_status()
            until = time.monotonic() + 2
            context = GLib.MainContext.default()
            while not events and time.monotonic() < until:
                while context.pending():
                    context.iteration(False)
                time.sleep(0.005)
            self.assertTrue(events, "native admission must publish without waiting for page API traffic")

        for daemon in (self.remote, LocalDaemon(self.server.server_port)):
            with self.subTest(remote=daemon is self.remote):
                app = self.app(daemon)
                events = []
                app.emit_state = lambda: events.append(app.status())
                self.assertEqual(app.status()["state"], "target_starting")
                refresh()
                self.assertEqual(events[-1]["state"], "ready")
                self.assertEqual([path for path, *_ in self.observed], ["/api/v1/health"] +
                                 ([] if daemon is self.remote else ["/api/v1/auth/bootstrap"]))
                self.assertEqual(self.forward(app)["status"], 200)
                self.observed.clear()

                self.cleanup_peer()
                try:
                    result = self.forward(app)
                    self.assertEqual((result["status"], json.loads(result["body"])),
                                     (503, {"detail": {"code": "target_unreachable"}}))
                    self.assertEqual(app.status()["state"], "unreachable")
                    refresh()
                    self.assertEqual(app.status()["state"], "unreachable")
                    self.assertEqual([event["state"] for event in events], ["unreachable"])
                    self.assertFalse(self.observed, "stopped peer must receive no HTTP")
                finally:
                    # Restore the actual peer at the same address, including
                    # when the regression assertion fails on pre-fix code.
                    self.server = ThreadingHTTPServer(self.server.server_address, self.server.RequestHandlerClass)
                    self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
                    self.thread.start()

                refresh()
                self.assertEqual(events[-1]["state"], "ready")
                self.assertEqual(app.status()["state"], "ready")
                self.assertEqual([path for path, *_ in self.observed], ["/api/v1/health"])
                self.observed.clear()

    def test_lock_timeout_is_ordinary_429(self) -> None:
        app = self.app(self.remote)
        self.remote._lock.acquire()
        try:
            result = self.forward(app)
        finally:
            self.remote._lock.release()
        self.assertEqual((result["status"], json.loads(result["body"])), (429, {"detail": {"code": "too_many_requests"}}))
        self.assertFalse(self.observed)
        self.assertNotEqual(app.status()["state"], "busy")

    def test_cancelled_delivery_never_relabels_forwarded_request(self) -> None:
        app = self.app(self.remote)
        result = {}
        app.reply = lambda _request, status, data, *_args: result.update(status=status, body=data)
        for forwarded, status, code in ((False, 409, "target_changed"), (True, 499, "cancelled")):
            transfer = Transfer(self.remote)
            transfer.generation = app.generation
            transfer.forwarded = forwarded
            transfer.cancel("target_changed")
            source = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(b"old"))
            app.deliver(object(), transfer, 200, source, {})
            self.assertTrue(source.is_closed())
            self.assertEqual((result["status"], json.loads(result["body"])), (status, {"detail": {"code": code}}))

    def test_native_bridge_refuses_unbound_or_replacement_document(self) -> None:
        app = self.app(None)
        app.controller = object()
        app.bridge_token = "native-probed-token"
        uri = [transport.ORIGIN + "/settings/connection"]
        app.view = SimpleNamespace(get_uri=lambda: uri[0])
        app.JSC = SimpleNamespace(Value=SimpleNamespace(new_string=lambda _context, raw: raw))
        outcomes = []
        reply = SimpleNamespace(return_value=lambda raw: outcomes.append((True, json.loads(raw))),
                                return_error_message=lambda raw: outcomes.append((False, json.loads(raw))))
        def message(token: str = "native-probed-token") -> object:
            return SimpleNamespace(is_string=lambda: True, get_context=lambda: None,
                to_string=lambda: json.dumps({"token": token, "command": "app.status", "data": "{}"}))
        app.bridge_message(app.controller, message(), reply)
        self.assertEqual(outcomes.pop(), (True, app.status()))
        for manager, token, location in ((object(), "native-probed-token", uri[0]),
                                        (app.controller, "stale", uri[0]),
                                        (app.controller, "native-probed-token", "https://untrusted.invalid/")):
            uri[0] = location
            app.bridge_message(manager, message(token), reply)
            self.assertFalse(outcomes.pop()[0])
        uri[0] = transport.ORIGIN + "/"
        app.invalidate_document()
        app.bridge_message(app.controller, message(), reply)
        self.assertFalse(outcomes.pop()[0])


class ClientDispatchTests(unittest.TestCase):
    def test_client_hook_requires_admitted_main_and_exact_empty_args(self) -> None:
        app = AppScheme.__new__(AppScheme)
        app.daemon, app.closed = None, False
        app.initialize_state()
        app.controller = object()
        app.bridge_token = "admitted-token"
        uri = [transport.ORIGIN + "/settings/connection"]
        app.view = SimpleNamespace(get_uri=lambda: uri[0])
        app.JSC = SimpleNamespace(Value=SimpleNamespace(new_string=lambda _context, raw: raw))
        calls, outcomes = [], []
        def dispatch(command: str, document: object, finish: object) -> None:
            calls.append((command, document))
            finish({"effect": "none"}, None)
        app.client_dispatch = dispatch
        reply = SimpleNamespace(return_value=lambda raw: outcomes.append((True, json.loads(raw))),
                                return_error_message=lambda raw: outcomes.append((False, json.loads(raw))))
        def send(*, token: str = "admitted-token", manager: object = None, data: str = "{}") -> None:
            message = SimpleNamespace(is_string=lambda: True, get_context=lambda: None,
                to_string=lambda: json.dumps({"token": token, "command": "client.commitSwitch", "data": data}))
            app.bridge_message(app.controller if manager is None else manager, message, reply)
        send()
        self.assertEqual(outcomes.pop(), (True, {"effect": "none"}))
        self.assertEqual(calls, [("client.commitSwitch", app.document)])
        calls.clear()
        for kwargs, location in (({"token": "stale"}, uri[0]), ({"manager": object()}, uri[0]),
                                 ({"data": '{"credential":"synthetic"}'}, uri[0]),
                                 ({}, "https://untrusted.invalid/"), ({}, transport.ORIGIN + "/api/v1/health")):
            uri[0] = location
            send(**kwargs)
            self.assertFalse(outcomes.pop()[0])
            self.assertFalse(calls)
        uri[0] = transport.ORIGIN + "/"
        app.bridge_token = None
        send()
        self.assertFalse(outcomes.pop()[0])
        self.assertFalse(calls)


class SwitchTests(PeerFixture):
    app = SchemeTests.app
    forward = SchemeTests.forward

    def setUp(self) -> None:
        super().setUp()
        from test_client_model import UI, owner
        self.ui, self.owner = UI(), owner
        self.model = self.ui.model
        self.main = self.app(self.remote)
        self.events = []
        self.main.emit_state = lambda: self.events.append(self.main.status())
        self.model.attach_main(self.main, app_scheme_webkit)
        self.model.owner_snapshot = lambda: ()
        self.approval_entered, self.approval_release = threading.Event(), threading.Event()
        self.approval_release.set()
        self.addCleanup(self.approval_release.set)
        self.new_candidate()
        for obj, name, value in ((owner._native, "ClientSession", lambda _prefix: self.candidate),
                                 (owner._native, "decode_invite", lambda _text: SimpleNamespace(
                                     home_id="synthetic-sensitive-home", expires_at=time.time() + 120)),
                                 (self.model.store, "list", lambda: ({}, "none", "known"))):
            patcher = patch.object(obj, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def new_candidate(self) -> None:
        session = self.candidate = client.ClientSession(Path("/synthetic-unused-prefix"))
        session._bearer = "synthetic-candidate-credential"
        def dial(deadline: float, acquire: Callable | None = None) -> socket.socket:
            stream = socket.create_connection(self.server.server_address, timeout=max(0.01, deadline - time.monotonic()))
            with session._lock:
                session._streams.add(stream)
            if acquire is not None and not acquire(stream):
                session.release(stream)
                raise runtime.ClientError("cancelled")
            return stream
        session.dial = dial
        session.join = lambda *_args, **_kwargs: None
        session.pair = lambda: ("pair-abcd-abcd-abcd-abcd", time.time() + 50)
        def complete() -> bool:
            self.approval_entered.set()
            if not self.approval_release.wait(2):
                raise self.owner._native.ClientError("unavailable")
            return True
        session.complete = complete
        self.addCleanup(session.stop)

    def command(self, name: str) -> tuple:
        replies = []
        self.model.dispatch("client." + name, self.main.document, lambda *result: replies.append(result))
        return replies[-1] if replies else ()

    def start(self) -> None:
        result = self.command("acceptInvite")
        self.assertEqual(result[0]["effect"], "none")
        self.ui.pasted("synthetic-invite-canary")
        self.ui.pump(lambda: hasattr(self.ui, "consent"))
        self.ui.consent(True)

    def ready(self) -> None:
        self.start()
        self.ui.pump(lambda: self.main.candidate["phase"] in ("ready_to_switch", "failed"))
        self.assertEqual(self.main.candidate["phase"], "ready_to_switch")

    def serves_old(self, generation: str) -> None:
        self.assertIs(self.main.daemon, self.remote)
        self.assertEqual(self.main.generation, generation)
        result = self.forward(self.main)
        self.assertEqual((result["status"], result["body"]), (200, b"native-peer-ok"))

    def test_success_keeps_active_serving_then_commits_and_errors_old_stream(self) -> None:
        generation = self.main.generation
        self.ready()
        self.serves_old(generation)
        raw = json.dumps(self.main.status())
        for secret in ("synthetic-sensitive-home", "synthetic-invite-canary", "synthetic-candidate-credential", "127.0.0.1"):
            self.assertNotIn(secret, raw)
        self.assertIsNone(self.candidate._proxy, "Main candidate admission must not open a loopback proxy")
        call = self.remote.connection()
        response = call.send("GET", "/api/v1/cancel", {}, b"")
        self.addCleanup(response.close)
        self.assertEqual(response.read1(), b"data: first\n\n")
        transfer = Transfer(self.remote)
        transfer.connection, transfer.socket = call, response.socket
        transfer.generation, transfer.forwarded = generation, True
        self.main.transfers["stream"] = transfer
        self.assertEqual(self.command("commitSwitch")[0]["effect"], "confirmed")
        self.assertIs(self.main.daemon.session, self.candidate)
        self.assertNotEqual(self.main.generation, generation)
        self.assertTrue(transfer.stop.is_set())
        with self.assertRaises((OSError, runtime.ClientError)):
            response.read1()
        self.assertIsNone(self.events[-1]["candidate"])
        self.assertTrue(self.events[-1]["access_unsaved"])
        self.assertEqual(self.forward(self.main)["status"], 200)

    def test_failed_candidate_keeps_active_target_generation_and_stream(self) -> None:
        generation = self.main.generation
        self.remote.bootstrap()
        response = self.call().send("GET", "/api/v1/stream", {}, b"")
        self.addCleanup(response.close)
        self.assertEqual(response.read1(), b"data: first\n\n")
        self.candidate._bearer = ""  # Candidate native authority is unavailable.
        self.start()
        self.ui.pump(lambda: self.model.stage == "failed")
        self.assertEqual(self.main.candidate["phase"], "failed")
        self.serves_old(generation)
        self.next_event.set()
        self.assertEqual(response.read1(), b"data: next\n\n")
        self.assertEqual(response.read1(), b"")
        self.assertEqual(self.command("commitSwitch")[1], {"code": "target_unreachable"})

    def test_cancel_mid_approval_fences_late_completion_without_touching_active(self) -> None:
        generation = self.main.generation
        self.approval_release.clear()
        self.start()
        self.ui.pump(self.approval_entered.is_set)
        self.serves_old(generation)
        self.command("cancelAttempt")
        self.ui.confirmed(True)
        self.approval_release.set()
        self.ui.pump(lambda: self.model.stage == "idle" and not self.model.workers)
        while not self.ui.queue.empty():
            self.ui.queue.get_nowait()()
        self.assertEqual(self.main.candidate["phase"], "cancelled")
        self.assertIsNone(self.model.candidate_target)
        self.assertTrue(self.candidate._closed)
        self.serves_old(generation)

    def test_commit_refuses_unknown_pending_saves(self) -> None:
        generation = self.main.generation
        self.ready()
        candidate = dict(self.main.candidate)
        for owners, expected in ((None, "owner_save_unknown"), (("unknown",), "owner_save_unknown"),
                                 (("pending",), "owner_save_pending"), ([], "owner_save_unknown")):
            self.model.owner_snapshot = lambda: owners
            self.assertEqual(self.command("commitSwitch")[1:], (expected,))
            self.assertEqual(self.main.candidate, candidate)
            self.serves_old(generation)

    def test_commit_refuses_current_non_get(self) -> None:
        generation = self.main.generation
        self.ready()
        candidate = dict(self.main.candidate)
        transfer = Transfer(self.remote)
        transfer.generation, transfer.method = generation, "POST"
        self.main.transfers["mutation"] = transfer
        self.assertEqual(self.command("commitSwitch")[1:], ("non_get_pending",))
        self.assertFalse(transfer.stop.is_set())
        self.assertEqual(self.main.candidate, candidate)
        transfer.finished = True
        self.assertEqual(self.command("commitSwitch")[0]["effect"], "confirmed")

    def test_incompatible_candidates_refuse_with_side_without_replacing_active(self) -> None:
        generation = self.main.generation
        for metadata, side in (({}, "host"), ({"current": True, "min_client": 1}, "host"),
                               ({"current": 0, "min_client": 0}, "host"),
                               ({"current": 2, "min_client": 2}, "app")):
            with self.subTest(metadata=metadata):
                # A new attempt has a new single-use native session.
                self.new_candidate()
                self.health_body = json.dumps({"api_contract": metadata} if metadata else {}).encode()
                if hasattr(self.ui, "consent"):
                    del self.ui.consent
                self.start()
                self.ui.pump(lambda: self.model.stage == "failed")
                self.assertEqual(self.command("commitSwitch")[1], {"code": "incompatible", "side": side})
                self.assertEqual(self.main.candidate["phase"], "failed")
                self.assertIs(self.main.daemon, self.remote)
                self.assertEqual(self.main.generation, generation)

    def test_owned_remote_survives_second_attempt_and_main_recreation(self) -> None:
        self.ready()
        self.assertEqual(self.command("commitSwitch")[0]["effect"], "confirmed")
        self.model.reading = True  # Native health already owns this model token.
        self.assertEqual(self.command("acceptInvite")[1:], ("busy",))
        self.assertIsNone(self.main.candidate)
        self.model.reading = False
        active = self.candidate
        target_id = self.main.daemon.target_id
        old = self.main
        old.closed = True
        self.main = self.app(None)
        self.model.attach_main(self.main, app_scheme_webkit)
        self.assertIsNone(old.client_dispatch)
        self.assertIsNotNone(self.main.daemon)
        self.assertIs(self.main.daemon.session, active)
        self.assertEqual(self.main.daemon.target_id, target_id)
        generation = self.main.generation
        self.new_candidate()
        self.candidate._bearer = ""
        del self.ui.consent
        self.start()
        self.ui.pump(lambda: self.model.stage == "failed")
        self.assertFalse(active._closed)
        self.assertIs(self.main.daemon.session, active)
        self.assertEqual(self.main.generation, generation)
        self.assertEqual(self.forward(self.main)["body"], b"native-peer-ok")

    def test_native_entry_retry_after_cancel_gets_fresh_candidate_admission(self) -> None:
        generation = self.main.generation
        self.ready()
        previous_id = self.main.candidate["id"]
        self.command("cancelAttempt")
        self.ui.confirmed(True)
        self.ui.pump(lambda: self.model.stage == "idle" and not self.model.workers)
        self.new_candidate()
        del self.ui.consent
        # Existing native/v1 controls call the shared model directly.
        self.model.accept(object())
        self.ui.pasted("synthetic-retry-invite")
        self.ui.pump(lambda: hasattr(self.ui, "consent"))
        self.ui.consent(True)
        self.ui.pump(lambda: self.main.candidate["phase"] in ("ready_to_switch", "failed"))
        self.assertEqual(self.main.candidate["phase"], "ready_to_switch")
        self.assertNotEqual(self.main.candidate["id"], previous_id)
        self.serves_old(generation)


class DialCancellationTests(unittest.TestCase):
    def test_select_target_cancels_pending_dials(self) -> None:
        self.check_pending_dials("target_changed", 409)

    def test_abort_cancels_pending_dials(self) -> None:
        self.check_pending_dials("cancelled", 499)

    def check_pending_dials(self, code: str, status: int) -> None:
        # The helper withholds its ack on all 32 slots. After cancellation it
        # accepts a genuine HTTP request on the same pinned Unix endpoint.
        entered = threading.Condition()
        stalled, closed, observed, releases = [], [], [], []
        stall = threading.Event()
        stall.set()

        class Helper(BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                pass

            def handle(self) -> None:
                if stall.is_set():
                    with entered:
                        stalled.append(self.connection)
                        entered.notify_all()
                    self.connection.settimeout(3)
                    data = self.connection.recv(1)
                    with entered:
                        closed.append(data)
                        entered.notify_all()
                    return
                self.connection.sendall(b"\0\0\0\0")
                super().handle()

            def do_GET(self) -> None:
                observed.append((self.path, dict(self.headers)))
                self.send_response(200)
                body = b'{"api_contract":{"current":1,"min_client":1}}' if self.path == "/api/v1/health" else b"ok"
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        class HelperServer(socketserver.ThreadingUnixStreamServer):
            request_queue_size = 64

        with tempfile.TemporaryDirectory(prefix="hr-dial-", dir="/tmp") as directory:
            helper = HelperServer(str(Path(directory) / "dial"), Helper)
            os.chmod(helper.server_address, 0o600)
            acceptor = threading.Thread(target=helper.serve_forever, kwargs={"poll_interval": 0.01})
            acceptor.start()
            session = client.ClientSession(Path("/synthetic-unused-prefix"))
            session._directory = Path(directory)
            session._node = "synthetic-node"
            session._bearer = "synthetic-device-credential"
            session._process = SimpleNamespace(pid=os.getpid(), poll=lambda: None)
            release = session.release

            def released(stream: socket.socket) -> None:
                releases.append(stream)
                release(stream)

            session.release = released
            remote = RemoteDaemon(session, ready=True)
            stall.clear()
            remote.bootstrap()
            observed.clear()
            releases.clear()
            stall.set()
            app = SchemeTests.app(self, remote)
            app.view = object()
            replies = {}
            app.reply = lambda request, response_status, data, *_args: replies.update(
                {request: (response_status, data)})

            def finish(request: object, response_status: int, source: object, *_args: object) -> None:
                try:
                    replies[request] = (response_status, source.read_bytes(10, None).get_data())
                finally:
                    source.close(None)

            app.finish = finish
            workers, transfers = [], []
            keys = [str(uuid.uuid4()) for _ in range(32)]
            context = GLib.MainContext.default()

            def dispatch() -> None:
                while context.pending():
                    context.iteration(False)

            def request(key: str) -> object:
                headers = SimpleNamespace(foreach=lambda callback, _data: (
                    callback("X-HappyRanch-Request-ID", key), callback("X-HappyRanch-Target", app.generation)))
                return SimpleNamespace(get_web_view=lambda: app.view, get_uri=lambda: "happyranch-app://app/api/v1/probe",
                                       get_http_method=lambda: "GET", get_http_headers=lambda: headers)

            logs = io.StringIO()
            try:
                with redirect_stdout(logs), redirect_stderr(logs):
                    for key in keys:
                        transfer = Transfer(remote)
                        app.transfers[key] = transfer
                        transfers.append(transfer)
                        worker = threading.Thread(target=app.forward, args=(key, transfer, "GET", "/api/v1/probe", {}, None))
                        workers.append(worker)
                        worker.start()
                    with entered:
                        self.assertTrue(entered.wait_for(lambda: len(stalled) == 32, timeout=2))
                    self.assertEqual(len(session._streams), 32)
                    # Prove the slot guard is exercised before cancellation.
                    busy_key = str(uuid.uuid4())
                    busy = request(busy_key)
                    app.reply = lambda req, response_status, data, *_args: replies.update(
                        {busy_key if req is busy else req: (response_status, data)})
                    app.handle(busy)
                    self.assertEqual(replies[busy_key][0], 429)
                    self.assertEqual(json.loads(replies[busy_key][1]), {"detail": {"code": "too_many_requests"}})
                    began = time.monotonic()
                    if code == "target_changed":
                        app.select_target(None, owner_saves=())
                    else:
                        for key in keys:
                            app.cancel_id(key)
                    for worker in workers:
                        worker.join(max(0, 1 - (time.monotonic() - began)))
                    self.assertFalse(any(worker.is_alive() for worker in workers), "cancelled dial worker remained running")
                    self.assertTrue(all(transfer.finished for transfer in transfers))
                    with entered:
                        self.assertTrue(entered.wait_for(lambda: len(closed) == 32, timeout=0.5))
                    self.assertEqual(closed, [b""] * 32, "cancelled handshake emitted HTTP bytes")
                    self.assertEqual(len(releases), 32)
                    self.assertEqual(len(set(releases)), 32, "a dial was released twice")
                    self.assertTrue(all(stream.fileno() == -1 for stream in releases))
                    self.assertFalse(session._streams)
                    dispatch()
                    for key in keys:
                        self.assertEqual(replies[key][0], status)
                        self.assertEqual(json.loads(replies[key][1]), {"detail": {"code": code}})
                    app.monitor()
                    self.assertFalse(app.transfers)
                    # Reuse the same session through actual handle/admission and HTTP.
                    stall.clear()
                    app.select_target(remote, owner_saves=())
                    fresh_key = str(uuid.uuid4())
                    fresh = request(fresh_key)
                    app.reply = lambda req, response_status, data, *_args: replies.update({fresh_key: (response_status, data)})
                    app.finish = lambda req, response_status, source, *args: finish(fresh_key, response_status, source, *args)
                    app.handle(fresh)
                    until = time.monotonic() + 1
                    while time.monotonic() < until and fresh_key not in replies:
                        dispatch()
                        time.sleep(0.005)
                    self.assertEqual(replies.get(fresh_key), (200, b"ok"))
                    self.assertEqual([path for path, _ in observed], ["/api/v1/health", "/api/v1/probe"])
                    self.assertTrue(all(headers["x-happyranch-device-credential"] == "synthetic-device-credential"
                                        for _, headers in observed))
                    self.assertEqual(len(releases), 34)
                    self.assertEqual(len(set(releases)), 34)
                    self.assertFalse(session._streams)
                    for secret in ("synthetic-device-credential", "synthetic-local-bearer"):
                        self.assertNotIn(secret, repr(replies))
            finally:
                app.cancel_all()
                # Test cleanup also releases pre-fix sockets after the intended
                # assertion failure, without completing the stalled handshake.
                with session._lock:
                    pending = list(session._streams)
                for stream in pending:
                    runtime.close_socket(stream)
                for worker in workers:
                    worker.join(4)
                helper.shutdown()
                helper.server_close()
                acceptor.join(1)
                dispatch()
            self.assertNotIn("synthetic-device-credential", logs.getvalue())
            self.assertNotIn("synthetic-local-bearer", logs.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)

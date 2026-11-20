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
        app.transfers["old"] = transfer
        app.select_target(None)
        result = self.forward(app, transfer)
        self.assertEqual(result["status"], 409)
        self.assertEqual(json.loads(result["body"]), {"detail": {"code": "target_changed"}})
        self.assertFalse(self.dials)


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
                headers = SimpleNamespace(foreach=lambda callback, _data: callback("X-HappyRanch-Request-ID", key))
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
                    self.assertEqual(replies[busy_key][0], 503)
                    self.assertEqual(json.loads(replies[busy_key][1]), {"detail": {"code": "busy"}})
                    began = time.monotonic()
                    if code == "target_changed":
                        app.select_target(None)
                    else:
                        for key in keys:
                            message = SimpleNamespace(get_js_value=lambda key=key: SimpleNamespace(to_string=lambda: key))
                            app.cancel_request(None, message)
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
                    app.select_target(remote)
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

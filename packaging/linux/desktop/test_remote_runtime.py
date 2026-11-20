"""Listener-free native transport: real HTTP stand-in peer, synthetic authority."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
from pathlib import Path
import socket
import threading
import time
import unittest

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
        fixture = self

        class Peer(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: object) -> None:
                pass

            def do_GET(self) -> None:
                fixture.observed.append((self.path, dict(self.headers), b""))
                if self.path == "/api/v1/auth/bootstrap":
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

        def dial(deadline: float) -> socket.socket:
            # Replace only the network venue, never the production adapter.
            stream = socket.create_connection(self.server.server_address, timeout=max(0.01, deadline - time.monotonic()))
            with self.session._lock:
                self.session._streams.add(stream)
            self.dials.append(stream)
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

    def forward(self, app: object, transfer: object | None = None) -> dict:
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
        app.forward(object(), transfer, "GET", "/api/v1/health", {}, None)
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
        def dial(deadline: float) -> socket.socket:
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


if __name__ == "__main__":
    unittest.main(verbosity=2)

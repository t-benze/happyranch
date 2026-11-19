"""Linux client compatibility with the shipping connector; synthetic authority."""
from __future__ import annotations

from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
from pathlib import Path
import socket
import sys
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from runtime.remote_access.forwarding import HttpLoopbackForwarder, LoopbackTarget
from runtime.remote_access.httpd import BaseConnectorHandler, serve_decision
from runtime.remote_access.models import Decision, RemoteRequest

SPEC = importlib.util.spec_from_file_location("client_session", Path(__file__).with_name("client_session.py"))
client = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(client)


class ShippingConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        observed = self.observed = []

        class Upstream(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(200)  # Shipping daemon responses also add Server/Date.
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args: object) -> None:
                pass

        upstream = self.server(Upstream)
        forwarder = HttpLoopbackForwarder(LoopbackTarget("127.0.0.1", upstream.server_port))

        class Connector(BaseConnectorHandler):
            def serve_request(self, request: RemoteRequest, now: datetime) -> None:
                observed.append(request)  # BaseConnectorHandler used the real parse_request.
                response = forwarder.forward_once("GET", "/api/v1/health", None, (), None,
                                                  "synthetic-daemon-authority")
                serve_decision(self, Decision(True, "", "", response=response))

        connector = self.server(Connector)

        def dial(deadline: float) -> socket.socket:
            return socket.create_connection(connector.server_address, timeout=max(0.01, deadline - time.monotonic()))

        self.session = client.ClientSession(Path("/synthetic-unused-prefix"))
        self.session.dial = dial  # Test-side transport adapter, no shipped hook.
        self.session._bearer = "hrpin_1_" + "b" * 64
        self.addCleanup(self.session.stop)

    def server(self, handler: type[BaseHTTPRequestHandler]) -> ThreadingHTTPServer:
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server.daemon_threads = False  # server_close also joins owned request workers.
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()

        def stop() -> None:
            server.shutdown()
            server.server_close()
            thread.join(3)
            self.assertFalse(thread.is_alive())

        self.addCleanup(stop)
        return server

    def proxy(self) -> object:
        proxy = client._runtime.RuntimeProxy(self.session.dial, self.session.release, self.session._credential)
        self.addCleanup(proxy.stop)
        return proxy

    def test_health_with_shipping_connector_response(self) -> None:
        with self.session.dial(time.monotonic() + 3) as stream:
            wire = client.Wire(stream, time.monotonic() + 3)
            wire.send(b"GET /api/v1/health HTTP/1.1\r\nHost: home:443\r\nConnection: close\r\n\r\n")
            _, headers = wire.head(repeated=True)
            for name in (b"server", b"date"):
                self.assertEqual(wire.raw_head.lower().count(b"\r\n" + name + b":"), 2)
            wire.body(headers, lambda _: None, 8192)
        error = None
        try:
            self.session.health()
        except client.ClientError as exc:
            error = str(exc)
        self.assertIsNone(error, "native health must accept the shipping connector response")
        self.assertTrue(self.session.open_runtime().startswith("http://127.0.0.1:"))

    def test_chunked_upload_with_shipping_connector_parser(self) -> None:
        proxy = self.proxy()
        host = proxy.url[7:-1]
        port = int(host.split(":")[1])
        cases = ((b"0\r\n\r\n", None, False),
                 (b"2\r\nbo\r\n4\r\ndy\x00\xff\r\n0\r\n\r\n", b"body\x00\xff", False),
                 (b"4\r\nbody\r\n0\r\n\r\n", b"body", True))
        for chunks, expected, expect_continue in cases:
            with self.subTest(body=expected, expect_continue=expect_continue):
                with socket.create_connection(("127.0.0.1", port), timeout=3) as stream:
                    wire = client.Wire(stream, time.monotonic() + 3)
                    header = (f"POST /upload HTTP/1.1\r\nHost: {host}\r\nTransfer-Encoding: chunked\r\n"
                              "Connection: close, transfer-encoding, content-length\r\n")
                    if expect_continue:
                        header += "Expect: 100-continue\r\n"
                    wire.send((header + "\r\n").encode())
                    if expect_continue:
                        self.assertEqual(wire.head()[0], "HTTP/1.1 100 Continue")
                    wire.send(chunks[:5])
                    wire.send(chunks[5:])
                    error = None
                    try:
                        line, headers = wire.head(repeated=True)
                        wire.body(headers, lambda _: None, 8192)
                    except client.ClientError as exc:
                        error = str(exc)
                    self.assertIsNone(error, "chunked upload must receive the connector response")
                    self.assertIn("200", line)
                request = self.observed[-1]
                self.assertEqual(request.body, expected)
                framing = [(h.name, h.value) for h in request.headers
                           if h.name in {"content-length", "transfer-encoding"}]
                self.assertEqual(framing, [("content-length", str(len(expected or b"")))])

    def test_rejected_chunked_upload_never_reaches_connector(self) -> None:
        proxy = self.proxy()
        host = proxy.url[7:-1]
        port = int(host.split(":")[1])
        cases = ((b"", b"0\r\nX-Trailer: refused\r\n\r\n"),
                 (b"", b"4000001\r\n"),  # 64 MiB + 1; refuse before reading that body.
                 (b"", b"invalid\r\n"),
                 (b"Content-Length: 4\r\n", b"4\r\nbody\r\n0\r\n\r\n"))
        for extra, chunks in cases:
            with self.subTest(extra=extra, chunks=chunks):
                before = len(self.observed)
                with socket.create_connection(("127.0.0.1", port), timeout=3) as stream:
                    stream.sendall(f"POST /refused HTTP/1.1\r\nHost: {host}\r\nTransfer-Encoding: chunked\r\n".encode()
                                   + extra + b"\r\n" + chunks)
                    try:
                        response = stream.recv(65536)
                    except ConnectionResetError:
                        response = b""
                self.assertEqual(response, b"")
                self.assertEqual(len(self.observed), before)

    def test_declared_upload_limit_with_shipping_connector(self) -> None:
        dials = []
        dial = self.session.dial

        def counted_dial(deadline: float) -> socket.socket:
            dials.append(deadline)
            return dial(deadline)

        self.session.dial = counted_dial
        proxy = self.proxy()
        host = proxy.url[7:-1]
        port = int(host.split(":")[1])
        limit = 64 * 1024 * 1024
        for length in (str(limit + 1), "999999999999", "-1", "+1", "1.0", "invalid", "9999999999999"):
            with self.subTest(length=length):
                before = len(dials), len(self.observed)
                with socket.create_connection(("127.0.0.1", port), timeout=3) as stream:
                    stream.sendall(f"POST /refused HTTP/1.1\r\nHost: {host}\r\nContent-Length: {length}\r\n\r\n".encode())
                    stream.shutdown(socket.SHUT_WR)
                    response = bytearray()
                    while data := stream.recv(1024):
                        response.extend(data)
                        self.assertLessEqual(len(response), 1024)
                self.assertEqual((len(dials), len(self.observed)), before, "refused length must never dial or submit")
                expected = (b"HTTP/1.1 413 Payload Too Large\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                            if length in (str(limit + 1), "999999999999") else b"")
                self.assertEqual(response, expected)
        with socket.create_connection(("127.0.0.1", port), timeout=10) as stream:
            wire = client.Wire(stream, time.monotonic() + 10)
            wire.send(f"POST /upload HTTP/1.1\r\nHost: {host}\r\nContent-Length: {limit}\r\n\r\n".encode())
            block = bytes(range(256)) * 256
            for _ in range(limit // len(block)):
                wire.send(block)
            self.assertIn("200", wire.head(repeated=True)[0])
        self.assertEqual(len(dials), 1)
        self.assertEqual(len(self.observed), 1)
        request = self.observed[0]
        self.assertEqual(request.path, "/upload")
        self.assertEqual([(h.name, h.value) for h in request.headers if h.name == "content-length"],
                         [("content-length", str(limit))])
        self.assertEqual(request.body, block * (limit // len(block)))


if __name__ == "__main__":
    unittest.main(verbosity=2)

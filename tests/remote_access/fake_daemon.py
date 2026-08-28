"""In-process fake daemon bound strictly to literal loopback 127.0.0.1.

Used by the connector-core harness as the positive loopback-forward control:
the connector forwards to 127.0.0.1 only, injects the daemon bearer on the
final hop, and the fake daemon asserts it. With ``hold_open`` the daemon holds
the response body open (headers already flushed) so revocation-mid-stream
tests can abort an in-flight HTTP/SSE exchange.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from runtime.remote_access.forwarding import LOOPBACK_HOST


class FakeDaemon:
    """A deterministic loopback-only fake daemon for the harness.

    ``expected_bearer``: the Authorization value the connector must inject.
    ``hold_open``: when True the response body is held open (headers flushed,
    ``started`` set) until ``release`` is set — enabling revocation-mid-stream
    tests for both HTTP and SSE.
    """

    def __init__(self, expected_bearer: str, hold_open: bool = False, port: int = 0) -> None:
        self.expected_bearer = expected_bearer
        self.requests: list[dict] = []
        self.hold_open = hold_open
        self.started = threading.Event()
        self.release = threading.Event()
        self._server = ThreadingHTTPServer(
            (LOOPBACK_HOST, port), self._handler_factory()
        )
        self._port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return self._port

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self.release.set()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def _handler_factory(self) -> type[BaseHTTPRequestHandler]:
        daemon = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
                return  # harness: never emit raw request lines to any log

            def _record_and_check_auth(self) -> bool:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                auth = self.headers.get("Authorization")
                daemon.requests.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body": body,
                    }
                )
                if auth != f"Bearer {daemon.expected_bearer}":
                    self.send_response(500)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return False
                return True

            def _serve(self, content_type: str, payload: bytes) -> None:
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.flush()
                daemon.started.set()
                if daemon.hold_open:
                    daemon.release.wait(timeout=10)
                    if self.wfile.closed:
                        return
                self.wfile.write(payload)
                self.wfile.flush()

            def do_GET(self) -> None:
                if not self._record_and_check_auth():
                    return
                if self.path.endswith("/tail"):
                    # SSE stream: emit two events (body held open when hold_open).
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.flush()
                    daemon.started.set()
                    if daemon.hold_open:
                        daemon.release.wait(timeout=10)
                        if self.wfile.closed:
                            return
                    self.wfile.write(b"data: hello\n\ndata: world\n\n")
                    self.wfile.flush()
                    return
                payload = json.dumps({"ok": True, "path": self.path}).encode()
                self._serve("application/json", payload)

            do_POST = do_GET

        return Handler


class FakeDaemonError(AssertionError):
    pass


def assert_daemon_received(fake: FakeDaemon, method: str, path: str) -> None:
    matching = [r for r in fake.requests if r["method"] == method and r["path"] == path]
    if not matching:
        raise FakeDaemonError(f"fake daemon never received {method} {path}")

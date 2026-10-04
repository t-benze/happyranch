"""In-process fake daemon bound strictly to literal loopback 127.0.0.1.

Used by the connector-core harness as the positive loopback-forward control:
the connector forwards to 127.0.0.1 only, injects the daemon bearer on the
final hop, and the fake daemon asserts it. With ``hold_open`` the daemon holds
the response body open (headers already flushed) so revocation-mid-stream
tests can abort an in-flight HTTP/SSE exchange. Legacy holds return after
10 seconds. Explicit held_sse_mode heartbeat/silent requests instead have
finite, request-specific ownership; only heartbeat excludes idle closure.
"""
from __future__ import annotations

import json
import threading
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from runtime.remote_access.forwarding import LOOPBACK_HOST


class FakeDaemon:
    """A deterministic loopback-only fake daemon for the harness.

    ``expected_bearer``: the Authorization value the connector must inject.
    ``hold_open``: when True the response body is held open until ``release``
    is set or the legacy 10s wait expires. SSE emits one frame before ``started`` is set; HTTP only flushes
    headers. This enables deterministic revocation-mid-stream tests.
    """

    def __init__(self, expected_bearer: str, hold_open: bool = False, port: int = 0, *, held_sse_mode: str | None = None) -> None:
        if held_sse_mode not in (None, "heartbeat", "silent") or (hold_open and held_sse_mode):
            raise ValueError("invalid held SSE selection")
        self.held_sse_mode = held_sse_mode
        self._held_lock = threading.Lock()
        self._held: dict[int, dict] = {}
        self.expected_bearer = expected_bearer
        self.requests: list[dict] = []
        self.hold_open = hold_open
        self.started = threading.Event()
        self.release = threading.Event()
        self._server = ThreadingHTTPServer(
            (LOOPBACK_HOST, port), self._handler_factory()
        )
        if held_sse_mode:
            # Only this opt-in instance avoids an unbounded server_close join.
            self._server.daemon_threads = True
            self._server.block_on_close = False
        self._port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return self._port

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        if self.held_sse_mode:
            self._stop_held()
            return
        self.release.set()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def _held_snapshot(self) -> dict[int, dict]:
        with self._held_lock:
            return {ordinal: {k: v for k, v in row.items() if k not in ("socket", "thread")}
                    | {"release": self.release.is_set()}
                    for ordinal, row in self._held.items()}

    def _serve_held_sse(self, handler: BaseHTTPRequestHandler) -> None:
        started = time.monotonic()
        with self._held_lock:
            ordinal = len(self._held) + 1
            row = dict(alive=True, first_frame_flushed=False, heartbeat_count=0,
                       last_flush=started, terminal=None, failure=False,
                       socket=handler.connection, thread=threading.current_thread())
            self._held[ordinal] = row
        def update(**values):
            with self._held_lock:
                row.update(values)
        try:
            handler.connection.settimeout(2)
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.send_header("Connection", "close")
            handler.end_headers()
            handler.wfile.write(b"data: hello\n\n")
            handler.wfile.flush()
            update(first_frame_flushed=True, last_flush=time.monotonic())
            self.started.set()
            while not self.release.wait(timeout=1):
                now = time.monotonic()
                if now - started >= 120 or row["heartbeat_count"] >= 120:
                    update(failure=True, terminal="budget_exhausted")
                    return
                if self.held_sse_mode == "heartbeat":
                    if now - row["last_flush"] > 3:
                        update(failure=True, terminal="write_gap")
                        return
                    handler.wfile.write(b":\n\n")
                    handler.wfile.flush()
                    if time.monotonic() - row["last_flush"] > 3:
                        update(failure=True, terminal="write_gap")
                        return
                    update(heartbeat_count=row["heartbeat_count"] + 1, last_flush=time.monotonic())
            update(terminal="released")
        except (BrokenPipeError, ConnectionResetError):
            update(terminal="peer_disconnect")
        except OSError:
            update(failure=True, terminal="write_failure")
        finally:
            update(alive=False)

    def _stop_held(self) -> None:
        deadline = time.monotonic() + 5
        self.release.set()
        with self._held_lock:
            rows = list(self._held.values())
        for row in rows:
            try:
                row["socket"].shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self._thread.is_alive():
            self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=max(0, deadline - time.monotonic()))
        for row in rows:
            row["thread"].join(timeout=max(0, deadline - time.monotonic()))
        if self._thread.is_alive() or any(row["thread"].is_alive() for row in rows):
            raise AssertionError("held fixture cleanup incomplete")

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
                    if daemon.held_sse_mode:
                        daemon._serve_held_sse(self)
                        return
                    # A held SSE emits one frame before holding the body open.
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.flush()
                    if daemon.hold_open:
                        self.wfile.write(b"data: hello\n\n")
                        self.wfile.flush()
                        daemon.started.set()
                        daemon.release.wait(timeout=10)
                        if self.wfile.closed:
                            return
                        self.wfile.write(b"data: world\n\n")
                    else:
                        daemon.started.set()
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

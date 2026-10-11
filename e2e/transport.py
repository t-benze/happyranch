"""Real loopback HTTP relay; loss happens only AFTER independent commit proof."""
from __future__ import annotations

import hashlib
import http.client
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable


class BrowserRelay:
    """E05-only whole-origin forwarding; holds one original compose response."""

    def __init__(self, upstream: int, deadline: float) -> None:
        self.upstream, self.deadline = upstream, deadline
        self.records: list[dict] = []
        self.errors: list[str] = []
        self.lock = threading.Lock()
        self.release = threading.Event()
        self.held = threading.Event()
        self.stopping = False
        self.workers: set[threading.Thread] = set()
        self.connections: set = set()
        self.arm_end: float | None = None
        self.proof = None
        self.matches = 0
        self.hold: dict = {}
        relay = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: object) -> None:
                pass

            def send_error(self, code, message=None, explain=None) -> None:
                relay.errors.append("unsupported browser request: " + str(code))
                self.close_connection = True

            def handle_expect_100(self) -> bool:
                relay.errors.append("unsupported browser expect framing")
                self.close_connection = True
                return False

            def forward(self) -> None:
                connection = None
                upstream_socket = None
                response = None
                with relay.lock:
                    selected = relay.arm_end is not None and self.command == "POST" and self.path == "/api/v1/orgs/alpha/threads"
                    if selected:
                        relay.matches += 1
                        ordinal = relay.matches
                        if ordinal != 1:
                            relay.errors.append("duplicate compose request")
                            relay.release.set()
                    else:
                        ordinal = None
                end = relay.arm_end if selected else relay.deadline
                record = dict(method=self.command, path=self.path.split("?")[0], ordinal=ordinal, entered=time.monotonic())
                relay.records.append(record)
                try:
                    def remaining() -> float:
                        value = (end if selected else relay.deadline) - time.monotonic()
                        if value <= 0 or relay.stopping:
                            raise TimeoutError("browser transport deadline")
                        return value

                    self.connection.settimeout(remaining())
                    if self.headers.get("Transfer-Encoding") or self.headers.get("Upgrade"):
                        raise ValueError("unsupported browser request framing")
                    lengths = self.headers.get_all("Content-Length", [])
                    if len(lengths) > 1:
                        raise ValueError("ambiguous browser request framing")
                    raw = self.rfile.read(int(lengths[0]) if lengths else 0)
                    remaining()
                    if selected:
                        record.update(target=self.path, request_sha256=hashlib.sha256(raw).hexdigest(), request_bytes=len(raw))
                    connection = http.client.HTTPConnection("127.0.0.1", relay.upstream, timeout=remaining())
                    with relay.lock:
                        relay.connections.add(connection)
                    connection.putrequest(self.command, self.path, skip_host=True, skip_accept_encoding=True)
                    hops = relay.hop_headers(self.headers.items())
                    for key, value in self.headers.items():
                        if key.lower() not in hops:
                            connection.putheader(key, value)
                    connection.putheader("Connection", "close")
                    connection.connect()
                    connection.sock.settimeout(remaining())
                    connection.endheaders(raw)
                    remaining()
                    upstream_socket = connection.sock
                    with relay.lock:
                        relay.connections.add(upstream_socket)
                    record["forwarded"] = time.monotonic()
                    upstream_socket.settimeout(remaining())
                    response = connection.getresponse()
                    remaining()
                    headers = response.getheaders()
                    if response.status == 101:
                        raise ValueError("unsupported browser upgrade")
                    encoding = response.getheader("Transfer-Encoding")
                    if encoding and (encoding.lower().strip() != "chunked" or response.getheader("Content-Length") is not None):
                        raise ValueError("unsupported upstream transfer framing")
                    record["status"] = response.status
                    streaming = (response.getheader("Content-Type") or "").split(";")[0] == "text/event-stream"
                    record["streaming"] = streaming
                    content = None
                    if selected:
                        upstream_socket.settimeout(remaining())
                        content = response.read()
                        remaining()
                        record.update(upstream_complete=time.monotonic(), response_sha256=hashlib.sha256(content).hexdigest(),
                                      response_bytes=len(content), content_type=response.getheader("Content-Type"))
                        # Parsing is evidence-only AFTER the unconditional
                        # upstream request. Invalid bodies are never swallowed.
                        try:
                            record["request"] = json.loads(raw)
                        except ValueError:
                            record["invalid_request_json"] = True
                        if ordinal == 1 and response.status == 200:
                            try:
                                parsed = json.loads(content)
                                record["response"] = parsed
                                record["commit"] = relay.proof(parsed, end)
                                record["committed"] = time.monotonic()
                                if relay.matches != 1:
                                    raise ValueError("duplicate compose before hold")
                                record["held"] = time.monotonic()
                                relay.hold = record
                                relay.held.set()
                                if not relay.release.wait(remaining()):
                                    raise TimeoutError("original compose hold expired")
                                record["release_observed"] = time.monotonic()
                            except Exception as exc:
                                relay.errors.append(type(exc).__name__ + ": compose proof/hold failed")
                                relay.release.set()
                        else:
                            relay.errors.append("compose status/ordinal refused")
                            relay.release.set()
                    self.connection.settimeout(remaining())
                    self.send_response_only(response.status, response.reason)
                    hops = relay.hop_headers(headers)
                    for key, value in headers:
                        if key.lower() not in hops:
                            self.send_header(key, value)
                    self.send_header("Connection", "close")
                    self.end_headers()
                    if self.command != "HEAD" and response.status not in {204, 304}:
                        if content is not None:
                            self.connection.settimeout(remaining())
                            self.wfile.write(content)
                            self.connection.settimeout(remaining())
                            self.wfile.flush()
                            remaining()
                        else:
                            while True:
                                self.connection.settimeout(remaining())
                                upstream_socket.settimeout(remaining())
                                chunk = response.read1(65536)
                                remaining()
                                if not chunk:
                                    break
                                self.connection.settimeout(remaining())
                                self.wfile.write(chunk)
                                self.connection.settimeout(remaining())
                                self.wfile.flush()
                                remaining()
                    record["delivered"] = time.monotonic()
                except (BrokenPipeError, ConnectionResetError) as exc:
                    record["client_disconnected"] = type(exc).__name__
                    if not record.get("streaming") and not relay.stopping:
                        relay.errors.append("finite response disconnected")
                    relay.release.set()
                except Exception as exc:
                    # Never export headers/auth/bootstrap/stream bodies or raw exceptions.
                    record["error"] = type(exc).__name__
                    if not relay.stopping:
                        relay.errors.append(type(exc).__name__ + ": browser transport failed")
                    relay.release.set()
                finally:
                    self.close_connection = True
                    if response:
                        response.close()
                    if connection:
                        connection.close()
                    with relay.lock:
                        relay.connections.discard(connection)
                        relay.connections.discard(upstream_socket)
                    record["closed"] = time.monotonic()

            do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = forward

        class Server(ThreadingHTTPServer):
            def get_request(self):
                connection, address = super().get_request()
                remaining = relay.deadline-time.monotonic()
                if remaining <= 0:
                    connection.close()
                    raise TimeoutError("browser accepted after deadline")
                connection.settimeout(min(10, remaining))
                with relay.lock:
                    relay.connections.add(connection)
                return connection, address

            def process_request_thread(self, request, address):
                worker = threading.current_thread()
                with relay.lock:
                    relay.workers.add(worker)
                try:
                    super().process_request_thread(request, address)
                finally:
                    with relay.lock:
                        relay.connections.discard(request)
                        relay.workers.discard(worker)

            def handle_error(self, request, address):
                relay.errors.append("browser request worker failed")

        self.server = Server(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.server.block_on_close = False
        self.port = self.server.server_port
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.05), daemon=True)
        self.thread.start()

    @staticmethod
    def hop_headers(headers) -> set[str]:
        result = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade"}
        for key, value in headers:
            if key.lower() == "connection":
                result.update(part.strip().lower() for part in value.split(","))
        return result

    def arm(self, proof: Callable, end: float) -> None:
        if self.arm_end is not None or not time.monotonic() < end <= min(time.monotonic() + 5, self.deadline):
            raise ValueError("one bounded compose arm required")
        self.proof, self.arm_end = proof, end
        self.hold = dict(armed=time.monotonic(), deadline=end)

    def close(self, end: float | None = None) -> dict:
        entered = time.monotonic()
        end = min(self.deadline, end if end is not None else self.deadline, entered+3)
        def left():
            value = end-time.monotonic()
            if value <= 0:
                raise TimeoutError("browser relay close deadline")
            return value
        left()
        self.release.set()
        self.stopping = True
        self.server.shutdown()
        with self.lock:
            connections, workers = list(self.connections), list(self.workers)
        for connection in connections:
            sock = connection.sock if isinstance(connection, http.client.HTTPConnection) else connection
            if sock:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                sock.close()
        self.server.server_close()
        for worker in [*workers, self.thread]:
            worker.join(timeout=left())
        left()
        result = dict(port=self.port, entered=entered, finished=time.monotonic(),
                      workers_alive=sum(w.is_alive() for w in workers), listener_alive=self.thread.is_alive(),
                      connections=len(self.connections), errors=list(self.errors))
        if result["workers_alive"] or result["listener_alive"] or result["connections"]:
            self.errors.append("browser proxy ownership did not close")
        return result


class Relay:
    def __init__(self, upstream: int, proof: Callable[[dict], None], deadline: Callable[[], float]) -> None:
        self.upstream = upstream
        self.proof = proof
        self.deadline = deadline
        self.records: list[dict] = []
        self.sockets: set[socket.socket] = set()
        self.workers: set[threading.Thread] = set()
        self.lock = threading.Lock()
        self.capture_e05 = False
        self.armed: tuple[str, str] | None = None
        self.error: str | None = None
        relay = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                pass

            def do_POST(self) -> None:
                self.forward()

            def do_GET(self) -> None:
                self.forward()

            def forward(self) -> None:
                self.connection.settimeout(relay.remaining())
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                relay.remaining()
                digest = hashlib.sha256(raw).hexdigest()
                connection = http.client.HTTPConnection("127.0.0.1", relay.upstream, timeout=relay.remaining())
                try:
                    connection.connect()
                    upstream_socket = connection.sock
                    upstream_socket.settimeout(relay.remaining())
                    connection.request(self.command, self.path, body=raw, headers=dict(self.headers))
                    upstream_socket.settimeout(relay.remaining())
                    response = connection.getresponse()
                    upstream_socket.settimeout(relay.remaining())
                    body = response.read()
                    relay.remaining()
                    parsed = json.loads(body)
                    record = dict(path=self.path, method=self.command, body_sha256=digest,
                                  status=response.status, response=parsed, dropped=False)
                    if relay.capture_e05:
                        # E05 only; no headers or credentials are exported.
                        # Controller retains this in memory until full-context
                        # sanitation, including partial/failing observations.
                        record.update(request=json.loads(raw) if raw else None,
                                      response_text=body.decode(), entered=time.monotonic())
                    if (relay.armed is not None and self.command == "POST"
                            and relay.armed == (self.path, json.loads(raw).get("session_id"))):
                        relay.armed = None
                        if response.status != 200 or parsed != {"ok": True}:
                            raise AssertionError("loss relay requires actual upstream 200/ok")
                        relay.proof(json.loads(raw))
                        record["committed_before_drop"] = True
                        record["dropped"] = True
                        relay.records.append(record)
                        self.close_connection = True
                        self.connection.shutdown(socket.SHUT_RDWR)
                        self.connection.close()
                        return
                    relay.records.append(record)
                    self.send_response(response.status)
                    for key, value in response.getheaders():
                        if key.lower() not in {"connection", "transfer-encoding", "content-length"}:
                            self.send_header(key, value)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.connection.settimeout(relay.remaining())
                    self.wfile.write(body)
                    relay.remaining()
                except Exception as exc:
                    relay.error = repr(exc)
                    self.close_connection = True
                finally:
                    connection.close()

        class Server(ThreadingHTTPServer):
            def get_request(self):
                connection, address = super().get_request()
                with relay.lock:
                    relay.sockets.add(connection)
                connection.settimeout(relay.remaining())
                return connection, address

            def process_request_thread(self, request, address):
                worker = threading.current_thread()
                with relay.lock:
                    relay.workers.add(worker)
                try:
                    super().process_request_thread(request, address)
                finally:
                    with relay.lock:
                        relay.sockets.discard(request)
                        relay.workers.discard(worker)

            def handle_error(self, request, address):
                relay.error = "CLI relay worker failed"

        self.server = Server(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.server.block_on_close = False
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def remaining(self, cap: float = 25) -> float:
        value = self.deadline()-time.monotonic()
        if value <= 0:
            raise TimeoutError("CLI relay deadline")
        return min(cap, value)

    def close(self, end: float) -> dict:
        def left():
            value = min(end, self.deadline())-time.monotonic()
            if value <= 0:
                raise TimeoutError("CLI relay close deadline")
            return min(3, value)
        left()
        self.server.shutdown()
        with self.lock:
            connections, workers = list(self.sockets), list(self.workers)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        self.server.server_close()
        for worker in workers:
            worker.join(timeout=left())
        self.thread.join(timeout=left())
        left()
        if self.thread.is_alive() or any(w.is_alive() for w in workers) or self.sockets:
            raise TimeoutError("CLI relay listener/socket/worker did not close")
        return dict(port=self.port, listener_alive=self.thread.is_alive(), workers_alive=sum(w.is_alive() for w in workers),
                    sockets=len(self.sockets), error=self.error, finished=time.monotonic())

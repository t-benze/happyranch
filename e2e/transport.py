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
        self.active_responses: dict[int, socket.socket] = {}
        self.active_streams: dict[int, tuple] = {}
        self.operation_gates: dict[int, threading.Lock] = {}
        self.context_closing: dict | None = None
        self.context_closed: dict | None = None
        self.browser_requests: list[dict] = []
        self.arm_end: float | None = None
        self.proof = None
        self.matches = 0
        self.hold: dict = {}
        relay = self

        def pair_request(method: str, target: str) -> None:
            # Called under lock from either observation order. Never guess an
            # order among concurrent identical requests, or reuse a failed one.
            pending = [e for e in relay.browser_requests if e['method'] == method and e['target'] == target
                       and not any(r.get('browser_request_id') == e['request_id'] for r in relay.records)]
            records = [r for r in relay.records if r['method'] == method and r['target'] == target
                       and 'browser_request_id' not in r]
            if len(pending) == len(records) == 1 and 'failed' not in pending[0]:
                records[0]['browser_request_id'] = pending[0]['request_id']

        def browser_request(event: dict) -> None:
            with relay.lock:
                relay.browser_requests.append(dict(event))
                pair_request(event['method'], event['target'])
        self.browser_request = browser_request

        def browser_terminal(event: dict) -> None:
            with relay.lock:
                for issued in relay.browser_requests:
                    if issued['request_id'] == event['request_id']:
                        issued.setdefault('terminals', []).append(dict(event))
        self.browser_terminal = browser_terminal

        def shutdown_upstream(record: dict, sock: socket.socket, receipt: dict) -> None:
            # Caller holds relay.lock. Never block behind an I/O owner here:
            # its native call may already have failed before Python resumes.
            # The gate spans the actual call AND outcome publication, so only
            # acquisition with a successful prior outcome proves non-overlap.
            gate = relay.operation_gates[id(record)]
            acquired = gate.acquire(blocking=False)
            try:
                if not acquired:
                    receipt['error'] = 'operation_in_flight'
                else:
                    prior = record.get('upstream_operation', {})
                    read = record.get('stream_read')
                    if (prior.get('outcome') != 'returned'
                            or read is not None and ('error' in read or read.get('bytes', 0) <= 0)):
                        receipt['error'] = 'prior_operation_not_successful'
                    else:
                        receipt['operation'] = dict(prior)
                receipt['started'] = time.monotonic()
                # Even an uncertain overlap needs the original cleanup attempt;
                # that attempt never clears the refusal recorded above.
                sock.shutdown(socket.SHUT_RDWR)
                if 'error' not in receipt:
                    receipt['outcome'] = 'shutdown'
            except OSError as exc:
                receipt['error'] = type(exc).__name__
            finally:
                receipt['finished'] = time.monotonic()
                if acquired:
                    gate.release()

        def browser_abort(event: dict) -> None:
            # Native Request failure is independent of the proxy's socket
            # exception. Ambiguous/unmatched requests receive no exemption.
            with relay.lock:
                for issued in relay.browser_requests:
                    if issued['request_id'] == event['request_id']:
                        issued['failed'] = event['at']
                if event['failure'] != 'net::ERR_ABORTED' or event['method'] != 'GET':
                    return
                matches = [r for r in relay.records if r['method'] == event['method']
                           and r['target'] == event['target'] and r['ordinal'] is None
                           and r.get('browser_request_id') == event['request_id']
                           and 'delivered' not in r and 'browser_abort' not in r and 'context_shutdown' not in r
                           and r['entered'] <= event['at']]
                if len(matches) != 1:
                    return
                record = matches[0]
                record['browser_abort'] = dict(event)
                sock = relay.active_responses.get(id(record))
                if sock is not None:
                    receipt = {}
                    shutdown_upstream(record, sock, receipt)
                    record['abort_shutdown_started'] = receipt['started']
                    record['abort_shutdown'] = receipt['finished']
                    if 'operation' in receipt:
                        record['abort_shutdown_operation'] = receipt['operation']
                    if 'error' in receipt:
                        record['abort_shutdown_error'] = receipt['error']
                        relay.errors.append('browser abort upstream shutdown unproven')
        self.browser_abort = browser_abort

        def close_context_streams(event: dict, end: float) -> list[dict]:
            # The E05 context owner calls BEFORE native context.close. Only
            # independently observed, still-owned response/socket pairs qualify.
            receipts = []
            with relay.lock:
                if relay.context_closing is not None:
                    raise RuntimeError('E05 context shutdown already attempted')
                relay.context_closing = dict(event)
                for record in relay.records:
                    owned = relay.active_streams.get(id(record))
                    if (owned is None or record.get('browser_request_id') not in event['request_ids']
                            or record['method'] != 'GET' or record['ordinal'] is not None
                            or record.get('status') != 200 or record.get('streaming') is not True
                            or any(k in record for k in ('closed', 'error', 'client_disconnected', 'browser_abort'))):
                        continue
                    sock, response = owned
                    if relay.active_responses.get(id(record)) is not sock or response.isclosed():
                        continue
                    if time.monotonic() >= min(end, relay.deadline):
                        raise TimeoutError('E05 context stream shutdown deadline')
                    issued = [r for r in relay.browser_requests
                              if r['request_id'] == record['browser_request_id']]
                    if len(issued) != 1 or issued[0].get('terminals'):
                        relay.errors.append('E05 native Request ended before stream shutdown')
                        continue
                    read = record.get('stream_read')
                    receipt = dict(request_id=record['browser_request_id'], target=record['target'],
                                   context_closing=event['at'], read_id=read['id'] if read else None)
                    record['context_shutdown'] = receipt
                    shutdown_upstream(record, sock, receipt)
                    if 'error' in receipt:
                        relay.errors.append('E05 owned stream shutdown unproven')
                    receipts.append(dict(receipt))
            return receipts
        self.close_context_streams = close_context_streams

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
                operation = 'request'
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
                record = dict(method=self.command, path=self.path.split("?")[0], target=self.path,
                              ordinal=ordinal, entered=time.monotonic())
                with relay.lock:
                    relay.records.append(record)
                    relay.operation_gates[id(record)] = threading.Lock()
                    pair_request(self.command, self.path)
                try:
                    def remaining() -> float:
                        value = (end if selected else relay.deadline) - time.monotonic()
                        if value <= 0:
                            raise TimeoutError("browser transport deadline")
                        if record.get('browser_abort'):
                            record['abort_observed'] = time.monotonic()
                            raise ConnectionAbortedError('native browser request cancelled')
                        if record.get('context_shutdown'):
                            record['shutdown_observed'] = time.monotonic()
                            raise ConnectionAbortedError('owned E05 stream shutdown')
                        if relay.stopping:
                            raise ConnectionAbortedError('browser relay stopped')
                        return value

                    def upstream_call(kind: str, call: Callable, read: dict | None = None) -> object:
                        # The same gate protects the actual native HTTP call,
                        # including an exception whose publication is delayed.
                        with relay.operation_gates[id(record)]:
                            remaining()
                            outcome = dict(kind=kind, read_id=read['id'] if read else None,
                                           started=time.monotonic())
                            record['upstream_operation'] = outcome
                            if read is not None:
                                read['started'] = outcome['started']
                                record['stream_read'] = read
                            try:
                                value = call()
                            except Exception as exc:
                                outcome.update(finished=time.monotonic(), error=type(exc).__name__)
                                if read is not None:
                                    read.update(finished=outcome['finished'], error=outcome['error'])
                                raise
                            else:
                                outcome.update(finished=time.monotonic(), outcome='returned')
                                if read is not None:
                                    read.update(finished=outcome['finished'], bytes=len(value))
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
                        relay.active_responses[id(record)] = upstream_socket
                    record["forwarded"] = time.monotonic()
                    operation = 'upstream'
                    upstream_socket.settimeout(remaining())
                    response = upstream_call('getresponse', connection.getresponse)
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
                    if streaming:
                        with relay.lock:
                            relay.active_streams[id(record)] = upstream_socket, response
                    content = None
                    if selected:
                        upstream_socket.settimeout(remaining())
                        content = upstream_call('read', response.read)
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
                    operation = 'downstream'
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
                            # read1 may close the response/socket while returning
                            # the final nonempty chunk. Deliver it before checking
                            # completion, without touching that socket again.
                            while not response.isclosed():
                                self.connection.settimeout(remaining())
                                upstream_socket.settimeout(remaining())
                                operation = 'upstream'
                                if streaming:
                                    with relay.lock:
                                        remaining()
                                        read = dict(id=record.get('stream_read', {}).get('id', 0)+1,
                                                    started=time.monotonic())
                                    chunk = upstream_call('read', lambda: response.read1(65536), read)
                                else:
                                    chunk = upstream_call('read', lambda: response.read1(65536))
                                remaining()
                                if not chunk:
                                    break
                                operation = 'downstream'
                                self.connection.settimeout(remaining())
                                self.wfile.write(chunk)
                                self.connection.settimeout(remaining())
                                self.wfile.flush()
                                remaining()
                            if response.length not in (None, 0):
                                raise http.client.IncompleteRead(b'')
                            if streaming:
                                raise ConnectionError('unexpected browser stream EOF')
                    record["delivered"] = time.monotonic()
                except (BrokenPipeError, ConnectionResetError) as exc:
                    record['failure_at'] = time.monotonic()
                    record["client_disconnected"] = type(exc).__name__
                    record['failure_operation'] = operation
                    if selected:
                        relay.errors.append("original compose response disconnected")
                        relay.release.set()
                except Exception as exc:
                    record['failure_at'] = time.monotonic()
                    # Never export headers/auth/bootstrap/stream bodies or raw exceptions.
                    record["error"] = type(exc).__name__
                    record['failure_operation'] = operation
                    if selected:
                        relay.errors.append(type(exc).__name__ + ": browser transport failed")
                        relay.release.set()
                finally:
                    self.close_connection = True
                    # Retain failed ownership and still attempt the other close.
                    for name, owner in (('response', response), ('connection', connection)):
                        if owner is not None:
                            try:
                                owner.close()
                            except Exception as exc:
                                record[name+'_close_error'] = type(exc).__name__
                                relay.errors.append('E05 '+name+' close failed')
                    with relay.lock:
                        if 'connection_close_error' not in record and 'response_close_error' not in record:
                            relay.connections.discard(connection)
                            relay.connections.discard(upstream_socket)
                            relay.active_responses.pop(id(record), None)
                            relay.active_streams.pop(id(record), None)
                            relay.operation_gates.pop(id(record), None)
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
        try:
            self.server.shutdown()
        except Exception as exc:
            self.errors.append(type(exc).__name__ + ': browser listener shutdown failed')
        with self.lock:
            connections, workers = list(self.connections), list(self.workers)
        for connection in connections:
            sock = connection.sock if isinstance(connection, http.client.HTTPConnection) else connection
            if sock:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    sock.close()
                except OSError as exc:
                    self.errors.append(type(exc).__name__ + ': browser socket close failed')
        try:
            self.server.server_close()
        except Exception as exc:
            self.errors.append(type(exc).__name__ + ': browser listener close failed')
        for worker in [*workers, self.thread]:
            worker.join(timeout=max(0, end-time.monotonic()))
        left()
        settled = time.monotonic()
        errors = list(self.errors)
        for record in self.records:
            failure = record.get('error') or record.get('client_disconnected')
            if not failure:
                continue
            abort = record.get('browser_abort')
            expected = (abort is not None and record['method'] == 'GET' and record['ordinal'] is None
                        and abort['failure'] == 'net::ERR_ABORTED' and abort['method'] == 'GET'
                        and abort['url'] == f'http://127.0.0.1:{self.port}'+record['target']
                        and abort['target'] == record['target']
                        and record.get('browser_request_id') == abort['request_id']
                        and len([r for r in self.records if r.get('browser_request_id') == abort['request_id']]) == 1
                        and len([r for r in self.browser_requests if r['request_id'] == abort['request_id']
                                 and r['method'] == 'GET' and r['target'] == record['target'] and r['at'] <= abort['at']]) == 1
                        and 'abort_shutdown_error' not in record
                        and record['entered'] <= abort['at'] <= record.get('failure_at', -1) <= record['closed'] <= settled
                        and failure == 'ConnectionAbortedError'
                        and abort['at'] <= record.get('abort_shutdown_started', -1)
                        <= record.get('abort_shutdown', -1) <= record.get('abort_observed', -1) <= record['failure_at']
                        and record.get('abort_shutdown_operation') == record.get('upstream_operation')
                        and record.get('upstream_operation', {}).get('outcome') == 'returned'
                        and record['upstream_operation']['finished'] <= record['abort_shutdown_started']
                        and ('stream_read' not in record and record['upstream_operation']['read_id'] is None
                             and (record['upstream_operation']['kind'] == 'getresponse'
                                  or record.get('streaming') is not True)
                             or 'stream_read' in record and 'error' not in record['stream_read']
                             and record['stream_read'].get('bytes', 0) > 0))
            shutdown = record.get('context_shutdown')
            read = record.get('stream_read', {})
            if shutdown is not None:
                expected = (self.context_closing is not None and self.context_closed is not None
                            and record['method'] == 'GET' and record['ordinal'] is None
                            and record.get('streaming') is True and record.get('status') == 200
                            and record.get('browser_request_id') == shutdown['request_id']
                            and shutdown['request_id'] in self.context_closing['request_ids']
                            and shutdown['target'] == record['target'] and shutdown.get('outcome') == 'shutdown'
                            and 'error' not in shutdown and abort is None
                            and shutdown['context_closing'] == self.context_closing['at']
                            <= shutdown['started'] <= shutdown['finished'] <= self.context_closed['at'] <= entered
                            and shutdown['started'] <= record.get('failure_at', -1) <= record['closed'] <= settled
                            and failure == 'ConnectionAbortedError'
                            and shutdown['finished'] <= record.get('shutdown_observed', -1) <= record['failure_at']
                            and shutdown.get('operation') == record.get('upstream_operation')
                            and record.get('upstream_operation', {}).get('outcome') == 'returned'
                            and record['upstream_operation']['finished'] <= shutdown['started']
                            and (not read and shutdown['read_id'] is None
                                 and record['upstream_operation']['kind'] == 'getresponse'
                                 or read and shutdown['read_id'] == read.get('id')
                                 and record['upstream_operation']['read_id'] == read['id']
                                 and 'error' not in read and read.get('bytes', 0) > 0
                                 and read['finished'] == record['upstream_operation']['finished']))
            if not expected:
                errors.append(failure + ': browser transport failed')
        result = dict(port=self.port, entered=entered, finished=time.monotonic(),
                      workers_alive=sum(w.is_alive() for w in workers), listener_alive=self.thread.is_alive(),
                      connections=len(self.connections), errors=errors)
        if result["workers_alive"] or result["listener_alive"] or result["connections"]:
            errors.append("browser proxy ownership did not close")
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

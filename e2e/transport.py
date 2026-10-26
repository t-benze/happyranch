"""Real loopback HTTP relay; loss happens only AFTER independent commit proof."""
from __future__ import annotations

import copy
import hashlib
import http.client
import io
import json
import os
import re
import selectors
import socket
import sys
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
        self.io_owners: dict[int, object] = {}
        self.context_notify = None
        self.context_closing: dict | None = None
        self.context_closed: dict | None = None
        self.browser_requests: list[dict] = []
        # Private originals never enter ordinary record serialization. The sole
        # worker owns capture/read/write; browser observers only inspect copies.
        self.originals: dict[int, dict] = {}
        self.capture_bytes = self.capture_facts = 0
        self.capture_refusals: list[dict] = []
        self.capture_closed: float | None = None
        self.arm_end: float | None = None
        self.proof = None
        self.matches = 0
        self.hold: dict = {}
        relay = self

        class Stopped(Exception):
            """Only the request worker raises this after its own acknowledgement."""

        class RequestOwner:
            # All parser and descriptor methods run on ONE request worker.
            # The browser callback can only enqueue/wake under relay.lock.
            def __init__(self, record: dict, end: float) -> None:
                self.record, self.end = record, end
                self.handles = {}
                self.registrations = []
                self.response = None
                self.intent = None
                self.closing = False
                self.wake_ready = False
                self.received = self.delivered = self.checkpoint_bytes = 0
                self.read_id = 0
                self.checkpoint = None
                self.terminal = None
                self.parser_phase = 'initial'
                self.state = record['io_owner'] = dict(
                    runtime=dict(implementation=sys.implementation.name, version=list(sys.version_info[:3])),
                    generation=record['owner_generation'], end=end, resources=[], registrations=[], trace=[], operations=[],
                    received=0, delivered=0, forced=None, terminal=None, mode=None, errors=[])

            def trace(self, event: str, **facts: object) -> None:
                if len(self.state['trace']) >= 16384:
                    raise ValueError('E05 response accounting limit')
                self.state['trace'].append(dict(event=event, at=time.monotonic(), **facts))

            def failure(self, stage: str, error: Exception) -> None:
                # Preserve safe original/secondary exception types even if
                # http.client's automatic close replaces the parser exception.
                seen = set()
                current = error
                while current is not None and id(current) not in seen and len(seen) < 8:
                    seen.add(id(current))
                    self.state['errors'].append(dict(owner=stage, error=type(current).__name__))
                    current = current.__cause__ or current.__context__
                if current is not None:
                    self.state['errors'].append(dict(owner=stage, error='exception_chain_unresolved'))

            def acquire(self, name: str, handle: object) -> object:
                # Register immediately after acquisition, BEFORE first use.
                self.handles[name] = handle
                self.state['resources'].append(dict(name=name, acquired=time.monotonic()))
                return handle

            def left(self) -> float:
                value = min(self.end, self.intent['end'] if self.intent else self.end)-time.monotonic()
                if value <= 0:
                    raise TimeoutError('E05 original response deadline')
                if self.state['forced'] is not None or relay.stopping:
                    raise ConnectionAbortedError('E05 forced teardown')
                return value

            def initialize(self, sock: socket.socket) -> None:
                if sys.implementation.name != 'cpython' or sys.version_info[:2] not in {(3, 12), (3, 14)}:
                    raise ValueError('E05 unsupported private HTTP parser')
                duplicate = self.acquire('duplicate', sock.dup())
                duplicate.setblocking(False)
                self.state['mode'] = dict(original_blocking=os.get_blocking(sock.fileno()),
                                          duplicate_blocking=os.get_blocking(duplicate.fileno()), at=time.monotonic())
                if self.state['mode']['original_blocking'] or self.state['mode']['duplicate_blocking']:
                    raise ValueError('E05 shared response descriptor mode')
                with relay.lock:
                    relay.connections.add(duplicate)
                wake_read, wake_write = socket.socketpair()
                self.acquire('wake_read', wake_read)
                self.acquire('wake_write', wake_write)
                wake_read.setblocking(False)
                wake_write.setblocking(False)
                with relay.lock:
                    self.wake_ready = True
                selector = self.acquire('selector', selectors.DefaultSelector())
                for name, handle in (('duplicate', duplicate), ('wake_read', wake_read)):
                    selector.register(handle, selectors.EVENT_READ, name)
                    registration = dict(name=name, registered=time.monotonic())
                    self.state['registrations'].append(registration)
                    self.registrations.append((name, handle, registration))
                raw = self.acquire('raw', Raw(self))
                buffered = self.acquire('buffer', io.BufferedReader(raw, buffer_size=8192))
                self.file = self.acquire('file', File(self, buffered))
                self.mark('before-response')

            def enqueue(self, kind: str, event: dict, end: float) -> dict:
                # Caller holds only relay.lock, never an operation gate.
                if self.intent is not None or self.closing:
                    raise ValueError('E05 duplicate or closed stop owner')
                self.intent = self.record['stop_intent'] = dict(
                    kind=kind, request_id=event['request_id'] if kind == 'navigation' else self.record['browser_request_id'],
                    method=self.record['method'], target=self.record['target'], generation=self.state['generation'],
                    event=dict(event), operation_id=self.record.get('upstream_operation', {}).get('id'),
                    enqueued=time.monotonic(), end=min(end, self.end))
                self.wake()
                return self.intent

            def wake(self) -> None:
                # Every caller holds relay.lock, as does the close latch.
                if self.closing or not self.wake_ready:
                    return
                handle = self.handles.get('wake_write')
                if handle is None:
                    return  # A queued intent is inspected by the first read.
                try:
                    handle.send(b'!')
                except BlockingIOError:
                    pass  # An already queued wake is sufficient notification.
                except OSError as exc:
                    self.state['errors'].append(dict(owner='wake_write', error=type(exc).__name__))

            def mark(self, kind: str, *, chunk_left: int | None = None, delimiter: str | None = None,
                     remaining: int | None = None) -> None:
                # A semantic checkpoint NEVER drops unread/read-ahead bytes.
                # received-delivered also includes an in-progress file call.
                self.checkpoint_bytes = self.delivered
                self.checkpoint = dict(kind=kind, received=self.received, delivered=self.delivered,
                                       chunk_left=chunk_left, delimiter=delimiter, remaining=remaining)
                self.trace('checkpoint', **self.checkpoint)

            def stop(self, would_block: bool) -> None:
                if self.intent is None:
                    return
                self.left()
                checkpoint = self.checkpoint
                current = self.record['upstream_operation']
                prior = self.state['operations'][-2] if len(self.state['operations']) > 1 else None
                complete = (checkpoint is not None and checkpoint['kind'] == 'between'
                            and prior is not None and prior.get('outcome') == 'returned'
                            and (prior['kind'] == 'getresponse' and self.record['response_outcome']['length'] == 0
                                 or prior['kind'] == 'read' and prior['result']['closed']))
                terminal_complete = self.terminal == 'EOF' and complete and not self.record.get('streaming')
                if (self.terminal is not None and not terminal_complete or self.state['errors'] or checkpoint is None
                        or self.received != self.delivered or self.delivered != self.checkpoint_bytes):
                    raise ValueError('E05 stop at unvalidated partial response')
                # Before headers means zero ENTIRE response bytes, including
                # discarded interim responses. No successful headers are invented.
                if checkpoint['kind'] == 'before-response':
                    if (self.intent['kind'] != 'navigation' or self.received != 0
                            or current['kind'] != 'getresponse' or prior is not None
                            or 'response_outcome' in self.record):
                        raise ValueError('E05 nonempty response before headers')
                elif (prior is None or prior.get('outcome') != 'returned'
                      or not relay._response_facts(self.record.get('response_outcome', {}), 'GET')):
                    raise ValueError('E05 prior response operation not validated')
                with relay.lock:
                    issued = [e for e in relay.browser_requests if e['request_id'] == self.intent['request_id']]
                    if (relay.io_owners.get(id(self.record)) is not self or len(issued) != 1
                            or issued[0]['method'] != 'GET' or issued[0]['target'] != self.record['target']
                            or relay.active_responses.get(id(self.record)) is not self.handles.get('original')
                            or self.state['forced'] is not None):
                        raise ValueError('E05 stop owner detached')
                    terminals = issued[0].get('terminals', [])
                    if self.intent['kind'] == 'navigation':
                        trigger = dict(self.intent['event'])
                        trigger.pop('target')
                        if terminals != [trigger] or trigger.get('failure') != 'net::ERR_ABORTED':
                            raise ValueError('E05 stop lacks original native abort')
                    elif (terminals or self.record.get('status') != 200 or not self.record.get('streaming')
                          or relay.active_streams.get(id(self.record)) != (self.handles['original'], self.response)):
                        raise ValueError('E05 original native stream no longer live')
                    started = time.monotonic()
                    self.left()
                    ack = self.record['stop_ack'] = dict(
                        generation=self.state['generation'], request_id=self.intent['request_id'],
                        operation_id=current['id'], previous_operation_id=prior['id'] if prior else None,
                        read_id=current['read_id'], previous_read_id=prior['read_id'] if prior else None,
                        checkpoint=dict(checkpoint), received=self.received, delivered=self.delivered,
                        buffered=self.received-self.delivered, since_checkpoint=self.delivered-self.checkpoint_bytes,
                        would_block=would_block, started=started)
                    # The worker alone shuts down; no other thread can race a
                    # native result between receive, validation and publication.
                    try:
                        if not complete:
                            self.handles['duplicate'].shutdown(socket.SHUT_RDWR)
                        self.left()
                        ack['outcome'] = 'already-complete' if complete else 'shutdown'
                    except OSError as exc:
                        ack['error'] = type(exc).__name__
                        raise
                    finally:
                        ack['finished'] = time.monotonic()
                raise Stopped()

            def between(self) -> None:
                if self.intent is None:
                    return
                # Preserve a completed return as a separate previous operation.
                self.begin_operation('stop', None)
                try:
                    self.stop(False)
                except Stopped:
                    self.finish_stopped()
                    raise
                except Exception as exc:
                    self.record['upstream_operation'].update(finished=time.monotonic(), error=type(exc).__name__)
                    raise

            def begin_operation(self, kind: str, read: dict | None) -> None:
                if kind == 'read':
                    self.read_id += 1
                with relay.lock:
                    operation = dict(id=len(self.state['operations'])+1, kind=kind,
                                     read_id=self.read_id if kind == 'read' else None, started=time.monotonic())
                    self.state['operations'].append(operation)
                    self.record['upstream_operation'] = operation
                if read is not None:
                    read.update(id=operation['read_id'], started=operation['started'])
                    self.record['stream_read'] = read

            def finish_stopped(self) -> None:
                operation = self.record['upstream_operation']
                operation.update(finished=time.monotonic(), outcome='cancelled')
                read = self.record.get('stream_read')
                if read is not None and operation['read_id'] == read['id']:
                    read.update(finished=operation['finished'], outcome='cancelled')

            def receive(self, buffer: bytearray | memoryview) -> int:
                if not 0 < len(buffer) <= 65536:
                    raise ValueError('E05 unexpected raw receive buffer')
                while True:
                    self.left()
                    try:
                        if os.get_blocking(self.handles['duplicate'].fileno()):
                            raise ValueError('E05 response descriptor became blocking')
                        count = self.handles['duplicate'].recv_into(buffer)
                    except BlockingIOError:
                        # Always attempt real recv FIRST, also after a wake.
                        # No would-block/None is returned to http.client.
                        self.trace('would-block', received=self.received, delivered=self.delivered)
                        self.stop(True)
                    except OSError as exc:
                        self.terminal = self.state['terminal'] = type(exc).__name__
                        self.trace('raw-error', error=self.terminal)
                        raise
                    else:
                        self.received += count
                        self.state['received'] = self.received
                        if count == 0:
                            self.terminal = self.state['terminal'] = 'EOF'
                        self.trace('receive', bytes=count, received=self.received)
                        return count
                    for key, _ in self.handles['selector'].select(self.left()):
                        if key.data == 'wake_read':
                            try:
                                self.handles['wake_read'].recv(4096)
                            except BlockingIOError:
                                pass

            def close(self) -> None:
                # Exclude callback send from descriptor close/reuse. This short
                # lock never spans parser/file/socket/selector close work.
                with relay.lock:
                    self.closing = True
                # Unregister before closing any descriptor. Every acquired
                # owner is attempted independently, including partial setup.
                for name, handle, registration in self.registrations:
                    try:
                        self.handles['selector'].unregister(handle)
                        registration['unregistered'] = time.monotonic()
                    except Exception as exc:
                        registration['error'] = type(exc).__name__
                        self.state['errors'].append(dict(owner='registration:'+name, error=type(exc).__name__))
                for resource in reversed(self.state['resources']):
                    try:
                        self.handles[resource['name']].close()
                    except Exception as exc:
                        resource['error'] = type(exc).__name__
                        self.state['errors'].append(dict(owner=resource['name'], error=type(exc).__name__))
                    else:
                        resource['closed'] = time.monotonic()
                self.state.update(received=self.received, delivered=self.delivered, finished=time.monotonic())

        class Raw(io.RawIOBase):
            def __init__(self, owner: RequestOwner) -> None:
                super().__init__()
                self.owner = owner

            def readable(self) -> bool:
                return True

            def readinto(self, buffer: bytearray | memoryview) -> int:
                return self.owner.receive(buffer)

        class File:
            def __init__(self, owner: RequestOwner, buffered: io.BufferedReader) -> None:
                self.owner, self.buffered = owner, buffered
                self.last_line = None

            def returned(self, method: str, value: bytes) -> bytes:
                self.owner.delivered += len(value)
                self.owner.state['delivered'] = self.owner.delivered
                self.owner.trace('file-return', method=method, bytes=len(value),
                                 delivered=self.owner.delivered)
                return value

            def read(self, size: int = -1) -> bytes:
                if self.owner.parser_phase != 'delimiter' or size != 2:
                    raise ValueError('E05 unexpected parser read')
                self.owner.left()
                return self.returned('read', self.buffered.read(size))

            def read1(self, size: int = -1) -> bytes:
                if self.owner.parser_phase not in {'payload','finite','body'} or not 0 <= size <= 65536:
                    raise ValueError('E05 unexpected parser read1')
                self.owner.left()
                return self.returned('read1', self.buffered.read1(size))

            def readinto(self, buffer: bytearray | memoryview) -> int:
                raise ValueError('E05 unexpected parser readinto')

            def readline(self, size: int = -1) -> bytes:
                if self.owner.parser_phase not in {'headers','chunk-size','trailer'}:
                    raise ValueError('E05 unexpected parser readline')
                self.owner.left()
                value = self.returned('readline', self.buffered.readline(size))
                self.last_line = value
                if self.owner.parser_phase == 'trailer' and not value.endswith(b'\r\n'):
                    raise ValueError('E05 incomplete chunk trailer')
                return value

            def flush(self) -> None:
                try:
                    self.buffered.flush()
                except Exception as exc:
                    self.owner.failure('file-flush', exc)
                    raise

            def close(self) -> None:
                try:
                    self.buffered.close()
                except Exception as exc:
                    self.owner.failure('file-close', exc)
                    raise

        class Response(http.client.HTTPResponse):
            def __init__(self, owner: RequestOwner, sock: socket.socket, *args: object, **kwargs: object) -> None:
                self.owner = owner
                owner.response = self
                owner.acquire('response', self)
                class SocketFile:
                    def makefile(_self, mode: str) -> File:
                        if mode != 'rb':
                            raise ValueError('E05 unexpected response file mode')
                        return owner.file
                super().__init__(SocketFile(), *args, **kwargs)

            def begin(self) -> None:
                self.owner.parser_phase = 'headers'
                try:
                    super().begin()
                except Stopped:
                    raise
                except Exception as exc:
                    self.owner.failure('parser-begin', exc)
                    raise
                finally:
                    self.owner.parser_phase = 'unknown'

            def read(self, amt: int | None = None) -> bytes:
                raise ValueError('E05 GET owner supports only the existing read1 caller')

            def readinto(self, buffer: bytearray | memoryview) -> int:
                raise ValueError('E05 unexpected GET readinto caller')

            def peek(self, size: int = -1) -> bytes:
                raise ValueError('E05 unexpected GET peek caller')

            def readline(self, limit: int = -1) -> bytes:
                raise ValueError('E05 unexpected GET readline caller')

            def read1(self, size: int = -1) -> bytes:
                if size != 65536:
                    raise ValueError('E05 unexpected GET read size')
                self.owner.parser_phase = 'finite' if self.length is not None else 'body'
                try:
                    return super().read1(size)
                except Stopped:
                    raise
                except Exception as exc:
                    self.owner.failure('parser-read1', exc)
                    raise
                finally:
                    self.owner.parser_phase = 'unknown'

            def close(self) -> None:
                try:
                    super().close()
                except Exception as exc:
                    self.owner.failure('response-close', exc)
                    raise

            def _get_chunk_left(self) -> int | None:
                # CPython3.12/3.14 read1 calls this BEFORE any payload read.
                # Retain stdlib size/trailer parsing; validate its discarded CRLF.
                left = self.chunk_left
                if not left:
                    delimiter = None
                    if left is not None:
                        self.owner.checkpoint = None
                        self.owner.parser_phase = 'delimiter'
                        delimiter = self._safe_read(2)
                        if delimiter != b'\r\n':
                            raise ValueError('E05 invalid chunk delimiter')
                    self.owner.mark('chunk-size', delimiter='0d0a' if delimiter else None)
                    self.owner.parser_phase = 'chunk-size'
                    left = self._read_next_chunk_size()
                    line = self.owner.file.last_line
                    prefix = line.split(b';', 1)[0].removesuffix(b'\r\n') if line is not None else b''
                    if (line is None or not line.endswith(b'\r\n') or not prefix
                            or any(byte not in b'0123456789abcdefABCDEF' for byte in prefix)
                            or not 0 <= left <= 2**63-1):
                        raise ValueError('E05 invalid chunk size framing')
                    if left == 0:
                        # No checkpoint during zero chunk/trailer processing.
                        self.owner.checkpoint = None
                        self.owner.trace('chunk-terminal', size=0, received=self.owner.received, delivered=self.owner.delivered)
                        self.owner.parser_phase = 'trailer'
                        self._read_and_discard_trailer()
                        if self.owner.file.last_line != b'\r\n':
                            raise ValueError('E05 incomplete trailer terminator')
                        self.owner.trace('trailer-end', delimiter='0d0a', received=self.owner.received, delivered=self.owner.delivered)
                        self._close_conn()
                        left = None
                    else:
                        self.owner.mark('payload', chunk_left=left)
                    self.chunk_left = left
                elif type(left) is not int or left < 0:
                    raise ValueError('E05 unknown chunk parser state')
                else:
                    self.owner.mark('payload', chunk_left=left)
                self.owner.parser_phase = 'payload'
                return left

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
                if len(relay.browser_requests) >= 512 or not relay._charge(None):
                    relay._overflow('membership-cap')
                    raise ValueError('E05 native Request membership capacity refused')
                relay.browser_requests.append(dict(event))
                pair_request(event['method'], event['target'])
        self.browser_request = browser_request

        def browser_terminal(event: dict) -> None:
            with relay.lock:
                for issued in relay.browser_requests:
                    if issued['request_id'] == event['request_id']:
                        if not relay._charge(None):
                            raise ValueError('E05 native terminal capacity refused')
                        issued.setdefault('terminals', []).append(dict(event))
        self.browser_terminal = browser_terminal

        def enqueue_stop(record: dict, kind: str, event: dict, end: float) -> dict:
            owner = relay.io_owners.get(id(record))
            if owner is None:
                raise ValueError('E05 original response owner unavailable')
            return owner.enqueue(kind, event, end)

        def browser_abort(event: dict) -> None:
            with relay.lock:
                for issued in relay.browser_requests:
                    if issued['request_id'] == event['request_id']:
                        issued['failed'] = event['at']
                if event['failure'] != 'net::ERR_ABORTED' or event['method'] != 'GET':
                    return
                matches = [r for r in relay.records if r['method'] == event['method']
                           and r['target'] == event['target'] and r['ordinal'] is None
                           and r.get('browser_request_id') == event['request_id']
                           and 'delivered' not in r and 'closed' not in r
                           and 'stop_intent' not in r and r['entered'] <= event['at']]
                if len(matches) != 1:
                    return
                record = matches[0]
                record['browser_abort'] = dict(event)
                try:
                    enqueue_stop(record, 'navigation', event, relay.deadline)
                except Exception as exc:
                    record['abort_shutdown_error'] = type(exc).__name__
                    relay.errors.append('E05 native abort owner unavailable')
        self.browser_abort = browser_abort

        def close_context_streams(event: dict, end: float) -> list[dict]:
            # Enqueue only. The native browser loop remains live while its local
            # coroutine awaits worker-finally notifications and real receipts.
            with relay.lock:
                if relay.context_closing is not None:
                    raise RuntimeError('E05 context shutdown already attempted')
                relay.context_closing = dict(event)
                records = []
                for request_id in event['request_ids']:
                    matches = [r for r in relay.records if r.get('browser_request_id') == request_id
                               and r['method'] == 'GET' and r['ordinal'] is None]
                    if len(matches) != 1:
                        raise ValueError('E05 ambiguous original live stream owner')
                    record = matches[0]
                    if (id(record) not in relay.active_streams or record.get('status') != 200
                            or record.get('streaming') is not True
                            or any(k in record for k in ('closed', 'error', 'client_disconnected', 'stop_intent'))):
                        raise ValueError('E05 live stream owner unavailable')
                    enqueue_stop(record, 'context', event, end)
                    records.append(record)
                return records
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
                io_owner = None
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
                    if self.command == 'GET' and not selected:
                        record['owner_generation'] = len(relay.records)+1
                    record['relay_id'] = len(relay.records)
                    # Reserve inventory BEFORE retaining this worker's record.
                    # Overflow keeps one sticky marker. Later traffic still has
                    # its sole local read/forward/close owner, without a retained
                    # per-request original/forwarding/refusal tail.
                    if (len(relay.records) >= 512 or len(self.path.encode()) > 8192
                            or relay.capture_closed is not None):
                        relay._overflow('late-member' if relay.capture_closed is not None else 'membership-cap')
                    elif relay._charge(None):
                        relay.records.append(record)
                        relay._capture_begin(record, selected)
                    relay.operation_gates[id(record)] = threading.Lock()
                    pair_request(self.command, self.path)
                try:
                    def remaining() -> float:
                        value = (end if selected else relay.deadline) - time.monotonic()
                        if value <= 0:
                            raise TimeoutError("browser transport deadline")
                        if io_owner is not None and io_owner.state['operations']:
                            with relay.operation_gates[id(record)]:
                                io_owner.between()
                        if relay.stopping:
                            raise ConnectionAbortedError('browser relay stopped')
                        return value

                    def upstream_call(kind: str, call: Callable, validate: Callable,
                                      read: dict | None = None) -> object:
                        # The same gate protects the actual native HTTP call,
                        # including an exception whose publication is delayed.
                        with relay.operation_gates[id(record)]:
                            if io_owner is not None:
                                io_owner.left()
                                io_owner.begin_operation(kind, read)
                                if kind == 'read' and not response.chunked:
                                    if type(response.length) is int and response.length > 0:
                                        io_owner.mark('finite', remaining=response.length)
                                    else:
                                        io_owner.checkpoint = None
                                outcome = record['upstream_operation']
                            else:
                                remaining()
                                outcome = dict(kind=kind, read_id=read['id'] if read else None,
                                               started=time.monotonic())
                                record['upstream_operation'] = outcome
                            if read is not None:
                                read['started'] = outcome['started']
                                record['stream_read'] = read
                            try:
                                value = call()
                                # No cancellation check between native return,
                                # indispensable validation and publication.
                                validate(value, outcome)
                            except Stopped:
                                io_owner.finish_stopped()
                                raise
                            except Exception as exc:
                                outcome.update(finished=time.monotonic(), error=type(exc).__name__)
                                if read is not None:
                                    read.update(finished=outcome['finished'], error=outcome['error'])
                                raise
                            else:
                                outcome.update(finished=time.monotonic(), outcome='returned')
                                if read is not None:
                                    read.update(finished=outcome['finished'], bytes=len(value))
                                if io_owner is not None:
                                    io_owner.mark('between')
                                return value

                    def receive_response() -> http.client.HTTPResponse:
                        nonlocal response
                        # Retain the owner even when header validation fails:
                        # HTTPConnection may already have detached its socket.
                        response = connection.getresponse()
                        return response

                    def validate_response(value, outcome: dict) -> None:
                        facts = dict(status=value.status,
                                     transfer_encoding=value.headers.get_all('Transfer-Encoding', []),
                                     content_length=value.headers.get_all('Content-Length', []),
                                     content_type=value.headers.get_all('Content-Type', []),
                                     chunked=value.chunked, length=value.length)
                        outcome['result'] = facts
                        relay._capture_headers(record, value)
                        if not relay._response_facts(facts, self.command):
                            raise ValueError('unsupported upstream response framing')
                        if io_owner is not None and facts['length'] == 0 and io_owner.received != io_owner.delivered:
                            raise ValueError('E05 buffered bytes after bodyless response')
                        record.update(response_outcome=facts, status=value.status,
                                      streaming=(facts['content_type'][0] if facts['content_type'] else '')
                                      .split(';')[0] == 'text/event-stream')

                    def read_response(call: Callable, read: dict | None = None, whole: bool = False) -> bytes:
                        returned = None
                        def validate_read(value, outcome: dict) -> None:
                            nonlocal returned
                            # Retain the actual native return even if its framing
                            # validation fails. IncompleteRead.partial is used
                            # only when the native call itself did not return.
                            returned = value
                            facts = dict(bytes=len(value), remaining_before=before,
                                         remaining_after=response.length, closed=response.isclosed(),
                                         eof=not value)
                            outcome['result'] = facts
                            if selected and len(value) > capacity:
                                with relay.lock:
                                    relay._refuse(record, 'entity-cap')
                                raise ValueError('E05 selected capture capacity exceeded')
                            if not relay._read_facts(facts, record['response_outcome'], streaming,
                                                     whole=whole):
                                raise http.client.IncompleteRead(b'')
                            if io_owner is not None and facts['closed'] and io_owner.received != io_owner.delivered:
                                raise ValueError('E05 buffered bytes after complete response')
                        # The gate in upstream_call spans both this native read
                        # and validation; no other owner changes response.length.
                        before = response.length
                        try:
                            value = upstream_call('read', call, validate_read, read)
                        except Exception as exc:
                            partial = returned if returned is not None else exc.partial if isinstance(exc, http.client.IncompleteRead) else b''
                            relay._capture_chunk(record, partial)
                            relay._capture_error(record, 'read', exc)
                            raise
                        relay._capture_chunk(record, value)
                        if response.isclosed():
                            relay._capture_seal(record)
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
                    if self.command == 'GET' and not selected:
                        io_owner = RequestOwner(record, end)
                        with relay.lock:
                            relay.io_owners[id(record)] = io_owner
                        io_owner.acquire('connection', connection)
                        io_owner.acquire('original', upstream_socket)
                        io_owner.initialize(upstream_socket)
                        connection.response_class = lambda sock, *args, **kwargs: Response(io_owner, sock, *args, **kwargs)
                    else:
                        upstream_socket.settimeout(remaining())
                    response = upstream_call('getresponse', receive_response, validate_response)
                    remaining()
                    headers = response.getheaders()
                    streaming = record['streaming']
                    if streaming:
                        with relay.lock:
                            relay.active_streams[id(record)] = upstream_socket, response
                    content = None
                    if selected:
                        upstream_socket.settimeout(remaining())
                        # One overflow-detection byte, never an unbounded whole read.
                        capacity = relay._capture_capacity(record)
                        content = read_response(lambda: response.read(capacity + 1), whole=True)
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
                                parsed = relay._parse_original(record)
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
                            segment = relay._forward_start(record, content)
                            written = self.wfile.write(content)
                            relay._forward_return(record, segment, written)
                            self.connection.settimeout(remaining())
                            self.wfile.flush()
                            relay._forward_flush(record, segment)
                            remaining()
                        else:
                            # read1 may close the response/socket while returning
                            # the final nonempty chunk. Deliver it before checking
                            # completion, without touching that socket again.
                            while not response.isclosed():
                                self.connection.settimeout(remaining())
                                if io_owner is None:
                                    upstream_socket.settimeout(remaining())
                                operation = 'upstream'
                                if streaming:
                                    remaining()
                                    read = dict(id=record.get('stream_read', {}).get('id', 0)+1,
                                                started=time.monotonic())
                                    chunk = read_response(lambda: response.read1(65536), read)
                                else:
                                    chunk = read_response(lambda: response.read1(65536))
                                remaining()
                                if not chunk:
                                    break
                                operation = 'downstream'
                                self.connection.settimeout(remaining())
                                segment = relay._forward_start(record, chunk)
                                written = self.wfile.write(chunk)
                                relay._forward_return(record, segment, written)
                                self.connection.settimeout(remaining())
                                self.wfile.flush()
                                relay._forward_flush(record, segment)
                                remaining()
                            if response.length not in (None, 0):
                                raise http.client.IncompleteRead(b'')
                            if streaming:
                                raise ConnectionError('unexpected browser stream EOF')
                    relay._capture_seal(record)
                    relay._forward_seal(record)
                    record["delivered"] = time.monotonic()
                except Stopped:
                    record.update(failure_at=time.monotonic(), error='Stopped', failure_operation=operation)
                except (BrokenPipeError, ConnectionResetError) as exc:
                    record['failure_at'] = time.monotonic()
                    record["client_disconnected"] = type(exc).__name__
                    record['failure_operation'] = operation
                    relay._capture_error(record, operation, exc)
                    if selected:
                        relay.errors.append("original compose response disconnected")
                        relay.release.set()
                except Exception as exc:
                    record['failure_at'] = time.monotonic()
                    # Only the explicit framing facts above are retained;
                    # never export arbitrary headers/auth/bodies or raw exceptions.
                    record["error"] = type(exc).__name__
                    record['failure_operation'] = operation
                    relay._capture_error(record, operation, exc)
                    if selected:
                        relay.errors.append(type(exc).__name__ + ": browser transport failed")
                        relay.release.set()
                finally:
                    self.close_connection = True
                    if io_owner is not None:
                        # HTTPConnection may not return a partially parsed
                        # response. Its constructor registered that owner first.
                        io_owner.close()
                    else:
                        for name, owner in (('response', response), ('connection', connection)):
                            if owner is not None:
                                try:
                                    owner.close()
                                except Exception as exc:
                                    record[name+'_close_error'] = type(exc).__name__
                                    relay.errors.append('E05 '+name+' close failed')
                    with relay.lock:
                        complete = (not io_owner.state['errors'] if io_owner is not None else
                                    'connection_close_error' not in record and 'response_close_error' not in record)
                        if complete:
                            relay.connections.discard(connection)
                            relay.connections.discard(upstream_socket)
                            if io_owner is not None:
                                relay.connections.discard(io_owner.handles.get('duplicate'))
                            relay.active_responses.pop(id(record), None)
                            relay.active_streams.pop(id(record), None)
                            relay.io_owners.pop(id(record), None)
                            relay.operation_gates.pop(id(record), None)
                        record['closed'] = time.monotonic()
                        if io_owner is not None and io_owner.intent is not None:
                            record['stop_settled'] = time.monotonic()
                        notify = relay.context_notify
                    if notify is not None:
                        try:
                            notify()
                        except Exception as exc:
                            relay.errors.append('E05 acknowledgement notification '+type(exc).__name__)

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
                        # Retain this actual worker until close observes/join its exit.

            def handle_error(self, request, address):
                relay.errors.append("browser request worker failed")

        self.server = Server(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.server.block_on_close = False
        self.port = self.server.server_port
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.05), daemon=True)
        self.thread.start()

    def inventory_fact(self, text: str) -> None:
        with self.lock:
            # This gate precedes every native request/response/terminal retained
            # allocation. A refusal never starts a second uncharged inventory.
            if self.capture_closed is not None:
                self._overflow('late-member')
            size = len(text.encode('utf8'))
            if size > 8192:
                self._overflow('identity-cap')
            if not self._charge(None, size):
                raise ValueError('E05 native identity inventory capacity refused')

    @staticmethod
    def required_kind(method: str, target: str, selected: bool = False) -> str | None:
        path = target.split('?')[0]
        if selected and method == 'POST' and path == '/api/v1/orgs/alpha/threads':
            return 'compose'
        if method == 'GET' and re.fullmatch(r'/api/v1/orgs/alpha/threads/THR-\d+(?:/messages)?', path):
            return 'messages' if path.endswith('/messages') else 'detail'
        return None

    def _overflow(self, reason: str, relay_id: int | None = None) -> None:
        # One pre-reserved constant-size emergency slot, outside the ordinary
        # budget. It makes the census incomplete and never grows per request.
        if not self.capture_refusals:
            self.capture_refusals.append(dict(reason=reason, at=time.monotonic(), relay_id=relay_id))

    def _refuse(self, record: dict | None, reason: str) -> None:
        self._overflow(reason, record['relay_id'] if record is not None else None)
        original = record.get('original') if record is not None else None
        if original is not None and original['capture_refusal'] is None:
            # Each admitted member reserved this fixed refusal slot at begin.
            original.update(capture_state='refused', capture_refusal=self.capture_refusals[0])

    def _charge(self, record: dict | None, size: int = 0, facts: int = 1) -> bool:
        if (self.capture_refusals or self.capture_bytes + size > 8*1024*1024
                or self.capture_facts + facts > 16384):
            self._refuse(record, 'aggregate-cap' if self.capture_bytes + size > 8*1024*1024 else 'fact-cap')
            return False
        self.capture_bytes += size
        self.capture_facts += facts
        return True

    def _capture_begin(self, record: dict, selected: bool) -> None:
        # Caller holds relay.lock. Reserve original/forwarding/error/refusal
        # slots before retaining any of them, in addition to the record slot.
        kind = self.required_kind(record['method'], record['target'], selected)
        if kind is None:
            return
        if len(self.originals) >= 512 or self.capture_closed is not None:
            self._overflow('late-member' if self.capture_closed is not None else 'membership-cap',record['relay_id'])
            return
        if not self._charge(record, facts=4):
            return
        record['original'] = dict(schema=1, relay_id=record['relay_id'], required_kind=kind,
            started=record['entered'], status=None, framing=None, header_bytes=None, finished=None,
            outcome='pending', byte_length=0, sha256=None, first_error=None,
            capture_state='open', capture_refusal=None)
        record['forwarding'] = dict(segments=[], byte_length=None, sha256=None, finished=None)
        self.originals[record['relay_id']] = dict(data=bytearray(), headers=None, parsed=None,
                                               parsed_ready=False, parse_error=None)

    def _capture_headers(self, record: dict, response: http.client.HTTPResponse) -> None:
        if 'original' not in record:
            return
        with self.lock:
            o = record['original']
            # getheaders() returns ordered parsed pairs, not raw wire octets.
            size = sum(len(k.encode('utf8'))+len(v.encode('utf8')) for k,v in response.headers.items())
            if size > 65536 or not self._charge(record, size):
                self._refuse(record, 'header-cap')
                return
            private = self.originals.get(record['relay_id'])
            if private is None or o['capture_state'] == 'refused':
                return
            o['status'] = response.status
            o['header_bytes'] = size
            private['headers'] = response.getheaders()
            o['framing'] = dict(content_type=response.headers.get_all('Content-Type', []),
                content_encoding=response.headers.get_all('Content-Encoding', []),
                content_length=response.headers.get_all('Content-Length', []),
                transfer_encoding=response.headers.get_all('Transfer-Encoding', []),
                bodyless=record['method'] == 'HEAD' or response.status in {204,304})
            if type(response.length) is int and response.length > 1024*1024:
                self._refuse(record, 'entity-cap')

    def _capture_capacity(self, record: dict) -> int:
        with self.lock:
            private = self.originals.get(record['relay_id'])
            if private is None or record['original']['capture_state'] == 'refused':
                raise ValueError('E05 selected capture refused before read')
            capacity = max(0,min(1024*1024-len(private['data']), (8*1024*1024-self.capture_bytes)//2))
            lengths = record['original']['framing']['content_length']
            if lengths and int(lengths[0]) > capacity:
                self._refuse(record, 'selected-declared-capacity')
                raise ValueError('E05 selected capture refused before read')
            return capacity

    def _capture_chunk(self, record: dict, data: bytes) -> None:
        if 'original' not in record:
            return
        with self.lock:
            o = record['original']
            private = self.originals.get(record['relay_id'])
            if o['capture_state'] != 'open' or private is None:
                return
            if len(private['data'])+len(data) > 1024*1024:
                self._refuse(record, 'entity-cap')
            elif self._charge(record, len(data)*(2 if o['required_kind'] == 'compose' else 1)):
                private['data'].extend(data)
                o['byte_length'] += len(data)

    def _capture_seal(self, record: dict) -> None:
        if 'original' not in record:
            return
        with self.lock:
            o = record['original']
            if o['capture_state'] != 'open':
                return
            private = self.originals[record['relay_id']]
            o.update(capture_state='sealed', finished=time.monotonic(),
                     sha256=hashlib.sha256(private['data']).hexdigest(),
                     outcome='bodyless' if o['framing']['bodyless'] else 'complete' if private['data'] else 'empty')

    def _capture_error(self, record: dict, stage: str, error: Exception) -> None:
        if 'original' not in record:
            return
        with self.lock:
            o = record['original']
            # A downstream failure cannot rewrite a sealed upstream original.
            storage_error = type(error) is ValueError and error.args in {
                ('E05 selected capture refused before read',), ('E05 selected capture capacity exceeded',)}
            if stage != 'downstream' and o['capture_state'] != 'sealed' and not storage_error and o['first_error'] is None:
                o['first_error'] = dict(stage=stage, error=type(error).__name__, at=time.monotonic())
                private = self.originals.get(record['relay_id'])
                o.update(outcome='partial' if o['byte_length'] else 'error', finished=o['first_error']['at'],
                         sha256=hashlib.sha256(private['data']).hexdigest() if private is not None else None)

    def _forward_start(self, record: dict, data: bytes) -> dict | None:
        if 'original' not in record:
            return None
        with self.lock:
            if not self._charge(record):
                return None
            segments = record['forwarding']['segments']
            segment = dict(offset=sum(s['length'] for s in segments), length=len(data),
                           sha256=hashlib.sha256(data).hexdigest(), write_started=time.monotonic(),
                           write_returned=None, flush_returned=None, written=None)
            segments.append(segment)
            return segment

    def _forward_return(self, record: dict, segment: dict | None, written: int) -> None:
        if segment is not None:
            with self.lock:
                segment.update(write_returned=time.monotonic(), written=written)
            if written != segment['length']:
                raise OSError('E05 short downstream write')

    def _forward_flush(self, record: dict, segment: dict | None) -> None:
        if segment is not None:
            with self.lock:
                segment['flush_returned'] = time.monotonic()

    def _forward_seal(self, record: dict) -> None:
        if 'original' not in record:
            return
        with self.lock:
            o, f = record['original'], record['forwarding']
            if (o['capture_state'] == 'sealed' and sum(s['length'] for s in f['segments']) == o['byte_length']
                    and all(s['flush_returned'] is not None and s['written'] == s['length'] for s in f['segments'])):
                f.update(byte_length=o['byte_length'], sha256=o['sha256'], finished=time.monotonic())

    @staticmethod
    def _publishable_body(value: object, depth: int = 0) -> bool:
        # E05 has finite public fixtures. Unknown fields or nonfixture strings
        # stay private rather than leaking an unexpected error/credential body.
        # This is publication selection, never a replacement content oracle.
        keys = set(('thread_id subject status started_at archived_at forwarded_from_id forwarded_from_kind '
                    'turn_cap turns_used summary transcript_path composed_by composed_from_task_id composed_from_dream_id '
                    'last_speaker pinned pinned_at last_activity_at participants messages reply_delivery pending_replies '
                    'has_more next_since_seq seq speaker kind body_markdown decline_reason system_payload attachments created_at '
                    'responder_status agent_name purpose responded_at category state from_seq through_seq coalesced_message_count '
                    'updated_at last_terminal_reason current_failure_category kind_tag task_id dispatcher target_agent team brief_preview '
                    'original_task_id root_task_id final_output_summary final_output_dir cancelled revisit_chain_length revisit_task_id').split())
        literals = {'case_manager','case_worker','founder','engineering','open','message','system','reply','task_followup',
                    'working','queued','running','held','retry_required','replied','declined','completed','failed','cancelled',
                    'no_callback','no_callback_after_reprompt','infra_fail','task_dispatched','task_completed',
                    'E05 compose race','E05 denial venue','E05 dispatch','E05 opening\nliteral beta',
                    'E05 reply\n@case_worker received','E05 denial anchor','E05 do one task','E05 task accepted',
                    'E05 task complete','E05 one owned task','E05 initial observed','E05 reply observed','E05 denial venue observed'}
        if depth > 16:
            return False
        if value is None or type(value) is bool:
            return True
        if type(value) is int:
            return 0 <= value <= 9007199254740991
        if type(value) is str:
            return (value in literals or bool(re.fullmatch(r'(?:THR|TASK)-\d+',value))
                    or len(value) <= 64 and bool(re.fullmatch(r'\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)?',value))
                    or len(value) <= 4096 and bool(re.fullmatch(r'(?:/?[A-Za-z0-9_.-]+/)*threads/THR-\d+\.(?:md|jsonl)',value)))
        if type(value) is list:
            return len(value) <= 512 and all(BrowserRelay._publishable_body(v,depth+1) for v in value)
        if type(value) is dict:
            return set(value) <= keys and all(BrowserRelay._publishable_body(v,depth+1) for v in value.values())
        return False

    def _parse_original(self, record: dict, *, locked: bool = False):
        # Callers either already hold relay.lock or are the selected sole worker.
        if not locked:
            with self.lock:
                return self._parse_original(record, locked=True)
        private = self.originals[record['relay_id']]
        o = record['original']
        if not private['parsed_ready']:
            # Parsing belongs to the retained original, not to a successful
            # browser join. Failed parses also consume their allowance once.
            if private['parse_error'] == 'utf8':
                raise UnicodeError('E05 invalid original UTF8')
            if private['parse_error'] is not None:
                raise ValueError({'framing': 'E05 identity UTF8 JSON original required',
                                  'capacity': 'E05 parsed copy capacity refused'}.get(
                                      private['parse_error'], 'E05 original parse refused'))
            f = o['framing']
            if f['content_encoding'] not in ([], ['identity']) or len(f['content_type']) != 1 or f['content_type'][0].split(';')[0] != 'application/json':
                private['parse_error'] = 'framing'
                raise ValueError('E05 identity UTF8 JSON original required')
            # Every JSON node/key requires a delimiter or the one root slot.
            # Counting raw delimiters over-reserves punctuation inside strings,
            # but bounds retained parser facts BEFORE json.loads allocates them.
            parse_facts = 1 + sum(private['data'].count(bytes([c])) for c in b'{}[],:')
            if not self._charge(record, len(private['data']), facts=parse_facts):
                private['parse_error'] = 'capacity'
                raise ValueError('E05 parsed copy capacity refused')
            def pairs(items):
                value = {}
                for k,v in items:
                    if k in value:
                        raise ValueError('E05 duplicate original JSON key')
                    value[k] = v
                return value
            def constant(_value):
                raise ValueError('E05 nonfinite original JSON')
            try:
                private['parsed'] = json.loads(private['data'].decode('utf8'), object_pairs_hook=pairs, parse_constant=constant)
            except (ValueError, UnicodeError, RecursionError) as exc:
                private['parse_error'] = 'utf8' if isinstance(exc, UnicodeError) else 'json'
                raise
            private['parsed_ready'] = True
        return private['parsed']

    def original_for(self, request_id: int) -> dict:
        # Full-window reconciliation is recomputed, never a sticky early URL
        # singleton. Unknown/overlapping intervals refuse rather than guessing.
        with self.lock:
            issued = [e for e in self.browser_requests if e['request_id'] == request_id]
            if len(issued) != 1:
                raise ValueError('E05 original Request identity unavailable')
            event = issued[0]
            def candidates(e):
                terminals = e.get('terminals', [])
                if len(terminals) != 1 or terminals[0]['event'] != 'request-finished':
                    return []
                return [r for r in self.records if 'original' in r and r['method'] == e['method']
                        and r['target'] == e['target'] and r['entered'] <= terminals[0]['at']
                        and r.get('closed', float('inf')) >= e['at']]
            matches = candidates(event)
            if (len(matches) != 1 or sum(matches[0] in candidates(e) for e in self.browser_requests) != 1
                    or event.get('document_request_id') is None or event.get('pending_documents')):
                raise ValueError('E05 original complete-window/document join ambiguous')
            record = matches[0]
            o = record['original']
            if o['capture_state'] != 'sealed' or o['outcome'] != 'complete' or o['status'] != 200:
                raise ValueError('E05 complete original JSON unavailable')
            private = self.originals[record['relay_id']]
            self._parse_original(record, locked=True)
            if not self._publishable_body(private['parsed']):
                raise ValueError('E05 original public-field selection refused')
            return dict(relay_id=record['relay_id'], request_id=request_id, origin='relay-original',
                        body=private['parsed'], bytes=o['byte_length'], sha256=o['sha256'])

    def snapshot(self, sanitize: Callable[[str], str]) -> list[dict]:
        with self.lock:
            # Snapshot metadata is bounded and reserved before copying; a failed
            # reservation exports an incomplete census plus the sticky refusal.
            facts = (len(self.records)+len(self.browser_requests)+sum(len(e.get('terminals',[])) for e in self.browser_requests)
                     +sum(len(r.get('forwarding',{}).get('segments',[])) for r in self.records))
            if not self._charge(None, facts=facts):
                return []
            records = copy.deepcopy(self.records)
            for record in records:
                if 'original' not in record:
                    continue
                o = record['original']
                source = self.records[record['relay_id']]
                private = self.originals.get(record['relay_id'])
                entity = dict(representation='unavailable', text=None, copy_bytes=None, copy_sha256=None,
                              original_byte_check='unavailable')
                if 'response' in record and not self._publishable_body(record['response']):
                    record['response'] = None
                publishable = False
                if private is not None and o['capture_state'] == 'sealed':
                    if o['outcome'] in {'empty', 'bodyless'} and not private['data']:
                        publishable = True  # Zero original bytes, never JSON success.
                    elif o['outcome'] == 'complete':
                        try:
                            parsed = self._parse_original(source, locked=True)
                        except (ValueError, UnicodeError, RecursionError):
                            pass  # Retain the original and explicit unavailable copy.
                        else:
                            publishable = self._publishable_body(parsed)
                if publishable and self._charge(source, len(private['data'])):
                    try:
                        text = private['data'].decode('utf8')
                    except UnicodeError:
                        pass
                    else:
                        safe = sanitize(text)
                        if self._charge(source, max(0,len(safe.encode('utf8'))-len(private['data'])), facts=0):
                            entity.update(representation='unchanged_utf8' if safe == text else 'sanitized_utf8', text=safe,
                                copy_bytes=len(safe.encode('utf8')), copy_sha256=hashlib.sha256(safe.encode('utf8')).hexdigest(),
                                original_byte_check='available' if safe == text else 'unavailable')
                # Parsing/copy reservation can refuse after the initial metadata
                # snapshot. Export that sticky refusal in this SAME snapshot.
                o.update(capture_state=source['original']['capture_state'],
                         capture_refusal=copy.deepcopy(source['original']['capture_refusal']))
                o['entity_copy'] = entity
            return records

    @staticmethod
    def _response_facts(facts: dict, method: str) -> bool:
        if (set(facts) != {'status', 'transfer_encoding', 'content_length', 'content_type', 'chunked', 'length'}
                or type(facts['status']) is not int or not 200 <= facts['status'] <= 599
                or type(facts['chunked']) is not bool
                or any(type(facts[k]) is not list or len(facts[k]) > 1
                       or any(type(v) is not str for v in facts[k])
                       for k in ('transfer_encoding', 'content_length', 'content_type'))):
            return False
        transfer, lengths = facts['transfer_encoding'], facts['content_length']
        if (transfer and (transfer[0].strip().lower() != 'chunked' or lengths)
                or lengths and (not lengths[0].isascii() or not lengths[0].isdecimal())
                or facts['chunked'] != bool(transfer)):
            return False
        expected = 0 if method == 'HEAD' or facts['status'] in {204, 304} else int(lengths[0]) if lengths else None
        return (facts['length'] is None if expected is None else
                type(facts['length']) is int and facts['length'] == expected)

    @staticmethod
    def _read_facts(facts: dict, response: dict, streaming: bool, *, whole: bool = False) -> bool:
        if (set(facts) != {'bytes', 'remaining_before', 'remaining_after', 'closed', 'eof'}
                or type(facts['bytes']) is not int or facts['bytes'] < 0
                or type(facts['closed']) is not bool or type(facts['eof']) is not bool
                or facts['eof'] != (facts['bytes'] == 0)
                or any(v is not None and (type(v) is not int or v < 0)
                       for v in (facts['remaining_before'], facts['remaining_after']))):
            return False
        before, after = facts['remaining_before'], facts['remaining_after']
        initial = response['length']
        if (initial is None and (before is not None or after is not None)
                or initial is not None and (before is None or after is None
                                           or before > initial or after != before-facts['bytes'])
                or (facts['closed'] or facts['eof']) and after not in (None, 0)
                or after == 0 and not facts['closed']
                or facts['eof'] and not facts['closed']
                or streaming and (facts['closed'] or facts['eof'])
                or whole and (not facts['closed'] or after not in (None, 0))):
            return False
        return True

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
            owned_sockets = set()
            for owner in self.io_owners.values():
                owner.state['forced'] = time.monotonic()
                owner.wake()
                owned_sockets.update(owner.handles.get(name) for name in ('original','duplicate'))
            owned_sockets.discard(None)
            connections, workers = list(self.connections), list(self.workers)
        for connection in connections:
            sock = connection.sock if isinstance(connection, http.client.HTTPConnection) else connection
            if sock:
                try:
                    if sock.fileno() >= 0:
                        sock.shutdown(socket.SHUT_RDWR)
                except OSError as exc:
                    if sock in owned_sockets:
                        self.errors.append(type(exc).__name__ + ': browser cleanup shutdown failed')
                # The request worker unregisters selector interests and closes
                # its descriptors. Forced cleanup wakes it without permitting
                # close/reuse of a descriptor still registered by that owner.
                if sock in owned_sockets:
                    continue
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
            owner = record.get('io_owner')
            intent, ack = record.get('stop_intent'), record.get('stop_ack')
            expected = (failure == 'Stopped' and record['method'] == 'GET' and record['ordinal'] is None
                        and owner is not None and intent is not None and ack is not None
                        and owner['forced'] is None and not owner['errors']
                        and ack.get('outcome') in {'shutdown', 'already-complete'} and 'error' not in ack
                        and owner['operations'][-1].get('outcome') == 'cancelled'
                        and intent['enqueued'] <= ack['started'] <= ack['finished'] <= record['failure_at']
                        <= owner['finished'] <= record['closed'] <= settled
                        and (intent['kind'] != 'context' or self.context_closed is not None
                             and record['stop_settled'] <= self.context_closed['at']))
            if expected:
                issued = [e for e in self.browser_requests if e['request_id'] == intent['request_id']]
                expected = len(issued) == 1
                if expected and intent['kind'] == 'context':
                    # A callback may have captured its native event before
                    # waiting for relay.lock. Preserve that original observation
                    # if its publication follows the worker's locked recheck.
                    expected = not any(t['at'] <= ack['started'] for t in issued[0].get('terminals', []))
                elif expected:
                    trigger = dict(intent['event'])
                    trigger.pop('target')
                    expected = issued[0].get('terminals') == [trigger]
            if not expected:
                errors.append(failure + ': browser transport failed')
        with self.lock:
            # Observe late-entering request owners and their pre-registered
            # downstream sockets in ONE snapshot after listener admission ends.
            result = dict(port=self.port, entered=entered, finished=time.monotonic(),
                          workers_alive=sum(w.is_alive() for w in self.workers), listener_alive=self.thread.is_alive(),
                          connections=len(self.connections), errors=errors)
        if result["workers_alive"] or result["listener_alive"] or result["connections"]:
            errors.append("browser proxy ownership did not close")
        with self.lock:
            self.capture_closed = time.monotonic()
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

"""Closed root-to-connector protocol; slice-2 binding is deliberately separate.

OperationAPI implementations must hold the SAME trust-store mutation lock across
status/revalidation and the operation, including revocation's stream closure.
No credentials, invitation material or raw exceptions cross this protocol.
"""
from __future__ import annotations

import hashlib
import html
import json
import math
import socket
import struct
import time
from dataclasses import asdict, dataclass
from typing import Callable, ContextManager, Protocol

CONTROL_SOCKET = '/run/happyranch-connector/control.sock'
MAX_FRAME = 32768
MAX_DEVICES = 32
MUTATIONS = {'ApprovePending': 'approve-pending', 'RejectPending': 'reject-pending',
             'RevokeSelected': 'revoke-selected'}
FAILURES = frozenset({'invalid-request', 'unknown-method', 'wrong-uid', 'unavailable',
                      'busy', 'denied', 'expired', 'changed', 'disconnected'})


class Refused(Exception):
    def __init__(self, category: str) -> None:
        self.category = category if category in FAILURES else 'unavailable'
        super().__init__(self.category)


def plain(value: str) -> str:
    """Bound controls/bidi and escape markup from protected device metadata."""
    return html.escape(''.join(c for c in value[:96] if c.isprintable()), quote=True)


@dataclass(frozen=True)
class Device:
    record_id: str
    stable_id: str
    label: str
    comparison_phrase: str
    expires_at: float

    def public(self) -> dict:
        return {'label': plain(self.label), 'fingerprint': hashlib.sha256(
            self.stable_id.encode()).hexdigest()[:24], 'comparison_phrase': plain(self.comparison_phrase)}


@dataclass(frozen=True)
class Snapshot:
    generation: int
    release_uid: int
    owned: bool
    phase: str
    ready: bool
    pending: Device | None
    devices: tuple[Device, ...]


class OperationAPI(Protocol):
    """Serial integration binds status and the three slice-2 operations only."""
    def mutation_lock(self) -> ContextManager[None]: ...
    def status(self) -> Snapshot: ...
    def approve_pending(self, record: Device, generation: int) -> None: ...
    def reject_pending(self, record: Device, generation: int) -> None: ...
    def revoke_device(self, record: Device, generation: int) -> None: ...


def _device(value: object) -> Device:
    if not isinstance(value, dict) or set(value) != set(Device.__dataclass_fields__):
        raise Refused('invalid-request')
    for key in ('record_id', 'stable_id', 'label', 'comparison_phrase'):
        if not isinstance(value[key], str) or not 0 < len(value[key]) <= 256:
            raise Refused('invalid-request')
    expiry = value['expires_at']
    if type(expiry) not in (int, float) or not math.isfinite(expiry) or expiry <= 0:
        raise Refused('invalid-request')
    return Device(**value)


def snapshot_from(value: object) -> Snapshot:
    if not isinstance(value, dict) or set(value) != set(Snapshot.__dataclass_fields__):
        raise Refused('invalid-request')
    if (type(value['generation']) is not int or value['generation'] < 0
            or type(value['release_uid']) is not int or value['release_uid'] < 0
            or type(value['owned']) is not bool or type(value['ready']) is not bool
            or value['phase'] not in ('off', 'pending', 'ready', 'paused', 'failed')
            or not isinstance(value['devices'], list) or len(value['devices']) > MAX_DEVICES):
        raise Refused('invalid-request')
    devices = tuple(_device(d) for d in value['devices'])
    if len({d.record_id for d in devices}) != len(devices):
        raise Refused('invalid-request')
    return Snapshot(value['generation'], value['release_uid'], value['owned'], value['phase'],
                    value['ready'], _device(value['pending']) if value['pending'] is not None else None, devices)


def _decode(raw: bytes) -> dict:
    def unique(pairs: list) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise Refused('invalid-request')
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise Refused('invalid-request')
    return value


def _receive(connection: socket.socket) -> dict:
    raw = bytearray()
    while len(raw) <= MAX_FRAME:
        data, ancillary, flags, _ = connection.recvmsg(min(4096, MAX_FRAME + 1 - len(raw)), 256)
        if ancillary or flags & (socket.MSG_CTRUNC | socket.MSG_TRUNC):
            # Close any received descriptors even on a refused request.
            for level, kind, content in ancillary:
                if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                    import os
                    for fd in struct.unpack(f'{len(content) // 4}i', content):
                        os.close(fd)
            raise Refused('invalid-request')
        if not data:
            raise Refused('invalid-request')
        raw.extend(data)
        if b'\n' in raw:
            if not raw.endswith(b'\n') or raw.count(b'\n') != 1 or len(raw) > MAX_FRAME:
                raise Refused('invalid-request')
            return _decode(bytes(raw))
    raise Refused('invalid-request')


def _send(connection: socket.socket, value: dict) -> None:
    raw = json.dumps(value, separators=(',', ':'), allow_nan=False).encode() + b'\n'
    if len(raw) > MAX_FRAME:
        raise Refused('unavailable')
    connection.sendall(raw)


class ControlHandler:
    def __init__(self, operations: OperationAPI) -> None:
        self.operations = operations

    def handle(self, connection: socket.socket) -> None:
        """One accepted Unix stream; root credential check precedes even parsing."""
        connection.settimeout(2)
        try:
            _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != 0:
                raise Refused('wrong-uid')
            request = _receive(connection)
            with self.operations.mutation_lock():
                current = snapshot_from(json.loads(json.dumps(asdict(self.operations.status()))))
                method = request.get('method')
                if method == 'status' and set(request) == {'method'}:
                    response = {'snapshot': asdict(current)}
                elif method in MUTATIONS.values() and set(request) == {'method', 'snapshot', 'record', 'deadline'}:
                    captured, record = snapshot_from(request['snapshot']), _device(request['record'])
                    deadline = request['deadline']
                    if type(deadline) not in (int, float) or not math.isfinite(deadline):
                        raise Refused('invalid-request')
                    now = time.monotonic()
                    if deadline <= now:
                        raise Refused('expired')
                    if deadline > now + 120:
                        raise Refused('invalid-request')
                    target = (current.pending if method != 'revoke-selected' else
                              next((d for d in current.devices if d.record_id == record.record_id), None))
                    if not current.owned or current != captured or target != record:
                        raise Refused('changed')
                    if record.expires_at <= time.time():
                        raise Refused('expired')
                    operation = {'approve-pending': self.operations.approve_pending,
                                 'reject-pending': self.operations.reject_pending,
                                 'revoke-selected': self.operations.revoke_device}[method]
                    # Hold the store lock while root revalidates its live bus caller.
                    _send(connection, {'result': 'ready'})
                    commit = _receive(connection)
                    if set(commit) != {'commit'} or commit['commit'] is not True:
                        raise Refused('invalid-request')
                    if time.monotonic() >= deadline:
                        raise Refused('expired')
                    if self.operations.status() != captured:
                        raise Refused('changed')
                    if record.expires_at <= time.time():
                        raise Refused('expired')
                    operation(record, captured.generation)
                    response = {'result': 'ok'}
                else:
                    raise Refused('invalid-request')
            _send(connection, response)
        except Exception as error:
            category = error.category if isinstance(error, Refused) else 'unavailable'
            try:
                _send(connection, {'error': category})
            except Exception:
                pass


class ControlClient:
    def __init__(self, path: str = CONTROL_SOCKET) -> None:
        self.path = path

    def _call(self, request: dict, before_commit: Callable[[], None] | None = None) -> dict:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(2)
                connection.connect(self.path)
                _send(connection, request)
                reply = _receive(connection)
                if before_commit is not None and reply == {'result': 'ready'}:
                    before_commit()
                    _send(connection, {'commit': True})
                    reply = _receive(connection)
                if 'error' in reply:
                    raise Refused(reply['error'])
                return reply
        except Refused:
            raise
        except Exception:
            raise Refused('unavailable') from None

    def status(self) -> Snapshot:
        return snapshot_from(self._call({'method': 'status'}).get('snapshot'))

    def mutate(self, method: str, snapshot: Snapshot, record: Device, deadline: float,
               before_commit: Callable[[], None]) -> None:
        if method not in MUTATIONS:
            raise Refused('unknown-method')
        reply = self._call({'method': MUTATIONS[method], 'snapshot': asdict(snapshot), 'record': asdict(record),
                            'deadline': deadline}, before_commit)
        if reply != {'result': 'ok'}:
            raise Refused('unavailable')

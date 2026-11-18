"""Closed root-to-connector protocol and private connector listener.

OperationAPI implementations must hold the SAME manager mutation lock across
status/revalidation and the operation, including revocation's stream closure.
The file lock is used only for coherent reads and actual mutation admission.
No credentials, invitation material or raw exceptions cross this protocol.
"""
from __future__ import annotations

import hashlib
import html
import json
import math
import os
import socket
import stat
import struct
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, ContextManager, Protocol, runtime_checkable

CONTROL_SOCKET = '/run/happyranch-connector/control.sock'
MAX_FRAME = 32768
MAX_DEVICES = 32
MUTATIONS = {'ApprovePending': 'approve-pending', 'RejectPending': 'reject-pending',
             'RevokeSelected': 'revoke-selected'}
INVITE_MUTATIONS = frozenset({'invite-reserve@1', 'invite-invalidate@1', 'revoke-all@1'})
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
    # Pending-request expiry vetoes approval/rejection. Paired-credential
    # expiry never vetoes revocation of an existing grant or its live streams.
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


@runtime_checkable
class InviteOperationAPI(Protocol):
    """Optional B10 capability, bound to the SAME serving pairing owner.

    Each method performs anchored generation CAS and returns its committed
    generation. Existing OperationAPI implementations remain valid without it.
    """
    def reserve_invite(self, generation: int, digest: str, expires_at: int) -> int: ...
    def invalidate_invite(self, generation: int) -> int: ...
    def revoke_all(self, generation: int) -> int: ...


def _invite_request(request: dict) -> tuple[int, float]:
    keys = {'method', 'expected_generation', 'deadline'}
    if request['method'] == 'invite-reserve@1':
        keys |= {'digest', 'expires_at'}
    if set(request) != keys:
        raise Refused('invalid-request')
    generation, deadline = request['expected_generation'], request['deadline']
    if (type(generation) is not int or generation < 0
            or type(deadline) not in (int, float) or not math.isfinite(deadline)):
        raise Refused('invalid-request')
    now = time.monotonic()
    if deadline <= now:
        raise Refused('expired')
    if deadline > now + 120:
        raise Refused('invalid-request')
    if request['method'] == 'invite-reserve@1':
        digest, expiry = request['digest'], request['expires_at']
        if (not isinstance(digest, str) or len(digest) != 64
                or any(c not in '0123456789abcdef' for c in digest)
                or type(expiry) is not int):
            raise Refused('invalid-request')
        now = time.time()
        if expiry <= now:
            raise Refused('expired')
        if expiry > now + 300:
            raise Refused('invalid-request')
    return generation, deadline


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
                    if method != 'revoke-selected' and record.expires_at <= time.time():
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
                    if method != 'revoke-selected' and record.expires_at <= time.time():
                        raise Refused('expired')
                    if time.monotonic() >= deadline:
                        raise Refused('expired')
                    operation(record, captured.generation)
                    response = {'result': 'ok'}
                elif isinstance(method, str) and method in INVITE_MUTATIONS:
                    generation, deadline = _invite_request(request)
                    if not isinstance(self.operations, InviteOperationAPI):
                        raise Refused('unavailable')
                    if not current.owned or current.generation != generation:
                        raise Refused('changed')
                    # Only the manager RLock spans IPC, never a trust-file flock.
                    _send(connection, {'result': 'ready'})
                    commit = _receive(connection)
                    if set(commit) != {'commit'} or commit['commit'] is not True:
                        raise Refused('invalid-request')
                    if time.monotonic() >= deadline:
                        raise Refused('expired')
                    if self.operations.status() != current:
                        raise Refused('changed')
                    # Status can block. Recheck all time bounds immediately
                    # before the actual operation's anchored CAS admission.
                    _invite_request(request)
                    if method == 'invite-reserve@1':
                        committed = self.operations.reserve_invite(generation, request['digest'], request['expires_at'])
                    elif method == 'invite-invalidate@1':
                        committed = self.operations.invalidate_invite(generation)
                    else:
                        committed = self.operations.revoke_all(generation)
                    if type(committed) is not int or committed <= generation:
                        raise Refused('unavailable')
                    # Needed for subsequent invalidate/revoke CAS continuity;
                    # never reflect operation input or ceremony state.
                    response = {'result': 'ok', 'generation': committed}
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

    def _call(self, request: dict, before_commit: Callable[[], None] | None = None,
              *, unavailable_on_loss: bool = False) -> dict:
        def receive(connection: socket.socket) -> dict:
            try:
                return _receive(connection)
            except Exception:
                if unavailable_on_loss:
                    raise Refused('unavailable') from None
                raise
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(2)
                connection.connect(self.path)
                _send(connection, request)
                reply = receive(connection)
                if before_commit is not None and reply == {'result': 'ready'}:
                    before_commit()
                    _send(connection, {'commit': True})
                    reply = receive(connection)
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

    def _invite_mutate(self, request: dict, before_commit: Callable[[], None]) -> int:
        # Refuse malformed digest/credential-like input before opening IPC.
        _invite_request(request)
        reply = self._call(request, before_commit, unavailable_on_loss=True)
        if (set(reply) != {'result', 'generation'} or reply['result'] != 'ok'
                or type(reply['generation']) is not int
                or reply['generation'] <= request['expected_generation']):
            raise Refused('unavailable')
        return reply['generation']

    def reserve_invite(self, generation: int, digest: str, expires_at: int, deadline: float,
                       before_commit: Callable[[], None]) -> int:
        """A lost reply is UNCERTAIN, never 'no change'; caller needs B11 recovery."""
        return self._invite_mutate({'method': 'invite-reserve@1', 'expected_generation': generation,
                                    'digest': digest, 'expires_at': expires_at, 'deadline': deadline}, before_commit)

    def invalidate_invite(self, generation: int, deadline: float, before_commit: Callable[[], None]) -> int:
        return self._invite_mutate({'method': 'invite-invalidate@1', 'expected_generation': generation,
                                    'deadline': deadline}, before_commit)

    def revoke_all(self, generation: int, deadline: float, before_commit: Callable[[], None]) -> int:
        return self._invite_mutate({'method': 'revoke-all@1', 'expected_generation': generation,
                                    'deadline': deadline}, before_commit)


class ControlListener:
    """Supervisor-owned AF_UNIX listener; only kernel UID 0 reaches operations.

    The fixed private parent belongs to the service UID. Bind/unlink relative
    to its pinned directory FD, refusing existing entries rather than repairing
    or deleting them. No network endpoint, environment or caller path input.
    """

    def __init__(self, operations: OperationAPI) -> None:
        self.handler = ControlHandler(operations)
        self._stopped = threading.Event()
        self._active_lock = threading.Lock()
        self._active: socket.socket | None = None
        self._parent = -1
        self._identity: tuple[int, int] | None = None
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._thread: threading.Thread | None = None
        path = Path(CONTROL_SOCKET)
        try:
            if not path.is_absolute() or '..' in path.parts:
                raise Refused('unavailable')
            self._name = path.name
            self._parent = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            for part in path.parts[1:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                dir_fd=self._parent)
                os.close(self._parent)
                self._parent = child
            info = os.fstat(self._parent)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise Refused('unavailable')
            # Linux /proc resolves this exact opened parent, even if a literal
            # ancestor moves after validation. bind refuses ANY existing leaf.
            self._socket.bind(f'/proc/self/fd/{self._parent}/{self._name}')
            info = os.stat(self._name, dir_fd=self._parent, follow_symlinks=False)
            self._identity = (info.st_dev, info.st_ino)
            os.chmod(self._name, 0o600, dir_fd=self._parent)
            self._socket.listen(4)
            self._socket.settimeout(.1)
            self._thread = threading.Thread(target=self._serve, daemon=True, name='remote-access-control')
            self._thread.start()
        except Exception:
            self.close()
            raise Refused('unavailable') from None

    @property
    def listening(self) -> bool:
        return not self._stopped.is_set() and self._thread is not None and self._thread.is_alive()

    def _serve(self) -> None:
        while not self._stopped.is_set():
            try:
                connection, _ = self._socket.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with connection:
                with self._active_lock:
                    if self._stopped.is_set():
                        return
                    self._active = connection
                try:
                    self.handler.handle(connection)
                finally:
                    with self._active_lock:
                        self._active = None

    def close(self) -> None:
        self._stopped.set()
        self._socket.close()
        with self._active_lock:
            if self._active is not None:
                try:
                    self._active.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        if self._thread is not None:
            self._thread.join(3)
            if self._thread.is_alive():
                raise Refused('unavailable')
        if self._parent >= 0:
            try:
                if self._identity is not None:
                    info = os.stat(self._name, dir_fd=self._parent, follow_symlinks=False)
                    if (info.st_dev, info.st_ino) == self._identity:
                        os.unlink(self._name, dir_fd=self._parent)
            except FileNotFoundError:
                pass
            finally:
                os.close(self._parent)
                self._parent = -1

"""TEST-only scoped Services admission; no account or daemon credential custody."""
from __future__ import annotations

import base64
import fcntl
import json
import math
import os
import re
import ssl
import stat
import time
import threading
import socket
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from types import MappingProxyType
from urllib.parse import urlsplit

from .home_invite_records import INVITE_USER
from .home_minter import HeadscaleJoinKeyMinter, MintUncertain, PreAuthKey
from .home_admin import RootHeadscaleAdminOperations, DurableOperationRecordStore, _directory as admin_directory
from .invite_issuer import MintedKey, MintRefused, _directory, _metadata, _pairs
from .invite_recovery import HeadscaleRecoveryMinter

CONFIG = Path('/etc/happyranch/account-invite/config.json')
CREDENTIAL = CONFIG.with_name('binding.credential')
SERVICES_CA = CONFIG.with_name('services-ca.pem')
STATE = Path('/var/lib/happyranch-remote-access/account-invite')


class MetadataAbsent(Exception):
    """Only a safely absent operation leaf, never a missing/unsafe parent."""


def signature(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def private_read(path: Path, *, uid: int, limit: int, private_parent: bool = True) -> bytes:
    """One bounded no-follow read; pin all literal parents and leaf metadata."""
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('custody')
    descriptors, parents = [], []
    try:
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        descriptors.append(fd)
        for part in path.parts[1:-1]:
            before = os.stat(part, dir_fd=fd, follow_symlinks=False)
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            if signature(os.fstat(child)) != signature(before):
                os.close(child)
                raise ValueError('custody')
            parents.append((fd, part, signature(before)))
            fd = child
            descriptors.append(fd)
            if uid == 0 and (before.st_uid != 0 or before.st_gid != 0 or before.st_mode & 0o022):
                raise ValueError('custody')
        parent = os.fstat(fd)
        if private_parent and (parent.st_uid != uid or stat.S_IMODE(parent.st_mode) != 0o700):
            raise ValueError('custody')
        leaf = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
        descriptors.append(leaf)
        before = os.fstat(leaf)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_uid != uid
                or (uid == 0 and before.st_gid != 0) or stat.S_IMODE(before.st_mode) != 0o600
                or not 0 < before.st_size <= limit
                or signature(os.stat(path.name, dir_fd=fd, follow_symlinks=False)) != signature(before)):
            raise ValueError('custody')
        data = os.read(leaf, limit + 1)
        if (len(data) != before.st_size or signature(os.fstat(leaf)) != signature(before)
                or signature(os.stat(path.name, dir_fd=fd, follow_symlinks=False)) != signature(before)
                or any(signature(os.stat(name, dir_fd=p, follow_symlinks=False)) != observed
                       for p, name, observed in parents)):
            raise ValueError('custody')
        return data
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def opaque(value: object, prefix: str) -> str:
    if type(value) is not str or re.fullmatch(prefix + '_[a-z2-7]{26}', value) is None:
        raise ValueError('identifier')
    raw = base64.b32decode(value[len(prefix) + 1:].upper() + '======')
    if not any(raw) or prefix + '_' + base64.b32encode(raw).decode().lower().rstrip('=') != value:
        raise ValueError('identifier')
    return value


def secret(value: object) -> str:
    if type(value) is not str or re.fullmatch('[A-Za-z0-9_-]{43}', value) is None:
        raise ValueError('credential')
    if base64.urlsafe_b64encode(base64.urlsafe_b64decode(value + '=')).decode().rstrip('=') != value:
        raise ValueError('credential')
    return value


def check(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if not math.isfinite(remaining) or not 0 < remaining <= 120:
        raise MintUncertain()
    return min(5.0, remaining)


@dataclass(frozen=True, repr=False)
class ServicesClient:
    origin: str
    binding_id: str
    credential: str = field(repr=False)
    ca_pem: str = field(repr=False)

    def __post_init__(self) -> None:
        target = urlsplit(self.origin)
        if (type(self.origin) is not str or target.scheme != 'https' or not target.hostname
                or target.username or target.password or target.path or target.query or target.fragment
                or target.port == 0 or '\\' in self.origin or any(c.isspace() for c in self.origin)):
            raise ValueError('configuration')
        opaque(self.binding_id, 'bind')
        secret(self.credential)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cadata=self.ca_pem)

    def post(self, verb: str, body: dict, deadline: float) -> dict:
        # Loaded only when this TEST transport is used, not during helper import.
        import http.client
        if verb not in ('admit', 'settle', 'observe'):
            raise MintUncertain()
        target = urlsplit(self.origin)
        if (target.scheme != 'https' or not target.hostname or target.username or target.password
                or target.path or target.query or target.fragment):
            raise MintUncertain()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cadata=self.ca_pem)
        with closing(http.client.HTTPSConnection(target.hostname, target.port or 443,
                                                timeout=check(deadline), context=context)) as connection:
            active_socket: list[socket.socket] = []
            def expire() -> None:
                if active_socket:
                    try:
                        active_socket[0].shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
            timer = threading.Timer(deadline - time.monotonic(), expire)
            timer.daemon = True
            timer.start()
            try:
                connection.connect()
                active_socket.append(connection.sock)
                check(deadline)
                connection.request('POST', '/internal/remote/enrollments/' + verb,
                    json.dumps(body, separators=(',', ':')).encode(),
                    {'Authorization': 'Bearer ' + self.credential, 'X-HR-Binding': self.binding_id,
                     'Content-Type': 'application/json'})
                connection.sock.settimeout(check(deadline))
                with closing(connection.getresponse()) as response:
                    data = response.read(4097)
                    check(deadline)
                    if response.status != 200:
                        if verb == 'admit':
                            raise MintRefused('refused')
                        raise MintUncertain()
                    if len(data) > 4096:
                        raise MintUncertain()
                    return json.loads(data, object_pairs_hook=_pairs)
            finally:
                timer.cancel()


class _BoundedAdmin:
    def __init__(self, deadline: float, before: Callable[[], None]) -> None:
        self.admin, self.deadline, self.before = RootHeadscaleAdminOperations(), deadline, before
        self.created: tuple[str, datetime] | None = None

    def _call(self, name: str, kwargs: dict) -> object:
        self.before()
        kwargs['timeout_s'] = min(kwargs['timeout_s'], check(self.deadline))
        value = getattr(self.admin, name)(**kwargs)
        check(self.deadline)
        self.before()
        return value

    def create_pre_auth_key(self, *, user: str, reusable: bool, ephemeral: bool,
                            expiration: datetime, timeout_s: float) -> PreAuthKey:
        result = self._call('create_pre_auth_key', dict(user=user, reusable=reusable,
                            ephemeral=ephemeral, expiration=expiration, timeout_s=timeout_s))
        self.created = (result.id, result.expiration)
        return result

    def list_pre_auth_keys(self, *, user: str, timeout_s: float) -> tuple[PreAuthKey, ...]:
        return self._call('list_pre_auth_keys', dict(user=user, timeout_s=timeout_s))

    def expire_pre_auth_key(self, *, user: str, key: str, timeout_s: float) -> None:
        self._call('expire_pre_auth_key', dict(user=user, key=key, timeout_s=timeout_s))


class AccountEnrollmentMinter:
    """One attempt, metadata-only journal, positive original ack before create."""
    def __init__(self, *, client: ServicesClient, permit: MappingProxyType | None, state_dir: Path,
                 deadline: float, before_effect: Callable[[], None]) -> None:
        self.client, self.permit, self.state_dir = client, permit, state_dir
        self.deadline, self.before = deadline, before_effect

    def _record(self, operation: str, value: dict | None = None) -> dict:
        if re.fullmatch('[0-9a-f]{32}', operation) is None:
            raise MintUncertain()
        name = 'account-' + operation + '.json'
        with _directory(self.state_dir) as parent:
            if value is not None:
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                with os.fdopen(fd, 'wb') as stream:
                    _metadata(stream.fileno(), parent, name)
                    stream.write(json.dumps(value, separators=(',', ':')).encode())
                    stream.flush()
                    os.fsync(stream.fileno())
                os.fsync(parent)
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            except FileNotFoundError:
                raise MetadataAbsent() from None
            with os.fdopen(fd, 'rb') as stream:
                _metadata(stream.fileno(), parent, name)
                before = signature(os.fstat(stream.fileno()))
                raw = stream.read(1025)
                _metadata(stream.fileno(), parent, name)
                if len(raw) > 1024 or signature(os.fstat(stream.fileno())) != before:
                    raise MintUncertain()
        result = json.loads(raw, object_pairs_hook=_pairs)
        if (type(result) is not dict or result.keys() != {'binding_id', 'enrollment_id', 'operation_id'}
                or result['binding_id'] != self.client.binding_id
                or result['operation_id'] != 'op_' + base64.b32encode(bytes.fromhex(operation)).decode().lower().rstrip('=')):
            raise MintUncertain()
        opaque(result['enrollment_id'], 'enr')
        return result

    def _ack(self, value: dict, record: dict, state: str, *, mint: bool = False) -> datetime:
        if (type(value) is not dict or value.keys() != {*record, 'state', 'mint_allowed', 'key_expires_at'}
                or any(value[k] != v for k, v in record.items())
                or value['state'] != state or value['mint_allowed'] is not mint):
            raise MintUncertain()
        expiry = datetime.fromisoformat(value['key_expires_at'].replace('Z', '+00:00'))
        if expiry.tzinfo is None or expiry.utcoffset().total_seconds() != 0:
            raise MintUncertain()
        return expiry

    def mint(self, operation_id: str, expires_at: datetime) -> MintedKey:
        # The issuer has fsynced minting and received its initial B10 ack.
        uncertain, refused, admitted = False, False, False
        try:
            self.before()
            check(self.deadline)
            if self.permit is None:
                raise MintRefused('refused')
            record = dict(binding_id=self.client.binding_id, enrollment_id=self.permit['enrollment_id'],
                          operation_id='op_' + base64.b32encode(bytes.fromhex(operation_id)).decode().lower().rstrip('='))
            self._record(operation_id, record)  # BEFORE admission can commit remotely.
            body = {k: v for k, v in record.items() if k != 'binding_id'}
            body.update(permit=self.permit['permit'], verifier=self.permit['verifier'],
                        requested_expiry=expires_at.isoformat())
            ack = self.client.post('admit', body, self.deadline)
            expiry = self._ack(ack, record, 'admitted', mint=True)
            admitted = True
            check(self.deadline)
            self.before()
            if not datetime.now(timezone.utc) < expiry <= expires_at:
                raise MintUncertain()
            admin = _BoundedAdmin(self.deadline, self.before)
            fd = admin_directory()
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                check(self.deadline)
                minter = HeadscaleJoinKeyMinter(admin=admin, invite_user=INVITE_USER,
                    now_fn=lambda: datetime.now(timezone.utc), records=DurableOperationRecordStore(fd))
                minted = minter.mint(operation_id, expiry)
            finally:
                os.close(fd)
            if admin.created is None:
                raise MintUncertain()
            provider, actual = admin.created
            result = self.client.post('settle', {**body_from(record), 'outcome': 'issued',
                'provider_key_id': provider, 'actual_expiry': actual.isoformat()}, self.deadline)
            if self._ack(result, record, 'issued') != actual:
                raise MintUncertain()
            self.before()
            check(self.deadline)
            return minted
        except MintRefused:
            refused = True
            try:
                if admitted:
                    result = self.client.post('settle', {**body_from(record), 'outcome': 'refused',
                        'provider_key_id': '', 'actual_expiry': ''}, self.deadline)
                    self._ack(result, record, 'refused')
                with _directory(self.state_dir) as parent:
                    fd = os.open('account-' + operation_id + '.refused',
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                    with os.fdopen(fd, 'wb') as stream:
                        stream.write(b'1')
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.fsync(parent)
            except BaseException:
                uncertain = True
        except BaseException:
            uncertain = True
        del self, operation_id, expires_at
        if refused and not uncertain:
            raise MintRefused('refused')
        raise MintUncertain()

    def recover(self, operation_id: str, *, deadline: float) -> bool:
        try:
            self.before()
            fd = admin_directory()
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return HeadscaleRecoveryMinter(admin=_BoundedAdmin(deadline, self.before), invite_user=INVITE_USER,
                    now_fn=lambda: datetime.now(timezone.utc), records=DurableOperationRecordStore(fd)
                ).recover(operation_id, deadline=deadline)
            finally:
                os.close(fd)
        except BaseException:
            return False

    def settle_recovery(self, operation_id: str) -> None:
        try:
            record = self._record(operation_id)
        except MetadataAbsent:
            return  # Durable mapping precedes dispatch: no admission was attempted.
        try:
            refused = private_read(self.state_dir / ('account-' + operation_id + '.refused'), uid=0, limit=1)
        except FileNotFoundError:
            pass
        else:
            if refused != b'1':
                raise MintUncertain()
            return
        observed = self.client.post('observe', body_from(record), self.deadline)
        state = observed.get('state')
        if state not in ('admitted', 'issued', 'uncertain', 'recovered'):
            raise MintUncertain()
        self._ack(observed, record, state)  # Observation NEVER grants mint permission.
        result = self.client.post('settle', {**body_from(record), 'outcome': 'recovered',
            'provider_key_id': '', 'actual_expiry': ''}, self.deadline)
        self._ack(result, record, 'recovered')


def body_from(record: dict) -> dict:
    return {k: record[k] for k in ('enrollment_id', 'operation_id')}

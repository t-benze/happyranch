"""Inert root-only fixed Headscale runner, durable records and mint serialization."""
from __future__ import annotations

import fcntl
import hashlib
import os
import re
import stat
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Iterator

from runtime.remote_access.headscale_service import INVITE_USER
from runtime.remote_access.home_minter import (
    AdminRefused, CreationWindow, HeadscaleJoinKeyMinter, MintUncertain, PreAuthKey,
)
from runtime.remote_access.invite_issuer import MintedKey, MintRefused
from runtime.remote_access.service_manager import _default_run

_RUNNER = '/opt/happyranch-desktop/libexec/remote-access/happyranch-home-admin'
_STATE = '/var/lib/happyranch-remote-access/home-minter'
_MAX_BYTES = 128 << 20
_KEY = re.compile('[0-9a-f]{48}')
_RECORD_TYPES = {1: 2, 2: 2, 3: 2, 4: 0, 5: 0, 6: 0, 7: 2, 8: 2, 9: 2}


def _deadline(timeout_s: float) -> float:
    if not 0 < timeout_s <= 5:
        raise AdminRefused()
    return time.monotonic() + timeout_s


def _remaining(deadline: float | None) -> float:
    if deadline is None:
        return 0
    remaining = deadline - time.monotonic()
    if not remaining > 0:
        raise MintUncertain()
    return remaining


def _varint(value: int) -> bytes:
    data = bytearray()
    while value >= 128:
        data.append((value & 127) | 128)
        value >>= 7
    data.append(value)
    return bytes(data)


def _bytes(number: int, value: bytes) -> bytes:
    return _varint(number * 8 + 2) + _varint(len(value)) + value


def _take_varint(data: bytes, offset: int) -> tuple[int, int]:
    value, shift = data[offset] & 127, 7
    offset += 1
    while True:
        if offset >= len(data) or shift >= 70:
            raise ValueError('wire')
        b = data[offset]
        offset += 1
        value |= (b & 127) << shift
        if b < 128:
            if b == 0 or (shift == 63 and b > 1):
                raise ValueError('wire')
            return value, offset
        shift += 7


def _fields(data: bytes, deadline: float | None = None) -> Iterator[tuple[int, int, bytes | int]]:
    offset = 0
    size = len(data)
    checkpoint = 0
    while offset < size:
        if offset >= checkpoint:
            _remaining(deadline)
            checkpoint = offset + 4096
        # Single-byte tags/values dominate Headscale's inventory. Check minimal
        # unsigned varints directly, without allocating/re-encoding each one.
        tag = data[offset]
        if tag < 128:
            offset += 1
        else:
            tag, offset = _take_varint(data, offset)
        number, wire = tag >> 3, tag & 7
        if not 0 < number < 1 << 29 or wire not in (0, 2):
            raise ValueError('wire')
        if offset >= size:
            raise ValueError('wire')
        value = data[offset]
        if value < 128:
            offset += 1
        else:
            value, offset = _take_varint(data, offset)
        if wire == 2:
            end = offset + value
            if end > size:
                raise ValueError('wire')
            value, offset = data[offset:end], end
        yield number, wire, value
    _remaining(deadline)


def _message(data: bytes, types: dict[int, int], repeated: int = 0,
             deadline: float | None = None) -> dict[int, object]:
    values: dict[int, object] = {repeated: []} if repeated else {}
    for number, wire, value in _fields(data, deadline):
        if types.get(number) != wire or (number != repeated and number in values):
            raise ValueError('wire')
        if number == repeated:
            if len(values[number]) >= (600_000 if repeated == 1 else 128):
                raise ValueError('wire')
            values[number].append(value)
        else:
            values[number] = value
    return values


def _timestamp(data: bytes, deadline: float | None = None) -> datetime:
    _remaining(deadline)
    return _timestamp_value(data)


@lru_cache(maxsize=1024)
def _timestamp_value(data: bytes) -> datetime:
    # Only successfully validated timestamp bytes are cached; no keys/records.
    values = _message(data, {1: 0, 2: 0})
    seconds, nanos = values.get(1, 0), values.get(2, 0)
    if seconds >= 1 << 63:
        seconds -= 1 << 64
    if not -62135596800 <= seconds <= 253402300799 or not 0 <= nanos <= 999999999:
        raise ValueError('wire')
    # Datetime projects nanos to microseconds by truncation; expiry never extends.
    return datetime.fromtimestamp(seconds, timezone.utc).replace(microsecond=nanos // 1000)


def _record(data: bytes, deadline: float | None = None) -> PreAuthKey:
    if len(data) > 4096:
        raise ValueError('wire')
    values = _message(data, _RECORD_TYPES, 9, deadline)
    # Required fields use subscription: absence refuses rather than defaulting.
    user, identity, key = values[1].decode('utf-8'), values[2].decode('utf-8'), values[3].decode('utf-8')
    reusable, ephemeral, used = values.get(4, 0), values.get(5, 0), values.get(6, 0)
    if (not user or not identity or not key
            or reusable not in (0, 1) or ephemeral not in (0, 1) or used not in (0, 1)):
        raise ValueError('wire')
    record = PreAuthKey(user, identity, key, bool(reusable), bool(ephemeral), bool(used),
                        _timestamp(values[7], deadline), _timestamp(values[8], deadline),
                        tuple(map(bytes.decode, values[9])))
    if _KEY.fullmatch(record.key) is None:
        raise ValueError('wire')
    return record


def _runner_custody() -> None:
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        parts = _RUNNER.strip('/').split('/')
        for i, part in enumerate(parts):
            leaf = i == len(parts) - 1
            child = os.open(part, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
                            | (0 if leaf else os.O_DIRECTORY), dir_fd=fd)
            os.close(fd)
            fd = child
            st = os.fstat(fd)
            if (st.st_uid != 0 or st.st_gid != 0 or st.st_mode & 0o022
                    or (leaf and (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1
                                  or stat.S_IMODE(st.st_mode) != 0o755))):
                raise AdminRefused('refused')
    finally:
        os.close(fd)


class RootHeadscaleAdminOperations:
    """Fixed packaged binary, unary protobuf only; no caller path or credentials."""

    def __repr__(self) -> str:
        return 'RootHeadscaleAdminOperations(<redacted>)'

    def _call(self, verb: str, request: bytes, deadline: float) -> bytes:
        refused = False
        try:
            if os.geteuid() != 0:
                raise AdminRefused()
            _runner_custody()
            remaining = _remaining(deadline)
            code, data = _default_run([_RUNNER, verb, str(max(1, int(remaining * 1000)))],
                                      timeout=remaining, input_bytes=request)
            _remaining(deadline)
            refused = code == 2
            if code == 0 and type(data) is bytes and len(data) <= _MAX_BYTES:
                return data
        except AdminRefused:
            refused = True
        except BaseException:
            pass
        del self, request
        if refused:
            raise AdminRefused('refused') from None
        raise MintUncertain() from None

    def create_pre_auth_key(self, *, user: str, reusable: bool, ephemeral: bool,
                            expiration: datetime, timeout_s: float) -> PreAuthKey:
        result = None
        refused = False
        try:
            deadline = _deadline(timeout_s)
            if reusable is not False or ephemeral is not False or expiration.utcoffset() != timedelta(0):
                raise AdminRefused()
            seconds = int(expiration.timestamp())
            timestamp = b'\x08' + _varint(seconds)
            response = self._call('create', _bytes(1, user.encode()) + _bytes(4, timestamp), deadline)
            values = _message(response, {1: 2}, deadline=deadline)
            projected = _record(values[1], deadline)
            if projected.user != user:
                raise ValueError('wire')
            _remaining(deadline)
            result = projected
        except AdminRefused:
            refused = True
        except BaseException:
            pass
        del self, user, expiration
        if refused:
            raise AdminRefused('refused') from None
        if result is None:
            raise MintUncertain() from None
        return result

    def list_pre_auth_keys(self, *, user: str, timeout_s: float) -> tuple[PreAuthKey, ...]:
        result = None
        try:
            deadline = _deadline(timeout_s)
            values = _message(self._call('list', _bytes(1, user.encode()), deadline), {1: 2}, 1, deadline)
            records, ids = [], set()
            for data in values[1]:
                _remaining(deadline)
                record = _record(data, deadline)
                if record.user != user or record.id in ids:
                    raise ValueError('wire')
                ids.add(record.id)
                records.append(record)
            projected = tuple(records)
            _remaining(deadline)
            result = projected
        except BaseException:
            pass
        del self, user
        if result is None:
            raise MintUncertain() from None
        return result

    def expire_pre_auth_key(self, *, user: str, key: str, timeout_s: float) -> None:
        ok = False
        try:
            deadline = _deadline(timeout_s)
            response = self._call('expire', _bytes(1, user.encode()) + _bytes(2, key.encode()), deadline)
            _remaining(deadline)
            ok = response == b''
        except BaseException:
            pass
        del self, user, key
        if not ok:
            raise MintUncertain() from None


def _directory() -> int:
    if os.geteuid() != 0:
        raise ValueError('custody')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if st.st_uid != 0 or st.st_gid != 0 or st.st_mode & 0o022:
            raise ValueError('custody')
        for i, part in enumerate((_STATE + '/' + INVITE_USER).strip('/').split('/')):
            if i >= 2:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = child
            st = os.fstat(fd)
            if st.st_uid != 0 or st.st_gid != 0 or st.st_mode & 0o022 or (i >= 2 and stat.S_IMODE(st.st_mode) != 0o700):
                raise ValueError('custody')
        return fd
    except BaseException:
        os.close(fd)
        raise


class DurableOperationRecordStore:
    """Used under the root wrapper's directory flock; writes no raw operation ID/key."""

    def __init__(self, directory_fd: int) -> None:
        st = os.fstat(directory_fd)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or st.st_gid != 0 or stat.S_IMODE(st.st_mode) != 0o700:
            raise ValueError('custody')
        self._fd = directory_fd

    def reserve(self, operation_id: str, window: CreationWindow) -> bool:
        name = 'operation-' + hashlib.sha256(operation_id.encode('utf-8')).hexdigest()
        data = (window.started_at.isoformat() + '\n' + window.expires_at.isoformat() + '\n').encode('ascii')
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=self._fd)
        except FileExistsError:
            return False
        try:
            os.fchown(fd, 0, 0)
            os.fchmod(fd, 0o600)
            if os.write(fd, data) != len(data):
                raise ValueError('store')
            os.fsync(fd)
            os.fsync(self._fd)
        finally:
            os.close(fd)
        return True


@contextmanager
def _locked() -> Iterator[HeadscaleJoinKeyMinter]:
    fd = _directory()
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield HeadscaleJoinKeyMinter(admin=RootHeadscaleAdminOperations(), invite_user=INVITE_USER,
                                    now_fn=lambda: datetime.now(timezone.utc),
                                    records=DurableOperationRecordStore(fd))
    finally:
        os.close(fd)


class RootHomeInviteMinter:
    """Unwired composition; nonblocking per-invite-user lock spans mint/recover."""

    def mint(self, operation_id: str, expires_at: datetime) -> MintedKey:
        refused, entered = False, False
        try:
            with _locked() as minter:
                entered = True
                return minter.mint(operation_id, expires_at)
        except MintRefused:
            refused = True
        except MintUncertain:
            pass
        except BaseException:
            refused = not entered
        del self, operation_id, expires_at
        if refused:
            raise MintRefused('refused') from None
        raise MintUncertain() from None

    def recover(self, operation_id: str) -> bool:
        try:
            with _locked() as minter:
                return minter.recover(operation_id)
        except BaseException:
            return False

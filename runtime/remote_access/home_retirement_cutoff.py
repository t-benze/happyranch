"""Explicit retirement cutoff through the original serving pairing owner.

A successful IPC reply is necessary, but is not store readback. Conversely,
burned ceremonies and revoked devices do not prove physical stream closure.
This owner requires both, without creating a second pairing manager/registry,
reinitializing the trust pair or turning a timeout into rollback. No startup caller
or CLI path invokes it implicitly. The coordinator must separately validate
legacy lineage before constructing it and hold the original retirement fence.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import socket
import stat
import struct
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

from runtime.remote_access.authorization import TrustState
from runtime.remote_access.helper_control import ControlClient, Refused, _receive, _send
from runtime.remote_access.home_retirement import (
    HomeRetirement, RetirementUncertain, STATE_DIRECTORY, _DIRECTORY_FLAGS, _FILE_FLAGS, _signature,
)
from runtime.remote_access.home_retirement_fence import RetirementFence
from runtime.remote_access.identity import ConnectorIdentity
from runtime.remote_access.state_store import AtomicFileTrustStateStore, MAX_STATE_BYTES, StateStoreError

if TYPE_CHECKING:
    from runtime.remote_access.installer import ManagedInstaller

_STORE = "var/lib/happyranch-connector"
_CONTROL = "run/happyranch-connector"
_PAIR = "trust-state.json"
_PEER_RECORD = "home-retirement-peer.json"
_BOOT_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


@dataclass(frozen=True, repr=False)
class OriginalPeer:
    """Original process identity selected after independent managed lineage.

    The cutoff pins it durably before its first request. A fresh recovery uses
    that preserved identity, not the current socket's peer. A replacement
    registry cannot acknowledge old transports.
    """

    pid: int
    started: int
    boot_id: str

    def __post_init__(self) -> None:
        if (any(type(value) is not int or value <= 0 for value in (self.pid, self.started))
                or type(self.boot_id) is not str or _BOOT_ID.fullmatch(self.boot_id) is None):
            raise RetirementUncertain()

    def __repr__(self) -> str:
        return "OriginalPeer(<redacted>)"


class _PairFiles:
    """Feed pinned bytes to the existing strict snapshot/anchor parser.

    This object has no save, transaction, initialization or permission-repair
    operation. Reuse the real parser, including legacy envelope rules, instead
    of maintaining a second interpretation of persistent authority.
    """

    def __init__(self, root: Path, data: dict[str, bytes]) -> None:
        self._path = root / _PAIR
        self._anchor_path = root / (_PAIR + ".anchor")
        self._data = data

    def _read_owner_only(self, path: Path, what: str) -> bytes:
        return self._data[path.name]

    @property
    def _default_state(self) -> TrustState:
        raise RetirementUncertain()

    _parse_anchor = AtomicFileTrustStateStore._parse_anchor


class _OriginalClient(ControlClient):
    def __init__(self, owner: RetirementCutoff) -> None:
        self.owner = owner
        super().__init__(str(owner.io.root / _CONTROL / "control.sock"))

    def _call(self, request: dict, before_commit=None, *, unavailable_on_loss=False) -> dict:
        owner = self.owner
        try:
            owner.require_fence()
            with owner._directory(_CONTROL) as parent:
                before = owner._socket(parent)
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                    connection.settimeout(min(2.0, owner.remaining()))
                    connection.connect(f"/proc/self/fd/{parent}/control.sock")
                    pid, uid, gid = struct.unpack("3i", connection.getsockopt(
                        socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                    if (uid, gid) != owner.account or pid <= 0:
                        raise RetirementUncertain()
                    process = owner._process(pid)
                    if owner.peer != OriginalPeer(pid, process[0], owner._boot_id()):
                        raise RetirementUncertain()
                    # Durable original identity precedes even the first status
                    # request. A fresh object must agree with the old pin; it
                    # cannot adopt this socket's replacement process/registry.
                    owner._pin_original_peer()

                    def current() -> None:
                        owner.require_fence()
                        owner.remaining()
                        owner._require_original_peer()
                        if (owner._socket(parent) != before or owner._process(pid) != process
                                or owner._boot_id() != owner.peer.boot_id):
                            raise RetirementUncertain()

                    current()
                    _send(connection, request)
                    reply = _receive(connection)
                    if before_commit is not None and reply == {"result": "ready"}:
                        before_commit()
                        current()
                        _send(connection, {"commit": True})
                        reply = _receive(connection)
                    current()
                    if "error" in reply:
                        raise Refused(reply["error"])
                    return reply
        except (OSError, ValueError, RuntimeError, Refused):
            raise RetirementUncertain() from None


class CutoffReceipt:
    """Live original-owner acknowledgement plus independently read deny state.

    Not a durable record, Boolean success input or replay permission. Later
    destructive steps must freshly require it while the same fence is live.
    """

    def __init__(self, owner: RetirementCutoff, generation: int) -> None:
        self._owner, self._generation = owner, generation

    def require(self) -> None:
        self._owner.confirm(self._generation)


class RetirementCutoff:
    def __init__(self, io: ManagedInstaller, fence: RetirementFence,
                 identity: ConnectorIdentity, release_uid: int, *, original_peer: OriginalPeer) -> None:
        self.io, self.fence, self.identity, self.release_uid = io, fence, identity, release_uid
        user, group = io._lookup_user("happyranch"), io._lookup_group("happyranch")
        self.account = (user.pw_uid, group.gr_gid)
        if (user.pw_name != "happyranch" or group.gr_name != "happyranch"
                or user.pw_gid != group.gr_gid or any(type(i) is not int or i <= 0 for i in self.account)
                or type(release_uid) is not int or release_uid <= 0):
            raise RetirementUncertain()
        if not isinstance(original_peer, OriginalPeer):
            raise RetirementUncertain()
        self.peer = original_peer
        self._peer_record_identity: tuple[int, ...] | None = None
        self._peer_record_synced = False
        self._acknowledged: int | None = None
        self.deadline = time.monotonic() + 60
        self.client = _OriginalClient(self)

    @classmethod
    def from_saved_peer(cls, io: ManagedInstaller, fence: RetirementFence,
                        identity: ConnectorIdentity, release_uid: int) -> RetirementCutoff:
        """Explicit recovery loads the original pin without discovering a peer.

        No missing-record fallback, native request, file creation or authority
        restoration. Independent managed lineage must still supply identity and
        release UID. The first real request resynchronizes this exact inode.
        """
        try:
            fence.require(io)
            reader = HomeRetirement(io)
            with reader._entry(STATE_DIRECTORY + "/" + _PEER_RECORD) as (parent, name, info):
                if info is None:
                    raise RetirementUncertain()
                data = reader._read(parent, name, info, private=True)
                record = io._json(data)
                peer = record["peer"]
                if type(peer) is not dict or peer.keys() != {"pid", "started", "boot_id"}:
                    raise RetirementUncertain()
                owner = cls(io, fence, identity, release_uid,
                    original_peer=OriginalPeer(peer["pid"], peer["started"], peer["boot_id"]))
                if data != owner._peer_record():
                    raise RetirementUncertain()
                owner._peer_record_identity = _signature(info)
            owner._require_original_peer()
            return owner
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError):
            raise RetirementUncertain() from None

    def _boot_id(self) -> str:
        from runtime.remote_access.home_retirement_service import boot_identity
        return boot_identity(self.io)

    def _peer_record(self) -> bytes:
        # This protected intent contains only nonsecret identity, never trust
        # bytes, credentials, keys, config contents or caller-selected paths.
        value = dict(format="home-retirement-peer@1",
            peer=dict(pid=self.peer.pid, started=self.peer.started, boot_id=self.peer.boot_id),
            account=dict(uid=self.account[0], gid=self.account[1]),
            identity=self.identity.model_dump(), release_uid=self.release_uid)
        data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        if len(data) > 1024:
            raise RetirementUncertain()
        return data

    def _read_original_peer(self) -> tuple[int, ...] | None:
        with HomeRetirement(self.io)._entry(STATE_DIRECTORY + "/" + _PEER_RECORD) as (parent, name, info):
            if info is None:
                return None
            if HomeRetirement(self.io)._read(parent, name, info, private=True) != self._peer_record():
                raise RetirementUncertain()
            return _signature(info)

    def _require_original_peer(self) -> None:
        self.require_fence()
        if self._peer_record_identity is None or self._read_original_peer() != self._peer_record_identity:
            raise RetirementUncertain()

    def _pin_original_peer(self) -> None:
        """Exclusive publication/readback; a pin is no cutoff acknowledgement.

        Complete existing bytes are synchronized again on explicit recovery.
        Unknown, torn or replaced evidence is never overwritten or removed.
        Only the native-validated original connection calls this method.
        """
        self.require_fence()
        self.remaining()
        if self._peer_record_identity is not None:
            self._require_original_peer()
            if self._peer_record_synced:
                return
        observed = self._read_original_peer()
        with self.io._publication_parent(STATE_DIRECTORY) as (parent, revalidate):
            self.require_fence()
            if observed is None:
                descriptor = os.open(_PEER_RECORD, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                    | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
            else:
                descriptor = os.open(_PEER_RECORD, _FILE_FLAGS, dir_fd=parent)
            try:
                if observed is None:
                    os.fchown(descriptor, *self.io.root_owner)
                    os.fchmod(descriptor, 0o600)
                    data = self._peer_record()
                    if os.write(descriptor, data) != len(data):
                        raise RetirementUncertain()
                elif _signature(os.fstat(descriptor)) != observed:
                    raise RetirementUncertain()
                os.fsync(descriptor)
                expected = _signature(os.fstat(descriptor))
                revalidate()
                self.require_fence()
                if self._read_original_peer() != expected:
                    raise RetirementUncertain()
                os.fsync(parent)
                if (_signature(os.fstat(descriptor)) != expected
                        or self._read_original_peer() != expected):
                    raise RetirementUncertain()
            finally:
                os.close(descriptor)
        self._peer_record_identity = expected
        self._peer_record_synced = True
        self._require_original_peer()

    def require_fence(self) -> None:
        self.fence.require(self.io)
        user, group = self.io._lookup_user("happyranch"), self.io._lookup_group("happyranch")
        if ((user.pw_name, user.pw_uid, user.pw_gid) != ("happyranch", *self.account)
                or (group.gr_name, group.gr_gid) != ("happyranch", self.account[1])):
            raise RetirementUncertain()

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise RetirementUncertain()
        return remaining

    @contextmanager
    def _directory(self, relative: str) -> Iterator[int]:
        """Pin no-follow parents; only the fixed final directory is service-owned."""
        if relative not in (_STORE, _CONTROL):
            raise RetirementUncertain()
        descriptor = os.open("/", _DIRECTORY_FLAGS)
        opened, parents = [descriptor], []
        current = Path("/")
        try:
            for part in (self.io.root / relative).parts[1:]:
                before = os.stat(part, dir_fd=descriptor, follow_symlinks=False)
                child = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
                opened.append(child)
                info = os.fstat(child)
                current /= part
                if _signature(info) != _signature(before):
                    raise RetirementUncertain()
                if current.is_relative_to(self.io.root):
                    leaf = current == self.io.root / relative
                    if ((info.st_uid, info.st_gid) != (self.account if leaf else self.io.root_owner)
                            or info.st_mode & 0o022 or leaf and stat.S_IMODE(info.st_mode) != 0o700):
                        raise RetirementUncertain()
                parents.append((descriptor, part, child, info))
                descriptor = child
            yield descriptor
            for ancestor, name, child, before in parents:
                expected = (before.st_dev, before.st_ino, before.st_uid, before.st_gid, before.st_mode)
                for info in (os.fstat(child), os.stat(name, dir_fd=ancestor, follow_symlinks=False)):
                    if (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode) != expected:
                        raise RetirementUncertain()
        finally:
            for child in reversed(opened):
                os.close(child)

    def _file(self, parent: int, name: str) -> tuple[bytes, tuple[int, ...]]:
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or (info.st_uid, info.st_gid) != self.account or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_size > MAX_STATE_BYTES):
            raise RetirementUncertain()
        descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent)
        try:
            if _signature(os.fstat(descriptor)) != _signature(info):
                raise RetirementUncertain()
            chunks, total = [], 0
            while True:
                chunk = os.read(descriptor, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > MAX_STATE_BYTES:
                    raise RetirementUncertain()
            if (total != info.st_size or _signature(os.fstat(descriptor)) != _signature(info)
                    or _signature(os.stat(name, dir_fd=parent, follow_symlinks=False)) != _signature(info)):
                raise RetirementUncertain()
            return b"".join(chunks), _signature(info)
        finally:
            os.close(descriptor)

    def snapshot(self) -> tuple[TrustState, int]:
        """Existing lock only, no create/chmod or first-run default authority."""
        self.require_fence()
        with self._directory(_STORE) as parent:
            _, lock = self._file(parent, _PAIR + ".lock")
            descriptor = os.open(_PAIR + ".lock", _FILE_FLAGS, dir_fd=parent)
            try:
                if _signature(os.fstat(descriptor)) != lock:
                    raise RetirementUncertain()
                fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
                files = {name: self._file(parent, name) for name in (_PAIR, _PAIR + ".anchor")}
                if len({value[1][:2] for value in files.values()} | {lock[:2]}) != 3:
                    raise RetirementUncertain()
                adapter = _PairFiles(self.io.root / _STORE, {name: value[0] for name, value in files.items()})
                state = AtomicFileTrustStateStore._load_locked(adapter)
                generation = adapter._parse_anchor(files[_PAIR + ".anchor"][0])["generation"]
                if (state.connector_identity != self.identity
                        or any(self._file(parent, name) != value for name, value in files.items())
                        or self._file(parent, _PAIR + ".lock")[1] != lock):
                    raise RetirementUncertain()
            finally:
                os.close(descriptor)
        self.require_fence()
        return state, generation

    def _socket(self, parent: int) -> tuple[int, ...]:
        info = os.stat("control.sock", dir_fd=parent, follow_symlinks=False)
        if (not stat.S_ISSOCK(info.st_mode) or (info.st_uid, info.st_gid) != self.account
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise RetirementUncertain()
        return _signature(info)

    def _process(self, pid: int) -> tuple[int, str]:
        # Reuse the bounded proc parser, not its Headscale service selector.
        from runtime.remote_access.home_retirement_service import OwnedService
        with self.io._publication_parent("proc") as (parent, revalidate):
            before = os.stat(str(pid), dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode) or before.st_uid != self.account[0]:
                raise RetirementUncertain()
            descriptor = os.open(str(pid), _DIRECTORY_FLAGS, dir_fd=parent)
            try:
                if _signature(os.fstat(descriptor)) != _signature(before):
                    raise RetirementUncertain()
                status = OwnedService._proc_read(descriptor, "status")
                for key, value in zip(("Uid", "Gid"), self.account):
                    if [line.split()[1:] for line in status.splitlines() if line.startswith(key + ":")] != [[str(value)] * 4]:
                        raise RetirementUncertain()
                fields = OwnedService._proc_read(descriptor, "stat").rsplit(") ", 1)
                if (len(fields) != 2 or fields[0].split(" (", 1)[0] != str(pid)
                        or len(fields[1].split()) < 20 or not fields[1].split()[19].isdigit()):
                    raise RetirementUncertain()
                start = int(fields[1].split()[19])
                executable = os.readlink("exe", dir_fd=descriptor)
                if (start <= 0 or executable != str(self.io.root / "opt/happyranch-desktop/python/bin/python3.12")
                        or OwnedService._proc_read(descriptor, "cgroup") != "0::/system.slice/happyranch-connector.service\n"):
                    raise RetirementUncertain()
                revalidate()
                if _signature(os.stat(str(pid), dir_fd=parent, follow_symlinks=False)) != _signature(before):
                    raise RetirementUncertain()
                return start, executable
            finally:
                os.close(descriptor)

    def _status(self, generation: int) -> None:
        status = self.client.status()
        if (not status.owned or status.release_uid != self.release_uid or status.generation != generation):
            raise RetirementUncertain()

    def capture_original(self) -> None:
        """Pin the native original before the coordinator captures its inventory.

        Status is read-only. No ceremony/device mutation or stream closure is
        requested and no cutoff receipt is produced. This separate boundary
        lets the coordinator preserve all remaining inventory before revoke.
        """
        try:
            _, generation = self.snapshot()
            self._status(generation)
            if self.snapshot()[1] != generation:
                raise RetirementUncertain()
            self._require_original_peer()
            self.remaining()
        except (OSError, ValueError, RuntimeError, KeyError, Refused, StateStoreError):
            raise RetirementUncertain() from None

    @staticmethod
    def _denied(state: TrustState) -> None:
        if (any(item is not None for item in (state.invite_record, state.pending_approval, state.delivery))
                or any(not device.revoked for device in state.devices.values())
                or any(not item.consumed for item in state.pending_pairings.values())):
            raise RetirementUncertain()

    def revoke(self) -> CutoffReceipt:
        try:
            _, generation = self.snapshot()
            self._status(generation)
            committed = self.client.revoke_all(generation, self.deadline, self.require_fence)
            self.remaining()
            self._acknowledged = committed
            # The original serving registry acknowledges physical cleanup in
            # revoke_all before returning. Do not construct another registry.
            self.confirm(committed)
            return CutoffReceipt(self, committed)
        except (OSError, ValueError, RuntimeError, KeyError, Refused, StateStoreError):
            raise RetirementUncertain() from None

    def confirm(self, generation: int) -> None:
        try:
            self.remaining()
            self._require_original_peer()
            if self._acknowledged != generation:
                raise RetirementUncertain()
            state, current = self.snapshot()
            self._denied(state)
            if current != generation:
                raise RetirementUncertain()
            self._status(generation)
            # Status may block; finish with another independent pair readback.
            state, current = self.snapshot()
            self._denied(state)
            if current != generation:
                raise RetirementUncertain()
            self.remaining()
        except (OSError, ValueError, RuntimeError, KeyError, Refused, StateStoreError):
            raise RetirementUncertain() from None

"""Inert B1 records and A5 custody/publication; no activation or recovery caller."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from runtime.remote_access.headscale_service import HeadscaleServiceError
from runtime.remote_access.installer import InstallerError, ManagedInstaller
# Preserve the historical type identities while retirement replaces all writers.
from runtime.remote_access.legacy_home_receipt import (
    ACTIVATION_PATH, BINDING_KEYS, CONTROL_URL, IDENTITY_KEYS, INVITE_CONFIG_PATH,
    LIVE_MEMBERS, MAX_RECORD_BYTES, MEMBER_PATHS, OWNED_LINK, OWNED_LINK_TARGET,
    TRANSACTION_FORMAT, HomeActivationError, HomeActivationReceipt, OwnershipInventory,
    _decode_record, _digest, _encode_record, _generation, _validate_identity,
)


def validate_transition(previous: HomeActivationReceipt, updated: HomeActivationReceipt) -> None:
    """Linear same-generation advancement; terminal recovery belongs to G2-C."""
    if type(previous) is not HomeActivationReceipt or type(updated) is not HomeActivationReceipt:
        raise HomeActivationError("transition")
    if any(getattr(previous, key) != getattr(updated, key) for key in (*BINDING_KEYS, "operation_id")):
        raise HomeActivationError("transition")
    edges = {"prepared": "activating", "activating": "joined", "joined": "ready"}
    if (previous.phase == "failed_uncertain"
            or (updated.phase != "failed_uncertain" and edges.get(previous.phase) != updated.phase)):
        raise HomeActivationError("transition")
    if (previous.phase in ("prepared", "activating") and updated.phase == "failed_uncertain"
            and updated.local_node_stable_id is not None):
        raise HomeActivationError("transition")
    if previous.local_node_stable_id is not None and (
            previous.local_node_stable_id != updated.local_node_stable_id
            or previous.home_endpoint != updated.home_endpoint):
        raise HomeActivationError("transition")


def validate_recovery_transition(previous: HomeActivationReceipt, updated: HomeActivationReceipt) -> None:
    """B7 only; ordinary linear transitions retain their terminal fence."""
    if (type(previous) is not HomeActivationReceipt or type(updated) is not HomeActivationReceipt
            or previous.phase != "failed_uncertain"
            or previous.member_digests != updated.member_digests
            or previous.ownership != updated.ownership):
        raise HomeActivationError("transition")
    fixed = tuple(key for key in BINDING_KEYS if key != "activation_generation")
    if any(getattr(previous, key) != getattr(updated, key) for key in fixed):
        raise HomeActivationError("transition")
    if updated.phase == "ready":
        if (previous.activation_generation != updated.activation_generation
                or previous.operation_id != updated.operation_id
                or updated.local_node_stable_id is None
                or (previous.local_node_stable_id is not None and
                    (previous.local_node_stable_id, previous.home_endpoint) !=
                    (updated.local_node_stable_id, updated.home_endpoint))):
            raise HomeActivationError("transition")
    elif updated.phase == "prepared":
        if (updated.activation_generation != previous.activation_generation + 1
                or updated.operation_id == previous.operation_id):
            raise HomeActivationError("transition")
    else:
        raise HomeActivationError("transition")


def _signature(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class HomeActivationStore:
    """One literal receipt writer and two read destinations; no mkdir or repair.

    root/root_owner are the existing ManagedInstaller temp-root venue. Live "/"
    always requires root:root. No command, service, account or installer flow runs.
    """

    def __init__(self, *, root: Path = Path("/"), root_owner: tuple[int, int] = (0, 0)) -> None:
        try:
            self._io = ManagedInstaller(root=root, root_owner=root_owner)
        except InstallerError:
            raise HomeActivationError("state") from None

    def __repr__(self) -> str:
        return "HomeActivationStore(<redacted>)"

    @contextmanager
    def _parent(self, path: str) -> Iterator[tuple[int, Callable[[], None]]]:
        if path not in (ACTIVATION_PATH, INVITE_CONFIG_PATH):
            raise HomeActivationError("state")
        relative = str(Path(path).parent).lstrip("/")
        with self._io._publication_parent(relative) as (parent, revalidate):
            if path == ACTIVATION_PATH:
                self._io._check_directory(parent, relative, (0o700, self._io.root_owner))
            yield parent, revalidate

    def _read_at(self, parent: int, name: str) -> bytes:
        if os.stat(name, dir_fd=parent, follow_symlinks=False).st_size > MAX_RECORD_BYTES:
            raise HomeActivationError("state")
        data = self._io._read_file(parent, name, owner=self._io.root_owner, mode=0o600)
        if len(data) > MAX_RECORD_BYTES:
            raise HomeActivationError("state")
        return data

    @contextmanager
    def _record(self, path: str, *, allow_absent: bool = False) -> Iterator[bytes | None]:
        """Pin both parent and leaf until a composed reader has finished."""
        try:
            with self._parent(path) as (parent, revalidate):
                name = Path(path).name
                self._io._headscale_no_pending(parent, name)
                try:
                    before = _signature(os.stat(name, dir_fd=parent, follow_symlinks=False))
                except FileNotFoundError:
                    if not allow_absent:
                        raise
                    yield None
                    self._io._headscale_no_pending(parent, name)
                    try:
                        os.stat(name, dir_fd=parent, follow_symlinks=False)
                    except FileNotFoundError:
                        revalidate()
                        return
                    raise HomeActivationError("state")
                data = self._read_at(parent, name)
                if _signature(os.stat(name, dir_fd=parent, follow_symlinks=False)) != before:
                    raise HomeActivationError("state")
                yield data
                self._io._headscale_no_pending(parent, name)
                if (self._read_at(parent, name) != data
                        or _signature(os.stat(name, dir_fd=parent, follow_symlinks=False)) != before):
                    raise HomeActivationError("state")
                revalidate()
        except (OSError, InstallerError, HeadscaleServiceError):
            raise HomeActivationError("state") from None

    def current(self) -> HomeActivationReceipt | None:
        """Read a pinned current receipt; only a safely absent leaf is absence.

        Pending evidence, missing/unsafe parents and changed reads still refuse.
        No publication, recovery, directory creation or installer effect runs.
        """
        with self._record(ACTIVATION_PATH, allow_absent=True) as data:
            receipt = None if data is None else HomeActivationReceipt.parse(data)
        return receipt

    def read(self, *, expected_generation: int) -> HomeActivationReceipt:
        _generation(expected_generation)
        with self._record(ACTIVATION_PATH) as data:
            receipt = HomeActivationReceipt.parse(data)
            if receipt.activation_generation != expected_generation:
                raise HomeActivationError("binding")
        return receipt

    def publish(self, receipt: HomeActivationReceipt, *,
                expected: HomeActivationReceipt | None = None) -> None:
        """Exclusive pending + fsync + CAS/rename. Every uncertain entry is retained."""
        if type(receipt) is not HomeActivationReceipt:
            raise HomeActivationError()
        data = receipt.to_bytes()
        if expected is None:
            if receipt.phase != "prepared":
                raise HomeActivationError("transition")
        else:
            validate_transition(expected, receipt)
        self._publish_record(ACTIVATION_PATH, data, expected=expected, parse=HomeActivationReceipt.parse)

    def _publish_record(self, path: str, data: bytes, *, expected: object | None,
                        parse: Callable[[bytes], object]) -> None:
        """Shared A5 writer; each closed store owns paths and transition checks."""
        try:
            with self._parent(path) as (parent, revalidate):
                name = Path(path).name
                self._io._headscale_no_pending(parent, name)
                previous = None
                before = None
                if expected is None:
                    self._io._refuse_provision_entry(parent, name)
                else:
                    before = _signature(os.stat(name, dir_fd=parent, follow_symlinks=False))
                    previous = self._read_at(parent, name)
                    if (parse(previous) != expected
                            or _signature(os.stat(name, dir_fd=parent, follow_symlinks=False)) != before):
                        raise HomeActivationError("transition")
                pending = "." + name + ".pending"
                descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                     | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
                try:
                    created = os.fstat(descriptor)
                    os.fchown(descriptor, *self._io.root_owner)
                    os.fchmod(descriptor, 0o600)
                    with os.fdopen(descriptor, "wb", closefd=False) as stream:
                        if stream.write(data) != len(data):
                            raise HomeActivationError("state")
                        stream.flush()
                        os.fsync(descriptor)
                    pending_info = os.stat(pending, dir_fd=parent, follow_symlinks=False)
                    if (self._read_at(parent, pending) != data
                            or (pending_info.st_dev, pending_info.st_ino) != (created.st_dev, created.st_ino)):
                        raise HomeActivationError("state")
                    revalidate()
                    if expected is None:
                        # Pending is ours; no-replace rename protects a late final arrival.
                        updating = False
                    else:
                        if (self._read_at(parent, name) != previous
                                or _signature(os.stat(name, dir_fd=parent, follow_symlinks=False)) != before):
                            raise HomeActivationError("transition")
                        updating = True
                    self._io._rename_provision_entry(parent, pending, name, updating=updating)
                    os.fsync(parent)
                    revalidate()
                finally:
                    os.close(descriptor)
                    # No pending cleanup/adoption: failed publication is inspection evidence.
        except (OSError, InstallerError, HeadscaleServiceError):
            raise HomeActivationError("state") from None


def helper_snapshot() -> bytes:
    """Fixed bundled -I metadata read; no full receipt, node identity or secrets."""
    receipt = HomeActivationStore().current()
    value = None if receipt is None else {
        'generation': receipt.activation_generation, 'operation': receipt.operation_id,
        'release_uid': receipt.release_uid, 'phase': receipt.phase}
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode() + b'\n'


if __name__ == '__main__':
    import sys
    if len(sys.argv) != 1 or os.geteuid() != 0:
        raise SystemExit('unavailable')
    try:
        sys.stdout.buffer.write(helper_snapshot())
    except Exception:
        raise SystemExit('unavailable') from None

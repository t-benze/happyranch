"""Inert B1 records and A5 custody/publication; no activation or recovery caller."""
from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Iterator, Mapping

from runtime.remote_access import invite_v2
from runtime.remote_access.headscale_service import HeadscaleServiceError
from runtime.remote_access.installer import InstallerError, ManagedInstaller
from runtime.remote_access.state_store import CorruptTrustStateError, _require_identity_text

MAX_RECORD_BYTES = 16 * 1024
ACTIVATION_PATH = "/var/lib/happyranch-remote-access/home-activation.json"
INVITE_CONFIG_PATH = "/etc/happyranch/invite-issuer.json"
# B4 fixes the format only. G2-C owns the transaction's closed member set and I/O.
TRANSACTION_FORMAT = "home-activation-txn@1"
MEMBER_PATHS = (
    "/etc/systemd/system/happyranch-headscale.service",
    "/etc/happyranch/headscale/config.yaml",
    "/etc/happyranch/headscale/derp.yaml",
    "/etc/happyranch/headscale/policy.hujson",
    "/etc/happyranch/control-ca.pem",
    INVITE_CONFIG_PATH,
)
LIVE_MEMBERS = (
    "/var/lib/happyranch-headscale/db.sqlite",
    "/var/lib/happyranch-headscale/db.sqlite-wal",
    "/var/lib/happyranch-headscale/db.sqlite-shm",
    "/var/lib/happyranch-headscale/noise.key",
    "/var/lib/happyranch-headscale/derp.key",
    "/run/happyranch-headscale/headscale.sock",
    "/run/happyranch-tsnet-sidecar/observe.sock",
    "/var/lib/happyranch-headscale/acme-unused",
)
OWNED_LINK = "/etc/systemd/system/happyranch-managed.target.wants/happyranch-headscale.service"
OWNED_LINK_TARGET = MEMBER_PATHS[0]
CONTROL_URL = "https://control.happyranch.invalid:28443"
BINDING_KEYS = (
    "release_uid", "home_id", "control_plane_id", "connector_id",
    "activation_generation", "ca_der_sha256", "dial_ip",
)
IDENTITY_KEYS = tuple(key for key in BINDING_KEYS if key != "activation_generation")
_RECEIPT_KEYS = frozenset((
    "format", *BINDING_KEYS, "operation_id", "member_digests",
    "local_node_stable_id", "home_endpoint", "phase", "ownership",
))
_PHASES = ("prepared", "activating", "joined", "ready", "failed_uncertain")


class HomeActivationError(ValueError):
    """Closed, input-free diagnostics, including underlying filesystem errors."""

    def __init__(self, category: str = "record") -> None:
        super().__init__(category if type(category) is str and category in
                         {"record", "state", "transition", "binding"} else "record")


def _unique_pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise HomeActivationError()
        result[key] = value
    return result


def _not_integer(token: str) -> int:
    raise HomeActivationError()


def _decode_record(data: bytes, keys: frozenset[str], format_name: str) -> dict:
    if type(data) is not bytes or not 0 < len(data) <= MAX_RECORD_BYTES:
        raise HomeActivationError()
    try:
        record = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_pairs,
                            parse_float=_not_integer, parse_constant=_not_integer)
        if type(record) is not dict or record.keys() != keys or record["format"] != format_name:
            raise HomeActivationError()
        return record
    except (ValueError, UnicodeError, RecursionError):
        raise HomeActivationError() from None


def _encode_record(record: dict) -> bytes:
    try:
        data = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError):
        raise HomeActivationError() from None
    if not 0 < len(data) <= MAX_RECORD_BYTES:
        raise HomeActivationError()
    return data


def _generation(value: object) -> None:
    if type(value) is not int or value < 0:
        raise HomeActivationError()


def _digest(value: object) -> None:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise HomeActivationError()


def _validate_identity(record: Any) -> None:
    if type(record.release_uid) is not int or not 0 < record.release_uid < 2**32 - 1:
        raise HomeActivationError()
    for name in ("home_id", "control_plane_id", "connector_id"):
        value = getattr(record, name)
        if type(value) is not str or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) is None:
            raise HomeActivationError()
    _digest(record.ca_der_sha256)
    if type(record.dial_ip) is not str or not invite_v2._canonical_dial_ip(record.dial_ip):
        raise HomeActivationError()


def _validate_binding(record: Any) -> None:
    _validate_identity(record)
    _generation(record.activation_generation)


def _endpoint(value: object) -> None:
    if type(value) is not str or not invite_v2._endpoint_valid(value):
        raise HomeActivationError()


@dataclass(frozen=True, repr=False)
class OwnershipInventory:
    """The B6 list and B5 link are literals, never caller-defined paths."""

    def __repr__(self) -> str:
        return "OwnershipInventory(<fixed>)"

    def to_dict(self) -> dict:
        return {"members": list(LIVE_MEMBERS), "links": {OWNED_LINK: OWNED_LINK_TARGET}}

    @classmethod
    def parse(cls, value: object) -> OwnershipInventory:
        if (type(value) is not dict or value.keys() != {"members", "links"}
                or type(value["members"]) is not list
                or any(type(item) is not str for item in value["members"])
                or len(value["members"]) != len(LIVE_MEMBERS)
                or set(value["members"]) != set(LIVE_MEMBERS)
                or type(value["links"]) is not dict
                or value["links"] != {OWNED_LINK: OWNED_LINK_TARGET}):
            raise HomeActivationError()
        return cls()


@dataclass(frozen=True, repr=False)
class HomeActivationReceipt:
    release_uid: int
    home_id: str
    control_plane_id: str
    connector_id: str
    activation_generation: int
    operation_id: str
    ca_der_sha256: str
    dial_ip: str
    member_digests: Mapping[str, str]
    local_node_stable_id: str | None
    home_endpoint: str | None
    phase: str
    ownership: OwnershipInventory = field(default_factory=OwnershipInventory)

    def __post_init__(self) -> None:
        _validate_binding(self)
        if (type(self.operation_id) is not str or re.fullmatch(r"[0-9a-f]{32}", self.operation_id) is None
                or type(self.phase) is not str or self.phase not in _PHASES
                or type(self.ownership) is not OwnershipInventory
                or not isinstance(self.member_digests, Mapping)
                or self.member_digests.keys() != set(MEMBER_PATHS)):
            raise HomeActivationError()
        for value in self.member_digests.values():
            _digest(value)
        object.__setattr__(self, "member_digests", MappingProxyType(dict(self.member_digests)))
        observed = self.local_node_stable_id is not None
        if (observed != (self.home_endpoint is not None)
                or (self.phase in ("prepared", "activating") and observed)
                or (self.phase == "ready" and not observed)):
            raise HomeActivationError()
        if observed:
            try:
                _require_identity_text(self.local_node_stable_id)
            except CorruptTrustStateError:
                raise HomeActivationError() from None
            _endpoint(self.home_endpoint)

    def __repr__(self) -> str:
        return "HomeActivationReceipt(<redacted>)"

    def to_bytes(self) -> bytes:
        record = {key: getattr(self, key) for key in (*BINDING_KEYS, "operation_id",
                  "local_node_stable_id", "home_endpoint", "phase")}
        record.update(format="home-activation@1", member_digests=dict(self.member_digests),
                      ownership=self.ownership.to_dict())
        return _encode_record(record)

    @classmethod
    def parse(cls, data: bytes) -> HomeActivationReceipt:
        record = _decode_record(data, _RECEIPT_KEYS, "home-activation@1")
        record.pop("format")
        record["ownership"] = OwnershipInventory.parse(record["ownership"])
        if type(record["member_digests"]) is not dict:
            raise HomeActivationError()
        return cls(**record)


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

"""Fixed read-only legacy custody, rendering and token checks for retained callers.

No activation, issuer, admin, minter or publication owner is imported here.
The existing protected readers and original installer directory lock are kept
unchanged while the retired writers' remaining callers are removed.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from runtime.remote_access.headscale_service import HeadscaleServiceError
from runtime.remote_access.installer import InstallerError, ManagedInstaller
from runtime.remote_access.installer_lock import installer_state_lock
from runtime.remote_access.legacy_home_receipt import (
    ACTIVATION_PATH, INVITE_CONFIG_PATH, MAX_RECORD_BYTES, OWNED_LINK, OWNED_LINK_TARGET,
    CONTROL_URL, CONFIG_MEMBERS, HomeActivationError, HomeActivationReceipt,
    HomeActivationTransaction, ActivationRefused, _generation,
)

TRANSACTION_PATH = "/var/lib/happyranch-remote-access/home-activation-txn.json"
_CONNECTOR, _SIDECAR = CONFIG_MEMBERS
_BINDING = ("home_id", "control_plane_id", "connector_id", "activation_generation")


def _signature(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class LegacyHomeReadback:
    """Fixed historical readers; no publication, mkdir or repair.

    root/root_owner are the existing ManagedInstaller temp-root venue. Live "/"
    always requires root:root. No command, service, account or installer flow runs.
    """

    def __init__(self, *, root: Path = Path("/"), root_owner: tuple[int, int] = (0, 0)) -> None:
        try:
            self._io = ManagedInstaller(root=root, root_owner=root_owner)
        except InstallerError:
            raise HomeActivationError("state") from None

    def __repr__(self) -> str:
        return "LegacyHomeReadback(<redacted>)"

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


class LegacyHomeTransactions(LegacyHomeReadback):
    @contextmanager
    def _parent(self, path: str) -> Iterator[tuple[int, Callable[[], None]]]:
        if path != TRANSACTION_PATH:
            raise HomeActivationError("state")
        with super()._parent(ACTIVATION_PATH) as parent:
            yield parent

    @contextmanager
    def locked(self) -> Iterator[int]:
        """One nonblocking directory lock spans both records and every effect."""
        try:
            with installer_state_lock(self._io) as parent:
                yield parent
        except (OSError, InstallerError, HeadscaleServiceError):
            raise HomeActivationError("state") from None

    def read_optional(self) -> HomeActivationTransaction | None:
        try:
            with self._parent(TRANSACTION_PATH) as (parent, revalidate):
                self._io._headscale_no_pending(parent, Path(TRANSACTION_PATH).name)
                try:
                    os.stat(Path(TRANSACTION_PATH).name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    revalidate()
                    return None
            with self._record(TRANSACTION_PATH) as data:
                return HomeActivationTransaction.parse(data)
        except (OSError, InstallerError, HeadscaleServiceError):
            raise HomeActivationError("state") from None


class HomeTokenSourceVerifier:
    """Compose the installer's same-FD/UID source rules; retain no token/digest."""

    def __init__(self, io: ManagedInstaller) -> None:
        self._io = io

    def require(self, receipt: HomeActivationReceipt) -> None:
        try:
            release = self._io._check_release_record()
            if release["uid"] != receipt.release_uid:
                raise ValueError
            source = self._io._input(Path(release["data_home"]) / "daemon.token",
                                     token=True, source_uid=receipt.release_uid)
            service = self._io._account(create=False)
            if service is None:
                raise ValueError
            with self._io._publication_parent() as (parent, check):
                staged = self._io._read_file(parent, "daemon.token", owner=service, mode=0o600)
                if staged != source:
                    raise ValueError
                check()
        except Exception:
            raise ActivationRefused("token_source_invalid") from None


def _addition(receipt: HomeActivationReceipt) -> bytes:
    return b"," + json.dumps(dict(
        ControlURL=CONTROL_URL,
        control_tls=dict(dial_ip=receipt.dial_ip, ca_sha256=receipt.ca_der_sha256),
        home_binding={key: getattr(receipt, key) for key in _BINDING},
    ), separators=(",", ":"), ensure_ascii=True).encode("ascii")[1:-1]


def render_home_live_configs(connector: bytes, sidecar: bytes,
                             receipt: HomeActivationReceipt) -> dict[str, bytes]:
    """Pure E2-shared renderer: preserve every prepared byte, add fixed fields only.

    The caller must establish protected provenance/receipt custody. No caller
    fields, paths or defaults select live identity, trust or control endpoints.
    Connector's existing identity profile needs no new loader field.
    """
    try:
        if (type(receipt) is not HomeActivationReceipt or type(connector) is not bytes
                or type(sidecar) is not bytes or not 0 < len(connector) <= 65536
                or not 0 < len(sidecar) <= 65536):
            raise ActivationRefused()
        sidecar.decode("utf-8")  # The existing Go JSON parser consumes UTF8.
        io = ManagedInstaller()
        io._validate_configs({"connector.json": connector, "sidecar.json": sidecar},
                             staged=True, partial=True)
        raw = io._json(connector)
        if (raw["managed"]["profile"] != "linux-managed-identity-v1"
                or raw["home_id"] != receipt.home_id or raw["connector_id"] != receipt.connector_id
                or raw["managed"]["control_plane_id"] != receipt.control_plane_id
                or io._json(sidecar)["RoleIdentity"] != "home-sidecar-" + receipt.home_id):
            raise ActivationRefused()
        end = sidecar.rindex(b"}")
        live = sidecar[:end] + _addition(receipt) + sidecar[end:]
        if len(live) > 65536:
            raise ActivationRefused()
        return {_CONNECTOR: connector, _SIDECAR: live}
    except Exception:
        raise ActivationRefused() from None


def helper_snapshot() -> bytes:
    """Fixed bundled -I metadata read; no full receipt, node identity or secrets."""
    receipt = LegacyHomeReadback().current()
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

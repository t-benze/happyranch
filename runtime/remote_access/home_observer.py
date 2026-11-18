"""Unmounted B2 client; protected owner/systemd and config writing are G2-E.

Only the injected owner supplies the current UID/MainPID of the exact owned unit.
The fixed B3 adapter independently checks node identity; no generic admin RPC.
"""
from __future__ import annotations

import os
import socket
import stat
import struct
import time
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network, ip_interface
from pathlib import Path
from typing import Protocol

from runtime.remote_access import invite_v2
from runtime.remote_access.home_activation import (
    CONTROL_URL, HomeActivationReceipt, _decode_record, _digest, _generation, _signature,
)
from runtime.remote_access.home_admin import HeadscaleNode
from runtime.remote_access.state_store import _require_identity_text

SOCKET_PATH = "/run/happyranch-tsnet-sidecar/observe.sock"
BINDING = ("home_id", "control_plane_id", "connector_id", "activation_generation")
_KEYS = frozenset(("format", "stable_id", "tailnet_addresses", "control_url", "node_key_sha256", "binding"))


class ObservationRefused(ValueError):
    def __init__(self) -> None:
        super().__init__("observation_refused")


@dataclass(frozen=True)
class SidecarProcess:
    uid: int
    main_pid: int


class ObservationOwner(Protocol):
    def sidecar_process(self, receipt: HomeActivationReceipt) -> SidecarProcess:
        """Fresh UID/MainPID from the exact owned unit; never cached or caller input."""
        ...


class NodeAdmin(Protocol):
    def list_nodes(self, *, user: str, timeout_s: float) -> tuple[HeadscaleNode, ...]: ...


@dataclass(frozen=True, repr=False)
class HomeObservation:
    stable_id: str
    home_endpoint: str


def _endpoint(addresses: object) -> tuple[str, str]:
    if type(addresses) is not list or len(addresses) != 1 or type(addresses[0]) is not str:
        raise ObservationRefused()
    text = addresses[0]
    address = ip_interface(text)
    if (not isinstance(address.ip, IPv4Address) or address.network.prefixlen != 32
            or ('/' in text and text.rsplit('/', 1)[1] != '32')
            or address.ip not in IPv4Network("100.64.0.0/10")):
        raise ObservationRefused()
    ip = str(address.ip)
    endpoint = ip + ":443"
    if not invite_v2._endpoint_valid(endpoint):
        raise ObservationRefused()
    return endpoint, ip


class HomeObserver:
    def __init__(self, *, owner: ObservationOwner, admin: NodeAdmin, root: Path = Path("/")) -> None:
        self._owner, self._admin, self._root = owner, admin, root

    def observe(self, receipt: HomeActivationReceipt, *, expected_generation: int) -> HomeObservation:
        try:
            return self._observe(receipt, expected_generation)
        except Exception:
            raise ObservationRefused() from None

    def _observe(self, receipt: HomeActivationReceipt, expected_generation: int) -> HomeObservation:
        until = time.monotonic() + 5
        def remaining() -> float:
            value = until - time.monotonic()
            if value <= 0:
                raise ObservationRefused()
            return value
        _generation(expected_generation)
        if type(receipt) is not HomeActivationReceipt or receipt.activation_generation != expected_generation:
            raise ObservationRefused()
        process = self._owner.sidecar_process(receipt)
        if (type(process) is not SidecarProcess or type(process.uid) is not int or process.uid < 0
                or type(process.main_pid) is not int or process.main_pid <= 0):
            raise ObservationRefused()
        path = self._root / SOCKET_PATH.lstrip("/")
        # Traverse all literal ancestors no-follow, pin the parent through connect.
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            for part in path.absolute().parts[1:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                os.close(fd)
                fd = child
            parent = os.fstat(fd)
            if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != process.uid or parent.st_mode & 0o022:
                raise ObservationRefused()
            before = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
            if (not stat.S_ISSOCK(before.st_mode) or before.st_uid != process.uid
                    or stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1):
                raise ObservationRefused()
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(remaining())
                conn.connect(f"/proc/self/fd/{fd}/{path.name}")
                pid, uid, _ = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
                if (uid, pid) != (process.uid, process.main_pid):
                    raise ObservationRefused()
                conn.sendall(b"observe-self@1\n")
                conn.shutdown(socket.SHUT_WR)
                data = bytearray()
                while True:
                    conn.settimeout(remaining())
                    chunk = conn.recv(4097 - len(data))
                    if not chunk:
                        break
                    data.extend(chunk)
                    if len(data) > 4096:
                        raise ObservationRefused()
            if (_signature(os.stat(path.name, dir_fd=fd, follow_symlinks=False)) != _signature(before)
                    or _signature(os.fstat(fd)) != _signature(parent)
                    or self._owner.sidecar_process(receipt) != process):
                raise ObservationRefused()
        finally:
            os.close(fd)
        record = _decode_record(bytes(data), _KEYS, "observe-self@1")
        if record["control_url"] != CONTROL_URL:
            raise ObservationRefused()
        binding = record["binding"]
        if type(binding) is not dict or binding.keys() != set(BINDING):
            raise ObservationRefused()
        _generation(binding["activation_generation"])
        if any(type(binding[k]) is not type(getattr(receipt, k)) or binding[k] != getattr(receipt, k) for k in BINDING):
            raise ObservationRefused()
        stable = _require_identity_text(record["stable_id"])
        if receipt.local_node_stable_id is not None and stable != receipt.local_node_stable_id:
            raise ObservationRefused()
        _digest(record["node_key_sha256"])
        endpoint, ip = _endpoint(record["tailnet_addresses"])
        nodes = self._admin.list_nodes(user="happyranch-node", timeout_s=remaining())
        if type(nodes) is not tuple or len(nodes) > 512:
            raise ObservationRefused()
        matching = [n for n in nodes if n.node_key_sha256 == record["node_key_sha256"]]
        if (len(matching) != 1 or matching[0].user.name != "happyranch-node"
                or ip not in matching[0].ip_addresses):
            raise ObservationRefused()
        remaining()
        return HomeObservation(stable, endpoint)

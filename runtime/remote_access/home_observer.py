"""Unmounted B2 reader. G2-E supplies the protected systemd owner/config wiring."""
from __future__ import annotations

import ipaddress
import json
import os
import socket
import stat
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from runtime.remote_access import home_admin, invite_v2
from runtime.remote_access.home_activation import (
    CONTROL_URL, HomeActivationReceipt, _digest, _generation, _unique_pairs,
)
from runtime.remote_access.state_store import _require_identity_text

SOCKET_PATH = "/run/happyranch-tsnet-sidecar/observe.sock"
BINDING = ("home_id", "control_plane_id", "connector_id", "activation_generation")
_KEYS = {"format", "stable_id", "tailnet_addresses", "control_url", "node_key_sha256", "binding"}


class ObservationRefused(ValueError):
    def __init__(self) -> None:
        super().__init__("observation_refused")


class SidecarOwner(Protocol):
    """Fresh UID/MainPID of the EXACT owned unit, never a caller-selected process.

    No production systemd implementation is mounted in G2-D1 (deferred to G2-E).
    """

    def current_sidecar(self) -> tuple[int, int]: ...


class NodeAdmin(Protocol):
    """Production composition must use the existing fixed B3 runner adapter only."""

    def list_nodes(self, *, user: str, timeout_s: float) -> tuple[home_admin.HeadscaleNode, ...]: ...


@dataclass(frozen=True, repr=False)
class HomeObservation:
    stable_id: str
    home_endpoint: str


class HomeObserver:
    def __init__(self, *, owner: SidecarOwner, admin: NodeAdmin, root: Path = Path("/")) -> None:
        self._owner, self._admin, self._root = owner, admin, root

    def _read(self, uid: int, pid: int, deadline: float) -> bytes:
        # Walk literal components, no following even an aliased ancestor. Keep
        # the parent descriptor pinned through connect and check the name again.
        path = self._root / SOCKET_PATH.lstrip("/")
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            for part in path.parent.parts[1:]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                os.close(fd)
                fd = child
            parent = os.fstat(fd)
            named_parent = path.parent.lstat()
            if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != uid or parent.st_mode & 0o022
                    or (parent.st_dev, parent.st_ino) != (named_parent.st_dev, named_parent.st_ino)):
                raise ObservationRefused()
            before = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
            if (not stat.S_ISSOCK(before.st_mode) or before.st_uid != uid
                    or stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1):
                raise ObservationRefused()
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(self._remaining(deadline))
                conn.connect(str(path))
                peer_pid, peer_uid, _ = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                after = path.lstat()
                if (peer_pid != pid or peer_uid != uid or before != after
                        or path.parent.lstat() != named_parent or self._owner.current_sidecar() != (uid, pid)):
                    raise ObservationRefused()
                conn.settimeout(self._remaining(deadline))
                conn.sendall(b"observe-self@1\n")
                conn.shutdown(socket.SHUT_WR)
                data = bytearray()
                while True:
                    conn.settimeout(self._remaining(deadline))
                    chunk = conn.recv(4097 - len(data))
                    if not chunk:
                        return bytes(data)
                    data.extend(chunk)
                    if len(data) > 4096:
                        raise ObservationRefused()
        finally:
            os.close(fd)

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ObservationRefused()
        return remaining

    def observe(self, receipt: HomeActivationReceipt, *, expected_generation: int) -> HomeObservation:
        result = None
        try:
            _generation(expected_generation)
            if type(receipt) is not HomeActivationReceipt or receipt.activation_generation != expected_generation:
                raise ObservationRefused()
            uid, pid = self._owner.current_sidecar()
            if type(uid) is not int or not 0 <= uid < 2**32 - 1 or type(pid) is not int or pid <= 0:
                raise ObservationRefused()
            deadline = time.monotonic() + 5
            data = self._read(uid, pid, deadline)
            value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_pairs)
            if (type(value) is not dict or value.keys() != _KEYS or value["format"] != "observe-self@1"
                    or value["control_url"] != CONTROL_URL or type(value["binding"]) is not dict
                    or value["binding"].keys() != set(BINDING)):
                raise ObservationRefused()
            binding = value["binding"]
            _generation(binding["activation_generation"])
            if any(type(binding[key]) is not type(getattr(receipt, key))
                   or binding[key] != getattr(receipt, key) for key in BINDING):
                raise ObservationRefused()
            stable_id = _require_identity_text(value["stable_id"])
            _digest(value["node_key_sha256"])
            addresses = value["tailnet_addresses"]
            if type(addresses) is not list or len(addresses) != 1 or type(addresses[0]) is not str:
                raise ObservationRefused()
            address = addresses[0]
            if "/" in address:
                host, prefix = address.split("/")
                if prefix != "32":
                    raise ObservationRefused()
                address = host
            ipv4 = ipaddress.IPv4Address(address)
            if ipv4 not in ipaddress.IPv4Network("100.64.0.0/10"):
                raise ObservationRefused()
            endpoint = str(ipv4) + ":443"
            if not invite_v2._endpoint_valid(endpoint):
                raise ObservationRefused()
            nodes = self._admin.list_nodes(user="happyranch-node", timeout_s=self._remaining(deadline))
            matches = [node for node in nodes if node.node_key_sha256 == value["node_key_sha256"]]
            if (len(matches) != 1 or matches[0].user.name != "happyranch-node"
                    or str(ipv4) not in matches[0].ip_addresses
                    or (receipt.local_node_stable_id is not None and receipt.local_node_stable_id != stable_id)
                    or self._owner.current_sidecar() != (uid, pid)):
                raise ObservationRefused()
            self._remaining(deadline)
            result = HomeObservation(stable_id, endpoint)
        except Exception:
            pass
        if result is None:
            raise ObservationRefused() from None
        return result

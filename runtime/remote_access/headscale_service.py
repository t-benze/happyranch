"""Fixed inert Headscale policy and port/startup probes (THR-327 3b-2f-1)."""
from __future__ import annotations

import errno
import http.client
import json
import socket
import struct
import sys
import time

from runtime.remote_access.home_headscale import LANDefaultRoute, LANInterface, _dial_ip_valid

ACCOUNT = "happyranch-headscale"
UNIT = "happyranch-headscale.service"
NODE_USER = "happyranch-node"
INVITE_USER = "happyranch-invite"
POLICY_PATH = "/etc/happyranch/headscale/policy.hujson"
RECEIPT = "headscale-account.json"
CUSTODY = ("etc/happyranch/headscale", "var/lib/happyranch-headscale", "run/happyranch-headscale")
INSTALLER_STATE = "var/lib/happyranch-remote-access"
PUBLICATION = "headscale-publication.json"
CONFIG_FILES = ("config.yaml", "derp.yaml")
LEAF_FILES = ("server.crt", "server.key")
CONTROL_CA = "etc/happyranch/control-ca.pem"
EMPTY_CA = "etc/happyranch/empty-ca-directory"
HOME_CA = INSTALLER_STATE + "/home-ca"
MATERIALIZER = "opt/happyranch-desktop/libexec/remote-access/happyranch-home-ca"


class HeadscaleServiceError(RuntimeError):
    """Secret-free refusal; no native exception text is retained."""

    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)


def _attributes(data: bytes) -> dict[int, bytes]:
    result: dict[int, bytes] = {}
    while data:
        if len(data) < 4:
            if any(data):
                raise ValueError
            break
        length, kind = struct.unpack_from("=HH", data)
        if length < 4 or length > len(data) or kind in result:
            raise ValueError
        result[kind] = data[4:length]
        data = data[(length + 3) & ~3:]
    return result


def collect_lan_inventory() -> tuple[tuple[LANInterface, ...], tuple[LANDefaultRoute, ...]]:
    """Bounded kernel NETLINK_ROUTE dumps; no PATH, supplied address or probe.

    Keep every unicast default, including IPv6 and every ECMP next hop, without
    ranking tables/metrics. A missing RTA_PREFSRC is deliberately unresolved:
    the selector refuses inventory rather than guessing from interface order.
    Dumps are sampled observations, not an atomic network-change fence.
    """
    try:
        messages: dict[int, list[bytes]] = {}
        deadline = time.monotonic() + 6.0
        budget = 4 * 1024 * 1024
        with socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, socket.NETLINK_ROUTE) as channel:
            channel.bind((0, 0))
            for sequence, (request, response, size) in enumerate(((18, 16, 16), (22, 20, 8), (26, 24, 12)), 1):
                channel.settimeout(max(0.001, deadline - time.monotonic()))
                packet = struct.pack("=IHHII", 16 + size, request, 0x301, sequence, 0) + bytes(size)
                if channel.sendto(packet, (0, 0)) != len(packet):
                    raise ValueError
                messages[response] = []
                done = False
                while not done:
                    if time.monotonic() >= deadline or budget <= 0:
                        raise ValueError
                    channel.settimeout(max(0.001, deadline - time.monotonic()))
                    data, _, flags, sender = channel.recvmsg(min(65536, budget))
                    budget -= len(data)
                    if not data or flags & socket.MSG_TRUNC or sender != (0, 0):
                        raise ValueError
                    while data:
                        if len(data) < 16:
                            raise ValueError
                        length, kind, msg_flags, seq, _ = struct.unpack_from("=IHHII", data)
                        if (length < 16 or length > len(data) or seq != sequence
                                or msg_flags & 0x10 or done):  # NLM_F_DUMP_INTR
                            raise ValueError
                        body = data[16:length]
                        if kind == 3:  # NLMSG_DONE may carry a negative dump error.
                            if body and (len(body) != 4 or struct.unpack("=i", body)[0] != 0):
                                raise ValueError
                            done = True
                        elif kind == response:
                            if len(messages[response]) >= 65536:
                                raise ValueError
                            messages[response].append(body)
                        else:  # ERROR, OVERRUN and unknown messages never mean an empty dump.
                            raise ValueError
                        data = data[(length + 3) & ~3:]
        interfaces: dict[int, tuple[bool, bool, list[str]]] = {}
        for body in messages[16]:
            if len(body) < 16:
                raise ValueError
            _, _, _, index, flags, _ = struct.unpack_from("=BBHiII", body)
            _attributes(body[16:])
            if index <= 0 or index in interfaces:
                raise ValueError
            interfaces[index] = bool(flags & 1), bool(flags & 8), []
        for body in messages[20]:
            if len(body) < 8:
                raise ValueError
            family, _, _, _, index = struct.unpack_from("=BBBBI", body)
            if family not in (socket.AF_INET, socket.AF_INET6):
                continue
            attrs = _attributes(body[8:])
            if index not in interfaces:
                raise ValueError
            address = socket.inet_ntop(family, attrs[2] if 2 in attrs else attrs[1])
            interfaces[index][2].append(address)
        routes: list[LANDefaultRoute] = []
        for body in messages[24]:
            if len(body) < 12:
                raise ValueError
            family, dst_len, src_len, _, _, _, _, kind, _ = struct.unpack_from("=BBBBBBBBI", body)
            if family not in (socket.AF_INET, socket.AF_INET6) or dst_len != 0 or kind != 1:
                continue
            attrs = _attributes(body[12:])
            if src_len or 30 in attrs:  # Source-specific or unresolved nexthop object.
                raise ValueError
            source = socket.inet_ntop(family, attrs[7]) if 7 in attrs else ""
            if 9 in attrs:  # RTA_MULTIPATH: do not preselect by weight or metric.
                hops = attrs[9]
                if not hops:
                    raise ValueError
                while hops:
                    if len(hops) < 8:
                        raise ValueError
                    length, _, _, index = struct.unpack_from("=HBBi", hops)
                    if length < 8 or length > len(hops):
                        raise ValueError
                    _attributes(hops[8:length])
                    routes.append(LANDefaultRoute(index, source))
                    hops = hops[(length + 3) & ~3:]
            else:
                index, = struct.unpack("=I", attrs[4])
                routes.append(LANDefaultRoute(index, source))
        return (tuple(LANInterface(index, up, loopback, tuple(addresses))
                      for index, (up, loopback, addresses) in interfaces.items()), tuple(routes))
    except (OSError, ValueError, KeyError, struct.error, OverflowError):
        pass
    raise HeadscaleServiceError("inventory")


def render_policy() -> bytes:
    """v0.25.1 hscontrol/policy/acls_types.go: accept/proto/src/dst, no defaults.

    Only the dedicated invite user's devices may initiate TCP to Linux-node
    devices on connector port 443. All other traffic has no accepting rule.
    Database user creation and minter injection belong to activation.
    """
    return (json.dumps({"acls": [{"action": "accept", "proto": "tcp",
                                "src": [INVITE_USER], "dst": [NODE_USER + ":443"]}]},
                       sort_keys=True, indent=2) + "\n").encode()


def preflight_ports(dial_ip: str) -> None:
    """Sample all four exclusive binds; no reuse option, no retained listener.

    Inert installer preparation calls this with the selected LAN address. This is a
    preflight observation, not a reservation against later competing binds.
    """
    if not _dial_ip_valid(dial_ip):
        raise HeadscaleServiceError("dial-ip")
    bindings = ((dial_ip, 8443, socket.SOCK_STREAM), (dial_ip, 3478, socket.SOCK_DGRAM),
                ("127.0.0.1", 19090, socket.SOCK_STREAM), ("127.0.0.1", 50443, socket.SOCK_STREAM))
    category = None
    try:
        for address, port, kind in bindings:
            with socket.socket(socket.AF_INET, kind) as probe:
                probe.bind((address, port))
    except OSError as error:
        category = "occupied-port" if error.errno == errno.EADDRINUSE else "port-check-uncertain"
    if category:
        raise HeadscaleServiceError(category)


def wait_ready() -> None:
    """Bounded local startup probe; never called by inert preparation.

    Tagged Serve binds the metrics listener after its HTTPS listener. This
    probe is startup evidence only, not connector/tailnet acceptance.
    """
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        connection = http.client.HTTPConnection("127.0.0.1", 19090, timeout=min(1.0, remaining))
        try:
            connection.request("GET", "/metrics")
            if connection.getresponse().status == 200:
                return
        except (OSError, http.client.HTTPException):
            pass
        finally:
            connection.close()
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
    raise HeadscaleServiceError("readiness-timeout")


def main() -> int:
    if sys.argv[1:] != ["wait-ready"]:
        return 2
    try:
        wait_ready()
    except HeadscaleServiceError:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

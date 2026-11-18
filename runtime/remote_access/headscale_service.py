"""Fixed inert Headscale policy and port/startup probes (THR-327 3b-2f-1)."""
from __future__ import annotations

import errno
import http.client
import json
import socket
import sys
import time

from runtime.remote_access.home_headscale import _dial_ip_valid

ACCOUNT = "happyranch-headscale"
UNIT = "happyranch-headscale.service"
NODE_USER = "happyranch-node"
INVITE_USER = "happyranch-invite"
POLICY_PATH = "/etc/happyranch/headscale/policy.hujson"
RECEIPT = "headscale-account.json"
CUSTODY = ("etc/happyranch/headscale", "var/lib/happyranch-headscale", "run/happyranch-headscale")
INSTALLER_STATE = "var/lib/happyranch-remote-access"


class HeadscaleServiceError(RuntimeError):
    """Secret-free refusal; no native exception text is retained."""

    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)


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

    Future activation must call this with the selected LAN address. This is a
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

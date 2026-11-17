"""Real Unix-socket identity ingress; no installed service or tailnet.

Run: uv run --frozen pytest scripts/test_identity_ingress.py -v
"""
from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.remote_access import managed_provider as managed
from runtime.remote_access.httpd import (
    BaseConnectorHandler, IdentityUnixServer, ingress_node_identity, send_json,
)
from runtime.remote_access.models import RemoteRequest
from runtime.remote_access.systemd_unit import ConnectorUnitSpec, SidecarUnitSpec, render_connector_unit, render_sidecar_unit


def frame(identity: bytes = b"node-stable-1", version: int = 1) -> bytes:
    return b"HRID" + bytes([version]) + len(identity).to_bytes(2, "big") + identity


class CaptureHandler(BaseConnectorHandler):
    def serve_request(self, request: RemoteRequest, now: datetime) -> None:
        self.server.observed.append((ingress_node_identity.get(), request))
        send_json(self, 200, {"node": ingress_node_identity.get()})


@contextmanager
def listener(expected_uid: int | None = None) -> Iterator[tuple[str, IdentityUnixServer]]:
    # sockaddr_un paths must be short even when pytest's worktree is long.
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="hr-ingress-") as directory:
        path = str(Path(directory) / "ingress.sock")
        server = IdentityUnixServer(path, CaptureHandler,
                                    os.getuid() if expected_uid is None else expected_uid)
        server.observed = []
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            yield path, server
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


def exchange(path: str, data: bytes) -> bytes:
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(4)
        client.connect(path)
        client.sendall(data)
        client.shutdown(socket.SHUT_WR)
        parts = []
        while True:
            try:
                chunk = client.recv(4096)
            except ConnectionResetError:
                break
            if not chunk:
                break
            parts.append(chunk)
        return b"".join(parts)


HTTP = b"GET /status HTTP/1.1\r\nHost: local\r\nConnection: close\r\n\r\n"


def test_identity_is_frame_only_and_headers_are_stripped() -> None:
    with listener() as (path, server):
        forged = HTTP.replace(b"Connection: close", b"X-HappyRanch-Node-ID: forged\r\n"
                              b"X-HappyRanch-Identity: forged\r\n"
                              b"X-HappyRanch-Tailnet-ID: forged\r\n"
                              b"X-Tailscale-User: forged\r\nX-Tailscale-User: duplicate\r\n"
                              b"X-HappyRanch-Device-Credential: synthetic\r\nConnection: close")
        response = exchange(path, frame() + forged)
        assert b"200 OK" in response
        identity, request = server.observed[0]
        assert identity == "node-stable-1"
        assert [(h.name, h.value) for h in request.headers if "forged" in h.value or "duplicate" in h.value] == []
        assert any(h.name == "x-happyranch-device-credential" for h in request.headers)
        assert ingress_node_identity.get() is None
        assert os.stat(path).st_mode & 0o777 == 0o600
        # A second real connection carries its own node, with no pooled context.
        response = exchange(path, frame(b"node-stable-2") + HTTP)
        assert json.loads(response.split(b"\r\n\r\n", 1)[1]) == {"node": "node-stable-2"}
        assert [item[0] for item in server.observed] == ["node-stable-1", "node-stable-2"]


@pytest.mark.parametrize("data", [
    pytest.param(frame(version=2) + HTTP, id="unknown-version"),
    pytest.param(b"HRID\x01\x04\x00" + b"a" * 1024 + HTTP, id="oversize"),
    pytest.param(b"HRID\x01\x00\x09short", id="truncated"),
    pytest.param(frame() + frame() + HTTP, id="duplicate"),
    pytest.param(HTTP, id="no-frame"),
    pytest.param(frame(b"") + HTTP, id="empty-id"),
    pytest.param(frame(b"\xff") + HTTP, id="invalid-utf8"),
    pytest.param(frame(b"node\nforged") + HTTP, id="control-character"),
])
def test_malformed_identity_refused(data: bytes) -> None:
    with listener() as (path, server):
        assert exchange(path, data) == b""
        assert server.observed == []


def test_wrong_kernel_peer_uid_refused() -> None:
    # No sudo or foreign-UID process: configure a UID unequal to the real peer.
    with listener(os.getuid() + 1) as (path, server):
        assert exchange(path, frame() + HTTP) == b""
        assert server.observed == []


def test_frame_total_bound() -> None:
    with listener() as (path, server):
        assert b"200 OK" in exchange(path, frame(b"n" * 1017) + HTTP)
        assert len(server.observed[0][0]) == 1017
        assert exchange(path, frame(b"n" * 1018) + HTTP) == b""
        assert len(server.observed) == 1


def test_managed_listener_fixed_private_path(monkeypatch: pytest.MonkeyPatch) -> None:
    config = managed.ManagedProviderConfig(profile="linux-managed-identity-v1")
    config.validate()
    with pytest.raises(managed.ManagedProviderError):
        replace(config, profile="future-v2").validate()
    with pytest.raises(managed.ManagedProviderError):
        replace(config, bind_port=12345).validate()
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="hr-managed-") as directory:
        path = Path(directory) / "ingress.sock"
        # Test-side filesystem isolation of the fixed profile constant only.
        monkeypatch.setattr(managed, "INGRESS_SOCKET", path)
        monkeypatch.setattr(managed.pwd, "getpwnam", lambda name: SimpleNamespace(pw_uid=os.geteuid()))
        adapter = managed.ManagedProviderAdapter(
            config=config, readiness=SimpleNamespace(evaluate=lambda now: SimpleNamespace(ready=True)),
            pairing=None, identity=None, ctx_factory=None,
        )
        adapter.start()
        try:
            assert adapter.listening and adapter.bound_port is None
            assert path.is_socket() and path.stat().st_mode & 0o777 == 0o600
        finally:
            adapter.stop()
        assert not path.exists()
        os.chmod(directory, 0o755)
        with pytest.raises(managed.ManagedProviderError):
            adapter.start()


@pytest.mark.parametrize("profile", [False, None], ids=["boolean", "null"])
def test_profile_type_refused(profile: object) -> None:
    with pytest.raises(managed.ManagedProviderError):
        managed.ManagedProviderConfig(profile=profile).validate()


@pytest.mark.parametrize("home_transport", [False, True], ids=["legacy", "home"])
@pytest.mark.parametrize("customized", [False, True], ids=["default", "customized"])
def test_exact_unit_hardening_parity(home_transport: bool, customized: bool) -> None:
    spec = ConnectorUnitSpec(user="happyranch", group="happyranch", release_verifier=True)
    legacy = render_connector_unit(spec).splitlines()
    modern = render_connector_unit(replace(spec, ingress_profile="linux-managed-identity-v1")).splitlines()
    assert [line for line in modern if line != "RuntimeDirectoryMode=0700"] == legacy
    assert modern.count("RuntimeDirectoryMode=0700") == 1
    assert tuple(line for line in modern if "=" in line and line != "RuntimeDirectoryMode=0700") == _BASE_CONNECTOR_DIRECTIVES
    sidecar_spec = SidecarUnitSpec()
    base_unit = _BASE_SIDECAR_UNIT
    base_directives = _BASE_SIDECAR_DIRECTIVES
    if customized:
        sidecar_spec = SidecarUnitSpec(
            binary_path="/custom path/sidecar", restart_sec=7, watchdog_sec=49,
        )
        # Independent main-rendered bytes for these exact custom inputs.
        for old, new in (
            ("/opt/happyranch/bin/happyranch-tsnet-sidecar", "'/custom path/sidecar'"),
            ("RestartSec=1", "RestartSec=7"),
            ("WatchdogSec=30", "WatchdogSec=49"),
        ):
            base_unit = base_unit.replace(old, new, 1)
            base_directives = tuple(line.replace(old, new, 1) for line in base_directives)
    if home_transport:
        sidecar_spec = replace(sidecar_spec, home_transport=True)
    # Legacy intentionally uses the omitted flag, guarding its default too.
    rendered_sidecar = render_sidecar_unit(sidecar_spec)
    sidecar = rendered_sidecar.splitlines()
    home_environment = (
        "Environment=SSL_CERT_FILE=/etc/happyranch/control-ca.pem",
        "Environment=SSL_CERT_DIR=/etc/happyranch/empty-ca-directory",
    )
    if home_transport:
        assert all(sidecar.count(line) == 1 for line in home_environment)
        assert rendered_sidecar == base_unit.replace(
            "Group=happyranch\n", "Group=happyranch\n" + "\n".join(home_environment) + "\n", 1
        )
    else:
        # No replacement of Go's OS trust-file/directory lookup by a legacy unit.
        # This observes rendered environment, not an installed TLS handshake.
        assert not any(line.startswith("Environment=SSL_CERT_") for line in sidecar)
        assert rendered_sidecar == base_unit
    assert tuple(line for line in sidecar if "=" in line and line not in home_environment) == base_directives
    # The independent directive contract includes the easy-to-miss MDWE line.
    for directive in ("MemoryDenyWriteExecute=yes", "NoNewPrivileges=yes", "ProtectSystem=strict",
                      "ProtectHome=yes", "CapabilityBoundingSet=", "AmbientCapabilities=",
                      "PrivateDevices=yes", "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX",
                      "Upholds=happyranch-tsnet-sidecar.service", "UMask=0077", "Type=notify"):
        assert directive in modern


# Frozen externally consumed systemd directives from origin/main e1ff752d.
# These baseline literals deliberately do not call the current renderer.
_BASE_CONNECTOR_DIRECTIVES = ('Description=HappyRanch remote-access connector (supervised Linux home connector)',
 'After=network-online.target',
 'Wants=network-online.target',
 'StartLimitIntervalSec=300',
 'StartLimitBurst=5',
 'PartOf=happyranch-managed.target',
 'Requires=happyranch-release-verifier.socket',
 'After=happyranch-release-verifier.socket',
 'Upholds=happyranch-tsnet-sidecar.service',
 'ExecStart=/opt/happyranch/venv/bin/python -m runtime.remote_access.cli run',
 'User=happyranch',
 'Group=happyranch',
 'RestartSec=1',
 'WatchdogSec=30',
 'TimeoutStopSec=10',
 'TimeoutStartSec=120',
 'NotifyAccess=main',
 'Type=notify',
 'Restart=on-failure',
 'NoNewPrivileges=yes',
 'PrivateTmp=yes',
 'ProtectSystem=strict',
 'ProtectHome=yes',
 'ProtectKernelTunables=yes',
 'ProtectControlGroups=yes',
 'RestrictSUIDSGID=yes',
 'RestrictRealtime=yes',
 'LockPersonality=yes',
 'MemoryDenyWriteExecute=yes',
 'SystemCallArchitectures=native',
 'UMask=0077',
 'RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX',
 'PrivateDevices=yes',
 'ProtectKernelModules=yes',
 'CapabilityBoundingSet=',
 'AmbientCapabilities=',
 'StateDirectory=happyranch-connector',
 'RuntimeDirectory=happyranch-connector',
 'LogsDirectory=happyranch-connector',
 'WantedBy=multi-user.target')
_BASE_SIDECAR_DIRECTIVES = ('Description=HappyRanch embedded tsnet sidecar',
 'After=network-online.target happyranch-connector.service',
 'Wants=network-online.target',
 'StartLimitIntervalSec=300',
 'StartLimitBurst=5',
 'BindsTo=happyranch-connector.service',
 'PartOf=happyranch-managed.target',
 'ExecStart=/opt/happyranch/bin/happyranch-tsnet-sidecar --config /etc/happyranch/sidecar.json '
 '--credential-file ${CREDENTIALS_DIRECTORY}/enrollment-key',
 'LoadCredential=enrollment-key:happyranch-tsnet-enrollment',
 'User=happyranch',
 'Group=happyranch',
 'NotifyAccess=main',
 'RestartSec=1',
 'WatchdogSec=30',
 'TimeoutStartSec=120',
 'TimeoutStopSec=35',
 'Type=notify',
 'Restart=on-failure',
 'NoNewPrivileges=yes',
 'PrivateTmp=yes',
 'ProtectSystem=strict',
 'ProtectHome=yes',
 'ProtectKernelTunables=yes',
 'ProtectControlGroups=yes',
 'RestrictSUIDSGID=yes',
 'RestrictRealtime=yes',
 'LockPersonality=yes',
 'MemoryDenyWriteExecute=yes',
 'SystemCallArchitectures=native',
 'UMask=0077',
 'RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK',
 'PrivateDevices=yes',
 'ProtectKernelModules=yes',
 'CapabilityBoundingSet=',
 'AmbientCapabilities=',
 'StateDirectory=happyranch-tsnet-sidecar',
 'StateDirectoryMode=0700',
 'RuntimeDirectory=happyranch-tsnet-sidecar',
 'RuntimeDirectoryPreserve=yes',
 'LogsDirectory=happyranch-tsnet-sidecar',
 'WantedBy=happyranch-managed.target')

# Complete origin/main 19c952c0 rendered bytes, including sections, order and whitespace.
_BASE_SIDECAR_UNIT = '[Unit]\nDescription=HappyRanch embedded tsnet sidecar\nAfter=network-online.target happyranch-connector.service\nWants=network-online.target\nStartLimitIntervalSec=300\nStartLimitBurst=5\nBindsTo=happyranch-connector.service\nPartOf=happyranch-managed.target\n\n[Service]\nExecStart=/opt/happyranch/bin/happyranch-tsnet-sidecar --config /etc/happyranch/sidecar.json --credential-file ${CREDENTIALS_DIRECTORY}/enrollment-key\nLoadCredential=enrollment-key:happyranch-tsnet-enrollment\nUser=happyranch\nGroup=happyranch\nNotifyAccess=main\nRestartSec=1\nWatchdogSec=30\nTimeoutStartSec=120\nTimeoutStopSec=35\nType=notify\nRestart=on-failure\nNoNewPrivileges=yes\nPrivateTmp=yes\nProtectSystem=strict\nProtectHome=yes\nProtectKernelTunables=yes\nProtectControlGroups=yes\nRestrictSUIDSGID=yes\nRestrictRealtime=yes\nLockPersonality=yes\nMemoryDenyWriteExecute=yes\nSystemCallArchitectures=native\nUMask=0077\nRestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK\nPrivateDevices=yes\nProtectKernelModules=yes\nCapabilityBoundingSet=\nAmbientCapabilities=\nStateDirectory=happyranch-tsnet-sidecar\nStateDirectoryMode=0700\nRuntimeDirectory=happyranch-tsnet-sidecar\nRuntimeDirectoryPreserve=yes\nLogsDirectory=happyranch-tsnet-sidecar\n\n[Install]\nWantedBy=happyranch-managed.target\n'

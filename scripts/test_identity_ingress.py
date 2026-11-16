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
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.remote_access import managed_provider as managed
from runtime.remote_access.httpd import (
    BaseConnectorHandler, IdentityUnixServer, ingress_node_identity, send_json,
)
from runtime.remote_access.systemd_unit import ConnectorUnitSpec, render_connector_unit


def frame(identity: bytes = b"node-stable-1", version: int = 1) -> bytes:
    return b"HRID" + bytes([version]) + len(identity).to_bytes(2, "big") + identity


class CaptureHandler(BaseConnectorHandler):
    def serve_request(self, request, now) -> None:
        self.server.observed.append((ingress_node_identity.get(), request))
        send_json(self, 200, {"node": ingress_node_identity.get()})


@contextmanager
def listener(expected_uid: int | None = None):
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


def test_identity_is_frame_only_and_headers_are_stripped():
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
def test_malformed_identity_refused(data):
    with listener() as (path, server):
        assert exchange(path, data) == b""
        assert server.observed == []


def test_wrong_kernel_peer_uid_refused():
    # No sudo or foreign-UID process: configure a UID unequal to the real peer.
    with listener(os.getuid() + 1) as (path, server):
        assert exchange(path, frame() + HTTP) == b""
        assert server.observed == []


def test_frame_total_bound():
    with listener() as (path, server):
        assert b"200 OK" in exchange(path, frame(b"n" * 1017) + HTTP)
        assert len(server.observed[0][0]) == 1017
        assert exchange(path, frame(b"n" * 1018) + HTTP) == b""
        assert len(server.observed) == 1


def test_managed_listener_fixed_private_path(monkeypatch):
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


def test_exact_unit_hardening_parity():
    spec = ConnectorUnitSpec(user="happyranch", group="happyranch", release_verifier=True)
    legacy = render_connector_unit(spec).splitlines()
    modern = render_connector_unit(replace(spec, ingress_profile="linux-managed-identity-v1")).splitlines()
    assert [line for line in modern if line != "RuntimeDirectoryMode=0700"] == legacy
    assert modern.count("RuntimeDirectoryMode=0700") == 1
    # The independent directive contract includes the easy-to-miss MDWE line.
    for directive in ("MemoryDenyWriteExecute=yes", "NoNewPrivileges=yes", "ProtectSystem=strict",
                      "ProtectHome=yes", "CapabilityBoundingSet=", "AmbientCapabilities=",
                      "PrivateDevices=yes", "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX",
                      "Upholds=happyranch-tsnet-sidecar.service", "UMask=0077", "Type=notify"):
        assert directive in modern

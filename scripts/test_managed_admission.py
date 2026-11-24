"""Managed admission through real HRID sockets; synthetic state/upstream only.

Run: PYTHONPATH=$PWD uv run --frozen pytest scripts/test_managed_admission.py -v
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import socket
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.remote_access import managed_provider as managed
from runtime.remote_access import supervisor as supervisor_module
from runtime.remote_access.diy_provider import DiyProviderAdapter, DiyProviderConfig
from runtime.remote_access.forwarding import ForwardingHarness
from runtime.remote_access.models import Header, RemoteRequest
from runtime.remote_access.network import NetworkConfig
from runtime.remote_access.pairing import PairingManager, PinnedPairingManager
from runtime.remote_access.policy import PolicyEnvelope, RoutePolicyConsumer
from runtime.remote_access.revocation import RevocationIncomplete
from runtime.remote_access.streams import StreamClosed, StreamRegistry
from runtime.remote_access.supervisor import ConnectorConfig, ConnectorSupervisor
from scripts.test_identity_ingress import exchange, frame

HTTP_PATH = "/api/v1/orgs"
STREAM_PATH = "/api/v1/orgs/synthetic/threads/THR-1/tail"
READY = SimpleNamespace(evaluate=lambda now: SimpleNamespace(ready=True))


def request(path=HTTP_PATH, *, credential="", body=None, headers=()):
    fields = ["Host: local", "Connection: close"]
    if credential:
        fields.append("X-HappyRanch-Device-Credential: " + credential)
    fields.extend(headers)
    if body is not None:
        fields.append("Content-Length: " + str(len(body)))
    return (("POST" if body is not None else "GET") + " " + path + " HTTP/1.1\r\n"
            + "\r\n".join(fields) + "\r\n\r\n").encode() + (body or b"")


def result(raw):
    header, body = raw.split(b"\r\n\r\n", 1)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = body
    return int(header.split(b" ")[1]), payload


def call(path, node, route=HTTP_PATH, **kwargs):
    return exchange(str(path), frame(node.encode()) + request(route, **kwargs))


def policy_now():
    now = datetime.now(timezone.utc)
    artifact = json.loads(Path("runtime/remote_access/route_policy.json").read_text())
    consumer = RoutePolicyConsumer.from_envelope(PolicyEnvelope(
        schema_version=1, artifact_version=artifact["version"], artifact=artifact,
        issued_at=now - timedelta(seconds=1), max_age_seconds=3600, revision=1, state="active",
    ), now=now)
    # Isolate transport attribution from the separately locked route contract.
    # The shipped policy denies all WS routes. Only this synthetic consumer
    # permits an already-allowed route, to reach WS registration/closure.
    consumer.upgrade_semantics["websocket"]["allowed_templates"] = ["GET " + STREAM_PATH]
    return consumer


@contextmanager
def running(monkeypatch, *, control="protected-control", state_path=None, helper=False):
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="hr-admit-") as directory:
        root = Path(directory)
        path = root / "ingress.sock"
        token = root / "daemon.token"
        token.write_text("synthetic-daemon-bearer")
        token.chmod(0o600)
        monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
        monkeypatch.setattr(managed, "INGRESS_SOCKET", path)
        monkeypatch.setattr(managed.pwd, "getpwnam", lambda name: SimpleNamespace(pw_uid=os.geteuid()))
        if helper:
            from runtime.remote_access import helper_control, release_verifier
            monkeypatch.setattr(helper_control, "CONTROL_SOCKET", str(root / "control.sock"))
            # Protected-record observation only; no live root record or verifier.
            monkeypatch.setattr(release_verifier, "_read", lambda *a, **kw: json.dumps({
                "user": "synthetic", "uid": os.getuid(), "data_home": "/opt/synthetic-release",
                "unit": release_verifier.UNIT, "fragment_path": release_verifier.FRAGMENT,
            }).encode())
        config = ConnectorConfig(
            tenant_id="tenant", home_id="home", connector_id="connector", daemon_port=12345,
            daemon_token_path=str(token), policy_path="synthetic-unused",
            state_path=str(state_path or root / "trust.json"),
            system=helper,
            managed=managed.ManagedProviderConfig(profile=managed.LINUX_IDENTITY_PROFILE,
                                                   control_plane_id=control),
        )
        supervisor = ConnectorSupervisor(config=config, readiness=READY, policy=policy_now())
        assert supervisor._start_provider(), "managed listener must start"
        try:
            yield path, supervisor
        finally:
            supervisor.shutdown()


def approve(supervisor, path, node="node-a", secret="synthetic-invite"):
    manager = supervisor._provider._pairing
    now = datetime.now(timezone.utc)
    manager.issue_invite_record(hashlib.sha256(secret.encode()).hexdigest(), now + timedelta(minutes=4))
    status, pending = result(call(path, node, "/pair/invite", body=secret.encode(), headers=(
        "X-HappyRanch-Node-ID: forged", "X-Tailscale-Control-Plane: forged",
    )))
    assert status == 200 and pending["state"] == "pending-approval", "pinned redemption must reach pending"
    state = supervisor.state_store.load().pending_approval
    assert (state.control_plane_id, state.stable_node_id) == ("protected-control", node)
    assert not supervisor.state_store.load().devices
    device = manager.approve_pending(state.generation, ("protected-control", node))
    assert device is not None
    return device, pending["completion_handle"]


def grant(supervisor, path, node="node-a", secret="synthetic-invite"):
    # Allow additional devices without the first-device assertion in approve.
    manager = supervisor._provider._pairing
    now = datetime.now(timezone.utc)
    manager.issue_invite_record(hashlib.sha256(secret.encode()).hexdigest(), now + timedelta(minutes=4))
    status, pending = result(call(path, node, "/pair/invite", body=secret.encode()))
    assert status == 200 and pending["state"] == "pending-approval", "pinned redemption must reach pending"
    record = supervisor.state_store.load().pending_approval
    assert (record.control_plane_id, record.stable_node_id) == ("protected-control", node)
    device = manager.approve_pending(record.generation, ("protected-control", node))
    assert device is not None
    status, completed = result(call(path, node, "/pair/complete", body=pending["completion_handle"].encode()))
    assert status == 200
    return device, completed["credential"]


def test_ceremony_pin_bearer_and_restart(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor_module, "HttpLoopbackForwarder", lambda *a, **kw: ForwardingHarness())
    state_path = tmp_path / "state" / "trust.json"
    with running(monkeypatch, state_path=state_path) as (path, supervisor):
        assert isinstance(supervisor._provider._pairing, PinnedPairingManager)
        assert supervisor._pairing_manager is None, "identity path must never construct legacy manager"
        _, handle = approve(supervisor, path)
        for node in ("wrong-node", "node-a\t"):
            raw = call(path, node, "/pair/complete", body=handle.encode(), headers=("X-HappyRanch-Node-ID: node-a",))
            assert result(raw) == (403, {"error": "pairing_denied"})
            assert handle.encode() not in raw and node.encode() not in raw
        status, completed = result(call(path, "node-a", "/pair/complete", body=handle.encode()))
        assert status == 200
        credential = completed["credential"]
        assert result(call(path, "node-a", "/pair/complete", body=handle.encode()))[0] == 403
        raw = call(path, "wrong-node", credential=credential, headers=(
            "X-HappyRanch-Node-ID: node-a", "X-HappyRanch-Control-Plane-ID: protected-control",
        ))
        assert result(raw)[0] == 403, "valid bearer must not authorize another HRID node"
        assert credential.encode() not in raw and b"wrong-node" not in raw
        assert b"200 OK" in call(path, "node-a", credential=credential)
        assert result(call(path, "node-a", "/auth/bootstrap", credential=credential))[0] == 403
        assert result(call(path, "node-a", "/pair", body=b"synthetic-legacy-code"))[0] == 403
        assert supervisor.registry.open_count() == 0
    with running(monkeypatch, state_path=state_path) as (path, restarted):
        assert b"200 OK" in call(path, "node-a", credential=credential), "completed grants survive restart"
    with running(monkeypatch, state_path=state_path, control="other-control") as (path, _):
        raw = call(path, "node-a", credential=credential,
                   headers=("X-HappyRanch-Control-Plane-ID: protected-control",))
        assert result(raw)[0] == 403, "configured control plane must match the persisted pin"


@pytest.mark.parametrize("node", ["n" * 257, "node\tvalue", "node\x01value", "node\x7fvalue"],
                         ids=["too-many-chars", "tab", "c0", "del"])
def test_frame_accepted_pairing_refused(monkeypatch, node):
    monkeypatch.setattr(supervisor_module, "HttpLoopbackForwarder", lambda *a, **kw: ForwardingHarness())
    with running(monkeypatch) as (path, supervisor):
        _, credential = grant(supervisor, path)
        manager = supervisor._provider._pairing
        secret = "synthetic-refused-invite"
        manager.issue_invite_record(hashlib.sha256(secret.encode()).hexdigest(),
                                    datetime.now(timezone.utc) + timedelta(minutes=4))
        raw = call(path, node, "/pair/invite", body=secret.encode())
        code, payload = result(raw)
        assert code == 403, "valid HRID must map pin validation to clean denial"
        assert payload == {"error": "pairing_denied"}
        assert node.encode() not in raw and secret.encode() not in raw
        assert supervisor.state_store.load().invite_record is None, "matched invalid redemption consumes invite"
        raw = call(path, node, credential=credential)
        assert result(raw)[0] == 403 and node.encode() not in raw and credential.encode() not in raw


def test_unicode_pairing_bound(monkeypatch):
    monkeypatch.setattr(supervisor_module, "HttpLoopbackForwarder", lambda *a, **kw: ForwardingHarness())
    with running(monkeypatch) as (path, supervisor):
        node = "界" * 256  # 768 UTF-8 bytes: both frame and pin bounds permit it.
        _, credential = grant(supervisor, path, node=node)
        assert b"200 OK" in call(path, node, credential=credential)


def test_old_config_missing_control_plane_refuses_without_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor_module, "HttpLoopbackForwarder", lambda *a, **kw: ForwardingHarness())
    with running(monkeypatch, control=None) as (path, supervisor):
        config_path = tmp_path / "config.json"
        supervisor.config.to_file(config_path)
        raw = json.loads(config_path.read_text())
        del raw["managed"]["control_plane_id"]
        config_path.write_text(json.dumps(raw))
        loaded = ConnectorConfig.from_file(config_path)
        assert loaded.managed.control_plane_id is None
        loaded.to_file(config_path)
        assert json.loads(config_path.read_text())["managed"]["control_plane_id"] is None
        assert supervisor._pairing_manager is None
        for route, args in [("/pair/invite", {"body": b"synthetic-invite"}),
                            ("/pair/complete", {"body": b"0" * 64}),
                            (HTTP_PATH, {"credential": "hrpair_synthetic"})]:
            assert result(call(path, "node-a", route, **args)) == (503, {"error": "control_plane_id_missing"})
        assert supervisor._pairing_manager is None
        loaded.managed = replace(loaded.managed, control_plane_id="protected-control")
        loaded.to_file(config_path)
        assert ConnectorConfig.from_file(config_path).managed.control_plane_id == "protected-control"


class ControlledHandle:
    status = 200
    headers = ()

    def __init__(self, label, *, fail_close=False):
        self.label = label
        self.events = queue.Queue()
        self.started = threading.Event()
        self.closed = False
        self.fail_close = fail_close
        if label == "instant":
            self.events.put(b"ok\n")
            self.events.put(None)

    def receive(self):
        self.started.set()
        event = self.events.get(timeout=8)
        if self.closed:
            raise StreamClosed(self.label)
        return event

    def close(self):
        self.closed = True
        self.events.put(None)
        if self.fail_close:
            raise OSError("synthetic-close-failure")


class ControlledForwarder(ForwardingHarness):
    def __init__(self):
        super().__init__()
        self.handles = queue.Queue()
        self.delayed = threading.Event()
        self.release = threading.Event()
        self.fail_close = False

    def open_stream(self, method, path, query, headers, body, bearer, stream_id):
        # Keep the existing real outbound credential-leak check.
        self.forward_once(method, path, query, headers, body, bearer)
        assert all(h.name != "x-happyranch-device-credential" for h in headers)
        label = next((h.value for h in headers if h.name == "x-case"), "instant")
        handle = ControlledHandle(label, fail_close=self.fail_close)
        self.handles.put(handle)
        if label == "a-late":
            self.delayed.set()
            assert self.release.wait(5)
        return handle


def connect(path, node, credential, kind, label):
    client = socket.socket(socket.AF_UNIX)
    client.settimeout(4)
    client.connect(str(path))
    headers = ["X-Case: " + label]
    if kind == "sse":
        headers.append("Accept: text/event-stream")
    elif kind == "websocket":
        headers.append("Upgrade: websocket")
    client.sendall(frame(node.encode()) + request(HTTP_PATH if kind == "http" else STREAM_PATH,
                                                 credential=credential, headers=headers))
    return client


def read_all(client):
    chunks = []
    while chunk := client.recv(4096):
        chunks.append(chunk)
    return b"".join(chunks)


@pytest.mark.parametrize("operation", ["revoke", "remove"])
def test_supervisor_revoke_preserves_other_device_http_ws_sse(monkeypatch, operation):
    transport = ControlledForwarder()
    monkeypatch.setattr(supervisor_module, "HttpLoopbackForwarder", lambda *a, **kw: transport)
    with running(monkeypatch) as (path, supervisor):
        device_a, credential_a = grant(supervisor, path)
        _, credential_b = grant(supervisor, path, "node-b", "synthetic-invite-b")
        provider = supervisor._provider
        registry = supervisor.registry
        clients = {}
        handles = {}
        try:
            for owner, node, credential in [("a", "node-a", credential_a), ("b", "node-b", credential_b)]:
                for kind in ("http", "websocket", "sse"):
                    label = owner + "-" + kind
                    clients[label] = connect(path, node, credential, kind, label)
                    handle = transport.handles.get(timeout=3)
                    assert handle.label == label and handle.started.wait(3)
                    handles[label] = handle
                    if kind != "http":
                        assert b"200 OK" in clients[label].recv(4096)
            assert registry.open_count() == 6
            clients["a-late"] = connect(path, "node-a", credential_a, "http", "a-late")
            assert transport.delayed.wait(3)
            # Real external core persists revocation with a different registry;
            # only the shipping supervisor reconciliation can close these sockets.
            if operation == "remove":
                # The shipped CLI accessor deletes the row in another process.
                external = ConnectorSupervisor(config=supervisor.config)
                external.pairing_manager().remove_device(device_a)
                assert device_a not in supervisor.state_store.load().devices
            else:
                external = PinnedPairingManager(state_store=supervisor.state_store,
                    identity=supervisor._configured_identity(), registry=StreamRegistry())
                assert external.revoke_device(device_a).complete
            assert not any(handle.closed for handle in handles.values())
            supervisor._reconcile_revocation()
            assert all(handles["a-" + kind].closed for kind in ("http", "websocket", "sse")), "all revoked-device transports must close"
            assert supervisor._reconciled_revocation_epoch == supervisor.state_store.load().revocation_epoch
            transport.release.set()
            assert result(read_all(clients["a-late"]))[0] == 403, "stale in-flight registration must be fenced"
            assert result(read_all(clients["a-http"]))[0] == 403
            assert read_all(clients["a-websocket"]) == b""
            assert read_all(clients["a-sse"]) == b""
            assert supervisor._provider is provider and supervisor.registry is registry
            assert registry.open_count() == 3 and not registry.sealed
            for kind in ("http", "websocket", "sse"):
                handle = handles["b-" + kind]
                assert not handle.closed
                handle.events.put(b"data: survivor\n\n")
                if kind == "http":
                    handle.events.put(None)
                    response = read_all(clients["b-http"])
                    assert b"200 OK" in response and b"survivor" in response
                else:
                    assert b"survivor" in clients["b-" + kind].recv(4096)
            assert b"200 OK" in call(path, "node-b", credential=credential_b)
            assert result(call(path, "node-a", credential=credential_a))[0] == 403
        finally:
            transport.release.set()
            supervisor.shutdown()
            for client in clients.values():
                client.close()


def test_removed_device_first_registration_is_fenced(monkeypatch):
    transport = ControlledForwarder()
    monkeypatch.setattr(supervisor_module, "HttpLoopbackForwarder", lambda *a, **kw: transport)
    with running(monkeypatch) as (path, supervisor):
        device_a, credential_a = grant(supervisor, path)
        _, credential_b = grant(supervisor, path, "node-b", "synthetic-invite-b")
        client_b = connect(path, "node-b", credential_b, "sse", "b-sse")
        handle_b = transport.handles.get(timeout=3)
        assert handle_b.started.wait(3) and b"200 OK" in client_b.recv(4096)
        client_a = connect(path, "node-a", credential_a, "http", "a-late")
        try:
            assert transport.delayed.wait(3)
            handle_a = transport.handles.get(timeout=3)
            assert supervisor.registry.open_count() == 1, "A has no registered owner yet"
            external = ConnectorSupervisor(config=supervisor.config)
            external.pairing_manager().remove_device(device_a)
            supervisor._reconcile_revocation()
            handle_a.events.put(None)
            transport.release.set()
            assert result(read_all(client_a))[0] == 403, "removed first registration must be fenced"
            assert handle_a.closed
            assert not handle_b.closed
            handle_b.events.put(b"data: survivor\n\n")
            assert b"survivor" in client_b.recv(4096)
            assert b"200 OK" in call(path, "node-b", credential=credential_b)
        finally:
            transport.release.set()
            supervisor.shutdown()
            client_a.close()
            client_b.close()


@pytest.mark.parametrize("phase", ["invite", "pending", "delivery"])
def test_diagnose_preserves_live_pairing_ceremony(monkeypatch, phase):
    monkeypatch.setattr(supervisor_module, "HttpLoopbackForwarder", lambda *a, **kw: ForwardingHarness())
    with running(monkeypatch) as (path, supervisor):
        manager = supervisor._provider._pairing
        secret = "synthetic-diagnostic-invite"
        manager.issue_invite_record(hashlib.sha256(secret.encode()).hexdigest(),
                                    datetime.now(timezone.utc) + timedelta(minutes=4))
        if phase != "invite":
            status, pending = result(call(path, "node-a", "/pair/invite", body=secret.encode()))
            assert status == 200
        if phase == "delivery":
            record = supervisor.state_store.load().pending_approval
            assert manager.approve_pending(record.generation, ("protected-control", "node-a"))
        state_path = Path(supervisor.config.state_path)
        anchor_path = Path(str(state_path) + ".anchor")
        before = (state_path.read_bytes(), anchor_path.read_bytes())
        observer = ConnectorSupervisor(config=supervisor.config, policy=policy_now(),
            readiness=SimpleNamespace(evaluate=lambda now: SimpleNamespace(ready=True, gates={})),
            manager=SimpleNamespace(status=lambda unit: SimpleNamespace(
                active_state="active", sub_state="running", running=True)))
        assert observer.diagnose()["provider"]["type"] == "managed"
        assert (state_path.read_bytes(), anchor_path.read_bytes()) == before, "diagnose must not recover live ceremony state"
        if phase == "invite":
            status, pending = result(call(path, "node-a", "/pair/invite", body=secret.encode()))
            assert status == 200, "the original invite must still redeem"
        if phase != "delivery":
            record = supervisor.state_store.load().pending_approval
            assert manager.approve_pending(record.generation, ("protected-control", "node-a")), "pending approval must remain usable"
        status, completed = result(call(path, "node-a", "/pair/complete", body=pending["completion_handle"].encode()))
        assert status == 200, "the original delivery handle must still complete"
        assert b"200 OK" in call(path, "node-a", credential=completed["credential"])


def test_supervisor_reconcile_reports_incomplete_cleanup(monkeypatch):
    transport = ControlledForwarder()
    transport.fail_close = True
    monkeypatch.setattr(supervisor_module, "HttpLoopbackForwarder", lambda *a, **kw: transport)
    with running(monkeypatch, helper=True) as (path, supervisor):
        device, credential = grant(supervisor, path)
        _, credential_b = grant(supervisor, path, "node-b", "synthetic-invite-b")
        client = connect(path, "node-a", credential, "sse", "a-sse")
        client_b = None
        try:
            assert transport.handles.get(timeout=3).started.wait(3)
            assert b"200 OK" in client.recv(4096)
            transport.fail_close = False
            client_b = connect(path, "node-b", credential_b, "sse", "b-sse")
            handle_b = transport.handles.get(timeout=3)
            assert handle_b.started.wait(3) and b"200 OK" in client_b.recv(4096)
            external = PinnedPairingManager(state_store=supervisor.state_store,
                identity=supervisor._configured_identity(), registry=StreamRegistry())
            external.revoke_device(device)
            for _ in range(2):
                with pytest.raises(RevocationIncomplete):
                    supervisor._reconcile_revocation()
            epoch = supervisor.state_store.load().revocation_epoch
            provider, registry = supervisor._provider, supervisor.registry
            # run() now admits only the fixed, custodied home config. Provision
            # that real foreground boundary in this fixture's private root;
            # keep the already-serving transport/trust store and its identities.
            from runtime.remote_access import installer
            from runtime.remote_access.legacy_home_receipt import CONTROL_URL
            io = installer.ManagedInstaller(root=path.parent, root_owner=(os.getuid(), os.getgid()))
            monkeypatch.setattr(io, "_account", lambda **kw: (os.getuid(), os.getgid()))
            files = {
                "connector.json": json.dumps(asdict(supervisor.config)).encode(),
                "sidecar.json": json.dumps(dict(Profile="linux-managed-identity-v1",
                    StateDir="/var/lib/happyranch-tsnet-sidecar", RoleIdentity="home-sidecar-fixture",
                    DERPPolicy="private-only", ListenAddr=":443", ExcludedDaemonPorts=[8765])).encode(),
            }
            raw = json.loads(files["connector.json"])
            raw.update(tenant_id=supervisor.config.tenant_id, home_id=supervisor.config.home_id,
                       connector_id=supervisor.config.connector_id)
            raw["managed"]["control_plane_id"] = supervisor.config.managed.control_plane_id
            sidecar = json.loads(files["sidecar.json"])
            sidecar.update(RoleIdentity="home-sidecar-" + raw["home_id"], ControlURL=CONTROL_URL,
                control_tls={"dial_ip": "192.168.1.2", "ca_sha256": "a" * 64},
                home_binding={"home_id": raw["home_id"], "connector_id": raw["connector_id"],
                    "control_plane_id": raw["managed"]["control_plane_id"], "activation_generation": 1})
            folder = path.parent / "etc/happyranch"
            folder.mkdir(parents=True)
            folder.parent.chmod(0o755)
            folder.chmod(0o755)
            for name, data in (("connector.json", raw), ("sidecar.json", sidecar)):
                member = folder / name
                member.write_text(json.dumps(data))
                member.chmod(0o640)
            managed_config = raw.pop("managed")
            supervisor.config = ConnectorConfig(**raw, managed=managed.ManagedProviderConfig(**managed_config))
            supervisor._config_path = Path("/etc/happyranch/connector.json")
            monkeypatch.setattr(installer, "ManagedInstaller", lambda: io)
            notifications = []
            supervisor._notify_fn = notifications.append

            def serving_pass(seconds):
                assert not handle_b.closed
                handle_b.events.put(b"data: survivor\n\n")
                assert b"survivor" in client_b.recv(4096)
                assert b"200 OK" in call(path, "node-b", credential=credential_b)
                assert result(call(path, "node-a", credential=credential))[0] == 403

            assert supervisor.run(max_iterations=2, wait_fn=serving_pass) == 0, "per-device cleanup must not escape the serving loop"
            assert supervisor._provider is provider and supervisor.registry is registry
            assert supervisor._reconciled_revocation_epoch < epoch, "failed cleanup must remain unacknowledged"
            assert sum("cleanup incomplete" in message for message in notifications) == 2, "each serving pass must retry and report incomplete cleanup"
            with pytest.raises(RevocationIncomplete):
                supervisor._reconcile_revocation()
            assert supervisor.state_store.load().devices[device].revoked
            assert read_all(client) == b""
        finally:
            supervisor.shutdown()
            client.close()
            if client_b is not None:
                client_b.close()


def test_legacy_diy_credential_unaffected(monkeypatch):
    transport = ControlledForwarder()
    monkeypatch.setattr(supervisor_module, "HttpLoopbackForwarder", lambda *a, **kw: transport)
    with running(monkeypatch) as (_, supervisor):
        supervisor.config.managed = None
        supervisor.config.diy = DiyProviderConfig(network=NetworkConfig())
        pairing = supervisor.pairing_manager()
        assert isinstance(pairing, PairingManager)
        issued = pairing.issue_pairing_code("legacy-device")
        adapter = DiyProviderAdapter(config=supervisor.config.diy, readiness=READY, pairing=pairing,
            identity=supervisor._configured_identity(), ctx_factory=supervisor.build_diy_ctx_factory())
        adapter._ready = True  # Drive the retained DIY request boundary, no tailnet resolver/service.
        now = datetime.now(timezone.utc)
        paired = adapter.handle_request(RemoteRequest("POST", "/pair", body=issued.code.encode()), now)
        credential = json.loads(paired.response.body)["credential"]
        assert credential.startswith("hrpair_")
        req = RemoteRequest("GET", HTTP_PATH, headers=(Header("x-happyranch-device-credential", credential),))
        allowed = adapter.handle_request(req, now).allowed
        assert allowed, "legacy credential must remain accepted through the gateway"
        assert not adapter.handle_request(replace(req, headers=()), now).allowed

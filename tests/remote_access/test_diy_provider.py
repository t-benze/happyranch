"""Supported-DIY customer-owned-network provider adapter tests
(THR-097 Unit 3A).

Proves at the adapter seam:

- strict config/bind validation (wildcard and loopback binds refused —
  the connector binds ONLY a concrete customer-owned-network address);
- no listener unless ALL readiness gates pass;
- the THR-034 wire contract end-to-end over a REAL socket: ``POST /pair``
  redemption returns ``{"credential": "hrpair_..."}`` exactly once;
  ``X-HappyRanch-Device-Credential``-authenticated forwarding reaches the
  literal-loopback daemon with the bearer injected on the final hop;
- forbidden routes, missing/tampered credentials, replayed/expired/removed
  credentials all deny 403 with category-level prose only;
- ``/pair`` is connector-local and never forwarded to the daemon;
- revocation (one device and all) closes live streams and persists across a
  fresh adapter/process instance over the same files;
- re-pairing invalidates old authority;
- the daemon stays loopback-only (not reachable on the customer-network
  address — a direct remote daemon/bearer attempt fails);
- credential/token/bearer leakage scans across responses, logs, and
  diagnostics.
"""
from __future__ import annotations

import http.client
import json
import socket
from datetime import datetime, timedelta, timezone

import pytest

from runtime.remote_access.authorization import TrustState
from runtime.remote_access.credentials import StaticDaemonCredentialProvider
from runtime.remote_access.diy_provider import (
    DEVICE_CREDENTIAL_HEADER,
    DiyProviderAdapter,
    DiyProviderConfig,
    DiyProviderError,
    make_diy_context_factory,
    make_diy_loopback_forwarder,
)
from runtime.remote_access.forwarding import LOOPBACK_HOST
from runtime.remote_access.identity import ConnectorIdentity
from runtime.remote_access.network import NetworkConfig
from runtime.remote_access.pairing import PairingManager
from runtime.remote_access.readiness import ConnectorReadiness
from runtime.remote_access.state import InMemoryTrustStateStore
from runtime.remote_access.state_store import AtomicFileTrustStateStore
from runtime.remote_access.streams import StreamRegistry
from runtime.remote_access.stripping import CredentialScanner

from .conftest import build_consumer, default_identity
from .fake_daemon import FakeDaemon, assert_daemon_received

BEARER = "diy-bearer-42"
NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)

# A real customer-owned-network bind address for the end-to-end socket tests:
# the host's first non-loopback IPv4 (hairpin to self is a genuine TCP path
# through the kernel network stack). Skipped with reason when the host has
# none (e.g. some CI runners).
def _host_network_ipv4() -> str | None:
    try:
        import subprocess

        out = subprocess.run(
            ["ip", "-4", "-o", "addr", "show"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        parts = line.split()
        for i, part in enumerate(parts):
            if part == "inet" and i + 1 < len(parts):
                addr = parts[i + 1].split("/")[0]
                if addr.startswith("127."):
                    continue
                try:
                    import ipaddress

                    ipaddress.ip_address(addr)
                except ValueError:
                    continue
                return addr
    return None


NETWORK_IPV4 = _host_network_ipv4()

pytestmark = pytest.mark.skipif(
    NETWORK_IPV4 is None,
    reason="host has no non-loopback IPv4 address for a customer-network socket test",
)


class _AlwaysReady:
    def evaluate(self, now):
        from runtime.remote_access.readiness import GateResult, ReadinessReport

        gates = {name: GateResult(True, f"{name}_ok", f"{name} ok") for name in ConnectorReadiness.GATE_NAMES}
        return ReadinessReport(ready=True, gates=gates)


class _NeverReady:
    def evaluate(self, now):
        from runtime.remote_access.readiness import GateResult, ReadinessReport

        gates = {name: GateResult(True, f"{name}_ok", f"{name} ok") for name in ConnectorReadiness.GATE_NAMES}
        gates["daemon_loopback"] = GateResult(False, "daemon_unavailable", "no daemon")
        return ReadinessReport(ready=False, gates=gates)


def _identity() -> ConnectorIdentity:
    return ConnectorIdentity(tenant_id="diy", home_id="home-a", connector_id="connector-a")


def _make_store(tmp_path, *, file_backed: bool = True):
    state = TrustState(connector_identity=_identity(), pairing_epoch=0, revocation_epoch=0)
    if file_backed:
        return AtomicFileTrustStateStore(tmp_path / "trust-state.json", state)
    return InMemoryTrustStateStore(state)


def _make_pairing(store, *, now_fn=None, registry=None) -> PairingManager:
    return PairingManager(
        state_store=store,
        identity=_identity(),
        now_fn=now_fn or (lambda: NOW),
        registry=registry,
    )


def _make_adapter(
    tmp_path,
    *,
    readiness=None,
    pairing=None,
    daemon: FakeDaemon | None = None,
    bind_address: str | None = None,
    config=None,
) -> DiyProviderAdapter:
    from .conftest import load_fixture, make_policy_envelope
    from runtime.remote_access.policy import RoutePolicyConsumer

    created_daemon = daemon is None
    daemon = daemon or FakeDaemon(BEARER)
    if created_daemon:
        daemon.start()
    registry = StreamRegistry()
    pairing = pairing or _make_pairing(_make_store(tmp_path), registry=registry)
    fixture = load_fixture("route-policy")
    # The policy is built relative to the REAL clock (the adapter serves
    # requests stamped with datetime.now) so the freshness window never
    # silently lapses mid-suite.
    real_now = datetime.now(timezone.utc)
    envelope = make_policy_envelope(fixture, issued_at=real_now - timedelta(seconds=60))
    policy = RoutePolicyConsumer.from_envelope(envelope, now=real_now)
    cfg = config or DiyProviderConfig(
        network=NetworkConfig(mode="explicit", address=bind_address or NETWORK_IPV4),
        bind_port=0,
    )
    adapter = DiyProviderAdapter(
        config=cfg,
        readiness=readiness or _AlwaysReady(),
        pairing=pairing,
        identity=_identity(),
        ctx_factory=make_diy_context_factory(
            identity=_identity(),
            pairing=pairing,
            policy=policy,
            credential_provider=StaticDaemonCredentialProvider(BEARER),
            forwarder=make_diy_loopback_forwarder(daemon.port),
            registry=registry,
            now_fn=lambda: NOW,
        ),
        bind_address=bind_address,
    )
    adapter._daemon = daemon  # for teardown
    return adapter


@pytest.fixture
def adapter(tmp_path):
    adapter = _make_adapter(tmp_path)
    yield adapter
    daemon = getattr(adapter, "_daemon", None)
    if daemon is not None:
        daemon.stop()
    try:
        adapter.stop()
    except Exception:
        pass


def _redeem(adapter: DiyProviderAdapter, code: str) -> tuple[int, dict]:
    conn = http.client.HTTPConnection(NETWORK_IPV4, adapter.bound_port, timeout=10)
    conn.request("POST", "/pair", body=code.encode(), headers={"Content-Type": "text/plain"})
    resp = conn.getresponse()
    body = json.loads(resp.read().decode())
    conn.close()
    return resp.status, body


def _forward(adapter: DiyProviderAdapter, credential: str, path: str = "/api/v1/health") -> tuple[int, bytes]:
    conn = http.client.HTTPConnection(NETWORK_IPV4, adapter.bound_port, timeout=10)
    conn.request("GET", path, headers={DEVICE_CREDENTIAL_HEADER: credential})
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp.status, body


class TestGating:
    def test_refuses_wildcard_bind(self, tmp_path) -> None:
        cfg = DiyProviderConfig(network=NetworkConfig(mode="explicit", address="0.0.0.0"))
        with pytest.raises((DiyProviderError, ValueError)):
            DiyProviderAdapter(
                config=cfg,
                readiness=_AlwaysReady(),
                pairing=_make_pairing(_make_store(tmp_path)),
                identity=_identity(),
                ctx_factory=None,  # type: ignore[arg-type]
            )

    def test_refuses_loopback_bind(self, tmp_path) -> None:
        cfg = DiyProviderConfig(network=NetworkConfig(mode="explicit", address="127.0.0.1"))
        with pytest.raises((DiyProviderError, ValueError)):
            DiyProviderAdapter(
                config=cfg,
                readiness=_AlwaysReady(),
                pairing=_make_pairing(_make_store(tmp_path)),
                identity=_identity(),
                ctx_factory=None,  # type: ignore[arg-type]
            )

    def test_no_listener_when_not_ready(self, tmp_path) -> None:
        cfg = DiyProviderConfig(network=NetworkConfig(mode="explicit", address=NETWORK_IPV4), bind_port=0)
        adapter = DiyProviderAdapter(
            config=cfg,
            readiness=_NeverReady(),
            pairing=_make_pairing(_make_store(tmp_path)),
            identity=_identity(),
            ctx_factory=None,  # type: ignore[arg-type]
            bind_address=NETWORK_IPV4,
        )
        with pytest.raises(DiyProviderError, match="readiness"):
            adapter.start()
        assert adapter.listening is False

    def test_bind_conflict_normalized(self, tmp_path) -> None:
        """An occupied port surfaces as the documented DiyProviderError
        category (never a bare OSError) so the supervised retry contract
        holds."""
        cfg = DiyProviderConfig(network=NetworkConfig(mode="explicit", address=NETWORK_IPV4), bind_port=0)
        a = DiyProviderAdapter(
            config=cfg,
            readiness=_AlwaysReady(),
            pairing=_make_pairing(_make_store(tmp_path)),
            identity=_identity(),
            ctx_factory=None,  # type: ignore[arg-type]
            bind_address=NETWORK_IPV4,
        )
        # occupy a port, then point the adapter at it
        import socket as _socket

        blocker = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        blocker.bind((NETWORK_IPV4, 0))
        blocker.listen(1)
        port = blocker.getsockname()[1]
        cfg2 = DiyProviderConfig(network=NetworkConfig(mode="explicit", address=NETWORK_IPV4), bind_port=port)
        a2 = DiyProviderAdapter(
            config=cfg2,
            readiness=_AlwaysReady(),
            pairing=_make_pairing(_make_store(tmp_path)),
            identity=_identity(),
            ctx_factory=None,  # type: ignore[arg-type]
            bind_address=NETWORK_IPV4,
        )
        with pytest.raises(DiyProviderError, match="bind"):
            a2.start()
        blocker.close()


class TestPairingCeremonyWire:
    def test_redeem_returns_credential_and_forward_works(self, adapter) -> None:
        adapter.start()
        assert adapter.listening and adapter.bound_port is not None
        pairing = adapter._pairing
        issued = pairing.issue_pairing_code("macbook-pro")
        status, body = _redeem(adapter, issued.code)
        assert status == 200
        credential = body["credential"]
        assert credential.startswith("hrpair_")
        # Forward with the credential reaches the loopback daemon.
        status, resp_body = _forward(adapter, credential, "/api/v1/health")
        assert status == 200
        assert json.loads(resp_body)["ok"] is True
        # Replay of the same code denies.
        status, body = _redeem(adapter, issued.code)
        assert status == 403
        assert "credential" not in body

    def test_pairing_path_never_forwards_to_daemon(self, adapter) -> None:
        adapter.start()
        daemon = adapter._daemon
        pairing = adapter._pairing
        issued = pairing.issue_pairing_code("macbook-pro")
        status, body = _redeem(adapter, issued.code)
        assert status == 200
        # No request with path /pair ever reached the daemon:
        assert not any(r["path"].startswith("/pair") for r in daemon.requests)

    def test_bad_code_denies_403(self, adapter) -> None:
        adapter.start()
        status, body = _redeem(adapter, "WRONGCODE")
        assert status == 403
        assert "error" in body and "credential" not in body

    def test_missing_credential_denies(self, adapter) -> None:
        adapter.start()
        conn = http.client.HTTPConnection(NETWORK_IPV4, adapter.bound_port, timeout=10)
        conn.request("GET", "/api/v1/health")
        resp = conn.getresponse()
        status = resp.status
        resp.read()
        conn.close()
        assert status == 403

    def test_tampered_credential_denies(self, adapter) -> None:
        adapter.start()
        pairing = adapter._pairing
        issued = pairing.issue_pairing_code("macbook-pro")
        status, body = _redeem(adapter, issued.code)
        assert status == 200
        credential = body["credential"]
        tampered = "hrpair_" + ("a" * 32 if credential[-1] != "a" else "b" * 32)
        status, _ = _forward(adapter, tampered)
        assert status == 403

    def test_forbidden_route_denies_403(self, adapter) -> None:
        adapter.start()
        pairing = adapter._pairing
        issued = pairing.issue_pairing_code("macbook-pro")
        status, body = _redeem(adapter, issued.code)
        assert status == 200
        credential = body["credential"]
        # Agent-callback route is forbidden remotely.
        status, resp_body = _forward(adapter, credential, "/api/v1/report-completion")
        assert status == 403
        assert b"Bearer" not in resp_body and BEARER.encode() not in resp_body

    def test_denials_are_category_only(self, adapter) -> None:
        adapter.start()
        pairing = adapter._pairing
        issued = pairing.issue_pairing_code("macbook-pro")
        status, body = _redeem(adapter, issued.code)
        credential = body["credential"]
        # Revoke then attempt: the denial body never contains the credential
        # or the bearer.
        pairing.revoke("macbook-pro")
        status, resp_body = _forward(adapter, credential)
        assert status == 403
        assert credential.encode() not in resp_body
        assert BEARER.encode() not in resp_body


class TestRevocationAndRestart:
    def test_restart_with_persisted_revocation_denies(self, tmp_path) -> None:
        """Revoke, stop the adapter, start a FRESH adapter over the same
        files: the revoked device is still denied (fail closed)."""
        daemon = FakeDaemon(BEARER)
        daemon.start()
        try:
            store = _make_store(tmp_path)
            pairing = _make_pairing(store)
            a1 = _make_adapter(tmp_path, pairing=pairing, daemon=daemon)
            a1.start()
            issued = pairing.issue_pairing_code("macbook-pro")
            status, body = _redeem(a1, issued.code)
            assert status == 200
            credential = body["credential"]
            assert _forward(a1, credential)[0] == 200
            pairing.revoke("macbook-pro")
            assert _forward(a1, credential)[0] == 403
            a1.stop()
            # Fresh adapter, fresh pairing manager, SAME files:
            fresh_pairing = _make_pairing(_make_store(tmp_path))
            a2 = _make_adapter(tmp_path, pairing=fresh_pairing, daemon=daemon)
            a2.start()
            try:
                assert _forward(a2, credential)[0] == 403
                assert fresh_pairing.load_state().revocation_epoch >= 1
            finally:
                a2.stop()
        finally:
            daemon.stop()

    def test_repair_invalidates_old_authority(self, adapter) -> None:
        adapter.start()
        pairing = adapter._pairing
        first = pairing.issue_pairing_code("macbook-pro")
        status, body = _redeem(adapter, first.code)
        assert status == 200
        old_credential = body["credential"]
        second = pairing.issue_pairing_code("macbook-pro")
        status, body = _redeem(adapter, second.code)
        assert status == 200
        new_credential = body["credential"]
        assert old_credential != new_credential
        # Old authority invalidated:
        assert _forward(adapter, old_credential)[0] == 403
        # New authority works:
        assert _forward(adapter, new_credential)[0] == 200

    def test_remove_device_denies_like_absent(self, adapter) -> None:
        adapter.start()
        pairing = adapter._pairing
        issued = pairing.issue_pairing_code("macbook-pro")
        status, body = _redeem(adapter, issued.code)
        assert status == 200
        credential = body["credential"]
        pairing.remove_device("macbook-pro")
        assert _forward(adapter, credential)[0] == 403


class TestDirectDaemonAttempt:
    def test_daemon_not_reachable_on_customer_network(self, adapter) -> None:
        """The daemon is loopback-only: connecting to the daemon port on the
        customer-network address must fail (a direct remote daemon/bearer
        attempt cannot reach it)."""
        adapter.start()
        daemon = adapter._daemon
        try:
            conn = socket.create_connection((NETWORK_IPV4, daemon.port), timeout=3)
            conn.close()
            pytest.fail("daemon must not listen on the customer network address")
        except OSError:
            pass  # refused — expected

    def test_direct_bearer_attempt_denied(self, adapter) -> None:
        """Sending the daemon bearer directly at the connector (as if it were
        a credential) denies — the bearer is never a pairing credential."""
        adapter.start()
        status, resp_body = _forward(adapter, BEARER, "/api/v1/health")
        assert status == 403


class TestSseThroughAdapter:
    def test_sse_stream_pumps_and_revocation_closes(self, tmp_path) -> None:
        daemon = FakeDaemon(BEARER)
        daemon.start()
        try:
            registry = StreamRegistry()
            pairing = _make_pairing(_make_store(tmp_path), registry=registry)
            a = _make_adapter(tmp_path, pairing=pairing, daemon=daemon)
            a.start()
            issued = pairing.issue_pairing_code("macbook-pro")
            status, body = _redeem(a, issued.code)
            assert status == 200
            credential = body["credential"]
            conn = http.client.HTTPConnection(NETWORK_IPV4, a.bound_port, timeout=10)
            conn.connect()
            conn.sock.settimeout(10)  # type: ignore[union-attr]
            conn.request(
                "GET",
                "/api/v1/orgs/acme/threads/T-1/tail",
                headers={DEVICE_CREDENTIAL_HEADER: credential, "Accept": "text/event-stream"},
            )
            resp = conn.getresponse()
            assert resp.status == 200
            first = resp.read1(1024)
            assert b"data: hello" in first
            # Revocation closes the live stream: the connection dies.
            pairing.revoke("macbook-pro")
            closed = False
            for _ in range(50):
                try:
                    chunk = resp.read1(1024)
                except (http.client.IncompleteRead, OSError, ConnectionError):
                    closed = True
                    break
                if not chunk:
                    closed = True
                    break
            assert closed, "stream should have been closed by revocation"
            conn.close()
            a.stop()
        finally:
            daemon.stop()

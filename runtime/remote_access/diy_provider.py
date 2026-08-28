"""Supported-DIY customer-owned-network provider adapter (THR-097 Unit 3A).

The PRODUCTION customer-owned-network (DIY) adapter: a home connector that
serves the inherited THR-034 wire contract — ``POST /pair`` redemption and
``X-HappyRanch-Device-Credential``-authenticated forwarding — over the
CUSTOMER'S OWN network (their own Tailscale/headscale tailnet, or an
explicitly configured customer-network address), forwarding every allowed
request to the literal-loopback daemon with the daemon bearer injected on
the final hop.

This is the Supported-DIY product lane, NOT the lab adapter: it has no
``lab_only`` flag, no static lab proof, and no lab banner. The existing
``lab_provider`` remains LAB-ONLY and unchanged in role; this adapter is a
sibling with the same locked gateway pipeline and the same fail-closed
invariants:

- binds ONLY to the resolved customer-owned-network address (wildcard,
  loopback, multicast, broadcast refused — ``runtime.remote_access.network``);
- no listener unless ALL readiness gates pass (the supervisor contract);
- every request runs the full locked gateway pipeline (identity/bind/proof/
  policy/normalize/allowlist/strip/bearer) and forwards to literal
  ``127.0.0.1`` only, bearer on the final hop;
- pairing is the explicit local ceremony (``pairing.PairingManager``):
  single-use expiring codes, per-device ``hrpair_`` credentials stored as
  digests only, monotonic epochs, re-pair invalidates old authority,
  revocation closes live streams and persists, removal denies identically
  to absent (no existence oracle);
- denials are 403 category-level prose only — no bearer, codes, credentials,
  paths, input, or exception text; the listener never logs raw request lines.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import unquote

from runtime.remote_access.authorization import AuthorizationVerifier
from runtime.remote_access.credentials import DaemonCredentialProvider
from runtime.remote_access.forwarding import LOOPBACK_HOST, LoopbackForwarder, LoopbackTarget
from runtime.remote_access.gateway import ConnectorGateway, GatewayContext
from runtime.remote_access.httpd import BaseConnectorHandler, send_json, serve_decision
from runtime.remote_access.identity import (
    ConnectorIdentity,
    DeviceProof,
    SingleUseGuard,
)
from runtime.remote_access.models import Decision, DeniedOutcome, ForwardedResponse, Header, RemoteRequest
from runtime.remote_access.network import (
    NetworkConfig,
    NetworkAddressError,
    resolve_customer_network_address,
)
from runtime.remote_access.pairing import PairingManager
from runtime.remote_access.policy import RoutePolicyConsumer
from runtime.remote_access.readiness import ConnectorReadiness
from runtime.remote_access.streams import StreamRegistry
from runtime.remote_access.stripping import CredentialScanner

# The THR-034 wire contract: the client presents its pairing credential in
# this header on every forwarded request (macOS ClientBridge).
DEVICE_CREDENTIAL_HEADER = "x-happyranch-device-credential"

# The THR-034 pairing-redemption route (connector-local, never forwarded).
PAIRING_PATH = "/pair"

_NOT_READY_STATUS = 503

# Addresses that would expose the DIY listener beyond the customer-owned
# network (defense in depth at the adapter boundary; the network module is
# the authority for resolution-time validation).
_FORBIDDEN_BIND_HOSTS = frozenset({"0.0.0.0", "::", ""})


class DiyProviderError(Exception):
    """The DIY adapter refused to start or serve (fail closed)."""


@dataclass(frozen=True)
class DiyProviderConfig:
    """Supported-DIY provider configuration. ``network`` carries the
    customer-owned-network resolution; ``bind_port`` is the connector
    listener port on that network."""

    network: NetworkConfig
    bind_port: int = 8443
    token_ttl_seconds: int = 300
    credential_ttl_days: int = 365

    def validate(self) -> None:
        self.network.validate()
        if (
            isinstance(self.bind_port, bool)
            or not isinstance(self.bind_port, int)
            or not (0 <= self.bind_port <= 65535)
        ):
            raise DiyProviderError("diy bind_port must be a valid TCP port")


ContextFactory = Callable[[str, RemoteRequest, datetime], GatewayContext]


class DiyProviderAdapter:
    """Readiness-gated, production Supported-DIY listener.

    ``ctx_factory(request, now)`` builds the per-request ``GatewayContext``
    from the presented device credential; ``pairing`` owns the ceremony and
    credential verification; ``gateway`` runs the locked decision order.
    """

    def __init__(
        self,
        *,
        config: DiyProviderConfig,
        readiness: ConnectorReadiness,
        pairing: PairingManager,
        identity: ConnectorIdentity,
        ctx_factory: ContextFactory,
        gateway: ConnectorGateway | None = None,
        bind_address: str | None = None,
    ) -> None:
        try:
            config.validate()
        except NetworkAddressError as exc:
            raise DiyProviderError(str(exc)) from exc
        self.config = config
        self._readiness = readiness
        self._pairing = pairing
        self._identity = identity
        self._ctx_factory = ctx_factory
        self._gateway = gateway or ConnectorGateway()
        self._bind_address = bind_address
        self._server = None
        self._thread: threading.Thread | None = None
        self._ready = False

    # ── lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> None:
        """Validate config, run readiness, resolve the customer-network bind
        address, then bind. Raises :class:`DiyProviderError` (redacted) on
        any failure — no listener. Expected operational listener failures
        (occupied port, permission, address unavailable) are normalized from
        the socket ``OSError`` to :class:`DiyProviderError` at this boundary
        so the supervisor's supervised-retry contract sees the documented
        category — never a bare ``OSError``."""
        self._validate_gating()
        report = self._readiness.evaluate(datetime.now(timezone.utc))
        if not report.ready:
            failed = ", ".join(report.failing_gates)
            raise DiyProviderError(f"readiness failed: {failed}")
        bind_host = self._resolve_bind_address()
        self._bind_address = bind_host
        try:
            from http.server import ThreadingHTTPServer

            self._server = ThreadingHTTPServer(
                (bind_host, self.config.bind_port), self._handler_factory()
            )
        except OSError as exc:
            raise DiyProviderError("diy provider failed to bind listener") from exc
        self._ready = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="hr-diy-provider", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        server = self._server
        self._ready = False
        if server is not None:
            server.shutdown()
            server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None

    @property
    def listening(self) -> bool:
        return self._ready and self._server is not None

    @property
    def bound_port(self) -> int | None:
        if self._server is None:
            return None
        return int(self._server.server_address[1])

    @property
    def bind_address(self) -> str | None:
        """The resolved customer-owned-network bind address (or None before
        a successful start)."""
        return self._bind_address

    def _resolve_bind_address(self) -> str:
        if self._bind_address is not None:
            # Explicitly supplied (acceptance/config override): still
            # strictly validated at the adapter boundary.
            from runtime.remote_access.network import validate_customer_network_address

            validate_customer_network_address(self._bind_address)
            return self._bind_address
        try:
            return resolve_customer_network_address(self.config.network)
        except NetworkAddressError as exc:
            raise DiyProviderError(f"customer network address unavailable: {exc}") from exc

    def _validate_gating(self) -> None:
        if self.config.network.mode not in {"tailscale", "explicit"}:
            raise DiyProviderError("diy provider refused: invalid network mode")
        if self.config.network.mode == "explicit" and not self.config.network.address:
            raise DiyProviderError("diy provider refused: explicit network address required")
        if self._bind_address in _FORBIDDEN_BIND_HOSTS:
            raise DiyProviderError("diy provider refused: wildcard bind address forbidden")

    # ── request pipeline ──────────────────────────────────────────────────

    def handle_request(self, request: RemoteRequest, now: datetime | None = None) -> Decision:
        """Handle one request: the ``POST /pair`` ceremony route or the full
        gateway pipeline for an authenticated forwarding request. Refuses to
        serve before readiness passed (fail closed)."""
        if not self._ready:
            raise DiyProviderError("diy adapter not ready")
        now = now or datetime.now(timezone.utc)
        if self._is_pairing_request(request):
            return self._handle_pairing(request, now)
        # The device credential is consumed at the adapter boundary: extract
        # it for the proof, then STRIP it from the forwarded request — the
        # credential must never reach the daemon on the loopback hop (the
        # outbound leak guard would fail closed on it anyway).
        credential = _extract_device_credential(request)
        stripped_request = _without_credential_header(request)
        ctx = self._ctx_factory(credential, stripped_request, now)
        return self._gateway.decide(stripped_request, ctx)

    def _is_pairing_request(self, request: RemoteRequest) -> bool:
        if request.method != "POST" or request.query is not None:
            return False
        # Strict literal ceremony route: only the exact normalized path is
        # the ceremony. Anything else (encoded variants, trailing slash,
        # query strings) flows through the gateway and is denied as an
        # unclassified route — the ceremony route is never bypassable.
        path = unquote(request.path or "")
        return path == PAIRING_PATH

    def _handle_pairing(self, request: RemoteRequest, now: datetime) -> Decision:
        """THR-034 ``POST /pair``: body is the one-time pairing code. On
        success returns ``{"credential": "hrpair_..."}`` exactly once; any
        failure (unknown/expired/consumed/blank) denies identically with
        403 — no existence oracle."""
        del now
        code = ""
        if request.body:
            try:
                code = request.body.decode("utf-8", errors="strict").strip()
            except (UnicodeDecodeError, ValueError):
                code = ""
        credential = self._pairing.redeem_pairing(code)
        if credential is None:
            return Decision(
                allowed=False,
                audit_category="pairing_denied",
                audit_detail="pairing denied",
                denied=DeniedOutcome(
                    deny_category="pairing",
                    audit_category="pairing_denied",
                    detail="pairing denied",
                    reason="single_use",
                ),
            )
        body = json.dumps({"credential": credential}).encode("utf-8")
        return Decision(
            allowed=True,
            audit_category="pairing_redeemed",
            audit_detail="pairing redeemed",
            response=ForwardedResponse(
                status=200,
                headers=(Header("Content-Type", "application/json"),),
                body=body,
            ),
        )

    # ── HTTP serving ──────────────────────────────────────────────────────

    def _handler_factory(self) -> type[BaseConnectorHandler]:
        adapter = self

        class Handler(BaseConnectorHandler):
            def serve_request(self, request: RemoteRequest, now: datetime) -> None:
                try:
                    decision = adapter.handle_request(request, now)
                except DiyProviderError:
                    send_json(self, _NOT_READY_STATUS, {"error": "not_ready"})
                    return
                serve_decision(self, decision)

        return Handler


def make_diy_context_factory(
    *,
    identity: ConnectorIdentity,
    pairing: PairingManager,
    policy: RoutePolicyConsumer,
    credential_provider: DaemonCredentialProvider,
    forwarder: LoopbackForwarder,
    registry: StreamRegistry,
    now_fn: Callable[[], datetime],
) -> ContextFactory:
    """Wire the per-request gateway context for the DIY adapter: the device
    proof is derived from the presented ``X-HappyRanch-Device-Credential``
    (THR-034 wire contract), verified by the production pairing verifier,
    then the full locked gateway pipeline runs."""

    def factory(credential: str, request: RemoteRequest, now: datetime) -> GatewayContext:
        del request
        # Resolve the presented credential to the paired device ONCE: the
        # proof then carries the resolved device identity so the bind step
        # (step 2) finds the record. An unknown/revoked/expired/removed
        # credential resolves to None and the verifier denies identically
        # (identity_unestablished) — no existence oracle.
        device = pairing.find_device_by_credential(credential)
        device_id = device.device_id if device is not None else ""
        tenant_id = device.tenant_id if device is not None else identity.tenant_id
        home_id = device.home_id if device is not None else identity.home_id
        return GatewayContext(
            connector_identity=identity,
            proof=DeviceProof(
                device_id=device_id,
                tenant_id=tenant_id,
                home_id=home_id,
                nonce=f"diy-{now.timestamp():.0f}-{len(credential or '')}",
                issued_at=now - timedelta(minutes=1),
                expires_at=now + timedelta(minutes=5),
            ),
            proof_verifier=pairing.credential_verifier(),
            single_use_guard=SingleUseGuard(),
            authorization=AuthorizationVerifier(pairing.load_state()),
            policy=policy,
            credential_provider=credential_provider,
            forwarder=forwarder,
            stream_registry=registry,
            scanner=CredentialScanner(),
            now=now,
        )

    return factory


def _extract_device_credential(request: RemoteRequest) -> str:
    for header in request.headers:
        if header.name == DEVICE_CREDENTIAL_HEADER:
            return header.value.strip()
    return ""


def _without_credential_header(request: RemoteRequest) -> RemoteRequest:
    """A copy of the request with the device-credential header removed — the
    credential is connector-consumed and must never reach the daemon."""
    headers = tuple(
        header for header in request.headers if header.name != DEVICE_CREDENTIAL_HEADER
    )
    if len(headers) == len(request.headers):
        return request
    return RemoteRequest(
        method=request.method,
        path=request.path,
        query=request.query,
        headers=headers,
        body=request.body,
        stream_type=request.stream_type,
    )


def make_diy_loopback_forwarder(daemon_port: int) -> LoopbackForwarder:
    """A forwarder that targets ONLY literal loopback 127.0.0.1 (the daemon
    remains loopback-only; the bearer is injected on the final hop)."""
    from runtime.remote_access.forwarding import HttpLoopbackForwarder

    return HttpLoopbackForwarder(LoopbackTarget(LOOPBACK_HOST, daemon_port))

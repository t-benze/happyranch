"""Literal-loopback ingress from the managed embedded-tailnet sidecar."""
from __future__ import annotations

from typing import Callable

import os
import pwd
import stat
import sys
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer

from runtime.remote_access.diy_provider import (
    ContextFactory,
    DiyProviderAdapter,
    DiyProviderConfig,
    DiyProviderError,
    _extract_device_credential,
    _without_credential_header,
)
from runtime.remote_access.authorization import AuthorizationVerdict
from runtime.remote_access.credentials import DaemonCredentialProvider
from runtime.remote_access.forwarding import LoopbackForwarder
from runtime.remote_access.gateway import GatewayContext
from runtime.remote_access.httpd import (
    BaseConnectorHandler, IdentityUnixServer, ingress_node_identity, ingress_device_label, send_json, serve_decision,
)
from runtime.remote_access.identity import ConnectorIdentity, DeviceProof, ProofVerdict, SingleUseGuard
from runtime.remote_access.models import RemoteRequest
from runtime.remote_access.network import NetworkConfig
from runtime.remote_access.pairing import PairingManager, PinnedPairingManager
from runtime.remote_access.policy import RoutePolicyConsumer
from runtime.remote_access.readiness import ConnectorReadiness
from runtime.remote_access.state_store import StateStoreError, _require_identity_text
from runtime.remote_access.streams import StreamRegistry
from runtime.remote_access.stripping import CredentialScanner

MANAGED_LOOPBACK_HOST = "127.0.0.1"
LINUX_IDENTITY_PROFILE = "linux-managed-identity-v1"
INGRESS_SOCKET = Path("/run/happyranch-connector/ingress.sock")


class ManagedProviderError(DiyProviderError):
    """Stable, redacted managed-ingress lifecycle failure."""


@dataclass(frozen=True)
class ManagedProviderConfig:
    bind_host: str = MANAGED_LOOPBACK_HOST
    bind_port: int = 8443
    token_ttl_seconds: int = 300
    credential_ttl_days: int = 365
    profile: str = ""
    # Protected connector.json input; never inferred from another identity.
    control_plane_id: str | None = None

    def validate(self, *, daemon_port: int | None = None,
                 excluded_daemon_ports: list[int] | None = None) -> None:
        if not isinstance(self.profile, str):
            raise ManagedProviderError("managed ingress profile is invalid")
        if self.control_plane_id is not None:
            try:
                _require_identity_text(self.control_plane_id)
            except StateStoreError:
                raise ManagedProviderError("managed control_plane_id is invalid") from None
        if self.profile:
            if self.profile != LINUX_IDENTITY_PROFILE or sys.platform != "linux":
                raise ManagedProviderError("managed ingress profile is invalid")
            if self.bind_host != MANAGED_LOOPBACK_HOST or self.bind_port != 8443:
                raise ManagedProviderError("identity profile has no TCP endpoint input")
            return
        if self.bind_host != MANAGED_LOOPBACK_HOST:
            raise ManagedProviderError("managed ingress requires literal loopback")
        if (
            isinstance(self.bind_port, bool)
            or not isinstance(self.bind_port, int)
            or not (0 <= self.bind_port <= 65535)
            or self.bind_port == 8765
            or self.bind_port == daemon_port
            or self.bind_port in (excluded_daemon_ports or [])
        ):
            raise ManagedProviderError("managed ingress port is invalid")


class ManagedProviderAdapter(DiyProviderAdapter):
    """Sidecar-only ingress; the Linux HRID profile uses pinned admission."""

    def __init__(
        self,
        *,
        config: ManagedProviderConfig,
        readiness: ConnectorReadiness,
        pairing: PairingManager | PinnedPairingManager,
        identity: ConnectorIdentity,
        ctx_factory: ContextFactory,
    ) -> None:
        config.validate()
        self.managed_config = config
        super().__init__(
            config=DiyProviderConfig(
                network=NetworkConfig(),
                bind_port=config.bind_port,
                token_ttl_seconds=config.token_ttl_seconds,
                credential_ttl_days=config.credential_ttl_days,
            ),
            readiness=readiness,
            pairing=pairing,
            identity=identity,
            ctx_factory=ctx_factory,
            bind_address=MANAGED_LOOPBACK_HOST,
        )

    def _validate_gating(self) -> None:
        self.managed_config.validate()

    def _resolve_bind_address(self) -> str:
        self.managed_config.validate()
        return MANAGED_LOOPBACK_HOST

    def _create_server(self, bind_host: str) -> ThreadingHTTPServer | IdentityUnixServer:
        if self.managed_config.profile != LINUX_IDENTITY_PROFILE:
            return super()._create_server(bind_host)
        # systemd creates this fixed private runtime directory as the service UID.
        # Refuse unsafe existing paths; never repair or replace foreign objects.
        path = INGRESS_SOCKET
        try:
            service_uid = pwd.getpwnam("happyranch").pw_uid
        except KeyError as exc:
            raise ManagedProviderError("managed service UID is unavailable") from exc
        if os.geteuid() != service_uid:
            raise ManagedProviderError("identity ingress requires the managed service UID")
        for parent in path.parents:
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode):
                raise ManagedProviderError("managed socket parent is invalid")
        info = path.parent.lstat()
        if info.st_uid != service_uid or stat.S_IMODE(info.st_mode) != 0o700:
            raise ManagedProviderError("managed socket parent is not private")
        return IdentityUnixServer(
            str(path), self._handler_factory(), service_uid,
            pairing=self._pairing, control_plane_id=self.managed_config.control_plane_id,
        )

    def _handler_factory(self) -> type[BaseConnectorHandler]:
        if self.managed_config.profile != LINUX_IDENTITY_PROFILE:
            return super()._handler_factory()
        adapter = self

        class Handler(BaseConnectorHandler):
            def serve_request(self, request: RemoteRequest, now: datetime) -> None:
                # No legacy ceremony/context fallback on the identity listener,
                # including configurations predating the protected field.
                if self.server.control_plane_id is None:
                    send_json(self, 503, {"error": "control_plane_id_missing"})
                    return
                if not adapter._ready or not isinstance(self.server.pairing, PinnedPairingManager):
                    send_json(self, 503, {"error": "pinned_pairing_unavailable"})
                    return
                node = ingress_node_identity.get()
                if request.method == "POST" and request.query is None and request.path in {
                    "/pair/invite", "/pair/complete",
                }:
                    try:
                        value = (request.body or b"").decode("utf-8", errors="strict")
                    except UnicodeError:
                        value = ""
                    manager = self.server.pairing
                    if request.path == "/pair/invite":
                        pending = manager.redeem(value, node, self.server.control_plane_id, now,
                                                 device_label=ingress_device_label.get())
                        payload = None if pending is None else {
                            "state": "pending-approval",
                            "completion_handle": pending.completion_handle,
                            "comparison_phrase": pending.comparison_phrase,
                            "expires_at": pending.expires_at.isoformat(),
                        }
                    else:
                        credential = manager.complete(value, node, self.server.control_plane_id, now)
                        payload = None if credential is None else {"credential": credential}
                    if payload is None:
                        send_json(self, 403, {"error": "pairing_denied"})
                    else:
                        send_json(self, 200, payload)
                    return
                credential = _extract_device_credential(request)
                stripped = _without_credential_header(request)
                ctx = adapter._ctx_factory(credential, stripped, now)
                serve_decision(self, adapter._gateway.decide(stripped, ctx))

        return Handler

    @property
    def bound_port(self) -> int | None:
        if self.managed_config.profile == LINUX_IDENTITY_PROFILE:
            return None
        return super().bound_port

    def start(self) -> None:
        try:
            super().start()
        except DiyProviderError as exc:
            raise ManagedProviderError("managed ingress unavailable") from exc


@dataclass(frozen=True)
class _PinnedRequestAuthorization:
    """Carry the current anchored admission through the existing gateway gates."""

    verdict: AuthorizationVerdict
    identity: ConnectorIdentity

    def check(self, tenant_id: str, home_id: str, device_id: str, now: datetime) -> AuthorizationVerdict:
        if ((tenant_id, home_id, device_id)
                != (self.identity.tenant_id, self.identity.home_id, self.verdict.device_id)):
            return AuthorizationVerdict(False, "identity")
        return self.verdict

    def verify(self, proof: DeviceProof, connector_identity: ConnectorIdentity, now: datetime) -> ProofVerdict:
        ok = connector_identity == self.identity and self.check(
            proof.tenant_id, proof.home_id, proof.device_id, now,
        ).ok
        return ProofVerdict(ok, None if ok else "identity_unestablished")


def make_pinned_context_factory(
    *, identity: ConnectorIdentity, pairing: PinnedPairingManager,
    control_plane_id: str | None, policy: RoutePolicyConsumer | Callable[[], RoutePolicyConsumer],
    credential_provider: DaemonCredentialProvider, forwarder: LoopbackForwarder,
    registry: StreamRegistry,
) -> ContextFactory:
    """HRID StableID + protected config + bearer, then unchanged gateway policy."""

    def factory(credential: str, request: RemoteRequest, now: datetime) -> GatewayContext:
        verdict = pairing.admit(credential, ingress_node_identity.get(), control_plane_id, now)
        authorization = _PinnedRequestAuthorization(verdict, identity)
        return GatewayContext(
            connector_identity=identity,
            proof=DeviceProof(
                device_id=verdict.device_id or "", tenant_id=identity.tenant_id,
                home_id=identity.home_id, nonce="managed-request",
                issued_at=now, expires_at=now + timedelta(minutes=1),
            ),
            proof_verifier=authorization, single_use_guard=SingleUseGuard(),
            authorization=authorization, policy=policy() if callable(policy) else policy, credential_provider=credential_provider,
            forwarder=forwarder, stream_registry=registry, scanner=CredentialScanner(),
            now=now, pinned_admission=verdict,
        )

    return factory

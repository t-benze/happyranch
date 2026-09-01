"""Literal-loopback ingress from the managed embedded-tailnet sidecar."""
from __future__ import annotations

from dataclasses import dataclass

from runtime.remote_access.diy_provider import (
    ContextFactory,
    DiyProviderAdapter,
    DiyProviderConfig,
    DiyProviderError,
)
from runtime.remote_access.identity import ConnectorIdentity
from runtime.remote_access.network import NetworkConfig
from runtime.remote_access.pairing import PairingManager
from runtime.remote_access.readiness import ConnectorReadiness

MANAGED_LOOPBACK_HOST = "127.0.0.1"


class ManagedProviderError(DiyProviderError):
    """Stable, redacted managed-ingress lifecycle failure."""


@dataclass(frozen=True)
class ManagedProviderConfig:
    bind_host: str = MANAGED_LOOPBACK_HOST
    bind_port: int = 8443
    token_ttl_seconds: int = 300
    credential_ttl_days: int = 365

    def validate(self) -> None:
        if self.bind_host != MANAGED_LOOPBACK_HOST:
            raise ManagedProviderError("managed ingress requires literal loopback")
        if (
            isinstance(self.bind_port, bool)
            or not isinstance(self.bind_port, int)
            or not (0 <= self.bind_port <= 65535)
            or self.bind_port == 8765
        ):
            raise ManagedProviderError("managed ingress port is invalid")


class ManagedProviderAdapter(DiyProviderAdapter):
    """Existing connector gateway exposed only to the local raw-TCP sidecar."""

    def __init__(
        self,
        *,
        config: ManagedProviderConfig,
        readiness: ConnectorReadiness,
        pairing: PairingManager,
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

    def start(self) -> None:
        try:
            super().start()
        except DiyProviderError as exc:
            raise ManagedProviderError("managed ingress unavailable") from exc

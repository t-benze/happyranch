"""Literal-loopback ingress from the managed embedded-tailnet sidecar."""
from __future__ import annotations

import os
import pwd
import stat
import sys
from pathlib import Path
from dataclasses import dataclass
from http.server import ThreadingHTTPServer

from runtime.remote_access.diy_provider import (
    ContextFactory,
    DiyProviderAdapter,
    DiyProviderConfig,
    DiyProviderError,
)
from runtime.remote_access.httpd import IdentityUnixServer
from runtime.remote_access.identity import ConnectorIdentity
from runtime.remote_access.network import NetworkConfig
from runtime.remote_access.pairing import PairingManager
from runtime.remote_access.readiness import ConnectorReadiness

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

    def validate(self, *, daemon_port: int | None = None,
                 excluded_daemon_ports: list[int] | None = None) -> None:
        if not isinstance(self.profile, str):
            raise ManagedProviderError("managed ingress profile is invalid")
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
        return IdentityUnixServer(str(path), self._handler_factory(), service_uid)

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

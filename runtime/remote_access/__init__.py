"""Portable supervised connector core (THR-097 merge unit C / TASK-5724 phase
unit 2).

A supervised portable Python companion skeleton: strict request parsing and
canonical normalization, a versioned route-policy consumer with fail-closed
drift behavior, connector identity + device-proof verifier seam, current
authorization/revocation checking with live-stream closure, remote-auth and
hop-by-hop stripping, a daemon-credential-provider seam, and a forwarding
abstraction that can target only literal loopback 127.0.0.1.

This is a skeleton/core with a loopback-only test harness: no tailnet or
externally reachable bind, no Headscale/DERP/Services integration, no durable
persistence, and no service packaging.
"""
from __future__ import annotations

from runtime.remote_access.authorization import (
    AuthorizationVerifier,
    DeviceAuthorization,
    RevocationSignal,
    TrustState,
)
from runtime.remote_access.credentials import (
    CredentialUnavailable,
    DaemonCredentialProvider,
    FileDaemonCredentialProvider,
    StaticDaemonCredentialProvider,
    SystemdCredentialProvider,
)
from runtime.remote_access.diy_provider import DiyProviderAdapter, DiyProviderConfig, DiyProviderError
from runtime.remote_access.gateway import ConnectorGateway, GatewayContext
from runtime.remote_access.httpd import BaseConnectorHandler
from runtime.remote_access.identity import (
    ConnectorIdentity,
    DeviceProof,
    DeviceProofVerifier,
    ProofVerdict,
    ReplayGuardingVerifier,
    SingleUseGuard,
    StaticProofVerifier,
)
from runtime.remote_access.models import (
    Decision,
    DeniedOutcome,
    ForwardedResponse,
    Header,
    LoopbackViolation,
    NormalizedTarget,
    RemoteRequest,
)
from runtime.remote_access.network import NetworkConfig, NetworkAddressError
from runtime.remote_access.pairing import PairingCredentialVerifier, PairingManager
from runtime.remote_access.revocation import RevocationCoordinator, RevocationIncomplete
from runtime.remote_access.state import InMemoryTrustStateStore, TrustStateStore
from runtime.remote_access.state_store import (
    AtomicFileTrustStateStore,
    CorruptTrustStateError,
    StateStoreError,
)
from runtime.remote_access.streams import StreamClosed, StreamHandle, StreamRegistry

__all__ = [
    "AtomicFileTrustStateStore",
    "AuthorizationVerifier",
    "ConnectorGateway",
    "ConnectorIdentity",
    "CorruptTrustStateError",
    "CredentialUnavailable",
    "DaemonCredentialProvider",
    "Decision",
    "DeniedOutcome",
    "DeviceAuthorization",
    "DeviceProof",
    "DeviceProofVerifier",
    "DiyProviderAdapter",
    "DiyProviderConfig",
    "DiyProviderError",
    "FileDaemonCredentialProvider",
    "ForwardedResponse",
    "GatewayContext",
    "Header",
    "InMemoryTrustStateStore",
    "LoopbackViolation",
    "NetworkAddressError",
    "NetworkConfig",
    "NormalizedTarget",
    "PairingCredentialVerifier",
    "PairingManager",
    "ProofVerdict",
    "RemoteRequest",
    "ReplayGuardingVerifier",
    "RevocationCoordinator",
    "RevocationIncomplete",
    "RevocationSignal",
    "SingleUseGuard",
    "StateStoreError",
    "StaticDaemonCredentialProvider",
    "StaticProofVerifier",
    "StreamClosed",
    "StreamHandle",
    "StreamRegistry",
    "SystemdCredentialProvider",
    "TrustState",
    "TrustStateStore",
]

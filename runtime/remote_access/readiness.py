"""Readiness gates (THR-097 phase unit 3).

Fixed invariant: **readiness must expose no listener unless daemon loopback
reachability, credential permissions, current policy, bind identity, and
non-corrupt trust state all pass.** Every gate fails closed with a stable,
secret-free category; one failed gate means not-ready and the supervisor
binds no listener.

- ``daemon_loopback``: TCP connect to **literal 127.0.0.1** at the configured
  daemon port. The connect implementation is shared and refuses any other
  host — the daemon stays loopback-only and the connector only ever touches
  it on the final loopback hop.
- ``credential_permissions``: the daemon credential provider (file ``0600``
  owner-only or systemd ``LoadCredential=`` injection) can read a non-empty
  bearer. The value is never stored or rendered.
- ``current_policy``: the versioned route-policy consumer is present and
  ``require_current`` passes (empty/malformed/stale/future/rollback/compiler/
  apply-failed all fail closed).
- ``bind_identity``: the configured connector identity is present and the
  loaded trust state's connector identity matches it exactly (a mismatch is
  ambiguous and fails closed).
- ``trust_state``: the trust-state store loads a non-corrupt state; a
  present-but-corrupt/loose/unreadable state fails closed (corruption could
  hide a revocation). Missing state is first-run (fresh deny-all), not
  corruption.
"""
from __future__ import annotations

import socket
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from runtime.remote_access.credentials import (
    CredentialUnavailable,
    DaemonCredentialProvider,
)
from runtime.remote_access.authorization import TrustState
from runtime.remote_access.forwarding import LOOPBACK_HOST
from runtime.remote_access.identity import ConnectorIdentity
from runtime.remote_access.policy import PolicyError, RoutePolicyConsumer
from runtime.remote_access.state import TrustStateStore
from runtime.remote_access.state_store import CorruptTrustStateError, StateStoreError

DEFAULT_CONNECT_TIMEOUT = 1.0


def connect_loopback(port: int, *, timeout: float = DEFAULT_CONNECT_TIMEOUT, host: str = LOOPBACK_HOST) -> None:
    """Probe daemon reachability on literal loopback.

    ``host`` is part of the signature so the literal-loopback guarantee is
    exercised by tests, but the implementation refuses any value other than
    ``127.0.0.1``: the daemon is reachable ONLY on the loopback hop and the
    readiness probe must never touch another interface.
    """
    if host != LOOPBACK_HOST:
        raise ValueError(f"daemon reachability probe requires 127.0.0.1, got {host!r}")
    with socket.create_connection((LOOPBACK_HOST, port), timeout=timeout):
        return None


ConnectFn = Callable[[int], None]


@dataclass(frozen=True)
class GateResult:
    """One readiness gate outcome. ``detail`` is stable, category-level
    prose — never the bearer, paths, input, or exception text."""

    ok: bool
    category: str
    detail: str


@dataclass(frozen=True)
class ReadinessReport:
    """The complete readiness evaluation; ``ready`` is true only when every
    gate passes."""

    ready: bool
    gates: dict[str, GateResult] = field(default_factory=dict)

    @property
    def failing_gates(self) -> tuple[str, ...]:
        return tuple(sorted(name for name, gate in self.gates.items() if not gate.ok))


class ConnectorReadiness:
    """The five-gate readiness evaluator. No listener may be exposed unless
    every gate passes (the supervisor consults ``evaluate`` before binding)."""

    GATE_NAMES = ("daemon_loopback", "credential_permissions", "current_policy", "bind_identity", "trust_state")

    def __init__(
        self,
        *,
        daemon_port: int | None,
        credential_provider: DaemonCredentialProvider,
        policy: RoutePolicyConsumer | None,
        policy_failure_category: str | None = None,
        configured_identity: ConnectorIdentity | None,
        state_store: TrustStateStore | None,
        connect_fn: ConnectFn | None = None,
    ) -> None:
        self._daemon_port = daemon_port
        self._credential_provider = credential_provider
        self._policy = policy
        self._policy_failure_category = policy_failure_category
        self._configured_identity = configured_identity
        self._state_store = state_store
        self._connect_fn = connect_fn or (lambda port: connect_loopback(port))
        self._loaded_state: TrustState | None = None

    def evaluate(self, now: datetime) -> ReadinessReport:
        gates: dict[str, GateResult] = {}
        gates["daemon_loopback"] = self._gate_daemon_loopback()
        gates["trust_state"] = self._gate_trust_state()
        gates["credential_permissions"] = self._gate_credential_permissions()
        gates["current_policy"] = self._gate_current_policy(now)
        gates["bind_identity"] = self._gate_bind_identity()
        return ReadinessReport(
            ready=all(gate.ok for gate in gates.values()), gates=gates
        )

    # ── gate 1: daemon loopback reachability (literal 127.0.0.1) ─────────

    def _gate_daemon_loopback(self) -> GateResult:
        port = self._daemon_port
        if port is None or not isinstance(port, int) or isinstance(port, bool) or not (0 <= port <= 65535):
            return GateResult(False, "daemon_unavailable", "daemon port not configured")
        try:
            self._connect_fn(port)
        except (OSError, ValueError):
            return GateResult(False, "daemon_unavailable", "daemon not reachable on loopback")
        return GateResult(True, "daemon_loopback_ok", "daemon reachable on 127.0.0.1")

    # ── gate 2: credential permissions (provider fail-closed read) ───────

    def _gate_credential_permissions(self) -> GateResult:
        try:
            self._credential_provider.read_bearer()
        except CredentialUnavailable:
            return GateResult(False, "credential_unreadable", "daemon credential missing or unreadable")
        except Exception:  # noqa: BLE001 — any provider failure fails closed
            return GateResult(False, "credential_unreadable", "daemon credential unavailable")
        return GateResult(True, "credential_ok", "daemon credential readable with safe permissions")

    # ── gate 3: current policy (present + well-formed + current) ─────────

    def _gate_current_policy(self, now: datetime) -> GateResult:
        if self._policy_failure_category is not None:
            return GateResult(
                False,
                self._policy_failure_category,
                "route policy malformed or unreadable",
            )
        policy = self._policy
        if policy is None:
            return GateResult(False, "policy_missing", "no route policy configured")
        try:
            policy.require_current(now)
        except PolicyError as exc:
            category = exc.outcome.audit_category if exc.outcome is not None else "policy_denied"
            return GateResult(False, category, "route policy not current")
        return GateResult(True, "policy_current", "route policy present and current")

    # ── gate 4: bind identity (configured vs trust-state consistency) ────

    def _gate_bind_identity(self) -> GateResult:
        configured = self._configured_identity
        if configured is None:
            return GateResult(False, "identity_denied", "connector identity not configured")
        loaded = self._loaded_state
        if loaded is None or loaded.connector_identity is None:
            return GateResult(False, "identity_denied", "trust state carries no connector identity")
        actual = loaded.connector_identity
        if (
            actual.tenant_id != configured.tenant_id
            or actual.home_id != configured.home_id
            or actual.connector_id != configured.connector_id
        ):
            return GateResult(False, "identity_mismatch", "trust state identity does not match configuration")
        return GateResult(True, "identity_ok", "connector bind identity consistent")

    # ── gate 5: non-corrupt trust state ──────────────────────────────────

    def _gate_trust_state(self) -> GateResult:
        store = self._state_store
        if store is None:
            return GateResult(False, "state_unavailable", "no trust state store configured")
        try:
            self._loaded_state = store.load()
        except CorruptTrustStateError:
            return GateResult(False, "state_corrupt", "trust state corrupt or invalid")
        except StateStoreError:
            return GateResult(False, "state_unavailable", "trust state unreadable")
        return GateResult(True, "state_ok", "trust state loadable and non-corrupt")

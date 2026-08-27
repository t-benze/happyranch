"""ConnectorGateway — the locked request decision pipeline (contract §6.1).

Every request/stream runs these steps in this exact order:

  authenticate (identity/device proof) -> bind (revocation/pairing) ->
  proof (freshness) -> policy (current) -> normalize (exactly once) ->
  allowlist (explicit allow) -> strip (remote auth/hop-by-hop) ->
  bearer (read daemon bearer) -> forward solely to 127.0.0.1

Any step failure denies fail-closed with a stable, tenant-neutral category.
The forwarding boundary additionally normalizes every forward/open/stream
failure (connection refused, timeouts, HTTP parse errors, hostile exception
text) into the stable Unit-A categories and deterministically closes partial
response/stream resources — raw exception strings, the daemon bearer, and
tenant/home/device identifiers can never escape to any decision surface.
Ordering mutations and guard removals change the security outcome — the
checked-in mutation battery proves it.
"""
from __future__ import annotations

import http.client
import uuid
from dataclasses import dataclass
from datetime import datetime

from runtime.remote_access.allowlist import template_matches
from runtime.remote_access.audit import ALLOWED_DETAIL, detail_for, redact_exception
from runtime.remote_access.authorization import AuthorizationVerifier, AuthorizationVerdict
from runtime.remote_access.credentials import CredentialUnavailable, DaemonCredentialProvider
from runtime.remote_access.forwarding import LOOPBACK_HOST, LoopbackForwarder, OutboundLeakError
from runtime.remote_access.identity import (
    ConnectorIdentity,
    DeviceProof,
    DeviceProofVerifier,
    SingleUseGuard,
)
from runtime.remote_access.models import (
    Decision,
    DeniedOutcome,
    ForwardedResponse,
    Header,
    NormalizedTarget,
    RemoteRequest,
)
from runtime.remote_access.normalization import NormalizationError, normalize_request
from runtime.remote_access.policy import PolicyError, RoutePolicyConsumer
from runtime.remote_access.streams import StreamClosed, StreamRegistry
from runtime.remote_access.stripping import FramingError, CredentialScanner, reject_remote_credentials, strip_remote_headers


def _deny(deny_category: str, audit_category: str, reason: str | None = None) -> DeniedOutcome:
    return DeniedOutcome(
        deny_category=deny_category,
        audit_category=audit_category,
        detail=detail_for(deny_category, audit_category, reason),
        reason=reason,
    )


def _forward_failure(exc: BaseException) -> DeniedOutcome:
    """Map forward/open/stream failures to stable, tenant-neutral denials.

    Transport failures at the loopback boundary (connection refused/reset,
    timeouts, HTTP parse errors) map to the normative local-daemon unavailable
    category; credential-shaped outbound material is its own category; any
    other failure is redacted to internal. Raw exception text never survives.
    """
    if isinstance(exc, OutboundLeakError):
        return _deny("local_daemon", "local_daemon_denied", "credential_shaped")
    if isinstance(exc, (ConnectionError, TimeoutError, OSError, http.client.HTTPException)):
        return _deny("local_daemon", "daemon_unavailable", "unavailable")
    return redact_exception(exc)


def _close_partial(handle) -> None:
    """Deterministically close a partially opened stream/resource; close
    failures are swallowed (the denial is already determined and the handle is
    dropped from the registry, so no live stream survives)."""
    try:
        handle.close()
    except Exception:
        pass


@dataclass(frozen=True)
class GatewayContext:
    """Everything the gateway needs to decide one request/stream."""

    connector_identity: ConnectorIdentity
    proof: DeviceProof | None
    proof_verifier: DeviceProofVerifier
    single_use_guard: SingleUseGuard
    authorization: AuthorizationVerifier
    policy: RoutePolicyConsumer
    credential_provider: DaemonCredentialProvider
    forwarder: LoopbackForwarder
    stream_registry: StreamRegistry
    scanner: CredentialScanner
    now: datetime


@dataclass
class PipelineState:
    """Intermediate values produced along the ordered pipeline."""

    normalized: NormalizedTarget | None = None
    headers: tuple[Header, ...] = ()
    bearer: str | None = None


class ConnectorGateway:
    """The ordered decision pipeline. Step order is load-bearing; the checked-in
    mutation battery proves reordering/removal changes the security outcome."""

    _STEP_ORDER: tuple[str, ...] = (
        "authenticate",
        "bind",
        "proof",
        "policy",
        "normalize",
        "allowlist",
        "strip",
        "bearer",
    )

    # Mutation-test seams: flipping these must be detected by the invariant
    # battery.
    NORMALIZATION_ENABLED = True
    ALLOWLIST_ENABLED = True
    AUTHORIZATION_ENABLED = True
    PROOF_ENABLED = True

    # ── public entry points ──────────────────────────────────────────────

    def decide(self, request: RemoteRequest, ctx: GatewayContext) -> Decision:
        """Run the full ordered pipeline; returns a Decision for HTTP or an
        opened stream for SSE/WebSocket."""
        state = PipelineState()
        for step in self._STEP_ORDER:
            denied = getattr(self, f"_step_{step}")(request, ctx, state)
            if denied is not None:
                return self._redact(denied)
        return self._forward(request, ctx, state)

    # ── step 1: authenticate (connector identity + device proof) ─────────

    def _step_authenticate(
        self, request: RemoteRequest, ctx: GatewayContext, state: PipelineState
    ) -> DeniedOutcome | None:
        del request, state
        if not self.PROOF_ENABLED:
            return None  # mutation seam
        if ctx.connector_identity is None:
            return _deny("identity", "identity_denied", "ambiguous")
        proof = ctx.proof
        if proof is None:
            return _deny("identity", "identity_denied", "missing_proof")
        if proof.binding_id is not None:
            outcome = ctx.single_use_guard.check(proof.binding_id)
            if outcome is not None:
                return outcome
        try:
            verdict = ctx.proof_verifier.verify(proof, ctx.connector_identity, ctx.now)
        except Exception as exc:
            return redact_exception(exc)
        if verdict.ok:
            return None
        return {
            "expired": _deny("expiry", "credential_expired", "expired"),
            "replayed": _deny("replay", "replay_denied", "replayed"),
            "wrong_audience": _deny("identity", "audience_denied", "audience"),
            "wrong_home": _deny("identity", "home_denied", "home"),
            "identity_unestablished": _deny("identity", "identity_denied", "ambiguous"),
        }.get(verdict.reason, _deny("identity", "identity_denied", "ambiguous"))

    # ── step 2: bind (tenant/home/device binding, current pairing) ───────

    def _step_bind(
        self, request: RemoteRequest, ctx: GatewayContext, state: PipelineState
    ) -> DeniedOutcome | None:
        del request, state
        if not self.AUTHORIZATION_ENABLED:
            return None  # mutation seam
        proof = ctx.proof
        if proof is None:
            return _deny("identity", "identity_denied", "missing_proof")
        verdict: AuthorizationVerdict = ctx.authorization.check(
            proof.tenant_id, proof.home_id, proof.device_id, ctx.now
        )
        if verdict.ok:
            return None
        return {
            "identity": _deny("identity", "identity_denied", "binding"),
            "pairing": _deny("pairing", "pairing_denied", "pairing"),
            "current_device": _deny("current_device", "device_mismatch_denied", "stale_device"),
            "revocation": _deny("revocation", "revocation_denied", "revoked"),
        }.get(verdict.reason, _deny("identity", "identity_denied", "binding"))

    # ── step 3: proof freshness (non-expired window at decision time) ────

    def _step_proof(
        self, request: RemoteRequest, ctx: GatewayContext, state: PipelineState
    ) -> DeniedOutcome | None:
        del request, state
        proof = ctx.proof
        if proof is None:
            return _deny("identity", "identity_denied", "missing_proof")
        if ctx.now > proof.expires_at or ctx.now < proof.issued_at:
            return _deny("expiry", "credential_expired", "expired")
        return None

    # ── step 4: policy (present, well-formed, current) ───────────────────

    def _step_policy(
        self, request: RemoteRequest, ctx: GatewayContext, state: PipelineState
    ) -> DeniedOutcome | None:
        del request, state
        try:
            ctx.policy.require_current(ctx.now)
        except PolicyError as exc:
            return exc.outcome
        return None

    # ── step 5: normalize (exactly once, deny ambiguity) ─────────────────

    def _step_normalize(
        self, request: RemoteRequest, ctx: GatewayContext, state: PipelineState
    ) -> DeniedOutcome | None:
        del ctx
        if not self.NORMALIZATION_ENABLED:
            state.normalized = NormalizedTarget(
                method=request.method,
                path=request.path,
                query=request.query,
                collapsed=False,
                raw_path=request.path,
            )
            return None  # mutation seam
        try:
            state.normalized = normalize_request(request)
        except NormalizationError as exc:
            reason = (
                "duplicate_slash" if exc.reason == "duplicate_slash_ambiguity" else "default"
            )
            return _deny("normalization", "normalization_denied", reason)
        return None

    # ── step 6: allow-list (explicit allow, deny unclassified) ───────────

    def _step_allowlist(
        self, request: RemoteRequest, ctx: GatewayContext, state: PipelineState
    ) -> DeniedOutcome | None:
        if not self.ALLOWLIST_ENABLED:
            return None  # mutation seam
        target = state.normalized
        if target is None:
            return _deny("normalization", "normalization_denied", "default")
        entry = ctx.policy.allowlist.match(target.method, target.path)
        if target.collapsed:
            raw_match = ctx.policy.allowlist.match(target.method, target.raw_path)
            if bool(raw_match) != bool(entry) or (raw_match is not None and raw_match != entry):
                return _deny("normalization", "normalization_denied", "duplicate_slash")
        if entry is None:
            # The remote default is deny-unclassified; any other declared
            # default is a policy mutation that would authorize unclassified
            # routes — the checked-in mutation battery proves the battery
            # catches it. (deny-unclassified is the only safe value.)
            if ctx.policy.default_behavior != "deny_unclassified":
                return None  # mutation seam: non-deny default allows unclassified
            # Method-awareness: an allowed template permits only its listed
            # methods; unsupported methods on an allowed path are method-denied.
            if ctx.policy.allowlist.match_any_method(target.path):
                return _deny("method", "method_denied", "default")
            if ctx.policy.is_forbidden(target.method, target.path) is not None:
                return _deny("route", "route_denied", "forbidden")
            return _deny("route", "unclassified_denied", "unclassified")

        # Upgrade semantics (HTTP/SSE/WebSocket distinctions).
        upgrade_headers = {h.name for h in request.headers}
        if request.stream_type == "websocket":
            if target.method == "GET" and any(
                template_matches(target.path, t) for t in ctx.policy.websocket_allowed_templates
            ):
                return None
            return _deny("method", "method_denied", "upgrade")
        if request.stream_type == "sse":
            sse_ok = target.method == "GET" and any(
                template_matches(target.path, t) for t in ctx.policy.sse_allowed_templates
            )
            if not sse_ok:
                return _deny("method", "method_denied", "upgrade")
            if request.body:
                return _deny("method", "method_denied", "body")
            return None
        if request.stream_type != "http":
            return _deny("method", "method_denied", "upgrade")
        if "upgrade" in upgrade_headers:
            return _deny("method", "method_denied", "upgrade")
        return None

    # ── step 7: strip remote auth / hop-by-hop; reject smuggling ─────────

    def _step_strip(
        self, request: RemoteRequest, ctx: GatewayContext, state: PipelineState
    ) -> DeniedOutcome | None:
        try:
            stripped = strip_remote_headers(request.headers)
        except FramingError as exc:
            return exc.outcome
        state.headers = stripped
        outcome = reject_remote_credentials(request, bearer=None, stripped=stripped)
        if outcome is not None:
            return outcome
        return None

    # ── step 8: bearer (read the daemon bearer; verify loopback target) ──

    def _step_bearer(
        self, request: RemoteRequest, ctx: GatewayContext, state: PipelineState
    ) -> DeniedOutcome | None:
        if ctx.forwarder.target.host != LOOPBACK_HOST:
            return _deny("local_daemon", "daemon_bind_mismatch", "bind_mismatch")
        try:
            bearer = ctx.credential_provider.read_bearer()
        except CredentialUnavailable:
            return _deny("local_daemon", "daemon_unavailable", "unavailable")
        # Defense in depth at the correct ordering point: with the bearer now
        # known, scan remote input for the exact value (LOCAL-001).
        outcome = reject_remote_credentials(request, bearer=bearer, stripped=state.headers)
        if outcome is not None:
            return outcome
        state.bearer = bearer
        return None

    # ── forward: solely to 127.0.0.1 with the bearer on the final hop ────

    def _forward(self, request: RemoteRequest, ctx: GatewayContext, state: PipelineState) -> Decision:
        target = state.normalized
        bearer = state.bearer
        if target is None or bearer is None:
            return self._redact(_deny("internal", "internal_error", "internal"))
        stream_id = f"connector-{uuid.uuid4().hex[:16]}"
        handle = None
        tracked = None
        try:
            # Every request/stream opens through the registry so revocation can
            # close in-flight HTTP/SSE/WebSocket exchanges immediately. The
            # registry returns its tracked wrapper — the only stream surface
            # that ever escapes the gateway — so a revocation can seal it
            # irrevocably even if the underlying transport close raises.
            handle = ctx.forwarder.open_stream(
                target.method, target.path, target.query, state.headers, request.body, bearer, stream_id
            )
            try:
                tracked = ctx.stream_registry.open(stream_id, handle)
            except StreamClosed:
                # The seal already linearized: the admission was NOT fully
                # registered before it, so the registry failed it closed —
                # the allocated transport is owned and already closed by the
                # registry (never leaked, never returned). The denial is the
                # normative revocation category.
                return self._redact(_deny("revocation", "revocation_stream_closed", "revoked"))
            if request.stream_type == "http":
                try:
                    chunks: list[bytes] = []
                    while True:
                        chunk = tracked.receive()
                        if chunk is None:
                            break
                        chunks.append(chunk)
                    response = ForwardedResponse(status=tracked.status, headers=tracked.headers, body=b"".join(chunks))
                    ctx.stream_registry.close(stream_id)
                    return Decision(
                        allowed=True,
                        audit_category="allowed_request",
                        audit_detail=ALLOWED_DETAIL,
                        response=response,
                    )
                except StreamClosed:
                    return self._redact(_deny("revocation", "revocation_stream_closed", "revoked"))
                except Exception as exc:
                    # Mid-stream receive failure (connection reset, HTTP parse
                    # error, ...): drop the partial stream deterministically and
                    # normalize the denial.
                    try:
                        ctx.stream_registry.close(stream_id)
                    except Exception:
                        pass
                    _close_partial(handle)
                    return self._redact(_forward_failure(exc))
            return Decision(
                allowed=True,
                audit_category="allowed_request",
                audit_detail=ALLOWED_DETAIL,
                stream=tracked,
            )
        except OutboundLeakError:
            return self._redact(_deny("local_daemon", "local_daemon_denied", "credential_shaped"))
        except Exception as exc:
            # Forward/open failure (connection refused, timeout, HTTP parse
            # error, hostile exception text, ...): normalize to a stable
            # category and close any partially opened resources.
            if handle is not None:
                _close_partial(handle)
            return self._redact(_forward_failure(exc))

    def _redact(self, outcome: DeniedOutcome) -> Decision:
        return Decision(
            allowed=False,
            audit_category=outcome.audit_category,
            audit_detail=outcome.detail,
            denied=outcome,
        )

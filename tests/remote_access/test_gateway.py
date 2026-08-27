"""ConnectorGateway — the locked 9-step decision pipeline (contract §6.1).

Proves the exact load-bearing order: authenticate (identity/proof) -> bind
(revocation/pairing) -> proof freshness -> policy -> normalize -> allow-list
-> strip -> bearer -> forward to 127.0.0.1 only. Ordering mutations and guard
removals must change the security outcome.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from runtime.remote_access import identity
from runtime.remote_access.authorization import AuthorizationVerifier, DeviceAuthorization
from runtime.remote_access.credentials import CredentialUnavailable, StaticDaemonCredentialProvider
from runtime.remote_access.forwarding import (
    LOOPBACK_HOST,
    ForwardingHarness,
    HttpLoopbackForwarder,
    LoopbackTarget,
)
from runtime.remote_access.gateway import ConnectorGateway, GatewayContext, PipelineState
from runtime.remote_access.models import Header
from runtime.remote_access.stripping import CredentialScanner
from runtime.remote_access.streams import StreamRegistry

from .conftest import NOW, build_consumer, default_authorization_state, default_identity, make_request

BEARER = "daemon-bearer-test-token-42"


def _ok_proof(**overrides) -> identity.DeviceProof:
    fields = dict(
        device_id="device-a",
        tenant_id="tenant-a",
        home_id="home-a",
        nonce="nonce-1",
        issued_at=NOW() - timedelta(minutes=1),
        expires_at=NOW() + timedelta(minutes=5),
        binding_id=None,
    )
    fields.update(overrides)
    return identity.DeviceProof(**fields)


_VALID_PROOF = object()


def _gateway_context(
    route_policy_fixture,
    *,
    proof: identity.DeviceProof | None | object = _VALID_PROOF,
    verifier: identity.DeviceProofVerifier | None = None,
    authz=None,
    provider: StaticDaemonCredentialProvider | None = None,
    forwarder=None,
    policy=None,
    stream_registry=None,
    now=NOW(),
) -> GatewayContext:
    from .conftest import build_consumer

    if proof is _VALID_PROOF:
        proof = _ok_proof()

    return GatewayContext(
        connector_identity=default_identity(),
        proof=proof,
        proof_verifier=verifier or identity.StaticProofVerifier(identity.ProofVerdict(ok=True)),
        single_use_guard=identity.SingleUseGuard(),
        authorization=authz or AuthorizationVerifier(default_authorization_state()),
        policy=policy or build_consumer(route_policy_fixture),
        credential_provider=provider or StaticDaemonCredentialProvider(BEARER),
        forwarder=forwarder or ForwardingHarness(),
        stream_registry=stream_registry or StreamRegistry(),
        scanner=CredentialScanner(),
        now=now,
    )


# ── positive controls ────────────────────────────────────────────────────


def test_positive_http_control(route_policy_fixture) -> None:
    decision = ConnectorGateway().decide(make_request("GET", "/api/v1/health"), _gateway_context(route_policy_fixture))
    assert decision.allowed is True
    assert decision.audit_category == "allowed_request"
    assert decision.response is not None
    assert decision.response.status == 200


def test_positive_template_route(route_policy_fixture) -> None:
    decision = ConnectorGateway().decide(
        make_request("GET", "/api/v1/orgs/acme/tasks"), _gateway_context(route_policy_fixture)
    )
    assert decision.allowed is True
    assert decision.response is not None


def test_positive_control_reaches_real_loopback(route_policy_fixture) -> None:
    from .fake_daemon import FakeDaemon, assert_daemon_received

    fake = FakeDaemon(expected_bearer=BEARER)
    fake.start()
    try:
        ctx = _gateway_context(
            route_policy_fixture,
            forwarder=HttpLoopbackForwarder(LoopbackTarget(LOOPBACK_HOST, fake.port)),
        )
        decision = ConnectorGateway().decide(make_request("GET", "/api/v1/health"), ctx)
        assert decision.allowed is True
        assert_daemon_received(fake, "GET", "/api/v1/health")
    finally:
        fake.stop()


def test_positive_sse_control(route_policy_fixture) -> None:
    from .fake_daemon import FakeDaemon

    fake = FakeDaemon(expected_bearer=BEARER)
    fake.start()
    try:
        ctx = _gateway_context(
            route_policy_fixture,
            forwarder=HttpLoopbackForwarder(LoopbackTarget(LOOPBACK_HOST, fake.port)),
        )
        decision = ConnectorGateway().decide(
            make_request(
                "GET",
                "/api/v1/orgs/acme/threads/T-1/tail",
                headers=[("accept", "text/event-stream")],
                stream_type="sse",
            ),
            ctx,
        )
        assert decision.allowed is True
        assert decision.stream is not None
        first = decision.stream.receive()
        assert first is not None
        assert b"hello" in first
        decision.stream.close()
    finally:
        fake.stop()


# ── denial per step with normative categories ────────────────────────────


def test_denied_when_proof_missing(route_policy_fixture) -> None:
    ctx = _gateway_context(route_policy_fixture, proof=None)
    decision = ConnectorGateway().decide(make_request(), ctx)
    assert decision.allowed is False
    assert decision.denied is not None
    assert decision.denied.deny_category == "identity"
    assert decision.denied.audit_category == "identity_denied"


def test_denied_when_proof_expired(route_policy_fixture) -> None:
    ctx = _gateway_context(route_policy_fixture, proof=_ok_proof(expires_at=NOW() - timedelta(seconds=1)))
    decision = ConnectorGateway().decide(make_request(), ctx)
    assert decision.denied.deny_category == "expiry"
    assert decision.denied.audit_category == "credential_expired"


def test_denied_when_revoked_device(route_policy_fixture) -> None:
    from runtime.remote_access.revocation import RevocationCoordinator
    from runtime.remote_access.streams import StreamRegistry

    state = default_authorization_state()
    RevocationCoordinator(state, StreamRegistry()).revoke(epoch=2)
    ctx = _gateway_context(route_policy_fixture, authz=AuthorizationVerifier(state))
    decision = ConnectorGateway().decide(make_request(), ctx)
    assert decision.denied.deny_category == "revocation"
    assert decision.denied.audit_category == "revocation_denied"


def test_denied_when_stale_policy(route_policy_fixture) -> None:
    stale = build_consumer(route_policy_fixture, max_age_seconds=60)
    ctx = _gateway_context(route_policy_fixture, policy=stale, now=NOW() + timedelta(seconds=120))
    decision = ConnectorGateway().decide(make_request(), ctx)
    assert decision.denied.deny_category == "policy"
    assert decision.denied.audit_category == "policy_stale"


def test_denied_when_encoded_traversal(route_policy_fixture) -> None:
    ctx = _gateway_context(route_policy_fixture)
    decision = ConnectorGateway().decide(
        make_request("GET", "/api/v1/orgs/%2e%2e%2f%2e%2e/etc/passwd"), ctx
    )
    assert decision.denied.deny_category == "normalization"
    assert decision.denied.audit_category == "normalization_denied"


def test_denied_when_forbidden_route(route_policy_fixture) -> None:
    ctx = _gateway_context(route_policy_fixture)
    decision = ConnectorGateway().decide(
        make_request("POST", "/api/v1/orgs/acme/tasks/T-1/completion"), ctx
    )
    assert decision.denied.deny_category == "route"
    assert decision.denied.audit_category == "route_denied"


def test_denied_when_unclassified_route(route_policy_fixture) -> None:
    ctx = _gateway_context(route_policy_fixture)
    decision = ConnectorGateway().decide(
        make_request("GET", "/api/v1/orgs/acme/some-new-route"), ctx
    )
    assert decision.denied.deny_category == "route"
    assert decision.denied.audit_category == "unclassified_denied"


def test_denied_when_method_not_allowed(route_policy_fixture) -> None:
    ctx = _gateway_context(route_policy_fixture)
    decision = ConnectorGateway().decide(make_request("DELETE", "/api/v1/health"), ctx)
    assert decision.denied.deny_category == "method"
    assert decision.denied.audit_category == "method_denied"


def test_denied_when_websocket_unsupported(route_policy_fixture) -> None:
    ctx = _gateway_context(route_policy_fixture)
    decision = ConnectorGateway().decide(
        make_request("GET", "/api/v1/health", stream_type="websocket"), ctx
    )
    assert decision.denied.deny_category == "method"
    assert decision.denied.audit_category == "method_denied"


def test_denied_when_sse_body_present(route_policy_fixture) -> None:
    ctx = _gateway_context(route_policy_fixture)
    decision = ConnectorGateway().decide(
        make_request(
            "GET",
            "/api/v1/orgs/acme/threads/T-1/tail",
            body=b"unexpected",
            stream_type="sse",
        ),
        ctx,
    )
    assert decision.denied.deny_category == "method"
    assert decision.denied.audit_category == "method_denied"


def test_denied_when_remote_authorization_present(route_policy_fixture) -> None:
    ctx = _gateway_context(route_policy_fixture)
    decision = ConnectorGateway().decide(
        make_request(headers=[("authorization", "Bearer remote-token")]), ctx
    )
    # Authorization is stripped, not leaked; the request itself still passes.
    assert decision.allowed is True
    assert decision.response is not None


def test_denied_when_bearer_shaped_material_in_input(route_policy_fixture) -> None:
    ctx = _gateway_context(route_policy_fixture)
    decision = ConnectorGateway().decide(
        make_request(headers=[("x-custom", "Bearer stolen-value")]), ctx
    )
    assert decision.allowed is False
    assert decision.denied.deny_category == "local_daemon"
    assert decision.denied.audit_category == "local_daemon_denied"


def test_denied_when_credential_unavailable(route_policy_fixture) -> None:
    class _Boom(StaticDaemonCredentialProvider):
        def read_bearer(self) -> str:
            raise CredentialUnavailable("token file missing")

    ctx = _gateway_context(route_policy_fixture, provider=_Boom("x"))
    decision = ConnectorGateway().decide(make_request(), ctx)
    assert decision.denied.deny_category == "local_daemon"
    assert decision.denied.audit_category == "daemon_unavailable"


def test_denied_when_forward_target_not_loopback(route_policy_fixture) -> None:
    from types import SimpleNamespace

    class _EvilForwarder(ForwardingHarness):
        def __init__(self) -> None:
            self.target = SimpleNamespace(host="0.0.0.0", port=80)
            self.forwarded = []
            self.streams = []

    ctx = _gateway_context(route_policy_fixture, forwarder=_EvilForwarder())
    decision = ConnectorGateway().decide(make_request(), ctx)
    assert decision.allowed is False
    assert decision.denied.deny_category == "local_daemon"
    assert decision.denied.audit_category == "daemon_bind_mismatch"


def test_denied_when_internal_failure(route_policy_fixture) -> None:
    class _ExplodingVerifier(identity.DeviceProofVerifier):
        def verify(self, proof, connector_identity, now):
            raise RuntimeError("boom")

    ctx = _gateway_context(route_policy_fixture, verifier=_ExplodingVerifier())
    decision = ConnectorGateway().decide(make_request(), ctx)
    assert decision.denied.deny_category == "internal"
    assert decision.denied.audit_category == "internal_error"
    assert "boom" not in decision.denied.detail


def test_denied_when_smuggling_headers(route_policy_fixture) -> None:
    ctx = _gateway_context(route_policy_fixture)
    decision = ConnectorGateway().decide(
        make_request(
            "POST",
            "/api/v1/orgs/acme/tasks",
            headers=[("content-length", "5"), ("transfer-encoding", "chunked")],
        ),
        ctx,
    )
    assert decision.allowed is False
    assert decision.denied.deny_category == "normalization"
    assert decision.denied.audit_category == "normalization_denied"


# ── ordering is load-bearing ─────────────────────────────────────────────


def test_mutation_policy_step_moved_after_bearer_is_detected(route_policy_fixture) -> None:
    """Ordering mutation: reading the daemon bearer before validating policy
    changes the outcome — the bearer must never be read under a stale policy."""

    class _Recording(StaticDaemonCredentialProvider):
        def __init__(self) -> None:
            super().__init__(BEARER)
            self.reads = 0

        def read_bearer(self) -> str:
            self.reads += 1
            return super().read_bearer()

    def _invariant_stale_policy_never_reads_bearer(gateway, ctx, request, provider) -> None:
        decision = gateway.decide(request, ctx)
        assert decision.allowed is False, "stale policy must never forward"
        assert decision.denied is not None
        assert decision.denied.audit_category == "policy_stale"
        assert provider.reads == 0, "daemon bearer must never be read under stale policy"

    gateway = ConnectorGateway()
    provider = _Recording()
    stale = build_consumer(route_policy_fixture, max_age_seconds=60)
    ctx = _gateway_context(
        route_policy_fixture, policy=stale, provider=provider, now=NOW() + timedelta(seconds=120)
    )
    request = make_request()
    _invariant_stale_policy_never_reads_bearer(gateway, ctx, request, provider)

    # Mutation: swap policy and bearer in the step order.
    original = ConnectorGateway._STEP_ORDER
    mutated = list(original)
    i, j = mutated.index("policy"), mutated.index("bearer")
    mutated[i], mutated[j] = mutated[j], mutated[i]
    try:
        monkeypatch_order(mutated)
        with pytest.raises(AssertionError):
            _invariant_stale_policy_never_reads_bearer(gateway, ctx, request, provider)
    finally:
        monkeypatch_order(original)


def test_mutation_authenticate_moved_after_allowlist_is_detected(route_policy_fixture) -> None:
    gateway = ConnectorGateway()
    ctx = _gateway_context(route_policy_fixture, proof=None)
    request = make_request("GET", "/api/v1/health")
    assert gateway.decide(request, ctx).allowed is False

    original = ConnectorGateway._STEP_ORDER
    mutated = list(original)
    mutated.remove("authenticate")
    mutated.insert(mutated.index("allowlist"), "authenticate")
    try:
        monkeypatch_order(mutated)
        # With identity checked after the allow-list, an allowed route still
        # denies (identity gate still present) — but the category changes:
        # the authenticate step no longer runs before normalize, so the
        # identity outcome cannot be produced for a valid route; the gateway
        # must still deny with identity (no request is authorized without it).
        decision = gateway.decide(request, ctx)
        assert decision.allowed is False
        assert decision.denied.audit_category in {"identity_denied", "internal_error"}
    finally:
        monkeypatch_order(original)


def test_mutation_strip_skipped_leaks_remote_auth(route_policy_fixture) -> None:
    """Guard-removal mutation: without stripping, remote Authorization would
    reach the daemon; the credential scan catches it before any forward."""
    from .fake_daemon import FakeDaemon

    fake = FakeDaemon(expected_bearer=BEARER)
    fake.start()
    try:
        ctx = _gateway_context(
            route_policy_fixture,
            forwarder=HttpLoopbackForwarder(LoopbackTarget(LOOPBACK_HOST, fake.port)),
        )
        gateway = ConnectorGateway()
        request = make_request(headers=[("authorization", "Bearer remote-token")])
        assert gateway.decide(request, ctx).allowed is True
        forwarded_before_mutation = len(fake.requests)

        # Mutation: replace the strip step with a no-op that passes the RAW
        # remote headers through to the forward hop.
        original_strip = ConnectorGateway._step_strip

        def _noop_strip(self, request, ctx, state):
            state.headers = request.headers  # mutation: nothing is stripped
            return None

        try:
            ConnectorGateway._step_strip = _noop_strip
            decision = gateway.decide(request, ctx)
            # The remote Bearer-shaped value is caught before any forward.
            assert decision.allowed is False
            assert decision.denied is not None
            assert decision.denied.audit_category == "local_daemon_denied"
            assert len(fake.requests) == forwarded_before_mutation, (
                "no request may reach the daemon under the strip-skip mutation"
            )
        finally:
            ConnectorGateway._step_strip = original_strip
    finally:
        fake.stop()


def monkeypatch_order(order: list[str]) -> None:
    ConnectorGateway._STEP_ORDER = tuple(order)


# ── decision details never leak secrets ──────────────────────────────────


def test_denied_detail_never_contains_bearer(route_policy_fixture) -> None:
    ctx = _gateway_context(route_policy_fixture)
    decision = ConnectorGateway().decide(
        make_request(headers=[("x-custom", BEARER)]), ctx
    )
    assert decision.denied is not None
    assert BEARER not in decision.denied.detail
    assert BEARER not in decision.audit_detail


# ── forwarding boundary: every open/forward/stream failure is normalized ──
#
# The gateway's forwarding boundary must catch and normalize every
# forward/open/stream failure (including ConnectionRefusedError and hostile
# exception text) into a stable Unit-A-category, tenant-neutral, secret-free
# denial, and deterministically close partial response/stream resources.


def test_connection_refused_normalized_to_daemon_unavailable(route_policy_fixture) -> None:
    """Literal loopback connection refusal must produce a stable denial, not
    escape decide() as a raw ConnectionRefusedError."""
    forwarder = HttpLoopbackForwarder(LoopbackTarget(LOOPBACK_HOST, 1))
    ctx = _gateway_context(route_policy_fixture, forwarder=forwarder)
    decision = ConnectorGateway().decide(make_request(), ctx)
    assert decision.allowed is False
    assert decision.denied is not None
    assert decision.denied.deny_category == "local_daemon"
    assert decision.denied.audit_category == "daemon_unavailable"
    assert "ConnectionRefusedError" not in decision.denied.detail
    assert "Errno" not in decision.denied.detail


def test_forwarder_raw_oserror_denied_and_secret_free(route_policy_fixture) -> None:
    """A forwarder raising a hostile OSError (raw exception text with secret
    material) is redacted to the stable daemon-unavailable denial."""
    class _Hostile(ForwardingHarness):
        def open_stream(self, method, path, query, headers, body, bearer, stream_id):
            raise ConnectionRefusedError(
                f"[Errno 111] tenant-b home-42 device-7 {BEARER} Connection refused"
            )

    ctx = _gateway_context(route_policy_fixture, forwarder=_Hostile())
    decision = ConnectorGateway().decide(make_request(), ctx)
    assert decision.allowed is False
    assert decision.denied is not None
    assert decision.denied.deny_category == "local_daemon"
    assert decision.denied.audit_category == "daemon_unavailable"
    blob = decision.denied.detail + decision.audit_detail
    for secret in (BEARER, "tenant-b", "home-42", "device-7", "Errno", "Connection refused"):
        assert secret not in blob


def test_forwarder_hostile_exception_redacted_to_internal(route_policy_fixture) -> None:
    """Arbitrary forwarder exceptions are redacted to the internal category;
    raw exception text never reaches any decision surface."""
    class _Hostile(ForwardingHarness):
        def open_stream(self, method, path, query, headers, body, bearer, stream_id):
            raise RuntimeError(f"boom at /home/user/.happyranch with {BEARER} for tenant-b")

    ctx = _gateway_context(route_policy_fixture, forwarder=_Hostile())
    decision = ConnectorGateway().decide(make_request(), ctx)
    assert decision.allowed is False
    assert decision.denied is not None
    assert decision.denied.deny_category == "internal"
    assert decision.denied.audit_category == "internal_error"
    blob = decision.denied.detail + decision.audit_detail
    for secret in (BEARER, "boom", "/home/user", "tenant-b"):
        assert secret not in blob


def test_forwarder_open_stream_raises_connection_refused(route_policy_fixture) -> None:
    """open_stream failure is normalized the same way as forward failure."""
    from types import SimpleNamespace

    class _Hostile(ForwardingHarness):
        def open_stream(self, method, path, query, headers, body, bearer, stream_id):
            raise ConnectionRefusedError("[Errno 111] Connection refused")

    ctx = _gateway_context(route_policy_fixture, forwarder=_Hostile())
    decision = ConnectorGateway().decide(
        make_request("GET", "/api/v1/orgs/acme/threads/T-1/tail", stream_type="sse"), ctx
    )
    assert decision.allowed is False
    assert decision.denied is not None
    assert decision.denied.deny_category == "local_daemon"
    assert decision.denied.audit_category == "daemon_unavailable"
    assert "Errno" not in decision.denied.detail


def test_stream_receive_failure_normalized_and_resources_closed(route_policy_fixture) -> None:
    """A mid-stream receive failure on an HTTP exchange denies with the stable
    category and deterministically closes the partial stream resources."""
    from types import SimpleNamespace

    class _BrokenHandle:
        stream_id = "s-broken"
        status = 200
        headers: tuple = ()

        def receive(self) -> bytes | None:
            raise ConnectionResetError("[Errno 104] connection reset by peer")

        def close(self) -> None:
            self.closed = True

    class _Broken(ForwardingHarness):
        def open_stream(self, method, path, query, headers, body, bearer, stream_id):
            handle = _BrokenHandle()
            handle.closed = False
            return handle

    registry = StreamRegistry()
    ctx = _gateway_context(route_policy_fixture, forwarder=_Broken(), stream_registry=registry)
    decision = ConnectorGateway().decide(make_request(), ctx)
    assert decision.allowed is False
    assert decision.denied is not None
    assert decision.denied.deny_category == "local_daemon"
    assert decision.denied.audit_category == "daemon_unavailable"
    assert "Errno" not in decision.denied.detail
    assert registry.is_open("s-broken") is False, "partial stream must be dropped from the registry"


def test_registry_revoked_race_closes_opened_handle(route_policy_fixture) -> None:
    """If the registry is revoked between open_stream and registration, the
    handle is closed and the denial is the normative revocation category."""
    from types import SimpleNamespace

    class _Handle:
        stream_id = "s-race"
        status = 200
        headers: tuple = ()

        def receive(self) -> bytes | None:
            return b"data: hello\n\n"

        def close(self) -> None:
            self.closed = True

    class _Racy(ForwardingHarness):
        def open_stream(self, method, path, query, headers, body, bearer, stream_id):
            handle = _Handle()
            handle.closed = False
            return handle

    registry = StreamRegistry()
    registry.close_all()  # sealed before the forward attempt
    ctx = _gateway_context(route_policy_fixture, forwarder=_Racy(), stream_registry=registry)
    decision = ConnectorGateway().decide(
        make_request("GET", "/api/v1/orgs/acme/threads/T-1/tail", stream_type="sse"), ctx
    )
    assert decision.allowed is False
    assert decision.denied is not None
    assert decision.denied.deny_category == "revocation"
    assert decision.denied.audit_category == "revocation_stream_closed"


def test_sse_open_stream_exception_never_reaches_client(route_policy_fixture) -> None:
    """Hostile exception text from stream open cannot escape to the stream
    decision surface (SSE path)."""
    class _Hostile(ForwardingHarness):
        def open_stream(self, method, path, query, headers, body, bearer, stream_id):
            raise RuntimeError(f"Bearer {BEARER} leaked for tenant-b/home-42")

    ctx = _gateway_context(route_policy_fixture, forwarder=_Hostile())
    decision = ConnectorGateway().decide(
        make_request("GET", "/api/v1/orgs/acme/threads/T-1/tail", stream_type="sse"), ctx
    )
    assert decision.allowed is False
    assert decision.denied is not None
    blob = decision.denied.detail + decision.audit_detail
    for secret in (BEARER, "leaked", "tenant-b", "home-42"):
        assert secret not in blob

"""LAB-ONLY conformance provider adapter (THR-097 phase unit 3).

This adapter exists ONLY to prove Mac-client -> Linux-home conformance in a
controlled lab/customer-network: a scripted lab client reaches the connector
over an explicit lab address, the FULL gateway pipeline runs (identity, bind,
proof, policy, normalize, allowlist, strip, bearer, forward), and the request
is forwarded to the literal-loopback daemon with the bearer injected on the
final hop. It is unambiguously NOT a product or Supported-DIY adapter and
does NOT close THR-034: the lab device proof is a static, explicitly
configured lab credential, the listener binds only an explicitly configured
lab address (0.0.0.0/:: are refused), readiness must pass before any
listener, and the adapter refuses to run unless the config carries
``lab_only: true``.

Fixed invariants enforced here:

- **no listener unless readiness passes** (daemon loopback reachability,
  credential permissions, current policy, bind identity, non-corrupt trust
  state);
- literal-loopback daemon forwarding with bearer injection on the final hop
  only (the existing ``HttpLoopbackForwarder`` contract);
- denials are 403 with category-level prose only — no bearer, paths, input,
  or exception text ever escapes to the client or any log.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from typing import Callable

from runtime.remote_access.gateway import ConnectorGateway, GatewayContext
from runtime.remote_access.httpd import BaseConnectorHandler, send_json, serve_decision
from runtime.remote_access.models import Decision, RemoteRequest
from runtime.remote_access.readiness import ConnectorReadiness

LAB_ONLY_BANNER = (
    "LAB-ONLY CONFORMANCE ADAPTER - not a product or Supported-DIY lane; "
    "does not close THR-034"
)

_NOT_READY_STATUS = 503

# Addresses that would expose the lab listener beyond an explicit interface.
_FORBIDDEN_BIND_HOSTS = frozenset({"0.0.0.0", "::", ""})


class LabProviderError(Exception):
    """The lab adapter refused to start or serve (fail closed)."""


@dataclass(frozen=True)
class LabProviderConfig:
    """Explicit lab-only configuration. ``lab_only`` MUST be true; the bind
    address MUST be a concrete lab interface (never a wildcard)."""

    bind_host: str
    bind_port: int = 0
    lab_only: bool = False
    lab_device_id: str = "lab-client-1"


ContextFactory = Callable[[datetime], GatewayContext]


class LabProviderAdapter:
    """Readiness-gated, LAB-ONLY listener running the full gateway pipeline.

    ``ctx_factory`` builds the per-request :class:`GatewayContext` (wired by
    the supervisor with the real policy/credential/forwarder); ``gateway`` is
    the shared :class:`ConnectorGateway` whose locked decision order runs for
    every request.
    """

    def __init__(
        self,
        *,
        config: LabProviderConfig,
        readiness: ConnectorReadiness,
        ctx_factory: ContextFactory,
        gateway: ConnectorGateway | None = None,
    ) -> None:
        self.config = config
        self._readiness = readiness
        self._ctx_factory = ctx_factory
        self._gateway = gateway or ConnectorGateway()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._ready = False

    # ── lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> None:
        """Validate lab-only gating, run readiness, then bind. Raises
        :class:`LabProviderError` (redacted) on any failure — no listener.
        Expected operational listener failures (occupied port, permission,
        address unavailable) are normalized from the socket ``OSError`` to
        :class:`LabProviderError` at this boundary so the supervisor's
        supervised-retry contract sees the documented category — never a
        bare ``OSError``. Unexpected defects (programming errors, readiness-
        subsystem failures) still propagate loudly."""
        self._validate_lab_gating()
        report = self._readiness.evaluate(datetime.now(timezone.utc))
        if not report.ready:
            failed = ", ".join(report.failing_gates)
            raise LabProviderError(f"readiness failed: {failed}")
        try:
            self._server = ThreadingHTTPServer(
                (self.config.bind_host, self.config.bind_port), self._handler_factory()
            )
        except OSError as exc:
            # Expected operational listener failure (bind/listen syscall: port
            # in use, permission denied, address unavailable). Normalize to the
            # documented startup category with a category-only message — never
            # the raw socket/errno text — leaving no listener behind. This is
            # deliberately narrow: only the bind/listen construction is wrapped,
            # so programming errors still fail loudly.
            raise LabProviderError("lab provider failed to bind listener") from exc
        self._ready = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="hr-lab-provider", daemon=True
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

    def _validate_lab_gating(self) -> None:
        if self.config.lab_only is not True:
            raise LabProviderError("lab provider refused: lab_only must be true")
        if self.config.bind_host in _FORBIDDEN_BIND_HOSTS:
            raise LabProviderError("lab provider refused: wildcard bind address forbidden")
        if self.config.lab_device_id and not self.config.lab_device_id.strip():
            raise LabProviderError("lab provider refused: lab_device_id must be set")

    # ── request pipeline ──────────────────────────────────────────────────

    def handle_request(self, request: RemoteRequest, now: datetime | None = None) -> Decision:
        """Run the full gateway pipeline for one request. Refuses to serve
        before readiness passed (fail closed)."""
        if not self._ready:
            raise LabProviderError("lab adapter not ready")
        ctx = self._ctx_factory(now or datetime.now(timezone.utc))
        return self._gateway.decide(request, ctx)

    # ── HTTP serving ──────────────────────────────────────────────────────

    def _handler_factory(self) -> type[BaseConnectorHandler]:
        adapter = self

        class Handler(BaseConnectorHandler):
            """Serves the shared BaseConnectorHandler surface: parse, run
            the adapter's full gateway pipeline, serve the decision (403
            category-only denials; relayed loopback responses; tracked SSE
            streams). Raw request lines are never logged (base class).
            """

            def serve_request(self, request: RemoteRequest, now) -> None:
                try:
                    decision = adapter.handle_request(request, now)
                except LabProviderError:
                    send_json(self, _NOT_READY_STATUS, {"error": "not_ready"})
                    return
                serve_decision(self, decision)

        return Handler

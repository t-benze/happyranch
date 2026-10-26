"""Boot-local recovery ownership; listener liveness never grants admission.

Native completion and the request-loop coordinator have distinct ownership.
Cancelling a waiter cannot release a native owner or close its databases.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Literal

_LOG = logging.getLogger(__name__)
_PHASES = ("sweep", "workflows", "returned", "compatibility", "redelivery", "producers")
_ACTION = "Read threads while recovery runs; retry new work after recovery is ready."
_FAILED_ACTION = "New work remains paused. Send the startup error code to engineering_manager."


class StartupUnavailable(RuntimeError):
    """Closed admission, without claiming that a request was accepted."""

    def __init__(self, code: str, startup: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = {"code": code, "startup": startup, "required_action":
                       _ACTION if code == "daemon_recovering" else _FAILED_ACTION}


@dataclass(frozen=True)
class RecoveryOutcome:
    disposition: Literal["settled", "local_hold", "unavailable", "unknown"]
    code: str | None = None

    @property
    def positive(self) -> bool:
        return self.disposition in ("settled", "local_hold")


@dataclass
class _Phase:
    outcome: str = "not_started"
    started_ns: int | None = None
    ended_ns: int | None = None

    def public(self) -> dict[str, Any]:
        return {"outcome": self.outcome, "duration_ns":
                None if self.started_ns is None or self.ended_ns is None
                else self.ended_ns - self.started_ns}


class StartupRecovery:
    """One retained native operation and one request-loop activation latch.

    Phases are serial owner intervals: durations include native joins and queue
    acknowledgements. Serving can overlap recovery; their times must not be
    summed. This owner does not infer launcher/API/browser usability timestamps.
    """

    def __init__(self, state: Any, app: Any) -> None:
        self.state = state
        self.app = app
        self.epoch = uuid.uuid4().hex
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._status = "recovering"
        self._serving = False
        self._phase = "attachment"
        self._phases = {name: _Phase() for name in _PHASES}
        self._first_cause: BaseException | None = None
        self._error_code: str | None = None
        self._secondary: list[BaseException] = []
        self._secondary_overflow = 0
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="startup-recovery")
        self._native: Future[RecoveryOutcome] | None = None
        self._native_thread: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._latch: asyncio.Event | None = None
        self._attachments: tuple | None = None
        self._closed = False
        self._ready_commits = 0
        self._created_ns = time.monotonic_ns()
        self._serving_ns: int | None = None
        self._sweep_owner: Any | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind references only, on the actual request loop, before submission."""
        if asyncio.get_running_loop() is not loop:
            raise RuntimeError("startup_wrong_loop")
        attachments = self.state._capture_attachments()
        with self._lock:
            if self._loop is not None or self._closed or self._stop.is_set():
                raise RuntimeError("startup_already_bound_or_stopped")
            self._loop = loop
            self._latch = asyncio.Event()
            self._attachments = attachments

    def signal_serving(self, app: Any) -> None:
        """Only the real Server's matching, successful startup calls this."""
        with self._lock:
            if app is not self.app or self._loop is None or self._closed or self._stop.is_set():
                raise RuntimeError("startup_listener_attachment_mismatch")
            if not self._serving:
                self._serving_ns = time.monotonic_ns()
                self._serving = True

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"state": self._status, "epoch": self.epoch,
                    "serving": self._serving,
                    "recovery_ready": self._status == "ready" and not self._stop.is_set(),
                    "stopping": self._stop.is_set(), "phase": self._phase,
                    "error_code": self._error_code,
                    "ready_commits": self._ready_commits,
                    "launcher_duration_ns": None,
                    "owner_to_serving_ns": None if self._serving_ns is None
                    else self._serving_ns - self._created_ns,
                    "phases": {name: phase.public() for name, phase in self._phases.items()}}

    def require_ready(self) -> None:
        with self._lock:
            allowed = self._status == "ready" and not self._stop.is_set() and not self._closed
        if not allowed:
            current = self.snapshot()
            code = "daemon_recovering" if current["state"] == "recovering" and not current["stopping"] else "daemon_recovery_failed"
            raise StartupUnavailable(code, current)

    def require_native_owner(self) -> None:
        with self._lock:
            allowed = (self._native_thread == threading.get_ident()
                       and self._status == "recovering" and not self._stop.is_set()
                       and self._first_cause is None)
        if not allowed:
            raise StartupUnavailable("startup_owner_unavailable", self.snapshot())

    def fail(self, code: str, cause: BaseException) -> None:
        with self._lock:
            if self._first_cause is None:
                self._first_cause = cause
                self._error_code = code
            elif cause is not self._first_cause:
                if len(self._secondary) < 8:
                    self._secondary.append(cause)
                else:
                    self._secondary_overflow += 1
            self._status = "recovery_failed"
            sweep = self._sweep_owner
        # Never nest the gate lock inside a sweep lock in the opposite order.
        # Close new tail effects without interrupting their native termination.
        if sweep is not None:
            sweep.fail(cause)

    @contextmanager
    def phase(self, name: str) -> Iterator[Callable[[RecoveryOutcome], None]]:
        self.require_native_owner()
        with self._lock:
            if name not in self._phases or self._phases[name].outcome != "not_started":
                raise RuntimeError("startup_phase_duplicate_or_unknown")
            index = _PHASES.index(name)
            if any(self._phases[previous].outcome not in ("settled", "local_hold") for previous in _PHASES[:index]):
                raise RuntimeError("startup_phase_out_of_order")
            phase = self._phases[name]
            phase.outcome = "incomplete"
            phase.started_ns = time.monotonic_ns()
            self._phase = name
        outcome: RecoveryOutcome | None = None

        def settle(value: RecoveryOutcome) -> None:
            nonlocal outcome
            if outcome is not None or not isinstance(value, RecoveryOutcome):
                raise RuntimeError("startup_outcome_duplicate_or_invalid")
            outcome = value

        try:
            yield settle
            self.require_native_owner()
            if outcome is None or not outcome.positive:
                raise RuntimeError(outcome.code if outcome is not None and outcome.code else "startup_outcome_unavailable")
        except BaseException as exc:
            self.fail("startup_" + name + "_failed", exc)
            with self._lock:
                phase.outcome = "failed"
            raise
        else:
            with self._lock:
                phase.outcome = outcome.disposition
        finally:
            with self._lock:
                phase.ended_ns = time.monotonic_ns()
            _LOG.info("startup phase=%s outcome=%s epoch=%s", name, phase.outcome, self.epoch)

    def submit(self, operation: Callable[[StartupRecovery], RecoveryOutcome]) -> Future[RecoveryOutcome]:
        """Reserve before native start; this operation is never resubmitted."""
        with self._lock:
            if self._loop is None or self._native is not None or self._closed or self._stop.is_set():
                raise RuntimeError("startup_submission_unavailable")
            # submit can start immediately; _run first acquires this same lock.
            self._native = self._executor.submit(self._run, operation)
            return self._native

    def _run(self, operation: Callable[[StartupRecovery], RecoveryOutcome]) -> RecoveryOutcome:
        with self._lock:
            self._native_thread = threading.get_ident()
        try:
            self.require_native_owner()
            outcome = operation(self)
            if not isinstance(outcome, RecoveryOutcome) or not outcome.positive:
                raise RuntimeError("startup_native_outcome_unavailable")
            self.require_native_owner()
            with self._lock:
                if any(phase.outcome not in ("settled", "local_hold") for phase in self._phases.values()):
                    raise RuntimeError("startup_native_phases_incomplete")
            return outcome
        except BaseException as exc:
            self.fail("startup_native_failed", exc)
            raise
        finally:
            with self._lock:
                self._native_thread = None

    async def join(self) -> RecoveryOutcome:
        """A cancelled waiter still consumes the ORIGINAL native completion."""
        with self._lock:
            native = self._native
        if native is None:
            raise RuntimeError("startup_not_submitted")
        wrapped = asyncio.wrap_future(native)
        cancellation: asyncio.CancelledError | None = None
        while True:
            try:
                outcome = await asyncio.shield(wrapped)
                break
            except asyncio.CancelledError as exc:
                # Cancellation does not cancel the concurrent Future.
                self.stop()
                cancellation = cancellation or exc
                if wrapped.done():
                    # A genuinely cancelled native Future is not settlement.
                    outcome = wrapped.result()
                    break
        if cancellation is not None:
            raise cancellation
        return outcome

    def activate(self) -> None:
        """One non-await commit on the true loop, after dormant handles exist."""
        if asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("startup_wrong_activation_loop")
        self.state._assert_attachments_unchanged(self._attachments)
        with self._lock:
            if (self._native is None or not self._native.done() or self._native.cancelled()
                    or self._closed or self._stop.is_set() or self._first_cause is not None
                    or self._status != "recovering" or self._latch is None):
                raise RuntimeError("startup_activation_unavailable")
            outcome = self._native.result()
            if not isinstance(outcome, RecoveryOutcome) or not outcome.positive:
                raise RuntimeError("startup_activation_outcome_unavailable")
            self._status = "ready"
            self._phase = "ready"
            self._ready_commits += 1
            self._latch.set()

    async def wait_ready(self) -> None:
        if self._latch is None or asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("startup_latch_unbound")
        await self._latch.wait()
        self.require_ready()

    def stop(self) -> None:
        # Synchronous admission fence, before any shutdown await.
        with self._lock:
            self._stop.set()
            sweep = self._sweep_owner
        if sweep is not None:
            sweep.negative('startup_sweep_stopped')

    def assert_native_idle(self) -> None:
        with self._lock:
            if self._native is not None and not self._native.done():
                raise RuntimeError("startup_native_still_owned")
            if self._sweep_owner is not None:
                raise RuntimeError("startup_sweep_native_still_owned")

    def retain_sweep(self, owner: Any) -> None:
        self.require_native_owner()
        with self._lock:
            if (self._sweep_owner is not None or self._stop.is_set()
                    or self._first_cause is not None):
                raise RuntimeError("startup_duplicate_or_closed_sweep_owner")
            self._sweep_owner = owner

    def settle_sweep(self, owner: Any) -> None:
        # The body Future or tail seal alone cannot retire this owner. Context
        # reset and the sole actual final batch result also belong to it.
        with owner._lock:
            if (not owner._boundary_complete or not owner._sealed or owner._tails
                    or owner._humans
                    or owner.counts['effects_started'] != owner.counts['effects_settled']):
                raise RuntimeError("startup_sweep_boundary_incomplete")
        with self._lock:
            if self._sweep_owner is not owner:
                raise RuntimeError("startup_sweep_owner_changed")
            self._sweep_owner = None

    async def close(self) -> None:
        self.stop()
        if self._native is not None:
            # Observe a failed operation as well; done is not a successful run.
            try:
                await self.join()
            except Exception:
                self.assert_native_idle()
        self.assert_native_idle()
        self._executor.shutdown(wait=True, cancel_futures=False)
        with self._lock:
            self._closed = True


def require_ready(owner: Any) -> None:
    """Direct production boundaries must have an explicitly bound owner."""
    gate = getattr(owner, "_startup_recovery", None)
    if not isinstance(gate, StartupRecovery):
        raise StartupUnavailable("startup_gate_unbound")
    gate.require_ready()


class StartupAdmissionMiddleware:
    """Resolve the actual route BEFORE receive, dependencies or body parsing.

    Matching never runs an endpoint. The seven early identities still enter
    their original auth/OrgDep handlers. Neither query strings, slash redirects
    nor a path-only method match can confer permission.
    """

    def __init__(self, app: Any, *, route_app: Any, state: Any) -> None:
        from runtime.daemon.routes import auth, health, orgs, runtime, threads
        from starlette.exceptions import HTTPException
        self.app = app
        self.route_app = route_app
        self.state = state
        self._early = {
            ("/api/v1/health", health.health),
            ("/api/v1/auth/bootstrap", auth.bootstrap),
            ("/api/v1/runtime", runtime.get_runtime),
            ("/api/v1/orgs", orgs.list_orgs),
            ("/api/v1/orgs/{slug}/threads", threads.list_threads_endpoint),
            ("/api/v1/orgs/{slug}/threads/{thread_id}", threads.get_thread_endpoint),
            ("/api/v1/orgs/{slug}/threads/{thread_id}/messages", threads.list_thread_messages_endpoint),
        }
        ready = getattr(health, "health_ready", None)
        if ready is not None:
            self._early.add(("/api/v1/health/ready", ready))
        self._spa_fallback = route_app.exception_handlers.get(HTTPException)
        if (self._spa_fallback is not None
                and self._spa_fallback.__module__ != "runtime.daemon.routes.web_static"):
            self._spa_fallback = None

    def _allowed(self, scope: dict[str, Any]) -> bool:
        from starlette.routing import Match, Mount
        from starlette.staticfiles import StaticFiles
        from starlette.exceptions import HTTPException
        from runtime.daemon.routes.web_static import _is_spa_route
        method = scope["method"]
        partial = False
        for route in self.route_app.router.routes:
            match, _ = route.matches(scope)
            if match is Match.PARTIAL:
                partial = True
            if match is not Match.FULL:
                continue
            if isinstance(route, Mount):
                return (route.path == "/assets" and route.name == "assets"
                        and isinstance(route.app, StaticFiles) and method in ("GET", "HEAD"))
            endpoint = getattr(route, "endpoint", None)
            identity = (getattr(route, "path", None), endpoint)
            if method == "GET" and identity in self._early:
                return True
            # Only the registered real SPA root, never docs/OpenAPI/API GET.
            return (method == "GET" and identity[0] == "/"
                    and endpoint is not None
                    and endpoint.__module__ == "runtime.daemon.routes.web_static"
                    and endpoint.__name__ in ("_spa_root", "_placeholder_root"))
        path = scope.get("path", "")
        if (partial or method != "GET" or path == "/api" or path.startswith("/api/")
                or not _is_spa_route(path) or self._spa_fallback is None
                or self.route_app.exception_handlers.get(HTTPException) is not self._spa_fallback):
            return False
        # Router slash redirects happen before the SPA 404 handler. A redirect
        # to a held or method-only identity is not an early read exemption.
        if path != "/":
            redirect_scope = {**scope, "path": path.rstrip("/") if path.endswith("/") else path + "/"}
            for route in self.route_app.router.routes:
                match, _ = route.matches(redirect_scope)
                if match is not Match.NONE:
                    return False
        return True

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from starlette.responses import JSONResponse
        gate = getattr(self.state, "_startup_recovery", None)
        if not isinstance(gate, StartupRecovery) or self.route_app.state.daemon is not self.state:
            error = StartupUnavailable("startup_gate_unbound")
        else:
            snapshot = gate.snapshot()
            if snapshot["recovery_ready"] and not snapshot["stopping"]:
                await self.app(scope, receive, send)
                return
            if self._allowed(scope):
                await self.app(scope, receive, send)
                return
            code = "daemon_recovering" if snapshot["state"] == "recovering" and not snapshot["stopping"] else "daemon_recovery_failed"
            error = StartupUnavailable(code, snapshot)
        headers = {"Retry-After": "2"} if error.code == "daemon_recovering" else None
        await JSONResponse({"detail": error.detail}, status_code=503, headers=headers)(scope, receive, send)

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# Audit action strings emitted by the throttle (issue #85 layer-1 surfacing).
# Additive action+payload via the existing ``insert_audit_log`` — no new
# columns, no audit-row-shape change (precedent: ``revisit_of``,
# ``agent_session_evicted_fallback``).
SLOT_WAIT_ACTION = "executor_slot_wait"
RATE_LIMIT_BACKOFF_ACTION = "executor_rate_limit_backoff"

# ``on_throttle_event(action, payload)`` — wired by the two call sites
# (Orchestrator._run_agent for tasks, thread_runner for threads) to a closure
# that writes via ``insert_audit_log`` scoped to the task_id / THR- id + agent.
OnThrottleEvent = Callable[[str, dict], None]

# A launch is any zero-arg callable that runs the subprocess once and returns
# an object exposing a ``rate_limited`` attribute (an ``ExecutorResult``). Kept
# duck-typed on purpose so this module never imports ``executors`` (which
# imports config/models) — avoids an import cycle.
Launch = Callable[[], Any]


class ProviderThrottle:
    """Process-wide, per-provider launch throttle (issue #85).

    Three gates, every one keyed by provider string so the providers are
    structurally isolated from each other:

    1. a lazily-created ``threading.BoundedSemaphore(ceiling)`` — the
       concurrency **CEILING**. Saturating ``claude`` never blocks a ``codex``
       launch because they hold independent semaphores.
    2. a per-provider ``Lock`` + last-launch monotonic stamp — the proactive
       inter-launch **SPACING** gate.
    3. a backoff schedule — the reactive 429 **RETRY**.

    Insert it at the single synchronous chokepoint ``executors._run_command``,
    reached on a real OS thread by both the task path (queue worker thread →
    ``Orchestrator._run_agent``) and the thread path (``thread_runner`` →
    ``run_in_executor``). A ``BoundedSemaphore`` is the one primitive that gates
    both with no async/sync impedance.

    ``sleep`` and ``monotonic`` are injectable so tests exercise the spacing and
    backoff logic without real wall-clock sleeps.
    """

    def __init__(
        self,
        *,
        ceiling_default: int,
        ceiling_overrides: dict[str, int] | None = None,
        spacing_seconds: float = 0.0,
        backoff_seconds: "list[int] | tuple[int, ...]" = (),
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ceiling_default = ceiling_default
        self._ceiling_overrides = dict(ceiling_overrides or {})
        self._spacing_seconds = spacing_seconds
        self._backoff_seconds = tuple(backoff_seconds)
        self._sleep = sleep
        self._monotonic = monotonic
        # Guards lazy creation of the per-provider semaphores + spacing locks so
        # two threads racing on a never-seen provider don't build two of either.
        self._registry_lock = threading.Lock()
        self._semaphores: dict[str, threading.BoundedSemaphore] = {}
        self._spacing_locks: dict[str, threading.Lock] = {}
        self._last_launch: dict[str, float] = {}

    def ceiling_for(self, provider: str) -> int:
        return self._ceiling_overrides.get(provider, self._ceiling_default)

    def _semaphore_for(self, provider: str) -> threading.BoundedSemaphore:
        with self._registry_lock:
            sem = self._semaphores.get(provider)
            if sem is None:
                sem = threading.BoundedSemaphore(self.ceiling_for(provider))
                self._semaphores[provider] = sem
            return sem

    def _spacing_lock_for(self, provider: str) -> threading.Lock:
        with self._registry_lock:
            lock = self._spacing_locks.get(provider)
            if lock is None:
                lock = threading.Lock()
                self._spacing_locks[provider] = lock
            return lock

    def _spacing_gate(self, provider: str) -> None:
        """Honor the minimum inter-launch interval for ``provider``.

        Serializes per-provider stamping (so two concurrent same-provider
        launches are spaced), then sleeps the residual interval before stamping
        the new launch time. Different providers take different locks, so they
        are never cross-throttled.
        """
        if self._spacing_seconds <= 0:
            return
        with self._spacing_lock_for(provider):
            last = self._last_launch.get(provider)
            now = self._monotonic()
            if last is not None:
                wait = self._spacing_seconds - (now - last)
                if wait > 0:
                    self._sleep(wait)
                    now = self._monotonic()
            self._last_launch[provider] = now

    def run(
        self,
        provider: str,
        launch: Launch,
        on_event: OnThrottleEvent | None = None,
    ) -> Any:
        """Acquire a provider slot, honor spacing, run ``launch``, and absorb
        transient 429s with backoff before returning the result.

        The 429 backoff/retry fires only for a launch that was rate-limited AND
        did **not** succeed; a successful session is never relaunched (idempotent
        re-launch is safe only because a failed rate-limited attempt did no
        useful work and never called ``report-completion``).

        The slot is released in a ``finally`` so it is freed on success, error,
        timeout, AND exception. During a backoff sleep the slot is explicitly
        released and re-acquired — a backing-off session must not keep a slot
        (that would effectively lower the ceiling, which the founder forbade).
        """
        sem = self._semaphore_for(provider)
        ceiling = self.ceiling_for(provider)

        # Measure how long the launch waited for a slot (the burst signal).
        wait_seconds = 0.0
        if not sem.acquire(blocking=False):
            t0 = self._monotonic()
            sem.acquire()
            wait_seconds = self._monotonic() - t0
        if wait_seconds > 0 and on_event is not None:
            self._emit(
                on_event,
                SLOT_WAIT_ACTION,
                {"provider": provider, "wait_seconds": wait_seconds, "ceiling": ceiling},
            )

        try:
            attempts = 1 + len(self._backoff_seconds)
            result: Any = None
            for attempt in range(attempts):
                self._spacing_gate(provider)
                result = launch()
                # The reactive 429 retry fires ONLY for a launch that BOTH
                # matched a rate-limit signature AND did not succeed. A
                # successful session is never relaunched even if its output
                # happened to contain a rate-limit phrase — relaunching it would
                # duplicate side effects (commits, pushes, completion rows,
                # thread replies). ``success`` defaults True so an indeterminate
                # result is treated as success (conservative: never spuriously
                # relaunched). Genuine transient 429s ARE failures, so this gate
                # does not weaken de-bursting. Return on a non-retry-worthy
                # result, or once the backoff schedule is exhausted — the
                # survivor then falls through to the existing classifier /
                # auto-revisit path.
                retry_worthy = getattr(result, "rate_limited", False) and not getattr(
                    result, "success", True
                )
                if not retry_worthy or attempt == attempts - 1:
                    return result
                backoff = self._backoff_seconds[attempt]
                if on_event is not None:
                    self._emit(
                        on_event,
                        RATE_LIMIT_BACKOFF_ACTION,
                        {"provider": provider, "attempt": attempt + 1, "backoff_seconds": backoff},
                    )
                # Release the slot during the sleep so the ceiling stays honest,
                # then re-acquire for the retry.
                sem.release()
                try:
                    self._sleep(backoff)
                finally:
                    sem.acquire()
            return result
        finally:
            sem.release()

    @staticmethod
    def _emit(on_event: OnThrottleEvent, action: str, payload: dict) -> None:
        # An audit-event failure must never break a launch.
        try:
            on_event(action, payload)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("throttle on_event(%s) raised: %s", action, exc)


# --- Process-wide default instance -----------------------------------------
# One throttle per daemon process, lazily built from ``runtime.config.settings``
# the first time ``_run_command`` runs. ``set_throttle``/``reset_throttle`` exist
# so tests can install a deterministic instance (zero spacing, no real sleeps).

_default_lock = threading.Lock()
_default_throttle: ProviderThrottle | None = None


def _throttle_from_settings() -> ProviderThrottle:
    from runtime.config import settings

    return ProviderThrottle(
        ceiling_default=settings.executor_ceiling_default,
        ceiling_overrides=settings.executor_ceiling_overrides,
        spacing_seconds=settings.executor_launch_spacing_seconds,
        backoff_seconds=settings.executor_rate_limit_backoff_seconds,
    )


def get_throttle() -> ProviderThrottle:
    global _default_throttle
    with _default_lock:
        if _default_throttle is None:
            _default_throttle = _throttle_from_settings()
        return _default_throttle


def set_throttle(throttle: ProviderThrottle | None) -> None:
    """Install a specific throttle instance (or ``None`` to force a rebuild
    from settings on the next ``get_throttle``). For tests + reconfiguration."""
    global _default_throttle
    with _default_lock:
        _default_throttle = throttle


def reset_throttle() -> None:
    set_throttle(None)

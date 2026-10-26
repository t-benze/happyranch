"""Fixed boot-local scalar observations, never an admission or recovery owner.

No product imports, DB/file reads, per-org records or resource census. The script
entry emits only a later local exec marker; it cannot observe shell entry or an
external parent's API/browser acceptance clock.
"""
from __future__ import annotations

import logging
import re
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

_LOG = logging.getLogger(__name__)
STAGES = (
    "imports", "main_preflight", "build_state", "runtime_registry", "runtime_load",
    "daemon_state", "org_load", "org_preflight", "database", "workflow_install",
    "workflow_validate", "move_schema", "cutover", "pause_schema", "roster",
    "orchestrator", "org_services", "identity_publication", "authority_publication",
    "authority_selectors", "authority_owner", "profile_reconcile", "asgi_listener",
)
_EVENTS = ("boot_prepared", "boot_failed", "serving", "native_settled", "ready", "failed", "stop", "closed")
V2_COUNTERS = (
    'history_hits',
    'history_misses',
    'history_fetches',
    'history_rows',
    'history_payload_characters',
    'history_payload_size_unavailable',
    'history_decode_attempts',
    'history_decodes',
    'admissions',
    'retention_rejections',
    'invalidations',
    'uncached_reads',
    'epoch_changes',
    'moving_read_fallbacks',
    'discovery_calls',
    'discovery_ns',
    'exact_calls',
    'exact_ns',
    'failed_reads',
    'history_max_rows',
    'discovery_max_ns',
    'exact_max_ns',
    'discovery_complete',
    'discovery_failed',
    'exact_complete',
    'exact_failed',
    'invalidation_explicit',
    'invalidation_exit',
    'invalidation_accounting',
    'invalidation_connection',
    'invalidation_uncached',
    'invalidation_epoch',
    'invalidation_moving',
    'invalidation_failed_read',
    'invalidation_discovery',
    'invalidation_refusal',
    'observation_failures',
)
_P0_COUNTS = ("orgs_skipped_diagnostic", "orgs_refused", "orgs_attached", "org_seed_nonfatal", "authority_fenced")
_V2_OUTCOMES = ("complete", "failed", "incomplete", "unavailable")


def observation_time_ns() -> int | None:
    try:
        return time.monotonic_ns()
    except Exception:
        return None


def _summary() -> dict[str, int | None]:
    return {"count": 0, "total": 0, "min": None, "max": None}


def _add(summary: dict, value: int) -> None:
    summary["count"] += 1
    summary["total"] += value
    summary["min"] = value if summary["min"] is None else min(summary["min"], value)
    summary["max"] = value if summary["max"] is None else max(summary["max"], value)


def _clock_domain() -> str | None:
    # Python monotonic is cross-process on supported normal Linux/macOS launches.
    # This label is NOT remote-host/boot attestation or a time-namespace proof.
    info = time.get_clock_info("monotonic")
    return sys.platform if sys.platform in ("linux", "darwin") and info.monotonic and not info.adjustable else None


def launch_clock() -> str:
    domain = _clock_domain()
    return "unavailable" if domain is None else f"{uuid.uuid4().hex}:{domain}:{time.monotonic_ns()}"


@dataclass
class _Span:
    started_ns: int | None
    child_ns: int | None = 0
    outcome: str = "complete"


class BootObservation:
    """Fixed-size totals, no retained operation/reader/connection or ID sets.

    P0 spans are synchronous same-thread nesting only. The lock covers fixed
    scalar copies/updates, never body work, DB ownership or logging.
    """

    def __init__(self, module_entry_ns: int) -> None:
        self.module_entry_ns = module_entry_ns
        self._lock = threading.Lock()
        self._stack = threading.local()
        self._stages = {name: {"inclusive_ns": _summary(), "exclusive_ns": _summary(),
                              **dict.fromkeys(("complete", "failed", "incomplete", "local_hold"), 0),
                              "first_started_ns": None, "last_ended_ns": None} for name in STAGES}
        self._p0_counts = dict.fromkeys(_P0_COUNTS, 0)
        self._v2 = {name: 0 for name in _V2_OUTCOMES}
        self._v2_work = {name: _summary() for name in V2_COUNTERS}
        self._v2_duration = _summary()
        self._events = dict.fromkeys(_EVENTS, False)
        self._faults = 0
        self._origin_ns: int | None = None
        self._origin_state = "absent"
        self._listener_ns: int | None = None

    def fault(self) -> None:
        try:
            with self._lock:
                self._faults += 1
        except Exception:
            pass  # An unavailable observer cannot replace an operation error.

    def count(self, event: str) -> None:
        try:
            with self._lock:
                self._p0_counts[event] += 1
        except Exception:
            self.fault()

    def origin(self, token: str | None, launch_id: str | None) -> None:
        """Optional explicitly transported marker, never inherited environment.

        Even a well-formed marker is untrusted local telemetry; it grants no
        source identity, original-parent or performance acceptance proof.
        """
        try:
            state, origin = "absent", None
            if token is not None or launch_id is not None:
                state = "invalid"
                match = re.fullmatch(r"([0-9a-f]{32}):(linux|darwin):([1-9][0-9]{0,19})", token) if (
                    type(token) is str and len(token) <= 60 and type(launch_id) is str and len(launch_id) == 32) else None
                if match is not None and match[1] == launch_id:
                    candidate = int(match[3])
                    if match[2] != _clock_domain():
                        state = "incomparable"
                    elif candidate > self.module_entry_ns:
                        state = "future"
                    elif self.module_entry_ns - candidate > 3_600_000_000_000:
                        state = "stale"
                    else:
                        state, origin = "local_late_exec_untrusted", candidate
            with self._lock:
                self._origin_state, self._origin_ns = state, origin
        except Exception:
            self.fault()

    @contextmanager
    def span(self, name: str) -> Iterator[_Span]:
        # No application exception can be suppressed by this optional observer.
        span, stack = _Span(0), None
        try:
            span.started_ns = observation_time_ns()
            stack = getattr(self._stack, "spans", None)
            if stack is None:
                stack = self._stack.spans = []
            stack.append(span)
        except Exception:
            stack = None
            self.fault()
        try:
            yield span
        except BaseException as exc:
            span.outcome = "failed" if isinstance(exc, Exception) else "incomplete"
            raise
        finally:
            if stack is not None:
                try:
                    stack.pop()  # Always release the retained span before fallible observation.
                    ended = observation_time_ns()
                    elapsed = None if ended is None or span.started_ns is None else ended - span.started_ns
                    if stack:
                        if elapsed is None:
                            stack[-1].child_ns = None
                        elif stack[-1].child_ns is not None:
                            stack[-1].child_ns += elapsed
                    with self._lock:
                        item = self._stages[name]
                        if elapsed is None:
                            item["incomplete"] += 1
                            self._faults += 1
                        else:
                            _add(item["inclusive_ns"], elapsed)
                            if span.child_ns is not None:
                                _add(item["exclusive_ns"], max(0, elapsed - span.child_ns))
                            item[span.outcome] += 1
                            if item["first_started_ns"] is None:
                                item["first_started_ns"] = span.started_ns
                            item["last_ended_ns"] = ended
                except Exception:
                    self.fault()

    def record_interval(self, name: str, start: int | None, end: int | None, outcome: str = "complete") -> None:
        try:
            with self._lock:
                item = self._stages[name]
                if start is None or end is None:
                    item["incomplete"] += 1
                    self._faults += 1
                    return
                _add(item["inclusive_ns"], end - start)
                # No explicitly nested child was measured in this interval.
                _add(item["exclusive_ns"], end - start)
                item[outcome] += 1
                if item["first_started_ns"] is None:
                    item["first_started_ns"] = start
                item["last_ended_ns"] = end
        except Exception:
            self.fault()

    def listener(self, when: int) -> None:
        try:
            with self._lock:
                if self._listener_ns is None:
                    self._listener_ns = when
        except Exception:
            self.fault()

    def reader(self, counters: dict[str, int | None], outcome: str, elapsed_ns: int | None) -> None:
        # Called AFTER the real consuming scope closes; input is fixed scalars.
        with self._lock:
            self._v2[outcome] += 1
            if elapsed_ns is not None:
                _add(self._v2_duration, elapsed_ns)
            for key in V2_COUNTERS:
                value = counters.get(key)
                if type(value) is int and value >= 0:
                    _add(self._v2_work[key], value)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            listener, origin = self._listener_ns, self._origin_ns
            return {"attachments": dict(self._p0_counts), "stages": {k: {**v, "outcome": "incomplete" if v["incomplete"] else "failed" if v["failed"]
                                          else "not_started" if v["inclusive_ns"]["count"] == 0
                                          else "local_hold" if v["local_hold"] else "complete",
                                          "inclusive_ns": dict(v["inclusive_ns"]),
                                          "exclusive_ns": dict(v["exclusive_ns"])} for k, v in self._stages.items()},
                    "v2_lifetimes": dict(self._v2), "v2_lifetime_ns": dict(self._v2_duration),
                    "v2_work": {k: dict(v) for k, v in self._v2_work.items()},
                    "history_payload_bytes": None, "observation_faults": self._faults,
                    "clock": {"origin": self._origin_state, "original_parent": "unavailable",
                              "module_entry_excludes": "interpreter_and_package_initialization",
                              "later_origin_excludes": "shell_uv_python_preflight_and_marker_interpreter_start",
                              "launcher_duration_ns": None, "startup_to_serving_ns": None,
                              "api_usable_ns": None, "ui_usable_ns": None,
                              "module_entry_to_listener_ns": None if listener is None else listener - self.module_entry_ns,
                              "local_late_exec_to_listener_ns": None if listener is None or origin is None else listener - origin,
                              "rss_high_water": None}}

    def emit(self, event: str, *, recovery: dict | None = None) -> None:
        """At most eight aggregate logs per boot; no per-root/query/request log."""
        try:
            with self._lock:
                if self._events[event]:
                    return
                self._events[event] = True
            _LOG.info("startup observation event=%s aggregate=%s recovery=%s", event, self.snapshot(), recovery)
        except Exception:
            self.fault()  # Optional telemetry cannot replace a primary cause.


@contextmanager
def observed(observation: BootObservation | None, name: str) -> Iterator[_Span]:
    if observation is None:
        yield _Span(0)
    else:
        with observation.span(name) as span:
            yield span


@dataclass
class _ReaderObservation:
    reader: Any = None
    outcome: str = "complete"


@contextmanager
def v2_lifetime(observation: BootObservation | None) -> Iterator[_ReaderObservation]:
    owner = _ReaderObservation()
    started_ns = observation_time_ns()
    try:
        yield owner
    except BaseException as exc:
        owner.outcome = "failed" if isinstance(exc, Exception) else "incomplete"
        raise
    finally:
        if observation is not None:
            try:
                # __exit__ has invalidated caches before this outer scope exits.
                ended_ns = observation_time_ns()
                elapsed = None if started_ns is None or ended_ns is None else ended_ns - started_ns
                if owner.reader is None or not owner.reader._closed:
                    observation.reader({}, "incomplete", elapsed)
                else:
                    observation.reader(owner.reader.statistics(), owner.outcome, elapsed)
            except Exception:
                observation.fault()
        owner.reader = None


if __name__ == "__main__" and sys.argv[1:] == ["--launch-clock"]:
    print(launch_clock())

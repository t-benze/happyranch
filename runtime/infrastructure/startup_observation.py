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
from contextlib import AbstractContextManager
from dataclasses import dataclass
from types import TracebackType
from typing import Any

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


def _ordinary_exception(error: BaseException) -> bool:
    # Native hierarchy only: isinstance may consult an opaque error.__class__.
    return any(base is Exception for base in type.__dict__["__mro__"].__get__(type(error)))


class _ObservationErrors:
    """One context's native occurrences; never retained by BootObservation."""

    def __init__(self) -> None:
        self.records: list[dict] = []
        self.last: dict[int, dict] = {}
        self.first: dict | None = None
        self.attachment_failed = False

    def capture(self, phase: str, error: BaseException, *, optional: bool = False,
                delivery: tuple | None = None, retain_prior: bool = True) -> dict:
        # Freeze before hierarchy classification, prior links or any error sink.
        record = dict(phase=phase, error=error,
            arguments=BaseException.args.__get__(error),
            traceback=BaseException.__traceback__.__get__(error),
            context=BaseException.__context__.__get__(error),
            cause=BaseException.__cause__.__get__(error),
            suppress_context=BaseException.__suppress_context__.__get__(error),
            owner=self, previous=None, nested=None, previous_occurrence=None,
            observation=delivery is not None, delivery=delivery, handled=False)
        self.records.append(record)
        record["previous_occurrence"] = self.last.get(id(error))
        self.last[id(error)] = record  # Link EACH throw, never deduplicate it.
        record["handled"] = optional and _ordinary_exception(error)
        if self.first is None and not record["handled"]:
            self.first = record
        if retain_prior:
            try:
                values = BaseException.__dict__["__dict__"].__get__(error)
                record["previous"] = values.get("_startup_observation_outcome")
                if record["previous_occurrence"] is None:
                    record["previous_occurrence"] = values.get("_startup_observation_occurrence")
                record["nested"] = (values.get("_workflow_schema_context"),
                                    values.get("_coding_read_frame"),
                                    values.get("_workflow_authority_failure"),
                                    values.get("_native_writer_disposition"))
            except BaseException as later:
                self.capture("observer prior retention", later, retain_prior=False)
        return record

    def fault(self, observation: BootObservation) -> None:
        try:
            observation.fault()
        except BaseException as error:
            # The original fault policy already swallowed its optional Exception.
            self.capture("observer fault delivery", error)

    def deliver(self, inbound: BaseException | None = None) -> bool:
        if self.first is None:
            return False
        first = self.first
        error = first["error"]
        try:
            BaseException.__setattr__(error, "_startup_observation_outcome", self)
            BaseException.__setattr__(error, "_startup_observation_occurrence", first)
        except BaseException as later:
            self.attachment_failed = True
            self.capture("observer attachment", later)
        try:
            BaseException.args.__set__(error, first["arguments"])
            BaseException.__traceback__.__set__(error, first["traceback"])
            BaseException.__context__.__set__(error, first["context"])
            BaseException.__cause__.__set__(error, first["cause"])
            BaseException.__suppress_context__.__set__(error, first["suppress_context"])
        except BaseException as later:
            self.attachment_failed = True
            self.capture("observer native restoration", later)
        if error is inbound and not self.attachment_failed:
            return False
        # A failed external attachment leaves this real record in the delivery
        # frame. Do not retry a sink or adopt stale attachments as current proof.
        raise error


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

    def span(self, name: str) -> AbstractContextManager[_Span]:
        return _ObservedSpan(self, name)

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


class _ObservedSpan:
    def __init__(self, observation: BootObservation | None, name: str) -> None:
        self.observation, self.name = observation, name
        self.span = _Span(0)
        self.stack = None
        self.errors = _ObservationErrors()

    def __enter__(self) -> _Span:
        observation, span = self.observation, self.span
        if observation is not None:
            try:
                span.started_ns = observation_time_ns()
                stack = getattr(observation._stack, "spans", None)
                if stack is None:
                    stack = observation._stack.spans = []
                stack.append(span)
                self.stack = stack
            except BaseException as error:
                self.errors.capture("span entry observation", error, optional=True)
                if _ordinary_exception(error):
                    self.errors.fault(observation)
        self.errors.deliver()
        return span

    def _aggregate(self, elapsed: int | None, ended: int | None) -> None:
        observation, span = self.observation, self.span
        lock = observation._lock
        lock_exit = type(lock).__exit__

        def release(record: dict | None) -> tuple[bool, bool]:
            try:
                error = None if record is None else record["error"]
                result = lock_exit(lock, None if record is None else type(error),
                                   error, None if record is None else record["traceback"])
                return True, False if record is None else bool(result)
            except BaseException as later:
                self.errors.capture("span lock exit", later, optional=True)
                if _ordinary_exception(later):
                    self.errors.fault(observation)
                return False, False

        type(lock).__enter__(lock)
        try:
            item = observation._stages[self.name]
            if elapsed is None:
                item["incomplete"] += 1
                observation._faults += 1
            else:
                _add(item["inclusive_ns"], elapsed)
                if span.child_ns is not None:
                    _add(item["exclusive_ns"], max(0, elapsed - span.child_ns))
                item[span.outcome] += 1
                if item["first_started_ns"] is None:
                    item["first_started_ns"] = span.started_ns
                item["last_ended_ns"] = ended
        except BaseException as later:
            # Freeze the original aggregate error BEFORE its original lock exit.
            record = self.errors.capture("span aggregate", later, optional=True)
            released, suppressed = release(record)
            if suppressed:
                record["handled"] = True
                self.errors.first = next((r for r in self.errors.records if not r["handled"]), None)
            elif released and _ordinary_exception(later):
                self.errors.fault(observation)
        else:
            release(None)

    def __exit__(self, kind: type[BaseException] | None, error: BaseException | None,
                 traceback: TracebackType | None) -> bool:
        span, observation, stack = self.span, self.observation, self.stack
        if error is not None:
            # Runtime exit arguments are an inbound body observation, not a
            # second throw inferred from an equal object or phase label.
            self.errors.capture("span body delivery", error, delivery=(kind, traceback))
            if observation is not None:
                span.outcome = "failed" if _ordinary_exception(error) else "incomplete"
        self.stack = None
        if stack is not None:
            try:
                stack.pop()  # Original release precedes ALL fallible observation.
                ended = observation_time_ns()
                elapsed = None if ended is None or span.started_ns is None else ended - span.started_ns
                if stack:
                    if elapsed is None:
                        stack[-1].child_ns = None
                    elif stack[-1].child_ns is not None:
                        stack[-1].child_ns += elapsed
                self._aggregate(elapsed, ended)
            except BaseException as later:
                self.errors.capture("span exit observation", later, optional=True)
                if _ordinary_exception(later):
                    self.errors.fault(observation)
        return self.errors.deliver(error)


def observed(observation: BootObservation | None, name: str) -> AbstractContextManager[_Span]:
    # No generator layer, including bootstrap without an observer. The real
    # span context receives the caller's original runtime exit arguments once.
    return _ObservedSpan(None, name) if observation is None else observation.span(name)


@dataclass
class _ReaderObservation:
    reader: Any = None
    outcome: str = "complete"


class _ObservedReader:
    def __init__(self, observation: BootObservation | None) -> None:
        self.observation = observation
        self.owner = _ReaderObservation()
        self.started_ns = None
        self.errors = _ObservationErrors()

    def __enter__(self) -> _ReaderObservation:
        try:
            self.started_ns = observation_time_ns()
        except BaseException as error:
            self.errors.capture("reader observation entry", error)
            self.owner.reader = None
            self.errors.deliver()
        return self.owner

    def __exit__(self, kind: type[BaseException] | None, error: BaseException | None,
                 traceback: TracebackType | None) -> bool:
        owner, observation = self.owner, self.observation
        if error is not None:
            self.errors.capture("reader body delivery", error, delivery=(kind, traceback))
            owner.outcome = "failed" if _ordinary_exception(error) else "incomplete"
        try:
            if observation is not None:
                try:
                    # The real reader __exit__ has already invalidated caches.
                    ended_ns = observation_time_ns()
                    elapsed = None if self.started_ns is None or ended_ns is None else ended_ns - self.started_ns
                    if owner.reader is None or not owner.reader._closed:
                        observation.reader({}, "incomplete", elapsed)
                    else:
                        observation.reader(owner.reader.statistics(), owner.outcome, elapsed)
                except BaseException as later:
                    self.errors.capture("reader exit observation", later, optional=True)
                    if _ordinary_exception(later):
                        self.errors.fault(observation)
        finally:
            # Independently owed even when observation or fault delivery fails.
            owner.reader = None
        return self.errors.deliver(error)


def v2_lifetime(observation: BootObservation | None) -> AbstractContextManager[_ReaderObservation]:
    return _ObservedReader(observation)


if __name__ == "__main__" and sys.argv[1:] == ["--launch-clock"]:
    print(launch_clock())

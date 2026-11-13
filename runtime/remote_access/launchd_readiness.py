"""Connector-owned launchd readiness and watchdog; no systemd notification.

The record is an observation, never an authentication or process-control input.
Only the foreground connector writes it. A watchdog exit needs no main-loop,
filesystem or lifecycle lock, allowing KeepAlive to recover a blocked pass.
"""
from __future__ import annotations

import errno
import json
import math
import os
import stat
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from runtime.remote_access.launchd_agent import LaunchdStatus
from runtime.remote_access.launchd_files import literal_directory
from runtime.remote_access.service_manager import ServiceManagerError

WATCHDOG_EXIT_CODE = 75
_MAX_RECORD_BYTES = 4096
_REASONS = {"starting", "healthy", "waiting-for-readiness", "readiness-or-listener-lost", "stopping"}
_RECORD_KEYS = {"pid", "process_started_at", "ready", "last_healthy_wall_time", "sequence", "reason"}


def _interval(value: float) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ServiceManagerError("macOS connector intervals must be finite and positive")
    return float(value)


class LaunchdReadiness:
    """Translate the existing notify seam into atomic records and healthy ticks."""

    def __init__(self, path: Path, watchdog_sec: float) -> None:
        self.path = path
        self.watchdog_sec = _interval(watchdog_sec)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_pass = 0.0
        self._record: dict = {}

    def start(self) -> None:
        # The initial deadline also bounds startup/readiness and publication.
        self._last_pass = time.monotonic()
        self._record = dict(pid=os.getpid(), process_started_at=time.time(), ready=False,
                            last_healthy_wall_time=None, sequence=0, reason="starting")
        self._thread = threading.Thread(target=self._watch, name="connector-watchdog", daemon=True)
        self._thread.start()
        self._publish()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def _watch(self) -> None:
        while not self._stop.is_set():
            remaining = self.watchdog_sec - (time.monotonic() - self._last_pass)
            if remaining <= 0:
                # Do not acquire a loop lock or write a record here: the main
                # thread may be stuck holding that lock or in filesystem I/O.
                os._exit(WATCHDOG_EXIT_CODE)
            self._stop.wait(remaining)

    def __call__(self, state: str) -> bool:
        if not self._record:
            return False  # construction/status/shutdown before run has no writes
        if state in ("READY=1\n", "WATCHDOG=1\n"):
            self._record.update(ready=True, last_healthy_wall_time=time.time(), reason="healthy")
            self._publish()
            # A stalled publication is not a completed healthy pass.
            self._last_pass = time.monotonic()
        elif state == "STOPPING=1\n":
            self._record["ready"] = False
            if self._record["reason"] != "readiness-or-listener-lost":
                self._record["reason"] = "stopping"
            self._publish()
        elif state.startswith("STATUS="):
            # Never persist arbitrary STATUS text (including exception/path or
            # provider input). These fixed categories contain no bearer material.
            lost = self._record["last_healthy_wall_time"] is not None
            self._record.update(ready=False, reason="readiness-or-listener-lost" if lost else "waiting-for-readiness")
            self._publish()
        return True

    def _publish(self) -> None:
        self._record["sequence"] += 1
        with literal_directory(self.path.parent, create=True) as parent:
            temporary = f".{self.path.name}.{uuid.uuid4().hex}"
            # Same exclusive 0600 temp + flush/fsync + rename publication as
            # launchd_agent's backup. Failed writes retain the prior complete file.
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=parent)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    json.dump(self._record, stream, sort_keys=True, allow_nan=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path.name, src_dir_fd=parent, dst_dir_fd=parent)
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass


@dataclass(frozen=True)
class LaunchdReadinessStatus(LaunchdStatus):
    """Additive CLI observations; inherited native process fields stay intact."""

    ready: bool = False
    readiness_state: str = "record-missing"
    readiness_record_state: str = "missing"
    readiness_max_age_seconds: float = 0.0
    readiness_age_seconds: float | None = None
    record_pid: int | None = None
    process_started_at: float | None = None
    last_healthy_wall_time: float | None = None
    sequence: int | None = None
    readiness_reason: str | None = None


def _timestamp(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _record_object(pairs: list[tuple[str, object]]) -> dict:
    value: dict = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate readiness field")
        value[key] = item
    return value


def _read_record(path: Path) -> tuple[str, dict]:
    try:
        with literal_directory(path.parent) as parent:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                return "foreign", {}
            raw = stream.read(_MAX_RECORD_BYTES + 1)
        if len(raw) > _MAX_RECORD_BYTES:
            return "corrupt", {}
        value = json.loads(raw, object_pairs_hook=_record_object)
        if not isinstance(value, dict) or value.keys() != _RECORD_KEYS:
            return "corrupt", {}
        healthy = value["last_healthy_wall_time"]
        if (type(value["pid"]) is not int or value["pid"] <= 0
                or type(value["ready"]) is not bool
                or type(value["sequence"]) is not int or value["sequence"] < 1
                or not _timestamp(value["process_started_at"])
                or (healthy is not None and (not _timestamp(healthy) or healthy < value["process_started_at"]))
                or (value["ready"] and healthy is None)
                or value["reason"] not in _REASONS):
            return "corrupt", {}
        return "valid", value
    except FileNotFoundError:
        return "missing", {}
    except (ValueError, UnicodeError, TypeError, OverflowError, RecursionError):
        return "corrupt", {}
    except OSError as exc:
        return ("foreign" if exc.errno == errno.ELOOP else "unavailable"), {}


def readiness_status(
    process: LaunchdStatus, path: Path, *, watchdog_sec: float, poll_seconds: float,
) -> LaunchdReadinessStatus:
    """Require a live matching PID plus fresh affirmative readiness.

    One configured poll interval is the observation/scheduling margin. Future
    healthy timestamps fail freshness too; a backward wall-clock jump cannot
    extend readiness indefinitely. The self-watchdog itself uses monotonic time.
    """
    max_age = _interval(watchdog_sec) + _interval(poll_seconds)
    record_state, record = _read_record(path)
    healthy = record.get("last_healthy_wall_time")
    age = time.time() - healthy if healthy is not None else None
    if process.load_state != "loaded":
        state = "not-loaded"
    elif not process.running or process.pid <= 0:
        state = "starting"
    elif record_state != "valid":
        state = f"record-{record_state}"
    elif record["pid"] != process.pid:
        state = "pid-mismatch"
    elif not record["ready"]:
        state = "running-not-ready"
    elif age is None or not 0 <= age <= max_age:
        state = "stale-readiness"
    else:
        state = "ready"
    return LaunchdReadinessStatus(
        **process.__dict__, ready=state == "ready", readiness_state=state,
        readiness_record_state=record_state, readiness_max_age_seconds=max_age,
        readiness_age_seconds=age, record_pid=record.get("pid"),
        process_started_at=record.get("process_started_at"), last_healthy_wall_time=healthy,
        sequence=record.get("sequence"), readiness_reason=record.get("reason"),
    )

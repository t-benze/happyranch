#!/usr/bin/env python3
"""Create the diagnostic harness from the pinned shipping startup literally."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import selectors
import signal
import subprocess
import time
from pathlib import Path
from typing import Callable, Sequence

START = "# semantic evidence: startup\n"
PROBE = "capture_denial_matrix shipping-unit\n"
FIRST_POSITIVE = "sudo systemctl start happyranch-managed.target\n"
# The diagnostic subject is a historical shipping commit, not this checkout's
# current tree.  Refuse even boundary-preserving substitutions.
FROZEN_SHIPPING_SHA256 = "f63c0e5eef23468a454b896a24515a6db62fdf674cb8bffd250ca7a0477d2301"
UNITS = ("happyranch-managed.target", "happyranch-tsnet-sidecar.service", "happyranch-connector.service")
PHASES = frozenset(("negative", "prepositive", "positive_failure"))
MAX_BYTES = 4096
MAX_LINES = 32
MAX_RECORDS = 16
ALLOWED_RESULT = frozenset(("success", "exit-code", "signal", "timeout", "resources", "protocol", "unknown"))
ALLOWED_ACTIVE = frozenset(("active", "inactive", "failed", "activating", "deactivating"))
ALLOWED_SUB = frozenset(("running", "dead", "failed", "exited", "auto-restart", "start-pre", "start"))
CAUSES = frozenset(("credential_missing", "credential_consumed", "exec_start_pre_failed", "main_exited", "timeout", "permission_denied"))
PATHS = ("/etc/happyranch/enrollment.key", "/etc/happyranch/enrollment.key.held", "/etc/systemd/system/happyranch-tsnet-sidecar.service.d/10-enrollment-credential.conf", "/run/credentials/happyranch-tsnet-sidecar.service", "/var/lib/happyranch-tsnet-sidecar/credential.consumed")


class ExtractionError(ValueError):
    """The immutable subject is not the expected shipping harness."""


def extract_startup(source: str, *, expected_digest: str = FROZEN_SHIPPING_SHA256) -> str:
    """Return literal setup/helpers plus startup through the first real start."""
    if hashlib.sha256(source.encode()).hexdigest() != expected_digest:
        raise ExtractionError("unexpected shipping source digest")
    if source.count(START) != 1 or source.count(PROBE) != 1:
        raise ExtractionError("unexpected shipping startup boundaries")
    start = source.index(START)
    probe = source.index(PROBE, start)
    first = source.find(FIRST_POSITIVE, probe + len(PROBE))
    if first < 0 or source.find(FIRST_POSITIVE, first + len(FIRST_POSITIVE)) < 0:
        raise ExtractionError("missing or ambiguous first positive start")
    return source[:start] + source[start : first + len(FIRST_POSITIVE)]


def write_extraction(source: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(extract_startup(source.read_text()))


class RunResult:
    """Only bounded, non-content process observations cross this boundary."""

    def __init__(self, returncode: int | None, stdout: bytes = b"", stderr: bytes = b"", truncated: bool = False, timed_out: bool = False) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.truncated = truncated
        self.timed_out = timed_out


Runner = Callable[[Sequence[str], float], RunResult]


def run_bounded(command: Sequence[str], deadline: float, *, now: Callable[[], float] = time.monotonic) -> RunResult:
    """Drain both pipes within a shared deadline; never retain their prose."""
    remaining = deadline - now()
    if remaining <= 0:
        return RunResult(None, timed_out=True)
    proc = subprocess.Popen(list(command), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    assert proc.stdout is not None and proc.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    selector.register(proc.stderr, selectors.EVENT_READ)
    captured = 0
    truncated = False
    try:
        while selector.get_map():
            remaining = deadline - now()
            if remaining <= 0:
                raise TimeoutError
            events = selector.select(min(remaining, 0.05))
            for key, _ in events:
                data = os.read(key.fileobj.fileno(), min(65536, max(1, MAX_BYTES - captured + 1)))
                if not data:
                    selector.unregister(key.fileobj)
                elif captured + len(data) > MAX_BYTES:
                    captured = MAX_BYTES
                    truncated = True
                else:
                    captured += len(data)
            if proc.poll() is not None and not events:
                # A descendant can retain a pipe after its parent exits.
                raise TimeoutError
        return RunResult(proc.wait(timeout=max(0.01, deadline - now())), truncated=truncated)
    except (TimeoutError, OSError, subprocess.SubprocessError):
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=0.2)
        except (OSError, subprocess.SubprocessError):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            proc.wait()
        return RunResult(proc.returncode, truncated=truncated, timed_out=True)
    finally:
        selector.close()


def _integer(value: str) -> int | None:
    return int(value) if value.isascii() and value.isdecimal() and len(value) <= 18 else None


def _properties(text: bytes) -> dict[str, object] | None:
    """Parse systemctl's fixed key/value response without accepting prose."""
    if len(text) > MAX_BYTES or text.count(b"\n") > MAX_LINES:
        return None
    values: dict[str, str] = {}
    allowed = {"Result", "ActiveState", "SubState", "MainPID", "NRestarts", "InvocationID", "ActiveEnterTimestampMonotonic", "ExecStartPreCode", "ExecStartPreStatus", "ExecMainCode", "ExecMainStatus"}
    for raw in text.splitlines():
        try:
            key, value = raw.decode("ascii").split("=", 1)
        except (UnicodeDecodeError, ValueError):
            return None
        if key not in allowed or key in values or len(value) > 64:
            return None
        values[key] = value
    if not values:
        return None
    output: dict[str, object] = {}
    for key, value in values.items():
        if key == "Result" and value in ALLOWED_RESULT: output[key] = value
        elif key == "ActiveState" and value in ALLOWED_ACTIVE: output[key] = value
        elif key == "SubState" and value in ALLOWED_SUB: output[key] = value
        elif key == "InvocationID" and len(value) == 32 and all(c in "0123456789abcdef" for c in value): output[key] = value
        elif key != "InvocationID" and (number := _integer(value)) is not None: output[key] = number
        else: return None
    return output


def collect(phase: str, output: Path, deadline: float, runner: Runner, *, paths: Sequence[str] = PATHS, now: Callable[[], float] = time.monotonic) -> dict[str, object]:
    """Persist secret-free, fail-closed causal observations for one seam.

    The later shell envelope owns when this is called.  This collector never
    starts, stops, waits for, or changes a shipping service.
    """
    if phase not in PHASES or deadline <= now():
        document: dict[str, object] = {"phase": phase if phase in PHASES else "unavailable", "availability": "unavailable"}
        output.write_text(json.dumps(document, sort_keys=True))
        return document
    document = {"phase": phase, "units": {}, "paths": {}, "job": {"availability": "unavailable"}, "journal": []}
    for unit in UNITS:
        result = runner(("systemctl", "show", unit), deadline - now())
        parsed = None if result.timed_out or result.truncated else _properties(result.stdout)
        document["units"][unit] = parsed if parsed is not None else {"availability": "unavailable"}
    for path in paths:
        # Caller supplies a fixed test command; only boolean exit status survives.
        result = runner(("test", "-e", path), deadline - now())
        document["paths"][path] = {"present": result.returncode == 0} if result.returncode in (0, 1) and not result.timed_out else {"availability": "unavailable"}
    journal = runner(("journalctl", "--diagnostic-fixed-format"), deadline - now())
    if not journal.timed_out and not journal.truncated and len(journal.stdout) <= MAX_BYTES:
        for line in journal.stdout.splitlines()[:MAX_RECORDS]:
            try:
                item = json.loads(line)
                unit, cause, stamp = item["unit"], item["cause"], item["timestamp"]
            except (ValueError, KeyError, TypeError):
                continue
            if unit in UNITS and cause in CAUSES and isinstance(stamp, int) and 0 <= stamp <= 10**18:
                document["journal"].append({"unit": unit, "cause": cause, "timestamp": stamp})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, sort_keys=True))
    return document


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--extract", action="store_true")
    parser.add_argument("--shipping", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.extract or args.shipping is None or args.output is None:
        parser.error("--extract, --shipping, and --output are required")
    try:
        write_extraction(args.shipping, args.output)
    except (OSError, ExtractionError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

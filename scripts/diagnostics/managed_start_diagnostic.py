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
MAX_WINDOW_SECONDS = 3600
MAX_BUDGET_SECONDS = 60
ALLOWED_RESULT = frozenset(("success", "exit-code", "signal", "timeout", "resources", "protocol", "unknown"))
ALLOWED_ACTIVE = frozenset(("active", "inactive", "failed", "activating", "deactivating"))
ALLOWED_SUB = frozenset(("running", "dead", "failed", "exited", "auto-restart", "start-pre", "start"))
OPTIONAL_PROPERTIES = frozenset(("MainPID", "NRestarts", "InvocationID", "ActiveEnterTimestampMonotonic", "ExecStartPre", "ExecMainCode", "ExecMainStatus"))
CAUSES = frozenset(("credential_missing", "credential_consumed", "exec_start_pre_failed", "main_exited", "timeout", "permission_denied"))
# These are the five paths in the frozen harness.  The observer deliberately
# accepts no caller-supplied paths: it reports metadata only and never opens a
# credential.
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


def _write_document(output: Path, document: dict[str, object]) -> None:
    """Atomically replace an artifact with already-redacted structured data."""
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.diagnostic-tmp")
    try:
        with open(temporary, "x", encoding="ascii") as handle:
            os.chmod(temporary, 0o600)
            handle.write(json.dumps(document, sort_keys=True, separators=(",", ":")))
        os.replace(temporary, output)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


class RunResult:
    """Only bounded, non-content process observations cross this boundary."""

    def __init__(self, returncode: int | None, stdout: bytes = b"", stderr: bytes = b"", truncated: bool = False, timed_out: bool = False, failed: bool = False) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.truncated = truncated
        self.timed_out = timed_out
        self.failed = failed


Runner = Callable[[Sequence[str], float], RunResult]


def run_bounded(command: Sequence[str], deadline: float, *, now: Callable[[], float] = time.monotonic) -> RunResult:
    """Run one observer command to an absolute monotonic deadline.

    Bytes are retained only long enough for the strict parsers below.  They
    never cross this module's artifact/log boundary.
    """
    remaining = deadline - now()
    if remaining <= 0:
        return RunResult(None, timed_out=True, failed=True)
    try:
        proc = subprocess.Popen(list(command), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    except (OSError, subprocess.SubprocessError):
        return RunResult(None, failed=True)
    assert proc.stdout is not None and proc.stderr is not None
    selector: selectors.BaseSelector | None = None
    captured = {proc.stdout: bytearray(), proc.stderr: bytearray()}
    truncated = False
    try:
        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ)
        selector.register(proc.stderr, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - now()
            if remaining <= 0:
                raise TimeoutError
            events = selector.select(min(remaining, 0.05))
            for key, _ in events:
                data = os.read(key.fileobj.fileno(), 65536)
                if not data:
                    selector.unregister(key.fileobj)
                elif len(captured[key.fileobj]) + len(data) > MAX_BYTES:
                    truncated = True
                    raise TimeoutError
                else:
                    captured[key.fileobj].extend(data)
            if proc.poll() is not None and not events:
                # A descendant can retain a pipe after its parent exits.
                raise TimeoutError
        return RunResult(proc.wait(timeout=max(0.01, deadline - now())), bytes(captured[proc.stdout]), bytes(captured[proc.stderr]), truncated=truncated)
    except (TimeoutError, OSError, subprocess.SubprocessError):
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            proc.wait(timeout=0.2)
        except subprocess.SubprocessError:
            pass
        # The leader may have exited while a child still owns a pipe; the
        # process group, not leader wait(), defines observation completion.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            proc.wait()
        except subprocess.SubprocessError:
            pass
        return RunResult(proc.returncode, bytes(captured[proc.stdout]), bytes(captured[proc.stderr]), truncated=truncated, timed_out=True, failed=True)
    finally:
        for stream in (proc.stdout, proc.stderr):
            try:
                if selector is not None:
                    selector.unregister(stream)
            except (AttributeError, KeyError, OSError):
                pass
            stream.close()
        if selector is not None:
            selector.close()


def _integer(value: str) -> int | None:
    return int(value) if value.isascii() and value.isdecimal() and len(value) <= 18 else None


def _exec_status(value: str) -> dict[str, object] | None:
    """Read systemd's structured ExecStartPre record without argv/path."""
    if not value.startswith("{") or not value.endswith("}") or len(value) > 1024:
        return None
    fields: dict[str, str] = {}
    for part in value[1:-1].split(";"):
        if "=" not in part:
            continue
        key, item = part.strip().split("=", 1)
        if key in fields:
            return None
        fields[key] = item
    code, status = fields.get("code"), fields.get("status")
    # show_exec_status() appends a symbolic signal suffix (15/TERM) for
    # killed records; the numeric prefix is the stable typed evidence.
    numeric_status = (status or "").split("/", 1)[0]
    if code not in {"exited", "killed", "dumped"} or (number := _integer(numeric_status)) is None:
        return None
    return {"code": code, "status": number}


def _properties(text: bytes) -> dict[str, object] | None:
    """Parse systemctl's fixed key/value response without accepting prose."""
    if len(text) > MAX_BYTES or text.count(b"\n") > MAX_LINES:
        return None
    values: dict[str, list[str]] = {}
    allowed = {"Result", "ActiveState", "SubState", "MainPID", "NRestarts", "InvocationID", "ActiveEnterTimestampMonotonic", "ExecStartPre", "ExecMainCode", "ExecMainStatus"}
    for raw in text.splitlines():
        try:
            key, value = raw.decode("ascii").split("=", 1)
        except (UnicodeDecodeError, ValueError):
            return None
        if key not in allowed or len(value) > (1024 if key == "ExecStartPre" else 64):
            return None
        values.setdefault(key, []).append(value)
    if not values:
        return None
    output: dict[str, object] = {}
    for key, raw_values in values.items():
        if key != "ExecStartPre" and len(raw_values) != 1:
            return None
        value = raw_values[0]
        # `systemctl show` represents an unset optional property as an empty
        # assignment. It must not erase independently valid state/result.
        if key in OPTIONAL_PROPERTIES and value == "":
            output[key] = {"availability": "not_applicable"}
            continue
        if key == "Result" and value in ALLOWED_RESULT: output[key] = value
        elif key == "ActiveState" and value in ALLOWED_ACTIVE: output[key] = value
        elif key == "SubState" and value in ALLOWED_SUB: output[key] = value
        elif key == "InvocationID" and len(value) == 32 and all(c in "0123456789abcdef" for c in value): output[key] = value
        elif key == "ExecStartPre" and all((record := _exec_status(item)) is not None for item in raw_values): output[key] = [_exec_status(item) for item in raw_values]
        elif key in {"MainPID", "NRestarts", "ActiveEnterTimestampMonotonic", "ExecMainStatus"} and (number := _integer(value)) is not None: output[key] = number
        # systemctl show v255 renders ExecMainCode as the numeric wait-code.
        elif key == "ExecMainCode" and (number := _integer(value)) is not None and number in {0, 1, 2, 3}: output[key] = number
        else:
            return None
    return output


PROPERTY_ARGS = ("--no-pager", "--property=Result", "--property=ActiveState", "--property=SubState", "--property=MainPID", "--property=NRestarts", "--property=InvocationID", "--property=ActiveEnterTimestampMonotonic", "--property=ExecStartPre", "--property=ExecMainCode", "--property=ExecMainStatus")


def _path_metadata(result: RunResult) -> dict[str, object]:
    # A small helper prints errno first.  GNU stat's exit status is not an
    # errno, so only ENOENT proves absence.
    if result.timed_out or result.truncated or result.returncode != 0:
        return {"availability": "unavailable"}
    try:
        fields = result.stdout.decode("ascii").strip().split(":")
        if fields == ["ENOENT"]:
            return {"present": False}
        if len(fields) != 4 or fields[0] != "PRESENT":
            raise ValueError
        _, mode, uid, gid = fields
        if not mode or len(mode) > 8 or not all(c in "0123456789abcdef" for c in mode) or (owner := _integer(uid)) is None or (group := _integer(gid)) is None:
            raise ValueError
    except (UnicodeDecodeError, ValueError):
        return {"availability": "unavailable"}
    return {"present": True, "custody": {"owner_uid": owner, "owner_gid": group, "mode_hex": mode}}


def _journal_records(raw: bytes) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    if len(raw) > MAX_BYTES or raw.count(b"\n") > MAX_LINES:
        return records
    for line in raw.splitlines()[:MAX_RECORDS]:
        try:
            item = json.loads(line)
            if not isinstance(item, dict):
                continue
            unit = item.get("JOB_UNIT") or item.get("UNIT") or item.get("_SYSTEMD_UNIT")
            stamp = item.get("__MONOTONIC_TIMESTAMP")
            message = item.get("MESSAGE")
            if unit not in UNITS or not isinstance(stamp, str) or (number := _integer(stamp)) is None or not isinstance(message, str):
                continue
        except (ValueError, TypeError):
            continue
        lowered = message.lower()
        cause = next((candidate for candidate in CAUSES if candidate.replace("_", " ") in lowered or candidate in lowered), None)
        if cause:
            records.append({"unit": unit, "cause": cause, "timestamp": number})
    return records


def _job_records(raw: bytes) -> list[dict[str, object]]:
    """Extract only a structured systemd job record; never infer it from Result."""
    if len(raw) > MAX_BYTES or raw.count(b"\n") > MAX_LINES:
        return []
    records: list[dict[str, object]] = []
    for line in raw.splitlines()[:MAX_RECORDS]:
        try:
            item = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(item, dict):
            continue
        unit, job_id, result = item.get("JOB_UNIT") or item.get("UNIT"), item.get("JOB_ID"), item.get("JOB_RESULT")
        if unit in UNITS and isinstance(job_id, str) and (number := _integer(job_id)) is not None and isinstance(result, str) and result in {"done", "failed", "canceled", "timeout", "dependency", "skipped"}:
            record = {"availability": "available", "unit": unit, "id": number, "result": result}
            if record not in records:
                records.append(record)
    return records


def collect(phase: str, output: Path, deadline: float, runner: Runner = run_bounded, *, window: tuple[int, int] | None = None, now: Callable[[], float] = time.monotonic) -> dict[str, object]:
    """Persist secret-free, fail-closed causal observations for one seam.

    The later shell envelope owns when this is called.  This collector never
    starts, stops, waits for, or changes a shipping service.
    """
    if phase not in PHASES or deadline <= now():
        document: dict[str, object] = {"phase": phase if phase in PHASES else "unavailable", "availability": "unavailable"}
        _write_document(output, document)
        return document
    if window is None or not all(isinstance(item, int) and 0 <= item <= 2**63 - 1 for item in window) or window[0] > window[1]:
        document = {"phase": phase, "units": {}, "paths": {}, "jobs": {unit: {"availability": "unavailable", "reason": "window_unavailable"} for unit in UNITS}, "journal": {"availability": "unavailable", "reason": "window_unavailable"}}
        _write_document(output, document); return document
    document = {"phase": phase, "units": {}, "paths": {}, "jobs": {unit: {"availability": "unavailable", "reason": "no_record"} for unit in UNITS}, "journal": []}
    for unit in UNITS:
        result = runner(("systemctl", "show", unit, *PROPERTY_ARGS), deadline)
        parsed = None if result.returncode != 0 or result.timed_out or result.truncated else _properties(result.stdout)
        document["units"][unit] = parsed if parsed is not None else {"availability": "unavailable"}
    for path in PATHS:
        result = runner(("sudo", "-n", "python3", "-c", "import os,stat,sys; p=sys.argv[1];\ntry:\n s=os.stat(p,follow_symlinks=False); print('PRESENT:%x:%d:%d' % (stat.S_IMODE(s.st_mode),s.st_uid,s.st_gid))\nexcept OSError as e:\n print('ENOENT' if e.errno==2 else 'UNAVAILABLE'); sys.exit(0)", path), deadline)
        document["paths"][path] = _path_metadata(result)
    for unit in (*UNITS, "init.scope"):
        journal = runner(("journalctl", "--no-pager", "--output=json", f"--lines={MAX_RECORDS}", f"--since=@{window[0]}", f"--until=@{window[1]}", f"--unit={unit}"), deadline)
        if journal.returncode == 0 and not journal.timed_out and not journal.truncated:
            for job in _job_records(journal.stdout):
                document["jobs"][job["unit"]] = job
            for record in _journal_records(journal.stdout):
                if record not in document["journal"]:
                    document["journal"].append(record)
        elif unit in UNITS and document["jobs"][unit]["reason"] == "no_record":
            document["jobs"][unit] = {"availability": "unavailable", "reason": "query_failed"}
    _write_document(output, document)
    return document


class _SafeParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        self.exit(2, "invalid diagnostic arguments\n")


def main() -> int:
    parser = _SafeParser(add_help=False)
    parser.add_argument("--extract", action="store_true")
    parser.add_argument("--shipping", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--capture", action="store_true")
    parser.add_argument("--phase")
    parser.add_argument("--window-start", type=int)
    parser.add_argument("--window-end", type=int)
    parser.add_argument("--budget-seconds", type=int)
    args = parser.parse_args()
    if args.extract == args.capture or args.output is None:
        parser.error("mode")
    if args.extract and args.shipping is None:
        parser.error("shipping")
    if args.capture and (args.phase not in PHASES or args.window_start is None or args.window_end is None or args.budget_seconds is None or args.window_start < 0 or args.window_end < args.window_start or args.window_end - args.window_start > MAX_WINDOW_SECONDS or not 1 <= args.budget_seconds <= MAX_BUDGET_SECONDS):
        parser.error("capture")
    try:
        if args.extract:
            write_extraction(args.shipping, args.output)
        else:
            deadline = time.monotonic() + args.budget_seconds
            collect(args.phase, args.output, deadline, window=(args.window_start, args.window_end))
    except (OSError, ExtractionError, ValueError, subprocess.SubprocessError):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

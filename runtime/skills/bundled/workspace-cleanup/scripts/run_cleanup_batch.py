#!/usr/bin/env python3
"""Durable bounded batch driver for the bundled workspace cleanup runner."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any


_ROW_KEYS = {
    "candidate", "containing", "kind", "allocated_bytes", "argv",
    "started_at", "ended_at", "terminal", "exit_code", "receipt",
    "stdout", "stderr", "error",
}
_PRE_ACTION_DECISIONS = {"refused", "report_only", "inventory_only"}
_REMOVAL_DECISIONS = {"removed_cache", "removed_worktree"}
_ANOMALY_DECISIONS = {"removed_with_anomaly", "isolation_anomaly"}
_MEASUREMENT_KEYS = {
    "path", "apparent_bytes_before", "allocated_bytes_before",
    "apparent_bytes_after", "allocated_bytes_after",
    "filesystem_free_before", "filesystem_free_after", "filesystem_free_delta",
}


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _load_manifest(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        value = [json.loads(line) for line in text.splitlines() if line.strip()]
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        raise ValueError("manifest must be a JSON array or JSONL rows")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {
            "candidate", "containing", "kind", "allocated_bytes",
        }:
            raise ValueError("manifest row has an invalid schema")
        candidate = item["candidate"]
        containing = item["containing"]
        kind = item["kind"]
        size = item["allocated_bytes"]
        if (not isinstance(candidate, str) or not candidate.startswith("/")
                or not isinstance(containing, str) or not containing.startswith("/")
                or kind not in {"worktree", "cache"}
                or isinstance(size, bool) or not isinstance(size, int) or size < 0):
            raise ValueError("manifest row contains an invalid value")
        if candidate in seen:
            raise ValueError("manifest contains a duplicate candidate")
        seen.add(candidate)
        rows.append(dict(item))
    return sorted(
        rows,
        key=lambda row: (
            0 if row["kind"] == "worktree" else 1,
            -row["allocated_bytes"],
            row["candidate"],
        ),
    )


def _command(runner: Path, item: dict[str, Any]) -> list[str]:
    return ["bash", str(runner), item["candidate"], item["containing"]]


def _timestamp(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _integer(value: Any, *, negative: bool = False) -> bool:
    return (not isinstance(value, bool) and isinstance(value, int)
            and (negative or value >= 0))


def _measurement_receipt(
    receipt: dict[str, Any], *, allow_unavailable_after: bool = False,
) -> bool:
    if not _MEASUREMENT_KEYS <= set(receipt):
        return False
    if not isinstance(receipt["path"], str) or not receipt["path"].startswith("/"):
        return False
    for key in _MEASUREMENT_KEYS - {"path", "filesystem_free_delta"}:
        value = receipt[key]
        if (value is None and not (
                allow_unavailable_after and key in {
                    "apparent_bytes_after", "allocated_bytes_after",
                })) or (value is not None and not _integer(value)):
            return False
    return _integer(receipt["filesystem_free_delta"], negative=True)


def _anomaly_measurements(receipt: dict[str, Any]) -> bool:
    expected = _MEASUREMENT_KEYS | {
        "decision", "anomaly", "residual_locations", "measurement_error",
    }
    if (set(receipt) != expected
            or not _measurement_receipt(receipt, allow_unavailable_after=True)):
        return False
    locations = receipt["residual_locations"]
    if not isinstance(locations, dict) or set(locations) != {
        "original", "isolated_candidate", "isolation_directory_residue",
    }:
        return False
    location_keys = {
        "path", "exists", "apparent_bytes", "allocated_bytes", "measurement_error",
    }
    for value in locations.values():
        if not isinstance(value, dict) or set(value) != location_keys:
            return False
        if not isinstance(value["path"], str) or not isinstance(value["exists"], bool):
            return False
        error = value["measurement_error"]
        if error is not None and (not isinstance(error, str) or not error):
            return False
        for key in ("apparent_bytes", "allocated_bytes"):
            if value[key] is not None and not _integer(value[key]):
                return False
    error = receipt["measurement_error"]
    if error is not None and (not isinstance(error, str) or not error):
        return False
    return ((receipt["apparent_bytes_after"] is None) == (error is not None)
            and (receipt["allocated_bytes_after"] is None) == (error is not None))


def _valid_receipt(
    receipt: Any, exit_code: int, item: dict[str, Any],
) -> bool:
    if not isinstance(receipt, dict):
        return False
    decision = receipt.get("decision")
    if decision in _PRE_ACTION_DECISIONS:
        return (exit_code == 2 and set(receipt) == {"decision", "reason"}
                and isinstance(receipt.get("reason"), str) and bool(receipt["reason"]))
    if decision in _REMOVAL_DECISIONS:
        expected = (
            "removed_worktree" if item["kind"] == "worktree" else "removed_cache"
        )
        return (decision == expected and receipt.get("path") == item["candidate"]
                and exit_code == 0
                and set(receipt) == _MEASUREMENT_KEYS | {"decision"}
                and _measurement_receipt(receipt))
    if decision in _ANOMALY_DECISIONS:
        return (receipt.get("path") == item["candidate"]
                and (decision != "isolation_anomaly" or item["kind"] == "cache")
                and exit_code == 3 and _anomaly_measurements(receipt)
                and isinstance(receipt.get("anomaly"), str)
                and bool(receipt["anomaly"]))
    return False


def _safe_terminal_row(
    row: Any, item: dict[str, Any], runner: Path, number: int,
) -> None:
    if not isinstance(row, dict) or set(row) != _ROW_KEYS:
        raise ValueError(f"journal row {number} has an invalid schema")
    for key in ("candidate", "containing", "kind", "allocated_bytes"):
        if row[key] != item[key] or type(row[key]) is not type(item[key]):
            raise ValueError(f"journal row {number} does not match the manifest")
    if row["argv"] != _command(runner, item):
        raise ValueError(f"journal row {number} has stale argv")
    started = _timestamp(row["started_at"])
    ended = _timestamp(row["ended_at"])
    if started is None or ended is None or ended < started or row["terminal"] is not True:
        raise ValueError(f"journal row {number} has invalid terminal timestamps")
    exit_code = row["exit_code"]
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        raise ValueError(f"journal row {number} has invalid exit code")
    if row["stdout"] is not None or not isinstance(row["stderr"], str):
        raise ValueError(f"journal row {number} has invalid captured output")
    if row["error"] is not None:
        raise ValueError(f"journal row {number} records an unsafe halted outcome")
    if not _valid_receipt(row["receipt"], exit_code, item):
        raise ValueError(f"journal row {number} has an exit/receipt mismatch")
    if exit_code not in (0, 2):
        raise ValueError(f"journal row {number} records an anomaly halt")


def _completed_candidates(
    path: Path, manifest: list[dict[str, Any]], runner: Path,
) -> set[str]:
    if not path.exists():
        return set()
    by_candidate = {item["candidate"]: item for item in manifest}
    completed: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        candidate = row.get("candidate") if isinstance(row, dict) else None
        if not isinstance(candidate, str) or candidate not in by_candidate:
            raise ValueError(f"journal row {number} is not in the current manifest")
        if candidate in completed:
            raise ValueError(f"journal has duplicate rows for {candidate}")
        _safe_terminal_row(row, by_candidate[candidate], runner, number)
        completed.add(candidate)
    return completed


def _append_durable(handle: Any, row: dict[str, Any]) -> None:
    handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    handle.flush()
    os.fsync(handle.fileno())


def _receipt(stdout: str) -> dict[str, Any] | None:
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) and isinstance(value.get("decision"), str) else None


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_group(
    process: subprocess.Popen[str], stdout: str | bytes | None, stderr: str | bytes | None,
) -> tuple[str, str, str]:
    process_group = process.pid
    for group_signal in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process_group, group_signal)
        except ProcessLookupError:
            pass
        if group_signal == signal.SIGTERM:
            time.sleep(0.1)
    try:
        tail_stdout, tail_stderr = process.communicate(timeout=2.0)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process_group, signal.SIGKILL)
        except ProcessLookupError:
            pass
        tail_stdout, tail_stderr = process.communicate()
    captured_stdout = _text(stdout) + _text(tail_stdout)
    captured_stderr = _text(stderr) + _text(tail_stderr)
    deadline = time.monotonic() + 2.0
    while _group_exists(process_group) and time.monotonic() < deadline:
        try:
            os.killpg(process_group, signal.SIGKILL)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    error = (
        "timeout_group_survival_unverified"
        if _group_exists(process_group) else "timeout_group_terminated"
    )
    return captured_stdout, captured_stderr, error


def _halt_reason(row: dict[str, Any]) -> str | None:
    exit_code = row["exit_code"]
    receipt = row["receipt"]
    if row["error"] is not None:
        return row["error"]
    if isinstance(exit_code, int) and exit_code < 0:
        return "runner_signal"
    if exit_code == 3:
        if (_valid_receipt(receipt, exit_code, row)
                and receipt.get("decision") in _ANOMALY_DECISIONS):
            return str(receipt["decision"])
        return "exit_3_unclassified"
    if not isinstance(exit_code, int) or not _valid_receipt(receipt, exit_code, row):
        return "unclassifiable_outcome"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument(
        "--runner", type=Path,
        default=Path(__file__).with_name("run_cleanup_candidate.sh"),
    )
    parser.add_argument("--max-candidates", type=int, default=100)
    parser.add_argument("--deadline-seconds", type=float, default=2400.0)
    parser.add_argument("--candidate-timeout-seconds", type=float, default=300.0)
    args = parser.parse_args(argv)
    if (args.max_candidates < 1 or args.deadline_seconds <= 0
            or args.candidate_timeout_seconds <= 0):
        parser.error("bounds must be positive")
    try:
        rows = _load_manifest(args.manifest)
        completed = _completed_candidates(args.journal, rows, args.runner)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"invalid input: {exc}", file=sys.stderr)
        return 2
    if not args.runner.is_file():
        print("runner is not a regular file", file=sys.stderr)
        return 2

    started = time.monotonic()
    invoked = 0
    stop_reason = "complete"
    exit_code = 0
    args.journal.parent.mkdir(parents=True, exist_ok=True)
    with args.journal.open("a", encoding="utf-8") as journal:
        for item in rows:
            if item["candidate"] in completed:
                continue
            if invoked >= args.max_candidates:
                stop_reason = "max_candidates"
                break
            remaining = args.deadline_seconds - (time.monotonic() - started)
            if remaining <= 0:
                stop_reason = "deadline"
                break
            command = _command(args.runner, item)
            row: dict[str, Any] = {
                **item, "argv": command, "started_at": _utc_now(), "terminal": True,
            }
            process: subprocess.Popen[str] | None = None
            try:
                process = subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, start_new_session=True,
                )
                try:
                    stdout, stderr = process.communicate(
                        timeout=args.candidate_timeout_seconds,
                    )
                    error = None
                except subprocess.TimeoutExpired as exc:
                    stdout, stderr, error = _terminate_group(process, exc.stdout, exc.stderr)
                receipt = _receipt(stdout)
                row.update({
                    "ended_at": _utc_now(), "exit_code": process.returncode,
                    "receipt": receipt, "stdout": stdout if receipt is None else None,
                    "stderr": stderr, "error": error,
                })
            except Exception as exc:
                if process is not None and process.poll() is None:
                    try:
                        stdout, stderr, termination = _terminate_group(process, "", "")
                    except Exception:
                        stdout, stderr, termination = "", "", "group_survival_unverified"
                else:
                    stdout, stderr, termination = "", "", "not_started_or_reaped"
                row.update({
                    "ended_at": _utc_now(),
                    "exit_code": process.returncode if process is not None else None,
                    "receipt": None, "stdout": stdout, "stderr": stderr,
                    "error": f"runner_error:{type(exc).__name__}:{termination}",
                })
            _append_durable(journal, row)
            invoked += 1
            halt = _halt_reason(row)
            if halt is not None:
                stop_reason = halt
                exit_code = 3
                break

    print(json.dumps({
        "invoked": invoked,
        "remaining": sum(row["candidate"] not in completed for row in rows) - invoked,
        "stop_reason": stop_reason,
    }, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

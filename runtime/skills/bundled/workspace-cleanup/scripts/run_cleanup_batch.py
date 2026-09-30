#!/usr/bin/env python3
"""Durable bounded batch driver for the bundled workspace cleanup runner."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _load_manifest(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        value = [json.loads(line) for line in text.splitlines() if line.strip()]
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


def _completed_candidates(path: Path) -> set[str]:
    if not path.exists():
        return set()
    completed: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"journal row {number} is not an object")
        if row.get("terminal") is True and isinstance(row.get("candidate"), str):
            completed.add(row["candidate"])
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
        completed = _completed_candidates(args.journal)
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
            command = [
                "bash", str(args.runner), item["candidate"], item["containing"],
            ]
            row: dict[str, Any] = {
                **item,
                "argv": command,
                "started_at": _utc_now(),
                "terminal": True,
            }
            try:
                process = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=min(args.candidate_timeout_seconds, remaining),
                    check=False,
                )
                row.update({
                    "ended_at": _utc_now(),
                    "exit_code": process.returncode,
                    "receipt": _receipt(process.stdout),
                    "stdout": process.stdout if _receipt(process.stdout) is None else None,
                    "stderr": process.stderr,
                    "error": None,
                })
            except subprocess.TimeoutExpired as exc:
                row.update({
                    "ended_at": _utc_now(),
                    "exit_code": None,
                    "receipt": None,
                    "stdout": exc.stdout or "",
                    "stderr": exc.stderr or "",
                    "error": "timeout",
                })
            except OSError as exc:
                row.update({
                    "ended_at": _utc_now(),
                    "exit_code": None,
                    "receipt": None,
                    "stdout": "",
                    "stderr": "",
                    "error": f"runner_error:{type(exc).__name__}",
                })
            _append_durable(journal, row)
            invoked += 1
            receipt = row["receipt"]
            if isinstance(receipt, dict) and receipt.get("decision") == "removed_with_anomaly":
                stop_reason = "removed_with_anomaly"
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

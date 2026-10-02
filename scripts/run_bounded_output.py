#!/usr/bin/env python3
"""Run a command while retaining a fixed-size tail of its merged output."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


DEFAULT_MAX_BYTES = 1024 * 1024
TRUNCATION_MARKER = b"[nightly log truncated; showing final output bytes]\n"


def run(command: list[str], *, output: Path, max_bytes: int) -> int:
    """Stream ``command`` output, write its bounded tail, and return its status."""
    if max_bytes <= len(TRUNCATION_MARKER):
        raise ValueError(
            f"max_bytes must exceed the {len(TRUNCATION_MARKER)}-byte marker"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert process.stdout is not None
    tail = bytearray()
    total_bytes = 0
    for chunk in iter(lambda: process.stdout.read1(64 * 1024), b""):
        sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
        total_bytes += len(chunk)
        tail.extend(chunk)
        if len(tail) > max_bytes:
            del tail[:-max_bytes]

    status = process.wait()
    if total_bytes > max_bytes:
        tail_bytes = max_bytes - len(TRUNCATION_MARKER)
        artifact = TRUNCATION_MARKER + bytes(tail[-tail_bytes:])
    else:
        artifact = bytes(tail)
    output.write_bytes(artifact)
    return status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("a command is required after --")
    return run(command, output=args.output, max_bytes=args.max_bytes)


if __name__ == "__main__":
    raise SystemExit(main())

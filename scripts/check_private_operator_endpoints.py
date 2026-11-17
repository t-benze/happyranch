"""Digest-only lint for private operator endpoints in tracked working-tree text."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import sys
import time


# Lowercase token SHA-256 only; never put private endpoint values here.
FORBIDDEN_SHA256 = frozenset({
    "6ca9248e89fc80595928c0c177f73faa3f7302dffeaf05c6e94e9235495bc693",
    "6fa999e92bbec8c567275d8aee5fb7f87cc52a959f1f933686c8e035f49c88bf",
    "91dce6f465ec383f565387ff81104495a38139ed24825d248b38df7184c370a7",
    "c07d3a8d201f7c609bacc3438f2b6e387f4b4eefa3eec0ba57e97bdbad6aed6c",
})
# Consume separator-free runs too, so a missing separator never makes the
# unanchored search rescan the same alphanumeric suffix from every position.
TOKEN = re.compile(rb"[a-z0-9]+(?:[.-][a-z0-9]+)*", re.IGNORECASE)
IPV4 = re.compile(
    rb"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])"
    rb"|(?<![0-9])(?:[0-9]{1,3}-){3}[0-9]{1,3}(?![0-9])"
)
MAX_FILES = 20_000
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_LIST_BYTES = 2 * 1024 * 1024
SCAN_SECONDS = 30


def forbidden(token: bytes) -> bool:
    return hashlib.sha256(token.lower()).hexdigest() in FORBIDDEN_SHA256


def private_endpoint(line: bytes) -> bool:
    return any(forbidden(match[0]) for pattern in (TOKEN, IPV4)
               for match in pattern.finditer(line))


def display_path(name: bytes) -> str:
    # A diagnostic's filename must not re-expose an endpoint either. Check
    # dotted labels as well, e.g. an instance-name followed by a file extension.
    if private_endpoint(name) or any(forbidden(label) for part in name.split(b"/")
                                     for label in part.split(b".")):
        return "<redacted-path>"
    return name.decode("utf-8", errors="backslashreplace")


def check(root: Path) -> int:
    deadline = time.monotonic() + SCAN_SECONDS
    listed = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=root, stderr=subprocess.DEVNULL, timeout=10,
    )
    names = listed.rstrip(b"\0").split(b"\0") if listed else []
    if len(listed) > MAX_LIST_BYTES or len(names) > MAX_FILES:
        raise ValueError("tracked file list exceeds scan limit")
    total = 0
    rejected = False
    for name in names:
        if time.monotonic() > deadline:
            raise ValueError("scan time limit exceeded")
        path = root / name.decode("utf-8", errors="surrogateescape")
        # Link targets and submodule contents are outside tracked text coverage.
        if path.is_symlink() or path.is_dir():
            continue
        with path.open("rb") as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
        total += len(raw)
        if len(raw) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise ValueError("tracked content exceeds scan limit")
        if b"\0" in raw:
            continue
        for number, line in enumerate(raw.split(b"\n"), 1):
            if time.monotonic() > deadline:
                raise ValueError("scan time limit exceeded")
            if private_endpoint(line):
                # Do not include matched content or exception details.
                print(f"{display_path(name)}:{number}: private operator endpoint",
                      file=sys.stderr)
                rejected = True
    if time.monotonic() > deadline:
        raise ValueError("scan time limit exceeded")
    return 1 if rejected else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        result = check(args.root.resolve())
    except (OSError, ValueError, subprocess.SubprocessError):
        print("Private operator endpoint lint: scan incomplete", file=sys.stderr)
        return 2
    if result == 0:
        print("Private operator endpoint lint PASS")
    return result


if __name__ == "__main__":
    raise SystemExit(main())

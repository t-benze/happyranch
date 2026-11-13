"""Read-only packaging prerequisites; no build, install, daemon or app launch."""

from __future__ import annotations

import os
import re
import selectors
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

TOOLS = {
    "uv": "--version", "node": "--version", "npm": "--version",
    "go": "version", "swift": "--version", "xcodebuild": "-version",
    "python3": "--version",
}
OUTPUT_CAP = 1024


def command(argv: list[str], deadline: float) -> dict[str, Any]:
    """Drain bounded output and always kill/reap the owned command group."""
    data = bytearray()
    result: dict[str, Any] = {"argv": argv}
    child = None
    end = min(deadline, time.monotonic() + 5)
    try:
        if time.monotonic() >= end:
            raise TimeoutError("read-only command deadline")
        child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, start_new_session=True)
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = end - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("read-only command deadline")
                for key, _ in selector.select(min(.1, remaining)):
                    block = os.read(key.fileobj.fileno(), 4096)
                    if not block:
                        selector.unregister(key.fileobj)
                    else:
                        overflow = len(data) + len(block) > OUTPUT_CAP
                        data.extend(block[:OUTPUT_CAP - len(data)])
                        if overflow:
                            # Any output beyond the retained cap refuses the command.
                            raise RuntimeError("read-only command output cap")
        result["exit_code"] = child.wait(timeout=max(.001, end - time.monotonic()))
    except (OSError, RuntimeError, TimeoutError, subprocess.TimeoutExpired) as exc:
        result.update(exit_code=None, error=f"{type(exc).__name__}: {exc}")
    finally:
        if child is not None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=2)
            if child.stdout is not None:
                child.stdout.close()
        result["output"] = data.decode("utf-8", errors="replace").strip()
    return result


def accepted(name: str, row: dict[str, Any]) -> bool:
    """Classify actual command evidence; missing/failed/unparseable refuses."""
    required = ("lookup", "version", "file") if name == "npm" else ("lookup", "version", "file", "arch")
    if not row.get("path") or any(
        row.get(key, {}).get("exit_code") != 0 or not row.get(key, {}).get("output")
        for key in required
    ):
        return False
    version = row["version"]["output"]
    if name != "npm" and not {"arm64", "arm64e"}.intersection(row["arch"]["output"].split()):
        return False
    patterns = {
        "uv": r"uv \d+\.\d+\.\d+\b",
        "node": r"v24\.\d+\.\d+\b",
        "npm": r"\d+\.\d+\.\d+\b",
        "go": r"go version go\d+\.\d+(?:\.\d+)? darwin/arm64\b",
        "swift": r"Swift version (?:[6-9]|[1-9]\d+)\.",
        "xcodebuild": r"Xcode (?:1[6-9]|[2-9]\d+)\.",
        "python3": r"Python 3\.14\.\d+\b",
    }
    return re.search(patterns[name], version) is not None


def run(report: dict[str, Any]) -> None:
    deadline = time.monotonic() + 90
    section: dict[str, Any] = {"tools": {}, "build_ran": False}
    report["toolchain"] = section
    scratch = Path(os.environ.get("TMPDIR", ""))
    if not scratch.is_absolute() or not scratch.is_dir():
        raise RuntimeError("toolchain requires the job scratch TMPDIR")
    # statvfs reads free space without creating any fixture or cache.
    space = os.statvfs(scratch)
    section["scratch"] = {"path": str(scratch), "free_bytes": space.f_bavail * space.f_frsize}
    for name, flag in TOOLS.items():
        lookup = command(["/bin/bash", "-p", "-c", 'command -v "$1"', "toolchain", name], deadline)
        path = lookup["output"] if lookup["exit_code"] == 0 else None
        row: dict[str, Any] = {"path": path, "lookup": lookup}
        section["tools"][name] = row
        if path and Path(path).is_absolute():
            row["version"] = command([path, flag], deadline)
            row["file"] = command(["/usr/bin/file", "-L", path], deadline)
            if name != "npm":
                row["arch"] = command(["/usr/bin/lipo", "-archs", path], deadline)
        row["status"] = "PASS" if accepted(name, row) else "UNAVAILABLE"
    section["ready"] = all(row["status"] == "PASS" for row in section["tools"].values())
    if not section["ready"]:
        missing = [name for name, row in section["tools"].items() if row["status"] != "PASS"]
        raise RuntimeError("packaging toolchain unavailable: " + ", ".join(missing))

"""Pinned native probe tools, installed only under the current job's TMPDIR."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tarfile
import time
from pathlib import Path
from typing import Any

PINS = Path(__file__).with_name("toolchain-pins.json")
HOST_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
DOWNLOAD_CAP = 128 * 1024 * 1024
FILE_CAP = 256 * 1024 * 1024
TOTAL_CAP = 768 * 1024 * 1024


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate pin key")
        result[key] = value
    return result


def parse_pins(raw: str) -> dict[str, dict[str, str]]:
    """Accept only exact versions, official artifact paths and SHA256 pins."""
    pins = json.loads(raw, object_pairs_hook=_unique)
    if not isinstance(pins, dict) or set(pins) != {"uv", "node", "go"}:
        raise ValueError("pin table must contain uv, node and go only")
    for name, row in pins.items():
        if not isinstance(row, dict) or set(row) != {"version", "url", "sha256", "checksum_source"}:
            raise ValueError("invalid pin fields")
        if any(not isinstance(value, str) for value in row.values()):
            raise ValueError("pin values must be strings")
        version = row["version"]
        if (not re.fullmatch(r"\d+\.\d+\.\d+", version)
                or (name == "node" and not version.startswith("24."))
                or (name == "go" and version != "1.23.12")):
            raise ValueError("exact uv / Node24 / Go1.23.12 version required")
        if not re.fullmatch(r"[a-f0-9]{64}", row["sha256"]):
            raise ValueError("SHA256 pin required")
        if name == "uv":
            url = f"https://github.com/astral-sh/uv/releases/download/{version}/uv-aarch64-apple-darwin.tar.gz"
            checksum = url + ".sha256"
        elif name == "node":
            url = f"https://nodejs.org/dist/v{version}/node-v{version}-darwin-arm64.tar.gz"
            checksum = f"https://nodejs.org/dist/v{version}/SHASUMS256.txt"
        else:
            url = f"https://go.dev/dl/go{version}.darwin-arm64.tar.gz"
            checksum = "https://go.dev/dl/?mode=json&include=all"
        if row["url"] != url or row["checksum_source"] != checksum:
            raise ValueError("official versioned artifact/checksum URL required")
    return pins


def remaining(deadline: float) -> float:
    value = deadline - time.monotonic()
    if value <= 0:
        raise TimeoutError("toolchain provisioning deadline")
    return value


def verify_checksum(archive: Path, expected: str, deadline: float) -> None:
    digest = hashlib.sha256()
    with archive.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            remaining(deadline)
            digest.update(block)
    if digest.hexdigest() != expected:
        raise ValueError("toolchain SHA256 mismatch; extraction refused")


def environment(scratch: Path, pins: dict[str, dict[str, str]], inherited: dict[str, str]) -> dict[str, str]:
    """Only the three private bin directories are added to the fixed host PATH."""
    if not scratch.is_absolute() or scratch.resolve() != scratch:
        raise ValueError("literal absolute scratch required")
    uv_bin = scratch / "uv-aarch64-apple-darwin"
    node_bin = scratch / f"node-v{pins['node']['version']}-darwin-arm64" / "bin"
    env = dict(inherited)
    env.update(PATH=f"{uv_bin}:{node_bin}:{scratch / 'go/bin'}:{HOST_PATH}",
               UV_CACHE_DIR=str(scratch / "uv-cache"),
               npm_config_cache=str(scratch / "npm-cache"),
               GOROOT=str(scratch / "go"), GOPATH=str(scratch / "go-path"),
               GOTOOLCHAIN="local", GOCACHE=str(scratch / "go-cache"),
               GOMODCACHE=str(scratch / "go-modules"))
    # build_daemon.sh owns UV_PYTHON_INSTALL_DIR under freeze-work and refuses
    # inherited selectors; never pass that variable to the packaging wrapper.
    return env


def provision(scratch: Path, pins: dict[str, dict[str, str]], deadline: float) -> dict[str, str]:
    env = environment(scratch, pins, dict(os.environ))
    scratch.mkdir(mode=0o700)  # fresh only; never reuse a partially installed tree
    total = 0
    members = 0
    for name, row in pins.items():
        archive = scratch / f"{name}.tar.gz"
        seconds = min(90, remaining(deadline))
        subprocess.run(["/usr/bin/curl", "-q", "--fail", "--silent", "--show-error",
                        "--location", "--max-redirs", "3", "--proto", "=https",
                        "--proto-redir", "=https", "--connect-timeout", "10",
                        "--max-time", str(seconds), "--max-filesize", str(DOWNLOAD_CAP),
                        "--output", str(archive), row["url"]],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=True, timeout=remaining(deadline))
        if not 0 < archive.stat().st_size <= DOWNLOAD_CAP:
            raise ValueError("toolchain download size cap")
        # This must precede opening the tar or extracting any bytes.
        verify_checksum(archive, row["sha256"], deadline)
        root = ("uv-aarch64-apple-darwin" if name == "uv" else
                "go" if name == "go" else f"node-v{row['version']}-darwin-arm64")
        with tarfile.open(archive, "r:gz") as bundle:
            for member in bundle:
                remaining(deadline)
                members += 1
                total += member.size
                if (members > 20000 or total > TOTAL_CAP or member.size > FILE_CAP
                        or member.size < 0 or member.name.split("/")[0] != root):
                    raise ValueError("toolchain archive bounds/root")
                bundle.extract(member, scratch, filter="data")
        archive.unlink()
    for name in ("uv", "node", "npm", "go"):
        binary = (scratch / "uv-aarch64-apple-darwin" / name if name == "uv" else
                  scratch / "go/bin/go" if name == "go" else
                  scratch / f"node-v{pins['node']['version']}-darwin-arm64" / "bin" / name)
        if not binary.resolve().is_relative_to(scratch) or not os.access(binary, os.X_OK):
            raise ValueError("missing/escaping provisioned executable")
    remaining(deadline)
    return env

"""Pinned native probe tools, installed only under the current job's TMPDIR."""

from __future__ import annotations

import hashlib
import json
import os
import re
import ipaddress
import selectors
import signal
import subprocess
import tarfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

PINS = Path(__file__).with_name("toolchain-pins.json")
HOST_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
DOWNLOAD_CAP = 128 * 1024 * 1024
FILE_CAP = 256 * 1024 * 1024
TOTAL_CAP = 768 * 1024 * 1024
ARCHIVE_MEMBER_CAP = 20000
MEMBER_CAP = 30000
STDERR_CAP = 2048
METRICS_CAP = 8192
METRICS = ("http_code", "url_effective", "num_redirects", "remote_ip",
           "time_namelookup", "time_connect", "time_appconnect",
           "time_starttransfer", "time_total", "size_download")


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate pin key")
        result[key] = value
    return result


def parse_pins(raw: str) -> dict[str, dict[str, Any]]:
    """Accept only exact versions, official artifact paths and SHA256 pins."""
    pins = json.loads(raw, object_pairs_hook=_unique)
    if not isinstance(pins, dict) or set(pins) != {"uv", "node", "go"}:
        raise ValueError("pin table must contain uv, node and go only")
    for name, row in pins.items():
        if not isinstance(row, dict) or set(row) != {"version", "origins", "official_sources", "sha256", "checksum_source"}:
            raise ValueError("invalid pin fields")
        if any(not isinstance(row[key], str) for key in ("version", "sha256", "checksum_source")):
            raise ValueError("pin scalar values must be strings")
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
            urls, sources = [url], [f"https://github.com/astral-sh/uv/releases/tag/{version}"]
        elif name == "node":
            url = f"https://nodejs.org/dist/v{version}/node-v{version}-darwin-arm64.tar.gz"
            checksum = f"https://nodejs.org/dist/v{version}/SHASUMS256.txt"
            urls, sources = [url], [f"https://nodejs.org/dist/v{version}/"]
        else:
            url = f"https://go.dev/dl/go{version}.darwin-arm64.tar.gz"
            checksum = "https://go.dev/dl/?mode=json&include=all"
            urls = [url, f"https://dl.google.com/go/go{version}.darwin-arm64.tar.gz",
                    f"https://golang.google.cn/dl/go{version}.darwin-arm64.tar.gz"]
            sources = ["https://go.dev/dl/",
                       "https://github.com/golang/website/blob/master/internal/dl/server.go",
                       "https://github.com/golang/website/blob/master/cmd/golangorg/server.go"]
        if (row["origins"] != urls or row["official_sources"] != sources
                or row["checksum_source"] != checksum):
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


def environment(scratch: Path, pins: dict[str, dict[str, Any]], inherited: dict[str, str]) -> dict[str, str]:
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


def safe_url(value: str) -> str:
    """Never publish userinfo, queries (including signed release tokens), or fragments."""
    try:
        url = urlsplit(value)
        if url.scheme == "https" and url.hostname and re.fullmatch(r"[a-zA-Z0-9.-]+", url.hostname):
            # The hostname diagnoses the redirect destination; arbitrary path segments
            # can themselves contain credentials, so retain only our known public paths.
            path = url.path if re.fullmatch(r"/(?:dl/|go/)?go[0-9.]+\.darwin-arm64\.tar\.gz", url.path) else "/[path omitted]"
            return ("https://" + url.hostname + path)[:512]
    except ValueError:
        pass
    return "[unavailable]"


def stderr_tail(raw: bytes) -> str:
    """Retain curl error lines only; omit URLs, quoted values and non-curl output."""
    lines = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not re.match(r"^curl: \([0-9]+\) ", line):
            continue
        line = re.sub(r"https?://\S+", "[url omitted]", line)
        line = re.sub(r'''["'][^"']*["']''', "[quoted value omitted]", line)
        # Error messages can embed remote/proxy supplied text. Only safe, useful
        # network diagnostics are retained; unknown messages preserve the code only.
        if not re.fullmatch(r"curl: \([0-9]+\) (?:Operation timed out after [0-9]+ milliseconds with [0-9]+ (?:out of [0-9]+ )?bytes received|Connection timed out after [0-9]+ milliseconds|SSL connection timeout|Resolving timed out after [0-9]+ milliseconds|Could not resolve host: [a-zA-Z0-9.-]+|Failed to connect to [a-zA-Z0-9.:-]+ port [0-9]+(?: after [0-9]+ ms)?: (?:Couldn't connect to server|Connection refused)|The requested URL returned error: [0-9]+)", line):
            line = re.match(r"curl: \([0-9]+\)", line)[0] + " [detail omitted]"
        lines.append(line)
    return "\n".join(lines)[-STDERR_CAP:]


def curl_attempt(url: str, archive: Path, seconds: float, deadline: float) -> dict[str, Any]:
    """Stream bounded diagnostics without retaining headers, cookies or curl config."""
    argv = ["/usr/bin/curl", "-q", "--fail", "--silent", "--show-error",
            "--location", "--max-redirs", "3", "--proto", "=https",
            "--proto-redir", "=https", "--noproxy", "*", "--proxy", "",
            "--connect-timeout", "10", "--max-time", str(seconds),
            "--max-filesize", str(DOWNLOAD_CAP), "--output", str(archive),
            "--write-out", "".join(f"%{{{key}}}\n" for key in METRICS), url]
    row: dict[str, Any] = {"url": url, "curl_exit_code": None, "max_time_seconds": seconds}
    deadline = min(deadline, time.monotonic() + seconds + 1)
    output, errors = bytearray(), bytearray()
    child = None
    try:
        child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, start_new_session=True)
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ, "metrics")
            selector.register(child.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                wait = remaining(deadline)
                for key, _ in selector.select(min(.1, wait)):
                    block = os.read(key.fileobj.fileno(), 4096)
                    if not block:
                        selector.unregister(key.fileobj)
                    elif key.data == "metrics":
                        output.extend(block[:max(0, METRICS_CAP - len(output))])
                    else:
                        errors.extend(block)
                        del errors[:-STDERR_CAP]
        row["curl_exit_code"] = child.wait(timeout=remaining(deadline))
    except (OSError, TimeoutError, subprocess.TimeoutExpired) as exc:
        row["error"] = type(exc).__name__  # exception text may contain external data
    finally:
        if child is not None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=2)
            row["curl_exit_code"] = child.returncode
            child.stdout.close()
            child.stderr.close()
        row["stderr_tail"] = stderr_tail(bytes(errors))
    values = output.decode("ascii", errors="replace").splitlines()
    metrics: dict[str, Any] = dict.fromkeys(METRICS)
    if len(values) == len(METRICS):
        for key, value in zip(METRICS, values):
            if key == "url_effective":
                metrics[key] = safe_url(value)
            elif key == "remote_ip":
                try:
                    metrics[key] = str(ipaddress.ip_address(value))
                except ValueError:
                    pass
            elif re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,9})?", value):
                metrics[key] = value
    row["metrics"] = metrics
    return row


def provision(scratch: Path, pins: dict[str, dict[str, Any]], deadline: float,
              attempts: list[dict[str, Any]] | None = None) -> dict[str, str]:
    env = environment(scratch, pins, dict(os.environ))
    scratch.mkdir(mode=0o700)  # fresh only; never reuse a partially installed tree
    total = 0
    members = 0
    for name, row in pins.items():
        archive = scratch / f"{name}.tar.gz"
        download_deadline = min(deadline, time.monotonic() + 90)
        for url in row["origins"]:
            archive.unlink(missing_ok=True)  # every origin starts with zero bytes
            seconds = min(90 / len(row["origins"]), remaining(download_deadline))
            attempt = curl_attempt(url, archive, seconds, download_deadline)
            attempt.update(artifact=name, sha256_verified=False)
            if attempts is not None:
                attempts.append(attempt)  # survives later failure in the probe JSON
            if attempt["curl_exit_code"] != 0 or attempt.get("error"):
                continue
            if not 0 < archive.stat().st_size <= DOWNLOAD_CAP:
                raise ValueError("toolchain download size cap")
            # Each successful transport must pass the SAME pin before tar open.
            # A mismatch stops; no fallback can hide unexpected artifact contents.
            verify_checksum(archive, row["sha256"], deadline)
            attempt["sha256_verified"] = True
            break
        else:
            raise RuntimeError(f"{name}: all official origins failed; see provisioning attempts")
        root = ("uv-aarch64-apple-darwin" if name == "uv" else
                "go" if name == "go" else f"node-v{row['version']}-darwin-arm64")
        archive_members = 0
        with tarfile.open(archive, "r:gz") as bundle:
            for member in bundle:
                remaining(deadline)
                members += 1
                archive_members += 1
                total += member.size
                if (members > MEMBER_CAP or archive_members > ARCHIVE_MEMBER_CAP
                        or total > TOTAL_CAP or member.size > FILE_CAP
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

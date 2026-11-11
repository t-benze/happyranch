"""S6: discover the Mac toolchain, then run scratch-contained Swift tests."""

from __future__ import annotations

import hashlib
import json
import os
import re
import resource
import selectors
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FILE_CAP = 32 * 1024 * 1024
OUTPUT_CAP = 1024 * 1024
CASES = (
    ("term_outcome", "TERM outcome is observed without KILL", "status 0/nil or 15/15; no KILL"),
    ("uncaught_signal", "both launch callbacks preserve actual uncaught signal", "dev and bundled: signal 15"),
    ("failed_kill", "failed KILL retains ownership, honest diagnostics and Start disabled", "live, stopping, no fabricated exit, no restart"),
    ("kill_exit", "scripted Stop escalates a disposable TERM-ignoring child", "real child exited with SIGKILL 9; Stop log status=9 signal=9"),
    ("health_continues", "healthy monitoring survives startup budget, reports failure, and recovers", "5s polling beyond 30 rounds; unhealthy/recovery transitions"),
    ("health_bounds", "startup remains bounded and inactive states end monitoring", "startup <=30 attempts; terminal/stopping states end polling"),
)


def _command(
    argv: list[str], env: dict[str, str], cwd: Path, deadline: float,
    *, output_cap: int = OUTPUT_CAP,
) -> tuple[int, bytes]:
    """Drain incrementally; never leave a timed-out compiler/test group running."""
    data = bytearray()
    try:
        child = subprocess.Popen(
            argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
        )
    except OSError as exc:
        return 127, str(exc).encode()
    with child:
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("supervisor suite command deadline")
                    for key, _ in selector.select(min(0.1, remaining)):
                        block = os.read(key.fileobj.fileno(), 4096)
                        if not block:
                            selector.unregister(key.fileobj)
                        elif len(data) + len(block) > output_cap:
                            raise RuntimeError("supervisor command output cap exceeded")
                        else:
                            data.extend(block)
            code = child.wait(timeout=max(0.001, deadline - time.monotonic()))
            return code, bytes(data)
        finally:
            # The group leader can exit while a compiler/child still owns pipes.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=3)


def supervisor(report: dict[str, Any]) -> None:
    """No installs, Jenkins operations, GUI launch, or daemon startup."""
    checks: list[dict[str, Any]] = []
    section: dict[str, Any] = {"checks": checks}
    report["supervisor"] = section
    # Leaves time for the existing <=300s bootstrap and parent cleanup within
    # the job's original 1800s limit. This never resets between commands.
    deadline = time.monotonic() + 1200

    def row(name: str, status: str, expected: Any, actual: Any) -> None:
        checks.append(dict(name=name, status=status, expected=expected, actual=actual))
        # Reserve space for indentation and the dispatcher's failure envelope,
        # including when compiler output contains heavily escaped characters.
        if len(json.dumps(report).encode()) > 46000:
            checks.pop()
            checks.append(dict(name=name, status="FAIL", expected="report <=48KiB",
                               actual="row exceeded remaining report budget"))
            raise RuntimeError("supervisor report budget exceeded")

    # The job supplies TMPDIR inside build scratch; require it explicitly rather
    # than silently falling back to the real user's temporary/cache directories.
    tmp = os.environ.get("TMPDIR")
    if not tmp or not Path(tmp).is_absolute() or not Path(tmp).is_dir():
        raise RuntimeError("supervisor suite requires the build scratch TMPDIR")
    with tempfile.TemporaryDirectory(prefix="supervisor-", dir=tmp) as folder:
        scratch = Path(folder).resolve(strict=True)
        env = dict(os.environ)
        for key, relative in {
            "HOME": "home", "CFFIXED_USER_HOME": "home", "TMPDIR": "tmp",
            "XDG_CACHE_HOME": "cache", "CLANG_MODULE_CACHE_PATH": "clang-modules",
            "SWIFT_MODULECACHE_PATH": "swift-modules",
            "SWIFTPM_MODULECACHE_OVERRIDE": "swift-modules",
            "SDK_STAT_CACHE_DIR": "sdk-cache",
        }.items():
            path = scratch / relative
            path.mkdir(exist_ok=True)
            env[key] = str(path)
        env["NO_COLOR"] = "1"
        env["HAPPYRANCH_DAEMON_HOME"] = str(scratch / "daemon-home")

        # FIRST rows: actual, read-only toolchain observations. Always attempt
        # all four, but never build/test if any prerequisite is unavailable.
        discovered: dict[str, str] = {}
        for name, argv in (
            ("developer_directory", ["/usr/bin/xcode-select", "-p"]),
            ("macos_sdk", ["/usr/bin/xcrun", "--show-sdk-path"]),
            ("swift_path", ["/usr/bin/xcrun", "--find", "swift"]),
            ("swift_version", ["swift", "--version"]),
        ):
            if name == "swift_version":
                argv = [discovered.get("swift_path", "swift"), "--version"]
            try:
                code, output = _command(argv, env, scratch, min(deadline, time.monotonic() + 15), output_cap=8192)
            except (RuntimeError, TimeoutError, subprocess.TimeoutExpired) as exc:
                code, output = 1, f"{type(exc).__name__}: {exc}".encode()
            value = output.decode("utf-8", errors="replace").strip()
            available = code == 0 and bool(value)
            expected = "AVAILABLE: existing developer directory/SDK/compiler"
            if name == "swift_version":
                version = re.search(r"Swift version (\d+)\.", value)
                available = available and version is not None and int(version[1]) >= 6
                expected = "AVAILABLE: Swift >=6"
            else:
                available = available and Path(value).is_absolute() and Path(value).exists()
            row(name, "AVAILABLE" if available else "UNAVAILABLE", expected,
                {"command": argv, "exit_code": code, "value": value[:2048]})
            if available:
                discovered[name] = value
        if any(item["status"] == "UNAVAILABLE" for item in checks):
            section["result"] = "toolchain unavailable; no build or tests ran"
            raise RuntimeError("toolchain unavailable")

        soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
        bounded_soft = FILE_CAP if soft == resource.RLIM_INFINITY else min(soft, FILE_CAP)
        resource.setrlimit(resource.RLIMIT_FSIZE, (bounded_soft, hard))
        actual_soft, actual_hard = resource.getrlimit(resource.RLIMIT_FSIZE)
        cap_ok = actual_soft != resource.RLIM_INFINITY and actual_soft <= FILE_CAP
        row("file_cap", "PASS" if cap_ok else "FAIL", "at most 33554432 bytes per file",
            {"soft_bytes": actual_soft, "hard_bytes": actual_hard})
        if not cap_ok:
            raise RuntimeError("native file cap unavailable; no build ran")
        swift = discovered["swift_path"]
        common = [
            swift, "test", "--package-path", str(ROOT / "app/mac"),
            "--scratch-path", str(scratch / "build"),
            "--cache-path", str(scratch / "swiftpm-cache"),
            "--config-path", str(scratch / "swiftpm-config"),
            "--security-path", str(scratch / "swiftpm-security"),
            # Release with no debug info avoids unnecessary large debug products;
            # the existing file limit still refuses any oversized native product.
            "-c", "release", "-j", "2", "-Xswiftc", "-gnone",
            "-Xswiftc", "-module-cache-path", "-Xswiftc", env["SWIFT_MODULECACHE_PATH"],
            "-Xcc", "-fmodules-cache-path=" + env["CLANG_MODULE_CACHE_PATH"],
        ]
        for name, extra in (
            ("swift_test", ["--filter", "AppDelegateStopHonestyTests|ManagedHealthPollingTests"]),
            ("scripted_stop_escalation", ["--skip-build", "--filter",
                                         "AppDelegateStopHonestyTests/stopEscalatesRealChild"]),
        ):
            argv = common + extra
            try:
                code, output = _command(argv, env, scratch, deadline)
            except (RuntimeError, TimeoutError, subprocess.TimeoutExpired) as exc:
                row(name, "FAIL", "exit 0 within original time/output/file caps",
                    {"error": f"{type(exc).__name__}: {exc}"[:1024]})
                section["result"] = "native command unavailable/incomplete; no further tests ran"
                raise RuntimeError(f"{name} failed: {type(exc).__name__}") from exc
            raw_output = output
            output = raw_output.decode("utf-8", errors="replace")
            passed = code == 0
            if name == "swift_test":
                passed = passed and "test run with" in output.lower() and "passed" in output
            if name == "scripted_stop_escalation":
                passed = passed and any(CASES[3][1] in line and "passed" in line
                                        for line in output.splitlines())
            row(name, "PASS" if passed else "FAIL", "exit 0; selected tests executed",
                {"command": argv, "exit_code": code, "output_bytes": len(raw_output),
                 "sha256": hashlib.sha256(raw_output).hexdigest(), "tail": output[-4096:]})
            if not passed:
                section["result"] = "native verification failed; inspect actual output/cap"
                raise RuntimeError(f"{name} failed; no further test commands ran")
            if name == "swift_test":
                for case, label, expected in CASES:
                    observed = next((line for line in output.splitlines()
                                     if label in line and "passed" in line), None)
                    row(case, "PASS" if observed else "FAIL", expected, observed)
                if any(item["status"] == "FAIL" for item in checks):
                    raise RuntimeError("expected S6 tests did not execute")
        section["result"] = "native source tests passed; frozen app acceptance NOT RUN"
    removed = not scratch.exists()
    row("scratch_removed", "PASS" if removed else "FAIL", True, removed)
    if not removed:
        raise RuntimeError("supervisor scratch cleanup incomplete")
    if len(json.dumps(report).encode()) > 48000:
        raise RuntimeError("supervisor report exceeds reserved 48KiB budget")

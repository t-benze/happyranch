"""S6: discover the Mac toolchain, then run scratch-contained Swift tests."""

from __future__ import annotations

import hashlib
import json
import os
import pwd
import re
import resource
import secrets
import selectors
import signal
import subprocess
import tempfile
import time
from contextlib import ExitStack
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
    *, output_cap: int = OUTPUT_CAP, input_data: bytes | None = None,
    stderr_data: bytearray | None = None,
) -> tuple[int, bytes]:
    """Drain incrementally; never leave a timed-out compiler/test group running."""
    data = bytearray()
    if input_data is not None and len(input_data) > 4096:
        raise RuntimeError("supervisor command input cap exceeded")
    try:
        child = subprocess.Popen(
            argv, cwd=cwd, env=env,
            stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE if stderr_data is not None else subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        return 127, str(exc).encode()
    with child:
        try:
            if input_data is not None:
                try:
                    child.stdin.write(input_data)
                    child.stdin.close()
                except BrokenPipeError:
                    pass  # Drain diagnostics and preserve the child's exit code.
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ, data)
                if stderr_data is not None:
                    selector.register(child.stderr, selectors.EVENT_READ, stderr_data)
                received = 0
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("supervisor suite command deadline")
                    for key, _ in selector.select(min(0.1, remaining)):
                        block = os.read(key.fileobj.fileno(), 4096)
                        if not block:
                            selector.unregister(key.fileobj)
                        elif received + len(block) > output_cap:
                            raise RuntimeError("supervisor command output cap exceeded")
                        else:
                            received += len(block)
                            key.data.extend(block)
            code = child.wait(timeout=max(0.001, deadline - time.monotonic()))
            return code, bytes(data)
        finally:
            # The group leader can exit while a compiler/child still owns pipes.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=3)


def _test_results(output: str) -> dict[str, Any]:
    """Keep named terminal results independently of the compiler-output tail."""
    lines: list[str] = []
    used = 0
    truncated = False
    for line in output.splitlines():
        if not re.search(r"\b(?:Test|Suite) .+\b(?:passed|failed|skipped)\b", line):
            continue
        size = len(json.dumps(line).encode()) + 2
        if used + size > 16384:
            truncated = True
            continue
        lines.append(line)
        used += size
    return {"lines": lines, "truncated": truncated, "json_bytes": used}


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
    scratch: Path | None = None
    with ExitStack() as cleanup:
        def scratch_removed() -> None:
            if scratch is None:
                return
            removed = not scratch.exists()
            row("scratch_removed", "PASS" if removed else "FAIL", True, removed)
            if not removed:
                raise RuntimeError("supervisor scratch cleanup incomplete")

        # LIFO: fixture cleanup, TemporaryDirectory removal, then this check.
        cleanup.callback(scratch_removed)
        folder = cleanup.enter_context(tempfile.TemporaryDirectory(prefix="supervisor-", dir=tmp))
        scratch = Path(folder).resolve(strict=True)
        env = dict(os.environ)
        # Jenkins itself redirects HOME before this suite starts. Account data,
        # rather than the inherited scratch HOME, selects the read-only bookends.
        real_env = dict(os.environ)
        real_env["HOME"] = pwd.getpwuid(os.getuid()).pw_dir
        real_env.pop("CFFIXED_USER_HOME", None)
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

        def preferences() -> dict[str, Any]:
            values = {}
            for command in ("list-keychains", "default-keychain"):
                argv = ["/usr/bin/security", command, "-d", "user"]
                stderr = bytearray()
                try:
                    code, output = _command(argv, real_env, scratch,
                                            min(deadline + 30, time.monotonic() + 10),
                                            output_cap=4096, stderr_data=stderr)
                    stdout = "\n".join(line.strip() for line in output.decode(errors="replace").splitlines()).strip()
                    diagnostic = stderr.decode(errors="replace").strip()
                    category = "unexpected_error"
                    if code == 0 and not diagnostic:
                        category = "none"
                    elif (command == "default-keychain" and code == 1 and not stdout
                          and diagnostic == "security: SecKeychainCopyDomainDefault user: A default keychain could not be found."):
                        category = "no_default_keychain"
                    values[command] = {"exit_code": code, "stdout": stdout, "stderr_category": category}
                    if category == "unexpected_error":
                        values[command]["diagnostic"] = diagnostic[:1024]
                except (RuntimeError, TimeoutError, subprocess.TimeoutExpired) as exc:
                    values[command] = {"exit_code": None, "stdout": "", "stderr_category": "read_error",
                                       "error": type(exc).__name__}
            return values

        before = preferences()

        def preferences_unchanged() -> None:
            after = preferences()
            for command in before:
                unchanged = before[command] == after[command]
                row("real_keychain_" + command.replace("-", "_") + "_unchanged",
                    "PASS" if unchanged else "FAIL", "original user preference unchanged",
                    {"before": before[command], "after": after[command]})
            if any(item["status"] == "FAIL" for item in checks):
                raise RuntimeError("native verification or Keychain fixture failed; inspect checks")

        # Registered before fixture creation; runs after exact keychain deletion
        # and before scratch removal, even when preflight or tests fail.
        cleanup.callback(preferences_unchanged)
        before_ok = all(value["stderr_category"] in {"none", "no_default_keychain"}
                        for value in before.values())
        row("real_keychain_preferences_before", "PASS" if before_ok else "FAIL",
            "read user preferences with account HOME; empty list/no default are valid", before)
        if not before_ok:
            raise RuntimeError("real Keychain preferences unavailable; no fixture or tests ran")
        keychain = scratch / "home/Library/Keychains/login.keychain-db"
        keychain.parent.mkdir(parents=True, exist_ok=True)
        create_attempted = False

        def remove_keychain() -> None:
            if create_attempted:
                argv = ["/usr/bin/security", "delete-keychain", str(keychain)]
                try:
                    code, output = _command(argv, env, scratch,
                                            min(deadline + 30, time.monotonic() + 10),
                                            output_cap=4096)
                    gone = not keychain.exists()
                    row("disposable_keychain_deleted", "PASS" if code == 0 and gone else "FAIL",
                        "delete exact scratch login Keychain before scratch removal",
                        {"command": argv, "exit_code": code, "absent": gone,
                         "output": output.decode(errors="replace")[:1024]})
                except (RuntimeError, TimeoutError, subprocess.TimeoutExpired) as exc:
                    row("disposable_keychain_deleted", "FAIL", "delete exact scratch login Keychain",
                        {"error": type(exc).__name__})

        cleanup.callback(remove_keychain)
        password = secrets.token_hex(32)
        try:
            # security -i reads one command from stdin and returns that command's
            # status. Never use -v, expose the password in argv, or retain its output.
            quoted_path = '"' + str(keychain).replace("\\", "\\\\").replace('"', '\\"') + '"'
            if any(char in quoted_path for char in "\r\n"):
                raise RuntimeError("invalid scratch Keychain path")
            for command in ("create-keychain", "unlock-keychain", "set-keychain-settings"):
                secret = " -p " + password if command != "set-keychain-settings" else ""
                instruction = (command + secret + " " + quoted_path + "\n").encode()
                create_attempted = True
                try:
                    code, _ = _command(["/usr/bin/security", "-i"], env, scratch,
                                       min(deadline, time.monotonic() + 15),
                                       output_cap=4096, input_data=instruction)
                except (RuntimeError, TimeoutError, subprocess.TimeoutExpired) as exc:
                    code = -1
                    failure = type(exc).__name__
                else:
                    failure = None
                passed = code == 0 and keychain.is_file()
                row("disposable_keychain_" + command.replace("-keychain", "").replace("-", "_"),
                    "PASS" if passed else "FAIL", "scratch login Keychain; unlocked; no timeout",
                    {"operation": command, "path": str(keychain), "exit_code": code,
                     "password_transport": "stdin only; output suppressed", "error": failure})
                if not passed:
                    raise RuntimeError("disposable Keychain fixture failed; no tests ran")
        finally:
            password = ""
            instruction = b""
            secret = ""
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
            ("swift_test", ["--filter", "AppDelegateStopHonestyTests|ManagedHealthPollingTests|ManagedClientBridgeTests|EnrollmentKeyStoreTests|ManagedJoinTests|ManagedMembershipRevocationTests"]),
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
                 "sha256": hashlib.sha256(raw_output).hexdigest(), "tail": output[-4096:],
                 "test_results": _test_results(output)})
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
    if len(json.dumps(report).encode()) > 48000:
        raise RuntimeError("supervisor report exceeds reserved 48KiB budget")

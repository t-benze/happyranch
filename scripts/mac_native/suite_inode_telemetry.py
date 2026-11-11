"""S9 inode advisory observations; stdlib only, never runs the CI wrapper."""

from __future__ import annotations

import json
import os
import re
import selectors
import shlex
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
LIMIT = 16384
UNAVAILABLE = "WARNING: temporary-filesystem inode measurement unavailable; continuing fail-open.\n"
PRESSURE = "WARNING: temporary-filesystem inode use is at or above 90%; this advisory does not authorize cleanup.\n"


def advisory_source() -> str:
    # Test-side isolation of the shipping function, without sourcing/running all.
    source = (ROOT / "scripts/local_ci.sh").read_text(encoding="utf-8")
    match = re.search(r"^observe_tmp_inodes\(\) \{\n.*?^\}", source, re.M | re.S)
    if match is None:
        raise RuntimeError("cannot locate the shipping inode advisory")
    return match[0]


def command(script: str) -> tuple[int, str]:
    """Bound/reap each isolated Apple Bash command, including failed commands."""
    data = bytearray()
    deadline = time.monotonic() + 5
    with subprocess.Popen(
        ["/bin/bash", "-p", "-euo", "pipefail", "-c", script],
        cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, start_new_session=True,
    ) as child:
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("inode advisory command deadline")
                    for key, _ in selector.select(min(.1, remaining)):
                        block = os.read(key.fileobj.fileno(), 4096)
                        if not block:
                            selector.unregister(key.fileobj)
                        elif len(data) + len(block) > LIMIT:
                            raise RuntimeError("inode advisory output exceeds 16KiB")
                        else:
                            data.extend(block)
            code = child.wait(timeout=max(.001, deadline - time.monotonic()))
            return code, data.decode("utf-8", errors="strict")
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=2)


def script(source: str, setup: str) -> str:
    return "YELLOW=''\nNC=''\n" + setup + "\n" + source + "\nobserve_tmp_inodes\n"


def row(checks: list[dict[str, Any]], name: str, expected: Any, actual: Any) -> None:
    checks.append(dict(name=name, status="PASS" if expected == actual else "FAIL",
                       expected=expected, actual=actual))


def require_pass(checks: list[dict[str, Any]]) -> None:
    failed = [item["name"] for item in checks if item["status"] == "FAIL"]
    if failed:
        raise RuntimeError("inode telemetry checks failed: " + ", ".join(failed))


def fixture_checks(report: dict[str, Any]) -> None:
    """Portable fixtures drive the exact shipping Bash function, not a copy."""
    checks: list[dict[str, Any]] = []
    report["inode_telemetry_fixtures"] = {"checks": checks}
    source = advisory_source()
    gnu = "Filesystem Inodes IUsed IFree IUse% Mounted on\n"
    bsd = "Filesystem 512-blocks Used Available Capacity iused ifree %iused Mounted on\n"
    # Expected values are independent constants; block/inode percentages disagree.
    cases = [
        ("gnu_low", "Linux", gnu + "/dev/disk 1000 120 880 12% /tmp\n", 0,
         (1000, 120, 880, 12)),
        ("gnu_warning", "Linux", gnu + "/dev/disk 1000 900 100 90% /tmp\n", 0,
         (1000, 900, 100, 90)),
        ("bsd_blocks_high_inodes_low", "Darwin", bsd + "/dev/disk 1000 990 10 99% 120 880 12% /private/tmp\n", 0,
         (1000, 120, 880, 12)),
        ("bsd_inodes_high_blocks_low", "Darwin", bsd + "/dev/disk 1000 10 990 1% 900 100 90% /private/tmp\n", 0,
         (1000, 900, 100, 90)),
        ("bsd_large_total", "Darwin", bsd + "/dev/disk 1000 10 990 1% 1234567 2999998765433 0% /private/tmp\n", 0,
         (3000000000000, 1234567, 2999998765433, 0)),
        ("gnu_malformed", "Linux", gnu + "/dev/disk - - - - /tmp\n", 0, None),
        ("bsd_malformed", "Darwin", bsd + "/dev/disk 1000 990 10 99% - - - /tmp\n", 0, None),
        ("df_unavailable", "Darwin", "", 1, None),
        ("uname_unavailable", "", gnu + "/dev/disk 1000 900 100 90% /tmp\n", 0, None),
    ]
    for name, system, output, df_exit, values in cases:
        setup = ("export TMPDIR=/inode-fixture\n"
                 f"uname() {{ printf '%s\\n' {shlex.quote(system)}; return {0 if system else 1}; }}\n"
                 f"df() {{ printf '%s' {shlex.quote(output)}; return {df_exit}; }}")
        expected = UNAVAILABLE
        if values is not None:
            total, used, free, percent = values
            expected = ("=== Temporary-filesystem inode advisory ===\n"
                        f"path=/inode-fixture used={used} free={free} total={total} percent={percent}%\n"
                        + (PRESSURE if percent >= 90 else ""))
        code, actual = command(script(source, setup))
        row(checks, name, {"exit": 0, "output": expected}, {"exit": code, "output": actual})
    require_pass(checks)


def run(report: dict[str, Any]) -> None:
    """Darwin only: run nine fixtures, then compare one real df snapshot."""
    fixture_checks(report)
    checks: list[dict[str, Any]] = []
    section: dict[str, Any] = {"checks": checks, "bash": "/bin/bash"}
    report["inode_telemetry"] = section
    with tempfile.TemporaryDirectory(prefix="inode-telemetry-") as folder:
        scratch = Path(folder).resolve(strict=True)
        snapshot = scratch / "df.txt"
        setup = (f"export TMPDIR={shlex.quote(str(scratch))}\n"
                 # Capture the SAME sample the shipping parser consumes.
                 f"df() {{ command /bin/df \"$@\" | /usr/bin/tee {shlex.quote(str(snapshot))}; }}")
        code, output = command(script(advisory_source(), setup)
                               + "printf 'bash_version=%s\\n' \"$BASH_VERSION\"\n")
        with snapshot.open("rb") as stream:
            raw = stream.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise RuntimeError("df snapshot exceeds 16KiB")
        section.update(df=raw.decode("utf-8"), advisory=output)
        # BSD df includes block columns first. Independently read iused/ifree/%iused.
        fields = raw.decode("utf-8").splitlines()[1].split()
        used, free, percent = int(fields[5]), int(fields[6]), int(fields[7].removesuffix("%"))
        expected = {"used": used, "free": free, "total": used + free, "percent": percent}
        match = re.search(r"^path=.* used=(\d+) free=(\d+) total=(\d+) percent=(\d+)%$", output, re.M)
        actual = dict(zip(("used", "free", "total", "percent"), map(int, match.groups()))) if match else None
        row(checks, "real_df_inode_values", expected, actual)
        row(checks, "real_df_advisory_exit", 0, code)
        row(checks, "real_df_warning_threshold", percent >= 90, PRESSURE in output)
    require_pass(checks)
    # Keep probe.json/stdout below the existing identity/report allowance.
    if len(json.dumps(report).encode("utf-8")) > 32768:
        raise RuntimeError("inode telemetry report exceeds 32KiB")


if __name__ == "__main__":
    # Manual fixture venue on either OS; the native dispatcher still skips Linux.
    result: dict[str, Any] = {}
    try:
        fixture_checks(result)
    finally:
        print(json.dumps(result, indent=2, sort_keys=True))

"""Bounded, no-network native LaunchAgent lifecycle using a private sleep job."""
from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from runtime.remote_access.launchd_agent import LaunchdServiceManager, render_launch_agent
from scripts.mac_native.suite_supervisor import _command

ROOT = Path(__file__).resolve().parents[2]


def run(report: dict[str, Any]) -> None:
    label = f"ai.happyranch.connector-probe-{uuid.uuid4().hex}"
    checks: list[dict[str, Any]] = []
    section: dict[str, Any] = {"label": label, "checks": checks, "cleanup": {}}
    report["connector_launchd"] = section
    deadline = time.monotonic() + 90

    def command(argv: list[str], *, timeout: float = 10.0) -> tuple[int, str]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("connector-launchd probe deadline")
        code, output = _command(argv, dict(os.environ), ROOT,
                                min(deadline, time.monotonic() + timeout), output_cap=1048576)
        return code, output.decode("utf-8", errors="replace")

    def check(name: str, passed: bool, **observed: Any) -> None:
        checks.append(dict(name=name, status="PASS" if passed else "FAIL", **observed))
        if not passed:
            raise RuntimeError(f"connector-launchd check failed: {name}")

    with tempfile.TemporaryDirectory(prefix="connector-launchd-") as folder:
        scratch = Path(folder)
        manager = LaunchdServiceManager(run=command)
        path = manager.unit_dir / f"{label}.plist"
        domain: str | None = None
        owned = False
        try:
            domain = manager.domain
            section["domain"] = domain
            check("unique_service_absent_before_install", manager.status(label).load_state == "not-found")
            plist = render_launch_agent(label, ("/bin/sleep", "300"), logs_dir=scratch)
            owned = True  # Absence proven; cleanup owns any partial installation.
            installed = manager.install(plist, label)
            check("installed", installed == path and path.is_file())
            manager.start(label)

            def wait_running(previous_pid: int = 0) -> Any:
                until = min(deadline - 15, time.monotonic() + 30)
                while time.monotonic() < until:
                    status = manager.status(label)
                    if status.running and status.pid > 0 and status.pid != previous_pid:
                        return status
                    time.sleep(0.2)
                raise TimeoutError("LaunchAgent did not reach running with a new pid")

            first = wait_running()
            check("started", first.running and first.pid > 0, pid=first.pid)
            # The PID was obtained from our unique service target, never from
            # a global process-name search. Kill that exact stand-in process.
            os.kill(first.pid, signal.SIGKILL)
            recovered = wait_running(first.pid)
            check("keepalive_restarted", recovered.pid != first.pid, old_pid=first.pid,
                  new_pid=recovered.pid, last_exit_code=recovered.last_exit_code,
                  last_exit_signal=recovered.last_exit_signal)
            manager.stop(label)
            stopped = manager.status(label)
            check("stopped", not stopped.running and stopped.load_state == "unloaded", pid=stopped.pid)
            manager.uninstall(label)
            absent = manager.status(label)
            check("uninstalled", not absent.running and absent.load_state == "not-found" and not path.exists())
        finally:
            # Cleanup has its own small reserve even when the observation
            # budget expires. All paths/targets belong to this UUID only.
            cleanup = section["cleanup"]
            if owned:
                try:
                    code, _ = _command(["launchctl", "bootout", f"{domain}/{label}"],
                                       dict(os.environ), ROOT, time.monotonic() + 5)
                    cleanup["bootout_exit"] = code
                    code, output = _command(["launchctl", "print", f"{domain}/{label}"],
                                            dict(os.environ), ROOT, time.monotonic() + 5)
                    cleanup["service_absent"] = code != 0 and (
                        f'Could not find service "{label}" in domain' in output.decode("utf-8", errors="replace")
                    )
                except (OSError, RuntimeError, TimeoutError, subprocess.TimeoutExpired) as exc:
                    cleanup["service_absent"] = False
                    cleanup["error"] = type(exc).__name__
                try:
                    path.unlink(missing_ok=True)
                    cleanup["plist_absent"] = not path.exists()
                except OSError as exc:
                    cleanup["plist_absent"] = False
                    cleanup["file_error"] = type(exc).__name__
                if not cleanup.get("service_absent") or not cleanup.get("plist_absent"):
                    raise RuntimeError("connector-launchd cleanup incomplete; see cleanup receipt")
            else:
                # Domain failure/collision before install owns nothing to remove.
                cleanup["not_installed"] = True
    section["scratch_removed"] = not scratch.exists()
    check("scratch_removed", section["scratch_removed"])

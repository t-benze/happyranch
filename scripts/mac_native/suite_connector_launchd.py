"""Bounded, no-network native LaunchAgent lifecycle using a private sleep job."""
from __future__ import annotations

import os
import plistlib
import signal
import stat
import subprocess
import tempfile
import time
import uuid
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from runtime.remote_access.launchd_agent import LaunchdServiceManager, render_launch_agent
from scripts.mac_native.suite_supervisor import _command

ROOT = Path(__file__).resolve().parents[2]


def run(report: dict[str, Any]) -> None:
    label = f"ai.happyranch.connector-probe-{uuid.uuid4().hex}"
    checks: list[dict[str, Any]] = []
    uid = os.getuid()
    context: dict[str, Any] = {"uid": uid, "managername": None, "domains": {},
                               "selected_domain": None, "print_disabled": None}
    section: dict[str, Any] = {"label": label, "checks": checks, "cleanup": {},
                               "context": context, "domain": None, "status_observations": {}}
    report["connector_launchd"] = section
    deadline = time.monotonic() + 90
    step = "context"

    def command(argv: list[str], *, timeout: float = 10.0) -> tuple[int, str]:
        observation: dict[str, Any] | None = None
        if (argv[:2] == ["launchctl", "print-disabled"]
                or (argv[:2] == ["launchctl", "print"] and argv[2].endswith(f"/{label}"))):
            key = "print_disabled" if argv[1] == "print-disabled" else "print"
            observation = {"excerpt": None}
            # Overwrite only this step's last poll. Capture before parsing, so
            # both successful status and parser/command failures retain context.
            section["status_observations"].setdefault(step, {})[key] = observation
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("connector-launchd probe deadline")
            code, output = _command(argv, dict(os.environ), ROOT,
                                    min(deadline, time.monotonic() + timeout), output_cap=1048576)
            text = output.decode("utf-8", errors="replace")
            if observation is not None:
                observation.update(exit_code=code, excerpt=text[:2048])
            return code, text
        except Exception as exc:
            if observation is not None:
                raw = getattr(exc, "output", None)
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                observation.update(error=type(exc).__name__, diagnostic=str(exc)[:512],
                                   excerpt=raw[:2048] if isinstance(raw, str) else None)
            raise

    def check(name: str, passed: bool, **observed: Any) -> None:
        checks.append(dict(name=name, status="PASS" if passed else "FAIL", **observed))
        if not passed:
            raise RuntimeError(f"connector-launchd check failed: {name}")

    with ExitStack() as stack:
        temporary = tempfile.TemporaryDirectory(prefix="connector-launchd-")
        scratch = Path(temporary.name)

        def scratch_receipt() -> None:
            section["scratch_removed"] = not scratch.exists()
            check("scratch_removed", section["scratch_removed"])

        # LIFO: record after TemporaryDirectory removal on success AND failure.
        # A successful receipt never suppresses the original probe exception.
        stack.callback(scratch_receipt)
        stack.enter_context(temporary)
        manager = LaunchdServiceManager(run=command)
        path = manager.unit_dir / f"{label}.plist"
        domain: str | None = None
        owned = False
        try:
            # Preserve caller/domain facts before selection can refuse. These
            # observations share the existing 90s deadline and 1MiB command cap;
            # only 512 characters per diagnostic enter probe.json.
            code, output = command(["launchctl", "managername"])
            context["managername"] = {"exit_code": code, "diagnostic": output.strip()[:512]}
            for candidate in (f"gui/{uid}", f"user/{uid}"):
                code, output = command(["launchctl", "print", candidate])
                context["domains"][candidate] = {"exit_code": code, "diagnostic": output.strip()[:512]}
            domain = manager.domain
            section["domain"] = context["selected_domain"] = domain
            code, output = command(["launchctl", "print-disabled", domain])
            context["print_disabled"] = {"exit_code": code, "diagnostic": output.strip()[:512]}
            step = "absence"
            check("unique_service_absent_before_install", manager.status(label).load_state == "not-found")
            plist = render_launch_agent(label, ("/bin/sleep", "300"), logs_dir=scratch)
            owned = True  # Absence proven; cleanup owns any partial installation.
            step = "install"
            installed = manager.install(plist, label)
            check("installed", installed == path and path.is_file())
            step = "start"
            try:
                manager.start(label)
            except Exception:
                # Optional read-only context shares the original observation
                # budget. Every failed observation is recorded, never raised
                # in place of the actual start/bootstrap failure.
                failure: dict[str, Any] = {"plist": {"current_uid": uid}}
                section["start_failure"] = failure
                try:
                    info = path.stat()
                    failure["plist"].update(mode=oct(stat.S_IMODE(info.st_mode)), owner_uid=info.st_uid)
                    failure["plist"]["LimitLoadToSessionType"] = plistlib.loads(path.read_bytes()).get("LimitLoadToSessionType")
                except Exception as exc:
                    failure["plist"].update(error=type(exc).__name__, diagnostic=str(exc)[:512])
                for key, argv in (
                    ("plutil", ["plutil", "-lint", str(path)]),
                    ("launchd_log", ["log", "show", "--last", "2m", "--style", "compact", "--predicate",
                                     f'process == "launchd" AND eventMessage CONTAINS "{label}"']),
                ):
                    try:
                        code, output = command(argv)
                        observed: dict[str, Any] = {"exit_code": code}
                        if key == "launchd_log":
                            filtered = "\n".join(line for line in output.splitlines() if label in line)
                            observed["excerpt"] = filtered.encode("utf-8")[:4096].decode("utf-8", errors="ignore")
                        if key == "plutil" or code:
                            observed["diagnostic"] = output.strip()[:512]
                        failure[key] = observed
                    except Exception as exc:
                        failure[key] = {"error": type(exc).__name__, "diagnostic": str(exc)[:512]}
                raise

            def wait_running(previous_pid: int = 0) -> Any:
                until = min(deadline - 15, time.monotonic() + 30)
                while time.monotonic() < until:
                    status = manager.status(label)
                    if status.running and status.pid > 0 and status.pid != previous_pid:
                        return status
                    time.sleep(0.2)
                raise TimeoutError("LaunchAgent did not reach running with a new pid")

            step = "wait_running"
            first = wait_running()
            check("started", first.running and first.pid > 0, pid=first.pid)
            # The PID was obtained from our unique service target, never from
            # a global process-name search. Kill that exact stand-in process.
            os.kill(first.pid, signal.SIGKILL)
            step = "recovery"
            recovered = wait_running(first.pid)
            check("keepalive_restarted", recovered.pid != first.pid, old_pid=first.pid,
                  new_pid=recovered.pid, last_exit_code=recovered.last_exit_code,
                  last_exit_signal=recovered.last_exit_signal)
            step = "stop"
            manager.stop(label)
            step = "stopped"
            stopped = manager.status(label)
            check("stopped", not stopped.running and stopped.load_state == "unloaded", pid=stopped.pid)
            step = "uninstall"
            manager.uninstall(label)
            step = "absent"
            absent = manager.status(label)
            check("uninstalled", not absent.running and absent.load_state == "not-found" and not path.exists())
        except Exception as exc:
            section["status_observations"].setdefault(step, {}).update(
                error=type(exc).__name__, diagnostic=str(exc)[:512])
            raise
        finally:
            # Cleanup has its own small reserve even when the observation
            # budget expires. All paths/targets belong to this UUID only.
            cleanup = section["cleanup"]
            if owned:
                cleanup_print: dict[str, Any] = {"excerpt": None}
                section["status_observations"]["cleanup"] = {"print": cleanup_print}
                try:
                    code, _ = _command(["launchctl", "bootout", f"{domain}/{label}"],
                                       dict(os.environ), ROOT, time.monotonic() + 5)
                    cleanup["bootout_exit"] = code
                    code, output = _command(["launchctl", "print", f"{domain}/{label}"],
                                            dict(os.environ), ROOT, time.monotonic() + 5, output_cap=1048576)
                    cleanup_print.update(exit_code=code, excerpt=output.decode("utf-8", errors="replace")[:2048])
                    cleanup["service_absent"] = code != 0 and (
                        f'Could not find service "{label}" in domain' in output.decode("utf-8", errors="replace")
                    )
                except (OSError, RuntimeError, TimeoutError, subprocess.TimeoutExpired) as exc:
                    raw = getattr(exc, "output", None)
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", errors="replace")
                    cleanup_print.update(error=type(exc).__name__, diagnostic=str(exc)[:512],
                                         excerpt=raw[:2048] if isinstance(raw, str) else None)
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

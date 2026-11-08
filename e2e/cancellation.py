"""Disposable-runner supervisor: real signal delivery, honest failure and closure."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from owned import identity
from reporting import HOUSEKEEPING, Report, scrub
from run import daily_admission, validate_admission


def validate(row: dict, source: str, python: str, signum: int) -> None:
    result, active = row["launcher_result"], row["active"]
    validate_admission(result, row)
    if (row["source_sha"] != source or active["source_sha"] != source
            or result["source_sha"] != source or result["python"] != python
            or result["exercise"] != "cancellation" or result["status"] != "FAIL"
            or row["launcher_exit"] != 1):
        raise ValueError("cancellation source/cell/non-success mismatch")
    if (result["action_deadline"] != result["started"] + 720
            or result["hard_deadline"] != result["started"] + 900
            or result["action_finished"] > result["action_deadline"]
            or result["cleanup_deadline"] != min(result["action_finished"] + 60, result["started"] + 780)
            or result["cleanup_finished"] >= result["cleanup_deadline"]):
        raise ValueError("launcher operational deadline exceeded")
    for phase in result["phases"]:
        cap = 240  # All diagnostic commands, including its hold exercise.
        if (phase["deadline"] != min(phase["entered"] + cap, result["action_deadline"])
                or not result["started"] <= phase["entered"] <= phase["finished"] <= phase["deadline"]):
            raise ValueError("diagnostic command operational deadline exceeded")
    sent = row["sent"]
    caught = result["signals"]
    if (sent["signal"] != signum or len(caught) != 1 or caught[0]["signal"] != signum
            or any(caught[0][key] != active["launcher"][key] or sent[key] != active["launcher"][key] for key in ("pid", "start"))
            or caught[0]["at"] < sent["at"] or sent["at"] < active["at"]
            or not row["identities_live_at_signal"]):
        raise ValueError("missing actual launcher signal delivery during owned work")
    if (active["callback"]["status"] != 200 or active["callback"]["response"] != {"ok": True}
            or active["callback"]["dropped"] or not active["browser_started"]):
        raise ValueError("real callback/browser readiness missing")
    cleanup = result["cleanup"]
    if (cleanup["ok"] is not True or cleanup["data_absent"] is not True
            or cleanup["sockets_absent"] is not True or cleanup["watcher_stopped"] is not True
            or cleanup["survivors"] or cleanup["errors"] or not row["launcher_absent"]):
        raise ValueError("unknown or unsuccessful cancellation cleanup")
    if (not active["owned"]["identities"] or len(active["ports"]) != 2
            or active["owned"]["identities"].get(str(active["provider"]["pid"])) != active["provider"]["pid_start"]):
        raise ValueError("missing active provider ownership or listener evidence")
    for pid, start in active["owned"]["identities"].items():
        if cleanup["identities"].get(pid) != start:
            raise ValueError("lost pre-signal owned identity")
    if (not active["owned"]["units"] or not active["owned"]["groups"]
            or not cleanup["units"] or not cleanup["groups"]):
        raise ValueError("missing owned containment evidence")
    for unit in active["owned"]["units"]:
        if "LoadState=not-found" not in cleanup["units"][unit]["stdout"]:
            raise ValueError("owned unit not absent")
    if any(value is not False for value in cleanup["groups"].values()):
        raise ValueError("owned cgroup remains")
    if any(cleanup["groups"].get(group) is not False for group in active["owned"]["groups"]):
        raise ValueError("missing cgroup absence")
    if any(cleanup["ports_absent"].get(str(port)) is not True for port in active["ports"]):
        raise ValueError("missing owned port absence")
    phases = {p["name"]: p for p in result["phases"]}
    required = {"dependencies", "browser-dependencies", "browser-install", "web-dependencies", "web-build", "scenarios"}
    if set(phases) != required or any(phases[n]["exit"] != 0 for n in required - {"scenarios"}):
        raise ValueError("setup failed before cancellation")
    if not phases["scenarios"]["interrupted"] or phases["scenarios"]["exit"] is not None:
        raise ValueError("scenarios were not active at signal")
    if result["total_seconds"] > 900 or row["total_seconds"] > 900:
        raise ValueError("cancellation hard deadline exceeded")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", required=True)
    parser.add_argument("--signal", choices=["TERM", "INT"], required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    output = args.artifacts.resolve()
    output.mkdir(parents=True, exist_ok=True)
    (output / "logs").mkdir(exist_ok=True)
    source = Path(__file__).resolve().parents[1]
    version = "3.12" if args.signal == "TERM" else "3.14"
    row = {"exercise": "cancellation", "python": version}
    report = Report(row)
    report.expect("diagnostic", ["launcher." + args.signal, *HOUSEKEEPING])
    started = time.monotonic()
    launcher = None
    launcher_start = None
    private = None
    signum = getattr(signal, "SIG" + args.signal)
    try:
        started = float(os.environ.get("E2E_STARTED_MONOTONIC", started))
        if not 0 <= time.monotonic() - started < 720:
            raise RuntimeError("Invalid cleanup-inclusive clock")
        row.update(daily_admission(source))
        private = Path(tempfile.mkdtemp(prefix="e2e-private-cancellation-", dir=os.environ.get("RUNNER_TEMP")))
        private.chmod(0o700)
        command = [sys.executable, str(source / "e2e/run.py"), "--python", args.python,
                   "--exercise", "cancellation", "--artifacts", str(output), "--handoff", str(private)]
        row["command"] = command
        with (private / "launcher.log").open("w") as log:
            launcher = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            launcher_start = identity(launcher.pid)
            ready = private / "active.json"
            while not ready.exists():
                if launcher.poll() is not None:
                    raise RuntimeError(f"Launcher exited before active work: {launcher.returncode}")
                if time.monotonic() >= started + 720:
                    raise TimeoutError("No active work before action cutoff")
                time.sleep(0.05)
            active = json.loads(ready.read_text())
            row["active"] = active
            if active["launcher"] != {"pid": launcher.pid, "start": launcher_start}:
                raise RuntimeError("Launcher identity mismatch")
            identities = [active["launcher"], active["daemon"], active["controller"],
                          {"pid": active["provider"]["pid"], "start": active["provider"]["pid_start"]}]
            row["identities_live_at_signal"] = all(identity(p["pid"]) == p["start"] for p in identities)
            if not row["identities_live_at_signal"]:
                raise RuntimeError("Owned work no longer active")
            descriptor = os.pidfd_open(launcher.pid)
            try:
                if identity(launcher.pid) != launcher_start:
                    raise RuntimeError("Launcher changed before signal")
                row["sent"] = {"signal": int(signum), "at": time.monotonic(), "pid": launcher.pid, "start": launcher_start}
                signal.pidfd_send_signal(descriptor, signum)
            finally:
                os.close(descriptor)
            remaining = started + 900 - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Cancellation hard deadline exhausted")
            row["launcher_exit"] = launcher.wait(timeout=remaining)
        row["launcher_absent"] = identity(launcher.pid) != launcher_start
        row["launcher_result"] = json.loads((private / "result.json").read_text())
        row["total_seconds"] = time.monotonic() - started
        validate(row, row["source_sha"], version, int(signum))
        report.record("diagnostic", "launcher." + args.signal)
    except BaseException as exc:  # noqa: BLE001 - finalize reports and owned cleanup on interruption
        report.record("diagnostic", "launcher." + args.signal, exc)
    finally:
        # Exact-child fallback, within the original hard envelope; never claim
        # closure merely because the supervisor failed or a PID is missing.
        if launcher is not None and launcher.poll() is None and identity(launcher.pid) == launcher_start:
            try:
                launcher.send_signal(signal.SIGTERM)
                launcher.wait(timeout=max(0, min(180, started + 900 - time.monotonic())))
            except (subprocess.TimeoutExpired, OSError):
                report.record("diagnostic", "owned-processes", RuntimeError("Launcher cleanup remains unknown"))
        if private is not None and (private / "result.json").is_file():
            with report.check("diagnostic", "setup"):
                result = json.loads((private / "result.json").read_text())
                phases = {p["name"]: p for p in result["phases"]}
                required = {"dependencies", "browser-dependencies", "browser-install", "web-dependencies", "web-build", "scenarios"}
                if set(phases) != required or any(phases[n]["exit"] != 0 for n in required - {"scenarios"}):
                    raise AssertionError("Launcher setup did not finish")
            cleanup = result.get("cleanup", {}) if "result" in locals() else {}
            conditions = {
                "owned-processes": cleanup.get("ok") is True and cleanup.get("watcher_stopped") is True
                    and not cleanup.get("survivors", True) and not cleanup.get("errors", True)
                    and launcher is not None and identity(launcher.pid) != launcher_start,
                "ports": bool(cleanup.get("ports_absent")) and all(v is True for v in cleanup["ports_absent"].values()),
                "sockets": cleanup.get("sockets_absent") is True,
                "data": cleanup.get("data_absent") is True,
            }
            for name, passed in conditions.items():
                report.record("diagnostic", name, None if passed else RuntimeError("Launcher " + name + " cleanup failed or unknown"))
        if "result" not in locals():
            for name in HOUSEKEEPING:
                report.record("diagnostic", name, RuntimeError("Launcher setup/cleanup result unavailable"))
        if private is not None:
            with report.check("diagnostic", "data"):
                # Inner launcher console contains only named case outcomes.
                log = private / "launcher.log"
                if log.is_file():
                    (output / "logs/launcher.log").write_text(scrub(log.read_text(errors="replace"), report.tokens))
                shutil.rmtree(private)
                if private.exists():
                    raise RuntimeError("Supervisor private data remains")
        if time.monotonic() - started > 900:
            report.record("diagnostic", "launcher." + args.signal, TimeoutError("Hard deadline exceeded"))
        (output / "logs/harness.log").write_text("\n".join(f"{'FAIL' if case['kind'] else 'PASS'} {name}" for (_, name), case in report.cases.items()) + "\n")
        report.write(output / "reports/junit.xml")
        report.console()
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

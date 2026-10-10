"""Disposable-runner supervisor: real signal delivery, honest failure and closure."""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import traceback
from pathlib import Path

from owned import identity


def validate(row: dict, source: str, python: str, signum: int) -> None:
    result, active = row["launcher_result"], row["active"]
    if (row["source_sha"] != source or active["source_sha"] != source
            or result["source_sha"] != source or result["python"] != python
            or result["exercise"] != "cancellation" or result["status"] != "FAIL"
            or row["launcher_exit"] != 1 or result["skipped"] != 0):
        raise ValueError("cancellation source/cell/non-success mismatch")
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
    output.mkdir(parents=True, exist_ok=False)
    started = float(os.environ.get("E2E_STARTED_MONOTONIC", time.monotonic()))
    if not 0 <= time.monotonic() - started < 720:
        raise RuntimeError("invalid cleanup-inclusive clock")
    source = Path(__file__).resolve().parents[1]
    row = dict(status="FAIL", source_sha=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip())
    launcher = None
    launcher_start = None
    signum = getattr(signal, "SIG" + args.signal)
    try:
        command = [sys.executable, str(source / "e2e/run.py"), "--python", args.python,
                   "--exercise", "cancellation", "--artifacts", str(output / "launcher")]
        row["command"] = command
        with (output / "launcher.log").open("w") as log:
            launcher = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            launcher_start = identity(launcher.pid)
            ready = output / "launcher/active.json"
            while not ready.exists():
                if launcher.poll() is not None:
                    raise RuntimeError(f"launcher exited before active witness: {launcher.returncode}")
                if time.monotonic() >= started + 720:
                    raise TimeoutError("no active witness before action cutoff")
                time.sleep(0.05)
            active = json.loads(ready.read_text())
            row["active"] = active
            if active["launcher"] != dict(pid=launcher.pid, start=launcher_start):
                raise RuntimeError("launcher identity mismatch")
            identities = [active["launcher"], active["daemon"], active["controller"],
                          dict(pid=active["provider"]["pid"], start=active["provider"]["pid_start"])]
            row["identities_live_at_signal"] = all(identity(p["pid"]) == p["start"] for p in identities)
            if not row["identities_live_at_signal"]:
                raise RuntimeError("owned work no longer active")
            descriptor = os.pidfd_open(launcher.pid)
            try:
                if identity(launcher.pid) != launcher_start:
                    raise RuntimeError("launcher changed before signal")
                row["sent"] = dict(signal=int(signum), at=time.monotonic(), pid=launcher.pid, start=launcher_start)
                signal.pidfd_send_signal(descriptor, signum)
            finally:
                os.close(descriptor)
            row["launcher_exit"] = launcher.wait(timeout=max(1, started + 840 - time.monotonic()))
        row["launcher_absent"] = identity(launcher.pid) != launcher_start
        row["launcher_result"] = json.loads((output / "launcher/result.json").read_text())
        row["total_seconds"] = time.monotonic() - started
        validate(row, row["source_sha"], row["launcher_result"]["python"], int(signum))
        row["status"] = "PASS"
    except BaseException:
        row["error"] = traceback.format_exc()
    finally:
        # A failed supervisor never fabricates closure. Give its exact child the
        # ordinary catchable teardown opportunity; the runner's 15m cap remains.
        if launcher is not None and launcher.poll() is None and identity(launcher.pid) == launcher_start:
            launcher.send_signal(signal.SIGTERM)
            try:
                row["launcher_exit"] = launcher.wait(timeout=65)
            except subprocess.TimeoutExpired:
                row["cleanup_unknown"] = True
        row["total_seconds"] = time.monotonic() - started
        row["target_exceeded"] = row["total_seconds"] > 600
        (output / "cancellation.json").write_text(json.dumps(row, indent=2))
    return 0 if row["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

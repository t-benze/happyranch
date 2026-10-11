#!/usr/bin/env python3
"""Standalone, bounded disposable-runner entry; never a pytest selection."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

from owned import Owned, identity


# Existing baseline artifact presence contract; no content/projection oracle.
BASELINE_ARTIFACTS = ["observations.json", "browser-events.json", "daemon.txt",
                      "lifecycle.png", "response-loss.png", "org-switch-0.png", "org-switch-1.png",
                      "org-switch-2.png", "org-switch-return-revalidated.png", "browser-final.png", "e07.json", "e07-prefix.json", "launch-witnesses.jsonl",
                      "e07-parent.png", "e07-child.png", "e07-list.png"]


def arm_deadline(deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("exhausted absolute deadline")
    # alarm(int(remaining)) can disable the timer for a positive fraction.
    signal.setitimer(signal.ITIMER_REAL, remaining)


def validate_timing(row: dict) -> None:
    """Require complete, bounded launcher receipts in both daily consumers."""
    timing = row["timing"]
    started, action_end, hard_end = (timing[key] for key in ("started", "action_deadline", "hard_deadline"))
    if not all(math.isfinite(value) for value in (started, action_end, hard_end)):
        raise ValueError("nonfinite timing")
    if action_end != started + 720 or hard_end != started + 900:
        raise ValueError("changed outer deadlines")
    action = timing["action"]
    if (not started <= action["finished"] <= action_end or action["deadline"] != action_end
            or action["allowance_seconds"] != 720 or action["elapsed_seconds"] != action["finished"] - started):
        raise ValueError("action budget exhausted")
    cleanup, export = timing["cleanup"], timing["export"]
    for phase, cap, outer in ((cleanup, 60, min(action_end + 60, hard_end)), (export, 120, hard_end)):
        if (phase["ok"] is not True or not action["finished"] <= phase["entered"]
                or phase["deadline"] != min(phase["entered"] + cap, outer)
                or not phase["entered"] <= phase["finished"] < phase["deadline"]
                or phase["allowance_seconds"] != phase["deadline"] - phase["entered"]
                or phase["elapsed_seconds"] != phase["finished"] - phase["entered"]):
            raise ValueError("missing, failed or inconsistent reserve timing")
    if cleanup["finished"] > export["entered"]:
        raise ValueError("overlapping cleanup/export")
    if (row["total_seconds"] != export["finished"] - started
            or row["evidence_seconds"] != export["elapsed_seconds"]):
        raise ValueError("inconsistent elapsed timing")
    for phase in row["phases"]:
        cap = 480 if phase["name"] == "scenarios" and row["exercise"] == "baseline" else 240
        if (phase["cap_seconds"] != cap
                or phase["deadline"] != min(phase["entered"] + cap, action_end)
                or phase["allowance_seconds"] != phase["deadline"] - phase["entered"]
                or not started <= phase["entered"] <= phase["finished"] <= phase["deadline"]):
            raise ValueError("inconsistent command timing")


def daily_admission(source: Path) -> dict:
    """Validate Actions' scheduled default-branch source before launching work.

    Environment/payload checks are admission validation, not a hostile-runner
    security boundary. No CLI event override or local execution alias exists.
    """
    if os.environ.get("GITHUB_EVENT_NAME") != "schedule":
        raise RuntimeError("fresh Product E2E requires the daily schedule event; absent/other events refuse")
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        raise RuntimeError("scheduled Actions event payload is required")
    event = json.loads(Path(event_path).read_text())
    repository = event["repository"]
    branch = repository["default_branch"]
    repo = repository["full_name"]
    ref = "refs/heads/" + branch
    sha = os.environ.get("GITHUB_SHA", "")
    if (event.get("schedule") != "0 19 * * *" or not branch
            or os.environ.get("GITHUB_REPOSITORY") != repo
            or os.environ.get("GITHUB_REF") != ref
            or os.environ.get("GITHUB_WORKFLOW_REF") != repo + "/.github/workflows/e2e.yml@" + ref
            or len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha)):
        raise RuntimeError("daily cron/default-branch/workflow/source metadata mismatch")
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    if actual != sha:
        raise RuntimeError("checkout differs from immutable scheduled github.sha")
    return dict(event="schedule", cron=event["schedule"], source_sha=sha, source_ref=ref)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", required=True, help="absolute supported interpreter")
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--exercise", choices=["baseline", "cancellation"], default="baseline")
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    output = args.artifacts.resolve()
    output.mkdir(parents=True, exist_ok=False)
    started = float(os.environ.get("E2E_STARTED_MONOTONIC", time.monotonic()))
    receipt = dict(schema=1, status="ENVIRONMENT_FAILURE", cleanup={"ok": False}, skipped=0,
                   variants={}, phases=[], source_sha=None, python=None,
                   exercise=args.exercise, signals=[])
    root = None
    owner = None
    logs = []
    action_end, hard_end = started + 720, started + 900
    receipt["timing"] = dict(started=started, action_deadline=action_end,
                             hard_deadline=hard_end, phase="action")

    def interrupted(signum: int, _frame: object) -> None:
        phase = receipt["timing"]["phase"]
        if signum == signal.SIGALRM and phase != "action":
            receipt["timing"][phase]["timeout_signal"] = dict(signal=signum, at=time.monotonic(),
                                                              pid=os.getpid(), start=identity(os.getpid()), phase=phase)
            raise TimeoutError(f"{phase} deadline exhausted")
        receipt["signals"].append(dict(signal=signum, at=time.monotonic(),
                                       pid=os.getpid(), start=identity(os.getpid()), phase=phase))
        raise RuntimeError(f"runner interrupted by signal {signum}")

    # Register before dependencies, daemon, browser or any other child launch.
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGALRM):
        signal.signal(sig, interrupted)
    try:
        if not 0 <= time.monotonic() - started < 720:
            raise RuntimeError("invalid or exhausted cleanup-inclusive start clock")
        arm_deadline(action_end)
        receipt.update(daily_admission(source))
        if not (sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true"
                and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
                and os.environ.get("RUNNER_OS") == "Linux"):
            raise RuntimeError("E2E requires an authorized disposable GitHub Ubuntu runner")
        if not Path(args.python).is_absolute():
            raise RuntimeError("interpreter must be absolute")
        version = subprocess.check_output([args.python, "-I", "-c", "import sys; print('.'.join(map(str,sys.version_info[:2])))"], text=True).strip()
        if version not in {"3.12", "3.13", "3.14"}:
            raise RuntimeError("unsupported Python")
        receipt["python"] = version
        receipt["source_sha"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
        if subprocess.check_output(["git", "status", "--porcelain"], cwd=source, text=True):
            raise RuntimeError("clean committed source required")
        receipt["interpreter"] = dict(path=str(Path(args.python).resolve()),
                                      sha256=hashlib.sha256(Path(args.python).resolve().read_bytes()).hexdigest())
        node, npm, uv = (shutil.which(tool) for tool in ("node", "npm", "uv"))
        if not all((node, npm, uv)):
            raise RuntimeError("Node24/npm/uv required")
        node_version = subprocess.check_output([node, "--version"], text=True).strip()
        if not node_version.startswith("v24."):
            raise RuntimeError("Node major must be 24")
        receipt["node"] = dict(path=node, version=node_version)
        root = Path(tempfile.mkdtemp(prefix="happyranch-e2e-"))
        root.chmod(0o700)
        for name in ("home", "config", "cache", "data", "tmp", "daemon", "client", "coord", "bin", "witness", "raw"):
            (root / name).mkdir(mode=0o700)
        owner = Owned(root)
        env = {"HOME": str(root / "home"), "XDG_CONFIG_HOME": str(root / "config"),
               "XDG_CACHE_HOME": str(root / "cache"), "XDG_DATA_HOME": str(root / "data"),
               "TMPDIR": str(root / "tmp"), "TMP": str(root / "tmp"), "TEMP": str(root / "tmp"),
               "PATH": os.pathsep.join(dict.fromkeys([str(Path(node).parent), str(Path(uv).parent), "/usr/local/bin", "/usr/bin", "/bin"])),
               "LANG": "C.UTF-8", "E2E_ROOT": str(root), "CI": "1",
               "HAPPYRANCH_DAEMON_HOME": str(root / "daemon"), "HAPPYRANCH_DAEMON_PORT": "0",
               "HAPPYRANCH_WEB_DIST": str(source / "web" / "dist"),
               "UV_PROJECT_ENVIRONMENT": str(root / "venv"), "UV_PYTHON_DOWNLOADS": "never",
               "PLAYWRIGHT_BROWSERS_PATH": str(root / "browsers")}
        # Production systemd selection may need the user's runtime socket.
        # These contain paths only, and never substitute/force a backend.
        for key in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"):
            if key in os.environ:
                env[key] = os.environ[key]

        def command(name: str, argv: list[str], cwd: Path = source) -> None:
            begin = time.monotonic()
            cap = 480 if name == "scenarios" and args.exercise == "baseline" else 240
            end = min(begin + cap, action_end)
            if name == "scenarios":
                argv = [*argv, "--deadline", str(end)]
            arm_deadline(end)  # Reject exhaustion before opening logs or spawning.
            log = root / "raw" / (name + ".txt")
            logs.append(log)
            with log.open("w") as stream:
                arm_deadline(end)
                process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT)
                owner.add(process.pid)
                active_phase = dict(name=name, argv=argv, pid=process.pid, exit=None,
                                    entered=begin, deadline=end, cap_seconds=cap, allowance_seconds=end - begin)
                receipt["phases"].append(active_phase)
                try:
                    while process.poll() is None:
                        if time.monotonic() >= end:
                            raise TimeoutError(f"{name} action deadline")
                        ready = root / "cancellation-ready.json"
                        if args.exercise == "cancellation" and ready.exists() and not (output / "active.json").exists():
                            witness = json.loads(ready.read_text())
                            owner.sample()
                            with owner.lock:
                                if (owner.pids.get(witness["provider"]["pid"]) == witness["provider"]["pid_start"]
                                        and owner.units and owner.groups):
                                    active = dict(witness, launcher=dict(pid=os.getpid(), start=identity(os.getpid())),
                                                  owned=dict(identities=dict(owner.pids), units=sorted(owner.units),
                                                             groups=sorted(owner.groups)), source_sha=receipt["source_sha"])
                                    temporary = output / "active.tmp"
                                    temporary.write_text(json.dumps(active, indent=2))
                                    temporary.replace(output / "active.json")
                        time.sleep(0.02)
                    code = process.returncode
                    active_phase["exit"] = code
                finally:
                    active_phase["finished"] = time.monotonic()
                    active_phase["seconds"] = active_phase["finished"] - begin
                    active_phase["interrupted"] = process.poll() is None
            arm_deadline(action_end)
            if code:
                raise RuntimeError(f"{name} exited {code}")

        command("dependencies", [uv, "sync", "--frozen", "--python", args.python])
        python = str(root / "venv" / "bin" / "python")
        command("browser-dependencies", [uv, "pip", "install", "--python", python, "--require-hashes", "-r", str(source / "e2e" / "browser.lock")])
        command("browser-install", [python, "-I", "-m", "playwright", "install", "chromium"])
        command("web-dependencies", [npm, "ci"], source / "web")
        command("web-build", [npm, "run", "build"], source / "web")
        # Harness uses only stdlib + locked browser library; runtime lives in
        # separate real processes. No runtime/pytest import in the controller.
        command("scenarios", [python, "-I", str(source / "e2e" / "controller.py"),
                              "--root", str(root), "--source", str(source),
                              "--sha", receipt["source_sha"],
                              "--exercise", args.exercise])
        result = json.loads((root / "scenario.json").read_text())
        receipt.update(result)
        receipt["status"] = "PASS"
    except BaseException as exc:
        receipt["failure_phase"] = "action"
        receipt["failure"] = repr(exc)
        receipt["traceback"] = traceback.format_exc()
    finally:
        action_finished = time.monotonic()
        receipt["timing"]["action"] = dict(finished=action_finished, deadline=action_end,
                                           allowance_seconds=720, elapsed_seconds=action_finished - started)
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, signal.SIG_IGN)
        staged: dict[str, bytes] = {}
        token = ""
        redaction_context: list[str] = []

        def redact(text: str) -> str:
            if not token:
                raise ValueError("redaction context unavailable")
            for secret in [token, *redaction_context]:
                text = text.replace(secret, "[REDACTED]")
            return text

        def collect_evidence() -> None:
            nonlocal token, redaction_context
            if not root or not root.exists():
                return
            # Capture once: partial removal must never erase trusted context.
            if not token:
                try:
                    token = (root / "daemon" / "daemon.token").read_text().strip()
                except TimeoutError:
                    raise  # Reserve expiry is not an ordinary token-read failure.
                except (OSError, UnicodeError):
                    return
            if not token:
                return  # No raw text/receipt is safe without established context.
            context_path = root / "coord/redaction-context.json"
            if context_path.exists():
                context = json.loads(context_path.read_text())
                if not isinstance(context, list) or not context or not all(isinstance(s, str) and s for s in context) or token not in context:
                    raise ValueError("invalid retained redaction context")
                redaction_context = context
            if (root / "scenario.json").exists():
                result = json.loads((root / "scenario.json").read_text())
                receipt.update({k: result[k] for k in ("variants", "scenario_phases", "e07") if k in result})
            for path in (root / "raw").glob("*"):
                if path.suffix in {".txt", ".json", ".jsonl"}:
                    staged[path.name] = redact(path.read_text(errors="replace")).encode()
                elif path.suffix == ".png":
                    staged[path.name] = path.read_bytes()

        def reserve(name: str, cap: float, outer: float) -> dict:
            entered = time.monotonic()
            phase = dict(entered=entered, deadline=min(entered + cap, outer), ok=False)
            phase["allowance_seconds"] = max(0, phase["deadline"] - entered)
            receipt["timing"].update({"phase": name, name: phase})
            return phase

        cleanup = reserve("cleanup", 60, min(action_end + 60, hard_end))
        try:
            arm_deadline(cleanup["deadline"])
            try:
                receipt["cleanup"] = owner.cleanup() if owner else {"ok": False, "reason": "setup did not admit containment"}
            except TimeoutError:
                raise
            except Exception as exc:
                receipt["cleanup"] = dict(ok=False, error=repr(exc))
            # Snapshot sanitized evidence before deleting its owned source. All
            # port checks, data/socket removal and staging consume cleanup time.
            collect_evidence()
            if root:
                ports = json.loads((root / "ports.json").read_text()) if (root / "ports.json").exists() else []
                import socket
                absence = {}
                for port in ports:
                    with socket.socket() as sock:
                        sock.settimeout(1)
                        absence[str(port)] = sock.connect_ex(("127.0.0.1", port)) != 0
                receipt["cleanup"]["ports_absent"] = absence
                shutil.rmtree(root)
                receipt["cleanup"]["data_absent"] = not root.exists()
                receipt["cleanup"]["sockets_absent"] = not root.exists()
                receipt["cleanup"]["ok"] &= all(absence.values()) and not root.exists()
            cleanup["ok"] = receipt["cleanup"]["ok"] is True
        except BaseException as exc:
            receipt["cleanup"].update(ok=False, error=repr(exc))
            cleanup["error"] = type(exc).__name__
        finally:
            cleanup["finished"] = time.monotonic()
            cleanup["elapsed_seconds"] = cleanup["finished"] - cleanup["entered"]
            cleanup["ok"] &= cleanup["finished"] < cleanup["deadline"]
            if not cleanup["ok"]:
                receipt["cleanup"]["ok"] = False
                receipt.setdefault("failure_phase", "cleanup")
                # A timed-out cleanup may not have returned its normal receipt.
                # Keep every observed identity/unit/group, without claiming absence.
                if owner:
                    receipt["cleanup"].setdefault("identities", owner.pids.copy())
                    receipt["cleanup"].setdefault("observed_units", sorted(owner.units.copy()))
                    receipt["cleanup"].setdefault("observed_groups", sorted(owner.groups.copy()))

        export = reserve("export", 120, hard_end)

        def publish(row: dict) -> None:
            temporary = output / "result.tmp"
            temporary.write_text(json.dumps(row, indent=2))
            temporary.replace(output / "result.json")

        def failure_receipt() -> dict:
            # Bounded metadata only: no raw exception/token text or partial PASS.
            closure = receipt["cleanup"]
            safe_cleanup = {key: closure[key] for key in
                            ("identities", "groups", "ports_absent", "data_absent", "sockets_absent", "watcher_stopped")
                            if key in closure}
            safe_cleanup.update(ok=False, observed_units=closure.get("observed_units", sorted(closure.get("units", {}))),
                                observed_groups=closure.get("observed_groups", sorted(closure.get("groups", {}))))
            binding = {key: receipt[key] for key in ("event", "cron", "source_ref", "interpreter", "node") if key in receipt}
            return dict(binding, schema=1, status="FAIL", source_sha=receipt["source_sha"],
                        python=receipt["python"], exercise=args.exercise, skipped=0,
                        variants={}, signals=receipt["signals"], phases=receipt["phases"],
                        cleanup=safe_cleanup, timing=receipt["timing"],
                        failure_phase=receipt.get("failure_phase", "export"),
                        failure="incomplete detailed evidence",
                        total_seconds=time.monotonic() - started)

        try:
            arm_deadline(export["deadline"])
            publish(failure_receipt())
            # Keep the final second of this SAME allowance for a failure receipt.
            arm_deadline(export["deadline"] - 1)
            if root and root.exists():
                # Preserve safe partial staging even if recollection fails.
                for name, content in staged.items():
                    (output / name).write_bytes(content)
                collect_evidence()  # Read-only partial diagnostics; never teardown.
            for name, content in staged.items():
                (output / name).write_bytes(content)
            if not token:
                raise ValueError("redaction context unavailable")
            if receipt["status"] == "PASS" and args.exercise == "baseline":
                required = BASELINE_ARTIFACTS + [phase["name"] + ".txt" for phase in receipt["phases"]]
                if any(name not in staged or (name.endswith((".json", ".png")) and not staged[name]) for name in required):
                    raise ValueError("missing/empty baseline evidence")
            manifest = json.loads((source / "e2e" / "manifest.json").read_text())
            export["finished"] = time.monotonic()
            export["elapsed_seconds"] = export["finished"] - export["entered"]
            export["ok"] = True
            receipt["evidence_seconds"] = export["elapsed_seconds"]
            receipt["total_seconds"] = export["finished"] - started
            receipt["target_exceeded"] = receipt["total_seconds"] > 600
            if (args.exercise != "baseline" or receipt["signals"] or not cleanup["ok"]
                    or set(receipt["variants"]) != set(manifest["variants"])
                    or any(row["status"] != "PASS" for row in receipt["variants"].values())):
                receipt["status"] = "FAIL"
            if receipt["status"] == "PASS":
                validate_timing(receipt)
            publish(json.loads(redact(json.dumps(receipt))))
        except BaseException as exc:
            export["ok"] = False
            export["error"] = type(exc).__name__
            export["finished"] = time.monotonic()
            export["elapsed_seconds"] = export["finished"] - export["entered"]
            receipt["status"] = "FAIL"
            receipt.setdefault("failure_phase", "export")
            try:
                arm_deadline(export["deadline"])
                publish(failure_receipt())
            except BaseException:
                pass  # Initial FAIL survives; no work after the absolute bound.
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

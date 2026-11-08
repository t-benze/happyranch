"""Bounded disposable-runner entry with private checks and ordinary JUnit."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from owned import Owned, identity
from reporting import HOUSEKEEPING, Report, scrub, scrub_trace

ADMISSION_FIELDS = ("event", "cron", "repository", "workflow_ref", "workflow_sha",
                    "run_id", "run_attempt", "source_ref", "source_sha", "source_tree")

def arm_deadline(deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("exhausted absolute deadline")
    # alarm(int(remaining)) can disable the timer for a positive fraction.
    signal.setitimer(signal.ITIMER_REAL, remaining)

def copy_npm_downloads(source: Path, destination: Path, deadline: float) -> None:
    """Copy only ordinary _cacache trees; no shared live state or race fence."""
    def fail(error: OSError) -> None:
        raise error

    def validate(path: Path) -> bool:
        if not path.is_absolute() or path.name != "_cacache" or ".." in path.parts:
            raise ValueError("invalid npm download-store path")
        # Check parents too: even a missing leaf must not conceal an alias.
        for parent in (*reversed(path.parents), path):
            arm_deadline(deadline)
            try:
                info = parent.lstat()
            except FileNotFoundError:
                return False
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError(f"non-directory or aliased npm download path: {parent}")
        for current, directories, files in os.walk(path, onerror=fail, followlinks=False):
            arm_deadline(deadline)
            for name in directories + files:
                arm_deadline(deadline)
                entry = Path(current) / name
                info = entry.lstat()
                if not (stat.S_ISDIR(info.st_mode)
                        or (stat.S_ISREG(info.st_mode) and info.st_nlink == 1)):
                    raise ValueError(f"aliased or special npm download entry: {entry}")
        return True

    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("overlapping npm download stores")
    present = validate(source)
    validate(destination)
    if present:
        arm_deadline(deadline)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination, dirs_exist_ok=True, copy_function=shutil.copyfile)
    arm_deadline(deadline)

def validate_admission(row: dict, admission: dict) -> None:
    """Join a receipt to the independently admitted Actions event/run/source."""
    if any(key not in row or row[key] != admission[key] for key in ADMISSION_FIELDS):
        raise ValueError("Actions event/run/source receipt mismatch")

def daily_admission(source: Path) -> dict:
    """Admit the daily schedule or an explicitly authorized main dispatch.

    Environment/payload checks are admission validation, not a hostile-runner
    security boundary or execution allocation. No CLI event override exists.
    """
    event_name = os.environ.get("GITHUB_EVENT_NAME")
    if event_name not in {"schedule", "workflow_dispatch"}:
        raise RuntimeError("fresh Product E2E requires schedule or authorized main workflow_dispatch")
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        raise RuntimeError("Actions event payload is required")
    event = json.loads(Path(event_path).read_text())
    repository = event["repository"]
    branch = repository["default_branch"]
    repo = repository["full_name"]
    if not isinstance(branch, str) or not branch or not isinstance(repo, str) or not repo:
        raise RuntimeError("missing default-branch/repository identity")
    ref = "refs/heads/" + branch
    if event_name == "schedule":
        if event.get("schedule") != "0 19 * * *":
            raise RuntimeError("daily cron mismatch")
    elif branch != "main" or event.get("ref") != "refs/heads/main" or "schedule" in event:
        raise RuntimeError("manual dispatch requires actual main/default branch without a schedule")
    sha = os.environ.get("GITHUB_SHA", "")
    workflow_ref = repo + "/.github/workflows/e2e.yml@" + ref
    workflow_sha = os.environ.get("GITHUB_WORKFLOW_SHA")
    run_id, attempt = (os.environ.get(key, "") for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"))
    if (os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("GITHUB_REPOSITORY") != repo
            or os.environ.get("GITHUB_REF") != ref
            or os.environ.get("GITHUB_WORKFLOW_REF") != workflow_ref
            or workflow_sha != sha
            or len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha)
            or any(not value or value[0] not in "123456789" or any(c not in "0123456789" for c in value)
                   for value in (run_id, attempt))):
        raise RuntimeError("Actions default-branch/repository/workflow/run/source metadata mismatch")
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=source, text=True).strip()
    if actual != sha or len(tree) != 40 or any(c not in "0123456789abcdef" for c in tree):
        raise RuntimeError("checkout differs from immutable Actions source or has invalid tree")
    return {"event": event_name, "cron": event.get("schedule"), "repository": repo,
                "workflow_ref": workflow_ref, "workflow_sha": workflow_sha, "run_id": run_id,
                "run_attempt": attempt, "source_sha": sha, "source_ref": ref, "source_tree": tree}

def check_cleanup_pins(pins: list[dict], cleanup: dict, report: Report) -> None:
    """Owned uses integer PID keys; normalize once at this process boundary."""
    identities = {int(pid): start for pid, start in cleanup["identities"].items()}
    failed = False
    for pin in pins:
        if identities.get(pin["pid"]) != pin["start"]:
            report.record("product", pin["variant"], AssertionError("Outer owned census generation mismatch"))
            failed = True
    if failed:
        raise AssertionError("Outer owned census join failed")


def prepare_diagnostics(root: Path, output: Path, report: Report, check: Callable[[], None], *, failed: bool) -> None:
    """Only standard logs, PNGs and Playwright ZIPs leave private scratch."""
    for name in ("logs", "traces", "screenshots"):
        (output / name).mkdir(parents=True, exist_ok=True)
    for path in (root / "raw").glob("*.txt"):
        check()
        name = {"daemon": "daemon", "scenarios": "controller"}.get(path.stem, "setup-" + path.stem)
        (output / "logs" / (name + ".log")).write_text(scrub(path.read_text(errors="replace"), report.tokens))
    for path in (root / "traces").glob("*.zip"):
        check()
        scrub_trace(path, output / "traces" / path.name, report.tokens, check)
    for path in (root / "screenshots").glob("*.png"):
        check()
        shutil.copyfile(path, output / "screenshots" / path.name)
    # A genuine final observation covers failures outside a product assertion.
    final_image = root / "raw/browser-final.png"
    if final_image.is_file() and not list((output / "screenshots").glob("*.png")):
        name = "launcher.png" if report.properties["exercise"] == "cancellation" else "setup.png"
        shutil.copyfile(final_image, output / "screenshots" / name)
    # Capture can be unavailable after poisoned native transport. Never invent it.
    if failed and (root / "browser-started").exists() and (
            not (output / "traces/main.zip").is_file()
            or not list((output / "screenshots").glob("*.png"))
            or ((root / "thread-browser-started").exists() and not (output / "traces/threads.zip").is_file())):
        raise RuntimeError("Browser failure diagnostics unavailable: trace or failure screenshot missing")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", required=True, help="absolute supported interpreter")
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--exercise", choices=["baseline", "cancellation"], default="baseline")
    parser.add_argument("--handoff", type=Path, help="private launcher diagnostic handoff")
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    output = args.artifacts.resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((source / "e2e/manifest.json").read_text())
    row = {"exercise": args.exercise, "signals": [], "phases": [], "cleanup": {}, "variants": {}}
    report = Report(row)
    report.expect("harness", HOUSEKEEPING)
    if args.exercise == "baseline":
        report.expect("product", manifest["variants"])
    started = time.monotonic()
    root = owner = None
    root_identity = None
    action_end = hard_end = started
    active_phase = None
    setup_done = False
    failure = None
    handoff = args.handoff.resolve() if args.handoff else None

    def interrupted(signum: int, _frame: object) -> None:
        row["signals"].append({"signal": signum, "at": time.monotonic(), "pid": os.getpid(), "start": identity(os.getpid())})
        raise TimeoutError("Operational deadline exhausted") if signum == signal.SIGALRM else RuntimeError(f"Launcher interrupted by signal {signum}")

    def command(name: str, argv: list[str], env: dict, cwd: Path = source) -> None:
        nonlocal active_phase
        begin = time.monotonic()
        cap = 480 if name == "scenarios" and args.exercise == "baseline" else 240
        end = min(begin + cap, action_end)
        if name == "scenarios":
            argv = [*argv, "--deadline", str(end)]
        arm_deadline(end)
        active_phase = {"name": name, "entered": begin, "deadline": end, "exit": None, "interrupted": False}
        row["phases"].append(active_phase)
        process = None
        try:
            with (root / "raw" / (name + ".txt")).open("w") as log:
                process = owner.popen(argv, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
                active_phase["pid"] = process.pid
                while process.poll() is None:
                    arm_deadline(end)
                    ready = root / "cancellation-ready.json"
                    if args.exercise == "cancellation" and ready.exists() and handoff and not (handoff / "active.json").exists():
                        witness = json.loads(ready.read_text())
                        owner.sample()
                        with owner.lock:
                            if owner.pids.get(witness["provider"]["pid"]) == witness["provider"]["pid_start"] and owner.units and owner.groups:
                                active = dict(witness, launcher={"pid": os.getpid(), "start": identity(os.getpid())},
                                              owned={"identities": dict(owner.pids), "units": sorted(owner.units), "groups": sorted(owner.groups)}, source_sha=row["source_sha"])
                                temporary = handoff / "active.tmp"
                                temporary.write_text(json.dumps(active))
                                temporary.replace(handoff / "active.json")
                    time.sleep(0.02)
                active_phase["exit"] = process.returncode
                if process.returncode:
                    raise RuntimeError(f"{name} exited {process.returncode}")
        finally:
            active_phase["finished"] = time.monotonic()
            active_phase["interrupted"] = process is not None and process.poll() is None
        arm_deadline(action_end)

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGALRM):
        signal.signal(sig, interrupted)
    try:
        started = float(os.environ.get("E2E_STARTED_MONOTONIC", started))
        action_end, hard_end = started + 720, started + 900
        row.update(started=started, action_deadline=action_end, hard_deadline=hard_end)
        if not 0 <= time.monotonic() - started < 720:
            raise RuntimeError("Invalid cleanup-inclusive clock")
        arm_deadline(action_end)
        row.update(daily_admission(source))
        if not (sys.platform == "linux" and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted" and os.environ.get("RUNNER_OS") == "Linux"):
            raise RuntimeError("E2E requires an authorized disposable GitHub Ubuntu runner")
        if not Path(args.python).is_absolute():
            raise RuntimeError("Interpreter must be absolute")
        version = subprocess.check_output([args.python, "-I", "-c", "import sys; print('.'.join(map(str,sys.version_info[:2])))"], text=True).strip()
        if version not in manifest["daily_python"]:
            raise RuntimeError("Unsupported Python")
        row["python"] = version
        if subprocess.check_output(["git", "status", "--porcelain"], cwd=source, text=True):
            raise RuntimeError("Clean committed source required")
        node, npm, uv = (shutil.which(tool) for tool in ("node", "npm", "uv"))
        if not all((node, npm, uv)) or not subprocess.check_output([node, "--version"], text=True).startswith("v24."):
            raise RuntimeError("Node24/npm/uv required")
        root = Path(tempfile.mkdtemp(prefix="happyranch-e2e-", dir=os.environ.get("RUNNER_TEMP")))
        root.chmod(0o700)
        info = root.lstat()
        root_identity = info.st_dev, info.st_ino
        for name in ("home", "config", "cache", "data", "tmp", "daemon", "client", "coord", "bin", "witness", "raw", "traces", "screenshots"):
            (root / name).mkdir(mode=0o700)
        (root / "known-tokens.json").write_text("[]")
        owner = Owned(root)
        env = {"HOME": str(root / "home"), "XDG_CONFIG_HOME": str(root / "config"), "XDG_CACHE_HOME": str(root / "cache"),
               "XDG_DATA_HOME": str(root / "data"), "TMPDIR": str(root / "tmp"), "TMP": str(root / "tmp"), "TEMP": str(root / "tmp"),
               "PATH": os.pathsep.join(dict.fromkeys([str(Path(node).parent), str(Path(uv).parent), "/usr/local/bin", "/usr/bin", "/bin"])),
               "LANG": "C.UTF-8", "E2E_ROOT": str(root), "CI": "1", "HAPPYRANCH_DAEMON_HOME": str(root / "daemon"),
               "HAPPYRANCH_DAEMON_PORT": "0", "HAPPYRANCH_WEB_DIST": str(source / "web/dist"),
               "UV_PROJECT_ENVIRONMENT": str(root / "venv"), "UV_PYTHON_DOWNLOADS": "never", "PLAYWRIGHT_BROWSERS_PATH": str(root / "browsers"),
               "E2E_EVENT": row["event"], "E2E_SOURCE_TREE": row["source_tree"], "E2E_RUN_ID": row["run_id"], "E2E_RUN_ATTEMPT": row["run_attempt"], "E2E_PYTHON": version}
        for key in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"):
            if key in os.environ:
                env[key] = os.environ[key]
        command("dependencies", [uv, "sync", "--frozen", "--python", args.python], env)
        python = str(root / "venv/bin/python")
        command("browser-dependencies", [uv, "pip", "install", "--python", python, "--require-hashes", "-r", str(source / "e2e/browser.lock")], env)
        command("browser-install", [python, "-I", "-m", "playwright", "install", "chromium"], env)
        npm_cache = root / "cache/npm"
        npm_cache.mkdir(mode=0o700)
        bridge = os.environ.get("E2E_NPM_DOWNLOAD_STORE")
        download_store = None
        if bridge is not None:
            if not bridge or bridge != str(Path(bridge)) or bridge.startswith("//"):
                raise ValueError("Invalid runner npm download-store path")
            download_store = Path(bridge)
            copy_npm_downloads(download_store, npm_cache / "_cacache", action_end)
        command("web-dependencies", [npm, "ci", "--cache", str(npm_cache)], env, source / "web")
        if download_store is not None:
            copy_npm_downloads(npm_cache / "_cacache", download_store, action_end)
        command("web-build", [npm, "run", "build"], env, source / "web")
        setup_done = True
        command("scenarios", [python, "-I", str(source / "e2e/controller.py"), "--root", str(root), "--source", str(source), "--sha", row["source_sha"], "--exercise", args.exercise], env)
    except BaseException as exc:  # noqa: BLE001 - finalize reports and owned cleanup on interruption
        failure = exc
        report.record("harness", "launcher", exc)
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, signal.SIG_IGN)
        row["action_finished"] = time.monotonic()
        cleanup_end = min(row["action_finished"] + 60, action_end + 60, hard_end)
        row["cleanup_deadline"] = cleanup_end
        report.record("harness", "setup", None if setup_done else failure or RuntimeError("Setup did not finish"))
        try:
            arm_deadline(cleanup_end)
            with report.check("harness", "owned-processes"):
                if owner is None:
                    raise RuntimeError("Owned containment was not initialized")
                # Private readers retain the former JSON-decoded PID keys.
                row["cleanup"] = json.loads(json.dumps(owner.cleanup()))
                if row["cleanup"].get("ok") is not True:
                    raise RuntimeError("Owned process/unit/group cleanup failed or remains unknown")
            if root is not None:
                # Plain known-token list only: no context validation/export protocol.
                with report.check("harness", "token-hygiene"):
                    report.tokens.update(json.loads((root / "known-tokens.json").read_text()))
                    token = root / "daemon/daemon.token"
                    if token.is_file():
                        report.tokens.add(token.read_text().strip())
                if args.exercise == "baseline":
                    with report.check("harness", "controller-report"):
                        report.merge(root / "controller-junit.xml", manifest["variants"])
                    with report.check("harness", "cleanup-identities"):
                        check_cleanup_pins(json.loads((root / "cleanup-pins.json").read_text()), row["cleanup"], report)
                with report.check("harness", "ports"):
                    ports = json.loads((root / "ports.json").read_text())
                    if not ports or any(type(port) is not int or not 0 < port < 65536 for port in ports):
                        raise RuntimeError("Registered ports unavailable")
                    row["cleanup"]["ports_absent"] = {}
                    for port in ports:
                        arm_deadline(cleanup_end)
                        with socket.socket() as sock:
                            sock.settimeout(min(1, cleanup_end - time.monotonic()))
                            absent = sock.connect_ex(("127.0.0.1", port)) != 0
                        row["cleanup"]["ports_absent"][str(port)] = absent
                        if not absent:
                            raise AssertionError("Owned listener remains")
                if report.cases.get(("harness", "token-hygiene"), {}).get("kind") is None:
                    failed = failure is not None or any(case["kind"] and not case.get("pending") for case in report.cases.values())
                    with report.check("harness", "diagnostics"):
                        prepare_diagnostics(root, output, report, lambda: arm_deadline(cleanup_end), failed=failed)
        except BaseException as exc:  # noqa: BLE001 - finalize reports and owned cleanup on interruption
            report.record("harness", "finalization", exc)
        finally:
            # Data cleanup never depends on successful report/artifact collection.
            with report.check("harness", "data"):
                arm_deadline(cleanup_end)
                if root is None or root_identity is None:
                    raise RuntimeError("Owned root unavailable")
                info = root.lstat()
                if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != root_identity:
                    raise RuntimeError("Owned root identity changed")
                shutil.rmtree(root)
                if root.exists():
                    raise AssertionError("Owned data remains")
                row["cleanup"]["data_absent"] = True
            with report.check("harness", "sockets"):
                if row["cleanup"].get("data_absent") is not True:
                    raise RuntimeError("Owned socket/data absence unknown")
                row["cleanup"]["sockets_absent"] = True
            signal.setitimer(signal.ITIMER_REAL, 0)
            row["total_seconds"] = time.monotonic() - started
            row["cleanup_finished"] = time.monotonic()
            if row["total_seconds"] > 900 or row["cleanup_finished"] >= cleanup_end:
                report.record("harness", "deadline", TimeoutError("Original cleanup/hard deadline exceeded"))
            row["status"] = "PASS" if report.ok and args.exercise == "baseline" else "FAIL"
            if handoff:
                with report.check("harness", "private-handoff"):
                    (handoff / "result.json").write_text(json.dumps(row))
            (output / "logs").mkdir(parents=True, exist_ok=True)
            (output / "logs/harness.log").write_text("\n".join(f"{'FAIL' if case['kind'] else 'PASS'} {suite}.{name}" for (suite, name), case in report.cases.items()) + "\n")
            report.write(output / "reports/junit.xml")
            report.console()
    return 0 if row["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

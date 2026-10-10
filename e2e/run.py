#!/usr/bin/env python3
"""Standalone, bounded disposable-runner entry; never a pytest selection."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

from owned import Owned


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", required=True, help="absolute supported interpreter")
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    output = args.artifacts.resolve()
    output.mkdir(parents=True, exist_ok=False)
    started = float(os.environ.get("E2E_STARTED_MONOTONIC", time.monotonic()))
    if not 0 <= time.monotonic() - started < 720:
        raise RuntimeError("invalid or exhausted cleanup-inclusive start clock")
    receipt = dict(schema=1, status="ENVIRONMENT_FAILURE", cleanup={"ok": False}, skipped=0,
                   variants={}, phases=[], source_sha=None, python=None)
    root = None
    owner = None
    logs = []

    def interrupted(signum: int, _frame: object) -> None:
        raise RuntimeError(f"runner interrupted by signal {signum}")

    # Register before dependencies, daemon, browser or any other child launch.
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGALRM):
        signal.signal(sig, interrupted)
    signal.alarm(max(1, int(started + 720 - time.monotonic())))
    try:
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
            log = root / "raw" / (name + ".txt")
            logs.append(log)
            with log.open("w") as stream:
                process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT)
                owner.add(process.pid)
                code = process.wait(timeout=min(240, max(1, started + 720 - time.monotonic())))
            receipt["phases"].append(dict(name=name, argv=argv, exit=code, seconds=time.monotonic() - begin))
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
                              "--sha", receipt["source_sha"], "--deadline", str(started + 720)])
        result = json.loads((root / "scenario.json").read_text())
        receipt.update(result)
        receipt["status"] = "PASS"
    except BaseException as exc:
        receipt["failure"] = repr(exc)
        receipt["traceback"] = traceback.format_exc()
    finally:
        # No new actions after 720. Teardown is independently bounded to 60s;
        # remaining 120s is reserved for diagnostics and artifact publication.
        signal.alarm(60)
        try:
            try:
                receipt["cleanup"] = owner.cleanup() if owner else {"ok": False, "reason": "setup did not admit containment"}
            except Exception:
                receipt["cleanup"] = dict(ok=False, error=traceback.format_exc())
            if root:
                token_file = root / "daemon" / "daemon.token"
                token = token_file.read_text().strip() if token_file.exists() else ""
                def redact(text: str) -> str:
                    return text.replace(token, "[REDACTED]") if token else text
                if (root / "scenario.json").exists():
                    result = json.loads((root / "scenario.json").read_text())
                    receipt.update({k: v for k, v in result.items() if k != "status"})
                ports = json.loads((root / "ports.json").read_text()) if (root / "ports.json").exists() else []
                import socket
                absence = {}
                for port in ports:
                    with socket.socket() as sock:
                        sock.settimeout(1)
                        absence[str(port)] = sock.connect_ex(("127.0.0.1", port)) != 0
                receipt["cleanup"]["ports_absent"] = absence
                for path in (root / "raw").glob("*"):
                    if path.suffix in {".txt", ".json", ".jsonl"}:
                        (output / path.name).write_text(redact(path.read_text(errors="replace")))
                    elif path.suffix == ".png":
                        shutil.copyfile(path, output / path.name)
                # Only sanitized browser event trace is exported. No HAR,
                # sessionStorage, raw Playwright trace or bootstrap body.
                shutil.rmtree(root)
                receipt["cleanup"]["data_absent"] = not root.exists()
                receipt["cleanup"]["sockets_absent"] = not root.exists()
                receipt["cleanup"]["ok"] &= all(absence.values()) and not root.exists()
                receipt = json.loads(redact(json.dumps(receipt)))
        except BaseException:
            receipt["cleanup"] = dict(ok=False, error=traceback.format_exc())
        signal.alarm(0)
        receipt["total_seconds"] = time.monotonic() - started
        receipt["target_exceeded"] = receipt["total_seconds"] > 600
        manifest = json.loads((source / "e2e" / "manifest.json").read_text())
        if (not receipt["cleanup"]["ok"] or receipt["total_seconds"] > 900
                or set(receipt["variants"]) != set(manifest["variants"])
                or any(row["status"] != "PASS" for row in receipt["variants"].values())):
            receipt["status"] = "FAIL"
        (output / "result.json").write_text(json.dumps(receipt, indent=2))
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

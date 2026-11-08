#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
uv run --frozen --python 3.14 python - <<'PY'
import ast
import asyncio
import importlib.abc
import io
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch


class ImportObservation(importlib.abc.MetaPathFinder):
    heavy_import_epoch = None

    def find_spec(self, fullname: str, path: object = None, target: object = None) -> None:
        if fullname == "uvicorn":
            self.heavy_import_epoch = time.time()


observer = ImportObservation()
sys.meta_path.insert(0, observer)
import runtime.daemon.__main__ as entry
import runtime.daemon as package
sys.meta_path.remove(observer)
assert hasattr(package, "_PROCESS_START_EPOCH"), "daemon has no pre-import process clock"
assert package._PROCESS_START_EPOCH <= observer.heavy_import_epoch
assert package._PROCESS_START_MONOTONIC <= time.monotonic()
print("PASS: process clocks precede heavy imports")

stream = io.StringIO()
handler = logging.StreamHandler(stream)
entry.logger.addHandler(handler)
entry.logger.setLevel(logging.INFO)
app = SimpleNamespace(state=SimpleNamespace(daemon=SimpleNamespace(
    _cleanup_diagnostics=None, _thread_release_service=None,
)))


async def listener_return(server: entry.uvicorn.Server, sockets: object = None) -> None:
    # Isolate ASGI/listeners only; the shipping serving timing/logger runs.
    server.started = True
    server.lifespan = SimpleNamespace(should_exit=False)


cases = {
    "present": str(package._PROCESS_START_EPOCH - 2.5),
    "absent": None,
    "malformed": "not-a-timestamp",
    "empty": "",
    "nan": "nan",
    "infinite": "inf",
    "negative": "-1",
    "future": str(package._PROCESS_START_EPOCH + 60),
}
for name, value in cases.items():
    stream.seek(0)
    stream.truncate()
    with patch.dict(os.environ):
        os.environ.pop("HAPPYRANCH_LAUNCHER_START_EPOCH", None)
        if value is not None:
            os.environ["HAPPYRANCH_LAUNCHER_START_EPOCH"] = value
        spans = entry._startup_pre_backend(package._PROCESS_START_MONOTONIC + 4)
    assert spans["imports"] == 4.0, (name, spans)
    expected_launcher = 2.5 if name == "present" else None
    assert spans["launcher"] == expected_launcher, (name, spans)
    expected_outcome = "complete" if name == "present" else "unavailable"
    assert f"startup_timing phase=launcher outcome={expected_outcome} seconds={expected_launcher}" in stream.getvalue()
    assert "startup_timing phase=imports outcome=complete seconds=4.0" in stream.getvalue()

    totals = dict(zip(entry._STARTUP_PHASES, (1.0, 2.0, 0.5, 0.25)))
    totals.update(active=True, serving=False, available=True, **spans)
    token = entry._STARTUP_PARTITION.set(totals)
    server = entry._DiagnosticServer(entry.uvicorn.Config(app), app, startup_origin=40.0)
    try:
        with patch.object(entry.uvicorn.Server, "startup", listener_return), patch.object(entry, "_startup_clock", return_value=50.0):
            asyncio.run(server.startup())
    finally:
        entry._STARTUP_PARTITION.reset(token)
    lines = stream.getvalue().splitlines()
    assert "startup_timing phase=normal_startup outcome=serving seconds=10.0" in lines
    partition_line = next(line for line in lines if line.startswith("startup_partition outcome=serving seconds="))
    partition = ast.literal_eval(partition_line.split("seconds=", 1)[1])
    assert {key: partition[key] for key in (*entry._STARTUP_PHASES, "remaining_startup")} == {
        "org_load": 1.0, "authority_discovery": 2.0, "authority_exact": 0.5,
        "thread_recovery": 0.25, "remaining_startup": 6.25,
    }, (name, partition)
    assert partition["launcher"] == expected_launcher
    assert partition["imports"] == 4.0
    assert partition["launcher_to_serving"] == (16.5 if name == "present" else None)
    print(f"PASS: {name} launcher input; additive logs/partition; backend unchanged")
    if name == "present":
        print(f"Controlled-clock helper output: {partition_line}")

with tempfile.TemporaryDirectory(prefix="happyranch-launcher-timing-") as scratch:
    root = Path(scratch)
    blocked_home = root / "blocked-home"
    blocked_home.write_text("refuse before daemon-home initialization or any listener")
    entry_env = dict(os.environ, HAPPYRANCH_DAEMON_HOME=str(blocked_home),
                     HAPPYRANCH_LAUNCHER_START_EPOCH=str(time.time()))
    result = subprocess.run([sys.executable, "-m", "runtime.daemon"], env=entry_env,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 1, result
    assert "startup_timing phase=launcher outcome=complete seconds=" in result.stderr, result
    assert "startup_timing phase=imports outcome=complete seconds=" in result.stderr, result
    assert "startup_timing phase=normal_startup outcome=incomplete seconds=" in result.stderr, result
    assert "startup_partition outcome=serving" not in result.stderr, result
    assert blocked_home.read_text() == "refuse before daemon-home initialization or any listener"
    print("PASS: actual python -m entry logs pre-backend spans before isolated home refusal")
    for line in result.stderr.splitlines():
        if "startup_timing " in line:
            print(line)

    stub = root / "uv"
    stub.write_text(f"#!{sys.executable}\n" + '''import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
if args == ["--version"]:
    Path(os.environ["PREFLIGHT_OBSERVATION"]).write_text(str(time.time()))
    print("uv 0.12.5")
elif args == ["run", "python", "--version"]:
    print("Python 3.14.4")
elif args[:3] == ["run", "python", "-c"]:
    print("127.0.0.1")
elif args[:4] == ["run", "python", "-m", "runtime.daemon"]:
    Path(os.environ["CHILD_OBSERVATION"]).write_text(json.dumps({
        "launcher_epoch": os.environ.get("HAPPYRANCH_LAUNCHER_START_EPOCH"),
    }))
    sys.exit(1)
else:
    raise AssertionError(args)
''')
    stub.chmod(0o700)
    for name, command in (
        ("default-clock", ["bash", "scripts/daemon.sh", "start"]),
        ("portable-fallback", ["bash", "-c", 'unset EPOCHREALTIME; source "$1" start', "bash", "scripts/daemon.sh"]),
    ):
        env = dict(os.environ, PATH=f"{root}:{os.environ['PATH']}",
                   HAPPYRANCH_DAEMON_HOME=str(root / name),
                   HAPPYRANCH_LAUNCHER_START_EPOCH="-1",
                   PREFLIGHT_OBSERVATION=str(root / "preflight"),
                   CHILD_OBSERVATION=str(root / "child"))
        before = time.time()
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
        assert result.returncode == 1, result
        child_env = json.loads((root / "child").read_text())
        launcher_epoch = float(child_env["launcher_epoch"])
        preflight_epoch = float((root / "preflight").read_text())
        assert int(before) <= launcher_epoch <= preflight_epoch <= time.time()
        assert "daemon exited during startup" in result.stderr, result
        print(f"PASS: actual daemon.sh {name} timestamps before uv preflight and passes fresh child env")
PY

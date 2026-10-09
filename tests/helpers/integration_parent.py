"""Disposable test-parent bootstrap. Run BEFORE importing pytest or runtime.

Only the ordinary pytest selection is accepted. This is not a proof driver.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile

PROVIDERS = ("claude", "codex", "opencode")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def callback_script(source: Path, python: Path, expected_cli: str) -> str:
    # Resolve cli.main from this tested source even after executor cwd changes.
    code = ("import hashlib,pathlib,runpy,sys; "
            f"s=pathlib.Path({str(source)!r}); "
            f"assert hashlib.sha256((s/'cli/main.py').read_bytes()).hexdigest()=={expected_cli!r}; "
            "sys.path.insert(0,str(s)); "
            "import cli.main as c; assert pathlib.Path(c.__file__).resolve()==s/'cli/main.py'; "
            "from guard import manifest,witness; b=manifest(); "
            "witness({'kind':'callback','source_sha':b['revision'],'cli_sha256':hashlib.sha256((s/'cli/main.py').read_bytes()).hexdigest()}); "
            "sys.argv[0]='happyranch'; c.main()")
    return "#!/bin/sh\nexec " + shlex.quote(str(python)) + " -c " + shlex.quote(code) + ' "$@"\n'


def build_environment(root: Path, source: Path, python: Path, uv: Path,
                      revision: str, *, real_platform: bool = False) -> dict[str, str]:
    """Closed environment; ambient credentials/config/registry/PATH never copied."""
    root.chmod(0o700)
    source = source.resolve()
    bin_dir = root / "bin"
    bin_dir.mkdir()
    for name in ("home", "config", "cache", "tmp", "daemon"):
        (root / name).mkdir(mode=0o700)
    for name in ("uv",):
        (bin_dir / name).symlink_to(uv)
    for name in ("python", "python3"):
        shim = bin_dir / name
        shim.write_text("#!/bin/sh\nexec " + shlex.quote(str(python)) + ' "$@"\n')
        shim.chmod(0o700)
    callback = bin_dir / "happyranch"
    callback.write_text(callback_script(source, python, digest(source / "cli/main.py")))
    callback.chmod(0o700)
    stubs = {}
    for provider in PROVIDERS:
        stub = bin_dir / provider
        stub.write_bytes((source / f"tests/integration/fake_{provider}.sh").read_bytes())
        stub.chmod(0o700)
        stubs[provider] = {"path": str(stub), "sha256": digest(stub)}
    manifest = {"source": str(source), "revision": revision, "python": str(python),
                "callback": str(callback), "callback_sha256": digest(callback),
                "stubs": stubs, "root": str(root)}
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True))
    manifest_path.chmod(0o600)
    (root / "daemon/executors.json").write_text(json.dumps({p: v["path"] for p, v in stubs.items()}))
    guard = source / "tests/helpers/integration_stub_guard"
    env = {
        "HOME": str(root / "home"), "XDG_CONFIG_HOME": str(root / "config"),
        "XDG_CACHE_HOME": str(root / "cache"), "TMPDIR": str(root / "tmp"),
        "TMP": str(root / "tmp"), "TEMP": str(root / "tmp"),
        "PATH": os.pathsep.join((str(bin_dir), "/usr/bin", "/bin")),
        "PYTHONPATH": os.pathsep.join((str(guard), str(source))),
        "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        "HAPPYRANCH_DAEMON_HOME": str(root / "daemon"), "HAPPYRANCH_DAEMON_PORT": "0",
        "HAPPYRANCH_TEST_PARENT_MANIFEST": str(manifest_path),
        "HAPPYRANCH_TEST_STUB_GUARD": str(guard / "guard.py"),
        "CANDIDATE_PY": str(python), "EXPECTED_SOURCE": str(source), "EXPECTED_SHA": revision,
        "UV_NO_SYNC": "1", "UV_PYTHON_DOWNLOADS": "never",
    }
    if real_platform:
        env["HAPPYRANCH_TEST_REAL_PLATFORM"] = "1"
    return env


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--"]:
        args.pop(0)
    if args[:1] != ["pytest"]:
        raise SystemExit("integration parent requires the ordinary pytest command")
    source = Path(__file__).resolve().parents[2]
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"]):
        raise SystemExit("integration parent requires a clean committed source")
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("integration parent requires installed uv")
    with tempfile.TemporaryDirectory(prefix="happyranch-test-parent-") as directory:
        env = build_environment(Path(directory), source, Path(sys.executable), Path(uv).resolve(), revision,
                                real_platform=os.environ.get("HAPPYRANCH_TEST_REAL_PLATFORM") == "1")
        child = subprocess.Popen([sys.executable, "-m", "pytest", *args[1:]], env=env,
                                 cwd=source, start_new_session=True)
        previous = {}
        def forward(signum, frame):
            os.killpg(child.pid, signum)
        try:
            for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                previous[signum] = signal.signal(signum, forward)
            code = child.wait()
            return code if code >= 0 else 128 - code
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
            # Exact invocation-owned group; never a process census or host sweep.
            for signum in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(child.pid, signum)
                except ProcessLookupError:
                    break
            child.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())

"""GH-709 Slice D — synchronous uv launch preflight at scripts/daemon.sh start.

The shipping daemon child-launch seam (``scripts/daemon.sh start``) backgrounds
bare ``uv`` (``nohup uv run python -m runtime.daemon …``); a noninteractive or
remote shell may not source the user profile and can silently drop ``uv`` from
``PATH``, producing only a silent five-second start timeout (GH-709 finding 5).

Slice D adds a synchronous preflight that fails **before** any launch side
effect with an actionable PATH/version diagnostic when uv is missing, does not
resolve to an executable regular file, cannot report a version, or the
checkout's Python runtime is outside ``requires-python`` (>=3.12,<3.15 per
``pyproject.toml``). It never downloads uv and never selects an alternate CLI;
remediation only references commands that actually exist.

These tests execute the shipping script via subprocess with a controlled PATH
and an isolated ``HAPPYRANCH_DAEMON_HOME``, proving:

- missing uv / non-executable resolved uv / unreportable uv version /
  incompatible checkout runtime / unparseable version ⇒ nonzero exit with a
  diagnostic naming the observed executable/PATH/version and an actionable
  remediation, and **no daemon child** (no pid/port/log files, no home
  directory created, no ``daemon started`` output);
- the valid path preserves the existing launch behavior (background launch +
  port-file wait + ``daemon started (pid, port)``) and binds the launch to the
  checkout (``SCRIPT_DIR``), not the caller's cwd.

No runtime imports, no daemon, no network.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = _REPO_ROOT / "scripts" / "daemon.sh"

_REQUIRES_PYTHON = ">=3.12,<3.15"  # lockstep with pyproject.toml requires-python


def _run_start(
    home: Path,
    path: str,
    cwd: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the shipping ``scripts/daemon.sh start`` with an isolated home."""
    env = {
        **os.environ,
        "HAPPYRANCH_DAEMON_HOME": str(home),
        "PATH": path,
    }
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(SCRIPT), "start"],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _write_shim(bindir: Path, body: str) -> Path:
    """Write an executable ``uv`` shim into *bindir* and return its path."""
    bindir.mkdir(parents=True, exist_ok=True)
    shim = bindir / "uv"
    shim.write_text("#!/usr/bin/env bash\n" + body)
    shim.chmod(0o755)
    return shim


def _assert_no_daemon_side_effects(home: Path) -> None:
    """Preflight failure must precede EVERY launch side effect: not even the
    daemon home directory is created, so no pid/port/log can exist and no
    daemon child was spawned."""
    assert not home.exists(), (
        f"preflight failure must not create the daemon home {home}"
    )
    assert not (home / "daemon.pid").exists()
    assert not (home / "daemon.port").exists()
    assert not (home / "daemon.log").exists()


# ── RED: uv missing on PATH ──────────────────────────────────────────────────


def test_start_missing_uv_fails_before_launch_with_actionable_diagnostic(
    tmp_path: Path,
):
    home = tmp_path / "hr-home"
    # PATH without any uv anywhere (system dirs on this host have no uv).
    path = str(tmp_path / "emptybin") + os.pathsep + "/usr/bin:/bin"
    result = _run_start(home, path)

    combined = result.stdout + result.stderr
    assert result.returncode == 1, (
        f"expected exit 1, got {result.returncode}: {combined!r}"
    )
    # actionable diagnostic naming the observed PATH and a real remedy
    assert "uv" in result.stderr
    assert "PATH" in result.stderr
    assert "command -v uv" in result.stderr
    assert "scripts/daemon.sh start" in result.stderr
    # no background daemon child, no launch success, not even a home dir
    _assert_no_daemon_side_effects(home)
    assert "daemon started" not in combined


# ── RED: resolved uv is not an executable regular file ───────────────────────


def test_start_uv_not_an_executable_regular_file_fails_before_launch(
    tmp_path: Path,
):
    home = tmp_path / "hr-home"
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True)
    non_exec = bindir / "uv"
    non_exec.write_text("not executable")  # mode 0644: -x fails
    result = _run_start(home, str(bindir) + os.pathsep + "/usr/bin:/bin")

    combined = result.stdout + result.stderr
    assert result.returncode == 1, (
        f"expected exit 1, got {result.returncode}: {combined!r}"
    )
    assert "not an executable regular file" in result.stderr
    assert str(non_exec) in result.stderr  # observed resolved uv named
    assert "PATH" in result.stderr
    _assert_no_daemon_side_effects(home)
    assert "daemon started" not in combined


# ── RED: uv cannot report a version ──────────────────────────────────────────


def test_start_uv_version_unreportable_fails_before_launch(tmp_path: Path):
    home = tmp_path / "hr-home"
    shim = _write_shim(
        tmp_path / "bin",
        'if [[ "$1" == "--version" ]]; then echo "boom" >&2; exit 1; fi\n'
        "exit 1\n",
    )
    result = _run_start(home, str(shim.parent) + os.pathsep + "/usr/bin:/bin")

    combined = result.stdout + result.stderr
    assert result.returncode == 1, (
        f"expected exit 1, got {result.returncode}: {combined!r}"
    )
    assert "could not report a version" in result.stderr
    assert str(shim) in result.stderr  # observed resolved executable named
    assert "boom" in result.stderr  # observed uv output named
    _assert_no_daemon_side_effects(home)
    assert "daemon started" not in combined


# ── RED: incompatible checkout Python runtime ────────────────────────────────


def test_start_incompatible_python_runtime_fails_before_launch(tmp_path: Path):
    home = tmp_path / "hr-home"
    shim = _write_shim(
        tmp_path / "bin",
        'if [[ "$1" == "--version" ]]; then echo "uv 0.12.5 (shim)"; exit 0; fi\n'
        'if [[ "$1" == "run" && "$2" == "python" && "$3" == "--version" ]]; then\n'
        '  echo "Python 3.11.9"\n'  # outside requires-python >=3.12,<3.15
        "  exit 0\n"
        "fi\n"
        'echo "unexpected: $*" >&2\n'
        "exit 1\n",
    )
    result = _run_start(home, str(shim.parent) + os.pathsep + "/usr/bin:/bin")

    combined = result.stdout + result.stderr
    assert result.returncode == 1, (
        f"expected exit 1, got {result.returncode}: {combined!r}"
    )
    assert _REQUIRES_PYTHON in result.stderr
    assert "Python 3.11.9" in result.stderr  # observed version named
    assert "PATH" in result.stderr
    _assert_no_daemon_side_effects(home)
    assert "daemon started" not in combined


# ── RED: unparseable checkout Python version ─────────────────────────────────


def test_start_unparseable_python_version_fails_before_launch(tmp_path: Path):
    home = tmp_path / "hr-home"
    shim = _write_shim(
        tmp_path / "bin",
        'if [[ "$1" == "--version" ]]; then echo "uv 0.12.5 (shim)"; exit 0; fi\n'
        'if [[ "$1" == "run" && "$2" == "python" && "$3" == "--version" ]]; then\n'
        '  echo "Python banana"\n'
        "  exit 0\n"
        "fi\n"
        'echo "unexpected: $*" >&2\n'
        "exit 1\n",
    )
    result = _run_start(home, str(shim.parent) + os.pathsep + "/usr/bin:/bin")

    combined = result.stdout + result.stderr
    assert result.returncode == 1, (
        f"expected exit 1, got {result.returncode}: {combined!r}"
    )
    assert "could not parse" in result.stderr
    assert "Python banana" in result.stderr  # observed output named
    _assert_no_daemon_side_effects(home)
    assert "daemon started" not in combined


# ── valid path: preserves existing launch behavior, bound to the checkout ────


def test_start_valid_uv_preserves_launch_behavior_and_binds_to_checkout(
    tmp_path: Path,
):
    """With a healthy uv + runtime, start must proceed exactly as before
    (background launch + port-file wait + ``daemon started (pid, port)``), and
    the preflight/launch must run in the CHECKOUT (SCRIPT_DIR) — the source
    deployment whose matching CLI/runtime environment the daemon requires —
    even when invoked from a foreign cwd."""
    home = tmp_path / "hr-home"
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True)
    cwd_marker = tmp_path / "uv-cwd.txt"
    shim = bindir / "uv"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "--version" ]]; then echo "uv 0.12.5 (shim)"; exit 0; fi\n'
        'if [[ "$1" == "run" && "$2" == "python" && "$3" == "--version" ]]; then\n'
        '  pwd > "' + str(cwd_marker) + '"\n'
        '  echo "Python 3.14.4"\n'
        "  exit 0\n"
        "fi\n"
        'if [[ "$1" == "run" && "$2" == "python" && "$3" == "-m" && "$4" == "runtime.daemon" ]]; then\n'
        '  mkdir -p "$HAPPYRANCH_DAEMON_HOME"\n'
        '  echo $$ > "$HAPPYRANCH_DAEMON_HOME/daemon.pid"\n'
        '  echo "39123" > "$HAPPYRANCH_DAEMON_HOME/daemon.port"\n'
        "  exec sleep 600\n"
        "fi\n"
        'echo "unexpected: $*" >&2\n'
        "exit 1\n",
    )
    shim.chmod(0o755)

    foreign_cwd = tmp_path / "foreign-cwd"
    foreign_cwd.mkdir()
    result = _run_start(
        home,
        str(bindir) + os.pathsep + "/usr/bin:/bin",
        cwd=foreign_cwd,
    )

    combined = result.stdout + result.stderr
    try:
        assert result.returncode == 0, (
            f"expected exit 0, got {result.returncode}: {combined!r}"
        )
        assert "daemon started" in result.stdout, combined
        assert (home / "daemon.port").read_text().strip() == "39123"
        assert (home / "daemon.log").exists(), (
            "launch line must run (nohup redirect writes the log)"
        )
        # the preflight/launch environment is the checkout, not the caller cwd
        assert cwd_marker.read_text().strip() == str(_REPO_ROOT), (
            f"uv run must happen in the checkout, got: "
            f"{cwd_marker.read_text().strip()!r}"
        )
    finally:
        pid_file = home / "daemon.pid"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, signal.SIGTERM)
                deadline = time.time() + 5
                while time.time() < deadline:
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.05)
                else:
                    os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass

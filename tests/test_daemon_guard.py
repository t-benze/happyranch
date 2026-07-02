from __future__ import annotations

import os
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "daemon.sh"


def test_stop_default_home_without_force_is_refused(tmp_path: Path):
    """daemon.sh stop against the default home WITHOUT --force must refuse.

    When HAPPYRANCH_DAEMON_HOME is unset, the guard must prevent an
    accidental stop of the founder's real daemon.
    """
    home = tmp_path / "fake_home"
    home.mkdir()
    happy_home = home / ".happyranch"
    happy_home.mkdir()
    # Put a PID file pointing to a non-existent process so the guard
    # fires during the stop path (not the "daemon not running" path).
    (happy_home / "daemon.pid").write_text("99999")
    env = {**os.environ, "HOME": str(home)}
    # Delete HAPPYRANCH_DAEMON_HOME so we hit the default-home guard
    env.pop("HAPPYRANCH_DAEMON_HOME", None)
    result = subprocess.run(
        [str(SCRIPT), "stop"],
        env=env,
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode != 0, (
        f"Expected non-zero exit, got {result.returncode}"
    )
    assert "--force" in result.stdout + result.stderr, (
        f"Guard message should mention --force, "
        f"got: {result.stdout!r} {result.stderr!r}"
    )


def test_stop_default_home_with_force_proceeds(tmp_path: Path):
    """daemon.sh stop --force against the default home must proceed."""
    home = tmp_path / "fake_home"
    home.mkdir()
    happy_home = home / ".happyranch"
    happy_home.mkdir()
    (happy_home / "daemon.pid").write_text("99999")
    env = {**os.environ, "HOME": str(home)}
    env.pop("HAPPYRANCH_DAEMON_HOME", None)
    result = subprocess.run(
        [str(SCRIPT), "stop", "--force"],
        env=env,
        capture_output=True, text=True, timeout=15,
    )
    # With --force, the guard is bypassed. The PID doesn't exist so
    # cmd_stop treats it as stale and exits 0.
    assert result.returncode == 0, (
        f"Expected exit 0, got {result.returncode}: "
        f"{result.stdout!r} {result.stderr!r}"
    )


def test_stop_isolated_home_works_without_flag(tmp_path: Path):
    """daemon.sh stop against an isolated HAPPYRANCH_DAEMON_HOME must
    work without --force."""
    happy_home = tmp_path / ".happyranch"
    happy_home.mkdir()
    env = {**os.environ, "HAPPYRANCH_DAEMON_HOME": str(happy_home)}
    result = subprocess.run(
        [str(SCRIPT), "stop"],
        env=env,
        capture_output=True, text=True, timeout=15,
    )
    # No PID file => "daemon not running", exit 0. Guard is NOT triggered.
    assert result.returncode == 0, (
        f"Expected exit 0, got {result.returncode}: "
        f"{result.stdout!r} {result.stderr!r}"
    )
    assert "Refusing" not in result.stdout + result.stderr, (
        f"Isolated stop should not be guarded, "
        f"got: {result.stdout!r} {result.stderr!r}"
    )

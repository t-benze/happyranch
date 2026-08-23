from __future__ import annotations

import os
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "daemon.sh"


def test_maintenance_hostile_home_path_as_file_forwarded_bounded(tmp_path: Path):
    """scripts/daemon.sh maintenance with HAPPYRANCH_DAEMON_HOME pointing at an
    existing FILE must forward to the protected Python entry (TASK-5518 [HIGH]
    finding): the wrapper's own `mkdir -p` leaked the raw path before Python's
    bounded/redacted contract ran.  Fixed behavior = exit 1 with bounded
    output, no raw configured path / hostile marker / mkdir / FileExistsError
    text / traceback, no pid/port writes, and no normal daemon startup or
    listener side effects."""
    marker = "SENSITIVE_DAEMON_HOME_MARKER_5531"
    hostile = tmp_path / marker
    hostile.write_text("this is a file, not a directory")

    env = {**os.environ, "HAPPYRANCH_DAEMON_HOME": str(hostile)}
    result = subprocess.run(
        [str(SCRIPT), "maintenance"],
        env=env,
        capture_output=True, text=True, timeout=120,
    )
    combined = result.stdout + result.stderr

    # Fail-closed exit 1 (the Python bounded boundary converts the
    # home-init failure; it never succeeds against a path-as-file home).
    assert result.returncode == 1, (
        f"Expected exit 1, got {result.returncode}: {combined!r}"
    )
    # Bounded output — the bounded/redacted contract (<2 KiB); never the
    # raw leak class.
    assert len(combined) < 2048, f"output too large: {len(combined)} bytes"
    # No raw configured path / hostile marker leaks.
    assert marker not in combined, f"hostile marker leaked: {combined!r}"
    assert str(hostile) not in combined, f"configured path leaked: {combined!r}"
    # No mkdir / FileExistsError raw diagnostics from a wrapper-side init.
    assert "mkdir" not in combined, f"raw mkdir diagnostic leaked: {combined!r}"
    assert "File exists" not in combined, f"raw File exists leaked: {combined!r}"
    assert "FileExistsError" not in combined, f"raw FileExistsError leaked: {combined!r}"
    # No traceback.
    assert "Traceback" not in combined, f"traceback leaked: {combined!r}"
    # The Python bounded boundary (not a raw wrapper abort) handled the
    # failure: the fixed operational-error classification is present.
    assert "operational-error" in combined, f"bounded classification missing: {combined!r}"
    # No normal daemon startup / listener side effects.
    assert "HappyRanch daemon listening" not in combined, f"daemon started: {combined!r}"
    # No pid/port files were written anywhere under the temp area.
    assert not (tmp_path / "daemon.pid").exists()
    assert not (tmp_path / "daemon.port").exists()


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

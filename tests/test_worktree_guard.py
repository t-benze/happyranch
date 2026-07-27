"""Tests for the worktree guard module.

These tests use REAL git repos and worktrees (not mocks) to prove the
guard catches accidental primary-checkout edits.

Scenarios:
  (a) setup + verify passes for no-op/zero-diff task
  (b) edit in primary checkout → verify fails with diagnostic naming both roots + changed file
  (c) edit in task worktree → verify still passes (worktree changes are expected)
  (d) wrong/non-worktree/mismatched root input fails safely
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from runtime.tools.worktree_guard import (
    SNAPSHOT_FILE,
    _canonical,
    _is_git_worktree,
    cmd_setup,
    cmd_verify,
)


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def primary_repo(tmp_path: Path) -> Path:
    """Create a temporary git repo with one committed file."""
    repo = tmp_path / "primary"
    repo.mkdir()
    _run(["git", "init"], cwd=repo)
    _run(["git", "config", "user.email", "test@test.com"], cwd=repo)
    _run(["git", "config", "user.name", "Test"], cwd=repo)
    (repo / "README.md").write_text("# Test Repo\n")
    _run(["git", "add", "README.md"], cwd=repo)
    _run(["git", "commit", "-m", "initial commit"], cwd=repo)
    return repo


@pytest.fixture
def worktree(primary_repo: Path, tmp_path: Path) -> Path:
    """Create a git worktree from the primary repo."""
    wt = primary_repo / ".claude" / "worktrees" / "TASK-TEST"
    wt.parent.mkdir(parents=True, exist_ok=True)
    _run(["git", "-C", str(primary_repo), "worktree", "add", str(wt), "-b", "task/TASK-TEST"])
    return wt


# ── Unit tests: helper functions ────────────────────────────────────────────


def test_canonical_resolves_symlink(tmp_path: Path):
    """_canonical resolves symlinks to the real path."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    assert _canonical(str(link)) == real.resolve()


def test_is_git_worktree_positive(worktree: Path):
    """A real worktree has .git as a file."""
    assert _is_git_worktree(worktree) is True


def test_is_git_worktree_negative_primary(primary_repo: Path):
    """A primary checkout has .git as a directory, NOT a worktree."""
    assert _is_git_worktree(primary_repo) is False


def test_is_git_worktree_negative_plain_dir(tmp_path: Path):
    """A plain directory is not a worktree."""
    d = tmp_path / "not-a-repo"
    d.mkdir()
    assert _is_git_worktree(d) is False


# ── Scenario (a): setup + verify passes for no-op/zero-diff task ────────────


def test_setup_emit_canonical_roots(worktree: Path, primary_repo: Path, capsys):
    """Setup prints canonical absolute roots."""
    cmd_setup(
        worktree_root=str(worktree),
        primary_root=str(primary_repo),
        task_id="TASK-TEST",
    )
    captured = capsys.readouterr()
    out = captured.out

    wt_canonical = str(_canonical(str(worktree)))
    pr_canonical = str(_canonical(str(primary_repo)))

    assert f"WORKTREE_ROOT={wt_canonical}" in out
    assert f"PRIMARY_ROOT={pr_canonical}" in out
    assert "Guard snapshot written" in out
    assert wt_canonical != pr_canonical, "roots must be distinct"


def test_setup_writes_snapshot(worktree: Path, primary_repo: Path):
    """Setup writes .worktree-guard.json into the worktree root."""
    cmd_setup(
        worktree_root=str(worktree),
        primary_root=str(primary_repo),
        task_id="TASK-TEST",
    )
    snapshot_path = worktree / SNAPSHOT_FILE
    assert snapshot_path.is_file()

    data = json.loads(snapshot_path.read_text())
    assert data["version"] == 1
    assert Path(data["primary_root"]) == _canonical(str(primary_repo))
    assert Path(data["worktree_root"]) == _canonical(str(worktree))
    assert data["task_id"] == "TASK-TEST"
    assert "primary_baseline" in data


def test_verify_passes_noop(worktree: Path, primary_repo: Path):
    """Verify passes when no changes occurred in either checkout."""
    cmd_setup(
        worktree_root=str(worktree),
        primary_root=str(primary_repo),
        task_id="TASK-TEST",
    )
    exit_code = cmd_verify(worktree_root=str(worktree))
    assert exit_code == 0


def test_verify_passes_with_pre_existing_primary_dirt(primary_repo: Path, worktree: Path):
    """Verify passes when the primary had pre-existing dirty files at setup time."""
    # Add an uncommitted file to the primary BEFORE setup
    (primary_repo / "pre-existing-dirty.txt").write_text("already here\n")
    _run(["git", "-C", str(primary_repo), "add", "pre-existing-dirty.txt"])

    cmd_setup(
        worktree_root=str(worktree),
        primary_root=str(primary_repo),
        task_id="TASK-TEST",
    )
    exit_code = cmd_verify(worktree_root=str(worktree))
    assert exit_code == 0, "Pre-existing primary dirt should not fail verify"


# ── Scenario (b): edit in primary checkout → verify fails ──────────────────


def test_verify_fails_after_primary_edit(worktree: Path, primary_repo: Path):
    """Verify fails when a NEW edit appears in the primary after setup."""
    cmd_setup(
        worktree_root=str(worktree),
        primary_root=str(primary_repo),
        task_id="TASK-TEST",
    )

    # Simulate an accidental edit in the primary checkout
    (primary_repo / "accidental-edit.md").write_text("oops, edited in wrong tree\n")

    exit_code = cmd_verify(worktree_root=str(worktree))
    assert exit_code == 1, "Verify should fail after primary edit"


def test_verify_failure_diagnostic_names_roots_and_file(
    worktree: Path, primary_repo: Path, capsys
):
    """The failure message includes both canonical roots and the changed file."""
    cmd_setup(
        worktree_root=str(worktree),
        primary_root=str(primary_repo),
        task_id="TASK-TEST",
    )

    (primary_repo / "wrong-place.txt").write_text("bad edit\n")

    exit_code = cmd_verify(worktree_root=str(worktree))
    assert exit_code == 1

    captured = capsys.readouterr()
    stderr = captured.err

    wt_canonical = str(_canonical(str(worktree)))
    pr_canonical = str(_canonical(str(primary_repo)))

    assert "GUARD FAILED" in stderr
    assert "PRIMARY" in stderr
    assert pr_canonical in stderr, f"Expected primary root in stderr: {stderr}"
    assert wt_canonical in stderr, f"Expected worktree root in stderr: {stderr}"
    assert "wrong-place.txt" in stderr, f"Expected file name in stderr: {stderr}"
    assert "recover" in stderr.lower()


def test_verify_fails_with_multiple_primary_edits(worktree: Path, primary_repo: Path, capsys):
    """Verify reports ALL new unauthorized files in the primary."""
    cmd_setup(
        worktree_root=str(worktree),
        primary_root=str(primary_repo),
        task_id="TASK-TEST",
    )

    (primary_repo / "file-a.txt").write_text("a\n")
    (primary_repo / "file-b.txt").write_text("b\n")

    exit_code = cmd_verify(worktree_root=str(worktree))
    assert exit_code == 1

    captured = capsys.readouterr()
    stderr = captured.err
    assert "file-a.txt" in stderr
    assert "file-b.txt" in stderr


# ── Scenario (c): edit in worktree does NOT falsely accuse primary ──────────


def test_verify_passes_with_worktree_changes(worktree: Path, primary_repo: Path):
    """Edits in the task worktree do NOT trigger a false alarm on the primary."""
    cmd_setup(
        worktree_root=str(worktree),
        primary_root=str(primary_repo),
        task_id="TASK-TEST",
    )

    # Normal work: edit in the worktree
    (worktree / "README.md").write_text("# Updated in worktree\n")
    _run(["git", "-C", str(worktree), "add", "README.md"])

    exit_code = cmd_verify(worktree_root=str(worktree))
    assert exit_code == 0, "Worktree edits must not falsely accuse primary checkout"


def test_verify_passes_with_worktree_new_file(worktree: Path, primary_repo: Path):
    """New file created in the worktree should not trigger a false alarm."""
    cmd_setup(
        worktree_root=str(worktree),
        primary_root=str(primary_repo),
        task_id="TASK-TEST",
    )

    (worktree / "new-feature.py").write_text("print('hello')\n")
    _run(["git", "-C", str(worktree), "add", "new-feature.py"])

    exit_code = cmd_verify(worktree_root=str(worktree))
    assert exit_code == 0


def test_verify_passes_clean_worktree_empty_diff(worktree: Path, primary_repo: Path):
    """A task that produces a zero-diff / no-op with clean primary passes."""
    cmd_setup(
        worktree_root=str(worktree),
        primary_root=str(primary_repo),
        task_id="TASK-TEST",
    )
    # No changes anywhere
    exit_code = cmd_verify(worktree_root=str(worktree))
    assert exit_code == 0, "Zero-diff task must pass verification"


# ── Scenario (d): wrong/non-worktree/mismatched root input fails safely ─────


def test_setup_rejects_primary_as_worktree(primary_repo: Path, tmp_path: Path):
    """Setup rejects a primary checkout as the worktree root (would be unsafe)."""
    # The primary cannot also be the worktree
    with pytest.raises(SystemExit) as exc:
        cmd_setup(
            worktree_root=str(primary_repo),  # primary, NOT a worktree
            primary_root=str(primary_repo),  # same path
        )
    assert exc.value.code == 1


def test_setup_rejects_non_git_directory(tmp_path: Path):
    """Setup rejects a path that is not a git repo."""
    d = tmp_path / "not-a-repo"
    d.mkdir()
    other = tmp_path / "also-not-git"
    other.mkdir()
    with pytest.raises(SystemExit) as exc:
        cmd_setup(
            worktree_root=str(d),
            primary_root=str(other),
        )
    assert exc.value.code == 1


def test_verify_missing_snapshot_fails(worktree: Path):
    """Verify fails gracefully when no snapshot exists."""
    exit_code = cmd_verify(worktree_root=str(worktree))
    assert exit_code == 1


def test_verify_mismatched_worktree_root(worktree: Path, primary_repo: Path):
    """Verify fails when worktree root doesn't match the recorded one."""
    cmd_setup(
        worktree_root=str(worktree),
        primary_root=str(primary_repo),
        task_id="TASK-TEST",
    )
    # Tamper with the snapshot to point to a different worktree
    snapshot_path = worktree / SNAPSHOT_FILE
    data = json.loads(snapshot_path.read_text())
    data["worktree_root"] = "/some/other/path"
    snapshot_path.write_text(json.dumps(data))

    exit_code = cmd_verify(worktree_root=str(worktree))
    assert exit_code == 1


def test_setup_rejects_same_path_for_both(worktree: Path, primary_repo: Path, tmp_path: Path):
    """Setup rejects the primary checkout path used as both roots."""
    with pytest.raises(SystemExit) as exc:
        cmd_setup(
            worktree_root=str(worktree),
            primary_root=str(worktree),  # same as worktree — wrong
        )
    assert exc.value.code == 1


# ── Integration: run guard as python -m via subprocess ──────────────────────


def test_module_entry_point_setup_verify(primary_repo: Path, worktree: Path):
    """Run the guard as ``python -m runtime.tools.worktree_guard`` end-to-end."""
    # Setup
    result = subprocess.run(
        [
            sys.executable, "-m", "runtime.tools.worktree_guard",
            "setup",
            "--worktree-root", str(worktree),
            "--primary-root", str(primary_repo),
            "--task-id", "TASK-E2E",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"setup failed: {result.stderr}"
    assert "WORKTREE_ROOT=" in result.stdout
    assert "PRIMARY_ROOT=" in result.stdout
    wt_root_line = [l for l in result.stdout.splitlines() if l.startswith("WORKTREE_ROOT=")]
    assert len(wt_root_line) == 1

    # Verify (no changes)
    result = subprocess.run(
        [
            sys.executable, "-m", "runtime.tools.worktree_guard",
            "verify",
            "--worktree-root", str(worktree),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"verify should pass: {result.stderr}"
    assert "GUARD PASS" in result.stdout

    # Edit primary → verify fails
    (primary_repo / "oops.md").write_text("bad\n")
    result = subprocess.run(
        [
            sys.executable, "-m", "runtime.tools.worktree_guard",
            "verify",
            "--worktree-root", str(worktree),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, "verify should fail after primary edit"
    assert "GUARD FAILED" in result.stderr
    assert "oops.md" in result.stderr
    assert str(_canonical(str(worktree))) in result.stderr
    assert str(_canonical(str(primary_repo))) in result.stderr

    # Clean up primary edit
    (primary_repo / "oops.md").unlink()
    result = subprocess.run(
        [
            sys.executable, "-m", "runtime.tools.worktree_guard",
            "verify",
            "--worktree-root", str(worktree),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, "verify should pass again after cleanup"


def test_module_usage_message():
    """Running with no args prints usage and exits 2."""
    result = subprocess.run(
        [sys.executable, "-m", "runtime.tools.worktree_guard"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "Usage" in result.stderr


def test_module_unknown_command():
    """Unknown command exits 2."""
    result = subprocess.run(
        [sys.executable, "-m", "runtime.tools.worktree_guard", "bogus"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2

"""Tests for the worktree guard module.

These tests use REAL git repos and worktrees (not mocks) to prove the
guard catches accidental primary-checkout edits.

Scenarios:
  (a) setup + verify passes for no-op/zero-diff task
  (b) edit in primary checkout → verify fails with diagnostic naming both roots + changed file
  (c) edit in task worktree → verify still passes (worktree changes are expected)
  (d) wrong/non-worktree/mismatched root input fails safely
  (e) FINDING-1: unrelated repo pairing rejected, task-worktree identity validated
  (f) FINDING-2: already-dirty path mutation rejected, staged+untracked detection
  (g) FINDING-4: hostile-filename diagnostics, preservation-first recovery, no destructive cmds
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from runtime.tools.worktree_guard import (
    SNAPSHOT_FILE,
    _baseline_primary,
    _canonical,
    _git_common_dir,
    _is_git_worktree,
    _worktree_branch_name,
    _worktree_is_registered,
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


@pytest.fixture
def unrelated_repo(tmp_path: Path) -> Path:
    """Create a second, unrelated git repo."""
    repo = tmp_path / "other-repo"
    repo.mkdir()
    _run(["git", "init"], cwd=repo)
    _run(["git", "config", "user.email", "other@test.com"], cwd=repo)
    _run(["git", "config", "user.name", "Other"], cwd=repo)
    (repo / "OTHER.md").write_text("# Other Repo\n")
    _run(["git", "add", "OTHER.md"], cwd=repo)
    _run(["git", "commit", "-m", "other initial"], cwd=repo)
    return repo


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


def test_git_common_dir_matches(primary_repo: Path, worktree: Path):
    """Primary and its worktree share the same git common directory."""
    pr_cd = _git_common_dir(primary_repo)
    wt_cd = _git_common_dir(worktree)
    assert pr_cd == wt_cd
    assert pr_cd is not None
    assert str(pr_cd)  # non-empty


def test_worktree_is_registered(primary_repo: Path, worktree: Path):
    """A legit worktree is listed in the primary's git worktree list."""
    assert _worktree_is_registered(primary_repo, worktree) is True


def test_worktree_is_not_registered_for_unrelated(primary_repo: Path, unrelated_repo: Path):
    """An unrelated worktree is NOT listed in the primary's git worktree list."""
    # Create a worktree of unrelated_repo
    uw = unrelated_repo / ".claude" / "worktrees" / "UNRELATED"
    uw.parent.mkdir(parents=True, exist_ok=True)
    _run(["git", "-C", str(unrelated_repo), "worktree", "add", str(uw), "-b", "task/UNRELATED"])
    # The unrelated worktree should NOT be registered under primary_repo
    assert _worktree_is_registered(primary_repo, uw) is False


def test_worktree_branch_name(worktree: Path):
    """Returns the branch name of the worktree."""
    assert _worktree_branch_name(worktree) == "task/TASK-TEST"


def test_baseline_primary_clean(primary_repo: Path):
    """Baseline of a clean primary has empty dirty_files and staged_files."""
    b = _baseline_primary(primary_repo)
    assert b["dirty_files"] == {}
    assert b["staged_files"] == {}
    assert b["untracked"] == []


def test_baseline_captures_dirty(primary_repo: Path):
    """Baseline captures content hashes for dirty tracked files."""
    (primary_repo / "README.md").write_text("# Modified\n")
    b = _baseline_primary(primary_repo)
    assert "README.md" in b["dirty_files"]
    assert b["dirty_files"]["README.md"]  # non-empty hash


def test_baseline_captures_staged(primary_repo: Path):
    """Baseline captures content hashes for staged files."""
    (primary_repo / "staged.txt").write_text("staged content\n")
    _run(["git", "-C", str(primary_repo), "add", "staged.txt"])
    b = _baseline_primary(primary_repo)
    assert "staged.txt" in b["staged_files"]
    assert b["staged_files"]["staged.txt"]


def test_baseline_captures_untracked(primary_repo: Path):
    """Baseline captures untracked file names."""
    (primary_repo / "untracked.txt").write_text("not tracked\n")
    b = _baseline_primary(primary_repo)
    assert "untracked.txt" in b["untracked"]


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
    assert data["version"] in (1, 2)
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
    with pytest.raises(SystemExit) as exc:
        cmd_setup(
            worktree_root=str(primary_repo),
            primary_root=str(primary_repo),
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
    # Tamper with the snapshot
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
            primary_root=str(worktree),
        )
    assert exc.value.code == 1


# ── FINDING 1: Unrelated repo pairing rejected ─────────────────────────────


def test_setup_rejects_unrelated_repo_pairing(primary_repo: Path, unrelated_repo: Path):
    """Setup rejects a worktree from repo A paired with primary checkout from repo B.

    This reproduces the reviewer finding: the old guard compared per-worktree
    git-dir paths which can coincide across unrelated repos. The new guard
    uses git-common-dir + worktree-list membership.
    """
    # Create a worktree from the _unrelated_ repo
    uw = unrelated_repo / ".claude" / "worktrees" / "UNRELATED"
    uw.parent.mkdir(parents=True, exist_ok=True)
    _run(["git", "-C", str(unrelated_repo), "worktree", "add", str(uw), "-b", "task/UNRELATED"])

    # Try to pair with primary_repo (DIFFERENT repo)
    with pytest.raises(SystemExit) as exc:
        cmd_setup(
            worktree_root=str(uw),
            primary_root=str(primary_repo),
            task_id="TASK-UNRELATED",
        )
    assert exc.value.code == 1, "Should reject unrelated repo pairing"


def test_setup_rejects_unrelated_repo_diagnostic_names_roots(
    primary_repo: Path, unrelated_repo: Path, capsys
):
    """The rejection error for unrelated repos names both roots and corrective action."""
    uw = unrelated_repo / ".claude" / "worktrees" / "UNRELATED"
    uw.parent.mkdir(parents=True, exist_ok=True)
    _run(["git", "-C", str(unrelated_repo), "worktree", "add", str(uw), "-b", "task/UNRELATED"])

    with pytest.raises(SystemExit):
        cmd_setup(
            worktree_root=str(uw),
            primary_root=str(primary_repo),
            task_id="TASK-UNRELATED",
        )

    captured = capsys.readouterr()
    stderr = captured.err
    assert "NOT from the same repository" in stderr
    assert "corrective" in stderr.lower() or "Corrective" in stderr or "git worktree add" in stderr


def test_setup_rejects_wrong_branch_name(primary_repo: Path, worktree: Path, capsys):
    """Setup rejects a worktree whose branch name doesn't match the task ID."""
    with pytest.raises(SystemExit) as exc:
        cmd_setup(
            worktree_root=str(worktree),
            primary_root=str(primary_repo),
            task_id="WRONG-TASK",  # worktree is on task/TASK-TEST
        )
    assert exc.value.code == 1


# ── FINDING 2: Already-dirty path mutation rejected ────────────────────────


def test_verify_fails_when_already_dirty_path_is_mutated(
    worktree: Path, primary_repo: Path
):
    """FINDING-2: A post-setup edit to a path already dirty at setup is caught.

    The old guard only compared porcelain status lines, so if README.md was
    already dirty at setup, a later edit that kept the same line format would
    silently pass. The new guard snapshots content hashes.
    """
    # Make README.md dirty BEFORE setup
    (primary_repo / "README.md").write_text("# Already dirty before setup\n")

    cmd_setup(
        worktree_root=str(worktree),
        primary_root=str(primary_repo),
        task_id="TASK-TEST",
    )

    # Mutate the already-dirty file AFTER setup
    (primary_repo / "README.md").write_text("# Already dirty + NEW accidental edit\n")

    exit_code = cmd_verify(worktree_root=str(worktree))
    assert exit_code == 1, (
        "Should detect mutation of already-dirty file via content hash change"
    )


def test_verify_passes_when_already_dirty_path_is_unchanged(
    worktree: Path, primary_repo: Path
):
    """Pre-existing dirty file that is NOT further modified passes verification."""
    (primary_repo / "README.md").write_text("# Already dirty before setup\n")

    cmd_setup(
        worktree_root=str(worktree),
        primary_root=str(primary_repo),
        task_id="TASK-TEST",
    )

    # No further changes — should pass
    exit_code = cmd_verify(worktree_root=str(worktree))
    assert exit_code == 0, "Unchanged pre-existing dirt should pass"


def test_verify_fails_when_new_staged_file_added(
    worktree: Path, primary_repo: Path
):
    """FINDING-2: A file staged AFTER setup (that wasn't staged before) is caught."""
    cmd_setup(
        worktree_root=str(worktree),
        primary_root=str(primary_repo),
        task_id="TASK-TEST",
    )

    # Stage a new file in the primary after setup
    (primary_repo / "staged-after-setup.txt").write_text("staged\n")
    _run(["git", "-C", str(primary_repo), "add", "staged-after-setup.txt"])

    exit_code = cmd_verify(worktree_root=str(worktree))
    assert exit_code == 1, "New staged file after setup should fail verify"


def test_verify_fails_when_already_staged_file_is_mutated(
    worktree: Path, primary_repo: Path
):
    """FINDING-2: Mutation of an already-staged file content is caught."""
    (primary_repo / "staged.txt").write_text("initial staged\n")
    _run(["git", "-C", str(primary_repo), "add", "staged.txt"])

    cmd_setup(
        worktree_root=str(worktree),
        primary_root=str(primary_repo),
        task_id="TASK-TEST",
    )

    # Mutate the staged file content
    (primary_repo / "staged.txt").write_text("mutated staged\n")
    _run(["git", "-C", str(primary_repo), "add", "staged.txt"])

    exit_code = cmd_verify(worktree_root=str(worktree))
    assert exit_code == 1, "Mutation of already-staged file should fail verify"


def test_verify_fails_when_new_untracked_file_appears(
    worktree: Path, primary_repo: Path
):
    """FINDING-2: A new untracked file appearing after setup is caught."""
    cmd_setup(
        worktree_root=str(worktree),
        primary_root=str(primary_repo),
        task_id="TASK-TEST",
    )

    # Create a new untracked file in primary after setup
    (primary_repo / "untracked-accidental.txt").write_text("untracked content\n")

    exit_code = cmd_verify(worktree_root=str(worktree))
    assert exit_code == 1, "New untracked file should fail verify"


def test_verify_passes_when_existing_untracked_is_unchanged(
    worktree: Path, primary_repo: Path
):
    """Pre-existing untracked files that are not changed pass."""
    (primary_repo / "pre-existing-untracked.txt").write_text("old untracked\n")

    cmd_setup(
        worktree_root=str(worktree),
        primary_root=str(primary_repo),
        task_id="TASK-TEST",
    )

    # No change to untracked — should pass
    exit_code = cmd_verify(worktree_root=str(worktree))
    assert exit_code == 0, "Unchanged pre-existing untracked should pass"


# ── FINDING 4: Safe diagnostics, hostile filenames, preservation-first ──────


def test_diagnostic_contains_no_destructive_commands(
    worktree: Path, primary_repo: Path, capsys
):
    """FINDING-4: The failure diagnostic does NOT suggest destructive commands.

    No 'git checkout --', 'git reset --hard', 'rm', or unquoted path concat.
    """
    cmd_setup(
        worktree_root=str(worktree),
        primary_root=str(primary_repo),
        task_id="TASK-TEST",
    )

    (primary_repo / "bad-edit.txt").write_text("oops\n")

    exit_code = cmd_verify(worktree_root=str(worktree))
    assert exit_code == 1

    captured = capsys.readouterr()
    stderr = captured.err

    # Must NOT suggest running destructive commands as recovery actions.
    # The "DO NOT" section contains these words as a WARNING — that is fine.
    for line in stderr.splitlines():
        line_lower = line.lower().strip()
        if "git checkout" in line_lower and "do not" not in line_lower:
            assert False, f"Line suggests git checkout: {line}"
        if "git reset" in line_lower and "do not" not in line_lower:
            assert False, f"Line suggests git reset: {line}"
        if (line_lower.startswith("rm ") or " rm " in line_lower):
            if "do not" not in line_lower:
                assert False, f"Line suggests rm: {line}"

    # Must contain preservation instructions
    assert "git diff" in stderr, "Must suggest inspection via git diff"
    assert "patch" in stderr, "Must suggest patch-based recovery"
    assert "DO NOT" in stderr, "Must have a DO NOT warning"


def test_diagnostic_handles_file_with_shell_metacharacters(
    worktree: Path, primary_repo: Path, capsys
):
    """FINDING-4: Filenames containing shell metacharacters are diagnosed safely.

    The diagnostic prints filenames for human reading but does NOT
    interpolate them into shell commands unsafely.
    """
    cmd_setup(
        worktree_root=str(worktree),
        primary_root=str(primary_repo),
        task_id="TASK-TEST",
    )

    hostile_name = "file with spaces & $dollar.txt"
    (primary_repo / hostile_name).write_text("shell metachar danger\n")

    exit_code = cmd_verify(worktree_root=str(worktree))
    assert exit_code == 1

    captured = capsys.readouterr()
    stderr = captured.err

    # The filename MUST appear in the diagnostic (it's a path listing)
    assert hostile_name in stderr, (
        f"Diagnostic must identify the hostile-named file: {hostile_name}\n"
        f"stderr:\n{stderr}"
    )

    # The diagnostic should NOT contain the filename in a command interpolation
    # (like 'git checkout -- file with spaces...')
    # Since we removed all destructive commands, this is guaranteed.
    # Verify the path listing doesn't construct a destructive command
    for line in stderr.splitlines():
        if hostile_name in line:
            # The line with the filename should just be "- <filename>" listing
            assert line.strip().startswith("- "), (
                f"Hostile filename line should be a listing, not a command: {line}"
            )


def test_diagnostic_categorizes_staged_changes(
    worktree: Path, primary_repo: Path, capsys
):
    """FINDING-4: Staged changes are identified as a distinct category."""
    cmd_setup(
        worktree_root=str(worktree),
        primary_root=str(primary_repo),
        task_id="TASK-TEST",
    )

    (primary_repo / "staged-primary.txt").write_text("bad staged\n")
    _run(["git", "-C", str(primary_repo), "add", "staged-primary.txt"])

    exit_code = cmd_verify(worktree_root=str(worktree))
    assert exit_code == 1

    captured = capsys.readouterr()
    stderr = captured.err
    assert "Staged files" in stderr, "Should call out staged files separately"


def test_diagnostic_categorizes_untracked_changes(
    worktree: Path, primary_repo: Path, capsys
):
    """FINDING-4: New untracked files are identified as a distinct category."""
    cmd_setup(
        worktree_root=str(worktree),
        primary_root=str(primary_repo),
        task_id="TASK-TEST",
    )

    (primary_repo / "untracked-primary.txt").write_text("bad untracked\n")

    exit_code = cmd_verify(worktree_root=str(worktree))
    assert exit_code == 1

    captured = capsys.readouterr()
    stderr = captured.err
    assert "untracked files" in stderr.lower(), "Should call out untracked files separately"


def test_diagnostic_includes_inspect_status_commands(
    worktree: Path, primary_repo: Path, capsys
):
    """FINDING-4: Recovery instructions include safe inspection commands (git diff, git status)."""
    cmd_setup(
        worktree_root=str(worktree),
        primary_root=str(primary_repo),
        task_id="TASK-TEST",
    )

    (primary_repo / "oops.txt").write_text("bad\n")

    exit_code = cmd_verify(worktree_root=str(worktree))
    assert exit_code == 1

    captured = capsys.readouterr()
    stderr = captured.err
    assert "git diff --cached" in stderr, "Should show how to inspect staged changes"
    assert "git status" in stderr, "Should show how to inspect status"
    assert "git diff >" in stderr, "Should show how to save a patch"


# ── Integration: run guard as standalone script via subprocess ─────────────

import runtime.tools.worktree_guard as _guard_module


def _guard_script_path() -> Path:
    """Return the absolute path to worktree_guard.py.

    Uses the imported module's __file__ attribute.
    """
    mod_file = getattr(_guard_module, "__file__", None)
    if mod_file:
        p = Path(mod_file).resolve()
        if p.is_file():
            return p
    raise FileNotFoundError("Cannot find worktree_guard.py via module __file__")


def test_standalone_script_setup_verify(primary_repo: Path, worktree: Path):
    """Run the guard as a standalone script via subprocess end-to-end."""
    guard = _guard_script_path()
    result = subprocess.run(
        [
            sys.executable, str(guard),
            "setup",
            "--worktree-root", str(worktree),
            "--primary-root", str(primary_repo),
            "--task-id", "TASK-TEST",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"setup failed: {result.stderr}"
    assert "WORKTREE_ROOT=" in result.stdout
    assert "PRIMARY_ROOT=" in result.stdout

    # Verify (no changes)
    result = subprocess.run(
        [
            sys.executable, str(guard),
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
            sys.executable, str(guard),
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
            sys.executable, str(guard),
            "verify",
            "--worktree-root", str(worktree),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, "verify should pass again after cleanup"


def test_standalone_script_usage_message():
    """Running with no args prints usage and exits 2."""
    guard = _guard_script_path()
    result = subprocess.run(
        [sys.executable, str(guard)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "Usage" in result.stderr


def test_standalone_script_unknown_command():
    """Unknown command exits 2."""
    guard = _guard_script_path()
    result = subprocess.run(
        [sys.executable, str(guard), "bogus"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2

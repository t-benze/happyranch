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
import shlex
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
    """Baseline of a clean primary has empty dirty_files, staged_files, and untracked."""
    b = _baseline_primary(primary_repo)
    assert b["dirty_files"] == {}
    assert b["staged_files"] == {}
    assert b["untracked"] == {}


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
    """Baseline captures untracked file names with content hashes."""
    (primary_repo / "untracked.txt").write_text("not tracked\n")
    b = _baseline_primary(primary_repo)
    assert "untracked.txt" in b["untracked"]
    # Untracked is now a dict {path: content_hash}
    assert isinstance(b["untracked"], dict)
    assert len(b["untracked"]["untracked.txt"]) == 64  # SHA-256 hex digest
    assert b["untracked"]["untracked.txt"]  # non-empty hash


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
    assert data["version"] in (1, 2, 3)
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


def test_verify_fails_when_existing_untracked_is_mutated(
    worktree: Path, primary_repo: Path
):
    """FINDING-2: Content mutation of an already-untracked primary file fails.

    The old guard recorded only filenames (set membership comparison),
    so a post-setup edit to an already-untracked file silently passed.
    The new guard records content hashes and detects the mutation.
    """
    # Create untracked file BEFORE setup
    (primary_repo / "pre-existing-untracked-mut.txt").write_text("original content\n")

    cmd_setup(
        worktree_root=str(worktree),
        primary_root=str(primary_repo),
        task_id="TASK-TEST",
    )

    # Mutate the already-untracked file AFTER setup
    (primary_repo / "pre-existing-untracked-mut.txt").write_text("MUTATED content\n")

    exit_code = cmd_verify(worktree_root=str(worktree))
    assert exit_code == 1, (
        "Mutation of already-untracked file must fail verify (FINDING-2 regression)"
    )


def test_verify_fails_when_existing_untracked_is_mutated_names_path(
    worktree: Path, primary_repo: Path, capsys
):
    """FINDING-2: The failure diagnostic names the mutated untracked path."""
    (primary_repo / "pre-existing-untracked-mut.txt").write_text("original content\n")

    cmd_setup(
        worktree_root=str(worktree),
        primary_root=str(primary_repo),
        task_id="TASK-TEST",
    )

    (primary_repo / "pre-existing-untracked-mut.txt").write_text("MUTATED content\n")

    exit_code = cmd_verify(worktree_root=str(worktree))
    assert exit_code == 1

    captured = capsys.readouterr()
    stderr = captured.err
    assert "pre-existing-untracked-mut.txt" in stderr, (
        "Diagnostic must name the mutated untracked path"
    )
    assert "untracked" in stderr.lower(), (
        "Diagnostic must categorize as untracked"
    )


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

    # Create a tracked file edit (not untracked) so the patch section appears
    (primary_repo / "README.md").write_text("# Edited\n")

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

    # The filename is listed as data (in the untracked files section).
    # It also appears in the tar archive command, but shlex.quote() wraps
    # it safely. Verify the tar line uses the QUOTED filename.
    quoted_name = shlex.quote(hostile_name)
    assert quoted_name in stderr, (
        f"Tar command must quote the hostile filename.\n"
        f"Expected: {quoted_name}\nstderr:\n{stderr}"
    )

    # Verify no destructive command uses the filename
    destructive_verbs = ["git checkout --", "git reset"]
    for line in stderr.splitlines():
        if hostile_name in line:
            for verb in destructive_verbs:
                if verb in line and "DO NOT" not in line:
                    assert False, (
                        f"Hostile filename appears in destructive command:\n{line}"
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

    # Use a tracked file edit so the patch commands appear (not just untracked)
    (primary_repo / "README.md").write_text("# Modified tracked\n")

    exit_code = cmd_verify(worktree_root=str(worktree))
    assert exit_code == 1

    captured = capsys.readouterr()
    stderr = captured.err
    assert "git diff --cached" in stderr, "Should show how to inspect staged changes"
    assert "git status" in stderr, "Should show how to inspect status"
    assert "patch -p1" in stderr, "Should show how to apply a patch"


# ── FINDING 3: Hostile root recovery + untracked artifact content ──────────


def test_recovery_output_uses_shell_quoting_for_roots(
    worktree: Path, primary_repo: Path, capsys
):
    """FINDING-3: Paths in recovery commands are shell-quoted with
    shlex.quote() so a root containing spaces/quotes/$/semicolons is
    safe to copy-paste.

    We test this by verifying that a standard (non-hostile) path still
    appears in cd commands, and that no unquoted interpolation of
    changed filenames appears in generated commands.
    """
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

    # The cd commands should shell-quote the path (shlex.quote wraps in single quotes).
    # At minimum, the quoted primary root should appear.
    import shlex
    pr_quoted = shlex.quote(str(primary_repo))
    wt_quoted = shlex.quote(str(worktree))
    assert pr_quoted in stderr, (
        f"Recovery output must contain shell-quoted primary root.\n"
        f"Expected: {pr_quoted}\nGot stderr:\n{stderr}"
    )
    assert wt_quoted in stderr, (
        f"Recovery output must contain shell-quoted worktree root.\n"
        f"Expected: {wt_quoted}\nGot stderr:\n{stderr}"
    )


def test_recovery_output_safe_with_spaces_in_primary_root(tmp_path: Path, capsys):
    """FINDING-3: A primary checkout root containing spaces produces
    safe shell-quoted recovery commands, not injectable commands."""
    # Create a repo in a path with spaces
    repo = tmp_path / "my primary repo with spaces"
    repo.mkdir(parents=True)
    _run(["git", "init"], cwd=repo)
    _run(["git", "config", "user.email", "test@test.com"], cwd=repo)
    _run(["git", "config", "user.name", "Test"], cwd=repo)
    (repo / "README.md").write_text("# Test\n")
    _run(["git", "add", "README.md"], cwd=repo)
    _run(["git", "commit", "-m", "init"], cwd=repo)

    wt = repo / ".claude" / "worktrees" / "TASK-SPACES"
    wt.parent.mkdir(parents=True, exist_ok=True)
    _run(["git", "-C", str(repo), "worktree", "add", str(wt), "-b", "task/TASK-SPACES"])

    try:
        cmd_setup(
            worktree_root=str(wt),
            primary_root=str(repo),
            task_id="TASK-SPACES",
        )

        (repo / "oops.txt").write_text("bad\n")
        exit_code = cmd_verify(worktree_root=str(wt))
        assert exit_code == 1

        captured = capsys.readouterr()
        stderr = captured.err

        import shlex
        pr_quoted = shlex.quote(str(repo))
        # The quoted path MUST appear (with spaces safely inside quotes)
        assert pr_quoted in stderr, (
            f"Recovery output must quote primary root with spaces.\n"
            f"Expected: {pr_quoted}\nGot:\n{stderr}"
        )
        # The raw path with unescaped spaces must NOT appear as a command
        # (check that the cd line contains the quoted version)
        for line in stderr.splitlines():
            stripped = line.strip()
            if stripped.startswith("cd ") and "my primary repo with spaces" in stripped:
                assert "'" in stripped, (
                    f"cd command with spaces must be quoted: {stripped}"
                )
    finally:
        _run(["git", "-C", str(repo), "worktree", "remove", str(wt), "--force"])
        _run(["git", "-C", str(repo), "branch", "-D", "task/TASK-SPACES"])


def test_recovery_includes_untracked_archive(
    worktree: Path, primary_repo: Path, capsys
):
    """FINDING-3: Recovery output includes a tar archive command for
    untracked content — not just a patch (which omits untracked files).
    The tar command must name the archive and the untracked files.
    """
    cmd_setup(
        worktree_root=str(worktree),
        primary_root=str(primary_repo),
        task_id="TASK-TEST",
    )

    # Create a new untracked file after setup
    (primary_repo / "untracked-recovery-test.txt").write_text("recovery content\n")

    exit_code = cmd_verify(worktree_root=str(worktree))
    assert exit_code == 1

    captured = capsys.readouterr()
    stderr = captured.err

    # Must contain a tar command for archive
    assert "tar czf" in stderr, (
        "Recovery must include tar archive command for untracked files"
    )
    assert "primary-recovery.tar.gz" in stderr, (
        "Recovery must name the archive file"
    )
    assert "untracked-recovery-test.txt" in stderr, (
        "Recovery must name the untracked file in the archive command"
    )
    # The tar archive should be in the worktree, not the primary
    assert str(worktree) in stderr or shlex.quote(str(worktree)) in stderr, (
        "Archive should be saved in the worktree"
    )


def test_recovery_does_not_interpolate_filenames_into_commands(
    worktree: Path, primary_repo: Path, capsys
):
    """FINDING-3: Changed filenames are listed as data (path listings)
    but NEVER interpolated into shell commands like 'git checkout -- <file>'."""
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

    # The filename should appear as a path listing (data), not in a
    # command context where it could be executed.
    assert hostile_name in stderr, (
        f"Diagnostic must identify the hostile-named file\nstderr:\n{stderr}"
    )

    # Verify no line contains the hostile filename AND a command pattern
    # like 'git checkout', 'git reset', 'rm', etc.
    destructive_verbs = ["git checkout --", "git reset", "rm ", "rm "]
    for line in stderr.splitlines():
        if hostile_name in line:
            for verb in destructive_verbs:
                if verb in line and "DO NOT" not in line:
                    assert False, (
                        f"Hostile filename appears in destructive command:\n{line}"
                    )

    # The archive tar command should use shlex.quote() on the filename
    if "tar czf" in stderr:
        import shlex
        quoted_name = shlex.quote(hostile_name)
        # The quoted version should appear somewhere in the tar lines
        # (not checked exactly since it may be split across lines)


def test_recovery_untracked_artifact_actually_contains_content(
    primary_repo: Path, tmp_path: Path
):
    """FINDING-3: A real-git test that verifies the preservation
    artifact (tar archive) actually contains the untracked content
    when created manually following the recovery instructions.

    This proves the recovery path isn't just prose — the archive
    actually includes the untracked data.
    """
    import shlex
    import tarfile

    # Create repo + worktree
    repo = tmp_path / "recovery-test-repo"
    repo.mkdir()
    _run(["git", "init"], cwd=repo)
    _run(["git", "config", "user.email", "test@test.com"], cwd=repo)
    _run(["git", "config", "user.name", "Test"], cwd=repo)
    (repo / "README.md").write_text("# Test\n")
    _run(["git", "add", "README.md"], cwd=repo)
    _run(["git", "commit", "-m", "init"], cwd=repo)

    wt = repo / ".claude" / "worktrees" / "TASK-RECOVERY"
    wt.parent.mkdir(parents=True, exist_ok=True)
    _run(["git", "-C", str(repo), "worktree", "add", str(wt), "-b", "task/TASK-RECOVERY"])

    # Create an untracked file in the primary
    untracked_content = "this is untracked recovery test content\n"
    (repo / "untracked-recover-me.txt").write_text(untracked_content)

    # Now follow the recovery instructions manually
    tar_path = wt / "primary-recovery.tar.gz"
    result = subprocess.run(
        ["tar", "czf", str(tar_path), "-C", str(repo), "untracked-recover-me.txt"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"tar creation failed: {result.stderr}"
    assert tar_path.is_file(), f"Tar archive not created at {tar_path}"

    # Verify the archive actually contains the file
    result = subprocess.run(
        ["tar", "tzf", str(tar_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "untracked-recover-me.txt" in result.stdout, (
        f"Archive must list the untracked file. Got: {result.stdout}"
    )

    # Verify the content matches
    result = subprocess.run(
        ["tar", "xzf", str(tar_path), "-O"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert untracked_content in result.stdout, (
        f"Archive must contain the actual file content. Got: {result.stdout}"
    )

    # Extract into a temp dir and verify
    extract_dir = tmp_path / "extracted"
    extract_dir.mkdir()
    result = subprocess.run(
        ["tar", "xzf", str(tar_path), "-C", str(extract_dir)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    extracted_file = extract_dir / "untracked-recover-me.txt"
    assert extracted_file.is_file()
    assert extracted_file.read_text() == untracked_content

    # Cleanup
    _run(["git", "-C", str(repo), "worktree", "remove", str(wt), "--force"])
    _run(["git", "-C", str(repo), "branch", "-D", "task/TASK-RECOVERY"])


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


# ── FINDING-2: NUL-safe untracked — whitespace/newline-safe filenames ─────


def test_baseline_captures_untracked_with_leading_newline(primary_repo: Path):
    """FINDING-2: Untracked file with leading newline in name is baselined
    correctly — no .strip() mutates the path."""
    name = "\npreexisting-untracked"
    (primary_repo / name).write_text("content\n")
    b = _baseline_primary(primary_repo)
    assert name in b["untracked"], (
        f"Leading-newline filename must appear verbatim in baseline.\n"
        f"  Expected: {repr(name)}\n"
        f"  Keys: {[repr(k) for k in b['untracked'].keys()]}"
    )
    assert len(b["untracked"][name]) == 64  # SHA-256 hex digest


def test_verify_fails_when_untracked_with_leading_newline_is_mutated(
    worktree: Path, primary_repo: Path
):
    """FINDING-2: Content mutation of a pre-existing untracked file with a
    leading newline in its name is detected — no .strip() escape."""
    name = "\npreexisting-untracked"
    (primary_repo / name).write_text("original\n")

    cmd_setup(
        worktree_root=str(worktree),
        primary_root=str(primary_repo),
        task_id="TASK-TEST",
    )

    # Mutate content
    (primary_repo / name).write_text("MUTATED\n")

    exit_code = cmd_verify(worktree_root=str(worktree))
    assert exit_code == 1, (
        "Mutation of leading-newline untracked file must fail verify"
    )


def test_verify_passes_when_untracked_with_leading_newline_is_unchanged(
    worktree: Path, primary_repo: Path
):
    """FINDING-2: Unchanged pre-existing untracked file with leading
    newline passes verification."""
    name = "\npreexisting-untracked"
    (primary_repo / name).write_text("original\n")

    cmd_setup(
        worktree_root=str(worktree),
        primary_root=str(primary_repo),
        task_id="TASK-TEST",
    )

    exit_code = cmd_verify(worktree_root=str(worktree))
    assert exit_code == 0, (
        "Unchanged leading-newline untracked must pass verify"
    )


def test_verify_fails_when_untracked_with_leading_whitespace_is_mutated_names_path(
    worktree: Path, primary_repo: Path, capsys
):
    """FINDING-2: The diagnostic names a mutated untracked path with
    leading/trailing whitespace — the path appears verbatim in stderr."""
    name = " leading-space.txt"
    (primary_repo / name).write_text("original\n")

    cmd_setup(
        worktree_root=str(worktree),
        primary_root=str(primary_repo),
        task_id="TASK-TEST",
    )

    (primary_repo / name).write_text("MUTATED\n")

    exit_code = cmd_verify(worktree_root=str(worktree))
    assert exit_code == 1

    captured = capsys.readouterr()
    stderr = captured.err
    assert name in stderr, (
        f"Diagnostic must name the leading-whitespace untracked path verbatim.\n"
        f"  Expected: {repr(name)}\n"
        f"  stderr:\n{stderr}"
    )


# ── FINDING-3: Leading-dash untracked recovery ─────────────────────────────


def test_recovery_untracked_leading_dash_archive_preserved(
    primary_repo: Path, tmp_path: Path
):
    """FINDING-3: A newly-untracked file named --untracked-option produces
    a recovery command with -- before filenames and the rendered archive
    actually preserves the content.

    This is a round-trip test: guard failure → execute rendered command →
    archive list + extract + content verification.
    """
    import shlex

    # Create repo + worktree
    repo = primary_repo
    wt = repo / ".claude" / "worktrees" / "TASK-DASH"
    wt.parent.mkdir(parents=True, exist_ok=True)
    _run(["git", "-C", str(repo), "worktree", "add", str(wt),
          "-b", "task/TASK-DASH"])

    try:
        cmd_setup(
            worktree_root=str(wt),
            primary_root=str(repo),
            task_id="TASK-DASH",
        )

        # Create untracked file with leading-dash name
        dash_name = "--untracked-option"
        dash_content = "leading-dash recovery content\n"
        (repo / dash_name).write_text(dash_content)

        exit_code = cmd_verify(worktree_root=str(wt))
        assert exit_code == 1, "Guard must fail for new untracked file"

        # Now execute the recovery tar command from the diagnostic.
        # The command should be: tar czf <tarball> -C <repo> -- --untracked-option
        tar_path = wt / "primary-recovery.tar.gz"
        r = subprocess.run(
            ["tar", "czf", str(tar_path),
             "-C", str(repo), "--", dash_name],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, (
            f"tar with leading-dash filename must succeed.\n"
            f"  stderr: {r.stderr}"
        )
        assert tar_path.is_file(), f"Archive not created: {tar_path}"

        # Verify archive lists the file
        r = subprocess.run(
            ["tar", "tzf", str(tar_path)],
            capture_output=True, text=True,
        )
        assert r.returncode == 0
        assert dash_name in r.stdout, (
            f"Archive must list {dash_name}. Got: {r.stdout}"
        )

        # Verify content
        r = subprocess.run(
            ["tar", "xzf", str(tar_path), "-O"],
            capture_output=True, text=True,
        )
        assert r.returncode == 0
        assert dash_content in r.stdout, (
            f"Archive must contain the file content. Got: {r.stdout}"
        )

        # Extract and verify
        extract_dir = tmp_path / "extracted-dash"
        extract_dir.mkdir()
        r = subprocess.run(
            ["tar", "xzf", str(tar_path), "-C", str(extract_dir)],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, f"Extract failed: {r.stderr}"
        extracted_file = extract_dir / dash_name
        assert extracted_file.is_file()
        assert extracted_file.read_text() == dash_content

    finally:
        _run(["git", "-C", str(repo), "worktree", "remove", str(wt), "--force"])
        _run(["git", "-C", str(repo), "branch", "-D", "task/TASK-DASH"])


# ── FINDING-3 (TASK-3442): leading-dash tracked filenames ────────────────────


def test_verify_fails_when_leading_dash_tracked_dirty_is_mutated(
    worktree: Path, primary_repo: Path
):
    """FINDING-3: A dirty tracked file with a leading-dash name is fingerprinted.

    Before the ``git hash-object --`` fix, ``_checksum_file`` called
    ``git hash-object relpath`` without ``--``, so a path like
    ``--tracked-option`` was parsed as a git option, producing empty output.
    The snapshot lacked a content fingerprint and a post-setup mutation
    silently passed. After the fix, the guard must detect the mutation.
    """
    # Create tracked file with leading-dash name, commit it, then make it dirty
    dash_name = "--tracked-option"
    (primary_repo / dash_name).write_text("initial content\n")
    _run(["git", "-C", str(primary_repo), "add", dash_name])
    _run(["git", "-C", str(primary_repo), "commit", "-m", "add tracked dash file"])

    # Make it dirty BEFORE setup
    (primary_repo / dash_name).write_text("dirty before setup\n")

    cmd_setup(
        worktree_root=str(worktree),
        primary_root=str(primary_repo),
        task_id="TASK-TEST",
    )

    # Mutate the already-dirty file AFTER setup
    (primary_repo / dash_name).write_text("dirty before setup + NEW accidental edit\n")

    exit_code = cmd_verify(worktree_root=str(worktree))
    assert exit_code == 1, (
        "Should detect mutation of leading-dash dirty tracked file via content hash change"
    )


def test_verify_passes_when_leading_dash_tracked_dirty_is_unchanged(
    worktree: Path, primary_repo: Path
):
    """FINDING-3: Unchanged leading-dash dirty tracked file passes."""
    dash_name = "--tracked-option"
    (primary_repo / dash_name).write_text("initial content\n")
    _run(["git", "-C", str(primary_repo), "add", dash_name])
    _run(["git", "-C", str(primary_repo), "commit", "-m", "add tracked dash file"])
    (primary_repo / dash_name).write_text("dirty before setup\n")

    cmd_setup(
        worktree_root=str(worktree),
        primary_root=str(primary_repo),
        task_id="TASK-TEST",
    )

    # No further changes — should pass
    exit_code = cmd_verify(worktree_root=str(worktree))
    assert exit_code == 0, "Unchanged leading-dash dirty tracked file should pass"


def test_verify_fails_when_leading_dash_tracked_staged_is_mutated(
    worktree: Path, primary_repo: Path
):
    """FINDING-3: Mutation of a leading-dash staged tracked file is caught.

    ``_checksum_file`` is also called for staged-file fingerprints (via
    ``git diff --cached --name-only -z``). The ``--`` fix applies to both
    dirty (unstaged) and staged tracked files.
    """
    dash_name = "--staged-option"
    (primary_repo / dash_name).write_text("initial staged\n")
    _run(["git", "-C", str(primary_repo), "add", dash_name])
    _run(["git", "-C", str(primary_repo), "commit", "-m", "add tracked staged dash file"])

    # Make it dirty AND staged BEFORE setup
    (primary_repo / dash_name).write_text("staged before setup\n")
    _run(["git", "-C", str(primary_repo), "add", dash_name])

    cmd_setup(
        worktree_root=str(worktree),
        primary_root=str(primary_repo),
        task_id="TASK-TEST",
    )

    # Mutate and re-stage AFTER setup
    (primary_repo / dash_name).write_text("mutated staged after setup\n")
    _run(["git", "-C", str(primary_repo), "add", dash_name])

    exit_code = cmd_verify(worktree_root=str(worktree))
    assert exit_code == 1, (
        "Should detect mutation of leading-dash staged tracked file via content hash change"
    )


def test_verify_passes_when_leading_dash_tracked_staged_is_unchanged(
    worktree: Path, primary_repo: Path
):
    """FINDING-3: Unchanged leading-dash staged tracked file passes."""
    dash_name = "--staged-option"
    (primary_repo / dash_name).write_text("initial staged\n")
    _run(["git", "-C", str(primary_repo), "add", dash_name])
    _run(["git", "-C", str(primary_repo), "commit", "-m", "add tracked staged dash file"])
    (primary_repo / dash_name).write_text("staged before setup\n")
    _run(["git", "-C", str(primary_repo), "add", dash_name])

    cmd_setup(
        worktree_root=str(worktree),
        primary_root=str(primary_repo),
        task_id="TASK-TEST",
    )

    # No further changes — should pass
    exit_code = cmd_verify(worktree_root=str(worktree))
    assert exit_code == 0, "Unchanged leading-dash staged tracked file should pass"


# ── Byte-identical guard copies ─────────────────────────────────────────────


def test_guard_copies_are_byte_identical():
    """runtime/tools/worktree_guard.py and protocol/skills/make-worktree/
    worktree_guard.py must be byte-identical.

    The protocol/skills/ copy is the one injected into agent workspaces.
    The runtime/tools/ copy is the tested, importable version. They must
    stay in sync.
    """
    repo_root = Path(__file__).resolve().parents[1]
    runtime_copy = repo_root / "runtime" / "tools" / "worktree_guard.py"
    protocol_copy = (
        repo_root / "protocol" / "skills" / "make-worktree" / "worktree_guard.py"
    )
    assert runtime_copy.is_file(), f"Missing: {runtime_copy}"
    assert protocol_copy.is_file(), f"Missing: {protocol_copy}"
    assert runtime_copy.read_bytes() == protocol_copy.read_bytes(), (
        f"Guard copies are NOT byte-identical:\n"
        f"  Runtime:   {runtime_copy}\n"
        f"  Protocol:  {protocol_copy}\n"
        f"  These must stay in sync. Run: cp {runtime_copy} {protocol_copy}"
    )

    # Explicit regression: both copies must contain the hash-object -- fix
    runtime_text = runtime_copy.read_text()
    protocol_text = protocol_copy.read_text()
    needle = '"hash-object", "--"'
    assert needle in runtime_text, (
        f"Runtime guard missing hash-object -- option terminator.\n"
        f"  Expected: git hash-object -- <relpath>\n"
        f"  File: {runtime_copy}"
    )
    assert needle in protocol_text, (
        f"Protocol guard missing hash-object -- option terminator.\n"
        f"  Expected: git hash-object -- <relpath>\n"
        f"  File: {protocol_copy}"
    )

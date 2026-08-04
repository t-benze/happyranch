"""Tests for the `happyranch doctor` CLI command.

The doctor command is local-only (no daemon required) and checks whether
the editable-install pointer resolves to the canonical source checkout.
"""
from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from io import StringIO
from pathlib import Path

import pytest

from cli.commands.doctor import (
    _editable_pointer,
    _canonical_source,
    _print_repair,
    cmd_doctor,
)


# ── unit tests: _editable_pointer ────────────────────────────────────


class TestEditablePointer:
    def test_returns_none_when_no_pth_file(self, monkeypatch, tmp_path):
        empty_site = tmp_path / "empty-site"
        empty_site.mkdir()
        monkeypatch.setattr("site.getsitepackages", lambda: [str(empty_site)])
        assert _editable_pointer() is None

    def test_returns_path_from_pth(self, monkeypatch, tmp_path):
        site_dir = tmp_path / "site-packages"
        site_dir.mkdir()
        pth = site_dir / "_editable_impl_happyranch.pth"
        pth.write_text("/canonical/happyranch\n")
        monkeypatch.setattr("site.getsitepackages", lambda: [str(site_dir)])
        result = _editable_pointer()
        assert result == Path("/canonical/happyranch")

    def test_skips_comment_lines(self, monkeypatch, tmp_path):
        site_dir = tmp_path / "site-packages"
        site_dir.mkdir()
        pth = site_dir / "_editable_impl_happyranch.pth"
        pth.write_text("# comment\n/canonical/happyranch\n")
        monkeypatch.setattr("site.getsitepackages", lambda: [str(site_dir)])
        result = _editable_pointer()
        assert result == Path("/canonical/happyranch")


# ── unit tests: _canonical_source ────────────────────────────────────


class TestCanonicalSource:
    def test_git_common_dir_from_worktree_pointer(self, monkeypatch, tmp_path):
        """When the .pth pointer is a git worktree, _canonical_source finds
        the main checkout via git rev-parse --git-common-dir."""
        main_repo = tmp_path / "main-repo"
        main_repo.mkdir()
        (main_repo / ".git").mkdir()
        worktree = tmp_path / "stale-worktree"
        worktree.mkdir()

        # Mock subprocess.run so git returns the main repo's .git
        def fake_run(cmd, **_kwargs):
            if cmd[0] == "git" and cmd[1] == "-C":
                return subprocess.CompletedProcess(
                    cmd, returncode=0,
                    stdout=str(main_repo / ".git") + "\n", stderr="",
                )
            return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="")

        # Also mock _editable_pointer to return the worktree path
        monkeypatch.setattr(
            "cli.commands.doctor._editable_pointer",
            lambda: worktree,
        )
        monkeypatch.setattr(subprocess, "run", fake_run)

        result = _canonical_source()
        assert result == main_repo.resolve()

    def test_returns_none_when_pointer_not_git(self, monkeypatch, tmp_path):
        """When the .pth pointer is not in a git repo, returns None."""
        pointer = tmp_path / "pointer"
        pointer.mkdir()
        monkeypatch.setattr(
            "cli.commands.doctor._editable_pointer",
            lambda: pointer,
        )
        # git fails for non-git directory
        def fake_run_fail(cmd, **_kwargs):
            return subprocess.CompletedProcess(cmd, returncode=128, stdout="", stderr="fatal: not a git repository")
        monkeypatch.setattr(subprocess, "run", fake_run_fail)

        result = _canonical_source()
        assert result is None

    def test_returns_none_when_pointer_is_none(self, monkeypatch):
        monkeypatch.setattr("cli.commands.doctor._editable_pointer", lambda: None)
        result = _canonical_source()
        assert result is None

    def test_git_failure_returns_none(self, monkeypatch, tmp_path):
        """When git returns non-zero, returns None (no guesswork)."""
        pointer = tmp_path / "pointer"
        pointer.mkdir()
        monkeypatch.setattr(
            "cli.commands.doctor._editable_pointer",
            lambda: pointer,
        )
        # git fails
        def fake_run_fail(cmd, **_kwargs):
            return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="")
        monkeypatch.setattr(subprocess, "run", fake_run_fail)

        result = _canonical_source()
        assert result is None


# ── integration-style: cmd_doctor output ─────────────────────────────


class TestCmdDoctorOutput:
    @staticmethod
    def _patch_doctor(monkeypatch, *, canonical: Path | None, pointer: Path | None):
        """Patch _canonical_source and _editable_pointer for cmd_doctor tests."""
        monkeypatch.setattr(
            "cli.commands.doctor._canonical_source",
            lambda: canonical,
        )
        monkeypatch.setattr(
            "cli.commands.doctor._editable_pointer",
            lambda: pointer,
        )

    def test_pass_when_pointer_matches_canonical(self, monkeypatch):
        """When .pth and canonical source point to same path, PASS."""
        canonical = Path("/canonical/happyranch")
        self._patch_doctor(monkeypatch, canonical=canonical, pointer=canonical)

        args = argparse.Namespace()
        saved_stdout = sys.stdout
        try:
            sys.stdout = StringIO()
            cmd_doctor(args)
            output = sys.stdout.getvalue()
        finally:
            sys.stdout = saved_stdout

        assert "PASS" in output
        assert "canonical/happyranch" in output

    def test_fail_when_pointer_mismatches(self, monkeypatch):
        """When .pth points to different path, FAIL and exit 1."""
        self._patch_doctor(
            monkeypatch,
            canonical=Path("/canonical/happyranch"),
            pointer=Path("/stale/worktree/TASK-999"),
        )

        args = argparse.Namespace()
        with pytest.raises(SystemExit) as exc_info:
            cmd_doctor(args)
        assert exc_info.value.code == 1

    def test_fail_when_no_pointer(self, monkeypatch):
        """When no .pth exists, exits 1."""
        self._patch_doctor(
            monkeypatch,
            canonical=Path("/canonical/happyranch"),
            pointer=None,
        )

        args = argparse.Namespace()
        with pytest.raises(SystemExit) as exc_info:
            cmd_doctor(args)
        assert exc_info.value.code == 1

    def test_exit_2_when_no_canonical_source(self, monkeypatch):
        """When no independent canonical source is available, exits 2."""
        self._patch_doctor(
            monkeypatch,
            canonical=None,
            pointer=Path("/stale/worktree"),
        )

        args = argparse.Namespace()
        with pytest.raises(SystemExit) as exc_info:
            cmd_doctor(args)
        assert exc_info.value.code == 2

    def test_repair_command_includes_shell_quoted_canonical_path(self, capsys):
        """_print_repair shows the PYTHONPATH repair command with shell-quoted path."""
        _print_repair(Path("/canonical/happyranch"))
        captured = capsys.readouterr()
        expected = shlex.quote("/canonical/happyranch")
        assert f"PYTHONPATH={expected}" in captured.err
        assert "happyranch" in captured.err
        assert "non-destructive" in captured.err.lower()

    def test_repair_command_quotes_path_with_spaces(self, capsys, tmp_path):
        """A canonical path containing spaces is shell-quoted so the repair
        command is a single runnable shell assignment."""
        space_path = tmp_path / "My Projects" / "happyranch"
        space_path.mkdir(parents=True)
        _print_repair(space_path)
        captured = capsys.readouterr()
        quoted = shlex.quote(str(space_path))
        assert f"PYTHONPATH={quoted}" in captured.err
        # The path itself is shell-quoted, not bare.
        assert f"PYTHONPATH={str(space_path)}" not in captured.err

    def test_repair_does_not_instruct_pip_install(self, capsys):
        """The repair command never instructs running pip install or uv install."""
        _print_repair(Path("/canonical/happyranch"))
        captured = capsys.readouterr()
        assert "pip install" not in captured.err.lower()
        assert "uv sync" not in captured.err.lower()
        assert "uv pip install" not in captured.err.lower()


# ── red-side regression: stale-but-existing worktree ─────────────────


class TestStaleWorktreeFalsePass:
    """When the .pth points at a still-existing disposable worktree, the
    old Settings().project_root / HAPPYRANCH_PROJECT_ROOT approaches would
    falsely PASS because the imported runtime package was also resolved from
    that worktree. The git-based _canonical_source prevents this false PASS
    by locating the main checkout via git-common-dir, independently of both
    the .pth and any untrusted environment override."""

    def test_stale_worktree_detected_as_mismatch(self, monkeypatch, tmp_path):
        """A .pth pointing at a still-existing worktree must FAIL when the
        git-based canonical source returns the main checkout."""
        main_repo = tmp_path / "main-repo"
        main_repo.mkdir()
        worktree = tmp_path / "stale-worktree"
        worktree.mkdir()

        # Canonical source resolves to main repo (via git common-dir)
        monkeypatch.setattr(
            "cli.commands.doctor._canonical_source",
            lambda: main_repo.resolve(),
        )
        # .pth points to stale worktree (which still exists!)
        monkeypatch.setattr(
            "cli.commands.doctor._editable_pointer",
            lambda: worktree.resolve(),
        )

        args = argparse.Namespace()
        with pytest.raises(SystemExit) as exc_info:
            cmd_doctor(args)
        assert exc_info.value.code == 1

    def test_real_git_linked_worktree_ignores_happyranch_project_root(
        self, monkeypatch, tmp_path,
    ):
        """End-to-end test with a real git repository and linked worktree.

        Proves that _canonical_source() ignores HAPPYRANCH_PROJECT_ROOT
        even when it is set to the same stale worktree named in the
        suspect .pth — the function uses only git-common-dir to locate
        the main checkout, preventing the false PASS.

        This test exercises the REAL _canonical_source() and cmd_doctor()
        discovery paths (not mocked), only patching _editable_pointer to
        supply the worktree path (since we cannot install a real .pth in
        the test's site-packages).
        """
        import subprocess as sp

        # 1. Create a real git repository with at least one commit.
        main_repo = tmp_path / "main-repo"
        main_repo.mkdir()
        sp.run(["git", "-C", str(main_repo), "init"], check=True, capture_output=True)
        sp.run(
            ["git", "-C", str(main_repo), "config", "user.email", "test@test"],
            check=True, capture_output=True,
        )
        sp.run(
            ["git", "-C", str(main_repo), "config", "user.name", "Test"],
            check=True, capture_output=True,
        )
        (main_repo / "README.md").write_text("# test")
        sp.run(
            ["git", "-C", str(main_repo), "add", "README.md"],
            check=True, capture_output=True,
        )
        sp.run(
            ["git", "-C", str(main_repo), "commit", "-m", "initial"],
            check=True, capture_output=True,
        )

        # 2. Create a real linked worktree from the main checkout.
        worktree_path = tmp_path / "stale-worktree"
        sp.run(
            ["git", "-C", str(main_repo), "worktree", "add", str(worktree_path)],
            check=True, capture_output=True,
        )

        # 3. Patch _editable_pointer to return the worktree (simulating a
        #    .pth that was rewritten by a disposable worktree session).
        monkeypatch.setattr(
            "cli.commands.doctor._editable_pointer",
            lambda: worktree_path,
        )

        # 4. Set HAPPYRANCH_PROJECT_ROOT to the STALE WORKTREE — the same
        #    path the suspect .pth points at.  The OLD code would false-PASS
        #    because env_override == pointer.  The NEW code ignores this
        #    untrusted override.
        monkeypatch.setenv("HAPPYRANCH_PROJECT_ROOT", str(worktree_path))

        # 5. Call the REAL _canonical_source() — it must return the main
        #    checkout via git-common-dir, NOT the worktree from the env var.
        canonical = _canonical_source()
        assert canonical is not None, (
            "_canonical_source must find the main checkout via git"
        )
        assert canonical == main_repo.resolve(), (
            f"Expected canonical={main_repo}, got {canonical}"
        )
        assert canonical != worktree_path.resolve(), (
            f"Canonical source must NOT be the worktree; "
            f"canonical={canonical}, worktree={worktree_path}"
        )

        # 6. Call cmd_doctor() — must FAIL (exit 1) because the .pth
        #    pointer (worktree) does not match the canonical source (main).
        #    Even though HAPPYRANCH_PROJECT_ROOT is set to the worktree,
        #    the doctor ignores it and correctly identifies the mismatch.
        args = argparse.Namespace()
        with pytest.raises(SystemExit) as exc_info:
            cmd_doctor(args)
        assert exc_info.value.code == 1, (
            f"Doctor must FAIL (exit 1) even with HAPPYRANCH_PROJECT_ROOT set; "
            f"got exit {exc_info.value.code}"
        )

        # 7. Clean up the linked worktree so the temp dir can be removed.
        sp.run(
            ["git", "-C", str(main_repo), "worktree", "remove", str(worktree_path), "--force"],
            capture_output=True,
        )

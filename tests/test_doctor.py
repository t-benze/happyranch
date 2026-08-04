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
    def test_env_var_override(self, monkeypatch, tmp_path):
        """HAPPYRANCH_PROJECT_ROOT takes priority."""
        canonical = tmp_path / "canonical"
        canonical.mkdir()
        monkeypatch.setenv("HAPPYRANCH_PROJECT_ROOT", str(canonical))
        result = _canonical_source()
        assert result == canonical.resolve()

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

    def test_returns_none_when_no_env_and_no_git(self, monkeypatch, tmp_path):
        """When neither HAPPYRANCH_PROJECT_ROOT nor git detection works."""
        monkeypatch.delenv("HAPPYRANCH_PROJECT_ROOT", raising=False)
        # Mock _editable_pointer to return a non-existent path
        monkeypatch.setattr(
            "cli.commands.doctor._editable_pointer",
            lambda: tmp_path / "nonexistent",
        )
        result = _canonical_source()
        assert result is None

    def test_returns_none_when_pointer_is_none(self, monkeypatch):
        monkeypatch.delenv("HAPPYRANCH_PROJECT_ROOT", raising=False)
        monkeypatch.setattr("cli.commands.doctor._editable_pointer", lambda: None)
        result = _canonical_source()
        assert result is None

    def test_git_failure_returns_none(self, monkeypatch, tmp_path):
        """When git returns non-zero, returns None (no guesswork)."""
        pointer = tmp_path / "pointer"
        pointer.mkdir()
        monkeypatch.delenv("HAPPYRANCH_PROJECT_ROOT", raising=False)
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
    old Settings().project_root approach would falsely PASS because the
    imported runtime package was also resolved from that worktree.
    The new git-based _canonical_source prevents this false PASS."""

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

        # The old Settings().project_root approach would have given false PASS
        # because runtime would be imported from worktree. With independent
        # git detection, we correctly detect the mismatch.

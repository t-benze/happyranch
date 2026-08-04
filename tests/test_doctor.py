"""Tests for the `happyranch doctor` CLI command.

The doctor command is local-only (no daemon required) and checks whether
the editable-install pointer resolves to the canonical source checkout.
"""
from __future__ import annotations

import argparse
import sys
from io import StringIO
from pathlib import Path

import pytest

from cli.commands.doctor import (
    _editable_pointer,
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


# ── integration-style: cmd_doctor output ─────────────────────────────


class TestCmdDoctorOutput:
    @staticmethod
    def _mock_settings(monkeypatch, project_root: Path):
        """Mock Settings().project_root to return *project_root*."""
        from unittest.mock import MagicMock
        mock_instance = MagicMock()
        mock_instance.project_root = project_root
        mock_class = MagicMock(return_value=mock_instance)
        monkeypatch.setattr(
            "runtime.config.Settings", mock_class
        )

    def test_pass_when_pointer_matches_canonical(self, monkeypatch, tmp_path):
        """When .pth and Settings.project_root point to same path, PASS."""
        site_dir = tmp_path / "site-packages"
        site_dir.mkdir()
        pth = site_dir / "_editable_impl_happyranch.pth"
        canonical = Path("/canonical/happyranch")
        pth.write_text(str(canonical) + "\n")

        monkeypatch.setattr("site.getsitepackages", lambda: [str(site_dir)])
        self._mock_settings(monkeypatch, canonical)

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

    def test_fail_when_pointer_mismatches(self, monkeypatch, tmp_path):
        """When .pth points to different path, FAIL and exit 1."""
        site_dir = tmp_path / "site-packages"
        site_dir.mkdir()
        pth = site_dir / "_editable_impl_happyranch.pth"
        pth.write_text("/stale/worktree/TASK-999\n")

        monkeypatch.setattr("site.getsitepackages", lambda: [str(site_dir)])
        self._mock_settings(monkeypatch, Path("/canonical/happyranch"))

        args = argparse.Namespace()
        with pytest.raises(SystemExit) as exc_info:
            cmd_doctor(args)
        assert exc_info.value.code == 1

    def test_fail_when_no_pointer(self, monkeypatch, tmp_path):
        """When no .pth exists, exits 1."""
        site_dir = tmp_path / "site-packages"
        site_dir.mkdir()

        monkeypatch.setattr("site.getsitepackages", lambda: [str(site_dir)])
        self._mock_settings(monkeypatch, Path("/canonical/happyranch"))

        args = argparse.Namespace()
        with pytest.raises(SystemExit) as exc_info:
            cmd_doctor(args)
        assert exc_info.value.code == 1

    def test_repair_command_includes_canonical_path(self, capsys):
        """_print_repair shows the exact PYTHONPATH repair command."""
        _print_repair(Path("/canonical/happyranch"))
        captured = capsys.readouterr()
        assert "PYTHONPATH=/canonical/happyranch" in captured.err
        assert "happyranch" in captured.err
        assert "non-destructive" in captured.err.lower()

    def test_repair_does_not_instruct_pip_install(self, capsys):
        """The repair command never instructs running pip install or uv install."""
        _print_repair(Path("/canonical/happyranch"))
        captured = capsys.readouterr()
        # The repair must not tell the user to run pip install or uv pip install.
        assert "pip install" not in captured.err.lower()
        assert "uv sync" not in captured.err.lower()
        assert "uv pip install" not in captured.err.lower()

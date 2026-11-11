"""Focused bundled-CLI checks; run directly, outside the retired pytest suite."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
# Module-level Settings must read a scratch home, never the operator's config.
with tempfile.TemporaryDirectory() as import_home, patch.dict(os.environ, {
    **{k: v for k, v in os.environ.items() if not k.startswith("HAPPYRANCH_")},
    "HAPPYRANCH_DAEMON_HOME": import_home,
}, clear=True):
    from runtime.orchestrator.executors import _normalize_path


class BundledPathTests(unittest.TestCase):
    def check_path(self, signal: str | None, path: str, expected: list[str]) -> None:
        with patch.dict(os.environ, {"PATH": path}, clear=True):
            if signal is not None:
                os.environ["HAPPYRANCH_BUNDLED_BIN"] = signal
            with patch.object(sys, "frozen", False, create=True), patch(
                "runtime.orchestrator.executors._STANDARD_TOOL_DIRS", ["/tools"]
            ):
                _normalize_path()
                self.assertEqual(os.environ["PATH"].split(":"), expected)
                _normalize_path()
                self.assertEqual(os.environ["PATH"].split(":"), expected)

    def test_env_set_resolves_bundled_cli_before_stale_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundled, stale = Path(tmp) / "bundled", Path(tmp) / "stale"
            for directory in (bundled, stale):
                directory.mkdir()
                cli = directory / "happyranch"
                cli.write_text("#!/bin/sh\nexit 0\n")
                cli.chmod(0o755)
            with patch.dict(os.environ, {
                "PATH": str(stale), "HAPPYRANCH_BUNDLED_BIN": str(bundled)
            }, clear=True), patch.object(sys, "frozen", False, create=True):
                _normalize_path()
                self.assertEqual(shutil.which("happyranch"), str(bundled / "happyranch"))
                self.assertEqual(os.environ["PATH"].split(":").count(str(bundled)), 1)

    def test_env_unset_preserves_standard_directory_behavior(self) -> None:
        self.check_path(None, "/usr/bin:/bin", ["/tools", "/usr/bin", "/bin"])

    def test_env_directory_later_in_path_moves_once_to_front(self) -> None:
        self.check_path("/bundle/bin", "/stale:/bundle/bin:/bundle/bin:/usr/bin",
                        ["/bundle/bin", "/tools", "/stale", "/usr/bin"])


if __name__ == "__main__":
    unittest.main()

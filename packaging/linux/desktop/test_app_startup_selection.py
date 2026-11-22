"""Standalone preference boundary; fresh module and temporary config per case."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class StartupSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        env = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root)})
        env.start()
        self.addCleanup(env.stop)

    def fresh(self) -> object:
        source = Path(__file__).with_name("app_startup_selection.py")
        self.assertTrue(source.is_file(), "installed native startup preference must exist")
        spec = importlib.util.spec_from_file_location("startup_fixture", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_absent_local_and_closed_roundtrip_then_corrupt_suppresses(self) -> None:
        store = self.fresh()
        self.assertEqual(store.load()["current"], "local")
        path = self.root / "happyranch/startup-selection"
        for value in ("remote:" + "a" * 64, "remote:unsaved", "local"):
            with self.subTest(value=value):
                self.assertEqual(store.set(value), {"current": value, "persisted": True})
                self.assertEqual(self.fresh().load()["current"], value)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        for raw in (b"", b"remote:host.example", b"local\n\n", b"x" * 129, b"\xff"):
            with self.subTest(raw=raw):
                path.write_bytes(raw)
                self.assertEqual(self.fresh().load()["current"], "remote:unsaved")
        for value in (None, True, "", "remote:host", "local\n"):
            with self.assertRaises(ValueError):
                store.set(value)

    def test_failed_write_reports_without_replacing_last_confirmed_record(self) -> None:
        store = self.fresh()
        store.set("remote:" + "a" * 64)
        with patch.object(store.os, "replace", side_effect=OSError("synthetic disk failure")):
            self.assertEqual(store.set("local"), {"current": "local", "persisted": False})
        self.assertEqual(self.fresh().load()["current"], "remote:" + "a" * 64)
        self.assertEqual(list((self.root / "happyranch").glob(".startup-selection-*")), [])
        store.clear_reference("b" * 64)
        self.assertEqual(self.fresh().load()["current"], "remote:" + "a" * 64)
        store.clear_reference("a" * 64)
        self.assertEqual(self.fresh().load()["current"], "local")

    def test_symlink_unreadable_and_relative_xdg_fail_closed(self) -> None:
        store = self.fresh()
        directory = self.root / "happyranch"
        directory.mkdir()
        target = self.root / "outside"
        target.write_text("local")
        (directory / "startup-selection").symlink_to(target)
        self.assertEqual(store.load()["current"], "remote:unsaved")
        self.assertEqual(target.read_text(), "local")
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": "relative"}), \
                patch.object(store.Path, "home", return_value=self.root):
            self.assertEqual(store._config_directory(), self.root / ".config/happyranch")


if __name__ == "__main__":
    unittest.main()

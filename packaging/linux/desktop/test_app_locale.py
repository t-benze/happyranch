"""Focused native locale checks: stdlib only, isolated XDG roots, no daemon."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import app_locale


class LocaleTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.config = self.root / "config"
        self.directory = self.config / "happyranch"
        self.directory.mkdir(parents=True)
        self.path = self.directory / "locale"
        environment = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.config),
                                              "HOME": str(self.root), "LANG": "en_US.UTF-8"})
        environment.start()
        self.addCleanup(environment.stop)
        self.store = self.fresh()

    def fresh(self) -> object:
        spec = importlib.util.spec_from_file_location("isolated_locale", app_locale.__file__)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_lang_fallback_and_snapshot_isolation(self) -> None:
        for lang, expected in (("zh_CN.UTF-8", "zh-CN"), ("ZH-anything", "zh-CN"),
                               ("en_US.UTF-8", "en"), ("fr_FR", "en"), ("", "en")):
            with self.subTest(lang=lang), patch.dict(os.environ, {"LANG": lang}):
                store = self.fresh()
                self.assertEqual(store.load(), {"current": expected, "persisted": False})
                snapshot = store.current()
                snapshot["current"] = "fr"
                self.assertEqual(store.load(), {"current": expected, "persisted": False})
        with patch.dict(os.environ):
            os.environ.pop("LANG", None)
            self.assertEqual(self.fresh().load(), {"current": "en", "persisted": False})
        self.assertFalse(self.path.exists())

    def test_xdg_default_empty_and_relative_paths(self) -> None:
        for xdg in (None, "", "relative-config"):
            with self.subTest(xdg=xdg), patch.dict(os.environ):
                if xdg is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = xdg
                self.assertTrue(self.fresh().set("zh-CN")["persisted"])
                path = self.root / ".config/happyranch/locale"
                self.assertTrue(path.is_file())
                self.assertEqual(path.read_bytes(), b"zh-CN\n")
                path.unlink()
        self.assertFalse(self.path.exists())

    def test_saved_value_and_restart_reread(self) -> None:
        for value in ("en", "zh-CN"):
            with self.subTest(value=value):
                self.assertEqual(self.store.set(value), {"current": value, "persisted": True})
                self.assertEqual(self.path.read_bytes(), (value + "\n").encode("ascii"))
                self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
                self.assertEqual(list(self.directory.iterdir()), [self.path])
                result = subprocess.run([sys.executable, "-B", "-c",
                    "import app_locale; print(app_locale.load())"],
                    cwd=Path(app_locale.__file__).parent,
                    env={**os.environ, "LANG": "fr_FR"}, capture_output=True, text=True, check=True)
                self.assertEqual(result.stdout.strip(), repr({"current": value, "persisted": True}))

    def test_invalid_set_changes_neither_state_nor_disk(self) -> None:
        self.store.set("zh-CN")
        for value in ("fr", "EN", "zh", "en\n", "", None, True, 1, [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.set(value)
            self.assertEqual(self.store.current(), {"current": "zh-CN", "persisted": True})
            self.assertEqual(self.path.read_bytes(), b"zh-CN\n")

    def test_corrupt_and_unsafe_files_fall_back(self) -> None:
        for content in (b"", b"garbage", b"fr\n", b"\xff", b"en\x00", b'"en"',
                        b" zh-CN\n", b"en" + b" " * 63, b"x" * 4096):
            with self.subTest(content=content[:20]), patch.dict(os.environ, {"LANG": "zh_CN.UTF-8"}):
                self.path.write_bytes(content)
                self.assertEqual(self.fresh().load(), {"current": "zh-CN", "persisted": False})
        self.path.unlink()
        target = self.root / "target"
        target.write_bytes(b"zh-CN\n")
        for kind in ("symlink", "dangling", "directory", "fifo"):
            with self.subTest(kind=kind):
                if kind in ("symlink", "dangling"):
                    self.path.symlink_to(target if kind == "symlink" else self.root / "missing")
                elif kind == "directory":
                    self.path.mkdir()
                else:
                    os.mkfifo(self.path)
                try:
                    self.assertEqual(self.fresh().load(), {"current": "en", "persisted": False})
                finally:
                    if kind == "directory":
                        self.path.rmdir()
                    else:
                        self.path.unlink()
        with patch.object(os, "open", side_effect=PermissionError("synthetic")):
            self.assertEqual(self.fresh().load(), {"current": "en", "persisted": False})

    def test_symlinked_parent_is_not_read_or_written(self) -> None:
        self.path.write_bytes(b"zh-CN\n")
        alias = self.root / "alias"
        alias.symlink_to(self.config, target_is_directory=True)
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(alias)}):
            self.assertEqual(self.store.load(), {"current": "en", "persisted": False})
            self.assertEqual(self.store.set("en"), {"current": "en", "persisted": False})
        self.assertEqual(self.path.read_bytes(), b"zh-CN\n")

    def test_read_only_directory_keeps_session_choice(self) -> None:
        self.store.set("en")
        self.directory.chmod(0o500)
        try:
            self.assertEqual(self.store.set("zh-CN"), {"current": "zh-CN", "persisted": False})
            self.assertEqual(self.path.read_bytes(), b"en\n")
            self.assertEqual(self.store.load(), {"current": "zh-CN", "persisted": False})
            self.assertEqual(self.fresh().load(), {"current": "en", "persisted": True})
        finally:
            self.directory.chmod(0o700)

    def test_write_failure_stages_and_durability_reporting(self) -> None:
        directory_stat = self.directory.stat()
        for stage in ("file_sync", "replace", "directory_sync"):
            with self.subTest(stage=stage):
                self.store.set("en")
                sync = os.fsync

                def fail_sync(fd: int) -> None:
                    target = os.fstat(fd)
                    directory = stat.S_ISDIR(target.st_mode)
                    if (stage == "directory_sync" and os.path.samestat(target, directory_stat)) or (stage == "file_sync" and not directory):
                        raise OSError("synthetic sync failure")
                    sync(fd)

                with patch.object(os, "fsync", side_effect=fail_sync), patch.object(
                        os, "replace", side_effect=OSError("synthetic replace failure") if stage == "replace" else os.replace):
                    self.assertEqual(self.store.set("zh-CN"), {"current": "zh-CN", "persisted": False})
                self.assertEqual(self.store.current(), {"current": "zh-CN", "persisted": False})
                self.assertEqual(self.path.read_bytes(), b"zh-CN\n" if stage == "directory_sync" else b"en\n")
                self.assertEqual(list(self.directory.iterdir()), [self.path])

        self.path.unlink()
        self.directory.rmdir()
        parent_stat = self.config.stat()
        sync = os.fsync
        parent_attempts = []
        successful_parent_syncs = []

        def fail_parent_once(fd: int) -> None:
            parent = os.path.samestat(os.fstat(fd), parent_stat)
            if parent:
                parent_attempts.append(fd)
                if len(parent_attempts) == 1:
                    raise OSError("synthetic parent sync failure")
            sync(fd)
            if parent:
                successful_parent_syncs.append(os.fstat(fd))

        with patch.object(os, "fsync", side_effect=fail_parent_once):
            self.assertEqual(self.store.set("zh-CN"), {"current": "zh-CN", "persisted": False})
            self.assertEqual(len(parent_attempts), 1)
            self.assertEqual(successful_parent_syncs, [])
            self.assertTrue(self.directory.is_dir())
            self.assertFalse(self.path.exists())
            self.assertEqual(self.store.load(), {"current": "zh-CN", "persisted": False})
            self.assertEqual(self.store.set("en"), {"current": "en", "persisted": True})
            self.assertEqual(len(successful_parent_syncs), 1,
                             "retry acknowledged persistence without syncing the config parent")
        self.assertEqual(self.path.read_bytes(), b"en\n")
        self.assertEqual(self.store.current(), {"current": "en", "persisted": True})
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_atomic_replacement_and_sync_boundaries(self) -> None:
        self.store.set("en")
        replace, sync = os.replace, os.fsync
        events = []

        def observe_sync(fd: int) -> None:
            sync(fd)
            target = os.fstat(fd)
            if os.path.samestat(target, self.config.stat()):
                events.append("parent_sync")
            else:
                events.append("directory_sync" if stat.S_ISDIR(target.st_mode) else "file_sync")

        def observe_replace(source: str, destination: str, **kwargs: object) -> None:
            self.assertEqual(self.path.read_bytes(), b"en\n")
            self.assertEqual((self.directory / source).read_bytes(), b"zh-CN\n")
            self.assertEqual(kwargs["src_dir_fd"], kwargs["dst_dir_fd"])
            events.append("replace")
            replace(source, destination, **kwargs)

        with patch.object(os, "fsync", side_effect=observe_sync), patch.object(os, "replace", side_effect=observe_replace):
            self.assertEqual(self.store.set("zh-CN"), {"current": "zh-CN", "persisted": True})
        self.assertEqual(events, ["parent_sync", "file_sync", "replace", "directory_sync"])
        self.assertEqual(self.path.read_bytes(), b"zh-CN\n")

    def test_threaded_newer_set_cannot_be_overwritten(self) -> None:
        entered, release, second_started, second_done = (threading.Event() for _ in range(4))
        replace = os.replace
        results, errors = {}, []

        def delayed_replace(source: str, destination: str, **kwargs: object) -> None:
            if threading.current_thread().name == "older":
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("test release missing")
            replace(source, destination, **kwargs)

        def choose(name: str, value: str) -> None:
            try:
                if name == "newer":
                    second_started.set()
                results[name] = self.store.set(value)
            except Exception as exc:
                errors.append(exc)
            finally:
                if name == "newer":
                    second_done.set()

        older = threading.Thread(target=choose, name="older", args=("older", "zh-CN"))
        newer = threading.Thread(target=choose, name="newer", args=("newer", "en"))
        with patch.object(os, "replace", side_effect=delayed_replace):
            older.start()
            try:
                self.assertTrue(entered.wait(3))
                newer.start()
                self.assertTrue(second_started.wait(3))
                self.assertFalse(second_done.wait(0.1), "newer write escaped serialization")
            finally:
                release.set()
                older.join(3)
                if newer.ident is not None:
                    newer.join(3)
        self.assertFalse(older.is_alive() or newer.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results["older"], {"current": "zh-CN", "persisted": True})
        self.assertEqual(results["newer"], {"current": "en", "persisted": True})
        self.assertEqual(self.store.current(), {"current": "en", "persisted": True})
        self.assertEqual(self.path.read_bytes(), b"en\n")


if __name__ == "__main__":
    unittest.main()

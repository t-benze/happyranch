"""Focused native home-isolation checks; no daemon or retired unit suite."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
# Keep module-time configuration away from the operator's home.
with tempfile.TemporaryDirectory() as import_home, patch.dict(os.environ, {
    **{k: v for k, v in os.environ.items() if not k.startswith("HAPPYRANCH_")},
    "HAPPYRANCH_DAEMON_HOME": import_home,
}, clear=True):
    from cli.thread_delivery_release_requests import PrivateReleaseRequests
    from runtime import runtime as runtime_paths
    from runtime.skills.canonical_store import CanonicalSkillStore
    from runtime.workflows import sequential_venue as venue


class HomeIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name).resolve()
        self.account = self.work / "account"
        self.default = self.account / ".happyranch"
        self.selected = self.work / "selected-daemon"
        for path in (self.default, self.selected):
            path.mkdir(parents=True)
        self.enterContext(patch.dict(os.environ, {
            **{k: v for k, v in os.environ.items() if not k.startswith("HAPPYRANCH_")},
            "HOME": str(self.account), "HAPPYRANCH_DAEMON_HOME": str(self.selected),
        }, clear=True))
        self.enterContext(patch.object(Path, "home", return_value=self.account))
        self.enterContext(patch.object(runtime_paths, "_DEFAULT_HOME", self.default))
        self.enterContext(patch.object(venue.pwd, "getpwuid",
                                      return_value=SimpleNamespace(pw_dir=str(self.account))))

    def test_release_store_custody_stays_in_selected_or_default_home(self) -> None:
        value = {"version": 1, "operation_key": "operation", "mode": "human_risk_override",
                 "selection": {"org_slug": "example", "thread_id": "THR-1", "invocation_id": 1,
                               "agent_id": "agent", "session_id": "synthetic-session",
                               "preparation_id": "synthetic-preparation", "token_digest": "0" * 64}}
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        for setting, home in ((str(self.selected), self.selected), (None, self.default), ("", self.default)):
            with self.subTest(setting=setting):
                if setting is None:
                    os.environ.pop("HAPPYRANCH_DAEMON_HOME", None)
                else:
                    os.environ["HAPPYRANCH_DAEMON_HOME"] = setting
                store = PrivateReleaseRequests("example")
                root = home / "client-thread-delivery-releases" / "example"
                self.assertEqual(store.root, root)
                self.assertEqual(store.save("operation", "selection", value), raw)
                self.assertEqual(store.read("operation", "selection"), raw)
                self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(root.parent.stat().st_mode), 0o700)
                saved = root / "operation.selection.json"
                inode = saved.stat().st_ino
                changed = {**value, "mode": "proof_backed"}
                with self.assertRaisesRegex(ValueError, "release_saved_bytes_changed"):
                    store.save("operation", "selection", changed)
                self.assertEqual(saved.read_bytes(), raw)
                self.assertEqual(saved.stat().st_ino, inode)
                if home == self.selected:
                    self.assertFalse((self.default / "client-thread-delivery-releases").exists())

        # The selected daemon home is an existing anchor, never created here.
        missing = self.work / "missing-daemon"
        os.environ["HAPPYRANCH_DAEMON_HOME"] = str(missing)
        with self.assertRaises(FileNotFoundError):
            with PrivateReleaseRequests("example").directory():
                self.fail("missing daemon home admitted")
        self.assertFalse(missing.exists())
        alias = self.work / "home-alias"
        alias.symlink_to(self.selected, target_is_directory=True)
        os.environ["HAPPYRANCH_DAEMON_HOME"] = str(alias)
        with self.assertRaises(OSError):
            with PrivateReleaseRequests("example").directory():
                self.fail("symlink daemon home admitted")

    def test_venue_refuses_both_persistent_homes_before_open_and_admits_disposable(self) -> None:
        # HOME and the selected daemon home are deliberately not the passwd home.
        os.environ["HOME"] = str(self.work / "private-home")
        org = self.selected / "runtime/orgs/example"
        org.mkdir(parents=True)
        db = org / "happyranch.db"
        db.touch()
        info = db.stat()
        observation = venue.VenueObservation(self.selected, self.selected / "runtime", "example",
                                             info.st_dev, info.st_ino)
        venue.write_marker(observation)
        self.assertEqual(venue.check_venue_start(org_slug="example", org_root=org, db_path=db), observation)

        for name in (".happyranch", ".happyranch-release"):
            live = self.account / name
            live.mkdir(exist_ok=True)
            alias = self.work / (name + "-alias")
            alias.symlink_to(live, target_is_directory=True)
            for home, root in ((live, org), (live / "nested", org), (alias, org),
                               (self.selected, live / "runtime/orgs/example")):
                with self.subTest(live=name, home=home, org=root):
                    os.environ["HAPPYRANCH_DAEMON_HOME"] = str(home)
                    with patch.object(venue.os, "open", side_effect=AssertionError("live home opened")):
                        with self.assertRaises(venue.SequentialVenueUnavailable) as raised:
                            venue.check_venue_start(org_slug="example", org_root=root, db_path=db)
                    self.assertEqual(raised.exception.code, "sequential_venue_live_home")

        # A non-live alias keeps its original env-absent classification.
        alias = self.work / "disposable-alias"
        alias.symlink_to(self.selected, target_is_directory=True)
        os.environ["HAPPYRANCH_DAEMON_HOME"] = str(alias)
        with self.assertRaises(venue.SequentialVenueUnavailable) as raised:
            venue.check_venue_start(org_slug="example", org_root=org, db_path=db)
        self.assertEqual(raised.exception.code, "sequential_venue_env_absent")

    def test_canonical_store_fallback_uses_selected_home_and_retains_precedence(self) -> None:
        for setting, home in ((str(self.selected), self.selected), (None, self.default), ("", self.default)):
            with self.subTest(setting=setting):
                if setting is None:
                    os.environ.pop("HAPPYRANCH_DAEMON_HOME", None)
                else:
                    os.environ["HAPPYRANCH_DAEMON_HOME"] = setting
                store = CanonicalSkillStore(isolation=SimpleNamespace())
                self.assertEqual(store.root, home / "canonical-skills")
                self.assertTrue((home / "canonical-skills").is_dir())
                if home == self.selected:
                    self.assertFalse((self.default / "canonical-skills").exists())
        settings = SimpleNamespace(daemon_home=self.work / "settings-home")
        store = CanonicalSkillStore(settings=settings, isolation=SimpleNamespace())
        self.assertEqual(store.root, settings.daemon_home / "canonical-skills")
        self.assertTrue(store.root.is_dir())
        os.environ["HAPPYRANCH_CANONICAL_STORE_ROOT"] = str(self.work / "explicit-root")
        store = CanonicalSkillStore(settings=settings, isolation=SimpleNamespace())
        self.assertEqual(store.root, self.work / "explicit-root")
        self.assertTrue(store.root.is_dir())


if __name__ == "__main__":
    unittest.main()

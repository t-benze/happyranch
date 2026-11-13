"""Managed Darwin installer boundaries in the retained standalone venue."""
from __future__ import annotations

import io
import json
import os
import plistlib
import stat
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Iterator
from unittest.mock import Mock, patch

from runtime.remote_access import installer
from scripts.mac_native.test_connector_launchd import (
    DOMAIN, LABEL, RUNNING, TARGET, Runner, absent, row,
)

KEY = b"synthetic-enrollment-only-C5"
TOKEN = b"synthetic-daemon-bearer-only-C5"


def directories(path: Path) -> None:
    missing = []
    while not path.exists():
        missing.append(path)
        path = path.parent
    for path in reversed(missing):
        path.mkdir(mode=0o700)


def private(path: Path, data: bytes) -> None:
    directories(path.parent)
    path.write_bytes(data)
    path.chmod(0o600)


class ManagedDarwinInstaller(unittest.TestCase):
    @contextmanager
    def home(self) -> Iterator[dict[str, Path]]:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            source = root / ".happyranch/remote_access/config.json"
            policy = root / "operator-policy.json"
            key = root / "operator.key"
            private(key, KEY)
            private(root / "daemon.token", TOKEN)
            private(policy, b'{"synthetic-policy": true}')
            private(source, json.dumps(dict(
                tenant_id="tenant-C5", home_id="home-C5", connector_id="connector-C5",
                daemon_port=8765, daemon_token_path=str(root / "daemon.token"),
                policy_path=str(policy), managed=dict(bind_port=18443),
            )).encode())
            state = root / "Library/Application Support/HappyRanch/connector/happyranch-connector"
            paths = dict(root=root, source=source, policy=policy, key=key, state=state,
                         plist=root / f"Library/LaunchAgents/{LABEL}.plist",
                         logs=root / "Library/Logs/HappyRanch")
            with patch("sys.platform", "darwin"), patch.object(Path, "home", return_value=root), \
                    patch("os.path.expanduser", side_effect=lambda path: str(root) + path[1:] if path == "~" or path.startswith("~/") else path), \
                    patch.object(installer, "ManagedInstaller") as linux:
                # Base-production RED is safe even on a root Linux host.
                linux.return_value = Mock()
                yield paths
                linux.assert_not_called()

    def command(self, args: list[str], rows: list | None = None) -> tuple[int, str, str]:
        runner = Runner(self, rows or [])
        out, err = io.StringIO(), io.StringIO()
        with patch("runtime.remote_access.launchd_agent._default_run", runner), \
                redirect_stdout(out), redirect_stderr(err):
            code = installer.main(args)
        self.assertFalse(runner.rows)
        self.assertNotIn(KEY.decode(), out.getvalue() + err.getvalue())
        self.assertNotIn(TOKEN.decode(), out.getvalue() + err.getvalue())
        return code, out.getvalue(), err.getvalue()

    def install(self, p: dict[str, Path]) -> None:
        code, output, error = self.command(
            ["install", "--enrollment-key-file", str(p["key"])],
            [row("print", DOMAIN), row("enable", TARGET)],
        )
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(json.loads(output)["readiness"], "not-verified")

    def uninstall_rows(self, *, loaded: bool = False) -> list:
        return [row("print", DOMAIN), *(
            [row("print", TARGET, output=RUNNING), row("bootout", TARGET)] if loaded else absent()
        ), *absent(), row("disable", TARGET)]

    def test_install_stages_private_source_entry_preserving_trust_and_inputs(self) -> None:
        with self.home() as p:
            private(p["state"] / "trust-state.json", b"synthetic-existing-revocations")
            private(p["state"] / "trust-state.json.anchor", b"synthetic-existing-anchor")
            original = p["source"].read_bytes()
            self.install(p)
            config = json.loads((p["state"] / "config.json").read_bytes())
            self.assertEqual(config["state_path"], str(p["state"] / "trust-state.json"))
            self.assertEqual(config["policy_path"], str(p["state"] / "policy.json"))
            self.assertFalse(config["system"])
            self.assertEqual((p["state"] / "policy.json").read_bytes(), p["policy"].read_bytes())
            self.assertEqual((p["state"] / "enrollment.key").read_bytes(), KEY)
            self.assertEqual((p["state"] / "trust-state.json").read_bytes(), b"synthetic-existing-revocations")
            self.assertEqual((p["state"] / "trust-state.json.anchor").read_bytes(), b"synthetic-existing-anchor")
            self.assertEqual((p["source"].read_bytes(), p["key"].read_bytes()), (original, KEY))
            plist = plistlib.loads(p["plist"].read_bytes())
            self.assertEqual(plist["ProgramArguments"], [sys.executable, "-m", "runtime.remote_access.cli",
                             "run", "--managed", "--config", str(p["state"] / "config.json")])
            self.assertTrue(plist["Disabled"])
            self.assertNotIn(KEY, p["plist"].read_bytes())
            self.assertNotIn(TOKEN, (p["state"] / "config.json").read_bytes())
            for path in (*p["state"].iterdir(), p["plist"], *p["logs"].iterdir()):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(p["state"].stat().st_mode), 0o700)
            self.assertEqual(self.command(["check-drift"])[0], 0)
            code, _, _ = self.command(["install", "--enrollment-key-file", str(p["key"])])
            self.assertEqual(code, 1)  # Stored key cannot be silently replaced.

    def test_enrollment_validation_is_wired_before_writes_or_launchctl(self) -> None:
        for case in ("missing-argument", "relative", "mode", "symlink", "hardlink", "fifo", "oversized", "foreign"):
            with self.subTest(case=case), self.home() as p:
                key = p["key"]
                if case == "relative":
                    key = Path("operator.key")
                elif case == "mode":
                    key.chmod(0o644)
                elif case in ("symlink", "hardlink", "fifo"):
                    key = p["root"] / "unsafe.key"
                    if case == "symlink":
                        key.symlink_to(p["key"])
                    elif case == "hardlink":
                        os.link(p["key"], key)
                    else:
                        os.mkfifo(key, 0o600)
                elif case == "oversized":
                    key.write_bytes(b"x" * 65537)
                args = ["install"] + ([] if case == "missing-argument" else ["--enrollment-key-file", str(key)])
                with patch("os.geteuid", return_value=os.geteuid() + 1) if case == "foreign" else context_noop():
                    code, _, _ = self.command(args)
                self.assertEqual(code, 1)
                self.assertFalse((p["root"] / "Library").exists())

    def test_layout_and_config_conflicts_refuse_before_effects(self) -> None:
        for case in ("state-mode", "state-alias", "parent-alias", "plist-alias", "pending-alias", "config-conflict", "system-config", "frozen"):
            with self.subTest(case=case), self.home() as p:
                if case == "state-mode":
                    directories(p["state"])
                    p["state"].chmod(0o755)
                elif case in ("state-alias", "parent-alias"):
                    path = p["state"] if case == "state-alias" else p["root"] / "Library"
                    directories(path.parent)
                    path.symlink_to(p["root"] / "outside-missing")
                elif case in ("plist-alias", "pending-alias"):
                    path = p["plist"] if case == "plist-alias" else p["plist"].with_suffix(".plist.pending")
                    directories(path.parent)
                    path.symlink_to(p["key"])
                elif case == "config-conflict":
                    private(p["state"] / "config.json", b"different-secret-free-config")
                elif case == "system-config":
                    value = json.loads(p["source"].read_bytes())
                    value["system"] = True
                    private(p["source"], json.dumps(value).encode())
                with patch.object(sys, "frozen", True, create=True) if case == "frozen" else context_noop():
                    code, _, _ = self.command(["install", "--enrollment-key-file", str(p["key"])])
                self.assertEqual(code, 1)
                self.assertFalse((p["state"] / "enrollment.key").exists())
                self.assertEqual(p["key"].read_bytes(), KEY)

    def test_drift_observes_files_and_layout_without_secret_output_or_mutation(self) -> None:
        for case in ("config.json", "policy.json", "plist", "install.json", "state-mode", "missing-log"):
            with self.subTest(case=case), self.home() as p:
                self.install(p)
                if case == "state-mode":
                    p["state"].chmod(0o755)
                elif case == "missing-log":
                    (p["logs"] / f"{LABEL}.stdout.log").unlink()
                else:
                    path = p["plist"] if case == "plist" else p["state"] / case
                    path.write_bytes(KEY)
                before = p["plist"].read_bytes()
                self.assertEqual(self.command(["check-drift"])[0], 1)
                self.assertEqual(p["plist"].read_bytes(), before)

    def test_uninstall_bootouts_and_removes_managed_files_preserving_state_and_others(self) -> None:
        with self.home() as p:
            self.install(p)
            private(p["state"] / "trust-state.json", b"revocations")
            private(p["logs"] / "unrelated.log", b"unrelated")
            code, _, _ = self.command(["uninstall"], self.uninstall_rows(loaded=True))
            self.assertEqual(code, 0)
            self.assertFalse(p["plist"].exists())
            for name in ("config.json", "policy.json", "install.json"):
                self.assertFalse((p["state"] / name).exists())
            self.assertEqual((p["state"] / "enrollment.key").read_bytes(), KEY)
            self.assertEqual((p["state"] / "trust-state.json").read_bytes(), b"revocations")
            self.assertEqual((p["logs"] / "unrelated.log").read_bytes(), b"unrelated")
            self.assertEqual(p["key"].read_bytes(), KEY)
            self.assertFalse((p["logs"] / f"{LABEL}.stdout.log").exists())

    def test_purge_is_idempotent_for_full_partial_and_absent_installations(self) -> None:
        for case in ("full", "state-only", "plist-only", "absent"):
            with self.subTest(case=case), self.home() as p:
                if case == "full":
                    self.install(p)
                elif case == "state-only":
                    private(p["state"] / "nested/persisted", b"state")
                    private(p["state"] / "enrollment.key", KEY)
                elif case == "plist-only":
                    private(p["plist"], b"partial plist")
                private(p["root"] / "Library/Application Support/HappyRanch/other-state", b"unrelated")
                for _ in range(2):
                    self.assertEqual(self.command(["uninstall", "--purge"], self.uninstall_rows())[0], 0)
                    self.assertFalse(p["state"].exists())
                    self.assertFalse(p["plist"].exists())
                self.assertEqual(p["key"].read_bytes(), KEY)
                self.assertTrue(p["source"].is_file())
                self.assertTrue((p["root"] / "Library/Application Support/HappyRanch/other-state").is_file())

    def test_unsafe_purge_refuses_before_lifecycle_and_never_follows_outside(self) -> None:
        for case in ("symlink", "dangling", "hardlink", "fifo", "mode", "foreign", "depth", "entries"):
            with self.subTest(case=case), self.home() as p:
                self.install(p)
                entry = p["state"] / "unsafe"
                if case in ("symlink", "dangling"):
                    entry.symlink_to(p["key"] if case == "symlink" else p["root"] / "missing")
                elif case == "hardlink":
                    os.link(p["key"], entry)
                elif case == "fifo":
                    os.mkfifo(entry, 0o600)
                elif case == "depth":
                    directories(entry.joinpath(*(["nested"] * 33)))
                elif case == "entries":
                    for index in range(4096):
                        private(p["state"] / f"extra-{index}", b"state")
                else:
                    private(entry, b"state")
                    if case == "mode":
                        entry.chmod(0o644)
                with patch("os.geteuid", return_value=os.geteuid() + 1) if case == "foreign" else context_noop():
                    self.assertEqual(self.command(["uninstall", "--purge"])[0], 1)
                self.assertTrue(p["plist"].exists())
                self.assertTrue((p["state"] / "config.json").exists())
                self.assertEqual(p["key"].read_bytes(), KEY)

    def test_launchctl_failures_are_redacted_and_partial_install_can_be_purged(self) -> None:
        for phase in ("domain", "enable"):
            with self.subTest(phase=phase), self.home() as p:
                rows = [row("print", DOMAIN, code=1, output=KEY.decode())] if phase == "domain" else [
                    row("print", DOMAIN), row("enable", TARGET, code=1, output=TOKEN.decode()),
                ]
                code, _, error = self.command(["install", "--enrollment-key-file", str(p["key"])], rows)
                self.assertEqual(code, 1)
                self.assertTrue(error.startswith("error: "))
                self.assertEqual(p["state"].exists(), phase == "enable")
                self.assertEqual(self.command(["uninstall", "--purge"], self.uninstall_rows())[0], 0)
                self.assertFalse(p["state"].exists())
        with self.home() as p:
            self.install(p)
            original = p["plist"].read_bytes()
            code, _, _ = self.command(["uninstall", "--purge"], [
                row("print", DOMAIN), row("print", TARGET, output=RUNNING),
                row("bootout", TARGET, code=1, output=TOKEN.decode()),
            ])
            self.assertEqual(code, 1)
            self.assertEqual(p["plist"].read_bytes(), original)
            self.assertTrue((p["state"] / "config.json").exists())
            self.assertEqual((p["state"] / "enrollment.key").read_bytes(), KEY)


@contextmanager
def context_noop() -> Iterator[None]:
    yield


if __name__ == "__main__":
    unittest.main()

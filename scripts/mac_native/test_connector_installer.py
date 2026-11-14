"""Managed Darwin installer boundaries in the retained standalone venue."""
from __future__ import annotations

import io
import hashlib
import time
import json
import os
import plistlib
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator
from unittest.mock import Mock, patch

from runtime.remote_access import installer
from scripts.mac_native.test_connector_launchd import (
    DOMAIN, LABEL, RUNNING, TARGET, Runner, absent, row,
)

SIDECAR_LABEL = "ai.happyranch.tsnet-sidecar"
SIDE_TARGET = f"{DOMAIN}/{SIDECAR_LABEL}"


def missing(label: str) -> list:
    return [row("print", f"{DOMAIN}/{label}", code=113,
                output=f'Could not find service "{label}" in domain for user gui: {os.getuid()}'), row("print", DOMAIN)]


def override(label: str) -> tuple:
    return row("print-disabled", DOMAIN, output=f'disabled services = {{\n "{label}" => false\n}}')


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
            binary = root / "operator-sidecar"
            private(binary, b"#!/bin/sh\nexit 99\n")
            binary.chmod(0o700)
            side_config = root / "operator-sidecar.json"
            private(side_config, json.dumps(dict(ControlURL="https://control.example.invalid", RoleIdentity="home-sidecar-C6b",
                    ExpectedPeer="away-client-C6b", DERPPolicy="private-only", ListenAddr=":443",
                    ConnectorAddr="127.0.0.1:18443")).encode())
            paths = dict(root=root, source=source, policy=policy, key=key, state=state,
                         binary=binary, side_config=side_config, side_state=state.parent / "happyranch-tsnet-sidecar",
                         side_plist=root / f"Library/LaunchAgents/{SIDECAR_LABEL}.plist",
                         plist=root / f"Library/LaunchAgents/{LABEL}.plist",
                         logs=root / "Library/Logs/HappyRanch")
            with patch("sys.platform", "darwin"), patch.object(Path, "home", return_value=root), \
                    patch("os.path.expanduser", side_effect=lambda path: str(root) + path[1:] if path == "~" or path.startswith("~/") else path), \
                    patch.object(installer, "ManagedInstaller") as linux, \
                    patch("runtime.remote_access.launchd_installer.observe", return_value={"state": "clear", "message": ""}):
                # Base-production RED is safe even on a root Linux host.
                linux.return_value = Mock()
                self.paths = paths
                yield paths
                linux.assert_not_called()

    def command(self, args: list[str], rows: list | None = None, *, supply_binary: bool = True,
                expected_code: int | None = None) -> tuple[int, str, str]:
        if args[0] == "install":
            args = list(args)
            for option, path in (("--sidecar-binary", self.paths["binary"]), ("--sidecar-config", self.paths["side_config"])):
                if option == "--sidecar-binary" and not supply_binary:
                    continue
                if option not in args:
                    args += [option, str(path)]
        runner = Runner(self, rows or [])
        out, err = io.StringIO(), io.StringIO()
        with patch("runtime.remote_access.launchd_agent._default_run", runner), \
                redirect_stdout(out), redirect_stderr(err):
            try:
                code = installer.main(args)
            except SystemExit as exc:
                code = exc.code  # argparse is the observable CLI exit boundary too.
        if expected_code is not None:
            self.assertEqual((code, err.getvalue()), (expected_code, ""))
        self.assertFalse(runner.rows)
        self.assertNotIn(KEY.decode(), out.getvalue() + err.getvalue())
        self.assertNotIn(TOKEN.decode(), out.getvalue() + err.getvalue())
        return code, out.getvalue(), err.getvalue()

    def status_rows(self, *, side: str | None = None, connector: str | None = None) -> list:
        rows = []
        for label, output in ((LABEL, connector), (SIDECAR_LABEL, side)):
            rows += ([row("print", f"{DOMAIN}/{label}", output=output)] if output is not None else missing(label))
            rows.append(override(label))
        return rows

    def install(self, p: dict[str, Path]) -> None:
        code, output, error = self.command(
            ["install", "--enrollment-key-file", str(p["key"])],
            [row("print", DOMAIN), row("enable", TARGET), row("enable", SIDE_TARGET), *self.status_rows()], expected_code=0,
        )
        self.assertEqual((code, error), (0, ""))
        result = json.loads(output)
        self.assertFalse(result["ready"])
        self.assertEqual(result["sidecar"]["readiness_state"], "not-loaded")
        self.assertEqual(result["enrollment"], "staged")

    def uninstall_rows(self, *, loaded: bool = False) -> list:
        rows = [row("print", DOMAIN)]
        for label in (SIDECAR_LABEL, LABEL):
            target = f"{DOMAIN}/{label}"
            rows += ([row("print", target, output=RUNNING.replace(LABEL, label)), row("bootout", target)]
                     if loaded else missing(label))
            rows += missing(label)
        return rows + [row("disable", SIDE_TARGET), row("disable", TARGET)]

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
            self.assertEqual(self.command(["check-drift"], [row("print", DOMAIN), *self.status_rows()])[0], 0)
            code, _, _ = self.command(["install", "--enrollment-key-file", str(p["key"])])
            self.assertEqual(code, 1)  # Stored key cannot be silently replaced.

    def test_frozen_install_argv_and_default_or_explicit_sidecar(self) -> None:
        for explicit in (False, True):
            with self.subTest(explicit=explicit), self.home() as p:
                daemon = p["root"] / "HappyRanch.app/Contents/Resources/daemon/happyranch-daemon"
                bundled = p["root"] / "HappyRanch.app/Contents/Helpers/happyranch-tsnet-sidecar"
                for path in (daemon, bundled):
                    private(path, b"synthetic-executable")
                    path.chmod(0o755)
                with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(daemon)):
                    code, _, error = self.command(["install", "--enrollment-key-file", str(p["key"])],
                        [row("print", DOMAIN), row("enable", TARGET), row("enable", SIDE_TARGET), *self.status_rows()],
                        supply_binary=explicit, expected_code=0)
                    self.assertEqual((code, error), (0, ""))
                    self.assertEqual(plistlib.loads(p["plist"].read_bytes())["ProgramArguments"],
                        [str(daemon), "--remote-access", "run", "--managed", "--config", str(p["state"] / "config.json")])
                    self.assertEqual(plistlib.loads(p["side_plist"].read_bytes())["ProgramArguments"][0],
                        str(p["binary"] if explicit else bundled))
                    self.assertEqual(self.command(["check-drift"], [row("print", DOMAIN), *self.status_rows()])[0], 0)

    def test_executable_drift_requires_reinstall_without_effects(self) -> None:
        for frozen, case in ((False, "missing"), (True, "missing"), (True, "moved"), (True, "sidecar-missing")):
            with self.subTest(frozen=frozen, case=case), self.home() as p:
                original = p["root"] / "HappyRanch.app/Contents/Resources/daemon/happyranch-daemon"
                private(original, b"synthetic-executable")
                original.chmod(0o755)
                with patch.object(sys, "frozen", frozen, create=True), patch.object(sys, "executable", str(original)):
                    # Explicit binary keeps the missing connector case independent
                    # of the frozen default-path selection test above.
                    self.install(p)
                    before = self.snapshot(p)
                    if case == "missing":
                        original.unlink()
                    elif case == "sidecar-missing":
                        p["binary"].unlink()
                    else:
                        moved = p["root"] / "Moved.app/Contents/Resources/daemon/happyranch-daemon"
                        private(moved, b"synthetic-executable")
                        moved.chmod(0o755)
                        # The old bundle still exists: equality must be checked too.
                        sys.executable = str(moved)
                    with patch("runtime.remote_access.launchd_installer.DarwinManagedInstaller._status", return_value={}) as status, \
                            patch("runtime.remote_access.launchd_agent.LaunchdServiceManager.start") as start:
                        for command in ("check-drift", "start"):
                            code, _, error = self.command([command])
                            self.assertEqual(code, 1)
                            self.assertIn("reinstall", error)
                        status.assert_not_called()
                        start.assert_not_called()
                    self.assertEqual(self.snapshot(p), before)

    def test_hidden_dispatch_routes_before_daemon_startup(self) -> None:
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder) / "must-not-start"
            for args, expected, text in ((["run", "--help"], 0, "--managed"),
                                         (["installer", "--help"], 0, "--sidecar-config"),
                                         (["run", "--config", str(Path(folder) / "missing.json")], 1, "config file not found")):
                with self.subTest(args=args):
                    result = subprocess.run([sys.executable, "-B", "-m", "runtime.daemon", "--remote-access", *args],
                        cwd=root, env={**os.environ, "HAPPYRANCH_DAEMON_HOME": str(home)},
                        capture_output=True, text=True, timeout=20)
                    self.assertEqual(result.returncode, expected, result.stderr)
                    self.assertIn(text, result.stdout + result.stderr)
                    self.assertFalse(home.exists())

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
        for case in ("state-mode", "state-alias", "parent-alias", "plist-alias", "pending-alias", "pending", "config-conflict", "system-config", "frozen"):
            with self.subTest(case=case), self.home() as p:
                if case == "state-mode":
                    directories(p["state"])
                    p["state"].chmod(0o755)
                elif case in ("state-alias", "parent-alias"):
                    path = p["state"] if case == "state-alias" else p["root"] / "Library"
                    directories(path.parent)
                    path.symlink_to(p["root"] / "outside-missing")
                elif case == "pending":
                    private(p["plist"].with_suffix(".plist.pending"), b"partial plist")
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
            for name in ("config.json", "policy.json", "sidecar.json", "install.json"):
                self.assertFalse((p["state"] / name).exists())
            self.assertEqual((p["state"] / "enrollment.key").read_bytes(), KEY)
            self.assertEqual((p["state"] / "trust-state.json").read_bytes(), b"revocations")
            self.assertEqual((p["logs"] / "unrelated.log").read_bytes(), b"unrelated")
            self.assertEqual(p["key"].read_bytes(), KEY)
            self.assertFalse((p["logs"] / f"{LABEL}.stdout.log").exists())

    def test_purge_is_idempotent_for_full_partial_and_absent_installations(self) -> None:
        for case, purge in (("full", True), ("state-only", True), ("plist-only", True),
                            ("absent", True), ("pending", False), ("pending", True)):
            with self.subTest(case=case, purge=purge), self.home() as p:
                pending = p["plist"].with_suffix(".plist.pending")
                if case in ("full", "pending"):
                    self.install(p)
                    if case == "pending":
                        p["plist"].rename(pending)
                        private(p["state"] / "trust-state.json", b"revocations")
                        private(p["state"] / "readiness.json", b"readiness")
                elif case == "state-only":
                    private(p["state"] / "nested/persisted", b"state")
                    private(p["state"] / "enrollment.key", KEY)
                elif case == "plist-only":
                    private(p["plist"], b"partial plist")
                private(p["root"] / "Library/Application Support/HappyRanch/other-state", b"unrelated")
                for attempt in range(2):
                    # Even without a final plist, a loaded label must be stopped.
                    rows = self.uninstall_rows(loaded=case == "pending" and attempt == 0)
                    self.assertEqual(self.command(["uninstall"] + (["--purge"] if purge else []), rows)[0], 0)
                    if purge:
                        self.assertFalse(p["state"].exists())
                    else:
                        self.assertEqual((p["state"] / "enrollment.key").read_bytes(), KEY)
                        self.assertEqual((p["state"] / "trust-state.json").read_bytes(), b"revocations")
                        self.assertEqual((p["state"] / "readiness.json").read_bytes(), b"readiness")
                    for name in ("config.json", "policy.json", "sidecar.json", "install.json"):
                        self.assertFalse((p["state"] / name).exists())
                    self.assertFalse(p["plist"].exists())
                    self.assertFalse(pending.exists())
                self.assertEqual(p["key"].read_bytes(), KEY)
                self.assertTrue(p["source"].is_file())
                self.assertTrue((p["root"] / "Library/Application Support/HappyRanch/other-state").is_file())

    def test_unsafe_pending_refuses_before_lifecycle_and_preserves_files(self) -> None:
        for purge in (False, True):
            for case in ("symlink", "dangling", "hardlink", "fifo", "directory", "mode", "foreign-fd"):
                with self.subTest(case=case, purge=purge), self.home() as p:
                    self.install(p)
                    pending = p["plist"].with_suffix(".plist.pending")
                    if case in ("symlink", "dangling"):
                        pending.symlink_to(p["key"] if case == "symlink" else p["root"] / "missing")
                    elif case == "hardlink":
                        os.link(p["key"], pending)
                    elif case == "fifo":
                        os.mkfifo(pending, 0o600)
                    elif case == "directory":
                        pending.mkdir(mode=0o700)
                    else:
                        private(pending, b"partial plist")
                        if case == "mode":
                            pending.chmod(0o644)
                    identity = pending.lstat()
                    fstat = os.fstat

                    def observe(fd: int) -> os.stat_result:
                        info = fstat(fd)
                        if case == "foreign-fd" and (info.st_dev, info.st_ino) == (identity.st_dev, identity.st_ino):
                            values = list(info)
                            values[4] = info.st_uid + 1
                            return os.stat_result(values)
                        return info

                    # The foreign owner appears only on the opened pending fd:
                    # directory and named-file checks cannot mask this boundary.
                    with patch("os.fstat", side_effect=observe):
                        self.assertEqual(self.command(["uninstall"] + (["--purge"] if purge else []))[0], 1)
                    self.assertEqual(pending.lstat(), identity)
                    self.assertTrue(p["plist"].is_file())
                    self.assertTrue((p["state"] / "config.json").is_file())
                    self.assertEqual((p["state"] / "enrollment.key").read_bytes(), KEY)
                    self.assertEqual(p["key"].read_bytes(), KEY)

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
        for pending_only in (False, True):
            for purge in (False, True):
                for phase in ("bootout", "still-loaded"):
                    with self.subTest(pending_only=pending_only, purge=purge, phase=phase), self.home() as p:
                        self.install(p)
                        retained = p["plist"].with_suffix(".plist.pending") if pending_only else p["plist"]
                        if pending_only:
                            p["plist"].rename(retained)
                        original = retained.read_bytes()
                        rows = [row("print", DOMAIN), *missing(SIDECAR_LABEL), *missing(SIDECAR_LABEL), row("print", TARGET, output=RUNNING)]
                        rows += ([row("bootout", TARGET, code=1, output=TOKEN.decode())] if phase == "bootout" else [
                            row("bootout", TARGET), row("print", TARGET, output=RUNNING),
                        ])
                        with patch("runtime.remote_access.launchd_agent.time.monotonic",
                                   side_effect=[0, 0, 0, 30] if phase == "still-loaded" else lambda: 0):
                            code, _, _ = self.command(["uninstall"] + (["--purge"] if purge else []), rows)
                        self.assertEqual(code, 1)
                        self.assertEqual(retained.read_bytes(), original)
                        self.assertTrue((p["state"] / "config.json").exists())
                        self.assertEqual((p["state"] / "enrollment.key").read_bytes(), KEY)


    def test_sidecar_render_staging_and_validation(self) -> None:
        with self.home() as p:
            original = p["side_config"].read_bytes()
            self.install(p)
            self.assertTrue(p["side_plist"].is_file(), "install must stage the second agent")
            value = plistlib.loads(p["side_plist"].read_bytes())
            self.assertEqual(value["Label"], SIDECAR_LABEL)
            self.assertTrue(value["KeepAlive"])
            self.assertEqual(value["ThrottleInterval"], 10)
            self.assertTrue(value["Disabled"])
            self.assertEqual(value.get("EnvironmentVariables"), {"HOME": str(p["root"]),
                **({"TMPDIR": os.environ["TMPDIR"]} if "TMPDIR" in os.environ else {})})
            self.assertEqual(value["ProgramArguments"], [str(p["binary"]), "--config", str(p["state"] / "sidecar.json"),
                "--readiness-file", str(p["side_state"] / f"{SIDECAR_LABEL}.readiness.json"),
                "--watchdog-interval", "30.000000000s", "--credential-file", str(p["state"] / "enrollment.key")])
            staged = json.loads((p["state"] / "sidecar.json").read_bytes())
            self.assertEqual(staged["StateDir"], str(p["side_state"]))
            self.assertEqual(staged["CredentialFile"], str(p["state"] / "enrollment.key"))
            self.assertEqual(p["side_config"].read_bytes(), original)
            self.assertEqual(stat.S_IMODE(p["side_state"].stat().st_mode), 0o700)
            for path in (p["side_plist"], p["state"] / "sidecar.json"):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertNotIn(KEY, path.read_bytes())
                self.assertNotIn(TOKEN, path.read_bytes())
        for case in ("relative-binary", "binary-symlink", "binary-mode", "binary-nonexec", "config-mode", "config-symlink",
                     "config-unknown", "config-duplicate", "config-control", "config-peer", "config-role", "config-policy",
                     "config-listen", "config-connector", "interval"):
            with self.subTest(case=case), self.home() as p:
                binary, config = p["binary"], p["side_config"]
                if case == "relative-binary":
                    binary = Path("sidecar")
                elif case == "binary-symlink":
                    binary = p["root"] / "sidecar-link"
                    binary.symlink_to(p["binary"])
                elif case == "binary-mode":
                    binary.chmod(0o777)
                elif case == "binary-nonexec":
                    binary.chmod(0o600)
                elif case == "config-mode":
                    config.chmod(0o644)
                elif case == "config-symlink":
                    config = p["root"] / "config-link"
                    config.symlink_to(p["side_config"])
                elif case == "config-duplicate":
                    private(config, b'{"ControlURL":"https://a.invalid","ControlURL":"https://b.invalid"}')
                elif case == "interval":
                    value = json.loads(p["source"].read_bytes())
                    value["watchdog_sec"] = float("nan")
                    private(p["source"], json.dumps(value).encode())
                else:
                    field, replacement = {
                        "config-unknown": ("AuthKey", KEY.decode()), "config-control": ("ControlURL", "http://unsafe.invalid"),
                        "config-peer": ("ExpectedPeer", ""), "config-role": ("RoleIdentity", "wrong-role"),
                        "config-policy": ("DERPPolicy", "public"), "config-listen": ("ListenAddr", ":0"),
                        "config-connector": ("ConnectorAddr", "127.0.0.1:8765"),
                    }[case]
                    value = json.loads(config.read_bytes())
                    value[field] = replacement
                    private(config, json.dumps(value).encode())
                code, _, _ = self.command(["install", "--enrollment-key-file", str(p["key"]),
                                          "--sidecar-binary", str(binary), "--sidecar-config", str(config)])
                self.assertEqual(code, 1)
                self.assertFalse((p["root"] / "Library").exists())

    def start_rows(self) -> list:
        return [override(LABEL), *missing(LABEL), row("bootstrap", DOMAIN, str(self.paths["plist"])),
                override(SIDECAR_LABEL), *missing(SIDECAR_LABEL), row("bootstrap", DOMAIN, str(self.paths["side_plist"]))]

    def stop_rows(self) -> list:
        result = []
        for label in (SIDECAR_LABEL, LABEL):
            result += [row("print", f"{DOMAIN}/{label}", output=RUNNING.replace(LABEL, label)),
                       row("bootout", f"{DOMAIN}/{label}"), *missing(label)]
        return result

    def snapshot(self, p: dict[str, Path]) -> dict:
        return {str(path.relative_to(p["root"])): path.read_bytes()
                for path in (p["plist"], p["side_plist"], *p["state"].iterdir(), *p["side_state"].iterdir(), *p["logs"].iterdir())
                if path.is_file()}

    def test_composite_settle_orders_and_deadline_retains_files(self) -> None:
        for operation in ("stop", "restart", "uninstall", "purge"):
            for stuck in (None, SIDECAR_LABEL, LABEL):
                with self.subTest(operation=operation, stuck=stuck), self.home() as p:
                    self.install(p)
                    before = self.snapshot(p)
                    clock = [0.0]
                    started = {}
                    settled = set()
                    def run(argv: list[str], *, timeout: float = 10.0) -> tuple[int, str]:
                        self.assertEqual(self.snapshot(p), before, "files changed before both writers settled")
                        verb, target = argv[1:3]
                        if verb == "print" and target == DOMAIN:
                            return 0, "domain present"
                        label = target.rsplit("/", 1)[-1]
                        if verb == "bootout":
                            if label == LABEL:
                                self.assertIn(SIDECAR_LABEL, settled, "connector stopped before sidecar settled")
                            started[label] = clock[0]
                            return 0, ""
                        self.assertEqual(verb, "print")
                        if label in started:
                            if clock[0] - started[label] >= 1 and label != stuck:
                                settled.add(label)
                                return 113, f'Could not find service "{label}" in domain'
                            return 0, RUNNING.replace(LABEL, label).replace("state = running", "state = SIGTERMed")
                        return 0, RUNNING.replace(LABEL, label)
                    # Once stop has proved both writers absent, retain the existing
                    # exact disable/start argv fixture for the rest of the operation.
                    tail = Runner(self, ([row("disable", SIDE_TARGET), row("disable", TARGET)]
                                  if operation in ("uninstall", "purge") else
                                  self.start_rows() if operation == "restart" else []))
                    def observed(argv: list[str], *, timeout: float = 10.0) -> tuple[int, str]:
                        if len(settled) == 2 and argv[1] != "print":
                            return tail(argv, timeout=timeout)
                        if tail.calls:
                            return tail(argv, timeout=timeout)
                        return run(argv, timeout=timeout)
                    def sleep(seconds: float) -> None:
                        self.assertGreater(seconds, 0)
                        self.assertLessEqual(seconds, .5)
                        clock[0] += seconds
                    args = ["uninstall", "--purge"] if operation == "purge" else [operation]
                    out, err = io.StringIO(), io.StringIO()
                    with patch("runtime.remote_access.launchd_agent._default_run", side_effect=observed), \
                            patch("runtime.remote_access.launchd_agent.time.monotonic", side_effect=lambda: clock[0]), \
                            patch("runtime.remote_access.launchd_agent.time.sleep", side_effect=sleep), \
                            redirect_stdout(out), redirect_stderr(err):
                        code = installer.main(args)
                    self.assertEqual(code, 1 if stuck else 0)
                    if stuck:
                        self.assertIn(stuck, started, "stop never reached the requested writer")
                        self.assertEqual(clock[0] - started[stuck], 30)
                        self.assertEqual(self.snapshot(p), before)
                        self.assertFalse(tail.calls)
                        if stuck == SIDECAR_LABEL:
                            self.assertNotIn(LABEL, started)
                    else:
                        self.assertEqual(settled, {SIDECAR_LABEL, LABEL})
                        self.assertFalse(tail.rows)
                        if operation in ("uninstall", "purge"):
                            self.assertFalse(p["plist"].exists())
                            self.assertFalse(p["side_plist"].exists())
                            self.assertEqual(p["state"].exists(), operation != "purge")
                        else:
                            self.assertEqual(self.snapshot(p), before)

    def test_composite_lifecycle_orders_and_partial_failures(self) -> None:
        for command in ("start", "stop", "restart", "enable", "disable"):
            with self.subTest(command=command), self.home() as p:
                self.install(p)
                before = self.snapshot(p)
                rows = {"start": self.start_rows(), "stop": self.stop_rows(),
                        "restart": self.stop_rows() + self.start_rows(),
                        "enable": [row("enable", TARGET), row("enable", SIDE_TARGET)],
                        "disable": [row("disable", SIDE_TARGET), row("disable", TARGET)]}[command]
                self.assertEqual(self.command([command], [row("print", DOMAIN), *rows])[0], 0)
                self.assertEqual(self.snapshot(p), before)
        for command in ("stop", "restart", "uninstall"):
            for phase in ("sidecar-bootout", "sidecar-still-loaded", "connector-bootout"):
                for purge in ((False, True) if command == "uninstall" else (False,)):
                    with self.subTest(command=command, phase=phase, purge=purge), self.home() as p:
                        self.install(p)
                        before = self.snapshot(p)
                        rows = [row("print", DOMAIN), row("print", SIDE_TARGET, output=RUNNING.replace(LABEL, SIDECAR_LABEL))]
                        if phase == "sidecar-bootout":
                            rows += [row("bootout", SIDE_TARGET, code=1, output=KEY.decode())]
                        elif phase == "sidecar-still-loaded":
                            rows += [row("bootout", SIDE_TARGET), row("print", SIDE_TARGET, output=RUNNING.replace(LABEL, SIDECAR_LABEL))]
                            # Advance the real stop deadline on its first settled observation.
                            # Keep the original refusal/retention assertion and native argv.
                        else:
                            rows += [row("bootout", SIDE_TARGET), *missing(SIDECAR_LABEL),
                                     row("print", TARGET, output=RUNNING), row("bootout", TARGET, code=1, output=TOKEN.decode())]
                        with patch("runtime.remote_access.launchd_agent.time.monotonic",
                                   side_effect=[0, 0, 0, 30] if phase == "sidecar-still-loaded" else lambda: 0):
                            self.assertEqual(self.command([command] + (["--purge"] if purge else []), rows)[0], 1)
                        self.assertEqual(self.snapshot(p), before)
        for fail_sidecar in (False, True):
            with self.subTest(fail_sidecar=fail_sidecar), self.home() as p:
                self.install(p)
                rows = [row("print", DOMAIN)]
                if fail_sidecar:
                    rows += self.start_rows()[:4]
                label = SIDECAR_LABEL if fail_sidecar else LABEL
                path = p["side_plist"] if fail_sidecar else p["plist"]
                rows += [override(label), *missing(label), row("bootstrap", DOMAIN, str(path), code=1, output=TOKEN.decode())]
                self.assertEqual(self.command(["start"], rows)[0], 1)
                self.assertTrue(p["plist"].exists())
                self.assertTrue(p["side_plist"].exists())

    def test_composite_status_matrix_and_drift_readiness(self) -> None:
        for case in ("not-installed", "not-loaded", "starting", "running-not-ready", "ready", "stale", "corrupt", "pid-mismatch", "missing", "connector-not-ready"):
            with self.subTest(case=case), self.home() as p:
                self.install(p)
                now = time.time()
                record = dict(pid=1234, process_started_at=now - 100, ready=True,
                              last_healthy_wall_time=now, sequence=2, reason="healthy")
                private(p["state"] / f"{LABEL}.readiness.json", json.dumps(record).encode())
                if case == "running-not-ready":
                    record.update(ready=False, reason="waiting-for-readiness", last_healthy_wall_time=None)
                elif case == "stale":
                    record["last_healthy_wall_time"] = now - 60
                elif case == "pid-mismatch":
                    record["pid"] = 9876
                path = p["side_state"] / f"{SIDECAR_LABEL}.readiness.json"
                if case != "missing":
                    private(path, b"corrupt-synthetic" if case == "corrupt" else json.dumps(record).encode())
                if case == "not-installed":
                    p["side_plist"].unlink()
                if case == "connector-not-ready":
                    (p["state"] / f"{LABEL}.readiness.json").unlink()
                side_output = None if case in ("not-loaded", "not-installed") else RUNNING.replace(LABEL, SIDECAR_LABEL)
                if case == "starting":
                    side_output = side_output.replace("state = running", "state = spawn scheduled").replace("pid = 1234", "pid = 0")
                code, output, _ = self.command(["status"], [row("print", DOMAIN), *self.status_rows(side=side_output, connector=RUNNING)])
                self.assertEqual(code, 0)
                result = json.loads(output)
                expected = {"stale": "stale-readiness", "corrupt": "record-corrupt", "missing": "record-missing",
                            "connector-not-ready": "ready"}.get(case, case)
                self.assertEqual(result["sidecar"]["readiness_state"], expected)
                self.assertEqual(result["sidecar"]["ready"], case in ("ready", "connector-not-ready"))
                self.assertEqual(result["ready"], case == "ready")
                if case not in ("not-installed",):
                    code, output, _ = self.command(["check-drift"], [row("print", DOMAIN), *self.status_rows(side=side_output, connector=RUNNING)])
                    self.assertEqual(code, 0)
                    self.assertEqual(json.loads(output)["sidecar"]["ready"], case in ("ready", "connector-not-ready"))
                    self.assertEqual(json.loads(output)["sidecar"]["readiness_state"], expected)

    def consume(self, p: dict[str, Path]) -> bytes:
        value = json.loads(p["side_config"].read_bytes())
        marker = (hashlib.sha256((value["RoleIdentity"] + "\0" + value["ControlURL"]).encode()).hexdigest() + "\n").encode()
        private(p["side_state"] / "credential.consumed", marker)
        (p["state"] / "enrollment.key").unlink()
        private(p["side_state"] / "tailscaled.state", b"synthetic-node-state")
        return marker

    def test_enrollment_matrix_reinstall_drift_and_removal(self) -> None:
        for case in ("key-only", "marker-only", "both", "neither", "invalid-marker"):
            for purge in (False, True):
                with self.subTest(case=case, purge=purge), self.home() as p:
                    self.install(p)
                    if case in ("marker-only", "both", "invalid-marker"):
                        marker = self.consume(p)
                        if case == "both":
                            private(p["state"] / "enrollment.key", KEY)
                        elif case == "invalid-marker":
                            private(p["side_state"] / "credential.consumed", b"invalid-marker")
                    elif case == "neither":
                        (p["state"] / "enrollment.key").unlink()
                    valid = case in ("key-only", "marker-only")
                    code, output, _ = self.command(["check-drift"], [row("print", DOMAIN), *self.status_rows()] if valid else [])
                    self.assertEqual(code, 0 if valid else 1)
                    if valid:
                        self.assertEqual(json.loads(output)["enrollment"], "staged" if case == "key-only" else "consumed")
                    before = self.snapshot(p)
                    args = ["install"] + ([] if case == "marker-only" else ["--enrollment-key-file", str(p["key"])])
                    success = case in ("marker-only", "neither")
                    rows = [row("print", DOMAIN), row("enable", TARGET), row("enable", SIDE_TARGET), *self.status_rows()] if success else []
                    self.assertEqual(self.command(args, rows)[0], 0 if success else 1)
                    if case == "marker-only":
                        self.assertEqual(self.snapshot(p), before)
                        self.assertFalse((p["state"] / "enrollment.key").exists())
                        self.assertEqual((p["side_state"] / "credential.consumed").read_bytes(), marker)
                        self.assertEqual(self.command(["install", "--enrollment-key-file", str(p["key"])])[0], 1)
                    if case in ("both", "invalid-marker"):
                        self.assertEqual(self.command(["start"])[0], 1)
                        self.assertEqual(self.snapshot(p), before)
                    self.assertEqual(self.command(["uninstall"] + (["--purge"] if purge else []), self.uninstall_rows(loaded=True))[0], 0)
                    if purge:
                        self.assertFalse(p["side_state"].exists())
                        self.assertFalse(p["state"].exists())
                    elif case in ("marker-only", "both", "invalid-marker"):
                        self.assertTrue((p["side_state"] / "credential.consumed").is_file())
                        self.assertEqual((p["side_state"] / "tailscaled.state").read_bytes(), b"synthetic-node-state")
                    self.assertEqual(self.command(["uninstall"] + (["--purge"] if purge else []), self.uninstall_rows())[0], 0)
                    self.assertEqual(p["key"].read_bytes(), KEY)

    def test_sidecar_drift_and_unsafe_state_pending_refuse_before_effects(self) -> None:
        for case in ("sidecar-config", "sidecar-plist", "sidecar-log", "binary-missing", "binary-symlink", "state-mode",
                     "state-alias", "state-file-symlink", "state-hardlink", "state-fifo", "pending-symlink", "pending-mode"):
            with self.subTest(case=case), self.home() as p:
                self.install(p)
                if case == "sidecar-config":
                    (p["state"] / "sidecar.json").write_bytes(b"different")
                elif case == "sidecar-plist":
                    p["side_plist"].write_bytes(b"different")
                elif case == "sidecar-log":
                    (p["logs"] / f"{SIDECAR_LABEL}.stdout.log").unlink()
                elif case.startswith("binary"):
                    p["binary"].unlink()
                    if case == "binary-symlink":
                        p["binary"].symlink_to(p["key"])
                elif case == "state-mode":
                    p["side_state"].chmod(0o755)
                elif case == "state-alias":
                    p["side_state"].rmdir()
                    p["side_state"].symlink_to(p["root"] / "missing")
                elif case == "state-file-symlink":
                    (p["side_state"] / "unsafe").symlink_to(p["key"])
                elif case == "state-hardlink":
                    os.link(p["key"], p["side_state"] / "unsafe")
                elif case == "state-fifo":
                    os.mkfifo(p["side_state"] / "unsafe", 0o600)
                elif case == "pending-symlink":
                    p["side_plist"].with_suffix(".plist.pending").symlink_to(p["key"])
                else:
                    private(p["side_plist"].with_suffix(".plist.pending"), b"partial")
                    p["side_plist"].with_suffix(".plist.pending").chmod(0o644)
                self.assertEqual(self.command(["check-drift"])[0], 1)
                self.assertEqual(self.command(["start"])[0], 1)
                if case.startswith(("state", "pending")):
                    for purge in (False, True):
                        self.assertEqual(self.command(["uninstall"] + (["--purge"] if purge else []))[0], 1)
                    self.assertTrue(p["plist"].is_file())
                    self.assertTrue(p["side_plist"].is_file())
                self.assertEqual(p["key"].read_bytes(), KEY)

    def test_sidecar_partial_pending_removal_and_stop_failure_retention(self) -> None:
        for purge in (False, True):
            for fails in (False, True):
                with self.subTest(purge=purge, fails=fails), self.home() as p:
                    self.install(p)
                    pending = p["side_plist"].with_suffix(".plist.pending")
                    p["side_plist"].rename(pending)
                    original = pending.read_bytes()
                    rows = ([row("print", DOMAIN), row("print", SIDE_TARGET, output=RUNNING.replace(LABEL, SIDECAR_LABEL)),
                             row("bootout", SIDE_TARGET, code=1, output=TOKEN.decode())] if fails else self.uninstall_rows(loaded=True))
                    self.assertEqual(self.command(["uninstall"] + (["--purge"] if purge else []), rows)[0], 1 if fails else 0)
                    if fails:
                        self.assertEqual(pending.read_bytes(), original)
                        self.assertTrue(p["plist"].is_file())
                        self.assertTrue((p["state"] / "sidecar.json").is_file())
                    else:
                        self.assertFalse(pending.exists())
                        self.assertFalse(p["plist"].exists())
                        self.assertEqual(self.command(["uninstall"] + (["--purge"] if purge else []), self.uninstall_rows())[0], 0)


class ManagedLinuxDebInstaller(unittest.TestCase):
    """The retained installer venue also drives Linux root= with fake commands."""

    @contextmanager
    def host(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            prefix = root / 'opt/happyranch-desktop'
            for name in ('bin/happyranch-tsnet-sidecar', 'python/bin/python3.12'):
                path = prefix / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'#!/bin/sh\nexit 99\n')
                path.chmod(0o755)
            (prefix / 'python/bin/python3').symlink_to('python3.12')
            for path in (root / 'opt', prefix, *[p for p in prefix.rglob('*') if p.is_dir()]):
                path.chmod(0o755)
            operator = root / 'operator'
            operator.mkdir(mode=0o700)
            private(operator / 'daemon.token', TOKEN)
            private(operator / 'enrollment.key', KEY)
            connector = dict(tenant_id='tenant-B1a', home_id='home-B1a', connector_id='connector-B1a',
                daemon_port=8766, daemon_token_path='/etc/happyranch/daemon.token',
                policy_path='/etc/happyranch/policy.json', state_path='/var/lib/happyranch-connector/trust-state.json',
                system=True, service_user='happyranch', service_group='happyranch', managed=dict(bind_port=18443))
            sidecar = dict(StateDir='/var/lib/happyranch-tsnet-sidecar', ControlURL='https://control.example.invalid:8443',
                RoleIdentity='home-sidecar-B1a', ExpectedPeer='synthetic-peer-B1a', DERPPolicy='private-only',
                ListenAddr=':443', ConnectorAddr='127.0.0.1:18443')
            artifact = json.loads((Path(__file__).resolve().parents[2] / 'runtime/remote_access/route_policy.json').read_text())
            policy = dict(schema_version=1, artifact_version=artifact['version'], revision=1, state='active',
                issued_at=(datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat(), max_age_seconds=3600, artifact=artifact)
            for name, value in (('connector', connector), ('sidecar', sidecar), ('policy', policy)):
                private(operator / f'{name}.json', json.dumps(value).encode())
            manager = Mock()
            manager.show_properties.side_effect = lambda name, _: dict(FragmentPath=str(root / 'etc/systemd/system' / name),
                DropInPaths='', NeedDaemonReload='no', ActiveState='active')
            owner = (os.geteuid(), os.getegid())
            user = SimpleNamespace(pw_uid=owner[0], pw_gid=owner[1], pw_shell='/usr/sbin/nologin', pw_dir='/nonexistent')
            options = dict(root=root, root_owner=owner, manager=manager, layout='deb',
                lookup_user=lambda _: user, lookup_group=lambda _: SimpleNamespace(gr_gid=owner[1]))
            instance = installer.ManagedInstaller(**options)
            inputs = dict(connector_config=operator / 'connector.json', sidecar_config=operator / 'sidecar.json',
                policy_file=operator / 'policy.json', daemon_token_source=operator / 'daemon.token')
            yield root, instance, inputs, manager, options

    def test_linux_unit_layout_bytes(self):
        # Source digests captured at base 10dbfaf20; deb changes only ExecStart.
        expected = {
            'source': ('417e9b7ed1476778c57c28393b1968ab4933649b085b56b7536800a59013a6f5',
                       '39b1590d41608b9c2f6bbbff5225d5fa4a2af0ba7761812dc68540b5da9be931'),
            'deb': ('8722f992e0c31b8fe58ed886a973d17d3bf373822445ae46355fa2b0bd09955b',
                    '005c001b6ed75959782bee9d5ca08746e504db62371994a46745b1905d528aae')}
        for layout, hashes in expected.items():
            with self.subTest(layout=layout):
                units = installer.managed_units(layout)
                for name, digest in zip(installer.UNITS, (*hashes, '6db43ed94332e3c23b1ae71fef4272aad19c2c90f72360cd8493dc9934e114ca')):
                    self.assertEqual(hashlib.sha256(units[name].encode()).hexdigest(), digest)
        self.assertEqual(installer.managed_units(), installer.managed_units('source'))
        from runtime.remote_access.systemd_unit import SidecarUnitSpec, render_sidecar_unit
        self.assertIn('ExecStart=/custom/sidecar --config ', render_sidecar_unit(SidecarUnitSpec(binary_path='/custom/sidecar')))

    def test_linux_deb_provision_install_and_drift(self):
        with self.host() as (root, instance, inputs, manager, options):
            prefix = root / 'opt/happyranch-desktop'
            before = {p: p.lstat() for p in (prefix, *prefix.rglob('*'))}
            real_installer = installer.ManagedInstaller
            def factory(**kwargs):
                return real_installer(**(options | kwargs))
            arguments = ['provision', '--layout', 'deb']
            for name, path in inputs.items():
                arguments += ['--' + name.replace('_', '-'), str(path)]
            with patch.object(installer, 'ManagedInstaller', side_effect=factory), patch('sys.platform', 'linux'):
                self.assertEqual(installer.main(arguments), 0)
            manager.assert_not_called()
            self.assertEqual(manager.method_calls, [])
            config_dir = root / 'etc/happyranch'
            for name in ('connector', 'sidecar', 'policy'):
                path = config_dir / f'{name}.json'
                expected = json.loads((root / 'operator' / path.name).read_bytes())
                if name != 'policy':
                    expected['excluded_daemon_ports' if name == 'connector' else 'ExcludedDaemonPorts'] = [8765, 8766]
                self.assertEqual(json.loads(path.read_bytes()), expected)
                self.assertEqual((path.stat().st_uid, path.stat().st_gid, stat.S_IMODE(path.stat().st_mode)), (*options['root_owner'], 0o640))
            self.assertEqual((config_dir / 'daemon.token').read_bytes(), TOKEN)
            self.assertEqual(stat.S_IMODE((config_dir / 'daemon.token').stat().st_mode), 0o600)
            record = json.loads((config_dir / 'provision.json').read_text())
            self.assertEqual(record['token_source'], str(inputs['daemon_token_source']))
            self.assertEqual(record['token_sha256'], hashlib.sha256(TOKEN).hexdigest())
            self.assertTrue((root / 'var/lib/happyranch-connector').is_dir())
            self.assertEqual(stat.S_IMODE((root / 'var/lib/happyranch-tsnet-sidecar').stat().st_mode), 0o700)
            with patch.object(installer, 'ManagedInstaller', side_effect=factory), patch('sys.platform', 'linux'):
                self.assertEqual(installer.main(['install', '--layout', 'deb', '--enrollment-key-file', str(root / 'operator/enrollment.key')]), 0)
            manager.start.assert_called_once_with(installer.UNITS[2], timeout=250.0)
            self.assertFalse((config_dir / 'enrollment.key').exists())
            self.assertFalse((root / 'etc/credstore/happyranch-tsnet-enrollment').is_symlink())
            fresh = installer.ManagedInstaller(**options)
            self.assertEqual(set(fresh.check_drift()), set(installer.UNITS))
            default_layout = installer.ManagedInstaller(**(options | {'layout': 'source'}))
            self.assertEqual(set(default_layout.check_drift()), set(installer.UNITS))
            self.assertEqual({p: p.lstat() for p in before}, before)
            self.assertFalse((root / 'opt/happyranch').exists())
            with self.assertRaisesRegex(installer.InstallerError, 'destination exists'):
                instance.provision(**inputs)

    def test_linux_deb_payload_refusals(self):
        access_cases = {
            'sidecar-0744': ('bin/happyranch-tsnet-sidecar', 0o744, 'execute'),
            'python-0744': ('python/bin/python3.12', 0o744, 'execute'),
            'account-created-0744': ('bin/happyranch-tsnet-sidecar', 0o744, 'execute'),
            'prefix-0700': ('', 0o700, 'search'),
            'bin-0700': ('bin', 0o700, 'search'),
            'python-dir-0700': ('python', 0o700, 'search'),
            'python-bin-0700': ('python/bin', 0o700, 'search'),
            'owner-no-execute': ('bin/happyranch-tsnet-sidecar', 0o645, 'execute'),
            'group-no-execute': ('bin/happyranch-tsnet-sidecar', 0o745, 'execute'),
            'supplementary-no-execute': ('python/bin/python3.12', 0o745, 'execute'),
        }
        positive_cases = ('payload-0755', 'primary-group-0750', 'supplementary-0750')
        for case in ('missing-sidecar', 'writable-prefix', 'foreign-prefix', 'missing-python', 'python-link',
                     *access_cases, *positive_cases):
            with self.subTest(case=case), self.host() as (root, instance, inputs, manager, options):
                prefix = root / 'opt/happyranch-desktop'
                if case == 'missing-sidecar':
                    (prefix / 'bin/happyranch-tsnet-sidecar').unlink()
                elif case == 'writable-prefix':
                    prefix.chmod(0o775)
                elif case == 'missing-python':
                    (prefix / 'python/bin/python3.12').unlink()
                elif case == 'python-link':
                    (prefix / 'python/bin/python3').unlink()
                    (prefix / 'python/bin/python3').symlink_to('/bin/true')
                elif case in access_cases:
                    relative, mode, permission = access_cases[case]
                    (prefix / relative).chmod(mode)
                if case in ('primary-group-0750', 'supplementary-0750'):
                    for path in (prefix, *[p for p in prefix.rglob('*') if not p.is_symlink()]):
                        path.chmod(0o750)
                original = os.fstat
                inode = prefix.stat().st_ino
                owners = {}
                def chown(fd, uid, gid):
                    info = original(fd)
                    owners[info.st_dev, info.st_ino] = (uid, gid)
                def metadata(fd):
                    info = original(fd)
                    values = list(info)
                    values[4:6] = owners.get((info.st_dev, info.st_ino), (info.st_uid, info.st_gid))
                    if case == 'foreign-prefix' and info.st_ino == inode:
                        values[4] += 1  # Simulate foreign ownership on this exact directory only.
                    return os.stat_result(values) if values[4:6] != list(info)[4:6] else info
                # Distinct synthetic account IDs model root-owned payload access
                # without chown, useradd or any real service/privilege changes.
                uid, gid = options['root_owner']
                if case in (*access_cases, *positive_cases) and case != 'owner-no-execute':
                    uid += 10000
                    if case not in ('group-no-execute', 'primary-group-0750'):
                        gid += 10000
                user = SimpleNamespace(pw_uid=uid, pw_gid=gid, pw_shell='/usr/sbin/nologin', pw_dir='/nonexistent')
                created = case != 'account-created-0744'
                account_commands = []
                def lookup_user(_):
                    if not created:
                        raise KeyError('happyranch')
                    return user
                def run(command, **_):
                    nonlocal created
                    self.assertEqual(command[0], 'useradd')
                    account_commands.append(command)
                    created = True
                    return 0, ''
                instance._lookup_user = lookup_user
                instance._lookup_group = lambda _: SimpleNamespace(gr_gid=gid)
                instance._run = run
                groups = ([gid, options['root_owner'][1]]
                          if case in ('supplementary-no-execute', 'supplementary-0750') else [gid])
                with patch('os.fstat', side_effect=metadata), patch('os.fchown', side_effect=chown), \
                        patch('os.getgrouplist', return_value=groups):
                    if case in positive_cases:
                        instance.provision(**inputs)
                        instance.install(enrollment_key_file=root / 'operator/enrollment.key')
                        self.assertTrue((root / 'etc/systemd/system' / installer.UNITS[0]).is_file())
                        manager.start.assert_called_once_with(installer.UNITS[2], timeout=250.0)
                        continue
                    for operation in ('provision', 'install'):
                        with self.subTest(operation=operation):
                            with self.assertRaises(installer.InstallerError) as error:
                                if operation == 'provision':
                                    instance.provision(**inputs)
                                else:
                                    instance.install(enrollment_key_file=root / 'operator/enrollment.key')
                            if case in access_cases:
                                self.assertIn(str(prefix / relative), str(error.exception))
                                self.assertIn(permission, str(error.exception))
                            self.assertFalse((root / 'etc/happyranch').exists())
                            self.assertFalse((root / 'etc/systemd/system').exists())
                            self.assertEqual(manager.method_calls, [])
                            manager.start.assert_not_called()
                    self.assertEqual(len(account_commands), int(case == 'account-created-0744'))

    def test_linux_provision_input_refusals(self):
        for case in ('missing-policy', 'invalid-policy', 'expired-policy', 'home-state', 'root-sidecar-state',
                     'token-mode', 'literal-8765', 'sidecar-8765', 'duplicate-json',
                     'daemon-8766', 'daemon-custom', 'excluded-ingress', 'sidecar-excluded-ingress',
                     'sidecar-mismatch', 'sidecar-leading-zero', 'sidecar-invalid-exclusions'):
            with self.subTest(case=case), self.host() as (root, instance, inputs, manager, _):
                if case == 'missing-policy':
                    inputs['policy_file'].unlink()
                elif case in ('invalid-policy', 'expired-policy'):
                    value = json.loads(inputs['policy_file'].read_text())
                    if case == 'invalid-policy':
                        value['artifact'] = {}
                    else:
                        value['issued_at'] = '2000-01-01T00:00:00+00:00'
                    private(inputs['policy_file'], json.dumps(value).encode())
                elif case in ('home-state', 'literal-8765'):
                    value = json.loads(inputs['connector_config'].read_text())
                    if case == 'home-state':
                        value.pop('state_path')
                    else:
                        value['managed']['bind_port'] = 8765
                    private(inputs['connector_config'], json.dumps(value).encode())
                elif case in ('root-sidecar-state', 'sidecar-8765'):
                    value = json.loads(inputs['sidecar_config'].read_text())
                    value['StateDir' if case == 'root-sidecar-state' else 'ConnectorAddr'] = '/' if case == 'root-sidecar-state' else '127.0.0.1:8765'
                    private(inputs['sidecar_config'], json.dumps(value).encode())
                elif case == 'duplicate-json':
                    private(inputs['sidecar_config'], b'{"StateDir":"a","StateDir":"b"}')
                elif case in ('daemon-8766', 'daemon-custom', 'excluded-ingress'):
                    connector = json.loads(inputs['connector_config'].read_text())
                    if case.startswith('daemon'):
                        port = 8766 if case == 'daemon-8766' else 18766
                        connector['daemon_port'] = connector['managed']['bind_port'] = port
                        sidecar = json.loads(inputs['sidecar_config'].read_text())
                        sidecar['ConnectorAddr'] = f'127.0.0.1:{port}'
                        private(inputs['sidecar_config'], json.dumps(sidecar).encode())
                    else:
                        connector['excluded_daemon_ports'] = [18443]
                    private(inputs['connector_config'], json.dumps(connector).encode())
                elif case.startswith('sidecar-'):
                    sidecar = json.loads(inputs['sidecar_config'].read_text())
                    if case == 'sidecar-excluded-ingress':
                        sidecar['ExcludedDaemonPorts'] = [18443]
                    elif case == 'sidecar-invalid-exclusions':
                        sidecar['ExcludedDaemonPorts'] = [True]
                    else:
                        sidecar['ConnectorAddr'] = '127.0.0.1:9443' if case == 'sidecar-mismatch' else '127.0.0.1:018443'
                    private(inputs['sidecar_config'], json.dumps(sidecar).encode())
                else:
                    inputs['daemon_token_source'].chmod(0o644)
                with self.assertRaises(installer.InstallerError) as error:
                    instance.provision(**inputs)
                self.assertNotIn(TOKEN.decode(), str(error.exception))
                self.assertFalse((root / 'etc/happyranch').exists())
                self.assertEqual(manager.method_calls, [])

    def test_linux_connector_config_port_contract(self):
        from runtime.remote_access.supervisor import ConnectorConfig, ConnectorConfigError, ConnectorSupervisor

        cases = [({}, True), ({'excluded_daemon_ports': [1, 65535, 8765, 8766]}, True),
                 ({'managed': {'bind_port': 8765}}, False),
                 ({'managed': {'bind_port': 8766}}, False),
                 ({'daemon_port': 18443}, False),
                 ({'excluded_daemon_ports': [18443]}, False)]
        cases += [({'excluded_daemon_ports': value}, False) for value in
                  (None, 8766, '8766', {}, [True], [False], [0], [-1], [65536], [8766.0], ['8766'])]
        for settings, valid in cases:
            with self.subTest(settings=settings), self.host() as (root, _, inputs, _, _):
                original = json.loads(inputs['connector_config'].read_text())
                private(inputs['connector_config'], json.dumps(original | settings).encode())
                if not valid:
                    with self.assertRaises(ConnectorConfigError):
                        ConnectorConfig.from_file(inputs['connector_config'])
                    continue
                cfg = ConnectorConfig.from_file(inputs['connector_config'])
                self.assertEqual(cfg.excluded_daemon_ports, settings.get('excluded_daemon_ports', []))
                # Source-layout staging must carry optional exclusions to the service.
                cfg.managed_dir_root = str(root / 'source-state')
                staged = ConnectorSupervisor(config=cfg)._managed_config()
                path = root / 'roundtrip.json'
                staged.to_file(path)
                self.assertEqual(ConnectorConfig.from_file(path).excluded_daemon_ports, cfg.excluded_daemon_ports)

    def test_linux_provision_preserves_operator_exclusions(self):
        with self.host() as (root, instance, inputs, _, _):
            for name, field, ports in (('connector', 'excluded_daemon_ports', [18765, 8765, 18765]),
                                       ('sidecar', 'ExcludedDaemonPorts', [18766, 18765])):
                path = inputs[f'{name}_config']
                value = json.loads(path.read_text())
                value[field] = ports
                private(path, json.dumps(value).encode())
            before = {path: path.read_bytes() for path in inputs.values()}
            instance.provision(**inputs)
            for name, field in (('connector', 'excluded_daemon_ports'), ('sidecar', 'ExcludedDaemonPorts')):
                self.assertEqual(json.loads((root / f'etc/happyranch/{name}.json').read_text())[field],
                                 [8765, 8766, 18765, 18766])
            self.assertEqual({path: path.read_bytes() for path in inputs.values()}, before)

    def test_linux_deb_start_requires_provision(self):
        for case in ('missing-record', 'missing-config', 'invalid-config', 'invalid-record', 'wrong-layout'):
            with self.subTest(case=case), self.host() as (root, instance, inputs, manager, _):
                instance.provision(**inputs)
                directory = root / 'etc/happyranch'
                if case.startswith('missing'):
                    (directory / ('provision.json' if case == 'missing-record' else 'sidecar.json')).unlink()
                elif case == 'invalid-config':
                    (directory / 'connector.json').write_text('{}')
                else:
                    record = json.loads((directory / 'provision.json').read_text())
                    record['layout'] = 'source' if case == 'wrong-layout' else None
                    (directory / 'provision.json').write_text(json.dumps(record))
                with self.assertRaises(installer.InstallerError):
                    instance.install(enrollment_key_file=root / 'operator/enrollment.key')
                self.assertEqual(manager.method_calls, [])
                self.assertFalse((root / 'etc/systemd/system').exists())
                self.assertFalse((directory / 'enrollment.key').exists())

    def test_linux_deb_start_failure_preserves_cleanup(self):
        from runtime.remote_access.service_manager import ServiceManagerError
        with self.host() as (root, instance, inputs, manager, _):
            instance.provision(**inputs)
            manager.start.side_effect = ServiceManagerError('synthetic start failure')
            with self.assertRaises(installer.InstallerError):
                instance.install(enrollment_key_file=root / 'operator/enrollment.key')
            manager.stop.assert_called_once_with(installer.UNITS[2], timeout=50.0)
            self.assertFalse((root / 'etc/happyranch/enrollment.key').exists())
            self.assertFalse((root / 'etc/credstore/happyranch-tsnet-enrollment').is_symlink())
            self.assertTrue((root / 'etc/systemd/system' / installer.UNITS[0]).is_file())
            self.assertTrue((root / 'etc/happyranch/provision.json').is_file())

    def test_linux_deb_token_and_unit_drift(self):
        for case in ('source-rotation', 'staged-rotation', 'both-rotated', 'source-missing', 'unit-drift',
                     'port-mismatch', 'port-daemon', 'missing-exclusions', 'missing-dev', 'missing-daemon',
                     'unequal-exclusions', 'invalid-exclusions'):
            with self.subTest(case=case), self.host() as (root, instance, inputs, manager, options):
                instance.provision(**inputs)
                instance.install(enrollment_key_file=root / 'operator/enrollment.key')
                if case in ('source-rotation', 'both-rotated'):
                    private(inputs['daemon_token_source'], b'synthetic-rotated-token')
                if case in ('staged-rotation', 'both-rotated'):
                    private(root / 'etc/happyranch/daemon.token', b'synthetic-rotated-token')
                if case == 'source-missing':
                    inputs['daemon_token_source'].unlink()
                if case == 'unit-drift':
                    (root / 'etc/systemd/system' / installer.UNITS[1]).write_text('[Service]\nExecStart=/bin/true\n')
                config_cases = ('port-mismatch', 'port-daemon', 'missing-exclusions', 'missing-dev',
                                'missing-daemon', 'unequal-exclusions', 'invalid-exclusions')
                if case in config_cases:
                    directory = root / 'etc/happyranch'
                    connector = json.loads((directory / 'connector.json').read_text())
                    sidecar = json.loads((directory / 'sidecar.json').read_text())
                    if case == 'port-mismatch':
                        sidecar['ConnectorAddr'] = '127.0.0.1:9443'
                    elif case == 'port-daemon':
                        connector['managed']['bind_port'] = 8766
                        sidecar['ConnectorAddr'] = '127.0.0.1:8766'
                    elif case == 'missing-exclusions':
                        connector.pop('excluded_daemon_ports')
                        sidecar.pop('ExcludedDaemonPorts')
                    elif case in ('missing-dev', 'missing-daemon'):
                        ports = [8766] if case == 'missing-dev' else [8765]
                        connector['excluded_daemon_ports'] = sidecar['ExcludedDaemonPorts'] = ports
                    elif case == 'unequal-exclusions':
                        sidecar['ExcludedDaemonPorts'] = [8765, 8766, 18765]
                    else:
                        sidecar['ExcludedDaemonPorts'] = [False]
                    # Matching provenance hashes cannot mask a semantic contract violation.
                    record = json.loads((directory / 'provision.json').read_text())
                    for name, value in (('connector.json', connector), ('sidecar.json', sidecar)):
                        data = json.dumps(value).encode()
                        (directory / name).write_bytes(data)
                        record['configs'][name] = hashlib.sha256(data).hexdigest()
                    (directory / 'provision.json').write_text(json.dumps(record))
                manager.reset_mock()
                with self.assertRaisesRegex(installer.InstallerError, 'provisioning' if case in config_cases else 'token|digest'):
                    installer.ManagedInstaller(**options).check_drift()
                if case in config_cases:
                    with self.assertRaises(installer.InstallerError):
                        instance.install(enrollment_key_file=root / 'operator/enrollment.key')
                if case != 'unit-drift':
                    self.assertEqual(manager.method_calls, [])
                manager.start.assert_not_called()
                manager.stop.assert_not_called()


@contextmanager
def context_noop() -> Iterator[None]:
    yield


if __name__ == "__main__":
    unittest.main()

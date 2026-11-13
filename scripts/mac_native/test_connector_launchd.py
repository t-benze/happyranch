"""Focused connector platform tests outside the retired Python-unit selection."""
from __future__ import annotations

import io
import os
import plistlib
import tempfile
from pathlib import Path
import unittest
from contextlib import redirect_stderr
from unittest.mock import Mock, patch

from runtime.remote_access import cli
from runtime.remote_access.service_manager import ServiceManagerError, SystemdServiceManager, service_manager
from runtime.remote_access.launchd_agent import (
    LABEL, LaunchdServiceManager, parse_launchctl_print, render_launch_agent,
)
from runtime.remote_access.supervisor import ConnectorConfig, ConnectorSupervisor


class CliErrors(unittest.TestCase):
    def test_lifecycle_service_errors_are_clean(self) -> None:
        for command in ("install", "uninstall", "start", "stop", "restart", "enable", "disable", "status", "upgrade", "rollback"):
            with self.subTest(command=command):
                supervisor = Mock()
                getattr(supervisor, command).side_effect = ServiceManagerError("cannot execute systemctl: not found")
                stderr = io.StringIO()
                with patch.object(cli, "_load_config", return_value=Mock()), patch.object(cli, "ConnectorSupervisor", return_value=supervisor), redirect_stderr(stderr):
                    result = cli.main([command])
                self.assertEqual(result, 1)
                self.assertEqual(stderr.getvalue(), "error: cannot execute systemctl: not found\n")


DOMAIN = f"gui/{os.getuid()}"
TARGET = f"{DOMAIN}/{LABEL}"
RUNNING = f'''{TARGET} = {{
    active count = 1
    path = /Users/example/Library/LaunchAgents/{LABEL}.plist
    type = LaunchAgent
    state = running
    program = /bin/sleep
    arguments = {{
        /bin/sleep
        300
    }}
    environment = {{
        state = not running
        pid = 999
    }}
    runs = 2
    pid = 1234
    last exit code = 0
}}
'''
CRASHED = f'''{TARGET} = {{
    active count = 0
    state = spawn scheduled
    runs = 3
    last exit code = 78: EX_CONFIG
}}
'''
MISSING = f'Could not find service "{LABEL}" in domain for user gui: {os.getuid()}'


class Runner:
    """Script launchctl observations only; assert every external argv boundary."""
    def __init__(self, test: unittest.TestCase, rows: list[tuple[list[str], int, str]]) -> None:
        self.test = test
        self.rows = list(rows)
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], *, timeout: float = 10.0) -> tuple[int, str]:
        self.test.assertGreater(timeout, 0)
        self.calls.append(argv)
        self.test.assertTrue(self.rows, f"unexpected command: {argv}")
        expected, code, output = self.rows.pop(0)
        self.test.assertEqual(argv, ["launchctl", *expected])
        return code, output


def row(*args: str, code: int = 0, output: str = "") -> tuple[list[str], int, str]:
    return list(args), code, output


def absent() -> list[tuple[list[str], int, str]]:
    return [row("print", TARGET, code=113, output=MISSING), row("print", DOMAIN)]


def enabled(value: bool = True) -> tuple[list[str], int, str]:
    return row("print-disabled", DOMAIN, output=f'disabled services = {{\n "{LABEL}" => {"false" if value else "true"}\n}}')


class PlatformSelection(unittest.TestCase):
    def test_linux_factory_preserves_systemd_argv_and_status(self) -> None:
        for system in (False, True):
            with self.subTest(system=system), patch("sys.platform", "linux"):
                manager = service_manager(system=system)
                self.assertIsInstance(manager, SystemdServiceManager)
                calls = []
                def run(argv: list[str], *, timeout: float) -> tuple[int, str]:
                    calls.append(argv)
                    return 0, "ActiveState=active\nSubState=running\nLoadState=loaded\nExecMainPID=55\nUnitFileState=enabled\n"
                manager._run = run
                manager.start("x.service")
                manager.stop("x.service")
                manager.restart("x.service")
                manager.enable("x.service")
                manager.disable("x.service")
                state = manager.status("x.service")
                prefix = ["systemctl"] + ([] if system else ["--user"])
                self.assertEqual(calls, [prefix + [verb, "x.service"] for verb in ("start", "stop", "restart", "enable", "disable")] + [
                    prefix + ["show", "-p", "ActiveState", "-p", "SubState", "-p", "LoadState", "-p", "ExecMainPID", "-p", "UnitFileState", "x.service"]])
                self.assertEqual(state.__dict__, dict(unit_name="x.service", active_state="active", sub_state="running", load_state="loaded", pid=55, unit_file_state="enabled"))

    def test_unsupported_cli_and_macos_system_mode_refuse_without_effects(self) -> None:
        for platform, system, message in (("win32", False, "unsupported on win32"), ("darwin", True, "per-user LaunchAgent")):
            with patch("sys.platform", platform), self.assertRaisesRegex(ServiceManagerError, message):
                service_manager(system=system)
            for command in ("install", "uninstall", "start", "stop", "restart", "enable", "disable", "status", "upgrade", "rollback"):
                with self.subTest(platform=platform, command=command):
                    stderr = io.StringIO()
                    with patch("sys.platform", platform), patch.object(cli, "_load_config", return_value=ConnectorConfig(system=system)), patch("subprocess.run") as execute, redirect_stderr(stderr):
                        result = cli.main([command])
                    self.assertEqual(result, 1)
                    self.assertTrue(stderr.getvalue().startswith("error: "))
                    self.assertIn(message, stderr.getvalue())
                    execute.assert_not_called()

    def test_darwin_cli_uses_launchd_and_original_config_without_staging(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = ConnectorConfig(daemon_token_path="synthetic-secret-path", state_path="missing-trust", policy_path="missing-policy")
            runner = Runner(self, [row("print", DOMAIN)])
            manager = LaunchdServiceManager(unit_dir=root / "agents", run=runner)
            with patch("sys.platform", "darwin"), patch.object(Path, "home", return_value=root):
                self.assertIsInstance(service_manager(), LaunchdServiceManager)
                supervisor = ConnectorSupervisor(config=config, config_path=root / "config.json", manager=manager)
                text = supervisor._launch_agent_text()
                value = plistlib.loads(text.encode())
                self.assertEqual(value["ProgramArguments"][1:], ["-m", "runtime.remote_access.cli", "run", "--config", str(root / "config.json")])
                self.assertNotIn("synthetic-secret", text)
                path = supervisor.install(enable=False)
            self.assertEqual(path, root / "agents" / f"{LABEL}.plist")
            self.assertFalse((root / "trust-state.json").exists())
            self.assertFalse(runner.rows)


class LaunchAgentFiles(unittest.TestCase):
    def test_render_install_backup_and_no_enable(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runner = Runner(self, [row("print", DOMAIN), row("enable", TARGET)])
            manager = LaunchdServiceManager(unit_dir=root / "agents", run=runner)
            text = render_launch_agent(LABEL, ("/a path/python", "--config", "/a path/config.json"), logs_dir=root / "logs")
            value = plistlib.loads(text.encode())
            self.assertEqual(value, dict(Label=LABEL, ProgramArguments=["/a path/python", "--config", "/a path/config.json"], KeepAlive=True, ThrottleInterval=10, Disabled=True, StandardOutPath=str(root / "logs" / f"{LABEL}.stdout.log"), StandardErrorPath=str(root / "logs" / f"{LABEL}.stderr.log")))
            path = manager.install(text, LABEL)
            self.assertEqual(path.read_text(), text)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            newer = render_launch_agent(LABEL, ("/bin/sleep", "200"), logs_dir=root / "logs")
            manager.install(newer, LABEL, enable=False)
            backups = list(manager.backup_dir.iterdir())
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), text)
            self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.read_text(), newer)
            self.assertFalse(runner.rows)

    def test_invalid_plist_and_labels_refuse_before_writes(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "agents"
            runner = Runner(self, [])
            manager = LaunchdServiceManager(unit_dir=root, run=runner)
            for text in ("invalid", "<plist><dict><key>broken</plist>", plistlib.dumps({"Label": LABEL, "ProgramArguments": ["relative"]}).decode(), plistlib.dumps({"Label": "wrong.label", "ProgramArguments": ["/bin/sleep"]}).decode()):
                with self.subTest(text=text), self.assertRaises(ServiceManagerError):
                    manager.install(text, LABEL)
            for name in ("../escape", "single", "a.b/../c"):
                with self.subTest(name=name), self.assertRaises(ServiceManagerError):
                    render_launch_agent(name, ("/bin/sleep",))
            for args in ((), ("relative",), ("/bin/sleep", "\x01"), ("/bin/sleep", "\x00")):
                with self.subTest(args=args), self.assertRaises(ServiceManagerError):
                    render_launch_agent(LABEL, args)
            self.assertFalse(root.exists())
            self.assertFalse(runner.calls)


class LaunchdObservations(unittest.TestCase):
    def test_domain_selection_and_honest_refusal(self) -> None:
        uid = os.getuid()
        for rows, expected in (([row("print", DOMAIN)], DOMAIN), ([row("print", DOMAIN, code=125, output="Could not find domain for"), row("print", f"user/{uid}")], f"user/{uid}")):
            runner = Runner(self, rows)
            manager = LaunchdServiceManager(run=runner)
            self.assertEqual(manager.domain, expected)
            self.assertEqual(manager.domain, expected)
            self.assertFalse(runner.rows)
        for rows in ([row("print", DOMAIN, code=1, output="Operation not permitted")], [row("print", DOMAIN, code=125, output="Could not find domain for"), row("print", f"user/{uid}", code=1, output="Operation not permitted")]):
            runner = Runner(self, rows)
            with self.assertRaisesRegex(ServiceManagerError, "Operation not permitted"):
                LaunchdServiceManager(run=runner).domain
            self.assertFalse(runner.rows)

    def test_real_shaped_print_running_crashed_and_never_exited(self) -> None:
        state = parse_launchctl_print(LABEL, RUNNING, enabled=True)
        self.assertTrue(state.running)
        self.assertEqual((state.load_state, state.pid, state.last_exit_code, state.last_exit_signal), ("loaded", 1234, 0, None))
        state = parse_launchctl_print(LABEL, CRASHED, enabled=False)
        self.assertFalse(state.running)
        self.assertEqual((state.pid, state.last_exit_code, state.last_exit_signal, state.unit_file_state), (0, 78, None, "disabled"))
        killed = CRASHED.replace("last exit code = 78: EX_CONFIG", "last terminating signal = Killed: 9")
        state = parse_launchctl_print(LABEL, killed, enabled=True)
        self.assertEqual((state.last_exit_code, state.last_exit_signal), (None, 9))
        state = parse_launchctl_print(LABEL, f'{TARGET} = {{\n state = not running\n last exit code = (never exited)\n}}', enabled=True)
        self.assertIsNone(state.last_exit_code)
        self.assertFalse(state.running)
        for text in ("garbage", RUNNING.replace("pid = 1234", "pid = bad"), RUNNING.replace("pid = 1234", "pid = 0"), RUNNING.replace("state = running", "state = running\n state = waiting")):
            with self.subTest(text=text), self.assertRaises(ServiceManagerError):
                parse_launchctl_print(LABEL, text, enabled=True)

    def test_not_loaded_and_failed_print_are_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runner = Runner(self, [row("print", DOMAIN), *absent(), enabled(), *absent(), enabled(), row("print", TARGET, code=1, output="Operation not permitted"), *absent()[:-1], row("print", DOMAIN, code=1, output="domain lost")])
            manager = LaunchdServiceManager(unit_dir=root, run=runner)
            self.assertEqual(manager.status(LABEL).load_state, "not-found")
            manager._write(render_launch_agent(LABEL, ("/bin/sleep", "20"), logs_dir=root), LABEL)
            state = manager.status(LABEL)
            self.assertEqual(state.load_state, "unloaded")
            self.assertFalse(state.running)
            self.assertEqual(state.pid, 0)
            with self.assertRaisesRegex(ServiceManagerError, "Operation not permitted"):
                manager.status(LABEL)
            with self.assertRaisesRegex(ServiceManagerError, "domain lost"):
                manager.status(LABEL)
            self.assertFalse(runner.rows)


class LaunchdLifecycle(unittest.TestCase):
    def test_lifecycle_argv_and_idempotent_uninstall(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / f"{LABEL}.plist"
            rows = [row("print", DOMAIN), enabled(False), row("enable", TARGET), *absent(), row("bootstrap", DOMAIN, str(path)), row("disable", TARGET),
                    enabled(), row("print", TARGET, output=RUNNING), row("kickstart", TARGET), enabled(False), row("enable", TARGET), row("kickstart", "-k", TARGET), row("disable", TARGET), row("enable", TARGET), row("disable", TARGET),
                    row("print", TARGET, output=RUNNING), row("bootout", TARGET), *absent(),
                    *absent(), *absent(), row("disable", TARGET), *absent(), *absent(), row("disable", TARGET)]
            runner = Runner(self, rows)
            manager = LaunchdServiceManager(unit_dir=root, run=runner)
            manager._write(render_launch_agent(LABEL, ("/bin/sleep", "20"), logs_dir=root), LABEL)
            manager.start(LABEL)
            manager.start(LABEL)
            manager.restart(LABEL)
            manager.enable(LABEL)
            manager.disable(LABEL)
            manager.stop(LABEL)
            manager.uninstall(LABEL)
            manager.uninstall(LABEL)
            self.assertFalse(path.exists())
            self.assertFalse(runner.rows)

    def test_bootstrap_and_bootout_errors_are_not_success(self) -> None:
        for operation in ("start", "stop"):
            with self.subTest(operation=operation):
                rows = [row("print", DOMAIN)]
                if operation == "start":
                    rows += [enabled(), *absent(), row("bootstrap", DOMAIN, "/private/agents/ai.happyranch.connector.plist", code=5, output="Input/output error")]
                else:
                    rows += [row("print", TARGET, output=RUNNING), row("bootout", TARGET, code=1, output="Operation not permitted")]
                runner = Runner(self, rows)
                manager = LaunchdServiceManager(unit_dir=Path("/private/agents"), run=runner)
                with self.assertRaises(ServiceManagerError):
                    getattr(manager, operation)(LABEL)
                self.assertFalse(runner.rows)

    def test_upgrade_rollback_rereads_plist_and_recovers_failure(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / f"{LABEL}.plist"
            old = render_launch_agent(LABEL, ("/bin/sleep", "20"), logs_dir=root)
            new = render_launch_agent(LABEL, ("/bin/sleep", "30"), logs_dir=root)
            def replace_rows(fail: bool = False) -> list:
                return [row("print", TARGET, output=RUNNING), row("bootout", TARGET), *absent(), enabled(), *absent(), row("bootstrap", DOMAIN, str(path), code=5 if fail else 0, output="bad executable" if fail else "")]
            verify = [row("print", TARGET, output=RUNNING), enabled()]
            runner = Runner(self, [row("print", DOMAIN), *replace_rows(), *verify, *replace_rows(), *verify, *replace_rows(True), *replace_rows(), *verify])
            manager = LaunchdServiceManager(unit_dir=root, run=runner)
            manager._write(old, LABEL)
            outcome = manager.upgrade(new, LABEL)
            self.assertTrue(outcome.ok)
            self.assertEqual(outcome.backup_path.read_text(), old)
            self.assertEqual(path.read_text(), new)
            self.assertTrue(manager.rollback(LABEL).ok)
            self.assertEqual(path.read_text(), old)
            outcome = manager.upgrade(new, LABEL)
            self.assertFalse(outcome.ok)
            self.assertTrue(outcome.rolled_back)
            self.assertEqual(path.read_text(), old)
            self.assertFalse(runner.rows)


if __name__ == "__main__":
    unittest.main()

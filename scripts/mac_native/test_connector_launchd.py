"""Focused connector platform tests outside the retired Python-unit selection."""
from __future__ import annotations

import io
import json
import os
import plistlib
import stat
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import Mock, patch

from scripts.mac_native import suite_connector_launchd
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
            self.assertEqual(value, dict(Label=LABEL, ProgramArguments=["/a path/python", "--config", "/a path/config.json"], KeepAlive=True, ThrottleInterval=10, Disabled=True, LimitLoadToSessionType=["Aqua", "Background"], StandardOutPath=str(root / "logs" / f"{LABEL}.stdout.log"), StandardErrorPath=str(root / "logs" / f"{LABEL}.stderr.log")))
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
    def test_disabled_overrides_use_exact_label_or_plist_fallback(self) -> None:
        cases = []
        for sibling in (f"{LABEL}.worker", f"{LABEL}-probe-12345678-abcd"):
            for override in ("true", "false", "disabled", "enabled"):
                for disabled in (True, False):
                    cases.append((sibling, override, disabled, not disabled))
        # Exact overrides take precedence over the opposite plist default.
        cases += [(LABEL, "true", False, False), (LABEL, "false", True, True),
                  (LABEL, "disabled", False, False), (LABEL, "enabled", True, True),
                  (LABEL, "maybe", False, None), (LABEL, "", True, None),
                  (LABEL, "disabled extra", False, None),
                  (LABEL, f'disabled\n "{LABEL}" => enabled', False, None)]
        cases = [(f'disabled services = {{\n "{key}" => {override}\n}}', disabled, expected)
                 for key, override, disabled, expected in cases]
        # Verbatim print-disabled receipt from native build24 (exit 0).
        empty = "disabled services = (no disabled services)"
        for disabled in (True, False):
            for output in (empty, "\n \t" + empty.replace(" = ", " \t= \t") + " \t\n",
                           empty + f'\nlogin item associations = {{\n "{LABEL}" => malformed\n}}'):
                cases.append((output, disabled, not disabled))
        for output, disabled, expected in cases:
            with self.subTest(output=output, disabled=disabled), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                path = root / f"{LABEL}.plist"
                value = plistlib.loads(render_launch_agent(LABEL, ("/bin/sleep", "20"), logs_dir=root).encode())
                value["Disabled"] = disabled
                path.write_bytes(plistlib.dumps(value))
                observation = row("print-disabled", DOMAIN, output=output)
                rows = [row("print", DOMAIN), *absent(), observation, observation]
                if expected is not None:
                    if not expected:
                        rows += [row("enable", TARGET)]
                    rows += [*absent(), row("bootstrap", DOMAIN, str(path))]
                    if not expected:
                        rows += [row("disable", TARGET)]
                    rows += [row("print", TARGET, output=RUNNING), observation]
                runner = Runner(self, rows)
                manager = LaunchdServiceManager(unit_dir=root, run=runner)
                if expected is None:
                    for operation in ("status", "start"):
                        with self.assertRaisesRegex(ServiceManagerError, "malformed launchctl disabled override") as raised:
                            getattr(manager, operation)(LABEL)
                        self.assertIn("exit 0", str(raised.exception))
                        self.assertIn(observation[2][:512], str(raised.exception))
                else:
                    state = manager.status(LABEL)
                    self.assertEqual(state.load_state, "unloaded")
                    self.assertEqual(state.unit_file_state, "enabled" if expected else "disabled")
                    manager.start(LABEL)
                    state = manager.status(LABEL)
                    self.assertTrue(state.running)
                    self.assertEqual(state.unit_file_state, "enabled" if expected else "disabled")
                self.assertFalse(runner.rows)

    def test_print_disabled_block_boundaries_and_diagnostics(self) -> None:
        cases = [
            (0, 'disabled services = {\n}', True),
            (0, f'\n \tdisabled services \t= \t{{ \t\n\n\t "{LABEL}"  =>  enabled \t\n \t}} \t\n\n', True),
            (0, f'disabled services = {{\n "{LABEL}" => disabled\n}}\n'
                f'login item associations = {{\n "{LABEL}" => enabled\n}}\ntrailing diagnostic', False),
            # Exact-looking rows outside the disabled block never override the plist.
            (0, f'disabled services = {{\n}}\nlogin item associations = {{\n'
                f' "{LABEL}" => disabled\n}}\n "{LABEL}" => malformed', True),
            (0, f'login item associations = {{\n "{LABEL}" => disabled\n}}', None),
            (0, f'disabled services = {{\n "{LABEL}" => disabled\n', None),
            (0, f'disabled services = {{\nlogin item associations = {{\n "{LABEL}" => enabled\n}}', None),
            (0, 'disabled services = (no services)', None),
            (0, 'unrecognized output ' + 'x' * 600, None),
            (5, 'print-disabled denied ' + 'y' * 600, None),
        ]
        for code, output, expected in cases:
            with self.subTest(code=code, output=output), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                value = plistlib.loads(render_launch_agent(LABEL, ("/bin/sleep", "20"), logs_dir=root).encode())
                value["Disabled"] = False
                (root / f"{LABEL}.plist").write_bytes(plistlib.dumps(value))
                runner = Runner(self, [row("print", DOMAIN), *absent(),
                                       row("print-disabled", DOMAIN, code=code, output=output)])
                manager = LaunchdServiceManager(unit_dir=root, run=runner)
                if expected is None:
                    with self.assertRaises(ServiceManagerError) as raised:
                        manager.status(LABEL)
                    message = str(raised.exception)
                    self.assertIn("print-disabled", message)
                    self.assertIn(f"exit {code}", message)
                    self.assertIn(output.strip()[:512], message)
                    if len(output.strip()) > 512:
                        self.assertNotIn(output.strip()[:513], message)
                else:
                    self.assertEqual(manager.status(LABEL).unit_file_state, "enabled" if expected else "disabled")
                self.assertFalse(runner.rows)

    def test_domain_selection_and_honest_refusal(self) -> None:
        user = f"user/{os.getuid()}"
        unsupported = "Could not print domain: 125: Domain does not support specified action"
        for diagnostic in ("Could not find domain for", unsupported):
            with self.subTest(diagnostic=diagnostic):
                runner = Runner(self, [row("print", DOMAIN, code=125, output=diagnostic),
                                       row("print", user)])
                manager = LaunchdServiceManager(run=runner)
                self.assertEqual(manager.domain, user)
                self.assertEqual(manager.domain, user)
                self.assertFalse(runner.rows)
            with self.subTest(diagnostic=diagnostic, user_available=False):
                runner = Runner(self, [row("print", DOMAIN, code=125, output=diagnostic),
                                       row("print", user, code=1, output="Operation not permitted")])
                with self.assertRaises(ServiceManagerError) as failure:
                    LaunchdServiceManager(run=runner).domain
                message = str(failure.exception)
                for expected in (DOMAIN, "(125)", diagnostic, user, "(1)", "Operation not permitted"):
                    self.assertIn(expected, message)
                self.assertFalse(runner.rows)
        runner = Runner(self, [row("print", DOMAIN)])
        manager = LaunchdServiceManager(run=runner)
        self.assertEqual(manager.domain, DOMAIN)
        self.assertEqual(manager.domain, DOMAIN)
        self.assertFalse(runner.rows)
        # Neither an unknown exit 125 nor the known text with another exit code
        # permits a fallback to a different domain.
        for code, diagnostic in ((1, "Operation not permitted"), (125, "Operation not permitted"),
                                 (1, unsupported)):
            with self.subTest(code=code, diagnostic=diagnostic):
                runner = Runner(self, [row("print", DOMAIN, code=code, output=diagnostic)])
                with self.assertRaises(ServiceManagerError) as failure:
                    LaunchdServiceManager(run=runner).domain
                self.assertIn(diagnostic, str(failure.exception))
                self.assertFalse(runner.rows)

    def test_cli_status_names_selected_domain(self) -> None:
        for domain in (DOMAIN, f"user/{os.getuid()}"):
            for load_state in ("loaded", "unloaded", "not-found"):
                with self.subTest(domain=domain, load_state=load_state), tempfile.TemporaryDirectory() as folder:
                    root = Path(folder)
                    rows = [row("print", DOMAIN)] if domain == DOMAIN else [
                        row("print", DOMAIN, code=125, output="Could not print domain: 125: Domain does not support specified action"),
                        row("print", domain)]
                    target = f"{domain}/{LABEL}"
                    if load_state == "loaded":
                        rows.append(row("print", target, output=RUNNING.replace(DOMAIN, domain)))
                    else:
                        rows += [row("print", target, code=113, output=MISSING), row("print", domain)]
                    rows.append(row("print-disabled", domain, output="disabled services = {\n}"))
                    runner = Runner(self, rows)
                    manager = LaunchdServiceManager(unit_dir=root, run=runner)
                    if load_state == "unloaded":
                        (root / f"{LABEL}.plist").write_text(render_launch_agent(LABEL, ("/bin/sleep", "300")))
                    supervisor = ConnectorSupervisor(config=ConnectorConfig(), manager=manager)
                    stdout = io.StringIO()
                    with patch.object(cli, "_load_config", return_value=ConnectorConfig()), patch.object(cli, "ConnectorSupervisor", return_value=supervisor), redirect_stdout(stdout):
                        result = cli.main(["status"])
                    self.assertEqual(result, 0)
                    status = json.loads(stdout.getvalue())
                    self.assertEqual(status["domain"], domain)
                    self.assertEqual(status["load_state"], load_state)
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

    def test_print_job_states_only_running_exposes_pid(self) -> None:
        states = ("running", "waiting", "not running", "spawn scheduled", "xpcproxy",
                  "exited", "exiting", "penalty box", "future launchd state", "Running", "u" * 3000)
        for raw in states:
            for pid_line in ("pid = 1234", "") if raw != "running" else ("pid = 1234",):
                with self.subTest(state=raw[:40], pid=pid_line):
                    text = f"{TARGET} = {{\n state = {raw}\n {pid_line}\n}}"
                    state = parse_launchctl_print(LABEL, text, enabled=True)
                    self.assertEqual(state.sub_state, raw[:2048])
                    self.assertEqual(state.pid, 1234 if raw == "running" else 0)
                    self.assertEqual(state.active_state, "active" if raw == "running" else "inactive")
                    self.assertEqual(state.running, raw == "running")

    def test_print_nested_sections_and_quoted_braces(self) -> None:
        # Synthetic production-shaped print: nested process/endpoint state is
        # distinct from job state; escaped quotes/backslashes occur in argv.
        text = rf'''{TARGET} = {{
    path = "/Users/example/Library/LaunchAgents/{LABEL}.plist"
    arguments = {{
        "/bin/sleep"
        "literal \"{{ in an argument"
        "escaped backslash \\"
    }}
    environment = {{
        TEMPLATE => "value {{"
        state = waiting
        pid = 999
    }}
    endpoints = {{
        "ai.example.endpoint" = {{
            state = not running
            port = 0x123
        }}
    }}
    resource coalition = {{
        id = 84
        type = resource
        state = active
    }}
    state = running
    pid = 1234
    last exit code = 78: EX_CONFIG
    last terminating signal = Killed: 9
}}
'''
        state = parse_launchctl_print(LABEL, text, enabled=False)
        self.assertEqual((state.active_state, state.sub_state, state.pid), ("active", "running", 1234))
        self.assertEqual((state.last_exit_code, state.last_exit_signal, state.unit_file_state), (78, 9, "disabled"))
        closed = text.replace('TEMPLATE => "value {"', 'TEMPLATE => "value }"')
        self.assertEqual(parse_launchctl_print(LABEL, closed, enabled=True).pid, 1234)

    def test_print_missing_duplicate_numeric_and_brace_errors_refuse(self) -> None:
        cases = {
            "missing state": RUNNING.replace("state = running", "unrelated = running"),
            "empty state": RUNNING.replace("state = running", "state = "),
            "duplicate state": RUNNING.replace("state = running", "state = running\n state = waiting"),
            "duplicate pid": RUNNING.replace("pid = 1234", "pid = 1234\n pid = 4321"),
            "duplicate exit": RUNNING.replace("last exit code = 0", "last exit code = 0\n last exit code = 1"),
            "duplicate signal": RUNNING.replace("last exit code = 0", "last terminating signal = 9\n last terminating signal = 15"),
            "missing pid": RUNNING.replace("pid = 1234", "unrelated = 1234"),
            "zero pid": RUNNING.replace("pid = 1234", "pid = 0"),
            "negative pid": RUNNING.replace("pid = 1234", "pid = -1"),
            "bad pid": RUNNING.replace("pid = 1234", "pid = bad"),
            "underscore pid": RUNNING.replace("pid = 1234", "pid = 1_234"),
            "decimal pid": RUNNING.replace("pid = 1234", "pid = 1234.0"),
            "bad exit": RUNNING.replace("last exit code = 0", "last exit code = bad"),
            "bad signal": RUNNING.replace("last exit code = 0", "last terminating signal = Killed: bad"),
            "unknown malformed pid": RUNNING.replace("state = running", "state = future state").replace("pid = 1234", "pid = bad"),
            "missing close": RUNNING[:-2],
            "extra close": RUNNING + "}\n",
            "premature close": "}\n{\n" + RUNNING,
        }
        for name, text in cases.items():
            with self.subTest(case=name), self.assertRaises(ServiceManagerError):
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
    def test_failed_backup_publication_keeps_original_running(self) -> None:
        for fault in ("read", "write", "fsync", "replace"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                old = render_launch_agent(LABEL, ("/bin/sleep", "20"), logs_dir=root)
                new = render_launch_agent(LABEL, ("/bin/sleep", "30"), logs_dir=root)
                path = root / f"{LABEL}.plist"
                replacement = [row("print", TARGET, output=RUNNING), row("bootout", TARGET), *absent(),
                               enabled(), *absent(), row("bootstrap", DOMAIN, str(path))]
                runner = Runner(self, [row("print", DOMAIN), *replacement,
                                       row("print", TARGET, output=RUNNING), enabled()])
                manager = LaunchdServiceManager(unit_dir=root, run=runner)
                path = manager._write(old, LABEL)
                target = {"read": "pathlib.Path.read_text", "write": "tempfile.NamedTemporaryFile",
                          "fsync": "runtime.remote_access.launchd_agent.os.fsync",
                          "replace": "runtime.remote_access.launchd_agent.os.replace"}[fault]
                original_replace = os.replace
                original_temporary = tempfile.NamedTemporaryFile
                def fail_backup_write(*args, **kwargs):
                    stream = original_temporary(*args, **kwargs)
                    original_write = stream.write
                    def partial_write(text: str) -> None:
                        original_write(text[:16])
                        stream.flush()
                        raise OSError("synthetic backup write failure")
                    stream.write = partial_write
                    return stream
                def fail_backup_replace(source: Path, destination: Path) -> None:
                    if Path(destination).parent == manager.backup_dir:
                        raise OSError("synthetic backup replace failure")
                    original_replace(source, destination)
                effect = {"write": fail_backup_write, "replace": fail_backup_replace}.get(fault, OSError(f"synthetic backup {fault} failure"))
                with patch(target, side_effect=effect):
                    with self.assertRaisesRegex(OSError, f"synthetic backup {fault} failure"):
                        manager.upgrade(new, LABEL)
                self.assertEqual(list(manager.backup_dir.glob(f"{path.name}.*")), [])
                self.assertEqual(list(manager.backup_dir.iterdir()), [])
                self.assertEqual(path.read_text(), old)
                self.assertFalse(runner.calls)
                runner.rows = [row("print", DOMAIN), row("print", TARGET, output=RUNNING), enabled()]
                self.assertTrue(manager.status(LABEL).running)
                self.assertFalse(any(call[1] == "bootout" for call in runner.calls))
                self.assertFalse(runner.rows)

    def test_invalid_rollback_backup_refuses_before_bootout(self) -> None:
        invalid = (b"", b"invalid", b"\xff", plistlib.dumps({"Label": "wrong.label", "ProgramArguments": ["/bin/sleep"]}))
        for candidate in invalid:
            with self.subTest(candidate=candidate), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                # A bootout row lets the pre-fix code reach its real validation
                # error; the explicit argv assertion below detects disruption.
                runner = Runner(self, [row("print", DOMAIN), row("print", TARGET, output=RUNNING),
                                       row("bootout", TARGET), *absent()])
                manager = LaunchdServiceManager(unit_dir=root, run=runner)
                old = render_launch_agent(LABEL, ("/bin/sleep", "20"), logs_dir=root)
                path = manager._write(old, LABEL)
                manager.backup_dir.mkdir()
                backup = manager.backup_dir / f"{path.name}.1"
                backup.write_bytes(candidate)
                with self.assertRaisesRegex(ServiceManagerError, "invalid LaunchAgent plist|cannot read LaunchAgent backup"):
                    manager.rollback(LABEL)
                self.assertFalse(any(call[1] == "bootout" for call in runner.calls), runner.calls)
                self.assertEqual(path.read_text(), old)
                self.assertEqual(backup.read_bytes(), candidate)
                runner.rows = [row("print", DOMAIN), row("print", TARGET, output=RUNNING), enabled()]
                self.assertTrue(manager.status(LABEL).running)
                self.assertFalse(runner.rows)

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
            # Real old-shape bytes, accepted before this additive plist key.
            old_value = plistlib.loads(old.encode())
            old_value.pop("LimitLoadToSessionType", None)
            old = plistlib.dumps(old_value, sort_keys=True).decode()
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
            self.assertEqual(plistlib.loads(path.read_bytes()).get("LimitLoadToSessionType"), ["Aqua", "Background"])
            self.assertTrue(manager.rollback(LABEL).ok)
            self.assertEqual(path.read_text(), old)
            outcome = manager.upgrade(new, LABEL)
            self.assertFalse(outcome.ok)
            self.assertTrue(outcome.rolled_back)
            self.assertEqual(path.read_text(), old)
            self.assertFalse(runner.rows)


class NativeProbeContext(unittest.TestCase):
    def test_lifecycle_status_excerpts_and_transient_polling(self) -> None:
        for never_running in (False, True):
            with self.subTest(never_running=never_running), tempfile.TemporaryDirectory() as folder:
                report = {}
                loaded = False
                pending = []
                now = 1000.0
                pid = 1234
                outputs = {}
                def command(argv: list[str], env: dict[str, str], cwd: Path, deadline: float, **kwargs: object) -> tuple[int, bytes]:
                    nonlocal loaded
                    self.assertEqual(kwargs.get("output_cap", 1048576), 1048576)
                    label = report["connector_launchd"]["label"]
                    if argv[:2] == ["launchctl", "managername"]:
                        return 0, b"Aqua"
                    if argv[:2] == ["launchctl", "print-disabled"]:
                        return 0, f'disabled services = {{\n "{label}" => false\n}}'.encode()
                    if argv[:2] == ["launchctl", "print"]:
                        if argv[2] in (DOMAIN, f"user/{os.getuid()}"):
                            return 0, b"domain context"
                        if not loaded:
                            text = f'Could not find service "{label}" in domain' + "a" * 3000
                            return 113, text.encode()
                        raw = pending.pop(0) if pending else ("future launchd state" if never_running else "running")
                        text = f'{argv[2]} = {{\n state = {raw}\n pid = {pid}\n note = "' + "z" * 3000 + '"\n}\n'
                        outputs[raw] = text
                        return 0, text.encode()
                    if argv[:2] == ["launchctl", "bootstrap"]:
                        loaded = True
                        pending[:] = ["future launchd state", "xpcproxy"]
                    elif argv[:2] == ["launchctl", "bootout"]:
                        loaded = False
                    else:
                        self.assertIn(argv[1], ("enable", "disable"))
                    return 0, b""
                def sleep(seconds: float) -> None:
                    nonlocal now
                    now += seconds
                def kill(target_pid: int, sig: int) -> None:
                    nonlocal pid
                    self.assertEqual((target_pid, sig), (1234, suite_connector_launchd.signal.SIGKILL))
                    pid = 4321
                    pending[:] = ["exiting", "penalty box"]
                with patch.object(suite_connector_launchd, "_command", side_effect=command), patch.object(Path, "home", return_value=Path(folder)), patch.object(suite_connector_launchd.time, "monotonic", side_effect=lambda: now), patch.object(suite_connector_launchd.time, "sleep", side_effect=sleep), patch.object(suite_connector_launchd.os, "kill", side_effect=kill):
                    if never_running:
                        with self.assertRaisesRegex(TimeoutError, "did not reach running"):
                            suite_connector_launchd.run(report)
                    else:
                        suite_connector_launchd.run(report)
                section = json.loads(json.dumps(report))["connector_launchd"]
                observations = section.get("status_observations", {})
                self.assertIn("absence", observations)
                self.assertEqual(observations["absence"]["print"]["exit_code"], 113)
                self.assertEqual(len(observations["absence"]["print"]["excerpt"]), 2048)
                self.assertIn("print_disabled", observations["absence"])
                if never_running:
                    self.assertGreaterEqual(now, 1030)
                    self.assertLess(now, 1031)
                    self.assertEqual(observations["wait_running"]["print"]["excerpt"], outputs["future launchd state"][:2048])
                    self.assertEqual(observations["wait_running"]["error"], "TimeoutError")
                    self.assertNotIn("started", [c["name"] for c in section["checks"]])
                else:
                    for step in ("wait_running", "recovery", "stopped", "absent"):
                        self.assertIn("print", observations[step])
                        self.assertIn("print_disabled", observations[step])
                    self.assertIn("pid = 1234", observations["wait_running"]["print"]["excerpt"])
                    self.assertIn("pid = 4321", observations["recovery"]["print"]["excerpt"])
                    self.assertEqual(observations["recovery"]["print"]["excerpt"], outputs["running"][:2048])
                    self.assertEqual(len(observations["recovery"]["print"]["excerpt"]), 2048)
                    self.assertEqual([c["name"] for c in section["checks"]], ["unique_service_absent_before_install", "installed", "started", "keepalive_restarted", "stopped", "uninstalled", "scratch_removed"])
                self.assertTrue(section["cleanup"]["service_absent"])
                self.assertIn("print", observations["cleanup"])
                self.assertTrue(section["scratch_removed"])

    def test_status_exception_retains_raw_excerpt_before_cleanup(self) -> None:
        for failure in ("parser", "nonzero", "timeout", "no_output", "disabled"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as folder:
                report = {}
                raw = "  malformed observation " + "q" * 3000 + "\n"
                def command(argv: list[str], env: dict[str, str], cwd: Path, deadline: float, **kwargs: object) -> tuple[int, bytes]:
                    label = report["connector_launchd"]["label"]
                    if argv[:2] == ["launchctl", "managername"]:
                        return 0, b"Aqua"
                    if argv[:2] == ["launchctl", "print-disabled"]:
                        return (5, raw.encode()) if failure == "disabled" else (0, b"disabled services = {\n}\n")
                    if argv[2] in (DOMAIN, f"user/{os.getuid()}"):
                        return 0, b"domain context"
                    self.assertEqual(argv, ["launchctl", "print", f"{DOMAIN}/{label}"])
                    if failure == "timeout":
                        raise subprocess.TimeoutExpired(argv, 10, output=raw.encode())
                    if failure == "no_output":
                        raise TimeoutError("observation expired without available output")
                    return (1 if failure == "nonzero" else 0), raw.encode()
                expected = subprocess.TimeoutExpired if failure == "timeout" else TimeoutError if failure == "no_output" else ServiceManagerError
                with patch.object(suite_connector_launchd, "_command", side_effect=command), patch.object(Path, "home", return_value=Path(folder)):
                    with self.assertRaises(expected):
                        suite_connector_launchd.run(report)
                section = json.loads(json.dumps(report))["connector_launchd"]
                observed = section.get("status_observations", {}).get("absence", {})
                self.assertEqual(observed.get("error"), expected.__name__)
                self.assertEqual(observed["print"]["excerpt"], None if failure == "no_output" else raw[:2048])
                if failure == "disabled":
                    self.assertEqual(observed["print_disabled"]["excerpt"], raw[:2048])
                self.assertEqual(section["cleanup"], {"not_installed": True})
                self.assertEqual(section["checks"], [{"name": "scratch_removed", "status": "PASS"}])
                self.assertTrue(section["scratch_removed"])

    def test_context_precedes_failed_domain_selection(self) -> None:
        unsupported = "Could not print domain: 125: Domain does not support specified action"
        user = f"user/{os.getuid()}"
        for user_available in (True, False):
            with self.subTest(user_available=user_available), tempfile.TemporaryDirectory() as folder:
                # Successful selection then refuses a service observation: no
                # install or actual process effect is allowed in this fixture.
                calls: list[list[str]] = []
                def command(argv: list[str], env: dict[str, str], cwd: Path, deadline: float, **kwargs: object) -> tuple[int, bytes]:
                    calls.append(argv)
                    if argv == ["launchctl", "managername"]:
                        return 0, ("Background" + "x" * 600).encode()
                    if argv == ["launchctl", "print", DOMAIN]:
                        return 125, unsupported.encode()
                    if argv == ["launchctl", "print", user]:
                        return (0 if user_available else 1), b"user context"
                    if argv == ["launchctl", "print-disabled", user]:
                        return 0, b"disabled services = {\n}\n"
                    self.assertTrue(user_available)
                    self.assertEqual(argv[:2], ["launchctl", "print"])
                    self.assertTrue(argv[2].startswith(f"{user}/ai.happyranch.connector-probe-"))
                    return 1, b"service observation denied"
                report = {}
                with patch.object(suite_connector_launchd, "_command", side_effect=command), patch.object(Path, "home", return_value=Path(folder)):
                    with self.assertRaises(ServiceManagerError):
                        suite_connector_launchd.run(report)
                # The dispatcher serializes this same report even on failure.
                section = json.loads(json.dumps(report))["connector_launchd"]
                self.assertIn("context", section)
                context = section["context"]
                self.assertEqual(context["uid"], os.getuid())
                self.assertEqual(context["managername"], {"exit_code": 0, "diagnostic": ("Background" + "x" * 600)[:512]})
                self.assertEqual(context["domains"][DOMAIN], {"exit_code": 125, "diagnostic": unsupported})
                self.assertEqual(context["domains"][user], {"exit_code": 0 if user_available else 1, "diagnostic": "user context"})
                self.assertEqual(context["selected_domain"], user if user_available else None)
                self.assertIn("print_disabled", context)
                self.assertEqual(context["print_disabled"],
                                 {"exit_code": 0, "diagnostic": "disabled services = {\n}"} if user_available else None)
                self.assertEqual(section["domain"], context["selected_domain"])
                self.assertEqual(section["cleanup"], {"not_installed": True})
                self.assertTrue(section.get("scratch_removed"))
                self.assertEqual(section["checks"], [{"name": "scratch_removed", "status": "PASS"}])
                self.assertEqual(calls[:3], [["launchctl", "managername"], ["launchctl", "print", DOMAIN], ["launchctl", "print", user]])

    def test_print_disabled_context_precedes_status_failure(self) -> None:
        for code in (0, 5):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as folder:
                report = {}
                output = "  actual print-disabled output " + "z" * 600 + "\n"
                expected = {"exit_code": code, "diagnostic": output.strip()[:512]}
                calls = []
                def command(argv: list[str], env: dict[str, str], cwd: Path, deadline: float, **kwargs: object) -> tuple[int, bytes]:
                    calls.append(argv)
                    self.assertEqual(kwargs.get("output_cap"), 1048576)
                    if argv == ["launchctl", "managername"]:
                        return 0, b"Background"
                    if argv in (["launchctl", "print", DOMAIN], ["launchctl", "print", f"user/{os.getuid()}"]):
                        return 0, b"domain context"
                    if argv == ["launchctl", "print-disabled", DOMAIN]:
                        return code, output.encode()
                    self.assertEqual(argv[:2], ["launchctl", "print"])
                    self.assertTrue(argv[2].startswith(f"{DOMAIN}/ai.happyranch.connector-probe-"))
                    # This is the first status observation, before any lifecycle effect.
                    self.assertEqual(report["connector_launchd"]["context"].get("print_disabled"), expected)
                    return 1, b"service observation denied"
                with patch.object(suite_connector_launchd, "_command", side_effect=command), patch.object(Path, "home", return_value=Path(folder)):
                    with self.assertRaisesRegex(ServiceManagerError, "service observation denied"):
                        suite_connector_launchd.run(report)
                section = json.loads(json.dumps(report))["connector_launchd"]
                self.assertEqual(section["context"]["print_disabled"], expected)
                self.assertEqual(section["context"]["selected_domain"], DOMAIN)
                self.assertTrue(section.get("scratch_removed"))
                self.assertEqual(section["checks"], [{"name": "scratch_removed", "status": "PASS"}])
                self.assertEqual(section["cleanup"], {"not_installed": True})
                self.assertEqual(calls[-2], ["launchctl", "print-disabled", DOMAIN])

    def test_start_failure_records_bounded_diagnostics_before_cleanup(self) -> None:
        for observation in ("success", "nonzero", "timeout", "deadline"):
            with self.subTest(observation=observation), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                report = {}
                calls = []
                plist_path = None
                scratch = None
                observed_deadlines = []
                now = 1000.0
                diagnostic = "Bootstrap failed: 5: Input/output error " + "x" * 700
                def command(argv: list[str], env: dict[str, str], cwd: Path, deadline: float, **kwargs: object) -> tuple[int, bytes]:
                    nonlocal plist_path, scratch, now
                    calls.append(argv)
                    label = report["connector_launchd"]["label"]
                    target = f"{DOMAIN}/{label}"
                    if argv == ["launchctl", "managername"]:
                        return 0, b"Aqua"
                    if argv in (["launchctl", "print", DOMAIN], ["launchctl", "print", f"user/{os.getuid()}"]):
                        return 0, b"domain context"
                    if argv == ["launchctl", "print-disabled", DOMAIN]:
                        return 0, b"disabled services = (no disabled services)"
                    if argv == ["launchctl", "print", target]:
                        return 113, f'Could not find service "{label}" in domain'.encode()
                    if argv == ["launchctl", "bootstrap", DOMAIN, str(root / "Library/LaunchAgents" / f"{label}.plist")]:
                        plist_path = Path(argv[3])
                        value = plistlib.loads(plist_path.read_bytes())
                        scratch = Path(value["StandardOutPath"]).parent
                        return 5, diagnostic.encode()
                    if argv[0] in ("plutil", "log"):
                        self.assertTrue(plist_path.is_file(), "diagnostics must precede cleanup")
                        self.assertEqual(kwargs.get("output_cap"), 1048576)
                        observed_deadlines.append(deadline)
                        self.assertLessEqual(deadline, 1090.0)
                        if observation == "timeout":
                            raise TimeoutError("synthetic observation timeout " + "t" * 700)
                        if argv[0] == "plutil":
                            self.assertEqual(argv, ["plutil", "-lint", str(plist_path)])
                            return (7 if observation == "nonzero" else 0), ("lint " + "p" * 700).encode()
                        self.assertEqual(argv[:6], ["log", "show", "--last", "2m", "--style", "compact"])
                        self.assertEqual(argv[6], "--predicate")
                        self.assertIn('process == "launchd"', argv[7])
                        self.assertIn(label, argv[7])
                        if observation == "nonzero":
                            return 9, ("log denied " + "d" * 700).encode()
                        return 0, ("unrelated host event\n" + label + " " + "é" * 5000 + "\nother host event").encode()
                    self.assertIn(argv, [["launchctl", verb, target] for verb in ("enable", "disable", "bootout")])
                    if argv == ["launchctl", "disable", target] and observation == "deadline":
                        now += 91
                    return 0, b""
                with patch.object(suite_connector_launchd, "_command", side_effect=command), patch.object(Path, "home", return_value=root), patch.object(suite_connector_launchd.time, "monotonic", side_effect=lambda: now):
                    with self.assertRaisesRegex(ServiceManagerError, "bootstrap.*failed \\(5\\)") as raised:
                        suite_connector_launchd.run(report)
                # Failure remains the original bootstrap failure, not a PASS
                # or an optional diagnostic error. Existing 200-char cap stays.
                self.assertIn(diagnostic[:200], str(raised.exception))
                self.assertNotIn(diagnostic[:201], str(raised.exception))
                section = json.loads(json.dumps(report))["connector_launchd"]
                self.assertIn("start_failure", section)
                failure = section["start_failure"]
                self.assertEqual(failure["plist"], {"mode": "0o600", "owner_uid": os.getuid(), "current_uid": os.getuid(), "LimitLoadToSessionType": ["Aqua", "Background"]})
                for key in ("plutil", "launchd_log"):
                    if observation in ("timeout", "deadline"):
                        self.assertEqual(failure[key]["error"], "TimeoutError")
                        self.assertLessEqual(len(failure[key]["diagnostic"]), 512)
                    else:
                        self.assertEqual(failure[key]["exit_code"], (7 if key == "plutil" else 9) if observation == "nonzero" else 0)
                        self.assertLessEqual(len(failure[key].get("diagnostic", "")), 512)
                if observation == "success":
                    excerpt = failure["launchd_log"]["excerpt"]
                    self.assertTrue(excerpt.startswith(section["label"]))
                    self.assertNotIn("host event", excerpt)
                    self.assertLessEqual(len(excerpt.encode()), 4096)
                    self.assertGreater(len(excerpt.encode()), 4000)
                if observation == "nonzero":
                    self.assertIn("log denied", failure["launchd_log"]["diagnostic"])
                self.assertEqual(len(observed_deadlines), 0 if observation == "deadline" else 2)
                self.assertFalse(plist_path.exists())
                self.assertFalse(scratch.exists())
                self.assertTrue(section["scratch_removed"])
                self.assertEqual(section["checks"], [{"name": name, "status": "PASS"} for name in ("unique_service_absent_before_install", "installed", "scratch_removed")])
                self.assertEqual(section["cleanup"], {"bootout_exit": 0, "service_absent": True, "plist_absent": True})
                # No start success, restart, kill or native call is fabricated.
                self.assertEqual(sum(argv[:2] == ["launchctl", "bootstrap"] for argv in calls), 1)



# C3 tests exercise the public supervisor/CLI and real child-process exits.
class _LoopProvider:
    def __init__(self) -> None:
        self.listening = False

    def start(self) -> None:
        self.listening = True

    def stop(self) -> None:
        self.listening = False


def _loop_supervisor(root: Path, *, watchdog: float = 2, poll: float = 0.01) -> ConnectorSupervisor:
    from types import SimpleNamespace
    from runtime.remote_access.readiness import ReadinessReport

    readiness = Mock()
    readiness.evaluate.return_value = ReadinessReport(ready=True)
    store = Mock()
    store.load.return_value = SimpleNamespace(revocation_epoch=0)
    return ConnectorSupervisor(
        config=ConnectorConfig(managed_dir_root=str(root), watchdog_sec=watchdog, poll_seconds=poll),
        readiness=readiness, state_store=store, provider=_LoopProvider(),
    )


def _record_path(root: Path) -> Path:
    return root / "happyranch-connector" / f"{LABEL}.readiness.json"


class DarwinCredentialFiles(unittest.TestCase):
    def test_state_paths_defaults_overrides_and_linux_parity(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.object(Path, "home", return_value=Path(folder)), patch.dict(os.environ, {"HOME": folder}):
            root = Path(folder)
            with patch("sys.platform", "darwin"):
                supervisor = ConnectorSupervisor(config=ConnectorConfig())
                expected = root / "Library/Application Support/HappyRanch/connector/happyranch-connector"
                self.assertEqual(supervisor._managed_state_root(), expected)
                self.assertEqual(supervisor._readiness_path(), expected / f"{LABEL}.readiness.json")
                custom = ConnectorSupervisor(config=ConnectorConfig(managed_dir_root=str(root / "override"), state_dir="custom", unit_name="ai.example.other"))
                self.assertEqual(custom._readiness_path(), root / "override/custom/ai.example.other.readiness.json")
                self.assertFalse(expected.exists(), "path selection must not create state")
            with patch("sys.platform", "linux"):
                self.assertEqual(ConnectorSupervisor(config=ConnectorConfig())._managed_state_root(), root / ".local/state/happyranch-connector")
                self.assertEqual(ConnectorSupervisor(config=ConnectorConfig(system=True))._managed_state_root(), Path("/var/lib/happyranch-connector"))
                self.assertEqual(ConnectorSupervisor(config=ConnectorConfig(managed_dir_root=folder, state_dir="custom", system=True))._managed_state_root(), root / "custom")

    def test_readiness_directories_private_and_aliases_refused(self) -> None:
        from runtime.remote_access.launchd_readiness import LaunchdReadiness

        for kind in ("new", "leaf-link", "parent-link", "dangling-link", "loose-directory"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                outside = root / "outside"
                outside.mkdir(mode=0o700)
                parent = root / "state/nested"
                if kind == "leaf-link":
                    parent.parent.mkdir()
                    parent.symlink_to(outside, target_is_directory=True)
                elif kind == "parent-link":
                    parent.parent.symlink_to(outside, target_is_directory=True)
                elif kind == "dangling-link":
                    parent.parent.symlink_to(root / "missing", target_is_directory=True)
                elif kind == "loose-directory":
                    parent.mkdir(parents=True)
                    parent.chmod(0o755)
                sink = LaunchdReadiness(parent / "ready.json", 10)
                try:
                    if kind == "new":
                        old_umask = os.umask(0)
                        try:
                            sink.start()
                        finally:
                            os.umask(old_umask)
                        for directory in (parent.parent, parent):
                            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
                            self.assertEqual(directory.stat().st_uid, os.geteuid())
                    else:
                        with self.assertRaises(OSError):
                            sink.start()
                    self.assertEqual(list(outside.iterdir()), [])
                finally:
                    sink.stop()

    def test_darwin_token_real_file_guards_and_bounded_secret_free_errors(self) -> None:
        from runtime.remote_access.credentials import CredentialUnavailable, FileDaemonCredentialProvider

        cases = ("valid", "limit", "empty", "whitespace", "unicode-whitespace", "oversize", "invalid-utf8", "group", "other", "symlink", "parent-link", "hardlink", "directory", "fifo", "missing", "foreign-euid")
        for kind in cases:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder, patch("sys.platform", "darwin"), patch.dict(os.environ, {}, clear=True):
                root = Path(folder)
                token = root / "daemon.token"
                token.write_bytes(b"synthetic-daemon-bearer\n")
                token.chmod(0o600)
                if kind in {"limit", "empty", "whitespace", "unicode-whitespace", "oversize", "invalid-utf8"}:
                    token.write_bytes({"limit": b"x" * 65536, "empty": b"", "whitespace": b" \n", "unicode-whitespace": "\u2003".encode(), "oversize": b"x" * 65537, "invalid-utf8": b"\xff"}[kind])
                elif kind in {"group", "other"}:
                    token.chmod(0o640 if kind == "group" else 0o604)
                elif kind == "hardlink":
                    os.link(token, root / "alias")
                elif kind in {"symlink", "directory", "fifo", "missing"}:
                    token.rename(root / "original")
                    if kind == "symlink":
                        token.symlink_to(root / "original")
                    elif kind == "directory":
                        token.mkdir()
                    elif kind == "fifo":
                        os.mkfifo(token, 0o600)
                elif kind == "parent-link":
                    (root / "alias").symlink_to(root, target_is_directory=True)
                    token = root / "alias/daemon.token"
                provider = ConnectorSupervisor(config=ConnectorConfig(daemon_token_path=str(token))).credential_provider()
                self.assertIsInstance(provider, FileDaemonCredentialProvider)
                if kind in {"valid", "limit"}:
                    expected = "x" * 65536 if kind == "limit" else "synthetic-daemon-bearer"
                    self.assertEqual(provider.read_bearer(), expected)
                    token.chmod(0o400)
                    self.assertEqual(provider.read_bearer(), expected)
                else:
                    identity = os.geteuid() + 1 if kind == "foreign-euid" else os.geteuid()
                    with patch("os.geteuid", return_value=identity), self.assertRaises(CredentialUnavailable) as failure:
                        provider.read_bearer()
                    self.assertTrue(str(failure.exception).startswith("macOS daemon token "))
                    self.assertLess(len(str(failure.exception)), 120)
                    self.assertNotIn("synthetic-daemon-bearer", str(failure.exception))
                    self.assertIsNone(failure.exception.__cause__)

    def test_darwin_refuses_loadcredential_and_requires_explicit_token_cleanly(self) -> None:
        from runtime.remote_access.supervisor import ConnectorConfigError

        for settings, environment in (({}, {}), ({"credentials_directory": "/synthetic/systemd"}, {}), ({"daemon_token_path": "/synthetic/token"}, {"CREDENTIALS_DIRECTORY": "/synthetic/systemd"}), ({"daemon_token_path": "/synthetic/token"}, {"CREDENTIALS_DIRECTORY": ""})):
            with self.subTest(settings=settings, environment=environment), tempfile.TemporaryDirectory() as folder, patch("sys.platform", "darwin"), patch.dict(os.environ, environment, clear=True):
                config = ConnectorConfig(tenant_id="t", home_id="h", connector_id="c", daemon_port=1234, policy_path="/synthetic/policy", **settings)
                with self.assertRaises(ConnectorConfigError):
                    ConnectorSupervisor(config=config).credential_provider()
                path = Path(folder) / "config.json"
                config.to_file(path)
                stderr = io.StringIO()
                with redirect_stderr(stderr):
                    self.assertEqual(cli.main(["status", "--config", str(path)]), 1)
                text = stderr.getvalue()
                self.assertTrue(text.startswith("error: macOS connector "), text)
                self.assertNotIn("Traceback", text)
                self.assertNotIn("/synthetic", text)

    def test_linux_credential_selection_and_file_semantics_unchanged(self) -> None:
        import hashlib
        import inspect
        from runtime.remote_access.credentials import FileDaemonCredentialProvider, SystemdCredentialProvider

        self.assertEqual(hashlib.sha256(inspect.getsource(SystemdServiceManager).encode()).hexdigest(), "88be99a3bb10db48794e363ccd91bfb82b3f555da33e13ee2c57afa3ede0e7fd")
        with tempfile.TemporaryDirectory() as folder, patch("sys.platform", "linux"), patch.dict(os.environ, {}, clear=True):
            root = Path(folder)
            token = root / "daemon.token"
            token.write_text("synthetic-linux-token")
            token.chmod(0o600)
            alias = root / "alias"
            alias.symlink_to(token)
            supervisor = ConnectorSupervisor(config=ConnectorConfig(daemon_token_path=str(alias)))
            self.assertIs(type(supervisor.credential_provider()), FileDaemonCredentialProvider)
            self.assertEqual(supervisor.credential_provider().read_bearer(), "synthetic-linux-token")
            supervisor.config.credentials_directory = folder
            self.assertIs(type(supervisor.credential_provider()), SystemdCredentialProvider)
            self.assertEqual(supervisor.credential_provider().read_bearer(), "synthetic-linux-token")
            with patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": str(root / "missing")}):
                from runtime.remote_access.credentials import CredentialUnavailable
                with self.assertRaises(CredentialUnavailable):
                    supervisor.credential_provider().read_bearer()

    def test_enrollment_reader_contract(self) -> None:
        from runtime.remote_access import credentials
        reader = getattr(credentials, "read_darwin_enrollment", None)
        self.assertTrue(callable(reader), "Darwin enrollment reader must be available to C5/C6")
        from runtime.remote_access.launchd_files import DARWIN_ENROLLMENT_KEY_PATH

        self.assertEqual(DARWIN_ENROLLMENT_KEY_PATH, Path("~/Library/Application Support/HappyRanch/connector/happyranch-connector/enrollment.key"))
        for kind in ("valid", "limit", "empty", "oversize", "mode", "owner", "hardlink", "symlink", "parent-link", "fifo", "directory", "relative", "dotdot"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                path = root / "enrollment.key"
                path.write_bytes(b"synthetic-enrollment-key\n")
                path.chmod(0o600)
                if kind in {"limit", "empty", "oversize"}:
                    path.write_bytes({"limit": b"x" * 65536, "empty": b" \n", "oversize": b"x" * 65537}[kind])
                elif kind == "mode":
                    path.chmod(0o400)
                elif kind == "hardlink":
                    os.link(path, root / "alias")
                elif kind == "symlink":
                    path.rename(root / "original")
                    path.symlink_to(root / "original")
                elif kind == "parent-link":
                    (root / "alias").symlink_to(root, target_is_directory=True)
                    path = root / "alias/enrollment.key"
                elif kind in {"fifo", "directory"}:
                    path.unlink()
                    os.mkfifo(path, 0o600) if kind == "fifo" else path.mkdir()
                elif kind == "relative":
                    path = Path("enrollment.key")
                elif kind == "dotdot":
                    path = root / "child/../enrollment.key"
                identity = os.geteuid() + 1 if kind == "owner" else os.geteuid()
                with patch("os.geteuid", return_value=identity):
                    if kind in {"valid", "limit"}:
                        value = reader(path)
                        self.assertIsInstance(value, bytearray)
                        self.assertEqual(value, path.read_bytes())
                    else:
                        with self.assertRaises(ValueError) as failure:
                            reader(path)
                        self.assertNotIn("synthetic-enrollment-key", str(failure.exception))

    def test_plist_cli_and_status_expose_no_file_contents(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch("sys.platform", "darwin"), patch.dict(os.environ, {}, clear=True):
            root = Path(folder)
            token, key = root / "daemon.token", root / "enrollment.key"
            token.write_text("synthetic-c4-bearer-value")
            key.write_text("synthetic-c4-enrollment-value")
            token.chmod(0o600)
            key.chmod(0o600)
            config = ConnectorConfig(tenant_id="t", home_id="h", connector_id="c", daemon_port=1234,
                                     daemon_token_path=str(token), managed_dir_root=folder,
                                     state_path=str(root / "trust.json"), policy_path=str(root / "policy.json"))
            config_path = root / "config.json"
            config.to_file(config_path)
            runner = Runner(self, [row("print", DOMAIN), *absent(), enabled()])
            manager = LaunchdServiceManager(unit_dir=root / "agents", run=runner)
            supervisor = ConnectorSupervisor(config=config, config_path=config_path, manager=manager)
            text = supervisor._launch_agent_text()
            value = plistlib.loads(text.encode())
            self.assertEqual(value["ProgramArguments"][-2:], ["--config", str(config_path)])
            self.assertEqual(supervisor.credential_provider().read_bearer(), token.read_text())
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch.object(cli, "ConnectorSupervisor", return_value=supervisor), redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(cli.main(["status", "--config", str(config_path)]), 0)
            status = json.loads(stdout.getvalue())
            self.assertEqual(status["load_state"], "not-found")
            self.assertFalse(status["ready"])
            self.assertFalse(runner.rows)
            for secret in (token.read_text(), key.read_text()):
                self.assertNotIn(secret, text + stdout.getvalue() + stderr.getvalue() + config_path.read_text())


class ConnectorReadinessLifecycle(unittest.TestCase):
    def test_record_lifecycle_and_permissions(self) -> None:
        from runtime.remote_access.readiness import ReadinessReport

        with tempfile.TemporaryDirectory() as folder, patch("sys.platform", "darwin"):
            root = Path(folder)
            supervisor = _loop_supervisor(root)
            path = _record_path(root)
            self.assertFalse(path.exists(), "construction/status must not publish readiness")
            observations = []

            def evaluate(_now: datetime) -> ReadinessReport:
                self.assertTrue(path.is_file(), "run must publish starting before readiness evaluation")
                if not observations:
                    initial = json.loads(path.read_text())
                    self.assertFalse(initial["ready"])
                    self.assertIsNone(initial["last_healthy_wall_time"])
                    self.assertEqual(initial["reason"], "starting")
                return ReadinessReport(ready=True)

            def observe(_seconds: float) -> None:
                self.assertTrue(path.is_file(), "healthy passes must publish readiness")
                value = json.loads(path.read_text())
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertEqual(path.stat().st_uid, os.getuid())
                self.assertEqual(value["pid"], os.getpid())
                self.assertTrue(value["ready"])
                self.assertGreaterEqual(value["last_healthy_wall_time"], value["process_started_at"])
                observations.append(value)

            supervisor._readiness.evaluate.side_effect = evaluate
            self.assertEqual(supervisor.run(max_iterations=3, wait_fn=observe), 0)
            self.assertEqual([item["sequence"] for item in observations], [2, 3, 4])
            self.assertEqual(len({item["process_started_at"] for item in observations}), 1)
            final = json.loads(path.read_text())
            self.assertFalse(final["ready"])
            self.assertEqual(final["reason"], "stopping")
            self.assertGreater(final["sequence"], observations[-1]["sequence"])
            self.assertFalse(supervisor._injected_provider.listening)

    def test_record_atomic_publication_failure(self) -> None:
        for failure in ("fsync", "replace"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as folder, patch("sys.platform", "darwin"):
                root = Path(folder)
                supervisor = _loop_supervisor(root)
                path = _record_path(root)
                observed = []
                original_replace = os.replace
                original_fsync = os.fsync

                def observe(_seconds: float) -> None:
                    self.assertTrue(path.is_file(), "no complete record was published")
                    previous = path.read_bytes()
                    syncs = []

                    def fsync(fd: int) -> None:
                        syncs.append(fd)
                        if failure == "fsync":
                            raise OSError("synthetic fsync failure")
                        return original_fsync(fd)

                    def replace(source: Path, target: Path, **kwargs: int) -> None:
                        staged = path.parent / source if kwargs else Path(source)
                        self.assertEqual(path.parent / target if kwargs else Path(target), path)
                        self.assertNotEqual(staged, path)
                        self.assertEqual(staged.parent, path.parent)
                        self.assertEqual(stat.S_IMODE(staged.stat().st_mode), 0o600)
                        self.assertEqual(path.read_bytes(), previous)
                        self.assertTrue(syncs, "publication must fsync before rename")
                        self.assertTrue(json.loads(staged.read_text())["ready"])
                        if failure == "replace":
                            raise OSError("synthetic rename failure")
                        return original_replace(source, target, **kwargs)

                    with patch("os.fsync", side_effect=fsync), patch("os.replace", side_effect=replace):
                        with self.assertRaises(OSError):
                            supervisor._notify_fn("WATCHDOG=1\n")
                    self.assertEqual(path.read_bytes(), previous, "failed publication replaced complete record")
                    self.assertEqual(list(path.parent.iterdir()), [path], "pending inode leaked")
                    observed.append(failure)

                self.assertEqual(supervisor.run(max_iterations=1, wait_fn=observe), 0)
                self.assertEqual(observed, [failure])

    def test_status_matrix_and_cli_fields(self) -> None:
        from runtime.remote_access.launchd_agent import LaunchdStatus

        now = 1000.0
        record = dict(pid=1234, process_started_at=900.0, ready=True,
                      last_healthy_wall_time=995.0, sequence=3, reason="healthy")
        # Freshness = watchdog(10) + one configured poll interval(2).
        cases = [
            ("ready", "loaded", "running", 1234, record),
            ("ready", "loaded", "running", 1234, {**record, "last_healthy_wall_time": 988.0}),
            ("stale-readiness", "loaded", "running", 1234, {**record, "last_healthy_wall_time": 987.9}),
            ("stale-readiness", "loaded", "running", 1234, {**record, "last_healthy_wall_time": 1001.0}),
            ("pid-mismatch", "loaded", "running", 777, record),
            ("running-not-ready", "loaded", "running", 1234, {**record, "ready": False, "last_healthy_wall_time": None, "reason": "starting"}),
            ("record-missing", "loaded", "running", 1234, None),
            ("starting", "loaded", "spawn scheduled", 0, record),
            ("not-loaded", "unloaded", "dead", 0, record),
            ("not-loaded", "not-found", "dead", 0, None),
        ]
        with tempfile.TemporaryDirectory() as folder, patch("sys.platform", "darwin"), patch("time.time", return_value=now):
            root = Path(folder)
            path = _record_path(root)
            path.parent.mkdir(parents=True)
            config = ConnectorConfig(managed_dir_root=folder, watchdog_sec=10, poll_seconds=2)
            for expected, loaded, substate, pid, data in cases:
                with self.subTest(expected=expected, pid=pid, data=data):
                    path.unlink(missing_ok=True)
                    if data is not None:
                        path.write_text(json.dumps(data))
                        path.chmod(0o600)
                    process = LaunchdStatus(unit_name=LABEL, active_state="active" if pid else "inactive",
                                            sub_state=substate, load_state=loaded, pid=pid,
                                            unit_file_state="enabled", domain=DOMAIN, last_exit_code=9)
                    manager = Mock()
                    manager.status.return_value = process
                    supervisor = ConnectorSupervisor(config=config, manager=manager)
                    stdout = io.StringIO()
                    with patch.object(cli, "_load_config", return_value=config), patch.object(cli, "ConnectorSupervisor", return_value=supervisor), redirect_stdout(stdout):
                        self.assertEqual(cli.main(["status"]), 0)
                    value = json.loads(stdout.getvalue())
                    self.assertEqual(value.get("readiness_state"), expected)
                    self.assertEqual(value.get("ready"), expected == "ready")
                    for key, original in process.__dict__.items():
                        self.assertEqual(value[key], original, f"changed existing launchd field {key}")
                    self.assertEqual(value["readiness_max_age_seconds"], 12)

    def test_invalid_readiness_records(self) -> None:
        from runtime.remote_access.launchd_agent import LaunchdStatus

        base = dict(pid=1234, process_started_at=900.0, ready=True,
                    last_healthy_wall_time=995.0, sequence=3, reason="healthy")
        malformed = ["{", "[]", "x" * 4097, "[" * 1500, b"\xff",
                     json.dumps(base).replace('"pid": 1234', '"pid": 7, "pid": 1234'), json.dumps({**base, "pid": True}),
                     json.dumps({**base, "ready": "true"}), json.dumps({**base, "sequence": True}),
                     json.dumps({**base, "last_healthy_wall_time": float("nan")}),
                     json.dumps({**base, "last_healthy_wall_time": None}),
                     json.dumps({**base, "process_started_at": 999.0}),
                     json.dumps({**base, "reason": "synthetic-bearer-material"})]
        with tempfile.TemporaryDirectory() as folder, patch("sys.platform", "darwin"), patch("time.time", return_value=1000):
            root = Path(folder)
            path = _record_path(root)
            path.parent.mkdir(parents=True)
            manager = Mock()
            manager.status.return_value = LaunchdStatus(unit_name=LABEL, active_state="active", sub_state="running", load_state="loaded", pid=1234)
            supervisor = ConnectorSupervisor(config=ConnectorConfig(managed_dir_root=folder), manager=manager)
            for raw in malformed:
                with self.subTest(raw=str(raw)[:80]):
                    path.write_bytes(raw if isinstance(raw, bytes) else raw.encode())
                    path.chmod(0o600)
                    value = supervisor.status().__dict__
                    self.assertEqual(value.get("readiness_state"), "record-corrupt")
                    self.assertFalse(value.get("ready"))
                    self.assertNotIn("synthetic-bearer-material", json.dumps(value))
            path.write_text(json.dumps(base))
            path.chmod(0o644)
            self.assertEqual(supervisor.status().__dict__.get("readiness_state"), "record-foreign")
            path.chmod(0o600)
            with patch("os.getuid", return_value=os.getuid() + 1):
                self.assertEqual(supervisor.status().__dict__.get("readiness_state"), "record-foreign")
            sibling = path.with_suffix(".foreign")
            os.link(path, sibling)
            self.assertEqual(supervisor.status().__dict__.get("readiness_state"), "record-foreign")
            sibling.unlink()
            sibling = path.with_suffix(".foreign")
            path.rename(sibling)
            path.symlink_to(sibling)
            self.assertEqual(supervisor.status().__dict__.get("readiness_state"), "record-foreign")
            path.unlink()
            os.mkfifo(path, 0o600)
            self.assertEqual(supervisor.status().__dict__.get("readiness_state"), "record-foreign")

    def _child(self, scenario: str, root: Path) -> tuple[int, str]:
        import subprocess
        import sys

        program = '''
import json, sys, time
from pathlib import Path
from scripts.mac_native.test_connector_launchd import _loop_supervisor
from runtime.remote_access.readiness import ReadinessReport
sys.platform = "darwin"
root = Path(sys.argv[1])
scenario = sys.argv[2]
supervisor = _loop_supervisor(root, watchdog=0.2, poll=0.02)
wait = time.sleep
if scenario == "startup-hang":
    supervisor._readiness.evaluate.side_effect = lambda now: time.sleep(10)
elif scenario == "loop-hang":
    wait = lambda seconds: time.sleep(10)
elif scenario == "publication-hang":
    import os
    os.fsync = lambda fd: time.sleep(10)
elif scenario == "readiness-loss":
    supervisor._readiness.evaluate.side_effect = [ReadinessReport(ready=True), ReadinessReport(ready=False)]
elif scenario == "listener-loss":
    def wait(seconds: float) -> None:
        supervisor._injected_provider.listening = False
result = supervisor.run(max_iterations=30, wait_fn=wait)
print(json.dumps({"result": result, "listening": supervisor._injected_provider.listening}), flush=True)
sys.exit(result)
'''
        with subprocess.Popen([sys.executable, "-B", "-c", program, str(root), scenario], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
            try:
                stdout, stderr = child.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                stdout, stderr = child.communicate()
                self.fail(f"{scenario}: connector hung instead of self-watchdog exit; {stderr[-500:]}")
            self.assertNotIn("Traceback", stderr, stderr)
            return child.returncode, stdout

    def test_watchdog_kills_stalled_subprocess(self) -> None:
        for scenario in ("startup-hang", "loop-hang", "publication-hang"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as folder:
                code, _ = self._child(scenario, Path(folder))
                self.assertEqual(code, 75, "self-watchdog must force a distinct nonzero exit")

    def test_healthy_subprocess_survives_multiple_deadlines(self) -> None:
        import time

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            started = time.monotonic()
            code, output = self._child("healthy", root)
            self.assertEqual(code, 0)
            self.assertGreater(time.monotonic() - started, 0.4, "must span multiple watchdog intervals")
            self.assertFalse(json.loads(output)["listening"])
            path = _record_path(root)
            self.assertTrue(path.is_file(), "healthy child never published readiness")
            record = json.loads(path.read_text())
            self.assertGreaterEqual(record["sequence"], 32)
            self.assertFalse(record["ready"])

    def test_readiness_and_listener_loss_close_admission(self) -> None:
        for scenario in ("readiness-loss", "listener-loss"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                code, output = self._child(scenario, root)
                self.assertEqual(code, 1)
                self.assertFalse(json.loads(output)["listening"])
                path = _record_path(root)
                self.assertTrue(path.is_file(), "readiness loss never persisted a not-ready record")
                record = json.loads(path.read_text())
                self.assertFalse(record["ready"])
                self.assertEqual(record["reason"], "readiness-or-listener-lost")
                self.assertIsNotNone(record["last_healthy_wall_time"])

    def test_linux_notify_and_status_bytes(self) -> None:
        import socket

        with tempfile.TemporaryDirectory(dir="/tmp", prefix="c3-") as folder, patch("sys.platform", "linux"):
            root = Path(folder)
            socket_path = root / "notify.sock"
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
                receiver.bind(str(socket_path))
                receiver.settimeout(0.5)
                with patch.dict(os.environ, {"NOTIFY_SOCKET": str(socket_path)}):
                    supervisor = _loop_supervisor(root)
                    self.assertEqual(supervisor.run(max_iterations=3, wait_fn=lambda seconds: None), 0)
                    supervisor.shutdown()
                messages = []
                try:
                    for _ in range(4):
                        messages.append(receiver.recv(1024))
                except TimeoutError:
                    self.fail(f"Linux sd_notify datagrams missing: {messages!r}")
                self.assertEqual(messages, [b"READY=1\n", b"WATCHDOG=1\n", b"WATCHDOG=1\n", b"STOPPING=1\n"])
            self.assertFalse(_record_path(root).exists())
            manager = Mock()
            from runtime.remote_access.service_manager import ServiceStatus
            process = ServiceStatus("x.service", "active", "running", "loaded", 55, "enabled")
            manager.status.return_value = process
            self.assertIs(ConnectorSupervisor(config=ConnectorConfig(), manager=manager).status(), process)
            self.assertEqual(json.dumps(process.__dict__, sort_keys=True), '{"active_state": "active", "load_state": "loaded", "pid": 55, "sub_state": "running", "unit_file_state": "enabled", "unit_name": "x.service"}')


if __name__ == "__main__":
    unittest.main()

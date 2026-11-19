"""Standalone CLI/native boundary tests; synthetic bus, no live helper actions."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout, redirect_stderr
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from gi.repository import Gio, GLib

import connection_cli as native

spec = importlib.util.spec_from_file_location("connection_command", Path(__file__).parents[3] / "cli/commands/connection.py")
command = importlib.util.module_from_spec(spec)
spec.loader.exec_module(command)

LIFECYCLE = {"enable": ("Enable", 480000), "turn-off": ("Disable", 300000),
             "create-invite": ("IssueInvite", 120000), "cancel-invite": ("CancelInvite", 120000),
             "replace-failed": ("ReplaceFailed", 480000), "recover-invite": ("RecoverInvite", 120000),
             "retry-policy-renewal": ("RetryPolicyRenewal", 120000)}
INVITE = "hrinvite2:synthetic_invite_canary"


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return 123

    def close(self) -> None:
        pass  # Retain the synthetic terminal transcript after product teardown.


class Bus:
    def __init__(self) -> None:
        device = dict(label="Same &amp; label", fingerprint="a" * 24, comparison_phrase="synthetic comparison")
        other = {**device, "fingerprint": "b" * 24, "comparison_phrase": "other comparison"}
        self.snapshot = dict(phase="ready", ready=True, devices=[device, other], pending=device, selected=None)
        self.calls = []
        self.error = self.status_error = None
        self.raw = self.ack = None
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed

    def set_exit_on_close(self, _value: bool) -> None:
        pass

    def close(self, *_args: object) -> None:
        self.closed = True

    def call(self, *args: object) -> None:
        name, path, interface, method, inputs, signature, flags, timeout, cancel, callback, data = args
        self.calls.append((name, path, interface, method, inputs, signature.dup_string(), flags, timeout))
        if method == "NextDevice":
            current = self.snapshot["selected"]
            self.snapshot["selected"] = self.snapshot["devices"][1 if current == self.snapshot["devices"][0] else 0]
        reply = (json.dumps(dict(result="complete", invite=INVITE, expires_at=int(time.time()) + 200))
                 if method == "IssueInvite" else '"accepted"' if method in
                 {entry[0] for entry in LIFECYCLE.values()} else '"ok"')
        raw = self.raw or json.dumps(self.snapshot) if method in ("Status", "NextDevice") else self.ack or reply
        error = self.status_error if method == "Status" else self.error
        GLib.idle_add(lambda: (callback(self, (raw, error), data), False)[1])

    def call_finish(self, result: tuple) -> GLib.Variant:
        raw, error = result
        if error:
            raise Gio.DBusError.new_for_dbus_error(error, "private-error-canary")
        return GLib.Variant("(s)", (raw,))


class ConnectionCLITests(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = Bus()
        self.opens = []
        self.holds = []

        def connect(*args: object) -> None:
            self.bus.closed = False
            self.opens.append(args[:3])
            GLib.idle_add(lambda: (args[-2](None, None, None), False)[1])

        for target, value in (("new_for_address", connect), ("new_for_address_finish", lambda _result: self.bus)):
            active = patch.object(Gio.DBusConnection, target, value)
            active.start()
            self.addCleanup(active.stop)

    def run_native(self, action: str, choices: str = "revoke\n", desktop: bool = True,
                   tty: bool = True) -> tuple:
        out, err, terminal = io.StringIO(), Terminal() if tty else io.StringIO(), Terminal()

        def hold(interval: int, callback: object) -> int:
            self.holds.append((interval, self.bus.closed))
            return GLib.idle_add(callback)  # Advance only the product's budget timer, not the bus reply.

        with patch.object(sys, "argv", ["connection_cli.py", action]), \
                patch.object(native, "desktop_available", return_value=desktop), \
                patch.object(native.os, "open", side_effect=None if tty else OSError(), return_value=123), \
                patch.object(native.os, "fdopen", return_value=terminal), \
                patch.object(native.os, "read", return_value=choices.encode()), \
                patch.object(GLib, "timeout_add", side_effect=hold), \
                patch.object(sys, "stdin", Terminal(choices)), \
                patch.object(native.select, "select", side_effect=lambda readers, *_args: (readers, [], [])), \
                redirect_stdout(out), redirect_stderr(err):
            code = native.main()
        self.tty_output = terminal.getvalue()
        text = out.getvalue() + err.getvalue()
        for secret in ("a" * 24, "b" * 24, "fingerprint", "private-error-canary", INVITE):
            self.assertNotIn(secret, text)
        return code, json.loads(out.getvalue()), err.getvalue()

    def test_public_grammar_and_availability(self) -> None:
        parser = argparse.ArgumentParser()
        command.register(parser.add_subparsers(dest="command"))
        for platform, host in (("linux", "available"), ("darwin", "unavailable"), ("other", "unavailable")):
            with self.subTest(platform=platform), patch.object(command.sys, "platform", platform):
                out = io.StringIO()
                with redirect_stdout(out):
                    command.cmd_connection(parser.parse_args(["connection", "status"]))
                self.assertEqual(json.loads(out.getvalue())["roles"], dict(host=host, client=host))
                if platform != "linux":
                    with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as stopped:
                        command.cmd_connection(parser.parse_args(["connection", "host", "approve-device"]))
                    self.assertEqual(stopped.exception.code, 1)
        with patch.object(command.sys, "platform", "linux"), patch.object(command.os, "execv", side_effect=SystemExit(0)) as execute:
            with self.assertRaises(SystemExit):
                command.cmd_connection(parser.parse_args(["connection", "host", "devices"]))
            self.assertEqual(execute.call_args.args, ("/usr/bin/python3", ["/usr/bin/python3", "-I", str(command.native_entry()), "devices"]))
            for action in command.UNAVAILABLE:
                with self.subTest(action=action):
                    out = io.StringIO()
                    with redirect_stdout(out), self.assertRaises(SystemExit) as stopped:
                        command.cmd_connection(parser.parse_args(["connection", "host", action]))
                    self.assertEqual(stopped.exception.code, 1)
                    self.assertEqual(json.loads(out.getvalue()), dict(code="unavailable", effect="none", recovery="nativeRecovery"))
            self.assertEqual(execute.call_count, 1)
        for args in (("client", "unknown-action"), ("host", "revoke-device", "--device-id", "synthetic"), ("host", "unknown-action")):
            with self.subTest(args=args), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stopped:
                parser.parse_args(["connection", *args])
            self.assertEqual(stopped.exception.code, 2)

    def test_mac_bundle_dispatch_and_closed_host(self) -> None:
        parser = argparse.ArgumentParser()
        command.register(parser.add_subparsers(dest="command"))
        with tempfile.TemporaryDirectory() as scratch:
            contents = Path(scratch) / "HappyRanch.app/Contents"
            executable = contents / "Resources/daemon/happyranch"
            executable.parent.mkdir(parents=True)
            executable.touch()
            entry = contents / "MacOS/HappyRanch"
            entry.parent.mkdir()
            entry.write_text("synthetic-native-owner")
            entry.chmod(0o755)
            with patch.object(command.sys, "platform", "darwin"), \
                    patch.object(command.sys, "executable", str(executable)), \
                    patch.object(command.sys, "frozen", True, create=True), \
                    patch.object(command.os, "execv", side_effect=SystemExit(0)) as execute:
                out = io.StringIO()
                with redirect_stdout(out):
                    command.cmd_connection(parser.parse_args(["connection", "status"]))
                self.assertEqual(json.loads(out.getvalue()), dict(platform="mac", roles=dict(host="unavailable", client="available")))
                for action in command.CLIENT_ACTIONS:
                    with self.subTest(action=action), self.assertRaises(SystemExit) as stopped:
                        command.cmd_connection(parser.parse_args(["connection", "client", action]))
                    self.assertEqual(stopped.exception.code, 0)
                    self.assertEqual(execute.call_args.args, (str(entry), [str(entry), "--connection-client", action]))
                execute.reset_mock()
                for action in command.HOST_ACTIONS + command.UNAVAILABLE:
                    with self.subTest(host=action), redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as stopped:
                        command.cmd_connection(parser.parse_args(["connection", "host", action]))
                    self.assertEqual(stopped.exception.code, 1)
                execute.assert_not_called()
                entry.unlink()
                with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as stopped:
                    command.cmd_connection(parser.parse_args(["connection", "client", "accept-invite"]))
                self.assertEqual(stopped.exception.code, 1)
                execute.assert_not_called()
                with patch.object(command.sys, "frozen", False):
                    self.assertIsNone(command.mac_client_entry())

    def test_fixed_transport_and_projection(self) -> None:
        for action, method in native.METHODS.items():
            if action in LIFECYCLE:
                continue  # Lifecycle has terminal frames/admission, not device acknowledgements.
            with self.subTest(action=action):
                self.bus.calls.clear()
                self.opens.clear()
                self.bus.snapshot["selected"] = None
                code, result, _ = self.run_native(action, "next\nrevoke\n")
                self.assertEqual(code, 0)
                expected = ["Status", "NextDevice", "NextDevice", "RevokeSelected", "Status"] if action == "revoke-device" else (
                    [method, "Status"] if method in native.contract.HOST_MUTATIONS else [method])
                self.assertEqual([call[3] for call in self.bus.calls], expected)
                self.assertEqual(len(self.opens), 1)
                self.assertEqual(self.opens[0][0], "unix:path=/run/dbus/system_bus_socket")
                for call in self.bus.calls:
                    mutation = call[3] in native.contract.HOST_MUTATIONS
                    self.assertEqual(call[:3], ("ai.happyranch.RemoteAccess1", "/ai/happyranch/RemoteAccess1", "ai.happyranch.RemoteAccess1"))
                    self.assertEqual(call[4:6], (None, "(s)"))
                    self.assertEqual(call[6:], (Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION if mutation else Gio.DBusCallFlags.NONE,
                                              122000 if mutation else 3000))
                if method in native.contract.HOST_MUTATIONS:
                    self.assertEqual(result["effect"], "confirmed")
                    self.assertEqual(result["networkCleanup"], "unknown" if method == "RevokeSelected" else "notApplicable")
                else:
                    self.assertEqual(result["devices"][0], dict(label="Same & label", comparisonPhrase="synthetic comparison"))

    def test_closed_errors_and_acknowledgement(self) -> None:
        for remote, code, effect in (("ai.happyranch.RemoteAccess1.Error.denied", "permissionDenied", "none"),
                ("ai.happyranch.RemoteAccess1.Error.changed", "stateChanged", "none"),
                ("org.freedesktop.DBus.Error.NoReply", "outcomeUnknown", "unknown"),
                ("org.freedesktop.DBus.Error.ServiceUnknown", "unavailable", "none")):
            with self.subTest(remote=remote):
                self.bus.error = remote
                self.bus.calls.clear()
                exit_code, result, _ = self.run_native("approve-device")
                self.assertEqual((exit_code, result["code"], result["effect"]), (1, code, effect))
                self.assertEqual(len(self.bus.calls), 1)  # Never retry a lost mutation.
        self.bus.error = None
        self.bus.ack = '"private-error-canary"'
        self.assertEqual(self.run_native("approve-device")[1]["effect"], "unknown")
        self.bus.ack = None
        self.bus.status_error = "org.freedesktop.DBus.Error.NoReply"
        result = self.run_native("revoke-device")[1]
        self.assertEqual(result["effect"], "none")  # Selection failed before mutation.
        result = self.run_native("reject-device")[1]
        self.assertEqual((result["effect"], result["host"]["observation"]), ("confirmed", "unavailable"))
        self.bus.status_error = None
        self.bus.raw = json.dumps({**self.bus.snapshot, "private": "private-error-canary"})
        self.assertEqual(self.run_native("status")[1]["code"], "unavailable")

    def test_headless_unavailable(self) -> None:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
        # GTK may default to wayland-0 even without explicit display variables.
        with patch.dict(native.os.environ, {"DISPLAY": "", "WAYLAND_DISPLAY": ""}), \
                patch.object(Gtk, "init_check", return_value=(True, [])):
            self.assertFalse(native.desktop_available())
        with patch.object(native, "Host", side_effect=RuntimeError("unexpected host construction")) as host:
            for action in ("approve-device", "reject-device", "revoke-device", *LIFECYCLE):
                with self.subTest(action=action):
                    code, result, _ = self.run_native(action, desktop=False)
                    self.assertEqual((code, result), (1, dict(code="unavailable", effect="none", recovery="nativePermission")))
            host.assert_not_called()
        self.assertEqual(self.opens, [])
        self.assertEqual(self.bus.calls, [])

    def test_lifecycle_grammar_and_exact_dispatch(self) -> None:
        parser = argparse.ArgumentParser()
        command.register(parser.add_subparsers(dest="command"))
        self.assertEqual(command.UNAVAILABLE, ("refresh-home-address",))
        for action, (method, budget) in LIFECYCLE.items():
            with self.subTest(action=action):
                self.assertEqual(native.METHODS.get(action), method)
                self.bus.calls.clear()
                with patch.object(command.sys, "platform", "linux"), \
                        patch.object(command.os, "execv", side_effect=SystemExit(0)) as execute, \
                        self.assertRaises(SystemExit):
                    command.cmd_connection(parser.parse_args(["connection", "host", action]))
                self.assertEqual(execute.call_args.args,
                                 ("/usr/bin/python3", ["/usr/bin/python3", "-I", str(command.native_entry()), action]))
                code, result, _ = self.run_native(action, "yes\n")
                self.assertEqual([call[3] for call in self.bus.calls], [method])
                call = self.bus.calls[0]
                self.assertEqual(call[:6], ("ai.happyranch.RemoteAccess1", "/ai/happyranch/RemoteAccess1",
                                           "ai.happyranch.RemoteAccess1", method, None, "(s)"))
                self.assertEqual(call[6], Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION)
                self.assertGreater(call[7], budget)
                self.assertEqual((code, result["effect"]), (0, "confirmed") if method == "IssueInvite" else (1, "unknown"))
                if method != "IssueInvite":
                    self.assertEqual(result["code"], "outcomeUnknown")  # accepted is not terminal success.
                    self.assertGreater(self.holds[-1][0], budget)
                    self.assertFalse(self.holds[-1][1])  # The caller lives through the held budget.
                host, outcomes = native.Host(), []
                native.contract.request_host(host, method, lambda *args: outcomes.append(args),
                                             lambda: True, lambda _snapshot: None)
                self.assertEqual(outcomes, [(None, "unavailable")])  # No bridge capability expansion.
                self.assertIsNone(host.connection)
                host.close()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stopped:
            parser.parse_args(["connection", "host", "issue-invite"])
        self.assertEqual(stopped.exception.code, 2)
        help_text = parser._subparsers._group_actions[0].choices["connection"]._subparsers._group_actions[0].choices["host"].format_help()
        self.assertNotIn("lifecycle recovery are unavailable", help_text)

    def test_terminal_admission_and_replace_confirmation(self) -> None:
        for action in ("create-invite", "replace-failed"):
            with self.subTest(action=action):
                code, result, _ = self.run_native(action, "yes\n", tty=False)
                self.assertEqual((code, result["effect"]), (1, "none"))
                self.assertEqual((self.opens, self.bus.calls), ([], []))
            with self.subTest(unverified=action), patch.object(Terminal, "isatty", return_value=False):
                code, result, _ = self.run_native(action, "yes\n")
                self.assertEqual((code, result["effect"]), (1, "none"))
                self.assertEqual((self.opens, self.bus.calls), ([], []))
        for choice in ("", "\n", "n\n", "Y\n", "yes please\n", "y", "yes", "y\ntrailing"):
            with self.subTest(choice=choice):
                code, result, _ = self.run_native("replace-failed", choice)
                self.assertEqual((code, result["effect"]), (0, "none"))
                self.assertEqual((self.opens, self.bus.calls), ([], []))
                self.assertEqual(self.tty_output, "Replace failed remote access; existing pairings will be lost\nContinue? [y/N] ")
        for choice in ("y\n", "yes\n"):
            self.bus.calls.clear()
            code, result, _ = self.run_native("replace-failed", choice)
            self.assertEqual((code, result["effect"]), (1, "unknown"))
            self.assertEqual([call[3] for call in self.bus.calls], ["ReplaceFailed"])
        self.bus.calls.clear()
        self.assertEqual(self.run_native("turn-off", tty=False)[1]["effect"], "unknown")
        self.assertEqual([call[3] for call in self.bus.calls], ["Disable"])

    def test_invite_terminal_only_and_closed_projection(self) -> None:
        code, result, stderr = self.run_native("create-invite")
        self.assertEqual((code, self.tty_output), (0, INVITE + "\n"))
        self.assertNotIn(INVITE, stderr)
        self.assertEqual((result["effect"], result["inviteState"]), ("confirmed", "issued"))
        self.assertIsInstance(result["expiresAt"], str)
        self.assertEqual((result["host"], result["client"]), (None, None))
        native.contract.Protocol(Path(__file__).parents[3] / "protocol/native-connection-bridge.v1.json").result("host.createInvite", result)
        self.bus.calls.clear()
        with patch.object(Terminal, "write", side_effect=OSError("private-error-canary")):
            self.assertEqual(self.run_native("create-invite")[1]["effect"], "unknown")
        self.assertEqual([call[3] for call in self.bus.calls], ["IssueInvite"])
        for clock in ("budget", "expiry"):
            self.bus.calls.clear()
            now = int(time.time())
            self.bus.ack = json.dumps(dict(result="complete", invite=INVITE, expires_at=now + 200))
            ticking = (patch.object(time, "monotonic", side_effect=[0, 0, 1000]) if clock == "budget" else
                       patch.object(time, "time", side_effect=[now, now, now + 201]))
            with self.subTest(before_display=clock), ticking:
                code, result, _ = self.run_native("create-invite")
            self.assertEqual((code, result["effect"], self.tty_output), (1, "unknown", ""))
            self.assertEqual([call[3] for call in self.bus.calls], ["IssueInvite"])

    def test_malformed_invite_never_displays(self) -> None:
        frame = dict(result="complete", invite=INVITE, expires_at=int(time.time()) + 200)
        invalid = ["{", json.dumps(frame) + " " * 6144, '"accepted"',
                   json.dumps(frame).replace('"result": "complete"', '"result":"complete","result":"complete"')]
        for key, value in (("result", "ready"), ("invite", "hrinvite2:hrinvite2:bad"),
                           ("invite", "hrinvite2:" + "x" * 5500), ("invite", "hrinvite2:bad\n"),
                           ("expires_at", True), ("expires_at", time.time() + 200),
                           ("expires_at", int(time.time()) - 1), ("expires_at", int(time.time()) + 400),
                           ("extra", "private-error-canary")):
            invalid.append(json.dumps({**frame, key: value}))
        invalid.extend(json.dumps({k: v for k, v in frame.items() if k != missing}) for missing in frame)
        for index, raw in enumerate(invalid):
            with self.subTest(frame=index):
                self.bus.ack = raw
                self.bus.calls.clear()
                code, result, _ = self.run_native("create-invite")
                self.assertEqual((code, result.get("code"), result["effect"], self.tty_output),
                                 (1, "outcomeUnknown", "unknown", ""))
                self.assertEqual([call[3] for call in self.bus.calls], ["IssueInvite"])

    def test_lifecycle_loss_and_late_reply_never_redispatch(self) -> None:
        for action in LIFECYCLE:
            for remote in ("org.freedesktop.DBus.Error.NoReply", "org.freedesktop.DBus.Error.Timeout",
                           "org.freedesktop.DBus.Error.Cancelled", "ai.happyranch.RemoteAccess1.Error.uncertain"):
                with self.subTest(action=action, remote=remote):
                    self.bus.error = remote
                    self.bus.calls.clear()
                    code, result, _ = self.run_native(action, "yes\n")
                    self.assertEqual((code, result.get("code"), result["effect"]), (1, "outcomeUnknown", "unknown"))
                    self.assertEqual(len(self.bus.calls), 1)
        self.bus.error = None
        for action in LIFECYCLE:
            if action == "create-invite":
                continue
            for reply in ('"ok"', '{', json.dumps(dict(outcome="partial_cleanup", residue=["private-error-canary"])),
                          json.dumps(dict(outcome="uncertain"))):
                self.bus.calls.clear()
                self.bus.ack = reply
                with self.subTest(action=action, terminal_shape=reply[:8]):
                    code, result, _ = self.run_native(action, "yes\n")
                    self.assertEqual((code, result.get("code"), result["effect"]), (1, "outcomeUnknown", "unknown"))
                    self.assertEqual(len(self.bus.calls), 1)
        self.bus.ack = None
        for action in LIFECYCLE:
            self.bus.calls.clear()
            with self.subTest(late=action), patch.object(time, "monotonic", side_effect=[0, 1000]):
                code, result, _ = self.run_native(action, "yes\n")
            self.assertEqual((code, result.get("code"), result["effect"]), (1, "outcomeUnknown", "unknown"))
            self.assertEqual(len(self.bus.calls), 1)
            self.assertNotIn(INVITE, self.tty_output)

    def test_revoke_selection_cancellation_and_bounds(self) -> None:
        for choices in ("\n", "cancel\n", "", "invalid\n", "next\n" * 33):
            with self.subTest(choices=choices):
                self.bus.calls.clear()
                code, result, _ = self.run_native("revoke-device", choices)
                self.assertEqual((code, result["effect"], result["hostDenial"]), (0, "none", "notApplicable"))
                native.contract.Protocol(Path(__file__).parents[3] / "protocol/native-connection-bridge.v1.json").result("host.revokeDevice", result)
                self.assertNotIn("RevokeSelected", [call[3] for call in self.bus.calls])
                self.assertLessEqual(len(self.bus.calls), 34)
        with patch.object(native.time, "monotonic", side_effect=[0, 121]):
            self.assertEqual(self.run_native("revoke-device")[1]["effect"], "none")
        with patch.object(Terminal, "isatty", return_value=False):
            self.assertEqual(self.run_native("revoke-device")[1]["recovery"], "nativePermission")
        for closure in ("before_cli_check", "between_checks"):
            with self.subTest(closure=closure):
                self.bus.calls.clear()
                self.opens.clear()
                self.bus.snapshot["selected"] = None
                replacement = Bus()
                replacement.error = "ai.happyranch.RemoteAccess1.Error.changed"

                def select_revoke(_limit: int) -> str:
                    # Gio may publish closure after the CLI's check but before
                    # the shared owner decides whether to open a connection.
                    observations = iter((False, True) if closure == "between_checks" else (True,))
                    closed_check.side_effect = lambda: next(observations, True)
                    return "revoke\n"

                with patch.object(self.bus, "is_closed", side_effect=lambda: self.bus.closed) as closed_check, \
                        patch.object(Terminal, "readline", side_effect=select_revoke), \
                        patch.object(Gio.DBusConnection, "new_for_address_finish",
                                     side_effect=lambda _result: self.bus if len(self.opens) == 1 else replacement):
                    code, result, _ = self.run_native("revoke-device")
                self.assertEqual((len(self.opens), [call[3] for call in self.bus.calls],
                                  [call[3] for call in replacement.calls]),
                                 (1, ["Status", "NextDevice"], []))
                self.assertEqual((code, result["code"], result["effect"]), (1, "unavailable", "none"))


if __name__ == "__main__":
    unittest.main()

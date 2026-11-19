"""Standalone CLI/native boundary tests; synthetic bus, no live helper actions."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout, redirect_stderr
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from gi.repository import Gio, GLib

import connection_cli as native

spec = importlib.util.spec_from_file_location("connection_command", Path(__file__).parents[3] / "cli/commands/connection.py")
command = importlib.util.module_from_spec(spec)
spec.loader.exec_module(command)


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


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
        raw = self.raw or json.dumps(self.snapshot) if method in ("Status", "NextDevice") else self.ack or '"ok"'
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

        def connect(*args: object) -> None:
            self.bus.closed = False
            self.opens.append(args[:3])
            GLib.idle_add(lambda: (args[-2](None, None, None), False)[1])

        for target, value in (("new_for_address", connect), ("new_for_address_finish", lambda _result: self.bus)):
            active = patch.object(Gio.DBusConnection, target, value)
            active.start()
            self.addCleanup(active.stop)

    def run_native(self, action: str, choices: str = "revoke\n", desktop: bool = True) -> tuple:
        out, err = io.StringIO(), Terminal()
        with patch.object(sys, "argv", ["connection_cli.py", action]), \
                patch.object(native, "desktop_available", return_value=desktop), \
                patch.object(sys, "stdin", Terminal(choices)), \
                patch.object(native.select, "select", side_effect=lambda readers, *_args: (readers, [], [])), \
                redirect_stdout(out), redirect_stderr(err):
            code = native.main()
        text = out.getvalue() + err.getvalue()
        for secret in ("a" * 24, "b" * 24, "fingerprint", "private-error-canary"):
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
                self.assertEqual(json.loads(out.getvalue())["roles"], dict(host=host, client="unavailable"))
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
        for args in (("client", "status"), ("host", "revoke-device", "--device-id", "synthetic"), ("host", "unknown-action")):
            with self.subTest(args=args), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stopped:
                parser.parse_args(["connection", *args])
            self.assertEqual(stopped.exception.code, 2)

    def test_fixed_transport_and_projection(self) -> None:
        for action, method in native.METHODS.items():
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
        for action in ("approve-device", "reject-device", "revoke-device"):
            with self.subTest(action=action):
                code, result, _ = self.run_native(action, desktop=False)
                self.assertEqual((code, result), (1, dict(code="unavailable", effect="none", recovery="nativePermission")))
        self.assertEqual(self.opens, [])
        self.assertEqual(self.bus.calls, [])

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
        self.bus.calls.clear()
        self.opens.clear()
        with patch.object(Terminal, "readline", side_effect=lambda _limit: (setattr(self.bus, "closed", True), "revoke\n")[1]):
            code, result, _ = self.run_native("revoke-device")
        self.assertEqual((code, result["effect"]), (1, "none"))
        self.assertEqual(len(self.opens), 1)
        self.assertNotIn("RevokeSelected", [call[3] for call in self.bus.calls])


if __name__ == "__main__":
    unittest.main()

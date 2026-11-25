"""Standalone CLI/native boundary tests; synthetic bus, no live helper actions."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout, redirect_stderr
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
import warnings
from unittest.mock import Mock, patch

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
PREPARATION_TOKENS = ('deb-layout-required', 'issuer-state-uncertain', 'account-shape',
    'account-receipt-uncertain', 'foreign-unit', 'foreign-custody', 'publication-uncertain',
    'address-changed', 'account-create-uncertain', 'cleanup-uncertain', 'materializer-uncertain',
    'custody-uncertain', 'inventory', 'no_address', 'multiple_addresses', 'dial_ip', 'dial-ip',
    'occupied-port', 'port-check-uncertain')


def terminal_frame(**changes: object) -> str:
    return json.dumps(dict(method="Enable", correlation=dict(serial=17, owner=":1.100"),
                           outcome="failed", category="inventory") | changes)


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
        self.filters, self.subscriptions, self.handlers = {}, {}, {}
        self.next_handle = 0
        self.terminal_frames = []
        self.early = False
        self.hold, self.held = False, []
        self.next_serial = 17
        self.unique_name, self.reply_owner = ":1.42", ":1.100"

    def queue(self, callback: object) -> None:
        if self.hold:
            self.held.append(callback)
        else:
            GLib.idle_add(lambda: (callback(), False)[1])

    def get_unique_name(self) -> str:
        return self.unique_name

    def signal_subscribe(self, *args: object) -> int:
        self.next_handle += 1
        self.subscriptions[self.next_handle] = args
        return self.next_handle

    def signal_unsubscribe(self, handle: int) -> None:
        self.subscriptions.pop(handle, None)

    def add_filter(self, callback: object, data: object) -> int:
        self.next_handle += 1
        self.filters[self.next_handle] = callback
        return self.next_handle

    def remove_filter(self, handle: int) -> None:
        self.filters.pop(handle, None)

    def connect(self, name: str, callback: object) -> int:
        self.next_handle += 1
        self.handlers[self.next_handle] = callback
        return self.next_handle

    def disconnect(self, handle: int) -> None:
        self.handlers.pop(handle, None)

    def incoming(self, message: object) -> None:
        for callback in tuple(self.filters.values()):
            callback(self, message, True, None)

    def signal(self, raw: str, *, sender: str = ":1.100", destination: str | None = ":1.42") -> object:
        message = Gio.DBusMessage.new_signal("/ai/happyranch/RemoteAccess1", "ai.happyranch.RemoteAccess1",
                                             "LifecycleCompleted")
        message.set_sender(sender)
        message.set_destination(destination)
        message.set_body(GLib.Variant("(s)", (raw,)))
        self.incoming(message)
        return message

    def send_message_with_reply(self, message: object, flags: object, timeout: int, cancel: object,
                                callback: object, data: object) -> int:
        serial = self.next_serial
        self.next_serial += 1
        message.set_serial(serial)
        assert message.get_flags() == Gio.DBusMessageFlags.ALLOW_INTERACTIVE_AUTHORIZATION
        def reply(_bus: object, result: tuple, _data: object) -> None:
            raw, error = result
            incoming = (message.new_method_error_literal(error, "synthetic private canary") if error
                        else message.new_method_reply())
            incoming.set_sender(self.reply_owner)
            incoming.set_destination(self.get_unique_name())
            if not error:
                incoming.set_body(GLib.Variant("(s)", (raw,)))
            self.incoming(incoming)
            if self.early:
                for frame in self.terminal_frames:
                    self.signal(frame)
            self.queue(lambda: callback(self, incoming, data))
            if not self.early:
                for frame in self.terminal_frames:
                    self.queue(lambda value=frame: self.signal(value))
        self.call(message.get_destination(), message.get_path(), message.get_interface(), message.get_member(),
                  None, GLib.VariantType.new("(s)"), Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION,
                  timeout, cancel, reply, data)
        return serial

    def send_message_with_reply_finish(self, result: object) -> object:
        return result

    def is_closed(self) -> bool:
        return self.closed

    def set_exit_on_close(self, _value: bool) -> None:
        pass

    def close(self, *_args: object) -> None:
        self.closed = True

    def call(self, *args: object) -> None:
        name, path, interface, method, inputs, signature, flags, timeout, cancel, callback, data = args
        self.calls.append((name, path, interface, method, inputs, signature.dup_string(), flags, timeout))
        if method == 'StatusV2' and hasattr(self, 'setup_state'):
            value = {'version': 2, **{key: dict(state='unknown', age_seconds=None)
                for key in ('setup', 'runtime', 'invite', 'address', 'policy', 'cleanup')}}
            value['setup'] = dict(state=self.setup_state, age_seconds=0)
            value['invite']['deadline_utc_seconds'] = None
            self.queue(lambda: callback(self, (json.dumps(value), None), data))
            return
        if method == "NextDevice":
            current = self.snapshot["selected"]
            self.snapshot["selected"] = self.snapshot["devices"][1 if current == self.snapshot["devices"][0] else 0]
        reply = (json.dumps(dict(result="complete", invite=INVITE, expires_at=int(time.time()) + 200))
                 if method == "IssueInvite" else '"accepted"' if method in
                 {entry[0] for entry in LIFECYCLE.values()} else '"ok"')
        raw = self.raw or json.dumps(self.snapshot) if method in ("Status", "NextDevice") else self.ack or reply
        error = self.status_error if method == "Status" else self.error
        self.queue(lambda: callback(self, (raw, error), data))

    def call_finish(self, result: tuple) -> GLib.Variant:
        raw, error = result
        if error:
            raise Gio.DBusError.new_for_dbus_error(error, "private-error-canary")
        return GLib.Variant("(s)", (raw,))


class ConnectionCLITests(unittest.TestCase):
    def test_setup_detail_requires_fresh_closed_setup(self) -> None:
        protocol = native.Host().protocol
        snapshot = protocol.host(json.dumps({**self.bus.snapshot, 'phase': 'failed', 'ready': False}))
        base = {'version': 2, **{key: dict(state='unknown', age_seconds=None)
            for key in ('setup', 'runtime', 'invite', 'address', 'policy', 'cleanup')}}
        base['invite']['deadline_utc_seconds'] = None
        base['setup'] = dict(state='runtime_unavailable', age_seconds=0)
        result = protocol.host_details(snapshot, json.dumps(base))
        self.assertEqual((result['setup'], result['runtime'], result['devices']), ('runtime_unavailable', 'offline', None))
        for part in (dict(state='runtime_unavailable', age_seconds=31),
                     dict(state='runtime_unavailable', age_seconds=True),
                     dict(state='runtime_unavailable', age_seconds=None),
                     dict(state='ready', age_seconds=0),
                     dict(state='runtime_unavailable', age_seconds=0, private='synthetic')):
            with self.subTest(part=part), self.assertRaises(ValueError):
                protocol.host_details(snapshot, json.dumps({**base, 'setup': part}))
        for raw in (json.dumps({**base, 'private': 'synthetic'}),
                    json.dumps(base).replace('"version": 2', '"version": 2,"version": 2'),
                    ' ' * 16385):
            with self.assertRaises(ValueError):
                protocol.host_details(snapshot, raw)

    def test_named_setup_states_remain_visible_in_cli(self) -> None:
        for state in ('prepared_no_receipt', 'failed_uncertain', 'partial_cleanup',
                      'runtime_unavailable', 'setup_unverified', 'renewal_failed'):
            with self.subTest(state=state):
                self.bus.calls.clear()
                self.bus.setup_state = state
                self.bus.snapshot.update(phase='pending' if state == 'prepared_no_receipt' else
                                         'paused' if state == 'renewal_failed' else 'failed', ready=False)
                code, result, _ = self.run_native('status')
                self.assertEqual(code, 0)
                self.assertEqual((result['setup'], result['runtime']), (state, 'offline'))
                self.assertIsNone(result['devices'])
                self.assertEqual([call[3] for call in self.bus.calls], ['Status', 'StatusV2'])

    def test_turn_off_settled_receipt_returns_without_timeout(self) -> None:
        for outcome in ('completed', 'partial_cleanup'):
            for early in (False, True):
                with self.subTest(outcome=outcome, early=early):
                    host, bus, seen = native.Host(), Bus(), []
                    host.connection, bus.hold, bus.early = bus, True, early
                    frame = dict(method='Disable', correlation=dict(serial=17, owner=':1.100'),
                                 outcome='completed' if outcome == 'completed' else 'failed')
                    if outcome == 'partial_cleanup':
                        frame['category'] = outcome
                    # An Enable receipt with the same serial must never settle Disable.
                    bus.terminal_frames = [terminal_frame(), json.dumps(frame), json.dumps(frame)]
                    try:
                        native.contract.request_host(host, 'Disable', lambda *a: seen.append(a),
                            lambda: True, lambda _: None, cli_lifecycle=True)
                        while bus.held:
                            bus.held.pop(0)()
                        context = GLib.MainContext.default()
                        while context.pending():
                            context.iteration(False)
                        expected = (dict(code='turnOffCompleted', effect='unknown', recovery='refresh'),) if outcome == 'completed' else (
                            None, dict(code='turnOffFailed', category='partial_cleanup',
                                       effect='unknown', recovery='nativeRecovery'), 'unknown')
                        self.assertEqual(seen, [expected])
                        self.assertEqual([call[3] for call in bus.calls], ['Disable'])
                        self.assertEqual((bus.filters, bus.subscriptions, bus.handlers), ({}, {}, {}))
                    finally:
                        host.close()

    def test_enable_preparation_cli_terminal(self) -> None:
        for token in PREPARATION_TOKENS:
            for early in (False, True):
                with self.subTest(token=token, early=early):
                    self.bus.early = early
                    self.bus.calls.clear()
                    self.bus.terminal_frames = [terminal_frame(category=token)] * 2
                    code, result, errors = self.run_native("enable")
                    self.assertEqual((code, result, errors), (1, dict(code="enablePreparationFailed",
                        category=token, effect="unknown", recovery="nativeRecovery"), ""))
                    self.assertEqual(len(self.stdout.splitlines()), 1)
                    self.assertEqual([call[3] for call in self.bus.calls], ["Enable"])
                    self.assertEqual((self.bus.filters, self.bus.subscriptions, self.bus.handlers), ({}, {}, {}))

    def test_enable_terminal_rejections(self) -> None:
        good = terminal_frame()
        invalid = {
            "malformed": '{"category":"inventory"',
            "lone_surrogate": good + "\ud800",
            "non_object": '["inventory"]',
            "trailing_value": good + '{"category":"inventory"}',
            "oversized": good + " " * (513 - len(good)),
            "multibyte": good + "界" * 200,
            "duplicate_key": good.replace('"category":', '"category":"inventory","category":'),
            "duplicate_category_conflict": good.replace('"category":', '"category":"occupied-port","category":'),
            "duplicate_method_equal": good.replace('"method":', '"method":"Enable","method":'),
            "duplicate_method_conflict": good.replace('"method":', '"method":"Disable","method":'),
            "duplicate_outcome_equal": good.replace('"outcome":', '"outcome":"failed","outcome":'),
            "duplicate_outcome_conflict": good.replace('"outcome":', '"outcome":"completed","outcome":'),
            "duplicate_nested": good.replace('"serial":', '"serial":17,"serial":'),
            "duplicate_serial_conflict": good.replace('"serial":', '"serial":18,"serial":'),
            "duplicate_owner_equal": good.replace('"owner":', '"owner":":1.100","owner":'),
            "duplicate_owner_conflict": good.replace('"owner":', '"owner":":1.101","owner":'),
            "wrong_type": terminal_frame(correlation=dict(serial=True, owner=":1.100")),
            "float_serial": terminal_frame(correlation=dict(serial=17.0, owner=":1.100")),
            "string_serial": terminal_frame(correlation=dict(serial="17", owner=":1.100")),
            "zero_serial": terminal_frame(correlation=dict(serial=0, owner=":1.100")),
            "overflow_serial": terminal_frame(correlation=dict(serial=4294967296, owner=":1.100")),
            "correlation_array": terminal_frame(correlation=[17, ":1.100"]),
            "extra_field": terminal_frame(message="SYNTHETIC_PRIVATE_CANARY"),
            "extra_correlation": terminal_frame(correlation=dict(serial=17, owner=":1.100", uid=1000)),
            "wrong_method": terminal_frame(method="Disable"),
            "wrong_method_case": terminal_frame(method="enable"),
            "wrong_method_invite": terminal_frame(method="IssueInvite"),
            "wrong_request": terminal_frame(correlation=dict(serial=18, owner=":1.100")),
            "wrong_owner": terminal_frame(correlation=dict(serial=17, owner=":1.101")),
            "completed_category": terminal_frame(outcome="completed"),
        }
        for token in ("unknown", "policy_path", "readiness-timeout", "partial-cleanup-uncertain",
                      "issuer-cleanup-uncertain", "issuer-partial-cleanup-uncertain", "owner",
                      " inventory", "inventory ", "xinventory", "Inventory", "", None, [], {}):
            invalid["unsupported_category_" + repr(token)] = terminal_frame(category=token)
        for name, bad in invalid.items():
            for schedule in ([bad], [bad, good], [good, bad, good]):
                with self.subTest(case=name, schedule=len(schedule)):
                    self.bus.calls.clear()
                    self.bus.terminal_frames = schedule
                    code, result, _ = self.run_native("enable")
                    self.assertEqual(code, 1)
                    self.assertEqual(result["code"], "enablePreparationFailed" if good in schedule else "outcomeUnknown")
                    if good in schedule:
                        self.assertEqual(result["category"], "inventory")
                    self.assertEqual(len(self.stdout.splitlines()), 1)
                    self.assertEqual([call[3] for call in self.bus.calls], ["Enable"])
                    self.assertEqual((self.bus.filters, self.bus.subscriptions), ({}, {}))
        # The 512-byte ASCII whitespace control isolates the byte-bound guard.
        self.bus.terminal_frames = [good + " " * (512 - len(good))]
        self.assertEqual(self.run_native("enable")[1]["category"], "inventory")

    def test_enable_terminal_envelope_and_authenticated_admission(self) -> None:
        original = self.bus.signal
        for header in (dict(sender=":1.101"), dict(destination=None), dict(destination=":1.43")):
            for then_valid in (False, True):
                with self.subTest(header=header, then_valid=then_valid):
                    self.bus.calls.clear()
                    self.bus.terminal_frames = [terminal_frame()]
                    def transfer(raw: str) -> None:
                        original(raw, **header)
                        if then_valid:
                            original(raw)
                    with patch.object(self.bus, "signal", side_effect=transfer):
                        _, result, _ = self.run_native("enable")
                    self.assertEqual(result["code"], "enablePreparationFailed" if then_valid else "outcomeUnknown")
                    self.assertEqual([call[3] for call in self.bus.calls], ["Enable"])
        for admission in ('"ok"', '{}', '"accepted" trailing'):
            self.bus.early = True
            self.bus.ack = admission
            self.bus.terminal_frames = [terminal_frame()]
            self.assertEqual(self.run_native("enable")[1]["code"], "outcomeUnknown")
        self.bus.ack = None
        self.bus.error = "ai.happyranch.RemoteAccess1.Error.denied"
        self.assertEqual(self.run_native("enable")[1], dict(code="permissionDenied", effect="none", recovery="nativePermission"))

    def test_enable_completed_is_not_ready(self) -> None:
        self.bus.terminal_frames = [json.dumps(dict(method="Enable", correlation=dict(serial=17, owner=":1.100"),
                                                    outcome="completed"))]
        code, result, _ = self.run_native("enable")
        self.assertEqual((code, result), (0, dict(code="enableCompleted", effect="unknown", recovery="refresh")))
        self.assertEqual(len(self.stdout.splitlines()), 1)
        self.assertEqual([call[3] for call in self.bus.calls], ["Enable"])

    def test_enable_terminal_deadline_loss_and_owner_retirement(self) -> None:
        for case in ("timely", "boundary", "late", "publication_late", "wrong_signature", "wrong_fds", "wrong_path",
                     "wrong_interface", "wrong_member", "helper_restart", "transport_loss",
                     "document", "context"):
            with self.subTest(case=case):
                host, bus, seen, clock, live = native.Host(), Bus(), [], [100.0], [True]
                host.connection, bus.hold = bus, True
                with patch.object(native.contract.time, "monotonic", side_effect=lambda: clock[0]):
                    native.contract.request_host(host, "Enable", lambda *a: seen.append(a),
                        lambda: live[0], lambda _snapshot: None, cli_lifecycle=True)
                    while bus.held:
                        bus.held.pop(0)()
                    context = GLib.MainContext.default()
                    while context.pending():
                        context.iteration(False)
                    observer = host.enable_observer
                    self.assertEqual(seen, [])
                    if case == "publication_late":
                        validate = host.protocol.validate
                        def validation(value: object, schema: dict) -> None:
                            validate(value, schema)
                            clock[0] = 580
                        host.protocol.validate = validation
                    if case in ("document", "context"):
                        live[0] = False
                    if case == "transport_loss":
                        observer._lost()
                    elif case == "helper_restart":
                        message = Gio.DBusMessage.new_signal("/org/freedesktop/DBus",
                            "org.freedesktop.DBus", "NameOwnerChanged")
                        message.set_sender("org.freedesktop.DBus")
                        message.set_body(GLib.Variant("(sss)", ("ai.happyranch.RemoteAccess1", ":1.100", ":1.101")))
                        bus.incoming(message)
                    else:
                        clock[0] = 579.999 if case == "timely" else 580 if case == "boundary" else 581 if case == "late" else 200
                        message = Gio.DBusMessage.new_signal(
                            "/wrong" if case == "wrong_path" else "/ai/happyranch/RemoteAccess1",
                            "ai.wrong" if case == "wrong_interface" else "ai.happyranch.RemoteAccess1",
                            "Wrong" if case == "wrong_member" else "LifecycleCompleted")
                        message.set_sender(":1.100")
                        message.set_destination(":1.42")
                        message.set_body(GLib.Variant("(ss)", (terminal_frame(), "extra")) if case == "wrong_signature"
                                         else GLib.Variant("(s)", (terminal_frame(),)))
                        if case == "wrong_fds":
                            descriptors = Gio.UnixFDList.new()
                            descriptor = os.open(os.devnull, os.O_RDONLY)
                            try:
                                descriptors.append(descriptor)
                            finally:
                                os.close(descriptor)
                            message.set_unix_fd_list(descriptors)
                        bus.incoming(message)
                    while context.pending():
                        context.iteration(False)
                    if case == "timely":
                        self.assertEqual(seen[0][1]["category"], "inventory")
                    elif case in ("document", "context"):
                        self.assertEqual(seen, [])
                    elif case in ("transport_loss", "helper_restart"):
                        self.assertEqual(seen, [(None, "outcomeUnknown", "unknown")])
                    else:
                        self.assertEqual(seen, [])
                        observer._expired()
                        self.assertEqual(seen, [(None, "outcomeUnknown", "unknown")])
                    bus.signal(terminal_frame())
                    while context.pending():
                        context.iteration(False)
                    self.assertLessEqual(len(seen), 1)
                    self.assertEqual([call[3] for call in bus.calls], ["Enable"])
                    host.close()

    def test_enable_pending_original_owner_survives_name_release(self) -> None:
        context = GLib.MainContext.default()
        for pending in (False, True):
            with self.subTest(pending_original_result=pending):
                host, bus, admissions, events = native.Host(), Bus(), [], []
                host.connection, bus.hold, bus.early = bus, True, True
                bus.terminal_frames = [terminal_frame()] if pending else []
                try:
                    native.contract.request_host(host, "Enable", lambda *a: admissions.append(a),
                        lambda: True, lambda _snapshot: None, terminal=events.append,
                        terminal_current=lambda: True)
                    # Receive the wire reply and optional result; keep the
                    # send_message_with_reply completion callback queued.
                    self.assertEqual(len(bus.held), 1)
                    bus.held.pop(0)()
                    while context.pending():
                        context.iteration(False)
                    observer = host.enable_observer
                    self.assertEqual((admissions, events), ([], []))
                    self.assertEqual(observer.pending is not None, pending)
                    for old, new in ((":1.100", ""), ("", ":1.101")):
                        changed = Gio.DBusMessage.new_signal("/org/freedesktop/DBus",
                            "org.freedesktop.DBus", "NameOwnerChanged")
                        changed.set_sender("org.freedesktop.DBus")
                        changed.set_body(GLib.Variant("(sss)", ("ai.happyranch.RemoteAccess1", old, new)))
                        bus.incoming(changed)
                    while context.pending():
                        context.iteration(False)
                    bus.signal(terminal_frame(correlation=dict(serial=17, owner=":1.101")), sender=":1.101")
                    while bus.held:
                        bus.held.pop(0)()
                    while context.pending():
                        context.iteration(False)
                    self.assertEqual(len(admissions), 1)
                    self.assertEqual(len(events), 1 if pending else 0)
                    if pending:
                        event = json.loads(events[0])
                        self.assertEqual(event["operationId"], admissions[0][0]["operationId"])
                        self.assertEqual(event["error"], dict(code="enablePreparationFailed",
                            category="inventory", effect="unknown", recovery="nativeRecovery"))
                    else:
                        self.assertEqual(admissions, [(None, "outcomeUnknown", "unknown")])
                    bus.signal(terminal_frame())
                    observer._expired()
                    observer._lost()
                    while context.pending():
                        context.iteration(False)
                    self.assertEqual((len(admissions), len(events)), (1, 1 if pending else 0))
                    self.assertEqual([call[3] for call in bus.calls], ["Enable"])
                    self.assertEqual((bus.filters, bus.subscriptions, bus.handlers), ({}, {}, {}))
                finally:
                    host.close()

    def test_enable_new_attempt_rejects_old_result(self) -> None:
        context = GLib.MainContext.default()
        for next_owner in (":1.100", ":1.101"):
            with self.subTest(next_owner=next_owner):
                host, bus, admissions, events = native.Host(), Bus(), [], []
                host.connection, bus.hold = bus, True
                def drain() -> None:
                    while bus.held:
                        bus.held.pop(0)()
                    while context.pending():
                        context.iteration(False)
                def dispatch() -> None:
                    native.contract.request_host(host, "Enable", lambda *a: admissions.append(a),
                        lambda: True, lambda _snapshot: None, terminal=events.append,
                        terminal_current=lambda: True)
                    drain()
                try:
                    dispatch()
                    bus.signal(terminal_frame())
                    drain()
                    self.assertEqual(len(events), 1)
                    first = host.enable_observer
                    bus.reply_owner = next_owner
                    dispatch()
                    second_id = admissions[-1][0]["operationId"]
                    self.assertNotEqual(second_id, json.loads(events[0])["operationId"])
                    bus.signal(terminal_frame())  # Prior serial, possibly prior owner.
                    drain()
                    self.assertEqual(len(events), 1)
                    bus.signal(terminal_frame(correlation=dict(serial=18, owner=next_owner)), sender=next_owner)
                    drain()
                    self.assertEqual(len(events), 2)
                    self.assertEqual(json.loads(events[-1])["operationId"], second_id)
                    self.assertEqual(json.loads(events[-1])["error"]["category"], "inventory")
                    bus.signal(terminal_frame())
                    first._lost()
                    first._expired()
                    drain()
                    self.assertEqual((len(admissions), len(events)), (2, 2))
                    self.assertEqual([call[3] for call in bus.calls], ["Enable", "Enable"])
                    self.assertEqual((bus.filters, bus.subscriptions, bus.handlers), ({}, {}, {}))
                    # A new same-UID caller has its own destination. Old bytes
                    # cannot satisfy its subscription even with serial reuse.
                    other, other_bus, other_events = native.Host(), Bus(), []
                    other.connection, other_bus.hold = other_bus, True
                    other_bus.unique_name = ":1.43"
                    try:
                        native.contract.request_host(other, "Enable", lambda *a: None,
                            lambda: True, lambda _snapshot: None, terminal=other_events.append,
                            terminal_current=lambda: True)
                        while other_bus.held:
                            other_bus.held.pop(0)()
                        while context.pending():
                            context.iteration(False)
                        self.assertEqual(other_events, [])
                        other_bus.signal(terminal_frame(), destination=":1.42")
                        while context.pending():
                            context.iteration(False)
                        self.assertEqual(other_events, [])
                        other_bus.signal(terminal_frame(), destination=":1.43")
                        while context.pending():
                            context.iteration(False)
                        self.assertEqual(len(other_events), 1)
                        self.assertEqual([call[3] for call in other_bus.calls], ["Enable"])
                    finally:
                        other.close()
                finally:
                    host.close()

    def test_enable_consumes_production_trace(self) -> None:
        target = os.environ.get("ENABLE_PREPARATION_TRACE")
        if target:
            trace = json.loads(Path(target).read_text())
            signal = trace["signal"]
            self.assertEqual((signal["destination"], signal["sender"], signal["signature"]),
                             (":1.42", ":1.100", "s"))
            self.assertEqual(trace["child"], dict(exit=1, stdout='{"preparationFailure":"inventory"}\n', stderr=''))
            raw = signal["raw"]
        else:
            raw = terminal_frame()
        self.bus.terminal_frames = [raw]
        code, result, _ = self.run_native("enable")
        self.assertEqual((code, result), (1, dict(code="enablePreparationFailed",
            category="inventory", effect="unknown", recovery="nativeRecovery")))
        self.assertEqual([call[3] for call in self.bus.calls], ["Enable"])

    def setUp(self) -> None:
        self.bus = Bus()
        self.opens = []
        self.holds = []

        def connect(*args: object) -> None:
            self.bus.closed = False
            self.bus.next_serial = 17
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
            return GLib.idle_add(callback, priority=GLib.PRIORITY_LOW)  # Drain explicit wire callbacks before expiry.

        with patch.object(sys, "argv", ["connection_cli.py", action]), \
                patch.object(native, "desktop_available", return_value=desktop), \
                patch.object(native.os, "open", side_effect=None if tty else OSError(), return_value=123), \
                patch.object(native.os, "fdopen", return_value=terminal), \
                patch.object(native.os, "read", return_value=choices.encode()), \
                patch.object(GLib, "timeout_add", side_effect=hold), \
                patch.object(sys, "stdin", Terminal(choices)), \
                patch.object(native.select, "select", side_effect=lambda readers, *_args: (readers, [], [])), \
                warnings.catch_warnings(), \
                redirect_stdout(out), redirect_stderr(err):
            warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"gi\.events")
            code = native.main()
        self.stdout = out.getvalue()
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

    def test_closed_host_state_diagnostic(self) -> None:
        for category in ("host_state_unrecognized", "owner_unknown", "owner_ambiguous",
                         "owner_unsupported", "owner_mismatch", "release_home_invalid",
                         *("host_state_unrecognized_" + part for part in (
                             "residue", "owner", "provenance", "custody", "inventory", "quiescence", "timeout", "unknown"))):
            with self.subTest(category=category):
                self.bus.status_error = "ai.happyranch.RemoteAccess1.Error." + category
                # Keep the exact CLI transcript assertion independent of the
                # system Gio event adapter's Python 3.14 deprecation warnings.
                with warnings.catch_warnings(record=True) as observed:
                    code, result, message = self.run_native("status")
                for warning in observed:
                    self.assertIs(warning.category, DeprecationWarning)
                    self.assertTrue(warning.filename.endswith('/gi/events.py'))
                self.assertEqual(code, 1)
                part = (category.removeprefix("host_state_unrecognized_") if category.startswith("host_state_unrecognized_")
                        else "unknown" if category == "host_state_unrecognized" else "owner")
                self.assertEqual(result, dict(code="hostStateUnrecognized", category=part, effect="none", recovery="refresh"))
                self.assertEqual(message.strip(), "Remote access files or the release runtime owner could not be verified. "
                                 "Check the local installation. Category: " + part + ".")

    def test_headless_unavailable(self) -> None:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
        original_init_check = Gtk.init_check
        initialize = Mock(return_value=(True, []))
        # GTK may default to wayland-0 even without explicit display variables.
        # Restore the proxy dict directly: patch.object teardown deletes the
        # underlying GI callable too, leaving stale PyGObject ResultTuple types.
        with patch.dict(native.os.environ, {"DISPLAY": "", "WAYLAND_DISPLAY": ""}), \
                patch.dict(Gtk.__dict__, {"init_check": initialize}):
            self.assertFalse(native.desktop_available())
            initialize.assert_not_called()
        self.assertIs(Gtk.init_check, original_init_check)
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
                if method in ("Enable", 'Disable'):
                    self.assertEqual(call[7], 122000)  # Admission only; original operation budget stays held separately.
                else:
                    self.assertGreater(call[7], budget)
                self.assertEqual((code, result["effect"]), (0, "confirmed") if method == "IssueInvite" else (1, "unknown"))
                if method != "IssueInvite":
                    self.assertEqual(result["code"], "outcomeUnknown")  # accepted is not terminal success.
                    self.assertGreater(self.holds[-1][0], budget)
                    self.assertFalse(self.holds[-1][1])  # The caller lives through the held budget.
                if method in ("ReplaceFailed", "CancelInvite", "RecoverInvite", "RetryPolicyRenewal"):
                    host, outcomes = native.Host(), []
                    native.contract.request_host(host, method, lambda *args: outcomes.append(args),
                                                 lambda: True, lambda _snapshot: None)
                    self.assertEqual(outcomes, [(None, "unavailable")])  # These remain CLI-only.
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

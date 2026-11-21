"""Standalone GTK/WebKit smoke using a synthetic HTTP runtime, never a daemon."""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
import hashlib
import io
import importlib.util
import json
import logging
import os
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gdk, Gio, GLib, Gtk

import client_ui as client_owner
import host_bridge as connection_owner
from client_ui import ClientUI
from test_client_model import Session

INVITE = "hrinvite2:synthetic_bridge_invite_canary"


class HostBus:
    """Test-side system-bus stub; production keeps its fixed system address."""
    def __init__(self):
        device = {"label": "Synthetic &amp; device", "fingerprint": "a" * 24,
                  "comparison_phrase": "synthetic comparison"}
        self.snapshot = dict(phase="ready", ready=True, devices=[device], pending=device, selected=device)
        self.calls, self.held = [], []
        self.error = self.status_error = None
        self.ack = None
        self.hold = False
        self.closed = False

    def is_closed(self):
        return self.closed

    def close(self, *_args):
        self.closed = True

    def call(self, name, path, interface, method, args, result_type, flags, timeout, cancel, callback, data):
        self.calls.append((name, path, interface, method, args, result_type.dup_string(), flags, timeout))
        error = self.status_error if method == "Status" else self.error
        result = Gio.DBusError.new_for_dbus_error(error, "synthetic-secret-canary") if error else None
        raw = json.dumps(self.snapshot) if method in ("Status", "NextDevice") else (
            self.ack if self.ack is not None else
            json.dumps(dict(result="complete", invite=INVITE, expires_at=int(time.time()) + 200))
            if method == "IssueInvite" else '"accepted"' if method in ("Enable", "Disable") else '"ok"')
        if method in ("ApprovePending", "RejectPending", "RevokeSelected") and not error:
            # Only a later Status may communicate this state to the web.
            self.snapshot = {**self.snapshot, "phase": "paused", "ready": False}
        deliver = lambda: (callback(self, (raw, result), data), False)[1]
        if self.hold:
            self.held.append(deliver)
        else:
            GLib.idle_add(deliver)

    def call_finish(self, result):
        raw, error = result
        if error:
            raise error
        return GLib.Variant("(s)", (raw,))


class NativeHostTests(unittest.TestCase):
    """Real admission/transport/projection without requiring a display server.

    Only the native invite display sink and the external D-Bus peer are doubles;
    GTKTests below exercises the actual widgets and complete web facade.
    """

    def setUp(self) -> None:
        self.bus, self.outcomes, self.displayed, self.events = HostBus(), [], [], []
        self.window = connection_owner.HostBridge.__new__(connection_owner.HostBridge)
        window = self.window
        window.Gio, window.GLib = Gio, GLib
        window.connection, window.cancellable = self.bus, Gio.Cancellable()
        window.protocol = connection_owner._contract.Protocol(Path(__file__).parents[3] / "protocol/native-connection-bridge.v1.json")
        window.busy, window.closed, window.active, window.generation = False, False, True, 1
        window.client_document = object()
        window.client = SimpleNamespace(prompt=None, model=SimpleNamespace(
            confirming_target=False, host_prompt_document=None))
        window.host_status_waiters = None
        window.host_subscribed, window.host_event_pending, window.host_sequence = True, False, 0
        window.view = SimpleNamespace(get_uri=lambda: "happyranch-app://app/",
            call_async_javascript_function=lambda *args: self.events.append(args[2].unpack()["raw"]))
        window.window = SimpleNamespace(get_display=lambda: SimpleNamespace(is_closed=lambda: False))
        window.show_invite = lambda *args: self.displayed.append(args)
        window.operation_owner = window.cancel_request = None
        window.host_timer, window.host_snapshot = None, None
        generation = window.generation
        current = lambda: window.active and not window.closed and window.generation == generation
        subscriber = dict(current=current, visible=lambda: True, emit=self.events.append, window=window.window)
        window.subscribers = {window.client_document: subscriber}
        request = window.host_request
        def host_request(method, finish):
            generation = window.generation
            def current():
                return window.active and not window.closed and generation == window.generation
            request(method, lambda *args: finish(*args) if current() else None,
                    owner=window.client_document, current=current, window=window.window)
        window.host_request = host_request


    def drain(self) -> None:
        context = GLib.MainContext.default()
        while context.pending():
            context.iteration(False)

    def request(self, method: str) -> tuple:
        self.outcomes.clear()
        self.window.host_request(method, lambda *args: self.outcomes.append(args))
        self.drain()
        self.assertEqual(len(self.outcomes), 1)
        return self.outcomes[0]

    def test_lifecycle_mapping_admission_and_observation(self) -> None:
        for command, method, timeout in (("host.enable", "Enable", 482000),
                                         ("host.turnOff", "Disable", 302000),
                                         ("host.createInvite", "IssueInvite", 122000)):
            with self.subTest(command=command):
                self.bus.calls.clear()
                self.assertEqual(connection_owner._contract.HOST_METHODS[command], method)
                result, = self.request(method)
                self.window.protocol.result(command, result)
                call, = self.bus.calls
                self.assertEqual(call, ("ai.happyranch.RemoteAccess1", "/ai/happyranch/RemoteAccess1",
                    "ai.happyranch.RemoteAccess1", method, None, "(s)",
                    Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION, timeout))
                self.assertFalse(self.bus.closed)
                if method != "IssueInvite":
                    self.assertEqual(result, dict(effect="unknown", hostDenial="unknown", hostCleanup="unknown",
                        localCleanup="notApplicable", networkCleanup="unknown", inviteState="unknown",
                        expiresAt=None, host=None, client=None))
                    self.assertEqual(self.events, [])  # accepted never fabricates an observation.
                else:
                    self.assertEqual((result["inviteState"], result["host"], result["client"]), ("issued", None, None))
                    self.assertIsInstance(result["expiresAt"], str)
        self.assertEqual(self.request("Status")[0]["setup"], "ready")
        self.assertEqual(json.loads(self.events[-1])["snapshot"]["setup"], "ready")
        for method in ("ReplaceFailed", "CancelInvite", "RecoverInvite", "RetryPolicyRenewal", "RefreshHomeAddress"):
            calls = list(self.bus.calls)
            self.assertEqual(self.request(method), (None, "unavailable"))
            self.assertEqual(self.bus.calls, calls)

    def test_lifecycle_denial_loss_late_and_document_fence(self) -> None:
        for method in ("Enable", "Disable", "IssueInvite"):
            for remote, code, effect in (
                    ("ai.happyranch.RemoteAccess1.Error.denied", "permissionDenied", "none"),
                    ("org.freedesktop.DBus.Error.NoReply", "outcomeUnknown", "unknown"),
                    ("org.freedesktop.DBus.Error.Timeout", "outcomeUnknown", "unknown"),
                    ("org.freedesktop.DBus.Error.Cancelled", "outcomeUnknown", "unknown")):
                with self.subTest(method=method, remote=remote):
                    self.bus.error, self.bus.calls = remote, []
                    result = self.request(method)
                    self.assertEqual(result, (None, code) if effect == "none" else (None, code, effect))
                    self.assertEqual([call[3] for call in self.bus.calls], [method])
                    self.assertEqual(self.displayed, [])
            self.bus.error, self.bus.calls, self.bus.hold = None, [], True
            with patch.object(connection_owner._contract.time, "monotonic", return_value=0):
                self.window.host_request(method, lambda *args: self.outcomes.append(args))
            self.outcomes.clear()
            with patch.object(connection_owner._contract.time, "monotonic", return_value=1000):
                self.bus.held.pop()()
            self.assertEqual(self.outcomes, [(None, "outcomeUnknown", "unknown")])
            self.assertEqual(len(self.bus.calls), 1)
            self.outcomes.clear()
            self.bus.calls.clear()
            self.window.host_request(method, lambda *args: self.outcomes.append(args))
            self.window.generation += 1
            self.bus.held.pop()()
            self.assertEqual((self.outcomes, self.displayed), ([], []))
            self.assertEqual(len(self.bus.calls), 1)
            self.window.busy, self.bus.hold = False, False
            self.window.client.model.host_prompt_document = None

    def test_invite_validator_and_secret_outputs(self) -> None:
        # Keep the +301s invalid frame outside the 300s window across callbacks.
        clock = patch.object(time, "time", return_value=time.time())
        clock.start()
        self.addCleanup(clock.stop)
        frame = dict(result="complete", invite=INVITE, expires_at=int(time.time()) + 200)
        invalid = ["{", json.dumps(frame) + " " * 6144, '"accepted"',
                   json.dumps(frame).replace('"result": "complete"', '"result":"complete","result":"complete"')]
        for key, value in (("result", "ready"), ("invite", "hrinvite2:hrinvite2:bad"),
                           ("invite", "hrinvite2:" + "x" * 5500), ("invite", "hrinvite2:bad\n"),
                           ("expires_at", True), ("expires_at", time.time() + 200),
                           ("expires_at", int(time.time())), ("expires_at", int(time.time()) + 301),
                           ("extra", INVITE)):
            invalid.append(json.dumps({**frame, key: value}))
        invalid.extend(json.dumps({k: v for k, v in frame.items() if k != missing}) for missing in frame)
        output, errors, logs = io.StringIO(), io.StringIO(), io.StringIO()
        handler = logging.StreamHandler(logs)
        previous_level = logging.getLogger().level
        logging.getLogger().setLevel(logging.DEBUG)
        self.addCleanup(logging.getLogger().setLevel, previous_level)
        logging.getLogger().addHandler(handler)
        self.addCleanup(logging.getLogger().removeHandler, handler)
        with redirect_stdout(output), redirect_stderr(errors):
            for index, raw in enumerate(invalid):
                with self.subTest(frame=index):
                    self.bus.ack, self.bus.calls = raw, []
                    self.assertEqual(self.request("IssueInvite"), (None, "outcomeUnknown", "unknown"))
                    self.assertEqual(self.displayed, [])
                    self.assertEqual([call[3] for call in self.bus.calls], ["IssueInvite"])
            self.bus.ack = json.dumps(frame)
            result, = self.request("IssueInvite")
            self.assertEqual(self.displayed[0][:2], (INVITE, frame["expires_at"]))
            self.assertGreater(self.displayed[0][2], time.monotonic())
            self.assertEqual(result["expiresAt"], time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(frame["expires_at"])))
            self.assertNotIn(INVITE, json.dumps(result))
            self.assertEqual(self.request("Status")[0]["inviteState"], "unknown")
        self.assertNotIn(INVITE, "".join(self.events) + output.getvalue() + errors.getvalue() + logs.getvalue())

    def test_headless_and_prompt_busy_before_dispatch(self) -> None:
        for method in ("Enable", "Disable", "IssueInvite"):
            for display in (None, SimpleNamespace(is_closed=lambda: True)):
                with self.subTest(headless=method, display=display), \
                        patch.object(self.window.window, "get_display", return_value=display):
                    self.assertEqual(self.request(method), (None, "unavailable", "none", "nativePermission"))
                    self.assertEqual(self.bus.calls, [])
            for owner, field in ((self.window.client, "prompt"), (self.window.client.model, "confirming_target"),
                                  (self.window.client.model, "host_prompt_document")):
                with self.subTest(method=method, reserved=field):
                    setattr(owner, field, object())
                    self.assertEqual(self.request(method), (None, "busy"))
                    self.assertEqual(self.bus.calls, [])
                    setattr(owner, field, None)
            self.bus.hold = True
            self.window.host_request(method, lambda *args: self.outcomes.append(args))
            self.assertIs(self.window.client.model.host_prompt_document, self.window.client_document)
            for conflicting in ("Status", "Enable", "Disable", "IssueInvite"):
                self.assertEqual(self.request(conflicting), (None, "busy"))
            self.assertEqual([call[3] for call in self.bus.calls], [method])
            self.bus.held.pop()()
            self.assertFalse(self.window.busy)
            self.assertIsNone(self.window.client.model.host_prompt_document)
            self.bus.calls.clear()
            self.bus.hold = False
            self.drain()


class GTKTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Gtk.init_check()[0]:
            raise unittest.SkipTest("GTK desktop unavailable")
        cls.app = Gtk.Application(application_id="org.happyranch.ClientSmoke")
        cls.app.register(None)

    def setUp(self):
        self.ui = ClientUI(self.app, None, Path("/synthetic"), lambda en, zh: en)
        self.addCleanup(self.ui.shutdown)

    def pump(self, predicate):
        deadline = time.monotonic() + 10
        context = GLib.MainContext.default()
        while not predicate() and time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            time.sleep(0.01)
        self.assertTrue(predicate(), "GTK/WebKit result did not arrive")

    def test_main_bridge_status_event_and_frame_navigation_refusal(self) -> None:
        from app_scheme_webkit import AppScheme, ORIGIN
        import app_scheme_webkit as main_owner
        with tempfile.TemporaryDirectory(prefix="hr-main-bridge-") as directory:
            root = Path(directory)
            spec = importlib.util.spec_from_file_location("webkit_locale", Path(__file__).with_name("app_locale.py"))
            store = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(store)
            environment = patch.dict(os.environ, XDG_CONFIG_HOME=str(root / "config"), LANG="en_US.UTF-8")
            environment.start()
            self.addCleanup(environment.stop)
            dependency = patch.object(main_owner, "_locale", store, create=True)
            dependency.start()
            self.addCleanup(dependency.stop)
            bundle = root / "bundle"
            bundle.mkdir()
            (bundle / "index.html").write_text('<!doctype html><title>Synthetic Main</title><script src="/early.js"></script><p>Main</p>')
            (bundle / "early.js").write_text("window.early=null;happyranchConnection.call('app.status','{}').then(raw=>early=JSON.parse(raw));")
            scheme = AppScheme(bundle, None, root / "data")
            window = Gtk.ApplicationWindow(application=self.app)
            window.add(scheme.view)
            window.show_all()
            try:
                def evaluate(source: str) -> object:
                    values = []
                    def done(view: object, result: object, _data: object) -> None:
                        values.append(json.loads(view.call_async_javascript_function_finish(result).to_json(0)))
                    scheme.view.call_async_javascript_function(source, -1, None, None, None, None, done, None)
                    self.pump(lambda: bool(values))
                    return values[0]
                scheme.view.load_uri(ORIGIN + "/")
                self.pump(lambda: scheme.bridge_token is not None)
                self.assertTrue(evaluate("return typeof window.webkit?.messageHandlers?.appBridge === 'undefined';"))
                self.pump(lambda: evaluate("return window.early!==null;"))
                self.assertEqual(evaluate("return window.early.schema;"), 2)
                status = evaluate("return JSON.parse(await happyranchConnection.call('app.status','{}'));")
                self.assertEqual((status["schema"], status["state"], status["target"]), (2, "no_target", None))
                evaluate("window.localeLog=[];window.offLocale=happyranchConnection.on('app.stateChanged',raw=>localeLog.push(['event',JSON.parse(raw).locale]));return null;")
                self.pump(lambda: evaluate("return localeLog.length;") == 1)
                evaluate("localeLog=[];for(const locale of ['zh-CN','en']) happyranchConnection.call('app.locale.set',JSON.stringify({schema:2,locale})).then(raw=>localeLog.push(['reply',JSON.parse(raw).locale]));return null;")
                self.pump(lambda: evaluate("return localeLog.length;") == 4)
                self.assertEqual(evaluate("return localeLog;"), [
                    ["reply", {"current": "zh-CN", "persisted": True}], ["event", {"current": "zh-CN", "persisted": True}],
                    ["reply", {"current": "en", "persisted": True}], ["event", {"current": "en", "persisted": True}]])
                self.pump(lambda: not main_owner._locale_process.busy)
                path = root / "config/happyranch/locale"
                self.assertEqual(path.read_bytes(), b"en\n")
                with patch.object(store.os, "replace", side_effect=OSError("synthetic write failure")):
                    evaluate("localeLog=[];happyranchConnection.call('app.locale.set','{\"schema\":2,\"locale\":\"zh-CN\"}').then(raw=>localeLog.push(['reply',JSON.parse(raw).locale]));return null;")
                    self.pump(lambda: evaluate("return localeLog.length;") == 2)
                self.assertEqual(evaluate("return localeLog;"), [[kind, {"current": "zh-CN", "persisted": False}] for kind in ("reply", "event")])
                self.assertEqual(path.read_bytes(), b"en\n")
                self.pump(lambda: not main_owner._locale_process.busy)
                self.assertEqual(evaluate("try {await happyranchConnection.call('app.locale.set','{\"schema\":2,\"locale\":\"en\",\"locale\":\"zh-CN\"}');return null;}catch(raw){return JSON.parse(raw);}"),
                    {"code": "invalidRequest", "effect": "none", "recovery": "none"})
                self.assertEqual(evaluate("return localeLog.length;"), 2)
                self.assertEqual(path.read_bytes(), b"en\n")
                evaluate("offLocale();return null;")
                self.pump(lambda: not scheme.subscribed)
                self.assertEqual(evaluate("return JSON.parse(await happyranchConnection.call('app.locale.set','{\"schema\":2,\"locale\":\"en\"}')).locale;"),
                    {"current": "en", "persisted": True})
                self.pump(lambda: not main_owner._locale_process.busy)
                self.assertEqual(evaluate("return localeLog.length;"), 2, "unsubscribed setter must drain without publication")
                calls = []
                def dispatch(command: str, document: object, finish: object, **args: object) -> None:
                    calls.append((command, document, args))
                    finish(None, {"code": "incompatible", "side": "host"} if command == "client.commitSwitch"
                           else "owner_save_unknown")
                scheme.client_dispatch = dispatch
                self.assertEqual(evaluate("try {await happyranchConnection.call('client.commitSwitch','{}');return null;}"
                    " catch(raw) {return JSON.parse(raw);}"),
                    {"code": "incompatible", "side": "host", "effect": "none", "recovery": "refresh"})
                self.assertEqual(evaluate("try {await happyranchConnection.call('client.connectSaved',"
                    "'{\"computerRef\":\"opaque-saved-ref\"}');return null;} catch(raw) {return JSON.parse(raw).code;}"),
                    "owner_save_unknown")
                self.assertEqual(calls, [("client.commitSwitch", scheme.document, {}),
                    ("client.connectSaved", scheme.document, {"computerRef": "opaque-saved-ref"})])
                self.assertEqual(evaluate("try {await happyranchConnection.call('client.commitSwitch','{\"invite\":\"synthetic\"}');"
                    "return null;} catch(raw) {return JSON.parse(raw).code;}"), "unsupportedCommand")
                self.assertEqual(len(calls), 2)
                evaluate("window.events=[];window.unsubscribe=happyranchConnection.on('app.stateChanged', raw=>events.push(JSON.parse(raw)));return null;")
                self.pump(lambda: scheme.subscribed)
                scheme.select_target(None, owner_saves=())
                self.pump(lambda: evaluate("return events.some(item=>item.generation!==" + json.dumps(status["generation"]) + ");"))
                self.assertEqual(evaluate("return events.at(-1);"), scheme.status())
                evaluate("const frame=document.createElement('iframe');frame.srcdoc='<script>parent.happyranchConnection.call(\"app.status\",\"{}\")<\\/script>';document.body.append(frame);return null;")
                self.pump(lambda: scheme.bridge_token is None)
                self.assertFalse(scheme.subscribed)
                self.assertEqual(evaluate("try {await happyranchConnection.call('app.status','{}');return 'admitted';} catch (raw) {return JSON.parse(raw).code;}"), "documentChanged")
                self.assertEqual(evaluate("try {await happyranchConnection.call('client.commitSwitch','{}');return 'admitted';}"
                    " catch(raw) {return JSON.parse(raw).code;}"), "documentChanged")
                self.assertEqual(len(calls), 2)
                scheme.view.load_uri(ORIGIN + "/")
                self.pump(lambda: scheme.bridge_token is not None)
                scheme.view.load_uri("about:blank")
                self.pump(lambda: scheme.bridge_token is None)
            finally:
                scheme.close()
                window.destroy()

    def test_host_invite_sheet_copy_expiry_and_close(self):
        parent = Gtk.ApplicationWindow(application=self.app)
        self.addCleanup(parent.destroy)
        expiry = int(time.time()) + 200
        for chinese in (False, True):
            owner = SimpleNamespace(Gtk=Gtk, GLib=GLib, window=parent, invite_sheet=None,
                                    tr=lambda en, zh: zh if chinese else en)
            clipboard = Gtk.Clipboard.get_default(parent.get_display())
            for close in (False, True):
                primary = Gtk.Clipboard.get(Gdk.SELECTION_PRIMARY)
                primary.set_text("other-app-primary", -1)
                clipboard.set_text("other-app-content", -1)
                sheet = connection_owner.InviteSheet(owner, INVITE, expiry, time.monotonic() + 120)
                owner.invite_sheet = sheet
                self.addCleanup(sheet.close)
                self.assertEqual(sheet.label.get_text(), INVITE)
                # Exercise GTK's real focus and key-binding handlers, including
                # the label action shared by Ctrl+C and its context Copy.
                for action in ("focus", "select-all", "ctrl-a", "ctrl-c", "label-copy"):
                    if action == "focus":
                        sheet.label.grab_focus()
                    elif action == "select-all":
                        sheet.label.select_region(0, -1)
                    elif action in ("ctrl-a", "ctrl-c"):
                        key = Gdk.KEY_a if action == "ctrl-a" else Gdk.KEY_c
                        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
                        event.window = sheet.dialog.get_window()
                        event.send_event = True
                        event.time = Gdk.CURRENT_TIME
                        event.state = Gdk.ModifierType.CONTROL_MASK
                        event.keyval = key
                        event.set_device(parent.get_display().get_default_seat().get_keyboard())
                        sheet.dialog.emit("key-press-event", event)
                    else:
                        sheet.label.emit("copy-clipboard")
                    for selection, expected in ((primary, "other-app-primary"),
                                                (clipboard, "other-app-content")):
                        with self.subTest(chinese=chinese, close=close, action=action, selection=expected):
                            self.assertEqual(selection.wait_for_text(), expected)
                    self.assertFalse(sheet.copied)
                with self.subTest(chinese=chinese, close=close, label="flags"):
                    self.assertFalse(sheet.label.get_selectable())
                    self.assertFalse(sheet.label.get_can_focus())
                    self.assertIsNot(sheet.dialog.get_focus(), sheet.label)
                with patch.object(connection_owner.time, "time", return_value=expiry - 125):
                    self.assertTrue(sheet.tick())
                self.assertIn("2:05", sheet.expiry.get_text())
                self.assertIn(time.strftime("%Y-%m-%d", time.localtime(expiry)), sheet.expiry.get_text())
                labels = [w.get_text() for w in sheet.dialog.get_content_area().get_children() if isinstance(w, Gtk.Label)]
                self.assertTrue(any(("Linux 机主" if chinese else "Linux owner") in text for text in labels))
                self.assertTrue(any(("关闭不会取消" if chinese else "Closing does not cancel") in text for text in labels))
                self.assertEqual(sheet.dialog.get_default_widget(), sheet.dialog.get_widget_for_response(Gtk.ResponseType.CLOSE))
                sheet.copy.clicked()
                self.assertTrue(sheet.copied)
                self.assertEqual(clipboard.wait_for_text(), INVITE)
                with self.subTest(chinese=chinese, close=close, action="explicit-copy-primary"):
                    self.assertEqual(primary.wait_for_text(), "other-app-primary")
                if close:
                    sheet.dialog.response(Gtk.ResponseType.CLOSE)
                    self.assertIsNone(owner.invite_sheet)
                else:
                    with patch.object(connection_owner.time, "time", return_value=expiry):
                        self.assertFalse(sheet.tick())
                    self.assertEqual(sheet.expiry.get_text(), "邀请已过期" if chinese else "Invite expired")
                self.assertEqual((sheet.text, sheet.label.get_text()), ("", ""))
                self.assertFalse(sheet.copy.get_sensitive())
                self.assertFalse(sheet.copied)
                self.assertNotEqual(clipboard.wait_for_text(), INVITE)
                with self.subTest(chinese=chinese, close=close, action="ending-primary"):
                    self.assertEqual(primary.wait_for_text(), "other-app-primary")
                sheet.close()
            for ending in ("close", "expiry"):
                sheet = connection_owner.InviteSheet(owner, INVITE, expiry, time.monotonic() + 120)
                owner.invite_sheet = sheet
                self.addCleanup(sheet.close)
                sheet.copy.clicked()
                clipboard.set_text("new-other-app-content", -1)
                if ending == "close":
                    sheet.close()
                else:
                    with patch.object(connection_owner.time, "time", return_value=expiry):
                        sheet.tick()
                self.assertEqual(clipboard.wait_for_text(), "new-other-app-content")
                sheet.close()
            for deadline, expires_at in ((0, expiry), (time.monotonic() + 120, 0)):
                with self.assertRaises(ValueError):
                    connection_owner.InviteSheet(owner, INVITE, expires_at, deadline)
                self.assertIsNone(owner.invite_sheet)

        # The helper request has completed, but the native invite belongs to
        # its original document until that document retires.
        bridge = connection_owner.HostBridge.__new__(connection_owner.HostBridge)
        bridge.Gtk, bridge.GLib, bridge.tr = Gtk, GLib, lambda en, zh: en
        bridge.subscribers, bridge.host_timer, bridge.invite_sheet = {}, None, None
        document = bridge.operation_owner = object()
        bridge.cancel_request = None
        bridge.show_invite(INVITE, expiry, time.monotonic() + 120, parent)
        sheet = bridge.invite_sheet
        self.addCleanup(sheet.close)
        bridge.operation_owner = None
        sheet.copy.clicked()
        bridge.detach(object())
        self.assertEqual(sheet.label.get_text(), INVITE)
        bridge.detach(document)
        self.assertEqual((sheet.text, sheet.label.get_text()), ("", ""))
        self.assertFalse(sheet.dialog.get_visible())
        self.assertNotEqual(sheet.clipboard.wait_for_text(), INVITE)

    def test_updated_target_requires_native_confirmation(self):
        invite = SimpleNamespace(home_id="synthetic-home", control_plane_id="control", home_endpoint="100.64.0.2:443",
                                 control_url="https://control.invalid", dial_ip="192.168.1.41", ca_der=b"synthetic-DER")
        for accept in (False, True):
            consent = []
            self.ui.recovery_target(invite, consent.append)
            dialog = self.ui.prompt
            label = next(widget for widget in dialog.get_content_area().get_children() if isinstance(widget, Gtk.Label))
            for field in (invite.home_id, invite.control_plane_id, invite.control_url, invite.dial_ip,
                          invite.home_endpoint, hashlib.sha256(invite.ca_der).hexdigest()):
                self.assertIn(field, label.get_text())
            self.assertIn("invitation will not be redeemed", label.get_text())
            self.assertEqual(dialog.get_default_widget(), dialog.get_widget_for_response(Gtk.ResponseType.CANCEL))
            self.assertEqual(consent, [])
            dialog.response(Gtk.ResponseType.OK if accept else Gtk.ResponseType.CANCEL)
            self.assertEqual(consent, [accept])

    def test_native_prompt_defaults(self):
        pasted = []
        self.ui.paste(pasted.append)
        self.assertIsNotNone(self.ui.prompt)
        entry = next(widget for widget in self.ui.prompt.get_content_area().get_children()
                     if isinstance(widget, Gtk.Entry))
        self.assertFalse(entry.get_visibility())
        entry.set_text("synthetic-invite-canary")
        self.ui.prompt.response(Gtk.ResponseType.CANCEL)
        self.assertEqual(pasted, [None])
        self.assertEqual(entry.get_text(), "")
        consent = []
        self.ui.target(SimpleNamespace(home_id="synthetic-home", control_plane_id="control", home_endpoint="home:443", ca_der=b"synthetic-DER"),
                       lambda *args: consent.append(args))
        save = next(w for w in self.ui.prompt.get_content_area().get_children() if isinstance(w, Gtk.CheckButton))
        self.assertFalse(save.get_active())
        save.set_active(True)
        self.ui.prompt.response(Gtk.ResponseType.OK)
        self.assertEqual(consent, [(True, True)])
        results = []
        self.ui.confirm_stop("synthetic-host", results.append)
        self.ui.prompt.response(Gtk.ResponseType.CANCEL)
        self.assertEqual(results, [False])



if __name__ == "__main__":
    unittest.main(verbosity=2)

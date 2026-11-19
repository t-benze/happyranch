"""Standalone GTK/WebKit smoke using a synthetic HTTP runtime, never a daemon."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import hashlib
import json
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gio, GLib, Gtk

import client_ui as client_owner
from client_ui import ClientUI
from connection_window import ConnectionWindow
from test_client_model import Session


class Runtime(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<!doctype html><title>Synthetic release runtime</title><p>Runtime loaded</p>"
        self.send_response(500 if getattr(self.server, "failure", False) else 200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class HostBus:
    """Test-side system-bus stub; production keeps its fixed system address."""
    def __init__(self):
        device = {"label": "Synthetic &amp; device", "fingerprint": "a" * 24,
                  "comparison_phrase": "synthetic comparison"}
        self.snapshot = dict(phase="ready", ready=True, devices=[device], pending=device, selected=device)
        self.calls, self.held = [], []
        self.error = self.status_error = None
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
        raw = json.dumps(self.snapshot) if method in ("Status", "NextDevice") else '"ok"'
        if method not in ("Status", "NextDevice") and not error:
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

    def test_native_prompt_defaults_and_runtime_gate(self):
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

        server = ThreadingHTTPServer(("127.0.0.1", 0), Runtime)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(worker.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        root = f"http://127.0.0.1:{server.server_port}/"
        model = self.ui.model
        model.stage = "opening"
        model.save_intent = True
        token = model.token
        self.ui.open_runtime(root, token)
        self.assertEqual(model.stage, "opening")
        self.pump(lambda: model.stage == "connected")
        notices = self.ui.runtime_window.get_child().get_children()
        self.assertIn("Access is not saved", notices[0].get_text())
        self.assertEqual(notices[0].get_accessible().get_name(), notices[0].get_text())
        controls = []
        self.ui.show_controls = lambda: controls.append(True)
        self.ui.retry_save.clicked()
        self.assertEqual(controls, [True])
        model.saved_access = "saved"
        self.ui.changed()
        self.assertFalse(self.ui.notice.get_visible())
        self.assertFalse(self.ui.retry_save.get_visible())
        model.saved_access = "notSaved"
        self.ui.changed()
        self.assertTrue(self.ui.notice.get_visible())
        self.assertTrue(self.ui.retry_save.get_visible())
        observed = []
        def checked(view, result, _data):
            observed.append(view.call_async_javascript_function_finish(result).to_boolean())
        self.ui.runtime_view.call_async_javascript_function(
            "return typeof window.happyranchConnection === 'undefined' && typeof window.webkit?.messageHandlers?.connection === 'undefined';",
            -1, None, None, root, None, checked, None)
        self.pump(lambda: bool(observed))
        self.assertEqual(observed, [True])
        self.ui.close_runtime()
        server.failure = True
        model.stage = "opening"
        self.ui.open_runtime(root, token)
        self.pump(lambda: model.stage == "failed")
        self.assertIsNone(self.ui.runtime_view)

    def test_host_facade_dispatch_outcomes_events_and_fencing(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory)
            root = prefix / "share/happyranch/desktop/connection"
            root.mkdir(parents=True)
            (root.parent / "native-connection-bridge.v1.json").write_bytes(
                (Path(__file__).parents[3] / "protocol/native-connection-bridge.v1.json").read_bytes())
            resources = {"index.html": b'<!doctype html><script src="host.js"></script>',
                         "host.js": b"happyranchConnection.call('bridge.hello','{\"minVersion\":1,\"maxVersion\":1}');"}
            for name, data in resources.items():
                (root / name).write_bytes(data)
            (root / "resource-manifest.json").write_text(json.dumps(dict(version=1, entry="index.html",
                resources=[dict(path=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
                           for name, data in resources.items()])))
            window = ConnectionWindow(self.app, None, prefix, self.ui.tr, self.ui)
            self.addCleanup(window.close)
            self.pump(lambda: window.hello_seen or window.unavailable_reason is not None)
            self.assertIsNone(window.unavailable_reason)
            bus = HostBus()
            window.connection = bus

            def evaluate(source, world=None):
                values = []
                def done(view, result, _data):
                    values.append(view.call_async_javascript_function_finish(result).to_json(0))
                window.view.call_async_javascript_function(source, -1, None, world, None, None, done, None)
                self.pump(lambda: bool(values))
                return json.loads(values[0])

            def start(command, args="{}"):
                return evaluate("window.hostReply=null;window.hostError=null;happyranchConnection.call(" +
                                json.dumps(command) + "," + json.dumps(args) +
                                ").then(v=>window.hostReply=JSON.parse(v),e=>window.hostError=JSON.parse(e));return null;")

            def outcome(command, args="{}"):
                start(command, args)
                self.pump(lambda: evaluate("return window.hostReply!==null || window.hostError!==null;"))
                return evaluate("return [window.hostReply,window.hostError];")

            # Match loadRole: subscribe, then immediately read before the
            # subscription's asynchronous Status reply can arrive.
            bus.hold = True
            evaluate("""
                window.hostEvents=[];window.hostReply=null;window.hostError=null;
                const controls=document.createElement('button');
                controls.id='host-controls';controls.hidden=true;controls.disabled=true;
                document.body.append(controls);
                window.stopHost=happyranchConnection.on('host.stateChanged',v=>hostEvents.push(JSON.parse(v)));
                happyranchConnection.call('host.status','{}').then(raw=>{
                    window.hostReply=JSON.parse(raw);
                    controls.hidden=false;controls.disabled=false;
                },raw=>{window.hostError=JSON.parse(raw);stopHost();controls.remove();});
                return null;
            """)
            self.pump(lambda: len(bus.held) == 1 and window.requests.reads == 1
                      or evaluate("return window.hostError!==null;"))
            self.assertIsNone(evaluate("return window.hostError;"), "initial Status rejected during subscription priming")
            self.assertIsNone(evaluate("return window.hostReply;"))
            self.assertEqual([c[3] for c in bus.calls], ["Status"])
            self.assertTrue(evaluate("return document.querySelector('#host-controls').hidden;"))
            # Eight native reads remain the cap even when they share one call.
            evaluate("""
                window.joined=[];
                for(let n=0;n<8;n++)window.webkit.messageHandlers.connection.postMessage(JSON.stringify({
                    protocol:'happyranch.connection',version:1,requestId:'joined'+n,command:'host.status',args:{}
                })).then(raw=>joined.push(JSON.parse(raw)));
                return null;
            """, "happyranch.connection.v1")
            self.pump(lambda: evaluate("return window.joined.length;", "happyranch.connection.v1") == 1)
            self.assertEqual(evaluate("return window.joined[0].error.code;", "happyranch.connection.v1"), "busy")
            self.assertEqual(window.requests.reads, 8)
            self.assertEqual([c[3] for c in bus.calls], ["Status"])
            # A mutation cannot queue behind the priming read.
            blocked = json.dumps(dict(protocol="happyranch.connection", version=1, requestId="blockedMutation",
                                      command="host.approveDevice", args={}))
            result = evaluate("return JSON.parse(await window.webkit.messageHandlers.connection.postMessage(" +
                              json.dumps(blocked) + "));", "happyranch.connection.v1")
            self.assertEqual(result["error"]["code"], "busy")
            bus.hold = False
            bus.held.pop()()
            self.pump(lambda: evaluate("return window.hostReply!==null && window.hostEvents.length>0;"))
            self.assertEqual(evaluate("return window.hostReply;"), evaluate("return window.hostEvents[0].snapshot;"))
            self.assertEqual(evaluate("return window.hostReply.setup;"), "ready")
            self.assertIsNone(evaluate("return window.hostError;"))
            self.assertTrue(evaluate("const c=document.querySelector('#host-controls');return !!c && !c.hidden && !c.disabled;"))
            evaluate("stopHost();return null;")
            self.pump(lambda: not window.host_subscribed)
            self.pump(lambda: evaluate("return window.joined.length;", "happyranch.connection.v1") == 8)
            self.assertEqual(evaluate("return window.joined.filter(v=>v.ok).length;", "happyranch.connection.v1"), 7)
            self.assertEqual(window.requests.reads, 0)
            self.assertIsNone(window.host_status_waiters)
            # Coalesced failures resolve every reader and release the slot.
            bus.calls.clear()
            bus.hold = True
            bus.status_error = "org.freedesktop.DBus.Error.NoReply"
            evaluate("""
                window.readErrors=[];
                for(let n=0;n<2;n++)happyranchConnection.call('host.status','{}').catch(raw=>readErrors.push(JSON.parse(raw)));
                return null;
            """)
            self.pump(lambda: len(bus.held) == 1 and window.requests.reads == 2)
            bus.hold = False
            bus.held.pop()()
            self.pump(lambda: evaluate("return window.readErrors.length;") == 2)
            self.assertEqual(evaluate("return window.readErrors.map(e=>e.code);"), ["unavailable", "unavailable"])
            self.assertEqual([c[3] for c in bus.calls], ["Status"])
            self.assertEqual(window.requests.reads, 0)
            bus.status_error = None

            methods = {"host.status": "Status", "host.nextDevice": "NextDevice",
                       "host.approveDevice": "ApprovePending", "host.rejectDevice": "RejectPending",
                       "host.revokeDevice": "RevokeSelected"}
            for command, method in methods.items():
                with self.subTest(command=command):
                    bus.calls.clear()
                    bus.snapshot.update(phase="ready", ready=True)
                    result, error = outcome(command)
                    self.assertIsNone(error)
                    self.assertEqual([c[3] for c in bus.calls],
                                     [method] if method in ("Status", "NextDevice") else [method, "Status"])
                    for call in bus.calls:
                        mutation = call[3] not in ("Status", "NextDevice")
                        self.assertEqual(call[:3], ("ai.happyranch.RemoteAccess1", "/ai/happyranch/RemoteAccess1",
                                                    "ai.happyranch.RemoteAccess1"))
                        self.assertIsNone(call[4])
                        self.assertEqual(call[5], "(s)")
                        self.assertEqual(call[6], Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION
                                         if mutation else Gio.DBusCallFlags.NONE)
                        self.assertEqual(call[7], 122000 if mutation else 3000)
                    snapshot = result if method in ("Status", "NextDevice") else result["host"]
                    self.assertEqual(snapshot["setup"], "ready" if method in ("Status", "NextDevice") else "paused")
                    self.assertEqual(snapshot["selected"], {"label": "Synthetic & device",
                                                          "comparisonPhrase": "synthetic comparison"})
                    self.assertNotIn("fingerprint", json.dumps(result))
                    self.assertNotIn("a" * 24, json.dumps(result))
                    if method not in ("Status", "NextDevice"):
                        self.assertEqual(result["effect"], "confirmed")
                        self.assertEqual(result["hostDenial"], "confirmed" if method != "ApprovePending" else "notApplicable")
                        self.assertEqual(result["networkCleanup"], "unknown" if method == "RevokeSelected" else "notApplicable")
            bus.calls.clear()
            raw = json.dumps(dict(protocol="happyranch.connection", version=1, requestId="badAuthority",
                                  command="host.revokeDevice", args={"fingerprint": "synthetic"}))
            error = evaluate("try {await window.webkit.messageHandlers.connection.postMessage(" + json.dumps(raw) +
                             ");return null;} catch(e) {return String(e);}", "happyranch.connection.v1")
            self.assertIn('"code":"invalidRequest"', error)
            self.assertEqual(bus.calls, [])
            for command in ("host.enable", "host.turnOff", "host.createInvite"):
                self.assertEqual(outcome(command)[1]["code"], "unavailable")
            for remote, code, effect in (
                    ("ai.happyranch.RemoteAccess1.Error.denied", "permissionDenied", "none"),
                    ("org.freedesktop.DBus.Error.AccessDenied", "permissionDenied", "none"),
                    ("ai.happyranch.RemoteAccess1.Error.changed", "stateChanged", "none"),
                    ("ai.happyranch.RemoteAccess1.Error.expired", "expired", "none"),
                    ("ai.happyranch.RemoteAccess1.Error.busy", "busy", "none"),
                    ("ai.happyranch.RemoteAccess1.Error.unavailable", "outcomeUnknown", "unknown"),
                    ("org.freedesktop.DBus.Error.NoReply", "outcomeUnknown", "unknown"),
                    ("org.freedesktop.DBus.Error.ServiceUnknown", "unavailable", "none"),
                    ("org.freedesktop.DBus.Error.NameHasNoOwner", "unavailable", "none")):
                with self.subTest(remote=remote):
                    bus.error = remote
                    bus.calls.clear()
                    result, error = outcome("host.approveDevice")
                    self.assertIsNone(result)
                    self.assertEqual((error["code"], error["effect"]), (code, effect))
                    self.assertNotIn("synthetic-secret-canary", json.dumps(error))
                    self.assertEqual(error["recovery"], "nativeRecovery" if effect == "unknown" else
                                     "nativePermission" if code == "permissionDenied" else "refresh")
                    self.assertEqual([c[3] for c in bus.calls], ["ApprovePending"])
            bus.error = None
            bus.status_error = "org.freedesktop.DBus.Error.ServiceUnknown"
            result, error = outcome("host.status")
            self.assertIsNone(error)
            self.assertEqual((result["setup"], result["observation"], result["devices"]), ("off", "unavailable", None))
            # The acknowledgement is confirmed even if its subsequent observation fails.
            result, error = outcome("host.revokeDevice")
            self.assertIsNone(error)
            self.assertEqual((result["effect"], result["host"]["observation"]), ("confirmed", "unavailable"))
            bus.status_error = None

            def prompt_conflict(command):
                calls = list(bus.calls)
                start(command)
                self.pump(lambda: bool(bus.held) or evaluate("return window.hostError!==null;"))
                self.assertEqual(evaluate("return window.hostError;"),
                                 dict(code="busy", effect="none", recovery="refresh"),
                                 "pending Client confirmation must refuse Host authorization before dispatch")
                self.assertEqual(bus.calls, calls)

            # Cancelling the initial paste must release the same reservation.
            self.assertIsNone(outcome("client.acceptInvite")[1])
            self.ui.prompt.response(Gtk.ResponseType.CANCEL)
            self.pump(lambda: self.ui.model.stage == "idle")
            self.assertIsNone(outcome("host.approveDevice")[1])
            for ending in ("cancel", "failure", "detach"):
                entered, release = threading.Event(), threading.Event()
                invite = SimpleNamespace(home_id="synthetic-home", control_plane_id="synthetic-control",
                    home_endpoint="synthetic.home:443", ca_der=b"synthetic-DER", expires_at=time.time() + 120)
                def decode(_text):
                    entered.set()
                    if not release.wait(10):
                        raise AssertionError("held decoder was not released")
                    if ending == "failure":
                        raise client_owner._model._native.ClientError("invalidInvite")
                    return invite
                bus.calls.clear()
                bus.hold = True
                try:
                    with patch.object(client_owner._model._native, "decode_invite", decode):
                        self.assertIsNone(outcome("client.acceptInvite")[1])
                        entry = next(w for w in self.ui.prompt.get_content_area().get_children()
                                     if isinstance(w, Gtk.Entry))
                        entry.set_text("synthetic-invite")
                        self.ui.prompt.response(Gtk.ResponseType.OK)
                        self.pump(entered.is_set)
                        self.pump(lambda: not window.requests.mutating)
                        self.assertIsNone(self.ui.prompt)
                        self.assertEqual(self.ui.model.stage, "validating")
                        for command in ("host.approveDevice", "host.rejectDevice", "host.revokeDevice"):
                            prompt_conflict(command)
                        self.assertFalse(window.busy)
                        if ending == "detach":
                            # A replacement can authorize immediately; the old
                            # worker must never open its late confirmation.
                            old_window = window
                            window.detach()
                            window = ConnectionWindow(self.app, None, prefix, self.ui.tr, self.ui)
                            self.addCleanup(window.close)
                            self.pump(lambda: window.hello_seen)
                            bus = HostBus()
                            window.connection = bus
                            bus.hold = True
                            start("host.approveDevice")
                            self.pump(lambda: len(bus.held) == 1)
                            old_window.close()
                            stopped = []
                            self.ui.model.confirm_stop(lambda *args: stopped.append(args))
                            self.assertEqual(stopped, [(None, "busy", "none")])
                            self.assertIsNone(self.ui.prompt)
                        release.set()
                        if ending == "detach":
                            self.pump(lambda: not self.ui.model.workers)
                            drained = []
                            GLib.idle_add(lambda: (drained.append(True), False)[1])
                            self.pump(lambda: bool(drained))
                            self.assertIsNone(self.ui.prompt)
                            self.assertEqual(self.ui.model.stage, "idle")
                            bus.hold = False
                            bus.held.pop()()
                            self.pump(lambda: evaluate("return window.hostReply!==null;"))
                            self.assertEqual([c[3] for c in bus.calls], ["ApprovePending", "Status"])
                        else:
                            self.pump(lambda: self.ui.prompt is not None)
                            self.assertFalse(window.busy)
                            if ending == "cancel":
                                self.assertEqual(self.ui.prompt.get_title(), "Confirm computer")
                                prompt_conflict("host.approveDevice")
                                self.ui.prompt.response(Gtk.ResponseType.CANCEL)
                                self.pump(lambda: self.ui.model.stage == "idle")
                            else:
                                self.assertEqual(self.ui.model.stage, "failed")
                                self.assertIn("Invite is invalid", self.ui.prompt.get_property("text"))
                                self.ui.prompt.response(Gtk.ResponseType.CLOSE)
                finally:
                    release.set()
                bus.hold = False
                bus.calls.clear()
                self.assertIsNone(outcome("host.rejectDevice")[1])
                self.assertEqual([c[3] for c in bus.calls], ["RejectPending", "Status"])

            # Host-first: web mutations and native runtime stop controls cannot
            # open a Client confirmation while interactive authorization waits.
            bus.calls.clear()
            bus.hold = True
            start("host.approveDevice")
            self.pump(lambda: len(bus.held) == 1)
            self.assertEqual(outcome("client.acceptInvite")[1]["code"], "busy")
            self.ui.model.changed("joining")
            self.assertEqual(outcome("client.cancelAttempt")[1]["code"], "busy")
            self.assertEqual(self.ui.model.stage, "joining")
            self.ui.model.changed("idle")
            stopped = []
            self.ui.model.confirm_stop(lambda *args: stopped.append(args))
            self.assertEqual(stopped, [(None, "busy", "none")])
            self.assertIsNone(self.ui.prompt)
            self.assertFalse(self.ui.model.hold)
            bus.hold = False
            bus.held.pop()()
            self.pump(lambda: not window.busy)
            self.ui.model.confirm_stop(lambda *args: stopped.append(args))
            self.assertIsNotNone(self.ui.prompt)
            self.ui.prompt.response(Gtk.ResponseType.CANCEL)
            self.assertEqual(len(stopped), 2)

            evaluate("window.hostEvents=[];window.stopHost=happyranchConnection.on('host.stateChanged',v=>hostEvents.push(JSON.parse(v)));return null;")
            self.pump(lambda: evaluate("return window.hostEvents.length>0;"))
            first = evaluate("return window.hostEvents.at(-1);")
            self.assertEqual(first["snapshot"]["setup"], "paused")
            self.assertNotIn("fingerprint", json.dumps(first))
            self.assertNotIn("a" * 24, json.dumps(first))
            # Observe an external helper change through the real subscription timer.
            bus.snapshot.update(phase="ready", ready=True)
            self.pump(lambda: evaluate("return window.hostEvents.at(-1).snapshot.setup;") == "ready")
            outcome("host.nextDevice")
            self.pump(lambda: evaluate("return window.hostEvents.at(-1).sequence;") > first["sequence"])
            evaluate("stopHost();stopHost();return null;")
            # Let native unsubscribe arrive before checking that later observations do not emit.
            self.pump(lambda: not window.host_subscribed)
            self.assertIsNone(window.host_timer)
            count = evaluate("return window.hostEvents.length;")
            outcome("host.status")
            self.assertEqual(evaluate("return window.hostEvents.length;"), count)
            # Duplicate native IDs share one dispatch and the recorded outcome.
            bus.calls.clear()
            raw = json.dumps(dict(protocol="happyranch.connection", version=1, requestId="hostOnce",
                                  command="host.rejectDevice", args={}))
            evaluate("window.dupes=[];const raw=" + json.dumps(raw) + ";"
                     "for(let n=0;n<2;n++)window.webkit.messageHandlers.connection.postMessage(raw).then(v=>dupes.push(v));return null;",
                     "happyranch.connection.v1")
            self.pump(lambda: evaluate("return window.dupes.length;", "happyranch.connection.v1") == 2)
            self.assertEqual([c[3] for c in bus.calls], ["RejectPending", "Status"])
            bus.hold = True
            bus.calls.clear()
            start("host.approveDevice")
            self.pump(lambda: len(bus.held) == 1)
            self.assertEqual(outcome("host.status")[1]["code"], "busy")
            window.detach()
            bus.held.pop()()
            self.assertEqual([c[3] for c in bus.calls], ["ApprovePending"])
            self.assertIsNone(evaluate("return window.hostReply;"))
            self.assertEqual(evaluate("return window.hostEvents.length;"), count)
            # Reopen with a fresh generation, then detach coalesced Status readers.
            window = ConnectionWindow(self.app, None, prefix, self.ui.tr, self.ui)
            self.addCleanup(window.close)
            self.pump(lambda: window.hello_seen)
            bus = HostBus()
            window.connection = bus
            bus.hold = True
            evaluate("""
                window.lateReplies=[];
                for(let n=0;n<2;n++)happyranchConnection.call('host.status','{}').then(raw=>lateReplies.push(raw));
                return null;
            """)
            self.pump(lambda: len(bus.held) == 1 and window.requests.reads == 2)
            window.detach()
            bus.held.pop()()
            self.assertIsNone(window.host_status_waiters)
            self.assertTrue(bus.closed)
            self.assertEqual(evaluate("return window.lateReplies;"), [])
            self.assertEqual([c[3] for c in bus.calls], ["Status"])

    def test_packaged_facade_events_unsubscribe_and_teardown(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory)
            root = prefix / "share/happyranch/desktop/connection"
            root.mkdir(parents=True)
            (root.parent / "native-connection-bridge.v1.json").write_bytes(
                (Path(__file__).parents[3] / "protocol/native-connection-bridge.v1.json").read_bytes())
            resources = {
                "index.html": b'<!doctype html><script src="smoke.js"></script>',
                "smoke.js": b'''window.events = [];
window.ready = false;
window.stopEvents = happyranchConnection.on('client.stateChanged', value => events.push(JSON.parse(value)));
Promise.all([happyranchConnection.call('bridge.hello', '{"minVersion":1,"maxVersion":1}'),
 happyranchConnection.call('client.status', '{}')]).then(values => {window.results=values.map(JSON.parse);window.ready=true;});'''
            }
            for name, data in resources.items():
                (root / name).write_bytes(data)
            (root / "resource-manifest.json").write_text(json.dumps(dict(version=1, entry="index.html",
                resources=[dict(path=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
                           for name, data in resources.items()])))
            window = ConnectionWindow(self.app, None, prefix, self.ui.tr, self.ui)
            self.addCleanup(window.close)
            self.pump(lambda: window.hello_seen or window.unavailable_reason is not None)
            self.assertIsNone(window.unavailable_reason)
            observed = []
            def evaluate(source):
                observed.clear()
                def done(view, result, _data):
                    observed.append(view.call_async_javascript_function_finish(result).to_json(0))
                window.view.call_async_javascript_function(source, -1, None, None, None, None, done, None)
                self.pump(lambda: bool(observed))
                return json.loads(observed[0])
            self.pump(lambda: window.event_sequence >= 1)
            self.assertTrue(evaluate("return window.ready;"))
            self.assertEqual(evaluate("return window.results[1].stage;"), "idle")
            self.assertTrue(evaluate("return window.results[0].capabilities.some(c => c.command === 'client.acceptInvite' && c.availability === 'available');"))
            self.ui.model.changed("validating")
            self.pump(lambda: window.event_sequence >= 2)
            self.assertEqual(evaluate("return window.events.at(-1).snapshot.stage;"), "validating")
            self.assertIsNone(evaluate("window.stopEvents();window.stopEvents();return null;"))
            self.pump(lambda: not window.subscribed)
            sequence = window.event_sequence
            self.ui.model.changed("joining")
            self.assertEqual(window.event_sequence, sequence)
            server = ThreadingHTTPServer(("127.0.0.1", 0), Runtime)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            self.addCleanup(worker.join, 2)
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            model = self.ui.model
            model.document, model.session = window.client_document, Session(None)
            model.stage = "opening"
            model.save_intent = True
            self.ui.open_runtime(f"http://127.0.0.1:{server.server_port}/", model.token)
            self.pump(lambda: model.stage == "connected")
            runtime, session = self.ui.runtime_window, model.session
            window.close()
            self.assertFalse(window.active)
            self.assertIsNone(self.ui.listener)
            self.assertEqual(model.stage, "connected")
            self.ui.retry_save.clicked()  # Closed controls reopen statically; runtime gets no bridge.
            replacement = self.ui.show_controls.__self__
            self.assertIsInstance(replacement, ConnectionWindow)
            self.addCleanup(replacement.close)
            self.pump(lambda: replacement.hello_seen)
            self.assertIsNot(replacement.client_document, window.client_document)
            replacement.view.call_async_javascript_function(
                "happyranchConnection.call('client.disconnect','{}');return null;",
                -1, None, None, None, None, None, None)
            self.pump(lambda: self.ui.prompt is not None)
            prompt = self.ui.prompt
            replacement.close()
            prompt.response(Gtk.ResponseType.OK)
            self.pump(lambda: not model.workers)
            context = GLib.MainContext.default()
            while context.pending():
                context.iteration(False)
            self.assertIsNone(self.ui.prompt)
            self.assertEqual(model.stage, "connected")
            self.assertIs(self.ui.runtime_window, runtime)
            self.assertNotIn("stop", [name for name, _ in session.calls])


if __name__ == "__main__":
    unittest.main(verbosity=2)

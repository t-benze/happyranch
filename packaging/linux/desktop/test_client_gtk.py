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

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import GLib, Gtk

from client_ui import ClientUI
from connection_window import ConnectionWindow


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
        token = model.token
        self.ui.open_runtime(root, token)
        self.assertEqual(model.stage, "opening")
        self.pump(lambda: model.stage == "connected")
        notices = self.ui.runtime_window.get_child().get_children()
        self.assertIn("Access is not saved", notices[0].get_text())
        self.assertEqual(notices[0].get_accessible().get_name(), notices[0].get_text())
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
            window.close()
            self.assertFalse(window.active)
            self.assertIsNone(self.ui.listener)


if __name__ == "__main__":
    unittest.main(verbosity=2)

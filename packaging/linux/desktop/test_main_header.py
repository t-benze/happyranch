"""Headless Main boundary checks: default titlebar, document Retry and lifecycle entry."""
from __future__ import annotations

import io
import importlib.util
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
from types import ModuleType, SimpleNamespace
import sys
import unittest
from unittest.mock import patch

import happyranch_window as native


class HeaderTests(unittest.TestCase):
    def exercise(self, check, chinese=False, app_scheme=True, saved_locale=None, system_chinese=None,
                 scheme_failure=False):
        from gi.repository import Gio
        callbacks, timers, widgets = queue.Queue(), [], []
        self.loaded_uris, self.attempts = [], []
        self.titlebars, self.actions, self.accels = [], {}, {}
        test = self

        class Widget:
            def __init__(self, **kwargs):
                self.label = kwargs.get("label", "")
                self.children, self.signals = [], {}
                self.visible, self.tooltip = True, ""
                widgets.append(self)
            def connect(self, name, callback):
                self.signals[name] = callback
            def add(self, child):
                self.children.append(child)
            def pack_start(self, child, *_args):
                self.add(child)
            pack_end = pack_start
            def get_child(self):
                return self.children[0] if self.children else None
            def set_titlebar(self, header):
                test.titlebars.append(header)
            def get_children(self):
                return self.children[:]
            def remove(self, child):
                self.children.remove(child)
            def set_label(self, value):
                self.label = value
            def load_uri(self, uri):
                test.loaded_uris.append(uri)
            def get_label(self):
                return self.label
            def set_visible(self, value):
                self.visible = value
            def set_tooltip_text(self, value):
                self.tooltip = value
            def get_style_context(self):
                return SimpleNamespace(add_class=lambda *_args: None)
            def __getattr__(self, _name):
                return lambda *_args: None

        app, shell = Widget(), Widget()
        app.add_action = lambda action: self.actions.update({action.name: action})
        app.set_accels_for_action = lambda name, keys: self.accels.update({name: keys})
        self.data = {"schema": 2, "state": "no_target", "target": None,
                     "incompatible": None, "candidate": None, "access_unsaved": False}
        self.requests, self.available, self.owner_available = [], True, True
        self.connections = []
        def request(dialog):
            self.requests.append(dialog)
            return self.available
        scheme = SimpleNamespace(view=Widget(), status=lambda: self.data if self.owner_available else None, request_dialog=request,
                                 close=lambda: None, closed=False, document=0, bridge_token="fixture",
                                 lifecycle_pending=None, owners=None, owner_uncertain=False, access_unsaved=False,
                                 request_lifecycle=lambda action, admit: self.requests.append(action) or False)
        self.scheme, self.shell = scheme, shell
        self.labels = lambda: [w.label for w in widgets if w.visible and w.label]
        def button(label, visible=True):
            found = [w for w in widgets if w.label == label and (w.visible or not visible) and "clicked" in w.signals]
            test.assertTrue(found, f"missing content action {label}: {self.labels()}")
            return found[0]
        self.button = button
        self.click = lambda label, visible=True: button(label, visible).signals["clicked"](button(label, visible))
        self.refresh = lambda: [callback() for callback in timers]
        def drain():
            while not callbacks.empty():
                callback, args = callbacks.get_nowait()
                callback(*args)
        self.drain = drain
        def run(_args):
            app.signals["activate"](app)
            for _ in range(50):
                try:
                    callback, args = callbacks.get(timeout=0.1)
                except queue.Empty:
                    continue
                callback(*args)
                if any("load-failed" in w.signals for w in widgets) or any(
                        "packaged web interface" in w.label or "打包的网页界面" in w.label for w in widgets):
                    break
            try:
                self.assertEqual(self.titlebars, [], "Main must use the plain system title bar")
                self.refresh()
                check()
                self.assertEqual(lifecycle, ["launch"], "dialog entry cannot start/restart service")
            finally:
                self.response = 1
                shell.signals["delete-event"]()
            return 0
        app.run = run
        gi, repository = ModuleType("gi"), ModuleType("gi.repository")
        gi.require_version = lambda *_args: None
        def action(name, _parameter):
            result = Widget()
            result.name = name
            return result
        repository.Gio = SimpleNamespace(ApplicationFlags=SimpleNamespace(FLAGS_NONE=0),
            Cancellable=Gio.Cancellable,
            SimpleAction=SimpleNamespace(new=action))
        repository.GLib = SimpleNamespace(set_prgname=lambda *_args: None, PRIORITY_DEFAULT=0,
            unix_signal_add=lambda *_args: None, source_remove=lambda *_args: None,
            timeout_add=lambda _ms, callback: timers.append(callback) or len(timers),
            idle_add=lambda callback, *args: callbacks.put((callback, args)))
        self.dialogs, self.response = [], 0
        class Dialog(Widget):
            def add_buttons(self, *buttons):
                self.buttons = buttons
            def set_default_response(self, response):
                self.default = response
            def run(self):
                self.signals["ran"] = True
                test.dialogs.append(self)
                return test.response
        repository.Gtk = SimpleNamespace(Application=lambda **_kwargs: app,
            ApplicationWindow=lambda **_kwargs: shell, HeaderBar=Widget, Box=Widget,
            Label=Widget, Button=Widget, Separator=Widget, Switch=Widget,
            MessageDialog=Dialog, MessageType=SimpleNamespace(WARNING=0),
            ButtonsType=SimpleNamespace(NONE=0, CLOSE=1), ResponseType=SimpleNamespace(CANCEL=0, OK=1),
            Orientation=SimpleNamespace(VERTICAL=0, HORIZONTAL=1), Align=SimpleNamespace(CENTER=0))
        repository.WebKit2 = SimpleNamespace(WebView=Widget)
        repository.Pango = SimpleNamespace(EllipsizeMode=SimpleNamespace(END=3))
        lifecycle = []
        def start(*_args, **_kwargs):
            lifecycle.append("launch")
            return "http://127.0.0.1:43127/"
        service = SimpleNamespace(start=start,
            shutdown_pending=False, attached=False, lifetime_state=lambda: "off", update_installed=lambda _prefix: False)
        fixture = ModuleType("header_fixture")
        fixture.scheme = scheme
        fixture.attempts = self.attempts
        fixture.connections = self.connections
        fixture.ClientUI = lambda *_args: SimpleNamespace(model=SimpleNamespace(saving=False, storage_busy=False, startup_selection_saved=True),
            attach_main=lambda *_args: None, shutdown=lambda: None)
        with tempfile.TemporaryDirectory() as scratch:
            prefix = Path(scratch)
            config = prefix / "config"
            if saved_locale is not None:
                (config / "happyranch").mkdir(parents=True)
                (config / "happyranch/locale").write_text(saved_locale + "\n")
            spec = importlib.util.spec_from_file_location("header_locale", Path(__file__).with_name("app_locale.py"))
            store = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(store)
            installed = prefix / "share/happyranch/desktop"
            installed.mkdir(parents=True)
            for name in ("host_bridge.py", "connection_contract.py"):
                shutil.copy2(Path(__file__).with_name(name), installed / name)
            shutil.copy2(Path(__file__).parents[3] / "protocol/native-connection-bridge.v1.json", installed)
            (installed / "app_scheme_webkit.py").write_text(
                "from header_fixture import scheme, attempts\nORIGIN='happyranch-app://app'\n"
                + ("def AppScheme(*args, **kwargs):\n attempts.append(args)\n raise ValueError('unavailable')\n" if scheme_failure else
                   "def AppScheme(*args, **kwargs):\n attempts.append(args)\n return scheme\n"))
            (installed / "client_ui.py").write_text(
                "from header_fixture import ClientUI\n")
            # Regression canary: any revived native fallback is observable.
            (installed / "connection_window.py").write_text(
                "from header_fixture import ClientUI, connections\n"
                "from types import SimpleNamespace\n"
                "class ConnectionWindow:\n"
                " def __init__(self,*args):\n"
                "  self.closed=False\n"
                "  self.window=SimpleNamespace(present=lambda: None)\n"
                "  connections.append(self)\n"
                " def close(self): self.closed=True\n")
            with patch.dict(sys.modules, {"gi": gi, "gi.repository": repository, "header_fixture": fixture}), \
                    patch.dict(os.environ, {"LANG": "zh_CN.UTF-8" if (chinese if system_chinese is None else system_chinese) else "en_US.UTF-8",
                                           "XDG_CONFIG_HOME": str(config),
                                           "HAPPYRANCH_DESKTOP_APP_SCHEME": "1" if app_scheme else ""}), \
                    patch.object(native, "_locale", store, create=True), \
                    patch.object(native, "RemoteAccessPanel", return_value=SimpleNamespace(box=Widget(), close=lambda: None)), \
                    patch.object(native, "ServiceMode", return_value=service), \
                    patch.object(native, "_startup", SimpleNamespace(load=lambda: {"current": "local"}, current=lambda: {"persisted": True})), \
                    patch.object(native.LocalControls, "observe", return_value={"running": "stopped", "keepRunning": "off"}), \
                    patch.object(native, "confirm_lifecycle", return_value=True), \
                    patch.object(native, "acquire_lock", return_value=io.BytesIO()):
                self.assertEqual(native.run_gui(prefix, prefix / "home"), 0)

    def test_saved_locale_controls_native_fallback_in_fresh_process(self):
        for locale, chinese in (("zh-CN", True), ("en", False)):
            with self.subTest(locale=locale):
                error = "无法加载打包的网页界面。" if chinese else "The packaged web interface could not load."
                code = ("from test_main_header import HeaderTests; case=HeaderTests(); "
                        f"case.exercise(lambda: case.assertTrue(any({error!r} in label for label in case.labels())), "
                        f"chinese={chinese!r}, scheme_failure=True, "
                        f"saved_locale={locale!r}, system_chinese={not chinese!r})")
                result = subprocess.run([sys.executable, "-B", "-c", code],
                    cwd=Path(__file__).parent, env=os.environ.copy(), capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_main_has_no_native_header_controls_in_any_target_state(self):
        for chinese in (False, True):
            def check():
                for state in ("no_target", "target_starting", "ready", "unreachable", "busy", "incompatible"):
                    self.data.update(state=state, access_unsaved=True,
                                     candidate={"name": "Host", "phase": "awaiting_approval"})
                    self.refresh()
                    self.assertEqual(self.titlebars, [])
                    for label in ("Connection", "Settings", "Quit", "Relaunch", "Exit anyway…",
                                  "连接", "设置", "退出", "重新启动", "仍要退出…"):
                        self.assertNotIn(label, self.labels())
                self.assertEqual(self.requests, [])
            self.exercise(check, chinese)

    def test_close_and_ctrl_q_keep_native_lifecycle_confirmation(self):
        def check():
            self.assertEqual(self.accels["app.quit"], ["<Primary>q"])
            self.actions["quit"].signals["activate"]()
            self.assertEqual(self.requests, ["quit"])
            self.shell.signals["delete-event"]()
            self.assertEqual(self.requests, ["quit", "close"])
            self.assertTrue(self.dialogs, "held owners must show a native prompt")
            self.assertEqual(self.dialogs[-1].buttons, ("Stay", 0, "Exit anyway…", 1))
            self.assertEqual(self.dialogs[-1].default, 0)
        self.exercise(check)

    def test_document_load_failure_retry_reloads_only_the_document(self):
        for chinese in (False, True):
            def check():
                self.scheme.view.signals["load-failed"](self.scheme.view, None, None, ValueError("fixture load failure"))
                self.drain()
                self.assertTrue(any("fixture load failure" in label for label in self.labels()))
                self.click("重试" if chinese else "Retry")
                self.assertEqual(self.loaded_uris, ["happyranch-app://app/"] * 2)
                self.assertEqual(self.requests, [])
            self.exercise(check, chinese)

    def test_main_uses_packaged_document_in_both_flag_environments(self):
        for app_scheme in (False, True):
            def check():
                self.assertEqual(self.loaded_uris, ["happyranch-app://app/"])
                self.assertEqual(self.connections, [])
                self.assertEqual(self.requests, [])
            self.exercise(check, app_scheme=app_scheme)

    def test_scheme_load_failure_has_error_and_document_retry(self):
        for chinese in (False, True):
            def check():
                self.assertEqual(self.loaded_uris, [])
                error = "无法加载打包的网页界面。" if chinese else "The packaged web interface could not load."
                self.assertTrue(any(error in label for label in self.labels()), self.labels())
                self.click("重试" if chinese else "Retry")
                self.assertEqual(self.loaded_uris, [], "missing assets remain a native error")
                self.assertEqual(len(self.attempts), 2, "Retry must attempt to load assets again")
                self.assertEqual(self.requests, [])
            self.exercise(check, chinese, scheme_failure=True)


if __name__ == "__main__":
    unittest.main()

"""Headless header boundary checks: shipped run_gui, synthetic GTK and status owner."""
from __future__ import annotations

import io
import os
from pathlib import Path
import queue
import tempfile
from types import ModuleType, SimpleNamespace
import sys
import unittest
from unittest.mock import patch

import happyranch_window as native


class HeaderTests(unittest.TestCase):
    def exercise(self, check, chinese=False):
        callbacks, timers, widgets = queue.Queue(), [], []
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
            def get_children(self):
                return self.children[:]
            def remove(self, child):
                self.children.remove(child)
            def set_label(self, value):
                self.label = value
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
        self.data = {"schema": 2, "state": "no_target", "target": None,
                     "incompatible": None, "candidate": None, "access_unsaved": False}
        self.requests, self.available, self.owner_available = [], True, True
        def request(dialog):
            self.requests.append(dialog)
            return self.available
        scheme = SimpleNamespace(view=Widget(), status=lambda: self.data if self.owner_available else None, request_dialog=request,
                                 close=lambda: None)
        self.labels = lambda: [w.label for w in widgets if w.visible and w.label]
        def button(label):
            found = [w for w in widgets if w.label == label and w.visible and "clicked" in w.signals]
            test.assertEqual(len(found), 1, f"missing header action {label}: {self.labels()}")
            return found[0]
        self.button = button
        self.click = lambda label: button(label).signals["clicked"](button(label))
        self.refresh = lambda: [callback() for callback in timers]
        def run(_args):
            app.signals["activate"](app)
            for _ in range(50):
                try:
                    callback, args = callbacks.get(timeout=0.1)
                except queue.Empty:
                    continue
                callback(*args)
                if scheme.view in [c for w in widgets for c in w.children]:
                    break
            try:
                self.assertIn("设置" if chinese else "Settings", self.labels(), "Main header needs both named dialog actions")
                self.refresh()
                check()
                self.assertEqual(lifecycle, ["launch"], "dialog entry cannot start/restart service")
            finally:
                shell.signals["delete-event"]()
            return 0
        app.run = run
        gi, repository = ModuleType("gi"), ModuleType("gi.repository")
        gi.require_version = lambda *_args: None
        repository.Gio = SimpleNamespace(ApplicationFlags=SimpleNamespace(FLAGS_NONE=0),
            SimpleAction=SimpleNamespace(new=lambda *_args: Widget()))
        repository.GLib = SimpleNamespace(set_prgname=lambda *_args: None, PRIORITY_DEFAULT=0,
            unix_signal_add=lambda *_args: None, source_remove=lambda *_args: None,
            timeout_add=lambda _ms, callback: timers.append(callback) or len(timers),
            idle_add=lambda callback, *args: callbacks.put((callback, args)))
        repository.Gtk = SimpleNamespace(Application=lambda **_kwargs: app,
            ApplicationWindow=lambda **_kwargs: shell, HeaderBar=Widget, Box=Widget,
            Label=Widget, Button=Widget, Separator=Widget, Switch=Widget,
            Orientation=SimpleNamespace(VERTICAL=0, HORIZONTAL=1), Align=SimpleNamespace(CENTER=0))
        repository.WebKit2 = SimpleNamespace(WebView=Widget)
        repository.Pango = SimpleNamespace(EllipsizeMode=SimpleNamespace(END=3))
        lifecycle = []
        def start(*_args, **_kwargs):
            lifecycle.append("launch")
            return "http://127.0.0.1:43127/"
        service = SimpleNamespace(start=start,
            shutdown_pending=False, lifetime_state=lambda: "off", update_installed=lambda _prefix: False)
        fixture = ModuleType("header_fixture")
        fixture.scheme = scheme
        with tempfile.TemporaryDirectory() as scratch:
            prefix = Path(scratch)
            installed = prefix / "share/happyranch/desktop"
            installed.mkdir(parents=True)
            (installed / "app_scheme_webkit.py").write_text(
                "from header_fixture import scheme\nORIGIN='happyranch-app://app'\n"
                "def AppScheme(*args, **kwargs): return scheme\n")
            (installed / "client_ui.py").write_text(
                "class ClientUI:\n def __init__(self,*args): pass\n"
                " def attach_main(self,*args): pass\n def shutdown(self): pass\n")
            with patch.dict(sys.modules, {"gi": gi, "gi.repository": repository, "header_fixture": fixture}), \
                    patch.dict(os.environ, {"LANG": "zh_CN.UTF-8" if chinese else "en_US.UTF-8",
                                           "HAPPYRANCH_DESKTOP_APP_SCHEME": "1"}), \
                    patch.object(native, "RemoteAccessPanel", return_value=SimpleNamespace(box=Widget(), close=lambda: None)), \
                    patch.object(native, "ServiceMode", return_value=service), \
                    patch.object(native, "acquire_lock", return_value=io.BytesIO()):
                self.assertEqual(native.run_gui(prefix, prefix / "home"), 0)

    def test_pill_states_and_target_identity_in_both_locales(self):
        for chinese in (False, True):
            with self.subTest(chinese=chinese):
                def check():
                    self.assertIn("尚未选择连接" if chinese else "No connection selected", self.labels())
                    for kind, name in (("local", "This computer"), ("remote", "Studio <Host>")):
                        self.data["target"] = {"kind": kind, "id": "local" if kind == "local" else "saved-1", "name": name}
                        for state, en, zh in (("target_starting", "Starting", "正在启动"),
                                ("ready", "Connected", "已连接"), ("unreachable", "Unavailable", "无法连接"),
                                ("busy", "Busy", "忙碌")):
                            self.data["state"] = state
                            self.refresh()
                            identity = ("此电脑" if chinese else "This computer") if kind == "local" else name
                            self.assertIn(f"{identity} · {zh if chinese else en}", self.labels())
                        self.data["state"] = "incompatible"
                        for side, en, zh in (("host", "Update Host", "更新主机"),
                                            ("app", "Update app", "更新应用"),
                                            ("unknown", "Check compatibility", "检查兼容性")):
                            self.data["incompatible"] = {"side": side}
                            self.refresh()
                            self.assertTrue(any((zh if chinese else en) in label for label in self.labels()))
                self.exercise(check, chinese)

    def test_candidate_and_unsaved_badge_survive_target_loss(self):
        for chinese in (False, True):
            with self.subTest(chinese=chinese):
                def check():
                    badge = "访问权限未保存" if chinese else "Access wasn't saved"
                    self.data["access_unsaved"] = True
                    self.data["candidate"] = {"name": "Work Host", "phase": "awaiting_approval"}
                    self.refresh()
                    self.assertIn(badge, self.labels())
                    self.assertTrue(any("Work Host" in label for label in self.labels()))
                    self.click(badge)
                    self.assertEqual(self.requests, ["connection"])
                    self.data.update(target=None, candidate=None, state="no_target")
                    self.refresh()
                    self.assertIn(badge, self.labels())
                    self.owner_available = False
                    self.refresh()
                    self.assertIn(badge, self.labels(), "document loss cannot confirm a save")
                    self.owner_available = True
                    self.data["access_unsaved"] = False
                    self.refresh()
                    self.assertNotIn(badge, self.labels())
                self.exercise(check, chinese)

    def test_header_entry_has_no_lifecycle_or_target_effect_and_manual_retry(self):
        for chinese in (False, True):
            with self.subTest(chinese=chinese):
                def check():
                    connection, settings = ("连接", "设置") if chinese else ("Connection", "Settings")
                    retry = "设置暂不可用 · 重试" if chinese else "Settings unavailable · Retry"
                    self.click(connection)
                    self.click(settings)
                    self.assertEqual(self.requests, ["connection", "settings"])
                    original = dict(self.data)
                    self.available = False
                    self.click(settings)
                    self.assertIn(retry, self.labels())
                    self.available = True
                    self.refresh()
                    self.assertEqual(self.requests, ["connection", "settings", "settings"], "no automatic replay")
                    self.click(retry)
                    self.assertEqual(self.requests[-1], "settings")
                    self.assertNotIn(retry, self.labels())
                    self.assertEqual(self.data, original)
                self.exercise(check, chinese)


if __name__ == "__main__":
    unittest.main()

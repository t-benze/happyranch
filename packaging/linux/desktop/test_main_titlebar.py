"""Real-GTK shipping-launcher regression; run in a disposable Xvfb session."""
from __future__ import annotations

import json
import os
from pathlib import Path
from collections.abc import Iterator
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from types import ModuleType
import unittest
from unittest.mock import patch


class TitlebarTests(unittest.TestCase):
    def test_titlebar_survives_startup_recovery_and_main_recreation(self) -> None:
        # Fresh processes recreate Main and keep GTK/WebKit global state isolated.
        for locale in ("en_US.UTF-8", "zh_CN.UTF-8"):
            with self.subTest(locale=locale), tempfile.TemporaryDirectory() as scratch:
                root = Path(scratch)
                env = {k: v for k, v in os.environ.items() if not k.startswith("HAPPYRANCH_")}
                for key in ("HAPPYRANCH_TITLEBAR_WEB_DIST", "HAPPYRANCH_TITLEBAR_OUTPUT"):
                    if key in os.environ:
                        env[key] = os.environ[key]
                env.update(HOME=str(root), XDG_CONFIG_HOME=str(root / "config"),
                           XDG_CACHE_HOME=str(root / "cache"), XDG_DATA_HOME=str(root / "data"),
                           LANG=locale, GSETTINGS_BACKEND="memory", GDK_BACKEND="x11",
                           PYTHONFAULTHANDLER="1")
                result = subprocess.run([sys.executable, "-B", __file__, "--probe", str(root)],
                                        env=env, text=True, capture_output=True, timeout=45)
                print(result.stdout, end="")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


def probe(root: Path) -> int:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("WebKit2", "4.1")
    from gi.repository import Gdk, GLib, Gtk
    import happyranch_window as native

    repo = Path(__file__).resolve().parents[3]
    installed = root / "prefix/share/happyranch/desktop"
    installed.mkdir(parents=True)
    shutil.copytree(Path(__file__).parent, installed, dirs_exist_ok=True)
    shutil.copy2(repo / "runtime/remote_access/invite_v2.py", installed / "invite_v2.py")
    bundle = root / "prefix/web/dist"
    source = os.environ.get("HAPPYRANCH_TITLEBAR_WEB_DIST")
    if source:
        shutil.copytree(source, bundle)
    else:
        bundle.mkdir(parents=True)
        (bundle / "index.html").write_text("<!doctype html><title>Titlebar content fixture</title>")
    holder = ModuleType("titlebar_fixture")
    holder.schemes = []
    sys.modules[holder.__name__] = holder
    # As in the existing native launcher venue, capture the installed adapter;
    # all window/content operations and AppScheme implementation remain shipping.
    (installed / "app_scheme_webkit.py").write_text(
        "import app_scheme_webkit as shipping\nimport titlebar_fixture as fixture\n"
        "ORIGIN=shipping.ORIGIN\ndef AppScheme(*args, **kwargs):\n"
        " scheme=shipping.AppScheme(*args, **kwargs)\n"
        " scheme.select_target(None, owner_saves=())\n"
        " fixture.schemes.append(scheme)\n return scheme\n")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    calls, observations, errors = [], [], []

    class Lifecycle:
        shutdown_pending = False
        update = True

        def __init__(self, home: Path) -> None:
            self.home = home

        def start(self, *_args: object, **kwargs: object) -> str:
            calls.append("retry" if kwargs.get("retry") else "start")
            return f"http://127.0.0.1:{port}/"

        def lifetime_state(self) -> str:
            return "off"

        def update_installed(self, _prefix: Path) -> bool:
            return self.update

        def restart(self, *_args: object) -> str:
            calls.append("restart")
            self.update = False
            raise native.LaunchError("Isolated restart failure", "retry")

    def descendants(widget: object) -> Iterator[object]:
        yield widget
        if isinstance(widget, Gtk.Container):
            for child in widget.get_children():
                yield from descendants(child)

    titlebar = None
    original_add = Gtk.ApplicationWindow.add

    def observe_add(window: object, content: object) -> None:
        nonlocal titlebar
        original_add(window, content)
        current = window.get_titlebar()
        if titlebar is None:
            titlebar = current
        buttons = [w.get_label() for w in descendants(current) if isinstance(w, Gtk.Button)]
        chinese = os.environ["LANG"].startswith("zh")
        expected = ["连接", "设置"] if chinese else ["Connection", "Settings"]
        record = {"titlebar_present": current is not None, "same_titlebar": current is titlebar,
                  "buttons": buttons, "children": [type(w).__name__ for w in window.get_children()],
                  "content_labels": [w.get_text() for w in descendants(content) if isinstance(w, Gtk.Label)]}
        observations.append(record)
        print("TITLEBAR " + json.dumps(record, ensure_ascii=False), flush=True)
        if current is None or current is not titlebar or not all(b in buttons for b in expected):
            errors.append("Main lost its titlebar during content replacement: " + json.dumps(record))

    stage, deadline = 0, time.monotonic() + 30

    def drive() -> bool:
        nonlocal stage
        app = Gtk.Application.get_default()
        try:
            if time.monotonic() > deadline:
                raise AssertionError(f"launcher stalled at stage {stage}")
            windows = [w for w in Gtk.Window.list_toplevels() if isinstance(w, Gtk.ApplicationWindow)]
            if not windows:
                return True
            window = windows[0]
            content = window.get_child()
            buttons = {w.get_label(): w for w in descendants(content) if isinstance(w, Gtk.Button)}
            chinese = os.environ["LANG"].startswith("zh")
            restart, retry = ("重启 HappyRanch", "重试") if chinese else ("Restart HappyRanch", "Retry")
            if stage == 0 and restart in buttons:
                stage = 1
                buttons[restart].clicked()
            elif stage == 1 and retry in buttons:
                if not isinstance(buttons[retry].get_parent(), Gtk.Box):
                    raise AssertionError("Retry must belong to recovery content")
                stage = 2
                buttons[retry].clicked()
            elif stage == 2 and len(holder.schemes) == 2 and holder.schemes[-1].view.get_mapped():
                scheme = holder.schemes[-1]
                if source and not scheme.dialog_subscribed:
                    return True
                labels = [w.get_text() for w in descendants(content) if isinstance(w, Gtk.Label)]
                strip = "退出登录后保持 HappyRanch 运行" if chinese else "Keep HappyRanch running when I'm logged out"
                if strip not in labels:
                    return True
                if not any(isinstance(w, Gtk.Switch) for w in descendants(content)):
                    raise AssertionError("Main strip lost its switch")
                if not any(isinstance(w, Gtk.Separator) for w in descendants(content)):
                    raise AssertionError("Main strip lost its separator")
                output = os.environ.get("HAPPYRANCH_TITLEBAR_OUTPUT")
                if output:
                    window.resize(760, 560)
                    # Capture on a later tick after GTK allocates the requested size.
                    if window.get_size().width != 760:
                        return True
                    pix = Gdk.pixbuf_get_from_window(window.get_window(), 0, 0, *window.get_size())
                    pix.savev(str(Path(output) / ("shipping-main-zh-760.png" if chinese else "shipping-main-en-760.png")), "png", [], [])
                stage = 3
                if chinese:
                    app.lookup_action("quit").activate(None)
                else:
                    window.emit("delete-event", None)
                return False
        except Exception as error:
            errors.append(str(error))
            app.quit()
            return False
        return True

    GLib.timeout_add(100, drive)
    with patch.object(native, "ServiceMode", Lifecycle), \
            patch.object(native.RemoteAccessPanel, "refresh", lambda self: None), \
            patch.object(Gtk.ApplicationWindow, "add", observe_add), \
            patch.dict(os.environ, HAPPYRANCH_DESKTOP_APP_SCHEME="1"):
        result = native.run_gui(root / "prefix", root / "daemon")
    if stage != 3 or calls != ["start", "restart", "retry"]:
        errors.append(f"incomplete transitions: stage={stage}, calls={calls}, content replacements={len(observations)}")
    record = {"locale": os.environ["LANG"], "exit": result, "calls": calls,
              "observations": observations, "errors": errors, "host_lifecycle": "isolated"}
    print(json.dumps(record, ensure_ascii=False))
    output = os.environ.get("HAPPYRANCH_TITLEBAR_OUTPUT")
    if output:
        (Path(output) / ("launcher-zh.json" if os.environ["LANG"].startswith("zh") else "launcher-en.json")).write_text(json.dumps(record, ensure_ascii=False, indent=2))
    return int(bool(errors) or result != 0)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--probe":
        raise SystemExit(probe(Path(sys.argv[2])))
    unittest.main()

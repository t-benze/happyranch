"""Separate asset-only GTK connection window; never attached to runtime views."""
from __future__ import annotations

import argparse
import importlib.util
import json
import mimetypes
from pathlib import Path
import re
import tempfile
from typing import Callable

# The desktop is launched with system Python -I. Resolve this installed sibling
# explicitly instead of accepting PYTHONPATH or the process working directory.
_spec = importlib.util.spec_from_file_location("connection_contract", Path(__file__).with_name("connection_contract.py"))
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
Assets, Protocol = _contract.Assets, _contract.Protocol
ORIGIN, ENTRY, CSP = _contract.ORIGIN, _contract.ENTRY, _contract.CSP
WORLD, CHANNEL = "happyranch.connection.v1", "connection"

# Fixed code only. Native data returns through JSC strings or GVariant arguments.
PAGE = r"""(() => {
  'use strict';
  if (window !== top || location.href !== 'happyranch-shell://connection/index.html'
      || location.origin !== 'happyranch-shell://connection') return;
  const fail = code => JSON.stringify({code, effect:'none', recovery:'refresh'});
  const pending = new Map(), listeners = new Map();
  let sequence = 0, active = true;
  const size = text => typeof text === 'string' && new TextEncoder().encode(text).length;
  addEventListener('message', event => {
    if (!active || event.source !== window || event.origin !== location.origin) return;
    const item = event.data;
    if (!item || item.channel !== 'hr-connection-reply' || typeof item.id !== 'string') return;
    const callback = pending.get(item.id);
    if (!callback) return;
    pending.delete(item.id);
    try {
      if (!size(item.raw) || size(item.raw) > 32768) throw 0;
      const reply = JSON.parse(item.raw);
      if (reply.protocol !== 'happyranch.connection' || reply.version !== 1
          || reply.requestId !== item.id || typeof reply.ok !== 'boolean') throw 0;
      if (reply.ok) callback.resolve(JSON.stringify(reply.result));
      else callback.reject(JSON.stringify(reply.error));
    } catch { callback.reject(fail('invalidRequest')); }
  });
  const call = (command, data) => new Promise((resolve, reject) => {
    if (!active) return reject(fail('documentChanged'));
    if (typeof command !== 'string' || typeof data !== 'string' || size(data) > 4096)
      return reject(fail('invalidRequest'));
    if (pending.size >= 8) return reject(fail('busy'));
    const id = 'r' + (++sequence);
    pending.set(id, {resolve, reject});
    window.postMessage({channel:'hr-connection-call', id, command, data}, location.origin);
  });
  const on = (name, handler) => {
    if (!['host.stateChanged','client.stateChanged'].includes(name) || typeof handler !== 'function')
      throw fail('unsupportedCommand');
    if (!active) throw fail('documentChanged');
    if (!listeners.has(name)) listeners.set(name, new Set());
    const group = listeners.get(name); group.add(handler);
    return () => group.delete(handler);
  };
  addEventListener('pagehide', () => {
    active = false; listeners.clear();
    pending.forEach(item => item.reject(fail('documentChanged'))); pending.clear();
  }, {once:true});
  Object.defineProperty(window, 'happyranchConnection', {value:Object.freeze({call,on})});
})();"""

RELAY = r"""(() => {
  'use strict';
  if (window !== top || location.href !== 'happyranch-shell://connection/index.html'
      || location.origin !== 'happyranch-shell://connection') return;
  const native = window.webkit.messageHandlers.connection;
  addEventListener('message', async event => {
    if (event.source !== window || event.origin !== 'happyranch-shell://connection') return;
    const item = event.data;
    if (!item || item.channel !== 'hr-connection-call' || typeof item.data !== 'string'
        || typeof item.command !== 'string' || !/^r[0-9]{1,16}$/.test(item.id)) return;
    // Keep args text intact: native must see duplicate keys and bad JSON too.
    const raw = JSON.stringify({protocol:'happyranch.connection',version:1,
      requestId:item.id,command:item.command}).slice(0,-1) + ',"args":' + item.data + '}';
    let reply;
    try { reply = await native.postMessage(raw); }
    catch { reply = JSON.stringify({protocol:'happyranch.connection',version:1,
      requestId:item.id,ok:false,error:{code:'unavailable',effect:'none',recovery:'refresh'}}); }
    if (typeof reply === 'string' && new TextEncoder().encode(reply).length <= 32768)
      window.postMessage({channel:'hr-connection-reply',id:item.id,raw:reply}, location.origin);
  });
})();"""

# Admission runs in the named world without the facade. Page-world builtins
# cannot forge this probe. A child able to access a parent function
# could call the future parent facade, defeating a source===window relay check.
# Test this reachability, not a JS-supplied isTop field. A failed probe never
# enables even read-only calls. This deliberately includes initial about:blank.
FRAME_PROBE = r"""() => {
  if (window !== top || location.origin !== 'happyranch-shell://connection'
      || location.href !== 'happyranch-shell://connection/index.html'
      || typeof window.webkit?.messageHandlers?.connection?.postMessage !== 'function'
      || window.frames.length !== 0) return false;
  const marker = () => true;
  Object.defineProperty(window, '__hrFrameProbe', {value:marker, configurable:true});
  let isolated = true;
  for (const src of [null, 'about:blank', 'happyranch-shell://connection/index.html']) {
    const frame = document.createElement('iframe');
    if (src !== null) frame.src = src;
    document.body.append(frame);
    try { if (frame.contentWindow?.parent.__hrFrameProbe() === true) isolated = false; }
    catch { /* An inaccessible child is safe for this case. */ }
    frame.remove();
  }
  const frame = document.createElement('iframe'); frame.srcdoc = '<p>probe</p>';
  document.body.append(frame);
  try { if (frame.contentWindow?.parent.__hrFrameProbe() === true) isolated = false; } catch {}
  frame.remove(); delete window.__hrFrameProbe;
  return isolated && window.frames.length === 0;
}"""


class ConnectionWindow:
    def __init__(self, app: object, parent: object, prefix: Path,
                 tr: Callable[[str, str], str]) -> None:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("WebKit2", "4.1")
        gi.require_version("Soup", "3.0")
        gi.require_version("JavaScriptCore", "4.1")
        from gi.repository import Gio, GLib, Gtk, JavaScriptCore, Soup, WebKit2
        self.Gio, self.GLib, self.Gtk, self.JSC, self.Soup, self.W = Gio, GLib, Gtk, JavaScriptCore, Soup, WebKit2
        self.tr, self.prefix = tr, prefix
        self.window = Gtk.ApplicationWindow(application=app, title=tr("Connection", "连接"))
        if parent is not None:
            self.window.set_transient_for(parent)
        self.window.set_default_size(720, 640)
        self.window.connect("destroy", lambda *_args: self.close())
        self.view = self.manager = self.connection = None
        self.cancellable = Gio.Cancellable()
        self.generation, self.active, self.closed, self.busy = 1, False, False, False
        self.registered, self.probing = False, True
        self.filter_dir = None
        self.unavailable_reason = None
        self.initial_navigation = True
        self.initial_load = True
        self.seen = set()
        try:
            self.assets = Assets(prefix / "share/happyranch/desktop/connection")
            self.protocol = Protocol(prefix / "share/happyranch/desktop/native-connection-bridge.v1.json")
            self.context = WebKit2.WebContext.new_ephemeral()
            security = self.context.get_security_manager()
            for name in ("register_uri_scheme_as_secure", "register_uri_scheme_as_cors_enabled",
                         "register_uri_scheme_as_display_isolated"):
                getattr(security, name)("happyranch-shell")
            self.context.register_uri_scheme("happyranch-shell", self.scheme, None)
            self.manager = WebKit2.UserContentManager()
            # Presence is checked by actually constructing/calling each API.
            if not callable(getattr(WebKit2.URISchemeResponse, "set_http_headers", None)):
                raise ValueError("CSP headers unavailable")
            self.view = WebKit2.WebView(web_context=self.context, user_content_manager=self.manager)
            if not callable(getattr(self.view, "call_async_javascript_function", None)):
                raise ValueError("structured delivery unavailable")
            self.reply_handler = self.manager.connect("script-message-with-reply-received::connection", self.message)
            if not self.manager.register_script_message_handler_with_reply(CHANNEL, WORLD):
                raise ValueError("named reply handler unavailable")
            self.registered = True
            settings = self.view.get_settings()
            settings.set_property("javascript-can-open-windows-automatically", False)
            settings.set_property("allow-file-access-from-file-urls", False)
            settings.set_property("allow-universal-access-from-file-urls", False)
            settings.set_property("enable-page-cache", False)
            self.view.connect("decide-policy", self.policy)
            self.view.connect("create", self.popup)
            self.view.connect("permission-request", lambda _view, request: (request.deny(), True)[1])
            self.view.connect("load-changed", self.loaded)
            self.view.connect("load-failed", self.load_failed)
            self.view.connect("web-process-terminated", lambda view, *_args:
                              self.unavailable("process") if view == self.view else None)
            self.filter_dir = tempfile.TemporaryDirectory(prefix="happyranch-connection-filter-")
            self.store = WebKit2.UserContentFilterStore.new(self.filter_dir.name)
            rules = [{"trigger": {"url-filter": ".*"}, "action": {"type": "block"}}]
            for path in self.assets.resources:
                rules.append({"trigger": {"url-filter": "^" + re.escape(ORIGIN + "/" + path) + "$"},
                              "action": {"type": "ignore-previous-rules"}})
            self.store.save("connection", GLib.Bytes.new(json.dumps(rules).encode()),
                            self.cancellable, self.filtered, None)
            GLib.timeout_add_seconds(5, self.admission_timeout)
        except Exception:
            self.unavailable("capability")
        self.window.show_all()

    def admission_timeout(self) -> bool:
        if not self.closed and (self.probing or not self.seen):
            self.unavailable("admission timeout")
        return False

    def filtered(self, store: object, result: object, _data: object) -> None:
        try:
            content_filter = store.save_finish(result)
            if self.closed or self.unavailable_reason:
                return
            self.manager.add_filter(content_filter)
            self.content_filter = content_filter
            self.window.add(self.view)
            self.view.load_uri(ENTRY)
            self.window.show_all()
        except Exception:
            self.unavailable("network isolation")

    def scheme(self, request: object, _data: object) -> None:
        try:
            if (self.closed or self.unavailable_reason or request.get_web_view() != self.view
                    or request.get_http_method() != "GET"):
                raise ValueError("invalid asset request")
            data = self.assets.resolve(request.get_uri())
            response = self.W.URISchemeResponse.new(
                self.Gio.MemoryInputStream.new_from_bytes(self.GLib.Bytes.new(data)), len(data))
            response.set_status(200, "OK")
            response.set_content_type(mimetypes.guess_type(request.get_uri())[0] or "application/octet-stream")
            headers = self.Soup.MessageHeaders.new(self.Soup.MessageHeadersType.RESPONSE)
            headers.append("Content-Security-Policy", CSP)
            headers.append("X-Content-Type-Options", "nosniff")
            headers.append("Cache-Control", "no-store")
            response.set_http_headers(headers)
            request.finish_with_response(response)
        except Exception:
            request.finish_error(self.GLib.Error.new_literal(self.Gio.io_error_quark(),
                                 "Connection asset unavailable", self.Gio.IOErrorEnum.PERMISSION_DENIED))
            self.unavailable("asset")

    def policy(self, view: object, decision: object, kind: object) -> bool:
        if view != self.view:
            decision.ignore()
            return True
        allowed = False
        if kind == self.W.PolicyDecisionType.NAVIGATION_ACTION:
            allowed = self.initial_navigation and decision.get_request().get_uri() == ENTRY
            self.initial_navigation = False
        elif kind == self.W.PolicyDecisionType.RESPONSE:
            allowed = (decision.is_main_frame_main_resource()
                       and decision.get_request().get_uri() == ENTRY
                       and decision.get_response().get_status_code() == 200)
        if allowed:
            decision.use()
        else:
            decision.ignore()
            self.unavailable("navigation")
        return True

    def popup(self, *_args: object) -> None:
        if _args[0] == self.view:
            self.unavailable("popup")
        return None

    def load_failed(self, *_args: object) -> bool:
        if _args[0] == self.view:
            self.unavailable("load")
        return True

    def loaded(self, view: object, event: object) -> None:
        if view != self.view or self.closed or self.unavailable_reason:
            return
        if event == self.W.LoadEvent.STARTED:
            if not self.initial_load:
                self.unavailable("reload")
            self.initial_load = False
        elif event == self.W.LoadEvent.REDIRECTED:
            self.unavailable("redirect")
        elif event == self.W.LoadEvent.FINISHED and self.probing:
            self.view.call_async_javascript_function(
                "return (" + FRAME_PROBE + ")();", -1, None, WORLD, ENTRY,
                self.cancellable, self.probed, None)

    def probed(self, view: object, result: object, _data: object) -> None:
        try:
            value = view.call_async_javascript_function_finish(result)
            if view != self.view or self.closed or self.unavailable_reason:
                return
            if not value.is_boolean() or not value.to_boolean():
                raise ValueError("frame/origin isolation unproven")
            # Verify the page world has no directly registered native channel.
            # Its result alone never supplies frame/origin admission authority.
            view.call_async_javascript_function(
                "return window === top && location.origin === 'happyranch-shell://connection'"
                " && typeof window.webkit?.messageHandlers?.connection === 'undefined'"
                " && typeof window.__hrFrameProbe === 'undefined';",
                -1, None, None, ENTRY, self.cancellable, self.world_probed, None)
        except Exception:
            self.unavailable("frame/origin isolation")

    def world_probed(self, view: object, result: object, _data: object) -> None:
        try:
            value = view.call_async_javascript_function_finish(result)
            if view != self.view or self.closed or self.unavailable_reason:
                return
            if not value.is_boolean() or not value.to_boolean():
                raise ValueError("isolated world unavailable")
            # Never grant a document that has already run scripts a new bridge.
            # Make a fresh native view after admission, preserving the same
            # private context/asset policy but a fresh controller/generation.
            self.manager.unregister_script_message_handler_in_world(CHANNEL, WORLD)
            self.manager.disconnect(self.reply_handler)
            old_view = self.view
            old_view.stop_loading()
            self.window.remove(old_view)
            self.manager = self.W.UserContentManager()
            self.reply_handler = self.manager.connect("script-message-with-reply-received::connection", self.message)
            if not self.manager.register_script_message_handler_with_reply(CHANNEL, WORLD):
                raise ValueError("handler unavailable")
            self.manager.add_filter(self.content_filter)
            for source, world in ((RELAY, WORLD), (PAGE, None)):
                script = (self.W.UserScript.new_for_world(source, self.W.UserContentInjectedFrames.TOP_FRAME,
                          self.W.UserScriptInjectionTime.START, world, [ENTRY], None) if world else
                          self.W.UserScript.new(source, self.W.UserContentInjectedFrames.TOP_FRAME,
                          self.W.UserScriptInjectionTime.START, [ENTRY], None))
                self.manager.add_script(script)
            self.view = self.W.WebView(web_context=self.context, user_content_manager=self.manager)
            settings = self.view.get_settings()
            settings.set_property("javascript-can-open-windows-automatically", False)
            settings.set_property("allow-file-access-from-file-urls", False)
            settings.set_property("allow-universal-access-from-file-urls", False)
            settings.set_property("enable-page-cache", False)
            self.view.connect("decide-policy", self.policy)
            self.view.connect("create", self.popup)
            self.view.connect("permission-request", lambda _view, request: (request.deny(), True)[1])
            self.view.connect("load-changed", self.loaded)
            self.view.connect("load-failed", self.load_failed)
            self.view.connect("web-process-terminated", lambda view, *_args:
                              self.unavailable("process") if view == self.view else None)
            self.window.add(self.view)
            self.probing, self.active = False, True
            self.initial_navigation = self.initial_load = True
            self.generation += 1
            old_view.destroy()
            self.view.load_uri(ENTRY)
            self.window.show_all()
        except Exception:
            self.unavailable("named-world admission")

    def message(self, _manager: object, value: object, reply: object) -> bool:
        generation = self.generation
        try:
            if (_manager != self.manager or not self.active or self.closed
                    or self.unavailable_reason or not value.is_string()
                    or self.view.get_uri() != ENTRY):
                raise ValueError("document unavailable")
            request = self.protocol.request(value.to_string(), origin=ORIGIN, main_frame=True)
            if len(self.seen) >= 4096 or request["requestId"] in self.seen:
                raise ValueError("request lifetime bound")
            self.seen.add(request["requestId"])
        except Exception:
            reply.return_error_message('{"code":"invalidRequest","effect":"none","recovery":"refresh"}')
            return True

        def finish(result: dict | None = None, code: str | None = None) -> None:
            if not self.active or self.closed or self.generation != generation or self.view.get_uri() != ENTRY:
                return
            envelope = {"protocol": "happyranch.connection", "version": 1,
                        "requestId": request["requestId"], "ok": code is None}
            envelope.update({"error": {"code": code, "effect": "none", "recovery": "refresh"}}
                            if code else {"result": result})
            raw = json.dumps(envelope, ensure_ascii=False)
            if len(raw.encode("utf-8")) > 32768:
                finish(code="unavailable")
                return
            reply.return_value(self.JSC.Value.new_string(value.get_context(), raw))

        if request["command"] == "bridge.hello":
            finish(self.protocol.hello())
        elif request["command"] == "host.status":
            if self.busy:
                finish(code="busy")
            else:
                self.status(finish)
        else:
            finish(code="unavailable" if request["command"] == "client.status" else "unsupportedCommand")
        return True

    def status(self, finish: Callable) -> None:
        self.busy = True

        def replied(connection: object, result: object, _data: object) -> None:
            self.busy = False
            try:
                raw, = connection.call_finish(result).unpack()
                finish(self.protocol.host(raw))
            except Exception:
                finish(code="unavailable")

        def call() -> None:
            self.connection.call("ai.happyranch.RemoteAccess1", "/ai/happyranch/RemoteAccess1",
                "ai.happyranch.RemoteAccess1", "Status", None, self.GLib.VariantType.new("(s)"),
                self.Gio.DBusCallFlags.NONE, 3000, self.cancellable, replied, None)

        def connected(_source: object, result: object, _data: object) -> None:
            try:
                connection = self.Gio.DBusConnection.new_for_address_finish(result)
                connection.set_exit_on_close(False)
                if not self.active or self.closed:
                    connection.close(None, None, None)
                    return
                self.connection = connection
                call()
            except Exception:
                self.busy = False
                finish(code="unavailable")

        try:
            if self.connection is not None and not self.connection.is_closed():
                call()
            else:
                self.Gio.DBusConnection.new_for_address("unix:path=/run/dbus/system_bus_socket",
                    self.Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT |
                    self.Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
                    None, self.cancellable, connected, None)
        except Exception:
            self.busy = False
            finish(code="unavailable")

    def detach(self) -> None:
        self.active = False
        self.generation += 1
        self.cancellable.cancel()
        if self.manager is not None:
            if self.registered:
                self.manager.unregister_script_message_handler_in_world(CHANNEL, WORLD)
                self.registered = False
            self.manager.remove_all_scripts()
        if self.connection is not None:
            self.connection.close(None, None, None)
            self.connection = None

    def unavailable(self, reason: str) -> None:
        if self.closed or self.unavailable_reason:
            return
        self.unavailable_reason = reason  # Closed internal category, never backend exception text.
        self.detach()
        if self.view is not None:
            self.view.stop_loading()
        for child in self.window.get_children():
            self.window.remove(child)
        label = self.Gtk.Label(label=self.tr(
            "Connection controls are unavailable in this build or web engine. "
            "The connection command-line fallback is not available yet.",
            "此版本或网页引擎暂不支持连接控制。连接命令行替代功能尚未提供。"))
        label.set_line_wrap(True)
        label.set_margin_start(24)
        label.set_margin_end(24)
        self.window.add(label)
        self.window.show_all()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.detach()
        if self.view is not None:
            self.view.destroy()
        if self.filter_dir is not None:
            self.filter_dir.cleanup()


def main() -> int:
    """Standalone packaged view: no daemon startup, service or helper mutation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", required=True, type=Path)
    parser.add_argument("--locale", choices=("en", "zh-CN"), default="en")
    args = parser.parse_args()
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    app = Gtk.Application(application_id="org.happyranch.Connection")
    windows = []
    def activate(_app: object) -> None:
        windows.append(ConnectionWindow(app, None, args.prefix,
                       lambda en, zh: zh if args.locale == "zh-CN" else en))
    app.connect("activate", activate)
    return app.run([])


if __name__ == "__main__":
    raise SystemExit(main())

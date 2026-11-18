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
_spec = importlib.util.spec_from_file_location("client_ui", Path(__file__).with_name("client_ui.py"))
_client = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_client)
ClientUI = _client.ClientUI

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
    if (item?.channel === 'hr-connection-event' && item.name === 'client.stateChanged'
        && typeof item.raw === 'string' && size(item.raw) <= 32768) {
      const group = listeners.get(item.name);
      if (group) group.forEach(handler => handler(item.raw));
      return;
    }
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
    window.postMessage({channel:'hr-connection-subscribe',name,enabled:true}, location.origin);
    let subscribed = true;
    return () => {
      if (!subscribed) return; subscribed = false; group.delete(handler);
      if (!group.size) window.postMessage({channel:'hr-connection-subscribe',name,enabled:false}, location.origin);
    };
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
  let active = true;
  const revoke = () => {
    if (!active) return;
    active = false;
    // Non-string input is always rejected. This notification can only revoke
    // the current document, never authorize a command or supply frame identity.
    native.postMessage(null).catch(() => {});
  };
  const embedded = node => node.nodeType === 1 &&
    (node.matches('iframe,frame,object,embed') || node.querySelector('iframe,frame,object,embed'));
  const observer = new MutationObserver(records => {
    if (records.some(record => Array.from(record.addedNodes).some(embedded))) revoke();
  });
  observer.observe(document, {childList:true, subtree:true});
  addEventListener('securitypolicyviolation', event => {
    if (['frame-src','child-src'].includes(event.effectiveDirective)) revoke();
  });
  addEventListener('pagehide', () => { active = false; observer.disconnect(); }, {once:true});
  addEventListener('message', async event => {
    if (!active || event.source !== window || event.origin !== 'happyranch-shell://connection') return;
    const item = event.data;
    if (item?.channel === 'hr-connection-subscribe' && item.name === 'client.stateChanged'
        && typeof item.enabled === 'boolean') {
      native.postMessage(JSON.stringify({protocol:'happyranch.connection',version:1,
        subscription:item.name,enabled:item.enabled})).catch(() => {});
      return;
    }
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

# Admission runs in the named world before adding the facade. The CSP, content
# filter and native policy construct a frame-free document; TOP_FRAME injection
# alone is insufficient. Do not create frames here: their refused navigation
# correctly invalidates admission. Nor does frame.contentWindow.parent test a
# child's execution context: from this caller it simply returns this window.
# Real child/navigation refusal is exercised separately against the loaded view.
FRAME_PROBE = r"""() => {
  return window === top && location.origin === 'happyranch-shell://connection'
      && location.href === 'happyranch-shell://connection/index.html'
      && typeof window.webkit?.messageHandlers?.connection?.postMessage === 'function'
      && window.frames.length === 0;
}"""


class ConnectionWindow:
    def __init__(self, app: object, parent: object, prefix: Path,
                 tr: Callable[[str, str], str], client: object = None) -> None:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("WebKit2", "4.1")
        gi.require_version("Soup", "3.0")
        gi.require_version("JavaScriptCore", "4.1")
        from gi.repository import Gio, GLib, Gtk, JavaScriptCore, Soup, WebKit2
        self.Gio, self.GLib, self.Gtk, self.JSC, self.Soup, self.W = Gio, GLib, Gtk, JavaScriptCore, Soup, WebKit2
        self.tr, self.prefix = tr, prefix
        self.app, self.parent = app, parent
        self.owns_client = client is None
        self.client = client or ClientUI(app, parent, prefix, tr)
        self.client.show_controls = self.show_controls
        self.client_document = object()
        self.requests = _contract.Requests()
        self.subscribed, self.event_pending, self.event_sequence = False, False, 0
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
        self.hello_seen = False
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
        if not self.closed and (self.probing or not self.hello_seen):
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
            content_type = mimetypes.guess_type(request.get_uri())[0] or "application/octet-stream"
            response.set_content_type(content_type)
            headers = self.Soup.MessageHeaders.new(self.Soup.MessageHeadersType.RESPONSE)
            headers.append("Content-Type", content_type)
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
                " && location.href === 'happyranch-shell://connection/index.html'"
                " && window.frames.length === 0"
                " && typeof window.webkit?.messageHandlers?.connection === 'undefined';",
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
            # The isolated relay uses rejected non-string input only to revoke
            # a document after frame insertion/CSP refusal (which may precede
            # decide-policy). It grants no request or frame/origin authority.
            if _manager == self.manager and self.active and not value.is_string():
                self.unavailable("document integrity")
            if (_manager != self.manager or not self.active or self.closed
                    or self.unavailable_reason or not value.is_string()
                    or self.view.get_uri() != ENTRY):
                raise ValueError("document unavailable")
            raw = value.to_string()
            subscription = _contract.parse_json(raw, 4096)
            if type(subscription) is dict and "subscription" in subscription:
                if (set(subscription) != {"protocol", "version", "subscription", "enabled"}
                        or subscription["protocol"] != "happyranch.connection"
                        or type(subscription["version"]) is not int or subscription["version"] != 1
                        or subscription["subscription"] != "client.stateChanged"
                        or type(subscription["enabled"]) is not bool):
                    raise ValueError("subscription")
                self.subscribed = subscription["enabled"]
                self.client.listener = self.queue_event if self.subscribed else None
                if self.subscribed:
                    self.queue_event()
                reply.return_value(self.JSC.Value.new_string(value.get_context(), "{}"))
                return True
            request = self.protocol.request(raw, origin=ORIGIN, main_frame=True)
        except Exception:
            reply.return_error_message('{"code":"invalidRequest","effect":"none","recovery":"refresh"}')
            return True

        def finish(result: dict | None = None, code: str | None = None, effect: str = "none") -> None:
            if not self.active or self.closed or self.generation != generation or self.view.get_uri() != ENTRY:
                return
            envelope = {"protocol": "happyranch.connection", "version": 1,
                        "requestId": request["requestId"], "ok": code is None}
            try:
                if code:
                    error = {"code": code, "effect": effect,
                             "recovery": "nativeRecovery" if effect == "unknown" else "refresh"}
                    self.protocol.validate(error, self.protocol.schema["$defs"]["error"])
                    envelope["error"] = error
                else:
                    envelope["result"] = self.protocol.result(request["command"], result)
            except (ValueError, UnicodeError):
                envelope["ok"] = False
                envelope["error"] = {"code": "unavailable", "effect": "unknown", "recovery": "nativeRecovery"}
            raw = json.dumps(envelope, ensure_ascii=False)
            if len(raw.encode("utf-8")) > 32768:
                reply.return_error_message('{"code":"unavailable","effect":"unknown","recovery":"nativeRecovery"}')
                return
            reply.return_value(self.JSC.Value.new_string(value.get_context(), raw))

        finish = self.requests.admit(request, self.client.model.token, finish)
        if finish is None:
            return True
        command = request["command"]
        if command == "bridge.hello":
            self.hello_seen = True
            finish(self.protocol.hello())
        elif command == "host.status":
            if self.busy:
                finish(None, "busy")
            else:
                self.status(finish)
        elif command in _contract.CLIENT_COMMANDS:
            model = self.client.model
            if command == "client.status":
                finish(model.snapshot())
            elif command == "client.health":
                model.health(finish)
            elif command == "client.savedComputers":
                model.discover(finish)
            elif command == "client.connectSaved":
                model.connect_saved(request["args"]["computerRef"], self.client_document, finish)
            elif command == "client.removeSaved":
                model.remove_saved(request["args"]["computerRef"], self.client_document, finish)
            elif command == "client.retrySave":
                if model.stage == "connected" and model.saved_access != "saved":
                    model.save(model.token, finish=finish)
                else:
                    finish(None, "stateChanged")
            elif command in ("client.acceptInvite", "client.retry"):
                if model.cleanup == "incomplete":
                    finish(None, "outcomeUnknown", "unknown")
                elif self.client.prompt or model.storage_busy or model.session or model.stage not in ("idle", "failed"):
                    finish(None, "busy")
                else:
                    model.accept(self.client_document)
                    finish(model.action())
            elif ((command == "client.disconnect" and model.stage == "connected")
                  or (command == "client.cancelAttempt" and model.stage in
                      ("validating", "joining", "pairing", "waitingApproval", "saving", "opening"))):
                model.confirm_stop(finish, self.client_document)
            else:
                finish(None, "stateChanged")
        else:
            finish(None, "unavailable")
        return True

    def show_controls(self) -> None:
        if self.closed:
            self.replacement = ConnectionWindow(self.app, self.parent,
                                                self.prefix, self.tr, self.client)
        else:
            self.window.present()
            self.queue_event()

    def queue_event(self) -> None:
        if not self.event_pending and self.active and self.subscribed:
            self.event_pending = True
            self.GLib.idle_add(self.send_event, self.generation)

    def send_event(self, generation: int) -> bool:
        self.event_pending = False
        if not self.active or not self.subscribed or self.generation != generation or self.view.get_uri() != ENTRY:
            return False
        self.event_sequence += 1
        data = dict(version=1, sequence=self.event_sequence, observedAt=_client._model.timestamp(),
                    snapshot=self.client.model.snapshot())
        self.protocol.validate(data, self.protocol.events["client.stateChanged"])
        raw = json.dumps(data, ensure_ascii=False)
        if len(raw.encode("utf-8")) > 32768:
            self.unavailable("event bound")
            return False
        arguments = self.GLib.Variant("a{sv}", {"raw": self.GLib.Variant("s", raw)})
        self.view.call_async_javascript_function(
            "window.postMessage({channel:'hr-connection-event',name:'client.stateChanged',raw}, location.origin);",
            -1, arguments, WORLD, ENTRY, self.cancellable, self.event_delivered, generation)
        return False

    def event_delivered(self, view: object, result: object, generation: int) -> None:
        try:
            view.call_async_javascript_function_finish(result)
        except Exception:
            if self.active and self.generation == generation:
                self.unavailable("event delivery")

    def status(self, finish: Callable) -> None:
        self.busy = True

        def replied(connection: object, result: object, _data: object) -> None:
            self.busy = False
            try:
                raw, = connection.call_finish(result).unpack()
                finish(self.protocol.host(raw))
            except Exception:
                finish(None, "unavailable")

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
                finish(None, "unavailable")

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
            finish(None, "unavailable")

    def detach(self) -> None:
        self.active = False
        self.generation += 1
        self.subscribed = False
        if self.client.listener == self.queue_event:
            self.client.listener = None
        self.client.model.detach(self.client_document)
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
        if self.owns_client:
            self.client.shutdown()
        self.window.destroy()


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

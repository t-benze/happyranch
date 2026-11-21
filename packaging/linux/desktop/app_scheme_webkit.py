"""WebKitGTK 4.1 loader for the opt-in, native-routed packaged app scheme."""
from __future__ import annotations

import http.client
from collections import deque
import itertools
import sys
from types import ModuleType
from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import select
import socket
import threading
import time
from urllib.parse import urlsplit

# Resolve the installed sibling under system Python -I, as connection_window does.
_spec = importlib.util.spec_from_file_location("app_scheme", Path(__file__).with_name("app_scheme.py"))
_transport = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_transport)
BODY_HEADER, CSP, MAX_BODY = _transport.BODY_HEADER, _transport.CSP, _transport.MAX_BODY
METHODS, ORIGIN, RESPONSE_HEADERS = _transport.METHODS, _transport.ORIGIN, _transport.RESPONSE_HEADERS
Bundle, LocalDaemon, SchemeError, route = _transport.Bundle, _transport.LocalDaemon, _transport.SchemeError, _transport.route
RemoteDaemon, RemoteError = _transport.RemoteDaemon, _transport.RemoteError
_spec = importlib.util.spec_from_file_location("connection_contract", Path(__file__).with_name("connection_contract.py"))
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
# Reconstructed views and the launcher share one installation-owned session.
_locale = sys.modules.get("_happyranch_app_locale")
if _locale is None:
    _spec = importlib.util.spec_from_file_location("_happyranch_app_locale", Path(__file__).with_name("app_locale.py"))
    _locale = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_locale)
    sys.modules["_happyranch_app_locale"] = _locale
_locale_process = ModuleType("_happyranch_locale_delivery")
_locale_process.queue = deque()
_locale_process.busy = False
_locale_process.launch = None
_locale_process.deferred = set()
_locale_process = sys.modules.setdefault("_happyranch_locale_delivery", _locale_process)
WORLD = "happyranch.app.v2"
CLIENT_COMMANDS = frozenset({"client.status", "client.acceptInvite", "client.retry",
                             "client.savedComputers", "client.connectSaved", "client.cancelAttempt",
                             "client.commitSwitch", "client.disconnect", "client.health",
                             "client.removeSaved", "client.retrySave"})


def owners_summary(value: object) -> dict:
    """Closed v2 ownersChanged summary; lifecycle decisions belong to S7-L3."""
    if (type(value) is not dict or set(value) != {"schema", "blocking", "counts"}
            or type(value["schema"]) is not int or value["schema"] != 2):
        raise ValueError("invalid owner summary")
    counts = value["counts"]
    if (type(counts) is not dict or set(counts) != {"dirty", "failed", "pending", "unknown"}
            or any(type(v) is not int or not 0 <= v <= 255 for v in counts.values())):
        raise ValueError("invalid owner counts")
    blocking = ("hold" if counts["pending"] or counts["unknown"] else
                "prompt" if counts["dirty"] or counts["failed"] else "none")
    if value["blocking"] != blocking:
        raise ValueError("inconsistent owner summary")
    return value
# The launcher loads installed siblings afresh after Restart. Keep this native
# counter in the process module registry, rather than reusing module-local IDs.
_generation_state = ModuleType("_happyranch_app_generations")
_generation_state.values = itertools.count(1)
_generations = sys.modules.setdefault("_happyranch_app_generations", _generation_state).values


def new_generation() -> str:
    value = format(next(_generations), "x")
    if len(value) > 32:
        raise ValueError("generation space exhausted")
    return value


# Document-start, top-frame fetch adaptation. Raw Blob/FormData must never reach
# URISchemeRequest.get_http_body (S0: WebKitGTK 2.52.6 SIGSEGV). S1a handles
# multipart serialization; this still owns JSON byte admission, request IDs
# and explicit abort messages. Unadapted XHR/form-navigation bodies are
# rejected before touching WebKit's body API.
FETCH_BYTES = r"""
(() => {
  if (location.protocol !== 'happyranch-app:' || location.host !== 'app') return;
  const original = window.fetch.bind(window), limit = 67108864;
  window.fetch = async (input, init) => {
    const request = new Request(input, init);
    const url = new URL(request.url);
    if (url.protocol !== 'happyranch-app:' || url.host !== 'app' || !url.pathname.startsWith('/api/v1/'))
      return original(input, init);
    const id = crypto.randomUUID();
    const headers = new Headers(request.headers);
    headers.delete('X-HappyRanch-Body-Bytes');
    headers.set('X-HappyRanch-Request-ID', id);
    const signal = request.signal;
    let sent = false, reader;
    const cancel = () => {
      if (reader) reader.cancel().catch(() => {});
      if (sent) window.postMessage({channel:'hr-app-cancel', id}, location.origin);
    };
    if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
    signal.addEventListener('abort', cancel, {once: true});
    const timer = setTimeout(() => { if (reader) reader.cancel().catch(() => {}); }, 10000);
    try {
      let body;
      if (request.body) {
        reader = request.body.getReader();
        const chunks = [];
        let length = 0;
        const deadline = performance.now() + 10000;
        while (true) {
          const part = await reader.read();
          if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
          if (performance.now() >= deadline) throw new TypeError('Request body timed out');
          if (part.done) break;
          length += part.value.byteLength;
          if (length > limit) { await reader.cancel(); throw new TypeError('Request body too large'); }
          chunks.push(part.value);
        }
        body = new Uint8Array(length);
        let offset = 0;
        for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.byteLength; }
        headers.set('X-HappyRanch-Body-Bytes', '1');
      }
      if (!['GET', 'HEAD'].includes(request.method)) headers.set('X-HappyRanch-Body-Bytes', '1');
      clearTimeout(timer);
      if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
      sent = true;
      // Use bytes here, never the original Request (which may own a Blob).
      const response = await original(request.url, {method: request.method, headers, body, signal,
        credentials: 'omit', redirect: 'error', cache: 'no-store'});
      // Keep the abort listener until the signal fires: streaming fetch resolves
      // at headers, long before the caller finishes consuming its body.
      return response;
    } catch (error) {
      cancel();
      signal.removeEventListener('abort', cancel);
      throw error;
    } finally { clearTimeout(timer); }
  };
})();
"""


APP_PAGE = r"""(() => {
  'use strict';
  if (window !== top || location.origin !== 'happyranch-app://app') return;
  let active = true, sequence = 0;
  const pending = new Map(), listeners = new Map([
    ['app.stateChanged', new Set()], ['app.dialogRequested', new Set()], ['host.stateChanged', new Set()]]);
  const clearListeners = () => { for (const handlers of listeners.values()) handlers.clear(); };
  const fail = code => JSON.stringify({code, effect:'none', recovery:'refresh'});
  const retired = command => {
    const read = ['app.status','client.status','client.savedComputers','client.health','host.status','host.nextDevice'].includes(command);
    // Posting alone cannot confirm that a mutation had no effect, even before admission.
    return JSON.stringify({code:'documentChanged', effect:read ? 'none' : 'unknown',
      recovery:read ? 'refresh' : 'nativeRecovery'});
  };
  addEventListener('message', event => {
    if (!active || event.source !== window || event.origin !== location.origin) return;
    const item = event.data;
    if (item?.channel === 'hr-app-revoked') {
      active = false; clearListeners();
      for (const callback of pending.values()) callback.reject(retired(callback.command));
      pending.clear(); return;
    }
    if (item?.channel === 'hr-app-event' && typeof item.raw === 'string') {
      for (const listener of listeners.get(item.event) || []) listener(item.raw);
    } else if (item?.channel === 'hr-app-reply' && pending.has(item.id) && typeof item.raw === 'string') {
      const callback = pending.get(item.id); pending.delete(item.id);
      if (item.ok === true) callback.resolve(item.raw); else callback.reject(item.raw);
    }
  });
  const call = (command, data) => new Promise((resolve, reject) => {
    if (!active) return reject(fail('documentChanged'));
    const empty = ['app.status','client.status','client.acceptInvite','client.retry',
      'client.savedComputers','client.cancelAttempt','client.commitSwitch','client.disconnect',
      'client.health','client.retrySave','host.status','host.enable','host.turnOff','host.createInvite',
      'host.approveDevice','host.rejectDevice','host.nextDevice','host.revokeDevice'];
    let args;
    // Native owns closed parsing, including duplicate-key detection. Preserve
    // the original string; JSON.parse here would erase duplicate keys.
    const locale = command === 'app.locale.set' && typeof data === 'string';
    try { args = JSON.parse(data); } catch {
      if (!locale) return reject(fail('unsupportedCommand'));
    }
    const saved = ['client.connectSaved','client.removeSaved'].includes(command) && args && !Array.isArray(args)
      && Object.keys(args).length === 1 && typeof args.computerRef === 'string'
      && args.computerRef.length > 0 && args.computerRef.length <= 256;
    const counts = args?.counts;
    const owners = command === 'app.ownersChanged' && args && !Array.isArray(args)
      && Object.keys(args).sort().join(',') === 'blocking,counts,schema' && args.schema === 2
      && counts && !Array.isArray(counts) && Object.keys(counts).sort().join(',') === 'dirty,failed,pending,unknown'
      && Object.values(counts).every(n=>Number.isInteger(n) && n >= 0 && n <= 255)
      && args.blocking === (counts.pending || counts.unknown ? 'hold' : counts.dirty || counts.failed ? 'prompt' : 'none');
    if (!(empty.includes(command) && data === '{}') && !saved && !owners && !locale) return reject(fail('unsupportedCommand'));
    if (pending.size >= 8) return reject(fail('busy'));
    const id = 'r' + (++sequence);
    pending.set(id, {resolve, reject, command});
    window.postMessage({channel:'hr-app-call', id, command, data}, location.origin);
  });
  const on = (event, handler) => {
    if (!active || !listeners.has(event) || typeof handler !== 'function') throw fail('unsupportedCommand');
    const handlers = listeners.get(event);
    handlers.add(handler);
    window.postMessage({channel:'hr-app-subscribe', event, enabled:true}, location.origin);
    return () => {
      handlers.delete(handler);
      if (!handlers.size) window.postMessage({channel:'hr-app-subscribe', event, enabled:false}, location.origin);
    };
  };
  addEventListener('pagehide', () => {
    active = false; clearListeners();
    for (const callback of pending.values()) callback.reject(retired(callback.command));
    pending.clear();
  }, {once:true});
  Object.defineProperty(window, 'happyranchConnection', {value:Object.freeze({call,on})});
})();"""

APP_RELAY = r"""(() => {
  'use strict';
  if (window !== top || location.origin !== 'happyranch-app://app') return;
  const native = window.webkit.messageHandlers.appBridge, token = crypto.randomUUID();
  let active = true, admitted = false;
  const queued = [];
  const localeReplies = [];
  const send = item => {
    if (admitted) return native.postMessage(JSON.stringify({token, ...item}));
    if (queued.length >= 8) return Promise.reject();
    return new Promise((resolve, reject) => queued.push({item, resolve, reject}));
  };
  globalThis.hrAppBridge = Object.freeze({get token() { return active ? token : ''; }, admit:value => {
    if (!active || value !== token || admitted) return;
    admitted = true;
    for (const entry of queued.splice(0)) send(entry.item).then(entry.resolve, entry.reject);
  }, publishLocale:async (raw, enabled) => {
    // Native waits for this promise before starting the next locale write.
    // Even if WebKit schedules publication before its native promise callback,
    // the page receives the setter reply before this full state snapshot.
    const delivery = localeReplies.shift();
    if (!delivery) return;
    await delivery.promise;
    if (active && admitted && enabled === 'true')
      window.postMessage({channel:'hr-app-event', event:'app.stateChanged', raw}, location.origin);
  }, emit:(event, raw) => {
    if (active && admitted && ['app.stateChanged','app.dialogRequested','host.stateChanged'].includes(event))
      window.postMessage({channel:'hr-app-event', event, raw}, location.origin);
  }});
  const revoke = () => {
    if (!active) return;
    active = false;
    for (const entry of queued.splice(0)) entry.reject();
    window.postMessage({channel:'hr-app-revoked'}, location.origin);
    native.postMessage(JSON.stringify({token, revoke:true})).catch(() => {});
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
    if (!active || event.source !== window || event.origin !== 'happyranch-app://app') return;
    const item = event.data;
    if (item?.channel === 'hr-app-subscribe' && ['app.stateChanged','app.dialogRequested','host.stateChanged'].includes(item.event)
        && typeof item.enabled === 'boolean') {
      send({event:item.event, enabled:item.enabled}).catch(() => {}); return;
    }
    if (item?.channel === 'hr-app-cancel' && typeof item.id === 'string') {
      send({cancel:item.id}).catch(() => {}); return;
    }
    if (item?.channel !== 'hr-app-call' || !['app.status','client.status','client.acceptInvite',
        'client.retry','client.savedComputers','client.connectSaved','client.cancelAttempt',
        'client.commitSwitch','client.disconnect','client.health','client.removeSaved','client.retrySave',
        'host.status','host.enable','host.turnOff','host.createInvite','host.approveDevice','host.rejectDevice',
        'host.nextDevice','host.revokeDevice','app.ownersChanged','app.locale.set'].includes(item.command) || typeof item.data !== 'string'
        || typeof item.id !== 'string' || !/^r[0-9]{1,16}$/.test(item.id)) return;
    let raw, ok = true, delivery;
    if (item.command === 'app.locale.set') {
      delivery = {};
      delivery.promise = new Promise(resolve => { delivery.resolve = resolve; });
      localeReplies.push(delivery);
    }
    try { raw = await send({command:item.command, data:item.data}); }
    catch (error) {
      ok = false;
      let detail;
      try { detail = JSON.parse(error.message); } catch {}
      // Lost transport after dispatch cannot establish that a mutation had no effect.
      if (!detail) detail = {code:'outcomeUnknown',effect:'unknown',recovery:'nativeRecovery'};
      const codes = ['owner_save_unknown','owner_save_pending','owner_edits_pending','non_get_pending','incompatible',
        'target_starting','target_unreachable','busy','stateChanged','documentChanged','unsupportedCommand',
        'permissionDenied','unavailable','outcomeUnknown','notOwned','removalIncomplete','invalidRequest',
        'expired','identityChanged','unsupportedVersion','notLocalShell','invalidInvite','newInviteRequired',
        'networkUnavailable','trustMismatch','runtimeUnavailable','accessDenied','revokedConfirmed',
        'keychainLocked','keychainDenied','saveFailed'];
      const result = {code:codes.includes(detail?.code) ? detail.code : 'invalidRequest',
        effect:['none','unknown','confirmed'].includes(detail?.effect) ? detail.effect : 'unknown',
        recovery:['none','refresh','explicitRetry','newInviteOnHost','nativeRecovery','nativePermission','operatorRecovery'].includes(detail?.recovery)
          ? detail.recovery : 'nativeRecovery'};
      if (result.code === 'incompatible' && ['host','app','unknown'].includes(detail.side)) result.side = detail.side;
      raw = JSON.stringify(result);
    }
    if (active && typeof raw === 'string' && new TextEncoder().encode(raw).length <= 32768)
      window.postMessage({channel:'hr-app-reply', id:item.id, raw, ok}, location.origin);
    if (delivery) {
      if (!ok) {
        const index = localeReplies.indexOf(delivery);
        if (index >= 0) localeReplies.splice(index, 1);
      }
      delivery.resolve();
    }
  });
})();"""


class Transfer:
    def __init__(self, target: LocalDaemon | RemoteDaemon | None = None) -> None:
        self.stop = threading.Event()
        self.forwarded = False
        self.generation = None
        self.method = "GET"
        self.send_lock = threading.RLock()
        self.target = target
        self.cancel_code = "cancelled"
        self.cancellable = None
        self.connection = None
        self.socket = None
        self.has_body = False
        self.write_fd: int | None = None
        self.deadline = time.monotonic() + 15
        self.finished = False

    def cancel(self, code: str = "cancelled") -> None:
        with self.send_lock:
            if code == "target_changed" or not self.stop.is_set():
                self.cancel_code = "cancelled" if self.forwarded else code
            self.stop.set()
        if self.cancellable is not None:
            self.cancellable.cancel()
        upstream = self.socket or (self.connection.sock if self.connection is not None else None)
        if upstream is not None:
            try:
                upstream.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        if self.connection is not None and callable(getattr(self.connection, "cancel", None)):
            self.connection.cancel()

    def interrupted(self) -> SchemeError:
        changed = self.cancel_code == "target_changed" and not self.forwarded
        return SchemeError(409 if changed else 499, "target_changed" if changed else "cancelled")

    def before_send(self) -> None:
        with self.send_lock:
            if self.stop.is_set():
                raise self.interrupted()
            # Mark BEFORE attempting the first upstream write. A partial write
            # or an error from sendall cannot prove that nothing was forwarded.
            self.forwarded = True


class AppScheme:
    """One trusted Main document, native status, and target-bound transfers."""

    def __init__(self, bundle: Path, verified_port: int | None, data: Path,
                 *, local_mode: str = "service") -> None:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("WebKit2", "4.1")
        gi.require_version("Soup", "3.0")
        gi.require_version("JavaScriptCore", "4.1")
        from gi.repository import Gio, GLib, Gtk, Soup, WebKit2, JavaScriptCore
        if ((WebKit2.get_major_version(), WebKit2.get_minor_version()) < (2, 40)
                or not callable(getattr(WebKit2.URISchemeRequest, "get_http_body", None))):
            raise ValueError("WebKitGTK 2.40 or newer is required")
        self.Gio, self.GLib, self.Gtk, self.Soup, self.W = Gio, GLib, Gtk, Soup, WebKit2
        self.JSC = JavaScriptCore
        self.bundle = Bundle(bundle)
        self.daemon = LocalDaemon(verified_port) if verified_port is not None else None
        self.closed = False
        self.initialize_state(local_mode)
        self.transfers: dict[str, Transfer] = {}
        self.cancelled: dict[str, float] = {}
        data.mkdir(parents=True, exist_ok=True, mode=0o700)
        manager = WebKit2.WebsiteDataManager(base_data_directory=str(data / "data"),
                                            base_cache_directory=str(data / "cache"))
        self.context = WebKit2.WebContext.new_with_website_data_manager(manager)
        security = self.context.get_security_manager()
        security.register_uri_scheme_as_secure("happyranch-app")
        security.register_uri_scheme_as_cors_enabled("happyranch-app")
        # No local-scheme privileges and no network listener.
        self.context.register_uri_scheme("happyranch-app", self.handle)
        controller = WebKit2.UserContentManager()
        self.controller = controller
        controller.connect("script-message-with-reply-received::appBridge", self.bridge_message)
        if not controller.register_script_message_handler_with_reply("appBridge", WORLD):
            raise ValueError("named-world bridge unavailable")
        controller.add_script(WebKit2.UserScript.new_for_world(
            APP_RELAY, WebKit2.UserContentInjectedFrames.TOP_FRAME,
            WebKit2.UserScriptInjectionTime.START, WORLD, [ORIGIN + "/*"], None))
        controller.add_script(WebKit2.UserScript.new(
            APP_PAGE, WebKit2.UserContentInjectedFrames.TOP_FRAME,
            WebKit2.UserScriptInjectionTime.START, [ORIGIN + "/*"], None))
        controller.add_script(WebKit2.UserScript.new(FETCH_BYTES, WebKit2.UserContentInjectedFrames.TOP_FRAME,
                                                     WebKit2.UserScriptInjectionTime.START, None, None))
        self.view = WebKit2.WebView(web_context=self.context, user_content_manager=controller)
        self.view.connect("destroy", lambda *_: self.close())
        self.view.connect("web-process-terminated", lambda *_: self.invalidate_document())
        self.view.connect("load-changed", self.load_changed)
        self.view.connect("decide-policy", self.policy)
        self.view.connect("load-failed", lambda *_: (self.invalidate_document(), False)[1])
        self.view.connect("create", lambda *_: None)
        self.context.connect("download-started", self.download_started)
        self.timer = GLib.timeout_add(100, self.monitor)

    def initialize_state(self, local_mode: str = "service") -> None:
        self.target_lock = threading.RLock()
        self.generation = new_generation()
        self.document = 0
        self.bridge_token = None
        self.subscribed = False
        self.dialog_subscribed = False
        self.host_subscribed = False
        self.host_adapter = None
        self.owners = None
        self.owner_uncertain = False
        self.owners_unresolved = False
        self.status_refresh = None
        self.status_stop = None
        self.local = {"supported": local_mode == "service", "mode": local_mode,
                      "running": "running" if self.daemon is not None else "unknown",
                      "prompt_base_url": None}
        self.candidate = None
        self.client_dispatch = None
        self.access_unsaved = False
        if _locale_process.launch is None:
            _locale_process.launch = _locale.load()
        self.locale = dict(_locale_process.launch)  # Native built-in names keep the launch language.

    def status(self) -> dict:
        with self.target_lock:
            daemon = self.daemon
            local = dict(self.local)
            target, incompatible, state = None, None, "no_target"
            if daemon is not None:
                remote = isinstance(daemon, RemoteDaemon)
                target = ({"kind": "remote", "id": daemon.target_id, "name": daemon.name} if remote else
                          {"kind": "local", "id": "local", "name": "这台电脑" if self.locale["current"] == "zh-CN" else "This computer"})
                with daemon._state:
                    state = daemon.state
                    if state == "incompatible":
                        incompatible = {"side": daemon._compatibility or "host", "host_contract": daemon.host_contract,
                                        "client_contract": _transport.CLIENT, "min_daemon": _transport.MIN_DAEMON}
                if not remote and local["running"] == "stopped":
                    state, incompatible = "unreachable", None
                if not remote and local["running"] == "running":
                    local["prompt_base_url"] = f"http://127.0.0.1:{daemon.port}/api/v1"
            return {"schema": 2, "generation": self.generation, "target": target, "state": state,
                    "incompatible": incompatible, "local": local, "candidate": self.candidate,
                    "access_unsaved": self.access_unsaved, "locale": _locale.current()}

    def next_locale(self) -> None:
        """GTK-context FIFO: write -> reply -> delivered publication -> next write."""
        # Process-owned queue survives AppScheme reconstruction after Restart.
        if _locale_process.busy or not _locale_process.queue:
            return
        app, locale, document, token, context, reply = _locale_process.queue.popleft()
        def current() -> bool:
            return not app.closed and document == app.document and token == app.bridge_token
        if not current():
            reply.return_error_message('{"code":"documentChanged","effect":"none","recovery":"refresh"}')
            self.next_locale()
            return
        _locale_process.busy = True
        def advance(*_args: object) -> None:
            if _args and _args[1] is not None:
                try:
                    _args[0].call_async_javascript_function_finish(_args[1])
                except app.GLib.Error:
                    pass  # WebKit/document loss does not replay an acknowledged write.
            _locale_process.busy = False
            deferred = tuple(_locale_process.deferred)
            _locale_process.deferred.clear()
            for owner in deferred:
                owner.emit_state()
            self.next_locale()
        def work() -> None:
            snapshot = _locale.set(locale)
            def complete() -> bool:
                if not current():
                    reply.return_error_message('{"code":"documentChanged","effect":"unknown","recovery":"nativeRecovery"}')
                    advance()
                    return False
                reply.return_value(app.JSC.Value.new_string(context, json.dumps({"schema": 2, "locale": snapshot})))
                _locale_process.deferred.discard(app)
                state = {**app.status(), "locale": snapshot}
                args = app.GLib.Variant("a{sv}", {"raw": app.GLib.Variant("s", json.dumps(state)),
                    "enabled": app.GLib.Variant("s", "true" if app.subscribed else "false"),
                    "token": app.GLib.Variant("s", token)})
                # Private named-world barrier, not a page facade or wire field.
                # Also drains the reply barrier when there is no subscription.
                app.view.call_async_javascript_function(
                    "if (globalThis.hrAppBridge?.token === token) return await globalThis.hrAppBridge.publishLocale(raw, enabled);",
                    -1, args, WORLD, ORIGIN + "/", None, advance, None)
                return False
            app.GLib.idle_add(complete)
        threading.Thread(target=work, daemon=True, name="app-locale").start()

    def select_target(self, target: LocalDaemon | RemoteDaemon | None,
                      *, owner_saves: tuple[str, ...] | None = None) -> str | None:
        """Native commit gate; S5-L2/S6 supply the captured owner-save snapshot.

        Missing/unknown owner evidence refuses a voluntary switch. Navigation,
        process loss and close invalidate transfers independently of this gate.
        """
        with self.target_lock:
            if self.owner_uncertain or owner_saves is None or any(state == "unknown" for state in owner_saves):
                return "owner_save_unknown"
            if any(state == "pending" for state in owner_saves):
                return "owner_save_pending"
            if any(state not in {"idle", "saved", "dirty", "failed"} for state in owner_saves):
                return "owner_save_unknown"
            if any(state in {"dirty", "failed"} for state in owner_saves):
                return "owner_edits_pending"
            if any(t.generation == self.generation and not t.finished and t.method != "GET"
                   for t in self.transfers.values()):
                return "non_get_pending"
            self._replace_target(target)
        return None

    def _replace_target(self, target: LocalDaemon | RemoteDaemon | None) -> None:
        # The same lock fences first-send admission against this atomic swap.
        with self.target_lock:
            old, generation = self.daemon, self.generation
            self.daemon, self.generation = target, new_generation()
            if self.status_stop is not None:
                self.status_stop.set()
            for transfer in self.transfers.values():
                if transfer.generation == generation:
                    transfer.cancel("target_changed")
            if old is not None:
                old.reset()
            if target is not None and target is not old:
                target.reset()
            self.emit_state()
            if self.bridge_token is not None:
                self.refresh_status()

    def refresh_status(self) -> None:
        """Native admission progresses even while the page is in recovery."""
        with self.target_lock:
            daemon, generation, document = self.daemon, self.generation, self.document
            if daemon is None or self.status_refresh == generation:
                return
            self.status_refresh = generation
            stop = self.status_stop = threading.Event()
        def work() -> None:
            try:
                daemon.bootstrap(stop)
            except SchemeError:
                pass
            def done() -> bool:
                if (not self.closed and generation == self.generation and document == self.document):
                    self.status_refresh = None
                    self.emit_state()
                return False
            self.GLib.idle_add(done)
        threading.Thread(target=work, daemon=True, name="app-status-admission").start()

    def invalidate_document(self) -> None:
        self.retire_owners()
        self.stop_host(retire=True)
        self.document += 1
        self.bridge_token, self.subscribed = None, False
        self.dialog_subscribed = False
        self._replace_target(self.daemon)

    def owner_snapshot(self) -> tuple[str, ...] | None:
        if self.owners is None or self.owner_uncertain:
            return None
        return tuple(state for state, count in self.owners["counts"].items() if count)

    def retire_owners(self) -> None:
        self.owner_uncertain |= self.owners_unresolved or any(
            t.generation == self.generation and not t.finished and t.method != "GET"
            for t in self.transfers.values())
        self.owners = None

    def stop_host(self, *, retire: bool = False) -> None:
        if self.host_adapter is not None:
            if retire:
                self.host_adapter.detach(self)
            else:
                self.host_adapter.unsubscribe(self)
        self.host_subscribed = False

    def emit_state(self) -> None:
        # Background health/target observations must not publish a locale that
        # the store has updated before its setter reply has been delivered.
        if _locale_process.busy:
            _locale_process.deferred.add(self)
            return
        self.emit_event("app.stateChanged", json.dumps(self.status()))

    def request_dialog(self, dialog: str) -> bool:
        """Entry only: no target/lifecycle effects, queue, replay or dialog owner."""
        if dialog not in ("connection", "settings"):
            return False
        return self.emit_event("app.dialogRequested", json.dumps({"schema": 2, "dialog": dialog},
                                                               separators=(",", ":")))

    def emit_event(self, event: str, raw: str) -> bool:
        subscribed = (self.subscribed if event == "app.stateChanged" else
                      self.dialog_subscribed if event == "app.dialogRequested" else
                      self.host_subscribed if event == "host.stateChanged" else False)
        if not subscribed or self.bridge_token is None or self.closed:
            return False
        args = self.GLib.Variant("a{sv}", {"raw": self.GLib.Variant("s", raw),
                                         "event": self.GLib.Variant("s", event),
                                         "token": self.GLib.Variant("s", self.bridge_token)})
        self.view.call_async_javascript_function(
            "if (globalThis.hrAppBridge?.token === token) globalThis.hrAppBridge.emit(event, raw);",
            -1, args, WORLD, ORIGIN + "/", None, None, None)
        return True

    def bridge_message(self, manager: object, value: object, reply: object) -> bool:
        owner_command = False
        locale_command = False
        try:
            if manager != self.controller or self.closed or not self.bridge_token or not value.is_string():
                raise ValueError("unavailable document")
            path, _ = route(self.view.get_uri())
            if path.startswith("/api/"):
                raise ValueError("untrusted document")
            item = _contract.parse_json(value.to_string(), 4096)
            if type(item) is not dict or item.get("token") != self.bridge_token:
                raise ValueError("stale document")
            if item.get("command") == "app.locale.set":
                locale_command = True
                if set(item) != {"token", "command", "data"} or type(item["data"]) is not str:
                    raise ValueError("invalid locale request")
                args = _contract.parse_json(item["data"], 1024)
                if (type(args) is not dict or set(args) != {"schema", "locale"}
                        or type(args["schema"]) is not int or args["schema"] != 2
                        or type(args["locale"]) is not str or args["locale"] not in {"en", "zh-CN"}):
                    raise ValueError("invalid locale request")
                if len(_locale_process.queue) + _locale_process.busy >= 8:
                    reply.return_error_message('{"code":"busy","effect":"none","recovery":"refresh"}')
                    return True
                _locale_process.queue.append((self, args["locale"], self.document, self.bridge_token, value.get_context(), reply))
                self.next_locale()
                return True
            if item.get("command") == "app.ownersChanged":
                owner_command = True
                self.owners = None  # Invalid current-document input cannot retain clean clearance.
                if set(item) != {"token", "command", "data"} or type(item["data"]) is not str:
                    raise ValueError("invalid owner push")
                self.owners = owners_summary(_contract.parse_json(item["data"], 1024))
                self.owners_unresolved = bool(self.owners["counts"]["pending"] or self.owners["counts"]["unknown"])
                reply.return_value(self.JSC.Value.new_string(value.get_context(), "{}"))
                return True
            if set(item) == {"token", "command", "data"} and item["command"] == "app.status" and item["data"] == "{}":
                self.refresh_status()
                result = self.status()
            elif (set(item) == {"token", "command", "data"}
                  and item["command"] in CLIENT_COMMANDS | _contract.HOST_METHODS.keys()
                  and type(item["data"]) is str):
                host = item["command"] in _contract.HOST_METHODS
                args = _contract.parse_json(item["data"], 1024)
                if item["command"] in ("client.connectSaved", "client.removeSaved"):
                    if (type(args) is not dict or set(args) != {"computerRef"}
                            or type(args["computerRef"]) is not str or not 0 < len(args["computerRef"]) <= 256):
                        raise ValueError("invalid saved reference")
                elif args != {} or item["data"] != "{}":
                    raise ValueError("unexpected arguments")
                document, token = self.document, self.bridge_token
                def finish(result: object = None, error: str | dict | None = None, effect: str = "none",
                           recovery: str | None = None) -> None:
                    if self.closed or document != self.document or token != self.bridge_token:
                        reply.return_error_message(json.dumps({"code": "documentChanged",
                            "effect": "unknown" if host and item["command"] != "host.status" else "none",
                            "recovery": "nativeRecovery" if host else "refresh"}))
                    elif error:
                        detail = error if type(error) is dict else {"code": error}
                        detail = {**detail, "effect": effect, "recovery": recovery or (
                            "nativeRecovery" if effect == "unknown" else
                            "nativePermission" if detail["code"] == "permissionDenied" else "refresh")}
                        if host:
                            try:
                                self.connection_protocol.validate(detail, self.connection_protocol.schema["$defs"]["error"])
                            except ValueError:
                                detail = {"code": "unavailable", "effect": "unknown", "recovery": "nativeRecovery"}
                        reply.return_error_message(json.dumps(detail))
                    else:
                        if item["command"] in {"client.health", "client.retrySave", "client.removeSaved"} or host:
                            try:
                                result = self.connection_protocol.result(item["command"], result)
                            except (ValueError, TypeError, UnicodeError):
                                reply.return_error_message('{"code":"unavailable","effect":"unknown","recovery":"nativeRecovery"}')
                                return
                        reply.return_value(self.JSC.Value.new_string(value.get_context(), json.dumps(result)))
                if host:
                    if self.host_adapter is None:
                        finish(None, "unavailable")
                    else:
                        self.host_adapter.host_request(_contract.HOST_METHODS[item["command"]], finish,
                            owner=self, current=lambda: not self.closed and document == self.document
                                and token == self.bridge_token, window=self.view.get_toplevel())
                elif not callable(self.client_dispatch):
                    finish(None, "unavailable")
                elif args:
                    self.client_dispatch(item["command"], document, finish, **args)
                else:
                    self.client_dispatch(item["command"], document, finish)
                return True
            elif (set(item) == {"token", "event", "enabled"}
                  and item["event"] in ("app.stateChanged", "app.dialogRequested", "host.stateChanged") and type(item["enabled"]) is bool):
                if item["event"] == "app.stateChanged":
                    self.subscribed = item["enabled"]
                elif item["event"] == "app.dialogRequested":
                    self.dialog_subscribed = item["enabled"]
                else:
                    self.host_subscribed = item["enabled"]
                    if self.host_adapter is None:
                        raise ValueError("host unavailable")
                    if self.host_subscribed:
                        document, token = self.document, self.bridge_token
                        def current() -> bool:
                            return not self.closed and document == self.document and token == self.bridge_token
                        self.host_adapter.subscribe(self, current=current,
                            visible=lambda: self.view.get_toplevel().get_visible(),
                            emit=lambda raw: self.emit_event("host.stateChanged", raw) if current() else False,
                            window=self.view.get_toplevel())
                    else:
                        self.stop_host()
                result = {}
                if item["event"] == "app.stateChanged" and self.subscribed:
                    self.emit_state()
            elif set(item) == {"token", "cancel"} and type(item["cancel"]) is str:
                self.cancel_id(item["cancel"])
                result = {}
            elif set(item) == {"token", "revoke"} and item["revoke"] is True:
                self.invalidate_document()
                raise ValueError("document integrity")
            else:
                raise ValueError("unsupported command")
            reply.return_value(self.JSC.Value.new_string(value.get_context(), json.dumps(result)))
        except (SchemeError, ValueError, TypeError, UnicodeError, RecursionError):
            reply.return_error_message(json.dumps({"code": "invalidRequest", "effect": "none",
                                                  "recovery": "none" if owner_command or locale_command else "refresh"}))
        return True

    def probe_document(self) -> None:
        document = self.document
        def named_done(view: object, result: object, _data: object) -> None:
            try:
                raw = view.call_async_javascript_function_finish(result)
                token = raw.to_string() if raw.is_string() else ""
                if not token or len(token) > 64 or document != self.document or self.closed:
                    return
                def page_done(view: object, result: object, _data: object) -> None:
                    try:
                        checked = view.call_async_javascript_function_finish(result)
                        if (document == self.document and not self.closed and checked.is_boolean()
                                and checked.to_boolean()):
                            self.bridge_token = token
                            self.refresh_status()
                            args = self.GLib.Variant("a{sv}", {"token": self.GLib.Variant("s", token)})
                            view.call_async_javascript_function("globalThis.hrAppBridge?.admit(token);",
                                -1, args, WORLD, ORIGIN + "/", None, None, None)
                    except self.GLib.Error:
                        self.invalidate_document()
                view.call_async_javascript_function(
                    "return window === top && window.frames.length === 0 && location.origin === 'happyranch-app://app'"
                    " && typeof window.webkit?.messageHandlers?.appBridge === 'undefined';",
                    -1, None, None, ORIGIN + "/", None, page_done, None)
            except self.GLib.Error:
                self.invalidate_document()
        self.view.call_async_javascript_function(
            "return window === top && window.frames.length === 0 && location.origin === 'happyranch-app://app'"
            " ? globalThis.hrAppBridge?.token || '' : '';",
            -1, None, WORLD, ORIGIN + "/", None, named_done, None)

    def cancel_all(self) -> None:
        for transfer in self.transfers.values():
            transfer.cancel()

    def close(self) -> None:
        if self.closed:
            return
        self.retire_owners()
        self.stop_host(retire=True)
        self.closed = True
        if self.status_stop is not None:
            self.status_stop.set()
        self.bridge_token, self.subscribed = None, False
        self.dialog_subscribed = False
        self.generation = new_generation()
        self.cancel_all()
        self.GLib.source_remove(self.timer)
        if self.daemon is not None:
            self.daemon.reset()
        self.daemon = None

    def load_changed(self, _view: object, event: object) -> None:
        if event == self.W.LoadEvent.STARTED:
            self.invalidate_document()
        elif event == self.W.LoadEvent.FINISHED:
            self.probe_document()

    def cancel_id(self, key: str) -> None:
        if len(key) != 36:
            return
        transfer = self.transfers.get(key)
        if transfer is not None:
            transfer.cancel()
        elif len(self.cancelled) < 256:
            self.cancelled[key] = time.monotonic() + 15

    def monitor(self) -> bool:
        now = time.monotonic()
        self.cancelled = {k: end for k, end in self.cancelled.items() if end > now}
        for key, transfer in list(self.transfers.items()):
            if transfer.finished:
                del self.transfers[key]
                continue
            if now >= transfer.deadline:
                transfer.cancel()
            if transfer.write_fd is not None:
                poller = select.poll()
                poller.register(transfer.write_fd, select.POLLERR | select.POLLHUP | select.POLLNVAL)
                try:
                    if poller.poll(0):
                        transfer.cancel()
                except OSError:
                    transfer.cancel()
        return not self.closed

    def reply(self, request: object, status: int, data: bytes, mime: str = "application/json") -> None:
        source = self.Gio.MemoryInputStream.new_from_bytes(self.GLib.Bytes.new(data))
        self.finish(request, status, source, len(data), {"content-type": mime})

    def error(self, request: object, error: SchemeError) -> None:
        detail = {"code": error.code}
        if error.side is not None:
            detail["side"] = error.side
        self.reply(request, error.status, json.dumps({"detail": detail}).encode())

    def finish(self, request: object, status: int, source: object, length: int,
               headers: dict[str, str]) -> None:
        response = self.W.URISchemeResponse.new(source, length)
        response.set_status(status, http.client.responses.get(status, "Response"))
        mime = headers.get("content-type", "application/octet-stream")
        response.set_content_type(mime)
        outgoing = self.Soup.MessageHeaders.new(self.Soup.MessageHeadersType.RESPONSE)
        for key, value in {**headers, "Content-Security-Policy": CSP,
                           "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}.items():
            outgoing.append(key, value)
        response.set_http_headers(outgoing)
        request.finish_with_response(response)

    def handle(self, request: object) -> None:
        transfer = None
        try:
            if self.closed or request.get_web_view() != self.view:
                raise SchemeError(503, "target_unreachable")
            path, target = route(request.get_uri())
            method = request.get_http_method()
            if method not in METHODS:
                raise SchemeError(405, "method_not_allowed")
            if not path.startswith("/api/v1/"):
                if method not in ("GET", "HEAD"):
                    raise SchemeError(405, "method_not_allowed")
                data, mime = self.bundle.get(path)
                self.reply(request, 200, b"" if method == "HEAD" else data, mime)
                return
            if path.rstrip("/") == "/api/v1/auth/bootstrap":
                if method != "GET":
                    raise SchemeError(405, "method_not_allowed")
            headers: dict[str, str] = {}
            request.get_http_headers().foreach(lambda k, v, *_: headers.__setitem__(k.lower(), v), None)
            if sum(len(k) + len(v) for k, v in headers.items()) > 65536:
                raise SchemeError(431, "headers_too_large")
            if not (method == "GET" and path.rstrip("/") == "/api/v1/auth/bootstrap"):
                if headers.get("x-happyranch-target") != self.generation:
                    raise SchemeError(409, "target_changed")
            key = headers.get("x-happyranch-request-id", "")
            if len(key) != 36 or key in self.transfers:
                raise SchemeError(400, "invalid_request_id")
            if len(self.transfers) >= 32:
                raise SchemeError(429, "too_many_requests")
            if (headers.get(BODY_HEADER.lower()) == "1"
                    and sum(t.has_body and not t.finished for t in self.transfers.values()) >= 2):
                raise SchemeError(429, "too_many_requests")
            # Even a zero-byte POST must be adapted. Do not inspect a raw body.
            if method not in ("GET", "HEAD") and headers.get(BODY_HEADER.lower()) != "1":
                raise SchemeError(415, "byte_body_required")
            transfer = Transfer(self.daemon)
            transfer.generation, transfer.method = self.generation, method
            transfer.send_lock = self.target_lock
            transfer.has_body = headers.get(BODY_HEADER.lower()) == "1"
            transfer.cancellable = self.Gio.Cancellable()
            self.transfers[key] = transfer
            if key in self.cancelled:
                transfer.cancel()
            # Only byte-adapted requests can enter WebKit's body API.
            body_stream = request.get_http_body() if headers.get(BODY_HEADER.lower()) == "1" else None
            threading.Thread(target=self.forward, args=(request, transfer, method, target, headers, body_stream),
                             daemon=True, name="app-scheme-api").start()
        except SchemeError as error:
            self.error(request, error)
        except (OSError, ValueError, self.GLib.Error):
            if transfer is not None:
                transfer.finished = True
            self.error(request, SchemeError(500, "bundle_unavailable"))

    def forward(self, request: object, transfer: Transfer, method: str, target: str,
                headers: dict[str, str], body_stream: object) -> None:
        delivered_headers = False
        writer = None
        complete = False
        try:
            with self.target_lock:
                if transfer.generation is None:
                    transfer.generation = self.generation
                transfer.method, transfer.send_lock = method, self.target_lock
                if transfer.generation != self.generation:
                    transfer.cancel("target_changed")
            if transfer.stop.is_set():
                raise transfer.interrupted()
            daemon = transfer.target
            if daemon is None:
                raise SchemeError(503, "no_target")
            daemon.bootstrap(transfer.stop)
            if route(ORIGIN + target)[0].rstrip("/") == "/api/v1/auth/bootstrap":
                # The dummy session is page-visible only after native admission.
                data = b'{"token":"hr_native_session","user":"founder","role":"admin"}'
                source = self.Gio.MemoryInputStream.new_from_bytes(self.GLib.Bytes.new(data))
                self.GLib.idle_add(self.deliver, request, transfer, 200, source,
                                  {"content-type": "application/json", "content-length": str(len(data))})
                return
            body = bytearray()
            began = time.monotonic()
            if body_stream is not None:
                while True:
                    chunk = body_stream.read_bytes(65536, transfer.cancellable).get_data()
                    if transfer.stop.is_set():
                        raise transfer.interrupted()
                    if time.monotonic() - began > 10:
                        raise SchemeError(408, "body_timeout")
                    if not chunk:
                        break
                    body.extend(chunk)
                    if len(body) > MAX_BODY:
                        raise SchemeError(413, "body_too_large")
            if transfer.stop.is_set():
                raise transfer.interrupted()
            connection = daemon.connection()
            transfer.connection = connection
            connection.before_send = transfer.before_send
            with closing(connection):
                if transfer.stop.is_set():
                    raise transfer.interrupted()
                response = daemon.send(connection, method, target, headers, bytes(body))
                with response:
                    kept = {k.lower(): v for k, v in response.getheaders() if k.lower() in RESPONSE_HEADERS}
                    # Fetch retains the attachment header for the existing blob
                    # path. Direct navigation is blocked by policy below.
                    # A plain pipe can only report EOF. On Linux an AF_UNIX
                    # peer closed with unread input reports ECONNRESET after
                    # queued output, which GIO/WebKit propagate as a body error.
                    # Drain this completion marker ONLY after validated EOF.
                    reader, writer = socket.socketpair()
                    reader.sendall(b"\0")
                    writer.setblocking(False)
                    write_fd = writer.fileno()
                    transfer.write_fd = write_fd
                    source = self.Gio.UnixInputStream.new(reader.detach(), True)
                    length = response.length
                    if length is not None:
                        kept["content-length"] = str(length)
                    transfer.deadline = time.monotonic() + 3600
                    self.GLib.idle_add(self.deliver, request, transfer, response.status, source, kept)
                    delivered_headers = True
                    # read1 delivers available bytes; read(n) can buffer SSE.
                    # HTTPConnection closes its socket reference on Connection:
                    # close responses, so retain the response socket for abort.
                    upstream_socket = response.socket if hasattr(response, "socket") else response.fp.raw._sock
                    # Quiet SSE is healthy. Shutdown from abort/monitor wakes
                    # this blocking read; the lifetime deadline still bounds it.
                    is_events = kept.get("content-type", "").split(";", 1)[0].strip().lower() == "text/event-stream"
                    upstream_socket.settimeout(None if is_events else 30)
                    transfer.socket = upstream_socket
                    if transfer.stop.is_set():
                        transfer.cancel()
                    delivered = 0
                    while not transfer.stop.is_set():
                        chunk = response.read1(65536)
                        if not chunk:
                            if length is not None and delivered != length:
                                raise SchemeError(502, "incomplete_body")
                            complete = not transfer.stop.is_set()
                            break
                        delivered += len(chunk)
                        pending = memoryview(chunk)
                        deadline = time.monotonic() + 10
                        while pending and not transfer.stop.is_set():
                            if time.monotonic() > deadline:
                                raise SchemeError(504, "reader_timeout")
                            if not select.select([], [write_fd], [], 0.1)[1]:
                                continue
                            try:
                                pending = pending[os.write(write_fd, pending):]
                            except BlockingIOError:
                                continue
        except SchemeError as error:
            if not delivered_headers:
                if transfer.stop.is_set() or error.code == "target_changed" and transfer.forwarded:
                    error = transfer.interrupted()
                self.GLib.idle_add(self.deliver_error, request, transfer, error)
        except (OSError, http.client.HTTPException, RemoteError, self.GLib.Error):
            if not delivered_headers:
                error = (transfer.interrupted() if transfer.stop.is_set() else
                         SchemeError(502, "incomplete_body") if transfer.forwarded else
                         SchemeError(503, "target_unreachable"))
                with self.target_lock:
                    if (error.code == "target_unreachable" and transfer.generation == self.generation
                            and transfer.target is self.daemon and self.daemon is not None):
                        with self.daemon._state:
                            self.daemon.state = "unreachable"
                self.GLib.idle_add(self.deliver_error, request, transfer, error)
        finally:
            if body_stream is not None:
                try:
                    body_stream.close(None)
                except self.GLib.Error:
                    pass
            transfer.write_fd = None
            with transfer.send_lock:
                if writer is not None:
                    if complete and not transfer.stop.is_set():
                        # Serialize successful EOF with target cancellation.
                        # Every interrupted transfer retains the error marker.
                        try:
                            writer.recv(1)
                        except OSError:
                            pass
                    writer.close()
                transfer.finished = True
            document, generation = self.document, transfer.generation
            def changed() -> bool:
                if document == self.document and generation == self.generation:
                    self.emit_state()
                return False
            self.GLib.idle_add(changed)

    def deliver_error(self, request: object, transfer: Transfer, error: SchemeError) -> bool:
        if transfer.generation != self.generation:
            transfer.cancel("target_changed")
        self.error(request, transfer.interrupted() if transfer.stop.is_set() else error)
        return False

    def deliver(self, request: object, transfer: Transfer, status: int, source: object,
                headers: dict[str, str]) -> bool:
        if transfer.generation != self.generation:
            transfer.cancel("target_changed")
        if transfer.stop.is_set() or self.closed:
            source.close(None)
            self.error(request, transfer.interrupted())
        else:
            self.finish(request, status, source, int(headers.get("content-length", "-1")), headers)
        return False

    def policy(self, _view: object, decision: object, kind: object) -> bool:
        if kind == self.W.PolicyDecisionType.NEW_WINDOW_ACTION:
            self.invalidate_document()
            decision.ignore()
            return True
        if kind in (self.W.PolicyDecisionType.NAVIGATION_ACTION, self.W.PolicyDecisionType.NEW_WINDOW_ACTION):
            uri = decision.get_navigation_action().get_request().get_uri()
            url = urlsplit(uri)
            if url.scheme == "blob":
                return False  # Existing fetch -> blob -> a.download route.
            if url.scheme != "happyranch-app" or url.netloc != "app":
                self.invalidate_document()
                decision.ignore()
                if url.scheme in ("http", "https"):
                    self.Gio.AppInfo.launch_default_for_uri(uri, None)
                return True
            if url.path.startswith("/api/"):
                decision.ignore()  # Direct attachment navigation is unreliable.
                return True
        return False

    def download_started(self, _context: object, download: object) -> None:
        def destination(_download: object, suggested: str) -> bool:
            title = "保存" if os.environ.get("LANG", "").lower().startswith("zh") else "Save"
            dialog = self.Gtk.FileChooserDialog(title=title, transient_for=self.view.get_toplevel(),
                                                 action=self.Gtk.FileChooserAction.SAVE)
            dialog.add_buttons(self.Gtk.STOCK_CANCEL, self.Gtk.ResponseType.CANCEL,
                               self.Gtk.STOCK_SAVE, self.Gtk.ResponseType.ACCEPT)
            dialog.set_do_overwrite_confirmation(True)
            dialog.set_current_name(Path(suggested).name or "download")
            folder = self.GLib.get_user_special_dir(self.GLib.UserDirectory.DIRECTORY_DOWNLOAD)
            if folder:
                dialog.set_current_folder(folder)
            if dialog.run() == self.Gtk.ResponseType.ACCEPT:
                download.set_destination(Path(dialog.get_filename()).as_uri())
                download.set_allow_overwrite(True)  # Only after chooser confirmation.
            else:
                download.cancel()
            dialog.destroy()
            return True
        download.connect("decide-destination", destination)

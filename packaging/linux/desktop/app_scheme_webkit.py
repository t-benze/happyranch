"""WebKitGTK 4.1 loader for the opt-in, local-only packaged app scheme."""
from __future__ import annotations

import http.client
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

# Document-start, top-frame fetch adaptation. Raw Blob/FormData must never reach
# URISchemeRequest.get_http_body (S0: WebKitGTK 2.52.6 SIGSEGV). This also makes
# today's bundle usable before S1a's web-side byte adapter lands. Unadapted XHR /
# form-navigation bodies are rejected before touching WebKit's body API.
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
      if (sent) window.webkit.messageHandlers.appSchemeCancel.postMessage(id);
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


class Transfer:
    def __init__(self) -> None:
        self.stop = threading.Event()
        self.cancellable = None
        self.connection = None
        self.socket = None
        self.has_body = False
        self.write_fd: int | None = None
        self.deadline = time.monotonic() + 15
        self.finished = False

    def cancel(self) -> None:
        self.stop.set()
        if self.cancellable is not None:
            self.cancellable.cancel()
        upstream = self.socket or (self.connection.sock if self.connection is not None else None)
        if upstream is not None:
            try:
                upstream.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


class AppScheme:
    """One context/view and bounded transfers; no native connection bridge."""

    def __init__(self, bundle: Path, verified_port: int, data: Path) -> None:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("WebKit2", "4.1")
        gi.require_version("Soup", "3.0")
        from gi.repository import Gio, GLib, Gtk, Soup, WebKit2
        if ((WebKit2.get_major_version(), WebKit2.get_minor_version()) < (2, 40)
                or not callable(getattr(WebKit2.URISchemeRequest, "get_http_body", None))):
            raise ValueError("WebKitGTK 2.40 or newer is required")
        self.Gio, self.GLib, self.Gtk, self.Soup, self.W = Gio, GLib, Gtk, Soup, WebKit2
        self.bundle, self.daemon = Bundle(bundle), LocalDaemon(verified_port)
        self.closed = False
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
        controller.register_script_message_handler("appSchemeCancel")
        controller.connect("script-message-received::appSchemeCancel", self.cancel_request)
        controller.add_script(WebKit2.UserScript.new(FETCH_BYTES, WebKit2.UserContentInjectedFrames.TOP_FRAME,
                                                     WebKit2.UserScriptInjectionTime.START, None, None))
        self.view = WebKit2.WebView(web_context=self.context, user_content_manager=controller)
        self.view.connect("destroy", lambda *_: self.close())
        self.view.connect("web-process-terminated", lambda *_: self.cancel_all())
        self.view.connect("load-changed", self.load_changed)
        self.view.connect("decide-policy", self.policy)
        self.context.connect("download-started", self.download_started)
        self.timer = GLib.timeout_add(100, self.monitor)

    def cancel_all(self) -> None:
        for transfer in self.transfers.values():
            transfer.cancel()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.cancel_all()
        self.GLib.source_remove(self.timer)
        self.daemon._bearer = None

    def load_changed(self, _view: object, event: object) -> None:
        if event == self.W.LoadEvent.STARTED:
            self.cancel_all()

    def cancel_request(self, _controller: object, message: object) -> None:
        key = message.get_js_value().to_string()
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
        self.reply(request, error.status, json.dumps({"detail": {"code": error.code}}).encode())

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
                # Never relay the real bootstrap JSON into the page.
                self.reply(request, 200, b'{"token":"hr_native_session","user":"founder","role":"admin"}')
                return
            headers: dict[str, str] = {}
            request.get_http_headers().foreach(lambda k, v, *_: headers.__setitem__(k.lower(), v), None)
            if sum(len(k) + len(v) for k, v in headers.items()) > 65536:
                raise SchemeError(431, "headers_too_large")
            key = headers.get("x-happyranch-request-id", "")
            if len(key) != 36 or key in self.transfers:
                raise SchemeError(400, "invalid_request_id")
            if len(self.transfers) >= 32:
                raise SchemeError(503, "busy")
            if (headers.get(BODY_HEADER.lower()) == "1"
                    and sum(t.has_body and not t.finished for t in self.transfers.values()) >= 2):
                raise SchemeError(503, "busy")
            # Even a zero-byte POST must be adapted. Do not inspect a raw body.
            if method not in ("GET", "HEAD") and headers.get(BODY_HEADER.lower()) != "1":
                raise SchemeError(415, "byte_body_required")
            transfer = Transfer()
            transfer.has_body = headers.get(BODY_HEADER.lower()) == "1"
            transfer.cancellable = self.Gio.Cancellable()
            self.transfers[key] = transfer
            if key in self.cancelled:
                transfer.cancel()
            # Only byte-adapted requests can enter WebKit's body API.
            body_stream = request.get_http_body() if headers.get(BODY_HEADER.lower()) == "1" else None
            threading.Thread(target=self.forward, args=(request, transfer, method, target, headers, body_stream),
                             daemon=True, name="app-scheme-local").start()
        except SchemeError as error:
            self.error(request, error)
        except (OSError, ValueError, self.GLib.Error):
            if transfer is not None:
                transfer.finished = True
            self.error(request, SchemeError(500, "bundle_unavailable"))

    def forward(self, request: object, transfer: Transfer, method: str, target: str,
                headers: dict[str, str], body_stream: object) -> None:
        sent = False
        write_fd = None
        try:
            body = bytearray()
            began = time.monotonic()
            if body_stream is not None:
                while True:
                    chunk = body_stream.read_bytes(65536, transfer.cancellable).get_data()
                    if transfer.stop.is_set():
                        raise SchemeError(499, "cancelled")
                    if time.monotonic() - began > 10:
                        raise SchemeError(408, "body_timeout")
                    if not chunk:
                        break
                    body.extend(chunk)
                    if len(body) > MAX_BODY:
                        raise SchemeError(413, "body_too_large")
            if transfer.stop.is_set():
                raise SchemeError(499, "cancelled")
            self.daemon.bootstrap(transfer.stop)
            connection = self.daemon.connection()
            transfer.connection = connection
            with closing(connection):
                if transfer.stop.is_set():
                    raise SchemeError(499, "cancelled")
                response = self.daemon.send(connection, method, target, headers, bytes(body))
                with response:
                    kept = {k.lower(): v for k, v in response.getheaders() if k.lower() in RESPONSE_HEADERS}
                    # Fetch retains the attachment header for the existing blob
                    # path. Direct navigation is blocked by policy below.
                    read_fd, write_fd = os.pipe()
                    os.set_blocking(write_fd, False)
                    transfer.write_fd = write_fd
                    source = self.Gio.UnixInputStream.new(read_fd, True)
                    transfer.deadline = time.monotonic() + 3600
                    self.GLib.idle_add(self.deliver, request, transfer, response.status, source, kept)
                    sent = True
                    # read1 delivers available bytes; read(n) can buffer SSE.
                    # HTTPConnection closes its socket reference on Connection:
                    # close responses, so retain the response socket for abort.
                    upstream_socket = response.fp.raw._sock
                    upstream_socket.settimeout(30)
                    transfer.socket = upstream_socket
                    while not transfer.stop.is_set():
                        chunk = response.read1(65536)
                        if not chunk:
                            break
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
            if not sent:
                self.GLib.idle_add(self.error, request, error)
        except (OSError, http.client.HTTPException, self.GLib.Error):
            if not sent:
                self.GLib.idle_add(self.error, request, SchemeError(502, "upstream_unavailable"))
        finally:
            if body_stream is not None:
                try:
                    body_stream.close(None)
                except self.GLib.Error:
                    pass
            transfer.write_fd = None
            if write_fd is not None:
                os.close(write_fd)
            transfer.finished = True

    def deliver(self, request: object, transfer: Transfer, status: int, source: object,
                headers: dict[str, str]) -> bool:
        if transfer.stop.is_set() or self.closed:
            source.close(None)
            self.error(request, SchemeError(499, "cancelled"))
        else:
            self.finish(request, status, source, -1, headers)
        return False

    def policy(self, _view: object, decision: object, kind: object) -> bool:
        if kind in (self.W.PolicyDecisionType.NAVIGATION_ACTION, self.W.PolicyDecisionType.NEW_WINDOW_ACTION):
            uri = decision.get_navigation_action().get_request().get_uri()
            url = urlsplit(uri)
            if url.scheme == "blob":
                return False  # Existing fetch -> blob -> a.download route.
            if url.scheme != "happyranch-app" or url.netloc != "app":
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

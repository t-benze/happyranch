"""Throwaway WebKitGTK custom-scheme observation. No daemon writes."""
from __future__ import annotations

import argparse
import email.parser
import email.policy
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import threading
import time
import urllib.parse
import urllib.request

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
gi.require_version("Soup", "3.0")
from gi.repository import Gio, GLib, Gtk, Soup, WebKit2  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DAEMON_STREAM = "/api/v1/orgs/happyranch/threads/THR-387/tail"


def emit(name: str, **details: object) -> None:
    print(json.dumps({"name": name, "monotonic": time.monotonic(), "wallMs":time.time() * 1000, **details}), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--path", default="/")
    parser.add_argument("--host", default="app", choices=("app", "other"))
    parser.add_argument("--spa", action="store_true")
    parser.add_argument("--daemon-port", type=int, default=8765)
    args = parser.parse_args()
    args.data.mkdir(parents=True, exist_ok=True)
    emit("venue", webkit=[WebKit2.get_major_version(), WebKit2.get_minor_version(), WebKit2.get_micro_version()],
         display=os.environ.get("DISPLAY"), spa=args.spa, path=args.path)
    manager = WebKit2.WebsiteDataManager(base_data_directory=str(args.data / "data"),
                                         base_cache_directory=str(args.data / "cache"))
    context = WebKit2.WebContext.new_with_website_data_manager(manager)
    security = context.get_security_manager()
    security.register_uri_scheme_as_secure("happyranch-app")
    security.register_uri_scheme_as_cors_enabled("happyranch-app")
    # Do not mark local: local schemes receive special cross-origin privileges.
    controller = WebKit2.UserContentManager()
    controller.register_script_message_handler("probe")
    view = WebKit2.WebView(web_context=context, user_content_manager=controller)
    window = Gtk.Window()
    window.set_default_size(1000, 700)
    window.add(view)
    retained = []

    def received(_controller: object, result: object) -> None:
        record = json.loads(result.get_js_value().to_string())
        emit("js", record=record)
        if record["name"] == "done":
            GLib.timeout_add(3000, Gtk.main_quit)

    controller.connect("script-message-received::probe", received)

    def reply(request: object, data: bytes, mime: str, attachment: bool = False) -> None:
        response = WebKit2.URISchemeResponse.new(Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(data)), len(data))
        response.set_status(200, "OK")
        response.set_content_type(mime)
        headers = Soup.MessageHeaders.new(Soup.MessageHeadersType.RESPONSE)
        headers.append("Content-Type", mime)
        if attachment:
            headers.append("Content-Disposition", 'attachment; filename="s0.txt"')
        response.set_http_headers(headers)
        request.finish_with_response(response)

    def scheme(request: object) -> None:
        path = urllib.parse.urlsplit(request.get_uri()).path
        emit("request", path=path, method=request.get_http_method())
        if path == "/probe/body":
            emit("body-get-stream")
            stream = request.get_http_body()
            emit("body-got-stream", present=stream is not None)
            body = bytearray()
            if stream:
                while chunk := stream.read_bytes(65536, None).get_data():
                    body.extend(chunk)
                    if len(body) > 12 * 1024 * 1024:
                        raise ValueError("probe body limit")
            emit("body-read-complete", length=len(body))
            content_type = request.get_http_headers().get_one("Content-Type") or ""
            result = {"length": len(body), "sha256": hashlib.sha256(body).hexdigest()}
            if content_type.startswith("multipart/"):
                message = email.parser.BytesParser(policy=email.policy.default).parsebytes(
                    f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body)
                files = [part.get_payload(decode=True) for part in message.iter_parts()]
                result.update(fileLengths=[len(x) for x in files], intact=files == [b"s" * (10 * 1024 * 1024)])
            else:
                result["text"] = body.decode("utf-8")
            emit("body", **result)
            reply(request, json.dumps(result).encode(), "application/json")
        elif path in ("/probe/stream", "/probe/cancel", DAEMON_STREAM):
            read_fd, write_fd = os.pipe()
            source = Gio.UnixInputStream.new(read_fd, True)
            response = WebKit2.URISchemeResponse.new(source, -1)
            response.set_status(200, "OK")
            response.set_content_type("text/event-stream")
            headers = Soup.MessageHeaders.new(Soup.MessageHeadersType.RESPONSE)
            headers.append("Content-Type", "text/event-stream")
            response.set_http_headers(headers)
            request.finish_with_response(response)

            def produce() -> None:
                began = time.monotonic()
                try:
                    if path == DAEMON_STREAM:
                        with daemon("/api/v1/orgs/happyranch/threads/THR-387/tail?since_seq=0", authenticated=True) as remote:
                            emit("daemon-sse-open", status=remote.status, contentType=remote.headers.get("Content-Type"))
                            # Read complete SSE lines; do not retain any content.
                            while data := remote.readline():
                                os.write(write_fd, data)
                    else:
                        for i in range(5 if path.endswith("stream") else 10):
                            now = time.monotonic()
                            os.write(write_fd, ("data: " + json.dumps({"i": i, "nativeMs": (now - began) * 1000,
                                                                    "nativeWallMs": time.time() * 1000}) + "\n\n").encode())
                            emit("chunk-written", path=path, i=i, elapsedMs=(now - began) * 1000)
                            time.sleep(1)
                except BrokenPipeError:
                    emit("stream-cancelled", path=path, elapsedMs=(time.monotonic() - began) * 1000)
                except Exception as exc:
                    emit("stream-error", path=path, category=type(exc).__name__)
                finally:
                    os.close(write_fd)

            threading.Thread(target=produce, daemon=True).start()
        elif path == "/api/v1/health":
            try:
                with daemon(path) as response:
                    data = response.read()
                    emit("daemon-health-native", status=response.status)
                    reply(request, data, "application/json")
            except Exception as exc:
                reply(request, json.dumps({"unavailable": type(exc).__name__}).encode(), "application/json")
        elif path == "/probe/download":
            reply(request, b"s0 download\n", "application/octet-stream", True)
        elif path == "/probe/page":
            reply(request, b'<html><body>S0<script type="module" src="/probe/probe.js"></script></body></html>', "text/html")
        elif path == "/probe/probe.js":
            reply(request, (HERE / "probe.js").read_bytes(), "text/javascript")
        elif path == "/probe/fetch-event-source.js":
            reply(request, (ROOT / "web/node_modules/@microsoft/fetch-event-source/lib/esm/index.js").read_bytes(), "text/javascript")
        elif path in ("/probe/fetch.js", "/probe/parse.js", "/probe/fetch", "/probe/parse"):
            reply(request, (ROOT / "web/node_modules/@microsoft/fetch-event-source/lib/esm" / (Path(path).stem + ".js")).read_bytes(), "text/javascript")
        else:
            file = (ROOT / "web/dist" / path.lstrip("/")).resolve()
            if not file.is_relative_to((ROOT / "web/dist").resolve()):
                return
            if not file.is_file():
                file = ROOT / "web/dist/index.html"
            reply(request, file.read_bytes(), mimetypes.guess_type(str(file))[0] or "application/octet-stream")

    def daemon(path: str, authenticated: bool = False) -> object:
        base = f"http://127.0.0.1:{args.daemon_port}"
        headers = {}
        if authenticated:
            # Native-memory-only bearer; never send it to the probe page or logs.
            with urllib.request.urlopen(base + "/api/v1/auth/bootstrap", timeout=3) as response:
                token = json.load(response)["token"]
            headers["Authorization"] = "Bearer " + token
        return urllib.request.urlopen(urllib.request.Request(base + path, headers=headers), timeout=6)

    def download_started(_context: object, download: object) -> None:
        retained.append(download)
        emit("download-started")

        def destination(_download: object, filename: str) -> bool:
            download.set_destination((args.data / filename).as_uri())
            download.set_allow_overwrite(True)
            return True

        download.connect("decide-destination", destination)
        download.connect("finished", lambda d: emit("download-finished", bytes=d.get_received_data_length()))
        download.connect("failed", lambda _d, _e: emit("download-failed"))

    context.connect("download-started", download_started)
    context.register_uri_scheme("happyranch-app", scheme)
    def policy(_view: object, decision: object, kind: object) -> bool:
        if kind == WebKit2.PolicyDecisionType.RESPONSE:
            headers = decision.get_response().get_http_headers()
            disposition = headers.get_one("Content-Disposition") if headers else None
            if disposition and disposition.startswith("attachment"):
                emit("attachment-policy-download", disposition=disposition)
                decision.download()
                return True
        return False
    view.connect("decide-policy", policy)
    view.connect("load-changed", lambda _v, event: emit("load", event=event.value_nick, uri=view.get_uri()))
    view.connect("load-failed", lambda _v, _e, uri, error: emit("load-failed", uri=uri, category=str(error)))
    if args.spa:
        def inspect() -> bool:
            view.evaluate_javascript("JSON.stringify({path:location.pathname,root:document.getElementById('root').childElementCount,text:document.body.innerText.slice(0,100)})", -1, None, None, None,
                                     lambda v, r, _data: emit("spa-dom", value=v.evaluate_javascript_finish(r).to_string()), None)
            GLib.timeout_add(1000, Gtk.main_quit)
            return False
        GLib.timeout_add(7000, inspect)
    GLib.timeout_add_seconds(35, Gtk.main_quit)
    window.show_all()
    view.load_uri(f"happyranch-app://{args.host}" + (args.path if args.spa or args.path != "/" else "/probe/page"))
    Gtk.main()
    emit("exit")


if __name__ == "__main__":
    main()

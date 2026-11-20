"""Local-only transport for the opt-in packaged UI. No GTK or daemon imports."""
from __future__ import annotations

import hashlib
from contextlib import closing
import http.client
import json
import mimetypes
import os
from pathlib import Path
import re
import stat
import threading
import time
from urllib.parse import unquote, urlsplit

ORIGIN = "happyranch-app://app"
MAX_BODY = 64 * 1024 * 1024
BODY_HEADER = "X-HappyRanch-Body-Bytes"
CSP = ("default-src 'none'; script-src 'self'; connect-src 'self'; "
       "img-src 'self' data: blob:; font-src 'self'; style-src 'self' 'unsafe-inline'; "
       "frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'")
# An allowlist, rather than an ever-growing list of identity headers to strip.
REQUEST_HEADERS = frozenset({"accept", "accept-language", "content-type", "if-match",
                             "if-none-match", "if-modified-since", "range", "last-event-id"})
RESPONSE_HEADERS = frozenset({"content-type", "content-disposition", "content-range",
                              "accept-ranges", "etag", "last-modified", "retry-after"})
METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})


class SchemeError(Exception):
    """Closed diagnostic; never include an upstream exception or request data."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status, self.code = status, code


def route(uri: str) -> tuple[str, str]:
    if len(uri) > 16384 or any(ord(c) < 32 or ord(c) == 127 for c in uri):
        raise SchemeError(400, "invalid_path")
    try:
        url = urlsplit(uri)
        if url.scheme != "happyranch-app" or url.netloc != "app" or url.fragment:
            raise ValueError
        path = unquote(url.path or "/", errors="strict")
        if (re.search(r"%(?![0-9a-fA-F]{2})", url.path) or "%" in path
                or "\\" in path or not path.startswith("/") or "//" in path
                or any(ord(c) < 32 or ord(c) == 127 for c in path)
                or any(p in (".", "..") for p in path.split("/"))):
            raise ValueError
        # http.client requires an ASCII request target; retain URI escaping.
        target = url.path or "/"
        if url.query:
            target += "?" + url.query
        target.encode("ascii")
        return path, target
    except (ValueError, UnicodeError):
        raise SchemeError(400, "invalid_path") from None


def request_headers(headers: dict[str, str]) -> dict[str, str]:
    connection = {s.strip().lower() for s in headers.get("connection", "").split(",")}
    kept = {k: v for k, v in headers.items() if k.lower() in REQUEST_HEADERS
            and k.lower() not in connection}
    if any("\r" in v or "\n" in v for v in kept.values()):
        raise SchemeError(400, "invalid_headers")
    return kept


class Bundle:
    """Startup file allowlist; descriptor-relative no-follow reads at delivery.

    S1a supplies a build manifest separately. Until cutover, inventory the actual
    packaged dist without trusting URL paths or following any symlink. Hashes pin
    the inventoried bytes for this loader's lifetime, including SPA fallback.
    """

    def __init__(self, root: Path) -> None:
        self.root = root.absolute()
        if self.root.resolve(strict=True) != self.root:
            raise ValueError("aliased bundle root")
        self.files: dict[str, str] = {}
        self.directories: set[str] = set()
        total = 0
        for directory, dirs, files in os.walk(self.root, followlinks=False):
            if any((Path(directory) / d).is_symlink() for d in dirs):
                raise ValueError("symlink in bundle")
            self.directories.add(Path(directory).relative_to(self.root).as_posix())
            if len(self.directories) > 4096:
                raise ValueError("bundle directory limit")
            for name in files:
                relative = (Path(directory) / name).relative_to(self.root).as_posix()
                data = self._read(relative)
                total += len(data)
                if len(self.files) >= 4096 or total > 256 * 1024 * 1024:
                    raise ValueError("bundle limit")
                self.files[relative] = hashlib.sha256(data).hexdigest()
        if "index.html" not in self.files:
            raise ValueError("missing bundle entry")

    def _read(self, relative: str) -> bytes:
        if self.root.resolve(strict=True) != self.root:
            raise ValueError("aliased bundle root")
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            parts = relative.split("/")
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            leaf = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(leaf, "rb") as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BODY:
                    raise ValueError("invalid bundle file")
                data = source.read(MAX_BODY + 1)
                if len(data) > MAX_BODY:
                    raise ValueError("bundle file limit")
                return data
        finally:
            os.close(fd)

    def get(self, path: str) -> tuple[bytes, str]:
        # Public boundary also validates when called independently of WebKit.
        route(ORIGIN + path)
        if path == "/api" or path.startswith("/api/"):
            raise SchemeError(404, "not_found")
        name = path.lstrip("/") or "index.html"
        if name in self.directories or path.endswith("/") and path != "/":
            raise SchemeError(404, "not_found")
        if name not in self.files:
            name = "index.html"
        try:
            data = self._read(name)
            if hashlib.sha256(data).hexdigest() != self.files[name]:
                raise ValueError("changed asset")
        except (OSError, ValueError):
            raise SchemeError(404, "not_found") from None
        mime = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
                ".woff2": "font/woff2", ".woff": "font/woff", ".svg": "image/svg+xml"}.get(
                    Path(name).suffix, mimetypes.guess_type(name)[0] or "application/octet-stream")
        return data, mime


class LocalDaemon:
    """Native bootstrap and single-hop HTTP to an already verified local port.

    No proxy environment, redirect following, retries, token persistence or
    page-visible credentials. A new loader bootstraps again after reattachment.
    """

    def __init__(self, port: int) -> None:
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("invalid verified port")
        self.port = port
        self._bearer: str | None = None
        self._lock = threading.Lock()

    def connection(self) -> http.client.HTTPConnection:
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)

    def bootstrap(self, stop: threading.Event | None = None) -> None:
        deadline = time.monotonic() + 10
        while not self._lock.acquire(timeout=0.1):
            if time.monotonic() >= deadline or stop is not None and stop.is_set():
                raise SchemeError(503, "target_unreachable")
        try:
            if self._bearer is not None:
                return
            try:
                with closing(self.connection()) as connection:
                    connection.request("GET", "/api/v1/auth/bootstrap", headers={"Connection": "close"})
                    response = connection.getresponse()
                    data = bytearray()
                    while True:
                        if time.monotonic() >= deadline or stop is not None and stop.is_set():
                            raise ValueError
                        chunk = response.read1(16385 - len(data))
                        data.extend(chunk)
                        if not chunk or len(data) >= 16385:
                            break
                    if response.status != 200 or len(data) > 16384:
                        raise ValueError
                    token = json.loads(data)["token"]
                    if (not isinstance(token, str) or not token or len(token) > 4096
                            or not token.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in token)):
                        raise ValueError
                    self._bearer = token
            except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
                raise SchemeError(503, "target_unreachable") from None
        finally:
            self._lock.release()

    def send(self, connection: http.client.HTTPConnection, method: str, target: str,
             headers: dict[str, str], body: bytes) -> http.client.HTTPResponse:
        path, checked_target = route(ORIGIN + target)
        if not path.startswith("/api/v1/") or method not in METHODS:
            raise SchemeError(400, "invalid_request")
        if path.rstrip("/") == "/api/v1/auth/bootstrap":
            raise SchemeError(400, "native_bootstrap_only")
        if len(body) > MAX_BODY:
            raise SchemeError(413, "body_too_large")
        if self._bearer is None:
            raise SchemeError(503, "target_unreachable")
        kept = request_headers({k.lower(): v for k, v in headers.items()})
        kept.update({"Authorization": "Bearer " + self._bearer,
                     "Connection": "close", "Accept-Encoding": "identity"})
        connection.request(method, checked_target, body=body, headers=kept)
        response = connection.getresponse()
        # Never let WebKit follow an upstream redirect with native authority.
        if 300 <= response.status < 400 and response.status != 304:
            response.close()
            raise SchemeError(502, "upstream_redirect")
        return response

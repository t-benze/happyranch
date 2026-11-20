"""Native-selected local/remote transport for the opt-in packaged UI. No GTK or daemon imports."""
from __future__ import annotations

import hashlib
import importlib.util
import http.client
import json
import mimetypes
import os
from pathlib import Path
import re
import stat
import socket
import threading
import time
from urllib.parse import unquote, urlsplit

# Installed system Python uses isolated imports; resolve this pure sibling.
_spec = importlib.util.spec_from_file_location("client_runtime", Path(__file__).with_name("client_runtime.py"))
_runtime = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_runtime)
RemoteError = _runtime.ClientError

ORIGIN = "happyranch-app://app"
CLIENT = MIN_DAEMON = 1
MAX_HEALTH = 64 * 1024
HEALTH_TIMEOUT = 5
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

    def __init__(self, status: int, code: str, side: str | None = None) -> None:
        super().__init__(code)
        self.status, self.code = status, code
        if side is not None and (code != "incompatible" or side not in {"host", "app", "unknown"}):
            raise ValueError("invalid error side")
        self.side = side


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


class _Target:
    """Serialize admission per native target; reset invalidates in-flight probes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = threading.Lock()
        self._epoch = threading.Event()
        self._compatibility: str | None = None  # None unchecked, empty compatible, else side
        self._bearer: str | None = None

    def reset(self) -> None:
        with self._state:
            self._epoch.set()
            self._epoch = threading.Event()
            self._compatibility = None
            self._bearer = None

    @staticmethod
    def _check(stop: threading.Event | None, epoch: threading.Event) -> None:
        if stop is not None and stop.is_set():
            raise SchemeError(499, "cancelled")
        if epoch.is_set():
            raise SchemeError(409, "target_changed")

    def _json(self, path: str, limit: int, deadline: float, stop: threading.Event | None,
              epoch: threading.Event, *, health: bool = False) -> object:
        connection = self.connection()
        response = None
        reached = False
        done = threading.Event()

        def interrupt() -> None:
            # Wake blocked headers/body/dial on cancellation AND the original
            # deadline. HTTPConnection may relinquish its socket after headers.
            while not done.wait(0.02):
                if (epoch.is_set() or stop is not None and stop.is_set()
                        or time.monotonic() >= deadline):
                    stream = getattr(connection, "sock", None)
                    if response is not None:
                        stream = (getattr(response, "socket", None)
                                  or getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", stream))
                    if stream is not None:
                        try:
                            stream.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                    # HTTPResponse owns its file wrapper while read1 runs;
                    # concurrent HTTPConnection.close can close it twice.
                    # Shutdown wakes the reader; its owner closes in finally.
                    if not isinstance(connection, http.client.HTTPConnection):
                        connection.close()
                    return

        watcher = threading.Thread(target=interrupt, daemon=True, name="app-scheme-admission")
        watcher.start()
        try:
            self._check(stop, epoch)
            if time.monotonic() >= deadline:
                raise ValueError
            if isinstance(connection, http.client.HTTPConnection):
                connection.timeout = max(0.001, deadline - time.monotonic())
                connection.connect()
                reached = True
                self._check(stop, epoch)
                connection.request("GET", path, headers={"Connection": "close", "Accept-Encoding": "identity"})
                response = connection.getresponse()
            else:
                try:
                    response = connection.send("GET", path, {}, b"", deadline=deadline)
                except RemoteError as error:
                    # Dial/credential failures are translated by RemoteCall;
                    # framing/deadline errors occur after the pinned dial.
                    reached = str(error) != "unavailable"
                    raise
                except OSError:
                    reached = True
                    raise
                reached = True
            if response.status != 200 or response.length is not None and response.length > limit:
                raise ValueError
            expected = response.length
            data = bytearray()
            while True:
                self._check(stop, epoch)
                if time.monotonic() >= deadline:
                    raise ValueError
                chunk = response.read1(min(16384, limit + 1 - len(data)))
                data.extend(chunk)
                if len(data) > limit:
                    raise ValueError
                if not chunk:
                    break
            if expected is not None and len(data) != expected:
                raise ValueError
            value = json.loads(data)
            self._check(stop, epoch)
            if time.monotonic() >= deadline:
                raise ValueError
            return value
        except (OSError, ValueError, TypeError, RecursionError, http.client.HTTPException, RemoteError):
            self._check(stop, epoch)
            if health and reached:
                raise SchemeError(503, "incompatible", "host") from None
            raise SchemeError(503, "target_unreachable") from None
        finally:
            done.set()
            watcher.join()
            if response is not None:
                response.close()
            connection.close()

    def bootstrap(self, stop: threading.Event | None = None) -> None:
        with self._state:
            epoch = self._epoch
        deadline = time.monotonic() + HEALTH_TIMEOUT
        self._check(stop, epoch)
        while not self._lock.acquire(timeout=0.02):
            self._check(stop, epoch)
            if time.monotonic() >= deadline:
                raise SchemeError(503, "busy")
        try:
            self._check(stop, epoch)
            with self._state:
                side = self._compatibility
            if side is None:
                try:
                    value = self._json("/api/v1/health", MAX_HEALTH, deadline, stop, epoch, health=True)
                    contract = value.get("api_contract") if isinstance(value, dict) else None
                    if (not isinstance(contract, dict) or type(contract.get("current")) is not int
                            or type(contract.get("min_client")) is not int):
                        side = "host"
                    elif contract["min_client"] > CLIENT:
                        side = "app"
                    elif contract["current"] < MIN_DAEMON or not contract["min_client"] <= CLIENT <= contract["current"]:
                        side = "host"
                    else:
                        side = ""
                except SchemeError as error:
                    if error.code != "incompatible":
                        raise
                    side = error.side
                with self._state:
                    self._check(stop, epoch)
                    self._compatibility = side
            if side:
                raise SchemeError(503, "incompatible", side)
            self._prepare(stop, epoch)
            self._check(stop, epoch)
        finally:
            self._lock.release()

    def _prepare(self, stop: threading.Event | None, epoch: threading.Event) -> None:
        pass

    def _admitted(self) -> None:
        with self._state:
            if self._compatibility is None:
                raise SchemeError(503, "target_starting")
            if self._compatibility:
                raise SchemeError(503, "incompatible", self._compatibility)


class LocalDaemon(_Target):
    """Native bootstrap and single-hop HTTP to an already verified local port.

    No proxy environment, redirect following, retries, token persistence or
    page-visible credentials. A new loader bootstraps again after reattachment.
    """

    def __init__(self, port: int) -> None:
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("invalid verified port")
        super().__init__()
        self.port = port

    def connection(self) -> http.client.HTTPConnection:
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)

    def _prepare(self, stop: threading.Event | None, epoch: threading.Event) -> None:
        with self._state:
            if self._bearer is not None:
                return
        value = self._json("/api/v1/auth/bootstrap", 16384, time.monotonic() + 10, stop, epoch)
        token = value.get("token") if isinstance(value, dict) else None
        if (not isinstance(token, str) or not token or len(token) > 4096
                or not token.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in token)):
            raise SchemeError(503, "target_unreachable")
        with self._state:
            self._check(stop, epoch)
            self._bearer = token

    def send(self, connection: http.client.HTTPConnection, method: str, target: str,
             headers: dict[str, str], body: bytes) -> http.client.HTTPResponse:
        self._admitted()
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


class RemoteDaemon(_Target):
    """One explicitly selected native session; no enrollment or candidate switch.

    Connecting sessions remain selected but cannot dial until the native owner
    publishes ready. The session remains the sole pinned-peer/credential owner.
    """

    def __init__(self, session: object, *, ready: bool = False) -> None:
        super().__init__()
        self.session, self._ready = session, ready

    @property
    def ready(self) -> bool:
        return self._ready

    @ready.setter
    def ready(self, value: bool) -> None:
        if value != self._ready:
            self.reset()
        self._ready = value

    def bootstrap(self, stop: threading.Event | None = None) -> None:
        if not self.ready:
            raise SchemeError(503, "target_starting")
        super().bootstrap(stop)

    def connection(self) -> object:
        return _runtime.RemoteCall(self.session.dial, self.session.release, self.session._credential)

    def send(self, connection: object, method: str, target: str,
             headers: dict[str, str], body: bytes) -> object:
        self._admitted()
        path, checked = route(ORIGIN + target)
        if not path.startswith("/api/v1/") or method not in METHODS:
            raise SchemeError(400, "invalid_request")
        if path.rstrip("/") == "/api/v1/auth/bootstrap":
            raise SchemeError(400, "native_bootstrap_only")
        if len(body) > MAX_BODY:
            raise SchemeError(413, "body_too_large")
        try:
            response = connection.send(method, checked, headers, body)
        except (OSError, ValueError, RemoteError):
            raise SchemeError(503, "target_unreachable") from None
        if 300 <= response.status < 400 and response.status != 304:
            response.close()
            raise SchemeError(503, "target_unreachable")
        return response

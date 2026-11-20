"""Native runtime HTTP proxy. No command bridge, persistence, or bearer export."""
from __future__ import annotations

import io
import json
import re
import secrets
import select
import socket
import threading
import time
from typing import Callable


class ClientError(Exception):
    """Only closed native categories may leave this transport."""


def close_socket(stream: socket.socket) -> None:
    try:
        stream.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    stream.close()


class Wire:
    """Fragment-safe bounded framing; one original deadline for native requests."""

    def __init__(self, stream: socket.socket, deadline: float) -> None:
        self.stream, self.deadline = stream, deadline
        self.buffer = bytearray()
        self.idle_timeout: float | None = None

    def receive(self) -> bytes:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ClientError("deadline")
        self.stream.settimeout(min(remaining, self.idle_timeout) if self.idle_timeout else remaining)
        return self.stream.recv(65536)

    def until(self, delimiter: bytes, limit: int) -> bytes:
        while True:
            end = self.buffer.find(delimiter)
            if end >= 0:
                end += len(delimiter)
                if end > limit:
                    break
                result = bytes(self.buffer[:end])
                del self.buffer[:end]
                return result
            if len(self.buffer) >= limit:
                break
            data = self.receive()
            if not data:
                break
            self.buffer.extend(data)
        raise ClientError("protocol")

    def exact(self, count: int) -> bytes:
        while len(self.buffer) < count:
            data = self.receive()
            if not data:
                raise ClientError("protocol")
            self.buffer.extend(data)
        result = bytes(self.buffer[:count])
        del self.buffer[:count]
        return result

    def send(self, data: bytes) -> None:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ClientError("deadline")
        self.stream.settimeout(remaining)
        self.stream.sendall(data)

    def head(self, *, repeated: bool = False) -> tuple[str, dict[str, str]]:
        raw = self.until(b"\r\n\r\n", 32768)
        self.raw_head = raw
        try:
            lines = raw[:-4].decode("ascii").split("\r\n")
        except UnicodeError:
            raise ClientError("protocol") from None
        headers: dict[str, str] = {}
        if any(ord(c) < 32 or ord(c) == 127 for c in lines[0]):
            raise ClientError("protocol")
        for line in lines[1:]:
            name, sep, value = line.partition(":")
            if (not sep or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name)
                    or any(ord(c) < 32 or ord(c) == 127 for c in value)
                    or name.lower() in headers and (not repeated or name.lower() in
                        {"content-length", "transfer-encoding", "connection", "upgrade"})):
                raise ClientError("protocol")
            headers[name.lower()] = value.strip()
        if ("content-length" in headers and not re.fullmatch(r"[0-9]{1,12}", headers["content-length"])
                or "transfer-encoding" in headers and headers["transfer-encoding"].lower() != "chunked"
                or "content-length" in headers and "transfer-encoding" in headers):
            raise ClientError("protocol")
        return lines[0], headers

    def body(self, headers: dict[str, str], emit: Callable[[bytes], None], limit: int, *, raw_chunks: bool = True) -> None:
        """Stream exactly one framed body; never tunnel a following HTTP request."""
        if "transfer-encoding" in headers:
            total = 0
            while True:
                line = self.until(b"\r\n", 128)
                if not re.fullmatch(rb"[0-9a-fA-F]{1,8}\r\n", line):
                    raise ClientError("protocol")
                size = int(line, 16)
                total += size
                if total > limit:
                    raise ClientError("protocol")
                if raw_chunks:
                    emit(line)
                if size == 0:
                    if self.exact(2) != b"\r\n":  # no trailer authority/headers
                        raise ClientError("protocol")
                    if raw_chunks:
                        emit(b"\r\n")
                    return
                while size:
                    data = self.exact(min(size, 65536))
                    emit(data)
                    size -= len(data)
                if self.exact(2) != b"\r\n":
                    raise ClientError("protocol")
                if raw_chunks:
                    emit(b"\r\n")
        else:
            size = int(headers.get("content-length", "0"))
            if size > limit:
                raise ClientError("protocol")
            while size:
                data = self.exact(min(size, 65536))
                emit(data)
                size -= len(data)


def remote_headers(headers: dict[str, str], credential: str, *, upgrade: bool = False) -> dict[str, str]:
    """Shared native authority/framing policy for listener and in-process use."""
    connection = {item.strip().lower() for item in headers.get("connection", "").split(",")}
    stripped = {"authorization", "cookie", "proxy-authorization", "host", "connection",
                "keep-alive", "upgrade", "te", "trailer", "expect", "forwarded", "via", "x-real-ip"} | connection
    kept = {key: value for key, value in headers.items() if key not in stripped
            and not key.startswith(("x-happyranch-", "x-tailscale-", "tailscale-", "x-tailnet-", "x-forwarded-", "proxy-"))}
    # Framing cannot be nominated away by a Connection header.
    for key in ("content-length", "transfer-encoding"):
        if key in headers:
            kept[key] = headers[key]
    kept.update({"host": "home:443", "connection": "Upgrade" if upgrade else "close",
                 "x-happyranch-device-credential": credential})
    if upgrade:
        kept["upgrade"] = "websocket"
    return kept


MAX_BODY = 64 * 1024 * 1024


class RemoteCall:
    """Listener-free single HTTP exchange over an existing pinned-session dial.

    Native owns the three callbacks. close is independently callable, including
    during dial/upload/headers, and releases each acquired dial exactly once.
    """

    def __init__(self, dial: Callable[[float, Callable[[socket.socket], bool]], socket.socket],
                 release: Callable[[socket.socket], None], credential: Callable[[], str]) -> None:
        self._dial, self._release, self._credential = dial, release, credential
        self._lock = threading.Lock()
        self._closed = False
        self.sock: socket.socket | None = None

    def _acquire(self, stream: socket.socket) -> bool:
        with self._lock:
            if self._closed:
                return False
            self.sock = stream
            return True

    def close(self) -> None:
        with self._lock:
            self._closed = True
            stream, self.sock = self.sock, None
        if stream is not None:
            # Wake blocked readers before transferring final custody to session.
            try:
                stream.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._release(stream)

    cancel = close

    def send(self, method: str, target: str, headers: dict[str, str],
             body: bytes | io.BufferedIOBase) -> RemoteResponse:
        deadline = time.monotonic() + 10
        if (not re.fullmatch(r"[A-Z]+", method) or not target.startswith("/")
                or target.startswith("//") or "#" in target
                or any(ord(c) < 33 or ord(c) > 126 for c in target)):
            raise ClientError("protocol")
        headers = {k.lower(): v for k, v in headers.items()}
        if (sum(len(k) + len(v) + 4 for k, v in headers.items()) > 32768
                or any(not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", k)
                       or any(ord(c) < 32 or ord(c) > 126 for c in v) for k, v in headers.items())):
            raise ClientError("protocol")
        if not isinstance(body, bytes):
            data = bytearray()
            while True:
                with self._lock:
                    if self._closed:
                        raise ClientError("cancelled")
                if time.monotonic() >= deadline:
                    raise ClientError("deadline")
                chunk = body.read(min(65536, MAX_BODY + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > MAX_BODY:
                    raise ClientError("bodyTooLarge")
            body = bytes(data)
        if len(body) > MAX_BODY:
            raise ClientError("bodyTooLarge")
        if time.monotonic() >= deadline:
            raise ClientError("deadline")
        with self._lock:
            if self._closed:
                raise ClientError("cancelled")
        try:
            credential = self._credential()
        except Exception:
            raise ClientError("unavailable") from None
        kept = remote_headers(headers, credential)
        # Structured bodies are payload bytes, never caller-supplied HTTP framing.
        kept.pop("transfer-encoding", None)
        kept["content-length"] = str(len(body))
        kept["accept-encoding"] = "identity"
        try:
            stream = self._dial(deadline, self._acquire)
        except Exception:
            self.close()
            raise ClientError("unavailable") from None
        with self._lock:
            if self._closed:
                raise ClientError("cancelled")
        try:
            wire = Wire(stream, deadline)
            wire.send((f"{method} {target} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in kept.items())
                       + "\r\n").encode("ascii"))
            for offset in range(0, len(body), 65536):
                wire.send(body[offset:offset + 65536])
            # Ignore bounded informational responses, never an unsolicited upgrade.
            for _ in range(8):
                line, response_headers = wire.head(repeated=True)
                if not re.fullmatch(r"HTTP/1\.[01] [1-5][0-9]{2}( [ -~]*)?", line):
                    raise ClientError("protocol")
                status = int(line.split(" ")[1])
                if status == 101:
                    raise ClientError("protocol")
                if status >= 200:
                    break
            else:
                raise ClientError("protocol")
            wire.deadline = time.monotonic() + 3600
            events = response_headers.get("content-type", "").split(";", 1)[0].strip().lower() == "text/event-stream"
            wire.idle_timeout = None if events else 30
            return RemoteResponse(self, wire, status, response_headers, method)
        except BaseException:
            self.close()
            raise


class RemoteResponse:
    """Decode one framed response incrementally; malformed/short bodies error."""

    def __init__(self, call: RemoteCall, wire: Wire, status: int,
                 headers: dict[str, str], method: str) -> None:
        self.call, self.wire, self.status, self.headers = call, wire, status, headers
        self.socket = wire.stream
        empty = method == "HEAD" or status in (204, 304)
        self.length = 0 if empty else int(headers["content-length"]) if "content-length" in headers else None
        self.remaining = self.length
        self.chunked = not empty and "transfer-encoding" in headers
        self.chunk_remaining = 0
        self.ended = empty

    def getheaders(self) -> list[tuple[str, str]]:
        return list(self.headers.items())

    def read1(self, size: int = 65536) -> bytes:
        if self.ended or self.remaining == 0 or size <= 0:
            return b""
        if self.chunked and not self.chunk_remaining:
            line = self.wire.until(b"\r\n", 128)
            if not re.fullmatch(rb"[0-9a-fA-F]{1,8}\r\n", line):
                raise ClientError("protocol")
            self.chunk_remaining = int(line, 16)
            if not self.chunk_remaining:
                if self.wire.exact(2) != b"\r\n":
                    raise ClientError("protocol")
                self.ended = True
                return b""
        if not self.wire.buffer:
            data = self.wire.receive()
            if not data:
                if self.chunked or self.remaining is not None:
                    raise ClientError("protocol")
                self.ended = True
                return b""
            self.wire.buffer.extend(data)
        count = min(size, 65536, len(self.wire.buffer))
        if self.chunked:
            count = min(count, self.chunk_remaining)
        elif self.remaining is not None:
            count = min(count, self.remaining)
        data = bytes(self.wire.buffer[:count])
        del self.wire.buffer[:count]
        if self.chunked:
            self.chunk_remaining -= count
            if not self.chunk_remaining and self.wire.exact(2) != b"\r\n":
                raise ClientError("protocol")
        elif self.remaining is not None:
            self.remaining -= count
        return data

    def close(self) -> None:
        self.call.close()

    def __enter__(self) -> RemoteResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class RuntimeProxy:
    """One session's bounded loopback listener and all accepted client streams.

    dial opens only the helper's pinned peer; credential is a native callback,
    never a URL/JS value. HTTP uses one request per connection; SSE response
    bytes are streamed unchanged and validated WebSocket upgrades are duplex.
    """

    def __init__(self, dial: Callable[[float], socket.socket],
                 release: Callable[[socket.socket], None], credential: Callable[[], str]) -> None:
        self._dial, self._release, self._credential = dial, release, credential
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._streams: set[socket.socket] = set()
        self._workers: set[threading.Thread] = set()
        self._listener = socket.socket()
        try:
            self._listener.bind(("127.0.0.1", 0))
            self._listener.listen(16)
            self._listener.settimeout(0.2)
            self.url = f"http://127.0.0.1:{self._listener.getsockname()[1]}/"
            self._acceptor = threading.Thread(target=self._accept, daemon=True)
            self._acceptor.start()
        except (OSError, RuntimeError):
            self._listener.close()
            raise ClientError("runtimeUnavailable") from None

    def __repr__(self) -> str:
        return "RuntimeProxy(<redacted>)"

    def _accept(self) -> None:
        while not self._closed.is_set():
            try:
                client, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with self._lock:
                if self._closed.is_set() or len(self._workers) >= 32:
                    close_socket(client)
                    continue
                self._streams.add(client)
                worker = threading.Thread(target=self._serve, args=(client,), daemon=True)
                self._workers.add(worker)
                worker.start()

    def _serve(self, client: socket.socket) -> None:
        remote = None
        try:
            incoming = Wire(client, time.monotonic() + 10)
            line, headers = incoming.head()
            if int(headers.get("content-length", "0")) > 64 * 1024 * 1024:
                incoming.send(b"HTTP/1.1 413 Payload Too Large\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                return
            parts = line.split(" ")
            if (len(parts) != 3 or not re.fullmatch(r"[A-Z]+", parts[0])
                    or parts[2] != "HTTP/1.1" or not parts[1].startswith("/")
                    or parts[1].startswith("//") or "#" in parts[1]
                    or headers.get("host") != self.url[7:-1]
                    or headers.get("origin", self.url[:-1]) != self.url[:-1]):
                raise ClientError("protocol")
            method, path, _ = parts
            if path.split("?", 1)[0].rstrip("/") in ("/auth/bootstrap", "/api/v1/auth/bootstrap"):
                if method != "GET" or headers.get("content-length", "0") != "0" or "transfer-encoding" in headers:
                    raise ClientError("protocol")
                body = json.dumps({"token": "hr_session_" + secrets.token_hex(32),
                                   "user": "founder", "role": "admin"}).encode()
                incoming.send(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nCache-Control: no-store\r\n"
                              + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
                return
            upgrade = headers.get("upgrade", "").lower() == "websocket"
            connection = {item.strip().lower() for item in headers.get("connection", "").split(",")}
            if upgrade and (method != "GET" or "upgrade" not in connection
                            or headers.get("content-length", "0") != "0" or "transfer-encoding" in headers):
                raise ClientError("protocol")
            # Do not forward browser credentials, identity claims or connection-
            # nominated fields. Reconstruct framing and native authority once.
            kept = remote_headers(headers, self._credential(), upgrade=upgrade)
            if "expect" in headers:
                if headers["expect"].lower() != "100-continue":
                    raise ClientError("protocol")
                incoming.send(b"HTTP/1.1 100 Continue\r\n\r\n")
            body = None
            if "transfer-encoding" in headers:
                # The connector reads Content-Length only. Validate/dechunk the
                # bounded body before sending any request to that parser.
                body = bytearray()
                incoming.body(headers, body.extend, 64 * 1024 * 1024, raw_chunks=False)
                kept.pop("transfer-encoding", None)
                kept["content-length"] = str(len(body))
            remote = self._dial(incoming.deadline)
            with self._lock:
                if self._closed.is_set():
                    raise ClientError("unavailable")
                self._streams.add(remote)
            outgoing = Wire(remote, incoming.deadline)
            outgoing.send((line + "\r\n" + "".join(f"{k}: {v}\r\n" for k, v in kept.items()) + "\r\n").encode("ascii"))
            if body is None:
                incoming.body(headers, outgoing.send, 64 * 1024 * 1024)
            else:
                outgoing.send(body)
            # Headers are bounded even for indefinite SSE/upgrade streams.
            response, response_headers = outgoing.head(repeated=True)
            if not re.fullmatch(r"HTTP/1\.[01] [1-5][0-9]{2}( [ -~]*)?", response):
                raise ClientError("protocol")
            switched = response.split(" ")[1] == "101"
            if switched and (not upgrade or response_headers.get("upgrade", "").lower() != "websocket"
                             or "upgrade" not in {v.strip().lower() for v in response_headers.get("connection", "").split(",")}):
                raise ClientError("protocol")
            incoming.send(outgoing.raw_head)
            if outgoing.buffer:
                incoming.send(bytes(outgoing.buffer))
            if switched and incoming.buffer:
                outgoing.send(bytes(incoming.buffer))
            client.settimeout(10)
            remote.settimeout(10)
            while not self._closed.is_set():
                readable, _, _ = select.select([client, remote] if switched else [remote, client], [], [], 0.2)
                for source in readable:
                    data = source.recv(65536)
                    if not data or source is client and not switched:
                        return
                    (remote if source is client else client).sendall(data)
        except (OSError, ValueError, ClientError):
            # No remote bytes, native exception or credential in diagnostics.
            pass
        finally:
            if remote is not None:
                self._release(remote)
            with self._lock:
                self._streams.discard(client)
                self._streams.discard(remote)
                self._workers.discard(threading.current_thread())
            close_socket(client)

    def stop(self) -> None:
        self._closed.set()
        close_socket(self._listener)
        with self._lock:
            for stream in self._streams:
                close_socket(stream)
            workers = list(self._workers)
        deadline = time.monotonic() + 2
        for worker in [self._acceptor, *workers]:
            worker.join(max(0, deadline - time.monotonic()))
        if any(worker.is_alive() for worker in [self._acceptor, *workers]):
            raise ClientError("cleanupIncomplete")

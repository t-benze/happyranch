"""Native loopback runtime forwarding. Never mounted in the Connection view.

One HTTP request per local connection; Connection: close retains ordinary HTTP
and SSE framing. Only an accepted 101 switches to opaque duplex bytes. This
prevents a pipelined second request bypassing native header custody.
"""
from __future__ import annotations

import io
import json
import re
import secrets
import select
import socket
import threading
import time

_TOKEN = re.compile(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


def read_head(sock: socket.socket, deadline: float) -> tuple[bytes, bytes]:
    data = bytearray()
    while b"\r\n\r\n" not in data:
        if time.monotonic() >= deadline or len(data) >= 16384:
            raise ValueError("httpIncomplete")
        sock.settimeout(max(0.001, deadline - time.monotonic()))
        piece = sock.recv(min(4096, 16384 - len(data)))
        if not piece:
            raise ValueError("httpIncomplete")
        data.extend(piece)
    head, extra = bytes(data).split(b"\r\n\r\n", 1)
    lines = head.split(b"\r\n")
    headers = []
    for line in lines[1:]:
        name, sep, value = line.partition(b":")
        if not sep or _TOKEN.fullmatch(name) is None or any(c < 32 and c != 9 or c == 127 for c in value):
            raise ValueError("httpFraming")
        headers.append(name.lower())
    for name in (b"content-length", b"transfer-encoding", b"host", b"upgrade"):
        if headers.count(name) > 1:
            raise ValueError("httpFraming")
    if b"content-length" in headers and b"transfer-encoding" in headers:
        raise ValueError("httpFraming")
    return head + b"\r\n\r\n", extra


class ResponseSocket:
    """HTTPResponse adapter with an aggregate ceremony deadline, not idle time."""

    def __init__(self, sock: socket.socket, prefix: bytes, deadline: float, max_bytes: int = 32768) -> None:
        self.sock, self.prefix, self.deadline = sock, prefix, deadline
        self.remaining = max_bytes

    def makefile(self, _mode: str) -> io.BufferedReader:
        owner = self

        class Reader(io.RawIOBase):
            def readable(self) -> bool:
                return True

            def readinto(self, buffer: bytearray) -> int:
                if owner.remaining <= 0:
                    raise ValueError("httpBudget")
                buffer = memoryview(buffer)[:owner.remaining]
                if owner.prefix:
                    data, owner.prefix = owner.prefix[:len(buffer)], owner.prefix[len(buffer):]
                    buffer[:len(data)] = data
                    owner.remaining -= len(data)
                    return len(data)
                remaining = owner.deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("deadline")
                owner.sock.settimeout(remaining)
                count = owner.sock.recv_into(buffer)
                owner.remaining -= count
                return count

        return io.BufferedReader(Reader(), buffer_size=1)


def _close(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    sock.close()


class RuntimeForwarder:
    def __init__(self, session: object) -> None:
        self._session = session
        self._lock = threading.RLock()
        self._closed = threading.Event()
        self._listener = None
        self._workers: set[threading.Thread] = set()
        self._clients: set[socket.socket] = set()
        self._acceptor = None

    def start(self) -> str:
        self._session.health()
        with self._lock:
            if self._closed.is_set() or self._listener is not None:
                raise ValueError("stateChanged")
            self._session.authority()
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                listener.bind(("127.0.0.1", 0))
                listener.listen(16)
                listener.settimeout(0.2)
                self._listener = listener
                self._acceptor = threading.Thread(target=self._accept, daemon=True)
                self._acceptor.start()
                return f"http://127.0.0.1:{listener.getsockname()[1]}/"
            except Exception:
                listener.close()
                raise ValueError("runtimeUnavailable") from None

    def _accept(self) -> None:
        while not self._closed.is_set():
            try:
                client, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with self._lock:
                if self._closed.is_set() or len(self._clients) >= 16:
                    _close(client)
                    continue
                self._clients.add(client)
                worker = threading.Thread(target=self._serve, args=(client,), daemon=True)
                self._workers.add(worker)
                worker.start()

    def _serve(self, client: socket.socket) -> None:
        try:
            deadline = time.monotonic() + 30
            head, extra = read_head(client, deadline)
            line, *raw_headers = head[:-4].split(b"\r\n")
            method, path, version = line.split(b" ")
            if _TOKEN.fullmatch(method) is None or version != b"HTTP/1.1" or not path.startswith(b"/") or path.startswith(b"//") or any(c <= 32 or c >= 127 for c in path):
                raise ValueError("httpFraming")
            headers = [(name.lower(), value.strip()) for name, value in (h.split(b":", 1) for h in raw_headers)]
            values = dict(headers)
            # Pin Host to the literal local listener. A foreign Origin (including
            # null) cannot use this native bearer-bearing loopback entry.
            authority = f"127.0.0.1:{self._listener.getsockname()[1]}".encode()
            if values.get(b"host") != authority or any(v != b"http://" + authority for n, v in headers if n == b"origin"):
                raise ValueError("originRefused")
            if any(n in (b"expect", b"trailer") for n, _ in headers):
                raise ValueError("httpFraming")
            length = values.get(b"content-length", b"0")
            if not re.fullmatch(rb"[0-9]{1,8}", length) or int(length) > 32 * 1024 * 1024:
                raise ValueError("httpFraming")
            chunked = values.get(b"transfer-encoding")
            if chunked is not None and chunked.lower() != b"chunked":
                raise ValueError("httpFraming")
            connections = {x.strip().lower() for x in values.get(b"connection", b"").split(b",")}
            if connections & {b"host", b"content-length", b"transfer-encoding"}:
                raise ValueError("httpFraming")
            upgrade = b"upgrade" in connections and values.get(b"upgrade", b"").lower() == b"websocket"
            normalized = re.sub(rb"^/api/v[0-9]+(?=/)", b"", path.split(b"?", 1)[0]).rstrip(b"/")
            if normalized == b"/auth/bootstrap":
                if method != b"GET" or int(length) or chunked:
                    raise ValueError("bootstrapRefused")
                body = json.dumps(dict(token="hr_session_" + secrets.token_hex(32), user="founder", role="admin")).encode()
                client.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nCache-Control: no-store\r\nConnection: close\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
                return
            target, bearer = self._session.authority()
            stripped = {b"host", b"authorization", b"proxy-authorization", b"x-happyranch-device-credential", b"connection", b"proxy-connection"} | connections
            kept = [n + b": " + v for n, v in headers if n not in stripped and n != b"upgrade"]
            kept += [b"Host: " + target.encode("ascii"), b"X-HappyRanch-Device-Credential: " + bearer.encode("ascii"),
                     b"Connection: Upgrade" if upgrade else b"Connection: close"]
            if upgrade:
                kept.append(b"Upgrade: websocket")
            with self._session.stream() as remote:
                remote.settimeout(max(0.001, deadline - time.monotonic()))
                remote.sendall(line + b"\r\n" + b"\r\n".join(kept) + b"\r\n\r\n")
                # A buffered, bounded reader consumes exactly the request body;
                # no second request or trailer can carry forged credentials.
                incoming = ResponseSocket(client, extra, deadline, max_bytes=64 * 1024 * 1024)
                reader = incoming.makefile("rb")
                try:
                    if chunked:
                        total = 0
                        while True:
                            size_line = reader.readline(128)
                            if not re.fullmatch(rb"[0-9A-Fa-f]{1,8}\r\n", size_line):
                                raise ValueError("httpFraming")
                            size = int(size_line.strip(), 16)
                            total += size
                            if total > 32 * 1024 * 1024:
                                raise ValueError("httpFraming")
                            remote.sendall(size_line)
                            self._body(reader, remote, size, deadline)
                            ending = reader.read(2)
                            if ending != b"\r\n":
                                raise ValueError("httpFraming")
                            remote.sendall(ending)
                            if size == 0:
                                break
                    else:
                        self._body(reader, remote, int(length), deadline)
                    response, remainder = read_head(remote, deadline)
                    if not re.fullmatch(rb"HTTP/1\.[01] [1-5][0-9]{2} [^\r\n]*", response.split(b"\r\n", 1)[0]):
                        raise ValueError("httpFraming")
                    switching = response.startswith(b"HTTP/1.1 101 ")
                    if switching and not upgrade:
                        raise ValueError("httpFraming")
                    client.sendall(response + remainder)
                    if switching:
                        # The one-byte buffer avoids hidden prefetch; pass the
                        # remaining first-frame bytes to the opaque duplex owner.
                        if incoming.prefix:
                            remote.sendall(incoming.prefix)
                            incoming.prefix = b""
                    self._relay(client, remote, switching)
                finally:
                    reader.close()
        except Exception:
            # Never log exceptions, input, private response bodies or credentials.
            pass
        finally:
            _close(client)
            with self._lock:
                self._clients.discard(client)
                self._workers.discard(threading.current_thread())

    def _body(self, reader: io.BufferedReader, remote: socket.socket, count: int, deadline: float) -> None:
        while count:
            data = reader.read(min(count, 65536))
            if not data or time.monotonic() >= deadline:
                raise ValueError("httpIncomplete")
            remote.settimeout(max(0.001, deadline - time.monotonic()))
            remote.sendall(data)
            count -= len(data)

    def _relay(self, client: socket.socket, remote: socket.socket, duplex: bool) -> None:
        client.settimeout(1)
        remote.settimeout(1)
        while not self._closed.is_set():
            ready, _, _ = select.select([client, remote], [], [], 0.2)
            for source in ready:
                data = source.recv(65536)
                if not data or source is client and not duplex:
                    return
                (remote if source is client else client).sendall(data)

    def close(self) -> None:
        self._closed.set()
        with self._lock:
            if self._listener is not None:
                _close(self._listener)
            for client in tuple(self._clients):
                _close(client)
            threads = [self._acceptor, *self._workers]
        self._session.close()
        deadline = time.monotonic() + 2
        for thread in threads:
            if thread is not None and thread is not threading.current_thread():
                thread.join(max(0, deadline - time.monotonic()))
        if any(thread is not None and thread.is_alive() for thread in threads):
            raise ValueError("cleanupIncomplete")

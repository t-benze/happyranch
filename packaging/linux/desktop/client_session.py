"""Session-only Linux client. Native callers alone own these redacted objects.

No bridge dispatcher is mounted here. The installed sibling invite_v2.py is
copied verbatim from the shared codec; Go retains full CA and peer admission.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import runpy
import select
import socket
import stat
import struct
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from typing import Iterator

_parse_json = runpy.run_path(str(Path(__file__).with_name("connection_contract.py")))["parse_json"]


class ClientFailure(Exception):
    """Closed categories only. Library diagnostics never leave this boundary."""


def private_runtime() -> Path:
    root = os.environ.get("XDG_RUNTIME_DIR", "")
    if not root.startswith("/") or root == "/" or os.path.normpath(root) != root:
        raise ClientFailure("runtimeUnavailable")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in root[1:].split("/"):
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ClientFailure("runtimeUnavailable")
    finally:
        os.close(fd)
    return Path(root)


def receive(sock: socket.socket, count: int, deadline: float) -> bytes:
    data = bytearray()
    while len(data) < count:
        sock.settimeout(max(0.001, deadline - time.monotonic()))
        if time.monotonic() >= deadline:
            raise ClientFailure("deadline")
        piece = sock.recv(count - len(data))
        if not piece:
            raise ClientFailure("transportUnavailable")
        data.extend(piece)
    return bytes(data)


def close_socket(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    sock.close()


class ClientSession:
    """One-shot owner: no restart, replay, persistence, or printable secrets.

    Blocking start/pair/complete/health belong on the native worker. close may
    run concurrently and fences every late result before it can acquire custody.
    """

    def __init__(self, prefix: Path) -> None:
        self._prefix = prefix
        self._lock = threading.RLock()
        self._closed = False
        self._started = False
        self._process = None
        self._directory = None
        self._sockets: set[socket.socket] = set()
        self._bearer = None
        self._pending = None
        self._expiry = 0.0
        self._pair_secret = None
        self._target = None
        self._node = None
        self._operation = threading.Lock()

    def __repr__(self) -> str:
        return "ClientSession(<redacted>)"

    def start(self, invite_text: str, hostname: str) -> None:
        try:
            codec = runpy.run_path(str(self._prefix / "share/happyranch/desktop/invite_v2.py"))
            invite = codec["decode"](invite_text, now=int(time.time()))
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-]{0,62}", hostname):
                raise ClientFailure("configuration")
            root = private_runtime()
            with self._lock:
                if self._closed or self._started:
                    raise ClientFailure("stateChanged")
                self._started = True
                self._directory = Path(tempfile.mkdtemp(prefix="hrclient-", dir=root))
                path = str(self._directory / "dial")
                if len(os.fsencode(path)) > 100:
                    raise ClientFailure("runtimeUnavailable")
                fields = dict(version=2, dialIP=invite.dial_ip, homeID=invite.home_id,
                              controlPlaneID=invite.control_plane_id, caDER=base64.b64encode(invite.ca_der).decode(),
                              fingerprint=hashlib.sha256(invite.ca_der).hexdigest(), controlURL=invite.control_url,
                              hostname=hostname, joinKey=invite.join_key, socketPath=path, target=invite.home_endpoint)
                payload = json.dumps(fields, separators=(",", ":")).encode()
                if not 0 < len(payload) <= 16384:
                    raise ClientFailure("configuration")
                process = subprocess.Popen([str(self._prefix / "bin/happyranch-home-bridge"), "--home-session-v1"],
                                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                           env={"XDG_RUNTIME_DIR": str(root)}, close_fds=True, bufsize=0)
                self._process = process
                self._target = invite.home_endpoint
                self._pair_secret, self._expiry = invite.pair_secret, invite.expires_at
                os.set_blocking(process.stdin.fileno(), False)
                wire = struct.pack("!I", len(payload)) + payload
                del payload, fields, invite, invite_text
            deadline = time.monotonic() + 120
            offset = 0
            while offset < len(wire):
                with self._lock:
                    if self._closed or time.monotonic() >= deadline:
                        raise ClientFailure("stateChanged")
                    if select.select([], [process.stdin], [], 0)[1]:
                        offset += os.write(process.stdin.fileno(), wire[offset:offset + 4096])
                if offset < len(wire):
                    time.sleep(0.01)
            del wire
            line = bytearray()
            while not line.endswith(b"\n") and len(line) < 260:
                with self._lock:
                    if self._closed:
                        raise ClientFailure("stateChanged")
                if time.monotonic() >= deadline:
                    raise ClientFailure("deadline")
                if not select.select([process.stdout], [], [], min(0.1, deadline - time.monotonic()))[0]:
                    continue
                byte = os.read(process.stdout.fileno(), 1)
                if not byte:
                    raise ClientFailure("joinIncomplete")
                line.extend(byte)
            if re.fullmatch(rb"0 [\x21-\x7e]{1,256}\n", line) is None:
                raise ClientFailure("joinIncomplete")
            with self._lock:
                if self._closed or process.poll() is not None:
                    raise ClientFailure("stateChanged")
                self._node = bytes(line[2:-1]).decode("ascii")
        except Exception:
            self.close()
            raise ClientFailure("joinIncomplete") from None
        finally:
            # Close the sole ready reader; stdin remains the lifetime capability.
            if self._process is not None and self._process.stdout is not None:
                self._process.stdout.close()

    @contextmanager
    def stream(self) -> Iterator[socket.socket]:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            with self._lock:
                if self._closed or self._node is None or self._process.poll() is not None or len(self._sockets) >= 32:
                    raise ClientFailure("transportUnavailable")
                self._sockets.add(sock)
                path = str(self._directory / "dial")
            deadline = time.monotonic() + 10
            sock.settimeout(10)
            sock.connect(path)
            if receive(sock, 4, deadline) != b"\0\0\0\0":
                raise ClientFailure("peerUnavailable")
            yield sock
        except (OSError, ValueError):
            raise ClientFailure("transportUnavailable") from None
        finally:
            with self._lock:
                self._sockets.discard(sock)
            close_socket(sock)

    def _request(self, path: str, body: str = "") -> tuple[int, bytes]:
        deadline = time.monotonic() + 10
        with self.stream() as sock:
            with self._lock:
                bearer = self._bearer if path == "/api/v1/health" else None
                target = self._target
            header = (f"{'GET' if path == '/api/v1/health' else 'POST'} {path} HTTP/1.1\r\n"
                      f"Host: {target}\r\nContent-Type: text/plain; charset=utf-8\r\n"
                      f"Content-Length: {len(body)}\r\nConnection: close\r\n")
            if bearer:
                header += f"X-HappyRanch-Device-Credential: {bearer}\r\n"
            sock.sendall((header + "\r\n" + body).encode("ascii"))
            # Reuse the bounded header parser; do not let HTTPResponse read an
            # unbounded count of header bytes before the ceremony body limit.
            runtime = runpy.run_path(str(Path(__file__).with_name("client_runtime.py")))
            head, extra = runtime["read_head"](sock, deadline)
            response = http.client.HTTPResponse(runtime["ResponseSocket"](sock, head + extra, deadline))
            try:
                response.begin()
                data = response.read(8193)
                if len(data) > 8192:
                    raise ClientFailure("authorizationIncomplete")
                return response.status, data
            finally:
                response.close()

    def _fields(self, body: bytes) -> dict:
        value = _parse_json(body.decode("utf-8"), 8192)
        if type(value) is not dict:
            raise ClientFailure("authorizationIncomplete")
        return value

    def pair(self) -> tuple[str, float]:
        return self._ceremony(False)

    def complete(self) -> bool:
        return self._ceremony(True)

    def _ceremony(self, complete: bool) -> object:
        if not self._operation.acquire(blocking=False):
            raise ClientFailure("busy")
        try:
            with self._lock:
                if self._closed or time.time() >= self._expiry:
                    raise ClientFailure("expiredInvite")
                body = self._pending if complete else self._pair_secret
                if body is None or self._bearer is not None:
                    raise ClientFailure("stateChanged")
                if complete:
                    self._pending = None  # only exact pending denial restores this handle
                if not complete:
                    self._pair_secret = None  # one redemption, including lost delivery
            status, data = self._request("/pair/complete" if complete else "/pair/invite", body)
            fields = self._fields(data)
            with self._lock:
                if self._closed or time.time() >= self._expiry:
                    raise ClientFailure("stateChanged")
                if complete and status == 403 and fields == {"error": "pairing_denied"}:
                    self._pending = body
                    return False  # pending/rejected/spent are intentionally indistinguishable
                if status != 200:
                    raise ClientFailure("authorizationIncomplete")
                if complete:
                    bearer = fields.get("credential")
                    if fields.keys() != {"credential"} or type(bearer) is not str or not re.fullmatch(r"hrpair_[0-9a-f]{64}", bearer):
                        raise ClientFailure("authorizationIncomplete")
                    self._bearer, self._pending = bearer, None
                    return True
                if fields.keys() != {"state", "completion_handle", "comparison_phrase", "expires_at"} or fields["state"] != "pending-approval":
                    raise ClientFailure("authorizationIncomplete")
                handle, phrase, stamp = (fields[k] for k in ("completion_handle", "comparison_phrase", "expires_at"))
                if not all(type(x) is str for x in (handle, phrase, stamp)) or not re.fullmatch(r"[0-9a-f]{64}", handle) or not re.fullmatch(r"pair-[0-9a-f]{4}(?:-[0-9a-f]{4}){3}", phrase):
                    raise ClientFailure("authorizationIncomplete")
                if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|\+00:00)", stamp):
                    raise ClientFailure("authorizationIncomplete")
                expiry = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
                if not time.time() < expiry <= self._expiry:
                    raise ClientFailure("authorizationIncomplete")
                self._pending, self._expiry = handle, expiry
                return phrase, expiry  # native dialog only; never a web projection
        except ClientFailure:
            raise
        except Exception:
            raise ClientFailure("authorizationIncomplete") from None
        finally:
            self._operation.release()

    def health(self) -> None:
        try:
            self.authority()
            status, data = self._request("/api/v1/health")
            if 200 <= status < 300:
                return
            fields = self._fields(data)
            code = fields.get("error")
            if status == 403 and code in ("revocation_denied", "revocation_stream_closed"):
                raise ClientFailure("revoked")
            if status in (403, 503) and code == "daemon_unavailable":
                raise ClientFailure("daemonUnavailable")
        except ClientFailure:
            raise
        except Exception:
            pass
        raise ClientFailure("healthUnknown")

    def authority(self) -> tuple[str, str]:
        with self._lock:
            if self._closed or self._bearer is None:
                raise ClientFailure("stateChanged")
            return self._target, self._bearer  # native forwarding boundary only

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._bearer = self._pending = self._pair_secret = self._node = None
            for sock in tuple(self._sockets):
                close_socket(sock)
            process, directory = self._process, self._directory
            if process is not None and process.stdin is not None:
                process.stdin.close()
            try:
                if process is not None:
                    if process.poll() is None:
                        try:
                            process.terminate()
                        except ProcessLookupError:
                            pass
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
                if directory is not None:
                    (directory / "dial").unlink(missing_ok=True)
                    directory.rmdir()
                    self._directory = None
            except (OSError, subprocess.TimeoutExpired):
                raise ClientFailure("cleanupIncomplete") from None

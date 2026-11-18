"""Session-only Linux client owner; called natively after explicit target consent.

No GTK/JS dependency. 3a.2 owns prompts, attempts and the real runtime load gate.
All methods may block and belong on a native worker; stop is independently callable.
"""
from __future__ import annotations

import base64
from datetime import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import select
import socket
import stat
import struct
import subprocess
import tempfile
import threading
import time


def _load(name: str, path: Path) -> object:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_directory = Path(__file__).parent
_runtime = _load("client_runtime", _directory / "client_runtime.py")
ClientError, Wire = _runtime.ClientError, _runtime.Wire
# System Python -I cannot import bundled runtime dependencies. The builder copies
# this pure shared codec verbatim; the second path is its source-checkout location.
_codec_path = _directory / "invite_v2.py"
if not _codec_path.exists():
    _codec_path = _directory.parents[2] / "runtime/remote_access/invite_v2.py"
_codec = _load("invite_v2", _codec_path)


def decode_invite(text: str) -> object:
    try:
        return _codec.decode(text, now=int(time.time()))
    except _codec.InviteError:
        raise ClientError("invalidInvite") from None


def _private_runtime() -> Path:
    path = os.environ.get("XDG_RUNTIME_DIR", "")
    if not path.startswith("/") or path == "/" or os.path.normpath(path) != path:
        raise ClientError("runtimeUnavailable")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for component in path[1:].split("/"):
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ClientError("runtimeUnavailable")
    finally:
        os.close(fd)
    return Path(path)


def _fields(body: bytes) -> dict[str, str]:
    def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, str]:
        result: dict[str, str] = {}
        for name, value in pairs:
            if name in result or type(value) is not str:
                raise ClientError("protocol")
            result[name] = value
        return result
    try:
        result = json.loads(body, object_pairs_hook=object_pairs)
        if type(result) is dict:
            return result
    except (ValueError, UnicodeError, RecursionError):
        pass
    raise ClientError("protocol")


class ClientSession:
    """Single-use owner. stop seals launch/dial/credential admission permanently."""

    def __init__(self, prefix: Path) -> None:
        self._executable = prefix / "bin/happyranch-home-bridge"
        self._lock = threading.RLock()
        self._stop_lock = threading.Lock()
        self._closed = False
        self._cleanup_uncertain = False
        self._process: subprocess.Popen | None = None
        self._directory: Path | None = None
        self._identity: tuple[int, int] | None = None
        self._streams: set[socket.socket] = set()
        self._proxy = None
        self._bearer = self._pair_secret = self._handle = ""
        self._expiry = 0.0
        self._node = None

    def __repr__(self) -> str:
        return "ClientSession(<redacted>)"

    def join(self, invite: object) -> None:
        try:
            # Re-admit the shared codec's immutable result against the current
            # clock. The Go child independently owns full certificate/peer trust.
            if type(invite) is not _codec.DecodedInvite or not time.time() < invite.expires_at:
                raise ClientError("invalidInvite")
            with self._lock:
                if self._closed or self._process is not None:
                    raise ClientError("unavailable")
                root = _private_runtime()
                self._directory = Path(tempfile.mkdtemp(prefix="hrclient-", dir=root))
                info = self._directory.lstat()
                self._identity = info.st_dev, info.st_ino
                path = str(self._directory / "dial")
                if len(path.encode()) > 100:
                    raise ClientError("runtimeUnavailable")
                fields = {"version": 2, "caDER": base64.b64encode(invite.ca_der).decode(),
                          "dialIP": invite.dial_ip, "homeID": invite.home_id,
                          "controlPlaneID": invite.control_plane_id,
                          "fingerprint": hashlib.sha256(invite.ca_der).hexdigest(),
                          "controlURL": invite.control_url, "hostname": "happyranch-linux-" + os.urandom(8).hex(),
                          "joinKey": invite.join_key, "socketPath": path, "target": invite.home_endpoint}
                payload = json.dumps(fields, separators=(",", ":")).encode()
                del fields
                if not 0 < len(payload) <= 16384:
                    raise ClientError("invalidInvite")
                # No HOME, PATH, credential, proxy, debug, trust or TS_* controls.
                process = subprocess.Popen([str(self._executable), "--home-session-v1"],
                                           env={"XDG_RUNTIME_DIR": str(root)}, stdin=subprocess.PIPE,
                                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                           close_fds=True, bufsize=0)
                self._process = process
            deadline = time.monotonic() + 120
            os.set_blocking(process.stdin.fileno(), False)
            frame = struct.pack("!I", len(payload)) + payload
            del payload
            while frame:
                with self._lock:
                    if self._closed:
                        raise ClientError("cancelled")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ClientError("deadline")
                if select.select([], [process.stdin], [], min(remaining, 0.1))[1]:
                    frame = frame[os.write(process.stdin.fileno(), frame):]
            ready = bytearray()
            while len(ready) < 260 and not ready.endswith(b"\n"):
                with self._lock:
                    if self._closed:
                        raise ClientError("cancelled")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ClientError("deadline")
                if select.select([process.stdout], [], [], min(remaining, 0.1))[0]:
                    byte = os.read(process.stdout.fileno(), 1)
                    if not byte:
                        raise ClientError("joinUnavailable")
                    ready.extend(byte)
            if not re.fullmatch(rb"0 [!-~]{1,256}\n", ready):
                raise ClientError("joinUnavailable")
            process.stdout.close()
            with self._lock:
                if self._closed or process.poll() is not None:
                    raise ClientError("cancelled")
                self._node = ready[2:-1].decode("ascii")
                self._pair_secret, self._expiry = invite.pair_secret, invite.expires_at
        except (OSError, ValueError, ClientError):
            self.stop()
            raise ClientError("joinUnavailable") from None

    def dial(self, deadline: float) -> socket.socket:
        stream = socket.socket(socket.AF_UNIX)
        try:
            with self._lock:
                if (self._closed or not self._node or self._process.poll() is not None
                        or len(self._streams) >= 32):
                    raise ClientError("unavailable")
                self._streams.add(stream)  # stop owns even a partial connect/frame
                path = self._directory / "dial"
                info = path.lstat()
                if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
                    raise ClientError("unavailable")
                pid = self._process.pid
            stream.settimeout(max(0.001, min(10, deadline - time.monotonic())))
            stream.connect(str(path))
            peer = struct.unpack("3i", stream.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if peer[:2] != (pid, os.geteuid()) or Wire(stream, deadline).exact(4) != b"\0\0\0\0":
                raise ClientError("peerUnavailable")
            return stream
        except (OSError, ValueError, ClientError):
            self.release(stream)
            raise ClientError("peerUnavailable") from None

    def release(self, stream: socket.socket) -> None:
        with self._lock:
            self._streams.discard(stream)
        _runtime.close_socket(stream)

    def _credential(self) -> str:
        with self._lock:
            if self._closed or not self._bearer:
                raise ClientError("unavailable")
            return self._bearer

    def _request(self, path: str, body: str = "") -> tuple[int, bytes]:
        deadline = time.monotonic() + 10
        stream = self.dial(deadline)
        try:
            method = "GET" if path == "/api/v1/health" else "POST"
            header = f"{method} {path} HTTP/1.1\r\nHost: home:443\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: {len(body)}\r\nConnection: close\r\n"
            if method == "GET":
                header += f"X-HappyRanch-Device-Credential: {self._credential()}\r\n"
            wire = Wire(stream, deadline)
            wire.send((header + "\r\n" + body).encode("ascii"))
            line, headers = wire.head(repeated=True)
            if not re.fullmatch(r"HTTP/1\.[01] [1-5][0-9]{2}( [ -~]*)?", line):
                raise ClientError("protocol")
            result = bytearray()
            if "content-length" in headers or "transfer-encoding" in headers:
                wire.body(headers, result.extend, 8192, raw_chunks=False)
            else:
                result.extend(wire.buffer)
                while len(result) <= 8192:
                    data = wire.receive()
                    if not data:
                        break
                    result.extend(data)
            if len(result) > 8192:
                raise ClientError("protocol")
            return int(line.split(" ")[1]), bytes(result)
        except (OSError, ValueError):
            raise ClientError("homeUnavailable") from None
        finally:
            self.release(stream)

    def pair(self) -> tuple[str, float]:
        with self._lock:
            if not self._pair_secret or time.time() >= self._expiry or self._closed:
                raise ClientError("expiredInvite")
            secret, self._pair_secret = self._pair_secret, ""  # no automatic replay after loss
        status, body = self._request("/pair/invite", secret)
        fields = _fields(body)
        if status != 200:
            raise ClientError("authorizationIncomplete")
        if (fields.keys() != {"state", "completion_handle", "comparison_phrase", "expires_at"}
                or fields["state"] != "pending-approval"
                or not re.fullmatch(r"[0-9a-f]{64}", fields["completion_handle"])
                or not re.fullmatch(r"pair-[0-9a-f]{4}(-[0-9a-f]{4}){3}", fields["comparison_phrase"])
                or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|\+00:00)", fields["expires_at"])):
            raise ClientError("protocol")
        try:
            expiry = datetime.fromisoformat(fields["expires_at"].replace("Z", "+00:00")).timestamp()
        except ValueError:
            raise ClientError("protocol") from None
        with self._lock:
            if self._closed or not time.time() < expiry <= self._expiry:
                raise ClientError("expiredInvite")
            self._handle, self._expiry = fields["completion_handle"], expiry
        return fields["comparison_phrase"], expiry  # native dialog only; never a bridge result

    def complete(self) -> bool:
        with self._lock:
            if self._closed or not self._handle or time.time() >= self._expiry:
                raise ClientError("expiredInvite")
            handle, self._handle = self._handle, ""
        status, body = self._request("/pair/complete", handle)
        fields = _fields(body)
        with self._lock:
            if self._closed:
                raise ClientError("cancelled")
            if status == 403 and fields == {"error": "pairing_denied"}:
                self._handle = handle
                return False  # C23: pending/rejected/spent are indistinguishable
            if status != 200 or fields.keys() != {"credential"} or not re.fullmatch(r"hrpin_[1-9][0-9]{0,18}_[0-9a-f]{64}", fields["credential"]):
                raise ClientError("authorizationIncomplete")
            self._bearer = fields["credential"]
        return True

    def health(self) -> None:
        status, body = self._request("/api/v1/health")
        if 200 <= status < 300:
            return
        fields = _fields(body)
        if status == 403 and fields in ({"error": "revocation_denied"}, {"error": "revocation_stream_closed"}):
            raise ClientError("revoked")
        if status in (403, 503) and fields == {"error": "daemon_unavailable"}:
            raise ClientError("daemonUnavailable")
        raise ClientError("accessUnavailable")

    def open_runtime(self) -> str:
        self.health()
        with self._lock:
            self._credential()
            if self._proxy is None:
                self._proxy = _runtime.RuntimeProxy(self.dial, self.release, self._credential)
            return self._proxy.url  # opening is not Connected; native load decides

    def stop(self) -> None:
        try:
            self._stop()
        except (OSError, ValueError):
            raise ClientError("cleanupIncomplete") from None

    def _stop(self) -> None:
        with self._stop_lock:
            with self._lock:
                self._closed = True
                self._bearer = self._pair_secret = self._handle = ""
                self._node = None
                for stream in self._streams:
                    _runtime.close_socket(stream)
                self._streams.clear()
                process, proxy = self._process, self._proxy
                if process is not None:
                    if process.stdin and not process.stdin.closed:
                        process.stdin.close()
                    if process.poll() is None:
                        process.terminate()
            if process is not None:
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._cleanup_uncertain = True
                    process.kill()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        raise ClientError("cleanupIncomplete") from None
                if process.returncode != 0:
                    self._cleanup_uncertain = True
                if process.stdout:
                    process.stdout.close()
            if proxy is not None:
                proxy.stop()
            with self._lock:
                if self._directory is not None:
                    info = self._directory.lstat()
                    if (info.st_dev, info.st_ino) != self._identity:
                        raise ClientError("cleanupIncomplete")
                    path = self._directory / "dial"
                    if path.exists():
                        info = path.lstat()
                        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid():
                            raise ClientError("cleanupIncomplete")
                        path.unlink()
                    self._directory.rmdir()  # no recursive or unknown-file removal
                    self._directory = None
                if self._cleanup_uncertain:
                    # SIGKILL cannot confirm the Go owner's private scratch cleanup.
                    raise ClientError("cleanupIncomplete")

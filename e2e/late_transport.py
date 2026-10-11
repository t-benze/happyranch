"""E07 only: retain one original provider POST before any upstream request.

Private headers/body stay in memory. Public records never contain headers or
unsanitized exceptions; a dead downstream is separate from upstream evidence.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import select
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def public_stream(raw: bytes, secrets: set[str], incomplete: bool) -> dict:
    safe = raw
    encoded = sorted((s.encode() for s in secrets), key=len, reverse=True)
    if not encoded:
        raise RuntimeError("E07 missing sanitation context")
    # A killed/EOF/timeout capture may stop in the middle of a credential.
    if incomplete:
        suffix = max((n for s in encoded for n in range(1, min(len(s), len(safe)) + 1)
                      if safe.endswith(s[:n])), default=0)
        if suffix:
            safe = safe[:-suffix] + b"[REDACTED]"
    for secret in encoded:
        safe = safe.replace(secret, b"[REDACTED]")
    return dict(text=safe[:16384].decode(errors="ignore"), bytes=len(raw),
                truncated=len(safe) > 16384 or len(raw) > 16384, incomplete=incomplete)


def process_record(pid: int) -> dict:
    stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    return dict(pid=pid, start=stat[19], state=stat[0], ppid=int(stat[1]),
                argv=[x.decode(errors="strict") for x in argv if x],
                cgroup=Path(f"/proc/{pid}/cgroup").read_text(), at=time.monotonic())


def provider_command(instruction: dict, binding: dict, stream: Any) -> dict:
    """The genuine provider writes its own payload and acknowledges before wait."""
    from thread_provider import redaction_context
    root = Path(os.environ["E2E_ROOT"])
    secrets = redaction_context(root, None)
    manifest = json.loads((root / "coord/manifest.json").read_text())
    callback = Path(manifest["callback"])
    if digest(callback.read_bytes()) != manifest["callback_sha256"]:
        raise RuntimeError("E07 callback binding changed")
    parent = instruction["action"] == "e07-parent"
    if instruction["action"] not in {"e07-parent", "e07-held"} or binding["org"] != "alpha" or binding["agent"] != ("case_manager" if parent else "case_worker"):
        raise RuntimeError("E07 unexpected finite provider action")
    payload = dict(task_id=binding["task"], session_id=binding["session"], agent=binding["agent"],
                   status="completed", confidence=90,
                   summary="E07 parent delegated" if parent else "E07 cancelled child must not complete")
    if parent:
        payload["decision"] = dict(action="delegate", agent="case_worker", prompt="E07_CANCEL_CHILD")
    raw = json.dumps(payload, sort_keys=True).encode()
    path = root / "coord" / ("e07-" + digest(raw) + ".json")
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as file:
        file.write(raw)
    home = root / ("client" if parent else "e07-client")
    argv = [str(callback), "report-completion", "--org", "alpha", "--from-file", str(path)]
    end = instruction["execution_deadline"]
    if time.monotonic() >= end:
        raise RuntimeError("E07 provider launch deadline")
    entered = time.monotonic()
    launch_path = root / "coord" / ("e07-launch-" + binding["session"] + ".json")
    child = subprocess.Popen(argv, cwd=root, env=dict(os.environ, HAPPYRANCH_DAEMON_HOME=str(home)),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    launch_path.write_text(json.dumps(dict(action=instruction["action"], provider=binding,
                                          child_pid=child.pid, entered=entered, incomplete=True)))
    launch_path.chmod(0o600)
    # Durable launch record precedes both acknowledgement and communicate. On
    # cancellation it survives even when neither process can write a final row.
    launch = dict(action=instruction["action"], provider=binding, child=process_record(child.pid),
                  argv=argv, payload=payload, payload_sha256=digest(raw), payload_bytes=len(raw),
                  callback_sha256=manifest["callback_sha256"], source_sha=manifest["revision"],
                  python=sys.executable, entered=entered, execution_deadline=end,
                  acknowledged=time.monotonic())
    launch_path.write_text(json.dumps(launch))
    launch_path.chmod(0o600)
    stream.write((json.dumps(dict(e07_launch=launch)) + "\n").encode())
    stream.flush()
    timed_out = False
    try:
        out, err = child.communicate(timeout=max(0, end - time.monotonic()))
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        out, err = exc.stdout or b"", exc.stderr or b""
    incomplete = timed_out or child.returncode is None or child.returncode < 0
    receipt = dict(launch=launch, exit=child.returncode, timed_out=timed_out,
                   stdout=public_stream(out, secrets, incomplete), stderr=public_stream(err, secrets, incomplete),
                   finished=time.monotonic())
    # No unsafe exception/headers/streams are ever written as partial output.
    return receipt


class LateTransport:
    def __init__(self, root: Path, upstream: int, task: str, payload: dict, token: str,
                 secrets: set[str], deadline: float) -> None:
        self.root, self.upstream, self.deadline = root, upstream, deadline
        self.path = f"/api/v1/orgs/alpha/tasks/{task}/completion"
        self.payload, self.token, self.secrets = payload, token, set(secrets)
        self.records: list[dict] = []
        self.errors: list[str] = []
        self.captured = threading.Event()
        self.release = threading.Event()
        self.completed = threading.Event()
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.sockets: set[socket.socket] = set()
        self.workers: set[threading.Thread] = set()
        self.selected: dict | None = None
        self.forward_count = 0
        self.accepted: list[dict] = []
        relay = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: object) -> None:
                pass

            def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
                relay.errors.append("unsupported HTTP framing/status " + str(code))
                self.close_connection = True

            def handle_expect_100(self) -> bool:
                relay.errors.append("unexpected Expect header")
                return False

            def forward(self) -> None:
                selected = self.command == "POST" and self.path == relay.path
                record = dict(method=self.command, path=self.path, entered=time.monotonic(),
                              selected=selected, upstream=relay.upstream,
                              peer=list(self.client_address), local=list(self.connection.getsockname()))
                relay.records.append(record)
                upstream_connection = None
                upstream_socket = None
                with relay.lock:
                    relay.sockets.add(self.connection)
                    relay.workers.add(threading.current_thread())
                try:
                    self.connection.settimeout(relay.remaining())
                    lengths = self.headers.get_all("Content-Length", [])
                    if self.headers.get("Transfer-Encoding") or self.headers.get("Upgrade") or len(lengths) > 1:
                        raise ValueError("framing")
                    length = int(lengths[0]) if lengths else 0
                    if length < 0 or length > 16384:
                        raise ValueError("request cap")
                    raw = self.rfile.read(length)
                    record["request_bytes"] = len(raw)
                    record["request_truncated"] = len(raw) != length
                    if len(raw) != length:
                        raise ValueError("partial request")
                    if selected:
                        if relay.selected is not None or json.loads(raw) != relay.payload:
                            raise ValueError("unexpected selected body")
                        authorization = self.headers.get_all("Authorization", [])
                        if authorization != ["Bearer " + relay.token]:
                            raise ValueError("wrong authority")
                        record.update(request=json.loads(raw), request_text=raw.decode(), request_sha256=digest(raw),
                                      authorization_sha256=digest(authorization[0].encode()),
                                      captured=time.monotonic(), listener_fd=relay.server.fileno(),
                                      accepted_fd=self.connection.fileno(), accepted_inode=os.readlink(f"/proc/self/fd/{self.connection.fileno()}"))
                        relay.selected = record
                        relay.captured.set()
                        # Absolutely no upstream connection/request exists before release.
                        if not relay.release.wait(relay.remaining()) or relay.stop.is_set():
                            raise TimeoutError("abandoned captured request")
                    elif self.command != "GET" or self.path != "/api/v1/orgs":
                        raise ValueError("unaccounted discovery request")
                    if selected:
                        relay.forward_count += 1
                        if relay.forward_count != 1:
                            raise ValueError("duplicate forward")
                    upstream_connection = http.client.HTTPConnection("127.0.0.1", relay.upstream, timeout=relay.remaining())
                    record["forwarded"] = time.monotonic()
                    # Exact original selected body; never reconstruct or substitute SID.
                    upstream_connection.request(self.command, self.path, body=raw or None,
                                                headers={"Authorization": self.headers.get("Authorization", ""),
                                                         "Content-Type": "application/json", "Connection": "close"})
                    upstream_socket = upstream_connection.sock
                    with relay.lock:
                        if upstream_socket:
                            relay.sockets.add(upstream_socket)
                    response = upstream_connection.getresponse()
                    content = response.read(16385)
                    safe = public_stream(content, relay.secrets, len(content) > 16384)
                    record.update(status=response.status, response_stream=safe, upstream_complete=time.monotonic())
                    if safe["truncated"] or safe["incomplete"]:
                        raise ValueError("response cap")
                    record["response"] = json.loads(safe["text"])
                    # Persist upstream outcome BEFORE observing a dead downstream.
                    if selected:
                        relay.completed.set()
                    peer_gone = bool(select.select([self.connection], [], [], 0)[0]) and self.connection.recv(1, socket.MSG_PEEK) == b""
                    if peer_gone:
                        record["downstream"] = "EOF"
                    else:
                        try:
                            self.send_response(response.status)
                            self.send_header("Content-Type", "application/json")
                            self.send_header("Content-Length", str(len(content)))
                            self.send_header("Connection", "close")
                            self.end_headers()
                            self.wfile.write(content)
                            self.wfile.flush()
                            record["downstream"] = "delivered"
                        except (BrokenPipeError, ConnectionResetError):
                            record["downstream"] = "peer-gone"
                    record["finished"] = time.monotonic()
                    self.close_connection = True
                except BaseException as exc:
                    # Type only: parse/header/socket exceptions may embed credentials.
                    record["failure"] = type(exc).__name__
                    relay.errors.append(type(exc).__name__)
                    self.close_connection = True
                finally:
                    if upstream_connection:
                        with relay.lock:
                            if upstream_socket:
                                relay.sockets.discard(upstream_socket)
                        upstream_connection.close()
                    with relay.lock:
                        relay.sockets.discard(self.connection)
                        relay.workers.discard(threading.current_thread())
                    record["closed"] = time.monotonic()

            do_GET = do_POST = forward

        class Server(ThreadingHTTPServer):
            def process_request(self, request: socket.socket, address: tuple) -> None:
                request.settimeout(relay.remaining())
                with relay.lock:
                    relay.sockets.add(request)
                relay.accepted.append(dict(peer=list(address), local=list(request.getsockname()),
                                           fd=request.fileno(), inode=os.readlink(f"/proc/self/fd/{request.fileno()}"),
                                           at=time.monotonic()))
                super().process_request(request,address)

            def process_request_thread(self, request: socket.socket, address: tuple) -> None:
                worker = threading.current_thread()
                with relay.lock:
                    relay.workers.add(worker)
                try:
                    super().process_request_thread(request,address)
                finally:
                    with relay.lock:
                        relay.sockets.discard(request)
                        relay.workers.discard(worker)

            def handle_error(self, request: socket.socket, address: tuple) -> None:
                relay.errors.append("HTTP worker failure")

        self.server = Server(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_port
        # Register before starting the listener or any wait/failable assertion.
        ports = json.loads((root / "ports.json").read_text())
        (root / "ports.json").write_text(json.dumps([*ports, self.port]))
        self.listener = dict(pid=os.getpid(), fd=self.server.fileno(),
                             inode=os.readlink(f"/proc/self/fd/{self.server.fileno()}"), port=self.port,
                             at=time.monotonic())
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()

    def remaining(self) -> float:
        value = self.deadline - time.monotonic()
        if value <= 0 or self.stop.is_set():
            raise TimeoutError("E07 original deadline")
        return value

    def provenance(self, launch: dict) -> dict:
        pid = launch["child"]["pid"]
        proc = process_record(pid)
        record = self.selected
        if not record or proc["start"] != launch["child"]["start"] or proc["ppid"] != launch["provider"]["pid"]:
            raise RuntimeError("E07 CLI lifetime/ancestry")
        def endpoint(raw: str) -> list:
            address, port = raw.split(":")
            return [socket.inet_ntoa(bytes.fromhex(address)[::-1]), int(port, 16)]
        tcp = []
        for line in Path(f"/proc/{pid}/net/tcp").read_text().splitlines()[1:]:
            fields = line.split()
            if endpoint(fields[1]) == record["peer"] and endpoint(fields[2]) == record["local"] and fields[3] == "01":
                tcp.append(dict(local=endpoint(fields[1]), remote=endpoint(fields[2]), state=fields[3], inode=fields[9], raw=line))
        fds = []
        for fd in Path(f"/proc/{pid}/fd").iterdir():
            try:
                target = os.readlink(fd)
            except FileNotFoundError:
                continue
            if any(target == "socket:[" + row["inode"] + "]" for row in tcp):
                fds.append(dict(fd=int(fd.name), target=target))
        if len(tcp) != 1 or len(fds) != 1:
            raise RuntimeError("E07 actual client socket provenance missing")
        return dict(process=proc, tcp=tcp, fds=fds, sampled=time.monotonic())

    def close(self) -> dict:
        self.stop.set()
        self.release.set()
        with self.lock:
            for connection in list(self.sockets):
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=max(0, min(1, self.deadline - time.monotonic())))
        with self.lock:
            workers = list(self.workers)
        for worker in workers:
            worker.join(timeout=max(0, min(1, self.deadline - time.monotonic())))
        return dict(port=self.port, listener=self.listener, listener_alive=self.thread.is_alive(),
                    workers_alive=sum(t.is_alive() for t in workers), sockets=len(self.sockets),
                    errors=list(self.errors), forward_count=self.forward_count, at=time.monotonic())

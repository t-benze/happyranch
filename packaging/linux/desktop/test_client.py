"""Standalone native process/socket contracts; synthetic authority only."""
from __future__ import annotations

import ast
import base64
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("client_session", Path(__file__).with_name("client_session.py"))
client = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(client)
BEARER = "hrpin_1_" + "b" * 64
HANDLE = "a" * 64

# A real executable peer exercises private stdin/stdout, Linux SO_PEERCRED,
# fragmented status/HTTP, EOF and signals. It never implements client policy.
HELPER = r'''#!/usr/bin/python3
import json, os, signal, socket, struct, sys, threading, time
from pathlib import Path
root = Path(__file__).parent.parent
mode = (root / "mode").read_text() if (root / "mode").exists() else "normal"
size = struct.unpack("!I", sys.stdin.buffer.read(4))[0]
cfg = json.loads(sys.stdin.buffer.read(size))
(root / "launch").write_text(json.dumps({"argv": sys.argv[1:], "env": dict(os.environ), "cfg": cfg}))
listener = socket.socket(socket.AF_UNIX)
listener.bind(cfg["socketPath"])
os.chmod(cfg["socketPath"], 0o600)
listener.listen(32)
lock = threading.Lock()
count = 0

def send(conn, data):
    for offset in range(0, len(data), 7):
        conn.sendall(data[offset:offset+7])

def serve(conn):
    global count
    try:
        for byte in b"\0\0\0\0":
            conn.sendall(bytes([byte]))
        raw = b""
        while not raw.endswith(b"\r\n\r\n"):
            part = conn.recv(1)
            if not part: return
            raw += part
        lines = raw.decode().split("\r\n")
        headers = dict(line.lower().split(": ", 1) for line in lines[1:] if line)
        body = b""
        if "content-length" in headers:
            while len(body) < int(headers["content-length"]):
                part = conn.recv(int(headers["content-length"]) - len(body))
                if not part: return
                body += part
        elif headers.get("transfer-encoding") == "chunked":
            while not body.endswith(b"0\r\n\r\n"):
                part = conn.recv(1)
                if not part: return
                body += part
        with lock:
            with (root / "requests").open("a") as out:
                out.write(json.dumps({"head": raw.decode(), "body": body.decode()}) + "\n")
        path = lines[0].split()[1]
        if path == "/ws":
            send(conn, b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: upgrade\r\n\r\n")
            while True:
                data = conn.recv(65536)
                if not data: return
                conn.sendall(data)
        if path == "/hold":
            send(conn, b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n")
            conn.recv(1)
            return
        if path == "/events":
            send(conn, b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nContent-Type: text/event-stream\r\nSet-Cookie: one=1\r\nSet-Cookie: two=2\r\n\r\n9\r\ndata: x\n\n\r\n0\r\n\r\n")
            return
        if (root / "reply").exists():
            send(conn, (root / "reply").read_bytes())
            return
        status = 200
        if path == "/pair/invite":
            payload = {"state": "pending-approval", "completion_handle": "a"*64,
                       "comparison_phrase": "pair-1111-2222-3333-4444", "expires_at": (root / "expiry").read_text()}
        elif path == "/pair/complete":
            with lock:
                count += 1
                current = count
            status = 403 if current == 1 else 200
            payload = {"error": "pairing_denied"} if current == 1 else {"credential": "hrpin_1_" + "b"*64}
        else:
            payload = {"ok": True}
        data = json.dumps(payload).encode()
        # Native request parser must decode fragmented chunked JSON.
        send(conn, f"HTTP/1.1 {status} Result\r\nTransfer-Encoding: chunked\r\n\r\n".encode() + f"{len(data):x}\r\n".encode() + data + b"\r\n0\r\n\r\n")
    except (OSError, ValueError):
        pass
    finally:
        conn.close()

def accept():
    while True:
        try: conn, _ = listener.accept()
        except OSError: return
        threading.Thread(target=serve, args=(conn,), daemon=True).start()
threading.Thread(target=accept, daemon=True).start()
if mode != "hang-ready":
    for byte in (b"0 node-synthetic\n" if mode != "bad-ready" else b"0 bad node\n"):
        os.write(1, bytes([byte]))
if mode == "ignore-stop":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    (root / "ignoring").touch()
    while True: time.sleep(1)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
try:
    sys.stdin.buffer.read()
finally:
    listener.close()
    Path(cfg["socketPath"]).unlink(missing_ok=True)
'''


class ClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory(prefix="hrclient-test-", dir="/tmp")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir(mode=0o700)
        self.prefix = self.root / "payload"
        (self.prefix / "bin").mkdir(parents=True)
        helper = self.prefix / "bin/happyranch-home-bridge"
        helper.write_text(HELPER)
        helper.chmod(0o700)
        # Reuse the existing public DER fixture without importing retired pytest.
        tree = ast.parse((ROOT / "scripts/test_invite_v2.py").read_text())
        node = next(n for n in tree.body if isinstance(n, ast.Assign) and n.targets[0].id == "SWIFT_CA")
        ca = base64.b64decode(ast.literal_eval(node.value.args[0]))
        now = int(time.time())
        encoded = client._codec.encode(home_id="test-home", control_plane_id="test-control", connector_id="test-connector",
                                       ca_der=ca, dial_ip="192.168.1.40", home_endpoint="100.64.0.10:443",
                                       join_key="cafe12" * 8, issued_at=now, expires_at=now + 240)
        self.invite = client.decode_invite(encoded.invite_text)
        (self.prefix / "expiry").write_text(datetime.fromtimestamp(now + 180, timezone.utc).isoformat())
        self.environment = patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.runtime), "SSLKEYLOGFILE": "DEBUG-CANARY",
                                                  "TS_DEBUG_TLS_DIAL": "TRUST-CANARY", "HAPPYRANCH_TOKEN": "ENV-CANARY"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.session = client.ClientSession(self.prefix)
        self.addCleanup(self.session.stop)

    def joined(self) -> None:
        self.session.join(self.invite)

    def paired(self) -> None:
        self.joined()
        phrase, expiry = self.session.pair()
        self.assertEqual(phrase, "pair-1111-2222-3333-4444")
        self.assertLessEqual(expiry, self.invite.expires_at)
        self.assertFalse(self.session.complete())
        self.assertTrue(self.session.complete())

    def requests(self) -> list[dict]:
        return [json.loads(line) for line in (self.prefix / "requests").read_text().splitlines()]

    def browser(self, request: bytes) -> bytes:
        port = int(self.session.open_runtime().split(":")[-1][:-1])
        with socket.create_connection(("127.0.0.1", port), timeout=3) as stream:
            request = request.replace(b"HOST", f"127.0.0.1:{port}".encode())
            for part in (request[:13], request[13:]):
                stream.sendall(part)
            data = bytearray()
            while chunk := stream.recv(65536):
                data.extend(chunk)
            return bytes(data)

    def test_launch_environment_ownership_and_reap(self) -> None:
        self.joined()
        record = json.loads((self.prefix / "launch").read_text())
        self.assertEqual(record["argv"], ["--home-session-v1"])
        # CPython itself may add LC_CTYPE; launch env must still have no controls.
        self.assertLessEqual(set(record["env"]), {"XDG_RUNTIME_DIR", "LC_CTYPE"})
        self.assertEqual(set(record["cfg"]), {"version", "caDER", "dialIP", "homeID", "controlPlaneID", "fingerprint",
                                             "controlURL", "hostname", "joinKey", "socketPath", "target"})
        self.assertEqual(record["cfg"]["version"], 2)
        self.assertTrue(record["cfg"]["joinKey"])
        directory = Path(record["cfg"]["socketPath"]).parent
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        stream = self.session.dial(time.monotonic() + 2)
        process = self.session._process
        self.session.stop()
        self.assertIsNotNone(process.returncode)
        self.assertEqual(stream.fileno(), -1)
        self.assertFalse(directory.exists())
        with self.assertRaises(client.ClientError):
            self.session.dial(time.monotonic() + 2)
        self.assertEqual(list(self.runtime.iterdir()), [])

    def test_runtime_directory_refusals(self) -> None:
        alias = self.root / "alias"
        alias.symlink_to(self.runtime)
        for value in ("", "relative", str(alias), str(alias / "child"), str(self.runtime) + "/../runtime"):
            with self.subTest(value=value), patch.dict(os.environ, {"XDG_RUNTIME_DIR": value}):
                with self.assertRaises(client.ClientError):
                    client.ClientSession(self.prefix).join(self.invite)
        self.runtime.chmod(0o755)
        with self.assertRaises(client.ClientError):
            self.joined()
        self.assertFalse((self.prefix / "launch").exists())

    def test_malformed_ready_reaps(self) -> None:
        (self.prefix / "mode").write_text("bad-ready")
        with self.assertRaises(client.ClientError) as error:
            self.joined()
        self.assertEqual(str(error.exception), "joinUnavailable")
        self.assertIsNotNone(self.session._process.returncode)
        self.assertEqual(list(self.runtime.iterdir()), [])

    def test_cancel_during_ready(self) -> None:
        (self.prefix / "mode").write_text("hang-ready")
        errors = []
        def join() -> None:
            try: self.joined()
            except client.ClientError as error: errors.append(str(error))
        worker = threading.Thread(target=join)
        worker.start()
        deadline = time.monotonic() + 3
        while not (self.prefix / "launch").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue((self.prefix / "launch").exists())
        self.session.stop()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, ["joinUnavailable"])
        self.assertIsNone(self.session._node)
        self.assertIsNotNone(self.session._process.returncode)

    def test_force_kill_reports_uncertain_cleanup(self) -> None:
        (self.prefix / "mode").write_text("ignore-stop")
        self.joined()
        deadline = time.monotonic() + 2
        while not (self.prefix / "ignoring").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        start = time.monotonic()
        with self.assertRaisesRegex(client.ClientError, "^cleanupIncomplete$"):
            self.session.stop()
        self.assertLess(time.monotonic() - start, 5)
        self.assertEqual(self.session._process.returncode, -9)
        with self.assertRaises(client.ClientError): self.session.stop()
        self._cleanups = [entry for entry in self._cleanups if entry[0] != self.session.stop]

    def test_pair_complete_health_custody(self) -> None:
        self.paired()
        self.session.health()
        requests = self.requests()
        self.assertEqual(requests[0]["body"], self.invite.pair_secret)
        self.assertEqual(requests[1]["body"], HANDLE)
        self.assertEqual(requests[2]["body"], HANDLE)
        self.assertIn(BEARER, requests[3]["head"])
        self.assertNotIn(BEARER, repr(self.session))
        self.assertNotIn(HANDLE, repr(self.session))
        self.session.stop()
        self.assertEqual(self.session._bearer, "")
        self.assertEqual(self.session._handle, "")

    def test_strict_pair_reply_and_no_replay(self) -> None:
        self.joined()
        good = {"state": "pending-approval", "completion_handle": HANDLE,
                "comparison_phrase": "pair-1111-2222-3333-4444", "expires_at": (self.prefix / "expiry").read_text()}
        bodies = [json.dumps(good | {key: value}).encode() for key, value in
                  (("extra", "untrusted"), ("completion_handle", True), ("expires_at", "bad"),
                   ("expires_at", "2999-01-01T00:00:00Z"))]
        bodies += [b'[]', json.dumps(good).replace('"state":', '"state":"duplicate","state":').encode()]
        for body in bodies:
            (self.prefix / "reply").write_bytes(b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
            with self.subTest(body=body), self.assertRaises(client.ClientError): self.session.pair()
            with self.assertRaises(client.ClientError): self.session.pair()
            self.session.stop()
            self.session = client.ClientSession(self.prefix)
            self.addCleanup(self.session.stop)
            self.joined()
        self.assertNotIn(self.invite.pair_secret, repr(client.ClientError("protocol")))

    def test_complete_and_health_refusals(self) -> None:
        self.paired()
        for status, code, expected in ((403, "revocation_denied", "revoked"),
                                       (503, "daemon_unavailable", "daemonUnavailable"),
                                       (403, "pairing_denied", "accessUnavailable")):
            body = json.dumps({"error": code}).encode()
            (self.prefix / "reply").write_bytes(f"HTTP/1.1 {status} Result\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)
            with self.subTest(code=code), self.assertRaisesRegex(client.ClientError, "^" + expected + "$"):
                self.session.health()
        for reply in (b"Content-Length: 0\r\nContent-Length: 1\r\n\r\nx",
                      b"Content-Length: 8193\r\n\r\n", b"Content-Length: 5\r\n\r\nx"):
            (self.prefix / "reply").write_bytes(b"HTTP/1.1 200 OK\r\n" + reply)
            with self.subTest(reply=reply), self.assertRaises(client.ClientError): self.session.health()
        self.session.stop()
        (self.prefix / "reply").unlink()
        self.session = client.ClientSession(self.prefix)
        self.addCleanup(self.session.stop)
        self.joined()
        self.session.pair()
        body = b'{"credential":"NOT-A-CREDENTIAL"}'
        (self.prefix / "reply").write_bytes(f"HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)
        with self.assertRaises(client.ClientError): self.session.complete()
        with self.assertRaises(client.ClientError): self.session.complete()
        self.assertEqual(self.session._bearer, "")

    def test_native_response_framing_refusals(self) -> None:
        self.paired()
        for headers in (b"Content-Length: 0\r\nContent-Length: 0",
                        b"Content-Length: 0\r\nContent-Length: 1",
                        b"Transfer-Encoding: chunked\r\nTransfer-Encoding: chunked",
                        b"Content-Length: 0\r\nTransfer-Encoding: chunked",
                        b"Connection: close\r\nConnection: keep-alive",
                        b"Upgrade: websocket\r\nUpgrade: other"):
            (self.prefix / "reply").write_bytes(b"HTTP/1.1 200 OK\r\n" + headers + b"\r\n\r\n0\r\n\r\n")
            with self.subTest(headers=headers), self.assertRaisesRegex(client.ClientError, "^protocol$"):
                self.session.health()

    def test_bootstrap_headers_http_and_upload(self) -> None:
        self.paired()
        response = self.browser(b"GET /api/v1/auth/bootstrap HTTP/1.1\r\nHost: HOST\r\n\r\n")
        self.assertIn(b"hr_session_", response)
        self.assertNotIn(BEARER.encode(), response)
        self.assertFalse(any("bootstrap" in row["head"] for row in self.requests()))
        request = (b"POST /upload HTTP/1.1\r\nHost: HOST\r\nAuthorization: Bearer JS-CANARY\r\n"
                   b"X-HappyRanch-Device-Credential: SPOOF-CANARY\r\nX-Tailscale-User: ID-CANARY\r\n"
                   b"Connection: close, x-hide\r\nX-Hide: HOP-CANARY\r\nContent-Length: 4\r\n\r\nbody")
        self.assertIn(b"200 Result", self.browser(request))
        forwarded = self.requests()[-1]
        for canary in ("JS-CANARY", "SPOOF-CANARY", "ID-CANARY", "HOP-CANARY"):
            self.assertNotIn(canary, forwarded["head"])
        self.assertEqual(forwarded["head"].count(BEARER), 1)
        self.assertEqual(forwarded["body"], "body")
        self.assertIn(b"200 Result", self.browser(b"POST /upload HTTP/1.1\r\nHost: HOST\r\nTransfer-Encoding: chunked\r\n\r\n4\r\nbody\r\n0\r\n\r\n"))

    def test_sse_upgrade_and_stream_stop(self) -> None:
        self.paired()
        response = self.browser(b"GET /events HTTP/1.1\r\nHost: HOST\r\n\r\n")
        self.assertIn(b"data: x\n\n", response)
        self.assertEqual(response.count(b"Set-Cookie:"), 2)
        url = self.session.open_runtime()
        port = int(url.split(":")[-1][:-1])
        with socket.create_connection(("127.0.0.1", port), timeout=3) as stream:
            stream.sendall(f"GET /ws HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n".encode())
            head = b""
            while not head.endswith(b"\r\n\r\n"): head += stream.recv(1)
            self.assertIn(b"101", head)
            stream.sendall(b"\x81\x02hi")
            self.assertEqual(stream.recv(4), b"\x81\x02hi")
        with socket.create_connection(("127.0.0.1", port), timeout=3) as stream:
            stream.sendall(f"GET /hold HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
            self.assertIn(b"200", stream.recv(4096))
            start = time.monotonic()
            self.session.stop()
            self.assertLess(time.monotonic() - start, 5)
            while stream.recv(4096): pass
        self.assertFalse(self.session._proxy._workers)
        self.assertFalse(self.session._streams)

    def test_bad_browser_framing_never_dials(self) -> None:
        self.paired()
        for header in (b"Host: evil", b"Host: HOST\r\nContent-Length: 0\r\nTransfer-Encoding: chunked",
                       b"Host: HOST\r\nContent-Length: 0\r\nContent-Length: 1", b"Host: HOST\r\nOrigin: https://evil.invalid"):
            with self.subTest(header=header):
                self.assertEqual(self.browser(b"GET /bad HTTP/1.1\r\n" + header + b"\r\n\r\n"), b"")
        self.assertFalse(any("/bad" in row["head"] for row in self.requests()))

    def test_installed_isolated_import(self) -> None:
        desktop = self.prefix / "share/happyranch/desktop"
        desktop.mkdir(parents=True)
        for name in ("client_session.py", "client_runtime.py"):
            shutil.copyfile(Path(__file__).with_name(name), desktop / name)
        shutil.copyfile(ROOT / "runtime/remote_access/invite_v2.py", desktop / "invite_v2.py")
        result = subprocess.run(["/usr/bin/python3", "-I", "-B", str(desktop / "client_session.py")],
                                capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, b"")


if __name__ == "__main__":
    unittest.main(verbosity=2)

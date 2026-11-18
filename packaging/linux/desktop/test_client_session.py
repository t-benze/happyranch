"""Standalone native client behavior: python packaging/linux/desktop/test_client_session.py.

The child is a synthetic helper protocol peer, not tsnet/trust emulation. A real
TCP connector fixture supplies raw fragmented HTTP, SSE and upgrade responses.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import runpy
import shutil
import socket
import socketserver
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from typing import Iterator

from client_session import ClientFailure, ClientSession, private_runtime, receive
from client_runtime import RuntimeForwarder, read_head

ROOT = Path(__file__).resolve().parents[3]
CODEC = runpy.run_path(str(ROOT / 'runtime/remote_access/invite_v2.py'))
BEARER = 'hrpair_' + 'e1' * 32
PAIR = 'pair-1234-5678-90ab-cdef'
HELPER = r'''#!/usr/bin/python3
import json, os, pathlib, select, signal, socket, struct, sys, threading
root = pathlib.Path(__file__).parent.parent
raw = sys.stdin.buffer.read(4)
size = struct.unpack('!I', raw)[0]
launch = json.loads(sys.stdin.buffer.read(size))
(root / 'launch.json').write_text(json.dumps(dict(launch=launch, env=sorted(os.environ), argv=sys.argv[1:])))
config = json.loads((root / 'config.json').read_text())
if config.get('stubborn'):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while True:
        select.select([], [], [], 1)
server = socket.socket(socket.AF_UNIX)
server.bind(launch['socketPath'])
os.chmod(launch['socketPath'], 0o600)
server.listen(32)
for piece in ((b'bad\n',) if config.get('bad_ready') else (b'0 ', b'node-', b'synthetic\n')):
    os.write(1, piece)
def relay(local):
    remote = socket.create_connection(('127.0.0.1', config['port']))
    try:
        status = struct.pack('!i', config.get('status', 0))
        for piece in (status[:1], status[1:3], status[3:]):
            local.sendall(piece)
        while True:
            ready, _, _ = select.select([local, remote], [], [])
            for source in ready:
                data = source.recv(65536)
                if not data: return
                (remote if source is local else local).sendall(data)
    finally:
        local.close(); remote.close()
while True:
    ready, _, _ = select.select([server, sys.stdin.buffer], [], [])
    if sys.stdin.buffer in ready:
        if not os.read(0, 1): break
    if server in ready:
        local, _ = server.accept()
        threading.Thread(target=relay, args=(local,), daemon=True).start()
server.close()
'''


class Connector(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class ClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix='hrtest-', dir='/tmp')
        self.addCleanup(self.temp.cleanup)
        self.prefix = Path(self.temp.name)
        self.runtime = self.prefix / 'run'
        self.runtime.mkdir(mode=0o700)
        desktop = self.prefix / 'share/happyranch/desktop'
        desktop.mkdir(parents=True)
        for name in ('client_runtime.py', 'connection_contract.py'):
            shutil.copyfile(Path(__file__).with_name(name), desktop / name)
        shutil.copyfile(ROOT / 'runtime/remote_access/invite_v2.py', desktop / 'invite_v2.py')
        (self.prefix / 'bin').mkdir()
        helper = self.prefix / 'bin/happyranch-home-bridge'
        helper.write_text(HELPER)
        helper.chmod(0o700)
        self.requests = []
        self.responses = {}
        self.release = threading.Event()
        self.addCleanup(self.release.set)
        test = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                try:
                    head, extra = read_head(self.request, time.monotonic() + 3)
                    line, *headers = head[:-4].split(b'\r\n')
                    fields = dict(h.lower().split(b': ', 1) for h in headers)
                    count = int(fields.get(b'content-length', b'0'))
                    body = extra + receive(self.request, max(0, count - len(extra)), time.monotonic() + 3)
                    if b'transfer-encoding' in fields:
                        while not body.endswith(b'0\r\n\r\n') and len(body) < 1024:
                            body += self.request.recv(1024)
                    test.requests.append((head, body))
                    path = line.split(b' ')[1]
                    response = test.responses.get(path)
                    if response is None:
                        if path == b'/pair/invite':
                            response = (200, dict(state='pending-approval', completion_handle='a' * 64,
                                                  comparison_phrase=PAIR, expires_at=datetime.fromtimestamp(test.expiry, timezone.utc).isoformat()))
                        elif path == b'/pair/complete':
                            response = (200, dict(credential=BEARER))
                        else:
                            response = (200, dict(ok=True))
                    if callable(response):
                        response(self.request)
                        return
                    status, value = response
                    body = value if type(value) is bytes else json.dumps(value).encode()
                    raw = f'HTTP/1.1 {status} Result\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n'.encode() + body
                    for offset in range(0, len(raw), 7):
                        self.request.sendall(raw[offset:offset + 7])
                except (OSError, ValueError, ClientFailure):
                    pass

        self.connector = Connector(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.connector.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
        self.thread.start()
        self.addCleanup(self.connector.server_close)
        self.addCleanup(self.connector.shutdown)
        (self.prefix / 'config.json').write_text(json.dumps(dict(port=self.connector.server_address[1])))
        self.env = patch.dict(os.environ, dict(XDG_RUNTIME_DIR=str(self.runtime), SSLKEYLOGFILE='SYNTHETIC_DEBUG_CANARY',
                                              TS_DEBUG_TLS_DIAL='SYNTHETIC_TRUST_CANARY', SECRET_TOKEN='SYNTHETIC_SECRET_CANARY'))
        self.env.start()
        self.addCleanup(self.env.stop)
        # Minimal structurally valid CA fixture; cryptographic CA admission is Go's boundary.
        tlv = lambda tag, data: bytes((tag, len(data))) + data
        constraints = tlv(0x30, tlv(0x01, b'\xff'))
        ext = tlv(0x30, tlv(0x06, b'\x55\x1d\x13') + tlv(0x04, constraints))
        tbs = tlv(0x02, b'\x01') + tlv(0x30, b'') * 5 + tlv(0xa3, tlv(0x30, ext))
        ca = tlv(0x30, tlv(0x30, tbs) + tlv(0x30, b'') + tlv(0x03, b'\x00'))
        now = int(time.time())
        self.expiry = now + 240
        self.invite = CODEC['encode'](home_id='home', control_plane_id='control', connector_id='connector',
                                    ca_der=ca, dial_ip='192.168.1.20', join_key='ab' * 24,
                                    issued_at=now, expires_at=self.expiry, home_endpoint='home:443').invite_text
        self.session = ClientSession(self.prefix)
        self.addCleanup(lambda: self.session.close())

    def join(self) -> None:
        self.session.start(self.invite, 'client-test')

    def authorize(self) -> None:
        self.join()
        self.assertEqual(self.session.pair(), (PAIR, self.expiry))
        self.assertTrue(self.session.complete())

    @contextmanager
    def browser(self, path: str, extra: bytes = b'', body: bytes = b'') -> Iterator[socket.socket]:
        bridge = getattr(self, 'bridge', None)
        if bridge is None:
            self.bridge = RuntimeForwarder(self.session)
            url = self.bridge.start()
            self.port = int(url.split(':')[2].strip('/'))
            self.addCleanup(self.bridge.close)
        client = socket.create_connection(('127.0.0.1', self.port), timeout=3)
        try:
            header = f'GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n'.encode() + extra + b'\r\n'
            for part in (header[:9], header[9:27], header[27:] + body):
                client.sendall(part)
            yield client
        finally:
            client.close()

    def all_bytes(self, client: socket.socket) -> bytes:
        result = bytearray()
        while True:
            piece = client.recv(65536)
            if not piece:
                return bytes(result)
            result.extend(piece)

    def test_closed_launch_and_private_lifetime(self) -> None:
        self.join()
        record = json.loads((self.prefix / 'launch.json').read_text())
        fields = record['launch']
        self.assertEqual(record['argv'], ['--home-session-v1'])
        self.assertEqual(set(record['env']) - {'LC_CTYPE'}, {'XDG_RUNTIME_DIR'})
        self.assertEqual(set(fields), {'version', 'dialIP', 'homeID', 'controlPlaneID', 'caDER', 'fingerprint', 'controlURL', 'hostname', 'joinKey', 'socketPath', 'target'})
        self.assertEqual((fields['version'], fields['joinKey']), (2, 'ab' * 24))
        parent = Path(fields['socketPath']).parent
        self.assertEqual(parent.stat().st_mode & 0o7777, 0o700)
        self.assertEqual(parent.stat().st_uid, os.geteuid())
        with self.session.stream() as stream:
            self.assertEqual(stream.family, socket.AF_UNIX)
            self.session.close()
            self.assertEqual(stream.fileno(), -1)
        self.assertFalse(parent.exists())
        self.assertIsNotNone(self.session._process.returncode)
        self.assertTrue(self.session._process.stdin.closed)
        self.assertTrue(self.session._process.stdout.closed)
        self.assertNotIn('ab' * 24, repr(self.session))
        with self.assertRaises(ClientFailure):
            self.session.start(self.invite, 'client-test')

    def test_runtime_directory_refusals(self) -> None:
        alias = self.prefix / 'alias'
        alias.symlink_to(self.runtime, target_is_directory=True)
        for root in ('', '/', str(alias), str(alias / 'child'), str(self.runtime / '..'), 'relative'):
            with self.subTest(root=root), patch.dict(os.environ, XDG_RUNTIME_DIR=root), self.assertRaises((ClientFailure, OSError)):
                private_runtime()
        self.runtime.chmod(0o750)
        with self.assertRaises(ClientFailure):
            private_runtime()
        self.runtime.chmod(0o700)
        self.assertEqual(private_runtime(), self.runtime)

    def test_strict_pair_complete_health(self) -> None:
        self.join()
        self.assertEqual(self.session.pair(), (PAIR, self.expiry))
        self.responses[b'/pair/complete'] = (403, {'error': 'pairing_denied'})
        self.assertFalse(self.session.complete())
        for status, body in ((200, b" " * 9000), (403, {'error': 'other'}), (200, b'{"credential":"x","credential":"y"}'),
                             (200, dict(credential='bad')), (200, dict(credential=BEARER, extra=1))):
            self.responses[b'/pair/complete'] = (status, body)
            with self.subTest(status=status, body=body), self.assertRaises(ClientFailure):
                self.session.complete()
            before = len(self.requests)
            with self.assertRaises(ClientFailure):
                self.session.complete()
            self.assertEqual(len(self.requests), before)
            self.session.close()
            self.session = ClientSession(self.prefix)
            self.join()
            self.session.pair()
        self.responses[b'/pair/complete'] = (200, dict(credential=BEARER))
        self.assertTrue(self.session.complete())
        self.session.health()
        self.assertIn(('X-HappyRanch-Device-Credential: ' + BEARER).encode(), self.requests[-1][0])
        for status, code, expected in ((403, 'revocation_denied', 'revoked'), (503, 'daemon_unavailable', 'daemonUnavailable'), (503, 'unknown', 'healthUnknown')):
            self.responses[b'/api/v1/health'] = (status, dict(error=code))
            with self.subTest(code=code), self.assertRaisesRegex(ClientFailure, '^' + expected + '$'):
                self.session.health()
        self.assertNotIn(BEARER, repr(self.session))

    def test_pair_refusal_consumes_secret_and_late_complete_is_fenced(self) -> None:
        self.join()
        self.responses[b'/pair/invite'] = (200, dict(state='pending-approval', completion_handle='x', comparison_phrase=PAIR, expires_at='bad'))
        with self.assertRaises(ClientFailure):
            self.session.pair()
        before = len(self.requests)
        with self.assertRaises(ClientFailure):
            self.session.pair()
        self.assertEqual(len(self.requests), before)
        self.assertIsNone(self.session._pair_secret)
        self.session.close()
        self.session = ClientSession(self.prefix)
        self.responses.pop(b'/pair/invite')
        self.join()
        self.session.pair()
        arrived = threading.Event()
        def late(sock: socket.socket) -> None:
            arrived.set()
            self.release.wait(3)
            payload = json.dumps(dict(credential=BEARER)).encode()
            sock.sendall(f'HTTP/1.1 200 OK\r\nContent-Length: {len(payload)}\r\n\r\n'.encode() + payload)
        self.responses[b'/pair/complete'] = late
        errors = []
        def complete() -> None:
            try:
                self.session.complete()
            except ClientFailure as error:
                errors.append(str(error))
        worker = threading.Thread(target=complete)
        worker.start()
        self.assertTrue(arrived.wait(2))
        self.session.close()
        self.release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(errors)
        with self.assertRaises(ClientFailure):
            self.session.authority()

    def test_bootstrap_headers_http_and_pipeline(self) -> None:
        self.authorize()
        for path in ('/auth/bootstrap', '/api/v1/auth/bootstrap/', '/api/v12/auth/bootstrap?x=1'):
            before = len(self.requests)
            with self.browser(path) as client:
                raw = self.all_bytes(client)
            self.assertIn(b'hr_session_', raw)
            self.assertNotIn(BEARER.encode(), raw)
            self.assertEqual(len(self.requests), before + (1 if before == 2 else 0))  # first bridge start checks health
        injected = b'Authorization: Bearer SYNTHETIC_BROWSER_CANARY\r\nX-HappyRanch-Device-Credential: SYNTHETIC_FORGED_CANARY\r\nContent-Length: 5\r\n'
        with self.browser('/api/v1/health', injected, b'a\x00bcdGET /second HTTP/1.1\r\n\r\n') as client:
            self.assertIn(b'200', self.all_bytes(client))
        head, body = self.requests[-1]
        self.assertNotIn(b'SYNTHETIC_', head)
        self.assertEqual(head.count(b'X-HappyRanch-Device-Credential:'), 1)
        self.assertIn(BEARER.encode(), head)
        self.assertEqual(body, b'a\x00bcd')
        for extra in (b'Host: evil\r\n', b'Origin: null\r\n', b'Content-Length: 0\r\nTransfer-Encoding: chunked\r\n', b' folded: x\r\n'):
            before = len(self.requests)
            with self.browser('/bad', extra) as client:
                self.assertEqual(self.all_bytes(client), b'')
            self.assertEqual(len(self.requests), before)

    def test_sse_upgrade_and_bounded_teardown(self) -> None:
        self.authorize()
        def streaming(sock: socket.socket) -> None:
            sock.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\ndata: first\n\n')
            self.release.wait(3)
        self.responses[b'/events'] = streaming
        with self.browser('/events') as client:
            head, extra = read_head(client, time.monotonic() + 2)
            self.assertIn(b'text/event-stream', head)
            if not extra:
                extra = client.recv(100)
            self.assertEqual(extra, b'data: first\n\n')
            start = time.monotonic()
            self.bridge.close()
            self.assertLess(time.monotonic() - start, 5)
            self.assertEqual(client.recv(1), b'')
        self.release.set()
        self.session = ClientSession(self.prefix)
        self.authorize()
        del self.bridge
        def upgrade(sock: socket.socket) -> None:
            sock.sendall(b'HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n')
            sock.sendall(receive(sock, 5, time.monotonic() + 2))
            sock.sendall(receive(sock, 5, time.monotonic() + 2))
        self.responses[b'/socket'] = upgrade
        with self.browser('/socket', b'Connection: Upgrade\r\nUpgrade: websocket\r\n', b'HELLO') as client:
            head, extra = read_head(client, time.monotonic() + 2)
            self.assertIn(b'101', head)
            self.assertEqual(extra + receive(client, 5 - len(extra), time.monotonic() + 2), b'HELLO')
            client.sendall(b'WORLD')
            self.assertEqual(client.recv(5), b'WORLD')

    def test_chunked_request_and_response(self) -> None:
        self.authorize()
        response = b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n3\r\nxyz\r\n0\r\n\r\n'
        self.responses[b'/chunked'] = lambda sock: sock.sendall(response)
        with self.browser('/chunked', b'Transfer-Encoding: chunked\r\nAuthorization: SYNTHETIC_CHUNK_CANARY\r\n', b'3\r\nabc\r\n0\r\n\r\n') as client:
            self.assertEqual(self.all_bytes(client), response)
        self.assertEqual(self.requests[-1][1], b'3\r\nabc\r\n0\r\n\r\n')
        self.assertNotIn(b'SYNTHETIC_CHUNK_CANARY', self.requests[-1][0])

    def test_ready_and_peer_refusals_close_owned_resources(self) -> None:
        config = dict(port=self.connector.server_address[1], bad_ready=True)
        (self.prefix / 'config.json').write_text(json.dumps(config))
        with self.assertRaisesRegex(ClientFailure, '^joinIncomplete$'):
            self.join()
        self.assertIsNotNone(self.session._process.returncode)
        self.assertEqual(list(self.runtime.iterdir()), [])
        self.session = ClientSession(self.prefix)
        config = dict(port=self.connector.server_address[1], status=-18)
        (self.prefix / 'config.json').write_text(json.dumps(config))
        self.join()
        with self.assertRaisesRegex(ClientFailure, '^peerUnavailable$'):
            with self.session.stream():
                self.fail('peer refusal admitted a stream')
        self.assertEqual(len(self.session._sockets), 0)
        self.session.close()
        self.assertEqual(list(self.runtime.iterdir()), [])

    def test_stubborn_launch_is_killed_and_reaped(self) -> None:
        (self.prefix / 'config.json').write_text(json.dumps(dict(stubborn=True)))
        errors = []
        def launch() -> None:
            try:
                self.join()
            except ClientFailure as error:
                errors.append(str(error))
        worker = threading.Thread(target=launch)
        worker.start()
        deadline = time.monotonic() + 2
        while not (self.prefix / 'launch.json').exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue((self.prefix / 'launch.json').exists())
        start = time.monotonic()
        self.session.close()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertLess(time.monotonic() - start, 5)
        self.assertEqual(self.session._process.returncode, -9)
        self.assertTrue(errors)
        self.assertEqual(list(self.runtime.iterdir()), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)

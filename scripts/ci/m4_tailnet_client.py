#!/usr/bin/env python3
"""M4 test-side caller of the unchanged shipped Go C archive.

Native macOS acceptance and explicitly non-acceptance Linux diagnostics only.
"""
from __future__ import annotations

import argparse
import ctypes
import http.client
import io
import json
import os
from pathlib import Path
import re
import select
import shutil
import socket
import ssl
import subprocess
import sys
import time


BASE = "/var/lib/happyranch-m4"
INSTANCE = "i-j6c1pwjqygzrccal4mzh"
PUBLIC_IP = "47.238.219.200"
CONTROL = "https://47-238-219-200.sslip.io"
ROUTE = "/api/v1/orgs/m2-synthetic/teams"
EXPECTED = {"teams": [{"name": "default", "manager": None, "workers": [],
                       "manager_kind": "human", "human_manager": "founder", "is_default": True}]}


HOSTNAME = '47-238-219-200.sslip.io'
JOIN_SECONDS = 90


class DiagnosticComplete(Exception):
    pass


def join_diagnostic(state: Path, stderr: Path, elapsed: float) -> dict:
    """Read private native text; export only fixed labels, counts and clocks."""
    paths = [stderr, *sorted(state.rglob('tailscaled.log*.txt'))]
    observations: dict[str, int] = {}
    patterns = {
        'dns': r'no such host|temporary failure in name resolution',
        'tcp-connect': r'connect: connection refused|network is unreachable|i/o timeout',
        'tls-verify': r'x509:|certificate verify failed|certificate signed by unknown authority',
        'tls-reset': r'(?:https://|tls).*(?:connection reset by peer|unexpected EOF|: EOF)',
        'control-upgrade/noise': r'(?:noise dial|controlhttp|ts2021|Noise handshake).*(?:failed|error|EOF|reset|timeout|unexpected HTTP response)',
        'auth-key-rejected': r'invalid authkey|auth key.*(?:invalid|expired|rejected)|authkey.*(?:invalid|expired|rejected)',
        'register': r'RegisterReq|register request|register response',
        'netmap-wait': r'netmap|map response',
        'derp-connect': r'derp-[0-9]+ connected;',
    }
    texts = []
    for path in paths:
        if not path.exists():
            continue
        assert path.stat().st_size <= 8_000_000, 'private diagnostic log budget'
        texts.append(path.read_text(errors='replace'))
    private = '\n'.join(texts)
    for label, pattern in patterns.items():
        observations[label] = len(re.findall(pattern, private, re.IGNORECASE))
    error_class = next((label for label in ('auth-key-rejected', 'tls-verify', 'control-upgrade/noise', 'tls-reset', 'dns', 'tcp-connect') if observations[label]), 'timeout')
    return dict(stage=error_class, error_class=error_class, elapsed_seconds=round(elapsed, 3),
                deadline_seconds=JOIN_SECONDS, observations=observations,
                port80_attempts=len(re.findall(r'trying noise dial \(http://', private)),
                port443_attempts=len(re.findall(r'trying noise dial \(https://', private)),
                native_text_exported=False)


def control_get(path: str) -> dict:
    """Pin IP and expected SNI; normal trust verification. Status/time only."""
    started = time.monotonic()
    connection = http.client.HTTPConnection(HOSTNAME, timeout=10)
    try:
        raw = socket.create_connection((PUBLIC_IP, 443), timeout=10)
        try:
            connection.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=HOSTNAME)
        except Exception:
            raw.close()
            raise
        connection.request('GET', path)
        response = connection.getresponse()
        assert len(response.read(8193)) <= 8192, 'public response budget'
        return dict(status=response.status, elapsed_seconds=round(time.monotonic() - started, 3))
    finally:
        connection.close()


def application_probe(port: int) -> dict:
    """A proxy-accepted TCP socket alone has no reachability meaning."""
    started = time.monotonic()
    try:
        with socket.create_connection((PUBLIC_IP, port), timeout=3) as raw:
            with ssl.create_default_context().wrap_socket(raw, server_hostname=HOSTNAME):
                return dict(port=port, reachable=True, evidence='verified-TLS', elapsed_seconds=round(time.monotonic()-started, 3))
    except ssl.SSLCertVerificationError:
        # A server replied with a wrong certificate: never turn this into absence.
        return dict(port=port, reachable=True, evidence='untrusted-TLS-response', elapsed_seconds=round(time.monotonic()-started, 3))
    except (ssl.SSLEOFError, ssl.SSLZeroReturnError):
        pass
    except ssl.SSLError:
        return dict(port=port, reachable=True, evidence='unverified-TLS-response', elapsed_seconds=round(time.monotonic()-started, 3))
    except (OSError, TimeoutError):
        pass
    try:
        with socket.create_connection((PUBLIC_IP, port), timeout=3) as raw:
            raw.sendall(f'GET /health HTTP/1.1\r\nHost: {HOSTNAME}\r\nConnection: close\r\n\r\n'.encode())
            received = raw.recv(4096)
            if received:
                return dict(port=port, reachable=True, evidence='response-bytes', response_bytes=len(received), elapsed_seconds=round(time.monotonic()-started, 3))
    except (OSError, TimeoutError):
        pass
    return dict(port=port, reachable=False, evidence='no-TLS-or-HTTP-response-within-bound', elapsed_seconds=round(time.monotonic()-started, 3))


def worker(library: Path, state: Path) -> None:
    """Private pipe protocol. Never echo input, credentials, or native errors."""
    lib = ctypes.CDLL(str(library))
    lib.tsnet_init.argtypes = [ctypes.c_char_p] * 3
    lib.tsnet_conn_dial.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    lib.tsnet_conn_read.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    lib.tsnet_conn_write.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    lib.tsnet_conn_close.argtypes = [ctypes.c_int]
    lib.tsnet_local_addr.restype = ctypes.c_void_p
    lib.tsnet_free_string.argtypes = [ctypes.c_void_p]

    class Stream(io.RawIOBase):
        def __init__(self, handle: int) -> None:
            super().__init__()
            self.handle = handle

        def readable(self) -> bool:
            return True

        def readinto(self, buffer: bytearray) -> int:
            native = ctypes.create_string_buffer(len(buffer))
            size = lib.tsnet_conn_read(self.handle, native, len(buffer))
            if size < 0:
                raise RuntimeError("transport read failed")
            buffer[:size] = native.raw[:size]
            return size

    class Connection:
        def __init__(self, handle: int) -> None:
            self.handle = handle

        def makefile(self, mode: str) -> io.BufferedReader:
            return io.BufferedReader(Stream(self.handle))

    for line in sys.stdin:
        try:
            message = json.loads(line)
            action = message["action"]
            if action == "join":
                assert lib.tsnet_init(message["key"].encode(), CONTROL.encode(), b"synthetic-m4-outside") == 0
                assert lib.tsnet_start() == 0
                deadline = time.monotonic() + JOIN_SECONDS
                while time.monotonic() < deadline:
                    pointer = lib.tsnet_local_addr()
                    if pointer:
                        address = ctypes.string_at(pointer).decode()
                        lib.tsnet_free_string(pointer)
                        break
                    time.sleep(0.2)
                else:
                    raise RuntimeError("join deadline")
                result = {"joined": True, "address": address}
            elif action == "request":
                handle = lib.tsnet_conn_dial(b"tcp", (message["address"] + ":443").encode(), 15000)
                if handle < 0:
                    raise RuntimeError("dial refused")
                try:
                    data = message.get("data", "").encode()
                    headers = [f'{message["method"]} {message["path"]} HTTP/1.1',
                               'Host: home', 'Connection: close', f'Content-Length: {len(data)}']
                    if message.get("credential") is not None:
                        headers.append("X-HappyRanch-Device-Credential: " + message["credential"])
                    request = ("\r\n".join(headers) + "\r\n\r\n").encode() + data
                    while request:
                        size = lib.tsnet_conn_write(handle, request, len(request))
                        if size <= 0:
                            raise RuntimeError("write failed")
                        request = request[size:]
                    response = http.client.HTTPResponse(Connection(handle))
                    response.begin()
                    raw = response.read(1_000_001)
                    assert len(raw) <= 1_000_000
                    result = {"status": response.status, "body": json.loads(raw)}
                    response.close()
                finally:
                    lib.tsnet_conn_close(handle)
            elif action == "derp-proof":
                # The existing upstream diagnostic knob delays log upload. Read
                # only backend log files, and export a strict non-secret subset.
                regions: set[int] = set()
                disabled = False
                for p in state.rglob('tailscaled.log*.txt'):
                    assert p.stat().st_size <= 8_000_000, 'native log budget'
                    for entry in p.read_text(errors='replace').splitlines():
                        try:
                            text = json.loads(entry).get('text', '')
                        except (ValueError, AttributeError):
                            continue
                        for region in re.findall(r'magicsock: derp-([0-9]+) connected;', text):
                            regions.add(int(region))
                        if re.search(r'disabled udp[46]? per TS_DEBUG_ALWAYS_USE_DERP', text):
                            disabled = True
                assert regions == {999}, 'missing/wrong selected DERP region'
                assert disabled, 'missing client UDP-disable evidence'
                result = {"selected_regions": sorted(regions), "udp_disabled": disabled,
                          "source": "unchanged tsnet backend filch logs; strict projection"}
            else:
                raise RuntimeError("unknown worker operation")
            print(json.dumps(result), flush=True)
        except Exception:
            # Even JSON/native exceptions may contain response credential bytes.
            print('{"worker_error":"operation_failed"}', flush=True)


class Bridge:
    def __init__(self, library: Path, scratch: Path) -> None:
        self.state = scratch / "client-state"
        self.state.mkdir(mode=0o700)
        environment = dict(os.environ, XDG_CONFIG_HOME=str(self.state),
                           TS_DEBUG_ALWAYS_USE_DERP="true", TS_DEBUG_LOGTAIL_FLUSHDELAY="24h",
                           TS_DEBUG_NOISE_DIAL="true")
        for key in ("TS_AUTHKEY", "TS_AUTH_KEY", "TSNET_FORCE_LOGIN", "TS_DEBUGSYSPOLICY_AUTH_KEY"):
            if key in environment:
                raise RuntimeError("unset ambient tsnet auth variables before M4")
        self.stderr_path = scratch / "client-private.stderr"
        self.stderr = self.stderr_path.open("wb")
        self.join_diagnostics: dict = {}
        self.process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--worker", str(library), "--worker-state", str(self.state)],
            env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr, text=True,
        )

    def call(self, action: str, **fields: object) -> dict:
        started = time.monotonic()
        try:
            return self._call(action, **fields)
        finally:
            if action == 'join':
                self.join_diagnostics = join_diagnostic(self.state, self.stderr_path, time.monotonic()-started)

    def _call(self, action: str, **fields: object) -> dict:
        assert self.process.stdin is not None and self.process.stdout is not None
        self.process.stdin.write(json.dumps(dict(action=action, **fields)) + "\n")
        self.process.stdin.flush()
        if not select.select([self.process.stdout], [], [], JOIN_SECONDS + 10 if action == "join" else 35)[0]:
            raise RuntimeError(f"client {action} deadline exceeded")
        line = self.process.stdout.readline()
        try:
            result = json.loads(line)
        except ValueError:
            raise RuntimeError(f"client {action} response unavailable") from None
        if "worker_error" in result:
            raise RuntimeError(f"client {action} failed (private native detail not exported)")
        return result

    def close(self) -> None:
        # Do not flush upstream diagnostic logs on graceful Go shutdown. These
        # are disposable nodes; reap the entire embedded engine with its worker.
        self.process.kill()
        self.process.wait(timeout=15)
        self.stderr.close()
        shutil.rmtree(self.state)


def build_archive(root: Path, scratch: Path) -> Path:
    native = scratch / "native"
    native.mkdir()
    import platform
    system = 'darwin' if sys.platform == 'darwin' else 'linux'
    architecture = {'arm64': 'arm64', 'aarch64': 'arm64', 'x86_64': 'amd64'}[platform.machine()]
    env = dict(os.environ, GOTOOLCHAIN="go1.23.12", CGO_ENABLED="1", GOOS=system, GOARCH=architecture,
               GOMAXPROCS="2", GOFLAGS="-p=2")
    archive = native / "libtsnet.a"
    library = native / ('libtsnet.dylib' if system == 'darwin' else 'libtsnet.so')
    link = (["cc", "-dynamiclib", "-Xlinker", "-force_load", "-Xlinker", str(archive),
             "-framework", "CoreFoundation", "-framework", "Security", "-framework", "SystemConfiguration",
             "-framework", "IOKit",
             "-lresolv", "-o", str(library)] if system == 'darwin' else
            ["cc", "-shared", "-o", str(library), "-Wl,--whole-archive", str(archive),
             "-Wl,--no-whole-archive", "-pthread", "-ldl", "-lm"])
    commands = [["go", "version"],
                ["go", "build", "-mod=readonly", "-buildmode=c-archive", "-o", str(archive), "."], link]

    for number, command in enumerate(commands):
        with (native / f"build-{number}.log").open('wb') as stream:
            result = subprocess.run(command, cwd=root / "app/mac/tsnet-bridge", env=env,
                                    stdout=stream, stderr=subprocess.STDOUT, timeout=900, check=False)
        if result.returncode:
            raise RuntimeError(f"unchanged archive build step {number} failed, exit={result.returncode}")
    version = (native / 'build-0.log').read_text().strip()
    if not version.endswith(f'go1.23.12 {system}/{architecture}'):
        raise RuntimeError('incorrect effective Go toolchain')
    return library


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', type=Path, required=True)
    parser.add_argument('--worker-state', type=Path, required=True)
    args = parser.parse_args()
    worker(args.worker, args.worker_state)

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
import math
import os
from pathlib import Path
import re
import select
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import sysconfig
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
NATIVE_HOST = 'm4-tsnet-host'
JOIN_PHASES = ('join-call', 'native-start', 'join-wait')
DIAGNOSTIC_LABELS = ('dns', 'tcp-connect', 'tls-verify', 'tls-reset', 'control-upgrade/noise',
                     'auth-key-rejected', 'register', 'netmap-wait', 'derp-connect', 'timeout')
BRIDGE_ERROR_CLASSES = {'state-dir', 'log-dir', 'executable', 'control-url', 'already-started', 'not-initialised', 'unknown'}


def bridge_error_text(lib: ctypes.CDLL) -> str:
    """Private native text only; every ABI allocation is freed."""
    pointer = lib.tsnet_last_error()
    if not pointer:
        return ''
    try:
        return ctypes.string_at(pointer).decode(errors='replace').lower()
    finally:
        lib.tsnet_free_string(pointer)


def bridge_error_class(lib: ctypes.CDLL) -> str:
    """Read/free the shipped ABI's error allocation; export a fixed label only."""
    text = bridge_error_text(lib)
    if text == 'tsnet already initialised':
        return 'already-started'
    if text in {'tsnet not initialised — call tsnet_init first', 'tsnet context is nil'}:
        return 'not-initialised'
    prefix = 'tsnet.start failed: tsnet: '
    if not text.startswith(prefix):
        return 'unknown'
    text = text[len(prefix):]
    if text == 'cannot find executable path':
        return 'executable'
    if text.startswith(('logpolicy.config.save ', 'logpolicy.loadconfig ', 'logpolicy.config.validate ', 'error creating filch:')):
        return 'log-dir'
    if text.startswith(('mkdir ', 'stat ', 'lstat ', 'rename ', 'expected old tslib path ', 'creating state directory:')) or text.endswith(' is not a directory') or text == '$home is not defined':
        return 'state-dir'
    if text.startswith(('starting backend: controlclient.new: no server url specified', 'starting backend: parse ')):
        return 'control-url'
    return 'unknown'


def exception_class(error: Exception) -> str:
    name = type(error).__name__
    return name if name in {'AssertionError', 'RuntimeError', 'TimeoutError', 'OSError', 'BrokenPipeError',
                           'FileNotFoundError', 'ValueError', 'UnicodeDecodeError', 'JSONDecodeError',
                           'HTTPError', 'URLError', 'SSLError', 'SSLCertVerificationError',
                           'TimeoutExpired', 'CalledProcessError'} else 'Unknown'


def safe_number(value: object) -> int | float | None:
    return value if type(value) in (int, float) and 0 <= value <= 10_000_000 and math.isfinite(value) else None


def safe_join_diagnostics(value: dict) -> dict:
    """Closed public projection, even when a private/native producer changes."""
    observations = value.get('observations', {})
    return dict(stage=value.get('stage') if value.get('stage') in DIAGNOSTIC_LABELS else 'Unknown',
                error_class=value.get('error_class') if value.get('error_class') in DIAGNOSTIC_LABELS else 'Unknown',
                **{key: safe_number(value.get(key)) for key in
                   ('elapsed_seconds', 'deadline_seconds', 'port80_attempts', 'port443_attempts')},
                observations={key: safe_number(observations.get(key)) for key in DIAGNOSTIC_LABELS[:-1]},
                native_text_exported=False)


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
    # The native main executable already contains and initialized the archive.
    # Never dlopen a Go archive repackaged as a dylib in a foreign Python host.
    lib = ctypes.CDLL(None)
    lib.tsnet_init.argtypes = [ctypes.c_char_p] * 3
    lib.tsnet_conn_dial.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    lib.tsnet_conn_read.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    lib.tsnet_conn_write.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    lib.tsnet_conn_close.argtypes = [ctypes.c_int]
    lib.tsnet_local_addr.restype = ctypes.c_void_p
    lib.tsnet_last_error.restype = ctypes.c_void_p
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
            if action == 'precondition':
                # An exclusively owned malformed config must stop Start before
                # NewLogger/netmon/engine. No enrollment key or join is used.
                code = lib.tsnet_init(b'synthetic-offline-only', b'https://example.invalid', b'synthetic-offline')
                if code:
                    print(json.dumps(dict(bridge_error_class=bridge_error_class(lib))), flush=True)
                assert code == 0
                code = lib.tsnet_start()
                label = bridge_error_class(lib)
                print(json.dumps(dict(bridge_error_class=label)), flush=True)
                text = bridge_error_text(lib)
                assert code == -2 and label == 'log-dir'
                assert text.startswith('tsnet.start failed: tsnet: logpolicy.loadconfig ')
                assert text.endswith(': unexpected end of json input')
                result = {'precondition': True}
            elif action == "join":
                print('{"join_phase":"join-call"}', flush=True)
                code = lib.tsnet_init(message["key"].encode(), CONTROL.encode(), b"synthetic-m4-outside")
                if code:
                    print(json.dumps(dict(bridge_error_class=bridge_error_class(lib))), flush=True)
                print(json.dumps(dict(join_phase='native-start', return_code=code)), flush=True)
                assert code == 0
                code = lib.tsnet_start()
                if code:
                    print(json.dumps(dict(bridge_error_class=bridge_error_class(lib))), flush=True)
                print(json.dumps(dict(join_phase='join-wait', return_code=code)), flush=True)
                assert code == 0
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
        except Exception as error:
            # Even JSON/native exceptions may contain response credential bytes.
            print(json.dumps(dict(worker_error='operation_failed', exception_class=exception_class(error))), flush=True)


class Bridge:
    def __init__(self, library: Path, scratch: Path) -> None:
        self.state = scratch / "client-state"
        if sys.platform != 'darwin':
            self.state.mkdir(mode=0o700)
        # The native runner exclusively creates the Darwin target before its
        # alias. Refuse symlink/foreign/non-private targets before worker start.
        info = self.state.lstat()
        assert stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700, 'private client state required'
        environment = dict(os.environ, XDG_CONFIG_HOME=str(self.state),
                           PYTHONHOME=sys.base_prefix,
                           TS_DEBUG_ALWAYS_USE_DERP="true", TS_DEBUG_LOGTAIL_FLUSHDELAY="24h",
                           TS_DEBUG_NOISE_DIAL="true")
        for key in ("TS_AUTHKEY", "TS_AUTH_KEY", "TSNET_FORCE_LOGIN", "TS_DEBUGSYSPOLICY_AUTH_KEY"):
            if key in environment:
                raise RuntimeError("unset ambient tsnet auth variables before M4")
        self.stderr_path = scratch / "client-private.stderr"
        self.stderr = self.stderr_path.open("wb")
        self.join_diagnostics: dict = {}
        self.join_phases: list[dict] = []
        self.join_phase = 'join-call'
        self.join_error = 'operation-failed'
        self.worker_exception = 'Unknown'
        self.bridge_error_class: str | None = None
        self.response_buffer = b''
        self.process = subprocess.Popen(
            [str(library), str(Path(__file__).resolve()), "--worker", str(library), "--worker-state", str(self.state)],
            env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr, text=True,
        )

    def call(self, action: str, **fields: object) -> dict:
        started = time.monotonic()
        if action == 'join':
            self.join_phases = [dict(phase='join-call', elapsed_seconds=0, return_code=None)]
            self.phase_started = started
        try:
            return self._call(action, **fields)
        finally:
            if action == 'join':
                self.join_phases[-1]['elapsed_seconds'] = round(time.monotonic() - self.phase_started, 3)
                self.join_diagnostics = safe_join_diagnostics(join_diagnostic(self.state, self.stderr_path, time.monotonic()-started))

    def _call(self, action: str, **fields: object) -> dict:
        assert self.process.stdin is not None and self.process.stdout is not None
        self.process.stdin.write(json.dumps(dict(action=action, **fields)) + "\n")
        self.process.stdin.flush()
        deadline = time.monotonic() + (JOIN_SECONDS + 10 if action == "join" else 35)
        while True:
            # Read bytes directly: TextIOWrapper read-ahead can hide a buffered
            # progress/result line from select(). One original deadline remains.
            while b'\n' not in self.response_buffer:
                remaining = max(0, deadline - time.monotonic())
                if not select.select([self.process.stdout], [], [], remaining)[0]:
                    self.join_error = 'worker-deadline'
                    raise RuntimeError('client response deadline')
                data = os.read(self.process.stdout.fileno(), 65536)
                if not data:
                    self.join_error = 'worker-response-unavailable'
                    raise RuntimeError('client response unavailable')
                self.response_buffer += data
                assert len(self.response_buffer) <= 1_000_001, 'worker response budget'
            line, self.response_buffer = self.response_buffer.split(b'\n', 1)
            try:
                result = json.loads(line)
            except ValueError:
                self.join_error = 'worker-response-unavailable'
                raise RuntimeError('client response unavailable') from None
            if 'bridge_error_class' in result:
                assert action in {'join', 'precondition'} and set(result) == {'bridge_error_class'}
                label = result['bridge_error_class']
                self.bridge_error_class = label if isinstance(label, str) and label in BRIDGE_ERROR_CLASSES else 'unknown'
                continue
            if 'join_phase' in result:
                assert action == 'join'
                phase = result['join_phase']
                if phase == 'join-call':
                    assert len(self.join_phases) == 1 and set(result) == {'join_phase'}
                    continue
                assert len(self.join_phases) < 3 and phase == JOIN_PHASES[len(self.join_phases)]
                assert set(result) == {'join_phase', 'return_code'} and type(result['return_code']) is int
                self.join_phases[-1].update(elapsed_seconds=round(time.monotonic() - self.phase_started, 3),
                                            return_code=result['return_code'])
                # A nonzero native return belongs to the phase that just ended.
                if result['return_code'] == 0:
                    self.join_phase = phase
                    self.phase_started = time.monotonic()
                    self.join_phases.append(dict(phase=phase, elapsed_seconds=0, return_code=None))
                continue
            if 'worker_error' in result:
                self.join_error = 'worker-failed'
                name = result.get('exception_class')
                self.worker_exception = name if name in {'AssertionError', 'RuntimeError', 'ValueError', 'OSError'} else 'Unknown'
                raise RuntimeError('client operation failed; private detail withheld')
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
    library = native / NATIVE_HOST
    # Preserve build_archive's paired-host/archive path contract for both the
    # native runner and the unchanged Linux diagnostic coordinator.
    archive = library.with_suffix('.a')
    framework = sysconfig.get_config_var('PYTHONFRAMEWORK')
    python_library = (Path(sys.base_prefix) / framework if framework else
                      Path(sysconfig.get_config_var('LIBDIR')) / sysconfig.get_config_var('INSTSONAME'))
    assert python_library.is_file(), 'existing shared Python runtime required'
    host = [str(root / 'scripts/ci/m4_tsnet_host.c'), '-I', str(root / 'app/mac/tsnet-bridge'),
            '-DM4_PYTHON_LIBRARY=' + json.dumps(str(python_library)), '-Wall', '-Wextra', '-Werror']
    link = (["cc", *host, "-Wl,-export_dynamic", "-Xlinker", "-force_load", "-Xlinker", str(archive),
             "-framework", "CoreFoundation", "-framework", "Security", "-framework", "SystemConfiguration",
             "-framework", "IOKit",
             "-lresolv", "-o", str(library)] if system == 'darwin' else
            ["cc", *host, "-Wl,--export-dynamic", "-o", str(library), "-Wl,--whole-archive", str(archive),
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

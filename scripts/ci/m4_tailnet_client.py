#!/usr/bin/env python3
"""M4 test-side caller of the unchanged shipped Go C archive.

Native macOS acceptance and explicitly non-acceptance Linux diagnostics only.
"""
from __future__ import annotations

import argparse
import ctypes
import http.client
import ipaddress
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
DERP_PROOF_LABELS = {'no-log-files', 'log-budget', 'no-decodable-entries', 'region-missing',
                     'region-unexpected', 'udp-disable-missing', 'unknown', 'passed'}

PACKET_PROOF_LABELS = {'unknown', 'capture-unavailable', 'capture-start', 'capture-budget',
                       'capture-deadline', 'capture-cleanup', 'capture-incomplete', 'capture-format',
                       'metadata-missing', 'udp-observed', 'udp-socket-observed', 'socket-evidence',
                       'relay-endpoint-unexpected', 'relay-traffic-missing', 'request-failed', 'passed'}
PACKET_PROOF_LABELS.update({'identity-missing', 'identity-malformed', 'identity-stale',
                            'identity-region', 'identity-endpoint', 'identity-upstream', 'identity-log-conflict'})
IDENTITY_COUNTS = dict(region_count=1, region=999, node_count=1, endpoint_matches=1,
                       upstream_count=0, auto_update=0, mesh_count=0)
PACKET_PROOF_COUNTS = ('packet_count', 'native_packet_count', 'udp_count', 'udp_socket_count',
                       'tcp_socket_observations', 'relay_tx_bytes', 'relay_rx_bytes')


def require_relay_identity(value: object, diagnostic: dict) -> int:
    """Only an authenticated, current envelope may supply this projection."""
    diagnostic['label'] = 'identity-missing'
    assert isinstance(value, dict) and value, 'missing runtime relay identity'
    diagnostic['label'] = 'identity-malformed'
    assert set(value) == {'label', 'observed_at', *IDENTITY_COUNTS}, 'runtime identity shape'
    assert value['label'] == 'passed' and all(type(value[key]) is int for key in ('observed_at', *IDENTITY_COUNTS)), 'runtime identity types/status'
    assert all(0 <= value[key] <= 16_000_000 for key in IDENTITY_COUNTS), 'runtime identity bounds'
    diagnostic['label'] = 'identity-stale'
    assert 0 <= int(time.time()) - value['observed_at'] <= 120, 'runtime identity freshness'
    diagnostic['label'] = 'identity-region'
    assert value['region_count'] == value['node_count'] == 1 and value['region'] == 999, 'runtime region identity'
    diagnostic['label'] = 'identity-endpoint'
    assert value['endpoint_matches'] == 1, 'runtime relay endpoint'
    diagnostic['label'] = 'identity-upstream'
    assert value['upstream_count'] == value['auto_update'] == value['mesh_count'] == 0, 'runtime relay sources'
    return value['region']


def safe_packet_diagnostics(value: object) -> dict:
    value = value if isinstance(value, dict) else {}
    label = value.get('label')
    return dict(label=label if isinstance(label, str) and label in PACKET_PROOF_LABELS else 'unknown',
                **{key: min(value[key], 16_000_000) if type(value.get(key)) is int and value[key] >= 0 else 0
                   for key in PACKET_PROOF_COUNTS})


CAPTURE_SHAPES = {'unknown', 'passed', 'prefix', 'metadata', 'multiline', 'truncated',
                  'transport', 'endpoint', 'attribution', 'counters'}
CAPTURE_COUNTS = ('header_lines', 'metadata_lines', 'ipv4_packets', 'ipv6_packets',
                  'other_process_packets', 'counter_lines', 'captured', 'received',
                  'kernel_drops', 'interface_drops', 'metadata_drops', 'stderr_other_lines',
                  *('unparsed_' + shape for shape in sorted(CAPTURE_SHAPES - {'unknown', 'passed'})))
OBSERVER_LABELS = PACKET_PROOF_LABELS | {'observer-child-start', 'observer-request', 'observer-child-cleanup'}

PROBE_WORDS = {'IP', 'IP6', 'pktap', 'proc', 'eproc', 'svc', 'in', 'out', 'pid',
               'Flags', 'length', 'UDP', 'tcp', 'udp'}
PROBE_PUNCTUATION = set('()[],:;<>.=+-/|*')
PROBE_SKELETON_LINES = 3
PROBE_SKELETON_INPUT = 2048
PROBE_SKELETON_OUTPUT = 256


def structural_skeleton(line: object) -> str:
    """Probe-only closed token vocabulary; never return any unmatched text."""
    if type(line) is not str:
        return 'unknown'
    if len(line) > PROBE_SKELETON_INPUT:
        return 'overlong'
    if not line or any(not (' ' <= char <= '~' or char == '\t') for char in line):
        return 'unknown'
    tokens = []
    for token in re.findall(r'[A-Za-z0-9_.]+|[ \t]+|.', line):
        if token.isspace():
            continue
        if token in PROBE_WORDS or token in PROBE_PUNCTUATION:
            tokens.append(token)
        elif token.isdecimal():
            tokens.append('N')
        elif re.fullmatch(r'(?:0x[0-9a-fA-F]+|[0-9a-fA-F.]+)', token):
            tokens.append('A')
        elif re.fullmatch(r'[A-Za-z0-9_.]+', token):
            tokens.append('W')
        else:
            return 'unknown'
    skeleton = ' '.join(tokens)
    return skeleton if 0 < len(skeleton) <= PROBE_SKELETON_OUTPUT else 'overlong'


def safe_probe_skeletons(value: object) -> list[str]:
    """Revalidate the closed projection at the probe's public sink."""
    if type(value) is not list:
        return ['unknown']
    result = []
    allowed = PROBE_WORDS | PROBE_PUNCTUATION | {'A', 'N', 'W'}
    for skeleton in value[:PROBE_SKELETON_LINES]:
        if type(skeleton) is not str:
            result.append('unknown')
        elif len(skeleton) > PROBE_SKELETON_OUTPUT:
            result.append('overlong')
        elif skeleton in {'unknown', 'overlong'}:
            result.append(skeleton)
        elif skeleton and all(token in allowed for token in skeleton.split(' ')):
            result.append(skeleton)
        else:
            result.append('unknown')
    return result


def safe_capture_diagnostics(value: object) -> dict:
    value = value if isinstance(value, dict) else {}
    label = value.get('label')
    return dict(label=label if isinstance(label, str) and label in CAPTURE_SHAPES else 'unknown',
                **{key: min(value[key], 16_000_000) if type(value.get(key)) is int and value[key] >= 0 else 0
                   for key in CAPTURE_COUNTS})


def capture_detail(diagnostic: dict) -> dict:
    return diagnostic.setdefault('capture_diagnostics', safe_capture_diagnostics(None))


def capture_refusal(diagnostic: dict, shape: str) -> None:
    detail = capture_detail(diagnostic)
    detail['label'] = shape
    detail['unparsed_' + shape] += 1
    diagnostic['label'] = 'capture-format'
    raise AssertionError('unsupported capture structure')


def packet_evidence(text: str, pid: int, diagnostic: dict, *, endpoint: tuple[str, int] = (PUBLIC_IP, 443)) -> None:
    """Apple -tt -nn -q -k PD. No capture filter; tuples stay private.

    Parse metadata BEFORE protocol bodies. Unsupported bodies/continuations may
    belong only to positively identified other processes. Kernel receive records
    need a reverse native flow; an absent/zero PID never identifies another process.
    The local observer precondition uses this same parser with its owned endpoint.
    """
    detail = capture_detail(diagnostic)
    # Only the credential-free observer probe enables this projection. Keep
    # first unrecognized prefix shapes, never the original lines or substrings.
    if type(diagnostic.get('_probe_skeletons')) is list:
        for line in text.splitlines():
            if not re.fullmatch(r'[0-9]+\.[0-9]+ \(([^)]*)\) (.+)', line):
                diagnostic['_probe_skeletons'].append(structural_skeleton(line))
                if len(diagnostic['_probe_skeletons']) >= PROBE_SKELETON_LINES:
                    break
    records: list[tuple[str, str, list[str]]] = []
    for line in text.splitlines():
        prefix = re.fullmatch(r'[0-9]+\.[0-9]+ \(([^)]*)\) (.+)', line)
        if prefix:
            metadata, body = prefix.groups()
            records.append((metadata, body, []))
        elif line[:1].isspace() and records:
            records[-1][2].append(line)
        else:
            capture_refusal(diagnostic, 'prefix')
    if text and not text.endswith('\n'):
        capture_refusal(diagnostic, 'truncated')
    rows = []
    flows = set()
    for metadata, body, continuations in records:
        diagnostic['packet_count'] += 1
        fields = metadata.split(', ')
        directions = [part for part in fields if part in {'in', 'out'}]
        # Apple prints an empty proc/eproc value when a name/UUID exists but
        # pid == -1 and only P was requested. Zero is also not process evidence.
        if len(directions) != 1 or not all(part in {'in', 'out'} or re.fullmatch(r'e?proc [0-9]{0,10}', part) for part in fields):
            capture_refusal(diagnostic, 'metadata')
        detail['metadata_lines'] += 1
        identity_fields = [part for part in fields if part.startswith(('proc ', 'eproc '))]
        if len({part.split(' ', 1)[0] for part in identity_fields}) != len(identity_fields):
            capture_refusal(diagnostic, 'metadata')
        identities = [int(part.split(' ', 1)[1] or '0') for part in identity_fields]
        native = pid in identities
        other = bool(identities) and all(identity > 0 and identity != pid for identity in identities)
        diagnostic['native_packet_count'] += int(native)
        if other:
            detail['other_process_packets'] += 1
            continue
        if continuations:
            capture_refusal(diagnostic, 'multiline')
        if re.search(r'\[\||truncated|\[invalid|bad (?:hdr|length)', body):
            capture_refusal(diagnostic, 'truncated')
        match = re.fullmatch(r'(IP6?) (\S+) > (\S+): (tcp |UDP, length )([0-9]{1,10})', body)
        if not match:
            capture_refusal(diagnostic, 'transport')
        family, source, destination, protocol, size_text = match.groups()
        detail['ipv6_packets' if family == 'IP6' else 'ipv4_packets'] += 1
        size = int(size_text)
        try:
            endpoints = []
            for value in (source, destination):
                host, port_text = value.rsplit('.', 1)
                host_ip, port = ipaddress.ip_address(host), int(port_text)
                if host_ip.version != (6 if family == 'IP6' else 4) or not 0 < port <= 65535 or size > 65535:
                    raise ValueError('endpoint bounds')
                endpoints.append((str(host_ip), port))
        except ValueError:
            capture_refusal(diagnostic, 'endpoint')
        origin, target = endpoints
        direction = directions[0]
        diagnostic['label'] = 'metadata-missing'
        if not native and direction != 'in':
            detail['label'] = 'attribution'
            detail['unparsed_attribution'] += 1
            raise AssertionError('unattributed egress')
        if native and protocol != 'tcp ':
            diagnostic['udp_count'] += 1
        if native and direction == 'out' and protocol == 'tcp ':
            diagnostic['label'] = 'relay-endpoint-unexpected'
            assert target == endpoint, 'unexpected native TCP destination'
            flows.add((origin, target))
            diagnostic['relay_tx_bytes'] += size
        rows.append((origin, target, protocol, size, direction, native))
    diagnostic['label'] = 'udp-observed'
    assert diagnostic['udp_count'] == 0, 'native UDP observed'
    for origin, target, protocol, size, direction, native in rows:
        if direction == 'in':
            if protocol != 'tcp ' or (target, origin) not in flows:
                capture_refusal(diagnostic, 'attribution')
            diagnostic['native_packet_count'] += int(not native)
            diagnostic['relay_rx_bytes'] += size
    diagnostic['label'] = 'relay-traffic-missing'
    assert diagnostic['relay_tx_bytes'] > 0 and diagnostic['relay_rx_bytes'] > 0, 'missing bidirectional relay payload'
    detail['label'] = 'passed'


class PacketCapture:
    """One private macOS capture, with OS-enforced lifetime and file limits."""

    seconds = 35
    byte_limit = 2_000_000
    packet_limit = 4096

    def __init__(self, scratch: Path, diagnostic: dict) -> None:
        self.diagnostic = diagnostic
        self.process: subprocess.Popen | None = None
        self.started = time.monotonic()
        self.paths = [scratch / 'packet-private.txt', scratch / 'packet-private.stderr']
        self.streams = []

    def start(self) -> None:
        self.diagnostic['label'] = 'capture-unavailable'
        assert sys.platform == 'darwin' and Path('/usr/sbin/tcpdump').is_file() and Path('/usr/sbin/lsof').is_file(), 'native capture tools required'
        for path in self.paths:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            self.streams.append(os.fdopen(fd, 'wb'))
        # Existing Python/OS tools only. Limits are set AFTER sudo's policy and
        # survive exec; no setting, dependency, routing or firewall is changed.
        launcher = '''import resource,select,signal,subprocess,sys
signal.signal(signal.SIGALRM,signal.SIG_DFL)
signal.alarm(45)
def limits():
    resource.setrlimit(resource.RLIMIT_FSIZE,(2000000,2000000))
    signal.alarm(35)
child=subprocess.Popen(sys.argv[1:],stdin=subprocess.DEVNULL,preexec_fn=limits)
ready=select.select([sys.stdin],[],[],35)[0]
stopped=bool(ready) and sys.stdin.buffer.readline(16)==b'stop\\n'
early=child.poll() is not None
if not early:
    child.send_signal(signal.SIGINT)
try:
    code=child.wait(timeout=5)
except subprocess.TimeoutExpired:
    child.kill()
    child.wait(timeout=5)
    code=125
print('M4_CAPTURE_END=%d:%d'%(code,int(early or not stopped)),flush=True)
'''
        self.process = subprocess.Popen(
            ['/usr/bin/sudo', '-n', sys.executable, '-I', '-c', launcher,
             '/usr/sbin/tcpdump', '-i', 'pktap,all', '-nn', '-tt', '-q', '-k', 'PD',
             '--immediate-mode', '-K', '-s', '512', '-B', '4096', '-c', str(self.packet_limit)],
            stdin=subprocess.PIPE, stdout=self.streams[0], stderr=self.streams[1])
        self.diagnostic['label'] = 'capture-start'
        deadline = min(self.started + self.seconds, time.monotonic() + 5)
        while time.monotonic() < deadline:
            raw = self.paths[1].read_bytes()
            assert len(raw) < 65536 and self.process.poll() is None, 'capture startup failed'
            if b'listening on' in raw and b'PKTAP' in raw:
                return
            time.sleep(0.05)
        raise RuntimeError('capture startup deadline')

    def sockets(self, pid: int) -> None:
        self.diagnostic['label'] = 'socket-evidence'
        result = subprocess.run(['/usr/sbin/lsof', '-nP', '-a', '-p', str(pid), '-i', '-FpfP'],
                                capture_output=True, timeout=5, check=False)
        assert result.returncode == 0 and not result.stderr and len(result.stdout) < 65536, 'socket observation unavailable'
        lines = result.stdout.decode('ascii').splitlines()
        assert lines and lines[0] == 'p' + str(pid), 'socket identity'
        assert all(re.fullmatch(r'p[0-9]+|f[0-9]+|P(?:TCP|UDP)', line) for line in lines), 'socket format'
        self.diagnostic['udp_socket_count'] += lines.count('PUDP')
        self.diagnostic['label'] = 'udp-socket-observed'
        assert 'PUDP' not in lines, 'native UDP socket observed'
        self.diagnostic['label'] = 'socket-evidence'
        assert 'PTCP' in lines, 'missing positive native TCP socket control'
        self.diagnostic['tcp_socket_observations'] += 1

    def stop(self) -> None:
        try:
            if self.process is not None:
                if self.process.poll() is None:
                    assert self.process.stdin is not None
                    self.process.stdin.write(b'stop\n')
                    self.process.stdin.flush()
                    self.process.stdin.close()
                # The root supervisor owns and waits for its exact child. No
                # process-name search, PID inference or broad signal is needed.
                self.process.wait(timeout=max(1, self.started + 50 - time.monotonic()))
        finally:
            for stream in self.streams:
                stream.close()

    def evidence(self, pid: int, *, endpoint: tuple[str, int] = (PUBLIC_IP, 443)) -> None:
        diagnostic = self.diagnostic
        diagnostic['label'] = 'capture-budget'
        assert all(p.stat().st_size < self.byte_limit for p in self.paths), 'capture byte budget'
        try:
            raw, errors = [p.read_text() for p in self.paths]
        except UnicodeError:
            capture_refusal(diagnostic, 'prefix')
        diagnostic['label'] = 'capture-deadline'
        assert time.monotonic() < self.started + self.seconds, 'capture deadline'
        diagnostic['label'] = 'capture-incomplete'
        assert self.process is not None and self.process.returncode == 0, 'capture did not finish cleanly'
        assert raw.endswith('M4_CAPTURE_END=0:0\n'), 'capture child not positively stopped/reaped'
        raw = raw.removesuffix('M4_CAPTURE_END=0:0\n')
        detail = capture_detail(diagnostic)
        detail['header_lines'] = len(re.findall(r'^.*listening on .*, link-type PKTAP(?: |$)', errors, re.M))
        captured = re.findall(r'^([0-9]+) packets? captured$', errors, re.M)
        received = re.findall(r'^([0-9]+) packets? received by filter$', errors, re.M)
        dropped = re.findall(r'^([0-9]+) packets? dropped by kernel$', errors, re.M)
        interface_drops = re.findall(r'^([0-9]+) packets? dropped by interface$', errors, re.M)
        metadata_drops = re.findall(r'^([0-9]+) drops? by metadata filter$', errors, re.M)
        counters = (captured, received, dropped, interface_drops, metadata_drops)
        detail['counter_lines'] = sum(map(len, counters))
        detail['stderr_other_lines'] = max(0, len(errors.splitlines()) - detail['header_lines'] - detail['counter_lines'])
        for key, values in zip(('captured', 'received', 'kernel_drops', 'interface_drops', 'metadata_drops'), counters):
            if len(values) == 1:
                detail[key] = min(int(values[0]), 16_000_000)
        detail['label'] = 'counters'
        assert detail['header_lines'] == 1, 'capture header absent/duplicate'
        assert len(interface_drops) <= 1 and len(metadata_drops) <= 1 and all(value == '0' for value in metadata_drops), 'filtered/duplicate capture counters'
        assert len(captured) == len(received) == len(dropped) == 1 and captured == received and dropped == ['0'] and all(value == '0' for value in interface_drops), 'missing/dropped/unread capture evidence'
        diagnostic['label'] = 'capture-budget'
        assert int(captured[0]) < self.packet_limit, 'capture packet budget'
        packet_evidence(raw, pid, diagnostic, endpoint=endpoint)
        diagnostic['label'] = 'capture-incomplete'
        assert int(captured[0]) == diagnostic['packet_count'], 'partial packet stream'
        diagnostic['label'] = 'capture-deadline'
        assert time.monotonic() < self.started + self.seconds, 'capture parsing deadline'


def observer_self_test(scratch: Path, diagnostic: dict) -> None:
    """Exercise the actual observer and both lsof controls before enrollment.

    A short-lived child keeps one synthetic loopback HTTP connection open until
    after both socket observations. No bridge, credential or outside request.
    """
    directory = scratch / 'observer-self-test'
    directory.mkdir(mode=0o700)
    try:
        observe_local_request(directory, diagnostic)
    finally:
        try:
            shutil.rmtree(directory)
        except Exception:
            diagnostic['label'] = 'capture-cleanup'
            raise


def observe_local_request(directory: Path, diagnostic: dict) -> None:
    capture = PacketCapture(directory, diagnostic)
    child: subprocess.Popen | None = None
    server = socket.socket()
    connection: socket.socket | None = None
    request = b'GET /observer-self-test HTTP/1.0\r\n\r\n'
    response = b'HTTP/1.0 200 OK\r\nContent-Length: 3\r\n\r\nOK\n'
    child_code = '''import signal,socket,sys
signal.alarm(25)
s=socket.create_connection(('127.0.0.1',int(sys.argv[1])),timeout=5)
print('ready',flush=True)
command=sys.stdin.buffer.readline(16)
if command==b'stop\\n':
    s.close()
    sys.exit(0)
assert command==b'go\\n'
s.sendall(b'GET /observer-self-test HTTP/1.0\\r\\n\\r\\n')
expected=b'HTTP/1.0 200 OK\\r\\nContent-Length: 3\\r\\n\\r\\nOK\\n'
data=b''
while len(data)<len(expected):
    part=s.recv(len(expected)-len(data))
    assert part
    data+=part
assert data==expected
print('done',flush=True)
assert sys.stdin.buffer.readline(16)==b'stop\\n'
s.close()
'''
    try:
        server.bind(('127.0.0.1', 0))
        server.listen(1)
        server.settimeout(5)
        endpoint = ('127.0.0.1', server.getsockname()[1])
        capture.start()
        diagnostic['label'] = 'observer-child-start'
        child = subprocess.Popen([sys.executable, '-I', '-c', child_code, str(endpoint[1])],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        connection, _ = server.accept()
        connection.settimeout(5)
        assert child.stdout is not None and child.stdin is not None
        assert select.select([child.stdout], [], [], 5)[0] and child.stdout.readline(16) == b'ready\n', 'observer child readiness'
        capture.sockets(child.pid)
        diagnostic['label'] = 'observer-request'
        child.stdin.write(b'go\n')
        child.stdin.flush()
        data = b''
        while len(data) < len(request):
            part = connection.recv(len(request) - len(data))
            assert part, 'observer request incomplete'
            data += part
        assert data == request, 'observer request mismatch'
        connection.sendall(response)
        assert select.select([child.stdout], [], [], 5)[0] and child.stdout.readline(16) == b'done\n', 'observer response mismatch'
        capture.sockets(child.pid)
        assert child.poll() is None, 'observer child ended early'
    finally:
        try:
            capture.stop()
        except Exception:
            diagnostic['label'] = 'capture-cleanup'
            raise
        finally:
            try:
                if child is not None:
                    if child.poll() is None:
                        assert child.stdin is not None
                        try:
                            child.stdin.write(b'stop\n')
                            child.stdin.flush()
                            child.stdin.close()
                            child.wait(timeout=5)
                        finally:
                            if child.poll() is None:
                                child.kill()
                                child.wait(timeout=5)
                    assert child.returncode == 0, 'observer child exit'
                    if child.stdout is not None:
                        child.stdout.close()
            except Exception:
                diagnostic['label'] = 'observer-child-cleanup'
                raise
            finally:
                if connection is not None:
                    connection.close()
                server.close()
    assert child is not None
    capture.evidence(child.pid, endpoint=endpoint)
    diagnostic['label'] = 'passed'


def prove_packet_path(bridge: Bridge, scratch: Path, address: str, credential: str, diagnostic: dict, identity: object) -> dict:
    """Observe the real native peer request; old filch data is corroboration only."""
    capture = PacketCapture(scratch, diagnostic)
    try:
        capture.start()
        # Preserve PR1459's useful closed log diagnostic. Missing log evidence
        # alone cannot establish or refute the packet/sockets proof; a
        # contradictory observed region still vetoes success below.
        try:
            bridge.call('derp-proof')
        except RuntimeError:
            pass
        assert bridge.process.poll() is None, 'native process unavailable'
        capture.sockets(bridge.process.pid)
        diagnostic['label'] = 'request-failed'
        assert bridge.call('request', address=address, method='GET', path=ROUTE, credential=credential) == {'status': 200, 'body': EXPECTED}, 'proof real-daemon response'
        capture.sockets(bridge.process.pid)
        assert bridge.process.poll() is None, 'native process ended during proof'
    finally:
        try:
            capture.stop()
        except Exception:
            diagnostic['label'] = 'capture-cleanup'
            raise
    capture.evidence(bridge.process.pid)
    region = require_relay_identity(identity, diagnostic)
    diagnostic['label'] = 'identity-log-conflict'
    log = bridge.derp_diagnostics
    assert log.get('label') != 'region-unexpected' and not set(log.get('matched_regions', [])) - {region}, 'contradictory runtime/log identity'
    diagnostic['label'] = 'passed'
    return dict(region=region, **safe_packet_diagnostics(diagnostic))


def safe_derp_diagnostics(value: object) -> dict:
    """Closed labels and bounded integers only, including untrusted pipe input."""
    value = value if isinstance(value, dict) else {}
    label = value.get('label')
    regions = value.get('matched_regions')
    regions = regions if isinstance(regions, list) else []
    return dict(label=label if isinstance(label, str) and label in DERP_PROOF_LABELS else 'unknown',
                **{key: min(value[key], 16_000_000) if type(value.get(key)) is int and value[key] >= 0 else 0
                   for key in ('file_count', 'total_bytes', 'decoded_entry_count',
                               'derp_connected_matches', 'udp_disable_matches')},
                matched_regions=sorted({v for v in regions if type(v) is int and 0 <= v <= 10_000_000})[:8])


def derp_proof(state: Path) -> dict:
    """Read private filch evidence; publish diagnostics even when proof fails."""
    diagnostic: dict = dict(label='unknown', file_count=0, total_bytes=0, decoded_entry_count=0,
                            derp_connected_matches=0, matched_regions=[], udp_disable_matches=0)
    regions: set[int] = set()
    try:
        paths = sorted(state.rglob('tailscaled.log*.txt'))
        diagnostic['file_count'] = len(paths)
        if not paths:
            diagnostic['label'] = 'no-log-files'
        assert paths, 'missing native logs'
        for path in paths:
            size = path.stat().st_size
            diagnostic['total_bytes'] += size
            if size > 8_000_000:
                diagnostic['label'] = 'log-budget'
            assert size <= 8_000_000, 'native log budget'
            for entry in path.read_text(errors='replace').splitlines():
                try:
                    value = json.loads(entry)
                except ValueError:
                    continue
                if not isinstance(value, dict) or not isinstance(value.get('text'), str):
                    continue
                text = value['text']
                diagnostic['decoded_entry_count'] += 1
                matches = re.findall(r'magicsock: derp-([0-9]+) connected;', text)
                diagnostic['derp_connected_matches'] += len(matches)
                regions.update(int(region) for region in matches)
                diagnostic['matched_regions'] = sorted(regions)
                diagnostic['udp_disable_matches'] += len(re.findall(r'disabled udp[46]? per TS_DEBUG_ALWAYS_USE_DERP', text))
        disabled = diagnostic['udp_disable_matches'] > 0
        if not diagnostic['decoded_entry_count']:
            diagnostic['label'] = 'no-decodable-entries'
        elif regions - {999}:
            diagnostic['label'] = 'region-unexpected'
        elif not regions:
            diagnostic['label'] = 'region-missing'
        elif not disabled:
            diagnostic['label'] = 'udp-disable-missing'
        assert regions == {999}, 'missing/wrong selected DERP region'
        assert disabled, 'missing client UDP-disable evidence'
        diagnostic['label'] = 'passed'
        return dict(selected_regions=sorted(regions), udp_disabled=disabled,
                    source='unchanged tsnet backend filch logs; strict projection')
    finally:
        print(json.dumps(dict(derp_diagnostics=safe_derp_diagnostics(diagnostic))), flush=True)


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
                result = derp_proof(state)
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
        self.derp_diagnostics: dict | None = None
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
            if action == 'derp-proof' and self.derp_diagnostics is None:
                self.derp_diagnostics = safe_derp_diagnostics({})
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
            if 'derp_diagnostics' in result:
                assert action == 'derp-proof' and set(result) == {'derp_diagnostics'}
                self.derp_diagnostics = safe_derp_diagnostics(result['derp_diagnostics'])
                continue
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

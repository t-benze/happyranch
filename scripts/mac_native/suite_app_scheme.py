"""MAC-1: build the product executable once, qualify its real app-scheme path.

Manual reviewed-head venue only. Never submits Jenkins or modifies its job.
Every page receipt is emitted immediately and fsynced when native receives it.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import tempfile
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

from scripts.mac_native.suite_supervisor import ROOT, _build_environment, _command
from scripts.mac_native.toolchain import curl_attempt, parse_pins, verify_checksum

LATENCY_MS = 500  # Half of the 1s send interval: batching one interval cannot pass.
ABORT_MS = 500
EVENT_CAP = 256 * 1024
UPLOAD_DIGEST = hashlib.sha256(b's' * (10 * 1024 * 1024)).hexdigest()


@contextmanager
def synthetic_daemon() -> Iterator[tuple[int, list[dict[str, Any]]]]:
    """Private loopback fixture; no product daemon, real token or live state."""
    observations: list[dict[str, Any]] = []
    stop = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, format: str, *args: Any) -> None:
            pass  # Never echo Authorization or request bytes.

        def handle_request(self) -> None:
            if self.headers.get('Authorization') != 'Bearer synthetic-mac1':
                self.answer({'error': 'unauthorized'}, status=401)
                return
            if self.path.endswith('/stream') or self.path.endswith('/abort'):
                mode = self.path.rsplit('/', 1)[1]
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Connection', 'close')
                self.end_headers()
                try:
                    for index in range(5 if mode == 'stream' else 10):
                        if stop.is_set():
                            break
                        sent = int(time.time() * 1000)
                        frame = {'index': index, 'sentWallMs': sent}
                        self.wfile.write(('data: ' + json.dumps(frame) + '\n\n').encode())
                        self.wfile.flush()
                        observations.append({'event': 'send', 'mode': mode, **frame})
                        if stop.wait(1):
                            break
                except (BrokenPipeError, ConnectionResetError):
                    observations.append({'event': 'upstream_closed', 'mode': mode, 'wallMs': time.time() * 1000})
                self.close_connection = True
                return
            if self.path == '/api/v1/health':
                self.answer({'status': 'ok', 'api_contract': {'current': 1, 'min_client': 1}, 'update_busy': {'busy': False}})
            elif self.path == '/api/v1/orgs':
                self.answer({'orgs': [], 'broken': []})
            elif self.path.endswith('/echo') or self.path.endswith('/upload'):
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 <= length <= 12 * 1024 * 1024:
                    self.answer({'error': 'size'}, status=413)
                    return
                body = self.rfile.read(length)
                if self.path.endswith('/upload'):
                    # Independent file digest oracle; multipart's final CRLF and
                    # boundary are framing, not part of the uploaded file.
                    payload = body.split(b'\r\n\r\n', 1)[-1].rsplit(b'\r\n--', 1)[0]
                    digest = hashlib.sha256(payload).hexdigest()
                    self.answer({'fileBytes': len(payload), 'sha256': digest, 'intact': digest == UPLOAD_DIGEST})
                else:
                    self.answer({'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest(),
                                 'intact': body == b'{"synthetic":"mac1"}'})
            elif self.path.endswith('/download'):
                self.answer(b'mac1 download\n', mime='application/octet-stream')
            else:
                self.answer({})

        def answer(self, value: Any, status: int = 200, mime: str = 'application/json') -> None:
            body = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        do_GET = handle_request
        do_POST = handle_request
        do_PUT = handle_request

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], observations
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    if path.stat().st_size > EVENT_CAP:
        raise RuntimeError('qualification event file exceeded 256KiB')
    # A killed final write is partial evidence, never a fabricated valid event.
    data = path.read_bytes()
    lines = data.splitlines(keepends=True)
    return [json.loads(line) for line in lines if line.endswith(b'\n')]


def _error_context(raw: bytes) -> dict[str, Any]:
    """Keep compiler errors outside head/tail, with two surrounding lines.

    Bound the serialized excerpt, including JSON escaping; report omitted text
    explicitly rather than mistaking SwiftPM's final fatalError for the cause.
    """
    lines = raw.decode(errors='replace').splitlines()
    matches = [index for index, line in enumerate(lines) if ': error:' in line or line.startswith('error:')]
    selected = sorted({near for index in matches for near in range(max(0, index - 2), min(len(lines), index + 3))})
    text = '\n'.join(f'{index + 1}: {lines[index]}' for index in selected)
    # Reserve space for the fixed metadata around the JSON string.
    budget = 65536 - 256
    truncated = len(json.dumps(text).encode()) > budget
    if truncated:
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if len(json.dumps(text[:middle]).encode()) <= budget:
                low = middle
            else:
                high = middle - 1
        text = text[:low]
    return {'text': text, 'matching_lines': len(matches), 'truncated': truncated}


def assess(events: list[dict[str, Any]], sends: list[dict[str, Any]], exit_code: int | None,
           download: bytes | None) -> dict[str, Any]:
    """Only measured per-event receipts can qualify streaming; EOF is insufficient."""
    rows = [row for row in events if row.get('event') == 'receipt']
    measurements = []
    valid = True
    stream_sends = [row for row in sends if row.get('event') == 'send' and row.get('mode') == 'stream']
    intervals = [b['sentWallMs'] - a['sentWallMs'] for a, b in zip(stream_sends, stream_sends[1:])]
    valid &= [row.get('index') for row in stream_sends] == list(range(5)) and all(900 <= gap <= 2000 for gap in intervals)
    for mode, expected in (('stream', 5), ('abort', 2)):
        group = [row for row in rows if row.get('mode') == mode]
        sent = [row for row in sends if row.get('event') == 'send' and row.get('mode') == mode]
        if [row.get('index') for row in group] != list(range(expected)):
            valid = False
        for row in group:
            matches = [send for send in sent if send['index'] == row.get('index') and send['sentWallMs'] == row.get('sentWallMs')]
            latency = row['receiptWallMs'] - row['sentWallMs']
            measurements.append({'mode': mode, 'index': row['index'], 'latencyMs': latency,
                                 'sentWallMs': row['sentWallMs'], 'receiptWallMs': row['receiptWallMs']})
            valid &= len(matches) == 1 and 0 <= latency < LATENCY_MS
    aborts = [row for row in events if row.get('event') == 'abort']
    stops = [row for row in events if row.get('event') == 'native_stop']
    delay = None
    post_stop_writes = None
    stream_owners = [row['requestID'] for row in events if row.get('event') == 'native_stream_open']
    if len(aborts) == 1 and len(stream_owners) == 2 and stream_owners[0] != stream_owners[1]:
        after = [row for row in stops if row['nativeWallMs'] >= aborts[0]['pageWallMs'] and row.get('requestID') == stream_owners[1]]
        if len(after) == 1:
            stop = after[0]
            delay = stop['nativeWallMs'] - aborts[0]['pageWallMs']
            post_stop_writes = sum(row.get('event') == 'native_write' and row.get('requestID') == stop['requestID']
                                   and row['nativeWallMs'] > stop['nativeWallMs'] for row in events)
    abort_ok = delay is not None and 0 <= delay < ABORT_MS and post_stop_writes == 0
    root_ok = any(row.get('event') == 'root' and row.get('path') == '/onboarding' and row.get('root', 0) > 0 for row in events)
    deep_ok = any(row.get('event') == 'deep_reload' and row.get('path') == '/settings'
                  and row.get('origin') == 'happyranch-app://app' and row.get('root', 0) > 0 for row in events)
    json_ok = all(any(row.get('event') == 'json' and row.get('method') == method and row.get('bytes') == 20
                      and row.get('sha256') == hashlib.sha256(b'{"synthetic":"mac1"}').hexdigest()
                      and row.get('intact') is True for row in events) for method in ('POST', 'PUT'))
    upload_ok = any(row.get('event') == 'upload' and row.get('fileBytes') == 10485760
                    and row.get('sha256') == UPLOAD_DIGEST and row.get('intact') is True for row in events)
    complete = exit_code == 0 and any(row.get('event') == 'done' for row in events) and any(row.get('event') == 'download_finish' for row in events)
    return {'status': 'PASS' if complete and valid and abort_ok and root_ok and deep_ok and json_ok and upload_ok
            and download == b'mac1 download\n' else 'FAIL',
            'path': 'WKURLSchemeHandler', 'fallback': 'NOT IMPLEMENTED; buffering requires manager follow-up',
            'threshold_ms': LATENCY_MS, 'threshold_reason': 'Less than half the 1s send interval excludes interval-sized buffering.',
            'measurements': measurements, 'send_intervals_ms': intervals, 'abort_delay_ms': delay, 'abort_pass': abort_ok,
            'post_stop_writes': post_stop_writes, 'stream_owners': stream_owners,
            'root_redirect_pass': root_ok, 'deep_reload_pass': deep_ok, 'json_pass': json_ok,
            'upload_pass': upload_ok, 'download_pass': download == b'mac1 download\n',
            'complete': complete, 'partial': not complete}


def run(report: dict[str, Any]) -> None:
    section: dict[str, Any] = {'commands': [], 'events': [], 'qualification': {'status': 'NOT RUN'}}
    report['app_scheme'] = section
    tmp = Path(os.environ.get('TMPDIR', ''))
    if not tmp.is_absolute() or not tmp.is_dir() or tmp == Path('/tmp') or tmp.is_relative_to('/tmp'):
        raise RuntimeError('requires existing private non-/tmp TMPDIR')
    deadline = time.monotonic() + 1500
    with tempfile.TemporaryDirectory(prefix='app-scheme-', dir=tmp) as folder:
        scratch = Path(folder).resolve()
        env = _build_environment(scratch)

        def command(name: str, argv: list[str], cwd: Path = ROOT, seconds: float = 60) -> bytes:
            row: dict[str, Any] = {'name': name, 'argv': argv, 'exit_code': None}
            section['commands'].append(row)
            code, raw = _command(argv, env, cwd, min(deadline, time.monotonic() + seconds), output_cap=1024 * 1024)
            row.update(exit_code=code, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                       head=raw[:2048].decode(errors='replace'), tail=raw[-2048:].decode(errors='replace'),
                       error_context=_error_context(raw))
            if code:
                raise RuntimeError(name + ' failed')
            return raw

        command('swift_version', ['/usr/bin/xcrun', 'swift', '--version'])
        command('macos', ['/usr/bin/sw_vers'])
        command('architecture', ['/usr/bin/uname', '-m'])
        pins = parse_pins((ROOT / 'scripts/mac_native/toolchain-pins.json').read_text())
        node = pins['node']
        archive = scratch / 'node.tar.gz'
        transfer_end = min(deadline, time.monotonic() + 90)
        attempt = curl_attempt(node['origins'][0], archive, transfer_end - time.monotonic(), transfer_end)
        section['node_transfer'] = attempt
        if attempt['curl_exit_code'] or attempt.get('error'):
            raise RuntimeError('pinned Node transfer failed')
        verify_checksum(archive, node['sha256'], deadline)
        node_root = f"node-v{node['version']}-darwin-arm64"
        with tarfile.open(archive) as source:
            total = 0
            for count, member in enumerate(source, 1):
                total += member.size
                if count > 20000 or total > 256 * 1024 * 1024 or not 0 <= member.size <= 128 * 1024 * 1024 or member.name.split('/')[0] != node_root:
                    raise RuntimeError('Node extraction bounds')
                source.extract(member, scratch, filter='data')
        env['PATH'] = str(scratch / node_root / 'bin') + ':' + env['PATH']
        env['npm_config_cache'] = str(scratch / 'npm-cache')
        # Build the actual product, never the throwaway S0 package. No archive
        # signing/notarization or real daemon/helper process is started.
        swift = ['/usr/bin/xcrun', 'swift', 'test', '--package-path', str(ROOT / 'app/mac'),
                 '--scratch-path', str(scratch / 'build'), '--cache-path', str(scratch / 'swiftpm-cache'),
                 '--config-path', str(scratch / 'swiftpm-config'), '--security-path', str(scratch / 'swiftpm-security'),
                 '-c', 'release', '-j', '2', '-Xswiftc', '-gnone']
        command('product_build_and_parser_tests', swift + ['--filter', 'AppHTTPResponseParserTests|AppSchemeRouteTests|AppSchemeLoadTests'], seconds=600)
        bin_command = list(swift); bin_command[2] = 'build'
        binary = Path(command('bin_path', bin_command + ['--show-bin-path']).decode().strip()) / 'HappyRanchApp'
        if not binary.is_file() or not binary.resolve().is_relative_to(scratch):
            raise RuntimeError('product binary not in scratch')
        assets = scratch / 'source'
        shutil.copytree(ROOT / 'web', assets / 'web', ignore=shutil.ignore_patterns('node_modules', 'dist', '*.tsbuildinfo'))
        shutil.copytree(ROOT / 'protocol', assets / 'protocol')
        command('node_version', ['node', '--version'])
        command('npm_ci', ['npm', 'ci'], assets / 'web', seconds=240)
        command('web_build', ['npm', 'run', 'build'], assets / 'web', seconds=180)
        # Match build-app.sh's release-only source-map removal and inventory.
        dist = assets / 'web/dist'
        for source_map in dist.rglob('*.map'):
            source_map.unlink()
        manifest_path = dist / 'resource-manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['resources'] = [row for row in manifest['resources'] if not row['path'].endswith('.map')]
        manifest_path.write_text(json.dumps(manifest) + '\n')
        events = scratch / 'events.jsonl'
        downloads = scratch / 'downloads'; downloads.mkdir()
        code = None
        with synthetic_daemon() as (port, sends):
            try:
                code, raw = _command([str(binary), '--qualify-app-scheme', str(assets / 'web/dist'),
                    str(assets / 'protocol/remote-client-config-policy.v1.json'), str(port), str(events), str(downloads)],
                    env, scratch, min(deadline, time.monotonic() + 40), output_cap=65536)
                section['native_exit'] = code
                section['native_output'] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                                            'head': raw[:1024].decode(errors='replace'), 'tail': raw[-1024:].decode(errors='replace')}
            finally:
                # Even timeout/failure keeps per-event receipts, never just an
                # end-of-stream array. probe.py serializes this partial report.
                section['events'] = read_events(events)
                section['sends'] = list(sends)
                downloaded = downloads / 'mac1.txt'
                data = downloaded.read_bytes() if downloaded.is_file() and downloaded.stat().st_size <= 1024 else None
                section['qualification'] = assess(section['events'], section['sends'], code, data)
        if section['qualification']['status'] != 'PASS':
            raise RuntimeError('app-scheme qualification failed; retain partial event evidence')

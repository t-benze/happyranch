"""MAC-1: build the product executable once, qualify its real app-scheme path.

Manual reviewed-head venue only. Never submits Jenkins or modifies its job.
Every page receipt is emitted immediately and fsynced when native receives it.
"""
from __future__ import annotations

import hashlib
import base64
import json
import os
import stat
import struct
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

from scripts.mac_native.app_scheme_diagnostics import archive_stream, sample_stalled_processes, write_diagnostics
from scripts.mac_native.app_scheme_test_evidence import TEST_OUTPUT_CAP, archive_events, assess_native, source_inventory, terminal_results
from scripts.mac_native.suite_supervisor import CommandTimeout, ROOT, _build_environment, _command, _prepare_keychain
from scripts.mac_native.toolchain import curl_attempt, parse_pins, verify_checksum

LATENCY_MS = 500  # Half of the 1s send interval: batching one interval cannot pass.
ABORT_MS = 500
EVENT_CAP = 256 * 1024
PNG_CAP = 2 * 1024 * 1024
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


def qualify_steps(argv: list[str], env: dict[str, str], scratch: Path, deadline: float,
                  section: dict[str, Any], downloads: Path) -> int:
    """Each step owns a fresh native process/MainActor/WebKit content process.

    A stream watchdog or dead page channel cannot suppress abort/download.
    This is the existing product binary/fixture venue, with separate JSONL files.
    """
    codes = []
    section['native_processes'] = []
    for step in ('stream', 'abort', 'download'):
        events = scratch / f'{step}-events.jsonl'
        command = [*argv, str(events), str(downloads), step]
        error_class = None
        try:
            code, raw = _command(command, env, scratch, min(deadline, time.monotonic() + 40), output_cap=65536)
        except (TimeoutError, RuntimeError, subprocess.TimeoutExpired) as error:
            # _command reaps its process group before raising. Preserve the
            # failure and continue with a separately launched product process.
            code, raw = 124 if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)) else 1, b''
            error_class = type(error).__name__
        codes.append(code)
        section['native_processes'].append({'step': step, 'argv': command, 'exit_code': code,
            'command_error': error_class, 'bytes': len(raw),
            'sha256': hashlib.sha256(raw).hexdigest(), 'head': raw[:1024].decode(errors='replace'),
            'tail': raw[-1024:].decode(errors='replace')})
        try:
            section['events'].extend({**row, 'processStep': step} for row in read_events(events))
        except (ValueError, RuntimeError):
            section['events'].append({'event': 'process_evidence_failure', 'step': step})
            codes[-1] = 1
        section['events'].append({'event': 'process_exit', 'step': step, 'exit_code': code})
    return 0 if all(code == 0 for code in codes) else 1


def qualify_cold_client(argv: list[str], env: dict[str, str], scratch: Path, deadline: float,
                        section: dict[str, Any], downloads: Path) -> None:
    """Require the product's cold ContentView/AppDelegate composition receipt."""
    events = scratch / 'cold-client.jsonl'
    command = [*argv, str(events), str(downloads), 'cold-client']
    result: dict[str, Any] = {'status': 'FAIL', 'argv': command, 'exit_code': None}
    section['cold_client'] = result
    try:
        code, raw = _command(command, env, scratch, min(deadline, time.monotonic() + 40), output_cap=65536)
        result.update(exit_code=code, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                      head=raw[:1024].decode(errors='replace'), tail=raw[-1024:].decode(errors='replace'))
        receipts = [row for row in read_events(events) if row.get('event') == 'cold_client']
        result['events'] = receipts
        if (code == 0 and len(receipts) == 1 and receipts[0].get('status') == 'PASS'
                and receipts[0].get('entries') == 3 and receipts[0].get('dialogs') is True
                and receipts[0].get('no_target') is True
                and receipts[0].get('unchanged_target') is True):
            result['status'] = 'PASS'
    except (OSError, ValueError, TimeoutError, RuntimeError, subprocess.TimeoutExpired) as error:
        result['command_error'] = type(error).__name__


def screenshot_archive(folder: Path, locale: str,
                       scroll_counts: dict[tuple[str, str], int] | None = None) -> list[dict[str, Any]]:
    """Closed names, bounded top/scroll PNGs in the existing probe JSON."""
    if locale not in ('en', 'zh'):
        raise ValueError('unsupported capture locale')
    rows = []
    scroll_counts = scroll_counts or {}
    captures = []
    for theme in ('light', 'dark'):
        for scale in (100, 200):
            for dialog in ('settings', 'connection', 'main'):
                count = scroll_counts.get((theme, dialog), 0) if scale == 200 else 0
                if type(count) is not int or not 0 <= count <= 16:
                    raise ValueError('screenshot scroll count')
                for number in range(count + 1):
                    suffix = f'-scroll-{number}' if number else ''
                    name = f'{locale}-{theme}-{scale}-{dialog}{suffix}.png'
                    captures.append((theme, scale, dialog, number, name))
    for scale, suffix in ((100, '-100'), (200, '')):
        captures.append(('dark', scale, 'main', 0, f'mac-recovery-no-relaunch-{locale}{suffix}.png'))
    recovery_count = scroll_counts.get(('dark', 'recovery'), 0)
    if type(recovery_count) is not int or not 0 <= recovery_count <= 16:
        raise ValueError('recovery screenshot scroll count')
    for number in range(1, recovery_count + 1):
        captures.append(('dark', 200, 'main', number, f'mac-recovery-no-relaunch-{locale}-scroll-{number}.png'))
    for theme, scale, dialog, number, name in captures:
        descriptor = os.open(folder / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or not 33 <= info.st_size <= PNG_CAP:
                raise ValueError('screenshot size/type')
            raw = source.read(PNG_CAP + 1)
        if (len(raw) != info.st_size or len(raw) > PNG_CAP
                or raw[:16] != b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
                or struct.unpack('>II', raw[16:24]) != (760, 560)):
            raise ValueError('screenshot PNG dimensions/header')
        rows.append(dict(name=name, locale=locale, theme=theme, text_percent=scale, dialog=dialog,
            scroll_capture=number, width=760, height=560, encoding='base64', bytes=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(), data=base64.b64encode(raw).decode('ascii')))
    if sum(row['bytes'] for row in rows) > 8 * 1024 * 1024:
        raise ValueError('screenshot archive size')
    return rows


def qualify_dialogs(argv: list[str], env: dict[str, str], scratch: Path, deadline: float,
                    section: dict[str, Any], downloads: Path) -> None:
    capture: dict[str, Any] = {'status': 'FAIL', 'processes': [], 'screenshots': []}
    section['dialogs'] = capture
    for locale in ('en', 'zh'):
        events = scratch / f'dialogs-{locale}.jsonl'
        command = [*argv, str(events), str(downloads), f'dialogs-{locale}']
        row: dict[str, Any] = {'locale': locale, 'argv': command, 'exit_code': None, 'status': 'FAIL'}
        capture['processes'].append(row)
        try:
            code, raw = _command(command, env, scratch, min(deadline, time.monotonic() + 40), output_cap=65536)
            row.update(exit_code=code, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                       head=raw[:1024].decode(errors='replace'), tail=raw[-1024:].decode(errors='replace'))
        except (TimeoutError, RuntimeError, subprocess.TimeoutExpired) as error:
            row['command_error'] = type(error).__name__
        # Keep completed files even if a later capture/process fails. A partial
        # locale is never accepted as complete or silently truncated.
        try:
            evidence = read_events(events)
            row['diagnostic_events'] = [event for event in evidence if event.get('event') in (
                'startup', 'capture_progress', 'capture_step', 'capture_navigation', 'admission_failure', 'failure',
                'watchdog', 'navigation_failure', 'content_process_terminated')]
            progress = [event for event in evidence if event.get('event') == 'capture_progress']
            row['last_step'] = progress[-1] if progress else None
            receipts = [event for event in evidence if event.get('event') == 'screenshot']
            row['events'] = receipts
            completions = [event for event in evidence if event.get('event') == 'capture_complete']
            row['completion_events'] = completions
            counts = {}
            for event in completions:
                key = (event.get('theme'), event.get('dialog'))
                count = event.get('scrollCaptures')
                panes = event.get('scrollers')
                if (key in counts or key[0] not in ('light', 'dark') or key[1] not in ('settings', 'connection', 'main', 'recovery')
                        or (key[1] == 'recovery' and key[0] != 'dark')
                        or event.get('locale') != locale or event.get('textPercent') != 200
                        or type(count) is not int or not 0 <= count <= 16 or not isinstance(panes, list)
                        or len(panes) > 8 or any(not isinstance(pane, dict) or pane.get('index') != index
                            or type(pane.get('pages')) is not int or pane['pages'] <= 0
                            for index, pane in enumerate(panes))
                        or sum(pane['pages'] for pane in panes) != count):
                    raise ValueError('incomplete scroll capture receipt')
                counts[key] = count
            if len(counts) != 7:
                raise ValueError('missing scroll capture completion')
            images = screenshot_archive(downloads, locale, counts)
            capture['screenshots'].extend(images)
            expected = {image['name']: image for image in images}
            recovery = [event for event in receipts if str(event.get('name', '')).startswith('mac-recovery-no-relaunch-')]
            visible_keys = {'guidance', 'reopen', 'recheck'}
            if (any(not isinstance(event.get('recoveryVisible'), dict)
                    or set(event['recoveryVisible']) != visible_keys
                    or any(type(value) is not bool for value in event['recoveryVisible'].values()) for event in recovery)
                    or any(not all(any(event['recoveryVisible'][key] for event in recovery
                            if event.get('textPercent') == scale) for key in visible_keys) for scale in (100, 200))):
                raise ValueError('recovery content not visibly captured at both scales')
            if (row['exit_code'] == 0 and len(receipts) == len(expected)
                    and {event.get('name') for event in receipts} == set(expected)
                    and all(event.get('webTheme') == event.get('theme') == expected[event['name']]['theme']
                        for event in receipts)
                    and all(event.get('locale') == locale
                        and type(event.get('textPercent')) is int
                        and event['textPercent'] == expected[event['name']]['text_percent']
                        and event.get('dialog') == 'main'
                        and event.get('width') == 760 and event.get('height') == 560
                        for event in receipts if event['name'].startswith('mac-recovery-no-relaunch-'))):
                row['status'] = 'PASS'
        except (OSError, ValueError, RuntimeError) as error:
            row['capture_error'] = type(error).__name__
            if isinstance(error, ValueError):
                row['capture_error_reason'] = str(error)
    if all(row['status'] == 'PASS' for row in capture['processes']):
        capture['status'] = 'PASS'


def live_switch_archive(folder: Path) -> list[dict[str, Any]]:
    """Separate closed six-image set; never widen the original dialog archive."""
    rows = []
    for theme, scale in (('light', 100), ('dark', 200)):
        for dialog in ('main', 'settings', 'connection'):
            name = f'live-switch-zh-{theme}-{scale}-{dialog}.png'
            descriptor = os.open(folder / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, 'rb') as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or not 33 <= info.st_size <= PNG_CAP:
                    raise ValueError('live-switch screenshot size/type')
                raw = source.read(PNG_CAP + 1)
            if (len(raw) != info.st_size or len(raw) > PNG_CAP
                    or raw[:16] != b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
                    or struct.unpack('>II', raw[16:24]) != (760, 560)):
                raise ValueError('live-switch PNG dimensions/header')
            rows.append(dict(name=name, locale='zh', theme=theme, text_percent=scale, dialog=dialog,
                width=760, height=560, encoding='base64', bytes=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(), data=base64.b64encode(raw).decode('ascii')))
    if sum(row['bytes'] for row in rows) > 8 * 1024 * 1024:
        raise ValueError('live-switch screenshot archive size')
    return rows


def qualify_live_switch(argv: list[str], env: dict[str, str], scratch: Path, deadline: float,
                        section: dict[str, Any], downloads: Path) -> None:
    """A missing live choice, durability/chrome step or capture fails separately."""
    events = scratch / 'live-switch.jsonl'
    command = [*argv, str(events), str(downloads), 'live-switch']
    capture: dict[str, Any] = {'status': 'FAIL', 'argv': command, 'exit_code': None, 'screenshots': []}
    section['live_switch'] = capture
    try:
        code, raw = _command(command, env, scratch, min(deadline, time.monotonic() + 40), output_cap=65536)
        capture.update(exit_code=code, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                       head=raw[:1024].decode(errors='replace'), tail=raw[-1024:].decode(errors='replace'))
    except (TimeoutError, RuntimeError, subprocess.TimeoutExpired) as error:
        capture['command_error'] = type(error).__name__
    # Preserve available semantic receipts even if the image archive is incomplete.
    try:
        evidence = read_events(events)
        capture['events'] = [row for row in evidence if row.get('event') in ('live-switch-step', 'live-switch-screenshot')]
        capture['diagnostic_events'] = [row for row in evidence if row.get('event') in (
            'startup', 'capture_progress', 'capture_step', 'capture_navigation', 'admission_failure', 'failure',
            'watchdog', 'navigation_failure', 'content_process_terminated', 'live-switch-dom-failure')]
        images = live_switch_archive(downloads)
        capture['screenshots'] = images
        steps = [row for row in evidence if row.get('event') == 'live-switch-step']
        required = [
            dict(step='start-en', locale='en', nativeLaunchLocale='en'),
            dict(step='control-change', locale='zh-CN', changed=True),
            dict(step='native-reply', locale='zh-CN', persisted=True, replyCount=1),
            dict(step='native-saved', locale='zh-CN', persisted=True, webSaved=True),
            dict(step='durable-readback', locale='zh-CN', scratch=True),
            dict(step='native-chrome-unchanged', nativeLaunchLocale='en', unchanged=True),
        ]
        steps_ok = len(steps) == len(required) and all(
            all(type(row.get(key)) is type(value) and row[key] == value for key, value in expected.items())
            for row, expected in zip(steps, required))
        receipts = [row for row in evidence if row.get('event') == 'live-switch-screenshot']
        captures_ok = len(receipts) == len(images) and all(
            row.get('name') == image['name'] and row.get('locale') == 'zh'
            and row.get('theme') == row.get('webTheme') == image['theme']
            and type(row.get('textPercent')) is int and row['textPercent'] == image['text_percent']
            and row.get('dialog') == image['dialog'] and row.get('chineseDOM') is True
            and row.get('width') == 760 and row.get('height') == 560
            for row, image in zip(receipts, images))
        # Require semantic ordering: every capture follows Saved/readback and
        # precedes the final immutable-native-chrome check.
        ordered = capture['events']
        order_ok = ([row.get('step') for row in ordered[:5]] == [row['step'] for row in required[:5]]
                    and all(row.get('event') == 'live-switch-screenshot' for row in ordered[5:-1])
                    and ordered[-1].get('step') == 'native-chrome-unchanged') if ordered else False
        failed = any(row.get('event') in ('failure', 'admission_failure', 'watchdog', 'navigation_failure',
                                         'content_process_terminated') or
                     (row.get('event') == 'capture_step' and row.get('result') != 'complete') for row in evidence)
        if capture['exit_code'] == 0 and steps_ok and captures_ok and order_ok and not failed:
            capture['status'] = 'PASS'
    except (OSError, ValueError, RuntimeError) as error:
        capture['capture_error'] = type(error).__name__
        if isinstance(error, ValueError):
            capture['capture_error_reason'] = str(error)


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


def _loader_diagnostics(raw: bytes) -> dict[str, Any]:
    """Retain only closed loader facts, independently of warning volume/tail."""
    result_types = {'not-attempted', 'in-flight', 'nil', 'null', 'string', 'dictionary', 'number', 'bool', 'other', 'threw'}
    types = {'undefined', 'object', 'function', 'boolean', 'number', 'string', 'symbol', 'bigint', 'threw'}
    allowed = {
        'label': {'missing-contract', 'host-too-old', 'app-too-old', 'busy', 'busy-unknown', 'ready'},
        'checkpoint': {'initial', 'lost-reply', 'busy', 'ready', 'recovered', 'pending-result', 'retired-document', 'closed'},
        'contextRead': {'not-attempted', 'in-flight', 'string-unchecked', 'missing-or-non-string', 'valid', 'threw'},
        'bind': {'not-attempted', 'in-flight', 'true', 'false', 'missing-or-non-boolean', 'threw'},
        'readiness': {'not-attempted', 'in-flight', 'target_starting', 'ready', 'busy', 'incompatible', 'unreachable'},
        **dict.fromkeys(('contextResult', 'bindResult', 'diagnosticsResult'), result_types),
    }
    page_allowed = {
        'bootstrap': {'missing', 'entered', 'top-frame-check', 'refused-top-frame', 'origin-check', 'refused-origin',
                      'native-handler', 'context-random', 'context-define', 'facade-define', 'fetch-install', 'complete', 'threw'},
        'initialOrigin': {'missing', 'not-read', 'app', 'null', 'other', 'threw'},
        'initialTop': {'missing', 'not-read', 'true', 'false', 'threw'},
        'origin': {'app', 'null', 'other', 'threw'}, 'protocol': {'app', 'other', 'threw'},
        'host': {'app', 'other', 'threw'}, 'top': {'true', 'false', 'threw'},
        'secure': {'true', 'false', 'undefined', 'threw'}, 'readyState': {'loading', 'interactive', 'complete', 'threw'},
        **dict.fromkeys(('webkit', 'handlers', 'appTransport', 'postMessage', 'crypto', 'random'), types),
    }
    rows = []
    matching = used = 0
    truncated = False
    for line in raw.decode(errors='replace').splitlines():
        if not line.startswith('MAC1_LOADER '):
            continue
        matching += 1
        try:
            source = json.loads(line.removeprefix('MAC1_LOADER '))
        except (ValueError, RecursionError):
            continue
        if not isinstance(source, dict):
            continue
        row: dict[str, Any] = {key: value if isinstance(value, str) and value in values else 'invalid'
                               for key, values in allowed.items() if (value := source.get(key)) is not None}
        for key in ('healthInvocations', 'loaded', 'failed', 'callbacks'):
            value = source.get(key)
            if type(value) is int and 0 <= value <= 10000:
                row[key] = value
        facts = source.get('pageFacts')
        if isinstance(facts, str) and facts in {'not-attempted', 'in-flight', 'document-changed', 'read-threw'}:
            row['pageFacts'] = facts
        elif isinstance(facts, str):
            page = {}
            for part in facts.split(', '):
                key, _, value = part.partition('=')
                if key in page_allowed:
                    page[key] = value if value in page_allowed[key] else 'invalid'
            row['pageFacts'] = page
        size = len(json.dumps(row).encode()) + 2
        if used + size > 65536 - 256:
            truncated = True
            continue
        rows.append(row)
        used += size
    return {'rows': rows, 'matching_lines': matching, 'truncated': truncated}


def assess(events: list[dict[str, Any]], sends: list[dict[str, Any]], exit_code: int | None,
           download: bytes | None) -> dict[str, Any]:
    """Only measured per-event receipts can qualify streaming; EOF is insufficient."""
    rows = [row for row in events if row.get('event') == 'receipt']
    measurements = []
    valid = True
    stream_sends = [row for row in sends if row.get('event') == 'send' and row.get('mode') == 'stream']
    intervals = [b['sentWallMs'] - a['sentWallMs'] for a, b in zip(stream_sends, stream_sends[1:])]
    valid &= [row.get('index') for row in stream_sends] == list(range(5)) and all(900 <= gap <= 2000 for gap in intervals)
    mode_valid = {'stream': bool(valid), 'abort': True}
    for mode, expected in (('stream', 5), ('abort', 2)):
        group = [row for row in rows if row.get('mode') == mode]
        sent = [row for row in sends if row.get('event') == 'send' and row.get('mode') == mode]
        if [row.get('index') for row in group] != list(range(expected)):
            valid = False
            mode_valid[mode] = False
        for row in group:
            matches = [send for send in sent if send['index'] == row.get('index') and send['sentWallMs'] == row.get('sentWallMs')]
            latency = row['receiptWallMs'] - row['sentWallMs']
            measurements.append({'mode': mode, 'index': row['index'], 'latencyMs': latency,
                                 'sentWallMs': row['sentWallMs'], 'receiptWallMs': row['receiptWallMs']})
            valid &= len(matches) == 1 and 0 <= latency < LATENCY_MS
            mode_valid[mode] &= len(matches) == 1 and 0 <= latency < LATENCY_MS
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
    deep_ok = any(row.get('event') == 'deep_reload' and row.get('path') == '/onboarding'
                  and row.get('origin') == 'happyranch-app://app' and row.get('root', 0) > 0 for row in events)
    json_ok = all(any(row.get('event') == 'json' and row.get('method') == method and row.get('bytes') == 20
                      and row.get('sha256') == hashlib.sha256(b'{"synthetic":"mac1"}').hexdigest()
                      and row.get('intact') is True for row in events) for method in ('POST', 'PUT'))
    upload_ok = any(row.get('event') == 'upload' and row.get('fileBytes') == 10485760
                    and row.get('sha256') == UPLOAD_DIGEST and row.get('intact') is True for row in events)
    ends = [row for row in events if row.get('event') == 'stream_end' and row.get('mode') == 'stream']
    eof_ok = len(ends) == 1 and ends[0].get('count') == 5
    observations = {'stream': mode_valid['stream'] and eof_ok, 'abort': mode_valid['abort'] and abort_ok,
                    'download': download == b'mac1 download\n' and any(row.get('event') == 'download_finish' for row in events)}
    step_results = {}
    for step in ('stream', 'abort', 'download'):
        results = [row for row in events if row.get('event') == 'step' and row.get('step') == step]
        step_results[step] = 'PASS' if len(results) == 1 and results[0].get('status') == 'PASS' and observations[step] else 'FAIL'
    steps_ok = all(value == 'PASS' for value in step_results.values())
    process_results = {}
    for step in ('stream', 'abort', 'download'):
        results = [row for row in events if row.get('event') == 'process_exit' and row.get('step') == step]
        process_results[step] = len(results) == 1 and results[0].get('exit_code') == 0 and any(
            row.get('event') == 'done' and row.get('processStep') == step for row in events)
    failed = any(row.get('event') in ('failure', 'step_diagnostic', 'download_failure', 'watchdog',
                                    'content_process_terminated', 'navigation_failure', 'process_evidence_failure') for row in events)
    complete = exit_code == 0 and all(process_results.values()) and any(row.get('event') == 'download_finish' for row in events)
    return {'status': 'PASS' if complete and steps_ok and not failed and valid and abort_ok and root_ok and deep_ok and json_ok and upload_ok
            and download == b'mac1 download\n' else 'FAIL',
            'path': 'WKURLSchemeHandler', 'fallback': 'NOT IMPLEMENTED; buffering requires manager follow-up',
            'threshold_ms': LATENCY_MS, 'threshold_reason': 'Less than half the 1s send interval excludes interval-sized buffering.',
            'measurements': measurements, 'send_intervals_ms': intervals, 'abort_delay_ms': delay, 'abort_pass': abort_ok,
            'post_stop_writes': post_stop_writes, 'stream_owners': stream_owners,
            'root_redirect_pass': root_ok, 'deep_reload_pass': deep_ok, 'json_pass': json_ok,
            'stream_eof_pass': eof_ok, 'step_results': step_results, 'process_results': process_results,
            'upload_pass': upload_ok, 'download_pass': download == b'mac1 download\n',
            'complete': complete, 'partial': not complete}


def run(report: dict[str, Any], artifact_dir: Path) -> None:
    section: dict[str, Any] = {'commands': [], 'events': [], 'qualification': {'status': 'NOT RUN'}}
    report['app_scheme'] = section
    tmp = Path(os.environ.get('TMPDIR', ''))
    if not tmp.is_absolute() or not tmp.is_dir() or tmp == Path('/tmp') or tmp.is_relative_to('/tmp'):
        raise RuntimeError('requires existing private non-/tmp TMPDIR')
    deadline = time.monotonic() + 2100
    with tempfile.TemporaryDirectory(prefix='app-scheme-', dir=tmp) as folder:
        scratch = Path(folder).resolve()
        env = _build_environment(scratch)

        def command(name: str, argv: list[str], cwd: Path = ROOT, seconds: float = 60,
                    allow_failure: bool = False, capture: dict[str, Any] | None = None) -> bytes:
            row: dict[str, Any] = {'name': name, 'argv': argv, 'exit_code': None, 'timeout_seconds': seconds}
            section['commands'].append(row)
            started = time.monotonic()
            code, raw, stderr = None, b'', bytearray()
            try:
                options = {}
                if capture is not None:
                    row['diagnostic_timeout_seconds'] = 30
                    capture['timeout_diagnostics'] = {}
                    options['timeout_observer'] = lambda pid: sample_stalled_processes(
                        pid, env, scratch, min(deadline, started + seconds + 30), capture['timeout_diagnostics'])
                code, raw = _command(argv, env, cwd, min(deadline, started + seconds),
                                     output_cap=TEST_OUTPUT_CAP if capture is not None else 1024 * 1024,
                                     stderr_data=stderr, **options)
            except (TimeoutError, RuntimeError, subprocess.TimeoutExpired) as error:
                if isinstance(error, CommandTimeout):
                    raw = error.stdout
                    stderr[:] = error.stderr
                row['command_error'] = type(error).__name__
                raise
            finally:
                if capture is not None:
                    capture.update(stdout=archive_stream(raw), stderr=archive_stream(bytes(stderr)),
                                   exit_code=code, command_error=row.get('command_error'))
                for stream, data in (('stdout', raw), ('stderr', bytes(stderr))):
                    row[stream] = dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                        head=data[:2048].decode(errors='replace'), tail=data[-2048:].decode(errors='replace'),
                        excerpts_truncated=len(data) > 4096)
                raw += bytes(stderr)
                row.update(exit_code=code, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                           elapsed_seconds=time.monotonic() - started,
                           head=raw[:2048].decode(errors='replace'), tail=raw[-2048:].decode(errors='replace'),
                           error_context=_error_context(raw), test_results=terminal_results(raw.decode(errors='replace')),
                           loader_diagnostics=_loader_diagnostics(raw))
            if code and not allow_failure:
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
        inventory = source_inventory(ROOT / 'app/mac/Tests')
        section['native_tests'] = {'status': 'FAIL', 'capture': 'incomplete', 'inventory': inventory}
        swift_events = scratch / 'swift-testing.jsonl'
        section['keychain_fixture'] = []
        def keychain_row(name: str, status: str, expected: Any, actual: Any) -> None:
            section['keychain_fixture'].append(dict(name=name, status=status, expected=expected, actual=actual))
        with ExitStack() as cleanup:
            _prepare_keychain(cleanup, env, scratch, deadline, keychain_row, section['keychain_fixture'])
            captures = {}
            native = section['native_tests']
            native['diagnostic_artifacts'] = {}
            for library in ('xctest', 'swift_testing'):
                captures[library] = dict(library=library, source_sha=os.environ.get('SOURCE_SHA'),
                                         build_number=os.environ.get('BUILD_NUMBER'), status='NOT RUN')
                native['diagnostic_artifacts'][library] = write_diagnostics(artifact_dir, library, captures[library])
            outputs, codes = [], []
            events = b''
            archive_errors = []
            for library, options in (
                ('xctest', ['--enable-xctest', '--disable-swift-testing']),
                ('swift_testing', ['--disable-xctest', '--enable-swift-testing',
                                   '--event-stream-output-path', str(swift_events), '--event-stream-version', '0']),
            ):
                capture = captures[library]
                capture['status'] = 'running'
                raw = b''
                try:
                    # Each library gets 570s plus at most 30s of diagnostics.
                    # Failure/timeout in XCTest cannot suppress Swift Testing.
                    raw = command('product_' + library, swift + [
                        '--enable-testable-imports', '--no-parallel', *options],
                        seconds=570, allow_failure=True, capture=capture)
                except CommandTimeout as error:
                    raw = error.output
                except (TimeoutError, RuntimeError, subprocess.TimeoutExpired):
                    pass  # Command metadata retains the failure, never PASS.
                finally:
                    row = section['commands'][-1]
                    codes.append(row['exit_code'])
                    outputs.append(raw)
                    capture.update(status='captured', command=row)
                    if library == 'swift_testing':
                        if swift_events.is_file() and swift_events.stat().st_size <= TEST_OUTPUT_CAP:
                            events = swift_events.read_bytes()
                        capture['events'] = archive_stream(events)
                    try:
                        native['diagnostic_artifacts'][library] = write_diagnostics(artifact_dir, library, capture)
                    except (OSError, ValueError) as error:
                        archive_errors.append(library + ' archive: ' + type(error).__name__)
            code = None if None in codes else next((code for code in codes if code), 0)
            native.update(assess_native(b'\n'.join(outputs), events, inventory, code), capture='assessed',
                event_bytes=len(events), event_sha256=hashlib.sha256(events).hexdigest(), library_exits=codes)
            native['errors'].extend(archive_errors)
            if any(row.get('command_error') for row in section['commands'] if row['name'] in ('product_xctest', 'product_swift_testing')):
                native['errors'].append('native library command failed or timed out')
            native['unfinished_cases'] = [row for row in native['cases']
                if row['status'] == 'INCOMPLETE' or (row.get('starts') and not row.get('ends') and not row.get('outcomes'))]
            try:
                native['event_archive'] = archive_events(events)
            except ValueError as error:
                native['errors'].append(str(error))
            if native['errors']:
                native['status'] = 'FAIL'
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
        # Give the scratch product binary its normal Bundle.main resource paths;
        # XCTest's main bundle can belong to the system runner and is never edited.
        shutil.copytree(dist, binary.parent / 'web/dist')
        shutil.copy2(assets / 'protocol/remote-client-config-policy.v1.json', binary.parent / 'remote-client-config-policy.v1.json')
        downloads = scratch / 'downloads'; downloads.mkdir()
        qualify_cold_client([str(binary), '--qualify-app-scheme', str(dist),
            str(assets / 'protocol/remote-client-config-policy.v1.json'), '0'],
            env, scratch, deadline, section, downloads)
        code = None
        with synthetic_daemon() as (port, sends):
            try:
                code = qualify_steps([str(binary), '--qualify-app-scheme', str(assets / 'web/dist'),
                    str(assets / 'protocol/remote-client-config-policy.v1.json'), str(port)],
                    env, scratch, deadline, section, downloads)
                section['native_exit'] = code
            finally:
                # Even timeout/failure keeps per-event receipts, never just an
                # end-of-stream array. probe.py serializes this partial report.
                section['sends'] = list(sends)
                downloaded = downloads / 'mac1.txt'
                data = downloaded.read_bytes() if downloaded.is_file() and downloaded.stat().st_size <= 1024 else None
                section['qualification'] = assess(section['events'], section['sends'], code, data)
            qualify_dialogs([str(binary), '--qualify-app-scheme', str(assets / 'web/dist'),
                str(assets / 'protocol/remote-client-config-policy.v1.json'), str(port)],
                env, scratch, deadline, section, downloads)
            qualify_live_switch([str(binary), '--qualify-app-scheme', str(assets / 'web/dist'),
                str(assets / 'protocol/remote-client-config-policy.v1.json'), str(port)],
                env, scratch, deadline, section, downloads)
        if section['cold_client']['status'] != 'PASS':
            raise RuntimeError('cold-client production composition failed; retain receipt')
        if section['dialogs']['status'] != 'PASS':
            raise RuntimeError('dialog screenshot qualification failed; retain capture evidence')
        if section['live_switch']['status'] != 'PASS':
            raise RuntimeError('live-switch qualification failed; retain separate capture evidence')
        if section['qualification']['status'] != 'PASS':
            raise RuntimeError('app-scheme qualification failed; retain partial event evidence')
        if section['native_tests']['status'] != 'PASS':
            raise RuntimeError('full native test workload failed or evidence incomplete')

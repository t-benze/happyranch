"""THR-387 throwaway WKURLSchemeHandler feasibility; stdlib-only and scratch-owned."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from suite_supervisor import _build_environment

ROOT = Path(__file__).resolve().parents[2]
# Cheap route checks first; no page awaits an unrelated M-check.
CASES = (('M1', 'history', 'probe'), ('M1', 'spa', '/'), ('M1', 'spa', '/settings'),
         ('M4', 'storage', 'probe'), ('M4', 'storage', 'probe'), ('M4', 'storage', 'other'),
         ('M2', 'POST', 'probe'), ('M2', 'PUT', 'probe'),
         ('M3', 'stream', 'probe'), ('M3', 'abort', 'probe'),
         ('M4', 'download', 'probe'), ('M4', 'download', 'probe-direct'),
         ('M2', 'multipart', 'probe'), ('M2', 'multipart', 'probe-serialized'))
OUTER_SECONDS = 30
WATCHDOG_SECONDS = 25
TERM_GRACE_SECONDS = 1
EXCERPT_BYTES = 1024
EVENT_BYTES = 24 * 1024
FILE_DIGEST = hashlib.sha256(b's' * (10 * 1024 * 1024)).hexdigest()
DOWNLOAD_DIGEST = hashlib.sha256(b's0 download\n').hexdigest()


class StreamEvidence:
    """Hash/count the full stream while retaining bounded head and tail bytes."""
    def __init__(self) -> None:
        self.count = 0
        self.digest = hashlib.sha256()
        self.head = bytearray()
        self.tail = bytearray()

    def add(self, block: bytes) -> None:
        self.count += len(block)
        self.digest.update(block)
        self.head.extend(block[:max(0, EXCERPT_BYTES - len(self.head))])
        self.tail.extend(block)
        del self.tail[:-EXCERPT_BYTES]

    def retained(self) -> bytes:
        return bytes(self.head + self.tail[max(0, 2 * EXCERPT_BYTES - self.count):])

    def summary(self) -> dict[str, Any]:
        return dict(bytes=self.count, sha256=self.digest.hexdigest(),
                    head=self.head.decode(errors='replace'), tail=self.tail.decode(errors='replace'),
                    truncated=self.count > 2 * EXCERPT_BYTES)


def capture(argv: list[str], env: dict[str, str], cwd: Path, deadline: float
            ) -> tuple[dict[str, Any], bytes]:
    """Always collect streams, including after TERM/grace/group KILL on timeout."""
    started = time.monotonic()
    streams = [StreamEvidence(), StreamEvidence()]
    row: dict[str, Any] = dict(argv=argv, timed_out=False, signals_sent=[])
    child = None
    try:
        if started >= deadline:
            row['timed_out'] = True
            raise TimeoutError('suite deadline exhausted before launch')
        child = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        with selectors.DefaultSelector() as selector:
            for pipe, stream in zip((child.stdout, child.stderr), streams, strict=True):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, stream)
            kill_at = None
            drain_until = None
            while selector.get_map() or child.poll() is None or kill_at is not None:
                now = time.monotonic()
                if not row['timed_out'] and now >= deadline:
                    row['timed_out'] = True
                    kill_at = now + TERM_GRACE_SECONDS
                    try:
                        os.killpg(child.pid, signal.SIGTERM)
                        row['signals_sent'].append('SIGTERM')
                    except ProcessLookupError:
                        pass
                if kill_at is not None and now >= kill_at:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                        row['signals_sent'].append('SIGKILL')
                    except ProcessLookupError:
                        pass
                    kill_at = None
                    drain_until = now + 1
                if drain_until is not None and now >= drain_until:
                    row['drain_incomplete'] = bool(selector.get_map())
                    break
                for key, _ in selector.select(.05):
                    try:
                        block = os.read(key.fd, 65536)
                    except BlockingIOError:
                        continue
                    if block:
                        key.data.add(block)
                    else:
                        selector.unregister(key.fileobj)
                # Even a TERM-cooperative leader can leave descendants/pipes behind.
                if not selector.get_map() and child.poll() is not None and kill_at is None:
                    break
        row['exit_code'] = child.wait(timeout=1)
    except (OSError, TimeoutError, subprocess.TimeoutExpired) as exc:
        row.update(error=f'{type(exc).__name__}: {exc}', exit_code=None)
    finally:
        if child is not None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                row['exit_code'] = child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                row['reap_incomplete'] = True
            for pipe in (child.stdout, child.stderr):
                pipe.close()
        row.update(stdout=streams[0].summary(), stderr=streams[1].summary(),
                   elapsed_seconds=round(time.monotonic() - started, 3))
        code = row.get('exit_code')
        row['signal'] = -code if isinstance(code, int) and code < 0 else None
    return row, streams[0].retained()


def event_evidence(path: Path) -> dict[str, Any]:
    stream = StreamEvidence()
    events = []
    count = invalid = retained = 0
    last = None
    if path.exists():
        with path.open('rb') as source:
            for line in source:
                stream.add(line)
                rows = records(line)
                if not rows:
                    invalid += 1
                    continue
                count += 1
                last = rows[0]
                size = len(json.dumps(last).encode())
                if retained + size <= EVENT_BYTES:
                    events.append(last)
                    retained += size
    # Last phase is useful even when middle events had to be omitted.
    return dict(events=events, last_event=last, event_count=count, invalid_lines=invalid,
                events_truncated=len(events) != count, event_file=stream.summary())


def observe(argv: list[str], env: dict[str, str], cwd: Path, deadline: float,
            event_file: Path) -> dict[str, Any]:
    receipt, _ = capture(argv, env, cwd, deadline)
    evidence = event_evidence(event_file)
    watchdog = any(e.get('name') == 'watchdog_fired' for e in evidence['events'])
    if evidence['last_event']:
        watchdog |= evidence['last_event'].get('name') == 'watchdog_fired'
    status = ('timeout_partial' if receipt['timed_out'] or watchdog else
              'completed' if receipt['exit_code'] == 0 else 'failed')
    return dict(status=status, **receipt, **evidence)


def records(raw: bytes) -> list[dict[str, Any]]:
    result = []
    for line in raw.decode('utf-8', errors='replace').splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            result.append(row)
    return result


def page(run: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for row in run['events']:
        if row.get('name') == 'js' and isinstance(row.get('detail'), str):
            try:
                event = json.loads(row['detail'])
                result[event['name']] = event['detail']
            except (ValueError, KeyError, TypeError):
                pass
    return result


def assess(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify observed JS/native evidence, never observer exit alone."""
    pages = [page(run) for run in runs]
    findings: dict[str, Any] = {}
    history = any(p.get('history', {}).get('path') == '/settings' for p in pages)
    spa = {}
    for run in runs:
        if run['mode'] not in ('/', '/settings'):
            continue
        events = run['events']
        dom = [e['detail'] for e in events if e.get('name') == 'spa-dom']
        try:
            value = json.loads(dom[-1]) if dom else {}
        except (ValueError, TypeError):
            value = {}
        served = [e['detail'] for e in events if e.get('name') == 'served']
        indices = sum(s.get('file') == 'index.html' for s in served)
        assets = [s for s in served if s.get('path', '').startswith('/assets/')]
        reloads = sum(e.get('name') == 'deep-reload' for e in events)
        ok = (run['exit_code'] == 0 and value.get('path') == run['mode']
              and value.get('root', 0) > 0 and len(assets) > 0 and indices >= 1
              and (run['mode'] != '/settings' or (reloads == 1 and indices >= 2)))
        spa[run['mode']] = dict(verified=ok, dom=value, index_responses=indices,
                               asset_responses=len(assets), reloads=reloads)
    findings['M1'] = dict(status='verified' if history and len(spa) == 2 and
                          all(v['verified'] for v in spa.values()) else 'failed',
                          same_document_history=history, spa=spa)
    bodies = []
    for run, p in zip(runs, pages, strict=True):
        upload = p.get('multipart', {})
        upload_ok = (upload.get('intact') is True and upload.get('fileLengths') == [10 * 1024 * 1024]
                     and upload.get('fileSHA256') == FILE_DIGEST)
        bodies.append(dict(mode=run['mode'], upload_intact=upload_ok,
                           POST=p.get('POST'), PUT=p.get('PUT'), upload=upload))
    findings['M2'] = dict(bodies=bodies)
    streams = []
    for run, p in zip(runs, pages, strict=True):
        chunks = p.get('stream', [])
        chunks = chunks if isinstance(chunks, list) else []
        latencies = [c['receivedWallMs'] - c['nativeWallMs'] for c in chunks
                     if isinstance(c, dict) and 'receivedWallMs' in c and 'nativeWallMs' in c]
        intervals = [chunks[i]['receivedMs'] - chunks[i-1]['receivedMs']
                     for i in range(1, len(chunks)) if 'receivedMs' in chunks[i] and 'receivedMs' in chunks[i-1]]
        abort = p.get('abort', {})
        stops = [e['wallMs'] for e in run['events'] if e.get('name') == 'stop' and e.get('detail', {}).get('path') == '/probe/cancel']
        abort_ms = abort.get('abortedWallMs') if isinstance(abort, dict) else None
        delay = stops[0] - abort_ms if stops and isinstance(abort_ms, (int, float)) else None
        late = [e for e in run['events'] if e.get('name') == 'chunk-written' and
                e.get('detail', {}).get('path') == '/probe/cancel' and stops and e['wallMs'] > stops[0]]
        prompt = (len(chunks) == 5 and [c.get('i') for c in chunks] == list(range(5))
                  and len(latencies) == 5 and all(-100 <= ms <= 1000 for ms in latencies)
                  and len(intervals) == 4 and all(500 <= ms <= 1500 for ms in intervals))
        cancelled = delay is not None and -100 <= delay <= 1000 and not late
        streams.append(dict(mode=run['mode'], prompt=prompt, abort_reached_stop=cancelled,
                            chunk_latency_ms=latencies, receipt_intervals_ms=intervals,
                            abort_stop_delay_ms=delay, sends_after_stop=len(late)))
    findings['M3'] = dict(status='verified' if any(s['prompt'] for s in streams) and any(s['abort_reached_stop'] for s in streams)
                          else 'failed', observations=streams,
                          thresholds='5 chunks; receipt interval 500..1500ms; latency/stop <=1000ms, clock tolerance -100ms')
    storage = [p.get('storage') for r, p in zip(runs, pages, strict=True) if r.get('check') == 'storage']
    empty = {'localStorage': None, 'indexedDB': None}
    saved = {'localStorage': 'persistent-s0', 'indexedDB': 'persistent-s0'}
    persistent = storage == [empty, saved, empty]
    downloaded = [dict(mode=r['mode'], **f) for r in runs for f in r['downloads']
                  if f['sha256'] == DOWNLOAD_DIGEST and f['bytes'] == 12]
    blob = any('s0-blob.txt' in f['name'] for f in downloaded)
    direct_download = any(f['mode'] == 'probe-direct' for f in downloaded)
    findings['M4'] = dict(status='verified' if persistent and (blob or direct_download) else 'failed',
                          storage_first_restart_other=storage, origin_persistence=persistent,
                          blob_download=blob, direct_attachment=direct_download, files=downloaded)
    # POST/PUT and upload now run in different processes. Retain the same end-to-end assertions.
    json_intact = all(any(isinstance(p.get(m), dict) and p[m].get('intact') is True
                         and p[m].get('length') == len('{"hello":"你好","n":42}'.encode())
                         and p[m].get('sha256') == hashlib.sha256('{"hello":"你好","n":42}'.encode()).hexdigest()
                         for p in pages) for m in ('POST', 'PUT'))
    direct = any(b['upload_intact'] and b['upload'].get('encoding') == 'FormData' for b in bodies)
    serialized = any(b['upload_intact'] and b['upload'].get('encoding') == 'serialized-bytes' for b in bodies)
    findings['M2'].update(status='verified' if json_intact and (direct or serialized) else 'failed',
                          json_intact=json_intact, direct_FormData=direct, serialized_bytes=serialized)
    for question, finding in findings.items():
        selected = [r for r in runs if r.get('question') == question]
        finding['checks'] = [dict(index=r['index'], check=r['check'], mode=r['mode'], status=r['status'])
                             for r in selected]
        if any(r['status'] == 'timeout_partial' for r in selected):
            finding['status'] = 'timeout_partial'
        elif any(r['status'] != 'completed' or r.get('events_truncated') for r in selected):
            finding['status'] = 'failed'
    return findings


def node_environment(scratch: Path, env: dict[str, str], deadline: float,
                     section: dict[str, Any]) -> dict[str, str]:
    """Reuse existing hash pins and bounded transfer; extract ONLY Node in scratch."""
    from toolchain import curl_attempt, parse_pins, verify_checksum

    pin = parse_pins((ROOT / 'scripts/mac_native/toolchain-pins.json').read_text())['node']
    archive = scratch / 'node.tar.gz'
    end = min(deadline, time.monotonic() + 90)
    attempt = curl_attempt(pin['origins'][0], archive, max(.001, end-time.monotonic()), end)
    section['node_transfer'] = attempt
    if attempt['curl_exit_code'] != 0 or attempt.get('error'):
        raise RuntimeError('pinned Node transfer failed')
    verify_checksum(archive, pin['sha256'], deadline)
    section['node_sha256'] = pin['sha256']
    root = f"node-v{pin['version']}-darwin-arm64"
    total = 0
    with tarfile.open(archive, 'r:gz') as bundle:
        for count, member in enumerate(bundle, 1):
            total += member.size
            if (time.monotonic() >= deadline or count > 20000 or total > 256 * 1024 * 1024
                    or member.size < 0 or member.size > 128 * 1024 * 1024
                    or member.name.split('/')[0] != root):
                raise RuntimeError('Node extraction bounds')
            bundle.extract(member, scratch, filter='data')
    archive.unlink()
    result = dict(env)
    result['PATH'] = str(scratch / root / 'bin') + ':' + env['PATH']
    result['npm_config_cache'] = str(scratch / 'npm-cache')
    return result


def run(report: dict[str, Any]) -> None:
    section: dict[str, Any] = dict(commands=[], runs=[], questions={
        q: dict(status='NOT RUN', reason='not reached') for q in ('M1', 'M2', 'M3', 'M4')})
    report['settings_scheme'] = section
    tmp = Path(os.environ.get('TMPDIR', ''))
    if not tmp.is_absolute() or not tmp.is_dir() or tmp == Path('/tmp') or tmp.is_relative_to('/tmp'):
        raise RuntimeError('requires private non-/tmp build TMPDIR')
    deadline = time.monotonic() + 1200
    with ExitStack() as cleanup:
        cleanup.callback(lambda: section.update(scratch_removed=not scratch.exists()))
        folder = cleanup.enter_context(tempfile.TemporaryDirectory(prefix='settings-scheme-', dir=tmp))
        scratch = Path(folder).resolve()
        section['scratch'] = str(scratch)
        env = _build_environment(scratch)
        env['SETTINGS_S0_STORE_UUID'] = str(uuid.uuid4())
        section['store_identifier'] = env['SETTINGS_S0_STORE_UUID']

        def command(name: str, argv: list[str], seconds: float, cwd: Path = ROOT) -> tuple[int | None, bytes]:
            started = time.monotonic()
            row, raw = capture(argv, env, cwd, min(deadline, started + seconds))
            row['name'] = name
            section['commands'].append(row)
            return row['exit_code'] if not row['timed_out'] else None, raw

        # Required venue facts precede any package build, npm or probe launch.
        for name, argv in (
            ('sw_vers', ['/usr/bin/sw_vers']), ('architecture', ['/usr/bin/uname', '-m']),
            ('swift_path', ['/usr/bin/xcrun', '--find', 'swift']),
            ('swift_version', ['/usr/bin/xcrun', 'swift', '--version']),
            ('managername', ['/bin/launchctl', 'managername']),
            ('sdk', ['/usr/bin/xcrun', '--show-sdk-version']),
        ):
            command(name, argv, 15)
        swift = ['/usr/bin/xcrun', 'swift']
        code, raw = command('gui_preflight', swift + ['-module-cache-path', env['SWIFT_MODULECACHE_PATH'],
            '-Xcc', '-fmodules-cache-path=' + env['CLANG_MODULE_CACHE_PATH'],
            str(ROOT / 'spikes/settings-s0/mac/gui-preflight.swift')], 75)
        gui = records(raw)
        usable = code == 0 and any(r.get('stage') == 'result' and r.get('usable') is True for r in gui)
        section['gui'] = dict(usable=usable, exit_code=code, observations=gui)
        if not usable:
            # A compile/observation error is distinct from evidence of a missing GUI.
            gui_failure = (any(r.get('stage') == 'result' and r.get('usable') is False for r in gui)
                           or (isinstance(code, int) and code < 0)
                           or b'WindowServer' in raw or b'CGSConnection' in raw
                           or (bool(gui) and isinstance(code, int) and code != 0))
            section['result'] = 'GUI unavailable; no M1-M4 execution' if gui_failure else 'GUI preflight failed; GUI availability unknown'
            section['minimal_venue_change'] = ('Run this existing Jenkins agent in the logged-in macOS user Aqua launchd session with WindowServer access; retain the same job/config and source.'
                                               if gui_failure else 'Resolve the recorded preflight error before deciding whether a venue change is necessary.')
            for question in section['questions'].values():
                question['reason'] = section['result']
            if gui_failure:
                section['stop_further_runs'] = True
                return  # Clean build; NOT RUN is not a technical GO.
            raise RuntimeError(section['result'])
        homes = [r.get('home') for r in gui if r.get('stage') == 'windowserver']
        if homes != [env['HOME']]:
            raise RuntimeError('Foundation HOME escaped private scratch; probe not run')
        package = ROOT / 'spikes/settings-s0/mac'
        build = swift + ['build', '--package-path', str(package), '--scratch-path', str(scratch / 'build'),
            '--cache-path', str(scratch / 'swiftpm-cache'), '--config-path', str(scratch / 'swiftpm-config'),
            '--security-path', str(scratch / 'swiftpm-security'), '-c', 'release', '-j', '2',
            '-Xswiftc', '-gnone', '-Xswiftc', '-module-cache-path', '-Xswiftc', env['SWIFT_MODULECACHE_PATH'],
            '-Xcc', '-fmodules-cache-path=' + env['CLANG_MODULE_CACHE_PATH']]
        code, _ = command('swift_build', build, 240)
        if code != 0:
            raise RuntimeError('Swift build failed; no M1-M4 execution')
        code, raw = command('bin_path', build + ['--show-bin-path'], 15)
        if code != 0:
            raise RuntimeError('Swift binary path unavailable')
        binary = Path(raw.decode().strip()) / 'SettingsSchemeProbe'
        if not binary.resolve().is_relative_to(scratch) or not binary.is_file():
            raise RuntimeError('Swift binary outside scratch or absent')
        # The real SPA and installed fetch-event-source come from the exact lock.
        env = node_environment(scratch, env, deadline, section)
        assets = scratch / 'assets'
        shutil.copytree(ROOT / 'web', assets / 'web', ignore=shutil.ignore_patterns('node_modules', 'dist', '*.tsbuildinfo'))
        shutil.copytree(ROOT / 'spikes/settings-s0', assets / 'spikes/settings-s0')
        (assets / 'protocol').mkdir()
        shutil.copy2(ROOT / 'protocol/native-connection-bridge.v1.json', assets / 'protocol')
        for name, argv in (('node_version', ['node', '--version']), ('npm_version', ['npm', '--version']),
                           ('npm_ci', ['npm', 'ci']), ('web_build', ['npm', 'run', 'build'])):
            code, _ = command(name, argv, 180, assets / 'web')
            if code != 0:
                raise RuntimeError(name + ' failed; M1-M4 not reached')
        section['timeouts'] = dict(outer_seconds=OUTER_SECONDS, watchdog_seconds=WATCHDOG_SECONDS,
                                   term_grace_seconds=TERM_GRACE_SECONDS, suite_seconds=1200)
        for index, (question, check, mode) in enumerate(CASES):
            downloads = scratch / f'downloads-{index}'
            downloads.mkdir()
            event_file = scratch / f'events-{index}.jsonl'
            argv = [str(binary), str(assets), str(downloads), mode, check, str(event_file), str(WATCHDOG_SECONDS)]
            observation = observe(argv, env, ROOT, min(deadline, time.monotonic() + OUTER_SECONDS), event_file)
            files = []
            for file in sorted(downloads.iterdir()):
                if file.is_file() and not file.is_symlink():
                    size = file.stat().st_size
                    if size > 1024 * 1024:
                        files.append(dict(name=file.name, bytes=size, sha256=None, error='unexpected download >1MiB'))
                    else:
                        files.append(dict(name=file.name, bytes=size, sha256=hashlib.sha256(file.read_bytes()).hexdigest()))
            section['runs'].append(dict(index=index, question=question, check=check, mode=mode,
                                        downloads=files, **observation))
        section['questions'] = assess(section['runs'])
        section['result'] = 'observed; question statuses control verdict'
        if len(json.dumps(section, indent=2).encode()) > 768 * 1024:
            raise RuntimeError('settings-scheme evidence exceeds 768KiB')
        section['stop_further_runs'] = False
    section['scratch_removed'] = not scratch.exists()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stub', action='store_true', help='Linux-safe result parser exercise; no Mac claim')
    args = parser.parse_args()
    if not args.stub:
        parser.error('standalone mode requires --stub; use probe.py --suite settings-scheme on Mac')
    sample = [dict(index=i, question=q, check=c, mode=m, status='completed', exit_code=0, events=[], downloads=[])
              for i, (q, c, m) in enumerate(CASES)]
    assert all(q['status'] == 'failed' for q in assess(sample).values())
    print(json.dumps(dict(stub=True, platform=sys.platform, missing_evidence_rejected=True,
                          questions={q: 'NOT RUN' for q in ('M1', 'M2', 'M3', 'M4')})))

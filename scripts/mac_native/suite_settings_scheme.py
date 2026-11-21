"""THR-387 throwaway WKURLSchemeHandler feasibility; stdlib-only and scratch-owned."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from suite_supervisor import _build_environment, _command

ROOT = Path(__file__).resolve().parents[2]
MODES = ('probe', 'probe', 'other', '/', '/settings', 'probe-no-multipart',
         'probe-serialized', 'probe-direct')
FILE_DIGEST = hashlib.sha256(b's' * (10 * 1024 * 1024)).hexdigest()
DOWNLOAD_DIGEST = hashlib.sha256(b's0 download\n').hexdigest()


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
        json_ok = all(isinstance(p.get(m), dict) and p[m].get('intact') is True
                      and p[m].get('length') == len('{"hello":"你好","n":42}'.encode())
                      and p[m].get('sha256') == hashlib.sha256('{"hello":"你好","n":42}'.encode()).hexdigest()
                      for m in ('POST', 'PUT'))
        upload = p.get('multipart', {})
        upload_ok = (upload.get('intact') is True and upload.get('fileLengths') == [10 * 1024 * 1024]
                     and upload.get('fileSHA256') == FILE_DIGEST)
        bodies.append(dict(mode=run['mode'], json_intact=json_ok, upload_intact=upload_ok,
                           POST=p.get('POST'), PUT=p.get('PUT'), upload=upload))
    direct = any(b['json_intact'] and b['upload_intact'] and
                 b['upload'].get('encoding') == 'FormData' for b in bodies)
    serialized = any(b['json_intact'] and b['upload_intact'] and
                     b['upload'].get('encoding') == 'serialized-bytes' for b in bodies)
    findings['M2'] = dict(status='verified' if direct or serialized else 'failed',
                          direct_FormData=direct, serialized_bytes=serialized, bodies=bodies)
    streams = []
    for run, p in zip(runs, pages, strict=True):
        chunks = p.get('stream', [])
        chunks = chunks if isinstance(chunks, list) else []
        latencies = [c['receivedWallMs'] - c['nativeWallMs'] for c in chunks
                     if isinstance(c, dict) and 'receivedWallMs' in c and 'nativeWallMs' in c]
        intervals = [chunks[i]['receivedMs'] - chunks[i-1]['receivedMs']
                     for i in range(1, len(chunks)) if 'receivedMs' in chunks[i] and 'receivedMs' in chunks[i-1]]
        abort = p.get('abort', {})
        stops = [e['wallMs'] for e in run['events'] if e.get('name') == 'stop' and e.get('detail') == '/probe/cancel']
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
    findings['M3'] = dict(status='verified' if any(s['prompt'] and s['abort_reached_stop'] for s in streams)
                          else 'failed', observations=streams,
                          thresholds='5 chunks; receipt interval 500..1500ms; latency/stop <=1000ms, clock tolerance -100ms')
    storage = [p.get('storage') for p in pages[:3]]
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
            row: dict[str, Any] = dict(name=name, argv=argv)
            section['commands'].append(row)
            try:
                code, raw = _command(argv, env, cwd, min(deadline, started + seconds), output_cap=1024*1024)
                row.update(exit_code=code, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                           head=raw[:2048].decode(errors='replace'), tail=raw[-4096:].decode(errors='replace'))
                return code, raw
            except (RuntimeError, TimeoutError, OSError, subprocess.TimeoutExpired) as exc:
                row.update(exit_code=None, error=f'{type(exc).__name__}: {exc}',
                           bytes=None, sha256=None)
                return None, b''
            finally:
                row['elapsed_seconds'] = round(time.monotonic() - started, 3)

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
        for name, argv in (('node_version', ['node', '--version']), ('npm_version', ['npm', '--version']),
                           ('npm_ci', ['npm', 'ci']), ('web_build', ['npm', 'run', 'build'])):
            code, _ = command(name, argv, 180, assets / 'web')
            if code != 0:
                raise RuntimeError(name + ' failed; M1-M4 not reached')
        for index, mode in enumerate(MODES):
            downloads = scratch / f'downloads-{index}'
            downloads.mkdir()
            code, raw = command(f'probe_{index}', [str(binary), str(assets), str(downloads), mode], 50)
            files = []
            for file in sorted(downloads.iterdir()):
                if file.is_file() and not file.is_symlink():
                    size = file.stat().st_size
                    if size > 1024 * 1024:
                        files.append(dict(name=file.name, bytes=size, sha256=None, error='unexpected download >1MiB'))
                    else:
                        files.append(dict(name=file.name, bytes=size, sha256=hashlib.sha256(file.read_bytes()).hexdigest()))
            section['runs'].append(dict(index=index, mode=mode, exit_code=code, events=records(raw), downloads=files))
        section['questions'] = assess(section['runs'])
        section['result'] = 'observed; question statuses control verdict'
        if len(json.dumps(section).encode()) > 200 * 1024:
            raise RuntimeError('settings-scheme evidence exceeds 200KiB')
        section['stop_further_runs'] = False
    section['scratch_removed'] = not scratch.exists()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stub', action='store_true', help='Linux-safe result parser exercise; no Mac claim')
    args = parser.parse_args()
    if not args.stub:
        parser.error('standalone mode requires --stub; use probe.py --suite settings-scheme on Mac')
    sample = [dict(mode=m, exit_code=0, events=[], downloads=[]) for m in MODES]
    assert all(q['status'] == 'failed' for q in assess(sample).values())
    print(json.dumps(dict(stub=True, platform=sys.platform, missing_evidence_rejected=True,
                          questions={q: 'NOT RUN' for q in ('M1', 'M2', 'M3', 'M4')})))

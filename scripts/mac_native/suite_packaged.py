"""One scratch-only packaged build and bounded frozen-runtime observation.

No runtime imports: every product operation crosses the installed bundle's
executable or HTTP boundary. Native execution needs separate job authorization.
"""
from __future__ import annotations

import hashlib
import http.client
from itertools import islice
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
STEP_NAMES = (
    'build', 'install', 'launch', 'health_and_web', 'session_stop',
    'frozen_cleanup', 'quit_and_census', 'exit_scratch_cleanup',
)
STREAM_CAP = 4 * 1024 * 1024
REPORT_CAP = 60000
PROCESS_FIELDS = ('pid', 'ppid', 'pgid', 'state', 'start_identity')
SURVIVOR_CAP = 32


def initial_steps(reason: str = 'not reached') -> dict[str, dict[str, Any]]:
    return {str(i): {'name': name, 'status': 'NOT_RUN', 'reason': reason}
            for i, name in enumerate(STEP_NAMES, 1)}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


class CensusError(ValueError):
    def __init__(self, evidence: dict[str, Any]) -> None:
        self.evidence = evidence
        super().__init__('incomplete process census: ' + evidence['reason'])


def census_failure(exc: Exception) -> dict[str, Any]:
    # Other census failures may carry arbitrary subprocess output. Keep their
    # class only; the parser's evidence contains exclusively the requested ps fields.
    return exc.evidence if isinstance(exc, CensusError) else {
        'status': 'INCOMPLETE', 'reason': type(exc).__name__}


class CommandFailure(RuntimeError):
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        super().__init__(f'command failed exit={result["exit_code"]}; '
                         + ('process census incomplete' if result.get('census_error')
                            else result.get('error', result['tail'][-1024:])))


def command(argv: list[str], cwd: Path, env: dict[str, str], seconds: float,
            *, stdin: bytes = b'', cap: int = STREAM_CAP) -> dict[str, Any]:
    """Drain child output despite census failure; always stop/reap owned group.

    Only build/fixture/helper commands use this runner; authentication responses
    and daemon output never enter this report. Census uncertainty latches failure
    independently of the child's exit, without aborting a running build.
    """
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            start_new_session=True)
    data = bytearray()
    total = 0
    sha = hashlib.sha256()
    end = time.monotonic() + seconds
    observed = {(proc.pid, proc.pid)}
    next_sample = 0.0
    result: dict[str, Any] = {}
    def sample(phase: str) -> list[dict[str, Any]] | None:
        try:
            current = census()
            observed.update((row['pid'], row['pgid']) for row in descendants(current, proc.pid))
            if len(observed) > 8192:
                raise RuntimeError('command process identity cap')
            return current
        except Exception as exc:
            evidence = census_failure(exc)
            error = result.setdefault('census_error', {'status': 'INCOMPLETE',
                'first': evidence, 'phases': []})
            error['phases'].append(phase)
            if evidence != error['first']:
                error[phase] = evidence
            return None
    try:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(stdin)
        proc.stdin.close()
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            while selector.get_map():
                if time.monotonic() >= next_sample and 'census_error' not in result:
                    sample('sampling')
                    next_sample = time.monotonic() + .5
                if time.monotonic() >= end:
                    raise TimeoutError('packaged command deadline')
                for key, _ in selector.select(min(.1, max(0, end - time.monotonic()))):
                    block = os.read(key.fileobj.fileno(), 65536)
                    if not block:
                        selector.unregister(key.fileobj)
                        continue
                    sha.update(block)
                    total += len(block)
                    data.extend(block)
                    del data[:-2048]
                    if total > cap:
                        raise RuntimeError('packaged command output cap')
        proc.wait(timeout=max(.001, end - time.monotonic()))
    except Exception as exc:
        result['error'] = type(exc).__name__ + ': ' + str(exc)
    finally:
        # A frozen helper may start its own worker session. Signal only groups
        # witnessed under this command, with a still-matching member, never a
        # process selected merely by a scratch-looking command name.
        try:
            current = sample('finally')
            if current is not None:
                alive = {(row['pid'], row['pgid']) for row in current}
                for group in {group for pid, group in observed if (pid, group) in alive}:
                    if group != os.getpgrp():
                        try:
                            os.killpg(group, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
        except Exception as exc:
            result['cleanup_error'] = type(exc).__name__
        finally:
            # This group is ours by start_new_session, even when ps is unknown.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError as exc:
                result['cleanup_error'] = type(exc).__name__
            try:
                proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired) as exc:
                result['cleanup_error'] = type(exc).__name__
            for stream in (proc.stdin, proc.stdout):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError as exc:
                        result['cleanup_error'] = type(exc).__name__
    result.update(exit_code=proc.returncode, bytes=total, sha256=sha.hexdigest(),
                  tail=data.decode('utf-8', errors='replace'))
    return result



def build_failure_evidence(out: Path) -> dict[str, Any]:
    """Keep bounded resource/archive verifier failures before scratch removal."""
    path = out / 'diagnostics/resource-failure.json'
    if not path.exists():
        return {'status': 'NOT_AVAILABLE', 'reason': 'resource/archive verifier failure file absent'}
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('unsafe resource failure evidence')
    with path.open('rb') as stream:
        raw = stream.read(49153)
    if len(raw) > 49152:
        raise RuntimeError('resource failure evidence cap')
    value = json.loads(raw)
    resource = value.get('error') == 'RESOURCE_VERIFICATION_FAILED' and isinstance(value.get('detail'), list)
    membership = (value.get('error') == 'ARCHIVE_IDENTITY_MISMATCH'
                  and isinstance(value.get('detail'), dict)
                  and value['detail'].get('reason') == 'archive membership')
    if not (resource or membership):
        raise RuntimeError('invalid resource failure evidence')
    return {'status': 'COMPLETE', 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
            'report': value}


def checked(argv: list[str], cwd: Path, env: dict[str, str], seconds: float,
            **kwargs: Any) -> dict[str, Any]:
    row = command(argv, cwd, env, seconds, **kwargs)
    if row['exit_code'] != 0 or any(key in row for key in ('error', 'census_error', 'cleanup_error')):
        raise CommandFailure(row)
    return row


def parse_census(raw: str) -> list[dict[str, Any]]:
    rows = []
    rejected: list[dict[str, Any]] = []
    rejected_count = 0
    # LF is ps's row separator. splitlines() would hide CR/control characters
    # and corrupt the original line indices in rejected-row evidence.
    lines = raw.split('\n')
    if lines[-1] == '':
        lines.pop()
    for index, line in enumerate(lines, 1):
        values = line.split(None, 9)
        reason = ''
        if len(values) != 10:
            reason = 'field_count'
        elif not all(re.fullmatch(r'[0-9]{1,10}', value) and int(value) <= 2147483647
                     for value in values[:3]):
            reason = 'pid_ppid_pgid'
        # Apple adv_cmds ps/tasks.c mach_state_table includes '?'. It is a
        # process with unknown state, never proof that the process is absent.
        elif not re.fullmatch(r'[A-Za-z?][A-Za-z0-9+<\-]*', values[3]):
            reason = 'state'
        elif any(ord(char) < 32 and char != '\t' for char in line):
            reason = 'control_character'
        else:
            identity = ' '.join(values[4:9])
            try:
                time.strptime(identity, '%a %b %d %H:%M:%S %Y')
            except ValueError:
                reason = 'lstart'
        if reason:
            rejected_count += 1
            if len(rejected) < 16:
                full_escaped = json.dumps(line, ensure_ascii=True)[1:-1]
                # ASCII JSON escapes, never literal controls. Limit the escaped
                # value to 512 bytes without cutting an escape sequence.
                escaped = ''
                for char in line:
                    part = json.dumps(char, ensure_ascii=True)[1:-1]
                    if len(escaped) + len(part) > 512:
                        break
                    escaped += part
                rejected.append({'line_index': index, 'reason': reason, 'row': escaped,
                                 'truncated': escaped != full_escaped,
                                 'row_bytes': len(full_escaped),
                                 'row_sha256': hashlib.sha256(full_escaped.encode('ascii')).hexdigest()})
        elif len(rows) < 8192:
            rows.append(dict(zip((*PROCESS_FIELDS, 'executable'),
                [*map(int, values[:3]), values[3], identity, values[9]], strict=True)))
    if rejected_count or not lines or len(lines) > 8192:
        raise CensusError({'status': 'INCOMPLETE',
            'reason': 'rejected_rows' if rejected_count else 'empty' if not lines else 'row_cap',
            'row_count': len(lines), 'rejected_count': rejected_count, 'rejected_rows': rejected,
            'truncated': rejected_count > len(rejected) or any(row['truncated'] for row in rejected),
            'row_limit': 16, 'row_bytes_limit': 512,
            'redaction': 'none; ps pid/ppid/pgid/stat/lstart/comm only, no argv; JSON-escaped rows'})
    return rows


def census() -> list[dict[str, Any]]:
    # comm omits argv: no arbitrary host command arguments/credentials collected.
    result = subprocess.run(['/bin/ps', '-axo', 'pid=,ppid=,pgid=,stat=,lstart=,comm='],
                            env=dict(os.environ, LC_ALL='C'), capture_output=True, timeout=5, check=True)
    if len(result.stdout) > 1024 * 1024:
        raise RuntimeError('process census byte cap')
    return parse_census(result.stdout.decode('utf-8'))


def descendants(rows: list[dict[str, Any]], root: int) -> list[dict[str, Any]]:
    selected = {root}
    for _ in range(len(rows)):
        added = {row['pid'] for row in rows if row['ppid'] in selected} - selected
        if not added:
            break
        selected.update(added)
    return [{key: row[key] for key in PROCESS_FIELDS}
            for row in rows if row['pid'] in selected]


def owned_rows(rows: list[dict[str, Any]], scratch: Path,
               groups: set[int], excluded: set[int],
               identities: set[tuple[int, str]] | None = None) -> list[dict[str, Any]]:
    return [{key: row[key] for key in PROCESS_FIELDS}
            for row in rows if row['pid'] not in excluded and
            (row['pgid'] in groups or row['executable'].startswith(str(scratch) + '/')
             or (row['pid'], row['start_identity']) in (identities or set()))]


def private_session_groups(rows: list[dict[str, Any]], root: int, daemon: int) -> set[int]:
    """A group is private only if its leader is on this session's ancestry."""
    by_pid = {row['pid']: row for row in rows}
    daemon_group = by_pid[daemon]['pgid']
    lineage: set[int] = set()
    pid = root
    while pid != daemon:
        if pid in lineage or pid not in by_pid:
            raise RuntimeError('session ancestry unknown')
        lineage.add(pid)
        pid = by_pid[pid]['ppid']
    return {by_pid[pid]['pgid'] for pid in lineage
            if by_pid[pid]['pgid'] != daemon_group and by_pid[pid]['pgid'] in lineage
            and by_pid[by_pid[pid]['pgid']]['pgid'] == by_pid[pid]['pgid']}


def session_survivors(rows: list[dict[str, Any]], verified: list[dict[str, Any]],
                      private_groups: set[int]) -> list[dict[str, Any]]:
    identities = {(row['pid'], row['start_identity']) for row in verified}
    return [{key: row[key] for key in PROCESS_FIELDS} for row in rows
            if (row['pid'], row['start_identity']) in identities or row['pgid'] in private_groups]


def survivor_snapshot(select: Any) -> dict[str, Any]:
    """Unknown observations never masquerade as an empty process table."""
    try:
        rows = select(census())
        return {'status': 'SURVIVORS' if rows else 'ABSENT', 'count': len(rows),
                'truncated': len(rows) > SURVIVOR_CAP, 'rows': rows[:SURVIVOR_CAP],
                'identity_source': 'ps lstart (seconds); same-second PID reuse conservatively survives'}
    except Exception as exc:
        result = {'status': 'UNKNOWN', 'error': type(exc).__name__}
        if isinstance(exc, CensusError):
            result['census_error'] = exc.evidence
        return result


def bound_census_evidence(report: dict[str, Any]) -> None:
    """Spend only the report's remaining serialized-byte budget on row copies.

    Keep the first rejected row's exact bounded prefix. Later copies may lose
    their text, never their indices, reasons, full escaped-row hashes or counts.
    Measure JSON bytes (including re-escaping), not the parser's prefix lengths.
    The reserve covers probe's failure envelope and the collector's EXIT join.
    """
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if 'rejected_rows' in value:
                rows.extend((value, row) for row in value['rejected_rows'])
            else:
                for child in value.values():
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(report)
    # Reverse retention order preserves the earliest exact evidence. This also
    # handles distinct failures and command first/final payloads; deduplicating
    # identical snapshots alone cannot bound those paths.
    for evidence, row in reversed(rows[1:]):
        if len(json.dumps(report).encode()) <= REPORT_CAP - 2000:
            break
        row.update(row='', report_truncated=True)
        evidence['truncated'] = True


def backend_diagnostics(path: Path, selected: dict[str, Any], secrets: tuple[str, ...]) -> dict[str, Any]:
    """Retain only the factory's fixed event templates, never raw daemon logs."""
    result: dict[str, Any] = {'selected': selected, 'outcome': 'SELECTED' if selected.get('name') else 'UNKNOWN', 'candidates': {
        name: {'status': 'NOT_OBSERVED'} for name in ('linux', 'macos')}, 'events': []}
    try:
        with path.open('rb') as stream:
            raw = stream.read(STREAM_CAP + 1)
        result.update(status='COMPLETE', bytes_read=len(raw), truncated=len(raw) > STREAM_CAP)
        for line in raw[:STREAM_CAP].decode('utf-8', errors='replace').splitlines():
            match = re.search(r"runtime\.platform\.backend_factory: (host backend '(linux|macos)' "
                              r"(?:probe raised: .*|unhealthy \(.*\); trying next)|"
                              r"selected host backend '[^']+' \(healthy, \d+ capability level\(s\)\)|"
                              r"selected honest fallback backend 'passthrough' \(all capabilities unavailable\))$", line)
            if not match:
                continue
            event = match[1]
            for secret in secrets:
                if secret:
                    event = event.replace(secret, '[redacted]')
            event = re.sub(r'(?i)\bBearer\s+[^\s,;)]+', 'Bearer [redacted]', event)
            event = re.sub(r'(?i)\b(token|password|secret|api[_-]?key|authorization)\s*[=:]\s*[^\s,;)]+',
                           r'\1=[redacted]', event)
            event = re.sub(r'://[^/\s@]+@', '://[redacted]@', event)
            if len(event.encode()) > 768:
                result['truncated'] = True
                event = event.encode()[:768].decode('utf-8', errors='ignore')
            if len(result['events']) < 8:
                result['events'].append(event)
            else:
                result['truncated'] = True
            if match[2]:
                result['candidates'][match[2]] = {'status': 'RAISED' if 'probe raised:' in match[1]
                                                else 'UNHEALTHY', 'reason': event}
    except OSError as exc:
        result.update(status='UNKNOWN', error=type(exc).__name__)
    return result


def require_tree_backend(selection: dict[str, Any]) -> None:
    backend = selection['selected']
    capabilities = backend.get('capabilities', {})
    if (not backend.get('name') or backend.get('name') == 'passthrough' or backend.get('healthy') is not True
            or not any(capabilities.get(name) in ('best_effort', 'guaranteed')
                       for name in ('kills_tree_best_effort', 'kills_tree_guaranteed'))):
        raise RuntimeError('packaged session requires a healthy tree-killing backend; '
                           + json.dumps(selection['candidates'], ensure_ascii=True))


def await_value(fn: Any, seconds: float, label: str) -> Any:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = fn()
        if value:
            return value
        time.sleep(.25)
    raise TimeoutError(label)


def await_absence(select: Any, seconds: float, label: str, section: dict[str, Any], key: str) -> None:
    def absent() -> bool:
        section[key] = survivor_snapshot(select)
        if section[key]['status'] == 'UNKNOWN':
            raise RuntimeError(label + ': process evidence unknown')
        return section[key]['status'] == 'ABSENT'
    await_value(absent, seconds, label)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class ApiFailure(RuntimeError):
    def __init__(self, reason: str, observation: dict[str, Any]):
        self.observation = {**observation, 'error': reason}
        super().__init__('packaged HTTP ' + json.dumps(self.observation, ensure_ascii=True))


class Api:
    def __init__(self, home: Path):
        self.base = 'http://127.0.0.1:' + str(int((home / 'daemon.port').read_text()))
        self.token = (home / 'daemon.token').read_text().strip()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.last_observation: dict[str, Any] = {}

    def observe(self, method: str, path: str, status: int | None,
                content_type: str | None, raw: bytes, token: str | None) -> dict[str, Any]:
        # Never retain auth/profile responses, or echo a bearer in an error body.
        sensitive = path.startswith(('/api/v1/auth/', '/api/v1/runtime/custom-cli/',
                                     '/api/v1/runtime/adapters/'))
        for secret in (self.token, token):
            if secret:
                raw = raw.replace(secret.encode(), b'[redacted]')
        prefix = '[redacted: credential-bearing endpoint]' if sensitive else raw[:512].decode('utf-8', errors='replace')
        prefix = prefix.encode('utf-8')[:512].decode('utf-8', errors='ignore')
        self.last_observation = {'method': method, 'path': path.split('?')[0],
            'http_status': status, 'content_type': (content_type or '')[:256],
            'body_prefix': prefix, 'body_redacted': sensitive}
        return self.last_observation

    def get_bytes(self, path: str, *, limit: int = 1024 * 1024,
                  body: dict | None = None, token: str | None = None) -> bytes:
        request = urllib.request.Request(self.base + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Authorization': 'Bearer ' + (self.token if token is None else token),
                     'Content-Type': 'application/json'})
        observation = self.observe(request.get_method(), path, None, None, b'', token)
        try:
            with self.opener.open(request, timeout=45 if body is not None else 5) as response:
                observation = self.observe(request.get_method(), path, response.status,
                                           response.headers.get('Content-Type'), b'', token)
                raw = response.read(limit + 1)
                observation = self.observe(request.get_method(), path, response.status,
                                           response.headers.get('Content-Type'), raw, token)
                if len(raw) > limit:
                    raise ApiFailure('HTTP response cap', observation)
                return raw
        except urllib.error.HTTPError as exc:
            observation = self.observe(request.get_method(), path, exc.code,
                                       exc.headers.get('Content-Type'), b'', token)
            with exc:
                try:
                    raw = exc.read(512 + max(len(self.token), len(token or '')))
                except (OSError, http.client.HTTPException) as read_error:
                    raise ApiFailure(type(read_error).__name__, observation) from None
            observation = self.observe(request.get_method(), path, exc.code,
                                       exc.headers.get('Content-Type'), raw, token)
            raise ApiFailure('non-2xx response', observation) from None
        except (OSError, http.client.HTTPException) as exc:
            raise ApiFailure(type(exc).__name__, observation) from None

    def json(self, path: str, body: dict | None = None, *, token: str | None = None) -> Any:
        raw = self.get_bytes(path, body=body, token=token)
        try:
            return json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise ApiFailure(type(exc).__name__, self.last_observation) from None


def health_and_web(api: Api, readiness: dict[str, Any]) -> dict[str, Any]:
    def healthy() -> Any:
        try:
            value = api.json('/api/v1/health')
        except ApiFailure as exc:
            readiness.update(exc.observation)
            # Malformed responses name themselves immediately, rather than
            # spending the entire readiness window retrying a decode failure.
            if exc.observation['http_status'] is not None and exc.observation['error'] != 'non-2xx response':
                raise
            return None
        readiness.clear()
        readiness.update(api.last_observation)
        readiness['health_status'] = value.get('status') if isinstance(value, dict) else None
        return value if readiness['health_status'] == 'ok' else None
    try:
        health = await_value(healthy, 45, 'daemon health endpoint')
    except TimeoutError:
        raise ApiFailure('readiness deadline', readiness) from None
    backend = health.get('host_sessions', {}).get('backend', {})
    readiness['backend'] = {key: backend[key] for key in ('name', 'version', 'healthy', 'capabilities') if key in backend}
    html = api.get_bytes('/')
    match = re.search(rb'(?:src|href)=["\'](/assets/[^"\']+\.(?:js|css))["\']', html)
    if not match or b'<html' not in html.lower():
        raise RuntimeError('served root is not packaged UI HTML with asset')
    asset_path = match[1].decode('ascii')
    asset = api.get_bytes(asset_path, limit=4 * 1024 * 1024)
    if not asset:
        raise RuntimeError('empty packaged web asset')
    return dict(name=STEP_NAMES[3], status='PASS', health_status=health.get('status'),
        backend=readiness['backend'],
        root_bytes=len(html), root_sha256=hashlib.sha256(html).hexdigest(), asset=asset_path,
        asset_bytes=len(asset), asset_sha256=hashlib.sha256(asset).hexdigest(), browser='NOT_RUN: HTTP only')


def retain_product(out: Path, workspace: Path, scratch: Path, source: str, tree: str,
                   bundle_hashes: dict[str, str]) -> dict[str, Any]:
    """Keep two receipt-verified ZIPs outside outer scratch, never archive them."""
    workspace = workspace.resolve(strict=True)
    if not scratch.is_relative_to(workspace) or scratch == workspace:
        raise RuntimeError('retention requires job scratch inside Jenkins workspace')
    receipt_path = out / 'build-receipt.json'
    if receipt_path.is_symlink() or receipt_path.stat().st_size > 65536:
        raise RuntimeError('unsafe/oversized build receipt')
    receipt = json.loads(receipt_path.read_bytes())
    run_id = receipt.get('run_id', '')
    if (receipt.get('role') != 'unsigned-inner-zip' or receipt.get('source_sha') != source
            or receipt.get('source_tree') != tree or not re.fullmatch(r'[a-f0-9-]{36}', run_id)
            or receipt.get('filename') != 'HappyRanch-1.0.1-1.0.1-arm64.zip'):
        raise RuntimeError('retained product receipt identity mismatch')
    archive = out / receipt['filename']
    if (archive.is_symlink() or not archive.is_file() or archive.stat().st_size > 256 * 1024 * 1024
            or archive.stat().st_size != receipt['size'] or digest(archive) != receipt['sha256']):
        raise RuntimeError('retained ZIP differs from strictly verified shipping receipt')
    root = workspace / 'retained'
    if root.is_relative_to(scratch):
        raise RuntimeError('retention location is inside outer scratch')
    def safe(path: Path, directory: bool) -> None:
        info = path.lstat()
        if (not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
                or info.st_uid != os.getuid() or info.st_mode & 0o022
                or (not directory and info.st_nlink != 1)):
            raise RuntimeError('unsafe retained path: ' + str(path))
    if not root.exists() and not root.is_symlink():
        root.mkdir(mode=0o700)
    safe(root, True)
    previous = list(islice(root.iterdir(), 3))
    if len(previous) > 2:
        raise RuntimeError('retention directory count exceeds two')
    # Validate the entire small owned namespace before removing any entry.
    for directory in previous:
        safe(directory, True)
        if not re.fullmatch(r'[a-f0-9]{40}-[a-f0-9-]{36}', directory.name):
            raise RuntimeError('unknown retained directory')
        entries = list(islice(directory.iterdir(), 3))
        if {p.name for p in entries} != {'identity.json', receipt['filename']}:
            raise RuntimeError('unknown retained contents')
        for path in entries:
            safe(path, False)
        identity = directory / 'identity.json'
        if identity.stat().st_size > 4096:
            raise RuntimeError('retained identity cap')
        old = json.loads(identity.read_bytes())
        if (directory.name != old['source_sha'] + '-' + old['run_id']
                or old['path'] != str(directory / receipt['filename'])):
            raise RuntimeError('retained identity mismatch')
    # Keep the newest previous product plus this product. Never touch any
    # directory outside this validated, nonrecursive workspace namespace.
    for directory in sorted(previous, key=lambda p: p.stat().st_mtime_ns, reverse=True)[1:]:
        for path in (directory / 'identity.json', directory / receipt['filename']):
            path.unlink()
        directory.rmdir()
    destination = root / (source + '-' + run_id)
    destination.mkdir(mode=0o700)
    target = destination / archive.name
    with archive.open('rb') as src, target.open('xb') as dst:
        remaining = receipt['size']
        while block := src.read(min(1024 * 1024, remaining + 1)):
            remaining -= len(block)
            if remaining < 0:
                raise RuntimeError('retained ZIP grew beyond verified size')
            dst.write(block)
    target.chmod(0o600)
    if target.stat().st_size != receipt['size'] or digest(target) != receipt['sha256']:
        raise RuntimeError('retained ZIP copy identity mismatch')
    identity = {key: receipt[key] for key in ('source_sha', 'source_tree', 'run_id', 'size',
                                             'sha256', 'app_inventory_sha256')}
    identity.update(path=str(target), bundle_hashes=bundle_hashes,
                    availability='Jenkins workspace only; latest two verified ZIPs')
    raw_identity = json.dumps(identity)
    if len(raw_identity.encode()) > 4096:
        raise RuntimeError('retained identity cap')
    identity_path = destination / 'identity.json'
    with os.fdopen(os.open(identity_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
        stream.write(raw_identity)
    return identity


# The child is a declared deterministic CLI. It consumes the entire prompt and
# returns its actual response during Connect conformance; the wrapper transports
# that response, rather than synthesizing a conformance success/canary. A real
# task blocks with one child, providing an independently enumerable process tree.
CHILD = '''import json, os, signal, subprocess, sys
from pathlib import Path
if sys.argv[1:] == ['--version']:
    print('packaged-deterministic 1.0.0'); raise SystemExit(0)
data = json.load(sys.stdin)
if data['invocation']['task_id'] is None:
    print(data['prompt']); raise SystemExit(0)
child = subprocess.Popen(['/bin/sleep', '180'])
def stop(*_):
    child.terminate()
    child.wait(timeout=3)
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
marker = Path(data['workspace']) / 'packaged-session.json'
marker.write_text(json.dumps({'pid': os.getpid(), 'child': child.pid,
    'pgid': os.getpgrp(), 'session_id': data['invocation']['invocation_id']}))
try:
    child.wait(timeout=180)
finally:
    if child.poll() is None: child.terminate()
    child.wait(timeout=3)
'''


def connect_deterministic(api: Api, scratch: Path) -> str:
    profile = 'packaged-deterministic'
    minted = api.json('/api/v1/auth/registration-token/runtime', {
        'name': profile, 'purpose': 'adapter', 'intended_profile_name': profile,
        'workspace_adapter_id': 'pi'})
    token = minted['token']
    reference = api.json('/api/v1/runtime/adapters/contract-reference', token=token)
    status = api.json('/api/v1/runtime/custom-cli/status?intended_profile_name=' + profile)
    wrapper = Path(status['wrapper_destination'])
    if not wrapper.is_relative_to(scratch):
        raise RuntimeError('wrapper escaped job scratch')
    child = scratch / 'deterministic-cli'
    child.write_text('#!' + sys.executable + '\n' + CHILD)
    child.chmod(0o700)
    wrapper.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    wrapper.write_text('#!' + sys.executable + '\n' + '''import json, subprocess, sys
value = json.load(sys.stdin)
p = subprocess.run([CHILD], input=json.dumps(value), text=True, capture_output=True,
                   timeout=value['timeout']['max_runtime_seconds'])
print(json.dumps({'success': p.returncode == 0, 'duration_seconds': 0,
 'session_id': value['invocation']['invocation_id'], 'returncode': p.returncode,
 'stdout_tail': p.stdout[-2000:], 'stderr_tail': p.stderr[-2000:],
 'result': {'text': p.stdout}, 'adapter_metadata': {
 'adapter': ADAPTER, 'adapter_version': '1.0.0', 'contract_version': 1}}))
'''.replace('CHILD', repr(str(child))).replace('ADAPTER', repr(reference['canonical_adapter_id'])))
    wrapper.chmod(0o700)
    connected = api.json('/api/v1/runtime/custom-cli/connect', {'manifest': {
        'manifest_version': 2, 'wrapper_sha256': digest(wrapper),
        'workspace_adapter_id': 'pi', 'upgradeable_children': [{
            'slot': 'deterministic-cli', 'executable': str(child),
            'version_probe_argv': [str(child), '--version']}] }}, token=token)
    # Normal daemon projection owns conformance/publication; no store edits,
    # approval fabrication or profile/eligibility bypass.
    operation = connected['operation_id']
    result = api.json('/api/v1/runtime/custom-cli/' + operation + '/commit', {})
    if result.get('profile_state') != 'committed':
        raise RuntimeError('ordinary custom CLI conformance/publication refused')
    return profile


def run(report: dict[str, Any]) -> None:
    from scripts.mac_native.suite_toolchain import executor_feasibility
    from scripts.mac_native.toolchain import PINS, parse_pins, provision

    steps = report.setdefault('steps', initial_steps())
    scratch = Path(os.environ.get('TMPDIR', '')).resolve().parent
    if (not os.environ.get('TMPDIR') or not scratch.is_dir() or
            not ROOT.is_relative_to(scratch)):
        raise RuntimeError('packaged requires existing job-owned source/TMPDIR scratch')
    area = scratch / 'packaged'
    area.mkdir(mode=0o700)
    section: dict[str, Any] = {'source_sha': os.environ.get('SOURCE_SHA'),
        'real_executor_session': {'status': 'NOT_RUN', 'reason': 'not reached'},
        'gui_launch': 'NOT_RUN: bundled AppDelegate persists NSHomeDirectory/Library; GUI cannot guarantee private HOME isolation in this agent context',
        'appkit_quit': 'NOT_RUN', 'commands': {}}
    report['packaged'] = section
    daemon = None
    log = None
    api = None
    selected_backend: dict[str, Any] = {}
    daemon_env: dict[str, str] = {}
    ancestors = {os.getpid()}
    groups: set[int] = set()
    tracked_identities: set[tuple[int, str]] = set()
    daemon_identity: tuple[int, str] | None = None
    ownership_unknown = False
    def select_owned(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if ownership_unknown:
            raise RuntimeError('daemon descendant evidence unknown')
        return owned_rows(rows, scratch, groups, ancestors, tracked_identities)
    def capture_owned(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        nonlocal daemon_identity
        root = next((row for row in rows if row['pid'] == daemon.pid), None)
        if root is None:
            raise RuntimeError('daemon ancestry unavailable')
        identity = (root['pid'], root['start_identity'])
        if daemon_identity is None:
            # Popen still owns a live, unreaped child at this first census.
            if daemon.poll() is not None or root['pgid'] != daemon.pid:
                raise RuntimeError('daemon identity unavailable')
            daemon_identity = identity
        if identity != daemon_identity:
            raise RuntimeError('daemon identity changed')
        tree = descendants(rows, daemon.pid)
        tracked_identities.update((row['pid'], row['start_identity']) for row in tree)
        by_pid = {row['pid']: row for row in tree}
        # A private group's leader must itself be an authenticated descendant.
        # These groups only select evidence; they never authorize signals.
        groups.update(row['pgid'] for row in tree if row['pgid'] != root['pgid']
                      and row['pgid'] in by_pid
                      and by_pid[row['pgid']]['pgid'] == row['pgid'])
        return select_owned(rows)
    def capture_snapshot() -> dict[str, Any]:
        nonlocal ownership_unknown
        result = survivor_snapshot(capture_owned)
        if result['status'] == 'UNKNOWN':
            ownership_unknown = True
        return result
    section['post_cancel_survivors'] = {'status': 'NOT_RUN', 'reason': 'cancel not reached'}
    active = '1'
    try:
        before = census()
        for _ in range(len(before)):
            parents = {r['ppid'] for r in before if r['pid'] in ancestors}
            if parents <= ancestors:
                break
            ancestors.update(parents)
        source = checked(['git', '--no-replace-objects', 'rev-parse', 'HEAD'], ROOT, dict(os.environ), 5)['tail'].strip()
        if not re.fullmatch('[a-f0-9]{40}', source) or source != section['source_sha']:
            raise RuntimeError('SOURCE_SHA does not match exact checked out commit')
        tree = checked(['git', '--no-replace-objects', 'rev-parse', 'HEAD^{tree}'], ROOT, dict(os.environ), 5)['tail'].strip()
        section['source_tree'] = tree
        feasibility = executor_feasibility(time.monotonic() + 30)
        section['executor_feasibility'] = feasibility
        attempts: list[dict] = []
        section['provisioning_attempts'] = attempts
        env = provision(area / 'toolchain', parse_pins(PINS.read_text()),
                        time.monotonic() + 240, attempts)
        # The documented freezer refuses inherited PYTHON* job-bootstrap
        # controls. Remove them only for this build; no host/global env mutation.
        build_env = {k: v for k, v in env.items() if not k.startswith('PYTHON')}
        # The wrapper's private GOMODCACHE must remain removable by outer EXIT.
        build_env['GOFLAGS'] = (build_env.get('GOFLAGS', '') + ' -modcacherw').strip()
        originals = area / 'web-originals'
        out = area / 'build'
        section['build_output'] = {'output_dir': str(out), 'app_path': str(out / 'HappyRanch.app'),
            'zip_path': str(out / 'HappyRanch-1.0.1-1.0.1-arm64.zip'),
            'availability': 'scratch paths only until retained_product verifies the ZIP'}
        section['commands']['originals'] = checked([sys.executable, '-B',
            str(ROOT / 'app/mac/scripts/prepare_web_originals.py'), '--source', str(ROOT),
            '--destination', str(originals)], area, build_env, 900)
        section['build_attempted'] = True
        section['commands']['build'] = command(['bash', str(ROOT / 'app/mac/scripts/build-app.sh'),
            '--version', '1.0.1', '--build', '1.0.1', '--source-sha', source,
            '--source-tree', tree, '--arch', 'arm64', '--web-tool-originals', str(originals),
            '--output-dir', str(out)], area, build_env, 2700)
        if section['commands']['build']['exit_code'] != 0:
            section['build_failure_evidence'] = build_failure_evidence(out)
            if section['commands']['build'].get('census_error'):
                raise CommandFailure(section['commands']['build'])
            raise RuntimeError('documented app build failed; see build command evidence')
        app = out / 'HappyRanch.app'
        binary = app / 'Contents/Resources/daemon/happyranch-daemon'
        if not binary.is_file():
            raise RuntimeError('build did not emit bundled daemon')
        section['bundle_hashes'] = {name: digest(path) for name, path in {
            'app_executable': app / 'Contents/MacOS/HappyRanch',
            'daemon': binary, 'cli': binary.with_name('happyranch')}.items()}
        section['retained_product'] = retain_product(out, Path(os.environ['WORKSPACE']),
            scratch, source, tree, section['bundle_hashes'])
        build_result = section['commands']['build']
        if any(key in build_result for key in ('error', 'census_error', 'cleanup_error')):
            raise CommandFailure(build_result)
        steps['1'] = dict(name=STEP_NAMES[0], status='PASS', source_sha=source, source_tree=tree,
                          signing='unsigned internal; no feed URL supplied or baked by suite')
        active = '2'
        installed = area / 'installed/HappyRanch.app'
        installed.parent.mkdir(mode=0o700)
        section['commands']['install'] = checked(['/usr/bin/ditto', str(app), str(installed)],
                                                  area, build_env, 120)
        binary = installed / 'Contents/Resources/daemon/happyranch-daemon'
        if digest(binary) != section['bundle_hashes']['daemon']:
            raise RuntimeError('installed daemon bytes differ')
        home = area / 'daemon-home'
        home.mkdir(mode=0o700)
        steps['2'] = dict(name=STEP_NAMES[1], status='PASS', app=str(installed), daemon_home=str(home))
        active = '3'
        # Headless Jenkins has no owned interactive AppKit session. This is
        # AppDelegate.launchBundledDaemon's exact executable/argv/cwd/env path.
        daemon_env = {'PATH': env['PATH'], 'HOME': os.environ['HOME'],
            'HAPPYRANCH_DAEMON_HOME': str(home), 'HAPPYRANCH_DAEMON_PORT': '0',
            'HAPPYRANCH_WEB_DIST': str(installed / 'Contents/Resources/web/dist'),
            'TMPDIR': str(area / 'runtime-tmp')}
        section['environment_difference'] = 'Only TMPDIR is added to the app operational environment, for private helper scratch'
        Path(daemon_env['TMPDIR']).mkdir(mode=0o700)
        log = (area / 'daemon.log').open('wb')
        daemon = subprocess.Popen([str(binary), '--reader-guarded-start=online-team-move@3'],
            cwd=home, env=daemon_env, stdin=subprocess.DEVNULL, stdout=log,
            stderr=subprocess.STDOUT, start_new_session=True)
        groups.add(daemon.pid)
        section['initial_daemon_survivors'] = capture_snapshot()
        if section['initial_daemon_survivors']['status'] == 'UNKNOWN':
            raise RuntimeError('initial daemon descendant evidence unknown')
        steps['3'] = dict(name=STEP_NAMES[2], status='PASS', pid=daemon.pid, pgid=daemon.pid,
            mode='bundled daemon fallback', gui=section['gui_launch'], temp_override=daemon_env['TMPDIR'],
            executable=str(binary), argv=['--reader-guarded-start=online-team-move@3'])
        active = '4'
        def ready() -> bool:
            if daemon.poll() is not None:
                raise RuntimeError('bundled daemon exited before health')
            if log.tell() > STREAM_CAP:
                raise RuntimeError('daemon output cap')
            return (home / 'daemon.port').is_file() and (home / 'daemon.token').is_file()
        await_value(ready, 45, 'daemon startup files')
        api = Api(home)
        readiness: dict[str, Any] = {}
        section['health_readiness'] = readiness
        steps['4'] = health_and_web(api, readiness)
        selected_backend = steps['4']['backend']
        section['backend_selection'] = backend_diagnostics(area / 'daemon.log', selected_backend, (api.token,))
        # Refuse before Connect/task launch while the daemon can still be
        # disposed by the mandatory finalizer. Health200 alone promises no tree kill.
        require_tree_backend(section['backend_selection'])
        active = '5'
        runtime = area / 'runtime'
        api.json('/api/v1/runtime', {'path': str(runtime)})
        api.json('/api/v1/orgs', {'slug': 'packaged'})
        authenticated = [name for name, row in feasibility['executors'].items()
                         if row['auth_status'] == 'CREDENTIALS_PRESENT_SESSION_UNTESTED']
        profile = authenticated[0] if authenticated else connect_deterministic(api, scratch)
        real = section['real_executor_session']
        if not authenticated:
            real.update(reason='No authenticated isolated executor: ' + '; '.join(
                f'{name}: path={row["path"]}, auth_exit={row.get("auth_exit_code")}, status={row["auth_status"]}'
                for name, row in feasibility['executors'].items()))
        else:
            real.update(reason='authenticated executor selected; completion pending', executor=profile)
        api.json('/api/v1/orgs/packaged/agents', {'name': 'packaged_agent', 'role': 'worker',
            'team': 'default', 'executor': profile, 'description': 'Disposable packaged observation',
            'system_prompt': 'Run only the supplied disposable packaged observation.', 'repos': {}})
        workspace = runtime / 'orgs/packaged/workspaces/packaged_agent'
        brief = ('In this isolated scratch workspace, start /bin/sleep 180 as a child and write '
                 'packaged-session.json with your pid, child pid, pgid and session_id. '
                 'Wait for operator cancellation. Do nothing else.')
        task = api.json('/api/v1/orgs/packaged/tasks', {'owner': 'packaged_agent', 'brief': brief})['task_id']
        section['task_id'] = task
        marker = workspace / 'packaged-session.json'
        await_value(marker.is_file, 90, 'normal daemon session marker')
        if marker.stat().st_size > 4096:
            raise RuntimeError('session marker cap')
        pin = json.loads(marker.read_text())
        try:
            current = census()
            capture_owned(current)
        except Exception:
            ownership_unknown = True
            raise
        tree_rows = descendants(current, pin['pid'])
        if not {pin['pid'], pin['child']} <= {r['pid'] for r in tree_rows}:
            raise RuntimeError('session parent/child not independently observed')
        daemon_descendants = {r['pid'] for r in descendants(current, daemon.pid)}
        if pin['pid'] not in daemon_descendants:
            raise RuntimeError('session was not spawned by bundled daemon')
        private_groups = private_session_groups(current, pin['pid'], daemon.pid)
        section['session_private_groups'] = sorted(private_groups)
        # Register a real task worktree before the terminal hook runs. No host
        # repo, prior task or remote credentials are used.
        repo = workspace / 'repos/happyranch'
        repo.mkdir(parents=True)
        for argv in (['git', 'init', '-b', 'main', str(repo)],
                     ['git', '-C', str(repo), '-c', 'user.name=Packaged Probe',
                      '-c', 'user.email=probe@example.invalid', 'commit', '--allow-empty', '-m', 'fixture']):
            checked(argv, area, env, 10)
        candidate = repo / '.claude/worktrees' / task
        checked(['git', '-C', str(repo), 'worktree', 'add', '-b', 'task/' + task, str(candidate)], area, env, 10)
        section['registered_worktree'] = str(candidate)
        section['session_before_stop'] = {'marker': pin, 'processes': tree_rows}
        try:
            section['cancel'] = api.json('/api/v1/orgs/packaged/tasks/' + task + '/cancel',
                                        {'rationale': 'Packaged probe owned session stop'})
            await_absence(lambda rows: session_survivors(rows, tree_rows, private_groups),
                          20, 'session tree/group survived cancellation', section, 'post_cancel_survivors')
        finally:
            if section['post_cancel_survivors']['status'] == 'NOT_RUN':
                section['post_cancel_survivors'] = survivor_snapshot(
                    lambda rows: session_survivors(rows, tree_rows, private_groups))
        section['session_after_stop'] = []
        if authenticated:
            real.update(status='PASS', reason='Real authenticated executor session and child observed; cancelled and absent')
        steps['5'] = dict(name=STEP_NAMES[4], status='PASS', executor=profile,
                          session_kind='real' if authenticated else 'credential-free deterministic',
                          before=tree_rows, after=[], task_id=task)
        active = '6'
        # Manual sweep is the shipped frozen entry, including its own worker
        # relaunch. Preview does not claim deletion. The task cancellation also
        # executes the actual terminal worktree cleanup owner in this daemon.
        sweep = command([str(binary), '--cleanup-helper', 'disk-sweep', '--dry-run',
                         '--runtime-root', str(runtime)], area, daemon_env, 120, cap=65536)
        section['commands']['disk_sweep'] = sweep
        snapshot = area / 'worktree-snapshot.json'
        if candidate.exists():
            section['commands']['worktree_safety'] = command([str(binary), '--cleanup-helper',
                'worktree-safety', 'snapshot', str(candidate), str(repo), str(repo),
                str(workspace), str(snapshot)], area, daemon_env, 15, cap=65536)
        oid = checked(['git', '-C', str(repo), 'rev-parse', 'HEAD'], area, env, 5)['tail'].strip()
        git_helper = command([str(binary), '--cleanup-helper', 'repository-git',
            str(repo / '.git'), 'sha1', 'check'], area, daemon_env, 10,
            stdin=(oid + '\n').encode(), cap=4096)
        section['commands']['frozen_git_helper'] = git_helper
        audits = api.json('/api/v1/orgs/packaged/audit?task_id=' + task + '&limit=100')
        finishes = [r for r in audits['entries'] if r['action'] == 'root_worktree_reconciliation_finished']
        payloads = [json.loads(r['payload']) if isinstance(r['payload'], str) else r['payload']
                    for r in finishes]
        outcomes = [outcome for payload in payloads for outcome in payload.get('outcomes', [])]
        section['worktree_cleanup'] = {'finished_audit_ids': [r['id'] for r in finishes],
            'candidate_exists_after': candidate.exists(), 'disk_sweep_mode': 'manual dry-run',
            'outcomes': outcomes}

        if (sweep['exit_code'] != 0 or git_helper['exit_code'] != 0 or
                f'{oid} commit ' not in git_helper['tail'] or not finishes or
                not any(item.get('task_id') == task for item in outcomes) or
                section['commands'].get('worktree_safety', {}).get('exit_code', 0) != 0 or
                any(key in row for row in (sweep, git_helper, section['commands'].get('worktree_safety', {}))
                    for key in ('error', 'census_error', 'cleanup_error'))):
            raise RuntimeError('frozen cleanup/helper failed or terminal cleanup receipt absent')
        steps['6'] = dict(name=STEP_NAMES[5], status='PASS', frozen_git_helper=git_helper,
            terminal_cleanup_audit_ids=[r['id'] for r in finishes], worktree_removed=not candidate.exists(),
            retention='Production cleanup policy may retain; PASS observes execution, not reclamation',
            disk_sweep='executed frozen manual preview; deletion NOT_RUN')
    except Exception as exc:
        steps[active] = dict(name=STEP_NAMES[int(active) - 1], status='FAIL',
                             reason=f'{type(exc).__name__}: {exc}'[:2048])
        if isinstance(exc, CensusError):
            steps[active]['census_error'] = exc.evidence
        if isinstance(exc, CommandFailure):
            key = next((key for key, value in section['commands'].items() if value is exc.result), None)
            if key is None:
                steps[active]['command_result'] = exc.result
            else:
                steps[active]['command_result_key'] = key
        if isinstance(exc, ApiFailure):
            steps[active]['http'] = exc.observation
        if active == '4' and section.get('health_readiness'):
            steps[active]['last_readiness_observation'] = section['health_readiness']
        if daemon is not None:
            section['failure_survivors'] = capture_snapshot()
        raise
    finally:
        if daemon is not None:
            try:
                section['before_quit'] = capture_snapshot()
                daemon.terminate()
                try:
                    daemon.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    daemon.kill()
                    daemon.wait(timeout=2)
                # Match the app's bounded TERM/KILL policy for its owned child.
                # Session groups are observed, never blindly signalled by ps.
                await_absence(select_owned, 10, 'run-owned process census remains after daemon stop',
                              section, 'post_stop_survivors')
                if section['before_quit']['status'] == 'UNKNOWN':
                    raise RuntimeError('pre-stop process evidence unknown')
                if (area / 'daemon.log').stat().st_size > STREAM_CAP:
                    raise RuntimeError('daemon output cap')
                section['daemon_log'] = {'bytes': (area / 'daemon.log').stat().st_size,
                                         'sha256': digest(area / 'daemon.log')}
                section['after_quit'] = []
                steps['7'] = dict(name=STEP_NAMES[6], status='PASS', daemon_exit=daemon.returncode,
                    after=[], mode='bundled daemon TERM/KILL fallback; AppKit Quit NOT_RUN')
            except Exception as exc:
                steps['7'] = dict(name=STEP_NAMES[6], status='FAIL', reason=str(exc)[:512])
            finally:
                if 'post_stop_survivors' not in section:
                    section['post_stop_survivors'] = survivor_snapshot(select_owned)
                if log is not None:
                    log.close()
                section['backend_selection'] = backend_diagnostics(
                    area / 'daemon.log', selected_backend or section.get('health_readiness', {}).get('backend', {}),
                    (api.token,) if api is not None else ())
        steps['8'] = dict(name=STEP_NAMES[7], status='NOT_RUN',
            reason='Outer Jenkins EXIT trap runs after probe.json emission; join identity.txt scratch_removed receipt. Never predict PASS.')
        bound_census_evidence(report)
        if len(json.dumps(report).encode()) > REPORT_CAP:
            raise RuntimeError('packaged report exceeds 60000 bytes')
    if any(steps[str(i)]['status'] != 'PASS' for i in range(1, 8)):
        raise RuntimeError('packaged acceptance incomplete')

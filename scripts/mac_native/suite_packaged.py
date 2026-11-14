"""One scratch-only packaged build and bounded frozen-runtime observation.

No runtime imports: every product operation crosses the installed bundle's
executable or HTTP boundary. Native execution needs separate job authorization.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
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


def initial_steps(reason: str = 'not reached') -> dict[str, dict[str, Any]]:
    return {str(i): {'name': name, 'status': 'NOT_RUN', 'reason': reason}
            for i, name in enumerate(STEP_NAMES, 1)}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


def command(argv: list[str], cwd: Path, env: dict[str, str], seconds: float,
            *, stdin: bytes = b'', cap: int = STREAM_CAP) -> dict[str, Any]:
    """Bound both elapsed time and retained output, kill/reap the owned group.

    Only build/fixture/helper commands use this runner; authentication responses
    and daemon output never enter this report.
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
    try:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(stdin)
        proc.stdin.close()
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            while selector.get_map():
                if time.monotonic() >= next_sample:
                    observed.update((row['pid'], row['pgid']) for row in descendants(census(), proc.pid))
                    if len(observed) > 8192:
                        raise RuntimeError('command process identity cap')
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
        code = proc.wait(timeout=max(.001, end - time.monotonic()))
        return {'exit_code': code, 'bytes': total, 'sha256': sha.hexdigest(),
                'tail': data.decode('utf-8', errors='replace')}
    except (OSError, RuntimeError, TimeoutError, subprocess.TimeoutExpired) as exc:
        return {'exit_code': None, 'bytes': total, 'sha256': sha.hexdigest(),
                'tail': data.decode('utf-8', errors='replace'),
                'error': type(exc).__name__ + ': ' + str(exc)}
    finally:
        # A frozen helper may start its own worker session. Signal only groups
        # witnessed under this command, with a still-matching member, never a
        # process selected merely by a scratch-looking command name.
        try:
            current = census()
            observed.update((row['pid'], row['pgid']) for row in descendants(current, proc.pid))
            alive = {(row['pid'], row['pgid']) for row in current}
            for group in {group for pid, group in observed if (pid, group) in alive}:
                if group != os.getpgrp():
                    try:
                        os.killpg(group, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=5)
            proc.stdout.close()



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
    if row['exit_code'] != 0:
        raise RuntimeError(f'command failed exit={row["exit_code"]}: {row["tail"][-1024:]}')
    return row


def parse_census(raw: str) -> list[dict[str, Any]]:
    rows = []
    for line in raw.splitlines():
        values = line.split(None, 3)
        if len(values) != 4 or not all(v.isdecimal() for v in values[:3]):
            raise ValueError('incomplete process census')
        rows.append(dict(zip(('pid', 'ppid', 'pgid', 'executable'),
                             [*map(int, values[:3]), values[3]], strict=True)))
    if not rows or len(rows) > 8192:
        raise ValueError('process census empty/row cap')
    return rows


def census() -> list[dict[str, Any]]:
    # comm omits argv: no arbitrary host command arguments/credentials collected.
    result = subprocess.run(['/bin/ps', '-axo', 'pid=,ppid=,pgid=,comm='],
                            capture_output=True, timeout=5, check=True)
    if len(result.stdout) > 1024 * 1024:
        raise RuntimeError('process census byte cap')
    return parse_census(result.stdout.decode('utf-8'))


def descendants(rows: list[dict[str, Any]], root: int) -> list[dict[str, int]]:
    selected = {root}
    for _ in range(len(rows)):
        added = {row['pid'] for row in rows if row['ppid'] in selected} - selected
        if not added:
            break
        selected.update(added)
    return [{key: row[key] for key in ('pid', 'ppid', 'pgid')}
            for row in rows if row['pid'] in selected]


def owned_rows(rows: list[dict[str, Any]], scratch: Path,
               groups: set[int], excluded: set[int]) -> list[dict[str, int]]:
    return [{key: row[key] for key in ('pid', 'ppid', 'pgid')}
            for row in rows if row['pid'] not in excluded and
            (row['pgid'] in groups or row['executable'].startswith(str(scratch) + '/'))]


def await_value(fn: Any, seconds: float, label: str) -> Any:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = fn()
        if value:
            return value
        time.sleep(.25)
    raise TimeoutError(label)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class Api:
    def __init__(self, home: Path):
        self.base = 'http://127.0.0.1:' + str(int((home / 'daemon.port').read_text()))
        self.token = (home / 'daemon.token').read_text().strip()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def get_bytes(self, path: str, *, limit: int = 1024 * 1024,
                  body: dict | None = None, token: str | None = None) -> bytes:
        request = urllib.request.Request(self.base + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Authorization': 'Bearer ' + (self.token if token is None else token),
                     'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=45 if body is not None else 5) as response:
                raw = response.read(limit + 1)
                if len(raw) > limit:
                    raise RuntimeError('HTTP response cap')
                return raw
        except urllib.error.HTTPError as exc:
            # Bodies for token/profile endpoints must never enter diagnostics.
            raise RuntimeError(f'packaged HTTP {exc.code}: {path.split("?")[0]}') from None

    def json(self, path: str, body: dict | None = None, *, token: str | None = None) -> Any:
        return json.loads(self.get_bytes(path, body=body, token=token))


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
    daemon_env: dict[str, str] = {}
    before = census()
    ancestors = {os.getpid()}
    for _ in range(len(before)):
        parents = {r['ppid'] for r in before if r['pid'] in ancestors}
        if parents <= ancestors:
            break
        ancestors.update(parents)
    groups: set[int] = set()
    active = '1'
    try:
        source = checked(['git', 'rev-parse', 'HEAD'], ROOT, dict(os.environ), 5)['tail'].strip()
        if not re.fullmatch('[a-f0-9]{40}', source) or source != section['source_sha']:
            raise RuntimeError('SOURCE_SHA does not match exact checked out commit')
        tree = checked(['git', 'rev-parse', 'HEAD^{tree}'], ROOT, dict(os.environ), 5)['tail'].strip()
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
            raise RuntimeError('documented app build failed; see build command evidence')
        app = out / 'HappyRanch.app'
        binary = app / 'Contents/Resources/daemon/happyranch-daemon'
        if not binary.is_file():
            raise RuntimeError('build did not emit bundled daemon')
        section['bundle_hashes'] = {name: digest(path) for name, path in {
            'app_executable': app / 'Contents/MacOS/HappyRanch',
            'daemon': binary, 'cli': binary.with_name('happyranch')}.items()}
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
        def healthy() -> Any:
            try:
                value = api.json('/health')
                return value if value.get('status') == 'ok' else None
            except (OSError, RuntimeError):
                return None
        health = await_value(healthy, 45, 'daemon health endpoint')
        html = api.get_bytes('/')
        match = re.search(rb'(?:src|href)=["\'](/assets/[^"\']+\.(?:js|css))["\']', html)
        if not match or b'<html' not in html.lower():
            raise RuntimeError('served root is not packaged UI HTML with asset')
        asset_path = match[1].decode('ascii')
        asset = api.get_bytes(asset_path, limit=4 * 1024 * 1024)
        if not asset:
            raise RuntimeError('empty packaged web asset')
        steps['4'] = dict(name=STEP_NAMES[3], status='PASS', health_status=health.get('status'),
            root_bytes=len(html), root_sha256=hashlib.sha256(html).hexdigest(), asset=asset_path,
            asset_bytes=len(asset), asset_sha256=hashlib.sha256(asset).hexdigest(), browser='NOT_RUN: HTTP only')
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
        current = census()
        tree_rows = descendants(current, pin['pid'])
        if not {pin['pid'], pin['child']} <= {r['pid'] for r in tree_rows}:
            raise RuntimeError('session parent/child not independently observed')
        daemon_descendants = {r['pid'] for r in descendants(current, daemon.pid)}
        if pin['pid'] not in daemon_descendants:
            raise RuntimeError('session was not spawned by bundled daemon')
        groups.update(r['pgid'] for r in tree_rows)
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
        section['cancel'] = api.json('/api/v1/orgs/packaged/tasks/' + task + '/cancel',
                                    {'rationale': 'Packaged probe owned session stop'})
        ids = {r['pid'] for r in tree_rows}
        await_value(lambda: not any(r['pid'] in ids or r['pgid'] in {x['pgid'] for x in tree_rows}
                                    for r in census()), 20, 'session tree/group survived cancellation')
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
                section['commands'].get('worktree_safety', {}).get('exit_code', 0) != 0):
            raise RuntimeError('frozen cleanup/helper failed or terminal cleanup receipt absent')
        steps['6'] = dict(name=STEP_NAMES[5], status='PASS', frozen_git_helper=git_helper,
            terminal_cleanup_audit_ids=[r['id'] for r in finishes], worktree_removed=not candidate.exists(),
            retention='Production cleanup policy may retain; PASS observes execution, not reclamation',
            disk_sweep='executed frozen manual preview; deletion NOT_RUN')
    except Exception as exc:
        steps[active] = dict(name=STEP_NAMES[int(active) - 1], status='FAIL',
                             reason=f'{type(exc).__name__}: {exc}'[:2048])
        raise
    finally:
        if daemon is not None:
            try:
                section['before_quit'] = owned_rows(census(), scratch, groups, ancestors)
                daemon.terminate()
                try:
                    daemon.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    daemon.kill()
                    daemon.wait(timeout=2)
                # Match the app's bounded TERM/KILL policy for its owned child.
                # Session groups are observed, never blindly signalled by ps.
                await_value(lambda: not owned_rows(census(), scratch, groups, ancestors),
                            10, 'run-owned process census remains after daemon stop')
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
                if log is not None:
                    log.close()
        steps['8'] = dict(name=STEP_NAMES[7], status='NOT_RUN',
            reason='Outer Jenkins EXIT trap runs after probe.json emission; join identity.txt scratch_removed receipt. Never predict PASS.')
        if len(json.dumps(report).encode()) > REPORT_CAP:
            raise RuntimeError('packaged report exceeds 60000 bytes')
    if any(steps[str(i)]['status'] != 'PASS' for i in range(1, 8)):
        raise RuntimeError('packaged acceptance incomplete')

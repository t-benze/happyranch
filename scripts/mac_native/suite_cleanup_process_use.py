"""S4 shared native cleanup observations; PR2 extends this same suite.

Only stdlib imports at module scope. --fixtures runs portable shipping-code
fixtures; normal dispatch is Darwin-only. No live org or cleanup runner action.
"""
from __future__ import annotations

import argparse
import ast
import ctypes as ct
import ctypes.util
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'runtime/skills/bundled/workspace-cleanup/scripts'
CAP = 65536


def load(name: str, filename: Path):
    cached = sys.modules.get(name)
    if cached is not None:
        if Path(cached.__file__).resolve() != filename.resolve():
            raise RuntimeError('unexpected module origin:' + name)
        return cached
    spec = importlib.util.spec_from_file_location(name, filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[name]
        raise
    return module


def check(rows: list, name: str, expected, actual, provenance='shipping-code fixture') -> None:
    rows.append({'name': name, 'expected': expected, 'actual': actual,
                 'status': 'PASS' if expected == actual else 'FAIL', 'provenance': provenance})


def command(argv: list[str], cwd: Path, deadline: float, *, env=None) -> tuple[int, str, str]:
    # File-backed output; never unbounded PIPE accumulation. Fixture children
    # cannot inherit streams. Every caller uses the original suite deadline.
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = subprocess.Popen(argv, cwd=cwd, stdout=out, stderr=err,
                                   stdin=subprocess.DEVNULL, env=env, start_new_session=True)
        try:
            process.wait(timeout=max(.01, min(30, deadline - time.monotonic())))
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=2)
        out.seek(0)
        err.seek(0)
        stdout, stderr = out.read(CAP + 1), err.read(CAP + 1)
    if len(stdout) > CAP or len(stderr) > CAP:
        raise RuntimeError('command output cap')
    return process.returncode, stdout.decode(), stderr.decode()


def portable(rows: list, scratch: Path, deadline: float) -> None:
    """Execute real embedded mount functions; no parser copied into tests."""
    import re
    runner = (SCRIPTS / 'run_cleanup_candidate.sh').read_text()
    heredocs = runner.split("<<'PY'\n")[1:]
    sources = [value.split('\nPY\n', 1)[0] for value in heredocs]
    functions = []
    for source in sources:
        for node in ast.parse(source).body:
            if isinstance(node, ast.FunctionDef) and node.name == 'mountpoints':
                functions.append(node)
    safety = ast.parse((SCRIPTS / 'worktree_safety.py').read_text())
    functions.extend(node for node in ast.walk(safety)
                     if isinstance(node, ast.FunctionDef) and node.name == 'mountpoints')
    if len(functions) != 3:
        raise RuntimeError('fixture discovery did not locate all three shipping mount parsers')
    root = '/dev/disk1 on / (apfs, local, read-only)\n'
    fixtures = [('bsd_mount', root, {'/'}),
                ('mount_spaces', root + '/dev/disk2 on /Volumes/Fixture Space (apfs, local)\n',
                 {'/', '/Volumes/Fixture Space'}),
                ('mount_empty', '', None), ('mount_malformed', root + 'broken\n', None),
                ('mount_relative', 'disk on relative (apfs)\n', None),
                ('mount_truncated', root + 'disk on /Volumes/Bad (apfs\n', None)]
    for number, function in enumerate(functions):
        for name, output, expected in fixtures:
            def no_proc(*args, **kwargs):
                raise FileNotFoundError('/proc/self/mountinfo')
            receipt = subprocess.CompletedProcess(['mount'], 0, output, '')
            globals_ = {'os': os, 're': re, 'subprocess': SimpleNamespace(run=lambda *a, **k: receipt),
                        'open': no_proc, 'primary': str(scratch), 'budget': SimpleNamespace(command=lambda *a: receipt)}
            exec(compile(ast.Module(body=[function], type_ignores=[]), '<shipping mountpoints>', 'exec'), globals_)
            try:
                actual = sorted(globals_['mountpoints']())
            except (ValueError, RuntimeError, re.error):
                actual = None
            check(rows, f'{name}_{number}', sorted(expected) if expected else None, actual)
    # Source only the definitions; sourcing does not enter run_cleanup_candidate.
    folder = scratch / 'skill with spaces'
    (folder / 'scripts').mkdir(parents=True)
    copy = folder / 'scripts/run_cleanup_candidate.sh'
    copy.write_text(runner)
    tools = scratch / 'path'
    tools.mkdir()
    (tools / 'python3').symlink_to(sys.executable)
    code, output, _ = command(['/bin/bash', '-c', 'source "$1"; printf "%s" "$_WC_RUNNER_SKILL_DIR"',
                              'fixture', str(copy)], scratch, deadline,
                             env={**os.environ, 'PATH': str(tools)})
    check(rows, 'self_location_no_realpath', [0, str(folder)], [code, output])
    sys.path.insert(0, str(ROOT))
    from runtime.platform import process_census as census
    native = load('darwin_process', SCRIPTS / 'darwin_process.py')
    check(rows, 'shared_libproc_identity', True, census._LibProc is native._LibProc)
    check(rows, 'shared_exception_identity', True,
          census.LibProcUnavailable is native.LibProcUnavailable
          and census.ProcessObservationUnavailable is native.ProcessObservationUnavailable)
    check(rows, 'shared_checked_state', True,
          census._checked_read is native._checked_read
          and census._checked_observation is native._checked_observation)
    check(rows, 'abi_offsets', {'sizes': [136, 152, 1176, 24, 1200, 2352, 8, 112, 1288, 96, 1272],
          'offsets': [152, 24, 1176, 112, 96, 80, 88]}, native.abi_layout())
    # Only the libc call boundary is synthetic; production read mapping runs.
    reader = native.DarwinReader.__new__(native.DarwinReader)
    reader.admit = lambda: None
    for name, count, code, variable, expected in (
        ('simulated_eperm', 0, errno.EPERM, False, 'denied'),
        ('simulated_esrch', 0, errno.ESRCH, False, 'gone'),
        ('short_read', 4, 0, False, 'short_read'),
        ('population_cap', 8, 0, True, 'capped')):
        def call(*args):
            ct.set_errno(code)
            return count
        result = reader.call(call, (), ct.c_uint64(), variable=variable)
        check(rows, name, [expected, code], [result.kind, result.errno], 'simulated ctypes return/errno')
    binding = native._LibProc.__new__(native._LibProc)
    binding._ctypes = ct
    binding._ProcBsdinfo = ct.c_uint64
    def denied(*args):
        ct.set_errno(errno.EACCES)
        return 0
    binding._lib = SimpleNamespace(proc_pidinfo=denied)
    try:
        with census._checked_observation():
            binding.bsd_info(os.getpid())
        checked = False
    except census.ProcessObservationUnavailable:
        checked = True
    check(rows, 'shared_checked_denial', True, checked, 'simulated ctypes EACCES')
    with patch.object(sys, 'platform', 'darwin'), patch.object(ctypes.util, 'find_library', return_value='fixture-libproc'), patch.object(ct, 'CDLL', side_effect=OSError('simulated unavailable')):
        try:
            native._LibProc()
            unavailable = False
        except native.LibProcUnavailable:
            unavailable = True
    check(rows, 'loader_unavailable', True, unavailable, 'simulated CDLL failure')
    scanner = load('s4_check_path_use', SCRIPTS / 'check_path_use.py')
    exhausted = native.scan_paths({str(scratch): (scratch.stat().st_dev, scratch.stat().st_ino)},
                                  scanner.Bounds(deadline_seconds=.1), agent_uid=os.getuid(), clock=lambda: 1., started=0.)
    check(rows, 'deadline', ['unknown', True], [exhausted['state'], any('deadline' in reason for reason in exhausted['reasons'])])
    simulated_scans(rows, scratch)
    value = scratch.stat()
    coverage = {'platform': 'darwin', 'agent_uid': 12345, 'self_pid': str(os.getpid()),
        'boot_uuid': '11111111-2222-3333-4444-555555555555', 'abi_validated': True,
        'population_complete': True, 'total_pids': 1, 'same_user': 1, 'root': 0,
        'other_user': 0, 'scanned': 1, 'enum_passes': 2,
        'lanes': dict.fromkeys(('cwd', 'root', 'fd', 'text', 'thread', 'region'), True),
        'target_dev_ino': [value.st_dev, value.st_ino], 'containing_worktree': None,
        'containing_worktree_dev_ino': None}
    valid = {'state': 'clear_observation', 'target': str(scratch), 'hits': [], 'reasons': [],
        'exempt': [], 'coverage': coverage, 'cycles': [
            {'phase': 'enumerate_pass', 'pass': 1, 'new': 1},
            {'phase': 'enumerate_pass', 'pass': 2, 'new': 0}]}
    for name, change, expected in (
        ('darwin_receipt_accept', {}, True),
        ('receipt_wrong_platform', {'platform': 'linux'}, False),
        ('receipt_missing_lane', {'lanes': dict.fromkeys(('cwd', 'root', 'fd', 'text', 'thread'), True)}, False),
        ('receipt_incomplete_lane', {'lanes': dict(coverage['lanes'], region=False)}, False),
        ('receipt_boolean_counter', {'same_user': True}, False)):
        candidate = dict(valid, coverage=dict(coverage, **change))
        kernel = SimulatedKernel(native, scratch, '')
        with patch.object(sys, 'platform', 'darwin'), patch.object(ct, 'CDLL', return_value=kernel), patch.object(os, 'getuid', return_value=12345):
            try:
                native.validate_clear_scan(candidate, str(scratch), str(scratch))
                accepted = True
            except (ValueError, KeyError):
                accepted = False
        check(rows, name, expected, accepted, 'Darwin schema only; simulated sysctl UUID; no authenticated job claim')



class NativeFunction:
    def __init__(self, function):
        self.function = function

    def __call__(self, *args):
        return self.function(*args)


class SimulatedKernel:
    """Only ctypes syscall buffers/errno are simulated, never scanner decisions."""

    def __init__(self, native, path: Path, mode: str):
        self.native, self.path, self.mode = native, path, mode
        self.identity_reads, self.fd_tables = 0, 0
        self.proc_listpids = NativeFunction(self.population)
        self.proc_pidinfo = NativeFunction(self.pidinfo)
        self.proc_pidfdinfo = NativeFunction(self.fdinfo)
        self.proc_pidpath = NativeFunction(self.pidpath)
        self.sysctlbyname = NativeFunction(self.boot)

    def vnode(self, node, path: Path, *, label=None):
        value = path.stat()
        node.info.vi_stat.dev, node.info.vi_stat.ino = value.st_dev, value.st_ino
        node.info.vi_stat.mode = value.st_mode
        node.info.vi_type = 2 if path.is_dir() else 1
        node.path = os.fsencode(path if label is None else label)

    def population(self, kind, info, pointer, size):
        pointer._obj[0] = os.getpid()
        return size if self.mode == 'population_cap_scan' else ct.sizeof(ct.c_int)

    def pidinfo(self, pid, flavor, arg, pointer, size):
        value = pointer._obj
        if flavor == 3:
            self.identity_reads += 1
            if self.mode == 'short_identity_scan' and self.identity_reads > 1:
                return 4
            value.pbi_pid, value.pbi_ppid = pid, os.getppid()
            value.pbi_start_tvsec = 123 + int(self.mode == 'reuse' and self.identity_reads > 2)
            value.pbi_status = 2
            value.pbi_uid = value.pbi_ruid = value.pbi_svuid = 12345
        elif flavor == 9:
            self.vnode(value.cwd, Path.cwd())
        elif flavor == 1:
            self.fd_tables += 1
            if self.mode in ('positive_precedence', 'alias_or_renamed_reference', 'alias_or_renamed_reference_empty_path', 'denied_kept'):
                value[0].fd, value[0].kind = 99999, 1
                return ct.sizeof(self.native.FdInfo)
            if self.mode == 'fd_churn' and self.fd_tables == 1:
                value[0].fd, value[0].kind = 99999, 2
                return ct.sizeof(self.native.FdInfo)
            return 0
        elif flavor == 6:
            value[0] = 1
            return 8
        elif flavor == 8:
            if arg:
                ct.set_errno(errno.EINVAL)
                return 0
            value.region.address, value.region.size = 0, 4096
        return size

    def fdinfo(self, pid, fd, flavor, pointer, size):
        if fd == 99999:
            if self.mode == 'denied_kept':
                ct.set_errno(errno.EPERM)
                return 0
            self.vnode(pointer._obj.vnode, self.path,
                       label=('/no-longer-resolvable/held.bin' if self.mode == 'alias_or_renamed_reference'
                              else '' if self.mode == 'alias_or_renamed_reference_empty_path' else None))
        else:
            self.vnode(pointer._obj.vnode, Path.cwd())
        return size

    def pidpath(self, pid, pointer, size):
        path = os.fsencode(sys.executable)
        ct.memmove(pointer, path + b'\0', len(path) + 1)
        return len(path)

    def boot(self, name, pointer, size, new, newsize):
        text = b'11111111-2222-3333-4444-555555555555\0'
        ct.memmove(pointer, text, len(text))
        size._obj.value = len(text)
        return 0


def simulated_mount(arguments):
    arguments['stdout'].write(b'/dev/fixture on / (apfs, local)\n')
    return SimpleNamespace(returncode=0)


def simulated_scans(rows: list, scratch: Path) -> None:
    import ctypes.util
    native = load('darwin_process', SCRIPTS / 'darwin_process.py')
    scanner = load('s4_check_path_use', SCRIPTS / 'check_path_use.py')
    candidate = scratch / 'simulated-candidate'
    candidate.mkdir()
    held = candidate / 'held.bin'
    held.write_bytes(b'fixture')
    identity = candidate.stat()
    for name, state, cause in (
        ('positive_precedence', 'blocked', 'public_region_negative'),
        ('alias_or_renamed_reference', 'blocked', 'public_region_negative'),
        ('alias_or_renamed_reference_empty_path', 'blocked', 'incomplete vnode path'),
        ('denied_kept', 'unknown', 'denied'),
        ('reuse', 'unknown', 'identity_changed'),
        ('short_identity_scan', 'unknown', 'short_read'),
        ('fd_churn', 'unknown', 'fd_churn'),
        ('population_cap_scan', 'unknown', 'capped')):
        kernel = SimulatedKernel(native, held, name)
        with patch.object(sys, 'platform', 'darwin'), patch.object(ct, 'CDLL', return_value=kernel), patch.object(ctypes.util, 'find_library', return_value='fixture-libproc'), patch.object(subprocess, 'run', side_effect=lambda *a, **k: simulated_mount(k)):
            result = native.scan_paths({str(candidate): (identity.st_dev, identity.st_ino)},
                                        scanner.Bounds(), agent_uid=12345)
        expected, actual = [state, True], [result['state'], any(cause in item for item in result['reasons'])]
        if name.startswith('alias_or_renamed_reference'):
            expected.append(True)
            actual.append(any(hit['kind'] == 'fd' and hit['dev_ino'] == [held.stat().st_dev, held.stat().st_ino]
                              and hit['candidate_path'] == str(held)
                              and hit['path'] == ('' if name.endswith('_empty_path') else '/no-longer-resolvable/held.bin')
                              for hit in result['hits']))
        check(rows, name, expected, actual,
              'simulated libproc/sysctl buffers and errno; actual scanner')


CHILD = r'''
import ctypes, json, mmap, os, sys
from pathlib import Path
kind, folder = sys.argv[1:]
path = Path(folder) / 'held.bin'
handle = mapping = None
if kind == 'cwd':
    os.chdir(folder)
elif kind in ('fd', 'region'):
    handle = path.open('r+b')
    identity = list((os.fstat(handle.fileno()).st_dev, os.fstat(handle.fileno()).st_ino))
    if kind == 'region':
        mapping = mmap.mmap(handle.fileno(), 0, trackfd=False)
        handle.close()
        handle = None
elif kind == 'thread':
    libc = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    call = libc.pthread_chdir_np
    call.argtypes = [ctypes.c_char_p]
    call.restype = ctypes.c_int
    if call(os.fsencode(folder)) != 0:
        raise OSError(ctypes.get_errno(), 'pthread_chdir_np')
print(json.dumps({'pid': os.getpid(), 'cwd': os.getcwd(), 'kind': kind,
                  'dev_ino': identity if kind in ('fd','region') else
                    [os.stat('.').st_dev, os.stat('.').st_ino]}), flush=True)
sys.stdin.buffer.read(1)
if mapping is not None: mapping.close()
if handle is not None: handle.close()
'''


def ready(process, deadline: float) -> dict:
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        if not selector.select(max(.01, min(5, deadline - time.monotonic()))):
            raise RuntimeError('child readiness timeout')
        line = process.stdout.readline(4097)
    if len(line) > 4096 or not line.endswith(b'\n'):
        raise RuntimeError('child readiness malformed/capped')
    value = json.loads(line)
    if value['pid'] != process.pid:
        raise RuntimeError('child readiness PID mismatch')
    return value


def run(report: dict) -> None:
    if sys.platform != 'darwin':
        raise RuntimeError('use probe dispatcher for Linux skip')
    if not os.environ.get('TMPDIR'):
        raise RuntimeError('job-owned TMPDIR required')
    started = time.monotonic()
    deadline = started + 230  # Original 240s including reserved cleanup window.
    checks, children = [], []
    report['cleanup_process_use'] = {'checks': checks, 'frozen_app': 'NOT RUN',
        'runner_idle_eligible': 'NOT PROVEN; read-only gates only',
        'scratch_routing': 'PR2; NOT RUN', 'real_eperm': 'NOT OBSERVED'}
    detail = report['cleanup_process_use']
    source_code, source_sha, _ = command(['git', 'rev-parse', 'HEAD'], ROOT, deadline)
    if source_code or len(source_sha.strip()) != 40:
        raise RuntimeError('source SHA unavailable')
    detail['source_sha'] = source_sha.strip()
    detail['source_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in SCRIPTS.iterdir() if p.suffix in ('.py', '.sh')}
    old_handler = signal.getsignal(signal.SIGALRM)
    def expired(signum, frame):
        raise TimeoutError('cleanup-process-use original suite deadline')
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, max(.01, deadline - time.monotonic()))
    scratch = Path(tempfile.mkdtemp(prefix='cleanup-process-use-', dir=os.environ['TMPDIR'])).resolve(strict=True)
    cleaned = False
    try:
        portable(checks, scratch, deadline)
        workspace = scratch / 'workspace'
        primary = workspace / 'repos/sample'
        primary.mkdir(parents=True)
        for args in (['init', '--initial-branch=main'], ['config', 'user.name', 'Native Probe'],
                     ['config', 'user.email', 'native@example.invalid']):
            code, _, _ = command(['git', *args], primary, deadline)
            if code:
                raise RuntimeError('fixture Git setup failed')
        (primary / 'held.bin').write_bytes(b'fixture\n' * 1024)
        for args in (['add', 'held.bin'], ['commit', '-m', 'fixture']):
            if command(['git', *args], primary, deadline)[0]:
                raise RuntimeError('fixture Git commit failed')
        candidates = {}
        for index, kind in enumerate(('cwd', 'fd', 'region', 'thread', 'idle'), 1):
            path = primary / '.claude/worktrees' / f'TASK-{index}'
            if command(['git', 'worktree', 'add', '-b', f'task/TASK-{index}', str(path)], primary, deadline)[0]:
                raise RuntimeError('fixture worktree failed')
            candidates[kind] = path
        native = load('darwin_process', SCRIPTS / 'darwin_process.py')
        reader = native.DarwinReader(lambda: None)
        detail['abi'] = native.abi_layout()
        detail['boot_uuid'] = native.boot_uuid()
        grounds = {}
        for kind in ('cwd', 'fd', 'region', 'thread'):
            process = subprocess.Popen([sys.executable, '-I', '-B', '-c', CHILD, kind, str(candidates[kind])],
                cwd=scratch, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                start_new_session=True)
            children.append(process)
            ground = ready(process, deadline)
            ground['start_identity'] = reader.identity(process.pid)[1]
            if kind in ('fd', 'region'):
                value = (candidates[kind] / 'held.bin').stat()
                check(checks, kind + '_ground_inode', [value.st_dev, value.st_ino], ground['dev_ino'], 'owned child fstat')
            if kind == 'region':
                # Independent native fd census before the scanner: mmap must
                # retain only its region reference, never an mmap-owned dup fd.
                remaining = []
                for row in reader.table(process.pid, 1, native.FdInfo, 4096, empty=True):
                    if row.kind == 1:
                        vnode = reader.fd_path(process.pid, row.fd).vnode.info.vi_stat
                        if (vnode.dev, vnode.ino) == (value.st_dev, value.st_ino):
                            remaining.append(int(row.fd))
                ground['held_file_fds'] = remaining
                check(checks, 'region_no_held_file_fd', [], remaining, 'independent native fd census before scanner')
                if remaining:
                    raise RuntimeError('region fixture still holds target fd')
            grounds[kind] = ground
        detail['ground_truth'] = grounds
        for kind, path in candidates.items():
            code, stdout, stderr = command([sys.executable, '-I', '-B', str(SCRIPTS / 'check_path_use.py'),
                '--target', str(path), '--containing-worktree', str(path), '--deadline-seconds', '20', '--json'], scratch, deadline)
            value = json.loads(stdout)
            detail[kind] = {'state': value['state'], 'exit': code,
                'hits': value['hits'][:4], 'reasons': value['reasons'][:4],
                'lanes': value['coverage'].get('lanes'), 'stderr': stderr[:512]}
            denied = [reason for reason in value['reasons'] if ':errno=' in reason
                      and reason.rsplit(':errno=', 1)[-1] in ('1', '13')]
            if denied:
                detail['real_eperm'] = {'state': value['state'], 'causes': denied[:4], 'provenance': 'native kernel errno'}
            if kind != 'idle':
                expected_kind = 'region' if kind == 'region' else kind
                matching = any(hit['pid'] == str(grounds[kind]['pid']) and hit['kind'] == expected_kind for hit in value['hits'])
                check(checks, kind + '_held', ['blocked', 3, True], [value['state'], code, matching], 'shipping CLI + native child')
            else:
                # Conservative negative is an explicit approved outcome, not idle eligibility.
                valid = ((value['state'] == 'unknown' and code == 2 and bool(value['reasons']))
                         or (value['state'] == 'clear_observation' and code == 0
                             and all(value['coverage']['lanes'].values())))
                check(checks, 'idle_clear_or_unknown_retain', True, valid, 'shipping CLI; gate-specific only')
            check(checks, kind + '_preserved', True, (path / 'held.bin').read_bytes() == b'fixture\n' * 1024)
            dest = scratch / (kind + '-snapshot.json')
            snap_code, _, _ = command([sys.executable, '-I', '-B', str(SCRIPTS / 'worktree_safety.py'),
                'snapshot', str(path), str(path), str(primary), str(workspace), str(dest)], scratch, deadline)
            check(checks, kind + '_snapshot', 0, snap_code, 'shipping snapshot CLI; no mutation')
        # Preservation needs authentic remote history; this offline fixture must refuse.
        code, stdout, _ = command([sys.executable, '-I', '-B', str(SCRIPTS / 'worktree_safety.py'),
            'preserved', str(candidates['idle']), str(primary), 'TASK-5'], scratch, deadline)
        check(checks, 'preservation_without_remote_retains', [2, True],
              [code, json.loads(stdout).get('refused')], 'shipping preserved CLI, offline gate refusal')
        # Missing binding must refuse before owner admission; no live task
        # identity is supplied. Collect real Darwin host facts separately.
        manifest, history, output = scratch / 'manifest.json', scratch / 'history.json', scratch / 'observation'
        manifest.write_text('[]')
        history.write_text('{}')
        code, stdout, _ = command([sys.executable, '-I', '-B', '-c',
            'import runpy,sys; sys.path.insert(0,sys.argv[1]); sys.argv=sys.argv[2:]; runpy.run_path(sys.argv[0],run_name="__main__")',
            str(SCRIPTS), str(SCRIPTS / 'observe_cleanup_batch.py'), 'observe', '--phase', 'before', '--manifest', str(manifest),
            '--history-exclusions', str(history), '--evidence-dir', str(output)], scratch, deadline,
            env={key: value for key, value in os.environ.items()
                 if key not in ('WORKSPACE', 'PRIMARY', 'ORG', 'AGENT', 'ACTING_TASK', 'SESSION_ID', 'CLEANUP_MARKER')})
        observed = json.loads((output / 'before.json').read_text())
        check(checks, 'observer_native_host', ['darwin', True, True],
              [observed['context'].get('platform'), observed['context']['complete'],
               bool(observed['context']['mounts'])], 'shipping read-only observer')
        check(checks, 'observer_missing_binding_retains', [2, False, ['scope_or_marker_unset'], False, False],
              [code, observed['complete'], observed['errors'][len(observed['context']['errors']):],
               'binding' in observed, 'owner_receipt' in observed], 'shipping observer; exact pre-owner binding refusal')
        check(checks, 'observer_without_findmnt', True,
              'findmnt' not in observed['context'] and 'reads' not in observed['context'])
        sys.path.insert(0, str(SCRIPTS))
        observer = load('observe_cleanup_batch', SCRIPTS / 'observe_cleanup_batch.py')
        source = scratch / 'history-source.json'
        source.write_text('{}')
        history.write_text(json.dumps({'complete': True, 'sources': [{'path': str(source),
            'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}], 'excluded_paths': []}))
        home = scratch / 'home'
        home.mkdir()
        with patch.dict(os.environ, {'HOME': str(home)}):
            protected = observer.capture_protected({'WORKSPACE': str(workspace), 'PRIMARY': str(primary)},
                [], history, observed['context'])
        check(checks, 'protected_without_findmnt', [True, True, True],
              [protected['complete'], 'findmnt' not in protected,
               protected.get('mount_evidence') == observed['context']['mounts']],
              'shipping protected metadata; no authenticated-owner claim')
        detail['protected_errors'] = protected['errors'][:4]

    finally:
        # Cleanup retains its reserved 10s, even after the observation alarm.
        signal.setitimer(signal.ITIMER_REAL, 0)
        cleanup_deadline = started + 240
        failures = []
        for process in children:
            try:
                process.stdin.write(b'x')
                process.stdin.flush()
            except (OSError, ValueError):
                pass
        for process in children:
            try:
                process.wait(timeout=max(.01, min(1, cleanup_deadline - time.monotonic())))
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=max(.01, min(1, cleanup_deadline - time.monotonic())))
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=max(.01, cleanup_deadline - time.monotonic()))
            for stream in (process.stdin, process.stdout):
                stream.close()
            try:
                os.killpg(process.pid, 0)
                failures.append(process.pid)
            except ProcessLookupError:
                pass
        detail['child_cleanup'] = {'reaped': len(children), 'group_residue': failures}
        signal.signal(signal.SIGALRM, old_handler)
        if not failures:
            import shutil
            shutil.rmtree(scratch)
            cleaned = not scratch.exists()
        detail['fixture_removed'] = cleaned
        if failures or not cleaned:
            raise RuntimeError('owned child/fixture cleanup incomplete')
    if len((json.dumps(report, indent=2, sort_keys=True) + '\n').encode()) > 32768:
        report['cleanup_process_use'] = {'report_capped': True, 'evidence': 'INCOMPLETE',
                                         'child_cleanup': detail.get('child_cleanup'), 'fixture_removed': cleaned}
        raise RuntimeError('cleanup-process-use report exceeds 32KiB')
    if any(row['status'] != 'PASS' for row in checks):
        raise AssertionError('cleanup-process-use contract failed; see named checks')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', action='store_true', required=True)
    args = parser.parse_args()
    rows = []
    with tempfile.TemporaryDirectory(prefix='s4-portable-') as folder:
        portable(rows, Path(folder).resolve(), time.monotonic() + 45)
    print(json.dumps({'checks': rows}, indent=2))
    raise SystemExit(int(any(row['status'] != 'PASS' for row in rows)))

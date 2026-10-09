"""Finite hosted native admission only; no product imports or provisioning.

Only the fixed native census executes through sudo. Everything else runs as
the original runner UID. No CLI options, ref/path/command service or reruns.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import runpy
import shlex
import shutil
import stat
import subprocess
import sys
import sysconfig
import time

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE.parents[2]
SUPPORT = runpy.run_path(str(HERE.parent / 'task_10272/runner.py'), run_name='preflight_support')
CANDIDATE = 'c6dcefa2a933443504f9e36e854bca9cf020ce78'
BASELINE = '8378064e9933d5b3af4247eca55750ac427a564f'
# Source hash is renewed from the final immutable C bytes before publication.
SOURCE_SHA256 = 'f70f92756cc057b289b863f972d097ccc47b16551b5b18de4c83f968da313984'


def receipt(path):
    path = Path(path).resolve(strict=True)
    assert path.is_file() and not path.stat().st_mode & 0o022
    return {'path': str(path), 'sha256': SUPPORT['sha'](path)}


def sudo_transport_identity() -> dict:
    """Fixed macOS system transport identity; never claim an unreadable byte hash."""
    assert sys.platform == 'darwin'
    paths = ('/', '/usr', '/usr/bin', '/usr/bin/sudo')
    entries = []
    for value in paths:
        path = Path(value)
        info = path.lstat()
        assert not path.is_symlink() and info.st_uid == 0
        assert not info.st_mode & 0o022
        if value == '/usr/bin/sudo':
            assert stat.S_ISREG(info.st_mode)
            assert info.st_mode & stat.S_ISUID and info.st_mode & 0o111 == 0o111
        else:
            assert stat.S_ISDIR(info.st_mode)
        entries.append({'path': value, 'dev': info.st_dev, 'ino': info.st_ino,
                        'mode': info.st_mode, 'uid': info.st_uid, 'gid': info.st_gid,
                        'size': info.st_size, 'mtime_ns': info.st_mtime_ns,
                        'ctime_ns': info.st_ctime_ns})
    return {'path': '/usr/bin/sudo', 'authentication': 'darwin-fixed-system-stat-v1',
            'entries': entries}


def authenticate_parent(parent, commands, root, env):
    """Bind the ordinary launcher and actual native executable separately."""
    launcher = Path(sys.executable).resolve(strict=True)
    executable = Path(os.fsdecode(parent['exe'].encode('latin1')))
    origin = {'status': 'incomplete',
              'scope': 'runner bootstrap only; not provisioned CPython workload',
              'pid': os.getpid(), 'native_row': parent,
              'sys_executable': sys.executable, 'version': sys.version,
              'sys_base_prefix': sys.base_prefix,
              'launcher': receipt(launcher), 'executable': receipt(executable),
              'framework': None}
    SUPPORT['save']('parent-origin.json', origin)
    if executable != launcher:
        assert sys.platform == 'darwin', f'unexpected native executable: {executable}; launcher: {launcher}'
        framework = Path(sys.base_prefix).resolve(strict=True)
        version = f'{sys.version_info.major}.{sys.version_info.minor}'
        assert framework.name == version and framework.parent.name == 'Versions'
        assert framework.parent.parent.name == 'Python.framework'
        assert sysconfig.get_config_var('PYTHONFRAMEWORK') == 'Python'
        assert launcher == framework / 'bin' / ('python' + version), f'unexpected framework launcher: {launcher}'
        expected = framework / 'Resources/Python.app/Contents/MacOS/Python'
        assert executable == expected.resolve(strict=True), f'unexpected framework executable: {executable}'
        library = (framework / 'Python').resolve(strict=True)
        assert library.stat().st_uid in {0, os.getuid()}
        origin['framework'] = {'root': str(framework), 'library': receipt(library),
                               'launcher_dependencies': None, 'executable_dependencies': None}
        SUPPORT['save']('parent-origin.json', origin)
        for name, binary in (('launcher', launcher), ('executable', executable)):
            linked, _ = commands.run('parent-' + name + '-dependencies',
                                     ['/usr/bin/otool', '-L', str(binary)], root, env)
            dependencies = [line.strip().split(' (', 1)[0] for line in linked.splitlines()[1:]]
            assert any(Path(value).is_absolute() and Path(value).resolve() == library
                       for value in dependencies), f'{name} does not link the same framework: {dependencies}'
            origin['framework'][name + '_dependencies'] = receipt(
                SUPPORT['RECEIPTS'] / ('parent-' + name + '-dependencies.log'))
    for binary in (launcher, executable):
        assert binary.stat().st_uid in {0, os.getuid()}
        assert stat.S_ISREG(binary.stat().st_mode) and not binary.stat().st_mode & 0o022
    origin['status'] = 'authenticated'
    return SUPPORT['save']('parent-origin.json', origin)


def main():
    assert len(sys.argv) == 1
    assert os.environ.get('GITHUB_ACTIONS') == 'true'
    assert os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
    assert os.environ.get('GITHUB_REPOSITORY') == 't-benze/happyranch'
    assert os.environ.get('GITHUB_REF') == 'refs/heads/task/TASK-10279'
    assert os.environ.get('GITHUB_EVENT_NAME') == 'push'
    assert os.environ.get('GITHUB_RUN_ATTEMPT') == '1'
    assert os.getuid() == os.geteuid() != 0
    assert sys.platform in ('linux', 'darwin')
    if sys.platform == 'darwin':
        assert platform.mac_ver()[0].split('.')[0] == '15'
    else:
        assert platform.machine() == 'x86_64'
    os.umask(0o077)
    root = Path(os.environ['RUNNER_TEMP']) / 'task-10279-native'
    root.mkdir(mode=0o700)
    SUPPORT['RECEIPTS'].mkdir(mode=0o700)
    save, commands = SUPPORT['save'], SUPPORT['Commands']()
    env = SUPPORT['clean_env'](root / 'ordinary')
    venue = {'repository': os.environ['GITHUB_REPOSITORY'], 'ref': os.environ['GITHUB_REF'],
             'event': os.environ['GITHUB_EVENT_NAME'], 'attempt': os.environ['GITHUB_RUN_ATTEMPT'],
             'run_id': os.environ['GITHUB_RUN_ID'], 'platform': sys.platform,
             'arch': platform.machine(), 'uid': os.getuid(), 'euid': os.geteuid(),
             'image_os': os.environ['ImageOS'], 'image_version': os.environ['ImageVersion']}
    result = {'status': 'failed', 'candidate': CANDIDATE, 'baseline': BASELINE,
              'scope': 'native admission only; source/wheel/frozen/browser/discovery unexecuted',
              'venue': venue}
    try:
        evidence_head, _ = commands.run('evidence-head', ['/usr/bin/git', 'rev-parse', 'HEAD'], EVIDENCE, env)
        assert evidence_head.strip() == os.environ['GITHUB_SHA']
        workspace = EVIDENCE.parent
        for role, pin in (('candidate', CANDIDATE), ('baseline', BASELINE)):
            head, _ = commands.run(role + '-head', ['/usr/bin/git', 'rev-parse', 'HEAD'], workspace / role, env)
            assert head.strip() == pin
        source = HERE / 'native_observer.c'
        assert SUPPORT['sha'](source) == SOURCE_SHA256
        save('evidence-source.json', {'sha': evidence_head.strip(), 'files': {
            str(p.relative_to(EVIDENCE)): receipt(p) for p in (
                source, HERE / 'preflight.py', HERE / 'README.md',
                EVIDENCE / '.github/workflows/task-10279-native-preflight.yml',
                HERE.parent / 'task_10272/runner.py')}})
        commands.run('native-os', ['/usr/bin/uname', '-a'], root, env)
        sdk = None
        if sys.platform == 'darwin':
            commands.run('native-macos', ['/usr/bin/sw_vers'], root, env)
            commands.run('native-xcode', ['/usr/bin/xcodebuild', '-version'], root, env)
            compiler, _ = commands.run('compiler-path', ['/usr/bin/xcrun', '--find', 'clang'], root, env)
            sdk, _ = commands.run('sdk-path', ['/usr/bin/xcrun', '--show-sdk-path'], root, env)
            compiler, sdk = compiler.strip(), sdk.strip()
            assert sdk.startswith('/Applications/Xcode_')
            flags = ['-isysroot', sdk, '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2']
            libs = ['-lproc']
        else:
            compiler = str(Path(shutil.which('cc', path=env['PATH'])).resolve(strict=True))
            flags = ['-std=c11', '-Wall', '-Wextra', '-Werror', '-O2', '-static']
            libs = []
        compiler = str(Path(compiler).resolve(strict=True))
        compiler_owner = Path(compiler).stat().st_uid
        save('compiler-origin.json', {'compiler': receipt(compiler), 'uid': compiler_owner,
                                     'mode': stat.S_IMODE(Path(compiler).stat().st_mode), 'sdk': sdk})
        allowed_owners = {0} if sys.platform == 'linux' else {0, os.getuid()}
        assert compiler_owner in allowed_owners, f'unexpected compiler UID {compiler_owner}'
        if sdk:
            assert compiler.startswith(sdk.split('.app/')[0] + '.app/')
        commands.run('compiler-version', [compiler, '--version'], root, env)
        header_output, _ = commands.run('native-headers', [compiler, *flags, '-M', source], root, env)
        # Only closed source + compiler SDK headers, never checkout modules.
        header_paths = shlex.split(header_output.split(':', 1)[1].replace('\\\n', ' '))
        headers, header_uids = [], {}
        for item in dict.fromkeys(header_paths):
            path = Path(item).resolve(strict=True)
            if path == source:
                continue
            owner = path.stat().st_uid
            assert owner in allowed_owners, f'unexpected SDK header UID {owner}: {path}'
            assert not path.stat().st_mode & 0o022
            assert str(path).startswith(('/usr/', '/Library/Developer/', '/Applications/Xcode_'))
            headers.append(receipt(path))
            header_uids[str(path)] = owner
        assert headers
        binary = root / 'native-observer'
        compile_argv = [compiler, *flags, str(source), *libs, '-o', str(binary)]
        commands.run('native-compile', compile_argv, root, env)
        binary.chmod(0o500)
        dependency_argv = (['/usr/bin/otool', '-L', str(binary)] if sys.platform == 'darwin'
                           else ['/usr/bin/readelf', '--dynamic', str(binary)])
        dependencies, _ = commands.run('native-dependencies', dependency_argv, root, env)
        if sys.platform == 'linux':
            assert binary.read_bytes()[:4] == b'\x7fELF'
            assert 'There is no dynamic section in this file.' in dependencies
        else:
            assert binary.read_bytes()[:4] in (b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe')
            assert '/usr/lib/libSystem.B.dylib' in dependencies
            assert all(line.strip().startswith(('/usr/lib/', '/System/Library/'))
                       for line in dependencies.splitlines()[1:] if line.strip())
        sudo = Path('/usr/bin/sudo')
        assert sudo.stat().st_uid == 0
        sudo_receipt = (sudo_transport_identity() if sys.platform == 'darwin' else receipt(sudo))
        # Own finite native executable only; no sudo interpreter/driver/shell.
        argv = [str(sudo), '-n', '--', str(binary), str(os.getuid())]
        observer_env = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'}
        started = time.monotonic()
        probe = subprocess.run(argv, cwd='/', env=observer_env, capture_output=True, timeout=30)
        result['native_exit'] = probe.returncode
        result['observer_command'] = argv
        if sys.platform == 'darwin':
            assert sudo_receipt == sudo_transport_identity()
        assert len(probe.stdout) <= 32 * 1024 * 1024 and len(probe.stderr) <= 65536
        snapshot_path = SUPPORT['RECEIPTS'] / 'native-process-preflight.json'
        snapshot_path.write_bytes(probe.stdout)
        error_path = SUPPORT['RECEIPTS'] / 'native-process-preflight.stderr'
        error_path.write_bytes(probe.stderr)
        save('observer-command.json', {'argv': argv, 'cwd': '/', 'env': observer_env,
             'parent_uid': os.getuid(), 'parent_euid': os.geteuid(), 'exit': probe.returncode,
             'elapsed_seconds': time.monotonic() - started,
             'stdout': receipt(snapshot_path), 'stderr': receipt(error_path),
             'source': receipt(source), 'binary': receipt(binary), 'sudo': sudo_receipt})
        result['native_exit'] = probe.returncode
        result['observer_command'] = argv
        if probe.returncode:
            raise RuntimeError(f'native observer exit {probe.returncode}: {probe.stdout.decode(errors="replace")[-4096:]}')
        snapshot = json.loads(probe.stdout)
        assert snapshot['complete'] is True and snapshot['error'] is None
        assert snapshot['workload_uid'] == os.getuid()
        assert snapshot['observer_uid'] == snapshot['observer_euid'] == 0
        assert snapshot['path_encoding'] == 'byte-latin1' and snapshot['abi']['pointer_size'] == 8
        assert 0 < len(snapshot['rows']) <= 8192
        pids = set()
        for row in snapshot['rows']:
            assert row['pid'] not in pids and row['start']
            pids.add(row['pid'])
            if os.getuid() in (row['uid'], row['ruid'], row['svuid'], row['fsuid']) and row['state'] != 'Z':
                assert row['cwd'].startswith('/') and row['exe'].startswith('/')
        parent = next(row for row in snapshot['rows'] if row['pid'] == os.getpid())
        assert parent['uid'] == parent['ruid'] == parent['svuid'] == os.getuid()
        assert parent['cwd'].encode('latin1') == os.fsencode(Path.cwd())
        parent_origin = authenticate_parent(parent, commands, root, env)
        result['parent_origin'] = receipt(parent_origin)
        if sys.platform == 'darwin':
            assert snapshot['abi']['bsd_size'] == 136
            assert snapshot['abi']['bsd_uid_offset'] == 20 and snapshot['abi']['bsd_start_offset'] == 120
        native = {'sdk': sdk, 'headers': headers,
                  'dependencies': receipt(SUPPORT['RECEIPTS'] / 'native-dependencies.log'),
                  'abi': snapshot['abi'], 'static': sys.platform == 'linux',
                  'compiler_uid': compiler_owner, 'header_uids': header_uids}
        admission = save('native-admission.json', {'schema_version': 1, 'venue': venue,
                         'source': receipt(source), 'binary': receipt(binary),
                         'compiler': receipt(compiler), 'sudo': sudo_receipt, 'native': native,
                         'compile_argv': compile_argv,
                         'preflight': {'exit': probe.returncode, 'snapshot': receipt(snapshot_path)}})
        save('native-descriptor.json', receipt(admission))
        result['status'] = 'native-admission-passed'
        result['row_count'] = len(snapshot['rows'])
        result['admission'] = receipt(admission)
        return 0
    except BaseException as error:
        import traceback  # ordinary stdlib diagnostics only; never elevated
        result['error'] = {'type': type(error).__name__, 'message': str(error),
                           'traceback': traceback.format_exc(limit=8)}
        raise
    finally:
        save('result.json', result)


if __name__ == '__main__':
    raise SystemExit(main())

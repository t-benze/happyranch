"""NEVER-MERGE observation overlay for the unchanged supported test parent.

Only macOS15 disposable collection. Never execute a fixture or test body.
The source parent retains its closed stub environment and interpreter checks.
"""
from __future__ import annotations

import ast
import hashlib
import json
import marshal
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve()
PIN = '294beab846efbceecc3fa5dbfb77ff40c95fa5af'
ARGS = ['tests/', '-v', '-m', '', '--collect-only']


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, data):
    Path(path).write_text(json.dumps(data, sort_keys=True, indent=2) + '\n')


def parent(config_path):
    config = json.loads(config_path.read_text())
    source = Path(config['source'])
    assert config['candidate'] == PIN and os.getuid() == os.geteuid() != 0
    assert sys.version_info[:3] == (3, 14, 4)
    target = source / 'tests/helpers/integration_parent.py'
    assert digest(target) == 'c7e0718e54f3c783561423f718b0a4073ff2638cf2d2af978d8013e3070f46ba'
    # Bind the observation overlay to ONLY the parent-owned pytest child.
    # The parent's git commands, nested uv identity command, closed environment,
    # stub creation and group cleanup execute unchanged.
    original = subprocess.Popen
    launches = []
    wrapped = False

    def popen(argv, *args, **kwargs):
        nonlocal wrapped
        argv = list(map(str, argv))
        launches.append({'argv': argv, 'cwd': str(kwargs.get('cwd', os.getcwd()))})
        if argv == [sys.executable, '-m', 'pytest', *ARGS]:
            assert not wrapped and kwargs['start_new_session'] is True
            assert Path(kwargs['cwd']).resolve() == source.resolve()
            assert kwargs['env']['EXPECTED_SHA'] == PIN
            assert kwargs['env']['CANDIDATE_PY'] == sys.executable
            wrapped = True
            argv = [sys.executable, '-S', str(HERE), '--child', str(config_path)]
        return original(argv, *args, **kwargs)

    subprocess.Popen = popen
    try:
        module = runpy.run_path(str(target), run_name='collection_source_parent')
        code = module['main'](['--', 'pytest', *ARGS])
        return code
    finally:
        subprocess.Popen = original
        write(config['parent_receipt'], {'source': PIN, 'parent_sha256': digest(target),
            'supported_selection': ['pytest', *ARGS], 'overlay_sha256': digest(HERE),
            'parent_launches': launches, 'pytest_child_wrapped': wrapped,
            'scope': 'source parent execution; original source parent and provider guard unchanged'})


def child(config_path):
    assert sys.flags.no_site and 'site' not in sys.modules
    assert os.getuid() == os.geteuid() != 0 and sys.platform == 'darwin'
    assert sys.version_info[:3] == (3, 14, 4)
    config = json.loads(config_path.read_text())
    source = Path(config['source']).resolve()
    stage = Path(config['stage']).resolve()
    evidence = json.loads(Path(config['audit']).read_text())
    assert evidence['candidate'] == PIN
    binding = json.loads(Path(os.environ['HAPPYRANCH_TEST_PARENT_MANIFEST']).read_text())
    assert binding['revision'] == PIN and Path(binding['source']) == source
    owned = Path(binding['root']).resolve()
    assert os.environ['HOME'] == str(owned / 'home')
    assert os.environ['HAPPYRANCH_DAEMON_HOME'] == str(owned / 'daemon')
    assert os.environ['PYTHONPATH'].split(os.pathsep) == [
        str(source / 'tests/helpers/integration_stub_guard'), str(source)]
    assert os.environ['PATH'] == os.pathsep.join((str(owned / 'bin'), '/usr/bin', '/bin'))
    assert not any(key in os.environ for key in (
        'PYTEST_ADDOPTS', 'PYTEST_PLUGINS', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD', 'EXECNET_DEBUG',
        'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'CODEX_HOME', 'CLAUDE_CONFIG_DIR',
        'HAPPYRANCH_RUNTIME', 'HAPPYRANCH_DAEMON_TOKEN', 'HAPPYRANCH_ORG_SLUG'))
    empty = {name: not any((owned / name).iterdir()) for name in ('home', 'config', 'tmp')}
    assert all(empty.values())
    # The unchanged parent has already performed its bounded uv interpreter
    # identity command. Its new invocation-owned cache may contain uv data;
    # that is not ambient configuration and must be recorded, not fabricated
    # as an empty cache.
    cache_entries = sorted(p.name for p in (owned / 'cache').iterdir())
    assert set(cache_entries) <= {'uv'}
    assert sorted(p.name for p in (owned / 'daemon').iterdir()) == ['executors.json']
    for name in ('config.yaml', 'daemon.pid', 'daemon.port', 'daemon.token'):
        assert not (owned / 'daemon' / name).exists()
    assert shutil.which('ip', path=os.environ['PATH']) is None, 'unreviewed macOS ip binary; refuse'
    # Reproduce -m pytest's cwd entry; site.main then performs the REAL normal
    # venv .pth and source sitecustomize startup, under the already-active fence.
    sys.path[0] = str(source)
    allowed = config['verified_files']
    checked = {}
    effects = {}
    denied = []
    probes = []
    control_probes = []
    current_probe = None
    counts = {'fixture_entries': 0, 'test_body_entries': 0, 'runtest_entries': 0,
              'provider_entries': 0, 'profile_control_calls': 0}
    call_counts = {}
    native_call_counts = {}
    descriptor_fd = os.open(config['events'], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    written = 0

    def emit(row):
        nonlocal written
        data = (json.dumps(row, sort_keys=True, default=str) + '\n').encode()
        if written + len(data) > 8 * 1024 * 1024:
            os.write(2, b'collection control receipt cap exceeded\n')
            os._exit(87)
        if os.write(descriptor_fd, data) != len(data):
            os._exit(87)
        written += len(data)

    def refuse(kind, detail):
        denied.append({'kind': kind, 'detail': detail})
        emit({'kind': 'denied', 'event': kind, 'detail': detail, 'uid': os.getuid()})
        # Fatal, before effect/body entry. A profile exception would turn off
        # profiling; pytest catching it must never enable subsequent execution.
        os._exit(87)

    def verify_file(path):
        path = str(Path(path).resolve())
        if path in checked:
            return
        expected = allowed.get(path)
        if expected is None or digest(path) != expected:
            refuse('unclassified-code-file', path)
        checked[path] = expected

    forbidden_fixtures = {(str(source / p), name) for p, names in evidence['fixture_functions'].items()
                          for name in names}
    runtime_exec = str(source / 'runtime/orchestrator/executors.py')

    def profile(frame, event, arg):
        if event == 'c_call':
            key = (getattr(arg, '__module__', '') or '') + '.' + getattr(arg, '__qualname__', '')
            native_call_counts[key] = native_call_counts.get(key, 0) + 1
        if event == 'c_call' and getattr(arg, '__name__', '') in ('start_new_thread', 'start_joinable_thread'):
            refuse('unclassified-native-thread', getattr(arg, '__name__', ''))
        if event != 'call':
            return
        code = frame.f_code
        path, name, qualname = code.co_filename, code.co_name, code.co_qualname
        key = (path, code.co_firstlineno, qualname)
        call_counts[key] = call_counts.get(key, 0) + 1
        if len(call_counts) > 50000:
            refuse('execution-closure-cap', len(call_counts))
        if path == str(HERE) and name == 'profile_control_sentinel':
            counts['profile_control_calls'] += 1
        if path.startswith(str(source / 'tests') + '/'):
            if name.startswith('test'):
                counts['test_body_entries'] += 1
                refuse('test-body-entry', {'file': path, 'function': qualname})
            if (path, name) in forbidden_fixtures:
                counts['fixture_entries'] += 1
                refuse('fixture-body-entry', {'file': path, 'function': qualname})
        if ('/_pytest/' in path and
                (name in ('pytest_runtest_protocol', 'pytest_runtest_setup', 'pytest_runtest_call',
                          'pytest_runtest_teardown', 'pytest_pyfunc_call', 'pytest_fixture_setup',
                          'call_fixture_func', 'runtestprotocol', 'call_and_report')
                 or qualname in ('FixtureDef.execute', 'TopRequest._fillfixtures'))):
            counts['runtest_entries'] += 1
            refuse('pytest-execution-entry', {'file': path, 'function': qualname})
        if path == runtime_exec and name in ('_run_command', '_resolve_binary', 'run', 'build_launch_spec'):
            counts['provider_entries'] += 1
            refuse('provider-entry', {'file': path, 'function': qualname})

    forbidden_events = {'os.system', 'os.fork', 'os.forkpty', 'os.posix_spawn', 'os.exec',
        'os.startfile', 'pty.spawn', 'socket.__new__', 'socket.connect', 'socket.bind', 'socket.getaddrinfo',
        'socket.gethostbyname', 'socket.gethostbyaddr', 'socket.sendto', 'sqlite3.connect',
        'os.kill', 'os.killpg'}
    mutations = {'os.mkdir', 'os.remove', 'os.rmdir', 'os.rename', 'os.chmod', 'os.chown',
                 'os.truncate', 'os.utime', 'os.symlink', 'os.link'}

    def audit(event, args):
        if not control_probes:
            effects[event] = effects.get(event, 0) + 1
        if event in ('sys.setprofile', 'sys.settrace') and sys._getframe(1).f_code.co_filename != str(HERE):
            refuse('observation-control-change', event)
        if event in forbidden_events:
            if control_probes:
                raise PermissionError('synthetic-control-denial')
            refuse(event, list(map(str, args)))
        if event == 'subprocess.Popen':
            if control_probes:
                raise PermissionError('synthetic-control-denial')
            executable, argv, cwd, env = args
            if (current_probe is None or executable != 'ip'
                    or argv != ['ip', '-4', '-o', 'addr', 'show']
                    or cwd is not None or env is not None):
                refuse(event, {'executable': executable, 'argv': argv, 'cwd': cwd})
            if (os.environ['PATH'] != os.pathsep.join((str(owned / 'bin'), '/usr/bin', '/bin'))
                    or shutil.which('ip', path=os.environ['PATH']) is not None):
                refuse('probe-binary-boundary-changed', os.environ['PATH'])
            emit({'kind': 'admitted-missing-ip-attempt', 'source': current_probe,
                  'executable': executable, 'argv': argv, 'uid': os.getuid(),
                  'effective_PATH': os.environ['PATH'], 'binary': None, 'timeout_seconds': 10})
        if event == 'import' and len(args) > 1 and args[1] and str(args[1]).endswith(('.so', '.dylib')):
            verify_file(args[1])
        if event == 'exec':
            filename = args[0].co_filename
            if filename.startswith('/'):
                verify_file(filename)
            elif filename.startswith('<frozen '):
                # Built by the same official pinned CPython source recipe.
                if filename not in config['frozen_code_names']:
                    refuse('unclassified-frozen-code', filename)
            elif filename in ('<string>', '<unknown>', '<module>', '<annotation>', '<typing>'):
                # Actual local annotation/dataclass/typing-generated code is
                # audited; these names confer no launch/body permission.
                emit({'kind': 'in-memory-code', 'filename': filename,
                      'code_sha256': hashlib.sha256(marshal.dumps(args[0])).hexdigest()})
            else:
                refuse('unclassified-code-origin', filename)
        if event == 'ctypes.dlopen':
            name = args[0]
            if name is not None and name not in ('/usr/lib/libc.dylib', '/usr/lib/libproc.dylib'):
                refuse(event, name)
            emit({'kind': 'audited-native-loader', 'library': name,
                  'binding': config['native_runtime']})
        if event == 'ctypes.dlsym' and args[1] not in (
                'Py_Version', '_dyld_get_image_name', '_dyld_image_count', 'proc_listpids', 'proc_pidinfo'):
            refuse(event, args[1])
        paths = []
        if event == 'open':
            path, mode, flags = args
            if isinstance(path, (str, bytes)) and ((mode and any(c in mode for c in 'wax+'))
                    or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)):
                paths.append(path)
        elif event in mutations:
            paths = [a for a in args[:2 if event in ('os.rename','os.link','os.symlink') else 1]
                     if isinstance(a, (str, bytes))]
        for path in paths:
            path = Path(os.fsdecode(path)).resolve()
            if path != Path('/dev/null') and not any(path.is_relative_to(p) for p in (owned, stage, Path(config['receipt_root']))):
                refuse('unclassified-write', {'event': event, 'path': str(path)})

    sys.addaudithook(audit)
    sys.setprofile(profile)
    # Real CPython audit dispatch, no command, socket or paused body executed.
    for event, payload in [('os.system', ('control-only-no-command',)),
                           ('socket.connect', (None, ('control-only-no-address', 0))),
                           ('subprocess.Popen', ('control-only-no-binary', [], None, None))]:
        control_probes.append(event)
        try:
            sys.audit(event, *payload)
        except PermissionError:
            emit({'kind': 'synthetic-audit-control-denied', 'event': event, 'effect_executed': False})
        else:
            refuse('control-not-denied', event)
        finally:
            control_probes.pop()

    def profile_control_sentinel():
        return None

    profile_control_sentinel()
    assert counts['profile_control_calls'] == 1
    emit({'kind': 'pre-site-controls-active', 'empty_home_config_tmp': empty,
          'parent_owned_cache_entries': cache_entries,
          'daemon_files': ['executors.json'], 'ambient_provider_config_absent': True,
          'profile_control_calls': 1, 'source_parent_manifest': binding,
          'sys_path_before_site': list(sys.path)})
    import site
    site.main()
    assert site.ENABLE_USER_SITE is False
    assert Path(sys.modules['sitecustomize'].__file__).resolve() == source / 'tests/helpers/integration_stub_guard/sitecustomize.py'
    assert not [str(Path(p) / 'multipart.py') for p in sys.path if (Path(p) / 'multipart.py').is_file()]
    emit({'kind': 'effective-startup', 'sys_path': list(sys.path), 'user_site_enabled': site.ENABLE_USER_SITE,
          'multipart_override_files': [], 'sitecustomize_sha256': digest(sys.modules['sitecustomize'].__file__)})
    original_run = subprocess.run
    original_execute_child = subprocess.Popen._execute_child

    def observed_execute_child(process, *args, **kwargs):
        # Missing exec still creates a native fork child. Keep the actual PID
        # and CPython waitpid-derived status rather than claiming no child.
        try:
            return original_execute_child(process, *args, **kwargs)
        finally:
            if current_probe is not None:
                row = probes[-1]
                row['native_fork_child_pid'] = getattr(process, 'pid', None)
                row['native_fork_child_exit'] = process.returncode
                row['native_fork_child_reaped'] = process.returncode is not None

    subprocess.Popen._execute_child = observed_execute_child

    def probe_run(argv, *args, **kwargs):
        nonlocal current_probe
        frame = sys._getframe(1)
        relative = str(Path(frame.f_code.co_filename).relative_to(source))
        if (relative not in evidence['ip_sources'] or frame.f_code.co_name != '_host_network_ipv4'
                or argv != ['ip','-4','-o','addr','show'] or args
                or kwargs != {'capture_output': True, 'text': True, 'check': False, 'timeout': 10}
                or any(p['source'] == relative for p in probes)):
            refuse('unclassified-subprocess-run', {'source': relative, 'argv': argv, 'kwargs': kwargs})
        assert digest(source / relative) == evidence['ip_sources'][relative]
        row = {'source': relative, 'source_sha256': evidence['ip_sources'][relative],
               'argv': argv, 'uid': os.getuid(), 'binary': None, 'binary_state': 'missing', 'timeout_seconds': 10}
        probes.append(row)
        current_probe = relative
        started = time.monotonic()
        try:
            result = original_run(argv, *args, **kwargs)
            row.update(exit=result.returncode, stdout=result.stdout, stderr=result.stderr)
            refuse('unexpected-macos-ip-execution', relative)
        except FileNotFoundError as exc:
            row.update(error_type=type(exc).__name__, errno=exc.errno, executable=exc.filename)
            row.update(stdout=None, stderr=None, output_status='exec-not-reached; no CompletedProcess output')
            assert row['native_fork_child_reaped']
            raise  # Preserve source helper's own existing error-to-None behavior.
        finally:
            row['elapsed_seconds'] = time.monotonic() - started
            emit({'kind': 'probe-result', **row})
            current_probe = None

    subprocess.run = probe_run
    import pytest
    state = {'deselected': 0, 'reports': [], 'items': [], 'sessionstarted': False}

    class Observation:
        def pytest_sessionstart(self, session):
            cfg = session.config
            assert cfg.option.collectonly is True and cfg.option.markexpr == ''
            assert cfg.args == ['tests/'] and not cfg.option.keyword
            assert not cfg.option.numprocesses and not cfg.option.looponfail
            state['sessionstarted'] = True
            emit({'kind': 'effective-options', 'options': vars(cfg.option), 'args': cfg.args,
                  'ini_addopts': cfg.getini('addopts'), 'ini_cache_dir': cfg.getini('cache_dir')})

        def pytest_deselected(self, items):
            state['deselected'] += len(items)

        def pytest_collectreport(self, report):
            state['reports'].append({'nodeid': report.nodeid, 'outcome': report.outcome,
                'error': str(report.longrepr) if report.failed else None})

        def pytest_collection_finish(self, session):
            state['items'] = [item.nodeid for item in session.items]

        def pytest_sessionfinish(self, session, exitstatus):
            cfg = session.config
            plugins = []
            for name, plugin in cfg.pluginmanager.list_name_plugin():
                if plugin is None:
                    continue
                module_name = getattr(plugin, '__name__', type(plugin).__module__)
                module = sys.modules.get(module_name)
                file = getattr(plugin, '__file__', None) or getattr(module, '__file__', None)
                plugins.append({'name': name, 'module': module_name, 'file': file,
                                'sha256': digest(file) if file and Path(file).is_file() else None})
            state.update(testscollected=session.testscollected, collection_errors=session.testsfailed,
                         exit=int(exitstatus), plugins=plugins,
                         actual_pytest11=[{'distribution': d.project_name, 'version': d.version,
                            'module': getattr(p,'__name__',type(p).__module__)}
                            for p,d in cfg.pluginmanager.list_plugin_distinfo()])

    sys.argv = ['pytest', *ARGS, '-o', 'cache_dir=' + str(stage / 'pytest-cache')]
    result = {'scope': 'collection/import discovery only; NEVER behavioral QA or keeper PASS'}
    try:
        code = pytest.main(sys.argv[1:], plugins=[Observation()])
        result['pytest_exit'] = int(code)
        return int(code)
    finally:
        subprocess.run = original_run
        subprocess.Popen._execute_child = original_execute_child
        result.update(source=PIN, uid=os.getuid(), selection=sys.argv, state=state,
            controls=dict(counts), denied=list(denied), probes=list(probes), checked_code_files=dict(checked),
            actual_python_call_closure=[{'file':p,'first_line':line,'qualname':name,'calls':n}
                for (p,line,name),n in sorted(call_counts.items())],
            actual_native_call_counts=dict(native_call_counts),
            actual_imports=[{'name':name,'file':getattr(module,'__file__',None),
                            'origin':getattr(getattr(module,'__spec__',None),'origin',None)}
                for name,module in sorted(sys.modules.items()) if module is not None],
            audit_event_counts=dict(effects), source_parent=PIN,
            profile_retained=sys.getprofile() is profile, test_or_fixture_bodies_executed=False,
            native_child_execution_scope='exact admitted missing-ip fork/exec attempts, with actual child PID/waitpid status; no ip executable found',
            cleanup_scope='source parent finally plus coordinator owned-group reap and native census')
        assert sys.getprofile() is profile
        write(config['child_receipt'], result)
        emit({'kind':'collection-control-tail', 'pytest_exit':result.get('pytest_exit'),
              'controls':counts, 'denied':denied, 'probe_count':len(probes)})
        os.close(descriptor_fd)


if __name__ == '__main__':
    assert sys.argv[1] in ('--parent', '--child') and len(sys.argv) == 3
    cfg = Path(sys.argv[2]).resolve(strict=True)
    raise SystemExit(parent(cfg) if sys.argv[1] == '--parent' else child(cfg))

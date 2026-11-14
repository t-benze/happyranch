"""Portable harness contracts only; no native/product execution claim."""
from __future__ import annotations

import ast
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import plistlib
import runpy
import shlex
import shutil
import stat
import zipfile
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import ModuleType
import unittest
import warnings
from unittest.mock import patch

from scripts.mac_native import suite_packaged as suite


class PackagedHarnessTests(unittest.TestCase):
    def test_libproc_load_does_not_need_discovery_module_or_relative_dyld_search(self) -> None:
        import builtins
        import ctypes
        from runtime.platform.process_census import _LibProc, LibProcUnavailable
        original_import = builtins.__import__
        def frozen_import(name, *args, **kwargs):
            if name == 'ctypes.util':
                raise ImportError('discovery module absent from synthetic PYZ')
            return original_import(name, *args, **kwargs)
        with (patch.object(sys, 'platform', 'darwin'),
              patch.object(builtins, '__import__', side_effect=frozen_import),
              patch.object(ctypes, 'CDLL', side_effect=OSError('native dylib unavailable on Linux')) as load):
            with self.assertRaises(LibProcUnavailable):
                _LibProc()
        load.assert_called_once_with('/usr/lib/libproc.dylib', use_errno=True)

    @unittest.skipUnless(sys.platform == 'linux', 'portable real POSIX probe uses Linux census')
    def test_process_group_probe_works_without_path(self) -> None:
        from runtime.platform.macos_process_group import MacOSProcessGroupBackend
        with patch.dict(os.environ, {'PATH': ''}):
            report = MacOSProcessGroupBackend().probe()
        self.assertTrue(report.healthy, report.reason)
        self.assertEqual(report.backend, 'macos-process-group')

    def test_pyz_relative_census_loads_skill_from_spec_data_destination(self) -> None:
        class StopAnalysis(Exception):
            pass
        inventory = []
        def analysis(*_args, **kwargs):
            inventory.extend(kwargs['datas'])
            raise StopAnalysis
        # Evaluate the real spec's data inputs, stopping before any freezer or
        # notice writer. This proves layout wiring, not a PyInstaller build.
        with self.assertRaises(StopAnalysis):
            runpy.run_path(str(suite.ROOT / 'packaging/daemon.spec'), init_globals={'Analysis': analysis})
        relative = Path('runtime/skills/bundled/workspace-cleanup/scripts/darwin_process.py')
        matches = [(Path(src), Path(dst)) for src, dst in inventory
                   if Path(src).is_dir() and relative.is_relative_to(dst)]
        self.assertEqual(len(matches), 1, 'spec must deliver the shared Darwin script at its loader-relative path')
        source, destination = matches[0]
        with tempfile.TemporaryDirectory() as folder:
            internal = Path(folder) / '_internal'
            target = internal / relative
            target.parent.mkdir(parents=True)
            shutil.copyfile(source / relative.relative_to(destination), target)
            module = internal / 'runtime/platform/process_census.pyc'
            module.parent.mkdir(parents=True)
            # Execute the actual archived module code with PyInstaller's filename,
            # not a second implementation of its data-path calculation.
            code = compile((suite.ROOT / 'runtime/platform/process_census.py').read_bytes(), str(module), 'exec')
            with patch.dict(sys.modules):
                sys.modules.pop('darwin_process', None)
                archived = ModuleType('runtime.platform.process_census')
                archived.__file__ = str(module)
                sys.modules[archived.__name__] = archived
                exec(code, archived.__dict__)
                self.assertEqual(Path(sys.modules['darwin_process'].__file__), target)

    def test_session_survivors_use_identity_and_only_ancestry_proven_private_groups(self) -> None:
        def row(pid, ppid, pgid, identity='original', state='S'):
            return dict(pid=pid, ppid=ppid, pgid=pgid, start_identity=identity, state=state)
        daemon, wrapper, parent, child = row(10, 1, 10), row(11, 10, 10), row(12, 11, 10), row(13, 12, 10)
        before = [daemon, wrapper, parent, child]
        shared = suite.private_session_groups(before, 12, 10)
        self.assertEqual(shared, set())
        self.assertEqual(suite.session_survivors([daemon, wrapper], [parent, child], shared), [])
        self.assertEqual(suite.session_survivors([parent], [parent, child], shared), [parent])
        reused = row(12, 1, 20, 'later')
        self.assertEqual(suite.session_survivors([daemon, reused], [parent, child], shared), [])
        zombie = row(13, 1, 10, state='Z')
        self.assertEqual(suite.session_survivors([zombie], [parent, child], shared), [zombie])
        private = [daemon, row(11, 10, 11), row(12, 11, 11), row(13, 12, 11)]
        groups = suite.private_session_groups(private, 12, 10)
        self.assertEqual(groups, {11})
        unrecorded_child = row(14, 1, 11, 'new')
        self.assertEqual(suite.session_survivors([unrecorded_child], private[2:], groups), [unrecorded_child])
        escaped = {**child, 'pgid': 90, 'executable': '/bin/sleep'}
        self.assertEqual(suite.owned_rows([escaped], Path('/scratch'), {10, 11}, set(),
                                        {(13, 'original')}), [child | {'pgid': 90}])
        foreign_group = [daemon, row(11, 10, 50), row(12, 11, 50), row(50, 1, 50)]
        self.assertEqual(suite.private_session_groups(foreign_group, 12, 10), set())
        with self.assertRaisesRegex(RuntimeError, 'ancestry unknown'):
            suite.private_session_groups([daemon, row(12, 99, 12)], 12, 10)

    def test_survivor_evidence_is_bounded_and_unknown_never_passes(self) -> None:
        rows = [dict(pid=i, ppid=1, pgid=2, state='Z', start_identity='original') for i in range(40)]
        with patch.object(suite, 'census', return_value=rows):
            kept = suite.survivor_snapshot(lambda current: current)
        self.assertEqual(kept['count'], 40)
        self.assertEqual(len(kept['rows']), 32)
        self.assertTrue(kept['truncated'])
        self.assertEqual(kept['status'], 'SURVIVORS')
        self.assertEqual(kept['rows'][0]['state'], 'Z')
        self.assertEqual(kept['rows'][0]['start_identity'], 'original')
        section = {}
        with patch.object(suite, 'census', side_effect=ValueError('sensitive host output')):
            with self.assertRaisesRegex(RuntimeError, 'evidence unknown'):
                suite.await_absence(lambda current: current, 1, 'no leftovers', section, 'post_stop')
        self.assertEqual(section['post_stop'], {'status': 'UNKNOWN', 'error': 'ValueError'})

    def test_backend_refusal_prevents_session_and_retains_finalization(self) -> None:
        backends = [dict(name='passthrough', healthy=True, capabilities={}),
                    dict(name='macos-process-group', healthy=True, capabilities={'reports_cpu_total': 'best_effort'})]
        for backend, leftover in [(backends[0], False), (backends[1], False), (backends[0], True)]:
            with self.subTest(backend=backend, leftover=leftover), tempfile.TemporaryDirectory() as folder:
                scratch = Path(folder).resolve()
                source = scratch / 'source'
                source.mkdir()
                report = {'suite': 'packaged', 'platform': 'darwin'}
                launched = []
                stopped = []
                def census():
                    return [dict(pid=900002, ppid=1, pgid=999999, state='Z',
                                 start_identity='Sat Nov 14 05:00:00 2026', executable='/bin/sleep')] if stopped and leftover else []
                def fast_await(fn, _seconds, label):
                    value = fn()
                    if not value:
                        raise TimeoutError(label)
                    return value
                def build(argv, *_args):
                    app = Path(argv[argv.index('--output-dir') + 1]) / 'HappyRanch.app'
                    for name in ('Contents/MacOS/HappyRanch', 'Contents/Resources/daemon/happyranch-daemon',
                                 'Contents/Resources/daemon/happyranch'):
                        path = app / name
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(b'synthetic executable; never run')
                    return {'exit_code': 0}
                def checked(argv, *_args, **_kwargs):
                    if argv[0] == '/usr/bin/ditto':
                        shutil.copytree(argv[1], argv[2])
                    return {'tail': 'a' * 40}
                def launch(*_args, **kwargs):
                    home = kwargs['cwd']
                    (home / 'daemon.port').write_text('1234')
                    (home / 'daemon.token').write_text('synthetic-secret')
                    kwargs['stdout'].write(
                        b"INFO runtime.platform.backend_factory: host backend 'linux' unhealthy (not Linux); trying next\n"
                        b"INFO runtime.platform.backend_factory: host backend 'macos' probe raised: cannot load libproc synthetic-secret\n"
                        b"INFO runtime.platform.backend_factory: selected honest fallback backend 'passthrough' (all capabilities unavailable)\n"
                        b"unrelated private output must not survive\n")
                    kwargs['stdout'].flush()
                    child = unittest.mock.Mock(pid=999999, returncode=0)
                    child.poll.return_value = None
                    child.terminate.side_effect = lambda: stopped.append(True)
                    launched.append(child)
                    return child
                with (patch.object(suite, 'ROOT', source),
                      patch.dict(os.environ, {'TMPDIR': str(scratch / 'tmp'), 'SOURCE_SHA': 'a' * 40,
                                              'WORKSPACE': str(scratch)}),
                      patch.object(suite, 'census', side_effect=census),
                      patch.object(suite, 'await_value', side_effect=fast_await),
                      patch.object(suite, 'checked', side_effect=checked),
                      patch.object(suite, 'command', side_effect=build),
                      patch.object(suite, 'retain_product', return_value={}),
                      patch.object(suite.subprocess, 'Popen', side_effect=launch),
                      patch.object(suite, 'health_and_web', return_value={'name': 'health_and_web', 'status': 'PASS', 'backend': backend}),
                      patch.object(suite.Api, 'json', side_effect=AssertionError('session API reached')) as session_api,
                      patch('scripts.mac_native.suite_toolchain.executor_feasibility', return_value={}),
                      patch('scripts.mac_native.toolchain.provision', return_value={'PATH': '/usr/bin:/bin'})):
                    with self.assertRaisesRegex(RuntimeError, 'requires a healthy tree-killing backend'):
                        suite.run(report)
                    session_api.assert_not_called()
                    launched[0].terminate.assert_called_once()
                    launched[0].wait.assert_called_once_with(timeout=5)
                section = report['packaged']
                selection = section['backend_selection']
                self.assertEqual(selection['selected'], backend)
                self.assertEqual(selection['candidates']['linux']['status'], 'UNHEALTHY')
                self.assertEqual(selection['candidates']['macos']['status'], 'RAISED')
                self.assertIn('cannot load libproc [redacted]', selection['candidates']['macos']['reason'])
                self.assertNotIn('synthetic-secret', json.dumps(report))
                self.assertNotIn('unrelated private output', json.dumps(report))
                self.assertEqual(section['failure_survivors']['status'], 'ABSENT')
                self.assertEqual(section['post_cancel_survivors']['status'], 'NOT_RUN')
                self.assertEqual(section['post_stop_survivors']['status'], 'SURVIVORS' if leftover else 'ABSENT')
                if leftover:
                    self.assertEqual(section['post_stop_survivors']['rows'][0]['state'], 'Z')
                    self.assertEqual(section['post_stop_survivors']['rows'][0]['start_identity'], 'Sat Nov 14 05:00:00 2026')
                self.assertEqual(report['steps']['4']['status'], 'FAIL')
                self.assertEqual(report['steps']['5']['status'], 'NOT_RUN')
                self.assertEqual(report['steps']['7']['status'], 'FAIL' if leftover else 'PASS')
                # JSON/collector survive the scratch removal; no log file required.
                serialized = json.loads(json.dumps(report))
                shutil.rmtree(scratch / 'packaged')
                from scripts.mac_native.collect_packaged import join_exit_receipt
                joined = join_exit_receipt(serialized, 'source_sha=' + 'a' * 40 +
                    '\nsuite=packaged\nscratch_removed=true\n', 'a' * 40)
                self.assertEqual(joined['packaged']['backend_selection'], selection)
                self.assertEqual(joined['steps']['8']['status'], 'PASS')
        suite.require_tree_backend({'selected': {'name': 'macos-process-group', 'healthy': True,
            'capabilities': {'kills_tree_best_effort': 'best_effort'}}, 'candidates': {}})

    def test_backend_reason_caps_and_missing_log_remain_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / 'daemon.log'
            missing = suite.backend_diagnostics(log, {}, ())
            self.assertEqual(missing['status'], 'UNKNOWN')
            self.assertEqual(missing['candidates']['macos']['status'], 'NOT_OBSERVED')
            log.write_bytes(b"runtime.platform.backend_factory: host backend 'linux' probe raised: token=synthetic-password\n" +
                            (b"runtime.platform.backend_factory: host backend 'macos' probe raised: "
                             + b'x' * 2000 + b' token=synthetic-password\n') * 10 + b'x' * suite.STREAM_CAP)
            kept = suite.backend_diagnostics(log, {}, ())
            self.assertTrue(kept['truncated'])
            self.assertLessEqual(len(kept['events']), 8)
            self.assertTrue(all(len(row.encode()) <= 768 for row in kept['events']))
            self.assertNotIn('synthetic-password', json.dumps(kept))

    def test_http_failures_name_response_without_credentials_or_unbounded_body(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:
                pass
            def do_GET(self) -> None:
                status, content, raw = {
                    '/html': (200, 'text/html', b'<html>SPA fallback</html>'),
                    '/utf8': (200, 'application/json', b'"\xff"'),
                    '/malformed': (200, 'application/json', b'{broken'),
                    '/overflow': (200, 'text/plain', b'x' * 1025),
                    '/failure': (502, 'text/plain', b'\x00\xff' + b'x' * 1024),
                    '/api/v1/auth/registration-token/runtime': (400, 'text/plain', b'synthetic-secret'),
                }[self.path.split('?')[0]]
                self.send_response(status)
                self.send_header('Content-Type', content)
                self.end_headers()
                self.wfile.write(raw)
            do_POST = do_GET
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                home = Path(folder)
                (home / 'daemon.port').write_text(str(server.server_port))
                (home / 'daemon.token').write_text('synthetic-secret')
                api = suite.Api(home)
                cases = [('/html', None, 200, 'text/html', 'JSONDecodeError'),
                         ('/utf8', None, 200, 'application/json', 'UnicodeDecodeError'),
                         ('/malformed', None, 200, 'application/json', 'JSONDecodeError'),
                         ('/failure?limit=100', None, 502, 'text/plain', 'non-2xx response'),
                         ('/failure', {}, 502, 'text/plain', 'non-2xx response'),
                         ('/api/v1/auth/registration-token/runtime', {}, 400, 'text/plain', 'non-2xx response')]
                for path, body, status, content, error in cases:
                    with self.subTest(path=path, body=body):
                        with self.assertRaises(Exception) as caught:
                            api.json(path, body)
                        row = getattr(caught.exception, 'observation', {})
                        self.assertEqual(row.get('method'), 'GET' if body is None else 'POST')
                        self.assertEqual(row['path'], path.split('?')[0])
                        self.assertEqual((row['http_status'], row['content_type'], row['error']), (status, content, error))
                        self.assertLessEqual(len(row['body_prefix'].encode()), 512)
                        self.assertNotIn('synthetic-secret', json.dumps(row))
                        if path.startswith('/api/v1/auth/'):
                            self.assertTrue(row['body_redacted'])
                        else:
                            self.assertTrue(row['body_prefix'])
                        self.assertEqual(json.loads(json.dumps(row)), row)
                with self.assertRaises(Exception) as caught:
                    api.get_bytes('/overflow', limit=1024)
                self.assertEqual(getattr(caught.exception, 'observation', {}).get('error'), 'HTTP response cap')
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

    def test_readiness_keeps_last_http_failure_and_decode_fails_immediately(self) -> None:
        row = {'method': 'GET', 'path': '/api/v1/health', 'http_status': 503,
               'content_type': 'text/plain', 'body_prefix': 'starting'}
        api = unittest.mock.Mock()
        api.json.side_effect = suite.ApiFailure('non-2xx response', row)
        readiness = {}
        with patch.object(suite.time, 'monotonic', side_effect=[0, 0, 46]), patch.object(suite.time, 'sleep'):
            with self.assertRaises(suite.ApiFailure) as caught:
                suite.health_and_web(api, readiness)
        self.assertEqual(caught.exception.observation['http_status'], 503)
        self.assertEqual(caught.exception.observation['body_prefix'], 'starting')
        api.json.reset_mock()
        api.json.side_effect = suite.ApiFailure('JSONDecodeError', {**row, 'http_status': 200})
        with patch.object(suite.time, 'sleep') as sleep:
            with self.assertRaises(suite.ApiFailure) as caught:
                suite.health_and_web(api, readiness)
        self.assertEqual(caught.exception.observation['error'], 'JSONDecodeError')
        api.json.assert_called_once_with('/api/v1/health')
        sleep.assert_not_called()

    def test_http_diagnostic_survives_failed_step_and_exit_collection(self) -> None:
        row = {'method': 'GET', 'path': '/api/v1/health', 'http_status': 200,
               'content_type': 'text/html', 'body_prefix': '<html>unexpected SPA</html>'}
        failure = suite.ApiFailure('JSONDecodeError', row)
        with tempfile.TemporaryDirectory() as folder:
            scratch = Path(folder).resolve()
            source = scratch / 'source'
            source.mkdir()
            report = {'suite': 'packaged', 'platform': 'darwin'}
            def build(argv: list[str], *_args) -> dict:
                out = Path(argv[argv.index('--output-dir') + 1])
                app = out / 'HappyRanch.app'
                for name in ('Contents/MacOS/HappyRanch', 'Contents/Resources/daemon/happyranch-daemon',
                             'Contents/Resources/daemon/happyranch'):
                    path = app / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b'synthetic; never executed')
                return {'exit_code': 0}
            def checked(argv: list[str], *_args, **_kwargs) -> dict:
                if argv[0] == '/usr/bin/ditto':
                    shutil.copytree(argv[1], argv[2])
                return {'tail': 'a' * 40}
            def launch(*_args, **kwargs):
                home = kwargs['cwd']
                (home / 'daemon.port').write_text('1234')
                (home / 'daemon.token').write_text('synthetic')
                child = unittest.mock.Mock(pid=999999, returncode=0)
                child.poll.return_value = None
                child.wait.return_value = 0
                return child
            with (patch.object(suite, 'ROOT', source),
                  patch.dict(os.environ, {'TMPDIR': str(scratch / 'tmp'), 'SOURCE_SHA': 'a' * 40,
                                          'WORKSPACE': str(scratch)}),
                  patch.object(suite, 'census', return_value=[]), patch.object(suite, 'checked', side_effect=checked),
                  patch.object(suite, 'command', side_effect=build),
                  patch.object(suite, 'retain_product', return_value={}),
                  patch.object(suite.subprocess, 'Popen', side_effect=launch),
                  patch.object(suite, 'health_and_web', side_effect=failure),
                  patch('scripts.mac_native.suite_toolchain.executor_feasibility', return_value={}),
                  patch('scripts.mac_native.toolchain.provision', return_value={'PATH': '/usr/bin:/bin'})):
                with self.assertRaises(suite.ApiFailure):
                    suite.run(report)
            from scripts.mac_native.collect_packaged import join_exit_receipt
            joined = join_exit_receipt(json.loads(json.dumps(report)),
                'source_sha=' + 'a' * 40 + '\nsuite=packaged\nscratch_removed=true\n', 'a' * 40)
            self.assertEqual(joined['steps']['4']['status'], 'FAIL')
            self.assertEqual(joined['steps']['4'].get('http'), {**row, 'error': 'JSONDecodeError'})
            self.assertEqual(joined['steps']['8']['status'], 'PASS')

    def test_retained_shipping_zip_survives_outer_cleanup_and_keeps_latest_two(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder).resolve()
            scratch = workspace / 'native-probe.test'
            out = scratch / 'packaged/build'
            out.mkdir(parents=True)
            archive = out / 'HappyRanch-1.0.1-1.0.1-arm64.zip'
            archive.write_bytes(b'synthetic verified ZIP bytes')
            hashes = {'app_executable': '1' * 64, 'daemon': '2' * 64, 'cli': '3' * 64}
            identities = []
            for index in range(3):
                receipt = {'filename': archive.name, 'role': 'unsigned-inner-zip',
                    'source_sha': 'a' * 40, 'source_tree': 'b' * 40,
                    'run_id': f'00000000-0000-0000-0000-{index:012d}', 'size': archive.stat().st_size,
                    'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(), 'app_inventory_sha256': '4' * 64}
                (out / 'build-receipt.json').write_text(json.dumps(receipt))
                identities.append(suite.retain_product(out, workspace, scratch, 'a' * 40, 'b' * 40, hashes))
                self.assertLessEqual(len(list((workspace / 'retained').glob('*/*.zip'))), 2)
            self.assertFalse(Path(identities[0]['path']).exists())
            # Execute the existing job's EXIT trap against only this fixture.
            shell = (suite.ROOT / 'ci/jenkins/mac-native-probe/config.xml.in').read_text()
            cleanup = shell[shell.index('cleanup() {'):shell.index('trap cleanup EXIT')]
            (workspace / 'evidence').mkdir()
            result = subprocess.run(['bash', '-ec', cleanup + '\nfinish_console() { exit "$1"; }\ntrap cleanup EXIT'],
                env=dict(os.environ, WORKSPACE=str(workspace), scratch=str(scratch)),
                capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(scratch.exists())
            self.assertEqual((workspace / 'evidence/identity.txt').read_text(), 'scratch_removed=true\n')
            for identity in identities[1:]:
                target = Path(identity['path'])
                self.assertEqual(target.read_bytes(), b'synthetic verified ZIP bytes')
                self.assertEqual(target.stat().st_size, identity['size'])
                self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), identity['sha256'])
                self.assertEqual(identity['bundle_hashes'], hashes)
                self.assertEqual(json.loads(target.with_name('identity.json').read_text()), identity)

    def test_retention_refuses_mismatched_shipping_receipt_and_aliased_store(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder).resolve()
            scratch = workspace / 'scratch'
            out = scratch / 'packaged/build'
            out.mkdir(parents=True)
            archive = out / 'HappyRanch-1.0.1-1.0.1-arm64.zip'
            archive.write_bytes(b'synthetic shipping bytes')
            receipt = {'filename': archive.name, 'role': 'unsigned-inner-zip',
                'source_sha': 'a' * 40, 'source_tree': 'b' * 40, 'run_id': '00000000-0000-0000-0000-000000000000',
                'size': archive.stat().st_size, 'sha256': '0' * 64, 'app_inventory_sha256': '4' * 64}
            (out / 'build-receipt.json').write_text(json.dumps(receipt))
            with self.assertRaisesRegex(RuntimeError, 'differs from strictly verified'):
                suite.retain_product(out, workspace, scratch, 'a' * 40, 'b' * 40, {})
            self.assertFalse((workspace / 'retained').exists())
            receipt['sha256'] = hashlib.sha256(archive.read_bytes()).hexdigest()
            (out / 'build-receipt.json').write_text(json.dumps(receipt))
            outside = workspace / 'unrelated'
            outside.mkdir()
            (workspace / 'retained').symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, 'unsafe retained path'):
                suite.retain_product(out, workspace, scratch, 'a' * 40, 'b' * 40, {})
            self.assertEqual(list(outside.iterdir()), [])

    def archive_fixture(self, root: Path) -> tuple[Path, Path, dict]:
        fixture = runpy.run_path(str(suite.ROOT / 'packaging/verify_manifest_notices.py'))
        source, output, app, mapping = fixture['fixture'](root)
        for directory in (output, output / 'diagnostics', app):
            directory.chmod(0o700)
        fixture['H']['collect'](source, app, output, mapping)
        return output, app, fixture

    def test_archive_membership_names_survive_trap_harness_collection_and_scratch_removal(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            output, _app, fixture = self.archive_fixture(root / 'fixture')
            sha, tree = fixture['SOURCE_SHA'], fixture['SOURCE_TREE']
            extra = ['__MACOSX/HappyRanch.app/._X', '.DS_Store', 'HappyRanch.app/.DS_Store']
            absent = ['HappyRanch.app/Contents/Info.plist',
                      'HappyRanch.app/Contents/Resources/licenses/LICENSE',
                      'HappyRanch.app/Contents/Resources/daemon/_internal/runtime/skills/README.md']
            bindir = root / 'bin'
            bindir.mkdir()
            ditto = bindir / 'ditto'
            ditto.write_text('#!' + sys.executable + '\n' +
                'import pathlib,sys,zipfile\n' +
                'assert sys.argv[1:-2] == ["-c", "-k", "--norsrc", "--noextattr", "--noqtn", "--noacl", "--keepParent"]\n' +
                'app=pathlib.Path(sys.argv[-2])\n' +
                'with zipfile.ZipFile(sys.argv[-1], "x", zipfile.ZIP_DEFLATED) as z:\n' +
                ' for p in sorted(app.rglob("*")):\n' +
                '  name="HappyRanch.app/"+p.relative_to(app).as_posix()\n' +
                '  if name not in ' + repr(absent) + ': z.write(p,name)\n' +
                ' for name in ' + repr(extra) + ': z.writestr(name,b"injected extra")\n')
            ditto.chmod(0o755)
            build = (suite.ROOT / 'app/mac/scripts/build-app.sh').read_text()
            finish = build[build.index('finish() {'):build.index('trap finish EXIT')]
            real_command = suite.command
            shipping_archive = suite.ROOT / 'app/mac/scripts/archive-app.sh'
            source = root / 'checkout'
            source.mkdir()
            report = {'suite': 'packaged', 'platform': 'darwin'}
            build_out = root / 'packaged/build'

            def failed_build(argv: list[str], *_args) -> dict:
                out = Path(argv[argv.index('--output-dir') + 1])
                self.assertEqual(out, build_out)
                shutil.move(str(output), str(out))
                archive = ['bash', str(shipping_archive), str(out / 'HappyRanch.app'), str(out),
                           '1.0.1', '1.0.1', sha, tree, 'arm64', 'synthetic']
                script = finish + '\ntrap finish EXIT\n' + shlex.join(archive)
                env = dict(os.environ, PATH=str(bindir) + ':' + os.environ['PATH'],
                           OUTPUT=str(out), STAGE='archive', RUN_ID='synthetic',
                           SOURCE_SHA=sha, SOURCE_TREE=tree, VERSION='1.0.1', BUILD='1.0.1', ARCH='arm64')
                return real_command(['bash', '-ec', script], root, env, 30)

            with (patch.object(suite, 'ROOT', source),
                  patch.dict(os.environ, {'TMPDIR': str(root / 'tmp'), 'SOURCE_SHA': sha}),
                  patch.object(suite, 'census', return_value=[]),
                  patch.object(suite, 'checked', return_value={'tail': sha}),
                  patch.object(suite, 'command', side_effect=failed_build),
                  patch('scripts.mac_native.suite_toolchain.executor_feasibility', return_value={}),
                  patch('scripts.mac_native.toolchain.provision', return_value={})):
                with self.assertRaisesRegex(RuntimeError, 'documented app build failed'):
                    suite.run(report)
            kept = report['packaged']['build_failure_evidence']
            self.assertEqual(kept['status'], 'COMPLETE', report)
            failure = kept['report']
            self.assertEqual(failure['error'], 'ARCHIVE_IDENTITY_MISMATCH')
            for key, names in (('extra', extra), ('absent', absent)):
                self.assertEqual(failure['detail'][key], sorted(names))
                self.assertEqual(failure['detail'][key + '_count'], len(names))
                self.assertEqual(failure['detail'][key + '_omitted'], 0)
            self.assertEqual(report['steps']['1']['status'], 'FAIL')
            bounded = build_out / 'diagnostics/bounded'
            index = json.loads((bounded / 'evidence-index.json').read_text())
            row = next(r for r in index['inputs'] if r['name'] == 'resource-failure.json')
            self.assertTrue(row['complete'])
            self.assertEqual(json.loads((bounded / row['artifact']).read_bytes()), failure)
            self.assertEqual(kept['sha256'], hashlib.sha256(
                (build_out / 'diagnostics/resource-failure.json').read_bytes()).hexdigest())
            probe = root / 'probe.json'
            probe.write_text(json.dumps(report))
            identity = root / 'identity.txt'
            identity.write_text('source_sha=' + sha + '\nsuite=packaged\nscratch_removed=true\n')
            shutil.rmtree(build_out)
            self.assertFalse(build_out.exists())
            collected = root / 'collected.json'
            result = subprocess.run([sys.executable, '-B', str(suite.ROOT / 'scripts/mac_native/collect_packaged.py'),
                '--probe', str(probe), '--identity', str(identity), '--source-sha', sha,
                '--output', str(collected)], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            final = json.loads(collected.read_bytes())
            self.assertEqual(final['packaged']['build_failure_evidence'], kept)
            self.assertEqual(final['steps']['8']['status'], 'PASS')

    def test_archive_membership_diagnostics_cover_empty_classes_duplicates_and_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            _output, app, _fixture = self.archive_fixture(root)
            members = {'HappyRanch.app/' + p.relative_to(app).as_posix() + ('/' if p.is_dir() else ''): p
                       for p in sorted(app.rglob('*'))}
            archive = root / 'candidate.zip'
            ordinary = 'HappyRanch.app/Contents/Info.plist'
            resource = 'HappyRanch.app/Contents/Resources/daemon/_internal/runtime/skills/README.md'
            notice = 'HappyRanch.app/Contents/Resources/licenses/LICENSE'
            long_names = ['HappyRanch.app/extra/' + f'{i:03d}' + '\x01\u2603' * 200 for i in range(70)]
            cases = [('extra', ['HappyRanch.app/.DS_Store'], [], False),
                     ('ordinary absent', [], [ordinary], False),
                     ('resource absent', [], [resource], False),
                     ('notice absent', [], [notice], False),
                     ('duplicate', [], [], True),
                     ('bounded', long_names, sorted(members)[:70], False)]
            for case, extra, absent, duplicate in cases:
                with self.subTest(case=case):
                    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
                        for name, path in members.items():
                            if name not in absent:
                                z.write(path, name)
                        for name in extra:
                            z.writestr(name, b'injected extra')
                        if duplicate:
                            # Duplicate app-root entries do not change set membership.
                            z.writestr('HappyRanch.app/', b'')
                            with warnings.catch_warnings():
                                warnings.simplefilter('ignore', UserWarning)
                                z.writestr('HappyRanch.app/', b'')
                    evidence = root / (case + '.json')
                    result = subprocess.run(['bash', str(suite.ROOT / 'app/mac/scripts/verify-release.sh'),
                        'archive', '--app', str(app), '--archive', str(archive), '--run-id', 'synthetic',
                        '--resource-evidence', str(evidence)], capture_output=True, text=True, timeout=15)
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertTrue(evidence.is_file(), result.stderr)
                    raw = evidence.read_bytes()
                    failure = json.loads(raw)
                    self.assertEqual(failure, json.loads(result.stderr))
                    self.assertEqual(failure['error'], 'ARCHIVE_IDENTITY_MISMATCH')
                    detail = failure['detail']
                    self.assertEqual(detail['duplicate_count'], int(duplicate))
                    for key, names in (('extra', extra), ('absent', absent)):
                        self.assertEqual(detail[key + '_count'], len(names))
                        self.assertEqual(detail[key + '_omitted'], max(0, len(names) - 64))
                        self.assertEqual(len(detail[key]), min(64, len(names)))
                        self.assertEqual(detail[key], sorted(detail[key]))
                        if case != 'bounded':
                            self.assertEqual(detail[key], sorted(names))
                            self.assertEqual(detail[key + '_truncated_names'], 0)
                        self.assertTrue(all(len(json.dumps(name).encode()) <= 256 for name in detail[key]))
                    if case == 'bounded':
                        self.assertEqual(detail['extra_truncated_names'], 64)
                        self.assertEqual(detail['absent_count'], 70)
                        self.assertLess(len(raw), 49152)

    def resource_fixture(self, root: Path) -> tuple[Path, list[dict], str, str]:
        script = suite.ROOT / 'app/mac/scripts/verify-release.sh'
        text = script.read_text().split("<<'PYTHON'\n", 1)[1].rsplit('\nPYTHON', 1)[0]
        baseline = next(node.value for node in ast.parse(text).body
                        if isinstance(node, ast.Assign) and any(
                            isinstance(target, ast.Name) and target.id == 'BASELINE'
                            for target in node.targets))
        rows = ast.literal_eval(baseline)
        self.assertEqual(len(rows), 29)
        sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=suite.ROOT, text=True).strip()
        tree = subprocess.check_output(['git', 'rev-parse', 'HEAD^{tree}'], cwd=suite.ROOT, text=True).strip()
        app = root / 'HappyRanch.app'
        (app / 'Contents').mkdir(parents=True)
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({'GitCommitSHA': sha, 'GitTreeSHA': tree}))
        for row in rows:
            relative = ('Contents/Resources/licenses/LICENSE' if row['path'] == 'LICENSE' else
                        'Contents/Resources/daemon/_internal/' + row['path'])
            row['member'] = relative
            raw = subprocess.check_output(['git', 'show', sha + ':' + row['path']], cwd=suite.ROOT)
            target = app / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            target.chmod(int(row['posix_mode'], 8))
        return app, rows, sha, tree

    def resource_command(self, app: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(['bash', str(suite.ROOT / 'app/mac/scripts/verify-release.sh'),
            'resources', '--app', str(app), *args], capture_output=True, text=True, timeout=15)

    def test_all_29_resources_match_exact_head_and_reject_bundle_archive_changes(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app, rows, sha, _tree = self.resource_fixture(root)
            good = self.resource_command(app)
            self.assertEqual(good.returncode, 0, good.stderr)
            observed = json.loads(good.stdout)['resources']
            self.assertEqual(len(observed), 29)
            for row, actual in zip(rows, observed, strict=True):
                raw = subprocess.check_output(['git', 'show', sha + ':' + row['path']], cwd=suite.ROOT)
                self.assertEqual((actual['size'], actual['sha256'], actual['posix_mode']),
                                 (len(raw), hashlib.sha256(raw).hexdigest(), row['posix_mode']))
            victim = app / next(row['member'] for row in rows if row['path'].endswith('/dream/SKILL.md'))
            original = victim.read_bytes()
            try:
                victim.write_bytes(bytes([original[0] ^ 1]) + original[1:])
                bad = self.resource_command(app)
                self.assertEqual(bad.returncode, 1, bad.stderr)
                failure = json.loads(bad.stderr)
                self.assertEqual(failure['error'], 'RESOURCE_VERIFICATION_FAILED')
                self.assertEqual([row['error'] for row in failure['detail']], ['RESOURCE_MISMATCH'])
                self.assertEqual(failure['detail'][0]['expected']['sha256'], hashlib.sha256(original).hexdigest())
            finally:
                victim.write_bytes(original)
            self.assertEqual(victim.read_bytes(), original)
            archive = root / 'candidate.zip'
            def write_archive(corrupt: bool) -> None:
                with zipfile.ZipFile(archive, 'w') as z:
                    for row in rows:
                        path = app / row['member']
                        entry = zipfile.ZipInfo('HappyRanch.app/' + row['member'])
                        entry.external_attr = (stat.S_IFREG | int(row['posix_mode'], 8)) << 16
                        raw = path.read_bytes()
                        z.writestr(entry, raw + b'changed' if corrupt and path == victim else raw)
            write_archive(False)
            original_zip = archive.read_bytes()
            try:
                write_archive(True)
                bad = self.resource_command(app, '--archive', str(archive))
                self.assertEqual(bad.returncode, 1, bad.stderr)
                self.assertEqual([row['error'] for row in json.loads(bad.stderr)['detail']], ['RESOURCE_MISMATCH'])
            finally:
                archive.write_bytes(original_zip)
            self.assertEqual(archive.read_bytes(), original_zip)
            restored = self.resource_command(app, '--archive', str(archive))
            self.assertEqual(restored.returncode, 0, restored.stderr)

    def test_resource_expectations_ignore_changed_live_source(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ):
            os.environ.pop('GIT_NO_REPLACE_OBJECTS', None)
            os.environ.pop('GIT_REPLACE_REF_BASE', None)
            root = Path(folder)
            app, rows, _sha, _tree = self.resource_fixture(root)
            source = root / 'source'
            source.mkdir()
            for row in rows:
                target = source / row['path']
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((app / row['member']).read_bytes())
                target.chmod(int(row['posix_mode'], 8))
            def git(*args: str) -> str:
                return subprocess.check_output(['git', '-C', str(source), *args], text=True, stderr=subprocess.PIPE).strip()
            git('init', '-q')
            git('add', '.')
            git('-c', 'user.name=Synthetic Test', '-c', 'user.email=synthetic@example.invalid',
                'commit', '-qm', 'synthetic committed resources')
            sha, tree = git('rev-parse', 'HEAD'), git('rev-parse', 'HEAD^{tree}')
            (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({'GitCommitSHA': sha, 'GitTreeSHA': tree}))
            (source / rows[0]['path']).write_bytes(b'live checkout differs from committed bytes')
            good = self.resource_command(app, '--source', str(source), '--source-sha', sha, '--source-tree', tree)
            self.assertEqual(good.returncode, 0, good.stderr)
            bad = self.resource_command(app, '--source', str(source), '--source-tree', '0' * 40)
            self.assertEqual(bad.returncode, 1)
            self.assertEqual(json.loads(bad.stderr)['error'], 'SOURCE_TREE_MISMATCH')
            (source / rows[0]['path']).write_bytes((app / rows[0]['member']).read_bytes())
            self.assertEqual(git('status', '--porcelain'), '')
            victim = app / 'Contents/Resources/licenses/LICENSE'
            original = victim.read_bytes()
            original_oid = git('rev-parse', sha + ':LICENSE')
            replacement = root / 'replacement'
            replacement.write_bytes(b'replacement resource must not become committed data\n')
            replacement_oid = git('hash-object', '-w', str(replacement))
            git('replace', original_oid, replacement_oid)
            try:
                self.assertEqual(git('cat-file', 'blob', original_oid), replacement.read_text().strip())
                self.assertEqual((git('rev-parse', 'HEAD'), git('rev-parse', 'HEAD^{tree}')), (sha, tree))
                self.assertEqual(git('status', '--porcelain'), '')
                good = self.resource_command(app, '--source', str(source), '--source-sha', sha, '--source-tree', tree)
                with self.subTest(bundle='committed bytes with local replacement'):
                    self.assertEqual(good.returncode, 0, good.stderr)
                victim.write_bytes(replacement.read_bytes())
                bad = self.resource_command(app, '--source', str(source), '--source-sha', sha, '--source-tree', tree)
                with self.subTest(bundle='replacement bytes with local replacement'):
                    self.assertEqual(bad.returncode, 1, bad.stderr)
                    failure = json.loads(bad.stderr)
                    self.assertEqual(failure['error'], 'RESOURCE_VERIFICATION_FAILED')
                    self.assertEqual([row['error'] for row in failure['detail']], ['RESOURCE_MISMATCH'])
                    self.assertEqual(failure['detail'][0]['expected']['sha256'], hashlib.sha256(original).hexdigest())
            finally:
                victim.write_bytes(original)
                git('replace', '-d', original_oid)
            self.assertEqual(victim.read_bytes(), original)
            self.assertEqual(git('for-each-ref', 'refs/replace'), '')
            self.assertEqual(git('status', '--porcelain'), '')
            self.assertEqual(self.resource_command(app, '--source', str(source)).returncode, 0)

    def test_resource_failure_artifact_keeps_all_rows_before_scratch_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app, rows, _sha, _tree = self.resource_fixture(root)
            diag = root / 'diagnostics'
            diag.mkdir()
            evidence = diag / 'resource-failure.json'
            originals = {app / row['member']: (app / row['member']).read_bytes() for row in rows}
            try:
                for path in originals:
                    path.write_bytes(b'corrupt')
                bad = self.resource_command(app, '--resource-evidence', str(evidence))
                self.assertEqual(bad.returncode, 1, bad.stderr)
                failure = json.loads(bad.stderr)
                self.assertEqual(len(failure['detail']), 29)
                kept = suite.build_failure_evidence(root)
                self.assertEqual(kept['status'], 'COMPLETE')
                self.assertEqual(kept['report'], failure)
                self.assertEqual(kept['bytes'], evidence.stat().st_size)
                self.assertEqual(kept['sha256'], hashlib.sha256(evidence.read_bytes()).hexdigest())
                self.assertGreater(kept['bytes'], 2048)
                self.assertLess(kept['bytes'], 49152)

                source = root / 'source'
                source.mkdir()
                report = {'suite': 'packaged', 'platform': 'darwin'}
                def failed_build(argv: list[str], *_args) -> dict:
                    out = Path(argv[argv.index('--output-dir') + 1])
                    target = out / 'diagnostics/resource-failure.json'
                    target.parent.mkdir(parents=True)
                    target.write_bytes(evidence.read_bytes())
                    return {'exit_code': 1}
                with (patch.object(suite, 'ROOT', source),
                      patch.dict(os.environ, {'TMPDIR': str(root / 'tmp'), 'SOURCE_SHA': _sha}),
                      patch.object(suite, 'census', return_value=[]),
                      patch.object(suite, 'checked', return_value={'tail': _sha}),
                      patch.object(suite, 'command', side_effect=failed_build),
                      patch('scripts.mac_native.suite_toolchain.executor_feasibility', return_value={}),
                      patch('scripts.mac_native.toolchain.provision', return_value={})):
                    with self.assertRaisesRegex(RuntimeError, 'documented app build failed'):
                        suite.run(report)
                self.assertEqual(report['steps']['1']['status'], 'FAIL')
                self.assertEqual(report['packaged']['build_failure_evidence'], kept)
                build = (suite.ROOT / 'app/mac/scripts/build-app.sh').read_text()
                finish = build[build.index('finish() {'):build.index('trap finish EXIT')]
                sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=suite.ROOT, text=True).strip()
                tree = subprocess.check_output(['git', 'rev-parse', 'HEAD^{tree}'], cwd=suite.ROOT, text=True).strip()
                trapped = subprocess.run(['bash', '-ec', finish + '\ntrap finish EXIT\nexit 1'],
                    env=dict(os.environ, OUTPUT=str(root), STAGE='verification', RUN_ID='synthetic',
                             SOURCE_SHA=sha, SOURCE_TREE=tree, VERSION='1.0.1', BUILD='1.0.1',
                             ARCH='arm64', INSPECTION_STAGE='resources'), capture_output=True, text=True, timeout=10)
                self.assertEqual(trapped.returncode, 1, trapped.stderr)
                index = json.loads((diag / 'bounded/evidence-index.json').read_text())
                row = next(row for row in index['inputs'] if row['name'] == 'resource-failure.json')
                self.assertTrue(row['complete'])
                self.assertEqual(json.loads((diag / 'bounded' / row['artifact']).read_bytes()), failure)
                # Existing collector must retain a full failure-shaped probe under its cap.
                probe = report
                (root / 'probe.json').write_text(json.dumps(probe))
                (root / 'identity.txt').write_text('source_sha=' + sha + '\nsuite=packaged\nscratch_removed=true\n')
                collected = root / 'collected.json'
                result = subprocess.run([sys.executable, '-B', str(suite.ROOT / 'scripts/mac_native/collect_packaged.py'),
                    '--probe', str(root / 'probe.json'), '--identity', str(root / 'identity.txt'),
                    '--source-sha', sha, '--output', str(collected)], capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(collected.read_bytes())['packaged']['build_failure_evidence']['report'], failure)

            finally:
                for path, raw in originals.items():
                    path.write_bytes(raw)
            self.assertTrue(all(path.read_bytes() == raw for path, raw in originals.items()))
            good = self.resource_command(app)
            self.assertEqual(good.returncode, 0, good.stderr)

    def test_packaged_consumes_completed_app_at_documented_build_output(self) -> None:
        final_move = next(line for line in (suite.ROOT / 'app/mac/scripts/build-app.sh').read_text().splitlines()
                          if line.startswith('mv "$APP" '))
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder).resolve()
            scratch = workspace / 'native-probe.test'
            scratch.mkdir()
            source = scratch / 'source'
            source.mkdir()
            report = {}
            def build(argv: list[str], _cwd: Path, _env: dict[str, str], _seconds: float) -> dict:
                out = Path(argv[argv.index('--output-dir') + 1])
                app = out / 'scratch/HappyRanch.app'
                for name in ('Contents/MacOS/HappyRanch', 'Contents/Resources/daemon/happyranch-daemon',
                             'Contents/Resources/daemon/happyranch'):
                    path = app / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b'synthetic executable; never run')
                subprocess.run(['bash', '-ec', final_move], check=True,
                               env=dict(os.environ, APP=str(app), OUTPUT=str(out)), timeout=5)
                archive = out / 'HappyRanch-1.0.1-1.0.1-arm64.zip'
                archive.write_bytes(b'synthetic verified archive')
                (out / 'build-receipt.json').write_text(json.dumps({
                    'filename': archive.name, 'role': 'unsigned-inner-zip',
                    'source_sha': 'a' * 40, 'source_tree': 'a' * 40,
                    'run_id': '00000000-0000-0000-0000-000000000000',
                    'size': archive.stat().st_size, 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                    'app_inventory_sha256': '4' * 64}))
                return {'exit_code': 0}
            def checked(argv: list[str], *_args, **_kwargs) -> dict:
                if argv[0] == '/usr/bin/ditto':
                    raise RuntimeError('stop after completed app observation')
                return {'tail': 'a' * 40}
            with (patch.object(suite, 'ROOT', source),
                  patch.dict(os.environ, {'TMPDIR': str(scratch / 'tmp'), 'SOURCE_SHA': 'a' * 40,
                                          'WORKSPACE': str(workspace)}),
                  patch.object(suite, 'census', return_value=[]), patch.object(suite, 'checked', side_effect=checked),
                  patch.object(suite, 'command', side_effect=build),
                  patch('scripts.mac_native.suite_toolchain.executor_feasibility', return_value={}),
                  patch('scripts.mac_native.toolchain.provision', return_value={})):
                with self.assertRaisesRegex(RuntimeError, 'stop after completed app observation'):
                    suite.run(report)
            self.assertEqual(report['steps']['1']['status'], 'PASS')
            self.assertEqual(set(report['packaged']['bundle_hashes']), {'app_executable', 'daemon', 'cli'})
            self.assertIn('retained_product', report['packaged'])
            self.assertTrue(Path(report['packaged']['retained_product']['path']).is_file())

    def test_collector_joins_only_exact_outer_exit_receipts(self) -> None:
        sha = 'a' * 40
        probe = {'suite': 'packaged', 'platform': 'darwin', 'steps': suite.initial_steps(),
                 'packaged': {'source_sha': sha}}
        identity = 'source_sha=' + sha + '\nsuite=packaged\n'
        cases = [('', 'NOT_RUN', 0), ('scratch_removed=false\n', 'FAIL', 0),
                 ('scratch_removed=true\n', 'PASS', 0),
                 ('scratch_removed=true\nscratch_removed=false\n', None, 2),
                 ('scratch_removed=unknown\n', None, 2),
                 ('source_sha=' + 'b' * 40 + '\n', None, 2),
                 ('suite=toolchain\n', None, 2)]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original = json.dumps(probe).encode()
            (root / 'probe.json').write_bytes(original)
            for index, (receipt, expected, code) in enumerate(cases):
                with self.subTest(receipt=receipt):
                    raw = (identity + receipt).encode()
                    (root / 'identity.txt').write_bytes(raw)
                    out = root / f'collected-{index}.json'
                    result = subprocess.run([sys.executable, '-B',
                        str(suite.ROOT / 'scripts/mac_native/collect_packaged.py'),
                        '--probe', str(root / 'probe.json'), '--identity', str(root / 'identity.txt'),
                        '--source-sha', sha, '--output', str(out)],
                        capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, code, result.stderr)
                    if code == 0:
                        joined = json.loads(out.read_text())
                        self.assertEqual(joined['steps']['8']['status'], expected)
                        for step in map(str, range(1, 8)):
                            self.assertEqual(joined['steps'][step], probe['steps'][step])
                    else:
                        self.assertFalse(out.exists())
                    self.assertEqual((root / 'probe.json').read_bytes(), original)
                    self.assertEqual((root / 'identity.txt').read_bytes(), raw)

    def test_freezer_venv_passes_patch_request_to_uv(self) -> None:
        # uv consumes this shell argv independently of our Python readers.
        line = next(line for line in (suite.ROOT / 'packaging/build_daemon.sh').read_text().splitlines()
                    if line.startswith('uv venv '))
        result = subprocess.run(['bash', '-c', '''
PYTHON_REQUEST=cpython@3.12.10
MANAGED_PYTHON=/private/python-install/cpython-3.12.10/bin/python3.12
REPO_ROOT=/private/source
uv() { printf '%s\\n' "$@"; }
''' + line], capture_output=True, text=True, check=True, timeout=5)
        args = result.stdout.splitlines()
        self.assertEqual(args[args.index('--python') + 1], 'cpython@3.12.10')
        self.assertIn('--managed-python', args)
        self.assertIn('--no-python-downloads', args)
        self.assertEqual(args[-1], '/private/source/.venv')

    def test_packaged_build_modcache_writable_only_in_child_environment(self) -> None:
        inherited = {'PATH': '/synthetic/tools', 'GOFLAGS': '-mod=readonly',
                     'PYTHONUSERBASE': '/job/bootstrap'}
        seen = []
        def build(_argv: list[str], _cwd: Path, env: dict[str, str], _seconds: float) -> dict:
            seen.append(dict(env))
            return {'exit_code': 7}
        with tempfile.TemporaryDirectory() as folder:
            scratch = Path(folder).resolve()
            source = scratch / 'source'
            source.mkdir()
            with (patch.object(suite, 'ROOT', source),
                  patch.dict(os.environ, {'TMPDIR': str(scratch / 'tmp'), 'SOURCE_SHA': 'a' * 40}),
                  patch.object(suite, 'census', return_value=[]),
                  patch.object(suite, 'checked', return_value={'tail': 'a' * 40}),
                  patch.object(suite, 'command', side_effect=build),
                  patch('scripts.mac_native.suite_toolchain.executor_feasibility', return_value={}),
                  patch('scripts.mac_native.toolchain.provision', return_value=inherited)):
                with self.assertRaisesRegex(RuntimeError, 'documented app build failed'):
                    suite.run({})
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]['GOFLAGS'], '-mod=readonly -modcacherw')
        self.assertNotIn('PYTHONUSERBASE', seen[0])
        self.assertEqual(inherited, {'PATH': '/synthetic/tools', 'GOFLAGS': '-mod=readonly',
                                     'PYTHONUSERBASE': '/job/bootstrap'})

    def test_linux_dispatch_has_eight_not_run_steps(self):
        if sys.platform == 'darwin':
            self.skipTest('Linux skip boundary only; never build in portable tests')
        result = subprocess.run([sys.executable, '-B', str(suite.ROOT / 'scripts/mac_native/probe.py'),
                                 '--suite', 'packaged'], capture_output=True, text=True,
                                timeout=10, check=True)
        report = json.loads(result.stdout)
        self.assertTrue(report['skipped'])
        self.assertEqual(set(report['steps']), {str(i) for i in range(1, 9)})
        self.assertTrue(all(row['status'] == 'NOT_RUN' and 'SKIPPED' in row['reason']
                            for row in report['steps'].values()))
        self.assertNotIn('packaged', report)

    def test_census_joins_children_groups_and_scratch_without_args(self):
        rows = suite.parse_census('\n'.join(f'{pid} {ppid} {pgid} S Sat Nov 14 05:00:00 2026 {path}'
            for pid, ppid, pgid, path in ((1, 0, 1, '/sbin/init'), (10, 1, 10, '/scratch/daemon'),
            (11, 10, 11, '/scratch/wrapper'), (12, 11, 11, '/bin/sleep'),
            (13, 1, 13, '/scratch/escaped'), (14, 1, 14, '/scratch-other/tool'))))
        self.assertEqual([r['pid'] for r in suite.descendants(rows, 10)], [10, 11, 12])
        self.assertEqual([r['pid'] for r in suite.owned_rows(rows, Path('/scratch'), {10, 11}, {10})],
                         [11, 12, 13])
        self.assertNotIn('executable', suite.descendants(rows, 10)[0])
        self.assertEqual(suite.descendants(rows, 10)[0]['state'], 'S')
        self.assertEqual(suite.descendants(rows, 10)[0]['start_identity'], 'Sat Nov 14 05:00:00 2026')
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            suite.parse_census('unknown')

    def test_command_preserves_failure_and_bounds_output_and_deadline(self):
        with tempfile.TemporaryDirectory() as folder:
            cwd = Path(folder)
            failed = suite.command([sys.executable, '-c', 'print("failure-evidence"); raise SystemExit(7)'],
                                   cwd, dict(os.environ), 2)
            self.assertEqual(failed['exit_code'], 7)
            self.assertIn('failure-evidence', failed['tail'])
            overflow = suite.command([sys.executable, '-c', 'print("x"*8192)'],
                                     cwd, dict(os.environ), 2, cap=1024)
            self.assertIsNone(overflow['exit_code'])
            self.assertIn('output cap', overflow['error'])
            self.assertLessEqual(len(overflow['tail']), 2048)
            timeout = suite.command([sys.executable, '-c', 'import time; time.sleep(30)'],
                                    cwd, dict(os.environ), .1)
            self.assertIsNone(timeout['exit_code'])
            self.assertIn('deadline', timeout['error'])


if __name__ == '__main__':
    unittest.main()

"""Focused child-launch regressions outside the retired Python unit selection."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import zlib

from runtime.workflows import repository_artifact_objects as objects


ROOT = Path(__file__).absolute().parents[1]


class RepositoryHelperTests(unittest.TestCase):
    def test_child_launch_argv(self) -> None:
        """Observe both actual Popen boundaries, including their isolation env."""
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            record = SimpleNamespace(model_dump=lambda: {})
            for frozen in (False, True):
                for executable in ('/synthetic/happyranch-daemon', '/synthetic/happyranch'):
                    for child, helper in (('--git-child', 'repository-git'),
                                          ('--reader-child', 'repository-reader')):
                        with self.subTest(frozen=frozen, executable=executable, child=child):
                            with (patch.object(sys, 'frozen', frozen, create=True),
                                  patch.object(sys, 'executable', executable),
                                  patch.object(objects.tempfile, 'mkdtemp', return_value=folder),
                                  patch.object(objects.subprocess, 'Popen', side_effect=OSError) as launch):
                                with self.assertRaises(objects.WorkflowRepositoryError):
                                    if child == '--git-child':
                                        objects._run_git(directory, 'sha1', 'a' * 40, 'check', 256, objects._Budget())
                                    else:
                                        objects._resolve_objects(paths=SimpleNamespace(root=directory),
                                            binding=record, adoption=record, proof=record,
                                            descriptor=record, configured_origin='synthetic')
                            argv = launch.call_args.args[0]
                            prefix = ([executable, '--cleanup-helper', helper] if frozen else
                                      [executable, '-I', str(Path(objects.__file__).absolute()), child])
                            self.assertEqual(argv[:len(prefix)], prefix)
                            self.assertEqual(argv[len(prefix)], folder)
                            if child == '--git-child':
                                self.assertEqual(argv[len(prefix) + 1:], ['sha1', 'check'])
                            else:
                                self.assertEqual(len(argv), len(prefix) + 2)
                                self.assertGreater(float(argv[-1]), time.monotonic())
                                self.assertTrue(launch.call_args.kwargs['start_new_session'])
                            environment = launch.call_args.kwargs['env']
                            self.assertNotIn('PYTHONPATH', environment)
                            self.assertNotIn('PYTHONHOME', environment)
                            self.assertEqual(environment['HOME'], '/nonexistent')
                            self.assertEqual(environment['GIT_CONFIG_GLOBAL'], '/dev/null')
                            self.assertEqual(environment['GIT_ALLOW_PROTOCOL'], '')
                            self.assertTrue(launch.call_args.kwargs['close_fds'])

    def test_closed_entrypoints(self) -> None:
        """Both binaries reach the fixed child before normal product startup."""
        with tempfile.TemporaryDirectory() as folder:
            scratch = Path(folder)
            git = scratch / 'objects-only.git'
            (git / 'objects').mkdir(parents=True)
            (git / 'refs').mkdir()
            (git / 'HEAD').write_text('ref: refs/heads/main\n')
            blob = b'blob 7\0fixture'
            oid = hashlib.sha1(blob).hexdigest()
            (git / 'objects' / oid[:2]).mkdir()
            (git / 'objects' / oid[:2] / oid[2:]).write_bytes(zlib.compress(blob))
            for entry in ('runtime.daemon', 'cli.main'):
                for mode, expected in (('check', f'{oid} blob 7\n'.encode()),
                                       ('body', f'{oid} blob 7\nfixture\n'.encode())):
                    with self.subTest(entry=entry, mode=mode):
                        result = subprocess.run([sys.executable, '-B', '-m', entry,
                            '--cleanup-helper', 'repository-git', str(git), 'sha1', mode],
                            input=oid.encode() + b'\n', capture_output=True, cwd=ROOT, timeout=10)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(result.stdout, expected)
                with self.subTest(entry=entry, child='reader'):
                    result = subprocess.run([sys.executable, '-B', '-m', entry,
                        '--cleanup-helper', 'repository-reader', str(scratch), str(time.monotonic() + 5)],
                        input=b'{}', capture_output=True, cwd=ROOT, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(result.stdout)['error'], 'workflow_repository_storage_corrupt')
                for arguments in ([], ['unknown'], [str(objects.__file__)],
                                  ['repository-git'], ['repository-git', str(git), 'md5', 'check'],
                                  ['repository-git', str(git), 'sha1', 'other'],
                                  ['repository-git', str(git), 'sha1', 'check', 'extra'],
                                  ['repository-reader'], ['repository-reader', str(scratch), '1', 'extra']):
                    with self.subTest(entry=entry, refused=arguments):
                        result = subprocess.run([sys.executable, '-B', '-m', entry,
                            '--cleanup-helper', *arguments], capture_output=True, cwd=ROOT, timeout=10,
                            env={**os.environ, 'HAPPYRANCH_DAEMON_HOME': str(scratch / 'absent-home')})
                        self.assertEqual(result.returncode, 2, result.stderr)
                        self.assertFalse((scratch / 'absent-home').exists())

    def test_git_dispatch_is_stdlib_only_and_limited(self) -> None:
        code = '''
import builtins, os, resource, runpy, sys
entry = sys.argv[1]
inherited_as = resource.getrlimit(resource.RLIMIT_AS)
sys.argv = [entry, '--cleanup-helper', 'repository-git', '/nonexistent', 'sha1', 'check']
original_import, original_exec = builtins.__import__, os.execve
def guarded_import(name, *args, **kwargs):
    if name in ('uvicorn', 'runtime.workflows') or name.startswith('cli.commands'):
        raise RuntimeError('product import before Git limits')
    return original_import(name, *args, **kwargs)
def limited_exec(*args):
    expected_as = inherited_as if sys.platform == 'darwin' else (134217728, 134217728)
    assert resource.getrlimit(resource.RLIMIT_AS) == expected_as
    for limit, expected in ((resource.RLIMIT_CPU, 5),
                           (resource.RLIMIT_CORE, 0), (resource.RLIMIT_FSIZE, 0),
                           (resource.RLIMIT_NOFILE, 32)):
        assert resource.getrlimit(limit) == (expected, expected)
    os.write(2, b'LIMITS-SET\\n')
    original_exec(*args)
builtins.__import__, os.execve = guarded_import, limited_exec
runpy.run_module(entry, run_name='__main__')
'''
        for entry in ('runtime.daemon', 'cli.main'):
            with self.subTest(entry=entry):
                result = subprocess.run([sys.executable, '-B', '-c', code, entry],
                    capture_output=True, cwd=ROOT, timeout=10)
                self.assertIn(b'LIMITS-SET', result.stderr)
                self.assertIn(b'not a git repository', result.stderr)
                self.assertNotIn(b'product import before Git limits', result.stderr)

    def test_children_platform_limits(self) -> None:
        """Darwin AS rejection and Linux caps both permit real child output."""
        code = '''
import builtins, os, resource, runpy, sys, time
entry, child, platform = sys.argv[1:]
native_platform = sys.platform
inherited_as = resource.getrlimit(resource.RLIMIT_AS)
original_set, original_import, original_exec = resource.setrlimit, builtins.__import__, os.execve
def set_limit(limit, value):
    if limit == resource.RLIMIT_AS and platform == 'darwin':
        # CPython maps Darwin setrlimit EINVAL to this ValueError.
        raise ValueError('current limit exceeds maximum limit')
    original_set(limit, value)
    # Only limit selection is simulated; product imports use the real host.
    sys.platform = native_platform
def check_limits():
    size = 134217728 if child == 'repository-git' else 268435456
    expected_as = inherited_as if platform == 'darwin' else (size, size)
    assert resource.getrlimit(resource.RLIMIT_AS) == expected_as
    for limit, expected in ((resource.RLIMIT_CPU, 5), (resource.RLIMIT_CORE, 0),
                           (resource.RLIMIT_NOFILE, 32 if child == 'repository-git' else 64)):
        assert resource.getrlimit(limit) == (expected, expected)
    if child == 'repository-git':
        assert resource.getrlimit(resource.RLIMIT_FSIZE) == (0, 0)
    os.write(2, b'LIMITS-SET\\n')
def guarded_import(name, *args, **kwargs):
    if name == 'runtime.orchestrator._paths':
        check_limits()
    return original_import(name, *args, **kwargs)
def limited_exec(*args):
    check_limits()
    original_exec(*args)
resource.setrlimit, builtins.__import__, os.execve = set_limit, guarded_import, limited_exec
sys.argv = [entry, '--cleanup-helper', child, '/nonexistent',
            *(['sha1', 'check'] if child == 'repository-git' else [str(time.monotonic() + 5)])]
sys.platform = platform
sys.frozen = True
runpy.run_module(entry, run_name='__main__')
'''
        for entry in ('runtime.daemon', 'cli.main'):
            for child in ('repository-git', 'repository-reader'):
                for platform in ('linux', 'darwin'):
                    with self.subTest(entry=entry, child=child, platform=platform):
                        result = subprocess.run([sys.executable, '-B', '-c', code, entry, child, platform],
                            input=b'{}', capture_output=True, cwd=ROOT, timeout=10)
                        self.assertIn(b'LIMITS-SET', result.stderr, result.stderr)
                        if child == 'repository-git':
                            self.assertEqual(result.returncode, 128, result.stderr)
                            self.assertIn(b'not a git repository', result.stderr)
                        else:
                            self.assertEqual(result.returncode, 0, result.stderr)
                            self.assertEqual(json.loads(result.stdout)['error'],
                                             'workflow_repository_storage_corrupt')

    def test_resource_failures_stop_children(self) -> None:
        """Every applied limit remains mandatory before either child can work."""
        code = '''
import resource, runpy, sys, time
entry, child, platform, failing = sys.argv[1:]
native_platform = sys.platform
original_set = resource.setrlimit
def set_limit(limit, value):
    if limit != resource.RLIMIT_AS:
        sys.platform = native_platform
    if limit == getattr(resource, failing):
        raise ValueError('synthetic limit refusal')
    original_set(limit, value)
resource.setrlimit = set_limit
sys.argv = [entry, '--cleanup-helper', child, '/nonexistent',
            *(['sha1', 'check'] if child == 'repository-git' else [str(time.monotonic() + 5)])]
sys.platform = platform
runpy.run_module(entry, run_name='__main__')
'''
        for entry in ('runtime.daemon', 'cli.main'):
            for child in ('repository-git', 'repository-reader'):
                for platform in ('linux', 'darwin'):
                    limits = ['RLIMIT_CPU', 'RLIMIT_CORE', 'RLIMIT_NOFILE']
                    if platform == 'linux':
                        limits.insert(0, 'RLIMIT_AS')
                    if child == 'repository-git':
                        limits.append('RLIMIT_FSIZE')
                    for limit in limits:
                        with self.subTest(entry=entry, child=child, platform=platform, limit=limit):
                            result = subprocess.run([sys.executable, '-B', '-c', code,
                                entry, child, platform, limit], input=b'{}', capture_output=True,
                                cwd=ROOT, timeout=10)
                            self.assertEqual(result.returncode, 1, result.stderr)
                            self.assertIn(b'ValueError: synthetic limit refusal', result.stderr)
                            self.assertEqual(result.stdout, b'')

    def test_source_git_isolated_from_python_environment(self) -> None:
        from runtime.daemon.cleanup_helpers import helper_argv
        with tempfile.TemporaryDirectory() as folder:
            scratch = Path(folder)
            for name in ('sitecustomize.py', 'resource.py'):
                (scratch / name).write_text("raise RuntimeError('ambient import executed')\n")
            result = subprocess.run([*helper_argv('repository-git'), folder, 'sha1', 'check'],
                input=b'a' * 40 + b'\n', capture_output=True,
                cwd=scratch, timeout=10, env={**os.environ, 'PYTHONPATH': folder, 'PYTHONUSERBASE': folder})
            # A real Git error establishes that the shim passed Python import/startup.
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'not a git repository', result.stderr)
            self.assertNotIn(b'ambient import executed', result.stderr)


if __name__ == '__main__':
    unittest.main()

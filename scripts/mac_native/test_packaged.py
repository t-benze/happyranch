"""Portable harness contracts only; no native/product execution claim."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.mac_native import suite_packaged as suite


class PackagedHarnessTests(unittest.TestCase):
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
        rows = suite.parse_census('1 0 1 /sbin/init\n10 1 10 /scratch/daemon\n'
                                  '11 10 11 /scratch/wrapper\n12 11 11 /bin/sleep\n'
                                  '13 1 13 /scratch/escaped\n14 1 14 /scratch-other/tool\n')
        self.assertEqual([r['pid'] for r in suite.descendants(rows, 10)], [10, 11, 12])
        self.assertEqual([r['pid'] for r in suite.owned_rows(rows, Path('/scratch'), {10, 11}, {10})],
                         [11, 12, 13])
        self.assertNotIn('executable', suite.descendants(rows, 10)[0])
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

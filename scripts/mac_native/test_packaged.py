"""Portable harness contracts only; no native/product execution claim."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.mac_native import suite_packaged as suite


class PackagedHarnessTests(unittest.TestCase):
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

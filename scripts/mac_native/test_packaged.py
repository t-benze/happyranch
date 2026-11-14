"""Portable harness contracts only; no native/product execution claim."""
from __future__ import annotations

import ast
import hashlib
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
import unittest
import warnings
from unittest.mock import patch

from scripts.mac_native import suite_packaged as suite


class PackagedHarnessTests(unittest.TestCase):
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
            scratch = Path(folder).resolve()
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
                return {'exit_code': 0}
            def checked(argv: list[str], *_args, **_kwargs) -> dict:
                if argv[0] == '/usr/bin/ditto':
                    raise RuntimeError('stop after completed app observation')
                return {'tail': 'a' * 40}
            with (patch.object(suite, 'ROOT', source),
                  patch.dict(os.environ, {'TMPDIR': str(scratch / 'tmp'), 'SOURCE_SHA': 'a' * 40}),
                  patch.object(suite, 'census', return_value=[]), patch.object(suite, 'checked', side_effect=checked),
                  patch.object(suite, 'command', side_effect=build),
                  patch('scripts.mac_native.suite_toolchain.executor_feasibility', return_value={}),
                  patch('scripts.mac_native.toolchain.provision', return_value={})):
                with self.assertRaisesRegex(RuntimeError, 'stop after completed app observation'):
                    suite.run(report)
            self.assertEqual(report['steps']['1']['status'], 'PASS')
            self.assertEqual(set(report['packaged']['bundle_hashes']), {'app_executable', 'daemon', 'cli'})

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

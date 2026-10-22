"""Finite portable workflow/output-layout checks, outside merge CI.

Uses actual workflow reservation/upload selectors and shipping preflight/reservation
with a tiny Git fixture. Artifact marker bytes are not native build observations.
Retired asset/freezer directory-diagnostic protocol cases are no longer shipping
requirements; ordinary path refusal keepers below exercise the active preflight.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace

import yaml

SOURCE = Path(__file__).resolve().parent.parent
REPORT = []


def call(label, argv, *, env=None, cwd=None, failure=None):
    p = subprocess.run(argv, env=env, cwd=cwd, text=True, capture_output=True, timeout=30)
    REPORT.append(dict(label=label, argv=list(map(str, argv)), exit=p.returncode,
                       stdout=p.stdout, stderr=p.stderr))
    if failure:
        assert p.returncode != 0 and failure in p.stdout + p.stderr, REPORT[-1]
    else:
        assert p.returncode == 0, REPORT[-1]
    return p


def verify(root: Path) -> None:
    assert root.is_absolute() and not root.exists()
    root.mkdir(mode=0o700)  # No reuse, overwrite or cleanup.
    repo = root / 'candidate'
    repo.mkdir(mode=0o700)
    (repo / 'fixture.txt').write_text('Portable layout candidate; no app binary.\n')
    call('fixture git init', ['git', 'init', '-q', str(repo)])
    call('fixture git add', ['git', 'add', 'fixture.txt'], cwd=repo)
    call('fixture git commit', ['git', '-c', 'user.name=Packaging fixture', '-c',
         'user.email=fixture@example.invalid', 'commit', '-qm', 'Layout fixture'], cwd=repo)
    sha = call('fixture SHA', ['git', 'rev-parse', 'HEAD'], cwd=repo).stdout.strip()
    tree = call('fixture tree', ['git', 'rev-parse', 'HEAD^{tree}'], cwd=repo).stdout.strip()
    shared = root / 'runner-temp'
    shared.mkdir(mode=0o755)
    shared.chmod(0o755)  # Fixture only; not an observed hosted runner mode.
    workflow = yaml.safe_load((SOURCE / '.github/workflows/build-mac-app.yml').read_text())
    steps = workflow['jobs']['build']['steps']
    reserve = next(s for s in steps if s.get('id') == 'run_paths')['run']
    env = dict(os.environ, RUNNER_TEMP=str(shared), GITHUB_RUN_ID='11771', GITHUB_RUN_ATTEMPT='1')

    def allocate(label):
        output_file = root / (label + '-github-output')
        output_file.touch(mode=0o600)
        result = call(label, ['bash', '-c', reserve], env=dict(env, GITHUB_OUTPUT=str(output_file)))
        paths = dict(line.split('=', 1) for line in output_file.read_text().splitlines())
        return paths, result

    paths, _ = allocate('workflow reservation')
    assert (Path(paths['root']) / 'setup-status.txt').is_file()
    assert not Path(paths['output']).exists()
    other, _ = allocate('second reservation is unique')
    assert paths['root'] != other['root']
    private = Path(paths['root'])
    assert private.parent == shared and stat.S_IMODE(private.stat().st_mode) == 0o700
    assert stat.S_IMODE(shared.stat().st_mode) == 0o755
    assert not Path(paths['output']).exists() and not Path(paths['originals']).exists()
    assert Path(paths['output']).parent == Path(paths['originals']).parent == private
    # No empty-root glob on allocation failure; the runner log holds that error.
    broken = root / 'missing' / 'runner-temp'
    failed_output = root / 'failed-github-output'
    failed_output.touch()
    call('allocation failure', ['bash', '-c', reserve],
         env=dict(env, RUNNER_TEMP=str(broken), GITHUB_OUTPUT=str(failed_output)), failure='mktemp')
    assert failed_output.read_bytes() == b''

    build = (SOURCE / 'app/mac/scripts/build-app.sh').read_text()
    setup = build[build.index('umask 077\n'):build.index('# Foreground commands')]

    def preflight(output, failure=None):
        return call('preflight ' + output.name, ['bash', str(SOURCE / 'app/mac/scripts/verify-release.sh'),
            'preflight', '--source', str(repo), '--version', '1.0.1', '--build', '1.0.1',
            '--source-sha', sha, '--source-tree', tree, '--arch', 'arm64', '--output-dir', str(output)],
            failure=failure)

    preflight(shared / 'happyranch-11771-1', 'OUTPUT_PARENT_NOT_PRIVATE')
    corrected = SimpleNamespace(output=Path(paths['output']))
    result = preflight(corrected.output)
    call('actual build-app reservation', ['bash', '-euc', setup],
         env=dict(os.environ, OUTPUT=str(corrected.output), PREFLIGHT=result.stdout))
    before = (corrected.output / 'owner').read_bytes()
    preflight(corrected.output, 'OUTPUT_EXISTS')
    call('atomic mkdir refuses existing output', ['bash', '-euc', setup],
         env=dict(os.environ, OUTPUT=str(corrected.output), PREFLIGHT='must not overwrite'), failure='File exists')
    assert (corrected.output / 'owner').read_bytes() == before
    private.chmod(0o755)
    preflight(private / 'another', 'OUTPUT_PARENT_NOT_PRIVATE')
    private.chmod(0o700)
    link = shared / 'linked-parent'
    link.symlink_to(private, target_is_directory=True)
    preflight(link / 'new-output', 'OUTPUT_SYMLINK')
    mismatch = ['bash', str(SOURCE / 'app/mac/scripts/verify-release.sh'),
        'preflight', '--source', str(repo), '--version', '1.0.1', '--build', '1.0.1',
        '--source-sha', '0' * 40, '--source-tree', tree, '--arch', 'arm64',
        '--output-dir', str(private / 'mismatch')]
    call('source mismatch refuses before allocation', mismatch, failure='SOURCE_COMMIT_MISMATCH')
    assert not (private / 'mismatch').exists()

    success = next(s for s in steps if s['name'].startswith('Upload successful'))
    failure = next(s for s in steps if s['name'] == 'Retain bounded failure diagnostics')
    assert failure['if'] == "failure() && steps.run_paths.outputs.root != ''"
    def matches(step):
        found = set()
        for pattern in step['with']['path'].splitlines():
            for key, value in paths.items():
                pattern = pattern.replace('${{ steps.run_paths.outputs.' + key + ' }}', value)
            assert '${{' not in pattern
            found.update(glob.glob(pattern))
        return found
    # At preflight stage only the marker and actual preflight exist.
    assert matches(failure) == {str(private / 'setup-status.txt'), str(corrected.output / 'preflight.json')}
    for name in ('HappyRanch-fixture.zip', 'build-receipt.json', 'diagnostics/native.json',
                 'diagnostics/bounded/native-failure.json', 'diagnostics/bounded/evidence-index.json'):
        path = corrected.output / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text('Fixture artifact marker, not a native receipt.\n')
    legacy = str(shared / 'happyranch-11771-1/HappyRanch-*.zip')
    assert glob.glob(legacy) == [], 'Old upload path must miss the corrected candidate'
    REPORT.append(dict(label='original upload glob misses corrected layout', pattern=legacy, matches=[]))
    expected_success = {str(corrected.output / n) for n in ('HappyRanch-fixture.zip', 'build-receipt.json', 'diagnostics/native.json')}
    expected_failure = {str(private / 'setup-status.txt')} | {str(corrected.output / n) for n in
        ('preflight.json', 'diagnostics/bounded/native-failure.json', 'diagnostics/bounded/evidence-index.json')}
    assert matches(success) == expected_success
    assert matches(failure) == expected_failure
    REPORT.append(dict(label='upload glob expansion', success=sorted(matches(success)), failure=sorted(matches(failure)),
                       limitation='Local glob expansion with synthetic marker bytes; hosted upload action UNEXECUTED.'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scratch', type=Path, required=True)
    args = parser.parse_args()
    try:
        verify(args.scratch)
    finally:
        print(json.dumps(REPORT, indent=2))

"""Finite manual D1-D7 diagnostics and workflow layout verification.

No native build, pytest or CI gate.

Requires PyYAML and an absent scratch path under safe, owned POSIX ancestry.
Uses a tiny real Git candidate, real preflight, actual workflow reservation and
build-app reservation shell, and unchanged asset_evidence.bindings. Empty export
contents and artifact marker bytes are fixtures, not native build observations.
Exercises real directory/subject guards and the production process-root helper;
subprocess traceback evidence is not PyInstaller/build.log/archive propagation.
Wrong-owner and numeric boundary records are explicitly synthetic. No host modes
or ownership are changed; only newly created private fixture descendants mutate.
"""
from __future__ import annotations

import argparse
import contextlib
from collections.abc import Callable, Iterator
import errno
import glob
import hashlib
import json
import os
from pathlib import Path
import runpy
import stat
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

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


@contextlib.contextmanager
def recorded_fds() -> Iterator[dict[str, Any]]:
    """Observe real opens/closes/fstats; never substitute guard outcomes."""
    opened, closed, held, opens = [], [], [], []
    original_open, original_close, original_fstat = os.open, os.close, os.fstat

    def opening(*args: Any, **kwargs: Any) -> int:
        fd = original_open(*args, **kwargs)
        opened.append(fd)
        opens.append((str(args[0]), args[1]))
        return fd

    def closing(fd: int) -> None:
        result = original_close(fd)
        closed.append(fd)
        return result

    def observing(fd: int) -> os.stat_result:
        info = original_fstat(fd)
        held.append((fd, info))
        return info

    os.open, os.close, os.fstat = opening, closing, observing
    try:
        yield dict(opened=opened, closed=closed, held=held, opens=opens)
    finally:
        os.open, os.close, os.fstat = original_open, original_close, original_fstat
        # Before any other opens: all recorded descriptors must already be closed.
        assert sorted(opened) == sorted(closed), (opened, closed)
        for fd in opened:
            try:
                original_fstat(fd)
            except OSError as error:
                assert error.errno == errno.EBADF
            else:
                raise AssertionError(('recorded descriptor still open', fd))


def expected_path(path: object) -> tuple[str | None, bool]:
    """Independent serialized-quota oracle, including longest-prefix assertion."""
    if path is None:
        return None, False
    path = str(path)
    if len(json.dumps(path, ensure_ascii=True)) <= 192:
        return path, False
    length = max(i for i in range(len(path) + 1)
                 if len(json.dumps(path[:i] + '...', ensure_ascii=True)) <= 192)
    return path[:length] + '...', True


def note_value(error: BaseException, *, category: str, info: Any, owned: bool,
               cursor: object, root: object, role: str | None = None) -> dict[str, Any]:
    notes = [n for n in getattr(error, '__notes__', []) if n.startswith('DIRECTORY_CONTEXT ')]
    assert len(notes) == 1, notes
    note = notes[0]
    assert note.isascii() and len(note.encode('ascii')) <= 768
    assert not any(ord(c) < 32 or ord(c) == 127 for c in note)
    value = json.loads(note[len('DIRECTORY_CONTEXT '):])
    cursor, ct = expected_path(cursor)
    root, rt = expected_path(root)
    expected = dict(category=category, mode=oct(info.st_mode), uid=info.st_uid,
                    owned=owned, role=role, cursor_truncated=ct,
                    root_truncated=rt, cursor=cursor, root=root)
    assert value == expected, (value, expected)
    assert list(value) == list(expected)
    for key in ('cursor', 'root'):
        assert len(json.dumps(value[key], ensure_ascii=True)) <= 192
    assert note == 'DIRECTORY_CONTEXT ' + json.dumps(expected, ensure_ascii=True, separators=(',', ':'))
    return value


def verify_diagnostics(root: Path) -> None:
    """Accepted D1-D7 portable cases; spec/platform and native lanes stay unrun."""
    root.mkdir(mode=0o700)
    freezer = runpy.run_path(str(SOURCE / 'app/mac/scripts/freezer_evidence.py'))
    asset = freezer['ASSET']
    real_root = asset['RootHandle']
    roles = ('prefix', 'base_prefix', 'stdlib', 'usr-bin')

    def directory(name: str, mode: int = 0o700) -> Path:
        path = root / name
        path.mkdir(mode=0o700, parents=True)
        path.chmod(mode)  # Newly created fixture descendants only.
        return path

    def refusal(label: str, action: Callable[[], object], category: str, *,
                cursor: Path | None = None, selected: Path | None = None, owned: bool = False,
                role: str | None = None, info: os.stat_result | None = None) -> None:
        with recorded_fds() as events:
            try:
                action()
            except asset['AssetError'] as error:
                assert error.category == category and error.subject == ''
                assert error.args == (category + ': ',)
                assert error.original_exit is None and error.owners == ()
                assert error.__cause__ is None and error.__context__ is None
                if cursor is not None:
                    value = note_value(error, category=category, info=info, owned=owned,
                                       cursor=cursor, root=selected, role=role)
                    assert any(s.st_mode == info.st_mode and s.st_uid == info.st_uid
                               for _, s in events['held']), 'diagnostic must match a real held fstat'
                else:
                    value = None
            else:
                raise AssertionError((label, 'unexpected admission'))
        REPORT.append(dict(label=label, exit=0, observed_refusal=category, context=value,
                           opened=len(events['opened']), closed=len(events['closed']),
                           all_recorded_fds_closed=True))

    for mode in (0o775, 0o777):
        ancestor = directory('ancestor-' + oct(mode), mode)
        leaf = ancestor / 'leaf'
        leaf.mkdir(mode=0o700)
        refusal('D1 ancestor ' + oct(mode), lambda: real_root(leaf, '/', True),
                'DIRECTORY_MODE_UNSAFE', cursor=ancestor, selected=leaf, info=ancestor.stat())
    unsafe = directory('unsafe-leaf', 0o777)
    refusal('D1 unsafe leaf', lambda: real_root(unsafe, '/', True),
            'DIRECTORY_MODE_UNSAFE', cursor=unsafe, selected=unsafe, info=unsafe.stat())

    safe = directory('source', 0o755)
    output = directory('output')
    toolchain = directory('toolchain', 0o755)
    for path in (safe, output, toolchain):
        executable = path / 'executable'
        executable.write_bytes(b'fixture executable bytes; never executed\n')
        executable.chmod(0o755)
        with recorded_fds(), real_root(path, '/', True) as handle:
            row = asset['read_subject'](handle, 'executable')
            assert row['size'] == executable.stat().st_size
            assert row['mode'] == executable.stat().st_mode
            assert row['uid'] == os.getuid()
            assert row['sha256'] == hashlib.sha256(b'fixture executable bytes; never executed\n').hexdigest()
        REPORT.append(dict(label='D2 safe root/read ' + path.name, exit=0, subject=row))

    descended = safe / 'descended'
    descended.mkdir(mode=0o700)
    (descended / 'data').write_bytes(b'fixture data')
    with real_root(safe, '/', True) as handle:
        descended.chmod(0o775)
        refusal('D3 parent_at unsafe descendant',
                lambda: asset['read_subject'](handle, 'descended/data'),
                'DIRECTORY_MODE_UNSAFE', cursor=descended, selected=safe,
                owned=True, info=descended.stat())
        refusal('D3 census unsafe descendant', lambda: asset['census'](handle),
                'DIRECTORY_MODE_UNSAFE', cursor=descended, selected=safe,
                owned=True, info=descended.stat())
        descended.chmod(0o700)
        # RootHandle.verify observes root metadata: only descendants changed here.
        target = descended / 'data'
        target.chmod(0o666)
        with recorded_fds():
            try:
                asset['read_subject'](handle, 'descended/data')
            except asset['AssetError'] as error:
                assert error.category == 'FILE_MODE_UNSAFE'
                assert error.subject == 'descended/data'
                assert not getattr(error, '__notes__', [])
            else:
                raise AssertionError('unsafe file admitted')
        target.chmod(0o600)
    REPORT.append(dict(label='D3 unsafe file', exit=0, category='FILE_MODE_UNSAFE'))
    for owned in (False, True):
        synthetic = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=max(0, os.getuid()) + 100001)
        try:
            asset['check_dir'](synthetic, owned)
        except asset['AssetError'] as error:
            note_value(error, category='DIRECTORY_OWNER_INVALID', info=synthetic,
                       owned=owned, cursor=None, root=None)
        else:
            raise AssertionError('synthetic invalid owner admitted')
    synthetic_file = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=os.getuid())
    try:
        asset['check_dir'](synthetic_file, False)
    except asset['AssetError'] as error:
        note_value(error, category='DIRECTORY_REQUIRED', info=synthetic_file,
                   owned=False, cursor=None, root=None)
    else:
        raise AssertionError('nondirectory admitted')
    REPORT.append(dict(label='D3 synthetic owner/type predicate only', exit=0,
                       limitation='Synthetic stat records; no real ownership change or native ownership proof.'))

    link = root / 'linked'
    link.symlink_to(safe, target_is_directory=True)
    for path in (link, link / 'descended'):
        with recorded_fds():
            try:
                real_root(path, '/', False)
            except OSError as error:
                assert error.errno in (errno.ELOOP, errno.ENOTDIR)
            else:
                raise AssertionError('symlink ancestry admitted')
    directory('replace-parent')
    replaced = directory('replace-parent/held')
    with recorded_fds():
        try:
            with real_root(replaced, '/', True) as handle:
                replaced.rename(replaced.with_name('old'))
                replaced.mkdir(mode=0o700)
                try:
                    handle.verify()
                except asset['AssetError'] as error:
                    assert error.category == 'DIRECTORY_PATH_REPLACED'
                else:
                    raise AssertionError('replacement not detected')
        except asset['AssetError'] as error:
            assert error.category == 'DIRECTORY_PATH_REPLACED'
        else:
            raise AssertionError('context exit must verify replacement and close')
    allowed = directory('allowed')
    outside = directory('outside') / 'executable'
    outside.write_bytes(b'outside executable: must not be read')
    alias = allowed / 'alias'
    alias.symlink_to(outside)
    with recorded_fds() as events:
        try:
            freezer['executable_alias'](alias, SimpleNamespace(roots=[str(allowed)]))
        except freezer['FreezerError'] as error:
            assert str(error) == 'FREEZER_EXECUTABLE_ROOT'
        else:
            raise AssertionError('outside executable admitted')
        # Only directory opens, no regular-file payload read.
        assert all(stat.S_ISDIR(info.st_mode) for _, info in events['held'])
        assert all(flags & os.O_DIRECTORY for _, flags in events['opens'])
    REPORT.append(dict(label='D4 no-follow/replacement/alias containment', exit=0,
                       outside_payload_opened=False))

    for index, role in enumerate(roles):
        admitted, visits, seen_errors = ['existing-root'], [], []
        paths = [directory('role-' + str(index) + '-' + str(i)) for i in range(4)]
        paths[index].chmod(0o775)
        pairs = tuple(zip(roles, paths))

        def visiting(path: Path, boundary: str, owned: bool) -> Any:
            visits.append((str(path), boundary, owned))
            try:
                return real_root(path, boundary, owned)
            except asset['AssetError'] as error:
                seen_errors.append(error)
                raise

        asset['RootHandle'] = visiting
        try:
            with recorded_fds() as events:
                try:
                    freezer['_admit_process_roots'](pairs, admitted)
                except asset['AssetError'] as error:
                    assert error is seen_errors[0]
                    assert error.category == 'DIRECTORY_MODE_UNSAFE'
                    assert error.subject == '' and error.args == ('DIRECTORY_MODE_UNSAFE: ',)
                    assert error.original_exit is None and error.owners == ()
                    assert error.__cause__ is None and error.__context__ is None
                    note_value(error, category=error.category, info=paths[index].stat(),
                               owned=False, cursor=paths[index], root=paths[index], role=role)
                else:
                    raise AssertionError('process root admitted')
            assert admitted == ['existing-root'] + [str(p) for p in paths[:index]]
            assert visits == [(str(p), '/', False) for p in paths[:index + 1]]
            REPORT.append(dict(label='D5 role ' + role, exit=0, admitted=admitted,
                               visits=visits, same_error=True, all_recorded_fds_closed=True,
                               opened=len(events['opened']), closed=len(events['closed'])))
        finally:
            asset['RootHandle'] = real_root

    visits = []

    def visiting_safe(path: Path, boundary: str, owned: bool) -> Any:
        visits.append(str(path))
        return real_root(path, boundary, owned)

    asset['RootHandle'] = visiting_safe
    try:
        admitted = []
        with recorded_fds():
            freezer['_admit_process_roots'](tuple((r, safe) for r in roles), admitted)
        assert visits == [str(safe)] * 4 and admitted == [str(safe)]
        visits.clear()
        admitted = []
        refusal('D5 first failure stops later invalid input',
                lambda: freezer['_admit_process_roots']((('prefix', unsafe), ('base_prefix', Path('/bad\ninput'))), admitted),
                'DIRECTORY_MODE_UNSAFE', cursor=unsafe, selected=unsafe, role='prefix', info=unsafe.stat())
        assert visits == [str(unsafe)] and admitted == []
    finally:
        asset['RootHandle'] = real_root
    REPORT.append(dict(label='D5 equal paths validated four times, admitted once', exit=0))

    code = "import runpy,sys; from pathlib import Path; h=runpy.run_path(sys.argv[1]); h['_admit_process_roots']((('stdlib',Path(sys.argv[2])),),[])"
    result = call('D5 uncaught real-helper subprocess traceback',
                  [sys.executable, '-c', code, str(SOURCE / 'app/mac/scripts/freezer_evidence.py'), str(unsafe)],
                  failure='DIRECTORY_MODE_UNSAFE')
    lines = [line for line in result.stderr.splitlines() if line.startswith('DIRECTORY_CONTEXT ')]
    assert len(lines) == 1
    error = asset['AssetError']('DIRECTORY_MODE_UNSAFE')
    error.add_note(lines[0])
    note_value(error, category=error.category, info=unsafe.stat(), owned=False,
               cursor=unsafe, root=unsafe, role='stdlib')

    for name in ('ascii-"quote"-\\slash', 'unicode-雪-😀'):
        escaped = directory(name, 0o775)
        refusal('D7 escaped path ' + name, lambda: real_root(escaped, '/', False),
                'DIRECTORY_MODE_UNSAFE', cursor=escaped, selected=escaped, info=escaped.stat())
    long = root
    for _ in range(12):
        long = long / ('long-雪-😀-' + 'a' * 90)
        long.mkdir(mode=0o700)
    assert len(str(long).encode()) < 4096
    long.chmod(0o775)
    refusal('D7 admitted long real path IO', lambda: real_root(long, '/', False),
            'DIRECTORY_MODE_UNSAFE', cursor=long, selected=long, info=long.stat())
    for path in ('x' * 190, 'x' * 189, 'x' * 191, '😀' * 40, '"\\雪' * 100):
        bounded, truncated = asset['_directory_path'](path)
        assert (bounded, truncated) == expected_path(path)
        synthetic = SimpleNamespace(st_mode=0xffffffff, st_uid=0xffffffff)
        error = asset['AssetError']('DIRECTORY_MODE_UNSAFE', 'unchanged', original_exit=23, owners=('owner',))
        cause, context = ValueError('cause'), RuntimeError('context')
        error.__cause__, error.__context__ = cause, context
        error.add_note('other note')
        before = (error.args, error.subject, error.original_exit, error.owners, error.__traceback__)
        asset['directory_context'](error, info=synthetic, owned=True, cursor=path, root=path)
        asset['directory_context'](error, role='stdlib', root=path)
        asset['directory_context'](error, role='prefix', root='must not replace existing role')
        note_value(error, category=error.category, info=synthetic, owned=True,
                   cursor=path, root=path, role='stdlib')
        assert error.__notes__[0] == 'other note'
        assert before == (error.args, error.subject, error.original_exit, error.owners, error.__traceback__)
        assert error.__cause__ is cause and error.__context__ is context
    # Formatter-only full-note limits: padding is not a real filesystem mode.
    boundary = dict(category='DIRECTORY_MODE_UNSAFE', mode='')
    overhead = len('DIRECTORY_CONTEXT ' + json.dumps(boundary, separators=(',', ':')))
    for size in (767, 768, 769):
        boundary['mode'] = 'x' * (size - overhead)
        try:
            note = asset['_directory_note'](boundary)
        except ValueError:
            assert size == 769
        else:
            assert size <= 768 and len(note) == size
    for path, category in ((root / 'control\n', 'PATH_LIMIT_OR_CONTROL'),
                           (Path('/' + 'x' * 4096), 'ABSOLUTE_PATH_REQUIRED')):
        with recorded_fds() as events:
            try:
                real_root(path, '/', False)
            except asset['AssetError'] as error:
                assert error.category == category and not getattr(error, '__notes__', [])
            else:
                raise AssertionError('invalid path admitted')
        assert not events['opened']

    globals_ = asset['directory_context'].__globals__
    formatter = globals_['_directory_note']

    def broken_formatter(_: dict) -> None:
        raise RuntimeError('ancillary formatter failure')

    globals_['_directory_note'] = broken_formatter
    try:
        with recorded_fds():
            try:
                real_root(unsafe, '/', False)
            except asset['AssetError'] as error:
                assert error.category == 'DIRECTORY_MODE_UNSAFE' and error.subject == ''
                assert error.args == ('DIRECTORY_MODE_UNSAFE: ',)
                assert error.original_exit is None and error.owners == ()
                assert error.__cause__ is None and error.__context__ is None
                assert not getattr(error, '__notes__', [])
            else:
                raise AssertionError('formatter failure masked refusal')
    finally:
        globals_['_directory_note'] = formatter

    class AttachmentFailure(asset['AssetError']):
        def add_note(self, note: str) -> None:
            raise RuntimeError('ancillary attachment failure')

    error = AttachmentFailure('DIRECTORY_MODE_UNSAFE')
    asset['directory_context'](error, info=unsafe.stat(), owned=False, cursor=unsafe, root=unsafe)
    assert error.args == ('DIRECTORY_MODE_UNSAFE: ',) and not getattr(error, '__notes__', [])
    REPORT.append(dict(label='D7 serialization, early invalid inputs, ancillary failure', exit=0,
                       note_cap=768, serialized_path_cap=192,
                       limitation='Numeric extremes/quotas are formatter probes, distinct from actual long-path IO.'))


def verify(root: Path) -> None:
    assert root.is_absolute() and not root.exists()
    root.mkdir(mode=0o700)  # No reuse, overwrite or cleanup.
    verify_diagnostics(root / 'diagnostics')
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

    asset = runpy.run_path(str(SOURCE / 'app/mac/scripts/asset_evidence.py'))
    candidate = asset['Candidate'](sha, tree, '1.0.1', '1.0.1', 'arm64')
    build = (SOURCE / 'app/mac/scripts/build-app.sh').read_text()
    setup = build[build.index('umask 077\n'):build.index('exec > >(tee')]

    def preflight(output, failure=None):
        return call('preflight ' + output.name, ['bash', str(SOURCE / 'app/mac/scripts/verify-release.sh'),
            'preflight', '--source', str(repo), '--version', '1.0.1', '--build', '1.0.1',
            '--source-sha', sha, '--source-tree', tree, '--arch', 'arm64', '--output-dir', str(output)],
            failure=failure)

    def prepare(output):
        result = preflight(output)
        call('actual build-app reservation ' + output.name, ['bash', '-euc', setup],
             env=dict(os.environ, OUTPUT=str(output), PREFLIGHT=result.stdout))
        (output / 'scratch/source').mkdir(mode=0o700)
        return asset['Paths'](repo, output / 'scratch/source', output / 'scratch/source/web/dist',
            output / 'scratch/HappyRanch.app', output, output / 'diagnostics', output / 'scratch/AppIcon.iconset')

    def boundary(label, p, expected=None):
        run_id = (p.output / 'owner').read_text().strip()
        try:
            value = asset['bindings'](candidate, p, run_id)
        except asset['AssetError'] as exc:
            REPORT.append(dict(label=label, category=exc.category, subject=exc.subject))
            assert exc.category == expected, REPORT[-1]
        else:
            assert expected is None, (label, 'unexpected acceptance')
            REPORT.append(dict(label=label, accepted=True, directories=value[0], owner=value[1], preflight=value[2]))

    original = prepare(shared / 'happyranch-11771-1')
    boundary('original shared parent', original, 'PRIVATE_ROOT_REQUIRED')
    corrected = prepare(Path(paths['output']))
    boundary('corrected private parent', corrected)
    before = (corrected.output / 'owner').read_bytes()
    preflight(corrected.output, 'OUTPUT_EXISTS')
    call('atomic mkdir refuses existing output', ['bash', '-euc', setup],
         env=dict(os.environ, OUTPUT=str(corrected.output), PREFLIGHT='must not overwrite'), failure='File exists')
    assert (corrected.output / 'owner').read_bytes() == before
    private.chmod(0o755)
    boundary('nonprivate parent remains refused', corrected, 'PRIVATE_ROOT_REQUIRED')
    private.chmod(0o700)
    link = shared / 'linked-parent'
    link.symlink_to(private, target_is_directory=True)
    preflight(link / 'new-output', 'OUTPUT_SYMLINK')
    linked = asset['Paths'](repo, link / 'output/scratch/source', link / 'output/scratch/source/web/dist',
        link / 'output/scratch/HappyRanch.app', link / 'output', link / 'output/diagnostics',
        link / 'output/scratch/AppIcon.iconset')
    try:
        asset['bindings'](candidate, linked, before.decode().strip())
    except (asset['AssetError'], OSError) as exc:
        REPORT.append(dict(label='bindings symlink parent refused', error=str(exc)))
    else:
        raise AssertionError('bindings accepted symlink parent')

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

"""Standalone B16 contracts using real private files, without root or services."""
from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import stat
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

# Like B8, load the standalone owner without the connector's package initializer.
SPEC = importlib.util.spec_from_file_location(
    'retained_home_audit', Path(__file__).resolve().parents[1] / 'runtime/remote_access/home_audit.py')
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)


@pytest.fixture
def venue(tmp_path: Path, monkeypatch: Any) -> Any:
    parent = tmp_path / 'protected'
    parent.mkdir(mode=0o700)
    owners: dict[int, tuple[int, int]] = {}

    def root_info(info: os.stat_result) -> os.stat_result:
        values = list(info)
        values[4], values[5] = owners.get(info.st_ino, (0, 0))
        return os.stat_result(values)

    # Only OS ownership observations are emulated, never persistence/rotation.
    fake_os = SimpleNamespace(**vars(os))
    fake_os.fstat = lambda descriptor: root_info(os.fstat(descriptor))
    fake_os.stat = lambda *args, **kwargs: root_info(os.stat(*args, **kwargs))
    monkeypatch.setattr(audit, 'os', fake_os)
    return audit.HomeAudit(parent / 'home-audit.jsonl'), parent, owners


def record(**changes: Any) -> Any:
    return replace(audit.Record(audit.Event.REFRESH, audit.Stage.INTENT,
                                'intent-1', 'op-1', 100), **changes)


def seed(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    path.chmod(0o600)


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_bytes().splitlines()]


EVENT_CASES = [
    ('activation', {'phase': 'prepared'}),
    ('invite_issued', {}), ('invite_cancelled', {}), ('invite_recovered', {}),
    ('invite_expired', {}), ('device_approved', {}), ('device_rejected', {}),
    ('device_revoked', {}), ('refresh', {}), ('policy_renew', {}), ('policy_fail', {}),
    ('turn_off', {'component': 'sidecar'}),
]


def test_closed_vocabulary_and_wire(venue: Any) -> None:
    writer, _, _ = venue
    assert {event.value for event in audit.Event} == {
        'activation', 'invite_issued', 'invite_cancelled', 'invite_recovered', 'invite_expired',
        'device_approved', 'device_rejected', 'device_revoked', 'refresh', 'policy_renew',
        'policy_fail', 'turn_off', 'uncertain'}
    for event, fields in EVENT_CASES:
        intent = writer.intent(event, intent_id='intent-1', operation_id='op-1', **fields)
        writer.outcome(intent, 'succeeded')
        writer.uncertain(intent)
    for phase in ['prepared', 'activating', 'joined', 'ready', 'failed_uncertain']:
        writer.intent('activation', intent_id='intent-1', operation_id='op-1', phase=phase)
    for component in ['sidecar', 'connector', 'headscale', 'units', 'live_state', 'credentials', 'accounts']:
        intent = writer.intent('turn_off', intent_id='intent-1', operation_id='op-1', component=component)
        for outcome in ['succeeded', 'failed', 'refused', 'absent', 'retained', 'partial_cleanup']:
            writer.outcome(intent, outcome)
    before = writer.path.read_bytes()
    for changes in [
        {'event': 'unknown'}, {'stage': 'unknown'}, {'event': 'activation', 'phase': 'unknown'},
        {'event': 'turn_off', 'component': 'unknown'}, {'stage': 'outcome', 'outcome': 'unknown'},
        {'event': 'activation'}, {'event': 'turn_off'}, {'phase': 'ready'},
        {'component': 'sidecar'}, {'outcome': 'failed'}, {'stage': 'outcome'},
        {'stage': 'outcome', 'outcome': 'uncertain'},
        {'stage': 'outcome', 'outcome': 'retained'}, {'event': 'uncertain'},
        {'timestamp_ns': True}, {'timestamp_ns': -1}, {'timestamp_ns': 2**63},
    ]:
        with pytest.raises(audit.AuditRefused, match='record'):
            writer.append(record(**changes))
    assert writer.path.read_bytes() == before
    first = rows(writer.path)[0]
    timestamp = first.pop('timestamp_ns')
    assert type(timestamp) is int and timestamp > 0
    assert first == {
        'version': 1, 'event': 'activation', 'stage': 'intent', 'intent_id': 'intent-1',
        'operation_id': 'op-1', 'subject_id': None, 'phase': 'prepared',
        'component': None, 'outcome': None}


@pytest.mark.parametrize('field', ['raw_secret', 'node_key', 'digest_prefix', 'token', 'extra'])
def test_closed_fields(venue: Any, field: str) -> None:
    writer, _, _ = venue
    with pytest.raises(TypeError):
        writer.intent('refresh', intent_id='intent-1', operation_id='op-1', **{field: 'synthetic'})
    assert not writer.path.exists()


@pytest.mark.parametrize('value', [
    'nodekey:synthetic', 'tskey-synthetic', 'op-tskey', 'op-nodekey', 'op-token',
    'op-' + 'a' * 64, 'op-deadbeef', 'op-0123456789abcdef',
    'op-QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo=', 'op-' + 'Ab3z' * 16,
    'op-with/slash', 'op-with\nline', 'op-雪', 'op--empty', 'op-' + 'x' * 321,
])
def test_secret_looking_identifiers(venue: Any, value: str) -> None:
    writer, _, _ = venue
    for field in ['operation_id', 'intent_id', 'subject_id']:
        # Each field also receives a matching-prefix attack, reaching its own validator.
        prefix = {'operation_id': 'op', 'intent_id': 'intent', 'subject_id': 'subject'}[field]
        candidate = value.replace('op-', prefix + '-', 1) if value.startswith('op-') else value
        with pytest.raises(audit.AuditRefused) as error:
            writer.append(record(**{field: candidate}))
        assert str(error.value) == 'record'
        assert candidate not in repr(error.value)
    assert not writer.path.exists()


def local_id(prefix: str, length: int) -> str:
    # Bounded, nonhex, non-token segments; length is independently chosen by case.
    result = prefix
    while len(result) < length:
        result += '-' + 'x' * min(12, length - len(result) - 1)
    return result


def boundary_record(size: int) -> Any:
    # 3 valid local identifiers can approach/exceed the record cap. Choose an
    # independent JSON overhead from a small fixed record, not encode()'s output.
    base = record(subject_id='subject-1')
    overhead = len((json.dumps({'version': 1, **{
        'event': 'refresh', 'stage': 'intent', 'intent_id': 'intent-1', 'operation_id': 'op-1',
        'timestamp_ns': 100, 'subject_id': 'subject-1', 'phase': None, 'component': None,
        'outcome': None}}, sort_keys=True, separators=(',', ':')) + '\n').encode()) - 21
    return replace(base, intent_id=local_id('intent', 300), operation_id=local_id('op', 300),
                   subject_id=local_id('subject', size - overhead - 600))


def test_record_byte_boundary_and_refusal(venue: Any) -> None:
    writer, _, _ = venue
    at_limit = boundary_record(1024)
    data = audit.encode(at_limit)
    assert len(data) == 1024
    assert data.endswith(b'\n') and json.loads(data)['subject_id'] == at_limit.subject_id
    writer.append(at_limit)
    assert writer.path.read_bytes() == data
    with pytest.raises(audit.AuditRefused, match='record-too-large'):
        writer.append(boundary_record(1025))
    assert writer.path.read_bytes() == data  # no truncated second JSON line


def test_file_cap_and_single_generation_rotation(venue: Any) -> None:
    writer, parent, _ = venue
    # Exactly 1024 bytes, an independent valid JSON-line fixture.
    line = b'{"fixture":"' + b'x' * 1009 + b'"}\n'
    assert len(line) == 1024
    prefix = line * 1023
    seed(writer.path, prefix)
    backup = parent / 'home-audit.jsonl.1'
    seed(backup, b'previous-generation\n')
    first_inode = writer.path.stat().st_ino
    writer.append(boundary_record(1024))
    full = prefix + audit.encode(boundary_record(1024))
    assert writer.path.read_bytes() == full and len(full) == 1024 * 1024
    assert writer.path.stat().st_ino == first_inode
    assert backup.read_bytes() == b'previous-generation\n'  # no early rotation
    writer.append(record())
    assert backup.read_bytes() == full
    assert backup.stat().st_ino == first_inode
    assert writer.path.read_bytes() == audit.encode(record())
    assert stat.S_IMODE(writer.path.stat().st_mode) == 0o600
    second_generation = line * 1024
    seed(writer.path, second_generation)
    writer.append(record(intent_id='intent-2'))
    assert backup.read_bytes() == second_generation
    assert writer.path.read_bytes() == audit.encode(record(intent_id='intent-2'))
    assert set(path.name for path in parent.iterdir()) == {'home-audit.jsonl', 'home-audit.jsonl.1'}


@pytest.mark.parametrize('case', ['file-mode', 'file-uid', 'file-gid', 'dir-mode', 'dir-uid', 'dir-gid'])
def test_mode_and_owner_refusal(venue: Any, case: str) -> None:
    writer, parent, owners = venue
    seed(writer.path, b'existing\n')
    target = writer.path if case.startswith('file') else parent
    if case.endswith('mode'):
        target.chmod(0o644 if case.startswith('file') else 0o777)
    else:
        owners[target.stat().st_ino] = (42, 0) if case.endswith('uid') else (0, 42)
    with pytest.raises(audit.AuditRefused):
        writer.append(record())
    assert writer.path.read_bytes() == b'existing\n'
    assert set(path.name for path in parent.iterdir()) == {'home-audit.jsonl'}


@pytest.mark.parametrize('case', ['file', 'directory', 'ancestor', 'dangling-file', 'hardlink', 'fifo', 'file-directory'])
def test_unsafe_path_refusal(venue: Any, tmp_path: Path, case: str) -> None:
    writer, parent, _ = venue
    target = tmp_path / 'target'
    seed(target, b'untouched\n')
    reader = None
    if case in ('file', 'dangling-file'):
        writer.path.symlink_to(target if case == 'file' else tmp_path / 'missing')
    elif case == 'hardlink':
        os.link(target, writer.path)
    elif case == 'fifo':
        os.mkfifo(writer.path, 0o600)
        reader = os.open(writer.path, os.O_RDONLY | os.O_NONBLOCK)
    elif case == 'file-directory':
        writer.path.mkdir(mode=0o700)
    else:
        alias = tmp_path / 'alias'
        alias.symlink_to(parent, target_is_directory=True)
        if case == 'directory':
            writer = audit.HomeAudit(alias / writer.path.name)
        else:
            (parent / 'child').mkdir(mode=0o700)
            writer = audit.HomeAudit(alias / 'child' / writer.path.name)
    try:
        with pytest.raises(audit.AuditRefused):
            writer.append(record())
        if reader is not None:
            assert os.read(reader, 1024) == b''
    finally:
        if reader is not None:
            os.close(reader)
    assert target.read_bytes() == b'untouched\n'
    if case in ('directory', 'ancestor'):
        assert not writer.path.exists()
    elif case in ('file', 'dangling-file'):
        assert writer.path.is_symlink()
    elif case == 'hardlink':
        assert writer.path.stat().st_nlink == 2


@pytest.mark.parametrize('case', ['symlink', 'hardlink', 'mode', 'owner'])
def test_unsafe_rotation_destination_preserved(venue: Any, tmp_path: Path, case: str) -> None:
    writer, parent, owners = venue
    full = b'x' * (1024 * 1024)
    seed(writer.path, full)
    target = tmp_path / 'old-generation'
    seed(target, b'old\n')
    backup = parent / 'home-audit.jsonl.1'
    if case == 'symlink':
        backup.symlink_to(target)
    elif case == 'hardlink':
        os.link(target, backup)
    else:
        seed(backup, b'old\n')
        if case == 'mode':
            backup.chmod(0o644)
        else:
            owners[backup.stat().st_ino] = (42, 0)
    with pytest.raises(audit.AuditRefused):
        writer.append(record())
    assert writer.path.read_bytes() == full
    assert target.read_bytes() == backup.read_bytes() == b'old\n'


def test_append_only_and_durable_descriptors(venue: Any, monkeypatch: Any) -> None:
    writer, parent, _ = venue
    calls: list[tuple] = []
    real_open = audit.os.open
    real_rename = audit.os.rename

    def opened(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        descriptor = real_open(path, flags, *args, **kwargs)
        calls.append(('open', str(path), flags, kwargs.get('dir_fd'), args))
        return descriptor

    def synced(descriptor: int) -> None:
        info = os.fstat(descriptor)
        calls.append(('sync-dir' if stat.S_ISDIR(info.st_mode) else 'sync-file',))
        os.fsync(descriptor)

    def written(descriptor: int, data: Any) -> int:
        assert fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_APPEND
        assert fcntl.fcntl(descriptor, fcntl.F_GETFD) & fcntl.FD_CLOEXEC
        calls.append(('write',))
        return os.write(descriptor, data)

    def renamed(src: str, dst: str, **kwargs: Any) -> None:
        assert kwargs['src_dir_fd'] == kwargs['dst_dir_fd']
        calls.append(('rename',))
        real_rename(src, dst, **kwargs)

    monkeypatch.setattr(audit.os, 'open', opened)
    monkeypatch.setattr(audit.os, 'fsync', synced)
    monkeypatch.setattr(audit.os, 'write', written)
    monkeypatch.setattr(audit.os, 'rename', renamed)
    writer.append(record())
    assert [call[0] for call in calls if call[0] != 'open'] == ['write', 'sync-file', 'sync-dir']
    for _, name, flags, dir_fd, args in [call for call in calls if call[0] == 'open']:
        assert flags & os.O_NOFOLLOW and flags & os.O_CLOEXEC
        if name == writer.path.name:
            assert dir_fd is not None and flags & os.O_APPEND
            if flags & os.O_CREAT:
                assert args == (0o600,) and flags & os.O_EXCL
        else:
            assert flags & os.O_DIRECTORY
    original = writer.path.read_bytes()
    inode = writer.path.stat().st_ino
    calls.clear()
    writer.append(record(intent_id='intent-2'))
    assert writer.path.read_bytes() == original + audit.encode(record(intent_id='intent-2'))
    assert writer.path.stat().st_ino == inode
    assert [call[0] for call in calls if call[0] != 'open'] == ['write', 'sync-file', 'sync-dir']
    seed(writer.path, b'x' * (1024 * 1024))
    calls.clear()
    writer.append(record())
    assert [call[0] for call in calls if call[0] != 'open'] == [
        'rename', 'sync-dir', 'write', 'sync-file', 'sync-dir']
    assert (parent / 'home-audit.jsonl.1').stat().st_size == 1024 * 1024


@pytest.mark.parametrize('rotated', [False, True], ids=['create', 'rotation'])
@pytest.mark.parametrize('failure', ['file', 'directory'])
def test_reopened_intent_syncs_directory_after_interrupted_create(
        venue: Any, monkeypatch: Any, rotated: bool, failure: str) -> None:
    writer, parent, _ = venue
    backup = parent / 'home-audit.jsonl.1'
    full = b'x' * (1024 * 1024)
    if rotated:
        seed(writer.path, full)
    failed = False
    successful_syncs: list[tuple[str, int]] = []

    def synced(descriptor: int) -> None:
        nonlocal failed
        info = os.fstat(descriptor)
        kind = 'directory' if stat.S_ISDIR(info.st_mode) else 'file'
        # Rotation's rename sync precedes creation of the new current file.
        # Interrupt that current file's first sync, leaving its entry behind.
        if not failed and kind == failure and writer.path.exists():
            failed = True
            raise OSError('synthetic-sync-failure')
        os.fsync(descriptor)
        successful_syncs.append((kind, info.st_ino))

    monkeypatch.setattr(audit.os, 'fsync', synced)
    with pytest.raises(audit.AuditRefused, match='unavailable'):
        writer.intent('refresh', intent_id='intent-1', operation_id='op-1')
    assert failed and writer.path.exists()
    prefix = writer.path.read_bytes()
    assert rows(writer.path)[0]['intent_id'] == 'intent-1'
    if rotated:
        assert backup.read_bytes() == full
    successful_syncs.clear()

    fresh = audit.HomeAudit(writer.path)
    intent = fresh.intent('refresh', intent_id='intent-2', operation_id='op-1')
    # Returning an intent requires successful real syncs for its inode and name,
    # even when this instance merely reopened an interrupted creation.
    assert successful_syncs == [
        ('file', writer.path.stat().st_ino), ('directory', parent.stat().st_ino)]
    assert writer.path.read_bytes() == prefix + audit.encode(intent)
    if rotated:
        assert backup.read_bytes() == full
    else:
        assert not backup.exists()


def test_intent_outcome_uncertain_sequence(venue: Any) -> None:
    writer, _, _ = venue
    intent = writer.intent('device_approved', intent_id='intent-1', operation_id='op-1', subject_id='subject-1')
    assert [(row['event'], row['stage']) for row in rows(writer.path)] == [('device_approved', 'intent')]
    # Simulated effect observes the already-persisted intent before settling.
    writer.outcome(intent, 'succeeded')
    lost = writer.intent('refresh', intent_id='intent-2', operation_id='op-1')
    writer.uncertain(lost)
    result = rows(writer.path)
    assert [(row['event'], row['stage'], row['intent_id'], row['outcome']) for row in result] == [
        ('device_approved', 'intent', 'intent-1', None),
        ('device_approved', 'outcome', 'intent-1', 'succeeded'),
        ('refresh', 'intent', 'intent-2', None),
        ('uncertain', 'uncertain', 'intent-2', 'uncertain')]
    assert all(row['operation_id'] == 'op-1' for row in result)
    with pytest.raises(audit.AuditRefused):
        writer.outcome(replace(intent, stage='outcome', outcome='succeeded'), 'failed')
    with pytest.raises(audit.AuditRefused):
        writer.uncertain(replace(lost, stage='outcome', outcome='failed'))
    assert rows(writer.path) == result


def test_io_failure_is_sanitized_and_preserves_prefix(venue: Any, monkeypatch: Any) -> None:
    writer, _, _ = venue
    seed(writer.path, b'prefix\n')

    def failed_sync(descriptor: int) -> None:
        raise OSError('synthetic-private-diagnostic')

    monkeypatch.setattr(audit.os, 'fsync', failed_sync)
    with pytest.raises(audit.AuditRefused) as error:
        writer.append(record())
    assert str(error.value) == 'unavailable'
    assert error.value.__suppress_context__
    # A lost fsync is uncertainty, not a no-change guarantee.
    assert writer.path.read_bytes() == b'prefix\n' + audit.encode(record())


@pytest.mark.parametrize('earlier', [b'', b'{"earlier":1}\n{"earlier":2}\n'],
                         ids=['empty', 'earlier-lines'])
def test_fresh_intent_after_kernel_partial_write(tmp_path: Path, earlier: bytes) -> None:
    path = tmp_path / 'home-audit.jsonl'
    if earlier:
        seed(path, earlier)
    # Isolate the process-wide kernel limit/signal; no OS operation is mocked.
    fault = subprocess.run([sys.executable, '-c', '''
import importlib.util
import os
import resource
import signal
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location('partial_home_audit', sys.argv[1])
audit = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = audit
spec.loader.exec_module(audit)
path = Path(sys.argv[2])
size = path.stat().st_size if path.exists() else 0
writer = audit.HomeAudit(path, uid=os.geteuid(), gid=os.getegid())
original_limit = resource.getrlimit(resource.RLIMIT_FSIZE)
original_signal = signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
try:
    resource.setrlimit(resource.RLIMIT_FSIZE, (size + 17, original_limit[1]))
    try:
        writer.intent('refresh', intent_id='intent-failed', operation_id='op-first')
    except audit.AuditRefused as error:
        assert str(error) == 'unavailable'
    else:
        raise AssertionError('kernel partial write did not refuse')
finally:
    resource.setrlimit(resource.RLIMIT_FSIZE, original_limit)
    signal.signal(signal.SIGXFSZ, original_signal)
assert resource.getrlimit(resource.RLIMIT_FSIZE) == original_limit
assert signal.getsignal(signal.SIGXFSZ) == original_signal
assert path.read_bytes()[size:] == b'{"component":null'
''', str(SPEC.origin), str(path)], capture_output=True, text=True, timeout=10)
    assert fault.returncode == 0, fault.stdout + fault.stderr
    prefix = path.read_bytes()
    assert prefix == earlier + b'{"component":null'  # exact real short-write bytes
    inode = path.stat().st_ino

    fresh = audit.HomeAudit(path, uid=os.geteuid(), gid=os.getegid())
    fresh.intent('refresh', intent_id='intent-acknowledged', operation_id='op-second')
    data = path.read_bytes()
    lines = data.splitlines(keepends=True)
    # Independent wire/JSON checks: encode() does not supply the expected line.
    assert data.startswith(prefix + b'\n')
    assert lines[:-2] == earlier.splitlines(keepends=True)
    assert [json.loads(line) for line in lines[:-2]] == (
        [{'earlier': 1}, {'earlier': 2}] if earlier else [])
    assert lines[-2] == b'{"component":null\n'
    with pytest.raises(json.JSONDecodeError):
        json.loads(lines[-2])
    assert lines[-1].endswith(b'\n') and len(lines[-1]) <= 1024
    acknowledged = json.loads(lines[-1])
    assert (acknowledged['intent_id'], acknowledged['operation_id'],
            acknowledged['event'], acknowledged['stage']) == (
                'intent-acknowledged', 'op-second', 'refresh', 'intent')
    assert path.stat().st_ino == inode
    assert set(item.name for item in tmp_path.iterdir()) == {'home-audit.jsonl'}


@pytest.mark.parametrize('spare', [0, 1], ids=['separator-overflows', 'separator-fits'])
def test_torn_tail_separator_counts_toward_file_cap(
        venue: Any, monkeypatch: Any, spare: int) -> None:
    writer, parent, _ = venue
    line = b'{"fixture":"' + b'x' * 1009 + b'"}\n'
    complete = line * 1022
    prefix = complete + b'x' * (1024 - spare)
    seed(writer.path, prefix)
    backup = parent / 'home-audit.jsonl.1'
    seed(backup, b'previous-generation\n')
    writes: list[bytes] = []

    def written(descriptor: int, data: Any) -> int:
        writes.append(bytes(data))
        return os.write(descriptor, data)

    monkeypatch.setattr(audit.os, 'write', written)
    writer.append(boundary_record(1024))
    if spare == 0:
        # Record alone fits exactly; the separator requires cap rotation.
        assert backup.read_bytes() == prefix
        retained = backup.read_bytes()
        data = writer.path.read_bytes()
        assert len(data) == 1024 and not data.startswith(b'\n')
    else:
        # Tornness alone must not rotate; separator + record fit exactly.
        data = writer.path.read_bytes()
        assert data.startswith(prefix + b'\n') and len(data) == 1024 * 1024
        assert backup.read_bytes() == b'previous-generation\n'
        retained = data
    assert all(json.loads(row) == {'fixture': 'x' * 1009}
               for row in retained.splitlines()[:1022])
    last = data.splitlines(keepends=True)[-1]
    assert len(last) == 1024 and last.endswith(b'\n')
    assert json.loads(last)['intent_id'] == local_id('intent', 300)
    assert writes == [last if spare == 0 else b'\n' + last]
    assert set(item.name for item in parent.iterdir()) == {'home-audit.jsonl', 'home-audit.jsonl.1'}


def test_tail_read_failure_refuses_without_writing(venue: Any, monkeypatch: Any) -> None:
    writer, _, _ = venue
    prefix = b'{"earlier":1}\n'
    seed(writer.path, prefix)

    def failed_read(descriptor: int, count: int, offset: int) -> bytes:
        assert os.fstat(descriptor).st_ino == writer.path.stat().st_ino
        assert (count, offset) == (1, len(prefix) - 1)
        raise OSError('synthetic-private-tail-diagnostic')

    monkeypatch.setattr(audit.os, 'pread', failed_read)
    with pytest.raises(audit.AuditRefused) as error:
        writer.append(record())
    assert str(error.value) == 'unavailable' and error.value.__suppress_context__
    assert writer.path.read_bytes() == prefix
    assert not writer.path.with_name(writer.path.name + '.1').exists()


def test_explicit_nonroot_custody_and_default_path(tmp_path: Path) -> None:
    assert str(audit.AUDIT_PATH) == '/var/lib/happyranch-remote-access/home-audit.jsonl'
    writer = audit.HomeAudit(tmp_path / 'home-audit.jsonl', uid=os.geteuid(), gid=os.getegid())
    writer.append(record())
    assert writer.path.read_bytes() == audit.encode(record())
    assert stat.S_IMODE(writer.path.stat().st_mode) == 0o600

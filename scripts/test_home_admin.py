"""Standalone synthetic-wire/temp-root tests for the inert root composition."""
from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.remote_access import home_admin as h
from runtime.remote_access import service_manager
from runtime.remote_access.home_minter import AdminRefused, CreationWindow, MintUncertain
from runtime.remote_access.invite_issuer import MintRefused

USER = 'happyranch-invite'
KEY = b'a1' * 24
NOW = datetime(2026, 11, 18, tzinfo=timezone.utc)


def timestamp(seconds: int = 1794960000, nanos: int = 0) -> bytes:
    return b'\x08' + h._varint(seconds & ((1 << 64) - 1)) + b'\x10' + h._varint(nanos)


def record() -> bytes:
    return (h._bytes(1, USER.encode()) + h._bytes(2, b'1') + h._bytes(3, KEY)
            + h._bytes(7, timestamp()) + h._bytes(8, timestamp()))


def transport(monkeypatch, data: bytes, code: int = 0) -> h.RootHeadscaleAdminOperations:
    monkeypatch.setattr(h.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(h, '_runner_custody', lambda: None)
    monkeypatch.setattr(h, '_default_run', lambda *a, **kw: (code, data))
    return h.RootHeadscaleAdminOperations()


def test_proto3_omission_and_valid_timestamps(monkeypatch) -> None:
    admin = transport(monkeypatch, h._bytes(1, record()))
    got = admin.create_pre_auth_key(user=USER, reusable=False, ephemeral=False,
                                    expiration=NOW + timedelta(seconds=120), timeout_s=1)
    assert (got.user, got.id, got.key) == (USER, '1', KEY.decode())
    assert (got.reusable, got.ephemeral, got.used, got.acl_tags) == (False, False, False, ())
    assert got.expiration.tzinfo == timezone.utc
    for seconds, nanos in [(-62135596800, 0), (253402300799, 999999999), (0, 0)]:
        value = h._timestamp(timestamp(seconds, nanos))
        assert value.year in (1, 1970, 9999)
        assert value.microsecond == nanos // 1000


def test_annual_inventory_under_real_deadline(monkeypatch) -> None:
    # 366 days at one mint/minute; complete expired history still needs decoding.
    rows = 527_040
    started = int(NOW.timestamp()) - rows * 60 - 120
    prefix = h._bytes(1, USER.encode())
    suffix = h._bytes(3, KEY)
    response = b''.join(h._bytes(1, prefix + h._bytes(2, str(i + 1).encode()) + suffix
                                + h._bytes(7, timestamp(started + i * 60 + 120))
                                + h._bytes(8, timestamp(started + i * 60)))
                        for i in range(rows))
    got = transport(monkeypatch, response).list_pre_auth_keys(user=USER, timeout_s=5)
    assert len(got) == rows
    for i, item in enumerate(got):
        assert (item.user, item.id, item.key) == (USER, str(i + 1), KEY.decode())
        assert (item.reusable, item.ephemeral, item.used, item.acl_tags) == (False, False, False, ())
        assert item.created_at == datetime.fromtimestamp(started + i * 60, timezone.utc)
        assert item.expiration == item.created_at + timedelta(seconds=120)


def mutations() -> list[bytes]:
    good = record()
    missing = [b''.join(h._bytes(n, v) if w == 2 else h._varint(n * 8) + h._varint(v)
                        for n, w, v in h._fields(good) if n != field) for field in (1, 2, 3, 7, 8)]
    return missing + [good + h._bytes(1, b'duplicate'), good + b'\x50\x00',
                     good + b'\x20\x02', good + b'\x22\x00', good + b'\x80',
                     good + b'\x4a\x7fabc', good + b'\x80\x80\x80\x80\x80\x80\x80\x80\x80\x02',
                     good + b'\x20\x80\x00', good + b'\x0a\x00',
                     good.replace(h._bytes(2, b'1'), h._bytes(2, b'\xff')),
                     good.replace(h._bytes(3, KEY), h._bytes(3, b'wrong-key')),
                     good.replace(h._bytes(7, timestamp()), h._bytes(7, timestamp(253402300800))),
                     good.replace(h._bytes(8, timestamp()), h._bytes(8, timestamp(nanos=1000000000))),
                     good.replace(h._bytes(7, timestamp()), h._bytes(7, b'\x08\x00\x08\x01'))]


@pytest.mark.parametrize('bad', mutations(), ids=lambda b: hashlib.sha256(b).hexdigest()[:8])
def test_strict_wire_complete_or_fail(monkeypatch, bad: bytes) -> None:
    # Valid prefix followed by a malformed row must never return partial success.
    admin = transport(monkeypatch, h._bytes(1, record()) + h._bytes(1, bad))
    with pytest.raises(MintUncertain) as caught:
        admin.list_pre_auth_keys(user=USER, timeout_s=1)
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert KEY.decode() not in repr(caught.value)
    transport(monkeypatch, h._bytes(1, bad))
    with pytest.raises(MintUncertain):
        admin.create_pre_auth_key(user=USER, reusable=False, ephemeral=False,
                                  expiration=NOW + timedelta(seconds=120), timeout_s=1)


@pytest.mark.parametrize('bad', [b'\x10\x00', h._bytes(1, record()) * 2, b'\x0a\x7fabc'])
def test_response_schema_refuses(monkeypatch, bad: bytes) -> None:
    admin = transport(monkeypatch, bad)
    with pytest.raises(MintUncertain):
        admin.create_pre_auth_key(user=USER, reusable=False, ephemeral=False,
                                  expiration=NOW + timedelta(seconds=120), timeout_s=1)


def test_calls_have_secret_only_in_pipes(monkeypatch) -> None:
    # Keep the existing exact argv assertion deterministic as transport now
    # receives the original deadline's remaining budget.
    monkeypatch.setattr(h, 'time', SimpleNamespace(monotonic=lambda: 0), raising=False)
    calls = []
    admin = transport(monkeypatch, b'')
    monkeypatch.setattr(h, '_default_run', lambda argv, **kw: calls.append((argv, kw)) or (0, b''))
    admin.expire_pre_auth_key(user=USER, key=KEY.decode(), timeout_s=1)
    argv, kw = calls[0]
    assert argv == [h._RUNNER, 'expire', '1000']
    assert KEY in kw['input_bytes'] and KEY.decode() not in repr(argv)
    assert 'redacted' in repr(admin)


@pytest.mark.parametrize('code,exception', [(2, AdminRefused), (3, MintUncertain), (-1, MintUncertain)])
def test_failure_categories(monkeypatch, code, exception) -> None:
    admin = transport(monkeypatch, KEY, code)
    with pytest.raises(exception) as caught:
        admin.create_pre_auth_key(user=USER, reusable=False, ephemeral=False,
                                  expiration=NOW + timedelta(seconds=120), timeout_s=1)
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert KEY.decode() not in str(caught.value)


def test_secret_runner_never_echoes_error_streams(monkeypatch, capsys) -> None:
    for error in (subprocess.TimeoutExpired(['fixed'], 1, output=KEY, stderr=KEY), OSError(KEY.decode())):
        def fail(*a, **kw):
            raise error
        monkeypatch.setattr(service_manager.subprocess, 'run', fail)
        assert service_manager._default_run(['fixed'], input_bytes=KEY) == (-1, b'')
    monkeypatch.setattr(service_manager.subprocess, 'run',
                        lambda *a, **kw: SimpleNamespace(returncode=0, stdout=b'private', stderr=KEY))
    assert service_manager._default_run(['fixed'], input_bytes=KEY) == (0, b'private')
    assert capsys.readouterr() == ('', '')


@pytest.fixture
def root(tmp_path, monkeypatch):
    # Test-side filesystem view: only literal / is remapped; all descendant
    # openat/no-follow/mode/link operations execute against real temporary files.
    (tmp_path / 'var/lib').mkdir(parents=True)
    (tmp_path / 'var').chmod(0o755)
    (tmp_path / 'var/lib').chmod(0o755)
    real_open, real_stat = os.open, os.fstat
    def open_root(path, flags, *a, **kw):
        return real_open(tmp_path if path == '/' else path, flags, *a, **kw)
    def owned(fd):
        st = real_stat(fd)
        return SimpleNamespace(st_uid=0, st_gid=0, st_mode=st.st_mode, st_nlink=st.st_nlink)
    monkeypatch.setattr(h.os, 'open', open_root)
    monkeypatch.setattr(h.os, 'fstat', owned)
    monkeypatch.setattr(h.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(h.os, 'fchown', lambda *a: None)
    return tmp_path


def test_reservation_durable_and_never_reused(root) -> None:
    fd = h._directory()
    try:
        store = h.DurableOperationRecordStore(fd)
        assert store.reserve('op', CreationWindow(NOW, NOW + timedelta(seconds=120)))
    finally:
        os.close(fd)
    fd = h._directory()
    try:
        assert not h.DurableOperationRecordStore(fd).reserve('op', CreationWindow(NOW, NOW))
    finally:
        os.close(fd)
    paths = list(root.rglob('operation-*'))
    assert len(paths) == 1 and stat.S_IMODE(paths[0].stat().st_mode) == 0o600
    assert paths[0].read_text() == NOW.isoformat() + '\n' + (NOW + timedelta(seconds=120)).isoformat() + '\n'
    assert KEY not in paths[0].read_bytes()


def test_reservation_failure_leaves_burned_slot(root, monkeypatch) -> None:
    fd = h._directory()
    try:
        store = h.DurableOperationRecordStore(fd)
        monkeypatch.setattr(h.os, 'fsync', lambda fd: (_ for _ in ()).throw(OSError('synthetic')))
        with pytest.raises(OSError):
            store.reserve('burned', CreationWindow(NOW, NOW))
        assert not store.reserve('burned', CreationWindow(NOW, NOW))
    finally:
        os.close(fd)


@pytest.mark.parametrize('tamper', ['mode', 'symlink', 'foreign-owner'])
def test_directory_custody_preserves_foreign(root, monkeypatch, tamper) -> None:
    path = root / 'var/lib/happyranch-remote-access'
    path.mkdir(mode=0o700)
    if tamper == 'mode':
        path.chmod(0o750)
    elif tamper == 'symlink':
        path.rename(path.with_suffix('.real'))
        path.symlink_to(path.with_suffix('.real'))
    else:
        real_stat = h.os.fstat
        def foreign(fd):
            st = real_stat(fd)
            return SimpleNamespace(st_uid=1, st_gid=0, st_mode=st.st_mode, st_nlink=st.st_nlink)
        monkeypatch.setattr(h.os, 'fstat', foreign)
    with pytest.raises((ValueError, OSError)):
        h._directory()
    assert path.exists()
    assert not list(root.rglob('operation-*'))


def test_lock_spans_entire_mint_and_recover(root, monkeypatch) -> None:
    monkeypatch.setattr(h.HeadscaleJoinKeyMinter, 'mint', lambda *a: h.MintedKey(KEY.decode(), NOW))
    monkeypatch.setattr(h.HeadscaleJoinKeyMinter, 'recover', lambda *a: True)
    with h._locked():
        minter = h.RootHomeInviteMinter()
        with pytest.raises(MintRefused):
            minter.mint('other', NOW)
        assert minter.recover('other') is False
    # Across processes too: a separately opened descriptor cannot take flock.
    fd = h._directory()
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path = root / h._STATE.lstrip('/') / USER
        result = subprocess.run([sys.executable, '-c',
                                 'import os,sys; from runtime.remote_access import home_admin as h;'
                                 'h._directory=lambda:os.open(sys.argv[1],os.O_RDONLY|os.O_DIRECTORY);'
                                 'h.DurableOperationRecordStore=lambda fd:None;'
                                 'h.HeadscaleJoinKeyMinter.recover=lambda *a:True;'
                                 'sys.exit(0 if h.RootHomeInviteMinter().recover("op") else 1)', str(path)], capture_output=True)
        assert result.returncode == 1 and result.stderr == b''
    finally:
        os.close(fd)


@pytest.mark.parametrize('tamper', ['none', 'mode', 'hardlink', 'symlink', 'parent-link'])
def test_runner_path_custody(root, monkeypatch, tamper) -> None:
    path = root / h._RUNNER.lstrip('/')
    path.parent.mkdir(parents=True)
    # The synthetic installation directories are root-owned via the fixture's
    # metadata view, and their real write modes remain independently checked.
    for parent in path.parents:
        if parent == root:
            break
        parent.chmod(0o755)
    path.write_bytes(b'fixture')
    path.chmod(0o755)
    if tamper == 'mode':
        path.chmod(0o775)
    elif tamper == 'hardlink':
        path.with_suffix('.alias').hardlink_to(path)
    elif tamper == 'symlink':
        path.rename(path.with_suffix('.real'))
        path.symlink_to(path.with_suffix('.real'))
    elif tamper == 'parent-link':
        path.parent.rename(path.parent.with_suffix('.real'))
        path.parent.symlink_to(path.parent.with_suffix('.real'))
    if tamper == 'none':
        h._runner_custody()
    else:
        with pytest.raises((AdminRefused, OSError)):
            h._runner_custody()
    assert path.exists()


def test_after_create_wrapper_failure_stays_uncertain(monkeypatch) -> None:
    from contextlib import contextmanager
    @contextmanager
    def failing_close():
        yield SimpleNamespace(mint=lambda *a: object())
        raise OSError('synthetic close failure')
    monkeypatch.setattr(h, '_locked', failing_close)
    with pytest.raises(MintUncertain):
        h.RootHomeInviteMinter().mint('op', NOW)


@pytest.mark.parametrize('failure', [None, 'refused', 'uncertain', 'reusable', 'ephemeral', 'tags', 'long-expiry'])
def test_local_node_mint_fixed_user_policy_and_private_result(root, monkeypatch, failure) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    deadline = now + timedelta(seconds=120)
    calls = []
    monkeypatch.setattr(h, '_runner_custody', lambda: None)
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[1] == 'list': return 0, b''
        assert argv[1] == 'create'
        if failure == 'refused': return 2, b''
        if failure == 'uncertain': return 3, KEY
        expiration = deadline + timedelta(seconds=1 if failure == 'long-expiry' else 0)
        data = (h._bytes(1, b'happyranch-node') + h._bytes(2, b'node-id') + h._bytes(3, KEY)
                + h._bytes(7, timestamp(int(expiration.timestamp())))
                + h._bytes(8, timestamp(int(now.timestamp()))))
        if failure == 'reusable': data += b'\x20\x01'
        if failure == 'ephemeral': data += b'\x28\x01'
        if failure == 'tags': data += h._bytes(9, b'tag:foreign')
        return 0, h._bytes(1, data)
    monkeypatch.setattr(h, '_default_run', run)
    if failure is None:
        result = h.mint_local_node('local-op', deadline)
        assert type(result) is h.MintedKey and result.key == KEY.decode() and result.expires_at == deadline
        assert repr(result) == 'MintedKey(<redacted>)'
    else:
        with pytest.raises(MintRefused if failure == 'refused' else MintUncertain) as caught:
            h.mint_local_node('local-op', deadline)
        assert caught.value.__cause__ is None and caught.value.__context__ is None
        assert KEY.decode() not in str(caught.value)
    assert [argv[1] for argv, _ in calls] == ['list', 'list', 'create']
    assert all(h._bytes(1, b'happyranch-node') in kw['input_bytes'] for _, kw in calls)
    create = list(h._fields(calls[-1][1]['input_bytes']))
    assert [number for number, _, _ in create] == [1, 4]  # false flags/no tags on wire
    assert KEY.decode() not in repr([argv for argv, _ in calls])
    records = list(root.rglob('operation-*'))
    assert len(records) == 1 and records[0].parent.name == 'happyranch-node'
    assert KEY not in records[0].read_bytes()


@pytest.mark.parametrize('seconds', [0, -1, 301])
def test_local_node_invalid_expiry_makes_no_admin_calls(root, monkeypatch, seconds) -> None:
    calls = []
    monkeypatch.setattr(h, '_runner_custody', lambda: None)
    monkeypatch.setattr(h, '_default_run', lambda *a, **kw: calls.append(a))
    with pytest.raises(MintRefused):
        h.mint_local_node('local-op', datetime.now(timezone.utc) + timedelta(seconds=seconds))
    assert calls == []


def test_local_node_serialization_and_retry_fence(root, monkeypatch) -> None:
    from runtime.remote_access.home_minter import PreAuthKey
    now = datetime.now(timezone.utc).replace(microsecond=0)
    calls = []
    monkeypatch.setattr(h.RootHeadscaleAdminOperations, 'list_pre_auth_keys', lambda *a, **kw: ())
    def create(*args, **kwargs):
        calls.append(kwargs)
        return PreAuthKey(kwargs['user'], 'id', KEY.decode(), False, False, False,
                          kwargs['expiration'], now, ())
    monkeypatch.setattr(h.RootHeadscaleAdminOperations, 'create_pre_auth_key', create)
    with h._locked('happyranch-node'):
        with pytest.raises(MintRefused): h.mint_local_node('op', now + timedelta(seconds=120))
    assert calls == []
    h.mint_local_node('op', now + timedelta(seconds=120))
    with pytest.raises(MintRefused): h.mint_local_node('op', now + timedelta(seconds=120))
    assert len(calls) == 1


@pytest.mark.parametrize('tamper', ['symlink', 'fifo', 'file', 'mode', 'owner', 'parent-link'])
def test_local_node_custody_refuses_before_admin(root, monkeypatch, tamper) -> None:
    fd = h._directory('happyranch-node')
    os.close(fd)
    path = root / h._STATE.lstrip('/') / 'happyranch-node'
    if tamper in {'symlink', 'fifo', 'file'}:
        path.rmdir()
        if tamper == 'symlink': path.symlink_to(root / 'var/lib')
        elif tamper == 'fifo': os.mkfifo(path, 0o600)
        else: path.write_bytes(b'foreign')
    if tamper == 'mode': path.chmod(0o755)
    if tamper == 'owner':
        original = os.fstat
        def foreign(fd):
            info = original(fd)
            if os.stat(fd).st_ino == path.stat().st_ino:
                return SimpleNamespace(st_uid=1, st_gid=0, st_mode=info.st_mode, st_nlink=info.st_nlink)
            return info
        monkeypatch.setattr(h.os, 'fstat', foreign)
    if tamper == 'parent-link':
        path.parent.rename(path.parent.with_name('kept-minter'))
        path.parent.symlink_to(path.parent.with_name('kept-minter'))
    calls = []
    monkeypatch.setattr(h, '_runner_custody', lambda: None)
    monkeypatch.setattr(h, '_default_run', lambda *a, **kw: calls.append(a))
    with pytest.raises(MintRefused):
        h.mint_local_node('op', datetime.now(timezone.utc) + timedelta(seconds=120))
    assert calls == []


@pytest.mark.parametrize('entry', ['foreign', 'symlink', 'hardlink', 'fifo', 'partial'])
def test_local_node_existing_reservation_is_never_reused(root, monkeypatch, entry) -> None:
    fd = h._directory('happyranch-node')
    os.close(fd)
    path = root / h._STATE.lstrip('/') / 'happyranch-node' / ('operation-' + hashlib.sha256(b'op').hexdigest())
    if entry == 'symlink': path.symlink_to(root / 'absent')
    elif entry == 'fifo': os.mkfifo(path, 0o600)
    else:
        path.write_bytes(b'' if entry == 'partial' else b'foreign')
        if entry == 'hardlink': path.with_suffix('.alias').hardlink_to(path)
    before = path.lstat()
    calls = []
    monkeypatch.setattr(h.RootHeadscaleAdminOperations, 'list_pre_auth_keys', lambda *a, **kw: ())
    monkeypatch.setattr(h.RootHeadscaleAdminOperations, 'create_pre_auth_key', lambda *a, **kw: calls.append(kw))
    with pytest.raises(MintRefused):
        h.mint_local_node('op', datetime.now(timezone.utc) + timedelta(seconds=120))
    assert calls == [] and path.lstat() == before


@pytest.mark.parametrize('phase', ['wire', 'records', 'final'])
def test_list_projection_deadline(monkeypatch, phase: str) -> None:
    # Unique, proto3-omitted history; synthetic time avoids a speed benchmark.
    rows = 8192
    response = b''.join(h._bytes(1, record().replace(h._bytes(2, b'1'),
                                                   h._bytes(2, str(i).encode())))
                        for i in range(rows))
    admin = transport(monkeypatch, response)
    clock = [0.0]
    monkeypatch.setattr(h, 'time', SimpleNamespace(monotonic=lambda: clock[0]), raising=False)
    original_fields, original_record = h._fields, h._record
    projected = [0]

    def fields(data, *args, **kwargs):
        for i, field in enumerate(original_fields(data, *args, **kwargs)):
            if data is response and i == 1024 and phase == 'wire':
                clock[0] = 5.0
            yield field

    def project(data, *args, **kwargs):
        result = original_record(data, *args, **kwargs)
        projected[0] += 1
        if projected[0] == (rows if phase == 'final' else 1024) and phase != 'wire':
            clock[0] = 5.0
        return result

    monkeypatch.setattr(h, '_fields', fields)
    monkeypatch.setattr(h, '_record', project)
    with pytest.raises(MintUncertain) as caught:
        admin.list_pre_auth_keys(user=USER, timeout_s=5)
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert KEY.decode() not in repr(caught.value)
    if phase != 'final':
        assert projected[0] < rows


@pytest.mark.parametrize('verb', ['create', 'list', 'expire'])
def test_transport_deadline_never_returns_late_success(monkeypatch, verb: str) -> None:
    response = b'' if verb == 'expire' else h._bytes(1, record())
    admin = transport(monkeypatch, response)
    clock = [0.0]
    monkeypatch.setattr(h, 'time', SimpleNamespace(monotonic=lambda: clock[0]), raising=False)

    def late(*args, **kwargs):
        clock[0] = 5.0
        return 0, response

    monkeypatch.setattr(h, '_default_run', late)
    with pytest.raises(MintUncertain):
        if verb == 'create':
            admin.create_pre_auth_key(user=USER, reusable=False, ephemeral=False,
                                      expiration=NOW + timedelta(seconds=120), timeout_s=5)
        elif verb == 'list':
            admin.list_pre_auth_keys(user=USER, timeout_s=5)
        else:
            admin.expire_pre_auth_key(user=USER, key=KEY.decode(), timeout_s=5)


def test_create_projection_deadline(monkeypatch) -> None:
    admin = transport(monkeypatch, h._bytes(1, record()))
    clock = [0.0]
    monkeypatch.setattr(h, 'time', SimpleNamespace(monotonic=lambda: clock[0]), raising=False)
    original = h._record

    def late(*args, **kwargs):
        result = original(*args, **kwargs)
        clock[0] = 5.0
        return result

    monkeypatch.setattr(h, '_record', late)
    with pytest.raises(MintUncertain):
        admin.create_pre_auth_key(user=USER, reusable=False, ephemeral=False,
                                  expiration=NOW + timedelta(seconds=120), timeout_s=5)


def test_custody_consumes_original_transport_budget(monkeypatch) -> None:
    admin = transport(monkeypatch, b'')
    clock = [0.0]
    monkeypatch.setattr(h, 'time', SimpleNamespace(monotonic=lambda: clock[0]), raising=False)
    monkeypatch.setattr(h, '_runner_custody', lambda: clock.__setitem__(0, 2.0))
    calls = []
    monkeypatch.setattr(h, '_default_run', lambda argv, **kw: calls.append((argv, kw)) or (0, b''))
    admin.expire_pre_auth_key(user=USER, key=KEY.decode(), timeout_s=5)
    assert calls[0][0][-1] == '3000'
    assert calls[0][1]['timeout'] == 3.0


def admin_user(identity: int = 1, name: str = 'happyranch-node') -> bytes:
    return b'\x08' + h._varint(identity) + h._bytes(2, name.encode())


@pytest.mark.parametrize('verb', ['list-users', 'create-user', 'list-nodes'])
def test_fixed_admin_projection_and_pipes(monkeypatch, verb: str) -> None:
    row = admin_user()
    if verb == 'list-nodes':
        row = b'\x08\x07' + h._bytes(7, row)
    admin = transport(monkeypatch, h._bytes(1, row))
    calls = []
    monkeypatch.setattr(h, '_default_run', lambda argv, **kw: calls.append((argv, kw)) or (0, h._bytes(1, row)))
    if verb == 'list-users':
        got = admin.list_users(timeout_s=1)
        assert [(u.id, u.name) for u in got] == [(1, 'happyranch-node')]
    elif verb == 'create-user':
        got = admin.create_user(name='happyranch-node', timeout_s=1)
        assert (got.id, got.name) == (1, 'happyranch-node')
    else:
        got = admin.list_nodes(user='happyranch-node', timeout_s=1)
        assert [(n.id, n.user.id, n.user.name) for n in got] == [(7, 1, 'happyranch-node')]
    assert len(calls) == 1
    argv, kw = calls[0]
    assert argv[:2] == [h._RUNNER, verb] and len(argv) == 3
    assert 0 < int(argv[2]) <= 1000
    assert kw['input_bytes'] == (b'' if verb == 'list-users' else h._bytes(1, b'happyranch-node'))
    assert 'redacted' in repr(got)


@pytest.mark.parametrize('name', ['foreign', 'happyranch-node\x00', KEY.decode(), '', None])
def test_fixed_admin_invalid_names_before_runner(monkeypatch, name) -> None:
    admin = transport(monkeypatch, b'')
    calls = []
    monkeypatch.setattr(h, '_default_run', lambda *a, **kw: calls.append(a))
    for call in (lambda: admin.create_user(name=name, timeout_s=1),
                 lambda: admin.list_nodes(user=name, timeout_s=1)):
        with pytest.raises(AdminRefused) as caught:
            call()
        assert str(caught.value) == 'refused' and caught.value.__context__ is None
    assert calls == []


@pytest.mark.parametrize('verb', ['list-users', 'create-user', 'list-nodes'])
@pytest.mark.parametrize('code,exception', [(2, AdminRefused), (3, MintUncertain), (-1, MintUncertain)])
def test_fixed_admin_error_categories(monkeypatch, caplog, verb: str, code: int, exception) -> None:
    admin = transport(monkeypatch, KEY, code)
    with pytest.raises(exception) as caught:
        if verb == 'list-users': admin.list_users(timeout_s=1)
        elif verb == 'create-user': admin.create_user(name='happyranch-node', timeout_s=1)
        else: admin.list_nodes(user='happyranch-node', timeout_s=1)
    assert KEY.decode() not in repr(caught.value) + caplog.text
    assert caught.value.__context__ is None and caught.value.__cause__ is None


@pytest.mark.parametrize('bad', [
    h._bytes(1, admin_user(name='foreign')),
    h._bytes(1, admin_user()) * 2,
    h._bytes(1, admin_user() + h._bytes(3, KEY)),
    h._bytes(1, admin_user() + h._bytes(2, b'happyranch-node')),
    h._bytes(1, b'\x08\x81\x00' + h._bytes(2, b'happyranch-node')),
    h._bytes(1, admin_user()) + b'\x0a\x7fabc',
    h._bytes(1, admin_user()) + h._bytes(2, b'next-page'),
    h._bytes(1, admin_user()) * 65,
    b'x' * ((4 << 20) + 1),
])
def test_fixed_admin_complete_projection_or_refuse(monkeypatch, bad: bytes) -> None:
    admin = transport(monkeypatch, bad)
    with pytest.raises(MintUncertain):
        admin.list_users(timeout_s=1)


@pytest.mark.parametrize('verb', ['list-users', 'create-user', 'list-nodes'])
def test_fixed_admin_late_transport(monkeypatch, verb: str) -> None:
    row = admin_user()
    if verb == 'list-nodes':
        row = b'\x08\x07' + h._bytes(7, row)
    response = h._bytes(1, row)
    admin = transport(monkeypatch, response)
    clock = [0.0]
    monkeypatch.setattr(h, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    def late(*a, **kw):
        clock[0] = 1.0
        return 0, response
    monkeypatch.setattr(h, '_default_run', late)
    with pytest.raises(MintUncertain):
        if verb == 'list-users': admin.list_users(timeout_s=1)
        elif verb == 'create-user': admin.create_user(name='happyranch-node', timeout_s=1)
        else: admin.list_nodes(user='happyranch-node', timeout_s=1)


@pytest.mark.parametrize('scenario', ['512', '513', 'duplicate', 'wrong-user', 'unknown', 'missing-id', 'mismatched-user-id'])
def test_fixed_admin_node_inventory(monkeypatch, scenario: str) -> None:
    def node(identity: int = 1, user: bytes = admin_user()) -> bytes:
        return b'\x08' + h._varint(identity) + h._bytes(7, user)
    body = h._bytes(1, node())
    if scenario in ('512', '513'):
        body = b''.join(h._bytes(1, node(i + 1)) for i in range(int(scenario)))
    elif scenario == 'duplicate': body *= 2
    elif scenario == 'wrong-user': body = h._bytes(1, node(user=admin_user(name=USER)))
    elif scenario == 'unknown': body = h._bytes(1, node() + h._bytes(3, KEY))
    elif scenario == 'missing-id': body = h._bytes(1, h._bytes(7, admin_user()))
    elif scenario == 'mismatched-user-id': body += h._bytes(1, node(2, admin_user(2)))
    admin = transport(monkeypatch, body)
    if scenario == '512':
        got = admin.list_nodes(user='happyranch-node', timeout_s=1)
        assert [n.id for n in got] == list(range(1, 513))
    else:
        with pytest.raises(MintUncertain):
            admin.list_nodes(user='happyranch-node', timeout_s=1)


@pytest.mark.parametrize('bad', [b'', h._bytes(1, admin_user(name=USER)), h._bytes(1, admin_user()) * 2])
def test_fixed_admin_create_reply_mismatch(monkeypatch, bad: bytes) -> None:
    with pytest.raises(MintUncertain):
        transport(monkeypatch, bad).create_user(name='happyranch-node', timeout_s=1)


def test_fixed_admin_projection_deadline(monkeypatch) -> None:
    admin = transport(monkeypatch, h._bytes(1, admin_user()))
    clock = [0.0]
    monkeypatch.setattr(h, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    original = h._user_identity
    def late(*a, **kw):
        value = original(*a, **kw)
        clock[0] = 1.0
        return value
    monkeypatch.setattr(h, '_user_identity', late)
    with pytest.raises(MintUncertain):
        admin.list_users(timeout_s=1)

"""Public helper mounts with test-side native transport seams, no host bus."""
from __future__ import annotations

import importlib.util
import copy
import errno
import fcntl
import json
import os
import sys
import threading
import time
import hashlib
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock
from typing import Any

import pytest

from runtime.remote_access import home_activation_backend as backend
from runtime.remote_access import policy_renew_backend as renewal_backend
from scripts.test_home_activation import store, put_pair, put, encoded, receipt_record, RECEIPT  # noqa: F401
from scripts.test_headscale_custody import fixture as custody, b6_setup, b6_minters  # noqa: F401
from scripts.test_installer_token_custody import fixture as renewal_fixture, renewal_intent, renewal_setup  # noqa: F401
from scripts.test_installer_token_custody import staged_inventory

REAL_THREAD = threading.Thread


class Variant:
    def __init__(self, signature: str, value: Any) -> None:
        self.value = value

    def unpack(self) -> Any:
        return self.value


PUBLIC_LIFECYCLE = {
    'Enable': 'enable', 'ReplaceFailed': 'replace-failed', 'Disable': 'disable',
    'RetryPolicyRenewal': 'retry-policy-renewal', 'IssueInvite': 'issue-invite',
    'CancelInvite': 'cancel-invite', 'RecoverInvite': 'recover-invite',
}


def test_enable_admission_diagnostic_preserves_refusal(
        mount: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    import syslog

    sent = Mock()
    monkeypatch.setattr(syslog, 'syslog', sent)
    mount.message.get_member = lambda: 'Enable'
    mount.reader.current_generation.side_effect = RuntimeError('synthetic-secret /private/path :12345')
    mount.helper._dispatch(mount.message)
    mount.helper._error.assert_called_once_with(mount.message, 'unavailable')
    assert not mount.helper.authority.call.called and not mount.reader.effect.called
    sent.assert_called_once_with(syslog.LOG_WARNING,
        'remote-access-enable-refusal origin=helper stage=dispatch.admission.capture exception=RuntimeError')
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize('method,action', PUBLIC_LIFECYCLE.items())
def test_public_lifecycle_introspection_and_fresh_auth(mount: Any, method: str, action: str) -> None:
    v = mount
    methods = {node.attrib['name']: node for node in ET.fromstring(v.h.INTROSPECTION).findall('.//method')}
    assert method in v.h.METHODS and method in methods
    assert [arg.attrib for arg in methods[method]] == [{'name': 'result', 'type': 's', 'direction': 'out'}]
    policy = ET.parse(Path(__file__).resolve().parents[1] /
                      'packaging/linux/polkit/ai.happyranch.remote-access.policy')
    defaults = policy.find('.//action[@id="ai.happyranch.remote-access.' + action + '"]/defaults')
    assert {node.tag: node.text for node in defaults} == {
        'allow_any': 'no', 'allow_inactive': 'no', 'allow_active': 'auth_admin'}
    v.message.get_member = lambda: method
    ids = []
    for _ in range(2):
        v.helper._dispatch(v.message)
        capture = v.helper.inflight
        assert capture is not None and capture.method == method and capture.uid == 1000
        assert capture.deadline == 220  # Auth never exceeds its original 120 seconds.
        call = v.helper.authority.call.call_args
        assert call.args[0] == 'CheckAuthorization'
        subject, actual, details, flags, cancellation = call.args[1].unpack()
        assert subject[0] == 'system-bus-name' and subject[1]['name'].unpack() == ':1.42'
        assert actual == 'ai.happyranch.remote-access.' + action and flags == 1
        assert details and cancellation
        ids.append(cancellation)
        v.helper._authorized(v.helper.authority, Variant('', ((False, False, {}),)), capture)
        assert v.helper.inflight is None
        v.renew()
    assert ids[0] != ids[1] and v.helper.authority.call.call_count == 2
    assert not v.effects and not v.reader.effect.called and not v.helper.audit.path.exists()
    assert not v.helper._reply.called and v.helper._error.call_count == 2


@pytest.mark.parametrize('method', PUBLIC_LIFECYCLE)
@pytest.mark.parametrize('case,category', [
    ('input', 'invalid-request'), ('fd', 'invalid-request'), ('interface', 'unknown-method'),
    ('unknown', 'unknown-method'), ('sender', 'invalid-request'),
    ('uid', 'wrong-uid'), ('owner', 'owner_mismatch'), ('busy', 'busy'), ('arrival', 'busy'),
    ('denied', 'denied'), ('cancelled', 'denied'), ('timeout', 'expired'),
])
def test_public_lifecycle_refusal_has_no_effect_or_audit(
        mount: Any, monkeypatch: pytest.MonkeyPatch, method: str, case: str, category: str) -> None:
    v = mount
    v.message.get_member = lambda: method
    sentinel = object() if case == 'busy' else None
    v.helper.inflight = sentinel
    if case == 'input': v.message.get_signature = lambda: 's'
    if case == 'fd': v.message.get_unix_fd_list = lambda: SimpleNamespace(get_length=lambda: 1)
    if case == 'interface': v.message.get_interface = lambda: 'untrusted.Interface'
    if case == 'unknown': v.message.get_member = lambda: 'RefreshHomeAddress'
    if case == 'sender': v.message.get_sender = lambda: 'caller.selected'
    if case == 'uid':
        # Native credential lookup succeeds, but the B8 release-owner pin differs.
        v.helper.connection.call_sync.return_value = Variant('', (1001,))
        monkeypatch.setattr(v.h, 'bootstrap_owner', lambda uid: SimpleNamespace(uid=uid))
        v.helper.release_uid = 1000
    if case == 'owner':
        def refuse_owner(uid: int) -> None:
            raise v.h.OwnerRefused('owner_mismatch')
        monkeypatch.setattr(v.h, 'bootstrap_owner', refuse_owner)
    v.helper._dispatch(v.message, busy_on_arrival=case == 'arrival')
    if case in ('denied', 'cancelled', 'timeout'):
        capture = v.helper.inflight
        assert capture is not None and v.helper.authority.call.call_count == 1
        if case == 'timeout':
            v.helper.clock = lambda: capture.deadline
            v.helper._expired()
            assert v.helper.inflight is capture  # Cancellation still owns the slot.
            assert v.helper.authority.call.call_args.args[0] == 'CancelCheckAuthorization'
            v.helper._authorized(v.helper.authority, Variant('', ((True, False, {}),)), capture)
        else:
            # Polkit marks a dismissed challenge as not authorized, challengeable.
            v.helper._authorized(v.helper.authority, Variant('', ((False, case == 'cancelled', {}),)), capture)
    else:
        assert not v.helper.authority.call.called
    v.helper._error.assert_called_once_with(v.message, category)
    assert v.helper.inflight is sentinel and not v.helper._reply.called
    assert not v.effects and not v.reader.effect.called and not v.helper.audit.path.exists()


@pytest.fixture
def mount(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SimpleNamespace:
    # Only the unavailable transport is simulated. The shipping helper, B9
    # capture/slot/budget/auth callback and B16 durable writer execute unchanged.
    repository = ModuleType('gi.repository')
    repository.Gio = SimpleNamespace(DBusCallFlags=SimpleNamespace(NONE=0))
    repository.GLib = SimpleNamespace(Variant=Variant, timeout_add=lambda *a: 1,
                                     source_remove=lambda *a: None, Error=RuntimeError,
                                     VariantType=SimpleNamespace(new=lambda s: s))
    monkeypatch.setitem(sys.modules, 'gi', ModuleType('gi'))
    monkeypatch.setitem(sys.modules, 'gi.repository', repository)
    spec = importlib.util.spec_from_file_location('i3a_fixed_helper', Path(__file__).resolve().parents[1] /
                                                 'runtime/remote_access/remote_access_helper.py')
    h = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, h)
    spec.loader.exec_module(h)
    helper = h.RemoteAccessHelper.__new__(h.RemoteAccessHelper)
    helper.lock = threading.RLock()
    helper.inflight = None
    helper.exit_requested = False
    helper.name_owned = True
    helper.status_lock, helper.status_worker = threading.Lock(), None
    statvfs = os.statvfs
    monkeypatch.setattr(h.os, 'statvfs', lambda path: statvfs(tmp_path / path.lstrip('/')))
    helper.clock = lambda: 100.0
    helper.last_activity = 0
    helper.cancelled = helper.authorization_terminal = False
    helper.timer = 0
    helper.lifecycle_operation = helper.lifecycle_result = None
    helper.release_uid = None
    owner = SimpleNamespace(uid=1000)
    helper.connection = SimpleNamespace(call_sync=Mock(return_value=Variant('', (1000,))))
    monkeypatch.setattr(h, 'bootstrap_owner', lambda uid: owner)
    helper.authority = SimpleNamespace(call=Mock(), call_finish=lambda result: result)
    helper._reply, helper._error = Mock(), Mock()
    helper.audit = h._home_audit.HomeAudit(tmp_path / 'audit.jsonl', uid=os.getuid(), gid=os.getgid())
    effects = []
    token = 'protected-generation-and-operation'
    reader = SimpleNamespace(current_generation=Mock(side_effect=lambda _: token))
    def effect(step: str, expected: str, remaining: float, **options: Any) -> dict:
        assert helper.inflight is not None and expected == 'protected-generation-and-operation'
        assert 0 < remaining <= 300
        assert json.loads(helper.audit.path.read_text().splitlines()[-1])['stage'] == 'intent'
        effects.append(step)
        return {'outcome': 'in_progress' if step == 'stop' else 'off', 'residue': []}
    reader.effect = Mock(side_effect=effect)
    monkeypatch.setattr(h._helper_generation, 'HomeLifecycleClient', lambda method, uid: reader)
    def revoke(generation: int, deadline: float, commit: Any) -> None:
        assert helper.inflight is not None and generation == 9 and deadline <= 220
        commit()
        effects.append('revoke')
    helper.control = SimpleNamespace(status=lambda: SimpleNamespace(owned=True, release_uid=1000, generation=9),
                                     revoke_all=Mock(side_effect=revoke))
    # Run the actual worker synchronously after the actual auth callback.
    monkeypatch.setattr(h.threading, 'Thread', lambda target, args, **kw: SimpleNamespace(start=lambda: target(*args)))
    message = SimpleNamespace(get_member=lambda: 'Disable', get_sender=lambda: ':1.42',
        get_unix_fd_list=lambda: None, get_interface=lambda: h.INTERFACE, get_signature=lambda: '')
    def admit() -> None:
        helper._dispatch(message)
        capture = helper.inflight
        helper._authorized(helper.authority, Variant('', ((True, False, {}),)), capture)
    def renew() -> None:
        # A distinct synthetic helper sharing external owners. Projection tests
        # explicitly retain their captured invite observation as fixture input.
        nonlocal helper
        assert helper.inflight is None
        helper = copy.copy(helper)
        helper.lock, helper.status_lock = threading.RLock(), threading.Lock()
        helper.exit_requested, helper.name_owned = False, True
        helper.status_worker = None
        helper.lifecycle_operation = helper.lifecycle_result = None
        helper.cancelled = helper.authorization_terminal = False
        helper.timer, helper.last_activity = 0, helper.clock()
        v.helper = helper
    v = SimpleNamespace(h=h, helper=helper, reader=reader, effects=effects, admit=admit,
                        message=message, renew=renew, statvfs=statvfs)
    return v


@pytest.mark.parametrize('schedule', ['immediate', 'after_authorization'])
def test_worker_settles_after_accepted_disable(
        mount: Any, monkeypatch: pytest.MonkeyPatch, schedule: str) -> None:
    v = mount
    queued, completed = [], []
    v.message.get_member = lambda: 'Disable'

    def effect(*args: Any, **kwargs: Any) -> dict:
        assert json.loads(v.helper.audit.path.read_text().splitlines()[-1])['stage'] == 'intent'
        return {'outcome': 'in_progress' if args[0] == 'stop' else 'off', 'residue': []}

    v.reader.effect.side_effect = effect

    def run_worker(target: Any, args: tuple) -> None:
        worker = REAL_THREAD(target=target, args=args, daemon=True)
        worker.start()
        worker.join(timeout=2)
        assert not worker.is_alive(), 'bounded local worker did not return'
        completed.append(True)

    def new_thread(*, target: Any, args: tuple, **kwargs: Any) -> SimpleNamespace:
        def start() -> None:
            assert v.helper.inflight is v.helper.lifecycle_operation.capture
            v.helper._reply.assert_called_once_with(v.message, 'accepted')
            if schedule == 'immediate':
                # A legal scheduling order: run the real worker to return before
                # Thread.start returns to the authorizing callback.
                run_worker(target, args)
            else:
                queued.append((target, args))
        return SimpleNamespace(start=start)

    monkeypatch.setattr(v.h.threading, 'Thread', new_thread)
    v.admit()
    for target, args in queued:
        run_worker(target, args)
    v.helper._reply.assert_called_once_with(v.message, 'accepted')
    print(json.dumps(dict(schedule=schedule, worker_returned=bool(completed),
        slot_released=v.helper.inflight is None, audit_exists=v.helper.audit.path.exists(),
        effect_calls=v.reader.effect.call_count, outcome=v.helper.lifecycle_result['outcome']), sort_keys=True))
    assert completed == [True]
    assert v.helper.inflight is None, 'accepted worker returned but its global slot stayed sealed'
    assert v.helper.lifecycle_result == {'outcome': 'off', 'residue': []}
    assert v.reader.effect.call_count == 2
    rows = [json.loads(line) for line in v.helper.audit.path.read_text().splitlines()]
    assert [row['stage'] for row in rows] == ['intent', 'outcome'] * 3
    v.helper._error.assert_not_called()


@pytest.mark.parametrize('method', PUBLIC_LIFECYCLE)
@pytest.mark.parametrize('granted', [False, True])
def test_lifecycle_settlement_renews_namespace(mount: Any, method: str, granted: bool) -> None:
    v = mount
    v.message.get_member = lambda: method
    events = []
    def bus(*args: Any) -> Variant:
        if args[3] == 'ReleaseName':
            assert args[4].unpack() == (v.h.BUS_NAME,)
            events.append('release')
            return Variant('', (1,))
        return Variant('', (1000,))
    v.helper.connection.call_sync.side_effect = bus
    loop = SimpleNamespace(quit=lambda: events.append('quit'))
    audit_reads = []
    for name in ('intent', 'outcome', 'uncertain'):
        original = getattr(v.helper.audit, name)
        def audit(*args: Any, original: Any = original, **kwargs: Any) -> Any:
            assert v.helper.inflight is not None
            audit_reads.append(True)
            v.helper._idle(loop)
            assert not events  # Neither effects nor B16 settlement have drained.
            return original(*args, **kwargs)
        setattr(v.helper.audit, name, audit)
    v.helper._dispatch(v.message)
    capture = v.helper.inflight
    v.helper._idle(loop)
    assert not events  # Authentication has not settled.
    v.helper._authorized(v.helper.authority, Variant('', ((granted, False, {}),)), capture)
    assert v.helper.inflight is None
    assert bool(audit_reads) is (granted and method not in v.h._helper_lifecycle.RETIRED_HOME_METHODS)
    # Every worker (including refused/uncertain effects), or terminal denial,
    # seals admission. A status worker must still finish before name release.
    v.helper.status_worker = object()
    v.helper._idle(loop)
    assert not events
    calls = v.helper.authority.call.call_count
    reads = v.reader.current_generation.call_count
    before = v.helper.audit.path.read_bytes() if v.helper.audit.path.exists() else None
    v.helper._error.reset_mock()
    for racing in ('Status', 'StatusV2', 'NextDevice', 'ApprovePending', *PUBLIC_LIFECYCLE):
        v.message.get_member = lambda: racing
        v.helper._dispatch(v.message)
        assert v.helper._error.call_args.args == (v.message, 'unavailable')
    assert v.helper.authority.call.call_count == calls
    assert v.reader.current_generation.call_count == reads
    assert (v.helper.audit.path.read_bytes() if v.helper.audit.path.exists() else None) == before
    v.helper.status_worker = None
    v.helper._idle(loop)
    assert events == ['release', 'quit']


SERVICE_PATHS = tuple('/' + parent + '/happyranch-' + service
    for parent in ('var/lib', 'run') for service in ('headscale', 'connector', 'tsnet-sidecar'))


@pytest.mark.parametrize('method', PUBLIC_LIFECYCLE)
@pytest.mark.parametrize('path', SERVICE_PATHS)
@pytest.mark.parametrize('observation', ['readonly', 'unavailable'])
def test_namespace_guard_before_capture(mount: Any, monkeypatch: pytest.MonkeyPatch,
                                        method: str, path: str, observation: str) -> None:
    v = mount
    v.message.get_member = lambda: method
    def statvfs(actual: str) -> Any:
        if actual != path:
            raise FileNotFoundError(actual)
        if observation == 'unavailable':
            raise PermissionError(actual)
        return SimpleNamespace(f_flag=os.ST_RDONLY)
    monkeypatch.setattr(v.h.os, 'statvfs', statvfs)
    v.helper._dispatch(v.message)
    v.helper._error.assert_called_once_with(v.message, 'unavailable')
    assert not v.helper.connection.call_sync.called  # Before UID/capture reads too.
    assert not v.reader.current_generation.called and not v.helper.authority.call.called
    assert not v.effects and not v.reader.effect.called and not v.helper.audit.path.exists()
    loop = SimpleNamespace(quit=Mock())
    v.helper.connection.call_sync.return_value = Variant('', (1,))
    v.helper._idle(loop)
    loop.quit.assert_called_once_with()


def test_late_service_custody_disable_requires_fresh_namespace(
        custody: Any, mount: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    # The namespace exists before historical service directories appear. No
    # activation/provisioning owner constructs this retained Disable prestate.
    m = mount
    root = custody.root
    path = lambda name: root / name.lstrip('/')
    unit = Path(__file__).resolve().parents[1] / 'packaging/linux/systemd/happyranch-remote-access-helper.service'
    entries = [line.split('=', 1)[1] for line in unit.read_text().splitlines()
               if line.startswith('ReadWritePaths=')]
    def projection() -> tuple[Path, ...]:
        return tuple(path(entry.lstrip('-')) for entry in entries
                     if not entry.startswith('-') or path(entry[1:]).is_dir())
    writable = projection()
    assert not path('/var/lib/happyranch-headscale').exists()
    original_statvfs = m.statvfs
    def statvfs(name: str) -> Any:
        full = path(name)
        result = original_statvfs(full)
        return SimpleNamespace(f_flag=result.f_flag if any(full.is_relative_to(p) for p in writable)
                               else os.ST_RDONLY)
    monkeypatch.setattr(m.h.os, 'statvfs', statvfs)
    activation, _ = b6_setup(custody, monkeypatch, capsys, staging=True)
    b6_minters(root)
    db = path('/var/lib/happyranch-headscale/db.sqlite')
    assert db.is_file()
    lifecycle = backend.HelperLifecycleBackend(custody.installer)
    m.reader.current_generation.side_effect = lambda _: lifecycle.capture('Disable', 1000)
    def effect(step: str, expected: str, remaining: float, **options: Any) -> dict:
        if step == 'stop':
            # Existing synthetic native boundary; real cleanup stays below.
            custody.installer._confirm_quiescent()
            custody.installer._headscale_stopped()
            return {'outcome': 'in_progress', 'residue': []}
        return lifecycle.dispatch('Disable', 1000, step, expected, **options)
    m.reader.effect.side_effect = effect
    m.message.get_member = lambda: 'Disable'
    before = staged_inventory(custody)
    m.helper._dispatch(m.message)
    m.helper._error.assert_called_once_with(m.message, 'unavailable')
    assert not m.helper.authority.call.called and not m.reader.current_generation.called
    assert not m.helper.audit.path.exists() and db.is_file() and activation.exists()
    assert not m.reader.effect.called and not m.helper.control.revoke_all.called
    assert staged_inventory(custody) == before and m.helper.inflight is None
    # A fresh helper gets the current writable projection. Model EROFS outside
    # that projection while the actual B6 cleanup removes the admitted inputs.
    writable = projection()
    blocked = []
    unlink, rmdir = os.unlink, os.rmdir
    def remove(original: Any, name: Any, *, dir_fd: Any = None) -> Any:
        parent = Path(os.readlink(f'/proc/self/fd/{dir_fd}')) if dir_fd is not None else Path.cwd()
        full = parent / name
        if full.is_relative_to(root) and not any(full.is_relative_to(p) for p in writable):
            blocked.append(full)
            raise OSError(errno.EROFS, 'modeled helper namespace is read-only')
        return original(name, dir_fd=dir_fd)
    m.renew()
    m.helper.control.revoke_all = Mock(side_effect=lambda generation, deadline, commit: commit())
    with monkeypatch.context() as fault:
        fault.setattr(os, 'unlink', lambda name, *, dir_fd=None: remove(unlink, name, dir_fd=dir_fd))
        fault.setattr(os, 'rmdir', lambda name, *, dir_fd=None: remove(rmdir, name, dir_fd=dir_fd))
        m.admit()
    assert not blocked and m.helper.lifecycle_result == {'outcome': 'off', 'residue': []}
    assert not db.exists() and not activation.exists()


@pytest.mark.parametrize('revoke', ['succeeded', 'unavailable', 'changed', 'expired', 'lost_reply'])
@pytest.mark.parametrize('cleanup', ['clean', 'residue', 'failed_stop'])
def test_disable_order_slot_audit_and_r7(mount: Any, revoke: str, cleanup: str) -> None:
    v = mount
    if revoke != 'succeeded':
        def failed(*args: Any) -> None:
            if revoke == 'lost_reply':
                args[-1]()
            raise v.h.Refused('unavailable' if revoke == 'lost_reply' else revoke)
        v.helper.control.revoke_all.side_effect = failed
    original = v.reader.effect.side_effect
    def effect(step: str, *args: Any, **options: Any) -> dict:
        if step == 'cleanup':
            assert options == {'revoke_succeeded': revoke == 'succeeded'}
        result = original(step, *args, **options)
        if cleanup == 'failed_stop' and step == 'stop':
            raise OSError('synthetic lost stop')
        if cleanup == 'residue' and step == 'cleanup':
            result = {'outcome': 'partial_cleanup', 'residue': ['CA staging and B7/B11 evidence']}
        # Second admission refuses while the first call still owns the slot.
        with pytest.raises(v.h.Refused, match='busy'):
            v.helper._start_lifecycle(v.message)
        return result
    v.reader.effect.side_effect = effect
    v.admit()
    assert v.effects == (['revoke'] if revoke == 'succeeded' else []) + (
        ['stop'] if cleanup == 'failed_stop' else ['stop', 'cleanup'])
    complete = revoke == 'succeeded' and cleanup == 'clean'
    assert v.helper.lifecycle_result['outcome'] == ('off' if complete else 'partial_cleanup')
    if revoke != 'succeeded':
        assert any('un-revoked connector trust state' in r for r in v.helper.lifecycle_result['residue'])
    assert v.helper.inflight is None
    rows = [json.loads(line) for line in v.helper.audit.path.read_text().splitlines()]
    assert all(row['stage'] in ('intent', 'outcome', 'uncertain') for row in rows)
    assert [r['component'] for r in rows if r['stage'] == 'intent'] == (
        ['credentials', 'units'] if cleanup == 'failed_stop' else ['credentials', 'units', 'live_state'])
    assert any(r['stage'] == ('uncertain' if revoke == 'lost_reply' else 'outcome') for r in rows)
    assert v.helper.control.revoke_all.call_count == (2 if revoke == 'changed' else 1)


@pytest.mark.parametrize('change', ['generation', 'operation', 'uid', 'owner', 'denied', 'deadline'])
def test_native_capture_action_and_cas_before_effect(mount: Any, monkeypatch: pytest.MonkeyPatch, change: str) -> None:
    v = mount
    v.helper._start_lifecycle(v.message)
    capture = v.helper.inflight
    call = v.helper.authority.call.call_args
    assert call.args[0] == 'CheckAuthorization'
    subject, action, details, flags, cancellation = call.args[1].unpack()
    assert subject[0] == 'system-bus-name' and subject[1]['name'].unpack() == ':1.42'
    assert action == 'ai.happyranch.remote-access.disable' and flags == 1 and cancellation
    if change in ('generation', 'operation'):
        v.reader.current_generation.side_effect = lambda _: change
    elif change == 'uid':
        v.helper.connection.call_sync.return_value = Variant('', (1001,))
    elif change == 'owner':
        monkeypatch.setattr(v.h, 'bootstrap_owner', lambda _: object())
    elif change == 'deadline':
        v.helper.clock = lambda: capture.deadline
    v.helper._authorized(v.helper.authority, Variant('', ((change != 'denied', False, {}),)), capture)
    assert not v.effects and not v.reader.effect.called
    assert not v.helper.audit.path.exists() and v.helper.inflight is None
    assert v.helper._error.called and not v.helper._reply.called


def test_auth_loss_retains_slot_until_terminal_ack(mount: Any) -> None:
    v = mount
    v.helper._start_lifecycle(v.message)
    capture = v.helper.inflight
    v.helper.authority.call_finish = Mock(side_effect=OSError('transport loss'))
    v.helper._authorized(v.helper.authority, None, capture)
    assert v.helper.inflight is capture and v.helper.cancelled and not v.effects
    assert v.helper.authority.call.call_args.args[0] == 'CancelCheckAuthorization'
    v.helper.authority.call_finish = lambda _: Variant('', ())
    v.helper._cancelled(v.helper.authority, None, capture)
    assert v.helper.inflight is None


@pytest.mark.parametrize('case', ['complete', 'refused', 'lost', 'audit_late', 'validation_late', 'denied', 'uid'])
def test_retry_policy_public_auth_slot_audit_deadline(mount: Any, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    v = mount
    v.message.get_member = lambda: 'RetryPolicyRenewal'
    def renew(expected: str, deadline: float, commit: Any) -> dict:
        assert expected == 'protected-generation-and-operation' and deadline == 220
        assert v.helper.inflight and json.loads(v.helper.audit.path.read_text().splitlines()[-1])['event'] == 'policy_renew'
        with pytest.raises(v.h.Refused, match='busy'):
            v.helper._start_lifecycle(v.message)
        commit()
        if case == 'lost':
            raise OSError('synthetic lost child reply')
        return {'outcome': 'refused' if case == 'refused' else 'complete'}
    v.reader.retry_policy = Mock(side_effect=renew)
    v.helper._dispatch(v.message)
    capture = v.helper.inflight
    subject, action, _, flags, _ = v.helper.authority.call.call_args.args[1].unpack()
    assert subject[1]['name'].unpack() == ':1.42'
    assert action == 'ai.happyranch.remote-access.retry-policy-renewal' and flags == 1
    assert v.helper.lifecycle_operation.budget.deadline == 220
    outcome = v.helper.audit.outcome
    def settle(*args: Any) -> None:
        assert v.helper.inflight is capture
        outcome(*args)
        if case == 'audit_late':
            v.helper.lifecycle_operation.budget.clock = lambda: 220
        if case == 'validation_late':
            def late(_: float) -> str:
                v.helper.lifecycle_operation.budget.clock = lambda: 220
                return 'protected-generation-and-operation'
            v.reader.current_generation.side_effect = late
    v.helper.audit.outcome = settle
    if case == 'uid':
        v.helper.connection.call_sync.return_value = Variant('', (1001,))
    v.helper._authorized(v.helper.authority, Variant('', ((case != 'denied', False, {}),)), capture)
    assert v.helper.inflight is None
    if case in ('denied', 'uid'):
        assert not v.reader.retry_policy.called and not v.helper.audit.path.exists()
        assert v.helper._error.called and not v.helper._reply.called
        return
    expected = 'complete' if case == 'complete' else 'refused' if case == 'refused' else 'uncertain'
    assert v.helper.lifecycle_result == {'outcome': expected}
    assert v.helper._reply.call_count == 1  # Original accepted reply only.
    rows = list(map(json.loads, v.helper.audit.path.read_text().splitlines()))
    assert rows[0]['stage'] == 'intent' and rows[0]['event'] == 'policy_renew'
    assert rows[-1]['stage'] == ('uncertain' if expected == 'uncertain' else 'outcome')
    assert not v.helper.control.revoke_all.called and not v.reader.effect.called


@pytest.mark.parametrize('case', ['valid', 'drift', 'malformed'])
def test_disable_policy_journal_recovery_before_stop(renewal_fixture: Any, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    v = renewal_fixture
    folder, journal = renewal_intent(v, monkeypatch)
    put_pair(v.root)
    if case == 'drift':
        (folder / 'policy.json').write_bytes(b'{}')
    if case == 'malformed':
        (folder / 'install-transaction.json').write_bytes(b'{"format":"policy-renewal@1"}')
    before = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}
    events = []
    # Native service transport and downstream B6 custody have their own venue.
    # The real P1 recovery/flock/files execute here before the legacy stop seam.
    v.installer.manager = SimpleNamespace(
        show_properties=lambda name, keys: dict(LoadState='not-found', FragmentPath='', DropInPaths='',
            NeedDaemonReload='no', ActiveState='inactive', SubState='dead', MainPID='0', ControlPID='0', ControlGroup='', Job=''),
        disable=lambda name: events.append(('disable', name)))
    def stop() -> None:
        events.append('legacy-stop')
        assert (folder / 'install-transaction.json').exists() is (case != 'valid')
        raise RuntimeError('stop at existing downstream seam')
    monkeypatch.setattr(v.installer, 'stop_and_confirm', stop)
    lifecycle = backend.HelperLifecycleBackend(v.installer)
    expected = lifecycle.capture('Disable', 1000)
    result = lifecycle.dispatch('Disable', 1000, 'stop', expected)
    assert result['outcome'] == 'partial_cleanup'
    assert 'owned remote units: stop unconfirmed' in result['residue']
    assert events[-1] == 'legacy-stop'
    if case == 'valid':
        record = json.loads(journal)
        assert all(hashlib.sha256((folder / name).read_bytes()).hexdigest() == entry['new_sha256']
                   for name, entry in record['files'].items())
    else:
        assert '/etc/happyranch/install-transaction.json' in result['residue']
        assert {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()} == before


@pytest.mark.parametrize('case', ['complete', 'changed_under_lock'])
def test_retry_policy_child_uses_real_fresh_renewal(renewal_fixture: Any, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    v = renewal_fixture
    folder, now = renewal_setup(v, monkeypatch)
    policy = json.loads((folder / 'policy.json').read_bytes())
    from datetime import timedelta
    policy['issued_at'] = (now - timedelta(seconds=3600)).isoformat()
    raw = json.dumps(policy, sort_keys=True).encode()
    (folder / 'policy.json').write_bytes(raw)
    provision = json.loads((folder / 'provision.json').read_bytes())
    provision['digests']['policy.json'] = hashlib.sha256(raw).hexdigest()
    (folder / 'provision.json').write_bytes(json.dumps(provision, sort_keys=True).encode())
    lifecycle = renewal_backend.PolicyRenewalBackend(v.installer)
    expected = lifecycle.capture(1000)
    if case == 'changed_under_lock':
        from runtime.remote_access import installer_lock
        original = installer_lock.installer_state_lock
        injected = []
        @contextmanager
        def changed(installer: Any) -> Any:
            with original(installer) as directory:
                # Valid non-home input changes after capture, before the live
                # callback under the SAME real directory lock. Keep provenance
                # valid so only capture freshness owns the intended refusal.
                sidecar = folder / 'sidecar.json'
                sidecar.write_bytes(sidecar.read_bytes() + b'\n')
                record = json.loads((folder / 'provision.json').read_bytes())
                record['digests']['sidecar.json'] = hashlib.sha256(sidecar.read_bytes()).hexdigest()
                (folder / 'provision.json').write_bytes(json.dumps(record, sort_keys=True).encode())
                injected.append(v.inventory())
                yield directory
        monkeypatch.setattr(installer_lock, 'installer_state_lock', changed)
        with pytest.raises(renewal_backend.PolicyRenewalRefused):
            lifecycle.dispatch('RetryPolicyRenewal', 1000, 'renew', expected,
                deadline=time.monotonic() + 100, before_commit=lambda: None)
        assert injected and v.inventory() == injected[0]
        assert not v.manager.calls and not (folder / 'install-transaction.json').exists()
        return
    before = v.inventory()
    live = []
    result = lifecycle.dispatch('RetryPolicyRenewal', 1000, 'renew', expected,
        deadline=time.monotonic() + 100, before_commit=lambda: live.append(True))
    assert result == {'outcome': 'complete'} and live
    renewed = json.loads((folder / 'policy.json').read_bytes())
    assert renewed['issued_at'] == now.isoformat() and renewed['revision'] == policy['revision'] + 1
    assert json.loads((folder / 'provision.json').read_bytes())['digests']['policy.json'] == hashlib.sha256(
        (folder / 'policy.json').read_bytes()).hexdigest()
    after = v.inventory()
    assert {name: value for name, value in after.items()
            if name not in ('etc/happyranch/policy.json', 'etc/happyranch/provision.json')} == {
        name: value for name, value in before.items()
        if name not in ('etc/happyranch/policy.json', 'etc/happyranch/provision.json')}
    assert all(after[name][1] == before[name][1] for name in before)
    assert not v.manager.calls and not (folder / 'install-transaction.json').exists()


@pytest.mark.parametrize('case,state', [('current', 'current'), ('expired', 'expired'),
    ('timer_stopped', 'renewal_failed'), ('service_failed', 'renewal_failed'),
    ('renewing', 'renewing'), ('journal', 'uncertain')])
def test_policy_status_existing_dimension(renewal_fixture: Any, monkeypatch: pytest.MonkeyPatch,
                                         case: str, state: str) -> None:
    from datetime import datetime, timedelta, timezone
    v = renewal_fixture
    folder, _ = renewal_setup(v, monkeypatch)
    if case == 'journal':
        (folder / 'install-transaction.json').write_bytes(b'{"format":"policy-renewal@1"}')
        (folder / 'install-transaction.json').chmod(0o600)
    if case == 'expired':
        raw = json.loads((folder / 'policy.json').read_bytes())
        raw['issued_at'] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        policy = json.dumps(raw).encode()
        (folder / 'policy.json').write_bytes(policy)
        record = json.loads((folder / 'provision.json').read_bytes())
        record['digests']['policy.json'] = hashlib.sha256(policy).hexdigest()
        (folder / 'provision.json').write_bytes(json.dumps(record).encode())
    def properties(name: str, keys: tuple) -> dict:
        if name.endswith('.timer'):
            return dict(ActiveState='inactive' if case == 'timer_stopped' else 'active',
                        SubState='waiting', UnitFileState='enabled')
        return dict(ActiveState='activating' if case == 'renewing' else 'inactive',
                    SubState='dead', Result='exit-code' if case == 'service_failed' else 'success')
    v.installer.manager = SimpleNamespace(show_properties=properties)
    assert renewal_backend.PolicyRenewalBackend(v.installer).policy_status(1000) == {'policy': state}


@pytest.mark.parametrize('residue', ['/etc/happyranch/install-transaction.json',
    '/etc/systemd/system/happyranch-managed.target.wants/happyranch-remote-access-policy-renew.timer',
    'happyranch-remote-access-policy-renew.service: quiescence unconfirmed'])
def test_disable_named_policy_residue_prevents_b6(mount: Any, residue: str) -> None:
    original = mount.reader.effect.side_effect
    def effect(step: str, *args: Any, **kwargs: Any) -> dict:
        result = original(step, *args, **kwargs)
        return {'outcome': 'partial_cleanup', 'residue': [residue]} if step == 'stop' else result
    mount.reader.effect.side_effect = effect
    mount.admit()
    assert mount.effects == ['revoke', 'stop']
    assert mount.helper.lifecycle_result == {'outcome': 'partial_cleanup', 'residue': [residue]}


@pytest.mark.parametrize('revoked', [True, False])
def test_backend_expectations_come_from_protected_store(store: Any, revoked: bool,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    put_pair(store._io.root)
    io = store._io
    io.layout = 'deb'
    io.manager = SimpleNamespace(show_properties=lambda name, keys: dict(
        ActiveState='inactive', SubState='dead', Job='', MainPID='0', ControlPID='0', ControlGroup=''))
    lifecycle = backend.HelperLifecycleBackend(io)
    expected = lifecycle.capture('Disable', 1000)
    io.cleanup_headscale = Mock(return_value={'state': 'removed'})
    from runtime.remote_access.helper_generation import HomeLifecycleClient
    from runtime.remote_access import helper_generation
    def child(command: Any, *, input: bytes, **kwargs: Any) -> Any:
        request = json.loads(input)
        assert request.pop('deadline') > time.monotonic()
        assert request['revoke_succeeded'] is revoked
        result = lifecycle.dispatch(**request)
        return SimpleNamespace(returncode=0, stdout=json.dumps(result).encode())
    monkeypatch.setattr(helper_generation.subprocess, 'run', child)
    assert HomeLifecycleClient('Disable', 1000).effect('cleanup', expected, 30,
        revoke_succeeded=revoked) == {'outcome': 'off', 'residue': []}
    assert io.cleanup_headscale.call_args.kwargs == dict(expected_release_uid=1000, expected_home_id='home',
        expected_control_plane_id='control', expected_connector_id='connector', expected_generation=7,
        expected_operation_id='b' * 32, revoke_succeeded=revoked)
    updated = receipt_record('ready')
    updated['operation_id'] = 'e' * 32
    put(io.root, RECEIPT, encoded(updated))
    io.cleanup_headscale.reset_mock()
    with pytest.raises(backend.ActivationRefused):
        lifecycle.dispatch('Disable', 1000, 'cleanup', expected)
    assert not io.cleanup_headscale.called


@pytest.mark.parametrize('case', ['complete', 'failed-revoke', 'unsafe-minter'])
def test_mounted_disable_real_b6_result(mount: Any, custody: Any, monkeypatch: pytest.MonkeyPatch,
                                       capsys: Any, case: str) -> None:
    activation, _ = b6_setup(custody, monkeypatch, capsys, staging=True)
    minters = b6_minters(custody.root)
    from runtime.remote_access.installer import _HOME_STATE_MEMBERS
    for path in _HOME_STATE_MEMBERS:
        target = custody.root / path.lstrip('/')
        target.parent.mkdir(mode=0o700, exist_ok=True)
        target.write_bytes(b'synthetic state')
        target.chmod(0o600)
    if case == 'unsafe-minter':
        (minters / 'happyranch-invite' / 'unknown').touch(mode=0o600)
    if case == 'failed-revoke':
        mount.helper.control.revoke_all.side_effect = mount.h.Refused('unavailable')
    lifecycle = backend.HelperLifecycleBackend(custody.installer)
    mount.reader.current_generation.side_effect = lambda remaining: lifecycle.capture('Disable', 1000)
    def effect(step: str, expected: str, remaining: float, **options: Any) -> dict:
        if step == 'stop':
            # Native systemd transport is already synthetic in this venue.
            custody.installer._confirm_quiescent()
            custody.installer._headscale_stopped()
            return {'outcome': 'in_progress', 'residue': []}
        return lifecycle.dispatch('Disable', 1000, step, expected, **options)
    mount.reader.effect.side_effect = effect
    mount.admit()
    assert mount.helper.lifecycle_result['outcome'] == ('off' if case == 'complete' else 'partial_cleanup')
    assert mount.helper.inflight is None
    if case == 'complete':
        assert not activation.exists() and not minters.exists()
        assert all(not (custody.root / p.lstrip('/')).exists() for p in _HOME_STATE_MEMBERS)
    else:
        assert activation.exists() and minters.exists()
        if case == 'failed-revoke':
            assert all((custody.root / p.lstrip('/')).exists() for p in _HOME_STATE_MEMBERS)


@pytest.mark.parametrize('revoked', [True, False])
@pytest.mark.parametrize('case', ['link', 'journal', 'service'])
def test_disable_real_backend_residue_still_attempts_owned_stops(
    mount: Any, custody: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any, case: str, revoked: bool,
) -> None:
    activation, _ = b6_setup(custody, monkeypatch, capsys, live=False, staging=True)
    io = custody.installer
    link = custody.root / 'etc/systemd/system/happyranch-managed.target.wants/happyranch-remote-access-policy-renew.timer'
    if case == 'link':
        link.symlink_to('/usr/lib/systemd/system/happyranch-remote-access-policy-renew.timer')
    journal = custody.root / 'etc/happyranch/install-transaction.json'
    if case == 'journal':
        journal.write_bytes(b'{"format":"policy-renewal@1"}')
        journal.chmod(0o600)
    original = io.manager._run
    def run(args, **kwargs):
        if args[0] == 'systemctl' and args[2:] == ['happyranch-remote-access-policy-renew.timer']:
            custody.calls.append(args)
            return 0, ''  # The manager failed to remove the timer link.
        if args[:2] == ['systemctl', 'show'] and args[2].startswith('happyranch-remote-access-policy-renew.'):
            custody.calls.append(args)
            values = dict(LoadState='loaded', FragmentPath=str(custody.root / 'usr/lib/systemd/system' / args[2]),
                          DropInPaths='', ActiveState='active' if case == 'service' and args[2].endswith('.service') else 'inactive',
                          SubState='dead', MainPID='0', ControlPID='0', ControlGroup='', Job='')
            return 0, '\n'.join(f'{key}={values[key]}' for key in
                (arg.split('=', 1)[1] for arg in args if arg.startswith('--property=')))
        return original(args, **kwargs)
    io.manager._run = run
    io.stop_and_confirm = Mock(wraps=io.stop_and_confirm)
    io.cleanup_headscale = Mock(wraps=io.cleanup_headscale)
    lifecycle = backend.HelperLifecycleBackend(io)
    mount.reader.current_generation.side_effect = lambda _: lifecycle.capture('Disable', 1000)
    mount.reader.effect.side_effect = lambda step, expected, remaining, **kw: lifecycle.dispatch(
        'Disable', 1000, step, expected, **kw)
    if not revoked:
        mount.helper.control.revoke_all.side_effect = mount.h.Refused('unavailable')
    mount.admit()
    io.stop_and_confirm.assert_called_once_with()
    assert ['systemctl', 'stop', 'happyranch-headscale.service'] in custody.calls
    assert not io.cleanup_headscale.called and activation.exists()
    result = mount.helper.lifecycle_result
    assert result['outcome'] == 'partial_cleanup'
    if case == 'link':
        assert link.is_symlink() and '/' + str(link.relative_to(custody.root)) in result['residue']
    if case == 'journal':
        assert journal.read_bytes() == b'{"format":"policy-renewal@1"}'
        assert '/etc/happyranch/install-transaction.json' in result['residue']
    if case == 'service':
        assert 'happyranch-remote-access-policy-renew.service: quiescence unconfirmed' in result['residue']
    if not revoked:
        assert 'un-revoked connector trust state and persisted sidecar state' in result['residue']


@pytest.mark.parametrize('case', ['complete', 'timer', 'service', 'link'])
def test_stop_includes_headscale_and_disables_only_owned_units(store: Any, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    from runtime.remote_access import home_unit_gate
    put_pair(store._io.root)
    io = store._io
    events = []
    io.stop_and_confirm = lambda: events.append('stop-owned')
    io.inventory = lambda: SimpleNamespace(state='owned-activated')
    io._confirm_quiescent = lambda: events.append('confirm-owned')
    io._headscale_receipt = lambda: {}
    io._headscale_cleanup_identity = lambda _: None
    io._headscale_owner = (1234, 1234)
    io._headscale_stopped = lambda: events.append('confirm-headscale')
    monkeypatch.setattr(home_unit_gate.HomeUnitGate, 'inspect', lambda _: events.append('inspect-custody'))
    link = io.root / 'etc/systemd/system/happyranch-managed.target.wants/happyranch-remote-access-policy-renew.timer'
    link.parent.mkdir(parents=True, exist_ok=True)
    for directory in (link.parent, link.parent.parent, link.parent.parent.parent):
        directory.chmod(0o755)
    link.symlink_to('/usr/lib/systemd/system/happyranch-remote-access-policy-renew.timer')
    def properties(name: str, keys: tuple) -> dict:
        directory = os.open(io.root / 'var/lib/happyranch-remote-access', os.O_RDONLY | os.O_DIRECTORY)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(directory)
        renewal = name.startswith('happyranch-remote-access-policy-renew.')
        return dict(LoadState='loaded', DropInPaths='',
            FragmentPath=str(io.root / ('usr/lib/systemd/system' if renewal else 'etc/systemd/system') / name),
            ActiveState='active' if (case == 'timer' and name.endswith('.timer') or
                case == 'service' and name.endswith('policy-renew.service')) else 'inactive',
            SubState='dead', Job='', MainPID='0', ControlPID='0', ControlGroup='')
    def disable(name: str) -> None:
        events.append(('disable', name))
        if name.endswith('.timer') and case != 'link':
            link.unlink()
    io.manager = SimpleNamespace(show_properties=properties,
        stop=lambda name, **kw: events.append(('stop', name)),
        disable=disable)
    lifecycle = backend.HelperLifecycleBackend(io)
    result = lifecycle.dispatch('Disable', 1000, 'stop', lifecycle.capture('Disable', 1000))
    assert events[:3] == [('stop', 'happyranch-remote-access-policy-renew.timer'),
        ('stop', 'happyranch-remote-access-policy-renew.service'), ('disable', 'happyranch-remote-access-policy-renew.timer')]
    if case != 'complete':
        assert result['outcome'] == 'partial_cleanup' and result['residue']
        assert any('policy-renew.' in item for item in result['residue']) and 'stop-owned' in events
        assert ('stop', 'happyranch-headscale.service') in events
        return
    assert result == {'outcome': 'in_progress', 'residue': []} and not link.exists() and not link.is_symlink()
    assert events[3:7] == ['stop-owned', 'inspect-custody', ('stop', 'happyranch-headscale.service'), 'confirm-headscale']
    assert [item[1] for item in events if isinstance(item, tuple) and item[0] == 'disable'] == [
        'happyranch-remote-access-policy-renew.timer', 'happyranch-managed.target', 'happyranch-connector.service', 'happyranch-tsnet-sidecar.service',
        'happyranch-release-verifier.socket']
    assert events[-2:] == ['confirm-owned', 'confirm-headscale']


@pytest.mark.parametrize('boundary', ['intent', 'effect_return', 'disconnect'])
def test_lifecycle_failed_settlement_never_reports_success_or_releases_early(mount: Any, boundary: str) -> None:
    v = mount
    if boundary == 'intent':
        v.helper.audit.intent = Mock(side_effect=OSError('synthetic audit unavailable'))
    else:
        original = v.reader.effect.side_effect
        def effect(step: str, *args: Any) -> dict:
            result = original(step, *args)
            if boundary == 'effect_return':
                v.helper.clock = lambda: 1000.0
                v.helper.lifecycle_operation.budget.clock = v.helper.clock
            else:
                capture = v.helper.inflight
                v.helper._cancel('disconnected')
                assert v.helper.inflight is capture  # Worker retains ownership.
                v.helper.connection.call_sync.side_effect = OSError('sender disconnected')
            return result
        v.reader.effect.side_effect = effect
    v.admit()
    assert v.helper.lifecycle_result['outcome'] == 'partial_cleanup'
    if boundary == 'intent':
        assert not v.effects
    assert v.helper.inflight is None
    assert v.helper._reply.call_count == 1  # No second reply on worker disconnect.


@pytest.mark.parametrize('method', ['CancelInvite', 'RecoverInvite'])
@pytest.mark.parametrize('change', ['uid', 'owner', 'denied', 'deadline', 'generation', 'busy', 'app_id', 'fd', 'sender', 'capture_uid'])
def test_invite_capture_refuses_before_effect(mount: Any, monkeypatch: pytest.MonkeyPatch,
                                             method: str, change: str) -> None:
    v = mount
    v.message.get_member = lambda: method
    if change in ('busy', 'app_id', 'fd', 'sender', 'capture_uid'):
        if change == 'busy': v.helper.inflight = object()
        if change == 'app_id': v.message.get_signature = lambda: 's'
        if change == 'fd': v.message.get_unix_fd_list = lambda: SimpleNamespace(get_length=lambda: 1)
        if change == 'sender': v.message.get_sender = lambda: 'app.selected.identity'
        if change == 'capture_uid':
            v.helper.connection.call_sync.side_effect = [Variant('', (1000,)), Variant('', (1001,))]
        with pytest.raises((v.h.Refused, v.h._helper_lifecycle.LifecycleRefused)):
            v.helper._start_lifecycle(v.message)
        assert not v.helper.authority.call.called
    else:
        v.helper._start_lifecycle(v.message)
        capture = v.helper.inflight
        if change == 'uid': v.helper.connection.call_sync.return_value = Variant('', (1001,))
        if change == 'owner': monkeypatch.setattr(v.h, 'bootstrap_owner', lambda _: object())
        if change == 'generation': v.reader.current_generation.side_effect = lambda _: 'changed-token'
        if change == 'deadline': v.helper.clock = lambda: capture.deadline
        v.helper._authorized(v.helper.authority, Variant('', ((change != 'denied', False, {}),)), capture)
        assert v.helper._error.called and not v.helper._reply.called
        assert v.helper.inflight is None
    assert not v.effects and not v.reader.effect.called and not v.helper.audit.path.exists()


@pytest.mark.parametrize('method', ['CancelInvite', 'RecoverInvite'])
def test_invite_status_staleness_and_binding(mount: Any, monkeypatch: pytest.MonkeyPatch, method: str) -> None:
    v = mount
    v.message.get_member = lambda: method
    token = json.dumps(dict(generation=7, operation='b' * 32))
    v.helper.invite_observation = (1000, token, dict(invite='cancelled' if method == 'CancelInvite' else 'recovered',
        cleanup='complete'), time.monotonic())
    v.renew()  # Projection fixture retains the observation; public exit is tested separately.
    v.helper.status_lock, v.helper.status_worker = threading.Lock(), None
    observed = SimpleNamespace(generation=7, operation='b' * 32, release_uid=1000, phase='ready')
    v.helper.generations = SimpleNamespace(current_generation=lambda _: observed)
    v.helper.control.status = lambda: SimpleNamespace(owned=True, release_uid=1000, ready=True)
    monkeypatch.setattr(v.h.threading, 'Thread', lambda target, args=(), **kw: SimpleNamespace(start=lambda: target(*args)))
    monkeypatch.setattr(v.h.threading, 'Timer', lambda *args: SimpleNamespace(start=lambda: None, cancel=lambda: None))
    v.h.Gio.DBusSendMessageFlags = SimpleNamespace(NONE=0)
    reply = SimpleNamespace(set_body=lambda body: setattr(reply, 'body', body))
    v.message.new_method_reply = lambda: reply
    v.helper.connection.send_message = Mock()
    at = v.helper.invite_observation[-1]
    for case in ('fresh', 'stale', 'operation', 'uid'):
        monkeypatch.setattr(v.h.time, 'monotonic', lambda: at + (31 if case == 'stale' else 1))
        observed.operation = 'e' * 32 if case == 'operation' else 'b' * 32
        observed.release_uid = 1001 if case == 'uid' else 1000
        v.helper._status_v2(v.message, ':1.42')
        if case == 'uid':
            v.helper._error.assert_called_with(v.message, 'changed')
            continue
        body = v.helper.connection.send_message.call_args.args[0].body.unpack()[0]
        result = json.loads(body)
        assert set(result) == {'version', 'setup', 'runtime', 'invite', 'address', 'policy', 'cleanup'}
        expected = ('cancelled' if method == 'CancelInvite' else 'recovered') if case == 'fresh' else (
            'stale' if case == 'stale' else 'unknown')
        assert result['invite']['state'] == expected
        assert result['cleanup']['state'] == ('complete' if case == 'fresh' else 'stale' if case == 'stale' else 'unknown')


@pytest.mark.parametrize('case', ['acknowledged', 'denied', 'late'])
def test_invite_pipe_live_callback(monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    from runtime.remote_access import helper_invite
    called = []
    source = Path(__file__).resolve().parents[1] / 'runtime/remote_access/helper_invite.py'
    script = ('import sys,json,runpy; h=runpy.run_path(sys.argv[1]); '
              'r=json.loads(sys.stdin.buffer.readline()); h["confirm"](r["deadline"]); '
              'print(\'{"outcome":"complete","invite":"none","cleanup":"not_needed"}\',flush=True)')
    deadline = time.monotonic() + 2
    def commit() -> None:
        called.append(True)
        if case == 'denied': raise ValueError('caller gone')
        if case == 'late': monkeypatch.setattr(helper_invite.time, 'monotonic', lambda: deadline)
    if case == 'acknowledged':
        result = helper_invite.effect((sys.executable, '-I', '-c', script, str(source)), {'deadline': deadline}, commit)
        assert result == dict(outcome='complete', invite='none', cleanup='not_needed')
    else:
        with pytest.raises((ValueError, TimeoutError)):
            helper_invite.effect((sys.executable, '-I', '-c', script, str(source)), {'deadline': deadline}, commit)
    assert called == [True]


@pytest.mark.parametrize('change', ['uid', 'owner', 'denied', 'deadline', 'generation', 'busy', 'input', 'fd', 'sender', 'capture_uid'])
def test_issue_invite_admission_refuses_before_effect(mount: Any, monkeypatch: pytest.MonkeyPatch,
                                                     change: str) -> None:
    v = mount
    v.message.get_member = lambda: 'IssueInvite'
    if change in ('busy', 'input', 'fd', 'sender', 'capture_uid'):
        if change == 'busy': v.helper.inflight = object()
        if change == 'input': v.message.get_signature = lambda: 's'
        if change == 'fd': v.message.get_unix_fd_list = lambda: SimpleNamespace(get_length=lambda: 1)
        if change == 'sender': v.message.get_sender = lambda: 'caller-selected'
        if change == 'capture_uid':
            v.helper.connection.call_sync.side_effect = [Variant('', (1000,)), Variant('', (1001,))]
        with pytest.raises((v.h.Refused, v.h._helper_lifecycle.LifecycleRefused)):
            v.helper._start_lifecycle(v.message)
        assert not v.helper.authority.call.called
    else:
        v.helper._start_lifecycle(v.message)
        capture = v.helper.inflight
        if change == 'uid': v.helper.connection.call_sync.return_value = Variant('', (1001,))
        if change == 'owner': monkeypatch.setattr(v.h, 'bootstrap_owner', lambda _: object())
        if change == 'generation': v.reader.current_generation.side_effect = lambda _: 'changed-token'
        if change == 'deadline': v.helper.clock = lambda: capture.deadline
        v.helper._authorized(v.helper.authority, Variant('', ((change != 'denied', False, {}),)), capture)
        assert v.helper._error.called and not v.helper._reply.called and v.helper.inflight is None
    assert not v.effects and not v.reader.effect.called
    assert not v.helper.audit.path.exists()


@pytest.mark.parametrize('method', ['CancelInvite', 'RecoverInvite'])
def test_invite_default_frame_cap_unchanged(method: str) -> None:
    from runtime.remote_access import helper_invite
    raw = json.dumps({'result': 'A' * 4200}) + '\n'
    script = 'import sys,json; json.loads(sys.stdin.buffer.readline()); sys.stdout.write(sys.argv[1]); sys.stdout.flush()'
    with pytest.raises(ValueError, match='unavailable'):
        helper_invite.effect((sys.executable, '-I', '-c', script, raw),
                            dict(method=method, deadline=time.monotonic() + 2), lambda: None)
    for size, accepted in ((4097, True), (4098, False)):
        data = b'{"x":"' + b'A' * (size - 9) + b'"}\n'
        assert len(data) == size
        for options in ({}, {'limit': 4096}):
            read, write = os.pipe()
            try:
                os.write(write, data)
                os.close(write)
                if accepted:
                    assert helper_invite.read_frame(read, time.monotonic() + 2, **options) == {'x': 'A' * (size - 9)}
                else:
                    with pytest.raises(ValueError, match='unavailable'):
                        helper_invite.read_frame(read, time.monotonic() + 2, **options)
            finally:
                os.close(read)


@pytest.mark.parametrize('method', ['Disable'])
def test_other_lifecycle_methods_keep_accepted_first(mount: Any, method: str) -> None:
    v = mount
    v.message.get_member = lambda: method
    seen = []
    def run(operation: Any) -> None:
        v.helper._reply.assert_called_once_with(v.message, 'accepted')
        assert v.helper.inflight is operation.capture
        seen.append(method)
        v.helper.release(operation.capture)
    v.helper._run_lifecycle = run
    v.admit()
    assert seen == [method] and v.helper.inflight is None


def test_issue_error_uses_closed_category_without_secret(mount: Any, caplog: Any) -> None:
    v = mount
    v.message.get_member = lambda: 'IssueInvite'
    v.h.Gio.DBusSendMessageFlags = SimpleNamespace(NONE=0)
    v.message.new_method_error_literal = Mock(return_value=object())
    v.helper.connection.send_message = Mock()
    v.h.RemoteAccessHelper._error(v.helper, v.message, 'uncertain')
    v.message.new_method_error_literal.assert_called_once_with(v.h.INTERFACE + '.Error.uncertain', 'uncertain')
    v.h.RemoteAccessHelper._error(v.helper, v.message, 'hrinvite2:SYNTHETIC_PRIVATE_VALUE')
    v.message.new_method_error_literal.assert_called_with(v.h.INTERFACE + '.Error.unavailable', 'unavailable')
    assert 'hrinvite2:' not in caplog.text


@pytest.mark.parametrize('method', ['Enable', 'ReplaceFailed', 'IssueInvite', 'CancelInvite', 'RecoverInvite'])
@pytest.mark.parametrize('token', ['clean', 'prepared', 'ready', 'migration pending', 'retired'])
def test_public_retired_activation_has_no_effect(mount: Any, tmp_path: Path, method: str, token: str) -> None:
    v = mount
    sentinel = tmp_path / 'local-work'
    sentinel.write_bytes(b'synthetic local work')
    v.message.get_member = lambda: method
    v.reader.current_generation.side_effect = lambda _: token
    v.reader.effect.return_value = {'outcome': 'ready', 'residue': []}
    v.reader.effect.side_effect = None
    v.admit()
    assert v.helper.authority.call.call_args.args[1].unpack()[1] == (
        'ai.happyranch.remote-access.' + v.h.LIFECYCLE_ACTIONS[method])
    v.helper._error.assert_called_once_with(v.message, 'home-mode-retired')
    assert not v.helper._reply.called and not v.reader.effect.called
    assert not v.helper.audit.path.exists() and v.helper.inflight is None
    assert sentinel.read_bytes() == b'synthetic local work'


@pytest.mark.parametrize('method', ['Enable', 'ReplaceFailed', 'IssueInvite', 'CancelInvite', 'RecoverInvite'])
def test_retired_activation_error_has_exact_copy(mount: Any, method: str) -> None:
    from runtime.remote_access.home_retirement import MESSAGE
    v = mount
    v.message.get_member = lambda: method
    v.h.Gio.DBusSendMessageFlags = SimpleNamespace(NONE=0)
    v.message.new_method_error_literal = Mock(return_value=object())
    v.helper.connection.send_message = Mock()
    v.h.RemoteAccessHelper._error(v.helper, v.message, 'home-mode-retired')
    v.message.new_method_error_literal.assert_called_once_with(
        v.h.INTERFACE + '.Error.home_mode_retired', MESSAGE)

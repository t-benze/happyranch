"""I3a/I3b private mounts with test-side native transport seams, no host bus."""
from __future__ import annotations

import importlib.util
import fcntl
import json
import os
import sys
import threading
import time
import ssl
import hashlib
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock
from typing import Any

import pytest

from runtime.remote_access import home_activation_backend as backend
from scripts.test_home_activation import store, put_pair, put, encoded, receipt_record, RECEIPT  # noqa: F401
from scripts.test_headscale_custody import fixture as custody, b6_setup, b6_minters  # noqa: F401
from scripts.test_installer_token_custody import fixture as renewal_fixture, renewal_intent, renewal_setup  # noqa: F401


class Variant:
    def __init__(self, signature: str, value: Any) -> None:
        self.value = value

    def unpack(self) -> Any:
        return self.value


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
        helper._start_lifecycle(message)
        capture = helper.inflight
        helper._authorized(helper.authority, Variant('', ((True, False, {}),)), capture)
    return SimpleNamespace(h=h, helper=helper, reader=reader, effects=effects, admit=admit, message=message)


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


@pytest.mark.parametrize('method,step,outcome,setup', [('Enable', 'enable', 'ready', 'ready'),
    ('Enable', 'enable', 'prepared', 'in_progress'), ('ReplaceFailed', 'replace', 'off', 'off')])
def test_lifecycle_private_mount_fresh_auth_and_audit(
        mount: Any, method: str, step: str, outcome: str, setup: str) -> None:
    v = mount
    v.message.get_member = lambda: method
    def effect(actual: str, expected: str, remaining: float) -> dict:
        assert actual == step and 0 < remaining <= 480 and v.helper.inflight
        assert json.loads(v.helper.audit.path.read_text().splitlines()[-1])['event'] == 'activation'
        return {'outcome': outcome, 'residue': []}
    v.reader.effect.side_effect = effect
    v.helper._dispatch(v.message)
    v.helper._error.assert_called_once_with(v.message, 'unknown-method')
    assert not v.helper.authority.call.called and not v.reader.effect.called
    for _ in range(2):
        v.admit()
        assert v.helper.lifecycle_result == {'outcome': setup, 'residue': []}
        assert v.helper.inflight is None
    assert [row['outcome'] for row in map(json.loads, v.helper.audit.path.read_text().splitlines())
            if row['stage'] == 'outcome'] == ['succeeded', 'succeeded']
    assert v.helper.authority.call.call_count == 2
    assert v.helper.authority.call.call_args.args[1].unpack()[1] == (
        'ai.happyranch.remote-access.' + v.h.LIFECYCLE_ACTIONS[method])
    assert all(name not in v.h.METHODS and name not in v.h.INTROSPECTION
               for name in ('Enable', 'ReplaceFailed', 'Disable'))


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
def test_retry_policy_private_auth_slot_audit_deadline(mount: Any, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    v = mount
    v.message.get_member = lambda: 'RetryPolicyRenewal'
    v.helper._dispatch(v.message)
    v.helper._error.assert_called_once_with(v.message, 'unknown-method')
    v.helper._error.reset_mock()
    assert 'RetryPolicyRenewal' not in v.h.METHODS and 'RetryPolicyRenewal' not in v.h.INTROSPECTION

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
    v.helper._start_lifecycle(v.message)
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
    put_pair(v.root)
    lifecycle = backend.HelperLifecycleBackend(v.installer)
    expected = lifecycle.capture('RetryPolicyRenewal', 1000)
    before = (folder / 'policy.json').read_bytes(), (folder / 'provision.json').read_bytes()
    if case == 'changed_under_lock':
        from runtime.remote_access.home_activation_txn import HomeActivationTransactionStore
        original = HomeActivationTransactionStore.locked
        @contextmanager
        def changed(store: Any) -> Any:
            with original(store) as directory:
                updated = receipt_record('ready')
                updated['operation_id'] = 'e' * 32
                put(v.root, RECEIPT, encoded(updated))
                yield directory
        monkeypatch.setattr(HomeActivationTransactionStore, 'locked', changed)
        with pytest.raises(backend.ActivationRefused):
            lifecycle.dispatch('RetryPolicyRenewal', 1000, 'renew', expected,
                deadline=time.monotonic() + 100, before_commit=lambda: None)
        assert ((folder / 'policy.json').read_bytes(), (folder / 'provision.json').read_bytes()) == before
        return
    live = []
    result = lifecycle.dispatch('RetryPolicyRenewal', 1000, 'renew', expected,
        deadline=time.monotonic() + 100, before_commit=lambda: live.append(True))
    assert result == {'outcome': 'complete'} and live
    renewed = json.loads((folder / 'policy.json').read_bytes())
    assert renewed['issued_at'] == now.isoformat() and renewed['revision'] == policy['revision'] + 1
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
    assert backend.HelperLifecycleBackend(v.installer).policy_status(1000) == {'policy': state}


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


def test_replacement_binds_inventory_before_auth_and_uses_captured_uid(store: Any) -> None:
    io = store._io
    io.replacement_inventory = Mock(return_value=SimpleNamespace(
        journal_generation=None, inventory_generation='a' * 64,
        installation=SimpleNamespace(state='known-failed-or-partial')))
    io.begin_replacement = Mock()
    lifecycle = backend.HelperLifecycleBackend(io)
    expected = lifecycle.capture('ReplaceFailed', 1000)
    lifecycle.dispatch('ReplaceFailed', 1000, 'replace', expected)
    io.begin_replacement.assert_called_once_with(expected_release_uid=1000, expected_generation='a' * 64)
    io.begin_replacement.reset_mock()
    io.replacement_inventory.return_value.inventory_generation = 'b' * 64
    with pytest.raises(backend.ActivationRefused):
        lifecycle.dispatch('ReplaceFailed', 1000, 'replace', expected)
    assert not io.begin_replacement.called


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


@pytest.fixture
def invite_mount(mount: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from runtime.remote_access import helper_invite_backend as b
    from scripts.test_invite_recovery import issued
    from scripts.test_home_minter import setup, key_record
    v = mount
    issuer, original, _, trust, _, _, control, _, _ = issued(tmp_path)
    destination = tmp_path / b.HOME_ISSUER
    destination.parent.mkdir(parents=True)
    issuer._state_dir.rename(destination)
    issuer._state_dir = destination
    locked, admin, _, _, events = setup()
    admin.keys = (key_record(key=original.key),)
    held = []
    @contextmanager
    def lock() -> Any:
        held.append(True)
        try:
            yield locked
        finally:
            held.pop()
            if getattr(v, 'lock_exit_failure', False):
                raise OSError('synthetic lost settlement')
    monkeypatch.setattr(b.home_admin, '_locked', lock)
    v.helper.clock = time.monotonic
    token = dict(uid=1000, generation=7, operation='b' * 32, configuration='c' * 64, ca='d' * 64)
    child = b.HelperInviteBackend(SimpleNamespace(root=tmp_path, root_owner=(os.getuid(), os.getgid())))
    monkeypatch.setattr(child, '_config', lambda uid: (token, issuer._config))
    control.status = Mock(side_effect=lambda: SimpleNamespace(owned=True, release_uid=1000,
                                                            generation=trust.snapshot()[1]))
    monkeypatch.setattr(b, 'ControlClient', lambda: control)
    v.reader.current_generation.side_effect = lambda _: child.capture(1000)
    def recover(expected: str, deadline: float, commit: Any) -> dict:
        assert v.helper.inflight and deadline == v.helper.lifecycle_operation.budget.deadline
        assert 0 < deadline - v.helper.clock() <= 120
        rows = list(map(json.loads, v.helper.audit.path.read_text().splitlines()))
        assert rows[-1]['stage'] == 'intent'
        with pytest.raises(v.h.Refused, match='busy'):
            v.helper._start_lifecycle(v.message)
        return child.dispatch(v.message.get_member(), 1000, 'recover', expected,
                              deadline=deadline, before_commit=commit)
    v.reader.recover_invite = Mock(side_effect=recover)
    original_list = admin.list_pre_auth_keys
    def list_keys(**kw: Any) -> tuple:
        assert held and trust.load().invite_record is None
        with issuer._recovery_record() as (_, current):
            assert current['outcome'] == 'uncertain', 'journal flock released before minter'
        return original_list(**kw)
    admin.list_pre_auth_keys = list_keys
    v.issuer, v.original, v.trust, v.control = issuer, original, trust, control
    v.admin, v.events, v.child, v.token = admin, events, child, token
    v.journal = destination / 'invite-issuer.json'
    v.record = lambda: json.loads(v.journal.read_bytes())
    return v


@pytest.mark.parametrize('method', ['CancelInvite', 'RecoverInvite'])
@pytest.mark.parametrize('case', ['complete', 'absent', 'minter', 'ipc', 'lost_reply', 'journal_busy', 'late', 'audit_late', 'connector_uid', 'lock_exit'])
def test_invite_mount_current_operation_and_audit(invite_mount: Any, monkeypatch: pytest.MonkeyPatch,
                                                method: str, case: str) -> None:
    v = invite_mount
    v.message.get_member = lambda: method
    initial = v.record()
    lock = None
    if case == 'absent':
        v.journal.unlink()
        v.control.status.side_effect = OSError('connector unavailable')
    elif case in ('ipc', 'lost_reply'):
        original = v.control.invalidate_invite
        def fail(*args: Any) -> None:
            if case == 'lost_reply':
                original(*args)
            raise OSError('synthetic lost IPC')
        v.control.invalidate_invite = fail
    elif case == 'minter':
        v.admin.ignore_expire = True
    elif case == 'journal_busy':
        lock = (v.journal.parent / 'invite-issuer.lock').open('rb')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    elif case == 'connector_uid':
        v.control.status.side_effect = lambda: SimpleNamespace(owned=True, release_uid=1001, generation=9)
    elif case == 'lock_exit':
        v.lock_exit_failure = True
    elif case == 'late':
        original = v.reader.recover_invite.side_effect
        def late(*args: Any) -> dict:
            result = original(*args)
            v.helper.clock = v.helper.lifecycle_operation.budget.clock = lambda: args[1]
            return result
        v.reader.recover_invite.side_effect = late
    elif case == 'audit_late':
        original = v.helper.audit.outcome
        def audit_late(*args: Any) -> None:
            assert v.helper.inflight is not None
            original(*args)  # Real B16 append/fsync settles before its budget is consumed.
            deadline = v.helper.lifecycle_operation.budget.deadline
            v.helper.clock = v.helper.lifecycle_operation.budget.clock = lambda: deadline + 1
        v.helper.audit.outcome = audit_late
    try:
        v.admit()
    finally:
        if lock:
            lock.close()
    terminal = 'cancelled' if method == 'CancelInvite' else 'recovered'
    expected = dict(outcome='complete', invite=terminal, cleanup='complete')
    if case == 'absent': expected = dict(outcome='complete', invite='none', cleanup='not_needed')
    if case == 'minter': expected = dict(outcome='partial_cleanup', invite='cancelled', cleanup='uncertain')
    if case in ('journal_busy', 'connector_uid'): expected = dict(outcome='refused', invite='unknown', cleanup='unknown')
    if case in ('ipc', 'lost_reply', 'late', 'audit_late', 'lock_exit'): expected = dict(outcome='uncertain', invite='uncertain', cleanup='uncertain')
    assert v.helper.lifecycle_result == expected
    assert v.helper.inflight is None and len(v.original.calls) == 1, 'no new mint'
    assert bool(v.events) == (case in ('complete', 'minter', 'late', 'audit_late', 'lock_exit'))
    if case in ('complete', 'late', 'audit_late', 'lock_exit'):
        assert v.record() == initial | {'outcome': 'recovered'}
    elif case != 'absent':
        assert v.record() == initial | {'outcome': 'issued' if case in ('journal_busy', 'connector_uid') else 'uncertain'}
    if case in ('complete', 'minter', 'lost_reply', 'late', 'audit_late', 'lock_exit'):
        assert v.trust.load().invite_record is None
    else:
        assert v.trust.load().invite_record is not None
    rows = list(map(json.loads, v.helper.audit.path.read_text().splitlines()))
    assert len(rows) == (3 if case == 'audit_late' else 2) and rows[0]['stage'] == 'intent'
    assert rows[0]['event'] == ('invite_cancelled' if method == 'CancelInvite' else 'invite_recovered')
    assert rows[-1]['outcome'] == ('uncertain' if case in ('ipc', 'lost_reply', 'late', 'audit_late', 'lock_exit') else
                                   'refused' if case in ('journal_busy', 'connector_uid') else 'succeeded')
    if case == 'audit_late':
        assert [row['stage'] for row in rows] == ['intent', 'outcome', 'uncertain']
        assert rows[1]['outcome'] == 'succeeded'  # Preserve the settled B16 record.
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
        v.helper._status_v2(v.message, ':1.42')
        body = v.helper.connection.send_message.call_args.args[0].body.unpack()[0]
        status = json.loads(body)
        assert status['invite']['state'] == status['cleanup']['state'] == 'uncertain'
        assert status['invite']['age_seconds'] == status['cleanup']['age_seconds'] == 0
    call = v.helper.authority.call.call_args.args[1].unpack()
    assert call[0][1]['name'].unpack() == ':1.42'
    assert call[1] == 'ai.happyranch.remote-access.' + v.h.LIFECYCLE_ACTIONS[method]
    assert call[3] == 1 and call[4]
    assert method not in v.h.METHODS and method not in v.h.INTROSPECTION
    v.helper._dispatch(v.message)
    v.helper._error.assert_called_with(v.message, 'unknown-method')


@pytest.mark.parametrize('method', ['CancelInvite', 'RecoverInvite'])
@pytest.mark.parametrize('change', ['uid', 'owner', 'denied', 'deadline', 'generation', 'busy', 'app_id', 'fd', 'sender', 'capture_uid'])
def test_invite_capture_refuses_before_effect(invite_mount: Any, monkeypatch: pytest.MonkeyPatch,
                                             method: str, change: str) -> None:
    v = invite_mount
    v.message.get_member = lambda: method
    initial = v.journal.read_bytes()
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
        if change == 'generation': v.token['operation'] = 'e' * 32
        if change == 'deadline': v.helper.clock = lambda: capture.deadline
        v.helper._authorized(v.helper.authority, Variant('', ((change != 'denied', False, {}),)), capture)
        assert v.helper._error.called and not v.helper._reply.called
        assert v.helper.inflight is None
    assert v.journal.read_bytes() == initial and v.trust.load().invite_record is not None
    assert not v.events and not v.reader.recover_invite.called and not v.helper.audit.path.exists()


@pytest.mark.parametrize('method', ['CancelInvite', 'RecoverInvite'])
@pytest.mark.parametrize('change', ['uid', 'owner', 'generation', 'deadline', 'disconnect'])
def test_invite_live_callback_refuses_changes(invite_mount: Any, monkeypatch: pytest.MonkeyPatch,
                                            method: str, change: str) -> None:
    v = invite_mount
    v.message.get_member = lambda: method
    original = type(v.issuer)._write
    def write(issuer: Any, parent: int, record: dict) -> None:
        original(issuer, parent, record)
        if change == 'uid': v.helper.connection.call_sync.return_value = Variant('', (1001,))
        if change == 'owner': monkeypatch.setattr(v.h, 'bootstrap_owner', lambda _: object())
        if change == 'generation': v.token['operation'] = 'e' * 32
        if change == 'deadline':
            v.helper.clock = v.helper.lifecycle_operation.budget.clock = lambda: v.helper.lifecycle_operation.budget.deadline
        if change == 'disconnect': v.helper.connection.call_sync.side_effect = OSError('caller gone')
    monkeypatch.setattr(type(v.issuer), '_write', write)
    v.admit()
    assert v.record()['outcome'] == 'uncertain'
    assert v.helper.lifecycle_result['outcome'] == 'uncertain'
    assert v.trust.load().invite_record is not None and not v.control.calls and not v.events
    assert v.helper.inflight is None


@pytest.mark.parametrize('method', ['CancelInvite', 'RecoverInvite'])
def test_invite_audit_failure_blocks_effects(invite_mount: Any, method: str) -> None:
    v = invite_mount
    v.message.get_member = lambda: method
    initial = v.journal.read_bytes()
    v.helper.audit.intent = Mock(side_effect=OSError('audit unavailable'))
    v.admit()
    assert v.helper.lifecycle_result['outcome'] == 'refused'
    assert v.journal.read_bytes() == initial and v.trust.load().invite_record is not None
    assert not v.reader.recover_invite.called and not v.events


@pytest.mark.parametrize('method', ['CancelInvite', 'RecoverInvite'])
def test_invite_status_staleness_and_binding(invite_mount: Any, monkeypatch: pytest.MonkeyPatch, method: str) -> None:
    v = invite_mount
    v.message.get_member = lambda: method
    v.admit()
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


@pytest.mark.parametrize('change', ['current', 'uid', 'config', 'ca', 'operation'])
def test_invite_protected_capture(store: Any, change: str) -> None:
    from runtime.remote_access.helper_invite_backend import HelperInviteBackend
    from scripts.test_home_activation import config_record, CONFIG
    from scripts.test_invite_v2 import SWIFT_CA
    ca = ssl.DER_cert_to_PEM_cert(SWIFT_CA).encode('ascii')
    config, receipt = config_record(), receipt_record('ready')
    config['ca_der_sha256'] = receipt['ca_der_sha256'] = hashlib.sha256(SWIFT_CA).hexdigest()
    receipt['member_digests']['/' + CONFIG] = hashlib.sha256(encoded(config)).hexdigest()
    receipt['member_digests']['/etc/happyranch/control-ca.pem'] = hashlib.sha256(ca).hexdigest()
    put(store._io.root, CONFIG, encoded(config))
    put(store._io.root, RECEIPT, encoded(receipt))
    path = put(store._io.root, 'etc/happyranch/control-ca.pem', ca)
    path.chmod(0o640)
    store._io._account = lambda **kw: (os.getuid(), os.getgid())
    store._io._headscale_members = lambda: {'/etc/happyranch/control-ca.pem': ((os.getuid(), os.getgid()), 0o640)}
    child = HelperInviteBackend(store._io)
    initial = child.capture(1000)
    assert json.loads(initial) == dict(uid=1000, generation=7, operation='b' * 32,
        configuration=receipt['member_digests']['/' + CONFIG], ca=receipt['ca_der_sha256'])
    assert backend.HelperLifecycleBackend(store._io).capture('CancelInvite', 1000) == initial
    assert backend.HelperLifecycleBackend(store._io).capture('RecoverInvite', 1000) == initial
    if change == 'uid':
        with pytest.raises(ValueError): child.capture(1001)
    elif change == 'config':
        config['dial_ip'] = '192.168.1.41'
        put(store._io.root, CONFIG, encoded(config))
        with pytest.raises(ValueError): child.capture(1000)
    elif change == 'ca':
        path.write_bytes(ca + b'\n')
        with pytest.raises(ValueError): child.capture(1000)
    elif change == 'operation':
        receipt['operation_id'] = 'e' * 32
        put(store._io.root, RECEIPT, encoded(receipt))
        assert child.capture(1000) != initial
    else:
        assert child.capture(1000) == initial


@pytest.fixture
def issue_mount(mount: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Real issuer/minter/journal/serving pairing; only native/admin transport is isolated."""
    from datetime import datetime, timezone
    from runtime.remote_access import helper_invite_backend as b
    from runtime.remote_access.invite_control import InviteControl
    from scripts.test_invite_issuer import setup as issuer_setup
    from scripts.test_home_minter import setup as minter_setup
    v = mount
    issuer, _, manager, trust, clock = issuer_setup(tmp_path)
    locked, admin, records, admin_clock, events = minter_setup()
    clock.now = admin_clock.now = datetime.now(timezone.utc)
    destination = tmp_path / b.HOME_ISSUER
    destination.parent.mkdir(parents=True)
    issuer._state_dir.rename(destination)
    issuer._state_dir = destination
    held = []
    @contextmanager
    def lock() -> Any:
        held.append(True)
        try:
            yield locked
        finally:
            held.pop()
    monkeypatch.setattr(b.home_admin, '_locked', lock)
    token = dict(uid=1000, generation=7, operation='b' * 32, configuration='c' * 64, ca='d' * 64)
    child = b.HelperInviteBackend(SimpleNamespace(root=tmp_path, root_owner=(os.getuid(), os.getgid())))
    monkeypatch.setattr(child, '_config', lambda uid: (token, issuer._config))
    control = SimpleNamespace(status=Mock(side_effect=lambda: SimpleNamespace(
        owned=True, release_uid=1000, generation=trust.snapshot()[1])))
    serving = InviteControl(manager)
    def reserve(generation: int, digest: str, expires: int, deadline: float, commit: Any) -> int:
        assert held and v.helper.inflight and not v.helper._reply.called
        assert len(digest) == 64 and deadline == v.helper.lifecycle_operation.budget.deadline
        assert json.loads(v.helper.audit.path.read_text().splitlines()[-1])['stage'] == 'intent'
        commit()
        return serving.reserve_invite(generation, digest, expires, deadline=deadline)
    control.reserve_invite = Mock(side_effect=reserve)
    monkeypatch.setattr(b, 'ControlClient', lambda: control)
    v.helper.clock = time.monotonic
    v.message.get_member = lambda: 'IssueInvite'
    v.reader.current_generation.side_effect = lambda _: child.capture(1000)
    def issue(expected: str, deadline: float, commit: Any) -> dict:
        assert not v.helper._reply.called, 'IssueInvite must not send accepted'
        assert deadline == v.helper.lifecycle_operation.budget.deadline
        assert 0 < deadline - time.monotonic() <= 120
        return child.dispatch('IssueInvite', 1000, 'issue', expected,
                              deadline=deadline, before_commit=commit)
    v.reader.issue_invite = Mock(side_effect=issue)
    v.issuer, v.manager, v.trust, v.control = issuer, manager, trust, control
    v.admin, v.events, v.records, v.child, v.token = admin, events, records, child, token
    v.journal = destination / 'invite-issuer.json'
    v.record = lambda: json.loads(v.journal.read_bytes())
    return v


def test_issue_invite_single_reply_and_secrecy(issue_mount: Any, monkeypatch: pytest.MonkeyPatch,
                                              caplog: Any, capsys: Any) -> None:
    from runtime.remote_access.invite_v2 import decode
    v = issue_mount
    original = v.helper._reply
    def reply(message: Any, frame: dict) -> None:
        assert v.helper.inflight and v.record()['outcome'] == 'issued'
        rows = list(map(json.loads, v.helper.audit.path.read_text().splitlines()))
        assert [row['stage'] for row in rows] == ['intent', 'outcome']
        assert rows[-1]['outcome'] == 'succeeded'
    original.side_effect = reply
    v.admit()
    original.assert_called_once()
    frame = original.call_args.args[1]
    assert set(frame) == {'result', 'invite', 'expires_at'} and frame['result'] == 'complete'
    decoded = decode(frame['invite'], now=int(time.time()))
    assert decoded.expires_at == frame['expires_at']
    assert v.helper.inflight is None and len(v.admin.keys) == 1
    assert v.control.reserve_invite.call_count == 2
    assert v.trust.load().invite_record.secret_digest == hashlib.sha256(decoded.pair_secret.encode()).hexdigest()
    observed = SimpleNamespace(generation=7, operation='b' * 32, release_uid=1000)
    invite, _ = v.helper._invite_status(1000, observed)
    assert invite.state.value == 'issued' and invite.deadline_utc_seconds == frame['expires_at']
    v.helper.status_lock, v.helper.status_worker = threading.Lock(), None
    observed.phase = 'ready'
    v.helper.generations = SimpleNamespace(current_generation=lambda _: observed)
    v.helper.control.status = lambda: SimpleNamespace(owned=True, release_uid=1000, ready=True)
    monkeypatch.setattr(v.h.threading, 'Thread', lambda target, args=(), **kw: SimpleNamespace(start=lambda: target(*args)))
    monkeypatch.setattr(v.h.threading, 'Timer', lambda *args: SimpleNamespace(start=lambda: None, cancel=lambda: None))
    v.h.Gio.DBusSendMessageFlags = SimpleNamespace(NONE=0)
    status_reply = SimpleNamespace(set_body=lambda body: setattr(status_reply, 'body', body))
    v.message.new_method_reply = lambda: status_reply
    v.helper.connection.send_message = Mock()
    v.helper._status_v2(v.message, ':1.42')
    status_text = v.helper.connection.send_message.call_args.args[0].body.unpack()[0]
    assert frame['invite'] not in status_text
    status = json.loads(status_text)
    assert status['invite']['state'] == 'issued' and status['invite']['deadline_utc_seconds'] == frame['expires_at']
    assert frame['invite'] not in repr(v.helper.lifecycle_result) + repr(v.helper.invite_observation)
    assert frame['invite'] not in v.journal.read_text() + v.helper.audit.path.read_text() + caplog.text
    assert frame['invite'] not in ''.join(capsys.readouterr())
    assert 'IssueInvite' not in v.h.METHODS and 'IssueInvite' not in v.h.INTROSPECTION
    v.helper._dispatch(v.message)
    v.helper._error.assert_called_with(v.message, 'unknown-method')
    call = v.helper.authority.call.call_args.args[1].unpack()
    assert call[0][1]['name'].unpack() == ':1.42'
    assert call[1] == 'ai.happyranch.remote-access.issue-invite' and call[3] == 1


@pytest.mark.parametrize('change', ['uid', 'owner', 'denied', 'deadline', 'generation', 'busy', 'input', 'fd', 'sender', 'capture_uid'])
def test_issue_invite_admission_refuses_before_effect(issue_mount: Any, monkeypatch: pytest.MonkeyPatch,
                                                     change: str) -> None:
    v = issue_mount
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
        if change == 'generation': v.token['operation'] = 'e' * 32
        if change == 'deadline': v.helper.clock = lambda: capture.deadline
        v.helper._authorized(v.helper.authority, Variant('', ((change != 'denied', False, {}),)), capture)
        assert v.helper._error.called and not v.helper._reply.called and v.helper.inflight is None
    assert not v.journal.exists() and not v.events and not v.reader.issue_invite.called
    assert not v.helper.audit.path.exists()


@pytest.mark.parametrize('case', ['unavailable', 'connector_uid', 'lost_reserve', 'minter_refused', 'minter_uncertain',
                                  'late', 'audit_late', 'expiry_final_read', 'deadline_final_read',
                                  'audit_intent', 'generation_drift', 'disconnect', 'reply_loss'])
def test_issue_invite_refusal_uncertainty_and_deadline(issue_mount: Any, case: str,
                                                     monkeypatch: pytest.MonkeyPatch,
                                                     caplog: Any, capsys: Any) -> None:
    from runtime.remote_access.home_minter import AdminRefused
    v = issue_mount
    if case == 'unavailable': v.control.status.side_effect = OSError('synthetic connector down')
    if case == 'connector_uid':
        v.control.status.side_effect = lambda: SimpleNamespace(owned=True, release_uid=1001, generation=0)
    if case in ('minter_refused', 'minter_uncertain'):
        v.admin.create_error = AdminRefused() if case == 'minter_refused' else TimeoutError('synthetic key loss')
    if case == 'lost_reserve':
        original = v.control.reserve_invite.side_effect
        def reserve(*args: Any) -> int:
            result = original(*args)
            if v.control.reserve_invite.call_count == 2:
                raise OSError('synthetic lost reserve reply')
            return result
        v.control.reserve_invite.side_effect = reserve
    if case in ('generation_drift', 'disconnect'):
        original = v.control.reserve_invite.side_effect
        def drift(*args: Any) -> int:
            if case == 'generation_drift': v.token['operation'] = 'e' * 32
            else: v.helper._cancel('disconnected')
            return original(*args)
        v.control.reserve_invite.side_effect = drift
    if case == 'late':
        original = v.reader.issue_invite.side_effect
        def late(*args: Any) -> dict:
            frame = original(*args)
            v.helper.clock = v.helper.lifecycle_operation.budget.clock = lambda: args[1]
            return frame
        v.reader.issue_invite.side_effect = late
    if case == 'audit_late':
        original = v.helper.audit.outcome
        def audit_late(*args: Any) -> None:
            original(*args)
            deadline = v.helper.lifecycle_operation.budget.deadline
            v.helper.clock = v.helper.lifecycle_operation.budget.clock = lambda: deadline + 1
        v.helper.audit.outcome = audit_late
    final_read = case in ('expiry_final_read', 'deadline_final_read')
    settled = {}
    if final_read:
        from datetime import datetime, timedelta, timezone
        if case == 'expiry_final_read':
            v.admin.created['expiration'] = datetime.now(timezone.utc) + timedelta(seconds=2.5)
        original = v.reader.issue_invite.side_effect
        def issue_then_slow_read(*args: Any) -> dict:
            frame = original(*args)
            settled.update(frame=frame, journal=v.journal.read_bytes(), reads=0)
            generation = v.reader.current_generation.side_effect
            def slow_generation(*values: Any) -> str:
                settled['reads'] += 1
                if settled['reads'] == 2:
                    assert v.helper.inflight is not None
                    rows = list(map(json.loads, v.helper.audit.path.read_text().splitlines()))
                    assert [row['stage'] for row in rows] == ['intent', 'outcome']
                    assert rows[1]['outcome'] == 'succeeded'
                    if case == 'expiry_final_read':
                        time.sleep(max(0.0, frame['expires_at'] + .03 - time.time()))
                        assert frame['expires_at'] <= time.time()
                        assert v.helper.lifecycle_operation.budget.remaining() > 0
                    else:
                        deadline = v.helper.lifecycle_operation.budget.deadline
                        v.helper.lifecycle_operation.budget.clock = lambda: deadline
                    settled['final_read_completed'] = True
                return generation(*values)
            v.reader.current_generation.side_effect = slow_generation
            return frame
        v.reader.issue_invite.side_effect = issue_then_slow_read
        def uncertain_reply(message: Any, category: str) -> None:
            assert message is v.message and category == 'uncertain' and v.helper.inflight is not None
            assert v.journal.read_bytes() == settled['journal']
            rows = list(map(json.loads, v.helper.audit.path.read_text().splitlines()))
            assert [row['stage'] for row in rows] == ['intent', 'outcome', 'uncertain']
        v.helper._error.side_effect = uncertain_reply
    if case == 'audit_intent': v.helper.audit.intent = Mock(side_effect=OSError('synthetic audit down'))
    if case == 'reply_loss': v.helper._reply.side_effect = OSError('synthetic lost method reply')
    v.admit()
    refused = case in ('unavailable', 'connector_uid', 'minter_refused', 'audit_intent')
    assert v.helper.lifecycle_result['outcome'] == ('refused' if refused else 'uncertain')
    if case == 'reply_loss':
        assert v.helper._reply.call_count == 1 and not v.helper._error.called
    else:
        assert not v.helper._reply.called and v.helper._error.call_count == 1
        assert v.helper._error.call_args.args[1] == ('unavailable' if refused else 'uncertain')
    assert v.helper.inflight is None
    creates = [event for event in v.events if event[0] == 'create']
    assert bool(creates) == (final_read or case in ('lost_reserve', 'minter_refused', 'minter_uncertain', 'late', 'audit_late', 'reply_loss'))
    if case in ('unavailable', 'connector_uid', 'audit_intent'): assert not v.journal.exists()
    if case in ('lost_reserve', 'minter_uncertain', 'generation_drift', 'disconnect'):
        assert v.record()['outcome'] == 'uncertain'
    if final_read or case in ('late', 'audit_late', 'reply_loss'):
        assert v.record()['outcome'] == 'issued'  # Consumed; NEVER fetch/re-deliver.
        assert v.helper.lifecycle_result['invite'] == 'uncertain'
    if final_read or case == 'audit_late':
        rows = list(map(json.loads, v.helper.audit.path.read_text().splitlines()))
        assert [row['stage'] for row in rows] == ['intent', 'outcome', 'uncertain']
        assert rows[1]['outcome'] == 'succeeded'
    if final_read:
        assert settled['reads'] == 2 and settled['final_read_completed']
        assert v.journal.read_bytes() == settled['journal']
        if case == 'expiry_final_read':
            assert settled['frame']['expires_at'] <= time.time()
            assert v.helper.lifecycle_operation.budget.remaining() > 0
        assert len(v.admin.keys) == 1 and v.control.reserve_invite.call_count == 2
        observed = SimpleNamespace(generation=7, operation='b' * 32, release_uid=1000, phase='ready')
        v.helper.status_lock, v.helper.status_worker = threading.Lock(), None
        v.helper.generations = SimpleNamespace(current_generation=lambda _: observed)
        v.helper.control.status = lambda: SimpleNamespace(owned=True, release_uid=1000, ready=True)
        monkeypatch.setattr(v.h.threading, 'Thread', lambda target, args=(), **kw: SimpleNamespace(start=lambda: target(*args)))
        monkeypatch.setattr(v.h.threading, 'Timer', lambda *args: SimpleNamespace(start=lambda: None, cancel=lambda: None))
        v.h.Gio.DBusSendMessageFlags = SimpleNamespace(NONE=0)
        status_reply = SimpleNamespace(set_body=lambda body: setattr(status_reply, 'body', body))
        v.message.new_method_reply = lambda: status_reply
        v.helper.connection.send_message = Mock()
        v.helper._status_v2(v.message, ':1.42')
        v.helper.connection.send_message.assert_called_once()
        status_text = status_reply.body.unpack()[0]
        status = json.loads(status_text)
        assert status['invite']['state'] == 'uncertain' and status['invite']['deadline_utc_seconds'] is None
        assert status['cleanup']['state'] == 'uncertain'
        retained = (status_text + repr(v.helper.lifecycle_result) + repr(v.helper.invite_observation)
                    + v.journal.read_text() + v.helper.audit.path.read_text()
                    + repr(v.helper._error.call_args) + caplog.text + ''.join(capsys.readouterr()))
        assert settled['frame']['invite'] not in retained


def test_issue_invite_rate_limit_no_second_delivery(issue_mount: Any) -> None:
    v = issue_mount
    v.admit()
    assert v.helper._reply.call_count == 1
    initial = v.journal.read_bytes()
    v.helper._reply.reset_mock()
    v.admit()
    assert v.helper.lifecycle_result['outcome'] == 'refused' and not v.helper._reply.called
    assert len(v.admin.keys) == 1 and v.control.reserve_invite.call_count == 2
    assert v.journal.read_bytes() == initial and v.helper.inflight is None


@pytest.mark.parametrize('case', ['valid_large', 'repeat_ready', 'oversize', 'frame_at_limit', 'frame_limit', 'malformed', 'duplicate', 'extra', 'prefix', 'alphabet',
                                  'too_long', 'bool_expiry', 'expired', 'future_expiry'])
def test_issue_invite_real_child_frame_is_bounded_and_validated(issue_mount: Any, monkeypatch: pytest.MonkeyPatch,
                                                               case: str) -> None:
    from runtime.remote_access import helper_generation as generation
    v = issue_mount
    frame = dict(result='complete', invite='hrinvite2:' + 'A' * 5490, expires_at=int(time.time()) + 240)
    if case == 'extra': frame['unexpected'] = True
    if case == 'prefix': frame['invite'] = 'hrinvite2:hrinvite2:AA'
    if case == 'alphabet': frame['invite'] = 'hrinvite2:AA='
    if case == 'too_long': frame['invite'] += 'A'
    if case == 'bool_expiry': frame['expires_at'] = True
    if case == 'expired': frame['expires_at'] = int(time.time())
    if case == 'future_expiry': frame['expires_at'] = int(time.time()) + 301
    raw = json.dumps(frame, separators=(',', ':')) + '\n'
    if case in ('frame_at_limit', 'frame_limit'):
        limit = 6144 if case == 'frame_at_limit' else 6145
        raw = raw[:-1] + ' ' * (limit - len(raw)) + '\n'
    if case == 'duplicate': raw = raw.replace('"result":', '"result":"complete","result":')
    if case == 'malformed': raw = '{synthetic malformed}\n'
    if case == 'oversize': raw = json.dumps({'result': 'complete', 'invite': 'A' * 6200}) + '\n'
    source = Path(__file__).resolve().parents[1] / 'runtime/remote_access/helper_invite.py'
    confirmations = 270 if case == 'repeat_ready' else 1
    script = ('import sys,json,runpy; h=runpy.run_path(sys.argv[1]); '
              'r=json.loads(sys.stdin.buffer.readline()); '
              f'[h["confirm"](r["deadline"]) for _ in range({confirmations})]; '
              'sys.stdout.write(sys.argv[2]); sys.stdout.flush()')
    monkeypatch.setattr(generation, 'LIFECYCLE_COMMAND', (sys.executable, '-I', '-c', script, str(source), raw))
    client = generation.HomeLifecycleClient('IssueInvite', 1000)
    v.reader.issue_invite.side_effect = client.issue_invite
    v.admit()
    if case in ('valid_large', 'repeat_ready', 'frame_at_limit'):
        v.helper._reply.assert_called_once_with(v.message, frame)
    else:
        assert not v.helper._reply.called
        v.helper._error.assert_called_once_with(v.message, 'uncertain')
        assert v.helper.lifecycle_result['outcome'] == 'uncertain'
    assert v.helper.inflight is None


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


@pytest.mark.parametrize('method', ['Enable', 'ReplaceFailed', 'Disable', 'CancelInvite', 'RecoverInvite'])
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


def test_issue_error_uses_closed_category_without_secret(issue_mount: Any, caplog: Any) -> None:
    v = issue_mount
    v.h.Gio.DBusSendMessageFlags = SimpleNamespace(NONE=0)
    v.message.new_method_error_literal = Mock(return_value=object())
    v.helper.connection.send_message = Mock()
    v.h.RemoteAccessHelper._error(v.helper, v.message, 'uncertain')
    v.message.new_method_error_literal.assert_called_once_with(v.h.INTERFACE + '.Error.uncertain', 'uncertain')
    v.h.RemoteAccessHelper._error(v.helper, v.message, 'hrinvite2:SYNTHETIC_PRIVATE_VALUE')
    v.message.new_method_error_literal.assert_called_with(v.h.INTERFACE + '.Error.unavailable', 'unavailable')
    assert 'hrinvite2:' not in caplog.text

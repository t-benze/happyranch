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
import ssl
import hashlib
import io
import subprocess
import xml.etree.ElementTree as ET
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
from scripts.test_home_activation_backend import composition, corrupt_composition  # noqa: F401
from scripts.test_installer_token_custody import staged_inventory

REAL_THREAD = threading.Thread

# Independent literal expectations: never derived from a production allowlist.
PREPARATION_TOKENS = ('deb-layout-required', 'issuer-state-uncertain', 'account-shape',
    'account-receipt-uncertain', 'foreign-unit', 'foreign-custody', 'publication-uncertain',
    'address-changed', 'account-create-uncertain', 'cleanup-uncertain', 'materializer-uncertain',
    'custody-uncertain', 'inventory', 'no_address', 'multiple_addresses', 'dial_ip', 'dial-ip',
    'occupied-port', 'port-check-uncertain')


@pytest.mark.parametrize('category', PREPARATION_TOKENS)
def test_enable_preparation_child_round_trip(
        composition: Any, monkeypatch: pytest.MonkeyPatch, category: str) -> None:
    from runtime.remote_access import helper_generation as generation
    from runtime.remote_access.headscale_service import HeadscaleServiceError
    v = composition
    lifecycle = backend.HelperLifecycleBackend(v.io)
    expected = lifecycle.capture('Enable', os.getuid())
    publication = Mock()
    monkeypatch.setattr(v.io, 'prepare_headscale', Mock(side_effect=HeadscaleServiceError(category)))
    monkeypatch.setattr(v.io, 'publish_home_units', publication)
    monkeypatch.setattr(backend, 'ManagedInstaller', lambda **kw: v.io)
    frames = []
    def child(command: tuple, **kwargs: Any) -> Any:
        output, errors = io.StringIO(), io.StringIO()
        with monkeypatch.context() as patch:
            patch.setattr(sys, 'argv', ['home_activation_backend.py'])
            patch.setattr(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(kwargs['input'])))
            patch.setattr(sys, 'stdout', output)
            patch.setattr(sys, 'stderr', errors)
            code = backend.helper_main()
        frames.append((code, output.getvalue(), errors.getvalue()))
        return SimpleNamespace(returncode=code, stdout=output.getvalue().encode())
    monkeypatch.setattr(generation.subprocess, 'run', child)
    with pytest.raises(Exception) as failure:
        generation.HomeLifecycleClient('Enable', os.getuid()).effect('enable', expected, 30)
    assert frames == [(1, json.dumps({'preparationFailure': category}, separators=(',', ':')) + '\n', '')]
    assert type(failure.value).__name__ == 'PreparationFailed'
    assert failure.value.category == category
    assert not publication.called
    # Provisioning preceded preparation: a diagnostic never proves no effect.
    assert v.path('/etc/happyranch/provision.json').exists()


@pytest.mark.parametrize('case', ['message', 'duck', 'cause', 'provision', 'publication',
    'unknown', 'policy_path', 'readiness-timeout', 'partial-cleanup-uncertain',
    'issuer-cleanup-uncertain', 'issuer-partial-cleanup-uncertain', 'bad_type', 'prefix', 'suffix',
    'bad_none', 'bad_int', 'bad_bool', 'bad_dict', 'residue', 'owner', 'provenance',
    'custody', 'quiescence', 'timeout', 'inventory-extra', 'xinventory', 'Inventory', 'inventory\n'])
def test_enable_preparation_provenance_rejected(
        composition: Any, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    from runtime.remote_access.headscale_service import HeadscaleServiceError
    v = composition
    lifecycle = backend.HelperLifecycleBackend(v.io)
    expected = lifecycle.capture('Enable', os.getuid())
    error = HeadscaleServiceError(case)
    if case == 'message':
        error = RuntimeError('inventory SYNTHETIC_PRIVATE_CANARY /synthetic/private-canary 192.0.2.123')
    elif case == 'duck':
        error = RuntimeError('SYNTHETIC_PRIVATE_CANARY')
        error.category = 'inventory'
    elif case == 'cause':
        error = RuntimeError('SYNTHETIC_PRIVATE_CANARY')
        error.__cause__ = HeadscaleServiceError('inventory')
    elif case == 'bad_type':
        error.category = ['inventory']
    elif case in ('bad_none', 'bad_int', 'bad_bool', 'bad_dict'):
        error.category = {'bad_none': None, 'bad_int': 7, 'bad_bool': True, 'bad_dict': {}}[case]
    elif case in ('prefix', 'suffix'):
        error.category = ' inventory' if case == 'prefix' else 'inventory '
    stage = {'provision': 'provision', 'publication': 'publish_home_units'}.get(case, 'prepare_headscale')
    if stage != 'prepare_headscale':
        error = HeadscaleServiceError('inventory')
    monkeypatch.setattr(v.io, stage, Mock(side_effect=error))
    monkeypatch.setattr(backend, 'ManagedInstaller', lambda **kw: v.io)
    request = dict(method='Enable', uid=os.getuid(), step='enable', expected=expected,
                   deadline=time.monotonic() + 30)
    output, errors = io.StringIO(), io.StringIO()
    with monkeypatch.context() as patch:
        patch.setattr(sys, 'argv', ['home_activation_backend.py'])
        patch.setattr(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode())))
        patch.setattr(sys, 'stdout', output)
        patch.setattr(sys, 'stderr', errors)
        assert backend.helper_main() == 1
    assert output.getvalue() == errors.getvalue() == ''


@pytest.mark.parametrize('raw,code,method,step', [
    (b'{"preparationFailure":"inventory"', 1, 'Enable', 'enable'),
    (b'{"preparationFailure":"inventory"}' + b' ' * 225, 1, 'Enable', 'enable'),
    (b'{"preparationFailure":"inventory","preparationFailure":"inventory"}', 1, 'Enable', 'enable'),
    (b'{"preparationFailure":{"inventory":1}}', 1, 'Enable', 'enable'),
    (b'{"preparationFailure":"inventory","message":"SYNTHETIC_PRIVATE_CANARY"}', 1, 'Enable', 'enable'),
    (b'{"preparationFailure":"inventory"}{}', 1, 'Enable', 'enable'),
    (b'{"preparationFailure":"inventory"}', 0, 'Enable', 'enable'),
    (b'{"preparationFailure":"inventory"}', 2, 'Enable', 'enable'),
    (b'{"preparationFailure":"inventory"}', 1, 'Enable', 'capture'),
    (b'{"preparationFailure":"inventory"}', 1, 'Disable', 'stop'),
])
def test_enable_preparation_private_frame_rejected(
        monkeypatch: pytest.MonkeyPatch, raw: bytes, code: int, method: str, step: str) -> None:
    from runtime.remote_access import helper_generation as generation
    monkeypatch.setattr(generation.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=code, stdout=raw))
    try:
        value = generation.HomeLifecycleClient(method, 1000)._call(step, 'snapshot', 30)
    except Exception as error:
        assert type(error) is generation.GenerationUnavailable
    else:
        # Exit zero remains a success object, never a typed preparation failure.
        assert code == 0 and value == {'preparationFailure': 'inventory'}


@pytest.mark.parametrize('outcome', ['typed', 'completed', 'generic', 'audit', 'settlement', 'uid', 'owner',
    'late', 'auth_deadline', 'disconnect', 'absent_to_new', 'success_audit', 'success_settlement', 'intent', 'flush_error'])
def test_enable_completion_unicast_envelope(
        mount: Any, monkeypatch: pytest.MonkeyPatch, outcome: str) -> None:
    m = mount
    clock = [100.0]
    m.helper.clock = lambda: clock[0]
    m.message.get_member = lambda: 'Enable'
    m.message.get_serial = lambda: 17
    sent, order = [], []
    m.helper.connection.get_unique_name = lambda: ':1.100'
    def signal(destination: str, path: str, interface: str, member: str, body: Any) -> None:
        assert m.helper.inflight is not None
        sent.append((destination, path, interface, member, json.loads(body.unpack()[0])))
        order.append('signal')
        # Flush uses actual bounded worker loop; only native IO is simulated.
    m.helper.connection.emit_signal = signal
    context = SimpleNamespace(push_thread_default=lambda: None, pop_thread_default=lambda: None)
    loop = SimpleNamespace(run=lambda: None, quit=lambda: None)
    m.h.GLib.MainContext = SimpleNamespace(new=lambda: context)
    m.h.GLib.MainLoop = SimpleNamespace(new=lambda *a: loop)
    m.h.GLib.timeout_source_new = lambda *a: SimpleNamespace(set_callback=lambda *a: None,
        attach=lambda *a: None, destroy=lambda: None)
    m.h.Gio.Cancellable = lambda: SimpleNamespace(cancel=lambda: None)
    def flushed(result: Any) -> None:
        order.append('flush')
        if outcome == 'flush_error':
            raise OSError('synthetic flush loss')
    m.helper.connection.flush_finish = flushed
    m.helper.connection.flush = lambda cancel, callback, data: callback(m.helper.connection, None, data)
    original_release = m.helper.release
    def release(capture: Any) -> None:
        order.append('release')
        original_release(capture)
    m.helper.release = release
    def effect(*a: Any, **kw: Any) -> dict:
        if outcome == 'uid':
            m.helper.connection.call_sync.return_value = Variant('', (1001,))
        if outcome == 'owner':
            monkeypatch.setattr(m.h, 'bootstrap_owner', lambda uid: SimpleNamespace(uid=1001))
        if outcome == 'disconnect':
            m.helper.cursors = {}
            m.helper._owner_changed(None, None, None, None, None, Variant('', (':1.42', ':1.42', '')), None)
            assert m.helper.inflight is not None
        if outcome == 'absent_to_new':
            m.reader.current_generation.side_effect = lambda _: 'new-own-generation-and-operation'
        if outcome in ('late', 'auth_deadline'):
            clock[0] = 580 if outcome == 'late' else 350
        if outcome in ('settlement', 'success_settlement'):
            m.helper.lifecycle_operation.complete = Mock(side_effect=RuntimeError('settlement'))
        if outcome in ('typed', 'audit', 'settlement', 'auth_deadline', 'flush_error'):
            raise m.h._helper_generation.PreparationFailed('inventory')
        if outcome == 'generic':
            raise RuntimeError('inventory SYNTHETIC_PRIVATE_CANARY')
        return {'outcome': 'ready', 'residue': []}
    m.reader.effect.side_effect = effect
    if outcome == 'audit':
        m.helper.audit.uncertain = Mock(side_effect=OSError('SYNTHETIC_PRIVATE_CANARY'))
    if outcome == 'success_audit':
        m.helper.audit.outcome = Mock(side_effect=OSError('synthetic audit loss'))
    if outcome == 'intent':
        m.helper.audit.intent = Mock(side_effect=OSError('synthetic intent loss'))
    m.admit()
    m.helper._reply.assert_called_once_with(m.message, 'accepted')
    assert not m.helper._error.called
    assert m.reader.effect.call_count == (0 if outcome == 'intent' else 1)
    if outcome in ('uid', 'owner', 'late', 'disconnect'):
        assert sent == []
    else:
        expected = dict(method='Enable', correlation=dict(serial=17, owner=':1.100'),
                        outcome='completed' if outcome in ('completed', 'absent_to_new') else 'failed')
        if outcome in ('typed', 'audit', 'settlement', 'auth_deadline', 'flush_error'):
            expected['category'] = 'inventory'
        assert sent == [(':1.42', m.h.OBJECT_PATH, m.h.INTERFACE, 'LifecycleCompleted', expected)]
        assert order.index('signal') < order.index('flush') < order.index('release')
    assert m.helper.inflight is None
    assert m.helper.lifecycle_result['outcome'] == ('ready' if outcome in ('completed', 'uid', 'owner', 'disconnect', 'absent_to_new') else 'uncertain')
    if outcome == 'audit':
        assert json.loads(m.helper.audit.path.read_text().splitlines()[-1])['stage'] == 'intent'


@pytest.mark.parametrize('case', ['timeout', 'empty', 'unexpected', 'late'])
def test_enable_child_failure_remains_unknown(monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    from runtime.remote_access import helper_generation as generation
    clock = [100.0]
    monkeypatch.setattr(generation.time, 'monotonic', lambda: clock[0])
    def child(*a: Any, **kw: Any) -> Any:
        if case == 'timeout':
            raise subprocess.TimeoutExpired('synthetic', 30)
        if case == 'late':
            clock[0] = 130
        return SimpleNamespace(returncode=2 if case == 'unexpected' else 1,
            stdout=b'{"preparationFailure":"inventory"}' if case == 'late' else b'')
    monkeypatch.setattr(generation.subprocess, 'run', child)
    with pytest.raises(generation.GenerationUnavailable) as failure:
        generation.HomeLifecycleClient('Enable', 1000).effect('enable', 'snapshot', 30)
    assert failure.value.category == ('timeout' if case in ('timeout', 'late') else 'unknown')


def test_enable_worker_start_failure_has_no_second_reply(mount: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    m = mount
    m.message.get_member = lambda: 'Enable'
    monkeypatch.setattr(m.h.threading, 'Thread', lambda *a, **kw:
        SimpleNamespace(start=Mock(side_effect=RuntimeError('SYNTHETIC_PRIVATE_CANARY'))))
    m.admit()
    m.helper._reply.assert_called_once_with(m.message, 'accepted')
    assert not m.helper._error.called and not m.reader.effect.called
    assert m.helper.inflight is None and m.helper.exit_requested


def test_enable_preparation_production_trace(
        composition: Any, mount: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from runtime.remote_access import helper_generation as generation
    v, m = composition, mount
    m.message.get_member = lambda: 'Enable'
    m.message.get_serial = lambda: 17
    m.helper.connection.get_unique_name = lambda: ':1.100'
    m.helper.connection.call_sync.return_value = Variant('', (os.getuid(),))
    monkeypatch.setattr(m.h, 'bootstrap_owner', lambda uid: SimpleNamespace(uid=uid))
    # Same exception identity across the test-side isolated module loader.
    monkeypatch.setattr(m.h._helper_generation, 'PreparationFailed', generation.PreparationFailed)
    lifecycle = backend.HelperLifecycleBackend(v.io)
    m.reader.current_generation.side_effect = lambda _: lifecycle.capture('Enable', os.getuid())
    monkeypatch.setattr(backend, 'ManagedInstaller', lambda **kw: v.io)
    children, signals = [], []
    def child(command: tuple, **kwargs: Any) -> Any:
        out, err = io.StringIO(), io.StringIO()
        with monkeypatch.context() as patch:
            patch.setattr(sys, 'argv', ['home_activation_backend.py'])
            patch.setattr(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(kwargs['input'])))
            patch.setattr(sys, 'stdout', out)
            patch.setattr(sys, 'stderr', err)
            code = backend.helper_main()
        children.append(dict(exit=code, stdout=out.getvalue(), stderr=err.getvalue()))
        return SimpleNamespace(returncode=code, stdout=out.getvalue().encode())
    monkeypatch.setattr(generation.subprocess, 'run', child)
    def effect(step: str, expected: str, remaining: float, **kw: Any) -> dict:
        # Existing bad kernel input, not a changed collector or fake exception.
        v.head.kernel_case = 'truncated'
        return generation.HomeLifecycleClient('Enable', os.getuid()).effect(step, expected, remaining)
    m.reader.effect.side_effect = effect
    def signal(destination: str, path: str, interface: str, member: str, body: Any) -> None:
        assert m.helper.inflight is not None
        signals.append(dict(destination=destination, sender=':1.100', path=path, interface=interface,
                            member=member, signature='s', raw=body.unpack()[0]))
    m.helper.connection.emit_signal = signal
    context = SimpleNamespace(push_thread_default=lambda: None, pop_thread_default=lambda: None)
    loop = SimpleNamespace(run=lambda: None, quit=lambda: None)
    m.h.GLib.MainContext = SimpleNamespace(new=lambda: context)
    m.h.GLib.MainLoop = SimpleNamespace(new=lambda *a: loop)
    m.h.GLib.timeout_source_new = lambda *a: SimpleNamespace(set_callback=lambda *a: None,
        attach=lambda *a: None, destroy=lambda: None)
    m.h.Gio.Cancellable = lambda: SimpleNamespace(cancel=lambda: None)
    m.helper.connection.flush_finish = lambda result: None
    m.helper.connection.flush = lambda cancel, callback, data: callback(m.helper.connection, None, data)
    m.admit()
    m.helper._reply.assert_called_once_with(m.message, 'accepted')
    assert m.reader.effect.call_count == 1 and len(children) == len(signals) == 1
    assert children == [dict(exit=1, stdout='{"preparationFailure":"inventory"}\n', stderr='')]
    assert json.loads(signals[0]['raw']) == dict(method='Enable', outcome='failed', category='inventory',
                                                correlation=dict(serial=17, owner=':1.100'))
    assert v.path('/etc/happyranch/provision.json').exists()
    assert m.helper.lifecycle_result == dict(outcome='uncertain', residue=[])
    target = os.environ.get('ENABLE_PREPARATION_TRACE')
    if target:
        sources = [backend.__file__, generation.__file__, m.h.__file__]
        Path(target).write_text(json.dumps(dict(child=children[0], signal=signals[0],
            sourceDigests={Path(p).name: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in sources}),
            sort_keys=True) + '\n')


@pytest.mark.parametrize('method', ['Status', 'StatusV2'])
@pytest.mark.parametrize('leftover', ['empty', 'expired', 'health.json', '.health-7', 'unknown', 'mode',
    'symlink', 'hardlink', 'oversize', 'bound', 'state', 'observer', 'verifier', 'active'])
def test_thr327_replacement_runtime_status(
        renewal_fixture: Any, mount: Any, monkeypatch: pytest.MonkeyPatch,
        method: str, leftover: str) -> None:
    """The actual v2 replacement's closed output must be usable by Enable/Status."""
    from scripts.test_installer_token_custody import prepared_replacement, module
    f, m = renewal_fixture, mount
    # Match installed package custody, as in the existing renewal_setup fixture;
    # the source checkout itself may be group-writable.
    source = f.inputs / 'route_policy.json'
    source.write_bytes(Path(module.__file__).with_name('route_policy.json').read_bytes())
    source.chmod(0o644)
    monkeypatch.setattr(module, '__file__', str(f.inputs / 'installer.py'))
    f.installer.layout = 'deb'
    f.installer.units = module.managed_units('deb')
    lookup_user, lookup_group = f.installer._lookup_user, f.installer._lookup_group
    def absent_headscale(name: str, lookup: Any) -> Any:
        if name == 'happyranch-headscale':
            raise KeyError(name)
        return lookup(name)
    f.installer._lookup_user = lambda name: absent_headscale(name, lookup_user)
    f.installer._lookup_group = lambda name: absent_headscale(name, lookup_group)
    payload = f.root / 'opt/happyranch-desktop'
    for name in ('bin/happyranch-tsnet-sidecar', 'python/bin/python3.12'):
        path = payload / name
        path.parent.mkdir(parents=True, mode=0o755, exist_ok=True)
        path.write_bytes(b'synthetic packaged executable')
        path.chmod(0o755)
        for ancestor in path.parents:
            if ancestor == f.root:
                break
            ancestor.chmod(0o755)
    (payload / 'python/bin/python3').symlink_to('python3.12')
    manager, parent, _ = prepared_replacement(f)
    (parent / 'install-transaction.json').unlink()
    (parent / 'provision.json').unlink()  # Real unrecorded manual install.
    release = (parent / 'release-daemon.json').read_bytes()
    runtime = f.root / 'run/happyranch-tsnet-sidecar'
    candidate = f.installer.replacement_inventory()
    with pytest.raises(module.InstallerError, match='replacement paused after stop intent'):
        f.installer.begin_replacement(expected_release_uid=os.getuid(),
            expected_generation=candidate.inventory_generation)
    journal = parent / 'install-transaction.json'
    for _ in range(8):
        if not journal.exists():
            break
        try:
            f.installer.replace_failed()
        except module.InstallerError as error:
            assert 'replacement paused after' in str(error)
    assert not journal.exists()
    assert (parent / 'release-daemon.json').read_bytes() == release
    assert json.loads((parent / 'provision.json').read_bytes())['version'] == 1
    if leftover not in ('empty', 'expired'):
        name = '.health-7' if leftover == '.health-7' else 'unknown' if leftover == 'unknown' else 'health.json'
        target = runtime / name
        target.write_bytes(b'{"ready":true,"stale":true}')
        target.chmod(0o600 if name == '.health-7' else 0o644)
        if leftover == 'mode': target.chmod(0o666)
        if leftover == 'symlink':
            target.unlink()
            target.symlink_to(parent / 'policy.json')
        if leftover == 'hardlink': os.link(target, runtime / '.health-8')
        if leftover == 'oversize': target.write_bytes(b'x' * (module._RUNTIME_DIAGNOSTIC_BYTES + 1))
        if leftover == 'bound':
            for index in range(module._RUNTIME_DIAGNOSTIC_LIMIT):
                path = runtime / f'.health-{index}'
                path.write_bytes(b'')
                path.chmod(0o600)
        if leftover == 'state':
            path = f.root / 'var/lib/happyranch-tsnet-sidecar/tailscaled.state'
            path.write_bytes(b'synthetic private state')
            path.chmod(0o600)
        if leftover in ('observer', 'verifier'):
            import socket
            path = runtime / 'observe.sock' if leftover == 'observer' else f.root / 'run/happyranch-release-verifier/control.sock'
            path.parent.mkdir(mode=0o755, exist_ok=True)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with socket.socket(socket.AF_UNIX) as sock:
                    sock.bind(f'/proc/self/fd/{directory}/{path.name}')
            finally:
                os.close(directory)
            path.chmod(0o600 if leftover == 'observer' else 0o660)
        if leftover == 'active': manager.states[module.UNITS[1]] = 'active'
    if leftover in ('empty', 'expired', 'health.json', '.health-7'):
        assert f.installer.inventory().detail == 'staged-inert'
    if leftover == 'expired':
        from scripts.test_installer_token_custody import expire_staged_policy
        expire_staged_policy(f, monkeypatch)
    state = f.root / 'var/lib/happyranch-remote-access'
    state.mkdir(mode=0o700)
    run = f.installer._run
    f.installer._run = lambda args, **kw: (0, '') if args == ['ps', '-eo', 'ruid=,euid=,suid=,fuid='] else run(args, **kw)
    show = manager.show_properties
    manager.show_properties = lambda name, properties: {key: value for key, value in {
        **show(name, tuple(key for key in properties if key != 'Names')), 'Names': name,
    }.items() if key in properties}
    lifecycle = backend.HelperLifecycleBackend(f.installer)
    failures = []
    def capture(_: float) -> str:
        try:
            return lifecycle.capture('Enable', os.getuid())
        except Exception as error:
            import traceback
            failures.append((str(error), [frame.name for frame in traceback.extract_tb(error.__traceback__)]))
            raise
    m.reader.current_generation.side_effect = capture
    m.helper.connection.call_sync.return_value = Variant('', (os.getuid(),))
    monkeypatch.setattr(m.h, 'bootstrap_owner', lambda uid: SimpleNamespace(uid=uid))
    m.helper.control = m.h.ControlClient(str(f.root / 'absent-connector.sock'))
    m.helper.cursors = {}
    m.helper.generations = SimpleNamespace(current_generation=lambda _: None)
    m.message.get_member = lambda: method
    monkeypatch.setattr(m.h.threading, 'Thread', lambda target, args=(), **kw:
                        SimpleNamespace(start=lambda: target(*args)))
    monkeypatch.setattr(m.h.threading, 'Timer', lambda *args: SimpleNamespace(start=lambda: None, cancel=lambda: None))
    m.h.Gio.DBusSendMessageFlags = SimpleNamespace(NONE=0)
    reply = SimpleNamespace(set_body=lambda body: setattr(reply, 'body', body))
    m.message.new_method_reply = lambda: reply
    m.message.new_method_error_literal = Mock(side_effect=lambda name, code: SimpleNamespace(name=name, code=code))
    m.helper.connection.send_message = Mock()
    m.helper._reply = m.h.RemoteAccessHelper._reply.__get__(m.helper)
    m.helper._error = m.h.RemoteAccessHelper._error.__get__(m.helper)
    before = staged_inventory(f)
    manager.calls.clear()
    m.helper._dispatch(m.message)
    if leftover not in ('empty', 'expired', 'health.json', '.health-7'):
        assert m.message.new_method_error_literal.called
        assert 'host_state_unrecognized' in m.message.new_method_error_literal.call_args.args[0]
        assert staged_inventory(f) == before
        assert all(call[0] == 'show' for call in manager.calls)
        return
    assert not m.message.new_method_error_literal.called, (m.message.new_method_error_literal.call_args, failures)
    result = json.loads(reply.body.unpack()[0])
    assert (result['phase'] if method == 'Status' else result['setup']['state']) == 'off'
    assert json.loads(lifecycle.capture('Enable', os.getuid()))['prestate'] == 'staged-inert'
    assert staged_inventory(f) == before
    assert all(call[0] == 'show' for call in manager.calls)
    assert not m.helper.authority.call.called and not m.reader.effect.called


@pytest.mark.parametrize('case', ['residue', 'policy-drift', 'non-shipped', 'shipped-custody'])
def test_thr327_capture_refusal_transport(composition: Any, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    from runtime.remote_access import helper_generation as generation
    v = composition
    v.io.provision(release_user=v.fixture.user, data_home=v.fixture.home)
    if case == 'residue':
        runtime = v.path('/run/happyranch-tsnet-sidecar')
        runtime.mkdir(mode=0o755)
        (runtime / 'unknown').write_bytes(b'synthetic-private-canary')
    elif case == 'shipped-custody':
        (v.root / 'package/route_policy.json').chmod(0o666)
    else:
        policy_path = v.path('/etc/happyranch/policy.json')
        if case == 'policy-drift':
            policy_path.write_bytes(policy_path.read_bytes() + b' ')
        else:
            policy = json.loads(policy_path.read_bytes())
            policy['artifact']['allow'] = policy['artifact']['allow'][:-1]
            policy_path.write_bytes(json.dumps(policy, sort_keys=True).encode())
            provision_path = v.path('/etc/happyranch/provision.json')
            provision = json.loads(provision_path.read_bytes())
            provision['digests']['policy.json'] = hashlib.sha256(policy_path.read_bytes()).hexdigest()
            provision_path.write_bytes(json.dumps(provision, sort_keys=True).encode())
    before, calls = staged_inventory(v.fixture), list(v.calls)
    writes = []
    monkeypatch.setattr(v.io, '_renew_policy_locked', lambda **kwargs: writes.append('renew'))
    monkeypatch.setattr(backend, 'ManagedInstaller', lambda **kw: v.io)
    frames = []
    def child(command: tuple, **kwargs: Any) -> Any:
        output = io.StringIO()
        with monkeypatch.context() as patch:
            patch.setattr(sys, 'argv', ['home_activation_backend.py'])
            patch.setattr(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(kwargs['input'])))
            patch.setattr(sys, 'stdout', output)
            code = backend.helper_main()
        frames.append(output.getvalue())
        return SimpleNamespace(returncode=code, stdout=frames[-1].encode())
    monkeypatch.setattr(generation.subprocess, 'run', child)
    with pytest.raises(generation.GenerationUnavailable) as failure:
        generation.HomeLifecycleClient('Enable', os.getuid()).current_generation(2)
    category = {'residue': 'residue', 'shipped-custody': 'custody'}.get(case, 'provenance')
    assert getattr(failure.value, 'category', None) == category
    assert json.loads(frames[0]) == {'refusal': category}
    assert staged_inventory(v.fixture) == before and v.calls == calls and writes == []
    assert 'canary' not in ''.join(frames) and str(v.root) not in ''.join(frames)
    for frame in ({'refusal': 'private/path'}, {'refusal': {}}, {'refusal': 'owner', 'private': 'canary'}, {}):
        monkeypatch.setattr(generation.subprocess, 'run', lambda *args, **kw:
            SimpleNamespace(returncode=1, stdout=json.dumps(frame).encode()))
        with pytest.raises(generation.GenerationUnavailable) as failure:
            generation.HomeLifecycleClient('Enable', os.getuid()).current_generation(2)
        assert failure.value.category == 'unknown'


@pytest.mark.parametrize('category', ['residue', 'owner', 'provenance', 'custody', 'inventory',
    'quiescence', 'timeout', 'unknown'])
def test_thr327_status_category_dbus(mount: Any, monkeypatch: pytest.MonkeyPatch, category: str) -> None:
    m = mount
    m.helper.control.status = Mock(side_effect=m.h.Refused('unavailable'))
    m.reader.current_generation.side_effect = m.h._helper_generation.GenerationUnavailable(category)
    m.h.Gio.DBusSendMessageFlags = SimpleNamespace(NONE=0)
    m.message.new_method_error_literal = Mock(return_value=object())
    m.helper.connection.send_message = Mock()
    with pytest.raises(m.h.Refused) as failure:
        m.helper._status_snapshot(1000, time.monotonic() + 2)
    m.h.RemoteAccessHelper._error(m.helper, m.message, failure.value.category)
    m.message.new_method_error_literal.assert_called_once_with(
        m.h.INTERFACE + '.Error.host_state_unrecognized_' + category, 'host-state-unrecognized-' + category)
    assert not m.reader.effect.called and not m.helper.authority.call.called


def test_clean_enable_empty_environment_account_tools(
        composition: Any, mount: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from runtime.remote_access import service_manager

    v, m = composition, mount
    native = v.io._run
    entered, proceed = threading.Event(), threading.Event()
    probes, child_exits, workers = [], [], []
    m.message.get_member = lambda: 'Enable'
    m.helper.connection.call_sync.return_value = Variant('', (os.getuid(),))
    monkeypatch.setattr(m.h, 'bootstrap_owner', lambda uid: SimpleNamespace(uid=uid))
    m.reader.current_generation.side_effect = lambda _: v.lifecycle.capture('Enable', os.getuid())

    def run(argv: list[str], **kwargs: Any) -> tuple[int, str]:
        name = Path(argv[0]).name
        if name in ('useradd', 'userdel', 'groupdel'):
            entered.set()
            assert proceed.wait(2), 'local account-tool probe was not released'
            try:
                # Exercise the shipped executable selection without modifying
                # host accounts; only the existing synthetic NSS is mutated.
                result = subprocess.run([argv[0], '--help'], env={}, cwd='/',
                    capture_output=True, timeout=2, check=False)
                code = result.returncode
            except FileNotFoundError:
                code = 127
            probes.append((argv[0], code))
            if code or argv[1:] == ['--help']:
                return code, ''
        return native([name, *argv[1:]] if name in ('useradd', 'userdel', 'groupdel') else argv, **kwargs)

    def installer(*, layout: str, manager: Any, run: Any) -> Any:
        assert layout == 'deb'
        v.io._run, v.io.manager = run, manager
        return v.io

    monkeypatch.setattr(service_manager, '_default_run', run)
    monkeypatch.setattr(backend, 'ManagedInstaller', installer)

    def effect(step: str, expected: str, remaining: float, **kwargs: Any) -> dict:
        request = dict(method='Enable', uid=os.getuid(), step=step,
            expected=expected, deadline=time.monotonic() + remaining)
        output = io.StringIO()
        with monkeypatch.context() as patch:
            patch.setattr(sys, 'argv', ['home_activation_backend.py'])
            patch.setattr(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode())))
            patch.setattr(sys, 'stdout', output)
            code = backend.helper_main()
        child_exits.append(code)
        if code:
            raise m.h._helper_generation.GenerationUnavailable()
        return json.loads(output.getvalue())

    m.reader.effect.side_effect = effect

    def worker(*, target: Any, args: tuple, **kwargs: Any) -> Any:
        thread = REAL_THREAD(target=target, args=args, **kwargs)
        workers.append(thread)
        return thread

    monkeypatch.setattr(m.h.threading, 'Thread', worker)
    # The composition's observation servers already use their captured real
    # thread factory; only the public helper's lifecycle worker is collected.
    m.admit()
    try:
        assert entered.wait(2), 'accepted worker never reached account preparation'
        capture = m.helper.lifecycle_operation.capture
        assert m.helper.inflight is capture and child_exits == []
        loop = SimpleNamespace(quit=Mock())
        m.helper.last_activity = -100  # Idle time alone cannot retire an effect.
        assert m.helper._idle(loop) is True
        loop.quit.assert_not_called()
        assert m.helper.inflight is capture and m.helper.name_owned
    finally:
        proceed.set()
        for thread in workers:
            thread.join(timeout=4)
    assert all(not thread.is_alive() for thread in workers)
    rows = [json.loads(line) for line in m.helper.audit.path.read_text().splitlines()]
    print(json.dumps(dict(probes=probes, child_exits=child_exits,
        outcome=m.helper.lifecycle_result, receipt_present=v.receipts.current() is not None,
        audit_stages=[row['stage'] for row in rows], slot_released=m.helper.inflight is None), sort_keys=True))
    assert m.helper.lifecycle_result == {'outcome': 'ready', 'residue': []}, 'clean Enable child did not publish READY'
    assert child_exits == [0] and len(probes) == 2 and all(code == 0 for _, code in probes)
    assert v.receipts.current().phase == 'ready'
    assert [row['stage'] for row in rows] == ['intent', 'outcome']
    assert m.helper.inflight is None
    for tool in ('userdel', 'groupdel'):
        assert v.io._run([tool, '--help'], timeout=2) == (0, ''), tool
    print(json.dumps(dict(cleanup_tool_probes=probes[2:]), sort_keys=True))


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


@pytest.mark.parametrize('journal_fails', [False, True])
def test_enable_child_diagnostic_preserves_closed_exit(
        monkeypatch: pytest.MonkeyPatch, capsys: Any, journal_fails: bool) -> None:
    import syslog

    sent = Mock(side_effect=OSError('synthetic-journal-secret') if journal_fails else None)
    monkeypatch.setattr(syslog, 'syslog', sent)
    monkeypatch.setattr(sys, 'argv', ['home_activation_backend.py'])
    request = dict(method='Enable', uid=1000, step='capture', expected=None,
                   deadline=time.monotonic() + 30)
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode())))
    monkeypatch.setattr(os, 'geteuid', lambda: 0)
    monkeypatch.setattr(backend.HomeActivationStore, 'current',
                        Mock(side_effect=OSError('synthetic-secret /private/path :12345')))
    assert backend.helper_main() == 1
    sent.assert_called_once_with(syslog.LOG_WARNING,
        'remote-access-enable-refusal origin=child stage=child.dispatch.capture exception=OSError')
    assert capsys.readouterr() == ('{"refusal": "unknown"}\n', '')


@pytest.mark.parametrize('case', ['clean', 'partial', 'failed', 'pending', 'uid', 'unsafe', 'unknown', 'txn', 'source', 'audit', 'headscale-mode', 'clean-ca', 'clean-unit'])
def test_public_enable_composes_or_refuses_before_auth(
    composition: Any, mount: Any, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    v, m = composition, mount
    if case != 'clean': corrupt_composition(v, case)
    uid = os.getuid() + (case == 'uid')
    m.message.get_member = lambda: 'Enable'
    m.helper.connection.call_sync.return_value = Variant('', (uid,))
    monkeypatch.setattr(m.h, 'bootstrap_owner', lambda uid: SimpleNamespace(uid=uid))
    m.reader.current_generation.side_effect = lambda _: v.lifecycle.capture('Enable', uid)
    def effect(step: str, expected: str, remaining: float, **kw: Any) -> dict:
        assert json.loads(m.helper.audit.path.read_text().splitlines()[-1])['stage'] == 'intent'
        return v.lifecycle.dispatch('Enable', uid, step, expected, deadline=time.monotonic() + remaining)
    m.reader.effect.side_effect = effect
    before, calls = staged_inventory(v.fixture), list(v.calls)
    if case == 'clean': m.admit()
    else: m.helper._dispatch(m.message)
    if case == 'clean':
        assert m.helper.lifecycle_result == {'outcome': 'ready', 'residue': []}
        assert m.helper.authority.call.call_args.args[1].unpack()[1] == 'ai.happyranch.remote-access.enable'
        rows = [json.loads(line) for line in m.helper.audit.path.read_text().splitlines()]
        assert [row['stage'] for row in rows] == ['intent', 'outcome']
        assert m.helper.inflight is None and len(v.mints) == 1
    else:
        assert not m.helper.authority.call.called and not m.reader.effect.called
        assert not m.helper.audit.path.exists() and not m.helper._reply.called
        assert staged_inventory(v.fixture) == before and v.calls == calls and not v.mints


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
def test_worker_settles_after_accepted_enable(
        mount: Any, monkeypatch: pytest.MonkeyPatch, schedule: str) -> None:
    v = mount
    queued, completed = [], []
    v.message.get_member = lambda: 'Enable'

    def effect(*args: Any, **kwargs: Any) -> dict:
        assert json.loads(v.helper.audit.path.read_text().splitlines()[-1])['stage'] == 'intent'
        return {'outcome': 'ready', 'residue': []}

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
    assert v.helper.lifecycle_result == {'outcome': 'ready', 'residue': []}
    assert v.reader.effect.call_count == 1
    rows = [json.loads(line) for line in v.helper.audit.path.read_text().splitlines()]
    assert [row['stage'] for row in rows] == ['intent', 'outcome']
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
    assert bool(audit_reads) is granted
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
        composition: Any, mount: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    v, m = composition, mount
    unit = Path(__file__).resolve().parents[1] / 'packaging/linux/systemd/happyranch-remote-access-helper.service'
    entries = [line.split('=', 1)[1] for line in unit.read_text().splitlines()
               if line.startswith('ReadWritePaths=')]
    def projection() -> tuple[Path, ...]:
        return tuple(v.path(entry.lstrip('-')) for entry in entries
                     if not entry.startswith('-') or v.path(entry[1:]).is_dir())
    writable = projection()
    assert not v.path('/var/lib/happyranch-headscale').exists()
    original_statvfs = m.statvfs
    def statvfs(path: str) -> Any:
        full = v.path(path)
        result = original_statvfs(full)  # Absence is observed, not pre-created.
        return SimpleNamespace(f_flag=result.f_flag if any(full.is_relative_to(p) for p in writable)
                               else os.ST_RDONLY)
    monkeypatch.setattr(m.h.os, 'statvfs', statvfs)
    uid = os.getuid()
    m.helper.connection.call_sync.return_value = Variant('', (uid,))
    monkeypatch.setattr(m.h, 'bootstrap_owner', lambda uid: SimpleNamespace(uid=uid))
    m.reader.current_generation.side_effect = lambda _: v.lifecycle.capture(m.message.get_member(), uid)
    def effect(step: str, expected: str, remaining: float, **options: Any) -> dict:
        return v.lifecycle.dispatch(m.message.get_member(), uid, step, expected,
            deadline=time.monotonic() + remaining, **options)
    m.reader.effect.side_effect = effect
    m.message.get_member = lambda: 'Enable'
    m.admit()
    assert m.helper.lifecycle_result == {'outcome': 'ready', 'residue': []}
    db = v.path('/var/lib/happyranch-headscale/db.sqlite')
    assert db.is_file()
    v.observers.close()
    m.message.get_member = lambda: 'Disable'
    m.helper._error.reset_mock()
    # First the settled Enable instance is sealed; even a racing call refuses.
    m.helper._dispatch(m.message)
    m.helper._error.assert_called_once_with(m.message, 'unavailable')
    m.renew()
    # A hypothetical stale namespace is independently caught before capture.
    calls, reads = m.helper.authority.call.call_count, m.reader.current_generation.call_count
    audit = m.helper.audit.path.read_bytes()
    effects, inventory = m.reader.effect.call_count, staged_inventory(v.fixture)
    m.helper._dispatch(m.message)
    assert m.helper._error.call_args.args == (m.message, 'unavailable')
    assert m.helper.authority.call.call_count == calls and m.reader.current_generation.call_count == reads
    assert m.helper.audit.path.read_bytes() == audit and db.is_file()
    assert m.reader.effect.call_count == effects and not m.helper.control.revoke_all.called
    assert staged_inventory(v.fixture) == inventory
    assert v.receipts.current() is not None and m.helper.inflight is None
    # Model EROFS for unlink/rmdir outside each instance's initial RW projection,
    # as in TASK-17944. The actual B6 cleanup owner executes unchanged.
    writable = projection()
    blocked = []
    unlink, rmdir = os.unlink, os.rmdir
    def remove(original: Any, name: Any, *, dir_fd: Any = None) -> Any:
        parent = Path(os.readlink(f'/proc/self/fd/{dir_fd}')) if dir_fd is not None else Path.cwd()
        full = parent / name
        if full.is_relative_to(v.root) and not any(full.is_relative_to(p) for p in writable):
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
    assert not db.exists() and v.receipts.current() is None


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
    ('Enable', 'enable', 'prepared', 'in_progress'), ('ReplaceFailed', 'replace', 'off', 'off'),
    ('Enable', 'enable', 'ready', 'uncertain')])
def test_lifecycle_public_mount_fresh_auth_and_audit(
        mount: Any, method: str, step: str, outcome: str, setup: str) -> None:
    v = mount
    v.message.get_member = lambda: method
    def effect(actual: str, expected: str, remaining: float) -> dict:
        assert actual == step and 0 < remaining <= 480 and v.helper.inflight
        assert json.loads(v.helper.audit.path.read_text().splitlines()[-1])['event'] == 'activation'
        return {'outcome': outcome, 'residue': []}
    v.reader.effect.side_effect = effect
    if setup == 'uncertain':
        original = v.helper.audit.outcome
        def late_audit(*args: Any) -> None:
            original(*args)
            deadline = v.helper.lifecycle_operation.budget.deadline
            v.helper.clock = v.helper.lifecycle_operation.budget.clock = lambda: deadline
        v.helper.audit.outcome = late_audit
    for _ in range(2):
        v.admit()
        assert v.helper.lifecycle_result == {'outcome': setup, 'residue': []}
        assert v.helper.inflight is None
        v.renew()
    assert [row['outcome'] for row in map(json.loads, v.helper.audit.path.read_text().splitlines())
            if row['stage'] == 'outcome'] == ['succeeded', 'succeeded']
    assert v.helper.authority.call.call_count == 2
    assert v.helper.authority.call.call_args.args[1].unpack()[1] == (
        'ai.happyranch.remote-access.' + v.h.LIFECYCLE_ACTIONS[method])
    assert not v.helper._error.called


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


@pytest.mark.parametrize('settlement', ['lock_contended', 'complete_raises'])
def test_thr327_returned_worker_does_not_seal_slot(
        mount: Any, monkeypatch: pytest.MonkeyPatch, settlement: str) -> None:
    v = mount
    v.message.get_member = lambda: 'ReplaceFailed'
    def effect(*args: Any, **kwargs: Any) -> dict:
        assert v.helper.inflight is not None
        assert json.loads(v.helper.audit.path.read_text().splitlines()[-1])['stage'] == 'intent'
        return {'outcome': 'off', 'residue': []}
    v.reader.effect.side_effect = effect
    queued = []
    monkeypatch.setattr(v.h.threading, 'Thread', lambda target, args, **kw:
                        SimpleNamespace(start=lambda: queued.append((target, args))))
    v.admit()
    operation = v.helper.lifecycle_operation
    finished, held, release = threading.Event(), threading.Event(), threading.Event()
    step = operation.run_step

    def run_step(execute: Any) -> Any:
        result = step(execute)
        if settlement == 'lock_contended':
            finished.set()
            assert held.wait(2), 'test lock holder did not reach settlement boundary'
        return result

    operation.run_step = run_step
    if settlement == 'complete_raises':
        operation.complete = Mock(side_effect=RuntimeError('synthetic settlement failure'))

    def hold() -> None:
        assert finished.wait(2), 'worker did not finish its audited effect'
        with v.helper.lock:
            held.set()
            assert release.wait(2), 'test lock holder was not released'

    holder = REAL_THREAD(target=hold) if settlement == 'lock_contended' else None
    if holder is not None:
        holder.start()
    worker = REAL_THREAD(target=queued[0][0], args=queued[0][1])
    worker.start()
    try:
        if holder is not None:
            assert held.wait(2)
        worker.join(.05)
    finally:
        release.set()
        if holder is not None:
            holder.join(2)
        worker.join(2)
    assert not worker.is_alive()
    print(json.dumps(dict(settlement=settlement, worker_returned=True,
        slot_released=v.helper.inflight is None, exit_requested=v.helper.exit_requested,
        outcome=v.helper.lifecycle_result), sort_keys=True))
    assert v.helper.inflight is None, 'terminated lifecycle worker left permanent busy'
    assert v.helper.exit_requested, 'terminated lifecycle helper did not seal admission for exit'


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
        v.renew()  # Isolate projection with the captured observation on a fresh test helper.
        v.helper._status_v2(v.message, ':1.42')
        body = v.helper.connection.send_message.call_args.args[0].body.unpack()[0]
        status = json.loads(body)
        assert status['invite']['state'] == status['cleanup']['state'] == 'uncertain'
        assert status['invite']['age_seconds'] == status['cleanup']['age_seconds'] == 0
    call = v.helper.authority.call.call_args.args[1].unpack()
    assert call[0][1]['name'].unpack() == ':1.42'
    assert call[1] == 'ai.happyranch.remote-access.' + v.h.LIFECYCLE_ACTIONS[method]
    assert call[3] == 1 and call[4]
    assert method in v.h.METHODS


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
    v.renew()  # Projection fixture retains the observation on a fresh test helper.
    v.helper._status_v2(v.message, ':1.42')
    status_text = v.helper.connection.send_message.call_args.args[0].body.unpack()[0]
    assert frame['invite'] not in status_text
    status = json.loads(status_text)
    assert status['invite']['state'] == 'issued' and status['invite']['deadline_utc_seconds'] == frame['expires_at']
    assert frame['invite'] not in repr(v.helper.lifecycle_result) + repr(v.helper.invite_observation)
    assert frame['invite'] not in v.journal.read_text() + v.helper.audit.path.read_text() + caplog.text
    assert frame['invite'] not in ''.join(capsys.readouterr())
    assert 'IssueInvite' in v.h.METHODS
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
        v.renew()  # Isolate the StatusV2 projection from public admission retirement.
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
    v.renew()
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
    # Keep invalid expiry fixtures well clear of child/validation clock boundaries.
    if case == 'expired': frame['expires_at'] = int(time.time()) - 3600
    if case == 'future_expiry': frame['expires_at'] = int(time.time()) + 3600
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


@pytest.mark.parametrize('method', ['Status', 'StatusV2'])
@pytest.mark.parametrize('case', ['clean', 'staged-inert', 'partial', 'pending', 'unsafe', 'unknown', 'failed', 'source', 'txn',
    'founder-expired', 'replacement-expired', 'post-disable-expired'])
def test_thr327_closed_host_status(
        composition: Any, mount: Any, monkeypatch: pytest.MonkeyPatch, method: str, case: str) -> None:
    """Real retained-stage verification after closure; only child/Gio transport is isolated."""
    v, m = composition, mount
    aged = case in ('founder-expired', 'replacement-expired', 'post-disable-expired')
    if case in ('staged-inert', 'founder-expired', 'replacement-expired'):
        v.io.provision(release_user=v.fixture.user, data_home=v.fixture.home)
    elif case == 'post-disable-expired':
        from contextlib import ExitStack
        assert v.lifecycle.dispatch('Enable', os.getuid(), 'enable', v.lifecycle.capture('Enable', os.getuid()),
            deadline=time.monotonic() + 480)['outcome'] == 'ready'
        v.observers.close()
        v.observers = ExitStack()
        expected = v.lifecycle.capture('Disable', os.getuid())
        assert v.lifecycle.dispatch('Disable', os.getuid(), 'stop', expected)['outcome'] == 'in_progress'
        assert v.lifecycle.dispatch('Disable', os.getuid(), 'cleanup', expected, revoke_succeeded=True)['outcome'] == 'off'
        v.admin.users, v.admin.nodes = (), ()
    elif case != 'clean':
        corrupt_composition(v, case)
    if case == 'replacement-expired':
        from runtime.remote_access.home_activation import HomeActivationError
        v.io.publish_home_units()
        v.path('/etc/happyranch/provision.json').unlink()  # Unrecorded failed manual install.
        v.states['happyranch-connector.service'] = 'failed'
        release = v.path('/etc/happyranch/release-daemon.json').read_bytes()
        for _ in range(8):
            expected = v.lifecycle.capture('ReplaceFailed', os.getuid())
            try:
                result = v.lifecycle.dispatch('ReplaceFailed', os.getuid(), 'replace', expected)
            except HomeActivationError:
                assert v.io.replacement_inventory().journal_generation
            else:
                assert result == {'outcome': 'off', 'residue': []}
                break
        else:
            pytest.fail('v2 replacement did not close')
        assert v.path('/etc/happyranch/release-daemon.json').read_bytes() == release
        assert json.loads(v.path('/etc/happyranch/provision.json').read_bytes())['version'] == 1
    if aged:
        from scripts.test_installer_token_custody import expire_staged_policy
        from runtime.remote_access.policy import PolicyEnvelope, RoutePolicyConsumer, PolicyError
        delayed = expire_staged_policy(v.fixture, monkeypatch)
        policy_before = v.path('/etc/happyranch/policy.json').read_bytes()
        with pytest.raises(PolicyError):
            RoutePolicyConsumer.from_envelope(PolicyEnvelope.model_validate_json(policy_before)).require_current(delayed)
        runtime = v.path('/run/happyranch-tsnet-sidecar')
        runtime.mkdir(mode=0o755, exist_ok=True)
        (runtime / 'health.json').write_bytes(b'{"backend":"Running","ready":true,"stale":true}')
        (runtime / 'health.json').chmod(0o644)
        v.path('/run/happyranch-release-verifier').mkdir(mode=0o755, exist_ok=True)
        v.path('/var/lib/happyranch-remote-access').mkdir(mode=0o700, exist_ok=True)
        if case == 'founder-expired':
            # Exact reported founder host shape, including retained service identity.
            for directory in ('happyranch-connector', 'happyranch-tsnet-sidecar'):
                path = v.path('/var/lib/' + directory)
                path.mkdir(mode=0o700, exist_ok=True)
                assert list(path.iterdir()) == []
            assert list(v.path('/run/happyranch-release-verifier').iterdir()) == []
            assert v.io._account(create=False) is not None
    if case == 'staged-inert' or aged:
        assert v.io.inventory().detail == ('post-disable' if case == 'post-disable-expired' else 'staged-inert')
        assert v.path('/etc/happyranch/release-daemon.json').is_file()
        assert not v.path('/etc/happyranch/install-transaction.json').exists()
        assert v.receipts.current() is None and v.transactions.read_optional() is None
    uid = os.getuid()
    m.helper.connection.call_sync.return_value = Variant('', (uid,))
    m.helper.control = m.h.ControlClient(str(v.root / 'absent-connector.sock'))
    m.helper.cursors = {}
    m.helper.generations = SimpleNamespace(current_generation=lambda _: None)
    m.reader.current_generation.side_effect = lambda _: v.lifecycle.capture('Enable', uid)
    m.message.get_member = lambda: method
    monkeypatch.setattr(m.h.threading, 'Thread', lambda target, args=(), **kw:
                        SimpleNamespace(start=lambda: target(*args)))
    monkeypatch.setattr(m.h.threading, 'Timer', lambda *args: SimpleNamespace(start=lambda: None, cancel=lambda: None))
    m.h.Gio.DBusSendMessageFlags = SimpleNamespace(NONE=0)
    reply = SimpleNamespace(set_body=lambda body: setattr(reply, 'body', body))
    m.message.new_method_reply = lambda: reply
    m.message.new_method_error_literal = Mock(side_effect=lambda name, code: SimpleNamespace(name=name, code=code))
    m.helper.connection.send_message = Mock()
    m.helper._reply = m.h.RemoteAccessHelper._reply.__get__(m.helper)
    m.helper._error = m.h.RemoteAccessHelper._error.__get__(m.helper)
    before, calls = staged_inventory(v.fixture), list(v.calls)
    accounts, mints = list(v.account_creations), list(v.mints)
    m.helper._dispatch(m.message)
    m.helper.connection.send_message.assert_called_once()
    if case in ('clean', 'staged-inert') or aged:
        m.message.new_method_error_literal.assert_not_called()
        status = json.loads(reply.body.unpack()[0])
        if method == 'Status':
            assert status == dict(phase='off', ready=False, devices=[], pending=None, selected=None)
        else:
            assert status['setup'] == dict(state='off', age_seconds=0)
            assert status['runtime'] == dict(state='unavailable', age_seconds=0)
    else:
        m.message.new_method_error_literal.assert_called_once_with(
            m.h.INTERFACE + '.Error.host_state_unrecognized', 'host-state-unrecognized')
    assert staged_inventory(v.fixture) == before and v.calls == calls
    assert not m.helper.authority.call.called and not m.reader.effect.called
    assert not m.helper.audit.path.exists() and v.mints == mints and v.account_creations == accounts
    if aged:
        from runtime.remote_access.policy_renew_units import TIMER_NAME
        from scripts.test_home_activation_backend import assert_locked
        from runtime.remote_access import home_activation_owner
        reload = v.io.manager.daemon_reload
        checkpoints = []
        def strict_reload() -> None:
            txn = v.transactions.read_optional()
            if txn is not None and txn.effects[-1].name == 'headscale_start_verify':
                assert txn.effects[-1].state == 'intent'
                assert_locked(v)
                renewed = v.path('/etc/happyranch/policy.json').read_bytes()
                assert renewed != policy_before
                RoutePolicyConsumer.from_envelope(PolicyEnvelope.model_validate_json(renewed)).require_current(delayed)
                checkpoints.append('current before effect reload')
            reload()
        monkeypatch.setattr(v.io.manager, 'daemon_reload', strict_reload)
        # Readiness still requires a live peer. A health file is no observation.
        expected = v.lifecycle.capture('Enable', uid)
        assert json.loads(expected)['prestate'] == ('post-disable' if case == 'post-disable-expired' else 'staged-inert')
        assert v.lifecycle.dispatch('Enable', uid, 'enable', expected, deadline=time.monotonic() + 480)['outcome'] == 'ready'
        assert checkpoints == ['current before effect reload']
        assert v.receipts.current().phase == 'ready'
        assert v.calls.index(['enable', TIMER_NAME]) < v.calls.index(['start', home_activation_owner._TARGET])


def test_thr327_worker_waits_for_transient_main_lock(mount: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    v = mount
    queued = []
    monkeypatch.setattr(v.h.threading, 'Thread', lambda target, args, **kw:
                        SimpleNamespace(start=lambda: queued.append((target, args))))
    v.admit()
    entered = threading.Event()
    def work() -> None:
        entered.set()
        queued[0][0](*queued[0][1])
    with v.helper.lock:
        worker = REAL_THREAD(target=work)
        worker.start()
        assert entered.wait(2)
        worker.join(.05)
        assert not v.effects
    worker.join(3)
    assert not worker.is_alive()
    assert v.effects == ['revoke', 'stop', 'cleanup']
    assert v.helper.lifecycle_result == {'outcome': 'off', 'residue': []}
    assert v.helper.inflight is None


def test_thr327_skipped_revoke_is_partial(mount: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    v = mount
    queued = []
    monkeypatch.setattr(v.h.threading, 'Thread', lambda target, args, **kw:
                        SimpleNamespace(start=lambda: queued.append((target, args))))
    v.admit()
    operation = v.helper.lifecycle_operation
    original = operation.run_step
    skipped = []
    def step(execute: Any) -> Any:
        if not skipped:
            skipped.append(True)
            return operation.outcome
        return original(execute)
    operation.run_step = step
    queued[0][0](*queued[0][1])
    assert v.effects == ['stop', 'cleanup']
    assert v.helper.lifecycle_result['outcome'] == 'partial_cleanup'
    assert 'un-revoked connector trust state and persisted sidecar state' in v.helper.lifecycle_result['residue']

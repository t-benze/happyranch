"""E2b in the existing E1b temp-root/fake-native/AF_UNIX observation venue."""
from __future__ import annotations

import fcntl
import hashlib
import inspect
import io
import json
import os
import socket
import threading
import time
import ssl
import sys
from contextlib import contextmanager, ExitStack
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

import pytest

from runtime.remote_access import home_activation_backend as h, home_activation_owner as adapters
from runtime.remote_access.home_activation import ACTIVATION_PATH, MEMBER_PATHS, OWNED_LINK, OWNED_LINK_TARGET, HomeActivationError, HomeActivationReceipt, HomeActivationStore
from runtime.remote_access.home_activation_coordinator import ActivationRefused
from runtime.remote_access.home_activation_txn import EFFECTS, TRANSACTION_PATH, Effect, HomeActivationTransactionStore
from runtime.remote_access.home_admin import HeadscaleNode, HeadscaleUser
from runtime.remote_access.home_unit_gate import HomeUnitGate, HomeUnitGateError
from runtime.remote_access.installer import managed_units
from runtime.remote_access.service_manager import SystemdServiceManager
from runtime.remote_access.systemd_unit import render_headscale_unit
from scripts.test_home_activation_owner import consumed, venue as owner_venue
from scripts.test_home_live_config import venue as live_venue
from scripts.test_home_activation_txn import Crash, Venue, put
from scripts.test_home_observer import reply
from scripts.test_installer_token_custody import Fixture, home_publication_setup, home_unit_writes, staged_inventory

SIDECAR = "/etc/systemd/system/happyranch-tsnet-sidecar.service"


@pytest.fixture
def composition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """Real installer/activation owners; simulated NSS, native/LAN/materializer.

    The systemd simulation alone creates service directories and live state in
    its own namespace. Helper writes remain separately observable for B8.
    """
    from scripts.test_headscale_custody import KernelDump
    from scripts.test_home_activation_owner import Admin
    from runtime.remote_access import installer, policy_renew_units as policy
    from runtime.remote_access.home_minter import CreationWindow
    from runtime.remote_access.invite_issuer import MintedKey

    fixture = Fixture(tmp_path, monkeypatch)
    for name in ('connector.json', 'sidecar.json', 'policy.json', 'enrollment.key'):
        (fixture.inputs / name).unlink()
    fixture.inputs.rmdir()
    real_socket = socket.socket
    head = home_publication_setup(fixture, monkeypatch)
    monkeypatch.setattr(socket, 'socket', lambda *args, **kw:
        KernelDump(head, args) if args[0] == socket.AF_NETLINK else real_socket(*args, **kw))
    io = fixture.installer
    (fixture.root / 'run').mkdir(mode=0o755)
    v = SimpleNamespace(root=fixture.root, fixture=fixture, head=head, io=io,
        path=lambda path: fixture.root / path.lstrip('/'), admin=Admin(), states={},
        calls=[], mints=[], writes=[], systemd_writes=[], observers=ExitStack(), thread_factory=threading.Thread,
        service_present=False, account_creations=[], track_systemd_paths=False, systemd_paths=set())
    lookup_user, lookup_group = io._lookup_user, io._lookup_group
    def user(name: str) -> Any:
        if name == 'happyranch' and not v.service_present: raise KeyError(name)
        return lookup_user(name)
    def group(name: str) -> Any:
        if name == 'happyranch' and not v.service_present: raise KeyError(name)
        return lookup_group(name)
    io._lookup_user, io._lookup_group = user, group
    v.receipts = HomeActivationStore(root=v.root, root_owner=io.root_owner)
    v.transactions = HomeActivationTransactionStore(root=v.root, root_owner=io.root_owner)
    v.lifecycle = h.HelperLifecycleBackend(io)
    monkeypatch.setattr(h.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(h, 'bootstrap_owner', lambda uid: SimpleNamespace(uid=os.getuid(), data_home=fixture.home))
    monkeypatch.setattr(h.pwd, 'getpwall', lambda: [SimpleNamespace(pw_uid=os.getuid(), pw_name=fixture.user)])
    # Installed read-only package artifacts, including the existing P1 units.
    package = v.root / 'package'
    package.mkdir(mode=0o755)
    put(package / 'route_policy.json', Path(installer.__file__).with_name('route_policy.json').read_bytes(), 0o644)
    monkeypatch.setattr(installer, '__file__', str(package / 'installer.py'))
    for name, render in ((policy.SERVICE_NAME, policy.render_service), (policy.TIMER_NAME, policy.render_timer)):
        put(v.path('/usr/lib/systemd/system/' + name), render().encode(), 0o644)
    for directory in ('usr', 'usr/lib', 'usr/lib/systemd', 'usr/lib/systemd/system'):
        v.path('/' + directory).chmod(0o755)
    v.admin.nodes = ()
    v.admin.list_nodes = lambda **kw: v.admin.nodes
    monkeypatch.setattr(adapters.home_admin, 'RootHeadscaleAdminOperations', lambda: v.admin)
    monkeypatch.setattr(adapters.VerifierClient, 'resolve', lambda self: 12345)
    monkeypatch.setattr(adapters.home_admin, 'read_local_node_window', lambda *a, **kw: v.admin.window)
    def mint(operation_id: str, expires_at: datetime, **kw: Any) -> MintedKey:
        assert_locked(v)
        now = datetime.now(timezone.utc)
        v.mints.append(operation_id)
        v.admin.window = CreationWindow(now, expires_at)
        v.admin.key = replace(v.admin.key, key=f'{len(v.mints):048x}', used=False,
                              created_at=now, expiration=expires_at)
        return MintedKey(v.admin.key.key, expires_at)
    monkeypatch.setattr(adapters.home_admin, 'mint_local_node', mint)
    links = {
        adapters._HEADSCALE: OWNED_LINK,
        adapters._TARGET: '/etc/systemd/system/multi-user.target.wants/' + adapters._TARGET,
        'happyranch-release-verifier.socket': '/etc/systemd/system/happyranch-managed.target.wants/happyranch-release-verifier.socket',
        policy.TIMER_NAME: '/etc/systemd/system/happyranch-managed.target.wants/' + policy.TIMER_NAME,
    }
    def run(args: list[str], **kw: Any) -> tuple[int, str]:
        if args[0] == 'useradd' and args[-1] == 'happyranch':
            v.account_creations.append(args)
            v.service_present = True
            return 0, ''
        if args[0] != 'systemctl':
            code, output = head.run(args, **kw)
            if args[0].endswith('happyranch-home-ca'):
                raw = json.loads(output)
                raw['ca_fingerprint'] = hashlib.sha256(ssl.PEM_cert_to_DER_cert(head.ca_pem.decode())).hexdigest()
                output = json.dumps(raw) + '\n'
            return code, output
        verb = args[1]
        name = args[2] if len(args) > 2 else None
        if verb == 'show':
            path = v.path('/usr/lib/systemd/system/' + name) if name in (policy.SERVICE_NAME, policy.TIMER_NAME) else v.path('/etc/systemd/system/' + name)
            state = v.states.get(name, 'inactive')
            props = dict(Id=name, Names=name, FragmentPath=str(path) if path.exists() else '',
                DropInPaths='', NeedDaemonReload='no', LoadState='loaded' if path.exists() else 'not-found',
                ActiveState=state, SubState=('waiting' if name == policy.TIMER_NAME else 'running') if state == 'active' else ('failed' if state == 'failed' else 'dead'), Job='',
                MainPID=str(os.getpid()) if state == 'active' else '0', ControlPID='0', ControlGroup='',
                User='happyranch-headscale' if name == adapters._HEADSCALE else 'happyranch',
                Group='happyranch-headscale' if name == adapters._HEADSCALE else 'happyranch',
                DynamicUser='no', Unit=policy.SERVICE_NAME, Result='success', UnitFileState='enabled')
            # Match systemd's per-type property shape, including omissions.
            if not name.endswith('.service'):
                props.pop('MainPID')
            if not name.endswith(('.service', '.socket')):
                props.pop('ControlPID')
                props.pop('ControlGroup')
            return 0, '\n'.join(f'{key}={props[key]}' for key in
                (arg.split('=', 1)[1] for arg in args if arg.startswith('--property=')) if key in props)
        v.calls.append(args[1:])
        assert_locked(v)
        mark = len(v.writes)
        before_native = staged_inventory(v.fixture) if v.track_systemd_paths else {}
        try:
            if verb == 'enable' and name in links:
                link = v.path(links[name])
                link.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                if not link.is_symlink():
                    link.symlink_to(OWNED_LINK_TARGET if name == adapters._HEADSCALE else '../' + name)
            if verb == 'disable' and name in links:
                v.path(links[name]).unlink(missing_ok=True)
            if verb in ('stop', 'reset-failed'): v.states[name] = 'inactive'
            if verb == 'start':
                v.states[name] = 'active'
                if name == adapters._HEADSCALE:
                    for relative in ('var/lib/happyranch-headscale', 'run/happyranch-headscale'):
                        v.path('/' + relative).mkdir(mode=0o700, parents=True, exist_ok=True)
                    put(v.path('/var/lib/happyranch-headscale/db.sqlite'), b'synthetic native Headscale DB')
                if name == adapters._TARGET:
                    v.states.update({adapters._CONNECTOR: 'active', adapters._SIDECAR: 'active',
                                     'happyranch-release-verifier.socket': 'active'})
                    if v.path(links[policy.TIMER_NAME]).is_symlink():
                        v.states[policy.TIMER_NAME] = 'active'
                    v.path('/var/lib/happyranch-tsnet-sidecar').mkdir(mode=0o700, parents=True, exist_ok=True)
                    v.path('/run/happyranch-tsnet-sidecar').mkdir(mode=0o755, parents=True, exist_ok=True)
                    put(v.path('/run/happyranch-tsnet-sidecar/health.json'), b'{"backend":"Running"}\n', 0o644)
                    v.admin.key = replace(v.admin.key, used=True)
                    v.admin.nodes = (HeadscaleNode(1, HeadscaleUser(1, 'happyranch-node'), 'c' * 64, ('100.64.0.10',)),)
                    role = 'home-sidecar-' + v.receipts.current().home_id
                    marker = hashlib.sha256((role + '\x00https://control.happyranch.invalid:8443').encode()).hexdigest()
                    put(v.path('/var/lib/happyranch-tsnet-sidecar/credential.consumed'), (marker + '\n').encode())
                    v.observers.enter_context(observations(v))
        finally:
            v.systemd_writes.extend(v.writes[mark:])
            del v.writes[mark:]
            if v.track_systemd_paths:
                after_native = staged_inventory(v.fixture)
                v.systemd_paths.update(path for path in before_native.keys() | after_native.keys()
                                       if before_native.get(path) != after_native.get(path))
        return 0, ''
    io._run = run
    io.manager = SystemdServiceManager(system=True, run=run)
    try:
        yield v
    finally:
        v.observers.close()


def test_clean_enable_capture_without_headscale_account(composition: Any) -> None:
    v = composition
    assert v.io._headscale_shape() is None
    native = v.io._run
    scans = []

    def run(args: list[str], **kwargs: Any) -> tuple[int, str]:
        if args == ['ps', '-eo', 'ruid=,euid=,suid=,fuid=']:
            scans.append(args)
            return 0, '1001 1001 1001 1001\n'
        return native(args, **kwargs)

    v.io._run = run
    expected = v.lifecycle.capture('Enable', os.getuid())
    assert json.loads(expected)['prestate'] == 'clean'
    assert scans and not v.head.present
    assert not v.calls and not v.account_creations


@pytest.mark.parametrize('journal_fails', [False, True])
def test_enable_effect_failure_journals_only_type_source_step(
        composition: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any,
        journal_fails: bool) -> None:
    import syslog
    from unittest.mock import Mock
    from runtime.remote_access import installer

    v = composition
    expected = v.lifecycle.capture('Enable', os.getuid())
    native = v.io._run
    secret = 'token=synthetic-secret https://private.invalid/auth /home/private-user pairing=12345'

    def run(argv: list[str], **kwargs: Any) -> tuple[int, str]:
        if Path(argv[0]).name == 'useradd':
            raise OSError(secret)
        return native(argv, **kwargs)

    v.io._run = run
    monkeypatch.setattr(h, 'ManagedInstaller', lambda **kwargs: v.io)
    monkeypatch.setattr(sys, 'argv', ['home_activation_backend.py'])
    request = dict(method='Enable', uid=os.getuid(), step='enable', expected=expected,
                   deadline=time.monotonic() + 30)
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode())))
    sent = Mock(side_effect=OSError('synthetic-journal-secret') if journal_fails else None)
    monkeypatch.setattr(syslog, 'syslog', sent)
    assert h.helper_main() == 1
    # The innermost real product frame calls the synthetic failing native runner.
    lines, start = inspect.getsourcelines(installer.ManagedInstaller._account)
    line = start + next(i for i, text in enumerate(lines) if 'code, _ = self._run(' in text)
    sent.assert_called_once_with(syslog.LOG_WARNING,
        'remote-access-activation-failure exception=OSError '
        f'source=runtime/remote_access/installer.py:{line} step=enable stage=account')
    emitted = str(sent.call_args)
    assert all(value not in emitted for value in (secret, 'synthetic-secret', 'private-user',
                                                  'https://', '12345', 'synthetic-journal-secret'))
    # The only child output is the closed generic category, even if journaling fails.
    assert capsys.readouterr() == ('{"preparationFailure":"activation-unverified"}\n', '')
    assert v.receipts.current() is None and not v.calls


@pytest.mark.parametrize('prestate', ['clean', 'staged-inert', 'prepared'])
def test_composed_enable_real_owners(composition: Any, monkeypatch: pytest.MonkeyPatch, prestate: str) -> None:
    v = composition
    assert not v.fixture.inputs.exists() and not v.fixture.key.exists()
    if prestate != 'clean': v.io.provision(release_user=v.fixture.user, data_home=v.fixture.home)
    if prestate == 'prepared':
        v.io.prepare_headscale()
        v.io.publish_home_units()
        v.io.publish_invite_config()
    expected = v.lifecycle.capture('Enable', os.getuid())
    assert json.loads(expected)['prestate'] == prestate
    v.writes = home_unit_writes(monkeypatch, v.root)
    assert v.lifecycle.dispatch('Enable', os.getuid(), 'enable', expected, deadline=time.monotonic() + 480) == {
        'outcome': 'ready', 'residue': []}
    ready = v.receipts.read(expected_generation=0)
    assert ready.phase == 'ready' and ready.local_node_stable_id == 'self-stable'
    assert len(v.mints) == 1 and not v.path('/etc/happyranch/enrollment.key').exists()
    assert len(v.account_creations) == 1
    assert json.loads(v.path('/etc/happyranch/sidecar.json').read_bytes())['home_binding']['activation_generation'] == 0
    calls, files = list(v.calls), staged_inventory(v.fixture)
    expected = v.lifecycle.capture('Enable', os.getuid())
    assert v.lifecycle.dispatch('Enable', os.getuid(), 'enable', expected, deadline=time.monotonic() + 480)['outcome'] == 'ready'
    assert v.calls == calls and staged_inventory(v.fixture) == files and len(v.mints) == 1


@pytest.mark.parametrize('case', ['twice', 'failed-revoke'])
def test_composed_disable_reenable(composition: Any, case: str) -> None:
    v = composition
    for cycle in range(3 if case == 'twice' else 1):
        expected = v.lifecycle.capture('Enable', os.getuid())
        assert json.loads(expected)['prestate'] == ('clean' if cycle == 0 else 'post-disable')
        assert v.lifecycle.dispatch('Enable', os.getuid(), 'enable', expected,
                                    deadline=time.monotonic() + 480)['outcome'] == 'ready'
        ready = v.receipts.read(expected_generation=0)
        health = v.path('/run/happyranch-tsnet-sidecar/health.json')
        assert health.is_file() and health.stat().st_mode & 0o7777 == 0o644
        assert ready.ca_der_sha256 == hashlib.sha256(ssl.PEM_cert_to_DER_cert(v.head.ca_pem.decode())).hexdigest()
        assert len(v.mints) == cycle + 1 and len(set(v.mints)) == cycle + 1
        v.observers.close()
        v.observers = ExitStack()
        expected = v.lifecycle.capture('Disable', os.getuid())
        assert v.lifecycle.dispatch('Disable', os.getuid(), 'stop', expected)['outcome'] == 'in_progress'
        if case == 'failed-revoke':
            with pytest.raises(adapters.headscale_service.HeadscaleServiceError):
                v.lifecycle.dispatch('Disable', os.getuid(), 'cleanup', expected, revoke_succeeded=False)
            before, calls = staged_inventory(v.fixture), list(v.calls)
            # Existing failed cleanup authority is retained. A new Enable may
            # neither resume staging nor mutate this ready-but-stopped install.
            with pytest.raises((ActivationRefused, OSError, adapters.headscale_service.HeadscaleServiceError)):
                v.lifecycle.capture('Enable', os.getuid())
            assert staged_inventory(v.fixture) == before and v.calls == calls
            return
        assert v.lifecycle.dispatch('Disable', os.getuid(), 'cleanup', expected,
                                    revoke_succeeded=True) == {'outcome': 'off', 'residue': []}
        assert v.receipts.current() is None and v.transactions.read_optional() is None
        assert not v.path('/var/lib/happyranch-headscale/db.sqlite').exists()
        assert not v.path('/etc/happyranch/invite-issuer.json').exists()
        assert not health.parent.exists()
        v.admin.users, v.admin.nodes = (), ()
        v.head.ca_pem = ssl.DER_cert_to_PEM_cert(f'synthetic fresh public CA {cycle}'.encode()).encode()


@pytest.mark.parametrize('boundary', ['provision', 'prepare_headscale', 'publish_home_units',
                                      'publish_invite_config', 'enable'])
def test_composed_interruption_retry(composition: Any, monkeypatch: pytest.MonkeyPatch, boundary: str) -> None:
    v = composition
    expected = v.lifecycle.capture('Enable', os.getuid())
    owner = h.HomeActivationBackend if boundary == 'enable' else v.io
    original = getattr(owner, boundary)
    def crash(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        raise Crash()
    with monkeypatch.context() as patch:
        patch.setattr(owner, boundary, crash)
        with pytest.raises(Crash):
            v.lifecycle.dispatch('Enable', os.getuid(), 'enable', expected, deadline=time.monotonic() + 480)
    expected = v.lifecycle.capture('Enable', os.getuid())
    assert v.lifecycle.dispatch('Enable', os.getuid(), 'enable', expected,
                                deadline=time.monotonic() + 480)['outcome'] == 'ready'
    assert v.receipts.read(expected_generation=0).phase == 'ready' and len(v.mints) == 1


@pytest.mark.parametrize('boundary', ['binding', 'provision', 'prepare_headscale', 'publish_home_units',
                                      'publish_invite_config', 'enable'])
def test_composed_original_deadline(composition: Any, monkeypatch: pytest.MonkeyPatch, boundary: str) -> None:
    v = composition
    now = [100.0]
    monkeypatch.setattr(h.time, 'monotonic', lambda: now[0])
    expected = v.lifecycle.capture('Enable', os.getuid())
    owner = h if boundary == 'binding' else h.HomeActivationBackend if boundary == 'enable' else v.io
    method = 'bootstrap_owner' if boundary == 'binding' else boundary
    original = getattr(owner, method)
    at_expiry = []
    def expire(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        now[0] = 580.0
        at_expiry.append((staged_inventory(v.fixture), list(v.calls), list(v.mints)))
        return result
    with monkeypatch.context() as patch:
        patch.setattr(owner, method, expire)
        with pytest.raises(ActivationRefused):
            v.lifecycle.dispatch('Enable', os.getuid(), 'enable', expected, deadline=580.0)
    assert not v.mints or boundary == 'enable'
    assert at_expiry == [(staged_inventory(v.fixture), v.calls, v.mints)]


@pytest.mark.parametrize('case', ['partial', 'failed', 'pending', 'uid', 'unsafe', 'unknown', 'txn', 'source', 'audit', 'headscale-mode', 'clean-ca', 'clean-unit'])
def test_composed_capture_refusal(composition: Any, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    v = composition
    corrupt_composition(v, case)
    before, calls = staged_inventory(v.fixture), list(v.calls)
    writes = home_unit_writes(monkeypatch, v.root)
    from runtime.remote_access.installer import InstallerError
    with pytest.raises((ValueError, OSError, TypeError, InstallerError)):
        v.lifecycle.capture('Enable', os.getuid() + 1 if case == 'uid' else os.getuid())
    assert staged_inventory(v.fixture) == before and v.calls == calls and writes == []
    assert v.receipts.current() is None and not v.mints


def corrupt_composition(v: Any, case: str) -> None:
    """Same fixed refusal inputs for owner and pre-auth transport boundaries."""
    if case == 'clean-ca':
        put(v.path('/var/lib/happyranch-remote-access/home-ca/ca.key'), b'synthetic unreceipted CA private key')
        put(v.path('/var/lib/happyranch-remote-access/home-ca/ca.pem'), v.head.ca_pem)
        v.path('/var/lib/happyranch-remote-access/home-ca').chmod(0o700)
        return
    if case == 'clean-unit':
        put(v.path(MEMBER_PATHS[0]), render_headscale_unit().encode(), 0o644)
        v.path('/etc/systemd').chmod(0o755)
        v.path('/etc/systemd/system').chmod(0o755)
        return
    v.io.provision(release_user=v.fixture.user, data_home=v.fixture.home)
    if case == 'partial': v.path('/etc/happyranch/provision.json').unlink()
    if case == 'failed':
        v.io.publish_home_units()
        v.states[adapters._CONNECTOR] = 'failed'
    if case == 'pending': v.path('/etc/happyranch/.sidecar.json.pending').touch(mode=0o600)
    if case == 'unsafe': v.path('/etc/happyranch/sidecar.json').chmod(0o666)
    if case == 'unknown': v.path('/etc/happyranch/unknown').touch(mode=0o600)
    if case == 'txn': put(v.path(TRANSACTION_PATH), b'{}')
    if case == 'source': v.fixture.token.write_bytes(b'synthetic rotated token')
    if case == 'audit': put(v.path('/var/lib/happyranch-remote-access/home-audit.jsonl'), b'synthetic unsafe audit', 0o644)
    if case == 'headscale-mode':
        v.io.prepare_headscale()
        v.path('/etc/happyranch/headscale').chmod(0o755)


@pytest.mark.parametrize('when', ['before-lock', 'under-lock'])
def test_composed_capture_cas_drift(composition: Any, monkeypatch: pytest.MonkeyPatch, when: str) -> None:
    v = composition
    expected = v.lifecycle.capture('Enable', os.getuid())
    original = h._OperationTransactions.locked
    @contextmanager
    def locked(store: Any) -> Iterator[int]:
        with original(store) as parent:
            v.io.provision(release_user=v.fixture.user, data_home=v.fixture.home)
            yield parent
    if when == 'before-lock': v.io.provision(release_user=v.fixture.user, data_home=v.fixture.home)
    else: monkeypatch.setattr(h._OperationTransactions, 'locked', locked)
    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail('CAS drift reached a staging effect owner')
    monkeypatch.setattr(v.lifecycle, '_enable_staging', forbidden)
    with pytest.raises(ActivationRefused):
        v.lifecycle.dispatch('Enable', os.getuid(), 'enable', expected, deadline=time.monotonic() + 480)
    assert v.receipts.current() is None and not v.calls and not v.mints


def test_composed_clean_helper_writes_stay_in_b8(composition: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    v = composition
    entries = [line.split('=', 1)[1] for line in Path(
        'packaging/linux/systemd/happyranch-remote-access-helper.service').read_text().splitlines()
        if line.startswith('ReadWritePaths=')]
    allowed = [Path(entry.lstrip('-/')) for entry in entries
               if not entry.startswith('-') or v.path(entry[1:]).is_dir()]
    before = staged_inventory(v.fixture)
    v.track_systemd_paths = True
    v.writes = home_unit_writes(monkeypatch, v.root)
    assert v.lifecycle.dispatch('Enable', os.getuid(), 'enable', v.lifecycle.capture('Enable', os.getuid()),
                                deadline=time.monotonic() + 480)['outcome'] == 'ready'
    after = staged_inventory(v.fixture)
    changed = {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)}
    helper_changes = changed - v.systemd_paths
    assert v.writes and helper_changes and v.systemd_writes and v.systemd_paths
    outside = sorted({path for path in [*v.writes, *helper_changes]
                      if not any(Path(path).is_relative_to(root) for root in allowed)})
    assert not outside, outside


@pytest.mark.parametrize('failure', ['refusal', 'provision-drift', 'config-custody'])
def test_thr327_renewal_failure_starts_no_unit(
        composition: Any, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    """A forward Enable policy-renewal refusal must precede every unit start."""
    v = composition
    v.io.provision(release_user=v.fixture.user, data_home=v.fixture.home)
    expected = v.lifecycle.capture('Enable', os.getuid())
    assert json.loads(expected)['prestate'] == 'staged-inert'
    attempts = []
    effect_calls = []
    publish = HomeActivationTransactionStore.publish
    def intent(store: Any, value: Any, **kwargs: Any) -> None:
        publish(store, value, **kwargs)
        if (value.phase == 'activating' and value.effects[-1].name == 'headscale_start_verify'
                and value.effects[-1].state == 'intent'):
            effect_calls.append(len(v.calls))
    monkeypatch.setattr(HomeActivationTransactionStore, 'publish', intent)
    renew = v.io._renew_policy_locked
    def refuse(*, fresh_auth: bool) -> None:
        assert_locked(v)
        assert fresh_auth is True
        attempts.append('renewal refused')
        if failure == 'refusal':
            raise adapters.installer.InstallerError('synthetic renewal refusal')
        renew(fresh_auth=fresh_auth)
        if failure == 'provision-drift':
            path = v.path('/etc/happyranch/provision.json')
            raw = json.loads(path.read_bytes())
            raw['digests']['release-daemon.json'] = 'a' * 64
            path.write_bytes(json.dumps(raw, sort_keys=True).encode())
        else:
            v.path('/etc/happyranch/connector.json').chmod(0o666)
    monkeypatch.setattr(v.io, '_renew_policy_locked', refuse)
    with pytest.raises(ActivationRefused):
        v.lifecycle.dispatch('Enable', os.getuid(), 'enable', expected,
                             deadline=time.monotonic() + 480)
    assert attempts == ['renewal refused']  # The intended guard actually ran.
    started = [call for call in v.calls if call[0] in ('enable', 'start')]
    assert not started, started
    assert len(effect_calls) == 1 and v.calls[effect_calls[0]:] == []


def test_thr327_noop_renewal_still_requires_current(composition: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.test_installer_token_custody import expire_staged_policy
    v = composition
    v.io.provision(release_user=v.fixture.user, data_home=v.fixture.home)
    expire_staged_policy(v.fixture, monkeypatch)
    attempts = []
    def noop(*, fresh_auth: bool) -> None:
        assert_locked(v)
        assert fresh_auth is True
        attempts.append('no publication')
    monkeypatch.setattr(v.io, '_renew_policy_locked', noop)
    expected = v.lifecycle.capture('Enable', os.getuid())
    with pytest.raises(ActivationRefused):
        v.lifecycle.dispatch('Enable', os.getuid(), 'enable', expected, deadline=time.monotonic() + 480)
    assert attempts == ['no publication']
    assert not [call for call in v.calls if call[0] in ('enable', 'start')]


@pytest.mark.parametrize('receipt_exists', [False, True])
@pytest.mark.parametrize('renewal', ['fresh', 'noop', 'refused'])
def test_prepared_retry_restages_expired_policy_before_preflight(
        composition: Any, monkeypatch: pytest.MonkeyPatch, receipt_exists: bool, renewal: str) -> None:
    from scripts.test_installer_token_custody import expire_staged_policy
    v = composition
    v.io.provision(release_user=v.fixture.user, data_home=v.fixture.home)
    v.io.prepare_headscale()
    v.io.publish_home_units()
    v.io.publish_invite_config()
    if receipt_exists:
        h.HomeActivationPrepare(io=v.io).prepare(expected_generation=0, authenticated=True)
    expire_staged_policy(v.fixture, monkeypatch)
    before = json.loads(v.path('/etc/happyranch/policy.json').read_bytes())
    renew = v.io._renew_policy_locked
    calls = []
    def restage(*, fresh_auth: bool) -> None:
        assert_locked(v)
        calls.append(v.transactions.read_optional())
        if renewal == 'refused':
            raise adapters.installer.InstallerError('synthetic renewal refusal')
        if renewal == 'fresh':
            renew(fresh_auth=fresh_auth)
    monkeypatch.setattr(v.io, '_renew_policy_locked', restage)
    expected = v.lifecycle.capture('Enable', os.getuid())
    if renewal == 'fresh':
        assert v.lifecycle.dispatch('Enable', os.getuid(), 'enable', expected,
                                    deadline=time.monotonic() + 480)['outcome'] == 'ready'
        policy = json.loads(v.path('/etc/happyranch/policy.json').read_bytes())
        assert policy['revision'] > before['revision'] and policy['issued_at'] > before['issued_at']
        assert len(v.mints) == 1
    else:
        with pytest.raises(ActivationRefused):
            v.lifecycle.dispatch('Enable', os.getuid(), 'enable', expected,
                                 deadline=time.monotonic() + 480)
        assert not v.mints and not [call for call in v.calls if call[0] in ('enable', 'start')]
    assert calls and calls[0] is None  # Fresh envelope precedes activation preflight intent.


def test_thr327_stale_health_cannot_make_enable_ready(composition: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    v = composition
    v.io.provision(release_user=v.fixture.user, data_home=v.fixture.home)
    runtime = v.path('/run/happyranch-tsnet-sidecar')
    runtime.mkdir(mode=0o755)
    stale = b'{"backend":"Running","ready":true}'
    put(runtime / 'health.json', stale, 0o644)
    # Simulate native services starting without their live observation listener.
    monkeypatch.setattr(v.observers, 'enter_context', lambda context: None)
    start = v.io.manager.start
    def no_peer(name: str, **kwargs: Any) -> None:
        start(name, **kwargs)
        if name == adapters._TARGET:
            put(runtime / 'health.json', stale, 0o644)
    monkeypatch.setattr(v.io.manager, 'start', no_peer)
    expected = v.lifecycle.capture('Enable', os.getuid())
    with pytest.raises(ActivationRefused):
        v.lifecycle.dispatch('Enable', os.getuid(), 'enable', expected, deadline=time.monotonic() + 480)
    assert ['start', adapters._TARGET] in v.calls
    assert v.states[adapters._SIDECAR] == 'active' and v.admin.key.used
    assert not (runtime / 'observe.sock').exists() and (runtime / 'health.json').read_bytes() == stale
    assert v.receipts.current().phase == 'failed_uncertain'
    assert v.transactions.read_optional().effects[-1].name == 'managed_services'


@pytest.mark.parametrize('expired', [False, True])
@pytest.mark.parametrize('entry', ['recover', 'enable'])
def test_thr327_old_headscale_intent_never_renews(
        venue: Venue, monkeypatch: pytest.MonkeyPatch, expired: bool, entry: str) -> None:
    from runtime.remote_access.home_activation_txn import HomeActivationTransaction
    from scripts.test_installer_token_custody import expire_staged_policy
    v = venue
    v.backend._prepare.prepare(expected_generation=7, authenticated=True)
    receipt = v.receipts.read(expected_generation=7)
    # Literal old-format wire keys and prefix, independent of the current writer.
    raw = {key: getattr(receipt, key) for key in (
        'release_uid', 'home_id', 'control_plane_id', 'connector_id', 'activation_generation',
        'operation_id', 'ca_der_sha256', 'dial_ip')}
    raw.update(format='home-activation-txn@1', phase='activating', effects=[
        dict(name='preflight', state='done', member_digests={}),
        dict(name='headscale_start_verify', state='intent', member_digests={})])
    data = json.dumps(raw, sort_keys=True).encode()
    parsed = HomeActivationTransaction.parse(data)
    # This writer module is byte-identical to the pre-fix main writer.
    data = parsed.to_bytes()
    assert json.loads(data) == raw
    put(v.path(TRANSACTION_PATH), data)
    put(v.path(ACTIVATION_PATH), replace(receipt, phase='activating').to_bytes())
    if expired:
        expire_staged_policy(SimpleNamespace(root=v.root), monkeypatch)
    active = v.receipts.current()
    coordinator, _, _ = v.backend._compose(active)
    assert coordinator._owner.verify('preflight', active) is (not expired)
    calls = []
    def forbidden(*args: Any, **kwargs: Any) -> None:
        calls.append('renew')
        pytest.fail('old journal recovery must never renew')
    monkeypatch.setattr(v.receipts._io, '_renew_policy_locked', forbidden)
    before = unit_config_bytes(v), list(v.audit), list(v.native.calls)
    # Existing I3a semantics: an interrupted nonterminal prefix is terminalized,
    # never resumed, including an intent predating early renewal.
    with pytest.raises(ActivationRefused):
        if entry == 'recover':
            v.backend.recover(expected_generation=7, expected_operation_id=receipt.operation_id,
                              fresh_authenticated=True)
        else:
            v.backend.enable(expected_generation=7, authenticated=True)
    terminal = v.transactions.read_optional()
    assert terminal.phase == 'failed_uncertain' and terminal.effects == parsed.effects
    assert v.receipts.current().phase == 'failed_uncertain' and calls == []
    assert (unit_config_bytes(v), v.audit, v.native.calls) == before


def test_composed_replace_failed_then_enable(composition: Any) -> None:
    v = composition
    v.io.provision(release_user=v.fixture.user, data_home=v.fixture.home)
    v.io.publish_home_units()
    v.states[adapters._CONNECTOR] = 'failed'
    assert v.io.inventory().state == 'known-failed-or-partial'
    from runtime.remote_access.home_activation import HomeActivationError
    phases = []
    # Existing B17 deliberately advances one bounded phase per fresh-auth call;
    # its exact source/config/unit/staging/closure owner is unchanged here.
    for _ in range(8):
        expected = v.lifecycle.capture('ReplaceFailed', os.getuid())
        try:
            result = v.lifecycle.dispatch('ReplaceFailed', os.getuid(), 'replace', expected)
        except HomeActivationError:
            candidate = v.io.replacement_inventory()
            assert candidate.installation.state == 'known-failed-or-partial' and candidate.journal_generation
            phase = candidate.installation.detail
            assert phase not in phases
            phases.append(phase)
        else:
            assert result == {'outcome': 'off', 'residue': []}
            break
    else:
        pytest.fail('existing replacement closure did not finish within its documented phases')
    assert v.io.inventory().detail == 'staged-inert'
    assert v.lifecycle.dispatch('Enable', os.getuid(), 'enable', v.lifecycle.capture('Enable', os.getuid()),
                                deadline=time.monotonic() + 480)['outcome'] == 'ready'
    assert v.receipts.read(expected_generation=0).phase == 'ready'


def post_disable_interrupted(v: Any, monkeypatch: pytest.MonkeyPatch, boundary: str, *,
                             rollovers: int = 1, retained_generation: int = 0) -> Any:
    """Real PD -> interruption -> B7, including a newly materialized CA."""
    from datetime import timedelta
    from runtime.remote_access.home_live_config import HomeLiveConfigOwner
    assert v.lifecycle.dispatch('Enable', os.getuid(), 'enable', v.lifecycle.capture('Enable', os.getuid()),
                                deadline=time.monotonic() + 480)['outcome'] == 'ready'
    old_ca = v.receipts.current().ca_der_sha256
    v.observers.close()
    v.observers = ExitStack()
    expected = v.lifecycle.capture('Disable', os.getuid())
    assert v.lifecycle.dispatch('Disable', os.getuid(), 'stop', expected)['outcome'] == 'in_progress'
    assert v.lifecycle.dispatch('Disable', os.getuid(), 'cleanup', expected, revoke_succeeded=True)['outcome'] == 'off'
    v.admin.users, v.admin.nodes = (), ()
    if retained_generation:
        # Synthetic old-generation variation of the exact deterministic tail.
        path = v.path('/etc/happyranch/sidecar.json')
        data = path.read_bytes()
        assert data.count(b'"activation_generation":0') == 1
        path.write_bytes(data.replace(b'"activation_generation":0',
                                     f'"activation_generation":{retained_generation}'.encode()))
    v.head.ca_pem = ssl.DER_cert_to_PEM_cert(b'synthetic replacement CA').encode()
    for generation in range(rollovers):
        expected = v.lifecycle.capture('Enable', os.getuid())
        connector_inode = v.path('/etc/happyranch/connector.json').stat().st_ino
        retained_sidecar = v.path('/etc/happyranch/sidecar.json').read_bytes()
        with monkeypatch.context() as patch:
            if boundary == 'publish_invite_config':
                publish = v.io.publish_invite_config
                def interrupted(**kw: Any) -> None:
                    publish(**kw)
                    raise Crash()
                patch.setattr(v.io, 'publish_invite_config', interrupted)
            elif boundary == 'home_config-partial':
                publish = HomeLiveConfigOwner._publish
                def interrupted(owner: Any, name: str, *args: Any) -> None:
                    publish(owner, name, *args)
                    if name == 'connector.json': raise Crash()
                patch.setattr(HomeLiveConfigOwner, '_publish', interrupted)
            else:
                publish = h.HomeActivationCoordinator._publish_transaction
                def interrupted(owner: Any, txn: Any, *args: Any) -> None:
                    publish(owner, txn, *args)
                    effect = txn.effects[-1]
                    name = 'home_config' if boundary == 'home_config-intent' else boundary
                    state = 'intent' if boundary == 'home_config-intent' else 'done'
                    if effect.name == name and effect.state == state: raise Crash()
                patch.setattr(h.HomeActivationCoordinator, '_publish_transaction', interrupted)
            with pytest.raises(Crash):
                v.lifecycle.dispatch('Enable', os.getuid(), 'enable', expected, deadline=time.monotonic() + 480)
        if boundary == 'publish_invite_config':
            break
        assert v.receipts.current().ca_der_sha256 != old_ca
        if boundary == 'credential_staging':
            assert all(e.name != 'home_config' for e in v.transactions.read_optional().effects)
            retained = json.loads(v.path('/etc/happyranch/sidecar.json').read_bytes())
            assert retained['control_tls']['ca_sha256'] == old_ca
            assert retained['home_binding']['activation_generation'] == retained_generation
        if boundary.startswith('home_config-'):
            assert v.transactions.read_optional().effects[-1] == Effect('home_config', 'intent', {})
            assert v.path('/etc/happyranch/sidecar.json').read_bytes() == retained_sidecar
            assert (v.path('/etc/happyranch/connector.json').stat().st_ino != connector_inode) == (
                boundary == 'home_config-partial')
        # Unchanged Ruling 17 first marks the interrupted current pair terminal.
        with pytest.raises(ActivationRefused):
            v.lifecycle.dispatch('Enable', os.getuid(), 'enable', v.lifecycle.capture('Enable', os.getuid()),
                                 deadline=time.monotonic() + 480)
        assert v.receipts.current().phase == 'failed_uncertain'
        # Native key expiry and no node are the existing B7 rollover proof.
        v.admin.key = replace(v.admin.key, expiration=datetime.now(timezone.utc) - timedelta(seconds=1))
        assert v.lifecycle.dispatch('Enable', os.getuid(), 'enable', v.lifecycle.capture('Enable', os.getuid()),
                                    deadline=time.monotonic() + 480)['outcome'] == 'prepared'
        assert v.receipts.current().activation_generation == generation + 1
    return v.receipts.current()


@pytest.mark.parametrize('boundary,rollovers,retained_generation', [
    ('publish_invite_config', 1, 0), ('home_config', 1, 0),
    ('credential_staging', 1, 0), ('credential_staging', 2, 0),
    ('credential_staging', 1, 1), ('credential_staging', 1, 7),
])
def test_composed_post_disable_interruption(composition: Any, monkeypatch: pytest.MonkeyPatch,
        boundary: str, rollovers: int, retained_generation: int) -> None:
    v = composition
    prepared = post_disable_interrupted(v, monkeypatch, boundary,
        rollovers=rollovers, retained_generation=retained_generation)
    assert v.lifecycle.dispatch('Enable', os.getuid(), 'enable', v.lifecycle.capture('Enable', os.getuid()),
                                deadline=time.monotonic() + 480)['outcome'] == 'ready'
    ready = v.receipts.current()
    assert ready.activation_generation == (0 if boundary == 'publish_invite_config' else rollovers)
    assert ready.phase == 'ready'
    if prepared is not None:
        assert ready.ca_der_sha256 == prepared.ca_der_sha256
    sidecar = json.loads(v.path('/etc/happyranch/sidecar.json').read_bytes())
    assert sidecar['home_binding']['activation_generation'] == ready.activation_generation
    assert sidecar['control_tls']['ca_sha256'] == ready.ca_der_sha256
    assert v.transactions.read_optional().matches(ready)


@pytest.mark.parametrize('case', ['home_config-intent', 'home_config-partial', 'two-additions',
    'foreign-tail', 'stable-ID', 'connector-drift', 'strip-mismatch', 'pending'])
def test_composed_post_disable_rollover_refuses_without_effects(
        composition: Any, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    from runtime.remote_access.home_live_config import HomeLiveConfigOwner, _addition
    v = composition
    prepared = post_disable_interrupted(v, monkeypatch,
        case if case.startswith('home_config-') else 'credential_staging')
    path = v.path('/etc/happyranch/sidecar.json')
    data = path.read_bytes()
    if case == 'two-additions':
        end = data.rindex(b'}')
        path.write_bytes(data[:end] + _addition(prepared) + data[end:])
    if case == 'foreign-tail':
        end = data.rindex(b'}')
        path.write_bytes(data[:end] + b',"foreign":true' + data[end:])
    if case == 'stable-ID':
        needle = b'"home_id":"' + prepared.home_id.encode() + b'"'
        assert data.count(needle) == 1
        path.write_bytes(data.replace(needle, b'"home_id":"foreign-home"'))
    if case == 'connector-drift':
        connector = v.path('/etc/happyranch/connector.json')
        connector.write_bytes(connector.read_bytes() + b' ')
    if case == 'strip-mismatch': path.write_bytes(b' ' + data)
    if case == 'pending': put(path.with_name('.sidecar.json.pending'), b'synthetic pending')
    before = staged_inventory(v.fixture)
    calls, mints = len(v.calls), len(v.mints)
    writes = home_unit_writes(monkeypatch, v.root)
    config = HomeLiveConfigOwner(io=v.io, expected_generation=prepared.activation_generation,
        expected_operation_id=prepared.operation_id)
    assert not config.verify('preflight', prepared), case
    assert writes == [] and staged_inventory(v.fixture) == before
    assert v.calls[calls:] == [] and len(v.mints) == mints


@pytest.fixture
def venue(live_venue: Venue, monkeypatch: pytest.MonkeyPatch) -> Venue:
    v = live_venue
    monkeypatch.setattr(h.os, "geteuid", lambda: 0)
    v.path(ACTIVATION_PATH).unlink()
    v.path(MEMBER_PATHS[0]).write_bytes(render_headscale_unit().encode())
    v.legacy = managed_units("source")[adapters._SIDECAR].encode()
    v.home = v.legacy.replace(b"NotifyAccess=main", b"Environment=SSL_CERT_FILE=/etc/happyranch/control-ca.pem\nEnvironment=SSL_CERT_DIR=/etc/happyranch/empty-ca-directory\nNotifyAccess=main", 1)
    v.path(SIDECAR).write_bytes(v.legacy)
    v.path("/etc/happyranch/headscale").chmod(0o700)
    # The mocked publication above claims this exact installed member exists.
    v.path("/etc/happyranch/empty-ca-directory").mkdir(mode=0o755)
    v.path("/run").mkdir(mode=0o755)
    v.active = False
    v.fail = None
    v.audit = []
    base_run = v.native.run

    def run(args: list[str], **kwargs: Any) -> tuple[int, str]:
        if args[0] == "ps":
            return 0, "99999 99999 99999 99999\n"
        if args[1] == "show":
            name = args[2]
            if name == adapters._HEADSCALE and not v.active:
                v.native.override.update(ActiveState="inactive", SubState="dead", MainPID="0")
            else:
                v.native.override.update(ActiveState="active", SubState=("waiting" if name == "happyranch-remote-access-policy-renew.timer" else "running"), MainPID=str(os.getpid()))
            v.native.override.update(ControlPID="0", ControlGroup="", Job="")
            if name == "happyranch-remote-access-policy-renew.timer":
                v.native.override["UnitFileState"] = "enabled"
        else:
            txn = v.transactions.read_optional()
            receipt_path = v.path(ACTIVATION_PATH)
            phase = v.receipts.current().phase if receipt_path.exists() else None
            v.audit.append((args[1:], phase, txn.effects[-1] if txn else None, v.path(SIDECAR).read_bytes()))
            # Independent descriptor must be excluded even between coordinator
            # contexts, including Prepare's reload and publish_ready's observer.
            assert_locked(v)
            if args[1] == "enable" and args[2] == adapters._HEADSCALE:
                link = v.path(OWNED_LINK)
                link.parent.mkdir(mode=0o755, exist_ok=True)
                if link.is_symlink():
                    assert os.readlink(link) == OWNED_LINK_TARGET
                else:
                    link.symlink_to(OWNED_LINK_TARGET)
            if args[1] == "enable" and args[2] == 'happyranch-remote-access-policy-renew.timer':
                link = v.path('/etc/systemd/system/happyranch-managed.target.wants/' + args[2])
                link.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                if not link.is_symlink():
                    link.symlink_to('/usr/lib/systemd/system/' + args[2])
            if args[1] == "start" and args[2] == adapters._HEADSCALE:
                v.active = True
            if args[1:] == ["start", adapters._TARGET]:
                consumed(v)
            if args[1] == v.fail and phase == "activating":
                raise OSError("synthetic lost native reply")
        return base_run(args, **kwargs)

    io = v.receipts._io
    io.manager = SystemdServiceManager(system=True, run=run)
    io._run = run
    v.admin.nodes = (HeadscaleNode(1, HeadscaleUser(1, "happyranch-node"), "c" * 64, ("100.64.0.10",)),)
    v.admin.list_nodes = lambda **kw: v.admin.nodes
    monkeypatch.setattr(adapters.home_admin, "RootHeadscaleAdminOperations", lambda: v.admin)
    monkeypatch.setattr(adapters.VerifierClient, "resolve", lambda self: 12345)
    v.backend = h.HomeActivationBackend(io=io)
    return v


def assert_locked(v: Venue) -> None:
    fd = os.open(v.path("/var/lib/happyranch-remote-access"), os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(BlockingIOError):
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd)


@contextmanager
def observations(v: Venue) -> Iterator[list[bytes]]:
    """Existing observe-self protocol, repeated for real owner verification reads."""
    path = v.path("/run/happyranch-tsnet-sidecar/observe.sock")
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(f"/proc/self/fd/{parent}/{path.name}")
    path.chmod(0o600)
    listener.listen(8)
    listener.settimeout(0.1)
    stop, requests, errors = threading.Event(), [], []
    def serve() -> None:
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except socket.timeout:
                continue
            with conn:
                try:
                    conn.settimeout(1)
                    request = b""
                    while chunk := conn.recv(128):
                        request += chunk
                    requests.append(request)
                    assert_locked(v)
                    receipt = v.receipts.current()
                    conn.sendall(json.dumps(reply(receipt)).encode())
                except BaseException as error:
                    errors.append(error)
    thread = getattr(v, 'thread_factory', threading.Thread)(target=serve)
    thread.start()
    try:
        yield requests
    finally:
        stop.set()
        thread.join(2)
        listener.close()
        path.unlink()
        os.close(parent)
        assert not thread.is_alive() and not errors


def gate(v: Venue) -> HomeUnitGate:
    return HomeUnitGate(root=v.root, root_owner=v.owner_ids,
        expected_release_uid=v.prepared.release_uid, expected_home_id="home",
        expected_control_plane_id="control", expected_connector_id="connector", expected_generation=7)


def test_prepare_order_and_exact_home_retry(venue: Venue) -> None:
    v = venue
    # Simulate the allowed residual from a crash after rename, before receipt.
    v.path(MEMBER_PATHS[0]).write_bytes(render_headscale_unit(home_transport=True).encode())
    with pytest.raises(HomeUnitGateError):
        gate(v).inspect()
    before = v.path(SIDECAR).read_bytes()
    generation, operation = v.backend._prepare.prepare(expected_generation=7, authenticated=True)
    receipt = v.receipts.read(expected_generation=7)
    assert (generation, operation) == (7, receipt.operation_id)
    assert receipt.phase == "prepared" and len(operation) == 32 and set(operation) <= set("0123456789abcdef")
    assert dict(receipt.member_digests) == {p: hashlib.sha256(v.path(p).read_bytes()).hexdigest() for p in MEMBER_PATHS}
    assert v.path(SIDECAR).read_bytes() == before == v.legacy
    assert v.audit == [(["daemon-reload"], None, None, v.legacy)]
    assert not v.path(OWNED_LINK).exists() and v.transactions.read_optional() is None
    gate(v).inspect()


@pytest.mark.parametrize("case", ["receipt", "txn", "receipt-pending", "txn-pending", "unit-pending",
    "caller-G", "nonroot", "unauthenticated", "running", "link", "foreign-unit", "source",
    "member", "provision", "sidecar-unit"])
def test_prepare_admission(venue: Venue, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    v = venue
    if case == "caller-G":
        generation, operation = v.backend._prepare.prepare(expected_generation=8, authenticated=True)
        prepared = v.receipts.read(expected_generation=8)
        assert (generation, operation) == (8, prepared.operation_id)
        assert prepared.phase == 'prepared' and v.transactions.read_optional() is None
        assert v.path(SIDECAR).read_bytes() == v.legacy
        assert v.audit == [(['daemon-reload'], None, None, v.legacy)]
        return
    if case == "receipt": v.path(ACTIVATION_PATH).write_bytes(v.prepared.to_bytes())
    if case == "txn": v.path(TRANSACTION_PATH).write_bytes(b"{}")
    if case.endswith("pending"):
        path = {"receipt-pending": ACTIVATION_PATH, "txn-pending": TRANSACTION_PATH,
                "unit-pending": MEMBER_PATHS[0]}[case]
        v.path(path).with_name("." + Path(path).name + ".pending").write_bytes(b"uncertain")
    if case == "nonroot": monkeypatch.setattr(h.os, "geteuid", lambda: 1000)
    if case == "running": v.active = True
    if case == "link":
        link = v.path(OWNED_LINK)
        link.parent.mkdir(mode=0o755)
        link.symlink_to(OWNED_LINK_TARGET)
    if case == "foreign-unit": v.path(MEMBER_PATHS[0]).write_bytes(b"foreign")
    if case == "source": v.source.write_bytes(b"synthetic rotation")
    if case == "member": v.path("/etc/happyranch/headscale/policy.hujson").write_bytes(b"foreign")
    if case == "provision": v.path("/etc/happyranch/provision.json").write_bytes(b"{}")
    if case == "sidecar-unit": v.path(SIDECAR).write_bytes(b"foreign")
    before = {p: v.path(p).read_bytes() for p in (MEMBER_PATHS[0], SIDECAR)}
    if case == "sidecar-unit":
        # Prepare NEVER publishes or selects a sidecar; E1a refuses it before intent.
        v.backend._prepare.prepare(expected_generation=7, authenticated=True)
        prepared = v.receipts.read(expected_generation=7)
        coordinator, _, _ = v.backend._compose(prepared)
        with pytest.raises(ActivationRefused): coordinator.run(prepared)
        assert v.transactions.read_optional() is None
    else:
        with pytest.raises(ActivationRefused):
            v.backend._prepare.prepare(expected_generation=7,
                                       authenticated=case != "unauthenticated")
        if case not in ("member", "provision"):
            assert v.path(MEMBER_PATHS[0]).read_bytes() == before[MEMBER_PATHS[0]]
        else:
            # Validation after the only permitted unit mutation leaves drift.
            assert not v.path(ACTIVATION_PATH).exists()
            with pytest.raises(HomeUnitGateError): gate(v).inspect()
    assert v.path(SIDECAR).read_bytes() == before[SIDECAR]
    assert all(call[0] == ["daemon-reload"] for call in v.audit)


def test_prepare_rotation_before_rename_retains_pending(venue: Venue, monkeypatch: pytest.MonkeyPatch) -> None:
    v = venue
    original = os.fsync
    def rotate(descriptor: int) -> None:
        pending = v.path(MEMBER_PATHS[0]).with_name(".happyranch-headscale.service.pending")
        if pending.exists(): v.source.write_bytes(b"synthetic rotated source")
        original(descriptor)
    monkeypatch.setattr(os, "fsync", rotate)
    with pytest.raises(ActivationRefused):
        v.backend._prepare.prepare(expected_generation=7, authenticated=True)
    assert v.path(MEMBER_PATHS[0]).read_bytes() == render_headscale_unit().encode()
    assert v.path(MEMBER_PATHS[0]).with_name(".happyranch-headscale.service.pending").read_bytes() == render_headscale_unit(home_transport=True).encode()
    assert not v.path(ACTIVATION_PATH).exists() and not v.audit


def test_prepare_crash_residual(venue: Venue, monkeypatch: pytest.MonkeyPatch) -> None:
    v = venue
    def crash() -> None: raise OSError("synthetic reload loss")
    monkeypatch.setattr(v.receipts._io.manager, "daemon_reload", crash)
    with pytest.raises(ActivationRefused):
        v.backend._prepare.prepare(expected_generation=7, authenticated=True)
    assert v.path(MEMBER_PATHS[0]).read_bytes() == render_headscale_unit(home_transport=True).encode()
    assert v.path(SIDECAR).read_bytes() == v.legacy
    assert not v.path(ACTIVATION_PATH).exists() and not v.path(TRANSACTION_PATH).exists()
    with pytest.raises(HomeUnitGateError): gate(v).inspect()


def test_backend_enable_and_recover_real_owners(venue: Venue) -> None:
    v = venue
    with observations(v) as requests:
        ready = v.backend.enable(expected_generation=7, authenticated=True)
        assert ready.phase == "ready" and ready.local_node_stable_id == "self-stable"
        assert ready.home_endpoint == "100.64.0.10:443"
        assert v.path(SIDECAR).read_bytes() == v.home
        txn = v.transactions.read_optional()
        assert txn.phase == "joined" and tuple(e.name for e in txn.effects) == EFFECTS
        assert all(e.state == "done" for e in txn.effects)
        assert len(requests) == 4
        # First reload is Prepare (no receipt, legacy sidecar); second is
        # Headscale intent; final reload must follow managed_services intent.
        reloads = [row for row in v.audit if row[0] == ["daemon-reload"]]
        assert reloads[0][1:3] == (None, None)
        assert reloads[-1][1:3] == ("activating", Effect("managed_services", "intent", {}))
        assert reloads[-1][3] == v.home
        assert [row[0] for row in v.audit] == [["daemon-reload"], ["daemon-reload"],
            ["enable", adapters._HEADSCALE], ["start", adapters._HEADSCALE], ["daemon-reload"],
            ["enable", "happyranch-remote-access-policy-renew.timer"],
            ["enable", "happyranch-release-verifier.socket"], ["enable", adapters._TARGET], ["start", adapters._TARGET]]
        v.path(ACTIVATION_PATH).write_bytes(replace(ready, phase="failed_uncertain").to_bytes())
        v.path(TRANSACTION_PATH).write_bytes(replace(txn, phase="failed_uncertain").to_bytes())
        before = list(v.audit)
        policy_before = v.path('/etc/happyranch/policy.json').read_bytes()
        recovered = v.backend.recover(expected_generation=7, expected_operation_id=ready.operation_id,
                                       fresh_authenticated=True)
        assert recovered == ready and v.audit == before and len(requests) == 7
        assert v.path('/etc/happyranch/policy.json').read_bytes() == policy_before


@pytest.mark.parametrize("case", ["enable-auth", "enable-root", "enable-G", "recover-auth", "recover-O", "recover-G", "recover-root"])
def test_backend_admission(venue: Venue, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    v = venue
    if case.startswith("recover"):
        with observations(v):
            ready = v.backend.enable(expected_generation=7, authenticated=True)
            txn = v.transactions.read_optional()
            put(v.path(ACTIVATION_PATH), replace(ready, phase="failed_uncertain").to_bytes())
            put(v.path(TRANSACTION_PATH), replace(txn, phase="failed_uncertain").to_bytes())
            before = v.records(), list(v.audit), list(v.admin.calls)
            if case == "recover-root": monkeypatch.setattr(h.os, "geteuid", lambda: 1000)
            with pytest.raises(ActivationRefused):
                v.backend.recover(expected_generation=8 if case == "recover-G" else 7,
                    expected_operation_id="e" * 32 if case == "recover-O" else ready.operation_id,
                    fresh_authenticated=case != "recover-auth")
            assert (v.records(), v.audit, v.admin.calls) == before
    else:
        composed = []
        if case == "enable-G":
            v.backend._prepare.prepare(expected_generation=7, authenticated=True)
            before = v.records()
            v.audit.clear()
            def forbidden(receipt: HomeActivationReceipt) -> None:
                composed.append(receipt)
                raise AssertionError('stale generation reached composition')
            monkeypatch.setattr(v.backend, '_compose', forbidden)
        if case == "enable-root": monkeypatch.setattr(h.os, "geteuid", lambda: 1000)
        with pytest.raises(ActivationRefused):
            v.backend.enable(expected_generation=8 if case == "enable-G" else 7,
                             authenticated=case != "enable-auth")
        assert not v.audit and not v.path(TRANSACTION_PATH).exists()
        assert composed == []
        if case == "enable-G": assert v.records() == before


@pytest.mark.parametrize("case", ["sidecar-reload", "sidecar-start", "source-after-sidecar"])
def test_post_intent_failure_retains_home_sidecar(venue: Venue, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    v = venue
    original = v.receipts._io.manager.daemon_reload
    def reload() -> None:
        txn = v.transactions.read_optional()
        if txn is not None and txn.effects[-1] == Effect("managed_services", "intent", {}):
            if case == "sidecar-reload": raise OSError("synthetic reload loss")
            if case == "source-after-sidecar": v.source.write_bytes(b"synthetic rotation")
        original()
    monkeypatch.setattr(v.receipts._io.manager, "daemon_reload", reload)
    # Only the target start should fail; Headscale start must complete first.
    if case == "sidecar-start":
        original_start = v.receipts._io.manager.start
        v.fail = None
        def start(name: str, **kw: Any) -> None:
            original_start(name, **kw)
            if name == adapters._TARGET: raise OSError("synthetic start loss")
        monkeypatch.setattr(v.receipts._io.manager, "start", start)
    with pytest.raises(ActivationRefused):
        v.backend.enable(expected_generation=7, authenticated=True)
    assert v.receipts.read(expected_generation=7).phase == "failed_uncertain"
    txn = v.transactions.read_optional()
    assert txn.phase == "failed_uncertain" and txn.effects[-1] == Effect("managed_services", "intent", {})
    assert v.path(SIDECAR).read_bytes() == v.home
    assert not any(row[0][0] in ("stop", "restart", "disable") for row in v.audit)


def test_managed_done_and_joined_require_home_sidecar(venue: Venue) -> None:
    v = venue
    with observations(v):
        ready = v.backend.enable(expected_generation=7, authenticated=True)
        coordinator, _, _ = v.backend._compose(ready)
        before = v.records(), list(v.audit)
        v.path(SIDECAR).write_bytes(v.legacy)
        assert not coordinator._owner.verify("managed_services", ready)
        assert not coordinator._owner.verify("joined", ready)
        assert (v.records(), v.audit) == before
        v.path(SIDECAR).write_bytes(v.home)
        with v.backend._transactions.locked():
            assert coordinator._owner.verify("managed_services", ready)
            assert coordinator._owner.verify("joined", ready)


def test_competing_operation_refuses_without_effect(venue: Venue) -> None:
    v = venue
    with v.transactions.locked():
        with pytest.raises(ActivationRefused):
            v.backend.enable(expected_generation=7, authenticated=True)
    assert not v.audit and not v.path(ACTIVATION_PATH).exists()


@pytest.mark.parametrize("case", ["valid", "dropin", "reload", "foreign"])
def test_legacy_sidecar_preflight_stays_exact(venue: Venue, case: str) -> None:
    v = venue
    v.backend._prepare.prepare(expected_generation=7, authenticated=True)
    prepared = v.receipts.read(expected_generation=7)
    coordinator, _, _ = v.backend._compose(prepared)
    if case == "dropin": v.native.override["DropInPaths"] = "/etc/foreign.conf"
    if case == "reload": v.native.override["NeedDaemonReload"] = "yes"
    if case == "foreign": v.path(SIDECAR).write_bytes(v.legacy + b"\n# foreign")
    assert coordinator._owner.verify("preflight", prepared) is (case == "valid")
    assert v.transactions.read_optional() is None
    assert v.path(SIDECAR).read_bytes() == (v.legacy + b"\n# foreign" if case == "foreign" else v.legacy)
    assert len(v.audit) == 1 and v.audit[0][0] == ["daemon-reload"]


def crash_checkpoint(v: Venue, monkeypatch: pytest.MonkeyPatch, checkpoint: str) -> None:
    """Interrupt real forward work after the selected durable/effect boundary."""
    with monkeypatch.context() as mp:
        publish_txn = HomeActivationTransactionStore.publish
        publish_receipt = HomeActivationStore.publish
        start = v.receipts._io.manager.start

        def transaction(store: HomeActivationTransactionStore, value: Any, **kwargs: Any) -> None:
            publish_txn(store, value, **kwargs)
            if ((checkpoint == "preflight-intent" and value.effects == (Effect("preflight", "intent", {}),))
                    or (checkpoint == "credential-done" and value.effects[-1] == Effect("credential_staging", "done", {}))
                    or (checkpoint == "joined-activating" and value.phase == "joined")):
                raise Crash()

        def receipt(store: HomeActivationStore, value: HomeActivationReceipt, **kwargs: Any) -> None:
            publish_receipt(store, value, **kwargs)
            if checkpoint == "joined-joined" and value.phase == "joined":
                raise Crash()

        def service(name: str, **kwargs: Any) -> None:
            start(name, **kwargs)
            if checkpoint == "headscale-start" and name == adapters._HEADSCALE:
                raise Crash()

        mp.setattr(HomeActivationTransactionStore, "publish", transaction)
        mp.setattr(HomeActivationStore, "publish", receipt)
        mp.setattr(v.receipts._io.manager, "start", service)
        with observations(v), pytest.raises(Crash):
            v.backend.enable(expected_generation=7, authenticated=True)
    txn = v.transactions.read_optional()
    current = v.receipts.read(expected_generation=7)
    expected = {
        "preflight-intent": ("prepared", "activating", Effect("preflight", "intent", {})),
        "headscale-start": ("activating", "activating", Effect("headscale_start_verify", "intent", {})),
        "credential-done": ("activating", "activating", Effect("credential_staging", "done", {})),
        "joined-activating": ("activating", "joined", Effect("joined", "done", {})),
        "joined-joined": ("joined", "joined", Effect("joined", "done", {})),
    }
    assert (current.phase, txn.phase, txn.effects[-1]) == expected[checkpoint]


def forbid_reentry_work(v: Venue, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Count even read-only native/admin calls, including caught refusal errors."""
    calls: list[str] = []

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        calls.append("forbidden")
        raise AssertionError("fresh Enable attempted forward/native/admin/source work")

    monkeypatch.setattr(v.receipts._io, "_run", forbidden)
    monkeypatch.setattr(v.receipts._io.manager, "_run", forbidden)
    monkeypatch.setattr(adapters.home_admin, "mint_local_node", forbidden)
    monkeypatch.setattr(adapters.headscale_service, "preflight_ports", forbidden)
    for name in ("list_users", "create_user", "list_pre_auth_keys", "list_nodes"):
        monkeypatch.setattr(v.admin, name, forbidden)
    monkeypatch.setattr(h.HomeActivationPrepare, "prepare", forbidden)
    monkeypatch.setattr(h.HomeActivationCoordinator, "_source", forbidden)
    monkeypatch.setattr(h.HomeActivationOwner, "verify", forbidden)
    monkeypatch.setattr(h.HomeActivationOwner, "perform", forbidden)
    return calls


def unit_config_bytes(v: Venue) -> dict[str, bytes]:
    return {str(p.relative_to(v.root)): p.read_bytes()
            for directory in ("etc/happyranch", "etc/systemd/system", "etc/credstore")
            for p in v.path("/" + directory).rglob("*") if p.is_file() and not p.is_symlink()}


@pytest.mark.parametrize("checkpoint", ["preflight-intent", "headscale-start", "credential-done",
                                       "joined-activating", "joined-joined"])
@pytest.mark.parametrize("source_valid", [True, False])
def test_backend_crash_reentry_terminal_only(venue: Venue, monkeypatch: pytest.MonkeyPatch,
                                            checkpoint: str, source_valid: bool) -> None:
    v = venue
    crash_checkpoint(v, monkeypatch, checkpoint)
    if not source_valid:
        v.source.write_bytes(b"synthetic rotated source")
    before_units = unit_config_bytes(v)
    before_calls = list(v.audit), list(v.native.calls), list(v.admin.calls)
    calls = forbid_reentry_work(v, monkeypatch)
    published: list[str] = []
    original = HomeActivationStore._publish_record

    def publish(store: HomeActivationStore, path: str, data: bytes, **kwargs: Any) -> None:
        assert_locked(v)
        assert json.loads(data)["phase"] == "failed_uncertain"
        if path == ACTIVATION_PATH:
            assert v.transactions.read_optional().phase == "failed_uncertain"
        original(store, path, data, **kwargs)
        published.append(path)

    monkeypatch.setattr(HomeActivationStore, "_publish_record", publish)
    fresh = h.HomeActivationBackend(io=v.receipts._io)
    with pytest.raises(ActivationRefused):
        fresh.enable(expected_generation=7, authenticated=True)
    assert v.transactions.read_optional().phase == "failed_uncertain"
    assert v.receipts.read(expected_generation=7).phase == "failed_uncertain"
    assert published == [TRANSACTION_PATH, ACTIVATION_PATH]
    terminal = v.records()
    with pytest.raises(ActivationRefused):
        h.HomeActivationBackend(io=v.receipts._io).enable(expected_generation=7, authenticated=True)
    assert v.records() == terminal and published == [TRANSACTION_PATH, ACTIVATION_PATH]
    assert unit_config_bytes(v) == before_units
    assert (v.audit, v.native.calls, v.admin.calls) == before_calls and calls == []


@pytest.mark.parametrize("destination", [TRANSACTION_PATH, ACTIVATION_PATH])
def test_backend_reentry_marker_failure(venue: Venue, monkeypatch: pytest.MonkeyPatch,
                                       destination: str) -> None:
    v = venue
    crash_checkpoint(v, monkeypatch, "headscale-start")
    before = v.records()
    units = unit_config_bytes(v)
    calls = forbid_reentry_work(v, monkeypatch)
    attempts: list[str] = []
    original = HomeActivationStore._publish_record

    def publish(store: HomeActivationStore, path: str, data: bytes, **kwargs: Any) -> None:
        assert_locked(v)
        attempts.append(path)
        if path == destination:
            raise OSError("synthetic still-failing terminal publication")
        original(store, path, data, **kwargs)

    monkeypatch.setattr(HomeActivationStore, "_publish_record", publish)
    for _ in range(2):
        with pytest.raises(ActivationRefused):
            h.HomeActivationBackend(io=v.receipts._io).enable(expected_generation=7, authenticated=True)
        assert v.records()[0] == before[0]
        if destination == TRANSACTION_PATH:
            assert v.records() == before
        else:
            assert v.transactions.read_optional().phase == "failed_uncertain"
            assert v.transactions.read_optional().effects[-1] == Effect("headscale_start_verify", "intent", {})
        assert unit_config_bytes(v) == units and calls == []
    assert attempts == ([TRANSACTION_PATH, TRANSACTION_PATH] if destination == TRANSACTION_PATH
                        else [TRANSACTION_PATH, ACTIVATION_PATH, ACTIVATION_PATH])


@pytest.mark.parametrize("case", ["ready", "failed", "stale-G", "mismatched-G", "mismatched-O",
    "older-failed", "receipt-pending", "txn-pending", "unsafe-receipt", "unknown-txn", "missing-txn"])
def test_backend_existing_pair_refusal(venue: Venue, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    v = venue
    if case == "ready":
        with observations(v):
            v.backend.enable(expected_generation=7, authenticated=True)
    else:
        crash_checkpoint(v, monkeypatch, "headscale-start")
        txn = v.transactions.read_optional()
        receipt = v.receipts.read(expected_generation=7)
        if case == "failed":
            put(v.path(TRANSACTION_PATH), replace(txn, phase="failed_uncertain").to_bytes())
            put(v.path(ACTIVATION_PATH), replace(receipt, phase="failed_uncertain").to_bytes())
        if case == "mismatched-G":
            put(v.path(TRANSACTION_PATH), replace(txn, activation_generation=6).to_bytes())
        if case == "mismatched-O":
            put(v.path(TRANSACTION_PATH), replace(txn, operation_id="e" * 32).to_bytes())
        if case == "older-failed":
            put(v.path(TRANSACTION_PATH), replace(txn, activation_generation=6, phase="failed_uncertain").to_bytes())
            put(v.path(ACTIVATION_PATH), replace(receipt, phase="prepared").to_bytes())
        if case.endswith("pending"):
            path = v.path(ACTIVATION_PATH if case == "receipt-pending" else TRANSACTION_PATH)
            path.with_name("." + path.name + ".pending").write_bytes(b"uncertain")
        if case == "unsafe-receipt":
            v.path(ACTIVATION_PATH).chmod(0o644)
        if case == "unknown-txn":
            v.path(TRANSACTION_PATH).write_bytes(b"{}")
        if case == "missing-txn":
            v.path(TRANSACTION_PATH).unlink()
    before, units = v.records(), unit_config_bytes(v)
    pending = {p.name: p.read_bytes() for p in v.path("/var/lib/happyranch-remote-access").glob(".*.pending")}
    calls = forbid_reentry_work(v, monkeypatch)
    for _ in range(2):
        with pytest.raises(ActivationRefused):
            h.HomeActivationBackend(io=v.receipts._io).enable(
                expected_generation=8 if case == "stale-G" else 7, authenticated=True)
        assert v.records() == before and unit_config_bytes(v) == units and calls == []
        assert {p.name: p.read_bytes() for p in v.path("/var/lib/happyranch-remote-access").glob(".*.pending")} == pending


def test_backend_prepared_without_txn(venue: Venue, monkeypatch: pytest.MonkeyPatch) -> None:
    v = venue
    v.backend._prepare.prepare(expected_generation=7, authenticated=True)
    prepared = v.receipts.read(expected_generation=7)
    assert v.transactions.read_optional() is None

    def prepare(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("existing prepared receipt must not run Prepare")

    monkeypatch.setattr(h.HomeActivationPrepare, "prepare", prepare)
    with observations(v):
        ready = h.HomeActivationBackend(io=v.receipts._io).enable(expected_generation=7, authenticated=True)
    assert ready.phase == "ready"
    assert (ready.activation_generation, ready.operation_id) == (7, prepared.operation_id)


@pytest.mark.parametrize('case', ['ready', 'failed', 'half-published', 'rollover',
                                 'unmarked-joined', 'missing-txn', 'mismatched-operation', 'live-key-no-node'])
def test_private_helper_enable_persists_ready_without_activation_replay(
        venue: Venue, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    v = venue
    lifecycle = h.HelperLifecycleBackend(v.backend._io)
    expected = lifecycle.capture('Enable', v.prepared.release_uid)
    assert json.loads(expected)['generation'] == 0
    with observations(v) as requests:
        assert lifecycle.dispatch('Enable', v.prepared.release_uid, 'enable', expected) == {
            'outcome': 'ready', 'residue': []}
        ready = v.receipts.read(expected_generation=0)
        assert ready.phase == 'ready' and ready.local_node_stable_id == 'self-stable'
        assert ready.operation_id != '0' * 32 and requests
        before = list(v.audit)
        policy_before = v.path('/etc/happyranch/policy.json').read_bytes()
        observed = lifecycle.capture('Enable', v.prepared.release_uid)
        assert lifecycle.dispatch('Enable', v.prepared.release_uid, 'enable', observed, deadline=time.monotonic() + 480)['outcome'] == 'ready'
        assert v.receipts.read(expected_generation=0) == ready and v.audit == before
        assert v.path('/etc/happyranch/policy.json').read_bytes() == policy_before
        if case == 'ready':
            v.source.write_bytes(b'synthetic source-token rotation')
            with pytest.raises(h.EnablePreparationRefused) as caught:
                lifecycle.dispatch('Enable', v.prepared.release_uid, 'enable', observed, deadline=time.monotonic() + 480)
            assert caught.value.category == 'ready-restart-failed-turn-off'
            assert v.receipts.read(expected_generation=0) == ready and v.audit == before
            return

        failed = replace(ready, phase='failed_uncertain')
        txn = replace(v.transactions.read_optional(), phase='failed_uncertain')
        put(v.path(ACTIVATION_PATH), failed.to_bytes())
        put(v.path(TRANSACTION_PATH), txn.to_bytes())
        if case == 'half-published':
            publish = HomeActivationStore._publish_record
            def crash(store: HomeActivationStore, path: str, *args: Any, **kwargs: Any) -> None:
                if path == ACTIVATION_PATH:
                    raise Crash()
                publish(store, path, *args, **kwargs)
            with monkeypatch.context() as patch:
                patch.setattr(HomeActivationStore, '_publish_record', crash)
                with pytest.raises(Crash):
                    v.backend.recover(expected_generation=0, expected_operation_id=ready.operation_id,
                                      fresh_authenticated=True)
            assert v.receipts.read(expected_generation=0) == failed
            txn = v.transactions.read_optional()
            assert txn.phase == 'joined' and txn.recovery_observation is not None
        if case == 'rollover':
            monkeypatch.setattr(v.admin, 'list_pre_auth_keys', lambda **kw: ())
            v.admin.nodes = ()
        if case == 'unmarked-joined':
            put(v.path(TRANSACTION_PATH), replace(txn, phase='joined').to_bytes())
        if case == 'missing-txn':
            v.path(TRANSACTION_PATH).unlink()
        if case == 'mismatched-operation':
            put(v.path(TRANSACTION_PATH), replace(txn, operation_id='e' * 32).to_bytes())
        if case == 'live-key-no-node':
            v.admin.key = replace(v.admin.key, used=False)
            v.admin.nodes = ()

        effects: list[str] = []
        def forbidden(*args: Any, **kwargs: Any) -> None:
            effects.append('replayed')
            raise AssertionError('mounted recovery must not replay lifecycle effects')
        monkeypatch.setattr(h.HomeActivationOwner, 'perform', forbidden)
        monkeypatch.setattr(adapters.home_admin, 'mint_local_node', forbidden)
        monkeypatch.setattr(adapters.headscale_service, 'preflight_ports', forbidden)
        records, units = v.records(), unit_config_bytes(v)
        observed = lifecycle.capture('Enable', v.prepared.release_uid)
        if case == 'live-key-no-node':
            with pytest.raises(h.EnablePreparationRefused) as caught:
                lifecycle.dispatch('Enable', v.prepared.release_uid, 'enable', observed, deadline=time.monotonic() + 480)
            assert caught.value.category == 'activation-retry-later'
            assert v.records() == records
        elif case in ('unmarked-joined', 'missing-txn', 'mismatched-operation'):
            with pytest.raises(ActivationRefused):
                lifecycle.dispatch('Enable', v.prepared.release_uid, 'enable', observed, deadline=time.monotonic() + 480)
            assert v.records() == records
        else:
            phase = 'prepared' if case == 'rollover' else 'ready'
            assert lifecycle.dispatch('Enable', v.prepared.release_uid, 'enable', observed, deadline=time.monotonic() + 480) == {
                'outcome': phase, 'residue': []}
            recovered = v.receipts.read(expected_generation=1 if case == 'rollover' else 0)
            assert recovered.phase == phase
            if case == 'rollover':
                assert recovered.operation_id != ready.operation_id
                assert recovered.local_node_stable_id is None and recovered.home_endpoint is None
                assert v.transactions.read_optional() == txn
            else:
                assert recovered == ready
                assert v.transactions.read_optional().phase == 'joined'
                assert v.transactions.read_optional().recovery_observation is not None
                if case == 'half-published':
                    assert v.transactions.read_optional() == txn
        assert v.audit == before and effects == [] and unit_config_bytes(v) == units


def rollover_after_lost_reply(v: Venue, monkeypatch: pytest.MonkeyPatch) -> HomeActivationReceipt:
    """Real B4 failure and B7; only native reply/admin inventory are simulated."""
    generation = v.receipts.current().activation_generation if v.receipts.current() else 7
    start = v.receipts._io.manager.start
    def lost(name: str, **kwargs: Any) -> None:
        start(name, **kwargs)
        if name == adapters._TARGET:
            raise OSError("synthetic lost target-start reply")
    with monkeypatch.context() as patch:
        patch.setattr(v.receipts._io.manager, "start", lost)
        with observations(v), pytest.raises(ActivationRefused):
            v.backend.enable(expected_generation=generation, authenticated=True)
    failed = v.receipts.read(expected_generation=generation)
    assert failed.phase == "failed_uncertain"
    old = v.transactions.read_optional()
    assert next(e for e in old.effects if e.name == "home_config").state == "done"
    invite = v.path('/etc/happyranch/invite-issuer.json').read_bytes()
    live = unit_config_bytes(v)
    enrollment = v.path('/etc/happyranch/enrollment.key')
    link = v.path('/etc/credstore/happyranch-tsnet-enrollment')
    assert enrollment.is_file() and link.is_symlink()
    with monkeypatch.context() as patch:
        patch.setattr(v.admin, "list_pre_auth_keys", lambda **kw: ())
        nodes = v.admin.nodes
        v.admin.nodes = ()
        prepared = v.backend.recover(expected_generation=generation,
            expected_operation_id=failed.operation_id, fresh_authenticated=True)
        v.admin.nodes = nodes
    assert prepared.phase == "prepared" and prepared.activation_generation == generation + 1
    assert prepared.member_digests == failed.member_digests
    assert v.transactions.read_optional() == old
    assert unit_config_bytes(v) == {key: value for key, value in live.items()
                                   if key != 'etc/happyranch/enrollment.key'}
    assert v.path('/etc/happyranch/invite-issuer.json').read_bytes() == invite
    assert not enrollment.exists() and not link.is_symlink()
    v.admin.key = replace(v.admin.key, used=False)
    return prepared


@pytest.mark.parametrize("double", [False, True])
def test_b7_live_config_rollover_reaches_ready(venue: Venue, monkeypatch: pytest.MonkeyPatch, double: bool) -> None:
    v = venue
    prepared = rollover_after_lost_reply(v, monkeypatch)
    if double:
        # Next generation fails at headscale start, before home_config intent.
        start = v.receipts._io.manager.start
        def lost(name: str, **kwargs: Any) -> None:
            start(name, **kwargs)
            if name == adapters._HEADSCALE:
                raise OSError("synthetic early lost reply")
        with monkeypatch.context() as patch:
            patch.setattr(v.receipts._io.manager, "start", lost)
            with pytest.raises(ActivationRefused):
                v.backend.enable(expected_generation=8, authenticated=True)
        failed = v.receipts.read(expected_generation=8)
        assert failed.phase == "failed_uncertain"
        assert all(e.name != "home_config" for e in v.transactions.read_optional().effects)
        with monkeypatch.context() as patch:
            patch.setattr(v.admin, "list_pre_auth_keys", lambda **kw: ())
            nodes, v.admin.nodes = v.admin.nodes, ()
            prepared = v.backend.recover(expected_generation=8,
                expected_operation_id=failed.operation_id, fresh_authenticated=True)
            v.admin.nodes = nodes
        assert prepared.phase == "prepared" and prepared.activation_generation == 9
    invite = v.path('/etc/happyranch/invite-issuer.json').read_bytes()
    retained = v.transactions.read_optional()
    replacements = []
    publish = HomeActivationTransactionStore._publish_record
    def replace_at_intent(store: Any, path: str, data: bytes, **kwargs: Any) -> None:
        raw = json.loads(data)
        if (path == TRANSACTION_PATH and raw['activation_generation'] == prepared.activation_generation
                and raw['effects'] == [dict(name='preflight', state='intent', member_digests={})]):
            assert store.read_optional() == retained and kwargs['expected'] == retained
            replacements.append(raw['operation_id'])
        publish(store, path, data, **kwargs)
    monkeypatch.setattr(HomeActivationTransactionStore, '_publish_record', replace_at_intent)
    with observations(v):
        ready = h.HomeActivationBackend(io=v.receipts._io).enable(
            expected_generation=prepared.activation_generation, authenticated=True)
    assert ready.phase == "ready" and ready.activation_generation == (9 if double else 8)
    assert ready.home_endpoint == "100.64.0.10:443"
    sidecar = json.loads(v.path('/etc/happyranch/sidecar.json').read_bytes())
    assert sidecar['home_binding']['activation_generation'] == ready.activation_generation
    assert v.path('/etc/happyranch/invite-issuer.json').read_bytes() == invite
    assert ready.member_digests == prepared.member_digests
    assert replacements == [prepared.operation_id]
    txn = v.transactions.read_optional()
    assert txn.matches(ready) and txn.phase == 'joined'
    assert not v.path('/etc/happyranch/enrollment.key').exists()
    assert not v.path('/etc/credstore/happyranch-tsnet-enrollment').is_symlink()


@pytest.mark.parametrize("case", ["same-G", "future-G", "unknown-G", "boolean-G", "negative-G", "float-G", "foreign-tail",
    "strip-mismatch", "connector-mismatch", "old-digest", "old-G", "old-intent", "pending", "custody",
    "activating-receipt", "joined-receipt", "ready-receipt"])
def test_b7_live_config_rollover_refuses_without_effects(
        venue: Venue, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    from runtime.remote_access.home_live_config import HomeLiveConfigOwner
    v = venue
    prepared = rollover_after_lost_reply(v, monkeypatch)
    path = v.path('/etc/happyranch/sidecar.json')
    data = path.read_bytes()
    if case in ("same-G", "future-G", "unknown-G", "boolean-G", "negative-G", "float-G", "foreign-tail"):
        replacement = {"same-G": b'8', "future-G": b'9', "unknown-G": b'null',
                       "boolean-G": b'true', "negative-G": b'-1', "float-G": b'7.0',
                       "foreign-tail": b'7,"foreign":true'}[case]
        path.write_bytes(data.replace(b'"activation_generation":7', b'"activation_generation":' + replacement))
    if case == "strip-mismatch": path.write_bytes(b' ' + data)
    if case == "connector-mismatch":
        connector = v.path('/etc/happyranch/connector.json')
        connector.write_bytes(connector.read_bytes() + b' ')
    if case in ("old-digest", "old-G", "old-intent"):
        txn = v.transactions.read_optional()
        if case == "old-G": txn = replace(txn, activation_generation=6)
        else:
            effect = next(e for e in txn.effects if e.name == 'home_config')
            effects = list(txn.effects)
            if case == "old-digest":
                effects[effects.index(effect)] = replace(effect,
                    member_digests={key: 'a' * 64 for key in effect.member_digests})
            else:
                effects = effects[:effects.index(effect)] + [Effect('home_config', 'intent', {})]
            txn = replace(txn, effects=tuple(effects))
        put(v.path(TRANSACTION_PATH), txn.to_bytes())
    if case == "pending": put(path.with_name('.sidecar.json.pending'), b'synthetic pending')
    if case == "custody": path.chmod(0o666)
    if case.endswith('-receipt'):
        phase = case.split('-')[0]
        prepared = replace(prepared, phase=phase, **(dict(local_node_stable_id='self-stable',
            home_endpoint='100.64.0.10:443') if phase == 'ready' else {}))
        put(v.path(ACTIVATION_PATH), prepared.to_bytes())
    before = {str(p): (p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns)
              for p in v.root.rglob('*') if p.is_file()}
    calls = len(v.native.calls)
    # Observe actual write attempts, including ephemeral pending entries.
    from scripts.test_installer_token_custody import home_unit_writes
    writes = home_unit_writes(monkeypatch, v.root)
    config = HomeLiveConfigOwner(io=v.receipts._io, expected_generation=8,
                                expected_operation_id=prepared.operation_id)
    assert not config.verify('preflight', prepared), case
    assert writes == []
    assert {str(p): (p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns)
            for p in v.root.rglob('*') if p.is_file()} == before
    assert all(call[1] == 'show' for call in v.native.calls[calls:])


@pytest.mark.parametrize('case', ['same-G', 'future-G', 'active-txn', 'same-operation',
    'release_uid', 'home_id', 'control_plane_id', 'connector_id', 'ca_der_sha256', 'dial_ip',
    'activating-receipt', 'joined-receipt', 'ready-receipt', 'matching'])
def test_b7_backend_pair_refuses_without_forward_effects(
        venue: Venue, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    v = venue
    prepared = rollover_after_lost_reply(v, monkeypatch)
    txn = v.transactions.read_optional()
    if case in ('same-G', 'future-G'):
        txn = replace(txn, activation_generation=8 if case == 'same-G' else 9)
    if case == 'active-txn': txn = replace(txn, phase='activating')
    if case == 'same-operation': txn = replace(txn, operation_id=prepared.operation_id)
    if case in h.IDENTITY_KEYS:
        value = {'release_uid': prepared.release_uid + 1, 'ca_der_sha256': 'a' * 64,
                 'dial_ip': '192.168.1.41'}.get(case, 'foreign')
        txn = replace(txn, **{case: value})
    if case.endswith('-receipt'):
        phase = case.split('-')[0]
        prepared = replace(prepared, phase=phase, **(dict(local_node_stable_id='self-stable',
            home_endpoint='100.64.0.10:443') if phase == 'ready' else {}))
        put(v.path(ACTIVATION_PATH), prepared.to_bytes())
    if case == 'matching':
        txn = replace(txn, activation_generation=prepared.activation_generation,
                      operation_id=prepared.operation_id)
    put(v.path(TRANSACTION_PATH), txn.to_bytes())
    before, units = v.records(), unit_config_bytes(v)
    native, admin = list(v.native.calls), list(v.admin.calls)
    calls = forbid_reentry_work(v, monkeypatch)
    from scripts.test_installer_token_custody import home_unit_writes
    writes = home_unit_writes(monkeypatch, v.root)
    with pytest.raises(ActivationRefused):
        v.backend.enable(expected_generation=8, authenticated=True)
    if case == 'matching':
        # Ruling 17 permits only a terminal receipt marker, never forward work.
        assert v.receipts.read(expected_generation=8) == replace(prepared, phase='failed_uncertain')
        assert v.transactions.read_optional() == txn
        # One write-open plus fchown/fchmod on that same pending inode.
        assert writes == ['var/lib/happyranch-remote-access/.home-activation.json.pending'] * 3
    else:
        assert v.records() == before and writes == []
    assert unit_config_bytes(v) == units and calls == []
    assert v.native.calls == native and v.admin.calls == admin


@pytest.mark.parametrize('state', ['inactive', 'failed', 'activating', 'deactivating'])
def test_thr327_disable_transitional_owned_only(composition: Any, state: str) -> None:
    v = composition
    expected = v.lifecycle.capture('Enable', os.getuid())
    assert v.lifecycle.dispatch('Enable', os.getuid(), 'enable', expected,
                                deadline=time.monotonic() + 480)['outcome'] == 'ready'
    v.observers.close()
    v.states[adapters._SIDECAR] = state
    assert v.io.inventory().state == ('manual-install-conflict' if state in ('activating', 'deactivating') else 'owned-activated')
    expected = v.lifecycle.capture('Disable', os.getuid())
    assert v.lifecycle.dispatch('Disable', os.getuid(), 'stop', expected)['outcome'] == 'in_progress'
    assert v.lifecycle.dispatch('Disable', os.getuid(), 'cleanup', expected,
                                revoke_succeeded=True)['outcome'] == 'off'
    assert v.receipts.current() is None


@pytest.mark.parametrize('stopped', [adapters._HEADSCALE, adapters._TARGET, adapters._CONNECTOR, 'both', 'start-limit-hit', 'socket-start-limit-hit', 'headscale-start-limit-hit'])
def test_thr327_ready_ordinary_restart(composition: Any, monkeypatch: pytest.MonkeyPatch, stopped: str) -> None:
    v = composition
    expected = v.lifecycle.capture('Enable', os.getuid())
    assert v.lifecycle.dispatch('Enable', os.getuid(), 'enable', expected,
                                deadline=time.monotonic() + 480)['outcome'] == 'ready'
    ready = v.path(ACTIVATION_PATH).read_bytes()
    txn = v.path(TRANSACTION_PATH).read_bytes()
    calls = len(v.calls)
    if stopped in (adapters._TARGET, 'both'):
        v.observers.close()
        v.observers = ExitStack()
    if stopped == adapters._CONNECTOR: v.states[adapters._CONNECTOR] = 'inactive'
    if stopped in (adapters._HEADSCALE, 'both'): v.states[adapters._HEADSCALE] = 'inactive'
    if stopped in (adapters._TARGET, 'both'):
        for name in (adapters._TARGET, adapters._CONNECTOR, adapters._SIDECAR): v.states[name] = 'inactive'
    failed_unit = (adapters._HEADSCALE if stopped == 'headscale-start-limit-hit' else
                   'happyranch-release-verifier.socket' if stopped == 'socket-start-limit-hit' else adapters._SIDECAR)
    if stopped in ('start-limit-hit', 'socket-start-limit-hit', 'headscale-start-limit-hit'):
        v.states[failed_unit] = 'failed'
        native = v.io.manager._run
        def start_limited(args: list[str], **kw: Any) -> tuple[int, str]:
            if args[:3] == ['systemctl', 'start', failed_unit] and v.states[failed_unit] == 'failed':
                return 1, ''
            return native(args, **kw)
        monkeypatch.setattr(v.io.manager, '_run', start_limited)
    expected = v.lifecycle.capture('Enable', os.getuid())
    assert v.lifecycle.dispatch('Enable', os.getuid(), 'enable', expected,
                                deadline=time.monotonic() + 480)['outcome'] == 'ready'
    assert v.path(ACTIVATION_PATH).read_bytes() == ready and v.path(TRANSACTION_PATH).read_bytes() == txn
    assert len(v.mints) == 1
    if stopped in ('start-limit-hit', 'socket-start-limit-hit', 'headscale-start-limit-hit'):
        assert v.calls[calls:] == [['reset-failed', failed_unit], ['start', failed_unit]]
        assert v.states[failed_unit] == 'active'
    else:
        assert all(call[0] == 'start' for call in v.calls[calls:])
    assert v.states[adapters._HEADSCALE] == v.states[adapters._TARGET] == 'active'


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize(('target_active', 'timer_start'), [
    (False, 'active'), (True, 'active'), (True, 'failed'), (True, 'unverified'),
    (True, 'running'), (True, 'in-flight'), (True, 'renewal-failed'),
    (True, 'timer-failed'), (True, 'foreign'), (True, 'mislinked'),
])
def test_thr327_ready_repairs_policy_unit_and_timer(
        composition: Any, monkeypatch: pytest.MonkeyPatch, legacy: bool,
        target_active: bool, timer_start: str) -> None:
    from runtime.remote_access import policy_renew_units as policy
    v = composition
    expected = v.lifecycle.capture('Enable', os.getuid())
    assert v.lifecycle.dispatch('Enable', os.getuid(), 'enable', expected,
                                deadline=time.monotonic() + 480)['outcome'] == 'ready'
    service = v.path('/usr/lib/systemd/system/' + policy.SERVICE_NAME)
    if legacy: put(service, policy.render_previous_service().encode(), 0o644)
    link = v.path('/etc/systemd/system/happyranch-managed.target.wants/' + policy.TIMER_NAME)
    link.unlink()
    v.states[policy.TIMER_NAME] = 'inactive'
    settled = False
    if timer_start == 'in-flight':
        v.states[policy.TIMER_NAME] = 'active'
        v.states[policy.SERVICE_NAME] = 'activating'
    if not target_active:
        v.observers.close()
        v.observers = ExitStack()
        for name in (adapters._TARGET, adapters._CONNECTOR, adapters._SIDECAR):
            v.states[name] = 'inactive'
    native = v.io.manager._run
    def run(args: list[str], **kwargs: Any) -> tuple[int, str]:
        if args[:3] == ['systemctl', 'start', policy.TIMER_NAME]:
            assert_locked(v)
            if timer_start in ('failed', 'unverified'):
                return (1 if timer_start == 'failed' else 0), ''
        code, output = native(args, **kwargs)
        if args[:3] == ['systemctl', 'start', policy.TIMER_NAME]:
            if timer_start in ('running', 'in-flight', 'renewal-failed'):
                v.states[policy.SERVICE_NAME] = 'failed' if timer_start == 'renewal-failed' else 'activating'
            if timer_start == 'timer-failed':
                v.states[policy.TIMER_NAME] = 'failed'
        if (args[:3] == ['systemctl', 'show', policy.TIMER_NAME]
                and timer_start in ('running', 'in-flight', 'renewal-failed') and not settled):
            output = output.replace('SubState=waiting', 'SubState=running')
        if args[:3] == ['systemctl', 'show', policy.SERVICE_NAME]:
            if v.states.get(policy.SERVICE_NAME) == 'activating':
                output = output.replace('SubState=dead', 'SubState=start')
            if timer_start == 'foreign':
                output = output.replace('Names=' + policy.SERVICE_NAME, 'Names=foreign.service')
        if args[:3] == ['systemctl', 'show', policy.TIMER_NAME] and timer_start == 'mislinked':
            output = output.replace('Unit=' + policy.SERVICE_NAME, 'Unit=foreign.service')
        return code, output
    monkeypatch.setattr(v.io.manager, '_run', run)
    calls = len(v.calls)
    ready, txn = v.path(ACTIVATION_PATH).read_bytes(), v.path(TRANSACTION_PATH).read_bytes()
    expected = v.lifecycle.capture('Enable', os.getuid())
    if timer_start not in ('active', 'running', 'in-flight') and target_active:
        with pytest.raises(h.EnablePreparationRefused) as failure:
            v.lifecycle.dispatch('Enable', os.getuid(), 'enable', expected,
                                 deadline=time.monotonic() + 480)
        assert failure.value.category == 'ready-restart-failed-turn-off'
        assert v.states[policy.TIMER_NAME] == {
            'renewal-failed': 'active', 'timer-failed': 'failed',
        }.get(timer_start, 'inactive')
    else:
        assert v.lifecycle.dispatch('Enable', os.getuid(), 'enable', expected,
                                    deadline=time.monotonic() + 480)['outcome'] == 'ready'
        running = timer_start in ('running', 'in-flight')
        assert v.io.manager.show_properties(policy.TIMER_NAME, ('ActiveState', 'SubState')) == {
            'ActiveState': 'active', 'SubState': 'running' if running else 'waiting'}
        assert v.lifecycle.policy_status(os.getuid()) == {'policy': 'renewing' if running else 'current'}
        assert v.lifecycle.setup_status(os.getuid()) == {'setup': 'ready'}
        if running:
            assert v.calls[calls:].count(['start', policy.TIMER_NAME]) == (0 if timer_start == 'in-flight' else 1)
            # Native observation settles after Enable has released its flock.
            with v.transactions.locked():
                v.states[policy.SERVICE_NAME] = 'inactive'
                settled = True
            assert v.io.manager.show_properties(policy.TIMER_NAME, ('ActiveState', 'SubState')) == {
                'ActiveState': 'active', 'SubState': 'waiting'}
        assert v.lifecycle.policy_status(os.getuid()) == {'policy': 'current'}
        assert v.lifecycle.setup_status(os.getuid()) == {'setup': 'ready'}
        if not target_active:
            assert v.calls[calls:].index(['enable', policy.TIMER_NAME]) < v.calls[calls:].index(['start', adapters._TARGET])
    assert service.read_bytes() == policy.render_service().encode()
    if timer_start in ('foreign', 'mislinked'):
        assert not link.is_symlink()
        assert ['enable', policy.TIMER_NAME] not in v.calls[calls:]
        assert ['start', policy.TIMER_NAME] not in v.calls[calls:]
    else:
        assert link.is_symlink() and link.readlink() == Path('../' + policy.TIMER_NAME)
    assert v.path(ACTIVATION_PATH).read_bytes() == ready and v.path(TRANSACTION_PATH).read_bytes() == txn
    assert len(v.mints) == 1


@pytest.mark.parametrize('minted', [False, True])
def test_thr327_failed_enable_recovery_without_replay(composition: Any, monkeypatch: pytest.MonkeyPatch, minted: bool) -> None:
    v = composition
    perform = adapters.HomeActivationOwner.perform
    def fail(owner: Any, effect: str, receipt: Any, credential: Any) -> None:
        if effect == ('managed_services' if minted else 'db_users'):
            raise OSError('synthetic pre-mint or post-mint interruption')
        perform(owner, effect, receipt, credential)
    with monkeypatch.context() as patch:
        patch.setattr(adapters.HomeActivationOwner, 'perform', fail)
        with pytest.raises(ActivationRefused):
            v.lifecycle.dispatch('Enable', os.getuid(), 'enable', v.lifecycle.capture('Enable', os.getuid()),
                                 deadline=time.monotonic() + 480)
    assert v.receipts.current().phase == 'failed_uncertain'
    if not minted:
        monkeypatch.setattr(v.admin, 'list_pre_auth_keys', lambda **kw: ())
        def absent_window(*args: Any, **kwargs: Any) -> Any:
            raise adapters.home_admin.NodeWindowAbsent('refused')
        monkeypatch.setattr(adapters.home_admin, 'read_local_node_window', absent_window)
    before = len(v.mints), list(v.calls)
    if minted:
        with pytest.raises(h.EnablePreparationRefused) as error:
            v.lifecycle.dispatch('Enable', os.getuid(), 'enable', v.lifecycle.capture('Enable', os.getuid()),
                                 deadline=time.monotonic() + 480)
        assert error.value.category == 'activation-retry-later'
    else:
        assert v.lifecycle.dispatch('Enable', os.getuid(), 'enable', v.lifecycle.capture('Enable', os.getuid()),
                                    deadline=time.monotonic() + 480)['outcome'] == 'prepared'
    assert v.receipts.current().phase == ('failed_uncertain' if minted else 'prepared')
    assert (len(v.mints), v.calls) == before


def test_thr327_failed_revoke_retained_state_has_no_existing_exit(composition: Any) -> None:
    v = composition
    assert v.lifecycle.dispatch('Enable', os.getuid(), 'enable', v.lifecycle.capture('Enable', os.getuid()),
                                deadline=time.monotonic() + 480)['outcome'] == 'ready'
    v.observers.close()
    expected = v.lifecycle.capture('Disable', os.getuid())
    assert v.lifecycle.dispatch('Disable', os.getuid(), 'stop', expected)['outcome'] == 'in_progress'
    with pytest.raises(adapters.headscale_service.HeadscaleServiceError):
        v.lifecycle.dispatch('Disable', os.getuid(), 'cleanup', expected, revoke_succeeded=False)
    before, calls = staged_inventory(v.fixture), list(v.calls)
    for method in ('Enable', 'ReplaceFailed'):
        with pytest.raises(Exception):
            v.lifecycle.capture(method, os.getuid())
    with pytest.raises(Exception):
        v.io.uninstall(purge=True)
    assert staged_inventory(v.fixture) == before and v.calls == calls


@pytest.mark.parametrize('mode', ['timer-release', 'timer-timeout', 'fresh-auth', 'guarded-retry', 'ordinary'])
def test_renewal_activation_lock_contention(composition: Any, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    v = composition
    v.io.provision(release_user=v.fixture.user, data_home=v.fixture.home)
    root = v.path('/var/lib/happyranch-remote-access')
    holder = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    before = staged_inventory(v.fixture)
    invoked = []
    renew = v.io._renew_policy_locked
    def renewal(*, fresh_auth: bool) -> None:
        assert_locked(v)
        invoked.append(fresh_auth)
        renew(fresh_auth=fresh_auth)
    monkeypatch.setattr(v.io, '_renew_policy_locked', renewal)
    def forbidden() -> None:
        pytest.fail('fresh capture callback executed without the activation lock')
    release = threading.Timer(0.5, lambda: fcntl.flock(holder, fcntl.LOCK_UN))
    if mode == 'timer-release':
        release.start()
    started = time.monotonic()
    try:
        if mode == 'timer-release':
            v.io.renew_policy()
        else:
            with pytest.raises(HomeActivationError, match='^state$'):
                if mode == 'ordinary':
                    with v.transactions.locked():
                        pytest.fail('ordinary caller bypassed held lock')
                else:
                    v.io.renew_policy(fresh_auth=mode == 'fresh-auth',
                        _before_renew=forbidden if mode == 'guarded-retry' else None)
        elapsed = time.monotonic() - started
    finally:
        if mode == 'timer-release':
            release.join(timeout=2)
            assert not release.is_alive()
        os.close(holder)
    if mode == 'timer-release':
        assert 0.4 <= elapsed < 3.0 and invoked == [False]
    elif mode == 'timer-timeout':
        assert 19.7 <= elapsed < 23.0 and invoked == []
        assert staged_inventory(v.fixture) == before
    else:
        assert elapsed < 0.3 and invoked == []
        assert staged_inventory(v.fixture) == before


@pytest.mark.parametrize(('name', 'case'), [
    (adapters._SIDECAR, case) for case in ('owned', 'absent', 'MainPID', 'ControlPID', 'ControlGroup', 'Job',
                                        'foreign', 'bytes', 'dropin', 'absent-cgroup')
] + [
    ('happyranch-release-verifier.socket', case) for case in
    ('owned', 'absent', 'ControlPID', 'ControlGroup', 'Job', 'ActiveState', 'SubState', 'foreign', 'bytes', 'dropin', 'absent-cgroup')
] + [(adapters._TARGET, case) for case in ('owned', 'absent', 'Job')])
def test_thr327_failed_unit_reset_proof(composition: Any, monkeypatch: pytest.MonkeyPatch, name: str, case: str) -> None:
    """Real installer proof observes owned bytes and the native reset boundary."""
    v = composition
    expected = v.lifecycle.capture('Enable', os.getuid())
    assert v.lifecycle.dispatch('Enable', os.getuid(), 'enable', expected,
                                deadline=time.monotonic() + 480)['outcome'] == 'ready'
    v.states[name] = v.states['foreign.service'] = 'failed'
    fragment = v.path('/etc/systemd/system/' + name)
    if case.startswith('absent'):
        fragment.unlink()
        v.path('/etc/systemd/system/multi-user.target.wants/' + adapters._TARGET).unlink()
        v.path('/etc/systemd/system/happyranch-managed.target.wants/happyranch-release-verifier.socket').unlink()

    if case == 'bytes': fragment.write_bytes(fragment.read_bytes() + b'# foreign\n')
    if case == 'dropin':
        override = fragment.with_name(name + '.d')
        override.mkdir(mode=0o755)
        put(override / 'foreign.conf', b'[Service]\n', 0o644)
    native = v.io.manager._run
    guards = []
    def run(args: list[str], **kw: Any) -> tuple[int, str]:
        code, output = native(args, **kw)
        if args[:3] == ['systemctl', 'show', name]:
            if case in ('MainPID', 'ControlPID', 'ControlGroup', 'Job'):
                output = output.replace(case + '=' + {'MainPID': '0', 'ControlPID': '0', 'ControlGroup': '', 'Job': ''}[case],
                                        case + '=' + ('42' if case.endswith('PID') else '/live' if case == 'ControlGroup' else '42 start'))
            if '--property=SubState' in args and case in ('ActiveState', 'SubState'):
                output = output.replace(case + '=failed', case + '=' + ('active' if case == 'ActiveState' else 'running'))
            if case == 'foreign': output = output.replace('FragmentPath=' + str(fragment), 'FragmentPath=/foreign.service')
            if case == 'absent-cgroup': output = output.replace('ControlGroup=', 'ControlGroup=/live')
        if args[:3] == ['systemctl', 'reset-failed', name]:
            assert guards and v.receipts.current().phase == 'ready'
        return code, output
    monkeypatch.setattr(v.io.manager, '_run', run)
    calls = len(v.calls)
    from runtime.remote_access.installer import InstallerError, UNITS
    with v.transactions.locked():
        if case in ('owned', 'absent'):
            try:
                v.io._reset_failed_home_units(require=lambda: guards.append(True))
            except InstallerError as error:
                pytest.fail(f'fixed quiescent unit reset refused: {error}')
            assert v.io.manager.show_properties(name, ('ActiveState', 'SubState')) == dict(ActiveState='inactive', SubState='dead')
        else:
            with pytest.raises(InstallerError):
                v.io._reset_failed_home_units(require=lambda: guards.append(True))
    resets = [call for call in v.calls[calls:] if call[0] == 'reset-failed']
    assert resets == ([['reset-failed', name]] if case in ('owned', 'absent') else [])
    assert all(call[1] in UNITS for call in resets)
    assert v.states['foreign.service'] == 'failed'


@pytest.mark.parametrize(('failed_unit', 'reset'), [
    (name, reset) for name in (adapters._SIDECAR, 'happyranch-release-verifier.socket', adapters._HEADSCALE)
    for reset in ('ok', 'error', 'still-failed')
] + [(adapters._HEADSCALE, proof) for proof in (
    'MainPID', 'ControlPID', 'ControlGroup', 'Job', 'Id', 'Names', 'FragmentPath',
    'DropInPaths', 'NeedDaemonReload', 'LoadState', 'SubState',
    'missing-MainPID', 'missing-ControlPID', 'missing-ControlGroup', 'after-Job', 'after-active')])
def test_thr327_failed_restart_turn_off_enable(composition: Any, monkeypatch: pytest.MonkeyPatch, reset: str, failed_unit: str) -> None:
    """Exercise the advice path with a marker retained across verified stop."""
    from runtime.remote_access.installer import UNITS
    v = composition
    expected = v.lifecycle.capture('Enable', os.getuid())
    assert v.lifecycle.dispatch('Enable', os.getuid(), 'enable', expected,
                                deadline=time.monotonic() + 480)['outcome'] == 'ready'
    units = {name: (v.path('/etc/systemd/system/' + name).stat().st_ino,
                   v.path('/etc/systemd/system/' + name).read_bytes()) for name in UNITS}
    v.states[failed_unit] = 'failed'
    native = v.io.manager._run
    def run(args: list[str], **kw: Any) -> tuple[int, str]:
        if args[:3] == ['systemctl', 'start', failed_unit]:
            v.states[failed_unit] = 'failed'
            return 1, ''
        if args[:3] == ['systemctl', 'stop', failed_unit]:
            v.calls.append(args[1:])
            return 0, ''
        if args[:3] == ['systemctl', 'reset-failed', failed_unit] and reset in ('error', 'still-failed'):
            v.calls.append(args[1:])
            return (1 if reset == 'error' else 0), 'synthetic secret diagnostic'
        code, output = native(args, **kw)
        if (args[:3] == ['systemctl', 'show', adapters._HEADSCALE]
                and '--property=Id' in args and '--property=ControlGroup' in args
                and not v.path('/etc/systemd/system/' + adapters._HEADSCALE).exists()):
            # The retained not-found .service still exposes Service properties.
            # Only this final reset proof is varied, leaving prior B6 gates real.
            if reset.startswith('missing-'):
                key = reset.removeprefix('missing-')
                output = '\n'.join(line for line in output.splitlines() if not line.startswith(key + '='))
            elif reset.startswith('after-'):
                if v.states[failed_unit] == 'inactive':
                    key = 'Job' if reset == 'after-Job' else 'ActiveState'
                    output = output.replace(key + '=' + ('' if key == 'Job' else 'inactive'),
                                            key + '=' + ('42 start' if key == 'Job' else 'failed'))
            elif reset not in ('ok', 'error', 'still-failed'):
                values = dict(MainPID='42', ControlPID='42', ControlGroup='/live', Job='42 start',
                    Id='foreign.service', Names=adapters._HEADSCALE + ' foreign.service',
                    FragmentPath='/foreign.service', DropInPaths='/foreign.conf', NeedDaemonReload='yes',
                    LoadState='loaded', SubState='running')
                output = '\n'.join(reset + '=' + values[reset] if line.startswith(reset + '=') else line
                                   for line in output.splitlines())
        return code, output
    monkeypatch.setattr(v.io.manager, '_run', run)
    expected = v.lifecycle.capture('Enable', os.getuid())
    with pytest.raises(h.EnablePreparationRefused) as failure:
        v.lifecycle.dispatch('Enable', os.getuid(), 'enable', expected, deadline=time.monotonic() + 480)
    assert failure.value.category == 'ready-restart-failed-turn-off'
    v.observers.close()
    v.states[failed_unit] = 'failed'
    expected = v.lifecycle.capture('Disable', os.getuid())
    assert v.lifecycle.dispatch('Disable', os.getuid(), 'stop', expected)['outcome'] == 'in_progress'
    calls = len(v.calls)
    result = v.lifecycle.dispatch('Disable', os.getuid(), 'cleanup', expected, revoke_succeeded=True)
    assert result == ({'outcome': 'off', 'residue': []} if reset == 'ok' else
                      {'outcome': 'partial_cleanup', 'residue': ['owned units: reset unconfirmed']})
    assert [call for call in v.calls[calls:] if call[0] == 'reset-failed'] == (
        [['reset-failed', failed_unit]] if reset in ('ok', 'error', 'still-failed', 'after-Job', 'after-active') else [])
    assert {name: (v.path('/etc/systemd/system/' + name).stat().st_ino,
                   v.path('/etc/systemd/system/' + name).read_bytes()) for name in UNITS} == units
    if reset != 'ok':
        assert v.receipts.current() is not None
        assert v.states[failed_unit] == ('inactive' if reset.startswith('after-') else 'failed')
        return
    assert v.io.manager.show_properties(failed_unit, ('ActiveState', 'SubState')) == dict(ActiveState='inactive', SubState='dead')
    assert v.io.inventory().detail == 'post-disable'
    assert v.lifecycle.setup_status(os.getuid()) == {'setup': 'off'}
    # Already-stranded shape stays read-only and named, never repaired by Status.
    v.states[failed_unit] = 'failed'
    status_calls = len(v.calls)
    from runtime.remote_access.helper_generation import host_state_category
    if failed_unit == adapters._HEADSCALE:
        # Immediate post-90ab9044 not-found/failed is still read as Off;
        # it has no receipt-bound Disable/reset admission. The later native
        # error-load/account/CA shape needs separate recovery authority too.
        assert v.lifecycle.setup_status(os.getuid()) == {'setup': 'off'}
        from runtime.remote_access.helper_generation import GenerationUnavailable
        with pytest.raises(GenerationUnavailable) as unavailable:
            v.lifecycle.capture('Disable', os.getuid())
        assert unavailable.value.category == 'not_applicable'
    else:
        try:
            v.lifecycle.setup_status(os.getuid())
        except Exception as error:
            assert host_state_category(error) == 'inventory'
        else:
            pytest.fail('stranded failed unit must not report off')
    assert v.states[failed_unit] == 'failed'
    assert not any(call[0] == 'reset-failed' for call in v.calls[status_calls:])
    v.states[failed_unit] = 'inactive'
    monkeypatch.setattr(v.io.manager, '_run', native)
    expected = v.lifecycle.capture('Enable', os.getuid())
    assert v.lifecycle.dispatch('Enable', os.getuid(), 'enable', expected,
                                deadline=time.monotonic() + 480)['outcome'] == 'ready'
    assert len(v.mints) == 2

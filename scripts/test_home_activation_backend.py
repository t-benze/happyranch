"""E2b in the existing E1b temp-root/fake-native/AF_UNIX observation venue."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import socket
import threading
import time
import ssl
from contextlib import contextmanager, ExitStack
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

import pytest

from runtime.remote_access import home_activation_backend as h, home_activation_owner as adapters
from runtime.remote_access.home_activation import ACTIVATION_PATH, MEMBER_PATHS, OWNED_LINK, OWNED_LINK_TARGET, HomeActivationReceipt, HomeActivationStore
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
                ActiveState=state, SubState='running' if state == 'active' else 'dead', Job='',
                MainPID=str(os.getpid()) if state == 'active' else '0', ControlPID='0', ControlGroup='',
                User='happyranch-headscale' if name == adapters._HEADSCALE else 'happyranch',
                Group='happyranch-headscale' if name == adapters._HEADSCALE else 'happyranch',
                DynamicUser='no', Unit=policy.SERVICE_NAME)
            return 0, '\n'.join(f'{key}={props[key]}' for key in
                (arg.split('=', 1)[1] for arg in args if arg.startswith('--property=')))
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
            if verb == 'stop': v.states[name] = 'inactive'
            if verb == 'start':
                v.states[name] = 'active'
                if name == adapters._HEADSCALE:
                    for relative in ('var/lib/happyranch-headscale', 'run/happyranch-headscale'):
                        v.path('/' + relative).mkdir(mode=0o700, parents=True, exist_ok=True)
                    put(v.path('/var/lib/happyranch-headscale/db.sqlite'), b'synthetic native Headscale DB')
                if name == adapters._TARGET:
                    v.states.update({adapters._CONNECTOR: 'active', adapters._SIDECAR: 'active'})
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
                v.native.override.update(ActiveState="active", SubState="running", MainPID=str(os.getpid()))
            v.native.override.update(ControlPID="0", ControlGroup="", Job="")
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
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
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
def test_private_helper_enable_persists_ready_and_ready_is_inspect_only(
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
        assert lifecycle.dispatch('Enable', v.prepared.release_uid, 'enable', observed)['outcome'] == 'ready'
        assert v.receipts.read(expected_generation=0) == ready and v.audit == before
        assert v.path('/etc/happyranch/policy.json').read_bytes() == policy_before
        if case == 'ready':
            v.source.write_bytes(b'synthetic source-token rotation')
            with pytest.raises(ActivationRefused):
                lifecycle.dispatch('Enable', v.prepared.release_uid, 'enable', observed)
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
        if case in ('unmarked-joined', 'missing-txn', 'mismatched-operation', 'live-key-no-node'):
            with pytest.raises(ActivationRefused):
                lifecycle.dispatch('Enable', v.prepared.release_uid, 'enable', observed)
            assert v.records() == records
        else:
            phase = 'prepared' if case == 'rollover' else 'ready'
            assert lifecycle.dispatch('Enable', v.prepared.release_uid, 'enable', observed) == {
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

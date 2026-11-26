"""F3 checks in the existing temp-root/fake-native activation venue.

Run: PYTHONPATH=$PWD uv run --frozen --python /usr/bin/python3 pytest scripts/test_home_firewall.py -q --tb=short

Four answers (no test-only production seams in any case):
* test_lifecycle_rules [none/untagged/deny/log/owned/inactive]: observable CLI argv, real
  journal/audit bytes, unchanged repeated Enable and Turn off rule preservation;
  regression = ignore a UFW match collision, repeat settled intents, or delete
  by ports alone. The inactive row retains ownership after UFW is disabled so
  Turn off removes the persisted rules. F2 tests inspect only advice;
  existing B6 tests never simulate UFW. The worker uses the distro UFWRule
  matcher; the fake native CLI refuses any colliding add independently.
* test_interrupted_add [resume/cleanup], test_interrupted_helper_enable,
  test_failed_enable_rolls_back: durable
  intent precedes native add; a fresh owner adopts/removes the tagged rule
  after reply loss. Regression = commit ownership only after add or erase the
  plan on failure. B4 crash tests have no external firewall state.
* test_no_firewall_effect [absent/inactive], test_rejected_subnet [wide/public/
  missing/ambiguous]: observable no native command and named journal/Status
  skip. Regression = ignore activity/private-prefix constraints. Selector tests
  do not own firewall effects. test_kernel_prefix additionally observes the
  production netlink decoder, a distinct wire-decoding risk.
* test_legacy_journal: exact old byte roundtrip and recovery transition retaining
  the optional record; regression = require the new field or drop ownership
  during B7. B4 tests predate F3.
* test_status_ports: public V2 roundtrip and native host projection; regression
  = discard/reject the per-port extension. F2 cases carry only aggregate advice.
* test_refused_enable_keeps_rules [unauthenticated/already_ready]: observable
  rules/journal unchanged on backend admission refusal; regression = run cleanup
  before activation admission from an unguarded exception
  handler. Existing auth tests have no firewall side effect to protect.
* test_worker_cli_identity: worker constructs the distro frontend with the real
  CLI path, so UFW's security checks can stat it. Regression = leave Python -c
  argv until mutation. Existing fake frontend never observes startup argv.
* test_lock_unavailable [missing/readonly, active/unverified]: real worker lock-open failure yields
  durable per-port skip, public Status, unchanged rules and successful Turn off;
  next Enable can acquire rules after access returns. Regression = allow UFW
  to create a missing lock or treat EROFS as activation failure. Existing
  absent/inactive rows do not execute the worker or model sandbox lock access.
* test_owned_lock_unavailable: retained ownership refuses successful cleanup
  when the lock becomes unavailable, with durable remove intent. Regression =
  classify every lock failure as a skip. Inactive row retains a usable lock.
* test_packaged_lock_mount: externally consumed systemd shape grants precisely
  the optional UFW lock while retaining hardening. Regression = omit the file
  grant or replace it with a directory. Existing unit cases cover state dirs.
"""
from __future__ import annotations

import builtins
import contextlib
import errno
import io
import json
import os
import stat
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from runtime.remote_access import home_firewall as f, host_firewall, status_v2
from runtime.remote_access.home_activation_backend import HomeActivationBackend
from runtime.remote_access.home_audit import AUDIT_PATH
from runtime.remote_access.home_activation_coordinator import ActivationRefused
from runtime.remote_access.home_activation_txn import HomeActivationTransaction, validate_transaction_transition
from runtime.remote_access.home_firewall_record import COMMENT, PORTS, FirewallRecord
from runtime.remote_access.home_headscale import LANDefaultRoute, LANInterface
from scripts.test_home_activation_backend import composition  # noqa: F401
from scripts.test_home_activation_txn import Crash, Venue, venue  # noqa: F401


class NativeUFW:
    """Execute the shipped worker with distro matching and fake OS/CLI effects."""
    def __init__(self, v: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.syspath_prepend('/usr/lib/python3/dist-packages')
        monkeypatch.setattr(builtins, '_', lambda value: value, raising=False)
        from ufw.common import UFWRule
        from runtime.remote_access import home_activation_backend
        monkeypatch.setattr(home_activation_backend, 'inspect_ufw', lambda **kw: 'clear')
        self.Rule = UFWRule
        self.v, self.rules, self.commands = v, [], []
        self.active, self.locked, self.crash_after_add = True, False, False
        self.lock_error = None
        self.frontend_argv = []
        self.lock_opens = []
        monkeypatch.setattr(f, 'host_firewall', SimpleNamespace(observe_ufw=lambda **kw:
            host_firewall.UFWObservation('clear', 'active' if self.active else 'inactive')))
        monkeypatch.setattr(f.subprocess, 'run', self.run)

    def rule(self, port: int, proto: str, *, comment: str = COMMENT, action: str = 'allow',
             subnet: str = '192.168.1.0/24', log: bool = False) -> Any:
        rule = self.Rule(action, proto, dport=str(port), src=subnet, dst='192.168.1.40', comment=comment)
        if log:
            rule.logtype = 'log'
        rule.normalize()
        return rule

    def lock(self, path: str) -> object:
        assert path == '/run/ufw.lock' and not self.locked
        self.locked = True
        return object()

    def unlock(self, lock: object) -> None:
        assert self.locked
        self.locked = False

    def cli(self, path: str, run_name: str) -> None:
        assert path == '/usr/sbin/ufw' and run_name == '__main__' and self.locked
        args = self.worker_sys.argv
        remove = args[1] == 'delete'
        spec = args[2:] if remove else args[1:]
        assert spec[0:2] == ['allow', 'proto'] and spec[3] == 'from' and spec[5] == 'to'
        assert spec[7] == 'port' and spec[9:] == ['comment', COMMENT]
        port, proto = int(spec[8]), spec[2]
        assert (port, proto) in PORTS and spec[4] == '192.168.1.0/24' and spec[6] == '192.168.1.40'
        txn = self.v.transactions.read_optional()
        index = PORTS.index((port, proto))
        assert txn.firewall.states[index] == ('remove_intent' if remove else 'add_intent')
        audit = json.loads(self.v.path('/var/lib/happyranch-remote-access/home-audit.jsonl').read_text().splitlines()[-1])
        assert audit['stage'] == 'intent' and audit['subject_id'] == 'subject-home-firewall-' + proto
        rule = self.rule(port, proto)
        self.commands.append(tuple(args))
        if remove:
            assert any(self.Rule.match(old, rule) == 0 for old in self.rules)
            self.rules[:] = [old for old in self.rules if self.Rule.match(old, rule) != 0]
        else:
            assert all(self.Rule.match(old, rule) == 1 for old in self.rules), 'CLI would rewrite an existing rule'
            self.rules.append(rule)
            if self.crash_after_add:
                self.crash_after_add = False
                raise Crash()
        raise SystemExit(0)

    def run(self, argv: tuple, **kwargs: Any) -> Any:
        assert argv == ('/usr/bin/python3', '-I', '-c', f._WORKER)
        assert kwargs['cwd'] == '/' and kwargs['env'] == {'LC_ALL': 'C', 'LANG': 'C'}
        self.worker_sys = SimpleNamespace(stdin=io.StringIO(kwargs['input'].decode()), argv=['-c'])
        ui = SimpleNamespace(get_status=lambda: 'Status: active' if self.active else 'Status: inactive',
            backend=SimpleNamespace(get_rules_ipv4=lambda: self.rules, get_rules_ipv6=lambda: []))
        def frontend(dryrun: bool) -> Any:
            self.frontend_argv.append(list(self.worker_sys.argv))
            return ui
        def open_lock(path: str, flags: int) -> int:
            assert path == '/run/ufw.lock' and not flags & os.O_CREAT
            self.lock_opens.append(path)
            if self.lock_error is not None:
                raise OSError(self.lock_error, 'synthetic lock unavailable', path)
            return 123
        modules = {'sys': self.worker_sys,
            'ufw.common': SimpleNamespace(UFWRule=self.Rule),
            'ufw.frontend': SimpleNamespace(UFWFrontend=frontend),
            'ufw.util': SimpleNamespace(create_lock=self.lock, release_lock=self.unlock),
            'os': SimpleNamespace(stat=lambda *a, **kw: SimpleNamespace(st_mode=stat.S_IFREG, st_size=100),
                open=open_lock, close=lambda fd: None,
                fstat=lambda fd: SimpleNamespace(st_mode=stat.S_IFREG, st_uid=0),
                O_WRONLY=os.O_WRONLY, O_NOFOLLOW=os.O_NOFOLLOW, O_CLOEXEC=os.O_CLOEXEC,
                O_NONBLOCK=os.O_NONBLOCK),
            'runpy': SimpleNamespace(run_path=self.cli),
            'gettext': SimpleNamespace(install=lambda domain: None)}
        importing = builtins.__import__
        def isolated_import(name: str, *args: Any, **kw: Any) -> Any:
            return modules[name] if name in modules else importing(name, *args, **kw)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            try:
                exec(f._WORKER, {'__builtins__': {**vars(builtins), '__import__': isolated_import}})
            except SystemExit as result:
                assert result.code in (None, 0)
        return SimpleNamespace(returncode=0, stdout=output.getvalue().encode())


def start(v: Any) -> None:
    expected = v.lifecycle.capture('Enable', os.getuid())
    assert v.lifecycle.dispatch('Enable', os.getuid(), 'enable', expected,
                               deadline=time.monotonic() + 480)['outcome'] == 'ready'


def test_worker_cli_identity(composition: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    native = NativeUFW(composition, monkeypatch)
    start(composition)
    assert native.frontend_argv and all(args == ['/usr/sbin/ufw'] for args in native.frontend_argv)


@pytest.mark.parametrize('lock_error', [errno.ENOENT, errno.EROFS], ids=['missing', 'readonly'])
@pytest.mark.parametrize('availability', ['active', 'unverified'])
def test_lock_unavailable(composition: Any, monkeypatch: pytest.MonkeyPatch, lock_error: int,
                          availability: str) -> None:
    v = composition
    native = NativeUFW(v, monkeypatch)
    native.lock_error = lock_error
    # A missing lock makes even UFW's read-only status command fail in the
    # packaged namespace. Do not rely on an active observation to handle it.
    native_observer = f.host_firewall.observe_ufw
    monkeypatch.setattr(f.host_firewall, 'observe_ufw', lambda **kw:
        host_firewall.UFWObservation('unverified', availability))
    unrelated = native.rule(22, 'tcp', comment='unrelated')
    native.rules.append(unrelated)
    start(v)
    assert native.rules == [unrelated] and native.commands == []
    assert native.lock_opens and native.frontend_argv == []
    assert v.transactions.read_optional().firewall.states == ('skipped_ufw_lock_unavailable',) * 2
    assert set(v.lifecycle.dispatch('Enable', os.getuid(), 'firewall', None)['ports'].values()) == {
        'skipped_ufw_lock_unavailable'}
    before = v.transactions.read_optional().to_bytes(), v.path(str(AUDIT_PATH)).read_bytes()
    start(v)
    assert (v.transactions.read_optional().to_bytes(), v.path(str(AUDIT_PATH)).read_bytes()) == before
    v.observers.close()
    expected = v.lifecycle.capture('Disable', os.getuid())
    v.lifecycle.dispatch('Disable', os.getuid(), 'stop', expected)
    assert v.lifecycle.dispatch('Disable', os.getuid(), 'cleanup', expected, revoke_succeeded=True)['outcome'] == 'off'
    assert native.rules == [unrelated] and native.commands == []
    native.lock_error = None
    monkeypatch.setattr(f.host_firewall, 'observe_ufw', native_observer)
    start(v)
    assert v.transactions.read_optional().firewall.states == ('added',) * 2
    assert len(native.commands) == 2


def test_owned_lock_unavailable(composition: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    v = composition
    native = NativeUFW(v, monkeypatch)
    start(v)
    before = [dict(rule.__dict__) for rule in native.rules]
    native.lock_error = errno.EROFS
    owner = f.HomeLANFirewall(v.transactions)
    with pytest.raises(ValueError):
        owner.cleanup()
    assert [rule.__dict__ for rule in native.rules] == before
    assert v.transactions.read_optional().firewall.states == ('remove_intent', 'added')
    assert set(owner.status(timeout=1).values()) == {'unverified'}


def test_packaged_lock_mount() -> None:
    unit = (Path(__file__).parents[1] / 'packaging/linux/systemd/happyranch-remote-access-helper.service').read_text()
    entries = [line.partition('=')[2] for line in unit.splitlines() if line.startswith('ReadWritePaths=')]
    assert '-/run/ufw.lock' in entries
    assert all(path.lstrip('-') not in ('/run', '/var/run', '/run/') for entry in entries for path in entry.split())
    for directive in ('ProtectSystem=strict', 'ProtectHome=read-only', 'PrivateTmp=yes',
                      'NoNewPrivileges=yes', 'User=root'):
        assert directive in unit.splitlines()


@pytest.mark.parametrize('collision', ['none', 'untagged', 'deny', 'log', 'owned', 'inactive'])
def test_lifecycle_rules(composition: Any, monkeypatch: pytest.MonkeyPatch, collision: str) -> None:
    v = composition
    native = NativeUFW(v, monkeypatch)
    unrelated = native.rule(22, 'tcp', comment='founder unrelated', subnet='10.10.0.0/24')
    native.rules.append(unrelated)
    existing = None
    if collision not in ('none', 'inactive'):
        existing = native.rule(8443, 'tcp', comment=COMMENT if collision == 'owned' else '',
            action='deny' if collision == 'deny' else 'allow', log=collision == 'log')
        native.rules.append(existing)
    snapshot = [dict(rule.__dict__) for rule in native.rules]
    start(v)
    expected = ('preexisting_not_owned' if collision == 'untagged' else
                'blocked_by_existing' if collision in ('deny', 'log') else 'added')
    assert v.transactions.read_optional().firewall.states == (expected, 'added')
    assert [args[args.index('port') + 1] for args in native.commands] == (
        ['8443', '3478'] if collision in ('none', 'inactive') else ['3478'])
    ports = v.lifecycle.dispatch('Enable', os.getuid(), 'firewall', None)['ports']
    assert ports == {'8443/tcp': expected, '3478/udp': 'added'}
    journal = v.transactions.read_optional().to_bytes()
    audit = v.path(str(AUDIT_PATH)).read_bytes()
    commands = list(native.commands)
    start(v)
    assert v.transactions.read_optional().to_bytes() == journal
    assert v.path(str(AUDIT_PATH)).read_bytes() == audit
    assert native.commands == commands
    if collision == 'inactive':
        native.active = False
        start(v)
        assert v.transactions.read_optional().to_bytes() == journal
        assert v.path(str(AUDIT_PATH)).read_bytes() == audit and native.commands == commands
        assert set(v.lifecycle.dispatch('Enable', os.getuid(), 'firewall', None)['ports'].values()) == {'skipped_ufw_inactive'}
    v.observers.close()
    captured = v.lifecycle.capture('Disable', os.getuid())
    assert v.lifecycle.dispatch('Disable', os.getuid(), 'stop', captured)['outcome'] == 'in_progress'
    assert v.lifecycle.dispatch('Disable', os.getuid(), 'cleanup', captured, revoke_succeeded=True)['outcome'] == 'off'
    preserved = snapshot[:1] if collision == 'owned' else snapshot
    assert [rule.__dict__ for rule in native.rules] == preserved
    assert all(args[1] in ('allow', 'delete') for args in native.commands)


@pytest.mark.parametrize('disposition', ['resume', 'cleanup'])
def test_interrupted_add(venue: Venue, monkeypatch: pytest.MonkeyPatch, disposition: str) -> None:
    v = venue
    v.coordinator().run(v.prepared)
    monkeypatch.setattr(f, 'selected_subnet', lambda dial: '192.168.1.0/24')
    native = NativeUFW(v, monkeypatch)
    native.crash_after_add = True
    with pytest.raises(Crash):
        f.HomeLANFirewall(v.transactions).enable()
    assert v.transactions.read_optional().firewall.states == ('add_intent', 'pending')
    assert len(native.rules) == 1
    fresh = f.HomeLANFirewall(type(v.transactions)(root=v.root, root_owner=v.owner_ids))
    if disposition == 'resume':
        fresh.enable()
        assert len(native.rules) == 2 and len(native.commands) == 2
    fresh.cleanup()
    assert native.rules == [] and v.transactions.read_optional().firewall.states == ('removed',) * 2


def test_failed_enable_rolls_back(composition: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    v = composition
    native = NativeUFW(v, monkeypatch)
    original = v.transactions._publish_record
    def fail_done(path: str, raw: bytes, **kwargs: Any) -> None:
        record = json.loads(raw)
        if record.get('firewall', {}).get('states') == ['added', 'pending']:
            raise OSError('synthetic lost journal commit')
        original(path, raw, **kwargs)
    # All store instances share the real publication method at this boundary.
    monkeypatch.setattr(type(v.transactions), '_publish_record', lambda self, path, raw, **kw: fail_done(path, raw, **kw))
    with pytest.raises(ActivationRefused):
        start(v)
    assert native.rules == []
    assert v.transactions.read_optional().firewall.states == ('removed',) * 2


def test_interrupted_helper_enable(composition: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    v = composition
    native = NativeUFW(v, monkeypatch)
    native.crash_after_add = True
    with pytest.raises(Crash):
        start(v)
    assert v.transactions.read_optional().firewall.states == ('add_intent', 'pending')
    with pytest.raises(ActivationRefused):
        start(v)  # Existing Ruling 7 records terminal uncertainty, never replays activation.
    assert v.receipts.current().phase == 'failed_uncertain' and native.rules == []
    start(v)  # Explicit B7 observation recovers joined state, then acquires F3 rules.
    assert len(v.mints) == 1 and len(native.rules) == 2
    v.observers.close()
    expected = v.lifecycle.capture('Disable', os.getuid())
    assert v.lifecycle.dispatch('Disable', os.getuid(), 'stop', expected)['outcome'] == 'in_progress'
    assert v.lifecycle.dispatch('Disable', os.getuid(), 'cleanup', expected, revoke_succeeded=True)['outcome'] == 'off'
    assert native.rules == []


@pytest.mark.parametrize('availability', ['absent', 'inactive'])
def test_no_firewall_effect(venue: Venue, monkeypatch: pytest.MonkeyPatch, availability: str) -> None:
    v = venue
    v.coordinator().run(v.prepared)
    monkeypatch.setattr(f, 'selected_subnet', lambda dial: '192.168.1.0/24')
    monkeypatch.setattr(f, 'host_firewall', SimpleNamespace(observe_ufw=lambda **kw:
        host_firewall.UFWObservation('unknown', availability)))
    monkeypatch.setattr(f, '_ufw', lambda *a, **kw: pytest.fail('no firewall command allowed'))
    owner = f.HomeLANFirewall(v.transactions)
    owner.enable()
    assert set(owner.status(timeout=1).values()) == {'skipped_ufw_' + availability}
    owner.cleanup()


@pytest.mark.parametrize('case', ['wide', 'public', 'missing', 'ambiguous'])
def test_rejected_subnet(venue: Venue, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    v = venue
    v.coordinator().run(v.prepared)
    ip = '8.8.8.8' if case == 'public' else v.prepared.dial_ip
    prefixes = () if case == 'missing' else (8 if case == 'wide' else 24,)
    addresses = (ip, ip) if case == 'ambiguous' else (ip,)
    interfaces = (LANInterface(2, True, False, addresses, prefixes * len(addresses)),)
    monkeypatch.setattr(f.headscale_service, 'collect_lan_inventory', lambda: (interfaces, (LANDefaultRoute(2, ip),)))
    monkeypatch.setattr(f, '_ufw', lambda *a, **kw: pytest.fail('rejected subnet must not execute UFW'))
    owner = f.HomeLANFirewall(v.transactions)
    owner.enable()
    assert set(owner.status(timeout=1).values()) == {'skipped_subnet_rejected'}
    owner.cleanup()


def test_kernel_prefix(composition: Any) -> None:
    interfaces, _ = f.headscale_service.collect_lan_inventory()
    assert interfaces[0].prefix_lengths[0] == 24
    assert f.selected_subnet('192.168.1.40') == '192.168.1.0/24'


def test_legacy_journal() -> None:
    from runtime.remote_access.home_activation_txn import Effect
    txn = HomeActivationTransaction(release_uid=1000, home_id='home', control_plane_id='control',
        connector_id='connector', activation_generation=7, operation_id='b' * 32,
        ca_der_sha256='c' * 64, dial_ip='192.168.1.40', phase='activating',
        effects=(Effect('preflight', 'intent', {}),))
    raw = txn.to_bytes()
    assert b'firewall' not in raw and HomeActivationTransaction.parse(raw).to_bytes() == raw
    updated = replace(txn, firewall=FirewallRecord('192.168.1.0/24', ('pending',) * 2))
    validate_transaction_transition(txn, updated)
    assert HomeActivationTransaction.parse(updated.to_bytes()) == updated


@pytest.mark.parametrize('ports', [
    {'8443/tcp': 'preexisting_not_owned', '3478/udp': 'blocked_by_existing'},
    {'8443/tcp': 'skipped_ufw_lock_unavailable', '3478/udp': 'skipped_ufw_lock_unavailable'},
], ids=['collision', 'lock-unavailable'])
def test_status_ports(ports: dict[str, str]) -> None:
    from importlib.util import module_from_spec, spec_from_file_location
    spec = spec_from_file_location('connection_f3', Path(__file__).parents[1] / 'packaging/linux/desktop/connection_contract.py')
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    value = status_v2.StatusV2(setup=status_v2.SetupObservation(status_v2.SetupPhase.READY, 0),
        firewall=status_v2.FirewallObservation(status_v2.FirewallState.UNVERIFIED, 0, ports))
    raw = status_v2.encode(value)
    assert status_v2.decode(raw) == value
    # Native host Status parser is the public display boundary.
    parser = module.Protocol(Path(__file__).parents[1] / 'protocol/native-connection-bridge.v1.json')
    snapshot = dict(setup='ready', runtime='available', observation='known', devices=[],
        pending=None, selected=None, inviteState='unknown', expiresAt=None)
    assert parser.host_details(snapshot, raw.decode())['firewallPorts'] == ports


@pytest.mark.parametrize('refusal', ['unauthenticated', 'already_ready'])
def test_refused_enable_keeps_rules(composition: Any, monkeypatch: pytest.MonkeyPatch, refusal: str) -> None:
    v = composition
    native = NativeUFW(v, monkeypatch)
    start(v)
    before = v.transactions.read_optional().to_bytes()
    rules = [dict(rule.__dict__) for rule in native.rules]
    with pytest.raises(ActivationRefused):
        HomeActivationBackend(io=v.io).enable(expected_generation=v.receipts.current().activation_generation,
                                              authenticated=refusal == 'already_ready')
    assert [rule.__dict__ for rule in native.rules] == rules
    assert v.transactions.read_optional().to_bytes() == before

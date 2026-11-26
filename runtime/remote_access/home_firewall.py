"""Fixed F3 LAN UFW effects. No caller-controlled commands or other firewall engine."""
from __future__ import annotations

import json
import subprocess
import secrets
import time
from dataclasses import replace
from ipaddress import ip_interface

from runtime.remote_access import headscale_service, host_firewall
from runtime.remote_access.home_activation import HomeActivationError
from runtime.remote_access.home_activation_txn import HomeActivationTransaction, HomeActivationTransactionStore
from runtime.remote_access.home_firewall_record import COMMENT, PORTS, FirewallRecord, valid_subnet
from runtime.remote_access.home_headscale import select_lan_dial_ip
from runtime.remote_access.home_audit import AUDIT_PATH, Component, Event, HomeAudit, Outcome, Phase

# UFW ships these Python modules with /usr/sbin/ufw. Its own rule reader and
# UFWRule.match are authoritative, including comment/log/action collisions.
# Keep its POSIX lock across inspection and CLI execution IN THE SAME PROCESS:
# a separate CLI process would deadlock or introduce a rule-replacement race.
# runpy runs only the fixed distro CLI with fixed-action argv; no shell/API writes.
_WORKER = r'''
import contextlib, errno, gettext, io, json, os, runpy, stat, sys
from ipaddress import ip_address, ip_network
gettext.install('ufw')
from ufw.common import UFWRule
from ufw.frontend import UFWFrontend
from ufw.util import create_lock, release_lock
request = json.load(sys.stdin)
assert request.keys() == {'action', 'subnet', 'dial_ip', 'index'}
action, subnet, dial_ip, index = (request[k] for k in ('action', 'subnet', 'dial_ip', 'index'))
assert action in ('inspect', 'add', 'remove') and type(index) is int and index in (0, 1)
network, address = ip_network(subnet), ip_address(dial_ip)
private = ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', 'fc00::/7')
assert str(network) == subnet and str(address) == dial_ip and address in network and network.prefixlen >= 16
assert any(network.version == ip_network(n).version and network.subnet_of(ip_network(n)) for n in private)
ports = ((8443, 'tcp'), (3478, 'udp'))
# UFW validates the actual CLI path during frontend startup. Python -c's argv
# is not a file; retain all distro security checks against its fixed CLI.
sys.argv = ['/usr/sbin/ufw']
# The optional systemd file bind is absent if the lock did not exist at unit
# start. Never create it: without that bind /run remains read-only. Probe before
# either port can acquire a rule, including when `ufw status` also failed.
try:
    probe = os.open('/run/ufw.lock', os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
except OSError as error:
    if error.errno not in (errno.ENOENT, errno.EROFS, errno.EACCES, errno.EPERM):
        raise
    print(json.dumps({'skipped': 'ufw lock unavailable'}))
    raise SystemExit(0)
try:
    info = os.fstat(probe)
    assert stat.S_ISREG(info.st_mode) and info.st_uid == 0
finally:
    os.close(probe)
lock = create_lock('/run/ufw.lock')
try:
    # Bound the distro reader's inputs before constructing its frontend.
    for path in ('/etc/ufw/user.rules', '/etc/ufw/user6.rules'):
        info = os.stat(path, follow_symlinks=False)
        assert stat.S_ISREG(info.st_mode) and info.st_size <= 1048576
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        ui = UFWFrontend(False)
        active = ui.get_status().splitlines()[0] == 'Status: active'
        states = []
        for port, protocol in ports:
            spec = UFWRule('allow', protocol, dport=str(port), src=subnet, dst=dial_ip,
                           comment='happyranch-home-headscale')
            spec.normalize()
            rules = ui.backend.get_rules_ipv6() if network.version == 6 else ui.backend.get_rules_ipv4()
            matches = [UFWRule.match(rule, spec) for rule in rules]
            # Any conflicting rule vetoes add, even if an exact owned rule also exists.
            state = ('blocked_by_existing' if -1 in matches else 'preexisting_not_owned' if -2 in matches
                     else 'owned' if 0 in matches else 'missing')
            states.append(state)
        mutate = action == 'add' and active and states[index] == 'missing'
        if action == 'remove':
            port, protocol = ports[index]
            spec = UFWRule('allow', protocol, dport=str(port), src=subnet, dst=dial_ip,
                           comment='happyranch-home-headscale')
            spec.normalize()
            rules = ui.backend.get_rules_ipv6() if network.version == 6 else ui.backend.get_rules_ipv4()
            mutate = any(UFWRule.match(rule, spec) == 0 for rule in rules)
        if mutate:
            port, protocol = ports[index]
            sys.argv = ['/usr/sbin/ufw'] + (['delete'] if action == 'remove' else []) + [
                'allow', 'proto', protocol, 'from', subnet, 'to', dial_ip, 'port', str(port),
                'comment', 'happyranch-home-headscale']
            try:
                runpy.run_path('/usr/sbin/ufw', run_name='__main__')
            except SystemExit as result:
                if result.code not in (None, 0):
                    raise RuntimeError('ufw command failed')
    print(json.dumps({'active': active, 'states': states, 'mutated': mutate}))
finally:
    release_lock(lock)
'''


class _UFWLockUnavailable(HomeActivationError):
    """No native effect occurred; retained ownership still requires cleanup."""


def selected_subnet(dial_ip: str) -> str | None:
    """Same selector and exact kernel address/prefix, never interface fallback."""
    try:
        interfaces, routes = headscale_service.collect_lan_inventory()
        if select_lan_dial_ip(interfaces, routes) != dial_ip:
            return None
        chosen = [r for r in routes if r.source_ip == dial_ip]
        if len(chosen) != 1:
            return None
        interface = next(i for i in interfaces if i.index == chosen[0].interface_index)
        if len(interface.addresses) != len(interface.prefix_lengths):
            return None
        prefixes = [p for address, p in zip(interface.addresses, interface.prefix_lengths) if address == dial_ip]
        if len(prefixes) != 1 or type(prefixes[0]) is not int:
            return None
        subnet = str(ip_interface(f'{dial_ip}/{prefixes[0]}').network)
        return subnet if valid_subnet(subnet, dial_ip) else None
    except (ValueError, TypeError, StopIteration, headscale_service.HeadscaleServiceError):
        return None


def _ufw(action: str, subnet: str, dial_ip: str, index: int, *, timeout: float = 8.0) -> dict:
    if action not in ('inspect', 'add', 'remove') or index not in (0, 1) or not valid_subnet(subnet, dial_ip):
        raise HomeActivationError()
    result = subprocess.run(('/usr/bin/python3', '-I', '-c', _WORKER),
        input=json.dumps(dict(action=action, subnet=subnet, dial_ip=dial_ip, index=index)).encode(),
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        env={'LC_ALL': 'C', 'LANG': 'C', 'PATH': '/usr/sbin:/usr/bin:/sbin:/bin'},
        cwd='/', timeout=timeout, check=False)
    if result.returncode != 0 or len(result.stdout) > 1024:
        raise HomeActivationError('firewall')
    value = json.loads(result.stdout)
    if value == {'skipped': 'ufw lock unavailable'}:
        raise _UFWLockUnavailable('firewall')
    if (type(value) is not dict or value.keys() != {'active', 'states', 'mutated'}
            or type(value['active']) is not bool or type(value['mutated']) is not bool
            or type(value['states']) is not list or len(value['states']) != 2
            or any(s not in ('owned', 'missing', 'preexisting_not_owned', 'blocked_by_existing') for s in value['states'])):
        raise HomeActivationError('firewall')
    return value


class HomeLANFirewall:
    def __init__(self, transactions: HomeActivationTransactionStore, *, deadline: float | None = None) -> None:
        self.transactions = transactions
        self.deadline = deadline

    def _budget(self, maximum: float) -> float:
        remaining = maximum if self.deadline is None else min(maximum, self.deadline - time.monotonic())
        if remaining <= 0:
            raise HomeActivationError('firewall')
        return remaining

    def _write(self, txn: HomeActivationTransaction, record: FirewallRecord) -> HomeActivationTransaction:
        updated = replace(txn, firewall=record)
        self.transactions.publish(updated, expected=txn)
        return updated

    def _state(self, txn: HomeActivationTransaction, index: int, state: str) -> HomeActivationTransaction:
        states = list(txn.firewall.states)
        states[index] = state
        record = FirewallRecord(txn.firewall.subnet, tuple(states))
        return txn if record == txn.firewall else self._write(txn, record)

    def enable(self) -> None:
        try:
            self._enable()
        except Exception:
            self.cleanup()
            raise

    def _audit(self, index: int, *, remove: bool) -> tuple:
        suffix = ''.join(secrets.choice('ghjkmnpqrstuvwxyz') for _ in range(12))
        io = self.transactions._io
        audit = HomeAudit(io.root / str(AUDIT_PATH).lstrip('/'), uid=io.root_owner[0], gid=io.root_owner[1],
                          directory_fd=getattr(self.transactions, '_directory', None))
        intent = audit.intent(Event.TURN_OFF if remove else Event.ACTIVATION,
            intent_id='intent-firewall-' + suffix, operation_id='op-firewall-' + suffix,
            subject_id='subject-home-firewall-' + PORTS[index][1],
            component=Component.LIVE_STATE if remove else None,
            phase=None if remove else Phase.JOINED)
        return audit, intent

    def _enable(self) -> None:
        txn = self.transactions.read_optional()
        if txn is None:
            raise HomeActivationError()
        subnet = selected_subnet(txn.dial_ip)
        if txn.firewall is not None and txn.firewall.subnet != subnet:
            self.cleanup()
            txn = self.transactions.read_optional()
        if (txn.firewall is None or txn.firewall.states == ('removed',) * 2
                or txn.firewall.subnet != subnet):
            txn = self._write(txn, FirewallRecord(subnet,
                ('pending',) * 2 if subnet else ('skipped_subnet_rejected',) * 2))
        if txn.firewall.subnet is None:
            return
        observation = host_firewall.observe_ufw(timeout=self._budget(2.0))
        if 'remove_intent' in txn.firewall.states:
            raise HomeActivationError()
        skipped = ('pending', 'skipped_ufw_absent', 'skipped_ufw_inactive', 'skipped_ufw_lock_unavailable')
        settled = (None, None)
        lock_unavailable = False
        if observation.availability in ('absent', 'inactive'):
            # Preserve acquired ownership when UFW is later disabled/removed.
            # Turn off still needs the retained add intent/spec to clean it up.
            if any(state not in skipped for state in txn.firewall.states):
                return
            settled = ('skipped_ufw_' + observation.availability,) * 2
        else:
            try:
                live = _ufw('inspect', txn.firewall.subnet, txn.dial_ip, 0, timeout=self._budget(8.0))
            except _UFWLockUnavailable:
                if any(state not in skipped for state in txn.firewall.states):
                    raise  # Never erase an acquired rule or an uncertain add.
                lock_unavailable = True
                settled = ('skipped_ufw_lock_unavailable',) * 2
            else:
                if live['active'] and any(state in ('added', 'preexisting_not_owned', 'blocked_by_existing')
                                          for state in txn.firewall.states):
                    settled = tuple('added' if state == 'owned' else state for state in live['states'])
        for index in range(2):
            previous = txn.firewall.states[index]
            if previous == settled[index]:
                continue  # Unchanged ready Enable is read-only, including its audit.
            txn = self._state(txn, index, 'add_intent')
            audit, intent = self._audit(index, remove=False)
            try:
                if lock_unavailable:
                    state = 'skipped_ufw_lock_unavailable'
                elif observation.availability in ('absent', 'inactive'):
                    state = 'skipped_ufw_' + observation.availability
                elif observation.availability != 'active':
                    raise HomeActivationError('firewall')
                else:
                    result = _ufw('add', txn.firewall.subnet, txn.dial_ip, index, timeout=self._budget(8.0))
                    state = ('skipped_ufw_inactive' if not result['active'] else
                             'added' if result['mutated'] or result['states'][index] == 'owned'
                             else result['states'][index])
                    if not result['active'] and previous not in skipped:
                        state = previous  # Activity changed after observation; retain ownership.
                    if state == 'missing':
                        raise HomeActivationError('firewall')
                txn = self._state(txn, index, state)
                audit.outcome(intent, Outcome.SUCCEEDED)
            except Exception:
                audit.uncertain(intent)
                raise

    def cleanup(self) -> None:
        txn = self.transactions.read_optional()
        if txn is None or txn.firewall is None or txn.firewall.subnet is None:
            return  # Legacy journals never acquired F3 effects.
        for index in range(2):
            if txn.firewall.states[index] == 'removed':
                continue
            skipped = txn.firewall.states[index] in (
                'skipped_ufw_absent', 'skipped_ufw_inactive', 'skipped_ufw_lock_unavailable', 'pending')
            txn = self._state(txn, index, 'remove_intent')
            audit, intent = self._audit(index, remove=True)
            try:
                if not skipped:
                    _ufw('remove', txn.firewall.subnet, txn.dial_ip, index, timeout=self._budget(8.0))
                    check = _ufw('inspect', txn.firewall.subnet, txn.dial_ip, index, timeout=self._budget(8.0))
                    if check['states'][index] == 'owned':
                        raise HomeActivationError('firewall')
                txn = self._state(txn, index, 'removed')
                audit.outcome(intent, Outcome.SUCCEEDED)
            except Exception:
                audit.uncertain(intent)
                raise

    def status(self, *, timeout: float) -> dict[str, str] | None:
        """Report recorded effects without spending Status's budget on UFW."""
        txn = self.transactions.read_optional()
        if txn is None or txn.firewall is None:
            return None
        states = txn.firewall.states
        return {f'{port}/{proto}': state for (port, proto), state in zip(PORTS, states)}

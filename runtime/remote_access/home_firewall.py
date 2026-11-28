"""Fixed F3 LAN UFW effects. No caller-controlled commands or other firewall engine."""
from __future__ import annotations

import json
import os
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
# The fixed distro CLI runs with its own security checks. Its nested lockf
# unlock drops this process's lock too, so reacquire before fresh verification.
_WORKER = r'''
import contextlib, errno, gettext, io, json, os, runpy, stat, sys, time
from ipaddress import ip_address, ip_network
gettext.install('ufw')
# UFW's msg() binds stdout at import time and writes through its binary buffer.
# Capture that original binding as well as later stdout/stderr lookups, keeping
# normal "Rule added/deleted" messages out of the worker's strict JSON reply.
quiet = io.TextIOWrapper(io.BytesIO(), encoding='utf-8')
with contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
    from ufw.common import UFWRule
    from ufw.frontend import UFWFrontend
    from ufw.util import create_lock, release_lock, hex_encode
request = json.load(sys.stdin)
assert request.keys() == {'action', 'subnet', 'dial_ip', 'index', 'deadline'}
deadline = request['deadline']
def check_deadline():
    if time.monotonic() >= deadline:
        raise TimeoutError('firewall')
check_deadline()
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
    check_deadline()
    # Bound the distro reader's inputs before constructing its frontend.
    for path in ('/etc/ufw/user.rules', '/etc/ufw/user6.rules'):
        info = os.stat(path, follow_symlinks=False)
        assert stat.S_ISREG(info.st_mode) and info.st_size <= 1048576
    with contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
        ui = UFWFrontend(False)
        active = ui.get_status().splitlines()[0] == 'Status: active'
        states = []
        specs = []
        for port, protocol in ports:
            spec = UFWRule('allow', protocol, dport=str(port), src=subnet, dst=dial_ip,
                           comment=hex_encode('happyranch-home-headscale'))
            spec.normalize()
            specs.append(spec)
            rules = ui.backend.get_rules_ipv6() if network.version == 6 else ui.backend.get_rules_ipv4()
            matches = [UFWRule.match(rule, spec) for rule in rules]
            # Any conflicting rule vetoes add, even if an exact owned rule also exists.
            state = ('blocked_by_existing' if -1 in matches else 'preexisting_not_owned' if -2 in matches
                     else 'owned' if 0 in matches else 'missing')
            states.append(state)
        mutate = action == 'add' and active and states[index] == 'missing'
        spec = specs[index]
        if action == 'remove':
            port, protocol = ports[index]
            spec = UFWRule('allow', protocol, dport=str(port), src=subnet, dst=dial_ip,
                           comment=hex_encode('happyranch-home-headscale'))
            spec.normalize()
            rules = ui.backend.get_rules_ipv6() if network.version == 6 else ui.backend.get_rules_ipv4()
            mutate = any(UFWRule.match(rule, spec) == 0 for rule in rules)
        if mutate:
            port, protocol = ports[index]
            sys.argv = ['/usr/sbin/ufw'] + (['delete'] if action == 'remove' else []) + [
                'allow', 'proto', protocol, 'from', subnet, 'to', dial_ip, 'port', str(port),
                'comment', 'happyranch-home-headscale']
            try:
                check_deadline()
                runpy.run_path('/usr/sbin/ufw', run_name='__main__')
            except SystemExit as result:
                if result.code not in (None, 0):
                    raise RuntimeError('ufw command failed')
            # Closing ANY handle on this inode drops our POSIX locks. Close
            # the old handle before reacquiring, then keep the new one through
            # frontend construction and exact persisted-rule comparison.
            release_lock(lock)
            lock = None
            lock = create_lock('/run/ufw.lock')
        if mutate or action == 'remove':
            # A successful CLI exit is not proof of the effect. Read the
            # persisted rules afresh under the same lock and compare the exact
            # encoded spec, independently of collision-prioritized states.
            sys.argv = ['/usr/sbin/ufw']
            check_deadline()
            readback = UFWFrontend(False)
            rules = (readback.backend.get_rules_ipv6() if network.version == 6
                     else readback.backend.get_rules_ipv4())
            present = any(UFWRule.match(rule, spec) == 0 for rule in rules)
            if present != (action == 'add'):
                raise RuntimeError('ufw effect unverified')
    print(json.dumps({'active': active, 'states': states, 'mutated': mutate}))
finally:
    if lock is not None:
        release_lock(lock)
'''

# This supervisor, rather than the killable backend, retains the inherited
# activation-directory flock until every native descendant is dead and reaped.
# Parent death requests cleanup; it must NOT kill the custody holder outright.
# The worker and the stock CLI's children stay in one private process group.
_SUPERVISOR = r'''
import ctypes, json, os, signal, subprocess, sys, time
cancelled = False
def cancel(signum, frame):
    global cancelled
    cancelled = True
signal.signal(signal.SIGTERM, cancel)
libc = ctypes.CDLL(None, use_errno=True)
if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0 or libc.prctl(36, 1, 0, 0, 0) != 0:
    raise RuntimeError('firewall custody')
if os.getppid() != int(sys.argv[2]):
    raise SystemExit(1)
request = json.load(sys.stdin)
deadline = request['deadline']
if cancelled or time.monotonic() >= deadline:
    raise SystemExit(1)
worker = subprocess.Popen(('/usr/bin/python3', '-I', '-c', sys.argv[1]),
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    start_new_session=True, cwd='/', env=dict(os.environ))
try:
    worker.stdin.write(json.dumps(request).encode())
    worker.stdin.close()
    os.set_blocking(worker.stdout.fileno(), False)
    output = b''
    while True:
        remaining = deadline - time.monotonic()
        if cancelled or remaining <= 0:
            raise TimeoutError('firewall')
        try:
            chunk = os.read(worker.stdout.fileno(), 1025)
        except BlockingIOError:
            chunk = b''
        output += chunk
        if len(output) > 1024:
            raise RuntimeError('firewall output')
        # Keep the group leader unreaped until killpg: its PID/PGID cannot be
        # recycled into an unrelated process group while we signal descendants.
        if os.waitid(os.P_PID, worker.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None:
            try:
                output += os.read(worker.stdout.fileno(), 1025)
            except BlockingIOError:
                pass
            break
        time.sleep(min(.01, remaining))
finally:
    # Even a successful CLI may have left descendants. Do not acknowledge any
    # result or release custody before killing and reaping the entire group.
    try:
        os.killpg(worker.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    worker.wait()
    while True:
        try:
            os.waitpid(-1, 0)
        except InterruptedError:
            continue
        except ChildProcessError:
            break
    # All descendants were adopted by this subreaper and have now been reaped.
if cancelled or worker.returncode != 0 or len(output) > 1024 or time.monotonic() >= deadline:
    raise SystemExit(1)
sys.stdout.buffer.write(output)
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


def _ufw(action: str, subnet: str, dial_ip: str, index: int, *, timeout: float = 8.0,
         deadline: float | None = None, custody_fd: int | None = None) -> dict:
    if action not in ('inspect', 'add', 'remove') or index not in (0, 1) or not valid_subnet(subnet, dial_ip):
        raise HomeActivationError()
    deadline = min(time.monotonic() + timeout, deadline) if deadline is not None else time.monotonic() + timeout
    if deadline <= time.monotonic() or (action != 'inspect' and custody_fd is None):
        raise HomeActivationError('firewall')
    process = subprocess.Popen(('/usr/bin/python3', '-I', '-c', _SUPERVISOR, _WORKER, str(os.getpid())),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        pass_fds=() if custody_fd is None else (custody_fd,),
        env={'LC_ALL': 'C', 'LANG': 'C', 'PATH': '/usr/sbin:/usr/bin:/sbin:/bin'},
        cwd='/')
    try:
        output, _ = process.communicate(json.dumps(dict(action=action, subnet=subnet,
            dial_ip=dial_ip, index=index, deadline=deadline)).encode(), timeout=timeout)
    except subprocess.TimeoutExpired:
        process.terminate()
        # The outer helper may kill this backend at its original deadline.
        # The supervisor still owns custody and finishes native group cleanup.
        process.communicate()
        raise HomeActivationError('firewall') from None
    except BaseException:
        process.terminate()
        process.communicate()
        raise
    if process.returncode != 0 or len(output) > 1024:
        raise HomeActivationError('firewall')
    value = json.loads(output)
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

    def _native(self, action: str, txn: HomeActivationTransaction, index: int, *, timeout: float) -> dict:
        directory = getattr(self.transactions, '_directory', None)
        if directory is None and action != 'inspect':
            with self.transactions.locked() as directory:
                return _ufw(action, txn.firewall.subnet, txn.dial_ip, index, timeout=timeout,
                            deadline=self.deadline, custody_fd=directory)
        return _ufw(action, txn.firewall.subnet, txn.dial_ip, index, timeout=timeout,
                    deadline=self.deadline, custody_fd=directory)

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
                live = self._native('inspect', txn, 0, timeout=self._budget(8.0))
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
                    result = self._native('add', txn, index, timeout=self._budget(8.0))
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
            # A skipped/pending port never attempted an effect. Preserve that
            # durable fact until removed commits; do not turn it into an
            # uncertain native removal that would require UFW after a crash.
            if not skipped:
                txn = self._state(txn, index, 'remove_intent')
            audit, intent = self._audit(index, remove=True)
            try:
                if not skipped:
                    self._native('remove', txn, index, timeout=self._budget(8.0))
                    check = self._native('inspect', txn, index, timeout=self._budget(8.0))
                    if check['states'][index] == 'owned':
                        raise HomeActivationError('firewall')
                txn = self._state(txn, index, 'removed')
                audit.outcome(intent, Outcome.SUCCEEDED)
            except Exception:
                audit.uncertain(intent)
                raise

    def status(self, *, timeout: float, live: bool = False) -> dict[str, str] | None:
        """Keep durable effects; optionally compare settled ports with live UFW."""
        txn = self.transactions.read_optional()
        if txn is None or txn.firewall is None:
            return None
        states = txn.firewall.states
        settled = {'added', 'preexisting_not_owned', 'blocked_by_existing'}
        if live and txn.firewall.subnet is not None and any(state in settled for state in states):
            try:
                observed = self._native('inspect', txn, 0, timeout=timeout)
                current = (tuple('added' if state == 'owned' else
                                 'unverified' if state == 'missing' else state
                                 for state in observed['states']) if observed['active']
                           else ('unverified',) * 2)
            except Exception:
                current = ('unverified',) * 2
            # Status never changes ownership, resolves an intent, or repairs.
            states = tuple(current[index] if state in settled else state for index, state in enumerate(states))
        return {f'{port}/{proto}': state for (port, proto), state in zip(PORTS, states)}

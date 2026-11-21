"""Root-only observations/fixtures for manual M1; never imported by production.

No staging, preparation, publication or activation owner is called here.
All lifecycle effects come from the zero-input public system-bus methods.
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
from types import FrameType
from typing import Any, Callable
import pwd

from runtime.remote_access import headscale_service as hs, home_audit, installer as installer_module
from runtime.remote_access import policy_renew_units as renewal
from runtime.remote_access.home_activation import ACTIVATION_PATH, LIVE_MEMBERS, MEMBER_PATHS, HomeActivationStore
from runtime.remote_access.home_activation_txn import TRANSACTION_PATH
from runtime.remote_access.installer import ManagedInstaller, UNITS, managed_units
from runtime.remote_access.systemd_unit import SidecarUnitSpec, render_sidecar_unit

WORK = Path('/var/tmp/happyranch-m1')
HELPER = 'happyranch-remote-access-helper.service'
REMOTE = (*UNITS, hs.UNIT, renewal.TIMER_NAME, renewal.SERVICE_NAME)
MUTATORS = r'member=(?:StartUnit|StopUnit|RestartUnit|TryRestartUnit|ReloadUnit|ReloadOrRestartUnit|KillUnit|EnableUnitFiles|DisableUnitFiles|ReenableUnitFiles|LinkUnitFiles|MaskUnitFiles|UnmaskUnitFiles|SetUnitProperties|ResetFailedUnit|Reload)\b'


def command(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, check=check, timeout=30)


def enable_refusal_logs() -> None:
    """Keep closed diagnostic records only; raw journal/config never leaves RAM."""
    units = (HELPER, *REMOTE)
    stages = ('dispatch|admission|capture|capture-transport|authorization|revalidation|child|'
              'protected-record|protected-parent|inventory|clean-residue|ca-sources|'
              'headscale-quiescence|managed-quiescence|snapshot|headscale-publication|'
              'issuer-receipt|custody-directory|native-command|unknown')
    allowed = re.compile(r'remote-access-enable-refusal origin=(?:helper|child) stage='
                         r'(?:' + stages + r')(?:\.(?:' + stages + r')){0,7}'
                         r' exception=[A-Za-z_][A-Za-z0-9_]{0,63}')
    matched, status, truncated = 0, 'unavailable', False
    try:
        result = command('journalctl', '--quiet', '--no-pager', '--boot', '--lines=160',
            '--output=json', '--output-fields=_SYSTEMD_UNIT,MESSAGE',
            *[argument for unit in units for argument in ('--unit', unit)], check=False)
        if result.returncode == 0:
            raw = result.stdout.encode('utf-8')
            status, truncated = 'ok', len(raw) > 262144
            for line in raw[:262144].decode('utf-8', errors='ignore').splitlines():
                if len(line) > 8192:
                    continue
                try:
                    record = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if type(record) is not dict:
                    continue
                unit, message = record.get('_SYSTEMD_UNIT'), record.get('MESSAGE')
                if (unit in units and type(message) is str and allowed.fullmatch(message)
                        and matched < 32):
                    print(json.dumps(dict(unit=unit, diagnostic=message), sort_keys=True))
                    matched += 1
    except Exception:
        pass  # Collection cannot replace the original acceptance failure.
    print(json.dumps(dict(journal=status, matched=matched, truncated=truncated), sort_keys=True))


def properties(unit: str, names: tuple[str, ...]) -> dict[str, str]:
    raw = command('systemctl', 'show', unit, '--property=' + ','.join(names)).stdout
    return dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)


def diagnostic_command(args: list[str], cap: int) -> str:
    """Five seconds and a byte cap; never return stderr or partial output."""
    with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as child:
        try:
            deadline, raw = time.monotonic() + 5, bytearray()
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise ValueError('unavailable')
                    part = os.read(child.stdout.fileno(), min(65536, cap + 1 - len(raw)))
                    if not part:
                        break
                    raw.extend(part)
                    if len(raw) > cap:
                        raise ValueError('bounded')
            if child.wait(timeout=max(.001, deadline - time.monotonic())):
                raise ValueError('unavailable')
            return raw.decode('utf-8', errors='strict')
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=1)


def audit_logs() -> None:
    # The fixture starts in a fresh WORK directory. Archive cells belong to
    # this invocation; compare intent IDs privately, never export them.
    with (WORK / 'diagnostic-audit-before').open('rb') as stream:
        raw = stream.read(65537)
    assert len(raw) <= 65536
    baseline = json.loads(raw)
    assert type(baseline) is list and len(baseline) <= 2048
    baseline = set(baseline)
    directories = [home_audit.AUDIT_PATH.parent]
    with os.scandir(WORK) as entries:
        for index, entry in enumerate(entries):
            assert index < 64
            if re.fullmatch(r'cell-[0-9]{1,20}', entry.name):
                assert entry.is_dir(follow_symlinks=False) and len(directories) < 5
                directories.append(Path(entry.path) / 'happyranch-remote-access')
    rows, remaining = [], 2 * 1024 * 1024
    for directory in directories:
        assert stat.S_ISDIR(directory.lstat().st_mode)
        for name in ('home-audit.jsonl.1', 'home-audit.jsonl'):
            try:
                fd = os.open(directory / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except FileNotFoundError:
                continue
            try:
                info = os.fstat(fd)
                assert (stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                        and (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (0, 0, 0o600)
                        and info.st_size <= min(remaining, home_audit.MAX_FILE_BYTES))
                with os.fdopen(fd, 'rb', closefd=False) as stream:
                    raw = stream.read(min(remaining, home_audit.MAX_FILE_BYTES) + 1)
                assert len(raw) <= min(remaining, home_audit.MAX_FILE_BYTES)
                remaining -= len(raw)
                for line in raw.splitlines():
                    assert len(line) <= home_audit.MAX_RECORD_BYTES and len(rows) < 4096
                    value = json.loads(line)
                    assert type(value) is dict
                    rows.append(value)
            finally:
                os.close(fd)
    intents = {row['intent_id'] for row in rows if row.get('stage') == 'intent'
               and type(row.get('intent_id')) is str and row['intent_id'] not in baseline}
    projected = []
    for row in rows:
        if row.get('intent_id') not in intents:
            continue
        value = {}
        for key, kind in (('event', home_audit.Event), ('phase', home_audit.Phase),
                          ('component', home_audit.Component), ('stage', home_audit.Stage),
                          ('outcome', home_audit.Outcome)):
            value[key] = None if row.get(key) is None else kind(row[key]).value
        projected.append(value)
    for value in projected[:128]:
        print(json.dumps(value, sort_keys=True))
    print(json.dumps(dict(audit='ok', rows=min(len(projected), 128), truncated=len(projected) > 128)))


def process_logs() -> None:
    pid = int(diagnostic_command(['systemctl', 'show', HELPER, '--property=MainPID', '--value'], 32))
    if pid <= 0:
        print(json.dumps(dict(process_tree='absent')))
        return
    raw = diagnostic_command(['ps', '-eo', 'pid=,ppid=,state=,comm=,wchan=,etime='], 1024 * 1024)
    processes = {}
    for line in raw.splitlines():
        fields = line.split()
        assert len(processes) < 8192 and len(fields) == 6
        assert fields[0].isascii() and fields[0].isdigit() and fields[1].isascii() and fields[1].isdigit()
        assert all(len(value) <= 64 and value.isascii() for value in fields)
        processes[int(fields[0])] = dict(zip(('pid', 'ppid', 'state', 'comm', 'wchan', 'etime'), fields))
    assert pid in processes
    selected, pending = set(), [pid]
    # Include the helper's ancestors and descendants, without reading argv,
    # environment, working directories or descriptors (even for discovery).
    ancestor = pid
    for _ in range(32):
        if ancestor not in processes or ancestor in selected:
            break
        selected.add(ancestor)
        ancestor = int(processes[ancestor]['ppid'])
    truncated = ancestor in processes and ancestor not in selected
    while pending and len(selected) < 256:
        parent = pending.pop()
        for child, value in processes.items():
            if int(value['ppid']) == parent and child not in selected:
                selected.add(child)
                pending.append(child)
                if len(selected) >= 256:
                    break
    for child in sorted(selected):
        print(json.dumps(processes[child], sort_keys=True))
    print(json.dumps(dict(process_tree='ok', rows=len(selected), truncated=truncated or bool(pending))))


def unit_logs() -> None:
    raw = diagnostic_command(['systemctl', 'list-units', '--all', 'happyranch*',
                              '--plain', '--no-legend', '--no-pager'], 65536)
    units = set()
    for line in raw.splitlines():
        name = line.split()[0]
        assert len(name) <= 256 and re.fullmatch(r'happyranch[A-Za-z0-9_.@:-]*', name)
        units.add(name)
        assert len(units) <= 128
    names = ('Id', 'LoadState', 'ActiveState', 'SubState', 'MainPID', 'Result',
             'ExecMainStatus', 'FragmentPath', 'DropInPaths', 'NeedDaemonReload')
    if units:
        raw = diagnostic_command(['systemctl', 'show', *sorted(units), '--property=' + ','.join(names)], 262144)
        # Keep exactly today's nonsecret property list, even if a manager
        # unexpectedly supplies another property.
        for line in raw.splitlines():
            key, separator, value = line.partition('=')
            if separator and key in names:
                print(key + '=' + value)
            elif not line:
                print()
    print(json.dumps(dict(units='ok', rows=len(units))))


def lifecycle_logs() -> None:
    for name, collect in (('audit', audit_logs), ('process_tree', process_logs), ('units', unit_logs)):
        try:
            collect()
        except Exception:
            print(json.dumps({name: 'unavailable'}))


def absent(path: str | Path) -> None:
    assert not os.path.lexists(path), 'unexpected fixed residue'


def fixed_file(path: Path, mode: int, expected: bytes | None = None) -> None:
    value = path.lstat()
    assert stat.S_ISREG(value.st_mode) and value.st_nlink == 1, 'fixed member must be a single-link regular file'
    assert (value.st_uid, value.st_gid, stat.S_IMODE(value.st_mode)) == (0, 0, mode), 'fixed member custody mismatch'
    if expected is not None:
        assert path.read_bytes() == expected, 'fixed member renderer drift'


def units() -> dict[str, str]:
    connector = json.loads(Path('/etc/happyranch/connector.json').read_bytes())
    expected = managed_units('deb', connector_config=connector)
    expected[UNITS[1]] = render_sidecar_unit(SidecarUnitSpec(
        binary_path='/opt/happyranch-desktop/bin/happyranch-tsnet-sidecar', home_transport=True))
    return expected


def fragments() -> None:
    for name, expected in units().items():
        fixed_file(Path('/etc/systemd/system') / name, 0o644, expected.encode())
        assert properties(name, ('Names', 'FragmentPath', 'DropInPaths', 'NeedDaemonReload', 'LoadState')) == {
            'Names': name, 'FragmentPath': '/etc/systemd/system/' + name,
            'DropInPaths': '', 'NeedDaemonReload': 'no', 'LoadState': 'loaded'}, name
    for name, render in ((renewal.TIMER_NAME, renewal.render_timer), (renewal.SERVICE_NAME, renewal.render_service)):
        fixed_file(Path('/usr/lib/systemd/system') / name, 0o644, render().encode())
        assert properties(name, ('FragmentPath', 'DropInPaths', 'NeedDaemonReload', 'LoadState')) == {
            'FragmentPath': '/usr/lib/systemd/system/' + name, 'DropInPaths': '',
            'NeedDaemonReload': 'no', 'LoadState': 'loaded'}, name


def inventory_message(value: object) -> str:
    # Closed owner enums and fixed member names only; never arbitrary filenames.
    fixed = {'/etc/happyranch', '/etc/systemd/system',
             '/var/lib/happyranch-remote-access', '/etc/credstore/happyranch-tsnet-enrollment',
             '/usr/lib/systemd/system/happyranch-remote-access-helper.service'}
    fixed.update('/etc/happyranch/' + name for name in (
        'connector.json', 'sidecar.json', 'policy.json', 'release-daemon.json',
        'provision.json', 'daemon.token', 'enrollment.key', 'install-transaction.json'))
    fixed.update('/' + base + '/' + name for base in (
        'etc/systemd/system', 'run/systemd/system', 'usr/local/lib/systemd/system',
        'usr/lib/systemd/system') for name in REMOTE)
    member = value.paths[0] if value.paths else 'absent'
    if member != 'absent' and member not in fixed:
        member = 'unrecognized-member'
    return f'inventory (state, detail)=({value.state}, {value.detail}); first_member={member}'


def runner_custody() -> None:
    # GitHub's disposable image makes /usr/share writable and supplies default
    # ACLs. This is fixture preparation, never a product custody exception.
    io = ManagedInstaller(layout='deb')
    paths = ('usr/share', 'usr/share/dbus-1', 'usr/share/dbus-1/system-services')
    # Validate every literal parent before changing any directory. Never repair
    # ownership or follow a redirected fixture path.
    for relative in ('', 'usr', *paths):
        info = (io.root / relative).lstat()
        assert stat.S_ISDIR(info.st_mode) and (info.st_uid, info.st_gid) == io.root_owner, \
            'unsafe runner D-Bus directory owner/type: /' + relative
    for relative in paths:
        path = io.root / relative
        print('Runner D-Bus custody before:', '/' + relative,
              f'{stat.S_IMODE(path.lstat().st_mode):04o}', flush=True)
        # Nonrecursive: leave service files and every unrelated image path alone.
        command('setfacl', '--remove-all', '--remove-default', '--', str(path))
        path.chmod(0o755)
        print('Runner D-Bus custody after:', '/' + relative,
              f'{stat.S_IMODE(path.lstat().st_mode):04o}', flush=True)


def ready() -> None:
    assert HomeActivationStore().current().phase == 'ready', 'home activation phase must be ready'
    candidate = ManagedInstaller(layout='deb').inventory()
    assert candidate.state == 'owned-activated', inventory_message(candidate)
    fragments()
    hardening = dict(NoNewPrivileges='yes', ProtectSystem='strict', ProtectHome='yes',
                     PrivateTmp='yes', PrivateDevices='yes', ProtectKernelTunables='yes',
                     ProtectKernelModules='yes', ProtectKernelLogs='no', ProtectControlGroups='yes',
                     RestrictSUIDSGID='yes', RestrictRealtime='yes', LockPersonality='yes',
                     MemoryDenyWriteExecute='yes', CapabilityBoundingSet='', AmbientCapabilities='')
    hardening.update(SystemCallArchitectures='native', UMask='0077')
    for name in UNITS[:2]:
        assert properties(name, tuple(hardening)) == hardening, 'loaded hardening: ' + name
        expected = dict(Type='notify', NotifyAccess='main', ActiveState='active', SubState='running',
                        User='happyranch', Group='happyranch', DynamicUser='no', Result='success',
                        ExecMainStatus='0', WatchdogUSec='30s', Restart='on-failure', RestartUSec='1s',
                        StartLimitBurst='5', StartLimitIntervalUSec='5min')
        assert properties(name, tuple(expected)) == expected, 'notify READY: ' + name
        families = set(properties(name, ('RestrictAddressFamilies',))['RestrictAddressFamilies'].split())
        assert families == {'AF_INET', 'AF_INET6', 'AF_UNIX'} | ({'AF_NETLINK'} if name == UNITS[1] else set()), 'loaded address families: ' + name
        assert int(properties(name, ('MainPID',))['MainPID']) > 0, 'running MainPID required: ' + name
    # Nonsecret retained config signatures; no configuration bytes are exported.
    (WORK / 'retained.json').write_text(json.dumps({name: hashlib.sha256(
        Path('/etc/happyranch', name).read_bytes()).hexdigest()
        for name in ('release-daemon.json', 'policy.json', 'provision.json')}))
    print('READY: protected receipt, exact fragments/loads/hardening and both notify services running')


def health_diagnostics() -> None:
    health = Path('/run/happyranch-tsnet-sidecar/health.json')
    deadline = time.monotonic() + 5
    while not health.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    info = health.lstat()
    account = pwd.getpwnam('happyranch')
    assert (stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 65536
            and stat.S_IMODE(info.st_mode) == 0o644
            and (info.st_uid, info.st_gid) == (account.pw_uid, account.pw_gid)), 'native health.json custody'
    # Empty test-side residue models a killed Go CreateTemp writer. The real
    # health.json remains untouched/unread. Both exist BEFORE public Disable,
    # exercising I5g's stop inventory and I5f's later retirement together.
    temp = health.with_name('.health-123456')
    with temp.open('xb'):
        pass
    temp.chmod(0o600)
    os.chown(temp, account.pw_uid, account.pw_gid, follow_symlinks=False)
    print('I5f/I5g pre-stop evidence: native health.json observed; synthetic numeric temp present; contents unread')


def off() -> None:
    io = ManagedInstaller(layout='deb')
    candidate = io.inventory()
    assert candidate.detail == 'post-disable', inventory_message(candidate)
    fragments()
    for unit in REMOTE:
        value = properties(unit, ('ActiveState', 'MainPID', 'ControlPID', 'Job', 'UnitFileState'))
        assert value['ActiveState'] in ('inactive', 'failed') and value.get('MainPID', '0') == '0', 'post-Off state/MainPID: ' + unit
        assert value.get('ControlPID', '0') == '0' and not value.get('Job', ''), unit
        assert value['UnitFileState'] in ('disabled', 'static', ''), unit
    # All B6 fixed-file members, including the public CA/config copies and
    # activation evidence, must be absent after a successful revoke and Off.
    removed = {*LIVE_MEMBERS, *MEMBER_PATHS, ACTIVATION_PATH, TRANSACTION_PATH,
               '/var/lib/happyranch-remote-access/' + hs.PUBLICATION,
               '/var/lib/happyranch-remote-access/' + hs.RECEIPT,
               '/var/lib/happyranch-remote-access/home-issuer-publication.json',
               '/var/lib/happyranch-remote-access/home-issuer',
               '/var/lib/happyranch-remote-access/home-minter',
               '/var/lib/happyranch-remote-access/home-ca',
               '/' + hs.EMPTY_CA,
               '/etc/credstore/happyranch-tsnet-enrollment', '/etc/happyranch/enrollment.key',
               '/run/happyranch-release-verifier/control.sock',
               '/etc/systemd/system/multi-user.target.wants/happyranch-managed.target',
               '/etc/systemd/system/happyranch-managed.target.wants/happyranch-headscale.service',
               '/etc/systemd/system/happyranch-managed.target.wants/happyranch-release-verifier.socket',
               '/etc/systemd/system/happyranch-managed.target.wants/' + renewal.TIMER_NAME,
               '/run/happyranch-tsnet-sidecar',
               '/var/lib/happyranch-connector', '/var/lib/happyranch-tsnet-sidecar'}
    for relative in (*hs.CONFIG_FILES, *hs.LEAF_FILES, 'policy.hujson'):
        removed.add('/etc/happyranch/headscale/' + relative)
    for relative in hs.CUSTODY:
        removed.add('/' + relative)
    for directory, names in (
        ('connector', ('trust-state.json', 'trust-state.json.anchor', 'trust-state.json.lock')),
        ('tsnet-sidecar', ('tailscaled.state', 'credential.consumed', 'tailscaled.log.conf',
                          'tailscaled.log1.txt', 'tailscaled.log2.txt'))):
        for name in names:
            removed.add('/var/lib/happyranch-' + directory + '/' + name)
    for path in sorted(removed):
        absent(path)
    retained = json.loads((WORK / 'retained.json').read_text())
    for name, digest in retained.items():
        assert hashlib.sha256(Path('/etc/happyranch', name).read_bytes()).hexdigest() == digest, 'retained release/policy/provision bytes changed'
    fixed_file(Path('/etc/happyranch/release-daemon.json'), 0o644)
    fixed_file(Path('/etc/happyranch/provision.json'), 0o600)
    try:
        pwd.getpwnam(hs.ACCOUNT)
    except KeyError:
        pass
    else:
        raise AssertionError('B6-created Headscale account retained after successful revoke')
    print('OFF: exact inert five fragments retained, not active/enabled; successful-revoke B6 members and ALL sidecar diagnostics absent')


def inventory() -> dict[str, tuple]:
    # Byte/inode/metadata comparison stays in memory, including all protected
    # state and all systemd load roots. No contents or digests are logged.
    result = {}
    roots = ('/etc/happyranch', '/etc/credstore', '/var/lib/happyranch-remote-access',
             '/var/lib/happyranch-headscale', '/var/lib/happyranch-connector',
             '/var/lib/happyranch-tsnet-sidecar', '/run/happyranch-headscale',
             '/run/happyranch-connector', '/run/happyranch-tsnet-sidecar',
             '/run/happyranch-release-verifier', '/etc/systemd/system',
             '/run/systemd/system', '/usr/local/lib/systemd/system', '/usr/lib/systemd/system')
    def visit(path: Path) -> None:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            result[str(path)] = ('absent',)
            return
        data = (path.read_bytes() if stat.S_ISREG(metadata.st_mode) else
                os.readlink(path) if stat.S_ISLNK(metadata.st_mode) else None)
        result[str(path)] = (metadata.st_dev, metadata.st_ino, metadata.st_uid, metadata.st_gid,
                             metadata.st_mode, metadata.st_nlink, metadata.st_size,
                             metadata.st_mtime_ns, metadata.st_ctime_ns, data)
        if stat.S_ISDIR(metadata.st_mode):
            for child in sorted(path.iterdir()):
                visit(child)
    for root in roots:
        visit(Path(root))
    return result


def uninstall_refusal() -> None:
    off()
    before = inventory()
    states = {name: properties(name, ('ActiveState', 'SubState', 'MainPID', 'ControlPID', 'UnitFileState',
                                      'ActiveEnterTimestampMonotonic', 'InactiveEnterTimestampMonotonic')) for name in REMOTE}
    # Observe actual native manager mutation calls, not merely final states.
    monitor = subprocess.Popen(['dbus-monitor', '--system',
        "type='method_call',destination='org.freedesktop.systemd1'"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        time.sleep(.2)
        assert monitor.poll() is None, 'system bus monitor unavailable'
        result = command('/opt/happyranch-desktop/python/bin/python3', '-I', '-B', '-m',
                         'runtime.remote_access.installer', 'uninstall', '--layout', 'deb', '--purge', check=False)
        time.sleep(.2)
    finally:
        monitor.terminate()
        observed, errors = monitor.communicate(timeout=5)
    assert not errors and b'member=NameAcquired' in observed and b'member=NameLost' in observed, \
        'native bus monitor did not establish monitoring'
    assert result.returncode != 0 and not result.stdout, 'uninstall must refuse with nonzero exit and empty stdout'
    assert result.stderr.strip() == 'error: home control binding not provisioned; requires slice 3b-2', 'uninstall must return the fixed home-profile deferral'
    assert inventory() == before, 'uninstall refusal changed inventory'
    assert states == {name: properties(name, tuple(states[name])) for name in REMOTE}, 'uninstall refusal changed native unit state'
    assert not re.search(MUTATORS, observed.decode(errors='replace')), 'service mutation during uninstall refusal'
    print('UNINSTALL DEFERRED: fixed nonzero refusal; unchanged byte/inode inventory; no manager mutation')


def archive_off() -> None:
    off()
    # Independent synthetic cells, never an uninstall implementation. Every
    # retained object remains in root-only storage until runner disposal.
    archive = WORK / ('cell-' + str(time.monotonic_ns()))
    archive.mkdir(mode=0o700)
    for path in [Path('/etc/happyranch'), Path('/var/lib/happyranch-remote-access'),
                 *(Path('/etc/systemd/system', name) for name in UNITS)]:
        shutil.move(str(path), str(archive / path.name))
    Path('/var/lib/happyranch-remote-access').mkdir(mode=0o700)
    command('systemctl', 'daemon-reload')
    candidate = ManagedInstaller(layout='deb').inventory()
    assert candidate.state == 'clean', inventory_message(candidate)
    print('Independent fixture cell: proven Off retained files archived root-only (no product cleanup claim)')


def failed_fixture() -> None:
    candidate = ManagedInstaller(layout='deb').inventory()
    assert candidate.state == 'clean', inventory_message(candidate)
    # Recognized incomplete manual install: exact legacy fragments but no
    # operator JSON, enrollment input or pre-staging calls. Real starts fail.
    for name, value in managed_units('deb').items():
        path = Path('/etc/systemd/system', name)
        with path.open('xb') as stream:
            stream.write(value.encode())
        path.chmod(0o644)
    command('systemctl', 'daemon-reload')
    command('systemctl', 'start', UNITS[2], check=False)
    candidate = ManagedInstaller(layout='deb').replacement_inventory()
    assert candidate.installation.state == 'known-failed-or-partial', inventory_message(candidate.installation)
    assert candidate.inventory_generation is not None, 'replacement inventory generation required; ' + inventory_message(candidate.installation)
    print('FAILED-manual fixture: protected replacement inventory recognized exact incomplete fragments')


def replacement_off() -> None:
    candidate = ManagedInstaller(layout='deb').inventory()
    assert candidate.detail == 'staged-inert', inventory_message(candidate)
    print('ReplaceFailed closure: staged-inert Off; next Enable owns all home preparation')


def replacement_progress() -> None:
    candidate = ManagedInstaller(layout='deb').replacement_inventory()
    assert candidate.installation.state == 'known-failed-or-partial' and candidate.journal_generation, 'replacement journal generation required; ' + inventory_message(candidate.installation)
    marker = WORK / 'replacement-phase'
    phase = candidate.installation.detail
    assert not marker.exists() or marker.read_text() != phase, 'replacement made no progress'
    marker.write_text(phase)
    print('ReplaceFailed requires next fresh authenticated bounded phase:', phase)


def interrupt(pid: int) -> None:
    # Observe the actual bundled child before provisioning completes. Stop it
    # at the final provision publication; never inject a production flag/call.
    deadline = time.monotonic() + 60
    # Kernel notifications minimize the scheduling race at final provision
    # rename; the independently checked state below remains authoritative.
    libc = ctypes.CDLL(None, use_errno=True)
    fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
    assert fd >= 0, 'inotify unavailable'
    watch = False
    assert libc.inotify_add_watch(fd, b'/etc', 0x00000100 | 0x00000080) >= 0, 'inotify watch failed: /etc'
    child = None
    print('ARMED', flush=True)
    try:
        while time.monotonic() < deadline:
            if not watch and Path('/etc/happyranch').is_dir():
                assert libc.inotify_add_watch(fd, b'/etc/happyranch', 0x00000080) >= 0, 'inotify watch failed: /etc/happyranch'
                watch = True
            try:
                os.read(fd, 65536)  # Names only; never read configuration contents here.
            except BlockingIOError:
                pass
            if not pid:
                pid = int(properties(HELPER, ('MainPID',))['MainPID'])
            child = None
            for entry in Path('/proc').iterdir():
                if not entry.name.isdecimal():
                    continue
                try:
                    parent = int((entry / 'stat').read_text().rsplit(')', 1)[1].split()[1])
                    argv = (entry / 'cmdline').read_bytes().split(b'\0')
                except (OSError, ValueError):
                    continue
                if parent == pid and b'runtime.remote_access.home_activation_backend' in argv:
                    child = int(entry.name)
            if not (child and Path('/etc/happyranch/provision.json').exists()):
                time.sleep(.001)
                continue
            handle = os.pidfd_open(child)
            try:
                signal.pidfd_send_signal(handle, signal.SIGSTOP)
            finally:
                os.close(handle)
            # The case is specifically BETWEEN completed steps. Refuse a
            # pending/torn/advanced boundary instead of treating it as coverage.
            io = ManagedInstaller(layout='deb')
            candidate = io.inventory()
            assert candidate.state == 'modern' and candidate.detail == 'staged-inert', 'missed provision boundary; ' + inventory_message(candidate)
            absent('/var/lib/happyranch-remote-access/' + hs.PUBLICATION)
            absent('/etc/happyranch/invite-issuer.json')
            absent(ACTIVATION_PATH)
            command('systemctl', 'kill', '--signal=SIGKILL', '--kill-whom=all', HELPER)
            print('Interrupted after public Enable provisioning; before Headscale/invite/activation')
            return
    finally:
        os.close(fd)
    raise AssertionError('did not observe a completed provisioning boundary')


def policy_fault() -> None:
    command('systemctl', 'stop', renewal.TIMER_NAME)
    command('systemctl', 'stop', renewal.SERVICE_NAME)
    assert properties(renewal.TIMER_NAME, ('ActiveState',))['ActiveState'] == 'inactive', 'policy renewal timer must be inactive'
    ready()
    policy = Path('/etc/happyranch/policy.json')
    (WORK / 'policy-before').write_bytes(policy.read_bytes())
    (WORK / 'provision-before').write_bytes(Path('/etc/happyranch/provision.json').read_bytes())
    (WORK / 'audit-before').write_text(json.dumps([row['intent_id'] for row in audit_records()]))
    (WORK / 'serving-pids').write_text(json.dumps({name: process_identity(int(
        properties(name, ('MainPID',))['MainPID'])) for name in UNITS[:2]}))
    policy.chmod(0o600)  # Expected protected policy mode is 0640.


def policy_refused() -> None:
    assert Path('/etc/happyranch/policy.json').read_bytes() == (WORK / 'policy-before').read_bytes(), 'failed renewal changed policy bytes'
    assert stat.S_IMODE(Path('/etc/happyranch/policy.json').stat().st_mode) == 0o600, 'failed renewal changed policy fixture mode'
    assert Path('/etc/happyranch/provision.json').read_bytes() == (WORK / 'provision-before').read_bytes(), 'failed renewal changed provision bytes'
    absent('/etc/happyranch/install-transaction.json')
    previous = set(json.loads((WORK / 'audit-before').read_text()))
    rows = audit_records()
    intents = {row['intent_id'] for row in rows if row['event'] == 'policy_renew'
               and row['stage'] == 'intent' and row['intent_id'] not in previous}
    terminal = [row for row in rows if row['intent_id'] in intents and row['stage'] != 'intent']
    assert len(intents) == 1 and len(terminal) == 1 and terminal[0]['outcome'] in ('refused', 'uncertain'), \
        'missing attributable failed renewal settlement'
    print('RetryPolicyRenewal: failed custody renewal settled in B16 without extending issuance/expiry')


def audit_records() -> list[dict]:
    rows = []
    for path in (home_audit.AUDIT_PATH.with_name('home-audit.jsonl.1'), home_audit.AUDIT_PATH):
        if not path.exists():
            continue
        fixed_file(path, 0o600)
        assert path.stat().st_size <= home_audit.MAX_FILE_BYTES, 'audit size bound'
        rows.extend(json.loads(line) for line in path.read_bytes().splitlines())
    return rows


def policy_closed() -> None:
    deadline = time.monotonic() + 45
    serving = json.loads((WORK / 'serving-pids').read_text())
    assert all(isinstance(value, list) and len(value) == 2 for value in serving.values()), 'missing original serving process identity'
    while time.monotonic() < deadline:
        sidecar = properties(UNITS[1], ('MainPID', 'ActiveState'))
        connector = properties(UNITS[0], ('ActiveState', 'SubState'))
        old_gone = all(process_identity(value[0]) != value for value in serving.values())
        # Restart=on-failure may already have a new connector waiting for
        # readiness. Require the original serving processes gone, sidecar gone,
        # no READY connector and an actually closed identity ingress socket.
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(.2)
            refused = probe.connect_ex('/run/happyranch-connector/ingress.sock') in (errno.ENOENT, errno.ECONNREFUSED)
        if (old_gone and sidecar['MainPID'] == '0' and sidecar['ActiveState'] in ('inactive', 'failed')
                and connector != {'ActiveState': 'active', 'SubState': 'running'} and refused):
            print('Policy expiry: both original serving processes gone, external sidecar stopped, identity ingress closed')
            return
        time.sleep(.2)
    raise AssertionError('expired cached policy did not close access')


def process_identity(pid: int) -> list | None:
    if pid <= 0:
        return None
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return None if fields[0] == 'Z' else [pid, fields[19]]
    except FileNotFoundError:
        return None


def clock_expire() -> None:
    # Disposable-runner wall clock only; never edit issued_at, TTL or digests.
    assert not (WORK / 'clock-before').exists(), 'clock fixture already has restoration evidence'
    before = {'wall': time.time(), 'mono': time.monotonic(),
              'ntp': command('timedatectl', 'show', '--property=NTP', '--value').stdout.strip()}
    (WORK / 'clock-before').write_text(json.dumps(before))
    command('timedatectl', 'set-ntp', 'false')
    command('date', '--set=@' + str(int(before['wall'] + 7200)))
    policy = json.loads((WORK / 'policy-before').read_bytes())
    assert time.time() > datetime.fromisoformat(policy['issued_at']).timestamp() + policy['max_age_seconds'], 'disposable clock did not cross cached policy expiry'
    print('Disposable clock advanced beyond unchanged cached-policy expiry')


def clock_restore() -> None:
    marker = WORK / 'clock-before'
    if not marker.exists():
        return
    before = json.loads(marker.read_text())
    command('date', '--set=@' + str(before['wall'] + time.monotonic() - before['mono']))
    command('timedatectl', 'set-ntp', 'true' if before['ntp'] == 'yes' else 'false')
    marker.unlink()
    print('Disposable clock restored using original elapsed monotonic time')


def policy_renewed() -> None:
    before = json.loads((WORK / 'policy-before').read_bytes())
    after = json.loads(Path('/etc/happyranch/policy.json').read_bytes())
    assert after['revision'] > before['revision'] and after['issued_at'] != before['issued_at'], 'successful renewal must advance revision and issuance'
    absent('/etc/happyranch/install-transaction.json')
    candidate = ManagedInstaller(layout='deb').inventory()
    assert candidate.state == 'owned-activated', inventory_message(candidate)
    print('Public RetryPolicyRenewal restored the protected policy after custody/clock restoration')


def recovered_ready() -> None:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if all(properties(name, ('ActiveState', 'SubState')) == {
                'ActiveState': 'active', 'SubState': 'running'} for name in UNITS[:2]):
            ready()
            return
        time.sleep(.5)
    raise AssertionError('normal systemd supervision did not recover READY before final public Disable')


def main() -> None:
    assert os.geteuid() == 0, 'M1 collector requires euid=0'
    assert os.environ.get('GITHUB_ACTIONS') == 'true', f'GITHUB_ACTIONS present={"GITHUB_ACTIONS" in os.environ}; equals true={os.environ.get("GITHUB_ACTIONS") == "true"}'
    assert os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted', f'RUNNER_ENVIRONMENT present={"RUNNER_ENVIRONMENT" in os.environ}; equals github-hosted={os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"}'
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action')
    parser.add_argument('pid', nargs='?', type=int)
    args = parser.parse_args()
    actions = {'runner-custody': runner_custody,
               'enable-refusal-logs': enable_refusal_logs,
               'lifecycle-logs': lifecycle_logs,
               'ready': ready, 'off': off, 'uninstall-refusal': uninstall_refusal,
               'health-diagnostics': health_diagnostics,
               'archive-off': archive_off, 'failed-fixture': failed_fixture,
               'replacement-off': replacement_off, 'replacement-progress': replacement_progress,
               'policy-fault': policy_fault, 'policy-refused': policy_refused, 'policy-closed': policy_closed,
               'clock-expire': clock_expire, 'clock-restore': clock_restore,
               'policy-renewed': policy_renewed, 'recovered-ready': recovered_ready,
               'policy-restore': lambda: Path('/etc/happyranch/policy.json').chmod(0o640),
               'clean': lambda: assert_clean(), 'interrupted': lambda: assert_staged(),
               'interrupt': lambda: interrupt(args.pid)}
    assert args.action in actions, 'unsupported fixed M1 action'
    actions[args.action]()


def clean_path_metadata(io: ManagedInstaller) -> None:
    # Pre-Enable metadata only: never read configuration or directory contents
    # as bytes. Include literal ancestors because the first inventory member is
    # not necessarily the directory that the cleanup-links walk rejects.
    paths = {'etc', 'etc/happyranch', 'lib'}
    paths.update((*installer_module._HEADSCALE_UNIT_DIRS,
                  *installer_module._HEADSCALE_DBUS_DIRS, 'lib/systemd/system'))
    for relative in tuple(paths):
        paths.update(str(parent) for parent in Path(relative).parents if str(parent) != '.')
    config = io.root / 'etc/happyranch'
    if config.is_dir() and not config.is_symlink():
        paths.update(str(path.relative_to(io.root)) for path in config.iterdir())
    getfacl = shutil.which('getfacl')
    print('M1 clean path metadata: uid/gid and octal mode include setgid/sticky; contents unread')
    for relative in ('', *sorted(paths)):
        path = io.root / relative
        record: dict[str, Any] = {'path': '/' + relative}
        try:
            info = path.lstat()
        except OSError as error:
            record['errno'] = error.errno
        else:
            record.update(uid=info.st_uid, gid=info.st_gid,
                          mode=f'{stat.S_IMODE(info.st_mode):04o}', type=stat.S_IFMT(info.st_mode))
            if getfacl and not stat.S_ISLNK(info.st_mode):
                acl = command(getfacl, '--numeric', '--omit-header', '--absolute-names',
                              '--physical', '--', str(path), check=False)
                record.update(acl_exit=acl.returncode, acl=acl.stdout[:16384],
                              acl_truncated=len(acl.stdout) > 16384)
            else:
                record['acl'] = 'symlink-not-followed' if getfacl else 'getfacl-unavailable'
        print(json.dumps(record, sort_keys=True))


def assert_clean() -> None:
    io = ManagedInstaller(layout='deb')
    clean_path_metadata(io)
    # Observe trusted source locations and only the directory predicate's fixed
    # error/metadata. Never emit arbitrary exception text, config or native output.
    origin = ''
    inventory_code = ManagedInstaller._inventory.__code__
    directory_code = ManagedInstaller._check_directory.__code__

    def trace_inventory(frame: FrameType, event: str, argument: Any) -> Callable | None:
        nonlocal origin
        if frame.f_code is directory_code:
            if event == 'exception' and isinstance(argument[1], installer_module.InstallerError):
                values = frame.f_locals
                path = values['path']
                # Only this exact fixed directory error is exported. The
                # descriptor identifies nested cleanup entries whose source
                # label is merely "systemd config inventory".
                if str(argument[1]) == 'unsafe directory ownership or mode: ' + path:
                    info = values['info']
                    actual = Path(os.readlink('/proc/self/fd/' + str(values['descriptor'])))
                    relative = actual.relative_to(io.root)
                    owner, modes = values['owner'], values['allowed_modes']
                    print('M1 inventory directory refusal:', json.dumps(dict(
                        message='unsafe directory ownership or mode: ' + ('/' if path == str(io.root) else path),
                        path='/' + (str(relative) if str(relative) != '.' else ''),
                        uid=info.st_uid, gid=info.st_gid, mode=f'{stat.S_IMODE(info.st_mode):04o}',
                        expected_owner=owner, allowed_modes=[f'{mode:04o}' for mode in modes],
                        owner_mismatch=owner is not None and (info.st_uid, info.st_gid) != owner,
                        group_or_other_write=bool(info.st_mode & 0o022),
                        mode_not_allowed=bool(values['expected'] and stat.S_IMODE(info.st_mode) not in modes)),
                        sort_keys=True))
            return trace_inventory
        if frame.f_code is not inventory_code:
            return None
        if event == 'exception':
            locations = []
            traceback = argument[2]
            for _ in range(8):
                if traceback is None:
                    break
                code = traceback.tb_frame.f_code
                if code.co_filename == inventory_code.co_filename:
                    locations.append(f'{code.co_name}:{traceback.tb_lineno}')
                traceback = traceback.tb_next
            origin = type(argument[1]).__name__ + ' ' + ' > '.join(locations)
        return trace_inventory

    previous_trace = sys.gettrace()
    try:
        sys.settrace(trace_inventory)
        candidate = io.inventory()
    finally:
        sys.settrace(previous_trace)
    if candidate.state != 'clean':
        print('M1 inventory refusal origin:', origin or 'unavailable')
    assert candidate.state == 'clean', inventory_message(candidate)
    absent('/etc/happyranch/invite-issuer.json')
    absent(ACTIVATION_PATH)
    print('Clean package: no home config, activation, enrollment or managed fragments')
    # Private correlation baseline for this disposable run, never uploaded.
    try:
        baseline = json.dumps([row['intent_id'] for row in audit_records()])
        assert len(baseline.encode()) <= 65536
        (WORK / 'diagnostic-audit-before').write_text(baseline)
    except Exception:
        pass  # Diagnostic preparation cannot change the acceptance result.


def assert_staged() -> None:
    candidate = ManagedInstaller(layout='deb').inventory()
    assert candidate.detail == 'staged-inert', inventory_message(candidate)
    absent(ACTIVATION_PATH)
    print('Interrupted complete generated staging is resumable by the next public Enable')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Never emit private config/native stdout/tracebacks from this fixture.
        print('M1 check failed:', type(error).__name__, str(error) if isinstance(error, AssertionError) else '')
        raise SystemExit(1) from None

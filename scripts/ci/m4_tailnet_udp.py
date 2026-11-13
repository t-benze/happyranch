#!/usr/bin/env python3
"""Test-owned home UDP condition. Root, authorized disposable Ubuntu VM only."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import time

ROOT = Path('/var/lib/happyranch-m4')
STATE = ROOT / 'cell/udp-window.json'
TABLE = 'happyranch_m4_udp'
UNIT = 'happyranch-tsnet-sidecar.service'
PROBE_UNIT = 'happyranch-m4-udp-probe.service'
PROBE_USER = 'happyranch-m4-udp'
NS = 'happyranch-m4-udp'
LINK = 'm4udp0'
PEER = 'm4udp1'


def command(*argv: str, data: str | None = None) -> str:
    result = subprocess.run(argv, input=data, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, 'UDP condition operation refused'
    assert len(result.stdout) < 1_000_000 and not result.stderr, 'UDP condition output refused'
    return result.stdout


def unit_identity(unit: str) -> dict:
    raw = command('systemctl', 'show', unit, '-p', 'ActiveState,User,ControlGroup,MainPID,InvocationID')
    value = dict(line.split('=', 1) for line in raw.splitlines())
    assert value['ActiveState'] == 'active' and re.fullmatch(r'[1-9][0-9]*', value['MainPID'])
    assert value['ControlGroup'] == '/system.slice/' + unit, 'unexpected cgroup layout'
    assert re.fullmatch(r'[0-9a-f]{32}', value['InvocationID'])
    uid = pwd.getpwnam(value['User']).pw_uid
    assert uid > 0, 'non-root service identity required'
    group = Path('/sys/fs/cgroup') / value['ControlGroup'].lstrip('/')
    assert Path('/sys/fs/cgroup/cgroup.controllers').is_file(), 'cgroup v2 required'
    return dict(uid=uid, cgroup_id=group.stat().st_ino, group=str(group), **value)


def snapshot() -> dict:
    value = json.loads(command('nft', '-j', '-a', 'list', 'table', 'inet', TABLE))
    rows = [row for row in value['nftables'] if 'metainfo' not in row]
    assert len(rows) == 5, 'unexpected UDP table shape'
    table = rows[0]['table']
    assert table['family'] == 'inet' and table['name'] == TABLE and not table.get('flags')
    chains = [row['chain'] for row in rows if 'chain' in row]
    rules = [row['rule'] for row in rows if 'rule' in row]
    assert len(chains) == len(rules) == 2
    for chain, hook in zip(chains, ('input', 'output')):
        assert chain['name'] == hook and chain['type'] == 'filter'
        assert chain['hook'] == hook and chain['prio'] == -10 and chain['policy'] == 'accept'
    counts = {}
    for rule, hook in zip(rules, ('input', 'output')):
        assert rule['chain'] == hook and rule['expr'][-1] == {'drop': None}
        counter = rule['expr'][-2]['counter']
        for field in ('packets', 'bytes'):
            number = counter[field]
            assert type(number) is int and 0 <= number < 2**64
            counts[hook + '_' + field] = number
            counter[field] = 0
    # Includes table/chain/rule handles, expression order and cgroup selector.
    # Counters are the only mutable fields removed from the private identity.
    return dict(shape=rows, table_handle=table['handle'],
                input_handle=rules[0]['handle'], output_handle=rules[1]['handle'], **counts)


def install(identity: dict) -> dict:
    absent()
    group = identity['ControlGroup'].lstrip('/')
    assert re.fullmatch(r'system.slice/happyranch-(?:tsnet-sidecar|m4-udp-probe)\.service', group)
    command('nft', '-f', '-', data=f'''create table inet {TABLE}
add chain inet {TABLE} input {{ type filter hook input priority -10; policy accept; }}
add chain inet {TABLE} output {{ type filter hook output priority -10; policy accept; }}
add rule inet {TABLE} input iifname != "lo" meta l4proto udp socket cgroupv2 level 2 "{group}" counter drop
add rule inet {TABLE} output oifname != "lo" meta l4proto udp meta skuid {identity['uid']} counter drop
''')
    return snapshot()


def absent() -> None:
    rows = json.loads(command('nft', '-j', 'list', 'tables'))['nftables']
    assert not any(row.get('table', {}).get('name') == TABLE for row in rows), 'test UDP table remains'


def clean() -> None:
    absent()
    assert not Path('/run/netns', NS).exists() and not Path('/sys/class/net', LINK).exists(), 'UDP control network remains'
    assert not subprocess.run(['getent', 'passwd', PROBE_USER], capture_output=True).stdout, 'UDP control account remains'
    assert command('systemctl', 'show', PROBE_UNIT, '-p', 'LoadState', '--value').strip() == 'not-found', 'UDP control unit remains'


def remove() -> None:
    rows = json.loads(command('nft', '-j', 'list', 'tables'))['nftables']
    if any(row.get('table', {}).get('family') == 'inet' and row['table']['name'] == TABLE for row in rows):
        command('nft', 'delete', 'table', 'inet', TABLE)
    absent()


def journal_request(path: str, since_us: int, seconds: float) -> None:
    """Existing final org-survival/request200 ACK; no new endpoint or credential."""
    unit = 'happyranch-m4-daemon.service'
    identity = dict(line.split('=', 1) for line in command(
        'systemctl', 'show', unit, '-p', 'MainPID,InvocationID').splitlines())
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        raw = command('journalctl', '--no-pager', '-o', 'json', '-u', unit,
                      '--since', '@' + str(since_us / 1_000_000))
        rows = [json.loads(line) for line in raw.splitlines()]
        assert len(rows) < 4096, 'request journal budget'
        for row in rows:
            if (row.get('_PID') == identity['MainPID'] and
                    row.get('_SYSTEMD_INVOCATION_ID') == identity['InvocationID'] and
                    int(row['__REALTIME_TIMESTAMP']) >= since_us and
                    re.search(r'"GET ' + re.escape(path) + r' HTTP/1\.[01]" 200(?: OK)?$', row.get('MESSAGE', ''))):
                assert dict(line.split('=', 1) for line in command(
                    'systemctl', 'show', unit, '-p', 'MainPID,InvocationID').splitlines()) == identity
                return
        time.sleep(0.2)
    raise AssertionError('fresh native request acknowledgement missing')


def receipt(state: dict, current: dict, stage: str) -> dict:
    return dict(label=stage, rule_version=1, uid=state['identity']['uid'],
                cgroup_id=state['identity']['cgroup_id'],
                observed_us=time.time_ns() // 1000,
                **{key: current[key] for key in ('table_handle', 'input_handle', 'output_handle',
                                               'input_packets', 'input_bytes', 'output_packets', 'output_bytes')})


def before() -> dict:
    assert not STATE.exists(), 'UDP proof window already owned'
    run_since = int(float(ROOT.joinpath('logs/journal-since.epoch').read_text()) * 1_000_000)
    journal_request('/api/v1/orgs', run_since, 60)
    identity = unit_identity(UNIT)
    current = install(identity)
    state = dict(identity=identity, snapshot=current, started=time.monotonic(),
                 sha=ROOT.joinpath('owned-sha').read_text(),
                 recipient=hashlib.sha256(ROOT.joinpath('cell/recipient.pem').read_bytes()).hexdigest(),
                 boot=Path('/proc/sys/kernel/random/boot_id').read_text())
    public = receipt(state, current, 'before')
    state['before'] = public
    with os.fdopen(os.open(STATE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
        json.dump(state, stream)
    return public


def after() -> dict:
    state = json.loads(STATE.read_text())
    assert state['sha'] == ROOT.joinpath('owned-sha').read_text()
    assert state['recipient'] == hashlib.sha256(ROOT.joinpath('cell/recipient.pem').read_bytes()).hexdigest()
    assert state['boot'] == Path('/proc/sys/kernel/random/boot_id').read_text()
    remaining = 35 - (time.monotonic() - state['started'])
    assert remaining > 0, 'UDP proof deadline'
    journal_request('/api/v1/orgs/m2-synthetic/teams', state['before']['observed_us'], remaining)
    remaining = 35 - (time.monotonic() - state['started'])
    assert remaining > 0, 'UDP proof deadline'
    journal_request('/api/v1/orgs', state['before']['observed_us'], remaining)
    assert unit_identity(UNIT) == state['identity'], 'sidecar identity changed'
    current = snapshot()
    assert current['shape'] == state['snapshot']['shape'], 'UDP rule identity changed'
    assert time.monotonic() - state['started'] < 35, 'UDP proof deadline'
    return receipt(state, current, 'after')


def controls() -> dict:
    """Real packets on a private veth; existing UDP3478 only, no public listener."""
    absent()
    assert not subprocess.run(['getent', 'passwd', PROBE_USER], capture_output=True).stdout
    assert not Path('/run/netns', NS).exists() and not Path('/sys/class/net', LINK).exists()
    user_created = unit_created = net_created = link_created = False
    try:
        command('useradd', '--system', '--no-create-home', '--shell', '/usr/sbin/nologin', PROBE_USER)
        user_created = True
        command('systemd-run', '--quiet', '--unit=' + PROBE_UNIT, '--property=User=' + PROBE_USER,
                '/usr/bin/python3', '-c', 'import time; time.sleep(120)')
        unit_created = True
        identity = unit_identity(PROBE_UNIT)
        command('ip', 'netns', 'add', NS)
        net_created = True
        command('ip', 'link', 'add', LINK, 'type', 'veth', 'peer', 'name', PEER)
        link_created = True
        command('ip', 'link', 'set', PEER, 'netns', NS)
        command('ip', 'address', 'add', '198.18.254.1/30', 'dev', LINK)
        command('ip', 'link', 'set', LINK, 'up')
        command('ip', '-n', NS, 'address', 'add', '198.18.254.2/30', 'dev', PEER)
        command('ip', '-n', NS, 'link', 'set', PEER, 'up')
        initial = install(identity)
        try:
            clean()
        except AssertionError as error:
            assert str(error) == 'test UDP table remains', 'wrong clean-state refusal'
        else:
            raise AssertionError('clean-state accepted a surviving UDP table')
        receiver = ('import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); '
                    's.bind(("{address}",3478)); s.settimeout(1); print("ready",flush=True)\n'
                    'try: print(int(s.recv(8)==b"M4"))\nexcept TimeoutError: print(0)')
        sender = ('import socket; socket.socket(socket.AF_INET,socket.SOCK_DGRAM)'
                  '.sendto(b"M4",("{address}",3478))')

        def enter() -> None:
            Path(identity['group'], 'cgroup.procs').write_text(str(os.getpid()))
            os.setgid(pwd.getpwnam(PROBE_USER).pw_gid)
            os.setuid(identity['uid'])

        for direction in ('output', 'input'):
            for selected in (True, False):
                before_count = snapshot()[direction + '_packets']
                receive_argv = ['/usr/bin/python3', '-u', '-c', receiver.format(
                    address='198.18.254.2' if direction == 'output' else '198.18.254.1')]
                if direction == 'output':
                    receive_argv = ['ip', 'netns', 'exec', NS, *receive_argv]
                child = subprocess.Popen(receive_argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         text=True, preexec_fn=enter if direction == 'input' and selected else None)
                try:
                    # A short owned receive process; readiness read has an OS lifetime bound.
                    import select
                    assert select.select([child.stdout], [], [], 5)[0], 'UDP control readiness deadline'
                    assert child.stdout.readline() == 'ready\n', 'UDP control not listening'
                    send_argv = ['/usr/bin/python3', '-c', sender.format(
                        address='198.18.254.2' if direction == 'output' else '198.18.254.1')]
                    if direction == 'input':
                        send_argv = ['ip', 'netns', 'exec', NS, *send_argv]
                    sent = subprocess.run(send_argv, capture_output=True, timeout=5,
                                          preexec_fn=enter if direction == 'output' and selected else None)
                    assert sent.returncode == 0 and not sent.stderr
                    out, err = child.communicate(timeout=5)
                    assert child.returncode == 0 and not err and out == ('0\n' if selected else '1\n'), 'UDP selection control failed'
                    delta = snapshot()[direction + '_packets'] - before_count
                    assert delta == int(selected), 'UDP drop counter control failed'
                finally:
                    if child.poll() is None:
                        child.kill()
                    child.wait(timeout=5)
        assert snapshot()['shape'] == initial['shape']
        return dict(label='passed', output_positive=1, output_negative=1, input_positive=1,
                    input_negative=1, present_table_refused=1, removed=1)
    finally:
        remove()
        if link_created:
            command('ip', 'link', 'delete', LINK)
        if net_created:
            command('ip', 'netns', 'delete', NS)
        if unit_created:
            command('systemctl', 'stop', PROBE_UNIT)
        if user_created:
            command('userdel', PROBE_USER)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('before', 'after', 'remove', 'absent', 'clean', 'controls'))
    args = parser.parse_args()
    assert os.geteuid() == 0 and os.environ.get('M4_AUTHORIZED_VM') == 'yes'
    try:
        result = globals()[args.phase]()
        if result is not None:
            print(json.dumps(result))
    except Exception:
        raise SystemExit('M4 UDP condition refused; private details withheld') from None


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Host-only GitHub/VM coordination for the manual M4 macOS run.

The existing gh credential stays on this host. No runner token or setting change.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Callable, Iterator

from m4_tailnet_runner import COORDINATION_PR, OPERATOR_ID, PREFIX, REPOSITORY, binding
from m4_tailnet_client import IDENTITY_COUNTS, exception_class, require_relay_identity, require_udp_evidence, UDP_IDENTITY


def relay_file(path: Path) -> dict:
    info = path.lstat()
    assert stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1 and not info.st_mode & 0o022, 'relay file provenance'
    return dict(device=info.st_dev, inode=info.st_ino, size=info.st_size,
                mtime=info.st_mtime_ns, ctime=info.st_ctime_ns,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def snapshot_relay_launch() -> None:
    """Record inputs BEFORE systemd launches the pinned disposable fixture."""
    root = Path('/var/lib/happyranch-m4')
    record = dict(sha=root.joinpath('owned-sha').read_text().strip(),
                  boot=Path('/proc/sys/kernel/random/boot_id').read_text(),
                  after_ticks=int(float(Path('/proc/uptime').read_text().split()[0]) * os.sysconf('SC_CLK_TCK')),
                  config=relay_file(root / 'cell/config.yaml'), executable=relay_file(root / 'cell/headscale'))
    assert record['executable']['sha256'] == 'd2cda0a5d748587f77c920a76cd1bf1ab429e5299ba5bc6b3dda90712721b45b', 'unpinned Headscale'
    with root.joinpath('cell/relay-launch.json').open('x') as stream:
        json.dump(record, stream)


def guest_relay_identity(initial: bool = False) -> dict:
    """Effective GenerateRegion records plus live input provenance, never raw logs/env."""
    import yaml  # Existing installed product dependency; guest only.

    root = Path('/var/lib/happyranch-m4')
    launch = json.loads(root.joinpath('cell/relay-launch.json').read_text())
    assert root.joinpath('owned-sha').read_text().strip() == launch['sha'], 'relay source ownership'
    assert Path('/proc/sys/kernel/random/boot_id').read_text() == launch['boot'], 'relay boot changed'
    assert relay_file(root / 'cell/config.yaml') == launch['config'], 'relay config changed after launch'
    assert relay_file(root / 'cell/headscale') == launch['executable'], 'relay executable changed'
    result = subprocess.run(['systemctl', 'show', 'happyranch-m4-headscale.service',
                             '-p', 'MainPID', '-p', 'InvocationID', '-p', 'ActiveState'],
                            capture_output=True, timeout=10, check=True)
    assert not result.stderr and len(result.stdout) < 4096, 'relay unit observation'
    unit = dict(line.split('=', 1) for line in result.stdout.decode().splitlines())
    assert unit['ActiveState'] == 'active' and re.fullmatch(r'[1-9][0-9]*', unit['MainPID']), 'relay unit not running'
    assert re.fullmatch(r'[0-9a-f]{32}', unit['InvocationID']), 'relay invocation missing'
    proc = Path('/proc') / unit['MainPID']
    process_stat = proc.joinpath('stat').read_text()
    start_ticks = int(process_stat.rsplit(')', 1)[1].split()[19])
    assert start_ticks >= launch['after_ticks'] - 1, 'relay process predates launch'
    assert proc.joinpath('exe').resolve() == root / 'cell/headscale', 'relay process executable'
    assert hashlib.sha256(proc.joinpath('exe').read_bytes()).hexdigest() == launch['executable']['sha256'], 'relay running binary'
    assert proc.joinpath('cmdline').read_bytes().split(b'\0') == [str(root / 'cell/headscale').encode(), b'serve', b'--config', str(root / 'cell/config.yaml').encode(), b''], 'relay argv drift'
    environment = proc.joinpath('environ').read_bytes()
    assert len(environment) <= 65536 and environment.endswith(b'\0'), 'relay environment unavailable'
    # Viper AutomaticEnv and envknob read HEADSCALE_*; refuse ALL overrides,
    # including debug settings, rather than emulate their coercion/defaults.
    assert not any(entry.split(b'=', 1)[0].upper().startswith((b'HEADSCALE_', b'DERP_', b'TS_')) for entry in environment.split(b'\0')), 'relay environment override'
    process = dict(pid=unit['MainPID'], invocation=unit['InvocationID'], start_ticks=start_ticks)
    if not initial:
        assert json.loads(root.joinpath('cell/relay-process.json').read_text()) == process, 'relay process replaced'
    config = yaml.safe_load(root.joinpath('cell/config.yaml').read_text())
    derp = config['derp']
    assert set(derp) == {'server', 'urls', 'paths', 'auto_update_enabled'}, 'unexpected relay map/mesh setting'
    assert derp['urls'] == derp['paths'] == [] and derp['auto_update_enabled'] is False, 'upstream relay sources'
    server = derp['server']
    assert set(server) == {'enabled', 'region_id', 'region_code', 'region_name', 'ipv4', 'stun_listen_addr', 'private_key_path', 'automatically_add_embedded_derp_region'}, 'unexpected embedded relay setting'
    assert server['enabled'] is True and server['automatically_add_embedded_derp_region'] is True, 'embedded relay disabled'
    assert type(server['region_id']) is int and server['region_id'] == 999, 'effective relay region'
    assert config['server_url'] == 'https://47-238-219-200.sslip.io' and config['listen_addr'] == '0.0.0.0:443', 'relay server endpoint'
    assert server['ipv4'] == '47.238.219.200' and server['stun_listen_addr'] == '0.0.0.0:3478', 'relay node endpoint'
    result = subprocess.run(['journalctl', '--no-pager', '--output=json', '-u', 'happyranch-m4-headscale.service',
                             '_SYSTEMD_INVOCATION_ID=' + unit['InvocationID']], capture_output=True, timeout=10, check=True)
    assert not result.stderr and len(result.stdout) <= 1_000_000, 'relay journal budget'
    messages = []
    for line in result.stdout.splitlines():
        row = json.loads(line)
        assert row['_SYSTEMD_INVOCATION_ID'] == unit['InvocationID'] and row['_PID'] == unit['MainPID'], 'relay journal attribution'
        assert isinstance(row['MESSAGE'], str), 'relay journal shape'
        messages.append(row['MESSAGE'])
    regions = [message.split('DERP region: ', 1)[1] for message in messages if 'DERP region: ' in message]
    nodes = [message.split('DERP Nodes[0]: ', 1)[1] for message in messages if 'DERP Nodes[0]: ' in message]
    assert len(regions) == len(nodes) == 1, 'missing/multiple effective relay records'
    assert re.match(r'\{RegionID:999 RegionCode:m4 RegionName:HappyRanch M4 private relay ', regions[0]), 'runtime relay region drift'
    for field, expected in dict(Name='999', RegionID='999', HostName='47-238-219-200.sslip.io', IPv4='47.238.219.200',
                                 IPv6='', CertName='', STUNPort='3478', DERPPort='443', STUNOnly='false', InsecureForTests='false').items():
        matches = re.findall(r'(?:\{| )' + field + r':([^ ]*)(?= |\})', nodes[0])
        assert matches == [expected], 'runtime relay node drift'
    assert proc.joinpath('stat').read_text().rsplit(')', 1)[1].split()[19] == str(start_ticks), 'relay process changed during observation'
    assert relay_file(root / 'cell/config.yaml') == launch['config'], 'relay config changed during observation'
    if initial:
        with root.joinpath('cell/relay-process.json').open('x') as stream:
            json.dump(process, stream)
        return dict(label='passed', **IDENTITY_COUNTS)
    public = root.joinpath('cell/recipient.pem').read_bytes()
    return dict(sha=launch['sha'], key_sha256=hashlib.sha256(public).hexdigest(),
                identity=dict(label='passed', observed_at=int(time.time()), **IDENTITY_COUNTS))


def relay_identity_receipt(value: dict, sha: str, key_sha256: str) -> dict:
    assert set(value) == {'sha', 'key_sha256', 'identity'}, 'relay receipt shape'
    assert value['sha'] == sha and value['key_sha256'] == key_sha256, 'relay receipt run binding'
    require_relay_identity(value['identity'], {})
    return value['identity']


@contextmanager
def control_wait(waits: list[dict], phase: str) -> Iterator[dict]:
    started = time.monotonic()
    item = dict(phase=phase, observations=0, status='FAIL', failure_label='observation-failed')
    waits.append(item)
    try:
        yield item
        item.update(status='PASS', failure_label=None)
    except Exception as error:
        item['exception_class'] = exception_class(error)
        raise
    finally:
        item['elapsed_seconds'] = round(time.monotonic() - started, 3)


def require_wait(item: dict, condition: bool, label: str) -> None:
    if not condition:
        item['failure_label'] = label
    assert condition, label  # Only fixed call-site labels, never external text.


def github(path: str, body: dict | None = None) -> bytes:
    command = ['gh', 'api', f'repos/{REPOSITORY}/{path}']
    if body is not None:
        command += ['--method', 'POST', '--input', '-']
    result = subprocess.run(command, input=json.dumps(body).encode() if body is not None else None,
                            capture_output=True, timeout=60, check=False)
    if result.returncode:
        # No credential or gh error/request text leaves this boundary. A failed
        # POST is uncertain and is NEVER retried automatically.
        raise RuntimeError('GitHub handoff operation failed; do not replay a write')
    return result.stdout


class HostedRun:
    def __init__(self, run_id: int, sha: str) -> None:
        self.run_id = run_id
        self.sha = sha
        self.observe()
        result = subprocess.run(['gh', 'api', 'user'], capture_output=True, timeout=30, check=False)
        assert result.returncode == 0, 'operator observation unavailable'
        who = json.loads(result.stdout)
        assert who['id'] == OPERATOR_ID, 'unexpected operator account; no credential fallback'
        self.request: dict = {}
        self.comments: list[dict] = []
        self.waits: list[dict] = []

    def observe(self) -> dict:
        run = json.loads(github(f'actions/runs/{self.run_id}'))
        assert run['event'] == 'workflow_dispatch' and run['head_branch'] == 'main'
        assert run['head_sha'] == self.sha and run['run_attempt'] == 1
        assert run['path'] == '.github/workflows/managed-m4.yml'
        assert run['head_repository']['full_name'] == REPOSITORY
        created = datetime.fromisoformat(run['created_at'].replace('Z', '+00:00'))
        assert 0 <= (datetime.now(timezone.utc) - created).total_seconds() < 2400, 'stale dispatch'
        return run

    def artifact(self, name: str, member: str) -> dict | None:
        listing = json.loads(github(f'actions/runs/{self.run_id}/artifacts?per_page=100'))
        assert listing['total_count'] < 100
        rows = [row for row in listing['artifacts'] if row['name'] == name]
        assert len(rows) <= 1, 'duplicate immutable artifact'
        if not rows:
            return None
        row = rows[0]
        assert not row['expired'] and row['size_in_bytes'] < 65536
        assert row['workflow_run']['id'] == self.run_id and row['workflow_run']['head_sha'] == self.sha
        archive = github(f'actions/artifacts/{row["id"]}/zip')
        assert len(archive) < 65536
        assert row['digest'] == 'sha256:' + hashlib.sha256(archive).hexdigest(), 'artifact digest mismatch'
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            assert member in bundle.namelist()
            entry = bundle.getinfo(member)
            assert entry.file_size < 16384
            return json.loads(bundle.read(entry))

    def public_request(self) -> dict:
        with control_wait(self.waits, 'public-key-handoff') as wait:
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                wait['observations'] += 1
                run = self.observe()
                require_wait(wait, run['status'] != 'completed', 'runner-completed')
                request = self.artifact(f'managed-m4-request-{self.run_id}-1', 'request.json')
                if request:
                    assert request['run_id'] == self.run_id and request['sha'] == self.sha
                    binding(request)
                    result = subprocess.run(['openssl', 'pkey', '-pubin', '-text', '-noout'],
                                            input=request['public_key'].encode(), capture_output=True, timeout=15)
                    assert result.returncode == 0 and b'(3072 bit)' in result.stdout, 'invalid RSA public key'
                    self.request = request
                    return request
                time.sleep(15)
            wait['failure_label'] = 'deadline'
            raise RuntimeError('public-key artifact deadline')

    def publish(self, phase: str, payload: dict) -> None:
        # A successful VM command alone cannot authorize public text. Accept
        # only canonical RSA3072 ciphertext, the synthetic address, a Boolean
        # or the closed runtime-identity projection.
        if phase == 'enroll':
            assert set(payload) == {'enroll'}
            secrets = ['enroll']
        elif phase == 'pairs':
            assert set(payload) == {'address', 'pair-main', 'pair-revoked', 'pair-ready'}
            assert ipaddress.ip_address(payload['address']) in ipaddress.ip_network('100.64.0.0/10')
            secrets = ['pair-main', 'pair-revoked', 'pair-ready']
        elif phase in {'udp-before', 'udp-after'}:
            assert set(payload) == {'enforcement'}
            require_udp_evidence(payload['enforcement'], phase.removeprefix('udp-'), {})
            secrets = []
        elif phase == 'relay-identity':
            assert set(payload) == {'identity'}
            require_relay_identity(payload['identity'], {})
            secrets = []
        else:
            assert phase == 'revoked' and payload == {'revoked': True}
            secrets = []
        for name in secrets:
            decoded = base64.b64decode(payload[name], validate=True)
            assert len(decoded) == 384 and base64.b64encode(decoded).decode() == payload[name], 'public handoff is not RSA3072 ciphertext'
        now = int(time.time())
        message = dict(format='m4-encrypted-envelope@1', binding=binding(self.request), phase=phase,
                       issued_at=now, expires_at=now + (540 if phase == 'enroll' else 240), payload=payload)
        response = json.loads(github(f'issues/{COORDINATION_PR}/comments', {'body': PREFIX + json.dumps(message)}))
        assert response['user']['id'] == OPERATOR_ID and response['body'] == PREFIX + json.dumps(message)
        self.comments.append(dict(phase=phase, id=response['id'], url=response['html_url']))

    def coordinate(self, phase: Callable[..., str], record: dict) -> None:
        record['control_waits'] = self.waits
        self.publish('enroll', {'enroll': phase('enroll', private=True).strip()})
        with control_wait(self.waits, 'outside-join') as wait:
            deadline = time.monotonic() + 180
            while True:
                wait['observations'] += 1
                rows = json.loads(phase('nodes', private=True))
                if len(rows) == 1 and rows[0]['name'] == 'synthetic-m4-outside':
                    break
                require_wait(wait, not rows, 'unexpected-membership')
                run = self.observe()
                require_wait(wait, run['status'] != 'completed', 'runner-completed')
                require_wait(wait, time.monotonic() < deadline, 'deadline')
                time.sleep(5)
        phase('install')
        rows = json.loads(phase('nodes', private=True))
        home = [row for row in rows if row['name'] == 'home-sidecar-ci']
        assert len(rows) == 2 and len(home) == 1 and len(home[0]['ips']) == 1
        self.publish('pairs', dict(address=home[0]['ips'][0], **{
            name: phase(name, private=True).strip() for name in ('pair-main', 'pair-revoked', 'pair-ready')}))
        with control_wait(self.waits, 'pair-ready') as wait:
            deadline = time.monotonic() + 180
            while True:
                wait['observations'] += 1
                devices = json.loads(phase('devices', private=True))['devices']
                active = {row['device_id'] for row in devices if row['paired'] and not row['revoked']}
                if 'pair-ready' in active:
                    require_wait(wait, active == {'pair-main', 'pair-revoked', 'pair-ready'}, 'unexpected-membership')
                    break
                run = self.observe()
                require_wait(wait, run['status'] != 'completed', 'runner-completed')
                require_wait(wait, time.monotonic() < deadline, 'deadline')
                time.sleep(5)
        phase('revoke')
        self.publish('revoked', {'revoked': True})
        identity = relay_identity_receipt(json.loads(phase('relay-identity', private=True)), self.sha, self.request['key_sha256'])
        self.publish('relay-identity', {'identity': identity})
        record['relay_identity'] = {key: identity[key] for key in ('label', *IDENTITY_COUNTS)}
        before = json.loads(phase('udp-before', private=True))
        require_udp_evidence(before, 'before', {})
        self.publish('udp-before', {'enforcement': before})
        after = json.loads(phase('udp-after', private=True))
        require_udp_evidence(after, 'after', {})
        assert all(before[key] == after[key] for key in UDP_IDENTITY), 'UDP rule identity changed'
        self.publish('udp-after', {'enforcement': after})
        record['udp_enforcement'] = dict(before=before, after=after)
        phase('exposure-sockets')
        with control_wait(self.waits, 'native-completion') as wait:
            deadline = time.monotonic() + 240
            while True:
                wait['observations'] += 1
                run = self.observe()
                if run['status'] == 'completed':
                    require_wait(wait, run['conclusion'] == 'success', 'runner-failed')
                    break
                require_wait(wait, time.monotonic() < deadline, 'deadline')
                time.sleep(15)
        summary = self.artifact(f'managed-m4-logs-{self.run_id}-1', 'client-summary.json')
        assert summary and summary['sha'] == self.sha and summary['run_id'] == self.run_id
        assert summary['status'] == 'CLIENT_CHECKS_PASSED_VM_CLEANUP_PENDING' and summary['private_scratch_absent']
        record['native_client'] = summary
        record['handoff_comments'] = self.comments

    def cancel_if_running(self) -> None:
        if self.observe()['status'] != 'completed':
            github(f'actions/runs/{self.run_id}/cancel', {})

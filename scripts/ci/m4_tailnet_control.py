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
import subprocess
import time
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Callable, Iterator

from m4_tailnet_runner import COORDINATION_PR, OPERATOR_ID, PREFIX, REPOSITORY, binding
from m4_tailnet_client import exception_class


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
        # only canonical RSA3072 ciphertext, the synthetic address or a Boolean.
        if phase == 'enroll':
            assert set(payload) == {'enroll'}
            secrets = ['enroll']
        elif phase == 'pairs':
            assert set(payload) == {'address', 'pair-main', 'pair-revoked', 'pair-ready'}
            assert ipaddress.ip_address(payload['address']) in ipaddress.ip_network('100.64.0.0/10')
            secrets = ['pair-main', 'pair-revoked', 'pair-ready']
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

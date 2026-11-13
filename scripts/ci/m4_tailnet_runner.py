#!/usr/bin/env python3
"""Native main-only M4 runner. Public artifact request, OAEP-only PR handoff.

No cloud/admin/daemon credential is accepted. See README-m4-tailnet.md.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

from m4_tailnet_client import (Bridge, EXPECTED, NATIVE_HOST, ROUTE, application_probe, build_archive, control_get,
                              exception_class, safe_join_diagnostics, safe_derp_diagnostics,
                              prove_enforced_path, safe_path_diagnostics, safe_exposure_diagnostics)

REPOSITORY = 't-benze/happyranch'
COORDINATION_PR = 1449
OPERATOR_ID = 4394426  # Immutable GitHub ID of the existing repository operator.
PREFIX = 'M4 encrypted handoff v1\n'


def openssl(arguments: list[str], data: bytes | None = None) -> bytes:
    result = subprocess.run(['openssl', *arguments], input=data, capture_output=True, timeout=30, check=False)
    if result.returncode:
        raise RuntimeError('OAEP/key operation failed; private details withheld')
    return result.stdout


def decrypt(private: bytes, ciphertext: str) -> str:
    # The private key is an inherited pipe, never argv/env or a decrypt output file.
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, private)
        os.close(write_fd)
        write_fd = -1
        result = subprocess.run(['openssl', 'pkeyutl', '-decrypt', '-inkey', f'/dev/fd/{read_fd}',
                                 '-pkeyopt', 'rsa_padding_mode:oaep', '-pkeyopt', 'rsa_oaep_md:sha256'],
                                input=base64.b64decode(ciphertext, validate=True), capture_output=True,
                                pass_fds=(read_fd,), timeout=30, check=False)
        if result.returncode:
            # Fixed command identity only; do not retain argv, stderr or output.
            raise subprocess.CalledProcessError(result.returncode, 'openssl-decrypt')
        return result.stdout.decode().strip()
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)


def binding(request: dict) -> dict:
    assert request['format'] == 'm4-public-request@1'
    assert type(request['run_id']) is int and request['run_id'] > 0
    assert request['attempt'] == 1 and type(request['attempt']) is int, 'reruns require a new dispatch'
    assert re.fullmatch(r'[0-9a-f]{40}', request['sha'])
    public = request['public_key'].encode('ascii')
    assert len(public) < 1024 and public.startswith(b'-----BEGIN PUBLIC KEY-----\n')
    digest = hashlib.sha256(public).hexdigest()
    assert request['key_sha256'] == digest
    return {key: request[key] for key in ('run_id', 'attempt', 'sha', 'key_sha256')}


def envelope(comments: list[dict], request: dict, phase: str) -> dict | None:
    matches = []
    expected = binding(request)
    now = int(time.time())
    for comment in comments:
        if comment.get('user', {}).get('id') != OPERATOR_ID:
            continue
        body = comment.get('body', '')
        if not body.startswith(PREFIX):
            continue
        try:
            value = json.loads(body[len(PREFIX):])
        except ValueError:
            continue
        if value.get('binding') != expected or value.get('phase') != phase:
            continue
        assert set(value) == {'format', 'binding', 'phase', 'issued_at', 'expires_at', 'payload'}
        assert value['format'] == 'm4-encrypted-envelope@1'
        assert type(value['issued_at']) is int and type(value['expires_at']) is int
        maximum = 600 if phase == 'enroll' else 300
        assert 0 <= now - value['issued_at'] <= maximum, 'stale/future handoff'
        assert now < value['expires_at'] <= value['issued_at'] + maximum
        created = datetime.fromisoformat(comment['created_at'].replace('Z', '+00:00')).timestamp()
        assert abs(created - value['issued_at']) < 60, 'comment/envelope clock mismatch'
        assert comment['created_at'] == comment['updated_at'], 'edited handoff refused'
        matches.append(value['payload'])
    assert len(matches) <= 1, 'duplicate handoff refused'
    return matches[0] if matches else None


class Inbox:
    def __init__(self, request: dict) -> None:
        self.request = request
        self.reads = 0

    def wait(self, phase: str, seconds: int) -> dict:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.reads += 1
            assert self.reads <= 48, 'public API read budget exhausted'
            # This repository is public. No GITHUB_TOKEN permission expansion is
            # needed; API limits/outages refuse the run instead of adding scopes.
            url = (f'https://api.github.com/repos/{REPOSITORY}/issues/{COORDINATION_PR}/comments'
                   '?per_page=100')
            # List-comments has ascending ordering; use since to bound this run,
            # and refuse overflow rather than silently ignoring another envelope.
            since = self.request['created_at']
            url += '&since=' + since
            with urllib.request.urlopen(urllib.request.Request(url, headers={'Accept': 'application/vnd.github+json',
                                                                            'User-Agent': 'happyranch-m4'}), timeout=20) as response:
                assert response.status == 200
                data = response.read(2_000_001)
            assert len(data) <= 2_000_000, 'comment response budget'
            comments = json.loads(data)
            assert len(comments) < 100, 'comment window overflow'
            result = envelope(comments, self.request, phase)
            if result is not None:
                return result
            # The two bookends share the original 35s proof window and the
            # same 48-read run budget. Existing credential handoffs stay at15s.
            time.sleep(1 if phase in {'udp-before', 'udp-after'} else 15)
        raise RuntimeError(f'{phase} handoff deadline')


def runner_context() -> tuple[Path, dict]:
    assert __debug__, 'Python optimization disables checks'
    assert sys.platform == 'darwin', 'native macOS required'
    assert os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
    assert os.environ.get('GITHUB_REPOSITORY') == REPOSITORY
    assert os.environ.get('GITHUB_REF') == 'refs/heads/main'
    assert os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
    root = Path(__file__).resolve().parents[2]
    sha = os.environ['GITHUB_SHA']
    assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip() == sha
    assert not subprocess.check_output(['git', 'status', '--porcelain'], cwd=root, text=True).strip()
    return root, dict(format='m4-public-request@1', sha=sha, run_id=int(os.environ['GITHUB_RUN_ID']),
                      attempt=int(os.environ['GITHUB_RUN_ATTEMPT']),
                      created_at=datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'))


def native_state_link(scratch: Path, remove: bool = False) -> None:
    # Pinned tsnet defaults to os.UserConfigDir()/tsnet-<os.Executable name>.
    # Darwin ignores XDG_CONFIG_HOME. Both preflight and acceptance execute this
    # fixed native main binary; Python is embedded only AFTER Go initialization.
    # No HOME override/product export. Remove only our exact disposable link.
    program = NATIVE_HOST
    config = Path.home() / 'Library/Application Support'
    target = scratch / 'client-state'
    alias = config / ('tsnet-' + program)
    legacy = config / ('tslib-' + program)
    assert not legacy.exists() and not legacy.is_symlink(), 'pre-existing native state refused'
    if remove:
        if alias.is_symlink():
            assert os.readlink(alias) == str(target), 'foreign native state link refused'
            alias.unlink()
        else:
            assert not alias.exists(), 'unowned native state refused'
    else:
        assert not alias.exists() and not alias.is_symlink(), 'pre-existing native state refused'
        info = scratch.lstat()
        assert stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700, 'private scratch required'
        # Create exclusively before exposing the alias; Bridge verifies it again
        # before spawning the worker. Never adopt a pre-existing target.
        target.mkdir(mode=0o700)
        config.mkdir(parents=True, exist_ok=True)
        alias.symlink_to(target, target_is_directory=True)


def native_precondition(library: Path, scratch: Path, output: Path) -> None:
    bridge: Bridge | None = None
    label = None
    state_owned = False
    try:
        # Reuse the acceptance target/name so always-cleanup has exactly one
        # possible owned alias even if preparation is interrupted.
        native_state_link(scratch)
        state_owned = True
        (scratch / 'client-state/tailscaled.log.conf').write_bytes(b'{')
        bridge = Bridge(library, scratch)
        try:
            assert bridge.call('precondition') == {'precondition': True}
        finally:
            label = bridge.bridge_error_class
    except Exception:
        # Same closed label projection as acceptance; no private error text.
        (output / 'client-summary.json').write_text(json.dumps(dict(
            status='FAIL', phase='native-precondition', bridge_error_class=label)) + '\n')
        print('M4 native precondition failed; bridge_error_class=' + str(label), flush=True)
        raise
    finally:
        if bridge is not None:
            bridge.close()
        if state_owned:
            native_state_link(scratch, remove=True)
            if (scratch / 'client-state').exists():
                shutil.rmtree(scratch / 'client-state')
    print('M4 native precondition passed; expected bridge_error_class=log-dir', flush=True)


def prepare(scratch: Path, output: Path) -> None:
    root, request = runner_context()
    assert request['attempt'] == 1, 'new dispatch required'
    scratch.mkdir(mode=0o700)
    output.mkdir()
    library = build_archive(root, scratch)
    # Refuse before generating/publishing the public recipient or consuming an
    # enrollment handoff. The exact same native binary is used for acceptance.
    native_precondition(library, scratch, output)
    private = openssl(['genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:3072'])
    public = openssl(['pkey', '-pubout'], private)
    # This is only the transient RSA key, never enrollment/pairing plaintext.
    descriptor = os.open(scratch / 'recipient.key', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(private)
    request.update(public_key=public.decode('ascii'), key_sha256=hashlib.sha256(public).hexdigest(),
                   native=dict(go_version=(library.parent / 'build-0.log').read_text().strip(),
                               archive_sha256=hashlib.sha256(library.with_suffix('.a').read_bytes()).hexdigest()))
    binding(request)
    (output / 'request.json').write_text(json.dumps(request, indent=2) + '\n')


def run(scratch: Path, output: Path) -> None:
    _, current = runner_context()
    request = json.loads((output / 'request.json').read_text())
    assert all(current[key] == request[key] for key in ('sha', 'run_id', 'attempt'))
    private = (scratch / 'recipient.key').read_bytes()
    (scratch / 'recipient.key').unlink()
    inbox = Inbox(request)
    record: dict = dict(sha=request['sha'], run_id=request['run_id'], attempt=1, native=request['native'],
                        outside_client='native macOS unchanged shipped C archive', status='FAIL', checks=[], phases=[],
                        bridge_error_class=None)
    bridge: Bridge | None = None
    exposure: dict | None = None
    phase_started = time.monotonic()

    def set_phase(name: str) -> None:
        nonlocal phase_started
        if record['phases']:
            record['phases'][-1]['elapsed_seconds'] = round(time.monotonic() - phase_started, 3)
        record['phase'] = name
        record['phases'].append(dict(phase=name, elapsed_seconds=0))
        phase_started = time.monotonic()

    try:
        set_phase('handoff-fetch')
        credentials = inbox.wait('enroll', 600)
        assert set(credentials) == {'enroll'}
        set_phase('decrypt')
        enrollment = decrypt(private, credentials['enroll'])
        set_phase('native-process-start')
        native_state_link(scratch)
        bridge = Bridge(scratch / 'native' / NATIVE_HOST, scratch)
        set_phase('join-call')
        try:
            bridge.call('join', key=enrollment)
        finally:
            # Retain only the closed projection BEFORE worker/state removal.
            record['join_diagnostics'] = safe_join_diagnostics(bridge.join_diagnostics)
            record['bridge_error_class'] = bridge.bridge_error_class
            record['phases'][-1:] = bridge.join_phases
            record['phase'] = bridge.join_phase
            phase_started = time.monotonic() - bridge.join_phases[-1]['elapsed_seconds']
        del enrollment
        record['checks'].append(dict(name='outside-joined', joined=True))
        set_phase('pairing-handoff')
        payload = inbox.wait('pairs', 240)
        assert set(payload) == {'address', 'pair-main', 'pair-revoked', 'pair-ready'}
        address = ipaddress.ip_address(payload['address'])
        assert address.version == 4 and address in ipaddress.ip_network('100.64.0.0/10')

        def call(method: str, path: str, credential: str | None = None, data: str = '') -> dict:
            assert bridge is not None
            return bridge.call('request', address=str(address), method=method, path=path, credential=credential, data=data)

        def allowed(credential: str, diagnostic: dict | None = None) -> None:
            response = call('GET', ROUTE, credential)
            if diagnostic is not None:
                diagnostic.update(status=response.get('status'),
                                  body_class='expected-teams' if response.get('body') == EXPECTED else 'other')
            assert response == {'status': 200, 'body': EXPECTED}, 'allowed real-daemon body/status'

        def pair(kind: str) -> str:
            response = call('POST', '/pair', data=decrypt(private, payload[kind]))
            assert response['status'] == 200, 'pair redemption status'
            value = response['body']['credential']
            assert isinstance(value, str) and value.startswith('hrpair_')
            return value

        set_phase('allowed-unpaired-controls')
        paired = pair('pair-main')
        allowed(paired)
        record['checks'].append(dict(name='allowed-200', status=200, body=EXPECTED))
        for name, value in [('absent', None), ('never-paired', 'hrpair_' + '0' * 32)]:
            allowed(paired)
            assert call('GET', ROUTE, value) == {'status': 403, 'body': {'error': 'identity_denied'}}
            allowed(paired)
            record['checks'].append(dict(name='unpaired-' + name, status=403, paired_control=200))
        set_phase('revocation-controls')
        revoked = pair('pair-revoked')
        allowed(revoked)
        # Existing pairing is the VM-visible acknowledgement AFTER this positive
        # control; no extra endpoint, product seam or runner write permission.
        pair('pair-ready')
        assert inbox.wait('revoked', 120) == {'revoked': True}
        deadline = time.monotonic() + 30
        while True:
            response = call('GET', ROUTE, revoked)
            if response == {'status': 403, 'body': {'error': 'identity_denied'}}:
                break
            assert response == {'status': 200, 'body': EXPECTED}, 'unexpected revocation response'
            assert time.monotonic() < deadline, 'revoked credential still succeeds'
            time.sleep(0.2)
        allowed(paired)
        record['checks'].append(dict(name='revoked-refused', status=403, baseline=200, unaffected_control=200))
        set_phase('forbidden-route-controls')
        for method, path, category in [
            ('POST', '/api/v1/executor-binaries/register', 'method_denied'),
            ('POST', '/api/v1/runtime/custom-cli/m4-operation/commit', 'route_denied'),
            ('POST', '/api/v1/runtime/use', 'route_denied'),
            ('DELETE', '/api/v1/runtime/adapters/m4-adapter', 'method_denied'),
            ('DELETE', '/api/v1/orgs/m2-synthetic', 'route_denied'),
            ('GET', '/api/v1/auth/bootstrap', 'route_denied'),
        ]:
            allowed(paired)
            assert call(method, path, paired) == {'status': 403, 'body': {'error': category}}
            allowed(paired)
            record['checks'].append(dict(name='forbidden-route', method=method, path=path, status=403,
                                         error=category, paired_control=200))
        orgs = call('GET', '/api/v1/orgs', paired)
        assert orgs['status'] == 200 and [org['slug'] for org in orgs['body']['orgs']] == ['m2-synthetic']
        set_phase('DERP-exposure')
        path_diagnostic = dict(label='unknown')
        try:
            identity = inbox.wait('relay-identity', 60)
            assert set(identity) == {'identity'}, 'runtime identity handoff shape'
            record['checks'].append(dict(name='DERP-path', **prove_enforced_path(bridge, str(address), paired, path_diagnostic, identity['identity'], inbox)))
        finally:
            record['path_diagnostics'] = safe_path_diagnostics(path_diagnostic)
        # Each row starts failed BEFORE its operation, so transport/worker
        # exceptions still name the exact sub-check without exporting errors.
        exposure = {'final-aggregation': dict(outcome='failed')}

        def exposure_step(label: str) -> dict:
            row = dict(outcome='failed')
            exposure[label] = row
            return row

        post_proof = exposure_step('post-proof-allowed-200')
        allowed(paired, post_proof)
        post_proof['outcome'] = 'passed'
        health_before = exposure_step('health-before')
        before = control_get('/health')
        health_before.update(before)
        assert before['headscale_health'] and not before['daemon_shaped'], 'health-before Headscale fingerprint'
        health_before['outcome'] = 'passed'
        public_path = exposure_step('public-daemon-path-refusal')
        refusal = control_get(ROUTE)
        public_path.update(refusal)
        assert not refusal['daemon_shaped'], 'public daemon-shaped response'
        assert refusal['headscale_refusal'], 'public exact Headscale refusal'
        public_path['outcome'] = 'passed'
        probes = []
        for port in (18765, 18443, 8765):
            port_check = exposure_step('forbidden-port-' + str(port))
            probe = application_probe(port)
            port_check.update(probe)
            port_check['outcome'] = 'failed' if probe['reachable'] else 'passed'
            probes.append(probe)
        health_after = exposure_step('health-after')
        after = control_get('/health')
        health_after.update(after)
        assert after['headscale_health'] and not after['daemon_shaped'], 'health-after Headscale fingerprint'
        health_after['outcome'] = 'passed'
        assert all(not probe['reachable'] for probe in probes), 'public forbidden-port aggregation'
        exposure['final-aggregation']['outcome'] = 'passed'
        record['checks'].append(dict(name='public-exposure', control=before, daemon_path=refusal,
                                     control_after=after, probes=probes))
        record['status'] = 'CLIENT_CHECKS_PASSED_VM_CLEANUP_PENDING'
    except Exception as error:
        label = bridge.join_error if bridge is not None and record['phase'] in ('join-call', 'native-start', 'join-wait') else 'operation-failed'
        record['failure'] = dict(stage=record['phase'], exception_class=exception_class(error),
                                 error_class=label if label in {'operation-failed', 'worker-failed', 'worker-deadline', 'worker-response-unavailable'} else 'Unknown',
                                 worker_exception_class=bridge.worker_exception if bridge is not None else 'Unknown',
                                 return_code=error.returncode if isinstance(error, subprocess.CalledProcessError) else None)
        raise
    finally:
        record['phases'][-1]['elapsed_seconds'] = round(time.monotonic() - phase_started, 3)
        cleanup_errors = []
        if bridge is not None and bridge.derp_diagnostics is not None:
            record['derp_diagnostics'] = safe_derp_diagnostics(bridge.derp_diagnostics)
        if exposure is not None:
            record['exposure_diagnostics'] = safe_exposure_diagnostics(exposure)
        if 'derp_diagnostics' in record or 'path_diagnostics' in record:
            # Land the closed evidence in both public sinks before private
            # logs/state are removed; the final summary also records cleanup.
            try:
                (output / 'client-summary.json').write_text(json.dumps(record, indent=2) + '\n')
                if 'derp_diagnostics' in record:
                    print('M4 public DERP evidence ' + json.dumps(record['derp_diagnostics']), flush=True)
                if 'path_diagnostics' in record:
                    print('M4 public enforcement evidence ' + json.dumps(record['path_diagnostics']), flush=True)
                if 'exposure_diagnostics' in record:
                    print('M4 public exposure evidence ' + json.dumps(record['exposure_diagnostics']), flush=True)
            except Exception:
                cleanup_errors.append('derp-evidence-publication')
        if bridge is not None:
            record['worker_return_code_before_cleanup'] = bridge.process.poll()
            try:
                bridge.close()
            except Exception:
                cleanup_errors.append('client-reap')
            record['worker_return_code'] = bridge.process.poll()
        try:
            native_state_link(scratch, remove=True)
            record['native_state_link_absent'] = True
        except Exception:
            cleanup_errors.append('native-state-link-removal')
        try:
            shutil.rmtree(scratch)
        except Exception:
            cleanup_errors.append('private-scratch-removal')
        record['private_scratch_absent'] = not scratch.exists()
        if cleanup_errors:
            record['status'] = 'FAIL'
            record['cleanup_errors'] = cleanup_errors
        record['runner_return_code'] = 1 if record['status'] == 'FAIL' else 0
        (output / 'client-summary.json').write_text(json.dumps(record, indent=2) + '\n')
        print('M4 public client evidence ' + json.dumps({key: record[key] for key in
              ('phase', 'phases', 'failure', 'join_diagnostics', 'bridge_error_class', 'derp_diagnostics', 'path_diagnostics', 'exposure_diagnostics', 'runner_return_code',
               'worker_return_code_before_cleanup', 'worker_return_code') if key in record}), flush=True)
    assert not cleanup_errors, 'native client cleanup failed'
    print('Native client checks complete; host VM cleanup receipt still required', flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['prepare', 'run', 'cleanup'])
    parser.add_argument('--scratch', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()

    def stop(signum: int, frame: object) -> None:
        raise RuntimeError('runner stop requested')

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    # Workflow supplies fixed RUNNER_TEMP children, never user-provided paths.
    temporary = Path(os.environ['RUNNER_TEMP']).resolve()
    assert args.scratch.resolve() == temporary / 'managed-m4-private'
    assert args.output.resolve() == temporary / 'managed-m4-public'
    if args.phase == 'prepare':
        prepare(args.scratch, args.output)
    elif args.phase == 'run':
        run(args.scratch, args.output)
    else:
        native_state_link(args.scratch, remove=True)
        if args.scratch.exists():
            shutil.rmtree(args.scratch)
        assert not args.scratch.exists()
        print('M4 runner private scratch absent')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('M4 runner FAILED; exception_class=' + exception_class(error) + '; private exception withheld', file=sys.stderr)
        sys.exit(1)

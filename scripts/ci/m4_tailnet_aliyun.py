#!/usr/bin/env python3
"""M4 host-side Cloud Assistant orchestration; Linux client is diagnostic only.

Run only as a persistent job, at a clean published head. See README-m4-tailnet.md.
The host Aliyun profile never leaves this host. No system Tailscale is used.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import time

from m3_reboot_aliyun import CloudAssistant

from m4_tailnet_client import Bridge, DiagnosticComplete, build_archive, control_get, exception_class, worker
from m4_tailnet_control import HostedRun
from m4_tailnet_runner import decrypt

BASE = "/var/lib/happyranch-m4"
INSTANCE = "i-j6c1pwjqygzrccal4mzh"
PUBLIC_IP = "47.238.219.200"


class M4CloudAssistant(CloudAssistant):
    """Retain original invocation ownership beyond the shared poller's budget."""

    def __init__(self, instance: str, region: str, output: Path) -> None:
        super().__init__(instance, region)
        self.output = output
        self.invocations: list[dict] = []

    def checkpoint(self) -> None:
        (self.output / 'invocations.json').write_text(json.dumps(self.invocations, indent=2) + '\n')

    def unsettled(self) -> list[dict]:
        return [row for row in self.invocations if not row['terminal']]

    def api(self, operation: str, *args: str, timeout: float = 45) -> dict:
        if operation == 'RunCommand':
            # Persist uncertainty BEFORE issue; a lost response is never replayed.
            row = dict(invoke_id=None, terminal=False)
            self.invocations.append(row)
            self.checkpoint()
        response = super().api(operation, *args, timeout=timeout)
        if operation == 'RunCommand':
            invocation = response.get('InvokeId')
            if not isinstance(invocation, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', invocation):
                raise RuntimeError('Cloud Assistant invocation identity unavailable')
            row['invoke_id'] = invocation
            self.checkpoint()
        elif operation == 'DescribeInvocationResults':
            invocation = args[args.index('--InvokeId') + 1]
            rows = response.get('Invocation', {}).get('InvocationResults', {}).get('InvocationResult', [])
            rows = [item for item in rows if item.get('InstanceId') == self.instance]
            if len(rows) == 1 and rows[0].get('InvokeRecordStatus') in {'Finished', 'Failed', 'Stopped'}:
                for row in self.unsettled():
                    if row['invoke_id'] == invocation:
                        row.update(terminal=True, terminal_state=rows[0]['InvokeRecordStatus'])
                        self.checkpoint()
        return response

    def run(self, command: str, timeout: int = 120) -> tuple[int, str]:
        if self.unsettled():
            raise RuntimeError('previous Cloud Assistant invocation is unsettled; no new command issued')
        return super().run(command, timeout=timeout)

    def settle(self) -> bool:
        # Observation only: no StopInvocation permission or mutation replay.
        deadline = time.monotonic() + 60
        while pending := self.unsettled():
            if any(row['invoke_id'] is None for row in pending):
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            try:
                self.api('DescribeInvocationResults', '--InvokeId', pending[0]['invoke_id'], timeout=min(15, remaining))
            except Exception:
                pass  # Controlled uncertainty is recorded; raw cloud text stays private.
            if self.unsettled():
                time.sleep(min(1, max(0, deadline - time.monotonic())))
        return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", choices=[INSTANCE], default=INSTANCE)
    parser.add_argument("--region", choices=["cn-hongkong"], default="cn-hongkong")
    parser.add_argument("--sha")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--scratch", type=Path)
    parser.add_argument("--clean-state-only", action="store_true")
    parser.add_argument("--runner-run", type=int, help="Coordinate this exact main-only macOS workflow run")
    parser.add_argument("--premerge-vm", action="store_true", help="VM prepare/encrypted test-key handoff/cleanup only; no outside client")
    parser.add_argument("--diagnose-only", action="store_true", help="One prepare/prejoin/join/cleanup run; no product acceptance")
    parser.add_argument("--remove-cert-cache", action="store_true", help="Explicit final decommission after clean-state; never part of a pass")
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--worker-state", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker(args.worker, args.worker_state)
        return
    if not __debug__:
        parser.error('Python optimization disables acceptance assertions; unset PYTHONOPTIMIZE')
    modes = sum(bool(value) for value in (args.runner_run, args.premerge_vm, args.diagnose_only, args.clean_state_only, args.remove_cert_cache))
    if modes != 1:
        parser.error('choose exactly one explicit mode; Linux acceptance has been retired')
    if not args.sha or not re.fullmatch(r"[0-9a-f]{40}", args.sha) or not args.output:
        parser.error('full --sha and --output are required')
    root = Path(__file__).resolve().parents[2]
    if subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip() != args.sha:
        parser.error('local source head differs from --sha')
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=root, text=True).strip():
        parser.error('requires clean source')
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    ca = M4CloudAssistant(args.instance, args.region, args.output)
    record: dict = {"sha": args.sha, "instance": args.instance, "outside_client": "native macOS workflow" if args.runner_run else "none: VM-only premerge" if args.premerge_vm else "Linux diagnostic only",
                    "verdict": "INCOMPLETE", "phases": [], "checks": [], "streams": [], "invocations": ca.invocations, "control_waits": []}

    def security_group() -> list[dict]:
        response = ca.api('DescribeSecurityGroupAttribute', '--SecurityGroupId', 'sg-j6c9rilzduc2xjhzmc3q', '--Direction', 'ingress')
        rules = [{key: row.get(key) for key in ('IpProtocol', 'PortRange', 'SourceCidrIp', 'Policy')}
                 for row in response['Permissions']['Permission']]
        for rule in rules:
            rule['IpProtocol'] = str(rule['IpProtocol']).lower()
        expected = [dict(IpProtocol='tcp', PortRange='443/443', SourceCidrIp='0.0.0.0/0', Policy='Accept'),
                    dict(IpProtocol='udp', PortRange='3478/3478', SourceCidrIp='0.0.0.0/0', Policy='Accept')]
        assert sorted(rules, key=str) == sorted(expected, key=str), 'VM security group differs from approved two-port ingress'
        return rules

    def save(name: str, data: bytes) -> None:
        record['streams'].append(dict(file=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
        if len(data) > 256 * 1024 - 128:
            (args.output / (name + '.gz')).write_bytes(gzip.compress(data))
        bound = 128 * 1024 - 64
        excerpt = data if len(data) <= 2 * bound else data[:bound] + b'\n... head/tail excerpt ...\n' + data[-bound:]
        (args.output / name).write_bytes(excerpt)
        if sum(p.stat().st_size for p in args.output.iterdir() if p.is_file()) > 45_000_000:
            raise RuntimeError('task output budget exceeded')

    def phase(name: str, private: bool = False) -> str:
        command = (f'cd {BASE}/repo && test "$(git rev-parse HEAD)" = {args.sha} && '
                   f'test -z "$(git status --porcelain)" && M4_AUTHORIZED_VM=yes GOMAXPROCS=2 GOFLAGS=-p=2 '
                   f'PATH=/var/lib/happyranch-m3/tools/bin:/usr/local/go/bin:/usr/sbin:/usr/bin:/sbin:/bin '
                   f'bash scripts/ci/m4_tailnet.sh {name}')
        if not private:
            command = f'mkdir -p {BASE}/logs; {command} > {BASE}/logs/{name}.log 2>&1; rc=$?; tail -40 {BASE}/logs/{name}.log; exit "$rc"'
        rc, output = ca.run(command, timeout=1200 if name == 'prepare' else 330)
        record['phases'].append(dict(name=name, exit_code=rc))
        if not private:
            save(name + '.excerpt.txt', (f'exit={rc}\n' + output).encode())
            print(f'M4 phase {name}: exit={rc}', flush=True)
        if rc:
            raise RuntimeError(f'VM {name} failed, exit={rc}')
        return output

    def collect() -> None:
        rc, output = ca.run(f'tar -C {BASE}/logs -czf {BASE}/plain-logs.tgz . && stat -c %s {BASE}/plain-logs.tgz')
        if rc or not output.strip().isdigit() or int(output) > 15_000_000:
            raise RuntimeError('plain log archive unavailable/oversize')
        total = int(output)
        chunks = []
        for offset in range(0, total, 12000):
            rc, text = ca.run(f'dd if={BASE}/plain-logs.tgz bs=1 skip={offset} count=12000 status=none | base64 -w0')
            if rc:
                raise RuntimeError('plain log transfer failed')
            chunks.append(base64.b64decode(text, validate=True))
        data = b''.join(chunks)
        if len(data) != total:
            raise RuntimeError('incomplete plain log transfer')
        expanded = 0
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as bundle:
            for member in bundle:
                if member.isdir():
                    continue
                name = member.name.removeprefix('./')
                if not member.isfile() or not re.fullmatch(r'[a-zA-Z0-9_.-]+', name):
                    raise RuntimeError('unsafe plain log member')
                expanded += member.size
                if expanded > 35_000_000:
                    raise RuntimeError('expanded log budget exceeded')
                stream = bundle.extractfile(member)
                assert stream is not None
                save('vm-' + name, stream.read())

    if args.clean_state_only or args.remove_cert_cache:
        security_group()
        phase('remove-cert-cache' if args.remove_cert_cache else 'clean-state')
        (args.output / 'clean-state-receipt.json').write_text(json.dumps(record, indent=2) + '\n')
        return
    if not args.scratch:
        parser.error('--scratch is required for this operation')
    scratch = args.scratch.resolve()
    if scratch.exists() or args.output == scratch or args.output in scratch.parents or scratch in args.output.parents:
        parser.error('use a fresh scratch directory separate from output')
    scratch.mkdir(parents=True, mode=0o700)
    bridge: Bridge | None = None
    prepared = False
    hosted: HostedRun | None = None
    try:
        record['security_group_before'] = security_group()
        if args.runner_run:
            hosted = HostedRun(args.runner_run, args.sha)
            record['control_waits'] = hosted.waits
            request = hosted.public_request()
            public_bytes = request['public_key'].encode('ascii')
            record['public_request'] = request
        elif args.diagnose_only:
            library = build_archive(root, scratch)
            record['native'] = dict(go_version=(library.parent / 'build-0.log').read_text().strip(),
                                    archive_sha256=hashlib.sha256(library.with_suffix('.a').read_bytes()).hexdigest())
        # Reuse only public source/build caches; no profile/credential transfer.
        command = f'''set -euo pipefail
mkdir -p {BASE}
test ! -e {BASE}/owned-sha
test ! -L {BASE}/owned-sha
if [ ! -d {BASE}/repo ]; then git clone --filter=blob:none https://github.com/t-benze/happyranch {BASE}/repo > {BASE}/bootstrap.log 2>&1; fi
git -C {BASE}/repo fetch origin {args.sha} >> {BASE}/bootstrap.log 2>&1
test -z "$(git -C {BASE}/repo status --porcelain)"
git -C {BASE}/repo checkout --detach {args.sha} >> {BASE}/bootstrap.log 2>&1
test "$(git -C {BASE}/repo rev-parse HEAD)" = {args.sha}
rm -rf -- {BASE}/logs
mkdir -p {BASE}/logs
echo exact-source={args.sha}
'''
        rc, output = ca.run(command, timeout=600)
        save('bootstrap.excerpt.txt', (f'exit={rc}\n' + output).encode())
        if rc:
            raise RuntimeError('public exact-head bootstrap failed')
        phase('prepare')
        prepared = True
        if not hosted:
            private_key = scratch / 'recipient.key'
            public_key = scratch / 'recipient.pem'
            subprocess.run(['openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:3072', '-out', str(private_key)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            private_key.chmod(0o600)
            subprocess.run(['openssl', 'pkey', '-in', str(private_key), '-pubout', '-out', str(public_key)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            public_bytes = public_key.read_bytes()
        public = base64.b64encode(public_bytes).decode()
        rc, _ = ca.run(f'umask 077; printf %s {shlex.quote(public)} | base64 -d > {BASE}/cell/recipient.pem')
        if rc:
            raise RuntimeError('recipient public-key transfer failed')

        def secret(kind: str) -> str:
            ciphertext = base64.b64decode(phase(kind, private=True).strip(), validate=True)
            result = subprocess.run(['openssl', 'pkeyutl', '-decrypt', '-inkey', str(private_key),
                                     '-pkeyopt', 'rsa_padding_mode:oaep', '-pkeyopt', 'rsa_oaep_md:sha256'],
                                    input=ciphertext, capture_output=True, check=False)
            if result.returncode:
                raise RuntimeError('encrypted credential handoff failed')
            return result.stdout.decode().strip()

        if args.premerge_vm:
            assert json.loads(phase('nodes')) == [], 'premerge cell has unexpected membership'
            record['checks'].append(dict(name='VM-empty-membership', nodes=[]))
            payload = json.loads(phase('handoff-check', private=True))
            assert set(payload) == {'enroll', 'pair-test'}
            private = private_key.read_bytes()
            # Test-only recipient; observe decryption/shape without exporting a
            # plaintext key/code or attempting any client join/redemption.
            enrollment = decrypt(private, payload['enroll'])
            pairing = decrypt(private, payload['pair-test'])
            assert enrollment and len(enrollment) <= 128 and not any(c.isspace() for c in enrollment), 'enrollment shape'
            assert re.fullmatch(r'[A-Z0-9]{8}', pairing), 'pairing shape'
            record['checks'].append(dict(name='VM-OAEP-test-public-key', enrollment_decrypted=True,
                                         pairing_decrypted=True, plaintext_exported=False,
                                         enrollment_ttl_requested_seconds=600, enrollment_reusable_requested=False, pairing_ttl_observed_max_seconds=300))
            del enrollment, pairing, private
        elif hosted:
            hosted.coordinate(phase, record)
        else:
            for endpoint, path in [('health', '/health'), ('key', '/key?v=106')]:
                check = dict(name='prejoin-' + endpoint, **control_get(path))
                record['checks'].append(check)
                assert check['status'] == 200
            bridge = Bridge(library, scratch)
            try:
                joined = bridge.call('join', key=secret('enroll'))
            finally:
                record['join_diagnostics'] = bridge.join_diagnostics
            record['checks'].append(dict(name='diagnostic-joined', **joined))
            record['verdict'] = 'DIAGNOSTIC_JOINED'
            raise DiagnosticComplete
        record['verdict'] = 'CHECKS_PASSED_CLEANUP_PENDING'
    except DiagnosticComplete:
        pass
    except Exception as error:
        # All exception text emitted by this driver is controlled. Native/cloud
        # raw command diagnostics and response bodies are never exported here.
        record['verdict'] = 'FAIL'
        record['failure_type'] = exception_class(error)
        record['failure'] = 'operation failed; private exception withheld'
        raise
    finally:
        cleanup_errors = []
        remote_uncertain = False

        def settled_for_cleanup() -> bool:
            nonlocal remote_uncertain
            if not remote_uncertain and not ca.settle():
                remote_uncertain = True
                cleanup_errors.append('remote-invocation-unsettled')
            return not remote_uncertain

        if bridge is not None:
            try:
                bridge.close()
            except Exception:
                cleanup_errors.append('outside-client-reap')
        if settled_for_cleanup() and not prepared:
            try:
                rc, marker = ca.run(f'test -f {BASE}/owned-sha && cat {BASE}/owned-sha', timeout=15)
                prepared = rc == 0 and marker.strip() == args.sha
            except Exception:
                cleanup_errors.append('prepare-ownership-unknown')
        if settled_for_cleanup() and prepared:
            for name in ('logs', 'purge', 'teardown', 'clean-state'):
                if not settled_for_cleanup():
                    break
                try:
                    phase(name)
                except Exception:
                    cleanup_errors.append(name)
            try:
                record['security_group_after'] = security_group()
            except Exception:
                cleanup_errors.append('final-SG')
            try:
                if settled_for_cleanup():
                    collect()
            except Exception:
                cleanup_errors.append('plain-log-collection')
        settled_for_cleanup()
        try:
            states = ca.api('DescribeInstanceStatus', '--InstanceId.1', INSTANCE)['InstanceStatuses']['InstanceStatus']
            assert len(states) == 1 and states[0]['Status'] == 'Running'
            record['vm_status'] = 'Running'
        except Exception:
            cleanup_errors.append('VM-Running-observation')
        if hosted and record['verdict'] != 'CHECKS_PASSED_CLEANUP_PENDING':
            try:
                hosted.cancel_if_running()
            except Exception:
                cleanup_errors.append('native-run-cancel-uncertain')
        if cleanup_errors:
            record['cleanup_errors'] = cleanup_errors
            record['verdict'] = 'FAIL'
        elif record['verdict'] == 'CHECKS_PASSED_CLEANUP_PENDING':
            record['verdict'] = 'VM_PREMERGE_PASS' if args.premerge_vm else 'PASS'
        # Erase every task-generated private key/native-state/raw log before the
        # persistent job returns. Only bounded public records remain in output.
        try:
            shutil.rmtree(scratch)
        except Exception:
            record.setdefault('cleanup_errors', []).append('private-scratch-removal')
            record['verdict'] = 'FAIL'
        if remote_uncertain:
            record['verdict'] = 'FAIL/UNCERTAIN'
            record['clean_state_clearance'] = False
        if args.diagnose_only:
            record['mode'] = 'diagnostic-only; no product acceptance'
        (args.output / 'summary.json').write_text(json.dumps(record, indent=2) + '\n')
    if args.diagnose_only and record['verdict'] == 'DIAGNOSTIC_JOINED':
        print('Diagnostic join completed; product acceptance NOT RUN', flush=True)
        return
    if record['verdict'] not in ('PASS', 'VM_PREMERGE_PASS'):
        raise RuntimeError('M4 failed; see controlled summary and cleanup receipts')
    print(record['verdict'] + '; see native/VM scope in summary', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Tracebacks can serialize a credential-bearing local exception.
        print('M4 FAILED: inspect bounded summary; no native exception exported', file=sys.stderr)
        sys.exit(1)

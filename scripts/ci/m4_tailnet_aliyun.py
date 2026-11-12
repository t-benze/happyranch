#!/usr/bin/env python3
"""Authorized M4 separate-network acceptance through the unchanged app C archive.

Run only as a persistent job, at a clean published head. See README-m4-tailnet.md.
The host Aliyun profile never leaves this host. No system Tailscale is used.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import gzip
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import re
import select
import shlex
import shutil
import socket
import ssl
import subprocess
import sys
import tarfile
import time

from m3_reboot_aliyun import CloudAssistant

BASE = "/var/lib/happyranch-m4"
INSTANCE = "i-j6c1pwjqygzrccal4mzh"
PUBLIC_IP = "47.238.219.200"
CONTROL = "https://47-238-219-200.sslip.io"
ROUTE = "/api/v1/orgs/m2-synthetic/teams"
EXPECTED = {"teams": [{"name": "default", "manager": None, "workers": [],
                       "manager_kind": "human", "human_manager": "founder", "is_default": True}]}


def worker(library: Path, state: Path) -> None:
    """Private pipe protocol. Never echo input, credentials, or native errors."""
    lib = ctypes.CDLL(str(library))
    lib.tsnet_init.argtypes = [ctypes.c_char_p] * 3
    lib.tsnet_conn_dial.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    lib.tsnet_conn_read.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    lib.tsnet_conn_write.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    lib.tsnet_conn_close.argtypes = [ctypes.c_int]
    lib.tsnet_local_addr.restype = ctypes.c_void_p
    lib.tsnet_free_string.argtypes = [ctypes.c_void_p]

    class Stream(io.RawIOBase):
        def __init__(self, handle: int) -> None:
            super().__init__()
            self.handle = handle

        def readable(self) -> bool:
            return True

        def readinto(self, buffer: bytearray) -> int:
            native = ctypes.create_string_buffer(len(buffer))
            size = lib.tsnet_conn_read(self.handle, native, len(buffer))
            if size < 0:
                raise RuntimeError("transport read failed")
            buffer[:size] = native.raw[:size]
            return size

    class Connection:
        def __init__(self, handle: int) -> None:
            self.handle = handle

        def makefile(self, mode: str) -> io.BufferedReader:
            return io.BufferedReader(Stream(self.handle))

    for line in sys.stdin:
        try:
            message = json.loads(line)
            action = message["action"]
            if action == "join":
                assert lib.tsnet_init(message["key"].encode(), CONTROL.encode(), b"synthetic-m4-outside") == 0
                assert lib.tsnet_start() == 0
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    pointer = lib.tsnet_local_addr()
                    if pointer:
                        address = ctypes.string_at(pointer).decode()
                        lib.tsnet_free_string(pointer)
                        break
                    time.sleep(0.2)
                else:
                    raise RuntimeError("join deadline")
                result = {"joined": True, "address": address}
            elif action == "request":
                handle = lib.tsnet_conn_dial(b"tcp", (message["address"] + ":443").encode(), 15000)
                if handle < 0:
                    raise RuntimeError("dial refused")
                try:
                    data = message.get("data", "").encode()
                    headers = [f'{message["method"]} {message["path"]} HTTP/1.1',
                               'Host: home', 'Connection: close', f'Content-Length: {len(data)}']
                    if message.get("credential") is not None:
                        headers.append("X-HappyRanch-Device-Credential: " + message["credential"])
                    request = ("\r\n".join(headers) + "\r\n\r\n").encode() + data
                    while request:
                        size = lib.tsnet_conn_write(handle, request, len(request))
                        if size <= 0:
                            raise RuntimeError("write failed")
                        request = request[size:]
                    response = http.client.HTTPResponse(Connection(handle))
                    response.begin()
                    raw = response.read(1_000_001)
                    assert len(raw) <= 1_000_000
                    result = {"status": response.status, "body": json.loads(raw)}
                    response.close()
                finally:
                    lib.tsnet_conn_close(handle)
            elif action == "derp-proof":
                # The existing upstream diagnostic knob delays log upload. Read
                # only backend log files, and export a strict non-secret subset.
                regions: set[int] = set()
                disabled = False
                for p in state.rglob('tailscaled.log*.txt'):
                    assert p.stat().st_size <= 8_000_000, 'native log budget'
                    for entry in p.read_text(errors='replace').splitlines():
                        try:
                            text = json.loads(entry).get('text', '')
                        except (ValueError, AttributeError):
                            continue
                        for region in re.findall(r'magicsock: derp-([0-9]+) connected;', text):
                            regions.add(int(region))
                        if re.search(r'disabled udp[46]? per TS_DEBUG_ALWAYS_USE_DERP', text):
                            disabled = True
                assert regions == {999}, 'missing/wrong selected DERP region'
                assert disabled, 'missing client UDP-disable evidence'
                result = {"selected_regions": sorted(regions), "udp_disabled": disabled,
                          "source": "unchanged tsnet backend filch logs; strict projection"}
            else:
                raise RuntimeError("unknown worker operation")
            print(json.dumps(result), flush=True)
        except Exception:
            # Even JSON/native exceptions may contain response credential bytes.
            print('{"worker_error":"operation_failed"}', flush=True)


class Bridge:
    def __init__(self, library: Path, scratch: Path) -> None:
        self.state = scratch / "client-state"
        self.state.mkdir(mode=0o700)
        environment = dict(os.environ, XDG_CONFIG_HOME=str(self.state),
                           TS_DEBUG_ALWAYS_USE_DERP="true", TS_DEBUG_LOGTAIL_FLUSHDELAY="24h")
        for key in ("TS_AUTHKEY", "TS_AUTH_KEY", "TSNET_FORCE_LOGIN", "TS_DEBUGSYSPOLICY_AUTH_KEY"):
            if key in environment:
                raise RuntimeError("unset ambient tsnet auth variables before M4")
        self.stderr = (scratch / "client-private.stderr").open("wb")
        self.process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--worker", str(library), "--worker-state", str(self.state)],
            env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr, text=True,
        )

    def call(self, action: str, **fields: object) -> dict:
        assert self.process.stdin is not None and self.process.stdout is not None
        self.process.stdin.write(json.dumps(dict(action=action, **fields)) + "\n")
        self.process.stdin.flush()
        if not select.select([self.process.stdout], [], [], 100 if action == "join" else 35)[0]:
            raise RuntimeError(f"client {action} deadline exceeded")
        line = self.process.stdout.readline()
        try:
            result = json.loads(line)
        except ValueError:
            raise RuntimeError(f"client {action} response unavailable") from None
        if "worker_error" in result:
            raise RuntimeError(f"client {action} failed (private native detail not exported)")
        return result

    def close(self) -> None:
        # Do not flush upstream diagnostic logs on graceful Go shutdown. These
        # are disposable nodes; reap the entire embedded engine with its worker.
        self.process.kill()
        self.process.wait(timeout=15)
        self.stderr.close()
        shutil.rmtree(self.state)


def build_archive(root: Path, scratch: Path) -> Path:
    native = scratch / "native"
    native.mkdir()
    env = dict(os.environ, GOTOOLCHAIN="go1.23.12", CGO_ENABLED="1", GOOS="linux", GOARCH="amd64",
               GOMAXPROCS="2", GOFLAGS="-p=2")
    archive = native / "libtsnet.a"
    commands = [
        ["go", "version"],
        ["go", "build", "-mod=readonly", "-buildmode=c-archive", "-o", str(archive), "."],
        ["cc", "-shared", "-o", str(native / "libtsnet.so"), "-Wl,--whole-archive", str(archive),
         "-Wl,--no-whole-archive", "-pthread", "-ldl", "-lm"],
    ]
    for number, command in enumerate(commands):
        with (native / f"build-{number}.log").open('wb') as stream:
            result = subprocess.run(command, cwd=root / "app/mac/tsnet-bridge", env=env,
                                    stdout=stream, stderr=subprocess.STDOUT, timeout=900, check=False)
        if result.returncode:
            raise RuntimeError(f"unchanged archive build step {number} failed, exit={result.returncode}")
    version = (native / 'build-0.log').read_text().strip()
    if not version.endswith('go1.23.12 linux/amd64'):
        raise RuntimeError('incorrect effective Go toolchain')
    return native / "libtsnet.so"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", choices=[INSTANCE], default=INSTANCE)
    parser.add_argument("--region", choices=["cn-hongkong"], default="cn-hongkong")
    parser.add_argument("--sha")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--scratch", type=Path)
    parser.add_argument("--clean-state-only", action="store_true")
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--worker-state", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker(args.worker, args.worker_state)
        return
    if not __debug__:
        parser.error('Python optimization disables acceptance assertions; unset PYTHONOPTIMIZE')
    if not args.sha or not re.fullmatch(r"[0-9a-f]{40}", args.sha) or not args.output:
        parser.error('full --sha and --output are required')
    root = Path(__file__).resolve().parents[2]
    if subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip() != args.sha:
        parser.error('local source head differs from --sha')
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=root, text=True).strip():
        parser.error('requires clean source')
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    ca = CloudAssistant(args.instance, args.region)
    record: dict = {"sha": args.sha, "instance": args.instance, "outside_client": "ii: Linux unchanged app Go C archive",
                    "verdict": "INCOMPLETE", "phases": [], "checks": [], "streams": []}

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

    if args.clean_state_only:
        security_group()
        phase('clean-state')
        (args.output / 'clean-state-receipt.json').write_text(json.dumps(record, indent=2) + '\n')
        return
    if not args.scratch:
        parser.error('--scratch is required for a full pass')
    scratch = args.scratch.resolve()
    if scratch.exists() or args.output == scratch or args.output in scratch.parents or scratch in args.output.parents:
        parser.error('use a fresh scratch directory separate from output')
    scratch.mkdir(parents=True, mode=0o700)
    bridge: Bridge | None = None
    prepared = False
    try:
        record['security_group_before'] = security_group()
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
        private_key = scratch / 'recipient.key'
        public_key = scratch / 'recipient.pem'
        subprocess.run(['openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:3072', '-out', str(private_key)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        private_key.chmod(0o600)
        subprocess.run(['openssl', 'pkey', '-in', str(private_key), '-pubout', '-out', str(public_key)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        public = base64.b64encode(public_key.read_bytes()).decode()
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

        bridge = Bridge(library, scratch)
        joined = bridge.call('join', key=secret('enroll'))
        record['checks'].append(dict(name='outside-joined', **joined))
        phase('install')
        rows = json.loads(phase('nodes', private=True))
        home = [row for row in rows if row['name'] == 'home-sidecar-ci']
        assert len(rows) == 2 and len(home) == 1 and len(home[0]['ips']) == 1, 'wrong synthetic node membership'
        address = home[0]['ips'][0]

        def request(method: str, path: str, credential: str | None = None, data: str = '') -> dict:
            assert bridge is not None
            return bridge.call('request', address=address, method=method, path=path, credential=credential, data=data)

        def allowed(credential: str) -> None:
            result = request('GET', ROUTE, credential)
            assert result == {'status': 200, 'body': EXPECTED}, 'allowed real-daemon status/body mismatch'

        def pair(kind: str) -> str:
            result = request('POST', '/pair', data=secret(kind))
            assert result['status'] == 200, 'pair redemption failed'
            return result['body']['credential']

        paired = pair('pair-main')
        allowed(paired)
        record['checks'].append(dict(name='allowed-200', status=200, body=EXPECTED))
        for label, credential in [('absent', None), ('never-paired', 'hrpair_' + '0' * 32)]:
            allowed(paired)
            result = request('GET', ROUTE, credential)
            assert result == {'status': 403, 'body': {'error': 'identity_denied'}}, 'unpaired exact refusal mismatch'
            record['checks'].append(dict(name='unpaired-' + label, **result))
            allowed(paired)
        revoked = pair('pair-revoked')
        allowed(revoked)
        phase('revoke')
        deadline = time.monotonic() + 30
        time.sleep(0.5)
        while True:
            result = request('GET', ROUTE, revoked)
            if result == {'status': 403, 'body': {'error': 'identity_denied'}}:
                break
            assert result == {'status': 200, 'body': EXPECTED}, 'unexpected revocation response'
            if time.monotonic() >= deadline:
                raise RuntimeError('product defect: revoked credential still succeeds after reconciliation deadline')
            time.sleep(0.2)
        record['checks'].append(dict(name='revoked-refused', **result))
        allowed(paired)
        for method, path, category in [
            ('POST', '/api/v1/executor-binaries/register', 'method_denied'),
            ('POST', '/api/v1/runtime/custom-cli/m4-operation/commit', 'route_denied'),
            ('POST', '/api/v1/runtime/use', 'route_denied'),
            ('DELETE', '/api/v1/runtime/adapters/m4-adapter', 'method_denied'),
            ('DELETE', '/api/v1/orgs/m2-synthetic', 'route_denied'),
            ('GET', '/api/v1/auth/bootstrap', 'route_denied'),
        ]:
            allowed(paired)
            result = request(method, path, paired)
            assert result == {'status': 403, 'body': {'error': category}}, 'forbidden exact refusal mismatch'
            record['checks'].append(dict(name='forbidden-route', method=method, path=path, **result))
            allowed(paired)
        result = request('GET', '/api/v1/orgs', paired)
        assert result['status'] == 200 and [org['slug'] for org in result['body']['orgs']] == ['m2-synthetic']
        record['checks'].append(dict(name='DERP-path', **bridge.call('derp-proof')))
        # Pin the actual public IP even on this host's proxy/fake-IP DNS setup.
        for path, status in [('/health', 200), (ROUTE, 404)]:
            raw_socket = socket.create_connection((PUBLIC_IP, 443), timeout=10)
            connection = http.client.HTTPConnection('47-238-219-200.sslip.io', timeout=10)
            connection.sock = ssl.create_default_context().wrap_socket(raw_socket, server_hostname='47-238-219-200.sslip.io')
            try:
                connection.request('GET', path)
                response = connection.getresponse()
                assert response.status == status, 'public control health/daemon exclusion mismatch'
                assert len(response.read(8193)) <= 8192, 'public response exceeds budget'
            finally:
                connection.close()
        record['checks'].append(dict(name='public-control-TLS', health=200, daemon_route=404))
        for port in (18765, 18443, 8765):
            try:
                with socket.create_connection((PUBLIC_IP, port), timeout=5):
                    pass
            except (ConnectionRefusedError, TimeoutError):
                record['checks'].append(dict(name='no-public-daemon-exposure', port=port, outcome='refused-or-timeout'))
            else:
                raise RuntimeError(f'public daemon/connector port {port} accepts TCP')
        record['verdict'] = 'CHECKS_PASSED_CLEANUP_PENDING'
    except Exception as error:
        # All exception text emitted by this driver is controlled. Native/cloud
        # raw command diagnostics and response bodies are never exported here.
        record['verdict'] = 'FAIL'
        record['failure_type'] = type(error).__name__
        if isinstance(error, (RuntimeError, AssertionError)):
            record['failure'] = str(error)
        raise
    finally:
        cleanup_errors = []
        if bridge is not None:
            try:
                bridge.close()
            except Exception:
                cleanup_errors.append('outside-client-reap')
        if not prepared:
            try:
                rc, marker = ca.run(f'test -f {BASE}/owned-sha && cat {BASE}/owned-sha', timeout=15)
                prepared = rc == 0 and marker.strip() == args.sha
            except Exception:
                cleanup_errors.append('prepare-ownership-unknown')
        if prepared:
            for name in ('logs', 'purge', 'teardown', 'clean-state'):
                try:
                    phase(name)
                except Exception:
                    cleanup_errors.append(name)
            try:
                record['security_group_after'] = security_group()
                for port in (443, 18765, 18443, 8765):
                    try:
                        with socket.create_connection((PUBLIC_IP, port), timeout=5):
                            pass
                    except (ConnectionRefusedError, TimeoutError):
                        continue
                    raise RuntimeError('public test listener survives teardown')
                record['checks'].append(dict(name='outside-after-teardown', outcome='all-test-TCP-ports-refused-or-timeout'))
            except Exception:
                cleanup_errors.append('outside-final-exposure-or-SG')
            try:
                collect()
            except Exception:
                cleanup_errors.append('plain-log-collection')
        if cleanup_errors:
            record['cleanup_errors'] = cleanup_errors
            record['verdict'] = 'FAIL'
        elif record['verdict'] == 'CHECKS_PASSED_CLEANUP_PENDING':
            record['verdict'] = 'PASS'
        # Erase every task-generated private key/native-state/raw log before the
        # persistent job returns. Only bounded public records remain in output.
        try:
            shutil.rmtree(scratch)
        except Exception:
            record.setdefault('cleanup_errors', []).append('private-scratch-removal')
            record['verdict'] = 'FAIL'
        (args.output / 'summary.json').write_text(json.dumps(record, indent=2) + '\n')
    if record['verdict'] != 'PASS':
        raise RuntimeError('M4 failed; see controlled summary and cleanup receipts')
    print('M4 separate-network pass and shipped clean-state: PASS', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Tracebacks can serialize a credential-bearing local exception.
        print('M4 FAILED: inspect bounded summary; no native exception exported', file=sys.stderr)
        sys.exit(1)

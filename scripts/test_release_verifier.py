"""Fixture-only Linux verifier checks; no installed services or elevated commands.

Run: uv run --frozen pytest scripts/test_release_verifier.py -v
This standalone script venue does not collect the retired tests/ unit suite.
"""
from __future__ import annotations

import json
import os
import pwd
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager, redirect_stderr
from io import StringIO
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from runtime.remote_access import release_verifier as rv
from runtime.remote_access.forwarding import HttpLoopbackForwarder, LoopbackTarget
from runtime.remote_access.gateway import _forward_failure
from runtime.remote_access.httpd import serve_decision
from runtime.remote_access.installer import InstallerError, ManagedInstaller, managed_units
from runtime.remote_access.models import Decision
from runtime.remote_access.supervisor import ConnectorConfig, ConnectorConfigError, ConnectorSupervisor


class ReleaseVerifierTests(unittest.TestCase):
    def setUp(self):
        # Isolated fixture paths; never use the real daemon home.
        self.folder = tempfile.TemporaryDirectory(prefix='release-verifier-test-', dir='/tmp')
        self.addCleanup(self.folder.cleanup)
        self.home = Path(self.folder.name)
        self.port_file = self.home / 'daemon.port'
        self.port_file.write_text('12345\n')
        self.record = dict(user=pwd.getpwuid(os.getuid()).pw_name, uid=os.getuid(),
                           data_home=str(self.home), unit=rv.UNIT, fragment_path=rv.FRAGMENT)
        self.verifier = rv.ReleaseVerifier()
        self.addCleanup(patch.stopall)
        patch.object(self.verifier, '_configuration', return_value=(self.record, {8765})).start()
        self.properties = dict(MainPID=str(os.getpid()), ActiveState='active',
                               FragmentPath=rv.FRAGMENT, DropInPaths='')
        self.command = patch.object(rv.subprocess, 'run', side_effect=self.unit_output).start()

    def unit_output(self, *args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=''.join(f'{k}={v}\n' for k, v in self.properties.items()).encode())

    def test_closed_requests(self):
        for request in (b'', b'resolve', b'resolve /tmp/x\n', b'resolve\nresolve\n',
                        b'confirm root 123\n', b'confirm 0 1\n', b'confirm 1 65536\n',
                        b'confirm 1 2 extra\n', b'confirm -1 12\n'):
            with self.subTest(request=request):
                self.assertEqual(self.verifier.request(request), b'refused invalid_request\n')
        self.command.assert_not_called()

    def test_refusal_diagnostics_are_closed(self):
        with self.assertLogs(rv.__name__, level='WARNING') as logs:
            self.assertEqual(self.verifier.request(b'resolve /synthetic-secret\n'),
                             b'refused invalid_request\n')
            with patch.object(self.verifier, '_configuration', side_effect=OSError('synthetic-secret')):
                self.assertEqual(self.verifier.request(b'resolve\n'), b'refused evidence_unavailable\n')
        self.assertEqual([entry.getMessage() for entry in logs.records], [
            'release verification refused: invalid_request',
            'release verification refused: evidence_unavailable',
        ])

    def test_initial_readiness_diagnostics(self):
        from runtime.remote_access.managed_provider import ManagedProviderConfig
        from runtime.remote_access.readiness import GateResult, ReadinessReport

        cfg = ConnectorConfig(tenant_id='t', home_id='h', connector_id='c', system=True,
                              daemon_token_path='fixture-unused', policy_path='fixture-unused',
                              managed=ManagedProviderConfig())
        notifications = []
        supervisor = ConnectorSupervisor(config=cfg, notify_fn=notifications.append)
        report = ReadinessReport(False, {'daemon_loopback': GateResult(
            False, 'daemon_unavailable', 'synthetic-secret-detail')})
        stderr = StringIO()
        with patch.object(supervisor, 'readiness_report', return_value=report), \
                patch.object(supervisor, '_reconcile_revocation'), redirect_stderr(stderr):
            code = supervisor.run(max_iterations=2, wait_fn=lambda _: None)
        message = 'waiting for readiness; failing_gates=daemon_loopback:daemon_unavailable\n'
        self.assertEqual(code, 0)
        self.assertEqual(stderr.getvalue(), message)  # unchanged failures logged once
        self.assertEqual(notifications, ['STATUS=' + message] * 2)

    def test_credential_refusal_diagnostics_are_closed(self):
        from runtime.remote_access.credentials import CredentialUnavailable
        from runtime.remote_access.readiness import ConnectorReadiness

        provider = SimpleNamespace(read_bearer=lambda: None)
        readiness = ConnectorReadiness(
            daemon_port=None, release_verifier=rv.VerifierClient(),
            credential_provider=provider, policy=None, configured_identity=None,
            state_store=None,
        )
        cases = (
            ('CREDENTIALS_DIRECTORY not set (not running under LoadCredential=)', 'injection_not_configured'),
            ('daemon credential not injected', 'injection_missing'),
            ('daemon credential must not be a symlink', 'injection_symlink'),
            ('daemon credential unreadable', 'injection_unreadable'),
            ('daemon credential permissions too loose', 'injection_permissions'),
            ('daemon credential empty', 'injection_empty'),
            ('synthetic-secret /private/path', 'unavailable'),
        )
        for message, reason in cases:
            with self.subTest(reason=reason):
                with patch.object(provider, 'read_bearer', side_effect=CredentialUnavailable(message)), \
                        self.assertLogs('runtime.remote_access.readiness', level='WARNING') as logs:
                    gate = readiness._gate_credential_permissions()
                    self.assertFalse(gate.ok)
                    self.assertEqual(gate.category, 'credential_unreadable')
                self.assertEqual([entry.getMessage() for entry in logs.records],
                                 ['daemon credential refused: ' + reason])
        with patch.object(provider, 'read_bearer', side_effect=RuntimeError('synthetic-secret')), \
                self.assertLogs('runtime.remote_access.readiness', level='WARNING') as logs:
            self.assertFalse(readiness._gate_credential_permissions().ok)
        self.assertEqual([entry.getMessage() for entry in logs.records],
                         ['daemon credential refused: unavailable'])
        readiness._release_verifier = None
        with patch.object(provider, 'read_bearer', side_effect=CredentialUnavailable('synthetic-secret')), \
                self.assertNoLogs('runtime.remote_access.readiness', level='WARNING'):
            self.assertFalse(readiness._gate_credential_permissions().ok)

    def test_unsafe_port_files(self):
        for data in (b'', b'0', b'65536', b'-2', b'True', b' 123', b'1\n2', b'9' * 17):
            with self.subTest(data=data):
                self.port_file.write_bytes(data)
                expected = b'refused unsafe_file\n' if len(data) > 16 else b'refused invalid_port\n'
                self.assertEqual(self.verifier.request(b'resolve\n'), expected)
        self.port_file.unlink()
        self.port_file.symlink_to(self.home / 'missing')
        self.assertEqual(self.verifier.request(b'resolve\n'), b'refused evidence_unavailable\n')
        self.port_file.unlink()
        os.mkfifo(self.port_file)
        self.assertEqual(self.verifier.request(b'resolve\n'), b'refused unsafe_file\n')
        self.port_file.unlink()
        self.port_file.mkdir()
        self.assertEqual(self.verifier.request(b'resolve\n'), b'refused unsafe_file\n')
        self.command.assert_not_called()

    def test_wrong_owner_and_aliased_parent(self):
        self.record['uid'] = os.getuid() + 1
        self.assertEqual(self.verifier.request(b'resolve\n'), b'refused unsafe_file\n')
        self.record['uid'] = os.getuid()
        alias = self.home / 'alias'
        alias.symlink_to(self.home, target_is_directory=True)
        self.record['data_home'] = str(alias)
        self.assertEqual(self.verifier.request(b'resolve\n'), b'refused evidence_unavailable\n')
        self.command.assert_not_called()

    def test_excluded_ports(self):
        for excluded in ({8765}, {8765, 23456}):
            for port in excluded:
                with self.subTest(port=port):
                    self.port_file.write_text(str(port))
                    with patch.object(self.verifier, '_configuration', return_value=(self.record, excluded)):
                        self.assertEqual(self.verifier.request(b'resolve\n'), b'refused excluded_port\n')
        self.command.assert_not_called()

    def test_unit_identity(self):
        for key, value in (('MainPID', '0'), ('MainPID', '-1'), ('ActiveState', 'inactive'),
                           ('FragmentPath', '/home/user/.config/systemd/user/happyranch-daemon.service'),
                           ('DropInPaths', '/etc/systemd/user/happyranch-daemon.service.d/override.conf')):
            with self.subTest(key=key):
                with patch.dict(self.properties, {key: value}):
                    reply = self.verifier.request(b'resolve\n')
                    self.assertIn(reply, (b'refused unit_identity\n', b'refused unit_pid\n'))
        args = self.command.call_args.args[0]
        self.assertEqual(args[:5], ['/usr/bin/systemctl', '--user', '-M', self.record['user'] + '@.host', 'show'])
        with self.assertRaises(rv.VerificationRefused) as error:
            self.verifier._unit(self.record | {'uid': os.getuid() + 1})
        self.assertEqual(error.exception.reason, 'process_owner')

    @contextmanager
    def daemon(self):
        observed = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                observed.append(self.headers.get('Authorization'))
                self.send_response(200)
                self.send_header('Content-Length', '2')
                self.end_headers()
                self.wfile.write(b'ok')
            def log_message(self, *args):
                pass
        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.port_file.write_text(str(server.server_port))
        try:
            yield server.server_port, observed
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_live_resolve_and_exact_confirm(self):
        with self.daemon() as (port, observed):
            self.assertEqual(self.verifier.request(b'resolve\n'), f'port {port}\n'.encode())
            with socket.create_connection(('127.0.0.1', port)) as client:
                local_port = client.getsockname()[1]
                self.assertEqual(self.verifier.request(f'confirm {local_port} {port}\n'.encode()), b'ok\n')
                self.assertEqual(self.verifier.request(f'confirm {local_port + 1} {port}\n'.encode()),
                                 b'refused socket_not_owned\n')
            self.assertEqual(observed, [])  # handshake/confirm sent no HTTP/bearer

    def test_squatter_gets_no_bearer(self):
        # An exec child owns the listener; the purported service MainPID is this process.
        child = subprocess.Popen([sys.executable, '-u', '-c', '''
from http.server import HTTPServer, BaseHTTPRequestHandler
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  print('squatter_received_bearer=' + str(self.headers.get('Authorization') == 'Bearer synthetic-verifier-bearer'), flush=True)
  self.send_response(200); self.send_header('Content-Length','2'); self.end_headers(); self.wfile.write(b'ok')
 def log_message(self,*args): pass
server=HTTPServer(('127.0.0.1',0),Handler)
print(server.server_port,flush=True)
server.serve_forever()
'''], stdout=subprocess.PIPE, text=True)
        try:
            port = int(child.stdout.readline())
            self.port_file.write_text(str(port))
            client = rv.VerifierClient()
            with patch.object(client, '_exchange', side_effect=self.verifier.request):
                forwarder = HttpLoopbackForwarder(LoopbackTarget('127.0.0.1', port), verifier=client)
                refused = False
                try:
                    forwarder.forward_once('GET', '/api/v1/health', None, (), None, 'synthetic-verifier-bearer')
                except rv.VerificationRefused:
                    refused = True
        finally:
            child.terminate()
            child.wait(timeout=3)
            observation = child.stdout.read()
            child.stdout.close()
        self.assertEqual(observation, '', 'squatter received bearer/request bytes')
        self.assertTrue(refused)

    def test_confirm_precedes_bytes_and_no_cache(self):
        with self.daemon() as (port, observed):
            client = rv.VerifierClient()
            with patch.object(client, '_exchange', side_effect=self.verifier.request) as exchange:
                forwarder = HttpLoopbackForwarder(LoopbackTarget('127.0.0.1', 0), verifier=client)
                for _ in range(2):
                    response = forwarder.forward_once('GET', '/api/v1/health', None, (), None, 'synthetic-verifier-bearer')
                    self.assertEqual(response.body, b'ok')
                self.assertEqual([call.args[0].split()[0] for call in exchange.call_args_list],
                                 [b'resolve', b'confirm', b'resolve', b'confirm'])
                with patch.object(client, 'confirm', side_effect=rv.VerificationRefused('socket_not_owned')):
                    with self.assertRaises(rv.VerificationRefused):
                        forwarder.open_stream('GET', '/api/v1/health', None, (), None, 'synthetic-verifier-bearer', 's')
                self.assertEqual(len(observed), 2)
                self.assertEqual(observed, ['Bearer synthetic-verifier-bearer'] * 2)

    def test_port_or_process_changes_refused(self):
        with self.daemon() as (port, _):
            self.assertEqual(self.verifier.request(f'confirm 12345 {port % 65535 + 1}\n'.encode()),
                             b'refused port_changed\n')
            with patch.object(self.verifier, '_unit', side_effect=[(os.getpid(), '1'), (os.getpid(), '2')]):
                self.assertEqual(self.verifier.request(b'resolve\n'), b'refused process_changed\n')

    def test_refusal_serves_503(self):
        outcome = _forward_failure(rv.VerificationRefused('socket_not_owned'))
        decision = Decision(allowed=False, audit_category=outcome.audit_category, audit_detail=outcome.detail, denied=outcome)
        with patch('runtime.remote_access.httpd.send_json') as send:
            serve_decision(object(), decision)
            self.assertEqual(send.call_args.args[1:], (503, {'error': 'daemon_unavailable'}))

    def test_installer_record_and_hardening(self):
        user = pwd.getpwuid(os.getuid())
        installer = ManagedInstaller(root=self.home, root_owner=(os.getuid(), os.getgid()))
        record = installer._release_record(user.pw_name, self.home)
        self.assertEqual(record['data_home'], str(self.home))
        for bad in (self.home / 'missing', Path('/etc/happyranch'), Path('/var/lib/happyranch'),
                    Path(user.pw_dir) / '.happyranch'):
            with self.subTest(path=bad), self.assertRaises(InstallerError):
                installer._release_record(user.pw_name, bad)
        units = managed_units()
        self.assertIn('SocketMode=0660', units['happyranch-release-verifier.socket'])
        self.assertIn('SocketGroup=happyranch', units['happyranch-release-verifier.socket'])
        service = units['happyranch-release-verifier.service']
        for directive in ('User=root', 'ProtectSystem=strict', 'NoNewPrivileges=yes',
                          'ProtectHome=read-only', 'RestrictAddressFamilies=AF_UNIX'):
            self.assertIn(directive, service)
        self.assertNotIn('PrivateNetwork=yes', service)
        connector = units['happyranch-connector.service']
        self.assertIn('User=happyranch', connector)
        self.assertIn('CapabilityBoundingSet=\n', connector)
        self.assertNotIn('CAP_SYS_PTRACE', connector)

    def test_provision_install_uninstall_record(self):
        from datetime import datetime, timedelta, timezone
        from runtime.remote_access.managed_provider import ManagedProviderConfig
        from dataclasses import asdict

        root = self.home / 'root'
        root.mkdir(mode=0o700)
        owner = (os.getuid(), os.getgid())
        user = pwd.getpwuid(os.getuid())
        service_user = SimpleNamespace(pw_uid=owner[0], pw_gid=owner[1],
                                       pw_dir='/nonexistent', pw_shell='/usr/sbin/nologin')
        class Manager:
            def __init__(self):
                self.calls = []
            def _run(self, args, **kwargs):
                raise AssertionError('unexpected external command')
            def daemon_reload(self): self.calls.append(('reload',))
            def enable(self, unit): self.calls.append(('enable', unit))
            def disable(self, unit): self.calls.append(('disable', unit))
            def start(self, unit, **kwargs): self.calls.append(('start', unit))
            def stop(self, unit, **kwargs): self.calls.append(('stop', unit))
            def show_properties(self, unit, properties):
                return dict(FragmentPath=str(root / 'etc/systemd/system' / unit),
                            DropInPaths='', NeedDaemonReload='no', ActiveState='active')
        manager = Manager()
        installer = ManagedInstaller(root=root, root_owner=owner, manager=manager,
                                     lookup_user=lambda name: service_user if name == 'happyranch' else user,
                                     lookup_group=lambda name: SimpleNamespace(gr_gid=owner[1]))
        cfg = ConnectorConfig(tenant_id='t', home_id='h', connector_id='c', system=True,
                              service_user='happyranch', service_group='happyranch',
                              daemon_token_path='/etc/happyranch/daemon.token',
                              state_path='/var/lib/happyranch-connector/trust-state.json',
                              policy_path='/etc/happyranch/policy.json', managed=ManagedProviderConfig(bind_port=18443))
        artifact = json.loads(Path('runtime/remote_access/route_policy.json').read_text())
        policy = dict(schema_version=1, artifact_version=int(artifact['version']),
                      issued_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
                      max_age_seconds=3600, revision=1, state='active', artifact=artifact)
        sidecar = dict(StateDir='/var/lib/happyranch-tsnet-sidecar', ControlURL='https://ci.invalid',
                       RoleIdentity='home-sidecar-ci', ExpectedPeer='ci-peer', DERPPolicy='private-only',
                       ListenAddr=':443', ConnectorAddr='127.0.0.1:18443')
        for name, value in (('connector', asdict(cfg)), ('sidecar', sidecar), ('policy', policy)):
            (self.home / (name + '.json')).write_text(json.dumps(value))
            (self.home / (name + '.json')).chmod(0o600)
        token = self.home / 'daemon.token'
        token.write_text('synthetic-verifier-bearer')
        token.chmod(0o600)
        installer.provision(connector_config=self.home / 'connector.json',
                            sidecar_config=self.home / 'sidecar.json', policy_file=self.home / 'policy.json',
                            release_user=user.pw_name, data_home=self.home)
        record = root / 'etc/happyranch/release-daemon.json'
        self.assertEqual(json.loads(record.read_text()), self.record)
        self.assertEqual(record.stat().st_mode & 0o777, 0o644)
        staged = json.loads((root / 'etc/happyranch/connector.json').read_text())
        self.assertEqual(staged['excluded_daemon_ports'], [8765])
        self.assertIsNone(staged.get('daemon_port'))
        enrollment = self.home / 'enrollment.key'
        enrollment.write_text('synthetic-enrollment-key')
        enrollment.chmod(0o600)
        installed = installer.install(enrollment_key_file=enrollment)
        self.assertEqual(set(installed), set(managed_units()))
        self.assertIn(('enable', 'happyranch-release-verifier.socket'), manager.calls)
        installer.uninstall(purge=True)
        self.assertFalse(record.exists())
        for unit in managed_units():
            self.assertFalse((root / 'etc/systemd/system' / unit).exists())
        self.assertIn(('disable', 'happyranch-release-verifier.socket'), manager.calls)

    def test_managed_config_has_no_fixed_port(self):
        cfg = ConnectorConfig(tenant_id='t', home_id='h', connector_id='c',
                              daemon_token_path='/etc/happyranch/daemon.token', policy_path='policy')
        from runtime.remote_access.managed_provider import ManagedProviderConfig
        cfg.system = True
        cfg.managed = ManagedProviderConfig()
        cfg.validate()
        cfg.daemon_port = 12345
        with self.assertRaises(ConnectorConfigError):
            cfg.validate()


if __name__ == '__main__':
    unittest.main()

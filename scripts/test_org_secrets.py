"""Focused org-secrets boundaries; no retired-suite imports or live services.

Run: <private-runtime-python> scripts/test_org_secrets.py -v
OSKeyring is replaced test-side before any production org is constructed.
All values stay in memory; assertion diagnostics contain booleans/codes only.
"""
from __future__ import annotations

import asyncio
import ast
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import hashlib
import json
import os
import shlex
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime.org_secrets.keyring import OSKeyring
from runtime.org_secrets.names import SecretError, reserved, validate_name, validate_value
from runtime.org_secrets.store import OrgSecrets, _STORES, store_for


class MemoryKeyring:
    def __init__(self) -> None:
        self.keys: dict[str, bytes] = {}
        self.locked = False
        self.creates = 0

    def read(self, backend: OSKeyring) -> bytes | None:
        if self.locked:
            raise SecretError('key_locked', 423)
        return self.keys.get(backend.account)

    def create(self, backend: OSKeyring) -> bytes:
        if self.locked:
            raise SecretError('key_locked', 423)
        if backend.account not in self.keys:
            self.keys[backend.account] = os.urandom(32)
            self.creates += 1
        return self.keys[backend.account]


def operation() -> str:
    return str(uuid.uuid4())


class SecretsCases(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix='org-secrets-')
        self.root = Path(self.tmp.name)
        self.backend = MemoryKeyring()
        self.stack = ExitStack()
        # Native children receive only test-safe inherited paths/locale.
        inherited = {key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR', 'TMP', 'TEMP', 'LANG', 'LC_CTYPE') if key in os.environ}
        self.stack.enter_context(patch.dict(os.environ, inherited, clear=True))
        self.stack.enter_context(patch.object(OSKeyring, 'read', lambda b: self.backend.read(b)))
        self.stack.enter_context(patch.object(OSKeyring, 'create', lambda b: self.backend.create(b)))
        self.sentinels = [uuid.uuid4().hex + '\n' + uuid.uuid4().hex]

    def tearDown(self) -> None:
        # Whole scratch oracle includes all org SQLite, workspace and temp files.
        needles = []
        for value in self.sentinels:
            needles.extend((value.encode(), json.dumps(value)[1:-1].encode()))
        matches = 0
        for path in self.root.rglob('*'):
            if path.is_file() and not path.is_symlink():
                matches += int(any(n in path.read_bytes() for n in needles))
        self.stack.close()
        self.tmp.cleanup()
        self.assertEqual(matches, 0, 'whole-scratch plaintext oracle failed')

    def store(self, name: str = 'alpha') -> OrgSecrets:
        root = self.root / name
        root.mkdir(exist_ok=True)
        return store_for(root)

    async def test_s01_s02_k01_k02_k03_storage_and_key_lifecycle(self) -> None:
        store = self.store()
        self.assertFalse(store.directory.exists())
        self.assertEqual(store.probe(), 'available')
        self.assertEqual(self.backend.creates, 0)
        replace = os.replace
        publication_modes = []
        def observe_publication(source, destination, **kwargs):
            publication_modes.append(stat.S_IMODE(Path(source).stat().st_mode))
            return replace(source, destination, **kwargs)
        with patch('runtime.org_secrets.store.os.replace', side_effect=observe_publication):
            store.mutate('create', 'SYNTH_NAME', self.sentinels[0], operation())
        self.assertEqual(publication_modes, [0o600])
        self.assertTrue(OrgSecrets(store.root).current_values()['SYNTH_NAME'] == self.sentinels[0])
        self.assertEqual(stat.S_IMODE(store.directory.stat().st_mode), 0o700)
        for path in store.directory.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.backend.locked = True
        self.assertTrue(store.current_values()['SYNTH_NAME'] == self.sentinels[0])
        restarted = OrgSecrets(store.root)
        self.assertEqual(restarted.metadata()['key_state'], 'locked')
        self.assertEqual(restarted.metadata()['secrets'][0]['name'], 'SYNTH_NAME')
        with self.assertRaises(SecretError) as caught:
            restarted.mutate('replace', 'SYNTH_NAME', self.sentinels[0], operation())
        self.assertEqual(caught.exception.code, 'key_locked')
        self.backend.locked = False
        self.assertEqual(restarted.probe(), 'available')
        original = (store.directory / 'store.json').read_bytes()
        for field in ('ciphertext', 'nonce', 'updated_at'):
            doc = json.loads(original)
            doc['records']['SYNTH_NAME'][field] = 'corrupted'
            (store.directory / 'store.json').write_text(json.dumps(doc))
            damaged = OrgSecrets(store.root)
            self.assertFalse(damaged.ready())
            self.assertEqual(self.backend.creates, 1)
        (store.directory / 'store.json').write_bytes(original)
        # Missing key over an encrypted store never permits creation/replacement.
        self.backend.keys.clear()
        missing = OrgSecrets(store.root)
        with self.assertRaises(SecretError):
            missing.mutate('create', 'SECOND', self.sentinels[0], operation())
        self.assertEqual(self.backend.creates, 1)
        missing.mutate('delete', 'SYNTH_NAME', None, operation())
        self.assertTrue(missing.ready())

    async def test_s03_concurrent_pristine_writers_and_write_failure(self) -> None:
        for same_name in (True, False):
            store = self.store('same' if same_name else 'distinct')
            def write(index: int) -> str:
                independent = OrgSecrets(store.root)
                try:
                    independent.mutate('create', 'ONE' if same_name else ('ONE' if index == 0 else 'TWO'), self.sentinels[0], operation())
                    return 'created'
                except SecretError as exc:
                    return exc.code
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(write, (0, 1)))
            self.assertEqual(sorted(results), ['created', 'secret_exists'] if same_name else ['created', 'created'])
            fresh = OrgSecrets(store.root)
            self.assertEqual(len(fresh.current_values()), 1 if same_name else 2)
        failed = self.store('failed-first')
        with patch.object(failed, '_write', side_effect=OSError('synthetic publication failure')):
            with self.assertRaises(SecretError):
                failed.mutate('create', 'ONE', self.sentinels[0], operation())
        count = self.backend.creates
        reopened = OrgSecrets(failed.root)
        reopened.mutate('create', 'ONE', self.sentinels[0], operation())
        self.assertEqual(self.backend.creates, count)
        before = (reopened.directory / 'store.json').read_bytes()
        with patch('runtime.org_secrets.store.os.replace', side_effect=OSError('synthetic atomic failure')):
            with self.assertRaises(SecretError):
                reopened.mutate('replace', 'ONE', 'synthetic replacement', operation())
        self.assertTrue((reopened.directory / 'store.json').read_bytes() == before)
        self.assertFalse(any(p.name.startswith('.store-') for p in reopened.directory.iterdir()))

    async def test_a02_a03_boundaries_and_independent_reserved_inventory(self) -> None:
        for name in ('', 'A' * 129, 'lower', 'NAME\n', 'NAME\r', 'NA\nME', 'A=B', 'A\0B'):
            with self.subTest(name_kind=len(name)), self.assertRaises(SecretError):
                validate_name(name)
        for name in ('A', 'A' * 128):
            self.assertEqual(validate_name(name), name)
        for value in ('', 'a' * 65537, 'é' * 32769, 'a\0b'):
            with self.subTest(size=len(value)), self.assertRaises(SecretError):
                validate_value(value)
        for value in ('a', 'a' * 65536, 'é' * 32768, ' \n '):
            self.assertTrue(validate_value(value) == value)
        from runtime.orchestrator.executors import _WORKSPACE_CACHE_ENV_DIRS, _ENV_VARS_TO_STRIP
        # Independent consumer inventory, not generated by reserved().
        names = set(_WORKSPACE_CACHE_ENV_DIRS) | set(_ENV_VARS_TO_STRIP) | {
            'PATH', 'HOME', 'TMPDIR', 'TMP', 'TEMP', 'PYTHONDONTWRITEBYTECODE',
            'HAPPYRANCH_TASK_TMP_ROOT', 'HAPPYRANCH_TASK_SCRATCH_MANIFEST',
            'HAPPYRANCH_ORG_SLUG', 'HAPPYRANCH_RUNTIME_SESSION_ID',
            'ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'CLAUDE_CONFIG_DIR', 'CODEX_HOME',
            'OPENCODE_CONFIG_CONTENT', 'PI_CODING_AGENT_DIR', 'npm_config_cache',
            'LC_ALL', 'LC_CTYPE', 'LANG', 'LANGUAGE', 'PYTHONWARNINGS',
            'PYTHONSTARTUP', 'PYTHONPATH', 'PYTHON_FUTURE_CONTROL', 'PYTHONHOME',
            'NODE_OPTIONS', 'NODE_EXTRA_CA_CERTS', 'NODE_FUTURE_CONTROL',
            'BASH_ENV', 'ENV', 'SHELLOPTS', 'BASHOPTS', 'PS4', 'IFS',
            'LD_DEBUG', 'LD_AUDIT', 'DYLD_PRINT_LIBRARIES', 'TZ', 'TMPDIR',
            'GIT_TRACE', 'GIT_FUTURE_CONTROL', 'NPM_CONFIG_FUTURE_CONTROL',
            'CURL_TRACE', 'OPENSSL_CONF', 'GCONV_PATH', 'GLIBC_TUNABLES',
            'MALLOC_TRACE', 'NSS_WRAPPER_DEBUGLEVEL', 'PS1', 'PS2', 'PROMPT', 'RPROMPT',
            'RUST_LOG', 'CARGO_FUTURE_CONTROL', 'BUN_OPTIONS', 'V8_OPTIONS',
            'GH_DEBUG', 'GITHUB_TOKEN', 'GOTOOLCHAIN', 'GODEBUG', 'SSH_ASKPASS',
            'GNUPGHOME', 'GNUTLS_DEBUG_LEVEL', 'LESSOPEN', 'PAGER', 'SSLKEYLOGFILE', 'DEBUG',
            'GO_FUTURE_CONTROL', 'GITPERLLIB', 'PERL5OPT'}
        for name in sorted(names):
            with self.subTest(reserved=name):
                self.assertTrue(reserved(name), 'launcher control must be reserved')
                with self.assertRaises(SecretError) as caught:
                    validate_name(name.upper())
                self.assertEqual(caught.exception.code, 'reserved_name')
        self.assertTrue(all(reserved(name) for name in names))
        # Environment names are an externally consumed shape contract. Detect
        # future literal runtime assignments independently of the reserve owner.
        repo = Path(__file__).resolve().parents[1]
        for source in ('runtime/orchestrator/executors.py', 'runtime/orchestrator/task_scratch.py',
                       'runtime/platform/linux_systemd.py', 'runtime/platform/macos_process_group.py',
                       'runtime/platform/isolation.py', 'packaging/linux/desktop/happyranch_window.py',
                       'runtime/adapters/claude.py', 'runtime/adapters/codex.py',
                       'runtime/adapters/opencode.py', 'runtime/adapters/pi.py'):
            tree = ast.parse((repo / source).read_text())
            if source.endswith('happyranch_window.py'):
                tree = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'child_environment')
            for node in ast.walk(tree):
                if (isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store)
                        and isinstance(node.value, ast.Name) and node.value.id in ('env', 'result', 'base_env')
                        and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str)):
                    self.assertTrue(reserved(node.slice.value), 'runtime environment assignment missing reservation')
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'update'
                        and isinstance(node.func.value, ast.Name) and node.func.value.id in ('env', 'result', 'base_env')):
                    for keyword in node.keywords:
                        if keyword.arg is not None:
                            self.assertTrue(reserved(keyword.arg), 'launcher environment keyword missing reservation')
                    for arg in node.args:
                        if isinstance(arg, ast.Dict):
                            self.assertTrue(all(reserved(k.value) for k in arg.keys
                                if isinstance(k, ast.Constant) and isinstance(k.value, str)))

    async def test_a01_a02_a05_crud_auth_and_lost_response_receipts(self) -> None:
        import httpx
        from scripts.test_agent_retirement import fixture
        async with fixture(self.root / 'fixture') as (org, client):
            path = '/api/v1/orgs/alpha/secrets'
            for method, suffix, body in [('GET', '', None), ('POST', '/key-status', None),
                    ('GET', '/operations/' + operation(), None), ('POST', '', {'name': 'SYNTH_NAME', 'value': self.sentinels[0]}),
                    ('PUT', '/SYNTH_NAME', {'value': self.sentinels[0]}), ('DELETE', '/SYNTH_NAME', None)]:
                for authority in ('', 'wrong'):
                    response = await client.request(method, path + suffix, json=body,
                        headers={'Authorization': 'Bearer ' + authority, 'X-Secret-Operation': operation()})
                    self.assertEqual(response.status_code, 401)
                    self.assertFalse((org.root / 'secrets/store.json').exists())
            for body in ({'name': 'SYNTH_NAME', 'value': self.sentinels[0], 'extra': True},
                         {'name': self.sentinels[0], 'value': {}}, {'name': 'NAME\n', 'value': self.sentinels[0]}):
                response = await client.post(path, json=body, headers={'X-Secret-Operation': operation()})
                self.assertEqual(response.status_code, 422)
                self.assertFalse(self.sentinels[0] in response.text)
                self.assertEqual(response.headers['cache-control'], 'no-store')
            for control in ('LC_ALL', 'PYTHONWARNINGS', 'NODE_EXTRA_CA_CERTS', 'GIT_TRACE', 'NPM_CONFIG_LOGLEVEL'):
                response = await client.post(path, json={'name': control, 'value': self.sentinels[0]},
                    headers={'X-Secret-Operation': operation()})
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()['detail']['code'], 'reserved_name')
                self.assertIn('launcher', response.json()['detail']['message'])
                self.assertFalse(self.sentinels[0] in response.text)
            for raw in (b'{', b'[]', b'null', b'{"name":"ONE","name":"TWO","value":"synthetic"}',
                        b'{"name":"ONE","value":NaN}'):
                response = await client.post(path, content=raw, headers={'X-Secret-Operation': operation()})
                self.assertEqual((response.status_code, response.json()['detail']['code']), (422, 'invalid_request'))
            for headers in ({}, {'X-Secret-Operation': 'not-a-uuid'},
                            [('X-Secret-Operation', operation()), ('X-Secret-Operation', operation())]):
                response = await client.post(path, json={'name': 'SYNTH_NAME', 'value': self.sentinels[0]}, headers=headers)
                self.assertEqual((response.status_code, response.json()['detail']['code']), (422, 'invalid_operation_id'))
            self.assertEqual(store_for(org.root).metadata()['secrets'], [])
            # A request lost before the ASGI app has no receipt and changes no state.
            before_commit = operation()
            class LostBeforeSend(httpx.AsyncBaseTransport):
                async def handle_async_request(inner, request):
                    raise httpx.ConnectError('synthetic loss before send')
            async with httpx.AsyncClient(transport=LostBeforeSend(), base_url='http://synthetic.test') as lost:
                with self.assertRaises(httpx.ConnectError):
                    await lost.post(path, json={'name': 'SYNTH_NAME', 'value': self.sentinels[0]},
                                    headers={'X-Secret-Operation': before_commit})
            self.assertEqual((await client.get(path + '/operations/' + before_commit)).json()['outcome'], 'unknown')
            self.assertEqual(store_for(org.root).metadata()['secrets'], [])
            # Same-owner authoritative operation read survives an intervening edit.
            for action, method, suffix, body in [('create', 'POST', '', {'name': 'SYNTH_NAME', 'value': self.sentinels[0]}),
                    ('replace', 'PUT', '/SYNTH_NAME', {'value': self.sentinels[0]}),
                    ('delete', 'DELETE', '/SYNTH_NAME', None)]:
                op = operation()
                class LostResponse(httpx.AsyncBaseTransport):
                    async def handle_async_request(inner, request):
                        await httpx.ASGITransport(app=client._transport.app).handle_async_request(request)
                        raise httpx.ReadError('synthetic response loss')
                async with httpx.AsyncClient(transport=LostResponse(), base_url='http://synthetic.test',
                        headers={'Authorization': 'Bearer synthetic-founder-token'}) as lost:
                    with self.assertRaises(httpx.ReadError):
                        await lost.request(method, path + suffix, json=body, headers={'X-Secret-Operation': op})
                await client.post(path, json={'name': 'INTERVENING_' + action.upper(), 'value': 'synthetic'},
                    headers={'X-Secret-Operation': operation()})
                observed = (await client.get(path + '/operations/' + op)).json()
                self.assertEqual((observed.get('outcome'), observed.get('action')), ('committed', action))
                self.assertFalse('value' in observed)
                reused = await client.request(method, path + suffix, json=body, headers={'X-Secret-Operation': op})
                self.assertEqual((reused.status_code, reused.json()['detail']['code']), (409, 'operation_reused'))
                unknown = (await client.get(path + '/operations/' + operation())).json()
                self.assertEqual(unknown['outcome'], 'unknown')
            store = store_for(org.root)
            store.mutate('create', 'SYNTH_NAME', self.sentinels[0], operation())
            duplicate = await client.post(path, json={'name': 'SYNTH_NAME', 'value': self.sentinels[0]}, headers={'X-Secret-Operation': operation()})
            self.assertEqual((duplicate.status_code, duplicate.json()['detail']['code']), (409, 'secret_exists'))
            self.backend.locked = True
            _STORES[org.root.resolve()] = OrgSecrets(org.root)
            self.assertEqual((await client.get(path)).json()['key_state'], 'locked')
            response = await client.put(path + '/SYNTH_NAME', json={'value': self.sentinels[0]}, headers={'X-Secret-Operation': operation()})
            self.assertEqual((response.status_code, response.json()['detail']['code']), (423, 'key_locked'))
            self.assertEqual((await client.delete(path + '/SYNTH_NAME', headers={'X-Secret-Operation': operation()})).status_code, 200)
            self.backend.locked = False
            self.assertEqual((await client.post(path + '/key-status')).json()['key_state'], 'available')

    async def test_d01_d03_d05_d06_current_child_env_and_budget(self) -> None:
        from runtime.orchestrator.executors import _callee_env
        from runtime.org_secrets.session import names_note
        store = self.store()
        workspace = store.root / 'workspaces/worker'
        workspace.mkdir(parents=True)
        with patch.dict(os.environ, {}, clear=True):
            self.assertNotIn('SYNTH_NAME', _callee_env(org_slug='alpha', workspace=workspace))
            for size in (1, 65536):
                value = 'a' if size == 1 else 's' * size
                action = 'replace' if size != 1 else 'create'
                store.mutate(action, 'SYNTH_NAME', value, operation())
                env = _callee_env(org_slug='alpha', workspace=workspace, session_id='synthetic-sid')
                self.assertTrue(env.get('SYNTH_NAME') == value)
                result = subprocess.run(['/bin/sh', '-c', '[ -n "$SYNTH_NAME" ] && echo set'], env=env,
                    cwd=workspace, text=True, capture_output=True, timeout=5)
                self.assertEqual((result.returncode, result.stdout), (0, 'set\n'))
            old = env.copy()
            store.mutate('delete', 'SYNTH_NAME', None, operation())
            self.assertNotIn('SYNTH_NAME', _callee_env(org_slug='alpha', workspace=workspace))
            self.assertIn('SYNTH_NAME', old)
            self.assertEqual(names_note('alpha', workspace), '')
            store.mutate('create', 'SYNTH_NAME', self.sentinels[0], operation())
            note = names_note('alpha', workspace)
            self.assertIn('SYNTH_NAME', note)
            self.assertFalse(self.sentinels[0] in note)
            with patch.dict(os.environ, {'SYNTH_AMBIENT': 'x' * 2000000}):
                with self.assertRaises(SecretError) as caught:
                    _callee_env(org_slug='alpha', workspace=workspace)
                self.assertEqual(caught.exception.code, 'environment_budget_exceeded')

    async def test_h01_h03_task_hold_retains_pending_without_preparation(self) -> None:
        from scripts.test_agent_retirement import fixture
        from runtime.models import TaskRecord, TaskStatus
        async with fixture(self.root / 'hold') as (org, _client):
            store = store_for(org.root)
            store.mutate('create', 'SYNTH_NAME', self.sentinels[0], operation())
            self.backend.locked = True
            locked = OrgSecrets(org.root)
            _STORES[org.root.resolve()] = locked
            org.orchestrator._org_secrets = locked
            org.db.insert_task(TaskRecord(id='TASK-SECRETS', assigned_agent='worker', team='retiring', brief='Synthetic hold'))
            with patch('runtime.orchestrator.run_step.run_step_impl') as run:
                org.orchestrator.run_step('TASK-SECRETS')
                run.assert_not_called()
                self.assertEqual(org.db.get_task('TASK-SECRETS').status, TaskStatus.PENDING)
                self.assertIsNone(org.db.get_task('TASK-SECRETS').current_session_id)
                self.backend.locked = False
                self.assertEqual(locked.probe(), 'available')
                org.orchestrator.run_step('TASK-SECRETS')
                run.assert_called_once()
            self.backend.locked = True
            fresh = OrgSecrets(org.root)
            fresh.mutate('delete', 'SYNTH_NAME', None, operation())
            self.assertTrue(fresh.ready())

    async def test_d01_builtin_and_custom_contained_and_direct_children(self) -> None:
        from scripts.test_agent_retirement import fixture
        from runtime.config import Settings
        from runtime.orchestrator import executors as ex
        from runtime.orchestrator._paths import OrgPaths
        async with fixture(self.root / 'executors') as (org, _client):
            workspace = org.root / 'workspaces/worker'
            store_for(org.root).mutate('create', 'SYNTH_NAME', self.sentinels[0], operation())
            builtin = self.root / 'synthetic-provider'
            builtin.write_text('#!/bin/sh\ncat >/dev/null\n[ -n "$SYNTH_NAME" ] && echo set\n')
            builtin.chmod(0o700)
            custom = self.root / 'synthetic-adapter'
            custom.write_text('#!' + sys.executable + '\n' + '''import os,sys,json
payload=json.load(sys.stdin)
assert os.environ.get('SYNTH_NAME')
assert os.environ['SYNTH_NAME'] not in json.dumps(payload)
print(json.dumps({'success':True,'returncode':0,'duration_seconds':0,'session_id':payload['invocation']['invocation_id'],
 'stdout_tail':'set','stderr_tail':'','adapter_metadata':{'adapter':'synthetic-adapter','adapter_version':'1','contract_version':1}}))
''')
            custom.chmod(0o700)
            settings = Settings()
            choices = [('claude', ex.ClaudeExecutor(str(builtin), 'default', settings, OrgPaths(org.root))),
                       ('codex', ex.CodexExecutor(str(builtin), 'workspace-write')),
                       ('opencode', ex.OpencodeExecutor(str(builtin))), ('pi', ex.PiExecutor(str(builtin))),
                       ('custom', ex.CustomAdapterExecutor(profile_name='synthetic', adapter_entry_id='synthetic-adapter',
                         adapter_executable=str(custom), adapter_hash=hashlib.sha256(custom.read_bytes()).hexdigest(),
                         adapter_version='1', adapter_contract_version=1, provider='synthetic',
                         invocation_context={'agent':'worker','org':'alpha','invocation_kind':'task'}))]
            with patch('runtime.orchestrator.executors._resolve_binary', return_value=str(builtin)):
                for name, executor in choices:
                    for contained in (False, True):
                        with self.subTest(executor=name, contained=contained):
                            kwargs = dict(workspace=workspace, prompt='Synthetic command acceptance',
                                          session_id='synthetic-sid', org_slug='alpha', timeout_seconds=5)
                            if contained:
                                spec = executor.build_launch_spec(**kwargs)
                                self.assertTrue(spec.env.get('SYNTH_NAME') == self.sentinels[0])
                                process = subprocess.Popen(spec.argv, cwd=spec.cwd, env=spec.env,
                                    stdin=spec.stdin, stdout=spec.stdout, stderr=spec.stderr, text=True)
                                kwargs['running'] = SimpleNamespace(process=process)
                            result = executor.run(**kwargs)
                            self.assertTrue(result.success, 'synthetic child did not complete')
                            self.assertTrue('set' in result.stdout_tail)

    async def test_d01_reserved_controls_never_reach_automatic_diagnostics(self) -> None:
        from runtime.orchestrator import executors as ex
        from runtime.orchestrator.run_step import _session_failed_note
        value = "synthetic-" + uuid.uuid4().hex[:12]
        self.sentinels.append(value)
        store = self.store()
        workspace = store.root / 'workspaces/worker'
        workspace.mkdir(parents=True)
        adapter = self.root / 'diagnostic-adapter'
        child = self.root / 'diagnostic-child.py'
        child.write_text('import json\nprint(json.dumps(' + repr({
            'success': True, 'returncode': 0, 'duration_seconds': 0, 'session_id': 'synthetic',
            'stdout_tail': '', 'stderr_tail': '', 'adapter_metadata': {
                'adapter': 'diagnostic-adapter', 'adapter_version': '1', 'contract_version': 1}}) + '))\n')
        subprocess.run(['/usr/bin/git', 'init', '-q', str(workspace)], check=True, capture_output=True)
        adapter.write_text('#!/bin/bash\n/usr/bin/git status --porcelain >/dev/null\nexec ' + shlex.quote(sys.executable) + ' ' + shlex.quote(str(child)) + '\n')
        adapter.chmod(0o700)
        executor = ex.CustomAdapterExecutor(profile_name='synthetic', adapter_entry_id='diagnostic-adapter',
            adapter_executable=str(adapter), adapter_hash=hashlib.sha256(adapter.read_bytes()).hexdigest(),
            adapter_version='1', adapter_contract_version=1, provider='synthetic',
            invocation_context={'agent':'worker', 'org':'alpha', 'invocation_kind':'task'})
        for name in ('LC_ALL', 'PYTHONWARNINGS', 'GIT_TRACE'):
            with self.subTest(control=name):
                # Emulate an encrypted record accepted by the predecessor policy.
                with patch('runtime.org_secrets.store.validate_name', side_effect=lambda name: name):
                    store.mutate('create', name, value, operation())
                for reader in (lambda: executor.run(workspace=workspace, prompt='Synthetic',
                                   session_id='synthetic', org_slug='alpha', timeout_seconds=5),
                               lambda: ex._callee_env(org_slug='alpha', workspace=workspace)):
                    try:
                        result = reader()
                    except SecretError as exc:
                        self.assertIn(exc.code, ('reserved_name', 'store_unavailable'))
                        self.assertFalse(value in str(exc))
                    else:
                        if isinstance(result, dict):
                            self.assertFalse(name in result, 'reserved control injected')
                        else:
                            self.assertEqual([value in result.stderr_tail,
                                              value in _session_failed_note(result, None)],
                                             [False, False], 'automatic diagnostics or failure note disclosed value')
                            self.fail('stored reserved control did not close launch')
                # Bypass only disk-name parsing to test the independent injection guard.
                with patch('runtime.org_secrets.store.validate_name', side_effect=lambda name: name):
                    with self.assertRaises(SecretError) as caught:
                        ex._callee_env(org_slug='alpha', workspace=workspace)
                    self.assertEqual(caught.exception.code, 'reserved_name')
                    store.mutate('delete', name, None, operation())

        # Unanticipated application name: actual stderr and JSON error fields
        # cross the real executor and persisted no-callback failure-note boundary.
        store.mutate('create', 'SYNTH_UNRESERVED', value, operation())
        child.write_text('import os,sys,json\nv=os.environ["SYNTH_UNRESERVED"]\n'
            'sys.stderr.write(v)\nprint(json.dumps({'
            '"success":False,"returncode":1,"duration_seconds":0,"session_id":"synthetic",'
            '"stdout_tail":v,"stderr_tail":v,"error":v,"adapter_metadata":{'
            '"adapter":"diagnostic-adapter","adapter_version":"1","contract_version":1}}))\n')
        with self.subTest(path='custom-diagnostic'):
            result = executor.run(workspace=workspace, prompt='Synthetic', session_id='synthetic',
                org_slug='alpha', timeout_seconds=5)
            self.assertFalse(result.success)
            observations = [value in text for text in (result.stdout_tail, result.stderr_tail,
                result.error or '', _session_failed_note(result, None))]
            self.assertEqual(observations, [False] * 4, 'session diagnostic disclosure')
            self.assertTrue('[REDACTED]' in result.stderr_tail, 'stderr was not exercised')
            note = self.root / 'saved-failure-note.txt'
            note.write_text(_session_failed_note(result, None))
        # The built-in command path has a different parser/error envelope.
        with self.subTest(path='builtin-diagnostic'):
            builtin = ex._run_command([sys.executable, '-c',
                'import os,sys; sys.stderr.write(os.environ["SYNTH_UNRESERVED"]); sys.exit(1)'],
                workspace, 'synthetic', 5, provider='synthetic', org_slug='alpha', throttle_backoff_seconds=())
            self.assertFalse(builtin.success)
            self.assertEqual([value in builtin.stderr_tail, value in (builtin.error or ''),
                value in _session_failed_note(builtin, None)], [False] * 3, 'built-in diagnostic disclosure')

        # Host-launched processes get the precise final spec's values, even
        # when the store changes before communicate/error persistence.
        from dataclasses import replace
        from runtime.orchestrator.host_supervisor import AdmissionRequest, HostSessionSupervisor, LaunchResult, canary_policy
        from runtime.platform.passthrough_backend import PassthroughBackend
        class SyntheticBackend(PassthroughBackend):
            def launch(self, pending, spec):
                process = subprocess.Popen(spec.argv, cwd=spec.cwd, env=spec.env,
                    stdin=spec.stdin, stdout=spec.stdout, stderr=spec.stderr, text=spec.text)
                return replace(super().launch(pending, spec), backend='synthetic-contained',
                    process=process, root_pid=process.pid)
        from scripts.test_agent_retirement import fixture
        with self.subTest(path='contained-diagnostic'):
            async with fixture(self.root / 'contained-diagnostic') as (org, _client):
                store = store_for(org.root)
                workspace = org.root / 'workspaces/worker'
                store.mutate('create', 'SYNTH_UNRESERVED', value, operation())
                spec = ex.build_command_launch_spec(cmd=[sys.executable, '-c',
                    'import os,sys; sys.stderr.write(os.environ["SYNTH_UNRESERVED"]); sys.exit(1)'],
                    workspace=workspace, input_text='', org_slug='alpha', session_id='synthetic')
                def communicate(running):
                    store.mutate('replace', 'SYNTH_UNRESERVED', 'synthetic-replacement', operation())
                    result = ex._run_command(list(spec.argv), workspace, 'synthetic', 5,
                        org_slug='alpha', provider='synthetic', running=running)
                    return LaunchResult(success=result.success, duration_seconds=0, error=value, payload=result)
                def publish(receipt):
                    raise RuntimeError(value)  # Real contained publisher diagnostic.
                with self.assertLogs('runtime.orchestrator.host_supervisor', level='WARNING') as logs:
                    outcome = HostSessionSupervisor(backend=SyntheticBackend(),
                        policy=canary_policy(sample_interval_seconds=0), publisher=publish).run(
                            AdmissionRequest(org='alpha', invocation_kind='task', logical_id='synthetic', executor_profile='synthetic'),
                            launch_spec=spec, launch_body=communicate)
                self.assertEqual([value in (outcome.error or ''), value in outcome.payload.payload.stderr_tail,
                    value in _session_failed_note(outcome.payload.payload, None), value in (outcome.payload.error or ''),
                    value in '\n'.join(logs.output)], [False] * 5, 'contained diagnostic disclosure')
        # A subsequent invocation may display an old value it was never given:
        # custody is per invocation, not a machine/org-wide masking registry.
        unrelated = ex._run_command([sys.executable, '-c', 'import sys; sys.stderr.write(sys.argv[1]); sys.exit(1)', value],
            workspace, 'unrelated', 5, provider='synthetic', org_slug=None, throttle_backoff_seconds=())
        self.assertTrue(value in unrelated.stderr_tail)

    async def test_d03_supervisor_refusal_settles_and_recovers_later_reply(self) -> None:
        import time
        from dataclasses import replace
        from scripts.test_agent_retirement import fixture
        from runtime.models import ThreadRecord, ThreadMessageKind, ThreadInvocationStatus
        from runtime.orchestrator import executors as ex
        from runtime.orchestrator.thread_move import bind_thread_preparation
        from runtime.orchestrator.host_supervisor import AdmissionController, AdmissionRequest, HostSessionSupervisor, canary_policy
        from runtime.platform.passthrough_backend import PassthroughBackend
        from runtime.daemon.thread_runner import _bound_thread_prompt, THREAD_PROMPT_MAX_BYTES, _settle_or_fail_reply

        for case in ('capacity_prompt', 'capacity_budget', 'passthrough_prompt', 'old_unknown', 'abandon_failed'):
            with self.subTest(case=case):
                async with fixture(self.root / case) as (org, _client):
                    workspace = org.root / 'workspaces/worker'
                    store, db = store_for(org.root), org.db
                    db.insert_thread(ThreadRecord(id='THR-refused', subject='Synthetic'))
                    db.add_thread_participant('THR-refused', 'worker', added_by='founder')
                    db.record_conversational_arrival(thread_id='THR-refused', speaker='founder',
                        kind=ThreadMessageKind.MESSAGE, body_markdown='Synthetic', recipients=['worker'])
                    token = db.get_reply_delivery_state('THR-refused', 'worker').queued_invocation_token
                    owner = db._team_move_coordinator.threads.reserve(token)
                    owner.claim = db.peek_conversational_reply(token)
                    body = 'p' * (THREAD_PROMPT_MAX_BYTES - len(ex._SESSION_LIFETIME_PREAMBLE.encode()))
                    def prompt_reader():
                        bounded = _bound_thread_prompt(body, messages=[], mandatory_from_seq=1,
                            rebuild=None, preamble=ex._session_preamble(org.slug, workspace))
                        if bounded is None:
                            raise ex.PromptTransportTooLargeError('prompt_too_large')
                        return bounded[0]
                    owner.prompt_reader = prompt_reader
                    self.assertTrue(prompt_reader() == body)
                    owner.last_spec = ex.build_command_launch_spec(cmd=['/bin/true'], workspace=workspace,
                        input_text=body, org_slug=org.slug, session_id=owner.session_id)
                    controller, backend = AdmissionController(cap=1), PassthroughBackend()
                    # A contained preparation must be abandoned before its launch.
                    prepare = backend.prepare
                    if case != 'passthrough_prompt':
                        backend.prepare = lambda request, policy: replace(prepare(request, policy), backend='synthetic-contained')
                    supervisor = HostSessionSupervisor(backend=backend, policy=canary_policy(sample_interval_seconds=0),
                        publisher=lambda receipt: None, admission=controller)
                    held = controller.acquire(AdmissionRequest(org='alpha', invocation_kind='thread',
                        logical_id='holder', executor_profile='synthetic'), timeout=3)
                    def launch_body(running):
                        self.assertEqual(case, 'passthrough_prompt')
                        # Host committed; the actual direct executor still has not
                        # called isolation.launch_executor/Popen. Change at that seam.
                        store.mutate('create', 'SYNTH_NAME', 'synthetic-value', operation())
                        return ex._run_command(['/bin/true'], workspace, owner.session_id, 5,
                            input_text=body, session_prompt=body, org_slug=org.slug, provider='synthetic',
                            throttle_backoff_seconds=())
                    def launch():
                        with bind_thread_preparation(owner):
                            return supervisor.run(AdmissionRequest(org='alpha', invocation_kind='thread',
                                logical_id='THR-refused', executor_profile='synthetic', agent_id=owner.identity.agent_id,
                                move_reservation=owner), launch_spec=owner.last_spec,
                                final_prelaunch_validator=owner, launch_body=launch_body)
                    with patch.object(backend, 'launch', wraps=backend.launch) as launched, \
                         patch('runtime.platform.isolation._PosixSameOwnerIsolation.launch_executor') as popen, \
                         patch.object(backend, 'abandon', side_effect=RuntimeError('synthetic abandon failure') if case == 'abandon_failed' else None), \
                         patch('runtime.org_secrets.session.os.sysconf', wraps=os.sysconf) as argmax:
                        with ThreadPoolExecutor(max_workers=1) as pool:
                            future = pool.submit(launch)
                            try:
                                deadline = time.monotonic() + 2
                                while controller.queue_depth() == 0 and time.monotonic() < deadline:
                                    time.sleep(.005)
                                self.assertEqual(controller.queue_depth(), 1, 'no real capacity wait')
                                self.assertFalse(future.done())
                                if case == 'capacity_budget':
                                    argmax.side_effect = lambda name: 1024
                                elif case != 'passthrough_prompt':
                                    store.mutate('create', 'SYNTH_NAME', 'synthetic-value', operation())
                                if case == 'old_unknown':
                                    owner.failed_cleanup()  # Existing history must remain sticky.
                            finally:
                                held.release()
                            try:
                                outcome = future.result(timeout=8)
                            except Exception as exc:
                                if case == 'old_unknown':
                                    from runtime.orchestrator.team_move import DeferredTeamMove
                                    self.assertIsInstance(exc, DeferredTeamMove)
                                    outcome = None
                                else:
                                    # Preserve the RED assertion at the actual bad state.
                                    self.assertFalse(owner.state.failed_cleanup, 'refusal invented failed cleanup')
                                    raise
                        self.assertEqual(launched.call_count, int(case == 'passthrough_prompt'))
                        self.assertEqual(popen.call_count, 0)
                    if case == 'old_unknown':
                        self.assertTrue(owner.state.failed_cleanup)
                    else:
                        self.assertEqual(outcome.error, 'environment_budget_exceeded' if case == 'capacity_budget' else 'prompt_too_large')
                    settled = _settle_or_fail_reply(org, invocation_token=token, claim=owner.claim,
                        status=ThreadInvocationStatus.FAILED, decline_reason='prelaunch_refused')
                    self.assertTrue(settled)
                    owner.finish(deferred=False, failed=False)
                    uncertain = case in ('old_unknown', 'abandon_failed')
                    self.assertEqual(owner.state.failed_cleanup, uncertain)
                    self.assertEqual(owner.state.unknown, uncertain)
                    self.assertFalse(owner.attempt_open)
                    self.assertEqual(len(owner.state.attempts), int(case == 'passthrough_prompt'))
                    if owner.state.attempts:
                        self.assertTrue(all(a.settled and a.quiescent and not a.launched for a in owner.state.attempts))
                    self.assertTrue(owner.state.producer_settled)
                    self.assertEqual(owner.admission.live.get(owner.inv.id) is owner, uncertain)
                    db.record_conversational_arrival(thread_id='THR-refused', speaker='founder',
                        kind=ThreadMessageKind.MESSAGE, body_markdown='Later founder reply', recipients=['worker'])
                    next_token = db.get_reply_delivery_state('THR-refused', 'worker').queued_invocation_token
                    self.assertIsNotNone(next_token)
                    recovered = db.recover_reply_delivery_state()
                    self.assertEqual(len(recovered), 0 if uncertain else 1, 'later queued reply stranded')
                    self.assertEqual(owner.admission.preserve_pair_uncommitted('THR-refused', 'worker'), uncertain)

    async def test_d03_capacity_wait_refreshes_native_snapshots(self) -> None:
        from datetime import datetime, timezone
        import time
        from scripts.test_agent_retirement import fixture
        from runtime.models import DreamRecord, WorkHourRecord, WorkHourMode, ScheduleRecord, ScheduleKind, ScheduleStatus
        from runtime.infrastructure.work_hours_store import WorkHoursStore
        from runtime.infrastructure.schedule_store import ScheduleStore
        from runtime.orchestrator.host_supervisor import AdmissionController, AdmissionRequest
        from runtime.orchestrator.team_move import DeferredTeamMove
        from runtime.orchestrator.thread_move import bind_thread_preparation
        from runtime.orchestrator import executors as ex
        for kind in ('thread', 'wake', 'schedule', 'dream'):
            changes = ('create', 'replace', 'delete', 'locked', 'cancelled', 'passthrough_locked')
            for change in changes + (('prompt_growth', 'trim_growth') if kind == 'thread' else ()):
                self.backend.locked = False
                with self.subTest(kind=kind, change=change):
                    async with fixture(self.root / (kind + '-' + change)) as (org, _client):
                        workspace = org.root / 'workspaces/worker'
                        store = store_for(org.root)
                        if change not in ('create', 'prompt_growth', 'trim_growth'):
                            store.mutate('create', 'SYNTH_NAME', 'before', operation())
                        move = org.db._team_move_coordinator
                        now = datetime.now(timezone.utc)
                        if kind == 'thread':
                            from runtime.models import ThreadRecord, ThreadMessageKind
                            org.db.insert_thread(ThreadRecord(id='THR-wait', subject='Synthetic'))
                            org.db.add_thread_participant('THR-wait', 'worker', added_by='founder')
                            org.db.record_conversational_arrival(thread_id='THR-wait', speaker='founder',
                                kind=ThreadMessageKind.MESSAGE, body_markdown='Synthetic', recipients=['worker'])
                            token = org.db.get_reply_delivery_state('THR-wait', 'worker').queued_invocation_token
                            owner = move.threads.reserve(token)
                            owner.claim = org.db.peek_conversational_reply(token)
                            commit = owner._thread_move_commit
                        else:
                            if kind == 'dream':
                                org.db.insert_dream(DreamRecord(id='DREAM-001', agent_name='worker', local_date='2026-11-28', scheduled_for=now, window_end=now))
                                source = 'DREAM-001'
                            elif kind == 'wake':
                                WorkHoursStore(org.db._conn, org.db._lock, db=org.db).insert(WorkHourRecord(id='WORK-001', agent_name='worker', local_date='2026-11-28', slot='synthetic', mode=WorkHourMode.WINDOWED, scheduled_for=now))
                                source = 'WORK-001'
                            else:
                                ScheduleStore(org.db._conn, org.db._lock, db=org.db).insert(ScheduleRecord(id='SCHED-001', agent_name='worker', kind=ScheduleKind.ONE_SHOT, fire_at=now, status=ScheduleStatus.FIRING, normalized_brief='Synthetic', source_instruction='Synthetic'))
                                source = 'SCHED-001'
                            owner = move.producers.reserve(kind, source)
                            commit = owner._producer_move_commit
                        self.assertIsNotNone(owner)
                        owner.retry_schedule('provider', ())
                        if change in ('prompt_growth', 'trim_growth'):
                            from runtime.daemon.thread_runner import _bound_thread_prompt, THREAD_PROMPT_MAX_BYTES
                            from runtime.models import ThreadMessage
                            available = THREAD_PROMPT_MAX_BYTES - len(ex._SESSION_LIFETIME_PREAMBLE.encode())
                            history = [ThreadMessage(thread_id='THR-wait', seq=1, speaker='founder',
                                kind=ThreadMessageKind.MESSAGE, body_markdown='h' * 500),
                                ThreadMessage(thread_id='THR-wait', seq=2, speaker='founder',
                                kind=ThreadMessageKind.MESSAGE, body_markdown='m' * (available - 500))]
                            def bounded_prompt():
                                result = _bound_thread_prompt(''.join(m.body_markdown for m in history),
                                    messages=history, mandatory_from_seq=1 if change == 'prompt_growth' else 2,
                                    rebuild=lambda kept: ''.join(m.body_markdown for m in kept),
                                    preamble=ex._session_preamble('alpha', workspace))
                                if result is None:
                                    raise ex.PromptTransportTooLargeError('prompt_too_large')
                                if change == 'trim_growth' and owner.secret_snapshot is not None:
                                    self.assertEqual(result[1], [2])
                                return result[0]
                            owner.prompt_reader = bounded_prompt
                        owner.last_spec = ex.build_command_launch_spec(cmd=[sys.executable, '-c',
                            'import os,sys,json; p=sys.stdin.read(); v=os.environ.get("SYNTH_NAME"); print(json.dumps([v=="after",v is None,("SYNTH_NAME" in p)]))'],
                            workspace=workspace, input_text='Synthetic', org_slug='alpha', session_id=owner.session_id)
                        controller = AdmissionController(cap=1)
                        def request(label):
                            return AdmissionRequest(org='alpha', invocation_kind=kind, logical_id=label, executor_profile='synthetic')
                        held = controller.acquire(request('holder'), timeout=3)
                        def launch():
                            with controller.acquire(request('queued'), timeout=3) as lease:
                                def admitted():
                                    spec = commit(lease.ctx)
                                    if spec is None:
                                        return 'refused'
                                    if change == 'passthrough_locked':
                                        from runtime.org_secrets.session import admit_session_values
                                        self.backend.locked = True
                                        _STORES[org.root.resolve()] = OrgSecrets(org.root)
                                        admit_session_values('alpha', workspace, selflaunch=True)
                                    # Mutate once more AFTER admission: the preamble must
                                    # still describe the delivered snapshot, not this read.
                                    if change == 'create':
                                        store.mutate('delete', 'SYNTH_NAME', None, operation())
                                    note = ex._session_preamble('alpha', workspace)
                                    body = owner.prompt_reader() if kind == 'thread' and owner.prompt_reader else 'Synthetic'
                                    self.assertLessEqual(len((note + body).encode()), 512 * 1024)
                                    child = subprocess.run(spec.argv, cwd=spec.cwd, env=spec.env,
                                        input=note + body, capture_output=True, text=True, timeout=5)
                                    self.assertEqual(child.returncode, 0)
                                    return json.loads(child.stdout)
                                if kind == 'thread':
                                    with bind_thread_preparation(owner):
                                        return admitted()
                                return owner.invoke(admitted)
                        with ThreadPoolExecutor(max_workers=1) as pool:
                            future = pool.submit(launch)
                            try:
                                deadline = time.monotonic() + 2
                                while controller.queue_depth() == 0 and time.monotonic() < deadline:
                                    time.sleep(0.005)
                                self.assertEqual(controller.queue_depth(), 1, 'child never entered real capacity wait')
                                self.assertFalse(future.done())
                                if change == 'locked':
                                    self.backend.locked = True
                                    _STORES[org.root.resolve()] = OrgSecrets(org.root)
                                elif change == 'cancelled':
                                    owner.owner_live = False
                                elif change == 'passthrough_locked':
                                    pass  # Lock lands only after the host's commitment.
                                elif change in ('prompt_growth', 'trim_growth'):
                                    store.mutate('create', 'SYNTH_NAME', 'after', operation())
                                else:
                                    store.mutate(change, 'SYNTH_NAME', None if change == 'delete' else 'after', operation())
                            finally:
                                held.release()
                            if change in ('locked', 'cancelled', 'prompt_growth', 'passthrough_locked'):
                                expected_error = ex.PromptTransportTooLargeError if change == 'prompt_growth' else DeferredTeamMove
                                with self.assertRaises(expected_error):
                                    future.result(timeout=5)
                                if change == 'passthrough_locked':
                                    owner.direct_terminal()
                                    self.assertTrue(owner.state.attempts[-1].no_launch_deferred)
                                    self.assertTrue(owner.state.attempts[-1].quiescent)
                                self.assertFalse(owner.attempt_open)
                                owner.owner_live = True
                                owner.finish(deferred=True, failed=False)
                            else:
                                self.assertEqual(future.result(timeout=5),
                                    [change != 'delete', change == 'delete', change != 'delete'])
                                owner.direct_terminal()
                                owner.finish(deferred=False, failed=False)
                        self.backend.locked = False

    async def test_p01_unsafe_roots_and_named_export_exclusion(self) -> None:
        from runtime.portability.roots import classify_root_entries, RootClassification, EXCLUDE_ORG_SECRETS
        root = self.root / 'portable'
        root.mkdir()
        (root / 'secrets').mkdir()
        entry = classify_root_entries(root).entries[0]
        self.assertEqual((entry.classification, entry.reason), (RootClassification.EXCLUDE, EXCLUDE_ORG_SECRETS))
        (root / 'secrets').rmdir()
        (root / 'secrets').symlink_to(self.root, target_is_directory=True)
        self.assertTrue(classify_root_entries(root).has_rejections)
        (root / 'secrets').unlink()
        (root / 'unknown').mkdir()
        self.assertTrue(classify_root_entries(root).has_rejections)
        from scripts.test_agent_retirement import fixture
        async with fixture(self.root / 'preflight') as (org, client):
            store_for(org.root).mutate('create', 'SYNTH_NAME', self.sentinels[0], operation())
            response = await client.get('/api/v1/orgs/alpha/portability-preflight')
            self.assertEqual(response.status_code, 200)
            entries = response.json()['classification']['entries']
            self.assertIn({'path': 'secrets', 'classification': 'exclude', 'reason': EXCLUDE_ORG_SECRETS}, entries)


if __name__ == '__main__':
    unittest.main()

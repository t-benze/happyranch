"""Naming-only disposable shipping daemon and read-only durable observations.

Imported only by explicitly selected integration nodes. Nothing starts on import.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import time

import httpx
import pytest

from runtime.runtime import RuntimeDir
from runtime.daemon import paths, runtimes
from runtime.orchestrator.executor_binary_registry import save_registry
from runtime.orchestrator.context_builder import ContextBuilder
from runtime.orchestrator import prompt_loader
from runtime.orchestrator._paths import OrgPaths
from runtime.config import Settings
from tests.integration.identity_names_owned_cases import seed, release_seed
from tests.helpers.deterministic_plan import DeterministicPlan

SOURCE = Path(__file__).resolve().parents[3]
THREAD_PLAN = '''#!/usr/bin/env bash
set -euo pipefail
printf '{"plan_pid":%d,"plan_start":"%s","stub_pid":%d,"stub_start":"%s"}\n' "$$" "$(awk '{print $22}' /proc/$$/stat)" "$PPID" "$(awk '{print $22}' /proc/$PPID/stat)" >> "$HAPPYRANCH_TEST_WITNESS_DIR/native-children.jsonl"
test "$(wc -c < "$HAPPYRANCH_TEST_WITNESS_DIR/native-children.jsonl")" -le 65536
thread=$1; token=$2; agent=$3; org=$4
payload=$(mktemp)
trap 'rm -f "$payload"' EXIT
printf '{"thread_id":"%s","invocation_token":"%s","speaker":"%s","body_markdown":"@founder naming delivery %s","in_response_to_seq":1}' "$thread" "$token" "$agent" "$agent" > "$payload"
happyranch threads reply --org "$org" --thread-id "$thread" --from-file "$payload"
'''
CODEX_PLAN = '''#!/usr/bin/env bash
set -euo pipefail
printf '{"plan_pid":%d,"plan_start":"%s","stub_pid":%d,"stub_start":"%s"}\n' "$$" "$(awk '{print $22}' /proc/$$/stat)" "$PPID" "$(awk '{print $22}' /proc/$PPID/stat)" >> "$HAPPYRANCH_TEST_WITNESS_DIR/native-children.jsonl"
test "$(wc -c < "$HAPPYRANCH_TEST_WITNESS_DIR/native-children.jsonl")" -le 65536
task=$1; session=$2; org=$3
payload=$(mktemp)
trap 'rm -f "$payload"' EXIT
printf '{"task_id":"%s","session_id":"%s","agent":"manager","status":"completed","confidence":90,"summary":"naming fixture callback","decision":{"action":"done","summary":"naming fixture callback"}}' "$task" "$session" > "$payload"
happyranch report-completion --org "$org" --from-file "$payload"
'''
# Any unexpected task/provider branch refuses, rather than silently succeeding.
REFUSE_PLAN = '#!/usr/bin/env bash\nexit 86\n'


def sql_rows(root, statement, parameters=()):
    path = OrgPaths(root).db_path
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=2) as conn:
        observed = [list(row) for row in conn.execute(statement, parameters)]
    evidence = root.parents[2] / 'evidence'
    evidence.mkdir(exist_ok=True)
    destination = evidence / 'sql-observations.jsonl'
    raw = (json.dumps({'org': root.name, 'sql': statement, 'parameters': parameters,
                       'rows': observed}, sort_keys=True) + '\n').encode()
    assert len(raw) <= 65536 and (destination.stat().st_size if destination.exists() else 0) + len(raw) <= 1048576
    with destination.open('ab') as stream:
        stream.write(raw)
    return observed


def persistent_identity(root):
    """No constructor/migration/repair, including on a running org DB."""
    with sqlite3.connect(f'file:{OrgPaths(root).db_path}?mode=ro', uri=True, timeout=2) as conn:
        tables = ('identity_name_owners', 'identity_name_claims', 'audit_log')
        return {table: [list(row) for row in conn.execute(f'SELECT rowid,* FROM {table} ORDER BY rowid')]
                for table in tables}


def wait_for(observe, predicate, seconds=20):
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        last = observe()
        if predicate(last):
            return last
        time.sleep(0.05)
    raise AssertionError(f'finite naming observation expired: {last!r}')


@dataclass
class NamingDaemon:
    origin: str
    root: Path
    evidence: Path
    headers: dict

    @property
    def alpha(self):
        return self.root / 'orgs' / 'alpha'

    @property
    def beta(self):
        return self.root / 'orgs' / 'beta'

    def request(self, method, path, **kwargs):
        response = httpx.request(method, self.origin + '/api/v1/orgs/alpha' + path,
                                 headers=self.headers, timeout=5, **kwargs)
        return response

    def identity(self, key):
        response = self.request('GET', '/identities')
        assert response.status_code == 200, response.text
        return next(row for row in response.json()['identities'] if row['canonical_id'] == key)

    def rename(self, key, label):
        previous = self.identity(key)
        path = '/founder' if key == 'founder' else '/agents/' + key
        response = self.request('PUT', path + '/addressable-name', json={
            'addressable_name': label, 'expected_name_revision': previous['name_revision']})
        assert response.status_code == 200, response.text
        return response.json()

    def restart(self):
        """Supported stop/start of only this fixture's isolated registration."""
        script = SOURCE / 'scripts/daemon.sh'
        old_port = int(paths.port_file().read_text())
        stopped = subprocess.run([str(script), 'stop'], timeout=20)
        assert stopped.returncode == 0
        with socket.socket() as probe:
            probe.settimeout(1)
            assert probe.connect_ex(('127.0.0.1', old_port)) != 0
        subprocess.run([str(script), 'start'], check=True, timeout=25)
        self.origin = 'http://127.0.0.1:' + paths.port_file().read_text().strip()
        (self.evidence / 'restarted.json').write_text(json.dumps({
            'old_port_closed': old_port, 'new_port': int(paths.port_file().read_text()),
            'new_pid': int(paths.pid_file().read_text())}))

    def browser(self, scenario):
        work = self.evidence / ('browser-' + scenario)
        work.mkdir()
        # Absolute provisioned tool paths are deliberate: parent PATH is closed.
        env = os.environ.copy()
        env['PLAYWRIGHT_BROWSERS_PATH'] = '/opt/naming-browsers'
        env['NAMING_NODE_CHILDREN'] = str(work / 'native-children.jsonl')
        env['NODE_OPTIONS'] = '--require=' + str(SOURCE / 'tests/helpers/identity_names/observe-node.cjs')
        config = work / '.playwright'
        config.mkdir()
        # Only our trusted loopback SPA is reachable. Chromium's CLI sandbox
        # default cannot launch inside the capability-free, no-new-privileges
        # venue; Docker's network, seccomp and resource restrictions stay intact.
        (config / 'cli.config.json').write_text(json.dumps({
            'browser': {'browserName': 'chromium', 'isolated': True,
                        'launchOptions': {'headless': True, 'chromiumSandbox': False}},
            'outputDir': str(work / 'cli-output')}))
        command = ['/usr/bin/node', str(SOURCE / 'web/scripts/identity-names-e2e.mjs'),
                   '--base-url', self.origin, '--out', str(work), '--scenario', scenario]
        log = work / 'adapter.log'
        with log.open('wb') as stream:
            child = subprocess.Popen(command, cwd=work, env=env, stdout=stream,
                                     stderr=subprocess.STDOUT, start_new_session=True)
            (work / 'child.json').write_text(json.dumps({'pid': child.pid, 'argv': command}))
            try:
                code = child.wait(timeout=300)
            finally:
                if child.poll() is None:
                    import signal
                    os.killpg(child.pid, signal.SIGTERM)
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait(timeout=5)
        assert log.stat().st_size <= 1024 * 1024, 'adapter log cap exceeded'
        assert code == 0, log.read_text()[-8192:]
        return json.loads((work / 'result.json').read_text())


@pytest.fixture
def naming_daemon(tmp_path, tmp_home, monkeypatch, fake_claude, fake_codex, fake_opencode, request):
    runtime = RuntimeDir.init(tmp_path / 'runtime')
    for slug in ('alpha', 'beta'):
        root = runtime.orgs_dir / slug
        seed(root)
        # Existing literal full schema, never candidate-generated reference SQL.
        release_seed(OrgPaths(root).db_path, 'v0', 'F', slug=slug)
        for key in ('maker', 'manager'):
            definition = prompt_loader.load_agent(OrgPaths(root), key)
            if key == 'manager' and request.node.name in ('test_scenario8_live_cli_picker_and_persisted_ids', 'test_scenario9_live_history_authority_reopen'):
                from dataclasses import replace
                from runtime.orchestrator.agent_def import render_agent_text
                definition = replace(definition, executor='codex')
                (OrgPaths(root).agents_dir / 'manager.md').write_text(render_agent_text(definition))
            ContextBuilder(Settings(), OrgPaths(root), slug=slug).ensure_workspace_ready(
                root / 'workspaces' / key, key, definition.system_prompt, provider=definition.executor)
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    witness = evidence / 'witness'
    witness.mkdir(mode=0o700)
    monkeypatch.setenv('HAPPYRANCH_TEST_WITNESS_DIR', str(witness))
    for variable, body in (('FAKE_CLAUDE_THREAD_PLAN', THREAD_PLAN),
                           ('FAKE_CODEX_PLAN', CODEX_PLAN),
                           ('FAKE_CLAUDE_PLAN', REFUSE_PLAN),
                           ('FAKE_OPENCODE_PLAN', REFUSE_PLAN),
                           ('FAKE_OPENCODE_THREAD_PLAN', REFUSE_PLAN)):
        plan = DeterministicPlan(tmp_path / (variable + '.sh'))
        plan.write_text(body)
        monkeypatch.setenv(variable, str(plan))
    monkeypatch.setenv('HAPPYRANCH_EXECUTOR_LAUNCH_SPACING_SECONDS', '0')
    save_registry({'claude': str(fake_claude), 'codex': str(fake_codex), 'opencode': str(fake_opencode)})
    runtimes.register(runtime.root)
    script = SOURCE / 'scripts/daemon.sh'
    child_env = os.environ.copy()
    child_env.pop('HAPPYRANCH_TASK_TMP_ROOT', None)
    child_env.pop('HAPPYRANCH_TASK_SCRATCH_MANIFEST', None)
    port = pid = None
    try:
        started = subprocess.run([str(script), 'start'], env=child_env, check=True,
                                 timeout=25, capture_output=True, text=True)
        import re
        wrapper = int(re.search(r'daemon started \(pid (\d+),', started.stdout).group(1))
        port = int(paths.port_file().read_text())
        pid = int(paths.pid_file().read_text())
        origin = f'http://127.0.0.1:{port}'
        assert httpx.get(origin + '/api/v1/health', timeout=2).status_code == 200
        (evidence / 'daemon-owned.json').write_text(json.dumps({
            'pid': pid, 'uv_wrapper_pid': wrapper, 'port': port, 'runtime': str(runtime.root), 'home': str(tmp_home),
            'source': str(SOURCE), 'plans': {v: os.environ[v] for v in (
                'FAKE_CLAUDE_THREAD_PLAN', 'FAKE_CODEX_PLAN', 'FAKE_CLAUDE_PLAN')},
            'stub_paths': [str(fake_claude), str(fake_codex), str(fake_opencode)]}, indent=2))
        yield NamingDaemon(origin, runtime.root, evidence,
                           {'Authorization': 'Bearer ' + paths.read_token()})
    finally:
        if paths.pid_file().exists():
            pid = int(paths.pid_file().read_text())
        if paths.port_file().exists():
            port = int(paths.port_file().read_text())
        stop = subprocess.run([str(script), 'stop'], env=child_env, timeout=20)
        closed = False
        if port is not None:
            with socket.socket() as probe:
                probe.settimeout(1)
                closed = probe.connect_ex(('127.0.0.1', port)) != 0
        if pid is not None:
            stat = Path(f'/proc/{pid}/stat')
            dead = not stat.exists() or stat.read_text().split(') ', 1)[1].split()[0] == 'Z'
        else:
            dead = False
        native_file = witness / 'native-children.jsonl'
        native_rows = [json.loads(line) for line in native_file.read_text().splitlines()] if native_file.exists() else []
        remaining = []
        for row in native_rows:
            for owner in ('plan', 'stub'):
                child_stat = Path(f'/proc/{row[owner + "_pid"]}/stat')
                if child_stat.exists():
                    fields = child_stat.read_text().split(') ', 1)[1].split()
                    if fields[0] != 'Z' and fields[19] == row[owner + '_start']:
                        remaining.append(row[owner + '_pid'])
        (evidence / 'daemon-closure.json').write_text(json.dumps({
            'stop_exit': stop.returncode, 'pid': pid, 'port': port,
            'pid_dead': dead, 'listener_closed': closed, 'remaining_native_pids': remaining}))
        assert stop.returncode == 0 and dead and closed and not remaining, 'owned daemon/native cleanup incomplete'

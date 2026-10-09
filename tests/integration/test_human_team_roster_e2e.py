"""Finite THR296 shipping cases. Execute only via authorized disposable parent.

No production-host runs. RF5/RF6 source-bound fault observation, writer barriers,
C5 history/portability, C7-C9 maintenance and browser cases need further owners.
"""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import httpx
import pytest
import yaml

from tests.integration.conftest import seed_workspace
from tests.integration.test_subtask_self_decompose_e2e import _auth_headers, _wait_for_terminal

pytestmark = pytest.mark.integration
VERDICTS = [(None, 'none'), ('', 'blank'), ('CUSTOM_REVIEW_OUTCOME', 'custom'),
            ('APPROVE', 'approve'), ('PASS', 'pass'), ('REQUEST_CHANGES', 'request_changes'),
            ('REVISE', 'revise'), ('BLOCK', 'block')]


@pytest.fixture
def human_daemon(runtime: Path, request: pytest.FixtureRequest,
                 fake_claude_plan_env: Path, fake_codex_plan_env: Path) -> tuple[int, Path]:
    roster = yaml.safe_load((runtime / 'org/teams.yaml').read_text())
    roster['teams']['default'] = {'manager': {'kind': 'human', 'principal': 'founder'},
                                 'workers': ['consultant_head', 'consultant_codex']}
    roster.update(default_team='default', task_default_team='engineering')
    (runtime / 'org/teams.yaml').write_text(yaml.safe_dump(roster))
    seed_workspace(runtime, 'consultant_head')
    seed_workspace(runtime, 'consultant_codex', executor='codex')
    # Plans are explicit; normal callback verification never invokes providers.
    _write_plan(fake_claude_plan_env, runtime, status='completed', verdict=None, self_child=False)
    _write_plan(fake_codex_plan_env, runtime, status='completed', verdict=None, self_child=False)
    return request.getfixturevalue('live_daemon'), runtime


def _write_plan(path: Path, root: Path, *, status: str, verdict: str | None, self_child: bool, recovery: bool = False) -> None:
    witness = path.parent / (path.name + '.calls.jsonl')
    # Existing bound fake binaries supply real task/runtime-session arguments;
    # this plan calls the supported callback, never writes task/results/audits.
    path.write_text('''#!/usr/bin/env bash
set -euo pipefail
python - "$1" "$2" "$PWD" <<'PLAN'
import json, pathlib, subprocess, sys
T, S, workspace = sys.argv[1:]
agent = pathlib.Path(workspace).name
org = pathlib.Path(workspace).parent.parent.name
''' + f"root = pathlib.Path({str(root)!r})\nwitness = pathlib.Path({str(witness)!r})\nstatus = {status!r}\nverdict = {verdict!r}\nself_child = {self_child!r}\nrecovery = {recovery!r}\n" + '''
with witness.open('a') as out:
    out.write(json.dumps({'task': T, 'session': S, 'agent': agent}) + '\\n')
# Independent read determines actual root/child provenance. The plan never
# manufactures a result or seeds the final transition.
import sqlite3
with sqlite3.connect((root / 'happyranch.db').as_uri() + '?mode=ro', uri=True) as conn:
    parent = conn.execute('SELECT parent_task_id FROM tasks WHERE id=?', (T,)).fetchone()[0]
    children = conn.execute('SELECT COUNT(*) FROM tasks WHERE parent_task_id=?', (T,)).fetchone()[0]
    if recovery and parent is not None:
        accepted = conn.execute('SELECT recovery_session_id FROM task_completion_recoveries WHERE task_id=?', (T,)).fetchone()
        if accepted is None:
            sys.exit(0)  # genuine Codex clean omission; native runner claims recovery
        assert accepted[0] == S, (accepted, S)
payload = {'task_id': T, 'session_id': S, 'agent': agent, 'status': 'completed', 'summary': 'root done', 'confidence': 90}
if self_child and parent is None and children == 0:
    payload['decision'] = {'action': 'delegate', 'agent': agent, 'prompt': 'self child'}
elif parent is None:
    payload['decision'] = {'action': 'done', 'summary': 'root done'}
else:
    payload.update(status=status, verdict=verdict, summary='child outcome')
file = pathlib.Path(workspace) / ('completion-' + S + '.json')
file.write_text(json.dumps(payload))
subprocess.run(['happyranch', 'report-completion', '--org', org, '--from-file', str(file)], check=True)
PLAN
''')


def _base(port: int) -> str:
    return f'http://127.0.0.1:{port}/api/v1/orgs/test'


PARTIAL_ROSTERS = [(head, codex, roster, empty)
                   for empty in (False, True)
                   for head, codex, roster in ((True, False, False), (False, True, False),
                       (False, False, True), (True, True, False), (True, False, True), (False, True, True))]


def _attach_process(root: Path, slug: str = 'test') -> subprocess.CompletedProcess[str]:
    """Actual cold OrgState attachment in a fresh candidate interpreter."""
    from tests.helpers.integration_stub_guard.guard import manifest
    binding = manifest()
    script = '''
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from runtime.config import Settings
from runtime.daemon.org_state import OrgState
assert Path(sys.modules['runtime'].__file__).resolve().parent == Path(sys.argv[1])/'runtime'
org=OrgState.load(slug=sys.argv[3],root=Path(sys.argv[2]),settings=Settings(project_root=Path(sys.argv[1])))
try:
    print(json.dumps({'agents':org.teams.all_agents(),'default':org.teams.default_team,
                      'task_default':org.teams.task_default_team}))
finally:
    org.close()
'''
    return subprocess.run([sys.executable, '-I', '-c', script, binding['source'], str(root), slug],
                          capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize('partial', [None, *PARTIAL_ROSTERS], ids=[
    'human-get', *[f"partial-{int(h)}{int(c)}{int(r)}-{'empty-default' if e else 'absent-default'}"
                   for h, c, r, e in PARTIAL_ROSTERS]])
def test_c1_registry_and_attachment(request: pytest.FixtureRequest, runtime: Path,
                                    tmp_path: Path, partial: tuple | None) -> None:
    if partial is not None:
        # A fresh fixture copy has no running database or worker. This drives
        # the shipped attachment owner, unlike the suspended validator units.
        from runtime.orchestrator.agent_def import AgentDef, render_agent_text
        cold = tmp_path / 'cold-attachment'
        shutil.copytree(runtime / 'org', cold / 'org')
        head, codex, roster_after, empty = partial
        data = yaml.safe_load((cold / 'org/teams.yaml').read_text())
        human = {'manager': {'kind': 'human', 'principal': 'founder'}, 'workers': []}
        data['teams']['consultant'] = {'manager': 'consultant_head', 'workers': ['consultant_codex']}
        if empty:
            data['teams']['default'] = human
        if roster_after:
            del data['teams']['consultant']
            data['teams']['default'] = {**human, 'workers': ['consultant_head', 'consultant_codex']}
        for name, after, executor in (('consultant_head', head, 'claude'), ('consultant_codex', codex, 'codex')):
            definition = AgentDef(name=name, team='default' if after else 'consultant',
                role='manager' if name == 'consultant_head' and not after else 'worker',
                executor=executor, allow_rules=(), repos={}, enrolled_by=None,
                enrolled_at_task=None, enrolled_at=None, system_prompt=f'You are {name}.')
            (cold / 'org/agents' / f'{name}.md').write_text(render_agent_text(definition))
        (cold / 'org/teams.yaml').write_text(yaml.safe_dump(data))
        before = {str(path.relative_to(cold)): path.read_bytes() for path in (cold / 'org').rglob('*') if path.is_file()}
        actual = _attach_process(cold)
        assert actual.returncode != 0, actual.stdout
        assert 'OrgConsistencyError: org content is inconsistent' in actual.stderr, actual.stderr
        assert {str(path.relative_to(cold)): path.read_bytes() for path in (cold / 'org').rglob('*') if path.is_file()} == before
        assert not (cold / 'workspaces/founder').exists()
        with sqlite3.connect(cold / 'happyranch.db') as conn:
            assert conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0
            assert conn.execute('SELECT COUNT(*) FROM workflow_publication_journals').fetchone()[0] == 0
        return
    port, root = request.getfixturevalue('human_daemon')
    reply = httpx.get(_base(port) + '/teams', headers=_auth_headers()).raise_for_status().json()
    assert next(row for row in reply['teams'] if row['name'] == 'default') == {
        'name': 'default', 'manager': None, 'manager_kind': 'human', 'human_manager': 'founder',
        'is_default': True, 'workers': ['consultant_head', 'consultant_codex']}
    agents = httpx.get(_base(port) + '/agents', headers=_auth_headers()).raise_for_status().json()['agents']
    assert not any(agent['name'] == 'founder' for agent in agents)
    assert not (root / 'workspaces/founder').exists()
    for name in ('consultant_head', 'consultant_codex'):
        assert next(agent for agent in agents if agent['name'] == name)['role'] == 'worker'
    # Deliberate new creation persists both Default pointers; cold reopen
    # preserves them while the empty roster remains fenced for reviewers.
    created = httpx.post(f'http://127.0.0.1:{port}/api/v1/orgs',
                         json={'slug': 'fresh-default'}, headers=_auth_headers())
    assert created.status_code == 200, created.text
    fresh = root.parent / 'fresh-default'
    canonical = (fresh / 'org/teams.yaml').read_bytes()
    data = yaml.safe_load(canonical)
    assert data['default_team'] == data['task_default_team'] == 'default'
    assert data['teams'] == {'default': {'manager': {'kind': 'human', 'principal': 'founder'}, 'workers': []}}
    fresh_teams = httpx.get(f'http://127.0.0.1:{port}/api/v1/orgs/fresh-default/teams', headers=_auth_headers()).raise_for_status().json()
    assert fresh_teams['teams'] == [{'name': 'default', 'manager': None, 'manager_kind': 'human',
                                    'human_manager': 'founder', 'is_default': True, 'workers': []}]
    with sqlite3.connect(fresh / 'happyranch.db') as conn:
        assert conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0
        assert conn.execute("SELECT state FROM workflow_authority_pointers WHERE namespace='org/fresh-default'").fetchone() == ('fenced',)
    # Reopen a closed independently created empty fixture rather than copy
    # or attach this live daemon-owned database in a second writer.
    empty = tmp_path / 'empty-cold'
    (empty / 'org/agents').mkdir(parents=True)
    (empty / 'org/teams.yaml').write_bytes(canonical)
    reopened = _attach_process(empty)
    assert reopened.returncode == 0, reopened.stderr
    assert json.loads(reopened.stdout.splitlines()[-1]) == {'agents': [], 'default': 'default', 'task_default': 'default'}
    assert (empty / 'org/teams.yaml').read_bytes() == canonical
    assert not (empty / 'workspaces/founder').exists()


@pytest.mark.parametrize('owner,code,http_status', [
    (None, 'owner_required_for_human_team', 422),
    ('founder', 'unknown_owner', 400),
    ('missing_worker', 'unknown_owner', 400),
    ('dev_agent', 'owner_team_mismatch', 400),
    ('consultant_head', None, 200),
    ('consultant_codex', None, 200),
    ('legacy-omitted', None, 200),
], ids=['missing-owner', 'founder', 'unknown-worker', 'other-team-worker',
        'head-with-attachment', 'codex-owner-only-with-attachment', 'legacy-omitted-with-attachment'])
def test_c2_owner_required_before_persistence(human_daemon: tuple[int, Path],
                                             owner: str | None, code: str, http_status: int) -> None:
    port, root = human_daemon
    upload = httpx.post(_base(port) + '/tasks/attachments', headers=_auth_headers(),
                        params={'agent': 'founder'},
                        files={'file': ('roster.png', b'\x89PNG\r\n\x1a\nfixture', 'image/png')})
    assert upload.status_code == 200, upload.text
    attachment = upload.json()
    with sqlite3.connect(root / 'happyranch.db') as conn:
        before = conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
        before_attachments = conn.execute('SELECT COUNT(*) FROM task_attachments').fetchone()[0]
    body = {'team': 'default', 'brief': 'owner guard before any durable allocation',
            'attachments': [{'storage_key': attachment['storage_key'], 'display_name': 'roster.png'}]}
    if owner == 'legacy-omitted':
        del body['team']
    elif owner == 'consultant_codex':
        del body['team']
        body['owner'] = owner
    elif owner is not None:
        body['owner'] = owner
    reply = httpx.post(_base(port) + '/tasks', json=body, headers=_auth_headers())
    assert reply.status_code == http_status
    if code is None:
        task_id = reply.json()['task_id']
        final = _wait_for_terminal(_base(port), task_id)
        expected_owner = 'engineering_head' if owner == 'legacy-omitted' else owner
        assert final['task']['status'] == 'completed', final
        with sqlite3.connect(root / 'happyranch.db') as conn:
            assert conn.execute('SELECT assigned_agent,team FROM tasks WHERE id=?', (task_id,)).fetchone() == (
                expected_owner, 'engineering' if owner == 'legacy-omitted' else 'default')
            assert conn.execute('SELECT storage_key FROM task_attachments WHERE task_id=?', (task_id,)).fetchall() == [(attachment['storage_key'],)]
            results = conn.execute('SELECT id,session_id,agent FROM task_results WHERE task_id=?', (task_id,)).fetchall()
            assert len(results) == 1 and type(results[0][0]) is int and results[0][0] > 0
            assert results[0][1] and results[0][2] == expected_owner
        return
    assert reply.json()['detail']['code'] == code
    with sqlite3.connect(root / 'happyranch.db') as conn:
        assert conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == before
        assert conn.execute('SELECT COUNT(*) FROM task_attachments').fetchone()[0] == before_attachments
        assert conn.execute('SELECT COUNT(*) FROM task_results').fetchone()[0] == 0


@pytest.mark.parametrize('agent', ['consultant_head', 'consultant_codex'], ids=['head', 'codex'])
def test_c3_worker_lifecycle_and_denials(human_daemon: tuple[int, Path], agent: str) -> None:
    port, root = human_daemon
    reply = httpx.post(_base(port) + '/tasks', json={'team': 'default', 'owner': agent, 'brief': 'ordinary root'}, headers=_auth_headers()).raise_for_status().json()
    final = _wait_for_terminal(_base(port), reply['task_id'])
    assert final['task']['status'] == 'completed'
    with sqlite3.connect(root / 'happyranch.db') as conn:
        actual = conn.execute('SELECT id,session_id,agent FROM task_results WHERE task_id=?', (reply['task_id'],)).fetchall()
        assert len(actual) == 1 and type(actual[0][0]) is int and actual[0][1] and actual[0][2] == agent
    # Current human-team worker is not an eligible manager policy target.
    denied = httpx.get(_base(port) + f'/agents/{agent}/team-escalation-policy', headers=_auth_headers())
    assert denied.status_code == 404


@pytest.mark.parametrize('agent,recovery', [('consultant_head', False), ('consultant_codex', False), ('consultant_codex', True)], ids=['head', 'codex', 'codex-accepted'])
@pytest.mark.parametrize('status', ['completed', 'blocked'], ids=['completed', 'failed'])
@pytest.mark.parametrize('verdict', [value for value, _ in VERDICTS], ids=[name for _, name in VERDICTS])
def test_c4_normal_and_recovered_verdict_attribution(
    human_daemon: tuple[int, Path], agent: str, recovery: bool, status: str, verdict: str | None,
    fake_claude_plan_env: Path, fake_codex_plan_env: Path,
) -> None:
    # Normal and genuine two-invocation Codex admission. RF5/RF6 external
    # crash cuts/writer barriers remain separately unfinished; no fake marker.
    port, root = human_daemon
    plan = fake_claude_plan_env if agent == 'consultant_head' else fake_codex_plan_env
    _write_plan(plan, root, status=status, verdict=verdict, self_child=True, recovery=recovery)
    reply = httpx.post(_base(port) + '/tasks', json={'team': 'default', 'owner': agent, 'brief': 'self child then final parent'}, headers=_auth_headers()).raise_for_status().json()
    final = _wait_for_terminal(_base(port), reply['task_id'])
    assert final['task']['status'] == 'completed'
    with sqlite3.connect(root / 'happyranch.db') as conn:
        children = conn.execute('SELECT id,status,assigned_agent FROM tasks WHERE parent_task_id=?', (reply['task_id'],)).fetchall()
        assert len(children) == 1 and children[0][1:] == ('completed' if status == 'completed' else 'failed', agent)
        rows = conn.execute("SELECT agent,payload FROM audit_log WHERE task_id=? AND action='review_verdict'", (children[0][0],)).fetchall()
        assert len(rows) == 1 and rows[0][0] == agent
        assert json.loads(rows[0][1]) == {'verdict': verdict if verdict is not None else 'approved' if status == 'completed' else 'rejected',
            'feedback': 'child outcome' if status == 'completed' else 'self-blocked: child outcome', 'reviewed_agent': agent}
        parent_results = conn.execute('SELECT id,session_id FROM task_results WHERE task_id=? ORDER BY id', (reply['task_id'],)).fetchall()
        assert len(parent_results) == 2 and all(type(row[0]) is int and row[0] > 0 and row[1] for row in parent_results)
        assert parent_results[0][1] != parent_results[1][1]

        if recovery:
            episode = conn.execute('SELECT origin_session_id,recovery_session_id,accepted_result_id,accepted_result_session_id,state,provider_session_id FROM task_completion_recoveries WHERE task_id=?', (children[0][0],)).fetchall()
            assert len(episode) == 1
            S0, S1, R, accepted_session, state, provider = episode[0]
            assert S0 and S1 and S0 != S1 and provider
            assert type(R) is int and R > 0 and accepted_session == S1 and state == 'callback_consumed'
            selected = conn.execute('SELECT task_id,agent,session_id,verdict FROM task_results WHERE id=?', (R,)).fetchone()
            assert selected == (children[0][0], agent, S1, verdict)
            assert conn.execute('SELECT COUNT(*) FROM task_results WHERE task_id=?', (children[0][0],)).fetchone()[0] == 1
            bound = [json.loads(row[0]) for row in conn.execute(
                "SELECT payload FROM audit_log WHERE task_id=? AND action='completion_report' ORDER BY id",
                (children[0][0],)) if '_result_row_id' in json.loads(row[0])]
            assert len(bound) == 1 and bound[0]['_result_row_id'] == R and bound[0]['_recovery_session_id'] == S1
            witness = Path(str(plan) + '.calls.jsonl')
            calls = [json.loads(line) for line in witness.read_text().splitlines()]
            child_calls = [call for call in calls if call['task'] == children[0][0]]
            assert child_calls == [
                {'task': children[0][0], 'session': S0, 'agent': agent},
                {'task': children[0][0], 'session': S1, 'agent': agent},
            ]


@pytest.mark.parametrize('roster_kind', ['human', 'legacy-agent-control'])
def test_c5_schema_history_publication_and_portability(runtime: Path, tmp_path: Path,
                                                       roster_kind: str) -> None:
    """Real current publication/cold reader plus a pinned admission input.

    This L subcase owns current JSON/cold-process admission. Historical
    activation/receipt fixtures, compatible M restore and old-source execution
    are separately required; this case does not substitute for those proofs.
    """
    from tests.helpers.integration_stub_guard.guard import manifest
    binding = manifest()
    if roster_kind == 'human':
        data = yaml.safe_load((runtime / 'org/teams.yaml').read_text())
        data['teams']['default'] = {'manager': {'kind': 'human', 'principal': 'founder'},
                                    'workers': ['consultant_head', 'consultant_codex']}
        data.update(default_team='default', task_default_team='engineering')
        (runtime / 'org/teams.yaml').write_text(yaml.safe_dump(data))
        seed_workspace(runtime, 'consultant_head')
        seed_workspace(runtime, 'consultant_codex', executor='codex')
    # No daemon owns this fixture. Each actual attachment process exits/closes
    # before independent source/row readback and before the next process.
    attached = _attach_process(runtime)
    assert attached.returncode == 0, attached.stderr
    authority = runtime / 'org/.workflow-authority.json'
    raw = authority.read_bytes()
    snapshot = json.loads(raw)
    assert snapshot['schema_version'] == 2
    assert snapshot['task_default_team'] == 'engineering'
    assert all(set(row['manager']) == {'kind', 'principal'} for row in snapshot['teams'])
    if roster_kind == 'human':
        assert snapshot['default_team'] == 'default'
        assert next(row for row in snapshot['teams'] if row['name'] == 'default') == {
            'name': 'default', 'manager': {'kind': 'human', 'principal': 'founder'},
            'workers': ['consultant_codex', 'consultant_head']}
        assert 'founder' not in [row['name'] for row in snapshot['agents']]
    else:
        assert snapshot['default_team'] == 'engineering'
        assert all(row['manager']['kind'] == 'agent' for row in snapshot['teams'])
    with sqlite3.connect(runtime / 'happyranch.db') as conn:
        before = conn.execute('SELECT * FROM workflow_publication_journals ORDER BY rowid').fetchall()
        pointer = conn.execute('SELECT current_generation,snapshot_digest,state FROM workflow_authority_pointers WHERE namespace=?', ('org/test',)).fetchone()
        assert pointer == (1, hashlib.sha256(raw).hexdigest(), 'ready')
        assert conn.execute('SELECT COUNT(*) FROM workflow_publication_leases').fetchone()[0] == 0
    reopened = _attach_process(runtime)
    assert reopened.returncode == 0, reopened.stderr
    assert authority.read_bytes() == raw
    with sqlite3.connect(runtime / 'happyranch.db') as conn:
        assert conn.execute('SELECT * FROM workflow_publication_journals ORDER BY rowid').fetchall() == before
    probe = Path(binding['source']) / 'tests/helpers/human_team_incompatible_reader_probe.py'
    reader_home = tmp_path / 'reader-home'
    reader_home.mkdir(mode=0o700)
    # Parent admission already closes the environment. Override only this
    # separately owned reader's home; never attach an ambient registered org.
    reader_environment = {**os.environ, 'HAPPYRANCH_DAEMON_HOME': str(reader_home)}
    command = [sys.executable, '-I', str(probe), '--source', binding['source'],
               '--source-sha', binding['revision'], '--root', str(runtime), '--org', 'test',
               '--operation', 'capture-admission', '--expect', 'admitted',
               '--snapshot-digest', hashlib.sha256(raw).hexdigest()]
    actual = subprocess.run(command, env=reader_environment, capture_output=True, text=True, timeout=30)
    assert actual.returncode == 0, (actual.stdout, actual.stderr)
    receipt = json.loads(actual.stdout.splitlines()[-1])
    assert receipt['actual'] == 'admitted' and receipt['persisted_readback_unchanged']
    assert receipt['reader_source_sha'] == binding['revision']
    assert receipt['snapshot_digest'] == hashlib.sha256(raw).hexdigest()
    (tmp_path / 'C5-current-reader-receipt.json').write_text(json.dumps(
        {'command': command, 'daemon_home': str(reader_home), 'exit': actual.returncode,
         'receipt': receipt}, sort_keys=True))


@pytest.mark.parametrize('refusal', ['no-inhibition', 'design-plan-not-manifest', 'wrong-digest'])
def test_c8_preflight_refusals_and_backup_cas(runtime: Path, tmp_path: Path, refusal: str) -> None:
    """L-only early input refusals through the genuine standalone utility.

    These controls require no systemd/process census or M simulation. Complete
    checked-backup CAS, mutation syscall/frame witnesses and successful recovery
    remain separate M/capability cases. Byte equality is not no-write proof.
    """
    from tests.helpers.human_team_incompatible_reader_probe import _closed_files
    from tests.helpers.integration_stub_guard.guard import manifest
    binding = manifest()
    attached = _attach_process(runtime)
    assert attached.returncode == 0, attached.stderr
    # This is explicitly a proposed input, never a production check receipt.
    proposed = tmp_path / 'proposed-plan.json'
    raw = json.dumps({'kind': 'THR296-design-plan-v1', 'source_sha': binding['revision'],
                      'operation_id': 'unexecuted-proposal', 'containment': {}}).encode()
    proposed.write_bytes(raw)
    arguments = ['--runtime-root', str(runtime.parent.parent), '--org', 'test']
    if refusal == 'no-inhibition':
        arguments.extend(['--check', '--plan', str(proposed)])
        expected = 'actual_persistent_restart_inhibition_required'
    else:
        arguments.extend(['--apply', '--manifest', str(proposed), '--expected-digest',
                          '0' * 64 if refusal == 'wrong-digest' else hashlib.sha256(raw).hexdigest()])
        expected = ('manifest_digest_mismatch' if refusal == 'wrong-digest'
                    else 'real_checked_manifest_and_exact_candidate_required')
    before = _closed_files(runtime.parent.parent)
    launcher = '''
import runpy,sys
from pathlib import Path
source=Path(sys.argv[1])
sys.dont_write_bytecode=True
sys.path.insert(0,str(source))
script=source/'scripts/migrate_human_team_roster.py'
sys.argv=[str(script),*sys.argv[2:]]
runpy.run_path(str(script),run_name='__main__')
'''
    command = [sys.executable, '-I', '-c', launcher, binding['source'], *arguments]
    actual = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert actual.returncode == 1, (actual.stdout, actual.stderr)
    assert expected in actual.stderr, actual.stderr
    assert _closed_files(runtime.parent.parent) == before
    (tmp_path / 'C8-refusal-receipt.json').write_text(json.dumps(
        {'source_sha': binding['revision'], 'command': command, 'exit': actual.returncode,
         'refusal': expected, 'closed_file_readback_unchanged': True,
         'transient_write_proof': 'separate observer required', 'M_success': 'not attempted'}, sort_keys=True))

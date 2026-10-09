"""Finite THR296 shipping cases. Execute only via authorized disposable parent.

No production-host runs. RF5/RF6 source-bound fault observation, writer barriers,
C5/C7-C9 and bilingual browser selections still require their dedicated owners.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

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


def test_c1_registry_and_attachment(human_daemon: tuple[int, Path]) -> None:
    port, root = human_daemon
    reply = httpx.get(_base(port) + '/teams', headers=_auth_headers()).raise_for_status().json()
    assert next(row for row in reply['teams'] if row['name'] == 'default') == {
        'name': 'default', 'manager': None, 'manager_kind': 'human', 'human_manager': 'founder',
        'is_default': True, 'workers': ['consultant_head', 'consultant_codex']}
    agents = httpx.get(_base(port) + '/agents', headers=_auth_headers()).raise_for_status().json()['agents']
    assert not any(agent['name'] == 'founder' for agent in agents)
    assert not (root / 'workspaces/founder').exists()
    for name in ('consultant_head', 'consultant_codex'):
        assert next(agent for agent in agents if agent['name'] == name)['role'] == 'worker'


@pytest.mark.parametrize('owner,code,http_status', [
    (None, 'owner_required_for_human_team', 422),
    ('founder', 'unknown_owner', 400),
    ('missing_worker', 'unknown_owner', 400),
    ('dev_agent', 'owner_team_mismatch', 400),
], ids=['missing-owner', 'founder', 'unknown-worker', 'other-team-worker'])
def test_c2_owner_required_before_persistence(human_daemon: tuple[int, Path],
                                             owner: str | None, code: str, http_status: int) -> None:
    port, root = human_daemon
    with sqlite3.connect(root / 'happyranch.db') as conn:
        before = conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
        before_attachments = conn.execute('SELECT COUNT(*) FROM task_attachments').fetchone()[0]
    body = {'team': 'default', 'brief': 'owner guard before any durable allocation'}
    if owner is not None:
        body['owner'] = owner
    reply = httpx.post(_base(port) + '/tasks', json=body, headers=_auth_headers())
    assert reply.status_code == http_status
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

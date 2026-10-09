"""Explicit real-daemon supplements to the twelve naming business scenarios.

Finite shipping HTTP/CLI/native-callback/browser assertions; no ASGI transport.
Static-authoring leg does not execute or collect this module.
"""
from __future__ import annotations

import json
import re
import subprocess

import httpx
import pytest

from tests.helpers.identity_names.live import (
    SOURCE, naming_daemon, persistent_identity, sql_rows, wait_for,
)

pytestmark = pytest.mark.integration


def test_scenario2_browser_agent_rename_and_stale_readback(naming_daemon):
    daemon = naming_daemon
    beta = persistent_identity(daemon.beta)
    canonical = {str(p.relative_to(daemon.alpha)): p.read_bytes()
                 for p in (daemon.alpha / 'org').rglob('*') if p.is_file()}
    report = daemon.browser('agent')
    assert report['responses'] == [200, 409]
    assert report['persistedName'] == 'ExternalSam'
    current = sql_rows(daemon.alpha,
        "SELECT current_label,revision FROM identity_name_owners WHERE kind='agent' AND canonical_id='maker'")
    assert current == [['ExternalSam', report['revision']]]
    changes = sql_rows(daemon.alpha, "SELECT payload FROM audit_log WHERE action='identity_name_changed' ORDER BY id")
    labels = [json.loads(row[0])['new_label'] for row in changes]
    assert labels == ['BrowserSam', 'ExternalSam'] and 'KeepDraft' not in labels
    assert persistent_identity(daemon.beta) == beta
    assert {str(p.relative_to(daemon.alpha)): p.read_bytes()
            for p in (daemon.alpha / 'org').rglob('*') if p.is_file()} == canonical


def test_scenario3_browser_founder_name_and_durable_human_inbox(naming_daemon):
    daemon = naming_daemon
    report = daemon.browser('founder')
    assert report['responses'] == [200]
    assert sql_rows(daemon.alpha,
        "SELECT kind,canonical_id,lifecycle,current_label,revision FROM identity_name_owners WHERE kind='founder'") == [
            ['founder', 'founder', 'founder', 'HumanBoss', 2]]
    before = sql_rows(daemon.alpha, 'SELECT * FROM thread_invocations ORDER BY rowid')
    response = daemon.request('POST', '/threads', json={
        'subject': 'Human delivery', 'recipients': ['HumanBoss'], 'body_markdown': '@HumanBoss'})
    assert response.status_code == 200, response.text
    thread = response.json()['thread_id']
    assert sql_rows(daemon.alpha, 'SELECT * FROM thread_invocations ORDER BY rowid') == before
    assert sql_rows(daemon.alpha, 'SELECT agent_name FROM thread_participants WHERE thread_id=?', (thread,)) == []
    assert sql_rows(daemon.alpha, 'SELECT composed_by FROM threads WHERE id=?', (thread,)) == [['founder']]
    assert sql_rows(daemon.alpha, 'SELECT speaker,body_markdown,mentions_json FROM thread_messages WHERE thread_id=?',
                    (thread,)) == [['founder', '@HumanBoss', '["founder"]']]
    inbox = daemon.request('GET', '/threads')
    assert inbox.status_code == 200, inbox.text
    assert any(row['thread_id'] == thread for row in inbox.json()['threads'])


def test_scenario6_live_current_name_callback_and_unknown_fallback(naming_daemon):
    daemon = naming_daemon
    daemon.rename('maker', 'Sam')
    daemon.rename('founder', 'Chief')
    response = daemon.request('POST', '/threads', json={
        'subject': 'Actual native delivery', 'recipients': ['sAM'], 'body_markdown': '@SAM'})
    assert response.status_code == 200, response.text
    thread = response.json()['thread_id']
    messages = wait_for(lambda: sql_rows(daemon.alpha,
        'SELECT seq,speaker,body_markdown FROM thread_messages WHERE thread_id=? ORDER BY seq', (thread,)),
        lambda rows: any(row[1:] == ['maker', '@founder naming delivery maker'] for row in rows))
    assert messages[0] == [1, 'founder', '@SAM']
    assert sql_rows(daemon.alpha,
        'SELECT agent_name,status,reply_message_seq FROM thread_invocations WHERE thread_id=?', (thread,)) == [
            ['maker', 'consumed', 2]]
    from tests.helpers.integration_stub_guard.guard import assert_launch_witness
    assert_launch_witness('claude')
    # Ordinary unknown body still broadcasts to the actual stable roster.
    fallback = daemon.request('POST', '/threads', json={
        'subject': 'Unknown remains ordinary', 'recipients': ['Sam', 'manager'], 'body_markdown': '@not_a_name'})
    assert fallback.status_code == 200, fallback.text
    other = fallback.json()['thread_id']
    assert {r[0] for r in sql_rows(daemon.alpha,
        'SELECT agent_name FROM thread_invocations WHERE thread_id=?', (other,))} == {'maker', 'manager'}
    assert sql_rows(daemon.alpha,
        'SELECT mentions_json FROM thread_messages WHERE thread_id=? AND seq=1', (other,)) == [['[]']]
    wait_for(lambda: sql_rows(daemon.alpha,
        'SELECT agent_name,status FROM thread_invocations WHERE thread_id=? ORDER BY agent_name', (other,)),
        lambda rows: rows == [['maker', 'consumed'], ['manager', 'consumed']])
    assert_launch_witness('claude', callbacks=3)


def test_scenario8_live_cli_picker_and_persisted_ids(naming_daemon):
    daemon = naming_daemon
    daemon.rename('manager', 'Lead')
    callback = subprocess.run(['happyranch', 'run', '--org', 'alpha', '--owner', 'lEAD',
        '--brief', 'naming fixture actual CLI current owner'], capture_output=True, text=True, timeout=15)
    assert callback.returncode == 0, callback.stdout + callback.stderr
    match = re.search(r'Submitted (TASK-\d+)', callback.stdout)
    assert match, callback.stdout
    task = match[1]
    result = wait_for(lambda: sql_rows(daemon.alpha,
        'SELECT status,assigned_agent FROM tasks WHERE id=?', (task,)), lambda rows: rows == [['completed', 'manager']])
    assert result == [['completed', 'manager']]
    assert sql_rows(daemon.alpha,
        'SELECT agent FROM task_results WHERE task_id=?', (task,)) == [['manager']]
    from tests.helpers.integration_stub_guard.guard import assert_launch_witness
    assert_launch_witness('codex')
    report = daemon.browser('picker')
    assert 'maker' in report['taskQueries'] and not any(q in ('PickerSam', 'NewPickerSam') for q in report['taskQueries'])
    thread = report['threadId']
    assert sql_rows(daemon.alpha,
        'SELECT agent_name FROM thread_participants WHERE thread_id=?', (thread,)) == [['maker']]
    assert sql_rows(daemon.alpha,
        'SELECT speaker,body_markdown FROM thread_messages WHERE thread_id=? AND seq=1', (thread,)) == [
            ['founder', 'Keep literal @unrecognized']]
    assert sql_rows(daemon.alpha,
        "SELECT current_label FROM identity_name_owners WHERE canonical_id='maker'") == [['FinalPickerSam']]
    assert len(report['screenshots']) == 32
    assert all((daemon.evidence / 'browser-picker' / name).stat().st_size > 0 for name in report['screenshots'])


def test_served_openapi_matches_supported_snapshot(naming_daemon):
    """Actual HTTP-served schema, separately from the offline generator check."""
    from scripts.generate_openapi_snapshot import _summarize
    response = httpx.get(naming_daemon.origin + '/openapi.json', timeout=5)
    assert response.status_code == 200
    schema = response.json()
    assert _summarize(schema) == json.loads((SOURCE / 'tests/contract/openapi.json').read_text())
    for path in ('/api/v1/orgs/{slug}/agents/{agent_id}/addressable-name',
                 '/api/v1/orgs/{slug}/founder/addressable-name'):
        assert 'put' in schema['paths'][path]
    (naming_daemon.evidence / 'served-openapi.json').write_text(json.dumps(schema, sort_keys=True))


def test_scenario9_live_history_authority_reopen(naming_daemon):
    daemon = naming_daemon
    daemon.rename('manager', 'Lead')
    submitted = subprocess.run(['happyranch', 'run', '--org', 'alpha', '--owner', 'Lead',
        '--brief', 'naming history keeper'], capture_output=True, text=True, timeout=15)
    assert submitted.returncode == 0, submitted.stderr
    task = re.search(r'Submitted (TASK-\d+)', submitted.stdout).group(1)
    wait_for(lambda: sql_rows(daemon.alpha, 'SELECT status FROM tasks WHERE id=?', (task,)),
             lambda rows: rows == [['completed']])
    def retained():
        return {
            'task': sql_rows(daemon.alpha, 'SELECT * FROM tasks WHERE id=?', (task,)),
            'result': sql_rows(daemon.alpha, 'SELECT * FROM task_results WHERE task_id=?', (task,)),
            'session_usage': sql_rows(daemon.alpha, 'SELECT * FROM session_token_usage WHERE task_id=?', (task,)),
            'authority': sql_rows(daemon.alpha,
                "SELECT * FROM audit_log WHERE task_id=? AND action IN ('authority_policy_session_binding','authority_policy_selector_session_binding') ORDER BY id", (task,)),
            'v2_bindings': sql_rows(daemon.alpha, 'SELECT * FROM authority_policy_v2_session_bindings ORDER BY rowid'),
        }
    frozen = wait_for(retained, lambda rows: bool(rows['result']) and bool(rows['authority']))
    assert all(json.loads(row[4])['session_id'] for row in frozen['authority'])
    assert all(row[2] == 'manager' for row in frozen['authority'])
    work = daemon.alpha / 'workspaces' / 'manager'
    history = work / 'task_history.md'
    wait_for(lambda: history.read_text() if history.exists() else '', lambda text: task in text)
    files = {p.name: p.read_bytes() for p in work.iterdir() if p.is_file()}
    daemon.rename('manager', 'LaterLead')
    assert retained() == frozen
    daemon.restart()
    assert retained() == frozen
    assert {p.name: p.read_bytes() for p in work.iterdir() if p.is_file()} == files
    assert daemon.identity('manager')['addressable_name'] == 'LaterLead'
    assert sql_rows(daemon.alpha, 'SELECT assigned_agent FROM tasks WHERE id=?', (task,)) == [['manager']]
    assert sql_rows(daemon.alpha, 'SELECT agent FROM task_results WHERE task_id=?', (task,)) == [['manager']]
    from tests.helpers.integration_stub_guard.guard import assert_launch_witness
    assert_launch_witness('codex')

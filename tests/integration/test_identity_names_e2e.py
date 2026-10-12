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
    # Durable mentions contain eligible agent participants; the human founder
    # is classified separately and must never become an agent mention.
    assert sql_rows(daemon.alpha, 'SELECT speaker,body_markdown,mentions_json FROM thread_messages WHERE thread_id=?',
                    (thread,)) == [['founder', '@HumanBoss', '[]']]
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
    # Both initial recipients must reply. Depending on actual callback ordering,
    # the existing exchange can also release a covered-range catch-up. Each
    # extra wake needs its real audit provenance and consumed callback link.
    invocations = wait_for(lambda: sql_rows(daemon.alpha,
        'SELECT agent_name,status,triggering_seq,invocation_token,reply_message_seq '
        'FROM thread_invocations WHERE thread_id=? ORDER BY id', (other,)),
        lambda rows: {row[0] for row in rows} == {'maker', 'manager'}
        and all(row[1] == 'consumed' for row in rows))
    assert sorted(row[0] for row in invocations if row[2] == 1) == ['maker', 'manager']
    wakes = [json.loads(row[0]) for row in sql_rows(daemon.alpha,
        "SELECT payload FROM audit_log WHERE task_id=? AND action IN "
        "('thread_reply_wake_created','thread_reply_wake_settled') ORDER BY id", (other,))]
    links = sql_rows(daemon.alpha,
        'SELECT i.invocation_token,m.seq,m.speaker,m.body_markdown '
        'FROM thread_invocations i JOIN thread_messages m '
        'ON m.thread_id=i.thread_id AND m.seq=i.reply_message_seq '
        'WHERE i.thread_id=? ORDER BY i.id', (other,))
    assert len(links) == len(invocations)
    assert len({row[3] for row in invocations}) == len(invocations)
    assert len({row[1] for row in links}) == len(links)
    for invocation, link in zip(invocations, links, strict=True):
        agent, _status, triggering_seq, token, reply_seq = invocation
        assert type(reply_seq) is int and reply_seq > triggering_seq >= 1
        assert link == [token, reply_seq, agent, '@founder naming delivery ' + agent]
        if triggering_seq > 1:
            assert any(wake.get('agent_name') == agent and (
                wake.get('follow_on_token_prefix') == token[:8]
                or (wake.get('kind') in ('exchange_catch_up', 'deferred_catch_up')
                    and wake.get('token_prefix') == token[:8])) for wake in wakes)
    assert_launch_witness('claude', callbacks=1 + len(invocations))


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


# Accepted finite C1/C2 verification source; execution requires separate admission.
# Uses only the existing disposable naming_daemon; no production seam.

def _composition_save(daemon, name, value):
    path = daemon.evidence / name
    encoded = json.dumps(value, sort_keys=True, indent=2).encode()
    assert len(encoded) <= 1048576, 'composition evidence cap'
    pending = path.with_suffix('.partial')
    pending.write_bytes(encoded)
    pending.replace(path)


def _composition_files(root):
    # Preserve actual bytes, including the Markdown body; serialization need not match.
    return {str(p.relative_to(root)): p.read_bytes().hex()
            for p in sorted(root.rglob('*')) if p.is_file() and not p.is_symlink()}


def _composition_no_launch(daemon, roots):
    tables = ('tasks', 'task_results', 'thread_invocations', 'session_token_usage')
    witness_path = daemon.evidence / 'witness' / 'identities.jsonl'
    witness_rows = [json.loads(line) for line in witness_path.read_text().splitlines()] if witness_path.exists() else []
    provider_rows = [row for row in witness_rows if row.get('kind') != 'callback']
    return {'orgs': {str(root): {table: sql_rows(root, 'SELECT * FROM ' + table + ' ORDER BY rowid')
                               for table in tables} for root in roots},
            'provider_witness': provider_rows,
            'native_children': (daemon.evidence / 'witness' / 'native-children.jsonl').read_text()
            if (daemon.evidence / 'witness' / 'native-children.jsonl').exists() else ''}


def _composition_definition(raw):
    import yaml
    import re
    # Parse the complete frontmatter independently; retain the complete body.
    match = re.fullmatch(r'---\r?\n(.*?)\r?\n---(?:\r?\n|$)(.*)', raw.decode(), re.S)
    assert match is not None, 'canonical Markdown definition'
    metadata = yaml.safe_load(match[1])
    assert isinstance(metadata, dict)
    return metadata, match[2]


def _composition_cli(daemon, options, case_id):
    import hashlib
    import os
    import selectors
    import signal
    import time
    import shutil
    from pathlib import Path
    executable = shutil.which('happyranch')
    command = [executable, 'set-model', '--org', 'alpha', 'sAm', *options]
    record = {'argv': command, 'cwd': str(SOURCE), 'exit': None,
              'limit_seconds': 15, 'limit_bytes': 262144, 'group_absent': None,
              'nested_session_closure': 'requires admitted outer containment receipt',
              'first_failure': None, 'errors': []}
    name = 'reasoning-' + case_id
    save = lambda: _composition_save(daemon, name + '.json', record)
    child = None
    mux = selectors.DefaultSelector()
    started = time.monotonic()
    deadline = started + 15  # 13 workload + 2 cleanup; original deadline only.
    capture = bytearray()
    primary = None
    def failure(stage, exc):
        row = {'stage': stage, 'error': type(exc).__name__ + ': ' + str(exc)}
        if record['first_failure'] is None:
            record['first_failure'] = dict(row)
        record['errors'].append(row)
    save()  # No launch if initial intent cannot be retained.
    try:
        assert executable is not None, 'source CLI missing'
        resolved = Path(executable).resolve(strict=True)
        record['executable'] = {'path': executable, 'resolved': str(resolved),
                                'sha256': hashlib.sha256(resolved.read_bytes()).hexdigest()}
        save()
        child = subprocess.Popen(command, cwd=SOURCE, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, start_new_session=True)
        record.update(pid=child.pid, pgid=child.pid)
        save()  # Persist known ownership before the fallible proc identity read.
        stat = Path('/proc/' + str(child.pid) + '/stat').read_text()
        record['start_ticks'] = stat.rsplit(') ', 1)[1].split()[19]
        save()
        mux.register(child.stdout, selectors.EVENT_READ)
        while mux.get_map():
            if time.monotonic() >= deadline - 2:
                raise TimeoutError('CLI workload deadline')
            for key, _ in mux.select(min(.05, max(0, deadline - 2 - time.monotonic()))):
                data = os.read(key.fd, 16384)
                if not data:
                    mux.unregister(key.fileobj)
                    continue
                room = 262144 - len(capture)
                capture.extend(data[:room])
                (daemon.evidence / (name + '.log')).write_bytes(capture)
                if len(data) > room:
                    raise RuntimeError('CLI output cap')
        record['exit'] = child.wait(timeout=max(.001, deadline - 2 - time.monotonic()))
    except BaseException as exc:
        primary = exc
        failure('workload', exc)
    finally:
        try:
            mux.close()
        except BaseException as exc:
            failure('mux_close', exc)
        if child is not None:
            # Recheck the exact owned group even if its leader already exited.
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(child.pid, sig)
                except ProcessLookupError:
                    break
                except BaseException as exc:
                    failure('group_signal', exc)
                if sig == signal.SIGTERM:
                    try:
                        child.wait(timeout=min(.25, max(.001, deadline - time.monotonic())))
                    except subprocess.TimeoutExpired:
                        pass
                    except BaseException as exc:
                        failure('term_reap', exc)
            try:
                child.wait(timeout=max(.001, deadline - time.monotonic()))
            except BaseException as exc:
                failure('final_reap', exc)
            try:
                os.killpg(child.pid, 0)
                record['group_absent'] = False
                failure('group_readback', RuntimeError('owned group remains; outer containment required'))
            except ProcessLookupError:
                record['group_absent'] = True
            except BaseException as exc:
                failure('group_readback', exc)
            record['exit'] = child.poll()
            try:
                child.stdout.close()
            except BaseException as exc:
                failure('pipe_close', exc)
        record['elapsed_seconds'] = time.monotonic() - started
        record['captured_bytes'] = len(capture)
        if record['elapsed_seconds'] > 15:
            failure('deadline', TimeoutError('CLI total deadline exceeded'))
        try:
            (daemon.evidence / (name + '.log')).write_bytes(capture)
        except BaseException as exc:
            failure('partial_log', exc)
        try:
            save()
        except BaseException as exc:
            failure('receipt', exc)  # Missing final receipt is never a success.
    if primary is not None:
        raise primary
    assert not record['errors'] and record['exit'] == 0 and record['group_absent'], record
    return capture.decode(errors='replace')


def test_scenario1_fresh_human_default_naming_I_composition(naming_daemon):
    from pathlib import Path
    import sqlite3
    daemon = naming_daemon
    root = daemon.root / 'orgs' / 'composition-fresh'
    record = {'scope': 'fresh-only human naming + observed I/Pause tables',
              'requested_root': str(root), 'http': [], 'observations': {}, 'errors': []}
    def save():
        _composition_save(daemon, 'fresh-human-I-public.json', record)
    def request(method, url, **kwargs):
        response = httpx.request(method, daemon.origin + url, headers=daemon.headers, timeout=5, **kwargs)
        record['http'].append({'method': method, 'url': url, 'status': response.status_code, 'body': response.text})
        save()
        return response
    def observe(tag):
        with sqlite3.connect(root.resolve().as_uri() + '/happyranch.db?mode=ro', uri=True) as conn:
            queries = {
                'schema': 'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name',
                'owners': 'SELECT kind,canonical_id,lifecycle,current_label,revision FROM identity_name_owners ORDER BY kind,canonical_id',
                'claims': 'SELECT normalized_name,owner_kind,owner_id,id_reserved,permanent FROM identity_name_claims ORDER BY normalized_name',
                'renames': "SELECT id,task_id,agent,action,payload FROM audit_log WHERE action='identity_name_changed' ORDER BY id",
                'I': 'SELECT * FROM workflow_document_revision_operations',
                'Pause': 'SELECT * FROM task_pause_controls',
            }
            observed = {}
            record['observations'][tag] = observed
            for key, query in queries.items():
                observed[key] = [list(row) for row in conn.execute(query)]
                save()  # Partial SQL observations survive a later SQL/assertion error.
        return observed
    save()
    try:
        record['root_absent'] = not root.exists() and not root.is_symlink()
        save()
        assert record['root_absent']
        baseline_other = _composition_no_launch(daemon, (daemon.alpha, daemon.beta))
        created = request('POST', '/api/v1/orgs', json={'slug': 'composition-fresh'})
        assert created.status_code == 200, created.text
        assert created.json()['slug'] == 'composition-fresh'
        assert Path(created.json()['root']) == root and root.resolve() == root
        org = '/api/v1/orgs/composition-fresh'
        teams = request('GET', org + '/teams')
        assert teams.status_code == 200
        expected_teams = [{'name': 'default', 'manager': None, 'workers': [],
                           'manager_kind': 'human', 'human_manager': 'founder', 'is_default': True}]
        assert teams.json()['teams'] == expected_teams
        initial = request('GET', org + '/identities')
        assert initial.status_code == 200
        rows = initial.json()['identities']
        assert len(rows) == 1
        first = rows[0]
        assert {k: first[k] for k in ('kind', 'canonical_id', 'lifecycle', 'addressable_name',
                                     'name_revision', 'canonical_definition_revision', 'naming_status')} == {
            'kind': 'founder', 'canonical_id': 'founder', 'lifecycle': 'founder',
            'addressable_name': 'founder', 'name_revision': 1,
            'canonical_definition_revision': None, 'naming_status': 'ready'}
        files = _composition_files(root / 'org')
        no_launch = _composition_no_launch(daemon, (root,))
        record['canonical_before'] = files
        record['no_launch_before'] = no_launch
        record['other_orgs_before'] = baseline_other; save()
        before = observe('before')
        assert before['I'] == [] and before['Pause'] == [] and before['renames'] == []
        assert before['owners'] == [['founder', 'founder', 'founder', 'founder', 1]]
        assert before['claims'] == [['founder', 'founder', 'founder', 1, 0]]
        renamed = request('PUT', org + '/founder/addressable-name',
                          json={'addressable_name': 'CompositionChief', 'expected_name_revision': 1})
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()['canonical_id'] == 'founder' and renamed.json()['kind'] == 'founder'
        assert renamed.json()['addressable_name'] == 'CompositionChief' and renamed.json()['name_revision'] == 2
        after_public = request('GET', org + '/identities')
        assert after_public.status_code == 200
        final_rows = after_public.json()['identities']
        assert len(final_rows) == 1
        expected = dict(first, addressable_name='CompositionChief', name_revision=2)
        assert final_rows[0] == expected
        agents = request('GET', org + '/agents')
        assert agents.status_code == 200 and agents.json()['agents'] == []
        teams_after = request('GET', org + '/teams')
        assert teams_after.status_code == 200 and teams_after.json() == teams.json()
        after = observe('after')
        assert after['schema'] == before['schema']
        assert after['I'] == [] and after['Pause'] == []
        assert after['owners'] == [['founder', 'founder', 'founder', 'CompositionChief', 2]]
        assert after['claims'] == [['compositionchief', 'founder', 'founder', 0, 1],
                                   ['founder', 'founder', 'founder', 1, 1]]
        assert len(after['renames']) == 1
        audit = after['renames'][0]
        assert audit[1:4] == ['founder', 'founder', 'identity_name_changed']
        record['rename_audit_details'] = json.loads(audit[4]); save()
        assert record['rename_audit_details'] == {
            'org_slug': 'composition-fresh', 'kind': 'founder', 'canonical_id': 'founder',
            'old_label': 'founder', 'new_label': 'CompositionChief', 'old_revision': 1,
            'new_revision': 2, 'source': 'founder', 'actor': 'founder'}
        record['canonical_after'] = _composition_files(root / 'org')
        record['no_launch_after'] = _composition_no_launch(daemon, (root,))
        record['other_orgs_after'] = _composition_no_launch(daemon, (daemon.alpha, daemon.beta)); save()
        assert record['canonical_after'] == files
        assert record['no_launch_after'] == no_launch
        assert record['other_orgs_after'] == baseline_other
    except BaseException as exc:
        record['errors'].append(type(exc).__name__ + ': ' + str(exc))
        raise
    finally:
        record['fixture_evidence'] = str(daemon.evidence)
        record['closure'] = 'fixture teardown and admitted outer containment receipts required'
        save()


@pytest.mark.parametrize(('options', 'expected_model', 'expected_effort'), [
    (['--reasoning-effort', 'high'], 'sonnet', 'high'),
    (['--clear-reasoning-effort'], 'sonnet', None),
    ([], None, 'medium'),
    (['--model', 'haiku', '--reasoning-effort', 'high'], 'haiku', 'high'),
], ids=['effort-high', 'clear-effort', 'no-options', 'model-and-effort'])
def test_scenario8_cli_reasoning_composition(naming_daemon, options, expected_model, expected_effort, request):
    daemon = naming_daemon
    case_id = request.node.callspec.id
    record = {'case': case_id, 'http': [], 'errors': [],
              'outgoing_payload': 'UNOBSERVED: field omission/null mapping is source inference only'}
    save = lambda: _composition_save(daemon, 'reasoning-case-' + case_id + '.json', record)
    def read_agent():
        response = daemon.request('GET', '/agents')
        record['http'].append({'method': 'GET', 'path': '/agents',
                               'status': response.status_code, 'body': response.text}); save()
        assert response.status_code == 200
        rows = [row for row in response.json()['agents'] if row['name'] == 'maker']
        assert len(rows) == 1
        return rows[0]
    save()
    try:
        renamed = daemon.rename('maker', 'Sam')
        record['renamed'] = renamed; save()
        seeded = daemon.request('PUT', '/agents/maker/model', json={'model': 'sonnet', 'reasoning_effort': 'medium'})
        record['http'].append({'method': 'PUT', 'path': '/agents/maker/model',
                              'status': seeded.status_code, 'body': seeded.text}); save()
        assert seeded.status_code == 200
        canonical = daemon.alpha / 'org' / 'agents' / 'maker.md'
        seed_bytes = canonical.read_bytes()
        before, body_before = _composition_definition(seed_bytes)
        record['seed_definition_hex'] = seed_bytes.hex(); save()
        assert before['model'] == 'sonnet' and before['reasoning_effort'] == 'medium'
        public_before = read_agent()
        record['public_before'] = public_before; save()
        assert public_before['model'] == 'sonnet' and public_before['reasoning_effort'] == 'medium'
        files_before = _composition_files(daemon.alpha / 'org')
        beta_files = _composition_files(daemon.beta / 'org')
        beta_identity = persistent_identity(daemon.beta)
        no_launch = _composition_no_launch(daemon, (daemon.alpha, daemon.beta))
        identity_before = daemon.identity('maker')
        record.update(files_before=files_before, beta_files_before=beta_files, beta_identity_before=beta_identity,
                      no_launch_before=no_launch, identity_before=identity_before); save()
        cli_output = _composition_cli(daemon, options, case_id)
        record['cli_output'] = cli_output
        record['actual_cli_witness'] = (daemon.evidence / 'witness' / 'identities.jsonl').read_text()
        save()
        assert 'Model change for Sam · maker:' in cli_output
        assert 'before: sonnet' in cli_output
        assert 'after:  ' + str(expected_model) in cli_output
        after_bytes = canonical.read_bytes()
        after, body_after = _composition_definition(after_bytes)
        record['after_definition_hex'] = after_bytes.hex(); save()
        assert after.get('model') == expected_model and after.get('reasoning_effort') == expected_effort
        assert {k: v for k, v in after.items() if k not in ('model', 'reasoning_effort')} == {
            k: v for k, v in before.items() if k not in ('model', 'reasoning_effort')}
        assert body_after == body_before
        files_after = _composition_files(daemon.alpha / 'org')
        record['files_after'] = files_after; save()
        assert {k: v for k, v in files_after.items() if k != 'agents/maker.md'} == {
            k: v for k, v in files_before.items() if k != 'agents/maker.md'}
        assert not (canonical.parent / 'Sam.md').exists() and not (canonical.parent / 'sAm.md').exists()
        public_after = read_agent()
        record['public_after'] = public_after; save()
        assert public_after['name'] == 'maker' and public_after['addressable_name'] == 'Sam'
        assert public_after['model'] == expected_model and public_after['reasoning_effort'] == expected_effort
        assert {k: v for k, v in public_after.items() if k not in ('model', 'reasoning_effort', 'revision')} == {
            k: v for k, v in public_before.items() if k not in ('model', 'reasoning_effort', 'revision')}
        identity_after = daemon.identity('maker')
        assert {k: v for k, v in identity_after.items() if k != 'canonical_definition_revision'} == {
            k: v for k, v in identity_before.items() if k != 'canonical_definition_revision'}
        record['beta_files_after'] = _composition_files(daemon.beta / 'org')
        record['beta_identity_after'] = persistent_identity(daemon.beta)
        record['no_launch_after'] = _composition_no_launch(daemon, (daemon.alpha, daemon.beta)); save()
        assert record['beta_files_after'] == beta_files
        assert record['beta_identity_after'] == beta_identity
        assert record['no_launch_after'] == no_launch
    except BaseException as exc:
        record['errors'].append(type(exc).__name__ + ': ' + str(exc))
        raise
    finally:
        record['fixture_evidence'] = str(daemon.evidence)
        record['closure'] = 'fixture teardown and admitted outer containment receipts required'
        save()

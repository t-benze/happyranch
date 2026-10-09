"""Naming source assertions, NOT RUN on the live host.

The original 24-node/297-domain manifest is historical proposed work, not the
current execution gate. THR293seq34 requires twelve readable E2E scenarios;
the four routing bodies below contribute to5/6/7/9/12. A11 is retained unchanged.
CLI/prompt portions of5/8/9 are now authored; UI and detailed runner/fault branches remain unexercised.
No collection, test, SQL or candidate import was executed during authoring.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from tests.integration.identity_names_owned_cases import (
    cases, digest, emit_case, http_naming_snapshot, mounted_naming_app,
)

pytestmark = pytest.mark.integration
NODE = 'test_a11_operator_denial'
PREFIX = '/api/v1/orgs/alpha'
AGENT = PREFIX + '/agents/maker/addressable-name'
FOUNDER = PREFIX + '/founder/addressable-name'
# Independent actual binding vocabulary; no importing the production deny set.
TASK_KEYS = ('task', 'task_id', 'session', 'session_id', 'agent', 'agent_id', 'agent_name')
THREAD_KEYS = ('thread', 'thread_id', 'invocation', 'invocation_id', 'invocation_token',
               'token', 'composer', 'speaker')


def test_a11_operator_denial(tmp_path, tmp_home, case_ids=None):
    if case_ids is None:  # Historical inventory assertion is not a finite-run gate.
        assert cases(NODE) == [
            'operator', 'task-full', 'task-partial', 'task-null', 'task-malformed',
            'thread-full', 'thread-partial', 'thread-null', 'thread-malformed',
            'forged-label-header', 'foreign-org', 'stale-revision',
        ]
    for case in cases(NODE, case_ids):
        root = tmp_path / case
        root.mkdir()
        with mounted_naming_app(root) as (app, orgs, headers):
            assertions = asyncio.run(_exercise_case(case, app, orgs, headers, root))
            final_snapshot = http_naming_snapshot(orgs)
            observation = {'assertions': assertions,
                           'both_orgs_snapshot_sha256': digest(final_snapshot),
                           'receipt_root': str(tmp_path / 'receipts'),
                           'shipping_http_routes': True, 'external_sessions': 0}
        # Both fixture DBs and HTTP transport are closed before a case receipt.
        emit_case(NODE, case, observation)


async def _exercise_case(case, app, orgs, headers, root):
    org = orgs['alpha']
    revision = org.db.execute("SELECT revision FROM identity_name_owners WHERE kind='agent' AND canonical_id='maker'").fetchone()[0]
    founder_revision = org.db.execute("SELECT revision FROM identity_name_owners WHERE kind='founder'").fetchone()[0]
    body = {'addressable_name': 'Sam', 'expected_name_revision': revision}
    founder_body = {'addressable_name': 'Chief', 'expected_name_revision': founder_revision}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://naming.test') as client:
        async def refuse(path, payload, status, code, *, query=None, extra_headers=None):
            before = http_naming_snapshot(orgs)
            response = await client.put(path, json=payload, params=query,
                                        headers={**headers, **(extra_headers or {})})
            assert response.status_code == status, response.text
            assert response.json()['detail']['code'] == code
            # Full DB rows/schema/files/modes/teams and session binding equality,
            # including audit/claims/revisions/authority and untouched foreign org.
            assert http_naming_snapshot(orgs) == before
            return response

        async def deny_bindings(bindings, *, extra_headers=None):
            for path, valid in ((AGENT, body), (FOUNDER, founder_body)):
                await refuse(path, {**valid, **bindings}, 403, 'identity_operator_binding_rejected',
                             extra_headers=extra_headers)
                # Query presence is rejected even when valid body has no binding.
                query = {key: '' if value is None else json.dumps(value) for key, value in bindings.items()}
                await refuse(path, valid, 403, 'identity_operator_binding_rejected', query=query,
                             extra_headers=extra_headers)

        if case == 'operator':
            # Actual bearer dependency runs; no override hides auth admission.
            for auth in ({}, {'Authorization': 'Bearer wrong-disposable-token'}):
                stable = http_naming_snapshot(orgs)
                denied = await client.put(AGENT, json=body, headers=auth)
                assert denied.status_code == 401
                assert http_naming_snapshot(orgs) == stable
            before = http_naming_snapshot(orgs)
            response = await client.put(AGENT, json=body, headers=headers)
            assert response.status_code == 200, response.text
            identity = response.json()
            assert identity['canonical_id'] == 'maker' and identity['kind'] == 'agent'
            assert identity['lifecycle'] == 'active' and identity['addressable_name'] == 'Sam'
            assert identity['name_revision'] == revision + 1 and identity['naming_status'] == 'ready'
            assert len(identity['canonical_definition_revision']) == 64
            after = http_naming_snapshot(orgs)
            # A pure rename leaves EVERY non-naming/audit table, all canonical
            # files/workspaces/teams and original session ownership unchanged.
            old = before['alpha']['persistent']; new = after['alpha']['persistent']
            assert old['files'] == new['files'] and old['teams'] == new['teams']
            assert old['schema'] == new['schema']
            for table in old['rows']:
                if not table.startswith('identity_name_') and table != 'audit_log':
                    assert old['rows'][table] == new['rows'][table]
            assert after['beta'] == before['beta']
            assert after['alpha']['manager_session'] == before['alpha']['manager_session'] == 'sess-owner'
            audits = org.db.execute("SELECT payload FROM audit_log WHERE action='identity_name_changed'").fetchall()
            assert len(audits) == 1
            assert json.loads(audits[0][0]) == {
                'org_slug': 'alpha', 'kind': 'agent', 'canonical_id': 'maker',
                'old_label': 'maker', 'new_label': 'Sam', 'old_revision': revision,
                'new_revision': revision + 1, 'source': 'founder', 'actor': 'founder',
            }
            # Identical spelling is an exact no-op, including rowids/audit.
            stable = http_naming_snapshot(orgs)
            noop = await client.put(AGENT, json={**body, 'expected_name_revision': revision + 1}, headers=headers)
            assert noop.status_code == 200 and noop.json() == identity
            assert http_naming_snapshot(orgs) == stable
            # Case-only and former reclaim advance exactly once each.
            for label, expected in (('sAM', revision + 1), ('Other', revision + 2), ('Sam', revision + 3)):
                changed = await client.put(AGENT, json={'addressable_name': label, 'expected_name_revision': expected}, headers=headers)
                assert changed.status_code == 200, changed.text
                assert changed.json()['addressable_name'] == label
                assert changed.json()['name_revision'] == expected + 1
            human = await client.put(FOUNDER, json=founder_body, headers=headers)
            assert human.status_code == 200 and human.json()['kind'] == 'founder'
            assert human.json()['canonical_id'] == 'founder' and human.json()['lifecycle'] == 'founder'
            assert human.json()['canonical_definition_revision'] is None
            assert human.json()['name_revision'] == founder_revision + 1
            # Strict decoder: missing, null, coercion, extra, malformed label.
            for invalid in ({}, {'addressable_name': 'Sam'},
                            {'expected_name_revision': revision},
                            {**body, 'addressable_name': None}, {**body, 'addressable_name': 1},
                            {**body, 'addressable_name': 'Sam\n'}, {**body, 'addressable_name': '_maker'},
                            {**body, 'addressable_name': 'é'}, {**body, 'addressable_name': 'x' * 65},
                            {**body, 'expected_name_revision': None}, {**body, 'expected_name_revision': True},
                            {**body, 'expected_name_revision': '1'}, {**body, 'expected_name_revision': 0},
                            {**body, 'expected_name_revision': 1.0}, {**body, 'request_id': 'superseded'},
                            {**body, 'namespace_generation': 1}, [], 'malformed'):
                await refuse(AGENT, invalid, 422, 'invalid_identity_request')
            # Reads resolve names/IDs without effects; former is a typed refusal.
            stable = http_naming_snapshot(orgs)
            roster = await client.get(PREFIX + '/identities', headers=headers)
            assert roster.status_code == 200
            ids = {row['canonical_id']: row for row in roster.json()['identities']}
            assert set(ids) == {'founder', 'manager', 'maker'}
            assert ids['maker']['addressable_name'] == 'Sam'
            assert ids['maker']['canonical_definition_revision'] == identity['canonical_definition_revision']
            agents = await client.get(PREFIX + '/agents', headers=headers)
            assert agents.status_code == 200
            agent = next(row for row in agents.json()['agents'] if row['name'] == 'maker')
            assert agent['name'] == 'maker' and agent['revision'] == identity['canonical_definition_revision']
            assert agent['addressable_name'] == 'Sam' and agent['name_revision'] == revision + 4
            for context in ('lookup', 'task_owner', 'thread_recipient'):
                resolved = await client.post(PREFIX + '/identities/resolve',
                                             json={'addresses': ['SAM', 'MAKER', 'Other', 'Chief', 'unknown', '_unowned'], 'context': context}, headers=headers)
                assert resolved.status_code == 200
                rows = resolved.json()['resolutions']
                assert [row['status'] for row in rows[:3]] == ['resolved', 'resolved', 'former_name']
                assert all(row['identity']['canonical_id'] == 'maker' for row in rows[:3])
                assert rows[2]['identity']['addressable_name'] == 'Sam' and rows[2]['eligible'] is False
                assert rows[3]['identity']['kind'] == 'founder'
                assert rows[3]['eligible'] is (context != 'task_owner')
                assert rows[4]['status'] == 'unknown_identity' and rows[4]['identity'] is None
                assert rows[5]['status'] == 'invalid_identity_address' and rows[5]['identity'] is None
            assert http_naming_snapshot(orgs) == stable
            # Canonical target IDs only: a label or absent owner cannot rebind.
            for target in ('sam', 'absent', 'founder'):
                await refuse(PREFIX + '/agents/' + target + '/addressable-name',
                             {'addressable_name': 'Ignored', 'expected_name_revision': revision + 4},
                             404, 'identity_owner_absent')
            # Actual optional thread context preserves membership/open state.
            from runtime.models import ThreadRecord, ThreadStatus
            org.db.insert_thread(ThreadRecord(id='THR-CONTEXT', subject='Naming read context'))
            org.db.add_thread_participant('THR-CONTEXT', 'maker', added_by='founder')
            stable = http_naming_snapshot(orgs)
            context = await client.post(PREFIX + '/identities/resolve',
                                        json={'addresses': ['Sam', 'Manager', 'Chief'],
                                              'context': 'thread_recipient', 'thread_id': 'THR-CONTEXT'}, headers=headers)
            assert context.status_code == 200
            assert [row['eligible'] for row in context.json()['resolutions']] == [True, False, True]
            assert http_naming_snapshot(orgs) == stable
            missing = await client.post(PREFIX + '/identities/resolve',
                                        json={'addresses': ['Sam'], 'context': 'thread_recipient',
                                              'thread_id': 'THR-NOT-IN-ALPHA'}, headers=headers)
            assert missing.status_code == 404 and missing.json()['detail']['code'] == 'unknown_thread'
            assert http_naming_snapshot(orgs) == stable
            org.db.set_thread_status('THR-CONTEXT', status=ThreadStatus.ARCHIVED)
            stable = http_naming_snapshot(orgs)
            archived = await client.post(PREFIX + '/identities/resolve',
                                         json={'addresses': ['Sam', 'Chief'], 'context': 'thread_recipient',
                                               'thread_id': 'THR-CONTEXT'}, headers=headers)
            assert archived.status_code == 200
            assert all(row['status'] == 'ineligible_identity' and row['eligible'] is False
                       for row in archived.json()['resolutions'])
            assert http_naming_snapshot(orgs) == stable
            # Explicit naming-unavailable metadata preserves baseline ID reads.
            org.naming_readiness = 'unavailable'
            unavailable = await client.get(PREFIX + '/agents', headers=headers)
            assert unavailable.status_code == 200
            assert all(row['addressable_name'] is None and row['name_revision'] is None
                       and row['naming_status'] == 'unavailable' for row in unavailable.json()['agents'])
            direct = await client.post(PREFIX + '/identities/resolve', json={'addresses': ['maker', 'Sam']}, headers=headers)
            assert direct.status_code == 200
            assert direct.json()['resolutions'][0]['identity']['canonical_id'] == 'maker'
            assert direct.json()['resolutions'][0]['identity']['addressable_name'] is None
            assert direct.json()['resolutions'][1]['status'] == 'naming_unavailable'
            assert http_naming_snapshot(orgs) == stable
            # Concrete naming corruption refuses mutation and keeps ID reads.
            # This is fixture setup in a disposable org, never a product seam.
            org.db.execute('CREATE INDEX identity_name_unapproved_idx ON identity_name_owners(revision)')
            await refuse(AGENT, {'addressable_name': 'Blocked', 'expected_name_revision': revision + 4},
                         503, 'naming_unavailable')
            broken = http_naming_snapshot(orgs)
            still_ids = await client.get(PREFIX + '/identities', headers=headers)
            assert still_ids.status_code == 200
            assert {row['canonical_id'] for row in still_ids.json()['identities']} == {'founder', 'manager', 'maker'}
            assert all(row['addressable_name'] is None and row['name_revision'] is None
                       and row['naming_status'] == 'unavailable' for row in still_ids.json()['identities'])
            assert http_naming_snapshot(orgs) == broken
            # Real pending/terminated definitions remain renameable while all
            # agent action contexts remain ineligible. Still ONE manifest case.
            for life in ('pending', 'terminated'):
                life_root = root / life
                life_root.mkdir()
                with mounted_naming_app(life_root, lifecycle=life) as (life_app, life_orgs, life_headers):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=life_app),
                                                 base_url='http://naming.test') as life_client:
                        before_life = http_naming_snapshot(life_orgs)
                        life_revision = life_orgs['alpha'].db.execute("SELECT revision FROM identity_name_owners WHERE canonical_id='maker' AND kind='agent'").fetchone()[0]
                        renamed = await life_client.put(AGENT, json={'addressable_name': 'Sam', 'expected_name_revision': life_revision}, headers=life_headers)
                        assert renamed.status_code == 200, renamed.text
                        assert renamed.json()['lifecycle'] == life
                        assert renamed.json()['name_revision'] == life_revision + 1
                        assert before_life['alpha']['persistent']['files'] == http_naming_snapshot(life_orgs)['alpha']['persistent']['files']
                        life_roster = await life_client.get(PREFIX + '/identities', headers=life_headers)
                        assert life_roster.status_code == 200
                        assert next(row for row in life_roster.json()['identities'] if row['canonical_id'] == 'maker') == renamed.json()
                        listed = await life_client.get(PREFIX + '/agents/enrollments',
                                                       params={'status': 'pending' if life == 'pending' else 'terminated'}, headers=life_headers)
                        assert listed.status_code == 200
                        summary = next(row for row in listed.json()['enrollments'] if row['name'] == 'maker')
                        assert summary['name'] == 'maker' and summary['addressable_name'] == 'Sam'
                        assert summary['name_revision'] == life_revision + 1
                        stable_life = http_naming_snapshot(life_orgs)
                        for context in ('lookup', 'task_owner', 'thread_recipient'):
                            resolved = await life_client.post(PREFIX + '/identities/resolve',
                                                            json={'addresses': ['Sam'], 'context': context}, headers=life_headers)
                            assert resolved.status_code == 200
                            row = resolved.json()['resolutions'][0]
                            assert row['identity']['lifecycle'] == life
                            assert row['eligible'] is (context == 'lookup')
                            assert row['status'] == ('resolved' if context == 'lookup' else 'ineligible_identity')
                        assert http_naming_snapshot(life_orgs) == stable_life
            return ['operator agent/founder CAS', 'exact no-op', 'case-only/former reclaim',
                    'canonical/session/foreign preservation', 'strict request refusals',
                    'typed read/resolve/context/former/no effects', 'unavailable baseline ID reads',
                    'corrupt naming refuses mutation', 'pending/terminated operator rename and lookup-only eligibility']
        if case.startswith('task-') or case.startswith('thread-'):
            keys = TASK_KEYS if case.startswith('task-') else THREAD_KEYS
            shape = case.split('-', 1)[1]
            if shape == 'full':
                binding = ({'task_id': 'TASK-OWNER', 'session_id': 'sess-owner', 'agent': 'manager'}
                           if case.startswith('task-') else
                           {'thread_id': 'THR-BOUND', 'invocation_token': 'opaque', 'speaker': 'maker', 'composer': 'maker'})
                await deny_bindings(binding)
            else:
                for key in keys:
                    value = 'partial' if shape == 'partial' else None if shape == 'null' else {'malformed': ['binding']}
                    await deny_bindings({key: value})
            return [shape + ' binding presence rejected in body and query on BOTH rename endpoints',
                    'exact both-org no effects']
        if case == 'forged-label-header':
            await deny_bindings({'agent': 'maker', 'session_id': None},
                                extra_headers={'X-Founder': 'founder', 'X-Addressable-Name': 'Founder'})
            for key in ('actor', 'actor_id', 'principal', 'principal_id', 'principal_kind'):
                await deny_bindings({key: 'founder'})
            return ['descriptive founder/label header cannot override bound-agent denial',
                    'claimed actor/principal presence rejected', 'exact both-org no effects']
        if case == 'foreign-org':
            await refuse(PREFIX + '/agents/foreign/addressable-name', body, 404, 'identity_owner_absent')
            await refuse('/api/v1/orgs/missing/agents/maker/addressable-name', body, 404, 'unknown_org')
            await deny_bindings({'org_slug': 'beta'})
            stable = http_naming_snapshot(orgs)
            roster = await client.get(PREFIX + '/identities', headers=headers)
            assert roster.status_code == 200
            assert 'foreign' not in {row['canonical_id'] for row in roster.json()['identities']}
            resolved = await client.post(PREFIX + '/identities/resolve', json={'addresses': ['foreign']}, headers=headers)
            assert resolved.status_code == 200
            assert resolved.json()['resolutions'][0]['status'] == 'unknown_identity'
            assert resolved.json()['resolutions'][0]['identity'] is None
            assert http_naming_snapshot(orgs) == stable
            return ['path org dependency/target isolation', 'no foreign label or canonical metadata',
                    'exact both-org no effects']
        assert case == 'stale-revision'
        first = await client.put(AGENT, json=body, headers=headers)
        assert first.status_code == 200 and first.json()['name_revision'] == revision + 1
        await refuse(AGENT, body, 409, 'stale_identity_revision')
        await refuse(AGENT, {**body, 'addressable_name': 'Other'}, 409, 'stale_identity_revision')
        readback = await client.get(PREFIX + '/identities', headers=headers)
        assert readback.status_code == 200
        assert next(row for row in readback.json()['identities'] if row['canonical_id'] == 'maker') == first.json()
        first_human = await client.put(FOUNDER, json=founder_body, headers=headers)
        assert first_human.status_code == 200 and first_human.json()['name_revision'] == founder_revision + 1
        await refuse(FOUNDER, founder_body, 409, 'stale_identity_revision')
        return ['stale identical/changed requests refuse with exact no effects',
                'readback after presumed lost response', 'founder stale CAS']


# Current twelve-scenario plan: routing portions5/6/7/9/12, not a new matrix.
async def _routing_names(client, org, headers):
    (org.root / 'workspaces/manager').mkdir(exist_ok=True)
    for path, label in ((AGENT, 'OldSam'), (AGENT, 'Sam'), (FOUNDER, 'Chief')):
        roster = (await client.get(PREFIX + '/identities', headers=headers)).json()['identities']
        owner = next(row for row in roster if row['canonical_id'] == ('founder' if path == FOUNDER else 'maker'))
        response = await client.put(path, json={'addressable_name': label,
                                               'expected_name_revision': owner['name_revision']}, headers=headers)
        assert response.status_code == 200, response.text


def _routing_effects(orgs):
    # Include actual private attachment directories/temp ownership and queues,
    # beyond the reused exact schema/rowid/canonical-file/session snapshots.
    result = http_naming_snapshot(orgs)
    for slug, org in orgs.items():
        result[slug]['thread_files'] = tuple(
            (str(p.relative_to(org.root)), p.lstat().st_mode,
             p.read_bytes() if p.is_file() else None)
            for p in sorted((org.root / 'threads').rglob('*')))
        result[slug]['queue'] = tuple(job.invocation_token for job in org.thread_queue._q._queue)
        result[slug]['artifact_root_exists'] = (org.root / 'artifacts').exists()
        result[slug]['artifact_files'] = tuple(
            (str(p.relative_to(org.root)), p.lstat().st_mode,
             p.read_bytes() if p.is_file() else None)
            for p in sorted((org.root / 'artifacts').rglob('*')))
    return result


async def _routing_compose(client, headers, body='@Sam', recipients=None):
    response = await client.post(PREFIX + '/threads', headers=headers,
                                 json={'subject': 'routing scenario', 'recipients': recipients or ['Sam', 'manager'],
                                       'body_markdown': body})
    assert response.status_code == 200, response.text
    return response.json()['thread_id']



from contextlib import asynccontextmanager


@asynccontextmanager
async def _naming_cli_boundary(app):
    """Owned loopback + actual CLI process, no daemon lifespan or providers.

    Reuses core A10's uvicorn/socket technique and mounted_naming_app's real
    isolated bearer/org/DB. This is future disposable execution, NOT RUN.
    Observe requests without substituting client, resolution or action logic.
    """
    import os
    import socket
    import subprocess
    import sys
    from pathlib import Path
    import uvicorn
    from runtime.daemon import paths as daemon_paths
    observed = []
    async def recorded(scope, receive, send):
        if scope.get('type') != 'http':
            return await app(scope, receive, send)
        row = {'method': scope['method'], 'path': scope['path'],
               'query': scope.get('query_string', b'').decode(), 'body': bytearray()}
        observed.append(row)
        async def tapped():
            event = await receive()
            if event.get('type') == 'http.request':
                row['body'].extend(event.get('body', b''))
                assert len(row['body']) <= 1024 * 1024
            return event
        await app(scope, tapped, send)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0)); sock.listen(32)
    port = sock.getsockname()[1]
    daemon_paths.port_file().write_text(str(port))
    server = uvicorn.Server(uvicorn.Config(recorded, host='127.0.0.1', port=port,
                                          lifespan='off', log_level='error'))
    serve = asyncio.create_task(server.serve(sockets=[sock]))
    deadline = asyncio.get_running_loop().time() + 90
    async def cli(*arguments, exit=0):
        remaining = min(10, deadline - asyncio.get_running_loop().time() - 5)
        assert remaining > 0, 'owned CLI scenario deadline exhausted'
        result = await asyncio.to_thread(subprocess.run,
            [sys.executable, '-m', 'cli.main', *map(str, arguments)],
            cwd=Path(__file__).resolve().parents[2], env=dict(os.environ),
            capture_output=True, text=True, timeout=remaining)
        assert result.returncode == exit, (arguments, result.stdout, result.stderr)
        assert len(result.stdout) + len(result.stderr) <= 256 * 1024
        return result
    try:
        startup = asyncio.get_running_loop().time() + 5
        while not server.started:
            assert not serve.done() and asyncio.get_running_loop().time() < startup
            await asyncio.sleep(0.01)
        yield cli, observed, f'http://127.0.0.1:{port}'
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(asyncio.shield(serve), 3)
        except asyncio.TimeoutError:
            server.force_exit = True
            await asyncio.wait_for(serve, 2)
        finally:
            sock.close()
        assert serve.done()


async def _scenario5_cli_former_no_upload(app, orgs, thread, token, tmp_path):
    org = orgs['alpha']
    attachment = tmp_path / 'never-uploaded.txt'
    attachment.write_text('independent local bytes')
    payload = tmp_path / 'former-message.json'
    payload.write_text(json.dumps({'body_markdown': '> quoted @OldSam and user@OldSam',
                                  'speaker': 'maker', 'invocation_token': token,
                                  'thread_id': thread, 'in_response_to_seq': 1}))
    compose = tmp_path / 'former-compose.json'
    compose.write_text(json.dumps({'subject': 'never compose', 'recipients': ['Sam'],
                                  'composer': 'manager', 'body_markdown': '@OldSam'}))
    note = tmp_path / 'former-note.txt'; note.write_text('@OldSam')
    async with _naming_cli_boundary(app) as (cli, requests, _):
        commands = [
            ('threads', 'send', '--org', 'alpha', '--thread-id', thread, '--from-file', payload),
            ('threads', 'reply', '--org', 'alpha', '--from-file', payload),
            ('threads', 'compose', '--org', 'alpha', '--subject', 'denied', '--recipients', 'Sam', '--body', '@OldSam'),
            ('threads', 'compose', '--org', 'alpha', '--task-id', 'TASK-OWNER', '--session-id', 'sess-owner', '--from-file', compose),
        ]
        for command in commands:
            for extra in ((), ('--shared',)):
                before = _routing_effects(orgs); start = len(requests)
                denied = await cli(*command, '--attach', attachment, *extra, exit=1)
                assert 'former name' in denied.stderr and 'Sam' in denied.stderr
                assert _routing_effects(orgs) == before
                assert all(row['method'] == 'GET' or row['path'].endswith('/identities/resolve')
                           for row in requests[start:]), 'upload/action escaped former preflight'
        for command in [
            ('threads', 'compose', '--org', 'alpha', '--subject', 'denied', '--recipients', 'OldSam,Sam', '--body', 'plain', '--attach', attachment),
            ('threads', 'forward', '--org', 'alpha', '--source', thread, '--recipients', 'Sam', '--note-file', note),
        ]:
            before = _routing_effects(orgs); start = len(requests)
            denied = await cli(*command, exit=1)
            assert 'former name' in denied.stderr and 'Sam' in denied.stderr
            assert _routing_effects(orgs) == before
            assert all(row['method'] == 'GET' or row['path'].endswith('/identities/resolve')
                       for row in requests[start:])


def test_scenario8_cli_targets_and_fresh_prompt_context(tmp_path, tmp_home):
    """Real CLI -> loopback HTTP action -> durable IDs, then shipping builders.

    Prompt rendering uses the real production-bound org reader. It does NOT
    claim provider resume/eviction or dream/wake/schedule launch coverage.
    """
    from runtime.daemon.thread_runner import build_thread_prompt, build_thread_delta_prompt
    from runtime.identities.registry import read_name_metadata
    from runtime.orchestrator.org_config import load_org_config
    from runtime.orchestrator.run_step import _build_agent_prompt, _validate_delegate
    from runtime.models import NextStep
    async def exercise(app, orgs, headers):
        org = orgs['alpha']; config = load_org_config(org.orchestrator._paths)
        async with _naming_cli_boundary(app) as (cli, requests, url):
            async with httpx.AsyncClient(base_url=url, headers=headers) as http:
                await _routing_names(http, org, headers)
                current = await cli('identities', 'list', '--org', 'alpha')
                assert 'Sam · maker' in current.stdout and 'Chief · founder' in current.stdout
                await cli('run', '--org', 'alpha', '--owner', 'sAm', '--brief', 'CLI canonical owner')
                request = next(row for row in requests if row['path'] == PREFIX + '/tasks' and row['method'] == 'POST')
                assert json.loads(request['body'])['owner'] == 'maker'
                task = org.db.list_tasks(assigned_agent='maker')[0]
                assert task.assigned_agent == 'maker'
                listed = await cli('tasks', '--org', 'alpha', '--agent', 'Sam')
                assert 'Sam · maker' in listed.stdout
                raw = await cli('tasks', '--org', 'alpha', '--agent', 'Sam', '--json')
                assert json.loads(raw.stdout)[0]['assigned_agent'] == 'maker'
                assert any(row['path'] == PREFIX + '/tasks' and 'assigned_agent=maker' in row['query'] for row in requests)
                await cli('threads', 'compose', '--org', 'alpha', '--subject', 'CLI names',
                                   '--recipients', 'Sam,Chief', '--body', '@ordinary_unknown retained')
                request = next(row for row in requests if row['path'] == PREFIX + '/threads' and row['method'] == 'POST')
                assert json.loads(request['body'])['recipients'] == ['maker', '@founder']
                thread = org.db.list_threads()[0].id
                assert {p.agent_name for p in org.db.list_thread_participants(thread)} == {'maker'}
                assert not org.db.is_thread_participant(thread, 'founder')
                assert org.db.get_thread_message_by_seq(thread, 1).body_markdown == '@ordinary_unknown retained'
                assert org.db.get_thread_message_by_seq(thread, 1).speaker == 'founder'
                await cli('threads', 'invite', '--org', 'alpha', '--thread-id', thread, '--agent', 'manager')
                assert org.db.is_thread_participant(thread, 'manager')
                await cli('threads', 'forward', '--org', 'alpha', '--source', thread, '--recipients', 'Sam,Chief')
                renamed = await cli('identities', 'rename', '--org', 'alpha', 'Sam', '--name', 'NewSam', '--expected-name-revision', '3', '--json')
                assert json.loads(renamed.stdout)['canonical_id'] == 'maker'
                before = _routing_effects(orgs)
                await cli('identities', 'rename', '--org', 'alpha', 'maker', '--name', 'NoChange', '--expected-name-revision', '3', exit=1)
                assert _routing_effects(orgs) == before
                await cli('identities', 'rename', '--org', 'alpha', 'Chief', '--name', 'NewChief', '--expected-name-revision', '2')
                shown = await cli('threads', 'show', '--org', 'alpha', thread)
                assert 'NewSam · maker' in shown.stdout and 'NewChief · founder' in shown.stdout
                before = _routing_effects(orgs)
                await cli('run', '--org', 'alpha', '--owner', 'NewChief', '--brief', 'never human owner', exit=1)
                await cli('run', '--org', 'alpha', '--owner', 'UnknownTarget', '--brief', 'never unknown owner', exit=1)
                assert _routing_effects(orgs) == before
                invalid = tmp_path / 'invalid-rename.json'
                invalid.write_text(json.dumps({'addressable_name': 'NoChange', 'expected_name_revision': 4, 'composer': None}))
                start = len(requests)
                await cli('identities', 'rename', '--org', 'alpha', 'maker', '--from-file', invalid, exit=2)
                assert len(requests) == start and _routing_effects(orgs) == before
                record = org.db.get_thread(thread); participants = org.db.list_thread_participants(thread)
                messages = org.db.list_thread_messages(thread)
                kwargs = dict(thread=record, invocation_token='existing-id-token', invoked_agent='maker',
                              purpose='reply', triggering_seq=1, org_config=config)
                full = build_thread_prompt(**kwargs, participants=participants, messages=messages,
                                           name_metadata=read_name_metadata(org))
                delta = build_thread_delta_prompt(**kwargs, new_messages=[], triggering_message=messages[0],
                    name_metadata=read_name_metadata(org), participant_ids=[p.agent_name for p in participants])
                fallback = build_thread_prompt(**kwargs, participants=participants, messages=messages,
                                               name_metadata=read_name_metadata(org))
                for prompt in (full, delta, fallback):
                    assert 'NewSam · maker' in prompt and 'NewChief · founder' in prompt
                    assert 'existing-id-token' in prompt and 'Automation is ID-only' in prompt
                # Actual role and wrapper builders, with the OrgState-bound reader.
                manager_task = task.model_copy(update={'assigned_agent': 'manager'})
                role = _build_agent_prompt(org.orchestrator, manager_task, 'manager')
                assert '| NewSam · maker |' in role
                assert '"agent": "<agent_name>"' in role
                outer = org.orchestrator._build_agent_prompt('codex', 'maker', task.id, 'sess-fixed', task.brief, '')
                assert 'You are maker.' in outer and 'session_id: sess-fixed' in outer
                assert 'NewSam · maker' in outer and 'NewChief · founder' in outer
                from datetime import datetime, timezone
                from runtime.models import DreamRecord
                from runtime.daemon.dream_runner import build_dream_prompt
                from runtime.daemon.wake_runner import build_wake_prompt
                from runtime.daemon.schedule_runner import build_schedule_prompt
                now = datetime.now(timezone.utc)
                auxiliary = [
                    build_dream_prompt(org_slug='alpha', dream=DreamRecord(id='DREAM-OWNED', agent_name='maker',
                        local_date='2030-01-01', scheduled_for=now, window_end=now), workspace=org.root / 'workspaces/maker',
                        recent_audit=[], task_history='', org_config=config, name_metadata=read_name_metadata(org)),
                    build_wake_prompt(org_slug='alpha', work_hour_id='WORKHOUR-OWNED', agent_name='maker', role='worker',
                        team='engineering', local_date='2030-01-01', slot='morning', mode='daily', preamble='',
                        routines=['one owned routine'], org_config=config, name_metadata=read_name_metadata(org)),
                    build_schedule_prompt(org_slug='alpha', schedule_id='SCHEDULE-OWNED', agent_name='maker', role='worker',
                        team='engineering', normalized_brief='owned task', kind='one_shot', fire_at_iso=now.isoformat(),
                        recurrence=None, timezone='UTC', org_config=config, name_metadata=read_name_metadata(org)),
                ]
                for prompt in auxiliary:
                    assert 'NewSam · maker' in prompt and 'NewChief · founder' in prompt
                    assert 'Automation is ID-only' in prompt
                assert _validate_delegate(org.orchestrator, NextStep(action='delegate', agent='NewSam', prompt='never alias')) is not None
                assert _validate_delegate(org.orchestrator, NextStep(action='delegate', agent='maker', prompt='canonical')) is None
                token = next(inv.invocation_token for inv in org.db.list_thread_invocations(thread) if inv.agent_name == 'maker')
                assert org.db.claim_conversational_reply(token) is not None
                before = _routing_effects(orgs)
                actor_payload = tmp_path / 'label-actor.json'
                actor_payload.write_text(json.dumps({'speaker': 'NewSam', 'thread_id': thread,
                    'invocation_token': token, 'in_response_to_seq': 1, 'body_markdown': 'no alias actor'}))
                await cli('threads', 'reply', '--org', 'alpha', '--from-file', actor_payload, exit=1)
                assert _routing_effects(orgs) == before
                await cli('tasks', '--org', 'alpha', '--agent', 'retired_historical_id', '--json')
                org.naming_readiness = 'unavailable'
                unavailable = await cli('tasks', '--org', 'alpha', '--agent', 'maker')
                assert task.id in unavailable.stdout
                await cli('tasks', '--org', 'alpha', '--agent', 'retired_historical_id', '--json')
                outer = org.orchestrator._build_agent_prompt('codex', 'maker', task.id, 'sess-fixed', task.brief, '')
                assert 'Naming metadata unavailable' in outer and 'Self: maker.' in outer
                assert 'NewSam · maker' not in outer
    with mounted_naming_app(tmp_path) as args:
        asyncio.run(exercise(*args))

def test_scenario5_former_before_callback_and_multipart_effects(tmp_path, tmp_home):
    from datetime import datetime, timedelta, timezone
    from fastapi import HTTPException
    from runtime.daemon.routes.threads import _create_agent_thread_locked
    from runtime.identities.schema import NamingError
    from runtime.models import TaskRecord, ThreadMessageKind, ThreadInvocationPurpose

    async def exercise(app, orgs, headers):
        org = orgs['alpha']
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://naming.test') as client:
            await _routing_names(client, org, headers)
            thread = await _routing_compose(client, headers)
            token = next(inv.invocation_token for inv in org.db.list_thread_invocations(thread) if inv.agent_name == 'maker')
            assert org.db.claim_conversational_reply(token) is not None
            old = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
            org.db.execute('UPDATE thread_reply_exchange SET opened_at=?,last_activity_at=? WHERE thread_id=?',
                           (old, old, thread))
            org.db.commit()
            org.db.insert_task(TaskRecord(id='TASK-OWNER', team='engineering', assigned_agent='manager', brief='disposable binding'))
            # Valid attachment exists BEFORE the refusal snapshot. Reply may
            # read it, but must not settle/link/finalize another attachment.
            uploaded = await client.post(PREFIX + f'/threads/{thread}/attachments?agent=founder', headers=headers,
                                         files={'file': ('owned.txt', b'owned prior bytes', 'text/plain')})
            assert uploaded.status_code == 200, uploaded.text
            attachment = {'attachment_id': uploaded.json()['attachment_id']}
            mixed = '@Chief @Sam @unknown @oLdSaM'
            async def refused(path, **kwargs):
                before = _routing_effects(orgs)
                response = await client.post(PREFIX + path, headers=headers, **kwargs)
                assert response.status_code == 409, response.text
                detail = response.json()['detail']
                assert detail['code'] == 'former_name' and detail['current_name'] == 'Sam'
                assert _routing_effects(orgs) == before
            await refused(f'/threads/{thread}/reply', json={
                'thread_id': thread, 'speaker': 'maker', 'invocation_token': token,
                'in_response_to_seq': 1, 'body_markdown': mixed, 'attachments': [attachment]})
            assert org.db.get_pending_invocation(token) is not None
            await refused(f'/threads/{thread}/send', json={'body_markdown': mixed, 'attachments': [attachment]})
            await refused(f'/threads/{thread}/post-as-agent', json={
                'composer': 'manager', 'task_id': 'TASK-OWNER', 'session_id': 'sess-owner', 'body_markdown': mixed})
            founder = {'subject': 'no thread', 'recipients': ['Sam'], 'body_markdown': mixed}
            agent = {**founder, 'composer': 'manager', 'task_id': 'TASK-OWNER', 'session_id': 'sess-owner'}
            for path, payload in (('/threads', founder), ('/threads/compose-as-agent', agent)):
                await refused(path, json=payload)
                await refused(path, data={'body': json.dumps(payload)},
                              files={'files': ('never-finalized.txt', b'never stored', 'text/plain')})
            # Former recipient itself, even with a current/unknown recipient,
            # refuses the whole input before allocation or unknown fallback.
            await refused('/threads', json={**founder, 'body_markdown': 'plain', 'recipients': ['Sam', 'unknown', 'OldSam']})
            before = _routing_effects(orgs)
            with pytest.raises(HTTPException) as err:
                _create_agent_thread_locked(org, composer='manager', subject='dream-style compose',
                                            body_text=mixed, recipients=['@founder'], turn_cap=500)
            assert err.value.detail['current_name'] == 'Sam'
            assert _routing_effects(orgs) == before
            # Both independent store entry points refuse without route help.
            with pytest.raises(NamingError) as err:
                org.db.record_conversational_arrival(thread_id=thread, speaker='founder', kind=ThreadMessageKind.MESSAGE,
                                                     body_markdown=mixed, recipients=['maker', 'manager'])
            assert err.value.code == 'former_name'
            with pytest.raises(NamingError):
                org.db.append_thread_message(thread_id=thread, speaker='founder', kind=ThreadMessageKind.MESSAGE,
                                             body_markdown=mixed)
            with pytest.raises(NamingError):
                org.db.reply_conversational(thread_id=thread, speaker='maker', body_markdown=mixed,
                                            attachments=[], token=token, token_purpose=ThreadInvocationPurpose.REPLY)
            assert _routing_effects(orgs) == before
            await _scenario5_cli_former_no_upload(app, orgs, thread, token, tmp_path)
    with mounted_naming_app(tmp_path) as args:
        asyncio.run(exercise(*args))


def test_scenario6_current_names_and_unknown_fallback(tmp_path, tmp_home):
    from runtime.models import ThreadMessageKind
    async def exercise(app, orgs, headers):
        org = orgs['alpha']
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://naming.test') as client:
            await _routing_names(client, org, headers)
            thread = await _routing_compose(client, headers, "quoted '@sAM', email user@SAM", ['sAM', 'MANAGER'])
            assert org.db.list_thread_messages(thread)[0].mentions == ['maker']
            assert {p.agent_name for p in org.db.list_thread_participants(thread)} == {'maker', 'manager'}
            # Clear initial exchange/delivery using shipping discard APIs;
            # each next input starts outside an exchange and isolates routing.
            for body, expected in (('@unknown', {'maker', 'manager'}),
                                   ('@Chief @SAM', {'maker'}), ('@Chief @MANAGER', {'manager'})):
                org.db.discard_reply_delivery(thread, decline_reason='disposable scenario reset')
                response = await client.post(PREFIX + f'/threads/{thread}/send', headers=headers, json={'body_markdown': body})
                assert response.status_code == 200, response.text
                assert set(response.json()['pending_replies']) == expected
                assert all(inv.agent_name != 'founder' for inv in org.db.list_thread_invocations(thread))
            # Public append also persists canonical IDs, never alias spelling.
            seq = org.db.append_thread_message(thread_id=thread, speaker='founder', kind=ThreadMessageKind.MESSAGE,
                                               body_markdown='@SAM')
            assert org.db.get_thread_message_by_seq(thread, seq).mentions == ['maker']
            # Existing canonical-ID writes remain available with no naming
            # projection; non-ID addresses refuse with org-local diagnostics.
            org.naming_readiness = 'unavailable'
            response = await client.post(PREFIX + f'/threads/{thread}/send', headers=headers, json={'body_markdown': '@MAKER'})
            assert response.status_code == 200, response.text
            before = _routing_effects(orgs)
            response = await client.post(PREFIX + f'/threads/{thread}/send', headers=headers, json={'body_markdown': '@Sam'})
            assert response.status_code == 503 and response.json()['detail']['code'] == 'naming_unavailable'
            assert _routing_effects(orgs) == before
    with mounted_naming_app(tmp_path) as args:
        asyncio.run(exercise(*args))


def test_scenario7_founder_only_preserves_open_and_stale_catchup(tmp_path, tmp_home):
    from datetime import datetime, timedelta, timezone
    from runtime.daemon.event_bus import thread_inbox_topic
    async def exercise(app, orgs, headers):
        org = orgs['alpha']
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://naming.test') as client:
            await _routing_names(client, org, headers)
            # Outside exchange, founder-only creates no token/audit, but all
            # recipient required watermarks advance and inbox publishes.
            inbox = org.event_bus.subscribe(thread_inbox_topic('alpha'))
            pending_event = asyncio.create_task(anext(inbox))
            await asyncio.sleep(0)
            try:
                outside = await _routing_compose(client, headers, '@Chief')
                event = await asyncio.wait_for(pending_event, timeout=1)
                assert event['thread_id'] == outside and event['event_kind'] == 'message'
            finally:
                pending_event.cancel()
                await inbox.aclose()
            assert org.db.list_thread_invocations(outside) == []
            assert not [a for a in org.db.get_audit_logs(outside) if a['action'] == 'thread_reply_wake_created']
            assert all(org.db.get_reply_delivery_state(outside, name).required_through_seq == 1 for name in ('maker', 'manager'))
            thread = await _routing_compose(client, headers, '@Sam')
            def rows(table):
                return tuple(tuple(r) for r in org.db.execute(f'SELECT * FROM {table} WHERE thread_id=? ORDER BY rowid', (thread,)))
            def members():
                return (org.db.get_thread_message_by_seq(thread, 1).mentions,
                        tuple(tuple(r) for r in org.db.execute(
                            'SELECT exchange_id,agent_name FROM thread_exchange_deferrals WHERE thread_id=? ORDER BY rowid', (thread,))))
            frozen = members()
            before_tokens = rows('thread_invocations')
            response = await client.post(PREFIX + f'/threads/{thread}/send', headers=headers, json={'body_markdown': '@Chief'})
            assert response.status_code == 200 and response.json()['pending_replies'] == []
            assert rows('thread_invocations') == before_tokens
            assert members() == frozen
            assert all(org.db.get_reply_delivery_state(thread, name).required_through_seq == 2 for name in ('maker', 'manager'))
            # Existing maximum-wait expiry releases prior held range1..2.
            old = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
            org.db.execute('UPDATE thread_reply_exchange SET opened_at=?,last_activity_at=? WHERE thread_id=?', (old, old, thread))
            org.db.commit()
            queued_before = org.thread_queue.size
            response = await client.post(PREFIX + f'/threads/{thread}/send', headers=headers, json={'body_markdown': '@Chief'})
            assert response.status_code == 200, response.text
            assert set(response.json()['pending_replies']) == {'maker', 'manager'}
            assert org.thread_queue.size == queued_before + 1
            state = org.db.get_reply_delivery_state(thread, 'manager')
            assert state.required_through_seq == 3 and state.queued_invocation_token is not None
            assert org.thread_queue._q._queue[-1].invocation_token == state.queued_invocation_token
            new_wakes = [a['payload'] for a in org.db.get_audit_logs(thread) if a['action'] == 'thread_reply_wake_created']
            assert new_wakes and all(payload['through_seq'] < 3 for payload in new_wakes)
            assert members() == frozen
            assert not org.db.is_thread_participant(thread, 'founder')
    with mounted_naming_app(tmp_path) as args:
        asyncio.run(exercise(*args))


def test_scenarios9_12_queued_ids_survive_rename_and_reopen(tmp_path, tmp_home):
    from runtime.daemon.org_state import OrgState
    from runtime.daemon.thread_queue import ThreadJob
    from runtime.models import ThreadInvocationPurpose
    async def exercise(app, orgs, headers):
        org = orgs['alpha']
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://naming.test') as client:
            await _routing_names(client, org, headers)
            thread = await _routing_compose(client, headers, '@Sam')
            token = next(inv.invocation_token for inv in org.db.list_thread_invocations(thread) if inv.agent_name == 'maker')
            def rows(state, table):
                return tuple(tuple(r) for r in state.db.execute(f'SELECT rowid,* FROM {table} WHERE thread_id=? ORDER BY rowid', (thread,)))
            tables = ('thread_messages', 'thread_participants', 'thread_invocations', 'thread_reply_delivery_state',
                      'thread_reply_exchange', 'thread_exchange_deferrals')
            durable = {table: rows(org, table) for table in tables}
            bindings = http_naming_snapshot(orgs)
            outer_before = org.orchestrator._build_agent_prompt('codex', 'maker', 'TASK-OWNER', 'sess-owner', 'continuity', '')
            assert 'Sam · maker' in outer_before
            response = await client.put(AGENT, headers=headers, json={'addressable_name': 'NewSam', 'expected_name_revision': 3})
            assert response.status_code == 200, response.text
            assert {table: rows(org, table) for table in tables} == durable
            binding_after = http_naming_snapshot(orgs)
            for slug in orgs:
                assert binding_after[slug]['manager_session'] == bindings[slug]['manager_session']
                assert binding_after[slug]['persistent']['files'] == bindings[slug]['persistent']['files']
                assert binding_after[slug]['persistent']['teams'] == bindings[slug]['persistent']['teams']
            outer_after = org.orchestrator._build_agent_prompt('codex', 'maker', 'TASK-OWNER', 'sess-owner', 'continuity', '')
            assert 'NewSam · maker' in outer_after and 'task_id: TASK-OWNER' in outer_after
            assert 'session_id: sess-owner' in outer_after
            assert org.thread_queue._q._queue[0].invocation_token == token
            root, settings = org.root, org.settings
            org.close()
            reopened = OrgState.load(slug='alpha', root=root, settings=settings)
            try:
                assert {table: rows(reopened, table) for table in tables} == durable
                recovered = reopened.db.recover_reply_delivery_state()
                assert any(r.invocation_token == token and r.agent_name == 'maker' and r.kind == 'retained_queued' for r in recovered)
                for receipt in recovered:
                    await reopened.thread_queue.put(ThreadJob(org_slug='alpha', invocation_token=receipt.invocation_token))
                assert reopened.thread_queue._q._queue[0].invocation_token == token
                assert reopened.db.get_thread_message_by_seq(thread, 1).mentions == ['maker']
                claim = reopened.db.claim_conversational_reply(token)
                assert claim is not None
                seq, settlement, _arrivals = reopened.db.reply_conversational(
                    thread_id=thread, speaker='maker', body_markdown='@founder', attachments=[],
                    token=token, token_purpose=ThreadInvocationPurpose.REPLY)
                assert seq == 2 and settlement is not None
                inv = reopened.db.get_invocation_any_status(token)
                assert inv.status.value == 'consumed' and inv.reply_message_seq == 2
                assert reopened.db.get_reply_delivery_state(thread, 'maker').acknowledged_through_seq == 1
                # The recovered decision came from IDs/ranges, despite Sam
                # becoming a former name. No historical body was re-resolved.
                assert reopened.db.get_thread_message_by_seq(thread, 1).body_markdown == '@Sam'
            finally:
                reopened.close()
    with mounted_naming_app(tmp_path) as args:
        asyncio.run(exercise(*args))


def test_scenario10_bound_and_unauthorized_rename(tmp_path, tmp_home):
    """Finite operator boundary; operator case includes genuine wrong-token 401."""
    test_a11_operator_denial(tmp_path, tmp_home,
                            case_ids=('operator', 'task-full', 'thread-full'))

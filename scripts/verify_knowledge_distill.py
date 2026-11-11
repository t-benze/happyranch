"""Focused S2b HTTP/CLI exercise, outside retired test suites.

Run: uv run python scripts/verify_knowledge_distill.py [CASE]
Uses registered authenticated routes, real SQLite, task admission/queue,
completion route and terminal owner, and unmodified CLI over loopback HTTP.
Fixture substitutes org attachment, retained retirement layout, launch/session
facts and MODEL OUTPUT. No provider/model, daemon lifespan or live org runs.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import uvicorn

from runtime.config import Settings
from runtime.daemon.app import create_app
from runtime.daemon.event_bus import EventBus
from runtime.daemon.routes._org_dep import resolve_org
from runtime.daemon.sessions import SessionTracker
from runtime.daemon.queue import TaskQueue
from runtime.daemon.state import DaemonState
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.database import Database
from runtime.infrastructure.workflow_schema import install_or_recover
from runtime.infrastructure.workflow_task_ownership import install_task_ownership
from runtime.knowledge.curation import capture_digest
from runtime.knowledge.sources import RetiredSource
from runtime.models import TaskStatus
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.executors import ExecutorResult
from runtime.orchestrator.orchestrator import Orchestrator, completion_report_from_result_row
from runtime.orchestrator.run_step import _consume_completion_report
from runtime.orchestrator.teams import TeamsRegistry
from runtime.runtime import RuntimeDir


def require(value: object, message: str) -> None:
    if not value:
        raise AssertionError(message)


def run(selected: str | None = None) -> None:
    with tempfile.TemporaryDirectory(prefix='knowledge-distill-') as directory:
        scratch = Path(directory)
        root = scratch / 'runtime/orgs/synthetic'
        home = scratch / 'daemon'
        home.mkdir()
        # Synthetic credential only; never emit it.
        (home / 'daemon.token').write_text('synthetic-distill-bearer')
        (root / 'org/agents/_terminated').mkdir(parents=True)
        (root / 'workspaces/_terminated').mkdir(parents=True)
        (root / 'org/teams.yaml').write_text('teams:\n  synthetic:\n    manager: curator\n    workers: [worker]\n')
        for name, role in [('curator', 'manager'), ('worker', 'worker')]:
            (root / 'org/agents' / f'{name}.md').write_text(
                f'---\nname: {name}\nteam: synthetic\nrole: {role}\nexecutor: codex\n---\nCurate supplied documents.\n')
            (root / 'workspaces' / name).mkdir()
        teams = TeamsRegistry.load(root)
        db = Database(root / 'happyranch.db')
        org = SimpleNamespace(slug='synthetic', root=root, db=db, teams=teams,
                              db_lock=asyncio.Lock(), sessions=SessionTracker(),
                              event_bus=EventBus(lambda _: []))
        state = DaemonState.idle(Settings())
        state.runtime = RuntimeDir(scratch / 'runtime')
        state.orgs['synthetic'] = org

        def orchestrator() -> None:
            install_or_recover(org.db, expected_org_slug='synthetic')
            install_task_ownership(org.db, org_slug='synthetic')
            org.orchestrator = Orchestrator(org.db, Settings(), OrgPaths(root), 'synthetic', teams)
            org.orchestrator.attach_queue(state.queue)
            org.orchestrator.attach_sessions(org.sessions)

        orchestrator()
        app = create_app(state)
        # Only org attachment is substituted; auth and registered routes run.
        app.dependency_overrides[resolve_org] = lambda: org
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        sock.listen(128)
        port = sock.getsockname()[1]
        (home / 'daemon.port').write_text(str(port))
        server = uvicorn.Server(uvicorn.Config(app, log_level='critical', lifespan='off'))
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]))
        env = dict(os.environ, HAPPYRANCH_DAEMON_HOME=str(home))
        results = []
        originals: dict[str, str] = {}
        base = '/api/v1/orgs/synthetic/knowledge'

        def seed(source: str, *, oversized: bool = False, history: bool = True, empty: bool = False,
                 unavailable: bool = False) -> None:
            (root / 'org/agents/_terminated' / f'{source}.md').write_text(
                f'---\nname: {source}\nteam: synthetic\nrole: worker\nexecutor: codex\n---\nRetired synthetic source.\n')
            memory = root / 'workspaces/_terminated' / source / 'memory'
            memory.mkdir(parents=True)
            for n in range(20 if oversized else 0 if empty else 1):
                (memory / f'MEM-{n+1:03d}-lesson{n}.md').write_text(
                    f"---\nid: MEM-{n+1:03d}\nslug: lesson{n}\ntitle: Synthetic lesson\ntopic: engineering\n"
                    f"authored_by: {source}\nauthored_at: '2026-01-01T00:00:00+00:00'\n"
                    "updated_at: '2026-01-02T00:00:00+00:00'\n---\n\n"
                    + ('S' * 20000 if oversized else 'RAW_CORPUS_SENTINEL: a synthetic source observation.') + '\n')
            if unavailable:
                (memory / 'MEM-001-lesson0.md').write_text('RAW_CORPUS_SENTINEL: malformed synthetic memory\n')
            for path in [root / 'org/agents/_terminated' / f'{source}.md', *memory.rglob('*')]:
                if path.is_file():
                    originals[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
            if history:
                AuditLogger(org.db).log_agent_managed(scope_id='TASK-retirement-fixture', action='terminate',
                                                       name=source, source='task', actor='curator')

        def cli(*arguments: str, local: bool = False) -> subprocess.CompletedProcess:
            cli_env = dict(env)
            if local:
                cli_env['HAPPYRANCH_DAEMON_HOME'] = str(scratch / 'no-daemon')
            result = subprocess.run([sys.executable, '-m', 'cli.main', *arguments], env=cli_env,
                                    capture_output=True, text=True, timeout=15)
            return result

        def submit(source: str, key: str | None = None, processor: str | None = None) -> dict:
            body = {'source_id': source, 'submission_id': key or source, 'confirmed': True}
            if processor is not None:
                body['processor_id'] = processor
            response = client.post(base + '/distill', json=body)
            require(response.status_code == 200, f'submit {source}: HTTP {response.status_code}')
            return response.json()

        def show(operation: dict) -> dict:
            response = client.get(base + '/distill/' + operation['operation_id'])
            require(response.status_code == 200, f'show HTTP {response.status_code}')
            require('capture_json' not in response.text and 'RAW_CORPUS_SENTINEL' not in response.text,
                    'operation exposed internal capture/body')
            return response.json()

        def result(operation: dict) -> str:
            row = org.db._conn.execute('SELECT capture_json FROM knowledge_distill_operations WHERE operation_id=?',
                                       (operation['operation_id'],)).fetchone()
            capture = RetiredSource.model_validate_json(row[0])
            return json.dumps({
                'format': 'knowledge-curation@1', 'capture_digest': capture_digest(capture),
                'explicitly_empty': not capture.records,
                'dispositions': [{'reference_id': r.reference_id, 'disposition': 'selected',
                                  'reason': 'Reusable attributed lesson', 'exclusion_category': None,
                                  'count': 1} for r in capture.records],
                'items': [{'item_id': f'lesson-{n}', 'category': 'lesson_fact', 'title': 'Keep attribution',
                           'content': 'Preserve attribution when sharing a lesson.', 'references': [r.reference_id],
                           'instill_eligible': True, 'attribution': [{'reference_id': r.reference_id,
                               'original_author': capture.source_id, 'authored_at': r.metadata['authored_at'],
                               'observed_at': capture.cutoff, 'founder_ruling': None, 'founder_ruling_reference': None}],
                           'flags': [], 'warnings': [], 'prerequisites': None, 'limits': None,
                           'open_work': None} for n, r in enumerate(capture.records)],
            })

        def fixed_coverage(operation: dict) -> list[dict]:
            row = org.db._conn.execute('SELECT capture_json FROM knowledge_distill_operations WHERE operation_id=?',
                                       (operation['operation_id'],)).fetchone()
            return json.loads(row[0])['coverage']

        def check_coverage(operation: dict, expected: list[dict], boundary: str) -> None:
            require(operation.get('coverage') == expected, boundary + ': fixed coverage missing or changed')
            fields = {'category', 'state', 'reason', 'count', 'cutoff', 'errors'}
            require(bool(expected) and all(set(c) == fields and c['cutoff'] == operation['cutoff']
                                          for c in operation['coverage']), boundary + ': unsafe/incomplete coverage')
            require(not {'records', 'capture_json', 'confirmation_json'} & set(operation)
                    and 'RAW_CORPUS_SENTINEL' not in json.dumps(operation), boundary + ': internal body exposed')

        def cli_coverage(operation: dict, expected: list[dict], *, replay: bool = False) -> None:
            if replay:
                path = scratch / 'coverage-confirmation.json'
                path.write_text(json.dumps({'submission_id': operation['submission_id'],
                                           'source_id': operation['source_id'], 'confirmed': True}))
                args = ('knowledge', 'distill', '--from-file', str(path))
            else:
                args = ('knowledge', 'distill-show', operation['operation_id'])
            read = cli(*args, '--org', 'synthetic')
            require(read.returncode == 0, 'coverage CLI failed')
            check_coverage(json.loads(read.stdout), expected, 'CLI POST replay' if replay else 'CLI GET')

        def callback(operation: dict, *, raw: str | None = None, action: str = 'done',
                     status: str = 'completed', through_cli: bool = False, generation: str = '') -> tuple[str, int]:
            task_id, agent = operation['task_id'], operation['processor_id']
            sid = 'synthetic-session-' + task_id + generation
            # Launch/model boundary substituted; callback auth is real.
            org.db.update_task(task_id, status=TaskStatus.IN_PROGRESS, current_session_id=sid)
            org.sessions.set_active(task_id, agent, sid, org_slug='synthetic')
            body = {'session_id': sid, 'agent': agent, 'status': status, 'confidence': 80,
                    'output_summary': result(operation) if raw is None else raw,
                    'decision': {'action': action, 'summary': 'DECISION_SUMMARY_IS_NOT_THE_CURATED_RESULT'}}
            if action == 'supersede':
                body['decision'] = {'action': action, 'successor_brief': 'Synthetic continuation',
                                    'rationale': 'Synthetic non-done outcome',
                                    'attestation': {'recovery_reason': 'Synthetic only', **{key: True for key in (
                                        'policy_product_intent_unchanged', 'no_budget_or_external_commitment',
                                        'no_permission_or_cross_team_change',
                                        'no_schema_auth_security_privacy_or_data_access_change', 'no_unresolved_founder_gate')}}}
            bad = client.post('/api/v1/orgs/synthetic/tasks/' + task_id + '/completion',
                              json={**body, 'session_id': 'unbound-session'})
            require(bad.status_code == 409, 'unbound completion accepted')
            if through_cli:
                payload = {'task_id': task_id, **body, 'summary': body['output_summary']}
                del payload['output_summary']
                path = scratch / 'completion.json'
                path.write_text(json.dumps(payload))
                sent = cli('report-completion', '--org', 'synthetic', '--from-file', str(path))
                require(sent.returncode == 0, f'completion CLI exit {sent.returncode}: {sent.stderr}')
            else:
                sent = client.post('/api/v1/orgs/synthetic/tasks/' + task_id + '/completion', json=body)
                require(sent.status_code == 200, f'completion route HTTP {sent.status_code}')
            row = org.db.get_latest_task_result(task_id, agent, sid)
            require(row is not None and row['output_summary'] == body['output_summary'], 'persisted result missing')
            return sid, row['id']

        def terminal(operation: dict) -> None:
            task_id = operation['task_id']
            task = org.db.get_task(task_id)
            row = org.db.get_latest_task_result(task_id, operation['processor_id'], task.current_session_id)
            report = completion_report_from_result_row(task_id, row)
            # Match run_step: native completion audit precedes consumption,
            # which also writes the ordinary orchestration decision audit.
            org.orchestrator._log_step_result(task_id, ExecutorResult(success=True, duration_seconds=0,
                                             session_id=task.current_session_id), report, result_row_id=row['id'])
            native = [r for r in org.db.get_audit_logs(task_id) if r['action'] == 'completion_report']
            require(len(native) == 1, 'native completion evidence missing/duplicated')
            require(native[0]['payload'] == report.model_dump(), 'native completion evidence altered')
            _consume_completion_report(org.orchestrator, task_id, report, result_row_id=row['id'])
            steps = [r for r in org.db.get_audit_logs(task_id) if r['action'] == 'orchestration_step']
            require(len(steps) == 1 and steps[0]['payload']['decision'] == report.decision.model_dump(exclude_none=True),
                    'native orchestration evidence altered')
            require(org.db.get_latest_task_result(task_id, operation['processor_id'], task.current_session_id) == row,
                    'genuine persisted completion changed')
            require(org.db.get_task(task_id).status == TaskStatus.COMPLETED, 'native completion owner did not settle')

        def count_tasks() -> int:
            return org.db._conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]

        def happy() -> None:
            seed('happy')
            path = scratch / 'distill.json'
            path.write_text(json.dumps({'submission_id': 'happy', 'source_id': 'happy', 'confirmed': True}))
            sent = cli('knowledge', 'distill', '--from-file', str(path), '--org', 'synthetic')
            require(sent.returncode == 0, f'distill CLI exit {sent.returncode}: {sent.stdout[:100]}')
            operation = json.loads(sent.stdout)
            expected = fixed_coverage(operation)
            check_coverage(operation, expected, 'CLI POST Processing')
            check_coverage(show(operation), expected, 'HTTP GET Processing')
            cli_coverage(operation, expected)
            task = org.db.get_task(operation['task_id'])
            require(task.assigned_agent == 'curator' and task.parent_task_id is None and task.task_type == 'task',
                    'processor not actual ordinary root owner')
            require('QUOTED_UNTRUSTED_SNAPSHOT=' in task.brief, 'S2a brief absent')
            require(state.queue._pending_counts[('synthetic', task.id)] == 1, 'ordinary task was not queued')
            repeated = submit('happy')
            check_coverage(repeated, expected, 'HTTP POST replay Processing')
            require(repeated['operation_id'] == operation['operation_id'] and count_tasks() == 1, 'duplicate dispatch')
            conflict = client.post(base + '/distill', json={'source_id': 'happy', 'submission_id': 'second', 'confirmed': True})
            require(conflict.status_code == 409, 'duplicate inflight source admitted')
            changed = client.post(base + '/distill', json={'source_id': 'happy', 'submission_id': 'happy',
                                                         'confirmed': True, 'processor_id': 'worker'})
            require(changed.status_code == 409, 'same key changed confirmation accepted')
            sid, row_id = callback(operation, through_cli=True)
            waiting = show(operation)
            require(waiting['state'] == 'Processing' and waiting['phase'] == 'completion_received_awaiting_terminal',
                    'callback alone became Ready')
            terminal(operation)
            ready = show(operation)
            check_coverage(ready, expected, 'HTTP GET Ready')
            cli_coverage(ready, expected)
            cli_coverage(ready, expected, replay=True)
            check_coverage(submit('happy'), expected, 'HTTP POST replay Ready')
            require(ready['state'] == 'Ready' and ready['session_id'] == sid and ready['result_id'] == row_id,
                    'terminal completion not published with exact references')
            for args in [('knowledge', 'distill-show', ready['operation_id']),
                         ('knowledge', 'archives', 'list'), ('knowledge', 'archives', 'show', ready['archive_id'])]:
                read = cli(*args, '--org', 'synthetic')
                require(read.returncode == 0 and 'RAW_CORPUS_SENTINEL' not in read.stdout, 'CLI read failed/exposed body')
                json.loads(read.stdout)
            archive = client.get(base + '/archives/' + ready['archive_id'])
            require(archive.status_code == 200 and len(archive.json()['items']) == 1, 'archive not readable')
            reference = archive.json()['manifest']['inventory'][0]['reference_id']
            require(client.get(base + '/retired-agents/happy/sources/' + reference).status_code == 200,
                    'safe source reference not readable')

        def processors() -> None:
            for source, processor, code in [('human', 'founder', 'processor_ineligible_choose_existing_owner'),
                                            ('absent', 'absent', 'processor_unknown_owner'),
                                            ('no_history', None, 'processor_missing_choose_existing_owner')]:
                seed(source, history=source != 'no_history')
                before = count_tasks()
                op = submit(source, processor=processor)
                require(op['state'] == 'Failed' and op['failure']['code'] == code and count_tasks() == before,
                        'ineligible processor allocated task or lost native cause')
            seed('explicit', history=False)
            op = submit('explicit', processor='worker')
            require(op['processor_id'] == 'worker' and org.db.get_task(op['task_id']).assigned_agent == 'worker',
                    'explicit executable owner not honored')
            callback(op)
            terminal(op)
            require(show(op)['state'] == 'Ready', 'worker processor failed normal task contract')
            source = 'inactive'
            seed(source)
            definition = root / 'org/agents/curator.md'
            saved = definition.read_bytes()
            definition.unlink()
            try:
                before = count_tasks()
                refused = submit(source)
                require(refused['state'] == 'Failed' and refused['failure']['code'] == 'processor_unknown_owner'
                        and refused['processor_id'] == 'curator' and count_tasks() == before,
                        'inactive historical manager silently replaced')
            finally:
                definition.write_bytes(saved)
            ordinary = client.post('/api/v1/orgs/synthetic/tasks', json={'owner': 'worker', 'brief': 'Synthetic ordinary task'})
            require(ordinary.status_code == 200 and org.db.get_task(ordinary.json()['task_id']).assigned_agent == 'worker',
                    'ordinary submission regressed')
            mismatch = client.post('/api/v1/orgs/synthetic/tasks', json={'owner': 'worker', 'team': 'missing',
                                                                       'brief': 'Synthetic refused task'})
            require(mismatch.status_code == 400, 'ordinary owner/team admission regressed')

        def oversize() -> None:
            seed('oversize', oversized=True)
            before = count_tasks()
            op = submit('oversize')
            require(op['state'] == 'Failed' and op['failure']['code'] == 'curation_input_too_large'
                    and op['failure']['limit_bytes'] == 262144 and op['failure']['entries'] == 20
                    and op['failure']['measured_bytes'] > 262144 and count_tasks() == before,
                    'oversize dispatched or lost measured refusal')

        def outcomes() -> None:
            for action in ('delegate', 'escalate', 'fanout', 'parallel', 'supersede'):
                seed(action)
                op = submit(action)
                callback(op, action=action)
                failed = show(op)
                require(failed['state'] == 'Failed' and failed['failure']['code'] == 'processor_decision_' + ('fanout' if action == 'parallel' else action),
                        'non-done decision accepted')
            seed('late_read')
            op = submit('late_read')
            first_sid, first_result = callback(op, action='delegate')
            callback(op, generation='-second')
            terminal(op)
            failed = show(op)
            require(failed['state'] == 'Failed' and failed['failure']['code'] == 'processor_decision_delegate'
                    and failed['result_id'] == first_result and failed['session_id'] == first_sid,
                    'later done hid original non-done completion')
            for status in ('failed', 'blocked', 'cancelled', 'superseded'):
                source = 'outcome_' + status
                seed(source)
                op = submit(source)
                callback(op, status=status)
                failed = show(op)
                require(failed['state'] == 'Failed' and failed['failure']['code'] == 'processor_outcome_' + status,
                        'non-success result accepted')

        def invalid_result() -> None:
            seed('validation')
            op = submit('validation')
            callback(op)
            terminal(op)
            ready = show(op)
            original = client.get(base + '/archives/' + ready['archive_id']).content
            for key, code in [('malformed', 'curation_output_malformed'),
                              ('invalid', 'curation_inventory_incomplete_or_duplicate'),
                              ('missing', 'curation_output_missing'),
                              ('large_result', 'curation_result_too_large'),
                              ('echo', 'curation_output_malformed')]:
                bad = submit('validation', key=key)
                invalid = json.loads(result(bad))
                invalid['dispositions'] = []
                raw = {'malformed': '{', 'invalid': json.dumps(invalid), 'missing': '',
                       'large_result': 'x' * 131073,
                       'echo': 'RAW_CORPUS_SENTINEL: a synthetic source observation.'}[key]
                callback(bad, raw=raw)
                terminal(bad)
                failed = show(bad)
                check_coverage(failed, fixed_coverage(bad), 'HTTP GET invalid Failed')
                cli_coverage(failed, fixed_coverage(bad))
                require(failed['state'] == 'Failed' and failed['failure']['code'] == code, 'invalid curation became Ready')
                require(client.get(base + '/archives/' + ready['archive_id']).content == original, 'prior Ready changed')
                available = client.get(base + '/archives').json()['archives']
                require([a['archive_id'] for a in available] == [ready['archive_id']], 'failed archive selectable')
            retry = submit('validation', key='retry')
            callback(retry)
            terminal(retry)
            retried = show(retry)
            require(retried['archive_version'] == 2 and retried['archive_id'] != ready['archive_id'], 'retry overwrote archive')
            seed('unavailable', unavailable=True)
            failed = submit('unavailable')
            expected = fixed_coverage(failed)
            require(failed['state'] == 'Failed' and failed['task_id'] is None, 'unavailable source dispatched')
            runtime = next(c for c in expected if c['category'] == 'runtime_memory')
            require(runtime['state'] == 'unavailable' and runtime['count'] is None
                    and runtime['errors'] == ['malformed_frontmatter:MEM-001-lesson0.md'],
                    'fixture did not reach required-source failure')
            check_coverage(failed, expected, 'HTTP POST source Failed')
            path = org.db.db_path
            org.db.close()
            org.db = Database(path)
            orchestrator()
            with patch('runtime.knowledge.distill.capture_retired_source', side_effect=AssertionError('recaptured Failed')):
                check_coverage(show(failed), expected, 'HTTP GET Failed after reopen')
                check_coverage(submit('unavailable'), expected, 'HTTP POST Failed replay after reopen')
                cli_coverage(failed, expected)
                cli_coverage(failed, expected, replay=True)

        def restart() -> None:
            seed('restart')
            op = submit('restart')
            expected = fixed_coverage(op)
            check_coverage(op, expected, 'HTTP POST Processing')
            path = org.db.db_path
            org.db.close()
            org.db = Database(path)
            org.sessions = SessionTracker()
            state.queue = TaskQueue()
            orchestrator()
            for _ in range(2):
                require(show(op)['state'] == 'Processing' and count_tasks() == 1, 'pending reconcile duplicates task')
            with patch('runtime.knowledge.distill.capture_retired_source', side_effect=AssertionError('recaptured Processing')):
                check_coverage(show(op), expected, 'HTTP GET Processing after reopen')
                check_coverage(submit('restart'), expected, 'HTTP POST Processing replay after reopen')
                cli_coverage(op, expected)
                cli_coverage(op, expected, replay=True)
            require(state.queue._pending_counts[('synthetic', op['task_id'])] == 1, 'restart did not coalesce original task')
            callback(op)
            terminal(op)
            path = org.db.db_path
            org.db.close()
            org.db = Database(path)
            org.sessions = SessionTracker()
            orchestrator()
            ready = show(op)
            check_coverage(ready, expected, 'HTTP GET Ready after reopen')
            require(ready['state'] == 'Ready' and count_tasks() == 1, 'restart did not converge original task')
            with patch('runtime.knowledge.distill.capture_retired_source', side_effect=AssertionError('recaptured Ready')):
                for _ in range(2):
                    repeated = submit('restart')
                    check_coverage(repeated, expected, 'HTTP POST Ready replay after reopen')
                    require(repeated['archive_id'] == ready['archive_id'], 'replay published another archive')
                cli_coverage(ready, expected)
                cli_coverage(ready, expected, replay=True)
            require(org.db._conn.execute('SELECT COUNT(*) FROM knowledge_archives').fetchone()[0] == 1, 'double publish')
            seed('uncertain')
            held = submit('uncertain')
            org.db.update_task(held['task_id'], status=TaskStatus.COMPLETED, current_session_id='unknown-generation')
            uncertain = show(held)
            require(uncertain['state'] == 'Processing' and uncertain['phase'] == 'completion_evidence_unavailable',
                    'missing terminal result invented Ready/Failed')

        def binding() -> None:
            seed('binding')
            # SQLite fault at the actual bind effect, with no runtime seam.
            org.db._conn.execute("CREATE TRIGGER synthetic_bind_fault BEFORE UPDATE ON knowledge_distill_operations "
                                 "WHEN NEW.task_id IS NOT NULL BEGIN SELECT RAISE(ABORT,'synthetic_bind_fault'); END")
            org.db._conn.commit()
            try:
                response = client.post(base + '/distill', json={'source_id': 'binding',
                                                               'submission_id': 'binding', 'confirmed': True})
                require(response.status_code == 500, 'binding fault did not reach real dispatch transaction')
                require(count_tasks() == 0, 'failed binding left an orphan task')
                row = org.db._conn.execute('SELECT state,task_id FROM knowledge_distill_operations').fetchone()
                require(tuple(row) == ('Queued', None), 'failed binding advertised a task')
            finally:
                org.db._conn.execute('DROP TRIGGER synthetic_bind_fault')
                org.db._conn.commit()
            op = submit('binding')
            require(op['state'] == 'Processing' and count_tasks() == 1, 'same submission did not recover atomic rollback')

        def local_args() -> None:
            for args in [('knowledge', 'distill', '--from-file', 'relative.json'),
                         ('knowledge', 'distill-show', '../invalid'),
                         ('knowledge', 'archives', 'show', '../invalid')]:
                bad = cli(*args, local=True)
                require(bad.returncode == 2 and 'invalid_knowledge_request' in bad.stdout,
                        'local invalid arg attempted daemon discovery')
            for content in ('{', '{"submission_id":"x","submission_id":"y"}',
                            '{"source_id":"source","submission_id":"x","confirmed":false}',
                            '{"source_id":"source","submission_id":"x","confirmed":1}'):
                path = scratch / 'invalid.json'
                path.write_text(content)
                bad = cli('knowledge', 'distill', '--from-file', str(path), local=True)
                require(bad.returncode == 2 and 'invalid_knowledge_request' in bad.stdout, 'malformed local file contacted daemon')

        def empty() -> None:
            seed('empty', empty=True)
            op = submit('empty')
            callback(op)
            terminal(op)
            ready = show(op)
            require(ready['state'] == 'Ready', 'verified empty corpus did not become Ready')
            archive = client.get(base + '/archives/' + ready['archive_id']).json()
            require(ready['state'] == 'Ready' and archive['items'] == [] and archive['manifest']['explicitly_empty'],
                    'verified empty corpus not explicit Ready')

        cases = {'happy': happy, 'processors': processors, 'oversize': oversize, 'outcomes': outcomes,
                 'invalid_result': invalid_result, 'restart': restart, 'binding': binding, 'local_args': local_args, 'empty': empty}
        with patch.dict(os.environ, {'HAPPYRANCH_DAEMON_HOME': str(home)}), patch.object(Path, 'home', return_value=scratch):
            thread.start()
            try:
                for _ in range(100):
                    if server.started:
                        break
                    time.sleep(.01)
                require(server.started, 'synthetic listener unavailable')
                with httpx.Client(base_url=f'http://127.0.0.1:{port}',
                                  headers={'Authorization': 'Bearer synthetic-distill-bearer'}, timeout=15) as client:
                    require(httpx.get(f'http://127.0.0.1:{port}' + base + '/retired-agents/missing').status_code == 401,
                            'existing source route lost bearer authentication')
                    for name, case in cases.items():
                        if selected is not None and name != selected:
                            continue
                        # Each case has an independent synthetic org/DB when
                        # selected; all-mode invokes the same script per case.
                        case()
                        results.append(name)
                        print(name + ': PASS', flush=True)
                    require(httpx.get(f'http://127.0.0.1:{port}' + base + '/archives').status_code == 401,
                            'new knowledge route lost bearer authentication')
                    feature_actions = [row[0] for row in org.db._conn.execute(
                        "SELECT DISTINCT action FROM audit_log WHERE action LIKE 'knowledge_%'")]
                    require(set(feature_actions) <= {'knowledge_distill'}, 'unclassified feature audit action')
                    for row in [r for action in feature_actions for r in org.db.get_audit_logs_by_action(action)]:
                        payload = json.dumps(row['payload'])
                        require(set(row['payload']) == {'operation_id', 'source_id', 'processor_id', 'task_id',
                                'session_id', 'result_id', 'state', 'phase', 'failure', 'archive_id', 'version'},
                                'feature audit contains non-metadata fields')
                        require(all(text not in payload for text in ('RAW_CORPUS_SENTINEL', 'capture_json',
                                'knowledge-curation@1', 'Preserve attribution when sharing a lesson.',
                                'DECISION_SUMMARY_IS_NOT_THE_CURATED_RESULT')),
                                'feature audit exposed corpus/result body')
                    require(results, 'unknown case')
                    actual = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                              for parent in (root / 'org/agents/_terminated', root / 'workspaces/_terminated')
                              for path in parent.rglob('*') if path.is_file()}
                    require(actual == originals, 'retained source bytes or inventory changed')
            finally:
                server.should_exit = True
                thread.join(timeout=5)
                require(not thread.is_alive(), 'synthetic HTTP owner not joined')
                sock.close()
                org.db.close()


if __name__ == '__main__':
    if len(sys.argv) > 1:
        run(sys.argv[1])
    else:
        for case in ('happy', 'processors', 'oversize', 'outcomes', 'invalid_result', 'restart', 'binding', 'local_args', 'empty'):
            subprocess.run([sys.executable, __file__, case], check=True, timeout=50)

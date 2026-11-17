"""THR-357 Fix 2: real prepare/refresh receipts and initial startup seam."""
from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import patch

from scripts.test_agent_retirement import fixture, PREFIX
from scripts.mac_native.suite_repo_refresh import git, git_environment, stale_clone
from runtime.daemon import __main__ as entry
from runtime.daemon.lifetime_lock import LockProof
from runtime.infrastructure.memory_collection import CollectionObserver
from runtime.models import TaskRecord
from runtime.orchestrator.host_supervisor import build_default_host_supervisor
from runtime.orchestrator.task_pause import TaskInvocation
from runtime.orchestrator import workspace_adapters as adapters
from runtime.platform.macos_process_group import MacOSProcessGroupBackend

PROVEN = LockProof(True, False, 'earlier_lock_holders_gone', None)
ACTION = 'repository_preparation_startup_closed'


def capture(org: Any, proof: LockProof = PROVEN) -> Any:
    # Base production still reaches the observable assertion for RED evidence.
    try:
        from runtime.daemon.preparation_recovery import StartupPreparations
    except ImportError:
        return None
    return StartupPreparations(org.orchestrator, proof)


def sweep(org: Any, captured: Any) -> Any:
    kwargs = ({'startup_preparations': captured}
              if 'startup_preparations' in inspect.signature(entry._sweep_on_startup).parameters else {})
    return entry._sweep_on_startup(org.db, org.orchestrator._queue, org.slug,
                                   org.orchestrator, **kwargs)


def journal(org: Any) -> list[dict]:
    with org.db._lock:
        return deepcopy(org.db._task_pause_store.row_uncommitted('TASK-stuck')['journal']['entries'])


def audits(org: Any) -> list[tuple]:
    return [tuple(r) for r in org.db.execute('SELECT * FROM audit_log ORDER BY id')]


def seed(org: Any, scratch: Path, *, linked: bool = False) -> tuple[TaskInvocation, Path]:
    """Interrupt the real refresh worker before its original caller's completion.

    Native process-group backend runs owned children only (no systemd/service).
    The fault drops subsequent emissions after an actual committed started row;
    the original supervisor still disposes/reaps the gate child for test hygiene.
    No receipt, PID, phase or executor/session-start evidence is fabricated.
    """
    generated, _, _ = stale_clone(scratch)
    workspace = org.root / 'workspaces' / 'worker'
    if linked:
        primary = scratch / 'primary'
        (generated / 'repos' / 'sample').rename(primary)
        (workspace / 'repos').mkdir()
        git(primary, 'worktree', 'add', '--detach', str(workspace / 'repos' / 'sample'), 'HEAD')
    else:
        (generated / 'repos').rename(workspace / 'repos')
    org.db.insert_task(TaskRecord(id='TASK-stuck', brief='Synthetic interrupted refresh',
                                  assigned_agent='worker', team='retiring'))
    observer = CollectionObserver(org=org.slug, root=org.root, db=org.db)
    org.db._memory_collection_context = SimpleNamespace(db=org.db, memory_collection=observer)
    owner = TaskInvocation(org.orchestrator, org.db.get_task('TASK-stuck'), 'worker', 'reserved-new-sid')
    owner.prepare()
    supervisor = build_default_host_supervisor(backend=MacOSProcessGroupBackend())
    original = adapters._RepoPreparationEvidence.emit
    interrupted = False
    def emit(evidence: Any, phase: str, **facts: Any) -> int:
        nonlocal interrupted
        if interrupted:
            raise RuntimeError('synthetic interrupted emitter')
        result = original(evidence, phase, **facts)
        if phase == 'started':
            interrupted = True
            raise RuntimeError('synthetic interruption after committed child start')
        return result
    work = []
    try:
        with patch.object(adapters._RepoPreparationEvidence, 'emit', emit):
            adapters._repo_refresh_batch(workspace, owner, supervisor,
                time.monotonic()+10, threading.Event(), work)
    finally:
        for record in work:
            record['lease'].__exit__(None, None, None)
    assert interrupted, ('must reach real started commit', [(r['evidence'].sequence if r['evidence'] else None, r['outcome']) for r in work], supervisor.probe())
    assert journal(org)[0]['phase'] == 'prepared'
    assert org.db.get_task('TASK-stuck').current_session_id is None
    history = org.db.repository_preparation_history_uncommitted('TASK-stuck')
    assert history[-1]['event']['phase'] == 'started'
    pid = history[-1]['event']['pid']
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        pass
    else:
        raise AssertionError('original child must be reaped by its real supervisor')
    # Model process exit: local owner maps vanish; persistent evidence survives.
    org.db._task_pause_store._live.clear()
    org.orchestrator._team_moves._reservations.clear()
    observer.boot_id = 'next-daemon-boot'
    return owner, workspace / 'repos' / 'sample'


def isolated(test: unittest.TestCase) -> bool:
    if os.environ.get('HAPPYRANCH_TEST_PREPARATION_ISOLATED') == test.id():
        return True
    result = subprocess.run([sys.executable, '-B', '-W', 'ignore::ResourceWarning', '-m',
        'unittest', test.id(), '-v'], env=dict(os.environ, HAPPYRANCH_TEST_PREPARATION_ISOLATED=test.id()),
        capture_output=True, text=True, timeout=55)
    test.assertEqual(result.returncode, 0, result.stdout[-8000:] + result.stderr[-8000:])
    return False


class PreparationStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_previous_boot_closes_and_normal_continuation_admits(self) -> None:
        if not isolated(self):
            return
        await self.check_positive(registered=False)

    async def test_registered_worktree_closes(self) -> None:
        if not isolated(self):
            return
        await self.check_positive(registered=True)

    async def test_linked_repository_closes(self) -> None:
        if not isolated(self):
            return
        await self.check_positive(registered=False, linked=True)

    async def check_positive(self, *, registered: bool, linked: bool = False) -> None:
        with tempfile.TemporaryDirectory() as temp:
            scratch = Path(temp)
            with git_environment(scratch):
                async with fixture(scratch/'org') as (org, _):
                    old, repo = seed(org, scratch, linked=linked)
                    if registered:
                        git(repo, 'worktree', 'add', '--detach', '.claude/worktrees/TASK-stuck', 'HEAD')
                    before = audits(org)
                    sweep(org, capture(org))
                    self.assertEqual(journal(org), [], 'proven prepared-only entry must close')
                    after = audits(org)
                    self.assertEqual(after[:len(before)], before)
                    closed = org.db.execute('SELECT agent,payload FROM audit_log WHERE action=?', (ACTION,)).fetchall()
                    self.assertEqual(len(closed), 1)
                    self.assertEqual(closed[0]['agent'], 'daemon')
                    receipt = json.loads(closed[0]['payload'])
                    self.assertEqual(receipt['original']['phase'], 'prepared')
                    self.assertTrue(receipt['restored']['context']['execution_unknown'])
                    self.assertIsNone(org.db.get_task('TASK-stuck').current_session_id)
                    successor = TaskInvocation(org.orchestrator, org.db.get_task('TASK-stuck'), 'worker', 'normal-next-sid')
                    successor.prepare()
                    self.assertTrue(successor.commit())
                    self.assertEqual(org.db.get_task('TASK-stuck').current_session_id, 'normal-next-sid')
                    self.assertNotEqual(successor.identity, old.identity)

    async def test_initial_load_captures_before_first_restore(self) -> None:
        if not isolated(self):
            return
        with tempfile.TemporaryDirectory() as temp:
            scratch = Path(temp)
            with git_environment(scratch):
                async with fixture(scratch/'org') as (org, _):
                    seed(org, scratch)
                    runtime_root = org.root.parent.parent
                code = """import inspect,sys
from pathlib import Path
from runtime.config import Settings
from runtime.daemon.state import DaemonState
from runtime.daemon.lifetime_lock import LockProof
from runtime.daemon import __main__ as entry
from runtime.runtime import RuntimeDir
proof=LockProof(True,False,'earlier_lock_holders_gone',None)
kwargs={'startup_lock_proof':proof} if 'startup_lock_proof' in inspect.signature(DaemonState.from_runtime).parameters else {}
state=DaemonState.from_runtime(RuntimeDir.load(Path(sys.argv[1])),Settings(),**kwargs)
org=state.orgs['alpha']
try:
    with org.db._lock:
        assert org.db._task_pause_store.row_uncommitted('TASK-stuck')['journal']['entries'][0]['phase']=='unknown'
    captured=getattr(org.orchestrator,'_startup_preparations',None)
    assert captured is not None and captured.records[0]['phase']=='prepared', 'initial prepared population must survive first restore'
    entry._initialize_runnable_startup(state,lock_proof=proof)
    with org.db._lock:
        assert org.db._task_pause_store.row_uncommitted('TASK-stuck')['journal']['entries']==[], 'startup must close captured prepared entry'
    assert org.db.execute("SELECT COUNT(*) FROM audit_log WHERE action='repository_preparation_startup_closed'").fetchone()[0]==1
finally:
    import asyncio
    asyncio.run(state.close_all())
"""
                result = subprocess.run([sys.executable, '-B', '-W', 'ignore::ResourceWarning', '-c', code, str(runtime_root)],
                    env=dict(os.environ, HAPPYRANCH_DAEMON_HOME=str(scratch/'org'/'home')),
                    capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stdout[-8000:] + result.stderr[-8000:])

    async def test_negative_proof_and_repository_matrix(self) -> None:
        cases = ('unproven', 'same_boot', 'possible_launch', 'job', 'live_child', 'eperm_child',
                 'missing_pid', 'missing_start_pin', 'attempt_without_start', 'new_entry',
                 'index_lock', 'repo_admin_lock', 'refs_lock', 'worktree_locked', 'broken_worktree', 'busy_lease',
                 'history_exhausted', 'native_payload_exhausted', 'malformed_boundary', 'duplicate_boundary', 'audit_failure', 'changed_entry')
        selected_case = os.environ.get('HAPPYRANCH_TEST_PREPARATION_CASE')
        if selected_case is None:
            # Source agreement is intentionally strict. Isolate real refresh
            # fixtures from earlier source-compilation/interning state; never
            # patch or relax that production check.
            def run_case(case: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run([sys.executable, '-B', '-W', 'ignore::ResourceWarning', '-m', 'unittest',
                    __name__ + '.PreparationStartupTests.test_negative_proof_and_repository_matrix', '-v'],
                    env=dict(os.environ, HAPPYRANCH_TEST_PREPARATION_CASE=case),
                    capture_output=True, text=True, timeout=20)
            with ThreadPoolExecutor(max_workers=4) as workers:
                results = list(workers.map(run_case, cases))
            for case, result in zip(cases, results, strict=True):
                with self.subTest(case=case):
                    self.assertEqual(result.returncode, 0, result.stdout[-8000:] + result.stderr[-8000:])
            return
        self.assertIn(selected_case, cases)
        for case in (selected_case,):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temp:
                scratch = Path(temp)
                with git_environment(scratch):
                    async with fixture(scratch/'org') as (org, client):
                        initial = capture(org) if case == 'new_entry' else None
                        prior, repo = seed(org, scratch, linked=case == 'repo_admin_lock')
                        proof = LockProof(False, True, 'previous_pid_probe_ambiguous', None) if case == 'unproven' else PROVEN
                        if case in {'possible_launch', 'job'}:
                            with org.db._task_pause_store.writer('TASK-stuck') as (_, row):
                                row['journal']['entries'][0]['phase' if case == 'possible_launch' else 'owner'] = case if case == 'possible_launch' else 'job'
                                org.db._task_pause_store.save_journal_uncommitted(row)
                        if case in {'missing_pid', 'missing_start_pin', 'attempt_without_start', 'history_exhausted'}:
                            row = org.db.execute("SELECT id,payload FROM audit_log WHERE action='repository_preparation' ORDER BY id DESC LIMIT 1").fetchone()
                            body = json.loads(row['payload'])
                            if case == 'attempt_without_start':
                                org.db.execute('DELETE FROM audit_log WHERE id=?', (row['id'],))
                            elif case == 'history_exhausted':
                                # Real appender below owns valid growth; this case proves the payload cap.
                                org.db.execute('UPDATE audit_log SET payload=? WHERE id=?', ('x'*16385, row['id']))
                            else:
                                body['pid' if case == 'missing_pid' else 'start_pin'] = None
                                org.db.execute('UPDATE audit_log SET payload=? WHERE id=?', (json.dumps(body), row['id']))
                        if case == 'index_lock':
                            (repo/'.git/index.lock').touch()
                        if case == 'repo_admin_lock':
                            admin = Path((repo / '.git').read_text()[8:].strip())
                            (admin / 'index.lock').touch()
                        if case == 'refs_lock':
                            (repo/'.git/refs/heads/retained.lock').touch()
                        if case == 'broken_worktree':
                            path = repo/'.claude/worktrees/TASK-stuck'
                            path.mkdir(parents=True)
                            (path/'.git').write_text('gitdir: /missing/admin\n')
                        if case == 'worktree_locked':
                            git(repo, 'worktree', 'add', '--detach', '.claude/worktrees/TASK-stuck', 'HEAD')
                            git(repo, 'worktree', 'lock', '.claude/worktrees/TASK-stuck')
                        if case == 'native_payload_exhausted':
                            org.db.insert_audit_log_uncommitted('TASK-stuck', 'worker', 'task_native_launch_boundary',
                                {'session_id': 'unrelated-old-sid', 'padding': 'x'*8192})
                        if case == 'malformed_boundary':
                            org.db.insert_audit_log_uncommitted('TASK-stuck', 'worker', 'task_native_launch_boundary',
                                {'session_id': 'unrelated-old-sid'})
                        if case == 'duplicate_boundary':
                            payload = json.dumps({'kind': 'started', 'logical_owner': 'd'*64, 'attempt_id': 'e'*32,
                                'invocation_id': 'f'*32, 'session_id': 'unrelated-old-sid', 'count': 0})
                            payload = payload.replace('"session_id": "unrelated-old-sid"',
                                '"session_id": "reserved-new-sid", "session_id": "unrelated-old-sid"')
                            audit_id = org.db.insert_audit_log_uncommitted('TASK-stuck', 'worker', 'task_native_launch_boundary', {})
                            org.db.execute('UPDATE audit_log SET payload=? WHERE id=?', (payload, audit_id))
                        org.db._conn.commit()
                        if case == 'same_boot':
                            org.db._memory_collection_context.memory_collection.boot_id = org.db.repository_preparation_history_uncommitted('TASK-stuck')[0]['event']['owner']['boot_id']
                        captured = capture(org, proof)
                        if case == 'new_entry':
                            captured = initial
                        if case == 'changed_entry':
                            with org.db._task_pause_store.writer('TASK-stuck') as (_, row):
                                row['journal']['entries'][0]['context']['audit_ids'] = [123]
                                org.db._task_pause_store.save_journal_uncommitted(row)
                        history = [tuple(r) for r in org.db.execute("SELECT * FROM audit_log WHERE action='repository_preparation'")]
                        from contextlib import ExitStack
                        filesystem_case = case in {'index_lock', 'repo_admin_lock', 'refs_lock', 'worktree_locked', 'broken_worktree', 'busy_lease'}
                        with ExitStack() as stack:
                            logs = stack.enter_context(self.assertLogs('happyranch.daemon', level='INFO')) if filesystem_case else None
                            if case == 'busy_lease':
                                stack.enter_context(adapters._repo_refresh_lease(repo, deadline=time.monotonic()+2, cancel_requested=None))
                            if case in {'live_child', 'eperm_child'}:
                                stack.enter_context(patch('os.kill', return_value=None) if case == 'live_child'
                                    else patch('os.kill', side_effect=PermissionError()))
                            if case == 'audit_failure':
                                real = org.db.insert_audit_log_uncommitted
                                def fail(task: str, agent: str, action: str, payload: dict) -> int:
                                    if action == ACTION:
                                        raise OSError('synthetic audit failure')
                                    return real(task, agent, action, payload)
                                stack.enter_context(patch.object(org.db, 'insert_audit_log_uncommitted', fail))
                            sweep(org, captured)
                        if filesystem_case:
                            expected_path = str(admin / 'index.lock') if case == 'repo_admin_lock' else str(repo)
                            self.assertIn(expected_path, '\n'.join(logs.output))
                        self.assertEqual(len(journal(org)), 1)
                        self.assertEqual([tuple(r) for r in org.db.execute("SELECT * FROM audit_log WHERE action='repository_preparation'")], history)
                        self.assertEqual(org.db.execute('SELECT COUNT(*) FROM audit_log WHERE action=?', (ACTION,)).fetchone()[0], 0)
                        response = await client.get(f'{PREFIX}/tasks/TASK-stuck/preparation-recovery')
                        if case == 'missing_pid':
                            self.assertEqual(response.status_code, 409, response.text)
                        elif case in {'history_exhausted', 'native_payload_exhausted'}:
                            self.assertEqual(response.status_code, 503, response.text)
                        else:
                            self.assertEqual(response.status_code, 200, response.text)
                            self.assertFalse(response.json()['eligible'])
                        if captured is not None:
                            # No same-boot retry after a veto heals.
                            captured.run()
                            self.assertEqual(len(journal(org)), 1)

    async def test_growth_is_candidate_local_at_real_startup(self) -> None:
        if not isolated(self):
            return
        with tempfile.TemporaryDirectory() as temp:
            scratch = Path(temp)
            with git_environment(scratch):
                async with fixture(scratch/'org') as (org, _):
                    _, _ = seed(org, scratch)
                    from runtime.infrastructure.db.tasks import repository_preparation_event
                    original = org.db.repository_preparation_history_uncommitted('TASK-stuck')[0]['event']
                    # Real validated appender, separate valid obligation prefixes.
                    # Its scoped byte bound is exhausted; that is not empty history.
                    inventory = {'intervals': [{'id': i, 'kind': 'native', 'closed': True, 'failed': True}
                        for i in range(128)], 'retained': [], 'outer_closed': False}
                    for n in range(300):
                        obligation = f'{n+1:032x}'
                        try:
                            with org.db._task_pause_store.writer('TASK-stuck'):
                                for sequence, phase, facts in ((0, 'opened', {}), (1, 'worker_returned', {}),
                                        (2, 'outer_closed', {'inventory': inventory})):
                                    event = repository_preparation_event(original['owner'], original['repository'],
                                        obligation, sequence, phase, **facts)
                                    org.db.append_repository_preparation_uncommitted(event)
                        except ValueError:
                            break
                    from runtime.infrastructure.db.tasks import RepositoryPreparationError
                    with self.assertRaises(RepositoryPreparationError):
                        org.db.repository_preparation_history_uncommitted('TASK-stuck')
                    # Unrelated healthy prepared candidate uses actual prepare and receipt appender.
                    org.db.insert_task(TaskRecord(id='TASK-unrelated', brief='Other preparation', assigned_agent='worker', team='retiring'))
                    other = TaskInvocation(org.orchestrator, org.db.get_task('TASK-unrelated'), 'worker', 'other-sid')
                    other.prepare()
                    binding = dict(original['owner'], scope=other.task.id, native_id=other.task.id,
                        invocation_id=other.identity, attempt_id=other.refusal_attempt_id, session_id=other.session_id)
                    with org.db._task_pause_store.writer(other.task.id):
                        org.db.append_repository_preparation_uncommitted(repository_preparation_event(binding, original['repository'], 'f'*32, 0, 'opened'))
                    org.db._task_pause_store._live.clear()
                    org.orchestrator._team_moves._reservations.clear()
                    sweep(org, capture(org))
                    self.assertEqual(len(journal(org)), 1)
                    closed = org.db.execute('SELECT task_id FROM audit_log WHERE action=?', (ACTION,)).fetchall()
                    self.assertEqual([r[0] for r in closed], ['TASK-unrelated'])


if __name__ == '__main__':
    unittest.main()

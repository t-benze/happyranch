"""Focused real-Git refresh checks, outside retired units and integration."""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
import json
import os
import sqlite3
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from typing import Any, Iterator
import unittest
from unittest.mock import patch

from scripts.test_agent_retirement import fixture
from scripts.test_preparation_startup import isolated
from scripts.mac_native.suite_repo_refresh import git, git_environment, stale_clone
from runtime.infrastructure.memory_collection import CollectionObserver
from runtime.models import BlockKind, JobInterpreter, JobRecord, JobStatus, TaskRecord, TaskStatus
from runtime.orchestrator import workspace_adapters as adapters
from runtime.orchestrator.host_supervisor import build_default_host_supervisor
from runtime.orchestrator.task_pause import TaskInvocation
from runtime.platform.macos_process_group import MacOSProcessGroupBackend


# Faults occur in an owned Git child's tail, after real Git exits successfully.
# Product observations, supervisor, journal, owner and closure are unpatched.
WRAPPER = '''#!{python}
import json, os, pathlib, subprocess, sys
root = pathlib.Path(__file__).parent
args = sys.argv[1:]
with (root/'calls').open('a') as stream:
    stream.write(json.dumps(args)+'\\n')
control = json.loads((root/'control').read_text())
def command_args(values):
    return [v for v in values if v not in ('--no-optional-locks', '--no-lazy-fetch')]
command = command_args(args)
calls = [json.loads(line) for line in (root/'calls').read_text().splitlines()]
matches = sum(command_args(a) == command for a in calls)
if command == control.get('trigger') and matches == control.get('nth', 1):
    result = subprocess.run(['/usr/bin/git', *args])
    if result.returncode == 0:
        action = control['action']
        if action == 'config':
            subprocess.run(['/usr/bin/git', 'config', 'merge.ff', 'false'], check=True)
        elif action == 'head':
            subprocess.run(['/usr/bin/git', 'update-ref', 'refs/heads/main', control['old']], check=True)
        elif action == 'branch':
            subprocess.run(['/usr/bin/git', 'symbolic-ref', 'HEAD', 'refs/heads/other'], check=True)
        elif action == 'transport':
            pathlib.Path('.git/remotes/origin').write_text('URL: '+control['other']+'\\n')
        (root/'fired').write_text(action)
    sys.exit(result.returncode)
os.execv('/usr/bin/git', ['git', *args])
'''


class RepoRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_hold_before_closure_defers_lease_error_and_pauses(self) -> None:
        if not isolated(self):
            return
        await self.held_lease_tail()

    async def test_hold_before_closure_missing_proof_stays_unknown(self) -> None:
        if not isolated(self):
            return
        await self.held_lease_tail('open_interval')

    async def test_hold_before_closure_unknown_cleanup_stays_unknown(self) -> None:
        if not isolated(self):
            return
        await self.held_lease_tail('cleanup_unavailable')

    async def held_lease_tail(self, fault: str | None = None) -> None:
        from runtime.orchestrator.run_step import run_step_impl
        from runtime.orchestrator.task_pause import current_invocation, settle_live
        with tempfile.TemporaryDirectory() as folder:
            scratch = Path(folder)
            with git_environment(scratch):
                generated, _, _ = stale_clone(scratch)
                async with fixture(scratch/'org') as (org, _):
                    workspace = org.root/'workspaces'/'worker'
                    (generated/'repos').rename(workspace/'repos')
                    git(workspace, 'clone', str(scratch/'origin.git'), str(workspace/'repos'/'second'))
                    org.db.insert_task(TaskRecord(id='TASK-root', brief='Synthetic held parent',
                        status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
                        assigned_agent='manager', team='retiring'))
                    child = TaskRecord(id='TASK-refused', parent_task_id='TASK-root',
                        brief='Synthetic held child', assigned_agent='worker', team='retiring')
                    org.db.insert_task(child)
                    observer = CollectionObserver(org=org.slug, root=org.root, db=org.db)
                    org.db._memory_collection_context = SimpleNamespace(db=org.db, memory_collection=observer)
                    supervisor = build_default_host_supervisor(backend=MacOSProcessGroupBackend())
                    original_lease, original_complete = adapters._repo_refresh_lease, adapters._repo_refresh_complete
                    released, owners = [], []

                    @contextmanager
                    def lease(*args: Any, **kwargs: Any) -> Iterator[Any]:
                        with original_lease(*args, **kwargs) as held:
                            yield held
                        released.append(args[0])
                        if len(released) % 2 == 1:
                            raise OSError('synthetic lease-release refusal')

                    def complete(preparation: Any, results: dict, work: list) -> dict:
                        org.db._task_pause_store.control('TASK-root', held=True, expected_generation=0,
                            actor='founder', sessions=org.sessions)
                        return original_complete(preparation, results, work)

                    def body(*args: Any, **kwargs: Any) -> None:
                        owner = current_invocation()
                        owners.append(owner)
                        adapters.refresh_workspace_repos(workspace, preparation=owner, host_supervisor=supervisor)
                        self.fail('the real lease error must reach the original run_step tail')

                    def tail(invocation: TaskInvocation, **kwargs: Any) -> None:
                        if fault == 'open_interval':
                            invocation._repository_effect_intervals.append(dict(
                                id=len(invocation._repository_effect_intervals), kind='native', closed=False, failed=False))
                        elif fault == 'cleanup_unavailable':
                            kwargs['cleanup'] = SimpleNamespace(kind='unknown',
                                reason='synthetic independently unavailable cleanup', processes=[])
                        return settle_live(invocation, **kwargs)

                    error = None
                    with patch.object(adapters, '_repo_refresh_lease', lease), patch.object(
                            adapters, '_repo_refresh_complete', complete), patch(
                            'runtime.orchestrator.run_step._run_step_body', body), patch(
                            'runtime.orchestrator.task_pause.settle_live', tail):
                        try:
                            run_step_impl(org.orchestrator, child.id)
                        except (OSError, RuntimeError) as exc:
                            error = exc
                    history = org.db.repository_preparation_history_uncommitted(child.id)
                    self.assertEqual(Counter(v['event']['phase'] for v in history)['closed'], 2,
                        f'Pause before closure must preserve both original finishing receipt streams: {error!r}; '
                        f'{Counter(v["event"]["phase"] for v in history)}; {owners[0].preparation_cause}')
                    self.assertIsNone(error, 'the held original lease OSError must be handled by production')
                    self.assertEqual(len(released), 2)
                    self.assertEqual(org.db.get_task(child.id).status, TaskStatus.PENDING)
                    self.assertEqual(org.db.get_task(child.id).orchestration_step_count, child.orchestration_step_count)
                    self.assertIsNone(org.db.get_task(child.id).current_session_id)
                    self.assertFalse(owners[0].launch_observed or owners[0].attempt_committed or owners[0].admitted)
                    self.assertEqual(org.db.execute("SELECT COUNT(*) FROM audit_log WHERE task_id=? "
                        "AND action='task_native_launch_boundary'", (child.id,)).fetchone()[0], 0)
                    store = org.db._task_pause_store
                    pause = store.projection('TASK-root')
                    self.assertTrue(pause['held'])
                    self.assertEqual(pause['control_state'], 'pausing' if fault else 'paused', pause)
                    self.assertEqual(pause['evidence_complete'], fault is None)
                    self.assertEqual(bool(pause['blockers']), fault is not None)
                    if fault:
                        self.assertTrue(any(b['kind'] == 'unknown' for b in pause['blockers']))
                    else:
                        with patch('runtime.orchestrator.run_step._run_step_body', body):
                            run_step_impl(org.orchestrator, child.id)
                        self.assertEqual(len(owners), 1, 'hold must veto a fresh preparation')
                        self.assertEqual(org.db.repository_preparation_history_uncommitted(child.id), history)
                        # Resume creates a fresh owner and ordinary refusal accounting.
                        store.control('TASK-root', held=False, expected_generation=1,
                            actor='founder', sessions=org.sessions)
                        with patch.object(adapters, '_repo_refresh_lease', lease), patch(
                                'runtime.orchestrator.run_step._run_step_body', body):
                            run_step_impl(org.orchestrator, child.id)
                        self.assertEqual(len(owners), 2)
                        self.assertNotEqual(owners[0].identity, owners[1].identity)
                        self.assertEqual(org.db.get_task(child.id).status, TaskStatus.FAILED)
                        self.assertEqual(org.db.execute("SELECT COUNT(*) FROM audit_log WHERE task_id=? "
                            "AND action='task_native_launch_boundary' AND json_extract(payload,'$.kind')='refused'",
                            (child.id,)).fetchone()[0], 1)

    async def test_refused_tail_retains_unknown_without_positive_closure(self) -> None:
        if not isolated(self):
            return
        from runtime.orchestrator.launch_refusals import settle_refusal
        from runtime.orchestrator.task_pause import release_live, settle_live
        for fault in ('possible_launch', 'launch_observed', 'unclosed', 'missing', 'unreadable',
                      'cleanup_live', 'cleanup_unavailable', 'running_job'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as folder:
                scratch = Path(folder)
                with git_environment(scratch):
                    generated, _, _ = stale_clone(scratch)
                    async with fixture(scratch/'org') as (org, _):
                        workspace = org.root/'workspaces'/'worker'
                        (generated/'repos').rename(workspace/'repos')
                        org.db.insert_task(TaskRecord(id='TASK-root', brief='Synthetic held parent',
                            status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
                            assigned_agent='manager', team='retiring'))
                        org.db.insert_task(TaskRecord(id='TASK-refused', parent_task_id='TASK-root',
                            brief='Synthetic refused child', assigned_agent='worker', team='retiring'))
                        observer = CollectionObserver(org=org.slug, root=org.root, db=org.db)
                        org.db._memory_collection_context = SimpleNamespace(db=org.db, memory_collection=observer)
                        owner = TaskInvocation(org.orchestrator, org.db.get_task('TASK-refused'), 'worker', 'refused-sid')
                        owner.prepare()
                        supervisor = build_default_host_supervisor(backend=MacOSProcessGroupBackend())
                        adapters.refresh_workspace_repos(workspace, preparation=owner, host_supervisor=supervisor)
                        self.assertTrue(settle_refusal(owner, OSError('synthetic refusal'), prepared=True))
                        cleanup = None
                        if fault == 'possible_launch':
                            owner.attempt_committed = owner.unsettled = True
                        elif fault == 'launch_observed':
                            owner.launch_observed = True
                        elif fault in {'unclosed', 'missing'}:
                            org.db._conn.execute("DELETE FROM audit_log WHERE task_id='TASK-refused' "
                                "AND action='repository_preparation'" +
                                (" AND json_extract(payload,'$.phase')='closed'" if fault == 'unclosed' else ''))
                            org.db._conn.commit()
                        elif fault == 'cleanup_live':
                            cleanup = SimpleNamespace(kind='unknown', reason='synthetic live cleanup', processes=['synthetic-process'])
                        elif fault == 'cleanup_unavailable':
                            cleanup = SimpleNamespace(kind='unknown', reason='synthetic unavailable cleanup', processes=[])
                        elif fault == 'running_job':
                            org.db.insert_job(JobRecord(id='JOB-synthetic', task_id='TASK-refused', agent_name='worker',
                                title='Synthetic running row', rationale='', script_text='true',
                                interpreter=JobInterpreter.BASH, status=JobStatus.RUNNING, created_at='2026-11-24T00:00:00Z'))
                        if fault == 'unreadable':
                            org.db._conn.set_authorizer(lambda action, table, *_: sqlite3.SQLITE_DENY
                                if action == sqlite3.SQLITE_READ and table == 'audit_log' else sqlite3.SQLITE_OK)
                        try:
                            # Reach the genuine tail after removal from the producer registry.
                            with patch('runtime.orchestrator.task_pause.settle_live'):
                                release_live(owner, failed=True)
                            settle_live(owner, failed=True, cleanup=cleanup)
                        finally:
                            org.db._conn.set_authorizer(None)
                        store = org.db._task_pause_store
                        store.control('TASK-root', held=True, expected_generation=0, actor='founder', sessions=org.sessions)
                        pause = store.projection('TASK-root')
                        self.assertEqual(pause['control_state'], 'pausing', pause)
                        self.assertFalse(pause['evidence_complete'])
                        self.assertTrue(any(b['kind'] == 'unknown' for b in pause['blockers']))
                        self.assertTrue(pause['held'])

    async def test_refused_lease_tail_closes_every_repository_and_pauses(self) -> None:
        if not isolated(self):
            return
        from runtime.orchestrator.run_step import run_step_impl
        from runtime.orchestrator.task_pause import current_invocation, settle_live
        with tempfile.TemporaryDirectory() as folder:
            scratch = Path(folder)
            with git_environment(scratch):
                generated, _, _ = stale_clone(scratch)
                async with fixture(scratch/'org') as (org, _):
                    workspace = org.root/'workspaces'/'worker'
                    (generated/'repos').rename(workspace/'repos')
                    git(workspace, 'clone', str(scratch/'origin.git'), str(workspace/'repos'/'second'))
                    org.db.insert_task(TaskRecord(id='TASK-root', brief='Synthetic parent',
                        status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
                        assigned_agent='manager', team='retiring'))
                    org.db.insert_task(TaskRecord(id='TASK-refused', parent_task_id='TASK-root',
                        brief='Synthetic refusal', assigned_agent='worker', team='retiring'))
                    observer = CollectionObserver(org=org.slug, root=org.root, db=org.db)
                    org.db._memory_collection_context = SimpleNamespace(db=org.db, memory_collection=observer)
                    supervisor = build_default_host_supervisor(backend=MacOSProcessGroupBackend())
                    original_lease = adapters._repo_refresh_lease
                    released = []
                    owners = []

                    @contextmanager
                    def lease(*args: Any, **kwargs: Any) -> Iterator[Any]:
                        with original_lease(*args, **kwargs) as held:
                            yield held
                        released.append(args[0])
                        if len(released) == 1:
                            raise OSError('synthetic lease-release refusal')

                    def body(*args: Any, **kwargs: Any) -> None:
                        owner = current_invocation()
                        owners.append(owner)
                        adapters.refresh_workspace_repos(workspace, preparation=owner, host_supervisor=supervisor)
                        self.fail('lease refusal must reach the native refusal boundary')

                    def held_tail(invocation: TaskInvocation, **kwargs: Any) -> None:
                        org.db._task_pause_store.control('TASK-root', held=True, expected_generation=0,
                            actor='founder', sessions=org.sessions)
                        return settle_live(invocation, **kwargs)

                    with patch.object(adapters, '_repo_refresh_lease', lease), patch(
                            'runtime.orchestrator.run_step._run_step_body', body), patch(
                            'runtime.orchestrator.task_pause.settle_live', held_tail):
                        run_step_impl(org.orchestrator, 'TASK-refused')
                    owner = owners[0]
                    self.assertEqual(org.db.get_task('TASK-refused').status, TaskStatus.FAILED)
                    self.assertFalse(owner.launch_observed)
                    self.assertEqual(len(released), 2, 'one lease failure must not abandon later original tails')
                    history = org.db.repository_preparation_history_uncommitted('TASK-refused')
                    self.assertEqual(Counter(v['event']['phase'] for v in history)['closed'], 2)
                    self.assertTrue(all(owner.move_reservation.repository_obligations.values()))
                    store = org.db._task_pause_store
                    pause = store.projection('TASK-root')
                    self.assertEqual(pause['control_state'], 'paused', pause)
                    self.assertTrue(pause['evidence_complete'])
                    self.assertEqual(pause['blockers'], [])
                    self.assertTrue(pause['held'])
                    self.assertIsNone(org.db.get_task('TASK-refused').current_session_id)

    async def refresh(self, case: str, *, control: dict | None = None,
                      recursive: bool = False) -> dict:
        with tempfile.TemporaryDirectory() as folder:
            scratch = Path(folder)
            with git_environment(scratch):
                generated, old, target = stale_clone(scratch)
                async with fixture(scratch/'org') as (org, _):
                    workspace = org.root/'workspaces'/'worker'
                    (generated/'repos').rename(workspace/'repos')
                    clone = workspace/'repos'/'sample'
                    if case in {'equal', 'legacy_equal'}:
                        git(clone, 'fetch', 'origin')
                        git(clone, 'merge', '--ff-only', target)
                    elif case == 'diverged':
                        git(clone, 'config', 'user.name', 'Synthetic Refresh')
                        git(clone, 'config', 'user.email', 'refresh@example.invalid')
                        (clone/'local').write_text('local\n')
                        git(clone, 'add', 'local')
                        git(clone, 'commit', '-m', 'local')
                    if recursive:
                        git(clone, 'config', 'submodule.recurse', 'true')
                    if case == 'legacy_equal':
                        git(clone, 'config', '--unset-all', 'remote.origin.url')
                        (clone/'.git'/'remotes').mkdir()
                        (clone/'.git'/'remotes'/'origin').write_text('URL: '+str(scratch/'origin.git')+'\n')
                        git(scratch, 'clone', '--bare', str(scratch/'origin.git'), str(scratch/'other.git'))
                    if control and control['action'] == 'branch':
                        git(clone, 'update-ref', 'refs/heads/other', target)
                    before = git(clone, 'rev-parse', 'HEAD')
                    binary = scratch/'bin'
                    binary.mkdir()
                    settings = dict(control or {}, old=old, other=str(scratch/'other.git'))
                    if 'trigger' in settings:
                        settings['trigger'] = [target if part == 'TARGET' else part for part in settings['trigger']]
                    (binary/'control').write_text(json.dumps(settings))
                    (binary/'git').write_text(WRAPPER.format(python=sys.executable))
                    (binary/'git').chmod(0o700)
                    os.environ['PATH'] = str(binary)+os.pathsep+os.environ['PATH']
                    org.db.insert_task(TaskRecord(id='TASK-refresh', brief='Synthetic refresh',
                        assigned_agent='worker', team='retiring'))
                    observer = CollectionObserver(org=org.slug, root=org.root, db=org.db)
                    org.db._memory_collection_context = SimpleNamespace(db=org.db, memory_collection=observer)
                    owner = TaskInvocation(org.orchestrator, org.db.get_task('TASK-refresh'), 'worker', 'refresh-sid')
                    owner.prepare()
                    receipts = []
                    supervisor = build_default_host_supervisor(backend=MacOSProcessGroupBackend(), publisher=receipts.append)
                    result = adapters.refresh_workspace_repos(workspace, preparation=owner, host_supervisor=supervisor)
                    calls = [json.loads(line) for line in (binary/'calls').read_text().splitlines()]
                    history = org.db.repository_preparation_history_uncommitted('TASK-refresh')
                    self.assertEqual(len(receipts), len(calls))
                    self.assertTrue(all(r.quiescent is True for r in receipts))
                    self.assertTrue(owner._repository_preparations)
                    self.assertTrue(all(e.closed and not e.uncertain for e in owner._repository_preparations.values()))
                    self.assertEqual(Counter(r['event']['phase'] for r in history),
                        Counter(dict.fromkeys(('intent', 'attempted', 'started', 'consumed', 'finished'), len(calls)))
                        + Counter(dict.fromkeys(('opened', 'worker_returned', 'outer_closed', 'closed'), 1)))
                    return dict(result=result, calls=[[v for v in a if v not in
                        ('--no-optional-locks', '--no-lazy-fetch')] for a in calls],
                        before=before, after=git(clone, 'rev-parse', 'HEAD'), target=target,
                        fired=(binary/'fired').read_text() if (binary/'fired').exists() else None)

    async def test_forward_call_budget(self) -> None:
        if not isolated(self):
            return
        row = await self.refresh('forward')
        self.assertEqual(row['result'], {'sample': True})
        self.assertEqual(row['after'], row['target'])
        self.assertEqual(len(row['calls']), 29)

    async def test_diverged_call_budget(self) -> None:
        if not isolated(self):
            return
        row = await self.refresh('diverged')
        self.assertEqual(row['result'], {'sample': False})
        self.assertEqual(row['after'], row['before'])
        self.assertEqual(len(row['calls']), 21)

    async def test_equal_target_skips_merge_and_submodule(self) -> None:
        if not isolated(self):
            return
        row = await self.refresh('equal', recursive=True)
        self.assertEqual(row['result'], {'sample': True})
        self.assertEqual(row['before'], row['after'])
        self.assertEqual(row['after'], row['target'])
        self.assertFalse(any(a[0] in {'merge', 'merge-base', 'submodule'} for a in row['calls']))
        self.assertEqual(len(row['calls']), 20)

    async def check_equal_window(self, action: str, trigger: list[str], nth: int = 1) -> None:
        row = await self.refresh('equal', control=dict(action=action, trigger=trigger, nth=nth))
        self.assertEqual(row['fired'], action)
        self.assertEqual(row['result'], {'sample': False})
        self.assertFalse(any(a[0] == 'merge' for a in row['calls']))

    async def test_equal_target_config_window(self) -> None:
        if not isolated(self):
            return
        await self.check_equal_window('config', ['cat-file', '-t', 'TARGET'])

    async def test_equal_target_head_window(self) -> None:
        if not isolated(self):
            return
        await self.check_equal_window('head', ['remote', 'get-url', '--all', 'origin'], 3)

    async def test_equal_target_branch_window(self) -> None:
        if not isolated(self):
            return
        await self.check_equal_window('branch', ['remote', 'get-url', '--all', 'origin'], 3)

    async def test_legacy_transport_change_is_detected(self) -> None:
        if not isolated(self):
            return
        row = await self.refresh('legacy_equal', control=dict(action='transport',
            trigger=['cat-file', '-t', 'TARGET']))
        self.assertEqual(row['fired'], 'transport')
        self.assertEqual(row['result'], {'sample': False})
        self.assertEqual(row['after'], row['before'])
        self.assertFalse(any(a[0] == 'merge' for a in row['calls']))


class IdentityTests(unittest.TestCase):
    def observe(self, repo: Path, arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        self.calls.append(arguments)
        result = subprocess.run(['git', '--no-lazy-fetch', *arguments], cwd=repo,
            capture_output=True, timeout=5)
        if result.returncode != 0:
            raise subprocess.SubprocessError('repository_refresh_probe_unavailable')
        return result

    def identity(self, clone: Path) -> adapters._RepoRefreshIdentity:
        self.calls = []
        with patch.object(adapters, '_repo_refresh_observe', self.observe):
            return adapters._repo_refresh_identity(clone, deadline=time.monotonic()+10, cancel_requested=None)

    def test_identity_fields_and_call_budget(self) -> None:
        for object_format in ('sha1', 'sha256'):
            with self.subTest(object_format=object_format), tempfile.TemporaryDirectory() as folder:
                scratch = Path(folder)
                with git_environment(scratch):
                    os.environ['GIT_DEFAULT_HASH'] = object_format
                    workspace, old, _ = stale_clone(scratch)
                    clone = workspace/'repos'/'sample'
                    git(clone, 'branch', '-m', 'feature/refresh')
                    git(clone, 'config', 'branch.feature/refresh.merge', 'refs/heads/feature/refresh')
                    git(clone, 'update-ref', 'refs/remotes/origin/feature/refresh', old)
                    identity = self.identity(clone)
                    self.assertEqual(identity.branch_ref, 'refs/heads/feature/refresh')
                    self.assertEqual(identity.head, old)
                    self.assertEqual(identity.upstream, 'refs/remotes/origin/feature/refresh')
                    self.assertEqual(identity.object_format, object_format)
                    self.assertEqual(identity.config, subprocess.check_output(['git', 'config', '--includes', '--null',
                        '--show-scope', '--show-origin', '--get-regexp', adapters._repo_refresh_config_selector('feature/refresh')], cwd=clone))
                    self.assertEqual(identity.transport, subprocess.check_output(['git', 'remote', 'get-url', '--all', 'origin'], cwd=clone))
                    self.assertEqual(len(self.calls), 3)

    def test_identity_rejects_unusable_controls(self) -> None:
        for case in ('detached', 'unborn', 'missing_upstream', 'blob_head', 'blob_upstream', 'tag_head', 'wrong_mapping'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as folder:
                scratch = Path(folder)
                with git_environment(scratch):
                    workspace, old, _ = stale_clone(scratch)
                    clone = workspace/'repos'/'sample'
                    if case == 'detached':
                        git(clone, 'checkout', '--detach')
                    elif case == 'unborn':
                        git(clone, 'symbolic-ref', 'HEAD', 'refs/heads/unborn')
                    elif case == 'missing_upstream':
                        git(clone, 'update-ref', '-d', 'refs/remotes/origin/main')
                    elif case.startswith('blob_'):
                        blob = git(clone, 'rev-parse', 'HEAD:value.txt')
                        ref = 'refs/heads/main' if case == 'blob_head' else 'refs/remotes/origin/main'
                        # Git correctly disallows installing noncommits in heads;
                        # model on-disk corruption without a product test seam.
                        (clone/'.git'/ref).write_text(blob+'\n')
                    elif case == 'tag_head':
                        git(clone, '-c', 'user.name=Synthetic', '-c', 'user.email=refresh@example.invalid',
                            'tag', '-a', 'synthetic', '-m', 'tag', old)
                        (clone/'.git'/'refs/heads/main').write_text(git(clone, 'rev-parse', 'refs/tags/synthetic')+'\n')
                    elif case == 'wrong_mapping':
                        git(clone, 'config', '--add', 'remote.origin.fetch', '+refs/tags/*:refs/tags/*')
                    error, message = ((ValueError, 'repository_refresh_commit_unavailable')
                        if case in {'blob_head', 'tag_head'} else
                        (subprocess.SubprocessError, 'unsupported_refresh_configuration')
                        if case == 'wrong_mapping' else
                        (subprocess.SubprocessError, 'repository_refresh_probe_unavailable'))
                    with self.assertRaisesRegex(error, '^'+message+'$'):
                        self.identity(clone)

    def test_identity_preserves_type_only_head_admission(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            scratch = Path(folder)
            with git_environment(scratch):
                workspace, _, _ = stale_clone(scratch)
                clone = workspace/'repos'/'sample'
                # The old admission checks HEAD's object type, not commit-body
                # validity. Do not turn a later Git failure into a no-effect skip.
                oid = subprocess.check_output(['git', 'hash-object', '--literally',
                    '-t', 'commit', '-w', '--stdin'], input=b'synthetic malformed commit\n', cwd=clone).decode().strip()
                (clone/'.git'/'refs/heads/main').write_text(oid+'\n')
                identity = self.identity(clone)
                self.assertEqual(identity.head, oid)
                self.assertEqual(identity.branch_ref, 'refs/heads/main')
                self.assertEqual(identity.upstream, 'refs/remotes/origin/main')

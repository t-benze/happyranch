"""Focused real-Git refresh checks, outside retired units and integration."""
from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.test_agent_retirement import fixture
from scripts.test_preparation_startup import isolated
from scripts.mac_native.suite_repo_refresh import git, git_environment, stale_clone
from runtime.infrastructure.memory_collection import CollectionObserver
from runtime.models import TaskRecord
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

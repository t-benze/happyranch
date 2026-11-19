"""Focused A4 refusal/startup checks; see docs/operations/local-ci.md."""
from __future__ import annotations

import asyncio
import json
import os
import stat
import tempfile
import sqlite3
import subprocess
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from runtime.config import Settings
from runtime.daemon.org_state import OrgState
from runtime.daemon.routes.orgs import _seed_skeleton
from runtime.daemon.state import DaemonState
from runtime.daemon.workflow_setup_status import read_setup_status
from runtime.infrastructure import workflow_sequential_bootstrap as bootstrap
from runtime.infrastructure.database import Database
from runtime.infrastructure.org_access_fence import OrgAccessFence
from runtime.infrastructure.producer_admission import NativeWriterLifecycle, NativeWriterStore
from runtime.infrastructure.task_pause_controls import install_pause_schema
from runtime.infrastructure.workflow_schema import initialize_complete_org_schema
from runtime.infrastructure.workflow_task_ownership import install_task_ownership
from runtime.models import TaskRecord
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.org_config import initialize_fresh_reviewer_agents
from runtime.platform.passthrough_backend import PassthroughBackend
from runtime.runtime import RuntimeDir


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RuntimeDir:
    home = tmp_path / 'home'
    home.mkdir(mode=0o700)
    monkeypatch.setenv('HAPPYRANCH_DAEMON_HOME', str(home))
    monkeypatch.setattr('runtime.platform.backend_factory.select_session_backend', PassthroughBackend)
    runtime = RuntimeDir.init(home / 'runtime')
    root = runtime.orgs_dir / 'scratch'
    _seed_skeleton(root, from_example=None)
    db = Database(root / 'happyranch.db')
    try:
        initialize_complete_org_schema(db, expected_org_slug='scratch', layout='J')
        install_pause_schema(db, org_slug='scratch')
        install_task_ownership(db, org_slug='scratch')
        initialize_fresh_reviewer_agents(OrgPaths(root), db)
        db.insert_task(TaskRecord(id='TASK-retained', brief='synthetic pending history'))
    finally:
        db.close()
    org = OrgState.load(slug='scratch', root=root, settings=Settings())
    org.close()
    return runtime


@pytest.mark.parametrize('prior_hold', [False, True], ids=['first-refusal', 'existing-hold'])
def test_no_effect_refusal_loads_and_latched_boot_does_not_retry(
    runtime: RuntimeDir, caplog: pytest.LogCaptureFixture, prior_hold: bool,
) -> None:
    root = runtime.orgs_dir / 'scratch'
    home = runtime.root.parent
    hold = None
    # Publication/profile custody still refuses; pending tasks carry forward.
    with sqlite3.connect(root / 'happyranch.db') as conn:
        conn.execute('INSERT INTO workflow_profile_leases VALUES (?,?,?)',
                     ('synthetic-unused-profile', 'synthetic-owner', 2147483647))
    if prior_hold:
        seed = bootstrap.BootstrapStartup(home=home, runtime_root=runtime.root)
        seed._hold('scratch', 'history_check_failed', 'not_installed')
        for record in reversed(seed.hold_descriptors):
            if not record['attempted']:
                bootstrap._close_descriptor(record)
    for boot in range(2):
        state = DaemonState.from_runtime(runtime, Settings(), automatic_workflow_setup=True)
        try:
            assert set(state.orgs) == {'scratch'}, state.broken_orgs
            assert not state.broken_orgs
            owner = state._workflow_bootstrap
            assert owner.hold_states == {'scratch': 'not_installed'}
            assert len(owner.workers) == (1 if boot == 0 and not prior_hold else 0)
            discovery = owner.discoveries[root]
            assert discovery.closed and discovery.fences[-1]['transferred']
            if boot or prior_hold:
                assert discovery.result == 'held' and not discovery.selections[0].connections
            fence = state.orgs['scratch'].db._org_access_fence
            assert not fence.exclusive and fence is discovery.fences[-1]['fence']
            assert all(record['closed'] for record in discovery.fences[:-1])
            assert not state.orgs['scratch'].db._conn.execute("SELECT 1 FROM sqlite_schema WHERE name GLOB 'workflow_seq_*'").fetchone()
            status = read_setup_status(home, runtime.root, 'scratch')
            assert (status.state, status.reason) == ('not_installed', 'history_check_failed')
            latch = next((home / 'backups/workflow-sequential').rglob('hold.json'))
            if hold is None:
                hold = latch.read_bytes()
            assert latch.read_bytes() == hold
            assert not list(latch.parent.glob('*/before.db'))
            assert state.orgs['scratch'].db.get_task('TASK-retained').brief == 'synthetic pending history'
        finally:
            asyncio.run(state.close_all())
            state.metrics_store.close()
    assert 'workflow setup: not_installed: history_check_failed' in caplog.text


def test_unknown_latch_stays_broken_without_retry(runtime: RuntimeDir) -> None:
    home = runtime.root.parent
    seed = bootstrap.BootstrapStartup(home=home, runtime_root=runtime.root)
    seed._hold('scratch', 'commit_uncertain', 'unknown')
    for record in reversed(seed.hold_descriptors):
        if not record['attempted']:
            bootstrap._close_descriptor(record)
    latch = next((home / 'backups/workflow-sequential').rglob('hold.json'))
    before = latch.read_bytes()
    state = DaemonState.from_runtime(runtime, Settings(), automatic_workflow_setup=True)
    try:
        assert not state.orgs and 'scratch' in state.broken_orgs
        assert state._workflow_bootstrap.hold_states == {'scratch': 'unknown'}
        assert not state._workflow_bootstrap.workers
        assert latch.read_bytes() == before
        assert read_setup_status(home, runtime.root, 'scratch').state == 'unknown'
    finally:
        asyncio.run(state.close_all())
        state.metrics_store.close()


@pytest.mark.parametrize('publisher,agents,teams,terminal,expected', [
    ('disk_sweep', (), (), 'unknown', None),
    ('disk_sweep', (), (), 'active', 'workflow_setup_history_not_idle'),
    ('other', (), (), 'unknown', 'workflow_setup_history_not_idle'),
    ('disk_sweep', ('worker',), (), 'unknown', 'workflow_setup_history_not_idle'),
    ('disk_sweep', (), ('engineering',), 'unknown', 'workflow_setup_history_not_idle'),
])
def test_writer_exemption_preserves_unknown_and_later_gate(
    runtime: RuntimeDir, publisher: str, agents: tuple[str, ...], teams: tuple[str, ...], terminal: str, expected: str | None,
) -> None:
    path = runtime.orgs_dir / 'scratch/happyranch.db'
    db = Database(path)
    try:
        store = NativeWriterStore(db, 'scratch')
        row = NativeWriterLifecycle(org_slug='scratch', writer_id='retained-sweep', publisher=publisher,
                                    agents=agents, teams=teams)
        store.save_uncommitted(row)
        if terminal != 'active':
            store.save_uncommitted(row.model_copy(update={'state': terminal}))
        db._conn.commit()
    finally:
        db.close()
    before = path.read_bytes()
    fence = OrgAccessFence.acquire(path.parent, exclusive=True)
    source = bootstrap.BootstrapSource(fence=fence, deadline=time.monotonic()+119)
    try:
        if expected is None:
            source.open()
        else:
            with pytest.raises(ValueError, match=expected):
                source.open()
    finally:
        source.close()
        fence.close()
    assert path.read_bytes() == before
    with sqlite3.connect(path) as conn:
        payload = conn.execute("SELECT payload FROM audit_log WHERE action='team_move_native_writer' ORDER BY id DESC LIMIT 1").fetchone()[0]
        assert json.loads(payload)['state'] == terminal


@pytest.mark.parametrize('writers', [0, 1800], ids=['pending-source', 'large-writer-census'])
def test_historical_sources_install_without_adoption_and_next_boot_noop(runtime: RuntimeDir, writers: int) -> None:
    root = runtime.orgs_dir / 'scratch'
    with sqlite3.connect(root / 'happyranch.db') as conn:
        for number in range(writers):
            row = NativeWriterLifecycle(org_slug='scratch', writer_id=f'synthetic-writer-{number}', publisher='synthetic')
            for state in ('active', 'settled'):
                conn.execute('INSERT INTO audit_log(timestamp,task_id,agent,action,payload) VALUES(?,?,?,?,?)',
                    ('2026-11-17T00:00:00+00:00', 'config:team-move-writer:'+row.writer_id, 'native_writer',
                     'team_move_native_writer', row.model_copy(update={'state': state}).model_dump_json()))
        before = tuple(conn.execute('SELECT rowid,* FROM workflow_task_ownership'))
        tasks = tuple(conn.execute('SELECT rowid,* FROM tasks'))
    for boot in range(2):
        state = DaemonState.from_runtime(runtime, Settings(), automatic_workflow_setup=True)
        try:
            assert set(state.orgs) == {'scratch'}, state.broken_orgs
            assert not state.broken_orgs
            conn = state.orgs['scratch'].db._conn
            assert tuple(tuple(row) for row in conn.execute('SELECT rowid,* FROM workflow_task_ownership')) == before
            assert tuple(tuple(row) for row in conn.execute('SELECT rowid,* FROM tasks')) == tasks
            status = read_setup_status(runtime.root.parent, runtime.root, 'scratch')
            assert (status.state, status.reason) == ('installed_verified', 'registered')
            assert conn.execute('SELECT count(*) FROM workflow_seq_runs').fetchone()[0] == 0
            assert conn.execute('SELECT count(*) FROM workflow_seq_settings').fetchone()[0] == 0
            assert conn.execute('SELECT count(*) FROM workflow_seq_definitions').fetchone()[0] == 1
            assert len(state._workflow_bootstrap.workers) == (1 if boot == 0 else 0)
        finally:
            asyncio.run(state.close_all())
            state.metrics_store.close()


@pytest.mark.parametrize('task_status,installed', [
    ('completed', True), ('failed', True), ('cancelled', True), ('superseded', True),
    ('pending', False), ('in_progress', False), ('escalated', False),
])
def test_native_recovery_history_install_or_fallback(
    runtime: RuntimeDir, task_status: str, installed: bool,
) -> None:
    root = runtime.orgs_dir / 'scratch'
    with sqlite3.connect(root / 'happyranch.db') as conn:
        conn.execute('UPDATE tasks SET status=? WHERE id=?', (task_status, 'TASK-retained'))
        for recovery_state in ('claimed', 'callback_accepted'):
            conn.execute('INSERT INTO task_completion_recoveries '
                '(task_id,agent,origin_session_id,recovery_session_id,provider_session_id,claimed_at,expires_at,state) '
                'VALUES (?,?,?,?,?,?,?,?)',
                ('TASK-retained', 'synthetic-agent', 'origin-'+recovery_state, 'recovery-'+recovery_state,
                 'synthetic-provider', '2026-09-01T00:00:00Z', '2026-09-01T00:02:00Z', recovery_state))
        before = tuple(conn.execute('SELECT rowid,* FROM task_completion_recoveries ORDER BY id'))
    for boot in range(2):
        state = DaemonState.from_runtime(runtime, Settings(), automatic_workflow_setup=True)
        try:
            assert set(state.orgs) == {'scratch'} and not state.broken_orgs
            conn = state.orgs['scratch'].db._conn
            assert tuple(tuple(row) for row in conn.execute(
                'SELECT rowid,* FROM task_completion_recoveries ORDER BY id')) == before
            status = read_setup_status(runtime.root.parent, runtime.root, 'scratch')
            expected = ('installed_verified', 'registered') if installed else ('not_installed', 'history_check_failed')
            assert (status.state, status.reason) == expected
            assert len(state._workflow_bootstrap.workers) == (1 if boot == 0 else 0)
            if installed:
                assert conn.execute('SELECT count(*) FROM workflow_seq_runs').fetchone()[0] == 0
                assert conn.execute('SELECT count(*) FROM workflow_seq_settings').fetchone()[0] == 0
            else:
                assert not conn.execute("SELECT 1 FROM sqlite_schema WHERE name='workflow_seq_runs'").fetchone()
        finally:
            asyncio.run(state.close_all())
            state.metrics_store.close()


def test_live_producer_refuses_install_with_fallback(runtime: RuntimeDir) -> None:
    root = runtime.orgs_dir / 'scratch'
    (root / 'org/teams.yaml').write_text(
        'teams:\n  engineering:\n    manager: {kind: human, principal: founder}\n    workers: [synthetic_agent]\n')
    (root / 'org/agents/synthetic_agent.md').write_text(
        '---\nname: synthetic_agent\nteam: engineering\nrole: worker\nexecutor: codex\n---\nSynthetic agent.\n')
    child = subprocess.Popen([sys.executable, '-c', """
from datetime import datetime, timezone
from pathlib import Path
import sys
from unittest.mock import patch
from runtime.config import Settings
from runtime.daemon.org_state import OrgState
from runtime.models import DreamRecord
from runtime.platform.passthrough_backend import PassthroughBackend
with patch('runtime.platform.backend_factory.select_session_backend', PassthroughBackend):
    live = OrgState.load(slug='scratch', root=Path(sys.argv[1]), settings=Settings())
preparation = None
try:
    now = datetime.now(timezone.utc)
    live.db.insert_dream(DreamRecord(id='DREAM-live', agent_name='synthetic_agent', local_date='2026-11-18',
                                    scheduled_for=now, window_end=now))
    admission = live.orchestrator._team_moves.producers
    preparation = admission.reserve('dream', 'DREAM-live')
    assert preparation is not None and admission.live[('dream', 'DREAM-live')] is preparation
    print('live producer ready', flush=True)
    sys.stdin.read(1)
    assert admission.live[('dream', 'DREAM-live')] is preparation and preparation.owner_live
finally:
    if preparation is not None:
        preparation.finish()
    live.close()
""", str(root)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'live producer ready'
        assert child.poll() is None
        state = DaemonState.from_runtime(runtime, Settings(), automatic_workflow_setup=True)
        try:
            assert set(state.orgs) == {'scratch'} and not state.broken_orgs
            status = read_setup_status(runtime.root.parent, runtime.root, 'scratch')
            assert (status.state, status.reason) == ('not_installed', 'writer_exclusion_unproven')
            assert not state._workflow_bootstrap.workers
            assert not state.orgs['scratch'].db._conn.execute(
                "SELECT 1 FROM sqlite_schema WHERE name='workflow_seq_runs'").fetchone()
            assert child.poll() is None
        finally:
            asyncio.run(state.close_all())
            state.metrics_store.close()
    finally:
        _, stderr = child.communicate('x', timeout=10)
        assert child.returncode == 0, stderr


class WorkflowPathDiagnostics(unittest.TestCase):
    """Real namespace refusal/log boundary; no database install or serving daemon."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix='workflow-path-diagnostics-')
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name).resolve()
        self.runtime = self.home / 'runtime'
        root = self.runtime / 'orgs/scratch'
        root.mkdir(parents=True)
        database = root / 'happyranch.db'
        database.touch(mode=0o600)
        environment = patch.dict(os.environ, HAPPYRANCH_DAEMON_HOME=str(self.home))
        environment.start()
        self.addCleanup(environment.stop)
        self.fence = OrgAccessFence.acquire(root, exclusive=True)
        self.addCleanup(self.fence.close)
        info = database.stat()
        self.attempt = bootstrap.BootstrapAttempt(home=self.home, runtime_root=self.runtime,
            slug='scratch', fence=self.fence,
            source_identity=(str(database), info.st_dev, info.st_ino, info.st_size),
            source_inventory_digest='0' * 64, deadline=time.monotonic()+119)
        self.addCleanup(self.attempt.close)

    def refusal(self, path: Path, check: str, error: type[Exception], reason: str = '') -> str:
        with self.assertLogs(bootstrap.__name__, level='WARNING') as captured:
            with self.assertRaises(error) as raised:
                self.attempt.begin()
        if reason:
            self.assertEqual(str(raised.exception), reason)
        message = '\n'.join(captured.output)
        self.assertIn(repr(str(path)), message)
        self.assertIn('check=' + check, message)
        self.assertIn('exception=' + type(raised.exception).__name__, message)
        self.assertTrue(self.attempt.failed)
        self.assertIsNone(self.attempt._phase)
        return message

    def test_wrong_mode_is_logged_and_never_repaired(self) -> None:
        path = self.home / 'backups'
        path.mkdir(mode=0o755)
        message = self.refusal(path, 'mode', bootstrap.BootstrapEvidenceError,
                               'workflow_setup_namespace_unsafe')
        self.assertIn('actual=0755 expected=0700', message)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o755)
        self.assertEqual(list(path.iterdir()), [])

    def test_wrong_owner_is_logged_without_chown(self) -> None:
        path = self.home / 'backups'
        path.mkdir(mode=0o700)
        original = os.fstat
        identity = path.stat().st_dev, path.stat().st_ino
        # No privileged chown: replace only the observed backup inode's UID.
        def owner(fd):
            info = original(fd)
            if (info.st_dev, info.st_ino) == identity:
                return SimpleNamespace(st_mode=info.st_mode, st_uid=os.getuid()+1)
            return info
        with patch.object(bootstrap.os, 'fstat', side_effect=owner):
            message = self.refusal(path, 'owner', bootstrap.BootstrapEvidenceError,
                                   'workflow_setup_namespace_unsafe')
        self.assertIn(f'actual_uid={os.getuid()+1} expected_uid={os.getuid()}', message)
        self.assertEqual(path.stat().st_uid, os.getuid())

    def test_symlink_is_logged_without_following(self) -> None:
        path = self.home / 'backups'
        target = self.home / 'untouched'
        target.mkdir(mode=0o700)
        path.symlink_to(target, target_is_directory=True)
        message = self.refusal(path, 'symlink', OSError)
        self.assertIn('expected=directory', message)
        self.assertTrue(path.is_symlink())
        self.assertEqual(list(target.iterdir()), [])

    def test_wrong_type_is_logged_and_preserved(self) -> None:
        path = self.home / 'backups'
        path.write_text('unchanged')
        message = self.refusal(path, 'type', OSError)
        self.assertIn('expected=directory', message)
        self.assertIn('actual_type=0o100000', message)
        self.assertIn(f'actual_uid={os.getuid()} expected_uid={os.getuid()}', message)
        self.assertIn('expected=0700', message)
        self.assertEqual(path.read_text(), 'unchanged')

    def test_inaccessible_mode_is_observed_without_repair(self) -> None:
        path = self.home / 'backups'
        path.mkdir(mode=0o700)
        self.addCleanup(path.chmod, 0o700)
        path.chmod(0)
        message = self.refusal(path, 'open', PermissionError)
        self.assertIn('actual=0000 expected=0700', message)
        self.assertIn(f'actual_uid={os.getuid()} expected_uid={os.getuid()}', message)
        self.assertIn('actual_type=0o40000 expected=directory', message)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0)
        print(message)

    def test_unavailable_observation_keeps_original_open_error(self) -> None:
        path = self.home / 'backups'
        original_open, original_stat = os.open, os.stat
        error = PermissionError(13, 'synthetic open denied', 'backups')
        def denied_open(name, flags, *args, **kwargs):
            if name == 'backups':
                raise error
            return original_open(name, flags, *args, **kwargs)
        def denied_stat(name, *args, **kwargs):
            if name == 'backups' and kwargs.get('follow_symlinks') is False:
                raise PermissionError(13, 'synthetic stat denied', 'backups')
            return original_stat(name, *args, **kwargs)
        with patch.object(bootstrap.os, 'open', side_effect=denied_open), \
                patch.object(bootstrap.os, 'stat', side_effect=denied_stat):
            message = self.refusal(path, 'open', PermissionError)
        self.assertIn('actual=unavailable', message)
        self.assertIn('actual_uid=unavailable', message)
        self.assertIn('actual_type=unavailable', message)
        self.assertIn('expected=0700', message)
        self.assertIn('synthetic open denied', message)

    def test_ordinary_ancestor_has_no_private_expectations(self) -> None:
        self.home.chmod(0o755)
        original = os.open
        def denied(name, flags, *args, **kwargs):
            if name == self.home.name:
                raise PermissionError(13, 'synthetic ancestor denied', name)
            return original(name, flags, *args, **kwargs)
        with patch.object(bootstrap.os, 'open', side_effect=denied), \
                self.assertLogs(bootstrap.__name__, level='WARNING') as captured:
            with self.assertRaises(PermissionError):
                bootstrap._directory(self.home, self.attempt._descriptors)
        message = '\n'.join(captured.output)
        self.assertIn(repr(str(self.home)), message)
        self.assertIn('actual=0755', message)
        self.assertIn('actual_type=0o40000 expected=directory', message)
        self.assertNotIn('expected=0700', message)
        self.assertNotIn('expected_uid=', message)
        self.assertEqual(stat.S_IMODE(self.home.stat().st_mode), 0o755)

    def test_missing_path_logs_original_exception_and_keeps_refusal(self) -> None:
        path = self.home / 'backups'
        original = os.open
        def missing(name, flags, *args, **kwargs):
            if name == 'backups':
                raise FileNotFoundError(2, 'No such file or directory', name)
            return original(name, flags, *args, **kwargs)
        with patch.object(bootstrap.os, 'open', side_effect=missing):
            message = self.refusal(path, 'missing', FileNotFoundError)
        self.assertIn('No such file or directory', message)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)


@pytest.mark.parametrize('publisher,stage', [
    ('attempt', 'namespace'), ('attempt', 'attempt'), ('hold', 'namespace'),
])
@pytest.mark.parametrize('operation', ['mkdir', 'fsync'])
def test_creation_failure_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    publisher: str, stage: str, operation: str,
) -> None:
    home = tmp_path / 'home'
    home.mkdir(mode=0o700)
    monkeypatch.setenv('HAPPYRANCH_DAEMON_HOME', str(home))
    runtime = home / 'runtime'
    root = runtime / 'orgs/scratch'
    root.mkdir(parents=True)
    database = root / 'happyranch.db'
    database.touch(mode=0o600)
    fence = OrgAccessFence.acquire(root, exclusive=True)
    info = database.stat()
    attempt = bootstrap.BootstrapAttempt(home=home, runtime_root=runtime,
        slug='scratch', fence=fence,
        source_identity=(str(database), info.st_dev, info.st_ino, info.st_size),
        source_inventory_digest='0' * 64, deadline=time.monotonic()+119)
    startup = bootstrap.BootstrapStartup(home=home, runtime_root=runtime)
    target = home / 'backups' if stage == 'namespace' else attempt.root
    error = PermissionError(13, 'synthetic creation denied', str(target))
    mkdir, fsync = os.mkdir, os.fsync
    calls = []
    syncing = None
    def create(name, mode=0o777, *, dir_fd=None):
        nonlocal syncing
        calls.append(('mkdir', name))
        if name == target.name and operation == 'mkdir':
            raise error
        result = mkdir(name, mode, dir_fd=dir_fd)
        syncing = name
        return result
    def sync(fd):
        calls.append(('fsync', syncing))
        if syncing == target.name and operation == 'fsync':
            raise error
        return fsync(fd)
    monkeypatch.setattr(bootstrap.os, 'mkdir', create)
    monkeypatch.setattr(bootstrap.os, 'fsync', sync)
    try:
        with caplog.at_level('WARNING', logger=bootstrap.__name__):
            with pytest.raises(PermissionError) as raised:
                if publisher == 'attempt':
                    attempt.begin()
                else:
                    startup._hold('scratch', 'install_incomplete', 'unknown')
        assert raised.value is error
        assert calls[-1] == (operation, target.name)
        message = caplog.text
        assert f'path={str(target)!r} check={operation}' in message
        assert f'parent={str(target.parent)!r}' in message
        assert 'parent_actual=0700' in message
        assert f'expected_uid={os.getuid()}' in message
        assert 'expected=0700' in message
        assert 'expected=directory' in message
        assert 'exception=PermissionError' in message
        assert 'synthetic creation denied' in message
        assert ('actual=0700' if operation == 'fsync' else 'actual=unavailable') in message
        assert target.exists() == (operation == 'fsync')
        assert attempt._phase is None
        assert not list(home.rglob('*.json'))
        assert stat.S_IMODE(home.stat().st_mode) == 0o700
        if publisher == 'attempt':
            assert attempt.failed
        else:
            assert startup.holds == {'scratch': 'install_incomplete'}
        print(message)
    finally:
        for record in reversed(startup.hold_descriptors):
            if not record['attempted']:
                bootstrap._close_descriptor(record)
        attempt.close()
        fence.close()


def test_worker_import_failure_is_sanitized(
    runtime: RuntimeDir, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture,
) -> None:
    """A config change before the real helper import must not leak its values."""
    home = runtime.root.parent
    settings = Settings()
    canary = 'REVIEW_SYNTHETIC_SETTINGS_CANARY'
    original = bootstrap.BootstrapSupervisor.run

    async def run(self, qualify):
        (home / 'config.yaml').write_text(f'queue_workers: {canary}\n')
        return await original(self, qualify)

    monkeypatch.setattr(bootstrap.BootstrapSupervisor, 'run', run)
    state = DaemonState.from_runtime(runtime, settings, automatic_workflow_setup=True)
    try:
        worker, = state._workflow_bootstrap.workers
        assert worker.reaped and worker.process.returncode == 1
        assert worker.result is None
        assert state._workflow_bootstrap.holds == {'scratch': 'install_incomplete'}
        assert not list((home / 'backups').rglob('before.db'))
        stderr = capfd.readouterr().err
        assert canary not in stderr
        assert 'Traceback' not in stderr and 'input_value' not in stderr
        assert 'workflow setup helper failed: exception=ValidationError' in stderr
        with capfd.disabled():
            print(next(line for line in stderr.splitlines() if 'workflow setup helper failed:' in line))
    finally:
        # Transport loss retains the original unknown-custody refusal on close.
        try:
            asyncio.run(state.close_all())
        except bootstrap.BootstrapEvidenceError as error:
            assert str(error) == 'workflow_setup_original_worker_in_flight'
        finally:
            state.metrics_store.close()


def test_worker_mkdir_diagnostic_reaches_installer_stderr(
    runtime: RuntimeDir, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture,
) -> None:
    home = runtime.root.parent
    original = bootstrap.BootstrapSupervisor.run
    async def run(self, qualify):
        async def unwritable_after_discovery(*args):
            accepted = await qualify(*args)
            home.chmod(0o555)
            return accepted
        return await original(self, unwritable_after_discovery)
    monkeypatch.setattr(bootstrap.BootstrapSupervisor, 'run', run)
    state = None
    try:
        state = DaemonState.from_runtime(runtime, Settings(), automatic_workflow_setup=True)
        worker, = state._workflow_bootstrap.workers
        assert worker.reaped and worker.result['closed']
        assert set(worker.result) == {'event', 'state', 'closed', 'reason', 'attempt_id'}
        assert (worker.result['state'], worker.result['reason']) == ('unknown', 'org_filesystem_unavailable')
        assert not (home / 'backups').exists()
        assert stat.S_IMODE(home.stat().st_mode) == 0o555
        stderr = capfd.readouterr().err
        assert f'path={str(home / "backups")!r} check=mkdir' in stderr
        assert f'parent={str(home)!r}' in stderr
        assert 'parent_actual=0555' in stderr
        assert 'exception=PermissionError' in stderr
        with capfd.disabled():
            print(next(line for line in stderr.splitlines() if 'check=mkdir' in line))
    finally:
        home.chmod(0o700)
        if state is not None:
            asyncio.run(state.close_all())
            state.metrics_store.close()


def test_worker_path_diagnostic_reaches_installer_stderr(
    runtime: RuntimeDir, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture,
) -> None:
    """Actual private worker's stderr survives the real supervisor spawn."""
    path = runtime.root.parent / 'backups'
    original = bootstrap.BootstrapSupervisor.run
    async def run(self, qualify):
        async def wrong_mode_after_discovery(*args):
            accepted = await qualify(*args)
            path.mkdir(mode=0o755)
            return accepted
        return await original(self, wrong_mode_after_discovery)
    monkeypatch.setattr(bootstrap.BootstrapSupervisor, 'run', run)
    state = DaemonState.from_runtime(runtime, Settings(), automatic_workflow_setup=True)
    try:
        worker, = state._workflow_bootstrap.workers
        assert worker.reaped and worker.result['state'] == 'unknown'
        assert set(worker.result) == {'event', 'state', 'closed', 'reason', 'attempt_id'}
        assert worker.result['reason'] == 'org_load_refused'
        assert stat.S_IMODE(path.stat().st_mode) == 0o755
        stderr = capfd.readouterr().err
        assert repr(str(path)) in stderr
        assert 'check=mode actual=0755 expected=0700' in stderr
        assert 'exception=BootstrapEvidenceError' in stderr
        assert 'workflow_setup_namespace_unsafe' in stderr
        with capfd.disabled():
            print(next(line for line in stderr.splitlines() if 'check=mode actual=0755' in line))
    finally:
        asyncio.run(state.close_all())
        state.metrics_store.close()

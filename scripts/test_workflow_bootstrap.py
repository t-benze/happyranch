"""Focused A4 refusal/startup checks; see docs/operations/local-ci.md."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

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

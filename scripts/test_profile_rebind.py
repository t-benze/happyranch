"""Standalone synthetic-org profile recovery checks; no lifespan or providers."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scripts.test_agent_retirement import fixture
from runtime.config import Settings
from runtime.daemon.org_state import OrgState
from runtime.daemon.direct_connect_binding import bind_direct_connect_adapter
from runtime.infrastructure.producer_admission import NativeWriterStore
from runtime.orchestrator import executor_registry, prompt_loader
from runtime.orchestrator.producer_move import native_writer
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.adapter_store import AdapterEntry
from runtime.orchestrator.agent_def import render_agent_text
from runtime.orchestrator.runtime_executor_store import remove_runtime_profile
from runtime.workflows.authority import WorkflowAuthorityError
from runtime.workflows.profile_coordinator import ProfileCoordinator, ProfileCoordinatorError


def operation(org: OrgState) -> dict:
    rows = org.db.execute("SELECT * FROM workflow_profile_operations WHERE profile_name='dsh'").fetchall()
    assert len(rows) == 1, "adoption must never allocate another profile operation"
    return dict(rows[0])


def fence(org: OrgState) -> tuple:
    return (tuple(org.db.execute("SELECT * FROM workflow_authority_pointers").fetchone()),
            tuple(tuple(row) for row in org.db.execute("SELECT * FROM workflow_publication_journals ORDER BY id")))


async def move(org: OrgState, consumer: str, executor: str) -> None:
    async with org._profile_coordinator.consumer_writer(
        org=org, publisher="standalone-executor-update", consumer=consumer, executor=executor,
    ) as writer:
        with writer.canonical_change():
            old = prompt_loader.load_agent(OrgPaths(org.root), consumer)
            (org.root / 'org/agents' / (consumer + '.md')).write_text(render_agent_text(replace(old, executor=executor)))


def ready(org: OrgState, executors: dict[str, str]) -> None:
    snapshot = json.loads(org.workflow_authority.verify_admission_ready().snapshot_bytes)
    actual = {agent['name']: agent['executor'] for agent in snapshot['agents']}
    assert all(actual[name] == executor for name, executor in executors.items()), actual
    assert not org.db.execute("SELECT * FROM workflow_profile_leases").fetchall()
    assert not org.db.execute("SELECT * FROM workflow_publication_leases").fetchall()


@asynccontextmanager
async def removed(tmp_path: Path, *, consumers: tuple[str, ...] = ('worker',), paired: bool = False,
                  publisher: str = 'standalone-remove') -> AsyncIterator[SimpleNamespace]:
    with patch.object(executor_registry, '_registry', executor_registry.ExecutorRegistry()):
        async with fixture(tmp_path / 'org', workers=consumers) as (org, _):
            coordinator = org._profile_coordinator
            peer = None
            if paired:
                root = org.root.parent / 'beta'
                shutil.copytree(org.root / 'org', root / 'org')
                # The first fixture has already consumed its seed config.
                (root / 'org/config.yaml').write_text('reviewer_agents: [other]\n')
                peer = OrgState.load(slug='beta', root=root, settings=Settings())
                coordinator = ProfileCoordinator(daemon_home=coordinator._daemon_home, orgs={'alpha': org, 'beta': peer})
                for member in (org, peer):
                    member._profile_coordinator = member.workflow_authority._profile_coordinator = coordinator
                coordinator.reconcile_startup()
            executable = tmp_path / 'synthetic-adapter'
            executable.write_text('#!/bin/sh\nexit 0\n')
            executable.chmod(0o700)
            entry = AdapterEntry(id='synthetic-dsh', name='dsh', executable=str(executable),
                executable_hash=hashlib.sha256(executable.read_bytes()).hexdigest(), version='1.0.0',
                workspace_adapter='claude', intended_profile_name='dsh', status='approved',
                registered_by='synthetic', dependency_manifest_version=1)
            bind_direct_connect_adapter(entry, operation_id='synthetic-setup', via='direct_connect')
            for member in (org,) if peer is None else (org, peer):
                for consumer in consumers:
                    await move(member, consumer, 'dsh')
            with coordinator.operation(['dsh'], operation_kind='remove', publisher=publisher):
                remove_runtime_profile('dsh')
                executor_registry.get_registry().unregister_custom_profile('dsh')
            assert operation(org)['state'] == 'forward_recovery_required'
            with pytest.raises(WorkflowAuthorityError, match='authority_pointer_not_ready'):
                org.workflow_authority.verify_admission_ready()
            try:
                yield SimpleNamespace(org=org, peer=peer, coordinator=coordinator, entry=entry)
            finally:
                if peer is not None:
                    peer.close()


@asynccontextmanager
async def restarted(f: SimpleNamespace) -> AsyncIterator[SimpleNamespace]:
    members = [org for org in (f.org, f.peer) if org is not None]
    for org in members:
        org.close()
    loaded = {}
    try:
        for org in members:
            loaded[org.slug] = OrgState.load(slug=org.slug, root=org.root, settings=org.settings)
        coordinator = ProfileCoordinator(daemon_home=f.coordinator._daemon_home, orgs=loaded)
        for org in loaded.values():
            org._profile_coordinator = org.workflow_authority._profile_coordinator = coordinator
        coordinator.reconcile_startup()
        yield SimpleNamespace(org=loaded[f.org.slug], orgs=loaded, coordinator=coordinator, entry=f.entry)
    finally:
        for org in loaded.values():
            org.close()


@pytest.mark.parametrize('action', ['register', 'claimed', 'consumer', 'paired-adapter-remove'])
async def test_restart_forward_remove_allows_explicit_rebind(tmp_path: Path, action: str) -> None:
    publisher = 'remove_adapter_entry' if action == 'paired-adapter-remove' else 'remove_runtime_executor_profile'
    async with removed(tmp_path, paired=action == 'paired-adapter-remove', publisher=publisher) as f:
        before = operation(f.org)
        unknown = {key: row for key, row in NativeWriterStore(f.org.db, f.org.slug).history_uncommitted().items()
                   if row.state == 'unknown'}
        assert len(unknown) == 1
        assert next(iter(unknown.values())).publisher == f'profile:{publisher}'
        async with restarted(f) as fresh:
            assert not fresh.org._startup_publication_held, 'the remove must not latch an org startup hold'
            assert not fresh.coordinator._startup_holds and not fresh.coordinator._startup_hold_profiles
            assert operation(fresh.org) == before
            with pytest.raises(WorkflowAuthorityError, match='authority_pointer_not_ready'):
                fresh.org.workflow_authority.verify_admission_ready()
            if action == 'consumer':
                await move(fresh.org, 'worker', 'codex')
            else:
                span = (fresh.coordinator.claimed_operation(['dsh'], operation_kind='register',
                            publisher='direct_connect_projection', terminal_check=lambda: False) if action == 'claimed' else
                        fresh.coordinator.operation(['dsh'], operation_kind='register', publisher='ordinary-reconnect'))
                with span:
                    bind_direct_connect_adapter(fresh.entry, operation_id='synthetic-restart', via='direct_connect')
            after = operation(fresh.org)
            assert after['state'] == 'published'
            assert {k: v for k, v in after.items() if k not in ('state', 'profile_digest')} == {
                k: v for k, v in before.items() if k not in ('state', 'profile_digest')}
            ready(fresh.org, {'worker': 'codex' if action == 'consumer' else 'dsh'})
            for org in fresh.orgs.values():
                assert operation(org)['state'] == 'published'
                ready(org, {'worker': 'codex' if action == 'consumer' else 'dsh'})
            history = NativeWriterStore(fresh.org.db, fresh.org.slug).history_uncommitted()
            assert {key: history[key] for key in unknown} == unknown, 'no native settlement may be inferred'


@pytest.mark.parametrize('case', ['unrelated', 'active', 'scoped-agent', 'scoped-team', 'ordinary-journal',
                                  'other-invocation', 'mixed-profile', 'not-forward'])
async def test_restart_ineligible_native_owner_stays_held(tmp_path: Path, case: str) -> None:
    async with removed(tmp_path, publisher='remove_runtime_executor_profile') as f:
        if case == 'active':
            from runtime.infrastructure.producer_admission import NativeWriterLifecycle, native_owner_uncommitted
            native_owner_uncommitted(f.org.db).write_writer(NativeWriterLifecycle(
                org_slug=f.org.slug, writer_id='synthetic-active', publisher='profile:remove_runtime_executor_profile'))
        elif case in ('unrelated', 'scoped-agent', 'scoped-team'):
            with native_writer(f.org.db, publisher='unrelated' if case == 'unrelated' else 'profile:remove_runtime_executor_profile',
                               agents=('worker',) if case == 'scoped-agent' else (),
                               teams=('retiring',) if case == 'scoped-team' else ()) as disposition:
                disposition.uncertain = True
        elif case in ('ordinary-journal', 'other-invocation'):
            invocation = 'workflow-writer:ordinary' if case == 'ordinary-journal' else 'profile-coordinator:other'
            f.org.db._conn.execute("UPDATE workflow_publication_journals SET publisher_invocation=? WHERE state='prepared'",
                                   (invocation,))
        elif case == 'mixed-profile':
            row = operation(f.org)
            row.update(id='WPO-synthetic-other', profile_name='other')
            f.org.db._conn.execute('INSERT INTO workflow_profile_operations VALUES (' + ','.join('?' for _ in row) + ')',
                                  tuple(row.values()))
        else:
            f.org.db._conn.execute("UPDATE workflow_profile_operations SET state='store_committed'")
        f.org.db._conn.commit()
        before, old_fence = operation(f.org), fence(f.org)
        async with restarted(f) as fresh:
            assert fresh.org._startup_publication_held
            assert fresh.coordinator._startup_holds and 'dsh' in fresh.coordinator._startup_hold_profiles
            assert operation(fresh.org) == before and fence(fresh.org) == old_fence
            with pytest.raises(ProfileCoordinatorError, match='profile_consumer_changed'):
                with fresh.coordinator.claimed_operation(['dsh'], operation_kind='register',
                        publisher='direct_connect_projection', terminal_check=lambda: False):
                    pytest.fail('ineligible startup entered rebind mutation')
            assert operation(fresh.org) == before and fence(fresh.org) == old_fence


@pytest.mark.parametrize('peer_state', ['store_committed', 'published', 'aborted'])
async def test_restart_partially_forward_group_stays_held(tmp_path: Path, peer_state: str) -> None:
    async with removed(tmp_path, paired=True, publisher='remove_runtime_executor_profile') as f:
        f.peer.db._conn.execute('UPDATE workflow_profile_operations SET state=?', (peer_state,))
        f.peer.db._conn.commit()
        before = {org.slug: (operation(org), fence(org)) for org in (f.org, f.peer)}
        async with restarted(f) as fresh:
            assert set(fresh.coordinator._startup_holds) == set(fresh.orgs)
            assert 'dsh' in fresh.coordinator._startup_hold_profiles
            assert {slug: (operation(org), fence(org)) for slug, org in fresh.orgs.items()} == before
            with pytest.raises(ProfileCoordinatorError, match='profile_consumer_changed'):
                await move(fresh.org, 'worker', 'codex')


@pytest.mark.parametrize('claimed', [False, True], ids=['ordinary-register', 'claimed-direct-connect'])
async def test_same_profile_reconnect_finalizes_original_remove(tmp_path: Path, claimed: bool) -> None:
    async with removed(tmp_path) as f:
        before, old_fence = operation(f.org), fence(f.org)
        span = (f.coordinator.claimed_operation(['dsh'], operation_kind='register', publisher='direct_connect_projection',
                    terminal_check=lambda: False) if claimed else
                f.coordinator.operation(['dsh'], operation_kind='register', publisher='ordinary-reconnect'))
        with span:
            assert operation(f.org) == before
            assert fence(f.org) == old_fence
            bind_direct_connect_adapter(f.entry, operation_id='synthetic-reconnect', via='direct_connect')
        after = operation(f.org)
        assert after['state'] == 'published'
        assert {k: v for k, v in after.items() if k not in ('state', 'profile_digest')} == {
            k: v for k, v in before.items() if k not in ('state', 'profile_digest')}
        ready(f.org, {'worker': 'dsh'})


@pytest.mark.parametrize('target', ['codex', 'another-cli'])
async def test_consumer_move_publishes_changed_canonical_roster(tmp_path: Path, target: str) -> None:
    async with removed(tmp_path) as f:
        before = operation(f.org)
        if target == 'another-cli':
            with f.coordinator.operation([target], operation_kind='register', publisher='new-target'):
                bind_direct_connect_adapter(replace(f.entry, id='synthetic-target', intended_profile_name=target),
                                            operation_id='synthetic-target', via='direct_connect')
        await move(f.org, 'worker', target)
        assert operation(f.org) == dict(before, state='published')
        ready(f.org, {'worker': target})
        assert f.org.db.execute("SELECT state FROM workflow_profile_dependencies WHERE profile_name='dsh'").fetchone()[0] == 'removed'


@pytest.mark.parametrize('finish', ['move', 'reconnect'])
async def test_partial_move_keeps_fence_until_closure_coherent(tmp_path: Path, finish: str) -> None:
    async with removed(tmp_path, consumers=('worker', 'second')) as f:
        before, old_fence = operation(f.org), fence(f.org)
        await move(f.org, 'worker', 'codex')
        assert operation(f.org) == before
        assert fence(f.org) == old_fence
        # Reconstruct the profile coordinator without claiming a whole daemon
        # restart: native writer recovery has its own independent startup gate.
        fresh = ProfileCoordinator(daemon_home=f.coordinator._daemon_home, orgs={f.org.slug: f.org})
        f.org._profile_coordinator = f.org.workflow_authority._profile_coordinator = fresh
        if finish == 'reconnect':
            with fresh.operation(['dsh'], operation_kind='rebind', publisher='ordinary-reconnect'):
                bind_direct_connect_adapter(f.entry, operation_id='synthetic-reconnect', via='direct_connect')
            ready(f.org, {'worker': 'codex', 'second': 'dsh'})
        else:
            await move(f.org, 'second', 'codex')
            ready(f.org, {'worker': 'codex', 'second': 'codex'})
        assert operation(f.org)['state'] == 'published'
        assert operation(f.org)['id'] == before['id']


@pytest.mark.parametrize('case', ['captured', 'fenced', 'store_committed', 'kind', 'other-profile', 'mixed-profile', 'missing-member', 'binding'])
async def test_ineligible_rebind_retains_original_owner(tmp_path: Path, case: str) -> None:
    async with removed(tmp_path) as f:
        conn = f.org.db._conn
        if case in ('captured', 'fenced', 'store_committed'):
            conn.execute('UPDATE workflow_profile_operations SET state=?', (case,))
        elif case == 'kind':
            conn.execute("UPDATE workflow_profile_operations SET operation_kind='register'")
        elif case == 'mixed-profile':
            row = operation(f.org)
            row.update(id='WPO-synthetic-other', profile_name='other')
            conn.execute('INSERT INTO workflow_profile_operations VALUES (' + ','.join('?' for _ in row) + ')', tuple(row.values()))
        elif case == 'missing-member':
            conn.execute("UPDATE workflow_profile_operations SET captured_members='[\"alpha\",\"unloaded\"]'")
        elif case == 'binding':
            conn.execute("UPDATE workflow_publication_journals SET publisher_invocation='profile-coordinator:other' WHERE state='prepared'")
        conn.commit()
        before, old_fence = operation(f.org), fence(f.org)
        if case == 'other-profile':
            with pytest.raises(ProfileCoordinatorError, match='profile_operation_in_progress'):
                await move(f.org, 'other', 'dsh')
        else:
            with pytest.raises(ProfileCoordinatorError, match='profile_operation_in_progress'):
                with f.coordinator.operation(['dsh'], operation_kind='register', publisher='invalid'):
                    pytest.fail('ineligible rebind entered mutation')
        assert operation(f.org) == before and fence(f.org) == old_fence


async def test_invalid_rebind_preserves_forward_owner(tmp_path: Path) -> None:
    async with removed(tmp_path) as f:
        before, old_fence = operation(f.org), fence(f.org)
        with pytest.raises(ValueError, match='hash'):
            with f.coordinator.operation(['dsh'], operation_kind='register', publisher='invalid'):
                bind_direct_connect_adapter(replace(f.entry, executable_hash='0' * 64),
                                            operation_id='invalid', via='direct_connect')
        assert operation(f.org) == before and fence(f.org) == old_fence
        with pytest.raises(ProfileCoordinatorError, match='profile_target_not_published'):
            await move(f.org, 'worker', 'missing')
        assert operation(f.org) == before and fence(f.org) == old_fence
        with pytest.raises(ProfileCoordinatorError, match='profile_operation_in_progress'):
            with f.coordinator.operation(['dsh'], operation_kind='remove', publisher='not-rebind'):
                pytest.fail('second remove entered mutation')
        assert operation(f.org) == before and fence(f.org) == old_fence


async def test_terminal_projection_loser_acquires_no_durable_owner(tmp_path: Path) -> None:
    async with removed(tmp_path) as f:
        before, old_fence = operation(f.org), fence(f.org)
        with patch.object(f.coordinator, '_insert_operation_rows', side_effect=AssertionError('claimed operation')):
            with f.coordinator.claimed_operation(['dsh'], operation_kind='register', publisher='direct_connect_projection',
                                                terminal_check=lambda: True) as owns:
                assert owns is False
        assert operation(f.org) == before and fence(f.org) == old_fence
        assert not f.org.db.execute('SELECT * FROM workflow_profile_leases').fetchall()


async def test_incoherent_remove_warns_with_dependency_identity(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    async with removed(tmp_path) as f:
        messages = [record.getMessage() for record in caplog.records
                    if record.name == 'runtime.workflows.profile_coordinator' and record.levelname == 'WARNING']
        assert any('forward_recovery_required' in message for message in messages), messages
        warning = next(message for message in messages if 'forward_recovery_required' in message)
        assert f'org={f.org.slug}' in warning and 'profile=dsh' in warning and operation(f.org)['id'] in warning
        assert all(field in warning for field in ('worker', 'unbound', 'bound_generation', 'published_generation', 'removed'))
        assert f.entry.executable not in warning and f.entry.executable_hash not in warning


async def test_startup_native_uncertainty_retains_independent_hold(tmp_path: Path) -> None:
    async with removed(tmp_path, consumers=('worker', 'second')) as f:
        await move(f.org, 'worker', 'codex')
        before, old_fence = operation(f.org), fence(f.org)
        fresh = ProfileCoordinator(daemon_home=f.coordinator._daemon_home, orgs={f.org.slug: f.org})
        fresh.reconcile_startup()
        assert fresh._startup_holds, 'uncertain native writer history must retain its existing startup hold'
        assert operation(f.org) == before and fence(f.org) == old_fence
        f.org._profile_coordinator = f.org.workflow_authority._profile_coordinator = fresh
        with pytest.raises(ProfileCoordinatorError, match='profile_consumer_changed'):
            await move(f.org, 'second', 'codex')
        assert operation(f.org) == before and fence(f.org) == old_fence


async def test_cross_org_partial_move_retains_every_original_binding(tmp_path: Path) -> None:
    async with removed(tmp_path, paired=True) as f:
        before = [operation(org) for org in (f.org, f.peer)]
        old_fences = [fence(org) for org in (f.org, f.peer)]
        assert before[0] == before[1]
        assert json.loads(before[0]['captured_members']) == ['alpha', 'beta']
        await move(f.org, 'worker', 'codex')
        assert [operation(org) for org in (f.org, f.peer)] == before
        assert [fence(org) for org in (f.org, f.peer)] == old_fences
        await move(f.peer, 'worker', 'codex')
        for org in (f.org, f.peer):
            assert operation(org) == dict(before[0], state='published')
            ready(org, {'worker': 'codex'})


async def test_invalid_canonical_capture_never_reuses_stale_predecessor(tmp_path: Path) -> None:
    async with removed(tmp_path) as f:
        f.org.db._conn.execute("UPDATE org_settings SET value_json=? WHERE section='reviewer_agents'",
                               ('["unknown-reviewer"]',))
        f.org.db._conn.commit()
        before, old_fence = operation(f.org), fence(f.org)
        with pytest.raises(WorkflowAuthorityError, match='authority_reviewer_incoherent'):
            with f.coordinator.operation(['dsh'], operation_kind='register', publisher='invalid-canonical'):
                pytest.fail('invalid canonical capture entered mutation')
        assert operation(f.org) == before and fence(f.org) == old_fence

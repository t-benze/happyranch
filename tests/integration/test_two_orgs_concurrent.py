"""End-to-end: two orgs each submit a task; both run to completion under
one daemon, and neither org's audit log mentions the other's task.

Style note: this file mirrors ``test_end_to_end.py`` — same ``live_daemon``
fixture, same ``_auth_headers`` helper, same ``httpx`` direct calls. The
plan (docs/superpowers/plans/2026-04-28-parallel-multi-org-runtime.md
§Task 24) sketched the flow using a ``daemon_under_test`` fixture with
``bootstrap_minimal_agent`` / ``task_status`` / ``audit_log`` helpers
that don't exist; we adapt to the project's established HTTP integration
pattern while preserving the plan's spirit — exercise daemon routes
end-to-end across two concurrent orgs.

Isolation invariant: each org has its own SQLite DB and its own
``next_task_id`` counter, so both orgs naturally produce ``TASK-001`` as
their first task. The test asserts that:

1. Both orgs reach ``completed``.
2. Org alpha's audit-log query for ``TASK-001`` returns entries tagged
   to alpha (e.g. ``session_start`` for agents in alpha) and never
   bleeds into beta — and vice versa.
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack, closing, contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import socket
import sqlite3
import threading
import time
from typing import Callable, Iterator
from pathlib import Path

import httpx
import pytest

from tests.integration.conftest import seed_agent_definition, seed_workspace
from tests.helpers.completion_plan import completion_prelude


pytestmark = pytest.mark.integration


def _auth_headers() -> dict:
    from runtime.daemon import paths

    return {"Authorization": f"Bearer {paths.read_token()}"}


def _org_base(port: str, slug: str) -> str:
    return f"http://127.0.0.1:{port}/api/v1/orgs/{slug}"


def _global_base(port: str) -> str:
    return f"http://127.0.0.1:{port}/api/v1"


def _make_example_tree(tmp_path: Path) -> Path:
    """Build a minimal engineering roster including the default reviewer.

    POST /orgs --from_example copies <tree>/org/
    verbatim into <runtime>/orgs/<slug>/org/, so this tree only needs an
    ``org/`` subdir with ``teams.yaml`` and its active roster before attachment."""
    tree = tmp_path / "example_org"
    org = tree / "org"
    (org / "agents" / "_pending").mkdir(parents=True)
    (org / "teams.yaml").write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: engineering_head\n"
        "    workers: [dev_agent, code_reviewer]\n"
    )
    # Dynamic attachment validates the configured reviewers too; the default
    # code_reviewer must be active before the real POST /orgs can attach it.
    for agent in ("engineering_head", "dev_agent", "code_reviewer"):
        seed_agent_definition(tree, agent)
    return tree


def _init_org(port: str, slug: str, example: Path) -> None:
    r = httpx.post(
        f"{_global_base(port)}/orgs",
        json={"slug": slug, "from_example": str(example)},
        headers=_auth_headers(),
        timeout=5.0,
    )
    assert r.status_code == 200, r.text


def _submit_task(port: str, slug: str, brief: str) -> str:
    r = httpx.post(
        f"{_org_base(port, slug)}/tasks",
        json={"type": "general", "brief": brief},
        headers=_auth_headers(),
        timeout=5.0,
    )
    assert r.status_code == 200, r.text
    return r.json()["task_id"]


def _wait_for_completed(port: str, slug: str, task_id: str, timeout: float) -> None:
    import time

    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        r = httpx.get(
            f"{_org_base(port, slug)}/tasks/{task_id}",
            headers=_auth_headers(),
            timeout=5.0,
        )
        if r.status_code == 200:
            last = r.json()
            status = last["task"]["status"]
            if status in ("completed", "failed"):
                assert status == "completed", (slug, task_id, last)
                return
        time.sleep(0.2)
    raise AssertionError(
        f"task {slug}/{task_id} did not complete within {timeout}s "
        f"(last body={last})"
    )


def test_two_orgs_run_tasks_concurrently_under_one_daemon(
    live_daemon,
    runtime_container: Path,
    fake_plan_env: Path,
    tmp_path: Path,
) -> None:
    """Submit a task to alpha and beta in quick succession; assert both
    complete and that each org's audit log for TASK-001 is namespaced to
    that org alone."""
    port = live_daemon

    # Plan: every fake-claude session reports completion. The script reads
    # $org_slug ($4) from PWD-derived extraction in fake_claude.sh, so the
    # same plan body works for alpha and beta.
    fake_plan_env.write_text(
        '#!/usr/bin/env bash\n' + completion_prelude() +
        'set -e\n'
        'task_id=$1; session_id=$2; agent=$3; org_slug=$4\n'
        'report_completion engineering_head \'{"action":"done","summary":"ok"}\'\n'
    )
    fake_plan_env.chmod(0o755)

    # Materialize alpha and beta orgs alongside the conftest-seeded
    # "test" org.
    example = _make_example_tree(tmp_path)
    _init_org(port, "alpha", example)
    _init_org(port, "beta", example)

    # Seed the engineering_head workspace inside each org's per-org root.
    alpha_root = runtime_container / "orgs" / "alpha"
    beta_root = runtime_container / "orgs" / "beta"
    seed_workspace(alpha_root, "engineering_head")
    seed_workspace(beta_root, "engineering_head")

    # Submit two tasks in rapid succession (same Python tick — the daemon
    # is async and should interleave them across its worker pool).
    tid_a = _submit_task(port, "alpha", "alpha ping")
    tid_b = _submit_task(port, "beta", "beta ping")

    # Per-org task counters are independent — both should be TASK-001.
    assert tid_a == "TASK-001", tid_a
    assert tid_b == "TASK-001", tid_b

    # Both reach completed within 30s.
    _wait_for_completed(port, "alpha", tid_a, timeout=30.0)
    _wait_for_completed(port, "beta", tid_b, timeout=30.0)

    # Audit isolation: alpha's audit for TASK-001 reports work in alpha;
    # beta's audit for TASK-001 reports work in beta. Neither query
    # surfaces the other org's session ids.
    r_a = httpx.get(
        f"{_org_base(port, 'alpha')}/audit",
        params={"task_id": tid_a},
        headers=_auth_headers(),
        timeout=5.0,
    )
    assert r_a.status_code == 200, r_a.text
    audit_a = r_a.json()["entries"]

    r_b = httpx.get(
        f"{_org_base(port, 'beta')}/audit",
        params={"task_id": tid_b},
        headers=_auth_headers(),
        timeout=5.0,
    )
    assert r_b.status_code == 200, r_b.text
    audit_b = r_b.json()["entries"]

    assert audit_a, "alpha audit empty"
    assert audit_b, "beta audit empty"

    # Sanity: each side recorded a session_start for engineering_head.
    starts_a = [
        e for e in audit_a
        if e.get("action") == "session_start" and e.get("agent") == "engineering_head"
    ]
    starts_b = [
        e for e in audit_b
        if e.get("action") == "session_start" and e.get("agent") == "engineering_head"
    ]
    assert starts_a, audit_a
    assert starts_b, audit_b

    # Cross-org isolation: alpha's session_start payload points at a
    # workspace under <container>/orgs/alpha/...; beta's points at
    # <container>/orgs/beta/.... The audit shard is per-org SQLite, so
    # neither query can leak the other org's rows even if task_ids
    # collide (and they do — both are TASK-001).
    workspaces_a = [
        e["payload"]["workspace"] for e in starts_a if e.get("payload")
    ]
    workspaces_b = [
        e["payload"]["workspace"] for e in starts_b if e.get("payload")
    ]
    assert all("/orgs/alpha/" in w for w in workspaces_a), workspaces_a
    assert all("/orgs/beta/" in w for w in workspaces_b), workspaces_b
    assert all("/orgs/beta/" not in w for w in workspaces_a), workspaces_a
    assert all("/orgs/alpha/" not in w for w in workspaces_b), workspaces_b

    from tests.helpers.integration_stub_guard.guard import assert_launch_witness
    assert_launch_witness("claude", callbacks=2)


# THR308seq301 / ROOT14301: finite qualification preparation. These cases are
# deliberately outside required PR CI. Do not run them on a live daemon host.
# K1 above and the compatible-profile K2 source remain independent keepers.


_STARTUP_PROFILES = ('startup-p', 'startup-q')
_STARTUP_TABLES = (
    'workflow_authority_pointers', 'workflow_publication_journals',
    'workflow_publication_leases', 'workflow_profile_dependencies',
    'workflow_profile_store', 'workflow_profile_registry',
    'workflow_profile_operations', 'org_settings', 'tasks', 'task_results', 'thread_invocations',
    'schedules', 'dreams', 'work_hours', 'audit_log',
    'workflow_activations', 'workflow_draft_dispatch_intents', 'workflow_contexts',
    'workflow_instances', 'workflow_binding_snapshots', 'workflow_authorization_revisions',
)


def _startup_rows(org: object) -> dict:
    """Independent committed observer O; no transaction survives this function."""
    with closing(sqlite3.connect((org if isinstance(org, Path) else org.db.path).resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('BEGIN')
        try:
            rows = {table: [dict(row) for row in conn.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
                    for table in _STARTUP_TABLES}
        finally:
            conn.rollback()
    root = (org.parent if isinstance(org, Path) else org.root)
    files = [root / 'org/teams.yaml', *sorted((root / 'org/agents').glob('*.md')), root / 'org/.workflow-authority.json']
    rows['canonical_files'] = {str(path.relative_to(root)): path.read_bytes() for path in files if path.is_file()}
    return rows


def _startup_record(request: pytest.FixtureRequest, phase: str, **data: object) -> None:
    """Retain actual cuts/readbacks beside JUnit, including on assertion failure."""
    # The unchanged parent deliberately strips ambient environment variables.
    # JUnit's authored absolute path is an existing supported pytest channel.
    xml = Path(request.config.option.xmlpath)
    assert xml.is_absolute() and xml.name == 'startup-isolation.xml'
    directory = xml.parent / 'startup-cases'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / (hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16] + '.jsonl')
    def encode(value: object) -> object:
        if isinstance(value, bytes):
            return {'hex': value.hex(), 'sha256': hashlib.sha256(value).hexdigest()}
        raise TypeError(type(value).__name__)
    with path.open('a') as stream:
        stream.write(json.dumps({'node': request.node.nodeid, 'phase': phase,
                                 'time_ns': time.time_ns(), **data}, default=encode, sort_keys=True) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


@contextmanager
def _startup_profiles(home: Path) -> Iterator[None]:
    """F3's real approved/hash-bound inert profile setup, in this private home."""
    from runtime.orchestrator.adapter_store import AdapterEntry, get_adapter, save_adapter, remove_adapter
    from runtime.orchestrator.executor_registry import get_registry
    from runtime.orchestrator.runtime_executor_store import load_runtime_profiles, save_runtime_profile, remove_runtime_profile
    registry = get_registry()
    with ExitStack() as restore:
        for name in _STARTUP_PROFILES:
            adapter_id = name + '-adapter'
            assert load_runtime_profiles().get(name) is None
            assert get_adapter(adapter_id) is None and registry.get_profile(name) is None
            executable = home / (name + '-never-launch')
            raw = b'#!/bin/sh\n# Profile metadata only; managers use the parent Claude stub.\nexit 86\n'
            executable.write_bytes(raw)
            executable.chmod(0o700)
            save_adapter(AdapterEntry(id=adapter_id, name=adapter_id, executable=str(executable),
                executable_hash=hashlib.sha256(raw).hexdigest(), version='startup-fixture-1',
                workspace_adapter='pi', status='approved', registered_at='2026-11-06T00:00:00+00:00',
                registered_by='startup-synthetic-fixture', approved_at='2026-11-06T00:00:00+00:00',
                approved_by='startup-synthetic-fixture', intended_profile_name=name))
            restore.callback(remove_adapter, adapter_id)
            config = {'workspace_adapter_id': 'pi', 'command_adapter_id': 'custom-adapter:' + adapter_id}
            profile = registry.validate_custom_profile_config(name, config)
            registry.register_custom_profile(profile)
            restore.callback(registry.unregister_custom_profile, name)
            save_runtime_profile(name, config)
            restore.callback(remove_runtime_profile, name)
            assert registry.get_profile(name) == profile
        yield


class _StartupCase:
    """Only C1-C8 fixture data/cuts; classification is always production code."""
    def __init__(self, request: pytest.FixtureRequest, container: Path, settings: object,
                 monkeypatch: pytest.MonkeyPatch) -> None:
        self.request, self.container, self.settings, self.patch = request, container, settings, monkeypatch
        self.states: list = []
        self.all_handles: dict = {}
        self.original: dict = {}
        self.closed: set[int] = set()
        self.failed_close: set[int] = set()
        self.retained_unknown: set[int] = set()
        self.metrics_closed: set[int] = set()

    def seed(self, assignments: dict[str, str | None]) -> None:
        import shutil
        from runtime.orchestrator.agent_def import render_agent_text
        for slug, profile in assignments.items():
            tree = _make_example_tree(self.container.parent / slug)
            root = self.container / 'orgs' / slug
            shutil.copytree(tree / 'org', root / 'org')
            seed_workspace(root, 'engineering_head')
            for agent in ('engineering_head', 'dev_agent', 'code_reviewer'):
                definition = seed_agent_definition(root, agent)
                if agent == 'dev_agent' and profile:
                    definition = replace(definition, executor=profile)
                if agent == 'engineering_head':
                    definition = replace(definition, system_prompt=definition.system_prompt +
                        '\n\n## Routine Tasks\n\n- Check the synthetic startup inbox.\n')
                (root / 'org' / 'agents' / (agent + '.md')).write_text(render_agent_text(definition))
            # These are proven new synthetic org roots, never an upgrade of an
            # installed layout. Reuse the explicit fresh-create native owner.
            from runtime.infrastructure.database import Database
            from runtime.infrastructure.workflow_schema import initialize_complete_org_schema
            assert not (root / 'happyranch.db').exists()
            db = Database(root / 'happyranch.db')
            try:
                initialize_complete_org_schema(db, expected_org_slug=slug, layout='I')
            finally:
                db.close()
            _startup_record(self.request, 'fresh-synthetic-org-initialized', slug=slug, layout='I')

    def boot(self, cut: Callable | None = None) -> object:
        from runtime.daemon.state import DaemonState
        from runtime.runtime import RuntimeDir
        from runtime.workflows.profile_coordinator import ProfileCoordinator, ProfileCoordinatorError
        real = ProfileCoordinator.reconcile_startup
        real_construct = DaemonState.__post_init__
        def constructed(state: object) -> None:
            real_construct(state)
            self.states.append(state)
        def observed(coordinator: object) -> None:
            # The real factory has loaded all original handles and selectors.
            self.original = dict(coordinator.orgs)
            self.all_handles.update({id(o): o for o in self.original.values()})
            if cut is not None:
                coordinator.synchronize_all_dependencies()
                cut(coordinator)
            real(coordinator)
        with self.patch.context() as patch:
            patch.setattr(DaemonState, '__post_init__', constructed)
            patch.setattr(ProfileCoordinator, 'reconcile_startup', observed)
            try:
                state = DaemonState.from_runtime(RuntimeDir.load(self.container), self.settings)
            except ProfileCoordinatorError as exc:
                _startup_record(self.request, 'constructor-refused', code=exc.code,
                                rows={s: _startup_rows(o) for s, o in self.original.items()})
                # This is the serving oracle AFTER the genuine guard, never a
                # fixture/import exception relabelled as the intended C1 RED.
                raise AssertionError('startup did not reach serving: ' + exc.code) from exc
        _startup_record(self.request, 'factory-returned', runnable=list(state.orgs_snapshot()),
                        broken=state.broken_orgs)
        return state

    def ordinary(self, org: object) -> dict:
        """Real supported writer with a selected publication failure, no mock capture."""
        from runtime.workflows.authority import WorkflowAuthorityError
        authority = org.workflow_authority
        # Canonical roster is valid before AND after the failed profile projection.
        canonical = authority.capture_canonical_snapshot()
        with self.patch.context() as patch:
            def failed_projection() -> list:
                raise WorkflowAuthorityError('authority_pointer_not_ready')
            patch.setattr(authority, '_profile_projection', failed_projection)
            with authority.writer_interval(publisher='startup-case-original') as interval:
                with interval.canonical_change():
                    org.db.upsert_org_setting('startup_case_owner', '"original"')
        assert authority.capture_canonical_snapshot() == canonical
        assert not authority._async_writer_lock.locked()
        rows = _startup_rows(org)
        active = [r for r in rows['workflow_publication_journals'] if r['id'] == interval.fence_journal_id]
        assert len(active) == 1 and active[0]['state'] == 'prepared'
        assert active[0]['publisher_invocation'] == interval.publisher_invocation
        assert rows['workflow_publication_leases'] == []
        from runtime.infrastructure.producer_admission import NativeWriterStore
        with org.db._lock:
            history = NativeWriterStore(org.db, org.slug).history_uncommitted()
        assert history[interval.native_disposition.writer_id].state == 'unknown'
        assert any('unknown' in str(r['payload']) for r in rows['audit_log'] if r['action'] == 'team_move_native_writer')
        _startup_record(self.request, 'ordinary-prepared', slug=org.slug, canonical=canonical,
                        journal=active[0], rows=rows)
        return rows

    def classify(self, state: object) -> None:
        from runtime.workflows.profile_coordinator import ProfileCoordinatorError
        try:
            state.profile_coordinator.reconcile_startup()
        except ProfileCoordinatorError as exc:
            _startup_record(self.request, 'classification-refused', code=exc.code)
            raise AssertionError('native startup classification escaped: ' + exc.code) from exc
        _startup_record(self.request, 'classified', runnable=list(state.orgs_snapshot()), broken=state.broken_orgs)

    def held(self, state: object, slugs: tuple[str, ...]) -> None:
        for slug in slugs:
            assert slug not in state.orgs_snapshot(), (slug, state.broken_orgs)
            assert slug in state.broken_orgs and state.broken_orgs[slug]
            with pytest.raises(KeyError):
                state.get_org(slug)
        # Global profile inventory must retain original handles after eviction.
        with state.profile_coordinator._capture_orgs() as inventory:
            assert all(inventory.get(slug) is self.original[slug] for slug in slugs)

    def close(self) -> None:
        # Only fixture-owned, positively idle resources. Never repeat a failed
        # close or infer native return from a done/cancelled Future.
        from runtime.daemon.org_state import OrgState
        handles = dict(self.all_handles)
        for state in self.states:
            handles.update({id(o): o for o in state.orgs_snapshot().values()})
        for identity, org in handles.items():
            if identity in self.closed or identity in self.failed_close or identity in self.retained_unknown:
                continue
            try:
                org._assert_runners_idle()
                OrgState.close(org)
            except BaseException as exc:
                self.failed_close.add(identity)
                _startup_record(self.request, 'cleanup-unknown', slug=org.slug, error=repr(exc))
                raise
            self.closed.add(identity)
            _startup_record(self.request, 'positive-close', slug=org.slug, identity=identity)
        for state in self.states:
            if state.metrics_store is not None:
                if id(state.metrics_store) not in self.metrics_closed:
                    state.metrics_store.close()
                    self.metrics_closed.add(id(state.metrics_store))
                state.metrics_store = None


@pytest.fixture
def startup_case(request: pytest.FixtureRequest, runtime_container: Path, tmp_home: Path,
                 fake_claude: Path, fake_codex: Path, fake_opencode: Path,
                 fake_plan_env: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[_StartupCase]:
    from runtime.config import Settings
    from runtime.daemon import paths, runtimes
    from tests.helpers.integration_stub_guard.guard import require_parent_environment, manifest
    require_parent_environment()
    tmp_home.mkdir(mode=0o700, parents=True, exist_ok=True)
    runtimes.register(runtime_container)
    for provider, path in (('CLAUDE', fake_claude), ('CODEX', fake_codex), ('OPENCODE', fake_opencode)):
        monkeypatch.setenv('HAPPYRANCH_' + provider + '_CLI_PATH', str(path))
    fake_plan_env.write_text('#!/usr/bin/env bash\n' + completion_prelude() +
        'set -e\ntask_id=$1; session_id=$2; agent=$3; org_slug=$4\n'
        'report_completion engineering_head \'{"action":"done","summary":"startup healthy control"}\'\n')
    fake_plan_env.chmod(0o755)
    paths.ensure_token()
    case = _StartupCase(request, runtime_container, Settings(), monkeypatch)
    import sys
    _startup_record(request, 'source-interpreter', parent=manifest(), executable=sys.executable, version=sys.version)
    with _startup_profiles(tmp_home):
        try:
            yield case
        finally:
            try:
                case.close()
            finally:
                witnesses = Path(os.environ['HAPPYRANCH_TEST_WITNESS_DIR']) / 'identities.jsonl'
                _startup_record(request, 'final-custody', positive_closed=sorted(case.closed),
                    close_unknown=sorted(case.failed_close), retained_unknown=sorted(case.retained_unknown),
                    witness=([json.loads(line) for line in witnesses.read_text().splitlines()] if witnesses.exists() else []))


@contextmanager
def _startup_http(case: _StartupCase, state: object, *, tasks: bool = True) -> Iterator[str]:
    """Owned loopback ASGI listener; use native queue, not the other producers.

    These cases own producer loops explicitly in C6. This listener therefore
    does not invoke the application's unrelated lifespan scans/recovery again.
    The actual cold constructor/reconciliation has already returned.
    """
    import uvicorn
    from runtime.daemon import paths
    from runtime.daemon.app import create_app, _wire_then_start_workers
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = str(listener.getsockname()[1])
    paths.port_file().write_text(port)
    server = uvicorn.Server(uvicorn.Config(create_app(state), host='127.0.0.1',
        port=int(port), lifespan='off', log_level='warning'))
    loop = asyncio.new_event_loop()
    errors: list = []
    async def serve() -> None:
        if tasks:
            _wire_then_start_workers(state, asyncio.get_running_loop())
        try:
            await server.serve(sockets=[listener])
        finally:
            if tasks:
                await state.queue.stop(timeout=10)
                state.queue.assert_native_idle()
    def owned() -> None:
        try:
            loop.run_until_complete(serve())
        except BaseException as exc:
            errors.append(exc)
        finally:
            loop.close()
    worker = threading.Thread(target=owned, name='startup-case-http')
    worker.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started and not errors, errors
        _startup_record(case.request, 'http-serving', port=port)
        yield port
    finally:
        server.should_exit = True
        worker.join(20)
        _startup_record(case.request, 'http-return', alive=worker.is_alive(), errors=[repr(e) for e in errors])
        assert not worker.is_alive() and not errors, errors
        listener.close()


def _startup_original_unchanged(before: dict, after: dict) -> None:
    for table in ('workflow_authority_pointers', 'workflow_publication_journals', 'workflow_profile_operations', 'canonical_files'):
        assert after[table] == before[table], table
    originals = [r for r in before['audit_log'] if r['action'] == 'team_move_native_writer']
    original_ids = {r['task_id'] for r in originals}
    assert [r for r in after['audit_log'] if r['action'] == 'team_move_native_writer' and r['task_id'] in original_ids] == originals


def test_startup_isolation_prepared_journal(startup_case: _StartupCase) -> None:
    case = startup_case
    case.seed({'alpha': 'startup-p', 'beta': None})
    baseline: dict = {}
    def prepared(coordinator: object) -> None:
        from runtime.workflows.profile_coordinator import ProfileCoordinatorError
        alpha = case.original['alpha']
        baseline.update(case.ordinary(alpha))
        # Reach the exact ordinary prepared guard with valid canonical capture,
        # no async lock and no durable lease. The startup call below still runs.
        with pytest.raises(ProfileCoordinatorError, match='^profile_consumer_changed$'):
            coordinator._capture_org_dependencies(alpha)
        _startup_record(case.request, 'historical-guard-reached', rows=_startup_rows(alpha))
    state = case.boot(prepared)
    case.held(state, ('alpha',))
    with _startup_http(case, state) as port:
        assert httpx.get(_global_base(port) + '/health').status_code == 200
        response = httpx.get(_global_base(port) + '/orgs', headers=_auth_headers())
        assert response.status_code == 200
        assert 'profile_consumer_changed' in response.text
        denied = httpx.post(_org_base(port, 'alpha') + '/tasks', headers=_auth_headers(),
                            json={'type': 'general', 'brief': 'held original'})
        assert denied.status_code == 404 and 'unknown_org' in denied.text
        task = _submit_task(port, 'beta', 'independent startup callback')
        _wait_for_completed(port, 'beta', task, 30)
    after = _startup_rows(case.original['alpha'])
    _startup_record(case.request, 'original-readback', before=baseline, after=after)
    _startup_original_unchanged(baseline, after)
    assert after['tasks'] == baseline['tasks'] and after['task_results'] == baseline['task_results']
    from tests.helpers.integration_stub_guard.guard import assert_launch_witness
    assert_launch_witness('claude', callbacks=1)


@pytest.mark.parametrize('profile', [None, 'startup-q'], ids=['builtin', 'custom-disjoint'])
def test_startup_isolation_independent_org(startup_case: _StartupCase, profile: str | None) -> None:
    case = startup_case
    case.seed({'alpha': 'startup-p', 'beta': profile})
    before: dict = {}
    def cut(coordinator: object) -> None:
        before.update(_startup_rows(case.original['beta']))
        case.ordinary(case.original['alpha'])
    state = case.boot(cut)
    case.held(state, ('alpha',))
    beta = state.get_org('beta')
    capture = beta.workflow_authority.capture_admission()
    assert capture.profile_names == (() if profile is None else (profile,))
    assert not _startup_rows(beta)['workflow_profile_operations']
    with _startup_http(case, state) as port:
        task = _submit_task(port, 'beta', 'independent builtin manager with disjoint consumer')
        _wait_for_completed(port, 'beta', task, 30)
    after = _startup_rows(beta)
    assert after['workflow_authority_pointers'] == before['workflow_authority_pointers']
    assert after['workflow_publication_journals'] == before['workflow_publication_journals']
    assert len(after['task_results']) == 1 and after['tasks'][0]['status'] == 'completed'
    _startup_record(case.request, 'independent-final', profile=profile, before=before, after=after)


def _startup_shared_operation(case: _StartupCase, state: object) -> dict:
    """Create actual multi-profile operation rows; fail only final publication."""
    coordinator = state.profile_coordinator
    with case.patch.context() as patch:
        real = coordinator._republish
        def fail_publication(**kwargs: object) -> bool:
            # Real fences/claims/rows already exist. Do not replace membership,
            # capture, dispatcher, classification or an expected outcome.
            raise OSError('startup-case-selected-profile-publication')
        patch.setattr(coordinator, '_republish', fail_publication)
        with coordinator.operation(_STARTUP_PROFILES, operation_kind='rebind', publisher='startup-shared'):
            pass
        patch.setattr(coordinator, '_republish', real)
    rows = {slug: _startup_rows(org) for slug, org in case.original.items()}
    operations = [r for row in rows.values() for r in row['workflow_profile_operations']]
    assert {r['profile_name'] for r in operations} == set(_STARTUP_PROFILES)
    assert len({r['coordinator_invocation'] for r in operations}) == 1
    assert all(r['state'] not in ('published', 'aborted') for r in operations)
    _startup_record(case.request, 'shared-operation-owned', rows=rows)
    return rows


@pytest.mark.parametrize('variant', ['active', 'unbound-absent-canonical', 'captured-absent-canonical'])
def test_startup_isolation_shared_closure(startup_case: _StartupCase, variant: str) -> None:
    from runtime.orchestrator.agent_def import render_agent_text
    from runtime.orchestrator import prompt_loader
    from runtime.orchestrator._paths import OrgPaths
    case = startup_case
    case.seed({'alpha': 'startup-p', 'beta': 'startup-p', 'gamma': 'startup-q', 'delta': None})
    state = case.boot()
    baseline = _startup_shared_operation(case, state)
    if variant != 'active':
        beta = case.original['beta']
        definition = prompt_loader.load_agent(OrgPaths(root=beta.root), 'dev_agent')
        # Fixture corruption represents an outstanding consumer that is no
        # longer canonical; its authenticated captured operation remains.
        (beta.root / 'org/agents/dev_agent.md').write_text(render_agent_text(replace(definition, executor='claude')))
        if variant == 'unbound-absent-canonical':
            with beta.db._lock:
                beta.db._conn.execute("UPDATE workflow_profile_dependencies SET state='unbound' WHERE profile_name='startup-p'")
                beta.db._conn.commit()
        baseline['beta'] = _startup_rows(beta)
    case.classify(state)
    case.held(state, ('alpha', 'beta', 'gamma'))
    assert state.get_org('delta') is case.original['delta']
    from runtime.workflows.profile_coordinator import ProfileCoordinatorError
    for name in _STARTUP_PROFILES:
        with pytest.raises(ProfileCoordinatorError):
            with state.profile_coordinator.operation((name,), operation_kind='rebind', publisher='forbidden-contender'):
                pytest.fail('held profile writer entered its business body')
    for slug in ('alpha', 'beta', 'gamma'):
        after = _startup_rows(case.original[slug])
        _startup_record(case.request, 'closure-readback', variant=variant, slug=slug, before=baseline[slug], after=after)
        _startup_original_unchanged(baseline[slug], after)


@pytest.mark.parametrize('variant', ['flock', 'ordinary-outside-lease', 'durable-unknown'])
def test_startup_isolation_writer_custody(startup_case: _StartupCase, variant: str) -> None:
    from runtime.workflows.profile_coordinator import ProfileCoordinatorError
    case = startup_case
    case.seed({'alpha': 'startup-p', 'beta': None})
    state = case.boot()
    alpha = case.original['alpha']
    if variant == 'durable-unknown':
        before = case.ordinary(alpha)
        case.classify(state)
        case.held(state, ('alpha',))
        _startup_original_unchanged(before, _startup_rows(alpha))
        return
    if variant == 'flock':
        import subprocess
        import sys
        # Separate OS process owns the real stable flock, not a Python lock mock.
        path = state.profile_coordinator._lock_path('startup-p')
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        process = subprocess.Popen([sys.executable, '-c',
            'import fcntl,os,sys; f=os.open(sys.argv[1],os.O_RDWR|os.O_CREAT,0o600);'
            'fcntl.flock(f,fcntl.LOCK_EX); print("owned",flush=True);'
            'sys.stdin.readline(); fcntl.flock(f,fcntl.LOCK_UN); os.close(f)', str(path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            import select
            assert select.select([process.stdout], [], [], 5)[0], 'owned flock not established'
            assert process.stdout.readline() == 'owned\n'
            before = _startup_rows(alpha)
            with pytest.raises(ProfileCoordinatorError, match='profile_coordinator_busy'):
                with state.profile_coordinator.operation(('startup-p',), operation_kind='rebind', publisher='flock-contender'):
                    pytest.fail('real flock was bypassed')
            refusal = None
            try:
                case.classify(state)
            except AssertionError as exc:
                refusal = exc
            observed = _startup_rows(alpha)
            _startup_original_unchanged(before, observed)
            _startup_record(case.request, 'flock-O-closed-W-owned', rows=observed)
            if refusal is not None:
                raise refusal
            case.held(state, ('alpha',))
            _startup_record(case.request, 'flock-competing-return', owner_pid=process.pid, rows=_startup_rows(alpha))
        finally:
            stdout, stderr = process.communicate('\n', timeout=10)
            _startup_record(case.request, 'flock-owner-return', exit=process.returncode, stdout=stdout, stderr=stderr)
            assert process.returncode == 0
        return
    async def ordinary() -> None:
        paused, release = asyncio.Event(), asyncio.Event()
        original: dict = {}
        async def writer() -> None:
            async with alpha.workflow_authority.async_writer_interval(publisher='startup-owned-W') as interval:
                with interval.canonical_change():
                    alpha.db.upsert_org_setting('startup_case_owner', '"paused-W"')
                original.update(_startup_rows(alpha))
                paused.set()
                await release.wait()
        w = asyncio.create_task(writer(), name='startup-original-W')
        try:
            await asyncio.wait_for(paused.wait(), 5)
            assert original['workflow_publication_leases'] == [] and not w.done()
            async def competitor() -> None:
                with pytest.raises(ProfileCoordinatorError, match='profile_consumer_changed'):
                    state.profile_coordinator._capture_org_dependencies(alpha)
            x = asyncio.create_task(competitor(), name='startup-competing-X')
            await x  # ONLY X returns here. W remains paused and owns its interval.
            refusal = None
            try:
                case.classify(state)
            except AssertionError as exc:
                refusal = exc
            observed = _startup_rows(alpha)  # O opens and closes inside this read.
            assert not w.done() and alpha.workflow_authority._async_writer_lock.locked()
            _startup_original_unchanged(original, observed)
            _startup_record(case.request, 'W-held-X-returned-O-closed', rows=observed,
                            native_users=list(map(id, alpha._native_task_users)))
            if refusal is not None:
                raise refusal
            case.held(state, ('alpha',))
        finally:
            release.set()
            await asyncio.wait_for(asyncio.shield(w), 10)
            _startup_record(case.request, 'W-positive-return', rows=_startup_rows(alpha))
    asyncio.run(ordinary())


_STARTUP_INVALID = (
    'members-scalar', 'members-duplicate', 'members-unknown', 'missing-mirror',
    'operation-identity', 'operation-state', 'dependency-reader', 'canonical-missing-dependency',
    'native-malformed', 'pointer-movement', 'dependency-movement',
    'operation-movement', 'native-movement',
)


@pytest.mark.parametrize('variant', _STARTUP_INVALID)
def test_startup_isolation_invalid_evidence(startup_case: _StartupCase, variant: str) -> None:
    from runtime.orchestrator.producer_move import native_writer
    from runtime.workflows.profile_coordinator import ProfileCoordinatorError
    case = startup_case
    case.seed({'alpha': 'startup-p', 'beta': 'startup-p', 'gamma': 'startup-q', 'delta': None})
    state = case.boot()
    alpha, beta = case.original['alpha'], case.original['beta']
    coordinator = state.profile_coordinator
    # First establish the healthy exact closure, before the single selected cut.
    assert alpha.workflow_authority.capture_admission().profile_names == ('startup-p',)
    if variant.startswith('members-') or variant in ('missing-mirror', 'operation-identity', 'operation-state', 'operation-movement'):
        _startup_shared_operation(case, state)
    before = {s: _startup_rows(o) for s, o in case.original.items()}
    if variant.startswith('members-'):
        value = {'members-scalar': 'alpha', 'members-duplicate': ['alpha', 'alpha', 'beta'],
                 'members-unknown': ['alpha', 'beta', 'not-attached']}[variant]
        for org in (alpha, beta):
            with org.db._lock:
                org.db._conn.execute('UPDATE workflow_profile_operations SET captured_members=?', (json.dumps(value),))
                org.db._conn.commit()
    elif variant == 'missing-mirror':
        with beta.db._lock:
            beta.db._conn.execute('DELETE FROM workflow_profile_operations')
            beta.db._conn.commit()
    elif variant == 'operation-identity':
        with beta.db._lock:
            beta.db._conn.execute('UPDATE workflow_profile_operations SET target_generation=target_generation+1')
            beta.db._conn.commit()
    elif variant == 'operation-state':
        with beta.db._lock:
            beta.db._conn.execute('PRAGMA ignore_check_constraints=ON')
            beta.db._conn.execute("UPDATE workflow_profile_operations SET state='invalid-fixture-state'")
            beta.db._conn.commit()
            beta.db._conn.execute('PRAGMA ignore_check_constraints=OFF')
    elif variant == 'canonical-missing-dependency':
        with alpha.db._lock:
            alpha.db._conn.execute("DELETE FROM workflow_profile_dependencies WHERE profile_name='startup-p'")
            alpha.db._conn.commit()
        case.ordinary(beta)
    elif variant == 'native-malformed':
        case.ordinary(alpha)
        with alpha.db._lock:
            row = alpha.db._conn.execute("SELECT id FROM audit_log WHERE action='team_move_native_writer' ORDER BY id DESC LIMIT 1").fetchone()
            assert row is not None
            alpha.db._conn.execute('UPDATE audit_log SET payload=? WHERE id=?', ('{"invalid":"native"}', row['id']))
            alpha.db._conn.commit()
    elif variant == 'operation-movement':
        # This distinct final-capture boundary must reject movement itself;
        # an earlier UNKNOWN/group gate cannot supply its negative oracle.
        capture = coordinator._capture_org_dependencies(alpha)
        def validate_selected() -> None:
            with ExitStack() as locks:
                for name in sorted(_STARTUP_PROFILES):
                    locks.enter_context(coordinator.profile_read(name))
                locks.enter_context(alpha.workflow_authority._publisher_lock)
                with alpha.db._lock:
                    coordinator._validate_capture(alpha, capture.revision)
        validate_selected()  # SAME genuine capture's positive validation
        with closing(sqlite3.connect(alpha.db.path)) as mover:
            mover.execute('UPDATE workflow_profile_operations SET target_generation=target_generation+1')
            mover.commit()
        moved = _startup_rows(alpha)
        assert moved['workflow_authority_pointers'] == before['alpha']['workflow_authority_pointers']
        assert moved['workflow_profile_operations'] != before['alpha']['workflow_profile_operations']
        _startup_record(case.request, 'operation-evidence-moved-pointer-stable', before=before['alpha'], after=moved)
        with pytest.raises(ProfileCoordinatorError, match='^profile_consumer_changed$'):
            validate_selected()
    elif variant == 'dependency-reader':
        # SQLite's real selected reader fails, rather than replacing capture.
        def deny(action: int, table: str, column: str, database: str, source: str) -> int:
            if action == sqlite3.SQLITE_READ and table == 'workflow_profile_dependencies':
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        alpha.db._conn.set_authorizer(deny)
    else:
        # A delegating observation at the actual discovery->validation cut.
        # Every invocation moves real selected evidence; existing bounded retry
        # cannot accidentally accept a later static snapshot.
        original = coordinator._capture_org_dependencies
        def moving(org: object, **kwargs: object) -> object:
            capture = original(org, **kwargs)
            if org is alpha:
                if variant == 'pointer-movement':
                    with alpha.workflow_authority.writer_interval(publisher='startup-pointer-movement') as writer:
                        with writer.canonical_change():
                            alpha.db.upsert_org_setting('startup_race', str(time.time_ns()))
                elif variant == 'native-movement':
                    with native_writer(alpha.db, publisher='startup-native-movement'):
                        pass
                else:
                    with alpha.db._lock:
                        if variant == 'dependency-movement':
                            alpha.db._conn.execute("UPDATE workflow_profile_dependencies SET bound_generation=bound_generation+1 WHERE profile_name='startup-p'")
                        else:
                            alpha.db._conn.execute('UPDATE workflow_profile_operations SET target_generation=target_generation+1')
                        alpha.db._conn.commit()
                actual = _startup_rows(alpha)
                fault_rows['alpha'] = actual
                _startup_record(case.request, 'actual-evidence-moved', variant=variant, rows=actual)
            return capture
        case.patch.setattr(coordinator, '_capture_org_dependencies', moving)
    fault_rows: dict = {}
    try:
        if variant != 'dependency-reader':
            fault_rows = {s: _startup_rows(o) for s, o in case.original.items()}
        if variant.startswith('members-') or variant in ('missing-mirror', 'operation-identity', 'operation-state'):
            invocation = next(r['coordinator_invocation'] for row in fault_rows.values()
                              for r in row['workflow_profile_operations'])
            with pytest.raises(ProfileCoordinatorError) as selected_refusal:
                coordinator._load_group_operations(invocation, _STARTUP_PROFILES)
            _startup_record(case.request, 'selected-closed-reader-refusal', variant=variant,
                            code=selected_refusal.value.code)
        if variant == 'native-malformed':
            from runtime.infrastructure.producer_admission import NativeWriterStore
            with alpha.db._lock, pytest.raises(ValueError):
                NativeWriterStore(alpha.db, alpha.slug).history_uncommitted()
            _startup_record(case.request, 'selected-native-reader-refusal', rows=_startup_rows(alpha))
        try:
            coordinator.reconcile_startup()
        except (ProfileCoordinatorError, sqlite3.DatabaseError, ValueError, KeyError) as exc:
            _startup_record(case.request, 'invalid-startup-escaped', variant=variant, error=repr(exc))
            raise AssertionError('invalid evidence must be contained with conservative refusal: ' + variant) from exc
    finally:
        if variant == 'dependency-reader':
            alpha.db._conn.set_authorizer(None)
        _startup_record(case.request, 'invalid-readback', variant=variant, baseline=before,
                        fault_rows=fault_rows, after={s: _startup_rows(o) for s, o in case.original.items()})
    case.held(state, ('alpha', 'beta'))
    for slug in ('alpha', 'beta'):
        _startup_original_unchanged(fault_rows.get(slug, before[slug]), _startup_rows(case.original[slug]))
    assert 'alpha' in state.broken_orgs
    if variant.startswith('members-') or variant in ('missing-mirror', 'operation-identity', 'operation-state', 'operation-movement'):
        case.held(state, ('gamma',))
    # A swallowed failed reader/unknown operation is not independent gamma.
    if variant in ('members-scalar', 'members-unknown', 'dependency-reader', 'native-malformed'):
        case.held(state, ('gamma', 'delta'))


def _startup_final_read(org: object, capture: object) -> None:
    """Read-only SQL boundary, separately attributed from earlier public gates."""
    authority = org.workflow_authority
    coordinator = authority._profile_coordinator
    with ExitStack() as locks:
        for name in sorted(capture.profile_names):
            locks.enter_context(coordinator.profile_read(name))
        locks.enter_context(authority._publisher_lock)
        with org.db._lock:
            conn = org.db._conn
            conn.execute('BEGIN')
            try:
                authority._validate_admission_uncommitted(conn, capture)
            finally:
                conn.rollback()


@pytest.mark.parametrize('boundary', ['lookup', 'retained-native', 'public-workflow', 'final-sql',
                                       'thread', 'schedule', 'dream', 'work-hours'])
def test_startup_isolation_admission(startup_case: _StartupCase, boundary: str,
                                    fake_claude_thread_plan_env: Path) -> None:
    from runtime.workflows.authority import WorkflowAuthorityError
    case = startup_case
    case.seed({'alpha': 'startup-p', 'beta': 'startup-p', 'gamma': None})
    state = case.boot()
    beta, gamma = case.original['beta'], case.original['gamma']
    capture = beta.workflow_authority.capture_admission()  # genuine, SAME object, before hold
    _startup_final_read(beta, capture)  # exact same-object positive SQL control
    async def positive_native() -> None:
        token = object()
        assert beta._acquire_native_task(token) is True and token in beta._native_task_users
        beta._release_native_task(token)  # acquired token, released exactly once
        assert token not in beta._native_task_users
    asyncio.run(positive_native())
    if boundary in ('thread', 'schedule', 'dream', 'work-hours'):
        _startup_producer_pass(case, state, beta, gamma, boundary, fake_claude_thread_plan_env)
        return
    if boundary in ('public-workflow', 'final-sql'):
        _startup_workflow_control(case, state, gamma)
    before = _startup_rows(beta)
    case.ordinary(case.original['alpha'])
    case.classify(state)
    case.held(state, ('alpha', 'beta'))
    _startup_record(case.request, 'C6-preheld-capture', boundary=boundary,
                    capture=repr(capture), before=before, held=_startup_rows(beta))
    if boundary == 'lookup':
        from runtime.daemon.dispatcher import Dispatcher
        async def shipping_lookup() -> None:
            dispatcher = Dispatcher(state)
            assert dispatcher.task_attachment('beta') is None
            native_before = set(state.queue._native_work)
            assert state.queue._submit(dispatcher, ('beta', 'TASK-001', None)) is None
            assert set(state.queue._native_work) == native_before
        asyncio.run(shipping_lookup())
        with _startup_http(case, state) as port:
            response = httpx.post(_org_base(port, 'beta') + '/tasks', headers=_auth_headers(),
                                  json={'type': 'general', 'brief': 'held route'})
            assert response.status_code == 404 and 'unknown_org' in response.text
            task = _submit_task(port, 'gamma', 'C6 actual shipping queue control')
            _wait_for_completed(port, 'gamma', task, 30)
    elif boundary == 'retained-native':
        async def retained() -> None:
            refused = object()
            users, runners = set(beta._native_task_users), set(beta._runner_tasks)
            assert beta._acquire_native_task(refused) is False
            assert refused not in beta._native_task_users
            async def runner() -> None:
                with beta._runner_lifetime() as admitted:
                    assert admitted is False
            await asyncio.create_task(runner())
            assert set(beta._native_task_users) == users and set(beta._runner_tasks) == runners
        asyncio.run(retained())
    elif boundary == 'public-workflow':
        for read in (beta.workflow_authority.verify_admission_ready, beta.workflow_authority.capture_admission):
            with pytest.raises(WorkflowAuthorityError, match='^authority_pointer_not_ready$'):
                read()
        from runtime.workflows.profile_coordinator import ProfileCoordinatorError
        with pytest.raises((WorkflowAuthorityError, ProfileCoordinatorError)) as refusal:
            with beta.workflow_authority.admission_writer(capture):
                pytest.fail('held public admission writer yielded a business connection')
        _startup_record(case.request, 'public-earliest-refusal', error=repr(refusal.value),
                        claim='earliest gate only; no final-SQL reach claim')
    elif boundary == 'final-sql':
        with pytest.raises(WorkflowAuthorityError, match='^authority_pointer_not_ready$'):
            _startup_final_read(beta, capture)
        # KeyError, profile denial, stale/malformed capture are NOT the oracle.
        _startup_original_unchanged(before, _startup_rows(beta))
    after = _startup_rows(beta)
    assert after['tasks'] == before['tasks'] and after['task_results'] == before['task_results']
    for table in ('org_settings', 'workflow_activations', 'workflow_draft_dispatch_intents', 'workflow_contexts',
                  'workflow_instances', 'workflow_binding_snapshots', 'workflow_authorization_revisions'):
        assert after[table] == before[table], table
    _startup_original_unchanged(before, after)
    _startup_record(case.request, 'C6-independent-readback', boundary=boundary, before=before, after=after)


def _startup_workflow_control(case: _StartupCase, state: object, org: object) -> None:
    """Real supported activation -> admission_writer -> domain-store COMMIT.

    Fresh fixture schema, real local bearer route and canonical principals.
    No manufactured task/SID/result, no replaced capture, no guard bypass.
    The author intent is observed queued; this is not an execution claim.
    """
    from fastapi.testclient import TestClient
    from runtime.daemon.app import create_app
    client = TestClient(create_app(state))
    client.headers.update(_auth_headers())
    base = '/api/v1/orgs/' + org.slug
    definition = dict(kind='product-design', schema_version=1,
        description='Startup isolation document admission control',
        author=dict(role='product-lead', kind='agent', artifact='immutable-prd-revision'),
        reviewers=[dict(role='founder', kind='human'), dict(role='implementer', kind='agent'), dict(role='tester', kind='agent')],
        approval=dict(mode='all', revision='current', required_roles=['founder', 'implementer', 'tester']),
        request_changes=dict(action='return-to-author'))
    try:
        published = client.post(base + '/workflows/templates/publish', json=dict(
            operation_key='startup-control-template', team_slug='engineering', template_name='startup-control',
            expected_current_version=0, definition=definition))
        assert published.status_code == 201, published.text
        template = published.json()
        enabled = client.post(base + '/workflows/cutover/requests', json=dict(
            operation_key='startup-synthetic-enable', action='enable', expected_generation=1))
        assert enabled.status_code == 200 and enabled.json()['state'] == 'enabled', enabled.text
        ready = org.workflow_authority.capture_admission().ready
        body = dict(operation_key='startup-control-activation', instance_id='startup-control', expected_activation_revision=0,
            template={key: template[key] for key in ('identity_id', 'version', 'definition_digest')},
            authority=dict(namespace=ready.namespace, generation=ready.generation, snapshot_digest=ready.snapshot_digest),
            scope=dict(brief='Synthetic bounded document admission control.'),
            bindings={'product-lead': dict(kind='agent', principal='engineering_head', team='engineering'),
                'founder': dict(kind='human', principal='founder', team=None),
                'implementer': dict(kind='agent', principal='dev_agent', team='engineering'),
                'tester': dict(kind='agent', principal='code_reviewer', team='engineering')},
            eligible_replacements={role: [] for role in ('product-lead', 'founder', 'implementer', 'tester')},
            allowed_actions=['draft-document', 'submit-immutable-document', 'collect-review', 'approve-planning-input', 'return-to-author'], inputs=[])
        response = client.post(base + '/workflows/activations', json=body)
        assert response.status_code == 201, response.text
        receipt = response.json()
        rows = _startup_rows(org)
        task = next(r for r in rows['tasks'] if r['id'] == receipt['root_task_id'])
        intent = next(r for r in rows['workflow_draft_dispatch_intents'] if r['id'] == receipt['intent_id'])
        context = next(r for r in rows['workflow_contexts'] if r['id'] == intent['context_id'])
        assert task['assigned_agent'] == 'engineering_head' and task['status'] == 'pending'
        assert intent['state'] == 'queued' and intent['session_id'] is None and intent['host_launch_started'] == 0
        assert hashlib.sha256(context['context_bytes']).hexdigest() == context['context_digest'] == receipt['context_digest']
        assert json.loads(context['context_bytes'])['authority_snapshot'] == json.loads(ready.snapshot_bytes)
        assert rows['task_results'] == []
        _startup_record(case.request, 'workflow-business-committed', receipt=receipt, rows=rows)
    finally:
        client.close()


def _startup_producer_inputs(case: _StartupCase, orgs: tuple, boundary: str, *, selected_now: datetime | None = None) -> dict:
    from runtime.models import ScheduleKind, ScheduleRecord
    from runtime.daemon.routes.threads import _create_agent_thread_locked
    now = selected_now or datetime.now(timezone.utc)
    inputs = {'now': now.isoformat(), 'timezone': 'UTC', 'orgs': {}}
    for org in orgs:
        if boundary == 'thread':
            async def compose() -> tuple:
                async with org.db_lock:
                    return _create_agent_thread_locked(org, composer='dev_agent',
                        subject='startup eligible thread', body_text='@engineering_head synthetic reply',
                        recipients=['engineering_head'], turn_cap=8)
            thread, seq, tokens, agents = asyncio.run(compose())
            assert len(tokens) == 1 and agents == ['engineering_head']
            from runtime.daemon.thread_queue import ThreadJob
            asyncio.run(org.thread_queue.put(ThreadJob(org_slug=org.slug, invocation_token=tokens[0])))
            invocation = org.db.get_pending_invocation(tokens[0])
            assert invocation is not None and invocation.thread_id == thread and invocation.triggering_seq == seq
            assert org.thread_queue.size == 1
            inputs['orgs'][org.slug] = {'thread': thread, 'seq': seq, 'token': tokens[0]}
        elif boundary == 'schedule':
            record = ScheduleRecord(id=org.db.schedules.next_id(), agent_name='engineering_head',
                kind=ScheduleKind.ONE_SHOT, fire_at=now - timedelta(seconds=1),
                normalized_brief='startup eligible schedule', source_instruction='synthetic fixture')
            org.db.schedules.insert(record)
            assert org.db.schedules.get(record.id).status.value == 'armed'
            assert any(r.id == record.id for r in org.db.schedules.list_due(now))
            inputs['orgs'][org.slug] = {'schedule': record.id}
        else:
            config = ({'enabled': True, 'schedule': {'time': '00:00', 'timezone': 'UTC',
                       'catch_up_on_startup': True}, 'agents': {'mode': 'whitelist', 'include': ['engineering_head']}}
                      if boundary == 'dream' else
                      {'enabled': True, 'default': {'mode': 'continuous', 'interval': '1h',
                       'timezone': 'UTC', 'catch_up_on_startup': True},
                       'agents': {'mode': 'whitelist', 'include': ['engineering_head']}})
            org.db.upsert_org_setting('dreaming' if boundary == 'dream' else 'working_hours', json.dumps(config))
            if boundary == 'dream':
                from runtime.daemon.dream_scheduler import _available_agents, _scheduled_datetime, select_dream_agents
                from runtime.orchestrator.org_config import DreamingConfig, resolve_org_setting_dreaming
                resolved = resolve_org_setting_dreaming(org.db, code_default=DreamingConfig())
                assert select_dream_agents(_available_agents(org), resolved) == ['engineering_head']
                date, scheduled = _scheduled_datetime(now, resolved)
                assert scheduled <= now and org.db.get_dream_for_agent_date('engineering_head', date) is None
            else:
                from runtime.daemon.work_hours_scheduler import current_due_slot, _agent_routines
                from runtime.orchestrator.org_config import WorkingHoursConfig, resolve_org_setting_working_hours
                resolved = resolve_org_setting_working_hours(org.db, code_default=WorkingHoursConfig())
                assert resolved.enabled and _agent_routines(org, 'engineering_head').has_wake
                due = current_due_slot(resolved.resolve_for('engineering_head', 'engineering'), now)
                assert due is not None
                date, slot, scheduled = due
                assert org.db.work_hours.get_for_agent_date_slot('engineering_head', date, slot) is None
            inputs['orgs'][org.slug] = {'config': config, 'resolved': repr(resolved), 'scheduled': scheduled.isoformat(), 'rows': _startup_rows(org)}
    _startup_record(case.request, 'eligible-producer-inputs', boundary=boundary, inputs=inputs)
    return inputs


def _startup_producer_pass(case: _StartupCase, state: object, held: object, healthy: object,
                           boundary: str, thread_plan: Path) -> None:
    from runtime.daemon import dream_scheduler, schedule_scheduler, work_hours_scheduler, thread_queue
    from runtime.daemon.thread_runner import run_invocation
    table, queue_name = {'thread': ('thread_invocations', 'thread_queue'),
        'schedule': ('schedules', 'schedule_queue'), 'dream': ('dreams', 'dream_queue'),
        'work-hours': ('work_hours', 'wake_queue')}[boundary]
    # Establish C's OWN real positive transition before classification can
    # refuse on pre-fix code. B gets matching eligible input BEFORE the hold.
    # The SAME actual loop pauses between passes; no startup restart/rerun.
    controls = _startup_producer_inputs(case, (healthy,), boundary)
    now = datetime.fromisoformat(controls['now'])
    thread_plan.write_text('#!/bin/bash\nset -eu\n' +
        'thread_id=$1; token=$2; agent=$3; org_slug=$4; purpose=$5\n' +
        'payload="$PWD/startup-thread-$token.json"\n' +
        'printf \'{"thread_id":"%s","invocation_token":"%s","speaker":"%s","body_markdown":"startup synthetic reply","in_response_to_seq":1}\' "$thread_id" "$token" "$agent" > "$payload"\n' +
        'happyranch threads reply --org "$org_slug" --thread-id "$thread_id" --from-file "$payload"\n')
    thread_plan.chmod(0o755)
    if boundary != 'thread':
        module = {'schedule': schedule_scheduler, 'dream': dream_scheduler, 'work-hours': work_hours_scheduler}[boundary]
        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz: object = None) -> datetime:
                return now if tz is None else now.astimezone(tz)
        case.patch.setattr(module, 'datetime', FixedDateTime)
    async def execute() -> None:
        first_end, second_end, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        task_name = 'startup-' + boundary
        real_sleep = asyncio.sleep
        async def pause_between_passes(delay: float, result: object = None) -> object:
            if asyncio.current_task().get_name() == task_name and delay in (0.05, 3600):
                if not first_end.is_set():
                    first_end.set()
                    await release.wait()
                    return await real_sleep(0, result)
                second_end.set()
            return await real_sleep(delay, result)
        # This observes only the real end-of-pass await; no business result,
        # registry, dispatcher or membership is supplied by the observer.
        case.patch.setattr(asyncio, 'sleep', pause_between_passes)
        if boundary == 'thread':
            coroutine = thread_queue.thread_worker_loop(state, state.settings)
        else:
            coroutine = getattr(module, {'schedule': 'schedule_scheduler_loop', 'dream': 'dream_scheduler_loop',
                'work-hours': 'work_hours_scheduler_loop'}[boundary])(state, interval_seconds=3600)
        loop = asyncio.create_task(coroutine, name=task_name)
        try:
            await asyncio.wait_for(first_end.wait(), 30)
            positive = _startup_rows(healthy)
            if boundary == 'thread':
                own = [r for r in positive[table] if r['invocation_token'] == controls['orgs'][healthy.slug]['token']]
                assert len(own) == 1 and own[0]['status'] == 'consumed' and own[0]['started_at'] is not None
                assert own[0]['reply_message_seq'] is not None
                assert healthy.thread_queue.size == 0
            else:
                assert len(positive[table]) == 1 and positive[table][0]['status'] == ('firing' if boundary == 'schedule' else 'pending')
                assert getattr(healthy, queue_name).size == 1
                action = {'schedule': 'schedule_claimed', 'dream': 'dream_scheduled', 'work-hours': 'work_hour_scheduled'}[boundary]
                assert any(r['action'] == action for r in positive['audit_log'])
            _startup_record(case.request, 'producer-healthy-positive-before-hold', boundary=boundary, rows=positive)
            # Reuse the existing fixture writer from a native thread while the
            # actual loop is paused outside every durable ownership scope.
            inputs = await asyncio.to_thread(_startup_producer_inputs, case, (held,), boundary, selected_now=now)
            before = _startup_rows(held)
            case.ordinary(case.original['alpha'])
            case.classify(state)
            case.held(state, ('alpha', 'beta'))
            if boundary == 'thread':
                # Direct retained-object runner denial is distinct from the
                # shipping snapshot loop retaining B's eligible queued token.
                await run_invocation(org_state=held, invocation_token=inputs['orgs'][held.slug]['token'], settings=state.settings)
                assert _startup_rows(held)[table] == before[table]
            release.set()
            await asyncio.wait_for(second_end.wait(), 10)
            await real_sleep(0)  # queued production put tasks return on this loop
            after = _startup_rows(held)
            _startup_record(case.request, 'producer-held-second-pass', boundary=boundary,
                            held_before=before, held_after=after, positive=positive)
            assert after[table] == before[table] and after['audit_log'] == before['audit_log']
            assert after['tasks'] == before['tasks'] and after['task_results'] == before['task_results']
            _startup_original_unchanged(before, after)
            assert getattr(held, queue_name).size == (1 if boundary == 'thread' else 0)
        finally:
            loop.cancel()
            result = await asyncio.gather(loop, return_exceptions=True)
            _startup_record(case.request, 'producer-loop-returned', boundary=boundary, result=[repr(r) for r in result])
            assert isinstance(result[0], asyncio.CancelledError), result
            # Cancellation of the scan is no native-return proof. Actual
            # run_invocation has returned before first_end, with no held users.
            assert not held._runner_tasks and not healthy._runner_tasks
            assert not held._native_task_users and not healthy._native_task_users
    with _startup_http(case, state, tasks=False):
        asyncio.run(execute())


def _startup_other_custody(case: _StartupCase, state: object, tail: str) -> None:
    from runtime.daemon.org_state import OrgState, _OrgBusyError
    from runtime.daemon.routes.runtime import _assert_can_swap
    if tail == 'stale-capture':
        original = case.original['alpha']
        async def remove() -> None:
            with state.capture_orgs(('alpha',), original=original):
                with pytest.raises(_OrgBusyError, match='attachment_readers_in_flight'):
                    await state.remove_org('alpha')
                assert original.db._conn.execute('SELECT 1').fetchone()[0] == 1
            await state.remove_org('alpha')
            case.closed.add(id(original))
            with pytest.raises(sqlite3.ProgrammingError):
                original.db._conn.execute('SELECT 1')
            with pytest.raises(_OrgBusyError, match='attachment_changed_during_read'):
                with state.capture_orgs(('alpha',), original=original):
                    pytest.fail('stale original was resolved through its slug')
            with pytest.raises(KeyError):
                state.get_org('alpha')
        asyncio.run(remove())
        _startup_record(case.request, 'stale-original-refused', identity=id(original))
        return
    if tail == 'shutdown-close':
        original_close = OrgState.close
        primary = RuntimeError('startup-shutdown-original-close')
        def fail_later(org: object) -> None:
            if org is case.original['beta']:
                case.failed_close.add(id(org))
                raise primary
            original_close(org)
            case.closed.add(id(org))
        case.patch.setattr(OrgState, 'close', fail_later)
        with pytest.raises(RuntimeError) as caught:
            asyncio.run(state.close_all())
        assert caught.value is primary and state._shutdown_complete is False
        assert id(case.original['alpha']) in case.closed
        assert case.original['beta'].db._conn.execute('SELECT 1').fetchone()[0] == 1
        for slug, org in case.original.items():
            with pytest.raises(_OrgBusyError, match='attachment_retiring'):
                with state.capture_orgs((slug,), original=org):
                    pytest.fail('failed shutdown reopened attachment capture')
            async def retained() -> None:
                token = object()
                assert org._acquire_native_task(token) is False and token not in org._native_task_users
            asyncio.run(retained())
        _startup_record(case.request, 'shutdown-incomplete-original-owned', positive_closed=sorted(case.closed),
                        unknown=sorted(case.failed_close), first_error=repr(caught.value))
        return
    case.seed({'gamma': 'startup-p'})
    if tail == 'dynamic-attachment':
        from runtime.workflows.profile_coordinator import ProfileCoordinatorError
        primary, secondary = ProfileCoordinatorError('profile_consumer_changed'), RuntimeError('startup-unpublished-close')
        real_validate = state.profile_coordinator._validate_capture
        real_close = OrgState.close
        def failed_validation(org: object, revision: object) -> None:
            real_validate(org, revision)
            if org.slug == 'gamma':
                case.all_handles[id(org)] = org
                raise primary
        def failed_close(org: object) -> None:
            if org.slug == 'gamma':
                case.failed_close.add(id(org))
                raise secondary
            real_close(org)
            case.closed.add(id(org))
        case.patch.setattr(state.profile_coordinator, '_validate_capture', failed_validation)
        case.patch.setattr(OrgState, 'close', failed_close)
        with pytest.raises(ProfileCoordinatorError) as caught:
            asyncio.run(state.add_org('gamma'))
        assert caught.value is primary
        owners = [o for o in state._attachment_owners.values() if o['slug'] == 'gamma']
        assert len(owners) == 1 and owners[0]['primary'] is primary and owners[0]['close_error'] is secondary
        org = owners[0]['org']
        assert org is not None and org.db._conn.execute('SELECT 1').fetchone()[0] == 1
        assert 'gamma' not in state.orgs_snapshot()
        with pytest.raises(_OrgBusyError, match='attachment_native_in_flight_or_unsafe'):
            state._assert_attachment_loads_idle()
        _startup_record(case.request, 'dynamic-owner-retained', owner=repr(owners[0]), rows=_startup_rows(org))
        return
    # Real submission is made; only delivery of its handle to the caller fails.
    # Join the original worker and observe its native returned Event, then keep
    # the original uncertain ticket/handle. No release, retry or second close.
    async def lost_submission() -> None:
        loop = asyncio.get_running_loop()
        real_submit = loop.run_in_executor
        futures: list = []
        primary = RuntimeError('startup-original-native-submission-unknown')
        def submit(executor: object, function: Callable, *args: object) -> object:
            future = real_submit(executor, function, *args)
            futures.append(future)
            raise primary
        with case.patch.context() as patch:
            patch.setattr(loop, 'run_in_executor', submit)
            with pytest.raises(RuntimeError) as caught:
                await state.add_org('gamma')
            assert caught.value is primary
        assert len(futures) == 1
        await asyncio.shield(futures[0])
        workers = list(state._attachment_workers.values())
        assert len(workers) == 1 and workers[0]['returned'].is_set()
        owner = workers[0]['owner']
        assert owner['primary'] is primary and owner['org'] is not None
        org = owner['org']
        case.all_handles[id(org)] = org
        case.retained_unknown.add(id(org))  # caller's ownership/retirement remains unknown
        assert org.db._conn.execute('SELECT 1').fetchone()[0] == 1
        assert 'gamma' not in state.orgs_snapshot()
        with pytest.raises(_OrgBusyError, match='attachment_native_in_flight_or_unsafe'):
            state._assert_attachment_loads_idle()
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as refused:
            _assert_can_swap(state)
        assert refused.value.status_code == 409
        _startup_record(case.request, 'native-original-ticket-retained', returned=True,
                        owner=repr(owner), ticket_count=len(workers), rows=_startup_rows(org))
    asyncio.run(lost_submission())


@pytest.mark.parametrize('tail', ['old-close', 'loaded-candidate', 'successful-transfer', 'active-reader',
                                'dynamic-attachment', 'shutdown-close', 'unknown-native', 'stale-capture'])
def test_startup_isolation_failure_custody(startup_case: _StartupCase, tail: str) -> None:
    from fastapi.testclient import TestClient
    from runtime.daemon.app import create_app
    from runtime.daemon.org_state import OrgState, _OrgBusyError
    from runtime.daemon.state import DaemonState
    from runtime.workflows.profile_coordinator import ProfileCoordinator
    case = startup_case
    case.seed({'alpha': None, 'beta': None})
    state = case.boot()
    if tail in ('dynamic-attachment', 'shutdown-close', 'unknown-native', 'stale-capture'):
        _startup_other_custody(case, state, tail)
        return
    originals = dict(case.original)
    closes: list = []
    candidates: list = []
    transferred: dict = {}
    real_close, real_post = OrgState.close, DaemonState.__post_init__
    real_reconcile = ProfileCoordinator.reconcile_startup
    primary = RuntimeError('startup-original-close' if tail == 'old-close' else 'startup-original-construction')
    cleanup = RuntimeError('startup-secondary-close')
    def observe_candidate(candidate: object) -> None:
        real_post(candidate)
        candidates.append(candidate)
    def close(org: object) -> None:
        if tail == 'old-close' and org is originals['beta']:
            case.failed_close.add(id(org))
            raise primary
        if tail == 'loaded-candidate' and org not in originals.values():
            case.failed_close.add(id(org))
            raise cleanup
        assert id(org) not in case.closed, 'positive close repeated'
        real_close(org)
        with pytest.raises(sqlite3.ProgrammingError):
            org.db._conn.execute('SELECT 1')
        case.closed.add(id(org))
        closes.append(id(org))
    def reconcile(coordinator: object) -> None:
        if tail == 'loaded-candidate':
            case.all_handles.update({id(o): o for o in coordinator.orgs.values()})
            case.retained_unknown.update(id(o) for o in coordinator.orgs.values())
            assert len(coordinator.orgs) == 2  # actual loaded candidate handles
            raise primary
        if tail == 'successful-transfer':
            coordinator.synchronize_all_dependencies()
            transferred.update(coordinator.orgs)
            case.all_handles.update({id(o): o for o in coordinator.orgs.values()})
            case.ordinary(coordinator.orgs['alpha'])
        real_reconcile(coordinator)
    case.patch.setattr(OrgState, 'close', close)
    case.patch.setattr(DaemonState, '__post_init__', observe_candidate)
    case.patch.setattr(ProfileCoordinator, 'reconcile_startup', reconcile)
    client = TestClient(create_app(state), raise_server_exceptions=True)
    client.headers.update(_auth_headers())
    if tail == 'active-reader':
        with state.capture_orgs(('alpha',), original=originals['alpha']):
            response = client.post('/api/v1/runtime', json={'path': str(case.container)})
            assert response.status_code == 409 and 'attachment_readers_in_flight' in response.text
        assert closes == [] and candidates == []
        assert originals['alpha'].db._conn.execute('SELECT 1').fetchone()[0] == 1
        client.close()
        return
    if tail == 'successful-transfer':
        response = client.post('/api/v1/runtime', json={'path': str(case.container)})
    else:
        with pytest.raises(RuntimeError) as refusal:
            client.post('/api/v1/runtime', json={'path': str(case.container)})
        assert refusal.value is primary
        response = None
    _startup_record(case.request, 'runtime-route-after-finally', tail=tail, status=(response.status_code if response is not None else None),
                    body=(response.text if response is not None else repr(refusal.value)), closed=closes, candidate_count=len(candidates),
                    swap_pending=state._attachment_swap_pending,
                    owners=repr(state._attachment_owners))
    if tail == 'successful-transfer':
        assert response.status_code == 200, response.text
        assert len(closes) == len(originals)
        assert candidates and state.profile_coordinator is candidates[-1].profile_coordinator
        callback = state.profile_coordinator._attachment_capture
        assert callback.__self__ is state, 'profile capture retained discarded candidate owner'
        with callback() as actual:
            assert actual['alpha'] is transferred['alpha']
            assert actual['beta'] is state.get_org('beta')
            assert 'alpha' not in state.orgs_snapshot()
        case.states.append(candidates[-1])
        client.close()
        return
    assert response is None, 'failure was observed as the original raised route exception'
    if tail == 'old-close':
        assert closes == [id(originals['alpha'])] and candidates == []
        # Failure branch refuses BEFORE calling the real later close. Observe
        # actual usability independently; no fabricated positive retirement.
        assert originals['beta'].db._conn.execute('SELECT 1').fetchone()[0] == 1
    else:
        assert set(closes) == {id(o) for o in originals.values()}
        assert len(candidates) == 1 and len(candidates[0].orgs) == 2
        candidate = candidates[0]
        # Retained original construction owner must be reachable from the
        # published state, not just this observer's local reference.
        def owns(value: object, target: object, depth: int = 0) -> bool:
            if value is target:
                return True
            if depth > 6:
                return False
            if isinstance(value, dict):
                return any(owns(v, target, depth + 1) for v in value.values())
            if isinstance(value, (tuple, list, set)):
                return any(owns(v, target, depth + 1) for v in value)
            return False
        candidate_owned = owns(vars(state), candidate) or all(
            owns(vars(state), org) for org in candidate.orgs.values())
        _startup_record(case.request, 'candidate-custody', owned=candidate_owned,
                        first_error=repr(primary), secondary=repr(cleanup),
                        handles=[{'slug': o.slug, 'usable': o.db._conn.execute('SELECT 1').fetchone()[0]}
                                 for o in candidate.orgs.values()])
        assert candidate_owned, 'failed loaded candidate lost original construction custody'
        assert owns(vars(state), primary) and owns(vars(state), cleanup), 'original owner lost primary/secondary error identities'
        assert any(id(o) in case.failed_close for o in candidate.orgs.values()), 'secondary close cut never reached'
    # All assertions below happen AFTER the route finally, including native and
    # a real scheduler pass. They cannot be supplied by the failed HTTP alone.
    for slug, original in originals.items():
        with pytest.raises(_OrgBusyError, match='^attachment_retiring$'):
            with state.capture_orgs((slug,), original=original):
                pytest.fail('post-failure old attachment capture admitted')
        response = client.get('/api/v1/orgs/' + slug + '/agents')
        assert response.status_code == 409 and 'attachment_retiring' in response.text
        try:
            returned = state.get_org(slug)
        except (KeyError, _OrgBusyError):
            returned = None
        assert returned is None, 'closed/retiring old object returned after failed swap'
        async def denied_native() -> None:
            token = object()
            assert original._acquire_native_task(token) is False
            assert token not in original._native_task_users
        asyncio.run(denied_native())
    assert not any(o in originals.values() for o in state.orgs_snapshot().values())
    from runtime.daemon.schedule_scheduler import schedule_scheduler_loop
    real_tick = state.metrics_registry.record_loop_tick
    async def producer_after_finally() -> None:
        reached = asyncio.Event()
        def tick(*args: object, **kwargs: object) -> object:
            result = real_tick(*args, **kwargs)
            reached.set()
            return result
        case.patch.setattr(state.metrics_registry, 'record_loop_tick', tick)
        loop = asyncio.create_task(schedule_scheduler_loop(state, interval_seconds=3600))
        try:
            await asyncio.wait_for(reached.wait(), 5)
        finally:
            loop.cancel()
            result = await asyncio.gather(loop, return_exceptions=True)
            assert isinstance(result[0], asyncio.CancelledError), result
    asyncio.run(producer_after_finally())
    assert len(closes) == (1 if tail == 'old-close' else 2)
    client.close()


def test_startup_isolation_status_repeat(startup_case: _StartupCase) -> None:
    from runtime.workflows.authority import WorkflowAuthorityError
    case = startup_case
    case.seed({'alpha': 'startup-p', 'beta': 'startup-p', 'gamma': None})
    baseline: dict = {}
    def prepared(coordinator: object) -> None:
        baseline.update(case.ordinary(case.original['alpha']))
    first = case.boot(prepared)
    alpha_root = case.original['alpha'].db.path
    for index in (1, 2):
        state = first if index == 1 else case.boot()
        assert set(state.orgs_snapshot()) == {'gamma'}
        assert {'alpha', 'beta'} <= state.broken_orgs.keys()
        with _startup_http(case, state) as port:
            health = httpx.get(_global_base(port) + '/health')
            orgs = httpx.get(_global_base(port) + '/orgs', headers=_auth_headers())
            assert health.status_code == 200 and health.json()['status'] == 'ok'
            assert orgs.status_code == 200 and 'profile_consumer_changed' in orgs.text
            assert state.get_org('gamma').workflow_authority.verify_admission_ready().generation > 0
            with state.profile_coordinator._capture_orgs() as inventory:
                for slug in ('alpha', 'beta'):
                    with pytest.raises(WorkflowAuthorityError, match='^authority_pointer_not_ready$'):
                        inventory[slug].workflow_authority.verify_admission_ready()
        after = _startup_rows(alpha_root)
        _startup_original_unchanged(baseline, after)
        _startup_record(case.request, 'same-state-boot', boot=index, rows=after,
                        http='serving', attachment=list(state.orgs_snapshot()), broken=state.broken_orgs)
        # Positive return of HTTP, queue workers, every locally owned handle and
        # metrics store is a prerequisite to the ONLY second same-state boot.
        case.close()
        assert not case.failed_close

"""Retained connector policy pickup: real policy/readiness/context/stream owners.

Run: PYTHONPATH=$PWD uv run --frozen pytest scripts/test_policy_pickup.py -v --tb=short
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

import pytest

from runtime.remote_access.authorization import TrustState
from runtime.remote_access.diy_provider import DiyProviderConfig
from runtime.remote_access.managed_provider import LINUX_IDENTITY_PROFILE, ManagedProviderConfig
from runtime.remote_access.network import NetworkConfig
from runtime.remote_access.models import RemoteRequest
from runtime.remote_access.state import InMemoryTrustStateStore
from runtime.remote_access.supervisor import ConnectorConfig, ConnectorSupervisor, PolicyLoadError

NOW = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)


class Listener:
    listening = False
    starts = 0
    stops = 0
    def start(self) -> None:
        self.listening = True
        self.starts += 1
    def stop(self) -> None:
        self.listening = False
        self.stops += 1


@pytest.fixture
def live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    token = tmp_path / 'daemon.token'
    token.write_text('synthetic-only')
    token.chmod(0o600)
    path = tmp_path / 'policy.json'
    artifact = json.loads(Path('runtime/remote_access/route_policy.json').read_bytes())
    initial = dict(schema_version=1, artifact_version=artifact['version'], artifact=artifact,
                   issued_at=(NOW - timedelta(seconds=1800)).isoformat(), max_age_seconds=3600,
                   revision=5, state='active')
    path.write_text(json.dumps(initial))
    cfg = ConnectorConfig(tenant_id='synthetic-tenant', home_id='synthetic-home', connector_id='synthetic-connector',
        daemon_port=12345, daemon_token_path=str(token), policy_path=str(path), state_path=str(tmp_path / 'trust.json'),
        diy=DiyProviderConfig(network=NetworkConfig()))
    clock, notifications = [NOW], []
    listener = Listener()
    supervisor = ConnectorSupervisor(config=cfg, provider=listener, now_fn=lambda: clock[0],
                                     notify_fn=lambda message: notifications.append(message) or True)
    store = InMemoryTrustStateStore(TrustState(connector_identity=supervisor._configured_identity(),
                                              pairing_epoch=0, revocation_epoch=0))
    supervisor._state_store = store
    cached = supervisor.load_policy()
    readiness = supervisor.build_readiness()
    readiness._connect_fn = lambda port: None
    supervisor._readiness = readiness
    # A real registry entry makes stream survival/closure an observed effect.
    handle = SimpleNamespace(close=lambda: closed.append(True))
    closed = []
    registry = supervisor.registry
    registry.open('synthetic-stream', handle)
    yield SimpleNamespace(supervisor=supervisor, path=path, initial=initial, cached=cached,
                          listener=listener, closed=closed, clock=clock, notifications=notifications,
                          readiness=readiness, registry=registry)
    supervisor.shutdown()


def publish(live: SimpleNamespace, **changes: object) -> None:
    data = {**live.initial, 'issued_at': NOW.isoformat(), 'revision': 6, **changes}
    pending = live.path.with_suffix('.next')
    pending.write_text(json.dumps(data))
    pending.replace(live.path)


@pytest.mark.parametrize('kind', ['lab', 'diy', 'pinned'])
def test_valid_pickup_updates_existing_context_and_readiness_without_teardown(live: SimpleNamespace, kind: str) -> None:
    supervisor = live.supervisor
    if kind == 'lab':
        factory = supervisor.build_ctx_factory()
        context = lambda: factory(live.clock[0])
    elif kind == 'diy':
        factory = supervisor.build_diy_ctx_factory()
        context = lambda: factory('', RemoteRequest(method='GET', path='/api/v1/orgs'), live.clock[0])
    else:
        supervisor.config.diy = None
        supervisor.config.managed = ManagedProviderConfig(profile=LINUX_IDENTITY_PROFILE, control_plane_id='synthetic-control')
        factory = supervisor.build_managed_provider()._ctx_factory
        context = lambda: factory('', RemoteRequest(method='GET', path='/api/v1/orgs'), live.clock[0])
    assert context().policy is live.cached
    waits = []
    def tick(_: float) -> None:
        waits.append(1)
        if len(waits) == 1:
            publish(live)
            # Cached expiry is already past at the next readiness evaluation.
            live.clock[0] = NOW + timedelta(seconds=1801)
    assert supervisor._run_loop(max_iterations=2, wait_fn=tick) == 0
    assert supervisor.load_policy().revision == 6
    assert context().policy is supervisor.load_policy()
    assert live.readiness._gate_current_policy(live.clock[0]).ok
    assert (live.listener.starts, live.listener.stops, live.closed) == (1, 0, [])
    assert live.registry.is_open('synthetic-stream')
    assert live.notifications.count('READY=1\n') == 1
    assert 'WATCHDOG=1\n' in live.notifications


@pytest.mark.parametrize('case', ['equal', 'rollback', 'backwards_issue', 'invalid', 'artifact', 'expired', 'symlink'])
def test_refused_pickup_keeps_cached_and_does_not_reread_unchanged(
    live: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    supervisor = live.supervisor
    reads = []
    load = supervisor.load_policy
    def observed(**kwargs: bool) -> object:
        if kwargs.get('refresh'):
            reads.append(1)
        return load(**kwargs)
    monkeypatch.setattr(supervisor, 'load_policy', observed)
    def tick(_: float) -> None:
        if case == 'equal':
            publish(live, revision=5)
        elif case == 'rollback':
            publish(live, revision=4)
        elif case == 'backwards_issue':
            publish(live, issued_at=(NOW - timedelta(seconds=1801)).isoformat())
        elif case == 'invalid':
            live.path.write_bytes(b'{')
        elif case == 'artifact':
            publish(live, artifact={})
        elif case == 'expired':
            publish(live, issued_at=(NOW - timedelta(hours=2)).isoformat())
        else:
            target = live.path.with_suffix('.other')
            live.path.rename(target)
            live.path.symlink_to(target)
    # One publication only; two following passes see the same no-follow signature.
    count = []
    def once(value: float) -> None:
        if not count:
            tick(value)
        count.append(1)
    assert supervisor._run_loop(max_iterations=3, wait_fn=once) == 0
    assert supervisor.load_policy() is live.cached
    assert reads == [1]
    assert (live.listener.starts, live.listener.stops, live.closed) == (1, 0, [])
    assert live.registry.is_open('synthetic-stream')


def test_refusal_then_cached_expiry_preserves_existing_teardown(live: SimpleNamespace) -> None:
    count = []
    def tick(_: float) -> None:
        count.append(1)
        if len(count) == 1:
            publish(live, revision=4)
        else:
            live.clock[0] = NOW + timedelta(seconds=1801)
    assert live.supervisor._run_loop(max_iterations=3, wait_fn=tick) == 1
    assert live.supervisor.load_policy() is live.cached
    assert (live.listener.starts, live.listener.stops, live.closed) == (1, 1, [True])
    assert live.registry.sealed
    assert not live.registry.is_open('synthetic-stream')
    assert 'STOPPING=1\n' in live.notifications
    assert not live.readiness._gate_current_policy(live.clock[0]).ok


@pytest.mark.parametrize('case', ['malformed', 'symlink', 'hardlink'])
def test_startup_policy_nofollow_and_validation(live: SimpleNamespace, case: str) -> None:
    if case == 'malformed':
        live.path.write_bytes(b'{}')
    elif case == 'symlink':
        target = live.path.with_suffix('.other')
        live.path.rename(target)
        live.path.symlink_to(target)
    else:
        import os
        os.link(live.path, live.path.with_suffix('.other'))
    live.supervisor._policy = None
    with pytest.raises(PolicyLoadError):
        live.supervisor.load_policy()

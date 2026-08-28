"""Shared harness for the THR-097 connector-core test battery.

The Unit-A normative fixtures under ``tests/contract/managed_remote_access/``
are the normative consumers: this harness loads them read-only and drives the
connector core against them.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from runtime.remote_access import authorization, identity, models
from runtime.remote_access.policy import PolicyEnvelope, RoutePolicyConsumer

_CONTRACT_DIR = Path(__file__).resolve().parents[1] / "contract" / "managed_remote_access"


def load_fixture(name: str) -> dict:
    """Load one Unit-A normative fixture (read-only)."""
    with (_CONTRACT_DIR / f"{name}.json").open("r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="session")
def route_policy_fixture() -> dict:
    return load_fixture("route-policy")


@pytest.fixture(scope="session")
def threat_cases_fixture() -> dict:
    return load_fixture("threat-cases")


@pytest.fixture(scope="session")
def failure_categories_fixture() -> dict:
    return load_fixture("failure-categories")


@pytest.fixture(scope="session")
def credential_taxonomy_fixture() -> dict:
    return load_fixture("credential-taxonomy")


def NOW() -> datetime:
    """Deterministic clock for the harness."""
    return datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)


def make_policy_envelope(
    artifact: dict,
    *,
    schema_version: int = 1,
    issued_at: datetime | None = None,
    max_age_seconds: int = 3600,
    revision: int = 1,
    state: str = "active",
) -> PolicyEnvelope:
    version = int(artifact.get("version", 1)) if isinstance(artifact, dict) else 0
    return PolicyEnvelope(
        schema_version=schema_version,
        artifact=artifact,
        artifact_version=version,
        issued_at=issued_at if issued_at is not None else NOW() - timedelta(seconds=60),
        max_age_seconds=max_age_seconds,
        revision=revision,
        state=state,
    )


def build_consumer(
    route_policy_fixture: dict,
    *,
    pinned_digest: str | None = None,
    issued_at: datetime | None = None,
    max_age_seconds: int = 3600,
    revision: int = 1,
    last_revision: int | None = None,
    state: str = "active",
    now: datetime | None = None,
) -> RoutePolicyConsumer:
    """Build a validated policy consumer from the real Unit-A route-policy fixture."""
    envelope = make_policy_envelope(
        route_policy_fixture,
        issued_at=issued_at,
        max_age_seconds=max_age_seconds,
        revision=revision,
        state=state,
    )
    return RoutePolicyConsumer.from_envelope(
        envelope,
        pinned_digest=pinned_digest,
        last_revision=revision - 1 if last_revision is None else last_revision,
        now=now or NOW(),
    )


def default_identity() -> identity.ConnectorIdentity:
    return identity.ConnectorIdentity(
        tenant_id="tenant-a",
        home_id="home-a",
        connector_id="connector-a",
    )


def default_authorization_state() -> authorization.TrustState:
    """A healthy trust state: one paired, current, non-revoked device."""
    state = authorization.TrustState(
        connector_identity=default_identity(),
        pairing_epoch=1,
        revocation_epoch=0,
    )
    state.apply_pairing(
        authorization.DeviceAuthorization(
            device_id="device-a",
            tenant_id="tenant-a",
            home_id="home-a",
            authorization_epoch=1,
            expires_at=NOW() + timedelta(days=30),
        )
    )
    return state


def make_request(
    method: str = "GET",
    path: str = "/api/v1/health",
    query: str | None = None,
    headers: list[tuple[str, str]] | None = None,
    body: bytes | None = None,
    stream_type: str = "http",
) -> models.RemoteRequest:
    return models.RemoteRequest(
        method=method,
        path=path,
        query=query,
        headers=tuple(models.Header(name, value) for name, value in (headers or [])),
        body=body,
        stream_type=stream_type,
    )

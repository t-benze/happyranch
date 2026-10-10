"""Policy fixture helpers for the retained DIY integration lane."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from runtime.remote_access.policy import PolicyEnvelope

_CONTRACT_DIR = Path(__file__).resolve().parents[1] / "contract" / "managed_remote_access"


def load_fixture(name: str) -> dict:
    """Load one Unit-A normative fixture (read-only)."""
    with (_CONTRACT_DIR / f"{name}.json").open("r", encoding="utf-8") as fh:
        return json.load(fh)


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

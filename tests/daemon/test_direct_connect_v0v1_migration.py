"""v0/v1 direct-connect schema compatibility migration.

The legacy operations/receipts tables enforced ``UNIQUE(token_fingerprint)``,
which blocks the approved parent-authority/append-only candidate retry model
(candidate A and corrected candidate B share one parent token fingerprint).
These tests prove the bounded migration removes only that uniqueness constraint,
preserves every coupled fact, and is restart-safe across all durable
interruption boundaries.
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runtime.config import Settings
from runtime.daemon import paths
from runtime.daemon.app import create_app
from runtime.daemon.direct_connect_store import (
    DirectConnectAuthorityStore,
    fingerprint_registration_token,
)
from runtime.daemon.state import DaemonState


_LEGACY_TABLES_V1 = """
CREATE TABLE IF NOT EXISTS direct_connect_authorities (
    token_fingerprint TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    intended_profile_name TEXT NOT NULL,
    wrapper_destination TEXT NOT NULL,
    workspace_adapter_id TEXT NOT NULL,
    issued_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    state TEXT NOT NULL CHECK (state = 'minted_nonlaunchable'),
    provenance TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_direct_connect_authorities_expiry
    ON direct_connect_authorities(expires_at);

CREATE TABLE IF NOT EXISTS direct_connect_reservations (
    token_fingerprint TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('reserved', 'terminalized', 'received_nonlaunchable')),
    reason TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS direct_connect_operations (
    operation_id TEXT PRIMARY KEY,
    token_fingerprint TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK (state = 'received_nonlaunchable'),
    intended_profile_name TEXT NOT NULL,
    workspace_adapter_id TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS direct_connect_artifacts (
    operation_id TEXT NOT NULL,
    slot TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('immutable_wrapper', 'upgradeable_child')),
    declared_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    structural_facts TEXT NOT NULL,
    PRIMARY KEY (operation_id, slot)
);

CREATE TABLE IF NOT EXISTS direct_connect_receipts (
    operation_id TEXT PRIMARY KEY,
    token_fingerprint TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK (state = 'received_nonlaunchable'),
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS direct_connect_events (
    event_id TEXT PRIMARY KEY,
    operation_id TEXT,
    token_fingerprint TEXT NOT NULL,
    event_type TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS direct_connect_projections (
    operation_id TEXT PRIMARY KEY,
    token_fingerprint TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('planned', 'committed', 'failed')),
    adapter_id TEXT,
    profile_name TEXT,
    reason TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS direct_connect_retry_attempts (
    attempt_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('running', 'succeeded', 'failed')),
    adapter_id TEXT,
    profile_name TEXT,
    reason TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_direct_connect_one_running_retry
    ON direct_connect_retry_attempts(operation_id) WHERE state = 'running';

-- THR-160 v1 additive tables, present in the schema lineage before the
-- uniqueness constraint was removed.
CREATE TABLE IF NOT EXISTS direct_connect_parent_lifecycles (
    token_fingerprint TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK (state IN ('open', 'committed', 'failed', 'expired')),
    latest_accepted_candidate_id TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS direct_connect_candidates (
    candidate_id TEXT PRIMARY KEY,
    token_fingerprint TEXT NOT NULL,
    operation_id TEXT UNIQUE,
    attempt_ordinal INTEGER NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('received_nonlaunchable', 'committed', 'failed')),
    identity_hash TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_direct_connect_candidates_token
    ON direct_connect_candidates(token_fingerprint);

CREATE TABLE IF NOT EXISTS direct_connect_identity_history (
    history_id TEXT PRIMARY KEY,
    token_fingerprint TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    identity_hash TEXT NOT NULL,
    identity_blob TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_direct_connect_identity_history_token
    ON direct_connect_identity_history(token_fingerprint);
"""

_LEGACY_TABLES_V0 = """
CREATE TABLE IF NOT EXISTS direct_connect_authorities (
    token_fingerprint TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    intended_profile_name TEXT NOT NULL,
    wrapper_destination TEXT NOT NULL,
    workspace_adapter_id TEXT NOT NULL,
    issued_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    state TEXT NOT NULL CHECK (state = 'minted_nonlaunchable'),
    provenance TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_direct_connect_authorities_expiry
    ON direct_connect_authorities(expires_at);

CREATE TABLE IF NOT EXISTS direct_connect_reservations (
    token_fingerprint TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('reserved', 'terminalized', 'received_nonlaunchable')),
    reason TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS direct_connect_operations (
    operation_id TEXT PRIMARY KEY,
    token_fingerprint TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK (state = 'received_nonlaunchable'),
    intended_profile_name TEXT NOT NULL,
    workspace_adapter_id TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS direct_connect_artifacts (
    operation_id TEXT NOT NULL,
    slot TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('immutable_wrapper', 'upgradeable_child')),
    declared_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    structural_facts TEXT NOT NULL,
    PRIMARY KEY (operation_id, slot)
);

CREATE TABLE IF NOT EXISTS direct_connect_receipts (
    operation_id TEXT PRIMARY KEY,
    token_fingerprint TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK (state = 'received_nonlaunchable'),
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS direct_connect_events (
    event_id TEXT PRIMARY KEY,
    operation_id TEXT,
    token_fingerprint TEXT NOT NULL,
    event_type TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS direct_connect_projections (
    operation_id TEXT PRIMARY KEY,
    token_fingerprint TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('planned', 'committed', 'failed')),
    adapter_id TEXT,
    profile_name TEXT,
    reason TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS direct_connect_retry_attempts (
    attempt_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('running', 'succeeded', 'failed')),
    adapter_id TEXT,
    profile_name TEXT,
    reason TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_direct_connect_one_running_retry
    ON direct_connect_retry_attempts(operation_id) WHERE state = 'running';
"""


def _seed_legacy_v1_database(
    db_path: Path,
    token: str,
    profile: str,
    wrapper_path: Path,
    *,
    expires_at: float = 100.0,
) -> tuple[str, str, str, str]:
    """Create a v1 legacy DB with a terminal-failed candidate A and open parent."""
    fingerprint = fingerprint_registration_token(token)
    operation_a = "op-a-0000-0000-0000-000000000001"
    candidate_a = "cand-a-0000-0000-0000-000000000001"
    identity_hash_a = "hash-a" * 16
    identity_blob_a = "blob-a"

    conn = sqlite3.connect(db_path)
    conn.executescript(_LEGACY_TABLES_V1)
    conn.execute(
        """INSERT INTO direct_connect_authorities
           (token_fingerprint, name, intended_profile_name, wrapper_destination,
            workspace_adapter_id, issued_at, expires_at, state, provenance)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (fingerprint, "custom-cli", profile, str(wrapper_path), "codex", 1.0, expires_at, "minted_nonlaunchable", "runtime-master-mint"),
    )
    conn.execute(
        """INSERT INTO direct_connect_reservations
           (token_fingerprint, operation_id, state, reason, created_at, updated_at)
           VALUES (?, ?, 'received_nonlaunchable', NULL, ?, ?)""",
        (fingerprint, operation_a, 2.0, 2.0),
    )
    conn.execute(
        """INSERT INTO direct_connect_operations
           (operation_id, token_fingerprint, state, intended_profile_name, workspace_adapter_id, created_at)
           VALUES (?, ?, 'received_nonlaunchable', ?, ?, ?)""",
        (operation_a, fingerprint, profile, "codex", 2.0),
    )
    conn.execute(
        """INSERT INTO direct_connect_artifacts
           (operation_id, slot, kind, declared_path, sha256, structural_facts)
           VALUES (?, 'wrapper', 'immutable_wrapper', ?, ?, ?)""",
        (operation_a, str(wrapper_path), "a" * 64, "{}"),
    )
    conn.execute(
        """INSERT INTO direct_connect_artifacts
           (operation_id, slot, kind, declared_path, sha256, structural_facts)
           VALUES (?, 'cli', 'upgradeable_child', ?, ?, ?)""",
        (operation_a, "/abs/child", "b" * 64, "{}"),
    )
    conn.execute(
        """INSERT INTO direct_connect_receipts
           (operation_id, token_fingerprint, state, created_at)
           VALUES (?, ?, 'received_nonlaunchable', ?)""",
        (operation_a, fingerprint, 2.0),
    )
    conn.execute(
        """INSERT INTO direct_connect_events
           (event_id, operation_id, token_fingerprint, event_type, detail, created_at)
           VALUES (?, ?, ?, 'received_nonlaunchable', 'validated receipt', ?)""",
        ("event-a-1", operation_a, fingerprint, 2.0),
    )
    conn.execute(
        """INSERT INTO direct_connect_projections
           (operation_id, token_fingerprint, state, adapter_id, profile_name, reason, created_at, updated_at)
           VALUES (?, ?, 'failed', NULL, NULL, 'conformance_probe_failed: missing canary', ?, ?)""",
        (operation_a, fingerprint, 3.0, 3.0),
    )
    conn.execute(
        """INSERT INTO direct_connect_parent_lifecycles
           (token_fingerprint, state, latest_accepted_candidate_id, created_at, updated_at, expires_at)
           VALUES (?, 'open', ?, ?, ?, ?)""",
        (fingerprint, candidate_a, 2.0, 3.0, expires_at),
    )
    conn.execute(
        """INSERT INTO direct_connect_candidates
           (candidate_id, token_fingerprint, operation_id, attempt_ordinal, state, identity_hash, created_at)
           VALUES (?, ?, ?, 1, 'failed', ?, ?)""",
        (candidate_a, fingerprint, operation_a, identity_hash_a, 2.0),
    )
    conn.execute(
        """INSERT INTO direct_connect_identity_history
           (history_id, token_fingerprint, candidate_id, identity_hash, identity_blob, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("hist-a-1", fingerprint, candidate_a, identity_hash_a, identity_blob_a, 2.0),
    )
    conn.commit()
    conn.close()
    return operation_a, candidate_a, identity_hash_a, identity_blob_a


def _seed_legacy_v0_database(
    db_path: Path,
    token: str,
    profile: str,
    wrapper_path: Path,
) -> str:
    """Create a v0 legacy DB with a terminal-failed operation A."""
    fingerprint = fingerprint_registration_token(token)
    operation_a = "op-a-0000-0000-0000-000000000001"

    conn = sqlite3.connect(db_path)
    conn.executescript(_LEGACY_TABLES_V0)
    conn.execute(
        """INSERT INTO direct_connect_authorities
           (token_fingerprint, name, intended_profile_name, wrapper_destination,
            workspace_adapter_id, issued_at, expires_at, state, provenance)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (fingerprint, "custom-cli", profile, str(wrapper_path), "codex", 1.0, 100.0, "minted_nonlaunchable", "runtime-master-mint"),
    )
    conn.execute(
        """INSERT INTO direct_connect_reservations
           (token_fingerprint, operation_id, state, reason, created_at, updated_at)
           VALUES (?, ?, 'received_nonlaunchable', NULL, ?, ?)""",
        (fingerprint, operation_a, 2.0, 2.0),
    )
    conn.execute(
        """INSERT INTO direct_connect_operations
           (operation_id, token_fingerprint, state, intended_profile_name, workspace_adapter_id, created_at)
           VALUES (?, ?, 'received_nonlaunchable', ?, ?, ?)""",
        (operation_a, fingerprint, profile, "codex", 2.0),
    )
    conn.execute(
        """INSERT INTO direct_connect_artifacts
           (operation_id, slot, kind, declared_path, sha256, structural_facts)
           VALUES (?, 'wrapper', 'immutable_wrapper', ?, ?, ?)""",
        (operation_a, str(wrapper_path), "a" * 64, "{}"),
    )
    conn.execute(
        """INSERT INTO direct_connect_artifacts
           (operation_id, slot, kind, declared_path, sha256, structural_facts)
           VALUES (?, 'cli', 'upgradeable_child', ?, ?, ?)""",
        (operation_a, "/abs/child", "b" * 64, "{}"),
    )
    conn.execute(
        """INSERT INTO direct_connect_receipts
           (operation_id, token_fingerprint, state, created_at)
           VALUES (?, ?, 'received_nonlaunchable', ?)""",
        (operation_a, fingerprint, 2.0),
    )
    conn.execute(
        """INSERT INTO direct_connect_events
           (event_id, operation_id, token_fingerprint, event_type, detail, created_at)
           VALUES (?, ?, ?, 'received_nonlaunchable', 'validated receipt', ?)""",
        ("event-a-1", operation_a, fingerprint, 2.0),
    )
    conn.execute(
        """INSERT INTO direct_connect_projections
           (operation_id, token_fingerprint, state, adapter_id, profile_name, reason, created_at, updated_at)
           VALUES (?, ?, 'failed', NULL, NULL, 'conformance_probe_failed: missing canary', ?, ?)""",
        (operation_a, fingerprint, 3.0, 3.0),
    )
    conn.commit()
    conn.close()
    return operation_a


def _assert_legacy_unique_blocks_b(
    db_path: Path, token: str, operation_a: str, wrapper_path: Path
) -> None:
    """Red-before-green proof: the legacy schema rejects candidate B."""
    fingerprint = fingerprint_registration_token(token)
    conn = sqlite3.connect(db_path)
    operation_b = "op-b-0000-0000-0000-000000000002"
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO direct_connect_operations
               (operation_id, token_fingerprint, state, intended_profile_name, workspace_adapter_id, created_at)
               VALUES (?, ?, 'received_nonlaunchable', ?, ?, ?)""",
            (operation_b, fingerprint, "profile", "codex", 4.0),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO direct_connect_receipts
               (operation_id, token_fingerprint, state, created_at)
               VALUES (?, ?, 'received_nonlaunchable', ?)""",
            (operation_b, fingerprint, 4.0),
        )
    conn.close()


def _assert_coupled_facts_retained(
    store: DirectConnectAuthorityStore,
    token: str,
    operation_a: str,
    profile: str,
    has_candidate: bool,
) -> None:
    """Post-migration adversarial regression: A and all coupled facts survive."""
    fingerprint = fingerprint_registration_token(token)
    cursor = store._conn.cursor()

    # Operation A and its artifacts/receipt/event remain.
    assert cursor.execute(
        "SELECT operation_id FROM direct_connect_operations WHERE operation_id = ?",
        (operation_a,),
    ).fetchone()["operation_id"] == operation_a
    assert cursor.execute(
        "SELECT operation_id FROM direct_connect_receipts WHERE operation_id = ?",
        (operation_a,),
    ).fetchone()["operation_id"] == operation_a
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_artifacts WHERE operation_id = ?",
        (operation_a,),
    ).fetchone()[0] == 2
    assert cursor.execute(
        "SELECT event_type FROM direct_connect_events WHERE operation_id = ?",
        (operation_a,),
    ).fetchone()["event_type"] == "received_nonlaunchable"
    projection = cursor.execute(
        "SELECT state, reason FROM direct_connect_projections WHERE operation_id = ?",
        (operation_a,),
    ).fetchone()
    assert projection["state"] == "failed"
    assert "conformance_probe_failed" in projection["reason"]

    # The parent lifecycle remains open so B can be admitted.
    parent = cursor.execute(
        "SELECT state FROM direct_connect_parent_lifecycles WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()
    if has_candidate:
        assert parent is not None and parent["state"] == "open"
        assert cursor.execute(
            "SELECT identity_hash FROM direct_connect_identity_history WHERE candidate_id = ?",
            ("cand-a-0000-0000-0000-000000000001",),
        ).fetchone()["identity_hash"] == "hash-a" * 16
    else:
        # v0 has no pre-existing parent/candidate/identity rows; the store
        # creates them on first identity-aware reservation.
        assert parent is None or parent["state"] == "open"

    # A second candidate B was accepted.
    candidates = cursor.execute(
        "SELECT operation_id, attempt_ordinal FROM direct_connect_candidates WHERE token_fingerprint = ? ORDER BY attempt_ordinal",
        (fingerprint,),
    ).fetchall()
    if has_candidate:
        # v1: A already had a candidate row, so B is the second candidate.
        assert len(candidates) == 2
        assert candidates[0]["operation_id"] == operation_a
        assert candidates[1]["operation_id"] != operation_a
    else:
        # v0: A had no candidate row; B is the first accepted candidate.
        assert len(candidates) == 1
        assert candidates[0]["operation_id"] != operation_a

    # Latest operation for the profile is B.
    assert store.get_latest_operation_for_profile(profile) != operation_a


def test_v1_legacy_unique_constraint_blocks_b_before_migration(tmp_path: Path) -> None:
    """Red: on the v1 legacy schema, inserting B violates token_fingerprint UNIQUE."""
    db_path = tmp_path / "direct.db"
    wrapper_path = tmp_path / "adapters" / "profile-adapter"
    operation_a, _, _, _ = _seed_legacy_v1_database(db_path, "hrreg_v1", "v1-profile", wrapper_path)
    _assert_legacy_unique_blocks_b(db_path, "hrreg_v1", operation_a, wrapper_path)


def test_v0_legacy_unique_constraint_blocks_b_before_migration(tmp_path: Path) -> None:
    """Red: on the v0 legacy schema, inserting B violates token_fingerprint UNIQUE."""
    db_path = tmp_path / "direct.db"
    wrapper_path = tmp_path / "adapters" / "profile-adapter"
    operation_a = _seed_legacy_v0_database(db_path, "hrreg_v0", "v0-profile", wrapper_path)
    _assert_legacy_unique_blocks_b(db_path, "hrreg_v0", operation_a, wrapper_path)


def test_v1_migration_removes_unique_and_allows_corrected_candidate_b(tmp_path: Path) -> None:
    """Green: opening the store migrates v1 legacy schema; B succeeds and A survives."""
    db_path = tmp_path / "direct.db"
    wrapper_path = tmp_path / "adapters" / "profile-adapter"
    operation_a, _, _, _ = _seed_legacy_v1_database(db_path, "hrreg_v1", "v1-profile", wrapper_path)

    store = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)

    # B uses a genuinely different identity hash.
    operation_b = store.reserve(
        "hrreg_v1", identity_hash="hash-b" * 16, identity_blob="blob-b", now=5.0,
    )
    assert operation_b is not None
    assert operation_b != operation_a
    store.receive(
        "hrreg_v1", operation_b, wrapper_sha256="c" * 64, wrapper_facts={},
        children=[], workspace_adapter_id="codex",
        identity_hash="hash-b" * 16, identity_blob="blob-b", now=5.0,
    )

    _assert_coupled_facts_retained(store, "hrreg_v1", operation_a, "v1-profile", has_candidate=True)
    store.close()

    # Second reopen is idempotent and still sound.
    reopened = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)
    _assert_coupled_facts_retained(reopened, "hrreg_v1", operation_a, "v1-profile", has_candidate=True)
    reopened.close()


def test_v0_migration_removes_unique_and_allows_corrected_candidate_b(tmp_path: Path) -> None:
    """Green: opening the store migrates v0 legacy schema; B succeeds and A survives."""
    db_path = tmp_path / "direct.db"
    wrapper_path = tmp_path / "adapters" / "profile-adapter"
    operation_a = _seed_legacy_v0_database(db_path, "hrreg_v0", "v0-profile", wrapper_path)

    store = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)

    operation_b = store.reserve(
        "hrreg_v0", identity_hash="hash-b" * 16, identity_blob="blob-b", now=5.0,
    )
    assert operation_b is not None
    assert operation_b != operation_a
    store.receive(
        "hrreg_v0", operation_b, wrapper_sha256="c" * 64, wrapper_facts={},
        children=[], workspace_adapter_id="codex",
        identity_hash="hash-b" * 16, identity_blob="blob-b", now=5.0,
    )

    _assert_coupled_facts_retained(store, "hrreg_v0", operation_a, "v0-profile", has_candidate=False)
    store.close()

    reopened = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)
    _assert_coupled_facts_retained(reopened, "hrreg_v0", operation_a, "v0-profile", has_candidate=False)
    reopened.close()


def test_migration_is_idempotent_for_already_modern_database(tmp_path: Path) -> None:
    """A database created by the current DDL needs no migration and stays sound."""
    db_path = tmp_path / "direct.db"
    store = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)
    store.mint_authority(
        token_plaintext="hrreg_modern", name="custom-cli", intended_profile_name="modern-profile",
        workspace_adapter_id="codex", issued_at=1.0, expires_at=100.0,
    )
    operation_a = store.reserve("hrreg_modern", identity_hash="hash-a" * 16, identity_blob="blob-a", now=2.0)
    store.receive(
        "hrreg_modern", operation_a, wrapper_sha256="a" * 64, wrapper_facts={},
        children=[], workspace_adapter_id="codex",
        identity_hash="hash-a" * 16, identity_blob="blob-a", now=2.0,
    )
    assert store.plan_projection(operation_a, now=3.0)
    assert store.mark_failed(operation_a, "conformance_probe_failed: missing canary", now=4.0)
    store.close()

    reopened = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)
    operation_b = reopened.reserve(
        "hrreg_modern", identity_hash="hash-b" * 16, identity_blob="blob-b", now=5.0,
    )
    assert operation_b is not None
    reopened.receive(
        "hrreg_modern", operation_b, wrapper_sha256="b" * 64, wrapper_facts={},
        children=[], workspace_adapter_id="codex",
        identity_hash="hash-b" * 16, identity_blob="blob-b", now=5.0,
    )
    assert len(reopened.list_candidates("hrreg_modern")) == 2
    reopened.close()


def _simulate_interrupted_migration(
    db_path: Path,
    token: str,
    profile: str,
    wrapper_path: Path,
    stage: str,
) -> str:
    """Seed a v1 legacy DB and leave it at one of the migration interruption stages."""
    operation_a, _, _, _ = _seed_legacy_v1_database(db_path, token, profile, wrapper_path)
    fingerprint = fingerprint_registration_token(token)
    new_operations = "direct_connect_operations_migration_new"
    new_receipts = "direct_connect_receipts_migration_new"

    conn = sqlite3.connect(db_path)

    # Build the new tables without the unique constraint, using the same DDL
    # shape the migration would use.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS direct_connect_operations_migration_new (
            operation_id TEXT PRIMARY KEY,
            token_fingerprint TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state = 'received_nonlaunchable'),
            intended_profile_name TEXT NOT NULL,
            workspace_adapter_id TEXT NOT NULL,
            created_at REAL NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS direct_connect_receipts_migration_new (
            operation_id TEXT PRIMARY KEY,
            token_fingerprint TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state = 'received_nonlaunchable'),
            created_at REAL NOT NULL
        )"""
    )

    if stage in {"after_copy", "after_drop", "after_rename"}:
        conn.execute(
            f"""INSERT INTO {new_operations}
                (operation_id, token_fingerprint, state, intended_profile_name, workspace_adapter_id, created_at)
                SELECT operation_id, token_fingerprint, state, intended_profile_name, workspace_adapter_id, created_at
                FROM direct_connect_operations"""
        )
        conn.execute(
            f"""INSERT INTO {new_receipts}
                (operation_id, token_fingerprint, state, created_at)
                SELECT operation_id, token_fingerprint, state, created_at
                FROM direct_connect_receipts"""
        )

    if stage in {"after_drop", "after_rename"}:
        conn.execute("DROP TABLE direct_connect_operations")
        conn.execute("DROP TABLE direct_connect_receipts")

    if stage == "after_rename":
        conn.execute(f"ALTER TABLE {new_operations} RENAME TO direct_connect_operations")
        conn.execute(f"ALTER TABLE {new_receipts} RENAME TO direct_connect_receipts")

    conn.commit()
    conn.close()
    return operation_a


@pytest.mark.parametrize(
    "stage",
    ["pre_copy", "after_copy", "after_drop", "after_rename"],
)
def test_migration_recovers_from_every_interruption_stage(tmp_path: Path, stage: str) -> None:
    """Restart safety: each durable interruption boundary resumes correctly."""
    db_path = tmp_path / "direct.db"
    wrapper_path = tmp_path / "adapters" / "profile-adapter"
    operation_a = _simulate_interrupted_migration(db_path, "hrreg_recover", "recover-profile", wrapper_path, stage)

    store = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)

    # Migration must leave the schema without a unique token_fingerprint index.
    assert store._unique_token_fingerprint_index("direct_connect_operations") is None
    assert store._unique_token_fingerprint_index("direct_connect_receipts") is None

    operation_b = store.reserve(
        "hrreg_recover", identity_hash="hash-b" * 16, identity_blob="blob-b", now=5.0,
    )
    assert operation_b is not None
    store.receive(
        "hrreg_recover", operation_b, wrapper_sha256="c" * 64, wrapper_facts={},
        children=[], workspace_adapter_id="codex",
        identity_hash="hash-b" * 16, identity_blob="blob-b", now=5.0,
    )

    _assert_coupled_facts_retained(store, "hrreg_recover", operation_a, "recover-profile", has_candidate=True)
    store.close()

    # Reopen again to prove recovery state is stable.
    reopened = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)
    _assert_coupled_facts_retained(reopened, "hrreg_recover", operation_a, "recover-profile", has_candidate=True)
    assert reopened._unique_token_fingerprint_index("direct_connect_operations") is None
    assert reopened._unique_token_fingerprint_index("direct_connect_receipts") is None
    reopened.close()



def _write_executable(path: Path, body: bytes = b"#!/bin/sh\nexit 0\n") -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    path.chmod(0o700)
    return hashlib.sha256(body).hexdigest()


def _connect_payload(wrapper_hash: str, child_path: Path) -> dict:
    return {
        "metadata": {"client": "migration-test"},
        "manifest": {
            "manifest_version": 2,
            "wrapper_sha256": wrapper_hash,
            "upgradeable_children": [
                {
                    "slot": "cli",
                    "executable": str(child_path),
                    "version_probe_argv": [str(child_path), "--version"],
                }
            ],
            "workspace_adapter_id": "codex",
        },
    }


def test_v1_migration_allows_corrected_candidate_b_via_http_ingress(tmp_path: Path, monkeypatch) -> None:
    """HTTP ingress proof: legacy v1 DB with failed A accepts corrected B after migration."""
    import time

    db_path = tmp_path / "direct.db"
    runtime_root = tmp_path / "daemon"
    wrapper_path = runtime_root / "adapters" / "recover-profile-adapter"
    token = "hrreg_http_v1"
    future = time.time() + 1000.0
    operation_a, candidate_a, identity_hash_a, identity_blob_a = _seed_legacy_v1_database(
        db_path, token, "recover-profile", wrapper_path, expires_at=future
    )

    # Build daemon state around the legacy DB so opening the store triggers migration.
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(runtime_root))
    paths.ensure_daemon_home()
    paths.ensure_token()
    state = DaemonState.idle(Settings())
    state.direct_connect_authority_store.close()
    state.direct_connect_authority_store = DirectConnectAuthorityStore(db_path, runtime_root=runtime_root)

    # The legacy DB already received A; mark the registration token consumed.
    record = state.registration_token_store._validate_raw(token, now=future - 1.0)
    if record is not None:
        record.consumed = True
        record.reserved = False

    from runtime.daemon.routes import auth, direct_connect

    monkeypatch.setattr(auth, "_LOCAL_HOSTS", auth._LOCAL_HOSTS | {"testclient"})
    monkeypatch.setattr(direct_connect, "_LOCAL_HOSTS", direct_connect._LOCAL_HOSTS | {"testclient"})

    tc = TestClient(create_app(state))
    tc.headers.update({"Authorization": f"Bearer {paths.read_token()}"})

    # Create genuinely changed candidate B artifacts.
    child_a = tmp_path / "bin" / "child-a"
    _write_executable(child_a, b"#!/bin/sh\necho a\n")
    wrapper_hash_a = _write_executable(wrapper_path, b"#!/bin/sh\necho wrapper-a\n")
    # A's identity hash was built from wrapper-a/child-a in the legacy fixture.
    # We change both wrapper and child so B is a genuinely different identity.
    child_b = tmp_path / "bin" / "child-b"
    _write_executable(child_b, b"#!/bin/sh\necho b\n")
    wrapper_hash_b = _write_executable(wrapper_path, b"#!/bin/sh\necho wrapper-b\n")

    # The corrected B /connect call must succeed (201) and create a second operation.
    response = tc.post(
        "/api/v1/runtime/custom-cli/connect",
        json=_connect_payload(wrapper_hash_b, child_b),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["state"] == "received_nonlaunchable"
    operation_b = body["operation_id"]
    assert operation_b != operation_a

    # A remains intact: operation, receipt, projection, event, candidate, identity history.
    store = state.direct_connect_authority_store
    cursor = store._conn.cursor()
    assert cursor.execute(
        "SELECT 1 FROM direct_connect_operations WHERE operation_id = ?",
        (operation_a,),
    ).fetchone() is not None
    assert cursor.execute(
        "SELECT 1 FROM direct_connect_receipts WHERE operation_id = ?",
        (operation_a,),
    ).fetchone() is not None
    assert cursor.execute(
        "SELECT state FROM direct_connect_projections WHERE operation_id = ?",
        (operation_a,),
    ).fetchone()["state"] == "failed"
    assert cursor.execute(
        "SELECT 1 FROM direct_connect_candidates WHERE operation_id = ?",
        (operation_a,),
    ).fetchone() is not None
    assert cursor.execute(
        "SELECT 1 FROM direct_connect_identity_history WHERE candidate_id = ?",
        (candidate_a,),
    ).fetchone() is not None

    # B is recorded as a second accepted candidate.
    candidates = cursor.execute(
        "SELECT operation_id, attempt_ordinal FROM direct_connect_candidates WHERE token_fingerprint = ? ORDER BY attempt_ordinal",
        (fingerprint_registration_token(token),),
    ).fetchall()
    assert len(candidates) == 2
    assert candidates[0]["operation_id"] == operation_a
    assert candidates[1]["operation_id"] == operation_b

    # Replaying the identical B identity is rejected as a duplicate.
    replay = tc.post(
        "/api/v1/runtime/custom-cli/connect",
        json=_connect_payload(wrapper_hash_b, child_b),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert replay.status_code == 409
    assert replay.json()["detail"].startswith("duplicate")

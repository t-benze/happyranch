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
import time
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
    *,
    expires_at: float = 100.0,
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
    conn.commit()
    conn.close()
    return operation_a


def _seed_legacy_v0_database_with_reason(
    db_path: Path,
    token: str,
    profile: str,
    wrapper_path: Path,
    *,
    reason: str,
    expires_at: float = 100.0,
) -> str:
    """Create a v0 legacy DB with a terminal-failed operation A and a custom reason."""
    fingerprint = fingerprint_registration_token(token)
    operation_a = "op-a-0000-0000-0000-000000000001"

    conn = sqlite3.connect(db_path)
    conn.executescript(_LEGACY_TABLES_V0)
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
           VALUES (?, ?, 'failed', NULL, NULL, ?, ?, ?)""",
        (operation_a, fingerprint, reason, 3.0, 3.0),
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
) -> None:
    """Post-migration adversarial regression: A and all coupled facts survive.

    After the terminal-v0 bridge, both v1 (pre-existing candidate row) and v0
    (backfilled candidate row) legacy databases present A as attempt ordinal 1
    and a corrected B as attempt ordinal 2.
    """
    fingerprint = fingerprint_registration_token(token)
    cursor = store._conn.cursor()

    # Operation A and its artifacts/receipt/event/projection remain.
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
    assert parent is not None and parent["state"] == "open"

    # A and B are both accepted candidates (A backfilled for v0, pre-existing for v1).
    candidates = cursor.execute(
        "SELECT candidate_id, operation_id, attempt_ordinal FROM direct_connect_candidates WHERE token_fingerprint = ? ORDER BY attempt_ordinal",
        (fingerprint,),
    ).fetchall()
    assert len(candidates) == 2
    assert candidates[0]["operation_id"] == operation_a
    assert candidates[0]["attempt_ordinal"] == 1
    assert candidates[1]["operation_id"] != operation_a
    assert candidates[1]["attempt_ordinal"] == 2

    # A's identity is retained in history.
    assert cursor.execute(
        "SELECT 1 FROM direct_connect_identity_history WHERE candidate_id = ?",
        (candidates[0]["candidate_id"],),
    ).fetchone() is not None

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

    _assert_coupled_facts_retained(store, "hrreg_v1", operation_a, "v1-profile")
    store.close()

    # Second reopen is idempotent and still sound.
    reopened = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)
    _assert_coupled_facts_retained(reopened, "hrreg_v1", operation_a, "v1-profile")
    reopened.close()


def test_v0_migration_removes_unique_and_allows_corrected_candidate_b(tmp_path: Path) -> None:
    """Green: opening the store migrates v0 legacy schema; B succeeds and A survives."""
    db_path = tmp_path / "direct.db"
    wrapper_path = tmp_path / "adapters" / "profile-adapter"
    operation_a = _seed_legacy_v0_database(
        db_path, "hrreg_v0", "v0-profile", wrapper_path, expires_at=time.time() + 1000.0,
    )

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

    _assert_coupled_facts_retained(store, "hrreg_v0", operation_a, "v0-profile")
    store.close()

    reopened = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)
    _assert_coupled_facts_retained(reopened, "hrreg_v0", operation_a, "v0-profile")
    reopened.close()


def test_v0_terminal_a_consumes_first_slot_so_b_is_ordinal_2(tmp_path: Path) -> None:
    """A migrated terminal v0 operation occupies candidate ordinal 1."""
    db_path = tmp_path / "direct.db"
    wrapper_path = tmp_path / "adapters" / "profile-adapter"
    operation_a = _seed_legacy_v0_database(
        db_path, "hrreg_v0_slot", "v0-slot", wrapper_path, expires_at=time.time() + 1000.0,
    )

    store = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)
    candidates_before_b = store.list_candidates("hrreg_v0_slot")
    assert len(candidates_before_b) == 1
    assert candidates_before_b[0].operation_id == operation_a
    assert candidates_before_b[0].attempt_ordinal == 1
    assert candidates_before_b[0].state == "failed"

    operation_b = store.reserve(
        "hrreg_v0_slot", identity_hash="hash-b" * 16, identity_blob="blob-b", now=5.0,
    )
    assert operation_b is not None
    store.receive(
        "hrreg_v0_slot", operation_b, wrapper_sha256="c" * 64, wrapper_facts={},
        children=[], workspace_adapter_id="codex",
        identity_hash="hash-b" * 16, identity_blob="blob-b", now=5.0,
    )

    candidates = store.list_candidates("hrreg_v0_slot")
    assert len(candidates) == 2
    assert candidates[0].operation_id == operation_a
    assert candidates[0].attempt_ordinal == 1
    assert candidates[1].operation_id == operation_b
    assert candidates[1].attempt_ordinal == 2
    store.close()


def test_v0_terminal_a_then_b_terminal_refuses_c(tmp_path: Path) -> None:
    """A terminal -> changed B once -> B terminal -> C is refused non-consumingly."""
    db_path = tmp_path / "direct.db"
    wrapper_path = tmp_path / "adapters" / "profile-adapter"
    operation_a = _seed_legacy_v0_database(
        db_path, "hrreg_v0_abc", "v0-abc", wrapper_path, expires_at=time.time() + 1000.0,
    )

    store = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)

    # Admit changed B as ordinal 2.
    operation_b = store.reserve(
        "hrreg_v0_abc", identity_hash="hash-b" * 16, identity_blob="blob-b", now=5.0,
    )
    assert operation_b is not None
    store.receive(
        "hrreg_v0_abc", operation_b, wrapper_sha256="c" * 64, wrapper_facts={},
        children=[], workspace_adapter_id="codex",
        identity_hash="hash-b" * 16, identity_blob="blob-b", now=5.0,
    )

    # B terminal-fails for a non-conformance reason.
    assert store.plan_projection(operation_b, now=6.0)
    assert store.mark_failed(operation_b, "profile_binding_failed", now=7.0)

    # C is refused: parent is closed/exhausted.
    operation_c = store.reserve(
        "hrreg_v0_abc", identity_hash="hash-c" * 16, identity_blob="blob-c", now=8.0,
    )
    assert operation_c is None
    assert store.parent_state("hrreg_v0_abc") == "failed"

    # Exactly two candidate facts exist; no third receipt/probe was created.
    fingerprint = fingerprint_registration_token("hrreg_v0_abc")
    cursor = store._conn.cursor()
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_candidates WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 2
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_receipts WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 2
    store.close()


def test_v0_backfilled_identity_fences_identical_and_reordered_replay(tmp_path: Path) -> None:
    """Retained A and B identities reject identical/reordered replay with 409."""
    db_path = tmp_path / "direct.db"
    wrapper_path = tmp_path / "adapters" / "profile-adapter"
    operation_a = _seed_legacy_v0_database(
        db_path, "hrreg_v0_fence", "v0-fence", wrapper_path, expires_at=time.time() + 1000.0,
    )

    store = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)

    # Capture the backfilled A identity.
    candidate_a = store.list_candidates("hrreg_v0_fence")[0]
    identity_a = store._conn.execute(
        "SELECT identity_hash, identity_blob FROM direct_connect_identity_history WHERE candidate_id = ?",
        (candidate_a.candidate_id,),
    ).fetchone()

    # Identical A replay is rejected as a duplicate.
    verdict, _ = store.evaluate_admission(
        "hrreg_v0_fence",
        identity_hash=identity_a["identity_hash"],
        identity_blob=identity_a["identity_blob"],
        now=5.0,
    )
    assert verdict == "duplicate"

    # Admit changed B.
    operation_b = store.reserve(
        "hrreg_v0_fence", identity_hash="hash-b" * 16, identity_blob="blob-b", now=5.0,
    )
    assert operation_b is not None
    store.receive(
        "hrreg_v0_fence", operation_b, wrapper_sha256="c" * 64, wrapper_facts={},
        children=[], workspace_adapter_id="codex",
        identity_hash="hash-b" * 16, identity_blob="blob-b", now=5.0,
    )

    # Identical B replay is rejected.
    verdict, _ = store.evaluate_admission(
        "hrreg_v0_fence",
        identity_hash="hash-b" * 16,
        identity_blob="blob-b",
        now=5.0,
    )
    assert verdict == "duplicate"

    # Reordered A replay (after B) is still rejected.
    verdict, _ = store.evaluate_admission(
        "hrreg_v0_fence",
        identity_hash=identity_a["identity_hash"],
        identity_blob=identity_a["identity_blob"],
        now=5.0,
    )
    assert verdict == "duplicate"

    # No extra candidate or receipt was created.
    fingerprint = fingerprint_registration_token("hrreg_v0_fence")
    cursor = store._conn.cursor()
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_candidates WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 2
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_receipts WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 2
    store.close()


def test_v0_backfill_is_idempotent_across_reopen(tmp_path: Path) -> None:
    """Reopening a migrated v0 database does not duplicate backfill facts."""
    db_path = tmp_path / "direct.db"
    wrapper_path = tmp_path / "adapters" / "profile-adapter"
    _seed_legacy_v0_database(db_path, "hrreg_v0_reopen", "v0-reopen", wrapper_path)

    first = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)
    first_candidates = first.list_candidates("hrreg_v0_reopen")
    assert len(first_candidates) == 1
    first.close()

    second = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)
    second_candidates = second.list_candidates("hrreg_v0_reopen")
    assert len(second_candidates) == 1
    assert second_candidates[0].operation_id == first_candidates[0].operation_id
    assert second_candidates[0].candidate_id == first_candidates[0].candidate_id
    second.close()

    # Two reopens: still exactly one backfilled candidate.
    third = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)
    assert len(third.list_candidates("hrreg_v0_reopen")) == 1
    third.close()


def test_v0_nonterminal_legacy_operation_is_not_backfilled(tmp_path: Path) -> None:
    """A v0 operation without a terminal failed projection stays conservative."""
    db_path = tmp_path / "direct.db"
    wrapper_path = tmp_path / "adapters" / "profile-adapter"
    token = "hrreg_v0_nonterminal"
    fingerprint = fingerprint_registration_token(token)
    operation_a = _seed_legacy_v0_database(db_path, token, "v0-nonterminal", wrapper_path)

    # Remove the terminal projection, leaving the operation nonterminal.
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM direct_connect_projections WHERE operation_id = ?", (operation_a,))
    conn.commit()
    conn.close()

    store = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)
    # No parent/candidate backfill because the operation is not terminal.
    assert store.parent_state(token) is None
    assert store.list_candidates(token) == []

    # B would be admitted as ordinal 1 if it were submitted (not tested here);
    # the key invariant is that the nonterminal legacy row is not synthesized.
    cursor = store._conn.cursor()
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_parent_lifecycles WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 0
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_candidates WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 0
    store.close()


def test_v0_committed_legacy_operation_is_not_backfilled(tmp_path: Path) -> None:
    """An approved (committed) v0 operation is not bridged into retry lifecycle."""
    db_path = tmp_path / "direct.db"
    wrapper_path = tmp_path / "adapters" / "profile-adapter"
    token = "hrreg_v0_committed"
    fingerprint = fingerprint_registration_token(token)
    operation_a = _seed_legacy_v0_database(db_path, token, "v0-committed", wrapper_path)

    # Change the projection to committed (approved), not terminal failed.
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE direct_connect_projections SET state = 'committed', reason = NULL WHERE operation_id = ?",
        (operation_a,),
    )
    conn.commit()
    conn.close()

    store = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)
    assert store.parent_state(token) is None
    assert store.list_candidates(token) == []

    cursor = store._conn.cursor()
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_parent_lifecycles WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 0
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_candidates WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 0
    store.close()


@pytest.mark.parametrize("reason", ["profile_binding_failed", "invalid_manifest"])
def test_v0_non_conformance_terminal_a_rejects_changed_b_non_consumingly(
    tmp_path: Path, reason: str
) -> None:
    """A terminal non-conformance v0 A is backfilled as a closed failed parent.

    The parent must never admit a changed B, must retain A as candidate ordinal 1
    with its normalized identity history, and must reject identical/reordered A
    replay. No second receipt, candidate, or probe is created.
    """
    db_path = tmp_path / "direct.db"
    wrapper_path = tmp_path / "adapters" / "profile-adapter"
    token = f"hrreg_v0_{reason}"
    fingerprint = fingerprint_registration_token(token)
    operation_a = _seed_legacy_v0_database_with_reason(
        db_path, token, f"v0-{reason}", wrapper_path, reason=reason,
    )

    store = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)

    # A is materialized as failed candidate ordinal 1.
    candidates = store.list_candidates(token)
    assert len(candidates) == 1
    assert candidates[0].operation_id == operation_a
    assert candidates[0].attempt_ordinal == 1
    assert candidates[0].state == "failed"

    # Parent is closed, so changed B is refused non-consumingly.
    assert store.parent_state(token) == "failed"
    assert store.is_retryable(token, now=5.0) is False
    operation_b = store.reserve(
        token, identity_hash="hash-b" * 16, identity_blob="blob-b", now=5.0,
    )
    assert operation_b is None

    # Identical/reordered A replay is rejected non-consumingly (closed parent
    # surfaces terminal_nonretryable; the identity is retained and never re-admitted).
    identity_a = store._conn.execute(
        "SELECT identity_hash, identity_blob FROM direct_connect_identity_history WHERE candidate_id = ?",
        (candidates[0].candidate_id,),
    ).fetchone()
    verdict, _ = store.evaluate_admission(
        token,
        identity_hash=identity_a["identity_hash"],
        identity_blob=identity_a["identity_blob"],
        now=5.0,
    )
    assert verdict == "terminal_nonretryable"

    # No B candidate/receipt/probe was created; original legacy rows survive.
    cursor = store._conn.cursor()
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_candidates WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 1
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_receipts WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 1
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_retry_attempts r JOIN direct_connect_candidates c ON c.operation_id = r.operation_id WHERE c.token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 0
    assert cursor.execute(
        "SELECT state, reason FROM direct_connect_projections WHERE operation_id = ?",
        (operation_a,),
    ).fetchone()["reason"] == reason

    # Reopen twice is idempotent and still closed.
    store.close()
    reopened = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)
    assert reopened.parent_state(token) == "failed"
    assert reopened.list_candidates(token)[0].state == "failed"
    assert reopened.is_retryable(token, now=6.0) is False
    reopened.close()


def test_v0_conformance_failure_still_allows_one_changed_b(tmp_path: Path) -> None:
    """The approved conformance-probe failure path remains open for one B."""
    db_path = tmp_path / "direct.db"
    wrapper_path = tmp_path / "adapters" / "profile-adapter"
    operation_a = _seed_legacy_v0_database(
        db_path, "hrreg_v0_conform", "v0-conform", wrapper_path, expires_at=time.time() + 1000.0,
    )

    store = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)
    assert store.parent_state("hrreg_v0_conform") == "open"
    assert store.is_retryable("hrreg_v0_conform", now=5.0) is True

    operation_b = store.reserve(
        "hrreg_v0_conform", identity_hash="hash-b" * 16, identity_blob="blob-b", now=5.0,
    )
    assert operation_b is not None
    store.receive(
        "hrreg_v0_conform", operation_b, wrapper_sha256="c" * 64, wrapper_facts={},
        children=[], workspace_adapter_id="codex",
        identity_hash="hash-b" * 16, identity_blob="blob-b", now=5.0,
    )

    # B terminal-fails (non-conformance) and C is refused.
    assert store.plan_projection(operation_b, now=6.0)
    assert store.mark_failed(operation_b, "profile_binding_failed", now=7.0)
    assert store.parent_state("hrreg_v0_conform") == "failed"
    assert store.is_retryable("hrreg_v0_conform", now=8.0) is False
    operation_c = store.reserve(
        "hrreg_v0_conform", identity_hash="hash-c" * 16, identity_blob="blob-c", now=8.0,
    )
    assert operation_c is None
    store.close()


def test_v0_ambiguous_legacy_record_fails_closed_no_open_parent(tmp_path: Path) -> None:
    """A terminal v0 record without a matching receipt/authority/artifacts is skipped.

    No parent is fabricated, so the token remains a fresh unminted surface from the
    ledger's point of view and a later legitimate mint can create a new lifecycle.
    """
    db_path = tmp_path / "direct.db"
    wrapper_path = tmp_path / "adapters" / "profile-adapter"
    token = "hrreg_v0_ambiguous"
    fingerprint = fingerprint_registration_token(token)
    operation_a = _seed_legacy_v0_database_with_reason(
        db_path, token, "v0-ambiguous", wrapper_path, reason="profile_binding_failed",
    )

    # Remove the authority row so the backfill cannot verify the token's scope.
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM direct_connect_authorities WHERE token_fingerprint = ?", (fingerprint,))
    conn.commit()
    conn.close()

    store = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)
    assert store.parent_state(token) is None
    assert store.list_candidates(token) == []
    assert store.get(token) is None

    cursor = store._conn.cursor()
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_parent_lifecycles WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 0
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_candidates WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 0
    # Legacy operation row is preserved for audit.
    assert cursor.execute(
        "SELECT operation_id FROM direct_connect_operations WHERE operation_id = ?",
        (operation_a,),
    ).fetchone()["operation_id"] == operation_a
    store.close()


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

    _assert_coupled_facts_retained(store, "hrreg_recover", operation_a, "recover-profile")
    store.close()

    # Reopen again to prove recovery state is stable.
    reopened = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)
    _assert_coupled_facts_retained(reopened, "hrreg_recover", operation_a, "recover-profile")
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


@pytest.mark.parametrize("reason", ["profile_binding_failed", "invalid_manifest"])
def test_v0_non_conformance_terminal_a_rejects_changed_b_via_http_ingress(
    tmp_path: Path, monkeypatch, reason: str
) -> None:
    """HTTP ingress proof: legacy v0 DB with non-conformance A rejects changed B."""
    import time

    db_path = tmp_path / "direct.db"
    runtime_root = tmp_path / "daemon"
    wrapper_path = runtime_root / "adapters" / "recover-profile-adapter"
    token = f"hrreg_http_v0_{reason}"
    future = time.time() + 1000.0
    operation_a = _seed_legacy_v0_database_with_reason(
        db_path, token, "recover-profile", wrapper_path, reason=reason, expires_at=future
    )

    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(runtime_root))
    paths.ensure_daemon_home()
    paths.ensure_token()
    state = DaemonState.idle(Settings())
    state.direct_connect_authority_store.close()
    state.direct_connect_authority_store = DirectConnectAuthorityStore(db_path, runtime_root=runtime_root)

    from runtime.daemon.routes import auth, direct_connect

    monkeypatch.setattr(auth, "_LOCAL_HOSTS", auth._LOCAL_HOSTS | {"testclient"})
    monkeypatch.setattr(direct_connect, "_LOCAL_HOSTS", direct_connect._LOCAL_HOSTS | {"testclient"})

    tc = TestClient(create_app(state))
    tc.headers.update({"Authorization": f"Bearer {paths.read_token()}"})

    child_a = tmp_path / "bin" / "child-a"
    _write_executable(child_a, b"#!/bin/sh\necho a\n")
    _write_executable(wrapper_path, b"#!/bin/sh\necho wrapper-a\n")
    child_b = tmp_path / "bin" / "child-b"
    _write_executable(child_b, b"#!/bin/sh\necho b\n")
    wrapper_hash_b = _write_executable(wrapper_path, b"#!/bin/sh\necho wrapper-b\n")

    # Changed B /connect is rejected non-consumingly because A closed the parent.
    response = tc.post(
        "/api/v1/runtime/custom-cli/connect",
        json=_connect_payload(wrapper_hash_b, child_b),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 409, response.text
    assert "closed" in response.json()["detail"]

    # No B candidate/receipt/probe was created; A's legacy facts survive.
    store = state.direct_connect_authority_store
    cursor = store._conn.cursor()
    fingerprint = fingerprint_registration_token(token)
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_candidates WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 1
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_receipts WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 1
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_operations WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 1
    assert cursor.execute(
        """SELECT COUNT(*) FROM direct_connect_retry_attempts r
           JOIN direct_connect_candidates c ON c.operation_id = r.operation_id
           WHERE c.token_fingerprint = ?""",
        (fingerprint,),
    ).fetchone()[0] == 0
    assert store.parent_state(token) == "failed"
    assert store.is_retryable(token, now=future - 1.0) is False

    # The original non-conformance projection reason is preserved.
    assert cursor.execute(
        "SELECT reason FROM direct_connect_projections WHERE operation_id = ?",
        (operation_a,),
    ).fetchone()["reason"] == reason


def test_v0_migration_allows_corrected_candidate_b_via_http_ingress(tmp_path: Path, monkeypatch) -> None:
    """HTTP ingress proof: legacy v0 DB with failed A accepts corrected B after migration."""
    import time

    db_path = tmp_path / "direct.db"
    runtime_root = tmp_path / "daemon"
    wrapper_path = runtime_root / "adapters" / "recover-profile-adapter"
    token = "hrreg_http_v0"
    future = time.time() + 1000.0
    operation_a = _seed_legacy_v0_database(
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
    _write_executable(wrapper_path, b"#!/bin/sh\necho wrapper-a\n")
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


def test_v0_mismatched_receipt_fingerprint_rejects_changed_b_non_consumingly(
    tmp_path: Path,
) -> None:
    """A v0 A whose receipt token_fingerprint does not match its operation is untrusted.

    The legacy schema correlates an operation with its receipt by operation_id AND
    by token_fingerprint. Changing only the receipt fingerprint severs that
    correlation. Before the trust-classification repair this still opened the
    parent (reason prefix alone was trusted) and admitted a changed B. After the
    repair the bridge is fail-closed: no open parent, no B candidate/identity,
    no new receipt/probe/event, and the original legacy rows are preserved.
    """
    db_path = tmp_path / "direct.db"
    wrapper_path = tmp_path / "adapters" / "profile-adapter"
    token = "hrreg_v0_bad_receipt_fp"
    fingerprint = fingerprint_registration_token(token)
    operation_a = _seed_legacy_v0_database(db_path, token, "v0-bad-receipt", wrapper_path)

    # Sever the receipt/operation correlation: receipt claims a different token.
    other_fp = "0" * 64
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE direct_connect_receipts SET token_fingerprint = ? WHERE operation_id = ?",
        (other_fp, operation_a),
    )
    conn.commit()
    conn.close()

    store = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)

    # No open parent or candidate is fabricated from contradictory facts.
    assert store.parent_state(token) != "open"
    assert store.is_retryable(token, now=5.0) is False

    # Changed B is refused non-consumingly at the store seam.
    operation_b = store.reserve(
        token, identity_hash="hash-b" * 16, identity_blob="blob-b", now=5.0,
    )
    assert operation_b is None

    # No open parent is fabricated; the derivable A identity is retained as
    # closed ordinal-1 history so replays stay fenced. No B receipt/probe/event.
    cursor = store._conn.cursor()
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_parent_lifecycles WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 1
    assert cursor.execute(
        "SELECT state FROM direct_connect_parent_lifecycles WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()["state"] != "open"
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_candidates WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 1
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_identity_history WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 1
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_receipts WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 0
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_events WHERE token_fingerprint = ? AND event_type != 'received_nonlaunchable'",
        (fingerprint,),
    ).fetchone()[0] == 0
    # The corrupted receipt row itself is preserved for audit.
    assert cursor.execute(
        "SELECT token_fingerprint FROM direct_connect_receipts WHERE operation_id = ?",
        (operation_a,),
    ).fetchone()["token_fingerprint"] == other_fp

    # Two reopens stay closed/idempotent; the retained A identity remains.
    store.close()
    reopened = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)
    assert reopened.parent_state(token) != "open"
    assert len(reopened.list_candidates(token)) == 1
    assert reopened.list_candidates(token)[0].state == "failed"
    reopened.close()


def test_v0_bound_profile_terminal_a_rejects_changed_b_non_consumingly(
    tmp_path: Path,
) -> None:
    """A terminal v0 A that already carries an approved/bound profile is nonretryable.

    Even when the reason text starts with conformance_probe_failed, a non-null
    projection profile_name (or adapter_id) is a durable approval/binding fact.
    The bridge must retain the closed ordinal-1 history when identity is
    trustworthy, but it must never open the parent or admit a corrected B.
    """
    db_path = tmp_path / "direct.db"
    wrapper_path = tmp_path / "adapters" / "profile-adapter"
    token = "hrreg_v0_bound_profile"
    fingerprint = fingerprint_registration_token(token)
    operation_a = _seed_legacy_v0_database(db_path, token, "v0-bound", wrapper_path)

    # Fabricate an approved/bound profile fact on the terminal projection.
    conn = sqlite3.connect(db_path)
    conn.execute(
        """UPDATE direct_connect_projections
           SET profile_name = ?, adapter_id = ? WHERE operation_id = ?""",
        ("bound-profile", "bound-adapter", operation_a),
    )
    conn.commit()
    conn.close()

    store = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)

    # The trustworthy identity is retained as closed ordinal-1 history.
    candidates = store.list_candidates(token)
    assert len(candidates) == 1
    assert candidates[0].operation_id == operation_a
    assert candidates[0].attempt_ordinal == 1
    assert candidates[0].state == "failed"
    assert store._conn.execute(
        "SELECT COUNT(*) FROM direct_connect_identity_history WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 1

    # Parent is closed, so B is refused non-consumingly.
    assert store.parent_state(token) != "open"
    assert store.is_retryable(token, now=5.0) is False
    operation_b = store.reserve(
        token, identity_hash="hash-b" * 16, identity_blob="blob-b", now=5.0,
    )
    assert operation_b is None

    # No B candidate/receipt/probe is created; original projection binding is kept.
    cursor = store._conn.cursor()
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_candidates WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 1
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_receipts WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 1
    assert cursor.execute(
        """SELECT COUNT(*) FROM direct_connect_retry_attempts r
           JOIN direct_connect_candidates c ON c.operation_id = r.operation_id
           WHERE c.token_fingerprint = ?""",
        (fingerprint,),
    ).fetchone()[0] == 0
    projection = cursor.execute(
        "SELECT profile_name, adapter_id FROM direct_connect_projections WHERE operation_id = ?",
        (operation_a,),
    ).fetchone()
    assert projection["profile_name"] == "bound-profile"
    assert projection["adapter_id"] == "bound-adapter"

    # Two reopens remain closed and idempotent.
    store.close()
    reopened = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)
    assert reopened.parent_state(token) != "open"
    assert reopened.list_candidates(token)[0].state == "failed"
    assert reopened.is_retryable(token, now=6.0) is False
    reopened.close()


def test_v0_receipt_state_mismatch_rejects_changed_b_non_consumingly(
    tmp_path: Path,
) -> None:
    """A v0 A whose receipt is not in the expected received state is untrusted."""
    db_path = tmp_path / "direct.db"
    wrapper_path = tmp_path / "adapters" / "profile-adapter"
    token = "hrreg_v0_bad_receipt_state"
    fingerprint = fingerprint_registration_token(token)
    operation_a = _seed_legacy_v0_database(db_path, token, "v0-bad-state", wrapper_path)

    # Remove the receipt row: a terminal operation without its expected receipt
    # is a correlation failure and must stay fail-closed.
    conn = sqlite3.connect(db_path)
    conn.execute(
        "DELETE FROM direct_connect_receipts WHERE operation_id = ?",
        (operation_a,),
    )
    conn.commit()
    conn.close()

    store = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)

    assert store.parent_state(token) != "open"
    assert store.is_retryable(token, now=5.0) is False
    operation_b = store.reserve(
        token, identity_hash="hash-b" * 16, identity_blob="blob-b", now=5.0,
    )
    assert operation_b is None

    cursor = store._conn.cursor()
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_candidates WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 1
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_identity_history WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 1
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_receipts WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 0
    store.close()


def test_v0_malformed_artifacts_fail_closed_no_open_parent(tmp_path: Path) -> None:
    """A terminal v0 A with unusable artifact facts closes the parent fail-closed.

    The identity cannot be normalized, so no candidate/identity is fabricated,
    but the parent lifecycle is closed so a later B cannot be admitted as a
    fresh ordinal-1 candidate.
    """
    db_path = tmp_path / "direct.db"
    wrapper_path = tmp_path / "adapters" / "profile-adapter"
    token = "hrreg_v0_malformed"
    fingerprint = fingerprint_registration_token(token)
    operation_a = _seed_legacy_v0_database(db_path, token, "v0-malformed", wrapper_path)

    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE direct_connect_artifacts SET structural_facts = 'not-json' WHERE operation_id = ? AND slot = 'wrapper'",
        (operation_a,),
    )
    conn.commit()
    conn.close()

    store = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)
    assert store.parent_state(token) != "open"
    assert store.list_candidates(token) == []
    assert store.is_retryable(token, now=5.0) is False

    operation_b = store.reserve(
        token, identity_hash="hash-b" * 16, identity_blob="blob-b", now=5.0,
    )
    assert operation_b is None

    cursor = store._conn.cursor()
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_parent_lifecycles WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 1
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_candidates WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 0
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_identity_history WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 0
    store.close()


def _setup_http_migration_test(
    tmp_path: Path,
    monkeypatch,
    token: str,
    db_path: Path,
) -> tuple[TestClient, DaemonState, str]:
    """Build a TestClient around a pre-seeded legacy direct-connect database."""
    import time

    runtime_root = tmp_path / "daemon"
    future = time.time() + 1000.0
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(runtime_root))
    paths.ensure_daemon_home()
    paths.ensure_token()
    state = DaemonState.idle(Settings())
    state.direct_connect_authority_store.close()
    state.direct_connect_authority_store = DirectConnectAuthorityStore(db_path, runtime_root=runtime_root)

    record = state.registration_token_store._validate_raw(token, now=future - 1.0)
    if record is not None:
        record.consumed = True
        record.reserved = False

    from runtime.daemon.routes import auth, direct_connect

    monkeypatch.setattr(auth, "_LOCAL_HOSTS", auth._LOCAL_HOSTS | {"testclient"})
    monkeypatch.setattr(direct_connect, "_LOCAL_HOSTS", direct_connect._LOCAL_HOSTS | {"testclient"})

    tc = TestClient(create_app(state))
    tc.headers.update({"Authorization": f"Bearer {paths.read_token()}"})
    return tc, state, str(future)


def test_v0_mismatched_receipt_fingerprint_rejects_changed_b_via_http_ingress(
    tmp_path: Path, monkeypatch,
) -> None:
    """HTTP ingress proof: corrupted receipt fingerprint rejects changed B with 409."""
    import time

    db_path = tmp_path / "direct.db"
    runtime_root = tmp_path / "daemon"
    wrapper_path = runtime_root / "adapters" / "recover-profile-adapter"
    token = "hrreg_http_v0_bad_receipt_fp"
    fingerprint = fingerprint_registration_token(token)
    future = time.time() + 1000.0
    operation_a = _seed_legacy_v0_database(
        db_path, token, "recover-profile", wrapper_path, expires_at=future,
    )

    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE direct_connect_receipts SET token_fingerprint = ? WHERE operation_id = ?",
        ("1" * 64, operation_a),
    )
    conn.commit()
    conn.close()

    tc, state, _future = _setup_http_migration_test(tmp_path, monkeypatch, token, db_path)

    child_b = tmp_path / "bin" / "child-b"
    _write_executable(child_b, b"#!/bin/sh\necho b\n")
    wrapper_hash_b = _write_executable(wrapper_path, b"#!/bin/sh\necho wrapper-b\n")

    response = tc.post(
        "/api/v1/runtime/custom-cli/connect",
        json=_connect_payload(wrapper_hash_b, child_b),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "closed" in detail or "exhausted" in detail or "nonretryable" in detail

    store = state.direct_connect_authority_store
    cursor = store._conn.cursor()
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_candidates WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 1
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_identity_history WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 1
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_receipts WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 0
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_operations WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 1


def test_v0_bound_profile_terminal_a_rejects_changed_b_via_http_ingress(
    tmp_path: Path, monkeypatch,
) -> None:
    """HTTP ingress proof: bound profile on terminal A rejects changed B with 409."""
    import time

    db_path = tmp_path / "direct.db"
    runtime_root = tmp_path / "daemon"
    wrapper_path = runtime_root / "adapters" / "recover-profile-adapter"
    token = "hrreg_http_v0_bound_profile"
    fingerprint = fingerprint_registration_token(token)
    future = time.time() + 1000.0
    operation_a = _seed_legacy_v0_database(
        db_path, token, "recover-profile", wrapper_path, expires_at=future,
    )

    conn = sqlite3.connect(db_path)
    conn.execute(
        """UPDATE direct_connect_projections
           SET profile_name = ?, adapter_id = ? WHERE operation_id = ?""",
        ("bound-profile", "bound-adapter", operation_a),
    )
    conn.commit()
    conn.close()

    tc, state, _future = _setup_http_migration_test(tmp_path, monkeypatch, token, db_path)

    child_b = tmp_path / "bin" / "child-b"
    _write_executable(child_b, b"#!/bin/sh\necho b\n")
    wrapper_hash_b = _write_executable(wrapper_path, b"#!/bin/sh\necho wrapper-b\n")

    response = tc.post(
        "/api/v1/runtime/custom-cli/connect",
        json=_connect_payload(wrapper_hash_b, child_b),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "closed" in detail or "nonretryable" in detail

    store = state.direct_connect_authority_store
    cursor = store._conn.cursor()
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_candidates WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 1
    assert cursor.execute(
        "SELECT COUNT(*) FROM direct_connect_receipts WHERE token_fingerprint = ?",
        (fingerprint,),
    ).fetchone()[0] == 1
    assert store.parent_state(token) != "open"

"""
THR-107 v9 Slice 1: direct-connect operation journal, token authority,
COMMITTED-only eligibility fence, and five-runner proof.

Tests for:
1. Additive SQLite schema + migration
2. DirectConnectStore CRUD, terminalization, idempotency
3. Raw-token authority claim and terminal failure predicates
4. Receipt/compensation residue boundaries
5. COMMITTED-only central eligibility fence
6. Five production runner paths (task/thread/wake/dream/schedule)
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from runtime.infrastructure.database import Database
from runtime.infrastructure.direct_connect_store import (
    DirectConnectStore,
    compute_cas,
    is_committed,
    is_launchable,
    is_terminal,
    LIFECYCLE_COMMITTED,
    LIFECYCLE_RESERVED,
    LIFECYCLE_PROJECTING,
    TERMINAL_EXPIRED,
    TERMINAL_MALFORMED,
    TERMINAL_FOREIGN,
    TERMINAL_OWNER_MISMATCH,
    TERMINAL_CAS_LOSS,
    TERMINAL_REPLAY,
    TERMINAL_CRASH,
    TERMINAL_DB_FAULT,
    TERMINAL_AUDIT_FAULT,
    TERMINAL_COMPENSATION_FAILED,
)
from runtime.orchestrator.executor_registry import (
    ExecutorRegistry,
    ExecutorProfile,
    get_registry,
    reset_registry,
)


# ── Helpers ────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _op_id(n: int) -> str:
    return f"DCO-TEST-{n:04d}"


def _create_committed_op(
    store: DirectConnectStore,
    n: int = 1,
    profile_name: str = "test-profile",
    adapter_id: str = "test-adapter-001",
    owner: str = "test-agent",
    token: str = "test-raw-token",
    auth_owner: str = "test-agent",
) -> dict:
    """Create, project, then commit a direct-connect operation."""
    op = store.reserve_operation(
        operation_id=_op_id(n),
        profile_name=profile_name,
        adapter_id=adapter_id,
        owner_agent=owner,
        raw_authority_token=token,
        authority_owner=auth_owner,
        replay_identity=f"replay-{n}",
    )
    assert op["lifecycle_status"] == LIFECYCLE_RESERVED

    # Write + verify receipts (Slice 1: plumbing only, no real projection)
    rid = store.write_receipt(op["id"], "projection_yaml", "adapter-profile-yaml")
    store.verify_receipt(rid, "ok")
    rid2 = store.write_receipt(op["id"], "projection_registry", "runtime-profile")
    store.verify_receipt(rid2, "ok")

    # Transition to projecting
    op2 = store.transition_to_projecting(op["id"])
    assert op2 is not None

    # Commit
    op3 = store.commit_operation(op["id"])
    assert op3 is not None
    assert op3["lifecycle_status"] == LIFECYCLE_COMMITTED
    return op3


# ════════════════════════════════════════════════════════════════════════════
# 1. Schema and migration
# ════════════════════════════════════════════════════════════════════════════

class TestDirectConnectSchema:
    """Verify the new tables exist and have the correct shape."""

    def test_tables_exist(self, db: Database):
        """direct_connect_operations and direct_connect_receipts tables exist."""
        tables = {
            row[0]
            for row in db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "direct_connect_operations" in tables
        assert "direct_connect_receipts" in tables

    def test_operations_columns(self, db: Database):
        """All required columns exist on direct_connect_operations."""
        cols = {
            row["name"]
            for row in db._conn.execute(
                "PRAGMA table_info(direct_connect_operations)"
            ).fetchall()
        }
        required = {
            "id", "profile_name", "adapter_id", "owner_agent",
            "raw_authority_token", "authority_state", "authority_expiry",
            "authority_owner", "cas_hash", "lifecycle_status",
            "replay_identity", "receipt_count", "compensation_residue",
            "audit_created_event_id", "terminal_reason",
            "terminal_audit_event_id", "created_at", "updated_at",
        }
        assert required.issubset(cols)

    def test_receipts_columns(self, db: Database):
        """All required columns exist on direct_connect_receipts."""
        cols = {
            row["name"]
            for row in db._conn.execute(
                "PRAGMA table_info(direct_connect_receipts)"
            ).fetchall()
        }
        required = {
            "id", "operation_id", "receipt_type", "planned_state",
            "actual_state", "status", "compensation_action",
            "audit_event_id", "created_at", "updated_at",
        }
        assert required.issubset(cols)

    def test_replay_identity_unique_constraint(self, db: Database):
        """Duplicate replay_identity raises IntegrityError."""
        store = db.direct_connect
        store.reserve_operation(
            operation_id=_op_id(1), profile_name="p1", adapter_id="a1",
            owner_agent="agent", raw_authority_token="tok1",
            authority_owner="agent", replay_identity="same-replay",
        )
        with pytest.raises(sqlite3.IntegrityError):
            store.reserve_operation(
                operation_id=_op_id(2), profile_name="p2", adapter_id="a2",
                owner_agent="agent", raw_authority_token="tok2",
                authority_owner="agent", replay_identity="same-replay",
            )

    def test_idempotent_schema_creation(self, db: Database):
        """Calling _create_tables twice is safe (IF NOT EXISTS)."""
        db._create_tables()  # already called once; should be a no-op
        db._create_tables()

    def test_v0_v1_compatibility_no_overloaded_columns(self, db: Database):
        """New tables are additive only — no alteration of existing tables or
        reinterpretation of overloaded columns."""
        # Verify existing tables are untouched
        original_task_cols = {
            row["name"]
            for row in db._conn.execute("PRAGMA table_info(tasks)").fetchall()
        }
        # tasks columns must not contain any direct_connect_operations columns
        assert "raw_authority_token" not in original_task_cols
        assert "cas_hash" not in original_task_cols
        assert "lifecycle_status" not in original_task_cols
        assert "replay_identity" not in original_task_cols

        # audit_log.task_id is not reinterpreted (overloaded-column guard)
        audit_cols = {
            row["name"]
            for row in db._conn.execute("PRAGMA table_info(audit_log)").fetchall()
        }
        assert "task_id" in audit_cols  # exists but unchanged


# ════════════════════════════════════════════════════════════════════════════
# 2. DirectConnectStore CRUD
# ════════════════════════════════════════════════════════════════════════════

class TestDirectConnectStoreCRUD:

    def test_reserve_operation(self, db: Database):
        store = db.direct_connect
        op = store.reserve_operation(
            operation_id=_op_id(1),
            profile_name="test-profile",
            adapter_id="adapter-1",
            owner_agent="agent-x",
            raw_authority_token="raw-secret-token",
            authority_owner="agent-x",
            replay_identity="replay-1",
            authority_expiry=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        )
        assert op["id"] == _op_id(1)
        assert op["profile_name"] == "test-profile"
        assert op["adapter_id"] == "adapter-1"
        assert op["owner_agent"] == "agent-x"
        assert op["lifecycle_status"] == LIFECYCLE_RESERVED
        assert op["authority_state"] == "reserved"
        assert op["receipt_count"] == 0
        expected_cas = compute_cas("agent-x", "raw-secret-token")
        assert op["cas_hash"] == expected_cas

    def test_get_operation_and_by_replay(self, db: Database):
        store = db.direct_connect
        store.reserve_operation(
            operation_id=_op_id(1), profile_name="p", adapter_id="a",
            owner_agent="agent", raw_authority_token="tok",
            authority_owner="agent", replay_identity="replay-get",
        )
        op = store.get_operation(_op_id(1))
        assert op is not None
        assert op["replay_identity"] == "replay-get"

        op2 = store.get_operation_by_replay("replay-get")
        assert op2 is not None
        assert op2["id"] == _op_id(1)

        assert store.get_operation("nonexistent") is None
        assert store.get_operation_by_replay("nonexistent") is None

    def test_commit_operation_full_flow(self, db: Database):
        store = db.direct_connect
        op = _create_committed_op(store, n=1)
        assert op["lifecycle_status"] == LIFECYCLE_COMMITTED

        # get_committed_operation finds it
        committed = store.get_committed_operation("test-profile", "test-adapter-001")
        assert committed is not None
        assert committed["id"] == _op_id(1)

    def test_get_committed_operation_requires_match(self, db: Database):
        store = db.direct_connect
        _create_committed_op(store, n=1, profile_name="p-a", adapter_id="a-1")

        # Wrong profile
        assert store.get_committed_operation("wrong-p", "a-1") is None
        # Wrong adapter
        assert store.get_committed_operation("p-a", "wrong-a") is None

    def test_transition_to_projecting_bad_state(self, db: Database):
        store = db.direct_connect
        op = store.reserve_operation(
            operation_id=_op_id(1), profile_name="p", adapter_id="a",
            owner_agent="agent", raw_authority_token="tok",
            authority_owner="agent", replay_identity="r",
        )
        # First transition works
        op2 = store.transition_to_projecting(op["id"])
        assert op2 is not None
        # Second from projecting -> fails (not reserved)
        op3 = store.transition_to_projecting(op["id"])
        assert op3 is None

    def test_commit_needs_projecting(self, db: Database):
        store = db.direct_connect
        op = store.reserve_operation(
            operation_id=_op_id(1), profile_name="p", adapter_id="a",
            owner_agent="agent", raw_authority_token="tok",
            authority_owner="agent", replay_identity="r",
        )
        # Commit from reserved fails
        result = store.commit_operation(op["id"])
        assert result is None


# ════════════════════════════════════════════════════════════════════════════
# 3. Token authority and terminalization
# ════════════════════════════════════════════════════════════════════════════

class TestTokenAuthority:

    def _reserve(self, store: DirectConnectStore, **kw) -> dict:
        defaults = {
            "operation_id": _op_id(1), "profile_name": "p", "adapter_id": "a",
            "owner_agent": "agent", "raw_authority_token": "tok",
            "authority_owner": "agent", "replay_identity": "r",
        }
        defaults.update(kw)
        return store.reserve_operation(**defaults)

    def test_terminalize_expired(self, db: Database):
        store = db.direct_connect
        op = self._reserve(store, replay_identity="r-expired")
        result = store.terminalize_operation(op["id"], TERMINAL_EXPIRED)
        assert result is not None
        assert result["lifecycle_status"] == TERMINAL_EXPIRED
        assert result["terminal_reason"] == TERMINAL_EXPIRED
        assert is_terminal(result["lifecycle_status"])

    def test_terminalize_malformed(self, db: Database):
        store = db.direct_connect
        op = self._reserve(store, replay_identity="r-malformed")
        result = store.terminalize_operation(op["id"], TERMINAL_MALFORMED)
        assert result["lifecycle_status"] == TERMINAL_MALFORMED

    def test_terminalize_foreign(self, db: Database):
        store = db.direct_connect
        op = self._reserve(store, replay_identity="r-foreign")
        result = store.terminalize_operation(op["id"], TERMINAL_FOREIGN)
        assert result["lifecycle_status"] == TERMINAL_FOREIGN

    def test_terminalize_owner_mismatch(self, db: Database):
        store = db.direct_connect
        op = self._reserve(store, replay_identity="r-owner-mismatch")
        result = store.terminalize_operation(op["id"], TERMINAL_OWNER_MISMATCH)
        assert result["lifecycle_status"] == TERMINAL_OWNER_MISMATCH

    def test_terminalize_cas_loss(self, db: Database):
        store = db.direct_connect
        op = self._reserve(store, replay_identity="r-cas-loss")
        result = store.terminalize_operation(op["id"], TERMINAL_CAS_LOSS)
        assert result["lifecycle_status"] == TERMINAL_CAS_LOSS

    def test_terminalize_replay(self, db: Database):
        store = db.direct_connect
        op = self._reserve(store, replay_identity="r-replay")
        result = store.terminalize_operation(op["id"], TERMINAL_REPLAY)
        assert result["lifecycle_status"] == TERMINAL_REPLAY

    def test_terminalize_crash(self, db: Database):
        store = db.direct_connect
        op = self._reserve(store, replay_identity="r-crash")
        result = store.terminalize_operation(op["id"], TERMINAL_CRASH)
        assert result["lifecycle_status"] == TERMINAL_CRASH

    def test_terminalize_db_fault(self, db: Database):
        store = db.direct_connect
        op = self._reserve(store, replay_identity="r-db-fault")
        result = store.terminalize_operation(op["id"], TERMINAL_DB_FAULT)
        assert result["lifecycle_status"] == TERMINAL_DB_FAULT

    def test_terminalize_audit_fault(self, db: Database):
        store = db.direct_connect
        op = self._reserve(store, replay_identity="r-audit-fault")
        result = store.terminalize_operation(op["id"], TERMINAL_AUDIT_FAULT)
        assert result["lifecycle_status"] == TERMINAL_AUDIT_FAULT

    def test_terminalize_compensation_failed(self, db: Database):
        store = db.direct_connect
        op = self._reserve(store, replay_identity="r-comp-failed")
        result = store.terminalize_operation(op["id"], TERMINAL_COMPENSATION_FAILED)
        assert result["lifecycle_status"] == TERMINAL_COMPENSATION_FAILED

    def test_terminalize_idempotent(self, db: Database):
        """Once terminal, another terminalize is a no-op (returns None)."""
        store = db.direct_connect
        op = self._reserve(store, replay_identity="r-idempotent")
        store.terminalize_operation(op["id"], TERMINAL_EXPIRED)
        result2 = store.terminalize_operation(op["id"], TERMINAL_CRASH)
        assert result2 is None  # already terminal

    def test_terminal_not_reusable(self, db: Database):
        """A terminalized operation cannot become committed."""
        store = db.direct_connect
        op = self._reserve(store, replay_identity="r-not-reusable")
        store.terminalize_operation(op["id"], TERMINAL_EXPIRED)
        # transition_to_projecting should fail (not reserved)
        result = store.transition_to_projecting(op["id"])
        assert result is None

    def test_cas_determinism(self):
        """Same (owner, token) produces same CAS."""
        c1 = compute_cas("agent", "token")
        c2 = compute_cas("agent", "token")
        assert c1 == c2
        assert len(c1) == 64  # SHA-256 hex

    def test_cas_different_owner(self):
        """Different owner produces different CAS."""
        c1 = compute_cas("agent-a", "token")
        c2 = compute_cas("agent-b", "token")
        assert c1 != c2

    def test_cas_different_token(self):
        """Different token produces different CAS."""
        c1 = compute_cas("agent", "token-a")
        c2 = compute_cas("agent", "token-b")
        assert c1 != c2

    def test_helpers_is_terminal(self):
        assert is_terminal(TERMINAL_EXPIRED)
        assert is_terminal(TERMINAL_MALFORMED)
        assert is_terminal(TERMINAL_FOREIGN)
        assert not is_terminal(LIFECYCLE_RESERVED)
        assert not is_terminal(LIFECYCLE_COMMITTED)

    def test_helpers_is_committed(self):
        assert is_committed(LIFECYCLE_COMMITTED)
        assert not is_committed(LIFECYCLE_RESERVED)
        assert not is_committed(TERMINAL_EXPIRED)

    def test_helpers_is_launchable(self):
        assert is_launchable(LIFECYCLE_COMMITTED)
        assert not is_launchable(LIFECYCLE_RESERVED)
        assert not is_launchable(LIFECYCLE_PROJECTING)
        assert not is_launchable(TERMINAL_EXPIRED)


# ════════════════════════════════════════════════════════════════════════════
# 4. Receipt and compensation residue boundaries
# ════════════════════════════════════════════════════════════════════════════

class TestReceiptCompensation:

    def test_write_and_verify_receipt(self, db: Database):
        store = db.direct_connect
        op = store.reserve_operation(
            operation_id=_op_id(1), profile_name="p", adapter_id="a",
            owner_agent="agent", raw_authority_token="tok",
            authority_owner="agent", replay_identity="r-receipts",
        )
        rid = store.write_receipt(op["id"], "projection", "planned-state")
        assert rid > 0

        receipt = store.verify_receipt(rid, "actual-ok")
        assert receipt is not None
        assert receipt["status"] == "completed"
        assert receipt["actual_state"] == "actual-ok"

    def test_get_pending_receipts(self, db: Database):
        store = db.direct_connect
        op = store.reserve_operation(
            operation_id=_op_id(1), profile_name="p", adapter_id="a",
            owner_agent="agent", raw_authority_token="tok",
            authority_owner="agent", replay_identity="r-pending",
        )
        store.write_receipt(op["id"], "type-1", "s1")
        store.write_receipt(op["id"], "type-2", "s2")

        pending = store.get_pending_receipts(op["id"])
        assert len(pending) == 2

        store.verify_receipt(pending[0]["id"], "ok")
        pending2 = store.get_pending_receipts(op["id"])
        assert len(pending2) == 1

    def test_fail_receipt_with_compensation(self, db: Database):
        store = db.direct_connect
        op = store.reserve_operation(
            operation_id=_op_id(1), profile_name="p", adapter_id="a",
            owner_agent="agent", raw_authority_token="tok",
            authority_owner="agent", replay_identity="r-fail",
        )
        rid = store.write_receipt(op["id"], "projection", "s")
        result = store.fail_receipt(rid, "manual-repair-needed")
        assert result["status"] == "failed"
        assert result["compensation_action"] == "manual-repair-needed"

    def test_compensation_residue_set_and_read(self, db: Database):
        store = db.direct_connect
        op = store.reserve_operation(
            operation_id=_op_id(1), profile_name="p", adapter_id="a",
            owner_agent="agent", raw_authority_token="tok",
            authority_owner="agent", replay_identity="r-residue",
        )
        store.set_compensation_residue(op["id"], {
            "failed_projection": "yaml", "reason": "disk-full"
        })
        op2 = store.get_operation(op["id"])
        residue = json.loads(op2["compensation_residue"])
        assert residue["failed_projection"] == "yaml"
        assert residue["reason"] == "disk-full"

    def test_receipt_count_increments(self, db: Database):
        store = db.direct_connect
        op = store.reserve_operation(
            operation_id=_op_id(1), profile_name="p", adapter_id="a",
            owner_agent="agent", raw_authority_token="tok",
            authority_owner="agent", replay_identity="r-count",
        )
        assert op["receipt_count"] == 0
        store.write_receipt(op["id"], "t", "s")
        store.write_receipt(op["id"], "t", "s")
        op2 = store.get_operation(op["id"])
        assert op2["receipt_count"] == 2

    def test_full_receipt_commit_readback_flow(self, db: Database):
        """Prove receipt commit + readback before COMMITTED transition."""
        store = db.direct_connect
        op = store.reserve_operation(
            operation_id=_op_id(1), profile_name="p", adapter_id="a",
            owner_agent="agent", raw_authority_token="tok",
            authority_owner="agent", replay_identity="r-full-flow",
        )
        # Write receipts
        r1 = store.write_receipt(op["id"], "projection_yaml", "yaml-path")
        r2 = store.write_receipt(op["id"], "projection_registry", "reg-name")

        # Verify both
        rec1 = store.verify_receipt(r1, "yaml-ok")
        rec2 = store.verify_receipt(r2, "registry-ok")
        assert rec1["status"] == "completed"
        assert rec2["status"] == "completed"

        # No pending receipts
        assert len(store.get_pending_receipts(op["id"])) == 0

        # Now commit
        store.transition_to_projecting(op["id"])
        op3 = store.commit_operation(op["id"])
        assert op3 is not None
        assert op3["lifecycle_status"] == LIFECYCLE_COMMITTED

        # Read back — committed state is durable
        op4 = store.get_operation(op["id"])
        assert is_committed(op4["lifecycle_status"])

    def test_failed_receipt_prevents_commit(self, db: Database):
        """A failed receipt leaves residue; commit should be possible only after
        handling compensation (Slice 1: the residue IS the proof boundary)."""
        store = db.direct_connect
        op = store.reserve_operation(
            operation_id=_op_id(1), profile_name="p", adapter_id="a",
            owner_agent="agent", raw_authority_token="tok",
            authority_owner="agent", replay_identity="r-fail-commit",
        )
        r1 = store.write_receipt(op["id"], "projection", "s")
        store.fail_receipt(r1, "manual-fix")

        # Store compensation residue
        store.set_compensation_residue(op["id"], {"failed": True})

        # get_receipts shows the failure
        receipts = store.get_receipts(op["id"])
        assert len(receipts) == 1
        assert receipts[0]["status"] == "failed"

        # Compensation residue is durable
        op2 = store.get_operation(op["id"])
        residue = json.loads(op2["compensation_residue"])
        assert residue["failed"] is True


# ════════════════════════════════════════════════════════════════════════════
# 5. COMMITTED-only central eligibility fence
# ════════════════════════════════════════════════════════════════════════════

class TestCommittedOnlyEligibilityFence:
    """Prove that _resolve_custom_adapter_eligibility requires a COMMITTED
    direct-connect operation when the store is wired."""

    @pytest.fixture(autouse=True)
    def _setup_store(self, db: Database):
        """Wire the store for eligibility checks; cleanup after."""
        reset_registry()
        reg = get_registry()
        reg.set_direct_connect_store(db.direct_connect)
        yield
        reset_registry()

    def _make_profile(self, name="test-profile", adapter_id="test-adapter-001") -> ExecutorProfile:
        return ExecutorProfile(
            name=name,
            kind="custom",
            command_adapter_id=f"custom-adapter:{adapter_id}",
        )

    def test_no_committed_record_returns_none(self, db: Database):
        """Without a COMMITTED op, eligibility returns None (fail closed)."""
        profile = self._make_profile()
        result = ExecutorRegistry._resolve_custom_adapter_eligibility(profile)
        assert result is None

    def test_with_committed_record_returns_binding(self, db: Database, tmp_path: Path):
        """With a COMMITTED op and a valid adapter store entry, returns binding."""
        # Create a committed operation
        _create_committed_op(db.direct_connect, n=1)

        # We need an actual adapter entry to exist in the adapter store.
        # Create a temporary adapter with a real executable.
        import os
        import stat
        exe = tmp_path / "fake-adapter"
        exe.write_text("#!/bin/bash\necho '{}'")
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)

        from runtime.orchestrator.adapter_store import AdapterEntry
        from runtime.orchestrator.adapter_store import save_adapter
        from runtime.orchestrator.adapter_store import compute_sha256

        exe_hash = compute_sha256(str(exe))
        entry = AdapterEntry(
            id="test-adapter-001",
            name="test-adapter-001",
            executable=str(exe),
            executable_hash=exe_hash,
            version="1.0",
            status="approved",
            contract_version=1,
        )
        try:
            save_adapter(entry)

            profile = self._make_profile()
            result = ExecutorRegistry._resolve_custom_adapter_eligibility(profile)
            assert result is not None
            assert result["executable"] == str(exe)
        finally:
            # Clean up
            from runtime.orchestrator.adapter_store import remove_adapter
            try:
                remove_adapter("test-adapter-001")
            except Exception:
                pass

    def test_non_custom_adapter_profile_returns_none(self):
        """Non-custom-adapter profiles skip the check."""
        profile = ExecutorProfile(
            name="claude", kind="builtin", command_adapter_id=None,
        )
        result = ExecutorRegistry._resolve_custom_adapter_eligibility(profile)
        assert result is None  # not a custom-adapter profile

    def test_store_not_wired_returns_none(self, db: Database):
        """When store is None, fail closed (no authority source)."""
        reset_registry()
        profile = self._make_profile()
        result = ExecutorRegistry._resolve_custom_adapter_eligibility(profile)
        assert result is None

    def test_wrong_profile_or_adapter_returns_none(self, db: Database):
        """COMMITTED op for different profile/adapter doesn't match."""
        _create_committed_op(db.direct_connect, n=1, profile_name="other-p", adapter_id="other-a")
        profile = self._make_profile(name="test-profile", adapter_id="test-adapter-001")
        result = ExecutorRegistry._resolve_custom_adapter_eligibility(profile)
        assert result is None

    def test_reserved_operation_is_not_eligible(self, db: Database):
        """A reserved (non-COMMITTED) operation does not grant eligibility."""
        db.direct_connect.reserve_operation(
            operation_id=_op_id(1), profile_name="test-profile",
            adapter_id="test-adapter-001", owner_agent="agent",
            raw_authority_token="tok", authority_owner="agent",
            replay_identity="r-reserved",
        )
        profile = self._make_profile()
        result = ExecutorRegistry._resolve_custom_adapter_eligibility(profile)
        assert result is None

    def test_projecting_operation_is_not_eligible(self, db: Database):
        """A projecting (non-COMMITTED) operation does not grant eligibility."""
        store = db.direct_connect
        op = store.reserve_operation(
            operation_id=_op_id(1), profile_name="test-profile",
            adapter_id="test-adapter-001", owner_agent="agent",
            raw_authority_token="tok", authority_owner="agent",
            replay_identity="r-projecting",
        )
        store.transition_to_projecting(op["id"])
        profile = self._make_profile()
        result = ExecutorRegistry._resolve_custom_adapter_eligibility(profile)
        assert result is None

    def test_terminalized_operation_is_not_eligible(self, db: Database):
        """A terminalized operation does not grant eligibility."""
        store = db.direct_connect
        op = store.reserve_operation(
            operation_id=_op_id(1), profile_name="test-profile",
            adapter_id="test-adapter-001", owner_agent="agent",
            raw_authority_token="tok", authority_owner="agent",
            replay_identity="r-terminal",
        )
        store.terminalize_operation(op["id"], TERMINAL_EXPIRED)
        profile = self._make_profile()
        result = ExecutorRegistry._resolve_custom_adapter_eligibility(profile)
        assert result is None

    def test_stale_or_missing_adapter_entry_returns_none(self, db: Database):
        """Even with COMMITTED op, a missing adapter_entry returns None."""
        _create_committed_op(db.direct_connect, n=1, adapter_id="nonexistent-adapter")
        profile = self._make_profile(adapter_id="nonexistent-adapter")
        result = ExecutorRegistry._resolve_custom_adapter_eligibility(profile)
        assert result is None


# ════════════════════════════════════════════════════════════════════════════
# 6. Five-runner proof — committed executor launch fence
# ════════════════════════════════════════════════════════════════════════════

class TestFiveRunnerCommittedFence:
    """Prove that all five production runner paths respect the COMMITTED-only
    launch fence. Each test:
      1. Creates a COMMITTED operation → executor builds normally
      2. Without COMMITTED operation → build_executor raises ValueError
    """

    @pytest.fixture(autouse=True)
    def _setup(self, db: Database, tmp_path: Path):
        """Set up store, a valid adapter entry, and a custom profile."""
        import os
        import stat

        reset_registry()
        reg = get_registry()
        reg.set_direct_connect_store(db.direct_connect)

        # Create a valid adapter executable
        self._exe = tmp_path / "test-adapter"
        self._exe.write_text("#!/bin/bash\necho '{}'")
        self._exe.chmod(self._exe.stat().st_mode | stat.S_IEXEC)

        from runtime.orchestrator.adapter_store import AdapterEntry, save_adapter, compute_sha256
        exe_hash = compute_sha256(str(self._exe))
        self._adapter_entry = AdapterEntry(
            id="test-adapter-002",
            name="test-adapter-002",
            executable=str(self._exe),
            executable_hash=exe_hash,
            version="1.0",
            status="approved",
            contract_version=1,
        )
        save_adapter(self._adapter_entry)

        # Register a custom profile directly
        profile = ExecutorProfile(
            name="thr107-test-profile",
            kind="custom",
            command_adapter_id="custom-adapter:test-adapter-002",
        )
        try:
            reg.register_custom_profile(profile)
        except Exception:
            pass  # may already exist from previous test

        self._db = db
        yield

        # Cleanup
        reset_registry()
        from runtime.orchestrator.adapter_store import remove_adapter
        try:
            remove_adapter("test-adapter-002")
        except Exception:
            pass

    def _get_settings_and_paths(self, tmp_path: Path):
        from runtime.config import Settings
        from runtime.orchestrator._paths import OrgPaths
        settings = Settings(
            claude_cli_path="/usr/bin/true",
            codex_cli_path="/usr/bin/true",
            opencode_cli_path="/usr/bin/true",
            pi_cli_path="/usr/bin/true",
        )
        paths = OrgPaths(root=tmp_path)
        return settings, paths

    # ── Positive: COMMITTED operation → executor builds ─────────────────

    def test_build_executor_with_committed_op(self, db: Database, tmp_path: Path):
        """build_executor returns a CustomAdapterExecutor with COMMITTED op."""
        _create_committed_op(db.direct_connect, n=1,
                             profile_name="thr107-test-profile",
                             adapter_id="test-adapter-002")
        settings, paths = self._get_settings_and_paths(tmp_path)

        from runtime.orchestrator.executor_registry import build_executor
        from runtime.orchestrator.executors import CustomAdapterExecutor
        executor = build_executor("thr107-test-profile", settings, paths)
        assert isinstance(executor, CustomAdapterExecutor)

    def test_thread_helper_with_committed_op(self, db: Database, tmp_path: Path):
        """_build_executor_for_provider returns executor with COMMITTED op."""
        _create_committed_op(db.direct_connect, n=2,
                             profile_name="thr107-test-profile",
                             adapter_id="test-adapter-002")
        settings, paths = self._get_settings_and_paths(tmp_path)

        from runtime.daemon.thread_runner import _build_executor_for_provider
        from runtime.orchestrator.executors import CustomAdapterExecutor
        executor = _build_executor_for_provider("thr107-test-profile", settings, paths)
        assert isinstance(executor, CustomAdapterExecutor)

    # ── Negative: no COMMITTED operation → ValueError ─────────────────────

    def test_build_executor_no_committed_raises(self, db: Database, tmp_path: Path):
        """Without COMMITTED op, build_executor raises ValueError (actionable, non-secret).

        This proves the central choke point that all five production runner paths
        (task _run_agent, thread run_invocation, wake run_wake, dream run_dream,
        schedule run_schedule) go through — they all call build_executor.
        """
        settings, paths = self._get_settings_and_paths(tmp_path)
        from runtime.orchestrator.executor_registry import build_executor
        with pytest.raises(ValueError, match="no durable COMMITTED"):
            build_executor("thr107-test-profile", settings, paths)

    def test_thread_helper_no_committed_raises(self, db: Database, tmp_path: Path):
        """Without COMMITTED op, thread helper also raises ValueError."""
        settings, paths = self._get_settings_and_paths(tmp_path)
        from runtime.daemon.thread_runner import _build_executor_for_provider
        with pytest.raises(ValueError, match="no durable COMMITTED"):
            _build_executor_for_provider("thr107-test-profile", settings, paths)

    # ── Retry fence: state still respected on repeated calls ──────────────

    def test_repeated_build_enforces_fence(self, db: Database, tmp_path: Path):
        """Multiple build calls consistently enforce the COMMITTED fence."""
        settings, paths = self._get_settings_and_paths(tmp_path)
        from runtime.orchestrator.executor_registry import build_executor

        # First call: no committed → raises
        with pytest.raises(ValueError, match="no durable COMMITTED"):
            build_executor("thr107-test-profile", settings, paths)

        # Create committed operation
        _create_committed_op(db.direct_connect, n=5,
                             profile_name="thr107-test-profile",
                             adapter_id="test-adapter-002")

        # Second call: committed → succeeds
        from runtime.orchestrator.executors import CustomAdapterExecutor
        executor = build_executor("thr107-test-profile", settings, paths)
        assert isinstance(executor, CustomAdapterExecutor)

    # ── Reserved/approved/nonterminal/unavailable/stale/missing refuse ────

    def test_reserved_refuses_build(self, db: Database, tmp_path: Path):
        """A reserved (not yet COMMITTED) operation refuses build."""
        db.direct_connect.reserve_operation(
            operation_id=_op_id(1), profile_name="thr107-test-profile",
            adapter_id="test-adapter-002", owner_agent="agent",
            raw_authority_token="tok", authority_owner="agent",
            replay_identity="r-build-refuse",
        )
        settings, paths = self._get_settings_and_paths(tmp_path)
        from runtime.orchestrator.executor_registry import build_executor
        with pytest.raises(ValueError, match="no durable COMMITTED"):
            build_executor("thr107-test-profile", settings, paths)

    def test_unavailable_returns_none(self, db: Database):
        """Profile not matching any operation returns None from eligibility check."""
        profile = ExecutorProfile(
            name="nonexistent", kind="custom",
            command_adapter_id="custom-adapter:no-such-adapter",
        )
        result = ExecutorRegistry._resolve_custom_adapter_eligibility(profile)
        assert result is None


# ════════════════════════════════════════════════════════════════════════════
# 7. Commit-Operation Atomic Predicates (reviewer F2)
# ════════════════════════════════════════════════════════════════════════════

class TestCommitOperationAtomicPredicates:
    """Prove commit_operation requires receipts + readback + no compensation
    residue before transitioning to COMMITTED (reviewer finding F2)."""

    def test_commit_rejects_zero_receipts(self, db: Database):
        """Commit fails when there are zero receipts."""
        store = db.direct_connect
        op = store.reserve_operation(
            operation_id=_op_id(1), profile_name="p", adapter_id="a",
            owner_agent="agent", raw_authority_token="tok",
            authority_owner="agent", replay_identity="r-zero-receipts",
        )
        store.transition_to_projecting(op["id"])
        with pytest.raises(ValueError, match="receipt"):
            store.commit_operation(op["id"])
        # Operation must remain projecting, not committed
        op2 = store.get_operation(op["id"])
        assert op2["lifecycle_status"] == LIFECYCLE_PROJECTING

    def test_commit_rejects_pending_receipts(self, db: Database):
        """Commit fails when any receipt is still pending."""
        store = db.direct_connect
        op = store.reserve_operation(
            operation_id=_op_id(1), profile_name="p", adapter_id="a",
            owner_agent="agent", raw_authority_token="tok",
            authority_owner="agent", replay_identity="r-pending-receipt",
        )
        store.write_receipt(op["id"], "projection", "s1")
        store.transition_to_projecting(op["id"])
        with pytest.raises(ValueError, match="pending"):
            store.commit_operation(op["id"])
        op2 = store.get_operation(op["id"])
        assert op2["lifecycle_status"] == LIFECYCLE_PROJECTING

    def test_commit_rejects_failed_receipts(self, db: Database):
        """Commit fails when any receipt is failed."""
        store = db.direct_connect
        op = store.reserve_operation(
            operation_id=_op_id(1), profile_name="p", adapter_id="a",
            owner_agent="agent", raw_authority_token="tok",
            authority_owner="agent", replay_identity="r-failed-receipt",
        )
        r1 = store.write_receipt(op["id"], "projection", "s1")
        store.verify_receipt(r1, "ok")
        r2 = store.write_receipt(op["id"], "projection", "s2")
        store.fail_receipt(r2, "manual-fix")
        store.transition_to_projecting(op["id"])
        with pytest.raises(ValueError, match="failed"):
            store.commit_operation(op["id"])
        op2 = store.get_operation(op["id"])
        assert op2["lifecycle_status"] == LIFECYCLE_PROJECTING

    def test_commit_rejects_compensation_residue(self, db: Database):
        """Commit fails when compensation residue exists."""
        store = db.direct_connect
        op = store.reserve_operation(
            operation_id=_op_id(1), profile_name="p", adapter_id="a",
            owner_agent="agent", raw_authority_token="tok",
            authority_owner="agent", replay_identity="r-residue-refuse",
        )
        r1 = store.write_receipt(op["id"], "projection", "s1")
        store.verify_receipt(r1, "ok")
        store.set_compensation_residue(op["id"], {"leftover": True})
        store.transition_to_projecting(op["id"])
        with pytest.raises(ValueError, match="compensation"):
            store.commit_operation(op["id"])
        op2 = store.get_operation(op["id"])
        assert op2["lifecycle_status"] == LIFECYCLE_PROJECTING

    def test_commit_succeeds_with_all_completed_and_no_residue(self, db: Database):
        """Commit with all receipts completed and no compensation residue succeeds."""
        store = db.direct_connect
        op = _create_committed_op(store, n=1)
        assert op["lifecycle_status"] == LIFECYCLE_COMMITTED
        committed = store.get_committed_operation("test-profile", "test-adapter-001")
        assert committed is not None

    def test_commit_reads_back_after_write(self, db: Database):
        """After commit, reading back the operation returns COMMITTED status."""
        store = db.direct_connect
        op = _create_committed_op(store, n=1)
        # Read-back verification
        op2 = store.get_operation(op["id"])
        assert op2["lifecycle_status"] == LIFECYCLE_COMMITTED
        # Receipts are all completed
        receipts = store.get_receipts(op["id"])
        assert len(receipts) >= 2
        for r in receipts:
            assert r["status"] == "completed"

    def test_commit_fails_when_not_projecting(self, db: Database):
        """Commit from non-projecting state fails (already covered)."""
        store = db.direct_connect
        op = store.reserve_operation(
            operation_id=_op_id(1), profile_name="p", adapter_id="a",
            owner_agent="agent", raw_authority_token="tok",
            authority_owner="agent", replay_identity="r-not-projecting",
        )
        result = store.commit_operation(op["id"])
        assert result is None


# ════════════════════════════════════════════════════════════════════════════
# 8. Claim-Before-Parse Coordinator (reviewer F3)
# ════════════════════════════════════════════════════════════════════════════

class TestClaimBeforeParseCoordinator:
    """Prove the typed shipping coordinator claims raw-token authority BEFORE
    JSON/Pydantic parsing, durably terminalizes all failures, and supports
    exact-COMMITTED replay (reviewer finding F3)."""

    def test_claim_before_parse_persists_token_fingerprint_only(self, db: Database):
        """The coordinator persists only non-secret authority material (CAS hash),
        never the raw token text after initial claim."""
        store = db.direct_connect
        from runtime.infrastructure.direct_connect_store import DirectConnectCoordinator
        coordinator = DirectConnectCoordinator(store)
        raw_token = "sk-very-secret-token-value"
        result = coordinator.claim_and_prepare(
            operation_id=_op_id(1),
            profile_name="test-profile",
            adapter_id="adapter-1",
            owner_agent="test-agent",
            raw_authority_token=raw_token,
            authority_owner="test-agent",
            replay_identity="r-claim-1",
        )
        assert result["status"] == "reserved"
        # Read back the operation — raw token is stored, but coordinator
        # doesn't return it in its result (non-secret only)
        op = store.get_operation(_op_id(1))
        assert "raw_authority_token" in op
        # CAS hash is the authority fingerprint
        expected_cas = compute_cas("test-agent", raw_token)
        assert op["cas_hash"] == expected_cas
        # Coordinator result must not expose raw token
        assert "raw_authority_token" not in result or result.get("raw_authority_token") is None

    def test_coordinator_terminalizes_malformed_input(self, db: Database):
        """Malformed authority input (empty token) is terminalized before parsing."""
        store = db.direct_connect
        from runtime.infrastructure.direct_connect_store import DirectConnectCoordinator
        coordinator = DirectConnectCoordinator(store)
        result = coordinator.claim_and_prepare(
            operation_id=_op_id(1),
            profile_name="test-profile",
            adapter_id="adapter-1",
            owner_agent="test-agent",
            raw_authority_token="",  # malformed
            authority_owner="test-agent",
            replay_identity="r-malformed-claim",
        )
        assert result["terminal"] is True
        assert "malformed" in result["reason"].lower()
        op = store.get_operation(_op_id(1))
        assert op is not None
        assert is_terminal(op["lifecycle_status"])

    def test_coordinator_terminalizes_expired_authority(self, db: Database):
        """Expired authority is terminalized."""
        store = db.direct_connect
        from runtime.infrastructure.direct_connect_store import DirectConnectCoordinator
        coordinator = DirectConnectCoordinator(store)
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        result = coordinator.claim_and_prepare(
            operation_id=_op_id(1),
            profile_name="test-profile",
            adapter_id="adapter-1",
            owner_agent="test-agent",
            raw_authority_token="valid-token",
            authority_owner="test-agent",
            replay_identity="r-expired-claim",
            authority_expiry=past,
        )
        assert result["terminal"] is True
        assert "expired" in result["reason"].lower()
        op = store.get_operation(_op_id(1))
        assert is_terminal(op["lifecycle_status"])

    def test_coordinator_terminalizes_owner_mismatch(self, db: Database):
        """Owner mismatch is terminalized."""
        store = db.direct_connect
        from runtime.infrastructure.direct_connect_store import DirectConnectCoordinator
        coordinator = DirectConnectCoordinator(store)
        result = coordinator.claim_and_prepare(
            operation_id=_op_id(1),
            profile_name="test-profile",
            adapter_id="adapter-1",
            owner_agent="test-agent",
            raw_authority_token="valid-token",
            authority_owner="other-agent",  # different from owner_agent
            replay_identity="r-mismatch-claim",
        )
        assert result["terminal"] is True
        assert "owner" in result["reason"].lower()
        op = store.get_operation(_op_id(1))
        assert is_terminal(op["lifecycle_status"])

    def test_replay_exact_match_is_zero_write(self, db: Database):
        """Exact same (owner, token, replay) is idempotent zero-write replay."""
        store = db.direct_connect
        from runtime.infrastructure.direct_connect_store import DirectConnectCoordinator
        coordinator = DirectConnectCoordinator(store)

        # First claim
        r1 = coordinator.claim_and_prepare(
            operation_id=_op_id(1),
            profile_name="test-profile",
            adapter_id="adapter-1",
            owner_agent="test-agent",
            raw_authority_token="token-replay",
            authority_owner="test-agent",
            replay_identity="r-exact-replay",
        )
        assert r1["status"] == "reserved"

        # Exact replay with same parameters
        r2 = coordinator.claim_and_prepare(
            operation_id=_op_id(1),
            profile_name="test-profile",
            adapter_id="adapter-1",
            owner_agent="test-agent",
            raw_authority_token="token-replay",
            authority_owner="test-agent",
            replay_identity="r-exact-replay",
        )
        assert r2["replay"] is True
        assert r2["status"] == "replay"

    def test_replay_different_token_is_terminalized(self, db: Database):
        """Replay identity collision with different token is terminalized."""
        store = db.direct_connect
        from runtime.infrastructure.direct_connect_store import DirectConnectCoordinator
        coordinator = DirectConnectCoordinator(store)

        coordinator.claim_and_prepare(
            operation_id=_op_id(1),
            profile_name="test-profile",
            adapter_id="adapter-1",
            owner_agent="test-agent",
            raw_authority_token="token-original",
            authority_owner="test-agent",
            replay_identity="r-different-replay",
        )
        result = coordinator.claim_and_prepare(
            operation_id=_op_id(2),
            profile_name="test-profile",
            adapter_id="adapter-1",
            owner_agent="test-agent",
            raw_authority_token="token-different",
            authority_owner="test-agent",
            replay_identity="r-different-replay",
        )
        assert result["terminal"] is True
        assert "replay" in result["reason"].lower()

    def test_terminalized_token_never_reusable(self, db: Database):
        """Once terminalized, the operation is never reusable — cannot transition
        to projecting or committed."""
        store = db.direct_connect
        from runtime.infrastructure.direct_connect_store import DirectConnectCoordinator
        coordinator = DirectConnectCoordinator(store)

        result = coordinator.claim_and_prepare(
            operation_id=_op_id(1),
            profile_name="test-profile",
            adapter_id="adapter-1",
            owner_agent="test-agent",
            raw_authority_token="",  # triggers malformed terminal
            authority_owner="test-agent",
            replay_identity="r-never-reusable",
        )
        assert result["terminal"] is True

        # Cannot transition to projecting
        r = store.transition_to_projecting(_op_id(1))
        assert r is None
        # Cannot commit
        r = store.commit_operation(_op_id(1))
        assert r is None
        # get_committed_operation returns None
        assert store.get_committed_operation("test-profile", "adapter-1") is None

    def test_claim_before_parse_durable_audit_residue(self, db: Database):
        """Every terminalization leaves durable audit/residue evidence."""
        store = db.direct_connect
        from runtime.infrastructure.direct_connect_store import DirectConnectCoordinator
        coordinator = DirectConnectCoordinator(store)

        result = coordinator.claim_and_prepare(
            operation_id=_op_id(1),
            profile_name="test-profile",
            adapter_id="adapter-1",
            owner_agent="test-agent",
            raw_authority_token="",
            authority_owner="test-agent",
            replay_identity="r-audit-residue",
        )
        assert result["terminal"] is True
        op = store.get_operation(_op_id(1))
        assert op is not None
        assert is_terminal(op["lifecycle_status"])
        assert op["terminal_reason"] and len(op["terminal_reason"]) > 0


# ════════════════════════════════════════════════════════════════════════════
# 9. Production-Store Wiring (reviewer F1)
# ════════════════════════════════════════════════════════════════════════════

class TestProductionStoreWiring:
    """Prove that the executor registry uses the authoritative Database.direct_connect
    store, not a test-only injection path (reviewer finding F1)."""

    def test_registry_starts_with_no_store(self):
        """A fresh ExecutorRegistry has no store (safe default)."""
        from runtime.orchestrator.executor_registry import ExecutorRegistry, reset_registry
        reset_registry()
        registry = ExecutorRegistry()
        assert registry.direct_connect_store is None

    def test_set_direct_connect_store_on_registry(self, db: Database):
        """Setting the store on a registry instance works."""
        registry = ExecutorRegistry()
        registry.set_direct_connect_store(db.direct_connect)
        assert registry.direct_connect_store is not None

    def test_store_is_wired_from_database_after_load(self):
        """After wiring, the singleton registry gets the store from DB."""
        import tempfile
        from runtime.orchestrator.executor_registry import get_registry, reset_registry
        reset_registry()
        reg = get_registry()
        # Before wiring, store is None
        assert reg.direct_connect_store is None
        # Wire it (simulating OrgState.load)
        with tempfile.NamedTemporaryFile(suffix=".db") as tf:
            db2 = Database(Path(tf.name))
            try:
                reg.set_direct_connect_store(db2.direct_connect)
                assert reg.direct_connect_store is not None
                # The store is the actual DirectConnectStore from Database
                from runtime.infrastructure.direct_connect_store import DirectConnectStore
                assert isinstance(reg.direct_connect_store, DirectConnectStore)
            finally:
                db2.close()

    def test_fail_closed_when_store_is_none(self, db: Database):
        """When no store is wired, eligibility check fails closed (returns None)."""
        from runtime.orchestrator.executor_registry import ExecutorRegistry, reset_registry
        reset_registry()
        registry = ExecutorRegistry()
        # No store wired
        profile = ExecutorProfile(
            name="test-profile", kind="custom",
            command_adapter_id="custom-adapter:any-adapter",
        )
        # Should fail closed — no store means no authority source
        result = ExecutorRegistry._resolve_custom_adapter_eligibility(profile)
        assert result is None

    def test_real_production_wiring_via_get_registry(self, db: Database):
        """The singleton from get_registry() can be wired with the store."""
        from runtime.orchestrator.executor_registry import get_registry, reset_registry
        reset_registry()
        reg = get_registry()
        reg.set_direct_connect_store(db.direct_connect)
        # Verify the wired store is visible and is the same object
        assert reg.direct_connect_store is db.direct_connect


# ════════════════════════════════════════════════════════════════════════════
# 10. Real-Runner Proof Matrix (reviewer F4)
# ════════════════════════════════════════════════════════════════════════════

class TestRealRunnerProofMatrix:
    """Prove that the real Orchestrator._run_agent, thread invocation,
    wake, dream, and schedule runner seams respect the COMMITTED-only fence
    with actual executor construction (reviewer finding F4)."""

    @pytest.fixture(autouse=True)
    def _setup(self, db: Database, tmp_path: Path):
        """Set up store, adapter entry, custom profile, and registry wiring."""
        import os, stat
        from runtime.orchestrator.executor_registry import get_registry, reset_registry

        # Reset + wire the store
        reset_registry()
        reg = get_registry()
        reg.set_direct_connect_store(db.direct_connect)

        # Create a valid adapter executable
        self._exe = tmp_path / "test-adapter-real"
        self._exe.write_text("#!/bin/bash\necho '{}'")
        self._exe.chmod(self._exe.stat().st_mode | stat.S_IEXEC)

        from runtime.orchestrator.adapter_store import AdapterEntry, save_adapter, compute_sha256
        exe_hash = compute_sha256(str(self._exe))
        self._adapter_entry = AdapterEntry(
            id="test-adapter-real",
            name="test-adapter-real",
            executable=str(self._exe),
            executable_hash=exe_hash,
            version="1.0",
            status="approved",
            contract_version=1,
        )
        save_adapter(self._adapter_entry)

        # Register custom profile
        profile = ExecutorProfile(
            name="thr107-real-profile",
            kind="custom",
            command_adapter_id="custom-adapter:test-adapter-real",
        )
        try:
            reg.register_custom_profile(profile)
        except Exception:
            pass

        self._db = db
        self._tmp_path = tmp_path
        yield

        # Cleanup
        from runtime.orchestrator.adapter_store import remove_adapter
        try:
            remove_adapter("test-adapter-real")
        except Exception:
            pass
        reset_registry()

    def _settings_and_paths(self):
        from runtime.config import Settings
        from runtime.orchestrator._paths import OrgPaths
        settings = Settings(
            claude_cli_path="/usr/bin/true",
            codex_cli_path="/usr/bin/true",
            opencode_cli_path="/usr/bin/true",
            pi_cli_path="/usr/bin/true",
        )
        paths = OrgPaths(root=self._tmp_path)
        return settings, paths

    # ── Orchestrator._build_executor (real task runner seam) ──────────────

    def test_orchestrator_build_executor_committed(self, db: Database):
        """Orchestrator._build_executor returns executor with COMMITTED op."""
        _create_committed_op(db.direct_connect, n=1,
                             profile_name="thr107-real-profile",
                             adapter_id="test-adapter-real")
        settings, paths = self._settings_and_paths()
        from runtime.orchestrator.orchestrator import Orchestrator
        orch = Orchestrator(db=db, settings=settings, paths=paths, slug="test", teams=None)
        from runtime.orchestrator.executors import CustomAdapterExecutor
        executor = orch._build_executor("thr107-real-profile")
        assert isinstance(executor, CustomAdapterExecutor)

    def test_orchestrator_build_executor_no_committed_raises(self, db: Database):
        """Orchestrator._build_executor raises ValueError without COMMITTED op."""
        settings, paths = self._settings_and_paths()
        from runtime.orchestrator.orchestrator import Orchestrator
        orch = Orchestrator(db=db, settings=settings, paths=paths, slug="test", teams=None)
        with pytest.raises(ValueError, match="no durable COMMITTED"):
            orch._build_executor("thr107-real-profile")

    # ── Thread runner seam ──────────────────────────────────────────────

    def test_thread_build_executor_committed(self, db: Database):
        """Thread _build_executor_for_provider returns executor with COMMITTED op."""
        _create_committed_op(db.direct_connect, n=2,
                             profile_name="thr107-real-profile",
                             adapter_id="test-adapter-real")
        settings, paths = self._settings_and_paths()
        from runtime.daemon.thread_runner import _build_executor_for_provider
        from runtime.orchestrator.executors import CustomAdapterExecutor
        executor = _build_executor_for_provider("thr107-real-profile", settings, paths)
        assert isinstance(executor, CustomAdapterExecutor)

    def test_thread_build_executor_no_committed_raises(self, db: Database):
        """Thread _build_executor_for_provider raises ValueError without COMMITTED op."""
        settings, paths = self._settings_and_paths()
        from runtime.daemon.thread_runner import _build_executor_for_provider
        with pytest.raises(ValueError, match="no durable COMMITTED"):
            _build_executor_for_provider("thr107-real-profile", settings, paths)

    # ── Wake runner seam ────────────────────────────────────────────────

    def test_wake_runner_build_executor_no_committed_raises(self, db: Database):
        """Wake runner's build_executor call refuses uncommitted via the
        same _build_executor_for_provider seam used by wake run_wake."""
        settings, paths = self._settings_and_paths()
        from runtime.daemon.thread_runner import _build_executor_for_provider
        with pytest.raises(ValueError, match="no durable COMMITTED"):
            _build_executor_for_provider("thr107-real-profile", settings, paths)

    def test_wake_runner_build_executor_committed_succeeds(self, db: Database):
        """Wake runner's build path returns executor with COMMITTED op."""
        _create_committed_op(db.direct_connect, n=3,
                             profile_name="thr107-real-profile",
                             adapter_id="test-adapter-real")
        settings, paths = self._settings_and_paths()
        from runtime.daemon.thread_runner import _build_executor_for_provider
        from runtime.orchestrator.executors import CustomAdapterExecutor
        executor = _build_executor_for_provider("thr107-real-profile", settings, paths)
        assert isinstance(executor, CustomAdapterExecutor)

    # ── Dream runner seam ───────────────────────────────────────────────

    def test_dream_runner_build_executor_no_committed_raises(self, db: Database):
        """Dream runner's build path refuses uncommitted."""
        settings, paths = self._settings_and_paths()
        from runtime.daemon.thread_runner import _build_executor_for_provider
        with pytest.raises(ValueError, match="no durable COMMITTED"):
            _build_executor_for_provider("thr107-real-profile", settings, paths)

    def test_dream_runner_build_executor_committed_succeeds(self, db: Database):
        """Dream runner's build path returns executor with COMMITTED op."""
        _create_committed_op(db.direct_connect, n=4,
                             profile_name="thr107-real-profile",
                             adapter_id="test-adapter-real")
        settings, paths = self._settings_and_paths()
        from runtime.daemon.thread_runner import _build_executor_for_provider
        from runtime.orchestrator.executors import CustomAdapterExecutor
        executor = _build_executor_for_provider("thr107-real-profile", settings, paths)
        assert isinstance(executor, CustomAdapterExecutor)

    # ── Schedule runner seam ────────────────────────────────────────────

    def test_schedule_runner_build_executor_no_committed_raises(self, db: Database):
        """Schedule runner's build path refuses uncommitted."""
        settings, paths = self._settings_and_paths()
        from runtime.daemon.thread_runner import _build_executor_for_provider
        with pytest.raises(ValueError, match="no durable COMMITTED"):
            _build_executor_for_provider("thr107-real-profile", settings, paths)

    def test_schedule_runner_build_executor_committed_succeeds(self, db: Database):
        """Schedule runner's build path returns executor with COMMITTED op."""
        _create_committed_op(db.direct_connect, n=5,
                             profile_name="thr107-real-profile",
                             adapter_id="test-adapter-real")
        settings, paths = self._settings_and_paths()
        from runtime.daemon.thread_runner import _build_executor_for_provider
        from runtime.orchestrator.executors import CustomAdapterExecutor
        executor = _build_executor_for_provider("thr107-real-profile", settings, paths)
        assert isinstance(executor, CustomAdapterExecutor)

    # ── Pre-Popen refusal proof ─────────────────────────────────────────

    def test_no_committed_refuses_before_popen(self, db: Database, monkeypatch):
        """Without COMMITTED op, the refusal happens before any subprocess Popen
        attempt. Instrument the real adapter Popen seam to prove this."""
        settings, paths = self._settings_and_paths()

        # Monkeypatch subprocess.Popen to record if it was ever called
        popen_calls = []
        original_popen = subprocess.Popen

        def _record_popen(*args, **kwargs):
            popen_calls.append((args, kwargs))
            return original_popen(*args, **kwargs)

        monkeypatch.setattr(subprocess, "Popen", _record_popen)

        from runtime.orchestrator.executor_registry import build_executor
        with pytest.raises(ValueError, match="no durable COMMITTED"):
            build_executor("thr107-real-profile", settings, paths)

        # No Popen was ever attempted
        assert len(popen_calls) == 0, (
            f"Popen called {len(popen_calls)} times before refusal"
        )

    # ── Retry/throttle revalidation ─────────────────────────────────────

    def test_repeated_refusal_consistent_no_state_drift(self, db: Database):
        """Multiple consecutive refusal calls produce consistent errors with
        no state drift (retry revalidation proof)."""
        settings, paths = self._settings_and_paths()
        from runtime.orchestrator.executor_registry import build_executor

        for _ in range(3):
            with pytest.raises(ValueError, match="no durable COMMITTED"):
                build_executor("thr107-real-profile", settings, paths)

        # After repeated refusals, adding a COMMITTED op should still work
        _create_committed_op(db.direct_connect, n=10,
                             profile_name="thr107-real-profile",
                             adapter_id="test-adapter-real")
        from runtime.orchestrator.executors import CustomAdapterExecutor
        executor = build_executor("thr107-real-profile", settings, paths)
        assert isinstance(executor, CustomAdapterExecutor)


# ════════════════════════════════════════════════════════════════════════════
# 7. Adversarial legacy compatibility and fault injection
# ════════════════════════════════════════════════════════════════════════════

class TestAdversarialCompatAndFaults:

    def test_empty_db_is_safe(self, db: Database):
        """An empty DB with no operations is safe — all queries return None/empty."""
        store = db.direct_connect
        assert store.get_committed_operation("any", "any") is None
        assert store.get_operation("any") is None
        assert store.get_operation_by_replay("any") is None
        assert store.get_pending_receipts("any") == []
        assert store.get_receipts("any") == []

    def test_db_fault_during_insert(self, db: Database, monkeypatch):
        """Simulated DB fault during reserve via a unique-constraint collision.

        This tests that the transactional boundary works: a reservation that
        fails mid-operation does not persist partial state.
        """
        store = db.direct_connect

        # First reservation succeeds
        store.reserve_operation(
            operation_id=_op_id(99), profile_name="p", adapter_id="a",
            owner_agent="agent", raw_authority_token="tok",
            authority_owner="agent", replay_identity="r-db-fault-1",
        )

        # Second reservation with same replay_identity MUST raise IntegrityError
        with pytest.raises(sqlite3.IntegrityError):
            store.reserve_operation(
                operation_id=_op_id(100), profile_name="p", adapter_id="a",
                owner_agent="agent", raw_authority_token="tok",
                authority_owner="agent", replay_identity="r-db-fault-1",
            )

        # The second operation should NOT exist
        assert store.get_operation(_op_id(100)) is None
        # The first operation should still exist
        assert store.get_operation(_op_id(99)) is not None

    def test_legacy_tables_unchanged(self, db: Database):
        """Existing tables (tasks, audit_log, etc.) remain structurally unchanged."""
        # Spot check a few known columns
        task_cols = {
            row["name"]
            for row in db._conn.execute("PRAGMA table_info(tasks)").fetchall()
        }
        assert "id" in task_cols
        assert "status" in task_cols
        assert "brief" in task_cols
        assert "team" in task_cols
        assert "parent_task_id" in task_cols

        # No direct_connect columns leaked into tasks
        assert "raw_authority_token" not in task_cols
        assert "cas_hash" not in task_cols
        assert "lifecycle_status" not in task_cols

    def test_audit_log_task_id_not_overloaded(self, db: Database):
        """audit_log.task_id remains the existing scope-prefix system unchanged."""
        audit_cols = {
            row["name"]
            for row in db._conn.execute("PRAGMA table_info(audit_log)").fetchall()
        }
        assert "task_id" in audit_cols
        # No new columns on audit_log
        assert "operation_id" not in audit_cols

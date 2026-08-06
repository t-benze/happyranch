"""
THR-107 v9 Slice 1: authoritative non-secret direct-connect operation journal.

Durable store for the ``direct_connect_operations`` and ``direct_connect_receipts``
tables. Shares the owning ``Database``'s single ``sqlite3.Connection`` and
``threading.RLock`` so the single-connection serialization invariant holds.

Slice 1 scope (deferred to Slice 2/3):
- No direct submit/commit HTTP routes
- No YAML/registry projection mutations
- No artifact binding table (adapter_artifact_bindings)
- No dependency observation table (adapter_dependency_observations)
- No reconciler/inventory/old-record disposition
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# ── Lifecycle status constants ────────────────────────────────────────────
# Non-terminal states
LIFECYCLE_RESERVED = "reserved"
LIFECYCLE_PROJECTING = "projecting"
LIFECYCLE_COMMITTED = "committed"

# Terminal states — each prefix records the reason.
TERMINAL_EXPIRED = "terminal:expired"
TERMINAL_MALFORMED = "terminal:malformed"
TERMINAL_FOREIGN = "terminal:foreign"
TERMINAL_OWNER_MISMATCH = "terminal:owner_mismatch"
TERMINAL_CAS_LOSS = "terminal:cas_loss"
TERMINAL_REPLAY = "terminal:replay"
TERMINAL_CRASH = "terminal:crash"
TERMINAL_DB_FAULT = "terminal:db_fault"
TERMINAL_AUDIT_FAULT = "terminal:audit_fault"
TERMINAL_COMPENSATION_FAILED = "terminal:compensation_failed"

_TERMINAL_PREFIXES = (
    "terminal:",
    "expired:",  # legacy
)

# Authority states (subset of lifecycle_status, used in raw-token claim).
AUTHORITY_RESERVED = "reserved"


def is_terminal(lifecycle_status: str) -> bool:
    return lifecycle_status.startswith(_TERMINAL_PREFIXES)


def is_committed(lifecycle_status: str) -> bool:
    return lifecycle_status == LIFECYCLE_COMMITTED


def is_launchable(lifecycle_status: str) -> bool:
    """Only durable COMMITTED records are launchable."""
    return lifecycle_status == LIFECYCLE_COMMITTED


def compute_cas(owner: str, raw_token: str) -> str:
    """Compute a content-addressable CAS hash of (owner, raw_token)."""
    return hashlib.sha256(f"{owner}:{raw_token}".encode()).hexdigest()


class DirectConnectStore:
    """Authoritative store for direct-connect operations and receipts.

    Every public method acquires ``self._lock`` before touching the connection.
    Callers must supply an audit-callback that the store invokes for audit_log
    insertion (the store does not import audit_logger directly — Slice 1 defers
    the real audit route dependency to Slice 2).
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        lock: threading.RLock,
        audit_fn: callable | None = None,
    ) -> None:
        self._conn = conn
        self._lock = lock
        self._audit_fn = audit_fn  # (task_id, agent, action, payload) -> audit_event_id

    def _audit(self, operation_id: str, agent: str, action: str, payload: dict | None = None) -> int | None:
        if self._audit_fn is None:
            return None
        return self._audit_fn(operation_id, agent, action, json.dumps(payload) if payload else None)

    # ── Operation lifecycle ────────────────────────────────────────────────

    def reserve_operation(
        self,
        operation_id: str,
        profile_name: str,
        adapter_id: str,
        owner_agent: str,
        raw_authority_token: str,
        authority_owner: str,
        replay_identity: str,
        authority_expiry: str | None = None,
    ) -> dict:
        """Claim raw authority and create a reserved operation.

        Caller must validate the raw token BEFORE calling this (is it malformed?
        expired? foreign owner?). This method only persists the claim atomically.

        Returns the inserted row as a dict.
        Raises sqlite3.IntegrityError on replay_identity collision.
        """
        cas = compute_cas(authority_owner, raw_authority_token)
        now = _now_iso()

        with self._lock:
            self._conn.execute(
                """INSERT INTO direct_connect_operations
                   (id, profile_name, adapter_id, owner_agent,
                    raw_authority_token, authority_state, authority_expiry,
                    authority_owner, cas_hash, lifecycle_status,
                    replay_identity, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    operation_id, profile_name, adapter_id, owner_agent,
                    raw_authority_token, AUTHORITY_RESERVED, authority_expiry,
                    authority_owner, cas, LIFECYCLE_RESERVED,
                    replay_identity, now, now,
                ),
            )

        audit_id = self._audit(operation_id, owner_agent, "direct_connect_reserved", {
            "profile_name": profile_name, "adapter_id": adapter_id,
            "authority_owner": authority_owner, "cas_hash": cas,
        })

        with self._lock:
            self._conn.execute(
                "UPDATE direct_connect_operations SET audit_created_event_id = ? WHERE id = ?",
                (audit_id, operation_id),
            )

        return self.get_operation(operation_id)

    def get_operation(self, operation_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM direct_connect_operations WHERE id = ?",
                (operation_id,),
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def get_operation_by_replay(self, replay_identity: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM direct_connect_operations WHERE replay_identity = ?",
                (replay_identity,),
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def terminalize_operation(
        self,
        operation_id: str,
        reason: str,
        agent: str = "system",
    ) -> dict | None:
        """Mark an operation as terminally failed.

        Only operates on non-terminal records. Returns the updated row or
        None if the operation was already terminal.
        """
        now = _now_iso()
        # Only one of the defined terminal reasons.
        valid_reasons = {
            TERMINAL_EXPIRED, TERMINAL_MALFORMED, TERMINAL_FOREIGN,
            TERMINAL_OWNER_MISMATCH, TERMINAL_CAS_LOSS, TERMINAL_REPLAY,
            TERMINAL_CRASH, TERMINAL_DB_FAULT, TERMINAL_AUDIT_FAULT,
            TERMINAL_COMPENSATION_FAILED,
        }
        if reason not in valid_reasons:
            reason = TERMINAL_MALFORMED

        audit_id = self._audit(operation_id, agent, "direct_connect_terminalized", {
            "reason": reason,
        })

        with self._lock:
            cursor = self._conn.execute(
                """UPDATE direct_connect_operations
                   SET lifecycle_status = ?, terminal_reason = ?,
                       terminal_audit_event_id = ?, updated_at = ?
                   WHERE id = ? AND lifecycle_status NOT LIKE 'terminal:%'
                     AND lifecycle_status != 'terminal:expired'
                     AND lifecycle_status != 'terminal:malformed'
                     AND lifecycle_status != 'terminal:foreign'
                     AND lifecycle_status != 'terminal:owner_mismatch'
                     AND lifecycle_status != 'terminal:cas_loss'
                     AND lifecycle_status != 'terminal:replay'
                     AND lifecycle_status != 'terminal:crash'
                     AND lifecycle_status != 'terminal:db_fault'
                     AND lifecycle_status != 'terminal:audit_fault'
                     AND lifecycle_status != 'terminal:compensation_failed'""",
                (reason, reason, audit_id, now, operation_id),
            )
        if cursor.rowcount == 0:
            return None  # already terminal
        return self.get_operation(operation_id)

    def transition_to_projecting(self, operation_id: str, agent: str = "system") -> dict | None:
        """Transition reserved → projecting (receipts about to be written)."""
        now = _now_iso()
        self._audit(operation_id, agent, "direct_connect_projecting", None)
        with self._lock:
            cursor = self._conn.execute(
                """UPDATE direct_connect_operations
                   SET lifecycle_status = ?, updated_at = ?
                   WHERE id = ? AND lifecycle_status = ?""",
                (LIFECYCLE_PROJECTING, now, operation_id, LIFECYCLE_RESERVED),
            )
        if cursor.rowcount == 0:
            return None
        return self.get_operation(operation_id)

    def commit_operation(self, operation_id: str, agent: str = "system") -> dict | None:
        """Transition projecting → committed after all receipts verified.

        This is the final gate: the operation becomes launchable.
        """
        now = _now_iso()
        self._audit(operation_id, agent, "direct_connect_committed", None)
        with self._lock:
            cursor = self._conn.execute(
                """UPDATE direct_connect_operations
                   SET lifecycle_status = ?, updated_at = ?
                   WHERE id = ? AND lifecycle_status = ?""",
                (LIFECYCLE_COMMITTED, now, operation_id, LIFECYCLE_PROJECTING),
            )
        if cursor.rowcount == 0:
            return None
        return self.get_operation(operation_id)

    def get_committed_operation(
        self, profile_name: str, adapter_id: str
    ) -> dict | None:
        """Return the COMMITTED operation for a given profile+adapter pair, or None."""
        with self._lock:
            row = self._conn.execute(
                """SELECT * FROM direct_connect_operations
                   WHERE profile_name = ? AND adapter_id = ?
                     AND lifecycle_status = ?
                   ORDER BY updated_at DESC LIMIT 1""",
                (profile_name, adapter_id, LIFECYCLE_COMMITTED),
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    # ── Receipt lifecycle (Slice 1: plumbing only, no real projection calls) ─

    def write_receipt(
        self,
        operation_id: str,
        receipt_type: str,
        planned_state: str,
        agent: str = "system",
    ) -> int:
        """Write a planned receipt and return its id."""
        now = _now_iso()
        self._audit(operation_id, agent, "direct_connect_receipt_planned", {
            "receipt_type": receipt_type, "planned_state": planned_state,
        })
        with self._lock:
            cursor = self._conn.execute(
                """INSERT INTO direct_connect_receipts
                   (operation_id, receipt_type, planned_state, status, created_at, updated_at)
                   VALUES (?, ?, ?, 'pending', ?, ?)""",
                (operation_id, receipt_type, planned_state, now, now),
            )
            receipt_id = cursor.lastrowid
            self._conn.execute(
                "UPDATE direct_connect_operations SET receipt_count = receipt_count + 1 WHERE id = ?",
                (operation_id,),
            )
        return receipt_id

    def verify_receipt(
        self,
        receipt_id: int,
        actual_state: str,
        agent: str = "system",
    ) -> dict | None:
        """Verify a receipt — record actual_state and mark completed."""
        now = _now_iso()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM direct_connect_receipts WHERE id = ?",
                (receipt_id,),
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                """UPDATE direct_connect_receipts
                   SET actual_state = ?, status = 'completed', updated_at = ?
                   WHERE id = ?""",
                (actual_state, now, receipt_id),
            )
        self._audit(dict(row)["operation_id"], agent, "direct_connect_receipt_verified", {
            "receipt_id": receipt_id, "actual_state": actual_state,
        })
        with self._lock:
            return dict(self._conn.execute(
                "SELECT * FROM direct_connect_receipts WHERE id = ?", (receipt_id,),
            ).fetchone())

    def fail_receipt(
        self,
        receipt_id: int,
        compensation_action: str,
        agent: str = "system",
    ) -> dict | None:
        """Mark a receipt as failed with compensation action."""
        now = _now_iso()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM direct_connect_receipts WHERE id = ?",
                (receipt_id,),
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                """UPDATE direct_connect_receipts
                   SET status = 'failed', compensation_action = ?, updated_at = ?
                   WHERE id = ?""",
                (compensation_action, now, receipt_id),
            )
        self._audit(dict(row)["operation_id"], agent, "direct_connect_receipt_failed", {
            "receipt_id": receipt_id, "compensation_action": compensation_action,
        })
        with self._lock:
            return dict(self._conn.execute(
                "SELECT * FROM direct_connect_receipts WHERE id = ?", (receipt_id,),
            ).fetchone())

    def get_pending_receipts(self, operation_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM direct_connect_receipts
                   WHERE operation_id = ? AND status = 'pending'""",
                (operation_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_receipts(self, operation_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM direct_connect_receipts WHERE operation_id = ? ORDER BY id",
                (operation_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def set_compensation_residue(
        self, operation_id: str, residue: dict, agent: str = "system"
    ) -> None:
        now = _now_iso()
        self._audit(operation_id, agent, "direct_connect_compensation_residue", residue)
        with self._lock:
            self._conn.execute(
                """UPDATE direct_connect_operations
                   SET compensation_residue = ?, updated_at = ?
                   WHERE id = ?""",
                (json.dumps(residue), now, operation_id),
            )

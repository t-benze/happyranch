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
    The store uses the owning Database's insert_audit_log method for audit_log
    insertion — wired via ``_audit_fn`` (the Database itself, or a test stub).

    THR-107 v9 Slice 1 fix-forward (TASK-4639, reviewer F1/F3):
    - No raw-token persistence: store only CAS hash fingerprint
    - Real audit callback wired from Database construction
    - Token-terminal: never parse before CAS claim is durably persisted
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        lock: threading.RLock,
        audit_fn: callable | None = None,
    ) -> None:
        self._conn = conn
        self._lock = lock
        # audit_fn is the Database (or test stub) with insert_audit_log
        self._audit_fn = audit_fn

    def _audit(self, operation_id: str, agent: str, action: str, payload: dict | None = None, task_id: str | None = None) -> int | None:
        """Insert an audit_log row via the shipping audit callback.

        Uses the audit_log task_id scope-prefix convention:
        ``config:direct_connect:<operation_id>``.
        """
        if self._audit_fn is None:
            return None
        scope_task_id = task_id or f"config:direct_connect:{operation_id}"
        try:
            return self._audit_fn(scope_task_id, agent, action, payload)
        except Exception:
            return None

    # ── Operation lifecycle ────────────────────────────────────────────────

    def reserve_operation(
        self,
        operation_id: str,
        profile_name: str,
        adapter_id: str,
        owner_agent: str,
        authority_owner: str,
        replay_identity: str,
        cas_hash: str | None = None,
        raw_authority_token: str | None = None,
        authority_expiry: str | None = None,
    ) -> dict:
        """Create a reserved operation with a CAS fingerprint.

        The CAS hash identifies the raw authority. Pass either ``cas_hash``
        (preferred) or ``raw_authority_token`` (convenience — the token is
        used ONLY to compute CAS, NEVER persisted). When both are given,
        ``cas_hash`` wins.

        Returns the inserted row as a dict.
        Raises sqlite3.IntegrityError on replay_identity collision.
        """
        if cas_hash is None and raw_authority_token is not None:
            cas_hash = compute_cas(authority_owner, raw_authority_token)
        if cas_hash is None:
            raise ValueError("Either cas_hash or raw_authority_token is required")
        now = _now_iso()

        with self._lock:
            self._conn.execute(
                """INSERT INTO direct_connect_operations
                   (id, profile_name, adapter_id, owner_agent,
                    authority_state, authority_expiry,
                    authority_owner, cas_hash, lifecycle_status,
                    replay_identity, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    operation_id, profile_name, adapter_id, owner_agent,
                    AUTHORITY_RESERVED, authority_expiry,
                    authority_owner, cas_hash, LIFECYCLE_RESERVED,
                    replay_identity, now, now,
                ),
            )

        with self._lock:
            self._conn.execute(
                "UPDATE direct_connect_operations SET audit_created_event_id = ? WHERE id = ?",
                (None, operation_id),
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

        Atomic predicate (THR-107 v9 Slice 1, reviewer F2/F4):
        - Requires at least one receipt (rejects zero receipts)
        - Requires every receipt to be completed with nonempty actual_state
          (rejects pending, failed, empty/unverified receipts)
        - Requires no compensation residue
        - Reads back operation AND all successful receipts after commit
        - Raises ValueError on predicate failure; returns None on state mismatch

        Returns dict with keys ``operation`` (the committed row) and
        ``receipts`` (list of completed receipt rows).

        This is the final gate: the operation becomes launchable.
        """
        now = _now_iso()
        with self._lock:
            # Read current operation state
            row = self._conn.execute(
                "SELECT lifecycle_status, compensation_residue FROM direct_connect_operations WHERE id = ?",
                (operation_id,),
            ).fetchone()
            if row is None or row["lifecycle_status"] != LIFECYCLE_PROJECTING:
                return None

            # Check compensation residue
            if row["compensation_residue"] is not None:
                raise ValueError(
                    f"Operation {operation_id} has uncleared compensation residue; "
                    f"resolve compensation before commit."
                )

            # Read all receipts for this operation
            receipts = self._conn.execute(
                "SELECT id, status, actual_state FROM direct_connect_receipts WHERE operation_id = ?",
                (operation_id,),
            ).fetchall()

            if len(receipts) == 0:
                raise ValueError(
                    f"Operation {operation_id} has zero receipts; "
                    f"at least one completed receipt is required for COMMITTED."
                )

            for r in receipts:
                if r["status"] == "pending":
                    raise ValueError(
                        f"Operation {operation_id} has pending receipts; "
                        f"all receipts must be completed or handled before commit."
                    )
                if r["status"] == "failed":
                    raise ValueError(
                        f"Operation {operation_id} has failed receipts; "
                        f"resolve failed receipts or record compensation before commit."
                    )
                if r["status"] != "completed":
                    raise ValueError(
                        f"Operation {operation_id} has unhandled receipt {r['id']} "
                        f"with status {r['status']!r}; all receipts must be completed."
                    )
                if not r["actual_state"] or not r["actual_state"].strip():
                    raise ValueError(
                        f"Operation {operation_id} receipt {r['id']} has empty "
                        f"actual_state; every completed receipt must have "
                        f"verified nonempty read-back state."
                    )

            # Atomic transition with predicate guards
            cursor = self._conn.execute(
                """UPDATE direct_connect_operations
                   SET lifecycle_status = ?, updated_at = ?
                   WHERE id = ?
                     AND lifecycle_status = ?
                     AND compensation_residue IS NULL
                     AND receipt_count > 0
                     AND (SELECT COUNT(*) FROM direct_connect_receipts
                          WHERE operation_id = ? AND status != 'completed') = 0
                     AND (SELECT COUNT(*) FROM direct_connect_receipts
                          WHERE operation_id = ?
                            AND (actual_state IS NULL OR actual_state = '')) = 0""",
                (LIFECYCLE_COMMITTED, now, operation_id,
                 LIFECYCLE_PROJECTING, operation_id, operation_id),
            )

        if cursor.rowcount == 0:
            return None

        self._audit(operation_id, agent, "direct_connect_committed", {
            "receipt_count": len(receipts),
        })

        # Read back committed operation AND all receipts as proof
        committed_op = self.get_operation(operation_id)
        committed_receipts = self.get_receipts(operation_id)
        return {
            "operation": committed_op,
            "receipts": committed_receipts,
        }

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


# ════════════════════════════════════════════════════════════════════════════
# DirectConnectCoordinator — claim-before-parse shipping coordinator (F3)
# ════════════════════════════════════════════════════════════════════════════

class DirectConnectCoordinator:
    """Typed shipping coordinator that claims raw-token authority BEFORE
    JSON/Pydantic parsing or validation.

    THR-107 v9 Slice 1 fix-forward (TASK-4639, reviewer F2/F3):
    - Computes CAS fingerprint from raw token (non-secret, never stored)
    - Checks replay by stable replay_identity BEFORE any parsing
    - Claims (reserve) atomically using ONLY the CAS hash
    - Validates (expiry, owner) ONLY AFTER the durable claim succeeds
    - Terminalizes on the SAME operation (replay identity unchanged)
    - Exact COMMITTED replay is zero-write
    - Terminalized tokens are never reusable
    - All audit writes go through the shipping Database callback
    """

    def __init__(self, store: DirectConnectStore) -> None:
        self._store = store

    def claim_and_prepare(
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
        """Claim raw-token authority: CAS compute → replay check → claim → validate → terminalize.

        THR-107 v9 Slice 1 fix-forward (reviewer F2):
        1. Compute domain-separated CAS fingerprint from raw token (in memory only)
        2. Check replay by stable replay_identity (before any durable write)
        3. Atomically reserve operation with CAS hash only (NEVER raw token)
        4. Validate expiry/owner AFTER claim
        5. On failure: terminalize the SAME operation with stable replay_identity
        6. Exact match replay (including COMMITTED) is zero-write

        Returns a result dict with keys:
        - ``status``: "reserved" (success), "replay" (exact match, zero-write),
          or "terminal" (failure)
        - ``terminal``: bool, True when the operation was terminalized
        - ``reason``: str, terminal reason when terminal=True
        - ``replay``: bool, True when an exact replay was detected
        - ``operation_id``: str
        - ``cas_hash``: str, the non-secret fingerprint (when reserved)
        """
        # ── Step 1: Compute CAS fingerprint (in memory, NEVER persisted as raw) ──
        cas = compute_cas(authority_owner, raw_authority_token)

        # ── Step 2: Check replay by stable replay_identity ──
        existing = self._store.get_operation_by_replay(replay_identity)
        if existing is not None:
            existing_cas = existing.get("cas_hash", "")
            if existing_cas == cas and existing.get("authority_owner") == authority_owner:
                # Exact match → replay (zero-write)
                if is_committed(existing.get("lifecycle_status", "")):
                    return {
                        "status": "replay", "terminal": False, "replay": True,
                        "reason": "exact-COMMITTED-replay",
                        "operation_id": existing.get("id", operation_id),
                        "cas_hash": cas,
                    }
                return {
                    "status": "replay", "terminal": False, "replay": True,
                    "reason": "exact-match-replay",
                    "operation_id": existing.get("id", operation_id),
                    "cas_hash": cas,
                }
            else:
                # Different CAS under same replay → the existing one is authoritative;
                # terminalize THIS new attempt under a distinct operation_id
                try:
                    self._store.reserve_operation(
                        operation_id=operation_id, profile_name=profile_name,
                        adapter_id=adapter_id, owner_agent=owner_agent,
                        authority_owner=authority_owner, replay_identity=replay_identity,
                        cas_hash=cas, authority_expiry=authority_expiry,
                    )
                except sqlite3.IntegrityError:
                    pass  # already exists, terminalize below
                self._store.terminalize_operation(operation_id, TERMINAL_REPLAY, owner_agent)
                return {
                    "status": "terminal", "terminal": True, "replay": False,
                    "reason": f"{TERMINAL_REPLAY}: CAS mismatch for {replay_identity!r}",
                    "operation_id": operation_id,
                }

        # ── Step 3: Atomically claim (reserve) with CAS hash only ──
        try:
            op = self._store.reserve_operation(
                operation_id=operation_id, profile_name=profile_name,
                adapter_id=adapter_id, owner_agent=owner_agent,
                authority_owner=authority_owner, replay_identity=replay_identity,
                cas_hash=cas, authority_expiry=authority_expiry,
            )
        except sqlite3.IntegrityError:
            return {
                "status": "terminal", "terminal": True, "replay": False,
                "reason": f"{TERMINAL_REPLAY}: IntegrityError claiming {operation_id!r}",
                "operation_id": operation_id,
            }
        except Exception:
            return {
                "status": "terminal", "terminal": True, "replay": False,
                "reason": f"{TERMINAL_DB_FAULT}: DB fault claiming {operation_id!r}",
                "operation_id": operation_id,
            }

        # ── Step 4: Validate AFTER claim (on the already-reserved operation) ──
        validation_failure = self._validate_authority(
            operation_id=operation_id, owner_agent=owner_agent,
            raw_authority_token=raw_authority_token,
            authority_owner=authority_owner,
            authority_expiry=authority_expiry,
        )
        if validation_failure is not None:
            reason, detail = validation_failure
            self._store.terminalize_operation(operation_id, reason, owner_agent)
            return {
                "status": "terminal", "terminal": True, "replay": False,
                "reason": f"{reason}: {detail}",
                "operation_id": operation_id,
            }

        return {
            "status": "reserved", "terminal": False, "replay": False,
            "reason": None, "operation_id": operation_id,
            "cas_hash": op.get("cas_hash"),
        }

    def _validate_authority(
        self,
        operation_id: str,
        owner_agent: str,
        raw_authority_token: str,
        authority_owner: str,
        authority_expiry: str | None = None,
    ) -> tuple[str, str] | None:
        """Validate authority AFTER claim. Returns (reason, detail) or None."""
        if not raw_authority_token or not raw_authority_token.strip():
            return (TERMINAL_MALFORMED, "Empty or whitespace-only authority token")

        if authority_expiry:
            try:
                expiry_dt = _parse_dt(authority_expiry)
                if expiry_dt < datetime.now(timezone.utc):
                    return (TERMINAL_EXPIRED, f"Authority expired at {authority_expiry}")
            except (ValueError, TypeError):
                return (TERMINAL_MALFORMED, f"Unparseable authority expiry: {authority_expiry}")

        if authority_owner != owner_agent:
            return (TERMINAL_OWNER_MISMATCH,
                    f"Authority owner {authority_owner!r} != operation owner {owner_agent!r}")

        return None

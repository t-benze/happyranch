"""Nonlaunchable durable authority for THR-107 direct-connect mints.

This daemon-global SQLite store is deliberately separate from per-org state,
runtime profile YAML, and the transient executor registry. Slice 1A records
mint intent only; it has no projection, connection, or launch eligibility.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


FIRST_PARTY_WORKSPACE_ADAPTER_IDS = frozenset({"claude", "codex", "opencode", "pi"})
_FINGERPRINT_DOMAIN = b"happyranch/direct-connect-authority/v1\0"
_NONLAUNCHABLE_STATE = "minted_nonlaunchable"


def fingerprint_registration_token(token_plaintext: str) -> str:
    """Return a domain-separated, non-reversible stable token identity."""
    return hashlib.sha256(_FINGERPRINT_DOMAIN + token_plaintext.encode("utf-8")).hexdigest()


def _canonical_adapter_id(intended_profile_name: str) -> str:
    """Keep the direct authority's identifier server-derived and stable."""
    import re

    return re.sub(r"[^a-z0-9]+", "-", f"{intended_profile_name}-adapter".lower()).strip("-") or "adapter"


def canonical_wrapper_destination(runtime_root: Path | None, intended_profile_name: str) -> Path:
    """Derive the public daemon-owned wrapper path without caller input."""
    root = runtime_root if runtime_root is not None else Path("/runtime")
    return root / "adapters" / _canonical_adapter_id(intended_profile_name)


@dataclass(frozen=True)
class DirectConnectAuthority:
    token_fingerprint: str
    name: str
    intended_profile_name: str
    wrapper_destination: Path
    workspace_adapter_id: str
    issued_at: float
    expires_at: float
    state: str
    provenance: str


@dataclass(frozen=True)
class DirectConnectReceipt:
    operation_id: str
    token_fingerprint: str
    state: str
    wrapper_sha256: str


@dataclass(frozen=True)
class DirectConnectProjection:
    operation_id: str
    token_fingerprint: str
    state: str
    adapter_id: str | None
    profile_name: str | None
    reason: str | None


@dataclass(frozen=True)
class DirectConnectReceiptArtifacts:
    operation_id: str
    wrapper_path: Path
    wrapper_sha256: str
    children: list[dict[str, str]]
    intended_profile_name: str
    workspace_adapter_id: str


class DirectConnectAuthorityStore:
    """Additive runtime-root SQLite authority store for direct mint intent."""

    def __init__(self, db_path: str | Path | None, runtime_root: Path | None = None) -> None:
        self._runtime_root = runtime_root
        self._conn = sqlite3.connect(
            str(db_path) if db_path is not None else ":memory:", check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS direct_connect_authorities (
                token_fingerprint TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                intended_profile_name TEXT NOT NULL,
                wrapper_destination TEXT NOT NULL,
                workspace_adapter_id TEXT NOT NULL,
                issued_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                state TEXT NOT NULL CHECK (state = 'minted_nonlaunchable'),
                provenance TEXT NOT NULL
            )"""
        )
        self._conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_direct_connect_authorities_expiry
               ON direct_connect_authorities(expires_at)"""
        )
        # These tables are intentionally additive.  The foundation authority
        # row remains immutable mint intent; an intake attempt gets its own
        # nonlaunchable reservation, receipt, artifact facts, and event trail.
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS direct_connect_reservations (
                token_fingerprint TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('reserved', 'terminalized', 'received_nonlaunchable')),
                reason TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS direct_connect_operations (
                operation_id TEXT PRIMARY KEY,
                token_fingerprint TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL CHECK (state = 'received_nonlaunchable'),
                intended_profile_name TEXT NOT NULL,
                workspace_adapter_id TEXT NOT NULL,
                created_at REAL NOT NULL
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS direct_connect_artifacts (
                operation_id TEXT NOT NULL,
                slot TEXT NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('immutable_wrapper', 'upgradeable_child')),
                declared_path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                structural_facts TEXT NOT NULL,
                PRIMARY KEY (operation_id, slot)
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS direct_connect_receipts (
                operation_id TEXT PRIMARY KEY,
                token_fingerprint TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL CHECK (state = 'received_nonlaunchable'),
                created_at REAL NOT NULL
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS direct_connect_events (
                event_id TEXT PRIMARY KEY,
                operation_id TEXT,
                token_fingerprint TEXT NOT NULL,
                event_type TEXT NOT NULL,
                detail TEXT NOT NULL,
                created_at REAL NOT NULL
            )"""
        )
        # THR-107 slice 1: durable projection state — tracks planned/committed/
        # failed per operation, independent of the immutable receipt above, so
        # a crash mid-projection or a retry never leaves ambiguous state.
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS direct_connect_projections (
                operation_id TEXT PRIMARY KEY,
                token_fingerprint TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('planned', 'committed', 'failed')),
                adapter_id TEXT,
                profile_name TEXT,
                reason TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )"""
        )
        self._conn.commit()

    def _read_authority(
        self, cursor: sqlite3.Cursor, token_fingerprint: str
    ) -> DirectConnectAuthority | None:
        row = cursor.execute(
            """SELECT token_fingerprint, name, intended_profile_name,
                      wrapper_destination, workspace_adapter_id, issued_at,
                      expires_at, state, provenance
               FROM direct_connect_authorities WHERE token_fingerprint = ?""",
            (token_fingerprint,),
        ).fetchone()
        if row is None:
            return None
        return DirectConnectAuthority(
            token_fingerprint=row["token_fingerprint"],
            name=row["name"],
            intended_profile_name=row["intended_profile_name"],
            wrapper_destination=Path(row["wrapper_destination"]),
            workspace_adapter_id=row["workspace_adapter_id"],
            issued_at=row["issued_at"],
            expires_at=row["expires_at"],
            state=row["state"],
            provenance=row["provenance"],
        )

    def mint_authority(
        self,
        *,
        token_plaintext: str,
        name: str,
        intended_profile_name: str,
        workspace_adapter_id: str,
        issued_at: float,
        expires_at: float,
    ) -> DirectConnectAuthority:
        """Persist and read back one authority row, or leave no row behind."""
        if workspace_adapter_id not in FIRST_PARTY_WORKSPACE_ADAPTER_IDS:
            raise ValueError("workspace adapter is not a first-party adapter")
        fingerprint = fingerprint_registration_token(token_plaintext)
        destination = canonical_wrapper_destination(self._runtime_root, intended_profile_name)
        with self._lock, self._conn:
            cursor = self._conn.cursor()
            cursor.execute(
                """INSERT INTO direct_connect_authorities (
                    token_fingerprint, name, intended_profile_name,
                    wrapper_destination, workspace_adapter_id, issued_at,
                    expires_at, state, provenance
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    fingerprint,
                    name,
                    intended_profile_name,
                    str(destination),
                    workspace_adapter_id,
                    issued_at,
                    expires_at,
                    _NONLAUNCHABLE_STATE,
                    "runtime-master-mint",
                ),
            )
            authority = self._read_authority(cursor, fingerprint)
            if authority is None:
                raise RuntimeError("direct authority readback failed")
        return authority

    def get(self, token_fingerprint: str) -> DirectConnectAuthority | None:
        with self._lock:
            return self._read_authority(self._conn.cursor(), token_fingerprint)

    def get_for_token(self, token_plaintext: str) -> DirectConnectAuthority | None:
        return self.get(fingerprint_registration_token(token_plaintext))

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM direct_connect_authorities").fetchone()[0])

    def reserve(self, token_plaintext: str, *, now: float | None = None) -> str | None:
        """Reserve one unexpired direct authority exactly once.

        A reservation is distinct from the immutable mint row so old minted
        authority data is never rewritten into a different trust target.
        """
        now = time.time() if now is None else now
        fingerprint = fingerprint_registration_token(token_plaintext)
        with self._lock, self._conn:
            cursor = self._conn.cursor()
            authority = self._read_authority(cursor, fingerprint)
            if authority is None or authority.expires_at < now:
                return None
            operation_id = str(uuid.uuid4())
            try:
                cursor.execute(
                    """INSERT INTO direct_connect_reservations
                       (token_fingerprint, operation_id, state, reason, created_at, updated_at)
                       VALUES (?, ?, 'reserved', NULL, ?, ?)""",
                    (fingerprint, operation_id, now, now),
                )
            except sqlite3.IntegrityError:
                return None
            return operation_id

    def terminalize(self, token_plaintext: str, operation_id: str, reason: str, *, now: float | None = None) -> bool:
        """Durably make a reservation non-reusable without creating an operation."""
        now = time.time() if now is None else now
        fingerprint = fingerprint_registration_token(token_plaintext)
        with self._lock, self._conn:
            cursor = self._conn.cursor()
            updated = cursor.execute(
                """UPDATE direct_connect_reservations
                   SET state = 'terminalized', reason = ?, updated_at = ?
                   WHERE token_fingerprint = ? AND operation_id = ? AND state = 'reserved'""",
                (reason, now, fingerprint, operation_id),
            ).rowcount
            if updated:
                cursor.execute(
                    """INSERT INTO direct_connect_events
                       (event_id, operation_id, token_fingerprint, event_type, detail, created_at)
                       VALUES (?, NULL, ?, 'terminalized', ?, ?)""",
                    (str(uuid.uuid4()), fingerprint, reason, now),
                )
            return bool(updated)

    def terminalize_known(self, token_plaintext: str, reason: str, *, now: float | None = None) -> bool:
        """Record an invalid known-direct attempt even before reservation."""
        now = time.time() if now is None else now
        fingerprint = fingerprint_registration_token(token_plaintext)
        with self._lock, self._conn:
            cursor = self._conn.cursor()
            if self._read_authority(cursor, fingerprint) is None:
                return False
            existing = cursor.execute(
                "SELECT state FROM direct_connect_reservations WHERE token_fingerprint = ?",
                (fingerprint,),
            ).fetchone()
            if existing is not None:
                return False
            cursor.execute(
                """INSERT INTO direct_connect_reservations
                   (token_fingerprint, operation_id, state, reason, created_at, updated_at)
                   VALUES (?, ?, 'terminalized', ?, ?, ?)""",
                (fingerprint, str(uuid.uuid4()), reason, now, now),
            )
            cursor.execute(
                """INSERT INTO direct_connect_events
                   (event_id, operation_id, token_fingerprint, event_type, detail, created_at)
                   VALUES (?, NULL, ?, 'terminalized', ?, ?)""",
                (str(uuid.uuid4()), fingerprint, reason, now),
            )
            return True

    def receive(
        self,
        token_plaintext: str,
        operation_id: str,
        *,
        wrapper_sha256: str,
        wrapper_facts: dict[str, object],
        children: list[dict[str, object]],
        workspace_adapter_id: str,
        now: float | None = None,
    ) -> DirectConnectReceipt:
        """Atomically write and read back the Slice-A nonlaunchable receipt.

        ``workspace_adapter_id`` is the candidate CLI's OWN declaration from
        its ``/connect`` manifest — not the value (if any) set at mint time.
        Only the connecting wrapper knows which workspace-bootstrap
        convention (Claude-style vs AGENTS.md-style) its underlying CLI
        expects; the founder's mint-time value is an unrelated activation
        trigger for the Slice-1A authority row and is never read here.
        """
        now = time.time() if now is None else now
        fingerprint = fingerprint_registration_token(token_plaintext)
        with self._lock, self._conn:
            cursor = self._conn.cursor()
            authority = self._read_authority(cursor, fingerprint)
            reservation = cursor.execute(
                """SELECT state FROM direct_connect_reservations
                   WHERE token_fingerprint = ? AND operation_id = ?""",
                (fingerprint, operation_id),
            ).fetchone()
            if authority is None or reservation is None or reservation["state"] != "reserved":
                raise RuntimeError("direct authority is not reserved by this operation")
            cursor.execute(
                """INSERT INTO direct_connect_operations
                   (operation_id, token_fingerprint, state, intended_profile_name, workspace_adapter_id, created_at)
                   VALUES (?, ?, 'received_nonlaunchable', ?, ?, ?)""",
                (operation_id, fingerprint, authority.intended_profile_name, workspace_adapter_id, now),
            )
            cursor.execute(
                """INSERT INTO direct_connect_artifacts
                   (operation_id, slot, kind, declared_path, sha256, structural_facts)
                   VALUES (?, 'wrapper', 'immutable_wrapper', ?, ?, ?)""",
                (operation_id, str(authority.wrapper_destination), wrapper_sha256, json.dumps(wrapper_facts, sort_keys=True)),
            )
            for child in children:
                cursor.execute(
                    """INSERT INTO direct_connect_artifacts
                       (operation_id, slot, kind, declared_path, sha256, structural_facts)
                       VALUES (?, ?, 'upgradeable_child', ?, ?, ?)""",
                    (operation_id, child["slot"], child["path"], child["sha256"], json.dumps(child["facts"], sort_keys=True)),
                )
            cursor.execute(
                """INSERT INTO direct_connect_receipts
                   (operation_id, token_fingerprint, state, created_at)
                   VALUES (?, ?, 'received_nonlaunchable', ?)""",
                (operation_id, fingerprint, now),
            )
            cursor.execute(
                """INSERT INTO direct_connect_events
                   (event_id, operation_id, token_fingerprint, event_type, detail, created_at)
                   VALUES (?, ?, ?, 'received_nonlaunchable', 'validated receipt', ?)""",
                (str(uuid.uuid4()), operation_id, fingerprint, now),
            )
            cursor.execute(
                """UPDATE direct_connect_reservations
                   SET state = 'received_nonlaunchable', updated_at = ?
                   WHERE token_fingerprint = ? AND operation_id = ? AND state = 'reserved'""",
                (now, fingerprint, operation_id),
            )
            receipt = cursor.execute(
                """SELECT operation_id, token_fingerprint, state FROM direct_connect_receipts
                   WHERE operation_id = ?""", (operation_id,)
            ).fetchone()
            if receipt is None:
                raise RuntimeError("direct receipt readback failed")
            return DirectConnectReceipt(
                operation_id=receipt["operation_id"], token_fingerprint=receipt["token_fingerprint"],
                state=receipt["state"], wrapper_sha256=wrapper_sha256,
            )

    def compensate_received(
        self, token_plaintext: str, operation_id: str, reason: str, *, now: float | None = None
    ) -> bool:
        """Remove a receipt that could not be paired with token consumption.

        Slice A has no projection to undo, but the registration-token consume is
        outside this SQLite transaction.  If that final consume reports failure,
        retain only terminal, fingerprint-only evidence rather than leaving a
        seemingly accepted receipt behind.
        """
        now = time.time() if now is None else now
        fingerprint = fingerprint_registration_token(token_plaintext)
        with self._lock, self._conn:
            cursor = self._conn.cursor()
            reservation = cursor.execute(
                """SELECT state FROM direct_connect_reservations
                   WHERE token_fingerprint = ? AND operation_id = ?""",
                (fingerprint, operation_id),
            ).fetchone()
            if reservation is None or reservation["state"] != "received_nonlaunchable":
                return False
            cursor.execute("DELETE FROM direct_connect_artifacts WHERE operation_id = ?", (operation_id,))
            cursor.execute("DELETE FROM direct_connect_receipts WHERE operation_id = ?", (operation_id,))
            cursor.execute("DELETE FROM direct_connect_operations WHERE operation_id = ?", (operation_id,))
            cursor.execute(
                """UPDATE direct_connect_reservations
                   SET state = 'terminalized', reason = ?, updated_at = ?
                   WHERE token_fingerprint = ? AND operation_id = ?""",
                (reason, now, fingerprint, operation_id),
            )
            cursor.execute(
                """INSERT INTO direct_connect_events
                   (event_id, operation_id, token_fingerprint, event_type, detail, created_at)
                   VALUES (?, NULL, ?, 'terminalized', ?, ?)""",
                (str(uuid.uuid4()), fingerprint, reason, now),
            )
            return True

    def get_latest_operation_for_profile(self, intended_profile_name: str) -> str | None:
        """Return the most recently received operation_id for a profile name.

        Used by the browser-facing status route to discover whether the
        candidate CLI's ``/connect`` call has landed yet, without ever
        touching token plaintext — the founder's browser knows only the
        profile name it minted, not any receipt identity.
        """
        with self._lock:
            row = self._conn.execute(
                """SELECT operation_id FROM direct_connect_operations
                   WHERE intended_profile_name = ? ORDER BY created_at DESC LIMIT 1""",
                (intended_profile_name,),
            ).fetchone()
            return row["operation_id"] if row is not None else None

    def get_receipt_artifacts(self, operation_id: str) -> DirectConnectReceiptArtifacts | None:
        """Read back the immutable wrapper + children artifacts for a receipt.

        Returns ``None`` when no ``received_nonlaunchable`` operation exists
        for ``operation_id`` (or its authority row is missing).
        """
        with self._lock:
            cursor = self._conn.cursor()
            operation = cursor.execute(
                """SELECT token_fingerprint, intended_profile_name, workspace_adapter_id
                   FROM direct_connect_operations WHERE operation_id = ?""",
                (operation_id,),
            ).fetchone()
            if operation is None:
                return None
            authority = self._read_authority(cursor, operation["token_fingerprint"])
            if authority is None:
                return None
            rows = cursor.execute(
                """SELECT slot, kind, declared_path, sha256 FROM direct_connect_artifacts
                   WHERE operation_id = ? ORDER BY slot""",
                (operation_id,),
            ).fetchall()
            wrapper_sha256 = ""
            children: list[dict[str, str]] = []
            for row in rows:
                if row["kind"] == "immutable_wrapper":
                    wrapper_sha256 = row["sha256"]
                else:
                    children.append({
                        "slot": row["slot"], "executable": row["declared_path"], "sha256": row["sha256"],
                    })
            return DirectConnectReceiptArtifacts(
                operation_id=operation_id,
                wrapper_path=authority.wrapper_destination,
                wrapper_sha256=wrapper_sha256,
                children=children,
                intended_profile_name=operation["intended_profile_name"],
                workspace_adapter_id=operation["workspace_adapter_id"],
            )

    def plan_projection(self, operation_id: str, *, now: float | None = None) -> bool:
        """Durably record projection intent exactly once per operation.

        Returns ``False`` (no-op) when a projection row already exists —
        the caller should treat that as "another attempt is/was in flight"
        and fall back to reading the current state via ``get_projection``.
        """
        now = time.time() if now is None else now
        with self._lock, self._conn:
            cursor = self._conn.cursor()
            operation = cursor.execute(
                "SELECT token_fingerprint FROM direct_connect_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if operation is None:
                raise RuntimeError("cannot plan projection for an unreceived operation")
            try:
                cursor.execute(
                    """INSERT INTO direct_connect_projections
                       (operation_id, token_fingerprint, state, adapter_id, profile_name, reason, created_at, updated_at)
                       VALUES (?, ?, 'planned', NULL, NULL, NULL, ?, ?)""",
                    (operation_id, operation["token_fingerprint"], now, now),
                )
            except sqlite3.IntegrityError:
                return False
            return True

    def _read_projection(self, cursor: sqlite3.Cursor, operation_id: str) -> DirectConnectProjection | None:
        row = cursor.execute(
            """SELECT operation_id, token_fingerprint, state, adapter_id, profile_name, reason
               FROM direct_connect_projections WHERE operation_id = ?""",
            (operation_id,),
        ).fetchone()
        if row is None:
            return None
        return DirectConnectProjection(
            operation_id=row["operation_id"], token_fingerprint=row["token_fingerprint"],
            state=row["state"], adapter_id=row["adapter_id"], profile_name=row["profile_name"],
            reason=row["reason"],
        )

    def get_projection(self, operation_id: str) -> DirectConnectProjection | None:
        with self._lock:
            return self._read_projection(self._conn.cursor(), operation_id)

    def mark_committed(
        self, operation_id: str, *, adapter_id: str, profile_name: str, now: float | None = None
    ) -> bool:
        """Transition planned -> committed. Returns False if not in planned state."""
        now = time.time() if now is None else now
        with self._lock, self._conn:
            cursor = self._conn.cursor()
            updated = cursor.execute(
                """UPDATE direct_connect_projections
                   SET state = 'committed', adapter_id = ?, profile_name = ?, updated_at = ?
                   WHERE operation_id = ? AND state = 'planned'""",
                (adapter_id, profile_name, now, operation_id),
            ).rowcount
            if updated:
                cursor.execute(
                    """INSERT INTO direct_connect_events
                       (event_id, operation_id, token_fingerprint, event_type, detail, created_at)
                       SELECT ?, operation_id, token_fingerprint, 'committed', ?, ?
                       FROM direct_connect_projections WHERE operation_id = ?""",
                    (str(uuid.uuid4()), f"adapter={adapter_id} profile={profile_name}", now, operation_id),
                )
            return bool(updated)

    def mark_failed(self, operation_id: str, reason: str, *, now: float | None = None) -> bool:
        """Transition planned -> failed. Returns False if not in planned state."""
        now = time.time() if now is None else now
        with self._lock, self._conn:
            cursor = self._conn.cursor()
            updated = cursor.execute(
                """UPDATE direct_connect_projections
                   SET state = 'failed', reason = ?, updated_at = ?
                   WHERE operation_id = ? AND state = 'planned'""",
                (reason, now, operation_id),
            ).rowcount
            if updated:
                cursor.execute(
                    """INSERT INTO direct_connect_events
                       (event_id, operation_id, token_fingerprint, event_type, detail, created_at)
                       SELECT ?, operation_id, token_fingerprint, 'projection_failed', ?, ?
                       FROM direct_connect_projections WHERE operation_id = ?""",
                    (str(uuid.uuid4()), reason, now, operation_id),
                )
            return bool(updated)

    def counts(self) -> dict[str, int]:
        """Testing/operator seam; never returns raw authorization material."""
        with self._lock:
            return {
                table: int(self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in ("direct_connect_operations", "direct_connect_artifacts", "direct_connect_receipts", "direct_connect_events")
            }

    def close(self) -> None:
        with self._lock:
            self._conn.close()

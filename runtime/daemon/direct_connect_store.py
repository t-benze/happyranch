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
MAX_DIRECT_CONNECT_ATTEMPTS = 2


class DirectConnectRetryInProgress(RuntimeError):
    """Raised when an atomic forget would race a claimed retry validation."""


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
class DirectConnectRetryAttempt:
    attempt_id: str
    operation_id: str
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
        # Retry validation is intentionally separate from the immutable
        # projection: a later successful probe must not rewrite the original
        # terminal failure or erase its evidence.
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS direct_connect_retry_attempts (
                attempt_id TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('running', 'succeeded', 'failed')),
                adapter_id TEXT,
                profile_name TEXT,
                reason TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )"""
        )
        self._conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_direct_connect_one_running_retry
               ON direct_connect_retry_attempts(operation_id) WHERE state = 'running'"""
        )
        self._migrate_attempt_series_tables()
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS direct_connect_operations (
                operation_id TEXT PRIMARY KEY,
                token_fingerprint TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state = 'received_nonlaunchable'),
                intended_profile_name TEXT NOT NULL,
                workspace_adapter_id TEXT NOT NULL,
                created_at REAL NOT NULL
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS direct_connect_receipts (
                operation_id TEXT PRIMARY KEY,
                token_fingerprint TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state = 'received_nonlaunchable'),
                created_at REAL NOT NULL
            )"""
        )
        # Parent lifecycle is deliberately separate from immutable mint
        # intent.  It is the sole authority for the bounded direct-only
        # reuse policy; no token material is stored here.
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS direct_connect_lifecycles (
                token_fingerprint TEXT PRIMARY KEY,
                state TEXT NOT NULL CHECK (state IN ('open', 'terminalized', 'consumed')),
                reason TEXT,
                updated_at REAL NOT NULL
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS direct_connect_attempts (
                operation_id TEXT PRIMARY KEY,
                token_fingerprint TEXT NOT NULL,
                attempt_number INTEGER NOT NULL CHECK (attempt_number >= 1),
                state TEXT NOT NULL CHECK (state IN ('reserved', 'received', 'terminalized')),
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(token_fingerprint, attempt_number)
            )"""
        )
        # Backfill pre-series receipts as their immutable first attempt.
        self._conn.execute(
            """INSERT OR IGNORE INTO direct_connect_lifecycles
               (token_fingerprint, state, reason, updated_at)
               SELECT token_fingerprint, 'open', NULL, expires_at FROM direct_connect_authorities"""
        )
        self._conn.execute(
            """INSERT OR IGNORE INTO direct_connect_attempts
               (operation_id, token_fingerprint, attempt_number, state, created_at, updated_at)
               SELECT operation_id, token_fingerprint, 1, 'received', created_at, created_at
               FROM direct_connect_operations"""
        )
        self._conn.commit()

    def _migrate_attempt_series_tables(self) -> None:
        """Replace only obsolete per-token UNIQUE constraints, preserving rows.

        SQLite cannot drop an inline UNIQUE constraint.  The migration copies
        the exact existing rows into same-column replacement tables inside the
        opening transaction, then atomically renames them.  Events and every
        other direct table are untouched; this is idempotent once the new DDL
        is installed.
        """
        migrations = (
            (
                "direct_connect_operations",
                "operation_id, token_fingerprint, state, intended_profile_name, workspace_adapter_id, created_at",
                """CREATE TABLE {name} (
                    operation_id TEXT PRIMARY KEY,
                    token_fingerprint TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state = 'received_nonlaunchable'),
                    intended_profile_name TEXT NOT NULL,
                    workspace_adapter_id TEXT NOT NULL,
                    created_at REAL NOT NULL
                )""",
            ),
            (
                "direct_connect_receipts",
                "operation_id, token_fingerprint, state, created_at",
                """CREATE TABLE {name} (
                    operation_id TEXT PRIMARY KEY,
                    token_fingerprint TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state = 'received_nonlaunchable'),
                    created_at REAL NOT NULL
                )""",
            ),
        )
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            for table, columns, ddl in migrations:
                row = self._conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
                ).fetchone()
                replacement = f"{table}_attempt_series_migration"
                replacement_row = self._conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (replacement,)
                ).fetchone()
                if row is not None and "token_fingerprint TEXT NOT NULL UNIQUE" in row["sql"]:
                    # A pre-transaction interruption can leave this deterministic
                    # scratch table behind.  The legacy table remains authoritative
                    # until its atomic replacement commits, so discard the stale
                    # scratch table and copy every preserved legacy row afresh.
                    if replacement_row is not None:
                        self._conn.execute(f"DROP TABLE {replacement}")
                    self._conn.execute(ddl.format(name=replacement))
                    self._conn.execute(f"INSERT INTO {replacement} ({columns}) SELECT {columns} FROM {table}")
                    self._conn.execute(f"DROP TABLE {table}")
                    self._conn.execute(f"ALTER TABLE {replacement} RENAME TO {table}")
                elif row is None and replacement_row is not None:
                    # Recover an old partial migration after DROP succeeded but
                    # before RENAME.  Accept only the exact non-UNIQUE target
                    # shape; an unfamiliar scratch table fails closed.
                    replacement_sql = replacement_row["sql"]
                    if (
                        "token_fingerprint TEXT NOT NULL" not in replacement_sql
                        or "token_fingerprint TEXT NOT NULL UNIQUE" in replacement_sql
                    ):
                        raise RuntimeError("unrecognized interrupted direct-connect migration")
                    self._conn.execute(f"ALTER TABLE {replacement} RENAME TO {table}")
                elif row is not None and replacement_row is not None:
                    # The current table is already authoritative; a stale scratch
                    # table can only be residue from an interrupted predecessor.
                    self._conn.execute(f"DROP TABLE {replacement}")
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

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
            cursor.execute(
                """INSERT INTO direct_connect_lifecycles
                   (token_fingerprint, state, reason, updated_at)
                   VALUES (?, 'open', NULL, ?)""",
                (fingerprint, issued_at),
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

    def is_known_direct_token(self, token_plaintext: str) -> bool:
        """Return whether a token is reserved for the direct-only surface."""
        return self.get_for_token(token_plaintext) is not None

    def reserve_or_join(self, token_plaintext: str, *, now: float | None = None) -> tuple[str | None, bool]:
        """Atomically reserve the next bounded attempt or join an active one.

        The second submission is allowed only after the first terminal
        conformance failure.  A matching concurrent caller gets the existing
        operation id and must not parse artifacts or start another projection.
        """
        now = time.time() if now is None else now
        fingerprint = fingerprint_registration_token(token_plaintext)
        with self._lock, self._conn:
            cursor = self._conn.cursor()
            authority = self._read_authority(cursor, fingerprint)
            if authority is None or authority.expires_at < now:
                return None, False
            lifecycle = cursor.execute(
                "SELECT state FROM direct_connect_lifecycles WHERE token_fingerprint = ?", (fingerprint,)
            ).fetchone()
            if lifecycle is None or lifecycle["state"] != "open":
                return None, False
            active = cursor.execute(
                """SELECT a.operation_id FROM direct_connect_attempts a
                   LEFT JOIN direct_connect_projections p ON p.operation_id = a.operation_id
                   WHERE a.token_fingerprint = ?
                     AND (a.state = 'reserved' OR p.state IS NULL OR p.state = 'planned')
                   ORDER BY a.attempt_number DESC LIMIT 1""",
                (fingerprint,),
            ).fetchone()
            if active is not None:
                return active["operation_id"], False
            attempt_count = int(cursor.execute(
                "SELECT COUNT(*) FROM direct_connect_attempts WHERE token_fingerprint = ?", (fingerprint,)
            ).fetchone()[0])
            if attempt_count >= MAX_DIRECT_CONNECT_ATTEMPTS:
                return None, False
            if attempt_count:
                previous = cursor.execute(
                    """SELECT p.state, p.reason FROM direct_connect_attempts a
                       JOIN direct_connect_projections p ON p.operation_id = a.operation_id
                       WHERE a.token_fingerprint = ? ORDER BY a.attempt_number DESC LIMIT 1""",
                    (fingerprint,),
                ).fetchone()
                if previous is None or previous["state"] != "failed" or not str(previous["reason"] or "").startswith("conformance_probe_failed:"):
                    return None, False
            operation_id = str(uuid.uuid4())
            cursor.execute(
                """INSERT INTO direct_connect_reservations
                   (token_fingerprint, operation_id, state, reason, created_at, updated_at)
                   VALUES (?, ?, 'reserved', NULL, ?, ?)
                   ON CONFLICT(token_fingerprint) DO UPDATE SET
                       operation_id=excluded.operation_id, state='reserved', reason=NULL, updated_at=excluded.updated_at""",
                (fingerprint, operation_id, now, now),
            )
            cursor.execute(
                """INSERT INTO direct_connect_attempts
                   (operation_id, token_fingerprint, attempt_number, state, created_at, updated_at)
                   VALUES (?, ?, ?, 'reserved', ?, ?)""",
                (operation_id, fingerprint, attempt_count + 1, now, now),
            )
            cursor.execute(
                """INSERT INTO direct_connect_events
                   (event_id, operation_id, token_fingerprint, event_type, detail, created_at)
                   VALUES (?, ?, ?, 'attempt_reserved', ?, ?)""",
                (str(uuid.uuid4()), operation_id, fingerprint, f"attempt={attempt_count + 1}", now),
            )
            return operation_id, True

    def reserve(self, token_plaintext: str, *, now: float | None = None) -> str | None:
        """Reserve one unexpired direct authority exactly once.

        A reservation is distinct from the immutable mint row so old minted
        authority data is never rewritten into a different trust target.
        """
        operation_id, owner = self.reserve_or_join(token_plaintext, now=now)
        return operation_id if owner else None

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
                    """UPDATE direct_connect_attempts SET state = 'terminalized', updated_at = ?
                       WHERE operation_id = ? AND state = 'reserved'""", (now, operation_id)
                )
                cursor.execute(
                    """UPDATE direct_connect_lifecycles SET state = 'terminalized', reason = ?, updated_at = ?
                       WHERE token_fingerprint = ?""", (reason, now, fingerprint)
                )
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
            cursor.execute(
                """UPDATE direct_connect_lifecycles SET state = 'terminalized', reason = ?, updated_at = ?
                   WHERE token_fingerprint = ? AND state = 'open'""", (reason, now, fingerprint)
            )
            if not cursor.rowcount:
                return False
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
            attempt = cursor.execute(
                "SELECT attempt_number FROM direct_connect_attempts WHERE operation_id = ?", (operation_id,)
            ).fetchone()
            if attempt is None:
                raise RuntimeError("direct attempt is not reserved by this operation")
            if attempt["attempt_number"] > 1:
                prior = cursor.execute(
                    """SELECT a.kind, a.slot, a.sha256 FROM direct_connect_artifacts a
                       JOIN direct_connect_attempts t ON t.operation_id = a.operation_id
                       WHERE t.token_fingerprint = ? AND t.attempt_number = ?
                       ORDER BY a.kind, a.slot""",
                    (fingerprint, attempt["attempt_number"] - 1),
                ).fetchall()
                proposed = [("immutable_wrapper", "wrapper", wrapper_sha256)]
                proposed.extend(
                    sorted(("upgradeable_child", str(child["slot"]), str(child["sha256"])) for child in children)
                )
                if [(row["kind"], row["slot"], row["sha256"]) for row in prior] == proposed:
                    raise ValueError("retry requires changed artifact integrity")
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
            cursor.execute(
                """UPDATE direct_connect_attempts SET state = 'received', updated_at = ?
                   WHERE operation_id = ? AND state = 'reserved'""", (now, operation_id)
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

    def status_for_profile(self, intended_profile_name: str, *, now: float | None = None) -> dict[str, object]:
        """Return nonsecret parent/attempt status for browser polling."""
        now = time.time() if now is None else now
        with self._lock:
            row = self._conn.execute(
                """SELECT o.operation_id, o.token_fingerprint, a.expires_at, l.state,
                          COUNT(t.operation_id) AS attempt_count
                   FROM direct_connect_operations o
                   JOIN direct_connect_authorities a ON a.token_fingerprint = o.token_fingerprint
                   JOIN direct_connect_lifecycles l ON l.token_fingerprint = o.token_fingerprint
                   JOIN direct_connect_attempts t ON t.token_fingerprint = o.token_fingerprint
                   WHERE o.intended_profile_name = ?
                   GROUP BY o.operation_id, o.token_fingerprint, a.expires_at, l.state
                   ORDER BY o.created_at DESC LIMIT 1""",
                (intended_profile_name,),
            ).fetchone()
            if row is None:
                return {"attempt_count": 0, "retry_eligible": False, "expires_at": None}
            projection = self._read_projection(self._conn.cursor(), row["operation_id"])
            retry_eligible = (
                row["state"] == "open" and row["expires_at"] >= now
                and int(row["attempt_count"]) < MAX_DIRECT_CONNECT_ATTEMPTS
                and projection is not None and projection.state == "failed"
                and str(projection.reason or "").startswith("conformance_probe_failed:")
            )
            return {
                "attempt_count": int(row["attempt_count"]),
                "retry_eligible": retry_eligible,
                "expires_at": row["expires_at"],
            }

    def list_operations_pending_projection(self) -> list[str]:
        """received_nonlaunchable operation_ids with no projection row yet, oldest first."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT o.operation_id FROM direct_connect_operations o
                   LEFT JOIN direct_connect_projections p ON p.operation_id = o.operation_id
                   WHERE p.operation_id IS NULL
                   ORDER BY o.created_at ASC"""
            ).fetchall()
            return [row["operation_id"] for row in rows]

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
            wrapper_path: Path | None = None
            wrapper_sha256 = ""
            children: list[dict[str, str]] = []
            for row in rows:
                if row["kind"] == "immutable_wrapper":
                    if wrapper_path is not None:
                        return None
                    wrapper_path = Path(row["declared_path"])
                    wrapper_sha256 = row["sha256"]
                else:
                    children.append({
                        "slot": row["slot"], "executable": row["declared_path"], "sha256": row["sha256"],
                    })
            if wrapper_path is None or not wrapper_sha256 or not children:
                return None
            child_slots = [child["slot"] for child in children]
            child_paths = [child["executable"] for child in children]
            if len(child_slots) != len(set(child_slots)) or len(child_paths) != len(set(child_paths)):
                return None
            return DirectConnectReceiptArtifacts(
                operation_id=operation_id,
                wrapper_path=wrapper_path,
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

    def forget_operation(self, operation_id: str) -> str | None:
        """Delete a terminal-failed operation and return its profile name.

        The caller owns deletion of the derived wrapper path.  No state other
        than a durable failed projection is eligible for removal.  A claimed
        retry holds this same store transaction boundary until it settles.
        """
        with self._lock, self._conn:
            cursor = self._conn.cursor()
            projection = cursor.execute(
                "SELECT state FROM direct_connect_projections WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if projection is None or projection["state"] != "failed":
                return None
            if cursor.execute(
                """SELECT 1 FROM direct_connect_retry_attempts
                   WHERE operation_id = ? AND state = 'running' LIMIT 1""",
                (operation_id,),
            ).fetchone() is not None:
                raise DirectConnectRetryInProgress("retry validation is running")
            # A retry success binds a live profile while deliberately leaving
            # the original projection as immutable failed evidence.  That
            # historical state must never make a connected profile forgettable.
            if cursor.execute(
                """SELECT 1 FROM direct_connect_retry_attempts
                   WHERE operation_id = ? AND state = 'succeeded' LIMIT 1""",
                (operation_id,),
            ).fetchone() is not None:
                return None
            operation = cursor.execute(
                """SELECT token_fingerprint, intended_profile_name
                   FROM direct_connect_operations WHERE operation_id = ?""",
                (operation_id,),
            ).fetchone()
            if operation is None:
                return None
            token_fingerprint = operation["token_fingerprint"]
            intended_profile_name = operation["intended_profile_name"]
            # History and the parent authority are append-only.  In
            # particular, a cleanup request for attempt 1 must never remove
            # a staged attempt 2 or the canonical wrapper it needs.
            newer = cursor.execute(
                """SELECT 1 FROM direct_connect_attempts newer
                   JOIN direct_connect_attempts current ON current.operation_id = ?
                   WHERE newer.token_fingerprint = current.token_fingerprint
                     AND newer.attempt_number > current.attempt_number LIMIT 1""",
                (operation_id,),
            ).fetchone()
            cursor.execute(
                """INSERT INTO direct_connect_events
                   (event_id, operation_id, token_fingerprint, event_type, detail, created_at)
                   VALUES (?, ?, ?, 'forget_requested', ?, ?)""",
                (str(uuid.uuid4()), operation_id, token_fingerprint,
                 "history retained; newer attempt protected" if newer else "history retained", time.time()),
            )
            return intended_profile_name

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
                    """UPDATE direct_connect_lifecycles SET state = 'consumed', reason = 'projection_committed', updated_at = ?
                       WHERE token_fingerprint = (SELECT token_fingerprint FROM direct_connect_projections WHERE operation_id = ?)""",
                    (now, operation_id),
                )
                cursor.execute(
                    """INSERT INTO direct_connect_events
                       (event_id, operation_id, token_fingerprint, event_type, detail, created_at)
                       SELECT ?, operation_id, token_fingerprint, 'committed', ?, ?
                       FROM direct_connect_projections WHERE operation_id = ?""",
                    (str(uuid.uuid4()), f"adapter={adapter_id} profile={profile_name}", now, operation_id),
                )
            return bool(updated)

    def consume_authority_for_operation(self, operation_id: str, *, now: float | None = None) -> bool:
        """Close a parent when a compatible retry validation binds a profile."""
        now = time.time() if now is None else now
        with self._lock, self._conn:
            updated = self._conn.execute(
                """UPDATE direct_connect_lifecycles SET state = 'consumed', reason = 'bound_profile', updated_at = ?
                   WHERE token_fingerprint = (SELECT token_fingerprint FROM direct_connect_operations WHERE operation_id = ?)
                     AND state = 'open'""", (now, operation_id)
            ).rowcount
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

    def _read_retry_attempt(
        self, cursor: sqlite3.Cursor, attempt_id: str
    ) -> DirectConnectRetryAttempt | None:
        row = cursor.execute(
            """SELECT attempt_id, operation_id, state, adapter_id, profile_name, reason
               FROM direct_connect_retry_attempts WHERE attempt_id = ?""",
            (attempt_id,),
        ).fetchone()
        if row is None:
            return None
        return DirectConnectRetryAttempt(
            attempt_id=row["attempt_id"], operation_id=row["operation_id"], state=row["state"],
            adapter_id=row["adapter_id"], profile_name=row["profile_name"], reason=row["reason"],
        )

    def claim_retry_attempt(
        self, operation_id: str, *, now: float | None = None,
    ) -> tuple[DirectConnectRetryAttempt, bool]:
        """Claim the sole live retry for an original failed projection.

        A successful retry is idempotent. A failed retry permits a later fresh
        retry, while concurrent callers share the one running attempt.
        """
        now = time.time() if now is None else now
        with self._lock, self._conn:
            cursor = self._conn.cursor()
            projection = self._read_projection(cursor, operation_id)
            if projection is None:
                raise RuntimeError("direct operation not found")
            if projection.state != "failed":
                raise ValueError(f"projection state is '{projection.state}', not 'failed'")
            succeeded = cursor.execute(
                """SELECT attempt_id FROM direct_connect_retry_attempts
                   WHERE operation_id = ? AND state = 'succeeded' ORDER BY created_at DESC LIMIT 1""",
                (operation_id,),
            ).fetchone()
            if succeeded is not None:
                attempt = self._read_retry_attempt(cursor, succeeded["attempt_id"])
                assert attempt is not None
                return attempt, False
            running = cursor.execute(
                """SELECT attempt_id FROM direct_connect_retry_attempts
                   WHERE operation_id = ? AND state = 'running'""",
                (operation_id,),
            ).fetchone()
            if running is not None:
                attempt = self._read_retry_attempt(cursor, running["attempt_id"])
                assert attempt is not None
                return attempt, False
            attempt_id = str(uuid.uuid4())
            cursor.execute(
                """INSERT INTO direct_connect_retry_attempts
                   (attempt_id, operation_id, state, adapter_id, profile_name, reason, created_at, updated_at)
                   VALUES (?, ?, 'running', NULL, NULL, NULL, ?, ?)""",
                (attempt_id, operation_id, now, now),
            )
            cursor.execute(
                """INSERT INTO direct_connect_events
                   (event_id, operation_id, token_fingerprint, event_type, detail, created_at)
                   SELECT ?, operation_id, token_fingerprint, 'retry_validation_started', 'retry_validation', ?
                   FROM direct_connect_projections WHERE operation_id = ?""",
                (str(uuid.uuid4()), now, operation_id),
            )
            attempt = self._read_retry_attempt(cursor, attempt_id)
            assert attempt is not None
            return attempt, True

    def get_retry_attempt(self, attempt_id: str) -> DirectConnectRetryAttempt | None:
        with self._lock:
            return self._read_retry_attempt(self._conn.cursor(), attempt_id)

    def get_successful_retry(self, operation_id: str) -> DirectConnectRetryAttempt | None:
        with self._lock:
            row = self._conn.execute(
                """SELECT attempt_id FROM direct_connect_retry_attempts
                   WHERE operation_id = ? AND state = 'succeeded' ORDER BY created_at DESC LIMIT 1""",
                (operation_id,),
            ).fetchone()
            return self._read_retry_attempt(self._conn.cursor(), row["attempt_id"]) if row else None

    def finish_retry_attempt(
        self,
        attempt_id: str,
        *,
        state: str,
        adapter_id: str | None = None,
        profile_name: str | None = None,
        reason: str | None = None,
        now: float | None = None,
    ) -> bool:
        """Finish one retry attempt and append only category-level evidence."""
        if state not in {"succeeded", "failed"}:
            raise ValueError("retry attempt must finish terminally")
        now = time.time() if now is None else now
        event_type = "retry_validation_succeeded" if state == "succeeded" else "retry_validation_failed"
        detail = "retry_validation_succeeded" if state == "succeeded" else (reason or "retry_validation_failed")
        with self._lock, self._conn:
            cursor = self._conn.cursor()
            updated = cursor.execute(
                """UPDATE direct_connect_retry_attempts
                   SET state = ?, adapter_id = ?, profile_name = ?, reason = ?, updated_at = ?
                   WHERE attempt_id = ? AND state = 'running'""",
                (state, adapter_id, profile_name, reason, now, attempt_id),
            ).rowcount
            if updated:
                cursor.execute(
                    """INSERT INTO direct_connect_events
                       (event_id, operation_id, token_fingerprint, event_type, detail, created_at)
                       SELECT ?, p.operation_id, p.token_fingerprint, ?, ?, ?
                       FROM direct_connect_retry_attempts r
                       JOIN direct_connect_projections p ON p.operation_id = r.operation_id
                       WHERE r.attempt_id = ?""",
                    (str(uuid.uuid4()), event_type, detail, now, attempt_id),
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

"""Nonlaunchable durable authority for THR-107 direct-connect mints.

This daemon-global SQLite store is deliberately separate from per-org state,
runtime profile YAML, and the transient executor registry. Slice 1A records
mint intent only; it has no projection, connection, or launch eligibility.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


FIRST_PARTY_WORKSPACE_ADAPTER_IDS = frozenset({"claude", "codex", "opencode", "pi"})
_FINGERPRINT_DOMAIN = b"happyranch/direct-connect-authority/v1\0"
_NONLAUNCHABLE_STATE = "minted_nonlaunchable"


class DirectConnectRetryInProgress(RuntimeError):
    """Raised when an atomic forget would race a claimed retry validation."""


class DuplicateCandidateError(ValueError):
    """Raised when a candidate identity matches one already accepted for this token."""


class InProgressAdmissionError(RuntimeError):
    """Raised when a concurrent admission is already in flight for this token."""


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


@dataclass(frozen=True)
class DirectConnectForgetOutcome:
    """Failed-only cleanup result, including the verified wrapper disposition."""

    intended_profile_name: str
    wrapper_status: str


@dataclass(frozen=True)
class DirectConnectCandidate:
    """One accepted candidate for a direct-connect parent lifecycle."""

    candidate_id: str
    operation_id: str
    attempt_ordinal: int
    state: str
    identity_hash: str


_ADMISSION_ADMIT_FIRST = "admit_first"
_ADMISSION_ADMIT_RETRY = "admit_retry"
_ADMISSION_DUPLICATE = "duplicate"
_ADMISSION_IN_PROGRESS = "in_progress"
_ADMISSION_TERMINAL_NONRETRYABLE = "terminal_nonretryable"

_IDENTITY_DOMAIN = "happyranch/direct-connect/identity/v1"


def _remove_matching_failed_wrapper(artifacts: DirectConnectReceiptArtifacts | None) -> str:
    """Resolve a receipt wrapper without ever unlinking a mutable pathname.

    POSIX provides no compare-and-unlink operation keyed to an opened file's
    identity.  Once a pathname is verified, any pathname unlink can still
    remove a replacement installed immediately afterwards.  Retaining a
    present wrapper is therefore the only fail-closed disposition.
    """
    if artifacts is None:
        return "preserved_unsafe"
    wrapper_path = artifacts.wrapper_path
    expected_sha = artifacts.wrapper_sha256
    try:
        descriptor = os.open(wrapper_path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return "already_absent"
    except OSError:
        return "preserved_unsafe"
    try:
        with os.fdopen(descriptor, "rb") as wrapper_file:
            if not stat.S_ISREG(os.fstat(wrapper_file.fileno()).st_mode) or not expected_sha:
                return "preserved_unsafe"
            actual_sha = hashlib.file_digest(wrapper_file, "sha256").hexdigest()
    except OSError:
        return "preserved_unsafe"
    if actual_sha != expected_sha:
        return "preserved_changed"
    return "preserved_unsafe"


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
                token_fingerprint TEXT NOT NULL,
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
                token_fingerprint TEXT NOT NULL,
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
        # THR-160: durable parent/candidate/identity history for same-token
        # direct-connect retry. These tables are additive to the Slice-A receipt
        # schema and are keyed by the direct-purpose registration token fingerprint.
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS direct_connect_parent_lifecycles (
                token_fingerprint TEXT PRIMARY KEY,
                state TEXT NOT NULL CHECK (state IN ('open', 'committed', 'failed', 'expired')),
                latest_accepted_candidate_id TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                expires_at REAL NOT NULL
            )"""
        )
        self._conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_direct_connect_parent_lifecycles_expiry
               ON direct_connect_parent_lifecycles(expires_at)"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS direct_connect_candidates (
                candidate_id TEXT PRIMARY KEY,
                token_fingerprint TEXT NOT NULL,
                operation_id TEXT UNIQUE,
                attempt_ordinal INTEGER NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('received_nonlaunchable', 'committed', 'failed')),
                identity_hash TEXT NOT NULL,
                created_at REAL NOT NULL
            )"""
        )
        self._conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_direct_connect_candidates_token
               ON direct_connect_candidates(token_fingerprint)"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS direct_connect_identity_history (
                history_id TEXT PRIMARY KEY,
                token_fingerprint TEXT NOT NULL,
                candidate_id TEXT NOT NULL,
                identity_hash TEXT NOT NULL,
                identity_blob TEXT NOT NULL,
                created_at REAL NOT NULL
            )"""
        )
        self._conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_direct_connect_identity_history_token
               ON direct_connect_identity_history(token_fingerprint)"""
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

    @staticmethod
    def normalize_identity_hash(
        wrapper_path: Path,
        wrapper_sha256: str,
        wrapper_facts: dict[str, object],
        children: list[dict[str, object]],
        workspace_adapter_id: str,
        manifest_version: int,
    ) -> str:
        """Return a domain-separated, order-independent identity hash.

        The canonical form covers the server-fixed wrapper destination, wrapper
        and child paths/hashes, structural/probe facts, workspace adapter id, and
        manifest version. It intentionally excludes client-controlled ordering so
        materially identical candidates hash the same even when children are
        reordered.
        """
        slots: set[str] = set()
        paths: set[str] = set()
        for child in children:
            slot = child["slot"]
            path = child["path"]
            if slot in slots:
                raise ValueError(f"duplicate child slot: {slot}")
            if path in paths:
                raise ValueError(f"duplicate child path: {path}")
            slots.add(slot)
            paths.add(path)
        canonical_children = sorted(
            [
                {
                    "path": child["path"],
                    "sha256": child["sha256"],
                    "facts": child["facts"],
                    "version_probe_argv": child.get("version_probe_argv", []),
                }
                for child in children
            ],
            key=lambda c: c["path"],
        )
        canonical = {
            "domain": _IDENTITY_DOMAIN,
            "wrapper_path": str(wrapper_path),
            "wrapper_sha256": wrapper_sha256,
            "wrapper_facts": wrapper_facts,
            "children": canonical_children,
            "workspace_adapter_id": workspace_adapter_id,
            "manifest_version": manifest_version,
        }
        return hashlib.sha256(
            json.dumps(canonical, sort_keys=True).encode("utf-8")
        ).hexdigest()

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM direct_connect_authorities").fetchone()[0])

    def reserve(
        self,
        token_plaintext: str,
        *,
        identity_hash: str | None = None,
        identity_blob: str | None = None,
        now: float | None = None,
    ) -> str | None:
        """Reserve one candidate slot atomically under the parent lifecycle.

        When ``identity_hash`` and ``identity_blob`` are supplied the THR-160
        retry-aware path is used: it creates the parent lifecycle row on first
        use, enforces duplicate-candidate detection, and raises
        ``DuplicateCandidateError`` or ``InProgressAdmissionError`` for racing
        admissions.  Callers that omit identity arguments use the legacy single-
        reservation path, which preserves the pre-THR-160 store contract and
        returns ``None`` when a reservation already exists for the token.

        Returns ``None`` for terminal non-retryable states (expired, committed,
        failed parent, or more than one prior accepted candidate).
        """
        now = time.time() if now is None else now
        fingerprint = fingerprint_registration_token(token_plaintext)
        with self._lock, self._conn:
            cursor = self._conn.cursor()
            authority = self._read_authority(cursor, fingerprint)
            if authority is None or authority.expires_at < now:
                return None

            # Legacy path: no identity means no parent/candidate lifecycle.
            if identity_hash is None or identity_blob is None:
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

            parent = cursor.execute(
                "SELECT state, latest_accepted_candidate_id, expires_at FROM direct_connect_parent_lifecycles WHERE token_fingerprint = ?",
                (fingerprint,),
            ).fetchone()
            if parent is None:
                cursor.execute(
                    """INSERT INTO direct_connect_parent_lifecycles
                       (token_fingerprint, state, latest_accepted_candidate_id, created_at, updated_at, expires_at)
                       VALUES (?, 'open', NULL, ?, ?, ?)""",
                    (fingerprint, now, now, authority.expires_at),
                )
            else:
                if parent["expires_at"] < now or parent["state"] != "open":
                    return None

            duplicate = cursor.execute(
                "SELECT 1 FROM direct_connect_identity_history WHERE token_fingerprint = ? AND identity_hash = ? LIMIT 1",
                (fingerprint, identity_hash),
            ).fetchone()
            if duplicate is not None:
                raise DuplicateCandidateError("candidate identity already accepted")

            candidates = cursor.execute(
                """SELECT candidate_id, operation_id, state FROM direct_connect_candidates
                   WHERE token_fingerprint = ? ORDER BY attempt_ordinal ASC""",
                (fingerprint,),
            ).fetchall()
            for candidate in candidates:
                if candidate["state"] != "received_nonlaunchable":
                    continue
                projection = cursor.execute(
                    "SELECT state FROM direct_connect_projections WHERE operation_id = ?",
                    (candidate["operation_id"],),
                ).fetchone()
                if projection is None or projection["state"] != "failed":
                    raise InProgressAdmissionError("direct admission is in progress")
            running_probe = cursor.execute(
                """SELECT 1 FROM direct_connect_retry_attempts r
                   JOIN direct_connect_candidates c ON c.operation_id = r.operation_id
                   WHERE c.token_fingerprint = ? AND r.state = 'running' LIMIT 1""",
                (fingerprint,),
            ).fetchone()
            if running_probe is not None:
                raise InProgressAdmissionError("direct retry probe is in progress")

            terminal_count = len([c for c in candidates if c["state"] != "received_nonlaunchable"])
            if terminal_count >= 2:
                return None
            received_count = len([c for c in candidates if c["state"] == "received_nonlaunchable"])
            if terminal_count == 1 or received_count > 1:
                return None
            if received_count == 1:
                candidate = candidates[0]
                projection = cursor.execute(
                    "SELECT state FROM direct_connect_projections WHERE operation_id = ?",
                    (candidate["operation_id"],),
                ).fetchone()
                if projection is None or projection["state"] != "failed":
                    return None

            attempt_ordinal = len(candidates) + 1
            operation_id = str(uuid.uuid4())
            candidate_id = str(uuid.uuid4())

            cursor.execute(
                """INSERT INTO direct_connect_candidates
                   (candidate_id, token_fingerprint, operation_id, attempt_ordinal, state, identity_hash, created_at)
                   VALUES (?, ?, ?, ?, 'received_nonlaunchable', ?, ?)""",
                (candidate_id, fingerprint, operation_id, attempt_ordinal, identity_hash, now),
            )
            try:
                cursor.execute(
                    """INSERT INTO direct_connect_reservations
                       (token_fingerprint, operation_id, state, reason, created_at, updated_at)
                       VALUES (?, ?, 'reserved', NULL, ?, ?)""",
                    (fingerprint, operation_id, now, now),
                )
            except sqlite3.IntegrityError:
                cursor.execute(
                    """UPDATE direct_connect_reservations
                       SET operation_id = ?, state = 'reserved', reason = NULL, updated_at = ?
                       WHERE token_fingerprint = ?""",
                    (operation_id, now, fingerprint),
                )
            return operation_id

    def evaluate_admission(
        self,
        token_plaintext: str,
        *,
        identity_hash: str,
        identity_blob: str,
        now: float | None = None,
    ) -> tuple[str, str | None]:
        """Decide and durably record admission for one /connect payload.

        Returns a tuple of (verdict, operation_id). Verdict is one of the
        module-level admission constants. ``operation_id`` is present for
        ``ADMIT_FIRST`` and ``ADMIT_RETRY``.
        """
        try:
            operation_id = self.reserve(
                token_plaintext,
                identity_hash=identity_hash,
                identity_blob=identity_blob,
                now=now,
            )
        except DuplicateCandidateError:
            return (_ADMISSION_DUPLICATE, None)
        except InProgressAdmissionError:
            return (_ADMISSION_IN_PROGRESS, None)
        if operation_id is None:
            return (_ADMISSION_TERMINAL_NONRETRYABLE, None)

        fingerprint = fingerprint_registration_token(token_plaintext)
        with self._lock, self._conn:
            cursor = self._conn.cursor()
            candidate = cursor.execute(
                """SELECT attempt_ordinal FROM direct_connect_candidates
                   WHERE token_fingerprint = ? AND operation_id = ?""",
                (fingerprint, operation_id),
            ).fetchone()
        if candidate is None:
            return (_ADMISSION_TERMINAL_NONRETRYABLE, None)
        if candidate["attempt_ordinal"] == 1:
            return (_ADMISSION_ADMIT_FIRST, operation_id)
        return (_ADMISSION_ADMIT_RETRY, operation_id)

    def list_candidates(self, token_plaintext: str) -> list[DirectConnectCandidate]:
        """Return every candidate accepted for this token, oldest first."""
        fingerprint = fingerprint_registration_token(token_plaintext)
        with self._lock:
            rows = self._conn.execute(
                """SELECT candidate_id, operation_id, attempt_ordinal, state, identity_hash
                   FROM direct_connect_candidates
                   WHERE token_fingerprint = ? ORDER BY attempt_ordinal ASC""",
                (fingerprint,),
            ).fetchall()
            return [
                DirectConnectCandidate(
                    candidate_id=row["candidate_id"],
                    operation_id=row["operation_id"],
                    attempt_ordinal=row["attempt_ordinal"],
                    state=row["state"],
                    identity_hash=row["identity_hash"],
                )
                for row in rows
            ]

    def parent_state(self, token_plaintext: str) -> str | None:
        """Return the durable parent lifecycle state, or None if no parent row."""
        fingerprint = fingerprint_registration_token(token_plaintext)
        with self._lock:
            row = self._conn.execute(
                "SELECT state FROM direct_connect_parent_lifecycles WHERE token_fingerprint = ?",
                (fingerprint,),
            ).fetchone()
            return row["state"] if row is not None else None

    def is_retryable(self, token_plaintext: str, *, now: float | None = None) -> bool:
        """True iff the durable parent lifecycle admits exactly one corrected retry."""
        now = time.time() if now is None else now
        fingerprint = fingerprint_registration_token(token_plaintext)
        with self._lock, self._conn:
            cursor = self._conn.cursor()
            parent = cursor.execute(
                "SELECT state, expires_at, latest_accepted_candidate_id FROM direct_connect_parent_lifecycles WHERE token_fingerprint = ?",
                (fingerprint,),
            ).fetchone()
            if parent is None:
                return False
            if parent["expires_at"] < now or parent["state"] != "open":
                return False
            committed = cursor.execute(
                "SELECT 1 FROM direct_connect_candidates WHERE token_fingerprint = ? AND state = 'committed' LIMIT 1",
                (fingerprint,),
            ).fetchone()
            if committed is not None:
                return False
            candidates = cursor.execute(
                """SELECT candidate_id, operation_id, state FROM direct_connect_candidates
                   WHERE token_fingerprint = ? ORDER BY attempt_ordinal ASC""",
                (fingerprint,),
            ).fetchall()
            if len(candidates) != 1:
                return False
            if candidates[0]["candidate_id"] != parent["latest_accepted_candidate_id"]:
                return False
            if candidates[0]["state"] != "received_nonlaunchable":
                return False
            projection = cursor.execute(
                "SELECT state FROM direct_connect_projections WHERE operation_id = ?",
                (candidates[0]["operation_id"],),
            ).fetchone()
            if projection is None or projection["state"] != "failed":
                return False
            running_probe = cursor.execute(
                """SELECT 1 FROM direct_connect_retry_attempts r
                   JOIN direct_connect_candidates c ON c.operation_id = r.operation_id
                   WHERE c.token_fingerprint = ? AND r.state = 'running' LIMIT 1""",
                (fingerprint,),
            ).fetchone()
            if running_probe is not None:
                return False
            return True

    def terminalize(self, token_plaintext: str, operation_id: str, reason: str, *, now: float | None = None) -> bool:
        """Durably make a reservation non-reusable and mark the candidate failed."""
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
                cursor.execute(
                    """UPDATE direct_connect_candidates
                       SET state = 'failed'
                       WHERE operation_id = ? AND state = 'received_nonlaunchable'""",
                    (operation_id,),
                )
                if not reason.startswith("conformance_probe_failed"):
                    cursor.execute(
                        """UPDATE direct_connect_parent_lifecycles
                           SET state = 'failed', updated_at = ?
                           WHERE token_fingerprint = ? AND state = 'open'""",
                        (now, fingerprint),
                    )
            return bool(updated)

    def terminalize_known(self, token_plaintext: str, reason: str, *, now: float | None = None) -> bool:
        """Record an invalid known-direct attempt and close the parent lifecycle."""
        now = time.time() if now is None else now
        fingerprint = fingerprint_registration_token(token_plaintext)
        with self._lock, self._conn:
            cursor = self._conn.cursor()
            authority = self._read_authority(cursor, fingerprint)
            if authority is None:
                return False
            existing = cursor.execute(
                "SELECT state FROM direct_connect_reservations WHERE token_fingerprint = ?",
                (fingerprint,),
            ).fetchone()
            if existing is None:
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
            cursor.execute(
                """INSERT OR IGNORE INTO direct_connect_parent_lifecycles
                   (token_fingerprint, state, latest_accepted_candidate_id, created_at, updated_at, expires_at)
                   VALUES (?, 'failed', NULL, ?, ?, ?)""",
                (fingerprint, now, now, authority.expires_at),
            )
            cursor.execute(
                """UPDATE direct_connect_parent_lifecycles
                   SET state = 'failed', updated_at = ?
                   WHERE token_fingerprint = ? AND state = 'open'""",
                (now, fingerprint),
            )
            return True

    def receive(
        self,
        token_plaintext: str,
        operation_id: str,
        *,
        wrapper_sha256: str,
        wrapper_facts: dict[str, object],
        wrapper_path: Path | None = None,
        children: list[dict[str, object]],
        workspace_adapter_id: str,
        identity_hash: str | None = None,
        identity_blob: str | None = None,
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
            artifact_wrapper_path = wrapper_path if wrapper_path is not None else authority.wrapper_destination
            cursor.execute(
                """INSERT INTO direct_connect_artifacts
                   (operation_id, slot, kind, declared_path, sha256, structural_facts)
                   VALUES (?, 'wrapper', 'immutable_wrapper', ?, ?, ?)""",
                (operation_id, str(artifact_wrapper_path), wrapper_sha256, json.dumps(wrapper_facts, sort_keys=True)),
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
            if identity_hash is not None and identity_blob is not None:
                candidate = cursor.execute(
                    "SELECT candidate_id FROM direct_connect_candidates WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                if candidate is not None:
                    cursor.execute(
                        """INSERT INTO direct_connect_identity_history
                           (history_id, token_fingerprint, candidate_id, identity_hash, identity_blob, created_at)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (str(uuid.uuid4()), fingerprint, candidate["candidate_id"], identity_hash, identity_blob, now),
                    )
                    cursor.execute(
                        """UPDATE direct_connect_parent_lifecycles
                           SET latest_accepted_candidate_id = ?, updated_at = ?
                           WHERE token_fingerprint = ?""",
                        (candidate["candidate_id"], now, fingerprint),
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
        """Terminalize a reservation when generic-token consumption failed.

        The receipt artifacts are preserved so the failure is attributable to
        the generic token seam, not the candidate identity. Identity history is
        retained forever; the parent lifecycle stays open for retry.
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

    def _read_receipt_artifacts(
        self, cursor: sqlite3.Cursor, operation_id: str,
    ) -> DirectConnectReceiptArtifacts | None:
        operation = cursor.execute(
            """SELECT token_fingerprint, intended_profile_name, workspace_adapter_id
               FROM direct_connect_operations WHERE operation_id = ?""",
            (operation_id,),
        ).fetchone()
        if operation is None or self._read_authority(cursor, operation["token_fingerprint"]) is None:
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

    def get_receipt_artifacts(self, operation_id: str) -> DirectConnectReceiptArtifacts | None:
        """Read back the immutable wrapper + children artifacts for a receipt."""
        with self._lock:
            return self._read_receipt_artifacts(self._conn.cursor(), operation_id)

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

    def forget_operation(self, operation_id: str) -> DirectConnectForgetOutcome | None:
        """Atomically clean up one terminal failed operation and its wrapper.

        The wrapper's receipt path and SHA are inspected and resolved while
        the failed receipt remains present and the store lock excludes retry
        claims. Only after that result is final are authority rows deleted.
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
            wrapper_status = _remove_matching_failed_wrapper(
                self._read_receipt_artifacts(cursor, operation_id)
            )
            cursor.execute(
                "DELETE FROM direct_connect_retry_attempts WHERE operation_id = ?", (operation_id,)
            )
            cursor.execute("DELETE FROM direct_connect_artifacts WHERE operation_id = ?", (operation_id,))
            cursor.execute("DELETE FROM direct_connect_receipts WHERE operation_id = ?", (operation_id,))
            cursor.execute("DELETE FROM direct_connect_operations WHERE operation_id = ?", (operation_id,))
            cursor.execute("DELETE FROM direct_connect_projections WHERE operation_id = ?", (operation_id,))
            cursor.execute(
                """DELETE FROM direct_connect_reservations
                   WHERE token_fingerprint = ? AND operation_id = ?""",
                (token_fingerprint, operation_id),
            )
            cursor.execute(
                """UPDATE direct_connect_parent_lifecycles
                   SET state = 'failed', updated_at = ?
                   WHERE token_fingerprint = ?""",
                (time.time(), token_fingerprint),
            )
            cursor.execute(
                """INSERT INTO direct_connect_events
                   (event_id, operation_id, token_fingerprint, event_type, detail, created_at)
                   VALUES (?, ?, ?, 'forgotten', 'terminal failed operation removed', ?)""",
                (str(uuid.uuid4()), operation_id, token_fingerprint, time.time()),
            )
            return DirectConnectForgetOutcome(
                intended_profile_name=intended_profile_name,
                wrapper_status=wrapper_status,
            )

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
                cursor.execute(
                    """UPDATE direct_connect_parent_lifecycles
                       SET state = 'committed', updated_at = ?
                       WHERE token_fingerprint = (
                           SELECT token_fingerprint FROM direct_connect_projections WHERE operation_id = ?
                       ) AND state = 'open'""",
                    (now, operation_id),
                )
            return bool(updated)

    def mark_failed(self, operation_id: str, reason: str, *, now: float | None = None) -> bool:
        """Transition planned -> failed. Returns False if not in planned state."""
        now = time.time() if now is None else now
        with self._lock, self._conn:
            cursor = self._conn.cursor()
            projection = cursor.execute(
                """SELECT token_fingerprint FROM direct_connect_projections
                   WHERE operation_id = ? AND state = 'planned'""",
                (operation_id,),
            ).fetchone()
            if projection is None:
                return False
            fingerprint = projection["token_fingerprint"]
            cursor.execute(
                """UPDATE direct_connect_projections
                   SET state = 'failed', reason = ?, updated_at = ?
                   WHERE operation_id = ? AND state = 'planned'""",
                (reason, now, operation_id),
            )
            cursor.execute(
                """INSERT INTO direct_connect_events
                   (event_id, operation_id, token_fingerprint, event_type, detail, created_at)
                   VALUES (?, ?, ?, 'projection_failed', ?, ?)""",
                (str(uuid.uuid4()), operation_id, fingerprint, reason, now),
            )
            latest = cursor.execute(
                """SELECT latest_accepted_candidate_id FROM direct_connect_parent_lifecycles
                   WHERE token_fingerprint = ?""",
                (fingerprint,),
            ).fetchone()
            if latest is not None:
                candidate = cursor.execute(
                    """SELECT candidate_id FROM direct_connect_candidates
                       WHERE operation_id = ? AND candidate_id = ?""",
                    (operation_id, latest["latest_accepted_candidate_id"]),
                ).fetchone()
                if candidate is not None and not reason.startswith("conformance_probe_failed"):
                    cursor.execute(
                        """UPDATE direct_connect_parent_lifecycles
                           SET state = 'failed', updated_at = ?
                           WHERE token_fingerprint = ? AND state = 'open'""",
                        (now, fingerprint),
                    )
            return True

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
                for table in (
                    "direct_connect_operations",
                    "direct_connect_artifacts",
                    "direct_connect_receipts",
                    "direct_connect_events",
                )
            }

    def close(self) -> None:
        with self._lock:
            self._conn.close()

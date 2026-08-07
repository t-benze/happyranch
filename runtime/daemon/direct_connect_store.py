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
import threading
import time
from dataclasses import dataclass
from pathlib import Path


FIRST_PARTY_WORKSPACE_ADAPTER_IDS = frozenset({"claude", "codex", "opencode", "pi"})
_FINGERPRINT_DOMAIN = b"happyranch/direct-connect-authority/v1\0"
_NONLAUNCHABLE_STATE = "minted_nonlaunchable"
_PRE_PROJECTION_STATE = "pre_projection"
_TERMINAL_STATE = "terminal"


def fingerprint_registration_token(token_plaintext: str) -> str:
    """Return a domain-separated, non-reversible stable token identity."""
    return hashlib.sha256(_FINGERPRINT_DOMAIN + token_plaintext.encode("utf-8")).hexdigest()


def _wrapper_destination(runtime_root: Path | None, intended_profile_name: str) -> Path:
    """Derive the public contract-reference target without touching the file.

    This deliberately mirrors ``compute_canonical_adapter_path``'s naming
    contract but does not create the directory or wrapper at mint time.
    ``runtime_root`` is the daemon home in production and lets the store keep
    its authority independent from the YAML/registry projection surfaces.
    """
    root = runtime_root if runtime_root is not None else Path("/runtime")
    from runtime.orchestrator.custom_adapter_registry import generate_adapter_id

    return (root / "adapters" / generate_adapter_id(f"{intended_profile_name}-adapter")).absolute()


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
class DirectConnectOperation:
    token_fingerprint: str
    adapter_id: str
    intended_profile_name: str
    workspace_adapter_id: str
    wrapper_destination: Path
    wrapper_sha256: str
    request_fingerprint: str
    state: str
    metadata_json: str
    dependencies_json: str


def stable_request_fingerprint(metadata: dict, dependencies: list[dict]) -> str:
    """Return a stable fingerprint for exact direct-ingress replay only."""
    payload = json.dumps(
        {"metadata": metadata, "dependencies": dependencies},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_no_symlink_regular_executable(path: Path) -> tuple[Path, str]:
    """Validate an exact absolute artifact without following any symlink.

    The helper is intentionally validation-only: it never creates, copies,
    moves, chmods, or executes an artifact.  Launch-time retained-handle
    protection remains a later, separate execution-fence slice.
    """
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("artifact path is not a canonical absolute path")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            st = os.lstat(current)
        except FileNotFoundError as exc:
            raise ValueError("artifact is missing") from exc
        if os.path.islink(current):
            raise ValueError("artifact path contains a symlink")
        if current != path and not os.path.isdir(current):
            raise ValueError("artifact parent is not a directory")
    if not os.path.isfile(path):
        raise ValueError("artifact is not a regular file")
    if not os.access(path, os.X_OK):
        raise ValueError("artifact is not executable")
    resolved = path.resolve(strict=True)
    if resolved != path:
        raise ValueError("artifact path is not canonical")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return resolved, digest


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
            """CREATE TABLE IF NOT EXISTS direct_connect_operations (
                token_fingerprint TEXT PRIMARY KEY,
                adapter_id TEXT NOT NULL,
                intended_profile_name TEXT NOT NULL,
                workspace_adapter_id TEXT NOT NULL,
                wrapper_destination TEXT NOT NULL,
                wrapper_sha256 TEXT NOT NULL,
                request_fingerprint TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('reserved', 'pre_projection', 'terminal')),
                metadata_json TEXT NOT NULL,
                dependencies_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS direct_connect_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_fingerprint TEXT NOT NULL,
                event_type TEXT NOT NULL,
                detail TEXT NOT NULL,
                created_at REAL NOT NULL
            )"""
        )
        self._conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_direct_connect_authorities_expiry
               ON direct_connect_authorities(expires_at)"""
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
        destination = _wrapper_destination(self._runtime_root, intended_profile_name)
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

    def get_operation(self, token_fingerprint: str) -> DirectConnectOperation | None:
        with self._lock:
            row = self._conn.execute(
                """SELECT token_fingerprint, adapter_id, intended_profile_name,
                          workspace_adapter_id, wrapper_destination, wrapper_sha256,
                          request_fingerprint, state, metadata_json, dependencies_json
                   FROM direct_connect_operations WHERE token_fingerprint = ?""",
                (token_fingerprint,),
            ).fetchone()
        if row is None:
            return None
        return DirectConnectOperation(
            token_fingerprint=row["token_fingerprint"], adapter_id=row["adapter_id"],
            intended_profile_name=row["intended_profile_name"],
            workspace_adapter_id=row["workspace_adapter_id"],
            wrapper_destination=Path(row["wrapper_destination"]),
            wrapper_sha256=row["wrapper_sha256"], request_fingerprint=row["request_fingerprint"],
            state=row["state"], metadata_json=row["metadata_json"], dependencies_json=row["dependencies_json"],
        )

    def terminalize(self, token_fingerprint: str, reason: str) -> None:
        """Durably make a known direct authority non-reusable after bad intake."""
        now = time.time()
        with self._lock, self._conn:
            cursor = self._conn.cursor()
            existing = cursor.execute(
                "SELECT state FROM direct_connect_operations WHERE token_fingerprint = ?",
                (token_fingerprint,),
            ).fetchone()
            if existing is not None:
                return
            authority = self._read_authority(cursor, token_fingerprint)
            if authority is None:
                return
            cursor.execute(
                """INSERT INTO direct_connect_operations (
                    token_fingerprint, adapter_id, intended_profile_name, workspace_adapter_id,
                    wrapper_destination, wrapper_sha256, request_fingerprint, state,
                    metadata_json, dependencies_json, created_at, updated_at
                ) VALUES (?, '', ?, ?, ?, '', '', 'terminal', '{}', '[]', ?, ?)""",
                (token_fingerprint, authority.intended_profile_name,
                 authority.workspace_adapter_id, str(authority.wrapper_destination), now, now),
            )
            cursor.execute(
                "INSERT INTO direct_connect_events (token_fingerprint, event_type, detail, created_at) VALUES (?, ?, ?, ?)",
                (token_fingerprint, "terminalized", reason, now),
            )

    def commit_pre_projection(
        self,
        *,
        authority: DirectConnectAuthority,
        metadata: dict,
        dependencies: list[dict],
    ) -> DirectConnectOperation:
        """Atomically reserve, append event, and read back a nonlaunchable intake.

        A prior identical committed request is the sole replay.  Every other
        existing operation is a single-winner conflict and is left untouched.
        """
        request_fingerprint = stable_request_fingerprint(metadata, dependencies)
        adapter_id = authority.wrapper_destination.name
        canonical = _wrapper_destination(self._runtime_root, authority.intended_profile_name)
        if authority.wrapper_destination != canonical:
            raise ValueError("stale direct authority destination requires a fresh mint")
        _, wrapper_sha256 = _validate_no_symlink_regular_executable(canonical)
        now = time.time()
        with self._lock, self._conn:
            cursor = self._conn.cursor()
            previous = self.get_operation(authority.token_fingerprint)
            if previous is not None:
                if previous.state == _PRE_PROJECTION_STATE and previous.request_fingerprint == request_fingerprint:
                    return previous
                raise ValueError("direct authority is no longer reusable")
            cursor.execute(
                """INSERT INTO direct_connect_operations (
                    token_fingerprint, adapter_id, intended_profile_name, workspace_adapter_id,
                    wrapper_destination, wrapper_sha256, request_fingerprint, state,
                    metadata_json, dependencies_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    authority.token_fingerprint, adapter_id, authority.intended_profile_name,
                    authority.workspace_adapter_id, str(canonical), wrapper_sha256,
                    request_fingerprint, _PRE_PROJECTION_STATE,
                    json.dumps(metadata, sort_keys=True), json.dumps(dependencies, sort_keys=True), now, now,
                ),
            )
            cursor.execute(
                "INSERT INTO direct_connect_events (token_fingerprint, event_type, detail, created_at) VALUES (?, ?, ?, ?)",
                (authority.token_fingerprint, "direct_connected_pre_projection", request_fingerprint, now),
            )
            operation = self.get_operation(authority.token_fingerprint)
            if operation is None:
                raise RuntimeError("direct operation readback failed")
        return operation

    def close(self) -> None:
        with self._lock:
            self._conn.close()

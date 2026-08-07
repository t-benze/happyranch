"""Nonlaunchable durable authority for THR-107 direct-connect mints.

This daemon-global SQLite store is deliberately separate from per-org state,
runtime profile YAML, and the transient executor registry. Slice 1A records
mint intent only; it has no projection, connection, or launch eligibility.
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path


FIRST_PARTY_WORKSPACE_ADAPTER_IDS = frozenset({"claude", "codex", "opencode", "pi"})
_FINGERPRINT_DOMAIN = b"happyranch/direct-connect-authority/v1\0"
_DESTINATION_DOMAIN = b"happyranch/direct-connect-wrapper-destination/v1\0"
_NONLAUNCHABLE_STATE = "minted_nonlaunchable"


def fingerprint_registration_token(token_plaintext: str) -> str:
    """Return a domain-separated, non-reversible stable token identity."""
    return hashlib.sha256(_FINGERPRINT_DOMAIN + token_plaintext.encode("utf-8")).hexdigest()


def _wrapper_destination(runtime_root: Path | None, intended_profile_name: str) -> Path:
    """Derive a daemon-owned destination without accepting a caller path."""
    root = runtime_root if runtime_root is not None else Path("/runtime")
    profile_digest = hashlib.sha256(
        _DESTINATION_DOMAIN + intended_profile_name.encode("utf-8")
    ).hexdigest()
    return root / "direct-connect" / profile_digest / "adapter-wrapper"


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

    def close(self) -> None:
        with self._lock:
            self._conn.close()

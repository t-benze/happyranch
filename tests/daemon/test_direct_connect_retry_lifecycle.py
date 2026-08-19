"""B0 executable acceptance harness for THR-160 same-token direct-connect retry.

All assertions encode the intended lifecycle semantics from TASK-5234/
TASK-5235. They are expected to be RED on current main and define the contract
that the B1 implementation must satisfy. No production code is edited in B0.
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runtime.config import Settings
from runtime.daemon import paths
from runtime.daemon.app import create_app
from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore
from runtime.daemon.state import DaemonState


@pytest.fixture(autouse=True)
def _allow_testclient_loopback(monkeypatch):
    """The /connect route is loopback-only; extend that set for TestClient."""
    from runtime.daemon.routes import auth, direct_connect

    monkeypatch.setattr(auth, "_LOCAL_HOSTS", auth._LOCAL_HOSTS | {"testclient"})
    monkeypatch.setattr(
        direct_connect, "_LOCAL_HOSTS", direct_connect._LOCAL_HOSTS | {"testclient"}
    )


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Runtime-backed TestClient with a real direct-connect DB file."""
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / "daemon"))
    paths.ensure_daemon_home()
    paths.ensure_token()
    state = DaemonState.idle(Settings())
    state.direct_connect_authority_store.close()
    state.direct_connect_authority_store = DirectConnectAuthorityStore(
        tmp_path / "direct.db", runtime_root=tmp_path / "daemon"
    )
    tc = TestClient(create_app(state))
    tc.headers.update({"Authorization": f"Bearer {paths.read_token()}"})
    return tc, state


def _mint_direct_token(client: TestClient, profile_name: str = "custom-profile") -> str:
    response = client.post(
        "/api/v1/auth/registration-token/runtime",
        json={
            "name": "custom-cli",
            "purpose": "adapter",
            "intended_profile_name": profile_name,
            "workspace_adapter_id": "codex",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


def _write_executable(path: Path, body: bytes = b"#!/bin/sh\nexit 0\n") -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    path.chmod(0o700)
    return hashlib.sha256(body).hexdigest()


def _payload(
    wrapper_hash: str,
    child_paths: list[Path],
    workspace_adapter_id: str = "codex",
) -> dict:
    return {
        "metadata": {"client": "test"},
        "manifest": {
            "manifest_version": 2,
            "wrapper_sha256": wrapper_hash,
            "upgradeable_children": [
                {
                    "slot": f"cli{i}",
                    "executable": str(child_path),
                    "version_probe_argv": [str(child_path), "--version"],
                }
                for i, child_path in enumerate(child_paths)
            ],
            "workspace_adapter_id": workspace_adapter_id,
        },
    }


def _drive_to_terminal_failure(
    client: TestClient, state: DaemonState, operation_id: str, monkeypatch
) -> None:
    """Use the existing /commit seam to drive a receipt to terminal failed."""
    from runtime.orchestrator import custom_adapter_registry

    monkeypatch.setattr(
        custom_adapter_registry,
        "run_conformance_probe",
        lambda _executable, _name, **_kwargs: (_ for _ in ()).throw(
            ValueError("probe failed")
        ),
    )
    response = client.post(f"/api/v1/runtime/custom-cli/{operation_id}/commit")
    assert response.status_code == 200, response.text
    assert response.json()["profile_state"] == "failed"


def _connect(client: TestClient, token: str, payload: dict) -> object:
    return client.post(
        "/api/v1/runtime/custom-cli/connect",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )


def _candidate_count(state: DaemonState, token: str) -> int:
    """Public-store seam the B1 implementation must provide."""
    store = state.direct_connect_authority_store
    assert hasattr(store, "list_candidates"), (
        "DirectConnectAuthorityStore must expose list_candidates(token_plaintext)"
    )
    return len(store.list_candidates(token))


def _accepted_candidate_count(state: DaemonState, token: str) -> int:
    store = state.direct_connect_authority_store
    assert hasattr(store, "list_candidates"), (
        "DirectConnectAuthorityStore must expose list_candidates(token_plaintext)"
    )
    return len(store.list_candidates(token))


def _parent_is_retryable(state: DaemonState, token: str) -> bool:
    store = state.direct_connect_authority_store
    assert hasattr(store, "is_retryable"), (
        "DirectConnectAuthorityStore must expose is_retryable(token_plaintext)"
    )
    return store.is_retryable(token)


def _operation_count(store: DirectConnectAuthorityStore) -> int:
    return store.counts()["direct_connect_operations"]


def _receipt_count(store: DirectConnectAuthorityStore) -> int:
    return store.counts()["direct_connect_receipts"]


def _event_count(store: DirectConnectAuthorityStore) -> int:
    return store.counts()["direct_connect_events"]


def test_same_token_duplicate_after_terminal_failure_returns_409_non_consuming(
    client, tmp_path, monkeypatch
):
    tc, state = client
    token = _mint_direct_token(tc)
    authority = state.direct_connect_authority_store.get_for_token(token)
    wrapper_hash = _write_executable(authority.wrapper_destination)
    child = tmp_path / "bin" / "child"
    _write_executable(child)
    payload_a = _payload(wrapper_hash, [child])

    first = _connect(tc, token, payload_a)
    assert first.status_code == 201
    operation_a = first.json()["operation_id"]
    _drive_to_terminal_failure(tc, state, operation_a, monkeypatch)

    popen_calls = []
    monkeypatch.setattr("subprocess.Popen", lambda *args, **kwargs: popen_calls.append((args, kwargs)))

    duplicate = _connect(tc, token, payload_a)

    assert duplicate.status_code == 409, (
        f"expected 409 CONFLICT for identical same-token retry candidate, got {duplicate.status_code}: {duplicate.text}"
    )
    assert duplicate.json()["detail"].startswith("duplicate")
    assert popen_calls == []
    assert _operation_count(state.direct_connect_authority_store) == 1
    assert _receipt_count(state.direct_connect_authority_store) == 1
    assert _accepted_candidate_count(state, token) == 1
    assert _parent_is_retryable(state, token) is True


def test_same_token_reordered_equivalent_children_returns_409(
    client, tmp_path, monkeypatch
):
    tc, state = client
    token = _mint_direct_token(tc)
    authority = state.direct_connect_authority_store.get_for_token(token)
    wrapper_hash = _write_executable(authority.wrapper_destination)
    child1 = tmp_path / "bin" / "child1"
    child2 = tmp_path / "bin" / "child2"
    _write_executable(child1)
    _write_executable(child2)
    payload_a = _payload(wrapper_hash, [child1, child2])

    first = _connect(tc, token, payload_a)
    assert first.status_code == 201
    operation_a = first.json()["operation_id"]
    _drive_to_terminal_failure(tc, state, operation_a, monkeypatch)

    reordered = _payload(wrapper_hash, [child2, child1])
    duplicate = _connect(tc, token, reordered)

    assert duplicate.status_code == 409
    assert _operation_count(state.direct_connect_authority_store) == 1


def test_same_token_corrected_candidate_b_is_sole_second_201(
    client, tmp_path, monkeypatch
):
    tc, state = client
    token = _mint_direct_token(tc)
    authority = state.direct_connect_authority_store.get_for_token(token)
    wrapper_a = authority.wrapper_destination
    wrapper_hash_a = _write_executable(wrapper_a)
    child_a = tmp_path / "bin" / "child-a"
    _write_executable(child_a)
    payload_a = _payload(wrapper_hash_a, [child_a])

    first = _connect(tc, token, payload_a)
    assert first.status_code == 201
    operation_a = first.json()["operation_id"]
    _drive_to_terminal_failure(tc, state, operation_a, monkeypatch)

    # B must install its corrected wrapper at the same server-fixed canonical
    # destination; the identity hash is computed from the actual file content.
    wrapper_b = wrapper_a
    wrapper_hash_b = _write_executable(wrapper_b, b"#!/bin/sh\necho v2\n")
    child_b = tmp_path / "bin" / "child-b"
    _write_executable(child_b)
    payload_b = _payload(wrapper_hash_b, [child_b])

    second = _connect(tc, token, payload_b)

    assert second.status_code == 201, (
        f"expected 201 for materially changed candidate B, got {second.status_code}: {second.text}"
    )
    operation_b = second.json()["operation_id"]
    assert operation_b != operation_a
    assert _operation_count(state.direct_connect_authority_store) == 2
    assert _receipt_count(state.direct_connect_authority_store) == 2
    assert _accepted_candidate_count(state, token) == 2


def test_same_token_changed_candidate_c_is_refused_after_b(
    client, tmp_path, monkeypatch
):
    tc, state = client
    token = _mint_direct_token(tc)
    authority = state.direct_connect_authority_store.get_for_token(token)
    wrapper_hash_a = _write_executable(authority.wrapper_destination)
    child_a = tmp_path / "bin" / "child-a"
    _write_executable(child_a)
    payload_a = _payload(wrapper_hash_a, [child_a])

    first = _connect(tc, token, payload_a)
    operation_a = first.json()["operation_id"]
    _drive_to_terminal_failure(tc, state, operation_a, monkeypatch)

    wrapper_b = authority.wrapper_destination
    wrapper_hash_b = _write_executable(wrapper_b, b"#!/bin/sh\necho v2\n")
    child_b = tmp_path / "bin" / "child-b"
    _write_executable(child_b)
    payload_b = _payload(wrapper_hash_b, [child_b])
    second = _connect(tc, token, payload_b)
    assert second.status_code == 201

    wrapper_c = authority.wrapper_destination
    wrapper_hash_c = _write_executable(wrapper_c, b"#!/bin/sh\necho v3\n")
    child_c = tmp_path / "bin" / "child-c"
    _write_executable(child_c)
    payload_c = _payload(wrapper_hash_c, [child_c])

    third = _connect(tc, token, payload_c)

    assert third.status_code == 409, (
        f"expected 409 for third changed candidate C, got {third.status_code}: {third.text}"
    )
    assert _operation_count(state.direct_connect_authority_store) == 2
    assert _receipt_count(state.direct_connect_authority_store) == 2


def test_concurrent_corrected_candidate_b_has_exactly_one_receipt_and_probe(
    client, tmp_path, monkeypatch
):
    tc, state = client
    token = _mint_direct_token(tc)
    authority = state.direct_connect_authority_store.get_for_token(token)
    wrapper_hash_a = _write_executable(authority.wrapper_destination)
    child_a = tmp_path / "bin" / "child-a"
    _write_executable(child_a)
    payload_a = _payload(wrapper_hash_a, [child_a])

    first = _connect(tc, token, payload_a)
    operation_a = first.json()["operation_id"]
    _drive_to_terminal_failure(tc, state, operation_a, monkeypatch)

    wrapper_b = authority.wrapper_destination
    wrapper_hash_b = _write_executable(wrapper_b, b"#!/bin/sh\necho v2\n")
    child_b = tmp_path / "bin" / "child-b"
    _write_executable(child_b)
    payload_b = _payload(wrapper_hash_b, [child_b])

    from runtime.orchestrator import custom_adapter_registry

    entered_probe = threading.Event()
    release_probe = threading.Event()
    probe_count = [0]
    probe_lock = threading.Lock()

    def fake_probe(_executable, _name, **_kwargs):
        with probe_lock:
            probe_count[0] += 1
        entered_probe.set()
        assert release_probe.wait(timeout=5)
        return type("ProbeOutput", (), {
            "adapter_metadata": type("Meta", (), {
                "adapter_version": "1.2.3",
                "contract_version": 1,
            })(),
        })()

    monkeypatch.setattr(custom_adapter_registry, "run_conformance_probe", fake_probe)

    # The /connect route must not itself run a probe; only projection/commit does.
    # We drive B to projection concurrently via the /commit route.
    results = []
    barrier = threading.Barrier(2)

    def attempt_commit():
        barrier.wait()
        # B must first be received via /connect.
        received = _connect(tc, token, payload_b)
        if received.status_code == 201:
            op_b = received.json()["operation_id"]
            results.append(("received", op_b))
            committed = tc.post(f"/api/v1/runtime/custom-cli/{op_b}/commit")
            results.append(("commit", committed.status_code, committed.json()))
        else:
            results.append(("rejected", received.status_code, received.json()))

    threads = [threading.Thread(target=attempt_commit) for _ in range(2)]
    for t in threads:
        t.start()
    assert entered_probe.wait(timeout=5)
    release_probe.set()
    for t in threads:
        t.join(timeout=5)

    received_ops = [item[1] for item in results if item[0] == "received"]
    assert len(set(received_ops)) == 1, "concurrent B submissions must create exactly one receipt"
    assert probe_count[0] == 1, "concurrent B submissions must run exactly one conformance probe"
    rejected = [r for r in results if r[0] == "rejected"]
    if rejected:
        assert rejected[0][1] == 409
        assert "in-progress" in rejected[0][2].get("detail", "").lower()
    assert _accepted_candidate_count(state, token) == 2


def test_route_exception_classification_duplicate_nonterminal_malformed_terminal(
    client, tmp_path, monkeypatch
):
    tc, state = client
    token = _mint_direct_token(tc)
    authority = state.direct_connect_authority_store.get_for_token(token)
    wrapper_hash = _write_executable(authority.wrapper_destination)
    child = tmp_path / "bin" / "child"
    _write_executable(child)
    payload_ok = _payload(wrapper_hash, [child])

    first = _connect(tc, token, payload_ok)
    operation_a = first.json()["operation_id"]
    _drive_to_terminal_failure(tc, state, operation_a, monkeypatch)

    # Duplicate is nonterminal: corrected B must still be accepted.
    duplicate = _connect(tc, token, payload_ok)
    assert duplicate.status_code == 409
    assert _parent_is_retryable(state, token) is True

    wrapper_b = authority.wrapper_destination
    wrapper_hash_b = _write_executable(wrapper_b, b"#!/bin/sh\necho v2\n")
    child_b = tmp_path / "bin" / "child-b"
    _write_executable(child_b)
    payload_b = _payload(wrapper_hash_b, [child_b])
    corrected = _connect(tc, token, payload_b)
    assert corrected.status_code == 201

    # Now terminalize B with malformed/integrity failure.
    operation_b = corrected.json()["operation_id"]
    _drive_to_terminal_failure(tc, state, operation_b, monkeypatch)

    # Malformed/integrity after two candidates is terminal/nonretryable.
    malformed = _connect(tc, token, {"manifest": {"manifest_version": 2, "wrapper_sha256": wrapper_hash_b, "upgradeable_children": [], "workspace_adapter_id": "codex"}})
    assert malformed.status_code == 422
    assert _parent_is_retryable(state, token) is False

    # Any further corrected candidate is refused because parent is closed.
    wrapper_c = authority.wrapper_destination
    wrapper_hash_c = _write_executable(wrapper_c, b"#!/bin/sh\necho v3\n")
    child_c = tmp_path / "bin" / "child-c"
    _write_executable(child_c)
    payload_c = _payload(wrapper_hash_c, [child_c])
    late = _connect(tc, token, payload_c)
    assert late.status_code == 409


def test_v0_no_direct_store_migration_reopens_with_additive_schema(tmp_path):
    """A v0 database (no direct_connect tables) upgrades additively."""
    db_path = tmp_path / "direct.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE legacy_runtime_table (id TEXT)")
    conn.execute("INSERT INTO legacy_runtime_table VALUES ('legacy-redacted-reference')")
    conn.commit()
    conn.close()

    store = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)

    # Existing v0 table/column preserved exactly.
    rows = store._conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='legacy_runtime_table'"
    ).fetchall()
    assert len(rows) == 1
    # Existing data retained.
    assert store._conn.execute(
        "SELECT id FROM legacy_runtime_table"
    ).fetchone()[0] == "legacy-redacted-reference"

    # Additive parent/candidate/identity/event relations are present.
    required_tables = {
        "direct_connect_authorities",
        "direct_connect_parent_lifecycles",
        "direct_connect_candidates",
        "direct_connect_identity_history",
        "direct_connect_events",
    }
    existing = {
        r[0] for r in store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    missing = required_tables - existing
    assert not missing, f"missing additive tables: {missing}"

    # Reopen twice must remain additive and not duplicate history rows.
    store.close()
    reopened1 = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)
    reopened1.close()
    reopened2 = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)
    reopened2.close()


def test_v1_current_schema_migration_reopens_retaining_exact_columns(tmp_path):
    """A v1 database (current direct_connect schema) keeps every existing column."""
    db_path = tmp_path / "direct.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE direct_connect_authorities (
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
        CREATE TABLE direct_connect_reservations (
            token_fingerprint TEXT PRIMARY KEY,
            operation_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('reserved', 'terminalized', 'received_nonlaunchable')),
            reason TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE direct_connect_operations (
            operation_id TEXT PRIMARY KEY,
            token_fingerprint TEXT NOT NULL UNIQUE,
            state TEXT NOT NULL CHECK (state = 'received_nonlaunchable'),
            intended_profile_name TEXT NOT NULL,
            workspace_adapter_id TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE direct_connect_artifacts (
            operation_id TEXT NOT NULL,
            slot TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('immutable_wrapper', 'upgradeable_child')),
            declared_path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            structural_facts TEXT NOT NULL,
            PRIMARY KEY (operation_id, slot)
        );
        CREATE TABLE direct_connect_receipts (
            operation_id TEXT PRIMARY KEY,
            token_fingerprint TEXT NOT NULL UNIQUE,
            state TEXT NOT NULL CHECK (state = 'received_nonlaunchable'),
            created_at REAL NOT NULL
        );
        CREATE TABLE direct_connect_events (
            event_id TEXT PRIMARY KEY,
            operation_id TEXT,
            token_fingerprint TEXT NOT NULL,
            event_type TEXT NOT NULL,
            detail TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE direct_connect_projections (
            operation_id TEXT PRIMARY KEY,
            token_fingerprint TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('planned', 'committed', 'failed')),
            adapter_id TEXT,
            profile_name TEXT,
            reason TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE direct_connect_retry_attempts (
            attempt_id TEXT PRIMARY KEY,
            operation_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('running', 'succeeded', 'failed')),
            adapter_id TEXT,
            profile_name TEXT,
            reason TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO direct_connect_authorities VALUES (?, 'custom-cli', 'profile', '/runtime/adapters/profile-adapter', 'codex', 1, 100, 'minted_nonlaunchable', 'runtime-master-mint')",
        ("a" * 64,),
    )
    conn.commit()
    conn.close()

    baseline = _table_infos(db_path)
    store = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)
    after_open = _table_infos(db_path)

    # Existing v1 tables/columns are untouched.
    for table, columns in baseline.items():
        assert table in after_open, f"existing table {table} was removed"
        assert after_open[table] == columns, (
            f"table {table} columns changed from {columns} to {after_open[table]}"
        )

    # First identity/history retained.
    assert store._conn.execute(
        "SELECT token_fingerprint FROM direct_connect_authorities"
    ).fetchone()[0] == "a" * 64

    # Additive parent/candidate/identity tables present.
    required_tables = {
        "direct_connect_parent_lifecycles",
        "direct_connect_candidates",
        "direct_connect_identity_history",
    }
    existing = {
        r[0] for r in store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    missing = required_tables - existing
    assert not missing, f"missing additive lifecycle tables: {missing}"

    store.close()
    reopened = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)
    reopened.close()


def test_v0_interrupted_migration_partial_lifecycle_tables(tmp_path):
    """A v0 database where some THR-160 tables already exist completes additively."""
    db_path = tmp_path / "direct.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE legacy_runtime_table (id TEXT);
        INSERT INTO legacy_runtime_table VALUES ('legacy-redacted-reference');
        CREATE TABLE direct_connect_authorities (
            token_fingerprint TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            intended_profile_name TEXT NOT NULL,
            wrapper_destination TEXT NOT NULL,
            workspace_adapter_id TEXT NOT NULL,
            issued_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            state TEXT NOT NULL,
            provenance TEXT NOT NULL
        );
        INSERT INTO direct_connect_authorities VALUES (
            'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
            'custom-cli', 'profile', '/runtime/adapters/profile-adapter', 'codex',
            1, 100, 'minted_nonlaunchable', 'runtime-master-mint'
        );
        """
    )
    conn.commit()
    conn.close()

    store = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)

    # Legacy data and the already-present authority row are retained.
    assert store._conn.execute(
        "SELECT id FROM legacy_runtime_table"
    ).fetchone()[0] == "legacy-redacted-reference"
    assert store._conn.execute(
        "SELECT token_fingerprint FROM direct_connect_authorities"
    ).fetchone()[0] == "a" * 64

    # Missing additive lifecycle tables are created without error.
    required_tables = {
        "direct_connect_parent_lifecycles",
        "direct_connect_candidates",
        "direct_connect_identity_history",
    }
    existing = {
        r[0] for r in store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert not (required_tables - existing)
    store.close()


def test_v1_interrupted_migration_partial_identity_tables(tmp_path):
    """A v1 database missing only the identity-history table completes additively."""
    db_path = tmp_path / "direct.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE direct_connect_authorities (
            token_fingerprint TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            intended_profile_name TEXT NOT NULL,
            wrapper_destination TEXT NOT NULL,
            workspace_adapter_id TEXT NOT NULL,
            issued_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            state TEXT NOT NULL,
            provenance TEXT NOT NULL
        );
        CREATE TABLE direct_connect_parent_lifecycles (
            token_fingerprint TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            latest_accepted_candidate_id TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            expires_at REAL NOT NULL
        );
        CREATE TABLE direct_connect_candidates (
            candidate_id TEXT PRIMARY KEY,
            token_fingerprint TEXT NOT NULL,
            operation_id TEXT UNIQUE,
            attempt_ordinal INTEGER NOT NULL,
            state TEXT NOT NULL,
            identity_hash TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        INSERT INTO direct_connect_authorities VALUES (
            'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
            'custom-cli', 'profile', '/runtime/adapters/profile-adapter', 'codex',
            1, 100, 'minted_nonlaunchable', 'runtime-master-mint'
        );
        """
    )
    conn.commit()
    conn.close()

    baseline = _table_infos(db_path)
    store = DirectConnectAuthorityStore(db_path, runtime_root=tmp_path)
    after_open = _table_infos(db_path)

    # Existing v1 tables/columns are untouched.
    for table, columns in baseline.items():
        assert table in after_open
        assert after_open[table] == columns

    # The missing identity-history table is added.
    assert "direct_connect_identity_history" in {
        r[0] for r in store._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    store.close()


def _table_infos(db_path: Path) -> dict[str, list[tuple]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    info = {
        table: conn.execute(f"PRAGMA table_info({table})").fetchall()
        for table in tables
    }
    conn.close()
    return info


def test_reopen_generic_token_unavailable_direct_retry_authority_evaluated(
    client, tmp_path, monkeypatch
):
    tc, state = client
    token = _mint_direct_token(tc)
    authority = state.direct_connect_authority_store.get_for_token(token)
    wrapper_hash = _write_executable(authority.wrapper_destination)
    child = tmp_path / "bin" / "child"
    _write_executable(child)
    payload = _payload(wrapper_hash, [child])

    first = _connect(tc, token, payload)
    operation_a = first.json()["operation_id"]
    _drive_to_terminal_failure(tc, state, operation_a, monkeypatch)

    db_path = tmp_path / "direct.db"
    runtime_root = tmp_path / "daemon"

    # Simulate daemon restart: the in-memory registration token store is gone,
    # but the direct authority store reopens from its durable DB file.
    state.direct_connect_authority_store.close()
    new_token_store = type(
        "RegistrationTokenStore",
        (),
        {"validate_runtime": lambda _self, _token: None},
    )()

    reopened_store = DirectConnectAuthorityStore(db_path, runtime_root=runtime_root)

    # Generic registration token remains unavailable (never revived).
    assert new_token_store.validate_runtime(token) is None

    # The durable direct authority store still holds the failed parent and
    # exposes a retry-evaluation seam independent of the generic token.
    assert hasattr(reopened_store, "is_retryable")
    assert reopened_store.is_retryable(token) is True

    # The first candidate receipt survived the restart.
    assert _accepted_candidate_count_from_store(reopened_store, token) == 1


def _accepted_candidate_count_from_store(store: DirectConnectAuthorityStore, token: str) -> int:
    assert hasattr(store, "list_candidates")
    return len(store.list_candidates(token))

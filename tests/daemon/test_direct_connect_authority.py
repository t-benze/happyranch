"""THR-107 Slice 1A nonlaunchable direct-mint authority tests."""
from __future__ import annotations

import threading

import sqlite3

import pytest


@pytest.fixture(autouse=True)
def runtime_mint_is_loopback_for_testclient(monkeypatch) -> None:
    from runtime.daemon.routes import auth as auth_route

    monkeypatch.setattr(
        auth_route, "_LOCAL_HOSTS", auth_route._LOCAL_HOSTS | {"testclient"}
    )


def test_fingerprint_is_domain_separated_and_one_way() -> None:
    from runtime.daemon.direct_connect_store import fingerprint_registration_token

    raw = "hrreg_not-a-real-token"
    fingerprint = fingerprint_registration_token(raw)

    assert len(fingerprint) == 64
    assert fingerprint != raw
    assert raw not in fingerprint
    assert fingerprint != fingerprint_registration_token("other")


def test_store_reopens_additively_without_reading_legacy_rows(tmp_path) -> None:
    from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore

    path = tmp_path / "direct_connect_authority.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE legacy_direct_drafts (token TEXT, org TEXT)")
    conn.execute("INSERT INTO legacy_direct_drafts VALUES (?, ?)", ("legacy-redacted-reference", "alpha"))
    conn.commit()
    conn.close()

    store = DirectConnectAuthorityStore(path)
    authority = store.mint_authority(
        token_plaintext="hrreg_new",
        name="custom-cli",
        intended_profile_name="custom-profile",
        workspace_adapter_id="codex",
        issued_at=10.0,
        expires_at=20.0,
    )
    store.close()

    reopened = DirectConnectAuthorityStore(path)
    assert reopened.get(authority.token_fingerprint) == authority
    legacy = sqlite3.connect(path).execute("SELECT token, org FROM legacy_direct_drafts").fetchone()
    assert legacy == ("legacy-redacted-reference", "alpha")


def test_store_never_persists_raw_or_reversible_token(tmp_path) -> None:
    from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore

    path = tmp_path / "direct_connect_authority.db"
    raw = "hrreg_secret_that_must_not_persist"
    store = DirectConnectAuthorityStore(path)
    authority = store.mint_authority(
        token_plaintext=raw,
        name="custom-cli",
        intended_profile_name="custom-profile",
        workspace_adapter_id="pi",
        issued_at=10.0,
        expires_at=20.0,
    )
    assert raw not in repr(authority)
    store.close()
    assert raw.encode() not in path.read_bytes()


def test_readback_failure_rolls_back_authority_row(tmp_path, monkeypatch) -> None:
    from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore

    store = DirectConnectAuthorityStore(tmp_path / "direct_connect_authority.db")
    monkeypatch.setattr(store, "_read_authority", lambda _cursor, _fingerprint: None)

    with pytest.raises(RuntimeError, match="readback"):
        store.mint_authority(
            token_plaintext="hrreg_never_persisted",
            name="custom-cli",
            intended_profile_name="custom-profile",
            workspace_adapter_id="claude",
            issued_at=10.0,
            expires_at=20.0,
        )

    assert store.count() == 0


@pytest.mark.parametrize(
    "workspace_adapter_id",
    ["", " ", "arbitrary", "claude-code", "codex ", "generic-cli", "Claude"],
)
def test_runtime_mint_rejects_invalid_workspace_adapter_without_authority_write(
    client, daemon_state, workspace_adapter_id
) -> None:
    response = client.post(
        "/api/v1/auth/registration-token/runtime",
        json={
            "name": "custom-cli",
            "purpose": "adapter",
            "intended_profile_name": "custom-profile",
            "workspace_adapter_id": workspace_adapter_id,
        },
    )

    assert response.status_code == 422
    assert daemon_state.direct_connect_authority_store.count() == 0


def test_runtime_mint_rejects_workspace_adapter_for_non_adapter_purpose(
    client, daemon_state
) -> None:
    response = client.post(
        "/api/v1/auth/registration-token/runtime",
        json={"name": "custom-cli", "purpose": "profile", "workspace_adapter_id": "pi"},
    )

    assert response.status_code == 422
    assert daemon_state.direct_connect_authority_store.count() == 0


def test_legacy_adapter_mint_without_workspace_adapter_preserves_no_direct_authority(
    client, daemon_state
) -> None:
    response = client.post(
        "/api/v1/auth/registration-token/runtime",
        json={
            "name": "custom-cli",
            "purpose": "adapter",
            "intended_profile_name": "custom-profile",
        },
    )

    assert response.status_code == 200
    assert daemon_state.registration_token_store.validate_runtime(response.json()["token"])
    assert daemon_state.direct_connect_authority_store.count() == 0


def test_direct_adapter_mint_persists_one_nonlaunchable_server_owned_authority(
    client, daemon_state
) -> None:
    response = client.post(
        "/api/v1/auth/registration-token/runtime",
        json={
            "name": "custom-cli",
            "purpose": "adapter",
            "intended_profile_name": "custom-profile",
            "workspace_adapter_id": "codex",
        },
    )

    assert response.status_code == 200
    token = response.json()["token"]
    authority = daemon_state.direct_connect_authority_store.get_for_token(token)
    assert authority is not None
    assert authority.workspace_adapter_id == "codex"
    assert authority.state == "minted_nonlaunchable"
    assert authority.intended_profile_name == "custom-profile"
    assert authority.wrapper_destination.is_absolute()
    assert token not in repr(authority)
    assert daemon_state.direct_connect_authority_store.count() == 1


def test_direct_mint_store_failure_returns_no_token_and_no_authority(client, daemon_state, monkeypatch) -> None:
    store = daemon_state.direct_connect_authority_store
    monkeypatch.setattr(store, "mint_authority", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("write failed")))

    response = client.post(
        "/api/v1/auth/registration-token/runtime",
        json={
            "name": "custom-cli",
            "purpose": "adapter",
            "intended_profile_name": "custom-profile",
            "workspace_adapter_id": "pi",
        },
    )

    assert response.status_code == 500
    assert daemon_state.direct_connect_authority_store.count() == 0
    assert not daemon_state.registration_token_store._tokens


def test_direct_mint_readback_failure_returns_no_token_and_no_authority(
    client, daemon_state, monkeypatch
) -> None:
    store = daemon_state.direct_connect_authority_store
    monkeypatch.setattr(store, "_read_authority", lambda _cursor, _fingerprint: None)

    response = client.post(
        "/api/v1/auth/registration-token/runtime",
        json={
            "name": "custom-cli",
            "purpose": "adapter",
            "intended_profile_name": "custom-profile",
            "workspace_adapter_id": "pi",
        },
    )

    assert response.status_code == 500
    assert store.count() == 0
    assert not daemon_state.registration_token_store._tokens


def test_direct_mint_name_collision_has_deterministic_server_destination(client, daemon_state) -> None:
    payload = {
        "name": "custom-cli",
        "purpose": "adapter",
        "intended_profile_name": "custom-profile",
        "workspace_adapter_id": "pi",
    }
    first = client.post("/api/v1/auth/registration-token/runtime", json=payload)
    second = client.post("/api/v1/auth/registration-token/runtime", json=payload)

    assert first.status_code == second.status_code == 200
    first_authority = daemon_state.direct_connect_authority_store.get_for_token(first.json()["token"])
    second_authority = daemon_state.direct_connect_authority_store.get_for_token(second.json()["token"])
    assert first_authority is not None and second_authority is not None
    assert first_authority.wrapper_destination == second_authority.wrapper_destination
    assert first_authority.token_fingerprint != second_authority.token_fingerprint


def test_direct_mint_target_is_the_public_canonical_adapter_path(client, daemon_state) -> None:
    response = client.post(
        "/api/v1/auth/registration-token/runtime",
        json={
            "name": "custom-cli", "purpose": "adapter",
            "intended_profile_name": "Custom Profile", "workspace_adapter_id": "codex",
        },
    )
    authority = daemon_state.direct_connect_authority_store.get_for_token(response.json()["token"])
    assert authority.wrapper_destination == (
        daemon_state.runtime.root / "adapters" / "custom-profile-adapter"
    )


def test_runtime_mint_openapi_exposes_optional_direct_workspace_adapter() -> None:
    from runtime.config import Settings
    from runtime.daemon.app import create_app
    from runtime.daemon.state import DaemonState

    schemas = create_app(DaemonState.idle(Settings())).openapi()["components"]["schemas"]
    schema = schemas["RuntimeRegistrationTokenMintRequest"]
    workspace_adapter = schema["properties"]["workspace_adapter_id"]
    allowed_adapter_schema = next(
        variant for variant in workspace_adapter["anyOf"] if "enum" in variant
    )
    assert allowed_adapter_schema["enum"] == ["claude", "codex", "opencode", "pi"]
    assert {variant["type"] for variant in workspace_adapter["anyOf"]} == {"string", "null"}
    assert "workspace_adapter_id" not in schema.get("required", [])


def test_authority_store_has_no_org_input_or_yaml_projection(tmp_path) -> None:
    """Changing loaded-org order cannot influence this daemon-global record."""
    from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore

    first = DirectConnectAuthorityStore(tmp_path / "first.db", runtime_root=tmp_path)
    second = DirectConnectAuthorityStore(tmp_path / "second.db", runtime_root=tmp_path)
    alpha = first.mint_authority(
        token_plaintext="hrreg_alpha", name="custom-cli", intended_profile_name="same-profile",
        workspace_adapter_id="opencode", issued_at=1, expires_at=2,
    )
    beta = second.mint_authority(
        token_plaintext="hrreg_beta", name="custom-cli", intended_profile_name="same-profile",
        workspace_adapter_id="opencode", issued_at=1, expires_at=2,
    )
    assert alpha.wrapper_destination == beta.wrapper_destination
    assert alpha.provenance == beta.provenance == "runtime-master-mint"


def test_plan_projection_is_idempotent_per_operation(tmp_path) -> None:
    from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore

    store = DirectConnectAuthorityStore(tmp_path / "direct.db", runtime_root=tmp_path)
    store.mint_authority(
        token_plaintext="hrreg_plan", name="custom-cli", intended_profile_name="profile",
        workspace_adapter_id="codex", issued_at=1, expires_at=100,
    )
    operation_id = store.reserve("hrreg_plan", now=2)
    store.receive(
        "hrreg_plan", operation_id, wrapper_sha256="a" * 64,
        wrapper_facts={}, children=[], workspace_adapter_id="codex", now=2,
    )

    assert store.plan_projection(operation_id, now=3) is True
    assert store.plan_projection(operation_id, now=4) is False  # already planned
    projection = store.get_projection(operation_id)
    assert projection.state == "planned"


def test_list_operations_pending_projection_excludes_projected_operations_in_fifo_order(tmp_path) -> None:
    from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore

    store = DirectConnectAuthorityStore(tmp_path / "direct.db", runtime_root=tmp_path)

    def receive(token: str, created_at: float) -> str:
        store.mint_authority(
            token_plaintext=token, name="custom-cli", intended_profile_name=token,
            workspace_adapter_id="codex", issued_at=1, expires_at=100,
        )
        operation_id = store.reserve(token, now=created_at)
        store.receive(
            token, operation_id, wrapper_sha256="a" * 64, wrapper_facts={},
            children=[], workspace_adapter_id="codex", now=created_at,
        )
        return operation_id

    oldest = receive("hrreg_oldest", 2)
    planned = receive("hrreg_planned", 3)
    committed = receive("hrreg_committed", 4)
    failed = receive("hrreg_failed", 5)
    newest = receive("hrreg_newest", 6)

    assert store.plan_projection(planned, now=7)
    assert store.plan_projection(committed, now=7)
    assert store.mark_committed(committed, adapter_id="committed-adapter", profile_name="committed", now=8)
    assert store.plan_projection(failed, now=7)
    assert store.mark_failed(failed, "probe failed", now=8)

    assert store.list_operations_pending_projection() == [oldest, newest]


def test_mark_committed_requires_planned_state(tmp_path) -> None:
    from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore

    store = DirectConnectAuthorityStore(tmp_path / "direct.db", runtime_root=tmp_path)
    assert store.mark_committed("unknown-op", adapter_id="a", profile_name="p") is False

    store.mint_authority(
        token_plaintext="hrreg_commit", name="custom-cli", intended_profile_name="profile",
        workspace_adapter_id="codex", issued_at=1, expires_at=100,
    )
    operation_id = store.reserve("hrreg_commit", now=2)
    store.receive("hrreg_commit", operation_id, wrapper_sha256="b" * 64, wrapper_facts={}, children=[], workspace_adapter_id="codex", now=2)
    store.plan_projection(operation_id, now=3)

    assert store.mark_committed(operation_id, adapter_id="custom-cli-adapter", profile_name="profile", now=4) is True
    projection = store.get_projection(operation_id)
    assert projection.state == "committed"
    assert projection.adapter_id == "custom-cli-adapter"
    # Retrying commit on an already-committed row is a no-op, not an error
    assert store.mark_committed(operation_id, adapter_id="custom-cli-adapter", profile_name="profile", now=5) is False


def test_mark_failed_from_planned_and_reopen_durability(tmp_path) -> None:
    from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore

    path = tmp_path / "direct.db"
    store = DirectConnectAuthorityStore(path, runtime_root=tmp_path)
    store.mint_authority(
        token_plaintext="hrreg_fail", name="custom-cli", intended_profile_name="profile",
        workspace_adapter_id="codex", issued_at=1, expires_at=100,
    )
    operation_id = store.reserve("hrreg_fail", now=2)
    store.receive("hrreg_fail", operation_id, wrapper_sha256="c" * 64, wrapper_facts={}, children=[], workspace_adapter_id="codex", now=2)
    store.plan_projection(operation_id, now=3)
    assert store.mark_failed(operation_id, "conformance_probe_failed", now=4) is True
    store.close()

    reopened = DirectConnectAuthorityStore(path, runtime_root=tmp_path)
    projection = reopened.get_projection(operation_id)
    assert projection.state == "failed"
    assert projection.reason == "conformance_probe_failed"


def _seed_projected_operation(store, *, token: str, state: str) -> str:
    """Use the public mint/receive/projection seam, never raw SQL fixtures."""
    store.mint_authority(
        token_plaintext=token, name="custom-cli", intended_profile_name="forget-profile",
        workspace_adapter_id="codex", issued_at=1, expires_at=100,
    )
    operation_id = store.reserve(token, now=2)
    assert operation_id is not None
    store.receive(
        token, operation_id, wrapper_sha256="f" * 64, wrapper_facts={}, children=[],
        workspace_adapter_id="codex", now=2,
    )
    assert store.plan_projection(operation_id, now=3)
    if state == "failed":
        assert store.mark_failed(operation_id, "probe failed", now=4)
    elif state == "committed":
        assert store.mark_committed(
            operation_id, adapter_id="forget-adapter", profile_name="forget-profile", now=4,
        )
    return operation_id


def test_forget_failed_operation_removes_its_authority_records_and_appends_audit_event(tmp_path) -> None:
    from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore

    store = DirectConnectAuthorityStore(tmp_path / "direct.db", runtime_root=tmp_path)
    operation_id = _seed_projected_operation(store, token="hrreg_forget_failed", state="failed")
    event_count = store._conn.execute("SELECT COUNT(*) FROM direct_connect_events").fetchone()[0]

    assert store.forget_operation(operation_id) == "forget-profile"

    for table in (
        "direct_connect_artifacts", "direct_connect_receipts", "direct_connect_operations",
        "direct_connect_projections", "direct_connect_reservations", "direct_connect_authorities",
    ):
        assert store._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    events = store._conn.execute(
        "SELECT event_type, detail FROM direct_connect_events ORDER BY created_at, rowid"
    ).fetchall()
    assert len(events) == event_count + 1
    assert tuple(events[-1]) == ("forgotten", "terminal failed operation removed")


def test_forget_refuses_failed_projection_with_successful_retry(tmp_path) -> None:
    from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore

    store = DirectConnectAuthorityStore(tmp_path / "direct.db", runtime_root=tmp_path)
    operation_id = _seed_projected_operation(store, token="hrreg_retry_connected", state="failed")
    attempt, claimed = store.claim_retry_attempt(operation_id, now=5)
    assert claimed
    assert store.finish_retry_attempt(
        attempt.attempt_id, state="succeeded", adapter_id="forget-adapter",
        profile_name="forget-profile", now=6,
    )

    assert store.forget_operation(operation_id) is None
    assert store.get_projection(operation_id).state == "failed"
    assert store.get_successful_retry(operation_id) is not None


@pytest.mark.parametrize("state", ["planned", "committed"])
def test_forget_refuses_nonfailed_projection_without_mutating_rows(tmp_path, state) -> None:
    from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore

    store = DirectConnectAuthorityStore(tmp_path / "direct.db", runtime_root=tmp_path)
    operation_id = _seed_projected_operation(store, token=f"hrreg_forget_{state}", state=state)
    before = {
        table: store._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in (
            "direct_connect_artifacts", "direct_connect_receipts", "direct_connect_operations",
            "direct_connect_projections", "direct_connect_reservations", "direct_connect_authorities",
            "direct_connect_events",
        )
    }

    assert store.forget_operation(operation_id) is None

    after = {
        table: store._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in before
    }
    assert after == before


def test_forget_refuses_unknown_operation_without_mutation(tmp_path) -> None:
    from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore

    store = DirectConnectAuthorityStore(tmp_path / "direct.db", runtime_root=tmp_path)
    _seed_projected_operation(store, token="hrreg_forget_known", state="planned")
    before = {
        table: store._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in (
            "direct_connect_artifacts", "direct_connect_receipts", "direct_connect_operations",
            "direct_connect_projections", "direct_connect_reservations", "direct_connect_authorities",
            "direct_connect_events",
        )
    }

    assert store.forget_operation("missing-operation") is None
    after = {
        table: store._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in before
    }
    assert after == before


def test_get_receipt_artifacts_returns_wrapper_and_children(tmp_path) -> None:
    from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore

    store = DirectConnectAuthorityStore(tmp_path / "direct.db", runtime_root=tmp_path)
    # The mint-time workspace_adapter_id is an unrelated activation trigger —
    # only the value the CLI declares at receive() time should end up on the
    # receipt artifacts, proving the founder's mint choice never wins.
    store.mint_authority(
        token_plaintext="hrreg_art", name="custom-cli", intended_profile_name="profile",
        workspace_adapter_id="claude", issued_at=1, expires_at=100,
    )
    operation_id = store.reserve("hrreg_art", now=2)
    store.receive(
        "hrreg_art", operation_id, wrapper_sha256="d" * 64, wrapper_facts={"mode": 493},
        children=[{"slot": "cli", "path": "/abs/child", "sha256": "e" * 64, "facts": {"version_probe_argv": ["/abs/child", "--version"]}}],
        workspace_adapter_id="pi", now=2,
    )

    artifacts = store.get_receipt_artifacts(operation_id)
    assert artifacts.wrapper_path.name  # non-empty Path
    assert artifacts.wrapper_sha256 == "d" * 64
    assert artifacts.children == [{"slot": "cli", "executable": "/abs/child", "sha256": "e" * 64}]
    assert artifacts.intended_profile_name == "profile"
    assert artifacts.workspace_adapter_id == "pi"


def test_get_latest_operation_for_profile_returns_none_before_receipt(tmp_path) -> None:
    from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore

    store = DirectConnectAuthorityStore(tmp_path / "direct.db", runtime_root=tmp_path)
    store.mint_authority(
        token_plaintext="hrreg_status", name="custom-cli", intended_profile_name="status-profile",
        workspace_adapter_id="codex", issued_at=1, expires_at=100,
    )

    assert store.get_latest_operation_for_profile("status-profile") is None
    assert store.get_latest_operation_for_profile("no-such-profile") is None


def test_get_latest_operation_for_profile_returns_most_recent_operation(tmp_path) -> None:
    from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore

    store = DirectConnectAuthorityStore(tmp_path / "direct.db", runtime_root=tmp_path)
    store.mint_authority(
        token_plaintext="hrreg_status_a", name="custom-cli", intended_profile_name="status-profile",
        workspace_adapter_id="codex", issued_at=1, expires_at=100,
    )
    op_a = store.reserve("hrreg_status_a", now=2)
    store.receive("hrreg_status_a", op_a, wrapper_sha256="a" * 64, wrapper_facts={}, children=[], workspace_adapter_id="codex", now=2)

    assert store.get_latest_operation_for_profile("status-profile") == op_a

    # A second mint + receive for the same profile name (regenerate-prompt case)
    # must surface as the newer operation.
    store.mint_authority(
        token_plaintext="hrreg_status_b", name="custom-cli", intended_profile_name="status-profile",
        workspace_adapter_id="codex", issued_at=3, expires_at=200,
    )
    op_b = store.reserve("hrreg_status_b", now=4)
    store.receive("hrreg_status_b", op_b, wrapper_sha256="b" * 64, wrapper_facts={}, children=[], workspace_adapter_id="codex", now=4)

    assert store.get_latest_operation_for_profile("status-profile") == op_b


def test_direct_authority_reservation_has_one_concurrent_winner(tmp_path) -> None:
    from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore

    store = DirectConnectAuthorityStore(tmp_path / "direct.db", runtime_root=tmp_path)
    store.mint_authority(
        token_plaintext="hrreg_concurrent", name="custom-cli", intended_profile_name="profile",
        workspace_adapter_id="codex", issued_at=1, expires_at=100,
    )
    barrier = threading.Barrier(2)
    results: list[str | None] = []

    def reserve() -> None:
        barrier.wait()
        results.append(store.reserve("hrreg_concurrent", now=2))

    first = threading.Thread(target=reserve)
    second = threading.Thread(target=reserve)
    first.start()
    second.start()
    first.join()
    second.join()

    assert sum(result is not None for result in results) == 1
    assert store.reserve("hrreg_concurrent", now=2) is None

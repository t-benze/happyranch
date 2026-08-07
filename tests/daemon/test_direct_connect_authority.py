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

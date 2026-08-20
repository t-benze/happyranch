"""THR-107 Slice 1: direct-connect projection coordinator tests."""
from __future__ import annotations

import hashlib
import json
import textwrap

import pytest


def _mint_and_receive(
    store, tmp_path, *, token="hrreg_proj", profile_name="custom-profile", adapter="codex",
    wrapper_body: bytes = b"#!/bin/sh\ncat\n",
):
    store.mint_authority(
        token_plaintext=token, name="custom-cli", intended_profile_name=profile_name,
        workspace_adapter_id=adapter, issued_at=1, expires_at=1000,
    )
    authority = store.get_for_token(token)
    wrapper = authority.wrapper_destination
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_bytes(wrapper_body)
    wrapper.chmod(0o700)
    wrapper_hash = hashlib.sha256(wrapper.read_bytes()).hexdigest()
    child = tmp_path / "bin" / "child"
    child.parent.mkdir(parents=True, exist_ok=True)
    child.write_bytes(b"#!/bin/sh\nexit 0\n")
    child.chmod(0o700)
    child_hash = hashlib.sha256(child.read_bytes()).hexdigest()
    operation_id = store.reserve(token, now=2)
    store.receive(
        token, operation_id, wrapper_sha256=wrapper_hash, wrapper_facts={},
        children=[{"slot": "cli", "path": str(child), "sha256": child_hash, "facts": {}}],
        workspace_adapter_id=adapter, now=2,
    )
    return operation_id, wrapper


def _behavioral_wrapper(mode: str = "success") -> bytes:
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json
        import sys

        request = json.load(sys.stdin)
        if {mode!r} == "empty":
            sys.exit(0)
        if {mode!r} == "malformed":
            sys.stdout.write("not json")
            sys.exit(0)
        canary = next(word for word in request["prompt"].split() if word.startswith("direct-connect-canary:"))
        payload = {{
            "success": {mode!r} != "provider_error",
            "duration_seconds": 0,
            "session_id": request["invocation"]["invocation_id"],
            "returncode": 0,
            "stdout_tail": "provider stdout secret",
            "stderr_tail": "provider stderr secret",
            "result": {{
                "text": (
                    canary
                    if {mode!r} in ("success", "null_agent_session")
                    else "direct-connect-canary:wrong" if {mode!r} == "wrong_canary"
                    else "prompt swallowed"
                )
            }},
            "error": "provider error secret" if {mode!r} == "provider_error" else None,
            "agent_session_id": None if {mode!r} == "null_agent_session" else "provider-session",
            "adapter_metadata": {{
                "adapter": request["executor_context"]["provider"],
                "adapter_version": "1.2.3",
                "contract_version": 1,
            }},
        }}
        sys.stdout.write(json.dumps(payload))
    """).encode()


def _fake_probe_output(adapter_id: str):
    from runtime.orchestrator.adapter_contract import AdapterOutput

    payload = {
        "success": True, "duration_seconds": 0, "session_id": "probe-sess-00000000-0000-0000-0000-000000000000",
        "returncode": 0, "stdout_tail": "", "stderr_tail": "",
        "adapter_metadata": {"adapter": adapter_id, "adapter_version": "1.2.3", "contract_version": 1},
    }
    return AdapterOutput.model_validate(payload)


def _receive_identity_candidate(
    store, tmp_path, *, token: str, operation_id: str,
    wrapper_body: bytes = b"#!/bin/sh\ncat\n", child_name: str = "child",
    identity_hash: str, identity_blob: str, now: float = 2,
) -> None:
    authority = store.get_for_token(token)
    wrapper = authority.wrapper_destination
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_bytes(wrapper_body)
    wrapper.chmod(0o700)
    wrapper_hash = hashlib.sha256(wrapper_body).hexdigest()
    child = tmp_path / "bin" / child_name
    child.parent.mkdir(parents=True, exist_ok=True)
    child.write_bytes(b"#!/bin/sh\nexit 0\n")
    child.chmod(0o700)
    child_hash = hashlib.sha256(child.read_bytes()).hexdigest()
    store.receive(
        token, operation_id, wrapper_sha256=wrapper_hash, wrapper_facts={},
        children=[{"slot": "cli", "path": str(child), "sha256": child_hash, "facts": {}}],
        workspace_adapter_id="codex",
        identity_hash=identity_hash, identity_blob=identity_blob, now=now,
    )


def _mint_and_receive_identity(
    store, tmp_path, *, token: str, profile_name: str,
    wrapper_body: bytes = b"#!/bin/sh\ncat\n",
    identity_hash: str, identity_blob: str,
) -> str:
    store.mint_authority(
        token_plaintext=token, name="custom-cli", intended_profile_name=profile_name,
        workspace_adapter_id="codex", issued_at=1, expires_at=1000,
    )
    operation_id = store.reserve(
        token, identity_hash=identity_hash, identity_blob=identity_blob, now=2,
    )
    assert operation_id is not None
    _receive_identity_candidate(
        store, tmp_path, token=token, operation_id=operation_id,
        wrapper_body=wrapper_body, identity_hash=identity_hash, identity_blob=identity_blob, now=2,
    )
    return operation_id


@pytest.fixture
def store(tmp_path, monkeypatch):
    from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore

    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / "daemon"))
    s = DirectConnectAuthorityStore(tmp_path / "direct.db", runtime_root=tmp_path)
    yield s
    s.close()


@pytest.fixture(autouse=True)
def reset_registry():
    from runtime.orchestrator.executor_registry import reset_registry as _reset

    _reset()
    yield
    _reset()


def test_successful_projection_commits_adapter_and_profile(store, tmp_path, monkeypatch):
    from runtime.daemon.direct_connect_projection import project
    from runtime.orchestrator import custom_adapter_registry
    from runtime.orchestrator.executor_registry import get_registry

    operation_id, wrapper = _mint_and_receive(store, tmp_path)
    adapter_id = custom_adapter_registry.generate_adapter_id("custom-profile-adapter")
    monkeypatch.setattr(
        custom_adapter_registry, "run_conformance_probe",
        lambda executable, name, **_kwargs: _fake_probe_output(name),
    )

    outcome = project(store, operation_id)

    assert outcome.state == "committed"
    assert outcome.adapter_id == adapter_id
    assert outcome.profile_name == "custom-profile"
    from runtime.orchestrator.adapter_store import get_adapter
    entry = get_adapter(adapter_id)
    assert entry.status == "approved"
    assert entry.registered_by == entry.approved_by == "direct-connect"
    assert entry.dependency_manifest_version == 1
    assert len(entry.dependencies) == 1
    profile = get_registry().get_profile("custom-profile")
    assert profile is not None
    assert profile.command_adapter_id == f"custom-adapter:{adapter_id}"
    projection = store.get_projection(operation_id)
    assert projection.state == "committed"


def test_projection_runs_real_wrapper_and_requires_terminal_canary_delivery(store, tmp_path):
    from runtime.daemon.direct_connect_projection import project

    operation_id, _ = _mint_and_receive(
        store, tmp_path, wrapper_body=_behavioral_wrapper(),
    )

    outcome = project(store, operation_id)

    assert outcome.state == "committed"
    assert outcome.profile_name == "custom-profile"


def test_projection_accepts_non_resumable_wrapper_without_provider_session_id(store, tmp_path):
    from runtime.daemon.direct_connect_projection import project
    from runtime.orchestrator.adapter_store import load_adapters
    from runtime.orchestrator.executor_registry import get_registry

    operation_id, _ = _mint_and_receive(
        store, tmp_path, wrapper_body=_behavioral_wrapper("null_agent_session"),
    )

    outcome = project(store, operation_id)

    assert outcome.state == "committed"
    assert load_adapters()
    assert get_registry().get_profile("custom-profile") is not None


@pytest.mark.parametrize(
    "mode",
    ["swallowed_prompt", "wrong_canary", "provider_error", "malformed", "empty"],
)
def test_projection_behavioral_conformance_failure_leaves_no_durable_adapter_or_profile(
    store, tmp_path, mode,
):
    from runtime.daemon.direct_connect_projection import project
    from runtime.orchestrator.adapter_store import load_adapters
    from runtime.orchestrator.executor_registry import get_registry

    operation_id, _ = _mint_and_receive(
        store, tmp_path, wrapper_body=_behavioral_wrapper(mode),
    )

    outcome = project(store, operation_id)

    assert outcome.state == "failed"
    assert load_adapters() == {}
    assert get_registry().get_profile("custom-profile") is None
    projection = store.get_projection(operation_id)
    assert projection is not None and projection.state == "failed"
    assert "provider error secret" not in projection.reason
    assert "provider stdout secret" not in projection.reason
    assert "provider stderr secret" not in projection.reason


def test_projection_is_idempotent_on_retry(store, tmp_path, monkeypatch):
    from runtime.daemon.direct_connect_projection import project
    from runtime.orchestrator import custom_adapter_registry

    operation_id, wrapper = _mint_and_receive(store, tmp_path)
    monkeypatch.setattr(
        custom_adapter_registry, "run_conformance_probe",
        lambda executable, name, **_kwargs: _fake_probe_output(name),
    )

    first = project(store, operation_id)
    second = project(store, operation_id)

    assert first.state == second.state == "committed"
    assert first.adapter_id == second.adapter_id


def test_conformance_probe_failure_compensates_with_no_partial_state(store, tmp_path, monkeypatch):
    from runtime.daemon.direct_connect_projection import project
    from runtime.orchestrator import custom_adapter_registry
    from runtime.orchestrator.adapter_store import load_adapters
    from runtime.orchestrator.executor_registry import get_registry

    operation_id, wrapper = _mint_and_receive(store, tmp_path)
    monkeypatch.setattr(
        custom_adapter_registry, "run_conformance_probe",
        lambda executable, name, **_kwargs: (_ for _ in ()).throw(ValueError("probe failed")),
    )

    outcome = project(store, operation_id)

    assert outcome.state == "failed"
    assert load_adapters() == {}
    assert get_registry().get_profile("custom-profile") is None
    projection = store.get_projection(operation_id)
    assert projection.state == "failed"
    assert projection.reason


def test_profile_binding_failure_removes_adapter_entry(store, tmp_path, monkeypatch):
    from runtime.daemon.direct_connect_projection import project
    from runtime.orchestrator import custom_adapter_registry
    from runtime.orchestrator.adapter_store import load_adapters

    operation_id, wrapper = _mint_and_receive(store, tmp_path)
    monkeypatch.setattr(
        custom_adapter_registry, "run_conformance_probe",
        lambda executable, name, **_kwargs: _fake_probe_output(name),
    )
    monkeypatch.setattr(
        custom_adapter_registry, "_perform_adapter_profile_binding",
        lambda **kwargs: (_ for _ in ()).throw(ValueError("binding failed")),
    )

    outcome = project(store, operation_id)

    assert outcome.state == "failed"
    assert load_adapters() == {}


def test_unknown_operation_raises(store):
    from runtime.daemon.direct_connect_projection import project

    with pytest.raises(RuntimeError, match="no receipt"):
        project(store, "does-not-exist")


def test_concurrent_projection_has_one_committer(store, tmp_path, monkeypatch):
    import threading

    from runtime.daemon.direct_connect_projection import project
    from runtime.orchestrator import custom_adapter_registry

    operation_id, wrapper = _mint_and_receive(store, tmp_path)
    monkeypatch.setattr(
        custom_adapter_registry, "run_conformance_probe",
        lambda executable, name, **_kwargs: _fake_probe_output(name),
    )
    barrier = threading.Barrier(2)
    outcomes = []

    def run():
        barrier.wait()
        outcomes.append(project(store, operation_id))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert {outcome.state for outcome in outcomes} <= {"planned", "committed"}
    reconciled = [project(store, operation_id) for _ in outcomes]
    assert all(outcome.state == "committed" for outcome in reconciled)
    assert len({outcome.adapter_id for outcome in reconciled}) == 1


def test_concurrent_retry_validation_has_one_probe_and_preserves_failed_projection(store, tmp_path, monkeypatch):
    import threading

    from runtime.daemon.direct_connect_retry import retry_validate
    from runtime.orchestrator import custom_adapter_registry

    operation_id, _ = _mint_and_receive(store, tmp_path)
    assert store.plan_projection(operation_id)
    assert store.mark_failed(operation_id, "original failure")
    calls = 0
    calls_lock = threading.Lock()
    entered_probe = threading.Event()
    release_probe = threading.Event()

    def fake_probe(_executable, adapter_id, **_kwargs):
        nonlocal calls
        with calls_lock:
            calls += 1
        entered_probe.set()
        assert release_probe.wait(timeout=5)
        return _fake_probe_output(adapter_id)

    monkeypatch.setattr(custom_adapter_registry, "run_conformance_probe", fake_probe)
    barrier = threading.Barrier(2)
    outcomes = []

    def run():
        barrier.wait()
        outcomes.append(retry_validate(store, operation_id))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    assert entered_probe.wait(timeout=5)
    release_probe.set()
    for thread in threads:
        thread.join(timeout=5)

    assert all(outcome.state == "committed" for outcome in outcomes)
    assert calls == 1
    projection = store.get_projection(operation_id)
    assert projection is not None and projection.state == "failed" and projection.reason == "original failure"


def test_projection_state_survives_store_reopen(tmp_path, monkeypatch):
    from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore
    from runtime.daemon.direct_connect_projection import project
    from runtime.orchestrator import custom_adapter_registry

    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / "daemon"))
    path = tmp_path / "direct.db"
    store = DirectConnectAuthorityStore(path, runtime_root=tmp_path)
    operation_id, wrapper = _mint_and_receive(store, tmp_path, token="hrreg_reopen")
    monkeypatch.setattr(
        custom_adapter_registry, "run_conformance_probe",
        lambda executable, name, **_kwargs: _fake_probe_output(name),
    )
    committed = project(store, operation_id)
    store.close()

    reopened = DirectConnectAuthorityStore(path, runtime_root=tmp_path)
    projection = reopened.get_projection(operation_id)
    assert projection.state == "committed"
    assert projection.adapter_id == committed.adapter_id
    reopened.close()


def test_same_parent_later_candidate_b_supersedes_earlier_candidate_a(store, tmp_path, monkeypatch):
    """Parent-local ordering: terminal failed candidate A is not re-driven once corrected candidate B exists."""
    from runtime.daemon.direct_connect_projection import project
    from runtime.orchestrator import custom_adapter_registry

    op_a = _mint_and_receive_identity(
        store, tmp_path, token="hrreg_same_parent", profile_name="same-parent-profile",
        identity_hash="hash-a", identity_blob="blob-a",
    )
    assert store.plan_projection(op_a, now=3)
    assert store.mark_failed(op_a, "conformance_probe_failed: missing canary", now=4)

    op_b = store.reserve(
        "hrreg_same_parent", identity_hash="hash-b", identity_blob="blob-b", now=5,
    )
    assert op_b is not None
    _receive_identity_candidate(
        store, tmp_path, token="hrreg_same_parent", operation_id=op_b,
        wrapper_body=b"#!/bin/sh\necho v2\n", child_name="child-b",
        identity_hash="hash-b", identity_blob="blob-b", now=5,
    )

    # A already has a failed projection; driving it again returns the existing
    # terminal outcome without starting a second probe. B is the sole candidate
    # that may proceed.
    outcome_a = project(store, op_a)
    assert outcome_a.state == "failed"
    assert "conformance_probe_failed" in outcome_a.reason

    monkeypatch.setattr(
        custom_adapter_registry, "run_conformance_probe",
        lambda executable, name, **_kwargs: _fake_probe_output(name),
    )
    outcome_b = project(store, op_b)

    assert outcome_b.state == "committed"
    assert outcome_b.profile_name == "same-parent-profile"


def test_cross_parent_terminal_b_does_not_suppress_new_authority_a(store, tmp_path, monkeypatch):
    """A newer authority's first candidate A must not be superseded by an older authority's terminal candidate B."""
    from runtime.daemon.direct_connect_projection import project
    from runtime.orchestrator import custom_adapter_registry

    # Authority 1: accepted candidate A, then terminal corrected candidate B.
    op_a1 = _mint_and_receive_identity(
        store, tmp_path, token="hrreg_old_parent", profile_name="shared-profile",
        identity_hash="hash-a1", identity_blob="blob-a1",
    )
    assert store.plan_projection(op_a1, now=3)
    assert store.mark_failed(op_a1, "conformance_probe_failed: missing canary", now=4)

    op_b1 = store.reserve(
        "hrreg_old_parent", identity_hash="hash-b1", identity_blob="blob-b1", now=5,
    )
    assert op_b1 is not None
    _receive_identity_candidate(
        store, tmp_path, token="hrreg_old_parent", operation_id=op_b1,
        wrapper_body=b"#!/bin/sh\necho v2\n", child_name="child-b1",
        identity_hash="hash-b1", identity_blob="blob-b1", now=5,
    )
    assert store.plan_projection(op_b1, now=6)
    assert store.mark_failed(op_b1, "integrity_probe_failed: bad signature", now=7)

    # Authority 2: distinct token fingerprint, same profile, first candidate A.
    op_a2 = _mint_and_receive_identity(
        store, tmp_path, token="hrreg_new_parent", profile_name="shared-profile",
        identity_hash="hash-a2", identity_blob="blob-a2",
    )

    monkeypatch.setattr(
        custom_adapter_registry, "run_conformance_probe",
        lambda executable, name, **_kwargs: _fake_probe_output(name),
    )

    outcome = project(store, op_a2)

    assert outcome.state == "committed", outcome.reason
    assert outcome.profile_name == "shared-profile"

    # Old authority's retained history is untouched.
    assert len(store.list_candidates("hrreg_old_parent")) == 2
    assert len(store.list_candidates("hrreg_new_parent")) == 1

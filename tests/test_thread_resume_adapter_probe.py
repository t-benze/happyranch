"""THR-200 PR-2: server-earned custom-adapter resume capability."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from runtime.orchestrator.adapter_contract import AdapterOutput
from runtime.orchestrator.adapter_store import AdapterEntry, get_adapter, save_adapter
from runtime.orchestrator import custom_adapter_registry as registry


def _output(
    adapter: str,
    *,
    text: str | None = "ok",
    success: bool = True,
    agent_session_id: str | None = "provider-session",
    session_status: str | None = None,
) -> AdapterOutput:
    return AdapterOutput.model_validate({
        "success": success,
        "duration_seconds": 0,
        "session_id": "probe",
        "returncode": 0 if success else 1,
        "stdout_tail": "",
        "stderr_tail": "",
        "result": None if text is None else {"text": text},
        "error": None if success else "not found",
        "agent_session_id": agent_session_id,
        "rate_limited": False,
        "session_status": session_status,
        "adapter_metadata": {
            "adapter": adapter,
            "adapter_version": "1.0.0",
            "contract_version": 1,
        },
    })


def test_submitted_thread_resume_is_rejected_actionably() -> None:
    with pytest.raises(ValueError, match="server-earned.*verify_thread_resume=true"):
        registry.validate_capabilities(["thread_resume"])


def test_probe_input_uses_caller_workspace_session_and_deadline(tmp_path: Path) -> None:
    from runtime.orchestrator.adapter_contract import SessionInfo

    session = SessionInfo(resume_session_id="provider-id")
    value = registry.build_probe_input(
        "adapter", workspace=tmp_path, session=session, deadline_seconds=41,
        invocation_kind="thread",
    )
    assert value.workspace == str(tmp_path)
    assert value.session == session
    assert value.timeout.deadline_seconds == 41
    assert value.timeout.max_runtime_seconds == 41


def test_resume_probe_is_stateful_and_cleans_workspace(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / "home"))
    calls = []

    def fake_run(executable, adapter_name, **kwargs):
        probe_input = kwargs["probe_input"]
        calls.append(probe_input)
        def bound(output):
            return output.model_copy(update={"session_id": probe_input.invocation.invocation_id})
        if len(calls) == 1:
            canary_a = probe_input.prompt.rsplit(" ", 1)[-1]
            fake_run.canary_a = canary_a
            return bound(_output(adapter_name, text=canary_a, session_status="fresh"))
        if len(calls) == 2:
            canary_b = probe_input.prompt.rsplit(" ", 1)[-1]
            assert fake_run.canary_a not in probe_input.prompt
            return bound(_output(
                adapter_name, text=f"{fake_run.canary_a} {canary_b}",
                agent_session_id="replacement-id", session_status="resumed",
            ))
        return bound(_output(
            adapter_name, text=None, success=False,
            agent_session_id=None, session_status="not_found",
        ))

    monkeypatch.setattr(registry, "run_conformance_probe", fake_run)
    fresh = registry.run_resume_conformance_probe("/adapter", "adapter")

    assert fresh.agent_session_id == "provider-session"
    assert len({call.workspace for call in calls}) == 1
    assert calls[0].session is None
    assert calls[1].session.resume_session_id == "provider-session"
    assert calls[2].session.resume_session_id.startswith("hr-probe-missing-")
    assert not Path(calls[0].workspace).exists()


@pytest.mark.parametrize("failure_stage", [1, 2, 3])
def test_incomplete_or_fake_stage_fails_and_cleans(
    tmp_path: Path, monkeypatch, failure_stage: int,
) -> None:
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / "home"))
    calls = []

    def fake_run(executable, adapter_name, **kwargs):
        value = kwargs["probe_input"]
        calls.append(value)
        def bound(output):
            return output.model_copy(update={"session_id": value.invocation.invocation_id})
        stage = len(calls)
        if stage == 1:
            canary_a = value.prompt.rsplit(" ", 1)[-1]
            fake_run.canary_a = canary_a
            if failure_stage == 1:  # static response
                return bound(_output(adapter_name, text="static", agent_session_id=None))
            return bound(_output(adapter_name, text=canary_a, session_status="fresh"))
        if stage == 2:
            canary_b = value.prompt.rsplit(" ", 1)[-1]
            if failure_stage == 2:  # echo-only, no retained A
                return bound(_output(adapter_name, text=canary_b, session_status="resumed"))
            return bound(_output(
                adapter_name, text=f"{fake_run.canary_a} {canary_b}",
                session_status="resumed",
            ))
        if failure_stage == 3:  # create-on-missing
            return bound(_output(adapter_name, text=value.prompt, session_status="fresh"))
        return bound(_output(adapter_name, text=None, success=False, session_status="not_found"))

    monkeypatch.setattr(registry, "run_conformance_probe", fake_run)
    with pytest.raises(ValueError, match="Resume conformance"):
        registry.run_resume_conformance_probe("/adapter", "adapter")
    if calls:
        assert not Path(calls[0].workspace).exists()


def test_registration_publishes_one_coherent_receipt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / "home"))
    executable = tmp_path / "receipt-adapter"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)
    monkeypatch.setattr(
        registry, "run_resume_conformance_probe",
        lambda executable, adapter_name: _output(adapter_name, session_status="fresh"),
    )

    entry = registry.register_custom_adapter(
        str(executable), "1", [], verify_thread_resume=True,
    )
    persisted = get_adapter(entry.id)
    assert persisted.capabilities == ["thread_resume"]
    assert persisted.thread_resume_verified_at
    assert persisted.thread_resume_contract_version == 1
    assert persisted.status == "pending"


def test_failed_reverification_leaves_entry_byte_identical(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / "home"))
    executable = tmp_path / "unchanged-adapter"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)
    original = AdapterEntry(
        id="unchanged-adapter", name="unchanged-adapter",
        executable=str(executable), executable_hash=registry.compute_sha256(executable),
        version="1", capabilities=["thread_resume"], status="approved",
        registered_at="old", approved_at="old", approved_by="founder",
        thread_resume_verified_at="old", thread_resume_contract_version=1,
    )
    save_adapter(original)
    before = copy.deepcopy(get_adapter(original.id).to_dict())
    monkeypatch.setattr(
        registry, "run_resume_conformance_probe",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("stage failed")),
    )

    with pytest.raises(ValueError, match="stage failed"):
        registry.register_custom_adapter(
            str(executable), "1", [], verify_thread_resume=True,
        )
    assert get_adapter(original.id).to_dict() == before


def test_plain_reregistration_invalidates_earned_state_and_approval(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / "home"))
    executable = tmp_path / "identity-adapter"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)
    original = AdapterEntry(
        id="identity-adapter", name="identity-adapter",
        executable=str(executable), executable_hash=registry.compute_sha256(executable),
        version="1", capabilities=["thread_resume"], status="approved",
        registered_at="old", approved_at="old", approved_by="founder",
        thread_resume_verified_at="old", thread_resume_contract_version=1,
    )
    save_adapter(original)
    monkeypatch.setattr(
        registry, "run_conformance_probe",
        lambda executable, adapter_name: _output(adapter_name),
    )

    entry = registry.register_custom_adapter(str(executable), "1", [])
    assert entry.status == "pending"
    assert entry.capabilities == []
    assert entry.approved_at is None
    assert entry.approved_by is None
    assert entry.thread_resume_verified_at is None
    assert entry.thread_resume_contract_version is None


def test_approval_binds_exact_server_earned_receipt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / "home"))
    executable = tmp_path / "approval-adapter"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)
    pending = AdapterEntry(
        id="approval-adapter", name="approval-adapter",
        executable=str(executable), executable_hash=registry.compute_sha256(executable),
        version="1", capabilities=["thread_resume"], status="pending",
        registered_at="now", thread_resume_verified_at="receipt-new",
        thread_resume_contract_version=1,
    )
    save_adapter(pending)
    common = dict(
        adapter_id=pending.id, executable=pending.executable,
        executable_hash=pending.executable_hash, version=pending.version,
        capabilities=pending.capabilities, contract_version=1,
        workspace_adapter="pi",
    )
    with pytest.raises(ValueError, match="thread_resume_verified_at mismatch"):
        registry.approve_adapter(
            **common, thread_resume_verified_at="receipt-stale",
            thread_resume_contract_version=1,
        )
    assert get_adapter(pending.id).status == "pending"

    approved = registry.approve_adapter(
        **common, thread_resume_verified_at="receipt-new",
        thread_resume_contract_version=1,
    )
    assert approved.status == "approved"
    assert approved.thread_resume_verified_at == "receipt-new"

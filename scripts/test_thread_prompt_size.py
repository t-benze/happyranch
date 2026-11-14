"""Focused no-provider thread prompt checks; see docs/local-ci.md."""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from scripts.test_agent_retirement import fixture
from runtime.config import Settings
from runtime.daemon import thread_runner as runner
from runtime.daemon.org_state import OrgState
from runtime.models import ThreadInvocationStatus, ThreadMessageKind, ThreadRecord
from runtime.orchestrator.executors import ExecutorResult
from runtime.orchestrator.thread_move import current_thread_preparation

BOUND = 16_000


def delivery_note(start: int, end: int) -> str:
    return ("\n## Delivery range (GH-688 Phase 1)\n"
        f"You are asked to respond to messages {start} through {end} (inclusive), in order. "
        "Consider every message in that range; do not skip any of them.\n")


@pytest.fixture
async def org(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[OrgState]:
    # Same standalone synthetic-org venue as test_task_cancel; no lifespan.
    async with fixture(tmp_path / "org") as (state, _client):
        monkeypatch.setattr(runner, "materialize_workspace_skills", lambda *a, **k: [])
        monkeypatch.setattr(runner, "validate_workspace_skills_integrity", lambda *a, **k: None)
        monkeypatch.setattr(runner, "refresh_workspace_repos", lambda *a, **k: [])
        monkeypatch.setattr(runner, "resolve_managed_skills_index", lambda **k: "")
        monkeypatch.setattr(runner, "render_current_time_line", lambda *a: "2026-11-14T00:00Z")
        monkeypatch.setattr(runner, "THREAD_PROMPT_MAX_BYTES", BOUND, raising=False)
        yield state


def seed(org: OrgState, *, history: list[str], required: list[str], resume: bool = False) -> str:
    org.db.insert_thread(ThreadRecord(id="THR-size", subject="Synthetic bounded thread"))
    org.db.add_thread_participant("THR-size", "worker", added_by="founder")
    for body in history:
        org.db.append_thread_message(thread_id="THR-size", speaker="founder",
            kind=ThreadMessageKind.MESSAGE, body_markdown=body)
    # Establish a genuinely delivered historical frontier through production DB APIs.
    if history:
        org.db.cutover_thread_reply_delivery_state("THR-size")
    for body in required:
        org.db.record_conversational_arrival(thread_id="THR-size", speaker="founder",
            kind=ThreadMessageKind.MESSAGE, body_markdown=body, recipients=["worker"])
    state = org.db.get_reply_delivery_state("THR-size", "worker")
    assert state is not None and state.queued_invocation_token
    if resume:
        org.db.update_thread_session("THR-size", "worker", agent_session_id="synthetic-sid",
                                     last_resumed_seq=len(history))
    return state.queued_invocation_token


class ExecutorStub:
    """Replace only provider execution; keep runner and delivery/session DB real."""

    def __init__(self, org: OrgState, token: str, *, first: str = "callback") -> None:
        self.org, self.token, self.first = org, token, first
        self.calls: list[dict[str, Any]] = []

    def run(self, **kwargs: Any) -> ExecutorResult:
        self.calls.append(kwargs)
        owner = current_thread_preparation()
        assert owner is not None
        owner.retry_schedule("provider", ())
        owner.before_selflaunch()
        # Synthetic provider-start observation, without an OS process.
        kwargs["on_started"](424242)
        if len(self.calls) == 1 and self.first == "evicted":
            return ExecutorResult(success=False, duration_seconds=0, session_id="synthetic",
                returncode=1, stderr_tail="No conversation found with session ID: synthetic-sid")
        if len(self.calls) == 1 and self.first == "no_callback":
            return ExecutorResult(success=True, duration_seconds=0, session_id="synthetic", returncode=0)
        self.org.db.settle_conversational_reply(token=self.token, outcome="decline")
        return ExecutorResult(success=True, duration_seconds=0, session_id="synthetic", returncode=0)


async def drive(org: OrgState, token: str, executor: ExecutorStub) -> None:
    with patch.object(runner, "_build_executor_for_provider", return_value=executor), \
         patch("subprocess.Popen", side_effect=AssertionError("provider must never run")):
        await runner.run_invocation(org_state=org, invocation_token=token, settings=Settings())


@pytest.mark.parametrize("resume", [False, True], ids=["fresh", "delta"])
@pytest.mark.parametrize("headroom", [0, 1000], ids=["exact-bound", "spare"])
async def test_under_bound_prompt_unchanged(
    org: OrgState, resume: bool, headroom: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = seed(org, history=["Already delivered history"], required=["CLAIM-A", "CLAIM-B"], resume=resume)
    executor = ExecutorStub(org, token)
    builder = runner.build_thread_delta_prompt if resume else runner.build_thread_prompt
    rendered: list[str] = []

    def observe(**kwargs: Any) -> str:
        value = builder(**kwargs)
        rendered.append(value)
        monkeypatch.setattr(runner, "THREAD_PROMPT_MAX_BYTES", len((value + delivery_note(2, 3)).encode()) + headroom)
        return value

    with patch.object(runner, builder.__name__, side_effect=observe):
        await drive(org, token, executor)
    assert len(executor.calls) == 1
    prompt = executor.calls[0]["prompt"]
    assert prompt == rendered[0] + delivery_note(2, 3)
    assert "CLAIM-A" in prompt and "CLAIM-B" in prompt and token in prompt
    assert "omitted" not in prompt
    assert bool(executor.calls[0].get("resume_session_id")) == resume
    assert org.db.get_invocation_any_status(token).status == ThreadInvocationStatus.DECLINED


@pytest.mark.parametrize("path", ["fresh", "fallback", "nudge"])
@pytest.mark.parametrize("newest_fits", [True, False], ids=["retain-newest", "newest-too-large"])
async def test_optional_trimming_preserves_claimed_range(org: OrgState, path: str, newest_fits: bool) -> None:
    history = (["OLD-HUGE" + "x" * BOUND, "RECENT-OPTIONAL"] if newest_fits else
               ["OLDEST-SMALL", "RECENT-HUGE" + "x" * BOUND])
    token = seed(org, history=history,
                 required=["CLAIM-A", "CLAIM-B"], resume=path == "fallback")
    executor = ExecutorStub(org, token, first={"fallback": "evicted", "nudge": "no_callback"}.get(path, "callback"))
    await drive(org, token, executor)
    assert len(executor.calls) == (1 if path == "fresh" else 2)
    for call in executor.calls:
        prompt = call["prompt"]
        assert len(prompt.encode("utf-8")) <= BOUND
        assert "CLAIM-A" in prompt and "CLAIM-B" in prompt and token in prompt
        if not call.get("resume_session_id"):
            assert "OLD-HUGE" not in prompt and "RECENT-HUGE" not in prompt
            assert ("RECENT-OPTIONAL" in prompt) == newest_fits
            assert "OLDEST-SMALL" not in prompt  # never skip newer optional history to keep older
            assert "Earlier optional thread history omitted" in prompt
            assert 'Synthetic bounded thread' in prompt
            assert "Delivery range (GH-688 Phase 1)" in prompt
    assert org.db.get_reply_delivery_state("THR-size", "worker").acknowledged_through_seq == 4


@pytest.mark.parametrize("path", ["fresh", "delta", "fallback", "nudge"])
async def test_oversize_fails_before_phase_launch(org: OrgState, path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    required = ["MANDATORY" + "界" * (BOUND // 2)]
    if path in {"fallback", "nudge"}:
        required = ["MANDATORY"]
    token = seed(org, history=["delivered"], required=required, resume=path in {"delta", "fallback"})
    before_session = org.db.get_thread_session("THR-size", "worker")
    before_delivery = org.db.get_reply_delivery_state("THR-size", "worker")
    executor = ExecutorStub(org, token, first={"fallback": "evicted", "nudge": "no_callback"}.get(path, "callback"))
    original_full = runner.build_thread_prompt

    def grow_full(**kwargs: Any) -> str:
        # A real full-only header/skills overhead can exceed a delta's bound.
        return original_full(**kwargs) + "S" * BOUND

    if path == "fallback":
        monkeypatch.setattr(runner, "build_thread_prompt", grow_full)
    if path == "nudge":
        # Initial full fits exactly; the corrective suffix makes the rebuild too large.
        padding: str | None = None

        def fill_header(**kwargs: Any) -> str:
            nonlocal padding
            value = original_full(**kwargs)
            if padding is None:
                padding = "S" * (BOUND - len((value + delivery_note(2, 2)).encode("utf-8")))
            return value + padding

        monkeypatch.setattr(runner, "build_thread_prompt", fill_header)
    with patch("runtime.orchestrator.thread_move.ThreadPreparation.save_session", autospec=True,
               side_effect=AssertionError("oversize phase must not save a session")) as save:
        await drive(org, token, executor)
    after = org.db.get_invocation_any_status(token)
    assert after.status == ThreadInvocationStatus.FAILED
    assert (after.decline_reason or "").startswith("prompt_too_large:")
    assert len(executor.calls) == (0 if path in {"fresh", "delta"} else 1)
    save.assert_not_called()
    assert org.db.get_thread_session("THR-size", "worker") == (
        (None, 1) if path == "fallback" else before_session)
    delivery = org.db.get_reply_delivery_state("THR-size", "worker")
    assert delivery.acknowledged_through_seq == before_delivery.acknowledged_through_seq
    assert delivery.required_through_seq == before_delivery.required_through_seq
    assert delivery.running_invocation_token is None and delivery.queued_invocation_token is None
    assert len(org.db.list_thread_invocations("THR-size")) == 1  # no automatic retry loop
    audits = org.db.get_audit_logs("THR-size")
    failures = [row["payload"] for row in audits if row["action"] == "thread_invocation_failed"]
    assert len(failures) == 1 and failures[0]["reason"].startswith("prompt_too_large:")
    assert not any(row["action"].startswith("thread_reply_breaker_") for row in audits)
    assert org.db.get_thread_reply_breaker("THR-size", "worker", "claude:default:3:900") is None
    result = ExecutorResult(success=False, duration_seconds=0, session_id="synthetic",
                            failure_category="prompt_too_large", provider_launched=False)
    assert runner._qualifying_breaker_failure(result) is None

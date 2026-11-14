"""Standalone no-provider regressions for #395 and #219; see docs/local-ci.md."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from runtime.daemon import thread_runner as runner
from runtime.daemon.org_state import OrgState
from runtime.models import ThreadInvocationPurpose, ThreadInvocationStatus
from runtime.orchestrator import executors
from runtime.orchestrator.executors import ExecutorResult
from runtime.orchestrator.team_move import DeferredTeamMove
from runtime.orchestrator.thread_move import current_thread_preparation
from scripts.test_thread_prompt_size import BOUND, drive, org as org, seed


class CallbackExecutor:
    """Replace provider execution only; callbacks and session/delivery writes are real."""

    def __init__(self, org: OrgState, token: str, outcomes: list[str]) -> None:
        self.org, self.token, self.outcomes = org, token, outcomes
        self.calls: list[dict[str, Any]] = []
        self.sessions: list[tuple[str | None, int]] = []
        self.acknowledged: list[int] = []

    def run(self, **kwargs: Any) -> ExecutorResult:
        self.calls.append(kwargs)
        self.sessions.append(self.org.db.get_thread_session("THR-size", "worker"))
        self.acknowledged.append(
            self.org.db.get_reply_delivery_state("THR-size", "worker").acknowledged_through_seq)
        owner = current_thread_preparation()
        assert owner is not None
        owner.retry_schedule("provider", ())
        owner.before_selflaunch()
        kwargs["on_started"](424242)  # synthetic start observation; no process
        outcome = self.outcomes[len(self.calls) - 1]
        if outcome == "reply":
            self.org.db.reply_conversational(
                thread_id="THR-size", speaker="worker", body_markdown="Synthetic answer",
                attachments=None, token=self.token, token_purpose=ThreadInvocationPurpose.REPLY)
        elif outcome == "decline":
            self.org.db.settle_conversational_reply(token=self.token, outcome="decline")
        else:
            assert outcome == "pending"
        return ExecutorResult(success=True, duration_seconds=0, session_id="synthetic-runtime",
            returncode=0, agent_session_id=f"provider-{len(self.calls)}", provider_launched=True)


@pytest.mark.parametrize("resume", [False, True], ids=["fresh", "stored"])
async def test_pending_first_pass_preserves_session_and_watermarks(org: OrgState, resume: bool) -> None:
    token = seed(org, history=["DELIVERED"], required=["CLAIM-A", "CLAIM-B"], resume=resume)
    executor = CallbackExecutor(org, token, ["pending", "decline"])
    await drive(org, token, executor)
    assert len(executor.calls) == 2
    expected = ("synthetic-sid", 1) if resume else (None, 0)
    assert executor.sessions == [expected, expected]
    assert executor.acknowledged == [1, 1]


async def test_nudge_is_fresh_full_context_and_bounded(org: OrgState) -> None:
    token = seed(org, history=["OLD-HUGE" + "x" * BOUND, "RECENT-HISTORY"],
                 required=["CLAIM-A", "CLAIM-B"], resume=True)
    executor = CallbackExecutor(org, token, ["pending", "decline"])
    await drive(org, token, executor)
    assert len(executor.calls) == 2
    assert executor.calls[0]["resume_session_id"] == "synthetic-sid"
    nudge = executor.calls[1]
    assert not nudge.get("resume_session_id")
    prompt = nudge["prompt"]
    assert len((executors._SESSION_LIFETIME_PREAMBLE + prompt).encode("utf-8")) <= BOUND
    for text in ("Synthetic bounded thread", "RECENT-HISTORY", "CLAIM-A", "CLAIM-B", token,
                 "messages 3 through 4", "you ended without posting", "Earlier optional thread history omitted"):
        assert text in prompt
    assert "OLD-HUGE" not in prompt


async def test_oversize_nudge_preserves_session_and_delivery(
    org: OrgState, monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = seed(org, history=["DELIVERED"], required=["CLAIM-A"], resume=True)
    original = runner.build_thread_prompt

    def oversize_full(**kwargs: Any) -> str:
        return original(**kwargs) + "mandatory instructions" * BOUND

    monkeypatch.setattr(runner, "build_thread_prompt", oversize_full)
    executor = CallbackExecutor(org, token, ["pending", "decline"])
    await drive(org, token, executor)
    assert len(executor.calls) == 1
    assert org.db.get_thread_session("THR-size", "worker") == ("synthetic-sid", 1)
    inv = org.db.get_invocation_any_status(token)
    assert inv.status == ThreadInvocationStatus.FAILED
    assert inv.decline_reason.startswith("prompt_too_large:")
    delivery = org.db.get_reply_delivery_state("THR-size", "worker")
    assert (delivery.acknowledged_through_seq, delivery.required_through_seq) == (1, 2)
    assert org.db.get_thread_reply_breaker("THR-size", "worker", "claude:default:3:900") is None
    assert len(org.db.list_thread_invocations("THR-size")) == 1


@pytest.mark.parametrize("resume", [False, True], ids=["fresh", "stored"])
async def test_no_callback_after_reprompt_clears_session_without_advancing(org: OrgState, resume: bool) -> None:
    token = seed(org, history=["DELIVERED"], required=["CLAIM-A", "CLAIM-B"], resume=resume)
    executor = CallbackExecutor(org, token, ["pending", "pending"])
    await drive(org, token, executor)
    assert len(executor.calls) == 2
    assert org.db.get_thread_session("THR-size", "worker") == (None, 1 if resume else 0)
    inv = org.db.get_invocation_any_status(token)
    assert inv.status == ThreadInvocationStatus.FAILED
    assert inv.decline_reason.startswith("no_callback_after_reprompt: rc=0")
    delivery = org.db.get_reply_delivery_state("THR-size", "worker")
    assert (delivery.acknowledged_through_seq, delivery.required_through_seq) == (1, 3)
    assert delivery.running_invocation_token is None and delivery.queued_invocation_token is None
    breaker = org.db.get_thread_reply_breaker("THR-size", "worker", "claude:default:3:900")
    assert breaker is not None and breaker.consecutive_failures == 1
    assert breaker.last_failure_category == "post_launch_contract"
    assert len(org.db.list_thread_invocations("THR-size")) == 1


@pytest.mark.parametrize("callback", ["reply", "decline"])
@pytest.mark.parametrize("nudge", [False, True], ids=["initial", "nudge"])
async def test_successful_callback_saves_session_and_advances(
    org: OrgState, callback: str, nudge: bool,
) -> None:
    token = seed(org, history=["DELIVERED"], required=["CLAIM-A", "CLAIM-B"], resume=True)
    executor = CallbackExecutor(org, token, ["pending", callback] if nudge else [callback])
    await drive(org, token, executor)
    assert len(executor.calls) == (2 if nudge else 1)
    assert org.db.get_thread_session("THR-size", "worker") == (f"provider-{len(executor.calls)}", 3)
    expected = ThreadInvocationStatus.CONSUMED if callback == "reply" else ThreadInvocationStatus.DECLINED
    assert org.db.get_invocation_any_status(token).status == expected
    assert org.db.get_reply_delivery_state("THR-size", "worker").acknowledged_through_seq == 3
    assert org.db.get_thread_reply_breaker("THR-size", "worker", "claude:default:3:900") is None


async def test_reentered_nudge_clears_session_without_another_retry(org: OrgState) -> None:
    token = seed(org, history=["DELIVERED"], required=["CLAIM-A"], resume=True)
    executor = CallbackExecutor(org, token, ["pending"])

    async def defer_after_phase(**kwargs: Any) -> None:
        # Seed a durable nudge phase through its real writer before any launch.
        # Direct fake launches retain unknown containment, so they cannot be
        # used to claim native cleanup/recovery proof or admit a second owner.
        kwargs["move_owner"].start_phase("nudge")
        raise DeferredTeamMove("synthetic-defer-after-phase")

    with patch.object(runner, "_run_invocation_owned", defer_after_phase), pytest.raises(DeferredTeamMove):
        await drive(org, token, executor)
    assert not executor.calls
    await drive(org, token, executor)
    assert len(executor.calls) == 1
    assert not executor.calls[0].get("resume_session_id")
    assert "CLAIM-A" in executor.calls[0]["prompt"]
    assert org.db.get_thread_session("THR-size", "worker") == (None, 1)
    inv = org.db.get_invocation_any_status(token)
    assert inv.status == ThreadInvocationStatus.FAILED
    assert inv.decline_reason.startswith("no_callback_after_reprompt:")
    assert org.db.get_reply_delivery_state("THR-size", "worker").acknowledged_through_seq == 1
    assert org.db.get_thread_reply_breaker(
        "THR-size", "worker", "claude:default:3:900").consecutive_failures == 1


def test_pi_raw_usage_preserves_stdout_tail() -> None:
    usage_line = json.dumps({"type": "diagnostic", "usage": {"input": 123, "output": 45}})
    stdout = "prefix-only" * 300 + "\n" + usage_line + "\n"
    usage = executors._parse_pi_usage(stdout)
    assert usage is not None and usage.input_tokens is None
    assert usage.usage_raw_json == stdout[-2000:]
    assert usage_line in usage.usage_raw_json and not usage.usage_raw_json.startswith("prefix-only")


def test_pi_terminal_usage_beyond_stdout_prefix() -> None:
    stdout = "x" * 3000 + "\n" + json.dumps({"type": "turn_end", "model": "synthetic-pi",
        "message": {"usage": {"input": 123, "output": 45, "cacheRead": 7, "cacheWrite": 2}}})
    usage = executors._parse_pi_usage(stdout)
    assert usage is not None
    assert (usage.input_tokens, usage.output_tokens, usage.cache_read_tokens,
            usage.cache_creation_tokens, usage.model) == (123, 45, 7, 2, "synthetic-pi")

from __future__ import annotations

import pytest

from runtime.models import ChainLeg, CompletionReport
from runtime.orchestrator.chain import (
    AdvanceAction,
    ChainState,
    build_prior_leg_context,
    compute_advance_action,
)


def test_chain_state_serialize_roundtrip():
    cs = ChainState(
        step_index=1,
        first_leg_expect_verdict="APPROVE",
        legs=[
            ChainLeg(agent="qa_engineer", prompt="qa", expect_verdict="PASS"),
        ],
        step_audit_id=4521,
    )
    payload = cs.serialize()
    cs2 = ChainState.deserialize(payload)
    assert cs2.step_index == 1
    assert cs2.first_leg_expect_verdict == "APPROVE"
    assert len(cs2.legs) == 1
    assert cs2.legs[0].agent == "qa_engineer"
    assert cs2.step_audit_id == 4521


def test_chain_state_deserialize_handles_missing_optional_fields():
    cs = ChainState.deserialize('{"step_index": 0, "legs": [], "step_audit_id": 1}')
    assert cs.first_leg_expect_verdict is None
    assert cs.legs == []


def test_build_prior_leg_context_includes_all_fields():
    report = CompletionReport(
        task_id="TASK-579",
        agent="senior_dev",
        status="completed",
        confidence=92,
        output_summary="PR #180 looks good. All gates green.",
        verdict="APPROVE",
        output_dir="workspaces/senior_dev/output/TASK-579/",
    )
    out = build_prior_leg_context(child_task_id="TASK-579", report=report)
    assert "Prior leg:    TASK-579" in out
    assert "agent: senior_dev" in out
    assert "Verdict:      APPROVE" in out
    assert "Confidence:   92" in out
    assert "PR #180 looks good. All gates green." in out
    assert "Output dir: workspaces/senior_dev/output/TASK-579/" in out


def test_build_prior_leg_context_omits_output_dir_when_unset():
    report = CompletionReport(
        task_id="TASK-580",
        agent="dev_agent",
        status="completed",
        confidence=80,
        output_summary="built",
    )
    out = build_prior_leg_context(child_task_id="TASK-580", report=report)
    assert "Verdict:      -" in out
    assert "Output dir:" not in out


def _legs_pair():
    return [
        ChainLeg(agent="senior_dev", prompt="review", expect_verdict="APPROVE"),
        ChainLeg(agent="qa_engineer", prompt="qa", expect_verdict="PASS"),
    ]


def _report(*, status="completed", verdict=None):
    return CompletionReport(
        task_id="TASK-X",
        agent="w",
        status=status,
        confidence=80,
        output_summary="...",
        verdict=verdict,
    )


def test_compute_advance_action_wake_on_blocked():
    cs = ChainState(step_index=0, first_leg_expect_verdict=None, legs=_legs_pair(), step_audit_id=1)
    action = compute_advance_action(chain=cs, report=_report(status="blocked"))
    assert action.kind == "wake"
    assert action.reason == "child_blocked"


def test_compute_advance_action_wake_on_verdict_mismatch_first_leg():
    cs = ChainState(step_index=0, first_leg_expect_verdict="APPROVE", legs=_legs_pair(), step_audit_id=1)
    action = compute_advance_action(chain=cs, report=_report(verdict="REQUEST_CHANGES"))
    assert action.kind == "wake"
    assert action.reason == "verdict_mismatch"
    assert action.expected == "APPROVE"
    assert action.actual == "REQUEST_CHANGES"


def test_compute_advance_action_wake_on_missing_verdict_when_gated():
    cs = ChainState(step_index=1, first_leg_expect_verdict=None, legs=_legs_pair(), step_audit_id=1)
    # step_index=1 means we just got the terminal of leg index 1 (= legs[0], senior_dev with expect=APPROVE)
    action = compute_advance_action(chain=cs, report=_report(verdict=None))
    assert action.kind == "wake"
    assert action.reason == "verdict_mismatch"
    assert action.expected == "APPROVE"
    assert action.actual is None


def test_compute_advance_action_advance_first_leg_ungated():
    cs = ChainState(step_index=0, first_leg_expect_verdict=None, legs=_legs_pair(), step_audit_id=1)
    action = compute_advance_action(chain=cs, report=_report())
    assert action.kind == "advance"
    assert action.next_leg.agent == "senior_dev"
    assert action.next_step_index == 1


def test_compute_advance_action_advance_first_leg_gated_match():
    cs = ChainState(step_index=0, first_leg_expect_verdict="APPROVE", legs=_legs_pair(), step_audit_id=1)
    action = compute_advance_action(chain=cs, report=_report(verdict="APPROVE"))
    assert action.kind == "advance"
    assert action.next_leg.agent == "senior_dev"


def test_compute_advance_action_wake_on_final_leg_match():
    cs = ChainState(step_index=2, first_leg_expect_verdict=None, legs=_legs_pair(), step_audit_id=1)
    # step_index=2 means terminal of leg index 2 (= legs[1], qa_engineer with expect=PASS)
    action = compute_advance_action(chain=cs, report=_report(verdict="PASS"))
    assert action.kind == "wake"
    assert action.reason == "chain_complete"

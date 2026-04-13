from src.models import ReviewVerdict
from src.orchestrator.revision_loop import NextAction, decide_next_action


def test_approve_returns_approved():
    action = decide_next_action(
        verdict=ReviewVerdict.APPROVE,
        revision_count=0,
        max_rounds=2,
    )
    assert action.action == "approved"
    assert action.target_agent is None
    assert action.feedback is None


def test_reject_returns_rejected():
    action = decide_next_action(
        verdict=ReviewVerdict.REJECT,
        revision_count=0,
        max_rounds=2,
    )
    assert action.action == "rejected"


def test_revise_first_round():
    action = decide_next_action(
        verdict=ReviewVerdict.REVISE,
        revision_count=0,
        max_rounds=2,
        feedback="Fix the error handling",
        target_agent="dev_agent",
    )
    assert action.action == "revise"
    assert action.target_agent == "dev_agent"
    assert action.feedback == "Fix the error handling"


def test_revise_second_round():
    action = decide_next_action(
        verdict=ReviewVerdict.REVISE,
        revision_count=1,
        max_rounds=2,
        feedback="Still needs work",
        target_agent="dev_agent",
    )
    assert action.action == "revise"


def test_revise_at_max_rounds_escalates():
    action = decide_next_action(
        verdict=ReviewVerdict.REVISE,
        revision_count=2,
        max_rounds=2,
        feedback="Not good enough",
        target_agent="dev_agent",
    )
    assert action.action == "escalated"
    assert "Max revision rounds" in action.feedback


def test_revise_past_max_rounds_escalates():
    action = decide_next_action(
        verdict=ReviewVerdict.REVISE,
        revision_count=5,
        max_rounds=2,
        feedback="Still wrong",
        target_agent="dev_agent",
    )
    assert action.action == "escalated"

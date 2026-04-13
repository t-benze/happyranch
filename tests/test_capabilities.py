from src.models import AgentName, PerformanceTier, StepRecord
from src.orchestrator.capabilities import build_capabilities_prompt


def test_prompt_includes_brief():
    prompt = build_capabilities_prompt(
        brief="Add Alipay support for international cards",
        agent_tiers={},
        step_number=1,
        max_steps=10,
    )
    assert "Add Alipay support for international cards" in prompt


def test_prompt_includes_agent_tiers():
    tiers = {
        AgentName.DEV_AGENT: PerformanceTier.YELLOW,
        AgentName.PRODUCT_MANAGER: PerformanceTier.GREEN,
    }
    prompt = build_capabilities_prompt(
        brief="Fix bug",
        agent_tiers=tiers,
        step_number=1,
        max_steps=10,
    )
    assert "dev_agent" in prompt
    assert "yellow" in prompt
    assert "product_manager" in prompt
    assert "green" in prompt


def test_prompt_includes_step_number():
    prompt = build_capabilities_prompt(
        brief="Explore",
        agent_tiers={},
        step_number=3,
        max_steps=10,
    )
    assert "step 3" in prompt.lower()
    assert "10" in prompt


def test_prompt_includes_prior_steps():
    prior = [
        StepRecord(
            step_number=1,
            agent="product_manager",
            action="delegate: write spec",
            result_summary="Spec written with 5 acceptance criteria",
            success=True,
        ),
    ]
    prompt = build_capabilities_prompt(
        brief="Add feature",
        agent_tiers={},
        step_number=2,
        max_steps=10,
        prior_steps=prior,
    )
    assert "product_manager" in prompt
    assert "Spec written" in prompt


def test_prompt_no_prior_steps():
    prompt = build_capabilities_prompt(
        brief="Explore",
        agent_tiers={},
        step_number=1,
        max_steps=10,
    )
    assert "Prior Steps" not in prompt


def test_prompt_includes_available_actions():
    prompt = build_capabilities_prompt(
        brief="Do something",
        agent_tiers={},
        step_number=1,
        max_steps=10,
    )
    assert "delegate" in prompt
    assert "done" in prompt
    assert "escalate" in prompt


def test_prompt_includes_constraints():
    prompt = build_capabilities_prompt(
        brief="Do something",
        agent_tiers={},
        step_number=1,
        max_steps=10,
    )
    assert "$200" in prompt
    assert "founder" in prompt.lower()

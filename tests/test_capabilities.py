from __future__ import annotations

from runtime.models import StepRecord
from runtime.orchestrator.capabilities import build_capabilities_prompt


def test_prompt_does_not_duplicate_brief():
    """Regression guard: the brief lives in the outer ``Parameters.brief``
    block built by ``Orchestrator._build_agent_prompt``. The capabilities
    block must NOT restate it — that was the bug we fixed (worker AND manager
    spawns both rendered the brief twice).
    """
    prompt = build_capabilities_prompt(
        agents=[],
        step_number=1,
        max_steps=10,
    )
    assert "Add Alipay support for international cards" not in prompt
    # Structural assertion: no top-level "# Task" header re-emitting the brief.
    assert "# Task" not in prompt
    # The manager must still be told what to do (just without restating the brief).
    assert "brief above" in prompt.lower() or "the brief" in prompt.lower()


def test_prompt_lists_available_agents_without_tier_column():
    """The performance-tier feature was removed. The Available Agents table
    must still list every candidate worker, but no `tier` column should
    appear in the table header or the rows.
    """
    agents = [
        {"name": "dev_agent", "description": "Implements features"},
        {"name": "product_manager", "description": "Writes specs"},
    ]
    prompt = build_capabilities_prompt(
        agents=agents,
        step_number=1,
        max_steps=10,
    )
    # Names + descriptions render
    assert "dev_agent" in prompt
    assert "Implements features" in prompt
    assert "product_manager" in prompt
    assert "Writes specs" in prompt
    # No tier column / no tier values in the prompt
    assert "Tier" not in prompt
    assert "yellow" not in prompt
    assert "green" not in prompt


def test_prompt_includes_step_number():
    prompt = build_capabilities_prompt(
        agents=[],
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
        agents=[],
        step_number=2,
        max_steps=10,
        prior_steps=prior,
    )
    assert "product_manager" in prompt
    assert "Spec written" in prompt


def test_prompt_no_prior_steps():
    prompt = build_capabilities_prompt(
        agents=[],
        step_number=1,
        max_steps=10,
    )
    assert "Prior Steps" not in prompt


def test_prompt_includes_available_actions():
    prompt = build_capabilities_prompt(
        agents=[],
        step_number=1,
        max_steps=10,
    )
    assert "delegate" in prompt
    assert "done" in prompt
    assert "escalate" in prompt
    assert "manage-agent" in prompt


def test_prompt_includes_constraints():
    """The capabilities block is org-agnostic: it must NOT bake in
    org-specific budget / jurisdictional / content thresholds (those live in
    each agent's role_guidance / system prompt, loaded from
    <runtime>/org/agents/<name>.md). The block must still:
      - render a step-budget line so the manager paces decisions, and
      - point the manager at the founder for out-of-scope work.
    """
    prompt = build_capabilities_prompt(
        agents=[],
        step_number=3,
        max_steps=10,
    )
    # Step budget must be rendered.
    assert "step 3" in prompt.lower()
    assert "10" in prompt
    # Generic escalation pointer to the founder must remain.
    assert "founder" in prompt.lower()
    # Org-specific HK/Macau constraints must NOT be inlined into the
    # org-agnostic capabilities block (regression guard for the cleanup).
    assert "$200" not in prompt
    assert "HK/Macau" not in prompt
    assert "PCI" not in prompt
    assert "PDPO" not in prompt


def test_prompt_frames_json_as_mandatory():
    """The prompt must clearly mark JSON-only output as non-negotiable.

    Regression for TASK-013 / TASK-016: EH wrote prose "Delegating to X..."
    and the orchestrator silently approved. The prompt was partly at fault
    — it asked for JSON in one sentence, buried among other content.
    """
    prompt = build_capabilities_prompt(
        agents=[],
        step_number=1,
        max_steps=10,
    )
    # Mandatory language — something unmistakable, not a soft request.
    lowered = prompt.lower()
    assert "mandatory" in lowered or "must" in lowered or "required" in lowered
    # The failure mode must be stated: prose = task fails / escalates.
    assert "prose" in lowered or "plain text" in lowered or "not json" in lowered


def test_prompt_shows_wrong_example():
    """The prompt must show a concrete 'WRONG' example of prose so EH sees
    exactly the failure mode to avoid, not just the right answer."""
    prompt = build_capabilities_prompt(
        agents=[],
        step_number=1,
        max_steps=10,
    )
    # "WRONG" or "BAD" label on a prose example — so the LLM cannot miss
    # the contrast between acceptable and unacceptable output.
    assert ("WRONG" in prompt) or ("BAD" in prompt) or ("DO NOT" in prompt)


def test_prompt_warns_that_prose_escalates():
    """The prompt must tell the EH the actual consequence of writing prose:
    the task escalates to the founder. Consequence-framing is stronger than
    "please write JSON"."""
    prompt = build_capabilities_prompt(
        agents=[],
        step_number=1,
        max_steps=10,
    )
    lowered = prompt.lower()
    assert "escalate" in lowered and (
        "prose" in lowered or "plain text" in lowered or "non-json" in lowered
        or "not json" in lowered
    )

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


def test_self_only_prompt_omits_roster_and_names_self():
    from runtime.orchestrator.capabilities import build_capabilities_prompt
    p = build_capabilities_prompt(
        agents=[], step_number=1, max_steps=10,
        manager_name="dev_agent", self_only=True,
    )
    assert "Available Agents" not in p          # no team roster
    assert "dev_agent" in p                       # delegate-to-self target named
    assert '"action": "delegate"' in p
    assert '"action": "done"' in p
    assert '"action": "escalate"' in p


# ── Fan-out capabilities prompt tests ──────────────────────────────────────

def test_manager_prompt_advertises_fanout_shape():
    """The manager prompt must include the fanout/parallel decision shape
    so managers know how to invoke native fan-out from their capabilities block."""
    p = build_capabilities_prompt(
        agents=[{"name": "dev_agent", "description": "Implements features"}],
        step_number=1, max_steps=10,
        manager_name="engineering_head",
    )
    assert "fanout" in p
    assert "parallel" in p  # alias mentioned
    assert '"action": "fanout"' in p
    assert '"children":' in p


def test_manager_prompt_shows_fanout_required_fields():
    """The manager prompt must show the required fan-out fields and constraints."""
    p = build_capabilities_prompt(
        agents=[{"name": "dev_agent", "description": "Implements features"}],
        step_number=1, max_steps=10,
        manager_name="engineering_head",
    )
    # Required fields advertised
    assert "width_cap_ack" in p
    assert "join_summary" in p


def test_manager_prompt_shows_fanout_constraints():
    """The manager prompt must surface fan-out constraints including
    THR-056 option 3 mutating fan-out."""
    p = build_capabilities_prompt(
        agents=[{"name": "dev_agent", "description": "Implements features"}],
        step_number=1, max_steps=10,
        manager_name="engineering_head",
    )
    # Width constraints
    assert "2" in p and "8" in p  # width range mentioned
    assert "hard cap" in p.lower() or "rejects" in p.lower()
    # width_cap_ack must match
    assert "exactly match" in p.lower() or "exact" in p.lower()
    # NO review_required gate (founder ruling THR-012 msg 129/131)
    assert "review_required" not in p
    # The text says "NO fan-out review gate" — ensure it doesn't say
    # "enters founder review_required" or similar gates exist
    assert "enters founder" not in p.lower()
    assert "resource limit" in p.lower()
    # Phase 2 pipeline — per-child then/expect_verdict now supported
    assert "pipeline" in p  # pipeline mention
    assert "then" in p.lower() or "expect_verdict" in p  # should mention these fields
    # Parent parks in_progress(delegated)
    assert "delegated" in p.lower()
    # Wakes once when all children terminal
    assert "terminal" in p.lower()
    # THR-056 option 3: mutating fan-out — children targeted at team managers
    # are decision-capable
    assert "team manager" in p.lower()
    assert "mutating fan-out" in p.lower() or "decision-capable" in p.lower()
    assert "implementation subtrees" in p.lower() or "implementation children" in p.lower()


def test_manager_prompt_no_longer_claims_only_delegate_done_escalate():
    """The manager prompt must NOT claim the decision field is limited to
    only delegate/done/escalate — the contradiction the reviewer flagged."""
    p = build_capabilities_prompt(
        agents=[{"name": "dev_agent", "description": "Implements features"}],
        step_number=1, max_steps=10,
        manager_name="engineering_head",
    )
    # The old contradictory phrase must be gone
    assert "still has to be one of delegate/done/escalate" not in p
    assert "one of the three decision shapes" not in p
    # The manage-agent section must now list fanout alongside the others
    # Find the manage-agent section
    manage_section_start = p.find("manage-agent")
    assert manage_section_start != -1
    # Within a reasonable range after manage-agent, the decision field listing
    # must include fanout
    tail = p[manage_section_start:]
    assert "fanout" in tail.lower()


def test_self_only_prompt_advertises_fanout_as_unavailable():
    """The self-only prompt must mention fanout exists but is unavailable
    in self-only mode, so non-manager owners are not confused by its absence."""
    p = build_capabilities_prompt(
        agents=[], step_number=1, max_steps=10,
        manager_name="dev_agent", self_only=True,
    )
    assert "fanout" in p.lower()
    assert "parallel" in p.lower()  # alias mentioned
    # Must say it's NOT available in self-only mode
    assert "not available" in p.lower() or "NOT available" in p
    assert "team-manager-only" in p.lower() or "team manager" in p.lower()

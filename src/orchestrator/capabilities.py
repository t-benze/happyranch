from __future__ import annotations

from src.models import AgentName, PerformanceTier, StepRecord

AGENT_DESCRIPTIONS: dict[AgentName, str] = {
    AgentName.PRODUCT_MANAGER: "Writes feature specs, triages bugs, prioritizes roadmap",
    AgentName.DEV_AGENT: "Implements features, fixes bugs, writes code",
    AgentName.PAYMENT_AGENT: "Drafts payment change proposals with compliance considerations",
}


def build_capabilities_prompt(
    brief: str,
    agent_tiers: dict[AgentName, PerformanceTier],
    step_number: int,
    max_steps: int,
    prior_steps: list[StepRecord] | None = None,
) -> str:
    """Build the prompt sent to the Engineering Head for each decision step."""
    sections = [
        "# Task\n",
        brief.strip(),
        "\n## Your Orchestration Capabilities\n",
        "You are the Engineering Head. Analyze the task and decide what to do next.",
        "You can explore the codebase, analyze code, and do research yourself in this session.",
        "You can also delegate work to your team.\n",
        "### Available Agents\n",
        "| Agent | Role | Tier |",
        "|-------|------|------|",
    ]

    for agent, description in AGENT_DESCRIPTIONS.items():
        tier = agent_tiers.get(agent, PerformanceTier.GREEN)
        sections.append(f"| {agent} | {description} | {tier.value} |")

    sections.extend([
        "\n### Available Actions\n",
        "Return your decision as a JSON object in your completion report's `output_summary` field.\n",
        '**delegate** -- Assign work to an agent:',
        "```json",
        '{"action": "delegate", "agent": "<agent_name>", "prompt": "<detailed instructions for the agent>"}',
        "```\n",
        "**done** -- Task is complete (or you handled it yourself):",
        "```json",
        '{"action": "done", "summary": "<what was accomplished or your findings>"}',
        "```\n",
        "**escalate** -- Needs founder attention:",
        "```json",
        '{"action": "escalate", "reason": "<why this needs escalation>"}',
        "```\n",
        "### Constraints\n",
        f"- This is step {step_number} of maximum {max_steps}",
        "- Budget authority: auto-approved up to $200 USD single / $100 USD monthly recurring",
        "- Any content about China/HK/Macau political relations must escalate to founder",
    ])

    if prior_steps:
        sections.append("\n### Prior Steps\n")
        for step in prior_steps:
            status = "OK" if step.success else "FAILED"
            sections.append(
                f"**Step {step.step_number}** [{step.agent}] {step.action} -- "
                f"{step.result_summary} ({status})"
            )

    return "\n".join(sections)

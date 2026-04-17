from __future__ import annotations

from src.models import PerformanceTier, StepRecord


def build_capabilities_prompt(
    brief: str,
    agents: list[dict],
    step_number: int,
    max_steps: int,
    prior_steps: list[StepRecord] | None = None,
) -> str:
    """Build the prompt sent to the Engineering Head for each decision step.

    ``agents`` is a list of dicts with keys: name, description, tier.
    """
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

    for agent in agents:
        sections.append(f"| {agent['name']} | {agent['description']} | {agent['tier']} |")

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
        "**manage-agent** -- Enroll, update, or terminate an agent:",
        "Use the manage-agent skill to write a JSON file and call `opc manage-agent --from-file <path>`.",
        "Enrollment requires founder approval before the agent becomes active.\n",
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

from __future__ import annotations

from runtime.models import StepRecord


def build_capabilities_prompt(
    agents: list[dict],
    step_number: int,
    max_steps: int,
    prior_steps: list[StepRecord] | None = None,
    manager_name: str = "team_manager",
    self_only: bool = False,
) -> str:
    """Build the prompt sent to a task owner for each decision step.

    The task brief is NOT rendered here — it is carried in the outer
    ``Parameters.brief`` block built by ``Orchestrator._build_agent_prompt``.
    Re-emitting it here would duplicate the brief in every manager spawn.

    ``agents`` is a list of dicts with keys: name, description.
    ``manager_name`` is the agent name of the calling manager; it is used to
    address the manager by role in the prompt (e.g. "You are the Content Manager.").
    The default value is a generic placeholder for tests; live callers in
    ``run_step._build_agent_prompt`` always pass the actual manager name.
    ``self_only`` suppresses the team roster and restricts delegation to self.
    """
    pretty = manager_name.replace("_", " ").title()  # "content_manager" -> "Content Manager"
    if self_only:
        me = manager_name
        sections = [
            "## Your Orchestration Capabilities\n",
            f"You own this task ({me}). You can do the work yourself in this "
            "session, OR break it into a sequence of sub-tasks that YOU "
            "execute — each sub-task is a fresh session you'll be woken from "
            "when it finishes, so you can decide the next step with a clean "
            "context.\n",
            "### Response Format (MANDATORY)\n",
            "Your completion payload MUST include a top-level `decision` field "
            "(a single JSON object). If you omit it, the task escalates to the "
            "founder — the orchestrator will NOT infer intent from prose.\n",
            "Choose EXACTLY ONE shape:\n",
            "**delegate** -- spawn the next sub-task (assigned to YOURSELF):",
            "```json",
            f'{{"action": "delegate", "agent": "{me}", "prompt": "<instructions for the next sub-task>"}}',
            "```",
            "The `agent` MUST be yourself "
            f"(`{me}`) — you may only delegate sub-tasks to yourself.\n",
            "**done** -- the whole task is complete:",
            "```json",
            '{"action": "done", "summary": "<what was accomplished>"}',
            "```\n",
            "**escalate** -- needs founder attention:",
            "```json",
            '{"action": "escalate", "reason": "<why>"}',
            "```\n",
            "**fanout** (`parallel` accepted as alias) -- NOT available in "
            "self-only mode. "
            "Fan-out (spawning N parallel sub-tasks across multiple agents, "
            "width 2–8, read-only Phase 1) is a team-manager-only capability. "
            "If you need parallel work, delegate sequentially or escalate to "
            "your parent.\n",
            "### Constraints\n",
            f"- This is step {step_number} of maximum {max_steps}",
            "- Org-specific authority limits come from your role_guidance / "
            "system prompt — escalate anything outside them.",
        ]
        if prior_steps:
            sections.append("\n### Prior Steps\n")
            for step in prior_steps:
                status = "OK" if step.success else "FAILED"
                sections.append(
                    f"**Step {step.step_number}** [{step.agent}] {step.action} -- "
                    f"{step.result_summary} ({status})"
                )
        return "\n".join(sections)
    sections = [
        "## Your Orchestration Capabilities\n",
        f"You are the {pretty}. Analyze the brief above and decide what to do next.",
        "You can explore the codebase, analyze code, and do research yourself in this session.",
        "You can also delegate work to your team.\n",
        "### Available Agents\n",
        "| Agent | Role |",
        "|-------|------|",
    ]

    for agent in agents:
        sections.append(f"| {agent['name']} | {agent['description']} |")

    sections.extend([
        "\n### Response Format (MANDATORY)\n",
        "Your completion payload MUST include a top-level `decision` field "
        "containing a single JSON object: the structured next-step action for "
        "the orchestrator. The `summary` field is prose describing what "
        "happened (for audit logs and founder visibility); the `decision` "
        "field is what the orchestrator acts on.\n",
        "If you omit `decision`, your task will escalate to the founder — "
        "the orchestrator will NOT guess intent from your prose `summary`. "
        "That guardrail exists because prose-as-decision was the root cause "
        "of past silent-approve bugs.\n",
        "Choose EXACTLY ONE `decision` shape below:\n",
        '**delegate** -- Assign work to an agent:',
        "```json",
        '{"action": "delegate", "agent": "<agent_name>", "prompt": "<detailed instructions for the agent>"}',
        "```",
        "",
        "Or, to declare a multi-leg workflow chain (auto-advances without consuming",
        "orchestration steps):",
        "",
        "```json",
        '{"action": "delegate", "agent": "dev_agent", "prompt": "Build it",',
        ' "then": [',
        '   {"agent": "senior_dev",  "prompt": "Code-review the PR.",  "expect_verdict": "APPROVE"},',
        '   {"agent": "qa_engineer", "prompt": "QA the PR.",            "expect_verdict": "PASS"}',
        ' ]}',
        "```",
        "",
        "Each leg in `then` has `agent`, `prompt`, and optional `expect_verdict`.",
        "The orchestrator auto-advances on verdict match; mismatches, blocked subtasks,",
        "or final-leg matches wake you. Full shape: `protocol/00-completion-contract.md`.\n",
        "**done** -- Task is complete (or you handled it yourself):",
        "```json",
        '{"action": "done", "summary": "<what was accomplished or your findings>"}',
        "```\n",
        "**escalate** -- Needs founder attention:",
        "```json",
        '{"action": "escalate", "reason": "<why this needs escalation>"}',
        "```\n",
        "**fanout** (`parallel` accepted as alias) -- Spawn N parallel sub-tasks "
        "(Phase 2 with pipeline, team-manager only):\n",
        "```json\n",
        '{"action": "fanout",',
        ' "children": [',
        '   {"agent": "dev_agent",    "prompt": "Investigate module A"},',
        '   {"agent": "qa_engineer",   "prompt": "Investigate module B"},',
        '   {"agent": "product_manager", "prompt": "Investigate module C"}',
        ' ],',
        ' "width_cap_ack": 3,',
        ' "join_summary": "Synthesize findings into a unified plan"}',
        "```\n",
        "Constraints:\n",
        "- Width: 2\u20138 children (hard cap; single-child or >8 rejects at parse-time).\n",
        "- `width_cap_ack` MUST exactly match the child count.\n",
        "- `join_summary` is optional prose for the rejoin prompt.\n",
        "- Width > 4 enters founder `review_required` before children spawn.\n",
        "- Each child may optionally carry `then` / `expect_verdict` (pipeline carrier — Phase 2); "
        "pipeline children stay read-only in this slice.\n",
        "- Children spawn through the normal child/session path; the parent "
        "parks `in_progress(delegated)` with `active_fanout` set and wakes "
        "once when all children are terminal.\n",
        "- Action token: `fanout` (the alias `parallel` is also accepted).\n",
        "#### Example completion payload\n",
        "Write `/tmp/completion-<task_id>.json` with BOTH fields set:",
        "```json",
        "{",
        '  "task_id": "TASK-XXX",',
        '  "session_id": "<sid>",',
        f'  "agent": "{manager_name}",',
        '  "status": "completed",',
        '  "confidence": 90,',
        '  "summary": "Triaged the request and staged implementation for the subtask.",',
        '  "decision": {"action": "delegate", "agent": "<worker_agent_name>", "prompt": "<detailed instructions>"}',
        "}",
        "```\n",
        "The `summary` is prose — it can describe what you did, what you found, "
        "or why you're delegating. The `decision` is what the orchestrator executes.\n",
        "#### WRONG — no `decision` field, task will escalate\n",
        "Prose-only completion (the `decision` field is missing entirely):",
        "```json",
        "{",
        '  "task_id": "TASK-XXX",',
        '  "session_id": "<sid>",',
        f'  "agent": "{manager_name}",',
        '  "status": "completed",',
        '  "summary": "Cleanup done — closed superseded items."',
        "}",
        "```",
        "DO NOT omit `decision`. Even for a direct-action cleanup you ran "
        'yourself this step, set `decision` to `{"action": "done", "summary": '
        '"<recap>"}`. The orchestrator will NOT infer "done" from your prose '
        "`summary` — that guardrail exists because silent prose-to-decision "
        "inference masked past delegation bugs (TASK-013 / TASK-016).\n",
        "**manage-agent** -- Enroll, update, or terminate an agent:",
        "Use the manage-agent skill to write a JSON file and call `happyranch manage-agent --from-file <path>`.",
        "Enrollment requires founder approval before the agent becomes active. "
        "This is a side-channel capability, not one of the decision shapes above — "
        "your `decision` field must be one of delegate/done/escalate/fanout.\n",
        "### Constraints\n",
        f"- This is step {step_number} of maximum {max_steps}",
        "- Org-specific authority limits (budget, jurisdictional, content)"
        " come from your role_guidance / system prompt — not this capabilities"
        " block. Escalate to the founder anything outside the bounds your"
        " role describes.",
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

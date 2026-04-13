from __future__ import annotations

from src.models import AgentName, PerformanceTier, TaskStep, TaskType


def _get_tier(
    agent: AgentName, tiers: dict[AgentName, PerformanceTier]
) -> PerformanceTier:
    return tiers.get(agent, PerformanceTier.GREEN)


def build_task_chain(
    task_type: TaskType,
    agent_tiers: dict[AgentName, PerformanceTier],
) -> list[TaskStep]:
    """Build an ordered list of task steps based on task type and agent tiers."""
    if task_type == TaskType.IMPLEMENT_FEATURE:
        return _implement_feature_chain(agent_tiers)
    elif task_type == TaskType.BUG_FIX:
        return _bug_fix_chain(agent_tiers)
    elif task_type == TaskType.PAYMENT_CHANGE:
        return _payment_change_chain(agent_tiers)
    else:
        raise ValueError(f"Unknown task type: {task_type}")


def _implement_feature_chain(
    tiers: dict[AgentName, PerformanceTier],
) -> list[TaskStep]:
    dev_tier = _get_tier(AgentName.DEV_AGENT, tiers)

    chain = [
        TaskStep(
            agent=AgentName.PRODUCT_MANAGER,
            action="write_spec",
            description="Write feature specification with acceptance criteria",
        ),
        TaskStep(
            agent=AgentName.DEV_AGENT,
            action="implement",
            description="Implement feature based on spec",
        ),
    ]

    if dev_tier == PerformanceTier.YELLOW:
        chain.extend([
            TaskStep(
                agent=AgentName.ENGINEERING_HEAD,
                action="pre_review",
                description="Pre-review implementation before final review",
            ),
            TaskStep(
                agent=AgentName.DEV_AGENT,
                action="revise",
                description="Revise implementation based on pre-review feedback",
            ),
        ])
    elif dev_tier == PerformanceTier.RED:
        chain.extend([
            TaskStep(
                agent=AgentName.ENGINEERING_HEAD,
                action="pre_review",
                description="Pre-review implementation before final review",
            ),
            TaskStep(
                agent=AgentName.DEV_AGENT,
                action="revise",
                description="Revise implementation based on pre-review feedback",
            ),
            TaskStep(
                agent=AgentName.ENGINEERING_HEAD,
                action="second_review",
                description="Second review (red tier requires extra scrutiny)",
            ),
        ])

    chain.append(
        TaskStep(
            agent=AgentName.ENGINEERING_HEAD,
            action="review",
            description="Final review of implementation",
        )
    )
    return chain


def _bug_fix_chain(
    tiers: dict[AgentName, PerformanceTier],
) -> list[TaskStep]:
    dev_tier = _get_tier(AgentName.DEV_AGENT, tiers)

    chain = [
        TaskStep(
            agent=AgentName.PRODUCT_MANAGER,
            action="triage",
            description="Triage bug: severity, reproduction steps, priority",
        ),
        TaskStep(
            agent=AgentName.DEV_AGENT,
            action="fix",
            description="Fix bug based on triage report",
        ),
    ]

    if dev_tier == PerformanceTier.YELLOW:
        chain.extend([
            TaskStep(
                agent=AgentName.ENGINEERING_HEAD,
                action="pre_review",
                description="Pre-review fix before final verification",
            ),
            TaskStep(
                agent=AgentName.DEV_AGENT,
                action="revise",
                description="Revise fix based on pre-review feedback",
            ),
        ])
    elif dev_tier == PerformanceTier.RED:
        chain.extend([
            TaskStep(
                agent=AgentName.ENGINEERING_HEAD,
                action="pre_review",
                description="Pre-review fix before final verification",
            ),
            TaskStep(
                agent=AgentName.DEV_AGENT,
                action="revise",
                description="Revise fix based on pre-review feedback",
            ),
            TaskStep(
                agent=AgentName.ENGINEERING_HEAD,
                action="second_review",
                description="Second review (red tier requires extra scrutiny)",
            ),
        ])

    chain.append(
        TaskStep(
            agent=AgentName.ENGINEERING_HEAD,
            action="review",
            description="Verify bug fix",
        )
    )
    return chain


def _payment_change_chain(
    tiers: dict[AgentName, PerformanceTier],
) -> list[TaskStep]:
    payment_tier = _get_tier(AgentName.PAYMENT_AGENT, tiers)

    chain = [
        TaskStep(
            agent=AgentName.PAYMENT_AGENT,
            action="draft_proposal",
            description="Draft payment change proposal with compliance considerations",
        ),
    ]

    if payment_tier == PerformanceTier.YELLOW:
        chain.extend([
            TaskStep(
                agent=AgentName.ENGINEERING_HEAD,
                action="pre_review",
                description="Pre-review payment proposal",
            ),
            TaskStep(
                agent=AgentName.PAYMENT_AGENT,
                action="revise",
                description="Revise proposal based on pre-review feedback",
            ),
        ])
    elif payment_tier == PerformanceTier.RED:
        chain.extend([
            TaskStep(
                agent=AgentName.ENGINEERING_HEAD,
                action="pre_review",
                description="Pre-review payment proposal",
            ),
            TaskStep(
                agent=AgentName.PAYMENT_AGENT,
                action="revise",
                description="Revise proposal based on pre-review feedback",
            ),
            TaskStep(
                agent=AgentName.ENGINEERING_HEAD,
                action="second_review",
                description="Second review (red tier requires extra scrutiny)",
            ),
        ])

    chain.append(
        TaskStep(
            agent=AgentName.ENGINEERING_HEAD,
            action="review",
            description="Review payment change proposal",
        )
    )
    return chain

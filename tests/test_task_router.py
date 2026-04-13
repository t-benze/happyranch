from src.models import AgentName, PerformanceTier, TaskType
from src.orchestrator.task_router import build_task_chain


def test_implement_feature_all_green():
    tiers = {
        AgentName.PRODUCT_MANAGER: PerformanceTier.GREEN,
        AgentName.DEV_AGENT: PerformanceTier.GREEN,
    }
    chain = build_task_chain(TaskType.IMPLEMENT_FEATURE, tiers)
    agents = [step.agent for step in chain]
    assert agents == [
        AgentName.PRODUCT_MANAGER,
        AgentName.DEV_AGENT,
        AgentName.ENGINEERING_HEAD,
    ]


def test_implement_feature_dev_yellow():
    tiers = {
        AgentName.PRODUCT_MANAGER: PerformanceTier.GREEN,
        AgentName.DEV_AGENT: PerformanceTier.YELLOW,
    }
    chain = build_task_chain(TaskType.IMPLEMENT_FEATURE, tiers)
    agents = [step.agent for step in chain]
    assert agents == [
        AgentName.PRODUCT_MANAGER,
        AgentName.DEV_AGENT,
        AgentName.ENGINEERING_HEAD,
        AgentName.DEV_AGENT,
        AgentName.ENGINEERING_HEAD,
    ]


def test_implement_feature_dev_red():
    tiers = {
        AgentName.PRODUCT_MANAGER: PerformanceTier.GREEN,
        AgentName.DEV_AGENT: PerformanceTier.RED,
    }
    chain = build_task_chain(TaskType.IMPLEMENT_FEATURE, tiers)
    agents = [step.agent for step in chain]
    assert agents == [
        AgentName.PRODUCT_MANAGER,
        AgentName.DEV_AGENT,
        AgentName.ENGINEERING_HEAD,
        AgentName.DEV_AGENT,
        AgentName.ENGINEERING_HEAD,
        AgentName.ENGINEERING_HEAD,
    ]


def test_bug_fix_all_green():
    tiers = {
        AgentName.PRODUCT_MANAGER: PerformanceTier.GREEN,
        AgentName.DEV_AGENT: PerformanceTier.GREEN,
    }
    chain = build_task_chain(TaskType.BUG_FIX, tiers)
    agents = [step.agent for step in chain]
    assert agents == [
        AgentName.PRODUCT_MANAGER,
        AgentName.DEV_AGENT,
        AgentName.ENGINEERING_HEAD,
    ]


def test_payment_change_all_green():
    tiers = {
        AgentName.PAYMENT_AGENT: PerformanceTier.GREEN,
    }
    chain = build_task_chain(TaskType.PAYMENT_CHANGE, tiers)
    agents = [step.agent for step in chain]
    assert agents == [
        AgentName.PAYMENT_AGENT,
        AgentName.ENGINEERING_HEAD,
    ]


def test_default_tier_is_green():
    chain = build_task_chain(TaskType.IMPLEMENT_FEATURE, {})
    agents = [step.agent for step in chain]
    assert agents == [
        AgentName.PRODUCT_MANAGER,
        AgentName.DEV_AGENT,
        AgentName.ENGINEERING_HEAD,
    ]


def test_step_actions_are_descriptive():
    chain = build_task_chain(TaskType.IMPLEMENT_FEATURE, {})
    assert chain[0].action == "write_spec"
    assert chain[1].action == "implement"
    assert chain[2].action == "review"

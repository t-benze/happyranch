# Engineering Head-Driven Orchestration

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace hardcoded task chains with an Engineering Head-driven orchestration loop where the EH analyzes each task, decides the approach (handle directly, delegate, or escalate), and drives the workflow step-by-step.

**Architecture:** The orchestrator becomes an execution engine. It sends the user's brief plus a capabilities description to the Engineering Head, who returns a structured `NextStep` JSON decision. The orchestrator executes that decision (run a delegate agent, mark done, or escalate), feeds the result back to the EH, and loops until the EH says "done" or a max-steps guardrail fires. This replaces the old model where the human picked a task type and the orchestrator built a fixed chain.

**Tech Stack:** Python 3.13, Pydantic v2, SQLite, Claude Code CLI

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/models.py` | Modify | Add `NextStep`, `StepRecord` models; add `TaskType.GENERAL` |
| `src/config.py` | Modify | Add `max_orchestration_steps` setting |
| `src/orchestrator/capabilities.py` | Create | Build capabilities prompt for EH decision sessions |
| `src/orchestrator/orchestrator.py` | Rewrite | EH-driven loop replacing chain-based execution |
| `src/infrastructure/audit_logger.py` | Modify | Add `log_orchestration_step` method |
| `src/cli.py` | Modify | Make `--task` optional, default to `general` |
| `src/orchestrator/task_router.py` | Delete | Replaced by EH judgment |
| `src/orchestrator/revision_loop.py` | Delete | Replaced by orchestration loop max-steps guardrail |
| `tests/test_models.py` | Modify | Add tests for NextStep, StepRecord |
| `tests/test_capabilities.py` | Create | Tests for capabilities prompt builder |
| `tests/test_orchestrator.py` | Rewrite | Tests for new EH-driven loop |
| `tests/test_cli.py` | Modify | Tests for optional --task flag |
| `tests/test_task_router.py` | Delete | No longer applicable |
| `tests/test_revision_loop.py` | Delete | No longer applicable |

---

### Task 1: Add new models and config

**Files:**
- Modify: `src/models.py:19-24` (TaskType enum), append new models after line 73
- Modify: `src/config.py:43` (add setting)
- Test: `tests/test_models.py`

- [ ] **Step 1: Write tests for NextStep and StepRecord**

Add to the end of `tests/test_models.py`:

```python
from src.models import NextStep, StepRecord


def test_next_step_delegate():
    step = NextStep(action="delegate", agent="dev_agent", prompt="Implement feature X")
    assert step.action == "delegate"
    assert step.agent == "dev_agent"
    assert step.prompt == "Implement feature X"


def test_next_step_done():
    step = NextStep(action="done", summary="Explored the codebase, found no issues")
    assert step.action == "done"
    assert step.summary == "Explored the codebase, found no issues"


def test_next_step_escalate():
    step = NextStep(action="escalate", reason="Budget exceeds $200")
    assert step.action == "escalate"
    assert step.reason == "Budget exceeds $200"


def test_step_record():
    record = StepRecord(
        step_number=1,
        agent="dev_agent",
        action="delegate: implement feature",
        result_summary="Feature implemented with 3 tests",
        success=True,
    )
    assert record.step_number == 1
    assert record.success is True


def test_task_type_general():
    from src.models import TaskType
    assert TaskType.GENERAL == "general"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py -v -k "next_step or step_record or general"`
Expected: FAIL — `NextStep`, `StepRecord`, `TaskType.GENERAL` not defined

- [ ] **Step 3: Add NextStep, StepRecord, and TaskType.GENERAL to models.py**

In `src/models.py`, add `GENERAL` to the `TaskType` enum:

```python
class TaskType(StrEnum):
    IMPLEMENT_FEATURE = "implement_feature"
    BUG_FIX = "bug_fix"
    PAYMENT_CHANGE = "payment_change"
    GENERAL = "general"
```

Append after the `TaskStep` class (after line 73):

```python
class NextStep(BaseModel):
    """Decision returned by the Engineering Head for what the orchestrator should do next."""
    action: str  # "delegate", "done", "escalate"
    agent: str | None = None
    prompt: str | None = None
    summary: str | None = None
    reason: str | None = None


class StepRecord(BaseModel):
    """Record of a completed orchestration step, shown to EH as history."""
    step_number: int
    agent: str
    action: str
    result_summary: str
    success: bool
```

- [ ] **Step 4: Add max_orchestration_steps to config.py**

In `src/config.py`, add after `session_timeout_seconds` (line 43):

```python
    # Orchestration loop
    max_orchestration_steps: int = 10
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/models.py src/config.py tests/test_models.py
git commit -m "feat: add NextStep, StepRecord models and max_orchestration_steps config"
```

---

### Task 2: Create capabilities prompt builder

**Files:**
- Create: `src/orchestrator/capabilities.py`
- Create: `tests/test_capabilities.py`

- [ ] **Step 1: Write tests for the capabilities prompt builder**

Create `tests/test_capabilities.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_capabilities.py -v`
Expected: FAIL — `capabilities` module doesn't exist

- [ ] **Step 3: Implement capabilities.py**

Create `src/orchestrator/capabilities.py`:

```python
from __future__ import annotations

from src.models import AgentName, PerformanceTier, StepRecord

AGENT_DESCRIPTIONS: dict[str, str] = {
    "product_manager": "Writes feature specs, triages bugs, prioritizes roadmap",
    "dev_agent": "Implements features, fixes bugs, writes code",
    "payment_agent": "Drafts payment change proposals with compliance considerations",
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

    for agent_name, description in AGENT_DESCRIPTIONS.items():
        agent_enum = AgentName(agent_name)
        tier = agent_tiers.get(agent_enum, PerformanceTier.GREEN)
        sections.append(f"| {agent_name} | {description} | {tier.value} |")

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_capabilities.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/capabilities.py tests/test_capabilities.py
git commit -m "feat: capabilities prompt builder for EH decision sessions"
```

---

### Task 3: Add orchestration step logging to AuditLogger

**Files:**
- Modify: `src/infrastructure/audit_logger.py:89` (append method)
- Test: `tests/test_audit_logger.py`

- [ ] **Step 1: Write test for log_orchestration_step**

Append to `tests/test_audit_logger.py`:

```python
def test_log_orchestration_step(db):
    logger = AuditLogger(db)
    logger.log_orchestration_step("TASK-001", step_number=1, decision={
        "action": "delegate",
        "agent": "dev_agent",
        "prompt": "Implement feature",
    })
    logs = db.get_audit_logs("TASK-001")
    assert len(logs) == 1
    assert logs[0]["action"] == "orchestration_step"
    assert logs[0]["payload"]["step_number"] == 1
    assert logs[0]["payload"]["decision"]["action"] == "delegate"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_audit_logger.py::test_log_orchestration_step -v`
Expected: FAIL — `log_orchestration_step` not defined

- [ ] **Step 3: Add log_orchestration_step to AuditLogger**

Append to `src/infrastructure/audit_logger.py` after `log_cross_audit_stub`:

```python
    def log_orchestration_step(
        self, task_id: str, step_number: int, decision: dict
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent="orchestrator",
            action="orchestration_step",
            payload={"step_number": step_number, "decision": decision},
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_audit_logger.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/audit_logger.py tests/test_audit_logger.py
git commit -m "feat: add orchestration step logging to AuditLogger"
```

---

### Task 4: Refactor Orchestrator to EH-driven loop

This is the core change. The orchestrator stops building hardcoded chains and instead enters a loop where the Engineering Head makes all routing decisions.

**Files:**
- Rewrite: `src/orchestrator/orchestrator.py`
- Rewrite: `tests/test_orchestrator.py`

- [ ] **Step 1: Write tests for the new orchestrator**

Replace `tests/test_orchestrator.py` entirely with:

```python
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.config import Settings
from src.infrastructure.database import Database
from src.models import (
    AgentName,
    CompletionReport,
    TaskStatus,
    TaskType,
)
from src.orchestrator.executor import ExecutorResult
from src.orchestrator.orchestrator import Orchestrator


def _make_eh_decision(task_id: str, decision: dict) -> ExecutorResult:
    """Simulate the Engineering Head returning a NextStep decision."""
    return ExecutorResult(
        success=True,
        report=CompletionReport(
            task_id=task_id,
            agent="engineering_head",
            status="completed",
            confidence=90,
            output_summary=json.dumps(decision),
        ),
        duration_seconds=30,
        session_id="sess-eh",
    )


def _make_agent_result(task_id: str, agent: str, summary: str = "Work done") -> ExecutorResult:
    """Simulate a worker agent completing its task."""
    return ExecutorResult(
        success=True,
        report=CompletionReport(
            task_id=task_id,
            agent=agent,
            status="completed",
            confidence=85,
            output_summary=summary,
        ),
        duration_seconds=60,
        session_id="sess-worker",
    )


def _make_failed_result(task_id: str) -> ExecutorResult:
    return ExecutorResult(
        success=False,
        report=None,
        duration_seconds=10,
        session_id="sess-fail",
        error="Session failed",
    )


@pytest.fixture
def orchestrator(test_settings):
    db = Database(test_settings.get_db_path())
    return Orchestrator(db=db, settings=test_settings)


def _setup_workspaces(settings):
    """Create workspace dirs with recent_tasks.md for all agents."""
    for agent in AgentName:
        ws = settings.get_workspaces_dir() / agent.value
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "recent_tasks.md").write_text(f"# Recent Tasks: {agent.value}\n\n")


def test_create_task(orchestrator):
    task_id = orchestrator.create_task(TaskType.GENERAL, "Explore the codebase")
    assert task_id == "TASK-001"
    task = orchestrator._db.get_task(task_id)
    assert task.status == TaskStatus.PENDING
    assert task.brief == "Explore the codebase"


def test_create_task_with_type(orchestrator):
    task_id = orchestrator.create_task(TaskType.IMPLEMENT_FEATURE, "Add Alipay")
    task = orchestrator._db.get_task(task_id)
    assert task.type == TaskType.IMPLEMENT_FEATURE


@patch.object(Orchestrator, "_run_agent")
def test_eh_handles_directly(mock_run, orchestrator, test_settings):
    """EH explores and returns done on first step -- no delegation."""
    _setup_workspaces(test_settings)

    mock_run.return_value = _make_eh_decision("TASK-001", {
        "action": "done",
        "summary": "Explored the payment system. Refunds use Stripe API v3.",
    })

    task_id = orchestrator.create_task(TaskType.GENERAL, "How do refunds work?")
    result = orchestrator.run_task(task_id)

    assert result == "approved"
    assert mock_run.call_count == 1
    # Only EH was called, no workers
    call_agent = mock_run.call_args_list[0][0][1]
    assert call_agent == AgentName.ENGINEERING_HEAD


@patch.object(Orchestrator, "_run_agent")
def test_eh_delegates_then_done(mock_run, orchestrator, test_settings):
    """EH delegates to dev_agent, then approves the result."""
    _setup_workspaces(test_settings)

    call_count = 0

    def mock_side_effect(task_id, agent, prompt):
        nonlocal call_count
        call_count += 1
        if agent == AgentName.ENGINEERING_HEAD:
            if call_count == 1:
                return _make_eh_decision(task_id, {
                    "action": "delegate",
                    "agent": "dev_agent",
                    "prompt": "Implement the Alipay integration",
                })
            else:
                return _make_eh_decision(task_id, {
                    "action": "done",
                    "summary": "Dev agent implemented Alipay. Looks good.",
                })
        return _make_agent_result(task_id, agent.value)

    mock_run.side_effect = mock_side_effect

    task_id = orchestrator.create_task(TaskType.GENERAL, "Add Alipay support")
    result = orchestrator.run_task(task_id)

    assert result == "approved"
    assert call_count == 3  # EH decide + dev_agent work + EH decide


@patch.object(Orchestrator, "_run_agent")
def test_eh_multi_step_delegation(mock_run, orchestrator, test_settings):
    """EH delegates to PM, then to Dev, then approves."""
    _setup_workspaces(test_settings)

    eh_calls = 0

    def mock_side_effect(task_id, agent, prompt):
        nonlocal eh_calls
        if agent == AgentName.ENGINEERING_HEAD:
            eh_calls += 1
            if eh_calls == 1:
                return _make_eh_decision(task_id, {
                    "action": "delegate",
                    "agent": "product_manager",
                    "prompt": "Write a spec for Alipay integration",
                })
            elif eh_calls == 2:
                return _make_eh_decision(task_id, {
                    "action": "delegate",
                    "agent": "dev_agent",
                    "prompt": "Implement based on the spec",
                })
            else:
                return _make_eh_decision(task_id, {
                    "action": "done",
                    "summary": "Feature complete",
                })
        return _make_agent_result(task_id, agent.value)

    mock_run.side_effect = mock_side_effect

    task_id = orchestrator.create_task(TaskType.GENERAL, "Add Alipay support")
    result = orchestrator.run_task(task_id)

    assert result == "approved"
    task = orchestrator._db.get_task(task_id)
    assert task.status == TaskStatus.APPROVED


@patch.object(Orchestrator, "_run_agent")
def test_eh_escalates(mock_run, orchestrator, test_settings):
    _setup_workspaces(test_settings)

    mock_run.return_value = _make_eh_decision("TASK-001", {
        "action": "escalate",
        "reason": "This involves China/HK political content",
    })

    task_id = orchestrator.create_task(TaskType.GENERAL, "Write about HK relations")
    result = orchestrator.run_task(task_id)

    assert result == "escalated"
    task = orchestrator._db.get_task(task_id)
    assert task.status == TaskStatus.ESCALATED


@patch.object(Orchestrator, "_run_agent")
def test_max_steps_exceeded(mock_run, orchestrator, test_settings):
    """EH keeps delegating until max steps is reached."""
    _setup_workspaces(test_settings)
    # Override max steps to something small for testing
    orchestrator._settings.max_orchestration_steps = 3

    def mock_side_effect(task_id, agent, prompt):
        if agent == AgentName.ENGINEERING_HEAD:
            return _make_eh_decision(task_id, {
                "action": "delegate",
                "agent": "dev_agent",
                "prompt": "Try again",
            })
        return _make_agent_result(task_id, agent.value)

    mock_run.side_effect = mock_side_effect

    task_id = orchestrator.create_task(TaskType.GENERAL, "Infinite loop task")
    result = orchestrator.run_task(task_id)

    assert result == "escalated"
    task = orchestrator._db.get_task(task_id)
    assert task.status == TaskStatus.ESCALATED


@patch.object(Orchestrator, "_run_agent")
def test_eh_session_fails(mock_run, orchestrator, test_settings):
    """If the EH session itself fails, task is rejected."""
    _setup_workspaces(test_settings)
    mock_run.return_value = _make_failed_result("TASK-001")

    task_id = orchestrator.create_task(TaskType.GENERAL, "Do something")
    result = orchestrator.run_task(task_id)

    assert result == "rejected"


@patch.object(Orchestrator, "_run_agent")
def test_delegate_agent_fails_eh_sees_failure(mock_run, orchestrator, test_settings):
    """When a delegated agent fails, EH sees the failure and can decide."""
    _setup_workspaces(test_settings)

    call_count = 0

    def mock_side_effect(task_id, agent, prompt):
        nonlocal call_count
        call_count += 1
        if agent == AgentName.ENGINEERING_HEAD:
            if call_count == 1:
                return _make_eh_decision(task_id, {
                    "action": "delegate",
                    "agent": "dev_agent",
                    "prompt": "Implement feature",
                })
            else:
                # EH sees the failure and escalates
                return _make_eh_decision(task_id, {
                    "action": "escalate",
                    "reason": "Dev agent failed, need human help",
                })
        # dev_agent fails
        return _make_failed_result(task_id)

    mock_run.side_effect = mock_side_effect

    task_id = orchestrator.create_task(TaskType.GENERAL, "Add feature")
    result = orchestrator.run_task(task_id)

    assert result == "escalated"


@patch.object(Orchestrator, "_run_agent")
def test_eh_plain_text_output_treated_as_done(mock_run, orchestrator, test_settings):
    """If EH returns plain text (not JSON), treat it as done with that text."""
    _setup_workspaces(test_settings)

    mock_run.return_value = ExecutorResult(
        success=True,
        report=CompletionReport(
            task_id="TASK-001",
            agent="engineering_head",
            status="completed",
            confidence=85,
            output_summary="I explored the codebase. The payment module uses Stripe.",
        ),
        duration_seconds=30,
        session_id="sess-eh",
    )

    task_id = orchestrator.create_task(TaskType.GENERAL, "Explore payments")
    result = orchestrator.run_task(task_id)

    assert result == "approved"


@patch.object(Orchestrator, "_run_agent")
def test_audit_log_records_orchestration_steps(mock_run, orchestrator, test_settings):
    """Orchestration steps are logged to the audit trail."""
    _setup_workspaces(test_settings)

    mock_run.return_value = _make_eh_decision("TASK-001", {
        "action": "done",
        "summary": "All good",
    })

    task_id = orchestrator.create_task(TaskType.GENERAL, "Check something")
    orchestrator.run_task(task_id)

    logs = orchestrator._db.get_audit_logs(task_id)
    orch_steps = [l for l in logs if l["action"] == "orchestration_step"]
    assert len(orch_steps) == 1
    assert orch_steps[0]["payload"]["decision"]["action"] == "done"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_orchestrator.py -v`
Expected: FAIL — orchestrator still has old interface (`_run_agent` not defined)

- [ ] **Step 3: Rewrite orchestrator.py**

Replace `src/orchestrator/orchestrator.py` entirely with:

```python
from __future__ import annotations

import json
import logging

from src.config import Settings
from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database
from src.models import (
    AgentName,
    NextStep,
    StepRecord,
    TaskRecord,
    TaskStatus,
    TaskType,
)
from src.orchestrator.capabilities import build_capabilities_prompt
from src.orchestrator.context_builder import ContextBuilder
from src.orchestrator.executor import AgentExecutor, ExecutorResult
from src.orchestrator.performance_tracker import PerformanceTracker

logger = logging.getLogger(__name__)


_DEFAULT_SYSTEM_PROMPTS: dict[str, str] = {
    "engineering_head": (
        "You are the Engineering Head for a tourism services company. "
        "You decide how to handle incoming tasks -- doing work yourself, "
        "delegating to your team, or escalating to the founder. "
        "Follow the instructions in your task prompt for the expected response format."
    ),
    "product_manager": "You are the Product Manager. Write specs and triage bugs.",
    "dev_agent": "You are the Dev Agent. Implement features and fix bugs.",
    "payment_agent": "You are the Payment Agent. Draft payment change proposals with compliance considerations.",
}


class Orchestrator:
    def __init__(self, db: Database, settings: Settings) -> None:
        self._db = db
        self._settings = settings
        self._audit = AuditLogger(db)
        self._tracker = PerformanceTracker(db, settings)
        self._context = ContextBuilder(settings)
        self._executor = AgentExecutor(
            claude_cli_path=settings.claude_cli_path,
            permission_mode=settings.permission_mode,
        )

    def create_task(self, task_type: TaskType, brief: str) -> str:
        """Create a new task and persist it."""
        task_id = self._db.next_task_id()
        task = TaskRecord(id=task_id, type=task_type, brief=brief)
        self._db.insert_task(task)
        logger.info("Created task %s: %s", task_id, brief)
        return task_id

    def run_task(self, task_id: str) -> str:
        """Run a task through the EH-driven orchestration loop.

        The Engineering Head decides each step: delegate to a worker,
        handle directly, or escalate. The loop continues until the EH
        says "done", "escalate", or the max steps guardrail fires.
        """
        task = self._db.get_task(task_id)
        if task is None:
            raise ValueError(f"Task {task_id} not found")

        self._db.update_task(task_id, status=TaskStatus.IN_PROGRESS)
        tiers = self._tracker.get_all_tiers()
        prior_steps: list[StepRecord] = []
        max_steps = self._settings.max_orchestration_steps

        for step_num in range(1, max_steps + 1):
            # Ask the Engineering Head what to do next
            eh_prompt = build_capabilities_prompt(
                brief=task.brief,
                agent_tiers=tiers,
                step_number=step_num,
                max_steps=max_steps,
                prior_steps=prior_steps,
            )

            eh_result = self._run_agent(task_id, AgentName.ENGINEERING_HEAD, eh_prompt)

            if not eh_result.success:
                self._db.update_task(task_id, status=TaskStatus.REJECTED)
                return "rejected"

            self._log_step_result(task_id, eh_result)
            next_step = self._parse_next_step(eh_result)

            self._audit.log_orchestration_step(
                task_id, step_num, next_step.model_dump(exclude_none=True),
            )

            if next_step.action == "done":
                self._db.update_task(task_id, status=TaskStatus.APPROVED)
                self._update_recent_tasks(task_id)
                return "approved"

            if next_step.action == "escalate":
                self._db.update_task(task_id, status=TaskStatus.ESCALATED)
                self._audit.log_escalation(
                    task_id, "engineering_head",
                    next_step.reason or "Escalated by Engineering Head",
                )
                self._update_recent_tasks(task_id)
                return "escalated"

            if next_step.action == "delegate":
                delegate_result = self._run_agent(
                    task_id, AgentName(next_step.agent), next_step.prompt or "",
                )
                if delegate_result.success:
                    self._log_step_result(task_id, delegate_result)

                result_summary = (
                    delegate_result.report.output_summary
                    if delegate_result.report
                    else "Agent session failed"
                )
                prior_steps.append(StepRecord(
                    step_number=step_num,
                    agent=next_step.agent or "unknown",
                    action=f"delegate: {(next_step.prompt or '')[:100]}",
                    result_summary=result_summary,
                    success=delegate_result.success,
                ))

        # Max steps exceeded — escalate
        self._db.update_task(task_id, status=TaskStatus.ESCALATED)
        self._audit.log_escalation(
            task_id, "orchestrator",
            f"Max orchestration steps ({max_steps}) exceeded",
        )
        self._update_recent_tasks(task_id)
        return "escalated"

    def _parse_next_step(self, result: ExecutorResult) -> NextStep:
        """Parse the Engineering Head's decision from its completion report."""
        if result.report is None:
            return NextStep(action="escalate", reason="No completion report from Engineering Head")
        try:
            data = json.loads(result.report.output_summary)
            return NextStep(**data)
        except (json.JSONDecodeError, TypeError, KeyError):
            # Plain text output — treat as "done" with the text as summary
            return NextStep(action="done", summary=result.report.output_summary)

    def _run_agent(
        self,
        task_id: str,
        agent: AgentName,
        prompt: str,
    ) -> ExecutorResult:
        """Set up workspace and run an agent session."""
        agent_name = agent.value
        workspace = self._settings.get_workspaces_dir() / agent_name

        system_prompt = _DEFAULT_SYSTEM_PROMPTS.get(agent_name, "")
        self._context.initialize_workspace(workspace, agent_name, system_prompt)

        self._audit.log_session_start(task_id, agent_name, str(workspace))
        self._db.update_task(task_id, assigned_agent=agent_name)

        result = self._executor.run(
            workspace=workspace,
            prompt=prompt,
            timeout_seconds=self._settings.session_timeout_seconds,
        )

        self._audit.log_session_end(task_id, agent_name, result.duration_seconds)
        return result

    def _update_recent_tasks(self, task_id: str) -> None:
        """Append a summary to recent_tasks.md for all agents."""
        task = self._db.get_task(task_id)
        if task is None:
            return
        summary = (
            f"- **{task_id}** ({task.type.value}): {task.brief} "
            f"-- {task.status.value}\n"
        )
        for agent in AgentName:
            workspace = self._settings.get_workspaces_dir() / agent.value
            recent_path = workspace / "recent_tasks.md"
            if recent_path.exists():
                content = recent_path.read_text()
                recent_path.write_text(content + summary)

    def _log_step_result(self, task_id: str, result: ExecutorResult) -> None:
        """Log a successful step result to audit trail."""
        if result.report:
            self._audit.log_completion_report(
                report=result.report,
                session_id=result.session_id,
                duration_seconds=result.duration_seconds,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_orchestrator.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/orchestrator.py tests/test_orchestrator.py
git commit -m "refactor: replace chain-based orchestration with EH-driven decision loop"
```

---

### Task 5: Update CLI to make --task optional

**Files:**
- Modify: `src/cli.py:30-50` (cmd_run), `src/cli.py:208-217` (parser)
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write tests for optional --task**

Add to `tests/test_cli.py`:

```python
def test_run_without_task_flag():
    parser = build_parser()
    args = parser.parse_args(["run", "--brief", "Explore the codebase"])
    assert args.command == "run"
    assert args.task == "general"
    assert args.brief == "Explore the codebase"


def test_run_with_task_flag():
    parser = build_parser()
    args = parser.parse_args(["run", "--task", "bug_fix", "--brief", "Fix it"])
    assert args.task == "bug_fix"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py::test_run_without_task_flag -v`
Expected: FAIL — `--task` is currently required

- [ ] **Step 3: Update cli.py**

In `src/cli.py`, change the `cmd_run` function to handle optional task type:

```python
def cmd_run(args: argparse.Namespace) -> None:
    """Run a task through the Engineering Head-driven orchestration loop."""
    _setup_logging(args.verbose)
    db = _get_db(args)
    orchestrator = Orchestrator(db=db, settings=settings)

    task_type = TaskType(args.task)
    task_id = orchestrator.create_task(task_type, args.brief)
    logging.info("Created task %s (%s): %s", task_id, args.task, args.brief)

    result = orchestrator.run_task(task_id)

    task = db.get_task(task_id)
    print(f"\n{'='*60}")
    print(f"Task ID:    {task_id}")
    print(f"Type:       {args.task}")
    print(f"Status:     {result}")
    print(f"{'='*60}")
    db.close()
```

In the parser section for `opc run`, change `--task` to be optional with a default:

```python
    # opc run
    p_run = sub.add_parser("run", help="Run a task")
    p_run.add_argument(
        "--task", default="general",
        choices=["general", "implement_feature", "bug_fix", "payment_change"],
        help="Task type hint (default: general -- EH decides the approach)",
    )
    p_run.add_argument("--brief", required=True, help="Task description")
    p_run.add_argument("--verbose", action="store_true", help="Debug logging")
    p_run.set_defaults(func=cmd_run)
```

Also update the existing `test_run_subcommand` test in `tests/test_cli.py` which currently asserts `args.task == "implement_feature"` — it still passes since it provides `--task`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/cli.py tests/test_cli.py
git commit -m "feat: make --task optional in CLI, default to general"
```

---

### Task 6: Delete old code and tests

**Files:**
- Delete: `src/orchestrator/task_router.py`
- Delete: `src/orchestrator/revision_loop.py`
- Delete: `tests/test_task_router.py`
- Delete: `tests/test_revision_loop.py`

- [ ] **Step 1: Verify no remaining imports of deleted modules**

Search the codebase for imports of `task_router` and `revision_loop`:

Run: `uv run python -c "from src.orchestrator import task_router" 2>&1 || echo "Not imported elsewhere"`

After the orchestrator refactor in Task 4, these modules should have no remaining imports in production code.

- [ ] **Step 2: Delete the files**

```bash
rm src/orchestrator/task_router.py
rm src/orchestrator/revision_loop.py
rm tests/test_task_router.py
rm tests/test_revision_loop.py
```

- [ ] **Step 3: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: All PASS (fewer tests total, but no failures)

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: delete task_router and revision_loop, replaced by EH-driven orchestration"
```

---

### Task 7: Full verification

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All PASS

- [ ] **Step 2: Verify test count**

Run: `uv run pytest tests/ -q`
Expected: ~82+ tests passed (down from 89 due to deleted chain/revision tests, up from new capabilities/orchestrator tests)

- [ ] **Step 3: Verify no broken imports**

Run: `uv run python -c "from src.orchestrator.orchestrator import Orchestrator; from src.orchestrator.capabilities import build_capabilities_prompt; from src.cli import build_parser; print('All imports OK')"`
Expected: `All imports OK`

- [ ] **Step 4: Verify CLI help shows updated --task**

Run: `uv run opc run --help`
Expected: `--task` shows as optional with `general` as default and in the choices list

- [ ] **Step 5: Verify git status is clean**

Run: `git status`
Expected: Clean working tree

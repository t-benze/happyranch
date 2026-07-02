"""Call-path integration: managed skill INDEX through dream/wake session entrypoints.

Proves that resolve_managed_skills_index flows through the REAL run_dream and
run_wake entrypoints — not just the pure prompt builders.
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.daemon.dream_runner import run_dream
from runtime.daemon.wake_runner import run_wake
from runtime.models import (
    DreamRecord,
    WorkHourMode,
    WorkHourRecord,
    WorkHourStatus,
)
from runtime.orchestrator.executors import ExecutorResult

FIXTURES = Path(__file__).parent.parent / "fixtures" / "skills"


def _seed_skills_and_config(
    root: Path,
    allow: list[str] | None = None,
    deny: list[str] | None = None,
    agent_name: str = "dev_agent",
    agent_executor: str = "claude",
) -> None:
    """Seed on-disk skill packages and org config under an org root.

    ``root`` is the org root (e.g. ``org_state.root``).
    Skills are copied into ``root/runtime/skills/``.
    Org config is written to ``root/org/config.yaml``.
    An agent definition is written to ``root/org/agents/<agent_name>.md``.
    """
    skills_dir = root / "runtime" / "skills"
    if skills_dir.exists():
        shutil.rmtree(skills_dir)
    skills_dir.parent.mkdir(parents=True, exist_ok=True)
    for fixture_dir in FIXTURES.iterdir():
        if fixture_dir.is_dir():
            shutil.copytree(fixture_dir, skills_dir / fixture_dir.name)

    org_dir = root / "org"
    org_dir.mkdir(parents=True, exist_ok=True)
    import yaml as _yaml
    cfg: dict = {"timezone": "Asia/Shanghai"}
    if allow is not None or deny is not None:
        cfg["skills"] = {
            "org": {
                "allow": allow or [],
                "deny": deny or [],
            },
        }
    (org_dir / "config.yaml").write_text(_yaml.dump(cfg))

    agents_dir = org_dir / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / f"{agent_name}.md").write_text(
        "---\n"
        f"name: {agent_name}\n"
        "team: engineering\n"
        "role: worker\n"
        f"executor: {agent_executor}\n"
        "---\n\n"
        f"# {agent_name}\n\nBuild software.\n"
        "## Routine Tasks\n\n- Triage open tickets.\n"
    )


class _FakeResult:
    """Minimal executor result with required fields."""
    success = True
    error = None
    returncode = 0
    session_id = "sess-fake"
    duration_seconds = 1
    agent_session_id = None
    stdout_tail = ""
    stderr_tail = ""
    token_usage = None


class TestDreamRunnerSkillsIndex:
    """run_dream call-path — managed skill INDEX injected into dream prompt."""

    @pytest.mark.asyncio
    async def test_dream_runner_injects_skills_index(self, org_state):
        """run_dream resolves skills and injects the index into the
        dream prompt."""
        _seed_skills_and_config(
            org_state.root, allow=["hr:standard-skill"],
        )

        workspace = org_state.root / "workspaces" / "dev_agent"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "agent.yaml").write_text("executor: claude\n")
        org_state.db.insert_dream(DreamRecord(
            id="DREAM-001", agent_name="dev_agent",
            local_date="2026-07-02",
            scheduled_for=datetime(2026, 7, 2, 15, 0, tzinfo=timezone.utc),
            window_start=datetime(2026, 7, 2, 14, 0, tzinfo=timezone.utc),
            window_end=datetime(2026, 7, 2, 15, 0, tzinfo=timezone.utc),
        ))

        captured_prompt: list[str] = []

        class FakeDreamExecutor:
            def run(self, **kwargs):
                captured_prompt.append(kwargs.get("prompt", ""))
                return _FakeResult()

        await run_dream(
            org_state=org_state, dream_id="DREAM-001",
            executor_factory=lambda *a, **k: FakeDreamExecutor(),
        )

        assert captured_prompt, "Expected executor.run() to be called"
        prompt = captured_prompt[0]
        assert "hr:standard-skill@1.0.0" in prompt
        assert "A standard operational skill for testing." in prompt
        assert "hr:disabled-skill" not in prompt
        assert "hr:draft-skill" not in prompt
        assert "hr:system-contract-skill" not in prompt
        assert "hr:high-impact-skill" not in prompt
        assert "current_time:" in prompt
        assert "Asia/Shanghai" in prompt

    @pytest.mark.asyncio
    async def test_dream_runner_empty_index_no_hr_entries(self, org_state):
        """When no skills are eligible, dream prompt has no hr: entries."""
        _seed_skills_and_config(
            org_state.root, allow=["hr:disabled-skill"],
        )

        workspace = org_state.root / "workspaces" / "dev_agent"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "agent.yaml").write_text("executor: claude\n")
        org_state.db.insert_dream(DreamRecord(
            id="DREAM-002", agent_name="dev_agent",
            local_date="2026-07-02",
            scheduled_for=datetime(2026, 7, 2, 15, 0, tzinfo=timezone.utc),
            window_start=datetime(2026, 7, 2, 14, 0, tzinfo=timezone.utc),
            window_end=datetime(2026, 7, 2, 15, 0, tzinfo=timezone.utc),
        ))

        captured_prompt: list[str] = []

        class FakeDreamExecutor:
            def run(self, **kwargs):
                captured_prompt.append(kwargs.get("prompt", ""))
                return _FakeResult()

        await run_dream(
            org_state=org_state, dream_id="DREAM-002",
            executor_factory=lambda *a, **k: FakeDreamExecutor(),
        )

        assert captured_prompt
        assert "hr:" not in captured_prompt[0]
        assert "current_time:" in captured_prompt[0]


class TestWakeRunnerSkillsIndex:
    """run_wake call-path — managed skill INDEX injected into wake prompt."""

    @pytest.mark.asyncio
    async def test_wake_runner_injects_skills_index(self, org_state):
        """run_wake resolves skills and injects the index into the
        wake prompt."""
        _seed_skills_and_config(
            org_state.root, allow=["hr:standard-skill"],
        )

        workspace = org_state.root / "workspaces" / "dev_agent"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "agent.yaml").write_text("executor: claude\n")
        org_state.db.work_hours.insert(WorkHourRecord(
            id="WORKHOUR-001", agent_name="dev_agent",
            local_date="2026-07-02", slot="09:00",
            mode=WorkHourMode.WINDOWED,
            scheduled_for=datetime(2026, 7, 2, 1, 0, tzinfo=timezone.utc),
            status=WorkHourStatus.PENDING,
            routine_count=1,
        ))

        captured_prompt: list[str] = []

        class FakeWakeExecutor:
            def run(self, **kwargs):
                captured_prompt.append(kwargs.get("prompt", ""))
                return _FakeResult()

        await run_wake(
            org_state=org_state, work_hour_id="WORKHOUR-001",
            settings=Settings(),
            executor_factory=lambda *a, **k: FakeWakeExecutor(),
        )

        assert captured_prompt, "Expected executor.run() to be called"
        prompt = captured_prompt[0]
        assert "hr:standard-skill@1.0.0" in prompt
        assert "A standard operational skill for testing." in prompt
        assert "hr:disabled-skill" not in prompt
        assert "hr:draft-skill" not in prompt
        assert "hr:system-contract-skill" not in prompt
        assert "hr:high-impact-skill" not in prompt
        assert "current_time:" in prompt
        assert "Asia/Shanghai" in prompt

    @pytest.mark.asyncio
    async def test_wake_runner_empty_index_no_hr_entries(self, org_state):
        """When no skills are eligible, wake prompt has no hr: entries."""
        _seed_skills_and_config(
            org_state.root, allow=["hr:disabled-skill"],
        )

        workspace = org_state.root / "workspaces" / "dev_agent"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "agent.yaml").write_text("executor: claude\n")
        org_state.db.work_hours.insert(WorkHourRecord(
            id="WORKHOUR-002", agent_name="dev_agent",
            local_date="2026-07-02", slot="10:00",
            mode=WorkHourMode.WINDOWED,
            scheduled_for=datetime(2026, 7, 2, 2, 0, tzinfo=timezone.utc),
            status=WorkHourStatus.PENDING,
            routine_count=1,
        ))

        captured_prompt: list[str] = []

        class FakeWakeExecutor:
            def run(self, **kwargs):
                captured_prompt.append(kwargs.get("prompt", ""))
                return _FakeResult()

        await run_wake(
            org_state=org_state, work_hour_id="WORKHOUR-002",
            settings=Settings(),
            executor_factory=lambda *a, **k: FakeWakeExecutor(),
        )

        assert captured_prompt
        assert "hr:" not in captured_prompt[0]
        assert "current_time:" in captured_prompt[0]

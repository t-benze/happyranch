from __future__ import annotations

from datetime import datetime, timezone

from runtime.config import Settings
from runtime.daemon.wake_runner import build_wake_prompt, run_wake
from runtime.models import WorkHourMode, WorkHourRecord, WorkHourStatus
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.org_config import OrgConfig


def _prompt(**overrides) -> str:
    base = dict(
        org_slug="happyranch",
        work_hour_id="WORKHOUR-007",
        agent_name="customer_service",
        role="worker",
        team="customer_service",
        local_date="2026-06-11",
        slot="09:00",
        mode="windowed",
        preamble="Run these every wake; phrase briefs relative to the last wake.",
        routines=["- Triage open customer tickets.", "- Send overdue follow-ups."],
        org_config=OrgConfig(),
    )
    base.update(overrides)
    return build_wake_prompt(**base)


def test_prompt_states_it_is_a_trigger_not_the_work() -> None:
    prompt = _prompt()
    assert "WORKING-HOURS WAKE" in prompt
    assert "NOT the work itself" in prompt
    assert "NOT a reflection" in prompt


def test_prompt_includes_bootstrap_context() -> None:
    prompt = _prompt()
    assert "customer_service" in prompt        # agent + team
    assert "worker" in prompt                  # role
    assert "happyranch" in prompt              # org


def test_prompt_injects_routine_section_verbatim() -> None:
    prompt = _prompt()
    assert "Run these every wake; phrase briefs relative to the last wake." in prompt
    assert "- Triage open customer tickets." in prompt
    assert "- Send overdue follow-ups." in prompt


def test_prompt_instructs_single_from_file_spawn_callback() -> None:
    prompt = _prompt()
    assert (
        "happyranch work-hours spawn --org happyranch "
        "--work-hour-id WORKHOUR-007 --from-file /tmp/wake-WORKHOUR-007.json"
    ) in prompt
    assert "SINGLE" in prompt
    assert "EACH routine" in prompt


def test_prompt_states_cadence_context() -> None:
    prompt = _prompt()
    assert "local_date 2026-06-11" in prompt
    assert "slot 09:00" in prompt
    assert "mode windowed" in prompt


def test_prompt_handles_empty_preamble() -> None:
    prompt = _prompt(preamble="")
    assert "## Routine Tasks (verbatim from your agent file)" in prompt
    assert "- Triage open customer tickets." in prompt


def test_prompt_surfaces_dropped_routine_count() -> None:
    # No silent truncation: when routines were dropped past the cap, the wake
    # session must be told so it doesn't assume it is spawning everything.
    prompt = _prompt(dropped=5)
    lower = prompt.lower()
    assert "dropped" in lower
    assert "5" in prompt


def test_prompt_omits_dropped_line_when_none() -> None:
    prompt = _prompt(dropped=0)
    assert "dropped" not in prompt.lower()


class _FakeResult:
    success = True
    error = None
    returncode = 0
    session_id = "executor-session"
    agent_session_id = "agent-session"
    token_usage = None


class _FakeExecutor:
    def run(self, **_kwargs):
        return _FakeResult()


async def test_run_wake_passes_org_paths_to_executor_factory(org_state) -> None:
    (org_state.root / "org" / "agents").mkdir(parents=True, exist_ok=True)
    (org_state.root / "org" / "agents" / "dev_agent.md").write_text(
        "---\n"
        "name: dev_agent\n"
        "team: engineering\n"
        "role: worker\n"
        "executor: claude\n"
        "---\n\n"
        "## Routine Tasks\n\n"
        "- Triage open tickets.\n"
    )
    (org_state.root / "workspaces" / "dev_agent").mkdir(parents=True, exist_ok=True)
    org_state.db.work_hours.insert(WorkHourRecord(
        id="WORKHOUR-001",
        agent_name="dev_agent",
        local_date="2026-06-11",
        slot="09:00",
        mode=WorkHourMode.WINDOWED,
        scheduled_for=datetime(2026, 6, 11, 1, 0, tzinfo=timezone.utc),
        status=WorkHourStatus.PENDING,
        routine_count=1,
    ))
    captured = {}

    def factory(executor_name, settings, paths):
        captured["paths"] = paths
        return _FakeExecutor()

    await run_wake(
        org_state=org_state,
        work_hour_id="WORKHOUR-001",
        settings=Settings(),
        executor_factory=factory,
    )

    assert isinstance(captured["paths"], OrgPaths)
    assert captured["paths"].root == org_state.root

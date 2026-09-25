"""B8 shipping-seam coverage for team-scoped policy prompt injection.

These cases invoke the real thread, wake, dream, and schedule runners. Only
external materialization/repository refresh/provider execution is doubled;
the live AgentDef + TeamsRegistry resolver, active selector lookup, policy
rendering, and each production prompt builder remain real.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from runtime.config import Settings
from runtime.daemon import dream_runner, schedule_runner, thread_runner, wake_runner
from runtime.models import (
    DreamRecord,
    ScheduleKind,
    ScheduleRecord,
    ScheduleStatus,
    ThreadInvocationPurpose,
    ThreadMessageKind,
    ThreadRecord,
    WorkHourMode,
    WorkHourRecord,
    WorkHourStatus,
)
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.agent_def import AgentDef, render_agent_text
from runtime.orchestrator.authority_policy import project_authority_policy_v2_starter
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
from runtime.orchestrator.executors import ExecutorResult
from runtime.orchestrator.teams import TeamManager, TeamsRegistry

CONTENT_TO = "CONTENT ONLY: escalate publishing commitments."
CONTENT_NOT = "CONTENT ONLY: continue bounded editorial work."
ENGINEERING_TO = "ENGINEERING ONLY: escalate architecture commitments."
ENGINEERING_NOT = "ENGINEERING ONLY: continue bounded implementation."
POLICY_HEADER = "## [RESERVED] Active Team Escalation Policy"


class _CaptureExecutor:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def run(self, **kwargs) -> ExecutorResult:
        self.calls.append(kwargs)
        return ExecutorResult(
            success=False,
            duration_seconds=0,
            returncode=1,
            error="expected test-provider stop",
            session_id=str(kwargs.get("session_id") or "schedule-session"),
        )


class _InlineSupervisor:
    """Exercise the schedule launch body without starting an OS process."""

    def run(self, _request, *, launch_spec, launch_body, pre_launch_validator, **_kwargs):
        assert launch_spec is not None
        pre_launch_validator()
        launch = launch_body(SimpleNamespace(process=None))
        return SimpleNamespace(payload=launch, error=None)


def _write_agent(root, *, role: str) -> None:
    paths = OrgPaths(root=root)
    paths.agents_dir.mkdir(parents=True, exist_ok=True)
    agent = AgentDef(
        name="content_manager",
        team="content",
        role=role,
        executor="claude",
        allow_rules=(),
        repos={},
        enrolled_by="founder",
        enrolled_at_task=None,
        enrolled_at=datetime(2026, 9, 24, tzinfo=timezone.utc),
        system_prompt="You are content_manager.\n\n## Routine Tasks\n\n- Review content operations.\n",
        description="Content manager",
    )
    (paths.agents_dir / "content_manager.md").write_text(render_agent_text(agent))


def _registry(state: str, root) -> TeamsRegistry:
    teams = {
        "content": TeamManager("content_manager", "content", ()),
        "engineering": TeamManager("engineering_head", "engineering", ()),
    }
    if state == "duplicate":
        teams["media"] = TeamManager("content_manager", "media", ())
    return TeamsRegistry(teams, root=root)


def _activate(store: AuthorityPolicyStore, *, team: str, what_to: str, what_not: str) -> None:
    selector = store.ensure_authority_selector(team)
    starter = project_authority_policy_v2_starter(team)
    store.create_and_activate_v2({
        "team": team,
        "policy_id": starter["policy_id"],
        "title": starter["title"],
        "create_request_id": f"runner-{team}-create",
        "activation_request_id": f"runner-{team}-activate",
        "based_on_selector_id": selector.selector_id,
        "expected_selector_id": selector.selector_id,
        "action": "bootstrap",
        "what_to_escalate": what_to,
        "what_not_to_escalate": what_not,
    })


def _prepare_policy_state(org_state, *, state: str) -> None:
    _write_agent(org_state.root, role="worker" if state == "worker" else "manager")
    org_state.teams = _registry(state, org_state.root)
    store = AuthorityPolicyStore(org_state.db)
    _activate(store, team="engineering", what_to=ENGINEERING_TO, what_not=ENGINEERING_NOT)
    _activate(store, team="content", what_to=CONTENT_TO, what_not=CONTENT_NOT)
    (org_state.root / "workspaces" / "content_manager").mkdir(parents=True, exist_ok=True)


def _bypass_external_launch_setup(monkeypatch) -> None:
    for module in (thread_runner, wake_runner, dream_runner, schedule_runner):
        monkeypatch.setattr(module, "materialize_workspace_skills", lambda *_a, **_k: [])
        monkeypatch.setattr(module, "validate_workspace_skills_integrity", lambda *_a, **_k: None)
        monkeypatch.setattr(module, "refresh_workspace_repos", lambda *_a, **_k: {})
        monkeypatch.setattr(module, "resolve_managed_skills_index", lambda *_a, **_k: "")


def _seed_thread(org_state, *, resumed: bool) -> str:
    db = org_state.db
    db.insert_thread(ThreadRecord(id="THR-POLICY", subject="Content policy launch"))
    db.add_thread_participant("THR-POLICY", "content_manager", added_by="founder")
    first = db.append_thread_message(
        thread_id="THR-POLICY",
        speaker="founder",
        kind=ThreadMessageKind.MESSAGE,
        body_markdown="first content message",
    )
    triggering_seq = first
    acknowledged = 0
    if resumed:
        second = db.append_thread_message(
            thread_id="THR-POLICY",
            speaker="founder",
            kind=ThreadMessageKind.MESSAGE,
            body_markdown="resumed content message",
        )
        triggering_seq = second
        acknowledged = first
        db.update_thread_session(
            "THR-POLICY",
            "content_manager",
            agent_session_id="claude-content-prior",
            last_resumed_seq=first,
        )
    invocation = db.mint_thread_invocation(
        thread_id="THR-POLICY",
        agent_name="content_manager",
        triggering_seq=triggering_seq,
        purpose=ThreadInvocationPurpose.REPLY,
    )
    db._conn.execute(
        "INSERT INTO thread_reply_delivery_state "
        "(thread_id, agent_name, acknowledged_through_seq, required_through_seq, "
        "queued_invocation_token, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            "THR-POLICY",
            "content_manager",
            acknowledged,
            triggering_seq,
            invocation.invocation_token,
            "2026-09-24T00:00:00+00:00",
        ),
    )
    db._conn.commit()
    return invocation.invocation_token


async def _run_surface(org_state, monkeypatch, *, surface: str) -> tuple[str, dict[str, object]]:
    executor = _CaptureExecutor()
    settings = Settings()
    if surface in {"thread_fresh", "thread_resumed"}:
        token = _seed_thread(org_state, resumed=surface == "thread_resumed")
        monkeypatch.setattr(
            thread_runner,
            "_build_executor_for_provider",
            lambda *_a, **_k: executor,
        )
        await thread_runner.run_invocation(
            org_state=org_state,
            invocation_token=token,
            settings=settings,
        )
    elif surface == "wake":
        now = datetime(2026, 9, 24, tzinfo=timezone.utc)
        org_state.db.work_hours.insert(WorkHourRecord(
            id="WAKE-POLICY",
            agent_name="content_manager",
            local_date="2026-09-24",
            slot="09:00",
            mode=WorkHourMode.WINDOWED,
            scheduled_for=now,
            window_start=now,
            window_end=now,
            status=WorkHourStatus.PENDING,
            routine_count=1,
        ))
        await wake_runner.run_wake(
            org_state=org_state,
            work_hour_id="WAKE-POLICY",
            settings=settings,
            executor_factory=lambda *_a, **_k: executor,
        )
    elif surface == "dream":
        org_state.db.insert_dream(DreamRecord(
            id="DREAM-POLICY",
            agent_name="content_manager",
            local_date="2026-09-24",
            scheduled_for=datetime(2026, 9, 24, 2, tzinfo=timezone.utc),
            window_start=datetime(2026, 9, 24, 1, tzinfo=timezone.utc),
            window_end=datetime(2026, 9, 24, 2, tzinfo=timezone.utc),
        ))
        await dream_runner.run_dream(
            org_state=org_state,
            dream_id="DREAM-POLICY",
            settings=settings,
            executor_factory=lambda *_a, **_k: executor,
        )
    else:
        now = datetime(2026, 9, 24, tzinfo=timezone.utc)
        org_state.db.schedules.insert(ScheduleRecord(
            id="SCHEDULE-POLICY",
            agent_name="content_manager",
            team="content",
            kind=ScheduleKind.ONE_SHOT,
            status=ScheduleStatus.FIRING,
            fire_at=now,
            normalized_brief="Review the content calendar.",
            source_instruction="Review the content calendar.",
        ))
        await schedule_runner.run_schedule(
            org_state=org_state,
            schedule_id="SCHEDULE-POLICY",
            settings=settings,
            executor_factory=lambda *_a, **_k: executor,
            host_supervisor=_InlineSupervisor(),
        )
    assert executor.calls, f"{surface} never reached the real prompt launch seam"
    return str(executor.calls[0]["prompt"]), executor.calls[0]


@pytest.mark.parametrize(
    "surface",
    ["thread_fresh", "thread_resumed", "wake", "dream", "schedule"],
)
@pytest.mark.parametrize("state", ["valid", "worker", "duplicate"])
async def test_content_policy_runner_shipping_is_team_scoped_and_fails_closed(
    org_state,
    monkeypatch,
    surface: str,
    state: str,
) -> None:
    _prepare_policy_state(org_state, state=state)
    _bypass_external_launch_setup(monkeypatch)

    prompt, launch = await _run_surface(
        org_state,
        monkeypatch,
        surface=surface,
    )

    if state == "valid":
        assert POLICY_HEADER in prompt
        assert CONTENT_TO in prompt and CONTENT_NOT in prompt
        assert ENGINEERING_TO not in prompt and ENGINEERING_NOT not in prompt
        assert prompt.count(POLICY_HEADER) == 1
        if surface == "thread_resumed":
            assert launch.get("resume_session_id") == "claude-content-prior"
            assert "resumed content message" in prompt
        elif surface == "thread_fresh":
            assert launch.get("resume_session_id") is None
            assert "first content message" in prompt
    else:
        assert POLICY_HEADER not in prompt
        assert CONTENT_TO not in prompt and CONTENT_NOT not in prompt
        assert ENGINEERING_TO not in prompt and ENGINEERING_NOT not in prompt
        assert org_state.db._conn.execute(
            "SELECT COUNT(*) FROM authority_policy_v2_session_bindings"
        ).fetchone()[0] == 0

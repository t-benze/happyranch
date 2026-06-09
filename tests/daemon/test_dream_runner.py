from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from runtime.daemon.dream_runner import build_dream_prompt, run_dream
from runtime.models import DreamRecord, DreamStatus


def _dt(hour: int) -> datetime:
    return datetime(2026, 6, 9, hour, 0, tzinfo=timezone.utc)


def test_build_dream_prompt_contains_private_contract(tmp_path):
    prompt = build_dream_prompt(
        org_slug="test",
        dream=DreamRecord(
            id="DREAM-001",
            agent_name="dev_agent",
            local_date="2026-06-09",
            scheduled_for=_dt(2),
            window_start=_dt(1),
            window_end=_dt(2),
        ),
        workspace=tmp_path,
        recent_audit=[],
        task_history="TASK-001 completed\n",
    )

    assert "private reflection" in prompt
    assert "happyranch dreams complete" in prompt
    assert "DREAM-001" in prompt
    assert "TASK-001 completed" in prompt


class FakeResult:
    success = True
    error = None
    returncode = 0
    session_id = "executor-session"
    agent_session_id = "agent-session"
    token_usage = None


class FakeTimeoutResult:
    success = False
    error = "Session timed out after 1800 seconds"
    returncode = None
    session_id = "executor-session"
    agent_session_id = None
    token_usage = None


class FakeFailureResult:
    success = False
    error = "Command exited with code 1: boom"
    returncode = 1
    session_id = "executor-session"
    agent_session_id = None
    token_usage = None


class FakeExecutor:
    def __init__(self, result_cls=FakeResult):
        self.calls = []
        self._result_cls = result_cls

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return self._result_cls()


def _insert_pending_dream(org_state, **overrides):
    fields = dict(
        id="DREAM-001",
        agent_name="dev_agent",
        local_date="2026-06-09",
        scheduled_for=_dt(2),
        window_start=_dt(1),
        window_end=_dt(2),
    )
    fields.update(overrides)
    org_state.db.insert_dream(DreamRecord(**fields))
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


async def test_run_dream_marks_running_and_waits_for_callback(org_state):
    workspace = _insert_pending_dream(org_state)
    fake = FakeExecutor()

    await run_dream(org_state=org_state, dream_id="DREAM-001", executor_factory=lambda *_args, **_kwargs: fake)

    dream = org_state.db.get_dream("DREAM-001")
    assert dream.status == DreamStatus.FAILED
    assert "no_callback" in dream.error
    assert fake.calls[0]["workspace"] == Path(workspace)


async def test_run_dream_timeout_sets_timeout_status_and_audit(org_state):
    _insert_pending_dream(org_state)
    fake = FakeExecutor(FakeTimeoutResult)

    await run_dream(org_state=org_state, dream_id="DREAM-001", executor_factory=lambda *_a, **_k: fake)

    dream = org_state.db.get_dream("DREAM-001")
    assert dream.status == DreamStatus.TIMEOUT
    assert "timed out" in dream.error
    actions = [r["action"] for r in org_state.db.get_audit_logs("DREAM-001")]
    assert "dream_timeout" in actions
    assert "dream_failed" not in actions
    # A timeout must NOT advance the successful-dream window.
    assert org_state.db.get_last_successful_dream("dev_agent") is None


async def test_run_dream_executor_failure_sets_failed_status(org_state):
    _insert_pending_dream(org_state)
    fake = FakeExecutor(FakeFailureResult)

    await run_dream(org_state=org_state, dream_id="DREAM-001", executor_factory=lambda *_a, **_k: fake)

    dream = org_state.db.get_dream("DREAM-001")
    assert dream.status == DreamStatus.FAILED
    actions = [r["action"] for r in org_state.db.get_audit_logs("DREAM-001")]
    assert "dream_failed" in actions
    assert "dream_timeout" not in actions
    assert org_state.db.get_last_successful_dream("dev_agent") is None


async def test_run_dream_prompt_includes_agent_window_audit(org_state):
    _insert_pending_dream(org_state)
    # An audit row from the agent's recent (non-dream) work inside the window.
    org_state.db.insert_audit_log(
        task_id="TASK-900", agent="dev_agent", action="session_start",
        payload={"workspace": "ws"},
    )
    fake = FakeExecutor()

    await run_dream(org_state=org_state, dream_id="DREAM-001", executor_factory=lambda *_a, **_k: fake)

    prompt = fake.calls[0]["prompt"]
    assert "TASK-900" in prompt
    assert "session_start" in prompt

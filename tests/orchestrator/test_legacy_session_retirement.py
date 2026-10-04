"""Terminal ownership supplements; these are not installed shipping positives."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest

from runtime.daemon.sessions import SessionTracker
from runtime.orchestrator import executors, throttle
from runtime.orchestrator.executors import ExecutorResult
from runtime.orchestrator.orchestrator import Orchestrator


def _owned(tracker: SessionTracker, task: str, sid: str) -> Callable[[], None]:
    control = lambda: None
    tracker.set_active(task, "dev_agent", sid, org_slug="alpha")
    tracker.set_pid(task, "dev_agent", sid, 1234)
    tracker.set_cancel_control(task, "dev_agent", sid, control)
    return control


def _assert_live(tracker: SessionTracker, task: str, sid: str,
                 control: Callable[[], None], pid: int = 1234) -> None:
    assert tracker.get_active(task, "dev_agent") == sid
    assert tracker.get_context_by_session(sid) == ("alpha", task, "dev_agent")
    assert tracker.get_pid(task, "dev_agent") == pid
    assert tracker.get_cancel_control(task, "dev_agent") is control


def _launch(tracker: SessionTracker, workspace: Path, executor: object,
            *, recovery: bool = False,
            on_started: Callable[[int], None] = lambda pid: None) -> ExecutorResult:
    # Exercise the exact owner method, with real tracker state. No production
    # test seam, task-status patch, or replacement clear implementation.
    orch = Orchestrator.__new__(Orchestrator)
    orch._host_supervisor, orch._sessions, orch._slug = None, tracker, "alpha"
    return orch._launch_agent_with_scratch(
        task_id="TASK-001", agent_name="dev_agent", workspace=workspace,
        provider="claude", model_name="pinned-model", executor=executor,
        session_id="runtime-old", full_prompt="owned prompt", timeout_seconds=10,
        on_started=on_started, on_throttle_event=None,
        pre_launch_integrity_validator=lambda: None,
        recovery_launch_validator=lambda: None, recovery=recovery,
    )


@pytest.mark.parametrize("outcome", ["success", "nonzero", "timeout"])
@pytest.mark.parametrize("recovery", [False, True], ids=["ordinary", "recovery"])
def test_terminal_result_retires_only_owned_tuple(tmp_path: Path, outcome: str,
                                                recovery: bool) -> None:
    """v24: outcome=all owned slots expire, identical result survives;
    regression=success/nonzero/timeout leaves stale context or rewrites result;
    insufficiency=tracker units do not call the ordinary terminal owner;
    seam=real _launch_agent_with_scratch finally and SessionTracker, no hook.
    """
    tracker = SessionTracker()
    control = _owned(tracker, "TASK-001", "runtime-old")
    result = ExecutorResult(success=outcome == "success", duration_seconds=2,
                            session_id="runtime-old",
                            returncode=None if outcome == "timeout" else 0 if outcome == "success" else 7,
                            error="timeout" if outcome == "timeout" else None)
    before = vars(result).copy()

    def run(**kwargs: object) -> ExecutorResult:
        _assert_live(tracker, "TASK-001", "runtime-old", control)
        assert kwargs["session_id"] == "runtime-old"
        assert kwargs["org_slug"] == "alpha"
        assert kwargs["prompt"] == "owned prompt"
        assert kwargs["model"] == "pinned-model"
        assert kwargs["timeout_seconds"] == 10
        assert kwargs["throttle_backoff_seconds"] == (() if recovery else None)
        assert kwargs["resume_session_id"] is None
        assert "recovery_deadline_monotonic" not in kwargs  # Claude signature
        return result

    assert _launch(tracker, tmp_path, SimpleNamespace(run=run), recovery=recovery) is result
    assert vars(result) == before
    assert tracker.iter_active() == []
    assert tracker.get_context_by_session("runtime-old") is None
    assert tracker.get_pid("TASK-001", "dev_agent") is None
    assert tracker.get_cancel_control("TASK-001", "dev_agent") is None


def test_executor_exception_retires_tuple_and_preserves_primary(tmp_path: Path) -> None:
    """v24: outcome=exact raised exception survives and all owned slots expire;
    regression=cleanup omitted on throw or exception replaced by return;
    insufficiency=shipping stand-ins exit nonzero rather than raising in run;
    seam=ordinary owner finally and real tracker; private throwing executor only.
    """
    tracker = SessionTracker()
    control = _owned(tracker, "TASK-001", "runtime-old")
    primary = RuntimeError("owned executor exception")

    def run(**kwargs: object) -> ExecutorResult:
        _assert_live(tracker, "TASK-001", "runtime-old", control)
        raise primary

    with pytest.raises(RuntimeError) as caught:
        _launch(tracker, tmp_path, SimpleNamespace(run=run))
    assert caught.value is primary
    assert tracker.get_context_by_session("runtime-old") is None
    assert tracker.get_active("TASK-001", "dev_agent") is None
    assert tracker.get_pid("TASK-001", "dev_agent") is None
    assert tracker.get_cancel_control("TASK-001", "dev_agent") is None


@pytest.mark.parametrize("raises", [False, True], ids=["return", "exception"])
def test_old_terminal_preserves_new_generation_and_sibling(tmp_path: Path, raises: bool) -> None:
    """v24: outcome=new same-task generation and other task keep context/PID/control;
    regression=unconditional task/agent clear erases the replacement;
    insufficiency=shipping overlap has different tasks, tracker units bypass owner;
    seam=exact ordinary finally after set_active supersession; no clear test hook.
    """
    tracker = SessionTracker()
    old = _owned(tracker, "TASK-001", "runtime-old")
    sibling = _owned(tracker, "TASK-002", "runtime-sibling")
    controls = []
    result = ExecutorResult(True, 1, "runtime-old")
    primary = RuntimeError("old generation ended")

    def run(**kwargs: object) -> ExecutorResult:
        _assert_live(tracker, "TASK-001", "runtime-old", old)
        controls.append(_owned(tracker, "TASK-001", "runtime-new"))
        if raises:
            raise primary
        return result

    if raises:
        with pytest.raises(RuntimeError) as caught:
            _launch(tracker, tmp_path, SimpleNamespace(run=run))
        assert caught.value is primary
    else:
        assert _launch(tracker, tmp_path, SimpleNamespace(run=run)) is result
    assert tracker.get_context_by_session("runtime-old") is None
    _assert_live(tracker, "TASK-001", "runtime-new", controls[0])
    _assert_live(tracker, "TASK-002", "runtime-sibling", sibling)


@pytest.mark.parametrize("recovery", [False, True], ids=["ordinary-retries", "recovery-once"])
def test_real_process_429_retains_binding_until_final_exit(tmp_path: Path,
                                                         monkeypatch, recovery: bool) -> None:
    """v24: outcome=ordinary 429 retries under one live SID; recovery runs once;
    regression=clear between attempts or recovery inherits ordinary retry budget;
    insufficiency=shipping r2 is a new invocation, throttle units bypass owner;
    seam=ordinary owner -> actual _run_command/Popen/ProviderThrottle; only provider fake.
    """
    tracker = SessionTracker()
    control = _owned(tracker, "TASK-001", "runtime-old")
    provider = tmp_path / "provider.py"
    receipt = tmp_path / "attempts.jsonl"
    provider.write_text(
        "import json, os, sys\nfrom pathlib import Path\n"
        f"p=Path({str(receipt)!r})\n"
        "n=len(p.read_text().splitlines()) if p.exists() else 0\n"
        "with p.open('a') as f: f.write(json.dumps({'sid':os.environ.get('HAPPYRANCH_RUNTIME_SESSION_ID'),"
        "'pid':os.getpid()})+'\\n')\n"
        "print('429 rate limit' if n == 0 else 'completed', file=sys.stderr)\n"
        "sys.exit(7 if n == 0 else 0)\n")
    attempts = []

    def started(pid: int) -> None:
        tracker.set_pid("TASK-001", "dev_agent", "runtime-old", pid)
        attempts.append(pid)
        _assert_live(tracker, "TASK-001", "runtime-old", control, pid)

    def sleep(seconds: float) -> None:
        assert seconds == 0
        _assert_live(tracker, "TASK-001", "runtime-old", control, attempts[-1])

    monkeypatch.setattr(throttle, "_default_throttle", throttle.ProviderThrottle(
        ceiling_default=1, spacing_seconds=0, backoff_seconds=(0,), sleep=sleep))

    def run(**kwargs: object) -> ExecutorResult:
        return executors._run_command(
            [sys.executable, str(provider)], workspace=kwargs["workspace"],
            session_id=kwargs["session_id"], timeout_seconds=kwargs["timeout_seconds"],
            on_started=kwargs["on_started"], provider="claude", org_slug=kwargs["org_slug"],
            pre_launch_validator=kwargs["pre_launch_validator"],
            throttle_backoff_seconds=kwargs["throttle_backoff_seconds"])

    result = _launch(tracker, tmp_path, SimpleNamespace(run=run),
                     recovery=recovery, on_started=started)
    rows = [json.loads(line) for line in receipt.read_text().splitlines()]
    assert len(rows) == len(attempts) == (1 if recovery else 2)
    assert [row["pid"] for row in rows] == attempts
    assert {row["sid"] for row in rows} == {"runtime-old"}
    assert result.success is (not recovery)
    assert result.rate_limited is recovery
    assert result.session_id == "runtime-old"
    assert tracker.get_context_by_session("runtime-old") is None
    assert tracker.get_active("TASK-001", "dev_agent") is None
    assert tracker.get_pid("TASK-001", "dev_agent") is None
    assert tracker.get_cancel_control("TASK-001", "dev_agent") is None
    print(json.dumps({"429": rows, "recovery": recovery, "retired": True}))

"""Fire-and-forget bridges for SR notifications (mirrors test_notify_failed_dispatch)."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest


def _make_orchestrator(tmp_path: Path):
    """Build a minimal Orchestrator without invoking the full daemon stack."""
    from src.orchestrator.orchestrator import Orchestrator
    from src.orchestrator._paths import OrgPaths
    from src.orchestrator.teams import TeamsRegistry
    from src.config import Settings
    from src.infrastructure.database import Database

    root = tmp_path / "orgs" / "acme"
    root.mkdir(parents=True, exist_ok=True)
    (root / "workspaces").mkdir(parents=True, exist_ok=True)
    (root / "org").mkdir(parents=True, exist_ok=True)
    (root / "org" / "teams.yaml").write_text("teams: {}\n")
    (root / "org" / "charter.md").write_text("# Charter\n")
    (root / "org" / "escalation-rules.md").write_text("\n")
    settings = Settings()

    paths = OrgPaths(root=root)
    db = Database(paths.db_path)
    orch = Orchestrator(
        db=db, settings=settings, paths=paths, slug="acme",
        teams=TeamsRegistry.load(root),
    )
    return orch


def test_notify_script_submitted_noop_when_notifier_unset(tmp_path):
    orch = _make_orchestrator(tmp_path)
    orch.notify_script_submitted(
        sr_id="SR-1", agent="a", task_id="TASK-1",
        title="t", rationale="r", script_text="s",
        interpreter="bash", cwd_hint=None,
    )


def test_notify_script_submitted_runs_in_thread_when_no_loop(tmp_path):
    orch = _make_orchestrator(tmp_path)
    captured: list[dict] = []
    done = threading.Event()

    class _FakeNotifier:
        async def send_script_request(self, **kw):
            captured.append(kw)
            done.set()

    orch.attach_notifier(_FakeNotifier())
    orch.notify_script_submitted(
        sr_id="SR-1", agent="a", task_id="TASK-1",
        title="t", rationale="r", script_text="s",
        interpreter="bash", cwd_hint="x",
    )
    assert done.wait(timeout=2.0), "send_script_request was not invoked"
    assert captured == [{
        "sr_id": "SR-1", "agent": "a", "task_id": "TASK-1",
        "title": "t", "rationale": "r", "script_text": "s",
        "interpreter": "bash", "cwd_hint": "x",
    }]


def test_notify_script_run_result_noop_when_notifier_unset(tmp_path):
    orch = _make_orchestrator(tmp_path)
    orch.notify_script_run_result(
        sr_id="SR-1", task_id="TASK-1",
        parent_message_id="om_x",
        status="completed", exit_code=0, duration_ms=100,
        stdout_head=None, stderr_head=None, reason=None,
    )


def test_notify_script_run_result_runs_in_thread_when_no_loop(tmp_path):
    orch = _make_orchestrator(tmp_path)
    captured: list[dict] = []
    done = threading.Event()

    class _FakeNotifier:
        async def send_script_run_result(self, **kw):
            captured.append(kw)
            done.set()

    orch.attach_notifier(_FakeNotifier())
    orch.notify_script_run_result(
        sr_id="SR-1", task_id="TASK-1",
        parent_message_id="om_x",
        status="completed", exit_code=0, duration_ms=100,
        stdout_head="ok", stderr_head=None, reason=None,
    )
    assert done.wait(timeout=2.0)
    assert captured[0]["parent_message_id"] == "om_x"

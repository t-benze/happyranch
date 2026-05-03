"""Tests for the task -> org -> settings precedence in
Orchestrator._resolve_session_timeout."""
from __future__ import annotations

import pytest

from src.config import Settings
from src.infrastructure.database import Database
from src.models import TaskRecord, TaskStatus
from src.orchestrator.orchestrator import Orchestrator
from src.orchestrator.teams import TeamsRegistry
from src.runtime import RuntimeDir


@pytest.fixture
def orchestrator(test_settings: Settings, test_runtime: RuntimeDir) -> Orchestrator:
    db = Database(test_runtime.db_path)
    teams = TeamsRegistry.load(test_runtime)
    return Orchestrator(db=db, settings=test_settings, runtime=test_runtime, teams=teams)


def _write_org_config(runtime: RuntimeDir, *, session_timeout: int) -> None:
    runtime.org_config_path.write_text(f"session_timeout_seconds: {session_timeout}\n")


def _insert_task(db: Database, task_id: str, *, session_timeout: int | None) -> None:
    db.insert_task(TaskRecord(
        id=task_id,
        brief="b",
        team="engineering",
        status=TaskStatus.PENDING,
        session_timeout_seconds=session_timeout,
    ))


def test_falls_back_to_settings_default(orchestrator: Orchestrator) -> None:
    """No task override, no org config -> Settings.session_timeout_seconds."""
    assert orchestrator._resolve_session_timeout("dev_agent") == 1800


def test_org_override_used_when_no_task_override(
    orchestrator: Orchestrator, test_runtime: RuntimeDir
) -> None:
    _write_org_config(test_runtime, session_timeout=3600)
    _insert_task(orchestrator._db, "TASK-001", session_timeout=None)
    assert orchestrator._resolve_session_timeout("dev_agent", task_id="TASK-001") == 3600


def test_org_override_used_when_task_id_missing(
    orchestrator: Orchestrator, test_runtime: RuntimeDir
) -> None:
    """Resolver tolerates a task_id that doesn't exist (e.g. early startup) —
    the org override still wins over the Settings default."""
    _write_org_config(test_runtime, session_timeout=2400)
    assert orchestrator._resolve_session_timeout("dev_agent", task_id="TASK-NOPE") == 2400


def test_task_override_beats_org(
    orchestrator: Orchestrator, test_runtime: RuntimeDir
) -> None:
    _write_org_config(test_runtime, session_timeout=3600)
    _insert_task(orchestrator._db, "TASK-002", session_timeout=7200)
    assert orchestrator._resolve_session_timeout("dev_agent", task_id="TASK-002") == 7200


def test_task_override_beats_settings_default(orchestrator: Orchestrator) -> None:
    _insert_task(orchestrator._db, "TASK-003", session_timeout=600)
    assert orchestrator._resolve_session_timeout("dev_agent", task_id="TASK-003") == 600


def test_no_task_id_uses_org_then_settings(
    orchestrator: Orchestrator, test_runtime: RuntimeDir
) -> None:
    """When the resolver is called without a task_id (legacy callers), the
    task layer is skipped and we fall straight to org -> settings."""
    _write_org_config(test_runtime, session_timeout=2700)
    assert orchestrator._resolve_session_timeout("dev_agent") == 2700


def test_settings_default_respects_env_override(
    test_runtime: RuntimeDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bottom layer is itself env-overridable via OPC_SESSION_TIMEOUT_SECONDS,
    so a runtime with no org/task overrides still picks up the env value."""
    monkeypatch.setenv("OPC_SESSION_TIMEOUT_SECONDS", "900")
    settings = Settings(project_root=test_runtime.root)
    db = Database(test_runtime.db_path)
    teams = TeamsRegistry.load(test_runtime)
    orch = Orchestrator(db=db, settings=settings, runtime=test_runtime, teams=teams)
    assert orch._resolve_session_timeout("any_agent") == 900

"""Tests for the task -> org -> settings precedence in
Orchestrator._resolve_session_timeout."""
from __future__ import annotations

import pytest

from src.config import Settings
from src.infrastructure.database import Database
from src.models import TaskRecord, TaskStatus
from src.orchestrator._paths import OrgPaths
from src.orchestrator.orchestrator import Orchestrator
from src.orchestrator.teams import TeamsRegistry


@pytest.fixture
def orchestrator(test_settings: Settings, test_runtime: OrgPaths) -> Orchestrator:
    test_runtime.root.mkdir(parents=True, exist_ok=True)
    db = Database(test_runtime.db_path)
    teams = TeamsRegistry.load(test_runtime.root)
    return Orchestrator(
        db=db, settings=test_settings,
        paths=test_runtime, slug="test", teams=teams,
    )


def _write_org_config(paths: OrgPaths, *, session_timeout: int) -> None:
    paths.org_config_path.parent.mkdir(parents=True, exist_ok=True)
    paths.org_config_path.write_text(f"session_timeout_seconds: {session_timeout}\n")


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
    orchestrator: Orchestrator, test_runtime: OrgPaths
) -> None:
    _write_org_config(test_runtime, session_timeout=3600)
    _insert_task(orchestrator._db, "TASK-001", session_timeout=None)
    assert orchestrator._resolve_session_timeout("dev_agent", task_id="TASK-001") == 3600


def test_org_override_used_when_task_id_missing(
    orchestrator: Orchestrator, test_runtime: OrgPaths
) -> None:
    """Resolver tolerates a task_id that doesn't exist (e.g. early startup) —
    the org override still wins over the Settings default."""
    _write_org_config(test_runtime, session_timeout=2400)
    assert orchestrator._resolve_session_timeout("dev_agent", task_id="TASK-NOPE") == 2400


def test_task_override_beats_org(
    orchestrator: Orchestrator, test_runtime: OrgPaths
) -> None:
    _write_org_config(test_runtime, session_timeout=3600)
    _insert_task(orchestrator._db, "TASK-002", session_timeout=7200)
    assert orchestrator._resolve_session_timeout("dev_agent", task_id="TASK-002") == 7200


def test_task_override_beats_settings_default(orchestrator: Orchestrator) -> None:
    _insert_task(orchestrator._db, "TASK-003", session_timeout=600)
    assert orchestrator._resolve_session_timeout("dev_agent", task_id="TASK-003") == 600


def test_no_task_id_uses_org_then_settings(
    orchestrator: Orchestrator, test_runtime: OrgPaths
) -> None:
    """When the resolver is called without a task_id (legacy callers), the
    task layer is skipped and we fall straight to org -> settings."""
    _write_org_config(test_runtime, session_timeout=2700)
    assert orchestrator._resolve_session_timeout("dev_agent") == 2700


def test_settings_default_respects_env_override(
    test_runtime: OrgPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bottom layer is itself env-overridable via HAPPYRANCH_SESSION_TIMEOUT_SECONDS,
    so a runtime with no org/task overrides still picks up the env value."""
    monkeypatch.setenv("HAPPYRANCH_SESSION_TIMEOUT_SECONDS", "900")
    test_runtime.root.mkdir(parents=True, exist_ok=True)
    settings = Settings(project_root=test_runtime.root)
    db = Database(test_runtime.db_path)
    teams = TeamsRegistry.load(test_runtime.root)
    orch = Orchestrator(
        db=db, settings=settings,
        paths=test_runtime, slug="test", teams=teams,
    )
    assert orch._resolve_session_timeout("any_agent") == 900

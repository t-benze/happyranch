"""Tests for the agent -> org -> settings precedence in
Orchestrator._resolve_session_timeout."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.config import Settings
from src.infrastructure.database import Database
from src.orchestrator.orchestrator import Orchestrator
from src.orchestrator.teams import TeamsRegistry
from src.runtime import RuntimeDir


@pytest.fixture
def orchestrator(test_settings: Settings, test_runtime: RuntimeDir) -> Orchestrator:
    db = Database(test_runtime.db_path)
    teams = TeamsRegistry.load(test_runtime)
    return Orchestrator(db=db, settings=test_settings, runtime=test_runtime, teams=teams)


def _write_agent(runtime: RuntimeDir, name: str, *, session_timeout: int | None) -> None:
    """Write a minimal active agent file with the given timeout (or none)."""
    line = f"session_timeout_seconds: {session_timeout}\n" if session_timeout is not None else ""
    text = (
        "---\n"
        f"name: {name}\n"
        "team: engineering\n"
        "role: worker\n"
        "executor: claude\n"
        f"{line}"
        "---\n"
        "body\n"
    )
    runtime.agents_dir.mkdir(parents=True, exist_ok=True)
    (runtime.agents_dir / f"{name}.md").write_text(text)


def _write_org_config(runtime: RuntimeDir, *, session_timeout: int) -> None:
    runtime.org_config_path.write_text(f"session_timeout_seconds: {session_timeout}\n")


def test_falls_back_to_settings_default(orchestrator: Orchestrator) -> None:
    """No agent override, no org config -> Settings.session_timeout_seconds."""
    assert orchestrator._resolve_session_timeout("dev_agent") == 1800


def test_org_override_used_when_no_agent_override(
    orchestrator: Orchestrator, test_runtime: RuntimeDir
) -> None:
    _write_org_config(test_runtime, session_timeout=3600)
    _write_agent(test_runtime, "dev_agent", session_timeout=None)
    assert orchestrator._resolve_session_timeout("dev_agent") == 3600


def test_org_override_used_when_agent_file_missing(
    orchestrator: Orchestrator, test_runtime: RuntimeDir
) -> None:
    """Agent file may not exist yet (early bootstrap) — org override still wins."""
    _write_org_config(test_runtime, session_timeout=2400)
    assert orchestrator._resolve_session_timeout("missing_agent") == 2400


def test_agent_override_beats_org(
    orchestrator: Orchestrator, test_runtime: RuntimeDir
) -> None:
    _write_org_config(test_runtime, session_timeout=3600)
    _write_agent(test_runtime, "dev_agent", session_timeout=7200)
    assert orchestrator._resolve_session_timeout("dev_agent") == 7200


def test_agent_override_beats_settings_default(
    orchestrator: Orchestrator, test_runtime: RuntimeDir
) -> None:
    _write_agent(test_runtime, "dev_agent", session_timeout=600)
    assert orchestrator._resolve_session_timeout("dev_agent") == 600


def test_settings_default_respects_env_override(
    test_runtime: RuntimeDir, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bottom layer is itself env-overridable via OPC_SESSION_TIMEOUT_SECONDS,
    so a runtime with no org/agent overrides still picks up the env value."""
    monkeypatch.setenv("OPC_SESSION_TIMEOUT_SECONDS", "900")
    settings = Settings(project_root=test_runtime.root)
    db = Database(test_runtime.db_path)
    teams = TeamsRegistry.load(test_runtime)
    orch = Orchestrator(db=db, settings=settings, runtime=test_runtime, teams=teams)
    assert orch._resolve_session_timeout("any_agent") == 900

"""THR-103: daemon-side repo freshness at session spawn.

Repo-freshness moved from the per-workspace Claude Code PreToolUse hook into
the daemon: ``refresh_workspace_repos`` fast-forward-pulls every cloned repo
in ``_run_agent`` before the executor subprocess starts, uniformly for all
executors (claude, codex, opencode). The PreToolUse repo-refresh hook is no
longer emitted by ``build_settings_json``.
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from runtime.infrastructure.database import Database
from runtime.orchestrator import workspace_adapters
from runtime.orchestrator.executors import ExecutorResult
from runtime.orchestrator.orchestrator import Orchestrator
from runtime.orchestrator.teams import TeamsRegistry
from runtime.orchestrator.workspace_adapters import (
    build_settings_json,
    refresh_workspace_repos,
)


# ── helpers ──────────────────────────────────────────────────────────────

def _make_workspace_with_repos(root: Path, repo_names: list[str]) -> Path:
    """Create <root>/workspace with repos/<name>/.git for each name."""
    workspace = root / "workspace"
    for name in repo_names:
        (workspace / "repos" / name / ".git").mkdir(parents=True)
    return workspace


class _RecordingRun:
    """Fake subprocess.run that records invocations; optionally raises."""

    def __init__(self, raise_for_cwd: dict[str, Exception] | None = None):
        self.calls: list[dict] = []
        self._raise_for_cwd = raise_for_cwd or {}

    def __call__(self, cmd, **kwargs):
        cwd = str(kwargs.get("cwd", ""))
        self.calls.append({"cmd": cmd, **kwargs})
        for needle, exc in self._raise_for_cwd.items():
            if needle in cwd:
                raise exc
        return subprocess.CompletedProcess(cmd, returncode=0)


# ── (a) iterates ALL detected repos, ff-only per repo ────────────────────

def test_refresh_workspace_repos_pulls_every_detected_repo(tmp_path, monkeypatch):
    workspace = _make_workspace_with_repos(tmp_path, ["happyranch", "my-opc", "web-app"])
    fake_run = _RecordingRun()
    monkeypatch.setattr(workspace_adapters.subprocess, "run", fake_run)

    refresh_workspace_repos(workspace)

    assert len(fake_run.calls) == 3
    pulled_cwds = sorted(str(c["cwd"]) for c in fake_run.calls)
    assert pulled_cwds == sorted(
        str(workspace / "repos" / n) for n in ["happyranch", "my-opc", "web-app"]
    )
    for call in fake_run.calls:
        assert call["cmd"] == ["git", "pull", "--ff-only"]
        assert call["capture_output"] is True
        assert call["timeout"] == 30


def test_refresh_workspace_repos_skips_non_git_dirs_and_missing_repos_dir(
    tmp_path, monkeypatch,
):
    workspace = tmp_path / "workspace"
    # not-a-repo has no .git — detect_repo_names must exclude it.
    (workspace / "repos" / "not-a-repo").mkdir(parents=True)
    (workspace / "repos" / "real" / ".git").mkdir(parents=True)
    fake_run = _RecordingRun()
    monkeypatch.setattr(workspace_adapters.subprocess, "run", fake_run)

    refresh_workspace_repos(workspace)
    assert [str(c["cwd"]) for c in fake_run.calls] == [
        str(workspace / "repos" / "real")
    ]

    # A workspace with no repos/ dir at all: no pulls, no raise.
    fake_run.calls.clear()
    refresh_workspace_repos(tmp_path / "empty-workspace")
    assert fake_run.calls == []


# ── (b) failures never raise, never stop sibling repos ───────────────────

@pytest.mark.parametrize(
    "exc",
    [
        subprocess.CalledProcessError(1, ["git", "pull", "--ff-only"]),
        subprocess.TimeoutExpired(cmd=["git", "pull", "--ff-only"], timeout=30),
        OSError("git binary not found"),
    ],
    ids=["called-process-error", "timeout", "os-error"],
)
def test_refresh_workspace_repos_swallows_failure_and_continues(
    tmp_path, monkeypatch, exc,
):
    # Sorted order: a-failing < b-ok < c-ok. The FIRST repo fails; siblings
    # must still be pulled and the function must return normally.
    workspace = _make_workspace_with_repos(tmp_path, ["a-failing", "b-ok", "c-ok"])
    fake_run = _RecordingRun(raise_for_cwd={"a-failing": exc})
    monkeypatch.setattr(workspace_adapters.subprocess, "run", fake_run)

    refresh_workspace_repos(workspace)  # must not raise

    attempted = [str(c["cwd"]) for c in fake_run.calls]
    assert str(workspace / "repos" / "a-failing") in attempted
    assert str(workspace / "repos" / "b-ok") in attempted
    assert str(workspace / "repos" / "c-ok") in attempted


# ── (c) _run_agent refreshes BEFORE executor.run, every provider ─────────

_TASK_CONTEXT_CONTRACT_IDS = ["start-task", "jobs", "make-worktree", "thread"]


def _setup_protocol_skills(settings) -> None:
    for sid in _TASK_CONTEXT_CONTRACT_IDS:
        src = settings.get_protocol_dir() / "skills" / sid
        src.mkdir(parents=True, exist_ok=True)
        (src / "SKILL.md").write_text(f"# {sid}\n\nSkill body for {sid}.\n")


def _setup_agent_workspace(runtime, agent: str, provider: str) -> None:
    """Workspace + org/agents/<agent>.md pinned to the given executor."""
    from runtime.orchestrator.agent_def import AgentDef, render_agent_text

    ws = runtime.workspaces_dir / agent
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "task_history.md").write_text(f"# Task History: {agent}\n\n")
    (ws / "AGENTS.md").write_text(f"# Agent: {agent}\n")
    ad = AgentDef(
        name=agent, team="engineering", role="worker",
        executor=provider, allow_rules=(), repos={},
        enrolled_by=None, enrolled_at_task=None, enrolled_at=None,
        system_prompt=f"You are {agent}.", description="", model=None,
    )
    runtime.agents_dir.mkdir(parents=True, exist_ok=True)
    (runtime.agents_dir / f"{agent}.md").write_text(render_agent_text(ad))


@pytest.mark.parametrize("provider", ["claude", "codex", "opencode"])
def test_run_agent_refreshes_repos_before_executor_run(
    provider, test_settings, test_runtime, monkeypatch,
):
    _setup_protocol_skills(test_settings)
    test_runtime.root.mkdir(parents=True, exist_ok=True)
    _setup_agent_workspace(test_runtime, "dev_agent", provider)

    db = Database(test_runtime.db_path)
    teams = TeamsRegistry.load(test_runtime.root)
    orch = Orchestrator(
        db=db, settings=test_settings,
        paths=test_runtime, slug="test", teams=teams,
    )
    task_id = orch.create_task("Do the thing")

    events: list[str] = []
    workspaces_seen: list[Path] = []

    def fake_refresh(workspace):
        events.append("refresh_workspace_repos")
        workspaces_seen.append(workspace)

    monkeypatch.setattr(
        "runtime.orchestrator.orchestrator.refresh_workspace_repos", fake_refresh,
    )

    mock_executor = MagicMock()

    def fake_executor_run(**kwargs):
        events.append("executor.run")
        return ExecutorResult(
            success=True, duration_seconds=1, session_id=kwargs["session_id"],
        )

    mock_executor.run.side_effect = fake_executor_run

    with patch.object(orch, "_build_executor", return_value=mock_executor) as build:
        orch._run_agent(task_id, "dev_agent", "")

    assert build.call_args.args[0] == provider
    assert events == ["refresh_workspace_repos", "executor.run"]
    assert workspaces_seen == [test_runtime.workspaces_dir / "dev_agent"]


# ── (d) hook gone; permissions.allow unchanged (scope boundary guard) ────

def test_build_settings_json_emits_no_pretooluse_repo_refresh_hook(test_runtime):
    settings = build_settings_json(
        test_runtime, ["my-opc", "web-app"], agent_name="dev_agent",
    )
    assert settings["hooks"] == {}
    assert "PreToolUse" not in json.dumps(settings)
    assert "git pull" not in json.dumps(settings)


def test_build_settings_json_permissions_allow_unchanged(test_runtime):
    """Scope guard: removing the hook must not touch permissions.allow."""
    with_repos = build_settings_json(
        test_runtime, ["my-opc", "web-app"], agent_name="dev_agent",
    )
    without_repos = build_settings_json(test_runtime, [], agent_name="dev_agent")
    # The baseline allow rule survives, identical regardless of repo list.
    assert with_repos["permissions"]["allow"] == ["Bash(happyranch:*)"]
    assert with_repos["permissions"] == without_repos["permissions"]

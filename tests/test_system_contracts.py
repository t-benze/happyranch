"""Tests for runtime/skills/system_contracts.py — system-contract definitions,
SessionContext, and context-predicate resolution.

TDD coverage:
- All 5 system contracts have correct context predicates
- resolve_system_contracts_for_session returns correct contracts per context
- Repo-capable check: with/without repos under workspace/repos/
- SessionContext enum values match caller semantics
- list_system_contracts returns all 5 (single source of truth)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.skills.system_contracts import (
    SYSTEM_CONTRACTS,
    SessionContext,
    SystemContract,
    _workspace_has_repos,
    list_system_contracts,
    resolve_system_contracts_for_session,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def workspace_with_repos(tmp_path: Path) -> Path:
    """Workspace with two cloned repos."""
    ws = tmp_path / "ws"
    (ws / "repos" / "happyranch" / ".git").mkdir(parents=True)
    (ws / "repos" / "other" / ".git").mkdir(parents=True)
    return ws


@pytest.fixture
def workspace_without_repos(tmp_path: Path) -> Path:
    """Workspace with no repos/ directory."""
    ws = tmp_path / "ws_no_repos"
    ws.mkdir()
    return ws


@pytest.fixture
def workspace_empty_repos(tmp_path: Path) -> Path:
    """Workspace with repos/ dir but no cloned repos."""
    ws = tmp_path / "ws_empty"
    (ws / "repos").mkdir(parents=True)
    return ws


# ── SessionContext enum ────────────────────────────────────────────────


class TestSessionContext:
    def test_four_contexts_exist(self):
        assert len(SessionContext) == 4
        assert SessionContext.TASK == "task"
        assert SessionContext.THREAD == "thread"
        assert SessionContext.WAKE == "wake"
        assert SessionContext.DREAM == "dream"

    def test_from_string(self):
        assert SessionContext("task") == SessionContext.TASK
        assert SessionContext("thread") == SessionContext.THREAD
        assert SessionContext("wake") == SessionContext.WAKE
        assert SessionContext("dream") == SessionContext.DREAM

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            SessionContext("invalid")


# ── SystemContract dataclass ───────────────────────────────────────────


class TestSystemContractDataclass:
    def test_frozen(self):
        sc = SystemContract(
            id="test", name="Test", description="desc",
            when_to_use="when", source_path="p", contexts=(SessionContext.TASK,),
        )
        with pytest.raises(Exception):
            sc.id = "other"  # type: ignore[misc]

    def test_default_requires_repo_false(self):
        sc = SystemContract(
            id="test", name="Test", description="desc",
            when_to_use="when", source_path="p", contexts=(SessionContext.TASK,),
        )
        assert sc.requires_repo is False


# ── Single source of truth: SYSTEM_CONTRACTS ──────────────────────────


class TestSystemContractsTuple:
    """Verify the 5 system contracts are correctly defined."""

    def test_exactly_five_contracts(self):
        assert len(SYSTEM_CONTRACTS) == 5

    def test_all_five_ids(self):
        ids = {sc.id for sc in SYSTEM_CONTRACTS}
        assert ids == {"start-task", "jobs", "make-worktree", "thread", "dream"}

    def test_no_requires_repo_except_make_worktree(self):
        for sc in SYSTEM_CONTRACTS:
            if sc.id == "make-worktree":
                assert sc.requires_repo is True
            else:
                assert sc.requires_repo is False

    def test_start_task_contexts(self):
        sc = _get("start-task")
        assert set(sc.contexts) == {SessionContext.TASK, SessionContext.WAKE}
        assert SessionContext.THREAD not in sc.contexts
        assert SessionContext.DREAM not in sc.contexts

    def test_jobs_contexts(self):
        sc = _get("jobs")
        assert set(sc.contexts) == {
            SessionContext.TASK,
            SessionContext.THREAD,
            SessionContext.WAKE,
            SessionContext.DREAM,
        }

    def test_make_worktree_contexts(self):
        sc = _get("make-worktree")
        assert set(sc.contexts) == {
            SessionContext.TASK,
            SessionContext.THREAD,
            SessionContext.WAKE,
            SessionContext.DREAM,
        }

    def test_thread_contexts(self):
        sc = _get("thread")
        assert set(sc.contexts) == {
            SessionContext.TASK,
            SessionContext.THREAD,
            SessionContext.WAKE,
        }
        assert SessionContext.DREAM not in sc.contexts

    def test_dream_contexts(self):
        sc = _get("dream")
        assert set(sc.contexts) == {SessionContext.DREAM}
        assert SessionContext.TASK not in sc.contexts
        assert SessionContext.THREAD not in sc.contexts
        assert SessionContext.WAKE not in sc.contexts

    def test_list_system_contracts_returns_all_five(self):
        result = list_system_contracts()
        assert len(result) == 5
        assert {sc.id for sc in result} == {
            "start-task", "jobs", "make-worktree", "thread", "dream",
        }


# ── Context-predicate resolution ──────────────────────────────────────


class TestResolveSystemContracts:
    """TDD: context-exposure predicates red-first."""

    # -- TASK context --

    def test_task_context_gets_start_task(self, workspace_with_repos):
        result = resolve_system_contracts_for_session(
            SessionContext.TASK, workspace=workspace_with_repos,
        )
        ids = {sc.id for sc in result}
        assert "start-task" in ids

    def test_task_context_gets_jobs(self, workspace_with_repos):
        result = resolve_system_contracts_for_session(
            SessionContext.TASK, workspace=workspace_with_repos,
        )
        ids = {sc.id for sc in result}
        assert "jobs" in ids

    def test_task_context_gets_make_worktree_when_repos(self, workspace_with_repos):
        result = resolve_system_contracts_for_session(
            SessionContext.TASK, workspace=workspace_with_repos,
        )
        ids = {sc.id for sc in result}
        assert "make-worktree" in ids

    def test_task_context_no_make_worktree_without_repos(self, workspace_without_repos):
        result = resolve_system_contracts_for_session(
            SessionContext.TASK, workspace=workspace_without_repos,
        )
        ids = {sc.id for sc in result}
        assert "make-worktree" not in ids

    def test_task_context_gets_thread(self, workspace_with_repos):
        result = resolve_system_contracts_for_session(
            SessionContext.TASK, workspace=workspace_with_repos,
        )
        ids = {sc.id for sc in result}
        assert "thread" in ids

    def test_task_context_no_dream(self, workspace_with_repos):
        result = resolve_system_contracts_for_session(
            SessionContext.TASK, workspace=workspace_with_repos,
        )
        ids = {sc.id for sc in result}
        assert "dream" not in ids

    def test_task_context_with_repos_exact_ids(self, workspace_with_repos):
        result = resolve_system_contracts_for_session(
            SessionContext.TASK, workspace=workspace_with_repos,
        )
        ids = {sc.id for sc in result}
        assert ids == {"start-task", "jobs", "make-worktree", "thread"}

    def test_task_context_without_repos_exact_ids(self, workspace_without_repos):
        result = resolve_system_contracts_for_session(
            SessionContext.TASK, workspace=workspace_without_repos,
        )
        ids = {sc.id for sc in result}
        assert ids == {"start-task", "jobs", "thread"}

    # -- THREAD context --

    def test_thread_context_no_start_task(self, workspace_with_repos):
        result = resolve_system_contracts_for_session(
            SessionContext.THREAD, workspace=workspace_with_repos,
        )
        ids = {sc.id for sc in result}
        assert "start-task" not in ids

    def test_thread_context_gets_jobs(self, workspace_with_repos):
        result = resolve_system_contracts_for_session(
            SessionContext.THREAD, workspace=workspace_with_repos,
        )
        ids = {sc.id for sc in result}
        assert "jobs" in ids

    def test_thread_context_gets_thread(self, workspace_with_repos):
        result = resolve_system_contracts_for_session(
            SessionContext.THREAD, workspace=workspace_with_repos,
        )
        ids = {sc.id for sc in result}
        assert "thread" in ids

    def test_thread_context_no_dream(self, workspace_with_repos):
        result = resolve_system_contracts_for_session(
            SessionContext.THREAD, workspace=workspace_with_repos,
        )
        ids = {sc.id for sc in result}
        assert "dream" not in ids

    def test_thread_context_with_repos_exact_ids(self, workspace_with_repos):
        result = resolve_system_contracts_for_session(
            SessionContext.THREAD, workspace=workspace_with_repos,
        )
        ids = {sc.id for sc in result}
        assert ids == {"jobs", "make-worktree", "thread"}

    def test_thread_context_without_repos_exact_ids(self, workspace_without_repos):
        result = resolve_system_contracts_for_session(
            SessionContext.THREAD, workspace=workspace_without_repos,
        )
        ids = {sc.id for sc in result}
        assert ids == {"jobs", "thread"}

    # -- WAKE context --

    def test_wake_context_gets_start_task(self, workspace_with_repos):
        result = resolve_system_contracts_for_session(
            SessionContext.WAKE, workspace=workspace_with_repos,
        )
        ids = {sc.id for sc in result}
        assert "start-task" in ids

    def test_wake_context_gets_jobs(self, workspace_with_repos):
        result = resolve_system_contracts_for_session(
            SessionContext.WAKE, workspace=workspace_with_repos,
        )
        ids = {sc.id for sc in result}
        assert "jobs" in ids

    def test_wake_context_gets_thread(self, workspace_with_repos):
        result = resolve_system_contracts_for_session(
            SessionContext.WAKE, workspace=workspace_with_repos,
        )
        ids = {sc.id for sc in result}
        assert "thread" in ids

    def test_wake_context_no_dream(self, workspace_with_repos):
        result = resolve_system_contracts_for_session(
            SessionContext.WAKE, workspace=workspace_with_repos,
        )
        ids = {sc.id for sc in result}
        assert "dream" not in ids

    def test_wake_context_with_repos_exact_ids(self, workspace_with_repos):
        result = resolve_system_contracts_for_session(
            SessionContext.WAKE, workspace=workspace_with_repos,
        )
        ids = {sc.id for sc in result}
        assert ids == {"start-task", "jobs", "make-worktree", "thread"}

    def test_wake_context_without_repos_exact_ids(self, workspace_without_repos):
        result = resolve_system_contracts_for_session(
            SessionContext.WAKE, workspace=workspace_without_repos,
        )
        ids = {sc.id for sc in result}
        assert ids == {"start-task", "jobs", "thread"}

    # -- DREAM context --

    def test_dream_context_no_start_task(self, workspace_with_repos):
        result = resolve_system_contracts_for_session(
            SessionContext.DREAM, workspace=workspace_with_repos,
        )
        ids = {sc.id for sc in result}
        assert "start-task" not in ids

    def test_dream_context_gets_jobs(self, workspace_with_repos):
        result = resolve_system_contracts_for_session(
            SessionContext.DREAM, workspace=workspace_with_repos,
        )
        ids = {sc.id for sc in result}
        assert "jobs" in ids

    def test_dream_context_gets_dream(self, workspace_with_repos):
        result = resolve_system_contracts_for_session(
            SessionContext.DREAM, workspace=workspace_with_repos,
        )
        ids = {sc.id for sc in result}
        assert "dream" in ids

    def test_dream_context_no_thread(self, workspace_with_repos):
        result = resolve_system_contracts_for_session(
            SessionContext.DREAM, workspace=workspace_with_repos,
        )
        ids = {sc.id for sc in result}
        assert "thread" not in ids

    def test_dream_context_with_repos_exact_ids(self, workspace_with_repos):
        result = resolve_system_contracts_for_session(
            SessionContext.DREAM, workspace=workspace_with_repos,
        )
        ids = {sc.id for sc in result}
        assert ids == {"jobs", "make-worktree", "dream"}

    def test_dream_context_without_repos_exact_ids(self, workspace_without_repos):
        result = resolve_system_contracts_for_session(
            SessionContext.DREAM, workspace=workspace_without_repos,
        )
        ids = {sc.id for sc in result}
        assert ids == {"jobs", "dream"}


# ── Repo detection ────────────────────────────────────────────────────


class TestWorkspaceHasRepos:
    def test_has_repos_with_cloned_repo(self, workspace_with_repos):
        assert _workspace_has_repos(workspace_with_repos) is True

    def test_no_repos_dir(self, workspace_without_repos):
        assert _workspace_has_repos(workspace_without_repos) is False

    def test_empty_repos_dir(self, workspace_empty_repos):
        assert _workspace_has_repos(workspace_empty_repos) is False

    def test_non_existent_path(self, tmp_path):
        assert _workspace_has_repos(tmp_path / "nonexistent") is False


# ── Helpers ────────────────────────────────────────────────────────────


def _get(skill_id: str) -> SystemContract:
    for sc in SYSTEM_CONTRACTS:
        if sc.id == skill_id:
            return sc
    raise AssertionError(f"System contract not found: {skill_id}")

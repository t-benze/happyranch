"""System-contract skill definitions for the HappyRanch runtime.

System-contract skills are mandatory operating-contract skills that every
agent receives based on its session/context type. They are OUTSIDE the
toggleable managed catalog and are NOT manager-toggleable.

This module is the SINGLE SOURCE OF TRUTH for:
- Which skills are system contracts
- Which session contexts receive each contract
- Whether a contract requires repo access

Used by BOTH the injection code (``workspace_adapters.inject_system_contracts``)
and the CLI debug output (``skills effective``).

Phase 1 (this file): encodes the definitions + context predicates.
Phase 4 (future): the wholesale protocol/skills dump is removed and this
becomes the SOLE skill injection path.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class SessionContext(str, Enum):
    """The type of session being created.

    Maps to the 4 callers of refresh_session_skills / inject_system_contracts:
    - TASK: ordinary task/subtask session (orchestrator._run_agent)
    - THREAD: thread reply/bootstrap invocation (thread_runner.run_invocation)
    - WAKE: working-hours wake / task-followup (wake_runner.run_wake)
    - DREAM: scheduled dream invocation (dream_runner.run_dream)
    """

    TASK = "task"
    THREAD = "thread"
    WAKE = "wake"
    DREAM = "dream"


@dataclass(frozen=True)
class SystemContract:
    """A system-contract skill definition with its context-exposure predicate.

    Fields:
        id: skill slug (matches the directory name under protocol/skills/<id>/)
        name: human-readable name for debug/CLI output
        description: one-line purpose
        when_to_use: guidance for the agent
        source_path: relative path from the project root
        contexts: which session contexts receive this contract
        requires_repo: if True, only injected when the agent workspace has repos
    """

    id: str
    name: str
    description: str
    when_to_use: str
    source_path: str
    contexts: tuple[SessionContext, ...]
    requires_repo: bool = False


# ── The 5 system contracts (single source of truth) ──────────────────

SYSTEM_CONTRACTS: tuple[SystemContract, ...] = (
    SystemContract(
        id="start-task",
        name="Start Task",
        description=(
            "Mandatory task lifecycle: parameter parsing, memory/KB consultation, "
            "progress, completion callback, output placement."
        ),
        when_to_use="Use at the start of every task session.",
        source_path="protocol/skills/start-task/SKILL.md",
        contexts=(SessionContext.TASK, SessionContext.WAKE),
    ),
    SystemContract(
        id="jobs",
        name="Jobs",
        description=(
            "Safe handling for long-running, persistent, or permission-reviewed "
            "commands; prevents blocked sessions and captures founder-review workflow."
        ),
        when_to_use="Use when a command may not return synchronously.",
        source_path="protocol/skills/jobs/SKILL.md",
        contexts=(
            SessionContext.TASK,
            SessionContext.THREAD,
            SessionContext.WAKE,
            SessionContext.DREAM,
        ),
    ),
    SystemContract(
        id="make-worktree",
        name="Make Worktree",
        description=(
            "Required isolation before repo mutations under repos/<name>/; "
            "protects user work and concurrent sessions."
        ),
        when_to_use="Use before any git commit, checkout, or file edit inside repos/.",
        source_path="protocol/skills/make-worktree/SKILL.md",
        contexts=(
            SessionContext.TASK,
            SessionContext.THREAD,
            SessionContext.WAKE,
            SessionContext.DREAM,
        ),
        requires_repo=True,
    ),
    SystemContract(
        id="thread",
        name="Thread",
        description=(
            "Mandatory thread participation and task-session thread compose/post "
            "mechanics, including invocation-token callback rules."
        ),
        when_to_use=(
            "Use for thread invocations, task-followups, and task sessions "
            "that may compose or post to threads."
        ),
        source_path="protocol/skills/thread/SKILL.md",
        contexts=(SessionContext.TASK, SessionContext.THREAD, SessionContext.WAKE),
    ),
    SystemContract(
        id="dream",
        name="Dream",
        description=(
            "Private scheduled reflection contract with learning/KB-candidate "
            "handling and dream-specific completion callback."
        ),
        when_to_use="Use during scheduled dream invocations only.",
        source_path="protocol/skills/dream/SKILL.md",
        contexts=(SessionContext.DREAM,),
    ),
)


# ── Public API ────────────────────────────────────────────────────────


def list_system_contracts() -> list[SystemContract]:
    """Return all system contract definitions (single source of truth)."""
    return list(SYSTEM_CONTRACTS)


def resolve_system_contracts_for_session(
    context: SessionContext,
    *,
    workspace: Path,
) -> list[SystemContract]:
    """Return system contracts that should be injected for a given session context.

    Filters by:
    1. Session context match — contract.contexts must include ``context``.
    2. Repo requirement — if ``requires_repo`` is True, the workspace must
       have at least one git repository under ``repos/``.

    This is the single resolution function used by both the injection path
    (workspace_adapters.inject_system_contracts) and the CLI debug output
    (skills effective --context).
    """
    has_repos = _workspace_has_repos(workspace)
    result: list[SystemContract] = []
    for sc in SYSTEM_CONTRACTS:
        if context not in sc.contexts:
            continue
        if sc.requires_repo and not has_repos:
            continue
        result.append(sc)
    return result


def _workspace_has_repos(workspace: Path) -> bool:
    """Return True if the workspace has at least one cloned git repo under repos/."""
    repos_dir = workspace / "repos"
    if not repos_dir.is_dir():
        return False
    for child in repos_dir.iterdir():
        if child.is_dir() and (child / ".git").exists():
            return True
    return False

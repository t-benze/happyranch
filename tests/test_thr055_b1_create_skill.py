"""THR-055 B1 proof-first tests: create-skill system contract and protocol parity.

Covers the B1 requirement-to-proof matrix items that can be tested without
a full daemon app:
1. System-contract registration and materialization predicates
5. Protocol parity (doc audit)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.skills.system_contracts import (
    SYSTEM_CONTRACTS,
    SessionContext,
    resolve_system_contracts_for_session,
)


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def workspace_with_repos(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "repos" / "test" / ".git").mkdir(parents=True)
    return ws


@pytest.fixture
def workspace_without_repos(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


# ═══════════════════════════════════════════════════════════════════════════
# Requirement 1: System-contract registration and materialization
# ═══════════════════════════════════════════════════════════════════════════


class TestCreateSkillSystemContract:
    """create-skill is the 7th system contract, TASK-only, requires repos."""

    def test_create_skill_in_system_contracts(self):
        ids = {sc.id for sc in SYSTEM_CONTRACTS}
        assert "create-skill" in ids

    def test_create_skill_contexts_task_only(self):
        sc = next(sc for sc in SYSTEM_CONTRACTS if sc.id == "create-skill")
        assert sc.contexts == (SessionContext.TASK,)
        assert SessionContext.THREAD not in sc.contexts
        assert SessionContext.WAKE not in sc.contexts
        assert SessionContext.DREAM not in sc.contexts
        assert SessionContext.SCHEDULE not in sc.contexts
        assert SessionContext.BOOTSTRAP not in sc.contexts

    def test_create_skill_requires_repo(self):
        sc = next(sc for sc in SYSTEM_CONTRACTS if sc.id == "create-skill")
        assert sc.requires_repo is True

    def test_create_skill_resolved_for_task_with_repos(self, workspace_with_repos):
        result = resolve_system_contracts_for_session(
            SessionContext.TASK, workspace=workspace_with_repos,
        )
        ids = {sc.id for sc in result}
        assert "create-skill" in ids

    def test_create_skill_not_resolved_for_task_without_repos(self, workspace_without_repos):
        result = resolve_system_contracts_for_session(
            SessionContext.TASK, workspace=workspace_without_repos,
        )
        ids = {sc.id for sc in result}
        assert "create-skill" not in ids

    def test_create_skill_not_resolved_for_thread(self, workspace_with_repos):
        result = resolve_system_contracts_for_session(
            SessionContext.THREAD, workspace=workspace_with_repos,
        )
        ids = {sc.id for sc in result}
        assert "create-skill" not in ids

    def test_create_skill_not_resolved_for_dream(self, workspace_with_repos):
        result = resolve_system_contracts_for_session(
            SessionContext.DREAM, workspace=workspace_with_repos,
        )
        ids = {sc.id for sc in result}
        assert "create-skill" not in ids

    def test_create_skill_source_path(self):
        sc = next(sc for sc in SYSTEM_CONTRACTS if sc.id == "create-skill")
        assert sc.source_path == "protocol/skills/create-skill/SKILL.md"

    def test_seven_system_contracts(self):
        assert len(SYSTEM_CONTRACTS) == 7

    def test_all_seven_ids(self):
        ids = {sc.id for sc in SYSTEM_CONTRACTS}
        assert ids == {"start-task", "jobs", "make-worktree", "thread", "dream", "todos", "create-skill"}


# ═══════════════════════════════════════════════════════════════════════════
# Requirement 5: Protocol parity — doc audit
# ═══════════════════════════════════════════════════════════════════════════


class TestProtocolDocParity:
    """Protocol docs have been updated to reflect B1 create-skill path."""

    def _find_protocol_dir(self) -> Path:
        """Locate the protocol/ directory from the worktree or parent."""
        for candidate in [
            Path(__file__).parent / "protocol",
            Path(__file__).parent.parent / "protocol",
            Path(__file__).parent.parent.parent / "protocol",
        ]:
            if (candidate / "05b-agent-runtime.md").exists():
                return candidate
        pytest.skip("Protocol docs directory not found in test environment")

    def test_05b_mentions_create_skill_path(self):
        proto = self._find_protocol_dir()
        content = (proto / "05b-agent-runtime.md").read_text()
        assert "skills create" in content, "05b should mention skills create"
        # Verify no stale "sole agent" language remains
        assert "sole agent authoring workflow" not in content.lower(), \
            "05b must not claim single sole agent path"

    def test_05c_mentions_create_skill_path(self):
        proto = self._find_protocol_dir()
        content = (proto / "05c-orchestrator.md").read_text()
        assert "create-skill" in content, "05c should mention create-skill contract"
        assert "skills create" in content, "05c should mention skills create CLI"
        assert "B2" in content, "05c should document B2 deferral boundary"
        assert "sole agent" not in content.lower(), "05c must not claim single sole agent path"

    def test_05c_context_table_includes_create_skill(self):
        proto = self._find_protocol_dir()
        content = (proto / "05c-orchestrator.md").read_text()
        assert "``create-skill``" in content

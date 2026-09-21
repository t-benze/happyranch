"""THR-259 / TASK-8667 causal tests for the shared workspace-cleanup skill.

Contract-text checks plus behavioral resolver/materialization checks: the ONE
shared daily/manual skill must be a TASK system contract that does not require a
repository, must materialize into BOTH provider roots, and must carry the exact
dispatch markers, own-workspace scope, inventory-only rule, approved seq185
exception rule, and non-force mechanisms.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.orchestrator.workspace_adapters import materialize_workspace_skills
from runtime.skills.skill_md import frontmatter_admission_violations
from runtime.skills.sources import bundled_skills_dir
from runtime.skills.system_contracts import (
    SessionContext,
    list_system_contracts,
    resolve_system_contracts_for_session,
)

SKILL_DIR = (
    Path(__file__).resolve().parents[1]
    / "runtime" / "skills" / "bundled" / "workspace-cleanup"
)
SKILL_MD = SKILL_DIR / "SKILL.md"
HELPER = SKILL_DIR / "scripts" / "check_path_use.py"
MANUAL_FIRST_LINE = "HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (manual-dispatch)"
DAEMON_MARKER = "HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (daemon-triggered)"


@pytest.fixture(scope="module")
def body() -> str:
    assert SKILL_MD.is_file(), f"missing packaged skill: {SKILL_MD}"
    return SKILL_MD.read_text(encoding="utf-8")


def test_frontmatter_is_admissible_and_named_for_slug(body):
    assert frontmatter_admission_violations(body) == []
    assert body.startswith("---\nname: workspace-cleanup\n")


def test_helper_is_present_in_skill_package():
    assert HELPER.is_file()
    assert HELPER.read_text(encoding="utf-8").startswith("#!/usr/bin/env python3")


def test_manual_and_daemon_dispatch_markers(body):
    lines = {ln.strip() for ln in body.splitlines()}
    assert MANUAL_FIRST_LINE in lines
    assert DAEMON_MARKER in lines


def test_own_workspace_scope_and_inventory_only(body):
    normalized = " ".join(body.split())
    assert "inventory-only" in normalized
    assert "your own workspace" in normalized
    assert "Never touch another agent's workspace" in normalized
    assert "Never use elevation" in normalized


def test_non_force_only_and_no_broad_deletion(body):
    normalized = " ".join(body.split())
    assert "worktree remove" in normalized
    assert "Never** `--force`" in normalized or "Never `--force`" in normalized
    assert "rm -rf" in normalized
    assert "never deletes production residue" in normalized


def test_approved_exception_names_and_roles(body):
    normalized = " ".join(body.split())
    for name in ("sshd-session", "systemd", "sd-pam", "ssh-agent",
                 "gpg-agent", "gcr-ssh-agent"):
        assert name in normalized
    assert "exact readable process name AND its exact bounded cgroup" in normalized
    assert "deliberately" in normalized and "not inspected" in normalized


def test_retention_and_ordinal_contract(body):
    normalized = " ".join(body.split())
    assert "first **two**" in normalized
    assert "24 hours" in normalized
    assert "seven terminal days" in normalized
    assert "zero" in normalized


def test_helper_reference_is_relative_to_skill(body):
    assert "scripts/check_path_use.py" in body


# ── Behavioral resolver/materialization ───────────────────────────────────

def test_resolver_includes_workspace_cleanup_without_repos(tmp_path):
    workspace = tmp_path / "ws_no_repos"
    workspace.mkdir()
    resolved = resolve_system_contracts_for_session(
        SessionContext.TASK, workspace=workspace,
    )
    by_id = {sc.id: sc for sc in resolved}
    assert "workspace-cleanup" in by_id
    assert by_id["workspace-cleanup"].requires_repo is False
    assert by_id["workspace-cleanup"].source_path == (
        "runtime/skills/bundled/workspace-cleanup/SKILL.md"
    )
    # not exposed to non-task contexts
    for context in (SessionContext.THREAD, SessionContext.DREAM,
                    SessionContext.BOOTSTRAP):
        others = {sc.id for sc in resolve_system_contracts_for_session(
            context, workspace=workspace)}
        assert "workspace-cleanup" not in others


def test_declared_contract_source_matches_release(body):
    contracts = {sc.id: sc for sc in list_system_contracts()}
    contract = contracts["workspace-cleanup"]
    source = bundled_skills_dir() / contract.id / "SKILL.md"
    assert source.is_file()
    assert source.read_text(encoding="utf-8") == body


def test_materializes_into_both_provider_roots_for_no_repo_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    materialize_workspace_skills(
        workspace,
        Settings(),
        slug="test",
        context="task",
        provider="codex",
        agent_name="dev_agent",
        team="engineering",
        skills_root=tmp_path / "no-managed-skills",
    )
    expected = SKILL_MD.read_text(encoding="utf-8")
    for root in (workspace / ".claude" / "skills", workspace / ".agents" / "skills"):
        marker = root / "workspace-cleanup" / "SKILL.md"
        assert marker.is_file(), f"workspace-cleanup not materialized at {marker}"
        assert marker.read_text(encoding="utf-8") == expected
    # no-repo workspace must not receive repo-only contracts
    assert not (workspace / ".agents" / "skills" / "make-worktree").exists()

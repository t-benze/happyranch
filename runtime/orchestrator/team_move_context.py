"""Pure native context plans and same-operation workspace readback.

No bootstrap, cloning, refresh, re-enrollment or memory initialization. Native
permission renderers and Codex launch enforcement remain unchanged.
"""
from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from runtime.infrastructure.team_move_store import ContextEvidence, TeamMoveError, _digest
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.executor_registry import get_registry
from runtime.orchestrator import workspace_adapters as native
from runtime.orchestrator.team_move_canonical import read_regular


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


@dataclass(frozen=True)
class ContextPlan:
    agent_id: str
    workspace: Path
    executor: str
    adapter: str
    instruction_bytes: bytes
    settings_bytes: bytes | None
    specs: tuple[dict, ...]
    launch_inputs: dict

    def manifest(self):
        return {"agent_id": self.agent_id, "workspace": str(self.workspace), "executor": self.executor,
            "adapter": self.adapter, "AGENTS.md": _digest(self.instruction_bytes), "CLAUDE.md": "AGENTS.md",
            "settings": None if self.settings_bytes is None else _digest(self.settings_bytes),
            "skills": list(self.specs), "launch_inputs": self.launch_inputs}


def plan_context(org, definition) -> ContextPlan:
    paths = OrgPaths(org.root)
    workspace = paths.workspaces_dir / definition.name
    if workspace.resolve(strict=True) != workspace or not stat.S_ISDIR(workspace.lstat().st_mode):
        raise TeamMoveError("team_move_original_workspace_required")
    for subdir in (".claude", ".agents"):
        path = workspace / subdir
        if os.path.lexists(path):
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
                raise TeamMoveError("team_move_generated_parent_unsupported")
    settings_path = workspace / ".claude/settings.json"
    retained_settings = None
    if os.path.lexists(settings_path):
        contents, _settings_identity = read_regular(settings_path)
        retained_settings = _digest(contents)
    profile = get_registry().get_profile(definition.executor)
    if profile is None or profile.workspace_adapter_id not in {"claude", "codex"}:
        raise TeamMoveError("team_move_workspace_adapter_unsupported")
    adapter = profile.workspace_adapter_id
    owner = (native.ClaudeWorkspaceAdapter if adapter == "claude" else native.CodexWorkspaceAdapter)(org.settings, paths, slug=org.slug)
    render = owner.render_claude_md if adapter == "claude" else owner.render_agents_md
    instructions = render(workspace, definition.name, definition.system_prompt).encode()
    settings = None
    if adapter == "claude":
        # Only role/team/body change, so the canonical allow rules/repos used by
        # the unchanged native renderer are identical before and after.
        settings = (json.dumps(native.build_settings_json(paths, list(definition.repos), agent_name=definition.name), indent=2) + "\n").encode()
    specs = native.plan_workspace_skills(workspace, org.settings, slug=org.slug,
        provider=adapter, agent_name=definition.name, team=definition.team,
        skills_root=(org.settings.project_root / "runtime" / "skills"), org_root=org.root, db=org.db)
    launch = {"executor": definition.executor, "model": definition.model,
        "reasoning_effort": definition.reasoning_effort, "allow_rules": list(definition.allow_rules),
        "repos": definition.repos, "workspace": str(workspace),
        "codex_sandbox_mode": org.settings.codex_sandbox_mode,
        "native_allow_rules": native.allow_rules_for_agent(paths, definition.name),
        "native_bash_prefixes": native.bash_allow_prefixes_for_agent(paths, definition.name),
        "retained_settings_digest": retained_settings if adapter == "codex" else None}
    return ContextPlan(definition.name, workspace, definition.executor, adapter,
        instructions, settings, tuple(specs), launch)


def materialize_context(org, plan: ContextPlan) -> ContextEvidence:
    """Native owners perform effects; exact plan and integrity readback decide.

    Partial package/store/instruction effects remain in their native owner. This
    operation never deletes or recursively compensates unrelated shared state.
    A caught custom build omission is detected by exact expected-spec equality.
    """
    workspace = plan.workspace
    with native._workspace_skills_transaction(workspace):
        native.write_canonical_instruction_pair(workspace, plan.instruction_bytes.decode())
        if plan.settings_bytes is not None:
            native.ClaudeWorkspaceAdapter(org.settings, OrgPaths(org.root), slug=org.slug).write_settings_json(
                workspace, repo_names=list(plan.launch_inputs["repos"]), agent_name=plan.agent_id)
        specs = native.materialize_workspace_skills_union(workspace, org.settings,
            slug=org.slug, contexts=["task", "thread", "wake", "dream", "schedule", "bootstrap"],
            provider=plan.adapter, agent_name=plan.agent_id, team="default",
            skills_root=(org.settings.project_root / "runtime" / "skills"), org_root=org.root, db=org.db)
        if canonical(specs) != canonical(list(plan.specs)):
            raise TeamMoveError("team_move_context_partial_native_effects")
        return verify_context(org, plan)


def verify_context(org, plan: ContextPlan) -> ContextEvidence:
    """Read back native outputs, re-run the native package/link integrity owner."""
    if native.instruction_pair_refusal(plan.workspace) is not None:
        raise TeamMoveError("team_move_context_instruction_pair")
    raw, _identity = read_regular(plan.workspace / "AGENTS.md")
    if raw != plan.instruction_bytes or os.readlink(plan.workspace / "CLAUDE.md") != "AGENTS.md":
        raise TeamMoveError("team_move_context_instructions_changed")
    if plan.settings_bytes is not None:
        raw, _identity = read_regular(plan.workspace / ".claude/settings.json")
        if raw != plan.settings_bytes:
            raise TeamMoveError("team_move_context_settings_changed")
    native.validate_workspace_skills_integrity(plan.workspace, list(plan.specs), settings=org.settings,
        db=org.db, agent_name=plan.agent_id)
    manifest = canonical(plan.manifest())
    return ContextEvidence(manifest_digest=_digest(manifest), selection_digest=_digest(canonical(list(plan.specs))),
        integrity_digest=_digest(manifest + b"\x00native-package-link-integrity"))

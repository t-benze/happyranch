from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from src.config import Settings

if TYPE_CHECKING:
    from src.runtime import RuntimeDir

logger = logging.getLogger(__name__)



def _copy_skills_tree(src: Path, dst: Path) -> None:
    """Copy each skill directory from ``src`` into ``dst``, replacing existing copies.

    Used by both Claude (``<ws>/.claude/skills/``) and Codex
    (``<ws>/.agents/skills/``) workspaces. Codex CLI ≥0.125 discovers skills by
    walking ``.agents/skills/`` from the working directory up to the repo root,
    so the destination differs by platform but the source — ``protocol/skills/``
    — is shared.
    """
    if not src.exists():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for child in src.iterdir():
        target = dst / child.name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(child, target)


def _format_allow_rule(prefix: str, *, cli: bool) -> str:
    """Render a Bash prefix in one of the two equivalent permission syntaxes.

    Settings.json uses ``Bash(<cmd>:*)``; the ``--allowedTools`` CLI flag uses
    ``Bash(<cmd> *)``. Both prefix-match the same invocations in Claude Code,
    but the project has historically used different separators in the two
    surfaces and we preserve that to minimize diff noise against prior tests
    and released workspaces.
    """
    sep = " " if cli else ":"
    return f"Bash({prefix}{sep}*)"


def allow_rules_for_agent(
    runtime: "RuntimeDir", agent_name: str | None, *, cli: bool,
) -> list[str]:
    """Build the Bash allow-rule list for ``agent_name``.

    Baseline ``opc`` is always included (the agent-callback channel).
    Additional prefixes come from the agent's ``allow_rules`` frontmatter
    field in ``<runtime>/org/agents/<name>.md``.
    """
    from src.orchestrator import prompt_loader
    rules = [_format_allow_rule("opc", cli=cli)]
    if agent_name is None:
        return rules
    for prefix in prompt_loader.allow_rules_for_agent(runtime, agent_name):
        rules.append(_format_allow_rule(prefix, cli=cli))
    return rules


def build_settings_json(
    runtime: "RuntimeDir",
    repo_names: list[str],
    agent_name: str | None = None,
) -> dict:
    """Build .claude/settings.json with a git pull hook for all repos."""
    if repo_names:
        pull_cmds = " && ".join(
            f"(cd repos/{name} && git pull --ff-only 2>/dev/null; true)"
            for name in repo_names
        )
        hooks = {
            "PreToolUse": [
                {
                    "matcher": "Bash|Read|Grep|Glob",
                    "hooks": [
                        {"type": "command", "command": pull_cmds, "once": True}
                    ],
                }
            ]
        }
    else:
        hooks = {}

    return {
        "permissions": {
            "allow": allow_rules_for_agent(runtime, agent_name, cli=False),
        },
        "hooks": hooks,
    }


@dataclass(slots=True)
class PersistentWorkspaceSetup:
    """Shared workspace files that every provider keeps up to date."""

    settings: Settings

    def ensure(self, workspace: Path, agent_name: str) -> list[str]:
        """Create persistent files and return detected cloned repo names."""
        workspace.mkdir(parents=True, exist_ok=True)

        # Migrate legacy recent_tasks.md → task_history.md in place so no
        # history is lost on workspaces created before the rename.
        legacy = workspace / "recent_tasks.md"
        renamed = workspace / "task_history.md"
        if legacy.exists() and not renamed.exists():
            legacy.rename(renamed)

        for filename, default_content in [
            ("learnings.md", f"# Learnings: {agent_name}\n\n"),
            ("task_history.md", f"# Task History: {agent_name}\n\n"),
        ]:
            path = workspace / filename
            if not path.exists():
                path.write_text(default_content)

        return self.detect_repo_names(workspace)

    def detect_repo_names(self, workspace: Path) -> list[str]:
        repos_dir = workspace / "repos"
        if not repos_dir.exists():
            return []
        return sorted(
            d.name for d in repos_dir.iterdir()
            if d.is_dir() and (d / ".git").exists()
        )


class ClaudeWorkspaceAdapter:
    """Bootstrap and maintain Claude Code workspaces."""

    provider_name = "claude"

    def __init__(self, settings: Settings, runtime: "RuntimeDir") -> None:
        self._settings = settings
        self._runtime = runtime
        self._persistent = PersistentWorkspaceSetup(settings)

    def write_settings_json(
        self,
        workspace: Path,
        repo_names: list[str] | None = None,
        agent_name: str | None = None,
    ) -> None:
        """Write .claude/settings.json to workspace."""
        claude_dir = workspace / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        settings_data = build_settings_json(
            self._runtime, repo_names or [], agent_name=agent_name,
        )
        (claude_dir / "settings.json").write_text(
            json.dumps(settings_data, indent=2) + "\n"
        )

    def write_claude_md(
        self,
        workspace: Path,
        agent_name: str,
        system_prompt: str,
        repo_names: list[str] | None = None,
    ) -> None:
        """Write CLAUDE.md to workspace with system prompt and context pointers.

        ``repo_names`` is accepted for API compatibility but is not listed
        inline — CLAUDE.md just points at ``agent.yaml`` as the source of
        truth so the repo list doesn't drift between the two files.
        """
        workspace.mkdir(parents=True, exist_ok=True)
        sections = self._build_sections(
            agent_name,
            system_prompt,
            include_start_task=True,
            repo_refresh_note=(
                "repositories cloned under `repos/`. Each is kept fresh via the "
                "PreToolUse hook in `.claude/settings.json`."
            ),
            callback_note=(
                "The `--from-file` form is mandatory here — multi-line `opc` "
                "invocations are blocked by the `Bash(opc:*)` permission rule."
            ),
            workflow_section=[
                "Every task arrives via the orchestrator's prompt. Use the **start-task** skill",
                "(in `.claude/skills/start-task/`) to parse parameters and report completion via",
                "`opc report-completion`. Mid-task learnings go through `opc learning`.\n",
            ],
        )
        (workspace / "CLAUDE.md").write_text("\n".join(sections))

    def _build_sections(
        self,
        agent_name: str,
        system_prompt: str,
        *,
        include_start_task: bool,
        repo_refresh_note: str,
        callback_note: str,
        workflow_section: list[str],
    ) -> list[str]:
        sections = [
            f"# Agent: {agent_name}\n",
            "## System Prompt\n",
            system_prompt.strip() + "\n",
            "## Available Repositories\n",
            "See `agent.yaml` in this workspace for the authoritative list of",
            repo_refresh_note + "\n",
            "## Persistent Files\n",
            "- `learnings.md` -- your accumulated operational learnings",
            "- `task_history.md` -- read-only, updated by orchestrator\n",
            "## Knowledge Base (shared across agents)\n",
            "Path: `<runtime>/kb/`. Read: everyone. Write: any agent (via `--from-file`).",
            "Delete: any team manager (audited); founder via `--as-founder`. Full rules: `protocol/06-knowledge-base.md`.",
        ]
        if include_start_task:
            sections.extend([
                "The **start-task** skill's *Consult KB* and *Contribute to KB* steps are",
                "mandatory — do not skip them.\n",
            ])
        sections.extend([
            "Read:",
            "```",
            "opc kb list [--topic <t>] [--type reference|precedent]",
            "opc kb search \"<keywords>\"",
            "opc kb get <slug>",
            "```\n",
            "Write (durable, cross-agent knowledge only — regulations, partner-API quirks,",
            "payment flows, precedents; **not** task-specific notes):",
            "```",
            "opc kb add --agent <you> --from-file /tmp/kb-<slug>.md",
            "opc kb update <slug> --agent <you> --from-file /tmp/kb-<slug>.md",
            "```",
            "Payload file needs YAML frontmatter (`slug`, `title`, `type`, `topic`,",
            "optional `tags`, `source_task`) followed by a markdown body.",
            callback_note + "\n",
            "## Task Recall\n",
            "Past task context (brief, completion summary, artifacts) is retrievable via:",
            "```",
            "opc recall <task_id>                              # brief + final summary",
            "opc recall <task_id> --tree                       # list files under artifacts/<task_id>/",
            "opc recall <task_id> --fetch-artifact <relpath>   # read one artifact",
            "```",
            "Use when the current brief references a prior task, when you need to revisit",
            "your own earlier output before reworking, or when a KB precedent points to",
            "`source_task: TASK-xyz`. Your own recent activity is also summarized in",
            "`task_history.md` at the workspace root.\n",
            "## Workflow\n",
        ])
        sections.extend(workflow_section)
        return sections

    def ensure_workspace_ready(
        self,
        workspace: Path,
        agent_name: str,
        system_prompt: str,
    ) -> None:
        """Make sure an agent workspace has every file the orchestrator requires."""
        repo_names = self._persistent.ensure(workspace, agent_name)

        # CLAUDE.md, settings.json, and the skills tree are always regenerated
        # so workspaces carried over from older code self-heal.
        self.write_claude_md(workspace, agent_name, system_prompt, repo_names=repo_names)
        self._copy_skills(workspace)
        self.write_settings_json(
            workspace, repo_names=repo_names, agent_name=agent_name,
        )

    def _copy_skills(self, workspace: Path) -> None:
        """Copy protocol/skills/ tree into workspace/.claude/skills/."""
        _copy_skills_tree(
            self._settings.get_protocol_dir() / "skills",
            workspace / ".claude" / "skills",
        )


class CodexWorkspaceAdapter:
    """Bootstrap and maintain Codex workspaces."""

    provider_name = "codex"

    def __init__(self, settings: Settings, runtime: "RuntimeDir") -> None:
        self._settings = settings
        self._runtime = runtime
        self._persistent = PersistentWorkspaceSetup(settings)

    def write_agents_md(
        self,
        workspace: Path,
        agent_name: str,
        system_prompt: str,
        repo_names: list[str] | None = None,
    ) -> None:
        """Write AGENTS.md to workspace with system prompt and context pointers.

        Codex CLI ≥0.125 discovers skills by walking ``.agents/skills/`` from
        the working directory up to the repo root, so the same
        ``protocol/skills/`` tree that Claude consumes is copied into
        ``<ws>/.agents/skills/`` by ``_copy_skills``. AGENTS.md therefore
        only points at the **start-task** skill — it does not re-inline the
        completion contract. The skill itself is the source of truth.
        """
        workspace.mkdir(parents=True, exist_ok=True)
        sections = ClaudeWorkspaceAdapter(self._settings, self._runtime)._build_sections(
            agent_name,
            system_prompt,
            include_start_task=True,
            repo_refresh_note=(
                "repositories cloned under `repos/`. Refresh repository state "
                "yourself when the task requires it; do not assume Claude-specific "
                "workspace hooks exist."
            ),
            callback_note=(
                "Use the `--from-file` form to keep the callback contract stable "
                "across executors and avoid shell quoting issues."
            ),
            workflow_section=[
                "Every task arrives via the orchestrator's prompt. Use the **start-task** skill",
                "(in `.agents/skills/start-task/`) to parse parameters and report completion via",
                "`opc report-completion`. Mid-task learnings go through `opc learning`.\n",
            ],
        )
        (workspace / "AGENTS.md").write_text("\n".join(sections))

    def _copy_skills(self, workspace: Path) -> None:
        """Copy protocol/skills/ tree into workspace/.agents/skills/."""
        _copy_skills_tree(
            self._settings.get_protocol_dir() / "skills",
            workspace / ".agents" / "skills",
        )

    def ensure_workspace_ready(
        self,
        workspace: Path,
        agent_name: str,
        system_prompt: str,
    ) -> None:
        """Make sure a Codex workspace has the shared persistent files and bootstrap."""
        self._persistent.ensure(workspace, agent_name)
        self.write_agents_md(workspace, agent_name, system_prompt)
        self._copy_skills(workspace)

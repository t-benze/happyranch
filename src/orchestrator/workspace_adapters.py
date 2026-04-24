from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from src.config import Settings

logger = logging.getLogger(__name__)



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
    settings: Settings, agent_name: str | None, *, cli: bool,
    db: object | None = None,
) -> list[str]:
    """Build the Bash allow-rule list for ``agent_name``.

    Baseline ``opc`` is always included (the agent-callback channel).
    Additional prefixes come from one of two sources (in priority order):
      1. The DB enrollment row's ``allow_rules`` field, if ``db`` is provided
         and the agent has an enrollment with a non-empty allow_rules list.
      2. The ``### Allow Rules`` subsection of the agent's role in the
         protocol markdown (covers built-in agents like engineering_head).

    See ``protocol/02-system-prompts-managers.md`` for the per-manager grants.
    """
    from src.orchestrator import prompt_loader
    rules = [_format_allow_rule("opc", cli=cli)]
    if agent_name is None:
        return rules

    prefixes: list[str] = []
    if db is not None:
        enrollment = db.get_enrollment(agent_name)  # type: ignore[union-attr]
        if enrollment is not None:
            prefixes = list(enrollment.get("allow_rules") or ())

    if not prefixes:
        prefixes = list(prompt_loader.allow_rules_for(
            settings.get_protocol_dir(), agent_name,
        ))

    for prefix in prefixes:
        rules.append(_format_allow_rule(prefix, cli=cli))
    return rules


def build_settings_json(
    settings: Settings,
    repo_names: list[str],
    agent_name: str | None = None,
) -> dict:
    """Build .claude/settings.json with a git pull hook for all repos."""
    if repo_names:
        # Pull all repos on first tool use.
        pull_cmds = " && ".join(
            f"(cd repos/{name} && git pull --ff-only 2>/dev/null; true)"
            for name in repo_names
        )
        hooks = {
            "PreToolUse": [
                {
                    "matcher": "Bash|Read|Grep|Glob",
                    "hooks": [
                        {
                            "type": "command",
                            "command": pull_cmds,
                            "once": True,
                        }
                    ],
                }
            ]
        }
    else:
        hooks = {}

    return {
        "permissions": {
            # `opc` is pinned open for every agent so callbacks
            # (report-completion, learning, etc.) can never be silently
            # blocked by auto-mode prompting. Per-agent extras come from the
            # ``### Allow Rules`` subsection in the protocol markdown.
            # Anything not in this list falls under Claude Code's default
            # auto-mode behavior.
            "allow": allow_rules_for_agent(settings, agent_name, cli=False),
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
            ("scorecard.md", "# Scorecard\n\nNo performance data yet. Tier: green (default)\n"),
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

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
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
        settings_data = build_settings_json(self._settings, repo_names or [], agent_name=agent_name)
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
            "- `scorecard.md` -- read-only, updated by orchestrator",
            "- `task_history.md` -- read-only, updated by orchestrator\n",
            "## Knowledge Base (shared across agents)\n",
            "Path: `<runtime>/kb/`. Read: everyone. Write: any agent (via `--from-file`).",
            "Delete: engineering_head only. Full rules: `protocol/06-knowledge-base.md`.",
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
        ])
        if include_start_task:
            sections.extend([
                "## Workflow\n",
                "Every task arrives via the orchestrator's prompt. Use the **start-task** skill",
                "(in `.claude/skills/start-task/`) to parse parameters and report completion via",
                "`opc report-completion`. Mid-task learnings go through `opc learning`.\n",
            ])
        else:
            sections.extend([
                "## Workflow\n",
                "Every task arrives via the orchestrator's prompt. Use the injected task",
                "parameters directly and report completion via `opc report-completion`.",
                "Mid-task learnings go through `opc learning`.\n",
            ])
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
        src = self._settings.get_protocol_dir() / "skills"
        if not src.exists():
            return
        dst = workspace / ".claude" / "skills"
        dst.mkdir(parents=True, exist_ok=True)
        for child in src.iterdir():
            target = dst / child.name
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(child, target)


class CodexWorkspaceAdapter:
    """Bootstrap and maintain Codex workspaces."""

    provider_name = "codex"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._persistent = PersistentWorkspaceSetup(settings)

    def write_agents_md(
        self,
        workspace: Path,
        agent_name: str,
        system_prompt: str,
        repo_names: list[str] | None = None,
    ) -> None:
        """Write AGENTS.md to workspace with system prompt and context pointers."""
        workspace.mkdir(parents=True, exist_ok=True)
        sections = ClaudeWorkspaceAdapter(self._settings)._build_sections(
            agent_name,
            system_prompt,
            include_start_task=False,
            repo_refresh_note=(
                "repositories cloned under `repos/`. Refresh repository state "
                "yourself when the task requires it; do not assume Claude-specific "
                "workspace hooks exist."
            ),
            callback_note=(
                "Use the `--from-file` form to keep the callback contract stable "
                "across executors and avoid shell quoting issues."
            ),
        )
        (workspace / "AGENTS.md").write_text("\n".join(sections))

    def ensure_workspace_ready(
        self,
        workspace: Path,
        agent_name: str,
        system_prompt: str,
    ) -> None:
        """Make sure a Codex workspace has the shared persistent files and bootstrap."""
        self._persistent.ensure(workspace, agent_name)
        self.write_agents_md(workspace, agent_name, system_prompt)

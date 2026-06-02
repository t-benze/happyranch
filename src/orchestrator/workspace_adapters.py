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
    from src.orchestrator._paths import OrgPaths

logger = logging.getLogger(__name__)


# Test override: when set (via monkeypatch), takes precedence over the
# settings-derived skills source. Production code leaves this ``None`` and
# adapters resolve the source via ``self._settings.get_protocol_dir() / "skills"``.
_SKILLS_SRC: Path | None = None


def _resolve_skills_src(settings: Settings) -> Path:
    """Source directory for ``protocol/skills/``.

    Honors the module-level ``_SKILLS_SRC`` test override before falling back
    to the settings-derived path so unit tests can stand up a fake skills tree
    in ``tmp_path`` without altering production behavior.
    """
    if _SKILLS_SRC is not None:
        return _SKILLS_SRC
    return settings.get_protocol_dir() / "skills"


def _copy_skills_tree(src: Path, dst: Path, *, slug: str) -> None:
    """Copy each skill directory from ``src`` into ``dst``, replacing existing copies.

    Used by both Claude (``<ws>/.claude/skills/``) and Codex
    (``<ws>/.agents/skills/``) workspaces. Codex CLI ≥0.125 discovers skills by
    walking ``.agents/skills/`` from the working directory up to the repo root,
    so the destination differs by platform but the source — ``protocol/skills/``
    — is shared.

    Every ``.md`` file has ``{ORG_SLUG}`` substituted with ``slug`` so example
    ``happyranch`` invocations carry the per-workspace ``--org`` automatically. Other
    file types are copied byte-for-byte.
    """
    if not src.exists():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for child in src.iterdir():
        target = dst / child.name
        if target.exists():
            shutil.rmtree(target)
        if child.is_dir():
            _copy_skill_dir(child, target, slug=slug)
        else:
            _copy_skill_file(child, target, slug=slug)


def _copy_skill_dir(src: Path, dst: Path, *, slug: str) -> None:
    """Recursively copy ``src`` to ``dst``, substituting ``{ORG_SLUG}`` in .md files."""
    dst.mkdir(parents=True, exist_ok=True)
    for child in src.iterdir():
        target = dst / child.name
        if child.is_dir():
            _copy_skill_dir(child, target, slug=slug)
        else:
            _copy_skill_file(child, target, slug=slug)


def _copy_skill_file(src: Path, dst: Path, *, slug: str) -> None:
    """Copy a single skill file. ``.md`` files get ``{ORG_SLUG}`` substituted."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix == ".md":
        text = src.read_text()
        dst.write_text(text.replace("{ORG_SLUG}", slug))
    else:
        shutil.copy2(src, dst)


def _learnings_bootstrap_section(workspace: Path) -> list[str]:
    """Returns the 'Persistent Files' + 'Your Learnings' block.

    Branches on workspace state: flat learnings.md vs migrated learnings/.
    """
    flat = workspace / "learnings.md"
    learnings_dir = workspace / "learnings"
    index = learnings_dir / "_index.md"

    if learnings_dir.exists() and index.exists():
        index_body = index.read_text()
        return [
            "## Persistent Files\n",
            "- `learnings/_index.md` -- index of your operational learnings",
            "  (full bodies via `happyranch learning get`)",
            "- `task_history.md` -- read-only, updated by orchestrator\n",
            "## Your Learnings\n",
            index_body,
            "\nFetch any entry's body:",
            "```",
            "happyranch learning get --org <slug> --agent <you> <LRN-NNN-or-slug>",
            "```",
            "Write a new learning (file payload with slug/title/topic/tags/body):",
            "```",
            "happyranch learning add --org <slug> --agent <you> --from-file <path>",
            "```",
            "Update an existing learning:",
            "```",
            "happyranch learning update --org <slug> --agent <you> <LRN-NNN> --from-file <path>",
            "```",
            "Promote a durable cross-agent rule to the shared KB (one-way):",
            "```",
            "happyranch learning promote --org <slug> --agent <you> <LRN-NNN> --kb-slug <slug>",
            "```\n",
        ]
    if flat.exists():
        flat_body = flat.read_text()
        return [
            "## Persistent Files\n",
            "- `learnings.md` -- your accumulated operational learnings (legacy flat-file format)",
            "- `task_history.md` -- read-only, updated by orchestrator\n",
            "## Your Learnings\n",
            flat_body + "\n",
            "Append a new line via `happyranch learning --agent <you> --text \"...\"`.",
            "_The structured per-entry format is available once this workspace is migrated._\n",
        ]
    # Brand-new workspace, ensure() should have created learnings/ already.
    return [
        "## Persistent Files\n",
        "- `learnings/_index.md` -- index of your operational learnings (empty)",
        "- `task_history.md` -- read-only, updated by orchestrator\n",
    ]


def _shared_artifacts_section() -> list[str]:
    return [
        "## Shared Artifacts (org-wide)\n",
        "Path: `<runtime>/orgs/<slug>/artifacts/`. Drop persistent files your work",
        "produces — generated reports, exports, screenshots, PDFs, images. Files",
        "here survive across tasks and are visible to every agent in this org.\n",
        "Use cases: a generated PDF report another agent needs to attach to a",
        "customer reply; a CSV export the founder will want to review; a screenshot",
        "captured during QA that the bug-triage agent should see.\n",
        "**Not** the KB. KB is for durable cross-agent *knowledge* (rules,",
        "references, founder rulings). Artifacts are for *files and binary blobs*.",
        "Don't put scratch work here — use your workspace `repos/`, learning",
        "entries, or task output for transient state.\n",
        "All access is via `happyranch`. Direct filesystem reads/writes won't work",
        "uniformly across executors — use the CLI:\n",
        "```",
        "happyranch artifacts put <local-path> --agent <you> [--name <name>]",
        "happyranch artifacts list",
        "happyranch artifacts get <name> --output <local-path>",
        "```\n",
        "Naming convention: prefix with your agent name + ISO date for",
        "traceability, e.g. `dev_agent-YYYY-MM-DD-perf-report.pdf`. Names must",
        "match `[A-Za-z0-9._-]+`, max 200 chars. Per-file size cap: 10 MB.\n",
    ]


def _thread_talk_dispatch_doctrine_section() -> list[str]:
    """System-injected doctrine: dispatch from a thread or talk is self-only.

    Surfaces the structural rule enforced at `/threads/{id}/dispatch` and
    `/talks/{id}/dispatch` so every agent reads it at bootstrap rather than
    discovering it via a 403 response. The rule itself is mechanical (route
    rejects `effective_target != dispatcher` with `*_dispatch_must_be_self`);
    this section is the *why* and the recommended pattern. Spec:
    `docs/superpowers/specs/2026-05-28-thread-talk-self-dispatch-only-design.md`.

    Keep the prose tight — every agent in every org reads this on every
    session. If this grows past ~25 lines it has become docs, not a prompt.
    """
    return [
        "## Thread and Talk Dispatch are Self-Only\n",
        "When you are inside a **thread invocation** (reply / bootstrap) or a",
        "**talk**, the runtime only lets you dispatch tasks to **yourself**. Any",
        "attempt to target another agent returns 403 with",
        "`thread_dispatch_must_be_self` or `talk_dispatch_must_be_self`.\n",
        "This is the doctrine the rule encodes:",
        "- **Threads and talks** exist for founder-visible coordination and",
        "  cross-team handoffs. They are messaging surfaces.",
        "- **Task trees** exist for iterative work. Managers drive sub-tasks",
        "  through the manager-decision loop; workers do bounded work and",
        "  report back. They are execution surfaces.\n",
        "When you need to do task-shaped work from inside a thread or talk:",
        "- **Self-dispatch a root task.** Omit `target_agent` (or set it to",
        "  your own name). If you are a manager and the work has multiple",
        "  steps, the manager-decision loop handles internal sub-task",
        "  spawning on its own. The thread sees a single `task_completed`",
        "  system message at the end and a single TASK_FOLLOWUP turn where",
        "  you report back.",
        "- **Do not** thread-dispatch another agent. Instead, open or extend",
        "  a thread with `happyranch threads compose --to <other-agent>` and",
        "  let them decide whether to take the work on. Cross-team handoffs",
        "  always route through compose, not dispatch.\n",
        "If you find yourself wanting to dispatch a SECOND task from the same",
        "thread, that is the signal that you should have dispatched a single",
        "self-managed root the first time.\n",
    ]


def _non_stop_command_warning_section() -> list[str]:
    """Persistent warning: never run a non-returning command synchronously.

    A `bash` tool call that doesn't return blocks the session until the
    executor's wall-clock timeout fires (default 1800s). The orchestrator
    then auto-revisits up to twice per failure kind, burning multiple
    session budgets on a command that was never going to exit. The session
    completes no useful work in the meantime.

    The remedy is the `jobs` skill: the daemon spawns the subprocess
    out-of-process, the agent's session continues, and the agent polls
    `happyranch jobs tail|wait|stop` for status.
    """
    return [
        "## Long-running and non-stop commands\n",
        "**Never** run a command synchronously via `bash` if it doesn't return on its",
        "own. Examples that will block your session until the wall-clock timeout",
        "kills it (and waste at least one full session budget):\n",
        "- Dev servers: `npm run dev`, `python -m http.server`, `cargo watch`",
        "- Log/file watchers: `tail -f`, `fswatch`, `entr`",
        "- Polling loops: `while true; do …; sleep N; done`",
        "- Long builds you don't need to wait on: full-image Docker builds, large",
        "  cross-compile runs, multi-hour migrations",
        "- Anything that needs founder credentials your `allow_rules` block",
        "  (`aws`, `stripe`, `ssh`, `sudo`, blocked `gh` verbs)\n",
        "Submit a **job** instead — the daemon runs the subprocess, your session",
        "continues, and you check on it with `happyranch jobs tail|wait|stop` when",
        "ready. See the **jobs** skill (`protocol/skills/jobs/SKILL.md`; available",
        "to you under your workspace's skills directory) for the form fields, the",
        "two policy flags (`review_required`, `persistent`), and how to self-block",
        "when founder review is required.\n",
        "If you're uncertain whether a command will return, submit it as a job",
        "with `persistent: true` — cheaper to be wrong than to lose a session.\n",
    ]


# H2 headers that the system emits into every assembled bootstrap doc.
# An agent's ``.md`` body must NOT use any of these as a section header,
# because the assembled prompt would then carry two sections with the
# same heading (the agent body's section above the agent-body cutline, and
# the system-injected section below). Confusing for the agent, and a
# maintenance hazard: each agent file becomes a place where system content
# can quietly drift.
#
# Keep this set synchronized with the ``## <Header>`` lines emitted by
# ``_build_sections`` and the ``_*_section`` helpers above.
_RESERVED_AGENT_BODY_HEADERS: frozenset[str] = frozenset({
    "Available Repositories",
    "Persistent Files",
    "Your Learnings",
    "Knowledge Base (shared across agents)",
    "Shared Artifacts (org-wide)",
    "Thread and Talk Dispatch are Self-Only",
    "Long-running and non-stop commands",
    "Task Completion Format",
    "Task Recall",
    "Workflow",
})


class ReservedHeaderInAgentBody(ValueError):
    """Raised when an agent's ``.md`` body uses a reserved H2 header that
    collides with a system-injected section in the assembled bootstrap doc.
    """


def _assert_no_reserved_headers_in_body(agent_name: str, body: str) -> None:
    """Block the bootstrap-doc write if the agent body collides with a
    system-injected H2 header.

    Boundary contract: the agent ``.md`` file owns who-the-agent-is content
    (role, authority, escalation, accountability). The system owns
    how-to-interact-with-the-orchestrator content (the headers in
    ``_RESERVED_AGENT_BODY_HEADERS`` above). If an agent file authors one of
    the reserved headers, the assembled CLAUDE.md / AGENTS.md will carry
    two sections with the same name and the system's section becomes
    duplicated, contradicted, or drifted.

    Surfaced at write time so the founder sees the violation BEFORE a
    session spawns against a broken assembled doc. Only the offending
    agent's workspace setup fails; the rest of the org keeps running.
    """
    offenders: list[str] = []
    for line in body.splitlines():
        if line.startswith("## "):
            heading = line[3:].strip()
            if heading in _RESERVED_AGENT_BODY_HEADERS:
                offenders.append(heading)
    if offenders:
        offenders_list = ", ".join(repr(h) for h in offenders)
        raise ReservedHeaderInAgentBody(
            f"agent {agent_name!r}: the agent .md body uses H2 header(s) "
            f"{offenders_list}, which the system also emits into the "
            f"assembled bootstrap doc. Rename or remove these headers in "
            f"the agent file. Reserved headers are owned by the system "
            f"(see _RESERVED_AGENT_BODY_HEADERS in workspace_adapters.py)."
        )


def _task_completion_format_section() -> list[str]:
    """System-injected reminder of the completion contract.

    Replaces the per-agent ``## Task Completion Format`` stubs that lived in
    agent ``.md`` files. The canonical JSON payload shape — including the
    manager-only ``decision`` block — lives in the ``start-task`` skill's
    *Report completion* step (``skills/start-task/SKILL.md``) and the
    universal spec (``protocol/00-completion-contract.md``). This section
    keeps every agent pointed at them and lists the prose-``summary`` items
    that apply regardless of role, so individual agent files don't have to
    restate (and slowly drift from) the contract.
    """
    return [
        "## Task Completion Format\n",
        "Every task ends with a `happyranch report-completion --from-file <path>`",
        "callback driven by the **start-task** skill. The skill's *Report",
        "completion* step carries the canonical JSON payload shape — fields,",
        "the manager-only `decision` block, and the blocked-path variant.",
        "Do **not** restate it here; consult the skill.\n",
        "In the prose `summary` field, include:",
        "- What was done — or, for a blocker, what is in the way.",
        "- Findings, risks, or concerns the founder or a downstream reviewer",
        "  should know about.",
        "- Items that need founder decision (call them out explicitly).",
        "- Follow-up work the next task should pick up.\n",
        "Role-specific items your output should mention (artifact paths, PR",
        "numbers, verdicts, tokens added, etc.) come from your role — name",
        "them concretely; do not leave the reader to infer.\n",
    ]


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
    paths: "OrgPaths", agent_name: str | None, *, cli: bool,
) -> list[str]:
    """Build the Bash allow-rule list for ``agent_name``.

    Baseline ``happyranch`` is always included (the agent-callback channel).
    Additional prefixes come from the agent's ``allow_rules`` frontmatter
    field in ``<runtime>/org/agents/<name>.md``.
    """
    from src.orchestrator import prompt_loader
    rules = [_format_allow_rule("happyranch", cli=cli)]
    if agent_name is None:
        return rules
    for prefix in prompt_loader.allow_rules_for_agent(paths, agent_name):
        rules.append(_format_allow_rule(prefix, cli=cli))
    return rules


def bash_allow_prefixes_for_agent(
    paths: "OrgPaths", agent_name: str | None,
) -> list[str]:
    """Return raw Bash allow-rule prefixes (no syntax wrapping).

    Used by ``OpencodeWorkspaceAdapter`` to build ``opencode.json``, where
    each prefix is rendered as ``"<prefix> *": "allow"`` rather than
    ``Bash(<prefix>:*)`` (settings.json) or ``Bash(<prefix> *)``
    (Claude ``--allowedTools``). Source of truth (the per-agent
    ``allow_rules`` frontmatter) is the same; only the rendering differs.
    """
    from src.orchestrator import prompt_loader
    prefixes = ["happyranch"]
    if agent_name is None:
        return prefixes
    for prefix in prompt_loader.allow_rules_for_agent(paths, agent_name):
        prefixes.append(prefix)
    return prefixes


def build_settings_json(
    paths: "OrgPaths",
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
            "allow": allow_rules_for_agent(paths, agent_name, cli=False),
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

        # task_history.md: always ensure
        history_path = workspace / "task_history.md"
        if not history_path.exists():
            history_path.write_text(f"# Task History: {agent_name}\n\n")

        # learnings: state-aware migration safety
        flat_path = workspace / "learnings.md"
        learnings_dir = workspace / "learnings"
        if learnings_dir.exists():
            # Post-migration: idempotently ensure _index.md exists.
            # Lazy import to avoid a hard infra dep at module top.
            from src.infrastructure.learnings_store import LearningsStore
            store = LearningsStore(learnings_dir)
            if not (learnings_dir / "_index.md").exists():
                store.regenerate_index()
        elif flat_path.exists():
            # Pre-migration legacy workspace: leave both untouched.
            pass
        else:
            # Brand-new workspace: create learnings/ on the new layout.
            from src.infrastructure.learnings_store import LearningsStore
            learnings_dir.mkdir(parents=True, exist_ok=True)
            store = LearningsStore(learnings_dir)
            store.regenerate_index()

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

    def __init__(self, settings: Settings, paths: "OrgPaths", *, slug: str) -> None:
        self._settings = settings
        self._paths = paths
        self._slug = slug
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
            self._paths, repo_names or [], agent_name=agent_name,
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
        _assert_no_reserved_headers_in_body(agent_name, system_prompt)
        workspace.mkdir(parents=True, exist_ok=True)
        sections = self._build_sections(
            agent_name,
            system_prompt,
            workspace=workspace,
            include_start_task=True,
            repo_refresh_note=(
                "repositories cloned under `repos/`. Each is kept fresh via the "
                "PreToolUse hook in `.claude/settings.json`."
            ),
            callback_note=(
                "The `--from-file` form is mandatory here — multi-line `happyranch` "
                "invocations are blocked by the `Bash(happyranch:*)` permission rule."
            ),
            workflow_section=[
                "Every task arrives via the orchestrator's prompt. Use the **start-task** skill",
                "(in `.claude/skills/start-task/`) to parse parameters and report completion via",
                "`happyranch report-completion`. Mid-task learnings go through `happyranch learning`.\n",
            ],
        )
        (workspace / "CLAUDE.md").write_text("\n".join(sections))

    def _build_sections(
        self,
        agent_name: str,
        system_prompt: str,
        *,
        workspace: Path,
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
            *_learnings_bootstrap_section(workspace),
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
            "happyranch kb list [--topic <t>] [--type <label>]",
            "happyranch kb search \"<keywords>\"",
            "happyranch kb get <slug>",
            "```\n",
            "Write (durable, cross-agent knowledge only — regulations, partner-API quirks,",
            "payment flows, founder rulings; **not** task-specific notes):",
            "```",
            "happyranch kb add --agent <you> --from-file /tmp/kb-<slug>.md",
            "happyranch kb update <slug> --agent <you> --from-file /tmp/kb-<slug>.md",
            "```",
            "Payload file needs YAML frontmatter (`slug`, `title`, `type`, `topic`,",
            "optional `tags`, `source_task`) followed by a markdown body. `type` is a",
            "freeform label (e.g. `reference`, `ruling`, `sop`) used for grouping.",
            callback_note + "\n",
            *_shared_artifacts_section(),
            *_thread_talk_dispatch_doctrine_section(),
            *_non_stop_command_warning_section(),
            *_task_completion_format_section(),
            "## Task Recall\n",
            "Past task context (brief, completion summary, output files) is retrievable via:",
            "```",
            "happyranch recall <task_id>                  # brief + final summary",
            "happyranch recall <task_id> --tree           # include the full subtree of child tasks",
            "happyranch recall <task_id> --fetch-output   # inline output file bodies (capped at ~200KB)",
            "```",
            "Use when the current brief references a prior task, when you need to revisit",
            "your own earlier output before reworking, or when a KB entry points to",
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
            _resolve_skills_src(self._settings),
            workspace / ".claude" / "skills",
            slug=self._slug,
        )


class CodexWorkspaceAdapter:
    """Bootstrap and maintain Codex workspaces."""

    provider_name = "codex"

    def __init__(self, settings: Settings, paths: "OrgPaths", *, slug: str) -> None:
        self._settings = settings
        self._paths = paths
        self._slug = slug
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
        _assert_no_reserved_headers_in_body(agent_name, system_prompt)
        workspace.mkdir(parents=True, exist_ok=True)
        # Shared bootstrap sections (KB, learnings, artifacts) are assembled in
        # Claude's _build_sections and flow through here unchanged.
        sections = ClaudeWorkspaceAdapter(self._settings, self._paths, slug=self._slug)._build_sections(
            agent_name,
            system_prompt,
            workspace=workspace,
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
                "`happyranch report-completion`. Mid-task learnings go through `happyranch learning`.\n",
            ],
        )
        (workspace / "AGENTS.md").write_text("\n".join(sections))

    def _copy_skills(self, workspace: Path) -> None:
        """Copy protocol/skills/ tree into workspace/.agents/skills/."""
        _copy_skills_tree(
            _resolve_skills_src(self._settings),
            workspace / ".agents" / "skills",
            slug=self._slug,
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


class OpencodeWorkspaceAdapter:
    """Bootstrap and maintain opencode workspaces.

    opencode reads ``AGENTS.md`` (with ``CLAUDE.md`` as a fallback) and
    discovers skills under ``.opencode/skills/``, ``.claude/skills/``, or
    ``.agents/skills/``. We use the same ``AGENTS.md`` + ``.agents/skills/``
    layout as Codex so a single workspace shape works for both executors.

    The opencode-specific surface is ``opencode.json``: a structured
    permission file that gates bash by command-prefix glob. We write a
    strict default (``"*": "deny"``) plus per-agent allow rules sourced
    from the same ``allow_rules`` frontmatter Claude reads. No
    ``--dangerously-skip-permissions`` — the file is the enforcement
    surface, and bypassing it would erase the per-prefix discipline that
    CLAUDE.md mandates.
    """

    provider_name = "opencode"

    def __init__(self, settings: Settings, paths: "OrgPaths", *, slug: str) -> None:
        self._settings = settings
        self._paths = paths
        self._slug = slug
        self._persistent = PersistentWorkspaceSetup(settings)
        # AGENTS.md generation is identical to Codex — delegate.
        self._codex_adapter = CodexWorkspaceAdapter(settings, paths, slug=slug)

    def write_agents_md(
        self,
        workspace: Path,
        agent_name: str,
        system_prompt: str,
        repo_names: list[str] | None = None,
    ) -> None:
        """Write AGENTS.md to workspace. Same shape as Codex's AGENTS.md."""
        self._codex_adapter.write_agents_md(
            workspace, agent_name, system_prompt, repo_names=repo_names,
        )

    def write_opencode_json(
        self, workspace: Path, agent_name: str | None = None,
    ) -> None:
        """Write ``opencode.json`` with the agent's bash allow list.

        Default for unmatched bash is ``"deny"`` so an agent attempting an
        unsanctioned command fails fast rather than waiting on an
        interactive prompt that will never arrive in headless mode. The
        sanctioned channel (``happyranch``) is always allowed; per-agent extras
        come from the same ``allow_rules`` frontmatter Claude reads.
        """
        prefixes = bash_allow_prefixes_for_agent(self._paths, agent_name)
        permission_bash: dict[str, str] = {"*": "deny"}
        for prefix in prefixes:
            permission_bash[f"{prefix} *"] = "allow"
        config = {
            "$schema": "https://opencode.ai/config.json",
            "permission": {"bash": permission_bash},
        }
        (workspace / "opencode.json").write_text(
            json.dumps(config, indent=2) + "\n"
        )

    def _copy_skills(self, workspace: Path) -> None:
        """Copy protocol/skills/ tree into workspace/.agents/skills/.

        opencode discovers skills under ``.opencode/skills/``,
        ``.claude/skills/``, or ``.agents/skills/``. We pick ``.agents/``
        to share the layout with Codex workspaces — a workspace can be
        re-bootstrapped between executors without churn.
        """
        _copy_skills_tree(
            _resolve_skills_src(self._settings),
            workspace / ".agents" / "skills",
            slug=self._slug,
        )

    def ensure_workspace_ready(
        self,
        workspace: Path,
        agent_name: str,
        system_prompt: str,
    ) -> None:
        """Make sure an opencode workspace has every file the orchestrator requires."""
        self._persistent.ensure(workspace, agent_name)
        self.write_agents_md(workspace, agent_name, system_prompt)
        self._copy_skills(workspace)
        self.write_opencode_json(workspace, agent_name=agent_name)

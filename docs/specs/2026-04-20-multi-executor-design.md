# Multi-Executor Agent Runtime Design

Date: 2026-04-20
Status: Proposed

## Summary

Extend the agent runtime so each agent can run on either Claude Code or Codex.
The orchestrator must select the executor per agent, default to Claude for
existing agents, and preserve the current task lifecycle, completion callback
contract, and audit flow.

The design keeps one shared operational procedure for task execution and
completion, but adapts that procedure into each provider's native workspace and
launch contract. Claude continues using its skill-based bootstrap. Codex gets a
Codex-native bootstrap surface that instructs the agent to follow the same
procedure without depending on Claude-specific file paths or permission rules.

## Goals

- Support mixed fleets where some agents run on Claude and others on Codex.
- Keep existing agents and runtimes working without migration.
- Preserve the current `opc report-completion --from-file` callback contract.
- Isolate provider-specific behavior behind clear interfaces.
- Keep orchestration, task state, session tracking, and scoring provider-agnostic.

## Non-Goals

- Redesigning the task lifecycle or callback payload schema.
- Rewriting the agent SOP itself beyond refactoring it into a shared source.
- Migrating existing `agent.yaml` files to add `executor: claude`.
- Adding providers beyond Claude and Codex in this change.
- Reworking the knowledge base, queue, or performance tracker.

## Current Problem

The implementation claims a future executor abstraction in docs, but the actual
runtime is Claude-specific:

- `src/orchestrator/executor.py` always launches `claude -p ...`.
- `src/orchestrator/context_builder.py` always writes `CLAUDE.md`,
  `.claude/settings.json`, and `.claude/skills/`.
- `src/orchestrator/orchestrator.py` hard-requires the Claude
  `.claude/skills/start-task/SKILL.md` marker before a task can run.
- `src/config.py` exposes only Claude-oriented executor settings.
- Agent enrollment and `agent.yaml` do not store executor selection.

This prevents mixed-provider fleets and makes Codex support more than a command
substitution.

## Requirements

### Functional requirements

1. Each agent can declare `executor: claude` or `executor: codex`.
2. Missing `executor` resolves to `claude`.
3. The orchestrator selects the executor per agent at task execution time.
4. Workspace bootstrap is provider-specific.
5. The task execution SOP is shared across providers.
6. Claude agents keep working with the current skill-based startup contract.
7. Codex agents run through `codex exec` with a Codex-native bootstrap.

### Operational requirements

1. Existing runtimes continue to work without data migration.
2. Task/session IDs, DB task status, audit logging, and `opc` callback endpoints
   remain stable.
3. Integration tests can simulate both providers locally.

## Design Overview

The runtime is split into three layers:

1. A shared task procedure source.
2. Provider-specific workspace adapters.
3. Provider-specific executors.

The shared task procedure defines what every agent must do at task start and
finish:

- parse the injected task parameters
- consult memory and KB
- perform the assigned work
- optionally record learnings
- submit completion via `opc report-completion --from-file`
- clean up any task-local git worktree

Claude and Codex both follow that procedure, but they consume it differently.
Claude consumes it as a Claude skill. Codex consumes the same procedure through
its own bootstrap instructions.

## Shared Procedure Model

### Canonical SOP

`protocol/skills/start-task/` remains the canonical source of the operational
procedure. It is no longer treated as "the Claude implementation"; it becomes
the shared procedure definition from which provider-specific bootstrap artifacts
are derived.

The same principle applies to the other operational procedures that are coupled
to task execution:

- `make-worktree`
- `manage-repo`
- `manage-agent`

### Shared callback contract

Both providers must continue using the same callback commands and payload
shapes:

- `opc report-completion --from-file <path>`
- `opc learning ...`
- `opc manage-repo --from-file <path>`
- `opc manage-agent --from-file <path>`

This contract stays stable so the daemon, DB writes, and audit log code remain
unchanged.

### Why keep `--from-file` for Codex too

Codex does not require the same Claude `Bash(opc:*)` matcher behavior, but the
single-line `--from-file` pattern is still worth preserving:

- it keeps the callback API consistent across providers
- it avoids large inline shell payloads
- it reduces quoting fragility
- it preserves a stable agent SOP and test surface

The rationale text in docs must change from "Claude-specific permission rule" to
"stable provider-neutral callback contract; additionally required by Claude."

## Provider-Specific Workspace Adapters

### Interface

Introduce a workspace adapter abstraction responsible for preparing the local
workspace shape expected by each provider.

Proposed responsibilities:

- write provider-specific bootstrap files
- render the shared SOP into the provider's native format
- copy any provider-usable support artifacts
- expose a provider-specific readiness marker path

Example interface:

```python
class WorkspaceAdapter(Protocol):
    def prepare_workspace(self, workspace: Path, agent_name: str, system_prompt: str) -> None: ...
    def readiness_marker(self, workspace: Path) -> Path: ...
    def provider_name(self) -> str: ...
```

Persistent workspace files remain shared and provider-agnostic:

- `agent.yaml`
- `learnings.md`
- `scorecard.md`
- `task_history.md`
- `repos/`
- `artifacts/`

### Claude adapter

The Claude adapter preserves current behavior:

- write `CLAUDE.md`
- write `.claude/settings.json`
- copy rendered skills into `.claude/skills/`
- expose `.claude/skills/start-task/SKILL.md` as the readiness marker

Behavioral changes are limited to sourcing the shared SOP and adjusting wording
to clarify which instructions are Claude-specific versus provider-neutral.

### Codex adapter

The Codex adapter creates a Codex-native workspace bootstrap:

- write `AGENTS.md` as the primary bootstrap file
- render the shared SOP into `AGENTS.md` in a Codex-appropriate format
- optionally copy support docs into a provider-neutral local directory if useful
  for reference, but do not require `.claude/skills/...`
- expose `AGENTS.md` as the readiness marker

The Codex adapter must not depend on Claude path conventions or Claude-specific
permission docs.

## Provider-Specific Executors

### Interface

Replace the single hard-coded executor with a provider abstraction.

Proposed responsibilities:

- build the provider-specific subprocess command
- run the subprocess in the agent workspace
- surface duration, session ID, and execution errors through `ExecutorResult`

Example interface:

```python
class AgentExecutor(Protocol):
    def run(
        self,
        workspace: Path,
        prompt: str,
        session_id: str | None = None,
        timeout_seconds: int = 1800,
    ) -> ExecutorResult: ...
```

### Claude executor

The Claude executor remains close to the current implementation:

- use the configured Claude CLI path
- pass the prompt using the current launch style
- preserve Claude-specific permission handling, including the current allow rule
  for `opc` callbacks

### Codex executor

The Codex executor launches `codex exec` non-interactively.

Expected launch pattern:

- `codex exec`
- prompt provided over stdin or as the positional prompt
- sandbox configured explicitly
- `--skip-git-repo-check` when the workspace is not itself a git repo root
- optional `--json` and `--output-last-message` for observability
- `--ephemeral` if the runtime should not rely on persisted Codex sessions

The exact command construction must be isolated inside the Codex executor so
future Codex CLI changes do not leak into the orchestrator.

## Agent Configuration

### `agent.yaml`

Extend `agent.yaml` to support:

```yaml
executor: claude
repos: {}
```

Rules:

- `executor` is optional
- if omitted, resolve to `claude`
- unknown values are validation errors

### Enrollment flow

Enrollment and agent management should accept an optional executor field:

- enroll: may specify executor
- update: may change executor
- approve: bootstraps the workspace using the enrolled executor

If omitted in enrollment data, executor resolves to `claude`.

### Source of truth

At runtime, executor resolution order should be:

1. workspace `agent.yaml`
2. approved enrollment record, if applicable during bootstrap
3. default `claude`

This keeps existing agents working and lets newly enrolled agents declare Codex
before approval.

## Orchestrator Changes

### Executor resolution

Before launching a task, the orchestrator resolves the agent's executor and then
selects:

- the workspace adapter
- the process executor
- the readiness marker to validate

### Prompt contract

The injected task prompt stays structurally the same:

- `task_id`
- `session_id`
- `brief`
- `role_guidance`

This preserves the shared task SOP and minimizes behavioral drift.

The prompt wording should no longer hard-code "Use the start-task skill" for all
providers. Instead:

- Claude prompt may continue to reference the skill explicitly
- Codex prompt should reference the task bootstrap instructions in `AGENTS.md`

### Readiness checks

The current hard-coded Claude skill check becomes provider-specific:

- Claude: `.claude/skills/start-task/SKILL.md`
- Codex: `AGENTS.md`

The error remains actionable and tells the operator to run `opc init-agent`.

## Settings

Settings should distinguish provider-specific launch configuration from the
per-agent executor selection.

Expected additions:

- `OPC_CLAUDE_CLI_PATH`
- `OPC_CODEX_CLI_PATH`
- optional Codex-specific launch settings if needed later

The global default provider may be stored in settings, but it should not replace
per-agent executor selection.

## Testing Strategy

### Unit tests

Add tests for:

- executor resolution from `agent.yaml`
- default-to-Claude behavior when executor is missing
- validation failure on unknown executor
- provider-specific workspace adapter outputs
- provider-specific readiness markers
- provider-specific command construction

### Integration tests

Keep the current fake Claude integration coverage and add a fake Codex runner.

The fake Codex test should prove:

- a Codex-configured agent can be bootstrapped
- the orchestrator launches the Codex executor path
- the agent can still call `opc report-completion`
- mixed fleets work when one step uses Claude and another uses Codex

### Backward-compatibility tests

Explicitly test that an existing agent workspace without `executor` in
`agent.yaml` still runs under Claude.

## Rollout Plan

1. Add executor values and resolution logic with default-to-Claude behavior.
2. Introduce provider-specific workspace adapters.
3. Introduce provider-specific executors.
4. Update orchestrator readiness checks and prompt selection.
5. Add fake Codex tests and mixed-fleet tests.
6. Update docs for runtime, README, and protocol design docs.
7. Only then assign selected agents to Codex in real runtimes.

## Risks and Mitigations

### Risk: fake abstraction

If Codex support still depends on `.claude/...` or Claude permission semantics,
the abstraction is cosmetic.

Mitigation:

- define provider-specific workspace adapters
- make readiness checks provider-specific
- keep Claude-only rules out of shared text

### Risk: SOP drift between providers

If Claude and Codex each get separate task procedures, behavior will diverge.

Mitigation:

- keep one canonical shared procedure source
- generate provider-specific bootstrap outputs from that shared source

### Risk: Codex CLI contract changes

Codex CLI is newer and may evolve faster than the current Claude contract.

Mitigation:

- isolate Codex subprocess construction in one executor class
- cover it with provider-specific tests

### Risk: documentation confusion

The repository currently uses `CLAUDE.md` as both a repo developer guide and an
agent workspace file name.

Mitigation:

- be explicit in docs when referring to repo-root `CLAUDE.md` versus per-agent
  workspace bootstrap files
- document Codex agent workspaces as using `AGENTS.md`

## Open Decisions

1. Whether Codex runs should use `--ephemeral` by default or persist sessions.
2. Whether Codex execution should emit JSON event logs into the audit pipeline in
   this phase or later.
3. Whether provider-specific support docs should live in a new provider-neutral
   workspace directory or only in the provider's native bootstrap file.

These are implementation details that do not block the core design.

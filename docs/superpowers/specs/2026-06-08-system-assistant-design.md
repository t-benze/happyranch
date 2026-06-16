# System Assistant — Design Spec

**Date:** 2026-06-08
**Status:** Draft, pending implementation.
**Origin:** Founder request for a runtime-level `system assistant` that knows the HappyRanch protocol and helps users operate the system. The initialization flow should also prove the user's environment is ready by finding at least one runnable interactive agentic CLI.
**Relates to:**
- GitHub issue #54: PTY bridge for live interactive agent sessions through the web UI.
- `docs/agent-guides/runtime-and-configuration.md` — runtime container and settings behavior.
- `docs/agent-guides/agent-executors-and-permissions.md` — executor and permission surfaces.
- `docs/agent-guides/web-and-cli.md` — CLI and web route contract.
- `runtime/orchestrator/workspace_adapters.py` — existing workspace bootstrap patterns to reuse where appropriate.

## 1. Goal

Add a runtime-global **system assistant** that helps the founder operate HappyRanch itself: setup, protocol explanation, runtime health, executor diagnosis, org discovery, and guided next actions. The assistant is accessed through a daemon-managed PTY session and uses a selected interactive-capable agentic CLI.

The setup flow must double as an environment readiness check. A runtime is ready for the system assistant only when at least one supported executor can pass a fixed request/reply probe in a long-lived interactive PTY session.

## 2. Non-goals

- Making the system assistant an org agent, team member, manager, or task target.
- Adding PTY support for threads or normal task execution.
- Replacing the existing headless executor path (`claude -p`, `codex exec`, etc.).
- Letting the system assistant mutate runtime/org state silently.
- General-purpose remote shell access through the daemon.
- Requiring web UI support in the first implementation. The backend shape should support web/xterm.js later, but the first client can be `happyranch assistant`.

## 3. Placement And Identity

The system assistant is **runtime-global**, not org-scoped. It lives under the active runtime container:

```text
<runtime>/
  system/
    assistant/
      config.json
      workspace/
        agent.yaml
        AGENTS.md or CLAUDE.md
        .agents/skills/ or .claude/skills/
        learnings/
        logs/
```

It must not appear under:

```text
<runtime>/orgs/<slug>/org/agents/
<runtime>/orgs/<slug>/org/teams.yaml
```

The daemon treats it as a runtime capability. Org routes and task routing must not discover it as a normal agent.

## 4. PTY-Capable Definition

For this feature, **PTY-capable** means the agentic CLI can run as a long-lived interactive program inside a daemon-owned pseudo-terminal. HappyRanch can stream user keystrokes into the session and stream output back incrementally.

This is stronger than running commands with `isatty() == true` or producing colored output. The requirement is interactive session control:

```text
daemon PTY manager
  <-> stdin/stdout terminal stream
agentic CLI interactive session
  <-> user conversation
```

A CLI that only accepts one prompt, exits, and returns structured output is headless-runnable, but not PTY-capable for the system assistant.

## 5. Fixed PTY Probe

Executor readiness is proven by an executor-specific PTY probe.

The probe creates a temporary minimal workspace, writes only the instruction file needed for that executor, launches the CLI in interactive mode from that cwd, sends a fixed request, and waits for an exact marker reply.

Probe request:

```text
HAPPYRANCH_PTY_PROBE_V1
```

Expected reply:

```text
HAPPYRANCH_PTY_READY_V1
```

The temporary workspace instruction file should say, in the executor's normal bootstrap surface:

```md
# HappyRanch PTY Probe

If the user sends `HAPPYRANCH_PTY_PROBE_V1`, reply with exactly:

HAPPYRANCH_PTY_READY_V1
```

The probe passes only when the exact marker appears before timeout. It fails on launch errors, immediate process exit, authentication/login prompts that never answer, timeout, or wrong output.

The temporary workspace is discarded after probing. The real system assistant workspace is created only after the user selects a passing executor.

### 5.1 Probe Result Shape

Persist the latest probe metadata in `<runtime>/system/assistant/config.json` under a `latest_probe_results` field:

```json
{
  "executor": "claude",
  "status": "passed",
  "command": "claude",
  "checked_at": "2026-06-08T00:00:00Z",
  "latency_ms": 2310
}
```

Failure entries should include concise diagnostics:

```json
{
  "executor": "codex",
  "status": "failed",
  "reason": "timeout_waiting_for_probe_reply",
  "stderr_tail": "...",
  "hint": "Run `codex` once locally and complete login, then retry `happyranch assistant init`."
}
```

## 6. Setup And Existing Runtime States

A runtime can be in one of three system-assistant states:

| State | Definition |
|---|---|
| `uninitialized` | Runtime exists, but no system assistant config is present. |
| `configured` | Config, workspace, selected executor, and bootstrap files exist. |
| `stale_or_broken` | Config exists, but workspace is missing, selected CLI no longer passes probe, or bootstrap files are outdated. |

Existing active runtimes must be supported. Daemon startup should not automatically mutate runtime directories to create the assistant.

### 6.1 New Runtime Creation

`happyranch init <runtime>` keeps creating/registering the runtime container. When the system assistant is missing, it may offer or run the system-assistant setup flow as part of guided initialization:

1. Probe all supported interactive executors.
2. Show pass/fail rows with reason and hint.
3. Ask the user to select from passing executors.
4. Create `<runtime>/system/assistant/`.
5. Write selected executor and probe metadata.
6. Bootstrap the assistant workspace.

If no executor passes, setup fails with remediation hints. This is intentional: the environment is not ready for the system assistant.

### 6.2 Existing Runtime Upgrade

Add an idempotent command:

```bash
happyranch assistant init
```

It operates on the active runtime.

Behavior:

- If no runtime is active, error with a clear instruction to run `happyranch init <path>` first.
- If the assistant is uninitialized, run the probe/select/bootstrap flow.
- If the assistant is configured and healthy, report status and do nothing by default.
- If the assistant is stale or broken, report the issue and support repair/reconfiguration.

Recommended flags:

```bash
happyranch assistant init --repair
happyranch assistant init --reconfigure
```

`--repair` refreshes bootstrap files and recreates missing workspace structure without changing the selected executor when that executor still passes.

`--reconfigure` reruns all probes and asks the user to select a passing executor.

## 7. Interaction Command

Add:

```bash
happyranch assistant
```

Behavior:

- If the assistant is configured and healthy, attach to a daemon-managed PTY session.
- If uninitialized, print a clear prompt to run `happyranch assistant init`.
- If stale or broken, print diagnosis and suggest `happyranch assistant init --repair` or `--reconfigure`.

The first implementation may use the CLI as the client. The PTY manager and attach semantics should be compatible with a future WebSocket/xterm.js UI using the same backend session model.

## 8. PTY Session Lifecycle

The daemon owns the PTY process. The client only attaches to the stream.

V1 lifecycle:

1. User runs `happyranch assistant`.
2. CLI asks the daemon to start or attach to the system assistant session.
3. Daemon spawns the selected executor in the assistant workspace when no live session exists.
4. CLI streams local keystrokes to the daemon and terminal output back to stdout.
5. Session logs are written under `<runtime>/system/assistant/workspace/logs/`.
6. Only one active attachment is allowed by default.
7. A reconnect may replace the previous attachment.

Idle cleanup can follow the issue #54 default of a 30-minute idle window when implemented, with a runtime setting added only if needed.

## 9. Assistant Authority

V1 authority is diagnostic and guided.

Allowed:

- Explain HappyRanch protocol, features, setup, and runtime concepts.
- Inspect runtime/org state through HappyRanch APIs and CLI commands.
- Diagnose executor, daemon, auth, and workspace readiness.
- Recommend next actions.
- Run mutating HappyRanch commands only after explicit user confirmation.

Disallowed:

- Silent filesystem edits to org definitions, agent files, or runtime config.
- Direct mutation that bypasses `happyranch` commands or daemon APIs.
- Acting as a normal org agent or task worker.
- Arbitrary shell access unrelated to the selected agentic CLI session.

The assistant bootstrap prompt should make this authority boundary explicit.

## 10. Executor Integration

Interactive command construction is executor-specific. It should not be forced through the existing headless `Executor.run()` API, which is built around one-shot subprocesses and structured output.

Add a separate interactive executor/probe layer that knows:

- command path from `Settings` (`claude_cli_path`, `codex_cli_path`, `opencode_cli_path`, `pi_cli_path`);
- workspace bootstrap file expected by the CLI (`CLAUDE.md` or `AGENTS.md`);
- interactive launch argv;
- probe timeout and output parsing;
- remediation hint for common failures.

The normal task executor path remains unchanged.

## 11. Security Boundaries

The PTY bridge exposes an agentic terminal surface. Treat it as sensitive.

Rules:

- Authenticate daemon routes with the existing bearer-token flow.
- Never accept cwd from the client. The daemon derives the assistant cwd from the active runtime.
- Do not accept arbitrary commands from the client. The daemon launches only the selected executor command.
- Use a narrow environment: `HOME`, `PATH`, `TERM`, and executor-required variables/config paths.
- Keep single-attach semantics in v1.
- Route system assistant side effects through `happyranch` commands or daemon APIs.

Any future web/xterm.js route should follow the same auth and cwd rules as issue #54.

## 12. CLI Shape

Add a new command family:

```bash
happyranch assistant init [--repair | --reconfigure]
happyranch assistant status
happyranch assistant
```

`status` should report:

- assistant state (`uninitialized`, `configured`, `stale_or_broken`);
- selected executor;
- latest probe result;
- workspace path;
- live PTY session status when available.

If argparse conflicts make a bare `happyranch assistant` difficult, use:

```bash
happyranch assistant attach
```

while preserving the same behavior.

## 13. Testing

- Probe:
  - fake CLI passes fixed request/reply marker through PTY.
  - fake CLI exits immediately -> failed probe.
  - fake CLI hangs -> timeout failure with hint.
  - fake CLI prints wrong marker -> failed probe.
- Setup:
  - new runtime creates assistant config/workspace after executor selection.
  - no passing executor fails setup with remediation hints.
  - existing runtime in `uninitialized` state upgrades via `happyranch assistant init`.
  - configured runtime is idempotent.
  - `--repair` recreates missing workspace files without changing executor.
  - `--reconfigure` reruns probes and updates selected executor.
- Attach:
  - configured assistant starts daemon-owned PTY in assistant workspace.
  - attach streams input/output.
  - uninitialized attach prints setup instruction.
  - stale/broken attach prints repair/reconfigure instruction.
- Boundaries:
  - system assistant does not appear in org agents list.
  - system assistant cannot be targeted by task routing.
  - daemon never accepts client-provided cwd or arbitrary command for assistant PTY.

Integration tests should use fake PTY-capable CLIs, similar to existing fake executor scripts under `tests/integration/`.

## 14. Open Implementation Details

- Exact interactive argv per executor must be verified during implementation.
- Whether `happyranch init <runtime>` should always run the assistant setup flow or ask first can be decided in implementation. `happyranch assistant init` is the required idempotent path either way.
- Web UI/xterm.js attach surface shipped (THR-024 reading #1) — the web attach (SystemAssistantPage / AssistantTerminal, xterm.js) and Settings config now exist; the daemon API should avoid CLI-only assumptions.

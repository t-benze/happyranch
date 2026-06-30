# Agent Executors And Permissions

Each workspace declares an `executor` in `agent.yaml`. The executor is resolved
against the **executor registry** — capability-registered, not name-listed
(THR-052). Four **built-in** profiles ship with the runtime; custom CLI profiles
can be registered in org config.

**Built-in profiles:**

| Executor | Bootstrap doc | Skills dir | Permission surface |
| --- | --- | --- | --- |
| `claude` | `CLAUDE.md` | `.claude/skills/` | `permissions.allow` in `.claude/settings.json` and `--allowedTools` |
| `codex` | `AGENTS.md` | `.agents/skills/` | sandbox flags on CLI |
| `opencode` | `AGENTS.md` | `.agents/skills/` | `opencode.json` `permission.bash` map |
| `pi` | `AGENTS.md` | `.agents/skills/` | no HappyRanch-managed sandbox |

Missing values default to `claude`. All executors share `protocol/skills/`.

**Custom CLI profiles** (example — OpenClaw):

Any agentic CLI that accepts a prompt via a positional flag and returns
structured output can register as a custom profile in the org's
`config.yaml`. A custom profile declares an `argv_template` with supported
placeholders (`{prompt}`, `{timeout_seconds}`, `{workspace}`) and an
`adapter` for workspace preparation (one of `claude`, `codex`, `opencode`,
`pi` — typically `pi` for AGENTS.md-based CLIs).

```yaml
# org/config.yaml
executor_profiles:
  openclaw:
    command: openclaw
    argv_template:
      - openclaw
      - agent
      - --local
      - --json
      - --message
      - "{prompt}"
      - --timeout
      - "{timeout_seconds}"
    adapter: pi
```

Custom profiles use the `GenericCliExecutor` which validates the argv template
at registration time and substitutes placeholders at launch. No shell string
is constructed — each template element becomes exactly one argv element, with
placeholders replaced by their resolved values.

## Executor Notes

All executors converge on `executors._run_command`, which runs every launch under the **per-provider throttle** (`runtime/orchestrator/throttle.py`, issue #85): a `threading.BoundedSemaphore` ceiling per provider string, an inter-launch spacing gate, and slot-releasing 429 backoff. Each executor passes its own `provider` string (the profile name — `"claude"`, `"codex"`, `"opencode"`, `"pi"`, or a custom profile name) and an optional `on_throttle_event` audit callback. The throttle never touches the permission surface — it is purely a launch-timing wrapper. See [runtime-and-configuration.md → Executor Throttle](./runtime-and-configuration.md#executor-throttle) and `docs/adr/0001-per-provider-executor-throttle.md`.

Codex: `CodexExecutor.run` passes `-c sandbox_workspace_write.network_access=true` on every invocation. The workspace-write sandbox blocks localhost by default, which would prevent `happyranch report-completion` callbacks to `127.0.0.1`. The system assistant does not go through `CodexExecutor` — it launches its executor as an interactive PTY via `AssistantPtySession` (`runtime/daemon/assistant_pty.py`) — so `_build_session_launch_argv` re-injects the same `-c sandbox_workspace_write.network_access=true` override (as a global codex option, immediately after the executable) when, and only when, the assistant executor is `codex`. Same rationale; without it the assistant's `happyranch` CLI calls die with the same localhost `ConnectError`.

opencode: `OpencodeWorkspaceAdapter.write_opencode_json` writes a strict default denying `*` and allowing `happyranch *` plus per-agent allow rules. Do not pass `--dangerously-skip-permissions`; it bypasses `opencode.json`.

Pi: `PiExecutor.run` invokes `pi -p ... --mode json` from the agent workspace. Use external containment when command/tool restriction matters.

Enrolling a worker with a non-default executor: set `"executor": "<profile-name>"` in the `happyranch manage-agent --from-file` payload where the profile name is a registered executor profile (built-in: `codex`, `opencode`, `pi`, or a custom profile registered in org `config.yaml`). Founder approval bootstraps the right workspace surface. See `protocol/skills/manage-agent/SKILL.md`.

Repos are configured per agent in `agent.yaml`:

```yaml
repos:
  web-app: https://github.com/t-benze/web-app.git
  docs: https://github.com/t-benze/docs.git
```

`happyranch init-agent` creates a default `agent.yaml` with empty repos if missing.

## Switching an Existing Agent's Executor

An agent's executor lives in two places that must stay in sync: the org agent `.md` frontmatter (`executor:`) and the workspace `agent.yaml`. The orchestrator resolves the executor at dispatch time from `agent.yaml` (`_resolve_executor_name`), so hand-editing only the frontmatter has no runtime effect.

Switch an existing agent end-to-end with the founder command:

```bash
happyranch set-executor --org <org> <agent> --executor <profile-name>
```

It reconciles all three surfaces in one call — the `.md` frontmatter (atomic rewrite), `agent.yaml` (`set_executor`), and the executor bootstrap (`ensure_workspace_ready` with the new provider) — then prints before/after state for both the frontmatter and `agent.yaml`. An unregistered executor is rejected with the list of registered profiles.

Switching **away from Claude** leaves the Claude-only files (`CLAUDE.md`, `.claude/`) behind, because the new adapter writes `AGENTS.md`/`.agents/` and never deletes them. By default the command **warns** that these files are stale and names them; it never auto-deletes. Pass `--clean` to delete them:

```bash
happyranch set-executor --org <org> <agent> --executor pi --clean
```

(The symmetric case — switching *to* Claude leaves `AGENTS.md`/`.agents/`/`opencode.json` stale — is not yet handled.)

`happyranch init-agent` does **not** auto-reconcile this drift. For an existing workspace whose frontmatter and `agent.yaml` disagree, init emits an `executor_drift` warning event (with the `set-executor` command to run) and changes nothing — a broad init must not silently mass-switch executors.

## Permission Model

Agents call the orchestrator CLI as their sanctioned side-effect channel: `happyranch report-completion`, `happyranch memory`, `happyranch manage-repo`, `happyranch manage-agent`, `happyranch dispatch`, and related callbacks. Baseline allow rule for every agent: `happyranch`.

Per-agent extras are declared in agent frontmatter under `allow_rules:`. Keep extras narrow; each prefix can mutate shared external state on future tasks.

For Claude, allow rules must be generated in two places:

1. `.claude/settings.json` `permissions.allow`, written by `ClaudeWorkspaceAdapter.write_settings_json`.
2. `--allowedTools`, passed by `ClaudeExecutor.run`.

Both surfaces are generated from `allow_rules_for_agent(agent_name, cli=...)` in `runtime/orchestrator/workspace_adapters.py`. Do not hand-edit either; `happyranch init-agent` rewrites them.

When adding orchestrator capabilities, keep them under the `happyranch` binary so they stay inside the baseline allow rule. Only add a raw-tool prefix when the operation cannot be wrapped in `happyranch`.

Agent-side completion payloads must be single-line `happyranch` invocations. The Claude permission matcher treats newlines and shell separators as separate commands. New callbacks with multiple arguments should use `--from-file <path>`.

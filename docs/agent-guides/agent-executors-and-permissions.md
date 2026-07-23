# Agent Executors And Permissions

**THR-095 (founder ruling option B):** The executor is declared in the
**org/agents/<name>.md frontmatter** (``AgentDef.executor``) — the single
authoritative store. The workspace ``agent.yaml`` is no longer read or
written for executor resolution. The executor is resolved against the
**executor registry** — capability-registered, not name-listed (THR-052).
Four **built-in** profiles ship with the runtime; custom CLI profiles are
registered in the machine-global runtime store (THR-107 — see below).

**Built-in profiles:**

| Executor | Bootstrap doc | Skills dir | Permission surface |
| --- | --- | --- | --- |
| `claude` | `CLAUDE.md` | `.claude/skills/` | `permissions.allow` in `.claude/settings.json` and `--allowedTools` |
| `codex` | `AGENTS.md` | `.agents/skills/` | sandbox flags on CLI |
| `opencode` | `AGENTS.md` | `.agents/skills/` | `opencode.json` `permission.bash` map |
| `pi` | `AGENTS.md` | `.agents/skills/` | no HappyRanch-managed sandbox |

**Per-agent model selection (THR-067):** Each built-in profile carries a verified `model_arg` — the CLI flag the executor uses when the agent has a model set:

| Executor | CLI flag | Verified syntax |
| --- | --- | --- |
| `claude` | `--model` | `--model <id>` (claude --help, 2026-07-04) |
| `codex` | `-m` | `-m <MODEL>` (codex --help, 2026-07-04) |
| `opencode` | `-m` | `-m <provider/model>` (opencode --help, 2026-07-04) |
| `pi` | `--model` | `--model <pattern>` (pi --help, 2026-07-04) |

When `model` is **unset** (the default for all existing agents), the executor launches with no model flag — each CLI uses its own default model. When `model` is **set** (via `happyranch set-model` or the agents route), the profile's `model_arg` template is substituted and injected as additive cmd elements after the binary, before permission flags. The model args never modify or reorder existing permission-bearing argv lines.

**THR-095:** Model is declared in the **org/agents/<name>.md frontmatter**
(``AgentDef.model``) — the single authoritative store. The workspace
``agent.yaml`` is no longer read or written for model resolution. The
``happyranch set-model`` command writes exclusively to the ``.md`` frontmatter.

Custom/self-registered profiles do not currently support `model_arg` (separate founder-gated track).

Missing values default to `claude`. All executors share `protocol/skills/`.

**Custom CLI profiles** (example — OpenClaw):

Any agentic CLI that accepts a prompt via a positional flag and returns
structured output can register as a custom profile. **THR-107:** custom
profiles are defined exclusively in the **machine-global runtime store**
(`<daemon-home>/executor_profiles.yaml`, typically
`~/.happyranch/executor_profiles.yaml`) — registered once per machine and
visible to EVERY org. The legacy per-org `org/config.yaml`
`executor_profiles` block is removed: it is no longer parsed, and a
one-shot startup migration lifts any lingering block into the runtime
store with a loud deprecation warning (name collisions across orgs are
logged and skipped — the existing store definition wins). A custom
profile declares an `argv_template` with supported placeholders
(`{prompt}`, `{timeout_seconds}`, `{workspace}`) and an `adapter` for
workspace preparation (one of `claude`, `codex`, `opencode`, `pi` —
typically `pi` for AGENTS.md-based CLIs).

```yaml
# ~/.happyranch/executor_profiles.yaml (machine-global runtime store;
# written by the registration flow — not hand-edited)
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

**Result-envelope (THR-107).** Custom CLIs may opt into token metering by
emitting a single-line JSON envelope on stdout between sentinel markers:

```
__HR_ENVELOPE_BEGIN__
{"envelope_version":1,"token_usage":{"input_tokens":1500,"output_tokens":420,"model":"my-cli"}}
__HR_ENVELOPE_END__
```

The envelope is **optional** — absence preserves existing behavior (no token
accounting). The ``envelope_version`` must be ``1`` (integer). The
``token_usage`` object maps 1:1 to the ``TokenUsage`` model with identical
key names. A top-level ``model`` field backfills ``token_usage.model`` when
absent. Multiple envelopes are last-wins. A minimal valid sample is:

```json
{"envelope_version":1,"token_usage":{"input_tokens":1,"output_tokens":1}}
```

The full contract is documented in
``docs/superpowers/specs/2026-07-19-custom-cli-adapter-envelope-design.md``.

## Self-Registration (custom executors)

THR-052 adds a founder-initiated, candidate-CLI-completed registration flow for
custom executor profiles. The flow has three phases — **Mint** (founder generates
a scoped token from Settings), **Conform** (candidate CLI proves it can run in
the agent workspace), and **Register** (daemon atomically writes the profile).

### Registration tokens

Tokens are held in an **in-memory, hashed** store (`runtime/daemon/registration_token.py`).
No DB schema, no migration. Daemon restart invalidates outstanding tokens (they
are short-lived — the founder re-mints in one click). Key properties:

- **Prefix**: `hrreg_` — distinct from the master bearer so the two can never be
  confused. `require_token()` (master bearer check) rejects any token that does
  not match the daemon's token file exactly; the `hrreg_` prefix is a separate
  namespace that never goes through the master-bearer gate.
- **TTL**: 600 seconds (10 minutes). Minting a new token for the same `(org, name)`
  expires any prior unconsumed token — a stale copy-paste prompt cannot be replayed.
- **Single-use**: `consume()` is an atomic validate-and-mark gate. Replay returns 401.
- **Reserve/commit/release**: The register route reserves the token before any
  durable work, commits (permanently consumes) only on clean success, and releases
  on any failure — so a config-write error does **not** consume the token; the
  candidate can retry within the unexpired TTL.

### Conformance challenge

Each minted token opens a conformance challenge with four required check-in
steps (mirrored in `RegistrationTokenStore.DEFAULT_CONFORMANCE_STEPS`):

| Step | What it proves | How it arrives |
| --- | --- | --- |
| `workspace_access` | The candidate CLI can read the agent prompt, workspace layout, and skills | Auto-completed by the candidate CLI (it is running locally) |
| `loopback_reachable` | The candidate CLI can reach `http://127.0.0.1` (the daemon loopback) | Auto-completed by the candidate CLI |
| `cli_callback` | The candidate CLI can invoke `happyranch executors register` with the `hrreg_` token | Completed when the candidate runs the register verb |
| `emit_envelope` | The candidate CLI can produce a well-formed result-envelope (THR-107 Phase 1) | CLI posts a sample envelope with the checkin; validated against the envelope schema |

The candidate CLI reports step arrivals via `POST /api/v1/orgs/{slug}/executors/conformance-checkin`
(gated by `require_registration_token()` — loopback-only; other routes' auth is
unchanged). The daemon tracks arrivals idempotently and exposes pending steps.

### Registration gate

`POST /api/v1/orgs/{slug}/executors/register` (same `require_registration_token()`
gate) consumes a fully-conformant token and writes the profile.

Registration succeeds **only** when ALL of the following are true:

1. Token is valid, unexpired, unconsumed, and loopback (checked by the dependency gate).
2. Token org matches the route slug.
3. The conformance challenge is fully complete — all four steps arrived.
4. Static validation passes: adapter is a known value, command is on `PATH`,
   `argv_template` is a non-empty list of strings with supported placeholders
   (`{prompt}`, `{timeout_seconds}`, `{workspace}`), and the profile name does
   not collide with a built-in executor.
5. No conflicting custom profile with a different definition is already registered
   (identical re-registration is idempotent).

These checks are enforced against the daemon's own token-store state —
the register request cannot succeed by asserting conformance in its
payload; the token must already have been driven through the token-gated
loopback conformance check-in sequence (all four steps recorded, token
valid, unconsumed, loopback-scoped, and org-matching) before the register
call is accepted. The store is populated by conformance check-ins that the
candidate CLI submits over the token-gated loopback channel.

The register route uses a per-profile-name lock so two concurrent registrations
for the same profile name cannot both pass the preflight collision check before
either publishes. The write order is:

1. **Reserve** the token atomically (reserve → durable store write → in-memory
   registry → audit → commit; release on any failure).
2. **Write** to the machine-global runtime store
   (`<daemon-home>/executor_profiles.yaml`) — durable. THR-107: no
   org-config write; the audit row stays in the org's audit log
   (`org_config_write` shape, section `executor_profiles`) with
   before/after snapshots of the runtime store.
3. **Register** in the process-wide in-memory registry (only after the durable
   write succeeds).

### Settings → Executors generator

The founder initiates the flow from the Settings → Executors panel
(`web/src/features/settings/sections/ExecutorsSection.tsx`). The UI
**collects only the candidate CLI's profile name** (the command,
`argv_template`, and adapter are determined by the candidate, not the
founder). On "Generate", the SPA calls
``POST /api/v1/auth/registration-token/runtime`` (loopback-only,
master-bearer-authed) and renders a generated prompt for the candidate to
paste into their CLI.

The generated prompt drives the candidate through:

1. **Self-introduction** — the candidate works out their own `command`,
   `argv_template` (with `{prompt}`, `{timeout_seconds}`, `{workspace}`
   placeholders), and `adapter` (typically `pi`).
2. **Conformance check-ins** — the candidate POSTs each step
   (`workspace_access`, `loopback_reachable`, `cli_callback`,
   `emit_envelope`) to `/api/v1/executors/runtime/conformance-checkin`
   with the `hrreg_` token as a Bearer header.
3. **Registration** — the candidate POSTs to
   `/api/v1/executors/runtime/register` with a JSON body carrying
   `command`, `argv_template`, and `adapter`. The daemon validates that
   `command` and `argv_template[0]` resolve to the same executable on
   PATH; a mismatch or unresolvable executable returns **422** at
   registration time with an actionable error message. The token is
   reserved before any durable write and released on failure, so the
   candidate can retry within the unexpired TTL.

The UI does **not** collect `command`, `argv_template`, or `adapter`
directly, and the generated prompt does **not** instruct the candidate
to run `happyranch executors register --org` — the candidate drives the
flow entirely via loopback HTTP calls to the runtime routes above.

### Registration ≠ enrollment

A registered profile becomes a **selectable executor option** (machine-global,
visible to every org); it is **not** an agent enrollment. Assigning an agent
to a registered executor
is a separate founder gate — see [Switching an Existing Agent's
Executor](#switching-an-existing-agents-executor) and
`protocol/skills/manage-agent/SKILL.md`. Registration only adds the profile
to the executor registry; the founder must still explicitly assign it to
individual agents.

### Managing registered custom profiles (THR-107 S4a)

Two founder-facing management routes expose the machine-global runtime
store for LIST + REMOVE (standard daemon bearer auth — same posture as
`GET /api/v1/executor-binaries`; **no** registration token, these are
management reads/writes, not registration):

- `GET /api/v1/executors/runtime/profiles` — lists every custom profile
  in the runtime store: `name`, `command`, `adapter`, plus a
  `present`/`path` signal mirroring `/health/prereqs`. **Custom profiles**
  derive `present`/`path` from the profile's declared `command`
  resolvability on the daemon's PATH (via `shutil.which`) — no
  `executors.json` entry is required. **Built-in** presence remains
  registry-gated via `executors.json` and is not reflected in this
  route (this route lists only custom profiles from the runtime store).
- `DELETE /api/v1/executors/runtime/profiles/{name}` — removes one
  profile from BOTH surfaces, durable store first (source of truth),
  then the transient in-memory registry
  (`ExecutorRegistry.unregister_custom_profile`) so the removed profile
  does not linger in-process until restart. 404 when the name is not in
  the store; built-in executor names are never removable. The removal is
  audited to `runtime-audit.db` with the same row shape as registration
  (`task_id='executor:<name>'`, payload `{command, argv_template,
  adapter}`, action `executor_removed`).

## Executor Notes

All executors converge on `executors._run_command`, which runs every launch under the **per-provider throttle** (`runtime/orchestrator/throttle.py`, issue #85): a `threading.BoundedSemaphore` ceiling per provider string, an inter-launch spacing gate, and slot-releasing 429 backoff. Each executor passes its own `provider` string (the profile name — `"claude"`, `"codex"`, `"opencode"`, `"pi"`, or a custom profile name) and an optional `on_throttle_event` audit callback. The throttle never touches the permission surface — it is purely a launch-timing wrapper. See [runtime-and-configuration.md → Executor Throttle](./runtime-and-configuration.md#executor-throttle) and `docs/adr/0001-per-provider-executor-throttle.md`.

Codex: `CodexExecutor.run` passes `-c sandbox_workspace_write.network_access=true` on every invocation. The workspace-write sandbox blocks localhost by default, which would prevent `happyranch report-completion` callbacks to `127.0.0.1`. The system assistant does not go through `CodexExecutor` — it launches its executor headlessly via the A-mode structured-WebSocket surface (`runtime/daemon/routes/assistant_a_mode.py`) — so `_build_session_launch_argv` in `runtime/daemon/headless_assistant.py` re-injects the same `-c sandbox_workspace_write.network_access=true` override (as a global codex option, immediately after the executable) when, and only when, the assistant executor is `codex`. Same rationale; without it the assistant's `happyranch` CLI calls die with the same localhost `ConnectError`.

opencode: `OpencodeWorkspaceAdapter.write_opencode_json` writes a strict default denying `*` and allowing `happyranch *` plus per-agent allow rules. Do not pass `--dangerously-skip-permissions`; it bypasses `opencode.json`.

Pi: `PiExecutor.run` invokes `pi -p ... --mode json` from the agent workspace. Use external containment when command/tool restriction matters.

Enrolling a worker with a non-default executor: set `"executor": "<profile-name>"` in the `happyranch manage-agent --from-file` payload where the profile name is a registered executor profile (built-in: `codex`, `opencode`, `pi`, or a custom profile registered in the machine-global runtime store). Founder approval bootstraps the right workspace surface. See `protocol/skills/manage-agent/SKILL.md`.

**THR-095:** Repos are configured in the **org/agents/<name>.md frontmatter**
(``AgentDef.repos``) — the single authoritative store. The workspace
``agent.yaml`` is no longer read or written for repos.

```yaml
repos:
  web-app: https://github.com/t-benze/web-app.git
  docs: https://github.com/t-benze/docs.git
```

**THR-095:** `happyranch init-agent` no longer creates or touches `agent.yaml`.

## Switching an Existing Agent's Executor

**THR-095:** The executor lives in the **org/agents/<name>.md frontmatter**
(``AgentDef.executor``) — the single authoritative store. The workspace
``agent.yaml`` is no longer read or written for executor resolution.

Switch an existing agent with the founder command:

```bash
happyranch set-executor --org <org> <agent> --executor <profile-name>
```

It reconciles the `.md` frontmatter (atomic rewrite) and the executor
bootstrap (``ensure_workspace_ready`` with the new provider). An unregistered
executor is rejected with the list of registered profiles.

Switching **away from Claude** leaves the Claude-only files (`CLAUDE.md`, `.claude/`) behind, because the new adapter writes `AGENTS.md`/`.agents/` and never deletes them. By default the command **warns** that these files are stale and names them; it never auto-deletes. Pass `--clean` to delete them:

```bash
happyranch set-executor --org <org> <agent> --executor pi --clean
```

(The symmetric case — switching *to* Claude leaves `AGENTS.md`/`.agents/`/`opencode.json` stale — is not yet handled.)

**THR-095:** ``happyranch init-agent`` no longer emits ``executor_drift``
warnings — the .md frontmatter is the single source of truth so there is
no dual-surface drift to detect.

## Permission Model

Agents call the orchestrator CLI as their sanctioned side-effect channel: `happyranch report-completion`, `happyranch memory`, `happyranch manage-repo`, `happyranch manage-agent`, `happyranch dispatch`, and related callbacks. Baseline allow rule for every agent: `happyranch`.

Per-agent extras are declared in agent frontmatter under `allow_rules:`. Keep extras narrow; each prefix can mutate shared external state on future tasks.

For Claude, allow rules must be generated in two places:

1. `.claude/settings.json` `permissions.allow`, written by `ClaudeWorkspaceAdapter.write_settings_json`.
2. `--allowedTools`, passed by `ClaudeExecutor.run`.

Both surfaces are generated from `allow_rules_for_agent(agent_name, cli=...)` in `runtime/orchestrator/workspace_adapters.py`. Do not hand-edit either; `happyranch init-agent` rewrites them.

When adding orchestrator capabilities, keep them under the `happyranch` binary so they stay inside the baseline allow rule. Only add a raw-tool prefix when the operation cannot be wrapped in `happyranch`.

Agent-side completion payloads must be single-line `happyranch` invocations. The Claude permission matcher treats newlines and shell separators as separate commands. New callbacks with multiple arguments should use `--from-file <path>`.

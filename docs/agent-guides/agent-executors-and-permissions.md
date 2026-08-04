# Agent Executors And Permissions

> **SUPERSESSION NOTICE (TASK-4009/TASK-4012/TASK-4195):** Skill materialization now uses
> the **canonical skill store + workspace symlink architecture**
> (macOS-only) with two isolation modes: distinct-identity (default) and
> same-owner opt-in (``HAPPYRANCH_ALLOW_SAME_OWNER_EXECUTOR``). The legacy
> per-session copy model is REMOVED. See
> ``protocol/05b-agent-runtime.md`` § "Canonical skill store + workspace symlinks"
> for ownership, provenance, link validation, repair/refusal/withdrawal/retention
> semantics, macOS provisioning, same-owner opt-in, pre-launch integrity validation,
> and the compatibility-fallback boundary.
> Linux and Windows are NOT supported — explicitly fail closed.

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

**Worktree-root guard.** The ``make-worktree`` skill (injected as a system
contract per §4.7 of the orchestrator protocol) delivers a stdlib-only guard
script (``worktree_guard.py``) alongside its ``SKILL.md``. The guard runs at
worktree setup and verify points, validates repository/worktree identity
(same git common directory + registered worktree), records canonical absolute
primary and task-worktree roots, snapshots the primary checkout baseline
with content hashes (including untracked files — not just names), and at
verify detects accidental primary-checkout edits — including mutations of
already-dirty tracked paths, already-staged files, and already-untracked
files. The guard fails with preservation-first recovery: ``git diff`` +
``patch`` for tracked and staged changes, ``tar`` archive for untracked
content, with all path-bearing shell commands shell-quoted via
``shlex.quote()``. It never suggests destructive commands. The delivered
skill computes the workspace root as 5 parents above the task worktree
(``$WORKTREE_ROOT/../../../../..``) and locates the guard under
``.claude/skills/`` then ``.agents/skills/``, failing loudly if neither
exists. It is a narrow, non-permission tool with no DB, API, schema, audit,
auth, notification, or sandbox footprint.

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
  # D6 (2026-07-27): adapter is the DEPRECATED alias for workspace_adapter_id.
  # For new profiles, use workspace_adapter_id: pi instead.
  workspace_adapter_id: pi
  # Optional — THR-107 D9 / Phase 3. Accepted values: generic-cli (template-based
  # generic CLI) or custom-adapter:<id> (D7B — separately registered,
  # founder-approved, hash-verified custom adapter executable).
  # D6: command_adapter is the DEPRECATED alias for command_adapter_id.
  # For new profiles, use command_adapter_id: generic-cli instead.
  command_adapter_id: generic-cli
  command_adapter: generic-cli
```

**Generic-cli profiles** use the `GenericCliExecutor` which validates the
argv template at registration time and substitutes placeholders at launch.
No shell string is constructed — each template element becomes exactly one
argv element, with placeholders replaced by their resolved values.

**Custom-adapter profiles** (D7B, ``command_adapter_id: custom-adapter:<id>``)
route through ``CustomAdapterExecutor`` instead — see
[Custom adapter profiles](#custom-adapter-profiles-thr-107-d7b) below.

**Adapter vs command_adapter (THR-107 D9 / Phase 3 + D6).** These are separately
composable. The canonical workspace `workspace_adapter_id` (deprecated alias:
`adapter`) controls which bootstrap files are written (e.g., `CLAUDE.md` with
`--allowedTools` for claude, or `AGENTS.md` for pi). The canonical
`command_adapter_id` (deprecated alias: `command_adapter`) controls which
execution template builds argv and parses result output. For custom profiles
this is `generic-cli` (template-based generic CLI) or `custom-adapter:<id>`
(D7B — separately registered, founder-approved, hash-verified custom adapter
executable, subprocess-only, mandatory v1 AdapterInput/AdapterOutput,
D5 baseline-only posture).

*Concrete example:* A custom profile with `workspace_adapter_id: claude` and
`command_adapter_id: generic-cli` gets a Claude workspace (CLAUDE.md,
settings.json, `--allowedTools` generation) but uses the generic template-based
executor for argv construction and result parsing. The workspace adapter controls
permission files; the command adapter controls subprocess launch. These may
differ — crossing them without explicit intent would be a security bug.
Similarly, `workspace_adapter_id: pi` with `command_adapter_id: generic-cli`
writes AGENTS.md (no permission file) while launching via the generic CLI
template.

**Result-envelope (THR-107).** Custom CLIs may opt into token metering by
emitting a single-line JSON envelope on stdout between sentinel markers:

```
__HR_ENVELOPE_BEGIN__
{"envelope_version":1,"token_usage":{"input_tokens":1500,"output_tokens":420,"model":"my-cli"}}
__HR_ENVELOPE_END__
```

**D7A strict enforcement (2026-07-27):** new registrations and re-registrations
automatically receive ``envelope_policy: "strict"``. A strict profile MUST
emit a valid v1 envelope on every execution — missing, malformed, or
invalid-version envelopes fail closed with an actionable error. Existing
profiles without ``envelope_policy`` are LEGACY COMPATIBILITY (optional
envelope). To opt into strict enforcement: verify your CLI emits a valid
v1 envelope, then re-register.

The envelope is **mandatory for strict profiles, optional for legacy**.
The ``envelope_version`` must be ``1`` (integer). The
``token_usage`` object maps 1:1 to the ``TokenUsage`` model with identical
key names. A top-level ``model`` field backfills ``token_usage.model`` when
absent. Multiple envelopes are last-wins. A minimal valid sample is:

```json
{"envelope_version":1,"token_usage":{"input_tokens":1,"output_tokens":1}}
```

The full generic-cli envelope contract is in
``docs/superpowers/specs/2026-07-19-custom-cli-adapter-envelope-design.md``.

**Custom adapter profiles (THR-107 D7B).** Profiles with
``command_adapter_id: custom-adapter:<id>`` bind to exactly one registered,
conformance-passed, founder-APPROVED custom adapter executable. The
``CustomAdapterExecutor`` spawns the adapter as a subprocess with the v1
``AdapterInput`` JSON on stdin and parses the v1 ``AdapterOutput`` JSON from
stdout. No ``argv_template``, ``command``, or PATH resolution is used — the
adapter executable's absolute path is resolved from the approved adapter entry.
Custom-adapter profiles do **not** require a separate ``executors.json`` record
keyed by their profile name — the approved adapter's absolute path IS the launch
artifact, verified by hash at every launch.

**Adapter contract reference (THR-107 seq184).** The authoritative v1
``AdapterInput``/``AdapterOutput`` contract is served by the running daemon via
``GET /api/v1/runtime/adapters/contract-reference`` — accessible during
registration through the existing scoped registration-token posture on loopback
(adapter-purpose ``hrreg_`` token, read-only, token is not consumed). The
endpoint returns JSON Schemas generated from the shipping Pydantic models
(``runtime/orchestrator/adapter_contract.py``) — the **server-derived schema is
canonical**. Candidates implementing adapter wrappers must fetch this reference
first and follow the returned schemas exactly.

**Adapter lifecycle:**

0. **Fetch contract-reference** — candidate CLI fetches
   ``GET /api/v1/runtime/adapters/contract-reference`` with the scoped
   adapter-purpose ``hrreg_`` token to learn the exact
   ``AdapterInput``/``AdapterOutput`` JSON Schemas (loopback-only, read-only).
1. **Register** — operator submits executable path, version, capabilities via
   ``POST /api/v1/runtime/adapters/register`` → PENDING adapter entry with
   SHA-256 hash computed at registration.
2. **Conform** — bounded stdin/stdout conformance probe (``POST
   /api/v1/runtime/adapters/{id}/conformance``) validates the adapter speaks v1
   ``AdapterInput``/``AdapterOutput``.
3. **Approve & connect (THR-107 seq237)** — founder explicitly approves the exact
   artifact snapshot (``POST /api/v1/runtime/adapters/{id}/approve``) binding
   path, hash, version, capabilities, and contract_version. **When the adapter
   has an ``intended_profile_name``**, the server atomically approves the snapshot
   AND creates/binds that named custom profile (``command_adapter_id: custom-adapter:<id>``)
   in one transaction — no client-side bind follow-up is needed. Adapters without
   an intended profile (master-bearer registration) are approved without auto-binding
   and retain explicit advanced Bind recovery via Settings.
4. **Advanced Bind recovery** — for approved adapters without an intended profile
   (``recovery_ready`` eligibility) or where atomic binding did not succeed
   (``ready_to_bind`` eligibility), the founder provides an explicit profile name
   through ``POST /api/v1/runtime/adapters/{id}/bind-profile``. In the ordinary
   Settings UI this recovery affordance lives inside **Settings → Executors →
   Custom CLIs**, not in a separate adapter list or the pending queue. Only
   APPROVED adapters with hash-verified artifacts can bind. The registration
   route rejects binding to PENDING, unknown, removed, tampered, non-regular, or
   non-executable adapters before any durable mutation, registry mutation, audit
   write, or token consumption. **This path is secondary to atomic
   approve-and-bind (seq237).**
5. **Remove (THR-107)** — the authenticated ``DELETE /api/v1/runtime/adapters/{adapter_id}``
   route still exists, but the ordinary Settings UI no longer exposes a standalone
   Custom Adapters list. Adapter-backed custom CLIs are managed inside
   **Settings → Executors → Custom CLIs**; removing a profile that references
   ``command_adapter_id: custom-adapter:<id>`` removes the binding from the
   runtime store. The underlying adapter registration cleanup is not surfaced as
   a separate founder-facing UI in ordinary Settings. When removal is performed
   via the API, the caller MUST supply an exact durable snapshot (all material
   identity and binding facts) — the server rejects stale, re-registered, and
   wrong-target snapshots. Under the reentrant adapter-store lock the adapter is
   durably removed and an audit entry (scope ``adapter:<id>``, action
   ``adapter_removed``) is written. The adapter's on-disk executable is never
   touched.

**Per-launch hash verification:** the ``CustomAdapterExecutor`` re-verifies
path type (exists, regular file, executable) and SHA-256 immediately before
EACH ``Popen``. The check is inside the per-attempt launch closure, so a
throttle retry after a rate-limited response re-verifies the artifact.
Hash mismatch, removal, non-regular, or non-executable → launch fails closed
with actionable re-registration/approval error.

**Mandatory v1 AdapterOutput:** custom-adapter profiles require a valid v1
``AdapterOutput`` JSON object. Rejected: missing, malformed, non-object,
unknown-version, oversized (>1MB) output; adapter identity/version/contract
mismatch; session-id echo mismatch; success/returncode inconsistency.

**Canonical adapter ID provenance invariant (THR-107 seq268):** the
``adapter_metadata.adapter`` field MUST exactly equal the stable
server-derived canonical adapter ID (e.g. ``kimi-adapter``), obtained
from the ``canonical_adapter_id`` field of the contract-reference
endpoint response. The value is a machine-stable slug derived from the
submission profile — never a display name, provider string, or arbitrary
implementation identity. A mismatch fails the conformance probe at
registration AND blocks every launch at runtime (D7B). The
contract-reference's self-test/probe fixture uses the same real
token-derived ID so adapter authors can verify exact-ID compatibility
before submission.

**Rollback:** re-register the profile with ``command_adapter_id: generic-cli``
or revert the deployment. Legacy stored profiles are never auto-mutated.

**D5 baseline-only:** the custom adapter contract introduces no allow-rule,
sandbox, network-access, filesystem-access, or permission changes.

**THR-107 seq244 dependency manifest:** new adapter registrations
require ``dependency_manifest_version: 1`` with a non-empty ``dependencies``
list (each: ``{executable: absolute-path, sha256: hex}``). Every declared
child executable must exist, be a regular executable file, and match its
declared SHA-256 at registration and again before each launch.
Manifest-adapters are explicitly absolute and hash-pinned/revalidated with
no executor fallback to ambient PATH; the adapter process inherits
normalized PATH for normal callback/utility availability. ``token_metering`` capability requires truthful non-null
``token_usage`` at conformance. Legacy entries without the manifest are
never auto-mutated. A dependency or wrapper change requires re-submission
and founder re-approval.

The code-level definition is ``runtime/orchestrator/adapter_contract.py``
(Pydantic models). The canonical contract surface for external consumers is the
``GET /api/v1/runtime/adapters/contract-reference`` endpoint (THR-107 seq184),
which returns schemas generated from those models at runtime. The
**server-derived schema is canonical** — candidates implementing adapter
wrappers must fetch the contract-reference endpoint and follow the returned
schemas, never a hand-constructed copy. Normative prose is the signed
architecture §2
(``docs/superpowers/specs/2026-07-24-unified-adapter-runtime-architecture.md``).

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
- **TTL**: 1800 seconds (30 minutes). Minting a new token for the same `(org, name)`
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
4. Static validation passes: adapter is a known value, the declared
   `command` name matches `argv_template[0]` (string-equality validation;
   PATH resolution is not used — THR-107 seq155), `argv_template` is a
   non-empty list of strings with supported placeholders (`{prompt}`,
   `{timeout_seconds}`, `{workspace}`), and the profile name does not
   collide with a built-in executor.
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
   `command`, `argv_template`, `adapter`, and an optional
   `command_adapter` (THR-107 D9 / Phase 3). The daemon validates that
   `command` and `argv_template[0]` are identical strings; a mismatch
   returns **422** at registration time with an actionable error
   message. PATH resolution is no longer performed — the registered
   binary path is validated at launch time via the machine-local
   ``executors.json`` pin (THR-107 seq155). The token is reserved
   before any durable write and released on failure, so the candidate
   can retry within the unexpired TTL.

The UI does **not** collect `command`, `argv_template`, or `adapter`
directly, and the generated prompt does **not** instruct the candidate
to run `happyranch executors register --org` — the candidate drives the
flow entirely via loopback HTTP calls to the runtime routes above.

### Registration ≠ enrollment

A registered profile whose binary is currently launchable (`present: true`)
becomes a **selectable executor option** (machine-global, visible to every
org). Registered profiles whose binary is absent or unavailable
(`present: false`) remain **visible but disabled/non-assignable** — they
appear in the UI to show what has been registered, but the founder cannot
assign them to an agent until the runtime reports them as launchable. All
registered profiles are **not** agent enrollments. Assigning an agent
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
  in the runtime store: `name`, `command`, canonical `workspace_adapter_id`
  (workspace adapter selector) and `command_adapter_id` (command adapter
  selector), plus deprecated aliases `adapter`, `adapter_id` (workspace
  aliases only), and `command_adapter` (command alias only), with a
  `present`/`path` signal mirroring `/health/prereqs`. Custom
  profiles (generic-CLI) and **built-ins** derive `present`/`path` from the
  machine-local binary registry (``executors.json``) keyed by the
  profile name — the same gating for both (THR-107 seq155).  Custom-adapter
  profiles (``command_adapter_id: custom-adapter:<id>``) are an exception —
  they use the exact founder-APPROVED, hash-verified absolute adapter
  executable as their launch artifact and do **not** require a separate
  ``executors.json`` record.  No
  ``shutil.which`` or PATH-based fallback is used.  Built-in presence
  is not reflected in this route (this route lists only custom profiles
  from the runtime store — use ``/health/prereqs`` for built-in
  availability).
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

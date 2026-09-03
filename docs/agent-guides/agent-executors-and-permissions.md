# Agent Executors And Permissions

> **SUPERSESSION NOTICE (TASK-4009/TASK-4012/TASK-4346):** Skill materialization
> now uses the **canonical skill store + workspace symlink architecture**
> (macOS and Linux). The legacy per-session copy model is REMOVED. The executor
> and daemon share the same OS identity — linked, validated relative skill
> links live under BOTH ``.claude/skills`` and ``.agents/skills``. Every
> user-facing and executor-facing guidance surface names both roots.
> Integrity verification is DETECTION-ONLY with FAIL-CLOSED refusal — NO
> OS-level security boundary, no automatic repair from same-UID local source.
> See ``protocol/05b-agent-runtime.md`` § "Canonical skill store + workspace
> symlinks" for ownership, provenance, link validation, refusal/withdrawal/
> retention semantics, integrity verification, and the compatibility-fallback
> boundary. Windows and unknown platforms are NOT supported — explicitly fail
> closed.
>
> **INTEGRITY HONESTY NOTICE:** Do NOT call canonical targets immutable,
> protected, or claim write/chmod/ACL denial. The prompt guard is operational
> guidance, not enforcement. Integrity verification is DETECTION-ONLY with
> FAIL-CLOSED refusal — NO automatic repair from same-UID local source. A
> same-UID process may mutate, race validation, and affect active/overlapping
> sessions. Manual recovery only: (a) ``set-executor`` for broken links,
> (b) ``happyranch skills recover <slug> <version> <content_hash>`` for
> corrupted canonical bytes of the eligible current B2 version. No automatic
> repair from same-UID local source.
> Recovery requires that an authoritative re-sync/redeploy of release or
> custom artifacts has occurred outside the compromised same-owner local
> source before recovery can safely materialize again. Policy withdrawal
> and atomic link repair remain safe.

**Supported host contract:** macOS (darwin) and native Linux use the same
same-owner POSIX adapter: relative symlinks, same-directory ``os.replace``,
cosmetic chmod hardening, and direct ``subprocess.Popen`` launch. Linux hosts
must provide those filesystem/process semantics; missing primitives fail
through the existing named refusal paths. Containers and network filesystems
are supported only when they preserve those semantics. Windows and unknown
platforms have no fallback. See the current Linux design in
``docs/superpowers/specs/2026-08-22-linux-canonical-store-design.md``.

# Agent Executors And Permissions

**THR-095 (founder ruling option B):** The executor is declared in the
**org/agents/<name>.md frontmatter** (``AgentDef.executor``) — the single
authoritative store. The workspace ``agent.yaml`` is no longer read or
written for executor resolution. The executor is resolved against the
**executor registry** — capability-registered, not name-listed (THR-052).
Four **built-in** profiles ship with the runtime; approved custom-adapter
profiles are bound in the machine-global runtime store (THR-107 — see below).

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

**Custom-adapter profiles** (D7B, ``command_adapter_id: custom-adapter:<id>``)
route through ``CustomAdapterExecutor`` instead — see
[Custom adapter profiles](#custom-adapter-profiles-thr-107-d7b) below.

**Custom-adapter result contract (THR-107).** Every registered custom adapter
receives one v1 ``AdapterInput`` JSON object on stdin and MUST return one valid
v1 ``AdapterOutput`` JSON object on stdout. This contract is mandatory; there
is no optional sentinel envelope or legacy profile fallback. Before launch the
daemon requires founder approval and an exact profile bind, verifies the
approved executable SHA-256, and resolves eligibility server-authoritatively.
Direct-connect uses the same approved bind/hash/eligibility contract.
Recovery is explicit: repair or re-register a valid approved adapter, or
reassign the agent to a built-in executor. Built-in executor transport is
unchanged and does not use ``AdapterOutput``.

THR-200 PR 1/3 keeps this contract at version 1 while adding dormant optional
session plumbing. A non-empty ``AdapterInput.session.resume_session_id`` is
valid only for truthful thread invocations. Optional
``AdapterOutput.session_status`` means ``fresh`` (new provider session),
``resumed`` (supplied session continued), or ``not_found`` (supplied session
absent). A sent session requires a status; incoherent combinations fail as
post-launch contract errors before an ``agent_session_id`` can reach mutation
callers, with stdout/stderr tails preserved. Legacy outputs remain compatible
when no session was sent, including existing kimi/codebuddy-shaped output.
Tasks remain fresh. PR 1 neither proves nor consumes a custom-adapter resume
capability; ``thread_runner`` remains unchanged and excludes custom profiles,
while SQLite thread transcript/delivery semantics remain canonical.

THR-200 PR 2/3 adds an explicit registration-time
``verify_thread_resume`` opt-in. ``thread_resume`` is reserved: a submitted
capability claim is rejected, and only the server may append it after three
stateful provider calls in one daemon-owned temporary workspace prove a fresh
opaque canary and session id, a genuine resume that recalls that canary without
receiving it again, and a fabricated-id ``not_found`` failure with no model
text. The entry stores the capability, verification timestamp, and probed v1
contract version in one atomic YAML replacement only after all stages pass;
failure leaves the previous entry byte-identical and removes the workspace.
Re-registration always requires a new opt-in proof and resets approval. This
earned receipt remains dormant: no runner reads it in PR 2, built-ins and
registered kimi/codebuddy ordinary behavior are unchanged, and custom profiles
still receive fresh/full SQLite-canonical thread prompts.

**Adapter contract reference (THR-107 seq184).** The authoritative v1
``AdapterInput``/``AdapterOutput`` contract is served by the running daemon via
``GET /api/v1/runtime/adapters/contract-reference`` — accessible during
registration through the existing scoped registration-token posture on loopback
(adapter-purpose ``hrreg_`` token, read-only, token is not consumed). The
endpoint returns JSON Schemas generated from the shipping Pydantic models
(``runtime/orchestrator/adapter_contract.py``) — the **server-derived schema is
canonical**. Candidates implementing adapter wrappers must fetch this reference
first and follow the returned schemas exactly.

**Canonical daemon-managed adapter path (THR-107 seq339/340).** The
contract-reference response includes ``canonical_directory`` (absolute path to
``<daemon-home>/adapters/``, created with restrictive 0o700 owner-only mode) and
``required_executable_path`` (the exact absolute canonical path where the adapter
wrapper MUST live — ``<daemon-home>/adapters/<canonical-adapter-id>``). The
filename is the canonical adapter ID itself (lowercase alnum/hyphen only).

**The scoped adapter submission route** (``POST /runtime/adapters/submit``)
validates ``body.executable`` against this server-owned canonical target:

- Rejects any non-canonical path (foreign directories, traversal spellings,
  alternate filenames, symlink escape) with a 422 error that names
  ``required_executable_path`` and keeps the token retryable.
- The registration seam (``register_custom_adapter`` with
  ``intended_profile_name``) independently rechecks the canonical path so a
  route-only check cannot be bypassed.
- Applies to **new scoped submissions and scoped re-registrations** — both
  must use the exact canonical location.
- Does **NOT** apply to dependency records (their existing absolute-path/
  hash-pinned rules remain) or the master-bearer ``/register`` route
  (no-intended-profile operational/recovery path unchanged).
- Existing APPROVED adapters at arbitrary locations remain hash-valid and
  launchable — no automatic migration, invalidation, or rewriting occurs.

**THR-107 slices 1–3 superseded this scoped-submission lifecycle as the
normal-flow UI path.** Steps 0–4 below (contract-reference fetch → submit
→ conformance → PENDING → founder approve/advanced-bind) describe the
seq141 scoped-token adapter-submission mechanism
(``POST /runtime/adapters/submit``) — it still exists at the API level and
its tests remain, but the ordinary Settings/onboarding "Connect a custom
CLI" UI no longer drives it. The normal UI now uses the THR-107 slice 1
direct-connect mechanism instead: mint (name only — the founder never picks
a workspace convention) → read the daemon-issued wrapper path from
``GET /runtime/custom-cli/status`` → the candidate CLI's single
``POST /runtime/custom-cli/connect`` declares its OWN
``workspace_adapter_id`` in the manifest and both proves wrapper integrity
and creates the receipt-only connection record (it starts zero subprocesses)
→ the browser auto-calls
``POST /runtime/custom-cli/{operation_id}/commit`` the moment it lands →
Connected, no PENDING wait, no founder click. Founders can check the same
terminal outcome from the CLI with
``happyranch custom-cli status <profile-name> [--wait]``.
See ``protocol/05b-agent-runtime.md`` § "Slices 1–3: projection, launch fence,
UI cutover" (and its `workspace_adapter_id` correction note) for the full
contract. The PENDING/approve/reject/bind-profile routes below remain as
operator-only one-time disposition tooling for legacy records — a new
custom CLI should always use the ordinary Connect
flow instead.

Before submitting the receipt, wrapper authors must locally send a fresh,
opaque canary through the ordinary one-shot provider path in the entire
``AdapterInput.prompt`` through one real provider invocation. It must obtain a
genuine terminal provider response containing the complete canary, then the
wrapper—not the provider—must construct ``AdapterOutput`` with that canary in
``result.text``. It must not fabricate a static success. The short probe guides
no optional tool use or workspace exploration, without enforcing or collecting
telemetry about provider-internal actions; normal task behavior is unchanged.
Trusted daemon commit/projection repeats this bounded behavioral proof before it
writes an adapter or profile; success also requires the matching invocation ID,
canonical adapter ID, and consistent terminal return code. Provider
``agent_session_id`` is optional and resume-only: a wrapper must faithfully
propagate it when the provider supplies one, but it must never fabricate one or
use it as a substitute for the HappyRanch invocation-identity proof. A wrapper
must faithfully propagate terminal provider errors and must never invent success
without a terminal provider response. Direct conformance does not require optional token usage:
only candidates that declare ``token_metering`` must supply trustworthy
canonical ``token_usage``. Failure diagnostics are category-only and never
persist provider stdout, stderr, errors, or the canary.

**Adapter lifecycle (legacy scoped-submission mechanism — operator-only, not the normal UI path):**

0. **Fetch contract-reference** — candidate CLI fetches
   ``GET /api/v1/runtime/adapters/contract-reference`` with the scoped
   adapter-purpose ``hrreg_`` token to learn the exact
   ``AdapterInput``/``AdapterOutput`` JSON Schemas (loopback-only, read-only).
1. **Register** — operator submits executable path, version, capabilities via
   ``POST /api/v1/runtime/adapters/register`` → PENDING adapter entry with
   SHA-256 hash computed at registration. The optional
   ``verify_thread_resume: true`` request runs the separately earned THR-200
   probe; clients may not put ``thread_resume`` in ``capabilities``.
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
   and require the operator-only advanced Bind recovery route below (step 4) —
   not surfaced in Settings since THR-107 slice 3.
4. **Advanced Bind recovery** — for approved adapters without an intended profile
   (``recovery_ready`` eligibility) or where atomic binding did not succeed
   (``ready_to_bind`` eligibility), an operator provides an explicit profile name
   through ``POST /api/v1/runtime/adapters/{id}/bind-profile``. **THR-107 slice 3
   removed this recovery affordance from the ordinary Settings UI entirely** — it
   is API-only operator tooling now, not surfaced anywhere in Settings → Executors.
   Only APPROVED adapters with hash-verified artifacts can bind. The registration
   route rejects binding to PENDING, unknown, removed, tampered, non-regular, or
   non-executable adapters before any durable mutation, registry mutation, audit
   write, or token consumption.
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

**D5 baseline-only:** the custom adapter contract introduces no allow-rule,
sandbox, network-access, filesystem-access, or permission changes.

**Wrapper-owned headless launch posture:** every custom-adapter wrapper MUST
select and apply the non-interactive, sufficiently permissive launch posture
required by its own agentic CLI. It MUST NOT rely on
``executor_context.permission_mode`` for that CLI-specific headless posture or
on daemon translation of policy or provider-specific allow-rule syntax. That
existing v1 field remains a legacy nullable, provider-specific compatibility
field; ``CustomAdapterExecutor`` supplies ``null`` for custom-adapter
invocations. Wrappers must also preserve the daemon-provided callback
environment, including ``PATH``, so the agent can make required ``happyranch``
callbacks after ordinary workspace actions. This is verified through wrapper
review and founder approval; it adds no new daemon-supplied or
daemon-translated permission policy or field to ``AdapterInput``, and no
daemon-managed permission enforcement. Founder approval must include evidence
of a successful end-to-end unattended session that invokes the required
callback.

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

## Spawn-Environment Invariant and Worktree Isolation

Every runtime-created child subprocess — agent executor sessions, custom-adapter
launches, and job-script subprocesses — inherits a sanitized copy of the daemon's
environment that **strips** ``VIRTUAL_ENV``, ``UV_PROJECT_ENVIRONMENT``,
``UV_PYTHON``, and ``UV_SYSTEM_PYTHON``.
These variables are stripped because the daemon itself runs inside the shared
canonical HappyRanch venv. If a child process inherits any of these, a bare
``uv sync`` or ``uv pip install -e .`` executed from a disposable worktree would
rewrite the shared venv's editable-install ``.pth`` file to point at the worktree
instead of the canonical source checkout. When the worktree is removed, every
agent using that venv loses the ability to import the ``cli`` and ``runtime``
packages.

**Preserved:** ``PATH`` (including daemon-normalized standard tool directories),
``HAPPYRANCH_ORG_SLUG``, and all other ``HAPPYRANCH_*`` runtime variables.

### Worktree Rule (hard)

**Never run** ``pip install -e .``, ``uv pip install -e .``, or
``uv sync --active`` from inside a per-task worktree when the inherited
environment carries the shared canonical venv. These commands rewrite the
shared ``.pth`` entry and break every agent using that venv.

Instead, create an **isolated worktree-local venv** before installing:

```bash
python3 -m venv .venv-local
source .venv-local/bin/activate
uv pip install -e .
```

**Never run editable install or ``uv sync --active`` against an inherited
shared venv.** Isolation is prevention; PYTHONPATH recovery (below) is
secondary and never a substitute for proper isolation.

### Recovery (secondary)

If a stale ``.pth`` has already broken the CLI, prefix every invocation with
the canonical source checkout on ``PYTHONPATH``:

```bash
PYTHONPATH=/path/to/canonical/happyranch happyranch <args>
```

This is a non-destructive workaround — it does not modify the ``.pth`` file
or run ``pip``/``uv``. Use it for one-off recovery; the permanent fix is to
restore the editable install from the canonical checkout.

The ``happyranch doctor`` command (local, read-only, no daemon required)
checks whether the editable-install pointer resolves to the canonical source
and emits the exact non-destructive repair command on failure. It uses an
independent git-based canonical source detection — it never trusts the
``.pth``-selected ``runtime`` import or an untrusted environment override
for the expected canonical path.

## Registered custom executors

Custom executor profiles use an explicit
`command_adapter_id: custom-adapter:<id>` binding. The referenced adapter must
already be registered, pass the shared conformance and health contract, and be
founder-approved. Profile validation binds the adapter's executable identity and
SHA-256 hashes; launch eligibility is re-derived from server-authoritative
registry state rather than trusted from profile text. The same registered
adapter identity is used by the direct-connect flow and its commit projection.

Legacy `generic-cli`, command/argv templates, omitted adapter identifiers, and
candidate-driven Mint/Conform/Register profile creation are retired. They are
rejected with guidance to register and approve an adapter and bind its
`custom-adapter:<id>`; there is no silent conversion or automatic/versioned
fallback. Supported recovery is to reassign the agent to `claude`, `codex`,
`opencode`, or `pi`, or ordinarily re-register a valid approved custom-adapter
profile after correcting its registration, conformance, health, or hash state.

### Direct custom-CLI receipt boundary (Slice A)

`POST /api/v1/runtime/custom-cli/connect` is a loopback registration-token-only
CLI ingress, not a browser API. It consumes a direct-purpose token only after
validating the daemon-owned canonical wrapper and a strict v2 child manifest,
then returns a nonsecret `received_nonlaunchable` receipt. It never launches a
process, writes a profile or registry entry, or exposes the registration token.
Projection, COMMITTED eligibility, and Connected UI are explicitly deferred.

### Settings → Executors generator

The generated prompt drives the candidate through:

#### Built-in binary registration (THR-107 seq352)

For **built-in** profiles (Claude Code, Codex, OpenCode, Pi), the SPA
mints a ``purpose='binary'`` token and renders a **strictly sequential,
copy-pasteable shell script** with no background or parallel commands.
The script opens with ``set -e`` so any ``curl`` HTTP error (signaled by
``--fail-with-body -sS``) stops execution immediately while still
printing the server error detail for debugging. The fourth check-in
(``emit_envelope``) uses an explicit ``|| exit 1`` guard because
command-substitution failures are not caught by ``set -e`` alone.

The built-in prompt drives the candidate through:

1. **Self-discovery** — the CLI uses ``command -v`` or ``which`` to
   resolve its own absolute binary path and exits with a clear error if
   the binary is not found or not executable.
2. **Sequential conformance check-ins** — four independent ``curl``
   commands POST ``workspace_access``, ``loopback_reachable``,
   ``cli_callback``, and ``emit_envelope`` in order. Each response is
   printed; a failed request stops the sequence.
3. **Completion gate** — the fourth (``emit_envelope``) response is
   captured and checked: the script reads the returned JSON and exits
   unless ``"all_complete":true`` is present. This is a **mechanical
   enforcement**, not an advisory comment — ``register-binary`` is
   reachable only after this gate passes.
4. **Registration** — the binary path is POSTed to
   ``/api/v1/executors/runtime/register-binary``. The kind is carried
   by the token (no ``kind`` field in the body).

Error responses from ``register-binary`` are designed to be
**actionable for an external CLI operator** without reading server
source code:

- **401 (invalid/expired/consumed/wrong-runtime token)** — tells the
  candidate to regenerate the connect prompt from Settings > Executors or
  onboarding and run the full sequence again. This applies to both
  pre-handler rejection (``_check_registration_token`` — invalid,
  expired, consumed, or non-registration-form token) and post-handler
  validation (``validate_runtime`` — consumed mid-flight).
- **400 (incomplete conformance)** — names the pending steps and
  instructs the candidate to complete them sequentially and await
  ``all_complete:true``.
- **422 (bad path)** — reports whether the path is non-absolute,
  non-existent, or non-executable so the candidate can correct it.
- **500 (write failure)** — releases the token for retry and advises
  checking daemon data-directory write access.

Failed attempts do **not** consume the token and do **not** write the
registry; the token remains retryable within its TTL.

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

**GH-709 Slice C (readiness marker):** `happyranch init-agent` reports `done`
only after the **selected executor profile's exact readiness marker** exists
as a valid regular file produced by the bootstrap. The bootstrap now also
materializes the workspace skills tree, so the claude marker
`.claude/skills/start-task/SKILL.md` exists immediately after init (not only
at first session spawn); `codex`/`opencode`/`pi` use `AGENTS.md`; custom
profiles use their registered `readiness_marker_fragment`. An unregistered
profile, a missing/wrong-profile marker, or a non-regular marker (directory,
dangling link) emits a per-agent `error` — never `done` — the stream stops at
the first error (no `all_done`), and the CLI exits nonzero.

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

## Canonical Adapter Path Migration Story (THR-107 seq339/340)

Existing APPROVED custom adapters at arbitrary (non-canonical) locations are
**not affected** — they remain hash-valid, launchable, and are never auto-migrated,
invalidated, rewritten, or moved. No automatic migration occurs.

An operator who wants to bring an existing adapter under the canonical
managed-location model should:

1. Create a **new scoped registration** with the same intended profile name
   via the normal Settings → onboarding flow. This places the wrapper at its
   canonical path (``<daemon-home>/adapters/<canonical-id>``).
2. Complete the existing founder approval and lifecycle gates for the new
   registration.
3. When ready, retire the old adapter record via the management UI.

Both old and new registrations coexist during the transition — there is no
conflict, no forced cutover, and no downtime. The old adapter remains launchable
until explicitly retired.

**Master-bearer /register path:** Enforcing canonical placement on the
master-bearer ``/register`` route (no intended profile, operational/recovery
path) is a separate founder authorization/contract decision and is **not**
implemented in this phase.
> **Current custom executor contract (TASK-6514):** Template-based generic
> profiles are retired. Register and approve a custom adapter, then bind a
> profile with `command_adapter_id: custom-adapter:<id>`. Recovery uses an
> existing built-in executor or ordinary re-registration of a valid approved
> custom-adapter profile; there is no automatic or versioned fallback.

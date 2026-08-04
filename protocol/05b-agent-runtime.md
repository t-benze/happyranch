# Agent Runtime: Execution, Memory & Lifecycle

How agents are spawned, how they remember across sessions, and when they run.

---

## 1. Agent Execution Model

### Every agent runs as a coding-agent session

Each agent in the organization is not just an LLM call — it's a full coding-agent session that can read files, write files, run commands, search the web, and interact with APIs. The orchestrator layer decides *when* each session runs, *what context* it gets, and *how* outputs flow between them.

### Per-agent executor selection

Agents run through a configured coding-agent CLI. The runtime ships with four
built-in adapter profiles: Claude Code (`claude -p` with `--permission-mode auto`),
Codex (`codex exec --json -`), opencode (`opencode run`), and Pi (`pi -p ... --mode json`).
Any agentic CLI that can accept a prompt argument and produce structured output may
register as a custom executor profile via the machine-global runtime store
(``~/.happyranch/executor_profiles.yaml``) — the runtime validates
argv templates against supported placeholders and builds per-profile subprocess
launches generically (THR-052 seq 6 founder ruling). Registration uses a founder-minted
scoped token and a four-step conformance challenge (workspace_access, loopback_reachable,
cli_callback, emit_envelope). This gives every agent full coding-agent capabilities:
file system access, shell commands, web search, and git operations. Executor selection
is stored in the org agent frontmatter (``AgentDef.executor``), so agents can run on
different executors in the same org.

**Custom CLI result-envelope (THR-107).** Custom CLIs may opt into token metering
by emitting a versioned JSON envelope on stdout, delimited by the sentinel markers
``__HR_ENVELOPE_BEGIN__`` and ``__HR_ENVELOPE_END__``. The daemon parses the
envelope via a generic best-effort parser (``_parse_generic_cli_usage`` in
``runtime/orchestrator/executors.py``).

**D7A strict enforcement (2026-07-27):** new custom-CLI registrations and
re-registrations durably record ``envelope_policy: "strict"`` and enforce
mandatory v1 envelope compliance at the ``GenericCliExecutor`` launch/result
seam. A strict profile whose stdout lacks a valid v1 envelope returns a
deterministic failed ``ExecutorResult`` with actionable re-registration/verification
guidance (tails preserved, forensic accounting intact). Existing stored profiles
without the ``envelope_policy`` field are LEGACY COMPATIBILITY entries with
unchanged optional-envelope behavior. The envelope is **optional for legacy
entries, mandatory for strict entries** — absence preserves legacy behavior
(no token accounting) for legacy profiles; strict profiles fail closed.

The envelope schema maps 1:1 to
the ``TokenUsage`` model (``runtime/models.py:302``) with identical key names.
Token-accounting invariants (``total`` excludes cache reads, nullable tolerance,
model-null backfill to provider label) apply uniformly to envelope-reported tokens.
The full contract is documented in
``docs/superpowers/specs/2026-07-19-custom-cli-adapter-envelope-design.md``.

**Custom adapter contract (THR-107 D7B — custom-adapter profiles).** Custom
executor profiles with ``command_adapter_id: custom-adapter:<id>`` bind to exactly
one registered, conformance-passed, founder-APPROVED custom adapter executable.
The CustomAdapterExecutor launches the adapter as a subprocess, passing a v1
``AdapterInput`` JSON on stdin and parsing a v1 ``AdapterOutput`` JSON from stdout.
The stable v1 contract is defined by the Pydantic models at
``runtime/orchestrator/adapter_contract.py``; the **canonical contract surface
for external consumers** (candidates implementing adapter wrappers) is the
versioned ``GET /api/v1/runtime/adapters/contract-reference`` endpoint
(THR-107 seq184), which returns JSON Schemas generated from those models at
runtime. **THR-107 seq339/340:** the contract-reference response also returns
``canonical_directory`` and ``required_executable_path`` — the daemon-managed
canonical adapter path (<daemon-home>/adapters/<canonical-id>, 0700). Scoped
submissions must create the wrapper at exactly this path; the route and
registration seam independently enforce canonical placement. The
master-bearer ``/register`` route is unchanged. The server-derived schema is canonical. Key invariants:

- **Registration → conformance → founder approval or rejection:** a custom
  adapter executable is registered with its absolute path, SHA-256 hash, version,
  and capabilities; submitted to a bounded stdin/stdout conformance probe; then
  enters **PENDING** and cannot bind to any profile or launch. From PENDING, the
  founder has two exact-snapshot management actions:
  - **PENDING exact-snapshot founder approve:** ``POST /runtime/adapters/{id}/approve``
    atomically validates the six material identity facts (executable, executable_hash,
    version, capabilities, contract_version, workspace_adapter) of the exact durable
    snapshot. Any mismatch, missing adapter, non-PENDING state, or already-approved
    incompatible repeat fails before persistence. **THR-107 seq237:** When the adapter
    has an ``intended_profile_name``, the server atomically approves the snapshot AND
    creates/binds that named custom profile in one transaction — ``eligibility`` becomes
    ``already_bound`` immediately, and no client-side bind follow-up is needed. Adapters
    without an intended profile (master-bearer registration path) are approved without
    auto-binding; they retain explicit advanced Bind recovery via Settings.
  - **PENDING exact-snapshot founder reject/removal:**
  - **PENDING exact-snapshot founder reject/removal:**
    ``POST /runtime/adapters/{id}/reject`` atomically validates the same six
    material identity facts and removes the PENDING durable entry. Rejects stale,
    hash-changed (re-registered), and non-PENDING snapshots without mutation. No
    persisted rejected status — the PENDING entry is removed. No SQLite/schema change.
  - **APPROVED bind (recovery / legacy):** ``POST /runtime/adapters/{id}/bind-profile``
    binds a profile name to the APPROVED adapter. For adapters with a non-null
    ``intended_profile_name``, the caller must supply the exact intended name.
    For adapters without an intended profile (``recovery_ready`` eligibility), the
    founder explicitly provides the desired profile name. After binding, the server
    reports ``eligibility: already_bound`` for that adapter. The durable UI must
    retain and render the adapter as Connected after a fresh render from a server
    ``already_bound`` response — not filter or unmount it. **This route is now
    secondary to atomic approve-and-bind (seq237) for adapters with intended profiles.**
  - **Approved-only removal:** ``DELETE /runtime/adapters/{id}`` removes an
    APPROVED custom adapter with an exact snapshot of all material identity/binding
    facts. Rejects stale, re-registered, wrong-target, and profile-referenced
    snapshots. Preserves approved-only semantics — the reject path above is the
    separate PENDING removal.
- **Exact hash verified at EVERY launch:** before each ``Popen``, the
  ``CustomAdapterExecutor`` re-verifies path type (exists, regular file, executable)
  and SHA-256 against the approved binding. Hash mismatch, removal, non-regular, or
  non-executable → launch fails closed with actionable re-registration/approval error.
  The hash is checked *inside* the per-attempt launch closure, so a throttle retry
  after a rate-limited response re-verifies the artifact before the next Popen.
- **Mandatory AdapterOutput:** custom-adapter profiles require a valid v1
  ``AdapterOutput`` JSON object. Missing, malformed, non-object, unknown-version,
  oversized (>1MB), identity/version/contract mismatch, or success/returncode
  inconsistency → deterministic failed ``ExecutorResult``. The adapter must echo
  the daemon-generated invocation ``session_id`` and match the approved
  ``adapter_version`` and ``contract_version``.
- **Subprocess-only — no Python import/discovery.** Custom adapters run as separate
  subprocesses. The daemon never imports, discovers, or executes third-party Python
  modules from adapter executables.
- **Legacy generic-cli profiles remain readable** and are never auto-mutated.
  The operator path to adopt custom adapters is: register executable → conformance
  → PENDING → founder exact-snapshot approve (or reject) → APPROVED +
  atomic profile bind for intended adapters (``already_bound``) OR
  advanced Bind recovery for no-intended adapters (``recovery_ready``)
  → re-register → launch/verify. PENDING rejection atomically removes the entry
  re-register → launch/verify. PENDING rejection atomically removes the entry
  with no persisted rejected status. Approved-only removal is a separate
  DELETE path for APPROVED adapters. Rollback: re-register the profile as
  ``generic-cli`` or revert the deployment.
- **D5 baseline-only posture:** the custom adapter contract introduces no allow-rule,
  sandbox, network-access, filesystem-access, or permission changes.
- **THR-107 seq244 dependency manifest:** new adapter registrations require
  ``dependency_manifest_version: 1`` with a non-empty list of declared child
  executable dependencies (absolute path + SHA-256). Dependencies are validated
  at registration and re-verified before every launch. Manifest-adapters are
  explicitly absolute and hash-pinned/revalidated with no executor fallback to
  ambient PATH; the adapter process inherits normalized PATH for normal
  callback/utility availability.
  ``token_metering`` capability enforces truthful non-null token_usage at
  conformance. Legacy entries without the manifest are preserved unchanged.

The signed architecture is at
``docs/superpowers/specs/2026-07-24-unified-adapter-runtime-architecture.md``.

Each agent's configuration specifies context and workspace:

```
agent_config:
  dev_agent:
    executor: claude
    system_prompt: 03-system-prompts-workers.md#dev-agent
    workspace: workspaces/dev_agent/
    context_files:
      - 01-org-charter.md
      - knowledge_base/technical/
      - agent_memory/dev_agent/memory/
    permission_mode: auto
```

### Context injection via executor bootstrap docs

The orchestrator assembles each agent's context into an executor-specific bootstrap file placed in the workspace root. Claude workspaces use `CLAUDE.md`; Codex, opencode, and Pi workspaces use `AGENTS.md`. This file is regenerated at the start of every session. It includes:
- Agent system prompt (role, accountability contract)
- Relevant org charter sections
- Pointer to the agent's persistent memory store
- Task-specific brief (the actual assignment)

### Permission enforcement and callbacks

Claude workspaces have a `.claude/settings.json` that configures Claude Code's auto-allowed tools. Codex, opencode, and Pi workspaces do not use that file. Across executors, agents call back through the same single-line `happyranch ... --from-file` contract. Agents can read, write, and execute freely within their workspace and the cloned codebase, subject to the executor's sandbox mode and the orchestrator's workflow rules. Pi has no HappyRanch-managed sandbox or permission file in this integration.

### Skill materialization at session spawn

Skills — structured guidance packages that tell an agent how to perform specific
operations — are materialized into the agent's workspace on every session spawn
by `materialize_workspace_skills` (`workspace_adapters.py`). This runs on all
spawn contexts (task, thread, wake, dream, schedule, bootstrap, executor-switch).

#### Canonical skill store + workspace symlinks (macOS-only)

As of TASK-4009/TASK-4012, skill materialization uses a **canonical skill store**
outside executor workspaces. Skills are built once into hash-addressed packages
and workspace entries are **validated relative symlinks** to exact approved
package versions under both `.claude/skills` and `.agents/skills` roots
(including Codex, Opencode, Pi, and mapped custom profiles).

**Supported platform:** macOS (darwin) only. Linux and Windows explicitly fail
closed before launch/materialization with a named `PlatformIsolationError`.

**Delivery model (same-owner):**

The executor and daemon share the same OS identity on macOS. Linked,
validated relative skill links live under BOTH ``.claude/skills`` and
``.agents/skills`` (including Codex, Opencode, Pi, and mapped custom
profiles). Every user-facing and executor-facing guidance surface names
both roots, never only the provider-selected root. Guidance is operational,
not a technical security boundary.

The executor runs under the SAME OS identity as the daemon — there is NO
OS-level isolation. An agent-controlled executor process can read, write,
chmod, or chown the canonical skill store and anything else the daemon
account can reach. A same UID may mutate, race validation, and affect
active/overlapping sessions. Integrity checks are DETECTION-ONLY,
FAIL-CLOSED behavior, not prevention. Do NOT call the target immutable,
protected, trusted source, or claim write/chmod/ACL denial, a security
boundary, or cross-agent isolation.

**Detection and refusal:** Before every executor launch (and at retry-time
before Popen/retry), every resolved package member's artifact bytes are
validated against the ledger-declared SHA-256 hashes. Both ``.claude/skills``
and ``.agents/skills`` root links are validated. A mismatched existing
canonical package is NEVER automatically rebuilt, copied, replaced, or healed
from same-UID local source. On mismatch or malformed/broken/malicious link,
malformed/broken/malicious link, or event-persistence failure, the daemon
emits a durable visible integrity event and refuses the session before
Popen/retry. First-ever materialization of an absent package remains
allowed; valid existing packages may be reused.

**Manual recovery only:** (a) For broken links: ``happyranch set-executor
<agent> --executor <current-executor>`` (re-materializes links only, NEVER
recovers corrupted bytes). (b) For corrupted canonical bytes:
``happyranch skills recover <slug> <version> <content_hash>`` — the sole
operator-invoked recovery path. Validates ledger provenance and every
declared member SHA-256 hash against the ArtifactStore before deletion;
refuses already-valid targets. The next materialization will rebuild the
package from the ArtifactStore. No automatic repair from same-UID local
source. This command can ONLY be used after an authoritative external
re-sync/redeploy of the release or custom artifacts has restored verified
artifact bytes outside the compromised same-owner local source — the
recovery route validates against ArtifactStore, which may itself be
corrupted if the same-UID executor previously tampered with it.

Policy withdrawal and atomic link repair remain safe.

**Ownership and provenance:**
- Canonical packages are content-addressed trees from exact verified
provenance/members for system, release-managed, and lifecycle
version-pinned packages.
- The readonly hardening is cosmetic — the executor shares the daemon's uid
and can chmod files back to writable. Do not describe byte targets, local
sources, ArtifactStore, or links as OS-immutable, ACL-protected, trusted,
executor-only writable/unwritable, or automatically recovered.

**Integrity verification:**
Before each executor launch, the daemon compares actual canonical package
content against the ledger-declared member hashes:
- System-contract packages: compared against the shipped source tree hash.
- Lifecycle skills: each member's actual hash compared against the
  ArtifactStore manifest.
On mismatch the daemon emits a durable integrity/operations event and
refuses the session. Corrupted bytes are NEVER silently accepted as valid
and NEVER automatically rebuilt, copied, or healed from same-UID local
source. The ArtifactStore is NOT a trusted or immutable source — a
same-UID process may also tamper with artifact bytes. This is
detection-only with fail-closed refusal; it is NOT an attacker-independent
external attestation authority.

**Isolation contract (macOS):**
- The executor launches directly under the daemon's identity. The prompt
guard directs agents not to edit managed skill links and states that
same-owner enforcement is not a security boundary.
- Canonical store ownership and permissions are verified before every launch.
- Ordinary directories, malicious/broken/external/wrong-version links, unsafe
targets, failed permission check, or repair errors fail closed and prevent
launch. Never recursively delete or follow attacker nodes.

**Mode observability:** `PlatformIsolation.is_same_owner_mode` (bool property)
makes the selected mode observable/auditable at runtime without an auth or
schema change. The mode is recorded in daemon logs at startup.

**Link validation and repair:**
- Materialized links are validated relative symlinks resolving inside the
canonical store. Stale, broken, wrong-version, non-symlink, external, or
mismatched-hash entries are atomically repaired.
- Withdrawal removes only owned validated links, retains canonical packages.
- The full expected union is derived once per provider root so system contracts
are never withdrawn by managed/lifecycle-only reconciliation.

**Legacy compatibility fallback:** The legacy per-session copy model
(``_copy_skills_tree``, ``refresh_session_skills``, and the former
``_WHOLESALE_DUMP_ENABLED`` flag) is removed as an executable path. No
catch-and-copy or silent fallback survives. The canonical store + symlink
model is the sole production materialization path.

**Org context:** `{ORG_SLUG}` placeholders in canonical skill bodies are NOT
substituted. The org slug is passed to the child process via
`HAPPYRANCH_ORG_SLUG` environment variable from the authorized session/task
metadata. Existing multi-org commands receive a real existing-org slug.

**Release-shipped managed-catalog skills.** Bundled skills ship inside the
repo at `<project_root>/runtime/skills/<slug>/` and are read-only at runtime.
These are resolved via the `SkillRegistry` and unioned with system contracts;
release and system-contract slugs win on collision.

**Lifecycle-ledger custom skills (THR-055).** User-authored/operator-authored
custom skills are governed exclusively by the immutable lifecycle ledger
(`skill_lifecycle_packages`, `skill_lifecycle_assignments`). Only PUBLISHED
skills with an active version-pinned assignment for the target agent are
materialized. Proposed, draft, validated, approved-but-unpublished, rolled_back,
retired, and legacy-quarantined content never reaches the workspace.

**Legacy quarantine.** The pre-THR-055 per-org user-authored filesystem store
(`<org_root>/skills/`) is retired and quarantined. During migration, legacy
SKILL.md content is copied to the org ArtifactStore under
`skill-lifecycle/legacy/<slug>/<hash>/SKILL.md` for retention; the
ledger stores only the artifact reference key, never the mutable filesystem path.
Quarantined content is never resolved by `inject_managed_skills`.

**Content retention (task-artifact policy).** Lifecycle proposal content
is stored in the org ArtifactStore under content-addressed keys:
- ``skill-lifecycle/<slug>/<hash[:16]>/SKILL.md`` — SKILL.md
- ``skill-lifecycle/<slug>/<hash[:16]>/references/<name>`` — each reference
- ``skill-lifecycle/<slug>/<hash[:16]>/assets/<name>`` — each asset
- ``skill-lifecycle/<slug>/<manifest_hash[:16]>/manifest.json`` — canonical manifest

The manifest is a JSON document listing every package member with its
normalized relative path, SHA-256 hash, artifact key, and size in bytes.
The package-version ``content_hash`` in the lifecycle ledger is the SHA-256
of the manifest (binding full-package provenance, distinct from individual
member hashes). The ``content_artifact_key`` points to the manifest artifact.

The ledger tables store only immutable metadata (hash, version, provenance);
the artifact store holds the sole canonical copy of every package byte.
Materialization loads the manifest from the ArtifactStore, validates each
member's hash byte-for-byte, and writes the complete directory tree
(SKILL.md + references/ + assets/) fail-closed into the target workspace.
Legacy single-SKILL.md artifacts (pre-manifest format) are still supported
by the materializer as backward compatibility.

**Failure-atomic persistence.** All ledger writes (package row insert +
lifecycle event insert) execute inside an explicit ``BEGIN IMMEDIATE`` /
``COMMIT`` transaction. Any SQLite failure rolls back both rows atomically.
Artifacts newly created during the request are cleaned up on ledger failure
(compensation); pre-existing artifacts from content-addressed deduplication
are never deleted. An ArtifactStore write failure before any ledger row
aborts without any side effects.

**Session-bound authority.** Agent proposal submission requires verified
task/session binding via the SessionTracker. A single agent-only path exists,
plus a human-only legacy route:

- **Opaque session path (agent CLI).** The agent commands
  ``happyranch skills propose --from-file <proposal.json> --session-id <session-id> [--org <slug>]``.
  The CLI builds a token-free transport (no bearer token read or sent) using
  only the daemon port. Org is resolved via the established
  ``resolve_org_slug(args_org=, available=)`` convention. The CLI sends the
  opaque session ID to
  ``POST /api/v1/orgs/{slug}/skill-lifecycle/proposals/agent`` — an agent-only
  route that does NOT accept the master bearer token. The server independently
  derives all four identity dimensions (org_slug, task_id, agent_name,
  active session_id) from the SessionTracker's additive context index
  (``get_context_by_session()``) — never from body/query/env/client claims,
  task lookup by agent, team membership, or YAML eligibility. Path-selected
  org is cross-checked against the session's org; cross-org and mismatched
  contexts are denied with 403. The server rejects the **presence** of every client-controlled trusted
  identity/authority field in the direct HTTP body — ``task_id``,
  ``session_id``, ``proposer_agent``, ``org``, ``org_slug``, ``agent``,
  ``agent_name``, ``actor``, ``eligibility``, ``permission``, and
  ``permissions`` — before request-model parsing, session lookup,
  policy checks, or any persistence. Presence includes empty values.
  Rejection returns exact HTTP 403 with error code
  ``body_identity_rejected``; no lifecycle package, event,
  materialization, or ArtifactStore residue is produced. This is the
  sole agent
  authoring workflow — there is no alternate agent-capable path.

- **Legacy route (human/founder only).** ``POST /skill-lifecycle/proposals``
  is restricted to bearer-authenticated human/founder callers. Non-bearer
  (agent) callers receive 403 directing them to the dedicated
  ``/proposals/agent`` endpoint. The legacy dual-auth bypass has been closed.

All identity derives exclusively from the server's verified context.

**Agent-id × canonical-slug pilot policy (THR-055 seq 127 corrective).** The
agent-only route enforces a fixed server-side policy BEFORE any artifact
creation or ledger write. The policy does NOT inspect team membership,
prompts, org config/YAML eligibility, request metadata, or body identity
claims:

| Agent | Allowed slug |
| --- | --- |
| ``frontend_engineer`` | ``frontend-development`` |
| ``product_lead`` | ``product-manager-prd`` (lowercase) |

Every other agent is denied (403). Either permitted agent with the wrong slug
is denied (403). Human/founder lifecycle authority (claim, draft, edit, validate,
submit-review, review, publish, assign, retire, rollback, all eligibility/
permission/config mutations) remains unchanged and returns 403 for agent
invocations. Proposals remain immutable and task/session-provenanced,
``standard_operational`` only, with content excluded from catalog/effective
resolution/materialization until founder publication.

Human/founder lifecycle mutations (claim, validate, review, publish, assign,
rollback, retire) require the master bearer token and are gated behind
bearer-only routes with no agent path. Agent callers receive server-side
403 for all lifecycle mutations other than their own active pilot
task/session-bound proposal submission.

**FAIL-CLOSED materialization.** Any error during materialization raises
immediately. A failed materialization must NOT leave a partially-populated
skills directory passing as complete. All five caller contexts (orchestrator
`run_step`, `thread_runner`, `wake_runner`, `dream_runner`, `schedule_runner`)
persist a database-terminal failure and return BEFORE executor spawn — a
materialization error in any spawn path blocks the agent launch, never silently
skipped.

**Process-local workspace serialization (Issue #536).** All pre-spawn skill
materialization for a given agent workspace — system-contract injection +
on-disk verification, managed-skill injection, and lifecycle-ledger injection
— runs inside a single unified transaction (``materialize_workspace_skills``)
protected by a process-local ``threading.RLock`` (re-entrant lock) keyed by
the canonical (resolved) workspace path. The legacy wholesale copy
(``_WHOLESALE_DUMP_ENABLED`` / ``refresh_session_skills``) is permanently
removed. Concurrent task, thread, wake, dream, schedule, and
executor-switch/bootstrap callers targeting the same workspace serialize
their complete pre-spawn materialization. The three executor adapter
``_copy_skills`` methods (Claude, Codex, Opencode) and the set-executor
route's all-context materialization also participate in this lock boundary.
The lock is **process-local only** — it does not coordinate across daemon
processes. Cross-process protection for the same agent workspace relies on
the daemon's per-agent concurrency ceiling (at most one ``run_step`` session
plus one thread invocation per agent).

The RLock allows safe re-entrant use: when the executor-switch route
acquires the lock and calls ``ensure_workspace_ready``, the adapter's
``_copy_skills`` can re-acquire the same lock without deadlocking.

Per-file ``os.replace`` reader safety is preserved: a concurrent reader
(or an agent session already running in the workspace) always sees either
the complete old or complete new skill file, never a half-written one.
The lock serializes writers only; it does NOT block readers.

Named fail-closed behavior: if materialization fails for a real filesystem
error (disk full, permission denied, missing source), the error propagates
as a named exception (``SystemContractMaterializationError``,
``LifecycleMaterializationError``, or the underlying ``OSError``) — never
a bare ``FileNotFoundError``. The caller persists the terminal failure and
no agent subprocess is launched. Recovery requires fixing the underlying
filesystem/permission issue and explicitly re-dispatching.

**Atomic emergency rollback.** The `POST /skill-lifecycle/rollback` handler wraps
package status change, assignment deactivation, and event insertion in an explicit
`BEGIN IMMEDIATE`/`COMMIT` transaction — all three mutations roll back together
on failure. Workspace residue is cleaned up on the next spawn by fail-closed
materialization.

**Visibility only — NO capability change.** Skills govern which guidance
playbooks an agent sees. They grant no tools, credentials, network access,
filesystem access, sandbox policy, or permission-map/allow-rule/auth changes.

**Only founder-concern boundaries are restricted** (as defined in the org charter):
- No `git push` to `main` / production deploy
- No actions involving spend >$200 single or >$100/month recurring
- No raw payment card data storage (PCI-DSS)
- No publishing content touching political sensitivity

These guardrails are enforced by the agent's system prompt (in `CLAUDE.md` or `AGENTS.md`) and the orchestrator's post-session review — not by provider-specific deny rules. If an agent violates a founder-concern boundary, the orchestrator catches it and escalates.

### Full codebase access

All agents can clone the project's git repo into their workspace for read access to the full codebase. The orchestrator handles the initial `git clone` (or `git pull` if already cloned) at session start so the agent always has fresh code. Agents can also pull on their own during a session.

Write restrictions are role-based but minimal:
- Dev Agent: can create branches, commit, push to feature branches (not main)
- Payment Agent: can create branches within `src/payments/**`, push to feature branches
- Product Manager: writes specs to workspace, no code commits
- Engineering Head: reviews only, no direct code changes

### Task attachment materialization at session spawn (THR-109)

When a task (or an ancestor it inherits from) has file attachments, the runtime
resolves them at session spawn by walking up the `parent_task_id` chain, unioning
any `task_attachments` rows found. The durable bytes are read from the private
task-attachment store (separate from the org-wide shared artifact store) and
written into a per-task session attachment directory under the agent's workspace
(`workspace/.happyranch/attachments/<session_id>/`). An `Attachments:` block is
injected into the brief prompt naming each file, its on-disk path, size, and
content-type hint. Delivery is by-path for all executors; image perception
depends on the executor CLI's own abilities. The materialized per-session
directory is a regenerable cache — the bytes of record live in the task-attachment
private store.

**Legacy rows.** Rows with non-`NULL` `legacy_status` (e.g. `duplicate_v1`) are
included in ancestor resolution and materialization. The collision-safe
materialized filename (`{storage_key}__{sanitized_display_name}__{id}`) uses
the immutable `task_attachments.id` row identity to produce distinct per-row
paths — legitimate duplicate legacy attachments do not overwrite each other
even when they share both `storage_key` and `display_name`.

### Executor abstraction

The executor interface supports multiple backends. Four built-in adapters are
provided; additional agentic CLIs can be registered as custom profiles via org
configuration (THR-052). Swapping an agent from one executor to another is a
one-line config change in `agent.yaml`.

**Profile identity (D6, THR-107 seq115).** Each registered executor profile
carries two canonical identity fields:
- ``workspace_adapter_id`` — selects workspace preparation (bootstrap file,
  permission surface). One of ``claude``/``codex``/``opencode``/``pi``.
- ``command_adapter_id`` — selects the command execution adapter (argv
  construction and output parsing). For built-in profiles this matches
  ``workspace_adapter_id`` (each carries its own first-party adapter); for
  custom profiles this may be ``"generic-cli"`` (template-based generic CLI)
  or ``"custom-adapter:<id>"`` (bound to a separately registered,
  founder-approved, hash-verified custom adapter executable — D7B,
  subprocess-only, mandatory v1 AdapterInput/AdapterOutput, D5 baseline-only
  posture).

Legacy fields ``adapter_id``/``adapter`` (deprecated alias for
``workspace_adapter_id``) and ``command_adapter`` (deprecated alias for
``command_adapter_id``) remain for read compatibility. The canonical
fields are the preferred surface for all consumers. See the
unified adapter-runtime architecture spec (§6.3) for dual-read,
conflict-detection, and no-auto-mutation guarantees.

### Executor binary-path resolution (THR-085 / THR-107 seq155)

Built-in and generic-CLI custom executor profiles require a valid explicit
machine-local binary registry entry before launch. Custom-adapter profiles
(``command_adapter_id: custom-adapter:<id>``) are an exception — they use
the exact founder-APPROVED, hash-verified absolute adapter executable as their
launch artifact and do **not** require a separate ``executors.json`` record
keyed by the profile name. At spawn time, each
executor's CLI binary is resolved as follows:

1. **Machine-local registry** — for non-custom-adapter profiles, consult the
   per-host binary-path registry at
   `<daemon-home>/executors.json`. The executor name (e.g. `claude`) is the sole
   resolution key (THR-107 seq155 hard no-PATH cutover).  If the name is
   registered, validate the stored path: it must exist and be executable.
   - **Valid** → use the stored path.
   - **Invalid (stale path)** → raise an **actionable block** that names the
     kind, the stale path, and the fix (`happyranch executor-binaries register <kind> --path <absolute-path>`). No silent
     fallback to PATH.
2. **Not registered** → raise an **actionable block** naming the kind and
   the fix (`happyranch executor-binaries register <kind> --path <absolute-path>`).
   **Never discover, resolve, or auto-pin a PATH executable** (THR-107 seq155).

**Custom-adapter profiles** resolve the adapter executable directly from
the approved adapter entry — its absolute path, SHA-256 hash, version, and
contract version are verified at construction time and re-verified before
each ``Popen``. Missing, tampered, non-regular, or non-executable adapters
fail closed.

Absolute `cli_path` values in Settings are never used as a bypass — only
an explicit ``executors.json`` entry keyed by the executor name (or, for
custom-adapter profiles, the approved adapter entry) permits launch.
This applies to all four built-ins (claude, codex, opencode, pi), generic-CLI
custom profiles, and custom-adapter-backed profiles.

The actionble block is an `ExecutorBinaryBlocked` exception (subclass of
`RuntimeError`). It always names the specific executor kind and gives the
operator a concrete command to fix it — never an opaque `rc=143` or bare
ENOENT death.

**Why a separate `executors.json` file?** The binary-path registry is
machine-local and must be writable at runtime by the `/api/v1/executor-binaries/register`
route (master-bearer-authed, for manual operator use) and by
`/api/v1/executors/runtime/register-binary` (scoped-token loopback, for
built-in agentic CLI self-registration — THR-088). Keeping it in a dedicated file under `<daemon-home>` isolates runtime
writes from `config.yaml` (which holds Settings values that may be under
version control or shared across hosts). This is distinct from the THR-052
executor profile registry (`org/config.yaml`), which describes *which*
executor kinds and capabilities exist and is org-portable.

### Bundled CLI PATH resolution (THR-085)

When the daemon is running as a PyInstaller-frozen bundle inside the Mac app,
the bundled `happyranch` CLI binary sits alongside `happyranch-daemon` inside
`Contents/Resources/daemon/`. The daemon MUST ensure that bare-name
`happyranch` invocations by agentic executors resolve to this bundled binary
— not to a stale `~/.local/bin/happyranch` left over from a previous install.

**Detection mechanism.** The ONLY signal available to the Python daemon is
PyInstaller's canonical frozen-detection flag: `getattr(sys, 'frozen', False)`
is `True` when running as the frozen bundle. (The Swift-side
`PACKAGING_MODE=bundled` environment variable is deliberately stripped by
`EnvironmentSanitizer` before the daemon child process launches, so the
Python daemon never sees it.) When frozen, `sys.executable` is the bundled
`happyranch-daemon` at `Contents/Resources/daemon/happyranch-daemon`, so
`os.path.dirname(sys.executable)` is the directory that also contains the
bundled `happyranch` CLI.

**Resolution rule.** At daemon startup, during PATH normalization:

- **Frozen (bundled Mac app):** Prepend `os.path.dirname(sys.executable)`
  (the bundled CLI directory) at the very front of the executor child's PATH,
  *before* the standard tool directories (`/opt/homebrew/bin`,
  `/usr/local/bin`, `~/.local/bin`). This ensures bare-name `happyranch`
  resolves to the bundled binary and beats any stale `~/.local/bin/happyranch`.
  The prepend is idempotent — repeated normalization does not duplicate the
  directory.
- **Not frozen (dev/headless/CI):** No change. The bundled CLI directory is
  NOT injected. PATH resolution stays exactly as today — the existing PATH
  `happyranch` (e.g. from `~/.local/bin` in `_STANDARD_TOOL_DIRS`) wins.

Because `_callee_env()` copies `os.environ` for child subprocesses, every
executor spawn inherits the normalized PATH with the bundled directory
leading when frozen.

### Spawn-Environment Invariant

Every runtime-created child subprocess — agent executor sessions (through
``_callee_env()`` in ``runtime/orchestrator/executors.py``), custom-adapter
launches, and job-script subprocesses (through ``_sanitize_child_env()`` in
``runtime/daemon/jobs_runner.py``) — inherits a sanitized copy of the daemon's
environment that **strips** the following variables:

- ``VIRTUAL_ENV`` — standard venv activation marker; its presence directs
  ``pip``, ``uv``, and other Python tooling to install into the venv.
- ``UV_PROJECT_ENVIRONMENT`` — uv project environment target override; can
  redirect ``uv sync`` / ``uv pip install`` away from the default ``.venv``.
- ``UV_PYTHON`` — uv ``--python`` selector: the interpreter into which
  packages are installed; can steer installation into the shared venv.
- ``UV_SYSTEM_PYTHON`` — uv ``--system`` flag: installs into the system
  Python environment instead of a managed venv.

These variables are stripped because the daemon process itself typically runs
inside the shared canonical HappyRanch venv.  If an agent executor or job
script inherits ``VIRTUAL_ENV``, a bare ``uv sync`` or ``uv pip install -e .``
executed from a **disposable worktree** would rewrite the shared venv's
editable-install ``.pth`` file to point at the worktree instead of the
canonical source checkout.  When the worktree is removed, every agent using
that venv loses the ability to import the ``cli`` and ``runtime`` packages.

**Preserved variables:** ``PATH`` (including the daemon-normalized standard
tool directories), ``HAPPYRANCH_ORG_SLUG``, and all other ``HAPPYRANCH_*``
runtime variables.  No unrelated configuration is blanket-removed.

#### Worktree Rule (hard)

**Never run** ``pip install -e .``, ``uv pip install -e .``, or
``uv sync --active`` from inside a per-task worktree when the inherited
environment carries the shared canonical venv.  These commands rewrite the
shared ``.pth`` entry and break every agent using that venv.

Instead, create an **isolated worktree-local venv** before installing:

```bash
python3 -m venv .venv-local
source .venv-local/bin/activate
uv pip install -e .
```

#### Recovery (secondary)

If a stale ``.pth`` has already broken the CLI, prefix every invocation with
the canonical source checkout on ``PYTHONPATH``:

```bash
PYTHONPATH=/path/to/canonical/happyranch happyranch <args>
```

This is a non-destructive workaround — it does not modify the ``.pth`` file
or run ``pip``/``uv``.  Use it for one-off recovery; the permanent fix is to
restore the editable install from the canonical checkout.

The ``happyranch doctor`` command (local, read-only, no daemon required) checks
whether the editable-install pointer resolves to the canonical source and emits
the exact repair command on failure.

---

## 2. Agent Memory Architecture

### Problem
Coding-agent sessions are stateless — context is lost when a session ends. Agents need to remember past work and learn from experience across sessions.

### Solution: persistent workspaces with file-based memory

Every agent has a **persistent workspace directory** that survives across sessions. The workspace contains the agent's memory files, any work products it creates (specs, code, proposals), and a cloned copy of the project repo. The orchestrator regenerates the executor bootstrap file (`CLAUDE.md` or `AGENTS.md`) and Claude settings when applicable at session start, but everything else persists.

```
workspaces/
├── engineering_head/
│   ├── agent.yaml               # Includes executor + repos
│   ├── CLAUDE.md or AGENTS.md   # Regenerated each session
│   ├── .claude/settings.json    # Claude-only permission config
│   ├── memory/                  # Per-entry store, persists across sessions (was learnings.md; LRN- ids resolve via permanent shim)
│   ├── task_history.md          # Rolling summary of last N tasks
│   └── repo/                    # Git clone of project (pulled at session start)
├── product_manager/
│   ├── CLAUDE.md
│   ├── .claude/settings.json
│   ├── memory/
│   ├── task_history.md
│   ├── specs/                   # Specs PM writes accumulate here
│   └── repo/
├── dev_agent/
│   ├── CLAUDE.md
│   ├── .claude/settings.json
│   ├── memory/
│   ├── task_history.md
│   └── repo/                    # Agent works on branches here
├── payment_agent/
│   ├── CLAUDE.md
│   ├── .claude/settings.json
│   ├── memory/
│   ├── task_history.md
│   ├── proposals/               # Payment change proposals
│   └── repo/
└── ...
```

### Three layers of memory

**1. Institutional memory (knowledge base)**
Shared across all agents. Org charter, SOPs, brand guidelines, partner directory, regulatory summaries. Read-only for most agents, write access scoped per role.

**2. Agent-specific memory (memory store)**
Each agent accumulates its own operational learnings. The Content QA records "DSAL website is more reliable than MGTO for Macau visa info." The Content Writer records "always show Octopus + AlipayHK side-by-side on HK transport guides — tourists usually only know one." These files persist across sessions and are loaded as context at session start.

After each task, the orchestrator prompts the agent: "Based on this task, are there any new memory entries to record?" Responses are appended to the memory store. Over time, when the store gets long, the orchestrator periodically asks the agent to consolidate and prune it.

Entries are addressed as `MEM-NNN`. Items migrated from the prior learnings store keep a permanent `LRN-NNN` alias so historical cross-references resolve forever. The audit trail is forward-only: new events log as `log_memory_*`; historical `log_learning_*` rows are never rewritten.

**3. ~~Performance memory~~ (REMOVED 2026-05-27)**
The 30-day rolling scorecard / tier classification was removed. The audit log (implicit `review_verdict` rows after every delegated child terminates, plus completion / failure events) is sufficient for the founder to identify which agents need attention — via `happyranch audit`. The legacy `scorecards` table is no longer created on fresh DBs.

### How context gets assembled at session start

The orchestrator regenerates the bootstrap document in the agent's workspace with:

```
1. System prompt (from 02/03-system-prompts-*.md)
2. Org charter summary (from 01-org-charter.md — key sections only)
3. Pointers to persistent files (memory/, task_history.md)
4. Team health summary (generated by orchestrator)
5. Task-specific context (brief, prior drafts, QA feedback, etc.)
```

The agent's persistent files (memory entries, prior work products) are already in the workspace — the bootstrap document just references them. The orchestrator also runs `git pull` on the repo clone to ensure fresh code.

### Write-back protocol

After each session completes, the orchestrator:
1. Extracts the completion report (`completion_report.json` written by the agent)
2. Checks for new memory entries and appends to the memory store
3. Writes an implicit `review_verdict` audit row for delegated work (approved / rejected) so the founder can audit per-agent outcomes via `happyranch audit`
4. Appends to `recent_tasks.md` with a summary of the task
5. Logs everything to the audit trail (SQLite)
6. Does NOT clean up the workspace — files persist for future sessions

---

## 3. Agent Lifecycle and Scheduling

### Principle: agents are not always running
Agents are not persistent processes. Running 12 agent sessions continuously would burn LLM credits and produce nothing — most agents are idle most of the time. Instead, the orchestrator manages agent lifecycles: spinning up sessions when there's work, and tearing them down when the task is done.

### Three operating modes

#### Mode 1: On-demand (most agents, most tasks)
The orchestrator spins up an agent session only when a task is assigned. The session starts, the agent completes the task, submits its completion report, and the session ends. Between tasks, the agent does not exist as a running process.

**Lifecycle:**
```
Task arrives in queue
    │
    ▼
Orchestrator assembles context (system prompt, memory, task brief)
    │
    ▼
Orchestrator spawns agent session (via configured executor)
    │
    ▼
Agent works on task (minutes, not hours)
    │
    ▼
Agent submits completion report
    │
    ▼
Orchestrator extracts output, logs results, writes back memory
    │
    ▼
Session terminates — agent no longer running
```

**Typical session duration:** 1-5 minutes for most tasks. Complex tasks (Dev Agent implementing a feature, Compliance Agent running a full audit) may take 10-30 minutes.

**Which agents use this mode:** Content Writer, Content QA, SEO Agent, Dev Agent, Payment Agent, QA Engineer, Partner Liaison, Compliance Agent, and all 4 Manager Agents for their review/approval tasks.

#### Mode 2: Scheduled (recurring tasks on a cron)
Some work happens on a fixed schedule. The orchestrator's scheduler triggers these sessions at configured times. The session runs, completes its task, and shuts down — same as on-demand, but the trigger is a clock instead of a task queue.

**Scheduled tasks:**

| Schedule | Agent | Task |
|---|---|---|
| Daily 9:00 AM | Content Manager | Generate and send daily report to founder |
| Daily 9:15 AM | Product Manager | Generate and send daily report |
| Daily 9:30 AM | Ops Manager | Generate and send daily report |
| Daily 9:45 AM | CX Manager | Generate and send daily report |
| Every Friday | Content QA | Content freshness audit — flag guides older than 90 days |
| Every Monday | SEO Agent | Weekly keyword ranking report |
| 1st of month | Compliance Agent | Monthly regulatory scan across 3 jurisdictions |
| 1st of month | Ops Manager | Monthly partner SLA compliance review |
| Weekly Monday 10:00 AM | Orchestrator (not an agent) | Generate and post weekly org summary to the dashboard |

Each scheduled task is configured in the orchestrator's scheduler (a cron-like system). Missed runs (e.g., Mac Mini was off) are handled by a catch-up mechanism: on startup, the orchestrator checks for missed scheduled tasks and runs them.

#### Agent Todos (THR-105): agent-owned scheduled work

Agent Todos are persistent Schedule records stored in the ``schedules`` SQLite
table. Each agent may own up to 20 armed schedules; the org cap is 100. Every
Schedule carries a ``normalized_brief`` (what fires) and a ``source_instruction``
(the natural-language instruction the manager originally provided, preserved
for audit/reconciliation).

**Kinds.** Two kinds are supported:

- **One-shot** — fires exactly once at a specified UTC ``fire_at`` (max 90 days
  out), then transitions to ``fired`` (terminal).
- **Weekly** — fires every week on a single weekday + HH:MM local time + timezone.
  After each fire the schedule re-arms with the next occurrence and continues
  until either the founder cancels/pauses it or it reaches its ``expires_at``
  (default 90 days from creation). Indefinite weekly schedules (``indefinite=1``,
  founder-set only) have no expiry.

**Fire mechanism.** The schedule fire is a two-stage pipeline:

1. **Scheduler (daemon loop).** A 60-second tick scans all orgs for ARMED
   Schedule rows whose ``fire_at <= now`` (one-shot) or ``fire_at`` is within a
   120-second tolerance window (weekly). For weekly rows whose ``fire_at`` is
   stale (missed during daemon downtime), the scheduler advances ``fire_at`` to
   the next weekly occurrence or expires the schedule — **no replay/backfill**
   of missed occurrences. A claimed row transitions from ARMED → FIRING.

2. **Runner + spawn callback.** The schedule worker loop drains the
   ``ScheduleQueue`` and invokes the owning agent's executor with a dedicated
   schedule-fire prompt. The agent's single job is to call the
   ``happyranch schedules spawn`` callback exactly once. The spawn callback:

   - Accepts only FIRING Schedule rows (single-use, record-scoped guard).
   - Creates one root task from the stored ``normalized_brief``, targeted to the
     owning agent on its own team.
   - Records ``spawned_task_ids`` and increments ``fire_count``.
   - Resolves the terminal state: one-shot → FIRED (terminal); weekly → re-armed
     with the next ``fire_at``, or EXPIRED if the next occurrence exceeds
     ``expires_at`` and ``indefinite=0``.
   - Writes ``schedule_spawned`` and ``schedule_completed`` audit log rows.
   - Enqueues the spawned task.

**Token usage.** Token usage for the schedule-fire executor session is recorded
under ``scope_type="schedule"`` and ``scope_id=<SCHEDULE-NNN>``, keeping it
separate from task-scoped token usage.

**Constraints.**

- The schedule's ``normalized_brief`` is the brief for the spawned root task —
  the schedule payload cannot choose the agent, team, or brief.
- Every Schedule targets a single agent on its own team (self-targeted).
- Cross-agent scheduling is not supported.
- Hidden / invisible schedules (not visible in the CLI ``list`` output) are
  not supported — every Schedule is visible to its owning agent.
- Weekly schedules never replay/backfill missed occurrences. A daemon restart
  after a missed slot advances the schedule to the next occurrence without
  enqueuing a fire job for the stale slot.
- The spawn callback is the only fire path — no alternate trigger mechanisms
  exist.

**Arming (creating) schedules.** Agents create new schedules by calling the
``happyranch schedules create`` callback — a single-line invocation that POSTs
to ``/api/v1/orgs/{slug}/schedules``:

   happyranch schedules create --org <slug> --from-file <path>

The payload file is a JSON object with ``task_id``, ``session_id``, ``agent``,
``source_instruction``, ``normalized_brief``, ``kind``, ``fire_at``, and
optionally ``recurrence`` and ``timezone``.  The server enforces:

- **Self-target only:** the creating agent is resolved server-side from the
  active session (``task_id`` + ``session_id`` + ``agent`` validated against
  the in-memory SessionTracker).  The payload cannot choose another agent.
- **Explicit instruction only:** both ``source_instruction`` (the verbatim
  NL instruction) and ``normalized_brief`` (the structured normalized brief)
  are mandatory.  Natural-language-only arming (without normalization)
  is refused.
- **Capability gate (default-deny):** the agent must be listed in
  ``scheduling.enabled_agents`` in ``org/config.yaml``.  Omission, empty
  list, and missing key all reject with 409 ``scheduling_disabled``.
- **Caps and defaults:** the 20-per-agent / 100-org-wide armed caps, 90-day
  one-shot horizon, weekly shape validation (single weekday + HH:MM + IANA
  timezone only), and 90-day recurring expiry are enforced at create time
  by the ``ScheduleService``.

Arming is fully autonomous — no pre-arming founder approval step — but the
schedule is immediately visible in the founder/operator ``list`` and ``show``
outputs and carries a ``schedule_created`` audit row with ``task_id=<SCHEDULE-NNN>``.

#### Mode 3: Persistent (Support Agent only)
The Support Agent is the one exception. Tourists need real-time help and the response time target is under 5 minutes. Two approaches:

**Option A: True persistent session.** The Support Agent runs as a long-lived agent session that waits for incoming inquiries. Advantages: instant response, no cold start. Disadvantages: continuous LLM session cost, needs health monitoring and auto-restart.

**Option B: Fast on-demand with warm-up.** The Support Agent is spun up on-demand like other agents, but with optimizations to reduce cold start: pre-assembled context kept ready, a lightweight executor for simple queries, full executor only for complex ones. If 10-20 second startup is acceptable within the 5-minute response window, this avoids the cost of a persistent session.

**Recommendation:** Start with Option B (fast on-demand). Switch to Option A only if response time is consistently too slow or if support volume justifies the cost.

### Concurrency

The orchestrator controls how many agent sessions run simultaneously. On a Mac Mini, practical limits:

| Constraint | Guideline |
|---|---|
| Concurrent sessions | 2-3 max (LLM API rate limits, memory, CPU for executors) |
| Task queuing | Tasks beyond concurrency limit are queued and processed FIFO |
| Priority queue | Tier 1 escalations and founder-initiated tasks jump the queue |
| Session timeout | 30 minutes max — if an agent session hasn't completed, kill it and escalate |

This means if the Content Writer is drafting a guide and the Content QA needs to review something else simultaneously, both can run. But if a third task arrives, it waits in the queue. The orchestrator logs queue wait times — if tasks are regularly waiting, it's a signal to either optimize agent session speed or increase concurrency.

### Cost profile

With on-demand sessions, daily cost scales with actual work, not idle time:

| Phase | Estimated daily sessions | Estimated daily LLM cost |
|---|---|---|
| Phase 1 (Content Team only) | 5-10 sessions | $3-8 |
| Phase 2 (+ Product/Ops Teams) | 15-25 sessions | $8-20 |
| Full org (all 4 Teams active) | 25-40 sessions | $15-35 |

These are rough estimates assuming Claude Sonnet pricing. Actual costs depend on task complexity, revision rounds, and which executor is used. The dashboard's cost tracking page (Page 6) gives you real-time visibility.

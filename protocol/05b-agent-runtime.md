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

**Per-agent model override (Issue #568).** An agent may override the default
model its executor launches with via the ``model:`` field in its frontmatter
(``AgentDef.model``). This is the SINGLE authoritative per-agent model store.
When set, the runtime forwards it to the executor via ``executor.run(model=...)``
in every built-in invocation class:

- **Task/subtask:** ``Orchestrator._run_agent`` resolves model via
  ``_resolve_model_name`` and passes it to ``executor.run(model=model_name)``.
- **Thread bootstrap/reply:** ``thread_runner.run_invocation`` resolves model
  from ``AgentDef.model`` and passes it in the `_invoke` closure.
- **Working-hours wake:** ``wake_runner.run_wake`` resolves model from
  ``AgentDef.model`` and passes it to ``executor.run(model=...)``.
- **Dream:** ``dream_runner.run_dream`` resolves model from ``AgentDef.model``
  and passes it to ``executor.run(model=...)``.
- **Schedule fire:** ``schedule_runner.run_schedule`` resolves model from
  ``AgentDef.model`` and passes it to ``executor.run(model=...)``.

When ``model`` is absent (``None`` or not set in frontmatter), the executor
launches with its CLI-default behavior — the same as before Issue #568.
Custom executor profiles and custom-adapter profiles are unaffected: model
is forwarded through the standard ``executor.run(model=...)`` API without
modifying the executor factory, permission construction, or adapter semantics.

The session-not-found eviction fallback in ``run_invocation`` also forwards
the model on its clean-slate retry — both the initial resume attempt and the
fallback full-prompt launch receive the same ``model`` value.

**Prompt transport and the pre-spawn argv guard (THR-200).** The invocation
prompt is one opaque string; the kernel caps a single argv element (Linux
``MAX_ARG_STRLEN`` = 128 KiB − 1, measured 131,071 bytes on this org's
hosts; macOS/Windows limits differ). Prompt bodies therefore belong on **stdin**
for every stdin-capable built-in:

- **Claude** (pinned 2.1.241): ``claude -p --permission-mode <m> --allowedTools
  <t> --output-format json [--resume <id>]`` with the prompt delivered via
  ``input_text`` (stdin). Verified by canary: exact Unicode/newline semantics
  preserved, ``>=512 KiB`` UTF-8 prompts launch, JSON envelope + session id
  parsed.
- **Pi** (pinned 0.84.2): ``pi -p --mode json`` with the prompt on stdin.
  Verified by canary: piped stdin becomes the SOLE user prompt (``role:
  user`` / ``type: text``, exact bytes) with no argv message.
- **Codex**: unchanged — ``codex exec ... --json -`` already reads stdin.
- **OpenCode and generic-CLI profiles remain argv-based and behaviorally
  unchanged** until their stdin contracts are separately proven (OpenCode's
  official docs only promise positional ``[message..]``; generic-CLI prompt
  transport is profile-defined via ``{prompt}`` template substitution).

Before any Popen, ``_run_command`` runs a **portable pre-spawn argv guard**:
when the prompt travels via argv (``input_text`` is None), every argv element
is checked against the platform-safe per-argument byte limit and an oversized
element fails deterministically with the normalized category
``prompt_transport_too_large`` BEFORE the kernel can raise ``E2BIG``
mid-launch. The prompt is NEVER truncated. Encoded byte size is
**transport-only** — it is not a cost or reset policy; future cost policy
must use turn count or cumulative session tokens (transcript bytes do not
track provider-session cost). The guard preserves known-good smaller argv
executions (the limit is never below the platform's kernel floor).

**Thread provider-session lifecycle (THR-200).** ``thread_participants``
carries the resumable provider session id + delta watermark
(``agent_session_id``, ``last_resumed_seq``). Lifecycle rules:

- **Resume is Claude-only and an optimization, never a correctness
dependency**; the SQLite transcript is canonical. A GH-688 claimed REPLY may
resume with a delta ONLY when the stored watermark is strictly below the
claim's ``running_from_seq`` — the ``<``, ``=``, and ``>`` cases are all
covered so no required sequence is ever omitted; ``=``/``>`` use the full
prompt. The equality state (watermark == ``running_from_seq``) is NOT
permanent: after ONE successfully transported, terminally settled full-prompt
turn both watermarks converge to the same frontier and resume eligibility
returns. Do not implement a standalone watermark-comparison change.
- **Eviction (provider-declared session-not-found)**: the eviction audit and
the durable ``agent_session_id = NULL`` invalidation commit in ONE
transaction BEFORE the full-prompt fallback launch. If the fallback also
fails, the id remains NULL and the delivery watermark does NOT advance — the
next wake re-attempts the same range from a full prompt. (THR-187/THR-195
missing-session wedges are this mechanism; THR-198's healthy-session
equality wedge needs only transport — no row change.)
- **Lifecycle invalidation**: thread archive, a SUCCESSFUL executor switch,
and agent termination clear resume state (id NULL, watermark 0) so any later
wake starts fresh. Each boundary is a database-owned transaction: the
participant reset and its ``thread_session_invalidated`` audit commit
atomically (the termination reset+audit run inside the existing
terminate-cleanup transaction; archive wraps the ``ARCHIVED`` status flip,
every participant reset, and the audit in one transaction). A reset/audit
failure leaves no partial lifecycle state — a failed switch is rolled back
(prior frontmatter restored, workspace re-reconciled, so no new executor is
installed) and a failed archive leaves the thread OPEN with every session
row unmodified. Participant removal already hard-deletes the row (session
state goes with it — no redundant clear).

**Thread reply delivery lifecycle (GH-688 Phase 1).** Conversational
``REPLY`` wakes are coalesced and durably tracked per ``(thread_id,
agent_name)`` in the additive ``thread_reply_delivery_state`` table. The store
owns every state transition: ``record_conversational_arrival`` appends a
message and raises/creates at most one queued wake per pair in one
transaction; ``claim_conversational_reply`` is the durable queued→running CAS
a runner must pass before any prompt or provider work (a stale/duplicate
queue notification no-ops there); ``settle_conversational_reply`` is the
single seam for every terminal path (reply, silent decline, clean-no-callback,
provider failure, timeout, materialization failure). A successful/declined
range acknowledges only the claimed coverage; arrivals during the run yield
exactly one follow-on; failures leave ``retry_required`` for the next
conversational arrival (no hot loop). Abort/archive/participant-removal
discard through an explicit boundary and never resurrect. At daemon startup,
``_sweep_on_startup`` replaces only the conversational ``REPLY`` portion of
the generic reaper with store-owned recovery: a valid queued wake — a
pending, **unstarted** same-pair ``REPLY`` (the claim CAS enforces the same
precondition) — is retained and re-enqueued; an interrupted running
``REPLY`` becomes exactly one ``daemon_restart`` replacement; and a malformed
queued slot referencing a **started** receipt fails closed — the owned
pending ``REPLY`` receipts for the pair are retired, the slot clears with a
truthful diagnostic, nothing is re-enqueued, and the preserved
``required_through_seq`` lets the next conversational arrival mint the single
covering wake. ``BOOTSTRAP`` / ``TASK_FOLLOWUP`` keep the
generic reaper exactly. See ``docs/agent-guides/features-and-invariants.md`` →
Thread Broadcast Routing for the full contract.

Every Phase-1 store transition emits a lifecycle audit row atomically in the
same SQLite transaction (Slice C), under the existing ``audit_log.task_id =
THR-*`` scope convention: ``thread_reply_wake_created`` (arrival mints a queued
wake), ``thread_reply_wake_coalesced`` (arrival advances an existing wake),
``thread_reply_wake_claimed`` (queued→running CAS success),
``thread_reply_wake_settled`` (reply/decline/failure/timeout terminal),
``thread_reply_wake_cancelled`` (abort/archive/participant-removal discard and
fail-closed recovery sweeps), and ``thread_reply_wake_recovered`` (startup
retention/replacement of a wake). Duplicate queue notifications (stale claim
no-ops) and idempotent recovery can never fabricate events; pure slot-clears
record only the existing ``last_terminal_reason`` diagnostic. Payloads carry
agent, inclusive range, 8-char token prefix and outcome/reason/follow-on
result — never full single-use tokens. The web UI projects the same
``reply_delivery`` pair state honestly (queued/coalesced count, running with
immutable range, retry_required diagnostic) without fabricating subprocesses.
Per-message ``responder_status`` entries additionally carry the authoritative
invocation ``purpose`` (``reply`` | ``task_followup``; BOOTSTRAP stays
excluded) so the web classifies/dedups in-flight responders by purpose —
never by the triggering row's kind, which would mislabel a
system-row-anchored coalesced REPLY range as a special wake.

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

**Wrapper-owned headless launch posture.** A custom-adapter wrapper MUST
choose and apply its underlying CLI's own non-interactive, sufficiently
permissive launch posture for every unattended daemon session. It MUST NOT
rely on ``executor_context.permission_mode`` for its CLI-specific headless
posture or on daemon translation of policy or provider-specific allow-rule
strings. That existing v1 field remains a legacy nullable, provider-specific
compatibility field; ``CustomAdapterExecutor`` supplies ``null`` for
custom-adapter invocations. The wrapper MUST preserve callback availability
from the daemon-provided environment (including ``PATH``) so the invoked agent
can perform ordinary workspace actions and invoke its required ``happyranch``
callback. This is a wrapper implementation and founder-approval responsibility:
approval requires evidence of a successful end-to-end unattended session that
invokes the required callback. It adds no new daemon-supplied or
daemon-translated permission policy or field to ``AdapterInput`` and no daemon
permission/sandbox expansion.

**THR-107 Slice 1A mint authority foundation.** A master-authenticated runtime
adapter-purpose token mint may additionally carry an exact first-party
``workspace_adapter_id``. When present, the daemon writes only a
domain-separated one-way token fingerprint and server-derived intent into a
separate runtime-root SQLite authority store. These ``minted_nonlaunchable``
rows are not profiles, adapters, projections, connection state, or launch
eligibility. **Slice A** adds one loopback, registration-token-only
``POST /api/v1/runtime/custom-cli/connect`` ingress. It accepts only a strict
v2 manifest and nonsecret metadata, derives profile/adapter/wrapper authority
solely from the fingerprinted mint, validates the existing canonical
``<daemon-home>/adapters/<canonical-adapter-id>`` wrapper and declared child
paths, then writes a read-back ``received_nonlaunchable`` receipt plus
append-only event. It never creates/copies/chmods a wrapper, runs a probe or
``Popen``, creates a YAML profile/registry/adapter entry, or claims Connected.
Malformed known-direct attempts terminalize their authority; unknown or
foreign registration context remains ordinary invalid context. Legacy adapter
mints without the optional field remain the existing PENDING-submission path.
If the final registration-token commit fails, Slice A compensates the
receipt/event boundary.

**THR-107 Slices 1–3: projection, launch fence, UI cutover.** Building on
Slice A's `received_nonlaunchable` receipt:

- **Slice 1 (projection).** A separate, master-bearer-authed
  `POST /api/v1/runtime/custom-cli/{operation_id}/commit` route (never the
  registration-token-authed `/connect` route, which is pinned to spawn zero
  subprocesses) drives one receipt through a durable `planned` →
  `committed`/`failed` state machine (`direct_connect_projections` table,
  additive to the Slice A authority store). Committing runs the SAME bounded
  conformance probe the legacy master-bearer registration path uses. For direct
  projection, the wrapper forwards the entire normal v1 ``AdapterInput.prompt``
  through one real provider invocation, obtains a genuine terminal provider
  response, and returns the wrapper-owned ``AdapterOutput``; the provider does
  not construct that envelope. The fresh opaque canary must appear complete in
  ``result.text``; a fabricated/static success does not prove delivery. The
  short probe guides no optional tool use or workspace exploration without
  enforcing or collecting telemetry about those provider-internal actions, and normal task
  behavior is unchanged. The projection then
  reuses the EXISTING `adapter_store`/`custom_adapter_registry` persistence
  primitives (not a second write path) to durably write an
  `AdapterEntry(status="approved", registered_by="direct-connect",
  approved_by="direct-connect")` with a `dependency_manifest_version: 1`
  manifest from the receipt's declared children, then binds a runtime
  profile via the same `_perform_adapter_profile_binding` the seq237
  atomic-approve-and-bind path uses. `version`/`contract_version` are read
  from the probe's own `AdapterOutput.adapter_metadata` (not the manifest,
  which carries no such fields); `capabilities` defaults to `[]`
  (D5 baseline-only — direct-connect has no manual capability-declaration
  surface). Idempotent on retry; a single winner under concurrent commit
  attempts (the `direct_connect_projections` insert is the sole arbiter;
  losers reconcile its durable `planned` or terminal state instead of racing
  the probe a second time); every failure path compensates (removes the just-created
  `AdapterEntry` if profile binding fails) so no partial adapter/profile/
  registry state survives. `GET /api/v1/runtime/custom-cli/status` (keyed
  only by `intended_profile_name`, never token plaintext) exposes the
  deterministic wrapper destination plus the latest live receipt's id and
  projection state. A historical `committed` projection whose profile is no
  longer present in both the durable runtime profile store and active executor
  registry is not a receipt: `operation_id`, `profile_state`, and `reason`
  are all reported as null. This read-time reconciliation lets the founder
  reconnect after removing a profile without mutating the historical
  projection record. The browser can find the daemon-issued
  wrapper path to show in a connect prompt and detect when the candidate
  CLI's own `/connect` call has landed.
  The daemon also runs a periodic projection sweep that finds each
  `received_nonlaunchable` operation without a projection row and invokes the
  same coordinator directly; therefore completing a connection does not
  depend on a browser calling `/commit`, while `/connect` remains a strict
  zero-subprocess receipt boundary.
  `POST /api/v1/runtime/custom-cli/{operation_id}/forget` is the separate
  master-bearer-authenticated Settings → Executors cleanup for a terminal `failed`
  projection. It is the only direct-connect route that deletes derived rows.
  It refuses missing, `planned`, and `committed` operations, and also refuses
  while retry validation is `running` or after it `succeeded` (the latter
  retains the live connected binding). Cleanup removes only the permitted
  derived artifacts for that operation, its failed projection row, and any
  retry-attempt row. The immutable parent authority, accepted candidate record,
  canonical identity history, receipt, operation row, and event trail remain
  append-only and retained for the parent authority lifetime; they are never
  deleted, rewritten, or truncated. Before removing derived artifacts, the
  route opens the failed receipt's persisted wrapper path with no-symlink
  handling and hashes its regular-file descriptor. Because the supported
  filesystem APIs provide no compare-and-unlink operation bound to that
  descriptor, cleanup never unlinks any present wrapper: an absent wrapper is
  reported absent, a hash mismatch is reported changed, and a matching,
  symlink, nonregular, or unreadable candidate is reported preserved-unsafe.
  This fail-closed retention prevents a successor from being silently unlinked
  or displaced at the canonical path.
  `happyranch custom-cli forget
  <profile>` first reads the status route and refuses to call this cleanup
  route unless the profile state is `failed`.
  **THR-160 corrected-artifact retry and immutable-snapshot validation.**
  The normal direct-connect flow has two separate retry paths.

  1. **Corrected-artifact retry (same-token).** When a first candidate fails
     only the conformance probe, the canonical candidate-ledger state is
     `failed_retryable` with `retry_eligible: true`. The founder modifies the
     wrapper or child artifacts and reruns the *existing* generated prompt
     before the original 30-minute expiry. The candidate CLI's `/connect`
     admits exactly one genuinely changed candidate; unchanged or merely
     reordered artifacts receive an indefinite, non-consuming `409 Duplicate`
     and are refused. There is no cooldown. A second terminal failure moves
     the ledger to `exhausted` (no further candidates allowed), while expiry
     moves it to `expired`; both are nonretryable. Success at any point
     closes the lifecycle as `connected`. Terminal legacy v0 operations that
     predate the THR-160 candidate ledger are classified on store open before
     any backfill: the operation, receipt, projection, artifacts, and authority
     must all correlate (matching operation/receipt IDs, receipt token
     fingerprint equal to the operation fingerprint, receipt in the expected
     received state, a terminal `conformance_probe_failed` projection with no
     bound/approved `profile_name` or `adapter_id`, no succeeded retry, and a
     derivable normalized identity). Only a fully trusted conformance failure
     backfills as an `open` parent with a failed ordinal-1 candidate so a
     genuinely changed corrected candidate is admitted as ordinal 2. Every
     other terminal category, corrupted receipt correlation, bound/approved
     profile fact, expired authority, or integrity-invalid artifact set is
     backfilled closed (`failed`/`expired`) without ever opening the parent;
     where the identity is still derivable it is retained so identical/reordered
     replay is rejected non-consumingly. The two-candidate cap still refuses a
     third candidate. This path never calls `/{operation_id}/retry`, never
     replays a generic token, and never requires `/forget` first.

  2. **Immutable-snapshot validation.** A distinct master-bearer
     `POST /api/v1/runtime/custom-cli/{operation_id}/retry` action is
     eligible only for a terminal `failed` projection that is also the
     latest accepted candidate for its parent token fingerprint. Once a
     newer candidate has been accepted under the same parent, the older
     candidate is stale and retry claims are refused without consuming a
     retry attempt or running a probe. It re-checks the *unchanged*
     persisted wrapper/child snapshot; it is not an artifact retry. It never updates, replaces, or deletes that projection or its
     failure reason, and `/commit` remains idempotent for the historical
     failure. A separate durable retry-attempt lifecycle supplies the atomic
     single-probe winner, terminal outcome, and append-only category-only
     events. Concurrent retry callers share that single running attempt and
     wait (bounded by the winner's own bounded probe work) for its real
     terminal outcome; a concurrent caller never receives a fabricated
     failure while the winner is still legitimately in flight. Before any probe it reads only the receipt's persisted wrapper
     path/SHA and every persisted child path/SHA, then independently rechecks
     each artifact with the intake/launch regular file, executable,
     no-symlink, exact-path, and SHA-256 checks. Missing, duplicate, unusable,
     or changed snapshot data fails closed before invocation; the retry never
     accepts user artifact fields, a mutable adapter record, ambient PATH, a
     new manifest, or a token replay. A successful bounded conformance probe
     writes/binds through the same adapter/profile persistence primitives and
     compensation rules as projection, then records a distinct retry-success
     fact. Status may report the resulting *live* connection as `committed`
     for existing consumers, but includes the retained historical failed
     projection state/reason whenever retry success is the source; it never
     claims the original projection row changed. Failed retries retain both
     the original projection/evidence and no adapter/profile/registry residue.

  Both paths are bounded by: a two-candidate cap per direct-connect lifecycle;
  durable retry after a generic initial consume or daemon restart; append-only
  identity/audit/receipt retention (forget only removes derived projection and
  retry-attempt rows); the legacy submit fence (`/connect` is receipt-only and
  spawns no subprocess); status redaction (no token plaintext, fingerprint,
  identity digest, candidate/artifact history, hash, probe output, or error
  output in `GET /runtime/custom-cli/status`); and the canonical wrapper
  destination as the only required prompt value.
- **Slice 2 (launch fence — proof, not new gating).** `build_executor()` /
  `ExecutorRegistry._resolve_custom_adapter_eligibility()` /
  `CustomAdapterExecutor._launch()` already refuse to construct or launch
  anything but an `AdapterEntry.status == "approved"` adapter with a live
  on-disk SHA-256 re-check at every `Popen` attempt including throttle
  retries — this predates THR-107 slice 1 and applies with no
  origin-specific branching, so a Slice-1-committed direct-connect profile
  is launch-eligible through the identical seam a legacy founder-approved
  adapter is. `runtime/daemon/wake_runner.py`, `dream_runner.py`, and
  `schedule_runner.py` import the SAME `_build_executor_for_provider`
  function `thread_runner.py` defines (not independent copies);
  `Orchestrator._build_executor` is a second thin wrapper over the same
  `build_executor()`. `tests/test_thr107_launch_fence.py` proves this
  end-to-end against a real Slice-1-committed profile, plus that an
  operation which never reached COMMITTED has no registered profile and
  fails closed with `"Unregistered executor"` at the same seam.
- **Slice 3 (UI cutover).** The normal Settings ▸ Executors and onboarding
  custom-CLI flow drives `POST /connect`, then derives connection state by
  polling `GET /runtime/custom-cli/status`. Its receipt-landing handler is
  observation-only: it polls status, never auto-fires `/commit`. A
  `failed_retryable` status instructs the founder to modify artifacts and
  rerun the existing generated prompt; the UI does not call
  `/{operation_id}/retry`, mint a new token, or call `/forget` first. The
  dedicated `/retry` action is exposed only as the historical
  immutable-snapshot validation path for terminal nonretryable failures,
  textually distinct from artifact retry. The daemon-owned projection
  sweep actually completes receipts to `committed`/`failed`, including when no
  browser is present or the tab closes — Connect → Connected in one perceived
  action, no founder-approval wait and no separate conformance-checkin round
  trips. The
  normal-flow PENDING/approve/reject/legacy-bind-recovery UI
  (`PendingAdaptersSection`, `RecoveryBindCard`, the `useAdapterConnect`/
  `useAdapterRecovery` hooks) is deleted outright, not hidden behind an
  advanced panel. The backend `POST /runtime/adapters/{id}/approve|reject|
  bind-profile` routes and their `lib/api/adapters.ts` TS bindings are
  UNCHANGED and preserved as operator-only one-time disposition tooling
  outside the normal user flow (`tests/contract/route-classification.json`
  reclassifies them from `included` to `excluded` — no normal-flow browser
  consumer remains, but the routes and Slice-1-era tests stay intact for
  manual/scripted operator use).

**Correction — `workspace_adapter_id` is CLI-declared, not founder-chosen
(post-slice-3 follow-up).** The Slice 1 paragraph above and the original
Slice 1A mint-authority paragraph both describe `workspace_adapter_id` as
a mint-time founder choice; that shipped, then was reversed after tracing
where the field is actually consumed. It is read ONLY at `happyranch
init-agent` time (`ContextBuilder._adapter()`, `runtime/orchestrator/
context_builder.py`), to pick which workspace-bootstrap convention an
agent's workspace uses (`.claude/settings.json` + `CLAUDE.md` vs
`AGENTS.md`-style) — it plays no role in the connect/probe handshake
itself, and the wrapper author is the only one who actually knows which
convention their CLI expects. `DirectManifestV2` now carries a REQUIRED
`workspace_adapter_id` field (one of `claude`/`codex`/`opencode`/`pi`),
declared by the connecting wrapper in its own `POST /connect` body.
`DirectConnectAuthorityStore.receive()` takes this as an explicit
parameter and stores it on `direct_connect_operations`, superseding
whatever value was set at mint time — which remains ONLY the pre-existing
Slice-1A authority-row activation trigger (the founder's browser now sends
a fixed internal value; the founder never picks one). Downstream
(`get_receipt_artifacts` → projection → `AdapterEntry.workspace_adapter` +
the bound runtime profile) is unaffected, since it already read from this
same column. The Settings/onboarding "Workspace CLI" dropdown described
above no longer exists — `AdapterConnect`'s form is name-only; the
generated connect prompt instead has the wrapper author pick their own
convention and send it in the manifest body.

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
  This register → conformance → PENDING → founder exact-snapshot approve (or
  reject) → APPROVED + bind path is now operator-only disposition tooling
  (its routes and TS bindings are preserved, but no normal-flow UI calls
  them — see the THR-107 Slices 1–3 paragraph above for the normal
  direct-connect path). Approve transitions APPROVED + atomic profile bind
  for intended adapters (``already_bound``) OR leaves no-intended adapters
  for advanced Bind recovery (``recovery_ready``). PENDING rejection
  atomically removes the entry with no persisted rejected status.
  Approved-only removal is a separate DELETE path for APPROVED adapters.
  Rollback: re-register the profile as ``generic-cli`` or revert the
  deployment.
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
An unrecognised context string — one that is not a valid ``SessionContext`` value
— is a no-op: the function returns immediately without creating, building,
preflighting, or reconciling any links, and must not withdraw or mutate an
existing valid workspace state.  For valid contexts, system-contract links are
unioned across all ordinary session contexts so a later single-context launch
never withdraws a valid link belonging to another context; release-managed and
B2 custom-skill links remain policy-reconciled and withdrawable.

#### Canonical skill store + workspace symlinks (macOS and Linux)

As of TASK-4009/TASK-4012, skill materialization uses a **canonical skill store**
outside executor workspaces. Skills are built once into hash-addressed packages
and workspace entries are **validated relative symlinks** to exact approved
package versions under both `.claude/skills` and `.agents/skills` roots
(including Codex, Opencode, Pi, and mapped custom profiles).

**Supported platforms:** macOS (darwin) and Linux. Windows and unknown platforms
explicitly fail closed before launch/materialization with a named
`PlatformIsolationError`.

**Delivery model (same-owner):**

The executor and daemon share the same OS identity on macOS and Linux. Linked,
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
from same-UID local source. On mismatch, malformed/broken/malicious link,
or event-persistence failure, the daemon
emits a durable visible integrity event and refuses the session before
Popen/retry. First-ever materialization of an absent package remains
allowed; valid existing packages may be reused.

**Manual recovery only:** (a) For broken links: ``happyranch set-executor
<agent> --executor <current-executor>`` (re-materializes links only, NEVER
recovers corrupted bytes). (b) For corrupted canonical bytes:
``happyranch skills recover <slug> <version> <content_hash>`` — the sole
operator-invoked recovery path. Accepts only the eligible current B2 version,
validates its ledger provenance and every
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
provenance/members for system, release-managed, and B2 custom-skill
version-pinned packages.
- The readonly hardening is cosmetic — the executor shares the daemon's uid
and can chmod files back to writable. Do not describe byte targets, local
sources, ArtifactStore, or links as OS-immutable, ACL-protected, trusted,
executor-only writable/unwritable, or automatically recovered.

**Integrity verification:**
Before each executor launch, the daemon compares actual canonical package
content against the ledger-declared member hashes:
- System-contract packages: compared against the shipped source tree hash.
- B2 custom skills: each member's actual hash compared against the
  ArtifactStore manifest.
On mismatch the daemon emits a durable integrity/operations event and
refuses the session. Corrupted bytes are NEVER silently accepted as valid
and NEVER automatically rebuilt, copied, or healed from same-UID local
source. The ArtifactStore is NOT a trusted or immutable source — a
same-UID process may also tamper with artifact bytes. This is
detection-only with fail-closed refusal; it is NOT an attacker-independent
external attestation authority.

**Platform contract (macOS and Linux):**
- Both platforms use native POSIX relative symlinks, same-directory
``os.replace`` publication, cosmetic chmod hardening, and direct same-identity
``subprocess.Popen`` launch. Linux support assumes filesystems preserve those
semantics; missing primitives fail through the existing named refusal paths.
- The executor launches directly under the daemon's identity. The prompt
guard directs agents not to edit managed skill links and states that
integrity verification is not a security boundary.
- Ordinary directories, malicious/broken/external/wrong-version links, unsafe
targets, failed permission check, or repair errors fail closed and prevent
launch. Never recursively delete or follow attacker nodes.

**Link validation and repair:**
- Materialized links are validated relative symlinks resolving inside the
canonical store. Stale, broken, wrong-version, non-symlink, external, or
mismatched-hash entries are atomically repaired.
- Withdrawal removes only owned validated links, retains canonical packages.
- The full expected union is derived once per provider root:
  **system-contract links are unioned across all ordinary session contexts**
  (task, thread, wake, dream, schedule, bootstrap) and retained so a later
  single-context launch never withdraws a valid link belonging to another
  context.  **Release-managed and B2 custom-skill links remain policy-reconciled**
  and are withdrawn when the agent becomes ineligible, retired, or unassigned.

**Workspace containment (THR-190 PR-B):**

*Threat principal:* a SANDBOXED Codex/Pi agent with workspace-write can
pre-position symlinked workspace/provider/nested-skills paths — e.g.
``<ws>/.claude``, ``<ws>/.claude/skills``, ``<ws>/.agents/skills``, or deeper
nested entries — that point OUTSIDE the real workspace. On the next session
start the daemon's materializer must NOT follow those pre-positioned parents
when creating, replacing, or withdrawing skill links; following them would
write, unlink, or replace files OUTSIDE the real workspace under the daemon's
identity. An unsandboxed Claude session (which can already reach anything the
daemon can) is NOT the defended principal.

*Structural enforcement:* resolved-parent containment inside the REAL
(resolved) workspace is enforced at the actual lowest-level link writer
(``PlatformIsolation.create_relative_symlink``) immediately before each link
creation/replacement — not by a route manifest, caller convention,
lexical-only check, or one-time earlier validation:
- **No-follow dirfd walk.** Every path component of the link's parent below
  the resolved workspace root is admitted (and, where authorized, created)
  RELATIVE to its already-pinned parent directory fd in a
  component-by-component walk rooted at a pinned no-follow
  (``O_NOFOLLOW``) fd for the REAL workspace root: ``os.open(part,
  O_RDONLY|O_DIRECTORY|O_NOFOLLOW, dir_fd=parent)`` /
  ``os.mkdir(part, dir_fd=parent)``. A full pathname is never re-resolved
  or reopened after admission, so a symlink at ANY level — workspace-level
  provider dir (``.claude``), provider root (``.claude/skills`` /
  ``.agents/skills``), or a nested skills root — fails closed with a named
  ``escaped_parent`` error, and a same-UID swap of an already-admitted
  ancestor cannot redirect any later step. Missing components are created
  as genuine directories anchored to the pinned parent.
- **Pinned-fd mutation.** The final parent fd is retained through the ENTIRE
  mutation — mkdir, stale temporary-parent/temp-link cleanup, temporary-
  symlink creation, ``os.replace`` repair, and withdrawal ``unlink`` — so
  every same-UID ancestor-swap window is closed: the write/unlink/replace
  is bound to the admitted inode, never re-resolved through a pathname.
- **Contained withdrawal, admission, and enumeration.** ``withdraw_workspace_link``
  and ``admit_skills_directory`` apply the same component-by-component dirfd
  walk; ``admit_skills_directory`` returns the ADMITTED directory fd,
  retained open, and repair enumerates the skills root ONLY through that
  admitted fd (the full pathname is never re-resolved to list after
  admission) — so no symlink swap or escaped parent can list, write, unlink,
  or replace outside the real workspace.
- **Ordinary workspaces unchanged.** Canonical relative symlinks wholly
  inside a normal workspace materialize and repair exactly as before.

*Failure ordering:* containment failures surface as named materialization
errors during the pre-spawn transaction, BEFORE executor construction or
launch — every session-start family (task, thread, wake, dream, schedule)
persists a terminal failure and returns without invoking the executor.

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

**THR-055 B2 custom skills.** Custom skills use `custom_skills`, immutable `custom_skill_versions`, eligibility rules/events, per-session materialization evidence, and custom-skill events. The only agent create path is `POST /api/v1/orgs/{slug}/skills/agent`, invoked by `happyranch skills create --from-file <package.json> --session-id <session-id> [--org <slug>]`. It is bearer-free, derives org/task/agent/session from the verified SessionTracker binding, returns `{skill, version, hidden_reason, provenance}`, and creates a default-hidden editable B2 record. Every verified agent may use it; founders configure eligibility later.

**Supported SKILL.md authoring contract (THR-169).** Newly authored SKILL.md bodies are YAML-frontmatter-first: a valid opening `---` fence, a YAML mapping, a closing `---` fence, then a Markdown heading. One canonical shape validator (`runtime/skills/skill_md.py`, reached by every custom-skill write route through `_validate_skill_package`) enforces the contract; malformed/unclosed/non-mapping frontmatter and missing post-frontmatter headings are rejected. Heading-first bodies are legacy-only: versions validated and stored valid under the pre-cutover contract remain resolvable and materializable (the resolver and materializer read stored `validation_state` and never re-validate), but heading-first bodies are NOT accepted for new authoring.

**Atomic version writes.** `POST /api/v1/orgs/{slug}/custom-skills/{skill_id}/versions` (and the human/agent create paths) finish validation before any persistence. Invalid input is rejected with HTTP 422 `validation_failed` and zero durable residue — no version row, event, `current_version_id` advance, materialization, or content-artifact file. A byte-identical body conflicts with the append-only `UNIQUE (skill_id, content_hash)` invariant as HTTP 409 `duplicate_content` (never relaxed).

**FAIL-CLOSED materialization.** Any error during materialization raises
immediately. A failed materialization must NOT leave a partially-populated
skills directory passing as complete. All five caller contexts (orchestrator
`run_step`, `thread_runner`, `wake_runner`, `dream_runner`, `schedule_runner`)
persist a database-terminal failure and return BEFORE executor spawn — a
materialization error in any spawn path blocks the agent launch, never silently
skipped.

**Process-local workspace serialization (Issue #536).** All pre-spawn skill
materialization for a given agent workspace — system-contract injection +
on-disk verification, managed-skill injection, and B2 custom-skill injection
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
or the underlying ``OSError``) — never
a bare ``FileNotFoundError``. The caller persists the terminal failure and
no agent subprocess is launched. Recovery requires fixing the underlying
filesystem/permission issue and explicitly re-dispatching.

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

### Host-session admission and terminal cleanup ordering (THR-207 / TASK-5584)

A daemon-wide ``HostSessionSupervisor`` (``runtime/orchestrator/host_supervisor.py``)
owns admission and containment ordering for every top-level agent invocation.
The governing spec is ``docs/superpowers/specs/2026-08-24-host-resource-concurrency.md``
and the platform-neutral backend contract is ``runtime/platform/session_backend.py``.

**Load-bearing ordering invariants** (enforced by the supervisor core):

1. **No agent subprocess launches before admission.** ``backend.prepare`` /
   ``backend.launch`` and the executor launch body run only after an admission
   lease is granted. Queued cancellation removes the request with no
   launch/handle/lease leak.
2. **Every terminal path finishes containment before lease release**, success
   included, in the fixed order: freeze terminal result → collect receipt →
   backend finish (tree teardown + quiescence check) → capability-appropriate
   residue accounting/reconciliation → publish bounded receipt → release lease
   exactly once. Cleanup errors never replace the primary terminal reason.
3. **Cancellation goes through the opaque containment handle**, never a bare
   PID-only signal, and is idempotent with the executor's own finish.
4. **Policy snapshots are immutable per invocation** and are explicit canary
   inputs (Linux `<=11` non-binding shadow; macOS 4 binding; low-single-digit
   measured cleanup grace), never host-derived permanent defaults.
5. **Ownership transfers atomically at admission grant.** The controller
   creates the ownership record under its lock and keeps it in its registry
   until lease release; the durable first-wins terminal reason lives on that
   record from grant; the daemon drain iterates the same registry. A shutdown
   that fires when or immediately after admission is granted freezes SHUTDOWN
   on the record, and the attempt's next gate observes it — refusing launch
   before any handle, or finishing containment exactly once if the launch was
   already committed. No reason- or window-specific special case.

Slice A wires **exactly one** narrow production producer per the
founder-approved real-caller amendment (THR-207 seq 41–44): schedule fires run
through the supervisor with the honest no-enforcement ``PassthroughBackend``
(``runtime/platform/passthrough_backend.py``; all capabilities unavailable),
and the daemon drain calls ``supervisor.shutdown()`` in the app lifespan
finally before producer workers are cancelled. The other producers, both Popen
bodies in ``runtime/orchestrator/executors.py``, Linux/macOS backends, and
observability wiring are later serial slices. ``runtime/platform/isolation.py``
(canonical-skill-store integrity + same-owner launch) is layered beneath the
supervisor and is unchanged by this design.

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

> **Org-root portability (THR-187 Slice A).** The *only* workspace content
> that is portable across a same-slug relocation is
> ``workspaces/<agent>/memory/**``. ``task_history.md`` (rebuilt from the DB),
> ``repo/`` clones, regenerated bootstrap files, injected settings/skills,
> caches, task-output directories, and every other workspace byte are
> non-portable and named as exclusions by the preflight classifier.
> ``runtime/portability/roots.py`` is the authoritative exhaustive direct-org-root
> classification (allow / named exclusion / reject); see 05c-orchestrator
> §Organization portability.

### Three layers of memory

**1. Institutional memory (knowledge base)**
Shared across all agents. Org charter, SOPs, brand guidelines, partner directory, regulatory summaries. Read-only for most agents, write access scoped per role.

**2. Agent-specific memory (memory store)**
Each agent accumulates its own operational learnings. The Content QA records "DSAL website is more reliable than MGTO for Macau visa info." The Content Writer records "always show Octopus + AlipayHK side-by-side on HK transport guides — tourists usually only know one." These files persist across sessions and are loaded as context at session start.

After each task, the orchestrator prompts the agent: "Based on this task, are there any new memory entries to record?" Responses are appended to the memory store. Over time, when the store gets long, the orchestrator periodically asks the agent to consolidate and prune it.

Entries are addressed as `MEM-NNN`. Items migrated from the prior learnings store keep a permanent `LRN-NNN` alias so historical cross-references resolve forever. The audit trail is forward-only: new events log as `log_memory_*`; historical `log_learning_*` rows are never rewritten.

**3. ~~Performance memory~~ (REMOVED 2026-05-27)**
The 30-day rolling scorecard / tier classification was removed. The audit log (`review_verdict` rows after every delegated child terminates, plus completion / failure events) is sufficient for the founder to identify which agents need attention — via `happyranch audit`. A `review_verdict` row's verdict is a distinct fact from the child's completion status: an explicit structured `verdict` reported by the child (a free-string workflow value such as `APPROVE`, `PASS`, or `REQUEST_CHANGES`) is preserved verbatim; only when no structured verdict is present is `approved`/`rejected` inferred from the completion status. The legacy `scorecards` table is no longer created on fresh DBs.

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
3. Writes a `review_verdict` audit row for delegated work so the founder can audit per-agent outcomes via `happyranch audit`. The audit verdict is a distinct fact from completion status: an explicit structured `verdict` reported by the worker is preserved verbatim (a completed worker that reports `REQUEST_CHANGES` carries `REQUEST_CHANGES`, not `approved`); only when no structured verdict is present is the implicit `approved`/`rejected` mapping from completion status applied. Missing/blank/unknown verdicts are normalized at dashboard read boundaries and never counted as accepted.
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

**Kinds.** Three kinds are supported:

- **One-shot** — fires exactly once at a specified UTC ``fire_at`` (max 90 days
  out), then transitions to ``fired`` (terminal).
- **Weekly** — fires every week on a single weekday + HH:MM local time + timezone.
  After each fire the schedule re-arms with the next occurrence and continues
  until either the founder cancels/pauses it or it reaches its ``expires_at``
  (default 90 days from creation). Indefinite weekly schedules (``indefinite=1``,
  founder-set only) have no expiry.
- **Recurring** — uses the bounded daily/weekly/monthly/yearly rule grammar.
  The server computes the first occurrence and immutable local ``anchor_date``;
  a native create or ARMED/PAUSED founder edit may request a canonical local
  ``start_date`` phase, which the server validates against the rule and DST,
  derives to ``fire_at``, and records only as the managed anchor.
  timing-only edits preserve that anchor, while cadence-shape edits reset it to
  the newly computed next occurrence's local date. A native recurring PATCH
  that changes recurrence and/or timezone may omit ``fire_at``; after merging
  and validating the rule, the server derives and persists the next eligible
  occurrence. A supplied ``fire_at`` remains an exact-match assertion against
  that server-computed occurrence. The recurring editor may explicitly send
  null for inactive ``byday``, ``bymonthday``, and ``ordinal`` selectors; the
  service removes those accepted clears after merge before validation and
  persistence, leaving canonical stored rules without stale/null selectors.
  DAILY/YEARLY remain selector-free and the bounded MONTHLY grammar is
  unchanged. One-shot and weekly PATCH semantics are unchanged.

**Founder controls and validation.** Founder/operator routes may pause, cancel,
edit, or ``POST /schedules/{schedule_id}/renew`` an ARMED or PAUSED Todo.
Normal renewal resets its 90-day review window; ``{"indefinite": true}`` grants
indefinite review authority. Renewal never changes cadence, anchor, next fire,
or dispatch count, and rejects FIRING, terminal, and EXPIRED rows with
``state_conflict``. Recurring-create validation returns stable 422 codes:
``invalid_freq_fields``, ``invalid_byday``, ``monthly_selector_missing``,
``monthly_selector_conflict``, ``invalid_interval``,
``anchor_date_not_settable``, ``invalid_start_date``, ``invalid_until``, ``invalid_count``,
``end_condition_conflict``, ``invalid_time``, and ``invalid_timezone``.

**Fire mechanism.** The schedule fire is a two-stage pipeline:

1. **Scheduler (daemon loop).** A 60-second tick scans all orgs for ARMED
   Schedule rows whose ``fire_at <= now`` (one-shot) or ``fire_at`` is within a
   120-second tolerance window (weekly/recurring). For a stale repeating
   occurrence (missed during daemon downtime), the scheduler does not replay or
   backfill it. It records ``occurrence_missed`` and re-arms the row only when a
   future next occurrence exists within any finite review expiry. The terminal
   alternatives do not emit ``occurrence_missed``: a recurring rule exhausted by
   its inclusive ``until`` date becomes FIRED with ``end_reason=date_ended`` and
   ``schedule_fired``; a future candidate beyond the review expiry becomes
   EXPIRED with ``schedule_expired``; and an otherwise unexplained missing
   candidate becomes FAILED with ``error=recurrence_no_candidate`` and
   ``schedule_failed``. A claimed row transitions ARMED → FIRING and emits
   ``schedule_claimed``.

2. **Runner + spawn callback.** The schedule worker loop drains the
   ``ScheduleQueue`` and invokes the owning agent's executor with a dedicated
   schedule-fire prompt. The agent's single job is to call the
   ``happyranch schedules spawn`` callback exactly once. The spawn callback:

   - Accepts only FIRING Schedule rows (single-use, record-scoped guard).
   - Creates one root task from the stored ``normalized_brief``, targeted to the
     owning agent on its own team.
   - Records ``spawned_task_ids`` and increments ``fire_count``.
   - Resolves one-shot → FIRED/``one_shot_completed``. A recurring successful
     dispatch first increments ``fire_count``; count exhaustion is FIRED/
     ``count_exhausted``, then an exhausted ``until`` is FIRED/``date_ended``,
     then a next candidate beyond review expiry is EXPIRED, then a defensive
     missing candidate is FAILED/``recurrence_no_candidate``; otherwise it
     re-arms with the next ``fire_at``.
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
- A claimed weekly or recurring occurrence that fails or times out is audited
  but advances to the next occurrence and remains ARMED; one-shot failures and
  timeouts remain terminal.
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
- **In-org availability:** every agent with a valid active session and a
  resolvable in-org team may create a self-owned Todo. Legacy
  ``scheduling.enabled_agents`` config is accepted as a no-op and does not
  authorize or deny creation.
- **Caps and defaults:** the 20-per-agent / 100-org-wide armed caps, 90-day
  one-shot horizon, weekly shape validation (single weekday + HH:MM + IANA
  timezone only), and 90-day recurring expiry are enforced at create time
  by the ``ScheduleService``.
- **Recurring callback grammar:** a native ``kind="recurring"`` request uses
  the documented `recurrence` object: ``freq`` is ``DAILY``, ``WEEKLY``,
  ``MONTHLY``, or ``YEARLY``; ``interval`` is positive; ``time`` and ``tz``
  are required; weekly requires distinct ``byday`` tokens; monthly has exactly
  one positive ``bymonthday`` or one ``byday`` plus named ``ordinal``; and
  daily/yearly permit no selector. Its end condition is exactly never (omit
  ``until`` and ``count``), inclusive local-date ``until``, or successful-
  dispatch ``count`` (never both). The agent must not set server-owned
  ``anchor_date``. Invalid recurring grammar returns its named stable 422 code;
  the agent must correct it only from the explicit instruction or ask, never
  approximate a different recurrence.

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

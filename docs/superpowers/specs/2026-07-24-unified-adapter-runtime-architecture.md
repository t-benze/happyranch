# Unified Adapter-Runtime Architecture — DESIGN ONLY

**THR-107 founder seq84** | **2026-07-24** | **DESIGN ONLY — no implementation authorization**

This document is a **founder-reviewable architecture spec**. It describes a
target-state unified adapter-runtime model; it does NOT authorize any runtime
refactor, plugin loader, adapter execution, profile migration,
schema/auth/permission change, or production-code test change.

---

## Table of Contents

1. [Boundary and Terminology](#1-boundary-and-terminology)
2. [Versioned Adapter Input Contract and Result Contract](#2-versioned-adapter-input-contract-and-result-contract)
3. [First-Party Packaging and Invocation](#3-first-party-packaging-and-invocation)
4. [Custom Adapter Supply and Trust](#4-custom-adapter-supply-and-trust)
5. [Lifecycle Ownership and Error Semantics](#5-lifecycle-ownership-and-error-semantics)
6. [Manifest / Profile Model](#6-manifest--profile-model)
7. [Migration and Rollback](#7-migration-and-rollback)
8. [Versioning, Conformance, Observability, and Tests](#8-versioning-conformance-observability-and-tests)
9. [Decision Log](#9-decision-log)

---

## 1. Boundary and Terminology

### 1.1 Target Architecture

Every executor implements **ONE** HappyRanch standard adapter interface.
First-party adapters are **bundled, versioned HappyRanch artifacts** — they
ship inside the runtime and are maintained by the project. A custom executor
either (a) is conformant to the standard result contract or (b) is paired
with a custom adapter/wrapper that conforms to the same input/output contract.

The **daemon orchestration layer MUST NOT carry vendor-specific argv/output
knowledge**. Provider-specific argv construction and output parsing live
inside first-party adapter implementations, never in the generic orchestration
or registration routes.

### 1.2 Historical Implementation Snapshot (as of `origin/main` @ `1fb1928b`)

> **This is a pinned historical baseline from before D10/D11.**
> D10/D11 (TASK-3414, THR-107 seq84, July 2026) replaced the if/elif chain
> in ``build_executor()`` with a static data-driven factory dict derived from
> the D8 authoritative catalog. The current shipped ``build_executor()`` no
> longer contains per-provider conditional dispatch — see D10/D11 in §9.3.

The implementation at this snapshot converged five executor classes (Claude,
Codex, OpenCode, Pi, and GenericCliExecutor) through a shared `_run_command` →
`ExecutorResult` lifecycle, but the adapter boundary was **not yet extracted**
into a formalized interface:

| Component | Current (concrete) | Target (formalized) |
|---|---|---|
| Executor selection | `build_executor()` dispatches on hard-coded `if profile.name == "claude"` chains (`executor_registry.py:370-408`) | Factory maps profile → adapter descriptor → executor instance; no hard-coded per-provider dispatch |
| Argv construction | Each `*Executor.run()` builds its own `cmd` list inline (`executors.py:787-822, 840-870, 906-927, 950-969, 1012-1036`) | Argv construction delegated to first-party adapter implementations behind a shared `build_argv()` contract |
| Output parsing | Five hand-written parsers: `_parse_claude_usage`, `_parse_codex_usage`, `_parse_opencode_usage`, `_parse_pi_usage`, `_parse_generic_cli_usage` (`executors.py:200-545`) | Each adapter exposes a `run(stdin) -> ExecutorResult` — argv + parsing are internal to the adapter, invisible to orchestration |
| Workspace prep | Selected by `adapter_id` in `ExecutorProfile` (`executor_registry.py:104`); adapter-specific bootstrap files (`CLAUDE.md` vs `AGENTS.md`) | Unchanged — workspace preparation stays independent of invocation |
| Custom CLI envelope | Optional v1 sentinel-envelope on stdout (`executors.py:476-545`); parsed generically by `_parse_generic_cli_usage` | Retained exactly — envelope remains the custom-CLI conformance surface |

### 1.3 Key Terms

| Term | Definition |
|---|---|
| **Adapter** | A self-contained implementation of the HappyRanch standard adapter interface: accepts `AdapterInput`, launches a subprocess, produces `AdapterOutput`/`ExecutorResult`. |
| **First-party adapter** | Bundled with the HappyRanch runtime. Maintained in-repo, versioned. Covers Claude, Codex, OpenCode, Pi. |
| **Custom adapter** | Supplied by the operator or a third party as a separately installed executable/wrapper. Conforms to the standard data contract. Never loaded as a Python module by the daemon. |
| **Adapter runtime boundary** | The single invocation point in the daemon where `build_executor` → adapter → `run()` → `ExecutorResult`. All five current executors already converge here via `_run_command` (`executors.py:607`); the target is to make the boundary explicit rather than emergent. |
| **`adapter_id`** (current) | A field on `ExecutorProfile` (`executor_registry.py:104`) indicating which workspace adapter to use. This is **misleading** — it selects workspace preparation (bootstrap doc, permission surface), NOT the adapter that executes the CLI. See §6.3 for proposed resolution. |
| **`adapter_id`** (target) | A profiles' reference to a registered adapter implementation versioned by `adapter_version`. Defaults to the profile kind's bundled adapter; custom profiles may reference a first-party adapter version or, in a future founder-gated track, a custom adapter. |

### 1.4 Historical Baseline vs. Target Summary

> **Historical snapshot pinned at `origin/main` @ `1fb1928b` (pre-D10/D11).**
> D10/D11 (TASK-3414, THR-107 seq84, July 2026) replaced the `if/elif` chain
> below with a static factory dict. The CURRENT block diagram is the
> pre-cutover baseline — not the shipped state after D10/D11.

```
BASELINE (1fb1928b, pre-D10/D11):
  build_executor(name) → if/elif chain → ConcreteExecutor(settings)
    → executor.run() → _run_command(cmd, parser=X)
  Orchestration knows: claude argv shape, codex sandbox flags,
     opencode --dir, pi -p, generic template substitution.

TARGET (this spec):
  build_executor(name) → registry.resolve(name) → adapter descriptor
    → executor = adapter.instantiate(settings)
    → executor.run(input) → ExecutorResult
  Orchestration knows: adapter reference + version.
  Adapter internals (argv, parsing) are opaque to orchestration.
```

---

## 2. Versioned Adapter Input Contract and Result Contract

### 2.1 Adapter Input Contract (what the daemon passes to EVERY adapter)

```jsonc
// AdapterInput — normative type (canonical, materialized in adapter_contract.py)
{
  "contract_version": 1,                    // int, required. Version of THIS input contract.
  "invocation": {
    "invocation_id": "sess-<uuid>",          // string, required. HappyRanch session id.
    "task_id": "TASK-3301",                  // string | null. The owning task (null for threads/dreams/wakes).
    "agent": "dev_agent",                    // string, required. Agent name.
    "org": "happyranch",                     // string, required. Org slug.
    "invocation_kind": "task"                // "task" | "thread" | "wake" | "dream" | "schedule"
  },
  "prompt": "<full prompt text>",            // string, required. Includes session-lifetime preamble.
  "workspace": "/abs/path/to/workspace",     // string, required. Already prepared by workspace adapter.
  "timeout": {
    "deadline_seconds": 1800,                // int, required. Wall-clock deadline.
    "max_runtime_seconds": 1800              // int, required. Subprocess communicate() timeout.
  },
  "model": {                                 // object | null. Null → adapter uses its own default.
    "model_id": "claude-sonnet-4-20250514",  // string, required when model is set.
    "model_arg_template": ["--model", "{model}"]  // [string], optional. How to splice into argv.
  },
  "session": {                               // object | null. Present for resume-capable invocations.
    "resume_session_id": "abc123"            // string. The agent CLI's own session id to resume.
  },
  "executor_context": {                      // object, immutable for this invocation.
    "provider": "claude",                    // string. Throttle key (profile name).
    "adapter_id": "claude",                  // string. Workspace adapter (current meaning — see §6.3).
    "adapter_version": "1.0.0",              // string. Adapter implementation version.
    "permission_mode": "auto"                // string | null. Provider-specific permission posture.
  }
}
```

**Custom-adapter headless posture (v1 compatibility exception).**
``executor_context.permission_mode`` remains in the frozen v1 wire schema as
a legacy nullable, provider-specific compatibility field.
``CustomAdapterExecutor`` supplies ``null`` for custom-adapter invocations.
Custom wrapper authors MUST choose their own provider-specific non-interactive,
sufficiently permissive headless posture and MUST NOT rely on this field or on
daemon translation of policy or allow rules for that posture. They MUST
preserve the daemon-provided callback environment, including ``PATH``, so the
underlying CLI can perform ordinary workspace work and invoke the required
``happyranch`` callback.

This is an approval and conformance requirement, not a new enforcement
mechanism: founder approval must include evidence of a successful end-to-end
unattended session that invokes the required callback. It adds no new
daemon-supplied or daemon-translated permission policy or field to
``AdapterInput``.

### 2.2 Adapter Result Contract (what EVERY adapter returns)

```jsonc
// AdapterOutput — normative type example (wire shape the adapter produces;
// the daemon-side adapter maps this INTO the existing ExecutorResult).
// NOTE: stdout_tail and stderr_tail are TOP-LEVEL ExecutorResult fields
// consumed directly by run_step (failure classification, retry/audit paths)
// and thread_runner (including session recovery). They MUST NOT be nested.
{
  "success": true,                           // bool, required. Did the subprocess exit 0?
  "duration_seconds": 42,                    // int, required. Wall-clock duration.
  "session_id": "sess-<uuid>",               // string, required. Echo back the invocation id.
  "returncode": 0,                           // int | null. Subprocess exit code (null on timeout).
  "stdout_tail": "<last 2000 bytes>",        // string, required. Top-level, per ExecutorResult.
                                             // Consumed by run_step:1850/1923/2490 and
                                             // thread_runner:294 for failure forensics.
                                             // MUST NOT be nested — see §2.5.
  "stderr_tail": "<last 2000 bytes>",        // string, required. Top-level, per ExecutorResult.
                                             // Consumed by run_step:1852/1922/2489 and
                                             // thread_runner:58/293 for failure forensics.
                                             // MUST NOT be nested — see §2.5.
  "result": {
    "text": "<agent's final response>"        // string | null. ADDITIVE field. The agent's
                                             // output text (stdout, trimmed). Consumers
                                             // read this as result.result.text.
  },
  "token_usage": {                           // object | null. Maps 1:1 to TokenUsage model
                                             // (runtime/models.py:302). Every field nullable.
    "input_tokens": 1500,                    // int | null
    "output_tokens": 420,                    // int | null
    "cache_read_tokens": 300,                // int | null — cache HITS (NOT new consumption)
    "cache_creation_tokens": 0,              // int | null — cache WRITES
    "reasoning_tokens": null,                // int | null
    "model": "claude-sonnet-4-20250514",     // string | null
    "usage_raw_json": "{...}"                // string | null — opaque raw payload for forensics
  },
  "error": "Session timed out",              // string | null. Human-readable error.
  "agent_session_id": "abc123",               // string | null. The agent CLI's own session id (for resume).
  "rate_limited": false,                     // bool. Did the provider rate-limit this attempt?
  "adapter_metadata": {                      // object, required. ADDITIVE field.
    "adapter": "happyranch-claude-adapter",   // string, required. MUST exactly equal the stable server-derived canonical_adapter_id from the contract-reference / submitted adapter ID. Never a display name, provider, or arbitrary implementation identity. A mismatch fails the conformance probe at registration AND blocks every launch at runtime (D7B).
    "adapter_version": "1.0.0",              // string. Version of the adapter implementation.
    "contract_version": 1                    // int. Version of THIS result contract.
  },
  "child_session_id": null,                  // string | null. Future: spawned child session id.
  "raw_forensics_ref": null                  // string | null. Path/ref to raw forensic capture.
}
```

### 2.3 TokenUsage Semantics (preserved from current code)

The existing `TokenUsage` invariants are authoritative and carry forward
unchanged:

1. **`total` excludes cache reads** (`models.py:316`): `total = (input_tokens or 0) + (output_tokens or 0) + (reasoning_tokens or 0)`. Cache reads are an effectiveness signal, not new consumption.
2. **model-null → back-filled to provider label** (`executors.py:698-699`): If `token_usage.model` is `None`, `_run_command` fills it with the `provider` string.
3. **Nullable tolerance** (`models.py:306-312`): every field is `int | None`. Partial parse success still writes a forensic row.
4. **`cache_read` / `cache_creation` split preserved**: the two fields stay separate.

No new token fields are introduced. The result contract above preserves the
current `ExecutorResult` (`executors.py:24-56`) top-level fields unchanged:
`stdout_tail` and `stderr_tail` remain at top level (not nested) because
they are consumed directly by `run_step` (failure classification at
`run_step.py:1850-1852`, retry/audit paths at `run_step.py:1922-1928`, and
`run_step.py:2489-2490`) and `thread_runner` (session recovery at
`thread_runner.py:58`, invocation error formatting at
`thread_runner.py:293-294`). The two truly additive fields are `result.text`
and `adapter_metadata` — neither existing consumer reads them, so they would
not break existing consumers.

### 2.5 Nested-Result Field Policy (D12 resolution for compatibility)

The `result.text` field is **additive** — it is a new field on the
`ExecutorResult` that lives under a nested `result` object. No existing
consumer reads `result.text`, so it is safe to introduce.

**`stdout_tail` and `stderr_tail` MUST stay top-level.** Moving them into a
nested `result` object would be a breaking relocation — every consumer that
reads `getattr(result, "stdout_tail", "")` or `getattr(result, "stderr_tail", "")`
would silently read empty strings. The current consumer sites are:

| Site | Field | Purpose |
|---|---|---|
| `run_step.py:1087-1091` | `stderr_tail` / `stdout_tail` (from error dicts) | Session-failed note enrichment |
| `run_step.py:1850-1852` | `stdout_tail` + `stderr_tail` | Rate-limit string-heuristic (legacy fallback) |
| `run_step.py:1922-1928` | `stderr_tail` + `stdout_tail` | Failure audit note (truncated tails) |
| `run_step.py:2489-2490` | `stderr_tail` + `stdout_tail` | Error-summary formatting |
| `thread_runner.py:58` | `stderr_tail` | Thread invocation error message |
| `thread_runner.py:293-294` | `stderr_tail` + `stdout_tail` | Wake/schedule/dream error formatting |

If a future version of this spec wishes to normalize *all* output fields
under a nested `result` object (including `stdout_tail` and `stderr_tail`),
that change requires:

1. **A separately approved compatibility adapter/migration** — never a
   behavior-preserving extraction.
2. **Consumer proof** — every `getattr(result, "stdout_tail", "")` site must
   be migrated or dual-read, confirmed by grep.
3. **Founder authorization** — changing the stable `ExecutorResult` contract
   surface is founder-gated (see decision D12).

The daemon-side adapter is responsible for mapping wire data (from custom
adapter executables or envelope JSON) into the existing `ExecutorResult`
shape **without moving existing top-level fields**.

### 2.6 Mapping the Existing v1 Envelope

The Phase-1 custom-CLI envelope (defined in
`docs/superpowers/specs/2026-07-19-custom-cli-adapter-envelope-design.md`)
remains **the canonical custom-CLI conformance surface**. In the target
architecture:

- First-party adapters produce `ExecutorResult` natively — they never emit
  the envelope.
- Custom CLIs that emit the v1 envelope are parsed by a first-party
  "generic envelope adapter" that wraps `_parse_generic_cli_usage`
  (`executors.py:480`) and produces the same `ExecutorResult` shape.
- The v1 envelope continues as the **compatibility entry-point** for custom
  CLIs. A future v2 envelope (if ever needed) would be a separate
  `envelope_version` recognized by the same generic adapter.

**Migration of optional fields:** During the migration window (§7), `result.text`
and `adapter_metadata` are optional — absent → `None`. After founder
authorization of the default-change phase, they become required for new
registrations.

---

## 3. First-Party Packaging and Invocation

### 3.1 Proposed: Bundled Adapter Catalog

A first-party adapter is a **versioned artifact** that ships inside the
HappyRanch runtime. In the target architecture, an **adapter catalog/manifest**
declares which adapters are available:

```yaml
# Proposed: runtime/adapters/catalog.yaml (bundled, NOT writable at runtime)
# DESIGN ONLY — file does not exist yet.

version: 1
adapters:
  claude:
    package: runtime.adapters.claude
    adapter_class: ClaudeAdapter
    capabilities:
      - token_metering
      - session_resume
      - model_select
      - permission_surface
    workspace_adapter: claude
    readiness_marker: .claude/skills/start-task/SKILL.md

  codex:
    package: runtime.adapters.codex
    adapter_class: CodexAdapter
    capabilities:
      - token_metering
      - model_select
      - sandbox_control
    workspace_adapter: codex
    readiness_marker: AGENTS.md

  opencode:
    package: runtime.adapters.opencode
    adapter_class: OpencodeAdapter
    capabilities:
      - token_metering
      - model_select
    workspace_adapter: opencode
    readiness_marker: AGENTS.md

  pi:
    package: runtime.adapters.pi
    adapter_class: PiAdapter
    capabilities:
      - token_metering
      - model_select
    workspace_adapter: pi
    readiness_marker: AGENTS.md

  generic-cli:
    package: runtime.adapters.generic_cli
    adapter_class: GenericCliAdapter
    capabilities:
      - token_metering_via_envelope
    workspace_adapter: pi            # default; overridable per profile
    readiness_marker: AGENTS.md
```

### 3.2 Adapter Runtime Boundary

The daemon invokes exactly ONE adapter runtime boundary per executor launch:

```
Orchestrator._run_agent()  (orchestrator.py)
  └─ build_executor(name, settings, paths)  (executor_registry.py:350)
       └─ registry.resolve(name) → profile
            └─ adapter = catalog.get(profile.adapter_id).instantiate(settings)
                 └─ executor = AdapterBackedExecutor(adapter, profile)
                      └─ executor.run(workspace, prompt, ...)
                           └─ adapter.build_argv(input) → cmd: list[str]
                           └─ adapter.run_command(cmd, workspace, ...) → ExecutorResult
                           └─ adapter.parse_output(stdout) → TokenUsage
```

**The `build_executor` function at `executor_registry.py:350` no longer
contains `if profile.name == "claude"` chains.** Instead, profile → adapter
resolution is data-driven from the catalog. The CRITICAL `ExecutorRegistry`
blast radius (§A.1) means this extraction must be behavior-preserving — the
same argv, same parser, same `ExecutorResult` for every profile.

### 3.3 What Moves Where

| Current location | Target location |
|---|---|
| `executors.py:757-822` (ClaudeExecutor.run — argv construction) | `runtime/adapters/claude.py:ClaudeAdapter.build_argv()` |
| `executors.py:824-870` (CodexExecutor.run — argv construction) | `runtime/adapters/codex.py:CodexAdapter.build_argv()` |
| `executors.py:872-927` (OpencodeExecutor.run — argv construction) | `runtime/adapters/opencode.py:OpencodeAdapter.build_argv()` |
| `executors.py:929-969` (PiExecutor.run — argv construction) | `runtime/adapters/pi.py:PiAdapter.build_argv()` |
| `executors.py:200-428` (four native output parsers) | Respective adapter modules (e.g., `ClaudeAdapter.parse_output()`) |
| `executors.py:480-545` (`_parse_generic_cli_usage`) | `runtime/adapters/generic_cli.py:GenericCliAdapter.parse_output()` |
| `executors.py:971-1038` (GenericCliExecutor.run — template substitution) | `runtime/adapters/generic_cli.py:GenericCliAdapter.build_argv()` |
| `executors.py:607-725` (`_run_command`) | **Stays** — it is the shared subprocess-launch function below the adapter boundary. Every adapter calls it. |
| `executors.py:24-56` (`ExecutorResult`) | **Stays** — unchanged; adapters produce it. May gain additive fields (see §2.2). |

### 3.4 Non-Goal: Plugin Loader

This spec does NOT introduce a plugin loader, dynamic discovery, or
`importlib`-based adapter resolution. First-party adapters are **statically
imported Python modules** inside the runtime package. Custom adapters are
**separate executables** (§4), never loaded as Python modules by the daemon.

---

## 4. Custom Adapter Supply and Trust

### 4.1 Hard Trust Boundary

> **A custom adapter is a separately installed executable/wrapper registered
> by declarative path/hash/version/capabilities. It communicates ONLY through
> the standard data contract (§2). It must NOT cause the daemon to import,
> discover, or execute arbitrary third-party Python modules.**

### 4.2 Proposed Registration Model

A custom adapter is registered as a **standalone executable** that the daemon
invokes as a subprocess — exactly like a custom CLI, but with a fixed,
adapter-specific argv contract:

```yaml
# Proposed: entry in a future "adapter store" (e.g., ~/.happyranch/adapters.yaml)
# DESIGN ONLY — file does not exist yet.

adapters:
  my-custom-adapter:
    executable: /usr/local/bin/hr-adapter-my-custom
    executable_hash: "sha256:abc123..."        # verified at launch
    version: "1.2.0"
    contract_version: 1                         # which AdapterInput/Output versions it speaks
    capabilities:
      - token_metering
    workspace_adapter: pi                       # which workspace prep to use
    registered_at: "2026-07-24T22:00:00Z"
    registered_by: "founder"
```

### 4.3 Adapter Executable Contract

The daemon launches the custom adapter as a subprocess with the
`AdapterInput` JSON on **stdin** and reads the `AdapterOutput` JSON from
**stdout**. The adapter process has a fixed lifetime: read stdin, do work,
write stdout, exit. No persistent process, no socket, no shared memory.

```
daemon ──[stdin: AdapterInput JSON]──→ adapter executable ──→ subprocess (actual CLI)
daemon ←──[stdout: AdapterOutput JSON]── adapter executable
```

This is a **process boundary**, not an in-process import. The adapter's
stdout is parsed by the daemon's generic output parser (same pattern as
`_parse_generic_cli_usage`). The adapter executable is responsible for
launching the actual agentic CLI and translating its output into the
standard result contract.

**Contract-reference endpoint (THR-107 seq184).** The canonical v1
``AdapterInput``/``AdapterOutput`` schemas are served at runtime by
``GET /api/v1/runtime/adapters/contract-reference``, accessible during
registration through the existing scoped registration-token posture on
loopback (adapter-purpose ``hrreg_`` token, read-only). Candidates
implementing adapter wrappers must fetch this endpoint and follow the
**server-derived schema** — the schemas are generated from the shipping
Pydantic models and are the authoritative external representation.

**Canonical daemon-managed adapter path (THR-107 seq339/340).** The
contract-reference response also returns:

- ``canonical_directory`` — the absolute path to
  ``<daemon-home>/adapters/``. Created with restrictive 0o700 owner-only
  mode if newly created; rejects symlinks on the adapters directory or the
  wrapper path.
- ``required_executable_path`` — the exact absolute canonical path where
  the adapter wrapper MUST live
  (``<daemon-home>/adapters/<canonical-adapter-id>``). The filename is the
  canonical adapter ID itself (lowercase alnum/hyphen only).

The scoped submission route (``POST /runtime/adapters/submit``) validates
``body.executable`` is exactly ``required_executable_path`` — rejecting
non-absolute paths, foreign directories, traversal spellings, alternate
filenames, and symlink escapes with an actionable 422 error that names the
required path and keeps the token retryable.  The registration seam
(``register_custom_adapter`` with ``intended_profile_name``) independently
rechecks so a route-only check cannot be bypassed.  This enforcement
applies to new scoped submissions and scoped re-registrations, but **not**
to dependency records (existing absolute-path/hash-pinned rules remain) or
the master-bearer ``/register`` route (operational/recovery path unchanged).

**Slice 1A mint authority foundation (TASK-4687).** Before the later direct
connection lifecycle, a normal master-authenticated runtime
adapter-purpose token mint may persist an exact first-party workspace adapter
with its intended profile/name and a server-derived wrapper destination in a
separate daemon-global SQLite store. The store contains only a
domain-separated one-way registration-token fingerprint and nonsecret mint
provenance; it is neither YAML/registry authority nor a projection. Its sole
state begins ``minted_nonlaunchable``. Slice A's one loopback,
registration-token-only direct-connect route validates a server-owned canonical
wrapper and strict v2 manifest, then stores only a nonlaunchable
receipt/event/artifact snapshot. If the final registration-token commit fails,
it compensates that receipt/event boundary. It does not create an
adapter/profile, change PENDING submission, choose a launcher, run a process,
or claim Connected. Direct projection, COMMITTED eligibility, Popen fencing,
and final UI simplification remain later serial slices.

**THR-160 direct-connect behavioral conformance.** Trusted commit/projection
(never receipt-only ``/runtime/custom-cli/connect``) invokes the candidate's
ordinary v1 stdin/stdout wrapper path once with a unique bounded opaque canary
inside ``AdapterInput.prompt`` before any adapter/profile persistence. The wrapper
forwards the entire normal v1 prompt through one real provider invocation,
obtains a genuine terminal provider response, and owns the ``AdapterOutput``
envelope; the provider must not construct it. A fabricated/static success is
not proof. The terminal ``AdapterOutput`` must be schema-valid, successful and
returncode-consistent, echo that HappyRanch invocation ID, identify the canonical
adapter, and include the complete opaque canary in canonical ``result.text``.
The short probe guides no optional tool use or workspace exploration, but does
not enforce or collect telemetry about those provider-internal actions; normal task behavior
is unchanged. Provider
``agent_session_id`` is optional and resume-only; when available it must be
returned faithfully, but it cannot substitute for HappyRanch invocation-identity
proof. Malformed/empty/absent output, timeout,
provider-declared error, or missing/wrong canary fails closed with no durable
adapter/profile/registry residue. Failure records retain only a bounded
category, never candidate stdout/stderr/errors or the canary. This is a
direct-flow gate only; legacy/operator registration retains its existing
shape-validation semantics. ``token_usage`` remains optional unless the
candidate declares the established ``token_metering`` capability and provides
trustworthy canonical usage.

**THR-160 corrected-artifact retry and immutable-snapshot validation.**
A direct-connect terminal failure is historical immutable evidence, not a
registration invitation. Two retry paths exist:

1. **Corrected-artifact retry (same-token).** A first candidate that fails
   only the conformance probe leaves the canonical ledger in
   ``failed_retryable`` with ``retry_eligible: true``. The candidate CLI may
   rerun the *existing* generated prompt with genuinely changed wrapper/child
   artifacts before the original 30-minute expiry; ``/connect`` admits exactly
   one such corrected candidate. Unchanged or merely reordered artifacts are
   refused with an indefinite, non-consuming ``409 Duplicate``. There is no
   cooldown. A second terminal failure closes the lifecycle as ``exhausted``
   (no further candidates); expiry closes it as ``expired``; both are
   nonretryable. Terminal legacy v0 operations that predate the THR-160
   candidate ledger are atomically backfilled as a failed ordinal-1 candidate
   on store open. Only a trusted ``conformance_probe_failed`` terminal category
   leaves the backfilled parent ``open`` so one genuinely changed corrected
   candidate is admitted as ordinal 2 and the two-candidate cap still refuses a
   third candidate. Every other terminal category
   (``profile_binding_failed``, ``invalid_manifest``, malformed or integrity
   failures, or any reason other than the approved conformance failure) is
   backfilled with the parent already ``failed``: no corrected candidate is ever
   admitted, the original legacy rows and identity history are retained, and
   identical/reordered replay is rejected non-consumingly. This path never calls
   ``POST /runtime/custom-cli/{operation_id}/retry``, never replays a generic
   token, and never requires ``/forget`` first.

2. **Immutable-snapshot validation.** Master-bearer
   ``POST /runtime/custom-cli/{operation_id}/retry`` is permitted only for a
   terminal ``failed`` projection and uses a separate durable attempt lifecycle.
   It retrieves the original receipt's persisted wrapper path/SHA and every
   child path/SHA, independently repeats the intake/launch path, type,
   executable, no-symlink, and hash checks, and fails closed before probing on
   any missing, duplicate, unusable, or mismatched snapshot fact. It accepts no
   user artifact fields, mutable adapter record, ambient PATH lookup, new
   candidate manifest, or token replay. Every outcome appends nonsecret
   category-only lifecycle evidence; no retry overwrites the original failed
   projection/reason or removes historical failure events. Only a successful
   identical bounded probe may bind through the ordinary direct adapter/profile
   persistence primitives with their normal compensation. Status may
   communicate that resulting live connection to existing consumers, but must
   retain explicit historical-failure fields rather than claiming the
   projection itself became committed.

Both paths are bounded by: a two-candidate cap per direct-connect lifecycle;
durable retry after a generic initial consume or daemon restart; append-only
retention of the parent authority, accepted candidates, identity history,
receipts, operations, and events for the authority lifetime (cleanup via
``POST /runtime/custom-cli/{operation_id}/forget`` removes only the permitted
derived artifacts, failed projection row, and any retry-attempt row for that
operation); the legacy submit fence (``/connect`` is receipt-only and spawns no
subprocess); status redaction; and the canonical wrapper destination as the only
required prompt value.

### 4.4 Registration, Conformance, Provenance

| Step | Description | Gate |
|---|---|---|
| **Declare** | Operator provides executable path, version, capabilities. | Static validation: path exists, is executable, version string valid. |
| **Hash** | Daemon computes SHA-256 of the executable at registration time. | Stored durably; verified on every launch. Hash mismatch → launch blocked. |
| **Conform** | Adapter is invoked with a sample `AdapterInput`; daemon validates the `AdapterOutput` against the contract schema. | Must produce valid JSON matching the contract. Similar to `emit_envelope` conformance step (`registration_token.py:116-118`). |
| **Approve** | **Founder-gated.** The founder must explicitly approve the adapter registration via POST /api/v1/runtime/adapters/{adapter_id}/approve (D4). The approval request binds the exact durable artifact snapshot (executable path, SHA-256 hash, version, capabilities, contract_version, workspace_adapter). Any mismatch, missing adapter, non-pending state, or already-approved incompatible repeat fails before persistence. Exact-idempotence: same stored facts + same approval state → no-op. **THR-107 seq237**: For adapters with a nonempty ``intended_profile_name`` (the normal adapter-submission path), approval atomically approves the snapshot AND creates/binds that same named custom profile (``command_adapter_id: custom-adapter:<id>``) in one server transaction. Settings' single confirmation refetches durable state and shows Connected — no client-side bind follow-up needed. Fail closed: any binding failure rolls back the approval to PENDING. Adapters without ``intended_profile_name`` (master-bearer registration) are approved without auto-binding and report ``eligibility: recovery_ready`` — they retain explicit advanced Bind recovery via Settings (see Bind row below). | Adapter enters `approved` status with `approved_at` and `approved_by` provenance. For adapters with ``intended_profile_name``, the named custom profile is bound in the same transaction (``command_adapter_id: custom-adapter:<id>``). The adapter reports ``eligibility: already_bound``. For no-intended adapters, the adapter reports ``eligibility: recovery_ready`` — explicit Bind is required. |
| **Reject (PENDING removal)** | **Founder-gated.** The founder may reject and atomically remove a PENDING adapter via POST /api/v1/runtime/adapters/{adapter_id}/reject (THR-107 seq220). The reject request binds the exact same six material artifact facts as approval. Any mismatch, missing adapter, or non-PENDING state fails before persistence. No persisted rejected status — the PENDING entry is removed. No SQLite/schema change. | The PENDING durable entry is atomically deleted. Audit entry written (scope `adapter:<id>`, action `adapter_rejected`). If audit fails after durable removal, exact adapter entry is restored and 500 returned. |
| **Bind (recovery/legacy)** | **Management-gated.** An APPROVED adapter WITHOUT an ``intended_profile_name`` (master-bearer registration path), or a legacy APPROVED-but-unbound adapter from before seq237, must be explicitly bound to a profile via POST /api/v1/runtime/adapters/{adapter_id}/bind-profile. This is the advanced Bind recovery action — labeled as recovery/legacy in the UI. The browser client renders a shared RecoveryBindCard that invokes bind → server-poll verify → durable Connected (eligibility: `already_bound`). **THR-107 seq237**: Normal adapters with ``intended_profile_name`` are auto-bound during approval and do NOT require this step. | Profile is bound with `command_adapter_id: custom-adapter:<id>`. After bind + server confirmation, the adapter reports `eligibility: already_bound`. The durable UI must retain Connected entries — not filter/unmount them. |
| **Remove (APPROVED deletion)** | **Management-gated.** An APPROVED custom adapter may be removed via DELETE /api/v1/runtime/adapters/{id} with an exact snapshot of all material identity/binding facts. Rejects stale, re-registered, wrong-target, non-APPROVED, and profile-referenced snapshots. This is the separate approved-only removal path — distinct from the PENDING reject route above. | Adapter is removed from the durable store if not referenced by any profile. |
| **Register** | Daemon writes to the durable adapter store. | Atomic load-validate-save under a store-level reentrant lock (RLock) that serializes competing writes (approval + rejection + other registrations) to the same adapters.yaml file. The lock is acquired before the durable reload at the commit boundary and released after the atomic temp-file replace. |

**Migration story (seq339/340):** Existing APPROVED adapters at arbitrary
(non-canonical) locations remain hash-valid and launchable — no automatic
migration, invalidation, or rewriting occurs. An operator may intentionally
create a separately scoped named registration in the managed location and
migrate/bind under existing founder gates while the old record remains until
explicitly retired. Enforcing canonical placement on the master-bearer
``/register`` route requires a separate founder authorization/contract
decision.

### 4.5 Change Detection

- **Hash verification on every launch**: the stored `executable_hash` is
  compared against the current file hash before each adapter invocation.
  Mismatch → launch blocked with an actionable error ("adapter executable
  changed since registration; re-register to update the hash").
- **No auto-update**: the daemon never silently accepts a modified
  executable.
- **Re-registration**: updating an adapter executable requires explicit
  re-registration (operator action).

### 4.6 Hard Gates (Founder-Gated, Not Presumed)

The following are **explicitly founder-gated** and this spec does NOT
presume they are authorized:

1. **Executable permission expansion**: a custom adapter executable may
   need additional filesystem/network permissions beyond the baseline
   `happyranch` allow rule. Any expansion of allow-rules for adapter
   subprocesses is founder-gated.
2. **Sandbox rule changes**: if a custom adapter executes a CLI that needs
   a different sandbox posture, the sandbox rule change is founder-gated.
3. **Permission surface for adapter subprocess**: what the adapter child
   process is allowed to do (network access, filesystem writes beyond
   workspace) is founder-gated.

---

## 5. Lifecycle Ownership and Error Semantics

### 5.1 Ownership Table

| Responsibility | Owner | Notes |
|---|---|---|
| Workspace preparation | Workspace adapter (selected by `adapter_id`) | Bootstrap files, permission config, skills injection. Unchanged from current. |
| Executor profile resolution | `ExecutorRegistry.get_profile()` → `build_executor()` | Resolves profile name → adapter reference. Currently hard-coded; target is data-driven from catalog. |
| Adapter instantiation | Adapter catalog | Maps `adapter_id` + `adapter_version` → adapter class. First-party: static import. Custom: subprocess spawn. |
| Argv construction | Adapter implementation | Provider-specific, opaque to orchestration. |
| Subprocess launch | `_run_command()` (`executors.py:607`) | Shared across ALL adapters. Handles Popen, communicate, timeout, rate-limit detection. |
| Stdout/stderr capture | `_run_command()` | `proc.communicate()` → `full_stdout`, `full_stderr`. |
| Output parsing | Adapter implementation | Provider-specific. First-party: native parsers. Custom: envelope parser. |
| Result normalization | Adapter → `ExecutorResult` | Every adapter produces the same `ExecutorResult` shape. |
| Token usage persist | `database.insert_token_usage()` | Called by `run_step` after `executor.run()` returns. |
| Cancellation / timeout | `_run_command()` → `proc.kill()` on `TimeoutExpired` | Process-group ownership via Popen. |
| Transient error classification | `_run_command()` → `rate_limited` detection | `is_rate_limit_signature()` (`executors.py:594`). |
| Permanent error classification | `_run_command()` → `returncode != 0` | Non-zero exit → `success=False`; no token row. |
| Retry (rate-limit) | `ProviderThrottle.run()` (`throttle.py`) | Re-launches `_launch` closure on rate-limit detection. |
| Resume (session continuity) | `ExecutorResult.agent_session_id` | Set by adapter's session-id parser; consumed by `run_step` for next launch's `--resume`. |
| Audit events | `run_step` + `audit_logger` | Unchanged — audit shape is independent of adapter boundary. |
| Process-group cleanup | `_run_command()` | `proc.kill()` + `proc.communicate()` drain on timeout. |

### 5.2 Lifecycle Sequence Diagram

```
┌──────────┐     ┌──────────────┐     ┌──────────┐     ┌────────────┐
│ run_step │     │build_executor│     │  Adapter  │     │_run_command│
└────┬─────┘     └──────┬───────┘     └─────┬──────┘     └─────┬──────┘
     │                  │                   │                   │
     │ build_executor() │                   │                   │
     │─────────────────→│                   │                   │
     │                  │                   │                   │
     │                  │ registry.resolve()│                   │
     │                  │──────────────────→│                   │
     │                  │                   │                   │
     │                  │  adapter instance │                   │
     │                  │←──────────────────│                   │
     │                  │                   │                   │
     │  executor        │                   │                   │
     │←─────────────────│                   │                   │
     │                  │                   │                   │
     │ executor.run(prompt, workspace, ...) │                   │
     │─────────────────────────────────────→│                   │
     │                  │                   │                   │
     │                  │                   │ build_argv(input) │
     │                  │                   │─────────┐         │
     │                  │                   │←────────┘         │
     │                  │                   │                   │
     │                  │                   │ _run_command(cmd) │
     │                  │                   │──────────────────→│
     │                  │                   │                   │
     │                  │                   │                   │ Popen+communicate
     │                  │                   │                   │────────┐
     │                  │                   │                   │←───────┘
     │                  │                   │                   │
     │                  │                   │  full_stdout      │
     │                  │                   │←──────────────────│
     │                  │                   │                   │
     │                  │                   │ parse_output()    │
     │                  │                   │────────┐          │
     │                  │                   │←───────┘          │
     │                  │                   │                   │
     │  ExecutorResult  │                   │                   │
     │←─────────────────────────────────────│                   │
     │                  │                   │                   │
     │ insert_token_usage(result.token_usage)                   │
     │────────┐         │                   │                   │
     │←───────┘         │                   │                   │
     │                  │                   │                   │
```

### 5.3 Workspace Adapter Selection vs Command Adapter Selection

These are **separately composable** — the workspace adapter (which writes
bootstrap files and configures permissions) and the command adapter (which
builds argv and parses output) may differ:

- **Built-in profiles**: the workspace adapter and command adapter are the
  same (e.g., `claude` → Claude workspace + Claude executor).
- **Custom profiles with `adapter: pi`**: the command adapter is
  `generic-cli` (template-based), but the workspace adapter is `pi`
  (AGENTS.md, no permission file).
- **Custom profiles with `adapter: claude`**: the command adapter is
  `generic-cli`, but the workspace adapter is `claude` (CLAUDE.md,
  settings.json, `--allowedTools`).

**Never silently collapse permissions.** A profile that declares
`adapter: claude` (workspace = Claude permission surface) but uses the
`generic-cli` command adapter does NOT inherit Claude's `--allowedTools`
generation — the command adapter has no permission-surface knowledge. The
workspace adapter controls the permission files; the command adapter
controls the subprocess launch. Crossing these without explicit intent
would be a security bug.

---

## 6. Manifest / Profile Model

### 6.1 Proposed Profile Fields and Namespaces

```yaml
# Proposed: what a registered profile carries in the target state.
# DESIGN ONLY — does not exist yet.

profile:
  identity:
    name: "claude"                          # string, required. Lowercase, unique.
    kind: "builtin"                         # "builtin" | "custom"
    display_name: "Claude Code"             # string, optional. Human-readable.

  adapter:
    adapter_id: "claude"                    # string, required. References a registered adapter.
    adapter_version: "1.0.0"               # string, required. Pinned version.
    capabilities:                           # [string], derived from adapter catalog.
      - token_metering
      - session_resume
      - model_select

  workspace:
    workspace_adapter: "claude"             # string, required. Which workspace prep to use.
    readiness_marker: ".claude/skills/start-task/SKILL.md"
    bootstrap_file: "CLAUDE.md"             # "CLAUDE.md" | "AGENTS.md"

  launch:
    command: null                           # string | null. For custom profiles only; null for builtins.
    argv_template: null                     # [string] | null. For custom profiles only.
    model_arg: ["--model", "{model}"]        # [string] | null.

  result_contract:
    contract_version_min: 1                 # int. Min supported result contract version.
    contract_version_max: 1                 # int. Max supported result contract version.

  provenance:
    registered_at: "2026-07-24T22:00:00Z"
    registered_by: "founder"
    source: "bundled"                       # "bundled" | "runtime_store" | "custom_adapter"
```

### 6.2 Current `ExecutorProfile` (for reference)

The current `ExecutorProfile` dataclass (`executor_registry.py:90-116`)
carries these fields:

```python
@dataclass(frozen=True)
class ExecutorProfile:
    name: str
    kind: str = "builtin"
    adapter_id: str = "claude"              # ← misleading (see §6.3)
    readiness_marker_fragment: str = ".claude/skills/start-task/SKILL.md"
    argv_template: list[str] | None = None
    command: str | None = None
    model_arg: list[str] | None = None
```

The target profile model (above) adds: `adapter_version`, `capabilities`,
`bootstrap_file`, `contract_version_min`/`max`, `provenance`, and
optionally `display_name`. It does NOT remove any current fields — it
extends them.

### 6.3 The `adapter_id` Problem → RESOLVED (D6, 2026-07-27)

**D6 COMPLETED (THR-107 founder seq115).** The migration established two explicit
canonical fields:

| Field | Purpose |
|---|---|
| `workspace_adapter_id` | Selects workspace preparation (CLAUDE.md vs AGENTS.md, permission files). Canonical field on `ExecutorProfile`. |
| `command_adapter_id` | Selects which command adapter builds argv and parses result output. Built-in profiles carry their own first-party adapter (`claude`/`codex`/`opencode`/`pi`); custom profiles may be `"generic-cli"` (template-based generic CLI) or `"custom-adapter:<id>"` (bound to a separately registered, founder-approved, hash-verified custom adapter executable — D7B, §4, subprocess-only, mandatory v1 AdapterInput/AdapterOutput, D5 baseline-only posture). |

**Deprecated aliases** (preserved for read compatibility):
- `adapter_id` / `adapter` → deprecated alias for `workspace_adapter_id`
- `command_adapter` → deprecated alias for `command_adapter_id`

**Dual-read behavior:**
- Legacy-only values (e.g., only `adapter_id` set) → canonical field mirrors it
- Canonical-only values (e.g., only `workspace_adapter_id` set) → deprecated alias mirrors it
- Agreeing dual values → both preserved
- Conflicting values (both set to different non-default values) → `ValueError` BEFORE any durable-store mutation, registry mutation, audit write, or token consumption

**Response contract:** Registration and list response models carry BOTH canonical (`workspace_adapter_id`, `command_adapter_id`) and deprecated (`adapter_id`, `command_adapter`) fields. Existing request bodies using legacy keys work; canonical request fields work; mixed conflicting bodies 422.

**No auto-mutation:** Existing machine-global custom profiles are never silently rewritten — legacy keys are read compatibly without altering the stored profile. New writes may carry compatibility aliases alongside canonical fields for downgrade safety.

**Built-in profiles:** Carry `command_adapter_id` == `workspace_adapter_id` (each built-in has its own first-party command adapter). Exact pre-existing workspace markers and permission-bearing surfaces are unchanged.

**Rollback/re-registration path:** Legacy profile operators need no immediate action. When they next re-register, the canonical fields are available. No mandatory result-envelope enforcement (D7 is later).

### 6.4 Manifest Storage

| Artifact | Storage | Writable at runtime | Notes |
|---|---|---|---|
| Adapter catalog | `runtime/adapters/catalog.yaml` (bundled) | No — shipped with release | First-party adapters only |
| Built-in profiles | `ExecutorRegistry._register_builtins()` (hard-coded) | No | Four profiles registered at import time |
| Custom CLI profiles | `~/.happyranch/executor_profiles.yaml` (machine-global) | Yes — via registration flow | Current state; unchanged |
| Custom adapter registry | `~/.happyranch/adapters.yaml` (proposed, does not exist) | Yes — founder-gated | New file for custom adapter executables |

---

## 7. Migration and Rollback

### 7.1 Phases

#### Phase 0: Inventory + Shadow (no behavior change)

- **Inventory every adapter seam**: For each of the five executor classes,
  document the exact argv construction, output parser, permission flags,
  and workspace-prep dependency.
- **Shadow contract tests**: Write a test class that, for each executor
  profile, captures the `cmd` list produced today and asserts it matches
  a pinned baseline. This is the regression gate for later extraction.
- **No code change.** Purely documentation + test additions.

#### Phase 1: First-Party Adapter Encapsulation Behind Compatibility Facade

- Extract each built-in executor's `build_argv()` logic into a
  `runtime/adapters/<name>.py` module.
- The executor classes (`ClaudeExecutor`, etc.) become thin shells that
  delegate to their adapter.
- `build_executor()` gains a data-driven path: profile → adapter catalog
  → adapter instance, but the `if/elif` chain remains as a **fallback**.
- **Compatibility invariant:** Every executor produces bit-identical argv
  to the Phase 0 pinned baseline.
- **Rollback:** revert the extraction commit; the `if/elif` chain is still
  the primary path.

#### Phase 2: Custom Declarative / Wrapper Profiles

- The `generic-cli` adapter becomes a first-party adapter alongside the
  four built-in adapters.
- Custom profiles with `adapter: pi` (or any workspace adapter) use the
  `generic-cli` command adapter.
- **No change to custom profile storage or registration.** The
  `GenericCliExecutor` class becomes a shell around the `generic-cli`
  adapter.

#### Phase 3: Opt-In Activation

- A new profile field `command_adapter` allows a custom profile to
  explicitly select a different command adapter (e.g., a future custom
  adapter registered via §4). Default = `generic-cli`.
- Built-in profiles cannot change their command adapter.
- **Rollback:** set `command_adapter` to `null` or remove the field; the
  default `generic-cli` adapter takes over.

#### Phase 4: Default Change / Removal (Founder-Authorized Only)

- **IMPLEMENTED** (TASK-3414, THR-107 D10/D11). After all built-in
  executors were adapter-backed and validated (D2/D8/Phase-2/D9 complete),
  the `if/elif` chain in `build_executor()` was removed and replaced with
  a static data-driven factory dict derived from the D8 authoritative
  built-in catalog (`runtime/adapters/__init__.py:_BUILTIN_CATALOG`).
- The factory dict maps each built-in profile name to a factory callable;
  no imperative per-provider dispatch or `if profile.name == …` chain
  remains.
- **Rollback:** revert the removal commit (TASK-3414).

### 7.2 Read/Write Compatibility

| Phase | Read old profile | Write new profile | Read new profile |
|---|---|---|---|
| 0–2 | Unchanged — current `ExecutorProfile` fields only | Unchanged — current fields only | New fields ignored if absent |
| 3 | Old profiles continue working; `command_adapter` absent → default | New custom profiles MAY set `command_adapter` | New field parsed; old profiles get default |
| 4 | All profiles must be readable (back-compat) | Built-ins: immutable. Customs: `command_adapter` optional. | All fields supported |

### 7.3 Dual-Read/Write

Not needed. New fields are additive-only. The `command_adapter` field
defaults to a safe value when absent. No mutation of stored profiles
occurs automatically.

### 7.4 No Auto-Mutation

Stored profiles are **never auto-mutated**. Migration from current
`ExecutorProfile` to the extended model happens at read time —
`from_legacy_profile()` constructs the extended profile with defaults
for new fields. The durable store (`executor_profiles.yaml`) is written
only through the existing registration flow.

---

## 8. Versioning, Conformance, Observability, and Tests

### 8.1 Compatibility Negotiation

- **Adapter → daemon:** Each adapter declares `contract_version_min` and
  `contract_version_max` (the range of `AdapterInput` versions it accepts).
  The daemon selects the highest version ≤ its own.
- **Unsupported version → hard failure:** If the daemon's minimum input
  version exceeds the adapter's maximum, the launch fails with a clear
  error (not a silent fallback).
- **Result contract version:** `AdapterOutput.contract_version` tells the
  daemon which version of the result contract the adapter produced. The
  daemon validates this is a version it understands.

### 8.2 Conformance Fixtures

A deterministic conformance fixture is a JSON file checked into the repo:

```
tests/adapters/fixtures/
  claude_input.json      → claude_output.json
  codex_input.json       → codex_output.json
  opencode_input.json    → opencode_output.json
  pi_input.json          → pi_output.json
  generic_cli_input.json → generic_cli_output.json
```

Each fixture pair captures a known-good adapter invocation. CI runs a
conformance test: given `*_input.json`, does the adapter produce output
matching `*_output.json`? This gates adapter behavior changes.

### 8.3 Test Matrix

| Test level | Claude | Codex | OpenCode | Pi | Kimi-like custom CLI |
|---|---|---|---|---|---|
| **Unit: adapter argv** | Assert `build_argv()` produces exact `cmd` list matching pinned baseline | Same | Same | Same | Assert template substitution produces expected argv |
| **Unit: adapter parser** | Feed known stdout → assert `parse_output()` extracts correct `TokenUsage` | Feed known JSONL → same | Feed known JSON → same | Feed known JSON → same | Feed envelope stdout → same |
| **Unit: adapter contract** | Feed `AdapterInput` → assert `AdapterOutput` schema valid | Same | Same | Same | Same |
| **Contract: conformance fixtures** | `claude_input.json` → exact `claude_output.json` | Same | Same | Same | `generic_cli_input.json` → exact output |
| **Integration: full launch** | Real subprocess launch (if CLI available in CI) | Same | Same | Same | Real subprocess with envelope-emitting test CLI |
| **E2E: task flow** | `run_step` → executor → completion callback → DB row | Same | Same | Same | Same |
| **Rollback: extraction revert** | Revert adapter extraction commit → all tests still pass | Same | Same | Same | Same |
| **Adversarial: trust boundary** | N/A (first-party) | N/A (first-party) | N/A (first-party) | N/A (first-party) | Hash mismatch → blocked; malformed output → forensic TokenUsage; executable not found → actionable error |

### 8.4 Unknown Vendor Behavior

The following vendor behaviors are **unknown** and must be flagged, not
invented:

| Vendor | Unknown |
|---|---|
| Claude | Whether `--output-format json` will remain stable; whether Claude Code's stdout format will change |
| Codex | Whether `exec --json -` will continue to emit `{"type":"turn.completed"}` as the terminal event carrying the cumulative `usage` object (confirmed against codex-cli 0.137.0/0.139.0); whether the sandbox flag names will change |
| OpenCode | Whether `--format json` output shape is stable; whether `--dir` flag behavior is consistent across versions |
| Pi | Whether `--mode json` will remain the structured output flag; whether output fields will change |
| Kimi-like custom CLI | Whether it can emit the current v1 sentinel envelope; whether it reports session ids; whether `--resume` is supported |

For each, the adapter is the **translation layer** — when a vendor
changes its output format, the adapter is updated (with a version bump),
not the orchestration layer.

---

## 9. Decision Log

### 9.1 Founder-Approved / Established Facts

| # | Fact | Source |
|---|---|---|
| F1 | Custom CLIs may opt into token metering via v1 sentinel-delimited JSON envelope (`__HR_ENVELOPE_BEGIN__` / `__HR_ENVELOPE_END__`) | THR-107 seq57/58; `docs/superpowers/specs/2026-07-19-custom-cli-adapter-envelope-design.md` |
| F2 | Envelope is optional — absence preserves existing behavior (no token accounting) | Phase 1 shipped spec §5.1 |
| F3 | `emit_envelope` conformance step is mandatory for new registration tokens | `registration_token.py:116-118`; THR-107 Phase 1 |
| F4 | `TokenUsage.total` excludes cache reads; nullable tolerance; model-null → back-filled to provider label | `runtime/models.py:316`; `executors.py:698-699` |
| F5 | All five executors converge through shared `_run_command` → `ExecutorResult` | `executors.py:607`; consolidation assessment TASK-3270 §1.2 |
| F6 | Workspace preparation is selected by `adapter_id` and is provider-specific | `executor_registry.py:104`; THR-052 |
| F7 | Custom profiles are machine-global, stored in `~/.happyranch/executor_profiles.yaml` | THR-107 seq71; TASK-3269 |
| F8 | `command` must equal `argv_template[0]` at registration time | Issue #490; TASK-3269; `executor_registry.py:300-344` |
| F9 | `ExecutorRegistry` is CRITICAL: 83 impacted, 13 direct dependents, 23 affected processes | GitNexus @ `1fb1928b` (this task) |
| F10 | The stronger unified adapter-runtime direction is founder-approved | THR-107 seq84 (this task's brief) |

### 9.2 Proposed but NOT Approved (D1-D5, D7-D12 remain pending)

| # | Proposal | Status |
|---|---|---|
| P1 | Extract `build_argv()` from each executor class into first-party adapter modules | Proposed this spec §3.3; requires separate founder-approved build task |
| P2 | Create adapter catalog at `runtime/adapters/catalog.yaml` | Proposed this spec §3.1; requires separate founder-approved build task |
| P3 | Data-driven `build_executor()` via adapter catalog instead of hard-coded `if/elif` chain | **D10/D11 (TASK-3414, THR-107 seq84, July 2026):** static factory dict replacing the if/elif chain. Full adapter-catalog-based dispatch (as originally proposed in §3.1–3.2) remains unimplemented. |
| P4 | Add `result.text` and `adapter_metadata` fields to `ExecutorResult` | Proposed this spec §2.2; additive-only, backward-compatible |
| P5 | Custom adapter executable subprocess model (separate process, stdin/stdout contract) | Proposed this spec §4; requires founder approval for the entire custom-adapter track |
| P6 | Custom adapter registration: executable path + hash + version + capabilities | Proposed this spec §4.2; requires founder approval |
| P7 | Extended `ExecutorProfile` with `adapter_version`, `capabilities`, `bootstrap_file`, `contract_version`, `provenance` | Proposed this spec §6.1; additive-only |
| P8 | `workspace_adapter` vs `command_adapter` split in profile model | ~~Proposed~~ → **APPROVED and IMPLEMENTED as D6** (PR TASK-3434, founder seq115). Canonical `workspace_adapter_id` + `command_adapter_id` with deprecated aliases, dual-read, conflict detection, no auto-mutation. |
| P9 | Migration phases 0–4 (inventory → encapsulation → opt-in → default change) | Proposed this spec §7; each phase requires explicit founder authorization |

### 9.3 Exact Founder-Gated Decisions Required Before Any Build

| # | Decision | Context | Why founder-gated | Status |
|---|---|---|---|---|
| **D1** | Approve Phase 0 (inventory + shadow contract tests) as a docs+test-only task? | §7.1 Phase 0 | No code change — but establishes the regression baseline for all subsequent extraction | **DONE** (PR #497, TASK-3347) |
| **D2** | Approve Phase 1 (first-party adapter encapsulation behind compatibility facade)? | §7.1 Phase 1, §3.3 | Touches `ExecutorRegistry` (CRITICAL, 83 impacted) and all five executor classes. Behavior-preserving but high blast radius. | **DONE** (PR #499) |
| **D3** | Approve the custom-adapter executable model (separate process, stdin/stdout contract, hash verification, conformance)? | §4 | Introduces a new trust boundary: daemon spawns third-party executables. Changes the security posture. | **IMPLEMENTED (pending-only store, auth-gated, bounded conformance)** (PR #508, TASK-3481 → TASK-3500). The registration store, validation, SHA-256, bounded stdin/stdout conformance probe, exact contract-version-1 enforcement, PENDING-only persistence, runtime bearer-auth gating on all three endpoints, pending-adapter resolution rejection, and whitespace-version rejection are implemented. D4 approval gate implemented in TASK-3501. D5 permission/sandbox, D7 profile binding, and D12 final contract remain pending. |
| **D4** | Approve custom-adapter registration requiring explicit founder approval? | §4.4 | Gates who can install executable adapters. | **IMPLEMENTED** (THR-107 D4, TASK-3501 + REVISE TASK-3503, July 2026). Explicit founder-gated approval route POST /api/v1/runtime/adapters/{adapter_id}/approve with exact snapshot binding (all material identity facts compared), PENDING→APPROVED transition, approved_at + approved_by provenance, exact-idempotence, and on-disk hash verification at resolve time. **THR-107 seq220 (TASK-3803, July 2026) added:** (a) founder-gated PENDING rejection route POST /api/v1/runtime/adapters/{adapter_id}/reject with same exact 6-field snapshot binding, atomically removes the PENDING entry (no persisted rejected status, no SQLite/schema change); (b) browser Settings > Executors > Pending Adapter Approvals area with approve/reject controls showing exact SHA-256 hash confirm/cancel; (c) PENDING excat-snapshot approve transitions to the shared RecoveryBindCard (bind → server poll → durable Connected, reusable from ConnectFlow as a Settings component). The approval persistence and registration write share a store-level reentrant lock (RLock) so that load-validate-save is atomic: a stale approval that sees old facts before a competing re-registration wins the lock will reload at the commit boundary, detect the mismatch, and reject without overwriting the new entry. No SQLite, auth semantic, permission/sandbox, or profile-binding changes. |
| **D5** | Approve any allow-rule or sandbox-rule changes for custom adapter subprocesses? | §4.6 | Permission model changes are founder-gated per protocol charter. | **PENDING** (founder-gated, not in D6 scope) |
| **D6** | Approve `adapter_id` rename to `workspace_adapter_id` + new `command_adapter_id` field? | §6.3 | Schema change; stored profiles carry `adapter_id` today. Migration implications. | **DONE** (PR TASK-3434, founder seq115) |
| **D7** | Approve when (if ever) v1 optional envelope becomes required for custom CLI registration? | Legacy spec §5.2; currently optional | Strands existing custom CLIs that don't emit the envelope. | **D7A IMPLEMENTED** (THR-107 D7A, TASK-3529, July 2026). **New registrations and re-registrations through either shipping route (org-level and runtime-level) now durably record ``envelope_policy: "strict"``** — mandatory v1 envelope enforcement at the ``GenericCliExecutor`` launch/result seam. A strict profile whose stdout has a missing, malformed, invalid, or unknown-version envelope returns a deterministic failed ``ExecutorResult`` with actionable re-registration/verification remediation (tails preserved, forensic accounting intact). **Existing machine-global custom profile YAML entries that lack the new explicit ``envelope_policy`` field are LEGACY COMPATIBILITY entries**: no auto-write, no destructive migration, no SQLite change, and their pre-existing optional-envelope runtime behavior remains intact. The runtime profile list/response surface (``GET /api/v1/executors/runtime/profiles``) truthfully inventories strict vs legacy state via the ``envelope_policy`` field. **Operator path: list → verify v1 envelope locally → re-register to opt into strict enforcement**. Rollback means restore the legacy profile entry or revert the deployment. **D7B IMPLEMENTED** (PR TASK-3589, July 2026): Custom-adapter profile binding through ``command_adapter_id: "custom-adapter:<id>"`` with CustomAdapterExecutor subprocess launch path. GenericCLI D7A strict/legacy behavior unchanged. No D5 permission expansion. |
| **D8** | Approve adapter catalog manifest as the authoritative source for first-party adapters (replacing hard-coded registration in `_register_builtins()`)? | §3.1, §6.4 | Changes how built-in profiles are defined — currently `_register_builtins()` at `executor_registry.py:145-176`. | **DONE** (PR #500) |
| **D9** | Approve the `command_adapter` field on custom profiles as opt-in (Phase 3)? | §7.1 Phase 3 | Adds a new profile field; gates on how custom executors are invoked. | **DONE** (PR #502) |
| **D10** | ~~Approve removal of the `if/elif` chain in `build_executor()`~~ **IMPLEMENTED** (TASK-3414, THR-107 seq84, July 2026). The chain was replaced with a static data-driven factory dict derived from the D8 authoritative catalog. Rollback: revert the removal commit. | §7.1 Phase 4 | THR-107 seq84 (July 2026) | **DONE** (as part of PR #503, merged) |
| **D11** | ~~Approve rollout/rollback authority~~ **IMPLEMENTED** (TASK-3414, THR-107 seq84, July 2026). Rollback across all registered profiles: revert the removal commit. | §7.1 Phase 4, §7.4 | THR-107 seq84 (July 2026) | **DONE** (as part of PR #503, merged) |
| **D12** | Approve any protocol/05b or 05c rewrite, or any `ExecutorResult` contract-surface change? | §2.5, §8.2 footnote, Appendix C | `AdapterInput`/`AdapterOutput` are proposed **internal architecture contracts** only. No rewrite of protocol/05b or 05c, no public/stable external contract, and no `ExecutorResult` behavior implementation follows unless founder explicitly authorizes that later change. | **FINALIZED in D7B (PR #514, founder seq115, July 2026).** The stable v1 `AdapterInput`/`AdapterOutput` contract (§2 of this doc) is the canonical request/result contract for ALL custom adapter subprocesses. Key finalized invariants: (1) stdin receives exactly one v1 `AdapterInput` JSON object; (2) stdout MUST be exactly one v1 `AdapterOutput` JSON object; (3) ``contract_version`` in ``adapter_metadata`` MUST be the integer 1; (4) ``success`` and ``returncode`` MUST be consistent (success=true with returncode≠0 → rejected; success=false with returncode=0 → rejected); (5) oversized output >1MB → rejected; (6) non-JSON / non-object / missing ``adapter_metadata`` / wrong adapter identity → rejected; (7) D5 baseline-only — no Python import/discovery, no permission/sandbox expansion; (8) hash verified at EVERY launch — the check is inside the per-attempt launch closure so throttle retries re-verify the artifact before each Popen (TASK-3605 repair); (9) ``InvocationInfo`` receives truthful context for every launch kind (task, thread, wake, dream, schedule). No ``ExecutorResult`` field change. D12 protocol/05b, 05c, executor-guide, and envelope-design-spec documentation parity shipped in same PR (TASK-3605). The contract lives in ``runtime/orchestrator/adapter_contract.py`` as the authoritative definition. |
| **D13** | Approve custom-adapter profile binding and CustomAdapterExecutor launch? | §6.3, D7B brief | Binds a custom profile to exactly one APPROVED custom adapter through ``command_adapter_id: "custom-adapter:<id>"``. Introduces CustomAdapterExecutor as the subprocess launch path. | **IMPLEMENTED (PR TASK-3589, founder seq115, July 2026).** |

---

## Appendix A: Source Evidence

### A.1 GitNexus Impact Analysis (run at `origin/main` @ `1fb1928b`)

| Symbol | Risk | Impacted | Direct | Processes | Modules |
|---|---|---|---|---|---|
| `ExecutorRegistry` (class, `executor_registry.py:139`) | **CRITICAL** | 83 | 13 | 23 | 4 |
| `build_executor` (function, `executor_registry.py:350`) | **HIGH** | 12 | 2 | 0 | 3 |
| `GenericCliExecutor` (class, `executors.py:971`) | **MEDIUM** | 53 | 7 | 0 | 2 |
| `_run_command` (function, `executors.py:607`) | **MEDIUM** | 5 | 5 | 0 | 1 |

Full raw impact output is preserved in the task session. Summary:

- **`ExecutorRegistry` CRITICAL**: Direct callers include `build_executor`, `get_registry`, `reset_registry`, `validate_custom_profile_config`, `register_custom_profile`, `unregister_custom_profile`. Affected processes span agent management (set/approve/reject/create/list agent), executor registration (org + runtime routes), health readiness, settings, threads, skills, and all four spawn queues (wake, thread, schedule, dream). **Any refactor touching this class MUST be behavior-preserving.**
- **`build_executor` HIGH**: Called by `Orchestrator._build_executor` and `thread_runner._build_executor_for_provider`. Propagates to `_run_agent` (task), `run_wake`, `run_invocation`, `run_schedule`, `run_dream` — all five spawn contexts.
- **`GenericCliExecutor` MEDIUM**: Instantiated by `build_executor` for custom profiles. Imported by 7 direct files.
- **`_run_command` MEDIUM**: Called by ALL five executor `.run()` methods. Change here cascades to every executor.

### A.2 Files and Line Anchors Cited

| File | Key symbols | Lines |
|---|---|---|
| `runtime/orchestrator/executors.py` | `ExecutorResult`, `_run_command`, `_parse_generic_cli_usage`, `ClaudeExecutor`, `CodexExecutor`, `OpencodeExecutor`, `PiExecutor`, `GenericCliExecutor`, `_HR_ENVELOPE_BEGIN`/`_HR_ENVELOPE_END` | 24–56, 476–477, 480–545, 607–725, 757–1038 |
| `runtime/orchestrator/executor_registry.py` | `ExecutorProfile`, `ExecutorRegistry`, `_register_builtins`, `validate_custom_profile_config`, `build_executor`, `get_registry` | 90–116, 139–176, 262–344, 350–408, 330–340 |
| `runtime/models.py` | `TokenUsage` | 302–316 |
| `runtime/daemon/registration_token.py` | `DEFAULT_CONFORMANCE_STEPS` | 116–118 |
| `runtime/daemon/routes/executors.py` | `register_executor`, `runtime_register_executor` | 400–608, 680–838 |
| `runtime/daemon/routes/health.py` | `health_prereqs`, `_get_cli_binary` | 35, 110–158 |

### A.3 Current-vs-Target Assumptions

1. **All executors converge through `_run_command` → `ExecutorResult`.** Verified at `executors.py:607` — every executor class calls `_run_command` which returns `ExecutorResult`. This is the emergent shared interface; the target formalizes it.
2. **Provider-specific argv knowledge is currently in `build_executor` and `*Executor.run()`** — not in a shared adapter boundary. Verified at `executor_registry.py:370-408` (hard-coded executor dispatch) and `executors.py:757-1038` (per-executor `run()` methods).
3. **`adapter_id` currently means "workspace adapter"** — not "executor adapter." Verified at `executor_registry.py:104`. The term is misleading; the target proposes a split.
4. **No contradiction found.** Current inspection at `1fb1928b` confirms the premise that all executors converge through a standard `run(...) -> ExecutorResult` lifecycle via `_run_command`.

---

## Appendix B: Compatibility Matrix

| Feature | Claude | Codex | OpenCode | Pi | Custom CLI (v1 envelope) | Kimi-like (hypothetical) |
|---|---|---|---|---|---|---|
| Token metering | ✓ native parser | ✓ native parser | ✓ native parser | ✓ native parser | ✓ envelope parser | ✓ if emits envelope |
| Session resume | ✓ `--resume` | ✗ | ✗ | ✗ | ✗ (phase 2 deferred) | ✗ (unknown) |
| Model select | ✓ `--model` | ✓ `-m` | ✓ `-m` | ✓ `--model` | ✗ (founder-gated) | ✗ (unknown) |
| Permission surface | ✓ `.claude/settings.json` + `--allowedTools` | ✓ sandbox flags | ✓ `opencode.json` | ✗ (no HR-managed sandbox) | per `adapter_id` | per workspace adapter |
| Workspace prep | `CLAUDE.md` | `AGENTS.md` | `AGENTS.md` | `AGENTS.md` | per `adapter_id` | per workspace adapter |
| Result-envelope (v1) | N/A (native parser) | N/A (native parser) | N/A (native parser) | N/A (native parser) | ✓ optional | ✓ if CLI emits it |
| Target adapter | `ClaudeAdapter` | `CodexAdapter` | `OpencodeAdapter` | `PiAdapter` | `GenericCliAdapter` | `GenericCliAdapter` or custom adapter |

---

## Appendix C: Cross-Reference to Existing Specs

- **`docs/superpowers/specs/2026-07-19-custom-cli-adapter-envelope-design.md`**: The Phase 1 envelope design spec. This unified architecture spec is a **superset** that encompasses both the envelope (custom-CLI output) and the first-party adapter boundary. The envelope spec's v1 schema (§1), sentinel transport (§2), generic parser (§3), and backward-compatibility guarantees (§5) remain authoritative and unchanged.
- **`docs/agent-guides/agent-executors-and-permissions.md`**: Documents current executor behavior. This spec's target model aligns with and extends the guide's descriptions.
- **`protocol/05b-agent-runtime.md`** §Executor abstraction and §Custom CLI result-envelope: Protocol-level documentation of the executor interface. This spec proposes future protocol doc updates but does NOT change protocol/ now.
- **`protocol/05c-orchestrator.md`** §Executor result-envelope contract: Protocol-level documentation of the envelope parsing. Same treatment — future updates only.

---

## Appendix D: Scope Verification

This task is DESIGN ONLY. The deliverable is exactly two files:

1. This spec file: `docs/superpowers/specs/2026-07-24-unified-adapter-runtime-architecture.md`
2. A narrow 15-line cross-link addition in `docs/superpowers/specs/2026-07-19-custom-cli-adapter-envelope-design.md` that references this unified architecture spec. The cross-link makes **no normative Phase-1 change** — it adds only a contextual preface pointing to the superset unified-adapter doc while preserving all existing envelope-spec content unchanged.

No production code, protocol doc, schema, auth, permission, profile migration,
plugin loader, or test file is modified. The spec explicitly labels itself
"DESIGN ONLY — no implementation authorization" in the header and every
section that proposes a code change qualifies it as "proposed" or "future."

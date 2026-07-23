# Standard Daemon↔CLI Interface: Adapter Contract for Custom CLIs — Design Spike

**THR-107** | **2026-07-19** | **DESIGN ONLY — no production code, no protocol/ edits, no implementation PR**

## Interface Model

HappyRanch defines a **standard daemon↔CLI interface** with two halves:

### INPUT (daemon → CLI)
How the daemon hands over the prompt, timeout, and workspace to the CLI. This
half is **declarative**: the registered `argv_template` — a list of strings
with supported placeholders (`{prompt}`, `{timeout_seconds}`, `{workspace}`) —
fully specifies the CLI invocation. A single generic executor
(`GenericCliExecutor`, `runtime/orchestrator/executors.py:860`) substitutes
these placeholders at `:905-909` and spawns the subprocess. **No CLI-side code
is needed for input** — the daemon adapts declaratively via the template.

### OUTPUT (CLI → daemon)
How the CLI reports results back to the daemon: token usage, model identifier,
session id, and final response text. This half is the **result-envelope**
(§1) — a small, versioned JSON blob the CLI emits on stdout. One generic
best-effort parser (`_parse_generic_cli_usage`, replacing `usage_parser=None`
at `:920`) reads it — no per-CLI daemon-side code.

### What is an "Adapter"?

An **adapter** is whatever implements this interface for a given CLI:

- **Bundled CLIs** (Claude, Codex, OpenCode, Pi): HappyRanch ships the adapter
  **daemon-side**. Each built-in executor builds its own argv (the INPUT half
  — known per-CLI argument conventions) and includes a hand-written output
  parser (`_parse_{claude,codex,opencode,pi}_usage`) that extracts token usage
  from each CLI's unique structured stdout format (the OUTPUT half). No CLI
  modification was needed — the adapter lives entirely in the HappyRanch
  runtime.

- **Custom CLIs** (registered via `GenericCliExecutor`): the CLI implements the
  contract itself **CLI-side**. The INPUT half needs no CLI work (the
  `argv_template` is declarative and the daemon fills it). The OUTPUT half
  requires the CLI to emit the result-envelope — a single structured JSON
  blob. This is the spec a custom-CLI author implements.

### Why INPUT/OUTPUT Asymmetry?

The two interface halves are intentionally asymmetric because the **cost of
conformance** differs:

- **INPUT asymmetry (daemon adapts):** An existing CLI's argument parser is
  fixed — you cannot force a new standard INPUT format on the Claude CLI or
  the Codex CLI. Each has its own argv convention (`claude -p "<prompt>"`,
  `codex exec "<prompt>"`, etc.). The daemon absorbs this complexity: the
  `argv_template` is a thin per-CLI declaration, and the generic executor
  fills it. **Bundled CLIs don't need the declarative template** because their
  executors already know the argv shape; for custom CLIs the template is the
  entire input-adapter story.

  **Issue #490 / THR-107 seq71: command/template parity.** The profile
  declares TWO names for the executable:
  - ``command`` — the declared name, validated via ``shutil.which()`` at
    registration and used by ``/health/prereqs`` to derive ``present``
    status for custom profiles.
  - ``argv_template[0]`` — the executable ``GenericCliExecutor`` ACTUALLY
    launches as ``subprocess.Popen(cmd)[0]``.
  The canonical validation (``ExecutorRegistry.validate_custom_profile_config``)
  proves BOTH resolve to the identical canonical binary (PATH resolution +
  symlink/alias canonicalization). A mismatch or an unresolvable element 0
  is rejected with a clear ``ValueError`` → 4xx before any durable/audit
  side effect. The profile author must use the same executable name in both
  fields, e.g. ``{"command":"<your-cli>","argv_template":["<your-cli>",
  "--flag","{prompt}"],"adapter":"pi"}``. No auto-prepend, no automatic
  mutation of stored argv templates.

- **OUTPUT asymmetry (CLI conforms):** Emitting one extra structured JSON blob
  is cheap for a CLI to add — a few lines after the agent loop finishes. The
  sentinel-delimited envelope (§2) is a minor stdout addition, not a rewrite
  of the CLI's output format. So the CLI CAN conform on the OUTPUT half, and
  the daemon-side parser stays generic (one function for all custom CLIs).

This asymmetry is the core architectural insight: **the daemon adapts on
input via a declarative template; the CLI adapts on output by emitting one
structured envelope.** The envelope is the OUTPUT half of the standard
interface; the `argv_template` is the INPUT half.

## Goal

Custom (non-built-in) CLIs today run via `GenericCliExecutor`
(`runtime/orchestrator/executors.py:860`) but are **second-class**: they run fine,
but have **zero token accounting** and **no session continuity** because
`usage_parser=None` (`:920`). The 4 built-in executors (Claude, Codex, OpenCode,
Pi) each have a hand-written output parser that extracts token usage from the
CLI's structured stdout. Custom CLIs cannot retrofit their output into those
per-CLI shapes.

This spec defines the OUTPUT half of the standard daemon↔CLI interface — a
**small, versioned JSON result-envelope** that any custom CLI **may** emit.
One generic parser (`:920`, replacing `usage_parser=None`) reads it — no
per-CLI daemon-side code. The envelope is **optional**; absence is harmless
(current behavior). This upgrades custom CLIs to first-class spend visibility
without bundling per-CLI logic.

---

## 1. Output-Adapter Contract: Result-Envelope Schema

The envelope is a single JSON object the CLI emits in its stdout (see §2 for
transport). All fields except `envelope_version` are optional — the parser is
best-effort (§3).

```jsonc
{
  "envelope_version": 1,            // REQUIRED. Integer. Must be 1.
  "result": "<output text>",        // string, optional. The agent's final
                                    // response/result text (the "answer").
  "token_usage": {                  // object, optional. Maps 1:1 to TokenUsage model
                                    // (runtime/models.py:302). Every field nullable.
    "input_tokens": 1500,           // int|null
    "output_tokens": 420,           // int|null
    "cache_read_tokens": 300,       // int|null — cache HITS (NOT new consumption)
    "cache_creation_tokens": 0,     // int|null — cache WRITES
    "reasoning_tokens": null,       // int|null — thinking/reasoning tokens
    "model": "my-cli-v2",           // string|null — model identifier
    "usage_raw_json": "...",        // string|null — opaque raw payload for forensics
  },
  "model": "my-cli-v2",             // string, optional. Top-level model id.
                                    // If token_usage.model is absent but this is
                                    // set, the parser copies it in (see §3).
  "agent_session_id": "abc123",     // string, optional. The agent CLI's own
                                    // session id for resume (parity with built-ins'
                                    // --resume, see §6).
}
```

### 1.1 Field-name mapping: envelope → TokenUsage

The parser maps `envelope.token_usage` directly to `runtime/models.py:302`
`TokenUsage` fields with **identical key names**:

| Envelope key | `TokenUsage` field | Notes |
|---|---|---|
| `input_tokens` | `input_tokens` | |
| `output_tokens` | `output_tokens` | |
| `cache_read_tokens` | `cache_read_tokens` | |
| `cache_creation_tokens` | `cache_creation_tokens` | |
| `reasoning_tokens` | `reasoning_tokens` | |
| `model` | `model` | Overridden by top-level `"model"` if absent (see below) |
| `usage_raw_json` | `usage_raw_json` | |

### 1.2 Token-accounting invariants (preserved)

The envelope **reuses the existing `TokenUsage` model verbatim** — it does NOT
reinvent a new token-shape. The following invariants from the existing
infrastructure are preserved:

1. **`total` excludes cache reads** (`runtime/models.py:316`):
   `total = (input_tokens or 0) + (output_tokens or 0) + (reasoning_tokens or 0)`.
   Cache reads are an effectiveness signal, not new consumption. The envelope
   keeps `cache_read_tokens` as a separate field — the parser passes it through
   as-is, and the existing `TokenUsage.total` property does the right thing.

2. **model-null → back-filled to provider label** (`executors.py:582-583`):
   If `token_usage.model` is `None` after parsing, `_run_command` back-fills it
   with the provider label (the `provider` kwarg). The envelope parser follows
   the same logic: if envelope `"model"` is absent from BOTH `token_usage.model`
   AND the top-level `"model"`, the generic parser returns `token_usage.model=None`
   and `_run_command` fills it. If the top-level `"model"` is set but
   `token_usage.model` is absent, the parser copies it in so the CLIs don't need
   to repeat the model string in both places.

3. **`cache_read` / `cache_creation` split preserved** (`models.py:308-309`).
   The two fields stay separate — no single "cache" field that conflates hits
   and writes.

4. **Nullable tolerance** (`models.py:306-312`): every field is `int | None`.
   Partial success → still writes a row (forensic). The envelope mirrors this.

---

## 2. Transport / Channel

### 2.1 Recommendation: sentinel-delimited JSON block on stdout **(OPTION A)**

The CLI emits the envelope as a **sentinel-delimited JSON block** within its
normal stdout stream. The generic parser scans stdout for the sentinel markers
and extracts the JSON between them.

**Sentinel markers:**

```
__HR_ENVELOPE_BEGIN__
{...JSON...}
__HR_ENVELOPE_END__
```

- The markers are on their own lines (preceded by a newline or at the start of
  stdout).
- The JSON block is a **single, compact line** (no internal newlines) between
  the markers.
- The markers are **case-sensitive and exact-match** — no whitespace trimming
  around them.
- The body of stdout before the first marker, and after the closing marker, is
  treated as the CLI's normal conversational output and is ignored by the parser.
- If the CLI emits its own `__HR_ENVELOPE_BEGIN__` / `__HR_ENVELOPE_END__` in
  its normal output text (non-envelope), the parser uses `rfind` (last
  occurrence of `__HR_ENVELOPE_BEGIN__` from the tail) to extract the final
  envelope — this is the one the CLI intends.

**Rationale for Option A (stdout-sentinel):**

- **Reuses the existing stdout capture path** — `_run_command` (`executors.py:558`)
  captures `full_stdout` via `proc.communicate()` and passes it to the usage
  parser at `:570`. The sentinel parser is a drop-in replacement for
  `usage_parser=None` — no new file I/O, no workspace-path plumbing.
- **Mirrors how built-in parsers work** — each built-in parser (`:210/259/321/428`)
  already extracts structured JSON from stdout. The sentinel is just adding
  framing around a JSON block on a stream that is already being captured.
- **Works with pipe/stderr split** — many CLIs write structured data to stdout
  and human text to stderr. A sentinel on stdout doesn't collide with stderr;
  the parser only scans `full_stdout`.
- **Single capture target** — one stream, one capture; no cross-file
  synchronization between stdout + a sidecar file.

### 2.2 Alternative: sidecar file **(OPTION B — documented fallback)**

The CLI writes the envelope JSON to a known file path under `{workspace}`:
`{workspace}/.happyranch/envelope.result.json`. The generic parser reads this
file after the subprocess exits.

**Benefits vs Option A:**

- Zero risk of collision with stdout output (the CLI's human-readable output
  never needs to worry about `__HR_ENVELOPE_BEGIN__` appearing in text).
- Cleaner separation of concerns — stdout is the CLI's domain, the envelope file
  is HappyRanch's domain.

**Drawbacks vs Option A:**

- Requires the parser to know `{workspace}` → GenericCliExecutor must pass
  workspace to `_run_command` or the parser; today `_run_command` receives
  workspace but the `usage_parser` callable only gets `full_stdout`.
  **This changes the parser signature**, which cascades to the four built-in
  parsers (signature change → need to update all call sites).
- File must be written with appropriate atomicity (write temp + rename) to
  avoid partial reads if the subprocess is killed mid-write.
- CLI must have workspace-path awareness and know the convention.

**EM's lean: Option A (stdout-sentinel).** The sentinel markers are unambiguous
and unlikely to collide with normal CLI output given the `__HR_` namespace
prefix. The implementation simplicity of reusing `_run_command`'s existing
stdout capture without signature changes is compelling. If collision risk is
judged too high, Option B is the documented alternative and the spec is written
so the parser can be swapped without changing the envelope schema.

### 2.3 Founder decision required

The founder must ratify Option A vs Option B during sign-off. See §9.

---

## 3. Generic Parser

### 3.1 Location

A single new parser function in `runtime/orchestrator/executors.py`, inserted
at the module level alongside the four existing parsers. Convention:
`_parse_generic_cli_usage(stdout: str) -> TokenUsage | None`.

### 3.2 Wiring

In `GenericCliExecutor.run` (`executors.py:920`), replace:

```python
usage_parser=None,  # custom CLI usage parsing is not supported yet
```

with:

```python
usage_parser=_parse_generic_cli_usage,
```

### 3.3 Parser algorithm

```
def _parse_generic_cli_usage(stdout: str) -> TokenUsage | None:
    1. If stdout is empty/whitespace → return None (same as all 4 built-in parsers)

    2. Find the LAST occurrence of "__HR_ENVELOPE_BEGIN__" via rfind.
       If not found → return None (no envelope = old behavior = zero tokens)

    3. Find "__HR_ENVELOPE_END__" AFTER the begin marker position.
       If not found → log warning, return TokenUsage(usage_raw_json=tail)
          (forensic preservation — same pattern as _parse_claude_usage:237)

    4. Extract the text between the markers, strip whitespace.

    5. json.loads() on the extracted text.
       If JSONDecodeError → log warning, return TokenUsage(usage_raw_json=tail)
          (same pattern as _parse_claude_usage:222)

    6. Validate envelope_version == 1.
       If absent or wrong → log warning, return TokenUsage(usage_raw_json=tail)
       (future versions will need parser evolution; v1 is the only recognized version)

    7. Extract fields:
       - token_usage dict → map to TokenUsage(**fields) with key-name parity
         (see §1.1 mapping)
       - model: use token_usage.get("model") if present, else top-level "model",
         else None
       - agent_session_id: top-level string, None if absent
       - result: top-level string, None if absent

    8. Return TokenUsage(...) with mapped fields.
```

### 3.4 Best-effort contract (mirror `executors.py:570-573` EXACTLY)

The parser MUST be wrapped in a try/except that never breaks the task:

```python
token_usage: TokenUsage | None = None
if usage_parser is not None:
    try:
        token_usage = usage_parser(full_stdout)
    except Exception as exc:
        logger.warning("usage parser raised: %s", exc)
        token_usage = None
```

This is the existing pattern at `executors.py:570-573`. The generic parser uses
the **same** try/except — no new error handling. Absent or malformed envelope →
`token_usage=None` → task still succeeds (the existing `_run_command` path
already handles `token_usage=None` gracefully; the task runs, just without spend
data).

### 3.5 Session-id extraction

The generic parser also extracts `agent_session_id` from the envelope. This
requires a second parser function: `_parse_generic_cli_session_id(stdout: str)
-> str | None` that follows the same sentinel-extraction pattern but returns
only the `agent_session_id` field.

Alternatively, the usage parser can **also** return the session id via a new
return type (e.g., a dataclass or tuple). However, changing the parser signature
from `Callable[[str], TokenUsage | None]` to a richer type would cascade to all
four built-in parsers. A cleaner approach: `_run_command` receives a SEPARATE
`session_id_parser` already (`executors.py:585-590`) — the same pattern. Add a
`_parse_generic_cli_session_id` parser and pass it alongside the usage parser.

**Phase-1 execution:** the generic parser resolves token_usage. Session-id is
Parsed but implementation of session-continuity wiring is deferred to phase-2
(see §6). An alternative approach would be to extract both from a single scan
of the envelope, returning a named tuple, but that changes the parser contract
for all built-in executors — avoid for a spec that aims to be additive-only.

An alternative: extract session-id from the same envelope JSON in
the usage parser itself and store it in a side-channel. But that violates the
parser's return-type contract (`TokenUsage | None`). **Recommendation**: a
second, narrow `_parse_generic_cli_session_id` function that re-scans the same
stdout for the same sentinel block — the substring scan is trivial and the
separate function keeps the parser signatures stable. The session-id parser is
wired at `executors.py:920` as `session_id_parser=_parse_generic_cli_session_id`
(phase-2; see §6).

---

## 4. Conformance Step: `emit_envelope`

### 4.1 New step_id

Add a **fourth** conformance step to the existing three:

```python
# runtime/daemon/registration_token.py:116-118
DEFAULT_CONFORMANCE_STEPS = [
    "workspace_access",
    "loopback_reachable",
    "cli_callback",
    "emit_envelope",          # ← NEW
]
```

The `emit_envelope` step validates that the candidate CLI can produce a
well-formed envelope matching the schema defined in §1.

### 4.2 Verification at registration

The candidate CLI, after completing the existing three steps, calls
`POST /executors/conformance-checkin` with `step_id: "emit_envelope"`. The
existing `conformance_checkin` route (`runtime/daemon/routes/executors.py:189`)
already handles arbitrary `step_id` values from the challenge store — no route
change needed.

**Verification mechanism:** the CLI MUST include the envelope in the
conformance-checkin **request body** for the `emit_envelope` step. The route
parses and validates the sample envelope against the schema. If invalid,
the checkin is rejected (step not recorded as arrived).

This requires a **small body extension** to `ConformanceCheckinRequest`
(`executors.py:163`):

```python
class ConformanceCheckinRequest(BaseModel):
    step_id: str = Field(..., min_length=1)
    envelope: dict | None = Field(None)  # ← NEW: sample envelope for emit_envelope step
```

When `step_id == "emit_envelope"` and `envelope` is `None` → 400 bad request.
When `step_id != "emit_envelope"`, `envelope` is ignored (backward-compat with
existing CLI registrations that don't send it).

The route validates the envelope against the schema at check-in time:
- `envelope_version` must be `1`
- `token_usage` (if present) must be a dict with keys matching `TokenUsage` fields
- If validation fails, the step is NOT recorded as arrived → registration
  remains blocked on `emit_envelope`

### 4.3 Consistency with existing pattern

The new step follows the exact same model as the existing three:

- **Minted at token creation** (`RegistrationTokenStore.mint`, `:167`)
  via `DEFAULT_CONFORMANCE_STEPS`.
- **Checked in** via the existing `POST /executors/conformance-checkin` route
  (`executors.py:189`), which iterates `challenge.steps` (`:224`) and accepts
  any `step_id` from the challenge.
- **Registration gated** on `is_challenge_complete` (`executors.py:255`) —
  unchanged; adding a fourth step automatically means all four must arrive.

### 4.4 Backward-compat for existing registrations

The `emit_envelope` step is **additive for new tokens only** — existing
registrations are unaffected because the conformance challenge is per-token,
not per-profile. See §5 for details.

---

## 5. Backward Compatibility (Mandatory)

The envelope is **OPTIONAL**. Existing custom CLIs that do NOT emit an envelope
MUST keep working exactly as they do today.

### 5.1 Absence = current behavior

- No `__HR_ENVELOPE_BEGIN__` sentinel in stdout → `_parse_generic_cli_usage`
  returns `None` → `token_usage=None` → no token row written → behavior is
  **byte-for-byte identical** to the existing `usage_parser=None` path.

- No `agent_session_id` in envelope → `session_id_parser` returns `None` →
  no resume → behavior is **byte-for-byte identical** to the existing
  `session_id_parser=None` path.

### 5.2 Existing registrations are NOT retroactively gated

The `emit_envelope` conformance step is added to `DEFAULT_CONFORMANCE_STEPS` —
which applies to **new tokens minted after this change**. An already-registered
custom CLI profile already consumed its token; its profile is in the in-memory
`ExecutorRegistry` and durable `runtime_executor_store`. It is NOT subject to
a new conformance challenge retroactively.

**Re-registration:** if a founder re-mints a token for an already-registered
profile (e.g., to change `argv_template`), the new token WILL require
`emit_envelope`. The founder chooses: if the CLI can't emit an envelope yet,
don't re-register until it can. The existing profile keeps working.

### 5.3 Built-in executors are unaffected

The four built-in executors (`ClaudeExecutor`, `CodexExecutor`, etc.) do NOT
use `GenericCliExecutor` and do NOT pass through `usage_parser=None`. They
retain their hand-written parsers (`:210/259/321/428`). The generic parser is
only wired into `GenericCliExecutor.run` (`:920`).

---

## 6. Session Continuity

### 6.1 Mechanism

The envelope's `agent_session_id` field feeds the `session_id_parser` callback
in `_run_command` (`executors.py:585-590`), which sets `ExecutorResult.
agent_session_id` (`:50`). This is the same field the built-in executors use
for `--resume` (Claude) / `--continue` (Codex) — see `executors.py:50-55`.

### 6.2 Phase-2: deferred

Session continuity for custom CLIs is a **phase-2 feature**, deferred relative
to token metering. Rationale:

- **Token metering works WITHOUT session continuity** — the generic parser
  extracts `token_usage` from the envelope, which `_run_command` writes to the
  DB regardless of whether `agent_session_id` is set. Spend visibility is the
  primary prize.

- **Session continuity requires the custom CLI to understand HappyRanch's
  resume protocol** — the CLI must accept a `--resume <session_id>` flag and
  restore state. This is a per-CLI implementation burden.

- **The envelope carries the session id** so the data is already flowing
  through `ExecutorResult` — the wiring from `ExecutorResult.agent_session_id`
  to the existing resume machinery is already implemented for built-ins
  (`run_step.py` → `--resume` flag injection). Custom CLIs just need to
  accept the flag, which is a documentation/CLI-author concern, not a runtime
  code concern.

- **Phase-1 ships with session_id_parser=None** — the envelope schema includes
  `agent_session_id`, the parser can extract it, but the wiring at
  `executors.py:920` sets `session_id_parser=None` in phase-1. Phase-2
  changes it to `session_id_parser=_parse_generic_cli_session_id`.

---

## 7. Phasing

### Phase 1: Token Metering (RECOMMENDED FOR IMMEDIATE BUILD)

**Scope:**
1. Define the result-envelope schema (§1) and sentinel markers (§2).
2. Implement `_parse_generic_cli_usage` parser (§3).
3. Wire it into `GenericCliExecutor.run` (`executors.py:920`), replacing
   `usage_parser=None`.
4. Add `emit_envelope` conformance step (§4).
5. Extend `ConformanceCheckinRequest` with optional `envelope` field for
   envelope validation at registration.
6. Update `DEFAULT_CONFORMANCE_STEPS` in `registration_token.py:116-118`.

**NOT in phase 1:**
- Session continuity (session_id_parser stays `None`)
- `result` field utilization (the `result` field in the envelope is defined
  but the parser ignores it in phase 1 — it's scaffolding for future use)

**Rationale:** Phase 1 gives spend visibility on custom CLIs immediately —
this is the primary prize. Custom CLI authors get token metering as soon as
they adopt the envelope. Session continuity is a separate, smaller prize that
can follow without destabilizing phase 1.

**Phase 1 delivers:** every custom CLI task shows up in token-usage dashboards
with real input/output/cache/reasoning token counts, per-model breakdowns, and
cost estimates — parity with the built-in executors on the spend surface.

### Phase 2: Session Continuity (DEFERRED)

**Scope:**
1. Implement `_parse_generic_cli_session_id` parser.
2. Wire `session_id_parser=_parse_generic_cli_session_id` into
   `GenericCliExecutor.run` (`executors.py:920`).
3. Document the `--resume` flag convention for custom CLI authors.
4. Test with a real custom CLI that supports resume.

**Prerequisite:** phase 1 must be shipped and stable.

---

## 8. Contract-Surface Impact

Every doc or code surface a build would need to touch, enumerated for the
implementing dev.

### 8.1 Code surfaces (non-protocol)

| File | What changes | Risk |
|---|---|---|
| `runtime/orchestrator/executors.py` | Add `_parse_generic_cli_usage()` parser (~50 lines). Add `_parse_generic_cli_session_id()` parser (~20 lines, phase-2). Wire `usage_parser=_parse_generic_cli_usage` at `:920`. Wire `session_id_parser=_parse_generic_cli_session_id` at `:920` (phase-2 only). | LOW — additive, no existing parser modified |
| `runtime/daemon/registration_token.py:116-118` | Add `"emit_envelope"` to `DEFAULT_CONFORMANCE_STEPS` | LOW — additive entry in a list |
| `runtime/daemon/routes/executors.py:163` | Extend `ConformanceCheckinRequest` with optional `envelope: dict \| None` field | LOW — additive field |
| `runtime/daemon/routes/executors.py:189-245` | Add envelope validation in `conformance_checkin` when `step_id == "emit_envelope"` | LOW — additive branch |
| `tests/test_executors.py` (or equivalent) | Test generic parser: valid envelope, absent envelope, malformed envelope, sentinel extraction, backward compat | MEDIUM — new test class |
| `tests/test_registration_token.py` | Test `emit_envelope` conformance step in challenge lifecycle | LOW — extends existing test |

### 8.2 Protocol docs (FOUNDER-GATED)

These doc edits require founder authorization before landing in a build PR:

| Protocol doc | Section | What changes | Why founder-gated |
|---|---|---|---|
| `protocol/05c-orchestrator.md` | Executor subsystem | Document the result-envelope contract (schema + transport) as part of the executor interface spec | Protocol surface that future executor designs must comply with |
| `protocol/05b-agent-runtime.md` | Token accounting | Note that custom CLIs can now report token usage; the `total` property invariant (excludes cache reads) applies to envelope-reported tokens as well | Load-bearing invariant doc — must be kept accurate |

### 8.3 Other docs (not founder-gated, but doc-parity required)

| Doc | What changes |
|---|---|
| `CLAUDE.md` or agent-level system prompt | Add note: custom CLIs that emit the envelope get token accounting; the envelope format is defined in `docs/superpowers/specs/2026-07-19-custom-cli-adapter-envelope-design.md` |
| Web contract / OpenAPI snapshot | If `ConformanceCheckinRequest` gains a new field, the OpenAPI snapshot (`tests/contract/openapi.json`) must be regenerated (per MEM-058: matched-pair: OpenAPI regen + TS coverage exemption) |

### 8.4 NOT touched

- `runtime/models.py:302` `TokenUsage` — unchanged (the envelope maps onto it, does not modify it)
- `runtime/orchestrator/executors.py:27` `ExecutorResult` — unchanged (existing fields `token_usage` and `agent_session_id` already carry the data)
- `runtime/orchestrator/executors.py:570-573` — the existing try/except contract is reused, not modified
- The four built-in parsers (`:210/259/321/428`) — untouched
- `protocol/` — this spike is a design doc; the build PR will update protocol/ per the table above with founder authorization
- The `adapter` field on executor profiles — unchanged; the envelope is orthogonal to adapter (adapter selects workspace readiness; envelope selects willingness to report spend)

---

## 9. Open Questions for Founder Sign-Off

These MUST be ratified by the founder before any build PR. EM reviews the spec
first for soundness/completeness, then relays to the founder with these
questions.

### Q1: Transport choice
**Option A** (stdout-sentinel: `__HR_ENVELOPE_BEGIN__` / `__HR_ENVELOPE_END__`)
or **Option B** (sidecar file at `{workspace}/.happyranch/envelope.result.json`)?

EM's lean: Option A. Founder decision required.

### Q2: Phase-1 scope confirmation
Phase 1 = token metering only (no session continuity, no `result` field
utilization). Phase 2 = session continuity + `result` field. Confirm this
phasing.

### Q3: `emit_envelope` mandatory on re-registration?
When a founder re-mints a token for an already-registered custom CLI, the new
token requires `emit_envelope`. This means: to change `argv_template` or
`command`, the CLI must also support the envelope. Is this acceptable, or should
re-registration be exempt from the new conformance step?

EM's lean: mandatory on re-registration (encourages envelope adoption; the
alternative of exempting old profiles creates a long tail of second-class CLIs).

### Q4: `envelope_version` policy
What happens when a CLI emits `envelope_version: 2`? The parser currently
rejects unknown versions (returns `TokenUsage(usage_raw_json=tail)` for
forensic preservation). Should the runtime accept version >=1, or strictly
reject non-1?

EM's lean: strict reject (the parser must understand the schema to extract
token_usage correctly; future versions need code changes).

### Q5: Protocol doc edits authorization
Confirm that the protocol/ doc edits listed in §8.2 are authorized for the
build PR. This design spike does NOT edit protocol/ — the build PR must carry
those edits with founder sign-off.

### Q6: `result` field in phase 1
The envelope schema includes a `result` field (the agent's final response text).
Phase 1 defines it in the schema but the parser ignores it. Is `result` needed
at all, or should it be removed?

EM's lean: define it now (non-breaking to add ignored field; useful as
scaffolding for future task-output capture features). If the founder wants a
leaner schema, remove it.

### Q7: Sentinel marker naming
Are `__HR_ENVELOPE_BEGIN__` and `__HR_ENVELOPE_END__` acceptable as the
sentinel markers? The `__HR_` prefix follows the convention used elsewhere
in the runtime. Alternative: `<<<HR_ENVELOPE>>>` / `<<<END_HR_ENVELOPE>>>`
(heredoc style); `<|HR_ENVELOPE|>` (GPT-style tokens). Confirmation needed.

### Q8: Multi-envelope behavior
What if a CLI emits multiple envelopes in the same stdout (e.g., to report
usage per turn in a multi-turn session)? Current design: last envelope wins
(`rfind`). Is this correct, or should we reject multiple envelopes?

EM's lean: last wins (simple, fits the single-turn daemon-spawned session
model where the CLI emits exactly one final envelope).

---

## Appendix A: End-to-End Flow (Illustrative)

```
1. Founder mints registration token (includes emit_envelope conformance step)
2. Candidate CLI:
   a. checkin: workspace_access
   b. checkin: loopback_reachable
   c. checkin: cli_callback
   d. checkin: emit_envelope (POSTs a sample envelope, route validates it)
3. Registration succeeds → profile stored in runtime_executor_store
4. Later, a task targets this profile:
   - GenericCliExecutor builds argv from template, substitutes {prompt}
   - GenericCliExecutor calls _run_command(cmd, ..., usage_parser=_parse_generic_cli_usage)
   - Subprocess runs, emits stdout with __HR_ENVELOPE_BEGIN__ ... __HR_ENVELOPE_END__
   - _run_command captures full_stdout, calls usage_parser(full_stdout)
   - _parse_generic_cli_usage extracts JSON, maps to TokenUsage
   - _run_command returns ExecutorResult(token_usage=<populated>)
   - run_step writes token_usage row to DB
   - Dashboard shows spend for this custom CLI task
```

---

## Appendix B: Builder Checklist

For the dev who implements this spec (phase 1):

1. [ ] Add `_parse_generic_cli_usage(stdout: str) -> TokenUsage | None` to `executors.py`
2. [ ] Wire `usage_parser=_parse_generic_cli_usage` in `GenericCliExecutor.run` (`:920`)
3. [ ] Add `"emit_envelope"` to `DEFAULT_CONFORMANCE_STEPS` in `registration_token.py:116`
4. [ ] Extend `ConformanceCheckinRequest` with `envelope: dict | None = Field(None)` in `executors.py:163`
5. [ ] Add envelope validation logic in `conformance_checkin` route (`executors.py:189`) for `step_id="emit_envelope"`
6. [ ] Write unit tests: valid envelope → TokenUsage populated; absent envelope → None; malformed JSON → forensics-only TokenUsage; sentinel extraction edge cases
7. [ ] Write conformance tests: `emit_envelope` step lifecycle in token mint→checkin→register flow
8. [ ] Regen OpenAPI snapshot if `ConformanceCheckinRequest` changes
9. [ ] Update `protocol/05c-orchestrator.md` (founder-authorized)
10. [ ] Update `protocol/05b-agent-runtime.md` (founder-authorized)
11. [ ] PR description MUST include: "design approved by founder at THR-107 seqXX" + link to this spec

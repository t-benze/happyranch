# Optional agent reasoning effort

> Status: current
> Authority: Founder THR298 seq15 and seq46 plain-input correction; accepted TASK11053 composed design/TASK11065 amendment/TASK11070 DESIGN PASS.
> Current sources: `runtime/adapters/__init__.py`, `runtime/orchestrator/adapter_contract.py`, the four maintained agent guides, and bundled `manage-agent`.
> Scope: optional configuration and literal input transport. Historical TASK10314/TASK10336 designs are inventories, not additional acceptance gates.

## Selection and defaults

Canonical AgentDef frontmatter owns optional `reasoning_effort` beside `model`.
Absent/null means no override: no effort flag, no rewriting an embedded model
suffix, and no new authority agent-projection key. Omitted update fields preserve
the saved selection; explicit null clears. Unrelated whole-definition writers
preserve it. CLI changes clear omitted model/effort or validate an explicit new
pair. UI drafts bind to their selected CLI through `expected_executor`; stale
CLI saves refuse before canonical mutation. All existing writer locks,
invalidation and lifecycle boundaries remain.

Built-ins accept case-preserving fullmatch `[A-Za-z][A-Za-z0-9_-]{0,31}` through shared `model_effort_args` validation; historical catalog values are metadata, not a membership gate. UI uses plain text: blank on explicit Save maps to null/default, without trimming or truncation. Explicit wire/direct empty strings refuse; CLI clear retains its existing meaning. Custom adapters still require approved declared-value membership (at most16 lowercase identifiers); legacy/model-only effort controls remain disabled with saved values visible. Token acceptance proves syntax/transport only, not native model/account support.

With explicit effort, OpenCode requires nonempty provider/model slash components (additional model path segments allowed), no whitespace/control/NUL, at most one # and a nonempty embedded variant byte-equal to effort; it composes one literal `-m provider/model#effort`. Unset preserves the original model transport. Pi recognizes only its existing finite thinking suffix set; arbitrary safe colon suffixes do not become native thinking syntax. Codex keeps one literal quoted config argv element.

Bounded evidence does not verify dynamic manage_agent update/enrollment at runtime (source only), native models/accounts, full task/thread/dream/wake/schedule lifecycles, host/callback or custom contained-wrapper prelaunch. General integration including callback is SKIPPED THR243seq42; former Python units are RETIRED. No ad hoc Product E2E or protected mutation/repetition proof is authorized.

Task invocation selects its configured executor, model and effort from one
canonical AgentDef byte snapshot before executor construction. An atomic save
after that read applies to the next selection; it cannot mix separately saved
values in the current task tuple. Missing, pending-only and terminated agents
retain their existing refusal before construction. This is selection coherence,
not a lock across the later invocation or a lifecycle guarantee.

Wake and schedule invocations likewise derive executor/model/effort from their
already-loaded canonical definition. The effective provider is the lowercase
registered executor, with the existing Claude fallback for an unregistered
executor. Their optional selection audit records that effective provider beside
the configured model/effort, which fallback preserves; it does not claim those
overrides are accepted by the native provider. The same provider reaches skill
materialization and executor construction. When effort is unset, no selection
audit is added. Missing-agent handling and existing failure transitions remain.

| CLI / verified source | Historical CLI examples (not built-in limits) | Literal argv with explicit effort |
| --- | --- | --- |
| Claude 2.1.280 installed help | low, medium, high, xhigh, max | `--effort <level>` beside unchanged `--model <id>` when pinned |
| Codex 0.160.0 help + [versioned enum](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/protocol/src/openai_models.rs) / [official config reference](https://learn.chatgpt.com/docs/config-file/config-reference) | none, minimal, low, medium, high, xhigh, max, ultra, persistent | `-c model_reasoning_effort="<level>"` beside unchanged `-m <id>` when pinned |
| Pi 0.85.1 installed help | off, minimal, low, medium, high, xhigh, max | `--thinking <level>` beside unchanged `--model <pattern>` when pinned |
| OpenCode 2.0.12 help + [official variants](https://opencode.ai/docs/models/) | none, minimal, low, medium, high, xhigh, max | `-m provider/model#<level>` |

All four built-ins accept the shared grammar above, including `future_Tier-2` and explicit `none`. The historical CLI examples are retained metadata; custom declarations remain finite and authoritative.

Fresh and supported resume arguments use the same selection, in adapters and
existing inline fallback builders. Default model works for Claude/Codex/Pi.
OpenCode explicit effort requires a nonempty `provider/model`; it cannot silently
drop effort with CLI-default model. A model containing an equal `#variant` stays
unchanged, a conflicting variant refuses. Pi recognizes its finite thinking
suffixes after the final `:`; equal explicit effort is allowed (preserve model
and add `--thinking`), conflict refuses, and unset preserves the exact model.
Other colon-bearing model IDs are left alone.

Deterministic subprocess stubs verify exact transport, not native provider
acceptance. OpenCode's independent 2.0.12 `--dir`/transport mismatch is unchanged;
this feature makes no complete native OpenCode behavior claim.

## Custom adapter input v2

Default registration and every unchanged legacy adapter retain input v1,
`model:null` even when the agent has a configured model, and no effort member.
Unsupported explicit effort is rejected; the UI control is disabled with a
localized explanation. There is no global adapter reapproval.

An ordinary adapter update may add this explicit declaration:

```json
{
  "input_options": {
    "version": 2,
    "model_arg_template": ["--model", "{model}"],
    "effort_arg_template": ["--effort", "{effort}"],
    "reasoning_effort_values": ["low", "high"]
  }
}
```

The closed declaration requires integer version2. Model template is optional.
Effort template and nonempty values must appear together. Templates contain
1..16 nonempty literal argv elements (each at most512 characters), exactly one
matching placeholder and no other braces. Up to16 distinct lowercase bounded
identifiers are allowed. They are literal argv, never shell interpolation.
Normal registration, conformance probe, storage/readback, profile binding,
approval and runtime use the same normalized declaration. Existing exact-artifact
snapshot checks include it; a changed declaration goes through ordinary pending
review/approval. Existing executable/dependency hashes and retry checks remain.

Opted-in shipping CustomAdapterExecutor sends `contract_version:2` plus:

```json
{
  "model": {"model_id":"actual-model", "model_arg_template":["--model","{model}"]},
  "reasoning_effort": {"reasoning_effort":"high", "effort_arg_template":["--effort","{effort}"]}
}
```

Each member is null when unset; a set model with no template is still forwarded
with `model_arg_template:null` for the wrapper's own documented mapping.
The wrapper receives and applies the literal templates; HappyRanch never
shell-expands them. Output stays contract1, with unchanged identity/version,
session echo, result/accounting and hash checks. Input2 requires the additional
integer acknowledgement `adapter_metadata.input_contract_version:2`, checked
both at conformance and every runtime response. Missing/coerced/wrong ack fails.

The existing scoped-token contract-reference includes legacy
`adapter_input_schema`/`adapter_output_schema` plus `input_v2_schema` and
`input_options_schema`. Its top-level `contract_version:1` denotes the unchanged
output/default contract. Default probe remains v1; opted registration probes v2
with a deterministic model and the first declared effort value. This grants no
new trust, callback, permission or resume authority.

## Generated custom-CLI connection prompt (THR298 seq34)

Onboarding and Settings share `buildDirectConnectPrompt`. Its en/zh-CN guidance
asks the CLI author to inspect installed help/documentation for actual
per-invocation model and effort/thinking syntax and exact finite values, recording
supported, unsupported and unknown separately. No invented flags, name-based
inference or silent omission of supported forwarding is permitted. Static support
is not native model/account acceptance.

The current direct connection is **input1-only**: its strict request/manifest
have no `input_options`, and metadata is not a declaration channel. Direct
manifest2 is distinct from input2. Direct projection probes input1 and stores no
opt-in; its existing automatic connection authority does not approve ordinary
updates. A dual-version wrapper can prepare for a future update, but must retain
input1/model:null/default behavior. This text does not activate input2 on a newly
direct-connected CLI.

For a later approved ordinary update, consume both input2 object/null members,
substitute each template into literal argv without a shell, and append no flag
for null. Preserve prompt/workspace/timeout, callback environment, identity,
truthful terminal success/error and session echo. Every input2 response retains
output1 (`adapter_metadata.contract_version:1`) and strict integer
`adapter_metadata.input_contract_version:2`, including failures.

Both controls may declare their verified templates and finite effort values.
Model-only is schema-valid with no effort template/values. Effort-only is also
schema-valid with `model_arg_template:null`, but runtime still forwards a non-null
configured model, and **any** opted conformance probe sends `probe-model`.
That model needs a documented wrapper-owned mapping or a truthful refusal;
ignoring it or inventing a native flag is incorrect. Without a mapping that
passes conformance, activation remains unsupported. Neither/unknown retains
input1/model:null/default and explicitly discloses both limitations.

The new-connect prompt does not execute an update or approval. For an existing
profile, an authorized local operator uses the ordinary APIs for the **same**
name/intended_profile_name, without duplicating, deleting or recreating it:

1. Mint a fresh adapter-purpose token at
   `POST /api/v1/auth/registration-token/runtime`, omitting
   `workspace_adapter_id`. Including that field creates direct authority.
   The current Connect screen always uses direct authority and has no ordinary
   update/approval mode. Its direct token is rejected by `/runtime/adapters/submit`
   before generic validation; never reuse it for submission.
2. Fetch `/api/v1/runtime/adapters/contract-reference` with the fresh ordinary
   token. Use its literal `required_executable_path` and `canonical_adapter_id`,
   not the direct status `wrapper_destination`. Implement the reviewed wrapper
   there, refresh all executable/dependency hashes, and preserve workspace/output1
   facts. Complete the reference conformance check-ins and submit the complete
   same-profile artifact with `input_options` to `/api/v1/runtime/adapters/submit`.
   Server versioned conformance must pass. Registration returns **PENDING**.
3. Inspect fresh `/api/v1/runtime/adapters/{id}`. Founder explicitly approves
   that exact snapshot, including input_options, version, workspace, capabilities,
   executable/dependency hashes and applicable resume facts, at
   `/api/v1/runtime/adapters/{id}/approve`. Then read adapter, profile and prereqs
   back. Stale/path/profile/hash refusals require operator resolution; rerunning
   the direct prompt never approves the update. Approval/readback does not select
   an agent model/effort or prove native acceptance.

## Authority, recording and compatibility

`_agent_projection` emits effort only when set. Absent canonical bytes and digest
match the current projection baseline without effort; set participates in normal snapshot publication,
admission and whole-byte authentication. Clear/reload removes the key. Current
profile/activation readers use named fields; retained digest readers consume
whole immutable bytes. Journal snapshots and old interpretation are never
rewritten. Effort adds no snapshot format bump of its own: the inherited roster
writer emits schema2, and current version-aware readers retain schema1 and
schema2 roster meanings. Old readers can ignore set effort and old
writers can drop it: absence equality does not imply every old release honors
new selections. Use a compatible writer, explicitly clear/reconcile before an
intentional rollback, and retain historical bytes.

Existing task normal/followup/resume/recovery, thread fresh/resume/fallback,
dream, wake and schedule run/spec builders forward the selection. Existing
session-start and dream-started JSON add effort only when set; thread model
columns remain unchanged, with associated optional selection audit JSON.
Wake/schedule selection uses existing audit storage beside model, without new
SQL fields. Omitted/default audit behavior remains unchanged. Full lifecycle,
genuine provider/model acceptance and daily E2E are unproven until their own
actual evidence; bounded feature checks are not a lifecycle campaign.

## Bounded feature checks

These separately selected checks are outside automated merge CI and retired
Python units. They use shipping factories/executors, deterministic executable
stubs, and the actual app/save routes in an owned disposable synthetic org.
No provider invocation, daily E2E controller or lifecycle worker is started.
The executable check also enters the actual shipping task-selection prefix
with synthetic canonical files and an inert task lookup. It observes the saved
tuple at the executor-construction boundary, then stops before construction.
Controlled ordinary atomic file transitions cover same-CLI and CLI-changing
tuples, unset/selected effort and unavailable-agent refusals. This prefix check
does not prove complete task execution, callbacks or recovery. Wake/schedule
prefix checks use real synthetic definitions and inert stores, observe the
materialization-call provider plus captured model/effort and selection audit,
then stop before real materialization or executor construction. They cover
ordinary atomic same-CLI/CLI-changing saves, registered normalization,
unregistered fallback, unset audit omission and missing-agent refusal. Synthetic
store/audit observations establish prefix decisions only, not full persisted
wake/schedule lifecycles or provider acceptance. Five-owner source tracing is
separate from executable lifecycle evidence.

```bash
uv run python scripts/verify_reasoning_effort.py --output <task-output>
# Install only the retained locked browser tooling into the task-local venv:
uv pip install --python .venv/bin/python --require-hashes -r e2e/browser.lock
(cd web && npm run build)
# Keep this fixture process under the caller's control; root must be absent.
uv run python scripts/reasoning_effort_browser_fixture.py --root /tmp/TASK-10683-browser-venue-<unique> --port <loopback-port>
# In another controlled terminal, provide short owned XDG/TMPDIR directories:
uv run python scripts/verify_reasoning_effort_browser.py --root /tmp/TASK-10683-browser-venue-<unique> --base-url http://127.0.0.1:<loopback-port> --chromium <installed-chromium> --output <task-output>
# After checks, write root/STOP, observe fixture exit, then clean owned scratch.
```

Browser evidence uses actual API persistence, English/zh-CN default/selected/
unsupported/error states at390x844 and1440x900, and locale draft/focus/action
preservation without mutation. It does not replace independent reviewer/QA
assessment or prove full task/thread/dream/wake/schedule lifecycles. Required
`local_ci.sh all` remains static only; general integration is SKIPPED under
THR243seq42 and fresh Product E2E remains daily-only under THR291seq69.

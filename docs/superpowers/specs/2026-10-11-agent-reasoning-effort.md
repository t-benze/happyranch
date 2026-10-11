# Optional agent reasoning effort

> Status: current
> Authority: Founder THR298 seq15 approving seq11/14 and engineering seq12; TASK10678/TASK10683.
> Current sources: `runtime/adapters/__init__.py`, `runtime/orchestrator/adapter_contract.py`, the four maintained agent guides, and bundled `manage-agent`.
> Scope: optional static configuration and input transport. Historical TASK10314/TASK10336 designs are inventories, not additional acceptance gates.

## Selection and defaults

Canonical AgentDef frontmatter owns optional `reasoning_effort` beside `model`.
Absent/null means no override: no effort flag, no rewriting an embedded model
suffix, and no new authority agent-projection key. Omitted update fields preserve
the saved selection; explicit null clears. Unrelated whole-definition writers
preserve it. CLI changes clear omitted model/effort or validate an explicit new
pair. UI drafts bind to their selected CLI through `expected_executor`; stale
CLI saves refuse before canonical mutation. All existing writer locks,
invalidation and lifecycle boundaries remain.

The catalog/declaration is authoritative for the finite static UI list and save
validation. It does not inspect runtime capabilities, accounts or model support.
A valid selection may still be rejected by the native model/account. Configured
selection is distinct from provider-reported behavior and token-usage model.

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

| CLI / verified source | Static values | Literal argv with explicit effort |
| --- | --- | --- |
| Claude 2.1.280 installed help | low, medium, high, xhigh, max | `--effort <level>` beside unchanged `--model <id>` when pinned |
| Codex 0.160.0 help + [versioned enum](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/protocol/src/openai_models.rs) / [official config reference](https://learn.chatgpt.com/docs/config-file/config-reference) | none, minimal, low, medium, high, xhigh, max, ultra, persistent | `-c model_reasoning_effort="<level>"` beside unchanged `-m <id>` when pinned |
| Pi 0.85.1 installed help | off, minimal, low, medium, high, xhigh, max | `--thinking <level>` beside unchanged `--model <pattern>` when pinned |
| OpenCode 2.0.12 help + [official variants](https://opencode.ai/docs/models/) | none, minimal, low, medium, high, xhigh, max | `-m provider/model#<level>` |

Codex’s versioned enum also accepts arbitrary custom strings; this product deliberately exposes only its nine named static values and rejects undeclared custom strings.

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

## Authority, recording and compatibility

`_agent_projection` emits effort only when set. Absent canonical bytes and digest
match the previous projection; set participates in normal snapshot publication,
admission and whole-byte authentication. Clear/reload removes the key. Current
profile/activation readers use named fields; retained digest readers consume
whole immutable bytes. Journal snapshots and old interpretation are never
rewritten. Snapshot schema stays1. Old readers can ignore set effort and old
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

# Per-task model and reasoning effort (THR-356)

Founder-approved design; TASK-15701 rulings R1–R5 clarify PR1. This supplements
[agent defaults](2026-10-11-agent-reasoning-effort.md).

## Selection and persistence

Add nullable TEXT task columns `model_override`, `reasoning_effort_override` and
`override_source` through the ordinary legacy ALTER path, without indexes. Old
rows read NULL. Each NULL field selects the CURRENT assigned agent-page default;
a selected field wins for every session of that task, including followups and
recovery. Changing an agent default affects only fields without their own task
override. No default is copied into storage. Unset preserves argv and existing
audit payload bytes. No keys are added to strict `session_start`.

Source is `founder` for founder-authenticated submission/revisit, `manager` for
agent-authored thread self-dispatch and handoffs. Founder revisit adopts
the resulting tuple with founder provenance; runtime linked retry carries its
predecessor's tuple and provenance. Source becomes NULL when both fields clear.
Ordinary children never inherit their manager's tuple. An explicit linked retry
carries its linked predecessor, including a pipeline retry's first executable
leg; existing lineage validation remains authoritative. Removed daemon automatic
revisit creation stays removed.

## Launch surfaces (PR1)

POST `/tasks`, POST `/tasks/{id}/revisit` and POST `/threads/{id}/dispatch` accept
optional `model` and `reasoning_effort`. Existing CLI commands `happyranch run`,
`happyranch revisit` and `happyranch threads dispatch` accept `--model` and
`--reasoning-effort`; thread dispatch also reads them from `--from-file` JSON.
Existing self-only and invocation-authority rules remain.

Revisit omission means carry; explicit JSON null clears that field to the current
agent default. CLI `--clear-model-override` and
`--clear-reasoning-effort-override` send null, mutually exclusive with matching
value flags. Empty/whitespace model and malformed effort fail locally before a
daemon connection. Executor-specific validation is server-owned. No silent
trimming, clearing or fallback.

Dispatch response exposes the accepted settings and sources. With overrides,
`task_dispatched` system messages and dispatch audits gain the same settings,
so the agent's confirmation can state them. The replay digest includes selected
model/effort only when set, preserving historical unset digests. Distinct
selections create distinct dispatches; same pending-token requests replay the
original task/message. Invalid choice: state the reason and valid options.

## Validation and execution

Every creation surface resolves the TARGET agent's current executor and checks
the effective pair through existing `validate_reasoning_selection`,
`model_effort_args` and custom `AdapterInputOptions` contracts. Invalid choices
refuse before any task row: HTTP422 `invalid_task_selection`, plain `reason`,
`executor` and `valid_options` from that executor's existing effort values.

Only an EXPLICIT TASK effort override must belong to
`executor_registry.reasoning_effort_values`. Agent defaults are never subjected
to this extra finite gate, including model-only overrides. Existing combination
validation still applies to the effective pair. THR-298 agent-default plain-input
grammar stays unchanged. There is no built-in model catalog: use existing
nonempty-string/combination rules. Unknown native model failure uses the CLI's
normal error path; never fall back to the default.

| Executor | Existing selection transport |
| --- | --- |
| Claude | `--model MODEL --effort EFFORT` |
| Codex | `-m MODEL -c model_reasoning_effort="EFFORT"` |
| OpenCode | `-m provider/model#EFFORT` |
| Pi | `--model MODEL --thinking EFFORT` |

Each CLI supplies its own effort choices. OpenCode effort requires pinned
provider/model and a compatible embedded variant; effort without it refuses at
creation. Pi suffix conflicts and existing literal grammar remain. Acceptance
proves syntax/transport, not native model/account support.

Custom cases retain ordinary registration, approval, artifact and dependency
checks, using existing input-v2 declarations and `opt_in_adapter_input`:

* Model and effort templates declared: both selected fields forward through
  input-v2 and the literal templates.
* Only model, or neither: unsupported explicit fields refuse with
  `executor X does not support effort/model`. A missing model template does not
  declare task model support. Existing legacy agent-default input-v1/model:null
  behavior remains unchanged.
* Declared but rejected at runtime: AdapterOutput has no selection-specific
  discriminator. Use the bounded fallback: actually launched custom executor
  with task overrides returns nonzero before any genuine callback. Fail with
  `custom executor X rejected the model/effort setting`, exit code and stderr
  tail. Custom override invocations have no throttle/supervisor retry, and no
  auto-revisit onto a default. No adapter contract field is added.

The single task resolution point remains `_run_agent_impl` immediately after
`_resolve_agent_def`. Recheck saved override fields against the current executor
and declaration before executor construction. Invalid saved choices return a
failed pre-launch ExecutorResult with `saved model no longer valid for this
agent`, taking ordinary failure -> parent wake -> thread-followup ordering.
This deterministic return does not enter the three-exception native launch
allowance. Never start an unchosen model. Non-task thread/wake/dream/schedule
launchers remain unchanged.

## Audit and read contract

Selected creation adds a `task_created` selection audit; ordinary root submission
previously had no creation audit. Unset adds no row. Revisit and thread-dispatch
audit payloads gain additive keys only when selected. Audit scopes and strict
session_start payload stay unchanged.

Task GET/list expose `model_override`, `reasoning_effort_override`,
`override_source`, `effective_model`, `effective_reasoning_effort`, `model_source`
and `reasoning_effort_source`. Per-field source is founder, manager or
agent_default. Effective fields resolve CURRENT defaults; existing session_start
is evidence of what actually ran historically. No history is backfilled.

## Manager handoffs (PR2 shipped)

Ordinary `delegate`, every `then` leg, and every `fanout`/`parallel` child
(including a carrier's first executable leg and nested `then` legs) accept
optional `model` and `reasoning_effort`, using the PR1 request names.
Each target's CURRENT executor validates through `resolve_task_selection`
before any child/task row is allocated. Unsupported custom fields and invalid
combinations anywhere refuse the WHOLE decision. The existing
`_feedback_and_reenqueue` authoring-error path writes a feedback task result and
feedback orchestration step, returns the owner to PENDING and enqueues it once.
The manager sees the offending child/leg, target executor, plain reason and
valid effort options, and can correct the decision. No silent fallback.

Selected rows use source `manager`; ordinary unset handoffs use their target's
own current agent defaults and never inherit the manager's override. Linked
`revisit_of_task_id` retries carry their predecessor's tuple/source when both
fields are omitted. A supplied value replaces that field; explicit JSON null
clears it to the current default. Any explicit edit adopts manager provenance
for the resulting tuple; both cleared fields leave source NULL. Existing
lineage validation and retry ceilings remain authoritative.

Then settings persist as additive optional request keys in chain JSON and in
fanout carrier details. Omitted new keys stay absent, so legacy state loads and
unset child rows, state JSON, audit payloads and spawn argv retain their shapes
and bytes. Explicit null survives state round trips and orchestration decision
audits, including `exclude_none` serialization, at every nested handoff level.
This preserves the exact accepted-result/audit binding used by attachment
correction; omitted selection keys in historical decisions/audits stay absent
and continue to match without reader normalization. Later auto-advance copies
the saved choice to the new task row; PR1's unchanged start-time recheck runs
before executor construction. If executor/options changed, the leg fails with
`saved model no longer valid for this agent`, then follows ordinary failure,
parent wake and thread followup ordering without starting an unchosen model.
Existing task-created selection audits remain conditional on a selected field.

Workflow-owned/sequential coding decisions and non-task launchers are outside
this contract. No new schema, adapter contract or session_start key is added.

## Web surfaces (PR3 shipped)

The Tasks page has a New task dialog and Revisit has model/effort inputs.
Unset adds no selection key. Revisit prefills the predecessor's override:
untouched omits the field (carry), an edit sends the value and an emptied or
explicitly cleared field sends JSON null, per field. Task detail renders the
effective model and effort, the per-field source (founder, manager or agent
default) and the latest existing `session_start` audit row as what the most
recent session was launched with. A null effective value is "CLI default"
while the assigned agent is in the roster and "Unavailable" otherwise.

Options and disabling come from the target agent's current executor using
existing reads only: `/health/prereqs` effort values, runtime profiles and the
adapter list's `input_options`. No daemon route, response field or schema was
added and the Web holds no model/effort catalog. Custom executors without a
model and/or effort template disable that field with a reason; OpenCode effort
needs a pinned provider/model; the server-picked default manager has no
client-known executor, so both fields stay disabled until an agent is chosen.
HTTP422 `invalid_task_selection` is shown with reason, executor and valid
options; nothing falls back or is dropped. TypeScript mirrors live in
`web/src/lib/api/tasks.ts` and `types.ts`; states ship in English and zh-CN.
The Web does not offer overrides on thread dispatch or manager handoffs.

## Verification contract

Bounded task-scratch scripts drive real production seams using temporary SQLite,
canonical agent files and executable stub CLIs, never live DBs, daemon or providers.
Cases: legacy upgrade/NULL; unset argv/audit parity; all four built-ins; invalid
pair/unsupported field with zero allocation; custom a/b/c; revisit carry/change/
clear; start refusal after executor change; default change; retry carry and no
manager inheritance; dispatch fields and confirmation data. Record evidence and
limits in the PR. No retired Python unit/Vitest venue is restored. OpenAPI
`--write` and `--check` replace retired Web coverage. Required checks remain
lint/static, general integration SKIPPED and Product E2E separately authorized.

# Usage v1: parser semantics, reported state, and lifecycle attribution

> Status: current (PR1 through PR3 of 4)
> Current Source: `runtime/orchestrator/executors.py`,
> `runtime/orchestrator/usage_normalization.py`,
> `runtime/infrastructure/database.py`,
> `runtime/infrastructure/audit_logger.py`,
> `runtime/orchestrator/orchestrator.py`,
> `runtime/daemon/thread_runner.py`, `runtime/daemon/dream_runner.py`, and
> `docs/agent-guides/features-and-invariants.md`
> Authority: THR-272 seq64; Product requirements TASK-9165, sections 6 and 10;
> product_lead THR-272 seq68

## Scope

PR1 establishes the provider-independent usage seam needed by later Usage v1
read-model and UI work. It corrects Codex cache-write ingest, declares the
reasoning meaning of every verifiable built-in parser, normalizes one stored
row into reported states, and preserves an offline Codex resume regression.

PR2 captures executor/model cohort identity at the lifecycle start even when a
run later produces no usage. It adds two nullable thread-invocation columns and
additive fields on the existing task and dream start audit events. It does not
infer or backfill history.

Together these PRs do not rewrite old rows, change `TokenUsage.total`, change
the existing `happyranch tokens`/`GET /tokens` contract, build the PR3
lifecycle read model/coverage queries, or add a route or UI.

## Stored Codex contract

For the last terminal `turn.completed` event only:

- `input_tokens` remains `max(input_tokens - cached_input_tokens, 0)` when
  both source fields are integers (issue #216); otherwise its prior behavior
  is unchanged.
- `cache_read_tokens` remains `cached_input_tokens`.
- `cache_creation_tokens` is the exact integer
  `cache_write_input_tokens`, including `0`. Missing, `null`, strings, and
  other non-integers become `None`/SQL `NULL`.
- Output, reasoning, model, raw preservation, and last-event-only behavior are
  unchanged.

The 2026-09-30 read-only production sample found 962/962 Codex rows from the
previous seven days reporting integer cache write, all genuine zero. Historical
rows are not backfilled from `usage_raw_json`.

## Declared parser semantics

`PARSER_USAGE_SEMANTICS` is immutable data beside the parsers. The normalizer
is its only consumer; downstream code never switches on executor names.

| Parser | Reasoning declaration | Evidence |
| --- | --- | --- |
| Claude | `in_output` | The parser stores no separate reasoning value. Anthropic documents thinking as billed output tokens that count toward the output limit: [Extended thinking](https://platform.claude.com/docs/en/docs/build-with-claude/extended-thinking). |
| Codex | `in_output` | The Codex protocol displays reasoning as a breakdown of `output_tokens`, while its blended total adds output once: [Codex `TokenUsage`](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/protocol.rs). Its response parser fixture also has input 100, output 10, reasoning 5, total 110—not 115: [Responses usage conversion](https://github.com/openai/codex/blob/main/codex-rs/codex-api/src/sse/responses.rs). |
| OpenCode | `separate` | OpenCode's own stats and cost paths add `tokens.output` and `tokens.reasoning` separately: [stats](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/cli/cmd/stats.ts) and [session usage cost](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/session/session.ts). |
| Pi | `in_output` | Pi's public `Usage` type says `reasoning` is a subset of `output`, which already includes it: [Pi usage type](https://github.com/earendil-works/pi/blob/main/packages/ai/src/types.ts#L402-L415). |

The normalizer supports `absent` for a future parser whose provider contract
proves that reasoning is never a separate addend. Custom/generic executors are
undeclared. If an undeclared row reports reasoning, normalized Output is
`not_reported`; no consumer guesses from a CLI name.

## Normalized row model

Each token class returns `{state, value}` where state is `reported` or
`not_reported`. Reported integer zero is a real value. A missing or non-integer
stored value is absent, never zero-coerced.

### Fresh input

- With both uncached `input_tokens` and `cache_creation_tokens` reported:
  `reported(input + cache_creation)`.
- With input reported but cache write absent: `not_reported`, plus an explicit
  `partial_uncached_subtotal=input`. The subtotal is never presented as the
  complete metric.
- With input absent: `not_reported`, without a subtotal.

### Re-read

`cache_read_tokens` is reported exactly, including zero. Absence is
`not_reported`.

### Output

- Output absent: `not_reported`.
- `in_output` or `absent`: reported output, without adding reasoning.
- `separate`: reported output plus reported reasoning; absent reasoning adds
  nothing.
- Undeclared semantics plus a reported reasoning value: `not_reported`.
- Undeclared semantics with no reasoning value: reported output is safe.

### Parseable coverage predicate

`parseable` is true if and only if stored `input_tokens` and `output_tokens`
are both reported integers. This is the Usage v1 coverage numerator predicate
that PR3 must use. It is deliberately independent from class completeness: a
row can be parseable while Fresh input is `not_reported` because cache write
is absent.

## Codex resumed-conversation regression

THR-272 seq44 records a controlled two-turn resume test, and seq47 confirms
the result against live resumed conversations. The offline fixtures preserved
here are two consecutive turns from one resumed product-lead conversation on
deployed codex-cli 0.153.4:

| Turn | Input | Cached input | Cache write | Output | Reasoning output |
| --- | ---: | ---: | ---: | ---: | ---: |
| A | 2,634,338 | 2,452,096 | 0 | 10,353 | 5,108 |
| B | 545,539 | 460,160 | 0 | 1,827 | 979 |

Turn B is lower than turn A, which a cumulative counter cannot be. Tests parse
both objects independently and also place both `turn.completed` events in one
stdout stream to prove the parser returns the last event, never their sum.

### Required major-version re-verification

On every Codex CLI major-version change:

1. In an isolated disposable workspace, record `codex --version` and run one
   fresh `codex exec --json -` turn with a fixed short prompt. Preserve only
   the numeric terminal `turn.completed.usage` object.
2. Extract `thread.started.thread_id`, then run the next short prompt through
   `codex exec resume <thread-id> ... --json -`. Preserve its numeric terminal
   usage object.
3. Parse each turn independently with `_parse_codex_usage`; verify the second
   object represents that turn rather than approximately A+B. Feed both
   terminal events to one parser call and verify the second event wins.
4. Replace the two numeric fixtures, update the recorded CLI version and this
   evidence table, and run the focused parser/normalizer/database tests.
5. If the check is ambiguous or cumulative, withhold Codex Efficiency until
   the semantics are understood. Codex Workload remains available.

This procedure is opt-in and never runs live in CI.

## Lifecycle-side executor and model attribution

Efficiency cohort membership comes from lifecycle rows, never from an inner
join to usage and never from an agent's current configuration.

### Thread invocations

`thread_invocations` has two additive nullable TEXT columns:

- `executor`: the effective executor kind selected for the invocation;
- `model`: the configured model argument passed to the executor, or NULL when
  no model is supplied and the executor chooses its default.

Fresh DDL includes both columns. Existing databases add only a missing column
on open, so pre-column, repeatedly opened, and one-column-partial databases
converge idempotently. The migration has no default, `NOT NULL`, index, data
rewrite, or inference; every legacy row remains NULL.

Both production writers that set `thread_invocations.started_at` write the
same effective executor/model tuple in that same UPDATE:

1. `Database.claim_conversational_reply`, whose queued-to-running transaction
   stamps a conversational reply before prompt work; and
2. `Database.stamp_invocation_started`, used by the runner for the invocation
   session ID and for non-reply start stamping.

The runner resolves one live `AgentDef` tuple and reuses it for both writers
and the actual executor call. An unavailable agent that launches no provider
gets no guessed attribution.

### Task session starts

The task `session_start` payload keeps the existing `workspace` value and adds
exactly:

```json
{
  "workspace": "<unchanged workspace path>",
  "session_id": "<daemon runtime invocation session>",
  "invocation_purpose": "worker_execution | manager_decision | unattributed",
  "executor": "<effective executor>",
  "model": "<configured model or null>"
}
```

The runtime session ID is the same value written to
`session_token_usage.session_id` for that run; provider conversation IDs remain
separate. The purpose is selected from the immutable spawn mode already used
to build the launched prompt:

- decision-capable `task` spawn → `manager_decision`;
- leaf `subtask` spawn → `worker_execution`;
- a mode outside those two → `unattributed`.

This is deliberately independent of agent title, assigned role, task owner,
and root/leaf position. A manager's bounded self-delegated subtask is worker
execution; a worker launched as a decision-capable task is a manager decision.

THR-247 completion recovery is a callback-only launch rather than either v1
task run type, so its `session_start.invocation_purpose` is `unattributed`.
It remains durably distinguishable from ordinary sessions without another
payload field: `task_completion_recoveries.recovery_session_id` uniquely binds
the recovery runtime session. PR3 may use that existing relation.

### Dream starts

`dream_started` has the exact additive payload
`{"executor": <effective executor>, "model": <configured model or null>}`.
The tuple is resolved before the event and is the tuple later passed to
`executor.run`.

### Missing-data boundary

No configured model means JSON/SQL NULL—never the literal `"default"` and
never a guessed model. Old task/dream event payloads and old thread rows stay
historical. PR3 must surface their lifecycle attribution as unknown and must
not make a missing usage row determine cohort membership.

## Legacy total boundary

`TokenUsage.total`, database churn, and `GET /tokens` still compute
input + output + reasoning. Because Codex output already includes reasoning,
that legacy metric double-counts Codex reasoning. This known pre-existing
issue is outside Usage v1 PR1; normalized Output does not double-count it.

## PR3 lifecycle read model and API

PR3 adds bearer-authenticated, read-only `GET /usage/workload` and
`GET /usage/efficiency`. Both return `generated_at`, `data_through`, rolling
UTC window bounds, their rendering in the effective org timezone, and the
timezone name. Current is `[data_through - 7 days, data_through)`; comparison
is the equal adjacent interval immediately before it. These are always 168-hour
UTC intervals, including across local DST changes.

Workload reads task `session_start`, ordered task `session_end`, started
`thread_invocations`, accepted `task_results`, and consumed reply invocations.
An end closes the starts since the prior end only when that segment has exactly
one start; otherwise every start in the segment lacks runtime. Deliveries are
distinct completed task IDs whose completed result session maps to a
`worker_execution` start. Results with missing/unattributed purpose are counted
separately as incomplete history.

Efficiency establishes the five run types and executor/model cohorts entirely
from lifecycle facts before optional usage correlation. NULL model is a real
`CLI default (not pinned)` cohort; NULL executor and legacy missing purpose are
unattributed. THR-247 recovery sessions are identified by
`task_completion_recoveries.recovery_session_id`, count in Workload, and remain
outside all five Efficiency rows without suppressing their comparisons.

Usage correlation is intentionally key-specific:

- task: scope `task` plus task ID, agent, and `session_start.session_id`;
- thread: scope `thread` plus thread ID, agent, and the invocation runtime
  session ID, with `invocation_token` allowed only as the persistence fallback;
- dream: scope `dream` plus `dream_started.task_id`/`dreams.id` and agent.

Dream provider session IDs and nullable `dreams.session_id` are never join
keys. More than one candidate usage row for one lifecycle run is ambiguous:
the run stays in the denominator but supplies no parseable observation and no
token value. All selected rows pass through `usage_normalization.py`.

Medians use `statistics.median` over reported values only (for an even count,
the arithmetic mean of the two middle values) and carry class-specific
denominators. Decline waste applies only to thread rows and includes explicit
agent declines; system closures (`participant_removed`, `agent_terminated`,
`agent_unavailable`, plus failed `daemon_restart`, `coalesced_cutover`, archive,
and founder-abort paths) are failures. The three reported token classes remain
separate known totals.

Comparison is server-computed. Workload uses absolute native-unit movement.
Efficiency withholds every row delta if either non-empty period has usage
coverage below 95%, or unattributed lifecycle facts could belong to the row.
Otherwise Runs handles previous-zero/current-positive as `new_from_zero`,
current-zero as an absolute negative count, and both-zero as `no_change`.
Each token/decline metric independently uses `withheld: invalid_baseline` when
a period has no valid observation; infinity and NaN are never emitted.

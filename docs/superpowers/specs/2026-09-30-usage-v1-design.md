# Usage v1: parser semantics and normalized reported state

> Status: current (PR1 of 4)
> Current Source: `runtime/orchestrator/executors.py`,
> `runtime/orchestrator/usage_normalization.py`, and
> `docs/agent-guides/features-and-invariants.md`
> Authority: THR-272 seq64; Product requirements TASK-9165, sections 6 and 10

## Scope

This PR establishes the provider-independent usage seam needed by later Usage
v1 read-model and UI work. It corrects Codex cache-write ingest, declares the
reasoning meaning of every verifiable built-in parser, normalizes one stored
row into reported states, and preserves an offline Codex resume regression.

It does not add a database column, migrate or rewrite old rows, change
`TokenUsage.total`, change the existing `happyranch tokens`/`GET /tokens`
contract, build lifecycle coverage, or add a route or UI.

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

## Legacy total boundary

`TokenUsage.total`, database churn, and `GET /tokens` still compute
input + output + reasoning. Because Codex output already includes reasoning,
that legacy metric double-counts Codex reasoning. This known pre-existing
issue is outside Usage v1 PR1; normalized Output does not double-count it.

# Thread And Talk Token Usage Scope Design

**Status:** Approved for implementation
**Date:** 2026-06-08
**Issue:** https://github.com/t-benze/happyranch/issues/67

## Problem

HappyRanch records token usage for normal task executor sessions in
`session_token_usage`, including tasks dispatched from threads and talks. Direct
thread agent invocations are also executor sessions, but `thread_runner.py` does
not persist their `ExecutorResult.token_usage`. Token reporting therefore cannot
answer which threads, agents, invocation purposes, or resumed thread sessions are
driving model usage.

Talk lifecycle routes do not currently run executors. Talk-related usage exists
today only through tasks dispatched from talks, so reporting needs to distinguish
lifecycle-only talks from talk-dispatched task usage and leave an explicit path
for future direct talk executor sessions.

## Decision

Keep `session_token_usage` as the single usage fact table and make it
scope-aware. Existing task reports keep working through `task_id`, while new
columns add explicit attribution:

- `scope_type`: `task`, `thread`, or `talk`
- `scope_id`: the primary scoped id, such as `TASK-001`, `THR-001`, or `TALK-001`
- `thread_id`: populated for direct thread invocations and thread-dispatched task rows when known
- `talk_id`: populated for direct talk sessions and talk-dispatched task rows when known
- `invocation_purpose`: thread invocation purpose such as `reply`, `bootstrap`, or `task_followup`

Direct thread invocations insert rows with `scope_type="thread"` and
`scope_id=<thread_id>`. Existing task writes insert `scope_type="task"` and
`scope_id=<task_id>`, preserving `task_id` for compatibility.

## Data Flow

Task execution remains:

`ExecutorResult.token_usage -> run_step.py -> insert_session_token_usage(task scope)`

Thread invocation becomes:

`ExecutorResult.token_usage -> thread_runner.py -> insert_session_token_usage(thread scope)`

The thread runner persists usage whenever the executor returns a `TokenUsage`,
including no-callback or failed invocations if usage is present on the result.
Runner crashes before an executor result remain untracked because no usage fact
exists.

## API And Reporting

`GET /api/v1/orgs/{slug}/tokens` keeps existing filters and rollups, then adds:

- filters: `scope_type`, `scope_id`, `thread_id`, `talk_id`, `purpose`
- rollups: `group_by=scope`, `group_by=thread`, `group_by=talk`

`group_by=task` remains task-shaped and returns normal task rows. Talk lifecycle
routes do not create usage rows. Talk-dispatched task usage can be queried with
`talk_id=<id>` once task rows carry that attribution, and a future talk runner
should insert `scope_type="talk"` rows.

## Compatibility

The migration is append-only. Existing SQLite databases gain nullable scope
columns and indexes. Legacy rows with null `scope_type` are treated as task rows
for reporting by using `COALESCE(scope_type, 'task')` and
`COALESCE(scope_id, task_id)` in read paths.

`task_id`, `agent`, `session_id`, `executor`, token columns, and existing
`group_by=agent|task` behavior stay compatible.

## Testing

Tests cover:

- schema and storage round-trip for scoped thread rows
- legacy task filtering and grouping still working
- token route filtering and grouping by thread/scope/talk
- successful thread invocation writes a token row
- failed/no-callback thread invocation still writes usage if the executor result
  carries `TokenUsage`
- route help/docstring explains task, thread, and talk scope behavior

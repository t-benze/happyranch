# Explicit thread resolution and safe auto-archive v1

Founder authorization: THR-339 seq2–5; implementation delegations TASK-14487 (PR1) and TASK-14508 (PR2)
under engineering_manager TASK-14477. This finalizes the revised memo after
TASK-14479 established that `mentions_json` contains only agent participants.

## Delivery boundary

PR1 provides explicit resolution state, resolving sends, clearing, audits and
read projections. PR2 activates automatic archiving after tracked obligations
settle, using the existing manual archive owner and shared transcript publisher.
There is no new channel, privacy model, ThreadStatus or TaskStatus lifecycle.
Founder-created and dream-composed threads remain manual archive only.

## State and admission

ThreadStatus remains `open|archived`. Resolution is orthogonal:

| Current state | Event | Result |
| --- | --- | --- |
| Open, unresolved | Eligible resolving send | Open, resolved |
| Open, resolved | Any later MESSAGE, including founder or invocation reply | Open, unresolved |
| Open, resolved | Settled obligations and no founder hold | Archived |
| Archived | Existing founder `/resume` | Open, unresolved |

Silence, decline, timeout, elapsed time and task termination never set
resolution. System and decline messages do not clear it. Another resolving
MESSAGE clears the prior resolution and then sets its own, with both audits.

Eligible threads have non-NULL `composed_from_task_id` and `composed_by !=
'founder'`; otherwise resolution refuses403 `thread_not_task_composed`. An
eligible actor is the opener or the opener's live executable team manager,
resolved through `team_for_agent` and `executable_manager_for_team`. A human
head (`name=None`) grants no agent manager eligibility. Other participants
refuse403 `thread_resolution_not_authorized`; nonparticipants still refuse403.
Both agent routes retain the existing active, non-recovery task/session binding.
The resolving task need not be the task that composed the thread.

## Existing send API and CLI

Both `/threads/{id}/send` with agent binding and `/threads/{id}/post-as-agent`
accept optional `resolution: {"next_owner": null}` through the shared
`_post_agent_message`. `next_owner` may be omitted, null or a known active agent's
permanent ID. Unknown IDs refuse422 `unknown_next_owner`; `founder` refuses409
`founder_decision_pending`. Names do not confer actor or owner authority.
The resolution object rejects extra keys and non-string/non-null owners.

The nonempty message body is the decision record; whitespace-only or
attachment-only resolution refuses422 `resolution_body_required`. A founder send
cannot set resolution (422 `binding_required`). The resolving message mints no
REPLY invocations, including no exchange catch-up tokens. Existing delivery
obligations and exchange holds remain intact; no acknowledgement round follows.

`happyranch threads send --from-file ABS --task-id TASK --session-id SID` passes
the optional object unchanged with `composer` and substantive `body_markdown`.
There is no new verb, UI control or browser resolution writer.

## Founder hold

`MessageAddresses.founder_addressed` defaults false and becomes true if any
classified subject has `kind='founder'`, including the founder's current name.
Every live MESSAGE append persists `addresses_founder` as INTEGER0/1 in its
existing transaction; non-MESSAGE appends write0. `founder_only`, canonical
`mentions_json`, wake sets and ordinary exchange handling retain their routing
behavior. The new bit observes addressing and grants no routing authority.

For historical NULL rows only, use the pure `parse_mentions(body_markdown)`
parser and test token `casefold() == 'founder'`. No namespace lookup, backfill or
reinterpretation of historical routing occurs. Historical personal-name founder
addresses cannot be detected; use the permanent `@founder` transport token.

H(thread) holds if a non-founder MESSAGE addresses the founder and its seq
exceeds the latest founder MESSAGE seq (or0 if none). Resolution includes its
own proposed message in this check, so it cannot bury a new founder request.
While H holds, refuse409 `founder_decision_pending` without appending. A founder
reply releases older holds. A founder MESSAGE after resolution clears it; a
founder question before resolution is answered by the substantive resolving
message. Pending founder-created REPLY obligations remain separately tracked.

## Persistence and atomicity

The existing ALTER-if-missing path adds nullable `threads.resolved_seq INTEGER`,
`resolved_by TEXT`, `resolved_at TEXT`, `resolved_from_task_id TEXT`,
`resolution_next_owner TEXT` and `thread_messages.addresses_founder INTEGER`.
There is no new index. Fresh creation uses the same additive path; no fresh
executescript reference to a new column can break a legacy open. Existing data
remains NULL and unmodified.

The arrival transaction already owns BEGIN IMMEDIATE/COMMIT. It rechecks open
status, task composition, participation and founder hold, appends the message
and address bit, sets resolution and inserts its audit before commit. There is
no nested BEGIN. The common uncommitted append clears all five resolution
fields for later MESSAGE rows and writes the clearing audit in the same
transaction. Resume reopens and clears with its resume audit atomically, then
retains the existing delivery cutover/system-message path.

List and detail JSON expose all five nullable resolution fields read-only.
ThreadRecord and message readers include the new persisted fields; TS consumers
allow absent resolution fields when talking to an older daemon. OpenAPI's
maintained summary pins routes/parameters/status codes, not component fields;
regeneration may therefore be byte-identical. Route coverage stays unchanged.

Audit scope follows existing thread convention `task_id=<thread_id>`:

| Action | Agent | Payload |
| --- | --- | --- |
| `thread_resolved` | Resolver ID | `{seq, resolved_from_task_id, next_owner}` |
| `thread_resolution_cleared` | Clearing speaker, including `founder` | `{resolved_seq, cleared_by_seq}` |
| `thread_resumed` | `founder` | Existing `{prior_archived_at}` plus `cleared_resolution_seq` (nullable) |

## Auto-archive (PR2)

`maybe_auto_archive_thread(org, thread_id, trigger)` requires open, resolved,
eligible, settled obligations and no founder hold. Settled means no pending
thread invocations of any purpose and no live delivery state or held exchange
deferrals on open exchanges. Derive the exact owner predicate from
`archive_thread_and_reset_sessions` so archive and eligibility cannot drift.
Text-only promises such as "I'll get back to you" are untracked in v1: do not
resolve while they remain actionable.

The archive transaction rechecks all predicates at decision time, including
`composed_from_dream_id IS NULL`. Its shared owner relation includes pending
invocations of every purpose, queued/running/unacknowledged delivery ranges and
held deferrals on open exchanges. Settled delivery rows and breaker history do
not block. PR-3b removes the move-only participant-write hold from archive
admission; native predicates remain. The transaction archives,
resets sessions, appends the system message as `orchestrator`, and commits
`thread_archived` with agent `orchestrator`, task_id equal to the thread ID and
payload `{turns_used, trigger, resolved_by, resolved_seq}` atomically.

The shared non-daemon helper then writes the transcript, persists its path and
publishes the existing detail/inbox SSE. Founder manual archive keeps its
`founder` system speaker and exact `{turns_used}` audit payload. Publication
failures are logged; native COMMIT is not rolled back by a filesystem/SSE failure.
The founder can inspect and resume any archived thread; resume clears resolution.

Evaluation runs after resolving-send commit, after any thread invocation
becomes terminal, after composed-from task termination (completed, failed,
cancelled or superseded), and in one daemon-startup sweep of open resolved
threads after recovery. The existing exchange reaper/reconcile also reevaluates
when the last held deferral is released without an invocation terminal. Triggers
are post-commit, idempotent and best-effort; logged failures never fail the
triggering operation. Task termination only evaluates; unresolved threads stay
open with the composer visible, including threads composed by subtasks.
Silence, declines, elapsed time and task termination never establish resolution.

## Reserved decisions

New channels, privacy models, automatic closure of founder-created threads and
TaskStatus lifecycle changes require returning to the founder. Normal independent
review, scoped QA, lint/static CI, hooks and guarded manager merge still apply.
Python units remain retired; general integration is SKIPPED THR-243 seq42.

## 2026-11-11 clarification: explicit reply/decline resolution

The historical statements above that decline never sets or establishes
resolution apply to plain declines without the explicit `resolution` object.
The [2026-11-11 invocation-resolution addendum](2026-11-11-thread-invocation-resolution.md)
extends resolution to the opener's or its current executable team manager's
REPLY or TASK_FOLLOWUP answer turn using `threads reply` or `threads decline`,
under its atomic eligibility, answer-turn, staleness, founder-hold and next-owner
guards. Both resolving callbacks also trigger post-commit archive evaluation
alongside send. Later MESSAGE and founder resume still clear resolution;
silence, plain declines, elapsed time and task termination never establish it.

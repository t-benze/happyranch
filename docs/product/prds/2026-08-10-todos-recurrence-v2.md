# Todos Recurrence v2 PRD

| Field | Value |
| --- | --- |
| Status | draft — revised 2026-08-11 |
| Owner | product_lead |
| Date | 2026-08-10 |
| Source Links | THR-105 seq1–20 (v1 rulings); seq223–226 (draft-only authorization); seq242–254 (review corrections); PR #635 (revised design spike); `docs/product/prds/2026-07-19-agent-todos.md` (v1); `protocol/skills/todos/SKILL.md` (current v1 contract) |
| Commitment Boundary | analysis-only — this PRD does not authorize implementation, migration execution, a dependency, schema change, or a delivery commitment. |
| Founder Decisions | Ruled: agent-owned, explicit-instruction-only, session-bound and self-targeted Todos; v1 controls remain mandatory. Required: ratification of this reconciled v2 contract, the design-spike sign-off items, and separate build authorization. |

## Decision summary and reconciliation

**Recommendation:** ratify this bounded recurrence contract as Todos v2, then
separately decide whether to authorize engineering planning. It extends the
shipped v1 *schedule expression*, not who may schedule or what authority they
hold. A Todo remains a visible, revocable agent commitment that dispatches one
normal root task when an occurrence succeeds.

This revision incorporates the THR-105 seq242–254 review contract and aligns
the product vocabulary with revised design PR #635. In particular, a cadence
has a durable anchor; After-N counts successful dispatches rather than calendar
candidates; a failed occurrence does not terminate a recurring commitment;
review expiry is renewable before it takes effect; and terminal `FIRED` states
carry a persisted reason. These are product behavior, not implementation
details.

This PRD supersedes only the recurrence portion of the 2026-07-19 Agent Todos
PRD when ratified. It preserves v1's user-facing name (Todos), internal
primitive (Schedule), autonomous arming from explicit founder/operator
instruction, self-targeting, immutable source instruction and normalized
brief, founder controls, audit, and 20-per-agent / 100-per-org active-Todo
caps. The current v1 source is one-shot plus single-weekday weekly recurrence;
this draft makes no claim that v2 is built.

## Problem

The v1 Saturday market-update use case is useful but too narrow. An agent
cannot truthfully normalize “every other Tuesday and Thursday,” “the first
Monday of each month,” or “the 31st, ending after six successful reports.”
Multiple weekly Todos or manually chained one-shots make the founder review
surface less legible and create duplicate, stale, or silently stopped work.

The product need is flexible **scheduled commitments**, not calendar software.
The rule must remain understandable in the founder-visible Todo, deterministic
across timezone and DST boundaries, and bounded by the existing control
envelope.

## Users and workflow

The founder/operator gives an explicit timed instruction. A valid in-org agent
with an active session normalizes it into a structured, reviewable Todo for
itself; it cannot target another agent. The system stores the civil-time rule,
its cadence anchor, and the derived next UTC fire instant. The founder can
inspect the human-readable rule, normalized brief, source instruction, status,
review expiry, next fire, outcomes, terminal reason, and provenance; they can
pause, cancel, edit permitted schedule fields, or renew review authority.

At each due occurrence, the Todo makes at most one normal root-task dispatch.
It then advances to the next eligible occurrence, naturally ends, expires for
review, or records a visible failed/missed result. It never catches up a
backlog or silently retries an occurrence.

## Goals

- Express useful recurring commitments without cron or sub-daily scheduling.
- Make every scheduled fire explainable from stored rule, timezone, anchor,
  terminal reason, and audit facts.
- Preserve founder control over longer-lived agent token-spending commitments.
- Make edits, expiry, migration, and failures safe and predictable.

## Non-goals and strict no-list

- No cron, seconds/minutes/hourly recurrence, or sub-daily cadence.
- No cross-agent scheduling, hidden schedules, agent-side re-arming, or
  scheduling without explicit founder/operator instruction.
- No general Todo backlog, priorities, tags, subtasks, shared lists, invites,
  availability, calendar grids, or calendar-suite integrations.
- No multiple daily time slots, holiday/business-calendar rules,
  natural-language-only arming, or inferred “best time.”
- No monthly selector lists, negative wire values, “last day of month” shortcut,
  or yearly by-month/by-day grammar beyond the anchor day.
- No automatic conversion of legacy one-shot chains, no backfill/catch-up run
  burst, no implicit retry, and no new spend dashboard.
- No implementation authorization, roadmap date, or external commitment.

## v2 cutline: normalized recurrence contract

Every native recurring Todo stores a unit, positive interval, IANA timezone,
minute-precision local time, server-computed `anchor_date`, exactly one
unit-specific selector, an end selector, `review_expires_at`, derived
`next_fire_at` in UTC, counters, and occurrence outcome facts. The normalized
rule—not source-language interpretation—is the executable and reviewable source
of truth. `fire_at` is a derived next UTC instant and never defines cadence.

| Unit | Selector | Meaning |
| --- | --- | --- |
| `day` | none | Every `interval` local calendar days from the anchor date. |
| `week` | `weekdays`: one to seven Mon–Sun values | Every `interval` ISO weeks, anchored to Monday of the anchor’s local week; each selected day is eligible. |
| `month` | exactly one `month_day` (1–31) **or** one `ordinal_weekday` | Every `interval` local calendar months from the anchor month. An ordinal is first, second, third, fourth, fifth, or last plus one weekday. |
| `year` | anchor month/day | Every `interval` local calendar years from the anchor year. |

`interval` is a positive integer and the smallest cadence is daily. A Todo has
one local time and one timezone. A fortnightly Tue/Thu Todo fires on both
selected days in each eligible ISO week. In its anchor week, only selected
occurrences at or after creation/edit are eligible; earlier days are never
retroactively fired.

### Stable anchor and edit semantics

`anchor_date` is server-computed from the local date of the first calculated
occurrence at creation and cannot be caller-supplied. It never drifts as a Todo
fires or re-arms. It is immutable for a given rule version, which prevents an
“every two weeks” or “every two years” cadence from being re-phased by `now` or
by the last successful run.

- A timing-only edit (local time or timezone) retains `anchor_date`; future
  instants change, but the existing cadence phase does not.
- A rule-shape edit (unit, interval, weekday, monthly date, or ordinal-weekday)
  creates a new rule version and resets `anchor_date` to that version’s newly
  calculated next local occurrence date. The edit must be atomically validated
  and audit before/after rules including the anchor.
- Edits calculate only an occurrence strictly after the edit; they never create
  a retroactive occurrence. An `ARMED` or `PAUSED` Todo may be edited. A Todo
  already claimed as `FIRING`, or terminal, rejects the edit with
  `state_conflict`; this is the required edit-during-fire race rule.

### End conditions and review control

The required end selector is exactly one of:

- `never`: no calendar or successful-dispatch end condition;
- `on_date`: inclusive local calendar date in the Todo timezone; or
- `after_count`: positive total number of successful root-task dispatches.

`after_count` increments only after exactly one root-task creation has
committed and its ID is stored. It is a successful-dispatch counter outside the
calendar generator: it must never be expressed as, or fed to, RRULE `COUNT`.
A missed, failed, timed-out, DST-skipped, missing-date, or cancelled occurrence
does not consume it. When the count is reached, the Todo naturally ends before
another occurrence is calculated.

The safety review window is independent of the end selector. Recurring Todos
have a 90-day `review_expires_at`; an expiry-time tie expires rather than fires.
`never` therefore means no *recurrence* end, not invisible perpetual authority.

The founder/operator can renew an `ARMED` or `PAUSED` Todo before expiry. A
normal renewal resets the review window to 90 days; an explicit
founder/operator-only indefinite grant is also auditable. Renewal changes only
review authority—it does not change the rule, anchor, next fire, or fire count.
`FIRING`, terminal, and already `EXPIRED` Todos reject renewal with
`state_conflict`; an expired Todo is not resurrected and requires a fresh
replacement.

## Deterministic time, occurrence, and lifecycle semantics

- Recurrence is calculated in the stored IANA timezone using civil local date
  and time; `next_fire_at` is the corresponding UTC instant. Display may
  localize but never changes the rule.
- On DST fall-back ambiguity, fire at the earlier matching instant (`fold=0`)
  and audit/display its UTC offset. On DST spring-forward gaps, skip that
  occurrence, audit `occurrence_skipped_dst_gap`, and continue; never shift to
  another wall time.
- A monthly day 29–31 skips months lacking that date. A fifth ordinal weekday
  skips months without one. A Feb 29 yearly anchor skips non-leap years.
  These `occurrence_skipped_missing_date` outcomes do not consume After-N.
- `on_date` compares the candidate’s **local calendar date**, inclusively. The
  DST-boundary case `until=2026-11-01` in `America/New_York` permits an
  occurrence on Nov 1 regardless of offset, and rejects Nov 2.
- A stale due occurrence is skipped, audited as `occurrence_missed`, and the
  Todo advances only to the next future eligible occurrence; no catch-up or
  batch execution. The stale tolerance remains an operational/runtime
  parameter (currently 120 seconds in v1), not a changed product promise.
- The stable occurrence key is `(schedule_id, fire_at)` at claim. Claiming
  atomically changes an occurrence from `ARMED` to `FIRING`; a concurrent tick,
  duplicate claim, or restart must not create a second root task for that key.
  Re-arming changes `fire_at`, so it creates a different future occurrence key.
- A claimed `WEEKLY` or native recurring occurrence that fails or times out is
  audited (`schedule_failed` or `schedule_timeout`), does not increment
  After-N, and advances to the next eligible occurrence while remaining
  `ARMED`. It never implicitly retries the failed key. `ONE_SHOT` remains
  terminal on failure/timeout because it has no subsequent occurrence.
- “Needs attention” is a founder-visible computation, not a new status: it is
  true when the most recent claimed-occurrence failure/timeout is newer than
  the latest successful dispatch/end event. The Todo remains visibly armed for
  its next occurrence.

### Persisted terminal meaning

`FIRED` means naturally complete and must persist a non-null `end_reason`:

| Status / reason | Set when |
| --- | --- |
| `FIRED` / `one_shot_completed` | A one-shot dispatch succeeds. |
| `FIRED` / `count_exhausted` | A successful dispatch reaches After-N. |
| `FIRED` / `date_ended` | The next calendar candidate would be after `on_date`, leaving no eligible occurrence. |
| `EXPIRED` | Review authority expires before a due occurrence, or the next otherwise-valid occurrence would exceed review expiry. |

`EXPIRED` is reserved for review expiry, not calendar-date exhaustion.
`end_reason` is null for non-`FIRED` states. Each `FIRED` transition audits the
reason and the founder-visible surface renders it as plain language rather than
a bare “Fired” label.

## Rule validation and actionable errors

Creation and edit validate the whole candidate rule atomically. The API returns
stable, actionable error codes rather than only a generic create failure:

- `invalid_interval`, `invalid_time`, `invalid_timezone`, `invalid_until`,
  `invalid_count`, and `end_condition_conflict`;
- `invalid_freq_fields` and `invalid_byday`;
- `monthly_selector_missing` or `monthly_selector_conflict` when monthly does
  not contain exactly one valid selector; and
- `anchor_date_not_settable` when a caller attempts to set the server-owned
  anchor, plus `state_conflict` for a disallowed lifecycle action.

The bounded monthly grammar accepts exactly one positive `month_day` from 1–31
**or** exactly one weekday paired with a named ordinal (`first`, `second`,
`third`, `fourth`, `fifth`, `last`). It accepts neither lists nor negative wire
values. Unsupported founder instructions must be refused and clarified, never
approximated.

## Safety, data, provenance, and observability

All v1 controls remain release-blocking: valid active session and resolvable
in-org team; self-target only; explicit founder/operator instruction; 20 armed
Todos per agent; 100 armed Todos per org; initial next fire inside the existing
90-day horizon; immutable source instruction and normalized brief;
founder-visible controls; and complete audit trail.

The list/detail and audit render stored facts: rule version and normalized
recurrence; anchor/timezone; next UTC instant and display offset; end/review
conditions; status and terminal reason; created/edited/renewed/expiry times;
successful, skipped, missed, failed, and timed-out counters; occurrence key;
spawned task IDs; immutable instruction/brief; and actor/reason for every
founder control. Required outcome events are creation, edit, pause, cancel,
renewal/indefinite grant, occurrence claimed, dispatched, failed, timed out,
DST skipped, missing-date skipped, missed, count-ended, date-ended, and
expired. Monitoring exposes due-to-claimed lag, misses/skips/failures,
duplicate-dispatch preventions, cap denials, and expiry volume; it is not a new
analytics suite.

## Migration and compatibility

- Existing `weekly` rows remain physically unchanged. They receive the same
  recurring failure-continuity behavior but retain their v1 stored shape and
  evaluation path. Read/list surfaces may project them into the v2 vocabulary
  (including display-only synthesized anchor), without data rewrite or cadence
  change.
- Existing one-shots remain one-shots with their ID, instant, provenance, and
  audit history intact.
- One-shot chains have no reliable recurrence intent. Migration must preserve
  every node as its own one-shot and any linkage as display-only provenance; it
  must not infer cadence, merge nodes, create fires, or delete a chain.
- A future founder/operator conversion of a reviewed chain is an operational
  cutover, not automatic migration: cancel the armed legacy link first, then
  arm one native rule with its intended next occurrence, then verify that no
  armed/paused legacy link remains. The operator determines the exact live
  anchor and timing; this PRD does not authorize execution.

## Acceptance criteria

- A founder can determine exact next UTC fire, local rule, cadence anchor,
  review expiry, last outcome, and terminal reason without interpreting free
  text or implementation internals.
- Valid day/week/month/year examples are deterministic; invalid interval,
  selector, end, anchor, and lifecycle inputs produce the named actionable
  error code. Monthly rules permit only the bounded single-selector grammar.
- A fortnightly Tue/Thu rule fires both selected days in eligible weeks, never
  in intervening weeks, and timing-only edits preserve its phase while
  rule-shape edits reset it visibly and prospectively.
- DST gaps/fall-back, missing month dates, leap-day years, inclusive end-date,
  stale observation, and dispatch failure each produce the specified outcome
  and inspectable audit record.
- After-N increments only after a successful root-task dispatch. A missed,
  skipped, failed, or timed-out occurrence neither consumes the count nor
  retries itself. No occurrence key can create more than one root task.
- Recurring and weekly Todos survive one failed/timed-out occurrence, advance
  to the next eligible one, and show Needs attention; one-shots retain terminal
  failure behavior.
- Count exhaustion and date exhaustion produce `FIRED` with the correct
  persisted reason. Only review expiry produces `EXPIRED`.
- A founder/operator can proactively renew an armed/paused Todo while retaining
  provenance and history; expiry is never silently undone.
- v1 rows and one-shot chains preserve provenance and never trigger inferred
  recurrence, backfill, automatic fire, or cap increase.

## Remaining gate and risks

The sole product gate is founder ratification of this reconciled contract,
followed by separate authorization for build planning and implementation. No
time, staffing, dependency, or delivery commitment follows from this draft.

The main risks are recurrence complexity obscuring founder intent, cost from
longer-lived schedules, and timezone edge cases eroding trust. The normalized
rule, stable anchor, bounded grammar, review renewal/expiry, explicit
skip/failure outcomes, occurrence-key idempotency, persisted terminal reason,
and no-backfill policy are required mitigations.

### Re-review focus

This PRD preserves the reconciled terminal rule: exhaustion of `on_date` must
become `FIRED/end_reason=date_ended`; `EXPIRED` is only review expiry. PR #635
§7.2 now applies the same cause-aware branch ordering, so no terminal-state
divergence remains between the documents. Final re-review should confirm this
wording retains that already-reconciled contract.

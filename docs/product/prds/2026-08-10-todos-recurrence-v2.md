# Todos Recurrence v2 PRD

| Field | Value |
| --- | --- |
| Status | draft |
| Owner | product_lead |
| Date | 2026-08-10 |
| Source Links | THR-105 seq1–20 (v1 rulings); THR-105 seq223–226 (authorization to draft this product definition only); `docs/product/prds/2026-07-19-agent-todos.md` (v1); `protocol/skills/todos/SKILL.md` (current v1 contract) |
| Commitment Boundary | analysis-only — this PRD does not authorize design, implementation, migration execution, or a delivery commitment. |
| Founder Decisions | Ruled: Todos remains agent-owned, explicit-instruction-only, session-bound and self-targeted; founder/operator controls and the v1 safety envelope remain mandatory. Required: founder ratification of this v2 contract and separate authorization before any build work. |

## Decision summary and reconciliation

**Recommendation:** ratify this bounded recurrence contract as Todos v2, then
separately decide whether to authorize engineering planning. It extends the
shipped v1 *schedule expression*, not the authorization model or the product
category. A Todo is still a visible, revocable agent commitment that dispatches
one normal root task when due.

This PRD supersedes the recurrence portion of the 2026-07-19 Agent Todos PRD
only when ratified. It preserves v1's user-facing name (Todos), internal
primitive (Schedule), autonomous arming from explicit founder/operator
instruction, valid-session self-targeting, immutable instruction provenance,
founder list/detail/pause/cancel/edit controls, audit, and 20-per-agent / 100-
per-org active-Todo caps. The current v1 source is one-shot plus one weekday
weekly recurrence; this draft is intentionally not a claim that v2 is built.

## Problem

The v1 Saturday market-update use case is useful but overly narrow. An agent
cannot truthfully normalize instructions such as “every other Tuesday and
Thursday,” “on the first Monday of each month,” or “every 31st, stopping after
six runs.” Workarounds—several separate weekly Todos or manually chained
one-shots—make the founder review surface harder to understand and increase the
chance of duplicate, stale, or unbounded work.

The product need is flexible **scheduled commitments**, not calendar software.
The recurrence rule must remain understandable in the founder-visible Todo,
deterministic across timezones and daylight saving time (DST), and bounded by
the same control envelope that made autonomous v1 scheduling acceptable.

## Users and workflow

The founder/operator gives an explicit timed instruction. A valid in-org agent
with an active session normalizes it into a structured, reviewable Todo for
itself; it cannot target another agent. The system validates the rule and
stores the civil-time recurrence plus the next UTC fire instant. The founder can
inspect its human-readable rule, normalized brief, source instruction, status,
review expiry, next fire, prior outcomes, and provenance; they can pause,
cancel, renew/review, or edit allowed schedule fields.

At each due occurrence, the Todo attempts one normal root-task dispatch for its
owner. The record then either advances to the next eligible occurrence, reaches
its selected end condition, expires pending review, or records a visible
failure/miss. It never silently catches up a backlog.

## Goals

- Express a useful, finite set of ordinary recurring commitments without cron.
- Make every future fire explainable from stored rule, timezone, anchor, and
  outcome facts.
- Preserve founder control over an agent's future token-spending commitments.
- Make edit, expiry, migration, and failure behavior safe and predictable.

## Non-goals and strict no-list

- No cron syntax, seconds/minutes/hourly recurrence, or sub-daily cadence.
- No cross-agent scheduling, agent-created schedules without explicit
  founder/operator instruction, hidden schedules, or agent-side re-arming.
- No general Todo backlog, priorities, tags, subtasks, shared lists, invites,
  availability, calendar grids, or calendar-suite integrations.
- No multiple time-of-day slots in one Todo, holiday/business-calendar rules,
  natural-language-only arming, or inferred "best time" behavior.
- No automatic conversion of ambiguous legacy one-shot chains into recurring
  commitments, no backfill/catch-up run burst, and no new spend dashboard.
- No implementation authorization, roadmap date, or external commitment.

## v2 cutline: normalized recurrence contract

Every recurring Todo stores: `unit`, positive integer `interval`, IANA
`timezone`, local `time` (minute precision), local `anchor_date`, exactly one
unit-specific selector, `end`, `review_expires_at`, `next_fire_at` (UTC), and
occurrence counters. The normalized rule—not source-language interpretation—is
the executable and reviewable source of truth. `fire_at` is a derived next UTC
instant, never the recurrence definition.

| Unit | Selector | Meaning |
| --- | --- | --- |
| `day` | none | Every `interval` local calendar days from the anchor date. |
| `week` | `weekdays`: one to seven Mon–Sun values | Every `interval` ISO weeks, anchored to the Monday of the anchor's local week; eligible selected days are at the stored local time. |
| `month` | exactly one of `month_day` (1–31) or `ordinal_weekday` | Every `interval` local calendar months from the anchor month. Ordinal weekday is first, second, third, fourth, fifth, or last plus Mon–Sun. |
| `year` | anchor month/day | Every `interval` local calendar years from the anchor year. |

`interval` must be a positive integer. The smallest supported cadence is every
one day; values that overflow the supported date range are rejected rather than
wrapped or approximated. A Todo has one local time and one timezone. Weekly
multi-day selection is a single Todo, so a fortnightly Tue/Thu Todo fires on
both selected days in each eligible ISO week. In its anchor week, only selected
occurrences at or after the creation/edit instant are eligible; prior days are
not retroactively fired.

### End conditions and review control

The required end selector is exactly one of:

- `never`: no recurrence-count or calendar-end condition;
- `on_date`: inclusive local calendar date in the Todo timezone; an eligible
  occurrence on that date may fire, a later local date may not; or
- `after_count`: a positive total number of successful root-task dispatches.

`after_count` increments only when the occurrence has committed exactly one
root-task creation and its task ID is stored. A missed, failed, timed-out, DST-
skipped, or cancelled occurrence does not consume the count. Reaching the
count ends the Todo before calculating another occurrence.

End selection does not override the safety review window. Existing v1 defaults
remain: recurring Todos receive a 90-day `review_expires_at`; expiry is checked
before a due occurrence, so an expiry-time tie expires rather than fires. Only
the founder/operator can explicitly set or renew an indefinite review state.
“Never” therefore means no *recurrence end*, not invisible perpetual authority.

## Deterministic time semantics

- Recurrence is calculated in the stored IANA timezone using civil local date
  and time; the stored next fire is the corresponding UTC instant. Display may
  localize, but never changes the stored rule.
- On a DST fall-back ambiguity, fire at the **earlier** matching instant. The
  displayed/audited occurrence includes its UTC offset so it is unambiguous.
- If the requested local wall time does not exist during a DST spring-forward
  gap, skip that occurrence, write `occurrence_skipped_dst_gap`, and calculate
  the next eligible occurrence. Do not shift it to a different wall time.
- Monthly `month_day=29..31` skips months that lack that date; it never clamps
  to month-end. An ordinal fifth weekday similarly skips months without one.
  A yearly Feb 29 rule skips non-leap years. Each skip is observable and does
  not consume an `after_count` value.
- Scheduler jitter of up to 15 minutes executes the due occurrence once. If the
  scheduler first observes it more than 15 minutes late, it records
  `occurrence_missed`, does not dispatch it, and advances only to the next
  future eligible occurrence. There is no catch-up or batch execution.
- Each occurrence has a stable occurrence key. Claiming and root-task creation
  are idempotent on that key: a retry may finish the same handoff but may not
  create a second root task. A claimed execution failure/timeout is recorded,
  does not count, and does not retry implicitly; the schedule advances under
  the missed-fire rule and remains founder-visible.

## Safety, edits, and limits

All v1 controls remain release-blocking: valid active session and resolvable
in-org team; self-target only; explicit founder/operator instruction; 20 armed
Todos per agent; 100 armed Todos per org; the existing 90-day one-shot horizon;
immutable `source_instruction` and `normalized_brief`; founder-visible controls;
and a complete audit trail.

Creation and edits validate the whole candidate rule atomically. Founder/operator
edits may change timing, timezone, recurrence selector, interval, end condition,
or review setting only while armed or paused; the original task intent and
provenance remain immutable. An edit records before/after normalized rule and
acting identity, invalidates an unclaimed next occurrence, and computes a next
occurrence strictly after the edit. A firing or terminal Todo cannot be edited;
the founder must act after the outcome or create a new Todo. Pausing/cancelling
prevents all unclaimed future occurrences.

## Migration and compatibility

- A v1 `weekly` Todo migrates losslessly to `unit=week`, `interval=1`, one-item
  `weekdays`, same local time/timezone, existing next fire where equivalent,
  current status, expiry, fire count, IDs, source instruction, normalized brief,
  and audit history. It gains `end=never`; its existing review expiry still
  applies.
- A v1 one-shot remains a one-shot and is not reinterpreted as recurrence.
  Its ID, instant, status, task provenance, and audit history remain intact.
- Existing manually or externally created “one-shot chains” have no reliable
  recurrence intent in the v1 Todo contract. Migration must preserve every node
  as its own one-shot and retain any available legacy linkage as display-only
  provenance. It must not infer cadence, merge nodes, create additional fires,
  or delete a chain. A founder/operator may explicitly replace a reviewed chain
  with one new v2 Todo, then cancel the old nodes.
- Migration is idempotent, reversible from a backup, dry-run/reportable, and
  stops on invalid/missing timezone or invariant violations rather than guessing.
  It must not increase active counts, bypass expiry, or fire any Todo.

## Data, provenance, and observability

The Todo list/detail and audit must render stored facts: rule version and
normalized recurrence; anchor/timezone; next UTC instant and display offset;
end and review conditions; status; created/edited/renewed/expiry timestamps;
fire count; skipped/missed/failed counters and last outcome; occurrence key;
spawned task IDs; immutable source instruction and normalized brief; actor and
reason for every founder control; and legacy migration provenance where present.

Required auditable events include validation rejection, creation, edit,
pause, cancel, renewal/indefinite grant, occurrence claimed, dispatched,
failed, timed out, DST skipped, monthly/yearly skipped, missed, count-ended,
date-ended, expired, and migration result. Operational monitoring must expose
due-to-claimed lag, misses/skips/failures by Todo and org, duplicate-dispatch
preventions, active-cap denials, and expiry volume. This is observability, not a
new founder analytics suite.

## Acceptance criteria

- A founder can inspect a v2 Todo and determine its exact next UTC fire and
  local rule without interpreting free text.
- Every valid day/week/month/year example above yields the deterministic eligible
  occurrences; invalid or zero intervals and mixed monthly selectors are refused.
- A fortnightly Tue/Thu rule fires on both days in eligible weeks and never in
  intervening weeks; month-day, ordinal weekday, end-date, and end-count rules
  meet the stated semantics.
- DST gaps, fall-back ambiguity, missing month dates, leap-day years, late
  scheduler observation, and dispatch failure satisfy the explicit behavior and
  leave an inspectable audit/result record.
- No execution occurs after cancel, pause, end, expiry, or a missed occurrence;
  no occurrence creates more than one root task; only successful handoffs count.
- v1 weekly and one-shot data migrate with preserved provenance and no automatic
  recurrence inference, new fire, or cap increase.
- The v1 safety envelope and strict no-list remain true after migration and on
  all create/edit paths.

## Remaining gate and risks

The sole genuine founder gate is whether to ratify this product contract and
then separately authorize build planning/implementation. No time, staffing, or
delivery commitment follows from this draft. The main risks are recurrence
complexity obscuring founder intent, cost from longer-lived schedules, and
timezone edge cases eroding trust; the normalized rule, review expiry, explicit
skips, at-most-once occurrence key, audit, and no-backfill policy are the
required mitigations.

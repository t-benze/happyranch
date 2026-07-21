# Agent-Owned Scheduled Work ("Todos") — Design Spike

**Date:** 2026-07-18
**Status:** DESIGN-ONLY. **Founder signed off on this design on 2026-07-21
at THR-105 seq19.** Implementation, schema migration, and permission-envelope
behavior still land only through the normal engineering merge gates, and the
boundaries below remain acceptance criteria for v1.
**Origin:** THR-105. Founder authorized the capability and the *fully-autonomous*
arming posture at THR-105 seq9 (reversing the earlier founder-scheduled / one-shot
v1 framing at seq4). Product and engineering framing settled seq5–seq8; founder
approval at seq19 ratified the label Todos, autonomy-with-mandatory-normalization,
the v1 no-list, and defaults of 20 armed Todos per agent, 100 org-wide, 90-day
one-shot horizon, and 90-day recurring review/expiry unless explicitly marked
indefinite.
**Author:** engineering_manager (design spike; founder is the reviewer/approver).
**Relates to:**
- `runtime/daemon/work_hours_scheduler.py`, `runtime/daemon/wake_queue.py`,
  `runtime/daemon/wake_runner.py`, `runtime/daemon/routes/work_hours.py` — the
  **working-hours wake → self-dispatch seam** this primitive reuses verbatim (§7).
- `protocol/05b-agent-runtime.md` — agent execution, memory & lifecycle; the
  scheduling/wake surface. Build must update this in the same PR (doc parity).
- `protocol/05c-orchestrator.md` — routing, **permissions** & task state. The
  permission envelope (§9) is a change to the **agent permission model** and is
  founder-gated; build must update this in the same PR.
- KB `goal-pattern-on-working-hours` — the neighbouring cadence-driven primitive
  this is deliberately **distinct** from (§4).

---

## 1. Goal

Give an agent a first-class way to **arm a bounded future commitment** — in
response to an explicit founder/operator instruction — that **self-dispatches one
normal task at fire time**, carrying the original instruction and provenance. A
one-shot absolute-time reminder ("follow up with the customer in 48 hours") or a
simple weekly recurrence ("every Saturday, post the market update") should be
expressible, armed by the agent with no second founder confirmation, yet **fully
visible, capped, auditable, and cancellable** by the founder.

The primitive is intentionally **narrow**: it schedules *when a specific task
fires*, and nothing more. It is not a calendar, not a cron server, and not an
agent-to-agent dispatch channel.

## 2. Motivation & anchor use cases

Today an agent can only act when *something else* wakes it: a founder message, a
thread turn, a working-hours cadence slot, or an auto-revisit. There is no way for
an agent to say "do this specific thing at this specific future time, once" — the
closest tool, working-hours, is **cadence-driven and goal-state-blind**: it fires
on a clock grid and re-reads a *static* routine checklist (KB
`goal-pattern-on-working-hours`; `work_hours_scheduler.py` docstring). That is the
wrong shape for a *one-shot future commitment* or a *specific dated recurrence*.

Three anchor use cases drive the design:

| # | Agent | Instruction | Schedule type |
|---|-------|-------------|---------------|
| A | `investment_advisor` | "Every Saturday, send me the weekly market update." | **weekly recurrence** (Sat, one time-of-day) |
| B | `support_agent` | "Follow up with this customer 48 hours after the issue was filed." | **one-shot absolute-time** |
| C | any agent | "Revisit this decision next Monday once the data lands." | **one-shot absolute-time** (self-scheduled future decision-revisit) |

All three share the same skeleton: *an explicit instruction → a normalized future
firing time → at fire, self-dispatch a normal task carrying the instruction.*

## 3. The primitive

**Definition.** A **Schedule** is a persisted, agent-owned record that binds:
a normalized instruction/brief, a firing rule (one-shot time *or* weekly
recurrence), a target (**always the creating agent itself**), a status, and the
permission-envelope bookkeeping (creator, caps context, review/expiry window). At
fire time the runtime self-dispatches **one normal root task** on the agent's own
team, targeted to that agent as executor, with the original instruction attached
and `spawned_task_ids` provenance recorded on the Schedule row.

**Invariants (v1):**

1. **Explicit-instruction-only.** A Schedule may be armed **only** in response to
   an explicit founder/operator instruction. **No proactive or inferred
   scheduling** — an agent may not decide on its own that something "should
   probably" recur. (Enforced by construction: the only arming surface is a skill
   the agent invokes while handling an instruction; there is no autonomous
   background "propose a schedule" path.)
2. **Self-target only.** `target == creator`. There is **no** agent-to-agent
   scheduling — an agent cannot arm a Schedule that fires work on another agent.
   This mirrors the working-hours spawn callback, which is structurally self-team-
   only (`routes/work_hours.py`: "no cross-team path from a wake").
3. **Bounded.** Every Schedule has a firing rule that either fires once (one-shot)
   or recurs on a **capped horizon** with a **review/expiry window** (§9). No
   unbounded recurrence.
4. **Normalized before arming.** The natural-language instruction MUST be
   normalized into a **structured, founder-reviewable** schedule (fire time /
   recurrence rule / brief) *before or at the moment of arming* (§8). Autonomy
   means "no pre-arming approval step," **not** "opaque scheduling."

## 4. Architecture: a new primitive, distinct from two neighbours

This is a **third** scheduling-adjacent primitive, deliberately distinct from the
two that already exist:

| | **Working hours** | **The task tree** | **Schedules (this spec)** |
|---|---|---|---|
| Trigger | clock **cadence** grid (slots) | a parent decision / dispatch | a **per-record `fire_at`** (or next recurrence) |
| Gates | *whether* an agent may work now | *what* work exists | *when a specific task fires* |
| Memory of intent | none (static checklist) | the parent chain | the Schedule's own normalized brief |
| Fan-out | one wake → N routine tasks | N children | one fire → **one** task |
| Owner store | `work_hours` table | `tasks` table | **new `schedules` table** |

The Schedule primitive **does not alter or overload** the `work_hours` or `tasks`
tables, the `audit_log.task_id` scope-prefix convention, or any permission-
generation surface. It gets its **own** table, its **own** id space
(`SCHEDULE-NNN`), and its **own** audit actions (§6, §11). Where it *reuses*
existing machinery, it reuses it **without modification** (§7).

> **Boundary / STOP-and-escalate.** If, during build, the design appears to
> require altering an existing schema column, overloading the `audit_log.task_id`
> scope-prefix semantics, or touching a permission-generation surface (Claude
> `--allowedTools`, Codex sandbox flags, opencode `permission.bash`, the baseline
> `happyranch` allow-rule), the implementer **must STOP and escalate** rather than
> proceed. Those are founder-contract surfaces outside EM authority.

## 5. Schedule types (v1)

Exactly two firing rules, kept deliberately small:

1. **One-shot, absolute time.** Fires once at a stored UTC instant
   (`fire_at`), then transitions to `fired` (terminal). Use cases B and C.
2. **Simple weekly recurrence.** A set of weekdays + one local time-of-day +
   a timezone; each fire computes the *next* occurrence and re-arms
   `fire_at`. Use case A.

**Explicitly NOT in v1:** cron syntax, arbitrary intervals ("every 3 days"),
monthly/"last business day"/nth-weekday rules, multiple times-of-day per rule,
end-date-by-count ("10 times then stop"). Recurrence beyond a plain weekly rule is
follow-up work, not v1.

The recurrence math is **partly reusable** from the working-hours slot-grid
engine: `work_hours_scheduler.next_wake_slots` / `windowed_slot_minutes` already
walk a weekday-filtered, timezone-aware grid day-by-day
(`work_hours_scheduler.py:137`). "Next weekly occurrence strictly after now" is a
one-slot specialization of that walk. v1 should extract/share that helper rather
than write a second timezone walker.

## 6. Data model — a new `schedules` table

A dedicated table, shaped after `work_hours` (`database.py:549`) so it inherits the
same audit/token/provenance conventions without touching the existing table.
**Illustrative** (final DDL is an implementation detail, gated on sign-off):

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | `SCHEDULE-NNN`, `MAX(...)+1` allocator like `work_hours.next_id()` |
| `agent_name` | TEXT | creator **and** target (self-target invariant) |
| `team` | TEXT | resolved from the agent; the spawned task's team |
| `kind` | TEXT | `one_shot` \| `weekly` |
| `fire_at` | TEXT | next UTC firing instant (recomputed per recurrence) |
| `recurrence` | TEXT (JSON) | null for one-shot; `{days:[...], time:"HH:MM", tz:"..."}` for weekly. **New column in a new table — not an overload.** |
| `timezone` | TEXT | display tz for the founder-visible list |
| `normalized_brief` | TEXT | the structured brief that fires as the task |
| `source_instruction` | TEXT | verbatim NL instruction, for audit/founder review |
| `status` | TEXT | see enum below |
| `active` | INTEGER | derived convenience; caps count only `armed` rows |
| `expires_at` | TEXT | review/expiry window end; null **only** if founder marked indefinite |
| `indefinite` | INTEGER | 1 only when founder explicitly opted out of expiry (§9) |
| `spawned_task_ids` | TEXT (JSON list) | provenance: every task this Schedule has fired |
| `last_fired_at` / `fire_count` | TEXT / INT | recurrence bookkeeping |
| `created_at` | TEXT | |

**Status enum:** `armed` → `fired` (one-shot terminal) / `paused` / `cancelled` /
`expired`. Weekly rows cycle `armed → (fire) → armed …` until `paused`,
`cancelled`, or `expired`.

**No existing column is altered or re-meant.** `tasks`, `work_hours`, and
`audit_log` schemas are untouched; this is a purely **additive** table +
additive audit actions. (Additive tables are within the migration guardrails; a
column drop/alter or overloaded-column re-meaning would be founder-gated — none is
proposed here.)

## 7. Lifecycle & the reused wake → self-dispatch seam

### 7a. Arm (create)
The agent, while handling an explicit instruction, invokes a **new skill** that:
normalizes the NL instruction (§8), then calls a **single-line
`happyranch schedules create --from-file <path>`** callback. The daemon validates
against the permission envelope (§9) and inserts an `armed` row.
**Fully-autonomous:** no second founder confirmation — arming is immediate once
the envelope checks pass. Audit: `schedule_created`.

### 7b. Fire — reuse the working-hours seam **verbatim**
The firing path is the working-hours wake path with `fire_at` swapped in for the
cadence slot grid. Concretely, it reuses each of these components **unchanged in
contract**:

1. **A scheduler loop** mirroring `work_hours_scheduler_loop`
   (`work_hours_scheduler.py:332`): a ~60s tick that, per org, selects `armed`
   rows with `fire_at <= now`, and for each enqueues a job onto a queue. The
   *only* new decision logic is "is this row due?" (a `fire_at` comparison) and,
   for weekly rows, "compute next `fire_at`" (the §5 shared helper). It never
   backfills — like `current_due_slot`, only the current due instant fires.
2. **The wake queue** (`wake_queue.py`): the same unbounded-`asyncio.Queue`
   pattern (`WakeJob` → a `ScheduleJob(org_slug, schedule_id)`), the same
   `put` / `put_nowait` no-loop escape hatch (LRN-005), the same
   `wake_worker_loop` drain-into-runner shape.
3. **A runner** mirroring `run_wake` (`wake_runner.py:113`): transition
   `armed → firing`, compose a trigger prompt (the analogue of
   `build_wake_prompt`), run **one** executor session in the agent's workspace
   whose only job is to self-dispatch, record token usage under a **new scope**
   `scope_type="schedule"` / `scope_id=<SCHEDULE-NNN>` (mirroring
   `scope_type="work_hour"`, `wake_runner.py:238`), and resolve the terminal
   status on no-callback / timeout / failure.
4. **A single-use, record-scoped spawn callback** mirroring `spawn_work_hour`
   (`routes/work_hours.py:165`): accepts **only** a `firing` `SCHEDULE-NNN`
   (the single-use / scoped guard — "can't be reused as a generic root-task
   backdoor"), creates the root task with **`assigned_agent` pre-set to the
   creating agent** (Q2: run_step honors the pre-set owner — MEM-028), targeted
   to the agent's **own team** (self-team-only, structural — no cross-team path),
   records `spawned_task_ids` on the Schedule row, and marks the row `fired`
   (one-shot) or re-arms it with the next `fire_at` (weekly). `enqueue_task` +
   audit happen outside the db lock, exactly as `spawn_work_hour` does.

> **Why "verbatim reuse" and not a bespoke route:** MEM-099 (founder ruling) —
> external-wait / triggered-action features are poll-loop + resume + task-triggered
> action; do **not** build a bespoke daemon action-route. The wake seam already
> *is* that pattern for cadence; Schedules apply it to `fire_at`.

The one intentional shape difference from working-hours: a wake spawns **N** tasks
(one per routine); a Schedule fire spawns **exactly one** task (the normalized
brief). The spawn payload is single-routine; the callback contract is otherwise
identical.

### 7c. Recur / expire
On a weekly fire, the runner (or callback) computes the next occurrence via the §5
helper and re-arms `fire_at`; if the next occurrence is past `expires_at`, it
transitions to `expired` (audit: `schedule_expired`) instead of re-arming. One-shot
rows go straight to `fired`.

### 7d. Pause / cancel / edit (founder + agent)
`paused` suspends firing without deletion; `cancelled` is terminal;
`edit` re-normalizes and re-validates against the envelope. Founder can do all
three from the founder-visible list (§10). Audit: `schedule_paused`,
`schedule_cancelled`, `schedule_edited`.

## 8. Normalization: NL → structured, reviewable schedule (seq8 reconciliation)

Founder seq9 = **fully autonomous** arming. Product_lead seq8 = the schedule must
still be **normalized and reviewable**. These reconcile cleanly:

> **Autonomy = no pre-arming approval step. It does NOT mean no normalization.**

Before arming, the agent MUST translate the NL instruction into the structured
form of §6 (kind, `fire_at`/`recurrence`, timezone, `normalized_brief`) and store
both the structured schedule **and** the verbatim `source_instruction`. The
founder-visible list (§10) shows the *structured* schedule ("Weekly · Sat 09:00
Asia/Shanghai · next fire 2026-07-19 · 'send weekly market update'"), never an
opaque "agent scheduled something." A normalization the agent cannot express in
the §5 rule set (e.g. "every third Tuesday") is **rejected at arming**, not
silently approximated — the agent surfaces the gap to the founder instead.

This is the load-bearing line in the whole design: **fully-autonomous arming with
mandatory normalization**, so nothing is hidden even though nothing is pre-approved.

## 9. Permission envelope = acceptance criteria (v1-blocking)

This section is **first-class acceptance criteria, not follow-up hardening.**
Because it governs what an agent may autonomously commit the org to, it is a change
to the **agent permission model** and is therefore **founder-gated**. Every item
below must exist in v1:

1. **Per-agent capability flag.** Scheduling is **off by default**; an agent may
   arm Schedules only if explicitly enabled for it (org-config, resolved like
   working-hours' per-agent enablement in `org_config.py`). No flag → arming is
   refused.
2. **Active-schedule cap.** A max number of concurrently `armed` Schedules per
   agent (and/or per org). Arming past the cap is refused with an actionable
   error (MEM-246: the error names the remediation — pause/cancel an existing
   one).
3. **Minimum interval / cadence floor.** A weekly rule may not fire more often
   than a floor (v1: weekly is inherently ≥7-day; the floor guards against a
   future finer recurrence and against one-shot `fire_at` in the immediate past /
   sub-minute future).
4. **Max horizon.** `fire_at` (and any recurrence occurrence) may not exceed a max
   look-ahead. Arming beyond the horizon is refused.
5. **Founder-visible schedule list with pause / cancel / edit.** §10. A schedule
   the founder cannot see and stop does not ship. (V1 no-list: *no silent/hidden
   schedules.*)
6. **Full audit of create / fire / cancel / expire.** §11. Every state change
   emits an audit row.
7. **Review / expiry window unless explicitly indefinite.** Every Schedule gets a
   default `expires_at` (a review window). It becomes indefinite **only** when the
   founder explicitly marks it so (`indefinite=1`) — an agent cannot arm an
   unbounded-forever recurrence on its own.

**Acceptance:** v1 is not "done" until all seven hold. A build that ships firing
without (1), (5), (6), or (7) is a permission-model regression and must not merge.

## 10. Founder-visible surfaces

- **CLI (read + manage):** `happyranch schedules list [--agent X]`,
  `happyranch schedules show SCHEDULE-NNN`, and founder management
  `pause` / `cancel` / `edit` — mirroring the `work-hours` read routes
  (`routes/work_hours.py:97`) plus management verbs. The agent-facing
  `schedules create` callback is single-line `--from-file` and **not**
  browser-callable (like the spawn callback).
- **Web (read + manage):** a founder-facing list under the existing dashboard,
  mirroring the work-hours read routes' TypeScript mirrors (`web/src/lib/api/`),
  showing per-row: agent, kind, next `fire_at` (in display tz), recurrence,
  `source_instruction`, status, `expires_at`, and `spawned_task_ids` provenance;
  with pause/cancel/edit controls. Adding a daemon route drifts the OpenAPI
  snapshot + `web` openapi-coverage test — the build must regenerate both in the
  same PR (MEM-094 / MEM-148).

Design-only note: the web surface is a straightforward list; **no complex calendar
UI** is in scope (v1 no-list).

## 11. Audit actions (scope-prefix convention preserved)

New audit actions, mirroring the `work_hour_*` family in `audit_logger.py:1241`:
`schedule_created`, `schedule_fired`, `schedule_spawned` (carries the
`spawned_task_ids`), `schedule_completed` / `schedule_failed` / `schedule_timeout`
(fire-session terminal states), `schedule_paused`, `schedule_cancelled`,
`schedule_edited`, `schedule_expired`.

**Convention preserved, not overloaded:** these rows set `task_id=<SCHEDULE-NNN>`
— the documented scope-prefix / `audit_log.task_id`-as-scope-id use, exactly as
the work-hour audit rows set `task_id=<WORKHOUR-NNN>` (`audit_logger.py:1248`,
MEM-075). This is the *sanctioned* use of that column, **not** a new overload; no
scope-prefix semantics change.

## 12. Naming — internal primitive vs. user-facing label

Founder (seq9) asked whether to call this **"Todos"** — she frames it as a to-do
app for agents. Recommendation:

- **User-facing label: adopt the founder's mental model — "Todos" (or
  "Reminders").** These read naturally to a founder ("my agent's to-do list") and
  match how she described the feature. Between the two, **"Reminders"** is a hair
  more precise (a Todo can imply an open checklist item with no time; every item
  here is time-triggered), but **"Todos" is the founder's own word** and carries
  her intent — either works.
- **Internal primitive: name it for its defining property — a *scheduled
  trigger* (`Schedule` / `schedules` table / `SCHEDULE-NNN`).** Do **not** name
  the internal primitive "todo" or "task."

**Tradeoff / why the split:** the runtime already has a load-bearing primitive
called **`tasks`** (the task tree). Naming the internal store "todos" invites
constant confusion with `tasks` in code, audit rows, and protocol docs — two
task-shaped nouns one synonym apart. Keeping the *internal* name anchored on the
scheduled-trigger property (`Schedule`) keeps the code vocabulary unambiguous,
while the *user-facing* label can freely be "Todos" to match the founder. UI/CLI
copy says "Todos"; tables, ids, routes, and audit actions say "schedule."

**This naming choice does not block the design** — the final user-facing label is
product_lead's / founder's call. The engineering ask is only: keep the internal
noun distinct from `tasks`.

## 13. Founder sign-off (resolved) and build gate

Founder sign-off landed on 2026-07-21 at THR-105 seq19. The approved decisions:

1. **Permission envelope (§9) is the v1 acceptance bar** — this is the
   agent-permission-model change and the load-bearing gate. Concrete defaults:
   **20 armed Todos per agent**, **100 armed Todos org-wide**, **90-day one-shot
   horizon**, and **90-day recurring review/expiry** unless explicitly marked
   indefinite.
2. **Fully-autonomous arming *with* mandatory normalization (§8)** is approved:
   autonomy = no pre-arming approval, not opaque scheduling.
3. **User-facing label is Todos**; internal primitive remains `Schedule`.
4. **V1 scope boundaries (§5 types, §3/§14 no-list) are approved:** one-shot +
   weekly only; self-target only; no silent schedules; no unbounded recurrence;
   no complex calendar UI; no NL scheduling without a normalized reviewable
   schedule.

The build lands as a phased engineering effort (new `schedules` table
+ scheduler loop/queue/runner/callback reusing the §7 seam + capability flag &
caps + CLI/web list + audit + protocol 05b/05c doc-parity in the same PR), routed
through the normal dev → code_reviewer → qa merge gate. **No part of it is
complete without verification output**, and any implementation step that appears
to require touching an existing schema column, the `audit_log` scope convention,
auth/notification routing, or a permission-generation surface must STOP and
escalate (§4 boundary).

## 14. Non-goals (v1 no-list, consolidated)

- No **agent-to-agent** scheduling (self-target only).
- No **silent / hidden** schedules (every row is founder-visible and normalized).
- No **unbounded** recurrence (horizon + expiry/review window unless founder marks
  indefinite).
- No **complex calendar UI** (a plain founder-visible list).
- No **NL scheduling without a normalized reviewable schedule** before arming.
- No **cron / arbitrary-interval / monthly / nth-weekday** recurrence (weekly
  only in v1).
- No **new or altered permission-generation surface**, no **schema-column
  overload**, no **`audit_log.task_id` scope-semantics change** (additive table +
  additive audit actions only).

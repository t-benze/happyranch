# Agent Todos — current product contract

| Field | Value |
| --- | --- |
| Status | founder-ruled — consolidated record; implementation exists, full current acceptance unverified |
| Owner | product_lead |
| Date | 2026-11-17 (reviewed) |
| Source Links | THR-375 seq4 (consolidation); THR-105 seq9, 19–20, 217, 271–277, 308–310, 334–340; THR-179 seq4–11; historical PRDs and technical designs in the evidence references below |
| Commitment Boundary | build-ready — records previously approved scope only; this consolidation authorizes no new implementation, migration, live schedule, deployment, roadmap or external date |
| Founder Decisions | Required: none for this documentation consolidation. Ruled: autonomous normalized self-scheduling; universal agent access; bounded recurrence v2 and its 12-item gate; server-derived recurring edits; explicit start/phase correction. Implementation differences and acceptance limits remain open below. |

This is the single current product document for Agent Todos, including one-shot,
legacy weekly, native recurrence v2, and the v2.1 start-date correction. It
supersedes the July v1 and August recurrence PRDs as current product references.
**Founder approval, merged source, historical live evidence, and current
acceptance are separate facts.** The gaps below prevent a blanket `shipped` or
fully accepted claim.

## Problem, users, and success

A founder should tell an agent once, “follow up in 48 hours” or “send a report
every other Tuesday,” and be able to inspect and stop that future commitment.
Manually re-prompting or chaining one-shots makes follow-through fragile.

The primary user is the founder/operator supervising an org; the executor is a
valid in-org agent with an active session and a resolvable team. A **Todo** is a
scheduled agent commitment, stored internally as a **Schedule**. It is distinct
from Work Hours (routine availability/wakes) and Dreams (private reflection).

Success means an explicit instruction produces the intended normalized Todo;
the founder can inspect its timing, source, controls and resulting tasks; each
eligible occurrence creates at most one ordinary self-owned root task; and
exceptions remain visible. Dispatch success is not completion of the report,
customer follow-up, or other underlying work. No usage-volume target substitutes
for these outcomes.

## Instruction-to-work workflow

1. The founder/operator explicitly instructs the agent to schedule work. The
   agent must not infer recurring obligations. This instruction rule is agent
   policy; the server validates identity and nonblank instruction/brief fields,
   not the semantic authenticity of the quoted instruction.
2. Before arming, normalize the owner, self-contained task brief, original
   instruction, kind, local date/time, IANA timezone, recurrence/end condition,
   and intended phase; the daemon materializes/validates the first UTC fire.
   Clarify ambiguous timing or intent; never
   approximate an unsupported rule. The source text and normalized brief remain
   immutable after creation (the service trims surrounding whitespace).
3. Submit `happyranch schedules create --org <org> --from-file <payload.json>`
   with the actual permanent agent ID, task ID and runtime session ID. The
   callback validates the active binding and team and rejects recovery-purpose
   sessions. It always targets the caller; no other agent/team/brief can be
   chosen at fire. No second pre-arming confirmation is required.
4. The service validates and stores the normalized schedule as `armed`, returns
   its ID and first fire, and writes creation audit. Report that actual result
   and review window to the founder; transport uncertainty is not proof of
   arming or permission to duplicate it.
5. At due time, a schedule trigger session calls the schedule spawn callback.
   It creates one normal root task from the stored brief on the owner's current
   team. The trigger does not perform the work itself. The Todo records the
   task ID and dispatch count; the ordinary task lifecycle then owns delivery.

Any valid in-org agent may use creation. Founder THR-105 seq217 removed the
`scheduling.enabled_agents` allowlist; legacy configuration is a no-op. The
Todos skill supplies instructions, not additional permissions.

## Supported schedules and ends

| Shape | Current expression and meaning |
| --- | --- |
| One-shot | One future timezone-aware absolute `fire_at`, within 90 days; no recurrence. |
| Legacy weekly | One weekday, `HH:MM`, IANA `tz`; existing rows retain their stored kind and evaluation path. |
| Native daily | `freq=DAILY`, positive integer `interval`, one `HH:MM` and IANA `tz`. |
| Native weekly | `freq=WEEKLY`, positive interval, one to seven distinct `byday` values (`MO`…`SU`); interval weeks start Monday. |
| Native monthly | Positive interval; exactly one `bymonthday` integer 1–31, or one `byday` plus `ordinal=first/second/third/fourth/fifth/last`. |
| Native yearly | Positive interval; preserves the anchor month/day. No separate yearly selector grammar. |

Native recurrence has one local time and is no more frequent than daily.
`Never` is represented by absent/null `until` and `count`; `On date` by an
inclusive local `YYYY-MM-DD` `until`; `After N` by a positive integer `count`.
`until` and `count` are mutually exclusive. After-N counts successful root-task
creation, not calendar candidates or completed business work. Misses, skipped
calendar dates, DST gaps, failed triggers and timeouts do not consume it.
Counters survive edits and explicit rephasing; After-N is a lifetime total for
the Todo, not “N more” after each edit.

Examples of intent, **not instructions to arm live Todos**:

- “Follow up in 48 hours”: one-shot at the resolved absolute future instant.
- “Every other Tuesday and Thursday at 09:00 Shanghai time, starting 2026-11-24,
  stop after six dispatches”: native `WEEKLY`, interval 2, `byday=[TU,TH]`,
  `time=09:00`, `tz=Asia/Shanghai`, `start_date=2026-11-24`, `count=6`.
- “First Monday of each month”: native `MONTHLY`, interval 1, `byday=[MO]`,
  `ordinal=first`, plus an explicit time/timezone and end selection.

### Normalization, phase, edits, and civil time

The daemon stores a stable local `anchor_date` and derived UTC `fire_at`.
`start_date` is an optional transient create/edit input, not a second stored
anchor. It must select a real future occurrence of the rule, including its
weekday/month selector and DST validity. The server writes the managed anchor;
callers cannot set `anchor_date` directly. Without a requested phase, creation
derives the anchor from the first calculated occurrence.

Native recurring creation can omit `fire_at` when supplying `start_date`;
otherwise it must supply the exact server-expected next instant. Native edits
submit recurrence/timezone/optional phase intent and omit `fire_at`; the server
merges, validates and derives it. A supplied `fire_at` is an exact-match
assertion. Timing-only edits preserve the cadence anchor; rule-shape edits
re-anchor to the newly calculated next local date. Explicit `start_date`
rephases prospectively. Inactive monthly/weekly selectors may be explicitly
cleared in an edit and are removed before persistence. Invalid edits leave the
record unchanged. Legacy weekly edits still require a matching `fire_at` and
the mounted editor computes it; do not describe that compatibility path as
server-derived native recurrence.

Native rules calculate in civil local time, then persist UTC. Fall-back uses
the earlier matching instant (`fold=0`). Spring-forward gaps skip that native
occurrence, never shift its wall time. Months without a selected day or fifth
weekday, and non-leap years for a Feb 29 anchor, are skipped. An explicitly
requested start in a gap is rejected. `until` compares local calendar dates,
including the named date regardless of offset. Legacy weekly uses its older
timezone path and does not implement native gap detection; uniform DST
semantics across both kinds are not confirmed.

Native rule validation exposes actionable codes such as `invalid_interval`,
`invalid_byday`, `monthly_selector_conflict`, `end_condition_conflict` and
`invalid_start_date` (HTTP 422); `anchor_date_not_settable` rejects direct
anchor input. Creation envelope errors use `create_failed` (409) with a
message; unavailable/disallowed management states use `state_conflict` (409).
The UI/agent should explain the returned cause and request correction rather
than silently changing the requested commitment.

## Lifecycle, lateness, recovery, and overlap

| State / result | Observable meaning |
| --- | --- |
| `armed` → `firing` | Due row claimed before enqueue; repeated ordinary ticks cannot claim the same firing row again. |
| One-shot success → `fired` | Naturally complete; stored `end_reason=one_shot_completed`. |
| Native count/date end → `fired` | Stored `end_reason=count_exhausted` or `date_ended`; date exhaustion is distinct from review expiry. |
| `paused` | No due firing; retains history. Edit/renew keep it paused. No resume/re-arm command is implemented. |
| `cancelled` | Permanently stopped; retains history. |
| `expired` | Review authority exhausted, including when a calculated next fire exceeds its review window. Not native calendar-date completion. |
| Trigger failure/timeout | One-shot becomes terminal `failed`/`timeout`. Weekly/native normally advance without retrying the failed occurrence; if no future candidate remains, date-end/review-expiry/defensive failure applies. |

Weekly/native due instants older than 120 seconds are skipped and advanced to
the next future occurrence, without catch-up bursts. The 120-second value is a
current runtime tolerance, not a delivery SLA. One-shots have no equivalent
stale-skip rule: an overdue armed one-shot can fire after downtime. The scan
runs roughly every 60 seconds; enqueue and execution may add delay.

The claim/status and spawn guards support at-most-once ordinary dispatch for a
claimed occurrence. They do not establish crash-safe exactly-once delivery:
task insertion, schedule update and audit are separate persistence operations.
On restart, recoverable leftover `firing` rows are marked terminal
`failed/error=daemon_restart`, including recurring ones; native ownership
preservation can defer recovery. Do not silently re-arm or replay an uncertain
occurrence. Inspect schedule, spawned tasks and audit before an explicitly
authorized replacement.

A recurring Todo re-arms after root-task creation, without waiting for that
root task to finish. There is no Todo-level prior-task-completion/overlap guard;
a later occurrence may create another root while earlier work is unfinished.
Ordinary runtime admission still applies. No business-work exclusivity or
on-time execution guarantee follows from the schedule.

## Review authority, limits, and founder controls

The approved envelope retains **20 armed Todos per agent**, **100 armed per
org**, a **90-day one-shot horizon**, and **90-day repeating review/expiry**
unless the founder/operator explicitly grants indefinite authority. Current cap
queries count `armed` rows, not paused/firing/history; creation checks them.
They are not a token budget or a bound on already spawned work.

Recurring `Never` removes the recurrence end, not the review checkpoint.
`expires_at` is the stored review field; the older PRD's `review_expires_at`,
`next_fire_at`, `after_count` and `on_date` are conceptual terms, not separate
wire fields. CLI/API renewal on `armed`/`paused` resets expiry to now + 90 days,
or sets the indefinite flag. It leaves cadence, next fire, counters and status
unchanged. Indefinite is not accepted in agent creation. Already `expired`,
`firing` and other terminal rows reject renewal by state. Clock-boundary and
claim-race limitations are recorded below.

The mounted founder routes are `/orgs/:slug/todos` and
`/orgs/:slug/todos/:scheduleId`. They show stored owner, status, normalized and
original instruction, schedule/timezone, fire time, activity and task links.
List filters cover agent and lifecycle groups. Detail links to
`/orgs/:slug/audit?task_id=<SCHEDULE-ID>`.

| Control | Verified source surface and restriction |
| --- | --- |
| List/detail | Web and `happyranch todos list/show`; API list defaults to 50, accepts up to 500, and has no paging. The web asks for 200, so this is not an exhaustive history view. |
| Pause | Web/CLI/API; armed only, idempotent if already paused. |
| Cancel | Web/CLI/API; armed or paused only. Does not cancel an already spawned task. |
| Edit | Web/CLI/API; timing/recurrence/timezone/phase only, armed or paused; source instruction, brief and kind are immutable. Claim conflicts reject the edit. |
| Renew / indefinite | CLI/API exists; web API helper exists but no mounted renewal action was found. |

No create form, general backlog, resume action, or terminal resurrection is
part of these controls. A changed work instruction or expired/terminal
commitment needs a newly instructed replacement, preserving the old record.

## Provenance and observable acceptance

Persisted facts include Schedule ID, permanent owner ID/team, kind, rule/anchor,
UTC fire, timezone, immutable instruction/brief, status/active flag,
`expires_at`/indefinite, successful `fire_count`, last fire, task IDs, error,
terminal reason and timestamps. Audit scope is `task_id=SCHEDULE-NNN`, not a
synthetic ordinary task. Create/edit/pause/cancel/renew/claim/fire/spawn/completion
and failure/timeout/expiry events describe that scope; spawned ordinary tasks
have their own task lifecycle. Follow stored task IDs to assess work completion.
No dedicated cost-by-Todo UI, per-outcome counter suite, or exhaustive recurrence
monitoring is evidenced.

The following is the acceptance checklist for the approved contract, **not a
report that these scenarios passed on 2026-11-17**:

1. A real authorized instruction yields one self-owned normalized Todo; invalid
   sessions, cross-agent targeting, unsupported shapes and cap overflow refuse
   visibly. The founder can inspect the result and stop it before claim.
2. Day/week/month/year, multi-day fortnightly, monthly ordinal, missing dates,
   leap days, fall-back, gap, explicit phase and invalid-phase cases produce the
   documented prospective instants. Native edits derive UTC on the daemon;
   timing-only edits preserve phase and shape/explicit rephase changes audit it.
3. One occurrence creates at most one linked root task in ordinary operation;
   After-N advances only on successful dispatch. Miss/failure/timeout does not
   consume count or retry the same occurrence. Root completion is assessed
   separately; crash uncertainty is retained.
4. Natural completion persists the distinct reason; review expiry stays
   distinct. Renewals preserve history/cadence, cannot revive expired records,
   and cannot bypass the approved expiry/claim boundary.
5. Mounted list/detail and management controls render authoritative state,
   limits/errors, review authority, last outcome and terminal reason. A stale
   conflict is visible; there is no hidden schedule or invented success.

## Evidence limits and unresolved implementation differences

Source reviewed: remote `main` at
`e8358a35174e7d29f74468527566bbf631099176`. The primary checkout was stale;
this document was authored in an isolated task worktree at that fetched base.
GitHub independently confirms recurrence PRs **#648, #649, #653, #654, #656**
and fixes **#660, #670** merged. The inclusive range “#648–#656” is misleading:
#650–#652 and #655 concern other features. #677 remains open and is not merged
acceptance evidence.

Authority is explicit: THR-105 seq274 approves the reconciled PRD/design and
12-item gate requested at seq271–272; seq310 approves server-derived recurring
PATCH; seq339 authorizes the phase-date correction defined at seq340.
THR-179 records the later native-edit cleanup check while retaining weekly
compatibility. The July weekly-only envelope is historical for native v2.

Historical live evidence is bounded: THR-105 seq319/325 records August API/UI
verification and disposable create → API edit → renew → cancel, with zero
dispatches. It also found a CLI PATCH transport defect later merged in #660;
seq335/338 records subsequent route/deployment observations, not a complete
successful CLI edit acceptance. Seq358 reports #670 merged with deployment
still outstanding. These are attributed historical reports, not fresh reruns.
On 2026-11-17, read-only health, live OpenAPI (including `start_date` and recurring
PATCH), and the current org's schedule list returned HTTP 200. This proves
surface reachability only, without a verified serving Git revision, browser
interaction, mutation, due fire, or current end-to-end acceptance. No live Todo
was created or changed for this consolidation.

| Difference from the approved narrative | Verified source finding / consequence |
| --- | --- |
| Terminal reason visibility | `end_reason` is stored and used by completion paths, but `_schedule_to_dict` omits it; mounted detail shows only a one-shot completion explanation. Do not claim native count/date reasons are visible in API/CLI/web. |
| Renewal and attention in web | No mounted Renew action; native detail omits the weekly-only review-date block. Web Needs attention groups terminal failed/timeout only; an armed recurring row with `error` is not included. CLI show does flag armed errors. |
| Review/horizon enforcement | One-shot horizon and default repeating expiry exist. Native create does not enforce the older PRD's initial-fire-within-90-days claim. Due claim/spawn does not check current expiry, and next-fire comparisons use `>` rather than the approved expiry-time tie rule. A late/long-horizon first fire is not proven blocked. |
| Renewal before expiry / races | Renewal checks lifecycle, not wall-clock expiry, and uses an unconditional store update. Do not promise expiry-clock or concurrent-claim protection. The ratified pre-expiry/armed-or-paused requirement remains the acceptance bar. |
| Pause/recovery | No resume endpoint; editing paused does not arm it. Restart recovery can terminate recurring firing rows despite ordinary failure-continuity behavior. Unexplained missing recurrence candidates terminate with `failed/recurrence_no_candidate`. No automatic safe recovery is claimed. |
| Skip observability / legacy DST | Native calendar math skips gaps/missing dates, but dedicated skip audit events and counters required by the older PRD were not found. Legacy weekly lacks native gap detection. |
| Atomicity / coverage | Claims and edit CAS exist; task/store/audit writes are separate. Cap counting is not a transactional org-wide reservation, and edits do not recheck caps. Crash-safe exactly-once and full audit completeness are unverified. |
| Phase preview | Native editor accepts start date and submits intent, but returns no computed next-fire preview before save (`nextFirePreview` is null for native recurrence), contrary to seq340's stated expectation. The stored result is available after save. |

These are reconciliation defects or unverified acceptance, not new product
choices and not authorization to widen or weaken the approved contract. No
unresolved Founder policy conflict was established. Engineering review should
first validate these exact gaps before any separately authorized remedy; a
proposal to change an approved control would require a named Founder ruling.

## Exclusions and historical record

No inferred/proactive or cross-agent schedules; sub-daily/cron; multiple daily
time slots; monthly selector lists/negative wire values/last-calendar-day
shortcut; yearly custom selector; holidays/business calendars; hidden
commitments; general unscheduled backlog, priorities, tags, subtasks, shared
lists or calendar integrations. No automatic weekly rewrite, inferred chain
conversion, catch-up burst, silent retry, or live cutover. Existing one-shot
chains retain their nodes/provenance; only a separately instructed operator
cutover can retire a reviewed chain and create a native replacement.

The v1 PRD's later desktop colour-acceptance addendum is retained in its Git
history (THR-221 seq235/236, PR #836); it is bounded visual evidence, not
acceptance of recurrence, mobile, deployed behavior or all lifecycle states.
Technical designs remain historical architecture references. Their old pending
gates, protocol paths and weekly-only language must be read with the subsequent
rulings above; current bundled instructions are under `runtime/skills/bundled/`.

## Evidence references

- [Historical v1 PRD](https://github.com/t-benze/happyranch/blob/e8358a35174e7d29f74468527566bbf631099176/docs/product/prds/2026-07-19-agent-todos.md) and [historical recurrence PRD](https://github.com/t-benze/happyranch/blob/e8358a35174e7d29f74468527566bbf631099176/docs/product/prds/2026-08-10-todos-recurrence-v2.md).
- [v1 technical design](https://github.com/t-benze/happyranch/blob/e8358a35174e7d29f74468527566bbf631099176/docs/superpowers/specs/2026-07-18-agent-scheduled-work-design.md) and [recurrence technical design](https://github.com/t-benze/happyranch/blob/e8358a35174e7d29f74468527566bbf631099176/docs/superpowers/specs/2026-08-10-agent-todos-recurrence-v2-design.md); architecture stays there.
- [Current agent skill](https://github.com/t-benze/happyranch/blob/e8358a35174e7d29f74468527566bbf631099176/runtime/skills/bundled/todos/SKILL.md), [CLI](https://github.com/t-benze/happyranch/blob/e8358a35174e7d29f74468527566bbf631099176/cli/commands/schedules.py), [API create/manage/spawn and serializer](https://github.com/t-benze/happyranch/blob/e8358a35174e7d29f74468527566bbf631099176/runtime/daemon/routes/schedules.py).
- [Validation and civil-time calculation](https://github.com/t-benze/happyranch/blob/e8358a35174e7d29f74468527566bbf631099176/runtime/orchestrator/schedule_rules.py), [lifecycle/edit/renew service](https://github.com/t-benze/happyranch/blob/e8358a35174e7d29f74468527566bbf631099176/runtime/orchestrator/schedule_service.py), [persistence/claim/recovery](https://github.com/t-benze/happyranch/blob/e8358a35174e7d29f74468527566bbf631099176/runtime/infrastructure/schedule_store.py).
- [Scheduler](https://github.com/t-benze/happyranch/blob/e8358a35174e7d29f74468527566bbf631099176/runtime/daemon/schedule_scheduler.py), [trigger runner](https://github.com/t-benze/happyranch/blob/e8358a35174e7d29f74468527566bbf631099176/runtime/daemon/schedule_runner.py), [mounted web routes](https://github.com/t-benze/happyranch/blob/e8358a35174e7d29f74468527566bbf631099176/web/src/routes.tsx), [daemon mount](https://github.com/t-benze/happyranch/blob/e8358a35174e7d29f74468527566bbf631099176/runtime/daemon/app.py).
- [Founder list](https://github.com/t-benze/happyranch/blob/e8358a35174e7d29f74468527566bbf631099176/web/src/features/todos/TodosPage.tsx), [detail/controls](https://github.com/t-benze/happyranch/blob/e8358a35174e7d29f74468527566bbf631099176/web/src/features/todos/TodoDetailPage.tsx), [editor](https://github.com/t-benze/happyranch/blob/e8358a35174e7d29f74468527566bbf631099176/web/src/features/todos/components/EditDialog.tsx), [web API helper](https://github.com/t-benze/happyranch/blob/e8358a35174e7d29f74468527566bbf631099176/web/src/lib/api/schedules.ts).

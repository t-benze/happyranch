# Agent Todos PRD

| Field | Value |
| --- | --- |
| Status | build-spec |
| Owner | product_lead |
| Date | 2026-07-19 |
| Source Links | THR-105 messages 1-20; PR #464 design spike `docs/superpowers/specs/2026-07-18-agent-scheduled-work-design.md`; KB `agent-todos-autonomous-scheduling-ruling` |
| Commitment Boundary | build-ready |
| Founder Decisions | Required: none — all product decisions resolved via THR-105 seq19-20. Ruled: Fully-autonomous arming with mandatory normalization (seq19); user-facing label "Todos", internal primitive "Schedule" (seq19); v1 scope: one-shot + weekly recurrence, self-target only (seq19); founder-visible list/detail/pause/cancel/edit (seq19); default caps: 20 armed Todos per agent, 100 org-wide, 90-day one-shot horizon, 90-day recurring review/expiry unless explicitly marked indefinite (seq19); PRD #475 ratified as source of truth, defaults confirmed (seq20). |

## Problem

HappyRanch agents can respond to tasks, thread turns, working-hours cadence, and
nightly dreams, but they cannot own a specific future obligation. If the founder
tells `investment_advisor`, "send me a market update every Saturday," the system
currently has no product primitive for the agent to remember that instruction,
arm the future work, and execute it later without founder babysitting.

Calling this "Calendar" under-specifies the real need. The founder is not asking
to manage availability, invites, or calendar blocks. The need is an agent todo:
"I told an agent something it must remember to do." Time is a property of that
todo, not the product itself.

If we do nothing, founder-directed future work stays manual. The founder must
remember to re-prompt agents, configure recurring work by hand, or misuse
working-hours for task-specific commitments. That weakens the product thesis:
agents should reduce operational follow-through load, not create another list
for the founder to manage.

## Users And Workflow

Primary user: the founder/operator supervising a HappyRanch org.

Primary agent: any agent with the scheduling capability explicitly enabled.

Core workflow:

1. The founder gives an explicit instruction with timing, such as "Every
   Saturday, send me the weekly market update" or "Follow up with this customer in
   48 hours."
2. The instructed agent normalizes the request into a structured Todo: owner
   agent, brief, schedule type, next fire time, timezone, source instruction,
   expiry/review window, and status.
3. If the Todo fits the allowed envelope, the agent arms it without a second
   founder confirmation.
4. The founder can see the armed Todo in a founder-visible list and can pause,
   cancel, or edit it. Edits must re-normalize and re-validate the Todo.
5. When due, the Todo self-dispatches a normal root task to the same agent,
   carrying the original instruction and provenance.
6. The Todo records spawned task IDs and audit events so the founder can inspect
   what happened.

## Goals

- Let agents autonomously create future commitments for themselves when the
  founder/operator explicitly instructs them to do so.
- Make scheduled agent work legible: every Todo must show who owns it, what will
  happen, when it will fire, why it exists, its status, and what tasks it spawned.
- Bound the new capability with product-level controls: per-agent enablement,
  active caps, horizon limits, expiry/review, audit, and founder pause/cancel.
- Keep the user-facing concept simple: Todos are scheduled commitments, not a
  broad calendar or human task-management app.

## Non-Goals

- Building a general calendar app.
- Building an unscheduled backlog or generic task manager.
- Letting agents schedule other agents.
- Letting agents infer proactive schedules without an explicit founder/operator
  instruction.
- Shipping high-frequency recurring work, arbitrary cron, or broad recurrence
  rules in v1.
- Creating external roadmap or delivery timeline commitments from this PRD.

## No-List

- No hidden schedules. A Todo the founder cannot see and stop does not ship.
- No cross-agent scheduling.
- No unbounded recurrence by default.
- No complex calendar UI, drag/drop calendar, invites, availability, shared
  calendars, or event attendees.
- No unscheduled todos, priorities, tags, subtasks, or collaborative todo lists.
- No natural-language schedule that is armed without storing a structured,
  reviewable normalized schedule.
- No build that omits capability gating, audit, or founder stop controls.

## Success Signal

The first acceptance signal is operational, not vanity usage: the founder can
tell an enabled agent once, "send me a market update every Saturday," then see an
armed Todo and receive the resulting agent task on the next Saturday without
manually configuring a calendar entry.

Measurable v1 signals:

- At least one founder-created instruction results in an agent-created recurring
  Todo that fires and dispatches a normal task with correct provenance.
- The founder can inspect all armed Todos and pause/cancel any one of them.
- Every created, fired, spawned, paused/cancelled, expired, or failed Todo emits
  an audit trail entry.
- Invalid or out-of-envelope requests fail visibly with actionable remediation,
  such as "pause or cancel an existing Todo" or "weekly recurrence only in v1."

## Phase Scope

### v1 Cutline

v1 must support scheduled agent commitments, not a full todo app:

- Agent-created Todos from explicit founder/operator instruction.
- Self-target only: the creating agent is the owner and execution target.
- Schedule types: one-shot absolute time and simple weekly recurrence.
- Fully autonomous arming within controls: no pre-arming approval step.
- Mandatory normalization into a stored structured schedule before arming.
- Founder-visible list showing owner, source instruction, normalized brief,
  schedule, next fire time, timezone, status, expiry/review, and spawned task
  provenance.
- Founder pause/cancel/edit controls.
- Product-recommended control defaults: maximum 20 armed Todos per agent, maximum
  100 armed Todos org-wide, maximum 90-day one-shot horizon, default 90-day
  review/expiry window for recurring Todos unless the founder explicitly marks
  one indefinite.
- Full audit trail.

### Later

- Agent-proposed schedules that require founder confirmation, if users want a
  more conservative mode.
- More recurrence rules: monthly, nth weekday, arbitrary intervals, end after N
  runs.
- Founder-created manual Todos on an agent's behalf.
- Unscheduled agent todos/backlogs.
- Editing recurring rules in a richer UI.
- Better reporting: upcoming workload, missed fire diagnostics, and cost by Todo.

## Functional Requirements

- Agents can create a Todo only through an explicit schedule-creation path while
  handling a founder/operator instruction.
- Todo creation is available only to agents with the per-agent scheduling
  capability enabled.
- A Todo must be self-targeted; `owner_agent` and `target_agent` are the same in
  v1.
- A Todo must store both the verbatim source instruction and the normalized brief
  that will be dispatched.
- A Todo must normalize time into a stored next fire instant and preserve the
  timezone used for founder display.
- v1 supports exactly two schedule types: one-shot absolute time and simple
  weekly recurrence.
- Unsupported recurrence requests must be rejected rather than approximated.
- Active caps and horizon/expiry rules must be enforced at creation and edit
  time.
- On fire, a Todo dispatches one normal root task to its owner agent.
- One-shot Todos become terminal after firing.
- Weekly Todos re-arm to the next valid weekly occurrence until paused,
  cancelled, expired, or explicitly marked indefinite by the founder.
- Founder management must include list, detail, pause, cancel, and edit.
- Editing a Todo must re-run normalization and envelope validation before the
  changed Todo is armed.
- Failed fire attempts must leave an inspectable status and audit trail rather
  than silently disappearing.

## Data And Provenance Requirements

The Todo list must render stored facts, not inferred status.

Each Todo needs a stable ID, owner agent, team/org context, schedule type, next
fire time, timezone, recurrence fields where applicable, normalized brief, source
instruction, status, created time, expiry/review window, indefinite flag,
last-fired time, fire count, and spawned task IDs.

Important provenance rules:

- Source instruction is stored verbatim so the founder can see why the Todo
  exists.
- Normalized brief is stored separately so the founder can see exactly what will
  be dispatched.
- Spawned task IDs are stored on the Todo and linked from the founder-visible
  surface.
- Every state transition writes an audit event.
- Token usage for Todo-fired work should be attributable to the scheduled work
  scope in the implementation design, but the PRD does not require a new spend UI
  in v1.

## Acceptance Criteria

- Given scheduling is disabled for an agent, when that agent attempts to create a
  Todo, creation is rejected with an actionable explanation.
- Given scheduling is enabled, when the founder instructs an agent to send a
  weekly Saturday market update, the agent arms a weekly Todo with a normalized
  schedule, source instruction, next fire time, and expiry/review window.
- Given an armed Todo exists, the founder can see it in a list with owner,
  status, next fire time, normalized brief, source instruction, and provenance.
- Given the founder pauses or cancels an armed Todo, it does not fire while paused
  or after cancellation.
- Given the founder edits an armed Todo, the edited Todo is re-normalized,
  re-validated, and audited before it is armed.
- Given a Todo reaches its fire time, the system dispatches exactly one normal
  root task to the owner agent with the normalized brief and records the spawned
  task ID.
- Given a one-shot Todo fires successfully, it becomes terminal and does not fire
  again.
- Given a weekly Todo fires successfully, it computes and stores the next fire
  time unless it has reached expiry.
- Given a request exceeds caps, horizon, or supported recurrence rules, it is not
  armed and the founder-facing error explains what to change.
- Given any create, fire, spawn, pause, cancel, expire, edit, failure, or timeout
  event occurs, the audit trail records it.
- Given the founder reviews the feature, no hidden, cross-agent, unbounded, or
  unsupported recurrence behavior exists in v1.

## Risks

- Product risk: "Todos" may create expectations for unscheduled task lists,
  priority, tags, and subtasks. The v1 UI copy must say scheduled Todos clearly.
- Permission risk: autonomous scheduling is token-spend authority in the future.
  Capability flag, caps, audit, and founder stop controls are v1 blockers, not
  hardening.
- Trust risk: if normalization is hidden or vague, the founder will not know what
  the agent armed. The UI must show the structured schedule before and after it
  fires.
- Scope risk: recurrence tends to expand quickly. Weekly-only is the right v1
  because it matches the founder's anchor use case without becoming cron.

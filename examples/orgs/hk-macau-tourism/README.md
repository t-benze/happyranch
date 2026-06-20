# HK/Macau Tourism — Goal-on-Working-Hours Worked Example

This directory demonstrates the **goal-on-working-hours** pattern: an
autonomous, scheduled, multi-cycle goal-pursuit setup that runs on
HappyRanch with **zero code or schema changes**. Every piece already
exists in the platform; this example shows how to assemble them.

The canonical recipe is documented in the KB at `goal-pattern-on-working-hours`.
The working-hours feature design lives in the spec at
`docs/superpowers/specs/2026-06-10-working-hours-design.md`.

## The six-part goal sentence

The goal is expressed in a single prose sentence that the agent reads
every wake. It carries six named parts:

| Part | Name | What it does |
| --- | --- | --- |
| 1 | **Outcome** | What "done" means (e.g. "ferry-booking E2E suite green"). |
| 2 | **Verification surface** | The command or test that proves completion (`npm run test:e2e -- booking/ferry`). |
| 3 | **Constraints** | Immutable quality bars the agent must preserve (mobile-first, <3s-on-3G). |
| 4 | **Boundaries** | Files and modules the agent may touch; everything else is off-limits. |
| 5 | **Iteration policy** | What to record each iteration, how to pick the next failing spec. |
| 6 | **Blocked-stop condition** | When to escalate rather than retry (partner-API contract change across two consecutive iterations). |

This concrete sentence lives in **one** `-` list item under the `## Routine Tasks`
H2 section of `org/agents/dev_agent.md`.

## The assembly: three slotted-together pieces

1. **Routine** — the goal-shaped checklist item in `## Routine Tasks`.
   On every wake the agent re-reads this static text and decides whether
   to dispatch the next iteration or stop.

2. **Durable state** — the goal's progress is tracked *outside* the
   routine text. In this example we document that the agent keeps the
   failing-spec list and red count in a recurring root task or thread,
   so each wake can read "where am I" before deciding.

3. **Cadence** — the `working_hours:` block in `org/config.yaml`
   sets a **windowed** schedule for `dev_agent`: weekdays 09:00–18:00
   Asia/Hong_Kong, every 2 hours (wakes at 09:00, 11:00, 13:00, 15:00,
   17:00). The scheduler fires on the clock; the agent re-reads its
   checklist and self-dispatches one root task per routine.

## The load-bearing stop-condition discipline

**Working-hours is cadence-driven, not goal-state-driven.** The scheduler
fires on the clock and re-reads a *static* checklist; it has **no memory
of whether the goal is done**. Therefore the agent MUST evaluate the
completion condition on every wake against durable state and decline to
spawn once the verification surface is green.

This is why the routine item contains the clause:

> if it IS green, STOP and report — do NOT spawn another iteration.

Without this clause, a naive routine ("always run an iteration") would
grind forever and burn tokens on every wake — the scheduler would never stop
on its own. The stop condition in the routine text is the anti-grind
discipline; it is load-bearing.

## The cost governor

There is **no per-goal token-budget gate**. Cost is bounded instead by the
**window + interval** — weekdays only, every 2 hours → a known, capped
number of wakes per day. That caps cadence, not spend — size the interval
accordingly. For a continuous agent (not shown here), a 30-minute interval
means up to 48 wakes/day; a 15-minute interval means 96 wakes/day. Choose
the interval that gives the responsiveness you need at the cost you accept.

## Files in this example

- `org/config.yaml` — working-hours block (windowed cadence for `dev_agent`)
- `org/agents/dev_agent.md` — `## Routine Tasks` section with the goal sentence

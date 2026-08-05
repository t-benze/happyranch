# Work Hours and routine execution PRD

| Field | Value |
| --- | --- |
| Status | shipped |
| Owner | Product Lead |
| Date | 2026-08-04 |
| Source Links | THR-140 seq. 18–19; THR-145 seq. 2–3; current Work Hours daemon/web contract |
| Commitment Boundary | analysis-only — documents the existing Founder-approved behavior; no extension is authorized |
| Founder Decisions | Ruled: current founder-authenticated schedule writes and read-only routine authoring. Required: none unless an excluded extension is proposed. |

## Problem and outcome

The founder must safely understand and configure when an eligible agent wakes and what normal root work follows. The outcome is a truthful resolved schedule, provenance, validation outcome, and wake record—not per-agent toggles, a second scheduler, or UI-authored routine tasks.

## Users and authority baseline

Founder-authenticated browser users configure available schedule tiers/eligibility. The daemon validates and executes; the browser must not claim a client-side role decision. Routine content remains authored in agent markdown, not the console. `/schedule` is a bookmark redirect to Work Hours Wakes, not a separate product surface.

## Shipped constraints

- Resolution is leaf-by-leaf: organization default → team default → agent override, for window/continuous mode, timezone, interval, active days, and startup catch-up.
- One organization `working_hours.enabled` gate plus eligibility selection controls wakes; `On` is derived. There is no independent per-agent enable switch.
- Validation precedes atomic write; invalid changes are rejected and last-known-good configuration remains active.
- For an eligible agent with non-empty `## Routine Tasks`, each top-level routine bullet self-dispatches one normal root task on that agent’s team. Wakes are scheduler triggers, not tasks/threads/talks, and retain spawned root IDs.
- One `(agent, local_date, slot)` record prevents duplication; startup catch-up uses only the most recent eligible slot. Failed callback/wake errors remain terminal rather than replaying all missed slots.

## Scope and non-goals

In scope: overview, agent detail, effective-value provenance, tier writes/eligibility, validation errors, next wakes, wake history, and read-only routine list.

Non-goals: routine editor; holidays/blackouts; urgent override; held escalations; calendar/timeline or schedule-health predictions; print/export; every prototype display field; an independent Schedule feature; and a new dispatch policy.

## Functional requirements

1. **FR-1–3:** show global gate, eligibility, resolved leaf values, source-of-value metadata, derived agent state, and next wakes without an invented per-agent switch.
2. **FR-4–5:** accept only supported tier/eligibility writes, surface validation errors, and preserve last-known-good effective configuration after rejection.
3. **FR-6–7:** display wake slots/records and spawned root-task provenance; distinguish disabled, ineligible, no-routine, scheduled, fired, and terminal-error outcomes.
4. **FR-8:** show read-only Routine Tasks as the markdown-authored source and direct authoring to the canonical agent source.
5. **FR-9–10:** retain compatibility redirect behavior and label any unavailable prototype calendar/timezone/metric field rather than calculating a false fact.

## Workflow and state behavior

Founder opens overview → inspects effective values/provenance and eligibility → edits a supported tier → daemon validates → atomic success updates next wakes or failure retains old effective state with explanation. At a due slot the scheduler records it → tests gates/eligibility/routine bullets → self-dispatches one root per bullet or records a non-dispatch/error outcome. Startup considers the latest eligible missed slot only.

## API and data dependencies

Work Hours list/detail/status/next-wakes, org settings, teams, agents, scheduler/wake store, agent markdown, task dispatch and provenance records. The data contract must expose effective values and source metadata before any projection. Calendar/health/holiday/override fields are absent until backed by a record and endpoint.

## UX and accessibility criteria

Use a semantic effective-schedule table with headers, source labels that do not rely on color, explicit timezone/availability caveats, keyboard-operable tier controls, and an error summary tied to invalid fields. Disabled/eligible/derived states must be textual. Empty routine and no-next-wake states state why and link to the correct canonical source.

## Acceptance criteria

- A fixture confirms organization/team/agent leaf resolution and source provenance for each field.
- Global disabled or ineligible state prevents a wake without rewriting schedule records or showing a per-agent toggle.
- Invalid write leaves last-known-good configuration in effect; one due eligible slot produces at most one wake record and one root per top-level routine bullet.
- Catch-up considers only the latest eligible slot; terminal errors remain inspectable.
- Routine list is read-only and all loading/empty/error/populated states are accessible and truthful.

## Metrics

Record validation rejection rate, derived eligibility distribution, wake due→dispatch latency, duplicate-suppression events, terminal wake/callback failures, and root tasks spawned per wake. These diagnose scheduler behavior; they do not promise a service level.

## Risks and gates

Risk: a frontend projection forks scheduler semantics. Mitigate by deriving only from resolved API fields. Risk: routine editing changes governance/source ownership.

**Engineering gate:** feasibility/schema/API review before unavailable display fields or any excluded scheduler extension. **Founder gate:** approve a holiday/override/routine-authoring or dispatch-policy change before build planning. The current implementation needs neither gate to remain as documented.

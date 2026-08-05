# Core operations console PRD

| Field | Value |
| --- | --- |
| Status | draft |
| Owner | Product Lead |
| Date | 2026-08-04 |
| Source Links | THR-140 seq. 17–19; THR-145 seq. 2–3; `2026-08-04-current-web-inventory-and-reconciliation.md` |
| Commitment Boundary | analysis-only — records the shipped baseline and bounded reconciliation work; no roadmap commitment |
| Founder Decisions | Ruled: founder-authenticated browser baseline; no generalized roles. Required: none for this reconciled scope. |

## Problem and outcome

A founder needs one trustworthy console to initialize/select an organization, navigate operational records, see work needing attention, and complete decisions on their canonical task, thread, job, schedule, agent, or settings surface. The outcome is an operable, traceable console—not a new permission system or a promise that every prototype field exists.

## Users and authority baseline

Primary user: the founder operating a local organization. The daemon bearer is localhost-bootstrapped and server authorization remains authoritative; the browser has no RBAC gate. Founder-authenticated actions may create/modify only where a current route permits. Org scoping and thread participation checks remain server-side. There is no commitment to delegated operators, remote access, autonomy toggles, or UI-only permission claims.

## Shipped constraints

- Root discovers organizations then redirects to the first dashboard or org-less onboarding. Executor registration/conformance and agent enrollment are distinct flows.
- Threads, tasks, jobs, schedules, agents, and settings are records with their own mutations; dashboard is triage. Todo schedules are persisted `SCHEDULE-*` work and are not Work Hours.
- System settings requiring daemon restart are read-only; organization settings may live-apply only through the supported write contract. Snapshot/SSE data can be stale or unavailable.
- JobRecord does not support prototype Thread/routed-via/kind/PR context fields. Do not fabricate them.

## Scope and non-goals

In scope: onboarding, AppShell navigation/accessibility, dashboard triage, Threads, Tasks, persisted Todos, Agents, Jobs, and Settings exactly as mapped in the inventory.

Non-goals: new runtime CRUD beyond routes; an RBAC/delegation model; autonomous agent controls; changing schedule policy; new JobRecord fields; a mobile-shell commitment; design-system/prototype routes; and a UX token remapping project. Frontend Engineering owns visual component quality; Engineering owns new browser/daemon contracts.

## Functional requirements

1. **FR-1 Onboarding:** distinguish prompt issued, conformance in progress/success/failure/expiry/existing registration, organization create/load failures, and scoped registration from normal auth.
2. **FR-2 Organization context:** show selected organization and let the user navigate/switch without conflating registration, enrollment, or runtime binding.
3. **FR-3–4 Shell:** provide keyboard-operable AppShell navigation, command/help discovery, clear selected context, and a recoverable route/error state.
4. **FR-5 Dashboard:** render summary/inbox data as traceable daemon projections and route the user to the actual record for action.
5. **FR-6–8 Threads:** list/detail messages, tasks, and inbox evidence; perform compose/send/participant/lifecycle actions only through supported routes; expose open/closed state and SSE uncertainty.
6. **FR-9–11 Tasks:** show roots, child roll-up, recall, dependencies/revisit lineage, attachments, linked jobs, escalation, cancel/revisit state, and fan-out status without turning a review job into a child task.
7. **FR-12 Todos integration:** display persisted schedule records and their target-agent/root-task behavior; allow pause/cancel/edit only when provided by the schedule contract. The independently shippable requirements remain in `2026-07-19-agent-todos.md`.
8. **FR-13–15 Agents:** represent roster/detail, pending enrollment, approval/rejection, memory, team/executor/model/repository bindings as server-backed facts, not browser permission state.
9. **FR-16–18 Jobs:** preserve command, review requirement, request/task linkage, output/tail/wait and run/reject/stop state. Unsupported prototype context is unavailable, not blank factual data.
10. **FR-19–22 Settings:** identify per control whether it is editable/live-applied, restart-required/read-only, unavailable, or an exceptional scoped-token/runtime-adapter/executor flow.

## Workflow and state behavior

On first entry, discover orgs → redirect dashboard or onboarding → establish selected org → browse triage → open authoritative record → invoke allowed mutation → show persisted success, pending/SSE, refusal, or failure. Thread/task/job/schedule state is never inferred from button visibility. A 401 clears/retries once; a 403 explains server denial. Loading, empty, stale, and failed states preserve context and recovery action.

## API and data dependencies

`/auth/bootstrap`; org/runtime/executor-binary APIs; dashboard; threads; tasks; schedules; agents; jobs; settings/teams/runtime-executors/adapters; corresponding SSE/download channels. Required identity/provenance is org, record ID, actor/action where retained, and linked task/thread/job/schedule IDs. Job routing/thread/kind/PR fields are explicitly absent and Engineering-owned.

## UX and accessibility criteria

Desktop AppShell must provide semantic navigation, focus restoration for dialogs/drawers, labeled forms and destructive actions, keyboard shortcuts discoverable in help, non-color status cues, readable empty/error/retry messaging, and announced async outcome. Do not hide controls based on unverified client roles. Content-level responsive treatment does not imply a responsive navigation-shell target.

## Acceptance criteria

- A fresh runtime can reach either a labeled onboarding terminal state or a selected-org dashboard; failed/expired registration never reads as registered.
- A user can trace a dashboard item to its task/thread/job and execute/observe the canonical mutation or a truthful server denial.
- Roots, lineage, fan-out/review states, persisted schedule behavior, and job command/review state survive list/detail refresh.
- Settings communicate read-only versus live/restart behavior, and unsupported JobRecord fields are not shown as facts.
- Keyboard-only use reaches navigation, dialogs, form errors, and async result feedback; all loading/empty/error/populated states are explicit.

## Metrics

Track onboarding terminal-state completion/error rate, time from dashboard triage to canonical record, failed/retried browser requests, stale/SSE reconnect indication rate, and task/job/schedule action outcome rate. Metrics are operational snapshots, not service-level claims.

## Risks and gates

Risk: a UI can imply permission where only daemon enforcement exists. Mitigation: authority copy follows server contracts and displays 401/403 truthfully. Risk: stale projections can mislead; label freshness/unavailability.

**Engineering gate:** schema/API proposal before JobRecord context fields or any Settings/agent/runtime contract extension. **Founder gate:** authorize any delegated-role, remote access, autonomy, or changed scheduling scope. No gate is needed to retain the shipped baseline.

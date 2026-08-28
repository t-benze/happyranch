# System-surface redesign PRD

| Field | Value |
| --- | --- |
| Status | draft |
| Owner | Product Lead |
| Date | 2026-08-05 |
| Source Links | PR #555; `2026-08-04-current-web-inventory-and-reconciliation.md`; four reconciled domain PRDs; founder `screens.zip` in THR-140; THR-140/145; TASK-4378; current `web/src/routes.tsx`, Settings and Work Hours contracts; THR-147 seq. 2 (Engineering Manager); THR-148 seq. 2 (Frontend Engineer) |
| Commitment Boundary | analysis-only — a target information architecture and governed evaluation frame; it does not authorize implementation, a visual rewrite, authority changes, or a roadmap/timeline |
| Founder Decisions | Required: redesign cutline and sequencing; whether to fund a cross-surface IA implementation after Engineering feasibility review. Ruled/current baseline: founder-authenticated browser control plane, server enforcement, no general role model, and no assistant governance writes. |

## Decision summary

**Recommendation:** organize the console by user intent and authoritative record, not by the screen bundle's visual grouping. Put organization-wide operating gates in **Settings → Organization → Operating controls**; put domain workflow, state, and evidence in the domain surface; keep consequential governance decisions on their canonical record. This makes the surface understandable without inventing a permission system or changing scheduler semantics.

The Work Hours enablement control is the first concrete application. Its target placement is **Settings → Organization → Operating controls**, alongside the existing organization configuration write boundary—not Settings → System and not an independent per-agent switch in Work Hours. Work Hours remains the functional surface for effective schedule/provenance, eligibility explanation, tiered scheduling, read-only routine-source visibility, and wake evidence. It should show a derived status and a link to the operating control, rather than a second toggle. This is a placement requirement only: it preserves the one org-wide `working_hours.enabled` gate, the existing include/exclude eligibility model, server validation/atomic persistence, and current founder-authenticated/server-enforced authority. It does **not** authorize an RBAC change, different write endpoint, or changed wake behavior.

## Evidence and truth boundary

### Shipped constraints (facts to preserve)

- The SPA is a local, bearer-authenticated console: it bootstraps at `/api/v1/auth/bootstrap`, holds the token in `sessionStorage`, and relies on server-side route authorization, organization scope, and thread-participant checks. The frontend has no general user-role/RBAC gate. A label such as “founder-only” is therefore governance intent, not proof of a distinct human identity model.
- Root is organization discovery/onboarding; normal product routes live under `/orgs/:slug/` inside AppShell. Current families are Dashboard, Threads, Tasks, Todos, KB, Audit, Skills, Agents, Jobs, Dreams, Work Hours, Artifacts, Usage, Health, and Settings. Assistant dock, command palette, help, top bar, navigation, error boundary and auth/SSE/WebSocket feedback are shared infrastructure.
- System Settings are read-only when a daemon restart is required. Organization Settings are the live persisted-org configuration boundary. `PUT /orgs/{slug}/settings/org` already accepts the single `working_hours` section, validates referenced agents/teams, builds/validates the full candidate, writes transactionally to the DB-backed org-settings store, records `config:working_hours` audit evidence, and returns the resolved snapshot. There is no system-settings write API for Work Hours.
- Work Hours is a single functional surface at `/orgs/:slug/work-hours`, with a Wakes view at `?view=wakes` and agent detail at `/work-hours/:agent`; `/schedule` only redirects. Its current data contracts are Work Hours records/status/next-wakes plus the resolved Settings snapshot. The scheduler has one organization `working_hours.enabled` gate and a separate eligibility include/exclude policy; “On” is derived from both. It has no independent per-agent enable control.
- Work Hours resolves schedule leaves organization → team → agent. `GET /settings` returns raw tiers rather than a server-computed effective/provenance model; the UI currently derives the display while scheduler-side resolution remains authoritative. Invalid writes retain last-known-good configuration. A due eligible slot creates at most one wake record; a non-empty Markdown `## Routine Tasks` section generates one self-targeted root task per top-level bullet. Routine bullets are Markdown/system-prompt owned, not Work Hours configuration. Wakes, tasks, threads and Todo schedules are distinct records and behaviors.
- The current Work Hours projection is not a blank slate: it has the feature switch in its header, an impact-preview then confirm/save eligibility flow, a confirm-before-disable safeguard, “saved; effective at the next scheduler pass” feedback, and a config-read recovery banner. Settings is a full page with a narrow content panel/left sub-navigation (plus a TopBar shortcut); Work Hours is a wider workspace under **Operate**, with overview/wakes tabs and agent detail. The desktop shell has no responsive reorganization target.
- Dashboard, metrics, health, runtime/config/process claims, and streaming state are time-bounded daemon projections and can be unavailable or stale. A stream augments polling/cache; it is not a durable-delivery guarantee.
- B2 custom Skills use the same immediately editable record for verified agents and founders. It is default Hidden — eligibility not configured, retains version/provenance/validation/materialization evidence, and has no legacy proposal UI, routes, history, or compatibility behavior.
- The founder bundle is design evidence, not a current contract. `/__prototypes/*`, `/__design__`, mock providers, component registry, redirect plumbing, and NotFound are explicitly outside this product redesign scope.

### Redesign requirements (not yet authorized to build)

The implementation, if ever approved, must reconcile every changed surface with the current-web inventory and one domain PRD. A design may improve hierarchy, navigation, labels, state presentation, and cross-links; it may not silently replace source-of-truth, API, authority, or lifecycle behavior.

## Problem and users

The founder needs to move from an operational question to the record that can answer or act on it: “What needs a decision?”, “What is running or failed?”, “What governs this organization?”, and “When will this agent run?” The current surface inventory is broad, while the supplied screens mix evidence, workflow, and configuration. A visual consolidation without an intent model would make governance look like a feature setting, and could make stale evidence or browser-visible controls appear authoritative.

Primary user: the founder operating one local organization. Secondary audience: an authorized agent/session only where an existing daemon contract permits it. The target remains desktop-first; content-level mobile improvements are welcome only when separately scoped, because the current AppShell has no responsive-shell commitment.

## Goals and success signals

- Make the destination for an operating question predictable: overview, work, governance, evidence, or setup.
- Keep the canonical write/decision surface visible; dashboard and assistant are triage/handoff, not substitute approval surfaces.
- Expose effective configuration with provenance and state rather than only raw controls.
- Reduce navigation ambiguity without removing current routes or manufacturing unsupported data.
- Preserve keyboard operation, semantic structure, visible focus, labeled actions, non-color status, and truthful loading, empty, stale, denied and failed states.

Success is measured only after a Founder-approved implementation: task-to-canonical-record completion rate; time from dashboard/assistant handoff to record; navigation backtracks; configuration validation/retry rate; percent of evidence views with source/freshness/unavailable treatment; and 401/403/error recovery outcomes. These are operational signals, not SLA or cost promises.

## Target information architecture and navigation

| Navigation group | Intent and authoritative destinations | Cross-surface rule |
| --- | --- | --- |
| **Operate** | Dashboard, Inbox/Threads, Tasks, Jobs, Todos | Dashboard is an attention queue; Threads, Tasks, Jobs and Todos own their mutations and records. Do not conflate scheduled Todos with Work Hours wakes. |
| **Organization** | Agents, Teams, Work Hours | Agent/team roster explains membership and ownership. Work Hours owns cadence configuration context, resolution/provenance, read-only routine-source visibility, next wakes, history and task linkage. |
| **Govern** | Skills, Settings | Skills owns B2 custom-skill version, provenance, validation, eligibility, and materialization evidence; Settings owns organization and runtime operating controls. Client navigation must not suggest a control is authorized when the daemon denies it. |
| **Evidence** | KB, Artifacts, Audit, Dreams, Usage, Health | These are sources, histories or projections. Show source/window/freshness and route actions back to their canonical decision record. |
| **Global shell** | Organization switcher, command palette, help, assistant dock, account/runtime context | Present across normal AppShell routes. Assistant may navigate and present tool/source evidence but has no direct governance-write authority. |

The navigation may use these labels and grouping only after an approved delivery plan validates route preservation, deep links, command-palette/help affordances, information-scent research, and desktop layout constraints. Existing route paths remain compatibility contracts until an approved migration says otherwise.

### Governance placement rules

1. **Settings → Organization → Operating controls** holds organization-wide gates and eligibility policies whose change governs whether a domain can run at all. Each control shows scope, effective state, impact, validation outcome, and a link to the affected functional surface.
2. **Settings** contains only Assistant, Organization, and Executors. The retired System rail and its read-only runtime facts have no replacement prerequisite; queue-worker and maximum-orchestration-step diagnostics may be reconsidered only when Health is independently touched. This THR-140 correction supersedes the former System-placement rule without changing runtime contracts.
3. **Functional domain surfaces** own in-context configuration, effective resolution, workflow execution, and evidence. They may summarize a governing control and deep-link to it, but must not duplicate a mutable control or derive a divergent state.
4. **Custom Skills** preserve version/provenance, technical validation, eligibility, and materialization as distinct dimensions; technical validation is not a permission grant.
5. **Evidence and assistant surfaces** may hand off to the authoritative record but never make an unsupported approval, authorization, retention, cost or freshness claim.

### Work Hours placement requirement

| Concern | Target location | Required behavior | Explicitly not changed |
| --- | --- | --- | --- |
| Organization enablement + eligibility | Settings → Organization → Operating controls | One representation of `working_hours.enabled` and its include/exclude eligibility policy; show organization scope, eligibility impact preview, confirm-before-disable, and a warning that changing it affects future eligibility/wakes. Submit only through `PUT /orgs/{slug}/settings/org`'s existing `working_hours` section and show server validation/result plus next-pass timing. | No new role/RBAC boundary, per-agent toggle, different persistence source, daemon-global switch, or different scheduler gate. |
| Schedule tiers and resolved configuration | Work Hours overview and agent detail | Show organization/team/agent leaf provenance, effective cadence, derived eligible/on state, and supported tier edits. | No client-side validation authority or conversion of leaf resolution to flat per-agent copies. |
| Routine source + execution evidence | Work Hours agent detail and Wakes | Show the current read-only Markdown source, no-routine/disabled/ineligible reasons, next-wake preview, wake record and spawned root-task links. | No routine editor, source migration, calendar, override or catch-up policy change. |

The user experience should ensure the setting is *discoverable* from Work Hours (status banner + “Manage operating control” link) without turning the functional surface into a competing control plane. If research finds the organization-level placement materially harms discoverability, the fallback is a Settings-owned control mirrored as read-only context in Work Hours—not a duplicate editable switch.

## Functional requirements

1. **FR-1: Intent-first wayfinding.** Every normal AppShell page belongs to one navigation group above, retains its current route/deep-link behavior unless separately migrated, and identifies the selected organization.
2. **FR-2: Canonical action.** A page that summarizes another record links to the record and cannot imply it executed a task/thread/job/schedule/governance action itself.
3. **FR-3: Provenance and truthfulness.** Configuration shows raw/effective/source-of-value where resolution applies. Evidence/projection shows source and time/window, or clear unavailable/derived/stale state. The redesign must not portray client-side Work Hours derivation as scheduler authority.
4. **FR-4: Governance placement.** Organization-wide enabling/eligibility resides once in Settings → Organization operating controls; domain workflows retain context and a non-mutating deep-link. Every affected state explains the applicable gate without exposing a nonexistent per-agent switch. The Settings implementation carries forward the current eligibility impact preview, confirm-before-disable, server-error recovery, and next-scheduler-pass feedback, with sufficient roster/team data to make eligibility understandable.
5. **FR-5: Work Hours contract preservation.** Any redesign of Work Hours preserves one organization gate; eligibility semantics; leaf-by-leaf resolution; server validation and last-known-good behavior; one-slot/one-wake uniqueness; limited startup catch-up; terminal wake failure evidence; and root-task provenance.
6. **FR-6: Authority honesty.** Browser-visible controls never claim frontend role enforcement. Founder-authenticated baseline and server 401/403 response behavior remain explicit; no delegated roles, remote access, or assistant governance writes are inferred.
7. **FR-7: B2 Skills honesty.** Verified agents and founders create the same immediately editable custom-skill record. It is default Hidden — eligibility not configured; legacy proposal UI, content, records, history, routes, adapters, and compatibility are deleted.
8. **FR-8: State coverage and accessibility.** Each redesigned family defines loading, empty, populated, stale/disconnected, validation failure, 401/403 and recoverable error behavior, with semantic landmarks/controls, keyboard path, focus management, text status, and non-color cues.
9. **FR-9: Compatibility and exclusions.** Retired `/schedule` and `/spend` bookmark redirects continue; developer/design routes remain excluded. No prototype-only fields, metrics, jobs metadata, approval state or permission claims become requirements without an authoritative record/API.

## Scope and non-goals

### Analysis scope

- A reconciled navigation/IA and cross-surface placement model covering every currently mounted normal product family through the PR #555 inventory.
- A concrete, contract-preserving placement decision for Work Hours organization enablement/eligibility.
- A requirements and acceptance frame for a future implementation proposal, including evidence that must be supplied before visual/build review.

### Non-goals / no-list

- Authorizing a frontend redesign, route migration, visual token rewrite, design-system rebuild, or delivery date.
- Changing daemon/browser authorization, granting manager/operator/self-service control, or asserting client-side RBAC.
- Moving or duplicating Work Hours persistence, changing its single `working_hours` section/API, adding independent agent enablement, or changing eligibility, resolution, scheduler, wake, Markdown-owned routine source, catch-up or task-dispatch behavior.
- DB-backed routine authoring, Markdown migration/cleanup, holidays, blackouts, urgent overrides, calendar/timeline, schedule prediction, print/export, or mobile-shell work.
- Reviving a legacy Skills route, record, proposal workflow, compatibility layer, or granting skills permissions.
- Assistant approval/denial or direct governance writes; retention, external analytics, billing/cost meter, SLA, remote/multi-user access, or prototype-only data contracts.

## Dependencies, delivery gates, and decision ledger

| Gate / decision | Owner | Current status | Required before build planning |
| --- | --- | --- | --- |
| #576/#577 pilot cutline and sequencing | Founder | Ruled in THR-140 | Deliver #576 first; then #577 as a navigation-only AppShell grouping pilot. Preserve routes, deep links, and compatibility redirects; broader navigation/surface changes remain out of scope. |
| Work Hours operating-control placement | Founder | Ruled in THR-140 | Settings → Organization is the sole editable owner for Work Hours enablement and eligibility; Work Hours provides derived context and a Manage operating control handoff. Preserve the existing organization settings patch and scheduler contract. |
| Current Work Hours contract feasibility | Engineering Manager | Confirmed, THR-147 seq. 2 | Settings placement is feasible only as an org-contextual representation of the one `org_settings[working_hours]` section through the existing org patch; preserve gate-before-eligibility-before-resolution-before-routine/evidence order, validated atomic writes and audit. |
| Current UX/fidelity constraints | Frontend Engineer | Confirmed, THR-148 seq. 2 | The API supports Settings placement, but Work Hours currently owns wider layout, roster/impact preview, modal editors, confirmation/recovery/timing feedback and Sidebar scent. An implementation needs an explicit cross-navigation/data/layout design, not a field relocation. |
| Detailed implementation plan and API impact | Engineering Manager | Not started | Reconcile touched routes/components, preserve endpoints/deep links, and identify any contract change as a new proposal. |
| Assistant governance writes or delegated human roles | Founder + Engineering | Not authorized | Separate product/authority design; excluded from this redesign. |

**Approved pilot sequence:** deliver the #576 Work Hours governance-placement pattern first, then #577 as the bounded navigation-only AppShell grouping pilot. Preserve existing routes, deep links, and compatibility redirects. Do not use either pilot to smuggle in cross-domain runtime changes or broader navigation consolidation.

## Acceptance criteria for any future implementation proposal

- It includes a route-to-group and current-contract crosswalk covering every changed normal product route, with explicit retained deep links and excluded developer/compatibility routes.
- The Work Hours organization control has exactly one editable UI owner in Settings → Organization operating controls; Work Hours shows derived gate/eligibility state and a link, not a duplicate editable switch.
- A contract test/proof demonstrates that changing the control still uses `PUT /orgs/{slug}/settings/org` with the single `working_hours` section, rejects invalid agent/team references, preserves last-known-good state and `config:working_hours` audit evidence, and does not introduce a per-agent or daemon-global enable write.
- Fixtures prove the gate-before-eligibility-before-resolution-before-routine/evidence order plus organization/team/agent schedule resolution and provenance; disabled/ineligible/no-routine/scheduled/fired/terminal-error outcomes remain distinct, with root-task links preserved.
- Browser evidence covers loading, empty, error, 401/403, validation rejection, stale/disconnected, and populated states; keyboard-only navigation reaches controls, feedback and deep links. Any desktop/mobile limitation is disclosed rather than hidden.
- No visual element claims a new role, decision, data field, cost, health promise, freshness guarantee, assistant authority, or Skills write path without an approved supporting contract.

## Risks

- **False authority:** grouping a control under “System” could imply daemon-wide/restart semantics; putting it only in Work Hours could imply local feature scope. The proposed Organization operating-controls location keeps the live organization scope truthful.
- **Duplicate control drift:** two editable representations could race or diverge. One canonical edit point plus derived in-context status prevents this.
- **Design-led contract invention:** screens may make unimplemented data look factual. The inventory and domain PRDs remain the source of requirements; prototype-only elements remain excluded.
- **Scope inflation:** an IA rewrite can become hidden platform work. The staged pilot and decision ledger prevent it from authorizing backend, authority, or visual-system expansion.

# HappyRanch operating-console design reconstruction — PRD pack

| Field | Value |
| --- | --- |
| Status | draft |
| Owner | Product Lead |
| Date | 2026-08-04 (reconciled against current web implementation) |
| Source Links | THR-140; `artifact:thread-draft-20260803T143323Z-screens.zip`; feasibility consultation THR-141; TASK-4325 audit of current `origin/main` web routes, feature components, browser API clients, and daemon route contracts |
| Commitment Boundary | analysis-only — no build, roadmap, or external delivery commitment |
| Founder Decisions | Current baseline: founder-authenticated actions and existing read-only cutlines remain authoritative. Only a new assistant write-authority scope requires a Founder decision. |

## Purpose and evidence boundary

This pack reconstructs the smallest honest set of PRDs implied by the founder-supplied prototype bundle, then reconciles that evidence with the full current shipped web implementation. It is not a claim that every visible control is implemented, approved, or intended for v1.

I downloaded and rendered all 34 HTML entry points in the bundle (31 product screens plus three print/state variants), reviewed their static UI text and interaction code, and used the provided `shell.js` and `workhours.js` as evidence. Screen labels and sample values are illustrative unless this document explicitly calls them out as a requirement. Several screens self-identify as a prototype or as needing new fields/routes; those disclosures override any stronger inference.

**Terminology used below**

- **Observed UI fact**: directly visible in a supplied screen or its included prototype code.
- **Provisional requirement**: the minimum product behavior needed to make that observed UI meaningful. It is subject to Founder confirmation and engineering feasibility.
- **Unresolved**: not safe to infer from the bundle.

## Product framing shared by the pack

The observed product is a founder-facing, organization-scoped operating console for an agentic runtime. Current browser actions use the founder-authenticated authority baseline; prototype copy cannot silently invent a broader operator/RBAC model. Its recurring design principle is clear separation between:

- read-only status/triage and an action's actual decision surface;
- guidance/visibility and authority/permissions;
- lifecycle status and runtime-effective status;
- configured, validated, and last-known-good state; and
- source records and UI-derived projections.

The bundle does **not** establish a commercial target market, pricing, service levels, nor a committed delivery sequence. The reconciliation inventory below is a truthful shipped-baseline record, not a new roadmap or authorization to build missing prototype behavior.

## Pack-level no-list

- Do not treat sample agents, teams, tasks, PR numbers, token figures, or file paths as production seed data.
- Do not create a per-agent Work Hours on/off switch; the observed prototype explicitly says the switch is org-wide and `On` is derived status.
- Do not make skills grant tools, commands, credentials, or permissions; the screens repeatedly state that skills are guidance only.
- Do not make a failed validation, failed materialization, or rejected proposal silently disappear.
- Do not infer a currency billing model from the Usage screens; they explicitly say tokens are not metered in currency.
- Do not turn prototype-only fields/routes into acceptance criteria without engineering confirmation.

---

# PRD 1 — Runtime onboarding and organization console

| Field | Value |
| --- | --- |
| Status | draft |
| Commitment Boundary | analysis-only |
| Primary user | Founder operating an organization |
| Screen evidence | `a-onboarding.html`, `shell.js`, `a-dashboard.html`, `a-threads.html`, `a-thread-detail.html`, `a-tasks.html`, `a-task-detail.html`, `a-task-detail-fanout.html`, `a-jobs.html`, `a-job-detail.html`, `a-agents.html`, `a-settings.html` |

## Problem

A founder needs to register a conformant agentic CLI, create/select an organization, see what needs a decision, and navigate to the record where a consequential action is resolved.

## Observed UI facts

- Onboarding is a two-step flow: register an agentic CLI, then create the first organization. It depicts registration through a short-lived loopback prompt, a conformance challenge, reported launch command, detected/expired/failed/already-registered states, and organization creation/loading failure states.
- The shared rail has an organization switcher, Home, Threads, Tasks, Agents, Skills, Knowledge, Artifacts, Usage, Dreams, Work Hours, Runtime Health, Audit, Jobs, and Settings. Mobile navigation and light/dark theme behavior are present in `shell.js`.
- Home is a read-only “Waiting on you” inbox plus activity and health/usage summaries. It says decisions occur on the task or thread and names `POST /tasks/{id}/resolve-escalation` for task escalation decisions.
- Tasks display root tasks, roll up child status, show revisit lineage/dependencies, and have a separate fan-out detail showing pre-approval, running, failure/follow-up, and joined states. The fan-out screen says approval is a review job, not a child task.
- Jobs show verbatim commands, request/task linkage, review-required status, and a two-step approve/reject interaction. The job-detail page explicitly says its proposed Thread/routed-via/kind/PR context needs new `JobRecord` fields.
- Settings separates editable org settings from read-only server-declared system settings; it labels organization writes as a deep-merge via `PUT /settings/org`. It says executor detection is read-only and an agent executor is bound at enrollment, not switched in Settings.

## Provisional requirements

1. The product shall provide a founder/operator onboarding path that distinguishes: registration prompt issued, conformance in progress, conformance success, expired prompt, failed conformance, existing registration, organization creation, and organization load failure.
2. A registered executor shall be selectable only after successful conformance; executor registration and agent assignment must remain distinct actions.
3. The console shall expose organization-scoped navigation and preserve the distinction between a summary/triage surface and the record where a decision is performed.
4. Task lists shall show roots only, with child state rolled up without hiding the existence of child work. A task detail shall retain task/thread/job links and revisit/dependency lineage when those records exist.
5. Any fan-out shall show whether it is planned, approval-gated, executing, or joined, and shall make child failure plus a linked follow-up visible.
6. A review-required job shall show the exact command, execution context available to the runtime, approving party, current status, and irreversible/consequential effects before confirmation. Approval/rejection must be explicit and auditable.
7. Settings shall identify, per field, whether it is editable now, live-applied, restart-required, server-declared, or unavailable because a backing route is absent.

## Acceptance signals

- An operator can correctly distinguish “registered executor,” “assigned agent,” “read-only status,” and “action that requires a decision” in usability review.
- A test fixture can traverse each onboarding terminal state without reporting success for an unregistered/failed executor.
- A fan-out fixture preserves a failed child and associated follow-up after the parent joins.

## Deferred gaps — documented missing fields only

1. The displayed Thread/routed-via/kind/PR job context is not backed by the current `JobRecord`. Those fields need a data-model and route extension before the console can claim them as recorded facts.
2. Organization/runtime CRUD and agent-callback operations remain outside the browser cutline; the prototype’s controls do not override their current CLI/TTY or agent-session authority boundaries.

---

# PRD 2 — Work Hours configuration and routine execution

| Field | Value |
| --- | --- |
| Status | draft |
| Commitment Boundary | analysis-only |
| Primary user | Founder/operator configuring when agents wake and what recurring work they dispatch |
| Screen evidence | `a-workhours.html`, `a-workhours-print-ioewwy.html`, `workhours.js`, legacy `a-schedule.html` |

## Problem

The operator needs to understand and safely change when individual agents are eligible to wake, which schedule values win across scope levels, and what routine tasks a wake will generate.

## Observed UI facts

- `workhours.js` explicitly calls itself a “Work-Hours Config UI” and models per-leaf resolution: agent override > team > org default for `mode`, window start/end/timezone, interval, days, and catch-up behavior.
- The overview depicts one global `working_hours.enabled` feature switch, a separate organization eligibility selector, effective cadence/next wake, and a read-only per-agent On indicator derived from both gates. It explicitly says there is no per-agent enable.
- Agent detail shows an effective-schedule reconciliation table, source-of-value highlighting, next wakes, and routine tasks. The code says each routine-task bullet becomes one root task self-dispatched on every wake.
- Routine-task editing is visibly marked “read-only today”; the empty state directs users to agent markdown and labels in-UI editing Phase 2.
- The prototype has validation-before-save, an impact preview before change, save rejection that leaves the previously valid configuration running, a last-known-good degraded banner, and a “feature off” state that pauses scheduled wakes.
- `a-schedule.html` is a simpler legacy view: weekday working hours, a dream window, weekend pause, finish-in-flight work, hold escalations, optional urgent override, and catch-up status. The shared shell comments say the retired Schedule surface is folded into Work Hours.

## Current implementation constraints

Work Hours is Founder-approved and implemented; its executable contract, rather than the prototype JavaScript, is the authority for this area.

1. Configuration is opt-in per organization and resolves leaf-by-leaf as organization default → team default → agent override. Windowed and continuous modes, timezone, interval, active days, and startup catch-up have an implemented validation contract.
2. The organization-level `working_hours.enabled` gate and the configured agent eligibility selector determine whether an agent can wake. There is no independent per-agent enable switch.
3. A wake is a scheduler trigger, not a task, thread, or talk. For a selected agent with a non-empty `## Routine Tasks` section in its agent markdown, each top-level routine item becomes one self-dispatched root task on that agent’s own team. The wake record retains the spawned root-task IDs for provenance.
4. The scheduler records one row per `(agent, local_date, slot)`, limits startup catch-up to the most recent eligible slot, and does not replay all missed slots. Invalid configuration is rejected; a failed wake or callback retains its terminal error rather than silently retrying the same slot.
5. The existing console can edit schedule tiers and eligibility under the founder-authenticated browser baseline, shows effective-value provenance and next wakes, and keeps Routine Tasks read-only. Routine authoring remains in agent markdown; it is not an in-console write feature.

## Current verification signals

- Implemented tests and the current UI expose deterministic effective schedules/provenance, validation errors, eligibility state, next wakes, and the read-only routine list.
- Disabling the global feature prevents wakes without converting the derived status into a per-agent toggle or rewriting schedule records.

## Deferred gaps — only fields not currently backed

1. The current wake list payload does not expose every prototype display field (for example an effective timezone, calendar/timeline projections, schedule-health metrics, or next-run prediction). Do not render those as facts until a backing record/endpoint is added.
2. The legacy prototype’s blackout/holiday, urgent-override, held-escalation, and print/export controls are not present in the current Work Hours contract. They are neither implicit requirements nor open policy decisions for this PRD pack.

---

# PRD 3 — Skills catalog, validation, assignment, and proposal governance

| Field | Value |
| --- | --- |
| Status | draft |
| Commitment Boundary | analysis-only |
| Primary user | Founder/operator; submitter/reviewer where separately authorized |
| Screen evidence | `a-skills.html`, `a-skills-print-xu90ii.html`, `a-skill-detail.html`, `a-skill-detail-bundled.html`, `a-skill-edit.html`, `a-skill-assign.html`, `a-skill-agent.html`, `a-skill-runtime.html`, `a-skill-proposals.html`, `a-skill-proposal-detail.html` |

## Problem

The operator needs to govern reusable guidance given to agents without confusing that guidance with permissions, while retaining a visible trail from authored package through validation, review, publication, assignment, and actual runtime effectiveness.

## Observed UI facts

- Skills are consistently described as guidance only; the screens state they do not grant tools, commands, credentials, or permissions.
- The catalog distinguishes bundled/system-contract and custom skills. Bundled contracts are read-only and apply by predicate; custom skills can be authored/imported and edited.
- Custom skill flow visibly includes draft preservation, technical validation, editable failed drafts, reference resolution, collision/reserved-field checks, dry materialization, assignment, and next-session effectiveness. Assignment is repeatedly distinguished from effectiveness.
- Runtime Validation is explicitly “technical status only,” not an approval queue; it includes validation, materialization, pending, resolved, and predicate events. An earlier effective version can remain in place when a new materialization fails.
- The proposal flow depicts an immutable submitted package with hash/version/evidence, technical validation, a claimed human-review stage, publication, and a separate assignment/effectiveness projection. It says proposal approval is required before publication and a proposal is excluded from catalog/agent sessions before publication.
- Human review screens include a policy/use-case checklist and a rationale field. The screens label the pilot as Founder-only, internal, standard-operational, with two sample use cases; those are prototype examples, not an approved policy.

## Current lifecycle and web-cutline constraints

1. The product shall preserve the guidance-only boundary in data model, copy, and authorization behavior: no skill action may grant runtime permissions.
2. The catalog shall preserve the current lifecycle ledger: source class, stable identifier, version, validation event/result, scoped assignment, and per-agent materialization/effectiveness projection.
3. Validation failure shall retain the saved custom draft and a concrete failure reason; validation is technical evidence, not human approval.
4. A package that is assigned but whose latest materialization does not match the current version shall be shown as `assigned_not_yet_effective`, not effective.
5. The web cutline is source-gated: system-contract skills are read-only and context-applied; managed/bundled skills remain platform-managed; only validated user-authored skills use the bounded per-agent assignment path.
6. The Runtime Validation and lifecycle-status surfaces are read-only evidence/projections. They do not constitute an approval queue or grant a skill permissions, commands, tools, or credentials.

## Current verification signals

- The existing ledger distinguishes saved custom catalog state, validation result, assignment, `assigned_not_yet_effective`, and effective materialization by version; the status endpoint exposes this as a read-only projection.
- Source gates prevent system contracts from being edited/unassigned through the web, and assignment rejects a user-authored skill whose current version has not passed validation.
- Skill content remains guidance-only and cannot expand platform permissions.

## Deferred gaps — only functionality not in the current ledger or web cutline

1. The current web now includes a founder-only proposal queue/detail and the bounded review actions claim, validate, submit for review, approve, and reject. It is a real lifecycle baseline, not prototype-only evidence. The detail deliberately excludes publish, assign, and rollback controls; those remain governed by their canonical lifecycle routes and are not implied by the prototype.
2. Agent proposal submission is narrowly server-gated: only verified active sessions in the current agent-id × canonical-skill pilot may submit, and agents cannot view or mutate the human review workflow. System contracts and platform-managed skills remain read-only.
3. A richer rollback/history view and additional materialization/budget projections require backing records beyond the current validation and latest-materialization evidence.

---

# PRD 4 — Operational evidence, observability, and assistant

| Field | Value |
| --- | --- |
| Status | draft |
| Commitment Boundary | analysis-only |
| Primary user | Founder/operator monitoring runtime work and deciding escalations |
| Screen evidence | `a-artifacts.html`, `a-artifact-detail.html`, `a-audit.html`, `a-spend.html`, `a-health.html`, `a-dreams.html`, `a-dream-detail.html`, `a-knowledge.html`, `a-knowledge-detail.html`, `a-assistant.html`, assistant dock in `shell.js` |

## Problem

The operator needs credible, source-linked evidence about what the organization did, what needs attention, what the runtime is consuming, and which knowledge candidates require a human decision — without a dashboard fabricating status or presenting an assistant answer as a direct action.

## Observed UI facts

- Artifacts list documents/patches/data with folders derived from artifact name paths; the artifact detail says v1 tracks provenance only, not checks/diffs/review history, and directs the user to the repo for contents.
- Audit is labeled append-only and lists actor, action, time, operational context, and event type counts. Knowledge candidates can originate from Dreams and are accepted/edited/dismissed before joining the library.
- Usage shows fresh input/output/reasoning totals separate from cache reads/creates and explicitly says no currency is metered. It groups usage by organization, thread, agent, and model in different views.
- Runtime Health depicts periodic metrics polling and route/queue/loop/session status. Dreams depict scheduled reflection, private learning, optional KB candidates, and a reflection thread.
- The assistant/dock displays conversation history, streamed tool-call evidence, source IDs, executor posture, and navigation chips. It explicitly says tool calls do not pause for approval and says an in-dock approve/deny gate would need a new write path.

## Provisional requirements

1. All operational summaries shall be backed by identifiable stored records or metrics. When a card is a projection/estimate or data is unavailable, the UI shall say so.
2. Artifact records shall expose the provenance fields actually retained, and link to the authoritative external/repository content when the console lacks checks, diffs, or review history.
3. Audit history shall be append-only; corrective events must add a new linked record rather than mutate prior event meaning.
4. Usage shall distinguish fresh token categories from cache activity and shall not present currency/cost unless a real metering source exists.
5. Dream and knowledge flows shall retain origin/proposer/status and require the authorized review action before a candidate becomes shared knowledge.
6. Assistant responses that cite runtime facts shall show enough tool/source evidence for an operator to verify the answer. The browser has no direct canonical assistant approval/denial route: founder-authenticated task, job, thread, and configuration actions remain the governance surfaces. Assistant tool activity remains subject to the selected executor/runtime policy and cannot substitute for a separately designed, auditable governance-write contract.

## Acceptance signals

- Every shown count, status, and summary in a representative dashboard fixture can be traced to a record/metric source or is labeled unavailable/derived.
- Cache activity cannot inflate “total tokens” unless the product definition explicitly changes and tests are updated.
- An audit correction creates a new record rather than modifying the original event.
- A user cannot approve a job or escalation solely through assistant presentation; the action remains on its founder-authenticated canonical surface.

## Deferred gap — assistant authority

The assistant currently has no canonical browser routed-action/approval write path. The sole Founder decision left by this pack is whether to retain the current founder-authenticated governance surfaces and executor-bounded assistant posture, or authorize a separately designed, auditable assistant governance-write scope. Retention/privacy, metric, and Dream behavior remain governed by their existing runtime contracts; prototype labels do not reopen them here.

---

## Cross-cutting data and provenance ledger

| Domain | Observed source of truth / fact | Requirement implication | Open implementation question |
| --- | --- | --- | --- |
| Organization settings | Screen names `OrgSettings`, `PUT /settings/org`, and deep merge | Persist field ownership, live/restart state, and change audit | Version/concurrency/authorization contract |
| Tasks/fan-out/jobs | Task/thread/job IDs and rolled-up or linked views | Keep root/child/dependency/job linkage and decision audit | Whether required back-links exist on `JobRecord` |
| Work Hours | Implemented org config, scheduler/runner, wake store, and founder-authenticated UI | Preserve resolved configuration, wake, provenance, and read-only routine-authoring constraints | Prototype-only display fields absent from the wake payload |
| Skills | Existing catalog, validation-event, assignment, and materialization ledger | Preserve source gates and distinguish validation, assignment, and effectiveness | Proposal/review/publication fields are not yet persisted |
| Audit/knowledge/dreams | Append-only audit; candidates with origin/proposer | Preserve provenance and non-destructive corrections | Retention and approval ownership |
| Usage/health | Metrics and aggregation labels | Show metric definition/window/source; distinguish unavailable | Stable production endpoints and data freshness |
| Assistant | Tool call cards and stated non-approval posture | Cite sources and route actions to founder-authenticated canonical surfaces | No assistant routed-action/approval write path |

## Founder decision ledger

The reviewed runtime establishes the following baseline and prevents this reconstruction from reopening shipped contracts:

1. Work Hours is implemented with its resolved three-tier configuration, organization gate/eligibility selection, wake-to-root-task behavior, and read-only routine authoring constraint.
2. Skills already have a lifecycle ledger and source-gated web cutline; the prototype’s proposal-governance UI is not an implicit additional state machine.
3. Browser actions use the founder-authenticated authority baseline. A prototype cannot imply generic operator roles, delegated approval, or web mutations outside existing routes.

**Founder decision required:** Should the assistant remain read-oriented with founder-authenticated navigation/handoffs (current state), or should a separately designed and auditable assistant write-authority scope be authorized? No other Founder decision is required merely to restate the current contracts.

## Engineering consultation status

The Engineering Manager’s factual contract corrections have been incorporated: Work Hours is shipped with current constraints; Skills has an existing lifecycle ledger and source-gated read-only web boundary; and founder-authenticated actions are the current authority baseline. This pack now defers only unsupported prototype fields, lifecycle actions beyond the bounded proposal-review slice, and the assistant’s missing governance-write authority.

TASK-4325 extends that correction to the merged web implementation rather than treating `screens.zip` as the surface inventory. The source audit found two important additions to the former statement: the founder-only proposal review slice is now shipped, and the Todos surface is a separate, persisted schedule-management workflow. The detailed inventory and exact cutlines follow; they are the controlling record where the earlier prototype language conflicts.

## Full current web implementation reconciliation — shipped baseline and exclusions

**Audit basis.** This is a source-grounded inventory of the current merged SPA (`web/src/routes.tsx`), every route-mounted feature family, its `web/src/lib/api`/hook contract, and the corresponding `runtime/daemon/routes` contract. “Shipped baseline” means the route/component and backing browser contract exist in the current implementation; it does **not** say the prototype made a new product commitment. “Excluded” means intentionally outside this PRD pack and names the owner rather than hiding the gap.

| Route or live component family | Supporting product/API contract | Disposition in this pack |
| --- | --- | --- |
| `/`, `/onboarding`, organization switcher and Add Organization dialog | `/auth/bootstrap` is localhost-only; organization list/create and executor-registration-token flows are bearer-authenticated. Executor registration and agent enrollment are distinct. | **Shipped baseline** in PRD 1. The onboarding requirement remains bounded by the real registration/organization contracts, not prototype labels. |
| App shell: Sidebar, AppBar, responsive layout, command palette, help drawer, theme/density, global shortcuts, error boundary | Org-scoped routing, dashboard summary for shell context, and browser API client auth; no independent product record is created by chrome. | **Shipped baseline** as PRD 1 navigation/triage infrastructure. Reusable visual primitives and layout-only components are **excluded** from feature scope; Frontend Engineering owns their implementation quality. |
| `/orgs/:slug/dashboard` | Read-only dashboard summary, task/escalation and token projections. | **Shipped baseline** in PRDs 1 and 4; displayed summaries must remain traceable to records/metrics. |
| `/orgs/:slug/threads` and `threads/:thread_id` | Thread list/detail/messages/SSE tail plus founder-authenticated compose, send, invite, remove, extend, archive, resume, and abort-replies routes. | **Shipped baseline** in PRD 1. The UI is a record/action surface, not an inferred approval model. |
| `/orgs/:slug/tasks` and `tasks/:task_id` | Root-task/list/detail/recall/events/attachments plus founder-authenticated create, resolve-escalation, revisit, and cancel routes; jobs are linked where recorded. | **Shipped baseline** in PRD 1. Root/child lineage, fan-out state, and canonical action provenance remain required. |
| `/orgs/:slug/todos` and `todos/:scheduleId` | Persisted `SCHEDULE-*` records: list/show, pause, cancel, edit; one stored normalized brief spawns its target agent’s root task when fired. The browser does not invent schedules from prose. | **Shipped baseline** in PRD 1’s operating-console scope. This is distinct from Work Hours and replaces the prior omission from the screenshot-derived pack. Scheduling policy/new todo-authoring scope is **excluded**; Founder and Engineering own any change to the scheduling capability. |
| `/orgs/:slug/agents` and `agents/:agent_name`, including roster/detail, enrollment and pending-enrollment components | Agent list/create/init/enrollment approval/rejection, executor/model/repository bindings, teams, and related task/thread/job projections. | **Shipped baseline** in PRD 1 and Settings. No autonomy toggle or new permission model is implied; those are **excluded** pending Founder scope. |
| `/orgs/:slug/jobs` and `jobs/:job_id` | Persisted job list/detail/output/events/tail/wait plus explicit run, reject, and stop routes. | **Shipped baseline** in PRD 1. It must render only retained command/context facts; unsupported Thread/routed-via/kind/PR fields remain a genuine Engineering-owned data-model gap. |
| `/orgs/:slug/work-hours`, `work-hours/:agent`, and `?view=wakes` | Work Hours status/list/detail, settings/teams, next-wakes, agents; org gate + eligibility + leaf-level tier resolution. | **Shipped baseline** in PRD 2. Routine Tasks remain markdown-authored/read-only in the web; unsupported timeline, holiday, urgent-override, print/export, and prototype fields stay excluded under Engineering ownership. |
| `/orgs/:slug/schedule` | Compatibility redirect to Work Hours Wakes. No separate live Schedule surface exists. | **Shipped compatibility baseline** in PRD 2; the retired route is not a new workflow or scope commitment. |
| `/orgs/:slug/skills`, `skills/new`, `skills/validation`, `skills/:skillId`, and `skills/:skillId/edit` | Catalog/lifecycle/status/event/validation/assignment API with source gates, versioned materialization, and guidance-only semantics. | **Shipped baseline** in PRD 3. System-contract and platform-managed skills are read-only; user-authored custom skills follow the bounded lifecycle. |
| `/orgs/:slug/skills/proposals` and `skills/proposals/:versionId` | Founder-only proposal queue/detail; bounded claim, validate, submit-review, approve, and reject actions. Agent submission uses a verified session and narrow pilot policy; proposal data is withheld from non-founder sessions. | **Shipped baseline** in PRD 3. Publish, assign, and rollback controls are deliberately absent from this web detail surface; expanding authority requires Founder/Engineering decision, not prototype inference. |
| `/orgs/:slug/kb` and KB compose/detail/candidate components | Search/get/stats plus founder-authenticated create/update/delete/reindex; Dreams candidate accept/dismiss is a separate review transition. | **Shipped baseline** in PRD 4. The browser cannot expose the CLI-only `--as-founder` impersonation delete path; that security boundary is **excluded** and owned by Engineering. |
| `/orgs/:slug/artifacts` | Artifact list/tree/metadata/download and thread-attachment access backed by stored artifact provenance. | **Shipped baseline** in PRD 4. Diffs, review history, and content checks remain external/repository concerns unless persisted; Engineering owns any new record/route. |
| `/orgs/:slug/audit` | Append-only audit query/filter/projection API. | **Shipped baseline** in PRD 4. The UI must add corrective events rather than rewrite event meaning. |
| `/orgs/:slug/usage` and `/spend` redirect | Token usage query and agent context; `/spend` preserves bookmarks by redirecting to Usage. | **Shipped baseline** in PRD 4. No currency/cost claim is authorized without a metering source. |
| `/orgs/:slug/dreams` | Dream status/list/detail and reviewed candidate accept/dismiss routes. | **Shipped baseline** in PRD 4. Reflection, learning, and KB promotion remain separate records and decisions. |
| `/orgs/:slug/health` | Runtime metrics history/health projections. | **Shipped baseline** in PRD 4. Health status is evidence, not a promise of availability or service level. |
| `/orgs/:slug/settings/*`: Assistant, System, Organization, Agents, Executors; executor binary/profile and adapter-management components | Settings/org/team patch routes; assistant init/register/repair; runtime executor profile/binary routes; adapter submission/bind/approve/reject contracts. Server-declared system values remain read-only where no write route exists. | **Shipped baseline** in PRD 1. It does not grant a browser a general runtime-admin or RBAC model. Adapter and executor lifecycle policy beyond exposed contracts is **excluded**; Engineering owns contract changes and Founder owns new authority scope. |
| Global System Assistant dock and A-mode conversation switcher | Bearer-authenticated A-mode status/conversation create/activate/rename/delete plus authenticated WebSocket turns and transparent tool activity. | **Shipped baseline** in PRD 4. Conversation management is not a direct approval surface; any assistant-initiated consequential authority remains bounded by the selected executor/runtime policy. The requested new, auditable assistant governance write-authority scope is still the sole Founder decision in this pack. |
| Not-found route, prototype routes, design-system showcase routes, mock providers, tests, scripts, and static assets | Prototype/design routes are build-gated; mock data and harnesses are non-authoritative developer support. | **Explicitly excluded** from product scope because they are not shipped founder workflows or authoritative runtime records. Frontend Engineering owns them; Product must not turn them into requirements without a new decision. |

### Reconciliation rules for component completeness

Every route-mounted page above includes its directly supporting dialogs, drawers, rows, detail panes, empty/error/loading states, and query/mutation hooks in the same disposition. Shared primitives, patterns, and layouts are covered only as the product-facing shell/reusable interaction infrastructure noted above; they do not create separate user workflows. This prevents a component appearing in the repository from being mislabeled as an uncommitted feature, while preserving a traceable owner for non-product or developer-only code.

### Genuine unresolved gaps after the full audit

1. `JobRecord` still lacks the prototype’s Thread, routed-via, kind, and PR-context fields. Do not render them as recorded facts; Engineering Manager owns any schema/route proposal.
2. Work Hours does not back every prototype display or control (effective timezone/calendar projections, schedule-health metrics, holiday/blackout, urgent override, held escalation, print/export). These are exclusions, not stealth requirements; Engineering Manager owns feasibility if a later product decision asks for them.
3. Proposal review is real but deliberately bounded: founder review exposes only claim/validate/submit-review/approve/reject, while agent submission is a narrow verified-session pilot. Any expansion of participant set, review actions, publication, assignment, rollback, or permission semantics requires an explicit Founder decision and Engineering authorization design.
4. The assistant has live conversations and executor-mediated tool activity, but no separate product-level direct approval/denial route. Its authority must not be inferred from the dock; Founder must decide whether to authorize a bounded, auditable assistant governance-write scope.

## Evidence inventory — all rendered entry points

| Product area | Rendered source files |
| --- | --- |
| Core navigation and operations | `a-dashboard`, `a-threads`, `a-thread-detail`, `a-tasks`, `a-task-detail`, `a-task-detail-fanout`, `a-jobs`, `a-job-detail`, `a-agents`, `a-settings`, `shell.js` |
| Onboarding | `a-onboarding` |
| Work Hours / legacy schedule | `a-workhours`, `a-workhours-print-ioewwy`, `a-schedule`, `workhours.js` |
| Skills | `a-skills`, `a-skills-print-xu90ii`, `a-skill-detail`, `a-skill-detail-bundled`, `a-skill-edit`, `a-skill-assign`, `a-skill-agent`, `a-skill-runtime`, `a-skill-proposals`, `a-skill-proposal-detail` |
| Evidence and observability | `a-artifacts`, `a-artifact-detail`, `a-audit`, `a-spend`, `a-health`, `a-dreams`, `a-dream-detail`, `a-knowledge`, `a-knowledge-detail` |
| Assistant | `a-assistant` and the assistant dock embedded in `shell.js` |

All names above are `.html` except the named JavaScript sources. `ds.css` and `skills.css` were inspected as visual-system support, not used as independent requirement evidence.

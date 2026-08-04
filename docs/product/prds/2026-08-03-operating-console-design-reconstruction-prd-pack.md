# HappyRanch operating-console design reconstruction — PRD pack

| Field | Value |
| --- | --- |
| Status | draft |
| Owner | Product Lead |
| Date | 2026-08-03 |
| Source Links | THR-140; `artifact:thread-draft-20260803T143323Z-screens.zip`; feasibility consultation THR-141 |
| Commitment Boundary | analysis-only — no build, roadmap, or external delivery commitment |
| Founder Decisions | Required: cutline, authority model, and treatment of prototype-only disclosures; Ruled: none in the supplied thread |

## Purpose and evidence boundary

This pack reconstructs the smallest honest set of PRDs implied by the founder-supplied prototype bundle. It is not a claim that every visible control is implemented, approved, or intended for v1.

I downloaded and rendered all 34 HTML entry points in the bundle (31 product screens plus three print/state variants), reviewed their static UI text and interaction code, and used the provided `shell.js` and `workhours.js` as evidence. Screen labels and sample values are illustrative unless this document explicitly calls them out as a requirement. Several screens self-identify as a prototype or as needing new fields/routes; those disclosures override any stronger inference.

**Terminology used below**

- **Observed UI fact**: directly visible in a supplied screen or its included prototype code.
- **Provisional requirement**: the minimum product behavior needed to make that observed UI meaningful. It is subject to Founder confirmation and engineering feasibility.
- **Unresolved**: not safe to infer from the bundle.

## Product framing shared by the pack

The observed product is a founder-facing, organization-scoped operating console for an agentic runtime. Its recurring design principle is clear separation between:

- read-only status/triage and an action's actual decision surface;
- guidance/visibility and authority/permissions;
- lifecycle status and runtime-effective status;
- configured, validated, and last-known-good state; and
- source records and UI-derived projections.

The bundle does **not** establish a commercial target market, pricing, service levels, nor a committed delivery sequence.

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
| Primary user | Founder/operator of an organization |
| Screen evidence | `a-onboarding.html`, `shell.js`, `a-dashboard.html`, `a-threads.html`, `a-thread-detail.html`, `a-tasks.html`, `a-task-detail.html`, `a-task-detail-fanout.html`, `a-jobs.html`, `a-job-detail.html`, `a-agents.html`, `a-settings.html` |

## Problem

An operator needs to register a conformant agentic CLI, create/select an organization, see what needs a decision, and navigate to the record where a consequential action is resolved.

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

## Unresolved decisions / feasibility gates

1. Which onboarding identities, authentication model, token scope, expiry, and loopback trust boundary are approved? The screen alone does not specify them.
2. Which actions are Founder-only versus delegated operator actions, particularly executor enrollment, task revisit, fan-out approval, and system-setting changes?
3. Does the runtime already support the shown task/job links and job-context fields, or must `JobRecord` be extended? Treat the job-detail disclosure as evidence of a gap, not a requirement already met.
4. Is `PUT /settings/org` the approved concurrency/validation contract? Define versioning/conflict handling and authorization before implementation.

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

## Provisional requirements

1. The system shall resolve schedule leaves independently with documented precedence: agent override, then team value, then organization default. The UI shall show the effective value and its source for a selected agent.
2. The system shall support windowed and continuous modes, timezone, interval, active days, and startup catch-up according to a validated schema; exact permitted values remain an engineering decision.
3. Work Hours shall use a single organization-level feature gate and a separate eligibility set. Per-agent status shall be derived, not independently toggled.
4. Before persisting a schedule/eligibility change, the UI shall validate it and show impacted agents/effective outcomes. Invalid changes must not replace the last valid configuration.
5. If live configuration cannot load or validate, the console shall disclose degraded state and the active fallback/last-known-good posture rather than implying new settings are active.
6. The console shall display routine tasks and whether an eligible wake would dispatch work; it must distinguish “no routine tasks” from a disabled/ineligible schedule.
7. Routine-task authoring is out of this v1 cutline unless Founder explicitly approves Phase 2. The UI may link to its established source of truth but must not promise in-console editing.

## Acceptance signals

- Given fixtures with org, team, and agent values, every displayed effective leaf and next wake is deterministic and traceable to its winning source.
- Invalid edits never change the active runtime configuration; the user sees the field-level reason and an intact fallback state.
- Disabling the global feature stops scheduled wakes without rewriting eligibility or per-agent schedule records.

## Unresolved decisions / feasibility gates

1. Confirm the authoritative configuration store and API; the JavaScript is a self-contained prototype, not evidence of a production persistence contract.
2. Confirm whether team membership exists as a stable runtime entity and how agent markdown, routine tasks, and UI configuration reconcile.
3. Decide whether “one routine bullet = one root task” is the approved dispatch contract, including idempotency, missed-wake/catch-up, concurrency, and duplicate prevention.
4. Decide time-zone/DST behavior, blackout/holiday behavior, urgent override authority, and whether held escalations are in v1. The legacy Schedule screen is insufficient to settle these policies.
5. Confirm print/export needs; the print-variant filename demonstrates a prototype variant, not a required output format.

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

## Provisional requirements

1. The product shall preserve the guidance-only boundary in data model, copy, and authorization behavior: no skill action may grant runtime permissions.
2. Each skill/package shall retain source class, stable identifier, version, content/reference integrity evidence, validation result, and provenance sufficient to explain its catalog state.
3. Validation failure shall retain an editable draft and a concrete failure reason; validation must be distinct from human approval.
4. Publication/lifecycle and assignment/effectiveness shall be independently represented. A package that is assigned but not yet materialized must not be presented as effective.
5. System contracts shall be predicate-scoped and read-only within the catalog UI. Custom-skill assignment shall be explicit and agent-scoped.
6. The runtime shall surface materialization failure without silently stripping a previously effective, known-good version; the applicable fallback must be visible.
7. If custom-skill proposals are included in the product cutline, an immutable package must pass technical validation and authorized human review before publication, and reviewer rationale/audit evidence must be retained.

## Acceptance signals

- A reviewer can distinguish draft, validation failed, validated, in review, approved/published, assigned-next-session, effective, and rolled-back states using fixture data.
- A failed validation or materialization yields a visible actionable record and does not result in an untraceable loss of existing effective guidance.
- An attempt to use skill content to expand tools/permissions is rejected by the platform contract and verified in tests.

## Unresolved decisions / feasibility gates

1. Confirm which lifecycle states are authoritative and which are projections; the prototype uses rich state labels but supplies no canonical transition model.
2. Confirm the publisher/reviewer role model, claim lease/timeout, review edit semantics, and whether Founder-only review is intentional or merely pilot copy.
3. Define rollback semantics: unassign, restore predecessor, delayed effect, audit retention, and what counts as a known-safe version.
4. Define technical validation/malware or prompt-injection review boundary; the visible checklist is a review aid, not a complete security policy.
5. Confirm per-agent guidance budgets, version materialization timing, and the runtime APIs needed for the effective/pending/fallback projections.

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
6. Assistant responses that cite runtime facts shall show enough tool/source evidence for an operator to verify the answer. Assistant-proposed actions must route to the existing canonical approval/decision surfaces until a separately approved write path exists.

## Acceptance signals

- Every shown count, status, and summary in a representative dashboard fixture can be traced to a record/metric source or is labeled unavailable/derived.
- Cache activity cannot inflate “total tokens” unless the product definition explicitly changes and tests are updated.
- An audit correction creates a new record rather than modifying the original event.
- A user cannot approve a job or escalation solely through assistant presentation if no authorized write route is implemented.

## Unresolved decisions / feasibility gates

1. Define event-retention, export, access-control, and privacy policy for audit/artifact/assistant traces.
2. Confirm which metrics endpoints and aggregation windows are production-supported; the Health screen names prototype endpoints but is not API documentation.
3. Decide whether Dreams are a product feature in this cutline, their scheduling/resource policy, and whether private learning vs shared KB requires explicit owner approval.
4. Decide assistant memory, conversation retention, tool transparency level, and write-authority scope before treating the dock as more than a read-oriented assistant UX.

---

## Cross-cutting data and provenance ledger

| Domain | Observed source of truth / fact | Requirement implication | Open implementation question |
| --- | --- | --- | --- |
| Organization settings | Screen names `OrgSettings`, `PUT /settings/org`, and deep merge | Persist field ownership, live/restart state, and change audit | Version/concurrency/authorization contract |
| Tasks/fan-out/jobs | Task/thread/job IDs and rolled-up or linked views | Keep root/child/dependency/job linkage and decision audit | Whether required back-links exist on `JobRecord` |
| Work Hours | Prototype JS objects and computed resolution | Store scoped values plus effective projection and last-known-good result | Canonical config store and scheduler API |
| Skills | Package/hash/version, validation, lifecycle and projection UI | Separate immutable package/lifecycle records from assignment/materialization records | Canonical state machine and reviewer authorization |
| Audit/knowledge/dreams | Append-only audit; candidates with origin/proposer | Preserve provenance and non-destructive corrections | Retention and approval ownership |
| Usage/health | Metrics and aggregation labels | Show metric definition/window/source; distinguish unavailable | Stable production endpoints and data freshness |
| Assistant | Tool call cards and stated non-approval posture | Cite sources and route actions to canonical surfaces | Conversation/tool authorization and retention |

## Founder decision ledger

These choices are required before an implementation-ready PRD can be issued. They are consolidated to prevent accidental scope expansion.

1. **Cutline:** Is this a single operating-console release, or should work be sequenced into (a) core console/onboarding, (b) Work Hours, (c) Skills governance, and (d) evidence/assistant? Recommendation: sequence them; Work Hours and Skills each change runtime behavior and should not be hidden inside a UI-refresh release.
2. **Authority:** Which concrete user roles can enroll executors, create/edit organizations, approve/reject jobs, resolve escalations, approve fan-out, publish/review skills, and alter schedules?
3. **Skills:** Is custom-skill proposal review Founder-only pilot behavior, or a durable authorization model? Are the sample use cases/policy classes approved beyond the prototype?
4. **Work Hours:** Is the three-tier schedule plus global eligibility gate the product policy to lock, and are routine-task authoring and escalation-hold/urgent override in or out of the first cutline?
5. **Assistant:** Should the initial assistant remain read-oriented with canonical navigation/approval handoffs, as the screens say, or is a separate write-authority design desired?
6. **Evidence:** What retention, export, privacy, and visibility rules govern audit, artifacts, assistant tool traces, usage, and dreams?

## Engineering consultation status

Material feasibility/data-model/API questions were sent to the Engineering Manager in dedicated coordination thread **THR-141** on 2026-08-03. No response was available during this reconstruction session. None of the above unresolved items should be treated as engineering-confirmed until that thread records a response.

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

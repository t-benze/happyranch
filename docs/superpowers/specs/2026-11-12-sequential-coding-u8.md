# Sequential Coding U8: common Workflows journey and task integration

> Status: current design; U8a (read/journey half) delivered in source by TASK-15582. U8b (Setup editing/Save custody UI and the Settings → Coding redirect with original-save recovery) and U9 (public selector/fresh-start cutover) remain. Public fresh start stays OFF.
> Current Source: docs/agent-guides/web-and-cli.md § U8a; web/ARCHITECTURE.md § Sequential Workflows (U8a)

THR-336 / ROOT TASK-15165. Plan rev3 TASK-14461 §U8 as re-scoped by R-M2-1
("U8 unchanged … three mandatory design corrections as locked requirements; no
readiness/duty-access panel"). Product authority: PRD v1.0.4 Shared Artifact
`product_lead-2026-11-11-workflow-mechanism-prd-v1.0.4.md`, SHA256
`96925395de4e44cc5c1570deb512ec843fcfd72c0b3eaeff0423c00b58a07bb5`, and Founder
THR-336 msg131 ACCESS-1. Source delivery is not deployment or acceptance.

## U8a split, declared before edits

U8a is the whole read/journey half in one PR; no smaller split was needed.
Frontend only: no backend, daemon route, schema or OpenAPI change. The UI uses
only the six merged U7a GETs through the existing provider hooks.

## Routes and views

All routes are under `/orgs/:slug` and are read-only. The Sidebar adds
**Workflows** beside Reviews; the AppBar title follows it.

| Route | View |
| --- | --- |
| `workflows` | Definitions: every stored definition version with its description, role/node counts and an Inspect link |
| `workflows/runs` | Runs: state, counters and real source/active task links (the U7 run list has no node field, so no node name is shown) |
| `workflows/definitions/:definitionId` | Exact stored version: pins/digests, definition-ordered nodes with role, responsibilities, inputs, output, checks and outcome edges; roles, limits, run inputs, terminal conditions |
| `workflows/runs/:runId` | Run detail: admitted-version link, settings generation, counters, source/carrier task links, run state, request/inputs, fixed assignments, process, recorded assessments and finalization |

A run's definition link always opens `definitions/{run.definition_id}`, its
admitted version. The independent document `/reviews` pages and all fixed-history
readers are unchanged.

## Recorded facts only

Node layout, roles, responsibilities and outcomes come from the admitted
definition text. Visits are grouped under their node in ordinal order with their
real task links; a node without a visit says **Task not created yet**. A
completed run says there is no active node. Requests, inputs, findings and
outputs are agent-authored text shown verbatim. Principals are permanent IDs.
Unknown state/outcome/cause tokens are shown verbatim, never reinterpreted.

The three locked design corrections:

1. **Exhaustion.** A held run whose held, selected visit recorded an outcome that
   the admitted definition maps to a counter-consuming return, with no later
   visit and counters at the effective limit (the lower of definition and run
   original limits — the sequential reducer's own predicate), is shown as
   "revision allowance exhausted" and its return target says **No further
   {role} task was created**. A successor is described as created only when its
   visit/task exists. The transition reason itself is not in the U7 projection;
   any other held run without an observation hold says the cause is not included
   in this view.
2. **Assessments.** Every visit keeps its recorded outcome and findings: an
   approval stays Approved for its content revision, QA Revise stays Revise with
   its findings and is never relabelled PASS. "Recorded assessments and
   finalization" lists the latest recorded outcome per assessment node with its
   visit/revision; a queued/running/waiting finalization visit is a separate
   "final rechecks pending" fact that changes no assessment.
3. **Reviewer.** The reviewer binding is labelled as this run's own selected
   independent reviewer. The organization's legacy `reviewer_agents` list appears
   only as a separate authority-dependency note. No U7 GET returns that list and
   the setting has no browser control, so no value or link is shown and it is
   never the source of the assignment.

The task-detail banner (`shared/workflows/SequentialTaskBanner`) reads the task
link and the same run detail/definition, and renders the same headline through
the shared presentation, separately from the task's own status.

## Access, view-only and late responses

U7 GETs expose no configure permission and U8a renders no configuration or run
control (no Start, Retry, Reassign, Merge again, Cancel, repository selector).
Every permitted view is therefore view-only and says so. A 403/404 refusal shows
a fixed denied message on the page or banner and discloses no run id, task
link, request, output or finding; the refused query's cache entry is blanked
before the error is published. Query keys include org and selection, pages are
keyed by org/selection, reads abort and are evicted on unmount, and data is hidden
while refreshing or after an error (unchanged U7 semantics), so a late response
for another org/selection cannot render.

PRD F6 asks a refresh failure to keep the last-loaded state with a freshness
warning; the merged U7 hook hides data on refresh/error. U8a keeps the U7
semantics and records this as an open manager decision.

## Copy

All new app copy ships in the typed en and zh-CN catalogs (`seqwf.*`,
`shell.nav.workflows`, `shell.title.workflows`). Chinese uses **执行轮次** for a
node visit, distinct from 版本 (definition version) and 内容修订 (content revision).

## Evidence limits

Fixture-backed screenshots (existing screenshot-harness createServer + mock GETs)
establish rendered UI behavior only, not V3 persistence/progression. Python units
and web Vitest are retired; no test was added. Required automated checks remain
Source Boundary Lint and Web Lint (Node 24).

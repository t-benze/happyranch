# Sequential Coding U8: common Workflows journey and task integration

> Status: current design; U8a (read/journey half) delivered in source by TASK-15582 and corrected by TASK-15587 (TASK-15583 findings: access fencing, single-snapshot banner, R-M3-7 exhaustion, PRD F6 freshness) and by TASK-15595 (TASK-15588 finding: nested admitted-definition read states). U8b (Setup editing/Save custody UI and the Settings → Coding redirect with original-save recovery) and U9 (public selector/fresh-start cutover) remain. Public fresh start stays OFF.
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

1. **Exhaustion.** Derived from the R-M3-7 RunDetail facts `effective_limits`
   (L: the reducer's definition ∩ original settings ∩ admitted StartCommand
   lower-limits intersection) and `reducer_counters` (R: the last transition's
   `after`), never from the settings-snapshot `limits` or the database event
   count. A held run's held, selected visit V with a recorded outcome that the
   admitted definition maps to a `next` edge, no later visit and no observation
   hold is exhausted when the reducer's limit check holds on its pre-transition
   counters (R with one event fewer): `visits ≥ L.max_visits`, `events+1 ≥
   L.max_events`, or for a counter-consuming return `returns ≥ L.content_returns`
   or `epoch ≥ L.max_content_epochs`; or R was already outside L. A return whose
   epoch advanced (R.epoch > V.epoch) was a required-input hold instead. A return
   ceiling shows "revision allowance exhausted"; a visit/event ceiling names that
   limit. The target node says **No further {role} task was created** and the
   state section shows the effective allowance with the token `limit_exhausted`
   labelled as derived, because `Decision.reason` is not stored. The reducer
   evaluates settlement/output-evidence holds before limits with the same counter
   shape, so such a hold coinciding with a satisfied limit check cannot be told
   apart (stated limitation). A successor is described as created only when its
   visit/task exists. Any other held run without an observation hold says the
   cause is not included in this view.
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

The task-detail banner (`shared/workflows/SequentialTaskBanner`) uses the task
link only to select the run id; its state badge, relationship, version link and
headline all render from ONE accepted run-detail snapshot through the shared
presentation, separately from the task's own status. Before that snapshot is
accepted it shows a single loading line.

## Access, view-only and late responses

U7 GETs expose no configure permission and U8a renders no configuration or run
control (no Start, Retry, Reassign, Merge again, Cancel, repository selector).
Every permitted view is therefore view-only and says so. A 403/404 refusal on
ANY sequential read (AC11) bumps a per-QueryClient, per-org admission generation,
aborts every in-flight sequential read of that org, and blanks every cached
`workflow-sequential` entry of the org — including the task-link read that holds
the run, definition pin and source/carrier/visit links. Sibling entries carry a
fixed access-fence refusal; a read started under an older generation throws
instead of caching its late success. Nothing is shown again until a read started
after the refusal succeeds (Refresh, remount or focus refetch). The page or banner
shows the fixed denied message and discloses no run id, task link, request,
output or finding. Query keys include org and selection; reads abort and are
evicted on unmount, so a late response for another org/selection cannot render.

PRD v1.0.4 F6: a network refresh failure (a transport failure with no HTTP
status) keeps the last state loaded in this mount visible with a freshness
warning and a Refresh button; background refetches no longer hide loaded data.
Other HTTP failures keep the error view. The warning is visibly distinct from the
denied message, and a refusal still evicts and gates strictly as above.

This applies to every read a view depends on, including the nested admitted-definition
read that run detail and the task banner use for the process, the next-step/exhaustion
explanation and the assessments (TASK-15595). When only the definition refresh fails
on transport while the run and task-link reads succeed, the retained definition and
everything derived from it stay visible with a freshness warning; its Refresh
refetches the failing definition read. When the definition was never loaded in this
mount, fails with a non-refusal HTTP error, or cannot be shown structurally, run
detail states that the original admitted definition is unavailable (with the raw
diagnostic and Refresh) instead of silently dropping the process and assessments,
and the banner states that the next step and any limit explanation cannot be shown.
A 403/404 on the definition read fences the org exactly as any other sequential read.

## Copy

All new app copy ships in the typed en and zh-CN catalogs (`seqwf.*`,
`shell.nav.workflows`, `shell.title.workflows`). Chinese uses **执行轮次** for a
node visit, distinct from 版本 (definition version) and 内容修订 (content revision).

## Evidence limits

Fixture-backed screenshots (existing screenshot-harness createServer + mock GETs)
establish rendered UI behavior only, not V3 persistence/progression. Python units
and web Vitest are retired; no test was added. Required automated checks remain
Source Boundary Lint and Web Lint (Node 24).

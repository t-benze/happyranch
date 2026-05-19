# Web App — Complete Feature Set (Umbrella Spec)

**Date:** 2026-05-18
**Status:** Draft, pending implementation plans.
**Replaces:** nothing — extends `docs/superpowers/specs/2026-05-14-web-ui-design.md`.
**Relates to:** `protocol/05e-dashboard.md` (the Live Status / scorecards source-of-truth), `web/UI_SPEC.md` (per-screen UX sketches), `web/ARCHITECTURE.md` (layer rules), CLAUDE.md "Open" item 14 (founder dashboard).

## 1. Goal

Define what "complete" means for the founder console web app, lock the sequencing, and fix the cross-cutting decisions so each follow-up PR extends a known boundary instead of inventing one. This is an umbrella spec: it does **not** contain per-screen implementation detail. Each surface gets its own detail spec at the time its PR starts.

The threads feature already shipped (`2026-05-14-web-ui-design.md`). Every daemon route already has a 1:1 TS mirror in `web/src/lib/api/` with a contract test (`tests/contract/test_openapi_snapshot.py` + `web/src/test/openapi-coverage.test.ts`) that fails the build if a new route is added without either a TS function or an explicit EXCLUDED entry. The remaining work is UI on top of those already-mirrored routes.

## 2. Non-goals

- **Mobile / responsive layouts.** Desktop founder workstation only.
- **Multi-user / RBAC / login.** Localhost-only, single founder, single Mac Mini.
- **Auto-generated TS types from Pydantic.** Hand-mirrored types + the OpenAPI snapshot stays the rule.
- **Orgs / runtime CRUD in the browser.** `grassland init`, `grassland orgs init`, `grassland orgs delete`, `grassland migrate-to-multi-org` stay TTY-gated through the CLI. The web shows orgs; never mutates them.
- **Agent-callback endpoints in the browser.** `report-completion`, `manage-agent`, `manage-repo`, `dispatch` (agent variant), `learning add/update/promote`, thread `/reply` `/decline` `/dispatch` `/close-out` are agent-subprocess-only and remain absent from `lib/api/`.
- **`--as-founder` impersonation surface for KB deletes.** TTY-gated through the CLI.
- **Notifications inside the web app.** Feishu owns the founder-push channel.
- **Charts library beyond simple sparklines/bars.** Chart.js or recharts is deferred until Dashboard PR proves it's needed; HTML/CSS bars are the first attempt.
- **Bulk-edit operations** on any surface.
- **Saved filters / per-user view preferences** beyond density + theme.

## 3. Inventory

### 3.1 Shipped

- Threads: read + write + 5 dialogs + keyboard + 2 SSE streams.
- App shell: TopBar, OrgSwitcher (read-only), Statusbar shell, nav row (Threads enabled).
- `lib/api/*` for every browser-facing daemon route. Contract test enforces coverage.
- Design system: 9 primitives, 13 patterns, AppShell + ThreadsLayout.
- Empty placeholder routes for Tasks / KB / Audit / Agents (PR 6 lite).

### 3.2 Remaining feature surfaces

| # | Surface | Daemon endpoints (existing) | SSE in v1? | Detail-spec status |
|---|---|---|---|---|
| 1 | Tasks | list / detail / recall / events / cancel / revisit / resolve-escalation | yes — `/tasks/events` (inbox) and `/tasks/{id}/events` (tail) | sketch in `web/UI_SPEC.md` §8 |
| 2 | Agents | list / pending / approve / reject / repos / learnings (read) / backfill | no | sketch in `web/UI_SPEC.md` §11 |
| 3 | Audit | `/audit` | no | sketch in `web/UI_SPEC.md` §10 |
| 4 | KB | list / search / read / write / reindex / delete | no | sketch in `web/UI_SPEC.md` §9 |
| 5 | Talks | list / detail / start / resume / abandon / end / dispatch | no | not yet sketched |
| 6 | Dashboard (Live Status) | composes Tasks + Agents + Audit + escalation queue | piggybacks on `/tasks/events` | source-of-truth: `protocol/05e-dashboard.md` Page 1 |
| 7 | Polish | density, theme, Cmd-K palette, jump-keys, HelpDrawer aggregation, a11y sweep | n/a | partial in `web/UI_SPEC.md` §12 |

## 4. Sequencing

Build in this order. The rationale per slot is in §4.1.

| PR | Feature | New patterns introduced |
|---|---|---|
| 7 | Tasks | `TaskCard`, `FilterSidebar`, `Drawer` primitive, `useTaskEventsSSE`, `useTaskTailSSE`, recall-tree renderer, density-toggle hook |
| 8 | Agents | Scorecard table, calibration table, agent detail Drawer (reuses TaskCard for history), Pending tab with approve/reject |
| 9 | Audit | `SubTabBar`, dense-row mode, expandable-row pattern, trace-tree renderer, cross-surface deep links |
| 10 | KB | Tag/type-filter pills (reuses `FilterSidebar`), markdown reader (reuses threads renderer), source-task badges that link to Tasks |
| 11 | Talks | `TalkTranscript` (reuses `MessageBubble`), start-talk dialog, lifecycle buttons (end / abandon / dispatch) |
| 12 | Dashboard | `DashboardLayout`, "pending your action" queue, active-tasks-by-team card, blocked-tasks card |
| 13 | Polish | Density wired everywhere, theme toggle lit, Cmd-K command palette, jump-keys `g t / g k / g a / g g / g l` finalized, HelpDrawer tabbed by feature, a11y sweep |

### 4.1 Why this order

- **Tasks first** rather than Dashboard. Dashboard is a synthesis screen — every card on it is a thin projection of an underlying feature page. Building it against placeholder data wastes effort that gets thrown away when the source feature lands. Tasks also forces the two SSE patterns (inbox events + per-entity tail) and the filter-sidebar layout shared by Audit/KB.
- **Agents second.** Builds on Tasks (TaskCard reused in agent history Drawer). First real use of `TierBadge`. Introduces Drawer-not-dialog interaction. Pending-enrollments tab covers approve/reject.
- **Audit third.** Tasks + Agents both deep-link out of Audit rows. Three sub-tabs lock the nested-route pattern.
- **KB fourth.** Read-only first; reuses the sidebar layout. Markdown renderer already exists from threads.
- **Talks fifth.** Smallest unique surface, no SSE in v1. Adds founder↔agent transcript pattern.
- **Dashboard sixth (LAST among source-bearing surfaces).** Every card is now a thin projection over a `lib/api/` module we've already proven. ~30 lines per card.
- **Polish seventh.** Density toggle is wired through earlier features as they ship (the hook lands in PR 7); PR 13 is the audit + theme/palette/jump-key finalization.

## 5. Cross-cutting decisions

These are the choices most likely to thrash if each PR decides independently. Lock them here.

### 5.1 Drawer vs. dialog

- **Drawer** for "look at a detail in context": Agent history, Task recall, Audit row expansion. Slides in from the right edge, 480px wide, does NOT block the rest of the page. Add a single `Drawer` primitive in PR 7 alongside Tasks.
- **Dialog** for "give me input then act": NewThread, Invite, Compose KB entry. Modal, centered, scrim backdrop. Existing pattern from threads.

### 5.2 Density toggle

Affects: InboxRow, MessageList row gap, TaskCard row, AuditRow, AgentScorecardRow. Does **not** affect markdown body text (we never make message bodies less legible).

Implementation: one `useDensity()` hook landing in PR 7 (Tasks introduces the first dense table). Stored in `localStorage["grassland.density"]`. Comfortable (default) ↔ Compact. Subsequent features adopt the hook for free.

### 5.3 Theme toggle

Tokens already ship both palettes. Toggle is rendered as part of the TopBar today; the actual swap (`data-theme="light|dark"` on `<html>`) lights up in PR 13. Earlier PRs ship dark-only and do not gate on theme.

### 5.4 Jump-keys

Each feature registers its own `g <letter>` via a `useGlobalJump('t', '/orgs/:slug/tasks')` hook landed in PR 7. PR 13 audits the final map and renders it in the HelpDrawer. Final map:

| Combo | Target |
|---|---|
| `g i` | Threads inbox |
| `g t` | Tasks |
| `g k` | KB |
| `g a` | Audit |
| `g g` | Agents |
| `g l` | Talks |

1.0s buffer for multi-key combos. Suppressed when focus is inside an input, textarea, or `[contenteditable]`.

### 5.5 SSE budget

Cap at 4 concurrent streams. Statusbar already shows the count.

- Threads inbox + Threads tail = 2 (existing).
- Tasks inbox + Tasks tail = +2 (PR 7).
- Audit / KB / Agents / Talks **poll** (60s `staleTime`) in v1.
- Dashboard piggybacks on `/tasks/events` (no new stream).

### 5.6 KB write surface

Browse-only through PR 10. A "Compose KB entry" dialog may land in PR 10 if the PR has slack, behind a feature flag. Until that dialog ships and is signed off, founder rulings flow through `grassland kb add`. Edits and deletes stay CLI-only in v1.

### 5.7 Agent enrollment approval

Lives in PR 8 (Agents), not a separate surface. A "Pending" tab on the Agents page lists `GET /agents/enrollments`. Approve / Reject buttons hit `POST /agents/{name}/approve` and `POST /agents/{name}/reject`. The Drawer for an active agent shows recent tasks + learnings (read-only); learning writes stay CLI-only because they're agent-callbacks.

### 5.8 Orgs / Runtime CRUD

Stays CLI/TTY-gated. The web reads `GET /api/v1/orgs` and `GET /api/v1/runtime`; never mutates either. The TopBar OrgSwitcher remains read-only.

## 6. Per-surface "what's IN" (locking list)

Each surface's detail spec extends this. Anything not on the list is **not** in v1 of that PR.

### 6.1 Tasks (PR 7)

- Inbox list with status / team / agent filters.
- Detail pane with recall tree + events stream.
- Lifecycle actions: cancel, revisit, resolve-escalation.
- Cross-link: any `TASK-NNN` reference site-wide opens the task detail.
- Density toggle wired (introduces it).
- Drawer primitive lands here.

### 6.2 Agents (PR 8)

- Scorecards table (30-day rolling): tier, acceptance, revision, errors.
- Calibration table below.
- Agent detail Drawer: recent tasks (TaskCard list) + learnings list (read-only) + metadata.
- "Pending" tab: list pending enrollments, approve / reject.

### 6.3 Audit (PR 9)

- Three sub-tabs: Activity, Escalations, Traces.
- Filter sidebar: agent / type / date.
- Expandable rows with full audit payload.
- Deep links from Tasks / Agents / Threads into a pre-filtered Audit view.

### 6.4 KB (PR 10)

- Search + tag / type sidebar.
- Entry detail with markdown body.
- Source-task badges link to Tasks detail.
- Read-only. Compose-entry dialog optional behind a feature flag; edits and deletes are CLI-only.

### 6.5 Talks (PR 11)

- List + detail (transcript view).
- Start-talk dialog.
- Lifecycle buttons: end, abandon, dispatch.
- No SSE; polls every 60s on the detail view.

### 6.6 Dashboard / Live Status (PR 12)

- System health card.
- "Pending your action" queue (escalations awaiting founder).
- Active tasks by team.
- Blocked tasks.
- Single screen, 30s refresh, no history pages (those live in Audit).

### 6.7 Polish (PR 13)

- Density toggle audited across every surface.
- Theme toggle lit.
- Cmd-K command palette: fuzzy over orgs + threads + tasks + agents + KB entries.
- Jump-keys `g t / g k / g a / g g / g l` finalized.
- HelpDrawer becomes tabbed by feature.
- Accessibility sweep: aria-label coverage, focus management, screen-reader smoke test on each surface.

## 7. Deferred / open questions

These are intentionally NOT decided in this umbrella. Each is the right size for a single detail spec or follow-up.

- **Talks UX shape.** No `web/UI_SPEC.md` sketch yet. The PR 11 detail spec will land that.
- **KB compose-entry dialog scope.** Feature-flagged in PR 10. Promotion to default-on is a separate small spec.
- **Charts in Dashboard.** First pass uses HTML/CSS bars. Chart.js/recharts adoption is a follow-up if the founder pushes for richer views.
- **Light theme calibration.** Dark-only ships through PR 12. PR 13 lights up light; if calibration surfaces are awkward, defer to a follow-up.
- **Per-task "view in audit"** deep-link shape. PR 9 detail spec decides URL form and pre-filter encoding.

## 8. Spec deliverables

- This umbrella: `docs/superpowers/specs/2026-05-18-web-app-complete-feature-set-design.md` (committed in the PR that opens the program).
- Detail specs, one per PR: `docs/superpowers/specs/2026-MM-DD-web-<feature>-design.md`. Each is written when its PR starts, not up-front.
- Each detail spec follows the existing convention from `2026-05-14-web-ui-design.md` (Goal / Non-goals / Architecture / Data flow / Components / Testing / Ops).

## 9. Implementation order summary

1. PR 7 — Tasks (introduces Drawer + density toggle + jump-keys infrastructure)
2. PR 8 — Agents
3. PR 9 — Audit
4. PR 10 — KB
5. PR 11 — Talks
6. PR 12 — Dashboard / Live Status
7. PR 13 — Polish (density audit, theme, Cmd-K, jump-keys finalization, a11y)

Each PR has its own detail spec, plan, implementation, and verification gate (contract test + Vitest + manual sign-off). The umbrella is not re-opened unless the sequencing or non-goals change materially.

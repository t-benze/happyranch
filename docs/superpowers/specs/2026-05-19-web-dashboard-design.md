# Web — Dashboard / Live Status

**Date:** 2026-05-19
**Status:** Draft, pending implementation plan.
**Extends:** `docs/superpowers/specs/2026-05-18-web-app-complete-feature-set-design.md` §6.6 (umbrella).
**Implements:** `protocol/05e-dashboard.md` Page 1 (Live Status).
**Tracks:** GitHub issue #22 (PR 12 — Dashboard / Live Status).

## 1. Goal

Ship the founder's "one-screen, what's-happening-now" landing card grid as the
final feature surface in the web roadmap. Every card is a thin projection over
a daemon route already mirrored in `lib/api/`. No new daemon work, no new SSE
stream, no charts library.

Single screen, polled every 30 s. History pages live in Audit.

## 2. Non-goals

- **New daemon endpoints.** Dashboard composes existing `tasks` + `health`
  reads.
- **SSE.** The umbrella suggests piggybacking on `/tasks/events`, but that
  route is per-task (`/tasks/{id}/events`) and an inbox stream does not
  exist in the current daemon. v1 polls instead — 30 s `refetchInterval`,
  which is the cadence the protocol doc specifies for this page anyway.
  Adding an inbox stream is a separate spec if polling proves insufficient.
- **Charts / sparklines / trend lines.** Defers to a follow-up. v1 is HTML/CSS
  bars at most; PR 12 doesn't need any of them.
- **Agent scorecards / tier rollups.** Already on the Agents page; not
  duplicated here in v1. (Protocol Page 2 is a separate dashboard subpage; this
  PR is Page 1 only.)
- **Mutations from the dashboard.** Cards link out to the underlying feature
  page (Tasks, Audit). The dashboard is read-only — same posture as the rest of
  the founder console.
- **Replacing the default landing.** Today `/` redirects to threads inbox; PR
  12 leaves that untouched. Promoting dashboard to landing is a one-line change
  that belongs in PR 13 polish (or a follow-up).
- **Jump-key.** The umbrella's final `g <letter>` map (§5.4) does not
  reserve a slot for dashboard. PR 13 may add one; PR 12 reaches the page
  via TopBar nav click only.

## 3. Cards (the four panels)

All four read from the same `useTasksList()` query (single round-trip, sliced
client-side) plus one `getHealth()` query for the health card.

### 3.1 System health

Source: `GET /api/v1/health` (already mirrored at `lib/api/health.ts`).

Render:

```
SYSTEM HEALTH
● daemon: ok
  active runtime: /Users/.../happyranch
```

- Green dot when `status === 'ok'`, red dot otherwise.
- Active runtime path truncated to the last 2 path segments with `…/` prefix
  when longer than 40 chars (avoid wrapping inside the card).
- Loading: dashed placeholder dot + `loading…`.
- Error: red dot + `unreachable`.

### 3.2 Pending your action

Source: client-side filter `task.status === 'blocked' && task.block_kind === 'escalated'`.

Render a list of `TaskCard` rows (existing pattern, reuse with
`density='compact'`), sorted by `updated_at` descending. Each card links to the
task detail page via `useTasksRoutes().detail(task_id)` — clicking lands on
`TasksPage` where the founder can run `Resolve escalation` from the dialog
that already exists.

Empty state: `EmptyState` pattern with `title="All clear"` + body `"No
escalations waiting on the founder."`.

### 3.3 Active tasks by team

Source: client-side filter `task.status === 'in_progress'`.

Group by `task.team`. Each group renders a small header (team name + count)
followed by compact `TaskCard` rows for tasks in that team. Teams sorted by
name; within a team, tasks sorted by `updated_at` descending.

Empty state: `EmptyState` with `title="No active tasks"`.

### 3.4 Blocked tasks

Source: client-side filter `task.status === 'blocked' && task.block_kind !== 'escalated'`.

These are the `delegated`-kind blocks — managers waiting on workers, parents
waiting on children. Show the same compact `TaskCard` rows, sorted by
`updated_at` descending.

Empty state: `EmptyState` with `title="No blocked tasks"` + body explicit that
escalations show in the panel above.

## 4. Layout

New layout primitive at `web/src/design-system/layouts/DashboardLayout.tsx`.

Slot-based. Accepts four named slot props:

```ts
interface DashboardLayoutProps {
  health: React.ReactNode;
  pending: React.ReactNode;
  activeByTeam: React.ReactNode;
  blocked: React.ReactNode;
}
```

Renders a two-column CSS grid (`grid-cols-1 lg:grid-cols-2`) with the following
arrangement:

```
┌────────────────┬──────────────────┐
│  System health │ Pending your     │
│                │ action           │
├────────────────┴──────────────────┤
│  Active tasks by team             │
├───────────────────────────────────┤
│  Blocked tasks                    │
└───────────────────────────────────┘
```

- Health + Pending share the top row on `lg`+ screens (stacked on mobile —
  even though the umbrella says desktop-only, the grid degrades gracefully so
  small windows aren't unusable).
- "Active by team" and "Blocked" span the full row each.
- Each slot is wrapped in a generic card frame: `border-border-subtle`,
  `bg-surface-raised`, `rounded-lg`, `p-4`, header with a small label.
- The card frame is internal to `DashboardLayout` — feature code only provides
  the body JSX, not the chrome. Matches the slot pattern already used by
  `ThreadsLayout`.

## 5. Page wiring

New feature folder at `web/src/features/dashboard/`:

- `DashboardPage.tsx` — top-level page. Reads `useTasksList()` and
  `useHealth()`, slices the list into three buckets, hands each to the layout.
- `DashboardPage.test.tsx` — Vitest + RTL, renders under the prototype provider
  with seeded fixtures, asserts that each card surfaces the right tasks.

The folder has no dialogs (read-only), no SSE wrappers, no mutations — the
narrowest feature on the site.

### 5.1 Health hook

A new provider-aware hook so the dashboard doesn't reach into `lib/api/` from
feature code:

```ts
// web/src/hooks/health.ts
export const useHealth = () => useData().health.useHealth();
```

Under the real provider:

```ts
// web/src/design-system/providers/_real-health.ts
function useHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: () => healthApi.getHealth(),
    refetchInterval: 30_000,
  });
}
```

Under the mock provider: return a static `{ status: 'ok', active_runtime: '/mock/runtime' }`.

A new `HealthApi` interface on `DataContext`:

```ts
export interface HealthApi {
  useHealth: () => QueryLike<HealthResponse>;
}
```

`DataContextValue` gains a `health: HealthApi` field; both providers wire it.

### 5.2 Refetch cadence

The dashboard re-renders every 30 s. Easiest implementation: every query
mounted on this page sets `refetchInterval: 30_000` and `staleTime: 0` (or
keep `staleTime: 30_000` matching the interval — same effect). The page itself
doesn't drive a timer.

Existing `useTasksList` under the real provider already sets
`refetchInterval: 10_000`. The dashboard reuses it as-is — 10 s is more
aggressive than 30 s but matches the rest of the app's posture; no need to
fork. Health gets its own 30 s interval since the daemon health probe is the
cheapest query in the system.

## 6. Routing

Append one route under `/orgs/:slug`:

```tsx
<Route path="dashboard" element={<DashboardPage />} />
```

TopBar gets a new placeholder-tab entry, pinned as the **first** nav tab:

```tsx
<NavTab {...placeholderTab('dashboard')}>Dashboard</NavTab>
```

(Slot before `Threads` so the founder reads left-to-right: overview → inbox →
work.) The existing `placeholderTab` helper handles slug-aware nav and the
prototype-sandbox disable.

The root `/` redirect remains pointed at `/threads` for v1 — see §2.

## 7. Data flow

```
DashboardPage
  ├── useHealth()                    → System health card body
  └── useTasksList()                 → all four task cards
        ├── filter blocked+escalated → Pending your action body
        ├── filter in_progress       → Active tasks by team body
        └── filter blocked+delegated → Blocked tasks body
```

One `useTasksList()` call powers three cards. The query fetches `limit=200`
(bumped from the default 20) so the dashboard doesn't miss in-flight tasks on
a busy day. 200 rows is well within the daemon's existing performance ceiling
and matches what `EscalationsTab` uses (`limit: 500`) for similar
cross-cutting reads.

To pass the limit, the page calls `useTasksList({ limit: 200 })`. The hook
already accepts `{ status?: string; limit?: number }`; only the limit is set.

## 8. Patterns reused

| What | From | How |
|---|---|---|
| Compact task rows | `@/design-system/patterns/TaskCard` | `density='compact'` |
| Empty card bodies | `@/design-system/patterns/EmptyState` | as-is |
| Card frame | `DashboardLayout` (new) | internal |
| Task detail routing | `useTasksRoutes().detail()` | as-is |

No new patterns under `design-system/patterns/`. Only the layout is new.

## 9. Mock-provider coverage

The prototype sandbox doesn't need to render the dashboard in v1 — but the
provider contract is total, so `_mock-tasks.ts` and a new `_mock-health.ts`
must export the same hook surface. Implementation: return canned fixtures
already used by Tasks; the new health mock returns
`{ status: 'ok', active_runtime: '/mock/runtime' }`.

This keeps `useData()` consumers (the page) provider-agnostic, even though
no sandbox route currently mounts `DashboardPage`.

## 10. Testing

### 10.1 Vitest

`web/src/features/dashboard/DashboardPage.test.tsx`:

- Renders under `<MemoryRouter>` + `<PrototypeProvider>` (or a hand-rolled
  context if richer fixture control is needed).
- Seeds the tasks list with a mix of statuses + block kinds: escalated
  blocks, delegated blocks, in_progress tasks across two teams, plus a
  completed task that should appear nowhere on the dashboard.
- Asserts:
  - "Pending your action" lists exactly the escalated-blocked task IDs.
  - "Active tasks by team" groups in_progress tasks under the right team
    headers.
  - "Blocked tasks" lists only delegated-blocked task IDs (not escalated).
  - "System health" renders the daemon-ok badge and active runtime.
  - The completed task is **absent** from every card.

### 10.2 Layout primitive

`web/src/design-system/layouts/DashboardLayout.test.tsx` (optional but
small): renders with stub slot content and asserts each slot's label is in
the document and the four slots' content nodes appear in DOM order.

### 10.3 Contract test

No new INCLUDED entry — `/api/v1/health` is already in the snapshot. Adding
the dashboard touches no daemon route.

### 10.4 Manual smoke

`scripts/daemon.sh start` + `cd web && npm run dev`. From a clean fixture
runtime:

1. Visit `/orgs/<slug>/dashboard` directly — confirm all four cards render
   and the layout doesn't overlap the TopBar.
2. Click TopBar `Dashboard` — confirm nav active state lights up.
3. Submit a task (`happyranch submit --team <team> --brief "..."`) — confirm
   it appears in "Active tasks by team" within 30 s.
4. Cancel the task — confirm it disappears within 30 s.

## 11. Risks / Open items

- **Polling at 30 s + 10 s mismatch.** The reused `useTasksList` polls at
  10 s; health at 30 s. On a busy dashboard this is fine, but if it adds
  noticeable load we drop dashboard's task polling to 30 s via a separate
  query key. Not addressed in v1.
- **`block_kind` semantics.** The dashboard divides on `block_kind`, but the
  field is nullable. Treat NULL as "delegated" for the Blocked card (a
  blocked task with no block kind is still blocked on something not the
  founder). Confirm by reading `BlockKind` typed as `'delegated' | 'escalated' | null`.
- **Limit=200 fall-off.** If a single org ever has >200 active+blocked tasks,
  the bottom cards become misleading. Not in scope; the realistic ceiling for
  a one-founder org is well under that.

## 12. Out-of-scope items (parking lot)

These came up while writing the spec and are not in this PR:

- Dashboard as the default landing page.
- A `g d` jump-key for dashboard.
- Charts library evaluation.
- Page 2 (scorecards) / Page 3 (audit) / Page 4 (escalations) / Page 5
  (trends) / Page 6 (traces) of the protocol doc — those are handled by other
  existing surfaces (Agents, Audit).

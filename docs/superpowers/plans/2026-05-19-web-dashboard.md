# Dashboard / Live Status — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the founder dashboard / Live Status page (PR 12 in the umbrella roadmap) — a single screen with four cards (system health, pending your action, active tasks by team, blocked tasks), polled every 30 s, read-only.

**Architecture:** New `web/src/features/dashboard/` feature folder + a single new layout primitive at `web/src/design-system/layouts/DashboardLayout.tsx`. All cards read from one `useTasksList({ limit: 200 })` query (sliced client-side) plus a new `useHealth()` hook over the already-mirrored `/health` route. No new daemon work, no new SSE stream, no charts.

**Tech Stack:** React 18 + TypeScript strict + Tailwind 3 + TanStack Query v5 + React Router v6 + Vitest/RTL + MSW for HTTP mocking. Layered per `web/ARCHITECTURE.md`: `lib/api/` (1:1 daemon mirror) → `design-system/` (primitives, patterns, layouts) → `features/<domain>/` (composition).

**Spec:** `docs/superpowers/specs/2026-05-19-web-dashboard-design.md`.

---

## File map

**New files:**

- `web/src/design-system/layouts/DashboardLayout.tsx` — slot-based card grid.
- `web/src/design-system/layouts/DashboardLayout.test.tsx` — slot rendering smoke test.
- `web/src/design-system/providers/_real-health.ts` — real `HealthApi` implementation.
- `web/src/design-system/providers/_mock-health.ts` — mock `HealthApi`.
- `web/src/hooks/health.ts` — provider-aware passthrough.
- `web/src/features/dashboard/DashboardPage.tsx` — page component.
- `web/src/features/dashboard/DashboardPage.test.tsx` — RTL coverage.

**Modified files:**

- `web/src/design-system/providers/DataContext.ts` — add `HealthApi` interface, expose on `DataContextValue`.
- `web/src/design-system/providers/AppProvider.tsx` — wire `realHealthApi`.
- `web/src/design-system/providers/PrototypeProvider.tsx` — wire `mockHealthApi`.
- `web/src/routes.tsx` — add `/orgs/:slug/dashboard` route.
- `web/src/design-system/layouts/AppShell/TopBar.tsx` — add Dashboard nav tab as first item.

**Not modified:**

- No daemon code. No `lib/api/` changes (health module already exists).
- No `tests/contract/openapi.json`. No `web/src/test/openapi-coverage.test.ts` (no new daemon routes).
- No CLAUDE.md or protocol/ docs.

---

## Task 1: DashboardLayout primitive

**Files:**
- Create: `web/src/design-system/layouts/DashboardLayout.tsx`
- Test: `web/src/design-system/layouts/DashboardLayout.test.tsx`

### Step 1: Write the failing test

```tsx
// web/src/design-system/layouts/DashboardLayout.test.tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';
import { DashboardLayout } from './DashboardLayout';

describe('DashboardLayout', () => {
  test('renders all four labeled slots', () => {
    render(
      <DashboardLayout
        health={<div>health-body</div>}
        pending={<div>pending-body</div>}
        activeByTeam={<div>active-body</div>}
        blocked={<div>blocked-body</div>}
      />,
    );
    expect(screen.getByText(/system health/i)).toBeInTheDocument();
    expect(screen.getByText(/pending your action/i)).toBeInTheDocument();
    expect(screen.getByText(/active tasks by team/i)).toBeInTheDocument();
    expect(screen.getByText(/blocked tasks/i)).toBeInTheDocument();
    expect(screen.getByText('health-body')).toBeInTheDocument();
    expect(screen.getByText('pending-body')).toBeInTheDocument();
    expect(screen.getByText('active-body')).toBeInTheDocument();
    expect(screen.getByText('blocked-body')).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd web && npx vitest run src/design-system/layouts/DashboardLayout.test.tsx`
Expected: FAIL — `Cannot find module './DashboardLayout'`.

- [ ] **Step 3: Implement DashboardLayout**

```tsx
// web/src/design-system/layouts/DashboardLayout.tsx
/**
 * DashboardLayout — four-card grid for the Live Status page (PR 12).
 *
 * Slot-based. Each slot is wrapped in a card frame (border + raised surface +
 * padded body) so feature code only supplies the body JSX. Two-column grid on
 * `lg` screens, single-column on small viewports. The umbrella spec scopes
 * this app to desktop, but the responsive collapse keeps narrow windows
 * usable without forking layouts.
 *
 * Spec: `docs/superpowers/specs/2026-05-19-web-dashboard-design.md` §4.
 */
import type { ReactNode } from 'react';

interface DashboardCardProps {
  label: string;
  children: ReactNode;
  /** When true the card spans both columns on `lg`. */
  wide?: boolean;
}

function DashboardCard({ label, children, wide }: DashboardCardProps): JSX.Element {
  return (
    <section
      className={[
        'border-border-subtle bg-surface-raised rounded-lg border p-4',
        wide ? 'lg:col-span-2' : '',
      ].join(' ')}
      aria-label={label}
    >
      <h2 className="text-overline text-text-muted mb-3 tracking-wide uppercase">
        {label}
      </h2>
      {children}
    </section>
  );
}

export interface DashboardLayoutProps {
  health: ReactNode;
  pending: ReactNode;
  activeByTeam: ReactNode;
  blocked: ReactNode;
}

export function DashboardLayout({
  health,
  pending,
  activeByTeam,
  blocked,
}: DashboardLayoutProps): JSX.Element {
  return (
    <div className="bg-surface-canvas h-full overflow-y-auto p-4">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <DashboardCard label="System health">{health}</DashboardCard>
        <DashboardCard label="Pending your action">{pending}</DashboardCard>
        <DashboardCard label="Active tasks by team" wide>
          {activeByTeam}
        </DashboardCard>
        <DashboardCard label="Blocked tasks" wide>
          {blocked}
        </DashboardCard>
      </div>
    </div>
  );
}

export const meta = {
  name: 'DashboardLayout',
  layer: 'layout',
  import: '@/design-system/layouts/DashboardLayout',
  variants: {},
  consumes: [],
  example:
    "<DashboardLayout health={<div/>} pending={<div/>} activeByTeam={<div/>} blocked={<div/>} />",
} as const;
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd web && npx vitest run src/design-system/layouts/DashboardLayout.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/design-system/layouts/DashboardLayout.tsx \
        web/src/design-system/layouts/DashboardLayout.test.tsx
git commit -m "feat(web): DashboardLayout primitive — four-card grid"
```

---

## Task 2: HealthApi on DataContext

The dashboard needs `useHealth()` exposed through the provider seam so feature
code never reaches into `lib/api/`. Adding a new domain to the provider is
three files: the context interface, the real impl, the mock impl.

**Files:**
- Modify: `web/src/design-system/providers/DataContext.ts`
- Create: `web/src/design-system/providers/_real-health.ts`
- Create: `web/src/design-system/providers/_mock-health.ts`
- Modify: `web/src/design-system/providers/AppProvider.tsx`
- Modify: `web/src/design-system/providers/PrototypeProvider.tsx`
- Create: `web/src/hooks/health.ts`

- [ ] **Step 1: Extend `DataContext` with HealthApi**

Open `web/src/design-system/providers/DataContext.ts` and locate the
"Context shape" section near the bottom (around the `DataContextValue`
interface).

Add this block above the `DataContextValue` declaration (anywhere alongside
the other `*Api` interfaces is fine; place it near `OrgsApi` for symmetry):

```ts
// ---------------------------------------------------------------------------
// HealthApi — minimal daemon liveness probe consumed by the Dashboard page.
// ---------------------------------------------------------------------------

import type { HealthResponse } from '@/lib/api/types';

export interface HealthApi {
  useHealth: () => QueryLike<HealthResponse>;
}
```

If `HealthResponse` is already imported at the top of the file, drop the
`import` line and just reuse the existing import. (Currently the file
imports `OrgsListResponse` from `'@/lib/api/types'`; add `HealthResponse` to
that same import.)

Then add `health: HealthApi;` to `DataContextValue`:

```ts
export interface DataContextValue {
  orgs: OrgsApi;
  agents: AgentsApi;
  audit: AuditApi;
  threads: ThreadsApi;
  tasks: TasksApi;
  kb: KbApi;
  talks: TalksApi;
  health: HealthApi;
  // ... unchanged routes hooks below
  useThreadRoutes: () => ThreadRoutes;
  // ...
}
```

- [ ] **Step 2: Implement `_real-health.ts`**

Create `web/src/design-system/providers/_real-health.ts`:

```ts
/**
 * Real (daemon-backed) implementation of `HealthApi`.
 *
 * Polls `/health` every 30 s — cheapest query in the system, used as the
 * Dashboard's heartbeat indicator.
 */
import { useQuery } from '@tanstack/react-query';
import { health as healthApi } from '@/lib/api';
import type { HealthResponse } from '@/lib/api/types';
import type { HealthApi, QueryLike } from './DataContext';

function useHealth(): QueryLike<HealthResponse> {
  return useQuery({
    queryKey: ['health'],
    queryFn: () => healthApi.getHealth(),
    refetchInterval: 30_000,
  });
}

export const realHealthApi: HealthApi = {
  useHealth,
};
```

- [ ] **Step 3: Implement `_mock-health.ts`**

Create `web/src/design-system/providers/_mock-health.ts`:

```ts
/**
 * Mock implementation of `HealthApi` for the prototype sandbox.
 */
import type { HealthApi, QueryLike } from './DataContext';
import type { HealthResponse } from '@/lib/api/types';

function ok<T>(data: T): QueryLike<T> {
  return { data, isLoading: false, isError: false, error: null };
}

const FIXTURE: HealthResponse = {
  status: 'ok',
  active_runtime: '/mock/runtime',
};

export const mockHealthApi: HealthApi = {
  useHealth: () => ok(FIXTURE),
};
```

- [ ] **Step 4: Wire `realHealthApi` into `AppProvider`**

Open `web/src/design-system/providers/AppProvider.tsx`. Add an import next to
the other `_real-*` imports:

```ts
import { realHealthApi } from './_real-health';
```

In the `<DataContext.Provider value={{...}}>` block, add `health: realHealthApi,`
alongside the other API entries:

```tsx
value={{
  orgs: realOrgsApi,
  agents: realAgentsApi,
  audit: realAuditApi,
  threads: realThreadsApi,
  tasks: realTasksApi,
  kb: realKbApi,
  talks: realTalksApi,
  health: realHealthApi,
  useThreadRoutes: useRealThreadRoutes,
  // ...
}}
```

- [ ] **Step 5: Wire `mockHealthApi` into `PrototypeProvider`**

Open `web/src/design-system/providers/PrototypeProvider.tsx`. Read the file
first if you haven't — the structure mirrors `AppProvider`. Add an import for
`mockHealthApi` next to the other `_mock-*` imports, then add
`health: mockHealthApi,` to the `<DataContext.Provider value={{...}}>` block.

- [ ] **Step 6: Public hook in `@/hooks/health.ts`**

Create `web/src/hooks/health.ts`:

```ts
/**
 * Public, provider-aware health hook. Features should call this instead of
 * reaching into `@/lib/api/health` directly.
 */
import { useData } from '@/design-system/providers/DataContext';

export const useHealth = () => useData().health.useHealth();
```

- [ ] **Step 7: Type-check the wiring**

Run: `cd web && npx tsc --noEmit`
Expected: PASS (no new type errors).

- [ ] **Step 8: Commit**

```bash
git add web/src/design-system/providers/DataContext.ts \
        web/src/design-system/providers/_real-health.ts \
        web/src/design-system/providers/_mock-health.ts \
        web/src/design-system/providers/AppProvider.tsx \
        web/src/design-system/providers/PrototypeProvider.tsx \
        web/src/hooks/health.ts
git commit -m "feat(web): HealthApi on DataContext for dashboard"
```

---

## Task 3: DashboardPage skeleton (no slicing logic yet)

Stand up the empty page that mounts under the new route. Slicing comes in Task 5
after the test scaffolding (Task 4) is in place.

**Files:**
- Create: `web/src/features/dashboard/DashboardPage.tsx`
- Modify: `web/src/routes.tsx`
- Modify: `web/src/design-system/layouts/AppShell/TopBar.tsx`

- [ ] **Step 1: Stub `DashboardPage`**

Create `web/src/features/dashboard/DashboardPage.tsx`:

```tsx
/**
 * Founder dashboard / Live Status page (PR 12).
 *
 * Spec: `docs/superpowers/specs/2026-05-19-web-dashboard-design.md`.
 *
 * One `useTasksList({ limit: 200 })` query powers three cards (sliced
 * client-side by status + block_kind); one `useHealth()` query powers the
 * fourth. Read-only — clicking a task opens the Tasks feature.
 */
import { DashboardLayout } from '@/design-system/layouts/DashboardLayout';

export function DashboardPage(): JSX.Element {
  return (
    <DashboardLayout
      health={<p className="text-text-muted">loading…</p>}
      pending={<p className="text-text-muted">loading…</p>}
      activeByTeam={<p className="text-text-muted">loading…</p>}
      blocked={<p className="text-text-muted">loading…</p>}
    />
  );
}
```

- [ ] **Step 2: Mount the route**

Open `web/src/routes.tsx`. Add the dashboard import next to the other feature
imports (alphabetical block at the top):

```tsx
import { DashboardPage } from '@/features/dashboard/DashboardPage';
```

Inside `<Route path="/orgs/:slug" element={<OrgLayout />}>`, add:

```tsx
<Route path="dashboard" element={<DashboardPage />} />
```

Place it before the `tasks` route to mirror the TopBar ordering.

- [ ] **Step 3: Add TopBar nav tab**

Open `web/src/design-system/layouts/AppShell/TopBar.tsx`. In the `<nav>` block
(around the Threads/Tasks tabs), add Dashboard as the first nav entry:

```tsx
<nav className="flex items-center gap-1 text-sm">
  <NavTab {...placeholderTab('dashboard')}>Dashboard</NavTab>
  <NavTab to={threadsHref} enabled={!!activeSlug && threadsHref !== '#'}>
    Threads
  </NavTab>
  <NavTab {...placeholderTab('tasks')}>Tasks</NavTab>
  {/* ...remaining unchanged */}
</nav>
```

The existing `placeholderTab` helper handles slug-aware nav and prototype-sandbox disable.

- [ ] **Step 4: Build + visual smoke**

Run: `cd web && npx tsc --noEmit && npm run build`
Expected: clean build, no TypeScript errors.

- [ ] **Step 5: Commit**

```bash
git add web/src/features/dashboard/DashboardPage.tsx \
        web/src/routes.tsx \
        web/src/design-system/layouts/AppShell/TopBar.tsx
git commit -m "feat(web): dashboard route + topbar nav (stub page)"
```

---

## Task 4: Failing dashboard tests

Write the full test file before wiring slicing logic. This is what TDD looks
like with MSW: seed the daemon responses, mount the page through the real
provider, assert what each card surfaces.

**Files:**
- Create: `web/src/features/dashboard/DashboardPage.test.tsx`

- [ ] **Step 1: Write the failing tests**

```tsx
// web/src/features/dashboard/DashboardPage.test.tsx
import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'hk-macau-tourism';

function task(overrides: Record<string, unknown>) {
  return {
    task_id: 'TASK-0001',
    team: 'content',
    brief: 'placeholder',
    status: 'in_progress',
    block_kind: null,
    parent_task_id: null,
    revisit_of_task_id: null,
    created_at: '2026-05-18T10:00:00Z',
    updated_at: '2026-05-18T10:00:00Z',
    closed_at: null,
    cancelled_at: null,
    session_timeout_seconds: null,
    ...overrides,
  };
}

const ESCALATED = task({
  task_id: 'TASK-ESC-1',
  team: 'cx',
  brief: 'Refund $280 awaiting founder',
  status: 'blocked',
  block_kind: 'escalated',
});

const DELEGATED = task({
  task_id: 'TASK-BLK-1',
  team: 'product',
  brief: 'Waiting on child worker',
  status: 'blocked',
  block_kind: 'delegated',
});

const ACTIVE_CONTENT = task({
  task_id: 'TASK-ACT-1',
  team: 'content',
  brief: 'Draft Hong Kong visa guide v2',
  status: 'in_progress',
});

const ACTIVE_OPS = task({
  task_id: 'TASK-ACT-2',
  team: 'ops',
  brief: 'Vet partner hotel candidates',
  status: 'in_progress',
});

const COMPLETED = task({
  task_id: 'TASK-DONE-1',
  team: 'content',
  brief: 'Already shipped',
  status: 'completed',
});

function mountAt(route: string) {
  sessionStorage.setItem('grassland.token', 'tok');
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
    http.get('/api/v1/health', () =>
      HttpResponse.json({ status: 'ok', active_runtime: '/Users/x/grassland' }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/tasks`, () =>
      HttpResponse.json({
        tasks: [ESCALATED, DELEGATED, ACTIVE_CONTENT, ACTIVE_OPS, COMPLETED],
      }),
    ),
  );
  return renderWithProviders(<AppRoutes />, { route });
}

describe('DashboardPage', () => {
  test('renders all four card sections', async () => {
    mountAt(`/orgs/${SLUG}/dashboard`);
    await waitFor(() => {
      expect(screen.getByLabelText(/system health/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/pending your action/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/active tasks by team/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/blocked tasks/i)).toBeInTheDocument();
    });
  });

  test('system health card shows daemon-ok + active runtime', async () => {
    mountAt(`/orgs/${SLUG}/dashboard`);
    const card = await screen.findByLabelText(/system health/i);
    await waitFor(() => {
      expect(within(card).getByText(/daemon: ok/i)).toBeInTheDocument();
    });
    expect(within(card).getByText(/grassland/i)).toBeInTheDocument();
  });

  test('pending your action lists only escalated-blocked tasks', async () => {
    mountAt(`/orgs/${SLUG}/dashboard`);
    const card = await screen.findByLabelText(/pending your action/i);
    await waitFor(() => {
      expect(within(card).getByText(/refund \$280/i)).toBeInTheDocument();
    });
    expect(within(card).queryByText(/waiting on child worker/i)).toBeNull();
    expect(within(card).queryByText(/draft hong kong/i)).toBeNull();
  });

  test('active tasks card groups by team', async () => {
    mountAt(`/orgs/${SLUG}/dashboard`);
    const card = await screen.findByLabelText(/active tasks by team/i);
    await waitFor(() => {
      expect(within(card).getByText(/draft hong kong/i)).toBeInTheDocument();
    });
    expect(within(card).getByText(/vet partner hotel/i)).toBeInTheDocument();
    // Team headings are rendered.
    expect(within(card).getByText(/^content$/i)).toBeInTheDocument();
    expect(within(card).getByText(/^ops$/i)).toBeInTheDocument();
    // Completed task does not leak in.
    expect(within(card).queryByText(/already shipped/i)).toBeNull();
  });

  test('blocked tasks card excludes escalations and completions', async () => {
    mountAt(`/orgs/${SLUG}/dashboard`);
    const card = await screen.findByLabelText(/blocked tasks/i);
    await waitFor(() => {
      expect(within(card).getByText(/waiting on child worker/i)).toBeInTheDocument();
    });
    expect(within(card).queryByText(/refund \$280/i)).toBeNull();
    expect(within(card).queryByText(/already shipped/i)).toBeNull();
  });

  test('empty buckets render their respective empty states', async () => {
    sessionStorage.setItem('grassland.token', 'tok');
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get('/api/v1/health', () =>
        HttpResponse.json({ status: 'ok', active_runtime: '/Users/x/grassland' }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks`, () =>
        HttpResponse.json({ tasks: [] }),
      ),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/dashboard` });

    const pending = await screen.findByLabelText(/pending your action/i);
    await waitFor(() => {
      expect(within(pending).getByText(/all clear/i)).toBeInTheDocument();
    });
    expect(
      within(screen.getByLabelText(/active tasks by team/i)).getByText(/no active tasks/i),
    ).toBeInTheDocument();
    expect(
      within(screen.getByLabelText(/blocked tasks/i)).getByText(/no blocked tasks/i),
    ).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the tests and verify they all fail**

Run: `cd web && npx vitest run src/features/dashboard/DashboardPage.test.tsx`
Expected: ALL FAIL — page is a stub, no health badge, no slicing, no empty states.

- [ ] **Step 3: Do NOT commit yet**

The plan keeps the test commit and the implementation commit separate
intentionally — but we want red→green in a single visible diff, so hold the
add until Task 5.

---

## Task 5: Implement DashboardPage slicing

Make the failing tests pass. The page now:

1. Fetches `useTasksList({ limit: 200 })` and `useHealth()`.
2. Slices the tasks by status + block_kind.
3. Renders the four card bodies through `DashboardLayout`.

**Files:**
- Modify: `web/src/features/dashboard/DashboardPage.tsx`
- Create: helper file is NOT needed — keep slicing inline; it's three filters.

- [ ] **Step 1: Replace the stub with the real page**

Overwrite `web/src/features/dashboard/DashboardPage.tsx`:

```tsx
/**
 * Founder dashboard / Live Status page (PR 12).
 *
 * Spec: `docs/superpowers/specs/2026-05-19-web-dashboard-design.md`.
 *
 * One `useTasksList({ limit: 200 })` query powers three cards (sliced
 * client-side by status + block_kind); one `useHealth()` query powers the
 * fourth. Read-only — clicking a task opens the Tasks feature.
 */
import { useMemo } from 'react';
import { DashboardLayout } from '@/design-system/layouts/DashboardLayout';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { TaskCard } from '@/design-system/patterns/TaskCard';
import type { TaskRecord } from '@/lib/api/types';
import { useHealth } from '@/hooks/health';
import { useTasksList, useTasksRoutes } from '@/hooks/tasks';

const FETCH_LIMIT = 200;

function truncatePath(p: string | null, max = 48): string {
  if (!p) return '—';
  if (p.length <= max) return p;
  const parts = p.split('/').filter(Boolean);
  if (parts.length <= 2) return p;
  return `…/${parts.slice(-2).join('/')}`;
}

function byUpdatedDesc(a: TaskRecord, b: TaskRecord): number {
  return a.updated_at < b.updated_at ? 1 : a.updated_at > b.updated_at ? -1 : 0;
}

interface CardListProps {
  tasks: TaskRecord[];
  detailFor: (taskId: string) => string;
}

function CardTaskList({ tasks, detailFor }: CardListProps): JSX.Element {
  return (
    <ul className="space-y-2">
      {tasks.map((t) => (
        <li key={t.task_id}>
          <TaskCard task={t} to={detailFor(t.task_id)} density="compact" />
        </li>
      ))}
    </ul>
  );
}

function HealthBody(): JSX.Element {
  const q = useHealth();
  if (q.isLoading) {
    return <p className="text-text-muted text-sm">loading…</p>;
  }
  if (q.isError || !q.data) {
    return (
      <p className="text-sm">
        <span className="text-feedback-danger" aria-label="daemon unreachable">●</span>{' '}
        <span className="text-text-muted">daemon: unreachable</span>
      </p>
    );
  }
  const ok = q.data.status === 'ok';
  return (
    <div className="text-sm">
      <p>
        <span
          className={ok ? 'text-feedback-success' : 'text-feedback-danger'}
          aria-label={ok ? 'daemon ok' : 'daemon not ok'}
        >
          ●
        </span>{' '}
        <span className="text-text">daemon: {q.data.status}</span>
      </p>
      <p className="text-text-muted mt-1 font-mono text-xs" title={q.data.active_runtime ?? ''}>
        active runtime: {truncatePath(q.data.active_runtime)}
      </p>
    </div>
  );
}

export function DashboardPage(): JSX.Element {
  const tasksQuery = useTasksList({ limit: FETCH_LIMIT });
  const routes = useTasksRoutes();

  const all = tasksQuery.data?.tasks ?? [];

  const escalated = useMemo(
    () =>
      all
        .filter((t) => t.status === 'blocked' && t.block_kind === 'escalated')
        .sort(byUpdatedDesc),
    [all],
  );

  const blockedDelegated = useMemo(
    () =>
      all
        .filter((t) => t.status === 'blocked' && t.block_kind !== 'escalated')
        .sort(byUpdatedDesc),
    [all],
  );

  const activeByTeam = useMemo(() => {
    const groups = new Map<string, TaskRecord[]>();
    for (const t of all) {
      if (t.status !== 'in_progress') continue;
      const list = groups.get(t.team) ?? [];
      list.push(t);
      groups.set(t.team, list);
    }
    return [...groups.entries()]
      .map(([team, list]) => [team, [...list].sort(byUpdatedDesc)] as const)
      .sort(([a], [b]) => a.localeCompare(b));
  }, [all]);

  const pending = escalated.length === 0 ? (
    <EmptyState
      title="All clear"
      body="No escalations waiting on the founder."
    />
  ) : (
    <CardTaskList tasks={escalated} detailFor={routes.detail} />
  );

  const blocked = blockedDelegated.length === 0 ? (
    <EmptyState
      title="No blocked tasks"
      body="Escalations awaiting your action appear in the panel above."
    />
  ) : (
    <CardTaskList tasks={blockedDelegated} detailFor={routes.detail} />
  );

  const active = activeByTeam.length === 0 ? (
    <EmptyState title="No active tasks" body="No tasks are running right now." />
  ) : (
    <div className="space-y-4">
      {activeByTeam.map(([team, tasks]) => (
        <section key={team}>
          <h3 className="text-text-secondary mb-2 text-xs font-semibold">
            {team}
            <span className="text-text-muted ml-2 font-normal">({tasks.length})</span>
          </h3>
          <CardTaskList tasks={tasks} detailFor={routes.detail} />
        </section>
      ))}
    </div>
  );

  return (
    <DashboardLayout
      health={<HealthBody />}
      pending={pending}
      activeByTeam={active}
      blocked={blocked}
    />
  );
}
```

- [ ] **Step 2: Run the failing tests, now green**

Run: `cd web && npx vitest run src/features/dashboard/DashboardPage.test.tsx`
Expected: PASS (all 6 cases).

If the team-heading test (`/^content$/i`) fails because the team name also
appears inside a `TaskCard` row (the card already shows `task.team` in
muted text), narrow the assertion — e.g.

```ts
expect(within(card).getAllByText(/^content$/i).length).toBeGreaterThan(0);
```

— and apply the same to `/^ops$/i`. Make the smallest change needed.

- [ ] **Step 3: Run the whole web test suite to catch regressions**

Run: `cd web && npx vitest run`
Expected: PASS across all files (Threads, Tasks, Talks, KB, Audit, Agents,
contract test, design-system tests).

- [ ] **Step 4: Type-check + build**

Run: `cd web && npx tsc --noEmit && npm run build`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add web/src/features/dashboard/DashboardPage.tsx \
        web/src/features/dashboard/DashboardPage.test.tsx
git commit -m "feat(web): dashboard slicing — pending/active/blocked + health"
```

---

## Task 6: Manual smoke verification

The contract test catches API-shape drift; the Vitest cases cover slicing;
this step makes sure the page actually renders against a live daemon.

- [ ] **Step 1: Build the web bundle**

Run: `scripts/build_web.sh`
Expected: `web/dist/` regenerated; no errors.

- [ ] **Step 2: Start (or restart) the daemon**

Run: `scripts/daemon.sh status` first. If running, restart so the bundled
SPA picks up the new files. Else: `scripts/daemon.sh start`.

Expected: pid + port file under `~/.grassland/`.

- [ ] **Step 3: Open the dashboard**

Run: `uv run grassland web` (or open the URL printed by the daemon and
navigate to `/orgs/<slug>/dashboard`).

Expected:
- TopBar shows `Dashboard | Threads | Tasks | KB | Talks | Audit | Agents`
  with Dashboard highlighted.
- Four cards render with frames + labels.
- Empty state ("No active tasks") shows if the fixture runtime is idle.
- System health card shows `daemon: ok` with the active runtime path.

- [ ] **Step 4: Submit a test task and watch it appear**

In a separate shell:

```bash
uv run grassland submit --team content --brief "Dashboard smoke task"
```

Expected: within 10–30 s the task appears in the "Active tasks by team"
card under the `content` group. (Refresh the browser if you'd rather not
wait for the next poll.)

- [ ] **Step 5: Cancel the task and watch it disappear**

```bash
uv run grassland cancel <TASK-ID>
```

Expected: the task disappears from the active card on the next poll.

- [ ] **Step 6: Record the result in the commit message body, if relevant**

If the smoke uncovered anything not obvious (e.g., a missing field, an
unexpected layout overflow at certain viewport widths), fold the fix into a
small follow-up commit before the PR. If everything was clean, no further
commit is needed.

---

## Task 7: PR

- [ ] **Step 1: Sync with main, push the branch**

```bash
git fetch origin
git rebase origin/main || git merge origin/main
git push -u origin worktree-dashboard
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create --title "web: PR 12 — Dashboard / Live Status" --body "$(cat <<'EOF'
## Summary
- New `/orgs/:slug/dashboard` page with four cards: System health, Pending your action (escalated blocks), Active tasks by team (in-progress, grouped), Blocked tasks (delegated blocks).
- New `DashboardLayout` slot-based grid primitive (`web/src/design-system/layouts/`).
- New `HealthApi` on `DataContext` so the page never reaches into `lib/api/`.
- TopBar gains a `Dashboard` nav tab as the first entry.
- Implements `docs/superpowers/specs/2026-05-19-web-dashboard-design.md`; closes #22.

## Test plan
- [ ] `cd web && npx vitest run` — full suite (incl. `DashboardPage.test.tsx`).
- [ ] `cd web && npx tsc --noEmit` — type-check.
- [ ] `cd web && npm run build` — production build.
- [ ] `scripts/build_web.sh && scripts/daemon.sh start && open dashboard URL` — manual smoke (Task 6).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR URL.

---

## Self-Review

Before handing off:

1. **Spec coverage.** §3.1 system health (Task 5 HealthBody), §3.2 pending
   (Task 5 escalated slice + Task 4 test), §3.3 active by team (Task 5
   `activeByTeam` group), §3.4 blocked (Task 5 delegated slice). §4 layout
   (Task 1 DashboardLayout). §6 routing (Task 3 routes.tsx + TopBar). §7 data
   flow + 200-limit (Task 5 `FETCH_LIMIT`). §9 mock coverage (Task 2 mock
   health). §10 testing (Tasks 1, 4, 5, 6). No spec section is unaccounted for.

2. **Placeholder scan.** No TBDs. Every step has runnable code or shell.

3. **Type/identifier consistency.**
   - `HealthApi`, `realHealthApi`, `mockHealthApi`, `useHealth` — consistent
     across DataContext, AppProvider, PrototypeProvider, and the hook file.
   - `DashboardLayoutProps` slot names `health / pending / activeByTeam / blocked`
     match the page and the test.
   - `FETCH_LIMIT = 200` matches §7 of the spec.
   - `byUpdatedDesc` is defined once and reused.

# Web KB Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the KB placeholder page with a read-only browser (list + filter sidebar + Drawer detail) at `/orgs/:slug/kb`, with source-task badges deep-linked into Tasks, plus an optional flag-gated compose dialog.

**Architecture:** Mirrors PR 7 (Tasks). The contract-pinned `lib/api/kb.ts` is unchanged. A new `KbApi` + `KbRoutes` is added to `DataContext`; `_real-kb.ts` (TanStack hooks) and `_mock-kb.ts` (canned fixtures) plug into `AppProvider` and `PrototypeProvider`. A new `hooks/kb.ts` façade is consumed by `features/kb/` compositions. Filters: server-side `type`, client-side single-tag, debounced search switches the active query to `/kb/search`.

**Tech Stack:** React 18 + TypeScript strict, Tailwind 3, TanStack Query v5, React Router v6, Vitest + MSW. Vite dev server for `cd web && npm run dev`.

**Spec:** `docs/superpowers/specs/2026-05-19-web-kb-surface-design.md`.

---

## Task 1: Extend DataContext with KbApi + KbRoutes

**Files:**
- Modify: `web/src/design-system/providers/DataContext.ts`

- [ ] **Step 1: Add KbApi + KbRoutes interfaces and extend DataContextValue**

In `web/src/design-system/providers/DataContext.ts`, add `import type { kb as kbApi } from '@/lib/api'` and `import type { KBEntry } from '@/lib/api/kb'` near the existing tasks/threads imports. Then append after the `TasksRoutes` interface and before the "Context shape" comment:

```ts
// ---------------------------------------------------------------------------
// KbApi — covers every hook KbPage + its drawer + (optional) compose dialog
// consume.
// ---------------------------------------------------------------------------

export type AddKBEntryArgs = Parameters<typeof kbApi.addKBEntry>[1];
export type AddKBEntryResult = Awaited<ReturnType<typeof kbApi.addKBEntry>>;

export interface KbApi {
  useKBList: (params?: {
    type?: string;
  }) => QueryLike<{ entries: KBEntry[] }>;
  useKBSearch: (
    q: string,
    params?: { limit?: number },
  ) => QueryLike<{ entries: KBEntry[] }>;
  useKBEntry: (entrySlug: string | undefined) => QueryLike<KBEntry>;
  /** Mutation is wired only under the real provider; mocks no-op. */
  useAddKBEntry: () => MutationLike<AddKBEntryArgs, AddKBEntryResult>;
}

export interface KbRoutes {
  inbox: () => string;
  detail: (entrySlug: string) => string;
  inboxForOrg: (slug: string) => string;
}
```

Then update `DataContextValue` to include the new bag and hook:

```ts
export interface DataContextValue {
  orgs: OrgsApi;
  agents: AgentsApi;
  threads: ThreadsApi;
  tasks: TasksApi;
  kb: KbApi;
  /**
   * Provider-supplied React hook that returns the active feature's route
   * builders. A hook (not a plain object) so the implementation can read
   * the current URL via `useParams` / `useLocation`.
   */
  useThreadRoutes: () => ThreadRoutes;
  useTasksRoutes: () => TasksRoutes;
  useKbRoutes: () => KbRoutes;
}
```

- [ ] **Step 2: Type-check**

Run: `cd web && npx tsc --noEmit`

Expected: two errors — `AppProvider.tsx` and `PrototypeProvider.tsx` no longer satisfy `DataContextValue` because `kb` and `useKbRoutes` are missing. These are fixed in the next tasks.

- [ ] **Step 3: Commit**

```bash
git add web/src/design-system/providers/DataContext.ts
git commit -m "feat(web): KbApi + KbRoutes interfaces on DataContext"
```

---

## Task 2: Mock KB fixtures and mock api

**Files:**
- Create: `web/src/mocks/kb.ts`
- Create: `web/src/design-system/providers/_mock-kb.ts`

- [ ] **Step 1: Create the canned fixtures**

`web/src/mocks/kb.ts`:

```ts
import type { KBEntry } from '@/lib/api/kb';

export const MOCK_KB_ENTRIES: KBEntry[] = [
  {
    slug: 'policy/refund-thresholds',
    title: 'Refund authority by tier',
    type: 'precedent',
    topic: 'finance',
    tags: ['policy', 'finance', 'customer-care'],
    body:
      '# Refund authority\n\nThe CX Manager may approve refunds up to **$150**.' +
      ' Beyond that, escalate to the founder.\n',
    updated_at: '2026-05-16T09:00:00Z',
    authored_by: 'founder',
    source_task: 'TASK-0042',
    related_entries: ['intake/spanish-walk-ins'],
  },
  {
    slug: 'intake/spanish-walk-ins',
    title: 'Spanish-speaking walk-in flow',
    type: 'sop',
    topic: 'intake',
    tags: ['intake', 'language-spanish'],
    body: '# Spanish walk-ins\n\nGreet in Spanish, hand off to translator.\n',
    updated_at: '2026-05-12T11:00:00Z',
    authored_by: 'intake_manager',
    source_task: null,
    related_entries: [],
  },
  {
    slug: 'routing/macau-after-hours',
    title: 'Macau after-hours routing',
    type: 'guide',
    topic: 'routing',
    tags: ['routing', 'macau'],
    body: '# After-hours\n\nRoute to the partner concierge.\n',
    updated_at: '2026-05-08T22:00:00Z',
    authored_by: 'ops_manager',
    source_task: null,
    related_entries: [],
  },
];
```

- [ ] **Step 2: Create the mock api**

`web/src/design-system/providers/_mock-kb.ts`:

```ts
import { MOCK_KB_ENTRIES } from '@/mocks/kb';
import type { KBEntry } from '@/lib/api/kb';
import type {
  AddKBEntryArgs,
  AddKBEntryResult,
  KbApi,
  KbRoutes,
  MutationLike,
  QueryLike,
} from './DataContext';

function ok<T>(data: T): QueryLike<T> {
  return { data, isLoading: false, isError: false, error: null };
}

function noopMutation<TArgs, TResult>(): MutationLike<TArgs, TResult> {
  return {
    mutateAsync: async () => ({}) as TResult,
    isPending: false,
  };
}

export const mockKbApi: KbApi = {
  useKBList: (params) =>
    ok({
      entries: params?.type
        ? MOCK_KB_ENTRIES.filter((e) => e.type === params.type)
        : MOCK_KB_ENTRIES,
    }),
  useKBSearch: (q) =>
    ok({
      entries: q
        ? MOCK_KB_ENTRIES.filter(
            (e) =>
              e.title.toLowerCase().includes(q.toLowerCase()) ||
              e.body.toLowerCase().includes(q.toLowerCase()),
          )
        : MOCK_KB_ENTRIES,
    }),
  useKBEntry: (entrySlug) =>
    ok(
      MOCK_KB_ENTRIES.find((e) => e.slug === entrySlug) ?? MOCK_KB_ENTRIES[0],
    ) as QueryLike<KBEntry>,
  useAddKBEntry: () => noopMutation<AddKBEntryArgs, AddKBEntryResult>(),
};

export function useMockKbRoutes(): KbRoutes {
  return {
    inbox: () => '/__prototypes/kb',
    detail: (entrySlug: string) => `/__prototypes/kb/${entrySlug}`,
    inboxForOrg: () => '/__prototypes/kb',
  };
}
```

- [ ] **Step 3: Type-check**

Run: `cd web && npx tsc --noEmit`

Expected: the two AppProvider/PrototypeProvider errors from Task 1 remain (kb not wired yet), but no new errors.

- [ ] **Step 4: Commit**

```bash
git add web/src/mocks/kb.ts web/src/design-system/providers/_mock-kb.ts
git commit -m "feat(web): mock KB fixtures + mockKbApi for PrototypeProvider"
```

---

## Task 3: Wire real-kb hooks

**Files:**
- Create: `web/src/design-system/providers/_real-kb.ts`
- Modify: `web/src/design-system/providers/_real-routes.ts`

- [ ] **Step 1: Create real KB api**

`web/src/design-system/providers/_real-kb.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { kb as kbApi } from '@/lib/api';
import type {
  AddKBEntryArgs,
  AddKBEntryResult,
  KbApi,
  MutationLike,
  QueryLike,
} from './DataContext';

function useRealOrgSlug(): string {
  const { slug } = useParams<{ slug: string }>();
  return slug ?? '';
}

function useKBList(params?: { type?: string }) {
  const slug = useRealOrgSlug();
  return useQuery({
    queryKey: ['kb-list', slug, params],
    queryFn: () => kbApi.listKB(slug, params),
    enabled: !!slug,
  });
}

function useKBSearch(q: string, params?: { limit?: number }) {
  const slug = useRealOrgSlug();
  return useQuery({
    queryKey: ['kb-search', slug, q, params],
    queryFn: () => kbApi.searchKB(slug, { q, limit: params?.limit ?? 50 }),
    enabled: !!slug && q.trim().length > 0,
  });
}

function useKBEntry(entrySlug: string | undefined) {
  const slug = useRealOrgSlug();
  return useQuery({
    queryKey: ['kb-entry', slug, entrySlug],
    queryFn: () => kbApi.getKBEntry(slug, entrySlug as string),
    enabled: !!slug && !!entrySlug,
  });
}

function useAddKBEntry(): MutationLike<AddKBEntryArgs, AddKBEntryResult> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AddKBEntryArgs) => kbApi.addKBEntry(slug, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['kb-list', slug] });
      qc.invalidateQueries({ queryKey: ['kb-search', slug] });
    },
  });
}

export const realKbApi: KbApi = {
  useKBList: useKBList as KbApi['useKBList'],
  useKBSearch: useKBSearch as KbApi['useKBSearch'],
  useKBEntry: useKBEntry as KbApi['useKBEntry'],
  useAddKBEntry,
};
```

- [ ] **Step 2: Add useRealKbRoutes**

Append to `web/src/design-system/providers/_real-routes.ts`:

```ts
export function useRealKbRoutes(): import('./DataContext').KbRoutes {
  const slug = useOrgSlugOptional();
  return {
    detail: (entrySlug: string) =>
      slug ? `/orgs/${slug}/kb/${entrySlug}` : '#',
    inbox: () => (slug ? `/orgs/${slug}/kb` : '#'),
    inboxForOrg: (target: string) => `/orgs/${target}/kb`,
  };
}
```

- [ ] **Step 3: Type-check**

Run: `cd web && npx tsc --noEmit`

Expected: still two errors on the providers (the value bags don't reference `kb` / `useKbRoutes` yet); next task fixes them.

- [ ] **Step 4: Commit**

```bash
git add web/src/design-system/providers/_real-kb.ts web/src/design-system/providers/_real-routes.ts
git commit -m "feat(web): realKbApi + useRealKbRoutes"
```

---

## Task 4: Wire kb into AppProvider and PrototypeProvider

**Files:**
- Modify: `web/src/design-system/providers/AppProvider.tsx`
- Modify: `web/src/design-system/providers/PrototypeProvider.tsx`

- [ ] **Step 1: AppProvider**

In `web/src/design-system/providers/AppProvider.tsx`, add imports and extend the value bag:

```ts
import { realKbApi } from './_real-kb';
import { useRealKbRoutes, useRealTasksRoutes, useRealThreadRoutes } from './_real-routes';
```

Inside the `<DataContext.Provider value={{ ... }}>` literal, add two lines:

```tsx
        kb: realKbApi,
        ...
        useKbRoutes: useRealKbRoutes,
```

Final value bag (for clarity):

```tsx
<DataContext.Provider
  value={{
    orgs: realOrgsApi,
    agents: realAgentsApi,
    threads: realThreadsApi,
    tasks: realTasksApi,
    kb: realKbApi,
    useThreadRoutes: useRealThreadRoutes,
    useTasksRoutes: useRealTasksRoutes,
    useKbRoutes: useRealKbRoutes,
  }}
>
```

- [ ] **Step 2: PrototypeProvider**

In `web/src/design-system/providers/PrototypeProvider.tsx`, add imports:

```ts
import { mockKbApi, useMockKbRoutes } from './_mock-kb';
```

And extend the value bag the same way:

```tsx
<DataContext.Provider
  value={{
    orgs: mockOrgsApi,
    agents: mockAgentsApi,
    threads: mockThreadsApi,
    tasks: mockTasksApi,
    kb: mockKbApi,
    useThreadRoutes: useMockThreadRoutes,
    useTasksRoutes: useMockTasksRoutes,
    useKbRoutes: useMockKbRoutes,
  }}
>
```

- [ ] **Step 3: Type-check is clean**

Run: `cd web && npx tsc --noEmit`

Expected: 0 errors.

- [ ] **Step 4: Run existing test suite to confirm no regression**

Run: `cd web && npm test -- --run`

Expected: all tests pass (the new code is not yet consumed by any test or page).

- [ ] **Step 5: Commit**

```bash
git add web/src/design-system/providers/AppProvider.tsx web/src/design-system/providers/PrototypeProvider.tsx
git commit -m "feat(web): wire kb api + routes into AppProvider/PrototypeProvider"
```

---

## Task 5: hooks/kb.ts public façade

**Files:**
- Create: `web/src/hooks/kb.ts`

- [ ] **Step 1: Create the façade**

`web/src/hooks/kb.ts`:

```ts
/**
 * Public, provider-aware KB hooks. Each is a one-liner that reads
 * `useData().kb` and forwards. Compositions in `features/kb/` import
 * only from this file.
 */
import { useData } from '@/design-system/providers/DataContext';

export const useKbRoutes = () => useData().useKbRoutes();

export const useKBList: ReturnType<typeof useData>['kb']['useKBList'] = (
  params,
) => useData().kb.useKBList(params);

export const useKBSearch: ReturnType<typeof useData>['kb']['useKBSearch'] = (
  q,
  params,
) => useData().kb.useKBSearch(q, params);

export const useKBEntry: ReturnType<typeof useData>['kb']['useKBEntry'] = (
  entrySlug,
) => useData().kb.useKBEntry(entrySlug);

export const useAddKBEntry: ReturnType<typeof useData>['kb']['useAddKBEntry'] = () =>
  useData().kb.useAddKBEntry();
```

- [ ] **Step 2: Type-check**

Run: `cd web && npx tsc --noEmit`

Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
git add web/src/hooks/kb.ts
git commit -m "feat(web): hooks/kb.ts public façade"
```

---

## Task 6: features/kb/strings.ts

**Files:**
- Create: `web/src/features/kb/strings.ts`

- [ ] **Step 1: Create the copy module**

`web/src/features/kb/strings.ts`:

```ts
export const KB_STRINGS = {
  pageTitle: 'Knowledge base',
  searchPlaceholder: 'Search entries…',
  composeButton: 'Compose…',
  emptyListTitle: 'No entries',
  emptyListBody: 'No KB entries match the current filters.',
  emptySearchTitle: 'No matches',
  emptySearchBody: 'No entries match that search.',
  drawerLoading: 'Loading entry…',
  filterAll: 'All',
  filterTypes: 'Types',
  filterTags: 'Tags',
  authoredBy: (agent: string) => `Authored by ${agent}`,
  sourceTaskLabel: 'Source task:',
  relatedEntriesLabel: 'Related entries:',
  composeDialogTitle: 'Compose KB entry',
  composeDialogSubmit: 'Add entry',
  composeDialogCancel: 'Cancel',
};
```

- [ ] **Step 2: Commit**

```bash
git add web/src/features/kb/strings.ts
git commit -m "feat(web): kb strings module"
```

---

## Task 7: KbEntryCard component

**Files:**
- Create: `web/src/features/kb/KbEntryCard.tsx`

- [ ] **Step 1: Create the card**

`web/src/features/kb/KbEntryCard.tsx`:

```tsx
import { Link } from 'react-router-dom';
import { cn } from '@/lib/utils';
import type { KBEntry } from '@/lib/api/kb';

type Density = 'comfortable' | 'compact';

function relativeAge(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const min = Math.round(ms / 60000);
  if (min < 1) return 'just now';
  if (min < 60) return `${min}m`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h`;
  const d = Math.round(hr / 24);
  return `${d}d`;
}

export interface KbEntryCardProps {
  entry: KBEntry;
  to: string;
  active?: boolean;
  density?: Density;
}

export function KbEntryCard({
  entry,
  to,
  active,
  density = 'comfortable',
}: KbEntryCardProps): JSX.Element {
  const pad = density === 'compact' ? 'p-2' : 'p-3';
  return (
    <Link
      to={to}
      className={cn(
        'border-border-subtle bg-surface-raised block rounded-lg border',
        pad,
        active && 'ring-accent ring-2',
        'hover:bg-surface-raised/80',
      )}
    >
      <div className="text-fg-muted font-mono text-xs">{entry.slug}</div>
      <div className="text-fg mt-0.5 flex items-baseline gap-2">
        <span className="font-medium">{entry.title}</span>
        <span className="text-fg-muted text-xs">· {entry.type}</span>
        <span className="text-fg-muted text-xs">· {relativeAge(entry.updated_at)}</span>
      </div>
      {density === 'comfortable' && entry.tags.length > 0 && (
        <div className="text-fg-muted mt-1 text-xs">{entry.tags.join(' · ')}</div>
      )}
    </Link>
  );
}
```

- [ ] **Step 2: Type-check**

Run: `cd web && npx tsc --noEmit`

Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
git add web/src/features/kb/KbEntryCard.tsx
git commit -m "feat(web): KbEntryCard list row"
```

---

## Task 8: KbEntryDetailPane (Drawer)

**Files:**
- Create: `web/src/features/kb/KbEntryDetailPane.tsx`

- [ ] **Step 1: Create the drawer**

`web/src/features/kb/KbEntryDetailPane.tsx`:

```tsx
import { Link, useNavigate } from 'react-router-dom';
import {
  Drawer,
  DrawerContent,
  DrawerTitle,
} from '@/design-system/primitives/Drawer';
import { IdBadge } from '@/design-system/patterns/IdBadge';
import { Markdown } from '@/design-system/patterns/Markdown';
import { useKBEntry, useKbRoutes } from '@/hooks/kb';
import { useTasksRoutes } from '@/hooks/tasks';
import { KB_STRINGS } from './strings';

function relativeAge(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const min = Math.round(ms / 60000);
  if (min < 1) return 'just now';
  if (min < 60) return `${min}m`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h`;
  const d = Math.round(hr / 24);
  return `${d}d`;
}

export function KbEntryDetailPane({ entrySlug }: { entrySlug: string }): JSX.Element {
  const navigate = useNavigate();
  const kbRoutes = useKbRoutes();
  const tasksRoutes = useTasksRoutes();
  const entryQuery = useKBEntry(entrySlug);
  const onClose = () => navigate(kbRoutes.inbox());
  const entry = entryQuery.data;

  return (
    <Drawer open onOpenChange={(o) => !o && onClose()}>
      <DrawerContent className="flex flex-col">
        <header className="border-border-subtle border-b p-4">
          <div className="text-fg-muted font-mono text-xs">{entrySlug}</div>
          <DrawerTitle className="text-fg mt-1 text-lg">
            {entry?.title ?? KB_STRINGS.drawerLoading}
          </DrawerTitle>
          {entry && (
            <p className="text-fg-muted mt-1 text-xs">
              {entry.type} · updated {relativeAge(entry.updated_at)} ·{' '}
              {KB_STRINGS.authoredBy(entry.authored_by)}
            </p>
          )}
          {entry && entry.tags.length > 0 && (
            <p className="text-fg-muted mt-1 text-xs">
              {KB_STRINGS.filterTags}: {entry.tags.join(', ')}
            </p>
          )}
        </header>
        <section className="flex-1 overflow-y-auto p-4">
          {entry ? (
            <Markdown body={entry.body} />
          ) : (
            <p className="text-fg-muted text-xs">{KB_STRINGS.drawerLoading}</p>
          )}
          {entry?.source_task && (
            <p className="text-fg-muted mt-6 text-xs">
              {KB_STRINGS.sourceTaskLabel}{' '}
              <IdBadge
                kind="task"
                id={entry.source_task}
                to={tasksRoutes.detail(entry.source_task)}
              />
            </p>
          )}
          {entry && entry.related_entries.length > 0 && (
            <div className="text-fg-muted mt-3 text-xs">
              <div>{KB_STRINGS.relatedEntriesLabel}</div>
              <ul className="mt-1 list-disc pl-5">
                {entry.related_entries.map((slug) => (
                  <li key={slug}>
                    <Link to={kbRoutes.detail(slug)} className="text-accent hover:underline">
                      {slug}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      </DrawerContent>
    </Drawer>
  );
}
```

- [ ] **Step 2: Type-check**

Run: `cd web && npx tsc --noEmit`

Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
git add web/src/features/kb/KbEntryDetailPane.tsx
git commit -m "feat(web): KbEntryDetailPane drawer"
```

---

## Task 9: Write the failing KbPage test (read path)

**Files:**
- Create: `web/src/features/kb/KbPage.test.tsx`

- [ ] **Step 1: Write the test**

`web/src/features/kb/KbPage.test.tsx`:

```tsx
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'hk-macau-tourism';

const ENTRY_A = {
  slug: 'policy/refund-thresholds',
  title: 'Refund authority by tier',
  type: 'precedent',
  topic: 'finance',
  tags: ['policy', 'finance', 'customer-care'],
  body: '# Refund authority\n\nThe CX Manager may approve refunds up to $150.',
  updated_at: '2026-05-16T09:00:00Z',
  authored_by: 'founder',
  source_task: 'TASK-0042',
  related_entries: ['intake/spanish-walk-ins'],
};

const ENTRY_B = {
  slug: 'intake/spanish-walk-ins',
  title: 'Spanish-speaking walk-in flow',
  type: 'sop',
  topic: 'intake',
  tags: ['intake'],
  body: '# Spanish walk-ins',
  updated_at: '2026-05-12T11:00:00Z',
  authored_by: 'intake_manager',
  source_task: null,
  related_entries: [],
};

function stubBase() {
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/kb`, () =>
      HttpResponse.json({ entries: [ENTRY_A, ENTRY_B] }),
    ),
  );
}

describe('KbPage — read path', () => {
  test('renders entries from /kb', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBase();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await waitFor(() => {
      expect(screen.getByText(/Refund authority by tier/)).toBeInTheDocument();
      expect(screen.getByText(/Spanish-speaking walk-in flow/)).toBeInTheDocument();
    });
  });

  test('filters by type via server param', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    let serverParams: string | null = null;
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb`, ({ request }) => {
        const url = new URL(request.url);
        serverParams = url.searchParams.get('type');
        const all = [ENTRY_A, ENTRY_B];
        const filtered = serverParams
          ? all.filter((e) => e.type === serverParams)
          : all;
        return HttpResponse.json({ entries: filtered });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByText(/Refund authority by tier/);
    await user.click(screen.getByRole('button', { name: /^precedent$/ }));
    await waitFor(() => expect(serverParams).toBe('precedent'));
    await waitFor(() =>
      expect(screen.queryByText(/Spanish-speaking walk-in flow/)).not.toBeInTheDocument(),
    );
  });

  test('client-side tag filter narrows the list', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBase();
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByText(/Refund authority by tier/);
    await user.click(screen.getByRole('button', { name: /^intake$/ }));
    await waitFor(() =>
      expect(screen.queryByText(/Refund authority by tier/)).not.toBeInTheDocument(),
    );
    expect(screen.getByText(/Spanish-speaking walk-in flow/)).toBeInTheDocument();
  });

  test('opens drawer with markdown + source-task badge linking to /tasks', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBase();
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/kb/policy/refund-thresholds`, () =>
        HttpResponse.json(ENTRY_A),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await user.click(await screen.findByText(/Refund authority by tier/));
    await waitFor(() =>
      expect(screen.getByText(/CX Manager may approve refunds/)).toBeInTheDocument(),
    );
    const badge = screen.getByText('TASK-0042');
    expect(badge.closest('a')).toHaveAttribute(
      'href',
      `/orgs/${SLUG}/tasks/TASK-0042`,
    );
  });

  test('search box switches active query to /kb/search', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    let searchHit = false;
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb`, () =>
        HttpResponse.json({ entries: [ENTRY_A, ENTRY_B] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb/search`, () => {
        searchHit = true;
        return HttpResponse.json({ entries: [ENTRY_A] });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByText(/Refund authority by tier/);
    await user.type(screen.getByPlaceholderText(/Search entries/i), 'refund');
    await waitFor(() => expect(searchHit).toBe(true), { timeout: 2000 });
  });
});
```

- [ ] **Step 2: Run — expect failure**

Run: `cd web && npm test -- --run KbPage.test.tsx`

Expected: every test fails (`EmptyState` placeholder still rendered; "Refund authority by tier" not found, etc.).

- [ ] **Step 3: Commit (red)**

```bash
git add web/src/features/kb/KbPage.test.tsx
git commit -m "test(web): KbPage failing read-path tests"
```

---

## Task 10: Implement KbPage to make the tests pass

**Files:**
- Modify (overwrite): `web/src/features/kb/KbPage.tsx`
- Modify: `web/src/routes.tsx`

- [ ] **Step 1: Add the kb detail child route**

In `web/src/routes.tsx`, immediately after the existing `<Route path="kb" element={<KbPage />} />`, add the detail variant:

```tsx
<Route path="kb" element={<KbPage />} />
<Route path="kb/:entry_slug" element={<KbPage />} />
```

- [ ] **Step 2: Overwrite the placeholder KbPage**

`web/src/features/kb/KbPage.tsx`:

```tsx
import { useDeferredValue, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { FilterSidebar, type FilterGroup } from '@/design-system/patterns/FilterSidebar';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { Input } from '@/design-system/primitives/Input';
import { useDensity } from '@/hooks/density';
import { useKBList, useKBSearch, useKbRoutes } from '@/hooks/kb';
import { KbEntryCard } from './KbEntryCard';
import { KbEntryDetailPane } from './KbEntryDetailPane';
import { KB_STRINGS } from './strings';

export function KbPage(): JSX.Element {
  const { entry_slug: openSlug } = useParams<{ entry_slug?: string }>();
  const [filters, setFilters] = useState<Record<string, string | null>>({
    type: null,
    tag: null,
  });
  const [searchInput, setSearchInput] = useState('');
  const deferredQ = useDeferredValue(searchInput.trim());
  const { density } = useDensity();
  const routes = useKbRoutes();

  const listQuery = useKBList(filters.type ? { type: filters.type } : undefined);
  const searchQuery = useKBSearch(deferredQ);
  const isSearching = deferredQ.length > 0;

  const rawEntries = isSearching
    ? (searchQuery.data?.entries ?? [])
    : (listQuery.data?.entries ?? []);

  const entries = useMemo(
    () =>
      filters.tag
        ? rawEntries.filter((e) => e.tags.includes(filters.tag as string))
        : rawEntries,
    [rawEntries, filters.tag],
  );

  // Sidebar option lists derive from the server-returned set (rawEntries),
  // BEFORE the client-side tag filter — so toggling a tag does not collapse
  // the Tag rail. Same shape as TasksPage's team filter.
  const types = useMemo(() => {
    const set = new Set<string>();
    rawEntries.forEach((e) => set.add(e.type));
    return [...set].sort();
  }, [rawEntries]);
  const tags = useMemo(() => {
    const set = new Set<string>();
    rawEntries.forEach((e) => e.tags.forEach((t) => set.add(t)));
    return [...set].sort();
  }, [rawEntries]);

  const groups: FilterGroup[] = [
    {
      key: 'type',
      label: KB_STRINGS.filterTypes,
      options: types.map((t) => ({ value: t, label: t })),
    },
    {
      key: 'tag',
      label: KB_STRINGS.filterTags,
      options: tags.map((t) => ({ value: t, label: t })),
    },
  ];

  const loading = isSearching ? searchQuery.isLoading : listQuery.isLoading;

  return (
    <div className="flex h-full">
      <aside className="border-border-subtle bg-surface-sunken w-60 shrink-0 overflow-y-auto border-r p-3">
        <div className="mb-3">
          <Input
            aria-label="Search KB entries"
            placeholder={KB_STRINGS.searchPlaceholder}
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
          />
        </div>
        <FilterSidebar groups={groups} value={filters} onChange={setFilters} />
      </aside>
      <main className="bg-surface-canvas flex-1 overflow-y-auto p-4">
        <h1 className="text-fg mb-3 text-lg font-semibold">{KB_STRINGS.pageTitle}</h1>
        {loading ? (
          <p className="text-fg-muted">Loading…</p>
        ) : entries.length === 0 ? (
          <EmptyState
            title={isSearching ? KB_STRINGS.emptySearchTitle : KB_STRINGS.emptyListTitle}
            body={isSearching ? KB_STRINGS.emptySearchBody : KB_STRINGS.emptyListBody}
          />
        ) : (
          <ul className="space-y-2">
            {entries.map((e) => (
              <li key={e.slug}>
                <KbEntryCard
                  entry={e}
                  to={routes.detail(e.slug)}
                  active={openSlug === e.slug}
                  density={density}
                />
              </li>
            ))}
          </ul>
        )}
      </main>
      {openSlug && <KbEntryDetailPane entrySlug={openSlug} />}
    </div>
  );
}
```

- [ ] **Step 3: Run tests**

Run: `cd web && npm test -- --run KbPage.test.tsx`

Expected: all 5 tests pass.

- [ ] **Step 4: Run the full unit suite**

Run: `cd web && npm test -- --run`

Expected: no regressions.

- [ ] **Step 5: Commit (green)**

```bash
git add web/src/features/kb/KbPage.tsx web/src/routes.tsx
git commit -m "feat(web): KbPage list + filter sidebar + drawer detail"
```

---

## Task 11: Wire `g k` jump-key in TopBar

**Files:**
- Modify: `web/src/design-system/layouts/AppShell/TopBar.tsx`

- [ ] **Step 1: Add jump-key**

In `web/src/design-system/layouts/AppShell/TopBar.tsx`, add to the imports:

```ts
import { useKbRoutes } from '@/hooks/kb';
```

Inside the `TopBar` function, after the existing `useGlobalJump('t', …)` call:

```ts
  const kbRoutes = useKbRoutes();
  useGlobalJump('k', () => {
    if (activeSlug && !isPrototype) navigate(kbRoutes.inboxForOrg(activeSlug));
  });
```

- [ ] **Step 2: Type-check + run tests**

Run: `cd web && npx tsc --noEmit && npm test -- --run`

Expected: 0 type errors, all tests pass.

- [ ] **Step 3: Commit**

```bash
git add web/src/design-system/layouts/AppShell/TopBar.tsx
git commit -m "feat(web): register g k jump-key for KB"
```

---

## Task 12: Flag-gated compose-entry write path — failing test

**Files:**
- Create: `web/src/features/kb/write-path.test.tsx`

- [ ] **Step 1: Write the test**

`web/src/features/kb/write-path.test.tsx`:

```tsx
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterAll, beforeAll, describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'hk-macau-tourism';
const FLAG_ON = import.meta.env.VITE_ENABLE_KB_COMPOSE === 'true';

beforeAll(() => {
  // The flag is read at module-load time; tests are run with it on via
  // VITE_ENABLE_KB_COMPOSE=true (set in package.json's test:write-path
  // script, or by the CI matrix). When unset, the suite is skipped.
});
afterAll(() => {
  // no-op
});

(FLAG_ON ? describe : describe.skip)('KB compose write path', () => {
  test('submits POST /kb and navigates to detail', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    let postedBody: Record<string, unknown> | null = null;
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb`, () =>
        HttpResponse.json({ entries: [] }),
      ),
      http.post(`/api/v1/orgs/${SLUG}/kb`, async ({ request }) => {
        postedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          slug: postedBody.slug,
          updated_at: '2026-05-19T12:00:00Z',
        });
      }),
      http.get(`/api/v1/orgs/${SLUG}/kb/policy/new-rule`, () =>
        HttpResponse.json({
          slug: 'policy/new-rule',
          title: 'A new rule',
          type: 'precedent',
          topic: 'policy',
          tags: ['policy'],
          body: 'Body here',
          updated_at: '2026-05-19T12:00:00Z',
          authored_by: 'founder',
          source_task: null,
          related_entries: [],
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });

    await user.click(await screen.findByRole('button', { name: /Compose…/ }));
    await user.type(screen.getByLabelText(/^Slug$/i), 'policy/new-rule');
    await user.type(screen.getByLabelText(/^Title$/i), 'A new rule');
    await user.type(screen.getByLabelText(/^Type$/i), 'precedent');
    await user.type(screen.getByLabelText(/^Topic$/i), 'policy');
    await user.type(screen.getByLabelText(/^Tags/i), 'policy');
    await user.type(screen.getByLabelText(/^Body/i), 'Body here');
    await user.click(screen.getByRole('button', { name: /Add entry/ }));

    await waitFor(() => {
      expect(postedBody).toMatchObject({
        slug: 'policy/new-rule',
        title: 'A new rule',
        type: 'precedent',
        topic: 'policy',
        tags: ['policy'],
        body: 'Body here',
        agent: 'founder',
      });
    });
  });
});

describe('KB compose write path (flag off)', () => {
  test('Compose button is absent when flag is off', async () => {
    if (FLAG_ON) return;
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb`, () =>
        HttpResponse.json({ entries: [] }),
      ),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByRole('heading', { name: /Knowledge base/ });
    expect(screen.queryByRole('button', { name: /Compose…/ })).toBeNull();
  });
});
```

- [ ] **Step 2: Run — expect failure of the flag-off test (no Compose button is the goal but we haven't gated yet — it's already absent because the component doesn't exist, so the flag-off test passes; the flag-on suite is skipped by default)**

Run: `cd web && npm test -- --run write-path.test.tsx`

Expected: the "flag off" test PASSES (no Compose button rendered yet); the FLAG_ON describe is skipped because `VITE_ENABLE_KB_COMPOSE` is unset in the default test environment.

- [ ] **Step 3: Commit**

```bash
git add web/src/features/kb/write-path.test.tsx
git commit -m "test(web): KB compose write-path test (flag-gated)"
```

---

## Task 13: Implement ComposeKbEntryDialog and gate it in KbPage

**Files:**
- Create: `web/src/features/kb/ComposeKbEntryDialog.tsx`
- Modify: `web/src/features/kb/KbPage.tsx`
- Modify: `web/package.json` (add a test:write-path script that flips the flag on)

- [ ] **Step 1: Create the dialog**

`web/src/features/kb/ComposeKbEntryDialog.tsx`:

```tsx
import { useId, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import { Button } from '@/design-system/primitives/Button';
import { Input } from '@/design-system/primitives/Input';
import { Label } from '@/design-system/primitives/Label';
import { Textarea } from '@/design-system/primitives/Textarea';
import { useAddKBEntry, useKbRoutes } from '@/hooks/kb';
import { KB_STRINGS } from './strings';

export function ComposeKbEntryDialog({
  onClose,
}: {
  onClose: () => void;
}): JSX.Element {
  const navigate = useNavigate();
  const routes = useKbRoutes();
  const mutation = useAddKBEntry();
  const slugId = useId();
  const titleId = useId();
  const typeId = useId();
  const topicId = useId();
  const tagsId = useId();
  const bodyId = useId();
  const sourceTaskId = useId();
  const relatedId = useId();
  const [slug, setSlug] = useState('');
  const [title, setTitle] = useState('');
  const [type, setType] = useState('');
  const [topic, setTopic] = useState('');
  const [tags, setTags] = useState('');
  const [body, setBody] = useState('');
  const [sourceTask, setSourceTask] = useState('');
  const [related, setRelated] = useState('');

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const splitCsv = (v: string) =>
      v
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean);
    const result = await mutation.mutateAsync({
      slug,
      title,
      type,
      topic,
      body,
      agent: 'founder',
      tags: splitCsv(tags),
      related_entries: splitCsv(related),
      source_task: sourceTask || undefined,
    });
    onClose();
    navigate(routes.detail(result.slug));
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{KB_STRINGS.composeDialogTitle}</DialogTitle>
        </DialogHeader>
        <form className="space-y-3" onSubmit={onSubmit}>
          <div>
            <Label htmlFor={slugId}>Slug</Label>
            <Input id={slugId} value={slug} onChange={(e) => setSlug(e.target.value)} required />
          </div>
          <div>
            <Label htmlFor={titleId}>Title</Label>
            <Input id={titleId} value={title} onChange={(e) => setTitle(e.target.value)} required />
          </div>
          <div>
            <Label htmlFor={typeId}>Type</Label>
            <Input id={typeId} value={type} onChange={(e) => setType(e.target.value)} required />
          </div>
          <div>
            <Label htmlFor={topicId}>Topic</Label>
            <Input id={topicId} value={topic} onChange={(e) => setTopic(e.target.value)} required />
          </div>
          <div>
            <Label htmlFor={tagsId}>Tags (comma-separated)</Label>
            <Input id={tagsId} value={tags} onChange={(e) => setTags(e.target.value)} />
          </div>
          <div>
            <Label htmlFor={bodyId}>Body (Markdown)</Label>
            <Textarea id={bodyId} value={body} onChange={(e) => setBody(e.target.value)} rows={8} required />
          </div>
          <div>
            <Label htmlFor={sourceTaskId}>Source task (optional, e.g. TASK-0042)</Label>
            <Input id={sourceTaskId} value={sourceTask} onChange={(e) => setSourceTask(e.target.value)} />
          </div>
          <div>
            <Label htmlFor={relatedId}>Related entries (comma-separated slugs)</Label>
            <Input id={relatedId} value={related} onChange={(e) => setRelated(e.target.value)} />
          </div>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={onClose}>
              {KB_STRINGS.composeDialogCancel}
            </Button>
            <Button type="submit" disabled={mutation.isPending}>
              {KB_STRINGS.composeDialogSubmit}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 2: Gate the Compose button in KbPage**

In `web/src/features/kb/KbPage.tsx`, add at the top of the file:

```ts
import { ComposeKbEntryDialog } from './ComposeKbEntryDialog';
import { Button } from '@/design-system/primitives/Button';

const COMPOSE_ENABLED = import.meta.env.VITE_ENABLE_KB_COMPOSE === 'true';
```

Inside the `KbPage` function body, declare local dialog state:

```ts
  const [composeOpen, setComposeOpen] = useState(false);
```

Change the `<h1>` line inside `<main>` to a flex header row:

```tsx
        <div className="mb-3 flex items-center justify-between">
          <h1 className="text-fg text-lg font-semibold">{KB_STRINGS.pageTitle}</h1>
          {COMPOSE_ENABLED && (
            <Button size="sm" onClick={() => setComposeOpen(true)}>
              {KB_STRINGS.composeButton}
            </Button>
          )}
        </div>
```

And at the end of the page (sibling of `KbEntryDetailPane`):

```tsx
      {composeOpen && <ComposeKbEntryDialog onClose={() => setComposeOpen(false)} />}
```

- [ ] **Step 3: Add the flag-on test script**

Edit `web/package.json` `"scripts"` block and append:

```json
"test:write-path": "VITE_ENABLE_KB_COMPOSE=true vitest run src/features/kb/write-path.test.tsx"
```

- [ ] **Step 4: Run the flag-off test (default)**

Run: `cd web && npm test -- --run write-path.test.tsx`

Expected: "flag off" test passes (Compose button absent because env var is unset). FLAG_ON describe stays skipped.

- [ ] **Step 5: Run the flag-on test**

Run: `cd web && npm run test:write-path`

Expected: the FLAG_ON describe runs and passes. The "flag off" test bails out via `if (FLAG_ON) return;` at its head.

- [ ] **Step 6: Run the full unit suite to confirm no regression**

Run: `cd web && npm test -- --run`

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add web/src/features/kb/ComposeKbEntryDialog.tsx web/src/features/kb/KbPage.tsx web/package.json
git commit -m "feat(web): flag-gated KB compose dialog (VITE_ENABLE_KB_COMPOSE)"
```

---

## Task 14: Update UI_SPEC.md §9 to as-built

**Files:**
- Modify: `web/UI_SPEC.md` §9

- [ ] **Step 1: Replace the §9 "placeholder shell" subsection**

Find the section heading in `web/UI_SPEC.md`:

```
## 9. KB — placeholder shell
```

Replace the entire §9 block (from that heading down to the next `## 10.` heading) with:

```markdown
## 9. KB — as built (PR 10)

### Purpose

Browse and read knowledge-base entries. Read-only by default. A flag-gated compose dialog lets the founder add entries without dropping to the CLI.

### Layout

- 240px left rail = search input + `FilterSidebar` (Types + Tags, derived from the unfiltered entry list).
- Canvas = `KbEntryCard` rows (slug, title, type, age, tags in comfortable density).
- Drawer detail at `/orgs/:slug/kb/:entry_slug` — markdown body via `<Markdown>`, source-task `IdBadge` linking to `/orgs/:slug/tasks/:task_id`, related-entry list.

### Filters

- **Type** = server-side `?type=` on `GET /kb`.
- **Tag** = client-side single-tag filter on the result set.
- **Search** = non-empty input switches the active query to `GET /kb/search?q=…`, debounced by `useDeferredValue`.

### Compose (flag-gated)

`VITE_ENABLE_KB_COMPOSE=true` shows a `[Compose…]` button next to the page title. The dialog hardcodes `agent: "founder"` and calls `POST /kb`. Edits, deletes, and reindex stay CLI-only.

### Jump-key

`g k` from anywhere navigates to `/orgs/<active-slug>/kb` (registered in `TopBar`).
```

- [ ] **Step 2: Commit**

```bash
git add web/UI_SPEC.md
git commit -m "docs(web): UI_SPEC §9 — KB as built (PR 10)"
```

---

## Task 15: Final verification

- [ ] **Step 1: Run the full unit suite**

Run: `cd web && npm test -- --run`

Expected: all tests pass.

- [ ] **Step 2: Run the flag-on suite**

Run: `cd web && npm run test:write-path`

Expected: write-path test passes.

- [ ] **Step 3: Type-check**

Run: `cd web && npx tsc --noEmit`

Expected: 0 errors.

- [ ] **Step 4: Lint**

Run: `cd web && npm run lint`

Expected: 0 errors.

- [ ] **Step 5: Build**

Run: `scripts/build_web.sh`

Expected: build succeeds, `web/dist/` produced.

- [ ] **Step 6: Manual smoke test**

```bash
scripts/daemon.sh start
happyranch web
```

In the browser at the KB tab:
- list renders entries;
- click a type pill, then a tag pill — list narrows correctly;
- typing in search switches to /kb/search results;
- clicking a row opens the Drawer with markdown body + source-task link;
- pressing `g k` from another nav tab jumps back to KB.

Report each as PASS or FAIL — do not claim success without running the browser. If UI-only manual testing is not possible in the agent environment, say so explicitly.

- [ ] **Step 7: Confirm the contract test is still happy**

Run: `cd web && npm test -- --run openapi-coverage`

Expected: passes. All 7 KB paths are already in `INCLUDED_PATHS`; we didn't touch the contract.

---

## Self-review notes

- **Spec coverage:** Every §3–§7 requirement of `2026-05-19-web-kb-surface-design.md` is implemented by a task above. Routing (§5.5) lands in Task 10; jump-key (§5.6) in Task 11; compose (§5.4) in Tasks 12–13; UI_SPEC update (§7 of the spec, implied) in Task 14.
- **Type consistency:** `KbApi` member names (`useKBList`, `useKBSearch`, `useKBEntry`, `useAddKBEntry`) are reused verbatim in `_real-kb.ts`, `_mock-kb.ts`, `hooks/kb.ts`, and all callers. `KbRoutes` member names (`inbox`, `detail`, `inboxForOrg`) match `TasksRoutes` and `ThreadRoutes`. The dialog hardcoded agent (`'founder'`) is consistent across spec §5.4 and Task 13 code.
- **Tests:** Each major behavior has a test before the implementation step (Tasks 9 → 10, 12 → 13). The flag-gated compose test handles both flag states deterministically.

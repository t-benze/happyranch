# Web Tasks Feature Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Tasks surface (PR 7 of the umbrella) — inbox with filters, detail pane in a Drawer with recall tree + live event tail, and lifecycle action dialogs (cancel / revisit / resolve-escalation). Earn the cross-cutting infrastructure (Drawer primitive, density hook, jump-key hook) for every future PR.

**Architecture:** Follow the threads-feature template strictly. Three-layer split: `lib/api/tasks.ts` (1:1 daemon mirror, already exists) → provider-aware hooks in `_real-tasks.ts` + `_mock-tasks.ts` → `features/tasks/` composition. Per-task tail SSE for live event streaming; inbox refreshes via TanStack-Query polling (every 10s) — no new daemon-side event topic. Drawer-not-dialog for task detail because detail is "look at info in context", not "give me input and act".

**Drift from umbrella `2026-05-18-web-app-complete-feature-set-design.md`:** §5.5 claimed `/tasks/events` (inbox SSE) would land here for a total of 2 new streams. This plan instead **polls** the inbox at 10s (saves a daemon-side event-publish wiring chore for PR 12/Dashboard if it proves needed). Net new streams in PR 7: **1** (per-task tail). The cap of 4 streams stands.

**Tech Stack:** React 18 + TypeScript strict + Tailwind 3 + TanStack Query v5 + React Router v6 + `@microsoft/fetch-event-source`. Tests: Vitest + React Testing Library + MSW.

---

## File Map

**New files (frontend):**

```
web/src/design-system/primitives/Drawer.tsx
web/src/design-system/primitives/Drawer.test.tsx
web/src/design-system/patterns/FilterSidebar.tsx
web/src/design-system/patterns/TaskCard.tsx
web/src/design-system/providers/_real-tasks.ts
web/src/design-system/providers/_real-routes.ts          # (modify, add useRealTasksRoutes)
web/src/design-system/providers/_mock-tasks.ts
web/src/hooks/density.ts
web/src/hooks/density.test.ts
web/src/hooks/global-jump.ts
web/src/hooks/global-jump.test.ts
web/src/hooks/tasks.ts
web/src/features/tasks/TaskDetailPane.tsx
web/src/features/tasks/TaskRecallTree.tsx
web/src/features/tasks/TaskEventsLog.tsx
web/src/features/tasks/CancelTaskDialog.tsx
web/src/features/tasks/RevisitTaskDialog.tsx
web/src/features/tasks/ResolveEscalationDialog.tsx
web/src/features/tasks/strings.ts
web/src/features/tasks/TasksPage.test.tsx
web/src/features/tasks/write-path.test.tsx
```

**Modified files (frontend):**

```
web/src/design-system/providers/DataContext.ts           # add TasksApi + TasksRoutes
web/src/design-system/providers/AppProvider.tsx          # wire real tasks api
web/src/design-system/providers/PrototypeProvider.tsx    # wire mock tasks api
web/src/design-system/layouts/AppShell/TopBar.tsx        # enable Tasks tab
web/src/design-system/patterns/IdBadge.tsx               # make TASK-NNN navigable
web/src/features/tasks/TasksPage.tsx                     # replace placeholder
web/src/routes.tsx                                       # add /orgs/:slug/tasks/:task_id
web/src/lib/api/types.ts                                 # TaskEvent shape
web/src/lib/api/tasks.ts                                 # (no changes — every route already mirrored)
web/UI_SPEC.md                                           # §8 updated with implementation notes
```

**Modified files (Python):** _none_. The Tasks plan is frontend-only.

---

## Task 1: Drawer primitive

**Files:**
- Create: `web/src/design-system/primitives/Drawer.tsx`
- Create: `web/src/design-system/primitives/Drawer.test.tsx`

The Drawer is a Radix `Dialog`-backed slide-in from the right, 480px wide, non-modal-feel (the rest of the page stays visible behind a thin scrim). Reuses the `Dialog` primitive's portal + escape handling.

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/design-system/primitives/Drawer.test.tsx
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Drawer, DrawerContent, DrawerTitle } from './Drawer';

describe('Drawer', () => {
  it('renders content when open', () => {
    render(
      <Drawer open onOpenChange={() => {}}>
        <DrawerContent>
          <DrawerTitle>Detail</DrawerTitle>
          <p>body</p>
        </DrawerContent>
      </Drawer>,
    );
    expect(screen.getByText('Detail')).toBeInTheDocument();
    expect(screen.getByText('body')).toBeInTheDocument();
  });

  it('does not render content when closed', () => {
    render(
      <Drawer open={false} onOpenChange={() => {}}>
        <DrawerContent>
          <DrawerTitle>Detail</DrawerTitle>
        </DrawerContent>
      </Drawer>,
    );
    expect(screen.queryByText('Detail')).toBeNull();
  });

  it('calls onOpenChange on escape', () => {
    const onOpenChange = vi.fn();
    render(
      <Drawer open onOpenChange={onOpenChange}>
        <DrawerContent>
          <DrawerTitle>Detail</DrawerTitle>
        </DrawerContent>
      </Drawer>,
    );
    fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

```
cd web && npx vitest run src/design-system/primitives/Drawer.test.tsx
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement the Drawer**

```tsx
// web/src/design-system/primitives/Drawer.tsx
import * as DialogPrimitive from '@radix-ui/react-dialog';
import { forwardRef, type ComponentPropsWithoutRef, type ElementRef } from 'react';
import { cn } from '@/lib/utils';

export const Drawer = DialogPrimitive.Root;
export const DrawerTrigger = DialogPrimitive.Trigger;
export const DrawerClose = DialogPrimitive.Close;
export const DrawerTitle = DialogPrimitive.Title;
export const DrawerDescription = DialogPrimitive.Description;

export const DrawerContent = forwardRef<
  ElementRef<typeof DialogPrimitive.Content>,
  ComponentPropsWithoutRef<typeof DialogPrimitive.Content>
>(({ className, children, ...props }, ref) => (
  <DialogPrimitive.Portal>
    <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-bg/40" />
    <DialogPrimitive.Content
      ref={ref}
      className={cn(
        'fixed right-0 top-0 z-50 flex h-full w-[480px] flex-col',
        'bg-surface-raised border-l border-border-subtle shadow-xl',
        'data-[state=open]:animate-in data-[state=closed]:animate-out',
        'data-[state=closed]:slide-out-to-right data-[state=open]:slide-in-from-right',
        className,
      )}
      {...props}
    >
      {children}
    </DialogPrimitive.Content>
  </DialogPrimitive.Portal>
));
DrawerContent.displayName = 'DrawerContent';
```

- [ ] **Step 4: Run the test to verify it passes**

```
cd web && npx vitest run src/design-system/primitives/Drawer.test.tsx
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/design-system/primitives/Drawer.tsx web/src/design-system/primitives/Drawer.test.tsx
git commit -m "feat(web): Drawer primitive for in-context detail panes"
```

---

## Task 2: `useDensity` hook

**Files:**
- Create: `web/src/hooks/density.ts`
- Create: `web/src/hooks/density.test.ts`

`localStorage["grassland.density"]` ∈ `{"comfortable", "compact"}`. Default comfortable.

- [ ] **Step 1: Write the failing test**

```ts
// web/src/hooks/density.test.ts
import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useDensity } from './density';

describe('useDensity', () => {
  beforeEach(() => localStorage.clear());

  it('defaults to comfortable', () => {
    const { result } = renderHook(() => useDensity());
    expect(result.current.density).toBe('comfortable');
  });

  it('reads persisted value', () => {
    localStorage.setItem('grassland.density', 'compact');
    const { result } = renderHook(() => useDensity());
    expect(result.current.density).toBe('compact');
  });

  it('persists on toggle', () => {
    const { result } = renderHook(() => useDensity());
    act(() => result.current.setDensity('compact'));
    expect(result.current.density).toBe('compact');
    expect(localStorage.getItem('grassland.density')).toBe('compact');
  });

  it('ignores invalid persisted value', () => {
    localStorage.setItem('grassland.density', 'garbage');
    const { result } = renderHook(() => useDensity());
    expect(result.current.density).toBe('comfortable');
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

```
cd web && npx vitest run src/hooks/density.test.ts
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement the hook**

```ts
// web/src/hooks/density.ts
import { useCallback, useState } from 'react';

export type Density = 'comfortable' | 'compact';
const KEY = 'grassland.density';

function readInitial(): Density {
  const v = typeof window !== 'undefined' ? window.localStorage.getItem(KEY) : null;
  return v === 'compact' ? 'compact' : 'comfortable';
}

export function useDensity(): {
  density: Density;
  setDensity: (d: Density) => void;
} {
  const [density, setDensityState] = useState<Density>(readInitial);
  const setDensity = useCallback((d: Density) => {
    setDensityState(d);
    if (typeof window !== 'undefined') window.localStorage.setItem(KEY, d);
  }, []);
  return { density, setDensity };
}
```

- [ ] **Step 4: Run the test to verify it passes**

```
cd web && npx vitest run src/hooks/density.test.ts
```

Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/hooks/density.ts web/src/hooks/density.test.ts
git commit -m "feat(web): useDensity hook backed by localStorage"
```

---

## Task 3: `useGlobalJump` hook

**Files:**
- Create: `web/src/hooks/global-jump.ts`
- Create: `web/src/hooks/global-jump.test.ts`

Registers a `g <letter>` chord. 1.0s buffer between `g` and the second key. Suppressed when focus is inside `<input>`, `<textarea>`, or `[contenteditable]`.

- [ ] **Step 1: Write the failing test**

```ts
// web/src/hooks/global-jump.test.ts
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useGlobalJump } from './global-jump';

function fire(key: string) {
  window.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
}

describe('useGlobalJump', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('fires on g+t chord', () => {
    const onJump = vi.fn();
    renderHook(() => useGlobalJump('t', onJump));
    fire('g');
    fire('t');
    expect(onJump).toHaveBeenCalledTimes(1);
  });

  it('does not fire when buffer expires', () => {
    const onJump = vi.fn();
    renderHook(() => useGlobalJump('t', onJump));
    fire('g');
    vi.advanceTimersByTime(1100);
    fire('t');
    expect(onJump).not.toHaveBeenCalled();
  });

  it('does not fire when focus is in an input', () => {
    const onJump = vi.fn();
    const input = document.createElement('input');
    document.body.appendChild(input);
    input.focus();
    renderHook(() => useGlobalJump('t', onJump));
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'g', bubbles: true }));
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 't', bubbles: true }));
    expect(onJump).not.toHaveBeenCalled();
    document.body.removeChild(input);
  });

  it('does not fire on the wrong second letter', () => {
    const onJump = vi.fn();
    renderHook(() => useGlobalJump('t', onJump));
    fire('g');
    fire('k');
    expect(onJump).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

```
cd web && npx vitest run src/hooks/global-jump.test.ts
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement the hook**

```ts
// web/src/hooks/global-jump.ts
import { useEffect, useRef } from 'react';

const BUFFER_MS = 1000;

function isInEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA') return true;
  if (target.isContentEditable) return true;
  return false;
}

export function useGlobalJump(letter: string, onJump: () => void): void {
  const armedAt = useRef<number | null>(null);

  useEffect(() => {
    const handler = (ev: KeyboardEvent) => {
      if (isInEditable(ev.target)) return;
      const now = Date.now();
      if (ev.key === 'g') {
        armedAt.current = now;
        return;
      }
      if (ev.key === letter && armedAt.current !== null) {
        if (now - armedAt.current <= BUFFER_MS) {
          armedAt.current = null;
          onJump();
        } else {
          armedAt.current = null;
        }
      } else {
        armedAt.current = null;
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [letter, onJump]);
}
```

- [ ] **Step 4: Run the test to verify it passes**

```
cd web && npx vitest run src/hooks/global-jump.test.ts
```

Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/hooks/global-jump.ts web/src/hooks/global-jump.test.ts
git commit -m "feat(web): useGlobalJump hook for g-prefix chord shortcuts"
```

---

## Task 4: Task event types

**Files:**
- Modify: `web/src/lib/api/types.ts`

The tail SSE emits orchestrator events (`session_start`, `session_end`, `manager_decision`, `task_complete`, `task_failed`, `task_blocked`, etc.). We don't enumerate every type — keep it open.

- [ ] **Step 1: Add types**

Append to `web/src/lib/api/types.ts`:

```ts
// ---------------------------------------------------------------------------
// Task events (SSE tail)
// ---------------------------------------------------------------------------

export interface TaskEvent {
  type: string;
  timestamp: string;
  task_id?: string;
  agent?: string | null;
  payload?: Record<string, unknown> | null;
  [extra: string]: unknown;
}

export interface TaskRecallNode {
  task_id: string;
  team: string;
  brief: string;
  status: import('./types').TaskStatus;
  output_summary?: string | null;
  children: TaskRecallNode[];
  [extra: string]: unknown;
}
```

- [ ] **Step 2: Verify typecheck passes**

```
cd web && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add web/src/lib/api/types.ts
git commit -m "feat(web): TaskEvent + TaskRecallNode types"
```

---

## Task 5: Tasks data layer (atomic)

**Files (all in one commit so typecheck stays green):**
- Modify: `web/src/design-system/providers/DataContext.ts`
- Create: `web/src/design-system/providers/_real-tasks.ts`
- Modify: `web/src/design-system/providers/_real-routes.ts` (add `useRealTasksRoutes`)
- Modify: `web/src/design-system/providers/AppProvider.tsx`
- Create: `web/src/design-system/providers/_mock-tasks.ts`
- Modify: `web/src/design-system/providers/PrototypeProvider.tsx`

This task is intentionally larger than average — splitting it leaves `DataContextValue` requiring a `tasks` field that the providers don't yet supply, which would break typecheck between commits. Land it atomically.

- [ ] **Step 1: Extend `DataContext.ts`**

Add to `web/src/design-system/providers/DataContext.ts` (after the `ThreadsApi` block, before the OrgsApi block):

```ts
import type { tasks as tasksApi } from '@/lib/api';
import type { TaskEvent, TaskRecord, TaskRecallNode } from '@/lib/api/types';

export type CancelTaskArgs = Parameters<typeof tasksApi.cancelTask>[2];
export type CancelTaskResult = Awaited<ReturnType<typeof tasksApi.cancelTask>>;

export type RevisitTaskArgs = Parameters<typeof tasksApi.revisitTask>[2];
export type RevisitTaskResult = Awaited<ReturnType<typeof tasksApi.revisitTask>>;

export type ResolveEscalationArgs = Parameters<typeof tasksApi.resolveEscalation>[2];
export type ResolveEscalationResult = Awaited<ReturnType<typeof tasksApi.resolveEscalation>>;

export interface TasksApi {
  useTasksList: (params?: {
    status?: string;
    limit?: number;
  }) => QueryLike<{ tasks: TaskRecord[] }>;
  useTask: (taskId: string | undefined) => QueryLike<TaskRecord>;
  useTaskRecall: (taskId: string | undefined) => QueryLike<TaskRecallNode>;

  /** Subscribes; passes each event to `onEvent`. No-op under mocks. */
  useTaskTailSSE: (
    taskId: string | undefined,
    onEvent: (ev: TaskEvent) => void,
  ) => void;

  useCancelTask: (taskId: string) => MutationLike<CancelTaskArgs, CancelTaskResult>;
  useRevisitTask: (taskId: string) => MutationLike<RevisitTaskArgs, RevisitTaskResult>;
  useResolveEscalation: (
    taskId: string,
  ) => MutationLike<ResolveEscalationArgs, ResolveEscalationResult>;
}

export interface TasksRoutes {
  inbox: () => string;
  detail: (taskId: string) => string;
  inboxForOrg: (slug: string) => string;
}
```

Then in the `DataContextValue` interface, add:

```ts
  tasks: TasksApi;
  useTasksRoutes: () => TasksRoutes;
```

- [ ] **Step 2: Create the real provider**

```ts
// web/src/design-system/providers/_real-tasks.ts
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { subscribeSSE, tasks as tasksApi } from '@/lib/api';
import type { TaskEvent } from '@/lib/api/types';
import type {
  CancelTaskArgs,
  MutationLike,
  QueryLike,
  ResolveEscalationArgs,
  RevisitTaskArgs,
  TasksApi,
} from './DataContext';

function useRealOrgSlug(): string {
  const { slug } = useParams<{ slug: string }>();
  return slug ?? '';
}

function useTasksList(params?: { status?: string; limit?: number }) {
  const slug = useRealOrgSlug();
  return useQuery({
    queryKey: ['tasks', slug, params],
    queryFn: () => tasksApi.listTasks(slug, params),
    enabled: !!slug,
    refetchInterval: 10_000,
  }) as QueryLike<Awaited<ReturnType<typeof tasksApi.listTasks>>>;
}

function useTask(taskId: string | undefined) {
  const slug = useRealOrgSlug();
  return useQuery({
    queryKey: ['task', slug, taskId],
    queryFn: () => tasksApi.getTask(slug, taskId as string),
    enabled: !!slug && !!taskId,
  });
}

function useTaskRecall(taskId: string | undefined) {
  const slug = useRealOrgSlug();
  return useQuery({
    queryKey: ['task-recall', slug, taskId],
    queryFn: () => tasksApi.recallTask(slug, taskId as string),
    enabled: !!slug && !!taskId,
  });
}

function useTaskTailSSE(
  taskId: string | undefined,
  onEvent: (ev: TaskEvent) => void,
): void {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  useEffect(() => {
    if (!slug || !taskId) return;
    const ctl = new AbortController();
    subscribeSSE<TaskEvent>(tasksApi.taskEventsPath(slug, taskId), {
      signal: ctl.signal,
      onMessage: (ev) => {
        onEvent(ev);
        if (ev.type === 'task_complete' || ev.type === 'task_failed' || ev.type === 'task_blocked') {
          qc.invalidateQueries({ queryKey: ['task', slug, taskId] });
          qc.invalidateQueries({ queryKey: ['tasks', slug] });
        }
      },
    }).catch(() => { /* swallow */ });
    return () => ctl.abort();
  }, [slug, taskId, qc, onEvent]);
}

function useCancelTask(taskId: string): MutationLike<CancelTaskArgs, unknown> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CancelTaskArgs) => tasksApi.cancelTask(slug, taskId, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['task', slug, taskId] });
      qc.invalidateQueries({ queryKey: ['tasks', slug] });
    },
  });
}

function useRevisitTask(taskId: string): MutationLike<RevisitTaskArgs, unknown> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: RevisitTaskArgs) => tasksApi.revisitTask(slug, taskId, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tasks', slug] });
    },
  });
}

function useResolveEscalation(taskId: string): MutationLike<ResolveEscalationArgs, unknown> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ResolveEscalationArgs) =>
      tasksApi.resolveEscalation(slug, taskId, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['task', slug, taskId] });
      qc.invalidateQueries({ queryKey: ['tasks', slug] });
    },
  });
}

export const realTasksApi: TasksApi = {
  useTasksList,
  useTask,
  useTaskRecall,
  useTaskTailSSE,
  useCancelTask,
  useRevisitTask,
  useResolveEscalation,
};
```

- [ ] **Step 3: Add `useRealTasksRoutes` to `_real-routes.ts`**

Append to `web/src/design-system/providers/_real-routes.ts`:

```ts
import type { TasksRoutes } from './DataContext';

export function useRealTasksRoutes(): TasksRoutes {
  const slug = useOrgSlugOptional();
  return {
    detail: (taskId: string) => (slug ? `/orgs/${slug}/tasks/${taskId}` : '#'),
    inbox: () => (slug ? `/orgs/${slug}/tasks` : '#'),
    inboxForOrg: (target: string) => `/orgs/${target}/tasks`,
  };
}
```

- [ ] **Step 4: Wire into `AppProvider.tsx`**

Add imports:

```tsx
import { realTasksApi } from './_real-tasks';
import { useRealTasksRoutes } from './_real-routes';
```

Extend the `DataContext.Provider` value:

```tsx
value={{
  orgs: realOrgsApi,
  threads: realThreadsApi,
  tasks: realTasksApi,
  useThreadRoutes: useRealThreadRoutes,
  useTasksRoutes: useRealTasksRoutes,
}}
```

- [ ] **Step 5: Create the mock provider**

```ts
// web/src/design-system/providers/_mock-tasks.ts
import type { TaskRecord, TaskRecallNode } from '@/lib/api/types';
import type {
  QueryLike,
  TasksApi,
  TasksRoutes,
  MutationLike,
} from './DataContext';

const FIXTURES: TaskRecord[] = [
  {
    task_id: 'TASK-0091',
    team: 'content',
    brief: 'Draft Hong Kong visa guide v2',
    status: 'in_progress',
    block_kind: null,
    parent_task_id: null,
    revisit_of_task_id: null,
    created_at: '2026-05-18T10:00:00Z',
    updated_at: '2026-05-18T10:06:12Z',
    closed_at: null,
    cancelled_at: null,
    session_timeout_seconds: null,
  },
  {
    task_id: 'TASK-0090',
    team: 'ops',
    brief: 'Vet partner hotel candidates',
    status: 'blocked',
    block_kind: 'escalated',
    parent_task_id: null,
    revisit_of_task_id: null,
    created_at: '2026-05-18T09:00:00Z',
    updated_at: '2026-05-18T09:30:00Z',
    closed_at: null,
    cancelled_at: null,
    session_timeout_seconds: null,
  },
];

const RECALL_TREE: TaskRecallNode = {
  task_id: 'TASK-0091',
  team: 'content',
  brief: 'Draft Hong Kong visa guide v2',
  status: 'in_progress',
  output_summary: null,
  children: [
    {
      task_id: 'TASK-0092',
      team: 'content',
      brief: 'Section 4: currency policy',
      status: 'completed',
      output_summary: 'Wrote section 4 (245 words).',
      children: [],
    },
  ],
};

function ok<T>(data: T): QueryLike<T> {
  return { data, isLoading: false, isError: false, error: null };
}

function noopMutation<TArgs, TResult>(): MutationLike<TArgs, TResult> {
  return {
    mutateAsync: async () => ({}) as TResult,
    isPending: false,
  };
}

export const mockTasksApi: TasksApi = {
  useTasksList: () => ok({ tasks: FIXTURES }),
  useTask: (taskId) =>
    ok(FIXTURES.find((t) => t.task_id === taskId) ?? FIXTURES[0]),
  useTaskRecall: () => ok(RECALL_TREE),
  useTaskTailSSE: () => { /* no-op */ },
  useCancelTask: () => noopMutation(),
  useRevisitTask: () => noopMutation(),
  useResolveEscalation: () => noopMutation(),
};

export function useMockTasksRoutes(): TasksRoutes {
  return {
    inbox: () => '/__prototypes/tasks/inbox',
    detail: (taskId: string) => `/__prototypes/tasks/${taskId}`,
    inboxForOrg: () => '/__prototypes/tasks/inbox',
  };
}
```

- [ ] **Step 6: Wire into `PrototypeProvider.tsx`**

Add imports:

```tsx
import { mockTasksApi, useMockTasksRoutes } from './_mock-tasks';
```

Extend the `DataContext.Provider` value:

```tsx
value={{
  orgs: mockOrgsApi,
  threads: mockThreadsApi,
  tasks: mockTasksApi,
  useThreadRoutes: useMockThreadRoutes,
  useTasksRoutes: useMockTasksRoutes,
}}
```

- [ ] **Step 7: Verify typecheck**

```
cd web && npx tsc --noEmit
```

Expected: clean.

- [ ] **Step 8: Commit (atomic data-layer)**

```bash
git add web/src/design-system/providers/DataContext.ts \
        web/src/design-system/providers/_real-tasks.ts \
        web/src/design-system/providers/_real-routes.ts \
        web/src/design-system/providers/AppProvider.tsx \
        web/src/design-system/providers/_mock-tasks.ts \
        web/src/design-system/providers/PrototypeProvider.tsx
git commit -m "feat(web): tasks data layer (DataContext + real/mock providers)"
```

---

## Task 6: Tasks public hooks façade

**Files:**
- Create: `web/src/hooks/tasks.ts`

Provider-aware façade. One-liners that forward to `useData().tasks`. Matches `web/src/hooks/threads.ts` shape.

- [ ] **Step 1: Implement**

```ts
// web/src/hooks/tasks.ts
import { useData } from '@/design-system/providers/DataContext';

export const useTasksRoutes = () => useData().useTasksRoutes();

export const useTasksList: ReturnType<typeof useData>['tasks']['useTasksList'] = (
  params,
) => useData().tasks.useTasksList(params);

export const useTask: ReturnType<typeof useData>['tasks']['useTask'] = (
  taskId,
) => useData().tasks.useTask(taskId);

export const useTaskRecall: ReturnType<typeof useData>['tasks']['useTaskRecall'] = (
  taskId,
) => useData().tasks.useTaskRecall(taskId);

export const useTaskTailSSE: ReturnType<typeof useData>['tasks']['useTaskTailSSE'] = (
  taskId,
  onEvent,
) => useData().tasks.useTaskTailSSE(taskId, onEvent);

export const useCancelTask: ReturnType<typeof useData>['tasks']['useCancelTask'] = (
  taskId,
) => useData().tasks.useCancelTask(taskId);

export const useRevisitTask: ReturnType<typeof useData>['tasks']['useRevisitTask'] = (
  taskId,
) => useData().tasks.useRevisitTask(taskId);

export const useResolveEscalation: ReturnType<typeof useData>['tasks']['useResolveEscalation'] = (
  taskId,
) => useData().tasks.useResolveEscalation(taskId);
```

- [ ] **Step 2: Verify typecheck**

```
cd web && npx tsc --noEmit
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add web/src/hooks/tasks.ts
git commit -m "feat(web): tasks public hooks façade"
```

---

## Task 7: `FilterSidebar` pattern

**Files:**
- Create: `web/src/design-system/patterns/FilterSidebar.tsx`

A generic 240px left-rail filter sidebar. Reusable across Tasks / Audit / KB. Renders a list of `<Group>`s, each with a label and chip-style options. Selection model: `Record<groupKey, string | null>` — at most one value selected per group, `null` means "all".

- [ ] **Step 1: Implement**

```tsx
// web/src/design-system/patterns/FilterSidebar.tsx
import { cn } from '@/lib/utils';

export interface FilterGroup {
  key: string;
  label: string;
  options: { value: string; label: string; count?: number }[];
}

export interface FilterSidebarProps {
  groups: FilterGroup[];
  value: Record<string, string | null>;
  onChange: (next: Record<string, string | null>) => void;
}

export function FilterSidebar({ groups, value, onChange }: FilterSidebarProps): JSX.Element {
  return (
    <aside className="border-border-subtle bg-surface-sunken w-60 shrink-0 overflow-y-auto border-r p-3">
      {groups.map((g) => (
        <section key={g.key} className="mb-4">
          <h3 className="text-fg-muted mb-2 text-xs font-medium uppercase tracking-wider">
            {g.label}
          </h3>
          <ul className="space-y-0.5">
            <li>
              <button
                type="button"
                onClick={() => onChange({ ...value, [g.key]: null })}
                className={cn(
                  'w-full rounded px-2 py-1 text-left text-sm',
                  value[g.key] == null
                    ? 'bg-accent-muted text-fg'
                    : 'text-fg-muted hover:bg-surface-raised',
                )}
              >
                All
              </button>
            </li>
            {g.options.map((o) => (
              <li key={o.value}>
                <button
                  type="button"
                  onClick={() => onChange({ ...value, [g.key]: o.value })}
                  className={cn(
                    'flex w-full items-center justify-between rounded px-2 py-1 text-left text-sm',
                    value[g.key] === o.value
                      ? 'bg-accent-muted text-fg'
                      : 'text-fg-muted hover:bg-surface-raised',
                  )}
                >
                  <span>{o.label}</span>
                  {o.count != null && (
                    <span className="font-mono text-xs">{o.count}</span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </aside>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add web/src/design-system/patterns/FilterSidebar.tsx
git commit -m "feat(web): FilterSidebar pattern (reusable 240px left rail)"
```

---

## Task 8: `TaskCard` pattern

**Files:**
- Create: `web/src/design-system/patterns/TaskCard.tsx`

Card used in the inbox list. Two rows: id + brief; status badge + team + agent + age. Honors density.

- [ ] **Step 1: Implement**

```tsx
// web/src/design-system/patterns/TaskCard.tsx
import { Link } from 'react-router-dom';
import { cn } from '@/lib/utils';
import { StatusBadge } from './StatusBadge';
import { IdBadge } from './IdBadge';
import type { TaskRecord } from '@/lib/api/types';
import type { Density } from '@/hooks/density';

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

export interface TaskCardProps {
  task: TaskRecord;
  to: string;
  active?: boolean;
  density?: Density;
}

export function TaskCard({ task, to, active, density = 'comfortable' }: TaskCardProps): JSX.Element {
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
      <div className="flex items-center gap-2">
        <IdBadge kind="task" id={task.task_id} />
        <span className="text-fg-muted text-xs">{task.team}</span>
      </div>
      <p className="text-fg mt-1 text-sm">{task.brief}</p>
      <div className="text-fg-muted mt-2 flex items-center gap-2 text-xs">
        <StatusBadge status={task.status} blockKind={task.block_kind} />
        <span>· updated {relativeAge(task.updated_at)} ago</span>
      </div>
    </Link>
  );
}
```

- [ ] **Step 2: Verify `IdBadge` accepts `kind="task"`**

```
grep -n "kind" web/src/design-system/patterns/IdBadge.tsx
```

If `IdBadge` only accepts `kind="thread"`, add `"task"` to the union there before continuing.

- [ ] **Step 3: Verify `StatusBadge` accepts `status` + `blockKind`**

```
grep -n "blockKind\|status" web/src/design-system/patterns/StatusBadge.tsx
```

If the prop set differs, adapt the call site (this Task) — do NOT mass-rename the existing pattern.

- [ ] **Step 4: Commit**

```bash
git add web/src/design-system/patterns/TaskCard.tsx
git commit -m "feat(web): TaskCard pattern for task inbox rows"
```

---

## Task 9: `TasksPage` shell + routing

**Files:**
- Modify: `web/src/features/tasks/TasksPage.tsx` (replace placeholder)
- Modify: `web/src/routes.tsx`
- Create: `web/src/features/tasks/strings.ts`

Two-pane layout: 240px FilterSidebar + 1fr canvas with the task list. When `:task_id` is in the URL, also render the `TaskDetailPane` in a Drawer.

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/features/tasks/TasksPage.test.tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { PrototypeProvider } from '@/design-system/providers/PrototypeProvider';
import { TasksPage } from './TasksPage';

function renderAt(path: string) {
  return render(
    <PrototypeProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/orgs/:slug/tasks" element={<TasksPage />} />
          <Route path="/orgs/:slug/tasks/:task_id" element={<TasksPage />} />
        </Routes>
      </MemoryRouter>
    </PrototypeProvider>,
  );
}

describe('TasksPage', () => {
  it('renders the inbox with fixture tasks', () => {
    renderAt('/orgs/hk-macau-tourism/tasks');
    expect(screen.getByText(/Draft Hong Kong visa guide/)).toBeInTheDocument();
  });

  it('renders empty filter sidebar groups', () => {
    renderAt('/orgs/hk-macau-tourism/tasks');
    expect(screen.getByText(/Status/i)).toBeInTheDocument();
    expect(screen.getByText(/Team/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

```
cd web && npx vitest run src/features/tasks/TasksPage.test.tsx
```

Expected: FAIL — placeholder TasksPage renders only EmptyState.

- [ ] **Step 3: Implement strings file**

```ts
// web/src/features/tasks/strings.ts
export const TASKS_ERROR_STRINGS: Record<string, string> = {
  task_not_escalated: 'This task is not currently escalated.',
  cannot_revisit: 'This task cannot be revisited from its current status.',
  not_found: 'Task not found.',
};
```

- [ ] **Step 4: Replace the placeholder TasksPage**

```tsx
// web/src/features/tasks/TasksPage.tsx
import { useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { FilterSidebar, type FilterGroup } from '@/design-system/patterns/FilterSidebar';
import { TaskCard } from '@/design-system/patterns/TaskCard';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { useTasksList, useTasksRoutes } from '@/hooks/tasks';
import { useDensity } from '@/hooks/density';
import { TaskDetailPane } from './TaskDetailPane';

const STATUSES: FilterGroup['options'] = [
  { value: 'pending', label: 'Pending' },
  { value: 'in_progress', label: 'In progress' },
  { value: 'blocked', label: 'Blocked' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed' },
];

export function TasksPage(): JSX.Element {
  const { task_id: openTaskId } = useParams<{ task_id: string }>();
  const [filters, setFilters] = useState<Record<string, string | null>>({
    status: null,
    team: null,
  });
  const { density } = useDensity();
  const routes = useTasksRoutes();
  const tasksQuery = useTasksList(
    filters.status ? { status: filters.status } : undefined,
  );

  const filtered = useMemo(() => {
    const all = tasksQuery.data?.tasks ?? [];
    return filters.team ? all.filter((t) => t.team === filters.team) : all;
  }, [tasksQuery.data, filters.team]);

  const teams = useMemo(() => {
    const set = new Set<string>();
    (tasksQuery.data?.tasks ?? []).forEach((t) => set.add(t.team));
    return [...set].sort();
  }, [tasksQuery.data]);

  const groups: FilterGroup[] = [
    { key: 'status', label: 'Status', options: STATUSES },
    { key: 'team', label: 'Team', options: teams.map((t) => ({ value: t, label: t })) },
  ];

  return (
    <div className="flex h-full">
      <FilterSidebar groups={groups} value={filters} onChange={setFilters} />
      <main className="bg-surface-canvas flex-1 overflow-y-auto p-4">
        {tasksQuery.isLoading ? (
          <p className="text-fg-muted">Loading…</p>
        ) : filtered.length === 0 ? (
          <EmptyState title="No tasks" body="No tasks match the current filters." />
        ) : (
          <ul className="space-y-2">
            {filtered.map((t) => (
              <li key={t.task_id}>
                <TaskCard
                  task={t}
                  to={routes.detail(t.task_id)}
                  active={openTaskId === t.task_id}
                  density={density}
                />
              </li>
            ))}
          </ul>
        )}
      </main>
      {openTaskId && <TaskDetailPane taskId={openTaskId} />}
    </div>
  );
}
```

- [ ] **Step 5: Add the detail route**

In `web/src/routes.tsx`, change the existing `<Route path="tasks" element={<TasksPage />} />` block to:

```tsx
<Route path="tasks" element={<TasksPage />} />
<Route path="tasks/:task_id" element={<TasksPage />} />
```

- [ ] **Step 6: Create a minimal `TaskDetailPane` stub so this task's test passes**

```tsx
// web/src/features/tasks/TaskDetailPane.tsx
export function TaskDetailPane({ taskId }: { taskId: string }): JSX.Element {
  return <aside data-testid="task-detail" data-task={taskId} />;
}
```

Task 10 expands this stub.

- [ ] **Step 7: Run the test to verify it passes**

```
cd web && npx vitest run src/features/tasks/TasksPage.test.tsx
```

Expected: 2 PASS.

- [ ] **Step 8: Commit**

```bash
git add web/src/features/tasks/TasksPage.tsx web/src/features/tasks/TasksPage.test.tsx \
        web/src/features/tasks/TaskDetailPane.tsx web/src/features/tasks/strings.ts \
        web/src/routes.tsx
git commit -m "feat(web): TasksPage inbox + filter sidebar"
```

---

## Task 10: `TaskDetailPane` (Drawer with header + recall tree + events log)

**Files:**
- Modify: `web/src/features/tasks/TaskDetailPane.tsx`
- Create: `web/src/features/tasks/TaskRecallTree.tsx`
- Create: `web/src/features/tasks/TaskEventsLog.tsx`

Drawer slides in when `:task_id` is in the URL. Closes by navigating back to `/orgs/:slug/tasks`.

- [ ] **Step 1: Implement `TaskRecallTree`**

```tsx
// web/src/features/tasks/TaskRecallTree.tsx
import type { TaskRecallNode } from '@/lib/api/types';
import { IdBadge } from '@/design-system/patterns/IdBadge';
import { StatusBadge } from '@/design-system/patterns/StatusBadge';

export function TaskRecallTree({ node, depth = 0 }: { node: TaskRecallNode; depth?: number }): JSX.Element {
  return (
    <div style={{ paddingLeft: depth * 16 }} className="py-1">
      <div className="flex items-center gap-2 text-sm">
        <IdBadge kind="task" id={node.task_id} />
        <span className="text-fg-muted">{node.team}</span>
        <StatusBadge status={node.status} />
      </div>
      <p className="text-fg mt-1 text-sm">{node.brief}</p>
      {node.output_summary && (
        <p className="text-fg-muted mt-1 text-xs italic">{node.output_summary}</p>
      )}
      {node.children.map((c) => (
        <TaskRecallTree key={c.task_id} node={c} depth={depth + 1} />
      ))}
    </div>
  );
}
```

- [ ] **Step 2: Implement `TaskEventsLog`**

```tsx
// web/src/features/tasks/TaskEventsLog.tsx
import { useCallback, useState } from 'react';
import { useTaskTailSSE } from '@/hooks/tasks';
import type { TaskEvent } from '@/lib/api/types';

export function TaskEventsLog({ taskId }: { taskId: string }): JSX.Element {
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const append = useCallback((ev: TaskEvent) => setEvents((prev) => [...prev, ev]), []);
  useTaskTailSSE(taskId, append);

  if (events.length === 0) {
    return <p className="text-fg-muted text-xs">Waiting for events…</p>;
  }
  return (
    <ol className="space-y-1 text-xs">
      {events.map((ev, i) => (
        <li key={i} className="flex gap-2">
          <span className="text-fg-muted font-mono">{ev.timestamp}</span>
          <span className="text-fg font-medium">{ev.type}</span>
          {ev.agent && <span className="text-fg-muted">· {ev.agent}</span>}
        </li>
      ))}
    </ol>
  );
}
```

- [ ] **Step 3: Replace `TaskDetailPane`**

```tsx
// web/src/features/tasks/TaskDetailPane.tsx
import { useNavigate } from 'react-router-dom';
import {
  Drawer,
  DrawerContent,
  DrawerTitle,
} from '@/design-system/primitives/Drawer';
import { Button } from '@/design-system/primitives/Button';
import { IdBadge } from '@/design-system/patterns/IdBadge';
import { StatusBadge } from '@/design-system/patterns/StatusBadge';
import { useTask, useTaskRecall, useTasksRoutes } from '@/hooks/tasks';
import { TaskRecallTree } from './TaskRecallTree';
import { TaskEventsLog } from './TaskEventsLog';
import { CancelTaskDialog } from './CancelTaskDialog';
import { RevisitTaskDialog } from './RevisitTaskDialog';
import { ResolveEscalationDialog } from './ResolveEscalationDialog';
import { useState } from 'react';

export function TaskDetailPane({ taskId }: { taskId: string }): JSX.Element {
  const navigate = useNavigate();
  const routes = useTasksRoutes();
  const task = useTask(taskId);
  const recall = useTaskRecall(taskId);
  const [dialog, setDialog] = useState<null | 'cancel' | 'revisit' | 'resolve'>(null);

  const onClose = () => navigate(routes.inbox());
  const isEscalated = task.data?.status === 'blocked' && task.data?.block_kind === 'escalated';

  return (
    <>
      <Drawer open onOpenChange={(o) => !o && onClose()}>
        <DrawerContent className="flex flex-col">
          <header className="border-border-subtle border-b p-4">
            <DrawerTitle className="text-fg flex items-center gap-2 text-lg">
              <IdBadge kind="task" id={taskId} />
              {task.data && <StatusBadge status={task.data.status} blockKind={task.data.block_kind} />}
            </DrawerTitle>
            {task.data && (
              <>
                <p className="text-fg mt-2 text-sm">{task.data.brief}</p>
                <p className="text-fg-muted mt-1 text-xs">team: {task.data.team}</p>
              </>
            )}
            <div className="mt-3 flex gap-2">
              {isEscalated && (
                <Button size="sm" onClick={() => setDialog('resolve')}>Resolve…</Button>
              )}
              <Button size="sm" variant="ghost" onClick={() => setDialog('revisit')}>
                Revisit
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setDialog('cancel')}>
                Cancel
              </Button>
            </div>
          </header>
          <section className="flex-1 overflow-y-auto p-4">
            <h3 className="text-fg-muted mb-2 text-xs font-medium uppercase tracking-wider">
              Recall tree
            </h3>
            {recall.data ? (
              <TaskRecallTree node={recall.data} />
            ) : (
              <p className="text-fg-muted text-xs">Loading recall…</p>
            )}
            <h3 className="text-fg-muted mb-2 mt-6 text-xs font-medium uppercase tracking-wider">
              Live events
            </h3>
            <TaskEventsLog taskId={taskId} />
          </section>
        </DrawerContent>
      </Drawer>
      {dialog === 'cancel' && (
        <CancelTaskDialog taskId={taskId} onClose={() => setDialog(null)} />
      )}
      {dialog === 'revisit' && (
        <RevisitTaskDialog taskId={taskId} onClose={() => setDialog(null)} />
      )}
      {dialog === 'resolve' && (
        <ResolveEscalationDialog taskId={taskId} onClose={() => setDialog(null)} />
      )}
    </>
  );
}
```

This imports three dialog components that don't exist yet — Tasks 11–13 create them.

- [ ] **Step 4: Add empty dialog stubs so compilation succeeds for now**

```tsx
// web/src/features/tasks/CancelTaskDialog.tsx
export function CancelTaskDialog(_: { taskId: string; onClose: () => void }): JSX.Element { return <></>; }
```

Same minimal stub in `RevisitTaskDialog.tsx` and `ResolveEscalationDialog.tsx`.

- [ ] **Step 5: Verify typecheck**

```
cd web && npx tsc --noEmit
```

Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add web/src/features/tasks/TaskDetailPane.tsx web/src/features/tasks/TaskRecallTree.tsx \
        web/src/features/tasks/TaskEventsLog.tsx web/src/features/tasks/CancelTaskDialog.tsx \
        web/src/features/tasks/RevisitTaskDialog.tsx web/src/features/tasks/ResolveEscalationDialog.tsx
git commit -m "feat(web): TaskDetailPane (Drawer + recall tree + events log)"
```

---

## Task 11: `CancelTaskDialog`

**Files:**
- Modify: `web/src/features/tasks/CancelTaskDialog.tsx`

Modeled after `web/src/features/threads/AbandonDialog.tsx`. Required reason field, destructive variant.

- [ ] **Step 1: Implement**

```tsx
// web/src/features/tasks/CancelTaskDialog.tsx
import { useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import { Button } from '@/design-system/primitives/Button';
import { Textarea } from '@/design-system/primitives/Textarea';
import { useCancelTask } from '@/hooks/tasks';
import { TASKS_ERROR_STRINGS } from './strings';

interface Props {
  taskId: string;
  onClose: () => void;
}

export function CancelTaskDialog({ taskId, onClose }: Props): JSX.Element {
  const [reason, setReason] = useState('');
  const [error, setError] = useState<string | null>(null);
  const cancel = useCancelTask(taskId);

  const onSubmit = async () => {
    setError(null);
    try {
      await cancel.mutateAsync({ reason });
      onClose();
    } catch (e: unknown) {
      const code = (e as { code?: string }).code;
      setError(code ? (TASKS_ERROR_STRINGS[code] ?? code) : 'Cancel failed.');
    }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Cancel task</DialogTitle>
        </DialogHeader>
        <p className="text-fg-muted text-sm">
          Reason (required). The agent's current session will be terminated.
        </p>
        <Textarea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          rows={4}
          placeholder="Reason for cancellation"
        />
        {error && <p className="text-danger text-sm">{error}</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Back</Button>
          <Button
            variant="destructive"
            disabled={!reason.trim() || cancel.isPending}
            onClick={onSubmit}
          >
            {cancel.isPending ? 'Cancelling…' : 'Cancel task'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 2: Verify typecheck**

```
cd web && npx tsc --noEmit
```

Expected: clean. (If `Textarea` is missing an `onChange` prop, mirror the call site already used in `AbandonDialog.tsx`.)

- [ ] **Step 3: Commit**

```bash
git add web/src/features/tasks/CancelTaskDialog.tsx
git commit -m "feat(web): CancelTaskDialog"
```

---

## Task 12: `RevisitTaskDialog`

**Files:**
- Modify: `web/src/features/tasks/RevisitTaskDialog.tsx`

Optional note + optional session-timeout override (positive integer).

- [ ] **Step 1: Implement**

```tsx
// web/src/features/tasks/RevisitTaskDialog.tsx
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import { Button } from '@/design-system/primitives/Button';
import { Textarea } from '@/design-system/primitives/Textarea';
import { Input } from '@/design-system/primitives/Input';
import { useRevisitTask, useTasksRoutes } from '@/hooks/tasks';
import { TASKS_ERROR_STRINGS } from './strings';

interface Props {
  taskId: string;
  onClose: () => void;
}

export function RevisitTaskDialog({ taskId, onClose }: Props): JSX.Element {
  const [note, setNote] = useState('');
  const [timeout, setTimeoutStr] = useState('');
  const [error, setError] = useState<string | null>(null);
  const revisit = useRevisitTask(taskId);
  const navigate = useNavigate();
  const routes = useTasksRoutes();

  const onSubmit = async () => {
    setError(null);
    const sst = timeout.trim();
    if (sst && !/^\d+$/.test(sst)) {
      setError('Session timeout must be a positive integer.');
      return;
    }
    try {
      const out = await revisit.mutateAsync({
        note: note || undefined,
        session_timeout_seconds: sst ? Number(sst) : undefined,
      });
      const newId = (out as { task_id?: string }).task_id;
      if (newId) navigate(routes.detail(newId));
      else onClose();
    } catch (e: unknown) {
      const code = (e as { code?: string }).code;
      setError(code ? (TASKS_ERROR_STRINGS[code] ?? code) : 'Revisit failed.');
    }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Revisit task</DialogTitle>
        </DialogHeader>
        <p className="text-fg-muted text-sm">
          Spawns a new root task inheriting the brief + team. The original task stays frozen.
        </p>
        <Textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={3}
          placeholder="Note for the new root (optional)"
        />
        <Input
          value={timeout}
          onChange={(e) => setTimeoutStr(e.target.value)}
          placeholder="Session timeout (seconds, optional)"
        />
        {error && <p className="text-danger text-sm">{error}</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button disabled={revisit.isPending} onClick={onSubmit}>
            {revisit.isPending ? 'Revisiting…' : 'Revisit'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 2: Verify typecheck**

```
cd web && npx tsc --noEmit
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add web/src/features/tasks/RevisitTaskDialog.tsx
git commit -m "feat(web): RevisitTaskDialog"
```

---

## Task 13: `ResolveEscalationDialog`

**Files:**
- Modify: `web/src/features/tasks/ResolveEscalationDialog.tsx`

Approve/reject radio + required rationale.

- [ ] **Step 1: Implement**

```tsx
// web/src/features/tasks/ResolveEscalationDialog.tsx
import { useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import { Button } from '@/design-system/primitives/Button';
import { Textarea } from '@/design-system/primitives/Textarea';
import { useResolveEscalation } from '@/hooks/tasks';
import { TASKS_ERROR_STRINGS } from './strings';

interface Props {
  taskId: string;
  onClose: () => void;
}

export function ResolveEscalationDialog({ taskId, onClose }: Props): JSX.Element {
  const [decision, setDecision] = useState<'approve' | 'reject'>('approve');
  const [rationale, setRationale] = useState('');
  const [error, setError] = useState<string | null>(null);
  const resolve = useResolveEscalation(taskId);

  const onSubmit = async () => {
    setError(null);
    if (!rationale.trim()) {
      setError('Rationale is required.');
      return;
    }
    try {
      await resolve.mutateAsync({ decision, rationale });
      onClose();
    } catch (e: unknown) {
      const code = (e as { code?: string }).code;
      setError(code ? (TASKS_ERROR_STRINGS[code] ?? code) : 'Resolve failed.');
    }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Resolve escalation</DialogTitle>
        </DialogHeader>
        <fieldset className="flex gap-4">
          <label className="text-fg flex items-center gap-2">
            <input
              type="radio"
              name="decision"
              value="approve"
              checked={decision === 'approve'}
              onChange={() => setDecision('approve')}
            />
            Approve
          </label>
          <label className="text-fg flex items-center gap-2">
            <input
              type="radio"
              name="decision"
              value="reject"
              checked={decision === 'reject'}
              onChange={() => setDecision('reject')}
            />
            Reject
          </label>
        </fieldset>
        <Textarea
          value={rationale}
          onChange={(e) => setRationale(e.target.value)}
          rows={4}
          placeholder="Rationale (required)"
        />
        {error && <p className="text-danger text-sm">{error}</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button disabled={resolve.isPending} onClick={onSubmit}>
            {resolve.isPending ? 'Resolving…' : 'Resolve'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 2: Verify typecheck**

```
cd web && npx tsc --noEmit
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add web/src/features/tasks/ResolveEscalationDialog.tsx
git commit -m "feat(web): ResolveEscalationDialog"
```

---

## Task 14: Wire IdBadge `kind="task"` to deep-link

**Files:**
- Modify: `web/src/design-system/patterns/IdBadge.tsx`

If `IdBadge` is currently inert (just a styled span), make `kind="task"` wrap the contents in a `<Link>` to the task detail route when used inside an org-routed view. If the existing IdBadge surface is purely presentational, add an optional `to?: string` prop and wire callers — DO NOT couple the pattern to react-router.

- [ ] **Step 1: Read the current IdBadge to decide**

```
cat web/src/design-system/patterns/IdBadge.tsx
```

- [ ] **Step 2: Implement the minimal change**

If currently presentational, add an optional `to?: string` prop:

```tsx
import { Link } from 'react-router-dom';
// ...
export function IdBadge({ kind, id, to }: { kind: string; id: string; to?: string }): JSX.Element {
  const inner = (
    <span className={`font-mono text-xs ${kindClass(kind)}`}>{id}</span>
  );
  return to ? <Link to={to} className="hover:underline">{inner}</Link> : inner;
}
```

Then update callers in `TaskCard.tsx` and `TaskDetailPane.tsx` to pass `to={routes.detail(task.task_id)}` for child references in the recall tree only (the badge in the active detail header stays inert).

- [ ] **Step 3: Verify typecheck**

```
cd web && npx tsc --noEmit
```

Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add web/src/design-system/patterns/IdBadge.tsx web/src/features/tasks/TaskRecallTree.tsx \
        web/src/design-system/patterns/TaskCard.tsx
git commit -m "feat(web): IdBadge supports optional deep-link target"
```

---

## Task 15: Enable Tasks tab + `g t` jump-key

**Files:**
- Modify: `web/src/design-system/layouts/AppShell/TopBar.tsx`

- [ ] **Step 1: Read TopBar to find the tabs config**

```
grep -n "tabs\|Tasks\|Threads" web/src/design-system/layouts/AppShell/TopBar.tsx
```

- [ ] **Step 2: Enable the Tasks tab**

Remove the `disabled` flag from the Tasks tab. The exact edit depends on the file's shape — change `{ key: 'tasks', label: 'Tasks', disabled: true }` to `{ key: 'tasks', label: 'Tasks' }` (or equivalent).

- [ ] **Step 3: Register the `g t` jump-key**

Add inside the TopBar component body (or its parent shell if shortcuts are owned higher):

```tsx
import { useNavigate } from 'react-router-dom';
import { useGlobalJump } from '@/hooks/global-jump';
import { useTasksRoutes } from '@/hooks/tasks';

// inside the component:
const navigate = useNavigate();
const tasksRoutes = useTasksRoutes();
useGlobalJump('t', () => navigate(tasksRoutes.inbox()));
```

- [ ] **Step 4: Smoke test**

```
cd web && npm run dev
# In a browser, click Tasks tab. Then press `g` then `t`.
```

Expected: lands on `/orgs/<slug>/tasks` with the inbox visible.

- [ ] **Step 5: Commit**

```bash
git add web/src/design-system/layouts/AppShell/TopBar.tsx
git commit -m "feat(web): enable Tasks tab + g t jump-key"
```

---

## Task 16: Integration test (MSW write path)

**Files:**
- Create: `web/src/features/tasks/write-path.test.tsx`

Asserts: viewing a task → opening the cancel dialog → submitting → invalidation kicks → task disappears from the in-progress list. Mirrors `web/src/features/threads/write-path.test.tsx`.

- [ ] **Step 1: Write the test**

```tsx
// web/src/features/tasks/write-path.test.tsx
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { AppProvider } from '@/design-system/providers/AppProvider';
import { TasksPage } from './TasksPage';
import { setupServer } from 'msw/node';
import { http, HttpResponse } from 'msw';

const TASK = {
  task_id: 'TASK-0091',
  team: 'content',
  brief: 'Draft Hong Kong visa guide v2',
  status: 'in_progress',
  block_kind: null,
  parent_task_id: null,
  revisit_of_task_id: null,
  created_at: '2026-05-18T10:00:00Z',
  updated_at: '2026-05-18T10:06:12Z',
  closed_at: null,
  cancelled_at: null,
  session_timeout_seconds: null,
};

const server = setupServer(
  http.get('/api/v1/auth/bootstrap', () => HttpResponse.json({ token: 'test' })),
  http.get('/api/v1/orgs/:slug/tasks', () => HttpResponse.json({ tasks: [TASK] })),
  http.get('/api/v1/orgs/:slug/tasks/:id', () => HttpResponse.json(TASK)),
  http.get('/api/v1/orgs/:slug/tasks/:id/recall', () =>
    HttpResponse.json({ ...TASK, children: [] }),
  ),
  http.post('/api/v1/orgs/:slug/tasks/:id/cancel', () => HttpResponse.json({})),
);

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderAt(path: string) {
  return render(
    <AppProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/orgs/:slug/tasks" element={<TasksPage />} />
          <Route path="/orgs/:slug/tasks/:task_id" element={<TasksPage />} />
        </Routes>
      </MemoryRouter>
    </AppProvider>,
  );
}

describe('Tasks write path', () => {
  it('cancels a task end-to-end', async () => {
    renderAt('/orgs/hk-macau-tourism/tasks/TASK-0091');
    await screen.findByText(/Draft Hong Kong visa guide/);

    fireEvent.click(screen.getByRole('button', { name: /^Cancel$/ }));
    fireEvent.change(screen.getByPlaceholderText(/Reason for cancellation/), {
      target: { value: 'No longer needed.' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^Cancel task$/ }));

    await waitFor(() => {
      // The dialog closes; the destructive button is gone.
      expect(screen.queryByRole('button', { name: /^Cancel task$/ })).toBeNull();
    });
  });
});
```

- [ ] **Step 2: Run**

```
cd web && npx vitest run src/features/tasks/write-path.test.tsx
```

Expected: 1 PASS.

- [ ] **Step 3: Commit**

```bash
git add web/src/features/tasks/write-path.test.tsx
git commit -m "test(web): tasks write-path MSW integration"
```

---

## Task 17: Update `web/UI_SPEC.md` §8

**Files:**
- Modify: `web/UI_SPEC.md`

Replace the placeholder one-screen sketch with the shipped UX: filter sidebar groups (status, team), TaskCard anatomy, Drawer detail pane, three lifecycle dialogs, polling cadence (10s), density toggle behavior.

- [ ] **Step 1: Read §8 to confirm scope**

```
sed -n '436,500p' web/UI_SPEC.md
```

- [ ] **Step 2: Replace §8 with the as-built spec**

Edit `web/UI_SPEC.md`, section 8 ("Tasks — placeholder shell"). Rename to "Tasks". Replace the body with:

```markdown
## 8. Tasks

### Purpose

Inbox + detail surface for every task across the org. Equivalent to `grassland tasks list` + `grassland details <task_id>` + `grassland events <task_id>` + `grassland cancel|revisit|resolve-escalation`.

### Layout

240px FilterSidebar + 1fr canvas. Detail pane mounts as a Drawer (480px slide-in from the right) when `:task_id` is in the URL. Closing the Drawer (Esc or backdrop click) navigates back to `/orgs/:slug/tasks`.

### Inbox

Polled at 10s via TanStack Query (`refetchInterval: 10_000`). Filter groups: Status (pending / in_progress / blocked / completed / failed) and Team (auto-derived from the loaded task set). Rows are TaskCard patterns honoring `useDensity()`.

**Deferred:** Agent filter (umbrella §6.1) is not shipped in PR 7. `TaskRecord` has no `agent` field — agent is implied per task via orchestration events. A follow-up will either surface a derived `current_agent` column on the task row or filter via a separate event-derived index.

### Detail Drawer

- Header: TASK-NNN IdBadge, StatusBadge, brief, team. Three action buttons — Resolve… (only when escalated), Revisit, Cancel.
- Recall tree: indented children, each row an IdBadge + brief + status badge + output_summary if completed.
- Live events: SSE subscription to `/tasks/{id}/events`, appended chronologically. Terminal events (`task_complete` / `task_failed` / `task_blocked`) invalidate both the task detail and the inbox list.

### Dialogs

- CancelTaskDialog — required reason, destructive variant.
- RevisitTaskDialog — optional note, optional session-timeout override (positive integer). On success navigates to the new root.
- ResolveEscalationDialog — approve/reject radio + required rationale.

### Keyboard

- `g t` — jump to Tasks inbox (registered by TopBar).
- `Esc` — close Drawer or dialog.

### Data dependencies

- `GET /orgs/:slug/tasks` (polled).
- `GET /orgs/:slug/tasks/:id`, `GET /orgs/:slug/tasks/:id/recall` (one-shot per Drawer mount).
- `GET /orgs/:slug/tasks/:id/events` (SSE while Drawer is open).
- `POST /orgs/:slug/tasks/:id/cancel|revisit|resolve-escalation`.

### Drift from `2026-05-18-web-app-complete-feature-set-design.md`

The umbrella's §5.5 SSE budget assumed `/tasks/events` (inbox SSE) would land in PR 7. This PR uses polling instead. Net SSE streams added: 1 (per-task tail). If the inbox feels visibly stale in production, revisit the daemon-side event publish wiring as a follow-up; the cap of 4 concurrent streams is preserved either way.
```

- [ ] **Step 3: Commit**

```bash
git add web/UI_SPEC.md
git commit -m "docs(web): UI_SPEC §8 — tasks as-built"
```

---

## Task 18: Final verification

- [ ] **Step 1: TypeScript clean**

```
cd web && npx tsc --noEmit
```

Expected: zero errors.

- [ ] **Step 2: Lint clean**

```
cd web && npm run lint
```

Expected: zero errors.

- [ ] **Step 3: All web tests pass**

```
cd web && npm test -- --run
```

Expected: all PASS.

- [ ] **Step 4: Contract test still green (no new daemon routes added)**

```
uv run pytest tests/contract/test_openapi_snapshot.py -v
```

Expected: PASS — this plan touches no Python.

- [ ] **Step 5: Manual smoke**

```bash
scripts/daemon.sh restart
scripts/build_web.sh
uv run grassland web --no-open
```

In a browser at the printed URL:

1. Click the Tasks tab → inbox renders with the runtime's real tasks.
2. Click a task → Drawer opens with header / recall tree / events log.
3. If the task is escalated → Resolve… button visible → submit a rationale → drawer closes, status flips in the inbox.
4. Cancel a task → reason dialog → submit → status flips.
5. Revisit a task → new task spawned, Drawer routes to the new task_id.
6. Press `g` then `t` from anywhere → lands on `/orgs/<slug>/tasks`.

- [ ] **Step 6: Open the PR**

```bash
git push -u origin HEAD
gh pr create --title "feat(web): PR 7 — Tasks surface (inbox + detail Drawer + lifecycle dialogs)" \
  --body "$(cat <<'EOF'
## Summary

- Ships the Tasks surface per `docs/superpowers/specs/2026-05-18-web-app-complete-feature-set-design.md` §6.1.
- Introduces three cross-cutting primitives the umbrella reserved for PR 7: `Drawer`, `useDensity`, `useGlobalJump`.
- Inbox polls every 10s; per-task tail uses the existing `/tasks/{id}/events` SSE stream. **Drift from umbrella §5.5** documented in the plan and in `UI_SPEC.md` §8.

## Test plan

- [x] `npm test -- --run` clean
- [x] `npx tsc --noEmit` clean
- [x] `npm run lint` clean
- [x] `uv run pytest tests/contract/` clean
- [x] Manual smoke: inbox loads, filter works, Drawer opens, cancel/revisit/resolve flows complete, `g t` jumps.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review notes

After running through the plan:

- **Spec coverage**: Every bullet in umbrella §6.1 (Tasks "what's IN") maps to a task. Drawer primitive → Task 1. Density → Task 2. Jump-keys → Task 3. Data layer → Tasks 4–6. Patterns → Tasks 7–8. Page → Task 9. Detail → Task 10. Lifecycle dialogs → Tasks 11–13. IdBadge cross-link → Task 14. TopBar wiring → Task 15. Integration test → Task 16. Spec sync → Task 17. Verification → Task 18.
- **One known drift**: umbrella §5.5 said `/tasks/events` would ship. This plan polls instead. Drift is documented in the header, in `UI_SPEC.md` §8 (Task 17), and in the PR body.
- **One known gap**: umbrella §6.1 lists "agent filter" alongside status/team. `TaskRecord` has no `agent` field — agent is implied per task via orchestration events, not stored on the task row. The FilterSidebar in Task 9 ships status + team only. The agent-filter call is deferred to a follow-up that decides whether to surface a derived `current_agent` column on the task row or filter via a separate event-derived index. Recorded in Task 17's `UI_SPEC.md` §8 update so the next sprint inherits it explicitly.
- **Names cross-checked**: `realTasksApi` / `mockTasksApi` / `useTasksList` / `useTask` / `useTaskRecall` / `useTaskTailSSE` / `useCancelTask` / `useRevisitTask` / `useResolveEscalation` all match across DataContext.ts, _real-tasks.ts, _mock-tasks.ts, hooks/tasks.ts, and the dialogs.
- **No placeholders.** Every code block is the actual content. Steps that depend on inspecting existing-file shape (e.g., the IdBadge `kind` union in Task 8; the TopBar `tabs` config in Task 15) ask the engineer to read the file first, then perform a stated edit — this is verification, not a TODO.

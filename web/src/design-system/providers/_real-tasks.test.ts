import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, act } from '@testing-library/react';
import React from 'react';

// The task list page (TasksPage) reads through useTasksRootsInfinite, whose
// queryKey is ['tasks-roots-infinite', slug, params]. React Query's
// invalidateQueries prefix-matches array keys element-by-element, so an
// onSuccess that only invalidates ['tasks', slug] never reaches the
// roots-infinite (or roots / tasks-infinite) families — the list keeps
// showing the OLD status after a detail-page mutation (THR-069 msg78).
//
// These tests pin the fix: every mutation that changes a task's status must
// invalidate ALL 'tasks*' list families for the slug PLUS the detail key.
// Red-before-green: against pre-fix code, cancel/revisit/resolve leave
// 'tasks-roots-infinite' (and, for revisit, the detail key) valid, so the
// assertions below FAIL. After the shared invalidateTaskViews helper is
// wired into all four sites they PASS.

// Mock the slug the hooks read from the router.
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useParams: () => ({ slug: SLUG }) };
});

// Stub the network layer so mutations resolve without a real request. Only
// the pieces _real-tasks.ts imports from '@/lib/api' need to exist.
vi.mock('@/lib/api', () => ({
  subscribeSSE: vi.fn(() => Promise.resolve()),
  tasks: {
    cancelTask: vi.fn(() => Promise.resolve({ ok: true })),
    revisitTask: vi.fn(() => Promise.resolve({ ok: true })),
    resolveEscalation: vi.fn(() => Promise.resolve({ ok: true })),
    taskEventsPath: vi.fn(() => '/events'),
  },
}));

import { realTasksApi } from './_real-tasks';

const SLUG = 'acme';
const TASK_ID = 'TASK-1';

// The four task-LIST families TasksPage / other surfaces read through, plus
// the detail key. Each is seeded with data (no observer -> stays inactive, so
// invalidateQueries marks it stale without triggering a refetch/queryFn).
const LIST_KEYS = {
  tasks: ['tasks', SLUG, undefined],
  tasksInfinite: ['tasks-infinite', SLUG, undefined],
  tasksRoots: ['tasks-roots', SLUG, undefined],
  tasksRootsInfinite: ['tasks-roots-infinite', SLUG, undefined],
} as const;
const DETAIL_KEY = ['task', SLUG, TASK_ID];
// Control keys that MUST NOT be invalidated: 'task-recall' doesn't start with
// 'tasks', and a different slug's list must be untouched.
const RECALL_KEY = ['task-recall', SLUG, TASK_ID];
const OTHER_SLUG_KEY = ['tasks-roots-infinite', 'other-org', undefined];

function makeClient(): QueryClient {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  for (const key of Object.values(LIST_KEYS)) qc.setQueryData(key, { seeded: true });
  qc.setQueryData(DETAIL_KEY, { seeded: true });
  qc.setQueryData(RECALL_KEY, { seeded: true });
  qc.setQueryData(OTHER_SLUG_KEY, { seeded: true });
  return qc;
}

function isInvalidated(qc: QueryClient, key: readonly unknown[]): boolean {
  return qc.getQueryState(key)?.isInvalidated === true;
}

function wrapper(qc: QueryClient) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return React.createElement(QueryClientProvider, { client: qc }, children);
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

function expectAllListFamiliesInvalidated(qc: QueryClient): void {
  expect(isInvalidated(qc, LIST_KEYS.tasks)).toBe(true);
  expect(isInvalidated(qc, LIST_KEYS.tasksInfinite)).toBe(true);
  expect(isInvalidated(qc, LIST_KEYS.tasksRoots)).toBe(true);
  // The regression that caused THR-069 msg78: the roots-infinite family the
  // task LIST actually reads was left stale.
  expect(isInvalidated(qc, LIST_KEYS.tasksRootsInfinite)).toBe(true);
  // Different slug and the recall key are correctly excluded.
  expect(isInvalidated(qc, OTHER_SLUG_KEY)).toBe(false);
  expect(isInvalidated(qc, RECALL_KEY)).toBe(false);
}

describe('realTasksApi status mutations invalidate every task-list family + detail', () => {
  it('useCancelTask invalidates all tasks* families and the detail key', async () => {
    const qc = makeClient();
    const { result } = renderHook(() => realTasksApi.useCancelTask(TASK_ID), {
      wrapper: wrapper(qc),
    });
    await act(async () => {
      await result.current.mutateAsync({} as never);
    });
    expectAllListFamiliesInvalidated(qc);
    expect(isInvalidated(qc, DETAIL_KEY)).toBe(true);
  });

  it('useRevisitTask invalidates all tasks* families AND the detail key', async () => {
    const qc = makeClient();
    const { result } = renderHook(() => realTasksApi.useRevisitTask(TASK_ID), {
      wrapper: wrapper(qc),
    });
    await act(async () => {
      await result.current.mutateAsync({} as never);
    });
    expectAllListFamiliesInvalidated(qc);
    // Revisit previously invalidated ['tasks', slug] ONLY — the detail key was
    // missing too. Assert it is now invalidated.
    expect(isInvalidated(qc, DETAIL_KEY)).toBe(true);
  });

  it('useResolveEscalation invalidates all tasks* families and the detail key', async () => {
    const qc = makeClient();
    const { result } = renderHook(() => realTasksApi.useResolveEscalation(TASK_ID), {
      wrapper: wrapper(qc),
    });
    await act(async () => {
      await result.current.mutateAsync({} as never);
    });
    expectAllListFamiliesInvalidated(qc);
    expect(isInvalidated(qc, DETAIL_KEY)).toBe(true);
  });
});

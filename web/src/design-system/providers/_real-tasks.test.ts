import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, type ReactNode } from 'react';

// The tasks LIST that the founder actually looks at (TasksPage) reads via
// useTasksRootsInfinite → queryKey ['tasks-roots-infinite', slug, params].
// A status mutation on the DETAIL page must invalidate THAT family, or the
// list keeps showing the pre-mutation status (THR-069 msg78 bug). React
// Query partial-matches by key prefix segment-by-segment, and 'tasks' does
// NOT prefix-match 'tasks-roots-infinite' — so a plain ['tasks', slug]
// invalidation never reaches it. These tests pin that every list family is
// swept and the detail is invalidated after cancel / revisit / resolve.

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>(
    'react-router-dom',
  );
  return { ...actual, useParams: () => ({ slug: 'test-org' }) };
});

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    tasks: {
      ...actual.tasks,
      cancelTask: vi.fn().mockResolvedValue({}),
      revisitTask: vi.fn().mockResolvedValue({}),
      resolveEscalation: vi.fn().mockResolvedValue({}),
    },
  };
});

import { realTasksApi } from './_real-tasks';

const SLUG = 'test-org';
const TASK_ID = 'TASK-999';

// Keys as they appear in the cache (params default to undefined for the
// unfiltered list surfaces TasksPage mounts).
const K_TASKS = ['tasks', SLUG, undefined];
const K_TASKS_INFINITE = ['tasks-infinite', SLUG, undefined];
const K_ROOTS = ['tasks-roots', SLUG, undefined];
const K_ROOTS_INFINITE = ['tasks-roots-infinite', SLUG, undefined];
const K_DETAIL = ['task', SLUG, TASK_ID];
const K_RECALL = ['task-recall', SLUG, TASK_ID];

const LIST_KEYS = [K_TASKS, K_TASKS_INFINITE, K_ROOTS, K_ROOTS_INFINITE];

function seededClient(): QueryClient {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  // Seed every list family the app mounts, the detail, and a decoy
  // ('task-recall') that shares the 'task' stem but must NOT be swept.
  qc.setQueryData(K_TASKS, { tasks: [] });
  qc.setQueryData(K_TASKS_INFINITE, { pages: [] });
  qc.setQueryData(K_ROOTS, { tasks: [] });
  qc.setQueryData(K_ROOTS_INFINITE, { pages: [] });
  qc.setQueryData(K_DETAIL, { task: {} });
  qc.setQueryData(K_RECALL, { task_id: TASK_ID });
  return qc;
}

function wrapperFor(qc: QueryClient) {
  const Wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: qc }, children);
  Wrapper.displayName = 'TestQueryWrapper';
  return Wrapper;
}

function invalidated(qc: QueryClient, key: readonly unknown[]): boolean {
  return qc.getQueryState(key)?.isInvalidated ?? false;
}

describe('_real-tasks status-mutation cache invalidation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('useCancelTask invalidates every task-list family + the detail', async () => {
    const qc = seededClient();
    const { result } = renderHook(() => realTasksApi.useCancelTask(TASK_ID), {
      wrapper: wrapperFor(qc),
    });

    await result.current.mutateAsync({});

    // The bug: the roots-infinite list (what TasksPage renders) was never swept.
    await waitFor(() => expect(invalidated(qc, K_ROOTS_INFINITE)).toBe(true));
    for (const key of LIST_KEYS) {
      expect(invalidated(qc, key)).toBe(true);
    }
    expect(invalidated(qc, K_DETAIL)).toBe(true);
    // 'task-recall' does not start with 'tasks' → must be left untouched.
    expect(invalidated(qc, K_RECALL)).toBe(false);
  });

  it('useRevisitTask invalidates every task-list family + the detail', async () => {
    const qc = seededClient();
    const { result } = renderHook(() => realTasksApi.useRevisitTask(TASK_ID), {
      wrapper: wrapperFor(qc),
    });

    await result.current.mutateAsync({});

    await waitFor(() => expect(invalidated(qc, K_ROOTS_INFINITE)).toBe(true));
    for (const key of LIST_KEYS) {
      expect(invalidated(qc, key)).toBe(true);
    }
    // Revisit previously invalidated the list ONLY; the detail must now refetch too.
    expect(invalidated(qc, K_DETAIL)).toBe(true);
    expect(invalidated(qc, K_RECALL)).toBe(false);
  });

  it('useResolveEscalation invalidates every task-list family + the detail', async () => {
    const qc = seededClient();
    const { result } = renderHook(
      () => realTasksApi.useResolveEscalation(TASK_ID),
      { wrapper: wrapperFor(qc) },
    );

    await result.current.mutateAsync({ decision: 'continue' });

    await waitFor(() => expect(invalidated(qc, K_ROOTS_INFINITE)).toBe(true));
    for (const key of LIST_KEYS) {
      expect(invalidated(qc, key)).toBe(true);
    }
    expect(invalidated(qc, K_DETAIL)).toBe(true);
    expect(invalidated(qc, K_RECALL)).toBe(false);
  });
});

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, act, waitFor } from '@testing-library/react';
import React from 'react';

// C7 exact-owner reconciliation. A Tasks-list continuation that is invalidated
// while in flight reconciles after it settles. If the same mounted list moved
// to another org meanwhile, that reconciliation must target only the old
// owner's key: it must not refetch (or disturb) the new org's owner, and the
// old owner must still reconcile its loaded pages when the user returns.

let slug = 'org-a';
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useParams: () => ({ slug }) };
});

type Page = { tasks: Array<Record<string, unknown>>; next_cursor: string | null };
const calls: string[] = [];
const deferred = new Map<string, (page: Page) => void>();
let hold = new Set<string>();

const root = (id: string, status: string) => ({
  task_id: id, status, assigned_agent: 'agent_a', parent_task_id: null, brief: id,
  created_at: '2026-10-20T00:00:00Z', updated_at: '2026-10-20T00:00:00Z',
});
function pageFor(org: string, status: string | undefined, before: string | undefined): Page {
  if (status !== 'pending') return { tasks: [], next_cursor: null };
  if (org === 'org-a') return before ? { tasks: [root('A-P2', 'pending')], next_cursor: null } : { tasks: [root('A-P1', 'pending')], next_cursor: 'A-P1' };
  return { tasks: [root('B-P1', 'pending')], next_cursor: null };
}

vi.mock('@/lib/api', () => ({
  subscribeSSE: vi.fn(() => Promise.resolve()),
  tasks: {
    listTaskRoots: vi.fn((org: string, params: { status?: string; before?: string }) => {
      const id = `${org}|${params.status ?? '-'}|${params.before ?? '-'}`;
      calls.push(id);
      const page = pageFor(org, params.status, params.before);
      if (!hold.has(id)) return Promise.resolve(page);
      hold.delete(id);
      return new Promise<Page>((resolve) => deferred.set(id, () => resolve(page)));
    }),
    cancelTask: vi.fn(() => Promise.resolve({ ok: true })),
  },
}));

import { realTasksApi } from './_real-tasks';

function setup() {
  // AppProvider defaults: a fresh owner is not refetched on remount.
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30_000, refetchOnWindowFocus: false } } });
  const wrapper = ({ children }: { children: React.ReactNode }) => React.createElement(QueryClientProvider, { client: qc }, children);
  const list = renderHook(() => realTasksApi.useTasksListStreams({ status: 'pending' }), { wrapper });
  // Real task-family invalidation owner, bound to org A (rendered once, never rerendered).
  const cancelA = renderHook(() => realTasksApi.useCancelTask('A-P1'), { wrapper });
  return { list, cancelA };
}
const pending = (list: ReturnType<typeof setup>['list']) => list.result.current.ordinary[0];
const ids = (list: ReturnType<typeof setup>['list']) => pending(list).tasks.map((t) => t.task_id);

async function continueAThenSwitchAndInvalidate(list: ReturnType<typeof setup>['list'], cancelA: ReturnType<typeof setup>['cancelA']) {
  hold.add('org-a|pending|A-P1');
  act(() => { void pending(list).fetchNextPage(); });
  await waitFor(() => expect(deferred.has('org-a|pending|A-P1')).toBe(true));
  slug = 'org-b';
  list.rerender();
  await act(async () => { await cancelA.result.current.mutateAsync({} as never); });
}

describe('useTasksListStreams C7: invalidated continuation reconciles only its own owner', () => {
  beforeEach(() => { slug = 'org-a'; calls.length = 0; deferred.clear(); hold = new Set(); });

  it('org B initial pending: A settlement neither refetches nor clears B; returning to A reconciles A pages', async () => {
    const { list, cancelA } = setup();
    await waitFor(() => expect(ids(list)).toEqual(['A-P1']));
    hold.add('org-b|pending|-');
    await continueAThenSwitchAndInvalidate(list, cancelA);
    await waitFor(() => expect(calls).toContain('org-b|pending|-'));
    expect(pending(list).isInitialPending).toBe(true);
    const settleFrom = calls.length;
    await act(async () => { deferred.get('org-a|pending|A-P1')!({ tasks: [], next_cursor: null }); await new Promise((r) => setTimeout(r, 30)); });
    expect(calls.slice(settleFrom)).toEqual([]);
    expect(pending(list).isInitialPending).toBe(true);
    await act(async () => { deferred.get('org-b|pending|-')!({ tasks: [], next_cursor: null }); });
    await waitFor(() => expect(ids(list)).toEqual(['B-P1']));
    const backFrom = calls.length;
    slug = 'org-a';
    list.rerender();
    await waitFor(() => expect(calls.slice(backFrom).filter((c) => c.includes('|pending|'))).toEqual(['org-a|pending|-', 'org-a|pending|A-P1']));
    await waitFor(() => expect(ids(list)).toEqual(['A-P1', 'A-P2']));
    expect(calls.slice(backFrom).every((c) => c.startsWith('org-a|'))).toBe(true);
  });

  it('org B already cached: A settlement makes zero B requests and leaves B loaded; returning to A reconciles A pages', async () => {
    const { list, cancelA } = setup();
    await waitFor(() => expect(ids(list)).toEqual(['A-P1']));
    slug = 'org-b';
    list.rerender();
    await waitFor(() => expect(ids(list)).toEqual(['B-P1']));
    slug = 'org-a';
    list.rerender();
    await waitFor(() => expect(ids(list)).toEqual(['A-P1']));
    const switchFrom = calls.length;
    await continueAThenSwitchAndInvalidate(list, cancelA);
    await waitFor(() => expect(ids(list)).toEqual(['B-P1']));
    await act(async () => { deferred.get('org-a|pending|A-P1')!({ tasks: [], next_cursor: null }); await new Promise((r) => setTimeout(r, 30)); });
    expect(calls.slice(switchFrom).filter((c) => c.startsWith('org-b|'))).toEqual([]);
    expect({ ids: ids(list), refreshing: pending(list).isRefreshing, stale: pending(list).isStale }).toEqual({ ids: ['B-P1'], refreshing: false, stale: false });
    const backFrom = calls.length;
    slug = 'org-a';
    list.rerender();
    await waitFor(() => expect(calls.slice(backFrom).filter((c) => c.includes('|pending|'))).toEqual(['org-a|pending|-', 'org-a|pending|A-P1']));
    await waitFor(() => expect(ids(list)).toEqual(['A-P1', 'A-P2']));
    expect(calls.slice(backFrom).every((c) => c.startsWith('org-a|'))).toBe(true);
  });
});

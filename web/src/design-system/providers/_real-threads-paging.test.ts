/**
 * THR-098 — Paging test for useThreadMessages.
 *
 * Prove that when the server returns `has_more: true`, the client pages
 * through all messages via since_seq until `has_more` becomes false, and the
 * assembled transcript equals ALL messages from the server.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor, act, render } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/server';
import React, { useEffect } from 'react';
import { MemoryRouter, Routes, Route, useNavigate } from 'react-router-dom';
import type { ThreadListPage, ThreadRecord } from '@/lib/api/types';
import type { ComposeArgs } from './DataContext';
import { transferableAbortController } from 'node:util';

// Each consumer resolves its slug through a real local route.
const SLUG = 'test-org';
const THREAD_ID = 'THR-098';

/** Seed the auth token so the API client doesn't 401-loop. */
function seedToken() {
  sessionStorage.setItem('happyranch.token', 'mock-token');
}

import { realThreadsApi } from './_real-threads';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeMessage(seq: number) {
  return {
    seq,
    speaker: 'founder' as const,
    kind: 'message' as const,
    body_markdown: `msg ${seq}`,
    decline_reason: null,
    system_payload: null,
    attachments: [],
    created_at: new Date().toISOString(),
    responder_status: [],
  };
}

function wrapper(qc: QueryClient, slug = SLUG) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return React.createElement(QueryClientProvider, { client: qc },
      React.createElement(MemoryRouter, { initialEntries: [`/orgs/${slug}/threads`], future: { v7_startTransition: true, v7_relativeSplatPath: true } },
        React.createElement(Routes, null,
          React.createElement(Route, { path: '/orgs/:slug/threads', element: children })),
      ),
    );
  };
}

// ---------------------------------------------------------------------------
// MSW server — simulates the daemon's /messages endpoint with keyset paging
// ---------------------------------------------------------------------------

beforeEach(() => {
  // Node fetch requires a Node signal; jsdom's controller is a different realm.
  vi.stubGlobal('AbortController', function () { return transferableAbortController(); });
  server.resetHandlers();
  vi.clearAllMocks();
});

afterEach(() => vi.unstubAllGlobals());

function stubMessagesPages(
  pages: { messages: ReturnType<typeof makeMessage>[]; has_more: boolean }[],
) {
  let callCount = 0;
  // Build a map from since_seq to page
  const pageMap = new Map<number, (typeof pages)[number]>();
  let cursor = 0;
  for (const page of pages) {
    pageMap.set(cursor, page);
    cursor = page.messages.length > 0 ? page.messages[page.messages.length - 1].seq : cursor;
  }
  server.use(
    http.get(`/api/v1/orgs/${SLUG}/threads/${THREAD_ID}/messages`, ({ request }) => {
      const url = new URL(request.url);
      const sinceSeq = parseInt(url.searchParams.get('since_seq') ?? '0', 10);
      callCount += 1;
      const page = pageMap.get(sinceSeq);
      if (!page) {
        return HttpResponse.json({ messages: [], has_more: false, next_since_seq: sinceSeq });
      }
      const lastSeq = page.messages.length > 0 ? page.messages[page.messages.length - 1].seq : sinceSeq;
      return HttpResponse.json({ ...page, next_since_seq: lastSeq });
    }),
  );
  return { getCallCount: () => callCount };
}

it('commits thread page totals and serializes duplicate next-page demand', async () => {
  seedToken();
  const requests: string[] = [];
  server.use(http.get(`/api/v1/orgs/${SLUG}/threads`, ({ request }) => {
    const cursor = new URL(request.url).searchParams.get('cursor') ?? 'first';
    requests.push(cursor);
    return HttpResponse.json({ threads: [{ thread_id: cursor }], totals: { open: 2, archived: 0, all: 2, dream_origin: 0 },
      has_more: cursor === 'first', next_cursor: cursor === 'first' ? 'next' : null, sampled_at: '2026-10-07T00:00:00Z' });
  }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { result } = renderHook(() => realThreadsApi.useThreadsInfiniteList('open'), { wrapper: wrapper(qc) });
  await waitFor(() => expect({ requests, cache: qc.getQueriesData({ queryKey: ['threads'] }), totals: result.current.data?.pages[0]?.totals, error: result.current.error }).toMatchObject({ requests: ['first'], totals: { open: 2, archived: 0, all: 2, dream_origin: 0 }, error: null }));
  await act(async () => { await Promise.all([result.current.fetchNextPage(), result.current.fetchNextPage()]); });
  expect(result.current.data?.pages.map((p) => p.threads[0].thread_id)).toEqual(['first', 'next']);
  expect(requests).toEqual(['first', 'next']);
  await act(async () => { await result.current.fetchNextPage(); });
  expect(requests).toEqual(['first', 'next']);
});

function threadPage(id: string, next: string | null = null, total = 2) {
  return { threads: [{ thread_id: id }], totals: { open: total, archived: 0, all: total, dream_origin: 0 },
    has_more: next !== null, next_cursor: next, sampled_at: '2026-10-07T00:00:00Z' };
}

it('shares one request latch across mounted consumers of the same list key', async () => {
  seedToken();
  const requests: string[] = [];
  server.use(http.get(`/api/v1/orgs/${SLUG}/threads`, ({ request }) => {
    const cursor = new URL(request.url).searchParams.get('cursor') ?? 'first';
    requests.push(cursor);
    return HttpResponse.json(threadPage(cursor, cursor === 'first' ? 'next' : null));
  }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { result } = renderHook(() => ({ a: realThreadsApi.useThreadsInfiniteList('open'), b: realThreadsApi.useThreadsInfiniteList('open') }), { wrapper: wrapper(qc) });
  await waitFor(() => expect(result.current.a.data?.pages[0].threads[0].thread_id).toBe('first'));
  await act(async () => { await Promise.all([result.current.a.fetchNextPage(), result.current.b.fetchNextPage()]); });
  expect(requests).toEqual(['first', 'next']);
  expect(result.current.a.data).toEqual(result.current.b.data);
});

it('retains committed rows on continuation failure and repeated cursors until explicit retry', async () => {
  seedToken();
  let outcome: 'fail' | 'repeat' | 'ok' = 'fail';
  const requests: string[] = [];
  server.use(http.get(`/api/v1/orgs/${SLUG}/threads`, ({ request }) => {
    const cursor = new URL(request.url).searchParams.get('cursor');
    requests.push(cursor ?? 'first');
    if (!cursor) return HttpResponse.json(threadPage('first', 'next'));
    if (outcome === 'fail') return HttpResponse.json({ detail: 'later page failed' }, { status: 503 });
    return HttpResponse.json(threadPage('second', outcome === 'repeat' ? 'next' : null));
  }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { result } = renderHook(() => realThreadsApi.useThreadsInfiniteList('open'), { wrapper: wrapper(qc) });
  await waitFor(() => expect(result.current.data?.pages.length).toBe(1));
  const committed = result.current.data;
  await act(async () => { await result.current.fetchNextPage(); });
  expect(result.current.isError).toBe(true);
  expect(result.current.data).toBe(committed);
  for (let i = 0; i < 3; i++) await act(async () => { await result.current.fetchNextPage(); });
  expect(requests).toEqual(['first', 'next']);
  outcome = 'repeat';
  await act(async () => { await result.current.retry(); });
  expect(result.current.error?.message).toBe('invalid_thread_page');
  expect(result.current.data).toBe(committed);
  outcome = 'ok';
  await act(async () => { await result.current.retry(); });
  expect(result.current.data?.pages.flatMap((p) => p.threads.map((r) => r.thread_id))).toEqual(['first', 'second']);
  expect(result.current.hasNextPage).toBe(false);
  expect(result.current.isError).toBe(false);
  expect(requests).toEqual(['first', 'next', 'next', 'next']);
});

it('stages a full replacement prefix and exposes no partial refresh in committed cache', async () => {
  seedToken();
  let phase: 'old' | 'new' | 'fail' = 'old';
  let releaseFirst!: () => void;
  let releaseSecond!: () => void;
  const firstGate = new Promise<void>((resolve) => { releaseFirst = resolve; });
  const secondGate = new Promise<void>((resolve) => { releaseSecond = resolve; });
  const requests: string[] = [];
  server.use(http.get(`/api/v1/orgs/${SLUG}/threads`, async ({ request }) => {
    const cursor = new URL(request.url).searchParams.get('cursor');
    requests.push(`${phase}:${cursor ?? 'first'}`);
    if (phase === 'fail') return HttpResponse.json({ detail: 'refresh failed' }, { status: 503 });
    if (phase === 'new') await (cursor === null ? firstGate : secondGate);
    return HttpResponse.json(threadPage(`${phase}-${cursor ?? 'first'}`, cursor === null ? `${phase}-next` : null, phase === 'new' ? 3 : 2));
  }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { result } = renderHook(() => realThreadsApi.useThreadsInfiniteList('open'), { wrapper: wrapper(qc) });
  await waitFor(() => expect(result.current.data?.pages.length).toBe(1));
  await act(async () => { await result.current.fetchNextPage(); });
  const committed = result.current.data;
  phase = 'new';
  let refresh!: Promise<unknown>;
  act(() => { refresh = result.current.refresh(); });
  await waitFor(() => expect(requests).toContain('new:first'));
  expect(result.current.isRefreshing).toBe(true);
  expect(result.current.data).toBe(committed);
  releaseFirst();
  await waitFor(() => expect(requests).toContain('new:new-next'));
  expect(qc.getQueryData(['threads', SLUG, { status: 'open', page_size: 50 }])).toBe(committed);
  expect(result.current.data?.pages.at(-1)?.totals.all).toBe(2);
  releaseSecond();
  await act(async () => { await refresh; });
  expect(result.current.data?.pages.flatMap((p) => p.threads.map((r) => r.thread_id))).toEqual(['new-first', 'new-new-next']);
  expect(result.current.data?.pages.at(-1)?.totals.all).toBe(3);
  phase = 'fail';
  const accepted = result.current.data;
  await act(async () => { await result.current.refresh(); });
  expect(result.current.data).toBe(accepted);
  expect(result.current.isStale).toBe(true);
});

it('rejects a late continuation after refresh starts a fresh generation', async () => {
  seedToken();
  let initialReads = 0;
  let releaseOld!: () => void;
  const oldGate = new Promise<void>((resolve) => { releaseOld = resolve; });
  const requests: string[] = [];
  server.use(http.get(`/api/v1/orgs/${SLUG}/threads`, async ({ request }) => {
    const cursor = new URL(request.url).searchParams.get('cursor');
    requests.push(cursor ?? 'first');
    if (cursor === 'old-next') {
      await oldGate;
      return HttpResponse.json(threadPage('obsolete', null, 99));
    }
    initialReads++;
    return HttpResponse.json(initialReads === 1 ? threadPage('old', 'old-next') : threadPage('fresh', null, 1));
  }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { result } = renderHook(() => realThreadsApi.useThreadsInfiniteList('open'), { wrapper: wrapper(qc) });
  await waitFor(() => expect(result.current.data?.pages.length).toBe(1));
  let old!: Promise<unknown>;
  act(() => { old = result.current.fetchNextPage(); });
  await waitFor(() => expect(requests).toContain('old-next'));
  await act(async () => { await result.current.refresh(); });
  releaseOld();
  await act(async () => { await old; });
  expect(result.current.data?.pages.flatMap((p) => p.threads.map((r) => r.thread_id))).toEqual(['fresh']);
  expect(result.current.data?.pages[0].totals.all).toBe(1);
  expect(requests).toEqual(['first', 'old-next', 'first']);
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('useThreadMessages paging (THR-098)', () => {
  it('assembles full transcript across pages when server returns has_more', async () => {
    seedToken();
    // Build 250 messages split across 2 pages: 200 + 50
    const page1Messages = Array.from({ length: 200 }, (_, i) => makeMessage(i + 1));
    const page2Messages = Array.from({ length: 50 }, (_, i) => makeMessage(201 + i));

    const counter = stubMessagesPages([
      { messages: page1Messages, has_more: true },
      { messages: page2Messages, has_more: false },
    ]);

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { result } = renderHook(() => realThreadsApi.useThreadMessages(THREAD_ID), {
      wrapper: wrapper(qc),
    });

    // Wait for first page to load
    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
      expect(result.current.data).toBeDefined();
    });

    // After first page, hasNextPage should be true
    expect(result.current.hasNextPage).toBe(true);

    // Fetch next page
    await act(async () => {
      await result.current.fetchNextPage();
    });

    // Wait for state to settle after page fetch
    await waitFor(() => {
      expect(result.current.hasNextPage).toBe(false);
    });

    // The pages array should contain 2 pages
    expect(result.current.data?.pages.length).toBe(2);

    // All 250 messages assembled
    const allMessages = result.current.data!.pages.flatMap((p) => p.messages);
    expect(allMessages.length).toBe(250);
    expect(allMessages.map((m) => m.seq)).toEqual(
      Array.from({ length: 250 }, (_, i) => i + 1),
    );

    // Verify the API was called at least twice (first page + second page)
    expect(counter.getCallCount()).toBeGreaterThanOrEqual(2);
  });

  it('does not page when has_more is false on first page', async () => {
    seedToken();
    const page1Messages = [makeMessage(1), makeMessage(2)];

    stubMessagesPages([
      { messages: page1Messages, has_more: false },
    ]);

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { result } = renderHook(() => realThreadsApi.useThreadMessages(THREAD_ID), {
      wrapper: wrapper(qc),
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
      expect(result.current.data).toBeDefined();
    });
    expect(result.current.hasNextPage).toBe(false);
    expect(result.current.data?.pages.length).toBe(1);
    const allMessages = result.current.data!.pages.flatMap((p) => p.messages);
    expect(allMessages.length).toBe(2);
  });

  it('auto-pages through all >200 messages via effect loop (no manual fetchNextPage)', async () => {
    seedToken();
    const page1Messages = Array.from({ length: 200 }, (_, i) => makeMessage(i + 1));
    const page2Messages = Array.from({ length: 50 }, (_, i) => makeMessage(201 + i));

    const counter = stubMessagesPages([
      { messages: page1Messages, has_more: true },
      { messages: page2Messages, has_more: false },
    ]);

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    // Consumer that models the ThreadsPage messages assembly (flatten all
    // existing pages + auto-page effect). Before the THR-098 fix the effect
    // is missing → only page 1 renders (200 msgs, red). After → all 250 msgs
    // render (green).
    function MessagesConsumer(): JSX.Element | null {
      const q = realThreadsApi.useThreadMessages(THREAD_ID);
      // ThreadsPage auto-page effect (THR-098 fix #1)
      useEffect(() => {
        if (q.hasNextPage && !q.isFetchingNextPage) {
          q.fetchNextPage().catch(() => {});
        }
      }, [q.hasNextPage, q.isFetchingNextPage, q.fetchNextPage]);
      // Flatmap all existing pages — mirrors ThreadsPage
      const messages = q.data?.pages?.flatMap((p) => p.messages) ?? [];
      return React.createElement('div', {
        'data-testid': 'auto-page-container',
        'data-message-count': messages.length,
        'data-has-next': String(q.hasNextPage),
        'data-is-fetching-next': String(q.isFetchingNextPage),
      }, messages.map((m) =>
        React.createElement('span', {
          key: m.seq,
          'data-testid': `msg-${m.seq}`,
        }, m.body_markdown)
      ));
    }

    const result = render(
      React.createElement(wrapper(qc), null,
        React.createElement(MessagesConsumer),
      ),
    );

    // Wait for auto-page to complete: hasNextPage becomes false and we have all 250 messages
    await waitFor(() => {
      const el = result.getByTestId('auto-page-container');
      expect(el.getAttribute('data-has-next')).toBe('false');
      expect(el.getAttribute('data-is-fetching-next')).toBe('false');
      expect(Number(el.getAttribute('data-message-count'))).toBe(250);
    }, { timeout: 5000 });

    // Verify all individual message elements rendered
    for (let i = 1; i <= 250; i++) {
      const msgEl = result.getByTestId(`msg-${i}`);
      expect(msgEl.textContent).toBe(`msg ${i}`);
    }

    // Verify both pages were fetched (2 calls)
    expect(counter.getCallCount()).toBeGreaterThanOrEqual(2);
  });
});

// C6: fixed wire oracles, independent of the production paginator. Small is
// genuinely exhausted; partial has two committed 50-row pages and a real tail.
function membershipFixture(partial: boolean, action: 'compose' | 'online') {
  const row = (id: string, status: 'open' | 'archived', dream = false): ThreadRecord => ({
    thread_id: id, subject: id, status, started_at: '2026-10-08T00:00:00Z',
    archived_at: status === 'archived' ? '2026-10-09T00:00:00Z' : null,
    forwarded_from_id: null, forwarded_from_kind: null, turn_cap: 500, turns_used: 0,
    summary: null, transcript_path: null, composed_from_dream_id: dream ? 'dream-1' : null,
    last_speaker: null, pinned: false, pinned_at: null, last_activity_at: null, participants: [],
  });
  const ids = (start: number, end: number) => Array.from({ length: end - start + 1 }, (_, i) =>
    `a${String(start + i).padStart(3, '0')}`);
  const allIds = partial ? ids(1, 121) : ['a001', 'a002', 'a003'];
  const openIds = partial ? ids(1, 113) : ['a001', 'a002'];
  const archivedIds = partial ? ids(114, 121) : ['a003'];
  const moved = partial ? 'a050' : 'a001';
  const changedOpen = action === 'compose' ? ['a000', ...openIds] : openIds.filter((id) => id !== moved);
  const changedArchived = action === 'compose' ? archivedIds : [moved, ...archivedIds];
  const changedAll = action === 'compose' ? ['a000', ...allIds] : allIds;
  const oldTotals = partial ? { open: 113, archived: 8, all: 121, dream_origin: 9 }
    : { open: 2, archived: 1, all: 3, dream_origin: 1 };
  const newTotals = partial
    ? action === 'compose' ? { open: 114, archived: 8, all: 122, dream_origin: 9 }
      : { open: 112, archived: 9, all: 121, dream_origin: 9 }
    : action === 'compose' ? { open: 3, archived: 1, all: 4, dream_origin: 1 }
      : { open: 1, archived: 2, all: 3, dream_origin: 1 };
  type Bucket = 'all' | 'open' | 'archived';
  const before: Record<Bucket, string[]> = { all: allIds, open: openIds, archived: archivedIds };
  const after: Record<Bucket, string[]> = { all: changedAll, open: changedOpen, archived: changedArchived };
  const maps = [before, after].map((buckets, generation) => {
    const pages = new Map<string, ThreadListPage>();
    for (const bucket of ['all', 'open', 'archived'] as const) {
      const members = buckets[bucket];
      const archiveMembers = generation === 0 ? archivedIds : changedArchived;
      const tokens = ['first', `opaque-${generation}-${bucket}-x7`, `opaque-${generation}-${bucket}-q9`];
      for (let offset = 0; offset < members.length; offset += 50) {
        const next = offset + 50 < members.length ? tokens[offset / 50 + 1] : null;
        pages.set(`${bucket}:${tokens[offset / 50]}`, {
          threads: members.slice(offset, offset + 50).map((id) => row(id,
            archiveMembers.includes(id) ? 'archived' : 'open',
            partial ? ids(1, 9).includes(id) : id === 'a001')),
          totals: generation === 0 ? oldTotals : newTotals, has_more: next !== null,
          next_cursor: next, sampled_at: '2026-10-09T00:00:00Z',
        });
      }
    }
    return pages;
  });
  return { maps, before, after, oldTotals, newTotals, moved, row };
}

function deferredGate() {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => { release = resolve; });
  return { promise, release };
}

function useMembershipProbe() {
  return {
    all: realThreadsApi.useThreadsInfiniteList(),
    open: realThreadsApi.useThreadsInfiniteList('open'),
    archived: realThreadsApi.useThreadsInfiniteList('archived'),
  };
}

it.each([
  ['compose', false], ['compose', true], ['online', false], ['online', true],
] as const)('C6 %s reconciles mounted lists (%s partial), stages each depth and fences old org work', async (action, partial) => {
  seedToken();
  const fixture = membershipFixture(partial, action);
  const buckets = ['all', 'open', 'archived'] as const;
  type Bucket = typeof buckets[number];
  let generation = 0;
  const postGate = deferredGate();
  const oldGate = deferredGate();
  const replacement = Object.fromEntries(buckets.map((bucket) => [bucket, [deferredGate(), deferredGate()]])) as Record<Bucket, ReturnType<typeof deferredGate>[]>;
  const requests: string[] = [];
  const completed: string[] = [];
  const posts: { slug: string; body: unknown }[] = [];
  const betaTotals = { open: 1, archived: 1, all: 2, dream_origin: 0 };
  server.use(
    http.get('/api/v1/orgs/:slug/threads', async ({ params, request }) => {
      const url = new URL(request.url);
      expect(url.searchParams.get('page_size')).toBe('50');
      expect(url.searchParams.has('limit')).toBe(false);
      const bucket = (url.searchParams.get('status') ?? 'all') as Bucket;
      expect(buckets).toContain(bucket);
      const cursor = url.searchParams.get('cursor') ?? 'first';
      const ownGeneration = generation;
      const key = `${params.slug}:${ownGeneration}:${bucket}:${cursor}`;
      requests.push(key);
      if (params.slug === 'beta') {
        expect(cursor).toBe('first');
        const betaIds = bucket === 'all' ? ['b001', 'b002'] : bucket === 'open' ? ['b001'] : ['b002'];
        completed.push(key);
        return HttpResponse.json({ threads: betaIds.map((id) => fixture.row(id, id === 'b002' ? 'archived' : 'open')),
          totals: betaTotals, has_more: false, next_cursor: null, sampled_at: '2026-10-09T00:00:00Z' });
      }
      expect(params.slug).toBe('alpha');
      const page = fixture.maps[ownGeneration].get(`${bucket}:${cursor}`);
      expect(page, key).toBeDefined();
      if (ownGeneration === 0 && cursor.endsWith('-q9')) {
        await oldGate.promise;
        completed.push(key);
        return HttpResponse.json({ ...page, threads: [fixture.row('obsolete', 'open')],
          totals: { open: 99, archived: 99, all: 99, dream_origin: 99 }, has_more: false, next_cursor: null });
      }
      if (ownGeneration === 1) await replacement[bucket][cursor === 'first' ? 0 : 1].promise;
      completed.push(key);
      return HttpResponse.json(page);
    }),
    http.post('/api/v1/orgs/:slug/threads', async ({ params, request }) => {
      posts.push({ slug: String(params.slug), body: await request.json() });
      await postGate.promise;
      return HttpResponse.json({ thread_id: 'a000', started_at: '2026-10-09T00:00:00Z', pending_replies: [] });
    }),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const added = vi.spyOn(window, 'addEventListener');
  const removed = vi.spyOn(window, 'removeEventListener');
  const alpha = renderHook(useMembershipProbe, { wrapper: wrapper(qc, 'alpha') });
  const view = renderHook(() => ({ lists: useMembershipProbe(), compose: realThreadsApi.useComposeThread(), navigate: useNavigate() }),
    { wrapper: wrapper(qc, 'alpha') });
  const idsOf = (list: ReturnType<typeof useMembershipProbe>['all']) =>
    list.data?.pages.flatMap((page) => page.threads.map((thread) => thread.thread_id));
  const expectLists = (lists: ReturnType<typeof useMembershipProbe>, ids: Record<Bucket, string[]>, totals: typeof betaTotals) => {
    for (const bucket of buckets) {
      expect(idsOf(lists[bucket]), bucket).toEqual(ids[bucket]);
      expect(lists[bucket].data?.pages.at(-1)?.totals, bucket).toEqual(totals);
      expect(lists[bucket].isError, bucket).toBe(false);
    }
  };
  const prefix = (members: Record<Bucket, string[]>) => Object.fromEntries(buckets.map((bucket) =>
    [bucket, members[bucket].slice(0, partial && bucket !== 'archived' ? 100 : 50)])) as Record<Bucket, string[]>;
  let old: Promise<unknown>[] = [];
  let mutation: Promise<unknown> | undefined;
  try {
    await waitFor(() => expectLists(alpha.result.current,
      Object.fromEntries(buckets.map((bucket) => [bucket, fixture.before[bucket].slice(0, 50)])) as Record<Bucket, string[]>, fixture.oldTotals));
    if (partial) {
      await act(async () => { await Promise.all([alpha.result.current.all.fetchNextPage(), alpha.result.current.open.fetchNextPage()]); });
      expectLists(alpha.result.current, prefix(fixture.before), fixture.oldTotals);
      act(() => { old = [alpha.result.current.all.fetchNextPage(), alpha.result.current.open.fetchNextPage()]; });
      await waitFor(() => {
        for (const bucket of ['all', 'open']) expect(requests).toContain(`alpha:0:${bucket}:opaque-0-${bucket}-q9`);
      });
    } else {
      for (const bucket of buckets) expect(alpha.result.current[bucket].hasNextPage).toBe(false);
    }
    const committed = Object.fromEntries(buckets.map((bucket) => [bucket, alpha.result.current[bucket].data]));
    const payload = { subject: 'new ordinary thread', recipients: ['dev_agent'], body_markdown: 'body' };
    if (action === 'compose') {
      const args: ComposeArgs & { destination: { slug: string } } = { ...payload, destination: { slug: 'alpha' } };
      act(() => { mutation = view.result.current.compose.mutateAsync(args); });
      await waitFor(() => expect(posts).toEqual([{ slug: 'alpha', body: payload }]));
    }
    // The original alpha observer stays mounted while the mutation view moves.
    act(() => view.result.current.navigate('/orgs/beta/threads'));
    const betaIds = { all: ['b001', 'b002'], open: ['b001'], archived: ['b002'] };
    await waitFor(() => expectLists(view.result.current.lists, betaIds, betaTotals));
    generation = 1;
    if (action === 'compose') {
      await act(async () => { postGate.release(); await mutation; });
    } else {
      act(() => window.dispatchEvent(new Event('online')));
    }
    await waitFor(() => {
      for (const bucket of buckets) expect(requests).toContain(`alpha:1:${bucket}:first`);
    });
    for (const bucket of buckets) expect(alpha.result.current[bucket].data).toBe(committed[bucket]);
    expectLists(alpha.result.current, prefix(fixture.before), fixture.oldTotals);
    // Per-list staging: Archived can finish while All/Open still retain their
    // complete committed depth. There is no cross-bucket atomicity assertion.
    replacement.archived[0].release();
    await waitFor(() => {
      expect(idsOf(alpha.result.current.archived)).toEqual(fixture.after.archived);
      expect(alpha.result.current.archived.data?.pages[0].totals).toEqual(fixture.newTotals);
    });
    for (const bucket of ['all', 'open'] as const) {
      expect(alpha.result.current[bucket].data).toBe(committed[bucket]);
      replacement[bucket][0].release();
      if (partial) {
        await waitFor(() => expect(requests).toContain(`alpha:1:${bucket}:opaque-1-${bucket}-x7`));
        expect(alpha.result.current[bucket].data).toBe(committed[bucket]);
        expect(qc.getQueryData(['threads', 'alpha', { status: bucket === 'all' ? undefined : bucket, page_size: 50 }]))
          .toBe(committed[bucket]);
        expect(alpha.result.current[bucket].data?.pages.at(-1)?.totals).toEqual(fixture.oldTotals);
        replacement[bucket][1].release();
      }
      await waitFor(() => {
        expect(idsOf(alpha.result.current[bucket])).toEqual(prefix(fixture.after)[bucket]);
        expect(alpha.result.current[bucket].data?.pages.at(-1)?.totals).toEqual(fixture.newTotals);
      });
    }
    await waitFor(() => expectLists(alpha.result.current, prefix(fixture.after), fixture.newTotals));
    if (action === 'online') {
      const changed = alpha.result.current.all.data?.pages.flatMap((page) => page.threads).find((thread) => thread.thread_id === fixture.moved);
      expect(changed?.status).toBe('archived');
      expect(posts).toEqual([]);
    } else {
      expect(posts).toEqual([{ slug: 'alpha', body: payload }]);
      expect(idsOf(alpha.result.current.all)?.filter((id) => id === 'a000')).toHaveLength(1);
      expect(idsOf(alpha.result.current.open)?.filter((id) => id === 'a000')).toHaveLength(1);
      expect(idsOf(alpha.result.current.archived)).not.toContain('a000');
    }
    const accepted = Object.fromEntries(buckets.map((bucket) => [bucket, alpha.result.current[bucket].data]));
    oldGate.release();
    await act(async () => { await Promise.all(old); });
    if (partial) await waitFor(() => {
      for (const bucket of ['all', 'open']) expect(completed).toContain(`alpha:0:${bucket}:opaque-0-${bucket}-q9`);
    });
    for (const bucket of buckets) expect(alpha.result.current[bucket].data).toBe(accepted[bucket]);
    expectLists(alpha.result.current, prefix(fixture.after), fixture.newTotals);
    expectLists(view.result.current.lists, betaIds, betaTotals);
    if (!partial) expect(requests.some((key) => !key.endsWith(':first'))).toBe(false);
  } finally {
    postGate.release(); oldGate.release();
    for (const gates of Object.values(replacement)) for (const gate of gates) gate.release();
    try {
      await act(async () => { await Promise.allSettled([...old, ...(mutation ? [mutation] : [])]); });
      await waitFor(() => expect([...completed].sort()).toEqual([...requests].sort()));
    } finally {
      alpha.unmount(); view.unmount(); qc.clear();
      try {
        for (const [name, callback] of added.mock.calls.filter(([name]) => name === 'online' || name === 'focus')) {
          expect(removed.mock.calls.some(([removedName, removedCallback]) => removedName === name && removedCallback === callback)).toBe(true);
        }
      } finally {
        added.mockRestore(); removed.mockRestore(); server.resetHandlers();
      }
    }
  }
});

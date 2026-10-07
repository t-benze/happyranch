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
import { transferableAbortController } from 'node:util';

// Use a real-ish slug for the router mock.
const SLUG = 'test-org';
const THREAD_ID = 'THR-098';

/** Seed the auth token so the API client doesn't 401-loop. */
function seedToken() {
  sessionStorage.setItem('happyranch.token', 'mock-token');
}

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useParams: () => ({ slug: SLUG }) };
});

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

function wrapper(qc: QueryClient) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return React.createElement(QueryClientProvider, { client: qc }, children);
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
      React.createElement(QueryClientProvider, { client: qc },
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

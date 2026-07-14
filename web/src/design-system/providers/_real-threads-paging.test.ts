/**
 * THR-098 — Paging test for useThreadMessages.
 *
 * Prove that when the server returns `has_more: true`, the client pages
 * through all messages via since_seq until `has_more` becomes false, and the
 * assembled transcript equals ALL messages from the server.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor, act } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import React from 'react';

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

const server = setupServer();

beforeEach(() => {
  server.resetHandlers();
  vi.clearAllMocks();
});

afterAll(() => {
  server.close();
});

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

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));

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
});

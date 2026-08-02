import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, act } from '@testing-library/react';
import React from 'react';

// ---------------------------------------------------------------------------
// classifyTailEvent — pure unit tests (no globals needed)
// ---------------------------------------------------------------------------
import { classifyTailEvent } from './_real-threads';

// Pins the routing the live "agent working on a reply" indicator depends on:
// the runner publishes seq-bearing invocation_started/settled events on the
// thread tail, and the consumer must invalidate the messages query so
// responder_status (queued/working/…) refetches. See thread_runner.py
// _publish_invocation_event + the spec (issue #53 follow-up).
describe('classifyTailEvent', () => {
  it("appends a full ThreadMessage (carries body_markdown, even when null)", () => {
    expect(classifyTailEvent({ seq: 3, body_markdown: 'hi' })).toBe('append');
    expect(classifyTailEvent({ seq: 3, body_markdown: null })).toBe('append');
  });

  it('invalidates for seq-bearing invocation lifecycle events', () => {
    expect(
      classifyTailEvent({ seq: 12, kind: 'invocation_started' } as never),
    ).toBe('invalidate');
    expect(
      classifyTailEvent({ seq: 12, kind: 'invocation_settled' } as never),
    ).toBe('invalidate');
  });

  it('invalidates for seq-bearing system events (e.g. task_dispatched)', () => {
    // THR-137: dispatch_from_thread_endpoint publishes a seq-bearing generic
    // kind=system tail event.  The consumer must invalidate thread-tasks so
    // the Linked-tasks column refetches without manual refresh.
    expect(
      classifyTailEvent({ seq: 8, kind: 'system' } as never),
    ).toBe('invalidate');
    // Explicit task_dispatched shape — same path through the classifier.
    expect(
      classifyTailEvent({
        seq: 8,
        kind: 'system',
        system_payload: { kind_tag: 'task_dispatched' },
      } as never),
    ).toBe('invalidate');
  });

  it('ignores events without a seq (e.g. decline_status seq=null)', () => {
    expect(classifyTailEvent({ seq: null })).toBe('ignore');
    expect(classifyTailEvent({})).toBe('ignore');
  });
});

// ---------------------------------------------------------------------------
// useThreadTailSSE — regression: thread-tasks invalidation (THR-137)
// ---------------------------------------------------------------------------

const SLUG = 'test-org';
const THREAD_ID = 'THR-137';

// Mock the slug the hooks read from the router.
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useParams: () => ({ slug: SLUG }) };
});

// Stub the network layer — only the pieces _real-threads.ts imports.
let capturedOnMessage: ((ev: unknown) => void) | null = null;

vi.mock('@/lib/api', () => ({
  subscribeSSE: vi.fn((_path: string, opts: { onMessage: (ev: unknown) => void }) => {
    capturedOnMessage = opts.onMessage;
    return Promise.resolve();
  }),
  threads: {
    threadInboxEventsPath: vi.fn(() => '/events'),
    threadTailPath: vi.fn(() => ({ path: '/tail', query: { since_seq: 0 } })),
  },
}));

import { realThreadsApi } from './_real-threads';

function makeClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function wrapper(qc: QueryClient) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return React.createElement(QueryClientProvider, { client: qc }, children);
  };
}

function isInvalidated(qc: QueryClient, key: readonly unknown[]): boolean {
  return qc.getQueryState(key)?.isInvalidated === true;
}

beforeEach(() => {
  vi.clearAllMocks();
  capturedOnMessage = null;
});

describe('useThreadTailSSE — thread-tasks invalidation (THR-137)', () => {
  it('invalidates thread-tasks when a seq-bearing system event arrives', async () => {
    const qc = makeClient();

    // Seed the thread-tasks cache as if a prior fetch returned one task.
    qc.setQueryData(['thread-tasks', SLUG, THREAD_ID], [
      { task_id: 'TASK-1', title: 'existing task' },
    ]);
    // Seed the thread-messages cache too (the hook also invalidates messages).
    qc.setQueryData(['thread-messages', SLUG, THREAD_ID], {
      pages: [{ messages: [], has_more: false, next_since_seq: 0 }],
      pageParams: [0],
    });

    // Render the hook — this triggers useEffect which subscribes to SSE.
    renderHook(() => realThreadsApi.useThreadTailSSE(THREAD_ID), {
      wrapper: wrapper(qc),
    });

    // The mock subscribeSSE should have captured the onMessage callback.
    expect(capturedOnMessage).not.toBeNull();

    // Simulate a seq-bearing system tail event arriving (e.g. task_dispatched).
    await act(async () => {
      capturedOnMessage!({
        thread_id: THREAD_ID,
        seq: 5,
        speaker: 'system',
        kind: 'system',
        preview: 'dispatched TASK-999',
      });
    });

    // After the event, the thread-tasks query MUST be invalidated so the
    // Linked-tasks column refetches.  Pre-fix this assertion FAILS.
    expect(isInvalidated(qc, ['thread-tasks', SLUG, THREAD_ID])).toBe(true);

    // The existing messages invalidation must still work too.
    expect(isInvalidated(qc, ['thread-messages', SLUG, THREAD_ID])).toBe(true);
  });

  it('ignores null-seq events and leaves thread-tasks untouched', async () => {
    const qc = makeClient();
    qc.setQueryData(['thread-tasks', SLUG, THREAD_ID], [
      { task_id: 'TASK-1', title: 'existing task' },
    ]);

    renderHook(() => realThreadsApi.useThreadTailSSE(THREAD_ID), {
      wrapper: wrapper(qc),
    });

    expect(capturedOnMessage).not.toBeNull();

    // A decline_status-like event with seq=null should NOT invalidate.
    await act(async () => {
      capturedOnMessage!({
        thread_id: THREAD_ID,
        seq: null,
        speaker: 'system',
        kind: 'decline_status',
        preview: '',
      });
    });

    expect(isInvalidated(qc, ['thread-tasks', SLUG, THREAD_ID])).toBe(false);
  });

  it('invalidates thread-tasks for seq-bearing invoke lifecycle events (harmless refresh)', async () => {
    const qc = makeClient();
    qc.setQueryData(['thread-tasks', SLUG, THREAD_ID], [
      { task_id: 'TASK-1', title: 'existing task' },
    ]);

    renderHook(() => realThreadsApi.useThreadTailSSE(THREAD_ID), {
      wrapper: wrapper(qc),
    });

    expect(capturedOnMessage).not.toBeNull();

    // invocation_started events also trigger thread-tasks invalidation.
    // This is harmless — the bounded linked-task query just re-runs.
    await act(async () => {
      capturedOnMessage!({
        thread_id: THREAD_ID,
        seq: 3,
        speaker: 'dev_agent',
        kind: 'invocation_started',
        preview: 'agent is working...',
      });
    });

    expect(isInvalidated(qc, ['thread-tasks', SLUG, THREAD_ID])).toBe(true);
  });
});

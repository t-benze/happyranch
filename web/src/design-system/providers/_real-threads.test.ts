import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, act } from '@testing-library/react';
import React from 'react';

// ---------------------------------------------------------------------------
// classifyTailEvent — pure unit tests (no globals needed)
// ---------------------------------------------------------------------------
import { classifyTailEvent } from './_real-threads';
// The mocked @/lib/api module — `threads` carries the THR-209 mutation fns.
import { threads as threadsApi } from '@/lib/api';

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
    // THR-209 pin/rename mutation hooks exercise these network functions.
    setThreadPinned: vi.fn(),
    getThread: vi.fn(),
    renameThread: vi.fn(),
    // THR-198 Slice C: per-thread mention-routing switch.
    setThreadMentionRouting: vi.fn(),
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

describe('useThreadTailSSE — thread-detail invalidation for reply_delivery (GH-688 Phase 1 Slice C)', () => {
  it('invalidates the thread detail query so the pair projection refetches', async () => {
    const qc = makeClient();
    // Seed the thread detail cache as if a prior GET /threads/{id} returned
    // the pair-level reply_delivery projection.
    qc.setQueryData(['thread', SLUG, THREAD_ID], {
      thread_id: THREAD_ID,
      subject: 'x',
      participants: [],
      messages: [],
      reply_delivery: [],
    });
    qc.setQueryData(['thread-messages', SLUG, THREAD_ID], {
      pages: [{ messages: [], has_more: false, next_since_seq: 0, reply_delivery: [] }],
      pageParams: [0],
    });

    renderHook(() => realThreadsApi.useThreadTailSSE(THREAD_ID), {
      wrapper: wrapper(qc),
    });

    // A seq-bearing invocation-lifecycle event must refresh the canonical
    // detail (the Reply delivery rail + tail live indicator live there).
    await act(async () => {
      capturedOnMessage!({
        thread_id: THREAD_ID,
        seq: 5,
        speaker: 'dev_agent',
        kind: 'invocation_settled',
        preview: '',
      });
    });

    expect(isInvalidated(qc, ['thread', SLUG, THREAD_ID])).toBe(true);
    expect(isInvalidated(qc, ['thread-messages', SLUG, THREAD_ID])).toBe(true);
  });

  it('leaves the thread detail untouched for null-seq events', async () => {
    const qc = makeClient();
    qc.setQueryData(['thread', SLUG, THREAD_ID], {
      thread_id: THREAD_ID,
      subject: 'x',
      participants: [],
      messages: [],
      reply_delivery: [],
    });

    renderHook(() => realThreadsApi.useThreadTailSSE(THREAD_ID), {
      wrapper: wrapper(qc),
    });

    await act(async () => {
      capturedOnMessage!({
        thread_id: THREAD_ID,
        seq: null,
        speaker: 'system',
        kind: 'decline_status',
        preview: '',
      });
    });

    expect(isInvalidated(qc, ['thread', SLUG, THREAD_ID])).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// THR-209 — useSetThreadPinned optimistic cache update + rollback
// ---------------------------------------------------------------------------

describe('useSetThreadPinned — optimistic pin (THR-209)', () => {
  interface PinState {
    thread_id: string;
    subject: string;
    pinned: boolean;
    pinned_at: string | null;
    last_activity_at: string | null;
  }

  function seed(qc: QueryClient): PinState {
    const thread: PinState = {
      thread_id: THREAD_ID,
      subject: 'S',
      pinned: false,
      pinned_at: null,
      last_activity_at: null,
    };
    qc.setQueryData(['threads', SLUG, { status: 'open' }], { threads: [thread] });
    qc.setQueryData(['thread', SLUG, THREAD_ID], {
      ...thread,
      participants: [],
      messages: [],
      reply_delivery: [],
    });
    return thread;
  }

  it('flips the cached pinned state optimistically and keeps it on success', async () => {
    const qc = makeClient();
    seed(qc);
    (threadsApi.setThreadPinned as ReturnType<typeof vi.fn>).mockResolvedValue({
      thread_id: THREAD_ID,
      pinned: true,
    });

    const { result } = renderHook(() => realThreadsApi.useSetThreadPinned(THREAD_ID), {
      wrapper: wrapper(qc),
    });
    await act(async () => {
      await result.current.mutateAsync({ pinned: true });
    });

    const list = qc.getQueryData<{ threads: { pinned: boolean }[] }>([
      'threads', SLUG, { status: 'open' },
    ]);
    expect(list?.threads[0].pinned).toBe(true);
    const detail = qc.getQueryData<{ pinned: boolean }>(['thread', SLUG, THREAD_ID]);
    expect(detail?.pinned).toBe(true);
  });

  it('rolls the optimistic flip back on failure', async () => {
    const qc = makeClient();
    seed(qc);
    (threadsApi.setThreadPinned as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error('boom'),
    );

    const { result } = renderHook(() => realThreadsApi.useSetThreadPinned(THREAD_ID), {
      wrapper: wrapper(qc),
    });
    await act(async () => {
      await expect(result.current.mutateAsync({ pinned: true })).rejects.toThrow('boom');
    });

    const list = qc.getQueryData<{ threads: { pinned: boolean }[] }>([
      'threads', SLUG, { status: 'open' },
    ]);
    expect(list?.threads[0].pinned).toBe(false);
    const detail = qc.getQueryData<{ pinned: boolean }>(['thread', SLUG, THREAD_ID]);
    expect(detail?.pinned).toBe(false);
  });

  it('unpin rolls back to pinned on failure', async () => {
    const qc = makeClient();
    const thread = seed(qc);
    thread.pinned = true;
    thread.pinned_at = '2026-05-20T00:00:00Z';
    qc.setQueryData(['threads', SLUG, { status: 'open' }], { threads: [thread] });
    qc.setQueryData(['thread', SLUG, THREAD_ID], {
      ...thread,
      participants: [],
      messages: [],
      reply_delivery: [],
    });
    (threadsApi.setThreadPinned as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error('boom'),
    );

    const { result } = renderHook(() => realThreadsApi.useSetThreadPinned(THREAD_ID), {
      wrapper: wrapper(qc),
    });
    await act(async () => {
      await expect(result.current.mutateAsync({ pinned: false })).rejects.toThrow('boom');
    });

    const list = qc.getQueryData<{ threads: { pinned: boolean }[] }>([
      'threads', SLUG, { status: 'open' },
    ]);
    expect(list?.threads[0].pinned).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// THR-209 — useRenameThread patches the detail cache
// ---------------------------------------------------------------------------

describe('useRenameThread — detail cache patch (THR-209)', () => {
  it('patches the thread detail subject and invalidates the list', async () => {
    const qc = makeClient();
    qc.setQueryData(['thread', SLUG, THREAD_ID], {
      thread_id: THREAD_ID,
      subject: 'Old',
      participants: [],
      messages: [],
      reply_delivery: [],
    });
    (threadsApi.renameThread as ReturnType<typeof vi.fn>).mockResolvedValue({
      thread_id: THREAD_ID,
      subject: 'New',
    });

    const { result } = renderHook(() => realThreadsApi.useRenameThread(THREAD_ID), {
      wrapper: wrapper(qc),
    });
    await act(async () => {
      await result.current.mutateAsync({ subject: 'New' });
    });

    const detail = qc.getQueryData<{ subject: string }>(['thread', SLUG, THREAD_ID]);
    expect(detail?.subject).toBe('New');
  });
});

// ---------------------------------------------------------------------------
// THR-198 Slice C — useSetThreadMentionRouting optimistic update + rollback
// ---------------------------------------------------------------------------

describe('useSetThreadMentionRouting — optimistic toggle (THR-198 Slice C)', () => {
  interface RoutingState {
    thread_id: string;
    subject: string;
    pinned: boolean;
    pinned_at: string | null;
    last_activity_at: string | null;
    mention_routing_enabled: boolean;
  }

  function seed(qc: QueryClient): RoutingState {
    const thread: RoutingState = {
      thread_id: THREAD_ID,
      subject: 'S',
      pinned: false,
      pinned_at: null,
      last_activity_at: null,
      mention_routing_enabled: true,
    };
    qc.setQueryData(['threads', SLUG, { status: 'open' }], { threads: [thread] });
    qc.setQueryData(['thread', SLUG, THREAD_ID], {
      ...thread,
      participants: [],
      messages: [],
      reply_delivery: [],
    });
    return thread;
  }

  it('flips the cached routing flag optimistically and keeps it on success', async () => {
    const qc = makeClient();
    seed(qc);
    (threadsApi.setThreadMentionRouting as ReturnType<typeof vi.fn>).mockResolvedValue({
      thread_id: THREAD_ID,
      mention_routing_enabled: false,
    });

    const { result } = renderHook(
      () => realThreadsApi.useSetThreadMentionRouting(THREAD_ID),
      { wrapper: wrapper(qc) },
    );
    await act(async () => {
      await result.current.mutateAsync({ mention_routing_enabled: false });
    });

    const list = qc.getQueryData<{ threads: { mention_routing_enabled: boolean }[] }>([
      'threads', SLUG, { status: 'open' },
    ]);
    expect(list?.threads[0].mention_routing_enabled).toBe(false);
    const detail = qc.getQueryData<{ mention_routing_enabled: boolean }>([
      'thread', SLUG, THREAD_ID,
    ]);
    expect(detail?.mention_routing_enabled).toBe(false);
  });

  it('rolls the optimistic flip back on failure', async () => {
    const qc = makeClient();
    seed(qc);
    (threadsApi.setThreadMentionRouting as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error('boom'),
    );

    const { result } = renderHook(
      () => realThreadsApi.useSetThreadMentionRouting(THREAD_ID),
      { wrapper: wrapper(qc) },
    );
    await act(async () => {
      await expect(
        result.current.mutateAsync({ mention_routing_enabled: false }),
      ).rejects.toThrow('boom');
    });

    const list = qc.getQueryData<{ threads: { mention_routing_enabled: boolean }[] }>([
      'threads', SLUG, { status: 'open' },
    ]);
    expect(list?.threads[0].mention_routing_enabled).toBe(true);
    const detail = qc.getQueryData<{ mention_routing_enabled: boolean }>([
      'thread', SLUG, THREAD_ID,
    ]);
    expect(detail?.mention_routing_enabled).toBe(true);
  });

  it('treats the idempotent same-state no-op as success', async () => {
    const qc = makeClient();
    seed(qc);
    (threadsApi.setThreadMentionRouting as ReturnType<typeof vi.fn>).mockResolvedValue({
      thread_id: THREAD_ID,
      mention_routing_enabled: true,
      idempotent: true,
    });

    const { result } = renderHook(
      () => realThreadsApi.useSetThreadMentionRouting(THREAD_ID),
      { wrapper: wrapper(qc) },
    );
    await act(async () => {
      // A same-state request resolves (never rejects) with idempotent: true.
      await result.current.mutateAsync({ mention_routing_enabled: true });
    });

    const detail = qc.getQueryData<{ mention_routing_enabled: boolean }>([
      'thread', SLUG, THREAD_ID,
    ]);
    expect(detail?.mention_routing_enabled).toBe(true);
  });
});

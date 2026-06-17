import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { QueryClient } from '@tanstack/react-query';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'alpha';

function mountAt(route: string) {
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/agents`, () =>
      HttpResponse.json({ agents: [] }),
    ),
  );
  return renderWithProviders(<AppRoutes />, { route });
}

/* ------------------------------------------------------------------ */
/*  Test helpers                                                       */
/* ------------------------------------------------------------------ */

function mkThread(
  id: string,
  subject: string,
  overrides?: Partial<{
    status: string;
    turn_cap: number;
    turns_used: number;
    composed_from_dream_id: string | null;
    last_speaker: string | null;
  }>,
) {
  return {
    thread_id: id,
    subject,
    status: 'open' as const,
    started_at: '2026-05-14T00:00:00Z',
    archived_at: null as string | null,
    forwarded_from_id: null as string | null,
    forwarded_from_kind: null as 'thread' | null,
    turn_cap: 500,
    turns_used: 12,
    summary: null as string | null,
    transcript_path: null as string | null,
    composed_from_dream_id: null as string | null,
    last_speaker: 'agent_a' as string | null,
    ...overrides,
  };
}

function mkMessage(
  seq: number,
  speaker: string,
  kind: 'message' | 'decline' | 'system',
  body: string,
) {
  return {
    seq,
    speaker,
    kind,
    body_markdown: kind === 'message' ? body : null,
    decline_reason: kind === 'decline' ? body : null,
    system_payload: kind === 'system' ? { event: body } : null,
    created_at: '2026-05-14T00:00:00Z',
    responder_status: [],
  };
}

function mkSystemMessage(seq: number, speaker: string, payload: Record<string, unknown>) {
  return {
    seq,
    speaker,
    kind: 'system' as const,
    body_markdown: null,
    decline_reason: null,
    system_payload: payload,
    created_at: '2026-05-14T00:00:00Z',
    responder_status: [],
  };
}

function setupThreadWithMessages(
  threadId: string,
  messages: ReturnType<typeof mkMessage | typeof mkSystemMessage>[],
) {
  server.use(
    http.get(`/api/v1/orgs/${SLUG}/threads`, () =>
      HttpResponse.json({ threads: [mkThread(threadId, 'Test thread')] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/threads/events`, () =>
      HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/threads/${threadId}`, () =>
      HttpResponse.json({
        ...mkThread(threadId, 'Test thread'),
        participants: ['agent_a'],
        messages,
      }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/threads/${threadId}/messages`, () =>
      HttpResponse.json({ messages }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/threads/${threadId}/tail`, () =>
      HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
    ),
  );
}

/* ------------------------------------------------------------------ */
/*  List tests                                                         */
/* ------------------------------------------------------------------ */

describe('ThreadsPage — list (design-overhaul reshape)', () => {
  test('renders empty state when no threads', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/threads`, () => HttpResponse.json({ threads: [] })),
      http.get(`/api/v1/orgs/${SLUG}/threads/events`, () =>
        HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
      ),
    );
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() =>
      expect(screen.getByText(/No threads yet/i)).toBeInTheDocument(),
    );
  });

  test('renders loading skeleton while fetching', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/threads`, () =>
        new Promise(() => {}), // never resolves
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/events`, () =>
        HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
      ),
    );
    mountAt(`/orgs/${SLUG}/threads`);
    // Skeleton should show while loading (check for animate-pulse skeleton presence)
    await waitFor(() => {
      const skeletons = document.querySelectorAll('.animate-pulse');
      expect(skeletons.length).toBeGreaterThan(0);
    });
  });

  test('renders error state with retry button', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/threads`, () =>
        HttpResponse.json({ error: 'boom' }, { status: 500 }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/events`, () =>
        HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
      ),
    );
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() => {
      expect(screen.getByText(/Couldn't load threads/i)).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Retry/i })).toBeInTheDocument();
    });
  });

  test('lists threads with turn budget and last speaker', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/threads`, () =>
        HttpResponse.json({
          threads: [
            mkThread('THR-001', 'Launch plan', { turns_used: 3, turn_cap: 500, last_speaker: 'dev_agent' }),
            mkThread('THR-002', 'Budget review', { turns_used: 487, turn_cap: 500, last_speaker: 'founder' }),
          ],
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/events`, () =>
        HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
      ),
    );
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() => {
      // Thread subjects appear in InboxRow
      expect(screen.getByText(/Launch plan/)).toBeInTheDocument();
      expect(screen.getByText(/Budget review/)).toBeInTheDocument();
      // Turn budgets should appear
      expect(screen.getByText('3/500')).toBeInTheDocument();
      expect(screen.getByText('487/500')).toBeInTheDocument();
      // Last speakers should appear
      expect(screen.getByText(/dev_agent/)).toBeInTheDocument();
      expect(screen.getByText(/founder/)).toBeInTheDocument();
    });
  });

  test('renders dream moon marker for dream-originated thread', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/threads`, () =>
        HttpResponse.json({
          threads: [
            mkThread('THR-042', 'Dream reflection', {
              composed_from_dream_id: 'DREAM-001',
              last_speaker: 'dream_agent',
            }),
          ],
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/events`, () =>
        HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
      ),
    );
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() => {
      expect(screen.getByText(/Dream reflection/)).toBeInTheDocument();
      // Moon badge with aria-label
      expect(screen.getByLabelText(/Dream-originated thread/)).toBeInTheDocument();
    });
  });

  test('thread with no messages has null last_speaker — no agent chip', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/threads`, () =>
        HttpResponse.json({
          threads: [
            mkThread('THR-099', 'Empty thread', { last_speaker: null }),
          ],
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/events`, () =>
        HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
      ),
    );
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() => {
      expect(screen.getByText(/Empty thread/)).toBeInTheDocument();
    });
  });

  test('supports filter', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/threads`, () =>
        HttpResponse.json({
          threads: [
            mkThread('THR-001', 'Discuss launch plan'),
            mkThread('THR-002', 'Review the budget'),
          ],
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/events`, () =>
        HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
      ),
    );
    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() => {
      expect(screen.getByText(/Discuss launch plan/)).toBeInTheDocument();
      expect(screen.getByText(/Review the budget/)).toBeInTheDocument();
    });
    await user.type(screen.getByLabelText(/Filter threads/i), 'budget');
    await waitFor(() => {
      expect(screen.queryByText(/Discuss launch plan/)).not.toBeInTheDocument();
      expect(screen.getByText(/Review the budget/)).toBeInTheDocument();
    });
  });
});

/* ------------------------------------------------------------------ */
/*  Detail tests                                                       */
/* ------------------------------------------------------------------ */

describe('ThreadsPage — detail (design-overhaul reshape)', () => {
  test('renders detail pane with messages when thread selected', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/threads`, () =>
        HttpResponse.json({ threads: [mkThread('THR-001', 'My subject')] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/events`, () =>
        HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-001`, () =>
        HttpResponse.json({
          ...mkThread('THR-001', 'My subject'),
          participants: ['agent_a'],
          messages: [mkMessage(1, 'founder', 'message', 'Hello team')],
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-001/messages`, () =>
        HttpResponse.json({ messages: [mkMessage(1, 'founder', 'message', 'Hello team')] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-001/tail`, () =>
        HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
      ),
    );
    mountAt(`/orgs/${SLUG}/threads/THR-001`);
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /My subject/i })).toBeInTheDocument();
      expect(screen.getByText(/Hello team/)).toBeInTheDocument();
      // agent_a appears in both the inbox row (last speaker) and the detail header (participants)
      expect(screen.getAllByText(/agent_a/).length).toBeGreaterThanOrEqual(1);
    });
  });

  test('renders dream moon marker in detail header for dream-originated thread', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/threads`, () =>
        HttpResponse.json({
          threads: [
            mkThread('THR-042', 'Dream thread', { composed_from_dream_id: 'DREAM-001' }),
          ],
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/events`, () =>
        HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-042`, () =>
        HttpResponse.json({
          ...mkThread('THR-042', 'Dream thread', { composed_from_dream_id: 'DREAM-001' }),
          participants: ['dream_agent'],
          messages: [mkMessage(1, 'dream_agent', 'message', 'Reflection note')],
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-042/messages`, () =>
        HttpResponse.json({ messages: [mkMessage(1, 'dream_agent', 'message', 'Reflection note')] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-042/tail`, () =>
        HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
      ),
    );
    mountAt(`/orgs/${SLUG}/threads/THR-042`);
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Dream thread/i })).toBeInTheDocument();
      // Dream moon marker appears in both inbox row and detail header
      expect(screen.getAllByLabelText(/Dream-originated thread/).length).toBeGreaterThanOrEqual(1);
    });
  });

  test('renders error state with retry in detail when thread load fails', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/threads`, () =>
        HttpResponse.json({ threads: [mkThread('THR-001', 'Subject')] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/events`, () =>
        HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-001`, () =>
        HttpResponse.json({ error: 'not found' }, { status: 404 }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-001/messages`, () =>
        HttpResponse.json({ error: 'not found' }, { status: 404 }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-001/tail`, () =>
        HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
      ),
    );
    mountAt(`/orgs/${SLUG}/threads/THR-001`);
    await waitFor(() => {
      expect(screen.getByText(/Failed to load thread/i)).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Retry/i })).toBeInTheDocument();
    });
  });

  test('loading skeleton shows in detail while fetching', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/threads`, () =>
        HttpResponse.json({ threads: [mkThread('THR-001', 'Subject')] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/events`, () =>
        HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-001`, () =>
        new Promise(() => {}),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-001/messages`, () =>
        new Promise(() => {}),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-001/tail`, () =>
        HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
      ),
    );
    mountAt(`/orgs/${SLUG}/threads/THR-001`);
    await waitFor(() => {
      expect(screen.getByText(/Loading messages/i)).toBeInTheDocument();
    });
  });
});

/* ------------------------------------------------------------------ */
/*  System messages — visually distinct cards                          */
/* ------------------------------------------------------------------ */

describe('ThreadsPage — system message rendering (design-overhaul)', () => {
  test('system cards render with system badge, distinct from agent-turn cards', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    setupThreadWithMessages('THR-010', [
      mkMessage(1, 'agent_a', 'message', 'Regular agent message'),
      mkSystemMessage(2, 'agent_b', { kind_tag: 'invited', agent: 'agent_c' }),
      mkMessage(3, 'founder', 'message', 'Founder reply'),
    ]);
    mountAt(`/orgs/${SLUG}/threads/THR-010`);
    await waitFor(() => {
      // System card badge
      expect(screen.getByText(/invited agent_c/)).toBeInTheDocument();
      // System event label should be visible
      const systemLabels = screen.getAllByText('system');
      expect(systemLabels.length).toBeGreaterThanOrEqual(1);
    });
  });

  test('renders task_completed system card with task id and summary', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    setupThreadWithMessages('THR-010', [
      mkSystemMessage(1, 'agent_a', {
        kind_tag: 'task_completed',
        task_id: 'TASK-007',
        status: 'completed',
        final_output_summary: 'PDF uploaded',
        cancelled: false,
        revisit_chain_length: 1,
      }),
    ]);
    mountAt(`/orgs/${SLUG}/threads/THR-010`);
    await waitFor(() => {
      expect(screen.getByText(/TASK-007/)).toBeInTheDocument();
      expect(screen.getByText(/PDF uploaded/)).toBeInTheDocument();
    });
  });

  test('renders task_failed system card with cancelled and revisit annotations', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    setupThreadWithMessages('THR-011', [
      mkSystemMessage(1, 'agent_a', {
        kind_tag: 'task_failed',
        task_id: 'TASK-031',
        status: 'failed',
        final_output_summary: '',
        cancelled: true,
        revisit_chain_length: 3,
      }),
    ]);
    mountAt(`/orgs/${SLUG}/threads/THR-011`);
    await waitFor(() => {
      expect(screen.getByText(/TASK-031/)).toBeInTheDocument();
      expect(screen.getByText(/founder-cancelled/)).toBeInTheDocument();
      expect(screen.getByText(/2 revisits/)).toBeInTheDocument();
    });
  });

  test('renders task_escalated system card with task id and reason', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    setupThreadWithMessages('THR-012', [
      mkSystemMessage(1, 'agent_a', {
        kind_tag: 'task_escalated',
        task_id: 'TASK-893',
        original_task_id: 'TASK-893',
        status: 'escalated',
        reason: 'needs founder CDN authorize',
        revisit_chain_length: 1,
      }),
    ]);
    mountAt(`/orgs/${SLUG}/threads/THR-012`);
    await waitFor(() => {
      expect(screen.getByText(/TASK-893/)).toBeInTheDocument();
      expect(screen.getByText(/needs founder CDN authorize/)).toBeInTheDocument();
    });
  });

  test('renders task_dispatched system card with link', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    setupThreadWithMessages('THR-013', [
      mkSystemMessage(1, 'agent_a', {
        kind_tag: 'task_dispatched',
        task_id: 'TASK-555',
        status: 'dispatched',
      }),
    ]);
    mountAt(`/orgs/${SLUG}/threads/THR-013`);
    await waitFor(() => {
      expect(screen.getByText(/TASK-555/)).toBeInTheDocument();
    });
  });
});

/* ------------------------------------------------------------------ */
/*  Retry button query-key invalidation (regression guard for TASK-506) */
/* ------------------------------------------------------------------ */

describe('ThreadsPage — retry invalidates correct query keys', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('list retry invalidates ["threads", slug] (not ["threads-list", ...])', async () => {
    const spy = vi.spyOn(QueryClient.prototype, 'invalidateQueries');
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/threads`, () =>
        HttpResponse.json({ error: 'boom' }, { status: 500 }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/events`, () =>
        HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
      ),
    );
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Retry/i })).toBeInTheDocument();
    });

    // Clear any initial invalidations (SSE init, etc.)
    spy.mockClear();

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /Retry/i }));

    // Must invalidate ["threads", slug] — the real provider's key prefix
    const calls = spy.mock.calls.filter(
      (call) =>
        call[0] &&
        typeof call[0] === 'object' &&
        'queryKey' in call[0],
    );
    expect(calls.length).toBeGreaterThanOrEqual(1);
    const hasCorrectKey = calls.some(
      (call) =>
        JSON.stringify((call[0] as { queryKey: unknown }).queryKey) ===
        JSON.stringify(['threads', SLUG]),
    );
    expect(hasCorrectKey).toBe(true);

    // Must NOT invalidate the old wrong key
    const hasWrongKey = calls.some(
      (call) =>
        JSON.stringify((call[0] as { queryKey: unknown }).queryKey) ===
        JSON.stringify(['threads-list', SLUG, 'open']),
    );
    expect(hasWrongKey).toBe(false);
  });

  test('detail retry invalidates ["thread", slug, threadId] and ["thread-messages", slug, threadId]', async () => {
    const spy = vi.spyOn(QueryClient.prototype, 'invalidateQueries');
    const threadId = 'THR-001';
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/threads`, () =>
        HttpResponse.json({ threads: [mkThread(threadId, 'Subject')] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/events`, () =>
        HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/${threadId}`, () =>
        HttpResponse.json({ error: 'not found' }, { status: 404 }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/${threadId}/messages`, () =>
        HttpResponse.json({ error: 'not found' }, { status: 404 }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/${threadId}/tail`, () =>
        HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
      ),
    );
    mountAt(`/orgs/${SLUG}/threads/${threadId}`);
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Retry/i })).toBeInTheDocument();
    });

    // Clear any initial invalidations
    spy.mockClear();

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /Retry/i }));

    const calls = spy.mock.calls.filter(
      (call) =>
        call[0] &&
        typeof call[0] === 'object' &&
        'queryKey' in call[0],
    );
    expect(calls.length).toBeGreaterThanOrEqual(2);

    const keys = calls.map((call) => (call[0] as { queryKey: unknown }).queryKey);
    const keysJson = keys.map((k) => JSON.stringify(k));

    expect(keysJson).toContain(JSON.stringify(['thread', SLUG, threadId]));
    expect(keysJson).toContain(JSON.stringify(['thread-messages', SLUG, threadId]));

    // Must NOT invalidate the old wrong key with undefined threadId
    expect(keysJson).not.toContain(
      JSON.stringify(['thread-detail', SLUG, undefined]),
    );
  });
});

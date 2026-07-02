import { screen, waitFor, within } from '@testing-library/react';
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

  test('lists threads without turn budget and with last speaker', async () => {
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
      // Turn budgets must NOT appear (THR-046 msg126 — turn cap UI removed)
      expect(screen.queryByText('3/500')).not.toBeInTheDocument();
      expect(screen.queryByText('487/500')).not.toBeInTheDocument();
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
      expect(screen.getByLabelText(/Dream-originated/)).toBeInTheDocument();
      // THREADS-05: dream origin surfaces as a labeled "from dream" pill.
      expect(screen.getByText('from dream')).toBeInTheDocument();
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

  test('no thread selected renders full-width list, not the master-detail "Select a thread" pane (THREADS-01)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/threads`, () =>
        HttpResponse.json({ threads: [mkThread('THR-001', 'Launch plan')] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/events`, () =>
        HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
      ),
    );
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() => {
      expect(screen.getByText(/Launch plan/)).toBeInTheDocument();
    });
    // The empty "Select a thread" detail placeholder must NOT render on /threads;
    // the list is the full-width single column, with detail shown only on navigate.
    expect(screen.queryByText(/Select a thread/i)).not.toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */
/*  Detail tests                                                       */
/* ------------------------------------------------------------------ */

describe('ThreadsPage — detail (design-overhaul reshape)', () => {
  test('detail pane has no Extend button, no turn budget rail, no turn meter (THR-046 msg126 regression)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    setupThreadWithMessages('THR-001', [mkMessage(1, 'founder', 'message', 'Hello team')]);
    mountAt(`/orgs/${SLUG}/threads/THR-001`);
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Test thread/i })).toBeInTheDocument();
    });
    // No Extend action button in the detail header actions
    expect(screen.queryByRole('button', { name: /Extend/i })).not.toBeInTheDocument();
    // No turn meter like "47/500" or "turns used" anywhere in the detail
    expect(screen.queryByText(/\/500/)).not.toBeInTheDocument();
    expect(screen.queryByText(/turns? used/i)).not.toBeInTheDocument();
    // Properties rail has no "Turn budget" section
    const rail = screen.getByLabelText('Thread properties');
    expect(within(rail).queryByText(/turn budget/i)).not.toBeInTheDocument();
    expect(within(rail).queryByText(/turn cap/i)).not.toBeInTheDocument();
    // No extend dialog rendered
    expect(screen.queryByRole('dialog', { name: /extend/i })).not.toBeInTheDocument();
  });

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
      expect(screen.getAllByLabelText(/Dream-originated/).length).toBeGreaterThanOrEqual(1);
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
/*  Transcript-focus view — list column collapses (THREADDET-01)       */
/* ------------------------------------------------------------------ */

describe('ThreadsPage — transcript focus (THREADDET-01)', () => {
  test('list column (filter + status segments) is absent when a thread is selected', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    setupThreadWithMessages('THR-100', [mkMessage(1, 'founder', 'message', 'Hello team')]);
    mountAt(`/orgs/${SLUG}/threads/THR-100`);
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Test thread/i })).toBeInTheDocument();
    });
    // The middle thread-LIST column collapses on a selected thread: its
    // filter input and the All/Open/Done status segments must not render.
    expect(screen.queryByLabelText(/Filter threads/i)).not.toBeInTheDocument();
    expect(
      screen.queryByRole('tablist', { name: /status filter/i }),
    ).not.toBeInTheDocument();
  });

  test('list column is present as the single column on /threads (no thread selected)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/threads`, () =>
        HttpResponse.json({ threads: [mkThread('THR-001', 'Launch plan')] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/events`, () =>
        HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
      ),
    );
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() => {
      expect(screen.getByText(/Launch plan/)).toBeInTheDocument();
    });
    // The list column (filter + status segments) is the single column here.
    expect(screen.getByLabelText(/Filter threads/i)).toBeInTheDocument();
    expect(
      screen.getByRole('tablist', { name: /status filter/i }),
    ).toBeInTheDocument();
  });

  test('transcript-focus view exposes a back affordance to the thread list', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    setupThreadWithMessages('THR-100', [mkMessage(1, 'founder', 'message', 'Hello team')]);
    mountAt(`/orgs/${SLUG}/threads/THR-100`);
    const back = await screen.findByRole('link', { name: /all threads/i });
    expect(back).toHaveAttribute('href', `/orgs/${SLUG}/threads`);
  });
});

/* ------------------------------------------------------------------ */
/*  Structured detail rail (THREADDET-02)                              */
/* ------------------------------------------------------------------ */

describe('ThreadsPage — structured detail rail (THREADDET-02)', () => {
  type Attachment = {
    artifact_name: string;
    display_name: string;
    size_bytes: number | null;
    content_type: string | null;
    uploaded_by: string;
  };

  function mkMessageWithAttachments(
    seq: number,
    speaker: string,
    body: string,
    attachments: Attachment[],
  ) {
    return { ...mkMessage(seq, speaker, 'message', body), attachments };
  }

  function setupDetail(
    threadId: string,
    participants: string[],
    messages: ReturnType<typeof mkMessage | typeof mkMessageWithAttachments>[],
  ) {
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/threads`, () =>
        HttpResponse.json({ threads: [mkThread(threadId, 'Rail thread')] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/events`, () =>
        HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/${threadId}`, () =>
        HttpResponse.json({ ...mkThread(threadId, 'Rail thread'), participants, messages }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/${threadId}/messages`, () =>
        HttpResponse.json({ messages }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/${threadId}/tail`, () =>
        HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
      ),
    );
  }

  test('participants render as AgentChip avatar chips (role-colored dots) in the rail', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    setupDetail('THR-200', ['founder', 'dev_agent', 'engineering_manager'], [
      mkMessage(1, 'founder', 'message', 'Kickoff'),
    ]);
    mountAt(`/orgs/${SLUG}/threads/THR-200`);
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Rail thread/i })).toBeInTheDocument();
    });
    const rail = screen.getByLabelText('Thread properties');
    // Every participant name is rendered in the rail (accessible text preserved).
    expect(within(rail).getByText('dev_agent')).toBeInTheDocument();
    expect(within(rail).getByText('engineering_manager')).toBeInTheDocument();
    // The rail uses the AgentChip idiom — role-colored dots. The plain header
    // join() carries no such dots, so their presence proves the avatar chips.
    expect(rail.querySelectorAll('.bg-agent-worker').length).toBeGreaterThanOrEqual(1);
    expect(rail.querySelector('.bg-agent-founder')).not.toBeNull();
  });

  test('aggregates message attachments into an Artifacts section, deduped by artifact_name', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const report: Attachment = {
      artifact_name: 'dev_agent/2026-06-21/report.pdf',
      display_name: 'Q2 Report.pdf',
      size_bytes: 2048,
      content_type: 'application/pdf',
      uploaded_by: 'dev_agent',
    };
    const data: Attachment = {
      artifact_name: 'dev_agent/2026-06-21/data.csv',
      display_name: 'metrics.csv',
      size_bytes: 512,
      content_type: 'text/csv',
      uploaded_by: 'dev_agent',
    };
    setupDetail('THR-201', ['dev_agent'], [
      mkMessageWithAttachments(1, 'dev_agent', 'first', [report, data]),
      // Re-attached SAME artifact_name in a later message — must dedupe to one.
      mkMessageWithAttachments(2, 'dev_agent', 'second', [report]),
    ]);
    mountAt(`/orgs/${SLUG}/threads/THR-201`);
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Rail thread/i })).toBeInTheDocument();
    });
    const rail = screen.getByLabelText('Thread properties');
    expect(within(rail).getByText('Artifacts')).toBeInTheDocument();
    // Real produced-artifact display names appear; the duplicate appears once.
    expect(within(rail).getAllByText('Q2 Report.pdf')).toHaveLength(1);
    expect(within(rail).getByText('metrics.csv')).toBeInTheDocument();
  });

  test('Artifacts section is absent when no message carries an attachment', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    setupDetail('THR-202', ['dev_agent'], [
      mkMessage(1, 'dev_agent', 'message', 'No files here'),
    ]);
    mountAt(`/orgs/${SLUG}/threads/THR-202`);
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Rail thread/i })).toBeInTheDocument();
    });
    // No attachment anywhere → no Artifacts section in the rail (the global
    // sidebar "Artifacts" nav link is a separate landmark, excluded by scope).
    const rail = screen.getByLabelText('Thread properties');
    expect(within(rail).queryByText('Artifacts')).not.toBeInTheDocument();
  });

  test('deferred rail sections are not fabricated (no Linked items / Token cost)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    setupDetail('THR-203', ['dev_agent'], [
      mkMessage(1, 'dev_agent', 'message', 'Hello'),
    ]);
    mountAt(`/orgs/${SLUG}/threads/THR-203`);
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Rail thread/i })).toBeInTheDocument();
    });
    // No fabricated sections that lack a real ThreadDetailResponse data source.
    const rail = screen.getByLabelText('Thread properties');
    expect(within(rail).queryByText(/linked items/i)).not.toBeInTheDocument();
    expect(within(rail).queryByText(/token cost/i)).not.toBeInTheDocument();
    expect(within(rail).queryByText(/pull request/i)).not.toBeInTheDocument();
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

  test('renders task_failed system card with cancelled and no-further-revisits annotations', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    setupThreadWithMessages('THR-011', [
      mkSystemMessage(1, 'agent_a', {
        kind_tag: 'task_failed',
        task_id: 'TASK-031',
        status: 'failed',
        final_output_summary: '',
        cancelled: true,
        revisit_chain_length: 3,
        revisit_task_id: null,
      }),
    ]);
    mountAt(`/orgs/${SLUG}/threads/THR-011`);
    await waitFor(() => {
      expect(screen.getByText(/TASK-031/)).toBeInTheDocument();
      expect(screen.getByText(/founder-cancelled/)).toBeInTheDocument();
      expect(screen.getByText(/no further revisits/)).toBeInTheDocument();
    });
  });

  test('renders task_failed system card with revisiting-as successor link', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    setupThreadWithMessages('THR-014', [
      mkSystemMessage(1, 'agent_a', {
        kind_tag: 'task_failed',
        task_id: 'TASK-032',
        status: 'failed',
        final_output_summary: '',
        cancelled: false,
        revisit_chain_length: 1,
        revisit_task_id: 'TASK-033',
      }),
    ]);
    mountAt(`/orgs/${SLUG}/threads/THR-014`);
    await waitFor(() => {
      expect(screen.getByText(/TASK-032/)).toBeInTheDocument();
      expect(screen.getByText(/revisiting as/)).toBeInTheDocument();
      expect(screen.getByText(/TASK-033/)).toBeInTheDocument();
    });
  });

  test('renders ordinary task_failed with no revisit suffix', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    setupThreadWithMessages('THR-015', [
      mkSystemMessage(1, 'agent_a', {
        kind_tag: 'task_failed',
        task_id: 'TASK-034',
        status: 'failed',
        final_output_summary: '',
        cancelled: false,
        revisit_chain_length: 1,
        revisit_task_id: null,
      }),
    ]);
    mountAt(`/orgs/${SLUG}/threads/THR-015`);
    await waitFor(() => {
      expect(screen.getByText(/TASK-034/)).toBeInTheDocument();
      expect(screen.getByText(/failed/)).toBeInTheDocument();
      expect(screen.queryByText(/revisiting/)).not.toBeInTheDocument();
      expect(screen.queryByText(/no further revisits/)).not.toBeInTheDocument();
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
/*  Segmented status filter — All / Open / Done (THREADS-02)           */
/* ------------------------------------------------------------------ */

describe('ThreadsPage — segmented status filter (THREADS-02)', () => {
  // Status-aware handler so the open and archived per-status fetches return
  // disjoint sets — All is derived client-side by merging them.
  function mountWithBuckets() {
    const open = [
      mkThread('THR-O1', 'Open alpha', { status: 'open' }),
      mkThread('THR-O2', 'Open beta', { status: 'open' }),
    ];
    const archived = [
      mkThread('THR-A1', 'Archived gamma', { status: 'archived' }),
    ];
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/threads`, ({ request }) => {
        const status = new URL(request.url).searchParams.get('status');
        if (status === 'archived') return HttpResponse.json({ threads: archived });
        if (status === 'open') return HttpResponse.json({ threads: open });
        return HttpResponse.json({ threads: [...open, ...archived] });
      }),
      http.get(`/api/v1/orgs/${SLUG}/threads/events`, () =>
        HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
      ),
    );
  }

  test('renders all / open / done segments with honest per-bucket counts', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    mountWithBuckets();
    mountAt(`/orgs/${SLUG}/threads`);

    // Three segments, each backed by a real count: All=3, Open=2, Done=1.
    await waitFor(() => {
      expect(screen.getByRole('tab', { name: /all/i })).toHaveTextContent(/3/);
      expect(screen.getByRole('tab', { name: /open/i })).toHaveTextContent(/2/);
      expect(screen.getByRole('tab', { name: /done/i })).toHaveTextContent(/1/);
    });

    // Default bucket "open": open threads visible, archived hidden.
    expect(screen.getByText(/Open alpha/)).toBeInTheDocument();
    expect(screen.queryByText(/Archived gamma/)).not.toBeInTheDocument();
  });

  test('selecting a bucket filters the visible list', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    mountWithBuckets();
    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/threads`);

    await waitFor(() => {
      expect(screen.getByText(/Open alpha/)).toBeInTheDocument();
    });

    // Select "Done" → only the archived thread shows.
    await user.click(screen.getByRole('tab', { name: /done/i }));
    await waitFor(() => {
      expect(screen.getByText(/Archived gamma/)).toBeInTheDocument();
    });
    expect(screen.queryByText(/Open alpha/)).not.toBeInTheDocument();

    // Select "All" → open + archived both show.
    await user.click(screen.getByRole('tab', { name: /all/i }));
    await waitFor(() => {
      expect(screen.getByText(/Open alpha/)).toBeInTheDocument();
      expect(screen.getByText(/Archived gamma/)).toBeInTheDocument();
    });
  });

  test('segmented control exposes an accessible group label', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    mountWithBuckets();
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() => {
      expect(
        screen.getByRole('tablist', { name: /status filter/i }),
      ).toBeInTheDocument();
    });
  });
});

/* ------------------------------------------------------------------ */
/*  List header — serif eyebrow + title (THREADS-04)                   */
/* ------------------------------------------------------------------ */

describe('ThreadsPage — list header eyebrow + serif title (THREADS-04)', () => {
  // Status-aware handler: open + archived are disjoint, so the org-wide header
  // counts (total threads, dream-opened) are derived across BOTH buckets.
  function mountWithHeaderData() {
    const open = [
      mkThread('THR-O1', 'Open alpha', { status: 'open' }),
      mkThread('THR-O2', 'Open beta', {
        status: 'open',
        composed_from_dream_id: 'DREAM-007',
      }),
    ];
    const archived = [mkThread('THR-A1', 'Archived gamma', { status: 'archived' })];
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/threads`, ({ request }) => {
        const status = new URL(request.url).searchParams.get('status');
        if (status === 'archived') return HttpResponse.json({ threads: archived });
        if (status === 'open') return HttpResponse.json({ threads: open });
        return HttpResponse.json({ threads: [...open, ...archived] });
      }),
      http.get(`/api/v1/orgs/${SLUG}/threads/events`, () =>
        HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
      ),
    );
  }

  test('renders the serif (font-display) title "Conversations across the org"', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    mountWithHeaderData();
    mountAt(`/orgs/${SLUG}/threads`);
    const title = await screen.findByRole('heading', {
      name: /Conversations across the org/i,
    });
    // Serif display role — the same font-display token the KB/Audit headers use.
    expect(title).toHaveClass('font-display');
  });

  test('eyebrow shows org-wide thread count and dream-opened count (data-backed)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    mountWithHeaderData();
    mountAt(`/orgs/${SLUG}/threads`);
    // 3 threads total (2 open + 1 archived), 1 opened from a dream.
    await waitFor(() => {
      expect(
        screen.getByText(/3\s+THREADS\b.*\b1\s+DREAM-OPENED/i),
      ).toBeInTheDocument();
    });
  });

  test('singular thread count is not pluralized', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/threads`, ({ request }) => {
        const status = new URL(request.url).searchParams.get('status');
        if (status === 'archived') return HttpResponse.json({ threads: [] });
        return HttpResponse.json({ threads: [mkThread('THR-1', 'Solo')] });
      }),
      http.get(`/api/v1/orgs/${SLUG}/threads/events`, () =>
        HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
      ),
    );
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() => {
      expect(screen.getByText(/\b1\s+THREAD\b/)).toBeInTheDocument();
    });
    // "1 THREAD", never "1 THREADS".
    expect(screen.queryByText(/\b1\s+THREADS\b/)).not.toBeInTheDocument();
  });

  test('omits the unbacked "waiting on you" eyebrow segment (no awaiting-founder field)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    mountWithHeaderData();
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() => {
      expect(
        screen.getByRole('heading', { name: /Conversations across the org/i }),
      ).toBeInTheDocument();
    });
    // The threads-list payload exposes no awaiting-founder field, so the
    // Direction-A "X WAITING ON YOU" segment is honestly omitted, not faked.
    expect(screen.queryByText(/waiting on you/i)).not.toBeInTheDocument();
  });

  // THREADS-06: the new-thread button shows the FULL Direction-A label,
  // not a truncated "+ New". The accessible name still comes from the
  // aria-label ("New thread"); this guards the *visible* text.
  test('new-thread button shows the full "+ New thread" label', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    mountWithHeaderData();
    mountAt(`/orgs/${SLUG}/threads`);
    const button = await screen.findByRole('button', { name: /New thread/i });
    expect(button).toHaveTextContent('+ New thread');
  });
});

/* ------------------------------------------------------------------ */
/*  Abort replies button — appears when queued/working responders exist */
/* ------------------------------------------------------------------ */

describe('ThreadsPage — abort replies', () => {
  const threadId = 'THR-001';

  function mkMsgWithResponders(
    seq: number,
    responders: Array<{ agent_name: string; status: string }>,
  ) {
    return {
      seq,
      speaker: 'founder',
      kind: 'message' as const,
      body_markdown: 'hi',
      decline_reason: null,
      system_payload: null,
      created_at: '2026-05-14T00:00:00Z',
      attachments: [],
      responder_status: responders.map((r) => ({
        agent_name: r.agent_name,
        status: r.status,
        responded_at: null,
        started_at: null,
      })),
    };
  }

  function mountThreadWithResponders(
    responders: Array<{ agent_name: string; status: string }>,
  ) {
    const thread = mkThread(threadId, 'Test thread');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/threads`, () =>
        HttpResponse.json({ threads: [thread] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/${threadId}`, () =>
        HttpResponse.json({
          ...thread,
          participants: ['dev_agent'],
          messages: [mkMsgWithResponders(1, responders)],
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/${threadId}/messages`, () =>
        HttpResponse.json({
          messages: [mkMsgWithResponders(1, responders)],
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/${threadId}/tail`, () =>
        HttpResponse.text('', {
          headers: { 'content-type': 'text/event-stream' },
        }),
      ),
    );
    return mountAt(`/orgs/${SLUG}/threads/${threadId}`);
  }

  test('abort button appears in composer footer when thread has queued/working responders', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    mountThreadWithResponders([
      { agent_name: 'dev_agent', status: 'working' },
    ]);

    const btn = await screen.findByRole('button', { name: /Abort replies/i });
    expect(btn).toBeEnabled();
    // The button must be in the composer footer, not the header actions area
    const footer = document.querySelector('footer');
    expect(footer).not.toBeNull();
    expect(footer!.contains(btn)).toBe(true);
  });

  test('abort button is disabled when no in-flight responders', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    mountThreadWithResponders([]);

    // Wait for detail to render.
    await screen.findByText('Test thread');
    const btn = screen.getByRole('button', { name: /Abort replies/i });
    expect(btn).toBeDisabled();
  });

  test('disabled abort button does not call POST when clicked', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');

    let abortHit = false;
    server.use(
      http.post(
        `/api/v1/orgs/${SLUG}/threads/${threadId}/abort-replies`,
        () => {
          abortHit = true;
          return HttpResponse.json({
            thread_id: threadId,
            aborted_count: 1,
          });
        },
      ),
    );

    mountThreadWithResponders([]);

    await screen.findByText('Test thread');
    const btn = screen.getByRole('button', { name: /Abort replies/i });
    expect(btn).toBeDisabled();

    const user = userEvent.setup();
    await user.click(btn);

    // The POST must NOT have been made — disabled button blocks the action.
    expect(abortHit).toBe(false);
  });

  test('abort button calls POST /abort-replies exactly once when enabled', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');

    let abortCount = 0;
    server.use(
      http.post(
        `/api/v1/orgs/${SLUG}/threads/${threadId}/abort-replies`,
        () => {
          abortCount++;
          return HttpResponse.json({
            thread_id: threadId,
            aborted_count: 1,
          });
        },
      ),
    );

    mountThreadWithResponders([
      { agent_name: 'dev_agent', status: 'queued' },
    ]);

    const btn = await screen.findByRole('button', { name: /Abort replies/i });
    expect(btn).toBeEnabled();

    const user = userEvent.setup();
    await user.click(btn);

    // Verify the POST was made exactly once.
    await waitFor(() => {
      expect(abortCount).toBe(1);
    });
  });

  test('abort button is enabled with queued responder', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    mountThreadWithResponders([
      { agent_name: 'dev_agent', status: 'queued' },
    ]);

    const btn = await screen.findByRole('button', { name: /Abort replies/i });
    expect(btn).toBeEnabled();
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

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'alpha';

function mountAt(route: string) {
  // Most ThreadsPage tests don't need a real org list — but the TopBar fetches
  // /api/v1/orgs unconditionally. Stub a default before each.
  // ThreadsPage also calls useAgentsList() on mount, so stub that endpoint too.
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

describe('ThreadsPage — read path', () => {
  test('renders inbox + empty state when no threads', async () => {
    sessionStorage.setItem('grassland.token', 'tok');
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

  test('lists threads and supports filter', async () => {
    sessionStorage.setItem('grassland.token', 'tok');
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

  test('renders detail pane with messages when a thread is selected', async () => {
    sessionStorage.setItem('grassland.token', 'tok');
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
      // Subject appears in both inbox row and header — match the header explicitly.
      expect(screen.getByRole('heading', { name: /My subject/i })).toBeInTheDocument();
      expect(screen.getByText(/Hello team/)).toBeInTheDocument();
      expect(screen.getByText(/agent_a/)).toBeInTheDocument();
    });
  });
});

describe('ThreadsPage — system message rendering', () => {
  test('renders task_completed system message with task id and summary', async () => {
    sessionStorage.setItem('grassland.token', 'tok');
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

  test('renders task_failed system message with cancelled and revisit annotations', async () => {
    sessionStorage.setItem('grassland.token', 'tok');
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
});

function mkThread(id: string, subject: string) {
  return {
    thread_id: id,
    subject,
    status: 'open',
    started_at: '2026-05-14T00:00:00Z',
    archived_at: null,
    forwarded_from_id: null,
    forwarded_from_kind: null,
    turn_cap: 500,
    turns_used: 1,
    summary: null,
    new_kb_slugs: null,
    transcript_path: null,
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

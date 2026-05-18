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
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
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
    addressed_to: ['@all'],
    decline_reason: kind === 'decline' ? body : null,
    system_payload: kind === 'system' ? { event: body } : null,
    created_at: '2026-05-14T00:00:00Z',
  };
}

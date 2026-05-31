import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'alpha';

function stubBaseHandlers() {
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/agents`, () =>
      HttpResponse.json({ agents: [] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/threads`, () => HttpResponse.json({ threads: [] })),
    http.get(`/api/v1/orgs/${SLUG}/threads/events`, () =>
      HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
    ),
  );
}

describe('ThreadsPage — write path', () => {
  test('NewThreadDialog posts compose body and navigates to the new thread', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    let body: unknown = null;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/threads`, async ({ request: req }) => {
        body = await req.json();
        return HttpResponse.json(
          { thread_id: 'THR-007', started_at: 'now', pending_replies: 1 },
          { status: 201 },
        );
      }),
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-007`, () =>
        HttpResponse.json({
          thread_id: 'THR-007',
          subject: 'Hi',
          status: 'open',
          started_at: 'now',
          archived_at: null,
          forwarded_from_id: null,
          forwarded_from_kind: null,
          turn_cap: 500,
          turns_used: 0,
          summary: null,
          new_kb_slugs: null,
          transcript_path: null,
          participants: ['agent_a'],
          messages: [],
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-007/messages`, () =>
        HttpResponse.json({ messages: [] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-007/tail`, () =>
        HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
      ),
    );

    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads` });
    // Open the dialog
    await user.click(await screen.findByRole('button', { name: /New thread/i }));
    await user.type(screen.getByLabelText(/^Subject$/i), 'Hi');
    await user.type(screen.getByLabelText(/^Recipients/i), 'agent_a');
    await user.type(screen.getByLabelText(/^Body \(Markdown\)$/i), 'Hello team');
    await user.click(screen.getByRole('button', { name: /^Send$/i }));

    await waitFor(() => {
      expect(body).toEqual({
        subject: 'Hi',
        recipients: ['agent_a'],
        body_markdown: 'Hello team',
      });
    });
    // After success, the page should navigate to the new thread's URL — header renders subject.
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: /Hi/i })).toBeInTheDocument(),
    );
  });

  test('Composer posts a follow-up message', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    let body: unknown = null;
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-001`, () =>
        HttpResponse.json({
          thread_id: 'THR-001',
          subject: 'Existing thread',
          status: 'open',
          started_at: 'now',
          archived_at: null,
          forwarded_from_id: null,
          forwarded_from_kind: null,
          turn_cap: 500,
          turns_used: 1,
          summary: null,
          new_kb_slugs: null,
          transcript_path: null,
          participants: ['agent_a'],
          messages: [],
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-001/messages`, () =>
        HttpResponse.json({ messages: [] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-001/tail`, () =>
        HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
      ),
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-001/send`, async ({ request: req }) => {
        body = await req.json();
        return HttpResponse.json({ thread_id: 'THR-001', seq: 2 });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads/THR-001` });
    await user.type(await screen.findByLabelText(/Compose follow-up/i), 'Quick update');
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    await waitFor(() => {
      expect(body).toEqual({ body_markdown: 'Quick update' });
    });
  });
});

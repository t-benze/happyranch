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

  test('Composer uploads attachment before sending follow-up', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    let uploadHit = false;
    let sent: unknown = null;
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
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, async () => {
        uploadHit = true;
        return HttpResponse.json({
          name: 'THR-001-report.pdf',
          size_bytes: 3,
          modified_at: '2026-06-09T00:00:00Z',
        });
      }),
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-001/send`, async ({ request: req }) => {
        sent = await req.json();
        return HttpResponse.json({ thread_id: 'THR-001', seq: 2 });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads/THR-001` });
    const file = new File(['pdf'], 'report.pdf', { type: 'application/pdf' });

    await user.upload(await screen.findByLabelText(/Attach files/i), file);
    await user.click(screen.getByRole('button', { name: /^Send$/i }));

    await waitFor(() => expect(uploadHit).toBe(true));
    await waitFor(() =>
      expect(sent).toEqual({
        body_markdown: '',
        attachments: [{ artifact_name: 'THR-001-report.pdf', display_name: 'report.pdf' }],
      }),
    );
  });

  test('message bubble renders attachment download link', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
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
          transcript_path: null,
          participants: ['agent_a'],
          messages: [],
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-001/messages`, () =>
        HttpResponse.json({
          messages: [
            {
              seq: 1,
              speaker: 'founder',
              kind: 'message',
              body_markdown: null,
              decline_reason: null,
              system_payload: null,
              created_at: '2026-06-09T00:00:00Z',
              responder_status: [],
              attachments: [
                {
                  artifact_name: 'THR-001-report.pdf',
                  display_name: 'report.pdf',
                  size_bytes: 3,
                  content_type: null,
                  uploaded_by: 'founder',
                },
              ],
            },
          ],
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-001/tail`, () =>
        HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
      ),
    );

    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads/THR-001` });

    expect(await screen.findByRole('link', { name: /report\.pdf/i })).toHaveAttribute(
      'href',
      '/api/v1/orgs/alpha/artifacts/THR-001-report.pdf',
    );
  });
});

/**
 * TASK-8599 — NewThreadDialog attachment cases (accepted case-design C1.3, C2
 * new-thread variant, C8.3). Real `<AppProvider>` + router + MSW boundary.
 */
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
    http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] })),
    http.get(`/api/v1/orgs/${SLUG}/agents`, () => HttpResponse.json({ agents: [] })),
    http.get(`/api/v1/orgs/${SLUG}/threads`, () => HttpResponse.json({ threads: [] })),
    http.get(`/api/v1/orgs/${SLUG}/threads/events`, () =>
      HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/tokens`, () => HttpResponse.json({ rollup: [] })),
  );
}

function stubCreatedThread(threadId: string) {
  server.use(
    http.get(`/api/v1/orgs/${SLUG}/threads/${threadId}`, () =>
      HttpResponse.json({
        thread_id: threadId,
        subject: 'Created',
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
    http.get(`/api/v1/orgs/${SLUG}/threads/${threadId}/messages`, () =>
      HttpResponse.json({ messages: [] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/threads/${threadId}/tail`, () =>
      HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
    ),
  );
}

function requestedName(request: Request): string {
  return new URL(request.url).searchParams.get('name') ?? '';
}

async function openDialog(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: /New thread/i }));
}

describe('NewThreadDialog attachments', () => {
  test('uploads the file before a JSON compose and passes the ref (C1.3)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    stubCreatedThread('THR-NEW');
    const order: string[] = [];
    let composeBody: unknown = null;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, async ({ request }) => {
        order.push('upload');
        return HttpResponse.json({ name: requestedName(request), size_bytes: 3, modified_at: 'now' });
      }),
      http.post(`/api/v1/orgs/${SLUG}/threads`, async ({ request }) => {
        order.push('compose');
        composeBody = await request.json();
        return HttpResponse.json(
          { thread_id: 'THR-NEW', started_at: 'now', pending_replies: 1 },
          { status: 201 },
        );
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads` });
    await openDialog(user);
    await user.type(screen.getByLabelText(/^Subject$/i), 'Hi');
    await user.type(screen.getByLabelText(/^Recipients/i), 'agent_a');
    await user.type(screen.getByLabelText(/^Body \(Markdown\)$/i), 'Hello');
    await user.upload(
      screen.getByLabelText(/Attach files/i),
      new File(['pdf'], 'report.pdf', { type: 'application/pdf' }),
    );
    await user.click(screen.getByRole('button', { name: /^Send$/i }));

    await waitFor(() => expect(composeBody).not.toBeNull());
    expect(order).toEqual(['upload', 'compose']);
    expect(composeBody).toMatchObject({
      subject: 'Hi',
      recipients: ['agent_a'],
      body_markdown: 'Hello',
      attachments: [
        {
          display_name: 'report.pdf',
          content_type: 'application/pdf',
        },
      ],
    });
  });

  test('a 413 upload rejection sends no compose and keeps the dialog open (C2 new-thread)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    let composeCount = 0;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, () =>
        HttpResponse.json(
          { detail: { code: 'artifact_too_large', max_bytes: 10, size_bytes: 11 } },
          { status: 413 },
        ),
      ),
      http.post(`/api/v1/orgs/${SLUG}/threads`, () => {
        composeCount += 1;
        return HttpResponse.json(
          { thread_id: 'THR-NEW', started_at: 'now', pending_replies: 1 },
          { status: 201 },
        );
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads` });
    await openDialog(user);
    await user.type(screen.getByLabelText(/^Subject$/i), 'Hi');
    await user.type(screen.getByLabelText(/^Recipients/i), 'agent_a');
    await user.upload(
      screen.getByLabelText(/Attach files/i),
      new File(['0123456789'], 'big.txt', { type: 'text/plain' }),
    );
    await user.click(screen.getByRole('button', { name: /^Send$/i }));

    const error = await screen.findByText(/too large to upload/i);
    expect(error.textContent).toContain('big.txt');
    expect(composeCount).toBe(0);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  test('a closed/reopened dialog is not closed by the abandoned submission (C8.3)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    stubCreatedThread('THR-NEW');
    let composeCount = 0;
    let releaseUpload: (value: unknown) => void = () => {};
    const held = new Promise((resolve) => { releaseUpload = resolve; });
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, async ({ request }) => {
        await held;
        return HttpResponse.json({ name: requestedName(request), size_bytes: 3, modified_at: 'now' });
      }),
      http.post(`/api/v1/orgs/${SLUG}/threads`, () => {
        composeCount += 1;
        return HttpResponse.json(
          { thread_id: 'THR-NEW', started_at: 'now', pending_replies: 1 },
          { status: 201 },
        );
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads` });
    await openDialog(user);
    await user.type(screen.getByLabelText(/^Subject$/i), 'First');
    await user.type(screen.getByLabelText(/^Recipients/i), 'agent_a');
    await user.upload(
      screen.getByLabelText(/Attach files/i),
      new File(['abc'], 'note.txt', { type: 'text/plain' }),
    );
    await user.click(screen.getByRole('button', { name: /^Send$/i }));
    // Close while the upload is held, then start a fresh dialog.
    await user.click(screen.getByRole('button', { name: /^Cancel$/i }));
    await openDialog(user);
    await user.type(screen.getByLabelText(/^Subject$/i), 'Second');
    await user.type(screen.getByLabelText(/^Recipients/i), 'agent_a');
    releaseUpload(undefined);

    await waitFor(() => expect(composeCount).toBe(1));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByLabelText(/^Subject$/i)).toHaveValue('Second');
  });
});

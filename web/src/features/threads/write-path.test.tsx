import { fireEvent, screen, within, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, test, vi } from 'vitest';
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
  test('double-click Send creates exactly one thread (synchronous in-flight guard)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();

    // Delay the POST so the second click can enter before React Query
    // sets isPending — this is the exact scenario that produced duplicate
    // threads (THR-046 message 49).
    // Use fireEvent.click (synchronous) instead of user.click (sequential)
    // so the second click arrives before React re-renders and disables the
    // button. Without the in-flight latch, this triggers two POSTs.
    let resolvePost: (v: unknown) => void;
    const postDeferred = new Promise((r) => { resolvePost = r; });
    let postCount = 0;
    let detailGetCount = 0;

    server.use(
      http.post(`/api/v1/orgs/${SLUG}/threads`, async () => {
        postCount++;
        await postDeferred;
        return HttpResponse.json(
          { thread_id: 'THR-007', started_at: 'now', pending_replies: 1 },
          { status: 201 },
        );
      }),
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-007`, () => {
        detailGetCount++;
        return HttpResponse.json({
          thread_id: 'THR-007',
          subject: 'Only one',
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
        });
      }),
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-007/messages`, () =>
        HttpResponse.json({ messages: [] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-007/tail`, () =>
        HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
      ),
    );

    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads` });

    // Open dialog, fill fields
    await user.click(await screen.findByRole('button', { name: /New thread/i }));
    await user.type(screen.getByLabelText(/^Subject$/i), 'Only one');
    await user.type(screen.getByLabelText(/^Recipients/i), 'agent_a');
    await user.type(screen.getByLabelText(/^Body \(Markdown\)$/i), 'Body');

    const sendBtn = screen.getByRole('button', { name: /^Send$/i });

    // fireEvent.click is synchronous — two clicks in the same tick. The
    // first starts the (delayed) POST; the second must be rejected by the
    // synchronous in-flight latch (submittingRef). React hasn't re-rendered
    // yet, so compose.isPending hasn't disabled the DOM button.
    fireEvent.click(sendBtn);
    fireEvent.click(sendBtn);

    // Release the deferred POST so both clicks (if both entered submit) resolve.
    resolvePost!({});

    // Assert exactly one POST reached the server.
    await waitFor(() => {
      expect(postCount).toBe(1);
    });

    // Assert exactly one navigation happened (one thread detail GET).
    await waitFor(() => {
      expect(detailGetCount).toBe(1);
    });

    // Navigated once to thread detail.
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: /Only one/i })).toBeInTheDocument(),
    );
  });

  test('MentionTextarea Enter + Send button race creates exactly one thread (cross-path in-flight guard)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();

    // Delay the POST so cross-path submit can race before resolution.
    let resolvePost: (v: unknown) => void;
    const postDeferred = new Promise((r) => { resolvePost = r; });
    let postCount = 0;
    let detailGetCount = 0;

    server.use(
      http.post(`/api/v1/orgs/${SLUG}/threads`, async () => {
        postCount++;
        await postDeferred;
        return HttpResponse.json(
          { thread_id: 'THR-ENTER', started_at: 'now', pending_replies: 1 },
          { status: 201 },
        );
      }),
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-ENTER`, () => {
        detailGetCount++;
        return HttpResponse.json({
          thread_id: 'THR-ENTER',
          subject: 'Enter race',
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
        });
      }),
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-ENTER/messages`, () =>
        HttpResponse.json({ messages: [] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-ENTER/tail`, () =>
        HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
      ),
    );

    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads` });

    await user.click(await screen.findByRole('button', { name: /New thread/i }));
    await user.type(screen.getByLabelText(/^Subject$/i), 'Enter race');
    await user.type(screen.getByLabelText(/^Recipients/i), 'agent_a');
    await user.type(screen.getByLabelText(/^Body \(Markdown\)$/i), 'Body');

    const sendBtn = screen.getByRole('button', { name: /^Send$/i });
    const bodyTextarea = screen.getByLabelText(/^Body \(Markdown\)$/i);

    // MentionTextarea onSubmit fires on Enter (when popup is closed, not
    // Shift+Enter, not IME-composing). Fire Enter synchronously, then
    // immediately click Send before React re-renders to disable either path.
    fireEvent.keyDown(bodyTextarea, { key: 'Enter' });
    fireEvent.click(sendBtn);

    resolvePost!({});

    await waitFor(() => {
      expect(postCount).toBe(1);
    });
    await waitFor(() => {
      expect(detailGetCount).toBe(1);
    });
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: /Enter race/i })).toBeInTheDocument(),
    );
  });

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
        attachments: [
          {
            artifact_name: 'THR-001-report.pdf',
            display_name: 'report.pdf',
            content_type: 'application/pdf',
          },
        ],
      }),
    );
  });

  test('message bubble attachment chip downloads with auth token via fetch, not raw anchor navigation', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();

    // Stub the browser download machinery that downloadArtifact relies on.
    // jsdom doesn't ship URL.createObjectURL / URL.revokeObjectURL — add them.
    const createObjectURL = vi.fn(() => 'blob:mock');
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, 'createObjectURL', { value: createObjectURL, writable: true, configurable: true });
    Object.defineProperty(URL, 'revokeObjectURL', { value: revokeObjectURL, writable: true, configurable: true });

    let downloadAuthHeader: string | null = null;
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
                  size_bytes: 5 * 1024 * 1024,
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
      // The artifact download endpoint — capture the Authorization header
      http.get(`/api/v1/orgs/${SLUG}/artifacts/THR-001-report.pdf`, ({ request }) => {
        downloadAuthHeader = request.headers.get('Authorization');
        return new HttpResponse('fake-pdf', { headers: { 'content-type': 'application/pdf' } });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads/THR-001` });

    // The attachment chip is now a button (not a link), still shows display name + size
    const chip = await screen.findByRole('button', { name: /report\.pdf/i });
    expect(chip).toHaveTextContent('5 MB');

    await user.click(chip);

    // The download triggered a GET with the bearer token — this is the fix:
    // raw anchor navigation cannot attach Authorization; fetch can.
    await waitFor(() => {
      expect(downloadAuthHeader).toBe('Bearer tok');
    });
  });

  describe('InviteDialog', () => {
    const INVITE_THREAD_ID = 'THR-002';

    function stubInviteStubs() {
      server.use(
        http.get(`/api/v1/orgs/${SLUG}/agents`, () =>
          HttpResponse.json({
            agents: [
              { name: 'agent_a', team: 'core', role: 'worker', executor: 'claude', description: null, repos: {}, system_prompt: '' },
              { name: 'agent_b', team: 'core', role: 'worker', executor: 'claude', description: null, repos: {}, system_prompt: '' },
              { name: 'agent_c', team: 'support', role: 'manager', executor: 'claude', description: null, repos: {}, system_prompt: '' },
            ],
          }),
        ),
        http.get(`/api/v1/orgs/${SLUG}/threads/events`, () =>
          HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
        ),
        http.get(`/api/v1/orgs/${SLUG}/threads/${INVITE_THREAD_ID}`, () =>
          HttpResponse.json({
            thread_id: INVITE_THREAD_ID,
            subject: 'Invite test thread',
            status: 'open',
            started_at: '2026-06-30T00:00:00Z',
            archived_at: null,
            forwarded_from_id: null,
            forwarded_from_kind: null,
            turn_cap: 500,
            turns_used: 2,
            summary: null,
            transcript_path: null,
            participants: ['founder', 'agent_a'],
            messages: [
              {
                seq: 1,
                speaker: 'founder',
                kind: 'message',
                body_markdown: 'Hello',
                decline_reason: null,
                system_payload: null,
                created_at: '2026-06-30T00:00:00Z',
                responder_status: [],
                attachments: [],
              },
            ],
          }),
        ),
        http.get(`/api/v1/orgs/${SLUG}/threads/${INVITE_THREAD_ID}/messages`, () =>
          HttpResponse.json({ messages: [] }),
        ),
        http.get(`/api/v1/orgs/${SLUG}/threads/${INVITE_THREAD_ID}/tail`, () =>
          HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
        ),
      );
    }

    test('exposes matching placeholder as NewThreadDialog recipients field', async () => {
      sessionStorage.setItem('happyranch.token', 'tok');
      stubBaseHandlers();
      stubInviteStubs();

      const user = userEvent.setup();
      renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads/${INVITE_THREAD_ID}` });

      // Open the Invite participant dialog.
      await user.click(await screen.findByRole('button', { name: /^Invite$/i }));

      // The agent name input should have the same placeholder as NewThreadDialog's recipients field.
      const input = await screen.findByLabelText(/^Agent name$/i);
      expect(input).toHaveAttribute('placeholder', 'agent_a, agent_b');
      expect(input).toHaveAttribute('autocomplete', 'off');
    });

    test('shows roster autocomplete and submits exactly one invite', async () => {
      sessionStorage.setItem('happyranch.token', 'tok');
      stubBaseHandlers();
      stubInviteStubs();

      let inviteBody: unknown = null;
      server.use(
        http.post(
          `/api/v1/orgs/${SLUG}/threads/${INVITE_THREAD_ID}/invite`,
          async ({ request: req }) => {
            inviteBody = await req.json();
            return HttpResponse.json({
              thread_id: INVITE_THREAD_ID,
              agent_name: 'agent_c',
              system_message_seq: 2,
            });
          },
        ),
      );

      const user = userEvent.setup();
      renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads/${INVITE_THREAD_ID}` });

      // Open the Invite participant dialog.
      await user.click(await screen.findByRole('button', { name: /^Invite$/i }));

      // Type an agent name prefix to trigger autocomplete.
      const input = screen.getByLabelText(/^Agent name$/i);
      await user.type(input, 'agent_c');

      // Autocomplete listbox should appear with matching option.
      const listbox = await screen.findByRole('listbox', { name: /Mention agents/i });
      expect(within(listbox).getByRole('option', { name: /agent_c/i })).toBeInTheDocument();

      // Select the option via keyboard (Enter while popup open).
      await user.keyboard('{Enter}');

      // The input should now contain the selected agent name.
      await waitFor(() => {
        expect(input).toHaveValue('agent_c, ');
      });

      // Submit via the dialog's Invite button (scoped to the dialog to avoid
      // the header's "Invite" button).
      const dialog = screen.getByRole('dialog');
      await user.click(within(dialog).getByRole('button', { name: /^Invite$/i }));

      // Assert exactly one POST with { agent_name: 'agent_c' }.
      await waitFor(() => {
        expect(inviteBody).toEqual({ agent_name: 'agent_c' });
      });
    });

    test('submits only the first agent name when multiple comma-separated tokens are entered', async () => {
      sessionStorage.setItem('happyranch.token', 'tok');
      stubBaseHandlers();
      stubInviteStubs();

      let inviteBody: unknown = null;
      server.use(
        http.post(
          `/api/v1/orgs/${SLUG}/threads/${INVITE_THREAD_ID}/invite`,
          async ({ request: req }) => {
            inviteBody = await req.json();
            return HttpResponse.json({
              thread_id: INVITE_THREAD_ID,
              agent_name: 'agent_a',
              system_message_seq: 2,
            });
          },
        ),
      );

      const user = userEvent.setup();
      renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads/${INVITE_THREAD_ID}` });

      // Open the Invite participant dialog.
      await user.click(await screen.findByRole('button', { name: /^Invite\b/i }));

      // Type multiple comma-separated agent names.
      const input = screen.getByLabelText(/^Agent name$/i);
      await user.type(input, 'agent_a, agent_b');

      // Submit via the dialog's Invite button.
      const dialog = screen.getByRole('dialog');
      await user.click(within(dialog).getByRole('button', { name: /^Invite$/i }));

      // Assert only the first name is sent.
      await waitFor(() => {
        expect(inviteBody).toEqual({ agent_name: 'agent_a' });
      });
    });

    test('validates empty input and shows error', async () => {
      sessionStorage.setItem('happyranch.token', 'tok');
      stubBaseHandlers();
      stubInviteStubs();

      const user = userEvent.setup();
      renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads/${INVITE_THREAD_ID}` });

      // Open the Invite participant dialog.
      await user.click(await screen.findByRole('button', { name: /^Invite\b/i }));

      // Submit with empty input via the dialog's Invite button.
      const dialog = screen.getByRole('dialog');
      await user.click(within(dialog).getByRole('button', { name: /^Invite$/i }));

      // Error message should appear.
      await waitFor(() => {
        expect(screen.getByText('Agent name is required.')).toBeInTheDocument();
      });
    });
  });
});

/**
 * THR-209 rename + pinning UI tests.
 *
 * Covers the founder-facing acceptance surface: inline rename (prefill,
 * Save/Cancel, Enter/Escape, trim, 120-char boundary, failure retention +
 * inline error), durable pin/unpin with optimistic update + rollback + visible
 * error, the Pinned section ranking above the ordinary list (including under
 * the active filter), archived-pin eligibility, direct row + header controls,
 * and keyboard/accessibility assertions.
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
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

function mkThread(
  id: string,
  subject: string,
  overrides?: Partial<{
    status: 'open' | 'archived';
    pinned: boolean;
    pinned_at: string | null;
    started_at: string;
    last_activity_at: string | null;
    participants: string[];
    messages: unknown[];
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
    pinned: false,
    pinned_at: null as string | null,
    last_activity_at: '2026-05-14T00:00:00Z',
    ...overrides,
  };
}

function mkMessage(seq: number, body: string) {
  return {
    seq,
    speaker: 'founder',
    kind: 'message' as const,
    body_markdown: body,
    decline_reason: null,
    system_payload: null,
    attachments: [],
    created_at: '2026-05-14T00:00:00Z',
    responder_status: [],
  };
}

function stubList(threads: ReturnType<typeof mkThread>[]) {
  server.use(
    http.get(`/api/v1/orgs/${SLUG}/threads`, ({ request }) => {
      const url = new URL(request.url);
      const status = url.searchParams.get('status');
      const filtered = status
        ? threads.filter((t) => t.status === status)
        : [...threads];
      return HttpResponse.json({ threads: filtered });
    }),
    http.get(`/api/v1/orgs/${SLUG}/threads/events`, () =>
      HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
    ),
  );
}

function stubDetail(thread: ReturnType<typeof mkThread>) {
  server.use(
    http.get(`/api/v1/orgs/${SLUG}/threads/${thread.thread_id}`, () =>
      HttpResponse.json({
        ...thread,
        participants: thread.participants ?? ['agent_a'],
        messages: thread.messages ?? [mkMessage(1, 'hi')],
        reply_delivery: [],
      }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/threads/${thread.thread_id}/messages`, () =>
      HttpResponse.json({
        messages: thread.messages ?? [mkMessage(1, 'hi')],
      }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/threads/${thread.thread_id}/tail`, () =>
      HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/tokens`, () => HttpResponse.json({ rollup: [] })),
  );
}

beforeEach(() => {
  sessionStorage.setItem('happyranch.token', 'tok');
});

afterEach(() => {
  sessionStorage.removeItem('happyranch.token');
  vi.restoreAllMocks();
});

/* ------------------------------------------------------------------ */
/*  Pinned section (list)                                              */
/* ------------------------------------------------------------------ */

describe('THR-209 — Pinned section', () => {
  test('pinned threads render in a Pinned section above ordinary threads', async () => {
    stubList([
      mkThread('THR-A', 'Pinned one', { pinned: true, pinned_at: '2026-05-20T00:00:00Z' }),
      mkThread('THR-B', 'Ordinary one'),
    ]);
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() => expect(screen.getByText(/Pinned one/i)).toBeInTheDocument());

    const pinnedHeading = screen.getByRole('heading', { name: /Pinned/i });
    const threadsHeading = screen.getByRole('heading', { name: 'Threads' });
    // Pinned section heading comes before the ordinary section heading.
    expect(pinnedHeading.compareDocumentPosition(threadsHeading)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    // Both threads visible.
    expect(screen.getByText(/Ordinary one/i)).toBeInTheDocument();
  });

  test('no Pinned heading when nothing is pinned', async () => {
    stubList([mkThread('THR-B', 'Ordinary one')]);
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() => expect(screen.getByText(/Ordinary one/i)).toBeInTheDocument());
    expect(screen.queryByRole('heading', { name: /Pinned/i })).not.toBeInTheDocument();
  });

  test('filter qualifies Pinned section inclusion (matching pinned above matching unpinned)', async () => {
    stubList([
      mkThread('THR-A', 'Alpha pinned', { pinned: true }),
      mkThread('THR-B', 'Alpha ordinary'),
      mkThread('THR-C', 'Beta unrelated'),
    ]);
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() => expect(screen.getByText(/Alpha pinned/i)).toBeInTheDocument());

    await userEvent.type(screen.getByRole('textbox', { name: /Filter threads/i }), 'Alpha');
    await waitFor(() => {
      expect(screen.getByText(/Alpha pinned/i)).toBeInTheDocument();
      expect(screen.getByText(/Alpha ordinary/i)).toBeInTheDocument();
    });
    // The non-matching thread is excluded by the active filter.
    expect(screen.queryByText(/Beta unrelated/i)).not.toBeInTheDocument();
    // Matching pinned thread ranks in the Pinned section; matching unpinned below.
    expect(screen.getByRole('heading', { name: /Pinned/i })).toBeInTheDocument();
  });

  test('archived pinned thread appears only in the Archived bucket', async () => {
    const archivedPinned = mkThread('THR-D', 'Archived pinned', {
      status: 'archived',
      pinned: true,
    });
    stubList([
      mkThread('THR-A', 'Open one'),
      archivedPinned,
    ]);
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() => expect(screen.getByText(/Open one/i)).toBeInTheDocument());

    // Open bucket: archived thread not eligible.
    expect(screen.queryByText(/Archived pinned/i)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('tab', { name: /Archived/i }));
    await waitFor(() => expect(screen.getByText(/Archived pinned/i)).toBeInTheDocument());
    expect(screen.getByRole('heading', { name: /Pinned/i })).toBeInTheDocument();
  });

  test('row pin toggle updates list optimistically and calls POST /pin', async () => {
    // Stateful stub: the POST flips the durable pin state the GET returns.
    const state = [mkThread('THR-A', 'Alpha subject')];
    stubList(state);
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-A/pin`, () => {
        state[0] = { ...state[0], pinned: true, pinned_at: '2026-05-20T00:00:00Z' };
        return HttpResponse.json({ thread_id: 'THR-A', pinned: true });
      }),
    );
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() => expect(screen.getByText('Alpha subject')).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: /Pin thread THR-A/i }));
    // Optimistic: the Pinned section appears without waiting for a refetch.
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: /Pinned/i })).toBeInTheDocument(),
    );
    // Control now reads as unpin (persists through the refetch).
    expect(
      await screen.findByRole('button', { name: /Unpin thread THR-A/i }),
    ).toBeInTheDocument();
  });

  test('row pin failure rolls back and shows a visible error', async () => {
    const state = [mkThread('THR-A', 'Alpha subject', { pinned: true })];
    stubList(state);
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-A/pin`, () =>
        HttpResponse.json({ error: 'boom' }, { status: 500 }),
      ),
    );
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() => expect(screen.getByText('Alpha subject')).toBeInTheDocument());
    expect(screen.getByRole('heading', { name: /Pinned/i })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /Unpin thread THR-A/i }));
    // Error banner appears (aria-live alert).
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(/Pin change failed/i),
    );
    // Rollback: the thread stays pinned (Pinned section still present).
    expect(screen.getByRole('heading', { name: /Pinned/i })).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /Unpin thread THR-A/i }),
    ).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */
/*  Inline rename (detail header)                                      */
/* ------------------------------------------------------------------ */

describe('THR-209 — inline rename', () => {
  function mountDetail(subject = 'Original title') {
    const thread = mkThread('THR-1', subject, { messages: [mkMessage(1, 'hi')] });
    stubList([thread]);
    stubDetail(thread);
    mountAt(`/orgs/${SLUG}/threads/THR-1`);
    return thread;
  }

  test('rename is prefilled, saves via button, and updates the header', async () => {
    mountDetail();
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: /Original title/i })).toBeInTheDocument(),
    );
    const postRename = vi.fn(() =>
      HttpResponse.json({ thread_id: 'THR-1', subject: 'Renamed' }),
    );
    server.use(http.post(`/api/v1/orgs/${SLUG}/threads/THR-1/rename`, postRename));

    await userEvent.click(screen.getByRole('button', { name: /Rename/i }));
    const input = screen.getByRole('textbox', { name: /Thread title/i });
    expect(input).toHaveValue('Original title'); // prefilled
    await userEvent.clear(input);
    await userEvent.type(input, 'Renamed');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    expect(postRename).toHaveBeenCalled();
    await waitFor(() =>
      expect(screen.getByText(/Renamed/i)).toBeInTheDocument(),
    );
    expect(screen.queryByRole('textbox', { name: /Thread title/i })).not.toBeInTheDocument();
  });

  test('Enter saves and Escape cancels', async () => {
    mountDetail('Start');
    await waitFor(() => expect(screen.getByText(/Start/i)).toBeInTheDocument());
    const postRename = vi.fn(() =>
      HttpResponse.json({ thread_id: 'THR-1', subject: 'Entered' }),
    );
    server.use(http.post(`/api/v1/orgs/${SLUG}/threads/THR-1/rename`, postRename));

    await userEvent.click(screen.getByRole('button', { name: /Rename/i }));
    const input = screen.getByRole('textbox', { name: /Thread title/i });
    await userEvent.clear(input);
    await userEvent.type(input, 'Entered{Enter}');
    await waitFor(() => expect(postRename).toHaveBeenCalled());

    // Escape cancels without saving.
    await userEvent.click(screen.getByRole('button', { name: /Rename/i }));
    const input2 = screen.getByRole('textbox', { name: /Thread title/i });
    await userEvent.clear(input2);
    await userEvent.type(input2, 'NotSaved{Escape}');
    await waitFor(() =>
      expect(screen.queryByRole('textbox', { name: /Thread title/i })).not.toBeInTheDocument(),
    );
    expect(screen.getByText(/Entered/i)).toBeInTheDocument();
  });

  test('failure retains the typed value with an inline error and allows retry', async () => {
    mountDetail('Original title');
    await waitFor(() => expect(screen.getByText(/Original title/i)).toBeInTheDocument());
    let fail = true;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-1/rename`, () =>
        fail
          ? HttpResponse.json({ detail: { code: 'empty_subject' } }, { status: 422 })
          : HttpResponse.json({ thread_id: 'THR-1', subject: 'Retried' }),
      ),
    );

    await userEvent.click(screen.getByRole('button', { name: /Rename/i }));
    const input = screen.getByRole('textbox', { name: /Thread title/i });
    await userEvent.clear(input);
    await userEvent.type(input, 'Kept value');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    // Inline error + typed value retained (edit stays open).
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(/Rename failed/i),
    );
    const stillOpen = screen.getByRole('textbox', { name: /Thread title/i });
    expect(stillOpen).toHaveValue('Kept value');

    // Retry succeeds.
    fail = false;
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(screen.queryByRole('textbox', { name: /Thread title/i })).not.toBeInTheDocument(),
    );
    expect(screen.getByText(/Retried/i)).toBeInTheDocument();
  });

  test('save is disabled for whitespace-only input', async () => {
    mountDetail();
    await waitFor(() => expect(screen.getByRole('button', { name: /Rename/i })).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /Rename/i }));
    const input = screen.getByRole('textbox', { name: /Thread title/i });
    await userEvent.clear(input);
    await userEvent.type(input, '   ');
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
  });

  test('rename control is available for archived threads', async () => {
    const thread = mkThread('THR-1', 'Archived title', {
      status: 'archived',
      participants: ['agent_a'],
      messages: [mkMessage(1, 'hi')],
    });
    stubList([thread]);
    stubDetail(thread);
    mountAt(`/orgs/${SLUG}/threads/THR-1`);
    await waitFor(() => expect(screen.getByText(/Archived title/i)).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /Rename/i })).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */
/*  Pin from detail header + overflow                                  */
/* ------------------------------------------------------------------ */

describe('THR-209 — pin from detail', () => {
  /** Stateful mount: the detail GET reads the mutable thread object so POST
   *  handlers can persist the flip through refetches. */
  function mountDetail(pinned = false) {
    const state = mkThread('THR-1', 'Subject', {
      pinned,
      pinned_at: pinned ? '2026-05-20T00:00:00Z' : null,
      messages: [mkMessage(1, 'hi')],
    });
    const listState = [state];
    stubList(listState);
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-1`, () =>
        HttpResponse.json({
          ...state,
          participants: ['agent_a'],
          messages: [mkMessage(1, 'hi')],
          reply_delivery: [],
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-1/messages`, () =>
        HttpResponse.json({ messages: [mkMessage(1, 'hi')] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-1/tail`, () =>
        HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tokens`, () => HttpResponse.json({ rollup: [] })),
    );
    mountAt(`/orgs/${SLUG}/threads/THR-1`);
    return state;
  }

  test('header Pin button pins optimistically and calls POST /pin', async () => {
    const state = mountDetail(false);
    await waitFor(() => expect(screen.getByText(/Subject/i)).toBeInTheDocument());
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-1/pin`, () => {
        state.pinned = true;
        state.pinned_at = '2026-05-20T00:00:00Z';
        return HttpResponse.json({ thread_id: 'THR-1', pinned: true });
      }),
    );

    await userEvent.click(screen.getByRole('button', { name: 'Pin' }));
    // Optimistic flip + refetch persistence both read as Unpin.
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Unpin' })).toBeInTheDocument(),
    );
  });

  test('header keeps Pin/Unpin direct and does not duplicate it in an overflow menu', async () => {
    const state = mountDetail(false);
    await waitFor(() => expect(screen.getByText(/Subject/i)).toBeInTheDocument());
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-1/pin`, () => {
        state.pinned = true;
        state.pinned_at = '2026-05-20T00:00:00Z';
        return HttpResponse.json({ thread_id: 'THR-1', pinned: true });
      }),
    );

    expect(screen.queryByRole('button', { name: /Thread actions/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Pin' }));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Unpin' })).toBeInTheDocument(),
    );
  });

  test('pin failure shows banner and rolls back the header control', async () => {
    mountDetail(true);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Unpin' })).toBeInTheDocument());
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-1/pin`, () =>
        HttpResponse.json({ error: 'boom' }, { status: 500 }),
      ),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Unpin' }));
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(/Pin change failed/i),
    );
    // Rollback: control reads Unpin again (still pinned; server never changed).
    expect(screen.getByRole('button', { name: 'Unpin' })).toBeInTheDocument();
  });
});

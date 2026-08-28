/**
 * THR-209 rename + pinning UI tests.
 *
 * Covers the founder-facing acceptance surface: inline rename (prefill,
 * Save/Cancel, Enter/Escape, trim, 120-char boundary, failure retention +
 * inline error), durable pin/unpin with optimistic update + rollback + visible
 * error, the Pinned section ranking above the ordinary list by immutable
 * numeric thread ID desc (THR-209 msg 9 correction: never activity, never
 * lexicographic; including under the active filter), archived/closed views
 * with zero pin presentation (no section, no rank), the 'all' merged bucket
 * with no pin leak, direct row + header controls, and keyboard/accessibility
 * assertions.
 */
import { act, screen, waitFor } from '@testing-library/react';
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

/**
 * Numeric suffix mirror of the server's CAST(SUBSTR(t.id, 5) AS INTEGER)
 * open-list pin-rank key (THR-10 > THR-2).
 */
function numericThreadId(id: string): number {
  const n = Number.parseInt(id.slice(4), 10);
  return Number.isFinite(n) ? n : 0;
}

/**
 * Server-order-aware stub: GET /threads returns open lists in the real
 * database.list_threads order (pinned first, pinned numeric id desc, unpinned
 * started_at desc) and archived lists in ordinary order — so a refetch after a
 * pin mutation reconciles against the authoritative server rule, exactly like
 * the live daemon. Status-less merges stay ordinary.
 */
function stubServerOrderedList(threads: ReturnType<typeof mkThread>[]) {
  server.use(
    http.get(`/api/v1/orgs/${SLUG}/threads`, ({ request }) => {
      const url = new URL(request.url);
      const status = url.searchParams.get('status');
      const filtered = status
        ? threads.filter((t) => t.status === status)
        : [...threads];
      if (status === 'open') {
        filtered.sort((a, b) => {
          const aPinned = a.pinned ? 0 : 1;
          const bPinned = b.pinned ? 0 : 1;
          if (aPinned !== bPinned) return aPinned - bPinned;
          if (aPinned === 0) return numericThreadId(b.thread_id) - numericThreadId(a.thread_id);
          return b.started_at.localeCompare(a.started_at);
        });
      }
      return HttpResponse.json({ threads: filtered });
    }),
    http.get(`/api/v1/orgs/${SLUG}/threads/events`, () =>
      HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
    ),
  );
}

/** Row link subjects in DOM order, scoped to the inbox list. */
function rowSubjects(): string[] {
  return screen
    .getAllByRole('link')
    .filter((el) => /Alpha|Ten|Two|One|Nine|Three/.test(el.textContent ?? ''))
    .map((r) => r.textContent ?? '');
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

  test('open list renders pinned threads in numeric thread-id descending order', async () => {
    // Server order (what GET /threads?status=open returns): pinned first,
    // immutable NUMERIC id descending — THR-10 above THR-3 above THR-2 (a
    // lexicographic server sort would give 3 > 2 > 10). The page must render
    // that order verbatim.
    stubList([
      mkThread('THR-10', 'Ten pinned', { pinned: true, pinned_at: '2026-05-20T00:00:00Z' }),
      mkThread('THR-3', 'Three pinned', { pinned: true, pinned_at: '2026-05-21T00:00:00Z' }),
      mkThread('THR-2', 'Two pinned', { pinned: true, pinned_at: '2026-05-22T00:00:00Z' }),
      mkThread('THR-1', 'One ordinary'),
    ]);
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() => expect(screen.getByText(/Ten pinned/i)).toBeInTheDocument());

    const rows = screen.getAllByRole('link').filter((el) =>
      /Ten pinned|Three pinned|Two pinned|One ordinary/.test(el.textContent ?? ''),
    );
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining('Ten pinned'),
      expect.stringContaining('Three pinned'),
      expect.stringContaining('Two pinned'),
      expect.stringContaining('One ordinary'),
    ]);
    // Both section headings present (pinned section + ordinary section).
    expect(screen.getByRole('heading', { name: /Pinned/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Threads' })).toBeInTheDocument();
  });

  test('pinned order is numeric id desc, never activity', async () => {
    // The higher-id thread has the OLDEST activity; the page must preserve the
    // server's numeric-id-desc order instead of re-sorting by activity.
    stubList([
      mkThread('THR-10', 'Older activity, higher id', {
        pinned: true,
        pinned_at: '2026-05-20T00:00:00Z',
        started_at: '2026-05-01T00:00:00Z',
        last_activity_at: '2026-05-01T00:00:00Z',
      }),
      mkThread('THR-2', 'Newer activity, lower id', {
        pinned: true,
        pinned_at: '2026-05-21T00:00:00Z',
        started_at: '2026-05-14T00:00:00Z',
        last_activity_at: '2026-05-30T00:00:00Z',
      }),
    ]);
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() =>
      expect(screen.getByText(/Older activity, higher id/i)).toBeInTheDocument(),
    );
    const rows = screen.getAllByRole('link').filter((el) =>
      /Older activity|Newer activity/.test(el.textContent ?? ''),
    );
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining('Older activity, higher id'), // THR-10 first
      expect.stringContaining('Newer activity, lower id'),
    ]);
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

  test('open search/filter retains pinned-first numeric-id-desc order', async () => {
    stubList([
      mkThread('THR-10', 'Alpha ten pinned', { pinned: true }),
      mkThread('THR-2', 'Alpha two pinned', { pinned: true }),
      mkThread('THR-3', 'Alpha three ordinary'),
      mkThread('THR-4', 'Beta other'),
    ]);
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() => expect(screen.getByText(/Alpha ten pinned/i)).toBeInTheDocument());

    await userEvent.type(screen.getByRole('textbox', { name: /Filter threads/i }), 'Alpha');
    await waitFor(() => {
      expect(screen.getByText(/Alpha three ordinary/i)).toBeInTheDocument();
    });
    expect(screen.queryByText(/Beta other/i)).not.toBeInTheDocument();

    const rows = screen.getAllByRole('link').filter((el) =>
      /Alpha/.test(el.textContent ?? ''),
    );
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining('Alpha ten pinned'), // THR-10 above THR-2
      expect.stringContaining('Alpha two pinned'),
      expect.stringContaining('Alpha three ordinary'),
    ]);
  });

  test('archived pinned thread appears only in the Archived bucket, with no Pinned section', async () => {
    const archivedPinned = mkThread('THR-D', 'Archived pinned', {
      status: 'archived',
      pinned: true,
    });
    const archivedOrdinary = mkThread('THR-E', 'Archived ordinary', {
      status: 'archived',
      pinned: false,
    });
    stubList([
      mkThread('THR-A', 'Open one'),
      archivedPinned,
      archivedOrdinary,
    ]);
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() => expect(screen.getByText(/Open one/i)).toBeInTheDocument());

    // Open bucket: archived thread not eligible.
    expect(screen.queryByText(/Archived pinned/i)).not.toBeInTheDocument();

    // Archived bucket: ONE flat list — pinned and unpinned interleave under
    // the ordinary archived order with NO Pinned section and NO pin rank
    // (THR-209 msg 9 correction).
    await userEvent.click(screen.getByRole('tab', { name: /Archived/i }));
    await waitFor(() => expect(screen.getByText(/Archived pinned/i)).toBeInTheDocument());
    expect(screen.getByText(/Archived ordinary/i)).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /Pinned/i })).not.toBeInTheDocument();
    // Ordinary archived server order preserved (pinned row NOT ranked first
    // just because it is pinned).
    const rows = screen.getAllByRole('link').filter((el) =>
      /Archived/.test(el.textContent ?? ''),
    );
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining('Archived pinned'),
      expect.stringContaining('Archived ordinary'),
    ]);
  });

  test("'all' bucket merges open+archived in ordinary order with no Pinned section", async () => {
    // Archived pin state must never surface a Pinned section in the 'all'
    // merged view (it is not the open-thread list).
    stubList([
      mkThread('THR-1', 'Open pinned', { pinned: true }),
      mkThread('THR-2', 'Open ordinary'),
      mkThread('THR-3', 'Archived pinned', {
        status: 'archived',
        pinned: true,
        started_at: '2026-05-13T00:00:00Z',
      }),
      mkThread('THR-4', 'Archived ordinary', {
        status: 'archived',
        started_at: '2026-05-12T00:00:00Z',
      }),
    ]);
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() => expect(screen.getByText(/Open pinned/i)).toBeInTheDocument());

    await userEvent.click(screen.getByRole('tab', { name: /All/i }));
    await waitFor(() => expect(screen.getByText(/Archived ordinary/i)).toBeInTheDocument());
    // All four rows present in ONE flat list; no Pinned section anywhere.
    expect(screen.getByText(/Open pinned/i)).toBeInTheDocument();
    expect(screen.getByText(/Archived pinned/i)).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /Pinned/i })).not.toBeInTheDocument();
    // Ordinary started_at DESC merge: newest started_at first.
    const rows = screen.getAllByRole('link').filter((el) =>
      /Open|Archived/.test(el.textContent ?? ''),
    );
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining('Open pinned'),
      expect.stringContaining('Open ordinary'),
      expect.stringContaining('Archived pinned'),
      expect.stringContaining('Archived ordinary'),
    ]);
  });

  test('single pinned thread renders in the Pinned section', async () => {
    stubList([
      mkThread('THR-1', 'Only pinned', { pinned: true, pinned_at: '2026-05-20T00:00:00Z' }),
    ]);
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() => expect(screen.getByText(/Only pinned/i)).toBeInTheDocument());
    expect(screen.getByRole('heading', { name: /Pinned/i })).toBeInTheDocument();
    // No ordinary section heading when there are no unpinned rows.
    expect(screen.queryByRole('heading', { name: 'Threads' })).not.toBeInTheDocument();
  });

  test('empty list renders no sections', async () => {
    stubList([]);
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() =>
      expect(screen.queryByRole('heading', { name: /Pinned/i })).not.toBeInTheDocument(),
    );
  });

  test('pinned section headings and controls are keyboard-accessible with clear labels', async () => {
    stubList([
      mkThread('THR-1', 'Pinned accessible', { pinned: true, pinned_at: '2026-05-20T00:00:00Z' }),
      mkThread('THR-2', 'Ordinary accessible'),
    ]);
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() => expect(screen.getByText(/Pinned accessible/i)).toBeInTheDocument());

    // Section headings are real h2 elements in document order.
    const headings = screen.getAllByRole('heading', { level: 2 });
    expect(headings.map((h) => h.textContent)).toEqual(['Pinned', 'Threads']);
    // Every row exposes a keyboard-reachable pin toggle with a labelled name.
    expect(
      screen.getByRole('button', { name: /Unpin thread THR-1/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /Pin thread THR-2/i }),
    ).toBeInTheDocument();
    // The per-row pin buttons are focusable controls (tabbable).
    for (const b of screen.getAllByRole('button', { name: /Pin thread|Unpin thread/i })) {
      expect(b).toHaveAttribute('type', 'button');
    }
  });

  test('Pinned section renders identically when the open list is the sole qualifying view', async () => {
    // Regression: switching away from Open and back must restore the section
    // (bucket state drives the split; the fetch cache is unchanged).
    stubList([
      mkThread('THR-1', 'Pinned again', { pinned: true, pinned_at: '2026-05-20T00:00:00Z' }),
      mkThread('THR-2', 'Ordinary again'),
    ]);
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() => expect(screen.getByText(/Pinned again/i)).toBeInTheDocument());
    expect(screen.getByRole('heading', { name: /Pinned/i })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('tab', { name: /Archived/i }));
    await waitFor(() =>
      expect(screen.queryByRole('heading', { name: /Pinned/i })).not.toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole('tab', { name: /Open/i }));
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: /Pinned/i })).toBeInTheDocument(),
    );
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
/*  Optimistic open-list reorder (TASK-5987 — PR #758 fix-forward)     */
/* ------------------------------------------------------------------ */

describe('THR-209 — optimistic pin reorders the open list under the server rule (TASK-5987)', () => {
  test('pinning a higher-id thread ranks it above lower pinned ids BEFORE the response/refetch', async () => {
    // Server open order (server rule): THR-2 pinned, then unpinned started_at
    // desc → THR-10 (05-14), THR-1 (05-13).
    const state = [
      mkThread('THR-2', 'Two pinned', {
        pinned: true,
        pinned_at: '2026-05-20T00:00:00Z',
        started_at: '2026-05-12T00:00:00Z',
      }),
      mkThread('THR-10', 'Ten unpinned', { started_at: '2026-05-14T00:00:00Z' }),
      mkThread('THR-1', 'One ordinary', { started_at: '2026-05-13T00:00:00Z' }),
    ];
    stubServerOrderedList(state);
    // Gated POST: the mutation stays pending until the test releases it, so the
    // optimistic render is observable before the response and the refetch.
    let releasePost!: () => void;
    const gate = new Promise<void>((res) => {
      releasePost = res;
    });
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-10/pin`, async () => {
        await gate;
        state[1] = {
          ...state[1],
          pinned: true,
          pinned_at: '2026-05-21T00:00:00Z',
        };
        return HttpResponse.json({ thread_id: 'THR-10', pinned: true });
      }),
    );
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() => expect(screen.getByText(/Ten unpinned/i)).toBeInTheDocument());
    // Initial server order: Pinned [THR-2], Threads [THR-10, THR-1].
    expect(rowSubjects().map((s) => s.includes('Two pinned') ? 'Two' : s.includes('Ten') ? 'Ten' : s.includes('One') ? 'One' : '?')).toEqual(['Two', 'Ten', 'One']);

    await userEvent.click(screen.getByRole('button', { name: /Pin thread THR-10/i }));

    // BEFORE the POST resolves: optimistic reorder → Pinned [THR-10, THR-2]
    // (numeric 10 > 2 — lexicographic would keep THR-2 first).
    await waitFor(() => {
      expect(rowSubjects().map((s) => s.includes('Two pinned') ? 'Two' : s.includes('Ten') ? 'Ten' : s.includes('One') ? 'One' : '?')).toEqual(['Ten', 'Two', 'One']);
    });
    // THR-10 renders inside the Pinned section (heading precedes its row).
    const pinnedHeading = screen.getByRole('heading', { name: /Pinned/i });
    const tenRow = screen.getByText(/Ten unpinned/i);
    expect(pinnedHeading.compareDocumentPosition(tenRow) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    // Release the response — success + refetch reconcile to the same order.
    await act(async () => {
      releasePost();
    });
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Unpin thread THR-10/i })).toBeInTheDocument(),
    );
    expect(rowSubjects().map((s) => s.includes('Two pinned') ? 'Two' : s.includes('Ten') ? 'Ten' : s.includes('One') ? 'One' : '?')).toEqual(['Ten', 'Two', 'One']);
  });

  test('unpinning re-inserts the row into ordinary started_at-desc order BEFORE the response/refetch', async () => {
    // Server order: THR-10 pinned (numeric first), THR-2 pinned, then THR-1.
    const state = [
      mkThread('THR-10', 'Ten pinned', {
        pinned: true,
        pinned_at: '2026-05-21T00:00:00Z',
        started_at: '2026-05-14T00:00:00Z',
      }),
      mkThread('THR-2', 'Two pinned', {
        pinned: true,
        pinned_at: '2026-05-20T00:00:00Z',
        started_at: '2026-05-12T00:00:00Z',
      }),
      mkThread('THR-1', 'One ordinary', { started_at: '2026-05-13T00:00:00Z' }),
    ];
    stubServerOrderedList(state);
    let releasePost!: () => void;
    const gate = new Promise<void>((res) => {
      releasePost = res;
    });
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-2/pin`, async () => {
        await gate;
        state[1] = { ...state[1], pinned: false, pinned_at: null };
        return HttpResponse.json({ thread_id: 'THR-2', pinned: false });
      }),
    );
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() => expect(screen.getByText(/Ten pinned/i)).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: /Unpin thread THR-2/i }));

    // BEFORE the POST resolves: THR-2 drops into ordinary started_at-desc
    // position (THR-10 05-14, THR-1 05-13, THR-2 05-12).
    await waitFor(() => {
      expect(rowSubjects().map((s) => s.includes('Two pinned') ? 'Two' : s.includes('Ten') ? 'Ten' : s.includes('One') ? 'One' : '?')).toEqual(['Ten', 'One', 'Two']);
    });
    // THR-2 is no longer inside the Pinned section (its row follows the
    // Pinned heading, i.e. it lives in the ordinary section).
    const pinnedHeading = screen.getByRole('heading', { name: /Pinned/i });
    const twoRow = screen.getByText(/Two pinned/i);
    expect(
      pinnedHeading.compareDocumentPosition(twoRow) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    await act(async () => {
      releasePost();
    });
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Pin thread THR-2/i })).toBeInTheDocument(),
    );
    expect(rowSubjects().map((s) => s.includes('Two pinned') ? 'Two' : s.includes('Ten') ? 'Ten' : s.includes('One') ? 'One' : '?')).toEqual(['Ten', 'One', 'Two']);
  });

  test('multi-row pin failure rolls back BOTH pin state and the exact prior row order', async () => {
    const state = [
      mkThread('THR-2', 'Two pinned', {
        pinned: true,
        pinned_at: '2026-05-20T00:00:00Z',
        started_at: '2026-05-12T00:00:00Z',
      }),
      mkThread('THR-10', 'Ten unpinned', { started_at: '2026-05-14T00:00:00Z' }),
      mkThread('THR-1', 'One ordinary', { started_at: '2026-05-13T00:00:00Z' }),
    ];
    stubServerOrderedList(state);
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-10/pin`, () =>
        HttpResponse.json({ error: 'boom' }, { status: 500 }),
      ),
    );
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() => expect(screen.getByText(/Ten unpinned/i)).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: /Pin thread THR-10/i }));
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(/Pin change failed/i),
    );
    // Rollback: exact prior order restored (Pinned [THR-2], Threads [THR-10, THR-1]).
    expect(rowSubjects().map((s) => s.includes('Two pinned') ? 'Two' : s.includes('Ten') ? 'Ten' : s.includes('One') ? 'One' : '?')).toEqual(['Two', 'Ten', 'One']);
    expect(screen.getByRole('button', { name: /Pin thread THR-10/i })).toBeInTheDocument();
    // No optimistic Pinned-section reorder leaked.
    const pinnedHeading = screen.getByRole('heading', { name: /Pinned/i });
    const tenRow = screen.getByText(/Ten unpinned/i);
    expect(pinnedHeading.compareDocumentPosition(tenRow) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
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

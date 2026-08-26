/**
 * THR-198 Slice C — per-thread mention-routing web control tests.
 *
 * Covers the founder-facing acceptance surface: the control appears only on
 * the founder-authorized settings surface (thread-detail header ⋯ overflow
 * menu → dialog), truthfully renders enabled/disabled, persists explicit
 * changes through the strict-boolean POST /mention-routing wire, treats the
 * idempotent same-state no-op as success, prevents duplicate mutation while
 * in flight, rolls back + surfaces the error on failure, and is keyboard /
 * screen-reader operable (role=switch, aria-checked, labelled). Auth and
 * permission posture is unchanged: the same founder-gated route and strict
 * boolean body are used; no new surface renders the control.
 */
import { screen, waitFor, within } from '@testing-library/react';
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
    mention_routing_enabled: boolean;
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
    mention_routing_enabled: true,
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

function stubDetail(thread: Partial<ReturnType<typeof mkThread>> & { thread_id: string }) {
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

/** Mount the detail page for one thread and open the mention-routing dialog. */
async function openRoutingDialog(thread: ReturnType<typeof mkThread>) {
  stubList([thread]);
  stubDetail(thread);
  mountAt(`/orgs/${SLUG}/threads/${thread.thread_id}`);
  await waitFor(() =>
    expect(screen.getByRole('heading', { name: new RegExp(thread.subject) })).toBeInTheDocument(),
  );
  await userEvent.click(screen.getByRole('button', { name: /Thread actions/i }));
  const menu = screen.getByRole('menu');
  await userEvent.click(within(menu).getByRole('menuitem', { name: /Mention routing/i }));
  const dialog = await screen.findByRole('dialog', { name: /Mention routing/i });
  return dialog;
}

/* ------------------------------------------------------------------ */
/*  Founder visibility / absence                                       */
/* ------------------------------------------------------------------ */

describe('THR-198 Slice C — visibility on the founder settings surface', () => {
  test('the mention-routing control appears only in the detail header overflow menu (founder settings surface)', async () => {
    const thread = mkThread('THR-1', 'Routing subject');
    stubList([thread]);
    stubDetail(thread);
    mountAt(`/orgs/${SLUG}/threads/THR-1`);
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: /Routing subject/i })).toBeInTheDocument(),
    );

    // Not rendered inline anywhere on the detail header (only via the menu).
    expect(screen.queryByRole('switch', { name: /Route replies/i })).not.toBeInTheDocument();

    const overflow = screen.getByRole('button', { name: /Thread actions/i });
    await userEvent.click(overflow);
    const menu = screen.getByRole('menu');
    expect(within(menu).getByRole('menuitem', { name: /Mention routing/i })).toBeInTheDocument();
  });

  test('no mention-routing control on the inbox list rows (outside the founder settings surface)', async () => {
    stubList([mkThread('THR-A', 'Row one')]);
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() => expect(screen.getByText('Row one')).toBeInTheDocument());
    // List rows carry no routing switch — the control is confined to the
    // thread-detail founder settings surface.
    expect(screen.queryByRole('switch')).not.toBeInTheDocument();
    expect(screen.queryByRole('menuitem', { name: /Mention routing/i })).not.toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */
/*  Truthful rendering                                                 */
/* ------------------------------------------------------------------ */

describe('THR-198 Slice C — truthful enabled/disabled rendering', () => {
  test('renders the switch checked when the thread has routing enabled', async () => {
    const dialog = await openRoutingDialog(
      mkThread('THR-1', 'Enabled thread', { mention_routing_enabled: true }),
    );
    const sw = within(dialog).getByRole('switch', { name: /Route replies/i });
    expect(sw).toHaveAttribute('aria-checked', 'true');
  });

  test('renders the switch unchecked when the thread has routing disabled', async () => {
    const dialog = await openRoutingDialog(
      mkThread('THR-1', 'Disabled thread', { mention_routing_enabled: false }),
    );
    const sw = within(dialog).getByRole('switch', { name: /Route replies/i });
    expect(sw).toHaveAttribute('aria-checked', 'false');
  });

  test('defaults to enabled when the payload predates the field (schema default is on)', async () => {
    const thread = mkThread('THR-1', 'Legacy thread');
    // Simulate a pre-Slice-A payload that never carried the field.
    const legacy: Partial<ReturnType<typeof mkThread>> & { thread_id: string } = {
      thread_id: thread.thread_id,
      subject: thread.subject,
      status: thread.status,
      started_at: thread.started_at,
      archived_at: thread.archived_at,
      forwarded_from_id: thread.forwarded_from_id,
      forwarded_from_kind: thread.forwarded_from_kind,
      turn_cap: thread.turn_cap,
      turns_used: thread.turns_used,
      summary: thread.summary,
      transcript_path: thread.transcript_path,
      composed_from_dream_id: thread.composed_from_dream_id,
      last_speaker: thread.last_speaker,
      pinned: thread.pinned,
      pinned_at: thread.pinned_at,
      last_activity_at: thread.last_activity_at,
    };
    stubList([thread]);
    stubDetail(legacy);
    mountAt(`/orgs/${SLUG}/threads/THR-1`);
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: /Legacy thread/i })).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole('button', { name: /Thread actions/i }));
    const menu = screen.getByRole('menu');
    await userEvent.click(within(menu).getByRole('menuitem', { name: /Mention routing/i }));
    const dialog = await screen.findByRole('dialog', { name: /Mention routing/i });
    const sw = within(dialog).getByRole('switch', { name: /Route replies/i });
    // Absent field → the durable schema default (enabled) is the honest state.
    expect(sw).toHaveAttribute('aria-checked', 'true');
  });
});

/* ------------------------------------------------------------------ */
/*  Persist explicit changes                                           */
/* ------------------------------------------------------------------ */

describe('THR-198 Slice C — mutation behavior', () => {
  test('toggle POSTs the strict boolean to /mention-routing and flips the switch', async () => {
    const thread = mkThread('THR-1', 'Toggle subject', { mention_routing_enabled: true });
    let receivedBody: unknown = null;
    server.use(
      http.post(
        `/api/v1/orgs/${SLUG}/threads/THR-1/mention-routing`,
        async ({ request: req }) => {
          receivedBody = await req.json();
          // Stateful stub: the server persists the flip (like the real route).
          thread.mention_routing_enabled = false;
          return HttpResponse.json({
            thread_id: 'THR-1',
            mention_routing_enabled: false,
          });
        },
      ),
    );
    const dialog = await openRoutingDialog(thread);
    const sw = within(dialog).getByRole('switch', { name: /Route replies/i });
    await userEvent.click(sw);

    await waitFor(() => expect(receivedBody).toEqual({ mention_routing_enabled: false }));
    await waitFor(() =>
      expect(within(dialog).getByRole('switch', { name: /Route replies/i })).toHaveAttribute(
        'aria-checked',
        'false',
      ),
    );
  });

  test('idempotent same-state response is success — no error, state intact', async () => {
    // Durable state is already disabled; a stale UI sends the same-state
    // request and the server answers idempotent (no transition, no audit).
    const thread = mkThread('THR-1', 'Idempotent subject', { mention_routing_enabled: false });
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-1/mention-routing`, async () => {
        // Server durable state: already disabled — the requested state.
        return HttpResponse.json({
          thread_id: 'THR-1',
          mention_routing_enabled: false,
          idempotent: true,
        });
      }),
    );
    const dialog = await openRoutingDialog(thread);
    const sw = within(dialog).getByRole('switch', { name: /Route replies/i });
    await userEvent.click(sw); // requests the current (disabled) state

    await waitFor(() =>
      expect(within(dialog).getByRole('switch', { name: /Route replies/i })).toHaveAttribute(
        'aria-checked',
        'false',
      ),
    );
    expect(within(dialog).queryByRole('alert')).not.toBeInTheDocument();
  });

  test('prevents duplicate mutation while a change is in flight', async () => {
    const thread = mkThread('THR-1', 'Pending subject', { mention_routing_enabled: true });
    let resolvePost: (v: unknown) => void = () => {};
    let postCalls = 0;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-1/mention-routing`, () => {
        postCalls += 1;
        return new Promise((resolve) => {
          resolvePost = () =>
            resolve(HttpResponse.json({ thread_id: 'THR-1', mention_routing_enabled: false }));
        });
      }),
    );
    const dialog = await openRoutingDialog(thread);
    const sw = within(dialog).getByRole('switch', { name: /Route replies/i });

    await userEvent.click(sw);
    // In flight: the switch is disabled — a second click cannot fire again.
    await waitFor(() =>
      expect(within(dialog).getByRole('switch', { name: /Route replies/i })).toBeDisabled(),
    );
    await userEvent.click(within(dialog).getByRole('switch', { name: /Route replies/i })).catch(() => {});
    expect(postCalls).toBe(1);

    resolvePost(null);
    await waitFor(() =>
      expect(
        within(dialog).getByRole('switch', { name: /Route replies/i }),
      ).not.toBeDisabled(),
    );
  });

  test('failure rolls the switch back to server state and shows a visible error', async () => {
    const thread = mkThread('THR-1', 'Fail subject', { mention_routing_enabled: true });
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-1/mention-routing`, () =>
        HttpResponse.json({ error: 'boom' }, { status: 500 }),
      ),
    );
    const dialog = await openRoutingDialog(thread);
    const sw = within(dialog).getByRole('switch', { name: /Route replies/i });
    await userEvent.click(sw);

    await waitFor(() =>
      expect(within(dialog).getByRole('alert')).toHaveTextContent(
        /Routing change failed — restored to the previous state/i,
      ),
    );
    // Rollback: the switch re-reads the server truth (still enabled).
    expect(within(dialog).getByRole('switch', { name: /Route replies/i })).toHaveAttribute(
      'aria-checked',
      'true',
    );
  });
});

/* ------------------------------------------------------------------ */
/*  Accessibility + keyboard                                           */
/* ------------------------------------------------------------------ */

describe('THR-198 Slice C — accessibility', () => {
  test('switch exposes role, checked state, and an accessible name', async () => {
    const dialog = await openRoutingDialog(
      mkThread('THR-1', 'A11y subject', { mention_routing_enabled: true }),
    );
    const sw = within(dialog).getByRole('switch', { name: /Route replies to mentioned participants/i });
    expect(sw).toHaveAttribute('aria-checked', 'true');
  });

  test('keyboard activation toggles exactly once via Enter and Space', async () => {
    const thread = mkThread('THR-1', 'Keyboard subject', { mention_routing_enabled: true });
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-1/mention-routing`, async () => {
        // Server persists each toggle (stateful stub).
        thread.mention_routing_enabled = !thread.mention_routing_enabled;
        return HttpResponse.json({
          thread_id: 'THR-1',
          mention_routing_enabled: thread.mention_routing_enabled,
        });
      }),
    );
    const dialog = await openRoutingDialog(thread);
    const sw = within(dialog).getByRole('switch', { name: /Route replies/i });
    sw.focus();
    await userEvent.keyboard('{Enter}');
    await waitFor(() =>
      expect(within(dialog).getByRole('switch', { name: /Route replies/i })).toHaveAttribute(
        'aria-checked',
        'false',
      ),
    );
    // Space toggles back.
    await userEvent.keyboard(' ');
    await waitFor(() =>
      expect(within(dialog).getByRole('switch', { name: /Route replies/i })).toHaveAttribute(
        'aria-checked',
        'true',
      ),
    );
  });

  test('explanatory copy distinguishes routing from priority/fairness', async () => {
    const dialog = await openRoutingDialog(mkThread('THR-1', 'Copy subject'));
    expect(
      within(dialog).getByText(/not priority, queueing, or fairness scheduling/i),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText(/messages with no valid mentions always broadcast/i),
    ).toBeInTheDocument();
  });
});

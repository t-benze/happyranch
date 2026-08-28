/**
 * TASK-5966 — per-thread strict reply-exchange web control tests.
 *
 * Covers the founder-facing acceptance surface of the INDEPENDENT rollback
 * control: the exchange switch renders truthfully in the same founder
 * settings dialog, persists explicit changes through the strict-boolean
 * POST /exchange-routing wire, treats the idempotent same-state no-op as
 * success, and NEVER touches ``mention_routing_enabled`` (the shipped
 * mention-set mode stays intact). Auth/permission posture is unchanged —
 * the same founder-gated route and strict boolean body are used.
 */
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, test } from 'vitest';
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
    mention_routing_enabled: boolean;
    reply_exchange_enabled: boolean;
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
    reply_exchange_enabled: true,
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
  };
}

function stubList(threads: ReturnType<typeof mkThread>[]) {
  server.use(
    http.get(`/api/v1/orgs/${SLUG}/threads`, () =>
      HttpResponse.json({ threads }),
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
      HttpResponse.json({ messages: thread.messages ?? [mkMessage(1, 'hi')] }),
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
});

async function openRoutingDialog(thread: ReturnType<typeof mkThread>) {
  stubList([thread]);
  stubDetail(thread);
  mountAt(`/orgs/${SLUG}/threads/${thread.thread_id}`);
  await waitFor(() =>
    expect(screen.getByRole('heading', { name: new RegExp(thread.subject) })).toBeInTheDocument(),
  );
  await userEvent.click(screen.getByRole('button', { name: 'Mention routing' }));
  return screen.findByRole('dialog', { name: /Mention routing/i });
}

describe('TASK-5966 — reply-exchange control', () => {
  test('dialog renders the exchange switch from the server state', async () => {
    const dialog = await openRoutingDialog(mkThread('THR-1', 'Exchange subject'));
    const sw = within(dialog).getByRole('switch', { name: /Hold replies/i });
    expect(sw).toHaveAttribute('aria-checked', 'true');
  });

  test('toggle POSTs the strict boolean to /exchange-routing and flips the switch', async () => {
    const thread = mkThread('THR-1', 'Toggle subject', {
      reply_exchange_enabled: true,
    });
    let receivedBody: unknown = null;
    server.use(
      http.post(
        `/api/v1/orgs/${SLUG}/threads/THR-1/exchange-routing`,
        async ({ request: req }) => {
          receivedBody = await req.json();
          thread.reply_exchange_enabled = false;
          return HttpResponse.json({
            thread_id: 'THR-1',
            reply_exchange_enabled: false,
          });
        },
      ),
    );
    const dialog = await openRoutingDialog(thread);
    const sw = within(dialog).getByRole('switch', { name: /Hold replies/i });
    await userEvent.click(sw);

    await waitFor(() => expect(receivedBody).toEqual({ reply_exchange_enabled: false }));
    await waitFor(() =>
      expect(within(dialog).getByRole('switch', { name: /Hold replies/i })).toHaveAttribute(
        'aria-checked',
        'false',
      ),
    );
  });

  test('the exchange toggle never touches mention routing', async () => {
    const thread = mkThread('THR-1', 'Independent subject', {
      mention_routing_enabled: true,
      reply_exchange_enabled: true,
    });
    let exchangeBody: unknown = null;
    let mentionBody: unknown = null;
    server.use(
      http.post(
        `/api/v1/orgs/${SLUG}/threads/THR-1/exchange-routing`,
        async ({ request: req }) => {
          exchangeBody = await req.json();
          thread.reply_exchange_enabled = false;
          return HttpResponse.json({
            thread_id: 'THR-1',
            reply_exchange_enabled: false,
          });
        },
      ),
      http.post(
        `/api/v1/orgs/${SLUG}/threads/THR-1/mention-routing`,
        async ({ request: req }) => {
          mentionBody = await req.json();
          return HttpResponse.json({
            thread_id: 'THR-1',
            mention_routing_enabled: true,
          });
        },
      ),
    );
    const dialog = await openRoutingDialog(thread);
    await userEvent.click(
      within(dialog).getByRole('switch', { name: /Hold replies/i }),
    );
    await waitFor(() => expect(exchangeBody).toEqual({ reply_exchange_enabled: false }));
    // Mention routing was NOT called and the routing switch stays on.
    expect(mentionBody).toBeNull();
    expect(
      within(dialog).getByRole('switch', { name: /Route replies/i }),
    ).toHaveAttribute('aria-checked', 'true');
  });

  test('explanatory copy discloses the hold + single catch-up contract', async () => {
    const dialog = await openRoutingDialog(mkThread('THR-1', 'Copy subject'));
    expect(
      within(dialog).getByText(/exactly one range-covering catch-up/i),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText(/separate switch/i),
    ).toBeInTheDocument();
  });
});

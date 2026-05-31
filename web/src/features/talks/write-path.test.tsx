import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'hk-macau-tourism';

const OPEN_TALK = {
  talk_id: 'TALK-0010',
  agent_name: 'support_lead',
  status: 'open',
  started_at: '2026-05-18T10:00:00Z',
  ended_at: null,
  summary: null,
  topic_list: [],
  new_learnings_count: 0,
  new_kb_slugs: [],
  transcript_path: null,
};

function baseStubs() {
  sessionStorage.setItem('happyranch.token', 'tok');
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/talks`, () =>
      HttpResponse.json({ talks: [OPEN_TALK] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/talks/${OPEN_TALK.talk_id}`, () =>
      HttpResponse.json(OPEN_TALK),
    ),
    http.get(`/api/v1/orgs/${SLUG}/agents`, () =>
      HttpResponse.json({ agents: [] }),
    ),
  );
}

describe('Talks write path', () => {
  test('abandon dialog POSTs reason', async () => {
    baseStubs();
    let captured: unknown;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/talks/${OPEN_TALK.talk_id}/abandon`, async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ talk_id: OPEN_TALK.talk_id, status: 'abandoned' });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/talks/${OPEN_TALK.talk_id}`,
    });
    await waitFor(() => expect(screen.getByText(/Talk is open/i)).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: /^Abandon$/ }));
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText(/Reason/i), 'orphaned chat');
    await user.click(within(dialog).getByRole('button', { name: /Confirm abandon/i }));
    await waitFor(() => expect(captured).toEqual({ reason: 'orphaned chat' }));
  });

  test('end dialog POSTs summary + transcript + learnings', async () => {
    baseStubs();
    let captured: unknown;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/talks/${OPEN_TALK.talk_id}/end`, async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({
          talk_id: OPEN_TALK.talk_id,
          status: 'closed',
          transcript_path: '/x.md',
          new_learnings_count: 1,
        });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/talks/${OPEN_TALK.talk_id}`,
    });
    await waitFor(() => expect(screen.getByText(/Talk is open/i)).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: /^End$/ }));
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText(/Summary/), 'all done');
    await user.type(within(dialog).getByLabelText(/Transcript/i), '## founder hi ## agent ok');
    await user.type(within(dialog).getByLabelText(/Learnings/i), 'lesson 1');
    await user.click(within(dialog).getByRole('button', { name: /^End talk$/i }));
    await waitFor(() =>
      expect(captured).toMatchObject({
        summary: 'all done',
        learnings: [{ text: 'lesson 1' }],
      }),
    );
  });

  test('dispatch dialog POSTs brief', async () => {
    baseStubs();
    let captured: unknown;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/talks/${OPEN_TALK.talk_id}/dispatch`, async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({
          task_id: 'TASK-9001',
          team: 'support',
          assigned_agent: 'support_lead',
          dispatched_from_talk_id: OPEN_TALK.talk_id,
        });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/talks/${OPEN_TALK.talk_id}`,
    });
    await waitFor(() => expect(screen.getByText(/Talk is open/i)).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: /^Dispatch$/ }));
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText(/Brief/i), 'follow up on visa update');
    await user.click(within(dialog).getByRole('button', { name: /Confirm dispatch/i }));
    await waitFor(() =>
      expect(captured).toMatchObject({ brief: 'follow up on visa update' }),
    );
  });

  test('start dialog POSTs agent_name and handles talk_already_open', async () => {
    baseStubs();
    let captured: unknown;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/talks`, async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json(
          {
            detail: {
              code: 'talk_already_open',
              prior_open_talk_id: OPEN_TALK.talk_id,
              prior_started_at: OPEN_TALK.started_at,
            },
          },
          { status: 409 },
        );
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/talks` });
    await waitFor(() => expect(screen.getByText(/TALK-0010/)).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: /Start new talk/i }));
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText(/Agent/i), 'support_lead');
    await user.click(within(dialog).getByRole('button', { name: /^Start talk$/i }));
    await waitFor(() => expect(captured).toEqual({ agent_name: 'support_lead' }));
    expect(
      await screen.findByRole('button', { name: /Open existing talk TALK-0010/i }),
    ).toBeInTheDocument();
  });
});

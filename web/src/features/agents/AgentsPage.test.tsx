import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'hk-macau-tourism';

const AGENTS_PAYLOAD = {
  agents: [
    {
      name: 'engineering_head',
      team: 'engineering',
      role: 'manager',
      executor: 'claude',
      description: 'Owns engineering.',
      tier: 'green',
      scorecard: {
        agent: 'engineering_head',
        period_start: '2026-04-19T00:00:00Z',
        period_end: '2026-05-19T00:00:00Z',
        acceptance_rate: 0.94,
        revision_rate: 0.04,
        error_count: 1,
        tier: 'green',
        updated_at: '2026-05-19T00:00:00Z',
      },
      avg_confidence: 88,
    },
    {
      name: 'support_agent',
      team: 'cx',
      role: 'worker',
      executor: 'codex',
      description: 'Handles support.',
      tier: 'yellow',
      scorecard: {
        agent: 'support_agent',
        period_start: '2026-04-19T00:00:00Z',
        period_end: '2026-05-19T00:00:00Z',
        acceptance_rate: 0.82,
        revision_rate: 0.12,
        error_count: 4,
        tier: 'yellow',
        updated_at: '2026-05-19T00:00:00Z',
      },
      avg_confidence: 78,
    },
  ],
};

function stubBaseHandlers() {
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/agents`, () => HttpResponse.json(AGENTS_PAYLOAD)),
  );
}

function mountAt(route: string) {
  sessionStorage.setItem('grassland.token', 'tok');
  return renderWithProviders(<AppRoutes />, { route });
}

describe('AgentsPage — active tab', () => {
  test('renders scorecard and calibration tables with daemon data', async () => {
    stubBaseHandlers();
    mountAt(`/orgs/${SLUG}/agents`);

    await waitFor(() =>
      expect(screen.getAllByText('engineering_head').length).toBeGreaterThan(0),
    );

    // Scorecard heading + agent rows + tier badge text.
    expect(screen.getByText(/Scorecards/i)).toBeInTheDocument();
    // engineering_head shows up in both the scorecard and calibration tables.
    expect(screen.getAllByText('engineering_head').length).toBe(2);
    expect(screen.getAllByText('green').length).toBeGreaterThanOrEqual(1);
    // engineering_head acceptance (scorecards) + accuracy (calibration) both 94%.
    expect(screen.getAllByText('94%').length).toBe(2);

    // Calibration table emits avg confidence + gap (88 - 94 = -6).
    expect(screen.getByRole('heading', { name: 'Calibration' })).toBeInTheDocument();
    expect(screen.getByText('88%')).toBeInTheDocument(); // engineering_head avg_confidence
    expect(screen.getByText('-6%')).toBeInTheDocument();
  });

  test('clicking a scorecard row opens the agent detail Drawer', async () => {
    stubBaseHandlers();
    // Drawer fetches recent tasks + learnings — stub both.
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks`, () =>
        HttpResponse.json({ tasks: [] }),
      ),
      http.get(
        `/api/v1/orgs/${SLUG}/agents/engineering_head/learnings/entries/`,
        () => HttpResponse.json({ entries: [] }),
      ),
    );
    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/agents`);

    await waitFor(() =>
      expect(screen.getAllByText('engineering_head').length).toBeGreaterThan(0),
    );

    // The scorecard row's name is a NavLink — click it.
    const allLinks = screen.getAllByRole('link', { name: /engineering_head/ });
    await user.click(allLinks[0]);

    await waitFor(() => {
      // Drawer surfaces the executor metadata.
      expect(screen.getByText(/executor: claude/)).toBeInTheDocument();
    });
    expect(
      screen.getByText(
        /No tasks where this agent was the assigned manager/,
      ),
    ).toBeInTheDocument();
  });
});

describe('AgentsPage — route collision regression', () => {
  test('an agent literally named "pending" opens the detail drawer (not the tab)', async () => {
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/agents`, () =>
        HttpResponse.json({
          agents: [
            {
              name: 'pending',
              team: 'engineering',
              role: 'worker',
              executor: 'claude',
              description: 'Edge-case agent name.',
              tier: 'green',
              scorecard: null,
              avg_confidence: null,
            },
          ],
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks`, () =>
        HttpResponse.json({ tasks: [] }),
      ),
      http.get(
        `/api/v1/orgs/${SLUG}/agents/pending/learnings/entries/`,
        () => HttpResponse.json({ entries: [] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/agents/pending`);

    // Drawer mounts (not the Pending enrollments tab).
    await waitFor(() =>
      expect(screen.getByText(/executor: claude/)).toBeInTheDocument(),
    );
    expect(
      screen.getByText(/No tasks where this agent was the assigned manager/),
    ).toBeInTheDocument();
  });
});

describe('AgentsPage — pending tab', () => {
  test('lists pending enrollments and approves one', async () => {
    let approveCalled = false;
    stubBaseHandlers();
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/agents/enrollments`, () =>
        HttpResponse.json({
          enrollments: [
            {
              name: 'new_writer',
              team: 'content',
              role: 'worker',
              executor: 'claude',
              description: 'Drafts long-form posts.',
              status: 'pending',
              enrolled_by: 'content_manager',
              created_at: '2026-05-18T19:00:00Z',
            },
          ],
        }),
      ),
      http.post(`/api/v1/orgs/${SLUG}/agents/new_writer/approve`, () => {
        approveCalled = true;
        return HttpResponse.json({ ok: true });
      }),
    );
    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/agents?view=pending`);

    await waitFor(() =>
      expect(screen.getByText('new_writer')).toBeInTheDocument(),
    );
    expect(screen.getByText(/team: content/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /^Approve$/ }));
    await waitFor(() => expect(approveCalled).toBe(true));
  });

  test('reject opens a dialog and posts a reason', async () => {
    let rejectBody: unknown = null;
    stubBaseHandlers();
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/agents/enrollments`, () =>
        HttpResponse.json({
          enrollments: [
            {
              name: 'new_writer',
              team: 'content',
              role: 'worker',
              executor: 'claude',
              description: 'Drafts long-form posts.',
              status: 'pending',
              enrolled_by: 'content_manager',
              created_at: '2026-05-18T19:00:00Z',
            },
          ],
        }),
      ),
      http.post(
        `/api/v1/orgs/${SLUG}/agents/new_writer/reject`,
        async ({ request }) => {
          rejectBody = await request.json();
          return HttpResponse.json({ ok: true });
        },
      ),
    );
    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/agents?view=pending`);

    await waitFor(() =>
      expect(screen.getByText('new_writer')).toBeInTheDocument(),
    );
    await user.click(screen.getByRole('button', { name: /^Reject$/ }));

    // Dialog rendered.
    const dialog = await screen.findByRole('dialog');
    await user.type(
      within(dialog).getByPlaceholderText(/Reason \(optional\)/),
      'duplicate of seo_agent',
    );
    await user.click(within(dialog).getByRole('button', { name: /^Reject$/ }));
    await waitFor(() =>
      expect(rejectBody).toEqual({ reason: 'duplicate of seo_agent' }),
    );
  });
});

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import type { JobRecord } from '@/lib/api/types';

const SLUG = 'hk-macau-tourism';

const AGENTS_PAYLOAD = {
  agents: [
    {
      name: 'engineering_head',
      team: 'engineering',
      role: 'manager',
      executor: 'claude',
      description: 'Owns engineering.',
    },
    {
      name: 'support_agent',
      team: 'cx',
      role: 'worker',
      executor: 'codex',
      description: 'Handles support.',
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
  test('renders the agent roster with team / executor / description', async () => {
    stubBaseHandlers();
    mountAt(`/orgs/${SLUG}/agents`);

    await waitFor(() =>
      expect(screen.getByText('engineering_head')).toBeInTheDocument(),
    );
    expect(screen.getByText('support_agent')).toBeInTheDocument();
    expect(screen.getByText('engineering')).toBeInTheDocument();
    expect(screen.getByText('cx')).toBeInTheDocument();
    expect(screen.getByText('Owns engineering.')).toBeInTheDocument();
    expect(screen.getByText('Handles support.')).toBeInTheDocument();
  });

  test('clicking an agent row opens the detail drawer', async () => {
    stubBaseHandlers();
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
      expect(screen.getByText('engineering_head')).toBeInTheDocument(),
    );

    const link = screen.getByRole('link', { name: /engineering_head/ });
    await user.click(link);

    await waitFor(() => {
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

describe('AgentDetailDrawer — recent jobs cross-link', () => {
  const JOB_FOR_AGENT: JobRecord = {
    id: 'JOB-0005',
    task_id: 'TASK-0010',
    agent_name: 'engineering_head',
    title: 'Run database vacuum',
    rationale: 'Reclaim disk space.',
    script_text: 'vacuumdb --all',
    interpreter: 'bash',
    cwd_hint: null,
    status: 'completed',
    exit_code: 0,
    stdout_head: null,
    stderr_head: null,
    stdout_path: null,
    stderr_path: null,
    duration_ms: 2000,
    started_at: '2026-05-20T08:00:00Z',
    finished_at: '2026-05-20T08:00:02Z',
    reviewed_at: null,
    reviewed_by: null,
    reject_reason: null,
    cwd_resolved: null,
    max_runtime_seconds: 300,
    max_output_bytes: 52428800,
    review_required: false,
    persistent: false,
    reason: null,
    created_at: '2026-05-20T07:59:00Z',
  };

  test('shows recent jobs in agent drawer when data present', async () => {
    sessionStorage.setItem('grassland.token', 'tok');
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/agents`, () => HttpResponse.json(AGENTS_PAYLOAD)),
      http.get(`/api/v1/orgs/${SLUG}/tasks`, () =>
        HttpResponse.json({ tasks: [] }),
      ),
      http.get(
        `/api/v1/orgs/${SLUG}/agents/engineering_head/learnings/entries/`,
        () => HttpResponse.json({ entries: [] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/jobs/`, () =>
        HttpResponse.json({ jobs: [JOB_FOR_AGENT] }),
      ),
    );

    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/agents/engineering_head`,
    });

    await waitFor(() =>
      expect(screen.getByText(/Recent jobs/i)).toBeInTheDocument(),
    );
    const link = screen.getByRole('link', { name: 'JOB-0005' });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute('href', `/orgs/${SLUG}/jobs/JOB-0005`);
    expect(screen.getByText(/Run database vacuum/)).toBeInTheDocument();
  });

  test('hides recent jobs section when agent has none', async () => {
    sessionStorage.setItem('grassland.token', 'tok');
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/agents`, () => HttpResponse.json(AGENTS_PAYLOAD)),
      http.get(`/api/v1/orgs/${SLUG}/tasks`, () =>
        HttpResponse.json({ tasks: [] }),
      ),
      http.get(
        `/api/v1/orgs/${SLUG}/agents/engineering_head/learnings/entries/`,
        () => HttpResponse.json({ entries: [] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/jobs/`, () =>
        HttpResponse.json({ jobs: [] }),
      ),
    );

    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/agents/engineering_head`,
    });

    await waitFor(() =>
      expect(screen.getByText(/executor: claude/)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/Recent jobs/i)).not.toBeInTheDocument();
  });
});

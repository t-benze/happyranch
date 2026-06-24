import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, test } from 'vitest';
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
      repos: {},
      system_prompt: 'You are the engineering head.',
    },
    {
      name: 'support_agent',
      team: 'cx',
      role: 'worker',
      executor: 'codex',
      description: 'Handles support.',
      repos: { happyranch: 'https://github.com/t-benze/happyranch' },
      system_prompt: 'You are support.',
    },
  ],
};

function stubBaseHandlers() {
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/agents`, () => HttpResponse.json(AGENTS_PAYLOAD)),
    http.get(`/api/v1/orgs/${SLUG}/settings`, () =>
      HttpResponse.json({}),
    ),
    http.get(`/api/v1/orgs/${SLUG}/teams`, () =>
      HttpResponse.json({ teams: [] }),
    ),
  );
}

function stubDetailHandlers(agentTasks: unknown[] = []) {
  server.use(
    http.get(`/api/v1/orgs/${SLUG}/tasks`, () =>
      HttpResponse.json({ tasks: agentTasks }),
    ),
    http.get(
      `/api/v1/orgs/${SLUG}/agents/:agentName/learnings/entries/`,
      () => HttpResponse.json({ entries: [] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/jobs/`, () =>
      HttpResponse.json({ jobs: [] }),
    ),
  );
}

function mountAt(route: string) {
  sessionStorage.setItem('happyranch.token', 'tok');
  return renderWithProviders(<AppRoutes />, { route });
}

describe('AgentsPage — two-pane roster list', () => {
  test('renders the agent roster with role meta + description in left pane', async () => {
    stubBaseHandlers();
    // The first agent auto-selects on mount (AGENTS-01), so the detail pane
    // also queries — stub its endpoints to satisfy onUnhandledRequest:'error'.
    stubDetailHandlers();
    mountAt(`/orgs/${SLUG}/agents`);

    // support_agent is the second (un-selected) agent, so it appears once —
    // in the roster — making it a stable anchor for the initial wait.
    await waitFor(() =>
      expect(screen.getByText('support_agent')).toBeInTheDocument(),
    );
    // Names/role-meta/descriptions appear in the roster list inside the
    // left-pane <aside> (there are 2 <aside> elements: sidebar + roster).
    // The auto-selected first agent also renders its name + description in the
    // detail pane, so scope every roster assertion to the roster aside.
    const asides = document.querySelectorAll('aside');
    const rosterAside = asides[1]; // sidebar is [0], roster is [1]
    expect(rosterAside).toBeTruthy();
    expect(within(rosterAside!).getByText('engineering_head')).toBeInTheDocument();
    expect(within(rosterAside!).getByText('support_agent')).toBeInTheDocument();
    // AGENTS-02: meta line reconciled toward the Direction-A 'role · status'
    // form. `role` is on the roster payload (→ Manager / Worker); `status` is
    // NOT, so it is omitted rather than fabricated. The old 'team · executor'
    // meta is gone — support_agent's executor ('codex') no longer appears.
    expect(within(rosterAside!).getByText('Manager')).toBeInTheDocument();
    expect(within(rosterAside!).getByText('Worker')).toBeInTheDocument();
    expect(within(rosterAside!).queryByText(/codex/)).not.toBeInTheDocument();
    expect(within(rosterAside!).getByText('Owns engineering.')).toBeInTheDocument();
    expect(within(rosterAside!).getByText('Handles support.')).toBeInTheDocument();
  });

  test('AGENTS-02: each roster row renders a client-derived avatar-initial chip', async () => {
    stubBaseHandlers();
    stubDetailHandlers();
    mountAt(`/orgs/${SLUG}/agents`);

    await waitFor(() =>
      expect(screen.getByText('support_agent')).toBeInTheDocument(),
    );
    const rosterAside = document.querySelectorAll('aside')[1];
    expect(rosterAside).toBeTruthy();
    // Initials are derived client-side from the agent name (two-token →
    // first letter of each of the first two parts): engineering_head → 'EH',
    // support_agent → 'SA'. No backend field, no per-agent hardcoded map.
    expect(within(rosterAside!).getByText('EH')).toBeInTheDocument();
    expect(within(rosterAside!).getByText('SA')).toBeInTheDocument();
    // Honesty fence: no fabricated status value (e.g. the prototype's
    // 'active'/'idle') is invented for the absent `status` field.
    expect(within(rosterAside!).queryByText('active')).not.toBeInTheDocument();
    expect(within(rosterAside!).queryByText('idle')).not.toBeInTheDocument();
  });

  test('clicking an agent row loads detail in the right pane', async () => {
    stubBaseHandlers();
    stubDetailHandlers();
    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/agents`);

    await waitFor(() =>
      expect(screen.getByText('engineering_head')).toBeInTheDocument(),
    );

    // Rows are buttons now, not links
    const rowBtn = screen.getByRole('button', { name: /engineering_head/ });
    await user.click(rowBtn);

    // Detail pane renders with agent metadata — role pill + team name
    await waitFor(() => {
      expect(screen.getByText('manager')).toBeInTheDocument();
    });
    expect(
      screen.getByText(/No tasks where this agent was the assigned manager/),
    ).toBeInTheDocument();
  });

  test('auto-selects the first roster agent on mount and renders its detail pane', async () => {
    stubBaseHandlers();
    stubDetailHandlers();
    mountAt(`/orgs/${SLUG}/agents`);

    // AGENTS-01: with a non-empty roster, the FIRST agent is auto-selected.
    // The first agent (engineering_head) is a manager and the second
    // (support_agent) is a worker, so the detail-pane "manager" role pill
    // uniquely proves the first agent's detail pane rendered by default.
    await waitFor(() => {
      expect(screen.getByText('manager')).toBeInTheDocument();
    });
    // Detail pane is rendered, so no empty "Select an agent" pane appears.
    expect(screen.queryByText(/Select an agent/)).not.toBeInTheDocument();
  });

  test('empty roster renders the calm empty state without auto-selecting', async () => {
    stubBaseHandlers();
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/agents`, () =>
        HttpResponse.json({ agents: [] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/agents`);

    // Left pane: roster empty state. Right pane: calm "No agents yet" state.
    // Nothing is auto-selected, so the page renders without error.
    await waitFor(() => {
      expect(screen.getByText('No agents enrolled')).toBeInTheDocument();
    });
    expect(screen.getByText('No agents yet')).toBeInTheDocument();
  });

  test('Add agent button opens dialog', async () => {
    stubBaseHandlers();
    // Auto-select (AGENTS-01) mounts the detail pane — stub its endpoints.
    stubDetailHandlers();
    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/agents`);

    await user.click(screen.getByRole('button', { name: 'Add agent' }));
    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });
    expect(screen.getByText('New agent')).toBeInTheDocument();
  });
});

describe('AgentDetailPane — editable fields', () => {
  test('shows executor selector and allows switching', async () => {
    stubBaseHandlers();
    stubDetailHandlers();
    mountAt(`/orgs/${SLUG}/agents/engineering_head`);

    await waitFor(() => {
      expect(screen.getByText('Executor')).toBeInTheDocument();
    });
    // Executor segmented control buttons should be visible
    const claudeBtn = screen.getByRole('button', { name: 'claude' });
    expect(claudeBtn).toBeInTheDocument();
  });

  test('shows repo chips and Add repository button', async () => {
    stubBaseHandlers();
    stubDetailHandlers();
    mountAt(`/orgs/${SLUG}/agents/support_agent`);

    await waitFor(() => {
      expect(screen.getByText('happyranch')).toBeInTheDocument();
    });
    expect(screen.getByText('Add repository')).toBeInTheDocument();
  });

  test('shows system prompt collapsible', async () => {
    stubBaseHandlers();
    stubDetailHandlers();
    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/agents/engineering_head`);

    await waitFor(() => {
      expect(screen.getByText('System prompt')).toBeInTheDocument();
    });
    await user.click(screen.getByText('System prompt'));
    await waitFor(() => {
      expect(screen.getByText(/You are the engineering head/)).toBeInTheDocument();
    });
  });

  test('shows accountability metrics with real task counts', async () => {
    stubBaseHandlers();
    stubDetailHandlers([
      {
        task_id: 'TASK-001',
        brief: 'Test task',
        status: 'completed',
        team: 'engineering',
        assigned_agent: 'engineering_head',
        parent_task_id: null,
        revisit_of_task_id: null,
        created_at: '2026-06-01T00:00:00Z',
        updated_at: '2026-06-01T00:00:00Z',
        closed_at: null,
        cancelled_at: null,
        session_timeout_seconds: null,
        block_kind: null,
      },
      {
        task_id: 'TASK-002',
        brief: 'Pending task',
        status: 'pending',
        team: 'engineering',
        assigned_agent: 'engineering_head',
        parent_task_id: null,
        revisit_of_task_id: null,
        created_at: '2026-06-02T00:00:00Z',
        updated_at: '2026-06-02T00:00:00Z',
        closed_at: null,
        cancelled_at: null,
        session_timeout_seconds: null,
        block_kind: null,
      },
    ]);
    mountAt(`/orgs/${SLUG}/agents/engineering_head`);

    await waitFor(() => {
      expect(screen.getByText('Accountability')).toBeInTheDocument();
      expect(screen.getByText('done')).toBeInTheDocument();
      expect(screen.getByText('tasks')).toBeInTheDocument();
    });
  });

  test('close button clears selection and shows calm state', async () => {
    stubBaseHandlers();
    stubDetailHandlers();
    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/agents/engineering_head`);

    await waitFor(() => {
      expect(screen.getByText('manager')).toBeInTheDocument();
    });

    // Click the X close button — it's the first X icon button in the detail pane
    const closeButtons = screen.getAllByRole('button');
    // Find the button containing only an X icon (no text children)
    const closeBtn = closeButtons.find((btn) => btn.closest('header') && !btn.textContent);
    expect(closeBtn).toBeTruthy();
    if (closeBtn) await user.click(closeBtn);

    await waitFor(() => {
      expect(
        screen.getAllByText(/Select an agent/).length,
      ).toBeGreaterThan(0);
    });
  });
});

describe('AgentDetailPane — save flow (executor switch)', () => {
  // Clear sessionStorage between tests
  beforeEach(() => {
    sessionStorage.clear();
  });

  test('shows save bar when executor is changed', async () => {
    let executorPutCalled = false;
    stubBaseHandlers();
    stubDetailHandlers();
    server.use(
      http.put(`/api/v1/orgs/${SLUG}/agents/engineering_head/executor`, async () => {
        executorPutCalled = true;
        return HttpResponse.json({
          agent: 'engineering_head',
          before: { org_executor: 'claude', workspace_executor: 'claude' },
          after: { org_executor: 'codex', workspace_executor: 'codex' },
          stale_files: [],
        });
      }),
    );
    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/agents/engineering_head`);

    // Wait for detail pane to render with executor segmented control.
    await waitFor(() => {
      expect(screen.getByText('manager')).toBeInTheDocument();
    });
    // The executor segmented control: find "codex" button and click it.
    const codexBtn = screen.getByRole('button', { name: 'codex' });
    await user.click(codexBtn);

    // Save bar should appear
    await waitFor(() => {
      expect(screen.getByText(/unsaved changes/)).toBeInTheDocument();
    });
    expect(screen.getByText('Save agent')).toBeInTheDocument();
    expect(screen.getByText('Reset')).toBeInTheDocument();

    // Click Save
    await user.click(screen.getByText('Save agent'));

    await waitFor(() => {
      expect(executorPutCalled).toBe(true);
    });
  });

  test('Reset reverts dirty state', async () => {
    stubBaseHandlers();
    stubDetailHandlers();
    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/agents/engineering_head`);

    await waitFor(() => {
      expect(screen.getByText('manager')).toBeInTheDocument();
    });
    // Find "codex" button in the segmented executor control
    const codexBtn = screen.getByRole('button', { name: 'codex' });
    await user.click(codexBtn);

    // Save bar visible
    await waitFor(() => {
      expect(screen.getByText('Reset')).toBeInTheDocument();
    });

    // Click Reset
    await user.click(screen.getByText('Reset'));

    // Save bar hidden — executor back to claude
    await waitFor(() => {
      expect(screen.queryByText('Reset')).not.toBeInTheDocument();
    });
  });
});

describe('AgentDetailPane — save flow (repo management)', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  test('shows save bar when a repo is removed', async () => {
    let repoRemoveCalled = false;
    stubBaseHandlers();
    stubDetailHandlers();
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/agents/support_agent/repos`, async ({ request }) => {
        const body = await request.json() as Record<string, unknown>;
        if (body.action === 'remove') repoRemoveCalled = true;
        return HttpResponse.json({ ok: true });
      }),
    );
    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/agents/support_agent`);

    await waitFor(() => {
      expect(screen.getByText('happyranch')).toBeInTheDocument();
    });

    // Click X to remove the repo
    const removeBtn = screen.getByRole('button', { name: 'Remove happyranch' });
    await user.click(removeBtn);

    // Save bar should appear
    await waitFor(() => {
      expect(screen.getByText('Save agent')).toBeInTheDocument();
    });

    // Click Save
    await user.click(screen.getByText('Save agent'));

    await waitFor(() => {
      expect(repoRemoveCalled).toBe(true);
    });
  });

  test('Save error shows inline message', async () => {
    stubBaseHandlers();
    stubDetailHandlers();
    server.use(
      http.put(`/api/v1/orgs/${SLUG}/agents/engineering_head/executor`, () =>
        HttpResponse.json({ detail: 'Internal error' }, { status: 500 }),
      ),
    );
    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/agents/engineering_head`);

    await waitFor(() => {
      expect(screen.getByText('manager')).toBeInTheDocument();
    });
    // Click "codex" button in the segmented executor control
    const codexBtn = screen.getByRole('button', { name: 'codex' });
    await user.click(codexBtn);

    await waitFor(() => {
      expect(screen.getByText('Save agent')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Save agent'));

    await waitFor(() => {
      expect(screen.getByText(/Save error/)).toBeInTheDocument();
    });
  });
});

describe('AgentsPage — route collision regression', () => {
  test('an agent literally named "pending" shows detail in right pane (not the tab)', async () => {
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
              repos: {},
              system_prompt: 'You are pending.',
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
      http.get(`/api/v1/orgs/${SLUG}/jobs/`, () =>
        HttpResponse.json({ jobs: [] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/agents/pending`);

    // Detail pane shows the "pending" agent's metadata, not the enrollments tab
    await waitFor(() =>
      expect(screen.getByText('worker')).toBeInTheDocument(),
    );
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

describe('AgentDetailPane — recent jobs cross-link', () => {
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

  test('shows recent jobs in agent detail pane when data present', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
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
    sessionStorage.setItem('happyranch.token', 'tok');
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
      expect(screen.getByText('manager')).toBeInTheDocument(),
    );
    expect(screen.queryByText(/Recent jobs/i)).not.toBeInTheDocument();
  });
});

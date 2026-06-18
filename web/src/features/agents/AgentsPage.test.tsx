import { screen, waitFor, within, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, test, vi, beforeAll } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import type { JobRecord } from '@/lib/api/types';

// Radix Select uses scrollIntoView which isn't available in jsdom.
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

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
  test('renders the agent roster with team / executor / description in left pane', async () => {
    stubBaseHandlers();
    mountAt(`/orgs/${SLUG}/agents`);

    await waitFor(() =>
      expect(screen.getByText('engineering_head')).toBeInTheDocument(),
    );
    expect(screen.getByText('support_agent')).toBeInTheDocument();
    // "engineering" and "cx" appear in the roster list inside the left-pane <aside>
    // (there are 2 <aside> elements: sidebar + roster). Scope to the second one.
    const asides = document.querySelectorAll('aside');
    const rosterAside = asides[1]; // sidebar is [0], roster is [1]
    expect(rosterAside).toBeTruthy();
    const engMatches = within(rosterAside!).getAllByText(/engineering/);
    expect(engMatches.length).toBeGreaterThan(0);
    const cxMatches = within(rosterAside!).getAllByText(/cx/);
    expect(cxMatches.length).toBeGreaterThan(0);
    expect(screen.getByText('Owns engineering.')).toBeInTheDocument();
    expect(screen.getByText('Handles support.')).toBeInTheDocument();
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

    // Detail pane renders with agent metadata
    await waitFor(() => {
      expect(screen.getByText(/team: engineering/)).toBeInTheDocument();
    });
    expect(
      screen.getByText(/No tasks where this agent was the assigned manager/),
    ).toBeInTheDocument();
  });

  test('shows calm empty state when no agent selected', () => {
    stubBaseHandlers();
    mountAt(`/orgs/${SLUG}/agents`);

    expect(
      screen.getByText(/Select an agent from the roster/),
    ).toBeInTheDocument();
  });

  test('Add agent button opens dialog', async () => {
    stubBaseHandlers();
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
      expect(screen.getByText('claude')).toBeInTheDocument();
    });
    // Executor select trigger should be visible
    expect(screen.getByText(/Executor/)).toBeInTheDocument();
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
      expect(screen.getByText(/1 done/)).toBeInTheDocument();
    });
    expect(screen.getByText(/2 total tasks/)).toBeInTheDocument();
  });

  test('close button clears selection and shows calm state', async () => {
    stubBaseHandlers();
    stubDetailHandlers();
    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/agents/engineering_head`);

    await waitFor(() => {
      expect(screen.getByText(/team: engineering/)).toBeInTheDocument();
    });

    // Click the X close button — it's the first X icon button in the detail pane
    const closeButtons = screen.getAllByRole('button');
    // Find the button containing only an X icon (no text children)
    const closeBtn = closeButtons.find((btn) => btn.closest('header') && !btn.textContent);
    expect(closeBtn).toBeTruthy();
    if (closeBtn) await user.click(closeBtn);

    await waitFor(() => {
      expect(
        screen.getByText(/Select an agent from the roster/),
      ).toBeInTheDocument();
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

    // Wait for detail pane to render, then find the executor Select.
    await waitFor(() => {
      expect(screen.getByText(/team: engineering/)).toBeInTheDocument();
    });
    // The executor Select is a combobox. There are 2 comboboxes (sidebar org
    // switcher + this one). Scope to the detail pane by looking inside <main>.
    const mainEl = document.querySelector('main');
    expect(mainEl).toBeTruthy();
    const selectTrigger = within(mainEl!).getByRole('combobox');
    fireEvent.click(selectTrigger);
    const codexOption = await screen.findByRole('option', { name: 'codex' });
    fireEvent.click(codexOption);

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
      expect(screen.getByText(/team: engineering/)).toBeInTheDocument();
    });
    const mainEl = document.querySelector('main');
    expect(mainEl).toBeTruthy();
    const selectTrigger = within(mainEl!).getByRole('combobox');
    fireEvent.click(selectTrigger);
    const codexOption = await screen.findByRole('option', { name: 'codex' });
    fireEvent.click(codexOption);

    // Save bar visible
    await waitFor(() => {
      expect(screen.getByText('Reset')).toBeInTheDocument();
    });

    // Click Reset
    await user.click(screen.getByText('Reset'));

    // Save bar hidden, executor back to claude (shown in trigger)
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
      expect(screen.getByText(/team: engineering/)).toBeInTheDocument();
    });
    const mainEl = document.querySelector('main');
    expect(mainEl).toBeTruthy();
    const selectTrigger = within(mainEl!).getByRole('combobox');
    fireEvent.click(selectTrigger);
    const codexOption = await screen.findByRole('option', { name: 'codex' });
    fireEvent.click(codexOption);

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
      expect(screen.getByText(/team: engineering/)).toBeInTheDocument(),
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
      expect(screen.getByText(/team: engineering/)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/Recent jobs/i)).not.toBeInTheDocument();
  });
});

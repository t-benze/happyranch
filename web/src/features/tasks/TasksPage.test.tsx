import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import type { ActiveChainResponse, JobRecord, TaskRecord } from '@/lib/api/types';

const SLUG = 'hk-macau-tourism';

function mountAt(route: string) {
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
  );
  return renderWithProviders(<AppRoutes />, { route });
}

/** A root task fixture with severity_rollup (roots endpoint field). */
function rootTask(overrides?: Partial<TaskRecord> & Record<string, unknown>): TaskRecord {
  return {
    task_id: 'TASK-0091',
    team: 'content',
    brief: 'Draft Hong Kong visa guide v2',
    status: 'completed',
    block_kind: null,
    parent_task_id: null,
    revisit_of_task_id: null,
    created_at: '2026-05-18T10:00:00Z',
    updated_at: '2026-05-18T10:06:12Z',
    closed_at: null,
    cancelled_at: null,
    session_timeout_seconds: null,
    severity_rollup: 'completed',
    ...overrides,
  } as TaskRecord;
}

const TASK = rootTask({ status: 'in_progress', severity_rollup: 'in_progress' });

const JOB: JobRecord = {
  id: 'JOB-0001',
  task_id: 'TASK-0091',
  agent_name: 'content_writer',
  title: 'Generate sitemap',
  rationale: 'SEO improvement.',
  script_text: 'python3 gen_sitemap.py',
  interpreter: 'bash',
  cwd_hint: null,
  status: 'completed',
  exit_code: 0,
  stdout_head: null,
  stderr_head: null,
  stdout_path: null,
  stderr_path: null,
  duration_ms: 800,
  started_at: '2026-05-18T10:02:00Z',
  finished_at: '2026-05-18T10:02:01Z',
  reviewed_at: null,
  reviewed_by: null,
  reject_reason: null,
  cwd_resolved: null,
  max_runtime_seconds: 300,
  max_output_bytes: 52428800,
  review_required: false,
  persistent: false,
  reason: null,
  created_at: '2026-05-18T10:01:00Z',
};

describe('TasksPage — read path (roots endpoint)', () => {
  test('fetches from /tasks/roots and renders fixture tasks', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [TASK] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    await waitFor(() =>
      expect(screen.getByText(/Draft Hong Kong visa guide/)).toBeInTheDocument(),
    );
  });

  test('renders group-by selector tabs', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [TASK] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Tasks' })).toBeInTheDocument();
      expect(screen.getByRole('tab', { name: 'Status' })).toBeInTheDocument();
      expect(screen.getByRole('tab', { name: 'Agent' })).toBeInTheDocument();
      expect(screen.getByRole('tab', { name: 'Thread' })).toBeInTheDocument();
    });
  });

  test('groups tasks by status with group heading', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [TASK] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    await waitFor(() => {
      expect(screen.getByText(/In progress/)).toBeInTheDocument();
    });
  });

  test('renders severity_rollup badge in TaskCard (worst subtree status)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    // Root is pending but has a blocked child → severity_rollup = 'blocked'
    // Use a brief that doesn't contain 'blocked' to avoid ambiguity with badge text
    const taskWithRollup = rootTask({
      task_id: 'TASK-0100',
      status: 'pending',
      severity_rollup: 'blocked',
      brief: 'Root task that has a stuck child',
    });
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [taskWithRollup] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    await waitFor(() => {
      // The badge should show 'blocked' from severity_rollup, not 'pending'
      expect(screen.getByText('blocked')).toBeInTheDocument();
      expect(screen.getByText(/Root task that has a stuck child/)).toBeInTheDocument();
    });
  });

  test('groups by thread on dispatched_from_thread_id, with no-thread bucket', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const threaded = rootTask({
      task_id: 'TASK-0200',
      dispatched_from_thread_id: 'THR-0030',
      status: 'in_progress',
      severity_rollup: 'in_progress',
    });
    const unthreaded = rootTask({
      task_id: 'TASK-0201',
      team: 'engineering',
      status: 'pending',
      severity_rollup: 'pending',
    });
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [threaded, unthreaded] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    // Switch to the Thread group-by tab
    const user = userEvent.setup();
    const threadTab = await screen.findByRole('tab', { name: 'Thread' });
    await user.click(threadTab);
    await waitFor(() => {
      // Should have a THR-0030 group heading AND a "No thread" heading
      expect(screen.getByText('THR-0030')).toBeInTheDocument();
      expect(screen.getByText('No thread')).toBeInTheDocument();
    });
  });

  test('renders supersede/revisit links from roots payload fields', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const superseder = rootTask({
      task_id: 'TASK-0300',
      revisit_of_task_id: 'TASK-0299',
      direct_revisits: ['TASK-0301'],
      status: 'completed',
      severity_rollup: 'completed',
    });
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [superseder] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    await waitFor(() => {
      expect(screen.getByText(/supersedes/)).toBeInTheDocument();
      expect(screen.getByText(/TASK-0299/)).toBeInTheDocument();
      expect(screen.getByText(/superseded by/)).toBeInTheDocument();
      expect(screen.getByText(/TASK-0301/)).toBeInTheDocument();
    });

    // Lineage links carry correct hrefs
    const supersedesLink = screen.getByRole('link', { name: /supersedes TASK-0299/ });
    expect(supersedesLink).toHaveAttribute('href', `/orgs/${SLUG}/tasks/TASK-0299`);
    const supersededByLink = screen.getByRole('link', { name: /superseded by TASK-0301/ });
    expect(supersededByLink).toHaveAttribute('href', `/orgs/${SLUG}/tasks/TASK-0301`);
  });

  test('renders 0 count when query resolves to empty (no loading placeholder)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    await waitFor(() => {
      // Empty state, not a loading indicator
      expect(screen.getByText(/No tasks match/)).toBeInTheDocument();
    });
  });
});

describe('TaskDetailPane — jobs cross-link', () => {
  function stubHandlers(jobs: JobRecord[]) {
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [TASK] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/${TASK.task_id}`, () =>
        HttpResponse.json(TASK),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/${TASK.task_id}/recall`, () =>
        HttpResponse.json({
          task_id: TASK.task_id,
          assigned_agent: null,
          brief: TASK.brief,
          status: TASK.status,
          output_summary: null,
          children: [],
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/jobs/`, () =>
        HttpResponse.json({ jobs }),
      ),
    );
  }

  test('shows jobs section when task has jobs', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubHandlers([JOB]);
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });
    await waitFor(() =>
      expect(screen.getByText(/Jobs from this task/i)).toBeInTheDocument(),
    );
    const link = screen.getByRole('link', { name: 'JOB-0001' });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute('href', `/orgs/${SLUG}/jobs/JOB-0001`);
    expect(screen.getByText(/Generate sitemap/)).toBeInTheDocument();
    expect(screen.getByText(/completed/)).toBeInTheDocument();
  });

  test('hides jobs section when task has no jobs', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubHandlers([]);
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });
    await waitFor(() =>
      expect(screen.getByText(/Live events/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/Jobs from this task/i)).not.toBeInTheDocument();
  });
});

describe('TaskDetailPane — workflow chain timeline', () => {
  const ACTIVE_CHAIN: ActiveChainResponse = {
    step_index: 1,
    first_leg_expect_verdict: null,
    legs: [
      { agent: 'senior_dev', prompt: 'review the PR', expect_verdict: 'APPROVE' },
      { agent: 'qa_engineer', prompt: 'run QA suite', expect_verdict: 'PASS' },
    ],
    step_audit_id: 14,
  };

  const TASK_DETAIL_ENVELOPE = {
    task: TASK,
    results: [],
    audit_log: [],
    revisit_chain: [],
    direct_revisits: [],
    predecessor_prior_status: null,
    blocked_on_jobs: null,
  };

  function stubHandlers(
    active_chain: ActiveChainResponse | null,
    taskOverrides?: Partial<TaskRecord> & Record<string, unknown>,
    blocked_on_jobs?: unknown,
  ) {
    const detailTask = { ...TASK, ...taskOverrides } as TaskRecord;
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [TASK] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/${detailTask.task_id}`, () =>
        HttpResponse.json({
          ...TASK_DETAIL_ENVELOPE,
          task: detailTask,
          active_chain,
          blocked_on_jobs: blocked_on_jobs ?? null,
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/${detailTask.task_id}/recall`, () =>
        HttpResponse.json({
          task_id: detailTask.task_id,
          assigned_agent: null,
          brief: detailTask.brief,
          status: detailTask.status,
          output_summary: null,
          children: [],
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/jobs/`, () =>
        HttpResponse.json({ jobs: [] }),
      ),
    );
  }

  test('renders the chain timeline when active_chain is set', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubHandlers(ACTIVE_CHAIN);
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });
    expect(await screen.findByText(/Workflow chain/i)).toBeInTheDocument();
    expect(screen.getByText('senior_dev')).toBeInTheDocument();
    expect(screen.getByText('qa_engineer')).toBeInTheDocument();
    expect(screen.getByText(/APPROVE/)).toBeInTheDocument();
  });

  test('does not render the chain timeline when active_chain is null', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubHandlers(null);
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });
    await waitFor(() =>
      expect(screen.getByText(/Live events/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/Workflow chain/i)).not.toBeInTheDocument();
  });

  test('renders blocked chain node when task is blocked with block_kind', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubHandlers(
      { ...ACTIVE_CHAIN, step_index: 0 },
      { status: 'blocked', block_kind: 'escalated' },
    );
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });
    expect(await screen.findByText(/Workflow chain/i)).toBeInTheDocument();
    // The blocked node should show "Blocked on: escalation"
    expect(screen.getByText(/Blocked on:/)).toBeInTheDocument();
    expect(screen.getByText(/escalation/)).toBeInTheDocument();
  });

  test('renders blocked chain node with job IDs from blocked_on_jobs', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubHandlers(
      { ...ACTIVE_CHAIN, step_index: 1 },
      { status: 'blocked', block_kind: 'blocked_on_job' },
      [{ job_id: 'JOB-0042', status: 'pending' }],
    );
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });
    expect(await screen.findByText(/Workflow chain/i)).toBeInTheDocument();
    expect(screen.getByText(/Blocked on:/)).toBeInTheDocument();
    expect(screen.getByText(/JOB-0042/)).toBeInTheDocument();
  });
});

describe('TaskDetailPane — execution subtasks', () => {
  function stubHandlers() {
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [TASK] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/${TASK.task_id}`, () =>
        HttpResponse.json(TASK),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/${TASK.task_id}/recall`, () =>
        HttpResponse.json({
          task_id: TASK.task_id,
          assigned_agent: 'content_writer',
          brief: TASK.brief,
          status: TASK.status,
          output_summary: null,
          children: [
            {
              task_id: 'TASK-0092',
              assigned_agent: 'content_writer',
              brief: 'Section 4: currency policy',
              status: 'completed',
              output_summary: 'Wrote section 4.',
              children: [],
            },
          ],
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/jobs/`, () =>
        HttpResponse.json({ jobs: [] }),
      ),
    );
  }

  test('shows execution subtasks from recall tree', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubHandlers();
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });
    await waitFor(() => {
      expect(screen.getByText(/Execution subtasks/i)).toBeInTheDocument();
    });
    expect(screen.getAllByText('TASK-0092').length).toBeGreaterThan(0);
    expect(screen.getAllByText('content_writer').length).toBeGreaterThan(0);
  });
});

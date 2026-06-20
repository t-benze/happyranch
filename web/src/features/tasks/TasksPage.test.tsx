import { fireEvent, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import type { ActiveChainResponse, JobRecord } from '@/lib/api/types';

const SLUG = 'hk-macau-tourism';

function mountAt(route: string) {
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
  );
  return renderWithProviders(<AppRoutes />, { route });
}

const TASK = {
  task_id: 'TASK-0091',
  team: 'content',
  brief: 'Draft Hong Kong visa guide v2',
  status: 'in_progress',
  block_kind: null,
  parent_task_id: null,
  revisit_of_task_id: null,
  created_at: '2026-05-18T10:00:00Z',
  updated_at: '2026-05-18T10:06:12Z',
  closed_at: null,
  cancelled_at: null,
  session_timeout_seconds: null,
  severity_rollup: 'in_progress',
};

const TASK_BLOCKED = {
  task_id: 'TASK-0090',
  team: 'ops',
  brief: 'Vet partner hotel candidates',
  status: 'blocked',
  block_kind: 'escalated',
  assigned_agent: 'qa_engineer',
  parent_task_id: null,
  revisit_of_task_id: null,
  created_at: '2026-05-18T09:00:00Z',
  updated_at: '2026-05-18T09:30:00Z',
  closed_at: null,
  cancelled_at: null,
  session_timeout_seconds: null,
  severity_rollup: 'blocked',
};

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

describe('TasksPage — read path', () => {
  test('renders roots-only list with severity rollup', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [TASK, TASK_BLOCKED] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    await waitFor(() => {
      expect(screen.getByText(/Draft Hong Kong visa guide/)).toBeInTheDocument();
      expect(screen.getByText(/Vet partner hotel/)).toBeInTheDocument();
    });
  });

  test('renders group-by segmented control', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [TASK] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    await waitFor(() => {
      expect(screen.getByText('Status')).toBeInTheDocument();
      expect(screen.getByText('Agent')).toBeInTheDocument();
      expect(screen.getByText('Thread')).toBeInTheDocument();
    });
  });

  test('shows severity rollup pill on root row', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [TASK_BLOCKED] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    await waitFor(() => {
      expect(screen.getByText('Blocked')).toBeInTheDocument();
    });
  });

  test('shows Empty state when no tasks', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    await waitFor(() => {
      expect(screen.getByText(/No tasks yet/i)).toBeInTheDocument();
    });
  });

  test('renders loading skeletons', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    // Use a handler that never resolves to trigger loading state
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        new Promise(() => { /* hang forever */ }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    // Skeleton rows should have aria-busy
    await waitFor(() => {
      expect(screen.getByRole('generic', { busy: true })).toBeInTheDocument();
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
        HttpResponse.json({
          task: TASK,
          results: [],
          audit_log: [],
          revisit_chain: [TASK.task_id],
          direct_revisits: [],
          predecessor_prior_status: null,
          blocked_on_jobs: null,
        }),
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
    // Wait for the drawer to fully load — "Live events" section always renders.
    await waitFor(() =>
      expect(screen.getByText(/Live events/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/Jobs from this task/i)).not.toBeInTheDocument();
  });
});

describe('TaskDetailPane — BlockedOnInfo job links', () => {
  const TASK_BLOCKED_ON_JOBS = {
    task_id: 'TASK-0200',
    team: 'engineering',
    brief: 'Task blocked on two jobs',
    status: 'blocked',
    block_kind: null,
    parent_task_id: null,
    revisit_of_task_id: null,
    assigned_agent: 'dev_agent',
    created_at: '2026-05-18T10:00:00Z',
    updated_at: '2026-05-18T10:06:12Z',
    closed_at: null,
    cancelled_at: null,
    session_timeout_seconds: null,
    severity_rollup: 'blocked',
  };

  const BLOCKED_ON_JOBS = [
    { job_id: 'JOB-0050', status: 'running' },
    { job_id: 'JOB-0051', status: 'failed' },
  ];

  function stubHandlers() {
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [TASK_BLOCKED_ON_JOBS] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/${TASK_BLOCKED_ON_JOBS.task_id}`, () =>
        HttpResponse.json({
          task: TASK_BLOCKED_ON_JOBS,
          results: [],
          audit_log: [],
          revisit_chain: [TASK_BLOCKED_ON_JOBS.task_id],
          direct_revisits: [],
          predecessor_prior_status: null,
          blocked_on_jobs: BLOCKED_ON_JOBS,
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/${TASK_BLOCKED_ON_JOBS.task_id}/recall`, () =>
        HttpResponse.json({
          task_id: TASK_BLOCKED_ON_JOBS.task_id,
          assigned_agent: null,
          brief: TASK_BLOCKED_ON_JOBS.brief,
          status: TASK_BLOCKED_ON_JOBS.status,
          output_summary: null,
          children: [],
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/jobs/`, () =>
        HttpResponse.json({ jobs: [] }),
      ),
    );
  }

  test('renders blocked-on job IDs as navigable links', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubHandlers();
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK_BLOCKED_ON_JOBS.task_id}`,
    });
    await waitFor(() =>
      expect(screen.getByText(/Waiting on jobs/i)).toBeInTheDocument(),
    );
    const link50 = screen.getByRole('link', { name: 'JOB-0050' });
    expect(link50).toBeInTheDocument();
    expect(link50).toHaveAttribute('href', `/orgs/${SLUG}/jobs/JOB-0050`);
    const link51 = screen.getByRole('link', { name: 'JOB-0051' });
    expect(link51).toBeInTheDocument();
    expect(link51).toHaveAttribute('href', `/orgs/${SLUG}/jobs/JOB-0051`);
  });

  test('renders blocked-on single job with singular label', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [TASK_BLOCKED_ON_JOBS] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/${TASK_BLOCKED_ON_JOBS.task_id}`, () =>
        HttpResponse.json({
          task: TASK_BLOCKED_ON_JOBS,
          results: [],
          audit_log: [],
          revisit_chain: [TASK_BLOCKED_ON_JOBS.task_id],
          direct_revisits: [],
          predecessor_prior_status: null,
          blocked_on_jobs: [{ job_id: 'JOB-0099', status: 'running' }],
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/${TASK_BLOCKED_ON_JOBS.task_id}/recall`, () =>
        HttpResponse.json({
          task_id: TASK_BLOCKED_ON_JOBS.task_id,
          assigned_agent: null,
          brief: TASK_BLOCKED_ON_JOBS.task_id,
          status: TASK_BLOCKED_ON_JOBS.status,
          output_summary: null,
          children: [],
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/jobs/`, () =>
        HttpResponse.json({ jobs: [] }),
      ),
    );
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK_BLOCKED_ON_JOBS.task_id}`,
    });
    await waitFor(() =>
      expect(screen.getByText(/Waiting on job:/i)).toBeInTheDocument(),
    );
    const link = screen.getByRole('link', { name: 'JOB-0099' });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute('href', `/orgs/${SLUG}/jobs/JOB-0099`);
  });
});

describe('TaskDetailPane — workflow chain strip', () => {
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
    revisit_chain: [TASK.task_id],
    direct_revisits: [],
    predecessor_prior_status: null,
    blocked_on_jobs: null,
  };

  function stubHandlers(active_chain: ActiveChainResponse | null) {
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [TASK] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/${TASK.task_id}`, () =>
        HttpResponse.json({ ...TASK_DETAIL_ENVELOPE, active_chain }),
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
        HttpResponse.json({ jobs: [] }),
      ),
    );
  }

  test('renders the chain strip when active_chain is set', async () => {
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

  test('does not render the chain strip when active_chain is null', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubHandlers(null);
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });
    // Wait for the drawer to fully load — "Live events" section always renders.
    await waitFor(() =>
      expect(screen.getByText(/Live events/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/Workflow chain/i)).not.toBeInTheDocument();
  });
});

describe('TasksPage — direct_revisits lineage inline', () => {
  const TASK_WITH_REVISITS = {
    task_id: 'TASK-0100',
    team: 'engineering',
    brief: 'Parent task with revisits',
    status: 'completed',
    block_kind: null,
    parent_task_id: null,
    revisit_of_task_id: 'TASK-0095',
    assigned_agent: 'dev_agent',
    created_at: '2026-05-18T10:00:00Z',
    updated_at: '2026-05-18T10:06:12Z',
    closed_at: null,
    cancelled_at: null,
    session_timeout_seconds: null,
    severity_rollup: 'completed',
    direct_revisits: ['TASK-0110', 'TASK-0111'],
  };

  test('renders forward lineage from direct_revisits field', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [TASK_WITH_REVISITS] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    await waitFor(() => {
      expect(screen.getByText(/Parent task with revisits/)).toBeInTheDocument();
    });
    // Should show the supersedes (←) lineage
    expect(screen.getByText(/TASK-0095/)).toBeInTheDocument();
    // Should show the forward revisits (→) with the first revisit ID
    expect(screen.getByText(/TASK-0110/)).toBeInTheDocument();
    // Should show the count indicator for multiple revisits (+1)
    expect(screen.getByText(/\+1/)).toBeInTheDocument();
  });

  test('shows no lineage when neither revisit fields are present', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [TASK] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    await waitFor(() => {
      expect(screen.getByText(/Draft Hong Kong visa guide/)).toBeInTheDocument();
    });
    // TASK has no revisit_of_task_id and no direct_revisits, so no lineage inline
    const options = screen.getAllByRole('option');
    expect(options.length).toBe(1);
    // Should not contain any TASK- references in the lineage position
    const briefCell = options[0].querySelector('span:nth-child(2)');
    expect(briefCell?.textContent).toContain('Draft Hong Kong visa guide');
  });
});

describe('TasksPage — keyboard nav agrees with render order', () => {
  test('under Agent grouping, ArrowDown highlights the same task that Enter opens', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    // Z-agent is inserted before A-agent (non-alphabetical insertion order)
    const Z_AGENT = {
      task_id: 'TASK-ZZZ', team: 'engineering', brief: 'Zebra task',
      status: 'pending', block_kind: null, parent_task_id: null,
      revisit_of_task_id: null, assigned_agent: 'Z-agent',
      created_at: '2026-05-18T10:00:00Z',
      updated_at: '2026-05-18T10:06:12Z', closed_at: null,
      cancelled_at: null, session_timeout_seconds: null,
      severity_rollup: 'pending',
    };
    const A_AGENT = {
      task_id: 'TASK-AAA', team: 'engineering', brief: 'Alpha task',
      status: 'in_progress', block_kind: null, parent_task_id: null,
      revisit_of_task_id: null, assigned_agent: 'A-agent',
      created_at: '2026-05-18T10:00:00Z',
      updated_at: '2026-05-18T10:06:12Z', closed_at: null,
      cancelled_at: null, session_timeout_seconds: null,
      severity_rollup: 'in_progress',
    };

    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [Z_AGENT, A_AGENT] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);

    // Wait for initial render with Status grouping
    await waitFor(() => {
      expect(screen.getByText(/Zebra task/)).toBeInTheDocument();
      expect(screen.getByText(/Alpha task/)).toBeInTheDocument();
    });

    // Switch to Agent grouping
    fireEvent.click(screen.getByText('Agent'));

    // After re-grouping, verify both tasks are still visible
    await waitFor(() => {
      expect(screen.getByText(/Zebra task/)).toBeInTheDocument();
    });

    // Get all option rows in render order
    const rows = screen.getAllByRole('option');
    expect(rows.length).toBe(2);

    // After the fix, rows must be in alphabetical order (A-agent before Z-agent)
    // If the bug is present, Z-agent (first in insertion order) renders first.
    // When rows are alphabetical, Keyboard ArrowDown to 0 highlights the first
    // rendered row, which is the same task that flatItems[0] tracks.
    expect(rows[0].getAttribute('data-task-id')).toBe(A_AGENT.task_id);
    expect(rows[1].getAttribute('data-task-id')).toBe(Z_AGENT.task_id);

    // ArrowDown to select first item
    const container = document.querySelector('[tabindex]');
    fireEvent.keyDown(container!, { key: 'ArrowDown' });
    expect(rows[0].getAttribute('aria-selected')).toBe('true');

    // ArrowDown again — second item highlighted
    fireEvent.keyDown(container!, { key: 'ArrowDown' });
    expect(rows[1].getAttribute('aria-selected')).toBe('true');

    // ArrowUp — back to first item
    fireEvent.keyDown(container!, { key: 'ArrowUp' });
    expect(rows[0].getAttribute('aria-selected')).toBe('true');
  });

  test('under Thread grouping, ArrowDown highlights the same task that Enter opens', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const Z_THREAD = {
      task_id: 'TASK-ZTH', team: 'engineering', brief: 'Zoo task',
      status: 'pending', block_kind: null, parent_task_id: null,
      revisit_of_task_id: null, dispatched_from_thread_id: 'THREAD-Z',
      created_at: '2026-05-18T10:00:00Z',
      updated_at: '2026-05-18T10:06:12Z', closed_at: null,
      cancelled_at: null, session_timeout_seconds: null,
      severity_rollup: 'pending',
    };
    const A_THREAD = {
      task_id: 'TASK-ATH', team: 'engineering', brief: 'Ant task',
      status: 'in_progress', block_kind: null, parent_task_id: null,
      revisit_of_task_id: null, dispatched_from_thread_id: 'THREAD-A',
      created_at: '2026-05-18T10:00:00Z',
      updated_at: '2026-05-18T10:06:12Z', closed_at: null,
      cancelled_at: null, session_timeout_seconds: null,
      severity_rollup: 'in_progress',
    };

    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [Z_THREAD, A_THREAD] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);

    await waitFor(() => {
      expect(screen.getByText(/Zoo task/)).toBeInTheDocument();
      expect(screen.getByText(/Ant task/)).toBeInTheDocument();
    });

    // Switch to Thread grouping
    fireEvent.click(screen.getByText('Thread'));

    await waitFor(() => {
      expect(screen.getByText(/Zoo task/)).toBeInTheDocument();
    });

    const rows = screen.getAllByRole('option');
    expect(rows.length).toBe(2);

    // After fix: alphabetical order — Thread A before Thread Z
    expect(rows[0].getAttribute('data-task-id')).toBe(A_THREAD.task_id);
    expect(rows[1].getAttribute('data-task-id')).toBe(Z_THREAD.task_id);

    const container = document.querySelector('[tabindex]');
    fireEvent.keyDown(container!, { key: 'ArrowDown' });
    expect(rows[0].getAttribute('aria-selected')).toBe('true');
  });
});

describe('TaskDetailPane — revisit chain timeline', () => {
  test('renders lineage chain when revisit_chain has multiple entries', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [TASK] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/${TASK.task_id}`, () =>
        HttpResponse.json({
          task: TASK,
          results: [],
          audit_log: [],
          revisit_chain: [TASK.task_id, 'TASK-0080', 'TASK-0075'],
          direct_revisits: [],
          predecessor_prior_status: null,
          blocked_on_jobs: null,
        }),
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
        HttpResponse.json({ jobs: [] }),
      ),
    );
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });
    // Should show Lineage section with chain nodes
    expect(await screen.findByText(/Lineage/i)).toBeInTheDocument();
    expect(screen.getByText('TASK-0075')).toBeInTheDocument();
    expect(screen.getByText('TASK-0080')).toBeInTheDocument();
    // TASK-0091 appears in both the drawer header (IdBadge) and the lineage chain
    const nodes = screen.getAllByText('TASK-0091');
    expect(nodes.length).toBeGreaterThanOrEqual(2);
  });
});

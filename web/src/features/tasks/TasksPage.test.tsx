import { screen, waitFor } from '@testing-library/react';
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
  test('renders the inbox with fixture tasks', async () => {
    sessionStorage.setItem('grassland.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks`, () =>
        HttpResponse.json({ tasks: [TASK] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    await waitFor(() =>
      expect(screen.getByText(/Draft Hong Kong visa guide/)).toBeInTheDocument(),
    );
  });

  test('renders filter sidebar groups', async () => {
    sessionStorage.setItem('grassland.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks`, () =>
        HttpResponse.json({ tasks: [TASK] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    await waitFor(() => {
      expect(screen.getByText(/Status/i)).toBeInTheDocument();
      expect(screen.getByText(/Team/i)).toBeInTheDocument();
    });
  });
});

describe('TaskDetailPane — jobs cross-link', () => {
  function stubHandlers(jobs: JobRecord[]) {
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks`, () =>
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
    sessionStorage.setItem('grassland.token', 'tok');
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
    sessionStorage.setItem('grassland.token', 'tok');
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
    revisit_chain: [],
    direct_revisits: [],
    predecessor_prior_status: null,
    blocked_on_jobs: null,
  };

  function stubHandlers(active_chain: ActiveChainResponse | null) {
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks`, () =>
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
    sessionStorage.setItem('grassland.token', 'tok');
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
    sessionStorage.setItem('grassland.token', 'tok');
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

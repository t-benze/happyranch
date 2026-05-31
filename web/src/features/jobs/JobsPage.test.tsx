import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import type { JobRecord } from '@/lib/api/types';

const SLUG = 'hk-macau-tourism';

function mountAt(route: string) {
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
  );
  return renderWithProviders(<AppRoutes />, { route });
}

const JOB: JobRecord = {
  id: 'JOB-0001',
  task_id: 'TASK-0042',
  agent_name: 'engineering_head',
  title: 'Clean up stale Docker images',
  rationale: 'Disk usage is above 90% on the build server.',
  script_text: 'docker image prune -af',
  interpreter: 'bash',
  cwd_hint: null,
  status: 'pending',
  exit_code: null,
  stdout_head: null,
  stderr_head: null,
  stdout_path: null,
  stderr_path: null,
  duration_ms: null,
  started_at: null,
  finished_at: null,
  reviewed_at: null,
  reviewed_by: null,
  reject_reason: null,
  cwd_resolved: null,
  max_runtime_seconds: 300,
  max_output_bytes: 52428800,
  review_required: true,
  persistent: false,
  reason: null,
  created_at: '2026-05-23T12:00:00Z',
};

describe('JobsPage — read path', () => {
  test('renders empty state when no jobs', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/jobs/`, () =>
        HttpResponse.json({ jobs: [] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/jobs`);
    await waitFor(() =>
      expect(screen.getByText(/No jobs/i)).toBeInTheDocument(),
    );
  });

  test('renders job cards when list returns data', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/jobs/`, () =>
        HttpResponse.json({ jobs: [JOB] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/jobs`);
    await waitFor(() =>
      expect(screen.getByText('Clean up stale Docker images')).toBeInTheDocument(),
    );
    expect(screen.getByText('JOB-0001')).toBeInTheDocument();
    expect(screen.getByText('engineering_head')).toBeInTheDocument();
  });

  test('renders filter sidebar with Status, Review, and Persistence groups', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/jobs/`, () =>
        HttpResponse.json({ jobs: [JOB] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/jobs`);
    await waitFor(() => {
      expect(screen.getByText(/^Status$/i)).toBeInTheDocument();
      expect(screen.getByText(/^Review$/i)).toBeInTheDocument();
      expect(screen.getByText(/^Persistence$/i)).toBeInTheDocument();
    });
  });
});

describe('JobDetailPane + RejectJobDialog — write path', () => {
  function stubDetailHandlers() {
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/jobs/`, () =>
        HttpResponse.json({ jobs: [JOB] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/jobs/JOB-0001`, () =>
        HttpResponse.json(JOB),
      ),
    );
  }

  test('detail drawer renders title, rationale, script, and action bar for pending job', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubDetailHandlers();
    mountAt(`/orgs/${SLUG}/jobs/JOB-0001`);
    // Title and rationale appear in both the card and the drawer — use getAllByText
    await waitFor(() =>
      expect(screen.getAllByText('Clean up stale Docker images').length).toBeGreaterThanOrEqual(1),
    );
    expect(screen.getAllByText(/Disk usage is above 90%/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('docker image prune -af')).toBeInTheDocument();
    // Action bar should be visible for pending job
    expect(screen.getByRole('button', { name: /Reject/i })).toBeInTheDocument();
  });

  test('RejectJobDialog submits reason and calls POST reject endpoint', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubDetailHandlers();
    let capturedBody: unknown = null;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/jobs/JOB-0001/reject`, async ({ request: req }) => {
        capturedBody = await req.json();
        return HttpResponse.json({ ...JOB, status: 'rejected', reject_reason: 'Too risky' });
      }),
    );

    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/jobs/JOB-0001`);

    // Click Reject to open dialog
    await user.click(await screen.findByRole('button', { name: /Reject/i }));

    // Dialog should open
    await waitFor(() =>
      expect(screen.getByRole('dialog')).toBeInTheDocument(),
    );

    // Type a reason
    await user.type(screen.getByPlaceholderText(/Reason \(required/i), 'Too risky');

    // Submit
    await user.click(screen.getByRole('button', { name: /^Reject$/ }));

    await waitFor(() => {
      expect(capturedBody).toEqual({ reason: 'Too risky' });
    });
  });

  test('reject button is disabled when reason is empty', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubDetailHandlers();

    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/jobs/JOB-0001`);

    await user.click(await screen.findByRole('button', { name: /Reject/i }));

    await waitFor(() =>
      expect(screen.getByRole('dialog')).toBeInTheDocument(),
    );

    // The Reject submit button should be disabled when reason is empty
    const submitBtn = screen.getByRole('button', { name: /^Reject$/ });
    expect(submitBtn).toBeDisabled();
  });

  test('detail drawer shows reject reason section for rejected job', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const rejectedJob: JobRecord = {
      ...JOB,
      status: 'rejected',
      reject_reason: 'Too risky to run',
    };
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/jobs/`, () =>
        HttpResponse.json({ jobs: [rejectedJob] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/jobs/JOB-0001`, () =>
        HttpResponse.json(rejectedJob),
      ),
    );
    mountAt(`/orgs/${SLUG}/jobs/JOB-0001`);
    await waitFor(() =>
      expect(screen.getByText('Too risky to run')).toBeInTheDocument(),
    );
    // Action bar should NOT be visible for rejected job
    expect(screen.queryByRole('button', { name: /Reject/i })).not.toBeInTheDocument();
  });
});

describe('JobDetailPane — Stop button (running job)', () => {
  function stubRunningHandlers(job: JobRecord) {
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/jobs/`, () =>
        HttpResponse.json({ jobs: [job] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/jobs/JOB-0001`, () =>
        HttpResponse.json(job),
      ),
      // OutputPanel seeds the live view by pulling /tail once on mount.
      http.get(`/api/v1/orgs/${SLUG}/jobs/JOB-0001/tail`, ({ request }) => {
        const url = new URL(request.url);
        const stream = url.searchParams.get('stream') ?? 'stdout';
        return HttpResponse.json({ stream, lines: [] });
      }),
    );
  }

  test('renders Stop button for running job and POSTs to /stop', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const runningJob: JobRecord = {
      ...JOB,
      status: 'running',
      started_at: '2026-05-23T12:01:00Z',
    };
    stubRunningHandlers(runningJob);
    let stopCalled = false;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/jobs/JOB-0001/stop`, () => {
        stopCalled = true;
        return HttpResponse.json({ ok: true, id: 'JOB-0001' });
      }),
    );

    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/jobs/JOB-0001`);
    const stopBtn = await screen.findByRole('button', { name: /^Stop$/ });
    await user.click(stopBtn);
    await waitFor(() => {
      expect(stopCalled).toBe(true);
    });
  });

  test('live drawer seeds output from /tail on mount', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const runningJob: JobRecord = {
      ...JOB,
      status: 'running',
      started_at: '2026-05-23T12:01:00Z',
    };
    stubRunningHandlers(runningJob);
    // Override the default empty /tail handler with one that returns prior lines.
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/jobs/JOB-0001/tail`, ({ request }) => {
        const url = new URL(request.url);
        const stream = url.searchParams.get('stream') ?? 'stdout';
        return HttpResponse.json({
          stream,
          lines:
            stream === 'stdout'
              ? ['boot line 1', 'boot line 2']
              : ['warning line'],
        });
      }),
    );

    mountAt(`/orgs/${SLUG}/jobs/JOB-0001`);
    await waitFor(() => {
      expect(screen.getByText('boot line 1')).toBeInTheDocument();
      expect(screen.getByText('boot line 2')).toBeInTheDocument();
      expect(screen.getByText('warning line')).toBeInTheDocument();
    });
  });

  test('no Stop button when job is pending', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubRunningHandlers(JOB);
    mountAt(`/orgs/${SLUG}/jobs/JOB-0001`);
    await waitFor(() =>
      expect(screen.getAllByText('Clean up stale Docker images').length).toBeGreaterThanOrEqual(1),
    );
    expect(screen.queryByRole('button', { name: /^Stop$/ })).not.toBeInTheDocument();
  });
});

describe('OutputPanel', () => {
  function stubDetailForStatus(job: JobRecord) {
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/jobs/`, () =>
        HttpResponse.json({ jobs: [job] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/jobs/JOB-0001`, () =>
        HttpResponse.json(job),
      ),
    );
  }

  test('renders no output section for pending job', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubDetailForStatus(JOB);
    mountAt(`/orgs/${SLUG}/jobs/JOB-0001`);
    // Wait for detail pane to load
    await waitFor(() =>
      expect(screen.getAllByText('Clean up stale Docker images').length).toBeGreaterThanOrEqual(1),
    );
    // OutputPanel returns null for pending — no "Output" heading
    expect(screen.queryByText(/^Output$/i)).not.toBeInTheDocument();
  });

  test('renders stdout and stderr pre blocks for completed job', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const completedJob: JobRecord = {
      ...JOB,
      status: 'completed',
      exit_code: 0,
      finished_at: '2026-05-23T12:05:00Z',
    };
    stubDetailForStatus(completedJob);
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/jobs/JOB-0001/output`, () =>
        HttpResponse.json({
          stdout: 'Deleted 3 images\n',
          stderr: '',
          truncated_stdout: false,
          truncated_stderr: false,
          total_stdout_bytes: 18,
          total_stderr_bytes: 0,
        }),
      ),
    );
    mountAt(`/orgs/${SLUG}/jobs/JOB-0001`);
    await waitFor(() =>
      expect(screen.getByText('Deleted 3 images')).toBeInTheDocument(),
    );
    // Both stdout and stderr headings should be present
    expect(screen.getByText(/^stdout$/i)).toBeInTheDocument();
    expect(screen.getByText(/^stderr$/i)).toBeInTheDocument();
    // Empty stderr renders as '(empty)'
    expect(screen.getByText('(empty)')).toBeInTheDocument();
  });

  test('renders no output section for rejected job', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const rejectedJob: JobRecord = {
      ...JOB,
      status: 'rejected',
      reject_reason: 'Not allowed',
    };
    stubDetailForStatus(rejectedJob);
    mountAt(`/orgs/${SLUG}/jobs/JOB-0001`);
    await waitFor(() =>
      expect(screen.getByText('Not allowed')).toBeInTheDocument(),
    );
    expect(screen.queryByText(/^Output$/i)).not.toBeInTheDocument();
  });
});

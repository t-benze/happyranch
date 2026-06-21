import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import type { JobRecord, TaskRecord } from '@/lib/api/types';

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

// ---------------------------------------------------------------------------
// JobsPage — index placeholder (Q6 retirement)
// ---------------------------------------------------------------------------

describe('JobsPage — index placeholder', () => {
  test('renders contextual guidance instead of a standalone job list', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    mountAt(`/orgs/${SLUG}/jobs`);
    await waitFor(() =>
      // "Jobs" appears on both the page heading and the top app-bar title.
      expect(screen.getAllByText('Jobs').length).toBeGreaterThan(0),
    );
    // Should mention Audit and Dashboard as the surfaces where jobs live
    // (getAllByText: sidebar also contains "Audit")
    expect(screen.getByText(/Jobs are reachable contextually/)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// JobDetailPage — standalone detail
// ---------------------------------------------------------------------------

function stubJobDetail(job: JobRecord = JOB, tasks: TaskRecord[] = []) {
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/jobs/${job.id}`, () =>
      HttpResponse.json(job),
    ),
    // "If approved" cascade: tasks blocked on this job
    http.get(`/api/v1/orgs/${SLUG}/tasks`, ({ request }) => {
      const url = new URL(request.url);
      if (url.searchParams.get('blocked_on_job_id') === job.id) {
        return HttpResponse.json({ tasks, next_cursor: null });
      }
      return HttpResponse.json({ tasks: [], next_cursor: null });
    }),
    // Output panel seed for running jobs
    http.get(`/api/v1/orgs/${SLUG}/jobs/${job.id}/tail`, ({ request }) => {
      const url = new URL(request.url);
      const stream = url.searchParams.get('stream') ?? 'stdout';
      return HttpResponse.json({ stream, lines: [] });
    }),
  );
}

describe('JobDetailPage — read path', () => {
  test('renders job header, title, verbatim command, and property rail', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubJobDetail();
    mountAt(`/orgs/${SLUG}/jobs/JOB-0001`);

    await waitFor(() => {
      expect(screen.getByText('JOB-0001')).toBeInTheDocument();
      expect(screen.getByText('Clean up stale Docker images')).toBeInTheDocument();
    });

    // Verbatim command
    expect(screen.getByText('docker image prune -af')).toBeInTheDocument();

    // Breadcrumb back to task
    expect(screen.getByText(/Back to TASK-0042/)).toBeInTheDocument();

    // Property rail items (stored fields only) — use getAllByText since agent_name
    // appears in both the header meta and the property rail
    expect(screen.getAllByText('engineering_head').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('bash')).toBeInTheDocument();
  });

  test('shows gated chip for review_required pending job', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubJobDetail(JOB);
    mountAt(`/orgs/${SLUG}/jobs/JOB-0001`);

    await waitFor(() => {
      expect(screen.getByText(/Needs your approval/)).toBeInTheDocument();
    });
    // Gated chip now shows separate Approve + Reject buttons
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reject' })).toBeInTheDocument();
  });

  test('gated chip shows BOTH Approve and Reject buttons', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubJobDetail(JOB);
    mountAt(`/orgs/${SLUG}/jobs/JOB-0001`);

    await waitFor(() => {
      expect(screen.getByText(/Needs your approval/)).toBeInTheDocument();
    });
    // Gated chip must show BOTH buttons
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reject' })).toBeInTheDocument();
  });

  test('gated chip Approve routes through the uniform two-step run confirm', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubJobDetail(JOB);
    // Mock the run endpoint so RunJobDialog can initialize
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/jobs/JOB-0001/run`, () =>
        HttpResponse.json({ id: 'JOB-0001', status: 'running', started_at: '2026-05-23T12:01:00Z', cwd_resolved: '/x', timeout_seconds: 300, events_url: '' }),
      ),
    );

    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/jobs/JOB-0001`);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument();
    });

    // Click Approve → enters the SAME run two-step confirm
    await user.click(screen.getByRole('button', { name: 'Approve' }));

    // Step 1: run prompt (same as non-gated Run path)
    await waitFor(() => {
      expect(screen.getByText(/Run this script/)).toBeInTheDocument();
    });

    // Click Run… → step 2
    await user.click(screen.getByRole('button', { name: 'Run…' }));

    await waitFor(() => {
      expect(screen.getByText(/Confirm: are you sure you want to run/)).toBeInTheDocument();
    });

    // Confirm → opens RunJobDialog
    await user.click(screen.getByRole('button', { name: 'Confirm run' }));

    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });
  });

  test('gated chip Reject opens RejectJobDialog', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubJobDetail(JOB);
    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/jobs/JOB-0001`);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Reject' })).toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: 'Reject' }));

    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });
    expect(screen.getByText(/Reject JOB-0001/)).toBeInTheDocument();
  });

  test('shows "if approved" cascade with blocked tasks', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const blockedTask = {
      id: 'TASK-0099',
      status: 'blocked',
      block_kind: 'escalated',
      blocked_on_job_ids: '["JOB-0001"]',
      assigned_agent: 'dev_agent',
      team: 'engineering',
      brief: 'Deploy the hotfix to production',
      revision_count: 0,
      created_at: '2026-05-23T11:00:00Z',
      updated_at: '2026-05-23T11:00:00Z',
      completed_at: null,
      parent_task_id: null,
      revisit_of_task_id: null,
      dispatched_from_thread_id: null,
      note: null,
      orchestration_step_count: 0,
      final_output_dir: null,
      cancelled_at: null,
      last_heartbeat: null,
      session_timeout_seconds: null,
      task_type: 'task',
      task_id: 'TASK-0099',
      closed_at: null,
    } as TaskRecord;
    stubJobDetail(JOB, [blockedTask]);
    mountAt(`/orgs/${SLUG}/jobs/JOB-0001`);

    await waitFor(() => {
      expect(screen.getByText(/If approved/)).toBeInTheDocument();
      expect(screen.getByText(/TASK-0099/)).toBeInTheDocument();
      expect(screen.getByText(/Deploy the hotfix/)).toBeInTheDocument();
    });
  });

  test('shows calm empty cascade when no tasks blocked', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubJobDetail(JOB, []);
    mountAt(`/orgs/${SLUG}/jobs/JOB-0001`);

    await waitFor(() => {
      expect(screen.getByText(/No tasks are currently blocked/)).toBeInTheDocument();
    });
  });

  test('shows reject reason for rejected job', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const rejectedJob: JobRecord = {
      ...JOB,
      status: 'rejected',
      reject_reason: 'Too risky to run',
    };
    stubJobDetail(rejectedJob);
    mountAt(`/orgs/${SLUG}/jobs/JOB-0001`);

    await waitFor(() => {
      expect(screen.getByText('Too risky to run')).toBeInTheDocument();
    });
    // No action buttons for rejected job
    expect(screen.queryByRole('button', { name: /Run|Reject|Approve/ })).not.toBeInTheDocument();
  });

  test('shows failure reason for failed job', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const failedJob: JobRecord = {
      ...JOB,
      status: 'failed',
      reason: 'exit code 1',
      exit_code: 1,
      finished_at: '2026-05-23T12:05:00Z',
      duration_ms: 5000,
    };
    stubJobDetail(failedJob);
    mountAt(`/orgs/${SLUG}/jobs/JOB-0001`);

    await waitFor(() => {
      expect(screen.getByText(/Failure reason/)).toBeInTheDocument();
      expect(screen.getByText('exit code 1')).toBeInTheDocument();
    });
  });

  test('shows loading state', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/jobs/JOB-0001`, () =>
        new Promise(() => {}), // never resolves
      ),
    );
    mountAt(`/orgs/${SLUG}/jobs/JOB-0001`);
    await waitFor(() => {
      expect(screen.getByText(/Loading JOB-0001/)).toBeInTheDocument();
    });
  });

  test('shows error state with retry', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/jobs/JOB-0001`, () =>
        HttpResponse.json({ error: 'boom' }, { status: 500 }),
      ),
    );
    mountAt(`/orgs/${SLUG}/jobs/JOB-0001`);
    await waitFor(() => {
      expect(screen.getByText(/Failed to load/)).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// JobDetailPage — actions
// ---------------------------------------------------------------------------

describe('JobDetailPage — reject dialog', () => {
  test('RejectJobDialog submits reason and calls POST reject endpoint', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubJobDetail({ ...JOB, review_required: false }); // non-gated → direct Run/Reject buttons

    let capturedBody: unknown = null;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/jobs/JOB-0001/reject`, async ({ request: req }) => {
        capturedBody = await req.json();
        return HttpResponse.json({ ...JOB, status: 'rejected', reject_reason: 'Too risky' });
      }),
    );

    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/jobs/JOB-0001`);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Reject' })).toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: 'Reject' }));

    await waitFor(() =>
      expect(screen.getByRole('dialog')).toBeInTheDocument(),
    );

    await user.type(screen.getByPlaceholderText(/Reason \(required/), 'Too risky');
    await user.click(screen.getByRole('button', { name: /^Reject$/ }));

    await waitFor(() => {
      expect(capturedBody).toEqual({ reason: 'Too risky' });
    });
  });
});

describe('JobDetailPage — run dialog', () => {
  test('RunJobDialog opens for non-gated pending job', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const nonGated: JobRecord = { ...JOB, review_required: false };
    stubJobDetail(nonGated);

    server.use(
      http.post(`/api/v1/orgs/${SLUG}/jobs/JOB-0001/run`, async () => {
        return HttpResponse.json({ id: 'JOB-0001', status: 'running', started_at: '2026-05-23T12:01:00Z', cwd_resolved: '/x', timeout_seconds: 300, events_url: '' });
      }),
    );

    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/jobs/JOB-0001`);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Run' })).toBeInTheDocument();
    });

    // Click Run → two-step confirm
    await user.click(screen.getByRole('button', { name: 'Run' }));

    await waitFor(() => {
      expect(screen.getByText(/Run this script/)).toBeInTheDocument();
    });

    // Step 2
    await user.click(screen.getByRole('button', { name: 'Run…' }));
    await waitFor(() => {
      expect(screen.getByText(/Confirm: are you sure/)).toBeInTheDocument();
    });

    // Confirm → dialog opens
    await user.click(screen.getByRole('button', { name: 'Confirm run' }));

    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });
  });
});

describe('JobDetailPage — stop (running job)', () => {
  test('renders Stop button for running job and POSTs to /stop', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const runningJob: JobRecord = {
      ...JOB,
      status: 'running',
      started_at: '2026-05-23T12:01:00Z',
    };
    stubJobDetail(runningJob);

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
});

describe('JobDetailPage — output', () => {
  test('renders stdout and stderr for completed job', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const completedJob: JobRecord = {
      ...JOB,
      status: 'completed',
      exit_code: 0,
      finished_at: '2026-05-23T12:05:00Z',
      duration_ms: 5000,
    };
    stubJobDetail(completedJob);
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
    expect(screen.getByText(/^stdout$/i)).toBeInTheDocument();
  });

  test('hides output section for pending job', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubJobDetail(JOB);
    mountAt(`/orgs/${SLUG}/jobs/JOB-0001`);
    await waitFor(() =>
      expect(screen.getByText('Clean up stale Docker images')).toBeInTheDocument(),
    );
    // OutputPanel returns null for pending
    expect(screen.queryByText(/^Output$/i)).not.toBeInTheDocument();
  });

  test('shows property rail with stored fields for completed job', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const completedJob: JobRecord = {
      ...JOB,
      status: 'completed',
      exit_code: 0,
      finished_at: '2026-05-23T12:05:00Z',
      duration_ms: 5000,
      reviewed_by: 'founder',
      reviewed_at: '2026-05-23T12:00:01Z',
    };
    stubJobDetail(completedJob);
    mountAt(`/orgs/${SLUG}/jobs/JOB-0001`);
    await waitFor(() => {
      expect(screen.getByText('Exit code')).toBeInTheDocument();
      expect(screen.getByText('0')).toBeInTheDocument();
      expect(screen.getByText('Duration')).toBeInTheDocument();
      expect(screen.getByText('5.0s')).toBeInTheDocument();
      expect(screen.getByText('Reviewed by')).toBeInTheDocument();
      expect(screen.getByText('founder')).toBeInTheDocument();
    });
  });
});

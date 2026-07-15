import { screen, waitFor, within } from '@testing-library/react';
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
// JobsPage — approval-queue LIST surface (TASK-907 reinstatement)
// ---------------------------------------------------------------------------

function makeJob(overrides: Partial<JobRecord>): JobRecord {
  return { ...JOB, ...overrides };
}

// A spread across all five JobStatus values; exactly one pending.
const LIST_JOBS: JobRecord[] = [
  makeJob({
    id: 'JOB-0005',
    status: 'pending',
    review_required: true,
    script_text: 'gh release create v0.41 --notes-file RELEASE_NOTES.md ./dist/*',
    agent_name: 'senior_dev',
    task_id: 'TASK-0548',
    cwd_resolved: '/srv/grassland',
  }),
  makeJob({
    id: 'JOB-0002',
    status: 'running',
    review_required: true,
    script_text: 'data-pipeline upload-photos --type guides --target oss',
    started_at: '2026-06-26T12:00:00Z',
  }),
  makeJob({
    id: 'JOB-0001',
    status: 'completed',
    review_required: false,
    exit_code: 0,
    script_text: 'data-pipeline publish --stack local --type guides --all',
  }),
  makeJob({
    id: 'JOB-0009',
    status: 'failed',
    review_required: true,
    script_text: 'npm run build',
  }),
  makeJob({
    id: 'JOB-0000',
    status: 'rejected',
    review_required: true,
    script_text: "psql $DATABASE_URL -c 'TRUNCATE guides CASCADE;'",
  }),
];

/** Stub GET /jobs/ — branches on the `status` query param (list asks for
 *  status=all; the Sidebar badge asks for status=pending). */
function stubJobsList(jobs: JobRecord[] = LIST_JOBS) {
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/jobs/`, ({ request }) => {
      const status = new URL(request.url).searchParams.get('status');
      const out =
        status && status !== 'all'
          ? jobs.filter((j) => j.status === status)
          : jobs;
      return HttpResponse.json({ jobs: out });
    }),
  );
}

describe('JobsPage — approval-queue list', () => {
  test('renders command heroes, status pills, and the "waiting on you" header', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubJobsList();
    mountAt(`/orgs/${SLUG}/jobs`);

    // Verbatim command is the hero of the row.
    await waitFor(() =>
      expect(
        screen.getByText('data-pipeline publish --stack local --type guides --all'),
      ).toBeInTheDocument(),
    );

    // N waiting on you = pending count (exactly one pending in the fixture).
    expect(screen.getByText(/1 waiting on you/)).toBeInTheDocument();

    // Status pills render for non-pending statuses StatusBadge can't express.
    // (`running` pill is distinct from the `running…` static state; `rejected`
    // appears as both the pill and the right-side static label, hence getAll.)
    expect(screen.getByText('running')).toBeInTheDocument();
    expect(screen.getAllByText('rejected').length).toBeGreaterThanOrEqual(1);
    // Completed row shows its real exit code.
    expect(screen.getByText('exit 0')).toBeInTheDocument();
  });

  test('outcome chips read the shared semantic-tone vocabulary (exit 0 green-filled, non-zero exit red-filled)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubJobsList([
      makeJob({ id: 'JOB-OK', status: 'completed', exit_code: 0, script_text: 'ok-cmd' }),
      makeJob({ id: 'JOB-BAD', status: 'failed', exit_code: 1, script_text: 'bad-cmd' }),
    ]);
    mountAt(`/orgs/${SLUG}/jobs`);

    // exit 0 → positive tone → green tinted FILL (not a bare outline).
    const ok = await screen.findByText('exit 0');
    expect(ok).toHaveClass('bg-tier-green-tint');
    // non-zero exit → danger tone → red tinted fill, and the row shows the
    // real exit code (THR-099 Batch 2: outcome chips converge on semanticTone).
    const bad = await screen.findByText('exit 1');
    expect(bad).toHaveClass('bg-tier-red-tint');
  });

  test('groups jobs under status-section headers, each with a per-status count', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubJobsList();
    mountAt(`/orgs/${SLUG}/jobs`);

    // The fixture spreads one job across each of the five lifecycle states, so
    // every status renders as a labelled section (an <section aria-label> →
    // implicit ARIA "region" landmark), in founder-blocking-first order.
    const pending = await screen.findByRole('region', { name: 'Pending' });
    // The header carries the group count (exactly one pending in the fixture).
    expect(within(pending).getByText('1')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Running' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Completed' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Failed' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Rejected' })).toBeInTheDocument();
  });

  test('status filter narrows the list to the chosen lifecycle state', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubJobsList();
    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/jobs`);

    await waitFor(() =>
      expect(screen.getByText('npm run build')).toBeInTheDocument(),
    );

    // Click the "Completed" status filter → only the completed command remains.
    await user.click(screen.getByRole('button', { name: /Completed/ }));

    await waitFor(() =>
      expect(screen.queryByText('npm run build')).not.toBeInTheDocument(),
    );
    expect(
      screen.getByText('data-pipeline publish --stack local --type guides --all'),
    ).toBeInTheDocument();
  });

  test('a job card links to the existing detail route', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubJobsList();
    mountAt(`/orgs/${SLUG}/jobs`);

    const link = await screen.findByRole('link', {
      name: /data-pipeline publish --stack local --type guides --all/,
    });
    expect(link).toHaveAttribute('href', `/orgs/${SLUG}/jobs/JOB-0001`);
  });

  test('shows the queue-clear state when nothing is pending', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubJobsList(LIST_JOBS.filter((j) => j.status !== 'pending'));
    mountAt(`/orgs/${SLUG}/jobs`);

    await waitFor(() =>
      expect(screen.getByText(/Queue clear · nothing waiting on you/)).toBeInTheDocument(),
    );
  });

  test('Sidebar Jobs nav item renders without a count badge (THR-046)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubJobsList();
    mountAt(`/orgs/${SLUG}/jobs`);

    const jobsNav = await screen.findByRole('link', { name: 'Jobs' });
    expect(within(jobsNav).queryByTestId('nav-count-badge')).toBeNull();
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
    // Interpreter now appears in both the property rail and the command-card
    // terminal footer (JOBDET-02), so scope this stored-field check to the rail.
    expect(within(screen.getByRole('complementary')).getByText('bash')).toBeInTheDocument();
  });

  // TASK-928 regression: the AppShell <main> is `overflow-hidden` and delegates
  // scrolling to each page. JobDetailPage's normal render was the lone full page
  // missing an internal `h-full overflow-y-auto` scroll wrapper, so a long brief
  // clipped and the two-step confirm control rendered below the clip, unreachable
  // — making "Approve job" appear to do nothing. Assert the page owns the wrapper.
  test('TASK-928: normal render is wrapped in an h-full overflow-y-auto scroll container', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubJobDetail();
    mountAt(`/orgs/${SLUG}/jobs/JOB-0001`);

    await waitFor(() => {
      expect(screen.getByText('Clean up stale Docker images')).toBeInTheDocument();
    });

    // The page content's nearest scroll-container ancestor must scroll internally
    // (h-full + overflow-y-auto) so long content is reachable inside the clipped
    // shared <main>. Walking up from the title lands on the page's own wrapper.
    const scroller = screen.getByText('Clean up stale Docker images').closest('.overflow-y-auto');
    expect(scroller).not.toBeNull();
    expect(scroller).toHaveClass('h-full', 'overflow-y-auto');
    // …and it wraps the constrained content column, confirming it is the page-level
    // wrapper rather than some inner panel's own scroll area. THR-099 Slice 1
    // widened this cap from max-w-5xl → the shared <ContentWrap> (max-w-content,
    // 1180px) — the constrained column still exists, just at the Direction-A cap.
    expect(scroller!.querySelector('.mx-auto.max-w-content')).not.toBeNull();
  });

  // JOBDET-02: the command card is styled with terminal chrome — a "›_ command"
  // header bar plus an interpreter/cwd footer — rather than a plain
  // "COMMAND (bash · cwd: …)" label above a bare command box.
  test('JOBDET-02: command card has a terminal header and an interpreter/cwd footer', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubJobDetail({ ...JOB, cwd_hint: '/srv/build' });
    mountAt(`/orgs/${SLUG}/jobs/JOB-0001`);

    await waitFor(() => {
      expect(screen.getByText('docker image prune -af')).toBeInTheDocument();
    });

    // Terminal-chrome header bar with the "›_ command" prompt glyph.
    expect(screen.getByText('›_')).toBeInTheDocument();
    expect(screen.getByText('command')).toBeInTheDocument();

    // Interpreter/cwd footer rendered from existing job fields.
    expect(screen.getByText('bash · cwd: /srv/build')).toBeInTheDocument();
  });

  // JOBDET-01: job metadata renders as a right-rail card styled per the
  // a-job-detail reference — agent identities via the AgentChip avatar idiom
  // (role-colored dot) and entity-link styling for the Task id. Existing
  // stored fields (e.g. interpreter) are preserved inside the same card.
  test('JOBDET-01: metadata is a right-rail card with an agent avatar and a Task entity link', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const reviewedJob: JobRecord = {
      ...JOB,
      status: 'completed',
      review_required: false,
      reviewed_by: 'founder',
      reviewed_at: '2026-05-23T13:00:00Z',
      exit_code: 0,
      duration_ms: 4200,
    };
    stubJobDetail(reviewedJob);
    mountAt(`/orgs/${SLUG}/jobs/JOB-0001`);

    await waitFor(() => {
      expect(screen.getByText('Clean up stale Docker images')).toBeInTheDocument();
    });

    // The metadata lives in a dedicated right-rail card (an <aside> →
    // implicit ARIA "complementary" landmark), not the old inline grid.
    const rail = screen.getByRole('complementary');

    // Requested-by agent identity uses the AgentChip avatar idiom
    // (role-colored dot), not plain text.
    expect(within(rail).getByText('engineering_head')).toBeInTheDocument();
    expect(rail.querySelector('.bg-agent-worker')).not.toBeNull();
    // Reviewed-by founder identity also renders via AgentChip (founder dot).
    expect(within(rail).getByText('founder')).toBeInTheDocument();
    expect(rail.querySelector('.bg-agent-founder')).not.toBeNull();

    // The Task id is an entity link to the task-detail route.
    const taskLink = within(rail).getByRole('link', { name: /TASK-0042/ });
    expect(taskLink).toHaveAttribute('href', `/orgs/${SLUG}/tasks/TASK-0042`);

    // Preserved stored field (no data loss): interpreter still shown.
    expect(within(rail).getByText('bash')).toBeInTheDocument();
  });

  test('shows gated notice for review_required pending job', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubJobDetail(JOB);
    mountAt(`/orgs/${SLUG}/jobs/JOB-0001`);

    await waitFor(() => {
      expect(screen.getByText(/This action is gated/)).toBeInTheDocument();
    });
    // Direction-A: controls are hoisted to the top-right header — a "Reject"
    // secondary action and a single "Approve & run" primary action (the founder
    // /run IS the approve+run; there is no separate approve step).
    expect(screen.getByRole('button', { name: 'Approve & run' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reject' })).toBeInTheDocument();
  });

  test('gated job shows BOTH the Reject and Approve & run header buttons', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubJobDetail(JOB);
    mountAt(`/orgs/${SLUG}/jobs/JOB-0001`);

    await waitFor(() => {
      expect(screen.getByText(/This action is gated/)).toBeInTheDocument();
    });
    // Header must show BOTH actions
    expect(screen.getByRole('button', { name: 'Approve & run' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reject' })).toBeInTheDocument();
  });

  test('gated "Approve & run" opens the single confirm and fires exactly ONE run mutation', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubJobDetail(JOB);
    // Count the founder /run calls — the single confirm must fire exactly one,
    // and the founder /run IS the approve+run (no separate approve endpoint).
    let runCalls = 0;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/jobs/JOB-0001/run`, () => {
        runCalls += 1;
        return HttpResponse.json({ id: 'JOB-0001', status: 'running', started_at: '2026-05-23T12:01:00Z', cwd_resolved: '/x', timeout_seconds: 300, events_url: '' });
      }),
    );

    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/jobs/JOB-0001`);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Approve & run' })).toBeInTheDocument();
    });

    // ONE button → ONE popup confirm (no intermediate two-step card).
    await user.click(screen.getByRole('button', { name: 'Approve & run' }));

    const dialog = await screen.findByRole('dialog');
    // The popup shows the VERBATIM command and is titled for the gated approve+run.
    expect(within(dialog).getByText('docker image prune -af')).toBeInTheDocument();
    expect(within(dialog).getByText(/Approve & run JOB-0001/)).toBeInTheDocument();

    // Confirm inside the popup → exactly one /run call (no double-submit).
    await user.click(within(dialog).getByRole('button', { name: 'Approve & run' }));

    await waitFor(() => {
      expect(runCalls).toBe(1);
    });
    // Give any erroneous second submission a chance to land, then re-assert.
    expect(runCalls).toBe(1);
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
      // Path B (THR-037 Change B): a task waiting on a job is in_progress with
      // block_kind=blocked_on_job (was blocked/escalated).
      status: 'in_progress',
      block_kind: 'blocked_on_job',
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
  test('non-gated "Run" opens the single confirm directly (no two-step)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const nonGated: JobRecord = { ...JOB, review_required: false };
    stubJobDetail(nonGated);

    let runCalls = 0;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/jobs/JOB-0001/run`, async () => {
        runCalls += 1;
        return HttpResponse.json({ id: 'JOB-0001', status: 'running', started_at: '2026-05-23T12:01:00Z', cwd_resolved: '/x', timeout_seconds: 300, events_url: '' });
      }),
    );

    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/jobs/JOB-0001`);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Run' })).toBeInTheDocument();
    });

    // ONE button → the popup confirm opens directly (no intermediate two-step).
    await user.click(screen.getByRole('button', { name: 'Run' }));

    const dialog = await screen.findByRole('dialog');
    // Non-gated popup shows the verbatim command and the plain "Run" title.
    expect(within(dialog).getByText('docker image prune -af')).toBeInTheDocument();
    expect(within(dialog).getByText(/Run JOB-0001/)).toBeInTheDocument();

    // Confirm inside the popup → exactly one /run call (no double-submit).
    await user.click(within(dialog).getByRole('button', { name: 'Run' }));
    await waitFor(() => {
      expect(runCalls).toBe(1);
    });
    expect(runCalls).toBe(1);
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

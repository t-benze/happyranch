import { screen, waitFor, within } from '@testing-library/react';
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
      expect(
        screen.getByRole('heading', { name: 'What the org is working on' }),
      ).toBeInTheDocument();
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

  // TASKS-04: group-by control is a segmented control (not plain text tabs).
  test('renders the group-by control as a bordered segmented control (TASKS-04)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [TASK] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    const tablist = await screen.findByRole('tablist', { name: 'Group by' });
    // Segmented = a grouped, bordered, rounded container — not plain text tabs.
    expect(tablist).toHaveClass('rounded-lg');
    expect(tablist).toHaveClass('border');
    // The active segment ('Status', the default) carries the accent fill.
    expect(screen.getByRole('tab', { name: 'Status' })).toHaveClass(
      'data-[state=active]:bg-accent-soft',
    );
  });

  // TASKS-04: group headers carry a count badge + a colored status dot, both
  // pure client-side derivations of the already-loaded roots payload.
  test('group headers carry a count badge and a colored status dot (TASKS-04)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const a = rootTask({
      task_id: 'TASK-0400',
      status: 'in_progress',
      severity_rollup: 'in_progress',
      brief: 'First running root',
    });
    const b = rootTask({
      task_id: 'TASK-0401',
      status: 'in_progress',
      severity_rollup: 'in_progress',
      brief: 'Second running root',
    });
    const c = rootTask({
      task_id: 'TASK-0402',
      status: 'pending',
      severity_rollup: 'pending',
      brief: 'Awaiting pickup',
    });
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [a, b, c] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    const inProgress = await screen.findByRole('heading', {
      name: /In progress/,
    });
    // Count badge reflects the client-side group size (2 in_progress roots).
    expect(within(inProgress).getByText('2')).toBeInTheDocument();
    // Colored status dot uses the green 'open' token for in_progress.
    const dot = inProgress.querySelector('span[aria-hidden="true"]');
    expect(dot).not.toBeNull();
    expect(dot).toHaveClass('text-status-open');
    // The pending group shows a count of 1.
    const pending = screen.getByRole('heading', { name: /Pending/ });
    expect(within(pending).getByText('1')).toBeInTheDocument();
  });

  test('renders severity_rollup badge in TaskCard (worst subtree status)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    // Root is pending but has an escalated child → severity_rollup = 'escalated'
    // (Path B: escalated is the worst rollup severity).
    const taskWithRollup = rootTask({
      task_id: 'TASK-0100',
      status: 'pending',
      severity_rollup: 'escalated',
      brief: 'Root task that has a stuck child',
    });
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [taskWithRollup] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    await waitFor(() => {
      // The badge should show 'escalated' from severity_rollup, not 'pending'
      expect(screen.getByText('escalated')).toBeInTheDocument();
      expect(screen.getByText(/Root task that has a stuck child/)).toBeInTheDocument();
    });
  });

  // TASKS-05: root rows surface the worst-child rollup inline when a descendant
  // sits in a strictly-worse state than the root itself. Pure client-side
  // derivation of severity_rollup vs the root's own status; count-free (the
  // count-decorated design form "1 of 2 subtasks blocked" needs per-status
  // subtask counts that the roots payload does not carry — deferred).
  test('surfaces worst-child subtask rollup inline on root rows (TASKS-05)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    // Root is in_progress but a descendant is escalated → severity_rollup='escalated'.
    const worseChild = rootTask({
      task_id: 'TASK-0500',
      status: 'in_progress',
      severity_rollup: 'escalated',
      brief: 'Root in progress with a stuck child',
    });
    // Root with no worse descendant (rollup === own status) → no inline rollup.
    const noWorseChild = rootTask({
      task_id: 'TASK-0501',
      status: 'in_progress',
      severity_rollup: 'in_progress',
      brief: 'Root in progress all subtasks fine',
    });
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [worseChild, noWorseChild] }),
      ),
    );
    mountAt(`/orgs/${SLUG}/tasks`);
    // The worse-child root names the worst descendant status inline, colored
    // with the escalated token.
    const rollup = await screen.findByText('subtask escalated');
    expect(rollup).toHaveClass('text-status-escalated');
    // The healthy root surfaces no inline rollup (no fabricated subtask state).
    expect(screen.queryByText('subtask in progress')).not.toBeInTheDocument();
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
      // THR-0030 appears as the group heading AND as the row's thread chip,
      // so multiple matches are expected; plus a "No thread" group heading.
      expect(screen.getAllByText('THR-0030').length).toBeGreaterThan(0);
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

// THR-037 Change B Phase 2: the status-GROUP header maps must speak the Path-B
// vocabulary. `escalated` is a first-class attention group (red dot, surfaced
// early); `cancelled` is a calm terminal group (muted dot, dimmed/terminal set);
// `blocked` is fully retired from this presentation surface.
describe('TasksPage — Path-B status group vocabulary (THR-037 Change B Phase 2)', () => {
  function mountStatuses(tasks: TaskRecord[]) {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks }),
      ),
    );
    return mountAt(`/orgs/${SLUG}/tasks`);
  }

  test('escalated group renders the red attention dot + a proper label and sorts early', async () => {
    const running = rootTask({
      task_id: 'TASK-0600',
      status: 'in_progress',
      severity_rollup: 'in_progress',
      brief: 'A healthy running root',
    });
    const escalated = rootTask({
      task_id: 'TASK-0601',
      status: 'escalated',
      severity_rollup: 'escalated',
      brief: 'A root escalated to the founder',
    });
    mountStatuses([running, escalated]);

    // Proper display label (not a raw-lowercase fallback).
    const escalatedHeading = await screen.findByRole('heading', {
      name: /Escalated/,
    });
    // Red attention dot — the SAME token StatusBadge uses for escalated.
    const dot = escalatedHeading.querySelector('span[aria-hidden="true"]');
    expect(dot).not.toBeNull();
    expect(dot).toHaveClass('text-status-escalated');

    // Sorts EARLY: the escalated attention group precedes the in_progress group
    // in document order (first-class attention, surfaced near the top).
    const inProgressHeading = screen.getByRole('heading', { name: /In progress/ });
    expect(
      escalatedHeading.compareDocumentPosition(inProgressHeading) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    // Escalated is an ATTENTION state, NOT dimmed/terminal.
    expect(escalatedHeading.closest('section')).not.toHaveClass('opacity-60');
  });

  test('cancelled group renders the muted/terminal treatment and is in the dimmed set', async () => {
    const cancelled = rootTask({
      task_id: 'TASK-0602',
      status: 'cancelled',
      severity_rollup: 'cancelled',
      brief: 'A cancelled root',
    });
    mountStatuses([cancelled]);

    const cancelledHeading = await screen.findByRole('heading', {
      name: /Cancelled/,
    });
    // Muted/terminal dot — the SAME token StatusBadge uses for cancelled
    // (mirrors resolved_superseded).
    const dot = cancelledHeading.querySelector('span[aria-hidden="true"]');
    expect(dot).not.toBeNull();
    expect(dot).toHaveClass('text-status-archived');

    // Cancelled sits in the terminal/dimmed set (calmer than completed).
    expect(cancelledHeading.closest('section')).toHaveClass('opacity-60');
  });

  test('no `blocked` group label or dot path remains on this surface', async () => {
    // Render the full Path-B vocabulary; no surface should fall back to the
    // retired `blocked` label or its dot token.
    const tasks = [
      rootTask({ task_id: 'TASK-0610', status: 'in_progress', severity_rollup: 'in_progress' }),
      rootTask({ task_id: 'TASK-0611', status: 'escalated', severity_rollup: 'escalated' }),
      rootTask({ task_id: 'TASK-0612', status: 'cancelled', severity_rollup: 'cancelled' }),
      rootTask({ task_id: 'TASK-0613', status: 'completed', severity_rollup: 'completed' }),
    ];
    mountStatuses(tasks);

    await screen.findByRole('heading', { name: /In progress/ });
    // No retired `blocked` group heading.
    expect(screen.queryByRole('heading', { name: /Blocked/ })).toBeNull();
    // No retired blocked dot token anywhere in the rendered surface.
    expect(document.querySelector('.text-status-blocked')).toBeNull();
  });
});

describe('TasksPage — Direction-A list reshape (THR-030 TASKS-01/02/03)', () => {
  function mountTasks(tasks: TaskRecord[]) {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks }),
      ),
    );
    return mountAt(`/orgs/${SLUG}/tasks`);
  }

  // TASKS-03: page eyebrow (derived from loaded list data) + serif title.
  test('renders serif title and a derived eyebrow with root/waiting/failed counts', async () => {
    const running = rootTask({
      task_id: 'TASK-0400',
      status: 'in_progress',
      severity_rollup: 'in_progress',
    });
    const escalated = rootTask({
      task_id: 'TASK-0401',
      status: 'escalated',
      severity_rollup: 'escalated',
    });
    const failed = rootTask({
      task_id: 'TASK-0402',
      status: 'failed',
      severity_rollup: 'failed',
    });
    mountTasks([running, escalated, failed]);

    // Serif title replaces the bare "Tasks" heading.
    expect(
      await screen.findByRole('heading', { name: 'What the org is working on' }),
    ).toBeInTheDocument();

    // Eyebrow derives from loaded list data: 3 roots · 1 waiting on you
    // (escalated) · 1 failed (rollup). Wait for the roots query to populate
    // (the static header renders before the fetch resolves).
    await waitFor(() =>
      expect(screen.getByText(/ROOT TASKS/)).toHaveTextContent('3 ROOT TASKS'),
    );
    const eyebrow = screen.getByText(/ROOT TASKS/);
    expect(eyebrow).toHaveTextContent('SUBTASKS ROLL UP');
    expect(eyebrow).toHaveTextContent('1 WAITING ON YOU');
    expect(eyebrow).toHaveTextContent('1 FAILED');
  });

  // TASKS-01: column header row aligned above the rows.
  test('renders the TASK · TITLE · AGENT · THREAD · UPDATED column header row', async () => {
    mountTasks([
      rootTask({ task_id: 'TASK-0410', status: 'in_progress', severity_rollup: 'in_progress' }),
    ]);
    await waitFor(() => {
      expect(screen.getByText('TASK')).toBeInTheDocument();
    });
    expect(screen.getByText('TITLE')).toBeInTheDocument();
    expect(screen.getByText('AGENT')).toBeInTheDocument();
    expect(screen.getByText('THREAD')).toBeInTheDocument();
    expect(screen.getByText('UPDATED')).toBeInTheDocument();
  });

  // TASKS-02: agent rendered as AgentChip (avatar idiom), thread as a chip,
  // row click-through preserved to the detail route.
  test('renders agent as an AgentChip avatar and thread as an inline chip', async () => {
    mountTasks([
      rootTask({
        task_id: 'TASK-0420',
        assigned_agent: 'dev_agent',
        dispatched_from_thread_id: 'THR-0030',
        status: 'in_progress',
        severity_rollup: 'in_progress',
        brief: 'Reshape the tasks list rows',
      }),
    ]);
    await waitFor(() => {
      expect(screen.getByText('dev_agent')).toBeInTheDocument();
    });
    // Agent is the AgentChip idiom (role-colored dot), not plain text.
    expect(document.querySelector('.bg-agent-worker')).not.toBeNull();
    // Thread reference renders as an inline (tinted) chip.
    expect(screen.getByText('THR-0030')).toBeInTheDocument();
    // Row click-through to the detail route is preserved.
    const rowLink = screen.getByRole('link', { name: /Reshape the tasks list rows/ });
    expect(rowLink).toHaveAttribute('href', `/orgs/${SLUG}/tasks/TASK-0420`);
  });

  // TASKS-02 honesty fence: missing agent/thread render a neutral fallback,
  // never a fabricated identity.
  test('renders neutral em-dash fallbacks when agent and thread are absent', async () => {
    mountTasks([
      rootTask({
        task_id: 'TASK-0430',
        assigned_agent: null,
        status: 'pending',
        severity_rollup: 'pending',
        brief: 'Unassigned, no thread',
      }),
    ]);
    await waitFor(() => {
      expect(screen.getByText('Unassigned, no thread')).toBeInTheDocument();
    });
    // No fabricated agent chip for this row.
    expect(document.querySelector('.bg-agent-worker')).toBeNull();
    // Both the agent and thread cells fall back to an em-dash.
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(2);
  });
});

describe('TaskDetailPage — jobs cross-link', () => {
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
      expect(screen.getByText(/Activity/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/Jobs from this task/i)).not.toBeInTheDocument();
  });
});

describe('TaskDetailPage — workflow chain timeline', () => {
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
      expect(screen.getByText(/Activity/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/Workflow chain/i)).not.toBeInTheDocument();
  });

  test('renders blocked chain node when task is escalated', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    // Path B: a genuine escalation is the top-level `escalated` status.
    stubHandlers(
      { ...ACTIVE_CHAIN, step_index: 0 },
      { status: 'escalated', block_kind: null },
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
    // Path B: a task waiting on a job is in_progress + blocked_on_job.
    stubHandlers(
      { ...ACTIVE_CHAIN, step_index: 1 },
      { status: 'in_progress', block_kind: 'blocked_on_job' },
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

describe('TaskDetailPage — execution subtasks', () => {
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

describe('TaskDetailPage — full-page surface', () => {
  function stubHandlers() {
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [TASK] }),
      ),
      // Detail endpoint returns the envelope; useTask selects response.task.
      http.get(`/api/v1/orgs/${SLUG}/tasks/${TASK.task_id}`, () =>
        HttpResponse.json({
          task: TASK,
          results: [],
          audit_log: [],
          revisit_chain: [],
          direct_revisits: [],
          predecessor_prior_status: null,
          active_chain: null,
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
      http.get(`/api/v1/orgs/${SLUG}/jobs/`, () => HttpResponse.json({ jobs: [] })),
    );
  }

  test('renders the task body with a "‹ All tasks" back link to the roots list', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubHandlers();
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });

    // Wait for the data-driven Brief section (gated on task.data.brief) — the
    // task id heading renders synchronously from the route param, so awaiting
    // it would not wait for the detail fetch.
    expect(
      await screen.findByRole('heading', { name: 'Brief' }),
    ).toBeInTheDocument();

    // Full-page body renders: task id heading + brief content, no drawer overlay.
    expect(
      screen.getByRole('heading', { name: new RegExp(TASK.task_id) }),
    ).toBeInTheDocument();
    expect(
      screen.getAllByText(/Draft Hong Kong visa guide/).length,
    ).toBeGreaterThan(0);

    // Back-nav returns to the roots list.
    const backLink = screen.getByRole('link', { name: /‹ All tasks/ });
    expect(backLink).toHaveAttribute('href', `/orgs/${SLUG}/tasks`);
  });
});

describe('TaskDetailPage — property grid (TASKDET-03)', () => {
  // Detail task carrying every property-grid field that has REAL backing in the
  // TaskRecord payload: status, assigned_agent, dispatched_from_thread_id,
  // created_at. Executor / Churn / Priority have no backing field and are
  // honestly omitted (see TaskDetailPage PropertyRail doc-comment).
  const DETAIL_TASK = {
    ...TASK,
    assigned_agent: 'content_writer',
    dispatched_from_thread_id: 'THR-0030',
    created_at: '2026-05-18T10:00:00Z',
  } as TaskRecord;

  function stubHandlers(jobs: JobRecord[]) {
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [DETAIL_TASK] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/${DETAIL_TASK.task_id}`, () =>
        HttpResponse.json({
          task: DETAIL_TASK,
          results: [],
          audit_log: [],
          revisit_chain: [],
          direct_revisits: [],
          predecessor_prior_status: null,
          active_chain: null,
          blocked_on_jobs: null,
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/${DETAIL_TASK.task_id}/recall`, () =>
        HttpResponse.json({
          task_id: DETAIL_TASK.task_id,
          assigned_agent: 'content_writer',
          brief: DETAIL_TASK.brief,
          status: DETAIL_TASK.status,
          output_summary: null,
          children: [],
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/jobs/`, () => HttpResponse.json({ jobs })),
    );
  }

  async function mountAndGetRail() {
    sessionStorage.setItem('happyranch.token', 'tok');
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${DETAIL_TASK.task_id}`,
    });
    return (await screen.findByRole('complementary', {
      name: /task properties/i,
    })) as HTMLElement;
  }

  test('renders a labeled property grid of the backed fields', async () => {
    const rail = await (async () => {
      stubHandlers([JOB]);
      return mountAndGetRail();
    })();

    // Backed fields — each label/value renders inside the rail.
    expect(within(rail).getByText('Status')).toBeInTheDocument();
    expect(within(rail).getByText('Assignee')).toBeInTheDocument();
    expect(within(rail).getByText('content_writer')).toBeInTheDocument();
    expect(within(rail).getByText('Thread')).toBeInTheDocument();
    const threadLink = within(rail).getByRole('link', { name: 'THR-0030' });
    expect(threadLink).toHaveAttribute(
      'href',
      `/orgs/${SLUG}/threads/THR-0030`,
    );
    expect(within(rail).getByText('Job')).toBeInTheDocument();
    const jobLink = within(rail).getByRole('link', { name: 'JOB-0001' });
    expect(jobLink).toHaveAttribute('href', `/orgs/${SLUG}/jobs/JOB-0001`);
    expect(within(rail).getByText('Created')).toBeInTheDocument();
  });

  test('honestly omits fields with no backing payload (Executor / Churn / Priority)', async () => {
    stubHandlers([JOB]);
    const rail = await mountAndGetRail();
    expect(within(rail).queryByText('Executor')).toBeNull();
    expect(within(rail).queryByText('Churn')).toBeNull();
    expect(within(rail).queryByText('Priority')).toBeNull();
  });

  test('omits the Thread and Job rows when those fields are absent', async () => {
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
          revisit_chain: [],
          direct_revisits: [],
          predecessor_prior_status: null,
          active_chain: null,
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
      http.get(`/api/v1/orgs/${SLUG}/jobs/`, () => HttpResponse.json({ jobs: [] })),
    );
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });
    const rail = (await screen.findByRole('complementary', {
      name: /task properties/i,
    })) as HTMLElement;
    // No thread / no jobs → those rows are absent (not fabricated).
    expect(within(rail).queryByText('Thread')).toBeNull();
    expect(within(rail).queryByText('Job')).toBeNull();
    // Always-present backed fields still render.
    expect(within(rail).getByText('Status')).toBeInTheDocument();
    expect(within(rail).getByText('Created')).toBeInTheDocument();
  });
});

describe('TaskDetailPage — escalation reason', () => {
  const ESCALATION_NOTE = 'Agent exhausted failure-round bound after 5 attempts';

  function stubDetailHandlers(
    overrides: Partial<TaskRecord> & Record<string, unknown>,
  ) {
    const detailTask = { ...TASK, ...overrides } as TaskRecord;
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/roots`, () =>
        HttpResponse.json({ tasks: [TASK] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/tasks/${detailTask.task_id}`, () =>
        HttpResponse.json({
          task: detailTask,
          results: [],
          audit_log: [],
          revisit_chain: [],
          direct_revisits: [],
          predecessor_prior_status: null,
          active_chain: null,
          blocked_on_jobs: null,
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

  test('displays escalation reason for a Path B escalated task with a note', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubDetailHandlers({
      status: 'escalated',
      block_kind: null,
      note: ESCALATION_NOTE,
    });
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });
    // Wait for the data-driven Brief section to confirm detail fetch completed.
    expect(
      await screen.findByRole('heading', { name: 'Brief' }),
    ).toBeInTheDocument();
    // Escalation reason banner is visible.
    expect(screen.getByText(/Escalation reason:/)).toBeInTheDocument();
    expect(screen.getByText(ESCALATION_NOTE)).toBeInTheDocument();
    // The Resolve button is present because the task is escalated.
    expect(screen.getByRole('button', { name: /Resolve/ })).toBeInTheDocument();
  });

  test('displays escalation reason for a legacy blocked+escalated task with a note', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubDetailHandlers({
      status: 'blocked',
      block_kind: 'escalated',
      note: 'Legacy escalation: budget override required',
    });
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });
    expect(
      await screen.findByRole('heading', { name: 'Brief' }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Escalation reason:/)).toBeInTheDocument();
    expect(
      screen.getByText('Legacy escalation: budget override required'),
    ).toBeInTheDocument();
    // The Resolve button is present for the legacy form too.
    expect(screen.getByRole('button', { name: /Resolve/ })).toBeInTheDocument();
  });

  test('does not display escalation reason for a non-escalated task with a note', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    // A completed task with a note — note belongs to a prior failure, not escalation.
    stubDetailHandlers({
      status: 'completed',
      block_kind: null,
      note: 'Some note from a prior escalation',
    });
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });
    expect(
      await screen.findByRole('heading', { name: 'Brief' }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Escalation reason:/)).not.toBeInTheDocument();
    // No Resolve button for non-escalated tasks.
    expect(
      screen.queryByRole('button', { name: /Resolve/ }),
    ).not.toBeInTheDocument();
  });

  test('does not display escalation reason for an escalated task with empty note', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubDetailHandlers({
      status: 'escalated',
      block_kind: null,
      note: '',
    });
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/tasks/${TASK.task_id}`,
    });
    expect(
      await screen.findByRole('heading', { name: 'Brief' }),
    ).toBeInTheDocument();
    // Empty note → no escalation reason banner.
    expect(screen.queryByText(/Escalation reason:/)).not.toBeInTheDocument();
    // Resolve button is still present (task is escalated, just no note).
    expect(screen.getByRole('button', { name: /Resolve/ })).toBeInTheDocument();
  });
});

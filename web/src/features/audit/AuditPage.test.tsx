import { act, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import userEvent from '@testing-library/user-event';

const SLUG = 'alpha';

function mountAt(route: string) {
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
  );
  return renderWithProviders(<AppRoutes />, { route });
}

function seedAudit(entries: object[] = defaultEntries()) {
  server.use(
    http.get(`/api/v1/orgs/${SLUG}/audit`, () =>
      HttpResponse.json({ entries }),
    ),
  );
}

function defaultEntries() {
  return [
    {
      id: 1,
      task_id: 'TASK-1',
      session_id: 'sess-1',
      agent: 'dev_agent',
      action: 'completion_report',
      payload: { token_usage: { total: 1500 } },
      timestamp: '2026-06-18T10:00:00Z',
    },
    {
      id: 2,
      task_id: 'TASK-2',
      session_id: 'sess-2',
      agent: 'code_reviewer',
      action: 'review_verdict',
      payload: { verdict: 'APPROVE' },
      timestamp: '2026-06-18T09:00:00Z',
    },
    {
      id: 3,
      task_id: 'TASK-3',
      session_id: 'sess-3',
      agent: 'qa_engineer',
      action: 'escalation',
      payload: {},
      timestamp: '2026-06-17T18:00:00Z',
    },
    {
      id: 4,
      task_id: 'TASK-3',
      session_id: 'sess-4',
      agent: 'founder',
      action: 'escalation_resolved',
      payload: {},
      timestamp: '2026-06-17T19:00:00Z',
    },
  ];
}

describe('AuditPage — day-grouped timeline', () => {
  test('renders day headers for entries on different days', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    seedAudit();
    mountAt(`/orgs/${SLUG}/audit`);

    await waitFor(() => {
      expect(screen.getByText('2026-06-18')).toBeInTheDocument();
      expect(screen.getByText('2026-06-17')).toBeInTheDocument();
    });
  });

  // AUDIT-03: page header treatment — uppercase eyebrow + Newsreader serif
  // title, matching the a-audit Direction-A reference and the Tasks/Agents
  // surfaces shipped earlier this program.
  test('renders the AUDIT-03 eyebrow and serif page title', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    seedAudit();
    mountAt(`/orgs/${SLUG}/audit`);

    const title = await screen.findByRole('heading', {
      name: "The org's audit trail",
    });
    expect(title).toBeInTheDocument();
    expect(title).toHaveClass('font-display');

    expect(
      screen.getByText('APPEND-ONLY · EVERY ACTION, WHO & WHEN'),
    ).toBeInTheDocument();

    // The old plain "Audit" h2 title must be gone.
    expect(
      screen.queryByRole('heading', { name: 'Audit' }),
    ).not.toBeInTheDocument();
  });

  test('renders the five-class right-rail legend with per-class counts', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    seedAudit();
    mountAt(`/orgs/${SLUG}/audit`);

    // The right rail shows all five fixed classes, each with a colored dot + count.
    const rail = await screen.findByLabelText('Event type filter');
    for (const label of ['Dispatch', 'Completed', 'Merge', 'Escalation', 'Failure']) {
      expect(within(rail).getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByRole('heading', { name: 'Event types' })).toBeInTheDocument();

    // defaultEntries: completion_report + review_verdict + escalation_resolved → Completed = 3
    const completedBtn = within(rail).getByText('Completed').closest('button')!;
    await waitFor(() => {
      expect(within(completedBtn).getByText('3')).toBeInTheDocument();
    });
    // escalation → Escalation = 1
    const escalationBtn = within(rail).getByText('Escalation').closest('button')!;
    expect(within(escalationBtn).getByText('1')).toBeInTheDocument();
  });

  test('clicking a class narrows the timeline to that class', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    seedAudit([
      { id: 1, task_id: 'TASK-C', agent: 'dev_agent', action: 'completion_report', payload: {}, timestamp: '2026-06-18T10:00:00Z' },
      { id: 2, task_id: 'TASK-E', agent: 'qa_engineer', action: 'escalation', payload: {}, timestamp: '2026-06-18T09:00:00Z' },
      { id: 3, task_id: 'TASK-F', agent: 'dev_agent', action: 'session_failed', payload: {}, timestamp: '2026-06-18T08:00:00Z' },
    ]);
    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/audit`);

    await waitFor(() => {
      expect(screen.getByText('TASK-C')).toBeInTheDocument();
      expect(screen.getByText('TASK-E')).toBeInTheDocument();
      expect(screen.getByText('TASK-F')).toBeInTheDocument();
    });

    const rail = screen.getByLabelText('Event type filter');
    const completedBtn = within(rail).getByText('Completed').closest('button')!;
    await user.click(completedBtn);

    await waitFor(() => {
      expect(completedBtn).toHaveAttribute('aria-pressed', 'true');
      // Only the completed-class row remains; other classes are narrowed out.
      expect(screen.getByText('TASK-C')).toBeInTheDocument();
      expect(screen.queryByText('TASK-E')).not.toBeInTheDocument();
      expect(screen.queryByText('TASK-F')).not.toBeInTheDocument();
    });
    // The clear affordance appears once a class is active.
    expect(within(rail).getByText('Show all events')).toBeInTheDocument();
  });

  test('clicking the active class clears the filter', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    seedAudit([
      { id: 1, task_id: 'TASK-C', agent: 'dev_agent', action: 'completion_report', payload: {}, timestamp: '2026-06-18T10:00:00Z' },
      { id: 2, task_id: 'TASK-E', agent: 'qa_engineer', action: 'escalation', payload: {}, timestamp: '2026-06-18T09:00:00Z' },
    ]);
    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/audit?class=completed`);

    await waitFor(() => {
      expect(screen.getByText('TASK-C')).toBeInTheDocument();
      expect(screen.queryByText('TASK-E')).not.toBeInTheDocument();
    });

    const rail = screen.getByLabelText('Event type filter');
    const completedBtn = within(rail).getByText('Completed').closest('button')!;
    expect(completedBtn).toHaveAttribute('aria-pressed', 'true');

    await user.click(completedBtn);

    await waitFor(() => {
      // Filter cleared — the escalation row is back, class no longer pressed.
      expect(screen.getByText('TASK-E')).toBeInTheDocument();
      expect(completedBtn).toHaveAttribute('aria-pressed', 'false');
    });
  });

  test('time window chips switch query window', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    seedAudit();
    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/audit`);

    await waitFor(() => {
      expect(screen.getByText('Completed')).toBeInTheDocument();
    });

    // The default "All time" radio should be checked
    const allTimeRadio = screen.getByRole('radio', { name: 'All time' });
    expect(allTimeRadio).toHaveAttribute('aria-checked', 'true');

    // Click "7d" window chip
    const sevenDay = screen.getByRole('radio', { name: '7d' });
    await user.click(sevenDay);

    // The 7d radio should now be checked
    await waitFor(() => {
      expect(sevenDay).toHaveAttribute('aria-checked', 'true');
    });
  });

  test('renders "All clear" banner when zero failures', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    seedAudit([
      {
        id: 1,
        task_id: 'TASK-1',
        agent: 'dev_agent',
        action: 'completion_report',
        payload: {},
        timestamp: '2026-06-18T10:00:00Z',
      },
    ]);
    mountAt(`/orgs/${SLUG}/audit`);

    await waitFor(() => {
      expect(screen.getByText('All clear')).toBeInTheDocument();
      expect(screen.getByText(/No failures or escalations/)).toBeInTheDocument();
    });
  });

  test('renders empty state for no entries', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    seedAudit([]);
    mountAt(`/orgs/${SLUG}/audit`);

    await waitFor(() => {
      expect(screen.getByText('No audit entries')).toBeInTheDocument();
    });
  });

  test('renders error with retry on failed query', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/audit`, () =>
        HttpResponse.json({ error: 'Internal error' }, { status: 500 }),
      ),
    );
    mountAt(`/orgs/${SLUG}/audit`);

    await waitFor(() => {
      expect(screen.getByText(/Could not load audit entries/)).toBeInTheDocument();
    });

    // Retry button should be present
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });

  test('renders token cost for entries with token_usage', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    seedAudit([
      {
        id: 1,
        task_id: 'TASK-1',
        agent: 'dev_agent',
        action: 'session_end',
        payload: { token_usage: { total: 1500 }, token_count: 1500 },
        timestamp: '2026-06-18T10:00:00Z',
      },
    ]);
    mountAt(`/orgs/${SLUG}/audit`);

    await waitFor(() => {
      // Token cost now renders in the mono secondary detail line ("… tokens").
      expect(screen.getByText('1.5K tokens')).toBeInTheDocument();
    });
  });

  test('renders dream marker for entries with _thread_dream_id', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    seedAudit([
      {
        id: 1,
        task_id: 'THR-001',
        agent: 'dev_agent',
        action: 'completion_report',
        payload: {},
        timestamp: '2026-06-18T10:00:00Z',
        _thread_dream_id: 'dream-abc',
      },
    ]);
    mountAt(`/orgs/${SLUG}/audit`);

    await waitFor(() => {
      expect(screen.getByLabelText('Dream-originated')).toBeInTheDocument();
    });
  });

  test('renders object ID as click-through link', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    seedAudit([
      {
        id: 1,
        task_id: 'TASK-42',
        agent: 'dev_agent',
        action: 'completion_report',
        payload: {},
        timestamp: '2026-06-18T10:00:00Z',
      },
    ]);
    mountAt(`/orgs/${SLUG}/audit`);

    await waitFor(() => {
      const taskLink = screen.getByText('TASK-42');
      expect(taskLink.closest('a')).toHaveAttribute('href', `/orgs/${SLUG}/tasks/TASK-42`);
    });
  });

  test('sorts entries reverse-chronological within same day', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    // Entries on same day, entered in ASC order (oldest first)
    seedAudit([
      {
        id: 1,
        task_id: 'TASK-1',
        agent: 'dev_agent',
        action: 'completion_report',
        payload: {},
        timestamp: '2026-06-18T08:00:00Z',
      },
      {
        id: 2,
        task_id: 'TASK-2',
        agent: 'dev_agent',
        action: 'completion_report',
        payload: {},
        timestamp: '2026-06-18T10:00:00Z',
      },
      {
        id: 3,
        task_id: 'TASK-3',
        agent: 'dev_agent',
        action: 'completion_report',
        payload: {},
        timestamp: '2026-06-18T09:00:00Z',
      },
    ]);
    mountAt(`/orgs/${SLUG}/audit`);

    // Within "2026-06-18" day, the rows should render newest-first.
    // TASK-2 (10:00) must appear before TASK-3 (09:00) which must
    // appear before TASK-1 (08:00).
    await waitFor(() => {
      const taskLinks = screen.getAllByText(/^TASK-[123]$/);
      expect(taskLinks).toHaveLength(3);
      expect(taskLinks[0].textContent).toBe('TASK-2');
      expect(taskLinks[1].textContent).toBe('TASK-3');
      expect(taskLinks[2].textContent).toBe('TASK-1');
    });
  });

  test('renders executor only from payload.executor, not agent_session_id', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    seedAudit([
      {
        id: 1,
        task_id: 'THR-001',
        agent: 'dev_agent',
        action: 'agent_session_reused',
        payload: {
          executor: 'claude-sonnet-4',
          agent_session_id: 'abc12345-6789-4def-9012-3456789abcde',
        },
        timestamp: '2026-06-18T10:00:00Z',
      },
    ]);
    mountAt(`/orgs/${SLUG}/audit`);

    await waitFor(() => {
      // Must show the real executor name
      expect(screen.getByText('claude-sonnet-4')).toBeInTheDocument();
      // Must NOT render agent_session_id as executor (not even truncated)
      expect(screen.queryByText(/abc12345/)).not.toBeInTheDocument();
      // Must NOT render "via" label
      expect(screen.queryByText(/via /)).not.toBeInTheDocument();
    });
  });

  test('omits executor when payload.executor is absent', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    seedAudit([
      {
        id: 1,
        task_id: 'TASK-1',
        agent: 'dev_agent',
        action: 'session_end',
        payload: {
          token_usage: { total: 500 },
          agent_session_id: 'abc12345-6789-4def-9012-3456789abcde',
        },
        timestamp: '2026-06-18T10:00:00Z',
      },
    ]);
    mountAt(`/orgs/${SLUG}/audit`);

    await waitFor(() => {
      // Token cost renders in the mono secondary detail line.
      expect(screen.getByText('500 tokens')).toBeInTheDocument();
      // agent_session_id must NOT appear as executor text
      expect(screen.queryByText(/abc12345/)).not.toBeInTheDocument();
    });
  });

  test('renders job badge from payload.script_request_id for job_ actions', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    seedAudit([
      {
        id: 1,
        task_id: 'TASK-100',
        agent: 'founder',
        action: 'job_run_completed',
        payload: {
          script_request_id: 'JOB-042',
          exit_code: 0,
          duration_ms: 1234,
        },
        timestamp: '2026-06-18T10:00:00Z',
      },
    ]);
    mountAt(`/orgs/${SLUG}/audit`);

    await waitFor(() => {
      // The JOB badge should link to the jobs page with the real job id
      const jobLink = screen.getByText('JOB-042');
      expect(jobLink.closest('a')).toHaveAttribute('href', `/orgs/${SLUG}/jobs/JOB-042`);
      // The parent task should also be linked separately
      const taskLink = screen.getByText('TASK-100');
      expect(taskLink.closest('a')).toHaveAttribute('href', `/orgs/${SLUG}/tasks/TASK-100`);
    });
  });

  test('omits job badge when script_request_id is absent from job_ action', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    seedAudit([
      {
        id: 1,
        task_id: 'TASK-100',
        agent: 'founder',
        action: 'job_run_completed',
        payload: {
          exit_code: 0,
        },
        timestamp: '2026-06-18T10:00:00Z',
      },
    ]);
    mountAt(`/orgs/${SLUG}/audit`);

    await waitFor(() => {
      // Parent task should still link
      expect(screen.getByText('TASK-100')).toBeInTheDocument();
      // No JOB- badge should appear (no script_request_id)
      expect(screen.queryByText(/^JOB-/)).not.toBeInTheDocument();
    });
  });

  test('export button triggers CSV download with filtered entries', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const user = userEvent.setup();
    seedAudit([
      {
        id: 1,
        task_id: 'TASK-1',
        agent: 'dev_agent',
        action: 'completion_report',
        payload: { token_usage: { total: 1500 }, executor: 'claude-sonnet-4' },
        timestamp: '2026-06-18T10:00:00Z',
      },
      {
        id: 2,
        task_id: 'TASK-2',
        agent: 'code_reviewer',
        action: 'review_verdict',
        payload: { verdict: 'APPROVE' },
        timestamp: '2026-06-18T09:00:00Z',
      },
    ]);
    mountAt(`/orgs/${SLUG}/audit`);

    // Wait for page to load
    await waitFor(() => {
      expect(screen.getByText('Export')).toBeInTheDocument();
    });

    // Spy on URL.createObjectURL + document.createElement('a').click()
    let capturedBlob: Blob | null = null;
    const originalCreateObjectURL = URL.createObjectURL;
    URL.createObjectURL = (blob: Blob) => {
      capturedBlob = blob;
      return 'blob:test';
    };
    let downloadName = '';
    const originalCreateElement = document.createElement.bind(document);
    document.createElement = ((tagName: string) => {
      const el = originalCreateElement(tagName);
      if (tagName === 'a') {
        const origClick = el.click.bind(el);
        el.click = () => {
          downloadName = (el as HTMLAnchorElement).download;
          origClick();
        };
      }
      return el;
    }) as typeof document.createElement;

    try {
      const exportBtn = screen.getByText('Export');
      await user.click(exportBtn);

      expect(capturedBlob).not.toBeNull();
      // Read blob via FileReader (jsdom Blob lacks .text())
      const csv = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result as string);
        reader.onerror = reject;
        reader.readAsText(capturedBlob!);
      });
      // Verify CSV headers
      expect(csv).toContain('timestamp,task_id,agent,action,executor,tokens,dream_id,job_id');
      // Verify both entries are in the CSV
      expect(csv).toContain('TASK-1');
      expect(csv).toContain('completion_report');
      expect(csv).toContain('claude-sonnet-4');
      expect(csv).toContain('1500');
      expect(csv).toContain('TASK-2');
      expect(csv).toContain('review_verdict');
      expect(downloadName).toMatch(/^audit-\d{4}-\d{2}-\d{2}\.csv$/);
    } finally {
      URL.createObjectURL = originalCreateObjectURL;
      document.createElement = originalCreateElement;
    }
  });

  test('export respects active legend filter — matching row present, non-matching absent', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const user = userEvent.setup();

    // Capture the since query param sent to the backend
    let capturedSince: string | null = null;
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/audit`, ({ request }) => {
        const url = new URL(request.url);
        capturedSince = url.searchParams.get('since');
        return HttpResponse.json({
          entries: [
            {
              id: 1,
              task_id: 'TASK-1',
              agent: 'dev_agent',
              action: 'completion_report',
              payload: { token_usage: { total: 1500 }, executor: 'claude-sonnet-4' },
              timestamp: '2026-06-18T10:00:00Z',
            },
            {
              id: 2,
              task_id: 'TASK-2',
              agent: 'qa_engineer',
              action: 'escalation',
              payload: {},
              timestamp: '2026-06-18T09:00:00Z',
            },
          ],
        });
      }),
    );

    // Mount with active class legend filter: class=completed, time window 7d
    mountAt(`/orgs/${SLUG}/audit?class=completed&since=7d`);

    // Wait for data to load — legend counts depend on allEntries
    await waitFor(() => {
      expect(screen.getByText('Export')).toBeInTheDocument();
      const rail = screen.getByLabelText('Event type filter');
      expect(within(rail).getByText('Completed')).toBeInTheDocument();
    });

    // Spy on Blob/URL for export
    let capturedBlob: Blob | null = null;
    const originalCreateObjectURL = URL.createObjectURL;
    URL.createObjectURL = (blob: Blob) => {
      capturedBlob = blob;
      return 'blob:test';
    };

    try {
      const exportBtn = screen.getByText('Export');
      await user.click(exportBtn);

      expect(capturedBlob).not.toBeNull();
      const csv = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result as string);
        reader.onerror = reject;
        reader.readAsText(capturedBlob!);
      });

      // CSV headers
      expect(csv).toContain('timestamp,task_id,agent,action,executor,tokens,dream_id,job_id');

      // Matching row (completion_report) IS present
      expect(csv).toContain('TASK-1');
      expect(csv).toContain('completion_report');

      // Non-matching row (escalation, a different class) is ABSENT — the key
      // assertion that proves handleExport respects the active class filter,
      // not just allEntries.
      expect(csv).not.toContain('TASK-2');
      expect(csv).not.toContain('escalation');

      // MSW handler should have received a since param (ISO date from 7d window)
      expect(capturedSince).not.toBeNull();
      expect(capturedSince).toMatch(/^\d{4}-\d{2}-\d{2}T/);
    } finally {
      URL.createObjectURL = originalCreateObjectURL;
    }
  });

  test('class filter narrows the timeline client-side and threads the memoized since param', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');

    // The class filter is applied CLIENT-SIDE off the already-fetched rows, so
    // the backend returns the full set regardless; AuditTimeline narrows to the
    // active class. Capture the since param to prove sinceISO is threaded.
    let capturedSince: string | null = null;
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/audit`, ({ request }) => {
        capturedSince = new URL(request.url).searchParams.get('since');
        return HttpResponse.json({
          entries: [
            {
              id: 10,
              task_id: 'TASK-10',
              agent: 'dev_agent',
              action: 'completion_report',
              payload: { token_usage: { total: 500 } },
              timestamp: '2026-06-18T10:00:00Z',
            },
            {
              id: 20,
              task_id: 'TASK-20',
              agent: 'qa_engineer',
              action: 'escalation',
              payload: {},
              timestamp: '2026-06-18T09:00:00Z',
            },
          ],
        });
      }),
    );

    mountAt(`/orgs/${SLUG}/audit?class=completed&since=7d`);

    // Completed-class row renders.
    await waitFor(() => {
      expect(screen.getByText('TASK-10')).toBeInTheDocument();
    });

    // FAIL-IF-VACUOUS: the escalation-class row (TASK-20) is narrowed out
    // client-side. If AuditTimeline rendered the unfiltered set, TASK-20 would
    // appear and this assertion would fail.
    expect(screen.queryByText('TASK-20')).not.toBeInTheDocument();

    // sinceISO must be threaded from AuditPage's memoized
    // `useMemo(() => sinceToISO(filters.since), [filters.since])`; an inline
    // sinceToISO() on the JSX prop would churn the queryKey on every render.
    expect(capturedSince).toMatch(/^\d{4}-\d{2}-\d{2}T/);
  });

  // AUDIT-02 REVISE (Finding 1): the CSV export set must match the displayed
  // /queried timeline. The timeline server-filters by the raw `action` deep-link
  // AND narrows by the active `eventClass` client-side, so the export must apply
  // BOTH. Below the MSW handler honors the `action` param exactly like the real
  // /audit route, so the timeline query (which sends `action`) gets only the
  // matching rows while the page's legend query (no `action`) gets the full set.
  test('export honors a raw ?action=… deep-link — export rows == the action-filtered set', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const user = userEvent.setup();

    server.use(
      http.get(`/api/v1/orgs/${SLUG}/audit`, ({ request }) => {
        const action = new URL(request.url).searchParams.get('action');
        const all = [
          { id: 1, task_id: 'TASK-1', agent: 'dev_agent', action: 'completion_report', payload: {}, timestamp: '2026-06-18T10:00:00Z' },
          { id: 2, task_id: 'TASK-2', agent: 'qa_engineer', action: 'escalation', payload: {}, timestamp: '2026-06-18T09:00:00Z' },
        ];
        const entries = action ? all.filter((e) => e.action === action) : all;
        return HttpResponse.json({ entries });
      }),
    );

    mountAt(`/orgs/${SLUG}/audit?action=completion_report`);

    // Timeline is server-filtered to the completion row only; the escalation
    // row is never displayed.
    await waitFor(() => {
      expect(screen.getByText('TASK-1')).toBeInTheDocument();
    });
    expect(screen.queryByText('TASK-2')).not.toBeInTheDocument();

    let capturedBlob: Blob | null = null;
    const originalCreateObjectURL = URL.createObjectURL;
    URL.createObjectURL = (blob: Blob) => {
      capturedBlob = blob;
      return 'blob:test';
    };
    try {
      await user.click(screen.getByText('Export'));
      expect(capturedBlob).not.toBeNull();
      const csv = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result as string);
        reader.onerror = reject;
        reader.readAsText(capturedBlob!);
      });
      // Export parity: the displayed action-filtered row is in; the row the
      // timeline never showed (TASK-2 / escalation) is out. With the bug the
      // export dumped every fetched row including TASK-2.
      expect(csv).toContain('TASK-1');
      expect(csv).toContain('completion_report');
      expect(csv).not.toContain('TASK-2');
      expect(csv).not.toContain('escalation');
    } finally {
      URL.createObjectURL = originalCreateObjectURL;
    }
  });

  test('export honors action + class together — a contradictory pair exports nothing, matching the empty timeline', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const user = userEvent.setup();

    server.use(
      http.get(`/api/v1/orgs/${SLUG}/audit`, ({ request }) => {
        const action = new URL(request.url).searchParams.get('action');
        const all = [
          { id: 1, task_id: 'TASK-OK', agent: 'dev_agent', action: 'completion_report', payload: {}, timestamp: '2026-06-18T10:00:00Z' },
          { id: 2, task_id: 'TASK-FAIL', agent: 'dev_agent', action: 'session_failed', payload: {}, timestamp: '2026-06-18T09:00:00Z' },
        ];
        const entries = action ? all.filter((e) => e.action === action) : all;
        return HttpResponse.json({ entries });
      }),
    );

    // action=completion_report server-filters the timeline to the completed
    // row; class=failure then narrows it client-side to nothing → empty.
    mountAt(`/orgs/${SLUG}/audit?action=completion_report&class=failure`);

    await waitFor(() => {
      expect(screen.getByText('No audit entries')).toBeInTheDocument();
    });

    let capturedBlob: Blob | null = null;
    const originalCreateObjectURL = URL.createObjectURL;
    URL.createObjectURL = (blob: Blob) => {
      capturedBlob = blob;
      return 'blob:test';
    };
    try {
      await user.click(screen.getByText('Export'));
      // The export set is empty too — it must NOT dump the failure-class row the
      // timeline never showed. With the bug (class-only export) it contained
      // TASK-FAIL while the timeline was empty.
      expect(capturedBlob).toBeNull();
    } finally {
      URL.createObjectURL = originalCreateObjectURL;
    }
  });

  // AUDIT-02 REVISE (Finding 2): isAllClear derives from classOf, so a
  // failure-class action the old hand-kept FAILURE_ACTIONS set omitted no
  // longer renders 'All clear' over a real failure row.
  test('does not render "All clear" for a failure-class action the old set omitted', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    seedAudit([
      { id: 1, task_id: 'TASK-J', agent: 'founder', action: 'job_rejected', payload: {}, timestamp: '2026-06-18T10:00:00Z' },
    ]);
    mountAt(`/orgs/${SLUG}/audit`);

    // Failure count == 1 proves the row loaded and classed as failure.
    const rail = await screen.findByLabelText('Event type filter');
    const failureBtn = within(rail).getByText('Failure').closest('button')!;
    await waitFor(() => {
      expect(within(failureBtn).getByText('1')).toBeInTheDocument();
    });

    // The 'All clear' calm state must NOT render.
    expect(screen.queryByTestId('all-clear')).not.toBeInTheDocument();
    expect(screen.queryByText('All clear')).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Container-geometry contract (THR-098 re-open): TimelineBody is
// `flex-1 overflow-y-auto` and paginates via an IntersectionObserver rooted on
// itself. It only gets a bounded height — and its inner scroll + sentinel
// re-intersection only work — when its PARENT is a bounded-height flex column.
// A THR-099 ContentWrap re-fit left the timeline card a plain `overflow-hidden`
// block, so `flex-1` went inert, TimelineBody grew to full content height, the
// card clipped it with no scrollbar, and infinite scroll died ("can't load
// more"). jsdom has no layout, so we lock the CLASS contract that the live
// geometry depends on — in BOTH render branches.
// ---------------------------------------------------------------------------
describe('AuditPage — timeline container-geometry contract', () => {
  test('normal branch: the timeline card is a bounded-height flex column', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    // defaultEntries() contains an escalation → NOT all-clear → normal branch.
    seedAudit();
    mountAt(`/orgs/${SLUG}/audit`);

    const body = await screen.findByLabelText('Audit timeline');
    // AuditTimeline renders TimelineBody directly, so the scroll box's parent
    // IS the card. It must be a bounded flex column, else TimelineBody's
    // flex-1 is inert and the card clips the list with no scrollbar.
    const card = body.parentElement!;
    expect(card).toHaveClass('flex', 'flex-col', 'min-h-0');
    // Still clips its own rounded corners.
    expect(card).toHaveClass('overflow-hidden');
  });

  test('all-clear branch: the wrapper carries the same bounded flex-column chain', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    // Zero failures/escalations → all-clear branch renders.
    seedAudit([
      { id: 1, task_id: 'TASK-1', agent: 'dev_agent', action: 'completion_report', payload: {}, timestamp: '2026-06-18T10:00:00Z' },
    ]);
    mountAt(`/orgs/${SLUG}/audit`);

    const wrapper = await screen.findByTestId('all-clear');
    expect(wrapper).toHaveClass('flex', 'flex-col', 'h-full', 'min-h-0');
    // TimelineBody is a direct child of the all-clear wrapper, so the bounded
    // chain reaches it exactly like the normal branch's card.
    const body = await screen.findByLabelText('Audit timeline');
    expect(body.parentElement).toBe(wrapper);
  });
});

// ---------------------------------------------------------------------------
// Keyset infinite scroll (THR-069 msg12): the timeline pages older entries in
// via `cursor`/`next_cursor`, appending them under their own day header —
// grouping preserved ACROSS pages. jsdom has no IntersectionObserver, so we
// stub a controllable one and fire the sentinel manually to simulate scroll.
// ---------------------------------------------------------------------------

// Per-test toggle for the observer-root lock test: when enabled, every useRef
// object tracks reads via a getter on `.current` that fires a stack check.
let _trackUseRefReads = false;
/** Read IDs of TimelineBody refs whose `.current` was accessed. */
let _timelineBodyReadIds: Set<number> = new Set();
/** Per-TimelineBody render, refs get sequential ids (0=scrollRef, 1=sentinelRef). */
let _timelineBodyRefCounter = 0;

vi.mock('react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react')>();
  return {
    ...actual,
    useRef(init: unknown) {
      if (!_trackUseRefReads) return actual.useRef(init);
      const stack = new Error().stack || '';
      const isTimelineBody = stack.includes('AuditTimeline');
      const refId = isTimelineBody ? _timelineBodyRefCounter++ : -1;
      const inner = { value: init };
      return Object.defineProperty({}, 'current', {
        get() {
          if (refId >= 0) _timelineBodyReadIds.add(refId);
          return inner.value;
        },
        set(v: unknown) {
          inner.value = v;
        },
        enumerable: true,
        configurable: true,
      });
    },
  };
});

describe('AuditPage — keyset infinite scroll', () => {
  let observerCallbacks: Array<(entries: { isIntersecting: boolean }[]) => void> =
    [];
  /** Roots passed to IntersectionObserver constructors, ordered by creation. */
  let observerRoots: Array<Element | null> = [];

  class MockIntersectionObserver {
    private cb: (entries: { isIntersecting: boolean }[]) => void;
    constructor(
      cb: (entries: { isIntersecting: boolean }[]) => void,
      options?: IntersectionObserverInit,
    ) {
      this.cb = cb;
      observerRoots.push((options?.root as Element | null) ?? null);
    }
    observe() {
      observerCallbacks.push(this.cb);
    }
    unobserve() {}
    disconnect() {
      observerCallbacks = observerCallbacks.filter((c) => c !== this.cb);
    }
    takeRecords() {
      return [];
    }
  }

  /** Simulate every live sentinel scrolling into view. */
  function fireIntersect() {
    [...observerCallbacks].forEach((cb) => cb([{ isIntersecting: true }]));
  }

  beforeEach(() => {
    observerCallbacks = [];
    observerRoots = [];
    vi.stubGlobal('IntersectionObserver', MockIntersectionObserver);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  test('loads additional OLDER rows on sentinel intersection, grouping across pages', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    const seenCursors: (string | null)[] = [];
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/audit`, ({ request }) => {
        const cursor = new URL(request.url).searchParams.get('cursor');
        seenCursors.push(cursor);
        if (!cursor) {
          // Page 1 — newest day, plus an opaque cursor for the next page.
          return HttpResponse.json({
            entries: [
              { id: 10, task_id: 'TASK-NEW-A', agent: 'dev_agent', action: 'completion_report', payload: {}, timestamp: '2026-06-20T10:00:00Z' },
              { id: 9, task_id: 'TASK-NEW-B', agent: 'dev_agent', action: 'completion_report', payload: {}, timestamp: '2026-06-20T09:00:00Z' },
            ],
            next_cursor: 'cursor-page-2',
          });
        }
        // Page 2 — older day; next_cursor null → set exhausted.
        return HttpResponse.json({
          entries: [
            { id: 2, task_id: 'TASK-OLD-A', agent: 'dev_agent', action: 'completion_report', payload: {}, timestamp: '2026-06-19T10:00:00Z' },
            { id: 1, task_id: 'TASK-OLD-B', agent: 'dev_agent', action: 'completion_report', payload: {}, timestamp: '2026-06-19T09:00:00Z' },
          ],
          next_cursor: null,
        });
      }),
    );

    mountAt(`/orgs/${SLUG}/audit`);

    // Page 1 rows render; the older page-2 rows are NOT loaded yet.
    await waitFor(() => {
      expect(screen.getByText('TASK-NEW-A')).toBeInTheDocument();
    });
    expect(screen.queryByText('TASK-OLD-A')).not.toBeInTheDocument();

    // The sentinel is being observed once a next page exists.
    await waitFor(() => expect(observerCallbacks.length).toBeGreaterThan(0));

    // Scroll the sentinel into view → fetch the next (older) page.
    await act(async () => {
      fireIntersect();
    });

    // Older rows append under their OWN day header — grouping spans pages and
    // does not reset per page; the newest rows remain (pages accumulate).
    await waitFor(() => {
      expect(screen.getByText('TASK-OLD-A')).toBeInTheDocument();
      expect(screen.getByText('TASK-OLD-B')).toBeInTheDocument();
    });
    expect(screen.getByText('2026-06-20')).toBeInTheDocument();
    expect(screen.getByText('2026-06-19')).toBeInTheDocument();
    expect(screen.getByText('TASK-NEW-A')).toBeInTheDocument();

    // Page 2 was fetched with the opaque cursor returned by page 1.
    expect(seenCursors).toContain('cursor-page-2');

    // End-of-list affordance appears once next_cursor is null.
    await waitFor(() => {
      expect(screen.getByText('End of audit trail')).toBeInTheDocument();
    });
  });

  test('stops paging when next_cursor is null (single-page result)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    let requestCount = 0;
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/audit`, () => {
        requestCount += 1;
        return HttpResponse.json({
          entries: [
            { id: 1, task_id: 'TASK-ONLY', agent: 'dev_agent', action: 'escalation', payload: {}, timestamp: '2026-06-20T10:00:00Z' },
          ],
          next_cursor: null,
        });
      }),
    );

    mountAt(`/orgs/${SLUG}/audit`);

    await waitFor(() => {
      expect(screen.getByText('TASK-ONLY')).toBeInTheDocument();
    });

    // next_cursor is null → no sentinel is observed, firing does nothing.
    expect(observerCallbacks.length).toBe(0);
    await act(async () => {
      fireIntersect();
    });
    // Still exactly one page fetched — no runaway pagination. The absence of an
    // observed sentinel (length 0) is the proof paging halts at next_cursor=null.
    expect(requestCount).toBe(1);
    expect(screen.getByText('End of audit trail')).toBeInTheDocument();
  });

  test('cursor-walk: pages through 3 cursor-linked pages in order, stopping at null (THR-098)', async () => {
    // Regression: prove multi-page chaining works end-to-end — page 1
    // (next_cursor=A) → page 2 (next_cursor=B) → page 3 (next_cursor=null).
    // The test asserts that each distinct cursor is passed to fetchNextPage
    // IN ORDER and that the chain stops when next_cursor is null.
    sessionStorage.setItem('happyranch.token', 'tok');
    const seenCursors: (string | null)[] = [];
    let pageHit = 0;
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/audit`, ({ request }) => {
        const cursor = new URL(request.url).searchParams.get('cursor');
        seenCursors.push(cursor);
        pageHit += 1;
        if (pageHit === 1) {
          // Page 1 — no cursor → first page, return cursor-A.
          return HttpResponse.json({
            entries: [
              { id: 20, task_id: 'TASK-P1', agent: 'dev_agent', action: 'completion_report', payload: {}, timestamp: '2026-06-22T10:00:00Z' },
            ],
            next_cursor: 'cursor-A',
          });
        }
        if (pageHit === 2 && cursor === 'cursor-A') {
          // Page 2 — cursor-A → return cursor-B.
          return HttpResponse.json({
            entries: [
              { id: 10, task_id: 'TASK-P2', agent: 'dev_agent', action: 'completion_report', payload: {}, timestamp: '2026-06-21T10:00:00Z' },
            ],
            next_cursor: 'cursor-B',
          });
        }
        // Page 3 — cursor-B → terminal page.
        return HttpResponse.json({
          entries: [
            { id: 5, task_id: 'TASK-P3', agent: 'dev_agent', action: 'completion_report', payload: {}, timestamp: '2026-06-20T10:00:00Z' },
          ],
          next_cursor: null,
        });
      }),
    );

    mountAt(`/orgs/${SLUG}/audit`);

    // Page 1 renders; page 2/3 rows are NOT visible yet.
    await waitFor(() => {
      expect(screen.getByText('TASK-P1')).toBeInTheDocument();
    });
    expect(screen.queryByText('TASK-P2')).not.toBeInTheDocument();
    expect(screen.queryByText('TASK-P3')).not.toBeInTheDocument();

    // Sentinel must be observed (hasNextPage true after page 1).
    await waitFor(() => expect(observerCallbacks.length).toBeGreaterThan(0));

    // Fire sentinel intersect → fetch page 2.
    await act(async () => {
      fireIntersect();
    });
    await waitFor(() => {
      expect(screen.getByText('TASK-P2')).toBeInTheDocument();
    });
    expect(screen.queryByText('TASK-P3')).not.toBeInTheDocument();

    // Fire again → fetch page 3 (terminal).
    await act(async () => {
      fireIntersect();
    });
    await waitFor(() => {
      expect(screen.getByText('TASK-P3')).toBeInTheDocument();
    });

    // Cursor chain walked in order: page1(null) → cursor-A → cursor-B.
    expect(seenCursors).toEqual([null, 'cursor-A', 'cursor-B']);

    // Terminal state: end-of-list affordance renders, no more pages.
    expect(screen.getByText('End of audit trail')).toBeInTheDocument();
    expect(pageHit).toBe(3);
  });

  test('observer-root lock: root is parentElement, never reads scrollRef.current (THR-098)', async () => {
    // Regression: if scrollRef.current is null during observer creation
    // (concurrent React ref timing), the observer silently uses the document
    // viewport and the sentinel never fires inside the scroll box.
    //
    // The fix uses sentinel.parentElement, which is always correct. This
    // test instruments useRef to detect whether TimelineBody reads any
    // ref's `.current` during observer construction.
    //
    // OLD code (root: scrollRef.current) → stack trace lands in TimelineBody → RED.
    // NEW code (root: node.parentElement) → no ref read → GREEN.
    _trackUseRefReads = true;
    _timelineBodyReadIds = new Set();
    _timelineBodyRefCounter = 0;

    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/audit`, () =>
        HttpResponse.json({
          entries: [
            { id: 10, task_id: 'TASK-A', agent: 'dev_agent', action: 'completion_report', payload: {}, timestamp: '2026-06-20T10:00:00Z' },
            { id: 9, task_id: 'TASK-B', agent: 'dev_agent', action: 'completion_report', payload: {}, timestamp: '2026-06-20T09:00:00Z' },
          ],
          next_cursor: 'cursor-next',
        }),
      ),
    );

    mountAt(`/orgs/${SLUG}/audit`);

    // Wait for entries + sentinel observer to be set up (hasNextPage true).
    await waitFor(() => {
      expect(screen.getByText('TASK-A')).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(observerCallbacks.length).toBeGreaterThan(0);
    });

    // KEY ASSERTION: scrollRef (id=0) was NEVER read.
    // sentinelRef (id=1) is always read to get the DOM node — that's expected.
    //
    // With NEW code (root: node.parentElement): only id=1 read → id=0 NOT in set → GREEN.
    // With OLD code (root: scrollRef.current): id=0 read → id=0 in set → RED.
    expect(_timelineBodyReadIds.has(0)).toBe(false);

    // Sentry: sentinelRef IS read (the code needs the DOM node).
    expect(_timelineBodyReadIds.has(1)).toBe(true);

    // Additionally verify the observer root IS the scroll container.
    expect(observerRoots.length).toBeGreaterThan(0);
    for (const root of observerRoots) {
      expect(root).not.toBeNull();
      // Sentinel parent IS the scroll container <div aria-label="Audit timeline">
      expect(root).toBeInstanceOf(HTMLDivElement);
      expect((root as HTMLElement).getAttribute('aria-label')).toBe('Audit timeline');
    }

    _trackUseRefReads = false;
  });
});

import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
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

  test('renders legend with counts', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    seedAudit();
    mountAt(`/orgs/${SLUG}/audit`);

    await waitFor(() => {
      // Each legend chip shows label + count. completion_report maps to "Completed".
      expect(screen.getByText('Completed')).toBeInTheDocument();
      expect(screen.getByText('Reviewed')).toBeInTheDocument();
      expect(screen.getByText('Escalation')).toBeInTheDocument();
      expect(screen.getByText('Escalated — resolved')).toBeInTheDocument();
    });
  });

  test('legend click filters timeline by event type', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    seedAudit();
    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/audit`);

    // Wait for legend to render
    await waitFor(() => {
      expect(screen.getByText('Completed')).toBeInTheDocument();
    });

    // Click "Escalation" legend chip (first one is the legend, second is the row action text)
    const escalationChips = screen.getAllByText('Escalation');
    // The legend chip is inside a button; click its closest button
    const chipBtn = escalationChips[0].closest('button')!;
    await user.click(chipBtn);

    // URL should contain action=escalation
    await waitFor(() => {
      // getSearchParams is driven by URL; check the active filter banner
      expect(screen.getByText(/Filtered:/)).toBeInTheDocument();
      // Verify the filter action text appears in the banner
      expect(screen.getByText('escalation', { selector: '.text-text-primary.font-medium' })).toBeInTheDocument();
    });
  });

  test('legend click toggles off filter', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    seedAudit();
    const user = userEvent.setup();
    mountAt(`/orgs/${SLUG}/audit?action=escalation`);

    await waitFor(() => {
      expect(screen.getByText('Escalation')).toBeInTheDocument();
      expect(screen.getByText(/Filtered:/)).toBeInTheDocument();
    });

    // Click the already-active "Escalation" chip to deactivate
    // getAllByText: legend chip + row action text
    const escalationChips = screen.getAllByText('Escalation');
    const chip = escalationChips[0].closest('button')!;
    await user.click(chip);

    // Filtered banner should disappear
    await waitFor(() => {
      expect(screen.queryByText(/Filtered:/)).not.toBeInTheDocument();
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
      expect(screen.getByText('1.5K tok')).toBeInTheDocument();
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
      // Token cost should render
      expect(screen.getByText('500 tok')).toBeInTheDocument();
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
              agent: 'code_reviewer',
              action: 'review_verdict',
              payload: { verdict: 'APPROVE' },
              timestamp: '2026-06-18T09:00:00Z',
            },
          ],
        });
      }),
    );

    // Mount with active legend filter: action=completion_report, time window 7d
    mountAt(`/orgs/${SLUG}/audit?action=completion_report&since=7d`);

    // Wait for data to load — legend chips depend on allEntries
    await waitFor(() => {
      expect(screen.getByText('Export')).toBeInTheDocument();
      expect(screen.getByText('Completed')).toBeInTheDocument();
      expect(screen.getByText(/Filtered:/)).toBeInTheDocument();
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

      // Non-matching row (review_verdict) is ABSENT — this is the key assertion
      // that proves handleExport respects filters.action, not just allEntries
      expect(csv).not.toContain('TASK-2');
      expect(csv).not.toContain('review_verdict');

      // MSW handler should have received a since param (ISO date from 7d window)
      expect(capturedSince).not.toBeNull();
      expect(capturedSince).toMatch(/^\d{4}-\d{2}-\d{2}T/);
    } finally {
      URL.createObjectURL = originalCreateObjectURL;
    }
  });

  test('timeline uses action-filtered query, not unfiltered allEntries (regression: queryKey churn)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');

    // Seed TWO entries with DIFFERENT actions, both within the 7d window.
    // The MSW handler differentiates: when `action` query param is present
    // (AuditTimeline), return only matching entries; when absent (AuditPage
    // legend query), return all entries so legend counts stay correct.
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/audit`, ({ request }) => {
        const url = new URL(request.url);
        const actionParam = url.searchParams.get('action');
        const all = [
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
            agent: 'code_reviewer',
            action: 'review_verdict',
            payload: { verdict: 'APPROVE' },
            timestamp: '2026-06-18T09:00:00Z',
          },
        ];
        // AuditPage legend query → no action param → return all entries
        // AuditTimeline query → action param present → return only matching
        return HttpResponse.json({
          entries: actionParam ? all.filter((e) => e.action === actionParam) : all,
        });
      }),
    );

    mountAt(`/orgs/${SLUG}/audit?action=completion_report&since=7d`);

    // Wait for data to settle — legend chips show both entries (legend
    // query is unfiltered by action), filter banner shows active filter.
    await waitFor(() => {
      expect(screen.getByText('Completed')).toBeInTheDocument();
      expect(screen.getByText('Reviewed')).toBeInTheDocument();
      expect(screen.getByText(/Filtered:/)).toBeInTheDocument();
    });

    // PASS: matching row (completion_report / TASK-10) renders in timeline.
    expect(screen.getByText('TASK-10')).toBeInTheDocument();

    // FAIL-IF-VACUOUS: non-matching row (review_verdict / TASK-20) MUST be
    // absent from the timeline.  If AuditTimeline rendered the unfiltered
    // allEntries set (from AuditPage's legend query) instead of its own
    // action-filtered query, TASK-20 would appear and this assertion would
    // fail — proving the test catches the vacuous baseline.
    expect(screen.queryByText('TASK-20')).not.toBeInTheDocument();

    // Grep-proof: the sinceISO prop threaded from AuditPage to AuditTimeline
    // must come from the memoized `useMemo(() => sinceToISO(filters.since),
    // [filters.since])` in AuditPage.  An inline sinceToISO(filters.since)
    // call on the JSX prop would produce a new ISO string every render,
    // churning the queryKey and causing infinite re-fetches.
  });
});

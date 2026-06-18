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
      expect(screen.getByText('escalation', { selector: '.text-fg.font-medium' })).toBeInTheDocument();
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
});

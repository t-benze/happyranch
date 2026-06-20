import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import type { WorkHourRecord } from '@/lib/api/types';

const SLUG = 'alpha';

function mountAt(route: string) {
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
  );
  return renderWithProviders(<AppRoutes />, { route });
}

function seedWorkHours(entries: WorkHourRecord[] = defaultEntries()) {
  server.use(
    http.get(`/api/v1/orgs/${SLUG}/work-hours`, () => {
      return HttpResponse.json({ work_hours: entries });
    }),
  );
}

function defaultEntries(): WorkHourRecord[] {
  return [
    {
      work_hour_id: 'WORKHOUR-001',
      agent_name: 'dev_agent',
      local_date: '2026-06-18',
      slot: '09:00',
      mode: 'windowed',
      scheduled_for: '2026-06-18T09:00:00Z',
      started_at: '2026-06-18T09:00:01Z',
      ended_at: '2026-06-18T09:30:00Z',
      status: 'completed',
      routine_count: 3,
      spawned_task_ids: ['TASK-510', 'TASK-511'],
      spawned_task_count: 2,
      summary: 'Morning routines completed.',
      transcript_path: null,
      session_id: 'sess-1',
      error: null,
      created_at: '2026-06-18T09:00:00Z',
    },
    {
      work_hour_id: 'WORKHOUR-002',
      agent_name: 'code_reviewer',
      local_date: '2026-06-18',
      slot: '13:00',
      mode: 'windowed',
      scheduled_for: '2026-06-18T13:00:00Z',
      started_at: null,
      ended_at: null,
      status: 'pending',
      routine_count: 2,
      spawned_task_ids: [],
      spawned_task_count: 0,
      summary: null,
      transcript_path: null,
      session_id: null,
      error: null,
      created_at: '2026-06-18T13:00:00Z',
    },
    {
      work_hour_id: 'WORKHOUR-003',
      agent_name: 'dev_agent',
      local_date: '2026-06-17',
      slot: '09:00',
      mode: 'windowed',
      scheduled_for: '2026-06-17T09:00:00Z',
      started_at: '2026-06-17T09:00:05Z',
      ended_at: '2026-06-17T09:25:00Z',
      status: 'completed',
      routine_count: 2,
      spawned_task_ids: ['TASK-508'],
      spawned_task_count: 1,
      summary: 'Morning routines completed.',
      transcript_path: null,
      session_id: 'sess-2',
      error: null,
      created_at: '2026-06-17T09:00:00Z',
    },
  ];
}

describe('SchedulePage — read-only work-hours list', () => {
  test('renders page header and description', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    seedWorkHours();
    mountAt(`/orgs/${SLUG}/schedule`);

    await waitFor(() => {
      // 'Schedule' appears in both sidebar nav and page header
      const scheduleElements = screen.getAllByText('Schedule');
      expect(scheduleElements.length).toBeGreaterThanOrEqual(2);
      expect(
        screen.getByText(/Per-agent working-hours wakes/),
      ).toBeInTheDocument();
    });
  });

  test('renders view-only notice', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    seedWorkHours();
    mountAt(`/orgs/${SLUG}/schedule`);

    await waitFor(() => {
      expect(
        screen.getByText(/View-only. Creating named recurring wakes is not available/),
      ).toBeInTheDocument();
    });
  });

  test('groups entries by agent with agent header links', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    seedWorkHours();
    mountAt(`/orgs/${SLUG}/schedule`);

    await waitFor(() => {
      // Two agent groups
      expect(screen.getByText('code_reviewer')).toBeInTheDocument();
      expect(screen.getByText('dev_agent')).toBeInTheDocument();

      // Agent names are links to Agents page
      const crLink = screen.getByText('code_reviewer').closest('a');
      expect(crLink).toHaveAttribute('href', `/orgs/${SLUG}/agents/code_reviewer`);
      const daLink = screen.getByText('dev_agent').closest('a');
      expect(daLink).toHaveAttribute('href', `/orgs/${SLUG}/agents/dev_agent`);
    });
  });

  test('renders wake entries with fields: date, slot, mode, scheduled, status, routines', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    seedWorkHours();
    mountAt(`/orgs/${SLUG}/schedule`);

    // Wait for all entries to render (check both extremes: first and last entry)
    await waitFor(() => {
      expect(screen.getByText('3 routines')).toBeInTheDocument();
      expect(screen.getByText('Pending')).toBeInTheDocument();
    });

    // Local dates — two entries share 2026-06-18
    const date18 = screen.getAllByText('2026-06-18');
    expect(date18).toHaveLength(2);
    expect(screen.getByText('2026-06-17')).toBeInTheDocument();

    // Slots
    expect(screen.getAllByText('09:00').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('13:00')).toBeInTheDocument();

    // Modes
    expect(screen.getAllByText('windowed').length).toBe(3);

    // Statuses
    expect(screen.getAllByText('Completed').length).toBe(2);

    // Routine counts (already verified in waitFor)
    // Two entries have 2 routines each
    expect(screen.getAllByText('2 routines').length).toBe(2);
  });

  test('renders spawned task IDs as IdBadge links', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    seedWorkHours();
    mountAt(`/orgs/${SLUG}/schedule`);

    await waitFor(() => {
      const taskLink = screen.getByText('TASK-510');
      expect(taskLink.closest('a')).toHaveAttribute(
        'href',
        `/orgs/${SLUG}/tasks/TASK-510`,
      );
    });
  });

  test('renders empty state when no wakes exist', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    seedWorkHours([]);
    mountAt(`/orgs/${SLUG}/schedule`);

    await waitFor(() => {
      expect(screen.getByText('No scheduled wakes')).toBeInTheDocument();
      expect(
        screen.getByText(/No working-hours wakes have been scheduled yet/),
      ).toBeInTheDocument();
    });
  });

  test('renders error state with retry when API fails', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/work-hours`, () =>
        HttpResponse.json({ detail: 'Internal error' }, { status: 500 }),
      ),
    );
    mountAt(`/orgs/${SLUG}/schedule`);

    await waitFor(() => {
      expect(screen.getByText(/Could not load scheduled wakes/)).toBeInTheDocument();
      expect(screen.getByText('Retry')).toBeInTheDocument();
    });
  });

  test('displays no authoring controls', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    seedWorkHours();
    mountAt(`/orgs/${SLUG}/schedule`);

    await waitFor(() => {
      const scheduleElements = screen.getAllByText('Schedule');
      expect(scheduleElements.length).toBeGreaterThanOrEqual(2);
    });

    // No "create", "new wake", "add wake" buttons present
    // (sidebar has an "Add org" button, so /add/i is too broad)
    expect(screen.queryByRole('button', { name: /create/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /new wake/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /add wake/i })).toBeNull();
    // No form inputs for editing (sidebar may have org selector)
    expect(screen.queryByPlaceholderText(/type a message/i)).toBeNull();
  });
});

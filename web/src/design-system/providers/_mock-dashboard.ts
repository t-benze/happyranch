/**
 * Mock implementation of `DashboardApi` for the prototype sandbox.
 *
 * Single static fixture mimicking a busy-but-healthy founder day.
 */
import type { DashboardApi, QueryLike } from './DataContext';
import type { DashboardSummaryResponse } from '@/lib/api/types';

function ok<T>(data: T): QueryLike<T> {
  return { data, isLoading: false, isError: false, error: null };
}

const FIXTURE: DashboardSummaryResponse = {
  heartbeat: Array.from({ length: 24 }, (_, h) => ({
    hour: h,
    steps: h >= 9 && h <= 17 ? 3 + (h % 5) : 0,
    failed: h === 15 ? 1 : 0,
    tier: h === 15 ? 'warn' : 'ok',
  })),
  narrative_counts: {
    completed_today: 18,
    failed_today: 3,
    escalated_open: 2,
    kb_added_today: 2,
    agents_active_now: 3,
    spend_today_usd: 4.18,
  },
  escalations: [
    {
      task_id: 'TASK-548',
      agent: 'engineering_head',
      team: 'engineering',
      question: 'Approve gh release create for v0.41?',
      raised_at: '2026-05-30T11:48:00Z',
      age_seconds: 720,
    },
    {
      task_id: 'TASK-549',
      agent: 'qa_engineer',
      team: 'engineering',
      question:
        'Photo licensing for guide 2day-foodie-itinerary — vendor unclear.',
      raised_at: '2026-05-30T11:00:00Z',
      age_seconds: 3600,
    },
  ],
  active_by_team: [
    { team: 'engineering', count: 2, task_ids: ['TASK-555', 'TASK-553'] },
    { team: 'content', count: 1, task_ids: ['TASK-552'] },
  ],
  recent_activity: [
    {
      timestamp: '2026-05-30T11:42:12Z',
      who: 'senior_dev',
      event_kind: 'completion_report',
      task_id: 'TASK-555',
      verdict: 'ok',
    },
  ],
  updates_this_week: [
    {
      marker: 'add',
      text: 'KB +1',
      meta: 'photo-attribution-required',
      timestamp: '2026-05-30T10:42:00Z',
    },
  ],
  org_pulse: [
    {
      team: 'engineering',
      acceptance_pct: 87,
      trend_delta: -3,
      sparkline: [
        0.94, 0.92, 0.88, 0.86, 0.84, 0.82, 0.84, 0.86, 0.88, 0.86, 0.87, 0.87,
      ],
      members: 5,
      lead: 'engineering_head',
    },
    {
      team: 'content',
      acceptance_pct: 97,
      trend_delta: 1,
      sparkline: [
        0.93, 0.94, 0.95, 0.96, 0.96, 0.97, 0.96, 0.97, 0.97, 0.97, 0.97, 0.97,
      ],
      members: 2,
      lead: 'content_manager',
    },
  ],
  org_age_days: 14,
  server_now: '2026-05-30T12:00:00Z',
};

export const mockDashboardApi: DashboardApi = {
  useDashboardSummary: () => ok(FIXTURE),
};

import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

// Mock the metrics hooks so the page renders deterministically (no network).
vi.mock('@/hooks/metrics', async () => {
  const actual = await vi.importActual<typeof import('@/hooks/metrics')>('@/hooks/metrics');
  return { ...actual, useMetrics: vi.fn(), useMetricsHistory: vi.fn() };
});

import { useMetrics, useMetricsHistory } from '@/hooks/metrics';
import { HealthPage, fmtUptime, fmtMs, fmtRelTime } from './HealthPage';

const asQuery = <T,>(over: Partial<{ data: T; isLoading: boolean; isError: boolean }>) => ({
  data: undefined,
  isLoading: false,
  isError: false,
  ...over,
});

describe('health formatters (honesty fence)', () => {
  it('fmtUptime renders coarse human duration', () => {
    expect(fmtUptime(0)).toBe('0s');
    expect(fmtUptime(42)).toBe('42s');
    expect(fmtUptime(5 * 60 + 12)).toBe('5m 12s');
    expect(fmtUptime(3 * 3600 + 14 * 60)).toBe('3h 14m');
    expect(fmtUptime(2 * 86400 + 3 * 3600)).toBe('2d 3h');
  });

  it('fmtMs converts latency seconds to ms and renders em dash for null', () => {
    expect(fmtMs(null)).toBe('—');
    expect(fmtMs(undefined)).toBe('—');
    expect(fmtMs(0.0123)).toBe('12.3 ms');
    expect(fmtMs(0.25)).toBe('250 ms');
  });

  it('fmtRelTime renders compact relative time', () => {
    const now = Date.parse('2026-07-09T00:00:00Z');
    expect(fmtRelTime('2026-07-09T00:00:00Z', now)).toBe('0s ago');
    expect(fmtRelTime('2026-07-08T23:59:30Z', now)).toBe('30s ago');
    expect(fmtRelTime('2026-07-08T23:55:00Z', now)).toBe('5m ago');
    expect(fmtRelTime('not-a-date', now)).toBe('not-a-date');
  });
});

describe('HealthPage', () => {
  it('renders live summary values from the snapshot', () => {
    vi.mocked(useMetrics).mockReturnValue(
      asQuery({
        data: {
          uptime_seconds: 3 * 3600 + 14 * 60,
          loops: {
            work_hours_scheduler_loop: {
              last_tick_iso: '2026-07-09T00:00:00Z',
              interval_seconds: 60,
              last_duration_seconds: 0.012,
            },
          },
          http: {
            __all__: { count: 120, p50: 0.01, p95: 0.05, max: 0.2 },
            'GET /api/v1/metrics': { count: 3, p50: 0.008, p95: 0.008, max: 0.008 },
          },
          tasks: { pending_and_in_flight: 4 },
          jobs_in_flight: 2,
          executor_sessions_active: 1,
          run_step_queue_depth: 7,
        },
      }) as ReturnType<typeof useMetrics>,
    );
    vi.mocked(useMetricsHistory).mockReturnValue(
      asQuery({ data: [] }) as ReturnType<typeof useMetricsHistory>,
    );

    render(<HealthPage />);

    expect(screen.getByText('Runtime Health')).toBeInTheDocument();
    expect(screen.getByText('3h 14m')).toBeInTheDocument(); // uptime
    expect(screen.getByText('All routes')).toBeInTheDocument(); // aggregate http row
    expect(screen.getByText('work_hours_scheduler_loop')).toBeInTheDocument();
  });

  it('shows the empty-history affordance when no snapshots persisted', () => {
    vi.mocked(useMetrics).mockReturnValue(
      asQuery({ isLoading: true }) as ReturnType<typeof useMetrics>,
    );
    vi.mocked(useMetricsHistory).mockReturnValue(
      asQuery({ data: [] }) as ReturnType<typeof useMetricsHistory>,
    );

    render(<HealthPage />);
    expect(screen.getByText(/No persisted snapshots/i)).toBeInTheDocument();
  });
});

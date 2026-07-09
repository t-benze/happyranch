/**
 * Read hooks for the Runtime Health surface (Slice 10, THR-061 / #302).
 *
 * The metrics routes are daemon-GLOBAL (not org-scoped), so these hooks take
 * no slug. Compositions import from here — never from `@/lib/api/*` directly
 * (the cross-boundary lint forbids it).
 */
import { useQuery } from '@tanstack/react-query';
import { metrics as metricsApi } from '@/lib/api';
import {
  parseSnapshotRow,
  type MetricsSnapshot,
  type MetricsHistoryQuery,
  type ParsedHistoryRow,
} from '@/lib/api/metrics';
import type { QueryLike } from '@/design-system/providers/DataContext';

export type {
  MetricsSnapshot,
  LoopStats,
  HttpRouteStats,
  ParsedHistoryRow,
  MetricsHistoryQuery,
} from '@/lib/api/metrics';

/** Live daemon snapshot. Polls so the cockpit stays fresh without a reload. */
export function useMetrics(): QueryLike<MetricsSnapshot> {
  return useQuery({
    queryKey: ['metrics', 'live'],
    queryFn: () => metricsApi.getMetrics(),
    refetchInterval: 15_000,
    staleTime: 10_000,
  }) as QueryLike<MetricsSnapshot>;
}

/** Persisted snapshot history, parsed and returned newest-first (as the route
 *  serves it). Consumers reverse to chronological for trend rendering. */
export function useMetricsHistory(
  params: MetricsHistoryQuery = {},
): QueryLike<ParsedHistoryRow[]> {
  const { since, until, limit } = params;
  return useQuery({
    queryKey: ['metrics', 'history', since ?? null, until ?? null, limit ?? null],
    queryFn: async () => {
      const res = await metricsApi.getMetricsHistory(params);
      return res.snapshots.map(parseSnapshotRow);
    },
    staleTime: 30_000,
  }) as QueryLike<ParsedHistoryRow[]>;
}

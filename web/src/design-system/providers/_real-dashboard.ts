/**
 * Real (daemon-backed) implementation of `DashboardApi`.
 *
 * Single `useDashboardSummary` hook backed by GET /dashboard/summary. No
 * polling — refetches on mount and explicit refetch only. staleTime 30s
 * keeps the summary warm during quick tab-switches per
 * docs/superpowers/specs/2026-05-30-dashboard-overhaul-design.md §3.4.
 *
 * Note: the Dashboard's escalation-resolve flow consumes the existing
 * `useResolveEscalation` hook on TasksApi, not a new dashboard-local one.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { dashboard as dashboardApi } from '@/lib/api';
import type { DashboardSummaryResponse } from '@/lib/api/types';
import { assertStartupAdmitted } from '@/lib/startup';
import type { DashboardApi, QueryLike } from './DataContext';
import { useRealStartup } from './_real-health';

function useRealOrgSlug(): string {
  const { slug } = useParams<{ slug: string }>();
  return slug ?? '';
}

function useDashboardSummary(): QueryLike<DashboardSummaryResponse> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  // The always-mounted Sidebar reads this too; it is held until startup is
  // positively ready (THR-308).
  const { admitted } = useRealStartup();
  return useQuery({
    queryKey: ['dashboard-summary', slug],
    queryFn: () => {
      assertStartupAdmitted(qc);
      return dashboardApi.getDashboardSummary(slug);
    },
    enabled: !!slug && admitted,
    staleTime: 30_000,
  }) as QueryLike<DashboardSummaryResponse>;
}

export const realDashboardApi: DashboardApi = {
  useDashboardSummary,
};

/**
 * Usage v1 read queries. Thin react-query wrappers over the read-only
 * `GET /usage/workload` and `GET /usage/efficiency` routes; the responses are
 * rendered as-is (no client-side joins, windows or deltas).
 */
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { usage as usageApi } from '@/lib/api';
import { useOrgSlugOptional } from '@/lib/orgSlug';

export type WorkloadResponse = usageApi.WorkloadResponse;
export type WorkloadAgent = usageApi.WorkloadAgent;
export type WorkloadPeriod = usageApi.WorkloadPeriod;
export type EfficiencyResponse = usageApi.EfficiencyResponse;
export type EfficiencyRow = usageApi.EfficiencyRow;
export type EfficiencyPeriod = usageApi.EfficiencyPeriod;
export type EfficiencyRunType = usageApi.EfficiencyRunType;
export type CohortOption = usageApi.CohortOption;
export type UsageDelta = usageApi.UsageDelta;
export type TokenMetric = usageApi.TokenMetric;
export type DeclineWaste = usageApi.DeclineWaste;
export type UnattributedCounts = usageApi.UnattributedCounts;

/** One selected Efficiency cohort: exactly one CLI and one model choice. */
export interface CohortSelection {
  executor: string;
  /** `null` selects the distinct "CLI default (not pinned)" cohort. */
  model: string | null;
}

function useSlug(): string {
  const { slug: routeSlug } = useParams<{ slug: string }>();
  const ctxSlug = useOrgSlugOptional();
  return routeSlug ?? ctxSlug ?? '';
}

export function useWorkload(compare: boolean) {
  const slug = useSlug();
  return useQuery({
    queryKey: ['usage', slug, 'workload', compare],
    queryFn: () => usageApi.getWorkload(slug, compare),
    enabled: !!slug,
  });
}

/**
 * Cohort options (and nothing else) — the unselected Efficiency read. Keeps the
 * previous option list while a Compare toggle refetches so the selectors do not
 * collapse; it never feeds figures.
 */
export function useEfficiencyOptions(compare: boolean) {
  const slug = useSlug();
  return useQuery({
    queryKey: ['usage', slug, 'efficiency-options', compare],
    queryFn: () => usageApi.getEfficiency(slug, { compare }),
    enabled: !!slug,
    placeholderData: keepPreviousData,
  });
}

export function useEfficiency(selection: CohortSelection | null, compare: boolean) {
  const slug = useSlug();
  return useQuery({
    queryKey: [
      'usage', slug, 'efficiency', compare,
      selection?.executor ?? null, selection?.model ?? null,
    ],
    queryFn: () =>
      usageApi.getEfficiency(slug, {
        compare,
        executor: selection!.executor,
        ...(selection!.model === null
          ? { model_unpinned: true }
          : { model: selection!.model }),
      }),
    enabled: !!slug && selection !== null,
  });
}

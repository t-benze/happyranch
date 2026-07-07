/**
 * Token-usage read hooks.
 *
 * The "Top token threads (window)" dashboard panel is a self-contained,
 * read-only card that fetches its OWN data over the existing
 * `GET /tokens?group_by=thread` route — it deliberately does NOT ride on
 * `DashboardSummaryResponse` (no server round change, no aggregate growth).
 *
 * Unlike the provider-swapped feature hooks, this one talks to the API
 * directly: the dashboard is never mounted in a prototype sandbox, so it
 * needs no mock/real DataContext split. Sort-by-churn + slice-to-N is a
 * presentation concern done in the component (see ./features/dashboard/
 * topTokens.ts), not on the route.
 */
import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { tokens as tokensApi } from '@/lib/api';
import { useOrgSlugOptional } from '@/lib/orgSlug';
import type { TokenUsageRollup } from '@/lib/api/tokens';
import type { QueryLike } from '@/design-system/providers/DataContext';

/**
 * Shared compact token formatter (e.g. 26_500_000 -> '26.5M'). It is defined
 * in the audit feature, but the cross-feature import boundary forbids one
 * feature from reaching into another's modules directly — the sanctioned
 * channel is "through @/hooks/" (eslint.config.js). Surfacing it here lets the
 * dashboard "Tokens today" tile reuse the exact formatter rather than
 * hand-rolling one (THR-030 HOME-04).
 */
export { formatTokens } from '@/features/audit/audit-narrative';

export function useTopThreadTokens(params?: {
  since?: string;
}): QueryLike<TokenUsageRollup[]> {
  const { slug: routeSlug } = useParams<{ slug: string }>();
  const ctxSlug = useOrgSlugOptional();
  const slug = routeSlug ?? ctxSlug ?? '';
  const since = params?.since;
  return useQuery({
    queryKey: ['tokens', slug, 'thread', since ?? null],
    queryFn: async () => {
      const res = await tokensApi.listTokens(slug, {
        group_by: 'thread',
        ...(since ? { since } : {}),
      });
      return 'rollup' in res ? res.rollup : [];
    },
    enabled: !!slug,
    staleTime: 30_000,
  }) as QueryLike<TokenUsageRollup[]>;
}

/**
 * Total tokens recorded since a `since` boundary — powers the dashboard
 * "Tokens today" tile (THR-030 HOME-04).
 *
 * Sums `total_tokens` across the by-agent rollup of the existing
 * `GET /tokens?group_by=agent` route — the same server-side aggregate the
 * Usage hero totals ride (see ./usage.ts). A rollup (bounded, one row per
 * agent) is used rather than the raw per-session listing because the latter
 * is capped by `limit` and would silently UNDERCOUNT; the rollup gives the
 * honest, untruncated total with no DashboardSummaryResponse change.
 *
 * `since` is optional so the dashboard can call the hook unconditionally
 * (rules-of-hooks) before `server_now` is known; the query stays disabled
 * until a boundary is supplied.
 */
export function useTokensToday(params: { since?: string }): QueryLike<number> {
  const { slug: routeSlug } = useParams<{ slug: string }>();
  const ctxSlug = useOrgSlugOptional();
  const slug = routeSlug ?? ctxSlug ?? '';
  const since = params.since;
  return useQuery({
    queryKey: ['tokens', slug, 'today-total', since ?? null],
    queryFn: async () => {
      const res = await tokensApi.listTokens(slug, {
        group_by: 'agent',
        ...(since ? { since } : {}),
      });
      const rollup = 'rollup' in res ? res.rollup : [];
      return rollup.reduce((sum, r) => sum + r.total_tokens, 0);
    },
    enabled: !!slug && !!since,
    staleTime: 30_000,
  }) as QueryLike<number>;
}

/**
 * Total tokens recorded since a `since` boundary, scoped to a 7-day window by
 * the caller — powers the dashboard "This week's burn" rail card (THR-030
 * HOME-06).
 *
 * A thin variant of {@link useTokensToday}: the identical
 * `GET /tokens?group_by=agent` rollup summed over `total_tokens`, differing
 * only in the window the caller supplies via `since` (this-week vs today) and
 * the query-key label. No DashboardSummaryResponse / web-contract change — a
 * client-side window aggregation of already-exposed data, the same source the
 * "Tokens today" tile and Top-token-threads card already ride.
 *
 * `since` is optional so the dashboard can call the hook unconditionally
 * (rules-of-hooks) before `server_now` is known; the query stays disabled
 * until a boundary is supplied.
 */
export function useTokensWeek(params: { since?: string }): QueryLike<number> {
  const { slug: routeSlug } = useParams<{ slug: string }>();
  const ctxSlug = useOrgSlugOptional();
  const slug = routeSlug ?? ctxSlug ?? '';
  const since = params.since;
  return useQuery({
    queryKey: ['tokens', slug, 'week-total', since ?? null],
    queryFn: async () => {
      const res = await tokensApi.listTokens(slug, {
        group_by: 'agent',
        ...(since ? { since } : {}),
      });
      const rollup = 'rollup' in res ? res.rollup : [];
      return rollup.reduce((sum, r) => sum + r.total_tokens, 0);
    },
    enabled: !!slug && !!since,
    staleTime: 30_000,
  }) as QueryLike<number>;
}

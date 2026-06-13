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

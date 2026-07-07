/**
 * Read hooks for the Usage surface (§4.7).
 *
 * Each hook fetches a single group_by rollup for a given window (`since`).
 * The UsagePage aggregates the hero totals from the agent rollup (or thread
 * rollup) and renders the breakdowns via segmented control.
 *
 * Churn invariant: `total_tokens = input + output + reasoning`.
 * Cache reads are a separate column, never folded into total.
 */
import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { tokens as tokensApi } from '@/lib/api';
import { useOrgSlugOptional } from '@/lib/orgSlug';
import type { TokenUsageRollup } from '@/lib/api/tokens';
import type { QueryLike } from '@/design-system/providers/DataContext';

export type { TokenUsageRollup };

function useSlug(): string {
  const { slug: routeSlug } = useParams<{ slug: string }>();
  const ctxSlug = useOrgSlugOptional();
  return routeSlug ?? ctxSlug ?? '';
}

export function useUsageByAgent(params: {
  since?: string;
}): QueryLike<TokenUsageRollup[]> {
  const slug = useSlug();
  const since = params?.since;
  return useQuery({
    queryKey: ['tokens', slug, 'agent', since ?? null],
    queryFn: async () => {
      const res = await tokensApi.listTokens(slug, {
        group_by: 'agent',
        ...(since ? { since } : {}),
      });
      return 'rollup' in res ? res.rollup : [];
    },
    enabled: !!slug,
    staleTime: 30_000,
  }) as QueryLike<TokenUsageRollup[]>;
}

export function useUsageByThread(params: {
  since?: string;
}): QueryLike<TokenUsageRollup[]> {
  const slug = useSlug();
  const since = params?.since;
  return useQuery({
    queryKey: ['tokens', slug, 'thread', since ?? null, 'usage'],
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

export function useUsageByModel(params: {
  since?: string;
}): QueryLike<TokenUsageRollup[]> {
  const slug = useSlug();
  const since = params?.since;
  return useQuery({
    queryKey: ['tokens', slug, 'model', since ?? null],
    queryFn: async () => {
      const res = await tokensApi.listTokens(slug, {
        group_by: 'model',
        ...(since ? { since } : {}),
      });
      return 'rollup' in res ? res.rollup : [];
    },
    enabled: !!slug,
    staleTime: 30_000,
  }) as QueryLike<TokenUsageRollup[]>;
}

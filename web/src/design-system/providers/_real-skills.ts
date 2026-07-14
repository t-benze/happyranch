/**
 * Real (daemon-backed) implementation of `SkillsApi` (THR-092 Slice 1).
 *
 * Single `useSkillsCatalog` read backed by GET /orgs/:slug/skills/catalog.
 * The Bundled / Custom filter forwards to the daemon `?filter=` param; `all`
 * sends no param. Delegates to the shared `@/lib/api/skills` client — this
 * provider does not re-implement the fetch.
 */
import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
// The `@/lib/api` barrel does not re-export the skills client (landed in
// #421 without a barrel entry), so this provider deep-imports it directly —
// the same idiom `_real-dreams` uses for `@/lib/api/dreams`.
import { listSkillsCatalog, type CatalogSkillItem } from '@/lib/api/skills';
import type { QueryLike, SkillsApi } from './DataContext';

function useRealOrgSlug(): string {
  const { slug } = useParams<{ slug: string }>();
  return slug ?? '';
}

function useSkillsCatalog(params?: {
  filter?: 'Bundled' | 'Custom';
}): QueryLike<{ items: CatalogSkillItem[] }> {
  const slug = useRealOrgSlug();
  const filter = params?.filter;
  return useQuery({
    queryKey: ['skills-catalog', slug, filter ?? 'all'],
    queryFn: () => listSkillsCatalog(slug, filter ? { filter } : undefined),
    enabled: !!slug,
    staleTime: 30_000,
  }) as QueryLike<{ items: CatalogSkillItem[] }>;
}

export const realSkillsApi: SkillsApi = {
  useSkillsCatalog,
};

/**
 * Real (daemon-backed) implementation of `SkillsApi` (THR-092 Slices 1–2).
 *
 * - `useSkillsCatalog` → GET /orgs/:slug/skills/catalog (Bundled/Custom filter
 *   forwards to the daemon `?filter=`; `all` sends no param).
 * - `useSkillDetail`   → GET /orgs/:slug/skills/catalog/:skill_id (source,
 *   validation, per-agent assignments[]) — backs the Slice-2 detail surface.
 *
 * Delegates to the shared `@/lib/api/skills` client — this provider does not
 * re-implement the fetch.
 */
import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
// The `@/lib/api` barrel does not re-export the skills client (landed in
// #421 without a barrel entry), so this provider deep-imports it directly —
// the same idiom `_real-dreams` uses for `@/lib/api/dreams`.
import {
  getSkillCatalogDetail,
  listSkillsCatalog,
  type CatalogSkillItem,
  type SkillDetail,
} from '@/lib/api/skills';
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

function useSkillDetail(
  skillId: string | undefined,
): QueryLike<SkillDetail> {
  const slug = useRealOrgSlug();
  return useQuery({
    queryKey: ['skill-detail', slug, skillId],
    queryFn: () => getSkillCatalogDetail(slug, skillId as string),
    enabled: !!slug && !!skillId,
    staleTime: 30_000,
  }) as QueryLike<SkillDetail>;
}

export const realSkillsApi: SkillsApi = {
  useSkillsCatalog,
  useSkillDetail,
};

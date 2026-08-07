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
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
// The `@/lib/api` barrel does not re-export the skills client (landed in
// #421 without a barrel entry), so this provider deep-imports it directly —
// the same idiom `_real-dreams` uses for `@/lib/api/dreams`.
import {
  assignSkill,
  createSkill,
  editSkill,
  getSkillCatalogDetail,
  getSkillStatus,
  listSkillsCatalog,
  listSkillValidation,
  validateSkill,
  type AssignSkillRequest,
  type AssignSkillResponse,
  type CatalogSkillItem,
  type CreateSkillRequest,
  type CreateSkillResponse,
  type EditSkillRequest,
  type EditSkillResponse,
  type SkillDetail,
  type SkillStatusResponse,
  type ValidateSkillResponse,
  type ValidationEvent,
} from '@/lib/api/skills';
import type { MutationLike, QueryLike, SkillsApi } from './DataContext';

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

// A content-validation failure is NOT an error — the daemon persists an
// editable draft and returns `201`/`200` with `validation.ok=false` (spec v3
// §9.1a). So these mutations resolve on that path; only a malformed request
// (422) or transport error rejects. On any persist we invalidate the catalog
// (a new/updated draft appears) and the skill's own detail query.
function useCreateSkill(): MutationLike<CreateSkillRequest, CreateSkillResponse> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateSkillRequest) => createSkill(slug, body),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ['skills-catalog', slug] });
      qc.invalidateQueries({ queryKey: ['skill-detail', slug, res.skill_id] });
    },
  });
}

function useValidateSkill(): MutationLike<
  { skillId: string },
  ValidateSkillResponse
> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ skillId }: { skillId: string }) =>
      validateSkill(slug, skillId),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ['skills-catalog', slug] });
      qc.invalidateQueries({ queryKey: ['skill-detail', slug, res.skill_id] });
    },
  });
}

// Edit a user-authored skill (PATCH). Same draft-persist-on-content-failure
// contract as create: a content-validation failure resolves (spec v3 §9.5);
// only a 422/transport error rejects. On any persist we invalidate the catalog
// and the skill's own detail query so the new version/state is picked up.
function useEditSkill(): MutationLike<
  { skillId: string; body: EditSkillRequest },
  EditSkillResponse
> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ skillId, body }: { skillId: string; body: EditSkillRequest }) =>
      editSkill(slug, skillId, body),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ['skills-catalog', slug] });
      qc.invalidateQueries({ queryKey: ['skill-detail', slug, res.skill_id] });
    },
  });
}

// Per-agent assignment status for one skill (Slice-5) — the authoritative
// assignment source that drives the custom-skill assignment table.
function useSkillStatus(
  skillId: string | undefined,
): QueryLike<SkillStatusResponse> {
  const slug = useRealOrgSlug();
  return useQuery({
    queryKey: ['skill-status', slug, skillId],
    queryFn: () => getSkillStatus(slug, skillId as string),
    enabled: !!slug && !!skillId,
    staleTime: 30_000,
  }) as QueryLike<SkillStatusResponse>;
}

// Assign / unassign one skill for one agent (Slice-5). Commits one queued
// config-review change. On success we invalidate the skill's status (the table
// re-derives), its detail (assignments[] rollup), and the catalog rollups.
function useAssignSkill(): MutationLike<
  { agentId: string; skillId: string; body: AssignSkillRequest },
  AssignSkillResponse
> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      agentId,
      skillId,
      body,
    }: {
      agentId: string;
      skillId: string;
      body: AssignSkillRequest;
    }) => assignSkill(slug, agentId, skillId, body),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ['skill-status', slug, res.skill_id] });
      qc.invalidateQueries({ queryKey: ['skill-detail', slug, res.skill_id] });
      qc.invalidateQueries({ queryKey: ['skills-catalog', slug] });
    },
  });
}

// Runtime Validation event list (Slice-6) — GET /orgs/:slug/skills/validation.
// Read-only; the filter params map straight through to the daemon query. The
// query key carries the params object so switching filters refetches; an empty
// (undefined) param set shares a key with the page's unfiltered options query,
// so React Query dedupes them into a single fetch.
function useSkillValidation(params?: {
  skill?: string;
  agent?: string;
  source?: string;
  since?: string;
  severity?: string;
  limit?: number;
}): QueryLike<{ events: ValidationEvent[]; label: string }> {
  const slug = useRealOrgSlug();
  return useQuery({
    queryKey: ['skill-validation', slug, params ?? null],
    queryFn: () => listSkillValidation(slug, params),
    enabled: !!slug,
    staleTime: 30_000,
  }) as QueryLike<{ events: ValidationEvent[]; label: string }>;
}

export const realSkillsApi: SkillsApi = {
  useSkillsCatalog,
  useSkillDetail,
  useCreateSkill,
  useValidateSkill,
  useEditSkill,
  useSkillStatus,
  useAssignSkill,
  useSkillValidation,
};

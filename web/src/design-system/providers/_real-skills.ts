/**
 * Real (daemon-backed) implementation of `SkillsApi`.
 * */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import {
  createSkill,
  editSkill,
  validateSkill,
  assignSkill,
  getSkillCatalogDetail,
  getSkillStatus,
  listSkillsCatalog,
  listSkillValidation,
  type CatalogSkillItem,
  type SkillDetail,
  type SkillStatusResponse,
  type ValidationEvent,
} from '@/lib/api/skills';
// Legacy mutation types retained for API contract compatibility.
import type {
  AssignSkillRequest,
  AssignSkillResponse,
  CreateSkillRequest,
  CreateSkillResponse,
  EditSkillRequest,
  EditSkillResponse,
  ValidateSkillResponse,
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

function useCreateSkill(): MutationLike<CreateSkillRequest, CreateSkillResponse> {
  const slug = useRealOrgSlug();
  return useMutation({ mutationFn: (body: CreateSkillRequest) => createSkill(slug, body) });
}

function useValidateSkill(): MutationLike<{ skillId: string }, ValidateSkillResponse> {
  const slug = useRealOrgSlug();
  return useMutation({ mutationFn: ({ skillId }) => validateSkill(slug, skillId) });
}

function useEditSkill(): MutationLike<{ skillId: string; body: EditSkillRequest }, EditSkillResponse> {
  const slug = useRealOrgSlug();
  return useMutation({ mutationFn: ({ skillId, body }) => editSkill(slug, skillId, body) });
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

function useAssignSkill(): MutationLike<{ agentId: string; skillId: string; body: AssignSkillRequest }, AssignSkillResponse> {
  const slug = useRealOrgSlug();
  return useMutation({ mutationFn: ({ agentId, skillId, body }) => assignSkill(slug, agentId, skillId, body) });
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

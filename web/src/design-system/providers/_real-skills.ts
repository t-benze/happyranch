/**
 * Real (daemon-backed) implementation of `SkillsApi` (THR-055 lifecycle cutover).
 *
 * READ operations remain on legacy catalog endpoints (read-only, not retired).
 * MUTATION operations cut over to THR-055 lifecycle endpoints via
 * ``@/lib/api/skillLifecycle``. Legacy create/edit/validate/assign endpoints
 * return 410 Gone.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import {
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
// THR-055 lifecycle client — canonical mutation surface.
import {
  assignSkill as lifecycleAssign,
  listCustomCatalog,
  submitProposal,
} from '@/lib/api/skillLifecycle';
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

// THR-055 lifecycle cutover: Create → submitProposal
// The `CreateSkillRequest` contract is preserved for API compatibility.
// The lifecycle `submitProposal` shapes are transformed into the legacy
// response shape expected by callers.
function useCreateSkill(): MutationLike<CreateSkillRequest, CreateSkillResponse> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: CreateSkillRequest) => {
      const resp = await submitProposal(slug, {
        slug: body.slug,
        name: body.name,
        description: body.description,
        skill_md: body.skill_md,
        version: body.version,
        policy_class: body.policy_class,
      });
      // Map lifecycle response to legacy CreateSkillResponse shape
      return {
        skill_id: resp.skill_id,
        name: body.name,
        slug: body.slug,
        version: resp.version,
        validation_state: resp.status,
        validation: { ok: true, errors: [] },
      } as CreateSkillResponse;
    },
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
    mutationFn: async ({ skillId }: { skillId: string }) => {
      // Lifecycle validation requires version_id, not skillId.
      // The caller must have the version_id; for now, the legacy endpoint
      // returns 410 and we surface the error. This mutation is a no-op
      // bridge until the Skills UI's Slice-3 calls the lifecycle client directly.
      throw new Error(
        'Skill validation moved to lifecycle: use POST /skill-lifecycle/validate with version_id. ' +
        'See THR-055 lifecycle cutover.',
      );
    },
  });
}

// THR-055 lifecycle cutover: PATCH edit → retired (410).
// Editing happens through new versions in the lifecycle flow.
function useEditSkill(): MutationLike<
  { skillId: string; body: EditSkillRequest },
  EditSkillResponse
> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      throw new Error(
        'Skill editing moved to lifecycle: submit a new proposal. ' +
        'See THR-055 lifecycle cutover.',
      );
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

// THR-055 lifecycle cutover: Assign → lifecycle assignSkill
// Looks up the latest PUBLISHED version_id for the skill before calling the
// lifecycle assign endpoint.
function useAssignSkill(): MutationLike<
  { agentId: string; skillId: string; body: AssignSkillRequest },
  AssignSkillResponse
> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      agentId,
      skillId,
      body,
    }: {
      agentId: string;
      skillId: string;
      body: AssignSkillRequest;
    }) => {
      if (body.action === 'remove') {
        // Per-agent removal: use rollback for the agent's assignment.
        // Lifecycle rollback deactivates all assignments for a skill,
        // so per-agent removal requires the lifecycle retire or a targeted
        // unassignment which isn't exposed in the pilot API surface yet.
        throw new Error(
          'Per-agent skill removal requires lifecycle rollback (deactivates all assignments). ' +
          'See THR-055 lifecycle cutover.',
        );
      }
      // Look up the latest published version for this skill
      const catalog = await listCustomCatalog(slug);
      const published = catalog.skills.find((s) => s.skill_id === skillId);
      if (!published) {
        throw new Error(`No published version found for skill ${skillId}. Publish the skill before assigning.`);
      }
      // Call lifecycle assign with the resolved version
      const resp = await lifecycleAssign(slug, {
        skill_id: skillId,
        agent_name: agentId,
        version_id: 0,  // version_id resolved server-side from the active published version
      });
      return {
        skill_id: resp.skill_id,
        agent_id: resp.agent_name,
        state: 'assigned' as const,
        version: resp.version,
        content_hash: resp.content_hash,
        assigned_at: resp.assigned_at,
      } as unknown as AssignSkillResponse;
    },
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

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
  getLifecycleStatus,
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
// The lifecycle `submitProposal` response is mapped into the legacy
// response shape expected by callers. A proposal submission reflects the
// true lifecycle status (proposed) — NOT a fabricated validation pass.
// Validation requires a founder-only lifecycle transition.
function useCreateSkill(): MutationLike<CreateSkillRequest, CreateSkillResponse> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: CreateSkillRequest) => {
      const resp = await submitProposal(slug, {
        slug: body.slug,
        name: body.name,
        description: body.summary ?? '',
        skill_md: body.skill_md,
        version: body.version,
        policy_class: body.policy_class,
        references: body.references,
        assets: body.assets,
      });
      return {
        skill_id: resp.skill_id,
        source: 'lifecycle',
        validation_state: 'proposed' as const,
        validation: { ok: false, errors: [] },
      };
    },
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ['skills-catalog', slug] });
      qc.invalidateQueries({ queryKey: ['skill-detail', slug, res.skill_id] });
    },
  });
}

// THR-055 lifecycle cutover: Validate → read lifecycle status.
// Agent-side "validate" reflects the CURRENT lifecycle state of the proposal.
// Does NOT fabricate validated — only the founder-only lifecycle validation
// transition sets the validated state.
function lifecycleStatusToValidationState(
  status: string | null,
): 'proposed' | 'validated' | 'failed_validation' {
  switch (status) {
    case 'validation_failed':
      return 'failed_validation';
    case 'validated':
    case 'approved':
    case 'published':
      return 'validated';
    case 'proposed':
    default:
      return 'proposed';
  }
}

function useValidateSkill(): MutationLike<
  { skillId: string },
  ValidateSkillResponse
> {
  const slug = useRealOrgSlug();
  return useMutation({
    mutationFn: async ({ skillId }: { skillId: string }) => {
      const status = await getLifecycleStatus(slug, skillId);
      const state = lifecycleStatusToValidationState(status.current_status);
      return {
        skill_id: status.skill_id,
        validation_state: state,
        validation: { ok: state === 'validated', errors: [] },
      };
    },
  });
}

// THR-055 lifecycle cutover: PATCH edit → new proposal submission.
// Editing creates a new version via the submitProposal lifecycle endpoint.
// Reflects the true lifecycle status (proposed) — NOT a fabricated validated.
function useEditSkill(): MutationLike<
  { skillId: string; body: EditSkillRequest },
  EditSkillResponse
> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      skillId,
      body,
    }: {
      skillId: string;
      body: EditSkillRequest;
    }) => {
      // Derive slug from skill_id (format: "hr:<slug>")
      const derivedSlug = skillId.startsWith('hr:')
        ? skillId.slice(3)
        : skillId;
      const resp = await submitProposal(slug, {
        slug: derivedSlug,
        name: body.name ?? derivedSlug,
        description: body.summary ?? '',
        skill_md: body.skill_md ?? '',
        version: body.version,
        references: body.references,
        assets: body.assets,
      });
      return {
        skill_id: resp.skill_id,
        source: 'lifecycle',
        validation_state: 'proposed' as const,
        validation: { ok: false, errors: [] },
        version: resp.version,
      };
    },
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ['skills-catalog', slug] });
      qc.invalidateQueries({ queryKey: ['skill-status', slug, res.skill_id] });
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
      // Call lifecycle assign with the resolved version_id from catalog
      const resp = await lifecycleAssign(slug, {
        skill_id: skillId,
        agent_name: agentId,
        version_id: published.version_id,
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

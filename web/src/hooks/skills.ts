/**
 * Public, provider-aware skills hooks. Mirrors `useData().skills` so
 * compositions never reach into `design-system/providers/` directly (same
 * seam as `@/hooks/audit`).
 *
 * Also the single sanctioned re-export point for the skills row/detail types:
 * `features/*` may not deep-import `@/lib/api/skills` (eslint
 * no-restricted-imports), so the Skills compositions take the types from here.
 *
 * THR-055: Proposal Detail hook owns TanStack Query — ProposalDetailPage
 * consumes this hook and must not directly import getProposalDetail,
 * ApiError, or useQuery.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useData } from '@/design-system/providers/DataContext';
import {
  claimProposalV2,
  getProposalDetail,
  reviewProposal,
  submitReviewProposal,
  validateProposal,
  type ClaimProposalV2Request,
  type ReviewProposalRequest,
  type SubmitReviewProposalRequest,
  type ValidateProposalRequest,
} from '@/lib/api/skillLifecycle';

export type {
  AssignSkillRequest,
  AssignSkillResponse,
  CatalogSkillItem,
  CreateSkillRequest,
  CreateSkillResponse,
  EditSkillRequest,
  EditSkillResponse,
  SkillDetail,
  SkillStatusAssignment,
  SkillStatusResponse,
  ValidateSkillResponse,
  ValidationEvent,
} from '@/lib/api/skills';

export type { ProposalDetailResponse } from '@/lib/api/skillLifecycle';

// Re-export proposal queue types for feature consumption (THR-055 Slice 3A).
export type {
  ProposalQueueItem,
  ProposalQueueResponse,
} from '@/lib/api/skillLifecycle';

export const useSkillsCatalog: ReturnType<
  typeof useData
>['skills']['useSkillsCatalog'] = (params) =>
  useData().skills.useSkillsCatalog(params);

export const useSkillDetail: ReturnType<
  typeof useData
>['skills']['useSkillDetail'] = (skillId) =>
  useData().skills.useSkillDetail(skillId);

export const useCreateSkill: ReturnType<
  typeof useData
>['skills']['useCreateSkill'] = () => useData().skills.useCreateSkill();

export const useValidateSkill: ReturnType<
  typeof useData
>['skills']['useValidateSkill'] = () => useData().skills.useValidateSkill();

export const useEditSkill: ReturnType<
  typeof useData
>['skills']['useEditSkill'] = () => useData().skills.useEditSkill();

export const useSkillStatus: ReturnType<
  typeof useData
>['skills']['useSkillStatus'] = (skillId) =>
  useData().skills.useSkillStatus(skillId);

export const useAssignSkill: ReturnType<
  typeof useData
>['skills']['useAssignSkill'] = () => useData().skills.useAssignSkill();

export const useSkillValidation: ReturnType<
  typeof useData
>['skills']['useSkillValidation'] = (params) =>
  useData().skills.useSkillValidation(params);

// ── THR-055: Proposal Detail (TanStack Query owned here) ────────────────

/**
 * Fetch a single proposal detail by org slug and version ID.
 * Returns the same QueryObserverResult shape as useQuery so the page can
 * inspect isError / isPending / error / data / refetch.
 */
export function useProposalDetail(
  slug: string | undefined,
  versionId: number | undefined,
) {
  return useQuery({
    queryKey: ['proposal-detail', slug, versionId],
    queryFn: () => getProposalDetail(slug as string, versionId as number),
    enabled: !!slug && versionId !== undefined && !Number.isNaN(versionId),
    staleTime: 30_000,
  });
}

// ── THR-136: Founder proposal review mutations ───────────────────────────

const PROPOSAL_DETAIL_QUERY_KEY = ['proposal-detail'];

function useInvalidateProposalDetail() {
  const queryClient = useQueryClient();
  return (slug: string, versionId: number) =>
    queryClient.invalidateQueries({
      queryKey: [...PROPOSAL_DETAIL_QUERY_KEY, slug, versionId],
    });
}

export function useClaimProposal() {
  const invalidate = useInvalidateProposalDetail();
  return useMutation({
    mutationFn: ({
      slug,
      versionId,
      expectedEventId,
    }: {
      slug: string;
      versionId: number;
      expectedEventId: number;
    }) => claimProposalV2(slug, versionId, { expected_event_id: expectedEventId }),
    onSuccess: (_data, variables) => {
      void invalidate(variables.slug, variables.versionId);
    },
  });
}

export function useValidateProposal() {
  const invalidate = useInvalidateProposalDetail();
  return useMutation({
    mutationFn: ({
      slug,
      versionId,
      body,
    }: {
      slug: string;
      versionId: number;
      body: ValidateProposalRequest;
    }) => validateProposal(slug, versionId, body),
    onSuccess: (_data, variables) => {
      void invalidate(variables.slug, variables.versionId);
    },
  });
}

export function useSubmitReviewProposal() {
  const invalidate = useInvalidateProposalDetail();
  return useMutation({
    mutationFn: ({
      slug,
      versionId,
      body,
    }: {
      slug: string;
      versionId: number;
      body: SubmitReviewProposalRequest;
    }) => submitReviewProposal(slug, versionId, body),
    onSuccess: (_data, variables) => {
      void invalidate(variables.slug, variables.versionId);
    },
  });
}

export function useReviewProposal() {
  const invalidate = useInvalidateProposalDetail();
  return useMutation({
    mutationFn: ({
      slug,
      versionId,
      body,
    }: {
      slug: string;
      versionId: number;
      body: ReviewProposalRequest;
    }) => reviewProposal(slug, versionId, body),
    onSuccess: (_data, variables) => {
      void invalidate(variables.slug, variables.versionId);
    },
  });
}

export { ApiError } from '@/lib/api/client';
export type {
  ClaimProposalV2Request,
  ReviewProposalRequest,
  SubmitReviewProposalRequest,
  ValidateProposalRequest,
};

// ── THR-055 Slice 3A: Proposal Queue ───────────────────────────────────

export const useProposalsQueue: ReturnType<
  typeof useData
>['skills']['useProposalsQueue'] = (params) =>
  useData().skills.useProposalsQueue(params);

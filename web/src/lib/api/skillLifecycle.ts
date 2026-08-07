/** Legacy lifecycle transport retained for non-review compatibility only. */
import { request } from './client';

export interface ProposalRequest {
  slug: string;
  name: string;
  description: string;
  skill_md: string;
  version?: string;
  policy_class?: string;
  purpose?: string;
  target_agent_suggestion?: string;
  references?: Record<string, string>;
  assets?: Record<string, string>;
}

export interface ProposalResponse {
  skill_id: string;
  version_id: number;
  version: string;
  status: string;
  content_hash: string;
  content_artifact_key: string | null;
  proposal_task_id: string | null;
}

export const submitProposal = (
  slug: string,
  body: ProposalRequest,
  params?: { task_id?: string; session_id?: string; agent_name?: string },
): Promise<ProposalResponse> =>
  request(`/orgs/${slug}/skill-lifecycle/proposals`, { method: 'POST', body, params });

export interface LifecycleStatusResponse {
  skill_id: string;
  slug: string;
  current_status: string | null;
  current_version: string | null;
  current_version_id: number | null;
  published_version: string | null;
  assignments: Array<{
    agent_name: string;
    version: string;
    content_hash: string;
    assigned_by: string;
    assigned_at: string;
    active: boolean;
  }>;
  events: Array<{
    event_type: string;
    actor: string;
    actor_role: string;
    new_status: string;
    content_hash: string | null;
    created_at: string;
  }>;
  proposal_task_id: string | null;
  proposer_agent: string | null;
}

export const getLifecycleStatus = (slug: string, skillId: string): Promise<LifecycleStatusResponse> =>
  request(`/orgs/${slug}/skill-lifecycle/${skillId}`);

export interface CustomCatalogSkill {
  version_id: number;
  skill_id: string;
  slug: string;
  name: string;
  version: string;
  description: string;
  content_hash: string;
  published_at: string | null;
  publisher: string | null;
}

export const listCustomCatalog = (slug: string): Promise<{ skills: CustomCatalogSkill[] }> =>
  request(`/orgs/${slug}/skill-lifecycle/catalog/custom`);

export interface LifecycleEventItem {
  id: number;
  event_type: string;
  actor: string;
  actor_role: string;
  previous_status: string | null;
  new_status: string;
  content_hash: string | null;
  created_at: string;
  metadata: Record<string, unknown> | null;
}

export const getLifecycleEvents = (
  slug: string,
  skillId: string,
  params?: { limit?: number },
): Promise<{ skill_id: string; events: LifecycleEventItem[] }> =>
  request(`/orgs/${slug}/skill-lifecycle/events/${skillId}`, { params });

export interface ClaimProposalRequest {
  proposal_version_id: number;
}

export interface ClaimProposalResponse {
  skill_id: string;
  version_id: number;
  status: string;
  version: string;
}

export const claimProposal = (
  slug: string,
  skillId: string,
  body: ClaimProposalRequest,
): Promise<ClaimProposalResponse> =>
  request(`/orgs/${slug}/skill-lifecycle/${skillId}/claim`, { method: 'POST', body });

export const validateVersion = (
  slug: string,
  versionId: number,
): Promise<{ skill_id: string; version_id: number; status: string; version: string }> =>
  request(`/orgs/${slug}/skill-lifecycle/validate`, {
    method: 'POST',
    params: { version_id: versionId },
  });

export interface SubmitForReviewRequest {
  version_id: number;
  intended_audience?: string;
  review_notes?: string;
}

export const submitForReview = (
  slug: string,
  body: SubmitForReviewRequest,
): Promise<{ skill_id: string; version_id: number; status: string; version: string }> =>
  request(`/orgs/${slug}/skill-lifecycle/submit-review`, { method: 'POST', body });

export interface ReviewDecisionRequest {
  version_id: number;
  decision: 'approved' | 'rejected';
  rationale: string;
}

export const reviewDecision = (
  slug: string,
  body: ReviewDecisionRequest,
): Promise<{ skill_id: string; version_id: number; status: string; decision: string }> =>
  request(`/orgs/${slug}/skill-lifecycle/review`, { method: 'POST', body });

export interface PublishRequest {
  version_id: number;
  approval_event_id: number;
}

export const publish = (
  slug: string,
  body: PublishRequest,
): Promise<{
  skill_id: string;
  version_id: number;
  status: string;
  version: string;
  published_at: string | null;
}> => request(`/orgs/${slug}/skill-lifecycle/publish`, { method: 'POST', body });

export interface AssignRequest {
  skill_id: string;
  agent_name: string;
  version_id: number;
}

export const assignSkill = (
  slug: string,
  body: AssignRequest,
): Promise<{
  skill_id: string;
  agent_name: string;
  version: string;
  content_hash: string;
  assigned_at: string;
}> => request(`/orgs/${slug}/skill-lifecycle/assign`, { method: 'POST', body });

export const rollback = (
  slug: string,
  skillId: string,
  reason: string,
): Promise<{ skill_id: string; assignments_deactivated: number; reason: string }> =>
  request(`/orgs/${slug}/skill-lifecycle/rollback`, {
    method: 'POST',
    params: { skill_id: skillId, reason },
  });

export const retire = (
  slug: string,
  skillId: string,
  reason: string,
): Promise<{ skill_id: string; status: string; reason: string }> =>
  request(`/orgs/${slug}/skill-lifecycle/retire`, {
    method: 'POST',
    params: { skill_id: skillId, reason },
  });

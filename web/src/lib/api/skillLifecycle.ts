/** Mirror of runtime/daemon/routes/skill_lifecycle.py — THR-055 lifecycle API.
 *
 * All lifecycle route types and fetch functions follow the same pattern as
 * ``lib/api/skills.ts``. Feature folders NEVER call ``fetch`` directly —
 * see ``web/ARCHITECTURE.md``.
 *
 * All queue/detail/action routes are Founder-only (bearer-required).
 * The agent proposal route is agent-only (session-binding, no bearer).
 * The catalog route is dual-auth (published skills only).
 */
import { request } from './client';

// ── Proposal submission (dual-auth: agent session OR founder bearer) ────

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
  params?: {
    task_id?: string;
    session_id?: string;
    agent_name?: string;
  },
): Promise<ProposalResponse> =>
  request(`/orgs/${slug}/skill-lifecycle/proposals`, {
    method: 'POST',
    body,
    params,
  });

// ── Read lifecycle status (dual-auth) ───────────────────────────────────

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

export const getLifecycleStatus = (
  slug: string,
  skillId: string,
): Promise<LifecycleStatusResponse> =>
  request(`/orgs/${slug}/skill-lifecycle/${skillId}`);

// ── Published custom catalog (dual-auth) ────────────────────────────────

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

export const listCustomCatalog = (
  slug: string,
): Promise<{ skills: CustomCatalogSkill[] }> =>
  request(`/orgs/${slug}/skill-lifecycle/catalog/custom`);

// ── Event history (dual-auth) ────────────────────────────────────────────

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

// ── Human-only lifecycle mutations (founder bearer only) ────────────────
// These return 403 for agent-session callers.

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
  request(`/orgs/${slug}/skill-lifecycle/${skillId}/claim`, {
    method: 'POST',
    body,
  });

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
  request(`/orgs/${slug}/skill-lifecycle/submit-review`, {
    method: 'POST',
    body,
  });

export interface ReviewDecisionRequest {
  version_id: number;
  decision: 'approved' | 'rejected';
  rationale: string;
}

export const reviewDecision = (
  slug: string,
  body: ReviewDecisionRequest,
): Promise<{ skill_id: string; version_id: number; status: string; decision: string }> =>
  request(`/orgs/${slug}/skill-lifecycle/review`, {
    method: 'POST',
    body,
  });

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
}> =>
  request(`/orgs/${slug}/skill-lifecycle/publish`, {
    method: 'POST',
    body,
  });

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
}> =>
  request(`/orgs/${slug}/skill-lifecycle/assign`, {
    method: 'POST',
    body,
  });

export const rollback = (
  slug: string,
  skillId: string,
  reason: string,
): Promise<{
  skill_id: string;
  assignments_deactivated: number;
  reason: string;
}> =>
  request(`/orgs/${slug}/skill-lifecycle/rollback`, {
    method: 'POST',
    params: { skill_id: skillId, reason },
  });

export const retire = (
  slug: string,
  skillId: string,
  reason: string,
): Promise<{
  skill_id: string;
  status: string;
  reason: string;
}> =>
  request(`/orgs/${slug}/skill-lifecycle/retire`, {
    method: 'POST',
    params: { skill_id: skillId, reason },
  });

// ═══════════════════════════════════════════════════════════════════════════
// THR-055 Founder-only proposal review API mirror
// ═══════════════════════════════════════════════════════════════════════════

// ── Founder-only proposal queue ─────────────────────────────────────────

export interface ProposalQueueItem {
  version_id: number;
  skill_id: string;
  slug: string;
  name: string;
  version: string;
  content_hash: string;
  proposer_agent: string;
  claimed_by: string | null;
  proposal_task_id: string | null;
  proposal_session_id: string | null;
  status: string;
  latest_validator_version: string | null;
  latest_validator_key: string | null;
  permitted_next_action: string | null;
  assigned_agent_count: number;
  assigned_agents: string[];
  created_at: string;
}

export interface ProposalQueueResponse {
  items: ProposalQueueItem[];
  page: number;
  page_size: number;
  total: number;
}

export const getProposalsQueue = (
  slug: string,
  params?: { status?: string; page?: number; page_size?: number },
): Promise<ProposalQueueResponse> =>
  request(`/orgs/${slug}/skill-lifecycle/proposals/queue`, { params });

// ── Founder-only proposal detail ────────────────────────────────────────

export interface ProposalDetailResponse {
  version_id: number;
  skill_id: string;
  slug: string;
  name: string;
  version: string;
  description: string;
  content_hash: string;
  content_artifact_key: string | null;
  policy_class: string;
  status: string;
  proposer_agent: string | null;
  proposal_task_id: string | null;
  proposal_session_id: string | null;
  claimed_by: string | null;
  claimed_at: string | null;
  reviewer: string | null;
  review_decision: string | null;
  review_rationale: string | null;
  reviewed_at: string | null;
  publisher: string | null;
  published_at: string | null;
  events: Array<Record<string, unknown>>;
  assignments: Array<Record<string, unknown>>;
  materializations: Array<Record<string, unknown>>;
  last_event_id: number | null;
  created_at: string;
}

export const getProposalDetail = (
  slug: string,
  versionId: number,
): Promise<ProposalDetailResponse> =>
  request(`/orgs/${slug}/skill-lifecycle/proposals/${versionId}`);

// ── Founder-only proposal actions (concurrency-protected) ──────────────

export interface ClaimProposalV2Request {
  expected_event_id: number;
}

export const claimProposalV2 = (
  slug: string,
  versionId: number,
  body: ClaimProposalV2Request,
): Promise<{
  skill_id: string;
  version_id: number;
  status: string;
  version: string;
  claimed_by: string | null;
  claimed_at: string | null;
}> =>
  request(`/orgs/${slug}/skill-lifecycle/proposals/${versionId}/claim`, {
    method: 'POST',
    body,
  });

export interface ValidateProposalRequest {
  validator_version: string;
  expected_event_id: number;
}

export const validateProposal = (
  slug: string,
  versionId: number,
  body: ValidateProposalRequest,
): Promise<{ skill_id: string; version_id: number; status: string; version: string }> =>
  request(`/orgs/${slug}/skill-lifecycle/proposals/${versionId}/validate`, {
    method: 'POST',
    body,
  });

export interface ReviewProposalRequest {
  decision: 'approved' | 'rejected';
  rationale?: string;
  expected_event_id: number;
}

export const reviewProposal = (
  slug: string,
  versionId: number,
  body: ReviewProposalRequest,
): Promise<{ skill_id: string; version_id: number; status: string; decision: string }> =>
  request(`/orgs/${slug}/skill-lifecycle/proposals/${versionId}/review`, {
    method: 'POST',
    body,
  });

export interface PublishProposalRequest {
  approval_event_id: number;
  expected_event_id: number;
}

export const publishProposal = (
  slug: string,
  versionId: number,
  body: PublishProposalRequest,
): Promise<{
  skill_id: string;
  version_id: number;
  status: string;
  version: string;
  published_at: string | null;
}> =>
  request(`/orgs/${slug}/skill-lifecycle/proposals/${versionId}/publish`, {
    method: 'POST',
    body,
  });

export interface AssignProposalRequest {
  agent_name: string;
  expected_event_id: number;
}

export const assignProposal = (
  slug: string,
  versionId: number,
  body: AssignProposalRequest,
): Promise<{
  skill_id: string;
  agent_name: string;
  version: string;
  content_hash: string;
  assigned_at: string;
}> =>
  request(`/orgs/${slug}/skill-lifecycle/proposals/${versionId}/assign`, {
    method: 'POST',
    body,
  });

export interface SubmitReviewProposalRequest {
  expected_event_id: number;
  intended_audience?: string;
  review_notes?: string;
}

export const submitReviewProposal = (
  slug: string,
  versionId: number,
  body: SubmitReviewProposalRequest,
): Promise<{
  skill_id: string;
  version_id: number;
  status: string;
  version: string;
}> =>
  request(`/orgs/${slug}/skill-lifecycle/proposals/${versionId}/submit-review`, {
    method: 'POST',
    body,
  });

export interface RollbackProposalRequest {
  reason?: string;
  expected_event_id: number;
}

export const rollbackProposal = (
  slug: string,
  versionId: number,
  body: RollbackProposalRequest,
): Promise<{
  skill_id: string;
  assignments_deactivated: number;
  reason: string;
}> =>
  request(`/orgs/${slug}/skill-lifecycle/proposals/${versionId}/rollback`, {
    method: 'POST',
    body,
  });

/** Mirror of src/daemon/routes/skills.py — PHASE 1 read + PHASE 2 write endpoints */
import { request } from './client';

export interface CatalogSkillItem {
  skill_id: string;
  name: string;
  type: 'managed' | 'system_contract' | 'user_authored';
  source: string;
  system_contract: boolean;
  visibility_category: 'toggleable' | 'read_only';
  policy_class: string;
  status: string;
  version: string;
  validation_state: 'in_catalog' | 'validated' | 'failed_validation';
  assigned_agent_count: number;
  effective_agent_count: number;
  has_assigned_not_yet_effective: boolean;
  summary: string;
}

export interface SkillDetail {
  skill_id: string;
  name: string;
  type: string;
  source: string;
  system_contract: boolean;
  visibility_category: string;
  policy_class: string;
  status: string;
  version: string;
  validation_state: string;
  summary: string;
  description: string;
  when_to_use: string;
  owner: string;
  validation?: { ok: boolean; errors: string[] };
  assignments?: Array<{
    agent: string;
    assigned: boolean;
    effective: boolean;
    state: string;
  }>;
}

export interface AgentSkillEffective {
  skill_id: string;
  name: string;
  type: string;
  source: string;
  status: string;
  version: string;
  provenance: string;
  hidden: boolean;
  summary: string;
}

export const listSkillsCatalog = (
  slug: string,
  params?: { filter?: 'Bundled' | 'Custom' },
): Promise<{ items: CatalogSkillItem[] }> =>
  request(`/orgs/${slug}/skills/catalog`, { params });

export const getSkillCatalogDetail = (
  slug: string,
  skillId: string,
): Promise<SkillDetail> =>
  request(`/orgs/${slug}/skills/catalog/${skillId}`);

export const getAgentSkillsEffective = (
  slug: string,
  agentId: string,
): Promise<{ skills: AgentSkillEffective[]; agent_id: string }> =>
  request(`/orgs/${slug}/agents/${agentId}/skills/effective`);

// ── PHASE 2 write endpoints ─────────────────────────────────────────────

export interface CreateSkillRequest {
  slug: string;
  name: string;
  version?: string;
  policy_class?: string;
  summary?: string;
  skill_md: string;
  references?: Record<string, string>;
  assets?: Record<string, string>;
}

export interface EditSkillRequest {
  name?: string;
  summary?: string;
  version?: string;
  skill_md?: string;
  references?: Record<string, string>;
  assets?: Record<string, string>;
}

export interface CreateSkillResponse {
  skill_id: string;
  source: string;
  validation_state: 'in_catalog' | 'validated' | 'failed_validation';
  validation: { ok: boolean; errors: string[] };
}

export interface ValidateSkillResponse {
  skill_id: string;
  validation_state: 'in_catalog' | 'validated' | 'failed_validation';
  validation: { ok: boolean; errors: string[] };
}

export interface EditSkillResponse {
  skill_id: string;
  source: string;
  validation_state: 'in_catalog' | 'validated' | 'failed_validation';
  validation: { ok: boolean; errors: string[] };
  version: string;
}

export interface ValidationEvent {
  id: number;
  skill_id: string;
  slug: string;
  agent: string | null;
  source: string;
  severity: string;
  ok: boolean;
  version: string;
  findings: string[];
  reason_codes: string[];
  created_at: string;
}

export const createSkill = (
  slug: string,
  body: CreateSkillRequest,
): Promise<CreateSkillResponse> =>
  request(`/orgs/${slug}/skills`, { method: 'POST', body: JSON.stringify(body) });

export const validateSkill = (
  slug: string,
  skillId: string,
): Promise<ValidateSkillResponse> =>
  request(`/orgs/${slug}/skills/${skillId}/validate`, { method: 'POST' });

export const editSkill = (
  slug: string,
  skillId: string,
  body: EditSkillRequest,
): Promise<EditSkillResponse> =>
  request(`/orgs/${slug}/skills/${skillId}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  });

export const listSkillValidation = (
  slug: string,
  params?: {
    skill?: string;
    agent?: string;
    source?: string;
    since?: string;
    severity?: string;
    limit?: number;
  },
): Promise<{ events: ValidationEvent[]; label: string }> =>
  request(`/orgs/${slug}/skills/validation`, { params });

// ── PHASE 3a assign endpoint ───────────────────────────────────────────

export interface AssignSkillRequest {
  action: 'allow' | 'remove';
}

export interface AssignSkillResponse {
  agent_id: string;
  skill_id: string;
  state: 'assigned' | 'unassigned';
  effective_hint: string | null;
  materializes_on: string | null;
}

export const assignSkill = (
  slug: string,
  agentId: string,
  skillId: string,
  body: AssignSkillRequest,
): Promise<AssignSkillResponse> =>
  request(`/orgs/${slug}/agents/${agentId}/skills/${skillId}/assign`, {
    method: 'POST',
    body: JSON.stringify(body),
  });

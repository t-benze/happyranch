/** Mirror of src/daemon/routes/skills.py — PHASE 1 read endpoints */
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
